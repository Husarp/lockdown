"""Read the URL of a browser window's active tab with Windows UI Automation (no browser extension needed).

Chromium browsers (Chrome, Edge, Brave, Opera, Vivaldi): the address bar is an Edit control whose class
name ends with "OmniboxViewViews". Firefox: a ComboBox with AutomationId "urlbar-input".
Both are language-independent. Must be called from a thread wrapped in UIAutomationInitializerInThread.
"""
import uiautomation as auto

BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe", "vivaldi.exe"}
SEARCH_DEPTH = 10

_cache: dict[int, auto.Control] = {}   # window handle -> its address bar control


def _find_address_bar(hwnd: int) -> auto.Control | None:
    window = auto.ControlFromHandle(hwnd)
    if window is None:
        return None
    if window.ClassName == "MozillaWindowClass":
        bar = window.ComboBoxControl(AutomationId="urlbar-input", searchDepth=SEARCH_DEPTH)
    else:
        bar = window.EditControl(searchDepth=SEARCH_DEPTH,
                                 Compare=lambda c, _depth: c.ClassName.endswith("OmniboxViewViews"))
    return bar if bar.Exists(0, 0) else None


def tab_count(hwnd: int) -> int | None:
    """How many tabs a Chromium browser window has open, or None when it can't tell (e.g. Firefox). Used to keep
    the window open: closing its only tab would close the whole window, so a fresh tab is opened first."""
    window = auto.ControlFromHandle(hwnd)
    if window is None or window.ClassName == "MozillaWindowClass":
        return None
    try:
        strip = window.TabControl(searchDepth=SEARCH_DEPTH)   # the tab strip
        if not strip.Exists(0, 0):
            return None
        return sum(1 for c in strip.GetChildren() if c.ControlTypeName == "TabItemControl")
    except Exception:
        return None


def browser_url(hwnd: int) -> str | None:
    """URL/text in the address bar of a browser window, or None."""
    for _attempt in range(2):
        bar = _cache.get(hwnd) or _find_address_bar(hwnd)
        if bar is None:
            return None
        try:
            value = bar.GetValuePattern().Value
            _cache[hwnd] = bar
            return value
        except Exception:  # stale control (window changed) - search again once
            _cache.pop(hwnd, None)
    return None
