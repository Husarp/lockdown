"""The browser tab in front, twice a second, in the tray agent:

- bad words in its address or title (keywords.find)
- a blocked site that you chose to have closed instead of (or as well as) being sent nowhere

Either way the tab is closed (Ctrl+W) or the browser goes back (Alt+Left) - if going back doesn't leave the
page (e.g. a new tab has no previous page), or lands on something blocked again, the tab is closed."""
import logging
import threading
import time

import keywords
from blocker import site_block
from blocker.hosts import normalize_host
from db import Database
from monitor import usage
from trusted_time import now_from_db

TICK_SEC = 0.5
RETRY_SEC = 2   # the same page still there this long after acting: act again (going back -> closing)
CLOSE_GAP_SEC = 10   # after closing a tab, another blocked tab of that window is left alone this long (0.84.11)
SITES_SEC = 2   # how often the list of blocked sites is re-read (and at once when a limit runs out - BlockedSites)
RESTART_SEC = 30   # wait before starting the check again after it fell over

log = logging.getLogger("lockdown.words")


_bar: dict[int, tuple[str, str]] = {}   # browser window -> (its title, its address) at the last read of its bar


def sense_tab():
    """(window handle, address, title, address bar hidden) of the browser window in front, or None."""
    from monitor import browser_url, win
    hwnd, exe = win.foreground()
    if not hwnd or exe not in browser_url.BROWSERS:
        return None
    title = win.window_title(hwnd)
    url, hidden = tab_address(hwnd, browser_url.browser_url(hwnd), title)
    return hwnd, url, title, hidden


def tab_address(hwnd: int, url: str | None, title: str) -> tuple[str | None, bool]:
    """(the address to check, whether the address bar couldn't be read). A video played full screen hides the
    address bar, so the tab check read nothing and left a full-screen YouTube video alone, while the
    tracker counted it (it keeps the last address - usage._read_url). Now the same: the address last read in this
    window while its title is unchanged, else the site the title names ("Cats - YouTube — Mozilla Firefox" -
    site_block.title_site), 0.84.9. Kept apart from the tracker's memory: this runs on its own thread."""
    if url is not None:
        if len(_bar) > 200:
            _bar.clear()
        _bar[hwnd] = (title, url)
        return url, False
    last = _bar.get(hwnd)
    return (last[1] if last and title and last[0] == title else site_block.title_site(title)), True


def act(hwnd: int, action: str) -> bool:
    from monitor import browser_url, win
    if action == "back":
        return win.press(hwnd, win.VK_MENU, win.VK_LEFT)
    if browser_url.tab_count(hwnd) == 1:
        # closing the only tab would close the whole browser window - open a fresh tab first, then close the bad one
        win.chord(hwnd, win.VK_CONTROL, win.VK_T)
        time.sleep(0.08)
        win.chord(hwnd, win.VK_CONTROL, win.VK_SHIFT, win.VK_TAB)   # back to the blocked tab
        time.sleep(0.05)
    return win.chord(hwnd, win.VK_CONTROL, win.VK_W)


def blocked_sites(db) -> dict[str, str]:
    """{hostname: "close" / "back"} for every site blocked right now whose way of being blocked says so - its
    own, or its group's (block_method, through db.blocks).

    A site that is only "can't load" (dns) is left to the network: its tabs are never closed or sent back. 0.84.1
    sent those back too (closing the tab when that didn't leave the page), as a backstop for a page already open
    when the block began - which closed YouTube videos you had chosen only to stop loading, several at once
    (0.84.11). The network covers that page now: its open connections are cut, browsers can't use QUIC while a
    site is blocked, and its video hosts are firewalled, IPv4 and IPv6 (0.84.9, service.update_video_block)."""
    out = {}
    for host, block in db.active_blocks(now_from_db(db)).items():
        if block["item"]["item_type"] == "site" and (action := site_block.tab_action(block["item"].get("block_type"))):
            out[host] = action
    return out


class BlockedSites:
    """blocked_sites(), read again every SITES_SEC - and at once after the usage tracker has written time that
    makes a limit or an allowance run out, or an opening (usage.limit_writes, 0.84.2). A site whose minutes just
    ran out used to stay on screen until the next read, up to SITES_SEC; now it is sent back on the next tick."""

    def __init__(self, read=blocked_sites):
        self.read = read
        self.at: float | None = None   # when last read (monotonic)
        self.writes = None             # usage.limit_writes as it was then
        self.sites: dict[str, str] = {}

    def get(self, db, now: float) -> dict[str, str]:
        writes = usage.limit_writes   # (taken first: a write during the read makes the next tick read again)
        if self.at is None or not 0 <= now - self.at < SITES_SEC or writes != self.writes:
            self.at, self.writes = now, writes
            self.sites = self.read(db)
        return self.sites


def site_action(url: str | None, sites: dict) -> str | None:
    """What to do about the address in front, if it is one of those sites (or below one of them)."""
    if not url or not sites:
        return None
    try:
        host = normalize_host(url)
    except ValueError:      # e.g. search text typed in the address bar
        return None
    while host:
        if host in sites:
            return sites[host]
        host = host.partition(".")[2]
    return None


class WordGuard(threading.Thread):
    """on_block(word, action) is called (from this thread) after a tab was closed / sent back for a bad word.
    A blocked site says nothing extra: visiting it already raises its own alert."""

    def __init__(self, on_block):
        super().__init__(daemon=True)
        self.on_block = on_block
        self.stop_event = threading.Event()
        self.last: tuple | None = None   # ((hwnd, address, title), when we acted, "close" / "back")

    def run(self):
        """Keep checking, whatever happens: starting up (COM, opening the database while the service holds it)
        used to be outside any guard, so one failure there ended the check for good, silently - the same hole
        the usage tracker had (UsageTracker.run)."""
        while not self.stop_event.is_set():
            try:
                self._check()
            except Exception:
                log.exception("Word check stopped - starting it again in %ds", RESTART_SEC)
                self.stop_event.wait(RESTART_SEC)

    def _check(self):
        import uiautomation as auto   # COM must be initialized in this thread
        with auto.UIAutomationInitializerInThread():
            db = Database()
            blocked = BlockedSites()
            while not self.stop_event.wait(TICK_SEC):
                try:
                    now = time.monotonic()
                    self.tick(keywords.settings_shared(db), sense_tab, act, now, blocked.get(db, now))   # (read-only)
                except Exception:
                    log.exception("Word check failed")

    def tick(self, cfg: dict, sense, do, now: float, sites: dict | None = None):
        """sites: {hostname: "close" / "back"} from blocked_sites().
        sense() -> (window, address, title[, address bar hidden]) - sense_tab()."""
        tab = sense() if (cfg["enabled"] or sites) else None
        if tab:
            usage.note_shown(tab[1])   # (a blocked-visit notice needs it: sent back before the tracker saw it)
        word = tab and cfg["enabled"] and keywords.find(tab[1], tab[2], cfg)
        action = cfg["action"] if word else (site_action(tab[1], sites or {}) if tab else None)
        if not action:
            self.last = None
            return
        first = not (self.last and self.last[0] == tab)
        if not first:
            # just acted - give the browser a moment (after closing a tab, as long as before the next one: two tabs
            # showing the same page look alike)
            if now - self.last[1] < (CLOSE_GAP_SEC if self.last[2] == "close" and not word else RETRY_SEC):
                return
            action = "close"    # going back didn't leave the page (or closing didn't happen)
        elif self.last and self.last[0][0] == tab[0] and not word and self.last[2] == "close":
            # a tab was just closed in this window and the one now in front is blocked too: Lockdown brought it
            # to the front, you didn't open it. One tab at a time - closing it at once closed every YouTube tab
            # of the window in a row (0.84.11). It is dealt with like any other only if it is still in front
            # CLOSE_GAP_SEC later.
            if now - self.last[1] < CLOSE_GAP_SEC:
                return
            if len(tab) > 3 and tab[3]:
                action = "close"    # (no address bar: going back would only seek the video)
        elif self.last and self.last[0][0] == tab[0]:
            # we just sent this window back and it still shows something blocked: going back on a YouTube video
            # lands on the one before, still YouTube - it hopped back through the history while the video
            # played (0.84.9). It is the same tab: close it.
            action = "close"
        elif not word and len(tab) > 3 and tab[3]:
            # no address bar (a full-screen video): going back would only seek the video or hop to the one
            # before - close the tab (0.84.9)
            action = "close"
        if do(tab[0], action):
            self.last = (tab, now, action)
            if first and word:  # one notice per detection; a blocked site has its own alert already
                self.on_block(word, action)
