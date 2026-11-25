"""Enforce Minimize for the selected tab of each ordinary browser window."""
from contextlib import ExitStack
from datetime import timedelta
from urllib.parse import urlsplit

import block_method
from blocker.site_block import MINIMIZE_INTERVAL_DEFAULT, minimize_interval, site_flags, tab_action
from resident.workers import Worker

SNAPSHOT_MAX_AGE_SEC = 3    # StatusWorker normally publishes every second.


def _observed_host(observation):
    """Keep an explicit www alias when the fresh address establishes it."""
    host, url = observation.host, observation.url
    if host and url:
        try:
            raw = urlsplit(url if "://" in url else "//" + url).hostname
        except ValueError:
            raw = None
        if raw and raw.rstrip(".") == "www." + host:
            return raw.rstrip(".")
    return host


class SiteMinimizeWorker(Worker):
    label = "site-minimize"
    interval = MINIMIZE_INTERVAL_DEFAULT

    def __init__(self, hub, before_action):
        super().__init__(hub)
        self.before_action = before_action
        self._reader = None
        self._resources = ExitStack()

    def _start_reader(self):
        import uiautomation
        from monitor.tab_reader import TabReader

        # Keep each reader and its UIA controls on the worker thread.
        with ExitStack() as resources:
            resources.enter_context(uiautomation.UIAutomationInitializerInThread())
            self._reader = TabReader()
            self._resources = resources.pop_all()

    def teardown(self):
        self._reader = None
        self._resources.close()

    def _site_ways(self):
        snap = self.hub.snapshot
        if snap is None or snap.off:
            return {}
        age = self.hub.clock.wall() - snap.wall
        if not 0 <= age <= SNAPSHOT_MAX_AGE_SEC:
            return {}
        now = snap.now + timedelta(seconds=age)
        return block_method.site_ways(block["item"] for block in snap.block_list
                                      if not block["item"].get("disabled")
                                      and (block["until"] is None or block["until"] > now))

    def tick(self):
        seconds = minimize_interval(self.db)
        ways = self._site_ways()
        if any("minimize" in site_flags(way) for way in ways.values()) and not self.hub.win.session_locked():
            self._scan(ways)
        return seconds

    def _scan(self, ways):
        from monitor.browser_catalog import identify

        win = self.hub.win
        if self._reader is None:
            self._start_reader()
        browsers = {}
        # EnumWindows on the resident's desktop includes covered windows on every monitor in this session.
        for hwnd in win.enum_windows():
            if self.stop_event.is_set():
                break
            pid = win.site_window_pid(hwnd)
            if not pid:
                continue
            if pid not in browsers:
                path = win.pid_path(pid)
                exe = win.exe_name(path)
                browsers[pid] = exe if exe and identify(exe, path) else None
            if exe := browsers[pid]:
                self._check_window(hwnd, pid, exe, ways)

    def _role(self, hwnd):
        return "foreground" if self.hub.win.foreground()[0] == hwnd else "nonforeground"

    @staticmethod
    def _may_minimize(matches, role):
        if not matches:
            return False
        way = block_method.merge("site", *matches.values())
        return "minimize" in site_flags(way) and (role != "foreground" or tab_action(way) is None)

    def _read(self, hwnd, pid, exe, role):
        observation = self._reader.read(hwnd, exe, role=role, refresh=True)
        if (observation.actionable and observation.source == "uia"
                and (observation.hwnd, observation.pid) == (hwnd, pid)):
            return observation
        return None  # TabReader records nonforeground read failures.

    def _check_window(self, hwnd, pid, exe, ways):
        role = self._role(hwnd)
        before = self._read(hwnd, pid, exe, role)
        if before is None or not before.host:
            return
        host = _observed_host(before)
        hosts = block_method.matching_sites(host, ways)
        if not self._may_minimize(hosts, role):
            return
        win = self.hub.win
        if self.stop_event.is_set() or win.site_window_pid(hwnd) != pid or role != self._role(hwnd):
            return
        hosts = block_method.matching_sites(host, self._site_ways())
        if not self._may_minimize(hosts, role):
            return
        try:
            self.before_action(before, frozenset(hosts), "minimize")
        except Exception as error:
            self.hub.report(self.label, error)

        if self.stop_event.is_set() or win.site_window_pid(hwnd) != pid or win.session_locked():
            return
        role = self._role(hwnd)
        after = self._read(hwnd, pid, exe, role)
        if (after is None or after.process_started_at != before.process_started_at
                or (before.host, before.tab_token, before.url) != (after.host, after.tab_token, after.url)):
            return
        current = block_method.matching_sites(host, self._site_ways())
        if self.stop_event.is_set() or role != self._role(hwnd) or not self._may_minimize(current, role):
            return
        if not win.minimize_window(hwnd, pid) and win.site_window_pid(hwnd) == pid:
            self.hub.report(self.label, RuntimeError("Windows did not minimize the browser window"))
