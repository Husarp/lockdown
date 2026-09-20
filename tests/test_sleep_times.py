""""Heads-up" and "Repeat every" on the sleep reminder take any time you type, down to seconds."""
from datetime import datetime

import pytest

import reminders
from tests.test_reminders import run, setup

NOW = datetime(2026, 9, 14, 9, 0)   # Monday


@pytest.mark.parametrize("text, minutes", [
    ("30", 30), ("30 min", 30), ("5m", 5), ("45s", 0.75), ("90s", 1.5), ("1h", 60), ("1h30", 90),
    ("2h15", 135), ("0,5", 0.5), ("Off", 0), ("", 0),
])
def test_what_you_can_type(text, minutes):
    assert reminders.parse_minutes(text) == minutes


@pytest.mark.parametrize("text", ["abc", "3s", "30h", "-5", "1h70m?"])
def test_what_it_refuses(text):
    with pytest.raises(ValueError):
        reminders.parse_minutes(text)


def test_off_is_not_a_repeat():
    with pytest.raises(ValueError):
        reminders.parse_minutes("off", allow_off=False)


@pytest.mark.parametrize("minutes, text", [(0, "Off"), (0.75, "45s"), (1.5, "90s"), (5, "5 min"), (60, "1h"),
                                           (90, "1h30")])
def test_how_it_is_shown_again(minutes, text):
    assert reminders.minutes_text(minutes) == text


def _nudges_after_answering(tmp_path, repeat):
    """How many times the "Time for bed" overlay is back within 45 seconds of you answering it."""
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00", "repeat": repeat})
    t = run(e, NOW.replace(hour=23), 1)
    assert ui.shown[-1][:2] == ("overlay", "sleep")
    e.answer("sleep", "bed")
    run(e, t, 0.75)
    return [s[1] for s in ui.shown].count("sleep") - 1


def test_the_overlay_comes_back_after_the_seconds_you_typed(tmp_path):
    assert _nudges_after_answering(tmp_path, 0.5) == 1       # "30s": back three quarters of a minute later
    assert _nudges_after_answering(tmp_path, 5) == 0         # "5 min": not yet, as before


def test_a_heads_up_in_seconds_still_comes_before_bedtime(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00", "before": 0.5})
    run(e, NOW.replace(hour=22, minute=57), 2)
    assert not [s for s in ui.shown if s[1] == "sleep-warn"]      # 3 minutes out: nothing yet
    run(e, NOW.replace(hour=22, minute=59, second=45), 0.2)
    assert ui.shown[-1][1] == "sleep-warn"
