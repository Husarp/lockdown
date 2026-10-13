"""Bedtime screen: an optional wait of N seconds before Dismiss works (Adam, 2026-10-08).

Dismiss shows "Dismiss (25)" and stays off until the wait is over, afresh each time the screen comes up.
Disable alerts and Emergency don't wait (they have their own challenge / uses), but backing out of them while
Dismiss is still waiting brings the screen straight back rather than counting as a Dismiss. A shorter wait (or Off)
is loosening and needs the challenge; a longer one is free."""
from datetime import datetime

import pytest

import reminders
from gui import screens
from tests.test_reminders import run, setup

NIGHT = datetime(2026, 10, 8, 23, 0)


def test_off_by_default():
    assert reminders.DEFAULT_SLEEP["dismiss_wait"] == 0
    assert reminders.dismiss_wait(reminders.DEFAULT_SLEEP) == 0
    assert reminders.dismiss_wait({}) == 0                     # settings saved before this: no wait


@pytest.mark.parametrize("typed, seconds", [("Off", 0), ("", 0), ("0", 0), ("25", 25), ("30s", 30),
                                            ("45 sec", 45), ("2 min", 120), ("1m", 60), ("600", 600)])
def test_typed_waits(typed, seconds):
    assert reminders.parse_seconds(typed) == seconds


@pytest.mark.parametrize("typed", ["abc", "-5", "1.5", "601", "11 min"])
def test_bad_or_too_long_waits_are_refused(typed):
    with pytest.raises(ValueError):
        reminders.parse_seconds(typed)


def test_a_stored_wait_is_kept_in_range():
    assert reminders.dismiss_wait({"dismiss_wait": 30}) == 30
    assert reminders.dismiss_wait({"dismiss_wait": -4}) == 0
    assert reminders.dismiss_wait({"dismiss_wait": 99999}) == reminders.MAX_DISMISS_WAIT
    assert reminders.dismiss_wait({"dismiss_wait": "junk"}) == 0


@pytest.mark.parametrize("guarded", [False, True])
def test_shorter_or_off_needs_the_challenge_longer_is_free(guarded):
    old = {**reminders.DEFAULT_SLEEP, "on": True, "guarded": guarded, "dismiss_wait": 30}
    assert reminders.loosens_reminder(old, {**old, "dismiss_wait": 10}) is True
    assert reminders.loosens_reminder(old, {**old, "dismiss_wait": 0}) is True
    assert reminders.loosens_reminder(old, {**old, "dismiss_wait": 60}) is False
    assert reminders.loosens_reminder(old, {**old, "dismiss_wait": 30}) is False
    assert reminders.loosens_reminder({**old, "dismiss_wait": 0}, {**old, "dismiss_wait": 10}) is False


def test_other_reminders_are_unchanged():
    """Breaks and your own reminders have no wait: the same answers as before."""
    assert reminders.loosens_reminder(reminders.DEFAULT_BREAK, {**reminders.DEFAULT_BREAK, "on": False}) is False
    important = {**reminders.DEFAULT_BREAK, "guarded": True}
    assert reminders.loosens_reminder(important, {**important, "on": False}) is True


def test_the_countdown_on_the_button():
    assert screens.dismiss_label(25.0) == ("Dismiss (25)", False, 1015)
    assert screens.dismiss_label(24.4)[:2] == ("Dismiss (25)", False)       # whole seconds, counted up
    assert screens.dismiss_label(24.4)[2] == 415                            # wakes as it turns to 24
    assert screens.dismiss_label(0.2)[:2] == ("Dismiss (1)", False)
    assert screens.dismiss_label(0) == ("Dismiss", True, None)
    assert screens.dismiss_label(-3) == ("Dismiss", True, None)


def _night(db, wait=30):
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00", "tiers": [], "repeat": 5, "dismiss_wait": wait})


def _bedtime_screens(ui):
    return [s for s in ui.shown if s[:2] == ("overlay", "sleep")]


def test_backing_out_while_waiting_brings_the_screen_straight_back(tmp_path):
    db, ui, e = setup(tmp_path)
    _night(db)
    t = run(e, NIGHT, 0.1)
    assert len(_bedtime_screens(ui)) == 1
    e.answer("sleep", "back")
    run(e, t, 0.1)
    assert len(_bedtime_screens(ui)) == 2                   # the next tick, not 5 min later


def test_dismiss_still_comes_back_on_its_escalation(tmp_path):
    db, ui, e = setup(tmp_path)
    _night(db)
    t = run(e, NIGHT, 0.1)
    e.answer("sleep", "dismiss")
    t = run(e, t, 4)
    assert len(_bedtime_screens(ui)) == 1
    run(e, t, 2)
    assert len(_bedtime_screens(ui)) == 2


# ---- each bedtime step's own wait (Adam, 2026-10-12): 22:00 -> 10 s, 00:00 -> 30 s, 02:00 -> 2 min

STEPS = {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "22:00", "wake": "07:00", "dismiss_wait": 20,
         "tiers": [{"from": "22:00", "every_sec": 900, "wait_sec": 10}, {"from": "00:00", "every_sec": 300, "wait_sec": 30},
                   {"from": "02:00", "every_sec": 60, "wait_sec": 120}, {"from": "04:00", "every_sec": 30}]}


def test_a_step_without_its_own_wait_and_old_settings_read_as_none():
    assert reminders.step_wait({"from": "22:00", "every_sec": 60}) is None
    assert reminders.step_wait({"wait_sec": "junk"}) is None and reminders.step_wait(None) is None
    assert reminders.step_wait({"wait_sec": 0}) == 0
    assert reminders.step_wait({"wait_sec": 99999}) == reminders.MAX_DISMISS_WAIT


@pytest.mark.parametrize("at, wait", [((8, 21, 45), 20),     # heads-up, before the night: the general wait
                                      ((8, 22, 0), 10), ((8, 23, 59), 10), ((9, 0, 0), 30), ((9, 1, 30), 30),
                                      ((9, 2, 0), 120), ((9, 3, 59), 120),
                                      ((9, 4, 30), 20)])     # a step with no own wait: the general one
def test_the_wait_is_the_active_steps(at, wait):
    d, h, m = at
    assert reminders.dismiss_wait_now(STEPS, datetime(2026, 10, d, h, m)) == wait


def test_steps_own_waits_count_only_with_the_general_wait_on():
    assert reminders.dismiss_wait_now({**STEPS, "dismiss_wait": 0}, datetime(2026, 10, 9, 2, 30)) == 0


def test_after_midnight_bedtime_places_steps_like_the_interval():
    s = {**STEPS, "bedtime": "01:00", "tiers": [{"from": "23:00", "every_sec": 900, "wait_sec": 45},
                                                 {"from": "03:00", "every_sec": 60, "wait_sec": 90}]}
    assert reminders.dismiss_wait_now(s, datetime(2026, 10, 9, 1, 10)) == 45    # 23:00 is before bedtime: from bedtime
    assert reminders.dismiss_wait_now(s, datetime(2026, 10, 9, 3, 10)) == 90


def test_shorter_step_waits_need_the_challenge_longer_are_free():
    def step(i, **kw):
        return {**STEPS, "tiers": [{**t, **kw} if j == i else t for j, t in enumerate(STEPS["tiers"])]}
    assert reminders.loosens_reminder(STEPS, step(2, wait_sec=60)) is True
    assert reminders.loosens_reminder(STEPS, step(2, wait_sec=300)) is False
    assert reminders.loosens_reminder(STEPS, step(2, **{"from": "03:00"})) is True     # 02:00-03:00 now only 30 s
    assert reminders.loosens_reminder(STEPS, {**STEPS, "tiers": STEPS["tiers"][:2]}) is True   # 02:00 step removed
    assert reminders.loosens_reminder(STEPS, {**STEPS, "dismiss_wait": 0}) is True
    assert reminders.loosens_reminder(STEPS, {**STEPS, "dismiss_wait": 15}) is True     # 04:00 uses the general one
    covered = {**STEPS, "tiers": STEPS["tiers"][:3]}                                  # every minute has its own wait:
    assert reminders.loosens_reminder(covered, {**covered, "dismiss_wait": 5}) is False  # the general one isn't used
    assert reminders.loosens_reminder({**STEPS, "dismiss_wait": 0}, step(2, wait_sec=5)) is False   # no wait was on


def test_the_bedtime_screen_gets_the_active_steps_wait(tmp_path):
    from resident.overlay import Overlay
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, STEPS)
    at = [datetime(2026, 10, 9, 2, 30)]
    o = Overlay(now=lambda: at[0], wait_for=lambda key: reminders.dismiss_wait_now(
        reminders.load(db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP), at[0]), clock=lambda: 0.0)
    o.show("sleep", "Time for bed", "", None, [("Dismiss", "dismiss")])
    assert o._cur["wait_total"] == 120
