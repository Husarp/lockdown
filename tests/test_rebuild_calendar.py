"""R22: Blocking > Calendar (webapi/calendar.py): the intervals, marks, totals and Day-view cards that `calendar_get`
works out in Python. The page only draws them (tests/ui/test_page_blocking_calendar.py)."""
import statistics
import time
from datetime import datetime, timedelta

import pytest

import webapi
from rules import make_schedule
from webapi import calendar as cal
from webapi import core

NOW = datetime(2026, 10, 11, 15, 42, 17)      # a Sunday afternoon
MON = "2026-10-05"
EVERY_DAY = list(range(7))


@pytest.fixture
def env(tmp_path):
    ctx = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    ctx.api = core.build_api(ctx)
    ctx.publish = lambda event, payload=None, key=None: None
    return ctx


def add(ctx, name, rules, kind="site", disabled=False):
    host = f"{name.lower().replace(' ', '')}.com" if kind == "site" else f"{name.lower()}.exe"
    iid = ctx.db.add_item(name, [host], kind, "manual", rules)
    if disabled:
        ctx.db.update_item(iid, name, [host], None, rules, disabled=True)
    return iid


def sched(mode, windows):
    return {"rule_type": "scheduled", "schedule": make_schedule(mode, windows)}


def get(ctx, start=None, days=1, filt="all"):
    reply = ctx.api.calendar_get(start, days, filt)
    assert reply["ok"], reply
    return reply["data"]


def row(view, name):
    return next(r for r in view["rows"] if r["name"] == name)


# ---------------------------------------------------------------- the intervals

def test_an_overnight_window_is_split_across_days(env):
    add(env, "Reddit", [sched("block", [([0], "22:00", "07:00")])])           # Monday night into Tuesday morning
    view = get(env, MON, 7)
    days = row(view, "Reddit")["days"]
    assert days[0] == [[1320, 1440]] and days[1] == [[0, 420]] and days[2] == []
    assert row(view, "Reddit")["sum"] == "9 h"


def test_allow_only_blocks_the_complement(env):
    add(env, "Steam", [sched("allow", [([0, 1, 2, 3, 4], "18:00", "22:00")])], kind="app")
    days = row(get(env, MON, 7), "Steam")["days"]
    assert days[0] == [[0, 1080], [1320, 1440]]
    assert days[5] == [[0, 1440]]                       # Saturday: nothing allowed, blocked all day


def test_a_permanent_block_is_all_day_and_says_so(env):
    add(env, "TikTok", [{"rule_type": "permanent"}])
    view = get(env, "2026-10-11", 1)
    r = row(view, "TikTok")
    assert r["days"] == [[[0, 1440]]] and r["sum"] == "blocked all day"


def test_a_group_rule_reaches_its_members(env):
    iid = add(env, "Reddit", [])
    env.db.add_group("School", [sched("block", [(EVERY_DAY, "09:00", "15:00")])], {iid: {}})
    assert row(get(env, "2026-10-11", 1), "Reddit")["days"] == [[[540, 900]]]


def test_disabled_items_are_included_but_marked(env):
    add(env, "Reddit", [sched("block", [(EVERY_DAY, "09:00", "10:00")])], disabled=True)
    view = get(env, "2026-10-11", 1)
    r = row(view, "Reddit")
    assert r["disabled"] and r["days"] == [[[540, 600]]] and r["sum"] == "Disabled"
    assert view["summary"]["busiest"] == "nothing is blocked today"       # it blocks nothing


def test_limits_and_temporary_blocks_are_not_drawn_but_limits_are_named(env):
    add(env, "YouTube", [{"rule_type": "time_limit", "daily_limit_min": 60}])
    add(env, "Netflix", [{"rule_type": "temporary", "temp_until": "2026-10-12 10:00:00"}])
    view = get(env, "2026-10-11", 1)
    assert view["rows"] == [] and view["limits"] == {"names": ["YouTube"], "more": 0}


def test_the_week_starts_on_monday_and_navigates(env):
    view = get(env, "2026-10-11", 7)            # a Sunday
    assert view["start"] == MON and [d["label"] for d in view["dates"]] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert view["prev"] == "2026-09-28" and view["next"] == "2026-10-12"
    assert [d["today"] for d in view["dates"]] == [False] * 6 + [True]
    assert view["title"] == "05 – 11 October" and view["head"] == "WEEK TOTAL"


def test_titles_and_heads_for_every_view(env):
    day = get(env)
    assert day["title"] == "Sunday 11 October" and day["is_today"] and day["head"] == "BLOCKED TODAY"
    other = get(env, "2026-10-08")
    assert other["head"] == "BLOCKED THAT DAY" and not other["is_today"]
    three = get(env, "2026-10-30", 3)
    assert three["head"] == "3-DAY TOTAL" and three["title"] == "30 Oct – 01 Nov"
    assert [d["label"] for d in three["dates"]] == ["Fri 30", "Sat 31", "Sun 01"]


def test_the_filter_keeps_sites_or_apps(env):
    add(env, "Reddit", [{"rule_type": "permanent"}])
    add(env, "Steam", [{"rule_type": "permanent"}], kind="app")
    assert [r["name"] for r in get(env, None, 1, "sites")["rows"]] == ["Reddit"]
    assert [r["name"] for r in get(env, None, 1, "apps")["rows"]] == ["Steam"]
    assert [r["name"] for r in get(env)["rows"]] == ["Reddit", "Steam"]


def test_emergency_marks_land_on_their_day_and_show_items_without_clock_rules(env):
    iid = add(env, "Reddit", [{"rule_type": "time_limit", "daily_limit_min": 30}])
    start = datetime(2026, 10, 9, 21, 30)
    env.db.add_unlock([iid], ["Reddit"], start, start + timedelta(minutes=15))
    r = row(get(env, MON, 7), "Reddit")
    assert r["marks"][4] == [21 * 60 + 30] and r["days"] == [[]] * 7
    assert get(env, "2026-10-11", 1)["rows"] == []              # not that day


def test_bad_arguments_are_refused(env):
    for args in ((None, 2, "all"), (None, True, "all"), (None, 1, "x"), ("tomorrow", 1, "all"), (20261011, 1, "all")):
        assert not env.api.calendar_get(*args)["ok"]


# ---------------------------------------------------------------- the Day-view cards

def test_the_cards_for_a_fixed_day(env):
    add(env, "Reddit", [sched("block", [(EVERY_DAY, "09:00", "12:00")])])
    add(env, "Steam", [sched("block", [(EVERY_DAY, "10:00", "13:00")])], kind="app")
    add(env, "Twitch", [sched("block", [(EVERY_DAY, "18:00", "20:00")])])
    s = get(env, "2026-10-11", 1)["summary"]                    # now = 15:42
    assert s["next"] == {"at": "18:00", "text": "Twitch blocked", "kind": "blocked"}
    assert s["busiest"] == "10:00–12:00 — 2 items blocked together"
    assert s["free"] == "00:00–09:00 — nothing but the lists"
    later = cal.summarize([[(540, 720)], [(600, 780)]], ["a", "b"], 12 * 60 + 30, "today")
    assert later["next"] == {"at": "13:00", "text": "b allowed again", "kind": "allowed"}


def test_the_cards_when_nothing_is_left(env):
    add(env, "Reddit", [sched("block", [(EVERY_DAY, "09:00", "10:00")])])
    assert get(env, "2026-10-11", 1)["summary"]["next"] == {"at": None, "text": "no more changes today", "kind": None}
    other = get(env, "2026-10-08", 1)["summary"]
    assert other["next"]["text"] == "(not today)" and other["busiest"] == "09:00–10:00 — 1 item blocked"


def test_a_day_blocked_all_day_has_no_free_window(env):
    add(env, "TikTok", [{"rule_type": "permanent"}])
    s = get(env, "2026-10-11", 1)["summary"]
    assert s["free"] == "none today — something is blocked all day"
    assert s["busiest"] == "00:00–24:00 — 1 item blocked"


def test_week_and_three_day_views_have_no_cards(env):
    assert get(env, None, 7)["summary"] is None and get(env, None, 3)["summary"] is None


def test_the_legend_lists_the_categories(env):
    names = [c["name"] for c in get(env)["categories"]]
    assert names[:4] == ["Productive", "Neutral", "Games", "Distracting"]


# ---------------------------------------------------------------- speed

def test_200_items_for_a_week_is_fast(env):
    for i in range(200):
        add(env, f"Site {i}", [sched("block", [(EVERY_DAY, "22:00", "07:00")]), sched("allow", [([0, 1], "08:00", "20:00")])])
    get(env, MON, 7)                                                  # (the draft is read once, here)
    runs = []
    for _ in range(10):
        t = time.perf_counter()
        view = get(env, MON, 7)
        runs.append((time.perf_counter() - t) * 1000)
    assert len(view["rows"]) == 200
    print(f"calendar_get 200 items x 7 days: median {statistics.median(runs):.1f} ms, max {max(runs):.1f} ms")
    assert statistics.median(runs) <= 100
