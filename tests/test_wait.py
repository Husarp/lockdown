"""A cooling-off period: passing the challenge doesn't unlock at once, it starts a wait. An easy phrase plus
ten minutes of waiting stops an impulse better than a hard phrase does."""
import json
from datetime import datetime, timedelta

import antibypass
from db import Database

NOW = datetime(2026, 9, 21, 12, 0)


def _db(tmp_path, wait_min=10, **cfg) -> Database:
    db = Database(tmp_path / "t.db")
    antibypass.save(db, {**antibypass.DEFAULTS, "phrase": True, "wait_min": wait_min, **cfg})
    return db


def test_with_no_wait_it_unlocks_at_once(tmp_path):
    db = _db(tmp_path, wait_min=0)
    assert antibypass.unlock(db, NOW) is None                  # nothing to wait for
    cfg = antibypass.settings(db)
    assert antibypass.status(cfg, NOW) == "free"
    assert antibypass.unlocked_until(cfg, NOW) == NOW + timedelta(minutes=antibypass.UNLOCK_MIN)


def test_the_wait_runs_before_the_unlock(tmp_path):
    db = _db(tmp_path, wait_min=10)
    assert antibypass.unlock(db, NOW) == NOW + timedelta(minutes=10)
    cfg = antibypass.settings(db)

    assert antibypass.status(cfg, NOW) == "waiting"            # passed, but nothing is allowed yet
    assert antibypass.unlocked_until(cfg, NOW) is None
    assert antibypass.waiting_until(cfg, NOW) == NOW + timedelta(minutes=10)

    ten_past = NOW + timedelta(minutes=10)
    assert antibypass.status(antibypass.settings(db), ten_past) == "free"
    assert antibypass.waiting_until(cfg, ten_past) is None

    later = NOW + timedelta(minutes=10 + antibypass.UNLOCK_MIN)
    assert antibypass.status(cfg, later) == "phrase"           # the unlock ran out: the phrase again


def test_a_wait_under_a_minute(tmp_path):
    db = _db(tmp_path, wait_min=0.5)
    assert antibypass.unlock(db, NOW) == NOW + timedelta(seconds=30)
    assert antibypass.status(antibypass.settings(db), NOW + timedelta(seconds=29)) == "waiting"
    assert antibypass.status(antibypass.settings(db), NOW + timedelta(seconds=31)) == "free"


def test_the_wait_survives_a_restart(tmp_path):
    """It is a timestamp in the database, not a timer in the app: quitting Lockdown doesn't skip it."""
    db = _db(tmp_path, wait_min=10)
    antibypass.unlock(db, NOW)
    db.close()
    again = Database(tmp_path / "t.db")
    assert antibypass.status(antibypass.settings(again), NOW + timedelta(minutes=5)) == "waiting"


def test_locking_clears_the_wait_too(tmp_path):
    db = _db(tmp_path, wait_min=10)
    antibypass.unlock(db, NOW)
    antibypass.lock(db)
    cfg = antibypass.settings(db)
    assert antibypass.status(cfg, NOW) == "phrase"
    assert antibypass.waiting_until(cfg, NOW) is None


def test_closed_hours_still_win(tmp_path):
    """Outside the allowed hours nothing is possible, wait or no wait."""
    cfg = {**antibypass.DEFAULTS, "hours": True, "wait_min": 10,
           "windows": [{"days": [6], "start": "18:00", "end": "20:00"}],
           "unlocked_from": NOW.strftime(antibypass.TIME_FMT),
           "unlocked_until": (NOW + timedelta(minutes=5)).strftime(antibypass.TIME_FMT)}
    assert antibypass.status(cfg, NOW) == "closed"             # Monday noon


def test_shortening_the_wait_needs_the_challenge():
    old = {**antibypass.DEFAULTS, "wait_min": 10}
    assert antibypass.settings_looser(old, {**old, "wait_min": 2})
    assert antibypass.settings_looser(old, {**old, "wait_min": 0})
    assert not antibypass.settings_looser(old, {**old, "wait_min": 30})


def test_settings_from_before_the_wait_existed():
    cfg = json.loads(json.dumps({"phrase": True, "length": 60, "hours": False, "windows": [],
                                 "unlocked_until": (NOW + timedelta(minutes=2)).strftime(antibypass.TIME_FMT)}))
    cfg = {**antibypass.DEFAULTS, **cfg}
    assert antibypass.status(cfg, NOW) == "free"               # no wait recorded: unlocked as before
