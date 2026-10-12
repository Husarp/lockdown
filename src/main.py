"""Lockdown entry point: .venv\\Scripts\\pythonw.exe src\\main.py [--tray | --watchdog | --challenge TEXT | --selftest REPORT]

Everything goes to the web window's agent (src/agent.py); this file only does what must happen before it.
--tray: start hidden in the tray (used when starting with Windows).
--watchdog: run every minute by a scheduled task - starts the agent hidden if it is not running (e.g. killed in Task
            Manager); does nothing (quickly) when it's running. After a tray Exit the agent keeps running without its
            icon and window, so there is nothing to start; if it was killed after that, it comes back without the icon.
--challenge TEXT: only show the Anti-Bypass challenge for TEXT; exit code 0 = passed (used by the uninstaller).
--selftest REPORT: build check - the windows and every Api read method with a temporary data folder, result written
            to REPORT.
--retried: added by the agent to the one second try after a window that could not start (agent.py).
Any other flag is ignored (an old shortcut with --legacy or --new-ui just opens the window).
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import winreg

if "--selftest" in sys.argv:   # (before paths is imported: it reads the data folder once)
    os.environ["LOCKDOWN_DATA_DIR"] = tempfile.mkdtemp(prefix="lockdown-selftest-")

from gui import single_instance  # noqa: E402
from paths import ASSETS, command_line, gui_command  # noqa: E402
from ui_theme import ENGINE_KEY  # noqa: E402   # (the saved window choice of the removed switch, deleted once: forget_engine_choice)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WATCHDOG_TASK = "Lockdown Agent Watchdog"
APP_ID = "com.husarp.lockdown"   # Windows app identity: notifications / taskbar show "Lockdown" + its icon, not "Python"
ICON = ASSETS / "lockdown.ico"


def read_engine_setting() -> str | None:
    """The saved ui.engine by a plain read-only query (fast, no migrations); None when there is no readable DB."""
    from ui_theme import read_setting
    return read_setting(ENGINE_KEY)


def set_identity():
    """Register the app's name + icon for this user (no admin) and use them for this process."""
    import ctypes
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Lockdown")
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(ICON))
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def register_autostart():
    """Start the tray agent hidden at login (per-user, no admin), and a per-user task that brings it back within a
    minute if it's killed. Both are re-written on every launch (anti-bypass: `schtasks /Create /F` also re-enables a
    watchdog task the user disabled or edited, and restores a deleted Run value) - on a background thread, so the
    100-300 ms of `schtasks` no longer delays the window."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, "Lockdown", 0, winreg.REG_SZ, command_line(gui_command("--tray")))
    done = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "1", "/TN", WATCHDOG_TASK,
                           "/TR", command_line(gui_command("--watchdog"))], capture_output=True,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode != 0:
        raise OSError(f"schtasks /Create failed ({done.returncode}): {done.stderr!r}")


def _log(message: str):
    from paths import LOG_PATH
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},000 {message}\n")
    except OSError:
        pass


def forget_engine_choice():
    """One-off clean-up (0.86): the "Try the new window" switch is gone and a saved ui.engine decides nothing any more:
    the key is deleted. A plain query; a write only when the key is there."""
    import sqlite3
    from contextlib import closing
    import paths
    if read_engine_setting() is None:      # (read-only: nothing to do, or no database yet - none is made here)
        return
    with closing(sqlite3.connect(paths.DB_PATH, timeout=5)) as con:
        with con:
            con.execute("DELETE FROM settings WHERE key = ?", (ENGINE_KEY,))


def register_autostart_later():
    """register_autostart off the UI thread (a failure there must not stop the app starting - it is logged)."""
    def run():
        try:
            forget_engine_choice()
        except Exception as e:
            _log(f"WARNING Removing the old window choice failed: {e!r}")
        try:
            register_autostart()
        except Exception as e:
            _log(f"WARNING Autostart / watchdog task registration failed: {e!r}")
    threading.Thread(target=run, name="lockdown-autostart", daemon=True).start()


def watchdog_should_start() -> bool:
    if single_instance.running():
        return False
    # (a tray Exit only closes the icon and the window: the agent runs on, so a missing agent is always a killed one -
    #  it comes back; agent.py then leaves the icon out if the Exit was chosen)
    _log("WARNING Lockdown's agent wasn't running - started again by the watchdog")
    return True


def run(argv: list[str]) -> int:
    """Every start goes to the agent (the window, the tray, the challenge, the self-test); flags it does not know
    (--legacy, --new-ui from an old shortcut) are ignored."""
    import agent
    if "--selftest" in argv:
        return agent.main(argv)
    if "--watchdog" in argv and not watchdog_should_start():
        return 0
    set_identity()
    return agent.main(argv, on_first_instance=register_autostart_later)


if __name__ == "__main__":
    sys.exit(run(sys.argv))
