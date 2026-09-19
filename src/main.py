"""GUI + tray agent entry point: .venv\\Scripts\\pythonw.exe src\\main.py [--tray | --watchdog | --challenge TEXT]

--tray: start hidden in the tray (used when starting with Windows).
--watchdog: run every minute by a scheduled task - starts the tray agent hidden if it was closed any other way than
            tray Exit (e.g. killed in Task Manager); does nothing (quickly) when it's running.
--challenge TEXT: only show the Anti-Bypass challenge for TEXT; exit code 0 = passed (used by the uninstaller).
"""
import queue
import subprocess
import sys
import time
import winreg
from pathlib import Path

from gui import single_instance

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WATCHDOG_TASK = "Lockdown Agent Watchdog"


def register_autostart():
    """Start the tray agent hidden at login (per-user, no admin), and a per-user task that brings it back within a
    minute if it's killed. Re-written each launch so the path stays current."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    command = f'"{pythonw}" "{Path(__file__).resolve()}"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, "Lockdown", 0, winreg.REG_SZ, f"{command} --tray")
    subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "1", "/TN", WATCHDOG_TASK,
                    "/TR", f"{command} --watchdog"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


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


if __name__ == "__main__":
    if "--challenge" in sys.argv:
        sys.exit(challenge(sys.argv[sys.argv.index("--challenge") + 1]))
    if "--watchdog" in sys.argv and not watchdog_should_start():
        sys.exit(0)
    from gui.app import LockdownApp
    events: queue.Queue = queue.Queue()
    if not single_instance.acquire(events):
        sys.exit(0)  # already running - it will show its window
    register_autostart()
    LockdownApp(events, start_hidden="--tray" in sys.argv or "--watchdog" in sys.argv).mainloop()
