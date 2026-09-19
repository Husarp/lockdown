"""A strict break minimises other windows but never Lockdown's own (or the user is soft-locked for the break)."""
import os

from monitor import win


class _User32:
    def __init__(self):
        self.minimized = []

    def IsIconic(self, hwnd):
        return 0

    def ShowWindow(self, hwnd, cmd):
        self.minimized.append(hwnd)

    def GetForegroundWindow(self):
        return 1   # Lockdown is in front


def test_strict_break_never_minimises_lockdown(monkeypatch):
    fake = _User32()
    monkeypatch.setattr(win, "_user32", fake)
    monkeypatch.setattr(win, "top_windows", lambda: [(1, "Lockdown", r"C:\x\Lockdown.exe"),
                                                     (2, "Chrome", r"C:\x\chrome.exe")])
    monkeypatch.setattr(win, "window_pid", lambda hwnd: os.getpid() if hwnd == 1 else 4242)
    assert win.minimize_all() == 1
    assert fake.minimized == [2]
    assert win.minimize_foreground() is False   # Lockdown in front: left alone
    assert fake.minimized == [2]
