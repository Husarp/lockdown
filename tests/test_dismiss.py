"""The X button: close a reminder without saying you did it.

Without it, a reminder that has run out of snoozes can only be cleared with Done - which counts towards the
daily limit and starts the "did you actually do it?" check. That makes you lie to your own statistics to get a
popup off the screen."""
from datetime import datetime, timedelta

import reminders
from tests.test_reminders import FakeUI, run, setup

NOW = datetime(2026, 9, 21, 9, 0)        # Monday 09:00


def _save(db, *items):
    reminders.save(db, reminders.CUSTOM_KEY, [{**reminders.DEFAULT_CUSTOM, **i} for i in items])


def _shown(ui):
    return [s for s in ui.shown if s[0] == "popup" and s[1].startswith("custom:")]


def _buttons(ui):
    return _shown(ui)[-1][4]


def test_every_reminder_offers_a_way_out(tmp_path):
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Push-ups", "kind": "interval", "every": 1})
    run(e, NOW, 2)
    assert _buttons(ui) == ["done", "snooze", "dismiss"]


def test_the_way_out_survives_running_out_of_snoozes(tmp_path):
    """The case that trapped you: no snoozes left, and Done is not true."""
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Push-ups", "kind": "interval", "every": 1, "max_snooze": 0})
    run(e, NOW, 2)
    assert _buttons(ui) == ["done", "dismiss"]


def test_dismiss_is_not_done(tmp_path):
    """The daily limit counts Done. Dismissing must not spend one."""
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 1, "per_day": 1})
    t = run(e, NOW, 2)
    e.answer("custom:a", "dismiss")
    assert reminders.counts(db, "a", NOW - timedelta(days=1)).get("done") is None
    t = run(e, t, 2)                                  # the day is not used up: it comes back
    assert len(_shown(ui)) == 2
    e.answer("custom:a", "done")                      # 1 of 1 - now it is
    before = len(_shown(ui))
    run(e, t, 5)
    assert len(_shown(ui)) == before


def test_dismiss_asks_again_at_its_normal_time(tmp_path):
    """Skip this one, not the rest of the day: an "every 1 min of use" reminder returns after another minute."""
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Push-ups", "kind": "interval", "every": 1})
    t = run(e, NOW, 2)
    e.answer("custom:a", "dismiss")
    assert len(_shown(ui)) == 1
    run(e, t, 2)
    assert len(_shown(ui)) == 2


def test_a_dismissed_reminder_at_a_set_time_waits_for_tomorrow(tmp_path):
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Vitamins", "kind": "times", "times": ["09:00"]})
    run(e, NOW, 2)
    e.answer("custom:a", "dismiss")
    run(e, NOW + timedelta(minutes=5), 20)            # still today: it does not come back
    assert len(_shown(ui)) == 1


def test_dismiss_skips_the_did_you_do_it_check(tmp_path):
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Push-ups", "kind": "interval", "every": 1, "check": 5})
    t = run(e, NOW, 2)
    e.answer("custom:a", "dismiss")
    run(e, t + timedelta(minutes=6), 1)
    assert not [s for s in ui.shown if s[1:2] == ("check:a",)]


def test_it_is_written_down_as_dismissed(tmp_path):
    """Not done, not snoozed - its own result, so the statistics stay honest."""
    db, ui, e = setup(tmp_path)
    _save(db, {"id": "a", "text": "Push-ups", "kind": "interval", "every": 1})
    run(e, NOW, 2)
    e.answer("custom:a", "dismiss")
    assert reminders.counts(db, "a", NOW - timedelta(days=1)) == {"dismissed": 1}


def test_a_gentle_break_can_be_skipped(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "every": 1, "strict": False})
    t = run(e, NOW, 2)
    shown = [s for s in ui.shown if s[1] == "break"][-1]
    assert shown[4] == ["start", "snooze", "dismiss"]
    e.answer("break", "dismiss")
    assert e.continuous == 0                          # asks again after another full stretch, not at once
    assert reminders.counts(db, "break", NOW - timedelta(days=1)).get("taken") is None


def test_a_strict_break_keeps_its_friction(tmp_path):
    """A strict break exists to be hard to wave away; one click out of it would defeat the point."""
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "every": 1, "strict": True})
    run(e, NOW, 2)
    shown = [s for s in ui.shown if s[1] == "break"][-1]
    assert "dismiss" not in shown[4]
