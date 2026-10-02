"""0.84.2: time on a limited app (and site) also counts while it is the window in front on another monitor.

Adam: "it should count on second monitor but only when its in front (minimized or in back doesnt count)".
Before, only the one foreground window counted: a game left running on the second screen while you browsed on
the first counted nothing. Now the tracker also counts the window in front on every monitor (win.front_windows):
not minimized, not behind another window, not on another virtual desktop. Each item counts once a tick however
many windows show it. Screen time ("what you were doing") stays the foreground window only.

Windows is faked at the enumeration: win.enum_windows (the Z-order, top first) and win.window_facts (each window's
style, rectangle and monitor); the selection (win.pick_front), sense_desktop and the tracker are the real code.
"""
from datetime import datetime, timedelta

import pytest

import rules
from db import Database
from monitor import browser_url, usage as usage_mod, win
from monitor.usage import UsageTracker
from monitor.win import WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT, WindowFacts

EVENING = datetime(2026, 10, 2, 19, 0)
M1, M2 = (0, 0, 1920, 1080), (1920, 0, 3840, 1080)          # two side-by-side 1080p monitors
FULL1, FULL2 = M1, M2
CHROME, GAME, WORD = r"C:\Chrome\chrome.exe", r"D:\Games\Game\game.exe", r"C:\Office\winword.exe"


def facts(hwnd, rect, **kw):
    """A window shown at `rect` (screen coordinates; monitor 1 is 0-1920, monitor 2 1920-3840)."""
    return WindowFacts(hwnd, rect=rect, **kw)


class Desktop:
    """The windows on screen, top first, and the process behind each."""

    def __init__(self, monkeypatch, foreground: int, windows: list[WindowFacts], procs: dict[int, tuple[int, str]],
                 urls: dict[int, str] | None = None, locked: bool = False):
        self.foreground, self.windows, self.procs, self.urls = foreground, windows, procs, urls or {}
        self.enumerations = 0
        monkeypatch.setattr(win, "enum_windows", self._enum)
        monkeypatch.setattr(win, "window_facts", lambda hwnd: next(w for w in self.windows if w.hwnd == hwnd))
        monkeypatch.setattr(win, "monitors", lambda: [M1, M2])
        monkeypatch.setattr(win, "window_pid", lambda hwnd: self.procs.get(hwnd, (0, ""))[0])
        monkeypatch.setattr(win, "pid_path", lambda pid: next((p for q, p in self.procs.values() if q == pid), ""))
        monkeypatch.setattr(win, "foreground_process", self._foreground)
        monkeypatch.setattr(win, "session_locked", lambda: locked)
        monkeypatch.setattr(win, "idle_seconds", lambda: 0.0)
        monkeypatch.setattr(browser_url, "browser_url", lambda hwnd: self.urls.get(hwnd))
        usage_mod._no_bar.clear()
        usage_mod._seen.clear()

    def _enum(self):
        self.enumerations += 1
        return [w.hwnd for w in self.windows]

    def _foreground(self):
        pid, path = self.procs.get(self.foreground, (0, ""))
        return (self.foreground, win.exe_name(path), path) if self.foreground else (0, "", "")


class Clock:
    def __init__(self, monkeypatch, start=EVENING):
        self.now = start
        monkeypatch.setattr(usage_mod, "now_from_db", lambda db: self.now)


def counting(clock):
    """A tracker that has been counting up to the clock's time - so each tick counts the `every` seconds before it,
    the first one too (a fresh tracker's first tick counts one TICK_SEC: it can't know what came before)."""
    tracker = UsageTracker()
    tracker.counted_ts = clock.now.timestamp()
    return tracker


def play(db, monkeypatch, seconds=120, every=2, running=lambda: {"game.exe", "chrome.exe", "winword.exe"}):
    clock = Clock(monkeypatch)
    tracker = counting(clock)
    for _ in range(round(seconds / every)):
        clock.now += timedelta(seconds=every)
        tracker.tick(db, usage_mod.sense_desktop, running_exes=running)
    return clock.now, tracker


def used(db, item_id, now):
    return db.usage_lookup(now)(f"item:{item_id}", rules.time_bucket("day", now))


def setup(tmp_path):
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    yt = db.add_item("YouTube", ["youtube.com"], "site", rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    return db, game, yt


PROCS = {1: (100, CHROME), 2: (200, GAME), 3: (300, WORD), 4: (100, CHROME)}


# ---------- what counts ----------

def test_a_game_on_top_of_monitor_2_counts_while_you_browse_on_monitor_1(tmp_path, monkeypatch):
    db, game, yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(2, FULL2)], procs=PROCS,
            urls={1: "https://www.youtube.com/watch?v=x"})
    now, tracker = play(db, monkeypatch)
    assert used(db, game, now) == 120                      # before 0.84.2: 0
    assert used(db, yt, now) == 120
    assert tracker.in_use == {game, yt}


def test_a_minimized_game_does_not_count(tmp_path, monkeypatch):
    db, game, yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), WindowFacts(2, minimized=True)], procs=PROCS,
            urls={1: "https://www.youtube.com/"})
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 0
    assert used(db, yt, now) == 120


def test_a_game_behind_another_window_on_monitor_2_does_not_count(tmp_path, monkeypatch):
    db, game, _yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(3, FULL2), facts(2, FULL2)], procs=PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 0


def test_a_game_mostly_covered_does_not_count(tmp_path, monkeypatch):
    db, game, _yt = setup(tmp_path)
    covering = (1920, 0, 3840, 900)                        # 83 % of the game hidden
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(3, covering), facts(2, FULL2)], procs=PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 0


def test_a_small_window_on_top_does_not_hide_the_game(tmp_path, monkeypatch):
    """A calculator, a picture-in-picture video, a tiny window put there on purpose: you still see the game."""
    db, game, _yt = setup(tmp_path)
    small = (3400, 700, 3840, 1080)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(3, small), facts(2, FULL2)], procs=PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120


def test_a_small_focused_window_does_not_hide_a_video_under_it(tmp_path, monkeypatch):
    """Same on the one monitor: a small window you click on top of full-screen YouTube left YouTube uncounted."""
    db, _game, yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=3, windows=[facts(3, (100, 100, 600, 500)), facts(1, FULL1)], procs=PROCS,
            urls={1: "https://www.youtube.com/watch?v=x"})
    now, _ = play(db, monkeypatch)
    assert used(db, yt, now) == 120


@pytest.mark.parametrize("over", [
    WindowFacts(9, exstyle=WS_EX_TOOLWINDOW, rect=FULL2),                     # a tool window
    WindowFacts(9, exstyle=WS_EX_NOACTIVATE, rect=FULL2),                     # an overlay
    WindowFacts(9, exstyle=WS_EX_LAYERED | WS_EX_TRANSPARENT, rect=FULL2),    # click-through
    WindowFacts(9, cls="Shell_SecondaryTrayWnd", rect=(1920, 1032, 3840, 1080)),
    WindowFacts(9, cloaked=True),
    WindowFacts(9, visible=False),
])
def test_overlays_taskbars_and_hidden_windows_hide_nothing(tmp_path, monkeypatch, over):
    db, game, _yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), over, facts(2, FULL2)], procs={**PROCS, 9: (900, WORD)})
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120


def test_a_game_on_another_virtual_desktop_does_not_count(tmp_path, monkeypatch):
    db, game, _yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), WindowFacts(2, cloaked=True, rect=FULL2)], procs=PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 0


def test_a_game_spanning_both_monitors_counts_once(tmp_path, monkeypatch):
    db, game, _yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=2, windows=[facts(2, (1000, 0, 3840, 1080)), facts(1, FULL1)], procs=PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120


def test_two_windows_of_the_game_one_per_monitor_count_once(tmp_path, monkeypatch):
    """The game focused on monitor 1 and its second window on top of monitor 2: one game, one tick's time."""
    db, game, _yt = setup(tmp_path)
    procs = {**PROCS, 5: (200, GAME)}
    Desktop(monkeypatch, foreground=2, windows=[facts(2, FULL1), facts(5, FULL2)], procs=procs)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120


def test_youtube_on_top_of_monitor_2_counts_while_you_work_on_monitor_1(tmp_path, monkeypatch):
    """A browser window on top of another monitor: its active tab's site counts (its URL read by UI Automation)."""
    db, _game, yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(4, FULL2)], procs=PROCS,
            urls={4: "https://www.youtube.com/watch?v=x"})
    now, _ = play(db, monkeypatch)
    assert used(db, yt, now) == 120


def test_a_youtube_window_behind_another_on_monitor_2_does_not_count(tmp_path, monkeypatch):
    db, _game, yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(2, FULL2), facts(4, FULL2)], procs=PROCS,
            urls={4: "https://www.youtube.com/watch?v=x"})
    now, _ = play(db, monkeypatch)
    assert used(db, yt, now) == 0


def test_a_category_limit_counts_a_member_on_monitor_2(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    db.set_category("app", "game.exe", "Games")
    games = db.add_item("Games", ["Games"], "category", rules=[{"rule_type": "time_limit", "daily_limit_min": 30}])
    Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(2, FULL2)], procs=PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, games, now) == 120


def test_the_limit_is_reached_and_the_game_blocked(tmp_path, monkeypatch):
    db, game, _yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(2, FULL2)], procs=PROCS)
    now, _ = play(db, monkeypatch, seconds=61 * 60, every=10)
    assert [b["reason"] for b in db.blocks(now) if b["item"]["id"] == game] == ["limit"]


def test_a_locked_pc_counts_nothing_on_any_monitor(tmp_path, monkeypatch):
    db, game, yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(2, FULL2)], procs=PROCS,
            urls={1: "https://www.youtube.com/"}, locked=True)
    now, _ = play(db, monkeypatch)
    assert db.usage_lookup(now).data == {}


def test_nothing_focused_still_counts_the_game_on_top_of_monitor_2(tmp_path, monkeypatch):
    """Unlocked, no foreground window for a moment (Windows between two windows): the game shown still counts."""
    db, game, _yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=0, windows=[facts(3, FULL1), facts(2, FULL2)], procs=PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120


# ---------- what stays the foreground only ----------

def test_screen_time_stays_what_you_were_doing(tmp_path, monkeypatch):
    db, game, _yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(2, FULL2)], procs=PROCS)
    play(db, monkeypatch, seconds=60)
    rows = {r["exe"] for r in db.conn.execute("SELECT exe FROM activity")}
    assert rows == {"winword.exe"}
    assert list(db.conn.execute("SELECT * FROM switch_events")) == []


def test_switching_on_monitor_1_is_not_a_switch_to_the_game_on_monitor_2(tmp_path, monkeypatch):
    """"Every switch" opening limits count switching TO the item, which you did only on the focused monitor."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app",
                       rules=[{"rule_type": "switch_limit", "daily_switch_limit": 1, "switch_mode": "switch"}])
    desk = Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(1, FULL1), facts(2, FULL2)],
                   procs=PROCS)
    clock, tracker = Clock(monkeypatch), UsageTracker()
    for fg in [3, 1, 3, 1, 3]:                           # Word <-> Chrome on monitor 1, four switches
        desk.foreground = fg
        clock.now += timedelta(seconds=2)
        tracker.tick(db, usage_mod.sense_desktop, running_exes=lambda: {"game.exe"})
    assert [b for b in db.blocks(clock.now) if b["item"]["id"] == game] == []


def test_one_enumeration_per_tick(tmp_path, monkeypatch):
    db, _game, _yt = setup(tmp_path)
    desk = Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(2, FULL2)], procs=PROCS)
    play(db, monkeypatch, seconds=20, every=2)
    assert desk.enumerations == 10


def test_a_browser_window_without_an_address_bar_is_not_searched_every_tick(tmp_path, monkeypatch):
    """An installed web app (no address bar) on top of monitor 2: after 3 failed reads in a row its UI tree is
    searched again every 30 s, not every 2 s."""
    db, _game, _yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(4, FULL2)], procs=PROCS)
    asked = []
    monkeypatch.setattr(browser_url, "browser_url", lambda hwnd: asked.append(hwnd))
    play(db, monkeypatch, seconds=20, every=2)
    assert asked == [4, 4, 4]


def test_a_browser_that_wont_answer_does_not_stop_the_count(tmp_path, monkeypatch):
    db, game, _yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(4, (1920, 0, 2880, 1080)),
                                                facts(2, (2880, 0, 3840, 1080))], procs=PROCS)

    def broken(hwnd):
        raise OSError("UI Automation timed out")
    monkeypatch.setattr(browser_url, "browser_url", broken)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120                    # side by side on monitor 2: both in front


# ---------- the selection itself ----------

def test_the_top_window_of_every_monitor_is_in_front():
    windows = [facts(1, FULL1), facts(3, FULL1), facts(2, FULL2), facts(4, FULL2)]
    assert win.pick_front(windows, [M1, M2]) == [1, 2]


def test_a_window_hanging_a_little_over_onto_monitor_2_does_not_hide_the_game_there():
    windows = [facts(1, (0, 0, 2200, 1080)), facts(2, FULL2)]
    assert win.pick_front(windows, [M1, M2]) == [1, 2]


def test_zero_size_windows_are_never_in_front():
    assert win.pick_front([facts(1, (10, 10, 10, 10)), facts(2, FULL1)], [M1, M2]) == [2]


def test_front_windows_names_each_window_once_per_process(monkeypatch):
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(2, FULL2)], procs=PROCS)
    assert win.front_windows() == [(1, 100, "chrome.exe", CHROME), (2, 200, "game.exe", GAME)]


def test_a_protected_game_without_a_path_still_has_a_name(monkeypatch):
    """Anti-cheat refuses OpenProcess: the name comes from the process list, as for the foreground window."""
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(2, FULL2)], procs=PROCS)
    monkeypatch.setattr(win, "pid_path", lambda pid: CHROME if pid == 100 else "")
    import blocker.apps
    monkeypatch.setattr(blocker.apps, "list_processes", lambda: [(100, "chrome.exe"), (200, "game-win64-shipping.exe")])
    assert win.front_windows()[1] == (2, 200, "game-win64-shipping.exe", "")


def test_on_this_machine_the_fake_win32_finds_no_windows():
    """(Linux: the conftest fakes return 0 for every call - the real enumeration just finds nothing.)"""
    import sys
    if sys.platform == "win32":
        pytest.skip("real Windows")
    assert win.front_windows() == []


# ---------- found in review (0.84.2): ways to watch on monitor 2 without it counting, and double time ----------

GROUP_PROCS = {**PROCS, 9: (900, WORD), 8: (800, WORD)}


@pytest.mark.parametrize("alpha", [1, 0, 120, win.UNKNOWN_ALPHA])
def test_a_see_through_window_over_monitor_2_does_not_hide_the_game(tmp_path, monkeypatch, alpha):
    """AutoHotkey: an always-on-top Notepad over all of monitor 2, `WinSet Transparent 1` - the game under it is in
    plain sight. Only a nearly opaque window hides anything (alpha unknown: per-pixel transparency, a colour key)."""
    db, game, _yt = setup(tmp_path)
    veil = facts(9, FULL2, exstyle=WS_EX_LAYERED, alpha=alpha)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), veil, facts(2, FULL2)], procs=GROUP_PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120                    # before the fix: 0


def test_a_nearly_opaque_layered_window_still_hides_the_game(tmp_path, monkeypatch):
    db, game, _yt = setup(tmp_path)
    solid = facts(9, FULL2, exstyle=WS_EX_LAYERED, alpha=250)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), solid, facts(2, FULL2)], procs=GROUP_PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 0


@pytest.mark.parametrize("style", [WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_LAYERED | WS_EX_TRANSPARENT])
def test_restyling_the_game_window_does_not_take_it_out_of_the_count(tmp_path, monkeypatch, style):
    """`WinSet ExStyle +0x80` (tool window), "no activate" or click-through on the game on monitor 2: still the
    game you see."""
    db, game, _yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(2, FULL2, exstyle=style)], procs=PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120                    # before the fix: 0


def test_a_restyled_youtube_window_on_monitor_2_still_counts_as_youtube(tmp_path, monkeypatch):
    db, _game, yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(4, FULL2, exstyle=WS_EX_TOOLWINDOW)],
            procs=PROCS, urls={4: "https://www.youtube.com/watch?v=x"})
    now, _ = play(db, monkeypatch)
    assert used(db, yt, now) == 120


def test_covering_the_rest_of_the_browser_does_not_hide_the_video(tmp_path, monkeypatch):
    """Chrome maximized on monitor 2, the video 1280x720 in its top left, two windows over everything else (56 % of
    the browser): the video is in plain sight. A quarter of a monitor or more in view is in front."""
    db, _game, yt = setup(tmp_path)
    right = facts(9, (1920 + 1280, 0, 3840, 1080))
    below = facts(8, (1920, 720, 1920 + 1280, 1080))
    Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), right, below, facts(4, FULL2)], procs=GROUP_PROCS,
            urls={4: "https://www.youtube.com/watch?v=x"})
    now, _ = play(db, monkeypatch)
    assert used(db, yt, now) == 120                      # before the fix: 0


def test_a_browser_stretched_over_both_monitors_counts_for_its_part_on_monitor_2(tmp_path, monkeypatch):
    """60 % of the browser on monitor 1 behind the maximized focused window, 40 % (1280 px) in view on monitor 2 with
    the video in it: it belonged to monitor 1 only, and didn't count."""
    db, _game, yt = setup(tmp_path)
    Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(4, (0, 0, 3200, 1080))], procs=PROCS,
            urls={4: "https://www.youtube.com/watch?v=x"})
    now, _ = play(db, monkeypatch)
    assert used(db, yt, now) == 120                      # before the fix: 0


def test_a_window_cut_down_by_a_region_hides_only_what_is_left_of_it(tmp_path, monkeypatch):
    """`WinSet Region 0-0 W1 H1` on a maximized Notepad: it covers one pixel, not the game."""
    db, game, _yt = setup(tmp_path)
    tiny = facts(9, FULL2, region=win.SIMPLEREGION, region_box=(1920, 0, 1921, 1))
    empty = facts(8, FULL2, region=win.NULLREGION)
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), tiny, empty, facts(2, FULL2)], procs=GROUP_PROCS)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120


def test_a_shared_group_limit_counts_two_monitors_once(tmp_path, monkeypatch):
    """The game on monitor 2 and YouTube focused on monitor 1, one group with a 60-minute limit: 120 real seconds
    are 120 s of the group's hour, not 240."""
    db, game, yt = setup(tmp_path)
    group = db.add_group("Fun", [{"rule_type": "time_limit", "daily_limit_min": 60}], {game: None, yt: None})
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(2, FULL2)], procs=PROCS,
            urls={1: "https://www.youtube.com/watch?v=x"})
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120 and used(db, yt, now) == 120
    assert db.usage_lookup(now)(f"group:{group}", rules.time_bucket("day", now)) == 120   # before the fix: 240


def test_one_window_windows_errors_on_does_not_stop_the_count_elsewhere(tmp_path, monkeypatch):
    db, game, _yt = setup(tmp_path)
    desk = Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(2, FULL2)], procs=PROCS)
    real = win.window_facts

    def flaky(hwnd):
        if hwnd == 7:
            raise OSError("access denied")
        return real(hwnd)
    monkeypatch.setattr(win, "window_facts", flaky)
    monkeypatch.setattr(desk, "_enum", lambda: [7, 1, 2])
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 120


def test_a_minimized_game_s_little_helper_window_does_not_count_it(tmp_path, monkeypatch):
    """Minimized means not counted, even if the game leaves a tiny window of its own on screen."""
    db, game, _yt = setup(tmp_path)
    procs = {**PROCS, 5: (200, GAME)}
    Desktop(monkeypatch, foreground=1, windows=[facts(5, (3000, 500, 3010, 510)), facts(1, FULL1),
                                                WindowFacts(2, minimized=True)], procs=procs)
    now, _ = play(db, monkeypatch)
    assert used(db, game, now) == 0


def test_a_full_screen_youtube_video_still_counts_when_the_address_bar_is_hidden(tmp_path, monkeypatch):
    """Full-screen video hides the address bar; the tab's title is unchanged, so it is still YouTube - on monitor
    2 and focused alike. Another title (another page) is not."""
    db, _game, yt = setup(tmp_path)
    desk = Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(4, FULL2)], procs=PROCS,
                   urls={4: "https://www.youtube.com/watch?v=x"})
    titles = {4: "Cats - YouTube - Google Chrome", 3: "Document1 - Word"}
    monkeypatch.setattr(win, "window_title", lambda hwnd: titles.get(hwnd, ""))
    clock = Clock(monkeypatch)
    tracker = counting(clock)

    def ticks(n):
        for _ in range(n):
            clock.now += timedelta(seconds=2)
            tracker.tick(db, usage_mod.sense_desktop, running_exes=lambda: set())
    ticks(1)
    desk.urls = {}                                       # full screen: no address bar
    ticks(30)                                            # (past the 30 s back-off too)
    assert used(db, yt, clock.now) == 62
    titles[4] = "Inbox - Gmail - Google Chrome"
    ticks(5)
    assert used(db, yt, clock.now) == 62
    # focused, the same
    titles[4] = "Cats - YouTube - Google Chrome"
    desk.urls = {4: "https://www.youtube.com/watch?v=x"}
    desk.foreground = 4
    desk.windows = [facts(4, FULL2), facts(3, FULL1)]
    ticks(1)
    desk.urls = {}
    ticks(5)
    assert used(db, yt, clock.now) == 62 + 12


def test_one_failed_read_does_not_drop_the_site_for_30_seconds(tmp_path, monkeypatch):
    db, _game, yt = setup(tmp_path)
    desk = Desktop(monkeypatch, foreground=3, windows=[facts(3, FULL1), facts(4, FULL2)], procs=PROCS, urls={})
    clock, tracker = Clock(monkeypatch), UsageTracker()
    clock.now += timedelta(seconds=2)
    tracker.tick(db, usage_mod.sense_desktop, running_exes=lambda: set())   # (the window still being created)
    desk.urls = {4: "https://www.youtube.com/watch?v=x"}
    clock.now += timedelta(seconds=2)
    tracker.tick(db, usage_mod.sense_desktop, running_exes=lambda: set())
    assert used(db, yt, clock.now) == 2


def test_a_store_app_counts_as_the_app_not_its_frame_host(monkeypatch):
    """A Store (UWP) app's window is a frame owned by ApplicationFrameHost.exe; the app is its CoreWindow child."""
    netflix = r"C:\Program Files\WindowsApps\Netflix\wwahost.exe"
    procs = {1: (100, CHROME), 6: (600, r"C:\Windows\System32\ApplicationFrameHost.exe"), 60: (610, netflix)}
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(6, FULL2, cls=win.UWP_FRAME)], procs=procs)
    monkeypatch.setattr(win._u32, "FindWindowExW", lambda parent, after, cls, title: 60 if parent == 6 else 0)
    assert [w[2] for w in win.front_windows()] == ["chrome.exe", "wwahost.exe"]


def test_the_process_list_is_read_once_however_many_protected_windows(monkeypatch):
    import blocker.apps
    procs = {1: (100, CHROME), 2: (200, ""), 5: (500, "")}
    Desktop(monkeypatch, foreground=1, windows=[facts(1, FULL1), facts(2, (1920, 0, 2880, 1080)),
                                                facts(5, (2880, 0, 3840, 1080))], procs=procs)
    calls = []
    monkeypatch.setattr(blocker.apps, "list_processes",
                        lambda: calls.append(1) or [(200, "game-win64-shipping.exe"), (500, "other.exe")])
    assert [w[2] for w in win.front_windows()] == ["chrome.exe", "game-win64-shipping.exe", "other.exe"]
    assert calls == [1]


def test_a_partly_covered_window_counts_only_while_enough_of_it_shows():
    """In front = you see at least half of it, or a quarter of a monitor; behind = less than that."""
    game = facts(2, FULL2)
    assert win.pick_front([facts(1, FULL1), facts(3, (1920, 0, 3840, 900)), game], [M1, M2]) == [1, 3]   # 17 % left
    assert win.pick_front([facts(1, FULL1), facts(3, (1920, 0, 3300, 1080)), game], [M1, M2]) == [1, 3, 2]  # 28 %
