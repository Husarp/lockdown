"""Synthetic YouTube title and quota regressions for the desktop."""
from datetime import timedelta

import pytest

from blocker.site_block import title_site
from test_firefox_youtube import EVENING, enforcer
from test_rebuild_dashboard import card, env


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
    assert set(enforcer.closing) == {"172.217.1.10"}
    enforcer.dns_blocks = {}
    enforcer.prune_cuts(EVENING + timedelta(seconds=1))
    assert enforcer.closing == {} and enforcer.closing_hosts == {}
    enforcer.dns_blocks = {"youtube.com": {}, "googlevideo.com": {}}
    enforcer.cut_live_connections(EVENING + timedelta(seconds=2))
    assert set(enforcer.closing) == {"172.217.1.10"}


def test_shared_ip_cut_remains_only_for_still_blocked_hosts(enforcer):
    enforcer.dns_blocks = {"youtube.com": {}, "netflix.com": {}}
    enforcer.queue_cut("1.2.3.4", "youtube.com", EVENING)
    enforcer.queue_cut("1.2.3.4", "netflix.com", EVENING)
    enforcer.dns_blocks = {"netflix.com": {}}
    enforcer.prune_cuts(EVENING)
    assert enforcer.closing_hosts == {"1.2.3.4": {"netflix.com"}}
    enforcer.prune_cuts(EVENING + timedelta(minutes=4))
    assert enforcer.closing == {}
