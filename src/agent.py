"""The resident Lockdown agent with the new web window (switch: `--new-ui` or setting ui.engine = "web").

Starts the local asset server, makes the three windows hidden, shows the tray icon and runs the pywebview loop.
Workers, the Api and the publisher are added by later tickets. Heavy modules (pywebview, pystray, PIL) are imported
inside the functions that need them, so `import agent` stays quick and never loads customtkinter."""
import ctypes
import queue
import sys
import time

TRAY_ARGS = ("--tray", "--watchdog")
NOT_YET = ("--challenge", "--selftest")   # R06 / R09 take their names out of this list when they handle them


def handles(argv: list[str]) -> bool:
    """Can the agent do everything argv asks for? (Until R06 / R09 it can't --challenge / --selftest, and main.py
    then keeps using the old window for those, e.g. the uninstaller's challenge.)"""
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


def exit_allowed() -> bool:
    """Placeholder until R06: tray Exit works only while Anti-Bypass says "free"."""
    import antibypass
    from db import Database
    from trusted_time import now_from_db
    db = Database()
    return antibypass.status(antibypass.settings(db), now_from_db(db)) == "free"


def _tray_exit(win, tray):
    from resident import windows
    if exit_allowed():
        import antibypass
        from db import Database
        _log("Tray Exit")
        Database().set_setting(antibypass.EXITED_KEY, "1")
        tray.stop()
        windows.quit_agent(win)
    else:
        windows.show_main(win)   # (R06: the challenge opens here)


def main(argv: list[str], on_first_instance=None) -> int:
    """`on_first_instance`: called once this process is the running agent (main.py: register autostart + watchdog)."""
    if not handles(argv):
        _log("The new UI does not handle --challenge / --selftest yet (R06 / R09)")
        return 2
    set_dpi_awareness()
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
    win = windows.create(base_url, ui_theme.background_color())
    windows.pump_open_events(events, win)

    def ready():   # (runs once the GUI loop is up)
        from gui.tray import Tray
        tray = Tray(on_open=lambda: windows.show_main(win), on_exit=lambda: _tray_exit(win, tray))
        tray.start()
        if not any(a in argv for a in TRAY_ARGS):
            windows.show_main(win)
    windows.start(ready)
    server.stop()
    return 0
