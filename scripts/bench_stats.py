"""Time the Dashboard / Screen Time data work on a big synthetic database.

    python scripts/bench_stats.py                      # this checkout
    python scripts/bench_stats.py --src /tmp/ld-old/src   # an older checkout, for before / after numbers

Builds (once per --src, in a temp folder) a database with a year of per-minute screen time, switches, blocked
visits and reminder answers, 10 000 network-log rows and 6 000 blocked items, then times what a Dashboard refresh,
the Screen Time overview / calendar, the weekly summary and the 5-second watcher tick read from it - the median
of several runs, in milliseconds. With a checkout that has retention.py it also times the first retention pass and
then everything again on the rolled-up database. Runs on Windows and (with the test fakes) on Linux.
"""
import argparse
import importlib
import statistics
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def timed(fn, runs: int) -> float:
    samples = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return statistics.median(samples)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "src"), help="the src folder to measure")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--items", type=int, default=6000)
    ap.add_argument("--network", type=int, default=10_000)
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    sys.path.insert(0, args.src)
    sys.path.insert(1, str(ROOT / "tests"))
    if sys.platform != "win32":
        import conftest  # noqa: F401  (inert Windows / Tk fakes so the modules import)
    from synthdata import build
    db_mod = importlib.import_module("db")
    stats = importlib.import_module("stats")
    digest = importlib.import_module("digest")
    modes = importlib.import_module("modes")
    reminders = importlib.import_module("reminders")
    rules = importlib.import_module("rules")
    dashboard = importlib.import_module("gui.dashboard")
    try:
        retention = importlib.import_module("retention")
    except ImportError:
        retention = None
    new = hasattr(stats, "daily_active")

    today = date(2026, 10, 1)
    path = Path(tempfile.mkdtemp(prefix="lockdown-bench-")) / "config.db"
    t = time.perf_counter()
    db_mod.Database(path).close()
    info = build(path, today, days=args.days, extra_items=args.items, network_rows=args.network)
    built = time.perf_counter() - t
    db = db_mod.Database(path)
    now = info["now"]
    rows = db.conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0]
    print(f"src: {args.src}  ({'0.84 code' if new else 'pre-0.84 code'})")
    print(f"database: {rows:,} activity rows, "
          f"{db.conn.execute('SELECT COUNT(*) FROM switch_events').fetchone()[0]:,} switches, "
          f"{db.conn.execute('SELECT COUNT(*) FROM block_events').fetchone()[0]:,} blocked visits, "
          f"{len(db.list_items()):,} items, {args.network:,} network rows  (built in {built:.1f} s)")

    upcoming = getattr(dashboard, "upcoming", None) or (lambda *a: dashboard.DashboardPage._upcoming(None, *a))
    per_day = (lambda s, e: stats.daily_active(db, s, e)) if new else \
        (lambda s, e: stats.per_day(stats.activity(db, s, e)))

    def dashboard_refresh():
        """The data side of DashboardPage.refresh (gui/dashboard.py), as each version does it."""
        first = stats.first_activity(db)
        items, groups = db.list_items(), db.list_groups()
        usage = db.usage_lookup(now)
        db.categories()
        r = stats.activity(db, today, today + timedelta(days=1))
        events = stats.switches(db, today, today + timedelta(days=1))
        state = modes.active(db, now)
        y = [x for x in stats.activity(db, today - timedelta(days=1), today) if x["minute"][11:] <= f"{now:%H:%M}"]
        stats.totals(r), stats.totals(y)
        [i for i in items if rules.item_block(rules.effective_rules(i, groups), now, usage)]
        coming = upcoming(now, items, groups, usage)
        stats.average_daily_switches(db, today, until=now.time())
        blocked = stats.blocked_events(db, today)
        history = stats.switches(db, today - timedelta(days=30), today + timedelta(days=1))
        stats.time_saved(blocked, items, [], history, now)
        stats.timeline(r, today, lambda exe, site: "neutral")
        per_day(today - timedelta(days=6), today + timedelta(days=1))
        db.unlocks_since(datetime.combine(today - timedelta(days=6), datetime.min.time()))
        if not new:
            upcoming(now, items, groups, usage)   # 0.83 ran it a second time for "Coming up"
        stats.blocked_events(db, today)
        stats.per_app(r), stats.switch_summary(events, now)
        goal = float(db.get_setting("stats.goal_hours", "5")) * 3600
        stats.streaks(db, today, goal)
        return first, state, coming

    def screen_time_30_days():
        start, end = stats.range_dates("30 days", today)
        r, events = stats.activity(db, start, end), stats.switches(db, start, end)
        if hasattr(stats, "minute_summary"):   # (0.84.0 review: the minute summary once, for both)
            minutes = stats.minute_summary(r)
            stats.totals(r), stats.longest_focus(r, minutes), stats.sessions(r, minutes)
        else:
            stats.totals(r), stats.longest_focus(r), stats.sessions(r)
        stats.switch_summary(events, now)
        if new:
            per_day(today - timedelta(days=29), today + timedelta(days=1))
            since = (today - timedelta(days=6)).isoformat()
            week = [x for x in r if x["minute"][:10] >= since] if hasattr(stats, "minute_summary") else \
                stats.activity(db, today - timedelta(days=6), today + timedelta(days=1))
        else:
            span = stats.activity(db, today - timedelta(days=29), today + timedelta(days=1))
            stats.per_day(span)
            week = [x for x in span if x["minute"][:10] >= (today - timedelta(days=6)).isoformat()]
        stats.hourly_minutes(week, [today - timedelta(days=6 - i) for i in range(7)])
        per_day(today - timedelta(days=29), today + timedelta(days=1))          # trend

    def calendar_old_month():
        first = (today - timedelta(days=95)).replace(day=1)
        nxt = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        per_day(first, nxt)
        day = first + timedelta(days=10)
        if new:
            stats.app_totals(db, day, day + timedelta(days=1)).most_common(10)
        else:
            stats.per_app(stats.activity(db, day, day + timedelta(days=1))).most_common(10)

    def watcher_tick():
        """app._poll_watcher: items, groups, usage, blocks (0.83: usage_lookup twice, lists twice)."""
        items, groups, usage = db.list_items(), db.list_groups(), db.usage_lookup(now)
        if new:
            db.blocks(now, usage=usage, items=items, groups=groups)
        else:
            db.blocks(now)

    def status_poll():
        """app._poll_status every 3 s (the tray's "N sites/apps blocked")."""
        return db.item_count() if hasattr(db, "item_count") else len(db.list_items())

    def network_page():
        since = now - timedelta(hours=1)
        db.network_since(since.strftime("%Y-%m-%d %H:%M"))
        db.block_events_since(since)

    def settings_reads():
        from trusted_time import now_from_db
        for _ in range(1000):
            now_from_db(db)
            db.get_setting("notify.cooldown_min", "30")

    def reminder_counts():
        since = datetime.combine(today - timedelta(days=7), datetime.min.time())
        whats = ["r1", "r2", "r3", "sleep", "break"] * 2
        if hasattr(reminders, "counts_many"):
            reminders.counts_many(db, whats, since)
        else:
            for w in whats:
                reminders.counts(db, w, since)

    benches = [("open Database()", lambda: db_mod.Database(path).close()),
               ("Dashboard refresh (data)", dashboard_refresh),
               ("  stats.streaks", lambda: stats.streaks(db, today, 5 * 3600)),
               ("  stats.average_daily_switches", lambda: stats.average_daily_switches(db, today, until=now.time())),
               ("  time saved (30-day history)", lambda: stats.time_saved(
                   stats.blocked_events(db, today), db.list_items(), [],
                   stats.switches(db, today - timedelta(days=30), today + timedelta(days=1)), now)),
               ("Screen Time, 30 days", screen_time_30_days),
               ("Screen Time calendar, 3 months ago", calendar_old_month),
               ("weekly digest", lambda: digest.summary(db, today, 5 * 3600, lambda k, n: n)),
               ("watcher tick (blocks)", watcher_tick),
               ("list_items (tracker, every 2 s)", db.list_items),
               ("status poll (every 3 s)", status_poll),
               ("Network Log page", network_page),
               ("1000 x now_from_db + get_setting", settings_reads),
               ("reminder counts (10)", reminder_counts)]

    def run_all(label):
        print(f"\n{label}")
        out = {}
        for name, fn in benches:
            out[name] = timed(fn, args.runs)
            print(f"  {name:<40} {out[name]:9.1f} ms")
        return out

    results = {"before retention": run_all("median ms, full year of detail:")}
    if retention is not None:
        t = time.perf_counter()
        deleted = retention.run(db_mod.Database(path), today)
        print(f"\nfirst retention pass: {deleted:,} detail rows rolled up in {time.perf_counter() - t:.1f} s "
              f"({db.conn.execute('SELECT COUNT(*) FROM activity').fetchone()[0]:,} activity rows left)")
        results["after retention"] = run_all("median ms, after retention (30+ days of detail, daily totals):")
    return results


if __name__ == "__main__":
    main()
