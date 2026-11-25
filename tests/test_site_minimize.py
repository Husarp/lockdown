import sys
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace

import pytest

from blocker.site_block import MINIMIZE_INTERVAL_KEY
from monitor import browser_url, tab_reader, win
from monitor.site_minimize import SiteMinimizeWorker
from monitor.tab_reader import TabObservation, TabReader
from resident.workers import Hub
from test_browser_tab_reader import Node

NOW = datetime(2026, 11, 24, 18)


class Desktop:
    def __init__(self):
        self.front = 1
        self.windows = {
            # Same process and page title across tabs. Window 2 is covered on a second monitor.
            1: SimpleNamespace(pid=42, tabs=["example.org", "safe.org"], selected=1, minimized=False, monitor=1),
            2: SimpleNamespace(pid=42, tabs=["video.example.org", "safe.org"], selected=0, minimized=False, monitor=2),
            3: SimpleNamespace(pid=42, tabs=["example.org", "safe.org"], selected=0, minimized=True, monitor=2),
        }
        self.events = []
        self.locked = False

    def enum_windows(self):
        self.events.append(("enumerate",))
        return list(self.windows)

    def session_locked(self):
        return self.locked

    def window_pid(self, hwnd):
        window = self.windows.get(hwnd)
        return window.pid if window else 0

    def site_window_pid(self, hwnd):
        window = self.windows.get(hwnd)
        return window.pid if window and not window.minimized else 0

    def pid_path(self, pid):
        return r"C:\Browser\chrome.exe" if pid == 42 else r"C:\Other\other.exe"

    def exe_name(self, path):
        return path.rsplit("\\", 1)[-1]

    def foreground(self):
        return self.front, self.exe_name(self.pid_path(42))

    def minimize_window(self, hwnd, pid):
        assert self.site_window_pid(hwnd) == pid
        self.events.append(("minimize", hwnd, pid))
        self.windows[hwnd].minimized = True
        return True

    def read(self, hwnd, exe, *, role, refresh):
        assert exe == "chrome.exe" and refresh
        self.events.append(("read", hwnd, role))
        window = self.windows[hwnd]
        host = window.tabs[window.selected]
        return TabObservation(hwnd, window.pid, role, 0, host=host, tab_token=(hwnd, window.selected),
                              url=f"https://{host}/" if host else None, title="Same title",
                              source="uia" if host else "unknown", actionable=bool(host),
                              process_started_at=getattr(window, "started", None))


def site(way="minimize", target="example.org", *, until=None, disabled=False):
    return {"item": {"item_type": "site", "target": target, "block_type": way, "disabled": disabled}, "until": until}


@pytest.fixture
def scanner(tmp_path, monkeypatch):
    desktop = Desktop()
    automation = ModuleType("uiautomation")

    class COM:
        def __enter__(self):
            desktop.events.append(("enter COM",))

        def __exit__(self, *args):
            desktop.events.append(("exit COM",))

    automation.UIAutomationInitializerInThread = COM
    monkeypatch.setitem(sys.modules, automation.__name__, automation)
    monkeypatch.setattr(tab_reader, "TabReader", lambda: desktop)
    notes = []
    monkeypatch.setattr("resident.diag.note", lambda text, *, important: notes.append((text, important)))
    clock = SimpleNamespace(wall=lambda: 100.0)
    hub = Hub(tmp_path / "scanner.db", clock=clock, win=desktop)
    hub.snapshot = SimpleNamespace(off=None, wall=100.0, now=NOW, block_list=[site()])
    worker = SiteMinimizeWorker(
        hub, lambda obs, hosts, action: desktop.events.append(("pause", obs.hwnd, hosts, action)))
    yield worker, desktop, notes
    worker.teardown()
    if worker._db is not None:
        worker.db.close()


def test_covered_selected_tab_is_paused_and_minimized_by_hwnd(scanner):
    worker, desktop, notes = scanner
    changes = worker.db.conn.total_changes
    assert worker.tick() == 3
    assert desktop.events == [
        ("enter COM",), ("enumerate",), ("read", 1, "foreground"), ("read", 2, "nonforeground"),
        ("pause", 2, {"example.org"}, "minimize"), ("read", 2, "nonforeground"), ("minimize", 2, 42),
    ]
    assert not desktop.windows[1].minimized and not notes
    desktop.events.clear()
    assert worker.tick() == 3
    assert desktop.events == [("enumerate",), ("read", 1, "foreground")]
    desktop.windows[2].minimized = False
    assert worker.tick() == 3
    assert desktop.events[-1] == ("minimize", 2, 42)
    assert worker.db.conn.total_changes == changes
    worker.teardown()
    assert desktop.events[-1] == ("exit COM",)


def test_all_suffix_ways_set_foreground_precedence_and_preserve_background(scanner):
    worker, desktop, _ = scanner
    desktop.windows[1].selected = 0
    desktop.windows[1].tabs[0] = "video.example.org"
    worker.hub.snapshot.block_list = [
        site("dns,close"), site("back,minimize", "video.example.org"), site("dns", "video.example.org")]
    worker.tick()
    assert ("pause", 2, {"example.org", "video.example.org"}, "minimize") in desktop.events
    assert ("minimize", 2, 42) in desktop.events
    assert not desktop.windows[1].minimized


def test_foreground_minimize_only_still_enforces(scanner):
    worker, desktop, _ = scanner
    desktop.windows[1].selected = 0
    worker.tick()
    assert ("minimize", 1, 42) in desktop.events


def test_media_failure_still_enforces_and_native_refusal_is_reported(scanner, monkeypatch):
    worker, desktop, _ = scanner
    errors = []
    monkeypatch.setattr(worker.hub, "report", lambda who, error: errors.append(str(error)))

    def unavailable(*args):
        raise RuntimeError("Media unavailable")

    worker.before_action = unavailable
    monkeypatch.setattr(desktop, "minimize_window", lambda hwnd, pid: False)
    worker.tick()
    assert errors == ["Media unavailable", "Windows did not minimize the browser window"]
    assert not desktop.windows[2].minimized


@pytest.mark.parametrize("transition", ["tab", "same_host_tab", "closed", "reused", "minimized", "unblocked",
                                       "off", "expired", "now_foreground", "shutdown"])
def test_revalidates_after_pause(scanner, transition):
    worker, desktop, _ = scanner
    if transition == "same_host_tab":
        desktop.windows[2].tabs[1] = desktop.windows[2].tabs[0]
    if transition == "now_foreground":
        worker.hub.snapshot.block_list = [site("close,minimize")]
    if transition == "expired":
        worker.hub.snapshot.block_list = [site(until=NOW + timedelta(seconds=1))]

    def changed(obs, hosts, action):
        desktop.events.append(("pause", obs.hwnd, hosts, action))
        if transition in {"tab", "same_host_tab"}:
            desktop.windows[2].selected = 1
        elif transition == "closed":
            del desktop.windows[2]
        elif transition == "reused":
            desktop.windows[2].pid = 43
        elif transition == "minimized":
            desktop.windows[2].minimized = True
        elif transition == "unblocked":
            worker.hub.snapshot.block_list = []
        elif transition == "off":
            worker.hub.snapshot.off = "now"
        elif transition == "expired":
            worker.hub.clock.wall = lambda: 101.0
        elif transition == "shutdown":
            worker.stop()
        else:
            desktop.front = 2

    worker.before_action = changed
    worker.tick()
    assert ("pause", 2, {"example.org"}, "minimize") in desktop.events
    assert not any(event[0] == "minimize" for event in desktop.events)


def test_reports_unreadable_nonforeground_once_and_waits_for_fresh_uia(scanner, monkeypatch):
    worker, desktop, notes = scanner
    worker._reader = TabReader()
    monkeypatch.setattr(win, "window_pid", desktop.window_pid)
    monkeypatch.setattr(win, "pid_path", desktop.pid_path)
    monkeypatch.setattr(win, "window_title", lambda _: "Same title")
    monkeypatch.setattr(tab_reader, "_window_reports", tab_reader.OrderedDict())
    bars = {1: Node("EditControl", cls="OmniboxViewViews", value="https://safe.org/")}
    monkeypatch.setattr(browser_url.auto, "ControlFromHandle", lambda hwnd: Node(
        "WindowControl", children=[bars[hwnd]] if hwnd in bars else []), raising=False)
    worker.tick()
    worker.tick()
    assert len(notes) == 1 and notes[0][1] and "nonforeground" in notes[0][0]
    assert "example.org" not in notes[0][0] and not desktop.windows[2].minimized
    bars[2] = Node("EditControl", cls="OmniboxViewViews", value="https://video.example.org/")
    worker.tick()
    assert desktop.windows[2].minimized and len(notes) == 1
    # A later window with a different handle is reported independently.
    desktop.windows[4] = SimpleNamespace(pid=42, tabs=[None], selected=0, minimized=False)
    worker.tick()
    assert len(notes) == 2


@pytest.mark.parametrize("no_scan", ["off", "stale", "disabled", "dns", "unblocked", "locked"])
def test_no_browser_scan_without_a_live_applicable_block(scanner, no_scan):
    worker, desktop, _ = scanner
    snap = worker.hub.snapshot
    if no_scan == "off":
        snap.off = "off"
    elif no_scan == "stale":
        snap.wall -= 4
    elif no_scan == "disabled":
        snap.block_list = [site(disabled=True)]
    elif no_scan == "dns":
        snap.block_list = [site("dns")]
    elif no_scan == "unblocked":
        snap.block_list = []
    else:
        desktop.locked = True
    assert worker.tick() == 3
    assert not desktop.events


def test_worker_paces_scans_at_the_latest_saved_interval(scanner, monkeypatch):
    worker, desktop, _ = scanner
    elapsed = [0.0]
    worker.hub.clock.mono = lambda: elapsed[0]
    actual_scan = worker._scan
    checks = []

    def scan(ways):
        checks.append(elapsed[0])
        actual_scan(ways)
        if len(checks) == 2:
            worker.db.set_setting(MINIMIZE_INTERVAL_KEY, "2")
        elif len(checks) == 4:
            worker.stop()

    def wait(seconds):
        elapsed[0] += seconds

    monkeypatch.setattr(worker, "_scan", scan)
    monkeypatch.setattr(worker, "_sleep", wait)
    # Settings are read before each scan, so the in-flight scan keeps its original delay.
    worker.run()
    worker._db = None  # Worker.run closed its connection.
    assert checks == [0, 3, 6, 8]
    assert desktop.events[-1] == ("exit COM",)


def test_native_helper_filters_windows_and_minimizes_without_activation(monkeypatch):
    from monitor import win

    minimized = set()
    calls = []

    def show(hwnd, command):
        calls.append((hwnd, command))
        minimized.add(hwnd)
        return True

    def style(hwnd, which):
        if hwnd == 1 and which == win.GWL_STYLE:
            return win.WS_CHILD
        if hwnd == 2 and which == win.GWL_EXSTYLE:
            return win.WS_EX_TOOLWINDOW
        return 0

    monkeypatch.setattr(win, "_user32", SimpleNamespace(IsWindowVisible=lambda hwnd: hwnd != 4,
                        IsIconic=lambda hwnd: hwnd in minimized, GetWindowLongW=style))
    monkeypatch.setattr(win, "_u32", SimpleNamespace(GetWindow=lambda hwnd, flag: 19 if hwnd == 3 else 0,
                        ShowWindowAsync=show))
    monkeypatch.setattr(win, "window_pid", lambda hwnd: 42)
    for hwnd in (1, 2, 3, 4):
        assert not win.minimize_window(hwnd, 42)
    assert not win.minimize_window(5, 43)
    assert win.minimize_window(5, 42)
    assert not win.minimize_window(5, 42)
    assert calls == [(5, 7)]
