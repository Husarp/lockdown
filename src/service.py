"""Lockdown enforcement service.

Every few seconds: read the blocklist from the database and make the hosts file match it
(repairing any manual edits). Needs admin/SYSTEM rights to write the hosts file.

Usage:
    python src/service.py run    # enforcement loop (what the scheduled task runs)
    python src/service.py once   # single pass, for testing
"""
import ctypes
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from blocker import hosts
from db import Database
from paths import DATA_DIR, LOG_PATH

INTERVAL_SEC = 5
HEARTBEAT_KEY = "service_heartbeat"

log = logging.getLogger("lockdown.service")


def setup_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)
    if sys.stdout:  # pythonw has no console
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        log.addHandler(console)
    log.setLevel(logging.INFO)


def enforce_once(db: Database):
    hostnames = db.blocked_hostnames()
    if hosts.apply(hostnames):
        hosts.flush_dns()
        log.info("Hosts file updated: %d hostnames blocked", len(hostnames))
    db.set_setting(HEARTBEAT_KEY, str(time.time()))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("run", "once"):
        print(__doc__)
        return 2
    setup_logging()
    if not ctypes.windll.shell32.IsUserAnAdmin():
        log.error("Not running as administrator - cannot write the hosts file")
        return 1
    db = Database()
    if cmd == "once":
        enforce_once(db)
        return 0
    log.info("Service started")
    while True:
        try:
            enforce_once(db)
        except Exception:
            log.exception("Enforcement pass failed")
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    sys.exit(main())
