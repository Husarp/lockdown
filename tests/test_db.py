from db import Database


def test_add_list_remove(tmp_path):
    db = Database(tmp_path / "t.db")
    rid = db.add_site("Reddit", ["reddit.com", "old.reddit.com"])
    db.add_site("Twitter/X", ["x.com", "twitter.com"], source="quick-list")

    items = db.list_items()
    assert [i["display_name"] for i in items] == ["Reddit", "Twitter/X"]
    assert items[0]["rules"] == "permanent"
    assert db.blocked_hostnames() == ["old.reddit.com", "reddit.com", "twitter.com", "x.com"]

    db.remove_item(rid)
    assert db.blocked_hostnames() == ["twitter.com", "x.com"]
    # rule was cascaded
    assert db.conn.execute("SELECT COUNT(*) FROM block_rules").fetchone()[0] == 1


def test_settings(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.get_setting("x", "d") == "d"
    db.set_setting("x", "1")
    db.set_setting("x", "2")
    assert db.get_setting("x") == "2"
