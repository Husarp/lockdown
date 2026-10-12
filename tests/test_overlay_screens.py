"""0.84.2: the bedtime / break screen covers exactly the primary monitor, centred, and is not laggy.

Adam: "the sleep screen is not centered and appears but only part of it on second screen + its a little laggy".
It was sized with the old Tk toolkit's geometry(), which multiplies the size by the display scaling: on a 150% primary
a 2880x1800 screen got a 4320x2700 cover that ran onto the second monitor, with the text centred in that (off the
middle of the real screen). Now the cover is exactly the primary monitor's rect in physical pixels, the message is
centred on it, and every other monitor gets a plain dark cover. Monitors are faked as Windows reports them
(rcMonitor, physical pixels; one left of / above the primary has negative coordinates).
"""

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
