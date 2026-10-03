"""A daily time limit set per weekday (0.84.12): e.g. 3 h Monday to Friday and 2 h at the weekend, or no limit on a day.

The day is the LIMIT day - with a 03:00 reset, Saturday 01:00 still belongs to Friday and gets Friday's amount. One
amount for every day stays the default and old rules read exactly as before. Weekly / monthly limits still stack.
Raising any day's amount, or clearing a day's limit, is loosening (Anti-Bypass); lowering one is free."""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

import antibypass as ab
from db import SCHEMA_VERSION, Database
from rules import (TIME_LIMIT_FIELDS, LimitClock, Usage, change_reset, counted_rules, day_limits, day_limits_text, describe_rule,
                   effective_rules, item_block, limit_targets, limit_weekday, limits, make_day_limits, next_block,
                   rule_block, usage_targets)

MON = datetime(2026, 9, 14)            # a Monday


def at(day, hh, mm=0):
    return MON + timedelta(days=day, hours=hh, minutes=mm)


FRI, SAT, SUN = 4, 5, 6
WEEKDAYS_3H_WEEKEND_2H = [180] * 5 + [120] * 2


def per_day(amounts, **more) -> dict:
    daily, days = make_day_limits(amounts)
    return {"rule_type": "time_limit", "daily_limit_min": daily, "daily_limit_days": days, **more}


def clock(t: str) -> LimitClock:
    return LimitClock(json.dumps({"time": t}))


def used(owner, bucket, seconds, c: LimitClock | None = None) -> Usage:
    return Usage({(owner, bucket): seconds}, c or LimitClock())


def _item(item_id, name, rules=()):
    return {"id": item_id, "display_name": name, "item_type": "app", "target": f"{name.lower()}.exe",
            "rules": [dict(r) for r in rules], "block_type": None, "app_path": None, "notify": None}


class Meter:
    """Usage as the tracker writes it: every second goes to every (owner, bucket) the rules name."""

    def __init__(self, groups, c: LimitClock | None = None):
        self.groups, self.data, self.clock = groups, {}, c or LimitClock()

    def use(self, item, now, seconds):
        for target in usage_targets(counted_rules(item, self.groups), item["id"], now, self.clock):
            self.data[target] = self.data.get(target, 0) + seconds

    @property
    def usage(self):
        return Usage(self.data, self.clock)

    def block(self, item, now):
        return item_block(effective_rules(item, self.groups), now, self.usage)


# ---------- storing it ----------

def test_one_amount_every_day_stays_one_amount():
    assert make_day_limits([120] * 7) == (120, None)              # how every limit was before - and still is
    assert make_day_limits([None] * 7) == (None, None)
    daily, days = make_day_limits(WEEKDAYS_3H_WEEKEND_2H)
    assert daily is None and json.loads(days) == WEEKDAYS_3H_WEEKEND_2H


def test_an_old_rule_reads_as_before():
    old = {"rule_type": "time_limit", "daily_limit_min": 60, "weekly_limit_min": 300}
    assert day_limits(old) is None
    assert limits(old, TIME_LIMIT_FIELDS) == limits(old, TIME_LIMIT_FIELDS, at(SAT, 12)) == {"day": 60, "week": 300}
    assert describe_rule(old, at(SAT, 12)) == "Limit: 0m / 1h 00m today\n0m / 5h 00m this week"


def test_it_is_saved_and_loaded_for_items_and_groups(tmp_path):
    db = Database(tmp_path / "t.db")
    rule = per_day(WEEKDAYS_3H_WEEKEND_2H)
    item_id = db.add_item("Game", ["game.exe"], "app", rules=[rule])
    db.add_group("Fun", [rule], {item_id: {"time_limit": per_day([60] * 5 + [None] * 2)}})
    saved = db.list_items()[0]["rules"][0]
    assert day_limits(saved) == WEEKDAYS_3H_WEEKEND_2H and saved["daily_limit_min"] is None
    group = db.list_groups()[0]
    assert day_limits(group["rules"][0]) == WEEKDAYS_3H_WEEKEND_2H
    assert day_limits(group["members"][item_id]["time_limit"]) == [60] * 5 + [None] * 2


def test_the_text_says_which_days():
    assert day_limits_text(WEEKDAYS_3H_WEEKEND_2H) == "3h 00m Mon–Fri, 2h 00m Sat–Sun"
    assert day_limits_text([60, 60, 90, 60, 60, 60, None]) == "1h 00m Mon–Tue, 1h 30m Wed, 1h 00m Thu–Sat, no limit Sun"



def test_an_older_database_gets_the_column_and_its_limits_unchanged(tmp_path):
    path = tmp_path / "t.db"
    db = Database(path)
    item_id = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    db.add_group("Fun", [{"rule_type": "time_limit", "daily_limit_min": 120}], {item_id: {}})
    db.close()
    raw = sqlite3.connect(path)
    for table in ("block_rules", "group_rules"):
        raw.execute(f"ALTER TABLE {table} DROP COLUMN daily_limit_days")
    raw.execute("PRAGMA user_version=5")
    raw.commit()
    raw.close()
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION >= 6
    [rule] = db.list_items()[0]["rules"]
    assert (rule["daily_limit_min"], rule["daily_limit_days"]) == (60, None)
    assert limits(rule, TIME_LIMIT_FIELDS, at(SAT, 12)) == {"day": 60}
    assert db.list_groups()[0]["rules"][0]["daily_limit_min"] == 120


# ---------- enforcing it ----------

def test_monday_3h_saturday_2h():
    rule = per_day(WEEKDAYS_3H_WEEKEND_2H, usage_owner="item:1")
    assert rule_block(rule, at(0, 20), used("item:1", "day:2026-09-14", 179 * 60)) is None
    assert rule_block(rule, at(0, 20), used("item:1", "day:2026-09-14", 180 * 60)) == ("limit", at(1, 0))
    assert rule_block(rule, at(SAT, 20), used("item:1", "day:2026-09-19", 119 * 60)) is None
    assert rule_block(rule, at(SAT, 20), used("item:1", "day:2026-09-19", 120 * 60)) == ("limit", at(SUN, 0))
    # 2.5 h is fine on a Monday and over the limit on a Saturday
    assert rule_block(rule, at(0, 20), used("item:1", "day:2026-09-14", 150 * 60)) is None
    assert rule_block(rule, at(SAT, 20), used("item:1", "day:2026-09-19", 150 * 60))[0] == "limit"


def test_reset_at_3am_a_day_belongs_to_the_weekday_it_started_on():
    """Friday 3 h, Saturday 2 h, reset 03:00: Saturday 01:00 is still Friday's day (3 h); Saturday's 2 h start
    at 03:00."""
    c = clock("03:00")
    assert limit_weekday(at(SAT, 1), c) == FRI and limit_weekday(at(SAT, 3), c) == SAT
    rule = per_day(WEEKDAYS_3H_WEEKEND_2H, usage_owner="item:1")
    fri_bucket = f"day:{c.period('day', at(SAT, 1))[0]}"
    assert fri_bucket == "day:2026-09-18T03:00"
    assert rule_block(rule, at(SAT, 1), used("item:1", fri_bucket, 150 * 60, c)) is None   # 2.5 h < Friday's 3 h
    assert rule_block(rule, at(SAT, 2), used("item:1", fri_bucket, 180 * 60, c)) == ("limit", at(SAT, 3))
    sat_bucket = f"day:{c.period('day', at(SAT, 12))[0]}"
    assert rule_block(rule, at(SAT, 12), used("item:1", sat_bucket, 119 * 60, c)) is None
    assert rule_block(rule, at(SAT, 12), used("item:1", sat_bucket, 120 * 60, c)) == ("limit", at(SUN, 3))
    # and Sunday night after midnight is still Sunday (2 h), not Monday (3 h)
    sun_bucket = f"day:{c.period('day', at(7, 1))[0]}"
    assert rule_block(rule, at(7, 1), used("item:1", sun_bucket, 120 * 60, c)) == ("limit", at(7, 3))



@pytest.mark.parametrize("new_time", ["03:00", "11:00", "12:00", "13:00", "23:00"])
def test_a_later_reset_time_never_starts_the_next_weekday_early(new_time):
    """Friday 1 h used up, Saturday 5 h, reset at midnight: moving the reset later (free, no challenge) stretches
    Friday's day - it stays Friday's 1 h, still used up, until the new time on Saturday."""
    rule = per_day([60] * 5 + [300] * 2, usage_owner="item:1")
    now = at(FRI, 20)
    c = LimitClock(change_reset(None, new_time, now))
    hours, minutes = map(int, new_time.split(":"))
    assert limit_weekday(now, c) == FRI and limit_weekday(at(SAT, hours, minutes) - timedelta(minutes=1), c) == FRI
    assert rule_block(rule, now, used("item:1", "day:2026-09-18", 60 * 60, c)) == ("limit", at(SAT, hours, minutes))


def test_the_tracker_counts_and_flushes_by_todays_amount():
    m = Meter([], clock("03:00"))
    game = _item(1, "Game", [per_day(WEEKDAYS_3H_WEEKEND_2H)])
    rules = counted_rules(game, [])
    assert limit_targets(rules, at(SAT, 1), m.clock) == {("item:1", "day:2026-09-18T03:00"): 180 * 60}
    assert limit_targets(rules, at(SAT, 4), m.clock) == {("item:1", "day:2026-09-19T03:00"): 120 * 60}
    m.use(game, at(SAT, 4), 119 * 60)
    assert m.block(game, at(SAT, 6)) is None
    m.use(game, at(SAT, 6), 60)
    assert m.block(game, at(SAT, 6))[:2] == ("limit", at(SUN, 3))


def test_a_day_without_a_limit():
    rule = per_day([180] * 6 + [None], usage_owner="item:1")
    hours = used("item:1", "day:2026-09-20", 10 * 3600)
    assert rule_block(rule, at(SUN, 22), hours) is None
    assert next_block([rule], at(SUN, 22), hours, in_use=True) is None
    assert describe_rule(rule, at(SUN, 22), hours) == "Limit: no limit today"
    assert rule_block(rule, at(0, 22), used("item:1", "day:2026-09-14", 180 * 60))[0] == "limit"
    # Sunday's time is still counted in the day bucket (so a limit added later today knows what was used)
    assert ("item:1", "day:2026-09-20") in usage_targets([rule], 1, at(SUN, 12))


def test_a_weekly_limit_still_stacks():
    game = _item(1, "Game", [per_day(WEEKDAYS_3H_WEEKEND_2H, weekly_limit_min=600)])
    m = Meter([])
    for day in range(3):                                  # Mon-Wed: 3 h a day = 9 h
        m.use(game, at(day, 10), 180 * 60)
        assert m.block(game, at(day, 14))[:2] == ("limit", at(day + 1, 0))
    m.use(game, at(3, 10), 59 * 60)
    assert m.block(game, at(3, 12)) is None               # Thursday: 59 min of 3 h, 9h59 of 10 h
    m.use(game, at(3, 12), 60)
    assert m.block(game, at(3, 12))[:2] == ("limit", at(7, 0))   # the week's 10 h: blocked until next Monday
    assert describe_rule(effective_rules(game, [])[0], at(3, 12), m.usage) == \
        "Limit: 1h 00m / 3h 00m today\n10h 00m / 10h 00m this week"


def test_next_block_and_warnings_use_todays_amount():
    rule = per_day(WEEKDAYS_3H_WEEKEND_2H, usage_owner="item:1")
    hour = used("item:1", "day:2026-09-19", 60 * 60)
    assert next_block([rule], at(SAT, 12), hour, in_use=True)[0] == at(SAT, 13)       # 2 h - 1 h
    hour = used("item:1", "day:2026-09-14", 60 * 60)
    assert next_block([rule], at(0, 12), hour, in_use=True)[0] == at(0, 14)           # 3 h - 1 h


def test_group_and_member_per_weekday():
    """The group's limit per weekday is one pot; a member's own per-weekday limit comes on top (0.84.3)."""
    youtube, twitch = _item(1, "YouTube"), _item(2, "Twitch")
    groups = [{"id": 7, "name": "Fun", "rules": [per_day([120] * 5 + [180] * 2)],
               "members": {1: {"time_limit": per_day([60] * 5 + [None] * 2)}, 2: {}}}]
    m = Meter(groups)
    m.use(youtube, at(0, 10), 60 * 60)                    # Monday: YouTube's own hour
    assert m.block(youtube, at(0, 11))[0] == "limit"
    assert m.block(twitch, at(0, 11)) is None             # the group's 2 h aren't used up
    m.use(twitch, at(0, 11), 60 * 60)
    assert m.block(twitch, at(0, 12))[0] == "limit"       # 1 h + 1 h = the group's Monday 2 h
    m.use(youtube, at(SAT, 10), 170 * 60)                 # Saturday: no own limit, the group has 3 h
    assert m.block(youtube, at(SAT, 13)) is None
    m.use(youtube, at(SAT, 13), 10 * 60)
    assert m.block(youtube, at(SAT, 13))[:2] == ("limit", at(SUN, 0))
    assert m.block(twitch, at(SAT, 13))[0] == "limit"
    texts = [describe_rule(r, at(SAT, 13), m.usage) for r in effective_rules(youtube, groups)]
    assert texts == ["Limit (group): 3h 00m / 3h 00m today", "Limit (own): no limit today"]


# ---------- what it shows ----------

def test_dashboard_and_web_view_show_todays_amount(tmp_path, monkeypatch):
    from gui.dashboard import upcoming
    import webview_app
    monkeypatch.setenv("LOCKDOWN_DATA_DIR", str(tmp_path))
    db = Database(tmp_path / "config.db")
    item_id = db.add_item("Game", ["game.exe"], "app", rules=[per_day(WEEKDAYS_3H_WEEKEND_2H)])
    sat = at(SAT, 12)
    usage = Usage({(f"item:{item_id}", "day:2026-09-19"): 100 * 60})
    out = upcoming(sat, db.list_items(), db.list_groups(), usage)
    assert [(e["kind"], e["left"]) for e in out] == [("limit", 20 * 60)]       # 2 h - 1h40, not 3 h - 1h40
    monkeypatch.setattr(webview_app, "now_from_db", lambda db: sat)
    api = webview_app.Api.__new__(webview_app.Api)
    api.window, api.db = None, db
    row = api.get_state()["rules"][0]
    assert row["limitType"] == "daily" and row["limit"] == 120
    # toggling it in the web view keeps the per-weekday amounts
    assert api.save_rule({**row, "enabled": False})["ok"]
    assert day_limits(db.list_items()[0]["rules"][0]) == WEEKDAYS_3H_WEEKEND_2H



def test_the_web_view_keeps_a_weekday_limit_whatever_it_shows_today(tmp_path, monkeypatch):
    """A Sunday without a daily limit shows the weekly one (or "off"): toggling the item there must not turn the
    per-weekday amounts into that."""
    import webview_app
    db = Database(tmp_path / "t.db")
    db.add_item("Game", ["game.exe"], "app", rules=[per_day([180] * 6 + [None], weekly_limit_min=900)])
    db.add_item("Chat", ["chat.exe"], "app", rules=[per_day([180] * 6 + [None])])
    monkeypatch.setattr(webview_app, "now_from_db", lambda db: at(SUN, 12))
    api = webview_app.Api.__new__(webview_app.Api)
    api.window, api.db = None, db
    rows = api.get_state()["rules"]
    assert {r["n"]: (r["limitType"], r["limit"]) for r in rows} == {"Game": ("weekly", 900), "Chat": ("off", 0)}
    for row in rows:
        assert api.save_rule({**row, "enabled": False})["ok"]
    game, chat = ({i["display_name"]: i["rules"][0] for i in db.list_items()}[n] for n in ("Game", "Chat"))
    assert day_limits(game) == day_limits(chat) == [180] * 6 + [None] and game["weekly_limit_min"] == 900


# ---------- Anti-Bypass ----------

NOW = at(2, 12)


def looser(old, new) -> bool:
    return ab.rule_looser(old, new, NOW)


def test_raising_saturday_only_needs_the_challenge():
    old = per_day(WEEKDAYS_3H_WEEKEND_2H)
    assert looser(old, per_day([180] * 5 + [150, 120]))          # Saturday 2 h -> 2h30
    assert not looser(old, per_day([180] * 5 + [90, 120]))       # Saturday 2 h -> 1h30: free
    assert not looser(old, per_day([120] * 7))                   # every day lower (stored as one amount): free
    assert looser(old, per_day([180] * 5 + [None, 120]))         # Saturday's limit cleared
    assert looser(old, per_day([180] * 7))                       # one amount, higher than the weekend's
    assert not looser(old, old)


def test_from_one_amount_to_per_weekday():
    old = {"rule_type": "time_limit", "daily_limit_min": 180}
    assert not looser(old, per_day(WEEKDAYS_3H_WEEKEND_2H))      # weekend lowered: free
    assert looser(old, per_day([180] * 6 + [None]))              # Sunday without a limit
    assert looser(old, per_day([180] * 5 + [240, 120]))          # Saturday raised
    assert not looser({"rule_type": "time_limit", "weekly_limit_min": 600},     # adding a day limit: free
                      per_day(WEEKDAYS_3H_WEEKEND_2H, weekly_limit_min=600))
    assert looser(per_day(WEEKDAYS_3H_WEEKEND_2H, weekly_limit_min=600),       # weekly still checked
                  per_day(WEEKDAYS_3H_WEEKEND_2H, weekly_limit_min=700))


def test_a_groups_and_a_members_weekday_amounts_are_guarded():
    yt = {**_item(1, "YouTube"), "target": "youtube.exe"}
    group = {"id": 5, "name": "Fun", "rules": [per_day(WEEKDAYS_3H_WEEKEND_2H)],
             "members": {1: {"time_limit": per_day([60] * 7)}}}
    raised = {**group, "rules": [per_day([180] * 5 + [150, 120])]}
    assert ab.draft_changes({1: yt}, {1: yt}, {5: group}, {5: raised}, NOW) == ["Loosen group Fun"]
    lowered = {**group, "rules": [per_day([180] * 5 + [60, 120])]}
    assert ab.draft_changes({1: yt}, {1: yt}, {5: group}, {5: lowered}, NOW) == []
    member_sat = {**group, "members": {1: {"time_limit": per_day([60] * 5 + [90, 60])}}}
    assert ab.draft_changes({1: yt}, {1: yt}, {5: group}, {5: member_sat}, NOW) == ["Loosen group Fun"]
    member_less = {**group, "members": {1: {"time_limit": per_day([60] * 5 + [30, 30])}}}
    assert ab.draft_changes({1: yt}, {1: yt}, {5: group}, {5: member_less}, NOW) == []


# ---------- the editor ----------

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


def test_the_editor_round_trips_a_weekday_grid(tk_root):
    from gui.rule_editors import LimitEditor, summary
    editor = LimitEditor(tk_root)
    assert editor.value()["daily_limit_min"] == 30 and editor.same.get()       # default: one amount
    editor.same.deselect()
    editor._same_changed()                                                    # every day starts with 30m
    assert [e.get() for e in editor.day_entries] == ["30m"] * 7
    editor.fill.delete(0, "end")
    editor.fill.insert(0, "3h")
    editor._fill(range(5))
    editor.fill.delete(0, "end")
    editor.fill.insert(0, "2h")
    editor._fill(range(5, 7))
    rule = editor.value()
    assert rule["daily_limit_min"] is None and day_limits(rule) == WEEKDAYS_3H_WEEKEND_2H
    assert summary("time_limit", editor) == "per day 3h Mon–Fri, 2h Sat–Sun"   # (written as the boxes are)
    editor.day_entries[SUN].delete(0, "end")                                  # Sunday: no limit
    assert day_limits(editor.value())[SUN] is None
    editor.load(per_day(WEEKDAYS_3H_WEEKEND_2H, weekly_limit_min=600))
    assert not editor.same.get() and editor.value()["weekly_limit_min"] == 600
    assert day_limits(editor.value()) == WEEKDAYS_3H_WEEKEND_2H
    editor.load({"rule_type": "time_limit", "daily_limit_min": 45})
    assert editor.same.get() and editor.value()["daily_limit_min"] == 45 and editor.value()["daily_limit_days"] is None
    editor.same.deselect()
    editor._same_changed()
    for e in editor.day_entries:                                              # every day the same again: one amount
        e.delete(0, "end")
        e.insert(0, "1h")
    assert editor.value()["daily_limit_min"] == 60 and editor.value()["daily_limit_days"] is None
    editor.day_entries[SAT].delete(0, "end")
    editor.day_entries[SAT].insert(0, "25h")
    with pytest.raises(ValueError, match="on Saturday"):
        editor.value()
