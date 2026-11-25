""""What you are using is about to be blocked" gets through a muting mode, Do not disturb and Mute 1 h - it is
the one notice worth interrupting a game for. Everything else stays quiet."""
import json
from datetime import datetime, timedelta

import alerts
from rules import Usage

NOW = datetime(2026, 9, 21, 20, 55)      # five minutes before a 21:00 block
SETTINGS = {"notify.warn.enabled": "1", "notify.warn.minutes": "10", "notify.warn.repeat_sec": "0",
            "notify.started.enabled": "1"}


def _item(item_id, name, start="21:00", end="05:00"):
    return {"id": item_id, "display_name": name, "item_type": "app", "target": f"{name.lower()}.exe",
            "block_type": "close", "app_path": None, "notify": None, "disabled": 0,
            "rules": [{"rule_type": "scheduled",
                       "schedule": json.dumps({"mode": "block",
                                               "windows": [{"days": [0, 1, 2, 3, 4, 5, 6],
                                                            "start": start, "end": end}]})}]}


def test_a_warning_about_what_you_are_using_is_urgent():
    watcher = alerts.BlockWatcher()
    messages = watcher.check([_item(1, "Discord")], [], Usage(), NOW, {1}, SETTINGS)
    assert messages and set(messages) == watcher.urgent


def test_a_warning_about_something_you_are_not_using_is_not():
    watcher = alerts.BlockWatcher()
    messages = watcher.check([_item(1, "Discord")], [], Usage(), NOW, set(), SETTINGS)
    assert messages and not watcher.urgent


def test_a_block_starting_on_what_you_are_in_is_urgent():
    watcher = alerts.BlockWatcher()
    watcher.prev_blocked = set()
    started = datetime(2026, 9, 21, 21, 1)
    messages = watcher.check([_item(1, "Discord")], [], Usage(), started, {1}, SETTINGS)
    assert any("now blocked" in m for m in messages)
    assert watcher.urgent == set(messages)

    watcher = alerts.BlockWatcher()
    watcher.prev_blocked = set()
    watcher.check([_item(1, "Discord")], [], Usage(), started, set(), SETTINGS)
    assert not watcher.urgent


def test_a_coincident_step_is_consumed_even_if_the_user_enters_just_after_the_general_lead():
    item = _item(1, "Discord")
    settings = {**alerts.DEFAULTS, "notify.warn.minutes": "5", "notify.front.steps": "5, 1"}
    watcher = alerts.BlockWatcher()
    (lead,) = watcher.check([item], [], Usage(), NOW, set(), settings)
    assert lead.triggers == ("lead:300s",)
    assert watcher.check([item], [], Usage(), NOW + timedelta(seconds=1), {1}, settings) == []
    (later,) = watcher.check([item], [], Usage(), NOW + timedelta(minutes=4), {1}, settings)
    assert later.triggers == ("in-use:60s",) and later.episode == lead.episode
    assert later.occurrence != lead.occurrence


def test_late_entry_uses_nearest_crossed_step_and_leaves_later_steps_armed():
    settings = {**alerts.DEFAULTS, "notify.warn.minutes": "10", "notify.front.steps": "5, 1"}
    watcher = alerts.BlockWatcher()
    t = NOW + timedelta(seconds=10)
    item = _item(1, "Discord")
    (early,) = watcher.check([item], [], Usage(), t, {1}, settings)
    assert early.triggers == ("in-use:300s",)
    assert watcher.check([item], [], Usage(), t + timedelta(seconds=1), {1}, settings) == []
    (later,) = watcher.check([item], [], Usage(), t + timedelta(minutes=4), {1}, settings)
    assert later.triggers == ("in-use:60s",)


def test_time_limit_rearms_at_the_limit_reset_and_after_an_exhausted_limit_is_extended():
    from rules import LimitClock, extra_time_bucket, time_bucket

    time = datetime(2026, 9, 22, 2, 59)
    clock = LimitClock('{"time":"03:00"}')
    item = {**_item(1, "Discord"), "rules": [{"rule_type": "time_limit", "daily_limit_min": 10}]}
    watcher = alerts.BlockWatcher()
    old = time_bucket("day", time, clock)
    new = time_bucket("day", time + timedelta(minutes=1), clock)
    counters = {("item:1", old): 570, ("item:1", new): 570}
    usage = Usage(counters, clock)
    (first,) = watcher.check([item], [], usage, time, {1}, alerts.DEFAULTS)
    time += timedelta(minutes=1)
    (second,) = watcher.check([item], [], usage, time, {1}, alerts.DEFAULTS)
    assert first.episode != second.episode and first == second
    counters[("item:1", new)] = 600
    (started,) = watcher.check([item], [], usage, time + timedelta(seconds=1), {1}, alerts.DEFAULTS)
    assert type(started) is str and "now blocked" in started
    counters[("item:1", extra_time_bucket(time, clock))] = 60
    (third,) = watcher.check([item], [], usage, time + timedelta(seconds=2), {1}, alerts.DEFAULTS)
    assert third.episode == second.episode and third.occurrence != second.occurrence


def test_allowance_start_and_low_are_distinct_and_a_new_stretch_rearms_them():
    from rules import allowance_bucket, effective_rules

    item = _item(1, "Discord")
    item["rules"][0]["allowance_min"] = 12
    watcher = alerts.BlockWatcher()
    time = NOW + timedelta(minutes=5)
    (start,) = watcher.check([item], [], Usage(), time, {1}, alerts.DEFAULTS)
    assert start.kind == "allowance" and start.triggers == ("start",)
    (rule,) = effective_rules(item, [])
    bucket = allowance_bucket(rule, time.replace(hour=5) + timedelta(days=1))
    usage = Usage({("item:1", bucket): 7 * 60})
    (low,) = watcher.check([item], [], usage, time + timedelta(minutes=7), {1}, alerts.DEFAULTS)
    assert low.kind == "allowance" and low.triggers == ("lead:300s",) and low.episode == start.episode
    (next_start,) = watcher.check([item], [], Usage(), time + timedelta(days=1), {1}, alerts.DEFAULTS)
    assert next_start.triggers == ("start",) and next_start.episode != start.episode


def test_pause_and_emergency_warnings_have_an_explicit_prediction_kind():
    item = {**_item(1, "Discord"), "rules": [{"rule_type": "permanent"}]}
    ends = NOW + timedelta(minutes=4)
    for kind, usage in [("pause", Usage(paused=ends)), ("unlock", Usage(unlocks={"item:1": ends}))]:
        (warning,) = alerts.BlockWatcher().check([item], [], usage, NOW, set(), alerts.DEFAULTS)
        assert warning.kind == kind and warning.episode == (kind, ends)


def test_weekly_limit_does_not_rearm_at_a_calendar_day_boundary():
    from rules import time_bucket

    sunday = datetime(2026, 9, 20, 12)
    days = [sunday, sunday + timedelta(days=1), sunday + timedelta(days=2)]
    item = {**_item(1, "Discord"), "rules": [{"rule_type": "time_limit", "weekly_limit_min": 10}]}
    usage = Usage({("item:1", time_bucket("week", day)): 570 for day in days})
    watcher = alerts.BlockWatcher()
    (first,) = watcher.check([item], [], usage, days[0], {1}, alerts.DEFAULTS)
    (next_period,) = watcher.check([item], [], usage, days[1], {1}, alerts.DEFAULTS)
    assert next_period.episode != first.episode
    assert watcher.check([item], [], usage, days[2], {1}, alerts.DEFAULTS) == []
