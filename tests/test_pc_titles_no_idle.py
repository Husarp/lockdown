"""Language-independent title attribution and passive screen-time regression tests."""
from datetime import datetime

import pytest

from blocker.site_block import title_site
from monitor.usage import UsageTracker
from test_firefox_youtube import firefox
from test_rebuild_dashboard import env


@pytest.mark.parametrize("decoration", ["i jeszcze 2 strony", "und 2 weitere Seiten", "ほか 2 ページ",
                                         "و ٢ صفحات أخرى", "и ещё 2 страницы"])
@pytest.mark.parametrize("site,host", [("YouTube", "youtube.com"), ("YouTube Music", "music.youtube.com"),
                                       ("YouTube Shorts", "youtube.com")])
def test_titles_in_any_language(decoration, site, host):
    assert title_site(f"動画 - {site} {decoration} - My renamed group - Google Chrome") == host


@pytest.mark.parametrize("exe", ["msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"])
@pytest.mark.parametrize("suffix", ["InPrivate", "シークレット", "تصفح خاص", "Приватное окно"])
def test_browser_executable_handles_private_titles_and_renamed_groups(exe, suffix):
    assert title_site(f"Clip - YouTube ほか 2 ページ - My renamed tab group - Google Chrome ({suffix})", exe) == "youtube.com"
    assert title_site(f"Song - YouTube Music - Google Chrome ({suffix})", exe) == "music.youtube.com"
    assert title_site(f"YouTuber - {suffix}", exe) is None


@pytest.mark.parametrize("idle", [0, 300, 3600, 86400])
def test_all_foreground_seconds_count_for_screen_time(idle):
    tracker = UsageTracker()
    now = datetime(2026, 11, 3, 12)
    tracker.record_activity("firefox.exe", "youtube.com", idle, now, 120)
    tracker.record_activity(None, "", idle, now, 120)  # locked: no foreground activity
    assert list(tracker.activity.values()) == [[120, 120]]


def test_private_title_fallback_reaches_both_monitors_and_tab_guard(firefox, monkeypatch):
    from monitor import browser_url, usage, win, word_guard
    from test_firefox_youtube import FIREFOX
    firefox.url = ""
    firefox.title = "動画 - YouTube Music、ほか2ページ - Renamed group - Mozilla Firefox (プライベート)"
    monkeypatch.setattr(win, "process_name", lambda hwnd: "firefox.exe")
    assert usage._read_url(browser_url, win, FIREFOX, False) == "music.youtube.com"
    assert usage._read_url(browser_url, win, FIREFOX, True) == "music.youtube.com"
    assert word_guard.sense_tab()[1] == "music.youtube.com"
    firefox.url = "https://example.com/"
    # A real address always wins over title text.
    assert word_guard.sense_tab()[1] == "https://example.com/"


def test_passive_playback_flows_into_all_statistics(env):
    from datetime import timedelta
    import stats
    from test_rebuild_dashboard import card
    tracker = UsageTracker()
    now = env.state["now"]
    start = now.replace(hour=9, minute=0)
    for minute in range(20):
        tracker.record_activity("firefox.exe", "youtube.com", 3600, start + timedelta(minutes=minute), 60)
    tracker.flush(env.db)
    rows = stats.activity(env.db, now.date(), now.date() + timedelta(days=1))
    assert stats.totals(rows) == (1200, 1200)
    assert stats.per_app(rows)["firefox.exe"] == 1200
    assert stats.per_site(rows)["youtube.com"] == 1200
    assert stats.daily_active(env.db, now.date(), now.date() + timedelta(days=1))[str(now.date())] == 1200
    assert stats.sessions(rows) == [(start, start + timedelta(minutes=20))]
    assert stats.longest_focus(rows)[0] == 1200
    assert card(env, "stats")["screen"]["sec"] == 1200
    overview = env.api.screentime_get("overview", "Today")["data"]
    assert next(c for c in overview["cards"] if c["key"] == "active")["value"] == "20 m"
    assert all(c["key"] != "idle" for c in overview["cards"])


def test_japanese_tab_count_without_word_spaces():
    assert title_site("動画 - YouTube、ほか2ページ - My group - Microsoft Edge") == "youtube.com"
