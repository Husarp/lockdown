"""The browser tab in front, twice a second, in the tray agent:

- bad words in its address or title (keywords.find)
- a blocked site that you chose to have closed instead of (or as well as) being sent nowhere

Either way the tab is closed (Ctrl+W) or the browser goes back (Alt+Left) - if going back doesn't leave the
page (e.g. a new tab has no previous page), the tab is closed."""
import logging
import threading
import time

import keywords
from blocker import site_block
from blocker.hosts import normalize_host
from db import Database
from trusted_time import now_from_db

TICK_SEC = 0.5
RETRY_SEC = 2   # the same page still there this long after acting: act again (going back -> closing)
SITES_SEC = 2   # how often the list of blocked sites is re-read

log = logging.getLogger("lockdown.words")


def sense_tab():
    """(window handle, address, title) of the browser window in front, or None."""
    from monitor import browser_url, win
    hwnd, exe = win.foreground()
    if not hwnd or exe not in browser_url.BROWSERS:
        return None
    return hwnd, browser_url.browser_url(hwnd), win.window_title(hwnd)


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
    """{hostname: "close" / "back"} for the sites blocked right now that ask for the tab to be acted on."""
    out = {}
    for host, block in db.active_blocks(now_from_db(db)).items():
        if block["item"]["item_type"] == "site":
            action = site_block.tab_action(block["item"].get("block_type"))
            if action:
                out[host] = action
    return out


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
        self.last: tuple | None = None   # ((hwnd, address, title), when we acted)

    def run(self):
        import uiautomation as auto   # COM must be initialized in this thread
        with auto.UIAutomationInitializerInThread():
            db = Database()
            read_at, sites = 0.0, {}
            while not self.stop_event.wait(TICK_SEC):
                try:
                    now = time.monotonic()
                    if now - read_at >= SITES_SEC:
                        read_at, sites = now, blocked_sites(db)
                    self.tick(keywords.settings(db), sense_tab, act, now, sites)
                except Exception:
                    log.exception("Word check failed")

    def tick(self, cfg: dict, sense, do, now: float, sites: dict | None = None):
        """sites: {hostname: "close" / "back"} from blocked_sites()."""
        tab = sense() if (cfg["enabled"] or sites) else None
        word = tab and cfg["enabled"] and keywords.find(tab[1], tab[2], cfg)
        action = cfg["action"] if word else (site_action(tab[1], sites or {}) if tab else None)
        if not action:
            self.last = None
            return
        first = not (self.last and self.last[0] == tab)
        if not first:
            if now - self.last[1] < RETRY_SEC:
                return          # just acted - give the browser a moment
            action = "close"    # going back didn't leave the page
        if do(tab[0], action):
            self.last = (tab, now)
            if first and word:  # one notice per detection; a blocked site has its own alert already
                self.on_block(word, action)
