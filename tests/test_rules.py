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


def used(seconds):
    return lambda owner, bucket: seconds


def test_time_limit():
    rule = {"rule_type": "time_limit", "daily_limit_min": 30}
    assert rule_block(rule, at(0, 12), used(29 * 60)) is None
    assert rule_block(rule, at(0, 12), used(30 * 60)) == ("limit", at(1, 0))
    assert describe_rule(rule, at(0, 12), used(12 * 60)) == "Limit: 12m / 30m today"


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


# ---------- groups / allowance / next block ----------
from rules import effective_rules, item_block, next_block, usage_targets

NIGHT = make_schedule(BLOCK, [(list(range(7)), "21:00", "07:00")])


def test_effective_rules_inherit_and_override():
    item = {"id": 1, "rules": [{"rule_type": "permanent"}]}
    groups = [{"id": 5, "name": "Night", "rules": [{"rule_type": "scheduled", "schedule": NIGHT},
                                                   {"rule_type": "time_limit", "daily_limit_min": 60}],
               "members": {1: {"scheduled": {"schedule": NIGHT, "allowance_min": 5}}}},
              {"id": 6, "name": "Other", "rules": [{"rule_type": "permanent"}], "members": {2: {}}}]
    rules = effective_rules(item, groups)
    assert [(r["rule_type"], (r["group"] or {}).get("name")) for r in rules] == \
        [("permanent", None), ("scheduled", "Night"), ("time_limit", "Night")]
    assert rules[1]["allowance_min"] == 5                  # member customization
    assert rules[2]["usage_owner"] == "group:5"            # shared group limit


def test_allowance_in_blocked_hours():
    rule = {"rule_type": "scheduled", "schedule": NIGHT, "allowance_min": 5, "rule_key": "k", "item_owner": "item:1"}
    assert rule_block(rule, at(0, 22), used(4 * 60)) is None
    assert rule_block(rule, at(0, 22), used(5 * 60)) == ("schedule", at(1, 7))
    assert rule_block(rule, at(0, 12), used(999)) is None          # outside blocked hours: free
    assert ("item:1", "win:k:2026-09-15T07:00") in usage_targets([rule], 1, at(0, 22))


def test_next_block_schedule_and_limit():
    allow = {"rule_type": "scheduled", "schedule": ALLOW_WORK}
    assert next_block([allow], at(0, 16, 50))[0] == at(0, 17)       # allowed window ends at 17:00
    block = {"rule_type": "scheduled", "schedule": NIGHT}
    assert next_block([block], at(0, 20, 40))[0] == at(0, 21)
    limit = {"rule_type": "time_limit", "daily_limit_min": 30, "usage_owner": "item:1"}
    assert next_block([limit], at(0, 12), used(20 * 60)) is None                    # not in use: unknown
    assert next_block([limit], at(0, 12), used(20 * 60), in_use=True)[0] == at(0, 12, 10)


def test_item_block_returns_rule():
    rule = {"rule_type": "permanent", "group": {"id": 1, "name": "G"}}
    assert item_block([rule], at(0, 0))[2]["group"]["name"] == "G"


def test_duration_text_days():
    from rules import duration_text
    assert duration_text(90 * 60) == "1h 30m"
    assert duration_text(3 * 86400 + 2 * 3600) == "3d 2h"


def test_switch_limit():
    rule = {"rule_type": "switch_limit", "daily_switch_limit": 3, "usage_owner": "item:1"}
    assert rule_block(rule, at(0, 12), used(3)) is None                  # 3 openings allowed
    assert rule_block(rule, at(0, 12), used(4)) == ("switches", at(1, 0))
    assert describe_rule(rule, at(0, 12), used(2)) == "Switches: opened 2 / 3 times today"
    from rules import switch_targets
    group_rule = {**rule, "usage_owner": "group:7"}
    assert switch_targets([group_rule], 1, at(0, 12)) == {("item:1", "sw:2026-09-14"), ("group:7", "sw:2026-09-14")}
