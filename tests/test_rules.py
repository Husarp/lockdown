from datetime import datetime

import pytest

from rules import describe_rule, make_schedule, rule_block, schedule_until

# 2026-09-14 is a Monday
def at(day, hh, mm=0):
    return datetime(2026, 9, 14 + day, hh, mm)


WORKDAYS_DAYTIME = make_schedule([0, 1, 2, 3, 4], "09:00", "17:00")
WORKDAYS_NIGHT = make_schedule([0, 1, 2, 3, 4], "21:00", "07:00")


@pytest.mark.parametrize("now,expected", [
    (at(0, 8, 59), None),
    (at(0, 9, 0), at(0, 17)),
    (at(0, 16, 59), at(0, 17)),
    (at(0, 17, 0), None),
    (at(5, 12), None),            # Saturday
])
def test_daytime_window(now, expected):
    assert schedule_until(WORKDAYS_DAYTIME, now) == expected


@pytest.mark.parametrize("now,expected", [
    (at(0, 20, 59), None),
    (at(0, 21, 0), at(1, 7)),      # Mon night -> Tue 07:00
    (at(1, 6, 59), at(1, 7)),      # Tue early morning belongs to Monday's window
    (at(1, 7, 0), None),
    (at(0, 3), None),              # Mon early morning: Sunday not selected
    (at(5, 3), at(5, 7)),          # Sat early morning: Friday's window
    (at(5, 22), None),             # Sat night not selected
])
def test_overnight_window(now, expected):
    assert schedule_until(WORKDAYS_NIGHT, now) == expected


def test_make_schedule_validates():
    with pytest.raises(ValueError):
        make_schedule([], "09:00", "17:00")
    with pytest.raises(ValueError):
        make_schedule([0], "9am", "17:00")
    with pytest.raises(ValueError):
        make_schedule([0], "25:00", "17:00")


def test_temporary():
    rule = {"rule_type": "temporary", "temp_until": "2026-09-14 13:30:00", "schedule": None}
    assert rule_block(rule, at(0, 12)) == ("temporary", at(0, 13, 30))
    assert rule_block(rule, at(0, 13, 30)) is None
    assert describe_rule(rule, at(0, 12)) == "Temporary: 1h 30m left"


def test_describe():
    assert describe_rule({"rule_type": "scheduled", "schedule": WORKDAYS_NIGHT}, at(0, 0)) == "Hours: Mo-Fr 21:00-07:00"
    assert describe_rule({"rule_type": "permanent"}, at(0, 0)) == "Permanent"
