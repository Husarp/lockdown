"""Bounded UI Automation probes of a browser window's chrome.

Only the probe thread owns COM controls. The family selectors beyond the
original Chrome and Firefox selectors are provisional pending Windows captures.
"""
import sys
import threading
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field

import uiautomation as auto

from monitor.address import classify_address
from monitor.browser_catalog import BROWSERS, BrowserIdentity, identify

SEARCH_DEPTH = 10
_MAX_CONTROLS = 160
_MAX_WINDOWS = 64
_READ_TIMEOUT = 0.3
_EDITS = {"EditControl", "ComboBoxControl"}
_ADDRESS_IDS = {"urlbar-input", "urlbar", "addressbar", "address-bar", "address-and-search-bar",
                "omnibox", "location-bar", "locationbar"}
_NAV_IDS = {"nav-bar", "navigation-bar", "navigation-toolbar", "address-toolbar", "omnibox-container"}
_TAB_IDS = {"tabbrowser-tabs", "tabstrip", "tab-strip", "vertical-tabs"}
_CONTENT_IDS = {"appcontent", "tabbrowser-tabpanels", "webview", "browser"}
_CONTENT_CLASSES = {"chrome_renderwidgethosthwnd", "internet explorer_server", "webview", "webview2"}
_TAB_URL_IDS = {"selected-tab-url", "tab-url"}


@dataclass(frozen=True)
class ChromeRead:
    url: str | None = None
    host: str | None = None
    tab_token: tuple | None = None
    window_class: str = "unknown"
    stage: str = "window"
    reason: str | None = None
    sampled: bool = True
    count: int | None = None


def _key(control, name):
    value = getattr(control, name, "")
    return value.casefold() if isinstance(value, str) else ""


def _window_class(window):
    value = _key(window, "ClassName")
    if value in {"chrome_widgetwin_0", "chrome_widgetwin_1", "chrome_widgetwin_2"}:
        return "Chrome_WidgetWin_" + value[-1]
    if value == "mozillawindowclass":
        return "MozillaWindowClass"
    if value in {"applicationframewindow", "winuidesktopwin32windowclass"}:
        return value
    return "other"


def _chrome(window):
    """Breadth-first scan of browser chrome; never descend into a page or renderer."""
    pending = deque([(window, 0, False)])
    visited = 0
    while pending and visited < _MAX_CONTROLS:
        control, depth, nav = pending.popleft()
        visited += 1
        kind = control.ControlTypeName
        aid = _key(control, "AutomationId")
        cls = _key(control, "ClassName")
        if depth and (kind == "DocumentControl" or aid in _CONTENT_IDS or cls in _CONTENT_CLASSES):
            continue
        nav = nav or aid in _NAV_IDS or cls in {"navigationtoolbar", "locationbarview", "omniboxcontainer"}
        yield control, kind, aid, cls, nav
        if depth < SEARCH_DEPTH and kind not in {"EditControl", "TabItemControl", "HyperlinkControl"}:
            child = control.GetFirstChildControl()
            while child is not None and visited + len(pending) < _MAX_CONTROLS:
                pending.append((child, depth + 1, nav))
                child = child.GetNextSiblingControl()


def _stage(kind, aid, cls, nav, family):
    if kind not in _EDITS:
        return None
    if ("search" in aid and aid != "address-and-search-bar") or "searchbox" in cls or "find" in aid:
        return None
    if ((family == "mozilla" and kind == "ComboBoxControl" and aid == "urlbar-input")
            or (family == "chromium" and kind == "EditControl" and cls.endswith("omniboxviewviews"))):
        return "fast-selector"
    if aid in _ADDRESS_IDS:
        return "address-id"
    if nav:
        return "navigation-value"
    return None


def _address(control, stage):
    if getattr(control, "IsOffscreen", False):
        return None, None, "bar-hidden"
    if getattr(control, "HasKeyboardFocus", False):
        return None, None, "address-edit"
    get_value = getattr(control, "GetValuePattern", None)
    pattern = get_value() if get_value else control.GetPattern(auto.PatternId.ValuePattern)
    url, host = classify_address(pattern.Value if pattern is not None else None)
    if url and stage in {"navigation-value", "selected-tab"}:
        # A generic edit needs stronger evidence than a browser address field.
        scheme, sep, _rest = url.partition(":")
        if not (sep and (host is None or scheme.casefold() in {"http", "https"})):
            return None, None, "invalid-value"
    return url, host, None if url else "invalid-value"


def _tab_items(strip):
    child = strip.GetFirstChildControl()
    for _ in range(_MAX_CONTROLS):
        if child is None:
            break
        if child.ControlTypeName == "TabItemControl":
            yield child
        child = child.GetNextSiblingControl()


def _selected(strips):
    selected = []
    for strip in strips:
        get_selection = getattr(strip, "GetSelectionPattern", None)
        pattern = get_selection() if get_selection else None
        if pattern is not None:
            selected.extend(item for item in pattern.GetSelection() if item.ControlTypeName == "TabItemControl")
        else:
            for item in _tab_items(strip):
                item_pattern = item.GetSelectionItemPattern()
                if item_pattern is not None and item_pattern.IsSelected:
                    selected.append(item)
                if len(selected) > 1:
                    return None, None
        if len(selected) > 1:
            return None, None
    if len(selected) != 1:
        return None, None
    item = selected[0]
    runtime = item.GetRuntimeId()
    token = tuple(runtime) if runtime else None
    return item, token


class _WindowControls:
    def __init__(self):
        self.generation = None
        self.title = ""
        self.tab_token = None
        self.window_class = "unknown"
        self.strips = []
        self.bar = None
        self.stage = "chrome"


class _Probe:
    def __init__(self):
        self.windows = OrderedDict()

    def read(self, hwnd, generation, browser, title, sample=True, rebuild=False):
        state = self.windows.get(hwnd) or _WindowControls()
        if state.generation != generation or rebuild:
            state = _WindowControls()
        state.generation = generation
        self.windows[hwnd] = state
        self.windows.move_to_end(hwnd)
        if len(self.windows) > _MAX_WINDOWS:
            self.windows.popitem(last=False)

        # A cached strip gives us a same-title switch signal even between address
        # samples. If a proxy was invalidated, reacquire chrome in this call.
        try:
            item, token = _selected(state.strips)
            changed = not title or title != state.title or token != state.tab_token
            if not sample and not changed:
                return ChromeRead(tab_token=token, window_class=state.window_class, sampled=False)
            if state.bar is not None and not changed:
                url, host, reason = _address(state.bar, state.stage)
                if url or reason == "address-edit":
                    return ChromeRead(url, host, token, state.window_class, state.stage, reason)
        except Exception:
            pass

        try:
            return self._discover(hwnd, browser, title, state)
        except Exception as exc:
            state.bar = None
            state.strips = []
            state.tab_token = None
            return ChromeRead(window_class=state.window_class, stage="chrome", reason=_automation_error(exc))

    def _discover(self, hwnd, browser, title, state):
        state.title = title
        state.bar = None
        window = auto.ControlFromHandle(hwnd)
        if window is None:
            return ChromeRead(reason="window-unavailable")
        state.window_class = _window_class(window)
        candidates = {stage: [] for stage in ("fast-selector", "address-id", "navigation-value")}
        state.strips = []
        if callable(getattr(window, "GetFirstChildControl", None)):
            for control, kind, aid, cls, nav in _chrome(window):
                if kind == "TabControl" or aid in _TAB_IDS or cls.endswith("tabstrip"):
                    state.strips.append(control)
                stage = _stage(kind, aid, cls, nav, browser.family)
                if stage:
                    candidates[stage].append(control)
        else:
            # Compatibility with selector-only UIA adapters. The native UIA
            # object supports chrome traversal and uses the scoped path above.
            if browser.family == "mozilla":
                bar = window.ComboBoxControl(AutomationId="urlbar-input", searchDepth=SEARCH_DEPTH)
            else:
                bar = window.EditControl(searchDepth=SEARCH_DEPTH,
                                         Compare=lambda c, _depth: c.ClassName.endswith("OmniboxViewViews"))
            if bar.Exists(0, 0):
                candidates["fast-selector"].append(bar)

        try:
            item, token = _selected(state.strips)
        except Exception:
            item, token = None, None
            state.strips = []
        state.tab_token = token
        stage, reason = "chrome", "bar-unavailable"
        for candidate_stage, controls in candidates.items():
            matches = []
            for control in controls:
                try:
                    url, host, failure = _address(control, candidate_stage)
                except Exception as exc:
                    url, host, failure = None, None, _automation_error(exc)
                if url or failure == "address-edit":
                    matches.append((control, url, host, failure))
                stage, reason = candidate_stage, failure
            if len(matches) == 1:
                control, url, host, failure = matches[0]
                if candidate_stage != "navigation-value" and len(controls) == 1:
                    state.bar, state.stage = control, candidate_stage
                return ChromeRead(url, host, token, state.window_class, candidate_stage, failure)
            if len(matches) > 1:
                return ChromeRead(tab_token=token, window_class=state.window_class,
                                  stage=candidate_stage, reason="ambiguous-controls")
        if item is not None and _key(item, "AutomationId") in _TAB_URL_IDS:
            url, host, failure = _address(item, "selected-tab")
            if url:
                return ChromeRead(url, host, token, state.window_class, "selected-tab")
            stage, reason = "selected-tab", failure
        return ChromeRead(tab_token=token, window_class=state.window_class, stage=stage, reason=reason)

    def count(self, hwnd, browser):
        window = auto.ControlFromHandle(hwnd)
        if window is None or browser.family != "chromium":
            return ChromeRead()
        counts = [sum(1 for _ in _tab_items(control)) for control, kind, aid, cls, _ in _chrome(window)
                  if kind == "TabControl" or aid in _TAB_IDS or cls.endswith("tabstrip")]
        counts = [count for count in counts if count]
        return ChromeRead(count=counts[0] if len(counts) == 1 else None)


def _automation_error(exc):
    if isinstance(exc, TimeoutError):
        return "uia-timeout"
    hresult = getattr(exc, "hresult", 0) or 0
    return {
        0x80040201: "element-unavailable",
        0x80010108: "provider-disconnected",
        0x80131505: "uia-timeout",
    }.get(hresult & 0xffffffff, "uia-error")


@contextmanager
def _com_probe():
    if sys.platform == "win32":
        import comtypes

        # All probe threads use the MTA. uiautomation's shared client and
        # walker can then service calls from any probe without an STA pump.
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            yield
        finally:
            comtypes.CoUninitialize()
    else:
        yield


@dataclass
class _Request:
    method: str
    args: tuple
    done: threading.Event = field(default_factory=threading.Event)
    result: ChromeRead | None = None
    abandoned: bool = False


class _ProbeLane:
    """One lazy COM thread per reader. A stuck provider cannot queue up more COM work."""
    def __init__(self):
        self._condition = threading.Condition()
        self._thread = None
        self._request = None

    def call(self, method, *args):
        with self._condition:
            if self._request is not None:
                return ChromeRead(stage="uia", reason="uia-busy")
            request = self._request = _Request(method, args)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="browser-uia", daemon=True)
                self._thread.start()
            self._condition.notify()
        if not request.done.wait(_READ_TIMEOUT):
            with self._condition:
                request.abandoned = True
            return ChromeRead(stage="uia", reason="uia-timeout")
        return request.result

    def _run(self):
        try:
            with _com_probe():
                probe = _Probe()
                try:
                    self._serve(probe)
                finally:
                    probe.windows.clear()
        except Exception:
            with self._condition:
                if request := self._request:
                    request.result = ChromeRead(reason="uia-init-error")
                    self._request = None
                    request.done.set()
                self._thread = None

    def _serve(self, probe):
        while True:
            with self._condition:
                if not self._condition.wait_for(lambda: self._request is not None, timeout=30):
                    self._thread = None
                    return
                request = self._request
            try:
                result = getattr(probe, request.method)(*request.args)
            except Exception as exc:
                result = ChromeRead(reason=_automation_error(exc))
            with self._condition:
                if request.abandoned:
                    probe.windows.clear()
                request.result = result
                self._request = None
                request.done.set()


_legacy = threading.local()


class _LegacyCache:
    def clear(self):
        _legacy.lane = None


_cache = _LegacyCache()  # reset hook for callers testing the legacy entry points


def _lane():
    if getattr(_legacy, "lane", None) is None:
        _legacy.lane = _ProbeLane()
    return _legacy.lane


def _legacy_window(hwnd):
    from blocker.apps import start_time
    from monitor import win

    pid = win.window_pid(hwnd)
    path = win.pid_path(pid) if pid else ""
    if path:
        exe = win.exe_name(path)
    else:
        exe = win.snapshot_name(pid) if pid else ""
        if not exe:
            front, name = win.foreground()
            exe = name if front == hwnd else ""
    return identify(exe, path), (pid, start_time(pid) if pid else None), win.window_title(hwnd)


def browser_url(hwnd: int) -> str | None:
    """Read the active tab's address. Legacy callers supply their own fallback."""
    from monitor import win

    browser, generation, title = _legacy_window(hwnd)
    if browser:
        address = _lane().call("read", hwnd, generation, browser, title).url
        if win.window_pid(hwnd) == generation[0] and win.window_title(hwnd) == title:
            return address
    return None


def tab_count(hwnd: int) -> int | None:
    """Current Chromium tab count for last-tab preservation, when known."""
    browser, _, _ = _legacy_window(hwnd)
    if browser and browser.family == "chromium":
        return _lane().call("count", hwnd, browser).count
    return None
