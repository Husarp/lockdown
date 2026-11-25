"""Synthetic visible-browser timelines at the limit-day boundary."""
import json
from datetime import datetime
from importlib.util import find_spec

import pytest

import rules
from db import Database
from monitor import usage, win
from test_browser_tab_reader import HWND, desktop
from test_fast_tick import Clock
from test_usage_counting import counting, play, reasons, used

# The integration run exercises the real, separately owned accounting writer.
half_open_writer = pytest.mark.skipif(find_spec("quota_accounting") is None,
                                     reason="T6 half-open accounting is integrated separately")


@pytest.fixture
def visible_chrome(desktop, monkeypatch):
    desktop.title = "Clip - YouTube - Personal - Google Chrome"
    monkeypatch.setattr(win, "foreground_process", lambda: (HWND, desktop.exe, str(desktop.file)))
    monkeypatch.setattr(win, "foreground", lambda: (HWND, desktop.exe))
    monkeypatch.setattr(win, "front_windows", lambda: [])
    monkeypatch.setattr(win, "session_locked", lambda: False)
    return desktop


@pytest.mark.parametrize("sleep", [pytest.param(0, marks=half_open_writer), 8 * 3600])
@pytest.mark.parametrize("failure", ["fullscreen", "empty"])
def test_confirmed_youtube_with_profile_keeps_counting_after_3am(tmp_path, monkeypatch, visible_chrome, sleep, failure):
    db = Database(tmp_path / "t.db")
    db.set_setting(rules.RESET_KEY, json.dumps({"time": "03:00"}))
    yt = db.add_item("YouTube", ["youtube.com"], "site",
                     rules=[{"rule_type": "time_limit", "daily_limit_min": 1}])
    chrome = db.add_item("Chrome", ["chrome.exe"], "app",
                         rules=[{"rule_type": "time_limit", "daily_limit_min": 1}])
    clock = Clock(monkeypatch, datetime(2026, 10, 3, 2, 59, 40))
    tracker = counting(clock)
    old = rules.time_bucket("day", clock.now, db.limit_clock())
    play(db, tracker, clock, 2, 2, usage.sense_desktop)
    assert used(db, f"item:{yt}", old, clock.now) == 2
    # Fullscreen hides the bar; an update can also leave it returning an empty value.
    visible_chrome.bar.IsOffscreen = failure == "fullscreen"
    if failure == "empty":
        visible_chrome.bar.value = ""
    play(db, tracker, clock, 16, 2, usage.sense_desktop)
    tracker.flush(db)
    old_seconds = used(db, f"item:{yt}", old, clock.now)
    clock += sleep
    play(db, tracker, clock, 60, 2, usage.sense_desktop)
    tracker.flush(db)
    new = rules.time_bucket("day", clock.now, db.limit_clock())
    assert new != old
    new_seconds = 118 if sleep else 58
    assert used(db, f"item:{chrome}", new, clock.now) == new_seconds
    assert used(db, f"item:{yt}", new, clock.now) == new_seconds
    assert used(db, f"item:{yt}", old, clock.now) == old_seconds + (0 if sleep else 2)
    assert reasons(db, clock.now, yt) == (["limit"] if sleep else [])
    if not sleep:
        play(db, tracker, clock, 2, 2, usage.sense_desktop)
        tracker.flush(db)
        assert used(db, f"item:{yt}", new, clock.now) == 60
        assert reasons(db, clock.now, yt) == ["limit"]


@half_open_writer
@pytest.mark.parametrize("host", ["www.youtube.com", "m.youtube.com", "music.youtube.com"])
def test_unchanged_tab_and_spa_navigation_use_the_new_day(tmp_path, monkeypatch, visible_chrome, host):
    db = Database(tmp_path / "t.db")
    db.set_setting(rules.RESET_KEY, json.dumps({"time": "03:00"}))
    yt = db.add_item("YouTube", ["youtube.com"], "site",
                     rules=[{"rule_type": "time_limit", "daily_limit_min": 1}])
    visible_chrome.bar.value = f"https://{host}/watch?v=abc"
    clock = Clock(monkeypatch, datetime(2026, 10, 3, 2, 59, 58))
    tracker = counting(clock)
    old = rules.time_bucket("day", clock.now, db.limit_clock())
    play(db, tracker, clock, 30, 2, usage.sense_desktop)
    # The single-page navigation keeps the window title and selected tab.
    visible_chrome.bar.value = f"https://{host}/watch?v=next"
    play(db, tracker, clock, 30, 2, usage.sense_desktop)
    tracker.flush(db)
    bucket = rules.time_bucket("day", clock.now, db.limit_clock())
    assert usage.sense_desktop()[1] == visible_chrome.bar.value
    assert used(db, f"item:{yt}", old, clock.now) == 2
    assert used(db, f"item:{yt}", bucket, clock.now) == 58
    assert reasons(db, clock.now, yt) == []
    play(db, tracker, clock, 2, 2, usage.sense_desktop)
    tracker.flush(db)
    assert used(db, f"item:{yt}", bucket, clock.now) == 60
    assert reasons(db, clock.now, yt) == ["limit"]


@pytest.mark.parametrize("failure", ["fullscreen", "empty"])
def test_profile_title_stays_attributed_across_reset_with_bar_unavailable(visible_chrome, monkeypatch, failure):
    clock = Clock(monkeypatch, datetime(2026, 10, 3, 2, 59, 50))
    first = visible_chrome.reader.read(HWND, visible_chrome.exe, role="foreground")
    assert first.host == "youtube.com"
    assert first.url == visible_chrome.bar.value
    visible_chrome.bar.IsOffscreen = failure == "fullscreen"
    if failure == "empty":
        visible_chrome.bar.value = ""
    clock += 12
    obs = visible_chrome.reader.read(HWND, visible_chrome.exe, role="foreground", refresh=True)
    assert (obs.host, obs.url, obs.source) == ("youtube.com", None, "confirmed_title")
    assert obs.verified_at == first.verified_at
    assert obs.actionable is (failure == "fullscreen")
    visible_chrome.title = "Unrelated title - Google Chrome"
    obs = visible_chrome.reader.read(HWND, visible_chrome.exe, role="foreground", refresh=True)
    assert obs.host is None and not obs.actionable
