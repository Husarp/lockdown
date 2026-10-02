"""0.84.2: the tray checks the windows twice a second instead of every 2 seconds.

Adam: "to make everything work faster make Lockdown check windows 2 times per second". Since 0.84.1 the tracker
counts the time that really passed, so a faster tick changes how SOON things happen - a tab sent back, a block
starting as the minutes run out - never how MUCH is counted. Pinned here:

- half-second ticks count real time exactly: per bucket (fractions of a second are kept, per bucket, until they
  make a whole one), when you switch every tick, with uneven ticks, and through a stall the service covers;
- the database is written every FLUSH_SEC in one transaction, not 2x a second per item - and at once when a limit
  or an allowance is about to be reached, so a block starts within a second of the time running out;
- a browser's address bar (UI Automation) is read only when its window or title changed, or URL_REFRESH_SEC after
  the last read; the process list at most every PROCESS_REFRESH_SEC unless a new process is in front.
"""
import contextlib
import random
import threading
import time
from datetime import datetime, timedelta

import pytest

import rules
import service
import uiautomation
from blocker import apps
from db import Database
from monitor import usage as usage_mod, word_guard
from monitor.usage import COUNTED_KEY, FLUSH_SEC, TICK_SEC, URL_REFRESH_SEC, UsageTracker

EVENING = datetime(2026, 10, 2, 19, 0)
YOUTUBE = "https://www.youtube.com/watch?v=x"


class Clock:
    """Trusted time (now_from_db) and the tracker's monotonic clock, moved together by hand."""

    def __init__(self, monkeypatch, start: datetime = EVENING):
        self.now = start
        self.mono = 1000.0
        monkeypatch.setattr(usage_mod, "now_from_db", lambda db: self.now)
        monkeypatch.setattr(usage_mod.time, "monotonic", lambda: self.mono)

    def __iadd__(self, seconds: float):
        self.now += timedelta(seconds=seconds)
        self.mono += seconds
        return self


def counting(clock: Clock) -> UsageTracker:
    """A tracker that has been counting up to the clock's time (so the first tick counts the time before it)."""
    tracker = UsageTracker()
    tracker.counted_ts = clock.now.timestamp()
    return tracker


def used(db, owner: str, bucket: str, now: datetime) -> int:
    return db.usage_lookup(now)(owner, bucket)


def day(now: datetime) -> str:
    return rules.time_bucket("day", now)


def reasons(db, now, item_id):
    return [b["reason"] for b in db.blocks(now) if b["item"]["id"] == item_id]


def commits(db) -> list:
    seen = []
    db.conn.set_trace_callback(lambda sql: sql == "COMMIT" and seen.append(1))
    return seen


# ---------- real time, exactly ----------

def test_half_second_ticks_count_real_time_exactly(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 600}])
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    for _ in range(round(20 * 60 / TICK_SEC)):                    # 20 minutes
        clock += TICK_SEC
        tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
    tracker.flush(db)
    assert used(db, f"item:{game}", day(clock.now), clock.now) == 20 * 60            # not 1199, not 1201
    screen = db.conn.execute("SELECT SUM(seconds) FROM activity").fetchone()[0]
    assert screen == 20 * 60


def test_uneven_ticks_count_exactly_what_passed(tmp_path, monkeypatch):
    """A tick takes a moment, the window is busy: the gaps are 0.5-0.6 s and never the same. Within the half
    second still waiting to make a whole one."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    start, rnd = clock.now, random.Random(7)
    for _ in range(2000):
        clock += TICK_SEC + rnd.random() * 0.1
        tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
    tracker.flush(db)
    real = (clock.now - start).total_seconds()
    counted = used(db, f"item:{game}", day(clock.now), clock.now)
    assert real - 1 < counted <= real


def test_switching_every_tick_shares_the_time_fairly(tmp_path, monkeypatch):
    """Game and YouTube in front by turns, half a second each. Whole seconds per tick with one shared remainder
    would hand every whole second to the same one of the two (0 s for one, all of it for the other); each bucket
    keeps its own fractions, so each gets its half."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    yt = db.add_item("YouTube", ["youtube.com"], "site")
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    senses = [lambda: ("game.exe", None, 0), lambda: ("chrome.exe", YOUTUBE, 0)]
    for i in range(1200):                                          # 10 minutes
        clock += TICK_SEC
        tracker.tick(db, senses[i % 2], running_exes=lambda: {"game.exe", "chrome.exe"})
    tracker.flush(db)
    assert used(db, f"item:{game}", day(clock.now), clock.now) == 300
    assert used(db, f"item:{yt}", day(clock.now), clock.now) == 300


def test_a_stall_covered_by_the_service_is_not_counted_twice_at_half_second_ticks(tmp_path, monkeypatch):
    """The tray freezes for 2 minutes with up to FLUSH_SEC of counted time not yet written; the service counts
    the game from the last written mark meanwhile. The unwritten part is covered by what it counted - dropped,
    not added on top."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    monkeypatch.setattr(apps, "list_processes_full", lambda: [(5, 1, "game.exe")])
    monkeypatch.setattr(apps, "process_path", lambda pid: None)
    e = object.__new__(service.Enforcer)
    e.db = db
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    start = clock.now
    for step in range(round(6 * 60 / TICK_SEC)):                   # 6 minutes; frozen from minute 2 to 4
        clock += TICK_SEC
        if not 240 <= step < 480:
            tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
        if step % 4 == 3:                                          # the service's 2-second pass
            e.count_unwatched_apps(clock.now)
    tracker.flush(db)
    counted = used(db, f"item:{game}", day(clock.now), clock.now)
    assert abs(counted - (clock.now - start).total_seconds()) <= 2, counted


# ---------- batched writes ----------

def test_writes_are_batched_not_twice_a_second(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 600}])
    yt = db.add_item("YouTube", ["youtube.com"], "site", rules=[{"rule_type": "time_limit", "daily_limit_min": 600}])
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    others = [("game.exe", None, r"D:\Games\game.exe", 5)]
    tick = lambda: tracker.tick(db, lambda: ("chrome.exe", YOUTUBE, 0, None, 7, others),
                                running_exes=lambda: {"game.exe", "chrome.exe"})
    clock += TICK_SEC
    tick()                                                         # (the first tick writes the mark at once)
    done = commits(db)
    for _ in range(round(60 / TICK_SEC)):                          # a minute: 120 ticks, two items in use
        clock += TICK_SEC
        tick()
        counted = (clock.now - EVENING).total_seconds()
        # never more than FLUSH_SEC (and the fraction of a second still waiting) behind
        assert used(db, f"item:{game}", day(clock.now), clock.now) >= counted - FLUSH_SEC - 1
    assert len(done) == 60 / FLUSH_SEC                             # one transaction every 2 s - not 240+
    assert float(db.get_setting(COUNTED_KEY)) == pytest.approx(clock.now.timestamp(), abs=FLUSH_SEC)
    assert used(db, f"item:{yt}", day(clock.now), clock.now) >= 60 - FLUSH_SEC - 1


def test_what_is_waiting_is_written_when_the_tracker_stops(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    monkeypatch.setattr(uiautomation, "UIAutomationInitializerInThread", contextlib.nullcontext, raising=False)
    monkeypatch.setattr(usage_mod, "TICK_SEC", 0.01)
    monkeypatch.setattr(usage_mod, "FLUSH_SEC", 3600)              # nothing would be written by itself
    monkeypatch.setattr(usage_mod, "list_processes", lambda: [(5, "game.exe")])
    monkeypatch.setattr(usage_mod, "sense_desktop", lambda: ("game.exe", None, 0))
    tracker = UsageTracker()
    ticked = threading.Event()
    real_tick = tracker.tick

    def tick(*a, **k):
        real_tick(*a, **k)
        if tracker.counted_ts and len([1 for e in tracker.pending.values() if e[0] >= 1]):
            ticked.set()
    monkeypatch.setattr(tracker, "tick", tick)
    thread = threading.Thread(target=tracker._count, args=(db,), daemon=True)
    thread.start()
    assert ticked.wait(10)
    before = used(db, f"item:{game}", day(datetime.now()), datetime.now())
    tracker.stop_event.set()
    thread.join(5)
    assert not thread.is_alive()
    assert used(db, f"item:{game}", day(datetime.now()), datetime.now()) > before


def test_a_failed_write_loses_nothing(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app")
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    real = db.add_tracked

    def locked(*a, **k):
        raise __import__("sqlite3").OperationalError("database is locked")
    monkeypatch.setattr(db, "add_tracked", locked)
    for _ in range(8):
        clock += TICK_SEC
        with pytest.raises(Exception):
            tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
    monkeypatch.setattr(db, "add_tracked", real)
    clock += TICK_SEC
    tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
    tracker.flush(db)
    assert used(db, f"item:{game}", day(clock.now), clock.now) == int(9 * TICK_SEC)   # all 9 ticks


# ---------- the block starts on time ----------

def _youtube_allowance(db):
    """YouTube in a group blocked 21:00-05:00 with 15 minutes allowed in those hours (one pot)."""
    yt = db.add_item("YouTube", ["youtube.com"], "site", rules=[])
    sched = rules.make_schedule("block", [(list(range(7)), "21:00", "05:00")])
    gid = db.add_group("Evening", [{"rule_type": "scheduled", "schedule": sched, "allowance_min": 15}], {yt: {}})
    return yt, gid


def test_the_block_starts_within_a_second_of_the_allowance_running_out(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    yt, _gid = _youtube_allowance(db)
    clock = Clock(monkeypatch, datetime(2026, 10, 2, 22, 0))
    monkeypatch.setattr(word_guard, "now_from_db", lambda db: clock.now)
    tracker = counting(clock)
    sites = word_guard.BlockedSites()
    runs_out = clock.now + timedelta(minutes=15)
    blocked_at = sent_back_at = None
    writes = usage_mod.limit_writes
    while clock.now < runs_out + timedelta(seconds=10):
        clock += TICK_SEC
        tracker.tick(db, lambda: ("chrome.exe", YOUTUBE, 0), running_exes=lambda: {"chrome.exe"})
        if blocked_at is None and reasons(db, clock.now, yt) == ["schedule"]:
            blocked_at = clock.now
        if sent_back_at is None and word_guard.site_action(YOUTUBE, sites.get(db, clock.mono)) == "back":
            sent_back_at = clock.now
    assert blocked_at is not None and runs_out <= blocked_at <= runs_out + timedelta(seconds=1)
    # the tab check's list of blocked sites was read again at once - not at its next 2-second read
    assert sent_back_at == blocked_at
    assert usage_mod.limit_writes > writes


def test_a_daily_limit_blocks_on_the_tick_it_runs_out(tmp_path, monkeypatch):
    """1 s already used today, so the limit runs out between two of the 2-second writes: it is still written -
    and blocks - on the very tick it runs out."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 3}])
    db.add_usage({(f"item:{game}", day(EVENING))}, 1, EVENING.date())
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    runs_out = clock.now + timedelta(minutes=3) - timedelta(seconds=1)
    while not reasons(db, clock.now, game):
        clock += TICK_SEC
        tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
        assert clock.now <= runs_out
    assert clock.now == runs_out                                   # not a tick before, not a tick after
    assert reasons(db, clock.now, game) == ["limit"]


def test_the_tab_check_is_told_once_when_the_limit_is_reached_not_on_every_write_before_it(tmp_path, monkeypatch):
    """limit_writes makes the tab check and the window's "Minimize" blocks read every block again (the latter on
    the window's thread): once, on the write that reaches the limit - not on each of the writes just before it."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 1}])
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    writes = usage_mod.limit_writes
    bumped_while_allowed = 0
    for _ in range(round(70 / TICK_SEC)):
        clock += TICK_SEC
        before = usage_mod.limit_writes
        tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
        if usage_mod.limit_writes != before and not reasons(db, clock.now, game):
            bumped_while_allowed += 1
    assert reasons(db, clock.now, game) == ["limit"]
    assert usage_mod.limit_writes == writes + 1
    assert bumped_while_allowed == 0


def test_a_tick_whose_database_read_fails_counts_nothing_twice_and_loses_no_launch(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    app = db.add_item("Discord", ["discord.exe"], "app", rules=[{"rule_type": "switch_limit", "daily_switch_limit": 5}])
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    clock += TICK_SEC
    tracker.tick(db, lambda: ("code.exe", None, 0), running_exes=lambda: {"code.exe"})
    real = db.list_groups

    def locked():
        raise __import__("sqlite3").OperationalError("database is locked")
    monkeypatch.setattr(db, "list_groups", locked)
    clock += TICK_SEC
    with pytest.raises(Exception):
        tracker.tick(db, lambda: ("discord.exe", None, 0), running_exes=lambda: {"code.exe", "discord.exe"})
    monkeypatch.setattr(db, "list_groups", real)
    for _ in range(7):
        clock += TICK_SEC
        tracker.tick(db, lambda: ("discord.exe", None, 0), running_exes=lambda: {"code.exe", "discord.exe"})
    tracker.flush(db)
    opening = rules.opening_bucket({"rule_key": f"i{app}switch_limit"}, "day", clock.now)
    assert used(db, f"item:{app}", opening, clock.now) == 1        # the launch, counted once the read worked
    assert used(db, f"item:{app}", day(clock.now), clock.now) == int(8 * TICK_SEC)   # 4 s, not 4.5
    screen = db.conn.execute("SELECT SUM(seconds) FROM activity").fetchone()[0]
    assert screen == int(9 * TICK_SEC)                             # 4.5 s in all (code 0.5 + discord 4)


def test_time_close_to_a_limit_is_written_every_tick_and_then_batched_again(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 1}])
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    done = commits(db)
    per_tick = []
    for _ in range(round(90 / TICK_SEC)):
        clock += TICK_SEC
        before = len(done)
        tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
        per_tick.append(len(done) - before)
    secs = [TICK_SEC * (i + 1) for i in range(len(per_tick))]
    near = [w for s, w in zip(secs, per_tick) if 60 - FLUSH_SEC < s <= 60]
    assert near and all(near)                                      # every tick in the last 2 s before the limit
    over = [w for s, w in zip(secs, per_tick) if s > 62]
    assert sum(over) <= len(over) * TICK_SEC / FLUSH_SEC + 1       # past it (blocked, not yet closed): batched


def test_a_limit_already_reached_does_not_make_every_tick_write(tmp_path, monkeypatch):
    """A disabled item over its limit keeps counting (its time is still true) - batched, like any other."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 1}])
    db.add_usage({(f"item:{game}", day(EVENING))}, 3600, EVENING.date())
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    clock += TICK_SEC
    tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
    done = commits(db)
    for _ in range(40):
        clock += TICK_SEC
        tracker.tick(db, lambda: ("game.exe", None, 0), running_exes=lambda: {"game.exe"})
    assert len(done) == 40 * TICK_SEC / FLUSH_SEC


def test_an_opening_is_written_at_once(tmp_path, monkeypatch):
    """An opening limit can go over on a single opening: no waiting for the next batch."""
    db = Database(tmp_path / "t.db")
    app = db.add_item("Discord", ["discord.exe"], "app", rules=[{"rule_type": "switch_limit", "daily_switch_limit": 1}])
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    steps = [("code.exe", set()), ("discord.exe", {"discord.exe"}), ("code.exe", set()),
             ("discord.exe", {"discord.exe"})]                     # launched, closed, launched again
    for exe, running in steps:
        clock += TICK_SEC
        tracker.tick(db, lambda e=exe: (e, None, 0), running_exes=lambda r=running: r)
    assert reasons(db, clock.now, app) == ["switches"]


def test_limit_targets_are_the_limited_buckets_usage_targets_fills():
    sched = rules.make_schedule("block", [(list(range(7)), "21:00", "05:00")])
    rule_list = rules.counted_rules(
        {"id": 3, "rules": [{"rule_type": "time_limit", "daily_limit_min": 30, "weekly_limit_min": 200}]},
        [{"id": 9, "name": "G", "members": {3: {}},
          "rules": [{"rule_type": "scheduled", "schedule": sched, "allowance_min": 15},
                    {"rule_type": "time_limit", "daily_limit_min": 20}]}])
    now = datetime(2026, 10, 2, 22, 0)
    limits = rules.limit_targets(rule_list, now)
    assert set(limits) <= rules.usage_targets(rule_list, 3, now)
    assert sorted(limits.values()) == [15 * 60, 20 * 60, 30 * 60, 200 * 60]
    assert rules.limit_targets(rule_list, datetime(2026, 10, 2, 12, 0)).keys() == \
        {k for k in limits if not k[1].startswith("win:")}        # outside the blocked hours: no allowance


def test_hours_rules_give_the_same_answer_all_through_a_minute():
    """schedule_until is worked out once per rule and minute - exactly what it was on every call."""
    sched = rules.make_schedule("allow", [([0, 1, 2, 3, 4], "16:00", "18:00"), ([5, 6], "10:00", "22:30")])
    block = rules.make_schedule("block", [(list(range(7)), "21:00", "05:00")])
    t = datetime(2026, 10, 2, 15, 58, 0)
    for _ in range(4000):
        t += timedelta(seconds=13.7)
        for s in (sched, block):
            assert rules.schedule_until(s, t) == rules._schedule_until.__wrapped__(s, t)


# ---------- the address bar and the process list ----------

class FakeWin:
    def __init__(self):
        self.titles = {1: "Cats - YouTube - Google Chrome", 2: "Inbox - Gmail - Google Chrome"}

    def window_title(self, hwnd):
        return self.titles.get(hwnd, "")


class FakeBrowser:
    def __init__(self):
        self.urls = {1: YOUTUBE, 2: "https://mail.google.com/"}
        self.asked = []

    def browser_url(self, hwnd):
        self.asked.append(hwnd)
        return self.urls.get(hwnd)


@pytest.fixture
def bar(monkeypatch):
    usage_mod._seen.clear()
    usage_mod._no_bar.clear()
    clock = Clock(monkeypatch)
    return clock, FakeWin(), FakeBrowser()


def test_the_address_bar_is_not_read_again_while_nothing_changed(bar):
    clock, win, browser = bar
    for _ in range(3):                                             # 1.5 s of ticks
        assert usage_mod._read_url(browser, win, 1, False) == YOUTUBE
        clock += TICK_SEC
    assert browser.asked == [1]
    clock += URL_REFRESH_SEC                                       # 2 s since the read: read again
    assert usage_mod._read_url(browser, win, 1, False) == YOUTUBE
    assert browser.asked == [1, 1]


def test_another_title_or_window_is_read_at_once(bar):
    clock, win, browser = bar
    usage_mod._read_url(browser, win, 1, False)
    clock += TICK_SEC
    win.titles[1] = "Reddit - Google Chrome"
    browser.urls[1] = "https://www.reddit.com/"
    assert usage_mod._read_url(browser, win, 1, False) == "https://www.reddit.com/"
    assert usage_mod._read_url(browser, win, 2, True) == "https://mail.google.com/"
    assert browser.asked == [1, 1, 2]


def test_a_page_that_keeps_its_title_is_still_caught_within_the_refresh(bar):
    """A single-page site changes its address without changing the window title: the periodic re-read finds the
    new address no later than URL_REFRESH_SEC after the last read."""
    clock, win, browser = bar
    win.titles[1] = "My feed"
    browser.urls[1] = "https://example.com/feed"
    usage_mod._read_url(browser, win, 1, False)
    browser.urls[1] = "https://www.youtube.com/shorts/1"           # same title, a blocked site
    seen = []
    for _ in range(round(URL_REFRESH_SEC / TICK_SEC)):
        clock += TICK_SEC
        seen.append(usage_mod._read_url(browser, win, 1, False))
    assert seen[-1] == "https://www.youtube.com/shorts/1"


def test_a_hidden_address_bar_is_not_searched_every_tick(bar):
    """Full screen hides the address bar: the last URL still counts (same title) and the bar is looked for
    again every URL_REFRESH_SEC, not on every half-second tick."""
    clock, win, browser = bar
    usage_mod._read_url(browser, win, 1, False)
    browser.urls = {}
    got = []
    for _ in range(round(10 / TICK_SEC)):
        clock += TICK_SEC
        got.append(usage_mod._read_url(browser, win, 1, False))
    assert set(got) == {YOUTUBE}
    assert len(browser.asked) <= 1 + 10 / URL_REFRESH_SEC


def test_a_browser_window_with_no_address_bar_in_front_is_not_searched_every_tick(bar):
    """An installed web app (no address bar) in front, never read: asked every URL_REFRESH_SEC, not twice a
    second - and at once when its title changes (it may have become a normal window with a bar)."""
    clock, win, browser = bar
    browser.urls = {}
    win.titles[1] = "Calendar"   # (a YouTube title would count as YouTube with no bar - site_block.title_site)
    for _ in range(round(10 / TICK_SEC)):
        clock += TICK_SEC
        assert usage_mod._read_url(browser, win, 1, False) is None
    assert len(browser.asked) <= 1 + 10 / URL_REFRESH_SEC
    asked = len(browser.asked)
    browser.urls = {1: "https://www.reddit.com/"}
    win.titles[1] = "Reddit - Google Chrome"
    clock += TICK_SEC
    assert usage_mod._read_url(browser, win, 1, False) == "https://www.reddit.com/"
    assert len(browser.asked) == asked + 1


def test_the_process_list_is_read_every_2_seconds_or_for_a_new_process(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    app = db.add_item("Discord", ["discord.exe"], "app", rules=[{"rule_type": "switch_limit", "daily_switch_limit": 5}])
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    procs = [(10, "code.exe")]
    reads = []
    monkeypatch.setattr(usage_mod, "list_processes", lambda: reads.append(1) or list(procs))
    front = {"sense": ("code.exe", None, 0, None, 10)}
    for _ in range(6):                                             # 3 s, the same program in front
        clock += TICK_SEC
        tracker.tick(db, lambda: front["sense"])
    assert len(reads) == 2                                         # at the start and 2 s later - not 6 times
    procs.append((20, "discord.exe"))                              # started, and in front at once
    front["sense"] = ("discord.exe", None, 0, None, 20)
    clock += TICK_SEC
    tracker.tick(db, lambda: front["sense"])
    assert len(reads) == 3                                         # read at once: a new process in front
    opening = rules.opening_bucket({"rule_key": f"i{app}switch_limit"}, "day", clock.now)
    assert used(db, f"item:{app}", opening, clock.now) == 1        # the launch counted on that very tick


# ---------- the tab check ----------

def test_the_tab_check_rereads_blocked_sites_every_2_seconds_and_when_a_limit_is_reached(monkeypatch):
    reads = []
    sites = word_guard.BlockedSites(read=lambda db: reads.append(1) or {})
    for i in range(8):                                             # 4 s of half-second ticks
        sites.get(None, 100 + i * word_guard.TICK_SEC)
    assert len(reads) == 2
    monkeypatch.setattr(usage_mod, "limit_writes", usage_mod.limit_writes + 1)
    sites.get(None, 104.0)
    assert len(reads) == 3                                         # at once, not at its next 2-second read
    sites.get(None, 104.5)
    assert len(reads) == 3


def test_the_tracker_ticks_twice_a_second():
    assert TICK_SEC == 0.5 and word_guard.TICK_SEC == 0.5
