"""Api for Screen Time (BLUEPRINT 3.5): the page shell's start-up answer, the Overview, Apps and Websites tabs, and the CSV
export. R15 adds Switches and Calendar by appending to BUILDERS (and `screentime_month` / `screentime_day` below it).

`screentime_get(tab, range)` returns only that tab's data, and never reads more than the tab needs: Calendar reads no
range activity at all. Every number comes from small pre-summed pieces:
- one DIGEST per day (`_Digest`: active / at-the-PC seconds, seconds per app / site, active seconds per hour, sessions
  and the longest run in one app), worked out in a single pass over that day's minute rows. A day that is over can't
  change any more, so its digest is kept in memory (`_Cache`, 15 minutes at most, then it is read again) and a range
  of 30 days is then 30 dictionary look-ups plus today's own digest - not a scan of 17 000 minute rows;
- the daily totals (`stats.daily_active`, which also counts days retention has rolled up) for the trend and the bars;
- one grouped query for the switch / visit counts of the Apps and Websites tables.
Sessions and the longest focus are put together from the digests exactly as stats.sessions / stats.longest_focus would
give them on the whole range (a session or a run in one app that goes over midnight is joined), and a test compares them.

Nothing here can loosen a block. The Display settings (the tab / range Screen Time opens with) are written by
webapi/dashboard.py `display_set`; this module only reads them.
"""
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta
from itertools import groupby
from operator import itemgetter

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
CACHE_SEC = 900                 # a finished day's digest is read again after this long, in case something wrote to it
STREAK_CACHE_SEC = 60           # the streaks cost the most to work out and change slowly
NAMES_CACHE_SEC = 600
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAY_MIN = 1440


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
    comes in on another thread at the same time may work a day out twice, which is harmless."""

    def __init__(self):
        self.days: dict = {}        # date -> (when, _Digest)
        self.totals: dict = {}      # date -> (when, active seconds)
        self.streaks: dict = {}     # key -> (when, {goal, no_unlock})
        self.names = None           # (when, catalog loaded?, {exe: (name, path)})

    def prune(self, today: date):
        old = today - timedelta(days=45)
        for table in (self.days, self.totals):
            for d in [d for d in table if d < old]:
                table.pop(d, None)


def _cache(ctx) -> _Cache:
    cache = ctx.__dict__.get("st_cache")
    if cache is None:
        cache = ctx.__dict__.setdefault("st_cache", _Cache())
    return cache


@core.internal
def forget(ctx):
    """Drop everything kept (tests; and anything that rewrites old activity)."""
    ctx.__dict__.pop("st_cache", None)


def _activity_rows(db, start: date, end: date):
    """The minute rows for [start, end) - the ONE place this module reads them (a test counts the calls)."""
    return db.conn.execute("SELECT minute, exe, site, seconds, active_seconds FROM activity "
                           "WHERE minute >= ? AND minute < ? ORDER BY minute", (f"{start} 00:00", f"{end} 00:00"))


def _digests(req, days: list) -> dict:
    """{date: _Digest} for these days (any order). Days that are over come from the cache or are worked out in one query
    for the stretch that is missing, and kept; today is always worked out fresh."""
    cache, today = req.cache, req.today
    now = time.monotonic()
    out, missing = {}, []
    for day in days:
        hit = cache.days.get(day) if day < today else None
        if hit is not None and now - hit[0] < CACHE_SEC:
            out[day] = hit[1]
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
            if day < today:
                cache.days[day] = (now, d)
        cache.prune(today)
    return out


def _daily_totals(req, first: date, last: date) -> dict:
    """{date: active seconds} for first..last (a day with nothing is 0). Finished days are kept; stats.daily_active does
    the sums in SQL and includes days retention has rolled up."""
    cache, today = req.cache, req.today
    now = time.monotonic()
    out, missing = {}, []
    day = first
    while day <= last:
        hit = cache.totals.get(day) if day < today else None
        if hit is not None and now - hit[0] < CACHE_SEC:
            out[day] = hit[1]
        else:
            missing.append(day)
        day += timedelta(days=1)
    if missing:
        lo, hi = min(missing), max(missing)
        found = stats.daily_active(req.db, lo, hi + timedelta(days=1))
        for day in missing:
            out[day] = found.get(day.isoformat(), 0)
            if day < today:
                cache.totals[day] = (now, out[day])
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


def _unlocks_per_day(db, first: date) -> Counter:
    return Counter(u["started"].date() for u in db.unlocks_since(datetime.combine(first, datetime.min.time())))


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


def _trend(req) -> dict:
    """The trend line: 7 days for the 7-day range, else 30; the dashed 7-day average, the goal, unlock rings, and the
    "vs last week" words (down = green = improving)."""
    span = 7 if req.range == "7 days" else 30
    today = req.today
    totals = req.memo("totals30", lambda: _daily_totals(req, today - timedelta(days=29), today))
    days = [today - timedelta(days=span - 1 - i) for i in range(span)]
    values = [totals.get(d, 0) for d in days]
    unlocks = req.memo("unlocks", lambda: _unlocks_per_day(req.db, today - timedelta(days=29)))
    points = []
    for i, d in enumerate(days):
        window = values[max(0, i - 6):i + 1]
        points.append({"label": str(d.day), "value": _minutes(values[i]), "tip": _day_tip(d, values[i]),
                       "avg": _minutes(sum(window) / len(window)), "unlocks": unlocks.get(d, 0)})
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


def _bars(req) -> dict:
    """The last 7 days (30 for the 30-day range) as bars: the note is the average of the days that have data."""
    n = 30 if req.range == "30 days" else 7
    today = req.today
    totals = req.memo("totals30", lambda: _daily_totals(req, today - timedelta(days=29), today))
    days = [today - timedelta(days=n - 1 - i) for i in range(n)]
    values = [totals.get(d, 0) for d in days]
    unlocks = req.memo("unlocks", lambda: _unlocks_per_day(req.db, today - timedelta(days=29)))
    bars = [{"label": DAY_NAMES[d.weekday()][:3] if n == 7 else str(d.day), "value": _minutes(v), "tip": _day_tip(d, v),
             "today": d == today, "unlocks": unlocks.get(d, 0)} for d, v in zip(days, values)]
    with_data = [v for v in values if v]
    goal = req.goal
    return {"title": f"Last {n} days", "note": f"avg {stats.hm(sum(with_data) / len(with_data))}" if with_data else "",
            "bars": bars, "goal": _minutes(goal) if goal else 0, "goal_label": f"{goal / 3600:g} h" if goal else ""}


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


def _streaks(req) -> list:
    """The goal streak (days in a row within the daily goal) and the days without an emergency unlock."""
    db, today, goal = req.db, req.today, req.goal
    unlocks = tuple(db.conn.execute("SELECT COUNT(*), MAX(started) FROM emergency_unlocks").fetchone())
    key = (today, goal, unlocks)
    now = time.monotonic()
    held = req.cache.streaks.get(key)
    if held is None or now - held[0] > STREAK_CACHE_SEC:
        if len(req.cache.streaks) > 20:
            req.cache.streaks.clear()
        held = req.cache.streaks[key] = (now, stats.streaks(db, today, goal))
    streak = held[1]
    out = []
    if goal:
        n = streak["goal"]
        out.append({"key": "goal", "icon": "flame", "text": f"{_plural(n, 'day')} within your goal" if n else "Over your goal today",
                    "sub": f"daily goal {goal / 3600:g} h"})
    n = streak["no_unlock"]
    out.append({"key": "no_unlock", "icon": "lifebuoy",
                "text": f"{_plural(n, 'day')} without an emergency unlock" if n else "Emergency unlock used today",
                "sub": ""})
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
            "bars": _bars(req), "donut": _donut(req, in_range, active), "streaks": _streaks(req)}


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
    """The Calendar has no range: it reads nothing here (R15 fills the month with screentime_month)."""
    return {}


# tab id -> the function that works out that tab's data from a _Req. R15 appends "switches" (and fills "calendar").
BUILDERS = {"overview": _overview, "apps": _apps, "websites": _websites, "calendar": _calendar}


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
