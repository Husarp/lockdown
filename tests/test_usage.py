from datetime import datetime, timedelta

from db import Database
from monitor import usage as usage_mod
from monitor.usage import UsageTracker, items_in_use, match_item

NOON = datetime(2026, 10, 2, 12, 0)


def every_2s(monkeypatch) -> UsageTracker:
    """A tracker whose ticks come 2 s of trusted time apart (each counts those 2 s), counting since NOON."""
    ticks = iter(range(1, 100_000))
    monkeypatch.setattr(usage_mod, "now_from_db", lambda db: NOON + timedelta(seconds=2 * next(ticks)))
    tracker = UsageTracker()
    tracker.counted_ts = NOON.timestamp()
    return tracker

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


def test_tick_adds_to_item_group_and_allowance_buckets(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    discord = db.add_item("Discord", ["discord.exe"], "app")
    gid = db.add_group("Games", [{"rule_type": "time_limit", "daily_limit_min": 60}], {discord: {}})
    tracker = every_2s(monkeypatch)
    tracker.tick(db, lambda: ("discord.exe", None, 0))
    tracker.tick(db, lambda: ("discord.exe", None, 0))
    tracker.tick(db, lambda: ("notepad.exe", None, 0))
    usage = db.usage_lookup(NOON)
    today = f"day:{NOON.date().isoformat()}"
    assert usage(f"item:{discord}", today) == 4
    assert usage(f"group:{gid}", today) == 4          # shared group limit counts too
    assert tracker.in_use == set()


def test_screen_time_and_switches(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    tracker = every_2s(monkeypatch)
    for sense in [("code.exe", None, 0), ("code.exe", None, 0), ("brave.exe", "https://www.youtube.com/x", 0),
                  ("brave.exe", "reddit.com", 400), (None, None, 0), ("code.exe", None, 0)]:
        tracker.tick(db, lambda s=sense: s)
    rows = {(r["exe"], r["site"]): (r["seconds"], r["active_seconds"]) for r in db.conn.execute(
        "SELECT exe, site, SUM(seconds) seconds, SUM(active_seconds) active_seconds FROM activity GROUP BY exe, site")}
    assert rows == {("code.exe", ""): (6, 6), ("brave.exe", "youtube.com"): (2, 2), ("brave.exe", "reddit.com"): (2, 0)}
    switches = [(r["exe"], r["site"]) for r in db.conn.execute("SELECT exe, site FROM switch_events ORDER BY id")]
    # first tick is the starting point, not a switch; the locked screen in between isn't one either
    assert switches == [("brave.exe", "youtube.com"), ("brave.exe", "reddit.com"), ("code.exe", "")]


def test_every_switch_mode(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_item("Discord", ["discord.exe"], "app",
                rules=[{"rule_type": "switch_limit", "daily_switch_limit": 1, "switch_mode": "switch"}])
    tracker = UsageTracker()
    none = lambda: set()
    for exe in ["code.exe", "discord.exe", "discord.exe", "code.exe"]:   # switched to it once
        tracker.tick(db, lambda e=exe: (e, None, 0), none)
    assert db.blocks(datetime.now()) == []
    tracker.tick(db, lambda: ("discord.exe", None, 0), none)              # second switch -> blocked
    assert [b["reason"] for b in db.blocks(datetime.now())] == ["switches"]


def test_launch_mode_ignores_clicking_back(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_item("Discord", ["discord.exe"], "app", rules=[{"rule_type": "switch_limit", "daily_switch_limit": 1}])
    tracker = UsageTracker()
    steps = [("code.exe", set()), ("discord.exe", {"discord.exe"}),         # launched once
             ("code.exe", {"discord.exe"}), ("discord.exe", {"discord.exe"}), ("code.exe", {"discord.exe"})]
    for exe, running in steps:                                            # clicking back and forth: no count
        tracker.tick(db, lambda e=exe: (e, None, 0), lambda r=running: r)
    assert db.blocks(datetime.now()) == []
    tracker.tick(db, lambda: ("code.exe", None, 0), lambda: set())         # closed
    tracker.tick(db, lambda: ("discord.exe", None, 0), lambda: {"discord.exe"})   # launched again -> over the limit
    assert [b["reason"] for b in db.blocks(datetime.now())] == ["switches"]
