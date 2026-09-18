"""Lockdown enforcement service.

Every few seconds: evaluate block rules (permanent / scheduled / temporary), make the hosts file match
(repairing manual edits), keep browser DoH/QUIC locked off, and close open connections to newly blocked
sites. A listener on 127.0.0.1:80/443 records attempts to open blocked sites for the tray agent to notify.
Needs admin/SYSTEM rights.

Usage:
    python src/service.py run              # enforcement loop (what the scheduled task runs)
    python src/service.py once             # single pass, for testing
    python src/service.py remove-policies  # undo the browser policies (used on uninstall)
"""
import ctypes
import logging
import sys
import threading
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from blocker import browser_policy, connections, hosts
from blocker.listener import BlockListener
from db import Database
from paths import DATA_DIR, LOG_PATH

INTERVAL_SEC = 5
HEARTBEAT_KEY = "service_heartbeat"
# Keep closing connections to a newly blocked site for this long (browsers cache DNS ~1 min).
CLOSE_CONNECTIONS_FOR = timedelta(minutes=3)
VISIT_DEDUPE_SEC = 10

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


class Enforcer:
    def __init__(self, db: Database):
        self.db = db
        self.blocks: dict[str, dict] = {}       # current active blocks, read by the listener
        self.closing: dict[str, datetime] = {}  # ip -> keep closing connections until
        self.visit_db = None                    # separate connection for listener threads
        self.visit_lock = threading.Lock()
        self.last_visit: dict[str, float] = {}

    def enforce_once(self):
        now = datetime.now()
        if self.db.delete_expired_temporary(now):
            log.info("Removed expired temporary blocks")
        blocks = self.db.active_blocks(now)

        newly_blocked = sorted(set(blocks) - set(self.blocks))
        if newly_blocked:
            # resolve before the hosts file points them at 127.0.0.1
            for ip in connections.resolve(hosts.expand(newly_blocked)):
                self.closing[ip] = now + CLOSE_CONNECTIONS_FOR

        if hosts.apply(sorted(blocks)):
            hosts.flush_dns()
            log.info("Hosts file updated: %d hostnames blocked", len(blocks))
        self.blocks = blocks

        if browser_policy.apply():
            log.info("Browser DoH/QUIC policies (re)applied")

        self.closing = {ip: until for ip, until in self.closing.items() if until > now}
        if self.closing:
            closed = connections.close_to(set(self.closing))
            if closed:
                log.info("Closed %d open connections to blocked sites", closed)

        self.db.set_setting(HEARTBEAT_KEY, str(time.time()))

    def on_visit(self, hostname: str):
        """Called by listener threads when a browser tries to open a blocked hostname."""
        blocks = self.blocks
        block = blocks.get(hostname) or blocks.get(hostname.removeprefix("www."))
        if not block:
            return
        with self.visit_lock:
            if time.time() - self.last_visit.get(hostname, 0) < VISIT_DEDUPE_SEC:
                return
            self.last_visit[hostname] = time.time()
            if self.visit_db is None:
                self.visit_db = Database()
            item = block["item"]
            self.visit_db.add_block_event(hostname, item["id"], item["display_name"], block["reason"], block["until"])


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("run", "once", "remove-policies"):
        print(__doc__)
        return 2
    setup_logging()
    if not ctypes.windll.shell32.IsUserAnAdmin():
        log.error("Not running as administrator - cannot write the hosts file")
        return 1
    if cmd == "remove-policies":
        browser_policy.remove()
        log.info("Browser policies removed")
        return 0
    enforcer = Enforcer(Database())
    if cmd == "once":
        enforcer.enforce_once()
        return 0
    log.info("Service started")
    BlockListener(enforcer.on_visit, log).start()
    while True:
        try:
            enforcer.enforce_once()
        except Exception:
            log.exception("Enforcement pass failed")
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    sys.exit(main())
