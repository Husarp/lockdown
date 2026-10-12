"""The resident Lockdown agent with the web window - the only window Lockdown has.

Starts the local asset server, makes the three windows hidden, shows the tray icon, starts the background workers
(resident/workers.py) and runs the pywebview loop. Heavy modules (pywebview, pystray, PIL) are imported inside the
functions that need them, so `import agent` stays quick.

Never stuck on a dark screen: no window is shown before its page has said it works (resident/windows.py). If the main
page does not within windows.MAIN_READY_SEC (WebView2 missing or failing, the page's files not reaching it, a script
error), or the agent fails to start, `start_failed` runs: the reason is logged, ONE second try is started (a fresh
process with the same arguments plus `--retried`, unless this run already is the second try) and a Windows message box
says what happened and where the logs are. main() returns FAILED. The blocking itself never needed the window (the
Lockdown service does it). Start-up notes: %TEMP%\\lockdown-newui.log, the important ones in the Lockdown log too
(resident/diag.py)."""
import ctypes
import os
import queue
import sys
import threading
import time

TRAY_ARGS = ("--tray", "--watchdog")
RETRIED = "--retried"   # added to the arguments of the second try: a run that has it does not try again
FAILED = 3              # main()'s exit code: the new window could not be used
HAND_OVER_SEC = 10      # after a failure this process is gone within this, even if the window loop does not end
MB_OK, MB_ICONWARNING, MB_SETFOREGROUND, MB_TOPMOST = 0x0, 0x30, 0x10000, 0x40000
_failed_lock = threading.Lock()
_failed: list = []


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


EXIT_TEXT = "Close the Lockdown icon and window (blocking, word filters, limits and reminders keep running)"


class TrayKeeper:
    """The tray icon of the running agent, which can come and go while everything else keeps running.

    `show()` makes the icon (a new Tray each time, with its listener and the notices' balloon fallback) - it does nothing
    when the icon is there. `hide()` takes it away again (tray Exit, an update that restarts the app, giving up on the
    window) and puts the notices' fallback back to nothing. The workers (keywords, minimise / grey, notices, usage,
    reminders ...) never depend on the icon, so after `hide()` the agent enforces headless, as before."""

    def __init__(self, make_tray, hub, toaster, listener_for, log=_log):
        self.make_tray, self.hub, self.toaster, self.listener_for, self.log = make_tray, hub, toaster, listener_for, log
        self._lock = threading.Lock()
        self.tray = None
        self._listener = None

    @property
    def shown(self) -> bool:
        return self.tray is not None

    def show(self) -> bool:
        """The icon is there now. True if this call made it."""
        with self._lock:
            if self.tray is not None:
                return False
            tray = self.make_tray()
            self.tray = tray
            self.toaster.fallback = tray.notify    # (a Windows notification that can't be had: the tray's balloon says it)
            tray.start()
            self._listener = self.listener_for(tray)
            self.hub.listeners.append(self._listener)
            snap = self.hub.snapshot
            if snap is not None:                   # (the listener only hears about changes: give the new icon the state now)
                try:
                    self._listener(None, snap)
                except Exception as error:
                    self.log(f"WARNING The tray icon could not be brought up to date: {error!r}")
            return True

    def hide(self) -> bool:
        """The icon is gone now. True if this call removed it."""
        with self._lock:
            tray, self.tray = self.tray, None
            if tray is None:
                return False
            if self._listener in self.hub.listeners:
                self.hub.listeners.remove(self._listener)
            self._listener = None
            self.toaster.fallback = lambda text: None
            try:
                tray.stop()
            except Exception as error:
                self.log(f"WARNING The tray icon did not stop cleanly: {error!r}")
            return True

    stop = hide      # (what the code that closes everything down calls)


def _tray_exit(win, keeper, ctx):
    """Tray Exit: closes the tray icon and the window - nothing else. Every worker (keyword blocking, minimise / grey,
    notices, the app-in-front warning, usage counting, reminders, bedtime, breaks) keeps running in this same process, and
    so does the Lockdown service. Opening Lockdown again brings the window and the icon back (open_again).
    Done at once while Anti-Bypass says "free"; otherwise the main window comes up with the challenge (GuardService keeps
    the Exit under a ticket and runs it only when the challenge is passed in Python)."""
    from resident import challenge_cli, windows

    def quit_now():
        import antibypass
        _log("Tray Exit: the icon and the window are closed, blocking keeps running")
        ctx.db.set_setting(antibypass.EXITED_KEY, "1")    # (no icon until you open Lockdown or log in again)
        keeper.hide()
        try:
            win.main.hide()
        except Exception as error:
            _log(f"WARNING The window did not hide: {error!r}")
        windows.set_visible(win, False)
    challenge_cli.ask_in_main(win, ctx, [EXIT_TEXT], quit_now)


def open_again(win, keeper, ctx):
    """Lockdown opened again (a second launch, the start menu): the window comes up and, after a tray Exit, the icon too -
    in this same process. The flag that said "no icon" is cleared."""
    import antibypass
    from resident import windows
    if keeper is not None and keeper.show():
        _log("Lockdown opened again: the tray icon is back")
    if ctx.db.get_setting(antibypass.EXITED_KEY, "0") == "1":
        ctx.db.set_setting(antibypass.EXITED_KEY, "0")
    windows.show_main(win)


def message_box(text: str, title: str = "Lockdown"):
    """A native Windows message box (OK button, on top of everything); returns when it is closed. Windows only - on
    any other system, and when it can't be shown, nothing happens. Tests replace this function."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST)
    except (OSError, AttributeError):
        pass


def failure_text(what: str, reason: str, trying_again: bool) -> str:
    """What the message box says: `what` could not start, why (one short line), where the logs are, that blocking
    keeps working, and what happens next."""
    import paths
    from resident import diag
    reason = " ".join(str(reason).split())
    if len(reason) > 200:
        reason = reason[:197] + "..."
    next_step = ("Lockdown is trying once more." if trying_again
                 else "Restart the PC. If it keeps happening, send the log files named above.")
    return (f"{what} could not start.\n\n{reason}\n\nBlocking keeps working: the Lockdown service does not need "
            f"the window.\n\nLogs:\n{paths.LOG_PATH}\n{diag.path()}\n\n{next_step}")


def retry_args(argv: list[str], visible: bool = True) -> list[str]:
    """The arguments of the second try: this run's own, plus --retried. The tray flags are left out when somebody asked
    to see the window (so the second try opens it)."""
    return [a for a in argv[1:] if not (visible and a in TRAY_ARGS)] + [RETRIED]


def start_retry(args: list[str]) -> bool:
    """A fresh process of this app with `args` (nothing of this one is left in it), started by the same small delayed
    helper the restart uses, once this process has let go of the single-instance port. True if it was started."""
    import subprocess
    from gui import single_instance
    from paths import command_line, gui_command
    from resident import diag
    single_instance.release()
    try:
        subprocess.Popen(f'ping -n 3 127.0.0.1 >nul & start "" {command_line(gui_command(*args))}', shell=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as error:
        diag.note(f"the second try could not be started: {error!r}", important=True)
        return False
    diag.note(f"the window is tried once more ({' '.join(args)})", important=True)
    return True


def start_failed(argv: list[str], reason: str, visible: bool) -> bool:
    """The window could not be used (the reason is in the logs already). Once per process: start the second try (unless
    this run already is one), THEN show the message box - a modal box must not hold the second try up. True if a
    second try was started. A `--watchdog` run (once a minute, nobody there) gets no box: it would come back every
    minute."""
    with _failed_lock:
        if _failed:
            return False
        _failed.append(True)
    retrying = RETRIED not in argv and start_retry(retry_args(argv, visible))
    if "--watchdog" not in argv:
        message_box(failure_text("The Lockdown window", reason, retrying))
    return retrying


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
    visible = not any(a in argv for a in TRAY_ARGS) or bool(win is not None and win.show_wanted)
    start_failed(argv, state["reason"], visible)
    return FAILED


def _run(argv: list[str], events, state: dict) -> int:
    """The agent itself. `state["reason"]` is set when it gave up on the new window (main() then runs start_failed)."""
    from resident import diag, windows
    from resident.assets import AssetServer
    server = AssetServer(log=diag.note)
    base_url = server.start()
    import webapi
    ctx = webapi.make_context()
    import applog                  # the App log: this start (and an update, or a last run that was cut off), crashes, errors
    from version import VERSION
    applog.set_source("agent")
    applog.install_crash_hooks()
    applog.install_error_log()
    applog.process_started(ctx.db, VERSION, extra=applog.args_text(argv[1:]))
    from resident import overlay
    win = windows.create(base_url, overlay.saved_background(), js_api=webapi.build_api(ctx),   # (the page's own --bg)
                         overlay_background=overlay.saved_background())
    state["win"] = win
    started = time.monotonic()

    def page_said_ready(name):     # (Api page_ready: the window may be shown now)
        if windows.page_ready(win, name):
            diag.note(f"{name} page ready after {time.monotonic() - started:.1f} s")
    ctx.on_page_ready = page_said_ready
    ctx.on_window_seen = lambda: windows.set_visible(win, True)   # (Api window_seen: clicked while thought hidden)
    lock = threading.Lock()

    def fall_back(reason: str):
        """The new window can't be used: say why, close it all down; main() then runs start_failed (the second try
        and the message box). If the window loop does not end, this process does that and goes after HAND_OVER_SEC."""
        with lock:
            if state["reason"] is not None or win.quitting:
                return
            state["reason"] = reason
        diag.note(f"the new window could not be used: {reason}", important=True)

        def leave():
            diag.note("the window loop did not end - leaving now", important=True)
            start_failed(argv, reason, not any(a in argv for a in TRAY_ARGS) or win.show_wanted)
            os._exit(FAILED)
        timer = state["leave"] = threading.Timer(HAND_OVER_SEC, leave)
        timer.daemon = True
        timer.start()
        tray = state.get("tray")      # (the TrayKeeper)
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
    state["keeper"] = None            # (the TrayKeeper, once start_up made it)
    windows.pump_open_events(events, win, lambda: open_again(win, state["keeper"], ctx))
    # R11: app icons (/icon/...), the installed-apps catalog (loads in the background after the window is up) and the
    # window the program-file dialog belongs to
    from resident import catalog, icon_prefetch, icons
    ctx.main_window = win.main
    ctx.publish = win.publisher.publish   # R18: the draft tells the page (draft / blocks_changed pushes)
    ctx.catalog = app_catalog = catalog.AppCatalog(on_ready=lambda: win.publisher.publish("apps_ready", {}), seed_games=True)
    icon_service = icons.IconService(on_ready=lambda key: win.publisher.publish("icon_ready", {"key": key}),
                                     path_of=app_catalog.path_for, download=icons.download_on(ctx.db))
    server.add_icon_routes(icon_service)
    site_icons = icon_prefetch.attach(ctx, icon_service)   # UP15: real icons for your sites (starts in start_up; after ctx.publish)

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
        # first: whatever happens below, the main page is watched - never ready in time, and fall_back runs
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
        keeper = TrayKeeper(
            lambda: Tray(on_open=lambda: windows.show_main(win), on_exit=lambda: _tray_exit(win, keeper, ctx),
                         on_mode=tray_actions.mode, on_update=tray_actions.update, on_pause=tray_actions.pause,
                         on_resume=tray_actions.resume),
            hub, toaster, workers.tray_listener)
        state["tray"] = state["keeper"] = keeper
        pool.hub.listeners.append(workers.publisher_listener(win))
        import antibypass
        if "--watchdog" in argv and ctx.db.get_setting(antibypass.EXITED_KEY, "0") == "1":
            diag.note("started by the watchdog after a tray Exit: no tray icon (open Lockdown to get it)")
        else:       # (a start at login or by hand is a normal start: the icon comes, the "no icon" flag goes)
            ctx.db.set_setting(antibypass.EXITED_KEY, "0")
            keeper.show()
        from resident import update_flow   # R17: the in-app update (notice "Install", About, banner); quits WITHOUT EXITED_KEY
        ctx.update_flow = update_flow.attach(pool.hub, pool.updates, win.publisher.publish,
                                             show=lambda: windows.show_main(win),
                                             quit_app=lambda: (keeper.stop(), windows.quit_agent(win)))
        from resident import protection_watch   # R23: list download progress -> the Site protection tab
        protection_watch.attach(pool, win.publisher.publish, lambda: win.publisher.visible)
        pool.start()
        app_catalog.start()   # (R11: reads the cached list, scans again if it is over a day old)
        site_icons.start()    # (UP15: a few seconds on, the sites of your lists get their real icons in the background)
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
    applog.process_stopped(ctx.db)
    return 0
