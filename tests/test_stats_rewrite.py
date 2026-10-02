"""0.84.0 rewrote the Dashboard's slow statistics (SQL aggregates, an index for time saved, one grouped query for
reminder counts). Each is checked here against the 0.83.x implementation, copied verbatim, on random data:
the results must be identical, not just close."""
import inspect
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta

import pytest

import reminders
import stats
from db import Database
from rules import TIME_FMT
from synthdata import build

TODAY = date(2026, 10, 1)


# ---------- the 0.83.3 versions ----------

def old_switches(db, start, end):
    rows = db.conn.execute("SELECT timestamp, exe, site FROM switch_events WHERE timestamp >= ? AND timestamp < ? "
                           "ORDER BY timestamp", (f"{start} 00:00:00", f"{end} 00:00:00"))
    return [{"ts": datetime.strptime(r[0], TIME_FMT), "exe": r[1], "site": r[2] or ""} for r in rows]


def old_average_daily_switches(db, today, days=7, until=None):
    end = until.strftime("%H:%M:%S") if until else "24:00:00"
    counts = []
    for i in range(1, days + 1):
        d = today - timedelta(days=i)
        if db.conn.execute("SELECT 1 FROM activity WHERE minute >= ? AND minute < ? LIMIT 1",
                           (f"{d} 00:00", f"{d + timedelta(days=1)} 00:00")).fetchone():
            counts.append(db.conn.execute("SELECT COUNT(*) FROM switch_events WHERE timestamp >= ? AND timestamp < ?",
                                          (f"{d} 00:00:00", f"{d} {end}")).fetchone()[0])
    return sum(counts) / len(counts) if counts else None


def old_time_saved(events, items, history_rows, history_events, now):
    lengths = defaultdict(list)
    for (kind, name), sec in stats.visits(history_events, now):
        lengths[name].append(sec)
    by_id = {i["id"]: i for i in items}
    total = 0.0
    for e in events:
        item = by_id.get(e["item_id"])
        names = item["target"].split() if item else [e["target"]]
        secs = [s for n, ls in lengths.items() for s in ls
                if any(n == h or n.endswith("." + h) for h in names)]
        total += sum(secs) / len(secs) if secs else stats.DEFAULT_VISIT_SEC
    return total


def old_streaks(db, today, goal_sec, max_days=366):
    row = db.conn.execute("SELECT MIN(minute) FROM activity").fetchone()
    first = datetime.strptime(row[0], "%Y-%m-%d %H:%M") if row and row[0] else None
    if not first:
        return {"goal": 0, "no_unlock": 0}
    start = max(first.date(), today - timedelta(days=max_days))
    days = [today - timedelta(days=i) for i in range((today - start).days + 1)]
    active = stats.per_day(stats.activity(db, start, today + timedelta(days=1)))
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


def old_counts(db, what, since):
    rows = db.conn.execute("SELECT result, COUNT(*) FROM reminder_log WHERE what = ? AND timestamp >= ? "
                           "GROUP BY result", (what, since.strftime(TIME_FMT)))
    return dict(rows.fetchall())


# ---------- comparisons ----------

@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    db = Database(tmp_path_factory.mktemp("synth") / "t.db")
    info = build(db.path, TODAY, days=45, seed=7)
    return db, info["now"]


def test_streaks_match(synth):
    db, now = synth
    for goal in (None, 600, 4 * 3600, 6 * 3600, 30 * 3600):
        for back in (0, 1, 5, 13, 29, 39):
            today = TODAY - timedelta(days=back)
            assert stats.streaks(db, today, goal) == old_streaks(db, today, goal), (goal, today)


def test_average_daily_switches_matches(synth):
    db, now = synth
    for back in range(0, 40, 2):
        today = TODAY - timedelta(days=back)
        for until in (None, time(0, 0, 1), time(9, 30), time(15, 42, 17), time(23, 59, 59)):
            for days in (1, 7, 14):
                assert stats.average_daily_switches(db, today, days, until) == \
                    old_average_daily_switches(db, today, days, until), (today, until, days)


def test_average_daily_switches_with_no_history(tmp_path):
    db = Database(tmp_path / "t.db")
    assert stats.average_daily_switches(db, TODAY) is None


def test_switches_parse_the_same(synth):
    db, _now = synth
    assert stats.switches(db, TODAY - timedelta(days=30), TODAY + timedelta(days=1)) == \
        old_switches(db, TODAY - timedelta(days=30), TODAY + timedelta(days=1))


def test_time_saved_matches_bit_for_bit(synth):
    db, now = synth
    items = db.list_items()
    history = stats.switches(db, TODAY - timedelta(days=30), TODAY + timedelta(days=1))
    for back in range(0, 30):
        events = stats.blocked_events(db, TODAY - timedelta(days=back))
        assert stats.time_saved(events, items, [], history, now) == old_time_saved(events, items, [], history, now)


def test_time_saved_suffix_matching_edge_cases():
    """Subdomains match, look-alikes don't, several hostnames per item, apps by exe, unknown items by hostname."""
    rnd = random.Random(3)
    names = ["youtube.com", "m.youtube.com", "notyoutube.com", "youtube.com.evil.net", "a.b.youtube.com",
             "discord.exe", "reddit.com", "old.reddit.com", "x.com", "twitter.com", "api.twitter.com", "com", ""]
    base = datetime(2026, 9, 30, 8)
    history = []
    t = base
    for _ in range(400):
        t += timedelta(seconds=rnd.randint(1, 900))
        n = rnd.choice(names)
        history.append({"ts": t, "exe": n if n.endswith(".exe") else "chrome.exe",
                        "site": "" if n.endswith(".exe") else n})
    now = t + timedelta(seconds=37.25)   # a fractional last visit: the sums must add in the same order
    items = [{"id": 1, "target": "youtube.com"}, {"id": 2, "target": "x.com twitter.com"},
             {"id": 3, "target": "discord.exe"}, {"id": 4, "target": "com"}, {"id": 5, "target": "nothing.here"}]
    events = [{"item_id": rnd.choice([1, 2, 3, 4, 5, 99, None]), "target": rnd.choice(names + ["cdn.youtube.com"])}
              for _ in range(300)]
    assert stats.time_saved(events, items, [], history, now) == old_time_saved(events, items, [], history, now)


def test_daily_totals_match_the_minute_rows(synth):
    db, _now = synth
    for start, end in ((TODAY - timedelta(days=60), TODAY + timedelta(days=1)), (TODAY, TODAY + timedelta(days=1)),
                       (date(2026, 9, 1), date(2026, 10, 1))):
        assert stats.daily_active(db, start, end) == stats.per_day(stats.activity(db, start, end))
        rows = stats.activity(db, start, end)
        old = stats.per_app(rows)
        new = stats.app_totals(db, start, end)
        assert new == old and new.most_common() == old.most_common()   # (ties broken the same way)


def test_first_activity(synth, tmp_path):
    db, _now = synth
    row = db.conn.execute("SELECT MIN(minute) FROM activity").fetchone()[0]
    assert stats.first_activity(db) == datetime.strptime(row, "%Y-%m-%d %H:%M")
    assert stats.first_activity(Database(tmp_path / "empty.db")) is None


def test_reminder_counts_in_one_query_match(synth):
    db, now = synth
    whats = ["break", "sleep", "r1", "r2", "r3", "nobody"]
    for since in (datetime.combine(TODAY, time()), datetime.combine(TODAY - timedelta(days=7), time()),
                  datetime(2026, 9, 20, 13, 7, 2)):
        many = reminders.counts_many(db, whats, since)
        for what in whats:
            assert reminders.counts(db, what, since) == old_counts(db, what, since)
            assert many.get(what, {}) == old_counts(db, what, since)
    assert reminders.counts_many(db, [], now) == {}


def test_fast_timestamp_parsing_is_the_same_as_strptime():
    for text in ("2026-10-01 00:00:00", "2026-02-28 23:59:59", "2024-02-29 12:30:05"):
        assert stats._ts(text) == datetime.strptime(text, TIME_FMT)
    for text in ("2026-10-01 00:00", "2026-12-31 23:59"):
        assert stats._minute(text) == datetime.strptime(text, "%Y-%m-%d %H:%M")
    for bad in ("2026-13-01 00:00:00", "2026-10-01T10:00:00", "garbage"):
        with pytest.raises(ValueError):
            stats._ts(bad)
    with pytest.raises(ValueError):
        stats._minute("2026-10-01T10:00")


def test_screen_time_works_out_minutes_once_and_reuses_the_ranges_rows(tmp_path):
    """Review (perf #8): sessions and longest focus each rebuilt the per-minute summary of the same rows (about 40%
    of Screen Time), and the heatmap read 7 days of minutes again although the 7 / 30-day range had them."""
    import types
    from datetime import date as _date, timedelta as _td
    from db import Database
    from gui import screen_time
    from synthdata import build
    today = _date(2026, 10, 1)
    db = Database(tmp_path / "t.db")
    build(db.path, today, days=40)
    for name in stats.RANGES:
        start, end = stats.range_dates(name, today)
        rows = stats.activity(db, start, end)
        minutes = stats.minute_summary(rows)
        assert stats.sessions(rows, minutes) == stats.sessions(rows)
        assert stats.longest_focus(rows, minutes) == stats.longest_focus(rows)
        c = types.SimpleNamespace(db=db, start=start, end=end, today=today, rows=rows)
        first = today - _td(days=6)
        assert screen_time.week_of(c, first) == stats.activity(db, first, today + _td(days=1)), name
    src = inspect.getsource(screen_time.OverviewView.update_view)
    assert src.count("stats.minute_summary(") == 1 and "stats.activity(" not in src
