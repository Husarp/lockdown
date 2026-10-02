"""0.84.1: time on blocked apps and sites was under-counted, so limits and "N minutes allowed during blocked hours"
never ran out - 2 hours of play outside the allowed hours were counted as about 21 minutes and nothing was closed;
YouTube stayed open all night inside a group's blocked hours.

Causes pinned here:
- every tick added a fixed 2 s, however long had really passed since the last one (a late tick lost the rest);
- nothing at all was counted while the tray app wasn't counting (not running, frozen, tracker stalled) - the
  service now counts running blocked apps itself then (count_unwatched_apps);
- a site stopped counting after 15 minutes without keyboard / mouse input (a video you just watch);
- a foreground window whose process can't be opened had no name, so it counted for nothing.
Plus the fixes found next to them: www. sites and the media hosts, a Neutral subdomain un-blocking YouTube in Work
mode, the tab check that never restarted, open connections that were never cut, a failed close that left no trace.
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta

import pytest

import modes
import rules
import service
from blocker import apps, connections, firewall, netlog
from db import Database
from monitor import usage as usage_mod
from monitor.usage import COUNTED_KEY, MAX_GAP_SEC, TICK_SEC, UsageTracker

EVENING = datetime(2026, 10, 2, 19, 0)           # a Friday, after the allowed hours (16-18)
ALLOW_16_18 = rules.make_schedule("allow", [(list(range(7)), "16:00", "18:00")])


class Clock:
    """The trusted time the tray reads (now_from_db), moved by hand."""

    def __init__(self, monkeypatch, start: datetime):
        self.now = start
        monkeypatch.setattr(usage_mod, "now_from_db", lambda db: self.now)

    def __iadd__(self, seconds: float):
        self.now += timedelta(seconds=seconds)
        return self


def used(db, owner: str, bucket: str, now: datetime) -> int:
    return db.usage_lookup(now)(owner, bucket)


def day(now: datetime) -> str:
    return rules.time_bucket("day", now)


def reasons(db, now, item_id):
    return [b["reason"] for b in db.blocks(now) if b["item"]["id"] == item_id]


def play(db, tracker, clock, seconds: float, every: float, sense, running=lambda: {"game.exe"}):
    """Tick the real tracker every `every` seconds of trusted time for `seconds`."""
    for _ in range(round(seconds / every)):
        clock += every
        tracker.tick(db, sense, running_exes=running)


# ---------- the tracker counts the time that really passed ----------

def test_late_ticks_count_the_time_that_really_passed(tmp_path, monkeypatch):
    """The tray app's window froze, the database was locked, the PC was busy: ticks came every 10-11 s instead of
    every 2. Each still added 2 s - about a fifth of the real time, the "21 minutes for 2 hours" Adam saw."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    clock, tracker = Clock(monkeypatch, EVENING), UsageTracker()
    play(db, tracker, clock, 2 * 3600, 11.4, lambda: ("game.exe", None, 0))
    counted = used(db, f"item:{game}", day(clock.now), clock.now)
    assert abs(counted - 2 * 3600) <= 12, counted          # not 21 minutes: two hours
    assert reasons(db, clock.now, game) == ["limit"]


def test_a_fraction_of_a_second_per_tick_is_not_lost(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    clock, tracker = Clock(monkeypatch, EVENING), UsageTracker()
    play(db, tracker, clock, 3600, 2.1, lambda: ("game.exe", None, 0))     # a tick takes a moment too
    assert abs(used(db, f"item:{game}", day(clock.now), clock.now) - 3600) <= 3


def test_a_long_gap_is_the_pc_asleep_not_play(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    clock, tracker = Clock(monkeypatch, EVENING), UsageTracker()
    tracker.tick(db, lambda: ("game.exe", None, 0))
    clock += 8 * 3600                                     # slept all night with the game in front
    tracker.tick(db, lambda: ("game.exe", None, 0))
    assert used(db, f"item:{game}", day(clock.now), clock.now) == MAX_GAP_SEC


def test_ticks_as_fast_as_before_still_count_a_tick_each(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    clock, tracker = Clock(monkeypatch, EVENING), UsageTracker()
    for _ in range(5):
        tracker.tick(db, lambda: ("game.exe", None, 0))   # (no time passes at all between them)
    assert used(db, f"item:{game}", day(clock.now), clock.now) == 5 * TICK_SEC


# ---------- input or not ----------

def test_a_game_counts_without_keyboard_or_mouse(tmp_path, monkeypatch):
    """A controller, a cutscene: no input Windows sees. Two hours in front = two hours counted."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    clock, tracker = Clock(monkeypatch, EVENING), UsageTracker()
    play(db, tracker, clock, 2 * 3600, 2, lambda: ("game.exe", None, 3 * 3600))
    assert used(db, f"item:{game}", day(clock.now), clock.now) == 2 * 3600


def _youtube_group(db):
    """Adam's setup: YouTube in a group blocked 21:00-05:00 with 15 minutes allowed in those hours (one pot for
    the group) and 2 h a day for the group; YouTube itself 1 h a day."""
    yt = db.add_item("YouTube", ["youtube.com", "googlevideo.com"], "site", rules=[])
    sched = rules.make_schedule("block", [(list(range(7)), "21:00", "05:00")])
    gid = db.add_group("Evening", [{"rule_type": "scheduled", "schedule": sched, "allowance_min": 15},
                                   {"rule_type": "time_limit", "daily_limit_min": 120}],
                       {yt: {"time_limit": {"daily_limit_min": 60}}})
    return yt, gid


def test_youtube_watched_without_touching_anything_uses_up_the_allowance(tmp_path, monkeypatch):
    """Inside the blocked hours YouTube is allowed until the group's 15 minutes are spent. A video watched
    without input stopped counting after 15 minutes, so they were never spent and YouTube stayed open."""
    db = Database(tmp_path / "t.db")
    yt, _gid = _youtube_group(db)
    clock, tracker = Clock(monkeypatch, datetime(2026, 10, 2, 22, 25)), UsageTracker()
    assert reasons(db, clock.now, yt) == []                       # the allowance is the way in
    play(db, tracker, clock, 2 * 3600, 2, lambda: ("chrome.exe", "https://www.youtube.com/watch?v=x", 40 * 60),
         running=lambda: {"chrome.exe"})
    assert clock.now == datetime(2026, 10, 3, 0, 25)
    assert reasons(db, clock.now, yt) == ["schedule"]             # spent long ago: blocked until 05:00
    stretch = rules.allowance_bucket({"rule_key": "g1scheduled"}, datetime(2026, 10, 3, 5, 0))
    assert used(db, f"group:{_gid}", stretch, clock.now) == 2 * 3600


def test_a_locked_pc_counts_nothing(tmp_path, monkeypatch):
    """Locked: what stops the count (not a lack of input). sense_desktop reports nothing in front."""
    from monitor import win
    monkeypatch.setattr(win, "foreground_process", lambda: (1234, "chrome.exe", r"C:\Chrome\chrome.exe"))
    monkeypatch.setattr(win, "idle_seconds", lambda: 3600.0)
    monkeypatch.setattr(win, "session_locked", lambda: True)
    assert usage_mod.sense_desktop() == (None, None, 3600.0, None, None)
    db = Database(tmp_path / "t.db")
    yt, _gid = _youtube_group(db)
    clock, tracker = Clock(monkeypatch, EVENING), UsageTracker()
    play(db, tracker, clock, 600, 2, usage_mod.sense_desktop, running=lambda: {"chrome.exe"})
    assert db.usage_lookup(clock.now).data == {}


def test_unlocked_the_window_in_front_is_reported(monkeypatch):
    from monitor import browser_url, win
    monkeypatch.setattr(win, "foreground_process", lambda: (1234, "game.exe", r"D:\Games\Game\game.exe"))
    monkeypatch.setattr(win, "idle_seconds", lambda: 3600.0)
    monkeypatch.setattr(win, "session_locked", lambda: False)
    monkeypatch.setattr(win, "window_pid", lambda hwnd: 4242)
    assert usage_mod.sense_desktop() == ("game.exe", None, 3600.0, r"D:\Games\Game\game.exe", 4242)
    monkeypatch.setattr(win, "foreground_process", lambda: (1234, "chrome.exe", r"C:\Chrome\chrome.exe"))
    monkeypatch.setattr(browser_url, "browser_url", lambda hwnd: "youtube.com/watch")
    assert usage_mod.sense_desktop()[:2] == ("chrome.exe", "youtube.com/watch")


def test_a_window_whose_process_cant_be_opened_still_has_a_name(monkeypatch):
    """Anti-cheat can refuse even "query limited information": the window then had no exe at all and its time
    went nowhere. The process list needs no access to the process."""
    from monitor import win
    monkeypatch.setattr(win._user32, "GetForegroundWindow", lambda: 77, raising=False)
    monkeypatch.setattr(win, "window_pid", lambda hwnd: 4242)
    monkeypatch.setattr(win, "pid_path", lambda pid: "")
    monkeypatch.setattr(apps, "list_processes", lambda: [(10, "explorer.exe"), (4242, "game-win64-shipping.exe")])
    assert win.foreground_process() == (77, "game-win64-shipping.exe", "")
    assert win.foreground() == (77, "game-win64-shipping.exe")


def test_the_game_window_from_the_games_folder_counts_for_the_game(tmp_path, monkeypatch):
    """An Unreal game: you pick HollowGame.exe, the window in front is HollowGame-Win64-Shipping.exe."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Hollow Game", ["hollowgame.exe"], "app",
                       rules=[{"rule_type": "scheduled", "schedule": ALLOW_16_18, "allowance_min": 15}],
                       app_path=r"D:\SteamLibrary\steamapps\common\Hollow Game\HollowGame.exe")
    clock, tracker = Clock(monkeypatch, EVENING), UsageTracker()
    shipping = r"D:\SteamLibrary\steamapps\common\Hollow Game\HollowGame\Binaries\Win64\HollowGame-Win64-Shipping.exe"
    play(db, tracker, clock, 16 * 60, 2, lambda: ("hollowgame-win64-shipping.exe", None, 0, shipping),
         running=lambda: {"hollowgame-win64-shipping.exe"})
    assert reasons(db, clock.now, game) == ["schedule"]           # 15 minutes allowed, 16 played


def test_a_target_typed_as_a_full_path_counts(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", [r"C:\Games\Game\Game.exe"], "app")
    clock, tracker = Clock(monkeypatch, EVENING), UsageTracker()
    play(db, tracker, clock, 60, 2, lambda: ("game.exe", None, 0))
    assert used(db, f"item:{game}", day(clock.now), clock.now) == 60


# ---------- the service counts when the tray app doesn't ----------

def _bare_enforcer(db):
    e = object.__new__(service.Enforcer)
    e.db = db
    return e


def _procs(monkeypatch, procs, paths=None):
    monkeypatch.setattr(apps, "list_processes_full", lambda: procs)
    monkeypatch.setattr(apps, "process_path", lambda pid: (paths or {}).get(pid))


def test_with_no_tray_app_the_service_counts_a_running_game(tmp_path, monkeypatch):
    """The tray app not running (exited, crashed, frozen): the allowance never ran out and the game was never
    closed. The service sees the processes - it counts a running blocked app itself."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app",
                       rules=[{"rule_type": "scheduled", "schedule": ALLOW_16_18, "allowance_min": 15}])
    _procs(monkeypatch, [(1, 0, "explorer.exe"), (5, 1, "game.exe")])
    e, now = _bare_enforcer(db), EVENING
    assert reasons(db, now, game) == []
    for _ in range(int(16 * 60 / service.INTERVAL_SEC)):
        now += timedelta(seconds=service.INTERVAL_SEC)
        e.count_unwatched_apps(now)
    assert reasons(db, now, game) == ["schedule"]
    stretch = rules.allowance_bucket({"rule_key": f"i{game}scheduled"}, datetime(2026, 10, 3, 16, 0))
    assert abs(used(db, f"item:{game}", stretch, now) - 16 * 60) <= service.TRACKER_STALE_SEC + 2


def test_the_service_counts_a_game_from_its_steam_folder(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Hollow Game", ["hollowgame.exe"], "app",
                       app_path=r"D:\SteamLibrary\steamapps\common\Hollow Game\HollowGame.exe")
    _procs(monkeypatch, [(5, 1, "hollowgame-win64-shipping.exe"), (6, 1, "notepad.exe")],
           {5: r"D:\SteamLibrary\steamapps\common\Hollow Game\HollowGame\Binaries\Win64\HollowGame-Win64-Shipping.exe",
            6: r"C:\Windows\notepad.exe"})
    e, now = _bare_enforcer(db), EVENING
    for _ in range(60):
        now += timedelta(seconds=2)
        e.count_unwatched_apps(now)
    assert used(db, f"item:{game}", day(now), now) >= 110


def test_while_the_tray_counts_the_service_does_not(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    _procs(monkeypatch, [(5, 1, "game.exe")])
    clock, tracker, e = Clock(monkeypatch, EVENING), UsageTracker(), _bare_enforcer(db)
    for _ in range(300):
        clock += 2
        tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
        e.count_unwatched_apps(clock.now)
    assert used(db, f"item:{game}", day(clock.now), clock.now) == 600     # counted once, not twice


def test_a_tray_that_stalls_is_covered_by_the_service_and_nothing_is_counted_twice(tmp_path, monkeypatch, caplog):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    _procs(monkeypatch, [(5, 1, "game.exe")])
    clock, tracker, e = Clock(monkeypatch, EVENING), UsageTracker(), _bare_enforcer(db)
    caplog.set_level(logging.INFO, logger="lockdown.service")
    start = clock.now
    for step in range(900):                           # 30 minutes; the tray frozen from minute 10 to 20
        clock += 2
        if not 300 <= step < 600:
            tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
        e.count_unwatched_apps(clock.now)
    counted = used(db, f"item:{game}", day(clock.now), clock.now)
    assert abs(counted - (clock.now - start).total_seconds()) <= 4, counted
    text = caplog.text
    assert "isn't counting usage" in text and "counting usage again" in text


def test_counting_never_stops_the_blocking(tmp_path, monkeypatch, caplog):
    db = Database(tmp_path / "t.db")
    e = _bare_enforcer(db)
    monkeypatch.setattr(e, "count_unwatched_apps", lambda now: 1 / 0)
    monkeypatch.setattr(e, "update_clock", lambda: EVENING)
    reached = []
    monkeypatch.setattr(db, "delete_expired_temporary", lambda now: reached.append(now) or 0)
    monkeypatch.setattr(service.antibypass, "is_off", lambda db: (_ for _ in ()).throw(StopIteration))
    with pytest.raises(StopIteration):
        e.enforce_once()
    assert reached == [EVENING] and "Counting app time failed" in caplog.text


# ---------- a close that Windows refuses is logged ----------

def test_a_close_that_fails_is_logged_once(monkeypatch, caplog):
    e = object.__new__(service.Enforcer)
    e.visit_lock, e.last_visit, e.app_first_seen, e.block_since = threading.Lock(), {}, {}, {}
    e.helpers, e.path_cache = {}, {}
    e.record_event = lambda key, block: None
    e.app_blocks = {"game.exe": {"item": {"id": 1, "display_name": "Game", "target": "game.exe",
                                          "block_type": None, "app_path": None}, "reason": "schedule", "until": None}}
    clock = [1000.0]
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    monkeypatch.setattr(apps, "list_processes_full", lambda: [(5, 1, "game.exe")])
    monkeypatch.setattr(apps, "terminate", lambda pid: False)       # anti-cheat says no
    monkeypatch.setattr(apps, "start_time", lambda pid: 2000.0)
    caplog.set_level(logging.INFO, logger="lockdown.service")
    for _ in range(100):
        clock[0] += 0.25
        e.enforce_apps()
    refused = [r for r in caplog.records if "Couldn't close" in r.getMessage()]
    assert len(refused) == 1 and "game.exe" in refused[0].getMessage()


# ---------- the item list survives a missing change counter ----------

def test_items_load_even_without_the_change_counter(tmp_path):
    """A damaged or hand-edited database without the 0.84.0 counter table made every enforcement pass fail."""
    db = Database(tmp_path / "t.db")
    db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "permanent"}])
    raw = sqlite3.connect(tmp_path / "t.db")
    for (name,) in raw.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
        raw.execute(f"DROP TRIGGER {name}")
    raw.execute("DROP TABLE change_counter")
    raw.commit()
    assert [b["item"]["target"] for b in db.blocks(EVENING)] == ["game.exe"]
    raw.execute("INSERT INTO blocked_items (display_name, target, item_type) VALUES ('Other', 'other.exe', 'app')")
    raw.execute("INSERT INTO block_rules (item_id, rule_type) VALUES (last_insert_rowid(), 'permanent')")
    raw.commit()
    assert sorted(b["item"]["target"] for b in db.blocks(EVENING)) == ["game.exe", "other.exe"]


# ---------- YouTube, the rest ----------

def test_a_www_site_gets_the_video_hosts(tmp_path):
    from importer.popular import MEDIA_HOSTS_KEY, add_media_hosts, hosts_of
    assert hosts_of("www.youtube.com") == hosts_of("youtube.com") and "googlevideo.com" in hosts_of("www.youtube.com")
    db = Database(tmp_path / "t.db")
    db.set_setting(MEDIA_HOSTS_KEY, "1")              # the first pass (before 0.84.1) has run and missed it
    db.add_item("YouTube", ["www.youtube.com"], "site", rules=[{"rule_type": "permanent"}])
    db.add_item("Twitch", ["twitch.tv"], "site", rules=[{"rule_type": "permanent"}])   # trimmed by hand: stays
    assert add_media_hosts(db) == ["YouTube"]
    targets = {i["display_name"]: i["target"].split() for i in db.list_items()}
    assert "googlevideo.com" in targets["YouTube"] and targets["Twitch"] == ["twitch.tv"]
    assert add_media_hosts(db) == []


def test_a_neutral_subdomain_doesnt_take_youtube_out_of_work_mode(tmp_path):
    db = Database(tmp_path / "t.db")
    yt = db.add_item("YouTube", ["youtube.com", "googlevideo.com"], "site", rules=[])
    db.set_category("site", "music.youtube.com", "neutral")
    modes.start(db, "work", EVENING - timedelta(minutes=1), None)
    assert reasons(db, EVENING, yt) == ["mode"]
    db.set_category("site", "googlevideo.com", "neutral")   # one host Neutral, the page itself not chosen
    assert reasons(db, EVENING, yt) == ["mode"]
    db.set_category("site", "youtube.com", "distracting")
    assert reasons(db, EVENING, yt) == ["mode"]              # Distracting wins over Neutral
    db.set_category("site", "youtube.com", "productive")     # what you chose for the item's own hosts holds
    db.set_category("site", "googlevideo.com", "productive")
    assert reasons(db, EVENING, yt) == []


def test_any_blocked_site_in_front_is_sent_back(tmp_path, monkeypatch):
    """A tab already open when the block began keeps playing (IPv6 / QUIC connections can't be cut from outside):
    the tab check now acts on every blocked site in front, not only on those set to close / go back."""
    from monitor import word_guard
    db = Database(tmp_path / "t.db")
    db.add_item("YouTube", ["youtube.com"], "site", rules=[{"rule_type": "permanent"}])          # dns only
    db.add_item("Reddit", ["reddit.com"], "site", rules=[{"rule_type": "permanent"}])
    db.update_item(db.list_items()[0]["id"], "Reddit", ["reddit.com"], None, [{"rule_type": "permanent"}],
                   block_type="dns,close")
    monkeypatch.setattr(word_guard, "now_from_db", lambda db: EVENING)
    sites = word_guard.blocked_sites(db)
    assert sites == {"youtube.com": "back", "reddit.com": "close"}
    assert word_guard.site_action("https://www.youtube.com/watch?v=1", sites) == "back"


def test_the_tab_check_starts_again_after_failing_to_start(monkeypatch):
    from monitor import word_guard
    guard = word_guard.WordGuard(lambda *a: None)
    calls = []

    def check():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("database is locked")
        guard.stop_event.set()
    monkeypatch.setattr(guard, "_check", check)
    monkeypatch.setattr(word_guard, "RESTART_SEC", 0)
    guard.run()
    assert len(calls) == 2


def test_open_connections_are_taken_from_the_cache_before_it_is_flushed(monkeypatch):
    """The video stream's name (rr1---sn-....googlevideo.com) can't be resolved in advance; this PC's own DNS
    cache knows it - until the hosts rewrite flushes it."""
    e = object.__new__(service.Enforcer)
    e.closing = {}
    monkeypatch.setattr(netlog, "dns_names", lambda: {"1.2.3.4": "rr1---sn-ab.googlevideo.com",
                                                      "5.6.7.8": "www.youtube.com", "9.9.9.9": "example.com",
                                                      "127.0.0.1": "youtube.com"})
    e.close_cached(["googlevideo.com", "youtube.com"], EVENING)
    assert sorted(e.closing) == ["1.2.3.4", "5.6.7.8"]


def test_quic_is_off_for_browsers_while_a_site_is_blocked(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    e = _bare_enforcer(db)
    e.quic = {}
    added, removed = [], []
    monkeypatch.setattr(firewall, "add_quic", lambda exe, path: added.append((exe, path)) or True)
    monkeypatch.setattr(firewall, "remove_quic", lambda exe: removed.append(exe) or True)
    monkeypatch.setattr(apps, "list_processes", lambda: [(1, "chrome.exe"), (2, "notepad.exe"), (3, "chrome.exe")])
    monkeypatch.setattr(apps, "process_path", lambda pid: {1: r"C:\Chrome\chrome.exe", 3: r"C:\Chrome\chrome.exe",
                                                           2: r"C:\Windows\notepad.exe"}[pid])
    e.update_quic(True)
    e.update_quic(True)
    assert added == [("chrome.exe", r"C:\Chrome\chrome.exe")] and removed == []
    assert json.loads(db.get_setting(service.QUIC_KEY)) == {"chrome.exe": r"C:\Chrome\chrome.exe"}
    e.update_quic(False)
    assert removed == ["chrome.exe"] and json.loads(db.get_setting(service.QUIC_KEY)) == {}


def test_runtime_marks_are_not_in_a_backup():
    import backup
    assert {COUNTED_KEY, service.QUIC_KEY} <= backup.RUNTIME_KEYS
