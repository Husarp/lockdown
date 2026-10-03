"""Desktop tracking in the tray agent (which sees the desktop), twice a second:

- screen time: which app (and which site, in a browser) is in front, and whether you're active
- switches: every change to another app / site
- blocked items in use - the foreground one and the window in front on every other monitor: time goes to every
  usage bucket their rules need (own day total, shared group limit, allowance of the current blocked stretch of
  an hours rule)

Twice a second (0.84.2; it was every 2 seconds) so a limit or an allowance running out is noticed at once. The
time counted is the time that really passed either way - a faster tick changes how soon, not how much. To keep
that cheap: the windows in front are listed every tick (cheap), but a browser's address bar is read only when its
window or tab title changed, or URL_REFRESH_SEC after the last read; the process list (app launches) at most every
PROCESS_REFRESH_SEC unless a window in front is a new process; and the counted time is written every FLUSH_SEC in
one transaction - at once when a limit or allowance is about to be reached, on an opening or a switch, and on stop.
"""
import logging
import threading
import time

import block_method
import modes
import pause
import json

from blocker.apps import PROTECTED, app_folder, exe_name, in_folder, kills, list_processes, names_of
from blocker.hosts import normalize_host
from blocker.site_block import title_site
from db import Database
from rules import (Usage, block_targets, closed_opening, counted_rules, effective_rules, limit_targets,
                   switch_targets, usage_targets, visit_targets)
from trusted_time import now_from_db

TICK_SEC = 0.5         # (0.84.2: was 2) how soon things are noticed - not how much time is counted
FLUSH_SEC = 2          # counted time is written at most this often (sooner when a limit is close - UsageTracker.flush)
URL_REFRESH_SEC = 2    # an unchanged browser window's address bar is read again this often (_read_url)
PROCESS_REFRESH_SEC = 2   # the process list (to spot app launches) is read again this often (UsageTracker.running_now)
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
# Bumped (from the tracker's thread) when a write makes a limit or an allowance run out, or counts an opening: the tab check (word_guard) and the window's "Minimize" blocks re-read what is blocked at once rather
# than at their next periodic read, so a block that has just started is acted on within a tick.
limit_writes = 0

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
    """(foreground exe, its browser URL or None, seconds since last input, exe path or None, its pid or None,
    the other windows in front) - None exe when nothing is in front.

    The other windows in front (0.84.2): on a second monitor the window on top is in use too - a game left
    running there, a video playing - while you work on the first. One per window in front on any monitor other
    than the foreground one (win.front_windows: not minimized, not behind another window, not on another virtual
    desktop): (exe, its active tab's URL if it is a browser, exe path or None, pid). They count towards limits and
    allowances only; screen time stays what you were doing (the foreground)."""
    from monitor import browser_url, win
    hwnd, exe, path = win.foreground_process()
    if win.session_locked():   # locked: nothing is being used, whatever window was in front
        return None, None, win.idle_seconds(), None, None
    others = []
    try:
        for o_hwnd, o_pid, o_exe, o_path in win.front_windows():
            if o_hwnd == hwnd:
                continue
            others.append((o_exe, _read_url(browser_url, win, o_hwnd, True) if o_exe in browser_url.BROWSERS
                           else None, o_path or None, o_pid))
    except Exception:
        log.exception("Couldn't list the windows in front on the other monitors")
    if not hwnd:
        return None, None, win.idle_seconds(), None, None, others
    url = _read_url(browser_url, win, hwnd, False) if exe in browser_url.BROWSERS else None
    return exe or None, url, win.idle_seconds(), path or None, win.window_pid(hwnd) or None, others


NO_BAR_RETRY_SEC = 30
NO_BAR_AFTER = 3   # failed reads in a row before a browser window counts as having no address bar
_no_bar: dict[int, tuple[int, float]] = {}   # browser window -> (failed reads in a row, when last looked)
# browser window -> (its title, its URL - None if never read under that title -, when it was last asked)
_seen: dict[int, tuple[str, str | None, float]] = {}


def _read_url(browser_url, win, hwnd: int, back_off: bool) -> str | None:
    """The active tab's URL of a browser window, by UI Automation.

    Reading the address bar (UI Automation, a call into the browser's process) is the expensive part of a tick, so
    at two ticks a second (0.84.2) it is read only when the URL can have changed: another window, another tab title
    than at the last read, or URL_REFRESH_SEC since it. A page can change its address and keep its title (a
    single-page site; a link followed before the new page has named itself), so a move to a blocked site is
    still caught within URL_REFRESH_SEC - no later than when the tracker ticked every 2 seconds.

    When the address bar can't be read - hidden while a video plays full screen, the browser busy - the URL last
    read from the same window still counts as long as the window's title (the tab's title) hasn't changed: a
    YouTube video in full screen is still YouTube. A window not in the foreground (`back_off`) that fails
    NO_BAR_AFTER times in a row has no address bar (an installed web app): it is looked at again only every
    NO_BAR_RETRY_SEC, as searching its UI tree each tick would be slow; one that won't answer is still its app,
    just without a site. A failed read is not repeated every tick either, while the title stays the same (a
    browser window in front with no address bar - an installed web app - used to be searched twice a second): it
    is asked again after URL_REFRESH_SEC, or at once when the title changes - as often as at the 2-second tick.

    No address read under this title (the bar hidden, and autoplay or a playlist moved the full-screen video on to
    the next one, or a browser web app): the site the title names, if it names one ("Cats - YouTube — Mozilla
    Firefox" is youtube.com - site_block.title_site, 0.84.9). That video used to count as no site at all."""
    title = win.window_title(hwnd)
    url = _read_bar(browser_url, win, hwnd, back_off, title)
    return url if url is not None else title_site(title)


def _read_bar(browser_url, win, hwnd: int, back_off: bool, title: str) -> str | None:
    now = time.monotonic()
    last = _seen.get(hwnd)
    if last and title and last[0] == title and 0 <= now - last[2] < URL_REFRESH_SEC:
        return last[1]
    fails, when = _no_bar.get(hwnd, (0, 0.0))
    if not (back_off and fails >= NO_BAR_AFTER and now - when < NO_BAR_RETRY_SEC):
        try:
            url = browser_url.browser_url(hwnd)
        except Exception:
            url = None
        if url is not None:
            _no_bar.pop(hwnd, None)
            if len(_seen) > 200:
                _seen.clear()
            _seen[hwnd] = (title, url, now)
            return url
        if len(_no_bar) > 200:
            _no_bar.clear()
        _no_bar[hwnd] = (fails + 1, now)
        if title:   # (the same page: asked again in URL_REFRESH_SEC, not every tick - the last URL still counts)
            if len(_seen) > 200:
                _seen.clear()
            _seen[hwnd] = (title, last[1] if last and last[0] == title else None, now)
    return last[1] if last and title and last[0] == title else None


def members(db: Database) -> dict:
    """{pid: [exe, ...]} the service published (MEMBERS_KEY)."""
    try:
        found = json.loads(db.get_setting(MEMBERS_KEY) or "{}")
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def owners_of(db: Database, pid: int | None, published: dict | None = None) -> frozenset:
    """The listed apps (exe names) the service says process `pid` is part of (MEMBERS_KEY)."""
    if not pid:
        return frozenset()
    try:
        return frozenset((members(db) if published is None else published).get(str(pid), ()))
    except (TypeError, AttributeError):
        return frozenset()


def site_of(url: str | None) -> str:
    try:
        return normalize_host(url) if url else ""
    except ValueError:
        return ""


# The sites a browser window in front showed lately: host -> when (time.monotonic()), noted by the tracker (the
# foreground window and the one in front on every other monitor) and by the tab check (word_guard, which sends a
# blocked tab back within half a second). A "<site> is blocked" notice is shown only when you were opening that
# site (0.84.10, LockdownApp._check_visits) - not for a page, a program or the DNS filter reaching it in the
# background (an embedded video, thumbnails, Discord's link previews, a browser's preconnect).
SHOWN_KEEP_SEC = 60
_shown: dict[str, float] = {}
_shown_lock = threading.Lock()   # (written by the tracker's and the tab check's threads, read by the window's)


def note_shown(url: str | None):
    host = site_of(url)
    if not host:
        return
    now = time.monotonic()
    with _shown_lock:
        _shown[host] = now
        if len(_shown) > 50:
            for h, t in list(_shown.items()):
                if not 0 <= now - t <= SHOWN_KEEP_SEC:
                    del _shown[h]


def shown_since(since: float) -> set[str]:
    """Hosts a browser window in front showed at or after `since` (time.monotonic())."""
    with _shown_lock:
        return {h for h, t in _shown.items() if t >= since}


class UsageTracker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.last_tick: float = 0.0     # when time was last counted - 0 until the first tick lands
        self.in_use: set[int] = set()   # item ids in use right now (read by the warning watcher)
        self.last_focus = _UNSET        # (exe, site) in front at the previous tick
        self.running: set[str] | None = None   # exes running at the previous tick (to spot app launches)
        self.last_used: dict[int, float] = {}  # item id -> when it was last in use (for "new visit")
        self.carry = 0.0                       # screen time: fraction of a second not yet counted
        self.counted_ts: float | None = None   # trusted time of the last tick that counted
        # Counted but not yet written (0.84.2) - flush() writes it all in one transaction:
        self.pending: dict[tuple[str, str], list] = {}   # (owner, bucket) -> [seconds, of which app-fed, day]
        self.activity: dict[tuple[str, str, str], list[int]] = {}   # (minute, exe, site) -> [seconds, active]
        self.switches: list[tuple] = []        # (when, exe, site)
        self.unflushed = 0.0                   # seconds counted since the last write
        self.urgent = False                    # write at the end of this tick (an opening, a switch)
        self.opened = False                    # an opening / a switch counted since the last write
        self.mark_ts: float | None = None      # the counted-until mark (COUNTED_KEY) as this tracker last wrote it
        self.limits: dict[tuple[str, str], int] = {}   # limit / allowance buckets in use -> seconds that block
        self.base: dict[tuple[str, str], int] = {}     # those buckets' seconds in the database at the last write
        self.proc_names: set[str] | None = None        # the process list (running_now), and when it was read
        self.proc_pids: set[int] = set()
        self.proc_read = 0.0

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
            try:
                while not self.stop_event.wait(TICK_SEC):
                    try:
                        self.tick(db, sense_desktop)
                        self.last_tick = time.time()
                    except Exception:
                        log.exception("Usage tracking failed")
            finally:   # stopping (the app closing), or starting again after a failure: nothing counted is lost
                try:
                    self.flush(db)
                except Exception:
                    log.exception("Couldn't write the last counted usage")

    def stop(self, timeout: float = 2.0):
        """Stop counting and write what is still waiting to be written (the app is closing)."""
        self.stop_event.set()
        if self.is_alive():
            self.join(timeout)

    def stalled_for(self, now: float | None = None) -> float:
        """Seconds since time was last counted. Anything much above TICK_SEC means use is going unrecorded."""
        now = time.time() if now is None else now
        return now - self.last_tick if self.last_tick else 0.0

    def elapsed(self, db: Database, now_ts: float) -> tuple[float, float]:
        """(seconds, app seconds) to count for this tick: the time since the last tick really took - not a fixed
        TICK_SEC - at least one tick, at most MAX_GAP_SEC. Fractions are kept: per usage bucket until they make
        a whole second (pending), and for screen time in `carry`.

        App seconds leave out what the service's backstop already counted for running apps while this tracker
        was stalled (it moved COUNTED_KEY past the mark we last wrote); sites and screen time have no backstop,
        so they get the whole gap. The app time still waiting to be written from before the stall is covered by
        what the service counted, so it is dropped (for the part of it the service's count spans)."""
        own = now_ts - self.counted_ts if self.counted_ts else TICK_SEC
        seconds = min(max(own, TICK_SEC), MAX_GAP_SEC)
        try:
            mark = float(db.get_setting(COUNTED_KEY) or 0)
        except ValueError:
            mark = 0.0
        if self.mark_ts and mark > self.mark_ts + 0.5:   # the service counted the apps in between
            waited = (self.counted_ts or 0) - self.mark_ts
            covered = min(max((mark - self.mark_ts) / waited, 0.0), 1.0) if waited > 0 else 1.0
            for entry in self.pending.values():
                entry[0] -= entry[1] * covered
                entry[1] = 0.0
            self.mark_ts = mark
            self.urgent = True   # (write our own mark again soon: the service sees the tray counting)
            return seconds, min(max(now_ts - mark, 0.0), seconds)
        return seconds, seconds

    def running_now(self, pids: set[int]) -> set[str]:
        """Names of the running programs (to spot app launches), from a process list read at most every
        PROCESS_REFRESH_SEC - and at once when a window in front belongs to a process the last list didn't have
        (a program just started, or closed and started again: a new process). A launch in the background is
        still seen, within PROCESS_REFRESH_SEC, as it was when the tracker ticked every 2 seconds."""
        now = time.monotonic()
        if self.proc_names is None or not pids <= self.proc_pids \
                or not 0 <= now - self.proc_read < PROCESS_REFRESH_SEC:
            procs = list_processes()
            self.proc_names = {name for _pid, name in procs}
            self.proc_pids = {pid for pid, _name in procs} | pids   # (one that isn't listed isn't asked about again)
            self.proc_read = now
        return self.proc_names

    def tick(self, db: Database, sense, running_exes=None):
        exe, url, idle_sec, *more = sense()
        path = more[0] if more else None
        pid = more[1] if len(more) > 1 else None
        others = more[2] if len(more) > 2 else ()
        for shown in (url, *(o[1] for o in others)):   # (before any database read: a lock can't lose a sighting)
            note_shown(shown)
        published = members(db) if exe or others else {}
        owners = owners_of(db, pid, published) if exe else frozenset()
        now = now_from_db(db)
        now_ts = now.timestamp()
        # Everything read from the database first, before anything is counted: a read that fails (a lock) then
        # fails the tick before it has changed anything - not after the screen time, a launch or the service's
        # cover had been taken in, which the next tick would have counted twice or not at all.
        items = db.list_items()
        categories = db.categories()
        groups = db.list_groups()
        clock = db.limit_clock()
        running = running_exes() if running_exes else self.running_now({p for p in (pid, *(o[3] for o in others)) if p})
        seconds, app_seconds = self.elapsed(db, now_ts)
        launched = running - self.running if self.running is not None else set()
        self.running = running
        total = seconds + self.carry   # screen time is whole seconds per minute row: the fraction waits
        screen = int(total)
        self.carry = total - screen
        switched = self.record_activity(exe, site_of(url), idle_sec, now, screen)
        # Never "idle" for limits: the site in the foreground tab counts like the app in front does, input or not.
        # 15 minutes without keyboard or mouse used to stop the count - and a video watched without touching
        # anything is exactly that, so a group's "15 min allowed during blocked hours" never ran out and YouTube
        # stayed open all night (0.84.1). Only a locked session stops it (sense_desktop: nothing in front).
        used = items_in_use(exe, url, False, items, categories, path, owners)
        focused_ids = {i["id"] for i in used}
        # The window in front on each other monitor is in use too (0.84.2) - a game left on the second screen while
        # you browse on the first. Each item counts once a tick, however many windows show it (never double time).
        for o_exe, o_url, o_path, o_pid in others:
            if o_exe or o_url:
                used += items_in_use(o_exe, o_url, False, items, categories, o_path,
                                     owners_of(db, o_pid, published) if o_exe else frozenset())
        used = list({i["id"]: i for i in used}.values())
        used_ids = {i["id"] for i in used}
        for item in items:
            is_app = item["item_type"] == "app"
            app_launched = is_app and bool(names_of(item["target"]) & launched)   # (Steam may start the Shipping exe)
            if not (app_launched or (not is_app and item["id"] in used_ids)):
                continue
            rules = counted_rules(item, groups)
            last = self.last_used.get(item["id"])
            away = None if last is None else now_ts - last
            # opening limits in "launches / new visits" mode - one opening can go over one: written at once
            if opened := self.unless_closed(db, item, groups, visit_targets(rules, item, now, app_launched, away,
                                                                            clock), now, clock):
                self._add(opened, 1, False, now)
                self.urgent = self.opened = True
        # Each bucket gets this tick's time once, however many items in use feed it: a shared group (or category)
        # limit with the game on monitor 2 and YouTube on monitor 1 counts real time, not twice it. App time where
        # an app feeds it (the service's backstop may already have counted the apps' part of the gap).
        buckets: dict[tuple[str, str], tuple[float, bool]] = {}
        limits: dict[tuple[str, str], int] = {}
        switch_to: set[tuple[str, str]] = set()
        for item in used:
            rules = counted_rules(item, groups)
            is_app = item["item_type"] == "app"
            spent = app_seconds if is_app else seconds
            for target in usage_targets(rules, item["id"], now, clock):
                old = buckets.get(target)
                buckets[target] = (min(old[0], spent), old[1] or is_app) if old else (spent, is_app)
            for target, limit in limit_targets(rules, now, clock).items():
                limits[target] = min(limits.get(target, limit), limit)
            if switched and item["id"] in focused_ids:   # you just switched to it ("every switch" opening limits)
                switch_to |= self.unless_closed(db, item, groups, switch_targets(rules, item["id"], now, clock),
                                                now, clock)   # (one switch: once per bucket too)
            self.last_used[item["id"]] = now_ts
        for target, (spent, app_fed) in buckets.items():
            self._add((target,), spent, app_fed, now)
        if switch_to:
            self._add(switch_to, 1, False, now)
            self.opened = True
        self.in_use = used_ids
        self.limits = limits
        self.counted_ts = now_ts
        self.unflushed += seconds
        near = self.near_limit(db)
        if near or self.urgent or self.mark_ts is None or self.unflushed >= FLUSH_SEC:
            self.flush(db)

    @staticmethod
    def unless_closed(db: Database, item: dict, groups: list[dict], targets: set, now, clock) -> set:
        """The opening counters to add 1 to: not the ones an app that is closed at once would spend
        (rules.closed_opening) - so retrying a blocked member doesn't use up the group's openings (0.84.3).
        Only for an app Lockdown closes when blocked: one that is only minimized or cut off the internet can
        still be used, so its openings count. Only the rules whose block closes it count here: a group that
        cuts its members' internet only doesn't close them, whatever the app's own setting (0.84.11). Anything
        that can't be read counts everything."""
        if not any(t[1].startswith("op:") for t in targets) or item["item_type"] != "app" \
                or names_of(item["target"]) & PROTECTED:
            return targets
        try:
            by_id = {g["id"]: g for g in groups}
            rules = [r for r in effective_rules(item, groups) if kills(block_method.rule_way(item, by_id, r))]
            if not rules:
                return targets
            unlocks = {f"item:{i}": u for i, u in db.active_unlocks(now).items()}
            usage = Usage(db.usage_of(block_targets(rules, now, clock) | set(targets)), clock, unlocks,
                          pause.until(db, now))   # (paused: it isn't closed, so its opening counts)
            return closed_opening(rules, targets, now, usage)
        except Exception:
            log.exception("Could not tell whether %s is blocked - counting its opening", item["display_name"])
            return targets

    def _add(self, targets, seconds: float, app_fed: bool, now):
        """Count `seconds` (or openings) in every (owner, bucket) of `targets` - in memory until flush()."""
        day = now.date()
        for target in targets:
            entry = self.pending.get(target)
            if entry is None:
                entry = self.pending[target] = [0.0, 0.0, day]
            entry[0] += seconds
            if app_fed:
                entry[1] += seconds
            entry[2] = day

    def near_limit(self, db: Database) -> bool:
        """Is a time limit or an allowance in use about to be reached - within FLUSH_SEC of what is counted - and
        not reached in the database yet? Then every tick is written at once, so the database (which the service
        and the tab check block by) reaches the limit on the very tick the time is used up, not up to FLUSH_SEC
        later. One already over in the database (a disabled rule, a page not yet sent back) needs no hurry."""
        if missing := [t for t in self.limits if t not in self.base]:
            if len(self.base) > 500:
                self.base.clear()
            self.base.update(db.usage_of(missing))
        for target, limit in self.limits.items():
            have = self.base.get(target, 0)
            entry = self.pending.get(target)
            if have < limit <= have + (entry[0] if entry else 0) + FLUSH_SEC:
                return True
        return False

    def flush(self, db: Database):
        """Write what has been counted since the last write - usage (whole seconds; the fractions wait in pending),
        screen time and switches - and the counted-until mark, in one transaction. Nothing is forgotten until
        it is written: if the write fails, it is all tried again on the next tick."""
        usage = [(owner, bucket, entry[2], int(entry[0])) for (owner, bucket), entry in self.pending.items()
                 if entry[0] >= 1]
        activity = [(*key, sec, active) for key, (sec, active) in self.activity.items() if sec or active]
        mark = f"{self.counted_ts:.3f}" if self.counted_ts else None
        if usage or activity or self.switches or (mark and float(mark) != self.mark_ts):
            db.add_tracked(usage, activity, self.switches, {COUNTED_KEY: mark} if mark else {})
        for owner, bucket, _day, whole in usage:
            entry = self.pending[(owner, bucket)]
            entry[0] -= whole
            entry[1] = min(entry[1], entry[0])
        if len(self.pending) > 1000:   # (old buckets' fractions of a second: days of them)
            self.pending = {k: e for k, e in self.pending.items() if k in self.limits}
        self.activity, self.switches = {}, []
        self.unflushed, self.urgent = 0.0, False
        if mark:
            self.mark_ts = float(mark)
        opened, self.opened = self.opened, False
        before, self.base = self.base, {}   # (until read again: a failed read leaves no stale counts behind)
        self.base = db.usage_of(self.limits) if self.limits else {}
        # Tell the tab check and the "Minimize" blocks (limit_writes) when this write made a limit or an allowance
        # run out - or counted an opening, which can go over an opening limit - so they act on it at once. Only
        # then: not on every write of the last FLUSH_SEC before it (each of those makes them read every block).
        if opened or any(before.get(t, 0) < limit <= self.base.get(t, 0) for t, limit in self.limits.items()):
            global limit_writes
            limit_writes += 1

    def record_activity(self, exe: str | None, site: str, idle_sec: float, now, seconds: int) -> bool:
        """Screen time + switch log (written by flush()). Returns True if you just switched to another app/site."""
        focus = (exe, site) if exe else None     # None: nothing in front (e.g. locked)
        switched = False
        if exe:
            if seconds:
                entry = self.activity.setdefault((now.strftime("%Y-%m-%d %H:%M"), exe, site), [0, 0])
                entry[0] += seconds
                entry[1] += seconds if idle_sec < ACTIVE_IDLE_SEC else 0
            if self.last_focus is not _UNSET and focus != self.last_focus:
                self.switches.append((now, exe, site))
                self.urgent = True
                switched = True
        self.last_focus = focus
        return switched
