"""Limit periods: reset time, weekly/monthly limits (stacked), emergency unlocks."""
import json
from datetime import datetime, timedelta

import pytest

import emergency
from db import Database
from rules import (LimitClock, Usage, apply_reset_now, change_reset, describe_rule, effective_rules,
                   item_block, next_block, reset_is_looser, rule_block, usage_targets)


def at(day, hh, mm=0):   # 2026-09-14 is a Monday
    return datetime(2026, 9, 14) + timedelta(days=day, hours=hh, minutes=mm)


def clock(t: str) -> LimitClock:
    return LimitClock(json.dumps({"time": t}))


def test_default_day_is_midnight_with_old_keys():
    c = LimitClock()
    assert c.day(at(0, 12)) == (at(0, 0), at(1, 0))
    assert c.period("day", at(0, 12)) == ("2026-09-14", at(1, 0))
    assert c.period("week", at(2, 12)) == ("w2026-09-14", at(7, 0))
    assert c.period("month", at(2, 12)) == ("m2026-09", datetime(2026, 10, 1))


def test_reset_at_4am():
    c = clock("04:00")
    assert c.day(at(1, 3)) == (at(0, 4), at(1, 4))       # Tue 03:00 still belongs to Monday
    assert c.period("week", at(7, 3)) == ("w2026-09-14", at(7, 4))   # next Monday 03:00: old week until 04:00
    assert c.period("week", at(7, 5))[0] == "w2026-09-21"


def test_late_reset_starts_week_the_evening_before():
    c = clock("23:00")
    assert c.period("week", at(6, 23, 30)) == ("w2026-09-21", at(13, 23))   # Sunday 23:30 = new week


def test_change_never_shortens_the_day():
    now = at(0, 22)   # Monday 22:00, midnight resets
    cfg = change_reset(None, "22:05", now)
    c = LimitClock(cfg)
    assert c.day(at(0, 22, 10)) == (at(0, 0), at(1, 22, 5))   # no fresh day at 22:05 today
    assert c.period("day", at(0, 22, 10))[0] == "2026-09-14"     # same counter as before the change
    assert c.day(at(1, 22, 6)) == (at(1, 22, 5), at(2, 22, 5))
    cfg = change_reset(None, "04:00", now)
    assert LimitClock(cfg).day(at(1, 1)) == (at(0, 0), at(1, 4))  # day runs to 04:00 tomorrow (28 h)


def test_an_earlier_time_leaves_the_running_day_alone():
    """Moving the reset back doesn't cut the day you are in short - and doesn't stretch it either: it simply
    takes over when that day ends, so the day AFTER it is the shorter one."""
    cfg = change_reset(None, "04:00", at(0, 22))      # Monday 22:00: the day now runs to Tue 04:00
    cfg = change_reset(cfg, "01:00", at(0, 23))       # back to 01:00: Monday still ends Tue 04:00 ...
    assert LimitClock(cfg).day(at(1, 3)) == (at(0, 0), at(1, 4))
    assert LimitClock(cfg).day(at(1, 12)) == (at(1, 1), at(2, 1))   # ... and 01:00 applies from then on


def test_a_later_time_stretches_the_running_day_once():
    cfg = change_reset(None, "04:00", at(0, 22))      # Monday 22:00 -> the day runs to Tue 04:00 (28 h)
    assert LimitClock(cfg).day(at(1, 1)) == (at(0, 0), at(1, 4))
    cfg = change_reset(cfg, "06:00", at(1, 1))        # again, while it is still running: not stretched twice
    assert LimitClock(cfg).day(at(1, 1))[1] <= at(1, 4) + timedelta(days=1)


def test_toggling_the_reset_time_cannot_stack_past_the_next_day():
    now = at(0, 22)   # Monday 22:00
    cfg = None
    for t in ("23:00", "00:30", "23:30", "01:00", "23:00", "02:00"):   # griefer toggles it back and forth
        cfg = change_reset(cfg, t, now)
    _, end = LimitClock(cfg).day(now)
    assert end <= now + timedelta(days=2)   # never stretched more than to the end of the next day


def test_change_never_starts_the_week_early():
    cfg = json.dumps({"time": "23:00"})                  # weeks start Sunday 23:00
    cfg = change_reset(cfg, "12:00", at(5, 22))          # Saturday 22:00
    c = LimitClock(cfg)
    assert c.period("week", at(6, 13)) == ("w2026-09-14", at(6, 23))   # Sunday 13:00: still the old week
    assert c.period("week", at(6, 23, 30))[0] == "w2026-09-21"


def test_stacked_time_limits():
    rule = {"rule_type": "time_limit", "daily_limit_min": 120, "weekly_limit_min": 480, "usage_owner": "item:1"}
    now = at(2, 12)   # Wednesday
    usage = Usage({("item:1", "day:2026-09-16"): 30 * 60, ("item:1", "week:w2026-09-14"): 480 * 60})
    assert rule_block(rule, now, usage) == ("limit", at(7, 0))      # week used up: blocked until Monday
    usage = Usage({("item:1", "day:2026-09-16"): 120 * 60, ("item:1", "week:w2026-09-14"): 200 * 60})
    assert rule_block(rule, now, usage) == ("limit", at(3, 0))      # only today's limit
    assert describe_rule(rule, now, usage) == "Limit: 2h 00m / 2h 00m today\n3h 20m / 8h 00m this week"
    assert usage_targets([rule], 1, now) == {("item:1", "day:2026-09-16"), ("item:1", "week:w2026-09-14")}


def test_monthly_opening_limit():
    rule = {"rule_type": "switch_limit", "monthly_switch_limit": 5, "usage_owner": "item:1", "rule_key": "k"}
    assert rule_block(rule, at(2, 12), Usage({("item:1", "op:k:m2026-09"): 6})) == ("switches", datetime(2026, 10, 1))


def test_limits_use_the_reset_time():
    rule = {"rule_type": "time_limit", "daily_limit_min": 60, "usage_owner": "item:1"}
    usage = Usage({("item:1", "day:2026-09-14T04:00"): 3600}, clock("04:00"))
    assert rule_block(rule, at(1, 3), usage) == ("limit", at(1, 4))   # Tue 03:00: still Monday's limit day


def test_unlock_lifts_every_block_and_warns_before_end():
    item = {"id": 1, "rules": [{"rule_type": "permanent"}]}
    rules = effective_rules(item, [])
    usage = Usage(unlocks={"item:1": at(0, 12, 20)})
    assert item_block(rules, at(0, 12), usage) is None
    assert next_block(rules, at(0, 12), usage) == (at(0, 12, 20), {"rule_type": "unlock", "group": None})
    assert item_block(rules, at(0, 12, 20), usage)[0] == "permanent"


def test_emergency_uses(tmp_path):
    db = Database(tmp_path / "t.db")
    iid = db.add_site("YouTube", ["youtube.com"])
    items = [{"id": iid, "display_name": "YouTube"}, {"id": 99, "display_name": "Discord"}]
    assert emergency.uses_left(db, at(0, 12))[:2] == (3, 3)
    until = emergency.unlock(db, items, at(0, 12))                 # two items = one use
    assert until == at(0, 12, 20)
    assert emergency.uses_left(db, at(0, 13)) == (2, 3, at(7, 0))
    assert db.blocked_hostnames(at(0, 12, 10)) == []
    assert db.blocked_hostnames(at(0, 12, 20)) == ["youtube.com"]
    emergency.unlock(db, items[:1], at(1, 12))
    emergency.unlock(db, items[:1], at(2, 12))
    with pytest.raises(ValueError, match="No emergency"):
        emergency.unlock(db, items[:1], at(3, 12))
    assert emergency.uses_left(db, at(7, 1))[0] == 3               # new week
    db.set_setting("emergency.per", "day")
    db.set_setting("emergency.uses", "1")
    assert emergency.uses_left(db, at(2, 18))[0] == 0
    assert emergency.uses_left(db, at(3, 1))[0] == 1


def test_a_day_stretched_twice_by_an_older_version_is_repaired():
    """0.5-0.67 could stack changes into a 45-hour day whose end no longer matched the reset time: the clock
    now caps a carried day at two days from its start, which puts it back on the real boundary."""
    cfg = json.dumps({"time": "03:00", "day_start": "2026-09-19 00:00:00", "switch": "2026-09-22 00:00:00",
                      "hold": {}})
    assert LimitClock(cfg).day(datetime(2026, 9, 21, 8, 56)) == (datetime(2026, 9, 21, 3, 0),
                                                                 datetime(2026, 9, 22, 3, 0))


def test_only_an_earlier_reset_time_asks_anti_bypass():
    cfg = json.dumps({"time": "03:00"})
    assert reset_is_looser(cfg, "00:00", at(0, 22))       # a day ends sooner than it would have
    assert not reset_is_looser(cfg, "05:00", at(0, 22))   # a day only gets longer
    assert not reset_is_looser(cfg, "nonsense", at(0, 22))


def test_starting_the_new_reset_time_at_once():
    """Behind the challenge: the day you are in ends now, a fresh one starts - but the week doesn't end early."""
    cfg = json.dumps({"time": "03:00"})
    now = at(1, 12)                                   # Tuesday 12:00, the limit day started Tue 03:00
    fresh = apply_reset_now(cfg, "01:00", now)
    c = LimitClock(fresh)
    assert c.day(now) == (at(1, 1), at(2, 1))         # counted from 01:00 today: a different day key
    assert c.period("day", now)[0] != LimitClock(cfg).period("day", now)[0]
    assert c.period("week", now) == LimitClock(cfg).period("week", now)   # the week is held where it was
