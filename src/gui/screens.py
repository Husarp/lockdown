"""Where the full-screen covers go (the bedtime / break screen): every monitor, in physical pixels.

The message goes on ONE monitor - the primary one - sized to exactly that monitor and centred on it; any other
monitor gets a plain dark cover, so nothing is half-visible there. All rectangles are (left, top, right, bottom)
in physical pixels, as Windows reports them (rcMonitor; the process is per-monitor DPI aware - customtkinter sets
that), which is also what Tk's own `wm geometry` takes. customtkinter's `geometry()` would multiply the size by
the display scaling a second time (a 150% screen got a cover 1.5x too big that spilled onto the next monitor and
put the text off-centre), so covers are placed with `wm_geometry` and the rect as it is.
"""
import sys
from dataclasses import dataclass

Rect = tuple[int, int, int, int]
CONTENT_RELY = 0.45     # the message sits a little above the middle: it reads as centred


@dataclass(frozen=True)
class Monitor:
    rect: Rect               # left, top, right, bottom - physical pixels; a monitor left of/above the primary is negative
    primary: bool = False
    scale: float = 1.0       # Windows' display scaling on it (its DPI / 96): 1.0, 1.25, 1.5 ...


def plan(found: list[Monitor], fallback: Rect) -> tuple[Monitor, list[Monitor]]:
    """(the monitor that gets the message, the others that just get covered). The primary one gets the message;
    if Windows named none, the one at the desktop origin, else the first. Mirrored displays report the same
    rectangle twice - that is one cover. No monitors at all (not Windows, or the calls failed): the screen Tk
    knows, i.e. the primary one."""
    seen, mons = set(), []
    for m in found:
        if m.rect not in seen and m.rect[2] > m.rect[0] and m.rect[3] > m.rect[1]:
            seen.add(m.rect)
            mons.append(m)
    if not mons:
        return Monitor(fallback, True), []
    main = next((m for m in mons if m.primary), None) \
        or next((m for m in mons if m.rect[0] <= 0 < m.rect[2] and m.rect[1] <= 0 < m.rect[3]), mons[0])
    return main, [m for m in mons if m is not main]


def geometry(rect: Rect) -> str:
    """Tk's geometry string for exactly this rectangle: '2560x1440+-2560+0' for a monitor left of the primary."""
    left, top, right, bottom = rect
    return f"{right - left}x{bottom - top}+{left}+{top}"


def content_center(rect: Rect) -> tuple[float, float]:
    """Where the middle of the message lands on screen once the cover fills `rect` (place(relx=.5, rely=...))."""
    left, top, right, bottom = rect
    return left + (right - left) / 2, top + (bottom - top) * CONTENT_RELY


def countdown(remaining: float) -> tuple[str, int | None]:
    """(what the clock shows, ms until it next changes - None once it is at 0:00). Waking exactly when the
    shown second changes means one cheap update a second, never a redraw for nothing."""
    left = max(0, int(remaining))
    text = f"{left // 60}:{left % 60:02d}"
    if remaining <= 0:
        return text, None
    return text, round((remaining - int(remaining)) * 1000) + 15   # (just past the boundary, so it has changed)


def monitors() -> list[Monitor]:
    """Every monitor Windows has: rcMonitor in physical pixels, the primary flag and its DPI scaling. [] off
    Windows or if anything fails (the cover then falls back to Tk's screen size). Uses its own handles to
    user32/shcore so it never fights another module's ctypes argtypes for the same functions."""
    if not sys.platform.startswith("win"):
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.WinDLL("user32")
        try:
            shcore = ctypes.WinDLL("shcore")
            shcore.GetDpiForMonitor.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.UINT),
                                                ctypes.POINTER(wintypes.UINT)]
        except OSError:
            shcore = None
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                  wintypes.LPARAM)
        user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, proc, wintypes.LPARAM]
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
        found: list[Monitor] = []

        def one(hmon, _hdc, _clip, _data):
            info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                scale = 1.0
                x, y = wintypes.UINT(), wintypes.UINT()
                # MDT_EFFECTIVE_DPI, the same reading customtkinter scales its widgets by
                if shcore is not None and shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(x), ctypes.byref(y)) == 0:
                    scale = (x.value + y.value) / (2 * 96)
                r = info.rcMonitor
                found.append(Monitor((r.left, r.top, r.right, r.bottom), bool(info.dwFlags & 1), scale))
            return True

        user32.EnumDisplayMonitors(None, None, proc(one), 0)
        return found
    except Exception:
        return []
