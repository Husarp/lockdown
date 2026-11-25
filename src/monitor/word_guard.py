"""The browser tab in front, twice a second, in the tray agent:

- bad words in its address or title (keywords.find)
- a blocked site that you chose to have closed instead of (or as well as) being sent nowhere

Either way the tab is closed (Ctrl+W) or the browser goes back (Alt+Left) - if going back doesn't leave the
page (e.g. a new tab has no previous page), or lands on something blocked again, the tab is closed."""
import logging
import threading
import time

import antibypass
import keywords
from blocker import protection, site_block
from blocker.hosts import normalize_host
from db import Database
from monitor import usage
from monitor.address import LAST_VALID_SEC, valid_address
from resident import diag
from resident.site_media import FRESH_SEC, tab_token
from trusted_time import now_from_db

TICK_SEC = 0.5
RETRY_SEC = 2   # the same page still there this long after acting: act again (going back -> closing)
CLOSE_GAP_SEC = 10   # after closing a tab, another blocked tab of that window is left alone this long (0.84.11)
SITES_SEC = 2   # how often the list of blocked sites is re-read (and at once when a limit runs out - BlockedSites)
RESTART_SEC = 30   # wait before starting the check again after it fell over

log = logging.getLogger("lockdown.words")


_bar: dict[int, tuple[str, str, float]] = {}   # browser window -> (title, address, monotonic time of its last valid read)


def sense_tab():
    """(window handle, address, title, address bar hidden) of the browser window in front, or None."""
    from monitor import browser_url, win
    hwnd, exe = win.foreground()
    if not hwnd or exe not in browser_url.BROWSERS:
        return None
    title = win.window_title(hwnd)
    url, hidden = tab_address(hwnd, browser_url.browser_url(hwnd), title, exe)
    return hwnd, url, title, hidden


def tab_address(hwnd: int, url: str | None, title: str, exe: str | None = None) -> tuple[str | None, bool]:
    """(the address to check, whether the address bar couldn't be read). A video played full screen hides the
    address bar, so the tab check read nothing and left a full-screen YouTube video alone, while the
    tracker counted it (it keeps the last address - usage._read_url). Now the same: the address last read in this
    window for up to 10 seconds while its title is unchanged, else the site the title names
    ("Cats - YouTube — Mozilla Firefox" - site_block.title_site), 0.84.9. Kept apart from the tracker's memory:
    this runs on its own thread. A host verified under the same title disambiguates a browser profile suffix
    after the full address expires; a title or address change discards that confirmation."""
    now = time.monotonic()
    if valid_address(url):
        if len(_bar) > 200:
            _bar.clear()
        _bar[hwnd] = (title, url, now)
        return url, False
    last = _bar.get(hwnd)
    if last and (not title or last[0] != title):
        _bar.pop(hwnd, None)
        last = None
    return (last[1] if last and title and last[0] == title
                       and 0 <= now - last[2] <= LAST_VALID_SEC else
            site_block.title_site(title, exe, usage.site_of(last[1]) if last else None)), True


def act(hwnd: int, action: str, recheck=None, original=None) -> bool:
    from monitor import browser_url, win
    def same_tab():
        if recheck is None:
            return True
        current = recheck()
        if not current or not current.actionable or current.host != original.host:
            return False
        token = tab_token(original)
        if token is not None:
            return tab_token(current) == token
        return bool(original.url and original.url == current.url and not original.bar_hidden and not current.bar_hidden)

    if action == "back":
        return same_tab() and win.press(hwnd, win.VK_MENU, win.VK_LEFT)
    if browser_url.tab_count(hwnd) == 1:
        # A keyboard shortcut can only target the current tab. Check the newly opened tab
        # before switching back and check the original again before closing.
        if not same_tab() or (recheck is not None and tab_token(original) is None):
            return False
        if not win.chord(hwnd, win.VK_CONTROL, win.VK_T):
            return False
        time.sleep(0.08)
        if recheck is not None:
            fresh = recheck()
            fresh_url = (getattr(fresh, "url", None) or "").lower().rstrip("/")
            if (not fresh or not tab_token(fresh) or tab_token(fresh) == tab_token(original)
                    or fresh_url not in _NEW_TAB_URLS):
                return False
        if not win.chord(hwnd, win.VK_CONTROL, win.VK_SHIFT, win.VK_TAB):
            return False
        time.sleep(0.05)
    return same_tab() and win.chord(hwnd, win.VK_CONTROL, win.VK_W)


_NEW_TAB_URLS = {"about:blank", "about:newtab", "about:home", "chrome://newtab", "edge://newtab",
                 "brave://newtab", "vivaldi://newtab", "chrome://new-tab-page", "opera://startpage"}


def blocked_sites(db) -> dict[str, str]:
    """{hostname: "close" / "back"} for every site blocked right now whose way of being blocked says so - its
    own, or its group's (block_method, through db.blocks).

    A site that is only "can't load" (dns) is left to the network: its tabs are never closed or sent back. 0.84.1
    sent those back too (closing the tab when that didn't leave the page), as a backstop for a page already open
    when the block began - which closed YouTube videos you had chosen only to stop loading, several at once
    (0.84.11). The network covers that page now: its open connections are cut, browsers can't use QUIC while a
    site is blocked, and its video hosts are firewalled, IPv4 and IPv6 (0.84.9, service.update_video_block)."""
    out = {}
    for block in db.blocks(now_from_db(db)):
        item = block["item"]
        if item["item_type"] != "site":
            continue
        flags = site_block.site_flags(item.get("block_type"))
        action = "close" if "close" in flags else "back" if "back" in flags else None
        if action:
            for host in item["target"].split():
                if out.get(host) != "close":
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


def matching_sites(host, sites):
    return {site: action for site, action in sites.items() if host == site or host.endswith("." + site)}


def site_action(url: str | None, sites: dict) -> str | None:
    """What to do about the address in front, combining applicable parent and exact host rules."""
    if not url or not sites:
        return None
    try:
        host = normalize_host(url)
    except ValueError:      # e.g. search text typed in the address bar
        return None
    actions = matching_sites(host, sites).values()
    return "close" if "close" in actions else "back" if "back" in actions else None


class WordGuard(threading.Thread):
    """on_block(word, action) is called (from this thread) after a tab was closed / sent back for a bad word.
    A blocked site says nothing extra: visiting it already raises its own alert."""

    def __init__(self, on_block, before_action=None):
        super().__init__(daemon=True)
        self.on_block = on_block
        self.before_action = before_action
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
        from monitor import tab_reader, win

        def observe(refresh=False):
            hwnd, exe = win.foreground()
            return reader.read(hwnd, exe, role="foreground", refresh=refresh) if hwnd else None

        def live_sites():
            return {} if antibypass.is_off(db) else blocked_sites(db)

        with auto.UIAutomationInitializerInThread():
            db = Database(ui=True)
            try:
                reader = tab_reader.TabReader()
                blocked = BlockedSites()
                while not self.stop_event.wait(TICK_SEC):
                    try:
                        if antibypass.is_off(db):
                            self.last = None
                            continue
                        now = time.monotonic()
                        self.tick(keywords.settings_shared(db), observe, act, now, blocked.get(db, now),
                                  protection.words_skip_sites(db), live_sites=live_sites)
                    except Exception:
                        log.exception("Word check failed")
            finally:
                db.close()

    def tick(self, cfg: dict, sense, do, now: float, sites: dict | None = None, skip_sites=(), *, live_sites=None):
        """sites: {hostname: "close" / "back"} from blocked_sites(). skip_sites: allowed sites that skip blocked words.
        sense() returns the thread's observation. Tuple readings are accepted by the legacy keyword/guard tests."""
        sensed = sense() if (cfg["enabled"] or sites) else None
        obs = sensed if getattr(sensed, "hwnd", None) else None
        tab = (obs.hwnd, obs.url or obs.host, obs.title, obs.bar_hidden, tab_token(obs)) if obs else sensed
        if tab:
            usage.note_shown(tab[1])   # a blocked-visit notice needs it
        word = tab and cfg["enabled"] and keywords.find(tab[1], tab[2], cfg, skip_sites)
        action = cfg["action"] if word else (site_action(tab[1], sites or {}) if tab else None)
        if not word and obs and (not obs.actionable or not 0 <= time.monotonic() - obs.observed_at <= FRESH_SEC):
            action = None
        expected = action
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
        if obs and not word:
            def recheck():
                fresh = sense(refresh=True)
                if (fresh and fresh.hwnd == obs.hwnd and fresh.pid and fresh.pid == obs.pid
                        and fresh.process_started_at == obs.process_started_at
                        and 0 <= time.monotonic() - fresh.observed_at <= FRESH_SEC
                        and (live_sites is None or site_action(obs.host, live_sites()) == expected)):
                    return fresh
                return None

            if self.before_action:
                try:
                    self.before_action(obs, frozenset(matching_sites(obs.host, sites or {})), action)
                except Exception:
                    log.exception("Couldn't request Pause before site action")
            acted = do(tab[0], action, recheck=recheck, original=obs)
            if acted:
                stage = " after Pause preflight" if self.before_action else ""
                diag.note(f"site_action {action} completed{stage}")
        else:
            acted = do(tab[0], action)
        if acted:
            self.last = (tab, now, action)
            if first and word:  # one notice per detection; a blocked site has its own alert already
                self.on_block(word, action)
