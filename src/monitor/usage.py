"""Counts time spent on blocked items (sites and apps) - runs in the tray agent, which sees the desktop.

Time goes to every usage bucket the item's rules need: its own day total, a shared group limit,
and the allowance of the current blocked stretch of an hours rule.
"""
import logging
import threading

from blocker.hosts import normalize_host
from db import Database
from rules import effective_rules, usage_targets
from trusted_time import now_from_db

TICK_SEC = 2
IDLE_LIMIT_SEC = 15 * 60   # sites: no keyboard/mouse input for this long = away, don't count

log = logging.getLogger("lockdown.usage")


def match_item(host: str, items: list[dict]) -> dict | None:
    """The site item whose hostnames cover `host` (exact or subdomain)."""
    for item in items:
        for h in item["target"].split():
            if host == h or host.endswith("." + h):
                return item
    return None


def items_in_use(exe: str | None, url: str | None, idle: bool, items: list[dict]) -> list[dict]:
    """Items being used: the foreground app (games count even without keyboard/mouse input), and the site
    open in the foreground browser tab (only while you're not away)."""
    used = [i for i in items if i["item_type"] == "app" and exe and i["target"].lower() == exe]
    if url and not idle:
        try:
            site = match_item(normalize_host(url), [i for i in items if i["item_type"] == "site"])
        except ValueError:   # e.g. search text typed in the address bar
            site = None
        if site:
            used.append(site)
    return used


def sense_desktop():
    """(foreground exe, its browser URL or None, idle?) - None exe when nothing is in front (e.g. locked)."""
    from monitor import browser_url, win
    hwnd, exe = win.foreground()
    if not hwnd:
        return None, None, True
    url = browser_url.browser_url(hwnd) if exe in browser_url.BROWSERS else None
    return exe, url, win.idle_seconds() > IDLE_LIMIT_SEC


class UsageTracker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.in_use: set[int] = set()   # item ids in use right now (read by the warning watcher)

    def run(self):
        import uiautomation as auto  # COM must be initialized in this thread
        with auto.UIAutomationInitializerInThread():
            db = Database()
            while not self.stop_event.wait(TICK_SEC):
                try:
                    self.tick(db, sense_desktop)
                except Exception:
                    log.exception("Usage tracking failed")

    def tick(self, db: Database, sense):
        exe, url, idle = sense()
        used = items_in_use(exe, url, idle, db.list_items())
        if used:
            now = now_from_db(db)
            groups = db.list_groups()
            for item in used:
                db.add_usage(usage_targets(effective_rules(item, groups), item["id"], now), TICK_SEC, now.date())
        self.in_use = {i["id"] for i in used}
