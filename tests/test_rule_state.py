"""A rule's colour on the Blocking list says what it is doing right now: red while it blocks, orange while it
is about to, green while it isn't."""
import json
from datetime import datetime, timedelta

import pytest

from rules import NEARLY, SOON_MIN, Usage, rule_state

NOW = datetime(2026, 9, 21, 12, 0)
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def _hours(start, end, **extra):
    return {"rule_type": "scheduled", "usage_owner": "item:1", "item_owner": "item:1", "rule_key": "i1scheduled",
            "group": None, "schedule": json.dumps({"mode": "block", "windows": [{"days": EVERY_DAY,
                                                                                 "start": start, "end": end}]}),
            **extra}


def _limit(minutes=60, used=0):
    rule = {"rule_type": "time_limit", "usage_owner": "item:1", "daily_limit_min": minutes, "group": None}
    return rule, Usage({("item:1", f"day:{NOW.date().isoformat()}"): used * 60})


def test_a_rule_that_is_blocking_is_red():
    assert rule_state({"rule_type": "permanent"}, NOW) == "blocked"
    assert rule_state(_hours("11:00", "15:00"), NOW) == "blocked"


def test_hours_about_to_start_are_orange():
    soon = (NOW + timedelta(minutes=SOON_MIN - 1)).strftime("%H:%M")
    later = (NOW + timedelta(minutes=SOON_MIN + 30)).strftime("%H:%M")
    assert rule_state(_hours(soon, "23:00"), NOW) == "soon"
    assert rule_state(_hours(later, "23:00"), NOW) == "allowed"


def test_a_limit_nearly_used_up_is_orange():
    rule, usage = _limit(60, used=int(60 * NEARLY) + 1)
    assert rule_state(rule, NOW, usage) == "soon"
    rule, usage = _limit(60, used=30)
    assert rule_state(rule, NOW, usage) == "allowed"
    rule, usage = _limit(60, used=60)
    assert rule_state(rule, NOW, usage) == "blocked"      # used up: it is blocking


def test_hours_with_an_allowance_left_are_still_red():
    """The hours are on - the allowance is the way out of them, not a reprieve - so the rule stays red and
    what is left of the allowance is a chip of its own (purple)."""
    rule = _hours("11:00", "15:00", allowance_min=20)
    from rules import allowance_bucket, allowance_note, schedule_until
    bucket = allowance_bucket(rule, schedule_until(rule["schedule"], NOW))
    for spent in (0, 5, 19, 20):
        assert rule_state(rule, NOW, Usage({("item:1", bucket): spent * 60})) == "blocked"
    assert allowance_note(rule, NOW, Usage({("item:1", bucket): 5 * 60})) == \
        "+ 20 min allowed during blocked hours (15m left until 15:00)"
    assert allowance_note(rule, NOW, Usage({("item:1", bucket): 20 * 60})) == \
        "+ 20 min allowed during blocked hours (used up until 15:00)"
    assert allowance_note(_hours("11:00", "15:00"), NOW) is None   # no allowance: no second chip


@pytest.mark.parametrize("state, kind", [("blocked", "danger"), ("soon", "warn"), ("allowed", "ok")])
def test_the_colours_the_states_map_to(state, kind):
    from gui.blocking import CHIP_STATES
    from gui.components import CHIP_STYLES
    assert CHIP_STATES[state] == kind and kind in CHIP_STYLES
