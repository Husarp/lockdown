"""Emergency kinds: several at once, each with its own uses per day / week / month and its own length (e.g. 3 a week
for 5 min and 1 a month for 60 min). You pick which kind when you use one (on the Blocking page and the bedtime
screen); each kind counts its own uses, by its id.

The single setting older versions had is one kind ("t1") that behaves exactly as before - the old tests in
test_periods / test_emergency_anytime / test_retention use those keys and run unchanged. Anti-Bypass: a new kind,
more uses, longer, another period (except per day -> per week / month) or turning it on needs the challenge;
fewer, shorter or removing a kind is free.
"""
import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import antibypass
import backup
import emergency
import reminders
from db import SCHEMA_VERSION, Database
from rules import LimitClock, apply_reset_now, change_reset
from tests.test_reminders import FakeUI, run


def at(day, hh, mm=0):   # 2026-09-14 is a Monday
    return datetime(2026, 9, 14) + timedelta(days=day, hours=hh, minutes=mm)


WEEK5 = {"id": "wk", "uses": 3, "per": "week", "minutes": 5}
MONTH60 = {"id": "mo", "uses": 1, "per": "month", "minutes": 60}
T1 = {"id": "t1", "uses": 3, "per": "week", "minutes": 20}


def two_kinds(tmp_path):
    db = Database(tmp_path / "t.db")
    emergency.save_tiers(db, [WEEK5, MONTH60])
    yt = db.add_site("YouTube", ["youtube.com"], rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    return db, [{"id": yt, "display_name": "YouTube"}]


def tiers_used(db):
    return [u["tier"] for u in db.unlocks_since(datetime(2026, 1, 1))]


# ---------------------------------------------------------------- the single setting becomes one kind

def test_without_kinds_the_single_setting_is_the_one_kind(tmp_path):
    db = Database(tmp_path / "t.db")
    assert emergency.tiers(db) == [T1]                                      # a fresh install: 3 a week, 20 min
    assert emergency.config(db) == {"enabled": True, "tiers": [T1]}
    db.set_setting("emergency.uses", "2")
    db.set_setting("emergency.per", "day")
    db.set_setting("emergency.minutes", "45")
    assert emergency.tiers(db) == [{"id": "t1", "uses": 2, "per": "day", "minutes": 45}]
    db.set_setting("emergency.uses", "0")                                   # (none: no kind to use)
    assert emergency.tiers(db) == [] and not emergency.usable(db)


def test_old_unlocks_count_for_the_first_kind_until_it_is_removed(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_unlock([], [], at(0, 9), at(0, 9, 20), alerts=True)              # from before kinds: no tier stored
    assert emergency.uses_left(db, at(0, 12), "t1") == (2, 3, at(7, 0))
    emergency.save_tiers(db, emergency.tiers(db))                           # the first save in the editor
    assert json.loads(db.get_setting(emergency.TIERS_KEY)) == [T1]
    db.set_setting("emergency.uses", "9")                                   # the old keys no longer apply
    assert emergency.tiers(db) == [T1]
    assert emergency.uses_left(db, at(0, 12), "t1")[:2] == (2, 3)           # still counted
    emergency.save_tiers(db, [{**T1, "id": "a1b2c3d4"}])                    # removed, the same values added again
    assert emergency.uses_left(db, at(0, 12)) == (3, 3, at(7, 0))           # ... starts afresh


def test_an_older_database_gets_the_tier_column(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE emergency_unlocks (id INTEGER PRIMARY KEY, started DATETIME, until DATETIME, "
                "item_ids TEXT NOT NULL, names TEXT NOT NULL, alerts INTEGER)")
    con.execute("INSERT INTO emergency_unlocks (started, until, item_ids, names, alerts) VALUES "
                "('2026-09-14 09:00:00', '2026-09-14 09:20:00', '[1]', '[\"YouTube\"]', 0)")
    con.execute("PRAGMA user_version=6")
    con.commit()
    con.close()
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    [old] = db.unlocks_since(at(0, 0))
    assert old["tier"] is None and old["item_ids"] == [1] and emergency.tier_of(old) == "t1"
    assert emergency.uses_left(db, at(0, 12)) == (2, 3, at(7, 0))
    emergency.unlock(db, [], at(0, 12), alerts=True)
    assert tiers_used(db) == [None, "t1"]


def test_bad_kinds_are_dropped_or_clamped(tmp_path):
    assert emergency.clean([
        {"id": "a", "uses": 50, "per": "week", "minutes": 500},
        {"id": "a", "uses": 1, "per": "day", "minutes": 5},                 # the same id again
        {"id": "", "uses": 1, "per": "day", "minutes": 5},
        {"id": "b", "uses": 0, "per": "day", "minutes": 5},
        {"id": "c", "uses": 1, "per": "year", "minutes": 5},
        {"id": "d", "uses": 1, "per": ["day"], "minutes": 5},
        {"id": 7, "uses": 1, "per": "day", "minutes": 5},
        {"uses": 1, "per": "day", "minutes": 5}, "junk", None,
        {"id": "e", "uses": "2", "per": "month", "minutes": 0},
    ]) == [{"id": "a", "uses": 10, "per": "week", "minutes": 60}, {"id": "e", "uses": 2, "per": "month", "minutes": 1}]
    assert len(emergency.clean([{**WEEK5, "id": str(i)} for i in range(8)])) == emergency.MAX_TIERS
    db = Database(tmp_path / "t.db")
    for broken in ("{not json", '{"id": "x"}', "[1, 2]"):
        db.set_setting(emergency.TIERS_KEY, broken)
        assert emergency.tiers(db) == [] and emergency.available(db, at(0, 12)) == 0
    a, b = emergency.new_id(), emergency.new_id()
    assert a != b and len(a) == 8 and int(a, 16) >= 0


# ---------------------------------------------------------------- counting per kind and period

def test_each_kind_counts_its_own_uses(tmp_path):
    db, item = two_kinds(tmp_path)
    for h in range(3):
        assert emergency.unlock(db, item, at(2, 12 + h), tier="wk") == at(2, 12 + h, 5)
    assert emergency.uses_left(db, at(2, 16), "wk")[:2] == (0, 3)
    assert emergency.uses_left(db, at(2, 16), "mo")[:2] == (1, 1)
    with pytest.raises(ValueError, match="No 5-min emergency unlocks left this week"):
        emergency.unlock(db, item, at(2, 16), tier="wk")
    assert emergency.unlock(db, item, at(2, 16), tier="mo") == at(2, 17)
    assert tiers_used(db) == ["wk", "wk", "wk", "mo"]
    assert [(t["id"], left) for t, left, _allowed, _reset in emergency.left_by_tier(db, at(2, 18))] == \
        [("wk", 0), ("mo", 0)]
    with pytest.raises(ValueError, match="No emergency unlocks left"):
        emergency.unlock(db, item, at(2, 18))


def test_the_week_kind_resets_on_monday_and_the_month_kind_on_the_first(tmp_path):
    db, item = two_kinds(tmp_path)
    emergency.unlock(db, item, at(2, 12), tier="wk")
    emergency.unlock(db, item, at(2, 13), tier="mo")
    assert emergency.uses_left(db, at(2, 14), "wk") == (2, 3, at(7, 0))
    assert emergency.uses_left(db, at(2, 14), "mo") == (0, 1, datetime(2026, 10, 1))
    assert emergency.uses_left(db, at(7, 0), "wk")[0] == 3                  # Monday 21 Sep
    assert emergency.uses_left(db, at(16, 23, 59), "mo")[0] == 0            # Wednesday 30 Sep, 23:59
    assert emergency.uses_left(db, datetime(2026, 10, 1), "mo")[0] == 1


def test_with_a_3am_reset_monday_2am_is_still_last_week(tmp_path):
    db, item = two_kinds(tmp_path)
    db.set_setting("limits.reset", json.dumps({"time": "03:00"}))
    emergency.unlock(db, item, at(7, 2), tier="wk")                         # Mon 21 Sep 02:00: the week of the 14th
    assert emergency.uses_left(db, at(7, 2, 30), "wk") == (2, 3, at(7, 3))
    assert emergency.uses_left(db, at(7, 3), "wk")[0] == 3


def test_with_an_8pm_reset_the_week_starts_on_sunday_evening(tmp_path):
    db, item = two_kinds(tmp_path)
    db.set_setting("limits.reset", json.dumps({"time": "20:00"}))
    emergency.unlock(db, item, at(6, 19), tier="wk")                        # Sun 20 Sep 19:00
    emergency.unlock(db, item, at(6, 19, 30), tier="mo")
    assert emergency.uses_left(db, at(6, 19, 45), "wk") == (2, 3, at(6, 20))
    assert emergency.uses_left(db, at(6, 20), "wk")[0] == 3
    assert emergency.uses_left(db, datetime(2026, 9, 30, 19, 59), "mo") == (0, 1, datetime(2026, 9, 30, 20))
    assert emergency.uses_left(db, datetime(2026, 9, 30, 20), "mo")[0] == 1   # October from the evening before


def test_a_held_month_does_not_end_early(tmp_path):
    db, item = two_kinds(tmp_path)
    emergency.unlock(db, item, datetime(2026, 9, 30, 10), tier="mo")
    db.set_setting("limits.reset", apply_reset_now(None, "20:00", datetime(2026, 9, 30, 12)))
    assert emergency.uses_left(db, datetime(2026, 9, 30, 21), "mo") == (0, 1, datetime(2026, 10, 1))
    assert emergency.uses_left(db, datetime(2026, 10, 1, 0, 1), "mo")[0] == 1


def test_a_changed_reset_time_holds_only_the_running_week_and_month(tmp_path):
    """The hold keeps the running week and month from ending early - it doesn't pull earlier ones into them. Uses
    inside them still count there, even ones the new time would put in the week or month before."""
    db, item = two_kinds(tmp_path)
    emergency.unlock(db, item, datetime(2026, 9, 20, 12), tier="mo")
    emergency.unlock(db, item, datetime(2026, 9, 30, 12), tier="wk")         # Wednesday of the week before
    now = datetime(2026, 10, 7, 12)                                           # Wednesday 7 Oct
    assert [left for _t, left, _a, _r in emergency.left_by_tier(db, now)] == [3, 1]
    db.set_setting("limits.reset", change_reset(None, "06:00", now))
    assert [left for _t, left, _a, _r in emergency.left_by_tier(db, now)] == [3, 1]
    emergency.unlock(db, item, datetime(2026, 10, 1, 3), tier="mo")          # 03:00: September by the new time
    emergency.unlock(db, item, datetime(2026, 10, 5, 3), tier="wk")          # Monday 03:00: last week by it
    assert [left for _t, left, _a, _r in emergency.left_by_tier(db, now)] == [2, 0]
    # a hold saved by an older version (no start) still takes in every earlier time: the strict side
    old = LimitClock(json.dumps({"time": "06:00", "hold": {"month": ["m2026-10", "2026-11-01 00:00:00"]}}))
    assert old.period("month", datetime(2026, 9, 20, 12))[0] == "m2026-10"


def test_available_is_all_kinds_together_and_0_when_off(tmp_path):
    db, item = two_kinds(tmp_path)
    assert emergency.available(db, at(2, 12)) == 4 and emergency.uses_left(db, at(2, 12)) == (4, 4, at(7, 0))
    emergency.unlock(db, item, at(2, 12), tier="mo")
    assert emergency.available(db, at(2, 13)) == 3 and emergency.usable(db)
    db.set_setting("emergency.enabled", "0")
    assert emergency.available(db, at(2, 13)) == 0 and not emergency.usable(db)
    with pytest.raises(ValueError, match="turned off"):
        emergency.unlock(db, item, at(2, 13), tier="wk")
    db.set_setting("emergency.enabled", "1")
    emergency.save_tiers(db, [])                                            # every kind removed
    assert emergency.available(db, at(2, 13)) == 0 and not emergency.usable(db)
    assert emergency.uses_left(db, at(2, 13)) == (0, 0, None)
    with pytest.raises(ValueError, match="No emergency unlocks left"):
        emergency.unlock(db, item, at(2, 13))


def test_which_kind_must_be_said_and_must_exist(tmp_path):
    db, item = two_kinds(tmp_path)
    with pytest.raises(ValueError, match="Pick how long"):
        emergency.unlock(db, item, at(2, 12))
    with pytest.raises(ValueError, match="kind is gone"):
        emergency.unlock(db, item, at(2, 12), tier="nope")
    emergency.save_tiers(db, [WEEK5])
    with pytest.raises(ValueError, match="kind is gone"):
        emergency.unlock(db, item, at(2, 12), tier="mo")
    assert tiers_used(db) == []
    assert emergency.unlock(db, item, at(2, 12)) == at(2, 12, 5)           # one kind left: no need to say which
    assert tiers_used(db) == ["wk"]


def test_a_use_dated_later_still_counts(tmp_path):
    """A use dated after now (the clock was set back since) counts as spent - it used to be skipped on the PC."""
    db, item = two_kinds(tmp_path)
    db.add_unlock([], [], at(9, 12), at(9, 12, 5), alerts=True, tier="wk")  # next week
    db.add_unlock([], [], at(40, 12), at(40, 13), alerts=True, tier="mo")   # next month
    assert emergency.uses_left(db, at(2, 12), "wk")[0] == 2
    assert emergency.uses_left(db, at(2, 12), "mo")[0] == 0


# ---------------------------------------------------------------- a kind changed or removed mid-period

def test_fewer_uses_mid_week_apply_at_once(tmp_path):
    db, item = two_kinds(tmp_path)
    emergency.unlock(db, item, at(0, 12), tier="wk")
    emergency.unlock(db, item, at(1, 12), tier="wk")
    emergency.save_tiers(db, [{**WEEK5, "uses": 1}, MONTH60])
    assert emergency.uses_left(db, at(2, 12), "wk") == (0, 1, at(7, 0))


def test_per_day_instead_of_per_week_counts_only_today(tmp_path):
    db, item = two_kinds(tmp_path)
    emergency.unlock(db, item, at(0, 12), tier="wk")
    emergency.unlock(db, item, at(2, 9), tier="wk")
    emergency.save_tiers(db, [{**WEEK5, "per": "day"}, MONTH60])
    assert emergency.uses_left(db, at(2, 12), "wk") == (2, 3, at(3, 0))
    assert emergency.uses_left(db, at(3, 0), "wk")[0] == 3
    emergency.save_tiers(db, [{**WEEK5, "per": "month"}, MONTH60])          # per month: every use since the 1st
    assert emergency.uses_left(db, at(3, 0), "wk")[0] == 1


def test_a_new_length_leaves_a_running_unlock_as_it_was(tmp_path):
    db, item = two_kinds(tmp_path)
    assert emergency.unlock(db, item, at(2, 12), tier="wk") == at(2, 12, 5)
    emergency.save_tiers(db, [{**WEEK5, "minutes": 30}, MONTH60])
    assert db.active_unlocks(at(2, 12, 3)) == {item[0]["id"]: at(2, 12, 5)}
    assert db.active_unlocks(at(2, 12, 5)) == {}
    assert emergency.unlock(db, item, at(2, 13), tier="wk") == at(2, 13, 30)


def test_a_removed_kind_s_unlock_runs_to_its_end_and_its_uses_count_for_nothing(tmp_path):
    db, item = two_kinds(tmp_path)
    emergency.unlock(db, item, at(2, 12), alerts=True, tier="mo")
    emergency.save_tiers(db, [WEEK5])
    assert db.active_unlocks(at(2, 12, 30)) == {item[0]["id"]: at(2, 13)}
    assert emergency.alerts_paused_until(db, at(2, 12, 30)) == at(2, 13)
    assert emergency.uses_left(db, at(2, 12, 30)) == (3, 3, at(7, 0))
    assert db.blocked_hostnames(at(2, 12, 30)) == []


# ---------------------------------------------------------------- the bedtime screen

TIERS = [{"from": "21:00", "every": 15}, {"from": "00:00", "every": 5}]
BED = datetime(2026, 9, 15, 23, 0)            # a Tuesday


class LabelUI(FakeUI):
    def overlay(self, key, title, text, until, buttons):
        super().overlay(key, title, text, until, buttons)
        self.labels = [b[0] for b in buttons]


def _bedtime(tmp_path):
    db, ui = Database(tmp_path / "t.db"), LabelUI()
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00", "tiers": TIERS, "guarded": True})
    emergency.save_tiers(db, [WEEK5, MONTH60])
    return db, ui, reminders.Engine(db, ui)


def overlays(ui):
    return [s for s in ui.shown if s[1] == "sleep"]


def spend_the_week(db):
    for h in range(3):   # Monday morning: the 5-min kind is spent (each paused the alerts for 5 min back then)
        emergency.unlock(db, [], at(0, 9 + h), alerts=True, tier="wk")


def test_bedtime_emergency_spends_the_kind_picked(tmp_path):
    db, ui, e = _bedtime(tmp_path)
    e.tick(BED, 0, False)
    assert overlays(ui)[-1][4] == ["dismiss", "disable", "emergency"] and ui.labels[-1] == "Emergency (4 left)"
    e.answer("sleep", "emergency:mo")
    [u] = db.unlocks_since(BED - timedelta(days=1))
    assert (u["tier"], u["alerts"], u["until"], u["item_ids"]) == ("mo", True, BED + timedelta(minutes=60), [])
    assert any("paused until 00:00" in t for t in ui.toasts)
    t = run(e, BED, 59.5)
    assert len(overlays(ui)) == 1
    run(e, t, 1)
    assert len(overlays(ui)) == 2 and ui.labels[-1] == "Emergency (3 left)"


def test_a_plain_emergency_uses_the_shortest_kind_with_uses_left(tmp_path):
    db, ui, e = _bedtime(tmp_path)
    e.tick(BED, 0, False)
    e.answer("sleep", "emergency")
    assert tiers_used(db) == ["wk"] and emergency.alerts_paused_until(db, BED) == BED + timedelta(minutes=5)
    db2, ui2, e2 = _bedtime(tmp_path / "2")
    spend_the_week(db2)
    e2.tick(BED, 0, False)
    assert ui2.labels[-1] == "Emergency (1 left)"
    e2.answer("sleep", "emergency")
    assert tiers_used(db2)[-1] == "mo" and emergency.alerts_paused_until(db2, BED) == BED + timedelta(minutes=60)


def test_bedtime_emergency_with_none_left_toasts_and_dismisses(tmp_path):
    db, ui, e = _bedtime(tmp_path)
    spend_the_week(db)
    emergency.unlock(db, [], at(0, 13), alerts=True, tier="mo")
    e.tick(BED, 0, False)
    assert overlays(ui)[-1][4] == ["dismiss", "disable"]                    # nothing left: no button
    for action in ("emergency", "emergency:wk"):
        e.answer("sleep", action)
        assert ui.toasts[-1].startswith("No ") and emergency.alerts_paused_until(db, BED) is None
    run(e, BED, 16)
    assert len(overlays(ui)) == 2                                           # back on the escalation as a dismiss


def test_the_bedtime_question_offers_each_kind(tmp_path, monkeypatch):
    from gui import reminders_ui
    db, ui, e = _bedtime(tmp_path)
    spend_the_week(db)
    asked = []
    monkeypatch.setattr(reminders_ui, "ChoiceDialog", lambda *a, **kw: asked.append(a))
    screen = reminders_ui.ReminderUI(SimpleNamespace(db=db, after=lambda ms, fn: None))
    screen.engine = e
    e.tick(BED, 0, False)
    screen._answer("sleep", "emergency")
    [(_app, _title, _message, choices)] = asked
    assert [(text, enabled) for text, enabled, _pick in choices] == [
        ("5 min - none left · resets Mon 21 Sep 00:00", False), ("Pause 60 min · 1 left this month", True)]
    assert emergency.available(db, BED) == 1                                # nothing spent by asking
    choices[1][2]()
    assert tiers_used(db)[-1] == "mo" and emergency.alerts_paused_until(db, BED) == BED + timedelta(minutes=60)
    assert "sleep" in ui.closed


# ---------------------------------------------------------------- Anti-Bypass

def cfg(*tiers, on=True):
    return {"enabled": on, "tiers": list(tiers)}


def test_what_needs_the_challenge():
    looser = antibypass.emergency_looser
    both = cfg(WEEK5, MONTH60)
    assert not looser(both, cfg(WEEK5, MONTH60, on=False))                  # switched off
    assert looser(cfg(WEEK5, MONTH60, on=False), both)                      # switched on
    assert not looser(cfg(WEEK5, on=False), cfg({**WEEK5, "uses": 10}, MONTH60, on=False))   # while off: nothing
    assert looser(cfg(WEEK5), both)                                         # a new kind
    assert not looser(both, cfg(WEEK5)) and not looser(both, cfg())         # removing kinds
    assert looser(both, cfg({**WEEK5, "uses": 4}, MONTH60))                 # more uses
    assert not looser(both, cfg({**WEEK5, "uses": 2}, MONTH60))
    assert looser(both, cfg(WEEK5, {**MONTH60, "minutes": 61}))             # longer
    assert not looser(both, cfg(WEEK5, {**MONTH60, "minutes": 30}))
    assert looser(both, cfg({**WEEK5, "per": "day"}, MONTH60))              # week -> day
    assert looser(both, cfg(WEEK5, {**MONTH60, "per": "week"}))             # month -> week
    day = {**WEEK5, "per": "day"}
    assert not looser(cfg(day), cfg({**day, "per": "month"}))               # day -> month (it holds today)
    assert not looser(cfg(day), cfg({**day, "per": "week"}))                # day -> week
    # week -> month: a month doesn't hold the week you are in - on the 1st it would hand a spent week back
    assert looser(both, cfg({**WEEK5, "per": "month"}, MONTH60))
    # the spent 5-min kind taking the 60-min kind's values and the other way round: matched by id, not as a set
    assert looser(both, cfg({**MONTH60, "id": "wk"}, {**WEEK5, "id": "mo"}))
    assert not looser(both, both) and not looser(both, cfg(MONTH60, WEEK5))
    # the one kind from the single setting: the same answers as before kinds
    one = cfg(T1)
    assert not looser(one, cfg({**T1, "uses": 2, "minutes": 10}))
    assert not looser(one, cfg(T1, on=False))
    assert looser(one, cfg({**T1, "uses": 5})) and looser(one, cfg({**T1, "per": "day"}))
    assert looser(cfg(T1, on=False), one)


def test_the_settings_card_saves_through_the_challenge(tmp_path):
    from gui.settings import SettingsPage
    db = Database(tmp_path / "t.db")
    asked = []
    page = SettingsPage.__new__(SettingsPage)
    page.db, page._draft = db, None
    page.app = SimpleNamespace(pages={}, guard=lambda what, proceed, cancel: asked.append((what, proceed)))
    page.load = page._hide_draft = lambda: None
    page._edit_kind("t1", "uses", 2)                                        # fewer: saved at once
    assert emergency.tiers(db) == [{**T1, "uses": 2}] and not asked
    page._edit_kind("t1", "minutes", 30)                                    # longer: the challenge first
    assert emergency.tiers(db) == [{**T1, "uses": 2}] and len(asked) == 1
    asked.pop()[1]()                                                        # passed
    assert emergency.tiers(db) == [{**T1, "uses": 2, "minutes": 30}]
    page._draft = {"uses": 1, "per": "month", "minutes": 60}
    page._add_kind()                                                        # a new kind: one challenge, then saved
    assert len(emergency.tiers(db)) == 1 and len(asked) == 1
    asked.pop()[1]()
    [_t1, added] = emergency.tiers(db)
    assert {k: added[k] for k in ("uses", "per", "minutes")} == {"uses": 1, "per": "month", "minutes": 60}
    assert len(added["id"]) == 8
    page._remove_kind("t1")                                                 # removing: free
    assert emergency.tiers(db) == [added] and not asked
    page._save_emergency(enabled=False)                                     # off: free
    assert emergency.get(db, "emergency.enabled") == "0" and not asked
    page._edit_kind(added["id"], "uses", 5)                                 # while off: free
    assert emergency.tiers(db)[0]["uses"] == 5 and not asked
    page._save_emergency(enabled=True)                                      # on again: the challenge
    assert emergency.get(db, "emergency.enabled") == "0" and len(asked) == 1


# ---------------------------------------------------------------- backup and the web view

def test_backups_carry_the_kinds_and_an_old_one_brings_its_single_setting(tmp_path):
    db = Database(tmp_path / "a.db")
    emergency.save_tiers(db, [WEEK5, MONTH60])
    data = backup.export(db)
    assert json.loads(data["settings"]["emergency.tiers"]) == [WEEK5, MONTH60]
    other = Database(tmp_path / "b.db")
    backup.restore(other, data)
    assert emergency.tiers(other) == [WEEK5, MONTH60]
    old = {**data, "settings": {**{k: v for k, v in data["settings"].items() if k != "emergency.tiers"},
                                "emergency.uses": "1", "emergency.per": "day", "emergency.minutes": "10"}}
    assert "Emergency unlock kinds will change" in backup.diff(db, old)
    backup.restore(db, old)
    assert emergency.tiers(db) == [{"id": "t1", "uses": 1, "per": "day", "minutes": 10}]
    assert "Emergency unlock kinds will change" not in backup.diff(db, old)
    # one made at the defaults has no single setting in it: the defaults, not what this computer had left over
    at_defaults = {**data, "settings": {k: v for k, v in data["settings"].items() if k != "emergency.tiers"}}
    backup.restore(db, at_defaults)
    assert emergency.tiers(db) == [T1]


def test_the_web_view_shows_each_kind_and_unlocks_the_one_asked(tmp_path, monkeypatch):
    from tests.test_webview_api import _api
    api = _api(tmp_path, monkeypatch)
    api.db.add_site("YouTube", ["youtube.com"], rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    emergency.save_tiers(api.db, [WEEK5, MONTH60])
    state = api.get_state()
    assert state["emergencyLeft"] == 4
    assert [{k: t[k] for k in ("id", "uses", "per", "minutes", "left")} for t in state["emergencyTiers"]] == \
        [{**WEEK5, "left": 3}, {**MONTH60, "left": 1}]
    assert all(datetime.strptime(t["reset"], "%Y-%m-%d %H:%M") for t in state["emergencyTiers"])
    assert api.emergency_unlock() == {"ok": False, "error": "Pick how long."}
    assert api.emergency_unlock("mo")["ok"]
    assert api.get_state()["emergencyLeft"] == 3
    assert api.emergency_unlock("mo")["ok"] is False
