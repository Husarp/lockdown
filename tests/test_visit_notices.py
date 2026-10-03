"""0.84.10: "YouTube is blocked" only when you try to open YouTube.

Adam (0.84.9): "I get notifications about YouTube being blocked when I don't have it open and am not doing anything
with it." The service's blocked-visit listener (127.0.0.1:80/443, where the hosts file and the DNS filter send a
blocked site) records every connection to a blocked site, and the tray turned every one into a notice (once per
cooldown). Most are nobody opening YouTube: a page embedding a video (www.youtube.com/embed), Discord's link
previews (youtu.be), a browser's preconnect, Steam or a widget - and since 0.84.9 the DNS filter answers for
youtube.com / googlevideo.com whatever asks. Now a site's notice needs a browser window in front (focused, or in
front on another monitor) showing that site around the time of the visit; every hit is still recorded.

These drive the real path: the service's Enforcer.on_visit (what the listener calls) writes the block events, the
tray's UsageTracker.tick and WordGuard.tick see the windows in front, and LockdownApp._poll_block_events /
_check_visits / _alert decide - only Windows (the windows in front, the browser's address bar) is faked."""
import time
import types
from datetime import timedelta

import pytest

from db import Database
from monitor import usage as usage_mod
from monitor.word_guard import WordGuard
from tests.test_app_limit_enforcement import FRIDAY, World, at

YOUTUBE = ["youtube.com", "m.youtube.com", "youtu.be", "googlevideo.com"]   # the popular-sites target
FIREFOX = r"C:\Program Files\Mozilla Firefox\firefox.exe"
WORDS_OFF = {"enabled": False, "action": "close"}


class Tray:
    """The tray agent's blocked-visit side: the real LockdownApp methods on a stand-in, its own connection."""

    def __init__(self, w: World, monkeypatch):
        from gui import app as appmod
        self.w = w
        self.db = Database(w.path)
        self.shown: list[str] = []
        self.closed: list[str] = []
        self.last_event_id = self.db.last_block_event_id()
        self.visits = {}
        self.last_alert = {}
        self._ALERT_KEY = appmod.LockdownApp._ALERT_KEY
        self.after = lambda *a: None
        self._show = lambda message, **k: self.shown.append(message)
        for name in ("_poll_block_events", "_check_visits", "_alert", "_save_last_alert"):
            setattr(self, name, types.MethodType(getattr(appmod.LockdownApp, name), self))
        monkeypatch.setattr(appmod, "now_from_db", lambda db: w.now)
        monkeypatch.setattr(appmod.win, "close_app", lambda exe: self.closed.append(exe))
        self.tracker = usage_mod.UsageTracker()

    def front(self, url: str | None, exe: str = "firefox.exe", others=()):
        """One tick of the tray's tracker with this in front (a browser on `url`)."""
        self.tracker.tick(self.db, lambda: (exe, url, 0.0, FIREFOX if url else None, 4242, list(others)),
                          running_exes=lambda: {exe})

    def poll(self, seconds: float = 1):
        self.w.ts += seconds
        self._poll_block_events()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_mod, "_shown", {})
    w = World(tmp_path, monkeypatch, at(FRIDAY, "21:30"))
    monkeypatch.setattr(time, "monotonic", lambda: w.ts)   # the tray's clocks move with the world's
    w.youtube = w.gui.add_item("YouTube", YOUTUBE, "site", rules=[{"rule_type": "permanent"}])
    w.step()
    assert w.enforcer.blocks["youtube.com"]["item"]["id"] == w.youtube
    w.tray = Tray(w, monkeypatch)
    return w


def visit(w: World, *hosts: str):
    """Connections to these names reach the listener (sent there by the hosts file / the DNS filter)."""
    for host in hosts:
        w.ts += 0.2
        w.enforcer.on_visit(host)


def recorded(w: World) -> list[str]:
    return [e["hostname"] for e in w.gui.block_events_since(w.now - timedelta(hours=1))]


# ---------------------------------------------------------------- background hits: recorded, silent

def test_background_embed_thumbnail_and_api_hits_say_nothing(world):
    """Nothing on screen is YouTube: an embed, Discord's link preview, thumbnails, the API, the bare video host."""
    w = world
    w.tray.front(None, exe="discord.exe")
    visit(w, "www.youtube.com", "youtu.be", "i.ytimg.com", "youtubei.googleapis.com", "googlevideo.com",
          "m.youtube.com")
    for _ in range(15):
        w.tray.poll()
        w.tray.front(None, exe="discord.exe")
    assert w.tray.shown == []
    assert sorted(recorded(w)) == ["googlevideo.com", "m.youtube.com", "www.youtube.com", "youtu.be"]   # still logged
    assert w.tray.visits == {}                                                    # and forgotten, not piling up


def test_a_page_in_front_embedding_a_youtube_video_says_nothing(world):
    w = world
    w.tray.front("https://www.bbc.co.uk/news/articles/c0abc")
    visit(w, "www.youtube.com", "googlevideo.com")
    for _ in range(12):
        w.tray.poll()
        w.tray.front("https://www.bbc.co.uk/news/articles/c0abc")
    assert w.tray.shown == []


def test_dns_filter_lookups_from_other_programs_say_nothing(world):
    """The DNS filter answers for youtube.com and everything under googlevideo.com (0.84.9) whatever asks; Steam's
    store page or a widget then connects to the listener. Recorded, silent."""
    w = world
    assert w.enforcer.blocked_name("rr3---sn-4g5e6nsz.googlevideo.com") == "blocked"
    assert w.enforcer.blocked_name("www.youtube.com") == "blocked"
    w.tray.front(None, exe="steamwebhelper.exe")
    visit(w, "rr3---sn-4g5e6nsz.googlevideo.com", "www.youtube.com", "youtube.com")
    for _ in range(12):
        w.tray.poll()
    assert w.tray.shown == []
    assert "www.youtube.com" in recorded(w)


def test_a_background_hit_on_a_protection_list_says_nothing_one_you_open_does(world):
    w = world
    w.enforcer.protection = types.SimpleNamespace(which=lambda h: "scam" if h.endswith("evil-scam.example") else None)
    w.tray.front("https://news.example/")
    visit(w, "ads.evil-scam.example")                   # an ad on the page in front
    for _ in range(12):
        w.tray.poll()
    assert w.tray.shown == []
    w.tray.front("https://evil-scam.example/login")     # you follow a link to it
    visit(w, "evil-scam.example")
    w.tray.poll()
    assert w.tray.shown == ["evil-scam.example is blocked - it's on the scam list."]


# ---------------------------------------------------------------- you opening it: a notice, with the cooldown

def test_opening_youtube_in_the_focused_browser_says_so_once_per_cooldown(world):
    w = world
    w.tray.front("https://www.youtube.com/")
    visit(w, "www.youtube.com", "googlevideo.com")
    w.tray.poll()
    assert w.tray.shown == ["YouTube is permanently blocked."]
    w.ts += 60
    w.tray.front("https://www.youtube.com/watch?v=abc")
    visit(w, "www.youtube.com")
    w.tray.poll()
    assert len(w.tray.shown) == 1                       # within the 30 min cooldown
    w.ts += 31 * 60
    w.tray.front("https://m.youtube.com/")
    visit(w, "m.youtube.com")
    w.tray.poll()
    assert w.tray.shown == ["YouTube is permanently blocked."] * 2


def test_the_tab_sent_back_before_the_tracker_read_it_still_says_so(world):
    """The tab check reads the address twice a second and sends a blocked tab back at once - the tracker may never
    see YouTube in front. What the tab check saw counts."""
    w = world
    w.tray.front("https://www.google.com/search?q=cats")
    guard = WordGuard(lambda *a: None)
    guard.tick(WORDS_OFF, lambda: (7, "https://www.youtube.com/", "YouTube — Mozilla Firefox", False),
               lambda hwnd, action: True, w.ts, {"youtube.com": "back"})
    visit(w, "www.youtube.com")
    w.tray.front("https://www.google.com/search?q=cats")   # (sent back)
    w.tray.poll()
    assert w.tray.shown == ["YouTube is permanently blocked."]


def test_the_address_read_a_moment_after_the_visit_still_says_so(world):
    w = world
    w.tray.front("https://www.google.com/")
    visit(w, "youtu.be")                                # a youtu.be link clicked: connects before the bar is read
    w.tray.poll()
    assert w.tray.shown == []
    w.tray.front("https://youtu.be/dQw4w9WgXcQ")
    w.tray.poll()
    assert w.tray.shown == ["YouTube is permanently blocked."]


def test_youtube_in_front_on_the_second_monitor_counts(world):
    w = world
    w.tray.front(None, exe="game.exe", others=[("firefox.exe", "https://www.youtube.com/", FIREFOX, 77)])
    visit(w, "www.youtube.com")
    w.tray.poll()
    assert w.tray.shown == ["YouTube is permanently blocked."]


def test_youtube_shown_long_after_a_background_hit_is_not_that_visit(world):
    w = world
    w.tray.front(None, exe="discord.exe")
    visit(w, "youtu.be")
    w.tray.poll(15)
    w.tray.front(None, exe="discord.exe")
    w.tray.poll(15)
    w.tray.front("https://www.youtube.com/")   # (opened now: its own visit is what counts)
    w.tray.poll()
    assert w.tray.shown == []


def test_a_blocked_app_still_says_so_at_once(world):
    w = world
    game = w.gui.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "permanent"}], block_type="close")
    w.step()
    pid = w.launch("game.exe")
    w.step()
    assert w.alive(pid) is False
    w.tray.poll()
    assert w.tray.closed == ["game.exe"] and w.tray.shown == ["Game is permanently blocked."]
    assert game in w.tray.last_alert


def test_a_visit_seen_late_by_a_busy_window_still_says_so(world):
    """The tray window was busy for 20 s (a page being built) when you tried YouTube; the tab check had sent the tab
    back at once. The visit is looked for in front from its own time, not from when the window got to it."""
    w = world
    w.tray.front("https://www.google.com/")
    guard = WordGuard(lambda *a: None)
    guard.tick(WORDS_OFF, lambda: (7, "https://www.youtube.com/", "YouTube — Mozilla Firefox", False),
               lambda hwnd, action: True, w.ts, {"youtube.com": "back"})
    visit(w, "www.youtube.com")
    w.ts += 20
    w.tray.front("https://www.google.com/")   # (sent back)
    w.tray.poll()
    assert w.tray.shown == ["YouTube is permanently blocked."]


def test_a_full_screen_youtube_video_when_its_block_begins_says_so(world):
    """Full screen hides the address bar: the tab check knows YouTube from the window's title (0.84.9)."""
    from monitor import word_guard
    w = world
    title = "Cats - YouTube — Mozilla Firefox"
    address, hidden = word_guard.tab_address(9, None, title)
    assert (address, hidden) == ("youtube.com", True)
    guard = WordGuard(lambda *a: None)
    guard.tick(WORDS_OFF, lambda: (9, address, title, hidden), lambda hwnd, action: True, w.ts,
               {"youtube.com": "back"})
    visit(w, "www.youtube.com")
    w.tray.front(None, exe="explorer.exe")   # (the tab closed)
    w.tray.poll()
    assert w.tray.shown == ["YouTube is permanently blocked."]
