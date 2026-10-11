"""R15: the Api behind Screen Time's Switches and Calendar tabs (webapi/screentime.py: the `switches` builder,
`screentime_month`, `screentime_day`).

What is checked: the Switches numbers equal what the old window's `stats.switch_summary` gives on the same events (counts,
per hour, short visits, the average, most switched-to, the peak sentence, the usual comparison for Today), a visit that
runs over midnight and the last visit; the month equals the daily totals, future days are flagged, Within goal with and
without a goal, the busiest day, the month's edges; a day's details are the top 10 apps with at least a minute; bad input
is refused; and every one of them answers in well under 50 ms on a 730-day database after retention.
"""
import statistics
import time
from datetime import date, datetime, timedelta

import pytest

import retention
import stats
import webapi
from db import Database
from synthdata import build
from webapi import core
from webapi import screentime as st

NOW = datetime(2026, 10, 11, 15, 42, 17)        # = tests/synthdata's "now" (a Sunday)
TODAY = NOW.date()
RANGES = list(stats.RANGES)


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


def make(path, now=NOW):
    ctx = webapi.make_context(path, clock=lambda db: now)
    return ctx, core.build_api(ctx)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    path = tmp_path_factory.mktemp("st60") / "t.db"
    Database(path).close()
    build(path, TODAY, days=60)
    ctx, api = make(path)
    return type("Synth", (), {"path": path, "ctx": ctx, "api": api})()


@pytest.fixture
def fresh(synth):
    st.forget(synth.ctx)
    return synth


def blank(tmp_path, now=NOW):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "b.db"
    Database(path).close()
    ctx, api = make(path, now)
    return ctx, api, ctx.db


def switch(db, day, hhmmss, exe="code.exe", site=""):
    db.add_switch(datetime.fromisoformat(f"{day} {hhmmss}"), exe, site)


def put(db, day, hhmm, exe="code.exe", seconds=60, active=60):
    db.add_activity(f"{day} {hhmm}", exe, "", seconds, active)


def roll_up(db):
    """Retention only rolls up once there are more than 32 days of detail after the old ones: add those, then run it."""
    for back in range(0, 40):
        put(db, TODAY - timedelta(days=back), "07:00", "other.exe")
    db.conn.commit()
    retention.run(db, TODAY)


# ---------------------------------------------------------------- Switches: the old numbers, from stats

def old(db, rng):
    start, end = stats.range_dates(rng, TODAY)
    return stats.switch_summary(stats.switches(db, start, end), NOW)


@pytest.mark.parametrize("rng", RANGES)
def test_the_switches_tab_says_what_switch_summary_says(fresh, rng):
    d = data(fresh.api.screentime_get("switches", rng))
    want = old(fresh.ctx.db, rng)
    cards = {c["key"]: c for c in d["cards"]}
    assert int(cards["switches"]["value"]) == want["count"] > 0
    assert d["hours"]["counts"] == [want["per_hour"].get(h, 0) for h in range(24)]
    assert int(cards["short"]["value"]) == want["short"]
    assert cards["visit"]["value"] == stats.ms(want["avg_visit"])
    items = fresh.ctx.db.list_items()
    from gui import appinfo
    top = [appinfo.name_of(kind, name, items) for kind, name in want["short_top"]]
    assert cards["short"]["sub"] == (f"mostly {' and '.join(top)}" if top else "")
    peak = max(want["per_hour"], key=want["per_hour"].get)
    assert d["peak"] == f"Peak between {peak:02d}:00 and {peak + 1:02d}:00 - {want['per_hour'][peak]} switches."
    rows = d["targets"]
    assert len(rows) == min(5, len(want["targets"]))
    for row, ((kind, name), count, avg) in zip(rows, want["targets"][:5]):
        text, tone = st.VISIT_STYLES[stats.visit_style(count, avg)]
        assert (row["kind"], row["name"], row["count"], row["avg"], row["style"], row["tone"]) == \
            (kind, name, count, f"avg {stats.ms(avg)}", text, tone)
        assert row["display"] == appinfo.name_of(kind, name, items)


def test_the_switch_labels_follow_the_range(fresh):
    labels = {r: data(fresh.api.screentime_get("switches", r))["cards"][0]["label"] for r in RANGES}
    assert labels == {"Today": "Switches today", "Yesterday": "Switches yesterday", "7 days": "Switches (7 days)",
                      "30 days": "Switches (30 days)"}


def test_a_visit_over_midnight_and_the_last_visit_are_counted_like_before(tmp_path):
    ctx, api, db = blank(tmp_path)
    y = TODAY - timedelta(days=1)
    put(db, TODAY, "09:00")
    switch(db, y, "23:50:00", "code.exe")
    switch(db, TODAY, "08:00:00", "chrome.exe", "reddit.com")      # code.exe's visit lasts 8 h 10 m, over midnight
    switch(db, TODAY, "08:00:10", "chrome.exe", "reddit.com")
    switch(db, TODAY, "15:30:00", "code.exe")                      # the last visit: until now (12 m 17 s), under the 30 min cap
    for rng in ("7 days", "Today"):
        d = data(api.screentime_get("switches", rng))
        want = old(db, rng)
        assert int(d["cards"][0]["value"]) == want["count"]
        assert d["cards"][1]["value"] == stats.ms(want["avg_visit"])
        assert [(r["kind"], r["name"], r["count"], r["avg"]) for r in d["targets"]] == \
            [(k, n, c, f"avg {stats.ms(a)}") for (k, n), c, a in want["targets"][:5]]
    far = NOW + timedelta(hours=3)                                 # the last visit is capped at 30 minutes when now is far off
    ctx2, api2 = make(tmp_path / "b.db", far)
    d = data(api2.screentime_get("switches", "Today"))
    want = stats.switch_summary(stats.switches(ctx2.db, TODAY, TODAY + timedelta(days=1)), far)
    assert d["cards"][1]["value"] == stats.ms(want["avg_visit"])


def test_equal_peaks_pick_the_hour_with_the_earliest_switch(tmp_path):
    ctx, api, db = blank(tmp_path)
    put(db, TODAY, "09:00")
    switch(db, TODAY, "14:05:00")
    switch(db, TODAY, "14:06:00", "chrome.exe")
    switch(db, TODAY, "09:05:00")
    switch(db, TODAY, "09:06:00", "chrome.exe")
    d = data(api.screentime_get("switches", "Today"))
    want = old(db, "Today")
    peak = max(want["per_hour"], key=want["per_hour"].get)
    assert peak == 9 and d["peak"].startswith("Peak between 09:00 and 10:00 - 2 switches")


def test_the_peak_in_the_last_hour_ends_at_24_00_like_the_old_window(tmp_path):
    ctx, api, db = blank(tmp_path)
    put(db, TODAY, "09:00")
    switch(db, TODAY, "23:10:00")
    assert data(api.screentime_get("switches", "7 days"))["peak"] == "Peak between 23:00 and 24:00 - 1 switch."


def test_an_empty_switch_log_is_an_empty_tab(tmp_path):
    ctx, api, db = blank(tmp_path)
    put(db, TODAY, "09:00")                                         # (something was recorded, no switch)
    d = data(api.screentime_get("switches", "Today"))
    assert [c["value"] for c in d["cards"]] == ["0", "-", "0"]
    assert d["hours"]["counts"] == [0] * 24 and d["peak"] == "" and d["targets"] == []
    assert d["cards"][0]["sub"] == ""


def test_the_usual_comparison_for_today(tmp_path):
    ctx, api, db = blank(tmp_path)
    for back in range(1, 4):                                        # 3 earlier days with 4 switches before 15:42 each
        day = TODAY - timedelta(days=back)
        put(db, day, "09:00")
        for h in (8, 9, 10, 11):
            switch(db, day, f"{h:02d}:00:00")
    put(db, TODAY, "09:00")

    def card(n):
        db.conn.execute("DELETE FROM switch_events WHERE timestamp >= ?", (f"{TODAY} 00:00:00",))
        for i in range(n):
            switch(db, TODAY, f"{9 + i // 60:02d}:{i % 60:02d}:00")
        db.conn.commit()
        return data(api.screentime_get("switches", "Today"))["cards"][0]

    assert (card(4)["sub"], card(4)["tone"]) == ("about usual for this time of day", "")
    assert (card(10)["sub"], card(10)["tone"]) == ("6 more than usual by this time", "bad")
    assert (card(1)["sub"], card(1)["tone"]) == ("3 fewer than usual by this time", "ok")
    assert data(api.screentime_get("switches", "Yesterday"))["cards"][0]["sub"] == ""
    assert data(api.screentime_get("switches", "7 days"))["cards"][0]["sub"] == ""


def test_no_history_gives_no_comparison(tmp_path):
    ctx, api, db = blank(tmp_path)
    put(db, TODAY, "09:00")
    switch(db, TODAY, "09:00:00")
    assert data(api.screentime_get("switches", "Today"))["cards"][0]["sub"] == ""


def test_the_short_visits_name_the_two_most_common(tmp_path):
    ctx, api, db = blank(tmp_path)
    put(db, TODAY, "09:00")
    t = 0
    for exe, site, n in (("a.exe", "", 3), ("chrome.exe", "reddit.com", 2), ("c.exe", "", 1)):
        for _ in range(n):
            switch(db, TODAY, f"09:{t // 60:02d}:{t % 60:02d}", exe, site)
            t += 5                                                  # every visit lasts 5 s: short
    switch(db, TODAY, "10:00:00", "z.exe")
    d = data(api.screentime_get("switches", "Today"))
    assert d["cards"][2]["value"] == "5" and d["cards"][2]["sub"] == "mostly A and reddit.com"


def test_the_most_switched_to_list_has_five_and_a_site_beats_its_browser(tmp_path):
    ctx, api, db = blank(tmp_path)
    put(db, TODAY, "09:00")
    n = 0
    for i, exe in enumerate(["a.exe", "b.exe", "c.exe", "d.exe", "e.exe", "f.exe", "g.exe"]):
        for _ in range(7 - i):
            switch(db, TODAY, f"09:{n // 60:02d}:{n % 60:02d}", exe)
            n += 20
    switch(db, TODAY, "11:00:00", "chrome.exe", "reddit.com")
    d = data(api.screentime_get("switches", "Today"))
    assert [r["name"] for r in d["targets"]] == ["a.exe", "b.exe", "c.exe", "d.exe", "e.exe"]
    assert all(r["key"] == f"app:{r['name']}" for r in d["targets"])


# ---------------------------------------------------------------- Calendar: the month

def month(api, y, m):
    return data(api.screentime_month(y, m))


def totals_of(db, first, last):
    got = stats.daily_active(db, first, last + timedelta(days=1))
    return {first + timedelta(days=i): got.get((first + timedelta(days=i)).isoformat(), 0) for i in range((last - first).days + 1)}


def test_the_month_equals_the_daily_roll_up_sums(fresh):
    d = month(fresh.api, 2026, 9)
    want = totals_of(fresh.ctx.db, date(2026, 9, 1), date(2026, 9, 30))
    assert [x["sec"] for x in d["days"]] == [want[date(2026, 9, 1) + timedelta(days=i)] for i in range(30)]
    assert [x["day"] for x in d["days"]] == list(range(1, 31)) and d["days"][0]["date"] == "2026-09-01"
    assert d["title"] == "September 2026" and d["offset"] == 1                 # 1 Sep 2026 is a Tuesday
    assert d["stats"][0]["value"] == stats.hm(sum(want.values())) and d["can_next"] is True
    assert all(not x["future"] for x in d["days"])
    for x in d["days"]:
        assert x["level"] == sum(x["sec"] / 3600 >= t for t in (1, 2, 4, 6))
        assert x["text"] == (stats.hm(x["sec"]) if x["sec"] >= 60 else "")


def test_the_month_includes_days_retention_rolled_up(tmp_path):
    ctx, api, db = blank(tmp_path)
    old_day = TODAY - timedelta(days=100)
    for m in range(90):
        put(db, old_day, f"{9 + m // 60:02d}:{m % 60:02d}")
    roll_up(db)
    assert db.conn.execute("SELECT COUNT(*) FROM activity WHERE minute < ?", (f"{TODAY - timedelta(days=60)} 00:00",)).fetchone()[0] == 0
    d = month(api, old_day.year, old_day.month)
    cell = d["days"][old_day.day - 1]
    assert cell["sec"] == 90 * 60 and cell["text"] == "1 h 30 m" and cell["level"] == 1


def test_future_days_are_flagged_and_have_no_text_or_tip(fresh):
    d = month(fresh.api, 2026, 10)
    flags = [x["future"] for x in d["days"]]
    assert flags == [i + 1 > 11 for i in range(31)]
    future = [x for x in d["days"] if x["future"]]
    assert future and all(x["text"] == "" and x["tip"] == "" and x["level"] == 0 and not x["over"] and x["sec"] == 0 for x in future)
    assert d["can_next"] is False and d["today"] == "2026-10-11"
    assert d["days"][10]["date"] == "2026-10-11" and d["days"][10]["sec"] > 0 and d["days"][10]["tip"].startswith("Sunday 11 Oct")
    nxt = month(fresh.api, 2026, 11)                                              # (a month ahead: all future)
    assert all(x["future"] for x in nxt["days"]) and nxt["can_next"] is False
    assert nxt["stats"][0]["value"] == "0 m" and nxt["stats"][3]["value"] == "-"


def test_the_stats_with_a_goal(fresh):
    fresh.ctx.db.set_setting("stats.goal_hours", "5")
    d = month(fresh.api, 2026, 10)
    want = totals_of(fresh.ctx.db, date(2026, 10, 1), TODAY)
    cards = {c["key"]: c for c in d["stats"]}
    assert cards["month"]["value"] == stats.hm(sum(want.values()))
    assert (cards["average"]["value"], cards["average"]["sub"]) == (stats.hm(sum(want.values()) / 11), "over 11 days")
    within = sum(1 for v in want.values() if v <= 5 * 3600)
    assert (cards["within"]["value"], cards["within"]["sub"]) == (f"{within} / 11", "goal 5 h 00 m a day")
    busiest = max(want, key=want.get)
    assert cards["busiest"]["value"] == stats.hm(want[busiest]) and cards["busiest"]["sub"] == f"{st.DAY_NAMES[busiest.weekday()]} {busiest.day} Oct"
    over = [x["date"] for x in d["days"] if x["over"]]
    assert over == [x.isoformat() for x, v in want.items() if v > 5 * 3600]
    assert all("(over your goal)" in x["tip"] for x in d["days"] if x["over"])
    assert d["goal"] == 300 and d["goal_label"] == "5 h"


def test_the_stats_without_a_goal(fresh):
    fresh.ctx.db.set_setting("stats.goal_hours", "0")
    d = month(fresh.api, 2026, 10)
    within = {c["key"]: c for c in d["stats"]}["within"]
    assert (within["value"], within["sub"]) == ("-", "no daily goal set (Settings)")
    assert not any(x["over"] for x in d["days"]) and d["goal"] == 0
    assert "(over your goal)" not in "".join(x["tip"] for x in d["days"])
    fresh.ctx.db.set_setting("stats.goal_hours", "5")


def test_a_finished_month_counts_every_day(fresh):
    d = month(fresh.api, 2026, 9)
    cards = {c["key"]: c for c in d["stats"]}
    assert cards["average"]["sub"] == "over 30 days" and cards["within"]["value"].endswith(" / 30")


def test_the_busiest_day_of_a_month_with_only_idle_time_is_0_m(tmp_path):
    """The old window listed every day that has rows (idle-only ones too): its busiest day is then "0 m", not "-"."""
    ctx, api, db = blank(tmp_path)
    put(db, TODAY - timedelta(days=3), "09:00", seconds=60, active=0)
    put(db, TODAY - timedelta(days=2), "09:00", seconds=60, active=0)
    cards = {c["key"]: c for c in month(api, 2026, 10)["stats"]}
    assert cards["busiest"]["value"] == "0 m" and cards["busiest"]["sub"] == "Thursday 8 Oct"      # (the first of equals)
    put(db, TODAY - timedelta(days=1), "09:00", seconds=60, active=60)
    st.forget(ctx)
    cards = {c["key"]: c for c in month(api, 2026, 10)["stats"]}
    assert (cards["busiest"]["value"], cards["busiest"]["sub"]) == ("1 m", "Saturday 10 Oct")
    # a month with nothing recorded at all stays "-"
    assert {c["key"]: c for c in month(api, 2026, 8)["stats"]}["busiest"]["value"] == "-"


def test_an_empty_month_has_dashes(tmp_path):
    ctx, api, db = blank(tmp_path)
    d = month(api, 2026, 8)
    assert [c["value"] for c in d["stats"]] == ["0 m", "0 m", "31 / 31", "-"]
    assert d["stats"][3]["sub"] == "" and all(x["level"] == 0 and x["text"] == "" for x in d["days"])


def test_a_day_of_one_minute_shows_no_text(tmp_path):
    ctx, api, db = blank(tmp_path)
    put(db, TODAY - timedelta(days=1), "09:00", seconds=30, active=30)
    put(db, TODAY - timedelta(days=2), "09:00", seconds=60, active=60)
    d = month(api, 2026, 10)
    assert d["days"][9]["text"] == "" and d["days"][8]["text"] == "1 m"


def test_the_levels_are_1_2_4_6_hours(tmp_path):
    ctx, api, db = blank(tmp_path)
    for back, minutes in enumerate([59, 60, 119, 120, 239, 240, 359, 360], start=1):
        day = TODAY - timedelta(days=back)
        for m in range(minutes):
            put(db, day, f"{m // 60:02d}:{m % 60:02d}")
    levels = [x["level"] for x in month(api, 2026, 10)["days"][2:10]]          # 3 Oct ... 10 Oct = 8 back ... 1 back
    assert levels[::-1] == [0, 1, 1, 2, 2, 3, 3, 4]


def test_no_year_and_month_means_this_month(fresh):
    d = data(fresh.api.screentime_month())
    assert (d["year"], d["month"], d["title"]) == (2026, 10, "October 2026") and d == data(fresh.api.screentime_month(None, None))


def test_month_edges_and_offset(tmp_path):
    ctx, api, db = blank(tmp_path)
    assert month(api, 2026, 2)["offset"] == 6 and len(month(api, 2026, 2)["days"]) == 28       # 1 Feb 2026 is a Sunday
    assert len(month(api, 2024, 2)["days"]) == 29
    assert month(api, 2026, 12)["title"] == "December 2026" and len(month(api, 2026, 12)["days"]) == 31
    assert month(api, 2025, 12)["can_next"] is True


@pytest.mark.parametrize("args", [(2026, 0), (2026, 13), ("2026", 5), (2026, "5"), (None, 5), (True, 5), (1999, 5), (2026, 5.5), (99999, 1)])
def test_a_bad_month_is_refused(synth, args):
    reply = synth.api.screentime_month(*args)
    assert not reply["ok"] and "Bad request" in reply["error"]


def test_the_month_reads_no_minute_rows_of_the_month_when_the_days_are_kept(fresh, monkeypatch):
    month(fresh.api, 2026, 9)
    seen = []
    orig = st.stats.daily_active
    monkeypatch.setattr(st.stats, "daily_active", lambda db, a, b: seen.append((a, b)) or orig(db, a, b))
    month(fresh.api, 2026, 9)
    assert seen == []                                                         # a finished month is all cache
    month(fresh.api, 2026, 10)
    assert seen and all(a >= date(2026, 10, 1) for a, b in seen)              # this month: only the days not kept (today)
    month(fresh.api, 2026, 10)
    assert seen[-1] == (TODAY, TODAY + timedelta(days=1))


# ---------------------------------------------------------------- Calendar: a day

def day(api, d):
    return data(api.screentime_day(d))


def test_the_day_lists_the_top_10_apps_sorted(tmp_path):
    ctx, api, db = blank(tmp_path)
    d0 = TODAY - timedelta(days=3)
    for i in range(14):
        for m in range(2 + i):                                         # app 0 gets 2 minutes ... app 13 gets 15
            put(db, d0, f"{m // 60 + 8:02d}:{m % 60:02d}", f"app{i:02d}.exe")
    put(db, d0, "20:00", "tiny.exe", seconds=30, active=30)
    d = day(api, d0.isoformat())
    assert [r["key"] for r in d["rows"]] == [f"app{i:02d}.exe" for i in range(13, 3, -1)]
    secs = [r["sec"] for r in d["rows"]]
    assert secs == sorted(secs, reverse=True) and len(d["rows"]) == 10
    total = sum(2 + i for i in range(14)) * 60 + 30
    assert d["note"] == f"{stats.hm(total)} active" and d["title"] == f"{st.DAY_NAMES[d0.weekday()]} {d0.day:02d} Oct 2026"
    assert d["rows"][0]["time"] == "15 m" and d["rows"][0]["name"] == "App13"


def test_the_day_matches_app_totals_on_the_synthetic_data(fresh):
    d0 = TODAY - timedelta(days=5)
    d = day(fresh.api, d0.isoformat())
    want = [(e, s) for e, s in stats.app_totals(fresh.ctx.db, d0, d0 + timedelta(days=1)).most_common(10) if s >= 60]
    assert [(r["key"], r["sec"]) for r in d["rows"]] == want and want


def test_a_day_with_nothing_and_a_rolled_up_day(tmp_path):
    ctx, api, db = blank(tmp_path)
    d = day(api, (TODAY - timedelta(days=4)).isoformat())
    assert d["note"] == "nothing recorded" and d["rows"] == []
    old_day = TODAY - timedelta(days=100)
    for m in range(5):
        put(db, old_day, f"09:0{m}", "code.exe")
    roll_up(db)
    d = day(api, old_day.isoformat())
    assert d["note"] == "5 m active" and [(r["key"], r["time"]) for r in d["rows"]] == [("code.exe", "5 m")]


@pytest.mark.parametrize("arg", ["", "yesterday", "2026-13-01", None, 5, "2026-10-1x"])
def test_a_bad_day_is_refused(synth, arg):
    reply = synth.api.screentime_day(arg)
    assert not reply["ok"] and "Bad request" in reply["error"]


def test_the_new_methods_are_reads_and_have_sample_arguments():
    from resident import selftest
    assert "screentime_month" in selftest.READ_ARGS and "screentime_day" in selftest.READ_ARGS


# ---------------------------------------------------------------- speed on the 2-year database after retention

@pytest.fixture(scope="module")
def big(tmp_path_factory):
    path = tmp_path_factory.mktemp("st730") / "t.db"
    Database(path).close()
    build(path, TODAY, days=730, extra_items=40)
    db = Database(path)
    retention.run(db, TODAY)
    db.close()
    ctx, api = make(path)
    return type("Big", (), {"ctx": ctx, "api": api})()


def p95(values):
    s = sorted(values)
    return s[max(0, int(-(-95 * len(s) // 100)) - 1)]


CALLS = [("screentime_get", ("switches", r)) for r in RANGES] + [
    ("screentime_month", (2026, 10)), ("screentime_month", (2026, 9)), ("screentime_month", (2025, 3)),
    ("screentime_day", ("2026-10-11",)), ("screentime_day", ("2026-10-02",)), ("screentime_day", ("2025-03-05",))]


@pytest.mark.parametrize("name,args", CALLS, ids=[f"{n}-{'-'.join(map(str, a))}" for n, a in CALLS])
def test_the_new_reads_answer_fast_on_730_days(big, name, args):
    """Target 50 ms at the 95th percentile; the assert allows 100 for a slow, busy box."""
    st.forget(big.ctx)
    t = time.perf_counter()
    assert getattr(big.api, name)(*args)["ok"]
    cold = (time.perf_counter() - t) * 1000
    samples = []
    for _ in range(20):
        t = time.perf_counter()
        getattr(big.api, name)(*args)
        samples.append((time.perf_counter() - t) * 1000)
    print(f"\n{name} {args} cold {cold:6.1f} ms  p50 {statistics.median(samples):5.2f}  p95 {p95(samples):5.2f}  max {max(samples):5.2f}")
    assert p95(samples) <= 100, samples
    assert cold <= 300
