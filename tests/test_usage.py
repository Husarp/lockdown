from datetime import datetime

from db import Database
from monitor.usage import UsageTracker, items_in_use, match_item

ITEMS = [{"id": 1, "target": "youtube.com youtu.be", "item_type": "site"},
         {"id": 2, "target": "reddit.com", "item_type": "site"},
         {"id": 3, "target": "discord.exe", "item_type": "app"}]


def test_match_item():
    assert match_item("youtube.com", ITEMS)["id"] == 1
    assert match_item("m.youtube.com", ITEMS)["id"] == 1
    assert match_item("old.reddit.com", ITEMS)["id"] == 2
    assert match_item("notyoutube.com", ITEMS) is None


def test_items_in_use():
    assert [i["id"] for i in items_in_use("discord.exe", None, True, ITEMS)] == [3]       # apps count when idle
    assert [i["id"] for i in items_in_use("brave.exe", "youtube.com/watch", False, ITEMS)] == [1]
    assert items_in_use("brave.exe", "youtube.com/watch", True, ITEMS) == []              # away from the PC
    assert items_in_use("brave.exe", "hunger games", False, ITEMS) == []                  # search text
    assert items_in_use(None, None, True, ITEMS) == []


def test_tick_adds_to_item_group_and_allowance_buckets(tmp_path):
    db = Database(tmp_path / "t.db")
    discord = db.add_item("Discord", ["discord.exe"], "app")
    gid = db.add_group("Games", [{"rule_type": "time_limit", "daily_limit_min": 60}], {discord: {}})
    tracker = UsageTracker()
    tracker.tick(db, lambda: ("discord.exe", None, False))
    tracker.tick(db, lambda: ("discord.exe", None, False))
    tracker.tick(db, lambda: ("notepad.exe", None, False))
    usage = db.usage_lookup(datetime.now())
    today = f"day:{datetime.now().date().isoformat()}"
    assert usage(f"item:{discord}", today) == 4
    assert usage(f"group:{gid}", today) == 4          # shared group limit counts too
    assert tracker.in_use == set()
