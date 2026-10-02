"""A member's own rules in a group come ON TOP of the group's - they never replace them (0.84.3).

The rule: the group's rules always apply to every member. Time on a member fills the group's shared limits AND
the member's own. Blocked hours add up, allowed hours narrow. A member's extra rule can only tighten that member;
nothing about it may loosen the group. Before 0.84.3 a customized rule REPLACED the group's rule of its kind and
counted for that member alone - "YouTube 3 h" in a "Fun 2 h" group gave YouTube 3 h, none of which filled Fun's 2 h.
"""
import json
from datetime import datetime, timedelta

import antibypass as ab
from db import Database
from rules import (Usage, allowance_bucket, counted_rules, describe_rule, effective_rules, item_block, make_schedule,
                   next_block, switch_targets, usage_targets, visit_targets)

MON = datetime(2026, 9, 14)            # a Monday
EVERY_DAY = list(range(7))


def at(h, m=0):
    return MON.replace(hour=h, minute=m)


def _item(item_id, name, kind="app"):
    return {"id": item_id, "display_name": name, "item_type": kind, "target": f"{name.lower()}.exe",
            "rules": [], "block_type": None, "app_path": None, "notify": None}


YOUTUBE, TWITCH = _item(1, "YouTube"), _item(2, "Twitch")


def _fun(rules, youtube_extra=None, gid=7):
    return [{"id": gid, "name": "Fun", "rules": rules, "members": {1: youtube_extra or {}, 2: {}}}]


class Meter:
    """Usage as the tracker writes it: every second of use goes to every (owner, bucket) the rules name."""

    def __init__(self, groups):
        self.groups, self.data = groups, {}

    def use(self, item, now, seconds):
        for target in usage_targets(counted_rules(item, self.groups), item["id"], now):
            self.data[target] = self.data.get(target, 0) + seconds

    def launch(self, item, now):
        rules = counted_rules(item, self.groups)
        for target in visit_targets(rules, item, now, True, None) | switch_targets(rules, item["id"], now):
            self.data[target] = self.data.get(target, 0) + 1

    @property
    def usage(self):
        return Usage(self.data)

    def block(self, item, now):
        return item_block(effective_rules(item, self.groups), now, self.usage)


# ---------- (a) group 2 h + YouTube's own 1 h ----------

def test_youtube_stops_at_its_own_hour_and_that_hour_fills_the_group():
    groups = _fun([{"rule_type": "time_limit", "daily_limit_min": 120}],
                  {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 60}})
    m = Meter(groups)
    m.use(YOUTUBE, at(10), 59 * 60)
    assert m.block(YOUTUBE, at(11)) is None
    m.use(YOUTUBE, at(11), 60)
    assert m.block(YOUTUBE, at(11))[0] == "limit"                     # its own hour is used up
    assert m.usage("group:7", "day:2026-09-14") == 3600               # ... and it is 1 h of the group's 2 h
    assert m.block(TWITCH, at(11)) is None                            # the rest of the group's 2 h is still there
    m.use(TWITCH, at(12), 59 * 60)
    assert m.block(TWITCH, at(13)) is None
    m.use(TWITCH, at(13), 60)
    assert m.block(TWITCH, at(13))[0] == "limit"                      # the group's 2 h are gone
    assert m.block(YOUTUBE, at(13))[0] == "limit"


def test_when_the_group_runs_out_first_the_member_stops_too():
    groups = _fun([{"rule_type": "time_limit", "daily_limit_min": 120}],
                  {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 60}})
    m = Meter(groups)
    m.use(TWITCH, at(9), 90 * 60)
    m.use(YOUTUBE, at(11), 30 * 60)                                   # only 30 min of YouTube: the group's 2 h
    assert m.block(YOUTUBE, at(12))[0] == "limit"
    assert m.usage("item:1", "day:2026-09-14") == 30 * 60             # well under its own hour


def test_the_chips_show_both_limits():
    groups = _fun([{"rule_type": "time_limit", "daily_limit_min": 120}],
                  {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 60}})
    m = Meter(groups)
    m.use(YOUTUBE, at(10), 40 * 60)
    m.use(TWITCH, at(10), 30 * 60)
    texts = [describe_rule(r, at(12), m.usage) for r in effective_rules(YOUTUBE, groups)]
    assert texts == ["Limit (group): 1h 10m / 2h 00m today", "Limit (own): 40m / 1h 00m today"]


def test_the_tracker_writes_the_member_both_pots_on_time():
    from rules import limit_targets
    groups = _fun([{"rule_type": "time_limit", "daily_limit_min": 120}],
                  {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 60}})
    rules = counted_rules(YOUTUBE, groups)
    assert limit_targets(rules, at(10)) == {("group:7", "day:2026-09-14"): 7200, ("item:1", "day:2026-09-14"): 3600}


# ---------- (b) blocked hours merge ----------

def test_blocked_hours_add_up():
    groups = _fun([{"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "15:00", "18:00")])}],
                  {"scheduled": {"rule_type": "scheduled",
                                 "schedule": make_schedule("block", [(EVERY_DAY, "13:00", "14:00")])}})
    m = Meter(groups)
    assert m.block(YOUTUBE, at(13, 30))[:2] == ("schedule", at(14))   # its own hours
    assert m.block(YOUTUBE, at(16))[:2] == ("schedule", at(18))       # ... and the group's
    assert m.block(YOUTUBE, at(14, 30)) is None
    assert m.block(TWITCH, at(13, 30)) is None                        # the other member: only the group's
    assert m.block(TWITCH, at(16))[0] == "schedule"
    assert next_block(effective_rules(YOUTUBE, groups), at(12, 50))[0] == at(13)
    assert next_block(effective_rules(YOUTUBE, groups), at(14, 10))[0] == at(15)


# ---------- (c) allowed-only hours narrow ----------

def test_allowed_hours_intersect():
    groups = _fun([{"rule_type": "scheduled", "schedule": make_schedule("allow", [(EVERY_DAY, "09:00", "17:00")])}],
                  {"scheduled": {"rule_type": "scheduled",
                                 "schedule": make_schedule("allow", [(EVERY_DAY, "12:00", "20:00")])}})
    m = Meter(groups)
    assert m.block(YOUTUBE, at(10)) is not None       # the group allows it, its own hours don't
    assert m.block(YOUTUBE, at(13)) is None           # both allow
    assert m.block(YOUTUBE, at(18)) is not None       # its own hours allow, the group's don't: no widening
    assert m.block(TWITCH, at(10)) is None
    assert m.block(TWITCH, at(18)) is not None


# ---------- (d) the group's shared allowance in blocked hours ----------

NIGHT = make_schedule("block", [(EVERY_DAY, "22:00", "07:00")])


def test_the_shared_allowance_stays_shared_and_youtube_spends_it():
    group_rule = {"rule_type": "scheduled", "schedule": NIGHT, "allowance_min": 15}
    # YouTube's own night rule even says 30 min - it can't stretch the group's 15
    groups = _fun([group_rule], {"scheduled": {"rule_type": "scheduled", "schedule": NIGHT, "allowance_min": 30}})
    m = Meter(groups)
    night, end = at(23), at(7) + timedelta(days=1)
    shared = ("group:7", allowance_bucket({"rule_key": "g7scheduled"}, end))
    assert shared in usage_targets(counted_rules(YOUTUBE, groups), 1, night)
    m.use(YOUTUBE, night, 10 * 60)
    assert m.data[shared] == 600                                      # YouTube's 10 min came out of the group's pot
    m.use(TWITCH, night, 4 * 60)
    assert m.block(TWITCH, night) is None and m.block(YOUTUBE, night) is None
    m.use(TWITCH, night, 60)
    assert m.block(TWITCH, night)[0] == "schedule"
    assert m.block(YOUTUBE, night)[0] == "schedule"                   # its own "30 min" doesn't open the group's


def test_a_member_own_allowance_is_its_own_pot_on_top():
    groups = _fun([{"rule_type": "scheduled", "schedule": NIGHT, "allowance_min": 15}],
                  {"scheduled": {"rule_type": "scheduled", "schedule": NIGHT, "allowance_min": 5}})
    m = Meter(groups)
    night = at(23)
    m.use(YOUTUBE, night, 5 * 60)
    assert m.block(YOUTUBE, night)[0] == "schedule"                   # its own 5 min: the tighter one wins
    assert m.block(TWITCH, night) is None                             # the group's pot still has 10 min


# ---------- (e) old customizations that were looser no longer loosen ----------

def test_an_old_looser_limit_no_longer_loosens():
    groups = _fun([{"rule_type": "time_limit", "daily_limit_min": 120}],
                  {"time_limit": {"daily_limit_min": 180}})          # saved by 0.84.2 or earlier (no rule_type)
    m = Meter(groups)
    m.use(YOUTUBE, at(10), 120 * 60)
    assert m.block(YOUTUBE, at(12))[0] == "limit"                     # the group's 2 h, not its own 3 h
    assert m.block(TWITCH, at(12))[0] == "limit"                      # and the 2 h were the group's


def test_an_old_night_allowance_no_longer_opens_the_group_block(tmp_path):
    db = Database(tmp_path / "t.db")
    signal = db.add_item("Signal", ["signal.exe"], "app")
    discord = db.add_item("Discord", ["discord.exe"], "app")
    db.add_group("Night", [{"rule_type": "scheduled", "schedule": NIGHT}],
                 {discord: {}, signal: {"scheduled": {"schedule": NIGHT, "allowance_min": 5}}})
    assert {b["item"]["display_name"] for b in db.blocks(at(23))} == {"Signal", "Discord"}


def test_an_old_looser_opening_limit_no_longer_loosens():
    groups = _fun([{"rule_type": "switch_limit", "daily_switch_limit": 3}],
                  {"switch_limit": {"daily_switch_limit": 10}})
    m = Meter(groups)
    for _ in range(4):
        m.launch(YOUTUBE, at(10))
    assert m.block(YOUTUBE, at(10))[0] == "switches"


def test_upgrading_keeps_what_the_member_already_used():
    """The member's extra rule reads its usage where its customization stored it (owner item:<id>, key
    g<group><type>), so the openings and allowance used before the upgrade still count after it."""
    groups = _fun([{"rule_type": "switch_limit", "daily_switch_limit": 10}],
                  {"switch_limit": {"daily_switch_limit": 2}})
    before = Usage({("item:1", "op:g7switch_limit:2026-09-14"): 3})   # written by 0.84.2
    assert item_block(effective_rules(YOUTUBE, groups), at(12), before)[0] == "switches"


# ---------- (f) Anti-Bypass: removing / relaxing an extra rule is loosening ----------

def test_removing_or_relaxing_an_extra_rule_needs_the_challenge():
    rules = [{"rule_type": "time_limit", "daily_limit_min": 120}]
    plain = {"id": 7, "name": "Fun", "rules": rules, "members": {1: {}, 2: {}}}
    one_hour = {**plain, "members": {1: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 60}}, 2: {}}}
    half = {**plain, "members": {1: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 30}}, 2: {}}}
    three = {**plain, "members": {1: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 180}}, 2: {}}}
    items = {1: YOUTUBE, 2: TWITCH}

    def changes(old, new):
        return ab.draft_changes(items, items, {7: old}, {7: new}, at(12))
    assert changes(plain, one_hour) == []                       # adding an extra limit: free
    assert changes(plain, three) == []                          # even a big one: the group's 2 h still applies
    assert changes(one_hour, half) == []                        # tightening: free
    assert changes(one_hour, plain) == ["Loosen group Fun"]     # removing it
    assert changes(one_hour, three) == ["Loosen group Fun"]     # relaxing it
    hours = {**plain, "members": {1: {"scheduled": {"rule_type": "scheduled", "schedule": NIGHT}}, 2: {}}}
    assert changes(plain, hours) == []                          # adding own blocked hours: free
    assert changes(hours, plain) == ["Loosen group Fun"]        # taking them away
    other = {**plain, "members": {1: {"scheduled": {"rule_type": "scheduled", "schedule": make_schedule(
        "block", [(EVERY_DAY, "23:00", "06:00")])}}, 2: {}}}
    assert changes(hours, other) == ["Loosen group Fun"]        # other hours
    temp = {"rule_type": "temporary", "temp_until": "2026-09-14 13:00:00"}
    running = {**plain, "members": {1: {"temporary": temp}, 2: {}}}
    assert changes(running, plain) == ["Loosen group Fun"]      # a running temporary block ended early
    assert ab.draft_changes(items, items, {7: running}, {7: plain}, at(14)) == []   # one that has run out: free


# ---------- (g) opening / switch limits fill both ----------

def test_openings_fill_the_group_and_the_member():
    groups = _fun([{"rule_type": "switch_limit", "daily_switch_limit": 5}],
                  {"switch_limit": {"rule_type": "switch_limit", "daily_switch_limit": 2}})
    m = Meter(groups)
    for _ in range(3):
        m.launch(YOUTUBE, at(10))
    assert m.block(YOUTUBE, at(10))[0] == "switches"            # its own 2 openings
    assert m.usage("group:7", "op:g7switch_limit:2026-09-14") == 3
    assert m.usage("item:1", "op:g7switch_limit:2026-09-14") == 3
    m.launch(TWITCH, at(11))
    m.launch(TWITCH, at(11))
    assert m.block(TWITCH, at(11)) is None                      # the group's 5 openings: 3 + 2
    m.launch(TWITCH, at(11))
    assert m.block(TWITCH, at(11))[0] == "switches"


def test_switches_fill_the_group_and_the_member():
    switch = {"rule_type": "switch_limit", "switch_mode": "switch"}
    groups = _fun([{**switch, "daily_switch_limit": 4}], {"switch_limit": {**switch, "daily_switch_limit": 1}})
    m = Meter(groups)
    m.launch(YOUTUBE, at(10))
    m.launch(YOUTUBE, at(10))
    assert m.block(YOUTUBE, at(10))[0] == "switches"
    m.launch(TWITCH, at(10))
    m.launch(TWITCH, at(10))
    assert m.block(TWITCH, at(10)) is None
    m.launch(TWITCH, at(10))
    assert m.block(TWITCH, at(10))[0] == "switches"


def test_a_disabled_group_still_counts_the_member_both_ways():
    groups = [{**_fun([{"rule_type": "time_limit", "daily_limit_min": 120}],
                      {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 60}})[0], "disabled": 1}]
    targets = usage_targets(counted_rules(YOUTUBE, groups), 1, at(10))
    assert {("group:7", "day:2026-09-14"), ("item:1", "day:2026-09-14")} <= targets
    assert effective_rules(YOUTUBE, groups) == []               # nothing of it enforced while it's off


def test_extra_rules_round_trip_through_the_database(tmp_path):
    db = Database(tmp_path / "t.db")
    yt, tw = db.add_item("YouTube", ["yt.exe"], "app"), db.add_item("Twitch", ["tw.exe"], "app")
    db.add_group("Fun", [{"rule_type": "time_limit", "daily_limit_min": 120}],
                 {yt: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 60}}, tw: {}})
    groups = db.list_groups()
    item = next(i for i in db.list_items() if i["id"] == yt)
    db.add_usage(usage_targets(counted_rules(item, groups), yt, at(10)), 3600, at(10).date())
    assert {b["item"]["display_name"] for b in db.blocks(at(11))} == {"YouTube"}
    assert db.usage_lookup(at(11))(f"group:{groups[0]['id']}", "day:2026-09-14") == 3600
    assert json.loads(db.conn.execute("SELECT overrides FROM group_members WHERE item_id = ?", (yt,)).fetchone()[0])


# ---------- a blocked member that is closed at once doesn't spend the group's openings ----------

class Launcher:
    """The real tray tracker, ticking while apps start and close."""

    def __init__(self, db, monkeypatch, now):
        from monitor import usage as usage_mod
        self.db, self.now, self.running = db, now, set()
        monkeypatch.setattr(usage_mod, "now_from_db", lambda _db: self.now)
        self.tracker = usage_mod.UsageTracker()
        self.tracker.counted_ts = now.timestamp()
        self.tick()

    def tick(self):
        self.now += timedelta(seconds=1)
        self.tracker.tick(self.db, lambda: (None, None, 0.0), running_exes=lambda: set(self.running))
        self.tracker.flush(self.db)

    def launch(self, exe):
        self.running.add(exe)
        self.tick()
        self.running.discard(exe)      # (closed again - by you, or by Lockdown)
        self.tick()

    def used(self, owner, gid):
        return self.db.usage_lookup(self.now)(owner, f"op:g{gid}switch_limit:2026-09-14")


def _launch_group(tmp_path, youtube_extra, block_type=None, group_limit=3):
    db = Database(tmp_path / "t.db")
    yt = db.add_item("YouTube", ["yt.exe"], "app", block_type=block_type)
    tw = db.add_item("Twitch", ["tw.exe"], "app")
    gid = db.add_group("Fun", [{"rule_type": "switch_limit", "daily_switch_limit": group_limit}],
                       {yt: youtube_extra, tw: {}})
    return db, yt, tw, gid


def test_retrying_a_blocked_member_does_not_use_up_the_groups_openings(tmp_path, monkeypatch):
    db, yt, tw, gid = _launch_group(tmp_path, {"switch_limit": {"rule_type": "switch_limit",
                                                                "daily_switch_limit": 1}})
    run = Launcher(db, monkeypatch, at(10))
    run.launch("yt.exe")                                         # its one opening: both counters
    assert (run.used(f"item:{yt}", gid), run.used(f"group:{gid}", gid)) == (1, 1)
    run.launch("yt.exe")                                         # over its own 1: closed - only its own counter
    run.launch("yt.exe")                                         # blocked: retrying spends nothing
    run.launch("yt.exe")
    assert (run.used(f"item:{yt}", gid), run.used(f"group:{gid}", gid)) == (2, 1)
    assert {b["item"]["display_name"] for b in db.blocks(run.now)} == {"YouTube"}
    run.launch("tw.exe")
    run.launch("tw.exe")                                         # Twitch still gets the group's other 2
    assert {b["item"]["display_name"] for b in db.blocks(run.now)} == {"YouTube"}
    run.launch("tw.exe")                                         # the 4th of the group's 3: blocked, as always
    assert run.used(f"group:{gid}", gid) == 4
    assert {b["item"]["display_name"] for b in db.blocks(run.now)} == {"YouTube", "Twitch"}


def test_a_blocked_member_that_is_only_minimized_still_spends_the_groups_openings(tmp_path, monkeypatch):
    db, yt, tw, gid = _launch_group(tmp_path, {"switch_limit": {"rule_type": "switch_limit",
                                                                "daily_switch_limit": 1}}, block_type="minimize")
    run = Launcher(db, monkeypatch, at(10))
    for _ in range(3):
        run.launch("yt.exe")                                     # minimized, not closed: it can still be used
    assert run.used(f"group:{gid}", gid) == 3                    # all three of the group's 3
    run.launch("tw.exe")
    assert {b["item"]["display_name"] for b in db.blocks(run.now)} == {"YouTube", "Twitch"}


def test_an_opening_just_before_a_block_ends_still_counts(tmp_path, monkeypatch):
    night = {"scheduled": {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "13:00",
                                                                                          "14:00")])}}
    db, yt, tw, gid = _launch_group(tmp_path, night, group_limit=5)
    run = Launcher(db, monkeypatch, at(13, 30))
    run.launch("yt.exe")                                         # blocked by its own hours: closed, not counted
    assert run.used(f"group:{gid}", gid) == 0
    run.now = at(13, 59)
    run.launch("yt.exe")                                         # the block ends in a minute: it may not be closed
    assert run.used(f"group:{gid}", gid) == 1                    # in time, so it counts
    db.add_unlock([yt], ["YouTube"], at(14, 10), at(14, 40))
    run.now = at(14, 15)
    run.launch("yt.exe")                                         # not blocked: counts as always
    assert run.used(f"group:{gid}", gid) == 2


def test_an_emergency_unlock_counts_the_opening(tmp_path, monkeypatch):
    db, yt, tw, gid = _launch_group(tmp_path, {"permanent": {"rule_type": "permanent"}}, group_limit=5)
    run = Launcher(db, monkeypatch, at(10))
    run.launch("yt.exe")
    assert run.used(f"group:{gid}", gid) == 0                    # always blocked by its own extra: closed at once
    db.add_unlock([yt], ["YouTube"], at(10, 30), at(11))
    run.now = at(10, 31)
    run.launch("yt.exe")                                         # unlocked: it really is opened
    assert run.used(f"group:{gid}", gid) == 1


def test_blocked_until_is_the_latest_of_the_rules_blocking_it():
    groups = _fun([{"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "15:00", "18:00")])}],
                  {"scheduled": {"rule_type": "scheduled",
                                 "schedule": make_schedule("block", [(EVERY_DAY, "17:00", "20:00")])}})
    assert Meter(groups).block(YOUTUBE, at(17, 30))[1] == at(20)          # not 18:00
    assert Meter(groups).block(TWITCH, at(17, 30))[1] == at(18)
    groups = _fun([{"rule_type": "time_limit", "daily_limit_min": 60}],
                  {"time_limit": {"rule_type": "time_limit", "weekly_limit_min": 60}})
    m = Meter(groups)
    m.use(YOUTUBE, at(10), 3600)
    assert m.block(YOUTUBE, at(11))[1] == datetime(2026, 9, 21)           # next week, not tomorrow


# ---------- what the screens show when a member has a group rule and its own of the same kind ----------

def _night(start, end, allowance, shared=None):
    rule = {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, start, end)]),
            "allowance_min": allowance}
    return rule if shared is None else {**rule, "allowance_shared": shared}


def test_the_allowance_notice_names_the_allowance_that_limits_it():
    from alerts import BlockWatcher
    # each member 30 min a night from the group, YouTube 5 of its own - one counter (same end), 5 limit it
    groups = _fun([_night("22:00", "07:00", 30, shared=0)], {"scheduled": _night("22:00", "07:00", 5)})
    notes = BlockWatcher()._allowance_notices([YOUTUBE], groups, Usage({}), at(23), {1})
    assert notes == ["YouTube is blocked now - you have 5 min of your 5 min allowance left (until 07:00)."]


def test_the_dashboard_limit_warning_is_the_limit_with_least_left():
    from gui.dashboard import upcoming
    groups = _fun([{"rule_type": "time_limit", "daily_limit_min": 120}],
                  {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 60}})
    m = Meter(groups)
    m.use(TWITCH, at(9), 75 * 60)
    m.use(YOUTUBE, at(10), 35 * 60)                       # group: 10 min left, its own: 25 min left
    rows = [e for e in upcoming(at(11), [YOUTUBE], groups, m.usage) if e["kind"] == "limit"]
    assert [e["left"] for e in rows] == [600]


def test_the_web_view_shows_both_blocked_hours_and_the_tightest_allowance(tmp_path, monkeypatch):
    import webview_app
    monkeypatch.setattr(webview_app, "now_from_db", lambda _db: at(10))
    db = Database(tmp_path / "t.db")
    yt = db.add_item("YouTube", ["yt.exe"], "app")
    db.add_group("Fun", [_night("15:00", "18:00", 20)], {yt: {"scheduled": _night("13:00", "14:00", 5)}})
    row = next(r for r in webview_app._rules_for_ui(db) if r["n"] == "YouTube")
    assert [(w["from"], w["to"]) for w in row["windows"]] == [("15:00", "18:00"), ("13:00", "14:00")]
    assert (row["allowOn"], row["allowMin"]) == (True, 5)


def test_a_member_extra_needs_no_group_rule_of_its_kind_and_an_old_temporary_one_is_cleared():
    from gui.groups import CUSTOMIZABLE, _live
    assert set(CUSTOMIZABLE) == {"scheduled", "time_limit", "switch_limit", "temporary"}
    over = {"temporary": {"rule_type": "temporary", "temp_until": "2020-01-01 00:00:00"},
            "time_limit": {"rule_type": "time_limit", "daily_limit_min": 60}}
    assert _live(over) == {"time_limit": over["time_limit"]}
    later = {"temporary": {"rule_type": "temporary", "temp_until": "2999-01-01 00:00:00"}}
    assert _live(later) == later
    # a group with only blocked hours, YouTube 1 h of its own: it applies, and it fills nothing of the group's
    groups = _fun([_night("22:00", "07:00", 0)], {"time_limit": over["time_limit"]})
    m = Meter(groups)
    m.use(YOUTUBE, at(10), 3600)
    assert m.block(YOUTUBE, at(11))[0] == "limit"
    assert m.block(TWITCH, at(11)) is None
