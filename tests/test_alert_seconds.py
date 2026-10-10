"""Alert intervals in seconds (2026-10-08): the bedtime "comes back" steps, a break's "every" and snooze, your
reminders' "every" and the "N min left" repeats can be set to e.g. 5 s or 30 s, for very annoying alerts.

- stored in seconds ("every_sec", "repeat_sec", "snooze_sec", notify.warn.repeat_sec); what was saved in minutes is
  read through (reminders.in_seconds, alerts.get) - an old backup too
- the boxes take "30s", "5 min", a bare 5 (= minutes, as before), "1h30"; 5 s is the least (one tick)
- an interval under a minute isn't held back by the pace (that is what you asked for); a minute or more still is
- the tray ticks sooner when something timed is due before the next tick, so 5 s means 5 s

Also the bedtime step after midnight (a step before a bedtime after midnight never applied) - the phone's rule
since 0.5.14."""
import itertools
from datetime import datetime, timedelta

import pytest

import alerts
import reminders
from alerts import BlockWatcher
from tests.test_alerts import GROUP, ITEMS, no_usage, settings
from tests.test_reminders import setup

NOW = datetime(2026, 9, 14, 9, 0)   # Monday


def _popups(ui, key):
    return [s for s in ui.shown if s[0] == "popup" and s[1] == key]


def _overlays(ui, key="sleep"):
    return [s for s in ui.shown if s[0] == "overlay" and s[1] == key]


def _tick(e, t, seconds, step=reminders.TICK_SEC):
    """Ticks every `step` seconds for `seconds`, from t; returns the time after."""
    for _ in range(int(seconds / step)):
        e.tick(t, 0, False, dt=step)
        t += timedelta(seconds=step)
    return t


# ---------------------------------------------------------------- typing and showing

def test_the_boxes_take_seconds_minutes_and_hours():
    assert reminders.parse_interval("30s") == 30
    assert reminders.parse_interval("30 s") == 30
    assert reminders.parse_interval("5 sec") == 5
    assert reminders.parse_interval("5 min") == 300
    assert reminders.parse_interval("5") == 300          # a bare number is minutes, as it always was
    assert reminders.parse_interval("1.5") == 90
    assert reminders.parse_interval("1h30") == 5400
    assert reminders.parse_interval("off", allow_off=True) == 0
    for bad in ("4s", "0", "off", "soon", "25h"):
        with pytest.raises(ValueError):
            reminders.parse_interval(bad)


def test_an_interval_is_shown_the_way_you_would_type_it():
    assert [reminders.seconds_text(s) for s in (5, 30, 90, 300, 3600, 5400, 0)] == \
        ["5s", "30s", "90s", "5 min", "1h", "1h30", "Off"]
    assert reminders.schedule_text({**reminders.DEFAULT_CUSTOM, "every_sec": 30}) == "every 30s of use"


# ---------------------------------------------------------------- what was saved in minutes

def test_settings_saved_in_minutes_are_read_as_seconds(tmp_path):
    db, _ui, _e = setup(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, {"on": True, "repeat": 10, "tiers": [{"from": "23:00", "every": 15}]})
    reminders.save(db, reminders.BREAK_KEY, {"on": True, "every": 30, "snooze": 5})
    reminders.save(db, reminders.CUSTOM_KEY, [{"id": "w", "text": "Water", "every": 20, "snooze": 5}])
    s = reminders.load(db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    assert s["repeat_sec"] == 600 and s["tiers"] == [{"from": "23:00", "every_sec": 900}] and "repeat" not in s
    b = reminders.load(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)
    assert (b["every_sec"], b["snooze_sec"]) == (1800, 300) and "every" not in b and "snooze" not in b
    r = reminders.custom_list(db)[0]
    assert r["every_sec"] == 1200 and "every" not in r
    assert r["snooze"] == 5                               # (a reminder's own snooze stays minutes)


def test_the_repeat_saved_in_minutes_is_read_as_seconds(tmp_path):
    db, _ui, _e = setup(tmp_path)
    assert alerts.get(db, "notify.warn.repeat_sec") == "0"
    db.set_setting("notify.warn.repeat_min", "2")
    assert alerts.get(db, "notify.warn.repeat_sec") == "120"
    db.set_setting("notify.warn.repeat_sec", "30")
    assert alerts.get(db, "notify.warn.repeat_sec") == "30"


# ---------------------------------------------------------------- bedtime

def _bedtime(db, **kw):
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00", **kw})


def test_the_bedtime_screen_comes_back_after_30_seconds(tmp_path):
    db, ui, e = setup(tmp_path)
    _bedtime(db, tiers=[{"from": "23:00", "every_sec": 30}])
    t = datetime(2026, 9, 14, 23, 10)
    e.tick(t, 0, False)
    e.answer("sleep", "dismiss")
    _tick(e, t, 25)                                       # not within 25 s
    assert len(_overlays(ui)) == 1
    _tick(e, t + timedelta(seconds=25), 10)               # back at 30 s
    assert len(_overlays(ui)) == 2


def test_a_step_is_never_under_5_seconds(tmp_path):
    db, _ui, e = setup(tmp_path)
    _bedtime(db, tiers=[{"from": "23:00", "every_sec": 1}])
    s = reminders.load(db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    assert e._sleep_interval(s, datetime(2026, 9, 14, 23, 0), datetime(2026, 9, 14, 23, 5)) == 5


_dirs = itertools.count()


def _back_after(tmp_path, bedtime, tiers, dismiss_at):
    """The seconds until the bedtime screen comes back, dismissed at `dismiss_at` (a fresh database each time)."""
    where = tmp_path / f"n{next(_dirs)}"
    where.mkdir()
    db, _ui, e = setup(where)
    _bedtime(db, bedtime=bedtime, tiers=tiers, repeat_sec=7 * 60)
    s = reminders.load(db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    bed, _wake = e._night(s, dismiss_at)
    return e._sleep_interval(s, bed, dismiss_at)


def test_a_step_before_a_bedtime_after_midnight_counts_from_bedtime(tmp_path):
    """Bedtime 01:00 with a 23:00 step: the step used to be dated the evening AFTER, so it never applied and the
    flat interval did (the phone fixed it in 0.5.14)."""
    steps = [{"from": "23:00", "every_sec": 20 * 60}]
    assert _back_after(tmp_path, "01:00", steps, datetime(2026, 9, 15, 1, 10)) == 20 * 60
    # 00:00 is before a 00:30 bedtime: it counts from bedtime too (it was dated after wake); 03:00 is its own time
    steps = [{"from": "00:00", "every_sec": 5 * 60}, {"from": "03:00", "every_sec": 60}]
    assert _back_after(tmp_path, "00:30", steps, datetime(2026, 9, 15, 0, 40)) == 5 * 60
    assert _back_after(tmp_path, "00:30", steps, datetime(2026, 9, 15, 3, 10)) == 60


def test_steps_at_the_usual_bedtime_work_as_before(tmp_path):
    steps = [{"from": "21:00", "every_sec": 900}, {"from": "00:00", "every_sec": 300}, {"from": "03:00", "every_sec": 60}]
    assert _back_after(tmp_path, "23:00", steps, datetime(2026, 9, 14, 23, 30)) == 900   # 21:00: from bedtime
    assert _back_after(tmp_path, "23:00", steps, datetime(2026, 9, 15, 1, 0)) == 300
    assert _back_after(tmp_path, "23:00", steps, datetime(2026, 9, 15, 4, 0)) == 60
    assert _back_after(tmp_path, "22:00", [{"from": "23:00", "every_sec": 60}],
                       datetime(2026, 9, 14, 22, 30)) == 7 * 60          # before the first step: the flat one


def test_two_steps_at_the_same_time_use_the_more_frequent(tmp_path):
    steps = [{"from": "21:00", "every_sec": 900}, {"from": "22:00", "every_sec": 30}]   # both count from 23:00
    assert _back_after(tmp_path, "23:00", steps, datetime(2026, 9, 14, 23, 5)) == 30


# ---------------------------------------------------------------- breaks

def test_a_break_every_30_seconds_with_a_10_second_snooze(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "every_sec": 30, "snooze_sec": 10,
                                             "text": "Up! {every} min of it."})
    t = _tick(e, NOW, 25)
    assert not _popups(ui, "break")
    t = _tick(e, t, 5)
    shown = _popups(ui, "break")
    assert len(shown) == 1 and shown[0][3].startswith("Up! 30s of it.")      # (not "30s min")
    e.answer("break", "snooze")
    _tick(e, t, 5)
    assert len(_popups(ui, "break")) == 1                                    # not within 5 s
    _tick(e, t + timedelta(seconds=5), 10)
    assert len(_popups(ui, "break")) == 2                                    # back after 10 s


def test_the_break_snooze_button_says_its_time(tmp_path):
    db, _ui, e = setup(tmp_path)
    labels = []
    e.ui.popup = lambda key, title, text, buttons: labels.extend(b[0] for b in buttons)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "every_sec": 30, "snooze_sec": 45})
    _tick(e, NOW, 30)
    assert "Snooze 45s" in labels


def test_a_break_in_whole_minutes_says_what_it_always_said(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "every_sec": 60})
    _tick(e, NOW, 60)
    assert _popups(ui, "break")[0][3].startswith("You've been at the PC for 1 min. Take 5 min away")


# ---------------------------------------------------------------- your reminders

def test_a_reminder_every_30_seconds_is_not_held_by_the_pace(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": False})
    reminders.save(db, reminders.CUSTOM_KEY, [{**reminders.DEFAULT_CUSTOM, "id": "n", "text": "Sit up", "every_sec": 30}])
    t = _tick(e, NOW, 30)
    assert len(_popups(ui, "custom:n")) == 1
    e.answer("custom:n", "done")
    t = _tick(e, t, 25)
    assert len(_popups(ui, "custom:n")) == 1
    _tick(e, t, 5)
    assert len(_popups(ui, "custom:n")) == 2                                 # 30 s later, not 20 min


def test_a_reminder_of_a_minute_or_more_still_keeps_the_pace(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": False})
    reminders.save(db, reminders.CUSTOM_KEY, [{**reminders.DEFAULT_CUSTOM, "id": "m", "text": "Water", "every_sec": 60}])
    t = _tick(e, NOW, 60)
    e.answer("custom:m", "done")
    _tick(e, t, 5 * 60)
    assert len(_popups(ui, "custom:m")) == 1                                 # waits for the pace, as before


# ---------------------------------------------------------------- the tick

def test_the_tick_comes_sooner_when_something_is_due_before_it(tmp_path):
    db, _ui, e = setup(tmp_path)
    _bedtime(db, tiers=[{"from": "23:00", "every_sec": 5}])
    t = datetime(2026, 9, 14, 23, 10)
    assert e.next_tick(t) == reminders.TICK_SEC                              # nothing timed: the usual tick
    e.tick(t, 0, False)
    e.answer("sleep", "dismiss")                                             # back at t + 5 s
    assert e.next_tick(t + timedelta(seconds=2)) == 3
    assert e.next_tick(t + timedelta(seconds=4.9)) == reminders.FAST_TICK_SEC
    assert e.next_tick(t + timedelta(seconds=6)) == reminders.TICK_SEC      # (it's past: the usual tick)


def test_the_tick_comes_sooner_when_a_reminder_after_so_much_use_is_due(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": False})
    reminders.save(db, reminders.CUSTOM_KEY, [{**reminders.DEFAULT_CUSTOM, "id": "n", "text": "Sit up", "every_sec": 7}])
    e.tick(NOW, 0, False, dt=5)                                              # 5 s of use: due in 2 s
    assert e.next_tick(NOW) == 2
    e.tick(NOW + timedelta(seconds=2), 0, False, dt=2)
    assert len(_popups(ui, "custom:n")) == 1                                 # at 7 s, not 10 s
    e.tick(NOW + timedelta(seconds=4), 600, False, dt=2)                     # away from the PC: no hurry
    assert e.next_tick(NOW + timedelta(seconds=4)) == reminders.TICK_SEC


# ---------------------------------------------------------------- "N min left" repeats

def test_the_warning_repeats_every_30_seconds_while_in_use():
    s = settings(warn_minutes="20", warn_repeat_sec="30")
    w = BlockWatcher()
    t0 = datetime(2026, 9, 14, 20, 41)
    assert len(w.check(ITEMS, GROUP, no_usage, t0, {1}, s)) == 1
    assert w.check(ITEMS, GROUP, no_usage, t0 + timedelta(seconds=25), {1}, s) == []
    assert len(w.check(ITEMS, GROUP, no_usage, t0 + timedelta(seconds=30), {1}, s)) == 1
    assert w.check(ITEMS, GROUP, no_usage, t0 + timedelta(seconds=60), set(), s) == []   # not in use: no repeat
