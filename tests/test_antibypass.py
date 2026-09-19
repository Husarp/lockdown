from datetime import datetime, timedelta

import antibypass as ab
from db import Database
from trusted_time import ZONE_DELAY_SEC, zone_step

NOW = datetime(2026, 9, 20, 19, 0)   # a Sunday, 19:00
SUN_EVENING = [{"days": [6], "start": "18:00", "end": "20:00"}]


def test_status_phrase_hours_and_unlock(tmp_path):
    db = Database(tmp_path / "t.db")
    assert ab.status(ab.settings(db), NOW) == "free"                     # off by default
    ab.save(db, {**ab.settings(db), "phrase": True})
    assert ab.status(ab.settings(db), NOW) == "phrase"
    ab.unlock(db, NOW)
    cfg = ab.settings(db)
    assert ab.status(cfg, NOW + timedelta(minutes=4)) == "free"
    assert ab.status(cfg, NOW + timedelta(minutes=ab.UNLOCK_MIN + 1)) == "phrase"
    ab.lock(db)
    assert ab.status(ab.settings(db), NOW) == "phrase"
    ab.save(db, {**ab.settings(db), "phrase": False, "hours": True, "windows": SUN_EVENING})
    cfg = ab.settings(db)
    assert ab.status(cfg, NOW) == "free"                                 # inside the hours
    assert ab.status(cfg, NOW + timedelta(hours=2)) == "closed"
    assert ab.next_hours(cfg, NOW + timedelta(hours=2)) == datetime(2026, 9, 27, 18, 0)
    cfg["phrase"] = True                                                 # both: inside the hours, still the phrase
    assert ab.status(cfg, NOW) == "phrase"


def test_phrase():
    p = ab.new_phrase(60)
    assert len(p.replace(" ", "")) == 60 and all(len(g) == 5 for g in p.split())
    assert p != ab.new_phrase(60)


def test_rule_looser():
    tl = {"rule_type": "time_limit", "daily_limit_min": 60, "weekly_limit_min": None}
    assert not ab.rule_looser(tl, {**tl, "daily_limit_min": 30}, NOW)         # stricter
    assert not ab.rule_looser(tl, {**tl, "weekly_limit_min": 300}, NOW)       # adding a limit
    assert ab.rule_looser(tl, {**tl, "daily_limit_min": 90}, NOW)
    assert ab.rule_looser(tl, {**tl, "daily_limit_min": None, "weekly_limit_min": 300}, NOW)
    assert ab.rule_looser(tl, None, NOW)                                        # removed
    sw = {"rule_type": "switch_limit", "daily_switch_limit": 5, "switch_mode": "visit", "visit_gap_min": 5}
    assert not ab.rule_looser(sw, {**sw, "daily_switch_limit": 3, "visit_gap_min": 2}, NOW)
    assert ab.rule_looser(sw, {**sw, "visit_gap_min": 30}, NOW)
    temp = {"rule_type": "temporary", "temp_until": "2026-09-20 21:00:00"}
    assert not ab.rule_looser(temp, {"rule_type": "temporary", "duration_min": 180}, NOW)   # ends 22:00: longer
    assert ab.rule_looser(temp, {"rule_type": "temporary", "duration_min": 30}, NOW)        # ends 19:30
    hours = {"rule_type": "scheduled", "schedule": "{}", "allowance_min": 10}
    assert not ab.rule_looser(hours, {**hours, "allowance_min": 5}, NOW)
    assert ab.rule_looser(hours, {**hours, "allowance_min": 20}, NOW)
    assert ab.rule_looser(hours, {**hours, "schedule": '{"x": 1}'}, NOW)        # other hours
    assert not ab.rule_looser({"rule_type": "permanent"}, {"rule_type": "permanent"}, NOW)


def test_draft_changes():
    yt = {"id": 1, "display_name": "YouTube", "target": "youtube.com youtu.be", "item_type": "site",
          "rules": [{"rule_type": "time_limit", "daily_limit_min": 60}]}
    game = {"id": 2, "display_name": "Game", "target": "game.exe", "item_type": "app", "block_type": "close,internet",
            "rules": [{"rule_type": "permanent"}]}
    saved = {1: yt, 2: game}
    group = {"id": 5, "name": "Social", "rules": [{"rule_type": "time_limit", "daily_limit_min": 30}],
             "members": {1: None, 2: None}}
    same = ab.draft_changes(saved, dict(saved), {5: group}, {5: group}, NOW)
    assert same == []
    new_item = {"id": -1, "display_name": "X", "target": "x.com", "item_type": "site", "rules": []}
    assert ab.draft_changes(saved, {**saved, -1: new_item}, {}, {}, NOW) == []           # adding is free
    assert ab.draft_changes(saved, {2: game}, {}, {}, NOW) == ["Remove YouTube"]
    assert ab.draft_changes(saved, {1: {**yt, "target": "youtube.com"}, 2: game}, {}, {}, NOW) == ["Loosen YouTube"]
    assert ab.draft_changes(saved, {1: yt, 2: {**game, "block_type": "minimize"}}, {}, {}, NOW) == ["Loosen Game"]
    assert ab.draft_changes(saved, {1: yt, 2: {**game, "block_type": "close,background,internet"}}, {}, {}, NOW) == []
    minimized = {**game, "block_type": "minimize"}
    assert ab.draft_changes({2: minimized}, {2: {**game, "block_type": "close"}}, {}, {}, NOW) == []   # stronger
    assert ab.draft_changes(saved, saved, {5: group}, {}, NOW) == ["Remove group Social"]
    fewer = {**group, "members": {1: None}}
    assert ab.draft_changes(saved, saved, {5: group}, {5: fewer}, NOW) == ["Loosen group Social"]
    custom = {**group, "members": {1: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 90}}, 2: None}}
    assert ab.draft_changes(saved, saved, {5: group}, {5: custom}, NOW) == ["Loosen group Social"]
    stricter = {**group, "members": {1: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 10}}, 2: None}}
    assert ab.draft_changes(saved, saved, {5: group}, {5: stricter}, NOW) == []


def test_settings_emergency_protection_looser():
    base = {**ab.DEFAULTS, "phrase": True, "length": 60, "hours": True, "windows": SUN_EVENING}
    assert not ab.settings_looser(base, {**base, "length": 120})
    assert ab.settings_looser(base, {**base, "length": 30})
    assert ab.settings_looser(base, {**base, "phrase": False})
    assert ab.settings_looser(base, {**base, "windows": [{"days": [5, 6], "start": "18:00", "end": "20:00"}]})
    assert not ab.settings_looser({**base, "hours": False}, base)                          # turning hours on
    em = {"emergency.enabled": "1", "emergency.minutes": "20", "emergency.uses": "3", "emergency.per": "week"}
    assert not ab.emergency_looser(em, {**em, "emergency.uses": "2", "emergency.minutes": "10"})
    assert not ab.emergency_looser(em, {**em, "emergency.enabled": "0"})
    assert ab.emergency_looser(em, {**em, "emergency.uses": "5"})
    assert ab.emergency_looser(em, {**em, "emergency.per": "day"})
    assert ab.emergency_looser({**em, "emergency.enabled": "0"}, em)
    pr = {"enabled": ["scam", "adult"], "allowed": ["a.com"]}
    assert not ab.protection_looser(pr, {"enabled": ["scam", "adult", "gambling"], "allowed": []})
    assert ab.protection_looser(pr, {"enabled": ["scam"], "allowed": ["a.com"]})
    assert ab.protection_looser(pr, {"enabled": ["scam", "adult"], "allowed": ["a.com", "b.com"]})


def test_draft_save_goes_through_the_guard(tmp_path):
    from gui.draft import Draft
    db = Database(tmp_path / "t.db")
    draft = Draft(db)
    item = draft.add_item("Reddit", ["reddit.com"], "manual")
    draft.set_rule(item["id"], {"rule_type": "permanent"})                  # adding: saved at once (auto-save)
    assert len(db.list_items()) == 1
    asked = []
    draft.guard = lambda changes, proceed, cancel: asked.append((changes, proceed, cancel))
    saved_id = db.list_items()[0]["id"]
    draft.remove_item(saved_id)
    assert asked and asked[0][0] == ["Remove Reddit"] and len(db.list_items()) == 1   # not removed yet
    asked[0][2]()                                                           # cancelled: the edit is undone
    assert saved_id in draft.items and len(db.list_items()) == 1
    draft.remove_item(saved_id)
    asked[1][1]()                                                           # passed: saved
    assert db.list_items() == []


def test_time_zone_change_counts_after_24_hours():
    state, shift, msg = zone_step(None, "Central European Standard Time", 7200, 0)
    assert shift == 0 and msg is None
    state, shift, _ = zone_step(state, "Central European Standard Time", 3600, 100)     # winter time: follows
    assert shift == 0 and state["offset"] == 3600
    state, shift, msg = zone_step(state, "Pacific Standard Time", -25200, 200)         # someone changes the zone
    assert shift == 3600 + 25200 and "keeps Central European" in msg
    state, shift, msg = zone_step(state, "Pacific Standard Time", -25200, 200 + ZONE_DELAY_SEC - 1)
    assert shift == 3600 + 25200 and msg is None
    state, shift, msg = zone_step(state, "Pacific Standard Time", -25200, 200 + ZONE_DELAY_SEC)
    assert shift == 0 and state["name"] == "Pacific Standard Time" and "accepted" in msg
