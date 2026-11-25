"""How a blocked website is blocked: sent nowhere (the hosts file / DNS filter), or its tab closed / sent back
by the tray agent - or both."""
import pytest

import block_method
from antibypass import item_looser
from blocker.site_block import blocks_dns, make_site_block_type, site_flags, tab_action, text
from monitor.word_guard import WordGuard, site_action
from datetime import datetime
from test_rebuild_add_edit import PERMANENT, edit, env, items, lock, new  # noqa: F401

NOW = datetime(2026, 9, 21, 12, 0)


def test_what_a_site_did_before_this_existed():
    """Every site saved before 0.66 has no block type at all - it must still be sent nowhere."""
    assert site_flags(None) == {"dns"}
    assert blocks_dns(None) and tab_action(None) is None


@pytest.mark.parametrize("block_type, dns, action", [
    ("dns", True, None),
    ("close", False, "close"),
    ("back", False, "back"),
    ("dns,close", True, "close"),
    ("dns,back", True, "back"),
    ("minimize", False, None),
    ("dns,minimize", True, None),
    ("close,minimize", False, "close"),
    ("", True, None),               # nothing readable: back to "can't load"
    ("nonsense", True, None),
])
def test_the_flags_of_a_site(block_type, dns, action):
    assert blocks_dns(block_type) is dns
    assert tab_action(block_type) == action


def test_how_it_reads_on_the_list():
    assert text("dns,close") == "can't load + closes the tab"
    assert text("back") == "goes back"


def test_make_keeps_the_order():
    assert make_site_block_type({"close", "dns"}) == "dns,close"


def _site(block_type):
    return {"id": 1, "display_name": "YouTube", "item_type": "site", "target": "youtube.com m.youtube.com",
            "block_type": block_type, "app_path": None, "notify": None, "disabled": 0,
            "rules": [{"rule_type": "permanent"}]}


def test_weakening_how_a_site_is_blocked_needs_the_challenge():
    assert item_looser(_site("dns,close"), _site("dns"), NOW)        # dropped "close the tab"
    assert item_looser(_site("dns"), _site("close"), NOW)            # it can load again
    assert not item_looser(_site("dns"), _site("dns,close"), NOW)    # adding one is free


@pytest.mark.parametrize("old, new_way, weaker", [
    ("back", "close", False),
    ("close", "back", True),
    ("minimize", "close", True),
    ("close", "minimize", True),
    ("back,minimize", "close,minimize", False),
    ("dns,minimize", "close,minimize", True),
    ("dns,close,minimize", "dns,close", True),
    ("dns,close", "dns,close,minimize", False),
])
def test_site_ways_compare_foreground_dns_and_background_coverage(old, new_way, weaker):
    assert item_looser(_site(old), _site(new_way), NOW) is weaker


def test_site_coverage_includes_all_matching_suffixes():
    ways = {"example.org": "dns,close", "video.example.org": "back,minimize"}
    assert block_method.merge("site", *block_method.matching_sites("m.video.example.org", ways).values()) == (
        "dns,close,minimize")
    assert block_method.matching_sites("notexample.org", ways) == {}
    assert not block_method.site_coverage_looser({"video.example.org": "back,minimize"},
                                                {"example.org": "close,minimize"})
    assert block_method.site_coverage_looser(ways, {"example.org": "dns,close"})


def test_direct_site_editor_saves_minimize_and_guards_its_removal(env):
    new(env, {"kind": "site", "target": "example.org", "name": "Example", "ways": ["minimize"], "rules": [PERMANENT]})
    item = items(env)["Example"]
    assert item["block_type"] == "minimize"
    lock(env)
    edit(env, item["id"], {"ways": ["dns", "minimize"], "rules": [PERMANENT]})
    assert items(env)["Example"]["block_type"] == "dns,minimize"
    assert text("dns,minimize") == "can't load + minimizes the window"
    assert edit(env, item["id"], {"ways": ["dns", "close"], "rules": [PERMANENT]})["needs_challenge"]
    assert items(env)["Example"]["block_type"] == "dns,minimize"


# ---------- the tray agent acting on the tab ----------

SITES = {"youtube.com": "close", "reddit.com": "back"}


@pytest.mark.parametrize("url, action", [
    ("https://youtube.com/watch?v=x", "close"),
    ("https://www.youtube.com/", "close"),
    ("https://music.youtube.com/", "close"),       # below a blocked hostname counts too
    ("https://old.reddit.com/r/x", "back"),
    ("https://example.com/", None),
    ("not a url", None),
    (None, None),
])
def test_which_tabs_are_acted_on(url, action):
    assert site_action(url, SITES) == action


class Tab:
    """The browser tab in front, and what was done to it."""

    def __init__(self, url):
        self.url, self.done = url, []

    def sense(self):
        return (1, self.url, "a title")

    def act(self, hwnd, action):
        self.done.append(action)
        return True


OFF = {"enabled": False, "action": "close"}


def test_a_blocked_site_closes_its_tab_even_with_word_checking_off():
    guard, tab = WordGuard(lambda *a: None), Tab("https://youtube.com/")
    guard.tick(OFF, tab.sense, tab.act, 0.0, SITES)
    assert tab.done == ["close"]


def test_going_back_becomes_closing_when_the_page_stays():
    guard, tab = WordGuard(lambda *a: None), Tab("https://reddit.com/")
    guard.tick(OFF, tab.sense, tab.act, 0.0, SITES)
    guard.tick(OFF, tab.sense, tab.act, 0.5, SITES)    # too soon: give the browser a moment
    guard.tick(OFF, tab.sense, tab.act, 5.0, SITES)
    assert tab.done == ["back", "close"]


def test_other_sites_are_left_alone():
    guard, tab = WordGuard(lambda *a: None), Tab("https://example.com/")
    guard.tick(OFF, tab.sense, tab.act, 0.0, SITES)
    assert tab.done == []
