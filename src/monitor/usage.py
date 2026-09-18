"""Counts time spent on sites that have a daily limit (runs in the tray agent, which sees the desktop)."""
import logging
import threading

from blocker.hosts import normalize_host
from db import Database
from trusted_time import now_from_db

TICK_SEC = 2
IDLE_LIMIT_SEC = 15 * 60   # no keyboard/mouse input for this long = away, don't count

log = logging.getLogger("lockdown.usage")


def match_item(host: str, items: list[dict]) -> dict | None:
    """The item whose hostnames cover `host` (exact or subdomain)."""
    for item in items:
        for h in item["target"].split():
            if host == h or host.endswith("." + h):
                return item
    return None


class UsageTracker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()

    def run(self):
        import uiautomation as auto  # COM must be initialized in this thread
        from monitor import browser_url
        with auto.UIAutomationInitializerInThread():
            db = Database()
            while not self.stop_event.wait(TICK_SEC):
                try:
                    self.tick(db, browser_url)
                except Exception:
                    log.exception("Usage tracking failed")

    @staticmethod
    def tick(db: Database, browser_url):
        if browser_url.idle_seconds() > IDLE_LIMIT_SEC:
            return
        url = browser_url.foreground_browser_url()
        if not url:
            return
        try:
            host = normalize_host(url)
        except ValueError:   # e.g. search text typed in the address bar
            return
        limited = [i for i in db.list_items() if any(r["rule_type"] == "time_limit" for r in i["rules"])]
        item = match_item(host, limited)
        if item:
            db.add_usage(item["id"], now_from_db(db).date(), TICK_SEC)
