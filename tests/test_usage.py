from types import SimpleNamespace

from db import Database
from monitor.usage import UsageTracker, match_item

ITEMS = [{"id": 1, "target": "youtube.com youtu.be"}, {"id": 2, "target": "reddit.com"}]


def test_match_item():
    assert match_item("youtube.com", ITEMS)["id"] == 1
    assert match_item("m.youtube.com", ITEMS)["id"] == 1
    assert match_item("old.reddit.com", ITEMS)["id"] == 2
    assert match_item("notyoutube.com", ITEMS) is None


def fake_browser(url, idle=0):
    return SimpleNamespace(foreground_browser_url=lambda: url, idle_seconds=lambda: idle)


def test_tick_counts_only_limited_sites(tmp_path):
    db = Database(tmp_path / "t.db")
    yt = db.add_site("YouTube", ["youtube.com"], rules=[{"rule_type": "time_limit", "daily_limit_min": 30}])
    db.add_site("Reddit", ["reddit.com"])  # permanent only -> not counted
    UsageTracker.tick(db, fake_browser("https://www.youtube.com/watch?v=x"))
    UsageTracker.tick(db, fake_browser("youtube.com/feed"))
    UsageTracker.tick(db, fake_browser("reddit.com"))
    UsageTracker.tick(db, fake_browser("youtube.com", idle=3600))   # away
    UsageTracker.tick(db, fake_browser("hunger games"))              # search text
    UsageTracker.tick(db, fake_browser(None))                        # no browser in front
    usage = db.conn.execute("SELECT item_id, seconds FROM site_usage").fetchall()
    assert [tuple(r) for r in usage] == [(yt, 4)]
