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

    def break_start(self, until):
        self.shown.append(("break_start", until))

    def break_end(self):
        self.shown.append(("break_end",))


def run(engine, start, minutes, idle=0, fullscreen=False):
    t = start
    for _ in range(int(minutes * 60 / reminders.TICK_SEC)):
        engine.tick(t, idle, fullscreen)
        t += timedelta(seconds=reminders.TICK_SEC)
    return t


def setup(tmp_path):
    db, ui = Database(tmp_path / "t.db"), FakeUI()
    return db, ui, reminders.Engine(db, ui)


def test_gentle_break_start_just_dismisses(tmp_path):
    db, ui, e = setup(tmp_path)
    t = run(e, NOW, 44)
    assert not ui.shown
    run(e, t, 2)
    assert ui.shown[0][:3] == ("popup", "break", "Time for a break")
    e.answer("break", "start")                          # gentle default: trust the user, no enforcement
    assert e.continuous == 0 and e.break_until is None
    assert not any(s[0] == "break_start" for s in ui.shown)
    assert reminders.counts(db, "break", NOW)["taken"] == 1


def test_strict_break_starts_itself_after_the_snoozes_run_out(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "strict": True, "every": 30,
                                             "max_snooze": 1, "snooze": 5, "length": 5})
    t = run(e, NOW, 31)
    assert ui.shown[0][:3] == ("popup", "break", "Time for a break")
    e.answer("break", "snooze")                         # one snooze allowed
    run(e, t, 6)                                        # snooze passes, still using -> it starts on its own
    assert any(s[0] == "break_start" for s in ui.shown) and e.break_until is not None


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
    assert ui.shown[-1][4] == ["done", "snooze", "dismiss"]
    e.answer("custom:s", "snooze")
    t = run(e, t, 6)
    assert ui.shown[-1][1] == "custom:s" and ui.shown[-1][4] == ["done", "dismiss"]   # no snoozes left
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


def test_do_not_disturb_holds_everything_until_it_is_over(tmp_path):
    """You asked Windows for quiet: the bedtime screen waits for it to end instead of being skipped - unlike
    a full-screen game, which it comes up over on purpose."""
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00"})
    t = NOW.replace(hour=23)
    for _ in range(12):                                  # a minute of ticks with Do not disturb on
        e.tick(t, 0, False, quiet=True)
        t += timedelta(seconds=reminders.TICK_SEC)
    assert not [s for s in ui.shown if s[1] == "sleep"]

    e.tick(t, 0, False, quiet=False)                     # you turn it off: it is still night, so up it comes
    assert ui.shown[-1][:2] == ("overlay", "sleep")


def test_a_game_does_not_hold_the_bedtime_screen(tmp_path):
    db, ui, e = setup(tmp_path)
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00"})
    e.tick(NOW.replace(hour=23), 0, True)                # full screen: the overlay still appears
    assert ui.shown[-1][:2] == ("overlay", "sleep")
