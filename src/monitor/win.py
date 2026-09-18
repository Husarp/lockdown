"""Desktop helpers for the tray agent: foreground app, idle time, closing an app's windows politely."""
import ctypes
from ctypes import wintypes

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_CLOSE = 0x0010

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


def foreground() -> tuple[int, str]:
    """(window handle, lowercase exe name) of the foreground window; (0, '') if none (e.g. locked)."""
    hwnd = _user32.GetForegroundWindow()
    return (hwnd, process_name(hwnd)) if hwnd else (0, "")


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


def close_app(exe: str) -> int:
    """Ask every window of the app to close (like clicking X). Returns how many windows were asked."""
    count = 0
    for hwnd, _title, path in top_windows():
        if exe_name(path) == exe.lower():
            _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            count += 1
    return count


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
    _user32.GetLastInputInfo(ctypes.byref(info))
    return (_kernel32.GetTickCount() - info.dwTime) / 1000
