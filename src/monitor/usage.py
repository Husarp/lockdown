"""Desktop tracking in the tray agent (which sees the desktop), every 2 seconds:

- screen time: which app (and which site, in a browser) is in front, and whether you're active
- switches: every change to another app / site
- blocked items in use: time goes to every usage bucket their rules need (own day total,
  shared group limit, allowance of the current blocked stretch of an hours rule)
"""
import logging
import threading
import time

import modes
import json

from blocker.apps import app_folder, exe_name, in_folder, list_processes, names_of
from blocker.hosts import normalize_host
from db import Database
from rules import counted_rules, switch_targets, usage_targets, visit_targets
from trusted_time import now_from_db

TICK_SEC = 2
RESTART_SEC = 30       # wait before starting the tracker again after it fell over
ACTIVE_IDLE_SEC = 5 * 60   # screen time: input within this = active (plan: 5 min idle threshold)
# Time is counted as it really passed between two ticks, not as TICK_SEC per tick: a tick that comes late (the
# window busy, a database lock, the PC under load) used to lose everything but 2 seconds of it. A longer gap than
# this is the PC asleep, not play - and while the tray isn't counting at all, the service counts instead.
MAX_GAP_SEC = 60
# Setting: trusted unix time up to which use has been counted - by this tracker, or by the service's backstop
# (service.Enforcer.count_unwatched_apps) while this tracker isn't running. One mark, so nothing is counted twice.
COUNTED_KEY = "usage.counted_until"
# Setting: JSON {pid: [exe, ...]} the service publishes - running processes that belong to a listed app other than by
# name: what it started from its own folder (after the starter has gone too), or a program from its game folder
# (service.Enforcer.publish_members). The tray counts the window in front by this as well.
MEMBERS_KEY = "apps.members"
_UNSET = object()

log = logging.getLogger("lockdown.usage")


def match_item(host: str, items: list[dict]) -> dict | None:
    """The site item whose hostnames cover `host` (exact or subdomain)."""
    for item in items:
        for h in item["target"].split():
            if host == h or host.endswith("." + h):
                return item
    return None


def app_matches(item: dict, exe: str, path: str | None, owners: frozenset | set = frozenset()) -> bool:
    """Is this app item the program `exe` (running from `path`)? Its own exe (also when the target was typed as
    a full path; an Unreal game's Game-Win64-Shipping.exe for Game.exe - apps.names_of), a process the service
    says is part of it (`owners`: what it started, or a program from its game folder - also when this process
    can't read the game's path, as with anti-cheat), or any program from its folder (apps.app_folder: a game's
    whole folder, or the install folder of an app that also closes its background processes)."""
    if exe in names_of(item["target"]) or exe_name(item["target"]) in owners:
        return True
    folder = app_folder(item)
    return bool(folder) and in_folder(path, folder)


def items_in_use(exe: str | None, url: str | None, idle: bool, items: list[dict],
                 categories: dict[tuple[str, str], str] | None = None, path: str | None = None,
                 owners: frozenset | set = frozenset()) -> list[dict]:
    """Items being used: the foreground app (games count even without keyboard/mouse input), and the site
    open in the foreground browser tab (only while you're not away). A blocker on a whole category counts as
    in use whenever one of its members is, so its time limit is one pot for everything in the category."""
    used = [i for i in items if i["item_type"] == "app" and exe and app_matches(i, exe, path, owners)]
    if url and not idle:
        try:
            site = match_item(normalize_host(url), [i for i in items if i["item_type"] == "site"])
        except ValueError:   # e.g. search text typed in the address bar
            site = None
        if site:
            used.append(site)
    cat_items = [i for i in items if i["item_type"] == "category"]
    if cat_items:
        cats = used_categories(exe, url, idle, used, categories or {})
        used += [i for i in cat_items if i["target"] in cats]
    return used


def used_categories(exe: str | None, url: str | None, idle: bool, used: list[dict],
                    categories: dict[tuple[str, str], str]) -> set[str]:
    """The categories the thing in front belongs to - what you set on Screen Time for this exe / site, and the
    category of any blocklist item it matched (those count as Distracting unless you said otherwise)."""
    out = {modes.item_category(i, categories) for i in used}
    if exe:
        out.add(categories.get(("app", exe.lower()), ""))
    if url and not idle:
        try:
            host = normalize_host(url)
        except ValueError:
            host = None
        if host:
            for (kind, name), cat in categories.items():
                if kind == "site" and (name == host or host.endswith("." + name)):
                    out.add(cat)
    return out - {""}


def sense_desktop():
    """(foreground exe, its browser URL or None, seconds since last input, exe path or None, its pid or None) -
    None exe when nothing is in front."""
    from monitor import browser_url, win
    hwnd, exe, path = win.foreground_process()
    if not hwnd or win.session_locked():   # locked: nothing is being used, whatever window was in front
        return None, None, win.idle_seconds(), None, None
    url = browser_url.browser_url(hwnd) if exe in browser_url.BROWSERS else None
    return exe or None, url, win.idle_seconds(), path or None, win.window_pid(hwnd) or None


def owners_of(db: Database, pid: int | None) -> frozenset:
    """The listed apps (exe names) the service says process `pid` is part of (MEMBERS_KEY)."""
    if not pid:
        return frozenset()
    try:
        return frozenset(json.loads(db.get_setting(MEMBERS_KEY) or "{}").get(str(pid), ()))
    except (ValueError, AttributeError):
        return frozenset()


def site_of(url: str | None) -> str:
    try:
        return normalize_host(url) if url else ""
    except ValueError:
        return ""


class UsageTracker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.last_tick: float = 0.0     # when time was last counted - 0 until the first tick lands
        self.in_use: set[int] = set()   # item ids in use right now (read by the warning watcher)
        self.last_focus = _UNSET        # (exe, site) in front at the previous tick
        self.running: set[str] | None = None   # exes running at the previous tick (to spot app launches)
        self.last_used: dict[int, float] = {}  # item id -> when it was last in use (for "new visit")
        self.carry = 0.0                       # fraction of a second not yet counted
        self.counted_ts: float | None = None   # trusted time of the last tick that counted

    def run(self):
        """Keep counting, whatever happens.

        Only the tick used to be guarded, so anything that went wrong while STARTING - the COM initializer,
        or opening the database while the service was restarting and holding it - killed this thread on the
        spot, logged nothing at all (the logging was inside the loop it never reached), and nothing ever
        started it again. The app carried on and blocking carried on, because the service does that; time
        simply stopped being counted, silently, until the app was restarted. Hours of use vanished that way.
        Now every failure is written down and the tracker starts itself again."""
        while not self.stop_event.is_set():
            try:
                self._count(db=Database())
            except Exception:
                log.exception("Usage tracking stopped - starting it again in %ds", RESTART_SEC)
                self.stop_event.wait(RESTART_SEC)

    def _count(self, db):
        import uiautomation as auto  # COM must be initialized in this thread
        with auto.UIAutomationInitializerInThread():
            while not self.stop_event.wait(TICK_SEC):
                try:
                    self.tick(db, sense_desktop)
                    self.last_tick = time.time()
                except Exception:
                    log.exception("Usage tracking failed")

    def stalled_for(self, now: float | None = None) -> float:
        """Seconds since time was last counted. Anything much above TICK_SEC means use is going unrecorded."""
        now = time.time() if now is None else now
        return now - self.last_tick if self.last_tick else 0.0

    def elapsed(self, db: Database, now_ts: float) -> tuple[int, int]:
        """(seconds, app seconds) to count for this tick: the time since the last tick really took - not a fixed
        TICK_SEC - at least one tick, at most MAX_GAP_SEC, the fraction carried to the next tick.

        App seconds leave out what the service's backstop already counted for running apps while this tracker
        was stalled (it moved COUNTED_KEY past our last tick); sites and screen time have no backstop, so they
        get the whole gap."""
        own = now_ts - self.counted_ts if self.counted_ts else TICK_SEC
        total = min(max(own, TICK_SEC), MAX_GAP_SEC) + self.carry
        seconds = int(total)
        self.carry = total - seconds
        try:
            mark = float(db.get_setting(COUNTED_KEY) or 0)
        except ValueError:
            mark = 0.0
        if self.counted_ts and mark > self.counted_ts + 0.5:   # the service counted the apps in between
            return seconds, int(min(max(now_ts - mark, 0), seconds))
        return seconds, seconds

    def tick(self, db: Database, sense, running_exes=None):
        exe, url, idle_sec, *more = sense()
        path = more[0] if more else None
        owners = owners_of(db, more[1] if len(more) > 1 else None) if exe else frozenset()
        now = now_from_db(db)
        seconds, app_seconds = self.elapsed(db, now.timestamp())
        running = running_exes() if running_exes else {name for _pid, name in list_processes()}
        launched = running - self.running if self.running is not None else set()
        self.running = running
        switched = self.record_activity(db, exe, site_of(url), idle_sec, now, seconds)
        items = db.list_items()
        # Never "idle" for limits: the site in the foreground tab counts like the app in front does, input or not.
        # 15 minutes without keyboard or mouse used to stop the count - and a video watched without touching
        # anything is exactly that, so a group's "15 min allowed during blocked hours" never ran out and YouTube
        # stayed open all night (0.84.1). Only a locked session stops it (sense_desktop: nothing in front).
        used = items_in_use(exe, url, False, items, db.categories(), path, owners)
        used_ids = {i["id"] for i in used}
        groups = db.list_groups()
        clock = db.limit_clock()
        now_ts = now.timestamp()
        for item in items:
            is_app = item["item_type"] == "app"
            app_launched = is_app and bool(names_of(item["target"]) & launched)   # (Steam may start the Shipping exe)
            if not (app_launched or (not is_app and item["id"] in used_ids)):
                continue
            rules = counted_rules(item, groups)
            last = self.last_used.get(item["id"])
            away = None if last is None else now_ts - last
            # opening limits in "launches / new visits" mode
            db.add_usage(visit_targets(rules, item, now, app_launched, away, clock), 1, now.date())
        for item in used:
            rules = counted_rules(item, groups)
            spent = app_seconds if item["item_type"] == "app" else seconds
            db.add_usage(usage_targets(rules, item["id"], now, clock), spent, now.date())
            if switched:   # you just switched to it (opening limits in "every switch" mode)
                db.add_usage(switch_targets(rules, item["id"], now, clock), 1, now.date())
            self.last_used[item["id"]] = now_ts
        self.in_use = used_ids
        # (last: a tick that failed half-way is counted again by the next one - twice rather than not at all)
        db.set_setting(COUNTED_KEY, f"{now_ts:.3f}")
        self.counted_ts = now_ts

    def record_activity(self, db: Database, exe: str | None, site: str, idle_sec: float, now,
                        seconds: int = TICK_SEC) -> bool:
        """Screen time + switch log. Returns True if you just switched to another app/site."""
        focus = (exe, site) if exe else None     # None: nothing in front (e.g. locked)
        switched = False
        if exe:
            db.add_activity(now.strftime("%Y-%m-%d %H:%M"), exe, site, seconds,
                            seconds if idle_sec < ACTIVE_IDLE_SEC else 0)
            if self.last_focus is not _UNSET and focus != self.last_focus:
                db.add_switch(now, exe, site)
                switched = True
        self.last_focus = focus
        return switched
