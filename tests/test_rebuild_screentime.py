"""R14: the Api behind Screen Time's Overview, Apps and Websites tabs (webapi/screentime.py).

What is checked: every range's numbers equal what the old window's `stats` functions give on the same rows (totals,
sessions with the 5-minute gap - also across midnight, the longest focus, the heatmap, the categories split, the
timeline, the trend, the top-40 / 60 s tables and their switch counts); the Calendar tab reads no range
activity at all; a finished day is read once and kept; the trend notes; the start-up answer; the CSV export; and that
every tab x range answers in well under 50 ms on a 730-day database after retention.
"""
import random
import statistics
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta

import pytest

import retention
import stats
import webapi
from db import Database
from synthdata import build
from webapi import core
from webapi import screentime as st

NOW = datetime(2026, 10, 11, 15, 42, 17)        # = tests/synthdata's "now"
TODAY = NOW.date()
RANGES = list(stats.RANGES)
TABS = ["overview", "apps", "websites", "calendar"]


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


def get(api, tab, rng="Today"):
    return data(api.screentime_get(tab, rng))


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    """60 days of synthetic history (the detail tables only), shared by the read-only tests."""
    path = tmp_path_factory.mktemp("st60") / "t.db"
    Database(path).close()
    build(path, TODAY, days=60)
    ctx, api = make(path)
    return type("Synth", (), {"path": path, "ctx": ctx, "api": api})()


@pytest.fixture
def fresh(synth):
    """The shared database with an empty cache, so a test sees a cold start."""
    st.forget(synth.ctx)
    return synth


def blank(tmp_path, now=NOW):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "b.db"
    Database(path).close()
    ctx, api = make(path, now)
    return ctx, api, ctx.db


def hhmm(t):
    return f"{t // 60:02d}:{t % 60:02d}"


# ---------------------------------------------------------------- the old numbers, from stats

def old(db, rng):
    """What gui/screen_time.py computed for a range, from stats on the minute rows of the whole range."""
    start, end = stats.range_dates(rng, TODAY)
    rows = stats.activity(db, start, end)
    minutes = stats.minute_summary(rows)
    return rows, stats.totals(rows), stats.sessions(rows, minutes), stats.longest_focus(rows, minutes)


def card(d, key):
    return next(c for c in d["cards"] if c["key"] == key)


# ---------------------------------------------------------------- the methods

def test_the_methods_are_registered_with_the_right_kind():
    kinds = dict(webapi.api_methods())
    assert kinds["screentime_get"] == "read" and kinds["screentime_export_csv"] == "ui"


def test_bad_arguments_are_refused(fresh):
    for args in (("nope", "Today"), (5, "Today"), ("overview", "Last year"), ("overview", ["Today"]), (None, 5)):
        reply = fresh.api.screentime_get(*args)
        assert not reply["ok"], args
    assert get(fresh.api, "Overview", "Today")["tab"] == "overview"        # (any case)
    assert fresh.api.screentime_get("switches", "Today")["ok"]                   # (R15 added it)


def test_a_fresh_database_says_empty(tmp_path):
    ctx, api, _db = blank(tmp_path)
    for tab in TABS:
        d = get(api, tab)
        assert d["empty"] is True and d["tab"] == tab and "cards" not in d and "rows" not in d


# ---------------------------------------------------------------- Overview: the same numbers as stats

@pytest.mark.parametrize("rng", RANGES)
def test_overview_numbers_equal_stats(fresh, rng):
    rows, (active, total), sessions, focus = old(fresh.ctx.db, rng)
    d = get(fresh.api, "overview", rng)
    assert d["range"] == rng and d["empty"] is False
    assert card(d, "active") == {"key": "active", "label": "Active", "value": stats.hm(active),
                                 "sub": f"of {stats.hm(total)} at the PC"}
    assert card(d, "idle")["value"] == stats.hm(total - active)
    assert card(d, "idle")["sub"] == "screen on, no input"
    assert card(d, "sessions")["value"] == str(len(sessions))
    avg = sum((e - s).total_seconds() for s, e in sessions) / len(sessions)
    assert card(d, "sessions")["sub"] == f"avg {stats.hm(avg)} each"
    sec, exe, start = focus
    assert card(d, "focus")["value"] == stats.hm(sec)
    assert card(d, "focus")["sub"].endswith(f", {start:%H:%M}")


@pytest.mark.parametrize("rng", RANGES)
def test_heatmap_equals_stats(fresh, rng):
    db = fresh.ctx.db
    week = [TODAY - timedelta(days=6 - i) for i in range(7)]
    rows = stats.activity(db, week[0], TODAY + timedelta(days=1))
    expect = stats.hourly_minutes(rows, week)
    heat = get(fresh.api, "overview", rng)["heat"]["rows"]
    assert [r["label"] for r in heat] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][week[0].weekday():] + \
        ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][:week[0].weekday()]
    for got, want in zip(heat, expect):
        assert got["minutes"] == [round(m, 2) for m in want]


@pytest.mark.parametrize("rng", RANGES)
def test_categories_split_equals_the_old_one(fresh, rng):
    db = fresh.ctx.db
    rows, (active, _total), _s, _f = old(db, rng)
    items, saved = db.list_items(), db.categories()
    split = Counter()
    for r in rows:
        if r["active"]:
            kind, name = ("site", r["site"]) if r["site"] else ("app", r["exe"])
            split[stats.category_of(kind, name, saved, items)] += r["active"]
    donut = get(fresh.api, "overview", rng)["donut"]
    got = {p["key"]: p["value"] for p in donut["parts"]}
    assert {k: v for k, v in got.items() if v} == {k: v for k, v in split.items() if v}
    assert sum(got.values()) == active
    assert donut["center"] == (stats.hm(active).replace(" h ", "h").replace(" m", "") if active else "0")
    assert [p["key"] for p in donut["parts"]][:4] == ["productive", "neutral", "games", "distracting"]


def test_the_timeline_is_for_today_and_yesterday_only(fresh):
    db = fresh.ctx.db
    for rng, day in (("Today", TODAY), ("Yesterday", TODAY - timedelta(days=1))):
        d = get(fresh.api, "overview", rng)["timeline"]
        rows = stats.activity(db, day, day + timedelta(days=1))
        saved, items = db.categories(), db.list_items()
        want = stats.timeline(rows, day, lambda e, s: stats.category_of(*(("site", s) if s else ("app", e)), saved, items))
        got = [(g["start"], g["len"], g["kind"]) for g in d["segments"]]
        if len(want) <= st.MAX_SEGMENTS:
            assert got == want
        else:                                         # (a very broken-up day: the longest ones, in order)
            assert len(got) == st.MAX_SEGMENTS and got == sorted(got) and set(got) <= set(want)
        assert d["start_hour"] == min(6, int(rows[0]["minute"][11:13]))
        assert {"idle", "productive"} <= set(d["names"]) and d["legend"][-1]["key"] == "idle"
    assert get(fresh.api, "overview", "7 days")["timeline"] is None
    assert get(fresh.api, "overview", "30 days")["timeline"] is None


@pytest.mark.parametrize("rng", RANGES)
def test_trend_equals_the_daily_totals(fresh, rng):
    db = fresh.ctx.db
    span = 7 if rng == "7 days" else 30
    d = get(fresh.api, "overview", rng)
    daily = stats.daily_active(db, TODAY - timedelta(days=29), TODAY + timedelta(days=1))
    days = [TODAY - timedelta(days=span - 1 - i) for i in range(span)]
    vals = [daily.get(x.isoformat(), 0) for x in days]
    trend = d["trend"]
    assert trend["title"] == f"Trend - last {span} days"
    assert [p["value"] for p in trend["points"]] == [round(v / 60, 1) for v in vals]
    assert [p["avg"] for p in trend["points"]] == [round(sum(vals[max(0, i - 6):i + 1]) / len(vals[max(0, i - 6):i + 1]) / 60, 1)
                                                   for i in range(span)]
    assert [p["label"] for p in trend["points"]] == [str(x.day) for x in days]
    assert [p["date"] for p in trend["points"]] == [x.isoformat() for x in days]
    assert [p["weekday"] for p in trend["points"]] == [x.weekday() for x in days]
    assert trend["goal"] == 300.0 and trend["goal_label"] == "5 h"
    assert "bars" not in d                                  # (the "Last 7 days" card is gone)


def test_unlock_days_are_marked_on_the_trend(fresh):
    d = get(fresh.api, "overview", "30 days")
    unlock_days = {3, 17}                                   # synthdata: unlocks 3, 17, 40 and 55 days back
    marked = {TODAY - timedelta(days=i) for i in range(30) if d["trend"]["points"][29 - i]["unlocks"]}
    assert marked == {TODAY - timedelta(days=i) for i in unlock_days}


def tips(d):
    """{iso date: the tip's lines} of a reply's trend."""
    return {p["date"]: p["tip"].split("\n") for p in d["trend"]["points"]}


def minutes_on(db, day, minutes):
    for m in range(minutes):
        put(db, day, f"{6 + m // 60:02d}:{m % 60:02d}")


def test_the_trend_tooltip_has_the_date_the_usage_and_the_week_average_so_far(tmp_path):
    """Sunday 11 Oct is today and the 30 days begin on Sat 12 Sep. Recording started on Wed 9 Sep - before the window - so
    the average of Sat 12 Sep needs the three days before it, and counts 9..12 Sep only (the days before the first recorded
    one don't count; a recorded day with nothing on it, Fri 11 Sep, counts as 0)."""
    ctx, api, db = blank(tmp_path)
    for day, minutes in ((date(2026, 9, 9), 60), (date(2026, 9, 10), 120), (date(2026, 9, 12), 180),
                         (date(2026, 9, 14), 30), (date(2026, 9, 15), 150), (date(2026, 9, 16), 60),
                         (date(2026, 10, 5), 60), (date(2026, 10, 7), 120), (date(2026, 10, 11), 30)):
        minutes_on(db, day, minutes)
    t = tips(get(api, "overview", "30 days"))
    assert t["2026-09-12"] == ["Sat, 12 Sep", "Screen time 3 h 00 m", "Week average 1 h 30 m"]       # 360 min over 4 days
    assert t["2026-09-13"] == ["Sun, 13 Sep", "Screen time 0 m", "Week average 1 h 12 m"]           # 360 min over 5 days
    assert t["2026-09-14"] == ["Mon, 14 Sep", "Screen time 30 m", "Week average 30 m"]              # a new week starts over
    assert t["2026-09-15"][2] == "Week average 1 h 30 m" and t["2026-09-16"][2] == "Week average 1 h 20 m"
    assert t["2026-10-06"] == ["Tue, 6 Oct", "Screen time 0 m", "Week average 30 m"]
    assert t["2026-10-07"][2] == "Week average 1 h 00 m"
    assert t["2026-10-11"] == ["Sun, 11 Oct", "Screen time 30 m so far", "Week average 30 m"]       # today: so far
    assert get(api, "overview", "7 days")["trend"]["points"][-1]["tip"] == "\n".join(t["2026-10-11"])


def test_the_week_average_is_left_out_for_days_before_anything_was_recorded(tmp_path):
    ctx, api, db = blank(tmp_path)
    minutes_on(db, date(2026, 10, 10), 40)                  # Sat: the first day with data
    minutes_on(db, TODAY, 20)
    t = tips(get(api, "overview", "7 days"))
    assert t["2026-10-05"] == ["Mon, 5 Oct", "Screen time 0 m"] and t["2026-10-09"] == ["Fri, 9 Oct", "Screen time 0 m"]
    assert t["2026-10-10"] == ["Sat, 10 Oct", "Screen time 40 m", "Week average 40 m"]
    assert t["2026-10-11"] == ["Sun, 11 Oct", "Screen time 20 m so far", "Week average 30 m"]


def test_the_trend_tooltip_lists_every_emergency_unlock_that_started_that_day(tmp_path):
    ctx, api, db = blank(tmp_path)
    minutes_on(db, date(2026, 10, 1), 10)
    at = lambda day, hh, mm: datetime(2026, 10, day, hh, mm)
    db.add_unlock([1], ["x"], at(8, 14, 5), at(8, 14, 25))
    db.add_unlock([1], ["x"], at(8, 9, 0), at(8, 9, 20))                # (added later, but it is listed first)
    db.add_unlock([1], ["x"], at(9, 23, 50), at(10, 0, 20))             # past midnight: shows its real end
    d = get(api, "overview", "7 days")
    t = tips(d)
    assert t["2026-10-08"][3:] == ["Emergency unlock 09:00–09:20", "Emergency unlock 14:05–14:25"]
    assert t["2026-10-09"][3:] == ["Emergency unlock 23:50–00:20"]
    assert t["2026-10-10"] == ["Sat, 10 Oct", "Screen time 0 m", "Week average 0 m"]       # (it started the day before)
    assert [p["unlocks"] for p in d["trend"]["points"]] == [0, 0, 0, 2, 1, 0, 0]
    assert "Emergency unlock" not in "".join(t["2026-10-07"])
    

def test_the_trend_reads_36_days_of_totals_in_one_go(tmp_path, monkeypatch):
    """The first Monday-to-day average of the 30 days needs up to 6 days before them: one read of 36 days, not two."""
    ctx, api, db = blank(tmp_path)
    minutes_on(db, TODAY - timedelta(days=33), 10)
    calls = []
    real = stats.daily_active
    monkeypatch.setattr(stats, "daily_active", lambda *a, **k: calls.append(a[1:]) or real(*a, **k))
    t = tips(get(api, "overview", "30 days"))
    assert calls.count((TODAY - timedelta(days=35), TODAY + timedelta(days=1))) == 1
    assert t["2026-09-12"][-1] == "Week average 2 m"        # (Sat 12 Sep: 10 min on Tue 8 Sep, before the window, over 8..12 Sep)


def week_db(tmp_path, this_week_min, last_week_min, goal="5"):
    """A database with `this_week_min` active minutes on each of the last 7 days and `last_week_min` on the 7 before."""
    ctx, api, db = blank(tmp_path)
    db.set_setting("stats.goal_hours", goal)
    for back in range(14):
        day = TODAY - timedelta(days=back)
        for m in range(this_week_min if back < 7 else last_week_min):
            db.add_activity(f"{day} {(6 + m // 60):02d}:{m % 60:02d}", "code.exe", "", 60, 60)
    return ctx, api, db


def test_trend_note_words_exactly(tmp_path):
    """Exact words for a clean week: 7 whole days each, so the percentages are round."""
    ctx, api, db = blank(tmp_path, datetime(2026, 10, 11, 23, 59, 0))
    day0 = date(2026, 10, 11)

    def fill(minutes_per_day, first_back, days=7):
        for back in range(first_back, first_back + days):
            d = day0 - timedelta(days=back)
            for m in range(minutes_per_day):
                db.add_activity(f"{d} {(6 + m // 60):02d}:{m % 60:02d}", "code.exe", "", 60, 60)
    fill(120, 0)
    fill(150, 7)
    trend = data(api.screentime_get("overview", "30 days"))["trend"]
    assert trend["note"] == "▼ 20% vs last week · avg 2 h 00 m a day" and trend["tone"] == "ok"
    trend = data(api.screentime_get("overview", "7 days"))["trend"]
    assert trend["note"] == "▼ 20% vs last week · avg 2 h 00 m a day"           # (the 7-day trend compares as well)
    fill(30, 14)                                                               # nothing changes: older than 14 days
    st.forget(ctx)
    # more this week: up
    for back in range(7):
        d = day0 - timedelta(days=back)
        for m in range(120, 165):
            db.add_activity(f"{d} {(6 + m // 60):02d}:{m % 60:02d}", "code.exe", "", 60, 60)
    st.forget(ctx)
    trend = data(api.screentime_get("overview", "30 days"))["trend"]
    assert trend["note"] == "▲ 10% vs last week · avg 2 h 45 m a day" and trend["tone"] == "bad"


def test_trend_note_without_a_previous_week_and_when_equal(tmp_path):
    ctx, api, db = blank(tmp_path, datetime(2026, 10, 11, 23, 59, 0))
    day0 = date(2026, 10, 11)
    for back in range(7):
        d = day0 - timedelta(days=back)
        for m in range(90):
            db.add_activity(f"{d} {(6 + m // 60):02d}:{m % 60:02d}", "code.exe", "", 60, 60)
    trend = data(api.screentime_get("overview", "30 days"))["trend"]
    assert trend["note"] == "avg 1 h 30 m a day" and trend["tone"] == "muted"
    for back in range(7, 14):
        d = day0 - timedelta(days=back)
        for m in range(90):
            db.add_activity(f"{d} {(6 + m // 60):02d}:{m % 60:02d}", "code.exe", "", 60, 60)
    st.forget(ctx)
    trend = data(api.screentime_get("overview", "30 days"))["trend"]
    assert trend["note"] == "about the same as last week · avg 1 h 30 m a day" and trend["tone"] == "muted"


def test_no_goal_means_no_goal_line(tmp_path):
    ctx, api, db = week_db(tmp_path, 60, 60, goal="0")
    d = get(api, "overview", "7 days")
    assert d["trend"]["goal"] == 0 and d["trend"]["goal_label"] == ""
    assert [s["key"] for s in d["streaks"]] == ["no_unlock"]


def test_streaks(fresh):
    d = get(fresh.api, "overview", "Today")
    want = stats.streaks(fresh.ctx.db, TODAY, 5 * 3600)
    by = {s["key"]: s for s in d["streaks"]}
    n = want["no_unlock"]
    assert by["no_unlock"]["text"] == (f"{n} day{'s' * (n != 1)} without an emergency unlock" if n else "Emergency unlock used today")
    n = want["goal"]
    assert by["goal"]["text"] == (f"{n} day{'s' * (n != 1)} within your goal" if n else "Over your goal today")
    assert by["goal"]["sub"] == "daily goal 5 h"


def streak_numbers(d):
    """(goal run or None, no-unlock run) from the Overview's two streak rows."""
    by = {x["key"]: x["text"] for x in d["streaks"]}
    num = lambda t: int(t.split()[0]) if t[0].isdigit() else 0
    return (num(by["goal"]) if "goal" in by else None), num(by["no_unlock"])


@pytest.mark.parametrize("today_minutes,unlock_back,goal", [(60, None, "2"), (200, None, "2"), (60, 0, "2"), (60, 3, "2"),
                                                            (60, None, "0"), (30, 9, "1"), (0, None, "2")])
def test_the_streaks_equal_stats_streaks_with_the_past_kept(tmp_path, today_minutes, unlock_back, goal):
    ctx, api, db = blank(tmp_path)
    db.set_setting(st.GOAL_KEY, goal)
    for back, minutes in enumerate([today_minutes, 90, 100, 150, 110, 30, 20, 130, 40], start=0):
        for m in range(minutes):
            put(db, TODAY - timedelta(days=back), f"{6 + m // 60:02d}:{m % 60:02d}")
    if unlock_back is not None:
        start = datetime.combine(TODAY - timedelta(days=unlock_back), datetime.min.time()) + timedelta(hours=8)
        db.add_unlock([1], ["x"], start, start + timedelta(hours=1))
    want = stats.streaks(db, TODAY, float(goal) * 3600 or None)
    for _ in range(2):                                                    # (the second answer comes from what was kept)
        assert streak_numbers(get(api, "overview", "Today")) == ((want["goal"] if float(goal) else None), want["no_unlock"])
        assert streak_numbers(get(api, "overview", "30 days")) == ((want["goal"] if float(goal) else None), want["no_unlock"])


def test_the_streaks_of_the_past_are_worked_out_once_a_day(tmp_path, monkeypatch):
    ctx, api, db = blank(tmp_path)
    db.set_setting(st.GOAL_KEY, "2")
    for back in range(0, 6):
        for m in range(60):
            put(db, TODAY - timedelta(days=back), f"{6 + m // 60:02d}:{m % 60:02d}")
    calls = []
    real, real_unlocks = stats.daily_active, db.unlocks_since
    monkeypatch.setattr(stats, "daily_active", lambda *a, **k: calls.append(a[1:]) or real(*a, **k))
    first = get(api, "overview", "Today")
    n = len(calls)
    assert any(c[1] == TODAY for c in calls[1:])                         # (the earlier days were looked up)
    for _ in range(3):
        again = get(api, "overview", "Today")
    assert again["streaks"] == first["streaks"]
    assert calls[n:] == [(TODAY, TODAY + timedelta(days=1))] * 3         # only the trend's totals incl. today, no streak look-up
    # an emergency unlock today shows at once; the kept past is not touched
    start = datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=8)
    db.add_unlock([1], ["x"], start, start + timedelta(hours=1))
    [row] = [x for x in get(api, "overview", "Today")["streaks"] if x["key"] == "no_unlock"]
    assert (row["text"], row["sub"]) == ("Emergency unlock used today", "08:00–09:00")
    db.add_unlock([1], ["x"], start + timedelta(hours=7), start + timedelta(hours=7, minutes=20))
    [row] = [x for x in get(api, "overview", "Today")["streaks"] if x["key"] == "no_unlock"]
    assert row["sub"] == "08:00–09:00, 15:00–15:20"                     # (every use of today, earliest first)
    # a change of the goal or more activity today is seen at once, too
    db.set_setting(st.GOAL_KEY, "0.5")
    assert [x["text"] for x in get(api, "overview", "Today")["streaks"] if x["key"] == "goal"] == ["Over your goal today"]
    db.set_setting(st.GOAL_KEY, "5")
    assert streak_numbers(get(api, "overview", "Today"))[0] == stats.streaks(db, TODAY, 5 * 3600)["goal"]


# ---------------------------------------------------------------- sessions and the longest focus, even over midnight

def put(db, day, hhmm, exe="code.exe", site="", seconds=60, active=60):
    db.add_activity(f"{day} {hhmm}", exe, site, seconds, active)


def old_sessions_focus(db, rng, today):
    start, end = stats.range_dates(rng, today)
    rows = stats.activity(db, start, end)
    minutes = stats.minute_summary(rows)
    return stats.sessions(rows, minutes), stats.longest_focus(rows, minutes)


def check_equal(api, db, rng, today=TODAY):
    sessions, focus = old_sessions_focus(db, rng, today)
    d = get(api, "overview", rng)
    assert card(d, "sessions")["value"] == str(len(sessions))
    if sessions:
        avg = sum((e - s).total_seconds() for s, e in sessions) / len(sessions)
        assert card(d, "sessions")["sub"] == f"avg {stats.hm(avg)} each"
    if focus:
        assert card(d, "focus")["value"] == stats.hm(focus[0])
        assert card(d, "focus")["sub"].endswith(f", {focus[2]:%H:%M}")
        return focus
    assert card(d, "focus")["value"] == "-"


def test_a_session_and_a_run_that_go_over_midnight_are_joined(tmp_path):
    now = datetime(2026, 10, 11, 12, 0, 0)
    ctx, api, db = blank(tmp_path, now)
    yesterday, two = date(2026, 10, 10), date(2026, 10, 9)
    for m in range(22 * 60 + 30, 24 * 60):                     # 22:30 - 23:59 in code.exe
        put(db, two, f"{m // 60:02d}:{m % 60:02d}")
    for m in range(0, 40):                                     # 00:00 - 00:39 on, the same app
        put(db, yesterday, f"{m // 60:02d}:{m % 60:02d}")
    for m in range(9 * 60, 9 * 60 + 20):                       # a short run in the morning
        put(db, yesterday, f"{m // 60:02d}:{m % 60:02d}", exe="chrome.exe", site="x.com")
    for m in range(9 * 60 + 23, 9 * 60 + 30):                  # 2 minutes' break only: the same session
        put(db, yesterday, f"{m // 60:02d}:{m % 60:02d}", exe="chrome.exe", site="x.com")
    for m in range(14 * 60, 14 * 60 + 5):                      # an hour later: a new session
        put(db, yesterday, f"{m // 60:02d}:{m % 60:02d}", exe="word.exe")
    focus = check_equal(api, db, "7 days", now.date())
    assert focus[0] == (90 + 40) * 60 and focus[1] == "code.exe"          # the run over midnight, not split in two
    check_equal(api, db, "30 days", now.date())
    check_equal(api, db, "Yesterday", now.date())


def test_a_run_that_fills_a_whole_day_goes_on_over_three_days(tmp_path):
    now = datetime(2026, 10, 11, 12, 0, 0)
    ctx, api, db = blank(tmp_path, now)
    for back, (lo, hi) in {3: (23 * 60, 1440), 2: (0, 1440), 1: (0, 30)}.items():
        for m in range(lo, hi):
            put(db, now.date() - timedelta(days=back), f"{m // 60:02d}:{m % 60:02d}", exe="game.exe")
    focus = check_equal(api, db, "7 days", now.date())
    assert focus[0] == (60 + 1440 + 30) * 60


def test_equal_runs_keep_the_earliest_and_a_gap_breaks_a_run(tmp_path):
    now = datetime(2026, 10, 11, 12, 0, 0)
    ctx, api, db = blank(tmp_path, now)
    d = now.date() - timedelta(days=1)
    for m in range(60, 90):
        put(db, d, f"{m // 60:02d}:{m % 60:02d}", exe="a.exe")
    for m in range(120, 150):                                  # the same length, later
        put(db, d, f"{m // 60:02d}:{m % 60:02d}", exe="b.exe")
    for m in range(200, 230):                                  # one idle minute in the middle splits it
        put(db, d, f"{m // 60:02d}:{m % 60:02d}", exe="c.exe", active=0 if m == 215 else 60)
    put(db, d, "04:00", exe="d.exe", active=60)
    assert check_equal(api, db, "7 days", now.date())[1] == "a.exe"
    # a minute with two apps: the one with more active seconds is "the app"; equal -> the first by name
    put(db, d, "05:00", exe="z.exe", seconds=60, active=30)
    put(db, d, "05:00", exe="m.exe", seconds=60, active=30)
    put(db, d, "05:01", exe="m.exe", seconds=60, active=30)
    put(db, d, "05:01", exe="z.exe", seconds=60, active=40)
    st.forget(ctx)
    check_equal(api, db, "7 days", now.date())


def test_sessions_and_focus_equal_stats_on_random_days(tmp_path):
    rnd = random.Random(7)
    now = datetime(2026, 10, 11, 12, 0, 0)
    ctx, api, db = blank(tmp_path, now)
    apps = ["a.exe", "b.exe", "c.exe"]
    for back in range(1, 9):
        day = now.date() - timedelta(days=back)
        t = rnd.choice([0, 300, 1380])
        exe = rnd.choice(apps)
        for _ in range(rnd.randint(300, 900)):
            if t >= 1440:
                break
            if rnd.random() < 0.1:
                exe = rnd.choice(apps)
            gap = rnd.choice([0, 0, 0, 0, 1, 4, 5, 6, 12])
            active = rnd.choice([60, 60, 60, 0, 20])
            put(db, day, hhmm(t), exe=exe, active=active)
            if rnd.random() < 0.2:
                put(db, day, hhmm(t), exe=rnd.choice(apps), seconds=30, active=rnd.choice([30, 0, 61]))
            t += 1 + gap
    for rng in RANGES:
        check_equal(api, db, rng, now.date())


# ---------------------------------------------------------------- Apps and Websites

def tables_db(tmp_path):
    ctx, api, db = blank(tmp_path)
    day = TODAY - timedelta(days=1)
    for i in range(45):                                         # 45 apps, app i has i+1 minutes of use
        for m in range(i + 1):
            put(db, day, f"{(i % 20) + 1:02d}:{m:02d}", exe=f"app{i:02d}.exe")
    put(db, day, "23:00", exe="short.exe", seconds=59, active=59)             # 59 s: under the minute
    for k in range(3):
        db.add_switch(datetime.combine(day, datetime.min.time()) + timedelta(hours=8, minutes=k), "app44.exe", "")
    return ctx, api, db


def test_apps_are_the_top_40_with_at_least_a_minute(tmp_path):
    ctx, api, db = tables_db(tmp_path)
    d = get(api, "apps", "Yesterday")
    rows = d["rows"]
    assert d["kind"] == "app" and len(rows) == st.MAX_ROWS == 40
    assert [r["key"] for r in rows] == [f"app{i:02d}.exe" for i in range(44, 4, -1)]      # most time first
    assert "short.exe" not in [r["key"] for r in rows]
    assert rows[0]["sec"] == 45 * 60 and rows[0]["share"] == 1.0
    assert rows[1]["share"] == round(44 / 45, 4)
    assert rows[0]["time"] == stats.hm(45 * 60) and rows[0]["sub"] == "app44.exe"
    assert rows[0]["count"] == 3 and rows[1]["count"] == 0                            # switches to it
    assert get(api, "apps", "Today")["rows"] == []                                    # nothing today
    # a short list: only those with 60 s
    ctx2, api2, db2 = blank(tmp_path / "x")
    put(db2, TODAY - timedelta(days=1), "10:00", exe="one.exe", seconds=59, active=59)
    assert get(api2, "apps", "Yesterday")["rows"] == []


def test_apps_equal_the_old_per_app_table_for_every_range(fresh):
    db = fresh.ctx.db
    for rng in RANGES:
        start, end = stats.range_dates(rng, TODAY)
        rows = stats.activity(db, start, end)
        events = stats.switches(db, start, end)
        times, counts = stats.per_app(rows), Counter(e["exe"] for e in events)
        want = [(n, s, counts.get(n, 0)) for n, s in times.most_common(40) if s >= 60]
        got = [(r["key"], r["sec"], r["count"]) for r in get(fresh.api, "apps", rng)["rows"]]
        assert got == want, rng


def test_websites_equal_the_old_per_site_table_for_every_range(fresh):
    db = fresh.ctx.db
    for rng in RANGES:
        start, end = stats.range_dates(rng, TODAY)
        rows = stats.activity(db, start, end)
        events = stats.switches(db, start, end)
        times, counts = stats.per_site(rows), Counter(e["site"] for e in events if e["site"])
        want = [(n, s, counts.get(n, 0)) for n, s in times.most_common(40) if s >= 60]
        d = get(fresh.api, "websites", rng)
        got = [(r["key"], r["sec"], r["count"]) for r in d["rows"]]
        assert got == want, rng
        assert d["kind"] == "site" and all(r["sub"] == "" and r["name"] == r["key"] for r in d["rows"])


def test_rows_carry_the_category_and_its_colour(tmp_path):
    ctx, api, db = blank(tmp_path)
    db.add_item("YouTube", ["youtube.com"], "site")
    db.add_item("Discord", ["discord.exe"], "app")
    day = TODAY - timedelta(days=1)
    for m in range(5):
        put(db, day, f"10:{m:02d}", exe="chrome.exe", site="www.youtube.com")
        put(db, day, f"11:{m:02d}", exe="discord.exe")
        put(db, day, f"12:{m:02d}", exe="code.exe")
        put(db, day, f"13:{m:02d}", exe="chrome.exe", site="docs.python.org")
    db.set_category("app", "code.exe", "productive")
    db.set_category("site", "docs.python.org", "productive")
    apps = {r["key"]: r for r in get(api, "apps", "Yesterday")["rows"]}
    assert apps["code.exe"]["cat"] == "productive" and apps["code.exe"]["cat_name"] == "Productive"
    assert apps["code.exe"]["color"] == "var(--cat-prod)"
    assert apps["discord.exe"]["cat"] == "distracting" and apps["discord.exe"]["color"] == "var(--cat-dist)"      # on the blocklist
    assert apps["chrome.exe"]["cat"] == "neutral"
    sites = {r["key"]: r for r in get(api, "websites", "Yesterday")["rows"]}
    assert sites["www.youtube.com"]["cat"] == "distracting"                      # a subdomain of a blocked site
    assert sites["docs.python.org"]["cat"] == "productive"
    # an own category gets its own colour; one that was deleted falls back to neutral
    from webapi import categories
    key = categories._add(db, "Study", "#2f9fd8")
    db.set_category("app", "code.exe", key)
    db.set_category("app", "discord.exe", "gone")
    apps = {r["key"]: r for r in get(api, "apps", "Yesterday")["rows"]}
    assert (apps["code.exe"]["cat_name"], apps["code.exe"]["color"]) == ("Study", "#2f9fd8")
    assert apps["discord.exe"]["cat"] == "neutral"


def test_the_category_lookup_gives_what_stats_category_of_gives(fresh):
    db = fresh.ctx.db
    cats = st._Cats(db)
    names = [("app", n) for n in ("code.exe", "discord.exe", "steam.exe", "game.exe", "x.exe")] + \
            [("site", n) for n in ("youtube.com", "m.youtube.com", "www.reddit.com", "reddit.com", "old.reddit.com", "github.com",
                                   "notyoutube.com", "twitch.tv", "x.com", "eyoutube.com.evil", "cdn.x.com")]
    db.set_category("site", "github.com", "productive")
    cats = st._Cats(db)
    for kind, name in names:
        want = stats.category_of(kind, name, db.categories(), db.list_items())
        assert cats.category(kind, name) == (want if want in cats.keys else "neutral"), (kind, name)


def test_names_blocklist_first_then_the_catalog_then_the_exe(tmp_path):
    from resident.catalog import AppCatalog
    ctx, api, db = blank(tmp_path)
    ctx.catalog = AppCatalog(cache_path=tmp_path / "c.json", running_now=lambda: {}, list_apps=lambda: [
        {"name": "Visual Studio Code", "exe": "code.exe", "path": "C:\\VS\\code.exe", "steam": False},
        {"name": "Catalog Name", "exe": "mine.exe", "path": "C:\\cat\\mine.exe", "steam": False}])
    ctx.catalog.scan_now()
    db.add_item("My Own Name", ["mine.exe"], "app", app_path="D:\\mine\\mine.exe")
    day = TODAY - timedelta(days=1)
    for exe in ("code.exe", "mine.exe", "pythonw.exe", "unknown.exe", "chrome.exe"):
        for m in range(3):
            put(db, day, f"10:{m:02d}", exe=exe)
    rows = {r["key"]: r for r in get(api, "apps", "Yesterday")["rows"]}
    assert rows["code.exe"]["name"] == "Visual Studio Code" and rows["code.exe"]["path"] == "C:\\VS\\code.exe"
    assert rows["mine.exe"]["name"] == "My Own Name" and rows["mine.exe"]["path"] == "D:\\mine\\mine.exe"
    assert rows["pythonw.exe"]["name"] == "Lockdown" and rows["pythonw.exe"]["path"] is None
    assert rows["unknown.exe"]["name"] == "Unknown" and rows["chrome.exe"]["name"] == "Chrome"
    # the longest focus on the Overview speaks the same names
    assert get(api, "overview", "Yesterday")["cards"][2]["sub"].split(",")[0] in {r["name"] for r in rows.values()}


# ---------------------------------------------------------------- the Calendar and what is read

def sql_log(db):
    log = []
    db.conn.set_trace_callback(log.append)
    return log


def test_the_calendar_tab_reads_no_range_activity(tmp_path, monkeypatch):
    ctx, api, db = blank(tmp_path)
    put(db, TODAY, "10:00")
    calls = []
    monkeypatch.setattr(stats, "activity", lambda *a, **k: calls.append("stats.activity") or [])
    monkeypatch.setattr(stats, "switches", lambda *a, **k: calls.append("stats.switches") or [])
    monkeypatch.setattr(stats, "daily_active", lambda *a, **k: calls.append("stats.daily_active") or Counter())
    monkeypatch.setattr(st, "_activity_rows", lambda *a, **k: calls.append("rows") or iter(()))
    log = sql_log(ctx.db)
    for rng in RANGES:
        d = get(api, "calendar", rng)
        assert d["tab"] == "calendar"
    assert calls == []
    assert not [q for q in log if "FROM activity" in q and "MIN(minute)" not in q and "first_minute" not in q], log
    assert not [q for q in log if "switch_events" in q]


def test_each_tab_reads_only_what_it_needs(fresh):
    db = fresh.ctx.db
    st.forget(fresh.ctx)
    log = sql_log(db)
    get(fresh.api, "apps", "Today")
    assert any("FROM switch_events" in q for q in log)
    log.clear()
    get(fresh.api, "overview", "30 days")
    assert not any("switch_events" in q for q in log)                  # the Overview doesn't count switches
    db.conn.set_trace_callback(None)


def test_a_finished_day_is_read_once_and_kept(tmp_path, monkeypatch):
    ctx, api, db = blank(tmp_path)
    for back in range(1, 12):
        for m in range(30):
            put(db, TODAY - timedelta(days=back), f"10:{m:02d}")
    put(db, TODAY, "09:00")
    spans = []
    real = st._activity_rows
    monkeypatch.setattr(st, "_activity_rows", lambda db_, a, b: spans.append((a, b)) or real(db_, a, b))
    first = get(api, "overview", "30 days")
    assert len(spans) == 1                                             # one query for everything that was missing
    spans.clear()
    again = get(api, "overview", "30 days")
    assert [(a, b) for a, b in spans] == [(TODAY, TODAY + timedelta(days=1))]       # only today is read again
    assert card(again, "active") == card(first, "active")
    # something written to today shows at once
    put(db, TODAY, "09:01")
    assert get(api, "overview", "Today")["cards"][0]["value"] == "2 m"
    # a day that was over keeps its cached numbers (they can't change), until the cache is dropped
    put(db, TODAY - timedelta(days=1), "20:00")
    assert get(api, "overview", "Yesterday")["cards"][0]["value"] == "30 m"
    st.forget(ctx)
    assert get(api, "overview", "Yesterday")["cards"][0]["value"] == "31 m"


def test_a_finished_day_never_expires_by_the_clock(tmp_path, monkeypatch):
    """The cache used to forget every finished day 15 minutes after it was made - all of them at once, so the shown page
    worked out 29 days in one refresh. Now only data drops a day (forget / retention), however long it has been."""
    ctx, api, db = blank(tmp_path)
    for back in range(1, 10):
        put(db, TODAY - timedelta(days=back), "10:00")
    put(db, TODAY, "09:00")
    get(api, "overview", "30 days")
    spans = []
    real = st._activity_rows
    monkeypatch.setattr(st, "_activity_rows", lambda db_, a, b: spans.append((a, b)) or real(db_, a, b))
    later = time.monotonic() + 86400 * 3
    monkeypatch.setattr(st.time, "monotonic", lambda: later)
    for _ in range(3):
        get(api, "overview", "30 days")
        get(api, "apps", "7 days")
    assert spans and all((a, b) == (TODAY, TODAY + timedelta(days=1)) for a, b in spans)       # only today, every time


def test_a_day_that_ended_minutes_ago_is_not_kept_yet(tmp_path):
    """The tracker writes in batches: just after midnight yesterday still gets its last minute. Until SETTLE_SEC has passed
    the day is worked out each time, then it is kept."""
    clock = [datetime(2026, 10, 12, 0, 1, 0)]
    path = tmp_path / "late.db"
    Database(path).close()
    ctx = webapi.make_context(path, clock=lambda db: clock[0])
    api, db = core.build_api(ctx), ctx.db
    yesterday = date(2026, 10, 11)
    put(db, yesterday, "23:58")
    put(db, yesterday, "23:59")
    assert get(api, "overview", "Yesterday")["cards"][0]["value"] == "2 m"
    put(db, yesterday, "23:59", exe="chrome.exe")                         # (the tracker's last batch lands)
    assert get(api, "overview", "Yesterday")["cards"][0]["value"] == "3 m"
    assert get(api, "overview", "7 days")["trend"]["points"][-2]["value"] == 3.0
    assert yesterday not in ctx.st_cache.days and yesterday not in ctx.st_cache.totals and ctx.st_cache.streaks == {}
    clock[0] = datetime(2026, 10, 12, 0, 3, 1)                            # three minutes later: it is over for good
    assert get(api, "overview", "Yesterday")["cards"][0]["value"] == "3 m"
    assert yesterday in ctx.st_cache.days and yesterday in ctx.st_cache.totals and ctx.st_cache.streaks
    put(db, yesterday, "20:00")                                           # (not seen any more: kept)
    assert get(api, "overview", "Yesterday")["cards"][0]["value"] == "3 m"


def test_the_kept_days_have_a_cap(tmp_path, monkeypatch):
    ctx, api, db = blank(tmp_path)
    for back in range(0, 20):
        put(db, TODAY - timedelta(days=back), "10:00")
    monkeypatch.setattr(st, "MAX_DIGESTS", 5)
    monkeypatch.setattr(st, "MAX_TOTALS", 8)
    get(api, "overview", "30 days")
    assert sorted(ctx.st_cache.days) == [TODAY - timedelta(days=i) for i in (5, 4, 3, 2, 1)]       # the newest
    assert len(ctx.st_cache.totals) == 8 and max(ctx.st_cache.totals) == TODAY - timedelta(days=1)
    assert get(api, "overview", "30 days")["cards"][0]["value"] == "20 m"                          # (and it is still right)


def test_a_roll_up_by_retention_drops_what_is_kept(tmp_path):
    """retention.roll_up_day rewrites a day's rows into the daily totals; the kept digests are dropped by it (no explicit
    forget here) and what the page shows stays the same."""
    ctx, api, db = blank(tmp_path)
    day = TODAY - timedelta(days=3)
    for m in range(10):
        put(db, day, f"10:{m:02d}", exe="code.exe")
    put(db, TODAY, "08:00")
    before = get(api, "overview", "7 days")
    assert day in ctx.st_cache.days and ctx.st_cache.totals
    retention.roll_up_day(db, day)
    assert ctx.st_cache.days == {} and ctx.st_cache.totals == {} and ctx.st_cache.streaks == {}
    after = get(api, "overview", "7 days")
    assert after["trend"] == before["trend"] and card(after, "active") == card(before, "active")
    other, other_api, other_db = blank(tmp_path / "other")                # (every agent context is told, not just one)
    get(other_api, "overview", "7 days")
    put(db, day - timedelta(days=1), "10:00")
    retention.roll_up_day(db, day - timedelta(days=1))
    assert other.st_cache.days == {}
    retention.roll_up_day(db, day - timedelta(days=9))                    # (nothing to roll: nothing is dropped)
    get(api, "overview", "7 days")
    retention.roll_up_day(db, day - timedelta(days=9))
    assert ctx.st_cache.days


def test_a_failing_retention_listener_does_not_stop_the_others(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(retention, "changed", [lambda: 1 / 0, lambda: seen.append("ok")])
    ctx, api, db = blank(tmp_path)
    put(db, TODAY - timedelta(days=2), "10:00")
    retention.roll_up_day(db, TODAY - timedelta(days=2))
    assert seen == ["ok"]


def test_warm_fills_the_cache_so_the_first_visit_reads_only_today(tmp_path, monkeypatch):
    ctx, api, db = blank(tmp_path)
    for back in range(1, 25):
        for m in range(5):
            put(db, TODAY - timedelta(days=back), f"10:{m:02d}")
    put(db, TODAY, "09:00")
    db.add_unlock([1], ["x"], datetime.combine(TODAY - timedelta(days=9), datetime.min.time()), datetime.combine(TODAY - timedelta(days=9), datetime.min.time()) + timedelta(hours=1))
    db.set_setting(st.GOAL_KEY, "5")
    st.warm(ctx)
    assert len(ctx.st_cache.days) == 29 and len(ctx.st_cache.totals) == 35 and ctx.st_cache.streaks
    spans, totals = [], []
    real_rows, real_daily = st._activity_rows, stats.daily_active
    monkeypatch.setattr(st, "_activity_rows", lambda db_, a, b: spans.append((a, b)) or real_rows(db_, a, b))
    monkeypatch.setattr(stats, "daily_active", lambda *a, **k: totals.append(a[1:]) or real_daily(*a, **k))
    d = get(api, "overview", "30 days")
    assert spans == [(TODAY, TODAY + timedelta(days=1))] and totals == [(TODAY, TODAY + timedelta(days=1))]       # today, nothing else
    assert card(d, "active")["value"] == stats.hm(24 * 5 * 60 + 60)


def test_warm_on_an_empty_database_does_nothing_and_a_warm_cache_is_left_alone(tmp_path):
    ctx, api, db = blank(tmp_path)
    st.warm(ctx)
    assert ctx.st_cache.days == {}
    put(db, TODAY - timedelta(days=1), "10:00")
    st.warm(ctx)
    held = dict(ctx.st_cache.days)
    st.warm(ctx)
    assert held and all(ctx.st_cache.days[d] is held[d] for d in held)            # (nothing was built twice)


def wait_until(check, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return check()


def test_the_warm_up_thread_warms_once_after_the_delay_and_stops_on_request(tmp_path, monkeypatch):
    ctx, api, db = blank(tmp_path)
    for back in range(1, 6):
        put(db, TODAY - timedelta(days=back), "10:00")
    seen = []
    real = st.warm
    monkeypatch.setattr(st, "warm", lambda c, wait=0.0: seen.append((threading.current_thread().name, wait)) or real(c))
    thread = st.start_warmup(ctx, delay=0)
    try:
        assert thread.daemon and wait_until(lambda: len(getattr(ctx, "st_cache", None) and ctx.st_cache.days or ()) >= 5)
        assert seen == [("lockdown-st-warm", st.WARM_CATALOG_WAIT_SEC)]             # (once; the next try is tomorrow)
    finally:
        thread.stop_event.set()
        thread.join(10)
    assert not thread.is_alive()
    late = st.start_warmup(ctx, delay=60)                                          # (stopped during the delay: never runs)
    late.stop_event.set()
    late.join(10)
    assert len(seen) == 1


def test_the_warm_up_runs_again_for_each_new_day_and_after_a_failure(tmp_path, monkeypatch):
    ctx, api, db = blank(tmp_path)
    put(db, TODAY - timedelta(days=1), "10:00")
    runs = []

    def flaky(c, wait=0.0):
        runs.append(wait)
        if len(runs) == 2:
            raise RuntimeError("boom")
    monkeypatch.setattr(st, "warm", flaky)
    monkeypatch.setattr(st, "_seconds_to_next_day", lambda now: 0.01)
    monkeypatch.setattr(st, "WARM_RETRY_SEC", 0.01)
    thread = st.start_warmup(ctx, delay=0)
    try:
        assert wait_until(lambda: len(runs) >= 4)                                  # (a failure is logged, then tried again)
    finally:
        thread.stop_event.set()
        thread.join(10)
    assert runs[:4] == [st.WARM_CATALOG_WAIT_SEC, 0.0, 0.0, 0.0]                  # (the catalog is waited for in the first run only)


def test_the_next_warm_up_is_when_the_new_day_has_settled():
    assert st._seconds_to_next_day(datetime(2026, 10, 11, 23, 59, 0)) == 60 + st.SETTLE_SEC + 2
    assert st._seconds_to_next_day(datetime(2026, 10, 12, 0, 0, 0)) == 24 * 3600 + st.SETTLE_SEC + 2
    assert st._seconds_to_next_day(datetime(2026, 10, 12, 0, 3, 30)) == 24 * 3600 - 3 * 60 - 30 + st.SETTLE_SEC + 2


# ---------------------------------------------------------------- start-up answer, CSV

def test_without_a_tab_or_range_the_display_settings_are_used(tmp_path):
    ctx, api, db = blank(tmp_path)
    put(db, TODAY - timedelta(days=1), "10:00")
    d = data(api.screentime_get())
    assert (d["tab"], d["range"]) == ("overview", "Today") and d["defaults"] == {"tab": "overview", "range": "Today"}
    db.set_setting("screentime.tab", "Websites")
    db.set_setting("screentime.range", "30 days")
    d = data(api.screentime_get(None, None))
    assert (d["tab"], d["range"]) == ("websites", "30 days") and d["defaults"] == {"tab": "websites", "range": "30 days"}
    assert d["kind"] == "site"                                                  # (and it is that tab's data)
    d = data(api.screentime_get("apps", None))                                  # a link names the tab: the range is the default
    assert (d["tab"], d["range"], d["defaults"]["tab"]) == ("apps", "30 days", "websites")
    d = data(api.screentime_get(None, "7 days"))
    assert (d["tab"], d["range"]) == ("websites", "7 days")
    assert "defaults" not in data(api.screentime_get("apps", "Today"))          # (asked for both: nothing to say)
    db.set_setting("screentime.tab", "Dancing")
    db.set_setting("screentime.range", "Next year")
    d = data(api.screentime_get())
    assert (d["tab"], d["range"]) == ("overview", "Today")
    db.set_setting("screentime.tab", "Calendar")                                # (not offered by Display settings, but a tab)
    assert data(api.screentime_get())["tab"] == "calendar"


def test_the_display_settings_use_the_same_keys():
    from webapi import dashboard
    assert (st.ST_TAB_KEY, st.ST_RANGE_KEY) == (dashboard.ST_TAB_KEY, dashboard.ST_RANGE_KEY)
    assert [t.capitalize() for t in st.TABS[:4]] == [t if t != "Websites" else "Websites" for t in dashboard.ST_TABS]


def test_export_csv_asks_where_then_writes_on_a_worker(tmp_path, monkeypatch):
    ctx, api, db = blank(tmp_path)
    put(db, date.today() - timedelta(days=2), "10:00", exe="code.exe")
    out = tmp_path / "out" / "mine"
    out.parent.mkdir()
    asked = []
    monkeypatch.setattr(st, "_ask_save_path", lambda window, name: asked.append((window, name)) or str(out))
    import backup
    real = backup.screen_time_csv
    threads = []
    monkeypatch.setattr(backup, "screen_time_csv", lambda *a, **k: threads.append(threading.current_thread()) or real(*a, **k))
    reply = data(api.screentime_export_csv())
    assert asked == [(None, "screen-time.csv")]
    assert reply == {"path": str(out) + ".csv", "rows": 1}                  # .csv is added
    assert threads and threads[0] is not threading.main_thread() and threads[0].name == "lockdown-st-csv"
    lines = (out.parent / "mine.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "date,app,site,minutes,active minutes,category" and "code.exe" in lines[1]
    monkeypatch.setattr(st, "_ask_save_path", lambda window, name: None)
    assert data(api.screentime_export_csv()) == {"cancelled": True}


def test_export_csv_reports_a_file_it_cannot_write(tmp_path, monkeypatch):
    ctx, api, db = blank(tmp_path)
    monkeypatch.setattr(st, "_ask_save_path", lambda window, name: str(tmp_path / "no" / "such" / "f.csv"))
    reply = api.screentime_export_csv()
    assert not reply["ok"] and "Couldn't save the file" in reply["error"]


# ---------------------------------------------------------------- speed on the 2-year database after retention

@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """730 days, rolled up the way a real install is: 32 days of minute rows, daily totals for the rest."""
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


@pytest.mark.parametrize("tab", TABS)
@pytest.mark.parametrize("rng", RANGES)
def test_every_tab_and_range_answers_fast_on_730_days(big, tab, rng):
    """Target 50 ms at the 95th percentile; the assert allows 100 for a slow, busy box."""
    st.forget(big.ctx)
    t = time.perf_counter()
    first = data(big.api.screentime_get(tab, rng))
    cold = (time.perf_counter() - t) * 1000
    assert first["tab"] == tab
    samples = []
    for _ in range(20):
        t = time.perf_counter()
        big.api.screentime_get(tab, rng)
        samples.append((time.perf_counter() - t) * 1000)
    print(f"\n{tab:9s} {rng:10s} cold {cold:6.1f} ms  p50 {statistics.median(samples):5.2f}  p95 {p95(samples):5.2f}  max {max(samples):5.2f}")
    assert p95(samples) <= 100, samples
    assert cold <= 300


def test_a_long_range_never_scans_minute_rows_for_the_trend(big):
    st.forget(big.ctx)
    log = sql_log(big.ctx.db)
    get(big.api, "overview", "Today")
    big.ctx.db.conn.set_trace_callback(None)
    spans = [q for q in log if "FROM activity" in q and "minute >=" in q]
    # the trend's 30 days come from stats.daily_active (grouped in SQL), not from rows pulled into Python
    assert all("GROUP BY" in q or "ORDER BY minute" in q for q in spans)
    assert any("GROUP BY d" in q for q in log)
