"""T1: failed address reads recover, stay bounded, and never hide limit usage."""
import types
from datetime import timedelta

import pytest

from blocker.site_block import title_site
from monitor import browser_url, usage, win, word_guard
from monitor.address import valid_address
from test_fast_tick import Clock
from test_firefox_youtube import EVENING, FIREFOX, enforcer, firefox
from test_rebuild_dashboard import card, env


@pytest.mark.parametrize("value", ["", "  ", "Search or type web address", "Wyszukaj lub wpisz adres internetowy",
                                   "Suchen oder Webadresse eingeben", "Rechercher ou saisir une adresse Web",
                                   "Buscar o escribir dirección web"])
def test_invalid_values_reacquire_instead_of_caching(value, monkeypatch):
    stale = types.SimpleNamespace(GetValuePattern=lambda: types.SimpleNamespace(Value=value))
    fresh = types.SimpleNamespace(GetValuePattern=lambda: types.SimpleNamespace(Value="https://youtube.com/"))
    browser_url._cache.clear()
    browser_url._cache[991] = stale
    monkeypatch.setattr(browser_url, "_find_address_bar", lambda hwnd: fresh)
    assert browser_url.browser_url(991) == "https://youtube.com/"
    assert browser_url._cache[991] is fresh


def test_empty_failure_and_same_title_recovery(firefox, monkeypatch):
    clock = Clock(monkeypatch)
    firefox.url = ""
    assert browser_url.browser_url(FIREFOX) is None
    assert FIREFOX not in browser_url._cache
    assert usage._read_url(browser_url, win, FIREFOX, False) == "youtube.com"
    assert word_guard.sense_tab() == (FIREFOX, "youtube.com", firefox.title, True)
    firefox.url = "https://example.com/spa"
    clock += usage.URL_REFRESH_SEC
    assert usage._read_url(browser_url, win, FIREFOX, False) == firefox.url
    assert word_guard.sense_tab()[1:4:2] == (firefox.url, False)
    assert FIREFOX not in usage._no_bar


def test_last_valid_fallback_expires_without_being_extended_by_failures(firefox, monkeypatch):
    clock = Clock(monkeypatch)
    firefox.title, firefox.url = "A clip — Mozilla Firefox", "https://example.com/video"
    assert usage._read_url(browser_url, win, FIREFOX, False) == firefox.url
    assert word_guard.sense_tab()[1] == firefox.url
    firefox.url = "  "
    for _ in range(5):
        clock += 2
        assert usage._read_url(browser_url, win, FIREFOX, False) == "https://example.com/video"
        assert word_guard.sense_tab()[1] == "https://example.com/video"
    clock += 0.5
    assert usage._read_url(browser_url, win, FIREFOX, False) is None
    assert word_guard.sense_tab()[1] is None
    # A new title naming YouTube can still attribute fullscreen playback indefinitely.
    firefox.title = "Next - YouTube — Mozilla Firefox"
    clock += 60
    assert usage._read_url(browser_url, win, FIREFOX, False) == "youtube.com"
    assert word_guard.sense_tab()[1] == "youtube.com"


def test_real_internal_navigation_replaces_last_site(firefox, monkeypatch):
    clock = Clock(monkeypatch)
    usage._read_url(browser_url, win, FIREFOX, False)
    firefox.url = "about:blank"
    clock += 2
    assert valid_address(firefox.url)
    assert usage._read_url(browser_url, win, FIREFOX, False) == "about:blank"


@pytest.mark.parametrize("extra", ["i 2 więcej stron", "i 2 inne strony", "und 2 weitere Seiten",
                                    "y 2 páginas más", "et 2 autres pages", "et 2 pages supplémentaires",
                                    "et 2 pages de plus", "y otras 2 páginas"])
@pytest.mark.parametrize("browser", ["Microsoft Edge", "Google Chrome", "Brave", "Mozilla Firefox"])
def test_localized_title_counts_and_profiles(extra, browser):
    assert title_site(f"Cats - YouTube {extra} - Osobisty - {browser}") == "youtube.com"


@pytest.mark.parametrize("suffix", ["Microsoft Edge: osobisty", "Google Chrome (navigation privée)",
                                     "Brave (ventana privada)", "Mozilla Firefox (Privater Modus)",
                                     "Mozilla Firefox (tryb prywatny)"])
def test_localized_browser_suffixes(suffix):
    assert title_site(f"Song - YouTube Music — {suffix}") == "music.youtube.com"


def test_ambiguous_profile_without_tab_count_does_not_attribute_an_earlier_segment():
    assert title_site("Cats - YouTube - Osobisty - Microsoft Edge") is None
    assert title_site("Cats - YouTube - Reddit — Mozilla Firefox") is None


def test_repeated_failure_logs_once_with_title_and_reason(firefox, monkeypatch, caplog):
    clock = Clock(monkeypatch)
    usage._url_diagnostics.clear()
    monkeypatch.setattr(win, "process_name", lambda hwnd: "firefox.exe")
    firefox.url = ""
    for _ in range(12):
        usage._read_url(browser_url, win, FIREFOX, False)
        clock += 2
    messages = [r.message for r in caplog.records if "URL read failed repeatedly" in r.message]
    assert len(messages) == 1
    assert "exe=firefox.exe" in messages[0] and "title_fallback=True" in messages[0]
    assert "reason=empty or invalid value" in messages[0]
    assert "source=title" in messages[0]
    assert f"title={firefox.title!r}" in messages[0]


def test_limit_card_and_screen_statistics_count_passive_foreground_time(env):
    from rules import time_bucket
    # Usage and activity are deliberately different, as during passive video playback.
    item = env.db.add_item("YouTube", ["youtube.com"], "site",
                           rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    now = env.state["now"]
    env.db.add_activity(f"{now.date()} 09:00", "firefox.exe", "youtube.com", 1200, 300)
    env.db.add_usage([(f"item:{item}", time_bucket("day", now))], 1200, now.date())
    row = card(env, "limits")["rows"][0]
    assert row["text"] == "20 m of 1 h 00 m" and row["left"] == "40 m left"
    assert card(env, "stats")["screen"]["sec"] == 1200
    overview = env.api.screentime_get("overview", "Today")["data"]
    assert next(c for c in overview["cards"] if c["key"] == "active")["value"] == "20 m"
    assert env.api.screentime_get("apps", "Today")["data"]["rows"][0]["time"] == "20 m"
    from rules import effective_rules
    from webapi.blocking import _rule_text
    rule = effective_rules(next(i for i in env.db.list_items() if i["id"] == item), [])[0]
    assert _rule_text(rule, now, env.db.usage_lookup(now)) == "Limit: 20m / 1h 00m today"


def test_unlock_prunes_cuts_and_reblock_can_queue_again(enforcer):
    enforcer.cut_live_connections(EVENING)
    assert "142.250.75.14" in enforcer.closing
    enforcer.dns_blocks = {}
    enforcer.prune_cuts(EVENING + timedelta(seconds=1))
    assert enforcer.closing == {} and enforcer.closing_hosts == {}
    enforcer.dns_blocks = {"youtube.com": {}}
    enforcer.cut_live_connections(EVENING + timedelta(seconds=2))
    assert "142.250.75.14" in enforcer.closing


def test_shared_ip_cut_remains_only_for_still_blocked_hosts(enforcer):
    enforcer.dns_blocks = {"youtube.com": {}, "netflix.com": {}}
    enforcer.queue_cut("1.2.3.4", "youtube.com", EVENING)
    enforcer.queue_cut("1.2.3.4", "netflix.com", EVENING)
    enforcer.dns_blocks = {"netflix.com": {}}
    enforcer.prune_cuts(EVENING)
    assert enforcer.closing_hosts == {"1.2.3.4": {"netflix.com"}}
    enforcer.prune_cuts(EVENING + timedelta(minutes=4))
    assert enforcer.closing == {}
