"""Anti-Bypass "real keyboard only": while the phrase window is open, keys that a program typed (macro tools,
auto-typers, anything using SendInput / keybd_event - Windows marks those as injected) are blocked when a Lockdown
window is in front, so the phrase has to be typed on a real keyboard. A low-level keyboard hook on the Tk thread
(Tk's event loop delivers its calls). Keyboards with their own built-in macros look like real typing and can't be
told apart."""
import ctypes
import os
from ctypes import wintypes

WH_KEYBOARD_LL = 13
LLKHF_INJECTED = 0x10
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


_user32 = ctypes.windll.user32
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = LRESULT
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
ctypes.windll.kernel32.GetModuleHandleW.restype = wintypes.HMODULE   # (a 64-bit handle: not an int)


def should_block(flags: int, foreground_pid: int, my_pid: int) -> bool:
    """A key typed by a program, going to a Lockdown window."""
    return bool(flags & LLKHF_INJECTED) and foreground_pid == my_pid


def _foreground_pid() -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value


class RealKeysOnly:
    """Blocks injected keys to Lockdown's windows until stop(). on_blocked() is called for each one blocked."""

    def __init__(self, on_blocked):
        self.on_blocked = on_blocked
        self._proc = HOOKPROC(self._hook)   # (keep a reference: the callback must outlive the hook)
        self._hook_id = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                                  ctypes.windll.kernel32.GetModuleHandleW(None), 0)

    @property
    def active(self) -> bool:
        return bool(self._hook_id)

    def _hook(self, code, wparam, lparam):
        if code == 0:
            key = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if should_block(key.flags, _foreground_pid(), os.getpid()):
                self.on_blocked()
                return 1
        return _user32.CallNextHookEx(None, code, wparam, lparam)

    def stop(self):
        if self._hook_id:
            _user32.UnhookWindowsHookEx(self._hook_id)
            self._hook_id = None
