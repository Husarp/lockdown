"""Retention (0.84.0): detail older than retention.DETAIL_DAYS is rolled into daily totals - and every number the app
shows from it stays exactly the same, while nothing enforcement or Anti-Bypass needs is touched."""
import json
import sqlite3
import threading
from collections import Counter
from datetime import date, datetime, time, timedelta

import pytest

import backup
import digest
import emergency
import reminders
import retention
import stats
from db import Database
from synthdata import build

TODAY = date(2026, 10, 1)
DAYS = 60



def use_every_day(db, days=DAYS, today=TODAY):
    """A minute of screen time on each of the last `days` days (retention only rolls up once that many days of use
    have really followed - see test_a_clock_jumped_forward_keeps_recent_detail)."""
    for back in range(days):
        db.add_activity(f"{today - timedelta(days=back)} 12:00", "explorer.exe", "", 30, 30)


def _old_per_day(db, start, end):
    """What every per-day chart did before 0.84.0: all minute rows into Python."""
    return stats.per_day(stats.activity(db, start, end))


def snapshot(db, today, now, items):
    """Every statistic the Dashboard, Screen Time (all ranges + calendar), weekly digest, Reminders page and CSV
    export derive from the tables retention prunes."""
    out = {"first": stats.first_activity(db)}
    for goal in (None, 1800, 3 * 3600, 5 * 3600, 9 * 3600, 20 * 3600):   # (20 h: a streak back to day one)
        out[("streaks", goal)] = stats.streaks(db, today, goal)
    out["avg_switches_now"] = stats.average_daily_switches(db, today, until=now.time())
    out["avg_switches_day"] = stats.average_daily_switches(db, today)
    out["time_saved"] = stats.time_saved(stats.blocked_events(db, today), items, [],
                                         stats.switches(db, today - timedelta(days=30), today + timedelta(days=1)),
                                         now)
    out["per_day_all"] = stats.daily_active(db, today - timedelta(days=DAYS + 5), today + timedelta(days=1))
    for back in range(0, DAYS + 31, 28):     # calendar months reaching back past the cut-off
        first = (today - timedelta(days=back)).replace(day=1)
        nxt = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        out[("month", first)] = stats.daily_active(db, first, nxt)
    for back in range(DAYS):                  # calendar: click a day
        day = today - timedelta(days=back)
        apps = stats.app_totals(db, day, day + timedelta(days=1))
        out[("day_apps", day)] = (sum(apps.values()), [(n, s) for n, s in apps.most_common(10) if s >= 60])
        events = stats.blocked_events(db, day)
        out[("blocked", day)] = (len(events), Counter((e["item_id"], e["name"]) for e in events))
    for name in stats.RANGES:                  # Screen Time ranges: raw detail, untouched
        start, end = stats.range_dates(name, today)
        rows, events = stats.activity(db, start, end), stats.switches(db, start, end)
        out[("range", name)] = (rows, events, stats.switch_summary(events, now), stats.sessions(rows),
                                stats.longest_focus(rows))
    for span in (7, 30):                       # trend / bars
        out[("trend", span)] = stats.daily_active(db, today - timedelta(days=span - 1), today + timedelta(days=1))
    midnight = datetime.combine(today, time())
    out["breaks_today"] = reminders.counts(db, "break", midnight)
    out["week_counts"] = reminders.counts_many(db, ["r1", "r2", "r3", "sleep"], midnight - timedelta(days=7))
    out["digest"] = digest.summary(db, today, 5 * 3600, lambda kind, name: name)
    out["unlocks"] = db.unlocks_since(datetime(2000, 1, 1))
    return out


def csv_rows(db, tmp_path, name):
    path = tmp_path / name
    backup.screen_time_csv(db, str(path), days=400)
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("unlock_days", [(3, 17, 40, 55), (45,), ()])
def test_sixty_days_of_minutes_give_the_same_statistics_after_retention(tmp_path, monkeypatch, unlock_days):
    """unlock_days: (45,) makes the "no emergency unlock" streak reach back past the cut-off into rolled-up days."""
    monkeypatch.setattr(backup, "date", type("D", (date,), {"today": staticmethod(lambda: TODAY)}))
    db = Database(tmp_path / "t.db")
    info = build(db.path, TODAY, days=DAYS, unlock_days=unlock_days)
    now, items = info["now"], db.list_items()
    before = snapshot(db, TODAY, now, items)
    csv_before = csv_rows(db, tmp_path, "before.csv")
    # the SQL versions agree with the old Python ones on the raw data
    assert before["per_day_all"] == _old_per_day(db, TODAY - timedelta(days=DAYS + 5), TODAY + timedelta(days=1))
    raw_rows = db.conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0]

    deleted = retention.run(db, TODAY)

    cut = retention.cutoff(TODAY)
    assert deleted > 0
    assert db.conn.execute("SELECT COUNT(*) FROM activity WHERE minute < ?", (f"{cut} 00:00",)).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM switch_events WHERE timestamp < ?", (f"{cut} 00:00:00",)
                           ).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM reminder_log WHERE timestamp < ?", (f"{cut} 00:00:00",)
                           ).fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] < raw_rows * 0.7
    assert db.conn.execute("SELECT COUNT(*) FROM daily_activity").fetchone()[0] > 0
    after = snapshot(db, TODAY, now, items)
    for key in before:
        assert after[key] == before[key], key
    assert before[("streaks", 20 * 3600)]["goal"] == DAYS                 # spans rolled-up days ...
    assert before[("streaks", None)]["no_unlock"] == (min(unlock_days) if unlock_days else DAYS)
    assert sum(before[("month", (TODAY - timedelta(days=56)).replace(day=1))].values()) > 0   # ... and so does this
    assert csv_rows(db, tmp_path, "after.csv") == csv_before


def test_detail_inside_the_window_is_kept(tmp_path):
    db = Database(tmp_path / "t.db")
    build(db.path, TODAY, days=DAYS)
    start = retention.cutoff(TODAY)
    rows = stats.activity(db, start, TODAY + timedelta(days=1))
    events = stats.switches(db, start, TODAY + timedelta(days=1))
    retention.run(db, TODAY)
    assert stats.activity(db, start, TODAY + timedelta(days=1)) == rows
    assert stats.switches(db, start, TODAY + timedelta(days=1)) == events
    # "30 days" and time saved's 30-day history are both inside what is kept
    assert retention.cutoff(TODAY) <= stats.range_dates("30 days", TODAY)[0]
    assert retention.cutoff(TODAY) <= TODAY - timedelta(days=30)


def test_limits_emergency_unlocks_and_protection_state_are_untouched(tmp_path):
    """Nothing enforcement needs is pruned: limit usage (day / week / month and openings, the whole current month
    and far beyond), emergency unlocks and their per-period count, settings (trusted time offset, notice cooldowns,
    the challenge), rules - including a temporary one."""
    for today in (TODAY, date(2026, 10, 31), date(2026, 11, 1)):   # also the first / last day of a month
        db = Database(tmp_path / f"{today}.db")
        info = build(db.path, today, days=DAYS)
        now = info["now"]
        db.set_setting("limits.reset", json.dumps({"time": "03:00"}))
        db.set_setting("emergency.per", "week")
        db.set_setting("notify.last_alert", json.dumps({"1": now.timestamp()}))
        db.set_setting("clock_offset", "12.5")
        db.set_setting("antibypass", json.dumps({"phrase": True, "length": 60}))
        db.add_unlock([info["items"][0]], ["YouTube"], now - timedelta(hours=1), now + timedelta(minutes=10))

        def state():
            return {
                "usage": db.conn.execute("SELECT owner, bucket, seconds, day FROM usage ORDER BY 1, 2").fetchall(),
                "usage_lookup": db.usage_lookup(now).data,
                "blocks": [(b["item"]["id"], b["reason"], b["until"]) for b in db.blocks(now)],
                "uses_left": emergency.uses_left(db, now),
                "unlocks": db.conn.execute("SELECT * FROM emergency_unlocks ORDER BY id").fetchall(),
                "active_unlocks": db.active_unlocks(now),
                "settings": {k: v for k, v in db.all_settings().items() if k != retention.LAST_KEY},
                "rules": db.conn.execute("SELECT * FROM block_rules ORDER BY id").fetchall(),
                "group_rules": db.conn.execute("SELECT * FROM group_rules ORDER BY id").fetchall(),
                "items": db.list_items(),
            }
        before = state()
        assert retention.run(db, today) > 0
        after = state()
        for key in before:
            assert [tuple(r) for r in after[key]] == [tuple(r) for r in before[key]] \
                if key in ("usage", "unlocks", "rules", "group_rules") else after[key] == before[key], key


def test_retention_runs_once_a_day_and_twice_changes_nothing(tmp_path):
    db = Database(tmp_path / "t.db")
    info = build(db.path, TODAY, days=DAYS)
    assert retention.due(db, TODAY)
    retention.run(db, TODAY)
    assert not retention.due(db, TODAY) and retention.due(db, TODAY + timedelta(days=1))
    first = snapshot(db, TODAY, info["now"], db.list_items())
    assert retention.run(db, TODAY) == 0
    assert snapshot(db, TODAY, info["now"], db.list_items()) == first


def test_the_newest_block_event_survives_so_notices_keep_working(tmp_path):
    """The window notifies about block events with an id above the last one it saw. Deleting the newest row
    would let the next event reuse a low id and never be announced."""
    db = Database(tmp_path / "t.db")
    old = datetime.combine(TODAY - timedelta(days=50), time(12))
    for i in range(3):
        db.add_block_event("reddit.com", 1, "Reddit", "permanent", None, old + timedelta(minutes=i))
    newest = db.last_block_event_id()
    use_every_day(db)
    assert retention.run(db, TODAY) > 0
    assert db.last_block_event_id() == newest
    assert len(stats.blocked_events(db, old.date())) == 3          # (2 rolled up + the kept newest)
    db.add_block_event("x.com", 2, "X", "permanent", None, datetime.combine(TODAY, time(9)))
    assert [e["hostname"] for e in db.block_events_after(newest)] == ["x.com"]


def test_two_processes_rolling_up_at_once_count_each_day_once(tmp_path):
    path = tmp_path / "t.db"
    db = Database(path)
    build(path, TODAY, days=DAYS)
    expected = stats.daily_active(db, TODAY - timedelta(days=DAYS), TODAY + timedelta(days=1))
    errors = []

    def roll():
        try:
            retention.run(Database(path), TODAY)
        except Exception as e:   # pragma: no cover - the assertion below shows it
            errors.append(e)
    threads = [threading.Thread(target=roll) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert stats.daily_active(db, TODAY - timedelta(days=DAYS), TODAY + timedelta(days=1)) == expected


def test_background_thread_uses_its_own_connection_and_trusted_date(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    db = Database(path)
    build(path, TODAY, days=DAYS)
    job = retention.RetentionThread(today=lambda: TODAY, open_db=lambda: Database(path))
    assert job.daemon
    assert job.run_once() > 0
    assert job.run_once() == 0                                      # at most once a day
    assert db.get_setting(retention.LAST_KEY) == TODAY.isoformat()


def test_rolled_up_reminder_days_still_count(tmp_path):
    db = Database(tmp_path / "t.db")
    old = datetime.combine(TODAY - timedelta(days=40), time(10))
    for result in ("done", "done", "not done"):
        reminders.log(db, "r1", result, old)
    since = datetime.combine(old.date(), time())
    before = reminders.counts(db, "r1", since)
    use_every_day(db)
    retention.run(db, TODAY)
    assert db.conn.execute("SELECT COUNT(*) FROM reminder_log").fetchone()[0] == 0
    assert reminders.counts(db, "r1", since) == before == {"done": 2, "not done": 1}


def test_first_activity_survives_the_roll_up(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_activity(f"{TODAY - timedelta(days=45)} 07:13", "code.exe", "", 60, 60)
    db.add_activity(f"{TODAY} 09:00", "code.exe", "", 60, 60)
    use_every_day(db, 40)
    retention.run(db, TODAY)
    assert not db.conn.execute("SELECT 1 FROM activity WHERE minute < ?", (f"{TODAY - timedelta(days=40)}",)).fetchone()
    assert stats.first_activity(db) == datetime.combine(TODAY - timedelta(days=45), time(7, 13))


def test_retention_never_touches_the_tables_it_does_not_own(tmp_path):
    """A guard against someone 'tidying' the wrong table: only the four detail tables lose rows."""
    path = tmp_path / "t.db"
    db = Database(path)
    build(path, TODAY, days=DAYS, network_rows=500)
    con = sqlite3.connect(path)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                        "AND name NOT LIKE 'sqlite_%'")]
    before = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    retention.run(db, TODAY)
    after = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    shrunk = {t for t in tables if after[t] < before[t]}
    assert shrunk == {"activity", "switch_events", "block_events", "reminder_log"}
    grown = {t for t in tables if after[t] > before[t]}
    assert grown <= {"daily_activity", "daily_switches", "daily_block_events", "daily_reminders", "settings"}


# ---------- review of 0.84.0 ----------

@pytest.mark.parametrize("jump", [60, 400])
def test_a_clock_jumped_forward_keeps_recent_detail(tmp_path, jump):
    """Review (data-integrity #1): the service stopped (stale offset) and the Windows clock moved forward, or an
    offline start with a bad hardware clock: retention ran with "today" weeks ahead and rolled up the real today -
    Today / 7 / 30-day views empty, the switch average 0, time saved wrong, for good. The cut-off now also needs
    DETAIL_DAYS + 1 days that really have data after it."""
    db = Database(tmp_path / "t.db")
    info = build(db.path, TODAY, days=DAYS)
    now, items = info["now"], db.list_items()
    before = snapshot(db, TODAY, now, items)
    fake = TODAY + timedelta(days=jump)
    retention.run(db, fake)                    # the jump itself: nothing really recent may go
    assert snapshot(db, TODAY, now, items) == before
    for n in range(3):                         # it keeps running (and recording) at the wrong date for 3 days:
        db.add_activity(f"{fake + timedelta(days=n)} 10:00", "code.exe", "", 60, 60)   # 3 real days of use,
        retention.run(db, fake + timedelta(days=n))                                   # so at most 3 days go
    db.conn.execute("DELETE FROM activity WHERE minute >= ?", (f"{TODAY + timedelta(days=1)}",))
    db.conn.commit()
    after = snapshot(db, TODAY, now, items)
    for name in ("Today", "Yesterday", "7 days"):
        assert after[("range", name)] == before[("range", name)], name
    first_kept = db.conn.execute("SELECT MIN(minute) FROM activity").fetchone()[0][:10]
    assert date.fromisoformat(first_kept) == retention.cutoff(TODAY) + timedelta(days=3)
    assert after["per_day_all"] == before["per_day_all"]      # (and the daily totals never change)


def test_normal_daily_use_still_rolls_up_at_the_cut_off(tmp_path):
    db = Database(tmp_path / "t.db")
    use_every_day(db, DAYS)
    assert retention.kept_from(db, TODAY) == retention.cutoff(TODAY)
    retention.run(db, TODAY)
    first = db.conn.execute("SELECT MIN(minute) FROM activity").fetchone()[0][:10]
    assert first == str(retention.cutoff(TODAY))


def test_too_few_days_of_data_rolls_up_nothing(tmp_path):
    db = Database(tmp_path / "t.db")
    for back in (0, 50, 100):                  # three days of use, far apart: nothing is "a month of detail" yet
        db.add_activity(f"{TODAY - timedelta(days=back)} 12:00", "code.exe", "", 60, 60)
    assert retention.kept_from(db, TODAY) is None
    assert retention.run(db, TODAY) == 0
    assert db.conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 3


def test_one_stray_old_row_is_not_thousands_of_transactions(tmp_path):
    """Review (perf #4): one row dated 1970 made the first pass walk 20 000 empty days, each a write transaction."""
    db = Database(tmp_path / "t.db")
    db.add_activity("1970-01-01 00:00", "x.exe", "", 1, 1)
    use_every_day(db)
    seen = []
    db.conn.set_trace_callback(seen.append)
    retention.run(db, TODAY)
    begins = [s for s in seen if s.startswith("BEGIN IMMEDIATE")]
    assert len(begins) == 1 + (DAYS - retention.DETAIL_DAYS - 1)      # 1970 + the real days before the cut-off
    assert db.conn.execute("SELECT COUNT(*) FROM activity WHERE minute < '2000'").fetchone()[0] == 0


def test_the_kept_newest_block_event_does_not_stall_the_pass(tmp_path):
    db = Database(tmp_path / "t.db")
    use_every_day(db)
    db.add_block_event("a.com", 1, "A", "permanent", None, datetime.combine(TODAY - timedelta(days=55), time(9)))
    db.conn.execute("DELETE FROM activity WHERE minute >= ?", (f"{TODAY - timedelta(days=54)}",))
    db.conn.commit()
    use_every_day(db, DAYS - 10, TODAY)       # (newer days of use, but the newest block event is 55 days old)
    retention.run(db, TODAY)                  # returns: the day that keeps its newest event is not retried forever
    assert db.last_block_event_id() == 1


def test_retention_refreshes_the_query_statistics(tmp_path):
    """Review (perf #5): ANALYZE ran once at migration (on a new install: on empty tables); the pass that deletes
    most rows now ends with PRAGMA optimize."""
    db = Database(tmp_path / "t.db")
    use_every_day(db)
    seen = []
    db.conn.set_trace_callback(seen.append)
    assert retention.run(db, TODAY) > 0
    assert "PRAGMA optimize" in seen


@pytest.mark.parametrize("call", ["blocked_events", "days_with_activity", "first_activity"])
def test_detail_plus_roll_up_reads_are_one_statement(tmp_path, call):
    """Review (data-integrity #2): blocked_events read the detail and the roll-ups in two statements; retention
    committing that day in between counted its visits twice (or, for the active days, missed it). Each read is now
    a single statement - one consistent snapshot."""
    db = Database(tmp_path / "t.db")
    build(db.path, TODAY, days=DAYS)
    use_every_day(db)
    retention.run(db, TODAY)
    seen = []
    db.conn.set_trace_callback(seen.append)
    day = TODAY - timedelta(days=40)
    {"blocked_events": lambda: stats.blocked_events(db, day),
     "days_with_activity": lambda: stats._days_with_activity(db, TODAY - timedelta(days=50), TODAY),
     "first_activity": lambda: stats.first_activity(db)}[call]()
    assert len(seen) == 1, seen


def test_days_with_activity_spans_detail_and_roll_ups(tmp_path):
    db = Database(tmp_path / "t.db")
    use_every_day(db)
    expected = {str(TODAY - timedelta(days=b)) for b in range(1, 50)}
    assert stats._days_with_activity(db, TODAY - timedelta(days=49), TODAY) == expected
    retention.run(db, TODAY)
    assert stats._days_with_activity(db, TODAY - timedelta(days=49), TODAY) == expected
    assert stats._days_with_activity(db, TODAY, TODAY) == set()


def test_first_activity_reads_the_roll_ups_through_their_key(tmp_path):
    """Review (perf #7): MIN(first_minute) over all of daily_activity was a full scan, twice per Dashboard refresh."""
    db = Database(tmp_path / "t.db")
    plan = " | ".join(r[3] for r in db.conn.execute(
        "EXPLAIN QUERY PLAN SELECT MIN(first_minute) FROM daily_activity "
        "WHERE day = (SELECT MIN(day) FROM daily_activity)"))
    assert "sqlite_autoindex_daily_activity_1" in plan, plan


def test_reminder_counts_from_mid_day_leave_out_a_rolled_up_start_day(tmp_path):
    """Review (data-integrity #3): the documented rule - a rolled-up day keeps no time of day, so a `since` in the
    middle of one leaves that day out; from midnight it counts in full."""
    db = Database(tmp_path / "t.db")
    day = TODAY - timedelta(days=40)
    for hour in (9, 15):
        reminders.log(db, "break", "taken", datetime.combine(day, time(hour)))
    use_every_day(db)
    retention.run(db, TODAY)
    assert reminders.counts(db, "break", datetime.combine(day, time())) == {"taken": 2}
    assert reminders.counts(db, "break", datetime.combine(day, time(12))) == {}


def test_a_backup_does_not_carry_when_retention_last_ran(tmp_path):
    """Review (data-integrity #4): retention.last_day was exported and compared, so importing a backup a day later
    always reported "1 other setting will change"."""
    assert retention.LAST_KEY in backup.RUNTIME_KEYS
    db = Database(tmp_path / "t.db")
    use_every_day(db)
    retention.run(db, TODAY - timedelta(days=1))
    data = backup.export(db)
    assert retention.LAST_KEY not in data["settings"]
    retention.run(db, TODAY)
    assert backup.diff(db, data) == backup.diff(db, backup.export(db))
    assert not any("other setting" in line for line in backup.diff(db, data))
