"""Something blocked is shown once, as "Blocked (until ...)", without the time used or left of its limits.

Before, a group with a shared limit and a member with its own limit on top showed the same block 2-3 times on
the Dashboard (the group's limit, its allowance, each member's own limit), each with used / left times that
could no longer be spent ("20m left" on a member its group had already blocked)."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from db import Database
from rules import (TIME_FMT, Usage, allowance_note, block_end, blocked_text, counted_rules, describe_rule,
                   effective_rules, make_schedule, usage_targets)

MON = datetime(2026, 9, 14)            # a Monday
EVERY_DAY = list(range(7))


def at(h, m=0):
    return MON.replace(hour=h, minute=m)


def _item(item_id, name):
    return {"id": item_id, "display_name": name, "item_type": "app", "target": f"{name.lower()}.exe",
            "rules": [], "block_type": None, "app_path": None, "notify": None}


YOUTUBE, TWITCH = _item(1, "YouTube"), _item(2, "Twitch")
GROUP_2H = {"rule_type": "time_limit", "daily_limit_min": 120}
OWN_1H = {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 60}}


def _fun(rules, youtube_extra=None):
    return [{"id": 7, "name": "Fun", "rules": rules, "members": {1: youtube_extra or {}, 2: {}}}]


def _used(groups, *uses):
    """Usage as the tracker writes it: (item, at, seconds)."""
    data = {}
    for item, now, seconds in uses:
        for target in usage_targets(counted_rules(item, groups), item["id"], now):
            data[target] = data.get(target, 0) + seconds
    return Usage(data)


def _rows(now, groups, usage):
    from gui.dashboard import limit_entries
    return [(e["name"], e["blocked"]) for e in limit_entries(now, [YOUTUBE, TWITCH], groups, usage)]


def test_the_blocked_line():
    assert blocked_text(at(21), at(12)) == "Blocked (until 21:00)"
    assert blocked_text(at(21).replace(day=15), at(12)) == "Blocked (until Tue 21:00)"
    assert blocked_text(None, at(12)) == blocked_text(datetime.max, at(12)) == "Blocked"


def test_a_block_a_week_or_more_away_says_the_date():
    assert blocked_text(MON.replace(day=19, hour=7), at(12)) == "Blocked (until Sat 07:00)"     # 5 days on
    assert blocked_text(MON.replace(day=21), at(12)) == "Blocked (until Mon 21 Sep 00:00)"     # a weekly limit
    assert blocked_text(datetime(2026, 10, 1), at(12)) == "Blocked (until Thu 1 Oct 00:00)"     # a monthly limit


def test_the_line_says_the_block_that_lasts_longest():
    temp = {"rule_type": "temporary", "temp_until": at(15).strftime(TIME_FMT)}
    item = {**_item(3, "Game"), "rules": [temp, {"rule_type": "time_limit", "daily_limit_min": 60}]}
    rules = effective_rules(item, [])
    used_up = _used([], (item, at(10), 60 * 60))
    assert block_end(rules, at(12), used_up) == at(0).replace(day=15)     # not 15:00: the limit holds till 00:00
    assert block_end(rules, at(12), Usage({})) == at(15)
    assert block_end(rules, at(16), Usage({})) is None


# ---------- Dashboard ----------

def test_nothing_blocked_shows_each_limit_with_its_times():
    groups = _fun([GROUP_2H], OWN_1H)
    usage = _used(groups, (YOUTUBE, at(10), 40 * 60), (TWITCH, at(10), 30 * 60))
    assert _rows(at(12), groups, usage) == [("YouTube (own, in Fun)", None), ("Fun (group)", None)]


def test_a_group_used_up_is_one_row_for_all_of_it():
    groups = _fun([GROUP_2H], OWN_1H)
    usage = _used(groups, (YOUTUBE, at(10), 40 * 60), (TWITCH, at(10), 80 * 60))   # the group's 2 h are gone
    assert _rows(at(12), groups, usage) == [("Fun (group)", "Blocked (until Tue 00:00)")]   # not YouTube's 20m left


def test_a_member_at_its_own_limit_is_its_own_blocked_row():
    groups = _fun([GROUP_2H], OWN_1H)
    usage = _used(groups, (YOUTUBE, at(10), 60 * 60))
    rows = _rows(at(12), groups, usage)
    assert rows == [("YouTube (own, in Fun)", "Blocked (until Tue 00:00)"), ("Fun (group)", None)]


def test_a_member_blocked_longer_by_its_own_limit_keeps_its_own_row():
    groups = _fun([GROUP_2H], {"time_limit": {"rule_type": "time_limit", "weekly_limit_min": 60}})
    usage = _used(groups, (YOUTUBE, at(10), 60 * 60), (TWITCH, at(10), 60 * 60))   # the group's 2 h and YouTube's week
    assert _rows(at(12), groups, usage) == [("Fun (group)", "Blocked (until Tue 00:00)"),
                                            ("YouTube (own, in Fun)", "Blocked (until Mon 21 Sep 00:00)")]


def test_a_loose_item_s_limit_row_says_when_the_item_is_allowed_again():
    night = {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "22:00", "07:00")])}
    item = {**_item(3, "Game"), "rules": [night, {"rule_type": "time_limit", "daily_limit_min": 60}]}
    from gui.dashboard import limit_entries
    rows = limit_entries(at(23), [item], [], _used([], (item, at(10), 60 * 60)))   # its limit ends at 00:00
    assert [(e["name"], e["blocked"]) for e in rows] == [("Game", "Blocked (until Tue 07:00)")]


def test_a_group_s_allowance_and_limit_blocked_together_are_one_row():
    night = {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "22:00", "07:00")]),
             "allowance_min": 15, "allowance_shared": 1}
    groups = _fun([night, GROUP_2H], OWN_1H)
    usage = _used(groups, (YOUTUBE, at(22, 30), 15 * 60))            # the allowance is used up
    assert _rows(at(23), groups, usage) == [("Fun (group)", "Blocked (until Tue 07:00)")]
    usage = _used(groups, (YOUTUBE, at(22, 30), 5 * 60))              # 10 min of it left: all three rows
    assert [n for n, b in _rows(at(23), groups, usage) if b is None] == \
        ["Fun (group)", "YouTube (own, in Fun)", "Fun (group)"]


def test_a_loose_item_blocked_by_its_hours_drops_its_limit_times():
    night = {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "22:00", "07:00")])}
    item = {**_item(3, "Game"), "rules": [night, {"rule_type": "time_limit", "daily_limit_min": 60}]}
    from gui.dashboard import limit_entries
    rows = limit_entries(at(23), [item], [], _used([], (item, at(10), 10 * 60)))
    assert [(e["name"], e["blocked"]) for e in rows] == [("Game", "Blocked (until Tue 07:00)")]


def test_an_emergency_unlock_shows_the_times_again():
    groups = _fun([GROUP_2H], OWN_1H)
    usage = _used(groups, (YOUTUBE, at(10), 60 * 60))
    usage.unlocks = {"item:1": at(12, 10)}
    assert [b for _, b in _rows(at(12), groups, usage)] == [None, None]


# ---------- Blocking list ----------

def test_a_limit_chip_on_something_blocked_has_no_used_time():
    from gui.blocking import _paint_chip
    groups = _fun([GROUP_2H], OWN_1H)
    usage = _used(groups, (YOUTUBE, at(10), 40 * 60), (TWITCH, at(10), 80 * 60))
    rules = effective_rules(YOUTUBE, groups)
    painted = []
    chip = SimpleNamespace(configure=lambda **kw: painted.append(kw["text"].strip()))
    for rule in rules:
        _paint_chip(chip, rule, at(12), usage, siblings=rules)
    assert painted == ["→ Fun · Limit (group): 2h 00m today", "→ Fun · Limit (own): 1h 00m today"]
    painted.clear()
    usage = _used(groups, (YOUTUBE, at(10), 40 * 60))                 # not blocked: the times are there
    for rule in rules:
        _paint_chip(chip, rule, at(12), usage, siblings=rules)
    assert painted == ["→ Fun · Limit (group): 40m / 2h 00m today", "→ Fun · Limit (own): 40m / 1h 00m today"]


def test_the_texts_without_used_times():
    switches = {"rule_type": "switch_limit", "daily_switch_limit": 3, "switch_mode": "opens"}
    assert describe_rule(switches, at(12), Usage({}), used=False).endswith(": 3 today")
    allowance = {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "22:00", "07:00")]),
                 "allowance_min": 15}
    assert allowance_note(allowance, at(23), Usage({}), used=False) == "+ 15 min allowed during blocked hours"
    assert "left until 07:00" in allowance_note(allowance, at(23), Usage({}))


def test_the_status_says_until_when_once():
    from gui.blocking import status_of
    groups = _fun([GROUP_2H], OWN_1H)
    draft = SimpleNamespace(saved_items={1: YOUTUBE, 2: TWITCH}, saved_groups={7: groups[0]},
                            item_not_applied=lambda i: False)
    page = SimpleNamespace(draft=draft, app=SimpleNamespace(service_running=True))
    usage = _used(groups, (TWITCH, at(10), 120 * 60))
    assert status_of(page, YOUTUBE, at(12), usage)["text"] == "● Blocked (until Tue 00:00)"
    assert status_of(page, YOUTUBE, at(12), Usage({}))["text"] == "● Allowed now"


# ---------- web view ----------

def test_the_web_view_shows_blocked_once_per_group(tmp_path, monkeypatch):
    import webview_app
    monkeypatch.setattr(webview_app, "now_from_db", lambda _db: at(12))
    db = Database(tmp_path / "t.db")
    yt = db.add_item("YouTube", ["yt.exe"], "app")
    tw = db.add_item("Twitch", ["tw.exe"], "app")
    solo = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    gid = db.add_group("Fun", [GROUP_2H], {yt: {}, tw: {}})
    db.add_usage([(f"group:{gid}", "day:2026-09-14")], 120 * 60, MON.date())
    db.add_usage([(f"item:{solo}", "day:2026-09-14")], 10 * 60, MON.date())
    rows = {r["n"]: (r["blocked"], r["blockedBy"]) for r in webview_app._rules_for_ui(db)}
    assert rows == {"YouTube": ("Blocked (until Tue 00:00)", "Fun"), "Twitch": ("Blocked (until Tue 00:00)", "Fun"),
                    "Game": ("", "")}


@pytest.fixture
def tk_root():
    import customtkinter as ctk
    try:
        root = ctk.CTk()
    except Exception as e:   # (no display)
        pytest.skip(f"no Tk display: {e}")
    root.withdraw()
    yield root
    root.destroy()


def test_the_dashboard_card_paints_one_blocked_line(tk_root):  # (needs a display; skipped without one)
    from gui.components import Rows
    from gui.dashboard import DashboardPage, _limit_row
    page = SimpleNamespace(limits=SimpleNamespace(note=SimpleNamespace(configure=lambda **kw: None)),
                           limit_rows=Rows(tk_root, _limit_row))
    groups = _fun([GROUP_2H], OWN_1H)
    usage = _used(groups, (YOUTUBE, at(10), 40 * 60), (TWITCH, at(10), 80 * 60))
    DashboardPage._limits(page, at(12), [YOUTUBE, TWITCH], groups, usage)
    rows = page.limit_rows.items[:page.limit_rows.packed]
    assert [(r.name.cget("text").strip(), r.text.cget("text"), r.left.cget("text")) for r in rows] == \
        [("Fun (group)", "", "Blocked (until Tue 00:00)")]
    DashboardPage._limits(page, at(12), [YOUTUBE, TWITCH], groups, _used(groups, (YOUTUBE, at(10), 40 * 60)))
    rows = page.limit_rows.items[:page.limit_rows.packed]
    assert [(r.name.cget("text").strip(), r.left.cget("text")) for r in rows] == \
        [("YouTube (own, in Fun)", "20 m left"), ("Fun (group)", "1 h 20 m left")]   # (stats.hm)


def test_the_web_view_names_the_group_only_when_it_blocks_every_member(tmp_path, monkeypatch):
    import webview_app
    db = Database(tmp_path / "t.db")
    yt = db.add_item("YouTube", ["yt.exe"], "app")
    tw = db.add_item("Twitch", ["tw.exe"], "app")
    night = {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "22:00", "07:00")]),
             "allowance_min": 15, "allowance_shared": 0}                  # each member has its own 15 min
    db.add_group("Fun", [night], {yt: {}, tw: {}})
    groups, items = db.list_groups(), {i["display_name"]: i for i in db.list_items()}
    usage = _used(groups, (items["YouTube"], at(22, 30), 15 * 60))        # only YouTube's is used up
    monkeypatch.setattr(webview_app, "now_from_db", lambda _db: at(23))
    monkeypatch.setattr(db, "usage_lookup", lambda _now: usage)
    rows = {r["n"]: (r["blocked"], r["blockedBy"]) for r in webview_app._rules_for_ui(db)}
    assert rows == {"YouTube": ("Blocked (until Tue 07:00)", ""), "Twitch": ("", "")}
    usage = _used(groups, (items["YouTube"], at(22, 30), 15 * 60), (items["Twitch"], at(22, 30), 15 * 60))
    monkeypatch.setattr(db, "usage_lookup", lambda _now: usage)
    assert {r["n"]: r["blockedBy"] for r in webview_app._rules_for_ui(db)} == {"YouTube": "Fun", "Twitch": "Fun"}
    usage.unlocks = {f"item:{tw}": at(23, 10)}                            # an emergency unlock frees one
    assert {r["n"]: (r["blocked"], r["blockedBy"]) for r in webview_app._rules_for_ui(db)} == \
        {"YouTube": ("Blocked (until Tue 07:00)", ""), "Twitch": ("", "")}
