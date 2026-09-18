"""Desktop tracking in the tray agent (which sees the desktop), every 2 seconds:

- screen time: which app (and which site, in a browser) is in front, and whether you're active
- switches: every change to another app / site
- blocked items in use: time goes to every usage bucket their rules need (own day total,
  shared group limit, allowance of the current blocked stretch of an hours rule)
"""
import logging
import threading

from blocker.apps import list_processes
from blocker.hosts import normalize_host
from db import Database
from rules import effective_rules, switch_targets, usage_targets, visit_targets
from trusted_time import now_from_db

TICK_SEC = 2
IDLE_LIMIT_SEC = 15 * 60   # sites: no keyboard/mouse input for this long = away, don't count for limits
ACTIVE_IDLE_SEC = 5 * 60   # screen time: input within this = active (plan: 5 min idle threshold)
_UNSET = object()

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
    """(foreground exe, its browser URL or None, seconds since last input) - None exe when nothing is in front."""
    from monitor import browser_url, win
    hwnd, exe = win.foreground()
    if not hwnd:
        return None, None, win.idle_seconds()
    url = browser_url.browser_url(hwnd) if exe in browser_url.BROWSERS else None
    return exe, url, win.idle_seconds()


def site_of(url: str | None) -> str:
    try:
        return normalize_host(url) if url else ""
    except ValueError:
        return ""


class UsageTracker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.in_use: set[int] = set()   # item ids in use right now (read by the warning watcher)
        self.last_focus = _UNSET        # (exe, site) in front at the previous tick
        self.running: set[str] | None = None   # exes running at the previous tick (to spot app launches)
        self.last_used: dict[int, float] = {}  # item id -> when it was last in use (for "new visit")

    def run(self):
        import uiautomation as auto  # COM must be initialized in this thread
        with auto.UIAutomationInitializerInThread():
            db = Database()
            while not self.stop_event.wait(TICK_SEC):
                try:
                    self.tick(db, sense_desktop)
                except Exception:
                    log.exception("Usage tracking failed")

    def tick(self, db: Database, sense, running_exes=None):
        exe, url, idle_sec = sense()
        now = now_from_db(db)
        running = running_exes() if running_exes else {name for _pid, name in list_processes()}
        launched = running - self.running if self.running is not None else set()
        self.running = running
        switched = self.record_activity(db, exe, site_of(url), idle_sec, now)
        items = db.list_items()
        used = items_in_use(exe, url, idle_sec > IDLE_LIMIT_SEC, items)
        used_ids = {i["id"] for i in used}
        groups = db.list_groups()
        now_ts = now.timestamp()
        for item in items:
            is_app = item["item_type"] == "app"
            app_launched = is_app and item["target"].lower() in launched
            if not (app_launched or (not is_app and item["id"] in used_ids)):
                continue
            rules = effective_rules(item, groups)
            last = self.last_used.get(item["id"])
            away = None if last is None else now_ts - last
            # opening limits in "launches / new visits" mode
            db.add_usage(visit_targets(rules, item, now, app_launched, away), 1, now.date())
        for item in used:
            rules = effective_rules(item, groups)
            db.add_usage(usage_targets(rules, item["id"], now), TICK_SEC, now.date())
            if switched:   # you just switched to it (opening limits in "every switch" mode)
                db.add_usage(switch_targets(rules, item["id"], now), 1, now.date())
            self.last_used[item["id"]] = now_ts
        self.in_use = used_ids

    def record_activity(self, db: Database, exe: str | None, site: str, idle_sec: float, now) -> bool:
        """Screen time + switch log. Returns True if you just switched to another app/site."""
        focus = (exe, site) if exe else None     # None: nothing in front (e.g. locked)
        switched = False
        if exe:
            db.add_activity(now.strftime("%Y-%m-%d %H:%M"), exe, site, TICK_SEC,
                            TICK_SEC if idle_sec < ACTIVE_IDLE_SEC else 0)
            if self.last_focus is not _UNSET and focus != self.last_focus:
                db.add_switch(now, exe, site)
                switched = True
        self.last_focus = focus
        return switched
