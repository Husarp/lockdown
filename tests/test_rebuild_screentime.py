"""R14: the Api behind Screen Time's Overview, Apps and Websites tabs (webapi/screentime.py).

What is checked: every range's numbers equal what the old window's `stats` functions give on the same rows (totals,
sessions with the 5-minute gap - also across midnight, the longest focus, the heatmap, the categories split, the
timeline, the trend and bars, the top-40 / 60 s tables and their switch counts); the Calendar tab reads no range
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
def test_trend_and_bars_equal_the_daily_totals(fresh, rng):
    db = fresh.ctx.db
    span = 7 if rng == "7 days" else 30
    n = 30 if rng == "30 days" else 7
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
    assert trend["goal"] == 300.0 and trend["goal_label"] == "5 h"
    bars = d["bars"]
    bdays = [TODAY - timedelta(days=n - 1 - i) for i in range(n)]
    bvals = [daily.get(x.isoformat(), 0) for x in bdays]
    assert bars["title"] == f"Last {n} days"
    assert [b["value"] for b in bars["bars"]] == [round(v / 60, 1) for v in bvals]
    assert [b["label"] for b in bars["bars"]] == ([x.strftime("%a") for x in bdays] if n == 7 else [str(x.day) for x in bdays])
    assert [b["today"] for b in bars["bars"]] == [False] * (n - 1) + [True]
    with_data = [v for v in bvals if v]
    assert bars["note"] == f"avg {stats.hm(sum(with_data) / len(with_data))}"
    assert bars["bars"][-1]["tip"] == f"Sunday 11 Oct  {stats.hm(bvals[-1])}"


def test_unlock_days_are_marked_on_the_trend_and_the_bars(fresh):
    d = get(fresh.api, "overview", "30 days")
    unlock_days = {3, 17}                                   # synthdata: unlocks 3, 17, 40 and 55 days back
    marked = {TODAY - timedelta(days=i) for i in range(30) if d["trend"]["points"][29 - i]["unlocks"]}
    assert marked == {TODAY - timedelta(days=i) for i in unlock_days}
    assert [i for i, b in enumerate(d["bars"]["bars"]) if b["unlocks"]] == [29 - 17, 29 - 3]


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
    assert d["trend"]["goal"] == 0 and d["bars"]["goal"] == 0 and d["trend"]["goal_label"] == ""
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
    # a day that was over keeps its cached numbers (they can't change), until the cache is dropped or has expired
    put(db, TODAY - timedelta(days=1), "20:00")
    assert get(api, "overview", "Yesterday")["cards"][0]["value"] == "30 m"
    st.forget(ctx)
    assert get(api, "overview", "Yesterday")["cards"][0]["value"] == "31 m"
    put(db, TODAY - timedelta(days=1), "20:01")
    monkeypatch.setattr(st, "CACHE_SEC", 0)
    assert get(api, "overview", "Yesterday")["cards"][0]["value"] == "32 m"


def test_the_cache_is_per_agent_not_per_database_file(tmp_path):
    ctx1, api1, db1 = blank(tmp_path / "a")
    ctx2, api2, db2 = blank(tmp_path / "b")
    put(db1, TODAY - timedelta(days=1), "10:00")
    assert get(api1, "overview", "Yesterday")["cards"][0]["value"] == "1 m"
    assert get(api2, "overview", "Yesterday")["empty"] is True


def test_days_retention_rolled_up_still_count(tmp_path):
    ctx, api, db = blank(tmp_path)
    day = TODAY - timedelta(days=3)
    for m in range(10):
        put(db, day, f"10:{m:02d}", exe="code.exe")
        put(db, day, f"11:{m:02d}", exe="chrome.exe", site="docs.python.org", active=45)
    db.add_switch(datetime.combine(day, datetime.min.time()) + timedelta(hours=10), "code.exe", "")
    put(db, TODAY, "08:00")
    before = {r["key"]: (r["sec"], r["count"]) for r in get(api, "apps", "7 days")["rows"]}
    day_before = get(api, "overview", "7 days")
    retention.roll_up_day(db, day)                                     # the minute rows are gone, the totals stay
    assert db.conn.execute("SELECT COUNT(*) FROM activity WHERE minute < ?", (f"{TODAY} 00:00",)).fetchone()[0] == 0
    st.forget(ctx)
    after = {r["key"]: (r["sec"], r["count"]) for r in get(api, "apps", "7 days")["rows"]}
    assert after == before
    now = get(api, "overview", "7 days")
    assert card(now, "active")["value"] == card(day_before, "active")["value"]
    assert now["bars"]["bars"] == day_before["bars"]["bars"] and now["trend"]["points"] == day_before["trend"]["points"]
    sites = {r["key"]: r["sec"] for r in get(api, "websites", "7 days")["rows"]}
    assert sites == {"docs.python.org": 450}


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
