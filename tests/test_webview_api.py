"""The new UI's Python bridge (src/webview_app.py). The window itself needs Edge WebView2 and can't run in
tests, but the Api - what every screen reads and writes - is plain Python and is tested here.

Port status: get_state() (read) is live; the write methods refuse until their screen is ported, so a click in
the not-yet-wired UI can't silently lose data."""
from datetime import datetime

import antibypass
from db import Database
import webview_app
from trusted_time import now_from_db


def _api(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCKDOWN_DATA_DIR", str(tmp_path))
    api = webview_app.Api.__new__(webview_app.Api)
    api.window = None
    api.db = Database(tmp_path / "config.db")
    return api


def test_get_state_has_the_keys_the_ui_whitelists(tmp_path, monkeypatch):
    s = _api(tmp_path, monkeypatch).get_state()
    for key in ("on", "service", "rules", "rem", "emergencyLeft", "theme", "accent", "size"):
        assert key in s, key
    assert s["on"] is True and s["service"] == "down"        # fresh: on, and no service heartbeat
    assert s["rules"] == [] and s["rem"] == []
    assert s["theme"] == "dark" and s["accent"] == "Orange" and s["size"] == "Auto"


def test_a_real_rule_shows_up_mapped(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.db.add_item("osu!(lazer)", ["osu!.exe"], "app", rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    rule = api.get_state()["rules"][0]
    assert rule["n"] == "osu!(lazer)" and rule["kind"] == "app" and rule["enabled"] is True
    assert rule["group"] is None and rule["when"]            # a human summary of the rule
    # the full shape the UI iterates - a missing/!list `windows` crashed the page ("r.windows is not iterable")
    for key in ("windows", "limitType", "limit", "used", "allowOn", "allowMin", "allowActive", "allowLeft"):
        assert key in rule, key
    assert isinstance(rule["windows"], list)
    assert rule["limitType"] == "daily" and rule["limit"] == 60


def test_every_rule_has_an_iterable_windows(tmp_path, monkeypatch):
    """The exact crash from the first run: the UI does `for w of r.windows`, so it must always be a list."""
    api = _api(tmp_path, monkeypatch)
    api.db.add_site("YouTube", ["youtube.com"], rules=[{"rule_type": "scheduled",
                    "schedule": '{"mode": "block", "windows": [{"days": [0,1,2], "start": "21:00", "end": "07:00"}]}'}])
    api.db.add_item("osu!", ["osu!.exe"], "app", rules=[{"rule_type": "permanent"}])
    for rule in api.get_state()["rules"]:
        assert isinstance(rule["windows"], list)
        for w in rule["windows"]:
            assert isinstance(w["days"], list) and len(w["days"]) == 7 and "from" in w and "to" in w


def test_switching_off_is_reflected(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert api.get_state()["on"] is True
    antibypass.switch_off(api.db, now_from_db(api.db))
    assert api.get_state()["on"] is False


def test_a_reminder_is_mapped(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    import reminders
    reminders.save(api.db, reminders.CUSTOM_KEY,
                   [{**reminders.DEFAULT_CUSTOM, "id": "a", "text": "Drink water", "kind": "interval", "every": 45}])
    rem = api.get_state()["rem"][0]
    assert rem["n"] == "Drink water" and rem["mode"] == "interval" and rem["interval"] == 45


def test_editing_round_trips(tmp_path, monkeypatch):
    """Create -> read back -> edit -> disable -> delete, all through the bridge, all persisted."""
    api = _api(tmp_path, monkeypatch)
    draft = {"id": "new", "n": "YouTube", "kind": "site", "target": "youtube.com", "when": "block",
             "windows": [{"days": [True] * 5 + [False, False], "from": "21:00", "to": "07:00"}],
             "limitType": "daily", "limit": 30, "allowOn": True, "allowMin": 15, "enabled": True}
    assert api.save_rule(draft)["ok"]
    r = api.get_state()["rules"][0]
    assert len(r["windows"]) == 1 and r["limit"] == 30 and r["limitType"] == "daily" and r["allowOn"] and r["when"] == "block"
    assert api.save_rule({**r, "limitType": "weekly", "limit": 120, "when": "close"})["ok"]
    r = api.get_state()["rules"][0]
    assert r["limit"] == 120 and r["limitType"] == "weekly" and r["when"] == "close"
    assert api.save_rule({**r, "enabled": False})["ok"]
    assert api.get_state()["rules"][0]["enabled"] is False
    assert api.delete_rule(r["id"])["ok"]
    assert api.get_state()["rules"] == []


def test_a_reminder_saves(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert api.save_reminder({"id": None, "n": "Drink water", "msg": "Drink water", "on": True,
                              "mode": "interval", "interval": 45, "times": ["12:00"]})["ok"]
    assert api.get_state()["rem"][0]["n"] == "Drink water"


def test_turning_off_needs_the_challenge_when_one_is_set(tmp_path, monkeypatch):
    """The untrusted-UI rule: with a challenge configured, loosening (turn off, delete) is refused server-side."""
    api = _api(tmp_path, monkeypatch)
    api.db.add_item("osu!", ["osu!.exe"], "app", rules=[{"rule_type": "permanent"}])
    antibypass.save(api.db, {**antibypass.settings(api.db), "phrase": True, "hours": False})
    assert api._may_loosen() is False
    assert api.set_enabled(False)["ok"] is False           # off refused
    assert api.delete_rule("1")["ok"] is False             # delete refused
    assert api.get_state()["on"] is True                   # still on - the UI could not force it off
    assert api.set_enabled(True)["ok"] is True             # turning ON is never gated


def test_adding_a_block_is_allowed_even_with_a_challenge(tmp_path, monkeypatch):
    """Making protection stronger is never gated - only loosening is."""
    api = _api(tmp_path, monkeypatch)
    antibypass.save(api.db, {**antibypass.settings(api.db), "phrase": True})
    assert api.save_rule({"id": "new", "n": "Reddit", "kind": "site", "target": "reddit.com",
                          "when": "block", "windows": [], "limitType": "off"})["ok"] is True
