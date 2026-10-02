"""0.84.7: the emergency unlock works whatever the time, and it can pause the bedtime alerts.

Adam had 15 minutes allowed inside a blocked stretch and could not use his emergency until that allowance was used
up: the picker only offered what was blocked right then. Now it offers everything it can free - blocked or not -
and the unlock holds until it ends even when the allowance or limit runs out meanwhile (the time still counts).
And one emergency use can pause the bedtime (and break) alerts, from the Blocking page or from the bedtime screen
itself, without touching the bedtime settings. Permanent blocks stay out of reach, and the use count still rules.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

import emergency
import reminders
from db import Database
from tests.test_app_limit_enforcement import BOOT, FRIDAY, add_game, allow, at, world  # noqa: F401 (fixture)
from tests.test_reminders import run, setup

TIERS = [{"from": "21:00", "every": 15}, {"from": "00:00", "every": 5}]
BED = datetime(2026, 9, 15, 23, 0)            # a Tuesday


def names(part):
    return [i["display_name"] for i in part]


# ---------------------------------------------------------------- items that aren't blocked yet

def test_emergency_on_an_item_with_allowance_left_holds_past_the_allowance(world):
    w = world(at(FRIDAY, "21:00"))
    item = add_game(w, [allow("16:00", "18:00", allowance_min=15)])     # outside its hours, 15 min allowed
    w.step()
    blocked, open_now = emergency.choices(w.gui, w.now)
    assert names(blocked) == [] and names(open_now) == ["Hollow Game"]  # offered although not blocked yet
    until = emergency.unlock(w.gui, open_now, w.now)
    assert emergency.uses_left(w.gui, w.now)[0] == 2                    # one use spent
    game = w.launch("hollowgame.exe", BOOT)
    w.play(game)
    w.run(19)                                                           # the 15 min allowance ran out at 15
    assert w.alive(game) and w.reason(item) is None                     # ...but the emergency holds
    allowance = w.gui.usage_lookup(w.now).data
    assert max(v for (owner, bucket), v in allowance.items() if bucket.startswith("win:")) >= 18 * 60   # counted
    w.run((until - w.now).total_seconds() / 60 + 15 / 60, every=2)     # the emergency ends: blocked, closed
    assert w.reason(item) == "schedule"
    assert not w.alive(game)


def test_emergency_before_a_limit_runs_out_holds_until_it_ends(world):
    w = world(at(FRIDAY, "14:00"))
    item = add_game(w, [{"rule_type": "time_limit", "daily_limit_min": 30}])
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.play(game)
    w.run(25)
    assert w.reason(item) is None
    emergency.unlock(w.gui, [{"id": item, "display_name": "Hollow Game"}], w.now)
    w.run(19)                                                           # 44 min played, limit 30
    assert w.alive(game) and w.reason(item) is None
    assert w.used(item) >= 43 * 60                                      # counting never paused
    w.run(1.5, every=2)
    assert w.reason(item) == "limit" and not w.alive(game)


def test_a_blocked_item_is_still_offered_and_listed_apart(tmp_path):
    db = Database(tmp_path / "t.db")
    now = datetime(2026, 9, 14, 12, 0)
    db.add_site("Reddit", ["reddit.com"], rules=[{"rule_type": "temporary", "temp_until": "2026-09-14 18:00:00"}])
    db.add_site("YouTube", ["youtube.com"], rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    blocked, open_now = emergency.choices(db, now)
    assert names(blocked) == ["Reddit"] and names(open_now) == ["YouTube"]


def test_permanent_blocks_are_never_offered_or_unlocked(tmp_path):
    db = Database(tmp_path / "t.db")
    now = datetime(2026, 9, 14, 12, 0)
    porn = db.add_site("Adult", ["adult.example"])                       # permanent (the default)
    db.set_category("site", "reddit.com", "distracting")
    db.add_item("Distracting", ["distracting"], "category", rules=[{"rule_type": "permanent"}])
    reddit = db.add_site("Reddit", ["reddit.com"], rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    blocked, open_now = emergency.choices(db, now)
    assert names(blocked + open_now) == []                              # reddit: in a permanent category
    for iid, name in ((porn, "Adult"), (reddit, "Reddit")):
        with pytest.raises(ValueError, match="Permanently"):
            emergency.unlock(db, [{"id": iid, "display_name": name}], now)
    assert emergency.uses_left(db, now)[0] == 3                         # nothing spent


def test_unlocking_a_site_frees_it_from_a_blocked_category_but_not_a_permanent_one(tmp_path):
    db = Database(tmp_path / "t.db")
    now = datetime(2026, 9, 14, 12, 0)
    db.set_category("site", "reddit.com", "distracting")
    cat = db.add_item("Distracting", ["distracting"], "category",
                      rules=[{"rule_type": "temporary", "temp_until": "2026-09-14 18:00:00"}])
    reddit = db.add_site("Reddit", ["reddit.com"], rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    assert db.blocked_hostnames(now) == ["reddit.com"]                  # through the category
    blocked, _ = emergency.choices(db, now)
    assert "Reddit" in names(blocked)
    emergency.unlock(db, [i for i in blocked if i["id"] == reddit], now)
    assert db.blocked_hostnames(now + timedelta(minutes=10)) == []
    assert db.blocked_hostnames(now + timedelta(minutes=20)) == ["reddit.com"]
    db.update_item(cat, "Distracting", ["distracting"], None, [{"rule_type": "permanent"}])
    db.add_unlock([reddit], ["Reddit"], now, now + timedelta(hours=1))  # (even a stored one can't lift it)
    assert db.blocked_hostnames(now + timedelta(minutes=30)) == ["reddit.com"]


def test_refused_when_off_or_no_uses_left_and_counts_reset_per_period(tmp_path):
    db = Database(tmp_path / "t.db")
    mon = datetime(2026, 9, 14, 12, 0)
    yt = db.add_site("YouTube", ["youtube.com"], rules=[{"rule_type": "time_limit", "daily_limit_min": 60}])
    item = [{"id": yt, "display_name": "YouTube"}]
    db.set_setting("emergency.uses", "2")
    with pytest.raises(ValueError, match="Tick"):
        emergency.unlock(db, [], mon)
    emergency.unlock(db, item, mon, alerts=True)                        # an item and the alerts: one use
    assert emergency.uses_left(db, mon)[0] == 1
    emergency.unlock(db, [], mon + timedelta(hours=1), alerts=True)     # the alerts alone: a use too
    assert emergency.available(db, mon + timedelta(hours=1)) == 0
    for chosen, alerts in ((item, False), ([], True)):
        with pytest.raises(ValueError, match="No emergency"):
            emergency.unlock(db, chosen, mon + timedelta(hours=2), alerts=alerts)
    assert emergency.uses_left(db, mon + timedelta(days=7))[0] == 2     # a new week
    db.set_setting("emergency.enabled", "0")
    assert emergency.available(db, mon + timedelta(days=7)) == 0
    with pytest.raises(ValueError, match="turned off"):
        emergency.unlock(db, item, mon + timedelta(days=7))


def test_an_old_database_gets_the_alerts_column(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE emergency_unlocks (id INTEGER PRIMARY KEY, started DATETIME, until DATETIME, "
                "item_ids TEXT NOT NULL, names TEXT NOT NULL)")
    con.execute("INSERT INTO emergency_unlocks (started, until, item_ids, names) VALUES "
                "('2026-09-14 12:00:00', '2026-09-14 12:20:00', '[1]', '[\"YouTube\"]')")
    con.execute("PRAGMA user_version=3")
    con.commit()
    con.close()
    db = Database(path)
    [old] = db.unlocks_since(datetime(2026, 9, 14))
    assert old["item_ids"] == [1] and old["alerts"] is False
    assert emergency.alerts_paused_until(db, datetime(2026, 9, 14, 12, 10)) is None


# ---------------------------------------------------------------- pausing the bedtime alerts

def _bedtime(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00", "tiers": TIERS, "guarded": True})
    return db, ui, e


def overlays(ui):
    return [s for s in ui.shown if s[1] == "sleep"]


def test_the_bedtime_screen_offers_the_emergency_only_with_uses_left(tmp_path):
    db, ui, e = _bedtime(tmp_path)
    e.tick(BED, 0, False)
    assert overlays(ui)[-1][4] == ["dismiss", "disable", "emergency"]
    db.set_setting("emergency.uses", "0")
    e.answer("sleep", "dismiss")
    run(e, BED, 16)
    assert overlays(ui)[-1][4] == ["dismiss", "disable"]                # none left: no button
    db.set_setting("emergency.uses", "3")
    db.set_setting("emergency.enabled", "0")
    e.answer("sleep", "dismiss")
    run(e, BED + timedelta(minutes=16), 16)
    assert len(overlays(ui)) == 3 and overlays(ui)[-1][4] == ["dismiss", "disable"]   # turned off: none either


def test_emergency_on_the_bedtime_screen_pauses_the_alerts_then_they_resume(tmp_path):
    db, ui, e = _bedtime(tmp_path)
    stored = db.get_setting(reminders.SLEEP_KEY, "")
    e.tick(BED, 0, False)
    e.answer("sleep", "emergency")                                      # (the UI asks to confirm first)
    assert emergency.uses_left(db, BED)[0] == 2
    assert any("paused until 23:20" in t for t in ui.toasts)
    t = run(e, BED, 19.5)                                               # long past the 15-min tier: silence
    assert len(overlays(ui)) == 1
    run(e, t, 1)                                                        # the emergency is over: back
    assert len(overlays(ui)) == 2
    assert db.get_setting(reminders.SLEEP_KEY, "") == stored            # the settings were never touched


def test_pausing_from_the_blocking_page_closes_the_bedtime_screen(tmp_path):
    db, ui, e = _bedtime(tmp_path)
    e.tick(BED + timedelta(hours=1), 0, False)                          # 00:00, the screen is up
    assert "sleep" in e.open
    emergency.unlock(db, [], BED + timedelta(hours=1, minutes=1), alerts=True)
    t = run(e, BED + timedelta(hours=1, minutes=1), 19.5)
    assert ui.closed.count("sleep") == 1 and "sleep" not in e.open      # taken down at once, kept down
    assert len(overlays(ui)) == 1
    run(e, t, 1)
    assert len(overlays(ui)) == 2


def test_the_bedtime_heads_up_waits_while_the_alerts_are_paused(tmp_path):
    db, ui, e = _bedtime(tmp_path)
    emergency.unlock(db, [], BED - timedelta(minutes=30), alerts=True)  # paused 22:30-22:50
    t = run(e, BED - timedelta(minutes=30), 19.5)
    assert not [s for s in ui.shown if s[1] == "sleep-warn"]
    run(e, t, 1)
    assert [s for s in ui.shown if s[1] == "sleep-warn"]


def test_emergency_on_the_bedtime_screen_with_no_uses_left_just_dismisses(tmp_path):
    db, ui, e = _bedtime(tmp_path)
    e.tick(BED, 0, False)
    db.set_setting("emergency.uses", "0")                               # spent elsewhere meanwhile
    e.answer("sleep", "emergency")
    assert "No emergency unlocks left." in ui.toasts
    assert emergency.alerts_paused_until(db, BED) is None
    run(e, BED, 16)
    assert len(overlays(ui)) == 2                                       # back on the escalation as a dismiss


def test_paused_alerts_hold_the_break_prompt_and_end_a_strict_break(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "every": 30, "strict": True,
                                             "max_snooze": 0})
    start = datetime(2026, 9, 14, 9, 0)
    t = run(e, start, 31)                                               # out of snoozes: the strict break starts
    assert ("break_start",) == ui.shown[-1][:1]
    emergency.unlock(db, [], t, alerts=True)
    e.tick(t, 0, False)
    assert ui.shown[-1] == ("break_end",) and e.break_until is None      # your windows are yours again
    t = run(e, t, 19.5)
    assert not [s for s in ui.shown if s[1:2] == ("break",)] and ui.shown[-1] == ("break_end",)
    run(e, t, 1)
    assert ui.shown[-1][0] == "break_start"                             # due again once it's over


def test_leaving_the_bedtime_emergency_question_open_is_no_free_pause(tmp_path, monkeypatch):
    """Review fix: the bedtime screen's Emergency button asks to confirm. Leaving that question open used to keep
    the bedtime screen away for good without spending a use (the engine still thought it was on screen). Now the
    click counts as a dismiss: the screen comes back on its escalation, and "Pause" later still works (and
    closes it)."""
    from types import SimpleNamespace

    from gui import reminders_ui
    db, ui, e = _bedtime(tmp_path)
    asked = []
    monkeypatch.setattr(reminders_ui, "ConfirmDialog", lambda *a, **kw: asked.append(kw))
    screen = reminders_ui.ReminderUI(SimpleNamespace(db=db, after=lambda ms, fn: None))
    screen.engine = e
    e.tick(BED, 0, False)
    screen._answer("sleep", "emergency")                                # the question is up, left unanswered
    assert len(asked) == 1 and emergency.uses_left(db, BED)[0] == 3
    t = run(e, BED, 16)
    assert len(overlays(ui)) == 2                                       # back on the 15-min escalation
    asked[0]["on_yes"]()                                                # answered at last: one use, paused
    assert emergency.uses_left(db, t)[0] == 2 and "sleep" in ui.closed and "sleep" not in e.open
    run(e, t, 19)
    assert len(overlays(ui)) == 2
    db.set_setting("emergency.uses", "1")                               # none left now: no question, a dismiss
    e.answer("sleep", "dismiss")
    screen._answer("sleep", "emergency")
    assert len(asked) == 1 and "No emergency unlocks left." in ui.toasts
