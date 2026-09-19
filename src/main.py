"""GUI + tray agent entry point: .venv\\Scripts\\pythonw.exe src\\main.py [--tray | --watchdog | --challenge TEXT]

--tray: start hidden in the tray (used when starting with Windows).
--watchdog: run every minute by a scheduled task - starts the tray agent hidden if it was closed any other way than
            tray Exit (e.g. killed in Task Manager); does nothing (quickly) when it's running.
--challenge TEXT: only show the Anti-Bypass challenge for TEXT; exit code 0 = passed (used by the uninstaller).
--selftest REPORT: build check - every page built with a temporary data folder, result written to REPORT.
"""
import os
import queue
import subprocess
import sys
import tempfile
import time
import winreg

if "--selftest" in sys.argv:   # (before paths is imported: it reads the data folder once)
    os.environ["LOCKDOWN_DATA_DIR"] = tempfile.mkdtemp(prefix="lockdown-selftest-")

from gui import single_instance  # noqa: E402
from paths import ASSETS, command_line, gui_command  # noqa: E402

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WATCHDOG_TASK = "Lockdown Agent Watchdog"
APP_ID = "Lockdown.App"   # Windows app identity: notifications / taskbar show "Lockdown" + its icon, not "Python"
ICON = ASSETS / "lockdown.ico"


def set_identity():
    """Register the app's name + icon for this user (no admin) and use them for this process."""
    import ctypes
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Lockdown")
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(ICON))
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def register_autostart():
    """Start the tray agent hidden at login (per-user, no admin), and a per-user task that brings it back within a
    minute if it's killed. Re-written each launch so the path stays current."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, "Lockdown", 0, winreg.REG_SZ, command_line(gui_command("--tray")))
    subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "1", "/TN", WATCHDOG_TASK,
                    "/TR", command_line(gui_command("--watchdog"))], capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def watchdog_should_start() -> bool:
    if single_instance.running():
        return False
    from antibypass import EXITED_KEY
    from db import Database
    db = Database()
    if db.get_setting(EXITED_KEY, "0") == "1":   # quit with tray Exit (after the challenge): stay off until login
        return False
    from paths import LOG_PATH
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},000 WARNING Tray app wasn't running (closed without "
                    "Exit) - started again by the watchdog\n")
    except OSError:
        pass
    return True


def challenge(text: str) -> int:
    import customtkinter as ctk
    import antibypass
    from db import Database
    from gui import theme
    from gui.antibypass_page import ChallengeWindow
    from trusted_time import now_from_db
    db = Database()
    if antibypass.status(antibypass.settings(db), now_from_db(db)) == "free":
        return 0
    theme.apply()
    root = ctk.CTk()
    root.db = db
    from gui import shortcuts
    shortcuts.install(root)
    root.withdraw()
    passed = []
    ChallengeWindow(root, [text], lambda: passed.append(True), root.destroy)
    try:
        while not passed:
            root.update()
            time.sleep(0.02)
    except ctk.tkinter.TclError:   # window closed (cancel)
        pass
    return 0 if passed else 1


def selftest(report: str) -> int:
    """Build check (scripts/build.ps1): start the app hidden with a temporary data folder, build every page, write
    "OK" or the error to `report`, quit. Doesn't touch the running Lockdown, autostart or tasks."""
    import traceback
    try:
        from gui.app import PAGES, LockdownApp
        from gui import theme
        missing = [p.name for p in [theme.APP_ICON] + [theme.ASSETS / "fonts" / f for f in theme.FONT_FILES]
                   if not p.exists()]
        if missing:
            raise FileNotFoundError(f"missing from the build: {missing}")
        import uiautomation as auto   # reading the browser's address bar needs Windows UI Automation
        auto.GetRootControl().Name
        app = LockdownApp(queue.Queue(), start_hidden=True)
        for name, _spec, _icon in PAGES:
            app.show_page(name)
            app.update()
        app.tray.stop()
        app.destroy()
        result = f"OK - {len(PAGES)} pages"
    except Exception:
        result = traceback.format_exc()
    with open(report, "w", encoding="utf-8") as f:
        f.write(result)
    return 0 if result.startswith("OK") else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest(sys.argv[sys.argv.index("--selftest") + 1]))
    if "--watchdog" in sys.argv and not watchdog_should_start():
        sys.exit(0)
    set_identity()
    if "--challenge" in sys.argv:
        sys.exit(challenge(sys.argv[sys.argv.index("--challenge") + 1]))
    from gui.app import LockdownApp
    events: queue.Queue = queue.Queue()
    if not single_instance.acquire(events):
        sys.exit(0)  # already running - it will show its window
    register_autostart()
    LockdownApp(events, start_hidden="--tray" in sys.argv or "--watchdog" in sys.argv).mainloop()
