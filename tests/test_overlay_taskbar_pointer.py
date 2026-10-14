"""The bedtime / strict-break cover on Windows (resident/overlay.OverlayHost), against a small fake desktop:

- No taskbar button, not in Alt+Tab: the screen window and every cover on the other monitors are tool windows
  (WS_EX_TOOLWINDOW on, WS_EX_APPWINDOW - which WinForms puts on every form - off), set while still hidden, put back if
  anything resets it. The corner window too.
- The pointer moves normally: nothing here moves the cursor; the guard only moves / resizes a window that really is out
  of place, and not again and again where Windows itself put it (a pixel off through DPI rounding); "back on top" is a
  Z-order change only (SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE); the keyboard is only taken back when another window has
  it. "Block clicks behind" off: the guard touches nothing while nothing moved.
- Usage counting (monitor/win.cover): the cover - now a tool window - still hides the apps under it.

What only a real Windows PC can prove: that the taskbar shows no button, that the pointer no longer jumps, that WinForms /
WebView2 never reset the style, and that the taskbar stays behind the cover."""
import ast
import ctypes
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gui import screens
from monitor import win as monitor_win
from resident import corner as corner_mod
from resident import overlay as overlay_mod
from resident.overlay import (GA_ROOTOWNER, SW_HIDE, SW_RESTORE, SW_SHOWNOACTIVATE, SWP_NOACTIVATE, SWP_NOMOVE,
                              SWP_NOSIZE, SWP_SHOWWINDOW, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW, OverlayHost, misplaced,
                              tool_style)

MAIN, OTHER = (0, 0, 1920, 1080), (1920, 0, 3840, 1080)
SCREEN, COVER = 100, 101
TITLES = {"Lockdown overlay": SCREEN, "Lockdown overlay cover 1": COVER}
WS_EX_NOACTIVATE, WS_EX_LAYERED, WS_EX_TOPMOST = 0x08000000, 0x80000, 0x8
RAISE = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
POINTER_CALLS = {"SetCursorPos", "SetPhysicalCursorPos", "ClipCursor", "mouse_event", "SendInput", "SetCapture"}
ICONIC = (-32000, -32000, -31840, -31972)


class Desktop:
    """user32 as the host sees it: the windows' rects, styles, visibility and the window in front. Every call is
    recorded; anything that would move the pointer fails the test."""

    def __init__(self):
        self.rect = {SCREEN: (560, 240, 1360, 840)}          # (pywebview's default spot until placed)
        self.visible = {SCREEN: False}
        self.style = {SCREEN: WS_EX_APPWINDOW}               # (WinForms: ShowInTaskbar)
        self.iconic: set = set()
        self.owner: dict = {}
        self.front = 555                                      # some app has the keyboard when bedtime comes
        self.calls: list = []
        self.adjust = lambda hwnd, rect: rect                 # where Windows really puts a window it is asked to place
        self.pointer: list = []

    def __getattr__(self, name):
        if name in POINTER_CALLS:
            self.pointer.append(name)                         # (seen even when the call came from another thread)
            raise AssertionError(f"{name}: the cover must never move or hold the pointer")
        raise AttributeError(name)

    def IsIconic(self, hwnd):
        return hwnd in self.iconic

    def IsWindowVisible(self, hwnd):
        return self.visible.get(hwnd, False)

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(("show", hwnd, cmd))
        self.visible[hwnd] = cmd != SW_HIDE
        if cmd == SW_RESTORE:
            self.iconic.discard(hwnd)
        return 1

    def SetWindowPos(self, hwnd, after, x, y, w, h, flags):
        self.calls.append(("pos", hwnd, flags))
        if not flags & SWP_NOMOVE:
            self.rect[hwnd] = self.adjust(hwnd, (x, y, x + w, y + h))
        if flags & SWP_SHOWWINDOW:
            self.visible[hwnd] = True
        if not flags & SWP_NOACTIVATE:
            self.front = hwnd
        return 1

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("front", hwnd))
        self.front = hwnd
        return 1

    def GetForegroundWindow(self):
        return self.front

    def GetAncestor(self, hwnd, flag):
        assert flag == GA_ROOTOWNER
        return self.owner.get(hwnd, hwnd)

    def GetWindowThreadProcessId(self, hwnd, pid):
        return 77

    def AttachThreadInput(self, mine, theirs, on):
        self.calls.append(("attach", on))
        return 1

    def BringWindowToTop(self, hwnd):
        self.calls.append(("top", hwnd))
        return 1

    def GetWindowLongW(self, hwnd, index):
        return self.style.get(hwnd, 0)

    def SetWindowLongW(self, hwnd, index, value):
        self.calls.append(("style", hwnd, value & 0xFFFFFFFF))
        self.style[hwnd] = value & 0xFFFFFFFF
        return 1

    def GetWindowTextW(self, hwnd, buf, n):
        buf.value = "Some app"
        return 8

    def GetClassNameW(self, hwnd, buf, n):
        buf.value = "SomeClass"
        return 9

    # ---------------------------------------------------------------- what the tests look at
    def placed(self, hwnd):
        """SetWindowPos calls that moved / resized `hwnd`."""
        return [c for c in self.calls if c[0] == "pos" and c[1] == hwnd and not c[2] & SWP_NOMOVE]

    def took_keys(self):
        return [c for c in self.calls if c[0] in ("attach", "front", "top")]


class ScreenWindow:
    """The pywebview overlay window: show() is Show() + Activate()."""

    def __init__(self, desk):
        self.desk = desk

    def show(self):
        self.desk.calls.append(("pywebview show", SCREEN))
        self.desk.visible[SCREEN] = True
        self.desk.front = SCREEN

    def hide(self):
        self.desk.calls.append(("pywebview hide", SCREEN))
        self.desk.visible[SCREEN] = False


def eventually(check, seconds=3):
    deadline = time.time() + seconds
    while not check() and time.time() < deadline:
        time.sleep(0.01)
    return check()


@pytest.fixture
def desk(monkeypatch):
    desk = Desktop()
    monkeypatch.setattr(overlay_mod, "_user32", lambda: desk)
    monkeypatch.setattr(overlay_mod, "_hwnd", lambda title: TITLES.get(title, 0) if title != "Lockdown overlay cover 1"
                        or COVER in desk.rect else 0)
    monkeypatch.setattr(overlay_mod, "_rect_of", lambda hwnd: ICONIC if hwnd in desk.iconic else desk.rect.get(hwnd))
    return desk


def make_host(desk, block=False, monitors=(MAIN, OTHER), log=None):
    state = {"block": block}

    def make_cover(title, background):                       # (pywebview, hidden=True: made hidden, at its default spot)
        desk.calls.append(("made", COVER))
        desk.rect[COVER], desk.visible[COVER], desk.style[COVER] = (560, 240, 1360, 840), False, \
            WS_EX_APPWINDOW | WS_EX_NOACTIVATE
        return SimpleNamespace(destroy=lambda: desk.calls.append(("destroyed", COVER)))

    found = [screens.Monitor(r, i == 0) for i, r in enumerate(monitors)]
    host = OverlayHost(ScreenWindow(desk), monitors=lambda: found, make_cover=make_cover,
                       blocking=lambda: state["block"], log=log)
    return host, state


def up(desk, block=False, **kw):
    host, state = make_host(desk, block, **kw)
    host.show("#000000")
    assert eventually(lambda: desk.visible.get(COVER) and host._covers)
    time.sleep(0.05)                                          # (the covers' thread has finished)
    return host, state


# ---------------------------------------------------------------- no taskbar button, not in Alt+Tab

def test_the_tool_window_style_drops_appwindow_and_keeps_the_rest():
    assert tool_style(WS_EX_APPWINDOW) == WS_EX_TOOLWINDOW
    kept = WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TOPMOST
    assert tool_style(kept | WS_EX_APPWINDOW) == kept | WS_EX_TOOLWINDOW
    assert tool_style(tool_style(WS_EX_APPWINDOW)) == WS_EX_TOOLWINDOW                     # (nothing more to change)
    assert tool_style(0xFFFFFFFF) == 0xFFFFFFFF & ~WS_EX_APPWINDOW


def test_the_screen_is_a_tool_window_before_it_comes_up(desk):
    host, _ = up(desk)
    assert desk.style[SCREEN] == WS_EX_TOOLWINDOW
    styled = desk.calls.index(("style", SCREEN, WS_EX_TOOLWINDOW))
    assert styled < desk.calls.index(("pywebview show", SCREEN))                           # set while still hidden
    host.hide()


def test_every_cover_is_made_hidden_and_shown_only_once_it_is_a_tool_window(desk):
    host, _ = up(desk)
    assert desk.style[COVER] == WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    styled = desk.calls.index(("style", COVER, WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE))
    shown = next(i for i, c in enumerate(desk.calls) if c[1] == COVER and (c[0] == "show" or
                                                                             (c[0] == "pos" and c[2] & SWP_SHOWWINDOW)))
    assert styled < shown
    assert ("show", COVER, SW_SHOWNOACTIVATE) in desk.calls                               # (WinForms learns it is up)
    assert desk.rect[COVER] == OTHER and desk.visible[COVER] and desk.front == SCREEN       # never took the keyboard
    first_show = desk.calls.index(("show", COVER, SW_SHOWNOACTIVATE))
    assert desk.placed(COVER)[0] == desk.calls[first_show - 1]                             # placed before it is seen
    host.hide()


def test_a_cover_for_a_monitor_plugged_in_later_is_a_tool_window_too(desk):
    host, _ = make_host(desk, True, monitors=(MAIN,))         # one monitor: no cover yet
    host.show("#000000")
    time.sleep(0.05)
    assert COVER not in desk.rect and not host._covers
    found = [screens.Monitor(MAIN, True), screens.Monitor(OTHER, False)]
    host._monitors = lambda: found
    host.refresh()
    assert eventually(lambda: desk.visible.get(COVER))
    assert desk.style[COVER] == WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE and desk.rect[COVER] == OTHER
    styled = desk.calls.index(("style", COVER, WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE))
    assert styled < desk.calls.index(("show", COVER, SW_SHOWNOACTIVATE)) and desk.front == SCREEN
    host.hide()


class Handlers(list):
    def __iadd__(self, fn):                                   # (pywebview's `events.closing += fn`)
        self.append(fn)
        return self


def test_the_pywebview_cover_is_made_hidden(monkeypatch):
    seen, events = {}, SimpleNamespace(closing=Handlers())
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(
        create_window=lambda title, **kw: seen.update(kw, title=title) or SimpleNamespace(events=events)))
    host = OverlayHost(SimpleNamespace(), monitors=lambda: [])
    host._pywebview_cover("Lockdown overlay cover 1", "#000000")
    assert seen["hidden"] is True and seen["focus"] is False and seen["on_top"] is True
    assert events.closing == [host.closing]


def test_a_style_reset_while_the_screen_came_up_is_undone_and_it_comes_up_again(desk):
    window = ScreenWindow(desk)
    show = window.show

    def show_and_reset():                                     # (as if WinForms rewrote its styles while showing it)
        show()
        desk.style[SCREEN] |= WS_EX_APPWINDOW
    window.show = show_and_reset
    host, _ = make_host(desk)
    host.window = window
    log = []
    host.log = log.append
    host.show("#000000")
    assert desk.style[SCREEN] == WS_EX_TOOLWINDOW
    after = desk.calls[desk.calls.index(("pywebview show", SCREEN)) + 1:]
    assert after[:3] == [("style", SCREEN, WS_EX_TOOLWINDOW), ("show", SCREEN, SW_HIDE),
                         ("show", SCREEN, SW_SHOWNOACTIVATE)]                              # (the taskbar looks again)
    assert ("front", SCREEN) in after and any("taskbar" in line for line in log)
    host.hide()


@pytest.mark.parametrize("block", [False, True])
def test_the_guard_puts_the_style_back_if_anything_resets_it(desk, block):
    log = []
    host, _ = up(desk, block, log=log.append)
    desk.style[SCREEN] |= WS_EX_APPWINDOW
    desk.style[COVER] |= WS_EX_APPWINDOW
    desk.calls.clear()
    host.refresh()
    assert desk.style[SCREEN] == WS_EX_TOOLWINDOW and desk.style[COVER] == WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    assert not desk.placed(SCREEN) and not desk.placed(COVER)                             # (nothing moved for it)
    assert sum("tool-window" in line for line in log) == 2
    host.hide()


def test_the_corner_window_is_a_tool_window_without_appwindow(monkeypatch):
    written = []

    class User32:
        def __getattr__(self, name):
            def fn(*args):
                if name == "SetWindowLongW":
                    written.append(args[2] & 0xFFFFFFFF)
                return {"IsWindowVisible": 0, "GetWindowLongW": WS_EX_APPWINDOW}.get(name, 1)
            return fn

    monkeypatch.setattr(corner_mod.CornerHost, "_hwnd", staticmethod(lambda: 42))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: User32(), raising=False)
    corner_mod.CornerHost(window=None, work_area=lambda: (0, 0, 1920, 1040)).show()
    assert written == [WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE]


# ---------------------------------------------------------------- the pointer moves normally

@pytest.mark.parametrize("block", [False, True])
def test_a_screen_that_nothing_moved_is_never_moved_again(desk, block):
    host, _ = up(desk, block)
    desk.calls.clear()
    for _ in range(5):
        host.refresh()
    assert not desk.placed(SCREEN) and not desk.placed(COVER)
    assert not [c for c in desk.calls if c[0] in ("show", "style", "made", "destroyed")]
    if block:                                                 # back on top: the Z-order only, never shown / activated
        assert desk.calls == [("pos", SCREEN, RAISE), ("pos", COVER, RAISE)] * 5
    else:
        assert desk.calls == []                               # (the switch off: nothing at all, as before)
    host.hide()


def test_placing_a_window_uses_the_flags_it_needs_and_nothing_more(desk):
    desk.visible[SCREEN] = True
    overlay_mod._put(SCREEN, MAIN)                            # the guard: placed and shown, the keyboard left alone
    overlay_mod._put(SCREEN, MAIN, show=False)                # while hidden: not shown, not activated
    overlay_mod._put(SCREEN, MAIN, front=True)                # coming up: activated, and the keyboard taken
    assert desk.calls == [("pos", SCREEN, SWP_SHOWWINDOW | SWP_NOACTIVATE), ("pos", SCREEN, SWP_NOACTIVATE),
                          ("pos", SCREEN, SWP_SHOWWINDOW), ("front", SCREEN)]
    desk.visible[SCREEN] = False                              # hidden by something: shown first, so its page is drawn
    desk.calls.clear()
    overlay_mod._put(SCREEN, MAIN)
    assert desk.calls == [("show", SCREEN, SW_SHOWNOACTIVATE), ("pos", SCREEN, SWP_SHOWWINDOW | SWP_NOACTIVATE)]


def test_back_on_top_is_a_z_order_change_only(monkeypatch):
    calls = []
    monkeypatch.setattr(overlay_mod, "_user32", lambda: SimpleNamespace(SetWindowPos=lambda *a: calls.append(a)))
    overlay_mod._raise(SCREEN)
    assert calls == [(SCREEN, overlay_mod.HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)]


def test_the_keyboard_is_taken_back_only_when_another_window_has_it(desk):
    log = []
    host, _ = up(desk, True, log=log.append)
    desk.front = 555                                          # Alt+Tab to the app behind
    desk.calls.clear()
    host.refresh()
    assert desk.took_keys() == [("attach", True), ("top", SCREEN), ("front", SCREEN), ("attach", False)]
    assert any("keyboard was taken back from 'Some app'" in line for line in log)
    desk.calls.clear()
    for _ in range(3):                                        # ours now: left alone
        host.refresh()
    assert desk.took_keys() == []
    desk.front = COVER                                        # a cover has it (a click on the other monitor): ours
    host.refresh()
    assert desk.took_keys() == []
    desk.front, desk.owner[777] = 777, SCREEN                 # a drop-down the page opened: owned by the screen
    host.refresh()
    assert desk.took_keys() == []
    desk.front = 0                                            # nothing has it (a desktop switch): asked for, as in 0.92.0
    host.refresh()
    assert desk.took_keys() == [("top", SCREEN), ("front", SCREEN)]                      # (nobody to attach to)
    host.hide()


def test_without_the_switch_the_keyboard_is_never_taken(desk):
    host, _ = up(desk, False)
    desk.front = 555
    desk.calls.clear()
    for _ in range(3):
        host.refresh()
    assert desk.calls == [] and desk.front == 555
    host.hide()


@pytest.mark.parametrize("block", [False, True])
def test_a_moved_window_is_put_back_once(desk, block):
    host, _ = up(desk, block)
    desk.rect[SCREEN] = (10, 10, 1930, 1090)                  # Win+arrow, another app ...
    desk.rect[COVER] = (1900, 0, 3820, 1080)
    desk.calls.clear()
    host.refresh()
    assert eventually(lambda: desk.rect[COVER] == OTHER)
    assert desk.rect[SCREEN] == MAIN
    assert [c[2] & SWP_NOACTIVATE for c in desk.placed(SCREEN) + desk.placed(COVER)] == [SWP_NOACTIVATE] * 2
    desk.calls.clear()
    host.refresh()
    time.sleep(0.05)
    assert not desk.placed(SCREEN) and not desk.placed(COVER)
    host.hide()


@pytest.mark.parametrize("block", [False, True])
def test_where_windows_itself_puts_a_window_is_not_fought_every_tick(desk, block):
    """A display scaled 125 % / 150 %: Windows puts the window a pixel off. 0.92.0 moved it back every tick (twice a
    second with the switch on) - the window under the pointer moving all the time."""
    desk.adjust = lambda hwnd, r: (r[0], r[1], r[2] - 1, r[3] - 1)
    log = []
    host, _ = up(desk, block, log=log.append)
    assert desk.rect[SCREEN] == (0, 0, 1919, 1079)
    assert len(desk.placed(SCREEN)) == 2 and len(desk.placed(COVER)) == 2                 # (placed hidden, then shown)
    desk.calls.clear()
    for _ in range(5):
        host.refresh()
    time.sleep(0.05)
    assert not desk.placed(SCREEN) and not desk.placed(COVER)
    assert sum("Windows placed it at" in line for line in log) == 2                       # said once per window
    desk.rect[SCREEN] = (300, 300, 900, 900)                  # really moved: put back (where Windows puts it)
    host.refresh()
    assert len(desk.placed(SCREEN)) == 1 and desk.rect[SCREEN] == (0, 0, 1919, 1079)
    host.hide()


@pytest.mark.parametrize("block", [False, True])
def test_a_minimised_screen_comes_back(desk, block):
    host, _ = up(desk, block)
    desk.iconic.add(SCREEN)                                   # Win+D
    desk.calls.clear()
    host.refresh()
    assert ("show", SCREEN, SW_RESTORE) in desk.calls and SCREEN not in desk.iconic and desk.rect[SCREEN] == MAIN
    host.hide()


def test_hidden_by_something_comes_back_only_with_the_switch_on(desk):
    host, state = up(desk, False)
    desk.visible[SCREEN] = False
    host.refresh()
    assert not desk.visible[SCREEN]                           # the switch off: as before
    state["block"] = True
    host.refresh()
    assert desk.visible[SCREEN] and ("show", SCREEN, SW_SHOWNOACTIVATE) in desk.calls
    host.hide()


def test_a_refresh_never_shows_what_hide_just_took_down(desk):
    host, state = up(desk, True)
    desk.visible[SCREEN] = False                              # (so the refresh would show it again)
    done = threading.Event()
    hider = threading.Thread(target=lambda: (host.hide(), done.set()))

    def blocking():                                           # the dismiss lands in the middle of a refresh
        hider.start()
        assert not done.wait(0.1)                             # hide() waits for the refresh to finish placing
        return True
    host.blocking = blocking
    host.refresh()
    assert done.wait(2)
    assert desk.visible[SCREEN] is False                     # (shown by the refresh, then hidden: never the other way)
    assert [c for c in desk.calls if c[1] == SCREEN][-1] == ("pywebview hide", SCREEN)
    desk.calls.clear()
    host.refresh()                                            # (the guard's last tick, after the screen went)
    assert [c for c in desk.calls if c[1] == SCREEN] == []


def test_nothing_here_moves_the_pointer(desk):
    host, state = up(desk, True)                              # the Desktop fails any pointer call
    desk.front, desk.rect[SCREEN] = 555, (5, 5, 50, 50)
    desk.iconic.add(COVER)
    host.refresh()
    state["block"] = False
    host.refresh()
    host.hide()
    assert eventually(lambda: ("destroyed", COVER) in desk.calls)
    assert desk.pointer == [] and ("show", COVER, SW_RESTORE) in desk.calls and ("front", SCREEN) in desk.calls
    tree = ast.parse(Path(overlay_mod.__file__).read_text(encoding="utf-8"))
    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
        {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not used & POINTER_CALLS


def test_misplaced_compares_like_for_like_and_trusts_where_windows_put_it():
    assert not misplaced(MAIN, MAIN)
    assert not misplaced(list(MAIN), MAIN)                    # (a list from a caller is the same rect)
    assert misplaced((0, 0, 1919, 1079), MAIN)
    assert not misplaced((0, 0, 1919, 1079), MAIN, settled=(0, 0, 1919, 1079))
    assert misplaced((300, 300, 900, 900), MAIN, settled=(0, 0, 1919, 1079))
    assert misplaced(ICONIC, MAIN, settled=(0, 0, 1919, 1079))
    assert not misplaced(None, MAIN)                          # (can't read it: left alone, never a loop)


# ---------------------------------------------------------------- usage counting: the cover still hides what is under it

def test_the_bedtime_cover_still_hides_the_apps_under_it_now_that_it_is_a_tool_window():
    screen = monitor_win.WindowFacts(1, exstyle=WS_EX_TOOLWINDOW, rect=MAIN, own_cover=True)
    cover = monitor_win.WindowFacts(2, exstyle=WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, rect=OTHER, own_cover=True)
    game = monitor_win.WindowFacts(3, rect=MAIN)
    browser = monitor_win.WindowFacts(4, rect=OTHER)
    assert monitor_win.pick_front([screen, cover, game, browser], [MAIN, OTHER]) == [1, 2]
    stranger = monitor_win.WindowFacts(5, exstyle=WS_EX_TOOLWINDOW, rect=MAIN)            # anyone else's tool window
    assert monitor_win.cover(stranger) is None
    assert monitor_win.pick_front([stranger, game], [MAIN]) == [5, 3]
