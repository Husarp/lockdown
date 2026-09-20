"""A group's "N minutes allowed during blocked hours" is one pot for all its members (unless you untick it),
and a block starting is announced as one summarized line rather than one popup per site and app."""
import json
from datetime import datetime

import alerts
from antibypass import rule_looser
from rules import Usage, allowance_bucket, allowance_left, allowance_owner, effective_rules, item_block, \
    usage_targets

NOW = datetime(2026, 9, 20, 23, 0)          # inside 22:00-07:00
SCHEDULE = json.dumps({"mode": "block", "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "22:00",
                                                     "end": "07:00"}]})


def _group(shared=None):
    rule = {"rule_type": "scheduled", "schedule": SCHEDULE, "allowance_min": 15, "allowance_shared": shared}
    return [{"id": 1, "name": "Good Night", "rules": [rule], "members": {2: {}, 3: {}}}]


def _item(item_id, name):
    return {"id": item_id, "display_name": name, "item_type": "site", "target": name.lower() + ".com",
            "rules": [], "block_type": None, "app_path": None, "notify": None}


def _rule_of(item, groups):
    return effective_rules(item, groups)[0]


def test_the_group_allowance_is_one_pot():
    groups = _group()
    youtube, twitch = _item(2, "YouTube"), _item(3, "Twitch")
    rule = _rule_of(youtube, groups)
    assert allowance_owner(rule) == "group:1"
    # 8 minutes spent on Twitch are gone from YouTube's allowance too
    usage = Usage({("group:1", allowance_bucket(rule, datetime(2026, 9, 21, 7, 0))): 8 * 60})
    used, allowed, _until = allowance_left(_rule_of(youtube, groups), NOW, usage)
    assert (used, allowed) == (480, 900)
    assert allowance_left(_rule_of(twitch, groups), NOW, usage)[0] == 480


def test_time_on_one_member_blocks_the_whole_group():
    groups = _group()
    youtube, twitch = _item(2, "YouTube"), _item(3, "Twitch")
    bucket = allowance_bucket(_rule_of(youtube, groups), datetime(2026, 9, 21, 7, 0))
    usage = Usage({("group:1", bucket): 15 * 60})
    assert item_block(effective_rules(youtube, groups), NOW, usage)[0] == "schedule"
    assert item_block(effective_rules(twitch, groups), NOW, usage)[0] == "schedule"


def test_the_time_you_spend_goes_into_the_shared_pot():
    groups = _group()
    youtube = _item(2, "YouTube")
    rules = effective_rules(youtube, groups)
    bucket = allowance_bucket(rules[0], datetime(2026, 9, 21, 7, 0))
    assert ("group:1", bucket) in usage_targets(rules, 2, NOW)


def test_each_member_can_have_its_own_pot():
    groups = _group(shared=0)
    youtube, twitch = _item(2, "YouTube"), _item(3, "Twitch")
    rule = _rule_of(youtube, groups)
    assert allowance_owner(rule) == "item:2"
    usage = Usage({("item:3", allowance_bucket(rule, datetime(2026, 9, 21, 7, 0))): 15 * 60})
    assert item_block(effective_rules(twitch, groups), NOW, usage)[0] == "schedule"
    assert item_block(effective_rules(youtube, groups), NOW, usage) is None   # YouTube still has its own 15


def test_splitting_the_pot_needs_the_guard():
    shared = {"rule_type": "scheduled", "schedule": SCHEDULE, "allowance_min": 15, "allowance_shared": 1}
    assert rule_looser(shared, {**shared, "allowance_shared": 0}, NOW)
    assert not rule_looser({**shared, "allowance_shared": 0}, shared, NOW)


SETTINGS = {"notify.warn.enabled": "0", "notify.warn.minutes": "5", "notify.warn.repeat_min": "0",
            "notify.started.enabled": "1"}


def _blocked_item(item_id, name, until="07:00"):
    rule = {"rule_type": "scheduled",
            "schedule": json.dumps({"mode": "block", "windows": [{"days": [0, 1, 2, 3, 4, 5, 6],
                                                                  "start": "22:00", "end": until}]})}
    return {**_item(item_id, name), "rules": [rule]}


def test_things_blocked_on_their_own_are_one_line_together():
    watcher = alerts.BlockWatcher()
    watcher.prev_blocked = set()
    items = [_blocked_item(1, "Reddit"), _blocked_item(2, "Twitter")]
    assert watcher.check(items, [], Usage(), NOW, set(), SETTINGS) == \
        ["2 things are now blocked until Monday 07:00."]


def test_one_blocked_thing_is_still_named():
    watcher = alerts.BlockWatcher()
    watcher.prev_blocked = set()
    assert watcher.check([_blocked_item(1, "Reddit")], [], Usage(), NOW, set(), SETTINGS) == \
        ["Reddit is now blocked until Monday 07:00."]
