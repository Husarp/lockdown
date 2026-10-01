"""Per-alert "important" flag. Marking a reminder important means you can't TURN IT OFF (or remove the flag,
or delete it) without the Anti-Bypass challenge - so you can't just disable the reminder to escape it.
Dismissing the popup itself stays free; it's the settings that are guarded."""
import reminders
from db import Database


def test_the_flag_round_trips(tmp_path):
    db = Database(tmp_path / "t.db")
    reminders.save(db, reminders.CUSTOM_KEY, [{**reminders.DEFAULT_CUSTOM, "id": "a", "text": "Meds", "guarded": True}])
    assert reminders.custom_list(db)[0]["guarded"] is True
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "guarded": True})
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "guarded": True})
    assert reminders.load(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)["guarded"] is True
    assert reminders.load(db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)["guarded"] is True


def test_default_is_not_important():
    assert reminders.DEFAULT_CUSTOM["guarded"] is False
    assert reminders.DEFAULT_BREAK["guarded"] is False
    assert reminders.DEFAULT_SLEEP["guarded"] is False


ON = {"guarded": True, "on": True}


def test_turning_an_important_alert_off_is_guarded():
    assert reminders.loosens_reminder(ON, {"guarded": True, "on": False}) is True


def test_removing_the_important_flag_is_guarded():
    assert reminders.loosens_reminder(ON, {"guarded": False, "on": True}) is True


def test_ordinary_edits_are_not_guarded():
    # still on, still important, just other fields changed -> no challenge
    assert reminders.loosens_reminder(ON, {"guarded": True, "on": True, "every": 90}) is False


def test_a_casual_alert_is_never_guarded():
    casual = {"guarded": False, "on": True}
    assert reminders.loosens_reminder(casual, {"guarded": False, "on": False}) is False
    assert reminders.loosens_reminder(casual, {"guarded": True, "on": True}) is False   # turning it ON is free
