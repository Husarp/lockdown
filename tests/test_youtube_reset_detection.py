"""Exercise browser discovery, not an injected URL, across the limit-day boundary."""
import json
import types
from datetime import datetime

import pytest

import rules
from db import Database
from monitor import browser_url, usage, win, word_guard
from test_fast_tick import Clock
from test_usage_counting import counting, play, reasons, used


@pytest.fixture
def browser(monkeypatch):
    state = types.SimpleNamespace(url="https://www.youtube.com/watch?v=abc", hidden=False,
                                  title="Cats - YouTube - Personal - Google Chrome")

    class Bar:
        ClassName = "OmniboxViewViews"

        def Exists(self, *_args):
            return not state.hidden

        def GetValuePattern(self):
            if state.hidden:
                raise RuntimeError("element not available")
            return types.SimpleNamespace(Value=state.url)

    class Window:
        ClassName = "Chrome_WidgetWin_1"

        def EditControl(self, searchDepth, Compare):
            bar = Bar()
            assert Compare(bar, 1)
            return bar

    monkeypatch.setattr(browser_url.auto, "ControlFromHandle", lambda hwnd: Window(), raising=False)
    monkeypatch.setattr(win, "foreground_process", lambda: (77, "chrome.exe", ""))
    monkeypatch.setattr(win, "foreground", lambda: (77, "chrome.exe"))
    monkeypatch.setattr(win, "front_windows", lambda: [])
    monkeypatch.setattr(win, "window_title", lambda hwnd: state.title)
    monkeypatch.setattr(win, "window_pid", lambda hwnd: 123)
    monkeypatch.setattr(win, "session_locked", lambda: False)
    monkeypatch.setattr(win, "process_name", lambda hwnd: "chrome.exe")
    for cache in (browser_url._cache, usage._seen, usage._no_bar, usage._url_diagnostics, word_guard._bar):
        cache.clear()
    return state


@pytest.mark.parametrize("sleep", [0, 8 * 3600])
@pytest.mark.parametrize("failure", ["fullscreen", "empty"])
def test_confirmed_youtube_with_profile_keeps_counting_after_3am(tmp_path, monkeypatch, browser, sleep, failure):
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
    # No URL or title change: fullscreen hides the bar, or UIA starts returning empty.
    browser.hidden = failure == "fullscreen"
    if failure == "empty":
        browser.url = ""
    play(db, tracker, clock, 16, 2, usage.sense_desktop)
    tracker.flush(db)
    old_seconds = used(db, f"item:{yt}", old, clock.now)
    clock += sleep
    play(db, tracker, clock, 60, 2, usage.sense_desktop)
    tracker.flush(db)
    new = rules.time_bucket("day", clock.now, db.limit_clock())
    assert new != old
    assert used(db, f"item:{chrome}", new, clock.now) == (118 if sleep else 60)
    assert used(db, f"item:{yt}", new, clock.now) == (118 if sleep else 60)
    assert used(db, f"item:{yt}", old, clock.now) == old_seconds
    assert reasons(db, clock.now, yt) == ["limit"]


@pytest.mark.parametrize("host", ["www.youtube.com", "m.youtube.com", "music.youtube.com"])
def test_unchanged_tab_and_spa_navigation_use_the_new_day(tmp_path, monkeypatch, browser, host):
    db = Database(tmp_path / "t.db")
    db.set_setting(rules.RESET_KEY, json.dumps({"time": "03:00"}))
    yt = db.add_item("YouTube", ["youtube.com"], "site",
                     rules=[{"rule_type": "time_limit", "daily_limit_min": 1}])
    browser.url = f"https://{host}/watch?v=abc"
    clock = Clock(monkeypatch, datetime(2026, 10, 3, 2, 59, 58))
    tracker = counting(clock)
    play(db, tracker, clock, 30, 2, usage.sense_desktop)
    # YouTube changes its URL without changing its window title or reloading the page.
    browser.url = f"https://{host}/watch?v=next"
    play(db, tracker, clock, 30, 2, usage.sense_desktop)
    tracker.flush(db)
    bucket = rules.time_bucket("day", clock.now, db.limit_clock())
    assert usage.sense_desktop()[1] == browser.url
    assert used(db, f"item:{yt}", bucket, clock.now) == 60
    assert reasons(db, clock.now, yt) == ["limit"]


def test_confirmed_title_fallback_also_enforces_the_tab_limit(browser, monkeypatch):
    clock = Clock(monkeypatch)
    assert word_guard.sense_tab()[1] == browser.url
    browser.hidden = True
    clock += 20
    assert word_guard.sense_tab()[1] == "youtube.com"
    acts = []
    guard = word_guard.WordGuard(lambda *args: None)
    guard.tick({"enabled": False}, word_guard.sense_tab,
               lambda hwnd, action: acts.append((hwnd, action)) or True, clock.mono, {"youtube.com": "back"})
    assert acts == [(77, "close")]


@pytest.mark.parametrize("change", ["title", "address", "internal", "away_and_back", "empty_title"])
def test_confirmation_cannot_survive_a_different_page(browser, monkeypatch, change):
    clock = Clock(monkeypatch)
    usage.sense_desktop()
    word_guard.sense_tab()
    if change in {"title", "away_and_back", "empty_title"}:
        original = browser.title
        browser.hidden = True
        browser.title = "" if change == "empty_title" else "Other page - Google Chrome"
        clock += 20
        assert usage.sense_desktop()[1] is None
        assert word_guard.sense_tab()[1] is None
        if change == "away_and_back":
            browser.title = original
    else:
        browser.url = "https://example.com/" if change == "address" else "about:blank"
        clock += 2
        assert usage.sense_desktop()[1] == browser.url
        assert word_guard.sense_tab()[1] == browser.url
        browser.hidden = True
    clock += 20
    assert usage.sense_desktop()[1] is None
    assert word_guard.sense_tab()[1] is None


def test_unverified_ambiguous_title_still_does_not_count(browser, monkeypatch):
    clock = Clock(monkeypatch)
    browser.hidden = True
    for _ in range(15):
        clock += 2
        assert usage.sense_desktop()[1] is None
        assert word_guard.sense_tab()[1] is None


def test_an_expired_cached_uia_control_is_reacquired(browser):
    class StaleBar:
        def GetValuePattern(self):
            raise RuntimeError("element not available after sleep")

    browser_url._cache[77] = StaleBar()
    assert usage.sense_desktop()[1] == browser.url
    assert word_guard.sense_tab()[1] == browser.url


@pytest.mark.parametrize("failure,reason", [("fullscreen", "address bar unavailable"),
                                          ("empty", "empty or invalid value"),
                                          ("error", "UI Automation error (RuntimeError)")])
def test_prolonged_fallback_logs_title_reason_and_recovers(browser, monkeypatch, caplog, failure, reason):
    clock = Clock(monkeypatch)
    usage.sense_desktop()
    find_bar = browser_url._find_address_bar
    if failure == "fullscreen":
        browser.hidden = True
    elif failure == "empty":
        browser.url = ""
    else:
        def unavailable(hwnd):
            raise RuntimeError("UIA unavailable")
        browser_url._cache.clear()
        monkeypatch.setattr(browser_url, "_find_address_bar", unavailable)
    for _ in range(30):
        clock += 2
        usage.sense_desktop()
    messages = [r.message for r in caplog.records if "Browser URL read failed repeatedly" in r.message]
    assert len(messages) == 1
    assert "exe=chrome.exe" in messages[0]
    assert f"reason={reason}" in messages[0]
    assert "source=confirmed_title" in messages[0]
    assert f"title={browser.title!r}" in messages[0]
    clock += 22
    usage.sense_desktop()
    assert len([r for r in caplog.records if "Browser URL read failed repeatedly" in r.message]) == 2
    if failure == "error":
        monkeypatch.setattr(browser_url, "_find_address_bar", find_bar)
    browser.hidden, browser.url = False, "https://example.com/"
    clock += 2
    assert usage.sense_desktop()[1] == browser.url
    assert 77 not in usage._no_bar
