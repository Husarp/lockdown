from datetime import datetime, timedelta

import reminders
from db import Database

NOW = datetime(2026, 9, 14, 9, 0)   # Monday


class FakeUI:
    def __init__(self):
        self.shown, self.closed, self.toasts, self.modes = [], [], [], []

    def popup(self, key, title, text, buttons):
        self.shown.append(("popup", key, title, text, [b[1] for b in buttons]))

    def overlay(self, key, title, text, until, buttons):
        self.shown.append(("overlay", key, title, until, [b[1] for b in buttons]))

    def close(self, key):
        self.closed.append(key)

    def toast(self, text):
        self.toasts.append(text)

    def start_mode(self, mode_id, until):
        self.modes.append((mode_id, until))


def run(engine, start, minutes, idle=0, fullscreen=False):
    t = start
    for _ in range(int(minutes * 60 / reminders.TICK_SEC)):
        engine.tick(t, idle, fullscreen)
        t += timedelta(seconds=reminders.TICK_SEC)
    return t


def setup(tmp_path):
    db, ui = Database(tmp_path / "t.db"), FakeUI()
    return db, ui, reminders.Engine(db, ui)


def test_break_after_45_minutes_of_use_and_reset_when_away(tmp_path):
    db, ui, e = setup(tmp_path)
    t = run(e, NOW, 44)
    assert not ui.shown
    t = run(e, t, 2)
    assert ui.shown[0][:3] == ("popup", "break", "Time for a break")
    e.answer("break", "start")
    assert ui.shown[-1][:2] == ("overlay", "break-overlay")
    t = run(e, t, 6, idle=400)
    assert "break-overlay" in ui.closed and e.continuous == 0
    assert reminders.counts(db, "break", NOW)["taken"] == 1


def test_forced_break_has_no_way_out(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "forced": True, "every": 30})
    run(e, NOW, 31)
    assert ui.shown[0] == ("overlay", "break-overlay", "Break time", e.break_until, [])


def test_popups_wait_during_full_screen_apps(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.CUSTOM_KEY, [{"id": "water", "text": "Drink water", "kind": "times",
                                              "times": ["09:10"]}])
    t = run(e, NOW, 15, fullscreen=True)
    assert not [s for s in ui.shown if s[1] == "custom:water"] and ui.toasts == ["Reminder: Drink water"]
    run(e, t, 1)
    assert [s for s in ui.shown if s[1] == "custom:water"]


def test_snooze_limit_and_double_check(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.CUSTOM_KEY, [{"id": "s", "text": "Stretch", "kind": "interval", "every": 10,
                                              "snooze": 5, "max_snooze": 1, "check": 10}])
    t = run(e, NOW, 11)
    assert ui.shown[-1][4] == ["done", "snooze"]
    e.answer("custom:s", "snooze")
    t = run(e, t, 6)
    assert ui.shown[-1][1] == "custom:s" and ui.shown[-1][4] == ["done"]     # no snoozes left
    e.answer("custom:s", "done")
    t = run(e, t, 11, idle=120)
    assert ui.shown[-1][1] == "check:s"
    e.answer("check:s", "no")
    assert ui.shown[-1][1] == "custom:s"                                      # fires again
    assert reminders.counts(db, "s", NOW) == {"snoozed": 1, "done": 1, "not done": 1}


def test_sleep_warning_overlay_repeat_and_mode(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00", "mode": "dnd"})
    t = run(e, NOW.replace(hour=22, minute=29), 2)
    assert ui.shown[-1][1] == "sleep-warn"
    t = run(e, NOW.replace(hour=23), 1)
    assert ui.shown[-1][:2] == ("overlay", "sleep") and ui.modes == [("dnd", datetime(2026, 9, 15, 7, 0))]
    e.answer("sleep", "bed")
    run(e, t, 6)
    assert [s[1] for s in ui.shown].count("sleep") == 2                       # again after 5 min
    run(e, datetime(2026, 9, 15, 7, 1), 1)
    assert "sleep" in ui.closed


def test_random_time_once_a_day(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.CUSTOM_KEY, [{"id": "r", "text": "Go outside", "kind": "random",
                                              "window": ["09:00", "09:20"]}])
    run(e, NOW, 40)
    assert [s[1] for s in ui.shown].count("custom:r") == 1
