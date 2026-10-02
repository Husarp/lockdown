"""Data retention: per-minute screen time and per-event logs are kept for DETAIL_DAYS, older days become daily totals.

The tables that grow forever - `activity` (one row per minute per app/site), `switch_events`, `block_events` and
`reminder_log` - made an old install slower every day (design/rebuild/inventory-perf.md #4). Once a day, in a
background thread, every whole day older than the cut-off is added to its daily roll-up table (`daily_activity`,
`daily_switches`, `daily_block_events`, `daily_reminders`, kept forever) and its detail rows are deleted - both in
ONE transaction per day, so at any moment a day is counted exactly once (raw rows + roll-up rows) and the totals
behind charts, streaks, the calendar, averages and the CSV export come out the same as before.

Never touched (enforcement / Anti-Bypass needs them): `usage` (time and opening counts for every day / week / month
limit and allowance), `emergency_unlocks` (uses per period, history), `settings` (trusted time, notice cooldowns,
the challenge state ...), the rules themselves (temporary rules expire through their own path), `network_log`
(the service keeps an hour of it).
"""
import logging
import threading
from datetime import date, datetime, timedelta

log = logging.getLogger("lockdown.retention")

# The user agreed to keep detail for 30 days. Two more: "time saved" compares with the last 30 days *before* today
# (today - 30), and a limit day that starts after midnight can begin the evening before. None of this detail is
# used by enforcement (limits count in `usage`), so this is about the statistics staying exact.
DETAIL_DAYS = 32
LAST_KEY = "retention.last_day"   # the trusted date retention last finished on (it runs at most once a day)
START_DELAY_SEC = 120             # let the app start first
CHECK_SEC = 3600                  # then look once an hour whether a new day has come


def cutoff(today: date) -> date:
    """The first day whose detail is kept; everything before it is rolled up."""
    return today - timedelta(days=DETAIL_DAYS)


def due(db, today: date) -> bool:
    return db.get_setting(LAST_KEY) != today.isoformat()


def _first_day(db, before: date) -> date | None:
    """The oldest day that still has detail rows before `before`."""
    t = f"{before} 00:00:00"
    found = []
    for sql, arg in (("SELECT MIN(minute) FROM activity WHERE minute < ?", f"{before} 00:00"),
                     ("SELECT MIN(timestamp) FROM switch_events WHERE timestamp < ?", t),
                     ("SELECT MIN(timestamp) FROM block_events WHERE timestamp < ?", t),
                     ("SELECT MIN(timestamp) FROM reminder_log WHERE timestamp < ?", t)):
        value = db.conn.execute(sql, (arg,)).fetchone()[0]
        if value:
            try:
                found.append(date.fromisoformat(value[:10]))
            except ValueError:   # a malformed row can't be dated: leave it alone
                pass
    return min(found) if found else None


def roll_up_day(db, day: date) -> int:
    """Move one day's detail into the daily tables. One IMMEDIATE transaction: the rows are read under the write
    lock, so two processes doing this at once can't count a day twice. Returns the detail rows deleted."""
    m0, m1 = f"{day} 00:00", f"{day + timedelta(days=1)} 00:00"
    t0, t1 = f"{day} 00:00:00", f"{day + timedelta(days=1)} 00:00:00"
    conn = db.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO daily_activity (day, exe, site, seconds, active_seconds, first_minute) "
            "SELECT ?, exe, site, SUM(seconds), SUM(active_seconds), MIN(minute) FROM activity "
            "WHERE minute >= ? AND minute < ? GROUP BY exe, site "
            "ON CONFLICT(day, exe, site) DO UPDATE SET seconds = seconds + excluded.seconds, "
            "active_seconds = active_seconds + excluded.active_seconds, "
            "first_minute = MIN(COALESCE(first_minute, excluded.first_minute), excluded.first_minute)",
            (day.isoformat(), m0, m1))
        n = conn.execute("DELETE FROM activity WHERE minute >= ? AND minute < ?", (m0, m1)).rowcount
        conn.execute(
            "INSERT INTO daily_switches (day, exe, site, count) "
            "SELECT ?, COALESCE(exe, ''), COALESCE(site, ''), COUNT(*) FROM switch_events "
            "WHERE timestamp >= ? AND timestamp < ? GROUP BY COALESCE(exe, ''), COALESCE(site, '') "
            "ON CONFLICT(day, exe, site) DO UPDATE SET count = count + excluded.count",
            (day.isoformat(), t0, t1))
        n += conn.execute("DELETE FROM switch_events WHERE timestamp >= ? AND timestamp < ?", (t0, t1)).rowcount
        # the newest block event always stays: the window notifies about ids above the last one it saw, and with
        # it gone the next event would reuse a low id and never be shown
        newest = "(SELECT COALESCE(MAX(id), 0) FROM block_events)"
        conn.execute(
            "INSERT INTO daily_block_events (day, item_id, display_name, hostname, reason, count) "
            "SELECT ?, item_id, display_name, hostname, reason, COUNT(*) FROM block_events "
            f"WHERE timestamp >= ? AND timestamp < ? AND id < {newest} "
            "GROUP BY item_id, display_name, hostname, reason", (day.isoformat(), t0, t1))
        n += conn.execute(f"DELETE FROM block_events WHERE timestamp >= ? AND timestamp < ? AND id < {newest}",
                          (t0, t1)).rowcount
        conn.execute(
            "INSERT INTO daily_reminders (day, what, result, count) "
            "SELECT ?, what, result, COUNT(*) FROM reminder_log WHERE timestamp >= ? AND timestamp < ? "
            "GROUP BY what, result", (day.isoformat(), t0, t1))
        n += conn.execute("DELETE FROM reminder_log WHERE timestamp >= ? AND timestamp < ?", (t0, t1)).rowcount
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return n


def _newest_day(db, before: date) -> date | None:
    """The newest day before `before` that has detail rows (in any of the four tables)."""
    t = f"{before} 00:00:00"
    found = []
    for sql, arg in (("SELECT MAX(minute) FROM activity WHERE minute < ?", f"{before} 00:00"),
                     ("SELECT MAX(timestamp) FROM switch_events WHERE timestamp < ?", t),
                     ("SELECT MAX(timestamp) FROM block_events WHERE timestamp < ?", t),
                     ("SELECT MAX(timestamp) FROM reminder_log WHERE timestamp < ?", t)):
        value = db.conn.execute(sql, (arg,)).fetchone()[0]
        if value:
            try:
                found.append(date.fromisoformat(value[:10]))
            except ValueError:
                pass
    return max(found) if found else None


def kept_from(db, today: date) -> date | None:
    """The oldest of the DETAIL_DAYS + 1 newest days, up to `today`, that have detail - from it on nothing is rolled
    up. None while there are fewer such days than that (nothing may go yet).

    A clock that is wrong forwards (a stale offset after the service was stopped and the Windows clock moved, or an
    offline start with a bad hardware clock) must not delete detail that is really recent: with only the date, a
    jump of 60 days rolled up "today" (review of 0.84.0). Counting days that really have data, the jump adds only
    the days written at the wrong date - real detail goes only once as many days of use have actually followed.
    Walks back through the indexes, DETAIL_DAYS + 1 steps."""
    bound, found = today + timedelta(days=1), None
    for _ in range(DETAIL_DAYS + 1):
        found = _newest_day(db, bound)
        if found is None:
            return None
        bound = found
    return found


def run(db, today: date, stop: threading.Event | None = None) -> int:
    """Roll up every day before cutoff(today) - and before kept_from(today), so a wrong clock can't take recent
    detail - oldest first, one short transaction per day (the service and the tracker keep writing in between),
    skipping days without detail. Returns the detail rows deleted."""
    end = cutoff(today)
    keep = kept_from(db, today)
    end = min(end, keep) if keep is not None else None
    deleted = 0
    day = _first_day(db, end) if end is not None else None
    while day is not None and day < end:
        if stop is not None and stop.is_set():
            return deleted
        deleted += roll_up_day(db, day)
        nxt = _first_day(db, end)   # (jump over empty days: one stray row from 1970 is not 20 000 transactions)
        day = None if nxt is None else max(nxt, day + timedelta(days=1))   # (the kept newest block event)
    if deleted:
        db.conn.execute("PRAGMA analysis_limit=1000")
        db.conn.execute("PRAGMA optimize")   # the statistics were taken before most rows went
    db.set_setting(LAST_KEY, today.isoformat())
    return deleted


class RetentionThread(threading.Thread):
    """In the tray agent: once a day (trusted date), after START_DELAY_SEC. Its own connection, never the window's."""

    def __init__(self, today=None, open_db=None):
        super().__init__(name="lockdown-retention", daemon=True)
        self.stop_event = threading.Event()
        self._today = today
        self._open_db = open_db

    def run(self):
        if self.stop_event.wait(START_DELAY_SEC):
            return
        while True:
            try:
                self.run_once()
            except Exception:
                log.exception("Retention failed - trying again in an hour")
            if self.stop_event.wait(CHECK_SEC):
                return

    def run_once(self) -> int:
        from db import Database
        from trusted_time import now_from_db
        db = self._open_db() if self._open_db else Database()
        try:
            today = self._today() if self._today else now_from_db(db).date()
            if not due(db, today):
                return 0
            started = datetime.now()
            n = run(db, today, self.stop_event)
            if n:
                log.info("Retention: rolled %d detail rows older than %s into daily totals (%.1f s)", n,
                         cutoff(today), (datetime.now() - started).total_seconds())
            return n
        finally:
            db.close()
