""""What you are using is about to be blocked" gets through a muting mode, Do not disturb and Mute 1 h - it is
the one notice worth interrupting a game for. Everything else stays quiet."""
import json
from datetime import datetime, timedelta

import alerts
from rules import Usage

NOW = datetime(2026, 9, 21, 20, 55)      # five minutes before a 21:00 block
SETTINGS = {"notify.warn.enabled": "1", "notify.warn.minutes": "10", "notify.warn.repeat_min": "0",
            "notify.started.enabled": "1"}


def _item(item_id, name, start="21:00", end="05:00"):
    return {"id": item_id, "display_name": name, "item_type": "app", "target": f"{name.lower()}.exe",
            "block_type": "close", "app_path": None, "notify": None, "disabled": 0,
            "rules": [{"rule_type": "scheduled",
                       "schedule": json.dumps({"mode": "block",
                                               "windows": [{"days": [0, 1, 2, 3, 4, 5, 6],
                                                            "start": start, "end": end}]})}]}


def test_a_warning_about_what_you_are_using_is_urgent():
    watcher = alerts.BlockWatcher()
    messages = watcher.check([_item(1, "Discord")], [], Usage(), NOW, {1}, SETTINGS)
    assert messages and set(messages) == watcher.urgent


def test_a_warning_about_something_you_are_not_using_is_not():
    watcher = alerts.BlockWatcher()
    messages = watcher.check([_item(1, "Discord")], [], Usage(), NOW, set(), SETTINGS)
    assert messages and not watcher.urgent


def test_a_block_starting_on_what_you_are_in_is_urgent():
    watcher = alerts.BlockWatcher()
    watcher.prev_blocked = set()
    started = datetime(2026, 9, 21, 21, 1)
    messages = watcher.check([_item(1, "Discord")], [], Usage(), started, {1}, SETTINGS)
    assert any("now blocked" in m for m in messages)
    assert watcher.urgent == set(messages)

    watcher = alerts.BlockWatcher()
    watcher.prev_blocked = set()
    watcher.check([_item(1, "Discord")], [], Usage(), started, set(), SETTINGS)
    assert not watcher.urgent
