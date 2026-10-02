"""0.84.0 database speed-ups: indexes on the hot queries, migrations only once (PRAGMA user_version), settings
read straight from the database (parsed values cached per text), and a window connection that can't be frozen by a lock - without
losing a write."""
import json
import sqlite3
import threading
import time as clock
from pathlib import Path
from datetime import date, datetime, timedelta

import pytest

import db as dbmod
import stats
from blocker import protection
from db import INDEXES, SCHEMA_VERSION, Database
from synthdata import build
from trusted_time import OFFSET_KEY, now_from_db

TODAY = date(2026, 10, 1)


def plan(db, sql, args=()):
    return " | ".join(r[3] for r in db.conn.execute("EXPLAIN QUERY PLAN " + sql, args))


# ---------- indexes ----------

@pytest.mark.parametrize("sql, args, index", [
    ("SELECT timestamp, exe, site FROM switch_events WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
     ("2026-09-01 00:00:00", "2026-10-01 00:00:00"), "idx_switch_events_ts"),
    ("SELECT substr(timestamp, 1, 10), COUNT(*) FROM switch_events WHERE timestamp >= ? AND timestamp < ? "
     "AND substr(timestamp, 12) < ? GROUP BY 1", ("2026-09-24 00:00:00", "2026-10-01 00:00:00", "12:00:00"),
     "idx_switch_events_ts"),
    ("SELECT item_id, display_name, hostname FROM block_events WHERE timestamp >= ? AND timestamp < ?",
     ("2026-10-01 00:00:00", "2026-10-02 00:00:00"), "idx_block_events_ts"),
    ("SELECT * FROM block_events WHERE timestamp >= ? ORDER BY +id DESC", ("2026-10-01 14:00:00",),
     "idx_block_events_ts"),
    ("SELECT owner, bucket, seconds FROM usage WHERE day >= ?", ("2026-08-22",), "idx_usage_day"),
    ("SELECT result, COUNT(*) FROM reminder_log WHERE what = ? AND timestamp >= ? GROUP BY result",
     ("break", "2026-10-01 00:00:00"), "idx_reminder_log_what_ts"),
    ("DELETE FROM block_rules WHERE item_id = ?", (1,), "idx_block_rules_item"),
    ("DELETE FROM group_rules WHERE group_id = ?", (1,), "idx_group_rules_group"),
    ("SELECT 1 FROM group_members WHERE item_id = ?", (1,), "idx_group_members_item"),
    ("DELETE FROM block_rules WHERE rule_type = 'temporary' AND temp_until <= ?", ("2026-10-01 00:00:00",),
     "idx_block_rules_temp"),
    ("SELECT minute, exe, site, seconds, active_seconds FROM activity WHERE minute >= ? AND minute < ? "
     "ORDER BY minute", ("2026-10-01 00:00", "2026-10-02 00:00"), "sqlite_autoindex_activity_1"),
    ("SELECT * FROM network_log WHERE minute >= ? ORDER BY minute DESC, count DESC", ("2026-10-01 14:00",),
     "sqlite_autoindex_network_log_1"),
])
def test_hot_queries_use_an_index(tmp_path, sql, args, index):
    """Each query as the code runs it (db.py, stats.py, reminders.py) is answered from an index, not a scan."""
    db = Database(tmp_path / "t.db")
    build(db.path, TODAY, days=10, extra_items=50, network_rows=200)
    db.conn.execute("ANALYZE")
    assert index in plan(db, sql, args), plan(db, sql, args)


def test_every_index_exists(tmp_path):
    db = Database(tmp_path / "t.db")
    names = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    for statement in INDEXES:
        assert statement.split(" ON ")[0].split()[-1] in names


# ---------- migrations once ----------

def _traced(monkeypatch):
    seen = []
    real = sqlite3.connect

    def connect(*a, **k):
        con = real(*a, **k)
        con.set_trace_callback(seen.append)
        return con
    monkeypatch.setattr(dbmod.sqlite3, "connect", connect)
    return seen


def test_migrations_run_once_then_open_is_cheap(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    Database(path).close()
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    seen = _traced(monkeypatch)
    import importer.popular
    import mojibake
    monkeypatch.setattr(importer.popular, "add_media_hosts", lambda db: pytest.fail("one-off ran again"))
    monkeypatch.setattr(mojibake, "repair_saved", lambda db: pytest.fail("one-off ran again"))
    Database(path)
    assert not [s for s in seen if "table_info" in s or "CREATE" in s or "ANALYZE" in s], seen
    assert len(seen) <= 4, seen


def test_an_old_database_is_upgraded_indexed_and_analyzed(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE blocked_items (id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, target TEXT NOT NULL,
            item_type TEXT NOT NULL, block_type TEXT, note TEXT, source TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE switch_events (id INTEGER PRIMARY KEY, timestamp DATETIME, exe TEXT, site TEXT);
        INSERT INTO switch_events (timestamp, exe, site) VALUES ('2026-09-30 10:00:00', 'code.exe', NULL);
    """)
    old.commit()
    old.close()
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert "disabled" in {r["name"] for r in db.conn.execute("PRAGMA table_info(blocked_items)")}
    assert db.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_switch_events_ts'").fetchone()
    assert db.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_stat1'").fetchone()   # ANALYZE ran
    assert db.conn.execute("SELECT COUNT(*) FROM switch_events").fetchone()[0] == 1


@pytest.mark.parametrize("attempt", range(15))
def test_two_processes_opening_a_fresh_database_together(tmp_path, attempt):
    """First start: the service and the tray create the file at the same moment. Switching a new file to WAL
    ignores SQLite's busy timeout, and one of them used to fail with "database is locked" (about 1 in 5 here)."""
    path = tmp_path / "t.db"
    errors = []

    def open_it():
        try:
            Database(path).close()
        except Exception as e:   # pragma: no cover
            errors.append(e)
    threads = [threading.Thread(target=open_it) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


# ---------- settings ----------

def test_settings_reads_see_writes_from_other_connections_at_once(tmp_path):
    path = tmp_path / "t.db"
    a, b = Database(path), Database(path)
    assert a.get_setting("k", "default") == "default"
    b.set_setting("k", "1")
    assert a.get_setting("k") == "1"            # no stale read: another connection committed
    a.set_setting("k", "2")
    assert a.get_setting("k") == "2" and b.get_setting("k") == "2"
    raw = sqlite3.connect(path)
    raw.execute("UPDATE settings SET value = '3' WHERE key = 'k'")
    raw.commit()
    assert a.get_setting("k") == "3"


def test_settings_read_is_one_statement(tmp_path):
    """0.84.0 checked PRAGMA data_version on every read to validate a cache - one statement swapped for another, and
    slower than the SELECT it saved (review). A read is now exactly one primary-key SELECT."""
    db = Database(tmp_path / "t.db")
    db.set_setting("x", "1")
    seen = []
    db.conn.set_trace_callback(seen.append)
    assert db.get_setting("x") == "1" and db.get_setting("missing", "d") == "d"
    assert len(seen) == 2 and all(s.startswith("SELECT value FROM settings") for s in seen), seen


def test_one_connection_used_from_several_threads_never_reads_a_stale_setting(tmp_path):
    """(review: a per-connection cache filled by one thread could hide another thread's newer write until a third
    connection committed. With no cache there is nothing to go stale.)"""
    db = Database(tmp_path / "t.db")
    db.set_setting("k", "old")
    old = db.get_setting("k")
    t = threading.Thread(target=db.set_setting, args=("k", "new"))
    t.start()
    t.join()
    assert old == "old" and db.get_setting("k") == "new"


def test_parsed_settings_are_parsed_once_per_value(tmp_path):
    db = Database(tmp_path / "t.db")
    calls = []

    def parse(text):
        calls.append(text)
        return json.loads(text or "{}")
    db.set_setting("j", '{"a": 1}')
    first = db.parsed("j", parse)
    assert db.parsed("j", parse) is first and len(calls) == 1
    db.set_setting("j", '{"a": 2}')
    assert db.parsed("j", parse) == {"a": 2} and len(calls) == 2


def test_now_from_db_follows_the_published_offset(tmp_path):
    path = tmp_path / "t.db"
    gui, service = Database(path), Database(path)
    service.set_setting(OFFSET_KEY, "3600")
    assert abs((now_from_db(gui) - datetime.now()).total_seconds() - 3600) < 5
    service.set_setting(OFFSET_KEY, "-60")
    assert abs((now_from_db(gui) - datetime.now()).total_seconds() + 60) < 5


def test_protection_list_names_are_cached_and_follow_saves(tmp_path):
    db = Database(tmp_path / "t.db")
    cfg = protection.settings(db)
    cfg["manual"] = [{"key": "m1", "name": "Mine", "entries": [{"name": f"s{i}", "host": f"s{i}.com"}
                                                               for i in range(2000)]}]
    protection.save_settings(db, cfg)
    names = protection.list_names(db)
    assert names == protection.all_lists(protection.settings(db)) and names["m1"][0] == "Mine"
    assert protection.list_names(db) is names                     # no re-parse per alert
    cfg["manual"][0]["name"] = "Renamed"
    protection.save_settings(db, cfg)
    assert protection.list_names(db)["m1"][0] == "Renamed"


def test_blocks_with_the_ticks_own_loads_is_the_same(tmp_path):
    db = Database(tmp_path / "t.db")
    info = build(db.path, TODAY, days=3)
    now = info["now"]
    usage, items, groups = db.usage_lookup(now), db.list_items(), db.list_groups()
    assert db.blocks(now, usage=usage, items=items, groups=groups) == db.blocks(now)


# ---------- the window's connection ----------

def _hold_lock(path, seconds):
    """Another process holding the write lock for `seconds`."""
    ready = threading.Event()

    def hold():
        con = sqlite3.connect(path, isolation_level=None)
        con.execute("BEGIN IMMEDIATE")
        ready.set()
        clock.sleep(seconds)
        con.execute("COMMIT")
        con.close()
    t = threading.Thread(target=hold)
    t.start()
    ready.wait()
    return t


def test_ui_connection_does_not_freeze_on_a_lock_and_loses_no_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "UI_BUSY_SEC", 0.3)
    path = tmp_path / "t.db"
    ui, other = Database(path, ui=True), Database(path)
    holder = _hold_lock(path, 1.2)
    started = clock.monotonic()
    ui.set_setting("notify.last_alert", '{"1": 5}')
    ui.set_setting("dash.visits_open", "1")
    ui.set_setting("notify.last_alert", '{"1": 6}')       # newest value wins
    assert clock.monotonic() - started < 1.0              # (was: up to 10 s of frozen window)
    assert ui.get_setting("notify.last_alert") == '{"1": 6}'   # the window sees its own write at once
    assert ui.all_settings()["dash.visits_open"] == "1"
    holder.join()
    assert ui.flush(10)
    assert other.get_setting("notify.last_alert") == '{"1": 6}' and other.get_setting("dash.visits_open") == "1"


def test_ui_structural_write_waits_for_the_lock_instead_of_failing(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "UI_BUSY_SEC", 0.2)
    path = tmp_path / "t.db"
    ui = Database(path, ui=True)
    holder = _hold_lock(path, 0.8)
    item = ui.add_site("Reddit", ["reddit.com"])          # retried past the short busy timeout, not lost
    holder.join()
    assert [i["id"] for i in Database(path).list_items()] == [item]


def test_ui_write_gives_up_with_an_error_after_the_retry_window(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "UI_BUSY_SEC", 0.1)
    monkeypatch.setattr(dbmod, "WRITE_RETRY_SEC", 0.3)
    path = tmp_path / "t.db"
    ui = Database(path, ui=True)
    holder = _hold_lock(path, 1.0)
    with pytest.raises(sqlite3.OperationalError):
        ui.add_site("Reddit", ["reddit.com"])             # gave up after WRITE_RETRY_SEC: an error, not a hang
    holder.join()
    assert Database(path).list_items() == []


def test_service_connection_keeps_the_long_timeout(tmp_path):
    assert Database(tmp_path / "t.db").conn.execute("PRAGMA busy_timeout").fetchone()[0] == dbmod.BUSY_SEC * 1000
    assert Database(tmp_path / "t.db", ui=True).conn.execute("PRAGMA busy_timeout").fetchone()[0] == \
        int(dbmod.UI_BUSY_SEC * 1000)


def test_daily_totals_are_one_query(tmp_path):
    db = Database(tmp_path / "t.db")
    build(db.path, TODAY, days=40)
    seen = []
    db.conn.set_trace_callback(seen.append)
    stats.daily_active(db, TODAY - timedelta(days=366), TODAY + timedelta(days=1))
    assert len(seen) == 1


def test_window_opening_right_after_an_update_waits_for_the_services_migration(tmp_path, monkeypatch):
    """After an update the service may hold the lock while it builds the new indexes; the window's short busy
    timeout applies only once it is open, so it doesn't fail to start."""
    monkeypatch.setattr(dbmod, "UI_BUSY_SEC", 0.1)
    path = tmp_path / "t.db"
    sqlite3.connect(path).execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)").connection.commit()
    holder = _hold_lock(path, 0.8)
    ui = Database(path, ui=True)
    holder.join()
    assert ui.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert ui.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 100


def test_a_waiting_setting_does_not_overwrite_a_newer_value_from_another_connection(tmp_path, monkeypatch):
    """Review (anti-bypass #2): a value the window kept in memory for the lock used to be written whenever the lock
    freed - on top of anything written meanwhile. It is now compare-and-set: the newer value stays."""
    monkeypatch.setattr(dbmod, "UI_BUSY_SEC", 0.1)
    path = tmp_path / "t.db"
    ui = Database(path, ui=True)
    Database(path).set_setting("ui.theme", "start")
    locked, deferred = threading.Event(), threading.Event()

    def other():                                             # holds the lock, and writes a newer value under it
        con = sqlite3.connect(path, isolation_level=None)
        con.execute("BEGIN IMMEDIATE")
        locked.set()
        deferred.wait(5)
        con.execute("UPDATE settings SET value = 'NEWER_FROM_OTHER' WHERE key = 'ui.theme'")
        clock.sleep(0.3)
        con.execute("COMMIT")
        con.close()
    t = threading.Thread(target=other)
    t.start()
    locked.wait(5)
    ui.set_setting("ui.theme", "STALE_FROM_GUI")             # deferred: the lock is held
    assert ui._pending
    deferred.set()
    t.join()
    assert ui.flush(10)
    assert Database(path).get_setting("ui.theme") == "NEWER_FROM_OTHER"
    assert ui.get_setting("ui.theme") == "NEWER_FROM_OTHER"


def test_compare_and_set_skips_a_key_changed_since_it_was_deferred(tmp_path):
    path = tmp_path / "t.db"
    db = Database(path)
    db.set_setting("a", "base")
    db.set_setting("b", "base")
    db.set_setting("a", "newer")                             # someone else, after the window deferred its value
    skipped = Database._write_batch(db.conn, {"a": ("mine", "base"), "b": ("mine", "base"), "c": ("mine", None)})
    assert skipped == ["a"]
    assert [db.get_setting(k) for k in "abc"] == ["newer", "mine", "mine"]


@pytest.mark.parametrize("key", ["app.off", "agent.exited", "antibypass", "modes.active", "modes.list",
                                 "limits.reset", "protection", "words", "reminders.sleep", "reminders.custom",
                                 "notify.enabled.limit", "retention.last_day", "clock_offset"])
def test_enforcement_settings_are_never_deferred(tmp_path, monkeypatch, key):
    """Review (anti-bypass #2/#3, threading #5): a tightening (switch back on, a mode starting, a list on, the tray
    app no longer "exited") kept in memory could be lost if the process is killed, or land late over a newer value.
    These keys are written before set_setting returns - waiting for the lock like any structural write."""
    import antibypass
    import modes
    from rules import RESET_KEY
    assert not dbmod.deferrable(key)
    for k in (antibypass.OFF_KEY, antibypass.EXITED_KEY, antibypass.SETTINGS_KEY, modes.ACTIVE_KEY, RESET_KEY,
              protection.SETTINGS_KEY):
        assert not dbmod.deferrable(k)
    monkeypatch.setattr(dbmod, "UI_BUSY_SEC", 0.1)
    path = tmp_path / "t.db"
    ui = Database(path, ui=True)
    holder = _hold_lock(path, 0.5)
    ui.set_setting(key, "1")
    assert not ui._pending                                   # written, not waiting in memory
    holder.join()
    assert Database(path).get_setting(key) == "1"


def test_display_settings_still_wait_in_memory(tmp_path):
    for key in ("ui.theme", "ui.accent", "ui.scale", "dash.hidden", "screentime.range", "notify.last_alert",
                "updates.last_check", "digest.last", "stats.goal_hours"):
        assert dbmod.deferrable(key), key


def test_writer_retries_any_database_error_and_loses_nothing(tmp_path, monkeypatch):
    """Review (anti-bypass #3): an error other than "locked" made the writer give up, leaving the value shown in
    the window but never written. It now retries."""
    monkeypatch.setattr(dbmod, "UI_BUSY_SEC", 0.1)
    path = tmp_path / "t.db"
    ui = Database(path, ui=True)
    real, fails = Database._write_batch, []

    def flaky(conn, batch):
        if len(fails) < 2:
            fails.append(1)
            raise sqlite3.OperationalError("disk I/O error")
        return real(conn, batch)
    monkeypatch.setattr(Database, "_write_batch", staticmethod(flaky))
    holder = _hold_lock(path, 0.3)
    ui.set_setting("ui.theme", "dark")
    holder.join()
    assert ui.flush(10) and len(fails) == 2
    assert Database(path).get_setting("ui.theme") == "dark"


def test_flush_writes_what_a_dead_writer_left(tmp_path):
    path = tmp_path / "t.db"
    ui = Database(path, ui=True)
    ui._pending["ui.theme"] = ("dark", None)                 # (as if the writer thread had died)
    assert ui.flush(5) and not ui._pending
    assert Database(path).get_setting("ui.theme") == "dark"


# ---------- list_items cache (review: 45 ms with 6 000 items, every 2-5 s) ----------

def test_list_items_is_reused_until_any_connection_changes_items_or_rules(tmp_path):
    path = tmp_path / "t.db"
    db, other = Database(path), Database(path)
    a = db.add_site("Reddit", ["reddit.com"])
    first = db.list_items()
    seen = []
    db.conn.set_trace_callback(seen.append)
    assert db.list_items() == first
    assert not [s for s in seen if "FROM blocked_items" in s or "FROM block_rules" in s], seen
    db.conn.set_trace_callback(None)
    other.add_site("YouTube", ["youtube.com"])                         # another connection
    assert [i["display_name"] for i in db.list_items()] == ["Reddit", "YouTube"]
    other.update_item(a, "Reddit", ["reddit.com"], None, [{"rule_type": "time_limit", "daily_limit_min": 5}])
    assert db.list_items()[0]["rules"][0]["daily_limit_min"] == 5      # a rule change on its own
    raw = sqlite3.connect(path)                                        # even a raw edit outside Lockdown
    raw.execute("UPDATE block_rules SET daily_limit_min = 1")
    raw.commit()
    assert db.list_items()[0]["rules"][0]["daily_limit_min"] == 1
    raw.execute("DELETE FROM blocked_items WHERE display_name = 'YouTube'")
    raw.commit()
    assert [i["display_name"] for i in db.list_items()] == ["Reddit"]
    db.remove_item(a)                                                  # its own write
    assert db.list_items() == []


def test_list_items_hands_out_copies(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_item("Discord", ["discord.exe"], "app", rules=[{"rule_type": "permanent"}])
    got = db.list_items()
    got[0]["display_name"] = "changed"
    got[0]["rules"][0]["rule_type"] = "changed"
    got[0]["rules"].append({})
    again = db.list_items()
    assert again[0]["display_name"] == "Discord" and again[0]["rules"] == [dict(again[0]["rules"][0])]
    assert again[0]["rules"][0]["rule_type"] == "permanent"


def test_status_poll_counts_items_without_loading_them():
    source = (Path(__file__).resolve().parents[1] / "src" / "gui" / "app.py").read_text(encoding="utf-8")
    poll = source[source.index("def _poll_status(self):"):source.index("def _poll_status_once")]
    assert "list_items()" not in poll and "item_count()" in poll


def test_schema_changes_come_with_a_schema_version_bump():
    """A change to SCHEMA / MIGRATIONS / INDEXES without bumping SCHEMA_VERSION is silently never applied to an
    existing database (the migration pass is skipped). Change both - then update the pinned hash here."""
    import hashlib
    text = dbmod.SCHEMA + repr(dbmod.MIGRATIONS) + repr(dbmod.INDEXES)
    pinned = {2: "bd713ebe83619146", 3: "bd713ebe83619146"}   # 3: same schema; re-runs the media-hosts one-off (0.84.1)
    assert pinned.get(SCHEMA_VERSION) == hashlib.sha256(text.encode()).hexdigest()[:16], \
        (SCHEMA_VERSION, hashlib.sha256(text.encode()).hexdigest()[:16])


def test_word_check_parses_its_settings_once_per_save(tmp_path, monkeypatch):
    """Review (perf #11): the word check parsed the keyword JSON every 0.5 s."""
    import keywords
    db = Database(tmp_path / "t.db")
    keywords.save(db, {**keywords.settings(db), "enabled": True})
    first = keywords.settings_shared(db)
    assert keywords.settings_shared(db) is first and first == keywords.settings(db)
    keywords.save(db, {**keywords.settings(db), "enabled": False})
    assert keywords.settings_shared(db)["enabled"] is False
    source = (Path(__file__).resolve().parents[1] / "src" / "monitor" / "word_guard.py").read_text(encoding="utf-8")
    assert "keywords.settings_shared(db)" in source and "keywords.settings(db)" not in source


BIG_TABLES = {"activity", "switch_events", "block_events", "reminder_log", "usage", "network_log", "daily_activity",
              "daily_switches", "daily_block_events", "daily_reminders"}


def test_the_real_code_paths_never_scan_a_growing_table(tmp_path):
    """Review (perf #9): the index test above checks hand-written copies of the SQL. This one records what the
    real functions run (stats, reminders, digest, db, retention itself) and asks SQLite for each statement's plan:
    none may walk a whole table that grows with time."""
    import re
    import digest
    import reminders
    import retention
    from datetime import time as dtime
    db = Database(tmp_path / "t.db")
    info = build(db.path, TODAY, days=60)
    db.conn.execute("ANALYZE")
    db.conn.commit()
    seen = []
    db.conn.set_trace_callback(seen.append)
    retention.run(db, TODAY)
    now, mid = info["now"], datetime.combine(TODAY, dtime())
    reminders.counts_many(db, ["break", "sleep"], mid - timedelta(days=7))
    for day in (TODAY, TODAY - timedelta(days=40)):
        stats.blocked_events(db, day)
        stats.app_totals(db, day, day + timedelta(days=1))
    stats.daily_active(db, TODAY - timedelta(days=365), TODAY + timedelta(days=1))
    stats.streaks(db, TODAY, 3600)
    stats.average_daily_switches(db, TODAY, until=now.time())
    stats.switches(db, TODAY - timedelta(days=30), TODAY + timedelta(days=1))
    stats.activity(db, TODAY - timedelta(days=7), TODAY + timedelta(days=1))
    db.block_events_after(5)
    db.block_events_since(now - timedelta(hours=1))
    db.usage_lookup(now)
    db.network_since((now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"))
    digest.summary(db, TODAY, 3600, lambda kind, name: name)
    db.conn.set_trace_callback(None)
    checked = 0
    for sql in seen:
        if not re.match(r"\s*(SELECT|WITH|INSERT|DELETE|UPDATE)", sql, re.I) or \
                not any(re.search(rf"\b{t}\b", sql) for t in BIG_TABLES):
            continue
        detail = " | ".join(r[3] for r in db.conn.execute("EXPLAIN QUERY PLAN " + sql))
        scanned = set(re.findall(r"\bSCAN (\w+)", detail)) & BIG_TABLES   # (with or without an index: all rows)
        assert not scanned, (sql[:160], detail)
        checked += 1
    assert checked > 30
