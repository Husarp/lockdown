"""Synthetic browser chrome trees. Fork selectors await Windows UIA captures."""
import threading
import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

import paths
from monitor import browser_catalog, browser_url, tab_reader, win
from monitor.address import classify_address
from resident import diag

YT = "https://www.youtube.com/watch?v=private-video"
TITLE = "Private page title - YouTube - Google Chrome"
OTHER = "https://example.com/other"
HWND = 99


class Node:
    def __init__(self, kind, aid="", cls="", *, value=None, token=None, selected=False, children=()):
        self.ControlTypeName = kind
        self.AutomationId = aid
        self.ClassName = cls
        self.value = value
        self.token = token
        self.selected = selected
        self.IsOffscreen = False
        self.HasKeyboardFocus = False
        self.error = None
        self.values_read = 0
        self.sibling = None
        self.set_children(*children)

    def set_children(self, *children):
        self.children = children
        for i, child in enumerate(children):
            child.sibling = children[i + 1] if i + 1 < len(children) else None

    def GetFirstChildControl(self):
        return self.children[0] if self.children else None

    def GetNextSiblingControl(self):
        return self.sibling

    def GetValuePattern(self):
        self.values_read += 1
        if self.error:
            raise self.error
        return SimpleNamespace(Value=self.value)

    def GetRuntimeId(self):
        return self.token

    def GetSelectionItemPattern(self):
        return SimpleNamespace(IsSelected=self.selected)


class Desktop:
    def __init__(self, monkeypatch, tmp_path):
        self.now = 100.0
        self.file = tmp_path / "chrome.exe"
        self.file.touch()
        self.file_version = "150.1.0.0"
        self.exe = self.file.name
        self.pid = 43
        self.started = 1000
        self.title = TITLE
        self.bar = Node("EditControl", cls="OmniboxViewViews", value=YT)
        self.tab = Node("TabItemControl", token=(1, 2), selected=True)
        self.strip = Node("TabControl", children=[self.tab])
        self.root = Node("WindowControl", cls="Chrome_WidgetWin_1", children=[self.strip, self.bar])
        self.windows = {HWND: self}
        self.calls = []
        self.reader = tab_reader.TabReader()
        self.log = tmp_path / "lockdown.log"
        monkeypatch.setattr(paths, "LOG_PATH", self.log)
        monkeypatch.setattr(diag, "path", lambda: tmp_path / "resident.log")
        monkeypatch.setattr(diag, "_app_log", lambda message: None)
        monkeypatch.setattr(win, "window_pid", lambda hwnd: self.windows[hwnd].pid if hwnd in self.windows else 0)
        monkeypatch.setattr(win, "pid_path", lambda pid: str(self.file))
        monkeypatch.setattr(win, "window_title", lambda hwnd: self.windows[hwnd].title)
        monkeypatch.setattr(tab_reader, "_pid_start", lambda pid: self.started)
        monkeypatch.setattr(tab_reader.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(browser_url.auto, "ControlFromHandle", self.control_from_handle, raising=False)
        monkeypatch.setattr(browser_catalog, "_read_file_version", lambda path: self.version_info(path))
        for cache in (tab_reader._recent, tab_reader._version_reports, tab_reader._window_reports):
            cache.clear()

    def control_from_handle(self, hwnd):
        self.calls.append((hwnd, threading.get_ident()))
        return self.windows[hwnd].root

    def version_info(self, path):
        return browser_catalog._FileVersion(version=self.file_version)

    def read(self, delta=0, *, hwnd=HWND, role="foreground", refresh=False):
        self.now += delta
        return self.reader.read(hwnd, self.exe, role=role, refresh=refresh)


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    return Desktop(monkeypatch, tmp_path)


@pytest.mark.parametrize(("exe", "product", "family", "file_product"), [
    ("chrome.exe", "Chrome", "chromium", "Google Chrome"),
    ("msedge.exe", "Edge", "chromium", "Microsoft Edge"),
    ("brave.exe", "Brave", "chromium", "Brave Browser"),
    ("firefox.exe", "Firefox", "mozilla", "Firefox"),
    ("opera.exe", "Opera", "chromium", "Opera Internet Browser"),
    ("opera.exe", "Opera GX", "chromium", "Opera GX"),
    ("vivaldi.exe", "Vivaldi", "chromium", "Vivaldi"),
    ("arc.exe", "Arc", "chromium", "Arc"),
    ("zen.exe", "Zen", "mozilla", "Zen Browser"),
    ("librewolf.exe", "LibreWolf", "mozilla", "LibreWolf"),
    ("waterfox.exe", "Waterfox", "mozilla", "Waterfox"),
    ("chromium.exe", "Chromium", "chromium", "Chromium"),
    ("chromium.exe", "Ungoogled Chromium", "chromium", "ungoogled-chromium"),
    ("branded.exe", "Thorium", "chromium", "Thorium"),
    ("cromite.exe", "Cromite", "chromium", "Cromite"),
    ("supermium.exe", "Supermium", "chromium", "Supermium"),
    ("iron.exe", "SRWare Iron", "chromium", "SRWare Iron"),
])
def test_browser_matrix_with_alternate_chrome_and_fullscreen_title(desktop, tmp_path, monkeypatch,
                                                                    exe, product, family, file_product):
    desktop.file = tmp_path / exe
    desktop.file.touch()
    desktop.exe = exe
    original = "chrome.exe" if family == "chromium" else "firefox.exe"
    monkeypatch.setattr(desktop, "version_info", lambda _: browser_catalog._FileVersion(file_product, original, "3.2.1.0"))
    desktop.bar.ClassName = "RenamedAddressView"
    desktop.bar.AutomationId = "address-bar" if family == "chromium" else "urlbar-input"
    desktop.bar.ControlTypeName = "ComboBoxControl" if family == "chromium" else "EditControl"
    desktop.title = f"Music clip - YouTube Music — {file_product} (Private)"
    desktop.bar.value = "https://music.youtube.com/watch?v=private-video"
    obs = desktop.read()
    assert (obs.exe, obs.product, obs.browser.family, obs.version, obs.path) == (
        exe, product, family, "3.2.1.0", str(desktop.file))
    assert (obs.host, obs.source, obs.selector_stage) == ("music.youtube.com", "uia", "address-id")

    desktop.root.set_children(desktop.strip)
    desktop.title = f"Next clip - YouTube Music — {file_product} (Private)"
    obs = desktop.read(0.5)
    assert (obs.host, obs.source, obs.actionable) == ("music.youtube.com", "title", False)


def test_opera_installations_with_shared_basename(tmp_path, monkeypatch):
    regular, gx = [tmp_path / part / "opera.exe" for part in ("Opera", "Opera GX")]
    for path in regular, gx:
        path.parent.mkdir()
        path.touch()
    monkeypatch.setattr(browser_catalog, "_read_file_version", lambda path:
                        browser_catalog._FileVersion("Opera Internet Browser", "opera.exe", "5.2.4.0"))
    assert browser_catalog.identify("opera.exe", regular).product == "Opera"
    assert browser_catalog.identify("opera.exe", gx).product == "Opera GX"


def test_file_version_resource_uses_fixed_file_version_and_product(tmp_path, monkeypatch):
    import win32api

    path = str(tmp_path / "opera.exe")
    queries = {
        "\\": {"FileVersionMS": (110 << 16) + 3, "FileVersionLS": (512 << 16) + 8},
        r"\VarFileInfo\Translation": [(0x409, 0x4b0)],
        r"\StringFileInfo\040904b0\ProductName": "Opera GX",
        r"\StringFileInfo\040904b0\OriginalFilename": "opera.exe",
    }
    monkeypatch.setattr(win32api, "GetFileVersionInfo", lambda file, query: queries[query], raising=False)
    assert browser_catalog.identify("opera.exe", path) == browser_catalog.BrowserIdentity(
        "opera.exe", "Opera GX", "chromium", "110.3.512.8", path)


def test_embedding_application_never_probed(desktop, monkeypatch, tmp_path):
    desktop.file = tmp_path / "chat.exe"
    desktop.exe = desktop.file.name
    monkeypatch.setattr(desktop, "version_info", lambda _: browser_catalog._FileVersion("Chromium", "chat.exe", "1.0.0.0"))
    assert desktop.read().failure_reason == "unknown-browser"
    assert desktop.calls == []


@pytest.mark.parametrize("stage", ["fast-selector", "address-id", "navigation-value", "selected-tab"])
def test_scoped_probes_ignore_document_inputs_and_search_boxes(desktop, stage):
    doc_edit = Node("EditControl", "address-bar", "OmniboxViewViews", value="https://document.example/")
    doc = Node("DocumentControl", children=[doc_edit])
    search = Node("EditControl", "toolbar-search", value="https://search.example/")
    desktop.bar.AutomationId = "address-bar" if stage == "address-id" else ""
    desktop.bar.ClassName = "OmniboxViewViews" if stage == "fast-selector" else "NewClass"
    chrome = desktop.bar
    if stage == "navigation-value":
        chrome = Node("ToolBarControl", "nav-bar", children=[search, desktop.bar])
    elif stage == "selected-tab":
        desktop.tab.AutomationId = "selected-tab-url"
        desktop.tab.value = YT
        chrome = search
    desktop.root.set_children(doc, desktop.strip, chrome)
    obs = desktop.read()
    assert (obs.url, obs.selector_stage) == (YT, stage)
    desktop.tab.value = None
    desktop.title = "Unrelated page - Google Chrome"
    desktop.root.set_children(doc, desktop.strip, Node("ToolBarControl", "nav-bar", children=[search]))
    obs = desktop.read(2, refresh=True)
    assert obs.url is None and not obs.actionable
    assert doc_edit.values_read == search.values_read == 0


@pytest.mark.parametrize(("value", "host"), [("www.YouTube.com/watch?v=x", "youtube.com"),
                                             ("http://internal", "internal"),
                                             ("[2001:db8::1]:80/", "2001:db8::1"),
                                             ("https://www.bücher.example/", "xn--bcher-kva.example."),
                                             ("https://user:password@real.example/", "real.example")])
def test_normalizes_committed_urls(value, host):
    assert classify_address(value) == (value, host.rstrip("."))


@pytest.mark.parametrize("value", [" ", "Suchen", "Search or type a URL", "https://", "ftp://example.com",
                                   "typed@example.com", "example.com:bad", "connection\\bad.com"])
def test_invalid_addresses(value):
    assert classify_address(value) == (None, None)


@pytest.mark.parametrize("url", ["about:blank", "chrome://newtab/", "edge://settings", "file:///C:/file.html",
                                 "data:text/html,a", "moz-extension://internal/configuration"])
def test_internal_navigation_clears_stale_title(desktop, url):
    assert desktop.read().host == "youtube.com"
    desktop.bar.value = url
    obs = desktop.read(2)
    assert (obs.host, obs.url, obs.read_failed, obs.actionable) == (None, url, False, False)
    desktop.bar.IsOffscreen = True
    obs = desktop.read(12)
    assert (obs.host, obs.url, obs.source) == (None, None, "unknown")


def test_in_progress_address_edit_never_reports_typed_site(desktop):
    assert desktop.read().url == YT
    desktop.bar.HasKeyboardFocus = True
    desktop.bar.value = OTHER
    obs = desktop.read(refresh=True)
    assert (obs.url, obs.failure_reason, obs.actionable) == (YT, "address-edit", False)
    desktop.bar.HasKeyboardFocus = False
    assert desktop.read(refresh=True).url == OTHER


def test_same_title_switch_bypasses_sampling_and_invalidates_confirmed_host(desktop):
    desktop.title = "Clip - YouTube - Personal - Google Chrome"
    first = desktop.read()
    assert first.tab_token == (1, 2)
    desktop.bar.value = OTHER
    assert desktop.read(0.4).url == YT
    assert desktop.bar.values_read == 1
    next_tab = Node("TabItemControl", token=(1, 3), selected=True)
    desktop.strip.set_children(next_tab)
    obs = desktop.read(0.1)
    assert (obs.tab_token, obs.url, obs.host) == ((1, 3), OTHER, "example.com")
    desktop.strip.set_children(desktop.tab)
    desktop.bar.IsOffscreen = True
    obs = desktop.read(0.1)
    assert (obs.tab_token, obs.host, obs.actionable) == ((1, 2), None, False)


def test_refresh_bypasses_cached_address_and_recovers_stale_proxy(desktop):
    assert desktop.read().url == YT
    desktop.bar.error = RuntimeError("stale control on a private page")
    new_bar = Node("EditControl", "addressbar", value=OTHER)
    desktop.root.set_children(desktop.strip, new_bar)
    assert desktop.read(2).url == OTHER
    new_bar.value = YT
    assert desktop.read(0.1).url == OTHER
    assert desktop.read(refresh=True).url == YT


def test_only_one_live_address_control_can_establish_the_host(desktop):
    duplicate = Node("EditControl", cls="OmniboxViewViews", value=OTHER)
    duplicate.IsOffscreen = True
    desktop.root.set_children(desktop.strip, desktop.bar, duplicate)
    assert desktop.read().url == YT
    duplicate.IsOffscreen = False
    obs = desktop.read(2, refresh=True)
    assert obs.failure_reason == "ambiguous-controls"
    assert not obs.actionable


def test_legacy_entry_points_use_scoped_chrome(desktop):
    doc_tab = Node("TabItemControl")
    doc = Node("DocumentControl", children=[Node("TabControl", children=[doc_tab, Node("TabItemControl")])])
    desktop.root.set_children(doc, desktop.strip, desktop.bar)
    browser_url._cache.clear()
    assert browser_url.browser_url(HWND) == YT
    assert browser_url.tab_count(HWND) == 1
    assert {"chrome.exe", "firefox.exe", "opera.exe", "arc.exe", "zen.exe"} <= browser_url.BROWSERS


def test_generation_and_closed_hwnd_invalidate_history(desktop):
    desktop.title = "Clip - YouTube - Personal - Google Chrome"
    assert desktop.read().host == "youtube.com"
    desktop.pid += 1
    desktop.bar.IsOffscreen = True
    assert desktop.read().host is None
    desktop.bar.IsOffscreen = False
    assert desktop.read(refresh=True).host == "youtube.com"
    desktop.started += 1  # PID recycled too
    desktop.bar.IsOffscreen = True
    assert desktop.read().host is None
    del desktop.windows[HWND]
    assert desktop.read().failure_reason == "window-unavailable"
    assert tab_reader.recent_observations(10) == ()
    desktop.windows[HWND] = desktop
    assert desktop.read().host is None


def test_recent_address_cap_and_safe_title_evidence(desktop):
    desktop.title = "Clip - YouTube - Personal - Google Chrome"
    first = desktop.read()
    desktop.bar.IsOffscreen = True
    obs = desktop.read(2)
    assert (obs.url, obs.source, obs.actionable) == (YT, "recent_same_title", True)
    assert obs.verified_at == first.observed_at
    obs = desktop.read(8)
    assert obs.url == YT
    obs = desktop.read(0.1)
    assert (obs.host, obs.url, obs.source) == ("youtube.com", None, "confirmed_title")
    desktop.root.set_children()  # chrome including its selected-tab identity hides during fullscreen
    obs = desktop.read(2)
    assert (obs.host, obs.source, obs.actionable) == ("youtube.com", "confirmed_title", False)
    desktop.title = "Next - YouTube Shorts - Google Chrome"
    assert desktop.read(0.1).source == "title"
    desktop.title = "A page mentioning YouTube - Google Chrome"
    assert desktop.read(0.1).host is None


@pytest.mark.parametrize("role", ["visible", "nonforeground"])
def test_secondary_window_backoff_and_recheck(desktop, role):
    hwnd = HWND + 1
    desktop.windows[hwnd] = desktop
    desktop.bar.IsOffscreen = True
    for _ in range(3):
        assert desktop.read(2, hwnd=hwnd, role=role).bar_hidden
    desktop.bar.IsOffscreen = False
    obs = desktop.read(3, hwnd=hwnd, role=role)
    assert obs.role == role
    if role == "visible":
        assert obs.source == "title"
        assert desktop.read(27, hwnd=hwnd, role=role).url == YT
    else:
        assert obs.url == YT


def test_blocked_uia_returns_on_deadline_and_drops_abandoned_result(desktop, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    call = desktop.control_from_handle

    def blocked(hwnd):
        entered.set()
        release.wait(3)
        return call(hwnd)

    monkeypatch.setattr(browser_url.auto, "ControlFromHandle", blocked)
    monkeypatch.setattr(browser_url, "_READ_TIMEOUT", 0.1)
    try:
        started = time.perf_counter()
        obs = desktop.read()
        assert entered.is_set() and time.perf_counter() - started < 1
        assert (obs.failure_reason, obs.actionable) == ("uia-timeout", False)
        assert desktop.read(2, refresh=True).failure_reason == "uia-busy"
    finally:
        release.set()
    desktop.bar.value = OTHER
    for _ in range(100):
        obs = desktop.read(0.5, refresh=True)
        if obs.url:
            break
        threading.Event().wait(0.01)
    assert obs.url == OTHER


def test_observations_plain_and_available_across_threads(desktop):
    obs = desktop.read()
    reader = tab_reader.TabReader()
    assert reader.read(HWND, desktop.exe, role="visible").host == "youtube.com"
    assert len({thread_id for _, thread_id in desktop.calls}) == 2
    assert all(thread_id != threading.get_ident() for _, thread_id in desktop.calls)
    desktop.now += 1
    found = []
    worker = threading.Thread(target=lambda: found.extend(tab_reader.recent_observations(2)))
    worker.start()
    worker.join()
    assert (found[0].hwnd, found[0].role, found[0].age_sec) == (HWND, "visible", 1)
    with pytest.raises(FrozenInstanceError):
        obs.host = OTHER
    assert tab_reader.recent_observations(0.5) == ()


def test_recent_snapshot_replaces_stale_observations_and_bounds_windows(desktop, monkeypatch):
    monkeypatch.setattr(tab_reader, "_MAX_RECENT", 2)
    for hwnd in (HWND, HWND + 1, HWND + 2):
        desktop.windows[hwnd] = desktop
        desktop.read(1, hwnd=hwnd, role="visible")
    assert [obs.hwnd for obs in tab_reader.recent_observations(10)] == [HWND + 2, HWND + 1]
    desktop.bar.value = OTHER
    desktop.read(1, hwnd=HWND + 1, refresh=True)
    assert [obs.host for obs in tab_reader.recent_observations(10)] == ["example.com", "youtube.com"]


def test_sustained_version_and_window_diagnostics_recover_without_browsing_data(desktop):
    desktop.windows[HWND + 1] = desktop
    desktop.title = "Private page title - YouTube - Personal - Google Chrome"
    assert desktop.read(role="nonforeground").host == "youtube.com"
    desktop.bar.error = RuntimeError("typed-account@example.com")
    for _ in range(12):
        desktop.read(2, role="nonforeground")
        desktop.read(2, hwnd=HWND + 1, role="nonforeground")
    log = desktop.log.read_text()
    assert log.count("scope=version") == 1
    assert log.count("scope=window") == 2
    assert "reason=uia-error" in log
    assert "Browser tab degraded" in log and "fallback=recent_same_title" in log
    desktop.bar.error = None
    desktop.bar.value = OTHER
    assert desktop.read(2, role="nonforeground").host == "example.com"
    assert desktop.log.read_text().count("scope=recovery") == 1
    desktop.file.write_bytes(b"updated")
    desktop.file_version = "151.0.0.0"
    desktop.bar.IsOffscreen = True
    for _ in range(6):
        desktop.read(2, role="nonforeground")
    log = desktop.log.read_text()
    assert log.count("scope=version") == 2 and "version=151.0.0.0" in log
    assert log.count("scope=window") == 2
    assert not any(private in log for private in (TITLE, "Private page", YT, OTHER, "example.com",
                                                  str(desktop.file), "private-video"))


def test_sustained_failures_include_autoplay_title_transitions(desktop):
    desktop.root.set_children()
    for video in range(4):
        desktop.title = f"Video {video} - YouTube - Google Chrome"
        assert desktop.read(2).source == "title"
    log = desktop.log.read_text()
    assert log.count("scope=version") == 1 and "failure_sec=4" in log
    assert "Video" not in log
