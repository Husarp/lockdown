"""A limit says which "today" it means. Screen Time counts from midnight; a limit day that starts at 03:00 made
two hours of play since midnight read as 28 minutes at 03:30 - and looked broken, with nothing to say why."""
from datetime import datetime

from rules import LimitClock, period_words

FRIDAY = datetime(2026, 9, 25)


def test_a_midnight_day_is_just_today():
    assert period_words("day", FRIDAY.replace(hour=15), LimitClock()) == "today"


def test_a_later_reset_says_when_the_day_started():
    clock = LimitClock('{"time": "03:00"}')
    assert period_words("day", FRIDAY.replace(hour=3, minute=30), clock) == "since 03:00"
    assert period_words("day", FRIDAY.replace(hour=20), clock) == "since 03:00"


def test_just_after_midnight_it_is_still_yesterdays_limit_day():
    """The exact moment it used to mislead: late-night play still counting toward the day before."""
    clock = LimitClock('{"time": "03:00"}')
    assert period_words("day", FRIDAY.replace(hour=1, minute=30), clock) == "since Thu 03:00"


def test_weeks_and_months_keep_their_words():
    clock = LimitClock('{"time": "03:00"}')
    assert period_words("week", FRIDAY.replace(hour=15), clock) == "this week"
    assert period_words("month", FRIDAY.replace(hour=15), clock) == "this month"
