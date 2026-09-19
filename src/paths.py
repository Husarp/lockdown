"""Shared filesystem locations - for the source checkout and for the installed .exe build (PyInstaller)."""
import os
import subprocess
import sys
from pathlib import Path

# Shared between the GUI (runs as user) and the service (runs as SYSTEM).
# LOCKDOWN_DATA_DIR overrides it for tests/development.
DATA_DIR = Path(os.environ.get("LOCKDOWN_DATA_DIR", r"C:\ProgramData\Lockdown"))
DB_PATH = DATA_DIR / "config.db"
LOG_PATH = DATA_DIR / "lockdown.log"
HOSTS_BACKUP_PATH = DATA_DIR / "hosts.backup"
HOSTS_PATH = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"

FROZEN = bool(getattr(sys, "frozen", False))   # running as Lockdown.exe / LockdownService.exe
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parents[1]   # install dir / project
ASSETS = (Path(getattr(sys, "_MEIPASS", APP_DIR)) if FROZEN else APP_DIR) / "assets"
SERVICE_NAME = "LockdownEnforcer"     # the Windows service (installed build)
TASK_NAME = "Lockdown Enforcer"       # the scheduled task that runs the service from source (scripts/)


def gui_command(*args: str) -> list[str]:
    """How to start the GUI / tray app (with arguments): Lockdown.exe, or pythonw + src/main.py from source."""
    if FROZEN:
        return [str(APP_DIR / "Lockdown.exe"), *args]
    pythonw = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
    return [str(pythonw), str(APP_DIR / "src" / "main.py"), *args]


def command_line(args: list[str]) -> str:
    return subprocess.list2cmdline(args)
