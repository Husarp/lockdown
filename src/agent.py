"""The resident Lockdown agent with the new web window - the window Lockdown opens in (ui_theme.DEFAULT_ENGINE; the old
customtkinter one only with --legacy, or as the fallback below).

Starts the local asset server, makes the three windows hidden, shows the tray icon, starts the background workers
(resident/workers.py) and runs the pywebview loop. Heavy modules (pywebview, pystray, PIL) are imported inside the
functions that need them, so `import agent` stays quick and never loads customtkinter.

Never stuck on a dark screen: no window is shown before its page has said it works (resident/windows.py). If the main
page does not within windows.MAIN_READY_SEC (WebView2 missing or failing, the page's files not reaching it, a script
error), or the agent fails to start, the reason is logged and this run is handed to the old window (`hand_over`):
main() returns FALLBACK. Start-up notes: %TEMP%\\lockdown-newui.log, the important ones in the Lockdown log too
(resident/diag.py)."""
import ctypes
import os
import queue
import sys
import threading
import time

TRAY_ARGS = ("--tray", "--watchdog")
NOT_YET = ()   # flags the agent can't do (main.py then keeps the old window for them): none left
FALLBACK = 3          # main()'s exit code: the new window could not be used - the old one was started for this run
HAND_OVER_SEC = 10    # after a fallback this process is gone within this, even if the window loop does not end
_hand_over_lock = threading.Lock()
_handed_over: list = []


def handles(argv: list[str]) -> bool:
    """Can the agent do everything argv asks for? (A flag listed in NOT_YET makes main.py keep the old window.)"""
    return not any(a in argv for a in NOT_YET)


def _log(message: str):
    from paths import LOG_PATH
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},000 {message}\n")
    except OSError:
        pass


def set_dpi_awareness():
    """Per-monitor DPI awareness (V2), before any window exists. Windows only; a failure is harmless."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        pass


EXIT_TEXT = "Quit Lockdown (blocked-visit notices, time limits and reminders stop until you start it again)"


def _tray_exit(win, tray, ctx):
    """Tray Exit: quits at once while Anti-Bypass says "free"; otherwise the main window comes up with the challenge
    (GuardService keeps the quit under a ticket and runs it only when the challenge is passed in Python)."""
    from resident import challenge_cli, windows

    def quit_now():
        import antibypass
        _log("Tray Exit")
        ctx.db.set_setting(antibypass.EXITED_KEY, "1")
        tray.stop()
        threading.Timer(0.2, windows.quit_agent, args=(win,)).start()   # (after the challenge's reply went back)
    challenge_cli.ask_in_main(win, ctx, [EXIT_TEXT], quit_now)


def hand_over(visible: bool) -> bool:
    """The old window takes over this run: a fresh `--legacy` process (nothing of this one is left in it; `--tray` too
    when nobody asked to see the window), started by the same small delayed helper the old window's restart uses, once
    this process has let go of the single-instance port. Once per process; True if it was started now."""
    with _hand_over_lock:
        if _handed_over:
            return False
        _handed_over.append(True)
    import subprocess
    from gui import single_instance
    from paths import command_line, gui_command
    from resident import diag
    single_instance.release()
    args = ["--legacy"] + ([] if visible else ["--tray"])
    try:
        subprocess.Popen(f'ping -n 3 127.0.0.1 >nul & start "" {command_line(gui_command(*args))}', shell=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as error:
        diag.note(f"the old window could not be started: {error!r}", important=True)
        return False
    diag.note(f"the old window takes over this run ({' '.join(args)})", important=True)
    return True


def _start_note(argv: list[str]) -> str:
    """One line about this start: version, build, the page files, the arguments."""
    try:
        from version import VERSION
        from resident.assets import ui_dir
        ui = ui_dir()
        return (f"start {VERSION} frozen={bool(getattr(sys, 'frozen', False))} python={sys.version.split()[0]} "
                f"args={argv[1:]} ui={ui} index.html={'yes' if (ui / 'index.html').is_file() else 'MISSING'}")
    except Exception as error:
        return f"start ({error!r})"


def main(argv: list[str], on_first_instance=None) -> int:
    """`on_first_instance`: called once this process is the running agent (main.py: register autostart + watchdog)."""
    if not handles(argv):
        _log("The new UI does not handle these flags yet")
        return 2
    set_dpi_awareness()
    if "--challenge" in argv:   # the uninstaller's challenge: its own small window, no single-instance lock
        from resident import challenge_cli
        return challenge_cli.run(argv)
    if "--selftest" in argv:   # the build check: temporary data folder (main.py), no single-instance lock
        from resident import selftest
        return selftest.run(argv[argv.index("--selftest") + 1])
    from gui import single_instance
    events: queue.Queue = queue.Queue()
    if not single_instance.acquire(events):
        return 0   # already running: it was told to show its window
    if on_first_instance:
        on_first_instance()
    from resident import diag
    diag.capture_pywebview()
    diag.note(_start_note(argv))
    state = {"reason": None, "win": None}
    try:
        code = _run(argv, events, state)
    except Exception:
        import traceback
        diag.note(f"the agent failed: {traceback.format_exc()}", important=True)
        state["reason"] = state["reason"] or "the agent failed to start"
    if state.get("leave") is not None:
        state["leave"].cancel()      # (the window loop did end: no need to leave the hard way)
    if state["reason"] is None:
        return code
    win = state["win"]
    hand_over(not any(a in argv for a in TRAY_ARGS) or bool(win is not None and win.show_wanted))
    return FALLBACK


def _run(argv: list[str], events, state: dict) -> int:
    """The agent itself. `state["reason"]` is set when it gave up on the new window (main() then hands over)."""
    from resident import diag, windows
    from resident.assets import AssetServer
    server = AssetServer(log=diag.note)
    base_url = server.start()
    import webapi
    ctx = webapi.make_context()
    from resident import overlay
    win = windows.create(base_url, overlay.saved_background(), js_api=webapi.build_api(ctx),   # (the page's own --bg)
                         overlay_background=overlay.saved_background())
    state["win"] = win
    started = time.monotonic()

    def page_said_ready(name):     # (Api page_ready: the window may be shown now)
        if windows.page_ready(win, name):
            diag.note(f"{name} page ready after {time.monotonic() - started:.1f} s")
    ctx.on_page_ready = page_said_ready
    lock = threading.Lock()

    def fall_back(reason: str):
        """The new window can't be used: say why, close it all down; main() starts the old window for this run. If the
        window loop does not end, this process goes anyway after HAND_OVER_SEC."""
        with lock:
            if state["reason"] is not None or win.quitting:
                return
            state["reason"] = reason
        diag.note(f"the new window could not be used: {reason}", important=True)

        def leave():
            diag.note("the window loop did not end - leaving now", important=True)
            hand_over(not any(a in argv for a in TRAY_ARGS) or win.show_wanted)
            os._exit(FALLBACK)
        timer = state["leave"] = threading.Timer(HAND_OVER_SEC, leave)
        timer.daemon = True
        timer.start()
        tray = state.get("tray")
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        windows.quit_agent(win)

    def describe():                # (what was seen, for the log line of a main page that never said it was ready)
        webview = sys.modules.get("webview")
        events_ = getattr(win.main, "events", None)
        seen = [f"renderer {getattr(webview, 'renderer', None)}"]
        if events_ is not None:
            seen.append(f"window made {events_.shown.is_set()}, pywebview page loaded {events_.loaded.is_set()}")
        seen.append(f"files served {server.served}" + (f", last {server.last}" if server.last else ""))
        return "; ".join(seen)
    windows.attach_publisher(win, _log)
    windows.pump_open_events(events, win)
    # R11: app icons (/icon/...), the installed-apps catalog (loads in the background after the window is up) and the
    # window the program-file dialog belongs to
    from resident import catalog, icons
    ctx.main_window = win.main
    ctx.publish = win.publisher.publish   # R18: the draft tells the page (draft / blocks_changed pushes)
    ctx.catalog = app_catalog = catalog.AppCatalog(on_ready=lambda: win.publisher.publish("apps_ready", {}), seed_games=True)
    server.add_icon_routes(icons.IconService(on_ready=lambda key: win.publisher.publish("icon_ready", {"key": key}),
                                             path_of=app_catalog.path_for))

    from resident import reminder_ui, workers
    hub = workers.Hub(log_sink=_log)
    ctx.hub = hub                         # (Api methods poke hub.status_worker after a change of the blocks)
    hub.netlog_ctx = ctx                  # R16: the Network Log's worker reads the last hour ahead
    ctx.window_visible = lambda: win.publisher.visible    # R16: the Network Log's live feed sleeps while the window is hidden
    # R07: the corner window (reminder popups + notices), the tray's handlers, hub.notify.show and hub.open_page
    ui, tray_actions, toaster, corner_publisher = reminder_ui.attach(win, hub, ctx, lambda: windows.show_main(win), _log)
    # R08: the full-screen cover (bedtime screen, running strict break): its Publisher, ctx.overlay, ui.overlay_handler
    overlay_publisher = overlay.attach(win, hub, ctx, ui, _log)
    pool = workers.Workers(hub=hub, reminder_ui=ui, log_sink=_log)   # (the background jobs: only the process that owns the port runs them)

    def ready():   # (runs once the GUI loop is up)
        # first: whatever happens below, the main page is watched - never ready in time, and the old window takes over
        windows.watch_main(win, not any(a in argv for a in TRAY_ARGS), fall_back, describe)
        import webview
        diag.note(f"window loop up: renderer {getattr(webview, 'renderer', None)}, {base_url}")
        if getattr(webview, "renderer", None) != "edgechromium":
            fall_back(f"Microsoft Edge WebView2 was not found (pywebview chose {getattr(webview, 'renderer', None)!r})")
            return
        try:
            start_up()
        except Exception as error:
            import traceback
            diag.note(f"the agent failed to start: {traceback.format_exc()}", important=True)
            fall_back(f"the agent failed to start ({error!r})")
            return
        if not any(a in argv for a in TRAY_ARGS):
            windows.show_main(win)   # (shown the moment its page says it works)

    def start_up():
        from gui.tray import Tray
        tray = Tray(on_open=lambda: windows.show_main(win), on_exit=lambda: _tray_exit(win, tray, ctx),
                    on_mode=tray_actions.mode, on_update=tray_actions.update, on_pause=tray_actions.pause,
                    on_resume=tray_actions.resume)
        state["tray"] = tray
        toaster.fallback = tray.notify      # (a Windows notification that can't be had: the tray's balloon says it)
        tray.start()
        pool.hub.listeners += [workers.tray_listener(tray), workers.publisher_listener(win)]
        from resident import update_flow   # R17: the in-app update (notice "Install", About, banner); quits WITHOUT EXITED_KEY
        ctx.update_flow = update_flow.attach(pool.hub, pool.updates, win.publisher.publish,
                                             show=lambda: windows.show_main(win),
                                             quit_app=lambda: (tray.stop(), windows.quit_agent(win)))
        from resident import protection_watch   # R23: list download progress -> the Site protection tab
        protection_watch.attach(pool, win.publisher.publish, lambda: win.publisher.visible)
        pool.start()
        app_catalog.start()   # (R11: reads the cached list, scans again if it is over a day old)
        webapi.screentime.start_warmup(ctx)   # (a few seconds on, in the background: the first visit of Screen Time finds its day sums ready)
        webapi.protection.start_warmup(ctx)   # (R29: ten seconds on: "Check a site" finds the lists' tables loaded, not 1.5 s of file reading)
    try:
        windows.start(ready)
    except Exception as error:      # (pywebview could not start at all: no .NET / pythonnet, a broken bundle ...)
        import traceback
        diag.note(f"the window loop failed: {traceback.format_exc()}", important=True)
        state["reason"] = state["reason"] or f"the window loop failed ({error!r})"
    pool.stop()
    win.publisher.stop()
    corner_publisher.stop()
    overlay_publisher.stop()
    server.stop()
    return 0
