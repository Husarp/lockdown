"""Disabling stops blocking, not the clock.

A disabled group used to get no rules at all, so its limit counted nothing: it read "0m / 2h 00m today" after
155 minutes of osu!, YouTube and Twitch. It was also a way round the limit - disabling needs the challenge but
enabling does not, so: disable, use it for hours, switch it back on, and the limit started from zero."""
from datetime import datetime

from rules import counted_rules, effective_rules, usage_targets

NOW = datetime(2026, 9, 25, 20, 54)


def _group(disabled: int) -> dict:
    return {"id": 1, "name": "Good Night", "disabled": disabled, "members": {1: {}},
            "rules": [{"rule_type": "time_limit", "daily_limit_min": 120}]}


def _item(disabled: int = 0) -> dict:
    return {"id": 1, "display_name": "osu!", "item_type": "app", "target": "osu!.exe", "disabled": disabled,
            "rules": [{"rule_type": "time_limit", "daily_limit_min": 60}]}


def _targets(item, groups) -> set:
    return usage_targets(counted_rules(item, groups), item["id"], NOW)


def test_a_disabled_group_enforces_nothing():
    assert effective_rules(_item(), [_group(disabled=1)]) == [r for r in effective_rules(_item(), [])]


def test_but_its_limit_still_counts():
    assert ("group:1", "day:2026-09-25") in _targets(_item(), [_group(disabled=1)])


def test_so_does_a_disabled_items_own_limit():
    """The same for an item: paused means not blocking, not not-counting."""
    assert effective_rules(_item(disabled=1), []) == []
    assert ("item:1", "day:2026-09-25") in _targets(_item(disabled=1), [])


def test_counting_is_the_same_whether_it_is_enabled_or_not():
    """So switching a group back on can never be a way to start its limit from zero."""
    assert _targets(_item(), [_group(disabled=1)]) == _targets(_item(), [_group(disabled=0)])


def test_the_inputs_are_not_changed():
    """counted_rules must not flip the real disabled flags on the objects the app holds."""
    item, group = _item(disabled=1), _group(disabled=1)
    counted_rules(item, [group])
    assert item["disabled"] == 1 and group["disabled"] == 1
