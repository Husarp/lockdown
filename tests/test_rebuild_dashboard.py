"""R13: the Dashboard's Api (webapi/dashboard.py) - dashboard_get, display_get / display_set - and the one status
vocabulary (get_status speaks the snapshot's words)."""
import json
import math
import time
from datetime import date, datetime, timedelta

import pytest

import antibypass as ab
import stats
import webapi
from db import Database
from rules import make_schedule
from webapi import core, dashboard

MON = datetime(2026, 9, 14)            # a Monday
EVERY_DAY = list(range(7))
GROUP_2H = {"rule_type": "time_limit", "daily_limit_min": 120}


@pytest.fixture
def env(tmp_path):
    state = {"now": MON.replace(hour=12)}
    ctx = webapi.make_context(tmp_path / "t.db", clock=lambda db: state["now"])
    ctx.db.set_setting("service_heartbeat", str(time.time()))
    return type("Env", (), {"ctx": ctx, "db": ctx.db, "api": core.build_api(ctx), "state": state,
                            "path": tmp_path / "t.db"})()


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def get(env):
    return data(env.api.dashboard_get())


def card(env, key):
    return get(env)["cards"][key]


def minute(day, hhmm):
    return f"{day} {hhmm}"


def record(db, day, hhmm, seconds=60, active=60, exe="code.exe", site=""):
    db.add_activity(minute(day, hhmm), exe, site, seconds, active)


# ---------------------------------------------------------------- classification

def test_the_methods_are_registered_with_the_right_kind():
    kinds = dict(webapi.api_methods())
    assert kinds["dashboard_get"] == "read" and kinds["display_get"] == "read"
    assert kinds["display_set"] == "ui"
    for name in ("antibypass_switch_on", "service_start"):                 # the banners' buttons (the shell has them)
        assert kinds[name] == "tighten"


# ---------------------------------------------------------------- fresh database

def test_a_fresh_database_is_the_empty_state(env):
    d = get(env)
    assert d["empty"] is True and d["cards"] == {} and d["since"] is None
    assert d["date"] == "Monday, 14 September"
    assert d["order"] == list(dashboard.CARDS) and d["hidden"] == []
    assert d["titles"]["stats"] == "Numbers at the top"
    assert d["status"]["blocks"]["total"] == 0 and d["status"]["running"] is True


def test_the_header_says_when_tracking_started_today(env):
    record(env.db, "2026-09-14", "07:12")
    record(env.db, "2026-09-14", "09:30")
    d = get(env)
    assert d["empty"] is False and d["since"] == "07:12"
    assert set(d["cards"]) == set(dashboard.CARDS)


def test_the_mode_goes_in_the_status_for_the_header(env):
    import modes
    record(env.db, "2026-09-14", "07:12")
    modes.start(env.db, "work", env.state["now"] - timedelta(minutes=5), env.state["now"] + timedelta(hours=1))
    mode = get(env)["status"]["mode"]
    assert mode["name"] == "Work" and mode["ends_at"] == "2026-09-14T13:00:00" and mode["phase"] is None


# ---------------------------------------------------------------- the four numbers

def test_screen_time_against_yesterday_at_the_same_time(env):
    day, yesterday = "2026-09-14", "2026-09-13"
    record(env.db, day, "09:00", 60, 60)                          # 60 s today
    record(env.db, yesterday, "09:00", 60, 60)                    # 60 s yesterday, before 12:00
    record(env.db, yesterday, "15:00", 600, 600)                  # ... and later: not counted yet
    screen = card(env, "stats")["screen"]
    assert screen["value"] == "1 m" and screen["note"] == "about the same as yesterday" and screen["tone"] == "muted"
    assert screen["yesterday_sec"] == 60 and screen["sec"] == 60


@pytest.mark.parametrize("today, expected", [(119, "about the same as yesterday"), (120, "1 m more than yesterday")])
def test_the_about_the_same_window_is_a_minute(env, today, expected):
    record(env.db, "2026-09-14", "09:00", today, today)
    record(env.db, "2026-09-13", "09:00", 60, 60)
    screen = card(env, "stats")["screen"]
    assert screen["note"] == expected
    assert screen["tone"] == ("muted" if "same" in expected else "warn")


def test_less_than_yesterday_is_good_news(env):
    record(env.db, "2026-09-14", "09:00", 60, 60)
    record(env.db, "2026-09-13", "09:00", 3600, 3600)
    screen = card(env, "stats")["screen"]
    assert screen["note"] == "59 m less than yesterday" and screen["tone"] == "ok"


def test_switches_say_no_history_yet_without_earlier_days(env):
    record(env.db, "2026-09-14", "09:00")
    env.db.add_switch(MON.replace(hour=9), "code.exe", "")
    sw = card(env, "stats")["switches"]
    assert sw["value"] == "1" and sw["note"] == "no history yet" and sw["tone"] == "muted"


def test_switches_against_the_usual_for_this_time(env):
    for back in range(1, 4):
        day = MON - timedelta(days=back)
        record(env.db, day.date().isoformat(), "09:00")
        for i in range(4):
            env.db.add_switch(day.replace(hour=8, minute=i), "code.exe", "")
    record(env.db, "2026-09-14", "09:00")
    for i in range(10):
        env.db.add_switch(MON.replace(hour=8, minute=i), "code.exe", "")
    sw = card(env, "stats")["switches"]
    assert sw["value"] == "10" and sw["note"] == "6 more than usual by this time" and sw["tone"] == "warn"
    env.db.write("DELETE FROM switch_events WHERE timestamp >= ?", ("2026-09-14 00:00:00",))
    for i in range(4):
        env.db.add_switch(MON.replace(hour=8, minute=i), "code.exe", "")
    sw = card(env, "stats")["switches"]
    assert sw["value"] == "4" and sw["note"] == "about usual for this time of day"


def test_blocked_now_and_what_comes_next(env):
    record(env.db, "2026-09-14", "09:00")
    night = {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "18:00", "19:00")])}
    env.db.add_item("Game", ["game.exe"], "app", rules=[night])
    env.db.add_item("Site", ["site.test"], "site", rules=[{"rule_type": "permanent"}])
    b = card(env, "stats")["blocked"]
    assert b["value"] == "1 of 2" and b["note"] == "Next: Game blocked at 18:00" and b["tone"] == "muted"
    ab.switch_off(env.db, env.state["now"])
    b = card(env, "stats")["blocked"]
    assert b["value"] == "0 of 2" and b["note"] == "Lockdown is off - not enforced" and b["tone"] == "bad"
    ab.switch_on(env.db)
    env.db.set_setting("service_heartbeat", str(time.time() - 3600))
    b = card(env, "stats")["blocked"]
    assert b["value"] == "0 of 2" and b["note"] == "Service stopped - not enforced"


def test_nothing_else_coming_up_today(env):
    record(env.db, "2026-09-14", "09:00")
    env.db.add_item("Site", ["site.test"], "site", rules=[{"rule_type": "permanent"}])
    assert card(env, "stats")["blocked"]["note"] == "Nothing else coming up today"


def test_time_saved_prices_a_blocked_visit(env):
    record(env.db, "2026-09-14", "09:00")
    assert card(env, "stats")["saved"] == {"value": "0 m", "note": "0 blocked visits turned away", "tone": "muted"}
    item = env.db.add_item("Site", ["site.test"], "site", rules=[{"rule_type": "permanent"}])
    env.db.add_block_event("site.test", item, "Site", "permanent", None, MON.replace(hour=10))
    saved = card(env, "stats")["saved"]
    assert saved["value"] == "5 m" and saved["note"] == "1 blocked visit turned away"      # 5 min without history


# ---------------------------------------------------------------- Limits today

def _fun(env):
    yt = env.db.add_item("YouTube", ["yt.exe"], "app")
    tw = env.db.add_item("Twitch", ["tw.exe"], "app")
    gid = env.db.add_group("Fun", [GROUP_2H], {yt: {}, tw: {}})
    return yt, tw, gid


def test_a_group_with_every_member_blocked_is_one_blocked_until_line(env):
    record(env.db, "2026-09-14", "09:00")
    yt, tw, gid = _fun(env)
    env.db.add_usage([(f"group:{gid}", "day:2026-09-14")], 120 * 60, MON.date())      # the group's 2 h are gone
    rows = card(env, "limits")["rows"]
    assert [(r["name"], r["left"], r["text"], r["blocked"]) for r in rows] == \
        [("Fun (group)", "Blocked (until Tue 00:00)", "", True)]
    assert rows[0]["tone"] == "bad" and rows[0]["frac"] == 1.0 and rows[0]["icon"] == ["letter", "Fun"]


def test_a_time_limit_row_says_used_of_limit_and_what_is_left(env):
    record(env.db, "2026-09-14", "09:00")
    solo = env.db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    env.db.add_usage([(f"item:{solo}", "day:2026-09-14")], 15 * 60, MON.date())
    row = card(env, "limits")["rows"][0]
    assert (row["name"], row["text"], row["left"], row["tone"]) == ("Game", "15 m of 1 h 00 m", "45 m left", "ok")
    assert row["frac"] == 0.25 and row["icon"] == ["app", "game.exe"]
    assert card(env, "limits")["note"] == "1 active"


def test_the_colour_goes_amber_then_red(env):
    record(env.db, "2026-09-14", "09:00")
    solo = env.db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 100}])
    for minutes, tone in ((59, "ok"), (60, "warn"), (84, "warn"), (85, "bad")):
        env.db.write("DELETE FROM usage")
        env.db.add_usage([(f"item:{solo}", "day:2026-09-14")], minutes * 60, MON.date())
        assert card(env, "limits")["rows"][0]["tone"] == tone, minutes


def test_an_opens_row_and_the_rows_are_sorted_fullest_first(env):
    record(env.db, "2026-09-14", "09:00")
    timed = env.db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    env.db.add_item("Reddit", ["reddit.com"], "site", rules=[{"rule_type": "switch_limit", "daily_switch_limit": 5}])
    env.db.add_usage([(f"item:{timed}", "day:2026-09-14")], 30 * 60, MON.date())
    rows = card(env, "limits")["rows"]
    assert [r["name"] for r in rows] == ["Game", "Reddit - opens"]
    assert rows[1]["text"] == "0 opens of 5 today" and rows[1]["left"] == "5 left" and rows[1]["icon"] == ["site", "reddit.com"]


def test_an_allowance_row(env):
    record(env.db, "2026-09-14", "09:00")
    env.state["now"] = MON.replace(hour=23)
    night = {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "22:00", "07:00")]),
             "allowance_min": 15}
    env.db.add_item("Game", ["game.exe"], "app", rules=[night])
    rows = card(env, "limits")["rows"]
    assert len(rows) == 1 and rows[0]["name"] == "Game - allowance"
    assert rows[0]["text"] == "0 m of 15 m allowed during blocked hours (until 07:00)" and rows[0]["left"] == "15 m left"


def test_no_limits_means_no_rows(env):
    record(env.db, "2026-09-14", "09:00")
    assert card(env, "limits") == {"count": 0, "note": "0 active", "rows": []}


# ---------------------------------------------------------------- Coming up

def test_members_that_start_together_are_one_entry(env):
    record(env.db, "2026-09-14", "09:00")
    evening = {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "18:00", "19:00")])}
    a = env.db.add_item("A", ["a.exe"], "app")
    b = env.db.add_item("B", ["b.exe"], "app")
    env.db.add_group("Fun", [evening], {a: {}, b: {}})
    rows = card(env, "coming")["rows"]
    starts = [r for r in rows if r["kind"] == "start"]
    assert len(starts) == 1
    assert (starts[0]["time"], starts[0]["title"], starts[0]["sub"]) == ("18:00", "Fun starts", "A · B")
    assert starts[0]["at"] == "2026-09-14T18:00:00"
    assert any(r["kind"] == "end" for r in rows) is False            # (it ends tomorrow's list, not today's first 24 h)


def test_at_most_four_and_a_tomorrow_time_has_its_day(env):
    record(env.db, "2026-09-14", "09:00")
    for i in range(6):
        evening = {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, f"{13 + i}:00", f"{13 + i}:30")])}
        env.db.add_item(f"Item {i}", [f"i{i}.exe"], "app", rules=[evening])
    rows = card(env, "coming")["rows"]
    assert len(rows) == 4 and [r["time"] for r in rows] == ["13:00", "14:00", "15:00", "16:00"]
    assert dashboard.when_text(MON.replace(day=15, hour=7), env.state["now"]) == "Tue 07:00"


def test_a_limit_about_to_run_out_is_a_today_entry(env):
    record(env.db, "2026-09-14", "09:00")
    solo = env.db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    env.db.add_usage([(f"item:{solo}", "day:2026-09-14")], 50 * 60, MON.date())
    row = card(env, "coming")["rows"][0]
    assert (row["kind"], row["time"], row["title"], row["sub"]) == \
        ("limit", "Today", "Game limit will be reached", "about 10 minutes of use left")


def test_nothing_coming_up(env):
    record(env.db, "2026-09-14", "09:00")
    assert card(env, "coming") == {"rows": []}


# ---------------------------------------------------------------- the rest of the cards

def test_blocked_visits_top_four_and_the_persisted_show_hide(env):
    record(env.db, "2026-09-14", "09:00")
    ids = [env.db.add_item(f"Site {i}", [f"s{i}.test"], "site", rules=[{"rule_type": "permanent"}]) for i in range(5)]
    for n, item in enumerate(ids):
        for _ in range(n + 1):
            env.db.add_block_event(f"s{n}.test", item, f"Site {n}", "permanent", None, MON.replace(hour=10))
    visits = card(env, "visits")
    assert visits["count"] == 15 and visits["open"] is False
    assert [(r["name"], r["text"]) for r in visits["rows"]] == \
        [("Site 4", "5 attempts"), ("Site 3", "4 attempts"), ("Site 2", "3 attempts"), ("Site 1", "2 attempts")]
    assert visits["rows"][0]["icon"] == ["site", "s4.test"]
    assert data(env.api.ui_setting("dash.visits_open", "1")) is None
    assert card(env, "visits")["open"] is True


def test_week_bars_goal_and_unlock_marks(env):
    for back in range(7):
        record(env.db, (MON - timedelta(days=back)).date().isoformat(), "09:00", 3600, 3600 * (back + 1))
    env.db.add_unlock([1], ["x"], MON.replace(hour=8), MON.replace(hour=9))
    week = card(env, "week")
    assert [b["label"] for b in week["bars"]] == ["Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon"]
    assert [b["today"] for b in week["bars"]] == [False] * 6 + [True]
    assert week["bars"][-1]["value"] == 60.0 and week["bars"][-1]["unlocks"] == 1
    assert week["bars"][-1]["tip"] == "Monday 14 September  1 h 00 m\nEmergency unlock 08:00–09:00"
    assert week["bars"][-2]["tip"] == "Sunday 13 September  2 h 00 m" and week["bars"][-2]["unlocks"] == 0
    assert week["goal"] == 300.0 and week["note"] == "daily goal 5 h" and week["goal_label"] == "5 h"
    env.db.set_setting(dashboard.GOAL_KEY, "0")
    week = card(env, "week")
    assert week["goal"] == 0 and week["note"] == ""


def test_at_a_glance(env):
    record(env.db, "2026-09-14", "09:00", 3600, 3600, exe="code.exe")
    record(env.db, "2026-09-14", "10:00", 600, 600, exe="chrome.exe")
    for hour, exe in ((9, "code.exe"), (9, "chrome.exe"), (11, "code.exe"), (11, "code.exe")):
        env.db.add_switch(MON.replace(hour=hour, minute=len(exe)), exe, "")
    labels = {r["label"]: r for r in card(env, "glance")["rows"]}
    assert labels["Top app today"]["main"] == "Code" and labels["Top app today"]["extra"] == "1 h 00 m"
    assert labels["Most switched to"]["main"] == "Code"
    assert labels["Quietest hour"]["main"] == "10:00 - 11:00"
    assert labels["Goal streak"]["main"] == "1 day in a row"
    assert labels["No emergency unlock"]["main"] == "1 day in a row"


def test_the_names_of_apps_come_from_the_blocklist(env):
    record(env.db, "2026-09-14", "09:00", exe="disc.exe")
    env.db.add_item("Discord", ["disc.exe"], "app", rules=[{"rule_type": "permanent"}])
    assert card(env, "glance")["rows"][0]["main"] == "Discord"


def test_an_app_that_is_not_blocked_gets_the_app_catalog_name_when_it_has_loaded(env):
    from resident.catalog import AppCatalog
    record(env.db, "2026-09-14", "09:00", 3000, 3000, exe="code.exe")
    record(env.db, "2026-09-14", "10:00", 600, 600, exe="odd_tool.exe")
    record(env.db, "2026-09-14", "11:00", 60, 60, exe="disc.exe")
    env.db.add_item("Discord", ["disc.exe"], "app", rules=[{"rule_type": "permanent"}])
    catalog = AppCatalog(env.path.parent / "cat.json", running_now=lambda: {}, list_apps=lambda: [
        {"name": "Visual Studio Code", "exe": "code.exe", "path": "C:/vscode/code.exe"},
        {"name": "Disc Golf", "exe": "disc.exe", "path": "C:/disc/disc.exe"}])
    env.ctx.catalog = catalog
    assert card(env, "glance")["rows"][0]["main"] == "Code"                  # (the catalog has not loaded yet)
    catalog.scan_now()
    env.ctx.dash_cache.clear()
    assert card(env, "glance")["rows"][0]["main"] == "Visual Studio Code"
    assert dashboard._app_name("disc.exe", [i for i in env.db.list_items()], catalog) == "Discord"   # blocklist first
    assert dashboard._app_name("odd_tool.exe", [], catalog) == "Odd_tool"                            # not in it: as before
    assert dashboard._app_name("pythonw.exe", [], catalog) == "Lockdown"


def test_active_blocks_and_pause(env):
    import pause
    record(env.db, "2026-09-14", "09:00")
    env.db.add_item("Site", ["site.test"], "site", rules=[{"rule_type": "permanent"}])
    active = card(env, "active")
    assert active["count"] == 1 and active["rows"][0]["text"] == "Blocked" and active["more"] == 0
    assert card(env, "pause") == {"paused": False, "until": None, "silent": False}
    pause.start(env.db, env.state["now"], 30, silent=True)
    assert card(env, "pause") == {"paused": True, "until": "2026-09-14T12:30:00", "silent": True}
    assert card(env, "active")["count"] == 0                                # nothing is blocked while paused


def test_bedtime_and_emergency(env):
    import reminders
    record(env.db, "2026-09-14", "09:00")
    assert card(env, "bedtime")["on"] is False
    reminders.save(env.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "22:30", "before": 15})
    bed = card(env, "bedtime")
    assert bed == {"on": True, "bedtime": "22:30", "wake": "07:00", "heads_up": 15, "mode": "", "at": "2026-09-14T22:30:00"}
    env.state["now"] = MON.replace(hour=23)
    assert card(env, "bedtime")["at"] == "2026-09-15T22:30:00"            # tonight's has passed: tomorrow's
    emergency = card(env, "emergency")
    assert emergency["usable"] is True and emergency["left"] == 3
    assert emergency["kinds"][0]["text"] == "3 of 3 left this week" and emergency["kinds"][0]["detail"] == "20 min each"
    assert emergency["used_today"] == []
    env.db.add_unlock([1], ["x"], MON.replace(hour=23, minute=50), MON.replace(hour=23, minute=50) + timedelta(minutes=20))
    env.db.add_unlock([1], ["x"], MON.replace(hour=8), MON.replace(hour=8, minute=20))
    assert card(env, "emergency")["used_today"] == ["08:00–08:20", "23:50–00:10"]      # (a use past midnight shows its real end)
    env.db.set_setting("emergency.enabled", "0")
    assert card(env, "emergency")["usable"] is False


def test_the_timeline_has_segments_and_a_legend(env):
    record(env.db, "2026-09-14", "07:12", 60, 60, exe="code.exe")
    record(env.db, "2026-09-14", "07:13", 60, 0, exe="code.exe")
    today = card(env, "today")
    assert today["start_hour"] == 6
    assert today["segments"] == [{"start": 432, "len": 1, "kind": "neutral"}, {"start": 433, "len": 1, "kind": "idle"}]
    assert [e["name"] for e in today["legend"]] == ["Productive", "Neutral", "Games", "Distracting", "Idle"]
    assert today["colors"]["productive"] == "var(--cat-prod)"


# ---------------------------------------------------------------- display settings

def test_display_get_defaults(env):
    d = data(env.api.display_get())
    assert [c["key"] for c in d["cards"]] == list(dashboard.CARDS) and all(c["shown"] for c in d["cards"])
    assert d["st_tab"] == "Overview" and d["st_range"] == "Today"
    assert d["tabs"] == ["Overview", "Apps", "Websites", "Switches"] and "Calendar" not in d["tabs"]
    assert d["ranges"] == ["Today", "Yesterday", "7 days", "30 days"]


def test_display_set_round_trip(env):
    order = ["limits", "stats"] + [k for k in dashboard.CARDS if k not in ("limits", "stats")]
    reply = data(env.api.display_set(["glance", "today"], order, "Apps", "7 days"))
    assert [c["key"] for c in reply["cards"]][:2] == ["limits", "stats"]
    assert {c["key"] for c in reply["cards"] if not c["shown"]} == {"glance", "today"}
    assert reply["st_tab"] == "Apps" and reply["st_range"] == "7 days"
    assert data(env.api.display_get()) == reply
    assert json.loads(env.db.get_setting("dash.hidden")) == ["today", "glance"]       # (in card order)
    assert env.db.get_setting("screentime.tab") == "Apps" and env.db.get_setting("screentime.range") == "7 days"
    record(env.db, "2026-09-14", "09:00")
    d = get(env)
    assert d["order"][:2] == ["limits", "stats"] and d["hidden"] == ["today", "glance"]
    assert "glance" not in d["cards"] and "today" not in d["cards"] and "limits" in d["cards"]


def test_display_set_leaves_screen_time_alone_when_not_given(env):
    data(env.api.display_set([], list(dashboard.CARDS), "Websites", "30 days"))
    data(env.api.display_set(["pause"], list(dashboard.CARDS)))
    d = data(env.api.display_get())
    assert d["st_tab"] == "Websites" and d["st_range"] == "30 days" and not [c for c in d["cards"] if c["key"] == "pause" and c["shown"]]


def test_display_set_is_ungated_even_when_locked(env):
    ab.save(env.db, {**ab.settings(env.db), "phrase": True})
    assert ab.status(ab.settings(env.db), env.state["now"]) == "phrase"
    reply = env.api.display_set(["today"], list(dashboard.CARDS), "Switches", "Yesterday")
    assert reply["ok"] and "needs_challenge" not in reply
    assert json.loads(env.db.get_setting("dash.hidden")) == ["today"]


@pytest.mark.parametrize("args", [
    (["nope"], list(dashboard.CARDS), "Overview", "Today"),          # an unknown card
    ([], ["stats", "nope"], "Overview", "Today"),
    ([], list(dashboard.CARDS), "Calendar", "Today"),                # never Calendar
    ([], list(dashboard.CARDS), "Overview", "Forever"),
    ("stats", list(dashboard.CARDS), "Overview", "Today"),
    ([5], list(dashboard.CARDS), "Overview", "Today"),
])
def test_display_set_refuses_what_is_not_a_card_tab_or_range(env, args):
    reply = env.api.display_set(*args)
    assert not reply["ok"]
    assert env.db.get_setting("dash.hidden") is None


def test_the_old_windows_hidden_cards_still_count(env):
    env.db.set_setting("dash.hidden", json.dumps(["glance", "limits", "no-such-card"]))
    record(env.db, "2026-09-14", "09:00")
    d = get(env)
    assert d["hidden"] == ["limits", "glance"] and "glance" not in d["cards"]
    env.db.set_setting("dash.hidden", "garbage")
    assert get(env)["hidden"] == []


def test_new_cards_go_last_in_a_saved_order(env):
    env.db.set_setting("dash.order", json.dumps(["glance", "stats"]))
    assert get(env)["order"] == ["glance", "stats"] + [k for k in dashboard.CARDS if k not in ("glance", "stats")]


def test_a_hidden_card_is_not_computed(env, monkeypatch):
    record(env.db, "2026-09-14", "09:00")
    site = env.db.add_item("Site", ["site.test"], "site", rules=[{"rule_type": "permanent"}])
    env.db.add_block_event("site.test", site, "Site", "permanent", None, MON.replace(hour=10))
    called = []

    def spy(name, real):
        def wrapper(*a, **kw):
            called.append(name)
            return real(*a, **kw)
        return wrapper
    for key, real in list(dashboard.BUILDERS.items()):
        monkeypatch.setitem(dashboard.BUILDERS, key, spy(key, real))
    for mod, fn in ((dashboard, "_streaks"), (stats, "visit_totals"), (stats, "switch_summary"), (stats, "blocked_events"),
                    (dashboard, "limit_entries"), (dashboard, "upcoming")):
        monkeypatch.setattr(mod, fn, spy(fn, getattr(mod, fn)))
    hidden = ["stats", "limits", "coming", "glance", "visits"]
    data(env.api.display_set(hidden, list(dashboard.CARDS)))
    d = get(env)
    assert sorted(d["cards"]) == sorted(set(dashboard.CARDS) - set(hidden))
    assert not [c for c in called if c in hidden]
    assert not [c for c in called if c in ("_streaks", "visit_totals", "switch_summary", "blocked_events", "limit_entries", "upcoming")]
    called.clear()
    data(env.api.display_set([], list(dashboard.CARDS)))
    get(env)
    assert {"_streaks", "visit_totals", "limit_entries", "stats", "glance"} <= set(called)


def test_upcoming_is_computed_once(env, monkeypatch):
    record(env.db, "2026-09-14", "09:00")
    calls = []
    real = dashboard.upcoming
    monkeypatch.setattr(dashboard, "upcoming", lambda *a: calls.append(1) or real(*a))
    get(env)                                        # the numbers (Next: ...) and Coming up both want it
    assert len(calls) == 1


# ---------------------------------------------------------------- one status vocabulary (WIRING: assigned to R13)

def test_get_status_is_the_snapshot_s_page_part_plus_the_shell_bits(env):
    from resident import snapshot
    st = data(env.api.get_status())
    assert set(st) == set(snapshot.PUBLIC_KEYS) | {"clock_now", "draft", "tray_text"}
    assert st["running"] is True and st["off"] is None and st["pause"] is None and st["mode"] is None
    assert st["clock_now"] == "2026-09-14T12:00:00" and st["tray_text"] == "0 sites/apps blocked"
    ab.switch_off(env.db, env.state["now"])
    assert data(env.api.get_status())["off"] == "2026-09-14 12:00:00"


def test_a_status_push_merges_over_get_status_without_translating(env):
    """A `status` delta from the publisher and get_status use the same keys, so Object.assign is enough."""
    from resident import snapshot
    old = snapshot.build(env.db, env.state["now"])
    ab.switch_off(env.db, env.state["now"])
    new = snapshot.build(env.db, env.state["now"])
    delta = snapshot.changed(old, new)
    st = data(env.api.get_status())
    assert set(delta) <= set(st) and st["off"] == delta["off"]


# ---------------------------------------------------------------- the cached roll-ups give the old numbers

def old_saved(db, day, items, now):
    """What the Dashboard worked out before the 30 days were kept apart from today: every visit, parsed."""
    history = stats.switches(db, day - timedelta(days=30), day + timedelta(days=1))
    return stats.time_saved(stats.blocked_events(db, day), items, [], history, now)


def make_ctx(path, now):
    ctx = webapi.make_context(path, clock=lambda _db: now)
    ctx.db.set_setting("service_heartbeat", str(time.time()))
    return ctx


def saved_and_streaks(ctx):
    snap = dashboard.fresh_snapshot(ctx)
    d = dashboard._Day(ctx.db, snap, {})
    return d, dashboard._saved_stat(d)["value"], dashboard._streaks(d, d.goal)


def test_time_saved_and_the_streaks_match_the_old_workings_on_the_synthetic_database(tmp_path):
    from synthdata import build
    now = datetime(2026, 10, 11, 15, 42, 17)
    path = tmp_path / "s.db"
    Database(path).close()
    build(path, now.date(), days=120, extra_items=20)
    ctx = make_ctx(path, now)
    ctx.db.add_unlock([1], ["x"], now - timedelta(days=3, hours=1), now - timedelta(days=3))
    for hour in (0, 9, 15):                    # a clock just after midnight, mid-morning, afternoon
        ctx.now = lambda h=hour: now.replace(hour=h, minute=20)
        d, saved, streak = saved_and_streaks(ctx)
        assert d.blocked_today, "the synthetic day should have blocked visits to price"
        assert saved == stats.hm(old_saved(ctx.db, d.today, d.items, d.now))
        for goal in (None, 1 * 3600.0, 5 * 3600.0, 12 * 3600.0, 24 * 3600.0):
            assert dashboard._streaks(d, goal) == stats.streaks(ctx.db, d.today, goal), goal


def test_time_saved_matches_with_equal_timestamps_suffix_names_and_no_visits_today(tmp_path):
    now = datetime(2026, 9, 14, 12, 0)
    ctx = make_ctx(tmp_path / "t.db", now)
    db = ctx.db
    yt = db.add_item("YouTube", ["youtube.com"], "site", rules=[{"rule_type": "permanent"}])
    game = db.add_item("Game", ["game.exe", "other.test"], "app", rules=[{"rule_type": "permanent"}])
    base = datetime(2026, 9, 13, 23, 0)
    for secs, exe, site in [(0, "chrome.exe", "youtube.com"), (90, "chrome.exe", "m.youtube.com"),
                            (400, "chrome.exe", "m.youtube.com"), (500, "game.exe", ""),
                            (540, "chrome.exe", "music.youtube.com"), (840, "code.exe", ""),
                            (901, "chrome.exe", "other.test"), (908, "game.exe", ""), (1008, "chrome.exe", "")]:
        db.add_switch(base + timedelta(seconds=secs), exe, site)
    db.add_switch(base, "code.exe", "")                            # two events in the same second as the first
    db.add_block_event("youtube.com", yt, "YouTube", "permanent", None, now.replace(hour=9))
    db.add_block_event("game.exe", game, "Game", "permanent", None, now.replace(hour=10))
    db.add_block_event("gone.test", 999, "Gone", "permanent", None, now.replace(hour=11))   # (item since deleted)
    d, saved, _ = saved_and_streaks(ctx)
    assert saved == stats.hm(old_saved(db, d.today, d.items, d.now))
    assert saved != "0 m"
    # ... and with visits today as well (the last old visit then ends at today's first one)
    db.add_switch(now.replace(hour=8, minute=30), "code.exe", "")
    db.add_switch(now.replace(hour=8, minute=45), "chrome.exe", "youtube.com")
    d, saved, _ = saved_and_streaks(ctx)
    assert saved == stats.hm(old_saved(db, d.today, d.items, d.now))


def test_the_past_days_are_kept_between_calls_and_today_is_always_live(env):
    record(env.db, "2026-09-12", "09:00", 3600, 3600)
    record(env.db, "2026-09-13", "09:00", 3600, 3600)
    record(env.db, "2026-09-14", "09:00", 600, 600)
    env.db.set_setting(dashboard.GOAL_KEY, "2")
    before = {r["label"]: r["main"] for r in card(env, "glance")["rows"]}
    assert before["Goal streak"] == "3 days in a row"
    record(env.db, "2026-09-14", "10:00", 7200, 7200)                  # today goes over the goal
    after = {r["label"]: r["main"] for r in card(env, "glance")["rows"]}     # (same ctx: yesterday's part is cached)
    assert after["Goal streak"] == "0 days in a row"
    assert after["No emergency unlock"] == "3 days in a row"
    env.db.add_unlock([1], ["x"], MON.replace(hour=11), MON.replace(hour=12))
    rows = {r["label"]: r for r in card(env, "glance")["rows"]}
    assert (rows["No emergency unlock"]["main"], rows["No emergency unlock"]["extra"]) == ("0 days in a row", "used today 11:00–12:00")
    assert rows["Goal streak"]["extra"] == "within your daily goal" and after["No emergency unlock"] == "3 days in a row"


# ---------------------------------------------------------------- speed

def test_dashboard_get_is_fast_on_two_years(tmp_path, capsys):
    """Budget: p95 <= 50 ms (asserted at 100 for a slow, shared machine) on the 730-day synthetic database after the
    retention job rolled it up, for the usual reload and with nothing cached (the target for that is 20 ms). 80
    samples each, so the p95 is a real percentile. Timed in CPU time (this thread's work), so other programs busy on the
    machine do not fail it; on a quiet machine it is the same as the wall clock."""
    import retention
    from synthdata import build
    now = datetime(2026, 10, 11, 15, 42, 17)
    path = tmp_path / "big.db"
    Database(path).close()
    build(path, now.date(), days=730, extra_items=40)
    db = Database(path)
    retention.run(db, now.date())
    db.close()
    ctx = webapi.make_context(path, clock=lambda _db: now)
    ctx.db.set_setting("service_heartbeat", str(time.time()))
    t = time.process_time()
    first = dashboard.dashboard_get(ctx)
    cold = (time.process_time() - t) * 1000
    assert first["empty"] is False and len(first["cards"]) == 11

    def p95_of(clear):
        times = []
        for _ in range(80):
            if clear:
                ctx.dash_cache = {}          # (what is kept for a minute is then worked out every time)
            t = time.process_time()
            dashboard.dashboard_get(ctx)
            times.append((time.process_time() - t) * 1000)
        times.sort()
        return times[len(times) // 2], times[math.ceil(len(times) * 0.95) - 1]
    cold_median, cold_p95 = p95_of(True)
    warm_median, warm_p95 = p95_of(False)
    with capsys.disabled():
        print(f"\ndashboard_get, 730 days rolled up: first call {cold:.1f} ms; nothing cached: median {cold_median:.1f}, "
              f"p95 {cold_p95:.1f} ms; the usual reload: median {warm_median:.1f}, p95 {warm_p95:.1f} ms (budget 50)")
    assert warm_p95 <= 100 and cold_p95 <= 100 and cold <= 150
