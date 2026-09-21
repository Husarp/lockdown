"""Text saved before 0.70.1, when an AltGr letter arrived as the wrong one, is put back once."""
import json

import mojibake

PL = "cp1250"
MANGLED = "Jest {time}, pamiêtaj o zdrowiu, zawsze ¿a³ujesz ¿ê siê nie wyspa³eœ"
MEANT = "Jest {time}, pamiętaj o zdrowiu, zawsze żałujesz żę się nie wyspałeś"


def test_the_sentence_from_the_bedtime_screen():
    assert mojibake.repair(MANGLED, PL) == MEANT


def test_ordinary_text_is_left_alone():
    for text in ("Remember to hydrate yourself", "YouTube", "Good Night", "", "Jest 21:02, pamiętaj"):
        assert mojibake.repair(text, PL) == text


def test_a_western_windows_is_never_touched():
    assert mojibake.repair(MANGLED, "cp1252") == MANGLED


def test_inside_the_json_a_setting_is_stored_as():
    stored = json.dumps({"on": True, "text": MANGLED, "times": ["12:00"], "before": 15})
    fixed = json.loads(mojibake.repair_json(stored, PL))
    assert fixed["text"] == MEANT and fixed["on"] is True and fixed["times"] == ["12:00"]


def test_the_pass_runs_once(tmp_path):
    from db import Database
    db = Database(tmp_path / "t.db")
    db.set_setting(mojibake.REPAIRED_KEY, "")
    db.set_setting("reminders.sleep", json.dumps({"text": MANGLED}))
    item = db.add_item("Wideo ³adne", ["x.example"], "site", rules=[{"rule_type": "permanent"}])

    changed = mojibake.repair_saved(db, PL)
    assert "reminders.sleep" in changed
    assert json.loads(db.get_setting("reminders.sleep"))["text"] == MEANT
    assert db.list_items()[0]["display_name"] == "Wideo ładne"

    db.set_setting("reminders.sleep", json.dumps({"text": MANGLED}))   # you typed it that way on purpose
    assert mojibake.repair_saved(db, PL) == []
    assert json.loads(db.get_setting("reminders.sleep"))["text"] == MANGLED
