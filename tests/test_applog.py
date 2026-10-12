"""The App log (src/applog.py, src/applog_changes.py): one append-only list of what Lockdown did and what happened to it,
written by the agent, the service and the installer, readable (and shareable) from Settings > App log.

What is checked: an event is written for every kind the owner asked for (start / stop / updated from X to Y / closed
without Exit / tray Exit / window failed and tried again / crash / settings and rules changes in plain words / challenge
passed and failed / Anti-Bypass changes / switched off and on / pause / emergency unlock / backup / list download failed /
hosts file repaired / clock changed / update check failed / install and uninstall), the change texts say what changed,
NOTHING a user visits or opens is ever written, the list is capped and trimmed by age, and reading 20 000 events is fast."""
import json
import logging
import sqlite3
import statistics
import threading
import time
from datetime import datetime, timedelta

import pytest

import antibypass
import applog
import applog_changes
import backup
import emergency
import modes
import pause
import reminders
import rules
import updates
from blocker import hosts, protection
from db import SCHEMA_VERSION, Database
from gui.draft import Draft
from webapi import applog_api, core, guard, make_context


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "config.db")
    yield d
    d.close()


@pytest.fixture(autouse=True)
def agent_source():
    applog.set_source("agent")
    applog._repeats.clear()
    yield
    applog.set_source("agent")


def log(db, chip="all", **kw):
    """The texts of the events, oldest first."""
    return [e["text"] for e in reversed(applog.query(db.conn, chip, limit=kw.pop("limit", 5000), **kw))]


def kinds(db):
    return [e["kind"] for e in reversed(applog.query(db.conn, limit=5000))]


def only(db, kind=None):
    rows = [e for e in applog.query(db.conn, limit=5000) if kind in (None, e["kind"])]
    assert len(rows) == 1, rows
    return rows[0]["text"]


# ---------------------------------------------------------------- the store

def test_the_table_is_in_a_fresh_database_and_an_old_one_gets_it(tmp_path):
    path = tmp_path / "old.db"
    d = Database(path)
    assert d.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    d.conn.execute("DROP TABLE app_events")
    d.conn.execute("PRAGMA user_version=9")
    d.conn.commit()
    d.close()
    d = Database(path)                                          # the one before the App log: opening makes the table
    assert applog.count(d.conn) == 0
    assert applog.record("start", "hello", d) and log(d) == ["hello"]
    d.close()


def test_one_event_is_one_cheap_row_with_the_source_the_kind_and_the_time(db):
    applog.set_source("service")
    assert applog.record("start", "The Lockdown service 1.0 started", db)
    row = applog.query(db.conn)[0]
    assert (row["source"], row["kind"], row["text"]) == ("service", "start", "The Lockdown service 1.0 started")
    assert datetime.strptime(row["ts"], applog.TS_FORMAT) <= datetime.now()
    applog.record("start", "from the installer", db, source="installer")
    assert applog.query(db.conn)[0]["source"] == "installer"


def test_text_is_one_short_line(db):
    applog.record("problem", "line one\n  line   two\r\n" + "x" * 2000, db)
    text = only(db)
    assert "\n" not in text and "  " not in text and len(text) <= applog.MAX_TEXT and text.endswith("…")
    applog.record("problem", "", db)                             # nothing to say: nothing written
    assert applog.count(db.conn) == 1


def test_writing_never_raises_and_quiet_writes_nothing(db):
    class Broken:
        def add_app_event(self, *a):
            raise sqlite3.OperationalError("database is locked")
    assert applog.record("problem", "x", Broken()) is False
    assert applog.record("problem", "x", object()) is False
    with applog.quiet():
        assert applog.record("problem", "silent", db) is False
        db.set_setting("stats.goal_hours", "9")                  # (a described setting: also silent)
    assert applog.count(db.conn) == 0
    assert applog.record("problem", "back", db) is True


def test_a_write_without_a_database_opens_its_own_short_connection(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "sub" / "config.db")
    assert applog.record("window_failed", "no Database in sight", source="agent")
    con = sqlite3.connect(paths.DB_PATH)
    assert con.execute("SELECT kind, text FROM app_events").fetchall() == [("window_failed", "no Database in sight")]
    con.close()


def test_the_log_is_capped_to_the_newest_rows(db, monkeypatch):
    monkeypatch.setattr(applog, "MAX_ROWS", 100)
    monkeypatch.setattr(applog, "TRIM_EVERY", 25)
    for i in range(260):
        applog.record("change", f"event {i}", db)
    n = applog.count(db.conn)
    assert 100 <= n <= 124                                       # (trimmed on every 25th row: never far over)
    newest = log(db, limit=10)
    assert newest[-1] == "event 259" and "event 0" not in log(db)
    assert [e["id"] for e in applog.query(db.conn, limit=3)] == [260, 259, 258]    # (the newest are the ones kept)


def test_the_real_cap_is_20000_rows_and_90_days():
    assert applog.MAX_ROWS == 20_000 and applog.KEEP_DAYS == 90


def test_rows_older_than_90_days_are_dropped(db):
    old = (datetime.now() - timedelta(days=91)).strftime(applog.TS_FORMAT)
    fresh = (datetime.now() - timedelta(days=89)).strftime(applog.TS_FORMAT)
    db.conn.executemany("INSERT INTO app_events (ts, source, kind, text) VALUES (?, 'agent', 'change', ?)",
                        [(old, "too old"), (fresh, "still kept")])
    db.conn.commit()
    with db.conn:
        applog.trim(db.conn)
    assert log(db) == ["still kept"]


def test_starting_trims_too(db):
    old = (datetime.now() - timedelta(days=200)).strftime(applog.TS_FORMAT)
    db.conn.execute("INSERT INTO app_events (ts, source, kind, text) VALUES (?, 'agent', 'change', 'ancient')", (old,))
    db.conn.commit()
    applog.process_started(db, "1.0")
    assert "ancient" not in log(db)


def test_the_chips_search_and_paging(db):
    for kind, text in [("change", "Rule changed: YouTube"), ("updated", "Updated from 0.86 to 0.87"),
                       ("crash", "Unexpected error"), ("challenge_passed", "challenge passed"),
                       ("start", "Lockdown started"), ("update_check_failed", "no answer")]:
        applog.record(kind, text, db)
    assert log(db, "changes") == ["Rule changed: YouTube"]
    assert log(db, "updates") == ["Updated from 0.86 to 0.87", "no answer"]
    assert log(db, "problems") == ["Unexpected error", "no answer"]
    assert log(db, "antibypass") == ["challenge passed"]
    assert log(db, "all", search="youtube") == ["Rule changed: YouTube"]
    assert log(db, "all", search="100%") == [] and log(db, "all", search="_") == []        # (LIKE wildcards are plain text)
    page = applog.query(db.conn, limit=2)
    older = applog.query(db.conn, before=page[-1]["id"], limit=10)
    assert [e["id"] for e in page + older] == [6, 5, 4, 3, 2, 1]


# ---------------------------------------------------------------- settings: what changed, in words

def lines(db):
    return log(db)


def test_a_setting_that_did_not_change_writes_nothing_and_a_hot_key_is_never_looked_at(db):
    db.set_setting("stats.goal_hours", "4")
    db.set_setting("stats.goal_hours", "4")
    for key in ("service_heartbeat", "clock_offset", "notify.last_alert", "updates.last_check", "dash.hidden",
                "screentime.tab", "usage.counted_until", "firewall_rules", "protection.progress", "digest.last",
                "retention.last_day", "apps.members", "ui.blocking.sort"):
        db.set_setting(key, "1")
        assert not applog_changes.watched(key), key
    assert lines(db) == ["Setting changed: Daily goal off → 4 h"]


def test_simple_settings_say_what_they_were_and_what_they_are(db):
    db.set_setting("stats.goal_hours", "3")
    db.set_setting("stats.goal_hours", "4")
    db.set_setting("icons.download", "0")
    db.set_setting("icons.download", "1")
    db.set_setting("updates.auto", "0")
    db.set_setting("ui.theme", "light")
    db.set_setting("ui.scale", "80")
    db.set_setting("ui.reduce_motion", "1")
    db.set_setting("ui.autosave", "0")
    assert lines(db) == [
        "Setting changed: Daily goal off → 3 h", "Setting changed: Daily goal 3 h → 4 h",
        "Setting changed: Download icons for my sites turned off", "Setting changed: Download icons for my sites turned on",
        "Setting changed: Check for updates automatically turned off", "Setting changed: Theme dark → light",
        "Setting changed: Interface size automatic → 80%", "Setting changed: Reduce motion turned on",
        "Setting changed: Auto-save turned off"]


def test_notification_settings(db):
    db.set_setting("notify.warn.minutes", "10")
    db.set_setting("notify.warn.enabled", "0")
    db.set_setting("notify.enabled.limit", "0")
    db.set_setting("notify.msg.permanent", "my own words, which stay out of the log")
    assert lines(db) == ["Setting changed: Notifications - Warn (min) before 5 → 10",
                         "Setting changed: Notifications - Warning before a block turned off",
                         "Notifications: the alert for \"limit\" turned off",
                         "Notifications: the message for \"permanent\" was changed"]


def test_antibypass_changes_name_what_changed_but_never_the_phrase(db):
    cfg = antibypass.settings(db)
    antibypass.save(db, {**cfg, "phrase": True, "length": 90, "custom_phrase": "my secret phrase", "wait_min": 10})
    antibypass.save(db, {**antibypass.settings(db), "windows": [{"days": [6], "start": "18:00", "end": "20:00"}, ],
                         "hours": True})
    text = " | ".join(lines(db))
    assert "challenge turned on" in text and "phrase length 60 → 90" in text and "wait (min) 0 → 10" in text
    assert "own phrase changed" in text and "allowed hours turned on" in text
    assert "secret" not in text
    assert set(kinds(db)) == {"antibypass_change"}


def test_an_unlock_is_not_a_change_of_anti_bypass(db):
    antibypass.save(db, {**antibypass.settings(db), "phrase": True})
    before = applog.count(db.conn)
    antibypass.unlock(db, datetime(2026, 10, 12, 12, 0))
    antibypass.lock(db)
    assert applog.count(db.conn) == before


def test_switching_off_and_on_pause_and_tray_exit(db):
    now = datetime(2026, 10, 12, 12, 0)
    antibypass.switch_off(db, now)
    antibypass.switch_on(db)
    pause.start(db, now, 30, silent=True)
    pause.resume(db)
    db.set_setting("agent.exited", "1")
    db.set_setting("agent.exited", "0")
    assert lines(db) == ["Lockdown switched off: nothing is blocked until it is switched on again",
                         "Lockdown switched back on", "Pause my blocks started, until 12:30 (silent)",
                         "Pause my blocks ended: your blocks are back on",
                         "Quit from the tray (Exit): the tray icon and the window are closed, blocking keeps running"]
    assert kinds(db) == ["off", "on", "pause", "pause", "tray_exit"]


def test_modes(db):
    now = datetime(2026, 10, 12, 12, 0)
    saved = modes.load(db)
    modes.save(db, saved + [{"id": "custom1", "name": "Reading", "categories": [], "items": [], "groups": [], "extra": [],
                             "mute": False, "pomodoro": None, "schedule": None}])
    saved = modes.load(db)
    saved[-1] = {**saved[-1], "name": "Deep reading", "mute": True}
    modes.save(db, saved)
    modes.save(db, saved[:-1])
    modes.start(db, "focus", now, now + timedelta(minutes=50), locked=True)
    modes.stop(db, now, force=True)
    assert lines(db) == ["Mode added: Reading", "Mode changed: Deep reading: renamed from Reading; mutes alerts changed",
                         "Mode removed: Deep reading", "Mode started: Focus until 12:50 (locked)", "Mode stopped: Focus"]
    assert kinds(db).count("mode") == 2


def test_protection_lists_and_your_own_lists(db):
    cfg = protection.settings(db)
    protection.save_settings(db, {**cfg, "enabled": [k for k in cfg["enabled"] if k != "adult"] + ["gambling"],
                                  "allowed": ["good.example"], "every_hours": 12})
    protection.save_settings(db, {**protection.settings(db), "manual": [
        {"key": "mine1", "name": "Mine", "entries": [{"host": "a.example"}, {"host": "b.example"}]}]})
    protection.save_settings(db, {**protection.settings(db), "manual": [
        {"key": "mine1", "name": "Mine", "entries": [{"host": "a.example"}, {"host": "c.example"}, {"host": "d.example"}]}]})
    text = lines(db)
    assert "Protection list switched on: Gambling" in text and "Protection list switched off: Adult" in text
    assert "Protection: site allowed anyway: good.example" in text
    assert "Protection: update every (h) 24 → 12" in text
    assert "Protection: made your own list \"Mine\" (2 sites)" in text
    assert "Protection: your list \"Mine\": 2 sites added, 1 removed" in text


def test_protection_download_info_is_not_a_change(db):
    cfg = protection.settings(db)
    protection.save_settings(db, cfg)
    base = applog.count(db.conn)
    protection.save_settings(db, {**cfg, "info": {"scam": {"count": 5, "updated": "2026-10-12T10:00:00"}},
                                  "update_now": "2026-10-12"})
    assert applog.count(db.conn) == base


def test_blocked_words_are_counted_never_listed(db):
    import keywords
    cfg = keywords.settings(db)
    keywords.save(db, {**cfg, "words": ["verysecretword", "other"], "safesearch": False})
    text = " | ".join(lines(db))
    assert "SafeSearch turned off" in text and "your own words: 2 added, 0 removed" in text
    assert "verysecretword" not in text


def test_categories_limits_reset_and_emergency_kinds(db):
    db.set_setting("stats.categories", json.dumps({"custom": [{"key": "study", "name": "Study", "color": "#fff"}]}))
    db.set_setting("stats.categories", json.dumps({"custom": [{"key": "study", "name": "Study", "color": "#fff"}],
                                                   "colors": {"study": "#000"}}))
    db.set_setting("limits.reset", "04:30")
    emergency.save_tiers(db, [{"id": "a", "uses": 3, "per": "week", "minutes": 20}])
    emergency.save_tiers(db, [{"id": "a", "uses": 4, "per": "week", "minutes": 20},
                              {"id": "b", "uses": 1, "per": "month", "minutes": 60}])
    db.set_setting("emergency.enabled", "0")
    assert lines(db) == ["Category added: Study", "Category colour changed: Study",
                         "Setting changed: when limits reset 00:00 → 04:30",
                         "Emergency unlock kind added: 3 per week for 20 min",
                         "Emergency unlock kind added: 1 per month for 60 min",
                         "Emergency unlock kind changed: 3 per week for 20 min → 4 per week for 20 min",
                         "Setting changed: Emergency unlock turned off"]


def test_reminders(db):
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "22:30",
                                             "text": "private words"})
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "every_sec": 3000})
    reminders.save(db, reminders.CUSTOM_KEY, [{"id": "r1", "on": True, "text": "Drink water", "every_sec": 3600}])
    reminders.save(db, reminders.CUSTOM_KEY, [{"id": "r1", "on": False, "text": "Drink water", "every_sec": 1800}])
    reminders.save(db, reminders.CUSTOM_KEY, [])
    text = lines(db)
    assert "Reminder - bedtime: reminder turned on; bedtime 23:00 → 22:30; text changed" in text
    assert "Reminder - breaks: every (s) 2700 → 3000" in text
    assert "Reminder added: Drink water" in text
    assert "Reminder changed: Drink water: reminder turned off; every (s) 3600 → 1800" in text
    assert "Reminder removed: Drink water" in text
    assert "private words" not in " ".join(text)


# ---------------------------------------------------------------- blocked sites, apps and groups (the Draft)

def saved_edits(db, *edits):
    """Each edit on a fresh Draft, saved at once (as the window does: the edit, then the save)."""
    for edit_ in edits:
        d = Draft(db)
        edit_(d, next(iter(d.items), None))
        d.save()


def test_an_added_site_a_changed_rule_a_new_rule_and_a_removed_site_are_each_told(db):
    db.set_setting("ui.autosave", "0")
    base = applog.count(db.conn)
    saved_edits(
        db,
        lambda d, i: d.set_rules(d.add_item("YouTube", ["youtube.com"], "manual")["id"],
                                 [{"rule_type": "time_limit", "daily_limit_min": 30}]),
        lambda d, i: d.set_rule(i, {"rule_type": "time_limit", "daily_limit_min": 45}),
        lambda d, i: d.set_rule(i, {"rule_type": "switch_limit", "daily_switch_limit": 5}),
        lambda d, i: d.remove_rule(i, "switch_limit"),
        lambda d, i: d.set_disabled(i, True),
        lambda d, i: d.set_disabled(i, False),
        lambda d, i: d.remove_item(i))
    assert lines(db)[base:] == [
        "Added site YouTube (Time limit: 30m per day)",
        "Rule changed: YouTube - time limit 30m per day → 45m per day",
        "Rule added: YouTube - Opening limit: 5 opens per day",
        "Rule removed: YouTube - Opening limit: 5 opens per day",
        "Site YouTube disabled (kept, nothing is enforced)",
        "Site YouTube enabled",
        "Removed site YouTube"]
    assert set(kinds(db)[base:]) == {"change"}


def test_a_rename_and_other_edits_to_an_app(db):
    db.set_setting("ui.autosave", "0")
    base = applog.count(db.conn)

    def change(d, i):
        d.items[i]["display_name"] = "Discord chat"
        d.items[i]["target"] = "discord.exe discordptb.exe"
        d.items[i]["block_type"] = "kill"
    saved_edits(db, lambda d, i: d.add_item("Discord", ["discord.exe"], "manual", "app"), change)
    text = lines(db)[base:]
    assert text[0] == "Added app Discord (no rules of its own)"
    assert text[1] == "Renamed app Discord → Discord chat"
    assert "App Discord chat: addresses changed (added discordptb.exe)" in text
    assert "App Discord chat: how it is blocked default → kill" in text


def test_groups_their_members_rules_and_removal(db):
    db.set_setting("ui.autosave", "0")
    d = Draft(db)
    for name in ("YouTube", "Reddit"):
        d.set_rules(d.add_item(name, [name.lower() + ".com"], "manual")["id"], [{"rule_type": "permanent"}])
    d.save()
    base = applog.count(db.conn)
    ids = {i["display_name"]: i["id"] for i in Draft(db).items.values()}
    saved_edits(
        db,
        lambda d, i: d.set_group(None, "Time wasters", [{"rule_type": "time_limit", "daily_limit_min": 60}], {ids["YouTube"]: {}}),
        lambda d, i: d.set_group(next(iter(d.groups)), "Time wasters", [{"rule_type": "time_limit", "daily_limit_min": 90}],
                                 {ids["YouTube"]: {}, ids["Reddit"]: {}}),
        lambda d, i: d.set_group_disabled(next(iter(d.groups)), True),
        lambda d, i: d.remove_group(next(iter(d.groups))))
    assert lines(db)[base:] == ["Added group Time wasters (1 members, Time limit: 1h per day)",
                                "Group Time wasters: added Reddit",
                                "Rule changed: group Time wasters - time limit 1h per day → 1h30 per day",
                                "Group Time wasters disabled (kept, nothing is enforced)",
                                "Removed group Time wasters"]


def test_saving_nothing_new_writes_nothing(db):
    saved_edits(db, lambda d, i: d.add_item("YouTube", ["youtube.com"], "manual"))
    n = applog.count(db.conn)
    Draft(db).save()
    assert applog.count(db.conn) == n


def test_a_big_save_is_one_line_per_change_with_a_cap(db):
    db.set_setting("ui.autosave", "0")
    base = applog.count(db.conn)

    def many(d, _i):
        for i in range(60):
            d.add_item(f"Site {i}", [f"site{i}.example"], "manual")
    saved_edits(db, many)
    texts = lines(db)[base:]
    assert len(texts) == 41 and texts[-1] == "…and 20 more changes"


# ---------------------------------------------------------------- the challenge, emergency unlock, backup

def make_guard(db, clock):
    return guard.GuardService(lambda: db, lambda _db: clock[0], monotonic=lambda: clock[1])


def test_challenge_passed_failed_and_too_fast(db):
    antibypass.save(db, {**antibypass.settings(db), "phrase": True, "length": 30})
    clock = [datetime(2026, 10, 12, 12, 0), 0.0]
    g = make_guard(db, clock)
    done = []
    ticket = g.request(["Remove YouTube", "Loosen Reddit"], lambda: done.append(1))["data"]["ticket"]
    phrase = g.begin(ticket)["data"]["phrase"]
    assert g.submit(ticket, "wrong")["ok"] is False
    assert g.submit(ticket, phrase)["error"].startswith("Too fast")
    clock[1] += 60
    assert g.submit(ticket, phrase)["data"]["passed"] is True and done == [1]
    text = [t for t in lines(db) if t.startswith("Anti-Bypass challenge")]
    assert text == ["Anti-Bypass challenge failed: what was typed didn't match (for: Remove YouTube, Loosen Reddit)",
                    "Anti-Bypass challenge failed: typed too fast (for: Remove YouTube, Loosen Reddit)",
                    "Anti-Bypass challenge passed (for: Remove YouTube, Loosen Reddit)"]
    assert "challenge_failed" in kinds(db) and "challenge_passed" in kinds(db)


def test_the_phrase_to_type_is_never_in_the_log(db):
    antibypass.save(db, {**antibypass.settings(db), "phrase": True, "length": 30})
    clock = [datetime(2026, 10, 12, 12, 0), 0.0]
    g = make_guard(db, clock)
    ticket = g.request(["x"], lambda: None)["data"]["ticket"]
    phrase = g.begin(ticket)["data"]["phrase"]
    g.submit(ticket, phrase.replace("a", "b"))
    assert phrase not in " ".join(lines(db))


def test_an_emergency_unlock_is_told_with_what_and_how_long(db):
    now = datetime(2026, 10, 12, 12, 0)
    db.add_site("Reddit", ["reddit.com"], rules=[{"rule_type": "scheduled", "schedule": rules.make_schedule(
        "block", [([0, 1, 2, 3, 4, 5, 6], "00:00", "23:59")])}])
    item = db.list_items()[0]
    until = emergency.unlock(db, [item], now, tier=emergency.tiers(db)[0]["id"])
    text = only(db, "emergency")
    assert text.startswith("Emergency unlock used for") and f"until {until:%H:%M}" in text and "Reddit" in text


def test_a_backup_import_is_one_line_not_one_per_setting(db, tmp_path):
    db.add_site("Reddit", ["reddit.com"])
    data = backup.export(db)
    data["settings"].update({"stats.goal_hours": "5", "ui.theme": "light", "icons.download": "0"})
    backup.restore(db, data)
    assert [t for t in lines(db) if t.startswith("Backup imported")] == [
        "Backup imported: 1 sites and apps, 0 groups, 0 category choices and the settings now replace the old ones"]
    assert not [t for t in lines(db) if t.startswith("Setting changed")]
    backup.save(db, str(tmp_path / "b.json"))
    assert "A backup was saved (everything you set up, in one file)" in lines(db)


def test_a_failed_import_leaves_no_line(db):
    with pytest.raises(Exception):
        backup.restore(db, {"items": [{"id": 1}]})
    assert not [t for t in lines(db) if t.startswith("Backup imported")]


# ---------------------------------------------------------------- the life of a process

def test_start_stop_and_an_update_from_x_to_y(db):
    applog.process_started(db, "0.86.0")
    applog.process_stopped(db)
    applog.process_started(db, "0.87.0")
    applog.process_stopped(db)
    applog.process_started(db, "0.87.0")
    assert lines(db) == ["Lockdown 0.86.0 started", "Lockdown stopped", "Updated from 0.86.0 to 0.87.0",
                         "Lockdown 0.87.0 started", "Lockdown stopped", "Lockdown 0.87.0 started"]
    assert kinds(db) == ["start", "stop", "updated", "start", "stop", "start"]


def test_a_start_after_a_run_that_never_stopped_says_it_was_not_closed_normally(db):
    applog.process_started(db, "0.87.0")
    applog.process_started(db, "0.87.0", extra=applog.args_text(["--watchdog"]))        # (the first one was killed)
    text = lines(db)
    assert text[0] == "Lockdown 0.87.0 started"
    assert text[1].startswith("Lockdown was not closed normally (last started ") and "crashed" in text[1]
    assert text[2] == ("Lockdown 0.87.0 started (started again by the watchdog because it was not running and was "
                       "not closed with Exit)")
    assert kinds(db)[1] == "killed"


def test_a_cut_off_run_before_an_update_is_put_down_to_the_update(db):
    applog.process_started(db, "0.86.0")
    applog.process_started(db, "0.87.0")
    assert any("stopped for the update" in t for t in lines(db))


def test_agent_and_service_each_keep_their_own_run_state(db):
    applog.process_started(db, "1.0", source="agent")
    applog.process_started(db, "1.0", source="service", extra="last seen alive 2026-10-12 11:00")
    applog.process_stopped(db, source="agent")
    applog.process_started(db, "1.0", source="agent")
    texts = lines(db)
    assert "The Lockdown service 1.0 started (last seen alive 2026-10-12 11:00)" in texts
    assert not any("not closed normally" in t for t in texts)


def test_a_crash_is_one_short_line_with_where_it_happened(db, monkeypatch):
    monkeypatch.setattr(applog, "record", lambda kind, text, db_=None, source=None: calls.append((kind, text)) or True)
    calls = []

    def boom():
        raise ValueError("the thing broke")
    try:
        boom()
    except ValueError as e:
        text = applog.crash_text(type(e), e, e.__traceback__)
    assert text.startswith("Unexpected error: ValueError: the thing broke at test_applog.py:") and " in boom" in text
    applog.problem(text, "crash")
    assert calls[0][0] == "crash"


def test_uncaught_exceptions_in_a_thread_become_a_crash_event(tmp_path, monkeypatch):
    import paths
    import sys
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "config.db")
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)               # (put back by monkeypatch)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)      # (the program's own handler would print it)
    applog.install_crash_hooks()

    def work():
        raise RuntimeError("worker died")
    t = threading.Thread(target=work, name="lockdown-test-thread")
    t.start()
    t.join()
    con = sqlite3.connect(paths.DB_PATH)
    rows = con.execute("SELECT kind, text FROM app_events").fetchall()
    con.close()
    assert len(rows) == 1 and rows[0][0] == "crash"
    assert "RuntimeError: worker died" in rows[0][1] and "lockdown-test-thread" in rows[0][1]


def test_logged_errors_become_problems_once_per_ten_minutes(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "config.db")
    handler = applog._ErrorHandler(logging.ERROR)
    logger = logging.getLogger("lockdown.test_applog")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        for _ in range(5):
            try:
                raise OSError("disk is full")
            except OSError:
                logger.exception("Counting app time failed")
        logger.error("Another thing failed")
        logger.warning("only a warning")
    finally:
        logger.removeHandler(handler)
    con = sqlite3.connect(paths.DB_PATH)
    texts = [r[0] for r in con.execute("SELECT text FROM app_events")]
    con.close()
    assert len(texts) == 2 and texts[0].startswith("Counting app time failed (OSError: disk is full")
    assert texts[1] == "Another thing failed"


# ---------------------------------------------------------------- the service, the window, updates

def test_the_hosts_file_edited_by_something_else_is_told_but_our_own_change_is_not(tmp_path):
    h, backup_ = tmp_path / "hosts", tmp_path / "hosts.backup"
    h.write_bytes(b"127.0.0.1 localhost\r\n")
    hosts._last = None
    assert hosts.apply(["reddit.com"], h, backup_) and hosts.repaired is False       # first time: our own change
    assert hosts.apply(["reddit.com", "x.com"], h, backup_) and hosts.repaired is False     # we asked for other names
    h.write_bytes(b"127.0.0.1 localhost\r\n")                                         # something wipes our section
    assert hosts.apply(["reddit.com", "x.com"], h, backup_) and hosts.repaired is True
    assert hosts.apply(["reddit.com", "x.com"], h, backup_) is False and hosts.repaired is False


class FakeClock:
    def __init__(self):
        self.offset_now = 0.0

    def maybe_sync(self):
        return False

    def offset(self):
        return self.offset_now

    def now_ts(self):
        return 1_800_000_000.0

    zone_shift = 0


def test_the_service_tells_a_clock_change_and_a_time_zone_change(db, monkeypatch):
    import service
    e = object.__new__(service.Enforcer)
    e.db, e.clock, e.last_offset = db, FakeClock(), None
    e.update_zone = lambda: None
    e.clock.now = lambda: datetime(2026, 10, 12, 12, 0)
    e.update_clock()
    e.clock.offset_now = 3600.0
    e.update_clock()
    assert lines(db) == ["The Windows clock was changed by -3600 s - ignored, Lockdown keeps its own trusted time"]
    assert kinds(db) == ["clock_changed"]
    e2 = object.__new__(service.Enforcer)
    e2.db, e2.clock = db, FakeClock()
    monkeypatch.setattr(service, "zone_step", lambda *a: (None, 0, "The Windows time zone changed - counts after 24 hours"))
    monkeypatch.setattr(service, "zone_name", lambda: "x")
    monkeypatch.setattr(service, "utc_offset", lambda ts: 0)
    e2.update_zone()
    assert lines(db)[-1] == "The Windows time zone changed - counts after 24 hours"


def test_a_protection_list_that_cannot_be_downloaded_is_told_once_until_it_works_again(db, tmp_path, monkeypatch):
    cfg = protection.settings(db)
    protection.save_settings(db, {**cfg, "enabled": ["scam"]})
    before = applog.count(db.conn)

    def fail(*a, **k):
        raise OSError("no internet")
    monkeypatch.setattr(protection, "download", fail)
    now = datetime(2026, 10, 12, 12, 0)
    protection.update_due_lists(db, now, tmp_path)
    protection.update_due_lists(db, now + timedelta(hours=2), tmp_path)
    assert [t for t in lines(db) if "could not be downloaded" in t] == [
        "Protection list Scam could not be downloaded (OSError: no internet); the old copy stays, Lockdown tries again "
        "in an hour"]
    assert "list_failed" in kinds(db)


def test_update_check_failure_a_found_version_and_a_failed_download(db, tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "other.db")
    monkeypatch.setattr(updates, "REPO", "someone/lockdown")

    def fetch(url):
        raise OSError("timed out")
    assert updates.latest_release(fetch) is None
    con = sqlite3.connect(paths.DB_PATH)
    assert con.execute("SELECT kind, text FROM app_events").fetchall() == [
        ("update_check_failed", "Update check got no answer (OSError: timed out)")]
    con.close()
    updates.remember(db, {"version": "9.9.9", "newer": True, "url": "u", "asset": "a", "size": 1})
    updates.remember(db, {"version": "9.9.9", "newer": True, "url": "u", "asset": "a", "size": 1})
    assert [t for t in lines(db) if "newer" in t] == [f"A newer version was found: 9.9.9 (this is {updates.VERSION})"]


def test_window_trouble_from_the_start_up_notes_is_told(tmp_path, monkeypatch):
    import paths
    from resident import diag
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "config.db")
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")
    monkeypatch.setattr(diag, "path", lambda: tmp_path / "newui.log")
    diag.note("start 0.87.0 frozen=True")                                                   # not important: not told
    diag.note("the new window could not be used: Microsoft Edge WebView2 was not found", important=True)
    diag.note("the window is tried once more (--tray --retried)", important=True)
    con = sqlite3.connect(paths.DB_PATH)
    rows = con.execute("SELECT kind, text FROM app_events ORDER BY id").fetchall()
    con.close()
    assert [k for k, _t in rows] == ["window_failed", "window_retry"]
    assert "WebView2 was not found" in rows[0][1]


def test_an_api_error_in_the_window_is_a_problem_line(db, tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "w.db")
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")
    core._last_error[:] = ["", 0.0]
    core.log_error("Api call x failed: Traceback (most recent call last):\n  File \"a.py\", line 1\nValueError: nope")
    core.log_error("update flow: RuntimeError: no answer from GitHub (timed out)")      # (the update check says that itself)
    con = sqlite3.connect(paths.DB_PATH)
    rows = con.execute("SELECT kind, text FROM app_events").fetchall()
    con.close()
    assert rows == [("problem", "Window error: Api call x failed: Traceback (most recent call last): ... ValueError: nope")]


def test_the_installer_tells_install_and_uninstall_attempts_but_never_creates_the_data_folder_first(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    import paths
    spec = importlib.util.spec_from_file_location("lockdown_setup_applog", Path(__file__).resolve().parents[1] / "installer" / "setup.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "data" / "config.db")
    monkeypatch.setattr(setup, "install", lambda log, at=None: log("installed"))
    monkeypatch.setattr(setup, "installed_version", lambda: None)
    monkeypatch.setattr(setup, "challenge_passed", lambda: False)
    setup.work("install", lambda *a: None)
    assert not (tmp_path / "data").exists()                       # fresh PC: nothing made before the permissions are set
    (tmp_path / "data").mkdir()
    Database(paths.DB_PATH).close()                               # (the service made the database meanwhile)
    setup.work("uninstall", lambda *a: None)
    setup.app_log("install", "late")
    con = sqlite3.connect(paths.DB_PATH)
    rows = con.execute("SELECT source, kind, text FROM app_events ORDER BY id").fetchall()
    con.close()
    assert ("installer", "uninstall", "Uninstall started (from Apps & features)") in rows
    assert ("installer", "uninstall", "Uninstall refused: the Anti-Bypass challenge was not passed") in rows
    assert ("installer", "install", "late") in rows


# ---------------------------------------------------------------- no visited site, ever

def test_nothing_a_user_visits_or_opens_lands_in_the_log(db, tmp_path, monkeypatch):
    """Every way the program records a visit or the use of an app leaves the App log without a single line."""
    import paths
    import service
    monkeypatch.setattr(paths, "DB_PATH", db.path)
    now = datetime(2026, 10, 12, 12, 0)
    item = db.add_site("YouTube", ["youtube.com"])
    base = applog.count(db.conn)
    db.add_block_event("secret-visit.example", item, "YouTube", "permanent", None)
    db.add_activity("2026-10-12 12:00", "chrome.exe", "visited-site.example", 60, 50)
    db.add_switch(now, "chrome.exe", "another-visit.example")
    db.add_usage([("item:1", "day:2026-10-12")], 30, now.date())
    db.add_tracked(usage=[("item:1", "day:2026-10-12", "2026-10-12", 5)], activity=[
        ("2026-10-12 12:01", "chrome.exe", "tracked-site.example", 60, 40)], switches=[
        (now, "chrome.exe", "tracked-switch.example")])
    db.add_network([{"minute": "2026-10-12 12:00", "exe": "chrome.exe", "ip": "1.2.3.4", "port": 443,
                     "domain": "net-host.example", "count": 1, "windows": 0, "local": 0}])
    db.add_unlock([item], ["YouTube"], now, now + timedelta(minutes=5))
    e = object.__new__(service.Enforcer)                       # a blocked visit, as the service sees it
    e.visit_lock, e.last_visit, e.visit_db, e.db = threading.Lock(), {}, db, db
    e.clock = type("C", (), {"now": lambda self: now})()
    e.blocks = {"listener-visit.example": {"item": {"id": item, "display_name": "YouTube"}, "reason": "permanent", "until": None}}
    e.on_visit("listener-visit.example")
    assert db.conn.execute("SELECT COUNT(*) FROM block_events").fetchone()[0] == 2        # (the visits are where they were)
    assert applog.count(db.conn) == base
    joined = " ".join(lines(db))
    for word in ("secret-visit", "visited-site", "another-visit", "tracked-", "net-host", "chrome", "listener-visit"):
        assert word not in joined


def test_no_code_that_sees_a_visit_writes_to_the_log():
    """The files that watch what you open or visit never mention the App log."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src"
    watchers = ["monitor/usage.py", "monitor/win.py", "monitor/word_guard.py", "blocker/listener.py", "blocker/netlog.py",
                "blocker/connections.py", "blocker/dnsfilter.py", "stats.py", "digest.py", "resident/snapshot.py"]
    for name in watchers:
        path = src / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            assert "applog" not in text, f"{name} must never write what you visit or open to the App log"


def test_the_kinds_the_page_filters_by_are_all_real_kinds():
    for chip, members in applog.CHIPS.items():
        assert members and all(k in applog.KINDS for k in members), chip
    assert applog.CHIP_NAMES == ("all", "changes", "updates", "problems", "antibypass")


# ---------------------------------------------------------------- the report (Copy and Save log...)

def test_the_report_has_version_windows_events_and_the_ends_of_both_logs(db, tmp_path, monkeypatch):
    import paths
    from resident import diag
    from version import VERSION
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")
    monkeypatch.setattr(diag, "path", lambda: tmp_path / "newui.log")
    (tmp_path / "lockdown.log").write_text("\n".join(f"2026-10-12 12:00:{i:02d},000 INFO line {i}" for i in range(300))
                                           + "\n2026-10-12 12:01:00,000 INFO Blocked app secretgame.exe was started - closed\n"
                                           "2026-10-12 12:01:01,000 INFO Closed 2 open connection(s) to blocked sites: 1.2.3.4:443\n",
                                           encoding="utf-8")
    (tmp_path / "newui.log").write_text("window note\n", encoding="utf-8")
    applog.record("change", "Rule changed: YouTube", db)
    text = applog.report(db.conn, when=datetime(2026, 10, 12, 18, 30))
    assert text.startswith("Lockdown app log\nSaved: 2026-10-12 18:30:00\n")
    assert f"Lockdown version: {VERSION}" in text and "Windows: " in text
    assert "[agent]  Rule changed: YouTube" in text
    assert "== lockdown.log (last 200 lines) ==" in text and "line 299" in text and "line 90" not in text
    assert "== lockdown-newui.log (last 200 lines) ==" in text and "window note" in text
    assert "secretgame" not in text and "1.2.3.4" not in text and "2 lines about closed apps and connections left out" in text


def test_the_report_carries_the_newest_thousand_events_in_order(db):
    db.conn.executemany("INSERT INTO app_events (ts, source, kind, text) VALUES (?, 'agent', 'change', ?)",
                        [("2026-10-12 10:00:00", f"event {i}") for i in range(1500)])
    db.conn.commit()
    text = applog.report(db.conn, with_logs=False)
    assert "event 1499" in text and "event 500" in text and "event 499" not in text
    assert text.index("event 1499") < text.index("event 500")
    assert applog.file_name(datetime(2026, 10, 12)) == "lockdown-log-2026-10-12.txt"


# ---------------------------------------------------------------- the Api

@pytest.fixture
def ctx(tmp_path):
    c = make_context(tmp_path / "config.db")
    yield c
    c.db.close()


def test_the_api_methods_are_classified(ctx):
    kinds_ = dict(core.api_methods())
    assert kinds_["applog_get"] == "read" and kinds_["applog_text"] == "read" and kinds_["applog_save"] == "ui"


def test_applog_get_pages_filters_and_refuses_bad_input(ctx):
    for i in range(40):
        applog.record("change" if i % 2 else "start", f"event {i}", ctx.db)
    first = applog_api.applog_get(ctx, "all", "", None, 15)
    assert [r["text"] for r in first["rows"]][:2] == ["event 39", "event 38"] and first["more"] is True and first["total"] == 40
    older = applog_api.applog_get(ctx, "all", "", first["rows"][-1]["id"], 100)
    assert len(older["rows"]) == 25 and older["more"] is False and older["total"] is None
    assert [r["text"] for r in applog_api.applog_get(ctx, "changes", "", None, 100)["rows"]][:2] == ["event 39", "event 37"]
    assert [r["text"] for r in applog_api.applog_get(ctx, "all", "event 3", None, 100)["rows"]][0] == "event 39"
    for bad in [("nope",), ("all", 5), ("all", "x" * 101), ("all", "", "a"), ("all", "", None, 0), ("all", "", None, 501),
                ("all", "", 0)]:
        with pytest.raises(core.ApiError):
            applog_api.applog_get(ctx, *bad)


def test_the_page_can_reach_the_api_only_through_the_bridge(ctx):
    api = core.build_api(ctx)
    reply = api.applog_get("problems", "", None, 10)
    assert reply["ok"] and reply["data"]["rows"] == [] and reply["data"]["chips"] == list(applog.CHIP_NAMES)
    assert api.applog_get("bogus")["ok"] is False


def test_copy_text_follows_the_filter(ctx):
    applog.record("change", "Rule changed: YouTube", ctx.db)
    applog.record("crash", "Unexpected error", ctx.db)
    text = applog_api.applog_text(ctx, "problems", "")["text"]
    assert "Unexpected error" in text and "Rule changed" not in text and "Filter: problems" in text
    assert "== lockdown.log" not in text


def test_save_log_writes_the_file_the_owner_picked_and_nothing_when_cancelled(ctx, tmp_path, monkeypatch):
    applog.record("change", "Rule changed: YouTube", ctx.db)
    asked = []
    monkeypatch.setattr(applog_api, "_ask_save_path", lambda window, name: asked.append(name) or None)
    assert applog_api.applog_save(ctx) == {"cancelled": True}
    target = tmp_path / "out" / "share"
    target.parent.mkdir()
    monkeypatch.setattr(applog_api, "_ask_save_path", lambda window, name: asked.append(name) or str(target))
    reply = applog_api.applog_save(ctx)
    assert reply["path"] == str(target) + ".txt" and reply["events"] == 1
    assert asked[0] == f"lockdown-log-{ctx.now():%Y-%m-%d}.txt"
    saved = (tmp_path / "out" / "share.txt").read_bytes().decode("utf-8")
    assert "Rule changed: YouTube" in saved and "Lockdown version:" in saved and "\r\n" in saved
    monkeypatch.setattr(applog_api, "_ask_save_path", lambda window, name: str(tmp_path / "no" / "such" / "x.txt"))
    with pytest.raises(core.ApiError, match="Couldn't save"):
        applog_api.applog_save(ctx)


def test_without_a_window_there_is_no_save_dialog(ctx):
    with pytest.raises(core.ApiError, match="Save dialog"):
        applog_api.applog_save(ctx)


def fill(db, n):
    kinds_ = ["change", "start", "updated", "crash", "challenge_passed", "problem", "stop"]
    db.conn.executemany("INSERT INTO app_events (ts, source, kind, text) VALUES (?, ?, ?, ?)",
                        [(f"2026-{1 + i % 9:02d}-{1 + i % 27:02d} {i % 24:02d}:{i % 60:02d}:00", "service" if i % 5 == 0 else "agent",
                          kinds_[i % len(kinds_)], f"Rule changed: item {i} daily limit {i % 90} min → {i % 90 + 5} min")
                         for i in range(n)])
    db.conn.commit()


def test_reading_20000_events_is_within_the_api_budget(ctx):
    fill(ctx.db, 20_000)
    assert applog.count(ctx.db.conn) == 20_000
    calls = [(), ("changes",), ("problems", "item 1"), ("all", "", 15000), ("antibypass",)]
    times = []
    for _round in range(12):
        for args in calls:
            t = time.perf_counter()
            reply = applog_api.applog_get(ctx, *args)
            times.append((time.perf_counter() - t) * 1000)
            assert reply["rows"]
    times.sort()
    p95 = times[int(len(times) * 0.95) - 1]
    assert p95 < 50, f"p95 {p95:.1f} ms over 20 000 rows (budget: target 50 ms, limit 100 ms)"
    assert statistics.median(times) < 25
    t = time.perf_counter()
    text = applog_api.applog_text(ctx)["text"]
    assert (time.perf_counter() - t) * 1000 < 200 and text.count("\n") > applog.REPORT_EVENTS


def test_writing_an_event_is_cheap(db):
    t = time.perf_counter()
    for i in range(300):
        applog.record("change", f"event {i}", db)
    per_write_ms = (time.perf_counter() - t) * 1000 / 300
    assert per_write_ms < 15, f"{per_write_ms:.1f} ms per event"


def test_moving_an_app_to_a_category_is_told(ctx):
    from webapi import categories
    reply = categories.category_assign(ctx, "app", "chrome.exe", "productive")
    assert reply["ok"] is True if isinstance(reply, dict) and "ok" in reply else True
    assert "Category: chrome.exe moved to Productive" in log(ctx.db)
    categories.category_add_and_assign(ctx, "site", "news.example", "Reading", "#112233")
    assert "Category: news.example moved to the new category Reading" in log(ctx.db)


def test_the_installer_killing_the_app_is_put_down_to_the_update_even_when_the_service_started_first(db):
    applog.process_started(db, "0.86.0", source="agent")
    applog.process_started(db, "0.86.0", source="service")
    applog.process_stopped(db, source="service")            # (the installer stops the service properly, kills the app)
    applog.process_started(db, "0.87.0", source="service")
    applog.process_started(db, "0.87.0", source="agent")
    texts = lines(db)
    assert texts.count("Updated from 0.86.0 to 0.87.0") == 1
    killed = [t for t in texts if "not closed normally" in t]
    assert len(killed) == 1 and killed[0].startswith("Lockdown was not closed normally") and "stopped for the update" in killed[0]


def test_a_members_own_limit_inside_a_group_is_told(db):
    db.set_setting("ui.autosave", "0")
    d = Draft(db)
    iid = d.add_item("YouTube", ["youtube.com"], "manual")["id"]
    d.set_rules(iid, [{"rule_type": "permanent"}])
    d.save()
    base = applog.count(db.conn)
    iid = next(iter(Draft(db).items))
    saved_edits(db, lambda d, i: d.set_group(None, "Wasters", [], {iid: {}}),
                lambda d, i: d.set_group(next(iter(d.groups)), "Wasters", [], {iid: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 20}}}))
    assert lines(db)[base:] == ["Added group Wasters (1 members)",
                                "Rule added: YouTube in group Wasters - Time limit: 20m per day"]
