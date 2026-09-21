"""Every reminder says what you tell it to; left empty it says what it always said."""
from datetime import datetime

import reminders
from tests.test_reminders import run, setup

NOW = datetime(2026, 9, 14, 9, 0)   # Monday


def test_the_standard_wording_is_used_when_you_write_nothing():
    assert reminders.message("", reminders.BREAK_TEXT, every=45, length=5) == \
        "You've been at the PC for 45 min. Take 5 min away from the screen."
    assert reminders.message("   ", reminders.SLEEP_TEXT, time="23:00") == \
        "It's 23:00. Sleep well - the screen can wait until tomorrow."


def test_your_own_wording_with_the_numbers_filled_in():
    assert reminders.message("Wstawaj, minelo {every} min!", reminders.BREAK_TEXT, every=45, length=5) == \
        "Wstawaj, minelo 45 min!"


def test_a_placeholder_that_does_not_exist_is_left_alone():
    """Better a reminder with a stray {word} in it than no reminder at all."""
    assert reminders.message("Time is {whatever}", reminders.BREAK_TEXT, every=45, length=5) == \
        "Time is {whatever}"


def _texts(ui) -> list[str]:
    """Everything the fake UI was asked to show, overlays included (its own record drops their text)."""
    return [s[3] for s in ui.shown if s[0] == "popup"] + ui.said


def _engine(tmp_path):
    db, ui, e = setup(tmp_path)
    ui.said = []
    overlay = ui.overlay
    ui.overlay = lambda key, title, text, until, buttons: (ui.said.append(text),
                                                           overlay(key, title, text, until, buttons))[1]
    return db, ui, e


def test_the_bedtime_reminders_say_your_words(tmp_path):
    db, ui, e = _engine(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00", "text": "Do lozka! Jest {time}.",
                                             "warn_text": "Za chwile {bedtime}."})
    run(e, NOW.replace(hour=22, minute=29), 2)
    assert "Za chwile 23:00." in _texts(ui)
    run(e, NOW.replace(hour=23), 1)
    assert "Do lozka! Jest 23:00." in _texts(ui)


def test_the_break_popup_says_your_words(tmp_path):
    db, ui, e = _engine(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": True, "every": 1, "length": 5,
                                             "text": "Przerwa po {every} min."})
    run(e, NOW, 2)
    assert "Przerwa po 1 min." in _texts(ui)


def test_the_twenty_twenty_twenty_toast_too(tmp_path):
    db, ui, e = _engine(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": True, "every": 90,
                                             "twenty": True, "twenty_text": "Popatrz w dal."})
    run(e, NOW, 21)
    assert ui.toasts == ["Popatrz w dal."]
