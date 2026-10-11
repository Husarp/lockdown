"""The resident Lockdown agent with the new web window (switch: `--new-ui` or setting ui.engine = "web").

Starts the local asset server, makes the three windows hidden, shows the tray icon, starts the background workers
(resident/workers.py) and runs the pywebview loop. The Api is added by later tickets. Heavy modules (pywebview,
pystray, PIL) are imported inside the functions that need them, so `import agent` stays quick and never loads
customtkinter."""
import ctypes
import queue
import sys
import threading
import time

TRAY_ARGS = ("--tray", "--watchdog")
NOT_YET = ()   # flags the agent can't do (main.py then keeps the old window for them): none left


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
    import ui_theme
    from resident import windows
    from resident.assets import AssetServer
    server = AssetServer()
    base_url = server.start()
    import webapi
    ctx = webapi.make_context()
    win = windows.create(base_url, ui_theme.background_color(), js_api=webapi.build_api(ctx))
    windows.attach_publisher(win, _log)
    windows.pump_open_events(events, win)
    # R11: app icons (/icon/...), the installed-apps catalog (loads in the background after the window is up) and the
    # window the program-file dialog belongs to
    from resident import catalog, icons
    ctx.main_window = win.main
    ctx.catalog = app_catalog = catalog.AppCatalog(on_ready=lambda: win.publisher.publish("apps_ready", {}), seed_games=True)
    server.add_icon_routes(icons.IconService(on_ready=lambda key: win.publisher.publish("icon_ready", {"key": key}),
                                             path_of=app_catalog.path_for))

    from resident import reminder_ui, workers
    hub = workers.Hub(log_sink=_log)
    # R07: the corner window (reminder popups + notices), the tray's handlers, hub.notify.show and hub.open_page
    ui, tray_actions, toaster, corner_publisher = reminder_ui.attach(win, hub, ctx, lambda: windows.show_main(win), _log)
    pool = workers.Workers(hub=hub, reminder_ui=ui, log_sink=_log)   # (the background jobs: only the process that owns the port runs them)

    def ready():   # (runs once the GUI loop is up)
        from gui.tray import Tray
        tray = Tray(on_open=lambda: windows.show_main(win), on_exit=lambda: _tray_exit(win, tray, ctx),
                    on_mode=tray_actions.mode, on_update=tray_actions.update, on_pause=tray_actions.pause,
                    on_resume=tray_actions.resume)
        toaster.fallback = tray.notify      # (a Windows notification that can't be had: the tray's balloon says it)
        tray.start()
        pool.hub.listeners += [workers.tray_listener(tray), workers.publisher_listener(win)]
        from resident import update_flow   # R17: the in-app update (notice "Install", About, banner); quits WITHOUT EXITED_KEY
        ctx.update_flow = update_flow.attach(pool.hub, pool.updates, win.publisher.publish,
                                             show=lambda: windows.show_main(win),
                                             quit_app=lambda: (tray.stop(), windows.quit_agent(win)))
        pool.start()
        app_catalog.start()   # (R11: reads the cached list, scans again if it is over a day old)
        if not any(a in argv for a in TRAY_ARGS):
            windows.show_main(win)
    windows.start(ready)
    pool.stop()
    win.publisher.stop()
    corner_publisher.stop()
    server.stop()
    return 0
