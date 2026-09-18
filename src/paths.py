"""Shared filesystem locations."""
import os
from pathlib import Path

# Shared between the GUI (runs as user) and the service (runs as SYSTEM).
# LOCKDOWN_DATA_DIR overrides it for tests/development.
DATA_DIR = Path(os.environ.get("LOCKDOWN_DATA_DIR", r"C:\ProgramData\Lockdown"))
DB_PATH = DATA_DIR / "config.db"
LOG_PATH = DATA_DIR / "lockdown.log"
HOSTS_BACKUP_PATH = DATA_DIR / "hosts.backup"
HOSTS_PATH = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
