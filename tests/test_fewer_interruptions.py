"""Fewer, better interruptions.

On 2026-09-23 a break every 45 minutes, "hydrate" every 45 and pull-ups every 60 came to about forty popups a
day - and most were waved away (the break: 0 taken out of 12). Three changes:
  - a pace: at most one interruption per PACE_MIN minutes; whatever falls due in between waits and arrives
    with the next one, as ONE popup
  - back-off: a reminder dismissed BACKOFF_AFTER times in a row asks half as often for the rest of the day
  - folding: reminders due around a break ride along in the break prompt ("While you're up: ...")"""
from datetime import datetime, timedelta

import reminders
from tests.test_reminders import run, setup

NOW = datetime(2026, 9, 21, 9, 0)        # Monday 09:00


def _save(db, *items):
    reminders.save(db, reminders.CUSTOM_KEY, [{**reminders.DEFAULT_CUSTOM, **i} for i in items])


def _no_break(db):
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": False})


def _popups(ui):
    return [s for s in ui.shown if s[0] == "popup"]


# ---------------------------------------------------------------- the pace

def test_two_reminders_a_few_minutes_apart_come_as_one(tmp_path):
    db, ui, e = setup(tmp_path)
    _no_break(db)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10},
          {"id": "b", "text": "Pull-ups", "kind": "interval", "every": 13})
    run(e, NOW, 11)                                   # water: shown
    e.answer("custom:a", "done")
    run(e, NOW + timedelta(minutes=11), 5)            # pull-ups falls due 2 min after - too soon: it waits
    assert [p[1] for p in _popups(ui)] == ["custom:a"]


def test_what_waited_comes_up_together_when_the_pace_allows(tmp_path):
    db, ui, e = setup(tmp_path)
    _no_break(db)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10},
          {"id": "b", "text": "Pull-ups", "kind": "interval", "every": 13},
          {"id": "c", "text": "Stretch", "kind": "interval", "every": 15})
    t = run(e, NOW, 11)
    e.answer("custom:a", "done")
    run(e, t, reminders.PACE_MIN)                     # b and c fall due in between; both wait
    assert len(_popups(ui)) == 2                      # water, then ONE more - drawn once, not once per line
    last = _popups(ui)[-1]
    assert "Pull-ups" in last[3] and "Stretch" in last[3]


def test_a_queued_reminder_does_not_open_a_second_popup(tmp_path):
    """While a reminder waits for you - queued, or shown in a group - its clock stands still. It used to keep
    running, so a reminder queued for a while was due again the moment it came up, and opened a second window
    next to the first."""
    db, ui, e = setup(tmp_path)
    _no_break(db)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10},
          {"id": "b", "text": "Pull-ups", "kind": "interval", "every": 13},
          {"id": "c", "text": "Stretch", "kind": "interval", "every": 15})
    t = run(e, NOW, 11)
    e.answer("custom:a", "done")
    run(e, t, 30)                                     # released, then left unanswered for a while
    assert len([k for k in e.open if k.startswith("custom:")]) == 1


def test_something_on_screen_is_joined_not_delayed(tmp_path):
    """The pace is about not interrupting AGAIN - one already open simply takes the new one in."""
    db, ui, e = setup(tmp_path)
    _no_break(db)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10},
          {"id": "b", "text": "Pull-ups", "kind": "interval", "every": 11})
    run(e, NOW, 12)                                   # a shown and NOT answered; b due a minute later
    assert "Pull-ups" in _popups(ui)[-1][3] and "Drink water" in _popups(ui)[-1][3]


def test_a_snooze_is_not_held_by_the_pace(tmp_path):
    """Snooze 5 min means 5 minutes - you asked for it back at that time."""
    db, ui, e = setup(tmp_path)
    _no_break(db)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10, "snooze": 5})
    t = run(e, NOW, 11)
    e.answer("custom:a", "snooze")
    run(e, t, 6)
    assert len(_popups(ui)) == 2


# ---------------------------------------------------------------- back-off

def _dismiss_n(e, ui, key, n, start):
    t = start
    for _ in range(n):
        t = run(e, t, reminders.PACE_MIN + 11)
        e.answer(key, "dismiss")
    return t


def test_three_dismisses_in_a_row_halve_it_for_the_day(tmp_path):
    db, ui, e = setup(tmp_path)
    _no_break(db)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10})
    _dismiss_n(e, ui, "custom:a", reminders.BACKOFF_AFTER, NOW)
    assert e.backed_off.get("a") == NOW.date()
    assert e._every("a", 10, NOW) == 20
    assert any("half as often" in t for t in ui.toasts)      # and it says so


def test_doing_it_starts_the_count_again(tmp_path):
    db, ui, e = setup(tmp_path)
    _no_break(db)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10})
    t = _dismiss_n(e, ui, "custom:a", reminders.BACKOFF_AFTER - 1, NOW)
    t = run(e, t, reminders.PACE_MIN + 11)
    e.answer("custom:a", "done")
    _dismiss_n(e, ui, "custom:a", reminders.BACKOFF_AFTER - 1, t)
    assert "a" not in e.backed_off                   # never three in a row


def test_the_back_off_ends_with_the_day(tmp_path):
    db, ui, e = setup(tmp_path)
    e.backed_off["a"] = NOW.date()
    assert e._every("a", 10, NOW + timedelta(days=1)) == 10


# ---------------------------------------------------------------- folding into the break

def _break(db, every=10):
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": True, "every": every})


def test_a_reminder_due_with_the_break_rides_along_in_it(tmp_path):
    db, ui, e = setup(tmp_path)
    _break(db, every=10)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10})
    run(e, NOW, 12)
    shown = _popups(ui)
    assert [p[1] for p in shown] == ["break"]                 # one popup, not two
    assert "While you're up" in shown[-1][3] and "Drink water" in shown[-1][3]


def test_taking_the_break_answers_what_rode_along(tmp_path):
    """Up and away counts as neither done (that's yours to say) nor skipped."""
    db, ui, e = setup(tmp_path)
    _break(db, every=10)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10})
    run(e, NOW, 12)
    e.answer("break", "start")
    assert reminders.counts(db, "a", NOW - timedelta(days=1)) == {"with break": 1}
    assert e.folded == []


def test_dismissing_the_break_dismisses_what_rode_along(tmp_path):
    db, ui, e = setup(tmp_path)
    _break(db, every=10)
    _save(db, {"id": "a", "text": "Drink water", "kind": "interval", "every": 10})
    run(e, NOW, 12)
    e.answer("break", "dismiss")
    assert reminders.counts(db, "a", NOW - timedelta(days=1)) == {"dismissed": 1}
    assert e.streak.get("a") == 1 and e.streak.get("break") == 1


def test_a_break_held_by_a_game_is_announced_once(tmp_path):
    """Over a full-screen game the break waits, with one quiet toast - not one toast per tick."""
    db, ui, e = setup(tmp_path)
    _break(db, every=10)
    run(e, NOW, 20, fullscreen=True)
    assert len([t for t in ui.toasts if t.startswith("Time for a break")]) == 1


def test_a_strict_break_is_never_held_by_the_pace(tmp_path):
    """Out of snoozes, a strict break starts on time - the pace must not become a way to put it off."""
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": True, "every": 10, "strict": True,
                                             "max_snooze": 0})
    e.last_interruption = NOW                         # something interrupted you a moment ago
    run(e, NOW, 12)
    assert any(s[0] == "break_start" for s in ui.shown)
