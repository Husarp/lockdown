"""Regressions for the round's F1 review findings."""
import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import stats
from blocker.site_block import title_site
from monitor.address import valid_address
from monitor.word_guard import tab_address


@pytest.mark.parametrize("url", ["http://127.0.0.1/porn", "http://[::1]/porn", "https://例え.テスト/porn",
                                  "https://xn--r8jz45g.xn--zckzah/porn", "http://localhost/porn"])
def test_word_guard_keeps_ip_and_international_addresses(url):
    assert valid_address(url)
    assert tab_address(90801, url, "Neutral title", "firefox.exe") == (url, False)


@pytest.mark.parametrize("exe,browser", [("firefox.exe", "Mozilla Firefox"), ("chrome.exe", "Google Chrome"),
                                        ("msedge.exe", "Microsoft Edge")])
@pytest.mark.parametrize("page", ["Wikipedia", "ウィキペディア", "ويكيبيديا", "Wikipédia"])
def test_site_marker_must_be_the_trailing_site_segment(exe, browser, page):
    assert title_site(f"YouTube - {page} — {browser}", exe) is None
    assert title_site(f"YouTube Music - {page} — {browser}", exe) is None


def test_foreground_seconds_in_detail_rollups_and_legacy_fallback():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE activity(minute, exe, site, seconds, active_seconds)")
    con.execute("CREATE TABLE daily_activity(day, exe, site, seconds, active_seconds, first_minute)")
    con.executemany("INSERT INTO activity VALUES (?, 'browser.exe', 'youtube.com', ?, ?)", [
        ("2026-10-11 09:00", 1200, 300), ("2026-10-11 09:01", None, 60), ("2026-10-11 09:02", 0, 30)])
    con.executemany("INSERT INTO daily_activity VALUES (?, 'browser.exe', 'youtube.com', ?, ?, ?)", [
        ("2026-10-10", 1800, 120, "2026-10-10 09:00"), ("2026-10-09", None, 90, "2026-10-09 09:00")])
    db = SimpleNamespace(conn=con)
    today = date(2026, 10, 11)
    end = today + timedelta(days=1)
    rows = stats.activity(db, today, end)
    assert stats.totals(rows) == (1260, 1260)
    assert stats.active_on(db, today) == 1260
    assert stats.daily_active(db, today - timedelta(days=2), end) == {
        "2026-10-09": 90, "2026-10-10": 1800, "2026-10-11": 1260}
    assert stats.app_totals(db, today - timedelta(days=2), end) == {"browser.exe": 3150}
    con.close()
