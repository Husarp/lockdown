"""Turning Lockdown off entirely: no blocking, no limits, no protection lists, no reminders - as if it were
not installed - until you turn it back on. Switching it off goes through the Anti-Bypass challenge; switching
it back on never does, because coming back to your own rules is not the thing to stand in the way of."""
from datetime import datetime

import antibypass
from db import Database
from service import Enforcer

NOW = datetime(2026, 9, 23, 1, 30)


def _db(tmp_path):
    return Database(tmp_path / "t.db")


def _enforcer(db, off: bool) -> Enforcer:
    e = Enforcer.__new__(Enforcer)
    e.db, e.dns_blocks, e.closing, e.was_off = db, {}, {}, off
    e.protection = type("P", (), {"which": staticmethod(lambda host: None)})()
    return e


def test_it_starts_on(tmp_path):
    db = _db(tmp_path)
    assert not antibypass.is_off(db) and antibypass.off_since(db) == ""


def test_off_stays_off_until_you_turn_it_back_on(tmp_path):
    db = _db(tmp_path)
    antibypass.switch_off(db, NOW)
    assert antibypass.is_off(db)
    assert antibypass.off_since(db) == "2026-09-23 01:30:00"   # shown on the page, so you know since when
    antibypass.switch_on(db)
    assert not antibypass.is_off(db) and antibypass.off_since(db) == ""


def test_nothing_is_blocked_while_it_is_off(tmp_path):
    """Including the protection lists, which are otherwise always on."""
    db = _db(tmp_path)
    on = _enforcer(db, off=False)
    on.dns_blocks = {"youtube.com": {}}
    on.protection = type("P", (), {"which": staticmethod(lambda host: "scam")})()
    assert on.blocked_name("youtube.com")
    assert on.blocked_name("a-scam-site.example")

    off = _enforcer(db, off=True)
    off.dns_blocks = {"youtube.com": {}}
    off.protection = type("P", (), {"which": staticmethod(lambda host: "scam")})()
    assert off.blocked_name("youtube.com") is None
    assert off.blocked_name("a-scam-site.example") is None


def test_turning_it_off_is_not_the_same_as_quitting(tmp_path):
    """The tray Exit flag is its own thing: switching off must not stop Lockdown coming back at login,
    or you could never reach the button that turns it on again."""
    db = _db(tmp_path)
    antibypass.switch_off(db, NOW)
    assert db.get_setting(antibypass.EXITED_KEY, "0") != "1"


def test_the_two_switches_do_not_share_a_setting(tmp_path):
    db = _db(tmp_path)
    db.set_setting(antibypass.EXITED_KEY, "1")
    assert not antibypass.is_off(db)
    antibypass.switch_on(db)
    assert db.get_setting(antibypass.EXITED_KEY, "0") == "1"
