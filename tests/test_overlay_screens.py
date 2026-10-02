"""0.84.2: the bedtime / break screen covers exactly the primary monitor, centred, and is not laggy.

Adam: "the sleep screen is not centered and appears but only part of it on second screen + its a little laggy".
It was sized with customtkinter's geometry(), which multiplies the size by the display scaling: on a 150% primary
a 2880x1800 screen got a 4320x2700 cover that ran onto the second monitor, with the text centred in that (off the
middle of the real screen). Now the cover is exactly the primary monitor's rect in physical pixels, the message is
centred on it, and every other monitor gets a plain dark cover. Monitors are faked as Windows reports them
(rcMonitor, physical pixels; one left of / above the primary has negative coordinates).
"""
from datetime import datetime

import pytest

from gui import screens
from gui.screens import Monitor, content_center, countdown, geometry, plan

FALLBACK = (0, 0, 1280, 800)


def center_of(rect):
    left, top, right, bottom = rect
    return left + (right - left) / 2


@pytest.mark.parametrize("found, main_rect, other_rects", [
    # primary on the left at 150%, a 1080p one to its right at 100% (offset down a bit)
    ([Monitor((0, 0, 2880, 1800), True, 1.5), Monitor((2880, 200, 4800, 1280), False, 1.0)],
     (0, 0, 2880, 1800), [(2880, 200, 4800, 1280)]),
    # primary on the RIGHT: the other monitor is left of it, at negative x (listed first)
    ([Monitor((-1920, 0, 0, 1080), False, 1.0), Monitor((0, 0, 2560, 1440), True, 1.25)],
     (0, 0, 2560, 1440), [(-1920, 0, 0, 1080)]),
    # a 150% one to the left at negative coords, 100% primary
    ([Monitor((-2560, -360, 0, 1080), False, 1.5), Monitor((0, 0, 1920, 1080), True, 1.0)],
     (0, 0, 1920, 1080), [(-2560, -360, 0, 1080)]),
    # one stacked above the primary
    ([Monitor((0, 0, 1920, 1080), True, 1.0), Monitor((0, -1440, 2560, 0), False, 1.5)],
     (0, 0, 1920, 1080), [(0, -1440, 2560, 0)]),
    # just the one monitor
    ([Monitor((0, 0, 3840, 2160), True, 1.5)], (0, 0, 3840, 2160), []),
])
def test_message_on_exactly_the_primary_monitor(found, main_rect, other_rects):
    main, others = plan(found, FALLBACK)
    assert main.rect == main_rect
    assert [m.rect for m in others] == other_rects
    # the message is centred on that monitor (left-right), and a little above its middle
    x, y = content_center(main.rect)
    assert x == center_of(main_rect)
    assert main_rect[1] < y < main_rect[1] + (main_rect[3] - main_rect[1]) / 2


def test_size_is_the_monitor_not_scaled_again():
    """At 150% the cover is the monitor's own pixel size - not 1.5x it (what spilled onto the second screen)."""
    main, _ = plan([Monitor((0, 0, 2880, 1800), True, 1.5), Monitor((2880, 0, 4800, 1080), False, 1.0)], FALLBACK)
    assert geometry(main.rect) == "2880x1800+0+0"


def test_geometry_strings_for_negative_positions():
    assert geometry((-1920, 0, 0, 1080)) == "1920x1080+-1920+0"
    assert geometry((0, -1440, 2560, 0)) == "2560x1440+0+-1440"
    assert geometry((2880, 200, 4800, 1280)) == "1920x1080+2880+200"


def test_no_primary_flag_takes_the_one_at_the_origin():
    main, others = plan([Monitor((-1920, 0, 0, 1080)), Monitor((0, 0, 2560, 1440))], FALLBACK)
    assert main.rect == (0, 0, 2560, 1440) and [m.rect for m in others] == [(-1920, 0, 0, 1080)]


def test_mirrored_displays_are_one_cover():
    main, others = plan([Monitor((0, 0, 1920, 1080), True), Monitor((0, 0, 1920, 1080))], FALLBACK)
    assert main.rect == (0, 0, 1920, 1080) and others == []


def test_no_monitors_falls_back_to_tks_screen():
    main, others = plan([], FALLBACK)
    assert main.rect == FALLBACK and others == []
    assert screens.monitors() == [] or isinstance(screens.monitors()[0], Monitor)


def test_countdown_wakes_once_a_second_when_the_text_changes():
    assert countdown(65.3) == ("1:05", 315)
    assert countdown(600.0) == ("10:00", 15)
    assert countdown(0.5) == ("0:00", 515)
    assert countdown(0) == ("0:00", None)        # done: stops ticking
    assert countdown(-4) == ("0:00", None)


# ---------------------------------------------------------------- the real window (needs a display)

def _tk_root():
    tkinter = pytest.importorskip("tkinter")
    ctk = pytest.importorskip("customtkinter")
    if not hasattr(ctk, "CTk") or not isinstance(ctk.CTk, type):
        pytest.skip("customtkinter is faked here")
    try:
        root = ctk.CTk()
    except tkinter.TclError:
        pytest.skip("no display")
    root.withdraw()
    return ctk, root


def test_overlay_window_fills_its_monitor_even_with_display_scaling(monkeypatch):
    ctk, root = _tk_root()
    from gui import reminders_ui
    layout = [Monitor((0, 0, 1000, 700), True, 1.5), Monitor((1000, 50, 1600, 450), False, 1.0)]
    monkeypatch.setattr(screens, "monitors", lambda: layout)
    ctk.set_window_scaling(1.5)          # what a 150% display does to customtkinter's geometry()
    calls = []

    def now():
        calls.append(1)
        return datetime(2026, 10, 2, 22, 0)

    try:
        ov = reminders_ui.Overlay(root, "Break", "Step away.", datetime(2026, 10, 2, 22, 5), [("OK", "ok")],
                                  lambda a: None, now)
        for _ in range(5):
            root.update()
        assert (ov.winfo_width(), ov.winfo_height()) == (1000, 700)
        assert (ov.winfo_rootx(), ov.winfo_rooty()) == (0, 0)
        [cover] = ov._covers
        assert (cover.winfo_width(), cover.winfo_height(), cover.winfo_rootx(), cover.winfo_rooty()) == \
               (600, 400, 1000, 50)
        assert ov.clock.cget("text") == "5:00" or ov.clock.cget("text") == "4:59"
        assert calls == [1]              # the trusted clock (a database read) once, at open - not every tick
        ov.destroy()
        assert not cover.winfo_exists()  # the other monitors' covers go with it
    finally:
        ctk.set_window_scaling(1.0)
        root.destroy()


def _open(ctk, root, monkeypatch, layout, until=datetime(2026, 10, 2, 22, 5), now=None):
    from gui import reminders_ui
    monkeypatch.setattr(screens, "monitors", lambda: list(layout))
    ov = reminders_ui.Overlay(root, "Break", "Step away.", until, [("OK", "ok")], lambda a: None,
                              now or (lambda: datetime(2026, 10, 2, 22, 0)))
    for _ in range(5):
        root.update()
    return ov


def _geom(w):
    w.update_idletasks()
    return w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height()


def test_covers_follow_monitors_plugged_and_unplugged_while_it_is_up(monkeypatch):
    """A monitor unplugged / plugged in / the primary changed while the screen is up: the guard re-reads the
    monitors - the message moves to the (new) primary, covers come and go with the monitors."""
    ctk, root = _tk_root()
    layout = [Monitor((0, 0, 1000, 700), True), Monitor((1000, 50, 1600, 450))]
    try:
        ov = _open(ctk, root, monkeypatch, layout)
        assert len(ov._covers) == 1
        layout[:] = [Monitor((0, 0, 1000, 700), True)]               # the second one unplugged
        ov._guard()
        root.update()
        assert ov._covers == [] and _geom(ov) == (0, 0, 1000, 700)
        layout[:] = [Monitor((0, 0, 800, 600)), Monitor((800, 0, 1400, 500), True)]   # new primary, elsewhere
        ov._guard()
        for _ in range(3):
            root.update()
        assert _geom(ov) == (800, 0, 600, 500)
        [cover] = ov._covers
        assert _geom(cover) == (0, 0, 800, 600)
        ov.destroy()
    finally:
        root.destroy()


def test_moved_windows_go_back_and_alt_f4_does_nothing(monkeypatch):
    """Anti-bypass: Win+Shift+Arrow (or anything) moving the screen or a cover off its monitor is undone on the
    next check; neither the screen nor a cover can be closed with Alt+F4 (WM_DELETE_WINDOW does nothing)."""
    ctk, root = _tk_root()
    layout = [Monitor((0, 0, 1000, 700), True), Monitor((1000, 50, 1600, 450))]
    try:
        ov = _open(ctk, root, monkeypatch, layout)
        [cover] = ov._covers
        ov.wm_geometry("1000x700+600+0")
        cover.wm_geometry("600x400+0+0")
        root.update()
        ov._guard()
        for _ in range(3):
            root.update()
        assert _geom(ov) == (0, 0, 1000, 700) and _geom(cover) == (1000, 50, 600, 400)
        assert ov._covers == [cover]                                  # put back, not rebuilt
        for w in (ov, cover):
            handler = w.protocol("WM_DELETE_WINDOW")
            assert handler
            w.tk.call(handler)                                        # what Alt+F4 runs
            assert w.winfo_exists()
        ov.destroy()
    finally:
        root.destroy()


def test_countdown_resyncs_with_the_trusted_clock_now_and_then(monkeypatch):
    """The clock ticks off the monotonic clock, but re-reads the trusted time every RESYNC_S - so after the PC
    slept (or the trusted offset moved) it shows the real time left again."""
    ctk, root = _tk_root()
    from gui import reminders_ui
    clock = [datetime(2026, 10, 2, 22, 0)]
    try:
        ov = _open(ctk, root, monkeypatch, [Monitor((0, 0, 1000, 700), True)], now=lambda: clock[0])
        clock[0] = datetime(2026, 10, 2, 22, 3)                       # e.g. it slept 3 minutes
        monkeypatch.setattr(ov, "_synced", ov._synced - reminders_ui.Overlay.RESYNC_S)
        ov._count()
        assert ov.clock.cget("text") in ("2:00", "1:59")
        ov.destroy()
    finally:
        root.destroy()
