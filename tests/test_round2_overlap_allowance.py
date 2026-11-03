"""Round 2, item 2: one allowance for a continuous union of blocked windows."""
from datetime import datetime, timedelta

import pytest

from rules import BLOCK, Usage, make_schedule, rule_block, schedule_until, usage_targets

MONDAY = datetime(2026, 9, 14)


@pytest.mark.parametrize("windows,start,probes,end", [
    ([([0], "09:00", "11:00"), ([0], "10:00", "12:00")], 9, [10, 11], 12),
    ([([0], "09:00", "10:00"), ([0], "10:00", "12:00")], 9, [10, 11], 12),
    ([([0], "09:00", "11:00"), ([0], "10:00", "12:00"), ([0], "11:30", "13:00")],
     9, [10, 11, 12], 13),
    ([([0], "22:00", "02:00"), ([1], "01:00", "04:00")], 22, [25, 26, 27], 28),
    ([([6], "22:00", "02:00"), ([0], "01:00", "04:00")], -2, [1, 2, 3], 4),
])
def test_exhausted_allowance_does_not_refill_in_continuous_block(windows, start, probes, end):
    rule = {"rule_type": "scheduled", "schedule": make_schedule(BLOCK, windows),
            "allowance_min": 5, "rule_key": "k", "item_owner": "item:1"}
    began = MONDAY + timedelta(hours=start, minutes=5)
    targets = usage_targets([rule], 1, began)
    used = Usage({target: 300 for target in targets})
    assert rule_block(rule, began, used) is not None
    for hour in probes:
        now = MONDAY + timedelta(hours=hour)
        assert rule_block(rule, now, used) is not None, f"Allowance refilled at {now}"
        # Screen-time day buckets correctly change at midnight; only the allowance must stay stable.
        allowance = lambda buckets: {target for target in buckets if target[1].startswith("win:")}
        assert allowance(usage_targets([rule], 1, now)) == allowance(targets)
        assert schedule_until(rule["schedule"], now) == MONDAY + timedelta(hours=end)


def test_a_real_gap_starts_a_fresh_allowance():
    schedule = make_schedule(BLOCK, [([0], "09:00", "10:00"), ([0], "10:01", "12:00")])
    rule = {"rule_type": "scheduled", "schedule": schedule, "allowance_min": 5, "rule_key": "k"}
    before = MONDAY.replace(hour=9)
    used = Usage({target: 300 for target in usage_targets([rule], 1, before)})
    assert rule_block(rule, before, used)
    assert schedule_until(schedule, MONDAY.replace(hour=10)) is None
    assert rule_block(rule, MONDAY.replace(hour=10, minute=1), used) is None


def test_weekly_continuous_block_has_one_stable_allowance():
    schedule = make_schedule(BLOCK, [(list(range(7)), "00:00", "00:00")])
    assert schedule_until(schedule, MONDAY) == datetime.max
    assert schedule_until(schedule, MONDAY + timedelta(days=8, hours=3)) == datetime.max
