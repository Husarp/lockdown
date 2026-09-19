import json

import blockcal


def sched(mode, windows):
    return {"rule_type": "scheduled", "schedule": json.dumps({"mode": mode, "windows": windows})}


def test_permanent_is_all_day():
    assert blockcal.item_day_intervals([{"rule_type": "permanent"}], 0) == [(0, 1440)]


def test_block_window_same_day():
    r = sched("block", [{"days": [0, 1], "start": "22:30", "end": "23:30"}])
    assert blockcal.item_day_intervals([r], 0) == [(1350, 1410)]
    assert blockcal.item_day_intervals([r], 2) == []          # not Wednesday


def test_block_window_overnight_spills_to_next_morning():
    r = sched("block", [{"days": [0], "start": "22:00", "end": "07:00"}])   # Monday night
    assert blockcal.item_day_intervals([r], 0) == [(1320, 1440)]           # Mon 22:00-24:00
    assert blockcal.item_day_intervals([r], 1) == [(0, 420)]               # Tue 00:00-07:00
    assert blockcal.item_day_intervals([r], 2) == []


def test_allow_mode_blocks_outside_the_window():
    r = sched("allow", [{"days": [0, 1, 2, 3, 4], "start": "18:00", "end": "22:00"}])
    assert blockcal.item_day_intervals([r], 0) == [(0, 1080), (1320, 1440)]   # blocked except 18:00-22:00
    assert blockcal.item_day_intervals([r], 5) == [(0, 1440)]                 # Saturday: no allowed window -> all day


def test_two_rules_union():
    a = sched("block", [{"days": [0], "start": "09:00", "end": "10:00"}])
    b = sched("block", [{"days": [0], "start": "09:30", "end": "11:00"}])
    assert blockcal.item_day_intervals([a, b], 0) == [(540, 660)]            # merged 09:00-11:00


def test_lane_packing_stacks_overlaps():
    bars = [{"start": 0, "end": 100}, {"start": 50, "end": 150}, {"start": 200, "end": 250}]
    n = blockcal.lanes(bars)
    assert n == 2
    assert {b["end"]: b["lane"] for b in bars} == {100: 0, 150: 1, 250: 0}
