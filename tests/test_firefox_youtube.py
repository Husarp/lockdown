"""YouTube in Firefox kept playing after its block began (0.84.9).

Adam watches YouTube in Firefox. Its time was counted and the block did start (21:15, his group's 15 allowed
minutes spent), but a video that was already playing went on to the end:

- the tab check couldn't see a full-screen video: the address bar is hidden, and it had no fallback (the tracker
  keeps the last address, the tab check didn't) - and autoplay moving on to the next video left even the
  tracker without a site;
- going back on a YouTube video lands on the previous video, still YouTube, so it hopped through the history;
- the open video connections were cut every 2 s, but the browser reconnected at once (its own DNS cache, video
  hosts the hosts file can't name) and IPv6 ones can't be cut at all.

The Firefox window below is a fake UI Automation tree: the address bar is the ComboBox "urlbar-input"
(browser_url._find_address_bar), and it is gone while a video plays full screen."""
import json
import types
from datetime import datetime

import pytest

import backup
import service
from blocker import firewall, netlog
from blocker.site_block import title_site
from db import Database
from monitor import browser_url, usage as usage_mod, win, word_guard
from test_usage_counting import Clock, counting, reasons, _youtube_group

OFF = {"enabled": False, "action": "close"}          # bad-word checking off: only blocked sites
YT = {"youtube.com": "back", "googlevideo.com": "back"}
FIREFOX = 77


class FakeFirefox:
    """A Firefox window as UI Automation shows it."""
    ClassName = "MozillaWindowClass"

    def __init__(self, url="https://www.youtube.com/watch?v=abc", title="Cats - YouTube — Mozilla Firefox"):
        self.url, self.title, self.full_screen = url, title, False

    def ComboBoxControl(self, AutomationId=None, searchDepth=None):
        assert AutomationId == "urlbar-input"
        return FakeUrlBar(self)


class FakeUrlBar:
    def __init__(self, window):
        self.window = window

    def Exists(self, *_a):
        return not self.window.full_screen       # full screen: the toolbar and its address bar are gone

    def GetValuePattern(self):
        if self.window.full_screen:
            raise RuntimeError("element not available")
        return types.SimpleNamespace(Value=self.window.url)


@pytest.fixture
def firefox(monkeypatch):
    ff = FakeFirefox()
    monkeypatch.setattr(browser_url.auto, "ControlFromHandle", lambda hwnd: ff if hwnd == FIREFOX else None,
                        raising=False)
    monkeypatch.setattr(win, "foreground", lambda: (FIREFOX, "firefox.exe"))
    monkeypatch.setattr(win, "window_title", lambda hwnd: ff.title if hwnd == FIREFOX else "")
    browser_url._cache.clear()
    word_guard._bar.clear()
    usage_mod._seen.clear()
    usage_mod._no_bar.clear()
    return ff


class Acts:
    def __init__(self):
        self.done = []

    def __call__(self, hwnd, action):
        self.done.append(action)
        return True


# ---------- the site a title names, when the address bar can't be read ----------

@pytest.mark.parametrize("title, site", [
    ("Cats - YouTube — Mozilla Firefox", "youtube.com"),
    ("(3) Cats - YouTube — Mozilla Firefox", "youtube.com"),                     # unread notifications
    ("YouTube — Mozilla Firefox", "youtube.com"),                                # the home page
    ("Cats - YouTube — Mozilla Firefox Private Browsing", "youtube.com"),        # a private window
    ("Cats - YouTube — Private Browsing", "youtube.com"),
    ("Cats - YouTube — Firefox Developer Edition", "youtube.com"),
    ("Song - YouTube Music — Mozilla Firefox", "music.youtube.com"),
    ("Cats - YouTube - Google Chrome", "youtube.com"),
    ("Cats - YouTube - Brave", "youtube.com"),
    ("Cats - YouTube - Microsoft​ Edge", "youtube.com"),
    ("Cats - YouTube and 2 more pages - Personal - Microsoft​ Edge", "youtube.com"),
    ("YouTube", "youtube.com"),                                                  # an installed YouTube web app
    ("Why YouTube is bad - Reddit — Mozilla Firefox", None),
    ("Cats - YouTube - Reddit — Mozilla Firefox", None),                         # a Reddit post's title
    ("Mozilla Firefox", None),
    ("", None),
    (None, None),
])
def test_the_site_a_browser_title_names(title, site):
    assert title_site(title) == site


# ---------- the tab check ----------

def test_a_full_screen_youtube_video_in_firefox_is_closed_when_its_block_begins(firefox):
    """Adam's case: watching full screen when the allowance ran out. The tab check read no address (None) and did
    nothing. The title still says YouTube: the tab is closed (going back would only hop to the video before)."""
    firefox.full_screen = True
    assert browser_url.browser_url(FIREFOX) is None                        # the address bar really is gone
    acts, guard = Acts(), word_guard.WordGuard(lambda *a: None)
    guard.tick(OFF, word_guard.sense_tab, acts, 0.0, {})                    # not blocked yet: left alone
    guard.tick(OFF, word_guard.sense_tab, acts, 0.5, YT)                    # the block begins
    assert acts.done == ["close"]


def test_a_full_screen_video_keeps_the_address_last_read_in_its_window(firefox, monkeypatch):
    """A blocked site the title doesn't name: the address read before the video went full screen still counts
    while the title is the same, as in the tracker (usage._read_url)."""
    firefox.url, firefox.title = "https://www.reddit.com/r/videos/x", "A clip : videos — Mozilla Firefox"
    sites = {"example.org": "back"}
    acts, guard = Acts(), word_guard.WordGuard(lambda *a: None)
    guard.tick(OFF, word_guard.sense_tab, acts, 0.0, sites)                 # read while windowed: not blocked
    firefox.full_screen = True
    guard.tick(OFF, word_guard.sense_tab, acts, 0.5, {**sites, "reddit.com": "back"})
    assert acts.done == ["close"]
    assert word_guard.sense_tab() == (FIREFOX, "https://www.reddit.com/r/videos/x", firefox.title, True)


def test_autoplay_in_full_screen_is_still_youtube_to_the_tab_check(firefox):
    firefox.full_screen = True
    word_guard.sense_tab()
    firefox.title = "Next video - YouTube — Mozilla Firefox"               # the next one starts
    assert word_guard.sense_tab() == (FIREFOX, "youtube.com", firefox.title, True)


def test_a_full_screen_video_in_a_private_window_is_closed(firefox):
    firefox.full_screen, firefox.title = True, "Cats - YouTube — Mozilla Firefox Private Browsing"
    acts = Acts()
    word_guard.WordGuard(lambda *a: None).tick(OFF, word_guard.sense_tab, acts, 0.0, YT)
    assert acts.done == ["close"]


def test_a_windowed_youtube_tab_in_firefox_is_still_sent_back_first(firefox):
    acts = Acts()
    word_guard.WordGuard(lambda *a: None).tick(OFF, word_guard.sense_tab, acts, 0.0, YT)
    assert word_guard.sense_tab() == (FIREFOX, firefox.url, firefox.title, False)
    assert acts.done == ["back"]


def test_a_full_screen_youtube_video_is_left_alone_while_youtube_is_allowed(firefox):
    firefox.full_screen = True
    acts = Acts()
    word_guard.WordGuard(lambda *a: None).tick(OFF, word_guard.sense_tab, acts, 0.0, {"reddit.com": "back"})
    assert acts.done == []


def test_going_back_that_lands_on_youtube_again_closes_the_tab_at_once(firefox):
    """Back on a YouTube video is the video before - still YouTube. Each new page counted as a new find, so it went
    back again and again while the video played."""
    acts, guard = Acts(), word_guard.WordGuard(lambda *a: None)
    guard.tick(OFF, word_guard.sense_tab, acts, 0.0, YT)
    firefox.url, firefox.title = "https://www.youtube.com/watch?v=before", "Before - YouTube — Mozilla Firefox"
    guard.tick(OFF, word_guard.sense_tab, acts, 0.5, YT)                    # no waiting for RETRY_SEC
    assert acts.done == ["back", "close"]


def test_going_back_that_leaves_the_site_does_nothing_more(firefox):
    acts, guard = Acts(), word_guard.WordGuard(lambda *a: None)
    guard.tick(OFF, word_guard.sense_tab, acts, 0.0, YT)
    firefox.url, firefox.title = "https://www.google.com/search?q=cats", "cats - Google Search — Mozilla Firefox"
    guard.tick(OFF, word_guard.sense_tab, acts, 0.5, YT)
    firefox.url, firefox.title = "https://www.youtube.com/", "YouTube — Mozilla Firefox"    # opened again later
    guard.tick(OFF, word_guard.sense_tab, acts, 30.0, YT)
    assert acts.done == ["back", "back"]


def test_closing_a_firefox_tab_is_ctrl_w(firefox, monkeypatch):
    pressed = []
    monkeypatch.setattr(win, "chord", lambda hwnd, *vks: pressed.append(vks) or True)
    assert word_guard.act(FIREFOX, "close")
    assert pressed == [(win.VK_CONTROL, win.VK_W)]


# ---------- counting ----------

def test_autoplay_in_full_screen_still_counts_as_youtube(firefox):
    assert usage_mod._read_url(browser_url, win, FIREFOX, False) == firefox.url
    firefox.full_screen = True
    firefox.title = "Next video - YouTube — Mozilla Firefox"
    assert usage_mod._read_url(browser_url, win, FIREFOX, False) == "youtube.com"


def test_a_full_screen_firefox_evening_is_counted_blocked_and_closed(tmp_path, monkeypatch, firefox):
    """Adam's setup (group blocks 21:00-05:00 with 15 minutes allowed, 2 h a day; YouTube 1 h a day), a
    playlist in full screen in Firefox from 21:00 - its address bar never readable, a new video every 4 minutes.
    It used to count nothing (no address ever read): no block at all, and nothing for the tab check to see.
    (The group sends its sites back here: one that is only "can't load" is left to the network - 0.84.11,
    test_group_block_ways.py.)"""
    db = Database(tmp_path / "t.db")
    yt, _gid = _youtube_group(db)
    group = db.list_groups()[0]
    db.update_group(_gid, group["name"], group["rules"], group["members"], ways={"site_block": "dns,back"})
    clock = Clock(monkeypatch, datetime(2026, 10, 2, 21, 0))
    monkeypatch.setattr(word_guard, "now_from_db", lambda d: clock.now)
    tracker = counting(clock)
    firefox.full_screen = True

    def sense():
        return "firefox.exe", usage_mod._read_url(browser_url, win, FIREFOX, False), 30 * 60
    for minute in range(16):
        firefox.title = f"Video {minute // 4} - YouTube — Mozilla Firefox"
        for _ in range(30):
            clock += 2
            tracker.tick(db, sense, running_exes=lambda: {"firefox.exe"})
    assert clock.now == datetime(2026, 10, 2, 21, 16)
    assert reasons(db, clock.now, yt) == ["schedule"]                       # the 15 minutes are spent
    sites = word_guard.blocked_sites(db)
    assert sites["youtube.com"] == "back"
    acts = Acts()
    word_guard.WordGuard(lambda *a: None).tick(OFF, word_guard.sense_tab, acts, 0.0, sites)
    assert acts.done == ["close"]


# ---------- the network: the video hosts' addresses are firewalled ----------

EVENING = datetime(2026, 10, 2, 21, 16)
CACHE = {"172.217.1.10": "rr3---sn-u2oxu-f5fed.googlevideo.com",
         "2a00:1450:4001:1::a": "rr3---sn-u2oxu-f5fed.googlevideo.com",   # IPv6: can't be cut, only firewalled
         "142.250.75.14": "www.youtube.com",                              # Google's front ends: search and Gmail too
         "198.38.96.1": "ipv4-c001-waw001-ix.1.oca.nflxvideo.net",
         "93.184.216.34": "example.com",
         "127.0.0.1": "rr1---sn-x.googlevideo.com"}                       # (the hosts file's own answer)


@pytest.fixture
def enforcer(tmp_path, monkeypatch):
    e = object.__new__(service.Enforcer)
    e.db, e.closing, e.was_off = Database(tmp_path / "t.db"), {}, False
    e.protection = types.SimpleNamespace(which=lambda host: None)
    e.dns_blocks = {"youtube.com": {}, "googlevideo.com": {}}
    e.rule = None
    monkeypatch.setattr(firewall, "set_video_block", lambda ips: setattr(e, "rule", list(ips)) or True)
    monkeypatch.setattr(firewall, "remove_video_block", lambda: setattr(e, "rule", "removed") or True)
    monkeypatch.setattr(netlog, "dns_names", lambda: dict(CACHE))
    return e


def test_a_blocked_video_host_is_firewalled_ipv4_and_ipv6(enforcer):
    enforcer.cut_live_connections(EVENING)
    enforcer.update_video_block()
    assert enforcer.rule == ["172.217.1.10", "2a00:1450:4001:1::a"]
    assert "142.250.75.14" in enforcer.closing                    # youtube.com's own: only cut, as before
    assert json.loads(enforcer.db.get_setting(service.VIDEO_KEY)) == {
        "172.217.1.10": "rr3---sn-u2oxu-f5fed.googlevideo.com",
        "2a00:1450:4001:1::a": "rr3---sn-u2oxu-f5fed.googlevideo.com"}
    enforcer.rule = None
    enforcer.cut_live_connections(EVENING)
    enforcer.update_video_block()
    assert enforcer.rule is None                                  # nothing new: the rule isn't rewritten


def test_the_video_host_seen_before_the_flush_is_firewalled(enforcer, monkeypatch):
    """When the block begins, the playing video's host is only in the DNS cache until the hosts rewrite flushes
    it (close_cached); afterwards the cache answers 127.0.0.1 for it."""
    enforcer.close_cached(["youtube.com", "googlevideo.com"], EVENING)
    monkeypatch.setattr(netlog, "dns_names", lambda: {"127.0.0.1": "rr3---sn-u2oxu-f5fed.googlevideo.com"})
    enforcer.cut_live_connections(EVENING)
    enforcer.update_video_block()
    assert enforcer.rule == ["172.217.1.10", "2a00:1450:4001:1::a"]


def test_a_video_host_gone_from_the_dns_cache_is_firewalled_from_the_network_log(enforcer, monkeypatch):
    """A video playing for over half an hour: its host's DNS entry ran out (or another block's flush took it) while
    the stream stays open - the network log still remembers it. Only video hosts are taken from there."""
    monkeypatch.setattr(netlog, "dns_names", lambda: {})
    enforcer.net_names = {"172.217.9.9": "rr5---sn-u2oxu-f5fed.googlevideo.com", "142.250.75.99": "youtube.com"}
    enforcer.close_cached(["youtube.com", "googlevideo.com"], EVENING)
    enforcer.update_video_block()
    assert enforcer.rule == ["172.217.9.9"]
    assert "142.250.75.99" not in enforcer.closing                # (an old youtube.com address may be Gmail's now)


def test_the_rule_goes_when_the_block_ends(enforcer):
    enforcer.cut_live_connections(EVENING)
    enforcer.update_video_block()
    enforcer.dns_blocks = {}                                      # 05:00 (or paused, or Lockdown switched off)
    enforcer.cut_live_connections(EVENING)
    enforcer.update_video_block()
    assert enforcer.rule == "removed" and json.loads(enforcer.db.get_setting(service.VIDEO_KEY)) == {}


def test_youtube_s_video_hosts_leave_the_rule_when_only_netflix_stays_blocked(enforcer):
    enforcer.dns_blocks = {"youtube.com": {}, "googlevideo.com": {}, "netflix.com": {}, "nflxvideo.net": {}}
    enforcer.cut_live_connections(EVENING)
    enforcer.update_video_block()
    assert enforcer.rule == ["172.217.1.10", "198.38.96.1", "2a00:1450:4001:1::a"]
    enforcer.dns_blocks = {"netflix.com": {}, "nflxvideo.net": {}}
    enforcer.cut_live_connections(EVENING)
    enforcer.update_video_block()
    assert enforcer.rule == ["198.38.96.1"]


def test_a_restart_rebuilds_the_rule_only_for_hosts_still_blocked(enforcer):
    """main() takes the rule down and hands the saved addresses to the first pass (video_seen)."""
    enforcer.video_ips = {}
    enforcer.video_seen = {"172.217.1.10": "rr3---sn-u2oxu-f5fed.googlevideo.com",
                           "198.38.96.1": "ipv4-c001-waw001-ix.1.oca.nflxvideo.net"}
    enforcer.update_video_block()
    assert enforcer.rule == ["172.217.1.10"]


def test_the_dns_filter_is_used_while_a_site_is_blocked():
    off = {"search": False, "youtube": False}
    e = types.SimpleNamespace(protection=types.SimpleNamespace(count=lambda: 0), dns_blocks={})
    assert not service.filter_wanted(e, off)
    e.dns_blocks = {"googlevideo.com": {}}
    assert service.filter_wanted(e, off)


def test_the_video_rule_is_not_part_of_a_backup():
    assert service.VIDEO_KEY in backup.RUNTIME_KEYS


def test_only_video_hosts_are_firewalled():
    assert firewall.is_video_host("rr3---sn-u2oxu-f5fed.googlevideo.com")
    assert firewall.is_video_host("GOOGLEVIDEO.COM.")
    assert not firewall.is_video_host("www.youtube.com")
    assert not firewall.is_video_host("notgooglevideo.com")
