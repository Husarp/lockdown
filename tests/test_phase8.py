import csv
import json
from datetime import date, datetime, timedelta

import backup
import digest
import stats
from db import Database

TODAY = date(2026, 9, 20)   # a Sunday


def _activity(db, day: date, minutes: int, exe="game.exe", site=""):
    for m in range(minutes):
        t = datetime.combine(day, datetime.min.time()) + timedelta(hours=10, minutes=m)
        db.add_activity(t.strftime("%Y-%m-%d %H:%M"), exe, site, 60, 60)


def test_backup_round_trip(tmp_path):
    db = Database(tmp_path / "a.db")
    yt = db.add_item("YouTube", ["youtube.com", "youtu.be"], "site", "popular",
                     [{"rule_type": "time_limit", "daily_limit_min": 60}])
    game = db.add_item("Game", ["game.exe"], "app", "app-browser", [{"rule_type": "permanent"}], None, "close,background",
                       r"C:\Games\game.exe")
    db.add_group("Evening", [{"rule_type": "scheduled", "schedule": json.dumps({"mode": "block", "windows": []})}],
                 {yt: {}, game: {"scheduled": {"rule_type": "scheduled", "allowance_min": 5}}})
    db.set_category("app", "game.exe", "distracting")
    db.set_setting("ui.accent", "#2F6FEB")
    db.set_setting("service_heartbeat", "123")                                    # runtime: not exported
    db.set_setting("antibypass", json.dumps({"phrase": True, "unlocked_until": "2026-09-20 10:00:00"}))
    path = tmp_path / "backup.json"
    backup.save(db, str(path))
    data = backup.load(str(path))
    assert "service_heartbeat" not in data["settings"]
    assert json.loads(data["settings"]["antibypass"])["unlocked_until"] is None

    other = Database(tmp_path / "b.db")
    other.add_item("Old", ["old.example"], "site", "manual", [{"rule_type": "permanent"}])
    backup.restore(other, data)
    items = {i["display_name"]: i for i in other.list_items()}
    assert set(items) == {"YouTube", "Game"}                                      # replaced, not added
    assert items["YouTube"]["rules"][0]["daily_limit_min"] == 60
    assert items["Game"]["block_type"] == "close,background" and items["Game"]["app_path"] == r"C:\Games\game.exe"
    group = other.list_groups()[0]
    assert group["name"] == "Evening" and set(group["members"]) == {items["YouTube"]["id"], items["Game"]["id"]}
    assert group["members"][items["Game"]["id"]]["scheduled"]["allowance_min"] == 5
    assert other.categories()[("app", "game.exe")] == "distracting"
    assert other.get_setting("ui.accent") == "#2F6FEB"

    (tmp_path / "bad.json").write_text('{"hello": 1}')
    try:
        backup.load(str(tmp_path / "bad.json"))
        assert False
    except ValueError as e:
        assert "isn't a Lockdown backup" in str(e)


def test_screen_time_csv(tmp_path):
    db = Database(tmp_path / "a.db")
    _activity(db, date.today(), 30, "chrome.exe", "youtube.com")
    _activity(db, date.today(), 10)
    out = tmp_path / "st.csv"
    assert backup.screen_time_csv(db, str(out)) == 2
    rows = list(csv.reader(open(out, encoding="utf-8")))
    assert rows[0][:4] == ["date", "app", "site", "minutes"] and rows[1][1:4] == ["chrome.exe", "youtube.com", "30.0"]


def test_streaks(tmp_path):
    db = Database(tmp_path / "a.db")
    assert stats.streaks(db, TODAY, 3600) == {"goal": 0, "no_unlock": 0}           # nothing recorded yet
    for i, minutes in enumerate([30, 20, 50, 90, 10]):                               # today, yesterday, ... 4 days ago
        _activity(db, TODAY - timedelta(days=i), minutes)
    s = stats.streaks(db, TODAY, 3600)
    assert s["goal"] == 3                        # 3 days back was 90 min > 60
    assert s["no_unlock"] == 5                   # every recorded day
    start = datetime.combine(TODAY - timedelta(days=1), datetime.min.time()) + timedelta(hours=12)
    db.add_unlock([1], ["Game"], start, start + timedelta(minutes=20))
    assert stats.streaks(db, TODAY, 3600)["no_unlock"] == 1
    assert stats.streaks(db, TODAY, None)["goal"] == 0                               # no goal set


def test_weekly_summary(tmp_path):
    db = Database(tmp_path / "a.db")
    for i in range(7):
        _activity(db, TODAY - timedelta(days=i), 60, "chrome.exe", "youtube.com")
    for i in range(7, 14):
        _activity(db, TODAY - timedelta(days=i), 120)
    text = digest.summary(db, TODAY, 2 * 3600, lambda kind, name: "Chrome" if name == "chrome.exe" else name)
    assert text.startswith("This week: 7 h 00 m of screen time (-50% vs last week).")
    assert "top app: Chrome (7 h 00 m)" in text and "top site: youtube.com" in text
    assert "within your goal on 7 of 7 days" in text
    sunday_evening = datetime.combine(TODAY, datetime.min.time()) + timedelta(hours=19, minutes=5)
    assert not digest.due(db, sunday_evening - timedelta(hours=1))                  # before 19:00
    assert digest.due(db, sunday_evening)
    digest.mark_shown(db, sunday_evening)
    assert not digest.due(db, sunday_evening + timedelta(hours=1))                  # once a week
    db.set_setting("digest.enabled", "0")
    assert not digest.due(db, sunday_evening + timedelta(days=7))


def test_update_keeps_an_older_database(tmp_path):
    """An update (new program, same C:\ProgramData\Lockdown) opens an older database: new columns are added,
    nothing is lost."""
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE blocked_items (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, target TEXT NOT NULL,
            item_type TEXT NOT NULL, block_type TEXT, note TEXT, source TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE block_rules (id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL, rule_type TEXT NOT NULL,
            schedule TEXT, temp_until TEXT, daily_limit_min INTEGER, duration_min INTEGER, daily_switch_limit INTEGER);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO blocked_items (display_name, target, item_type, source) VALUES ('Reddit', 'reddit.com', 'site', 'x');
        INSERT INTO block_rules (item_id, rule_type, daily_limit_min) VALUES (1, 'time_limit', 45);
        INSERT INTO settings VALUES ('ui.accent', '#2F6FEB');
    """)
    con.commit()
    con.close()
    db = Database(path)
    item = db.list_items()[0]
    assert item["display_name"] == "Reddit" and item["notify"] is None and item["app_path"] is None
    assert item["rules"][0]["daily_limit_min"] == 45 and item["rules"][0]["weekly_limit_min"] is None
    assert db.get_setting("ui.accent") == "#2F6FEB"
    db.add_item("New", ["new.example"], "site", "manual", [{"rule_type": "time_limit", "weekly_limit_min": 300}])
    assert len(db.list_items()) == 2
