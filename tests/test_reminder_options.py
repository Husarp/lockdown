"""Per-reminder hours, a daily limit counted in Done (not in times shown), and two due at once arriving as one
interruption."""
import json
from datetime import datetime, timedelta

import reminders
from tests.test_reminders import FakeUI, run, setup

NOW = datetime(2026, 9, 21, 9, 0)        # Monday 09:00
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]


def _save(db, *items):
    reminders.save(db, reminders.CUSTOM_KEY, [{**reminders.DEFAULT_CUSTOM, **i} for i in items])


def _shown(ui):
    return [s for s in ui.shown if s[0] == "popup" and s[1].startswith("custom:")]


def test_only_in_the_hours_you_picked(tmp_path):
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Push-ups", "kind": "interval", "every": 1, "hours": True,
               "window": ["08:00", "10:00"]})
    run(e, NOW, 2)                                    # 09:00: inside
    assert len(_shown(ui)) == 1
    e.answer("custom:a", "done")
    run(e, NOW.replace(hour=3), 2)                    # 03:00: outside, nothing at all
    assert len(_shown(ui)) == 1


def test_a_window_that_runs_past_midnight(tmp_path):
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Wind down", "kind": "interval", "every": 1, "hours": True,
               "window": ["22:00", "02:00"]})
    run(e, NOW.replace(hour=23), 2)
    assert len(_shown(ui)) == 1


def test_only_on_the_days_you_picked(tmp_path):
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Push-ups", "kind": "interval", "every": 1, "days": [5, 6]})
    run(e, NOW, 2)                                    # a Monday
    assert not _shown(ui)


def test_the_limit_counts_what_you_did_not_what_you_saw(tmp_path):
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 1, "per_day": 2, "snooze": 5})
    t = run(e, NOW, 2)
    e.answer("custom:a", "done")                      # 1 of 2
    t = run(e, t, reminders.PACE_MIN + 1)             # (the next one waits for the pace)
    assert len(_shown(ui)) == 2
    e.answer("custom:a", "snooze")                    # snoozing is not doing it: still 1 of 2
    t = run(e, t, 6)
    assert len(_shown(ui)) == 3
    e.answer("custom:a", "done")                      # 2 of 2 - that's the day done
    run(e, t, 60)
    assert len(_shown(ui)) == 3


def test_a_reminder_queued_by_the_pace_still_respects_its_hours(tmp_path):
    """It waited for the pace - and its hours ended meanwhile. It must not come up after them."""
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": False})
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10},
          {"id": "b", "text": "Pull-ups", "kind": "interval", "every": 12, "hours": True,
           "window": ["08:00", "09:15"]})
    t = run(e, NOW, 11)                               # water at 09:10
    e.answer("custom:a", "done")
    run(e, t, 30)                                     # pull-ups due 09:12 - queued - its hours end 09:15
    assert not [s for s in _shown(ui) if "Pull-ups" in s[3]]


def test_two_due_at_once_interrupt_you_once(tmp_path):
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 1},
          {"id": "b", "text": "Stretch", "kind": "interval", "every": 1})
    run(e, NOW, 2)
    last = _shown(ui)[-1]
    assert "Drink water" in last[3] and "Stretch" in last[3]      # one popup, both lines
    assert last[2] == "Reminders"

    e.answer(last[1], "done")                                     # one Done answers both
    today = datetime.combine(NOW.date(), datetime.min.time())
    assert reminders.counts(db, "a", today).get("done") == 1
    assert reminders.counts(db, "b", today).get("done") == 1
