"""The bedtime screen gets more insistent the later it is, and turning it off needs the challenge.

The later it gets, the shorter the gap before the "Time for bed" overlay returns after you dismiss it - so
that going to bed is the path of least resistance, not typing the phrase. Dismiss just delays it; the escape
hatch ("Disable alerts") is gated behind the anti-bypass challenge and is the only way to actually stop it."""
from datetime import datetime, timedelta

import reminders
from tests.test_reminders import run, setup

TIERS = [{"from": "21:00", "every": 15}, {"from": "00:00", "every": 5}, {"from": "03:00", "every": 1}]


def _engine(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00", "tiers": TIERS})
    return db, ui, e


def _interval(e, hour, minute=0):
    """The gap the engine would use if you dismissed at this time (bedtime 23:00, so 00:00/03:00 are after
    midnight, on the next date)."""
    s = reminders.load(e.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    bed = datetime(2026, 9, 15, 23, 0)
    now = datetime(2026, 9, 15 if hour >= 21 else 16, hour, minute)
    return e._sleep_interval(s, bed, now)


def test_it_escalates_through_the_night(tmp_path):
    _db, _ui, e = _engine(tmp_path)
    assert _interval(e, 23) == 15      # 23:00 - the 21:00 tier
    assert _interval(e, 0) == 5        # after midnight - the 00:00 tier
    assert _interval(e, 1) == 5
    assert _interval(e, 3) == 1        # after 3am - one a minute, relentless
    assert _interval(e, 5) == 1


def test_before_the_first_tier_it_falls_back_to_repeat(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "20:00",
                                             "wake": "07:00", "repeat": 10, "tiers": TIERS})
    s = reminders.load(db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    bed = datetime(2026, 9, 15, 20, 0)
    assert e._sleep_interval(s, bed, datetime(2026, 9, 15, 20, 30)) == 10   # 20:30, before the 21:00 tier


def _show_bed(e, hour):
    """Drive the engine to the bedtime overlay at a given hour of the night."""
    start = datetime(2026, 9, 15, 23, 0)
    e.tick(start, 0, False)                              # first appearance at bedtime
    return start


def test_the_overlay_offers_dismiss_and_disable(tmp_path):
    _db, ui, e = _engine(tmp_path)
    _show_bed(e, 23)
    overlay = [s for s in ui.shown if s[1] == "sleep"][-1]
    assert overlay[4] == ["dismiss", "disable"]


def test_dismiss_brings_it_back_after_the_current_interval(tmp_path):
    _db, ui, e = _engine(tmp_path)
    t = datetime(2026, 9, 16, 3, 0)                      # 3am: the 1-minute tier
    e.tick(t, 0, False)
    assert [s for s in ui.shown if s[1] == "sleep"]
    e.answer("sleep", "dismiss")
    run(e, t, 0.9)                                       # not back within ~55s
    assert [s[1] for s in ui.shown].count("sleep") == 1
    run(e, t + timedelta(seconds=54), 0.3)              # back just after a minute
    assert [s[1] for s in ui.shown].count("sleep") == 2


def test_off_tonight_stops_it_until_morning(tmp_path):
    _db, ui, e = _engine(tmp_path)
    t = datetime(2026, 9, 16, 1, 0)
    e.tick(t, 0, False)
    e.answer("sleep", "off_tonight")                     # behind the challenge in the UI
    run(e, t, 120)                                       # two hours: silence
    assert [s[1] for s in ui.shown].count("sleep") == 1
    assert reminders.counts(e.db, "sleep", t - timedelta(days=1)).get("off tonight") == 1


def test_a_custom_snooze_delays_it_by_that_much(tmp_path):
    _db, ui, e = _engine(tmp_path)
    t = datetime(2026, 9, 16, 1, 0)
    e.tick(t, 0, False)
    e.answer("sleep", "snooze:15")
    run(e, t, 14)
    assert [s[1] for s in ui.shown].count("sleep") == 1
    run(e, t + timedelta(minutes=15), 0.2)
    assert [s[1] for s in ui.shown].count("sleep") == 2


def test_dismiss_is_written_down_as_dismissed(tmp_path):
    _db, ui, e = _engine(tmp_path)
    t = datetime(2026, 9, 16, 1, 0)
    e.tick(t, 0, False)
    e.answer("sleep", "dismiss")
    assert reminders.counts(e.db, "sleep", t - timedelta(days=1)).get("dismissed") == 1
