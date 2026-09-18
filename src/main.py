"""GUI + tray agent entry point: .venv\\Scripts\\pythonw.exe src\\main.py [--tray]

--tray: start hidden in the tray (used when starting with Windows).
"""
import queue
import sys
import winreg
from pathlib import Path

from gui import single_instance
from gui.app import LockdownApp

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def register_autostart():
    """Start the tray agent hidden at login (per-user, no admin). Re-written each launch so the path stays current."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    command = f'"{pythonw}" "{Path(__file__).resolve()}" --tray'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, "Lockdown", 0, winreg.REG_SZ, command)


if __name__ == "__main__":
    events: queue.Queue = queue.Queue()
    if not single_instance.acquire(events):
        sys.exit(0)  # already running - it will show its window
    register_autostart()
    LockdownApp(events, start_hidden="--tray" in sys.argv).mainloop()
