from datetime import datetime

from db import Database
from rules import make_schedule

MON_22 = datetime(2026, 9, 14, 22, 0)   # Monday 22:00
MON_12 = datetime(2026, 9, 14, 12, 0)


def test_add_list_remove(tmp_path):
    db = Database(tmp_path / "t.db")
    rid = db.add_site("Reddit", ["reddit.com", "old.reddit.com"])
    db.add_site("Twitter/X", ["x.com", "twitter.com"], source="quick-list")

    items = db.list_items()
    assert [i["display_name"] for i in items] == ["Reddit", "Twitter/X"]
    assert [r["rule_type"] for r in items[0]["rules"]] == ["permanent"]
    assert db.blocked_hostnames() == ["old.reddit.com", "reddit.com", "twitter.com", "x.com"]

    db.remove_item(rid)
    assert db.blocked_hostnames() == ["twitter.com", "x.com"]
    # rule was cascaded
    assert db.conn.execute("SELECT COUNT(*) FROM block_rules").fetchone()[0] == 1


def test_scheduled_and_temporary(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_site("Reddit", ["reddit.com"], rules=[
        {"rule_type": "scheduled", "schedule": make_schedule("block", [([0, 1, 2, 3, 4], "21:00", "07:00")])}])
    db.add_site("YouTube", ["youtube.com"], rules=[
        {"rule_type": "temporary", "temp_until": "2026-09-14 13:00:00"}])

    assert db.blocked_hostnames(MON_12) == ["youtube.com"]
    blocks = db.active_blocks(MON_22)
    assert list(blocks) == ["reddit.com"]
    assert blocks["reddit.com"]["reason"] == "schedule"
    assert blocks["reddit.com"]["until"] == datetime(2026, 9, 15, 7, 0)

    # expired temporary item gets cleaned up, scheduled one stays
    assert db.delete_expired_temporary(MON_22) == 1
    assert [i["display_name"] for i in db.list_items()] == ["Reddit"]


def test_stacked_rules_reason_priority(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_site("Reddit", ["reddit.com"], rules=[
        {"rule_type": "scheduled", "schedule": make_schedule("block", [(list(range(7)), "00:00", "23:59")])},
        {"rule_type": "permanent"}])
    assert db.active_blocks(MON_12)["reddit.com"]["reason"] == "permanent"


def test_block_events_and_notify(tmp_path):
    db = Database(tmp_path / "t.db")
    iid = db.add_site("Reddit", ["reddit.com"])
    db.update_item(iid, "Reddit", ["reddit.com"], "off", [{"rule_type": "permanent"}])
    assert db.list_items()[0]["notify"] == "off"
    assert db.last_block_event_id() == 0
    db.add_block_event("reddit.com", iid, "Reddit", "permanent", None, MON_12)
    db.add_block_event("www.reddit.com", iid, "Reddit", "schedule", datetime(2026, 9, 15, 7, 0), MON_12)
    events = db.block_events_after(1)
    assert len(events) == 1 and events[0]["until"] == "2026-09-15 07:00:00"


def test_migration_adds_notify_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE blocked_items (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, "
                "target TEXT NOT NULL, item_type TEXT NOT NULL, block_type TEXT, note TEXT, source TEXT, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
    old.commit()
    old.close()
    db = Database(path)
    db.add_site("Reddit", ["reddit.com"])
    assert db.list_items()[0]["notify"] is None


def test_update_item_replaces_rules(tmp_path):
    db = Database(tmp_path / "t.db")
    iid = db.add_site("Reddit", ["reddit.com"])
    db.update_item(iid, "Reddit!", ["reddit.com", "redd.it"], None,
                   [{"rule_type": "time_limit", "daily_limit_min": 30}])
    item = db.list_items()[0]
    assert item["display_name"] == "Reddit!" and item["target"] == "reddit.com redd.it"
    assert [(r["rule_type"], r["daily_limit_min"]) for r in item["rules"]] == [("time_limit", 30)]
    assert db.item_ids() == {iid}


def test_time_limit_uses_usage(tmp_path):
    db = Database(tmp_path / "t.db")
    iid = db.add_site("Reddit", ["reddit.com"], rules=[{"rule_type": "time_limit", "daily_limit_min": 1}])
    me = [(f"item:{iid}", "day:2026-09-14")]
    db.add_usage(me, 30, MON_12.date())
    assert db.blocked_hostnames(MON_12) == []
    db.add_usage(me, 30, MON_12.date())
    assert db.usage_lookup(MON_12)(f"item:{iid}", "day:2026-09-14") == 60
    block = db.active_blocks(MON_12)["reddit.com"]
    assert block["reason"] == "limit" and block["until"] == datetime(2026, 9, 15, 0, 0)
    # next day starts fresh
    assert db.blocked_hostnames(datetime(2026, 9, 15, 0, 1)) == []


def test_groups_shared_limit_and_membership(tmp_path):
    db = Database(tmp_path / "t.db")
    steam = db.add_item("Steam", ["steam.exe"], "app", block_type="kill")
    yt = db.add_item("YouTube", ["youtube.com"], "site")
    gid = db.add_group("Games", [{"rule_type": "time_limit", "daily_limit_min": 1}], {steam: {}, yt: {}})
    [g] = db.list_groups()
    assert g["name"] == "Games" and set(g["members"]) == {steam, yt}
    db.add_usage([(f"group:{gid}", "day:2026-09-14")], 60, MON_12.date())   # shared total used up
    assert {b["item"]["display_name"] for b in db.blocks(MON_12)} == {"Steam", "YouTube"}
    assert db.blocks(MON_12)[0]["rule"]["group"]["name"] == "Games"
    # items only in a group survive the cleanup of rule-less items
    assert db.delete_expired_temporary(MON_12) == 0
    db.remove_group(gid)
    assert db.blocks(MON_12) == []
    assert db.delete_expired_temporary(MON_12) == 2


def test_member_override(tmp_path):
    db = Database(tmp_path / "t.db")
    night = make_schedule("block", [(list(range(7)), "21:00", "07:00")])
    a = db.add_item("Discord", ["discord.exe"], "app")
    b = db.add_item("Signal", ["signal.exe"], "app")
    db.add_group("Night", [{"rule_type": "scheduled", "schedule": night}],
                 {a: {}, b: {"scheduled": {"schedule": night, "allowance_min": 5}}})
    assert {x["item"]["display_name"] for x in db.blocks(MON_22)} == {"Discord"}   # Signal has 5 min left


def test_migrates_old_usage_table(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE site_usage (date TEXT, item_id INTEGER, seconds INTEGER, PRIMARY KEY (date, item_id))")
    old.execute("INSERT INTO site_usage VALUES ('2026-09-14', 7, 120)")
    old.commit()
    old.close()
    db = Database(path)
    assert db.usage_lookup(MON_12)("item:7", "day:2026-09-14") == 120


def test_history(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_history("reddit.com", "Reddit")
    db.add_history("x.com", "X")
    db.add_history("reddit.com", "Reddit")
    assert [h["hostname"] for h in db.history()][0] in ("reddit.com", "x.com")
    assert len(db.history()) == 2
    db.clear_history()
    assert db.history() == []


def test_settings(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.get_setting("x", "d") == "d"
    db.set_setting("x", "1")
    db.set_setting("x", "2")
    assert db.get_setting("x") == "2"
