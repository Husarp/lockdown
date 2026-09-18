from datetime import datetime

from alerts import format_message, should_notify

NOW = datetime(2026, 9, 14, 22, 0)


def ev(reason, until):
    return {"display_name": "Reddit", "reason": reason, "until": until}


def test_format_message():
    assert format_message("{site} is blocked until {until}.", ev("schedule", "2026-09-14 23:30:00"), NOW) \
        == "Reddit is blocked until 23:30."
    assert format_message("{site}: {reason} until {until}", ev("schedule", "2026-09-15 07:00:00"), NOW) \
        == "Reddit: blocked at this time until Tuesday 07:00"
    assert format_message("{site} {until}", ev("permanent", None), NOW) == "Reddit further notice"
    assert format_message("bad {placeholder}", ev("permanent", None), NOW) == "bad {placeholder}"


def test_should_notify():
    assert should_notify({}, None, True, None, 1000, 5)
    assert not should_notify({}, None, False, None, 1000, 5)       # reason disabled
    assert should_notify({}, "on", False, None, 1000, 5)           # item override on
    assert not should_notify({}, "off", True, None, 1000, 5)       # item override off
    assert not should_notify({}, None, True, 1000 - 60, 1000, 5)   # within cooldown
    assert should_notify({}, None, True, 1000 - 300, 1000, 5)
