"""Api for Screen Time (BLUEPRINT 3.5): the page shell's start-up answer, the Overview, Apps, Websites and Switches tabs, the
Calendar's month and day, and the CSV export.

`screentime_get(tab, range)` returns only that tab's data, and never reads more than the tab needs: Calendar reads no
range activity at all. Every number comes from small pre-summed pieces:
- one DIGEST per day (`_Digest`: active / at-the-PC seconds, seconds per app / site, active seconds per hour, sessions
  and the longest run in one app), worked out in a single pass over that day's minute rows. A day that is over can't
  change any more (a few minutes after midnight the tracker has written its last batch), so its digest is kept in
  memory (`_Cache`) and a range of 30 days is then 30 dictionary look-ups plus today's own digest - not a scan of 17 000
  minute rows. Nothing expires by the clock: what is kept is dropped by `forget` / `forget_all` (retention's roll-up
  calls the latter) and `warm` fills it in the background when the agent starts and after each midnight, so the first
  visit doesn't pay for it either;
- the daily totals (`stats.daily_active`, which also counts days retention has rolled up) for the trend (30 days plus the
  days before them that the first week's average needs);
- one grouped query for the switch / visit counts of the Apps and Websites tables.
Sessions and the longest focus are put together from the digests exactly as stats.sessions / stats.longest_focus would
give them on the whole range (a session or a run in one app that goes over midnight is joined), and a test compares them.

Nothing here can loosen a block. The Display settings (the tab / range Screen Time opens with) are written by
webapi/dashboard.py `display_set`; this module only reads them.
"""
import logging
import threading
import time
import weakref
from collections import Counter
from datetime import date, datetime, timedelta
from itertools import groupby
from operator import itemgetter

import emergency
import retention
import stats
from rules import DAY_NAMES

from . import categories, core, shared
from .core import ApiError

TABS = ("overview", "apps", "websites", "switches", "calendar")     # the ids of the page's tabs, in order
ST_TAB_KEY, ST_RANGE_KEY = "screentime.tab", "screentime.range"
GOAL_KEY, DEFAULT_GOAL_HOURS = "stats.goal_hours", "5"              # the daily goal in hours, "0" = off (Settings)
MAX_ROWS = 40                   # the Apps / Websites tables list this many at most ...
MIN_ROW_SEC = 60                # ... and only those with at least this much active time
MAX_SEGMENTS = 150              # the day timeline draws the longest ones anyway (charts.js MAX_SEGMENTS)
SETTLE_SEC = 180                # a day is kept only this long after it ended: the tracker writes in batches, so its last minute
                                # lands a few seconds after midnight (until then the day is worked out fresh each time)
MAX_DIGESTS = 45                # finished days kept: the 30-day range and its week, with room to spare
MAX_TOTALS = 400                # finished days' totals kept (the Calendar pages back through the months)
WARM_DELAY_SEC = 3.0            # `start_warmup` waits this long, so the window comes up first
WARM_CATALOG_WAIT_SEC = 120.0   # ... and then waits (at most) this long for the app catalog, whose names the tables use
WARM_RETRY_SEC = 600.0          # ... and tries again this long after a failure
NAMES_CACHE_SEC = 600
WEEK_LEAD_DAYS = 6              # days read before the trend's 30: a week average from a Monday can reach back that far
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAY_MIN = 1440
log = logging.getLogger("lockdown.screentime")


# ---------------------------------------------------------------- one day, summed once

class _Digest:
    """What one day's minute rows add up to. Built by `_digests`; the minute-of-day numbers are 0..1439."""
    __slots__ = ("active", "seconds", "pairs", "hours", "sessions", "best", "lead", "tail", "_run")

    def __init__(self):
        self.active = 0                    # seconds with input
        self.seconds = 0                   # seconds at the PC
        self.pairs = {}                    # (exe, site) -> active seconds, in the order the rows had them
        self.hours = [0] * 24              # active seconds per hour of the day
        self.sessions = []                 # [start, end) minute of day; a break of more than SESSION_GAP_MIN splits
        self.best = None                   # longest run of minutes in one app: (minutes, exe, start minute)
        self.lead = None                   # the run that starts at 00:00: (exe, minutes)
        self.tail = None                   # the run that ends at 23:59: (exe, minutes, start minute)
        self._run = None                   # [exe, minutes, start, last] while it goes on

    def minute(self, t: int, active: int, exe: str | None):
        """One minute of the day, in order: `active` seconds with input, `exe` = the app with most of them (or None when
        there were none, which ends a run)."""
        run = self._run
        if active > 0:
            sessions = self.sessions
            if sessions and t - sessions[-1][1] <= stats.SESSION_GAP_MIN:
                sessions[-1][1] = t + 1
            else:
                sessions.append([t, t + 1])
            if run is not None and run[0] == exe and t == run[3] + 1:
                run[1] += 1
                run[3] = t
            else:
                if run is not None:
                    self._close(run)
                run = self._run = [exe, 1, t, t]
            if self.best is None or run[1] > self.best[0]:
                self.best = (run[1], exe, run[2])
        elif run is not None:
            self._close(run)
            self._run = None

    def _close(self, run):
        if run[2] == 0:
            self.lead = (run[0], run[1])
        if run[3] == DAY_MIN - 1:
            self.tail = (run[0], run[1], run[2])

    def finish(self):
        if self._run is not None:
            self._close(self._run)
            self._run = None


_first = itemgetter(0)


def _digest_rows(rows) -> dict:
    """{"YYYY-MM-DD": _Digest} from activity rows (minute, exe, site, seconds, active_seconds) in minute order, one pass.
    The app of a minute is the row with most active seconds (the first one when equal) - what stats._minutes picks."""
    out: dict = {}
    d = None
    day = None
    for minute, group in groupby(rows, _first):
        if minute[:10] != day:
            if d is not None:
                d.finish()
            day = minute[:10]
            d = out[day] = _Digest()
        pairs = d.pairs
        total = at_pc = 0
        top = None
        top_active = -1
        for _m, exe, site, seconds, active in group:
            key = (exe, site)
            pairs[key] = pairs.get(key, 0) + active
            total += active
            at_pc += seconds
            if active > top_active:
                top_active, top = active, exe
        d.active += total
        d.seconds += at_pc
        t = int(minute[11:13]) * 60 + int(minute[14:16])
        d.hours[t // 60] += total
        d.minute(t, total, top if total > 0 else None)
    if d is not None:
        d.finish()
    return out


def _rolled_digest(rows) -> _Digest:
    """A day retention has rolled up has only its per-app / per-site totals left: no hours, sessions or runs."""
    d = _Digest()
    for exe, site, seconds, active in rows:
        d.pairs[(exe, site)] = d.pairs.get((exe, site), 0) + active
        d.active += active
        d.seconds += seconds
    return d


# ---------------------------------------------------------------- what is kept between calls

class _Cache:
    """Per agent (ctx.st_cache): finished days' digests and totals, the streaks, the app names. Plain dicts: a call that
    comes in on another thread at the same time may work a day out twice, which is harmless. Nothing here expires by the
    clock: a finished day is kept until `clear` (retention rolled days up / `forget`) or until newer days push it out."""

    def __init__(self):
        self.clear()
        _caches.add(self)

    def clear(self):
        self.days: dict = {}        # date -> _Digest
        self.totals: dict = {}      # date -> active seconds, or None when nothing was recorded that day
        self.streaks: dict = {}     # key -> {goal, no_unlock} of the days before today
        self.names = None           # (when, catalog loaded?, {exe: (name, path)})

    @staticmethod
    def trim(table: dict, limit: int):
        """Keep the newest `limit` days."""
        if len(table) > limit:
            for day in sorted(table)[:len(table) - limit]:
                table.pop(day, None)


_caches: "weakref.WeakSet[_Cache]" = weakref.WeakSet()


def _cache(ctx) -> _Cache:
    cache = ctx.__dict__.get("st_cache")
    if cache is None:
        cache = ctx.__dict__.setdefault("st_cache", _Cache())
    return cache


@core.internal
def forget(ctx):
    """Drop everything kept (tests; and anything that rewrites old activity)."""
    cache = ctx.__dict__.pop("st_cache", None)
    if cache is not None:
        cache.clear()


@core.internal
def forget_all():
    """Drop what every agent context keeps. Retention calls this after it rolled a day's minute rows up into daily totals
    (`retention.changed`), the one thing that rewrites old activity while the app runs."""
    for cache in list(_caches):
        cache.clear()


if forget_all not in retention.changed:
    retention.changed.append(forget_all)


def _activity_rows(db, start: date, end: date):
    """The minute rows for [start, end) - the ONE place this module reads them (a test counts the calls)."""
    return db.conn.execute("SELECT minute, exe, site, seconds, active_seconds FROM activity "
                           "WHERE minute >= ? AND minute < ? ORDER BY minute", (f"{start} 00:00", f"{end} 00:00"))


def _digests(req, days: list) -> dict:
    """{date: _Digest} for these days (any order). Days that are over come from the cache or are worked out in one query
    for the stretch that is missing, and kept; today (and a day that ended only minutes ago) is always worked out fresh."""
    cache, today, settled = req.cache, req.today, req.settled
    out, missing = {}, []
    for day in days:
        hit = cache.days.get(day) if day < settled else None
        if hit is not None:
            out[day] = hit
        else:
            missing.append(day)
    if missing:
        lo, hi = min(missing), max(missing)
        built = _digest_rows(_activity_rows(req.db, lo, hi + timedelta(days=1)))
        gone = [d for d in missing if d.isoformat() not in built and d < today]
        rolled = {}
        if gone:        # (days retention has rolled up: only their totals are left)
            for day, rows in groupby(req.db.conn.execute(
                    "SELECT day, exe, site, seconds, active_seconds FROM daily_activity WHERE day >= ? AND day <= ? "
                    "ORDER BY day, first_minute, exe, site", (min(gone).isoformat(), max(gone).isoformat())), _first):
                rolled[day] = _rolled_digest((r[1:] for r in rows))
        for day in missing:
            key = day.isoformat()
            d = built.get(key) or rolled.get(key) or _Digest()
            out[day] = d
            if day < settled:
                cache.days[day] = d
        cache.trim(cache.days, MAX_DIGESTS)
    return out


def _daily_totals(req, first: date, last: date) -> dict:
    """{date: active seconds} for first..last, for the days that have something recorded (a day with only idle time is
    there with 0, a day with nothing is not there). Finished days are kept; stats.daily_active does the sums in SQL and
    includes days retention has rolled up."""
    cache, settled = req.cache, req.settled
    out, missing = {}, []
    day = first
    while day <= last:
        if day < settled and day in cache.totals:
            if cache.totals[day] is not None:
                out[day] = cache.totals[day]
        else:
            missing.append(day)
        day += timedelta(days=1)
    if missing:
        lo, hi = min(missing), max(missing)
        found = stats.daily_active(req.db, lo, hi + timedelta(days=1))
        for day in missing:
            sec = found.get(day.isoformat())
            if sec is not None:
                out[day] = sec
            if day < settled:
                cache.totals[day] = sec
        cache.trim(cache.totals, MAX_TOTALS)
    return out


# ---------------------------------------------------------------- one request

def _goal(db) -> float | None:
    """The daily goal in seconds, or None when it is off."""
    try:
        hours = float(db.get_setting(GOAL_KEY, DEFAULT_GOAL_HOURS) or 0)
    except ValueError:
        hours = 0.0
    return hours * 3600 or None


def _css_color(cat: dict) -> str:
    """A built-in category keeps the theme's own colour until the owner picks one; their own colours are as chosen."""
    own = {"productive": "var(--cat-prod)", "neutral": "var(--cat-neutral)", "distracting": "var(--cat-dist)"}
    defaults = {c[0]: c[2] for c in categories.BUILTIN}
    if cat["key"] in own and cat["color"] in defaults[cat["key"]] and cat["color_light"] in defaults[cat["key"]]:
        return own[cat["key"]]
    return cat["color"]


class _Req:
    """Everything one `screentime_get` call shares; each piece is worked out the first time it is asked for."""

    def __init__(self, ctx, range_name: str):
        self.ctx = ctx
        self.db = ctx.db
        self.cache = _cache(ctx)
        self.now = ctx.now()
        self.today = self.now.date()
        self.settled = (self.now - timedelta(seconds=SETTLE_SEC)).date()      # (the days before this one are over for good)
        self.range = range_name
        self.start, self.end = stats.range_dates(range_name, self.today)        # [start, end)
        self.days = [self.start + timedelta(days=i) for i in range((self.end - self.start).days)]
        self._memo: dict = {}

    def memo(self, key, make):
        if key not in self._memo:
            self._memo[key] = make()
        return self._memo[key]

    @property
    def goal(self):
        return self.memo("goal", lambda: _goal(self.db))

    @property
    def cats(self) -> "_Cats":
        return self.memo("cats", lambda: _Cats(self.db))

    def digests(self, days: list) -> dict:
        return _digests(self, days)

    def names(self) -> dict:
        """{exe: (display name, path)} from the app catalog (once it has loaded); the blocklist's names win."""
        return self.memo("names", lambda: _names(self.ctx, self.cache))

    def app_name(self, exe: str) -> str:
        return self.app_info(exe)[0]

    def app_info(self, exe: str) -> tuple:
        """(name, path or None): the blocklist's name and path, else the Start menu's, else the exe made readable."""
        if exe == "pythonw.exe":                       # Lockdown's own window
            return "Lockdown", None
        item = self.cats.apps.get(exe)
        if item is not None:
            return item["display_name"], item.get("app_path") or None
        found = self.names().get(exe)
        if found:
            return found
        stem = exe[:-4] if exe.endswith(".exe") else exe
        return stem[:1].upper() + stem[1:], None


class _Cats:
    """The categories and who is in which, read once per call. category() gives what stats.category_of would."""

    def __init__(self, db):
        self.items = db.list_items()
        self.saved = db.categories()
        self.list = categories.load(db)
        self.keys = {c["key"] for c in self.list}
        self.colors = {c["key"]: _css_color(c) for c in self.list}
        self.names = {c["key"]: c["name"] for c in self.list}
        self.apps = {i["target"].lower(): i for i in self.items if i["item_type"] == "app"}
        self.hosts = {h for i in self.items if i["item_type"] == "site" for h in i["target"].split()}
        self._memo: dict = {}

    def category(self, kind: str, name: str) -> str:
        key = (kind, name)
        found = self._memo.get(key)
        if found is None:
            if key in self.saved:
                found = self.saved[key]
            elif kind == "app":
                found = "distracting" if name in self.apps else "neutral"
            else:
                parts = [name] + [name[i + 1:] for i, ch in enumerate(name) if ch == "."]
                found = "distracting" if any(p in self.hosts for p in parts) else "neutral"
            if found not in self.keys:
                found = "neutral"
            self._memo[key] = found
        return found

    def of_row(self, exe: str, site: str) -> str:
        return self.category("site", site) if site else self.category("app", exe)


def _names(ctx, cache: _Cache) -> dict:
    """{exe: (name, path)} of the app catalog. Built once it has loaded, then again every NAMES_CACHE_SEC."""
    catalog = shared._catalog(ctx)
    now = time.monotonic()
    ready = catalog.loaded.is_set()
    held = cache.names
    if held is not None and held[1] == ready and (not ready or now - held[0] < NAMES_CACHE_SEC):
        return held[2]
    found = {}
    if ready:
        for app in catalog.search("all", "", limit=10 ** 6)["apps"]:
            found.setdefault(app["exe"], (app["name"], app["path"] or None))
    cache.names = (now, ready, found)
    return found


# ---------------------------------------------------------------- text

def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def _day_tip(day: date, seconds: float) -> str:
    return f"{DAY_NAMES[day.weekday()]} {day.day} {MONTHS[day.month - 1]}  {stats.hm(seconds)}"


def _minutes(seconds: float) -> float:
    return round(seconds / 60, 1)


def _unlocks(req) -> dict:
    """{day: [unlock, ...]} of the emergency unlocks of the last 30 days, read once per reply."""
    return req.memo("unlocks", lambda: emergency.unlocks_by_day(req.db, req.today - timedelta(days=29)))


# ---------------------------------------------------------------- Overview

def _sessions_and_focus(days: list, digests: list) -> tuple:
    """(sessions [[start, end] in minutes from the first day], longest focus (minutes, exe, start minute) or None), as
    stats.sessions / stats.longest_focus give on the minute rows of the whole range."""
    base = days[0].toordinal()
    sessions: list = []
    best = None
    carry = None                    # the run that went on into this day: (exe, minutes, start, ordinal of its last day)
    for day, g in zip(days, digests):
        ordinal = day.toordinal()
        off = (ordinal - base) * DAY_MIN
        for s, e in g.sessions:
            if sessions and s + off - sessions[-1][1] <= stats.SESSION_GAP_MIN:
                sessions[-1][1] = e + off
            else:
                sessions.append([s + off, e + off])
        if carry is not None and carry[3] == ordinal - 1 and g.lead is not None and g.lead[0] == carry[0]:
            minutes = carry[1] + g.lead[1]
            if best is None or minutes > best[0]:
                best = (minutes, carry[0], carry[2])
            carry = (carry[0], minutes, carry[2], ordinal) if g.lead[1] == DAY_MIN else (
                (g.tail[0], g.tail[1], off + g.tail[2], ordinal) if g.tail else None)
        else:
            carry = (g.tail[0], g.tail[1], off + g.tail[2], ordinal) if g.tail else None
        if g.best is not None and (best is None or g.best[0] > best[0]):
            best = (g.best[0], g.best[1], off + g.best[2])
    return sessions, best


def _trend_tip(day: date, today: date, totals: dict, first_day: date | None, unlocks: list) -> str:
    """The hover text of one day, four lines or more: the date; its screen time; the average of its week so far (Monday to
    that day - the days before Lockdown recorded anything don't count, a recorded day with nothing counts as 0, and a week
    that has no recorded day yet has no such line); one line per emergency unlock that started that day."""
    secs = totals.get(day, 0)
    lines = [f"{DAY_NAMES[day.weekday()][:3]}, {day.day} {MONTHS[day.month - 1]}",
             f"Screen time {stats.hm(secs)}" + (" so far" if day == today else "")]
    start = day - timedelta(days=day.weekday())
    if first_day is not None and first_day > start:
        start = first_day
    n = (day - start).days + 1
    if n > 0:
        lines.append(f"Week average {stats.hm(sum(totals.get(start + timedelta(days=i), 0) for i in range(n)) / n)}")
    lines += [emergency.unlock_line(u) for u in unlocks]
    return "\n".join(lines)


def _trend(req) -> dict:
    """The trend line: 7 days for the 7-day range, else 30; the dashed 7-day average, the goal, unlock rings, and the
    "vs last week" words (down = green = improving). Every point carries its date and weekday (0 = Monday) so the chart
    can draw the day and week lines, and the tooltip text (`tip`, lines joined with a newline)."""
    span = 7 if req.range == "7 days" else 30
    today = req.today
    totals = req.memo("totals", lambda: _daily_totals(req, today - timedelta(days=29 + WEEK_LEAD_DAYS), today))
    days = [today - timedelta(days=span - 1 - i) for i in range(span)]
    values = [totals.get(d, 0) for d in days]
    unlocks = _unlocks(req)
    first = stats.first_activity(req.db)
    first_day = first.date() if first else None
    points = []
    for i, d in enumerate(days):
        window = values[max(0, i - 6):i + 1]
        day_unlocks = unlocks.get(d, [])
        points.append({"label": str(d.day), "date": d.isoformat(), "weekday": d.weekday(), "value": _minutes(values[i]),
                       "tip": _trend_tip(d, today, totals, first_day, day_unlocks),
                       "avg": _minutes(sum(window) / len(window)), "unlocks": len(day_unlocks)})
    last7 = sum(totals.get(today - timedelta(days=i), 0) for i in range(7)) / 7
    prev7 = sum(totals.get(today - timedelta(days=i), 0) for i in range(7, 14)) / 7
    avg_text = f"avg {stats.hm(last7)} a day"
    if not prev7:
        note, tone = avg_text, "muted"
    else:
        pct = round((last7 - prev7) / prev7 * 100)
        if pct == 0:
            note, tone = f"about the same as last week · {avg_text}", "muted"
        else:
            note = f"{'▼' if pct < 0 else '▲'} {abs(pct)}% vs last week · {avg_text}"
            tone = "ok" if pct < 0 else "bad"
    goal = req.goal
    return {"title": f"Trend - last {span} days", "note": note, "tone": tone, "points": points,
            "goal": _minutes(goal) if goal else 0, "goal_label": f"{goal / 3600:g} h" if goal else ""}


def _heat(req, digests: dict) -> dict:
    week = [req.today - timedelta(days=6 - i) for i in range(7)]
    return {"rows": [{"label": DAY_NAMES[d.weekday()][:3], "minutes": [round(h / 60, 2) for h in digests[d].hours]}
                     for d in week]}


def _timeline(req) -> dict | None:
    """The day as coloured segments - for Today and Yesterday only (the other ranges have the bars and the heatmap)."""
    if len(req.days) != 1:
        return None
    day = req.days[0]
    rows = stats.activity(req.db, day, day + timedelta(days=1))
    cats = req.cats
    segments = stats.timeline(rows, day, cats.of_row)
    if len(segments) > MAX_SEGMENTS:
        segments = sorted(sorted(segments, key=lambda g: -g[1])[:MAX_SEGMENTS])
    used = {kind for _, _, kind in segments}
    legend = [{"key": c["key"], "name": c["name"], "color": cats.colors[c["key"]]} for c in cats.list
              if c["key"] in used or c["builtin"]] + [{"key": "idle", "name": "Idle", "color": "var(--cat-idle)"}]
    return {"segments": [{"start": s, "len": n, "kind": k} for s, n, k in segments],
            "start_hour": min(6, int(rows[0]["minute"][11:13])) if rows else 6,
            "colors": dict(cats.colors), "names": {**cats.names, "idle": "Idle"}, "legend": legend,
            "note": f"{DAY_NAMES[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"}


def _donut(req, digests: list, active: int) -> dict:
    cats = req.cats
    split = Counter()
    for g in digests:
        for (exe, site), a in g.pairs.items():
            if a:
                split[cats.of_row(exe, site)] += a
    shown = [c for c in cats.list if split[c["key"]] or c["builtin"]]
    parts = [{"key": c["key"], "name": c["name"], "color": cats.colors[c["key"]], "value": split[c["key"]],
              "time": stats.hm(split[c["key"]]), "tip": f"{c['name']}  {stats.hm(split[c['key']])}"} for c in shown]
    return {"parts": parts, "center": stats.hm(active).replace(" h ", "h").replace(" m", "") if active else "0",
            "caption": "total"}


def _streak_runs(req, goal, start: date) -> tuple:
    """(days in a row within the goal, days in a row without an emergency unlock) for the days BEFORE today, looking back as
    far as `start`. These can't change during the day, so they are kept (key: the day, the goal, when recording started
    and what the emergency unlocks look like) - unless yesterday ended only minutes ago and may still get its last batch."""
    db, today = req.db, req.today
    unlocks = tuple(db.conn.execute("SELECT COUNT(*), MAX(started) FROM emergency_unlocks").fetchone())
    key = (today, goal, start, unlocks)
    held = req.cache.streaks.get(key)
    if held is not None:
        return held
    run_goal, end = 0, today
    # (the run of days within the goal usually stops soon: a week is looked at first, then further back)
    for size in (7, 60, 400) if goal else ():
        lo = max(start, end - timedelta(days=size))
        active = stats.daily_active(db, lo, end)
        days = [end - timedelta(days=i) for i in range(1, (end - lo).days + 1)]
        over = next((n for n, day in enumerate(days) if active.get(day.isoformat(), 0) > goal), None)
        if over is not None:
            run_goal += over
            break
        run_goal += len(days)
        end = lo
    unlocked = {u["started"].date() for u in db.unlocks_since(datetime.combine(start, datetime.min.time()))}
    run_unlock = 0
    for day in (today - timedelta(days=i) for i in range(1, (today - start).days + 1)):
        if day in unlocked:
            break
        run_unlock += 1
    runs = (run_goal, run_unlock)
    if today - timedelta(days=1) < req.settled:
        if len(req.cache.streaks) > 20:
            req.cache.streaks.clear()
        req.cache.streaks[key] = runs
    return runs


def _streaks(req, today_active: int) -> list:
    """The goal streak (days in a row within the daily goal) and the days without an emergency unlock: what stats.streaks
    gives, with the days before today kept for the day and only today worked out now - today ends a streak when it is over
    the goal / has an unlock, else it adds one day to the run before it."""
    db, today, goal = req.db, req.today, req.goal
    first = stats.first_activity(db)
    start = max(first.date(), today - timedelta(days=366)) if first else None
    if start is None or start > today:
        streak = {"goal": 0, "no_unlock": 0}
    else:
        run_goal, run_unlock = _streak_runs(req, goal, start)
        unlocked_today = db.conn.execute("SELECT 1 FROM emergency_unlocks WHERE started >= ? AND started < ? LIMIT 1",
                                         (f"{today} 00:00:00", f"{today + timedelta(days=1)} 00:00:00")).fetchone()
        over = today_active > goal if goal else True
        streak = {"goal": 0 if over else run_goal + 1, "no_unlock": 0 if unlocked_today else run_unlock + 1}
        if unlocked_today:
            streak["times"] = [emergency.unlock_span(u["started"], u["until"]) for u in _unlocks(req).get(today, [])]
    out = []
    if goal:
        n = streak["goal"]
        out.append({"key": "goal", "icon": "flame", "text": f"{_plural(n, 'day')} within your goal" if n else "Over your goal today",
                    "sub": f"daily goal {goal / 3600:g} h"})
    n = streak["no_unlock"]
    out.append({"key": "no_unlock", "icon": "lifebuoy",
                "text": f"{_plural(n, 'day')} without an emergency unlock" if n else "Emergency unlock used today",
                "sub": ", ".join(streak.get("times", ()))})
    return out


def _overview(req) -> dict:
    week = [req.today - timedelta(days=6 - i) for i in range(7)]
    digests = req.digests(list(set(req.days) | set(week)))
    in_range = [digests[d] for d in req.days]
    active = sum(g.active for g in in_range)
    total = sum(g.seconds for g in in_range)
    sessions, focus = _sessions_and_focus(req.days, in_range)
    if focus:
        minutes, exe, start = focus
        start %= DAY_MIN
        focus_card = {"value": stats.hm(minutes * 60), "sub": f"{req.app_name(exe)}, {start // 60:02d}:{start % 60:02d}"}
    else:
        focus_card = {"value": "-", "sub": ""}
    avg = sum(e - s for s, e in sessions) * 60 / len(sessions) if sessions else 0
    cards = [{"key": "active", "label": "Active", "value": stats.hm(active), "sub": f"of {stats.hm(total)} at the PC"},
             {"key": "idle", "label": "Idle", "value": stats.hm(total - active), "sub": "screen on, no input"},
             {"key": "focus", "label": "Longest focus", **focus_card},
             {"key": "sessions", "label": "Sessions", "value": str(len(sessions)),
              "sub": f"avg {stats.hm(avg)} each" if sessions else ""}]
    return {"cards": cards, "trend": _trend(req), "timeline": _timeline(req), "heat": _heat(req, digests),
            "donut": _donut(req, in_range, active),
            "streaks": _streaks(req, digests[req.today].active)}


# ---------------------------------------------------------------- Apps and Websites

def _switch_counts(db, start: date, end: date) -> tuple:
    """(switches per app, visits per site) for [start, end): the logged events, plus the daily counts of days retention
    has rolled up - in one grouped query."""
    apps, sites = Counter(), Counter()
    for exe, site, n in db.conn.execute(
            "SELECT exe, site, SUM(n) FROM ("
            " SELECT COALESCE(exe, '') AS exe, COALESCE(site, '') AS site, COUNT(*) AS n FROM switch_events"
            "  WHERE timestamp >= ? AND timestamp < ? GROUP BY 1, 2"
            " UNION ALL SELECT exe, site, count FROM daily_switches WHERE day >= ? AND day < ?"
            ") GROUP BY exe, site",
            (f"{start} 00:00:00", f"{end} 00:00:00", start.isoformat(), end.isoformat())):
        apps[exe] += n
        if site:
            sites[site] += n
    return apps, sites


def _list(req, kind: str) -> dict:
    """The table: the top MAX_ROWS apps (or sites) with at least MIN_ROW_SEC of active time in the range."""
    digests = req.digests(req.days)
    times = Counter()
    for day in req.days:
        for (exe, site), a in digests[day].pairs.items():
            if kind == "app":
                times[exe] += a
            elif site:
                times[site] += a
    ranked = [(n, s) for n, s in times.most_common(MAX_ROWS) if s >= MIN_ROW_SEC]
    cats = req.cats
    apps, sites = _switch_counts(req.db, req.start, req.end) if ranked else (Counter(), Counter())
    counts = apps if kind == "app" else sites
    top = ranked[0][1] if ranked else 1
    rows = []
    for name, sec in ranked:
        cat = cats.category(kind, name)
        if kind == "app":
            display, path = req.app_info(name)
        else:
            display, path = name, None
        rows.append({"key": name, "name": display, "sub": name if kind == "app" else "", "path": path,
                     "sec": sec, "time": stats.hm(sec), "share": round(sec / top, 4), "count": counts.get(name, 0),
                     "cat": cat, "cat_name": cats.names[cat], "color": cats.colors[cat]})
    return {"kind": kind, "rows": rows}


def _apps(req) -> dict:
    return _list(req, "app")


def _websites(req) -> dict:
    return _list(req, "site")


def _calendar(req) -> dict:
    """The Calendar has no range: it reads nothing here (the page asks `screentime_month` / `screentime_day`)."""
    return {}


# ---------------------------------------------------------------- Switches

SWITCH_SQL = (
    "WITH ev AS (SELECT timestamp AS ts, COALESCE(exe, '') AS exe, COALESCE(site, '') AS site,"
    " strftime('%s', LEAD(timestamp) OVER (ORDER BY timestamp)) - strftime('%s', timestamp) AS dur"
    " FROM switch_events WHERE timestamp >= ? AND timestamp < ?) "
    "SELECT CAST(substr(ts, 12, 2) AS INTEGER), exe, site, dur IS NULL, COUNT(*), COALESCE(SUM(dur), 0), "
    "COALESCE(SUM(dur < ?), 0), MIN(ts), MIN(CASE WHEN dur < ? THEN ts END) FROM ev GROUP BY 1, 2, 3, 4")
VISIT_STYLES = {"checking": ("Short visits - often just checking", "warn"),
                "focused": ("Long, focused stretches", "ok"),
                "mixed": ("Mixed use", "muted")}


def _switch_stats(db, start: date, end: date, now: datetime) -> dict:
    """What stats.switch_summary(stats.switches(...), now) gives for [start, end), worked out by ONE grouped query (hour,
    app, site): the visit lengths (until the next switch) are summed in SQL, so a month of switches is a few hundred
    group rows, not thousands of events. The last visit runs until now, at most 30 minutes (done here)."""
    hours, hour_first = [0] * 24, {}
    targets: dict = {}              # (kind, name) -> [visits, seconds, short visits, first seen, first short]
    for hour, exe, site, is_last, n, seconds, short, first, first_short in db.conn.execute(
            SWITCH_SQL, (f"{start} 00:00:00", f"{end} 00:00:00", stats.SHORT_VISIT_SEC, stats.SHORT_VISIT_SEC)):
        if is_last:                 # (the one visit with no next switch)
            ts = stats._ts(first)
            seconds = max(0.0, (min(now, ts + timedelta(minutes=30)) - ts).total_seconds())
            short = int(seconds < stats.SHORT_VISIT_SEC)
            first_short = first if short else None
        hours[hour] += n
        if hour not in hour_first or first < hour_first[hour]:
            hour_first[hour] = first
        t = targets.setdefault(("site", site) if site else ("app", exe), [0, 0, 0, first, None])
        t[0] += n
        t[1] += seconds
        t[2] += short
        t[3] = min(t[3], first)
        if first_short is not None and (t[4] is None or first_short < t[4]):
            t[4] = first_short
    count = sum(t[0] for t in targets.values())
    ranked = sorted(targets.items(), key=lambda kv: (-kv[1][0], kv[1][3]))
    shorts = sorted((kv for kv in targets.items() if kv[1][2]), key=lambda kv: (-kv[1][2], kv[1][4]))
    return {"count": count, "hours": hours, "hour_first": hour_first,
            "short": sum(t[2] for t in targets.values()), "short_top": [k for k, _ in shorts[:2]],
            "targets": [(k, t[0], t[1] / t[0]) for k, t in ranked],
            "avg_visit": sum(t[1] for t in targets.values()) / count if count else 0}


def _switches(req) -> dict:
    """The Switches tab: three cards, switches per hour (+ the peak sentence), and the 5 most switched-to."""
    sw = _switch_stats(req.db, req.start, req.end, req.now)
    n = sw["count"]
    label = "Switches today" if req.range == "Today" else "Switches yesterday" if req.range == "Yesterday" else f"Switches ({req.range})"
    sub, tone = "", ""
    if req.range == "Today":
        usual = stats.average_daily_switches(req.db, req.today, until=req.now.time())
        if usual is not None:
            if abs(n - usual) < 1:
                sub = "about usual for this time of day"
            else:
                sub = f"{abs(round(n - usual))} {'more' if n > usual else 'fewer'} than usual by this time"
                tone = "bad" if n > usual else "ok"
    top = [req.app_name(name) if kind == "app" else name for kind, name in sw["short_top"]]
    cards = [{"key": "switches", "label": label, "value": str(n), "sub": sub, "tone": tone},
             {"key": "visit", "label": "Average time per visit", "value": stats.ms(sw["avg_visit"]) if n else "-", "sub": "", "tone": ""},
             {"key": "short", "label": f"Short visits (< {stats.SHORT_VISIT_SEC} s)", "value": str(sw["short"]),
              "sub": f"mostly {' and '.join(top)}" if top else "", "tone": ""}]
    hours = sw["hours"]
    peak = ""
    if n:
        best = max(hours)           # (a tie goes to the hour that had its first switch earliest, as Counter.most_common does)
        hour = min((h for h in range(24) if hours[h] == best), key=lambda h: sw["hour_first"][h])
        peak = f"Peak between {hour:02d}:00 and {hour + 1:02d}:00 - {best} switch{'es' * (best != 1)}."
    rows = []
    for (kind, name), count, avg in sw["targets"][:5]:
        text, tone = VISIT_STYLES[stats.visit_style(count, avg)]
        display, path = req.app_info(name) if kind == "app" else (name, None)
        rows.append({"key": f"{kind}:{name}", "kind": kind, "name": name, "display": display, "path": path,
                     "style": text, "tone": tone, "count": count, "avg": f"avg {stats.ms(avg)}"})
    return {"cards": cards, "hours": {"counts": hours}, "peak": peak, "targets": rows}


# tab id -> the function that works out that tab's data from a _Req.
BUILDERS = {"overview": _overview, "apps": _apps, "websites": _websites, "switches": _switches, "calendar": _calendar}


# ---------------------------------------------------------------- Calendar: a month, a day

LEVEL_HOURS = (1, 2, 4, 6)          # a day's colour level: below 1 h = 0, then 1 / 2 / 4 / 6 h and more = 1 ... 4
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]
DAY_TOP = 10                        # the day's details list this many apps


def _month_of(year, month, today: date) -> date:
    """The 1st of the month asked for; no year and no month = this month (the page's first ask, before it knows the date)."""
    if year is None and month is None:
        return today.replace(day=1)
    for v in (year, month):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ApiError("Bad request: pick a month.")
    if not (2000 <= year <= 2200 and 1 <= month <= 12):
        raise ApiError("Bad request: pick a month.")
    return date(year, month, 1)


@core.read
def screentime_month(ctx, year=None, month=None):
    """One month for the Calendar (no year and month = this month): {year, month, title, offset (blank cells before the 1st, Monday first), can_next, today,
    goal (minutes, 0 = off), days: [{date, day, sec, text, level, future, over, tip}], stats: [{key, label, value, sub}]}.
    Every number is a per-day total (`_daily_totals`: finished days are kept, the rest is one grouped query), never the
    minute rows of the month. The page asks again for another month only (the day details are `screentime_day`)."""
    req = _Req(ctx, "Today")
    today, goal = req.today, req.goal
    first = _month_of(year, month, today)
    nxt = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    per_day = _daily_totals(req, first, min(nxt - timedelta(days=1), today)) if first <= today else {}      # (a future day is 0)
    days = []
    for i in range((nxt - first).days):
        d = first + timedelta(days=i)
        sec = per_day.get(d, 0)
        future = d > today
        over = bool(goal and sec > goal and not future)
        days.append({"date": d.isoformat(), "day": d.day, "sec": sec, "future": future, "over": over,
                     "text": stats.hm(sec) if sec >= 60 and not future else "",
                     "level": 0 if future else sum(sec / 3600 >= t for t in LEVEL_HOURS),
                     "tip": "" if future else _day_tip(d, sec) + ("  (over your goal)" if over else "")})
    total = sum(d["sec"] for d in days)
    past = max(1, min((today - first).days + 1, len(days)))
    cards = [{"key": "month", "label": "This month", "value": stats.hm(total), "sub": ""},
             {"key": "average", "label": "Average day", "value": stats.hm(total / past), "sub": f"over {_plural(past, 'day')}"}]
    if goal:
        within = sum(1 for d in days[:past] if d["sec"] <= goal)
        cards.append({"key": "within", "label": "Within goal", "value": f"{within} / {past}", "sub": f"goal {stats.hm(goal)} a day"})
    else:
        cards.append({"key": "within", "label": "Within goal", "value": "-", "sub": "no daily goal set (Settings)"})
    recorded = {d.isoformat() for d in per_day}         # (a day with only idle time counts: it is "0 m", as in the old window)
    busy = max((d for d in days if not d["future"] and d["date"] in recorded), key=lambda d: d["sec"], default=None)
    cards.append({"key": "busiest", "label": "Busiest day", "value": stats.hm(busy["sec"]) if busy else "-",
                  "sub": _day_tip(date.fromisoformat(busy["date"]), 0).split("  ")[0] if busy else ""})
    return {"year": first.year, "month": first.month, "title": f"{MONTH_NAMES[first.month - 1]} {first.year}",
            "offset": first.weekday(), "can_next": nxt <= today, "today": today.isoformat(),
            "goal": _minutes(goal) if goal else 0, "goal_label": f"{goal / 3600:g} h" if goal else "",
            "days": days, "stats": cards}


@core.read
def screentime_day(ctx, day):
    """The details of one day: {date, title, note ("X active" / "nothing recorded"), rows: [{key, name, path, sec, time}]} -
    the top 10 apps with at least a minute, from one grouped query on that day (a day retention rolled up has its totals too)."""
    try:
        d = date.fromisoformat(day)
    except (TypeError, ValueError):
        raise ApiError("Bad request: pick a day.") from None
    req = _Req(ctx, "Today")
    apps = stats.app_totals(req.db, d, d + timedelta(days=1))
    active = sum(apps.values())
    rows = []
    for exe, sec in apps.most_common(DAY_TOP):
        if sec >= MIN_ROW_SEC:
            name, path = req.app_info(exe)
            rows.append({"key": exe, "name": name, "path": path, "sec": sec, "time": stats.hm(sec)})
    return {"date": d.isoformat(), "title": f"{DAY_NAMES[d.weekday()]} {d.day:02d} {MONTHS[d.month - 1]} {d.year}",
            "note": f"{stats.hm(active)} active" if active else "nothing recorded", "rows": rows}


# ---------------------------------------------------------------- the Api

def _tab_of(tab) -> str:
    if not isinstance(tab, str) or tab.lower() not in TABS:
        raise ApiError("Bad request: no such Screen Time tab.")
    return tab.lower()


def _range_of(name) -> str:
    if not isinstance(name, str) or name not in stats.RANGES:
        raise ApiError("Bad request: pick Today, Yesterday, 7 days or 30 days.")
    return name


def _defaults(db) -> dict:
    """Where Screen Time opens, from the Display settings: {tab, range} (tab: overview / apps / websites / switches)."""
    tab = (db.get_setting(ST_TAB_KEY, "Overview") or "").lower()
    rng = db.get_setting(ST_RANGE_KEY, "Today")
    return {"tab": tab if tab in TABS else TABS[0], "range": rng if rng in stats.RANGES else "Today"}


@core.read
def screentime_get(ctx, tab=None, range=None):
    """One tab's data for a range, and nothing else: {tab, range, now, empty, ...the tab's own parts}. `empty` is true when
    nothing has ever been recorded. `tab`: overview / apps / websites / switches / calendar; `range`: Today / Yesterday /
    7 days / 30 days (the Calendar ignores it). Leave `tab` or `range` out (null) and the Display settings' one is used -
    that is how the page asks on its first visit after the window opens: the reply then also carries `defaults`
    ({tab, range}), and from then on the page keeps the last tab and range itself."""
    out = {}
    if tab is None or range is None:
        out["defaults"] = defaults = _defaults(ctx.db)
        tab = defaults["tab"] if tab is None else tab
        range = defaults["range"] if range is None else range
    tab, range = _tab_of(tab), _range_of(range)
    builder = BUILDERS.get(tab)
    if builder is None:
        raise ApiError("That tab isn't available yet.")
    req = _Req(ctx, range)
    out.update(tab=tab, range=range, now=req.now.isoformat(), empty=stats.first_activity(req.db) is None)
    if not out["empty"]:
        out.update(builder(req))
    return out


# ---------------------------------------------------------------- warming up

@core.internal
def warm(ctx, catalog_wait: float = 0.0):
    """(a background thread) Work out what the first visit of Screen Time would otherwise work out while the owner waits: the
    digests of the last 30 days, their daily totals, the streaks, and - once the app catalog has loaded (`catalog_wait` =
    how many seconds to wait for it) - the app names. Returns nothing, it only fills what `_Cache` keeps; with nothing
    recorded yet there is nothing to do. Cheap when it is warm already."""
    req = _Req(ctx, "30 days")
    if stats.first_activity(req.db) is None:
        return
    digests = req.digests(req.days)
    _daily_totals(req, req.today - timedelta(days=29 + WEEK_LEAD_DAYS), req.today)
    _streaks(req, digests[req.today].active)
    catalog = shared._catalog(ctx)
    if catalog_wait and not catalog.loaded.is_set():
        catalog.loaded.wait(catalog_wait)
    if catalog.loaded.is_set():
        req.names()


def _seconds_to_next_day(now: datetime) -> float:
    """Seconds from `now` until the day that is running has ended AND settled: from then on there is one more finished day
    to keep (and the streaks start again from the new day)."""
    nxt = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()) + timedelta(seconds=SETTLE_SEC + 2)
    return max(1.0, (nxt - now).total_seconds())


@core.internal
def start_warmup(ctx, delay: float = WARM_DELAY_SEC):
    """(the agent, once it is up) A daemon thread of its own that runs `warm` `delay` seconds from now, so the window and the
    first page come first, and again each time a new day has settled - so a window opened after midnight finds the new day
    ready too. A failure is logged and changes nothing (the page then does the work itself); it is tried again in ten
    minutes. `thread.stop_event.set()` ends it (tests)."""
    stop = threading.Event()

    def work():
        if stop.wait(delay):
            return
        wait_for_catalog = WARM_CATALOG_WAIT_SEC
        while True:
            try:
                warm(ctx, wait_for_catalog)
                wait_for_catalog = 0.0
                seconds = _seconds_to_next_day(ctx.now())
            except Exception:
                log.exception("Screen Time warm-up failed")
                seconds = WARM_RETRY_SEC
            if stop.wait(seconds):
                return
    thread = threading.Thread(target=work, name="lockdown-st-warm", daemon=True)
    thread.stop_event = stop
    thread.start()
    return thread


# ---------------------------------------------------------------- Export CSV (also used by Settings)

def _ask_save_path(window, name: str) -> str | None:
    """The file the owner picked in the native Save dialog, or None when cancelled (thin glue: replaced in the tests)."""
    if window is None:
        raise ApiError("The Save dialog isn't available.")
    import webview
    kind = webview.FileDialog.SAVE if hasattr(webview, "FileDialog") else webview.SAVE_DIALOG
    result = window.create_file_dialog(kind, save_filename=name, file_types=("CSV (*.csv)",))
    if not result:
        return None
    return result if isinstance(result, str) else result[0]


@core.ui
def screentime_export_csv(ctx):
    """"Export CSV": ask where to save (the owner's own dialog - the page never names a path), then write the screen time
    of the last year (day, app, site, minutes, active minutes, category) on a worker thread with its own connection.
    -> {cancelled: true} or {path, rows}. Settings > Backup & export calls this same method."""
    path = _ask_save_path(getattr(ctx, "main_window", None), "screen-time.csv")
    if not path:
        return {"cancelled": True}
    if not path.lower().endswith(".csv"):
        path += ".csv"
    result: dict = {}

    def work():
        from db import Database
        import backup
        try:
            db = Database(ui=True) if ctx._path is None else Database(ctx._path, ui=True)
            try:
                result["rows"] = backup.screen_time_csv(db, path)
            finally:
                db.close()
        except Exception as e:          # (reported to the owner below)
            result["error"] = e
    worker = threading.Thread(target=work, name="lockdown-st-csv", daemon=True)
    worker.start()
    worker.join()
    if "error" in result:
        raise ApiError(f"Couldn't save the file: {result['error']}")
    return {"path": path, "rows": result["rows"]}
