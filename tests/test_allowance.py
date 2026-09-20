""""N minutes allowed during blocked hours": what is left of it has to be visible, and you get told you are
spending it rather than wondering why the app opened."""
import json
from datetime import datetime

import alerts
from rules import Usage, allowance_left, describe_rule

NOW = datetime(2026, 9, 20, 23, 0)          # inside 22:00-07:00
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def _rule(minutes=15, owner="item:1"):
    return {"rule_type": "scheduled", "usage_owner": owner, "item_owner": owner, "allowance_min": minutes,
            "group": None,
            "schedule": json.dumps({"mode": "block", "windows": [{"days": EVERY_DAY, "start": "22:00",
                                                                  "end": "07:00"}]})}


def _usage(seconds=0, owner="item:1"):
    rule = _rule(owner=owner)
    from rules import allowance_bucket, schedule_until
    bucket = allowance_bucket(rule, schedule_until(rule["schedule"], NOW))
    return Usage({(owner, bucket): seconds})


def test_how_much_allowance_is_left():
    used, allowed, until = allowance_left(_rule(15), NOW, _usage(5 * 60))
    assert (used, allowed) == (300, 900)
    assert until.hour == 7                      # the end of this blocked stretch


def test_no_allowance_outside_the_blocked_hours():
    midday = datetime(2026, 9, 20, 12, 0)
    assert allowance_left(_rule(15), midday, _usage()) is None


def test_the_rule_chip_says_what_is_left():
    text = describe_rule(_rule(15), NOW, _usage(5 * 60))
    assert "15 min allowed during blocked hours" in text
    assert "10m left" in text and "until 07:00" in text
    spent = describe_rule(_rule(15), NOW, _usage(15 * 60))
    assert "used up until 07:00" in spent


def _item():
    return {"id": 1, "display_name": "Discord", "item_type": "app", "target": "discord.exe",
            "rules": [_rule(15)], "block_type": "close", "app_path": None, "notify": None}


def _spent(item, seconds):
    """Usage as the watcher will look it up (effective_rules fills in the rule's key and owner)."""
    from rules import allowance_bucket, effective_rules, schedule_until, _item_owner
    rule = effective_rules(item, [])[0]
    bucket = allowance_bucket(rule, schedule_until(rule["schedule"], NOW))
    return Usage({(_item_owner(rule), bucket): seconds})


SETTINGS = {"notify.warn.enabled": "1", "notify.warn.minutes": "5", "notify.warn.repeat_min": "0",
            "notify.started.enabled": "0"}


def test_you_are_told_when_you_start_spending_it():
    item = _item()
    watcher = alerts.BlockWatcher()
    watcher.prev_blocked = set()
    messages = watcher.check([item], [], _spent(item, 2 * 60), NOW, {1}, SETTINGS)
    assert any("blocked now" in m and "13 min of your allowance left" in m for m in messages), messages

    again = watcher.check([item], [], _spent(item, 3 * 60), NOW, {1}, SETTINGS)
    assert not any("allowance left" in m for m in again)      # said once per blocked stretch, not every tick


def test_nothing_is_said_once_the_allowance_is_gone():
    item = _item()
    watcher = alerts.BlockWatcher()
    watcher.prev_blocked = set()
    messages = watcher.check([item], [], _spent(item, 15 * 60), NOW, {1}, SETTINGS)
    assert not any("allowance left" in m for m in messages)
