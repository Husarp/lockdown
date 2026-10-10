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


def test_phrase_charset_and_custom():
    assert ab.new_phrase(40).replace(" ", "").islower()                       # letters only by default
    assert any(c.isdigit() or c.isupper() for c in ab.new_phrase(200, complex=True))   # complex adds digits/caps
    assert ab.phrase_for({**ab.DEFAULTS, "custom_phrase": "my secret pass"}) == "my secret pass"


def test_custom_and_complex_looser():
    base = {**ab.DEFAULTS, "phrase": True, "length": 60}
    assert ab.settings_looser({**base, "complex": True}, base)                # dropping complexity is looser
    assert not ab.settings_looser(base, {**base, "complex": True})            # adding it is not
    assert ab.settings_looser(base, {**base, "custom_phrase": "short"})       # 5-char custom < 60 is looser
    assert not ab.settings_looser(base, {**base, "custom_phrase": "x" * 80})  # a longer custom phrase is not


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
    # a member's extra rule comes on top of the group's 30 min (0.84.3), so adding one never loosens anything -
    # even a "90 min" one, which used to replace the group's 30 for that member
    custom = {**group, "members": {1: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 90}}, 2: None}}
    assert ab.draft_changes(saved, saved, {5: group}, {5: custom}, NOW) == []
    stricter = {**group, "members": {1: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 10}}, 2: None}}
    assert ab.draft_changes(saved, saved, {5: group}, {5: stricter}, NOW) == []
    # ... but relaxing or removing an extra rule hands that member time back
    assert ab.draft_changes(saved, saved, {5: stricter}, {5: custom}, NOW) == ["Loosen group Social"]
    assert ab.draft_changes(saved, saved, {5: stricter}, {5: group}, NOW) == ["Loosen group Social"]


def test_settings_emergency_protection_looser():
    base = {**ab.DEFAULTS, "phrase": True, "length": 60, "hours": True, "windows": SUN_EVENING}
    assert not ab.settings_looser(base, {**base, "length": 120})
    assert ab.settings_looser(base, {**base, "length": 30})
    assert ab.settings_looser(base, {**base, "phrase": False})
    assert ab.settings_looser(base, {**base, "windows": [{"days": [5, 6], "start": "18:00", "end": "20:00"}]})
    assert not ab.settings_looser({**base, "hours": False}, base)                          # turning hours on
    t1 = {"id": "t1", "minutes": 20, "uses": 3, "per": "week"}
    em = {"enabled": True, "tiers": [t1]}
    assert not ab.emergency_looser(em, {**em, "tiers": [{**t1, "uses": 2, "minutes": 10}]})
    assert not ab.emergency_looser(em, {**em, "enabled": False})
    assert ab.emergency_looser(em, {**em, "tiers": [{**t1, "uses": 5}]})
    assert ab.emergency_looser(em, {**em, "tiers": [{**t1, "per": "day"}]})
    assert ab.emergency_looser({**em, "enabled": False}, em)
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


def test_turning_the_grid_off_needs_the_challenge():
    base = {**ab.DEFAULTS, "phrase": True, "grid": True}
    assert ab.settings_looser(base, {**base, "grid": False})
    assert not ab.settings_looser({**base, "grid": False}, base)


def test_custom_phrase_length_bounds_and_looseness():
    assert ab.parse_length(" 30 ") == ab.MIN_LENGTH == 30
    assert ab.parse_length("777") == 777
    assert ab.parse_length(str(ab.MAX_LENGTH)) == ab.MAX_LENGTH == 2000
    for bad in ("29", "2001", "0", "-50", "", "abc", "60.5"):
        try:
            ab.parse_length(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    base = {**ab.DEFAULTS, "phrase": True, "length": 250}
    assert not ab.settings_looser(base, {**base, "length": 251})       # longer than a preset: free
    assert not ab.settings_looser(base, {**base, "length": 2000})
    assert ab.settings_looser(base, {**base, "length": 249})           # any shorter: the challenge
    assert ab.settings_looser({**base, "length": 1500}, {**base, "length": 250})
    assert len(ab.new_phrase(2000).replace(" ", "")) == 2000


def test_schedule_extending_is_free_across_midnight():
    from rules import make_schedule

    def sched(start, end, days=(0,), mode="block"):
        return {"rule_type": "scheduled", "schedule": make_schedule(mode, [(list(days), start, end)])}

    # ending at midnight, then moved later: stricter, free
    assert not ab.rule_looser(sched("22:00", "00:00"), sched("22:00", "01:00"), NOW)
    assert not ab.rule_looser(sched("22:00", "23:00"), sched("22:00", "00:00"), NOW)
    assert not ab.rule_looser(sched("22:00", "00:00"), sched("21:00", "00:00"), NOW)      # earlier start
    assert not ab.rule_looser(sched("22:00", "00:00"), sched("22:00", "00:00", (0, 1)), NOW)   # more days
    assert not ab.rule_looser(sched("22:00", "00:00"), sched("22:00", "22:00"), NOW)      # whole 24 h
    assert not ab.rule_looser(sched("22:00", "00:00", (6,)), sched("22:00", "01:00", (6,)), NOW)   # Sunday night into Monday
    # shortened or moved away: still a loosening
    assert ab.rule_looser(sched("22:00", "01:00"), sched("22:00", "00:00"), NOW)
    assert ab.rule_looser(sched("22:00", "00:00"), sched("23:00", "00:00"), NOW)
    assert ab.rule_looser(sched("22:00", "00:00"), sched("22:00", "00:00", (1,)), NOW)    # other day
    assert ab.rule_looser(sched("22:00", "00:00", (0, 1)), sched("22:00", "00:00"), NOW)  # fewer days
    # allow mode: a longer allowed window blocks less
    assert ab.rule_looser(sched("09:00", "17:00", mode="allow"), sched("09:00", "18:00", mode="allow"), NOW)
    assert not ab.rule_looser(sched("09:00", "17:00", mode="allow"), sched("09:00", "16:00", mode="allow"), NOW)
    assert ab.rule_looser(sched("22:00", "00:00"), sched("22:00", "00:00", mode="allow"), NOW)


def test_schedule_days_outside_week_never_block():
    from rules import make_schedule

    def sched(days, mode="block"):
        return {"rule_type": "scheduled", "schedule": make_schedule(mode, [(list(days), "22:00", "00:00")])}

    # day 7 / -1 never match in rules.window_until, so moving a block there stops it: looser
    assert ab.rule_looser(sched([0]), sched([7]), NOW)
    assert ab.rule_looser(sched([0]), sched([-1]), NOW)      # no wrap, no crash
    assert not ab.rule_looser(sched([7]), sched([0]), NOW)
    assert not ab.rule_looser(sched([0]), sched([0, 7]), NOW)
    # allow mode: an allowed window on day 7 never allows anything, so everything is blocked
    assert not ab.rule_looser(sched([0], "allow"), sched([7], "allow"), NOW)
    assert ab.rule_looser(sched([7], "allow"), sched([0], "allow"), NOW)


def test_block_until_midnight_can_be_moved_later_free():
    temp = {"rule_type": "temporary", "temp_until": "2026-09-21 00:00:00"}
    assert not ab.rule_looser(temp, {**temp, "temp_until": "2026-09-21 00:30:00"}, NOW)
    assert ab.rule_looser({**temp, "temp_until": "2026-09-21 00:30:00"}, temp, NOW)
