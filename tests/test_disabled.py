"""Disabling keeps something with all its blockers but stops enforcing them - the "pause instead of delete"
that saves you rebuilding a group from scratch. It is a weakening change, so Anti-Bypass asks first."""
import json
from datetime import datetime

from antibypass import draft_changes, group_looser, item_looser
from rules import effective_rules, item_block, no_usage

NOW = datetime(2026, 9, 20, 23, 0)          # inside 22:00-07:00
SCHEDULE = json.dumps({"mode": "block", "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "22:00",
                                                     "end": "07:00"}]})


def _item(disabled=0, rules=None):
    return {"id": 1, "display_name": "YouTube", "item_type": "site", "target": "youtube.com", "block_type": None,
            "app_path": None, "notify": None, "disabled": disabled,
            "rules": [{"rule_type": "scheduled", "schedule": SCHEDULE}] if rules is None else rules}


def _group(disabled=0):
    return [{"id": 1, "name": "Good Night", "disabled": disabled, "members": {1: {}},
             "rules": [{"rule_type": "scheduled", "schedule": SCHEDULE}]}]


def test_a_disabled_item_blocks_nothing():
    assert item_block(effective_rules(_item(), []), NOW, no_usage)[0] == "schedule"
    assert effective_rules(_item(disabled=1), []) == []


def test_a_disabled_group_stops_applying_to_its_members():
    item = _item(rules=[])
    assert item_block(effective_rules(item, _group()), NOW, no_usage)[0] == "schedule"
    assert effective_rules(item, _group(disabled=1)) == []


def test_a_disabled_group_leaves_the_items_own_rules_alone():
    item = _item()          # blocked by its own hours as well as the group's
    assert item_block(effective_rules(item, _group(disabled=1)), NOW, no_usage)[0] == "schedule"


def test_disabling_goes_through_the_challenge():
    assert item_looser(_item(), _item(disabled=1), NOW)
    assert not item_looser(_item(disabled=1), _item(), NOW)      # enabling it again is free
    assert group_looser(_group()[0], _group(disabled=1)[0], NOW)
    assert not group_looser(_group(disabled=1)[0], _group()[0], NOW)


def test_the_challenge_says_disable_not_remove():
    saved, draft = {1: _item()}, {1: _item(disabled=1)}
    assert draft_changes(saved, draft, {}, {}, NOW) == ["Disable YouTube"]
    groups, off = {1: _group()[0]}, {1: _group(disabled=1)[0]}
    assert draft_changes({}, {}, groups, off, NOW) == ["Disable group Good Night"]
