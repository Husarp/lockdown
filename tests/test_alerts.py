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


# ---------- upcoming-block watcher ----------
from datetime import timedelta

from alerts import DEFAULTS, BlockWatcher
from rules import make_schedule

NIGHT = make_schedule("block", [(list(range(7)), "21:00", "07:00")])
SETTINGS = dict(DEFAULTS)


def settings(**kw):
    """settings(warn_minutes="20") -> notify.warn.minutes = "20" (first "_" is a dot)."""
    return {**SETTINGS, **{"notify." + k.replace("_", ".", 1): v for k, v in kw.items()}}


def app(i, name):
    return {"id": i, "display_name": name, "target": f"{name.lower()}.exe", "item_type": "app", "rules": []}


GROUP = [{"id": 9, "name": "Night schedule", "rules": [{"rule_type": "scheduled", "schedule": NIGHT}],
          "members": {1: {}, 2: {}}}]
ITEMS = [app(1, "Discord"), app(2, "Steam")]
no_usage = lambda o, b: 0


def test_group_warning_is_combined_and_shown_once():
    w = BlockWatcher()
    t0 = datetime(2026, 9, 14, 20, 50)
    assert w.check(ITEMS, GROUP, no_usage, t0, set(), SETTINGS) == []                      # 10 min away
    msgs = w.check(ITEMS, GROUP, no_usage, t0 + timedelta(minutes=6), set(), SETTINGS)
    assert msgs == ["Night schedule starts in 4 min (21:00): Discord, Steam will be blocked."]
    assert w.check(ITEMS, GROUP, no_usage, t0 + timedelta(minutes=7), set(), SETTINGS) == []


def test_repeat_reminder_only_while_in_use():
    s = settings(warn_minutes="20", warn_repeat_sec="300")
    w = BlockWatcher()
    t0 = datetime(2026, 9, 14, 20, 40)
    assert len(w.check(ITEMS, GROUP, no_usage, t0, set(), s)) == 1
    assert w.check(ITEMS, GROUP, no_usage, t0 + timedelta(minutes=6), set(), s) == []      # not in use
    msgs = w.check(ITEMS, GROUP, no_usage, t0 + timedelta(minutes=7), {1}, s)
    assert msgs == ["Night schedule starts in 13 min (21:00): Discord, Steam will be blocked."]


def test_block_started_and_disabled_warnings():
    w = BlockWatcher()
    s = settings(warn_enabled="0")
    assert w.check(ITEMS, GROUP, no_usage, datetime(2026, 9, 14, 20, 58), set(), s) == []
    msgs = w.check(ITEMS, GROUP, no_usage, datetime(2026, 9, 14, 21, 0), set(), s)
    # summarized: one line for the group, not one per member (you didn't open any of them)
    assert msgs == ["Night schedule started - 2 things blocked until Tuesday 07:00."]


def test_limit_warning_while_in_use():
    item = {**app(1, "Discord"), "rules": [{"rule_type": "time_limit", "daily_limit_min": 30}]}
    w = BlockWatcher()
    msgs = w.check([item], [], lambda o, b: 26 * 60, datetime(2026, 9, 14, 12, 0), {1}, SETTINGS)
    assert msgs == ["Discord: 4 min of the time limit left."]
    assert w.check([item], [], lambda o, b: 26 * 60, datetime(2026, 9, 14, 12, 0), set(), SETTINGS) == []


def test_unlock_end_warning():
    from rules import Usage
    items = [{"id": 1, "display_name": "YouTube", "rules": [{"rule_type": "permanent"}]},
             {"id": 2, "display_name": "Discord", "rules": [{"rule_type": "permanent"}]}]
    end = datetime(2026, 9, 14, 12, 20)
    usage = Usage(unlocks={"item:1": end, "item:2": end})
    w = BlockWatcher()
    msgs = w.check(items, [], usage, datetime(2026, 9, 14, 12, 16), set(), SETTINGS)
    assert msgs == ["Emergency unlock ends in 4 min: Discord, YouTube will be blocked again."]


# ---------- blocked because of a category ----------
from alerts import reason_category, tag_reason, base_reason, DEFAULT_MESSAGES


def test_a_category_block_names_the_category():
    tagged = ev(tag_reason("permanent", "distracting"), None)
    assert base_reason(tagged["reason"]) == "permanent" and reason_category(tagged["reason"]) == "distracting"
    cats = {"distracting": "Distracting"}
    assert format_message(DEFAULT_MESSAGES["permanent"], tagged, NOW, categories=cats) \
        == "Reddit is permanently blocked. It's in Distracting."
    assert format_message("{site}: {reason}", tagged, NOW, categories=cats) \
        == "Reddit: permanently blocked (it's in Distracting)"
    assert format_message(DEFAULT_MESSAGES["schedule"], ev("schedule@my-cat", "2026-09-14 23:30:00"), NOW,
                          categories={"my-cat": "Time wasters"}) == "Reddit is blocked until 23:30. It's in Time wasters."
    assert format_message("{site}", ev("limit@gone", None), NOW) == "Reddit. It's in gone."   # unknown key: shown as is


def test_a_block_in_its_own_right_reads_as_before():
    assert tag_reason("permanent", None) == "permanent"
    assert format_message(DEFAULT_MESSAGES["permanent"], ev("permanent", None), NOW, categories={"x": "X"}) \
        == "Reddit is permanently blocked."


# ---------- the warning for the app in front (notify.front.*) ----------
import pytest

from alerts import front_steps, front_steps_text, parse_front_steps
from rules import Usage

T_BLOCK = datetime(2026, 9, 14, 21, 0)       # the night schedule starts


def at(h, m, s=0):
    return datetime(2026, 9, 14, h, m, s)


@pytest.mark.parametrize("text, steps", [
    ("1", [1]), ("5, 1", [5, 1]), ("1,5", [5, 1]), ("5;1", [5, 1]), (" 10 ", [10]), ("30", [30]),
    ("5 min, 1 min", [5, 1]), ("5m 1m", [5, 1]), ("1 minute", [1]), ("5, 5, 1", [5, 1]), ("15, 5, 1", [15, 5, 1]),
])
def test_the_steps_are_read_as_typed(text, steps):
    assert parse_front_steps(text) == steps


@pytest.mark.parametrize("text", ["", "   ", "0", "31", "5, 0", "5, 31", "10, 5, 2, 1", "abc", "1.5", "-1", "5ms", "٣",
                                  "x" * 100, None, 5, ["1"]])
def test_bad_steps_are_refused(text):
    with pytest.raises(ValueError):
        parse_front_steps(text)


def test_steps_are_saved_in_one_form_and_a_bad_saved_value_counts_as_the_default():
    assert front_steps_text([5, 1]) == "5, 1" and front_steps_text(parse_front_steps("1,5")) == "5, 1"
    assert front_steps("5, 1") == [5, 1]
    assert front_steps("garbage") == front_steps(None) == [1]
    assert DEFAULTS["notify.front.enabled"] == "1" and DEFAULTS["notify.front.steps"] == "1"
    assert DEFAULTS["notify.front.sound"] == "1"


def test_the_app_in_front_is_warned_at_its_step_once_and_only_it_is_named():
    w = BlockWatcher()
    assert len(w.check(ITEMS, GROUP, no_usage, at(20, 56), {1}, SETTINGS)) == 1     # 4 min: the ordinary warning
    assert not w.front
    assert w.check(ITEMS, GROUP, no_usage, at(20, 58), {1}, SETTINGS) == []         # 2 min: nothing new
    msgs = w.check(ITEMS, GROUP, no_usage, at(20, 59, 10), {1}, SETTINGS)           # 50 s: the front warning
    assert msgs == ["Night schedule starts in 1 min (21:00): Discord will be blocked."]
    assert w.front == set(msgs) and w.urgent == set(msgs)
    assert w.check(ITEMS, GROUP, no_usage, at(20, 59, 11), {1}, SETTINGS) == []     # once
    assert w.check(ITEMS, GROUP, no_usage, at(20, 59, 40), {1}, SETTINGS) == []
    assert not w.front


def test_the_front_warning_stands_in_for_the_ordinary_one_no_double_notice():
    w = BlockWatcher()
    msgs = w.check(ITEMS, GROUP, no_usage, at(20, 59, 30), {1}, SETTINGS)           # first seen inside the last minute
    assert len(msgs) == 1 and w.front == set(msgs)
    assert w.check(ITEMS, GROUP, no_usage, at(20, 59, 31), {1}, SETTINGS) == []
    assert w.check(ITEMS, GROUP, no_usage, at(20, 59, 32), set(), SETTINGS) == []   # (switched away: still no second one)


def test_something_you_are_not_in_gets_the_ordinary_warning_only():
    w = BlockWatcher()
    msgs = w.check(ITEMS, GROUP, no_usage, at(20, 59, 30), set(), SETTINGS)
    assert msgs == ["Night schedule starts in 1 min (21:00): Discord, Steam will be blocked."]
    assert not w.front and not w.urgent
    assert BlockWatcher().check(ITEMS, GROUP, no_usage, at(20, 59, 30), {3}, SETTINGS) == msgs   # (an item that is in no group)


def test_each_step_is_shown_once_per_block():
    s = settings(front_steps="5, 1")
    w = BlockWatcher()
    assert w.check(ITEMS, GROUP, no_usage, at(20, 53), {1}, s) == []               # 7 min away
    first = w.check(ITEMS, GROUP, no_usage, at(20, 55, 30), {1}, s)                # 4.5 min: the 5 step
    assert first == ["Night schedule starts in 5 min (21:00): Discord will be blocked."] and w.front == set(first)
    assert w.check(ITEMS, GROUP, no_usage, at(20, 57), {1}, s) == []
    second = w.check(ITEMS, GROUP, no_usage, at(20, 59, 5), {1}, s)                # the 1 step
    assert second == ["Night schedule starts in 1 min (21:00): Discord will be blocked."] and w.front == set(second)
    assert w.check(ITEMS, GROUP, no_usage, at(20, 59, 30), {1}, s) == []
    assert w.check(ITEMS, GROUP, no_usage, T_BLOCK + timedelta(days=1, minutes=-1), {1}, s) != []   # the next night is a new block


def test_joining_late_shows_one_warning_not_one_per_step():
    s = settings(front_steps="5, 1")
    w = BlockWatcher()
    assert len(w.check(ITEMS, GROUP, no_usage, at(20, 59, 30), {1}, s)) == 1
    assert w.check(ITEMS, GROUP, no_usage, at(20, 59, 45), {1}, s) == []


def test_the_front_warning_does_not_need_the_ordinary_warnings_switched_on():
    w = BlockWatcher()
    s = settings(warn_enabled="0")
    assert w.check(ITEMS, GROUP, no_usage, at(20, 56), {1}, s) == []
    msgs = w.check(ITEMS, GROUP, no_usage, at(20, 59, 20), {1}, s)
    assert len(msgs) == 1 and w.front == set(msgs)


def test_the_front_steps_may_reach_further_than_the_ordinary_minutes():
    s = settings(warn_minutes="1", front_steps="10")
    w = BlockWatcher()
    assert w.check(ITEMS, GROUP, no_usage, at(20, 49), {1}, s) == []
    msgs = w.check(ITEMS, GROUP, no_usage, at(20, 51), {1}, s)
    assert msgs == ["Night schedule starts in 9 min (21:00): Discord will be blocked."] and w.front == set(msgs)
    again = w.check(ITEMS, GROUP, no_usage, at(20, 59, 30), {1}, s)                # the ordinary 1 min warning is its own step
    assert len(again) == 1 and not w.front


def test_switched_off_the_old_behaviour_is_exact():
    w = BlockWatcher()
    off = settings(front_enabled="0")
    msgs = w.check(ITEMS, GROUP, no_usage, at(20, 59, 30), {1}, off)
    assert msgs == ["Night schedule starts in 1 min (21:00): Discord, Steam will be blocked."]
    assert not w.front and w.urgent == set(msgs)                                    # (urgent: it is what you are using)
    legacy = {k: v for k, v in SETTINGS.items() if not k.startswith("notify.front.")}   # a settings dict from before
    w = BlockWatcher()
    assert w.check(ITEMS, GROUP, no_usage, at(20, 59, 30), {1}, legacy) == msgs and not w.front


def test_a_time_limit_running_out_warns_the_app_in_front():
    item = {**app(1, "Discord"), "rules": [{"rule_type": "time_limit", "daily_limit_min": 30}]}
    s = settings(warn_enabled="0")
    w = BlockWatcher()
    now = datetime(2026, 9, 14, 12, 0)
    assert w.check([item], [], lambda o, b: 26 * 60, now, {1}, s) == []
    msgs = w.check([item], [], lambda o, b: 29 * 60 + 5, now, {1}, s)
    assert msgs == ["Discord: 1 min of the time limit left."] and w.front == set(msgs)
    assert w.check([item], [], lambda o, b: 29 * 60 + 20, now, {1}, s) == []         # once for the day
    assert BlockWatcher().check([item], [], lambda o, b: 29 * 60 + 5, now, set(), s) == []   # (not in front: no prediction)


def test_an_emergency_unlock_ending_warns_the_app_in_front():
    items = [{"id": 1, "display_name": "YouTube", "rules": [{"rule_type": "permanent"}]},
             {"id": 2, "display_name": "Discord", "rules": [{"rule_type": "permanent"}]}]
    end = datetime(2026, 9, 14, 12, 20)
    usage = Usage(unlocks={"item:1": end, "item:2": end})
    w = BlockWatcher()
    msgs = w.check(items, [], usage, datetime(2026, 9, 14, 12, 19, 10), {2}, SETTINGS)
    assert msgs == ["Emergency unlock ends in 1 min: Discord will be blocked again."] and w.front == set(msgs)


def test_an_item_on_its_own_schedule_and_a_group_rule_are_both_found():
    own = {**app(5, "Game"), "rules": [{"rule_type": "scheduled", "schedule": make_schedule("block", [(list(range(7)), "21:00", "07:00")])}]}
    w = BlockWatcher()
    msgs = w.check([own], [], no_usage, at(20, 59, 20), {5}, SETTINGS)
    assert msgs == ["Game will be blocked in 1 min (21:00)."] and w.front == set(msgs)
