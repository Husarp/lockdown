"""Read the URL of the active browser tab with Windows UI Automation (no browser extension needed).

Chromium browsers (Chrome, Edge, Brave, Opera, Vivaldi): the address bar is an Edit control whose class
name ends with "OmniboxViewViews". Firefox: a ComboBox with AutomationId "urlbar-input".
Both are language-independent. Must be called from a thread wrapped in UIAutomationInitializerInThread.
"""
import ctypes
from ctypes import wintypes

import uiautomation as auto

BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe", "vivaldi.exe"}
SEARCH_DEPTH = 10
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_cache: dict[int, auto.Control] = {}   # window handle -> its address bar control


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        _kernel32.CloseHandle(handle)


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


def foreground_browser_url() -> str | None:
    """URL/text in the address bar of the foreground browser window, or None if no browser is in front."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd or _process_name(hwnd) not in BROWSERS:
        return None
    for attempt in range(2):
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


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
    _user32.GetLastInputInfo(ctypes.byref(info))
    return (_kernel32.GetTickCount() - info.dwTime) / 1000
