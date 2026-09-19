from datetime import datetime, timedelta

import pytest

import modes
from db import Database
from rules import make_schedule

NOW = datetime(2026, 9, 14, 10, 0)   # a Monday


def test_pomodoro_phases():
    p = {"work": 25, "break": 5, "rounds": 2, "long": 15}
    assert modes.pomodoro_phase(p, NOW, NOW + timedelta(minutes=10)) == ("focus", NOW + timedelta(minutes=25), 1)
    assert modes.pomodoro_phase(p, NOW, NOW + timedelta(minutes=27))[0] == "break"
    assert modes.pomodoro_phase(p, NOW, NOW + timedelta(minutes=31))[:1] == ("focus",)
    assert modes.pomodoro_phase(p, NOW, NOW + timedelta(minutes=56))[0] == "long break"
    assert modes.pomodoro_phase(p, NOW, NOW + timedelta(minutes=71)) is None
    assert modes.pomodoro_length(p) == timedelta(minutes=70)


def test_start_stop_lock_and_schedule(tmp_path):
    db = Database(tmp_path / "t.db")
    assert modes.active(db, NOW) is None
    modes.start(db, "work", NOW, NOW + timedelta(hours=1), locked=True)
    state = modes.active(db, NOW + timedelta(minutes=5))
    assert state["mode"]["id"] == "work" and state["locked"] and modes.blocking(state)
    with pytest.raises(ValueError, match="locked"):
        modes.stop(db, NOW + timedelta(minutes=5))
    assert modes.active(db, NOW + timedelta(hours=2)) is None      # ran out
    modes.stop(db, NOW + timedelta(hours=2))
    ms = modes.load(db)
    ms[1]["schedule"] = make_schedule("block", [([0, 1, 2, 3, 4], "09:00", "17:00")])   # Study, weekdays 9-17
    modes.save(db, ms)
    assert modes.active(db, NOW)["mode"]["id"] == "study"
    assert modes.active(db, NOW.replace(hour=18)) is None
    modes.start(db, "relax", NOW, None)
    state = modes.active(db, NOW)
    assert state["mode"]["id"] == "relax"                               # by hand wins over the schedule
    assert modes.blocking(state) and not modes.targets(state["mode"], [], [], {})


def test_focus_blocks_only_in_focus_rounds(tmp_path):
    db = Database(tmp_path / "t.db")
    modes.start(db, "focus", NOW, None)
    assert modes.blocking(modes.active(db, NOW + timedelta(minutes=10)))
    assert not modes.blocking(modes.active(db, NOW + timedelta(minutes=27)))
    assert modes.active(db, NOW + timedelta(hours=3)) is None           # all rounds done


def test_targets_and_blocks(tmp_path):
    db = Database(tmp_path / "t.db")
    yt = db.add_site("YouTube", ["youtube.com"], rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    gh = db.add_site("GitHub", ["github.com"], rules=[{"rule_type": "time_limit", "daily_limit_min": 600}])
    db.set_category("site", "github.com", "productive")
    db.set_category("app", "steam.exe", "distracting")
    assert db.blocked_hostnames(NOW) == []
    modes.start(db, "work", NOW, None)
    blocked = {b["item"]["target"]: b for b in db.blocks(NOW)}
    assert set(blocked) == {"youtube.com", "steam.exe"}                  # GitHub was marked productive
    assert blocked["youtube.com"]["reason"] == "mode" and blocked["steam.exe"]["item"]["id"] is None
    db.add_unlock([yt], ["YouTube"], NOW, NOW + timedelta(minutes=20))   # emergency unlock still works
    assert "youtube.com" not in db.blocked_hostnames(NOW + timedelta(minutes=5))
    ms = modes.load(db)
    ms[0]["items"] = [gh]
    ms[0]["extra"] = [{"kind": "site", "name": "Reddit", "targets": ["reddit.com"]}]
    modes.save(db, ms)
    assert {"github.com", "reddit.com"} <= set(db.blocked_hostnames(NOW + timedelta(minutes=30)))
