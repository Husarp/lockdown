"""Screen-time numbers for the Dashboard and Screen Time pages, computed from the activity and switch logs.

Definitions:
- active time: seconds in front with keyboard/mouse input in the last 5 minutes (idle = in front, no input)
- session: time at the PC without a break of 5+ minutes
- longest focus: the longest stretch in one app without switching away
- visit: from switching to an app/site until switching away; short visit = under 30 s
"""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from rules import TIME_FMT

SESSION_GAP_MIN = 5
SHORT_VISIT_SEC = 30
DEFAULT_VISIT_SEC = 5 * 60      # "time saved" per blocked visit when there's no history for it
RANGES = {"Today": (0, 1), "Yesterday": (1, 1), "7 days": (0, 7), "30 days": (0, 30)}   # (days back, length)


def range_dates(name: str, today: date) -> tuple[date, date]:
    """[start, end) dates for a range name."""
    back, length = RANGES[name]
    end = today - timedelta(days=back) + timedelta(days=1)
    return end - timedelta(days=length), end


# ---------- loading ----------

def activity(db, start: date, end: date) -> list[dict]:
    """Rows {minute, exe, site, seconds, active} for [start, end)."""
    rows = db.conn.execute("SELECT minute, exe, site, seconds, active_seconds FROM activity "
                           "WHERE minute >= ? AND minute < ? ORDER BY minute", (f"{start} 00:00", f"{end} 00:00"))
    return [{"minute": r[0], "exe": r[1], "site": r[2], "seconds": r[3], "active": r[4]} for r in rows]


def switches(db, start: date, end: date) -> list[dict]:
    """Switch events {ts, exe, site} for [start, end), oldest first."""
    rows = db.conn.execute("SELECT timestamp, exe, site FROM switch_events WHERE timestamp >= ? AND timestamp < ? "
                           "ORDER BY timestamp", (f"{start} 00:00:00", f"{end} 00:00:00"))
    return [{"ts": datetime.strptime(r[0], TIME_FMT), "exe": r[1], "site": r[2] or ""} for r in rows]


def first_activity(db) -> datetime | None:
    row = db.conn.execute("SELECT MIN(minute) FROM activity").fetchone()
    return datetime.strptime(row[0], "%Y-%m-%d %H:%M") if row and row[0] else None


# ---------- categories ----------

def category_of(kind: str, name: str, saved: dict, items: list[dict]) -> str:
    """Saved choice; else blocked things are distracting, everything else neutral."""
    if (kind, name) in saved:
        return saved[(kind, name)]
    for item in items:
        if kind == "app" and item["item_type"] == "app" and item["target"].lower() == name:
            return "distracting"
        if kind == "site" and item["item_type"] == "site" and any(
                name == h or name.endswith("." + h) for h in item["target"].split()):
            return "distracting"
    return "neutral"


# ---------- totals ----------

def totals(rows: list[dict]) -> tuple[int, int]:
    """(active seconds, seconds at the PC)."""
    return sum(r["active"] for r in rows), sum(r["seconds"] for r in rows)


def per_app(rows: list[dict]) -> Counter:
    out = Counter()
    for r in rows:
        out[r["exe"]] += r["active"]
    return out


def per_site(rows: list[dict]) -> Counter:
    out = Counter()
    for r in rows:
        if r["site"]:
            out[r["site"]] += r["active"]
    return out


def per_day(rows: list[dict]) -> Counter:
    out = Counter()
    for r in rows:
        out[r["minute"][:10]] += r["active"]
    return out


def _minutes(rows: list[dict]) -> dict[datetime, dict]:
    """Per minute: total seconds, active seconds and the app/site with the most active time."""
    by_minute: dict[str, dict] = {}
    for r in rows:
        m = by_minute.setdefault(r["minute"], {"seconds": 0, "active": 0, "top": None, "top_active": -1})
        m["seconds"] += r["seconds"]
        m["active"] += r["active"]
        if r["active"] > m["top_active"]:
            m["top"], m["top_active"] = (r["exe"], r["site"]), r["active"]
    return {datetime.strptime(k, "%Y-%m-%d %H:%M"): v for k, v in by_minute.items()}


def sessions(rows: list[dict]) -> list[tuple[datetime, datetime]]:
    """(start, end) of each stretch of active minutes without a 5+ minute break."""
    out = []
    for minute in sorted(m for m, v in _minutes(rows).items() if v["active"] > 0):
        if out and (minute - out[-1][1]).total_seconds() <= SESSION_GAP_MIN * 60:
            out[-1] = (out[-1][0], minute + timedelta(minutes=1))
        else:
            out.append((minute, minute + timedelta(minutes=1)))
    return out


def longest_focus(rows: list[dict]) -> tuple[int, str, datetime] | None:
    """(seconds, exe, start) of the longest run of consecutive active minutes in the same app."""
    best, run = None, None
    for minute, v in sorted(_minutes(rows).items()):
        exe = v["top"][0] if v["active"] > 0 else None
        if run and exe == run[1] and minute - run[3] == timedelta(minutes=1):
            run = (run[0] + 60, exe, run[2], minute)
        else:
            run = (60, exe, minute, minute) if exe else None
        if run and (best is None or run[0] > best[0]):
            best = run
    return (best[0], best[1], best[2]) if best else None


def timeline(rows: list[dict], day: date, category) -> list[tuple[int, int, str]]:
    """Segments (start minute of day, length in minutes, kind) for one day; kind is a category or "idle".
    category(exe, site) -> category. Minutes without any record are left out (PC off / locked)."""
    segs: list[list] = []
    for minute, v in sorted(_minutes(rows).items()):
        if minute.date() != day:
            continue
        kind = category(*v["top"]) if v["active"] > 0 else "idle"
        start = minute.hour * 60 + minute.minute
        if segs and segs[-1][2] == kind and segs[-1][0] + segs[-1][1] == start:
            segs[-1][1] += 1
        else:
            segs.append([start, 1, kind])
    return [tuple(s) for s in segs]


def hourly_minutes(rows: list[dict], days: list[date]) -> list[list[float]]:
    """Active minutes per (day, hour)."""
    active = defaultdict(int)
    for r in rows:
        active[(r["minute"][:10], int(r["minute"][11:13]))] += r["active"]
    return [[active[(d.isoformat(), h)] / 60 for h in range(24)] for d in days]


def heatmap(rows: list[dict], days: list[date]) -> list[list[int]]:
    """Level 0-4 of active minutes per (day, hour)."""
    return [[0 if m < 1 else 1 if m < 15 else 2 if m < 30 else 3 if m < 45 else 4 for m in row]
            for row in hourly_minutes(rows, days)]


# ---------- switches / visits ----------

def target_of(event: dict) -> tuple[str, str]:
    """(kind, name) a switch went to: the site in a browser tab, else the app."""
    return ("site", event["site"]) if event["site"] else ("app", event["exe"])


def visits(events: list[dict], now: datetime) -> list[tuple[tuple[str, str], float]]:
    """(target, seconds) for each visit (until the next switch; the last one until now, at most 30 min)."""
    out = []
    for i, e in enumerate(events):
        end = events[i + 1]["ts"] if i + 1 < len(events) else min(now, e["ts"] + timedelta(minutes=30))
        out.append((target_of(e), max(0.0, (end - e["ts"]).total_seconds())))
    return out


def switch_summary(events: list[dict], now: datetime) -> dict:
    """{count, per_hour: Counter, short: int, short_top: [targets], targets: [(target, count, avg_sec)], avg_visit}."""
    vs = visits(events, now)
    by_target: dict[tuple, list[float]] = defaultdict(list)
    for target, sec in vs:
        by_target[target].append(sec)
    short = Counter(t for t, sec in vs if sec < SHORT_VISIT_SEC)
    ranked = sorted(by_target.items(), key=lambda kv: -len(kv[1]))
    return {"count": len(events), "per_hour": Counter(e["ts"].hour for e in events),
            "short": sum(short.values()), "short_top": [n for n, _ in short.most_common(2)],
            "targets": [(t, len(secs), sum(secs) / len(secs)) for t, secs in ranked],
            "avg_visit": sum(sec for _, sec in vs) / len(vs) if vs else 0}


def visit_style(count: int, avg_sec: float) -> str:
    """How a most-switched-to entry is described: "checking", "focused" or "mixed"."""
    if avg_sec < 60 and count >= 10:
        return "checking"
    if avg_sec >= 3 * 60:
        return "focused"
    return "mixed"


def average_daily_switches(db, today: date, days: int = 7, until: time | None = None) -> float | None:
    """Average switches per day over the previous days that have any activity - counting each day only up to
    `until` (the time now), so a morning isn't compared with whole days."""
    end = until.strftime("%H:%M:%S") if until else "24:00:00"
    counts = []
    for i in range(1, days + 1):
        d = today - timedelta(days=i)
        if db.conn.execute("SELECT 1 FROM activity WHERE minute >= ? AND minute < ? LIMIT 1",
                           (f"{d} 00:00", f"{d + timedelta(days=1)} 00:00")).fetchone():
            counts.append(db.conn.execute("SELECT COUNT(*) FROM switch_events WHERE timestamp >= ? AND timestamp < ?",
                                          (f"{d} 00:00:00", f"{d} {end}")).fetchone()[0])
    return sum(counts) / len(counts) if counts else None


# ---------- blocked visits / time saved ----------

def blocked_events(db, day: date) -> list[dict]:
    rows = db.conn.execute("SELECT item_id, display_name, hostname FROM block_events WHERE timestamp >= ? "
                           "AND timestamp < ?", (f"{day} 00:00:00", f"{day + timedelta(days=1)} 00:00:00"))
    return [{"item_id": r[0], "name": r[1], "target": r[2]} for r in rows]


def time_saved(events: list[dict], items: list[dict], history_rows: list[dict], history_events: list[dict],
               now: datetime) -> float:
    """Blocked attempts x your usual visit length on that site/app (5 min when there's no history)."""
    lengths = defaultdict(list)
    for (kind, name), sec in visits(history_events, now):
        lengths[name].append(sec)
    by_id = {i["id"]: i for i in items}
    total = 0.0
    for e in events:
        item = by_id.get(e["item_id"])
        names = item["target"].split() if item else [e["target"]]
        secs = [s for n, ls in lengths.items() for s in ls
                if any(n == h or n.endswith("." + h) for h in names)]
        total += sum(secs) / len(secs) if secs else DEFAULT_VISIT_SEC
    return total


# ---------- text ----------

def streaks(db, today: date, goal_sec: float | None, max_days: int = 366) -> dict[str, int]:
    """Days in a row (back from today, not before Lockdown started recording): "goal" - active screen time within
    the daily goal (today counts while it still is); "no_unlock" - without an emergency unlock."""
    first = first_activity(db)
    if not first:
        return {"goal": 0, "no_unlock": 0}
    start = max(first.date(), today - timedelta(days=max_days))
    days = [today - timedelta(days=i) for i in range((today - start).days + 1)]
    active = per_day(activity(db, start, today + timedelta(days=1)))
    goal = 0
    if goal_sec:
        for d in days:
            if active.get(d.isoformat(), 0) > goal_sec:
                break
            goal += 1
    unlocked = {u["started"].date() for u in db.unlocks_since(datetime.combine(start, time()))}
    no_unlock = 0
    for d in days:
        if d in unlocked:
            break
        no_unlock += 1
    return {"goal": goal, "no_unlock": no_unlock}


def hm(seconds: float) -> str:
    """'4 h 12 m' / '52 m' / '0 m'."""
    minutes = int(seconds // 60)
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d} m" if h else f"{m} m"


def ms(seconds: float) -> str:
    """'1 m 46 s' / '42 s'."""
    m, s = divmod(int(seconds), 60)
    return f"{m} m {s:02d} s" if m else f"{s} s"
