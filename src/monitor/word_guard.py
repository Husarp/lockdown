"""Bad-word check in the tray agent: twice a second, the address and title of the browser tab in front are checked
(keywords.find); on a blocked word the tab is closed (Ctrl+W) or the browser goes back (Alt+Left) - if going back
doesn't leave the page (e.g. a new tab has no previous page), the tab is closed."""
import logging
import threading
import time

import keywords
from db import Database

TICK_SEC = 0.5
RETRY_SEC = 2   # the same page still there this long after acting: act again (going back -> closing)

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


class WordGuard(threading.Thread):
    """on_block(word, action) is called (from this thread) after a tab was closed / sent back."""

    def __init__(self, on_block):
        super().__init__(daemon=True)
        self.on_block = on_block
        self.stop_event = threading.Event()
        self.last: tuple | None = None   # ((hwnd, address, title), when we acted)

    def run(self):
        import uiautomation as auto   # COM must be initialized in this thread
        with auto.UIAutomationInitializerInThread():
            db = Database()
            while not self.stop_event.wait(TICK_SEC):
                try:
                    self.tick(keywords.settings(db), sense_tab, act, time.monotonic())
                except Exception:
                    log.exception("Word check failed")

    def tick(self, cfg: dict, sense, do, now: float):
        tab = sense() if cfg["enabled"] else None
        word = tab and keywords.find(tab[1], tab[2], cfg)
        if not word:
            self.last = None
            return
        action = cfg["action"]
        first = not (self.last and self.last[0] == tab)
        if not first:
            if now - self.last[1] < RETRY_SEC:
                return          # just acted - give the browser a moment
            action = "close"    # going back didn't leave the page
        if do(tab[0], action):
            self.last = (tab, now)
            if first:           # only one notice per detection (no second one when back escalates to close)
                self.on_block(word, action)
