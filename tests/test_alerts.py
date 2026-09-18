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
    s = settings(warn_minutes="20", warn_repeat_min="5")
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
    assert msgs == ["Night schedule started: Discord, Steam blocked until Tuesday 07:00."]


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
