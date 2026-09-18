import json
from datetime import datetime

import pytest

from rules import ALLOW, BLOCK, describe_rule, make_schedule, rule_block, schedule_until

WEEKDAYS = [0, 1, 2, 3, 4]


# 2026-09-14 is a Monday
def at(day, hh, mm=0):
    return datetime(2026, 9, 14 + day, hh, mm)


BLOCK_DAYTIME = make_schedule(BLOCK, [(WEEKDAYS, "09:00", "17:00")])
BLOCK_NIGHT = make_schedule(BLOCK, [(WEEKDAYS, "21:00", "07:00")])
ALLOW_WORK = make_schedule(ALLOW, [(WEEKDAYS, "09:00", "17:00"), ([5, 6], "10:00", "12:00")])


@pytest.mark.parametrize("now,expected", [
    (at(0, 8, 59), None),
    (at(0, 9, 0), at(0, 17)),
    (at(0, 16, 59), at(0, 17)),
    (at(0, 17, 0), None),
    (at(5, 12), None),            # Saturday
])
def test_block_daytime(now, expected):
    assert schedule_until(BLOCK_DAYTIME, now) == expected


@pytest.mark.parametrize("now,expected", [
    (at(0, 20, 59), None),
    (at(0, 21, 0), at(1, 7)),      # Mon night -> Tue 07:00
    (at(1, 6, 59), at(1, 7)),      # Tue early morning belongs to Monday's window
    (at(1, 7, 0), None),
    (at(0, 3), None),              # Mon early morning: Sunday not selected
    (at(5, 3), at(5, 7)),          # Sat early morning: Friday's window
    (at(5, 22), None),             # Sat night not selected
])
def test_block_overnight(now, expected):
    assert schedule_until(BLOCK_NIGHT, now) == expected


@pytest.mark.parametrize("now,expected", [
    (at(0, 10), None),             # inside allowed weekday window
    (at(0, 20), at(1, 9)),         # Mon 20:00 blocked until Tue 09:00  (the reported YouTube case)
    (at(4, 18), at(5, 10)),        # Fri evening -> Sat 10:00 (custom weekend hours)
    (at(5, 11), None),             # Sat inside weekend window
    (at(5, 13), at(6, 10)),        # Sat afternoon -> Sun 10:00
    (at(6, 13), at(7, 9)),         # Sun afternoon -> next Mon 09:00
])
def test_allow_mode_with_per_day_windows(now, expected):
    assert schedule_until(ALLOW_WORK, now) == expected


def test_old_format_is_block_mode():
    old = json.dumps({"days": WEEKDAYS, "start": "09:00", "end": "17:00"})
    assert schedule_until(old, at(0, 10)) == at(0, 17)
    assert schedule_until(old, at(0, 20)) is None


def test_make_schedule_validates():
    with pytest.raises(ValueError):
        make_schedule(ALLOW, [])
    with pytest.raises(ValueError):
        make_schedule(ALLOW, [([], "09:00", "17:00")])
    with pytest.raises(ValueError):
        make_schedule(ALLOW, [([0], "9am", "17:00")])
    with pytest.raises(ValueError):
        make_schedule(BLOCK, [([0], "25:00", "17:00")])


def test_temporary():
    rule = {"rule_type": "temporary", "temp_until": "2026-09-14 13:30:00", "schedule": None}
    assert rule_block(rule, at(0, 12)) == ("temporary", at(0, 13, 30))
    assert rule_block(rule, at(0, 13, 30)) is None
    assert describe_rule(rule, at(0, 12)) == "Temporary: 1h 30m left"


def test_time_limit():
    rule = {"rule_type": "time_limit", "daily_limit_min": 30}
    assert rule_block(rule, at(0, 12), used_sec=29 * 60) is None
    assert rule_block(rule, at(0, 12), used_sec=30 * 60) == ("limit", at(1, 0))
    assert describe_rule(rule, at(0, 12), used_sec=12 * 60) == "Limit: 12m / 30m today"


def test_allow_mode_reports_schedule_reason():
    rule = {"rule_type": "scheduled", "schedule": ALLOW_WORK}
    assert rule_block(rule, at(0, 20)) == ("schedule", at(1, 9))


def test_describe():
    assert describe_rule({"rule_type": "scheduled", "schedule": BLOCK_NIGHT}, at(0, 0)) == "Blocked:\nMonday–Friday 21:00-07:00"
    assert describe_rule({"rule_type": "scheduled", "schedule": ALLOW_WORK}, at(0, 0)) == \
        "Allowed only:\nMonday–Friday 09:00-17:00\nSaturday–Sunday 10:00-12:00"
    assert describe_rule({"rule_type": "permanent"}, at(0, 0)) == "Permanent"


def test_days_text():
    from rules import days_text
    assert days_text(list(range(7))) == "Every day"
    assert days_text([0, 2, 5, 6]) == "Monday, Wednesday, Saturday–Sunday"
    assert days_text([0, 1, 2, 4]) == "Monday–Wednesday, Friday"
