"""Blocking a whole category: one blocker on "Distracting" covers everything in it, on the blocklist or not,
and a time limit on it is a single shared pot."""
import json
from datetime import datetime, timedelta

import modes
from db import Database
from monitor.usage import items_in_use
from rules import usage_targets, effective_rules

NOW = datetime(2026, 9, 20, 12, 0)


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_category("app", "discord.exe", "distracting")
    db.set_category("site", "reddit.com", "distracting")
    db.set_category("app", "code.exe", "productive")
    return db


def test_a_blocked_category_covers_everything_in_it(tmp_path):
    db = _db(tmp_path)
    db.add_item("Distracting", ["distracting"], "category", rules=[{"rule_type": "permanent"}])
    blocked = {(b["item"]["item_type"], b["item"]["target"]) for b in db.blocks(NOW)}
    assert ("app", "discord.exe") in blocked          # marked on Screen Time, never added to Blocking
    assert ("site", "reddit.com") in blocked
    assert ("app", "code.exe") not in blocked         # productive: left alone
    assert db.blocked_hostnames(NOW) == ["reddit.com"]
    db.close()


def test_the_category_itself_keeps_its_reason_and_members_keep_their_own(tmp_path):
    db = _db(tmp_path)
    db.add_item("Distracting", ["distracting"], "category", rules=[{"rule_type": "permanent"}])
    # reddit is also blocked in its own right, until 13:00 - that block wins for reddit
    db.add_site("Reddit", ["reddit.com"], rules=[{"rule_type": "temporary",
                                                  "temp_until": (NOW + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")}])
    by_target = {b["item"]["target"]: b for b in db.blocks(NOW)}
    assert by_target["reddit.com"]["reason"] == "temporary"
    assert by_target["discord.exe"]["reason"] == "permanent"      # from the category
    assert by_target["distracting"]["item"]["item_type"] == "category"
    db.close()


def test_an_empty_category_blocks_nothing(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_item("Distracting", ["distracting"], "category", rules=[{"rule_type": "permanent"}])
    assert db.blocked_hostnames(NOW) == []
    db.close()


def test_the_category_is_in_use_while_any_of_its_members_is(tmp_path):
    db = _db(tmp_path)
    cat_id = db.add_item("Distracting", ["distracting"], "category",
                         rules=[{"rule_type": "time_limit", "daily_limit_min": 120}])
    items, cats = db.list_items(), db.categories()

    used = items_in_use("discord.exe", None, False, items, cats)
    assert [i["id"] for i in used] == [cat_id]                    # an app in the category
    used = items_in_use("chrome.exe", "https://reddit.com/r/x", False, items, cats)
    assert [i["id"] for i in used] == [cat_id]                    # a site in the category
    assert items_in_use("code.exe", None, False, items, cats) == []   # productive: not this category
    db.close()


def test_the_limit_is_one_pot_for_the_whole_category(tmp_path):
    db = _db(tmp_path)
    cat_id = db.add_item("Distracting", ["distracting"], "category",
                         rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    items, cats = db.list_items(), db.categories()
    groups = db.list_groups()
    rules = effective_rules(next(i for i in items if i["id"] == cat_id), groups)
    # 40 minutes of Discord and then 30 of Reddit go into the same bucket, so the hour is used up
    for exe, url, minutes in (("discord.exe", None, 40), ("chrome.exe", "https://reddit.com", 30)):
        for item in items_in_use(exe, url, False, items, cats):
            db.add_usage(usage_targets(rules, item["id"], NOW), minutes * 60, NOW.date())
    assert any(b["item"]["target"] == "distracting" and b["reason"] == "limit" for b in db.blocks(NOW))
    assert "discord.exe" in {b["item"]["target"] for b in db.blocks(NOW)}
    db.close()


def test_a_mode_and_a_blocker_agree_on_what_is_in_a_category(tmp_path):
    db = _db(tmp_path)
    db.add_site("TikTok", ["tiktok.com"])            # on the blocklist, so Distracting unless set otherwise
    items, cats = db.list_items(), db.categories()
    from_blocker = {(i["item_type"], i["target"]) for i in modes.category_members({"distracting"}, items, cats)}
    from_mode = {(i["item_type"], i["target"])
                 for i in modes.targets({"categories": ["distracting"]}, items, [], cats)}
    assert ("site", "tiktok.com") in from_blocker and ("site", "tiktok.com") in from_mode
    assert ("app", "discord.exe") in from_blocker and ("app", "discord.exe") in from_mode
    db.close()
