"""Desktop helpers for the tray agent: foreground app, the window in front on each monitor, idle time, closing an
app's windows politely."""
import ctypes
import os
from ctypes import wintypes
from typing import NamedTuple

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_CLOSE = 0x0010
SW_MINIMIZE = 6

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def pid_path(pid: int) -> str:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        return buf.value if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        _kernel32.CloseHandle(handle)


def exe_name(path: str) -> str:
    return path.rsplit("\\", 1)[-1].lower()


def process_name(hwnd: int) -> str:
    """Lowercase exe name of the process owning a window ('' if unknown)."""
    return exe_name(pid_path(window_pid(hwnd)))


def foreground_process() -> tuple[int, str, str]:
    """(window handle, lowercase exe name, exe path or '') of the foreground window; (0, '', '') if none.

    The path needs OpenProcess, which a game protected by anti-cheat (or one running elevated under some
    policies) can refuse - then the name comes from the process list, which needs no access to the process.
    Before, that window simply had no name, and its time was never counted."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", ""
    pid = content_pid(hwnd)   # (a Store app: the app inside its frame, not ApplicationFrameHost.exe)
    path = pid_path(pid)
    return hwnd, exe_name(path) if path else snapshot_name(pid), path


DESKTOP_READOBJECTS = 0x0001


def session_locked() -> bool:
    """Is the workstation locked (or the secure desktop up - UAC, Ctrl+Alt+Del)? Then the input desktop is
    Winlogon's and this session can't open it. That is what stops time being counted - no keyboard or mouse
    input does not (a video, a cutscene, a game played with a controller)."""
    desk = _user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not desk:
        return True
    _user32.CloseDesktop(desk)
    return False


def snapshot_name(pid: int) -> str:
    """Lowercase exe name of a process from the process list ('' if it isn't there)."""
    from blocker.apps import list_processes
    return next((name for p, name in list_processes() if p == pid), "") if pid else ""


def foreground() -> tuple[int, str]:
    """(window handle, lowercase exe name) of the foreground window; (0, '') if none (e.g. locked)."""
    hwnd, exe, _path = foreground_process()
    return hwnd, exe


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


_user32.MonitorFromWindow.restype = wintypes.HANDLE   # a 64-bit handle: don't let ctypes cut it to an int
_user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]


# SHQueryUserNotificationState: what Windows itself tells an app about interrupting right now.
QUNS_PRESENTATION_MODE, QUNS_QUIET_TIME = 4, 6


def do_not_disturb() -> bool:
    """Is Windows' Do not disturb (focus assist / quiet hours) on, or is a presentation running? Both mean you
    asked not to be interrupted, so Lockdown's reminders wait for it to be over."""
    state = ctypes.c_int()
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
    except Exception:
        return False
    return state.value in (QUNS_PRESENTATION_MODE, QUNS_QUIET_TIME)


def is_fullscreen() -> bool:
    """Is a full-screen app (a game, a video) in front? Not the desktop, not Lockdown itself."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd or window_pid(hwnd) == os.getpid():
        return False
    cls = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return False
    rect = wintypes.RECT()
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    monitor = _user32.MonitorFromWindow(hwnd, 2)   # MONITOR_DEFAULTTONEAREST
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)) or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    m = info.rcMonitor
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom


def top_windows() -> list[tuple[int, str, str]]:
    """(hwnd, title, exe path) of visible top-level windows with a title."""
    found = []

    def callback(hwnd, _):
        if _user32.IsWindowVisible(hwnd) and _user32.GetWindowTextLengthW(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            _user32.GetWindowTextW(hwnd, buf, 512)
            found.append((hwnd, buf.value, pid_path(window_pid(hwnd))))
        return True

    _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return found


# ---------- the window in front on every monitor (0.84.2) ----------
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x20, 0x80, 0x40000
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
LWA_COLORKEY, LWA_ALPHA = 0x1, 0x2
NULLREGION, SIMPLEREGION, COMPLEXREGION = 1, 2, 3   # GetWindowRgnBox (0: the window has no region)
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
# The desktop and the taskbars are windows too, always on screen - they hide nothing and are nothing in use.
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
# A Store (UWP) app's window is a frame owned by ApplicationFrameHost.exe; the app itself is its CoreWindow child.
UWP_FRAME, UWP_CONTENT = "ApplicationFrameWindow", "Windows.UI.Core.CoreWindow"

# A window is in front while you can see enough of it - what windows above it don't cover:
FRONT_VISIBLE_SHARE = 0.5     # at least half of it (of its part on screen), or
LARGE_VISIBLE_SHARE = 0.25    # a quarter of a monitor or more (a video left uncovered in a maximized browser), and
MIN_VISIBLE_SHARE = 0.01      # never less than 1 % of a monitor (a helper window of a minimized game, a tooltip)
# Alpha (0-255) of a see-through window (WS_EX_LAYERED): it hides what is under it only when nearly opaque, and it
# is not itself "in front" when you can hardly see it.
OPAQUE_ALPHA, INVISIBLE_ALPHA = 230, 12
UNKNOWN_ALPHA = -1            # per-pixel transparency or a colour key: can't tell how much of it you see

Rect = tuple[int, int, int, int]   # left, top, right, bottom


class WindowFacts(NamedTuple):
    """What decides whether a top-level window is in front, and whether it hides what is under it."""
    hwnd: int
    visible: bool = True
    minimized: bool = False
    cloaked: bool = False          # on another virtual desktop, or hidden by DWM
    exstyle: int = 0
    cls: str = ""
    rect: Rect = (0, 0, 0, 0)      # what you see of it on screen (without the invisible resize borders)
    alpha: int = 255               # see-through windows: their opacity, or UNKNOWN_ALPHA
    region: int = 0                # a window region (SetWindowRgn) cutting its shape: GetWindowRgnBox's kind
    region_box: Rect = (0, 0, 0, 0)   # a SIMPLEREGION's rectangle on screen


_MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                      wintypes.LPARAM)
# Our own handle on user32 for the calls below, so their argtypes never fight another module's (gui.screens).
_u32 = ctypes.WinDLL("user32")
_u32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, _MONITORENUMPROC, wintypes.LPARAM]
_u32.GetLayeredWindowAttributes.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
                                            ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)]
_u32.GetWindowRgnBox.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
_u32.FindWindowExW.restype = wintypes.HWND


def enum_windows() -> list[int]:
    """Every top-level window, in Z-order: the one on top first (EnumWindows' order)."""
    found = []

    def callback(hwnd, _):
        if hwnd:
            found.append(hwnd)
        return True

    _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return found


def monitors() -> list[Rect]:
    """Every monitor's rectangle (physical pixels, like the window rectangles). If Windows lists none, the whole
    virtual screen."""
    found = []

    def callback(_hmon, _hdc, rect, _data):
        r = rect.contents
        found.append((r.left, r.top, r.right, r.bottom))
        return True

    try:
        _u32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(callback), 0)
    except Exception:
        found = []
    if not found:
        x, y = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN), _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        found = [(x, y, x + _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), y + _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))]
    return [m for m in found if _area(m) > 0]


def _dwm_attr(hwnd: int, attr: int, value) -> bool:
    try:
        return ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), wintypes.DWORD(attr), ctypes.byref(value), ctypes.sizeof(value)) == 0
    except Exception:
        return False


def _alpha(hwnd: int) -> int:
    """Opacity of a layered window: its alpha, 255 if it set none, UNKNOWN_ALPHA for a colour key or per-pixel
    transparency (UpdateLayeredWindow, where GetLayeredWindowAttributes fails)."""
    key, alpha, flags = wintypes.DWORD(), ctypes.c_ubyte(), wintypes.DWORD()
    if not _u32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags)):
        return UNKNOWN_ALPHA
    if flags.value & LWA_COLORKEY:
        return UNKNOWN_ALPHA
    return alpha.value if flags.value & LWA_ALPHA else 255


def window_facts(hwnd: int) -> WindowFacts:
    """The facts about one window. Stops at the first fact that rules it out (most windows are invisible), so a
    tick stays cheap."""
    if not _user32.IsWindowVisible(hwnd):
        return WindowFacts(hwnd, visible=False)
    if _user32.IsIconic(hwnd):
        return WindowFacts(hwnd, minimized=True)
    cloaked = wintypes.DWORD()
    if _dwm_attr(hwnd, DWMWA_CLOAKED, cloaked) and cloaked.value:
        return WindowFacts(hwnd, cloaked=True)
    exstyle = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    cls = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, cls, 64)
    outer = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(outer)):
        return WindowFacts(hwnd, exstyle=exstyle, cls=cls.value)
    rect = wintypes.RECT()   # what you see: without the invisible resize borders, when DWM says
    if not _dwm_attr(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, rect):
        rect = outer
    box = wintypes.RECT()    # a region is relative to the window's outer corner
    region = _u32.GetWindowRgnBox(hwnd, ctypes.byref(box))
    return WindowFacts(hwnd, True, False, False, exstyle, cls.value, (rect.left, rect.top, rect.right, rect.bottom),
                       _alpha(hwnd) if exstyle & WS_EX_LAYERED else 255, region,
                       (outer.left + box.left, outer.top + box.top, outer.left + box.right, outer.top + box.bottom))


def can_be_in_front(f: WindowFacts) -> bool:
    """A window you could be looking at: shown, not minimized, not on another virtual desktop, not the desktop or
    a taskbar, not (nearly) fully see-through. Its style doesn't matter: a game or a browser marked as a tool
    window, "no activate" or click-through is still in front when you see it."""
    if not f.visible or f.minimized or f.cloaked or f.cls in SHELL_CLASSES:
        return False
    return not (f.exstyle & WS_EX_LAYERED and 0 <= f.alpha <= INVISIBLE_ALPHA)


def cover(f: WindowFacts) -> Rect | None:
    """What a window in front hides of the windows under it - None for nothing: a tooltip, a notification, a
    click-through overlay, a see-through window (unless nearly opaque), one whose region cuts it to nothing or to
    a shape that isn't a rectangle. Erring that way only ever counts more."""
    if not can_be_in_front(f):
        return None
    if f.exstyle & WS_EX_TOOLWINDOW and not f.exstyle & WS_EX_APPWINDOW:
        return None
    if f.exstyle & (WS_EX_NOACTIVATE | WS_EX_TRANSPARENT):
        return None
    if f.exstyle & WS_EX_LAYERED and f.alpha < OPAQUE_ALPHA:   # (UNKNOWN_ALPHA too)
        return None
    if f.region == SIMPLEREGION:
        return _clip(f.rect, f.region_box)
    if f.region in (NULLREGION, COMPLEXREGION):
        return None
    return f.rect


def _clip(r: Rect, to: Rect) -> Rect | None:
    left, top, right, bottom = max(r[0], to[0]), max(r[1], to[1]), min(r[2], to[2]), min(r[3], to[3])
    return (left, top, right, bottom) if left < right and top < bottom else None


def _area(r: Rect | None) -> int:
    return (r[2] - r[0]) * (r[3] - r[1]) if r else 0


def _covered(r: Rect, above: list[Rect]) -> int:
    """How much of `r` the rectangles `above` cover together (overlaps counted once)."""
    parts = [c for c in (_clip(a, r) for a in above) if c]
    if not parts:
        return 0
    xs = sorted({x for c in parts for x in (c[0], c[2])})
    total = 0
    for x0, x1 in zip(xs, xs[1:]):
        spans = sorted((c[1], c[3]) for c in parts if c[0] <= x0 and c[2] >= x1)
        height, start, end = 0, None, None
        for s, e in spans:
            if end is None or s > end:
                height += (end - start) if end is not None else 0
                start, end = s, e
            else:
                end = max(end, e)
        if end is not None:
            height += end - start
        total += height * (x1 - x0)
    return total


def seen_enough(r: Rect, above: list[Rect], screens: list[Rect]) -> bool:
    """Can you see enough of a window at `r` past the windows `above` it? Its part on every monitor counts - a
    window stretched over two monitors is seen on either."""
    on = seen = 0
    large = least = False
    for m in screens:
        part = _clip(r, m)
        if not part:
            continue
        visible = _area(part) - _covered(part, above)
        on, seen = on + _area(part), seen + visible
        large |= visible >= _area(m) * LARGE_VISIBLE_SHARE
        least |= visible >= _area(m) * MIN_VISIBLE_SHARE
    return least and (large or seen >= on * FRONT_VISIBLE_SHARE)


def pick_front(facts: list[WindowFacts], screens: list[Rect]) -> list[int]:
    """The windows in front, from facts in Z-order (top first): every window you could be looking at
    (can_be_in_front) of which you see enough (seen_enough) past what the windows above it hide (cover). The top
    one on each monitor always is; one behind a window that covers it is not; a minimized one or one on another
    virtual desktop never is. A small window on top (a calculator, a picture-in-picture video) doesn't hide the
    game under it, and neither does a see-through or click-through one - you still see the game."""
    above: list[Rect] = []
    front = []
    for f in facts:
        if not can_be_in_front(f):
            continue
        if seen_enough(f.rect, above, screens):
            front.append(f.hwnd)
        if hidden := cover(f):
            above.append(hidden)
    return front


def _facts_or_skip(hwnd: int) -> WindowFacts:
    """One window's facts; a window Windows errors on is left out (it hides nothing) - not every monitor's count."""
    try:
        return window_facts(hwnd)
    except Exception:
        return WindowFacts(hwnd, visible=False)


def content_pid(hwnd: int, cls: str | None = None) -> int:
    """The process whose content a window shows: a Store app's frame (owned by ApplicationFrameHost.exe) shows
    its CoreWindow child's process - the app."""
    pid = window_pid(hwnd)
    if cls is None:
        buf = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, buf, 64)
        cls = buf.value
    if cls == UWP_FRAME:
        try:
            child = _u32.FindWindowExW(hwnd, None, UWP_CONTENT, None)
        except Exception:
            child = 0
        if child:
            pid = window_pid(child) or pid
    return pid


def front_windows() -> list[tuple[int, int, str, str]]:
    """(hwnd, pid, lowercase exe name, exe path or '') of every window in front on any monitor (pick_front) -
    one EnumWindows per call. Like foreground_process, a window whose process can't be opened gets its name from
    the process list (read once per call, however many such windows)."""
    facts = [_facts_or_skip(h) for h in enum_windows()]
    by_hwnd = {f.hwnd: f for f in facts}
    out, paths = [], {}
    snapshot: dict | None = None
    for hwnd in pick_front(facts, monitors()):
        pid = content_pid(hwnd, by_hwnd[hwnd].cls)
        if not pid:
            continue
        if pid not in paths:
            path = pid_path(pid)
            if not path and snapshot is None:
                from blocker.apps import list_processes
                snapshot = dict(list_processes())
            paths[pid] = (exe_name(path) if path else (snapshot or {}).get(pid, ""), path)
        exe, path = paths[pid]
        if exe:
            out.append((hwnd, pid, exe, path))
    return out


def close_app(exe: str) -> int:
    """Ask every window of the app to close (like clicking X). Returns how many windows were asked."""
    count = 0
    for hwnd, _title, path in top_windows():
        if exe_name(path) == exe.lower():
            _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            count += 1
    return count


def minimize_app(exe: str) -> int:
    """Minimize every window of the app (it keeps running). Returns how many windows were minimized."""
    count = 0
    for hwnd, _title, path in top_windows():
        if exe_name(path) == exe.lower() and not _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_MINIMIZE)
            count += 1
    return count


def minimize_all() -> int:
    """Minimize every visible top-level window except Lockdown's own (used for a strict break)."""
    count = 0
    for hwnd, _title, _path in top_windows():
        if window_pid(hwnd) != os.getpid() and not _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_MINIMIZE)
            count += 1
    return count


def minimize_foreground() -> bool:
    """Minimize the window in front unless it's Lockdown - so opening something during a strict break sends it back."""
    hwnd = _user32.GetForegroundWindow()
    if hwnd and window_pid(hwnd) != os.getpid() and not _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_MINIMIZE)
        return True
    return False


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


VK_CONTROL, VK_MENU, VK_LEFT, VK_W = 0x11, 0x12, 0x25, 0x57
VK_SHIFT, VK_TAB, VK_T = 0x10, 0x09, 0x54
KEYEVENTF_KEYUP = 0x2


def press(hwnd: int, modifier: int, key: int) -> bool:
    """Press modifier+key (e.g. Ctrl+W) - only if `hwnd` is still the window in front."""
    return chord(hwnd, modifier, key)


def chord(hwnd: int, *vks: int) -> bool:
    """Press a key chord (e.g. Ctrl+Shift+Tab): hold all keys down, release in reverse. Only if `hwnd` is in front."""
    if _user32.GetForegroundWindow() != hwnd:
        return False
    for vk in vks:
        _user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    return True


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
    _user32.GetLastInputInfo(ctypes.byref(info))
    return (_kernel32.GetTickCount() - info.dwTime) / 1000
