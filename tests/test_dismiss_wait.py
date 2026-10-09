"""Bedtime screen: an optional wait of N seconds before Dismiss works (Adam, 2026-10-08).

Dismiss shows "Dismiss (25)" and stays off until the wait is over, afresh each time the screen comes up.
Disable alerts and Emergency don't wait (they have their own challenge / uses), but backing out of them while
Dismiss is still waiting brings the screen straight back rather than counting as a Dismiss. A shorter wait (or Off)
is loosening and needs the challenge; a longer one is free."""
from datetime import datetime
from types import SimpleNamespace

import pytest

import reminders
from gui import screens
from tests.test_reminders import run, setup

NIGHT = datetime(2026, 10, 8, 23, 0)


def test_off_by_default():
    assert reminders.DEFAULT_SLEEP["dismiss_wait"] == 0
    assert reminders.dismiss_wait(reminders.DEFAULT_SLEEP) == 0
    assert reminders.dismiss_wait({}) == 0                     # settings saved before this: no wait


@pytest.mark.parametrize("typed, seconds", [("Off", 0), ("", 0), ("0", 0), ("25", 25), ("30s", 30),
                                            ("45 sec", 45), ("2 min", 120), ("1m", 60), ("600", 600)])
def test_typed_waits(typed, seconds):
    assert reminders.parse_seconds(typed) == seconds


@pytest.mark.parametrize("typed", ["abc", "-5", "1.5", "601", "11 min"])
def test_bad_or_too_long_waits_are_refused(typed):
    with pytest.raises(ValueError):
        reminders.parse_seconds(typed)


def test_a_stored_wait_is_kept_in_range():
    assert reminders.dismiss_wait({"dismiss_wait": 30}) == 30
    assert reminders.dismiss_wait({"dismiss_wait": -4}) == 0
    assert reminders.dismiss_wait({"dismiss_wait": 99999}) == reminders.MAX_DISMISS_WAIT
    assert reminders.dismiss_wait({"dismiss_wait": "junk"}) == 0


@pytest.mark.parametrize("guarded", [False, True])
def test_shorter_or_off_needs_the_challenge_longer_is_free(guarded):
    old = {**reminders.DEFAULT_SLEEP, "on": True, "guarded": guarded, "dismiss_wait": 30}
    assert reminders.loosens_reminder(old, {**old, "dismiss_wait": 10}) is True
    assert reminders.loosens_reminder(old, {**old, "dismiss_wait": 0}) is True
    assert reminders.loosens_reminder(old, {**old, "dismiss_wait": 60}) is False
    assert reminders.loosens_reminder(old, {**old, "dismiss_wait": 30}) is False
    assert reminders.loosens_reminder({**old, "dismiss_wait": 0}, {**old, "dismiss_wait": 10}) is False


def test_other_reminders_are_unchanged():
    """Breaks and your own reminders have no wait: the same answers as before."""
    assert reminders.loosens_reminder(reminders.DEFAULT_BREAK, {**reminders.DEFAULT_BREAK, "on": False}) is False
    important = {**reminders.DEFAULT_BREAK, "guarded": True}
    assert reminders.loosens_reminder(important, {**important, "on": False}) is True


def test_the_countdown_on_the_button():
    assert screens.dismiss_label(25.0) == ("Dismiss (25)", False, 1015)
    assert screens.dismiss_label(24.4)[:2] == ("Dismiss (25)", False)       # whole seconds, counted up
    assert screens.dismiss_label(24.4)[2] == 415                            # wakes as it turns to 24
    assert screens.dismiss_label(0.2)[:2] == ("Dismiss (1)", False)
    assert screens.dismiss_label(0) == ("Dismiss", True, None)
    assert screens.dismiss_label(-3) == ("Dismiss", True, None)


def _night(db, wait=30):
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00", "tiers": [], "repeat": 5, "dismiss_wait": wait})


def _bedtime_screens(ui):
    return [s for s in ui.shown if s[:2] == ("overlay", "sleep")]


def test_backing_out_while_waiting_brings_the_screen_straight_back(tmp_path):
    db, ui, e = setup(tmp_path)
    _night(db)
    t = run(e, NIGHT, 0.1)
    assert len(_bedtime_screens(ui)) == 1
    e.answer("sleep", "back")
    run(e, t, 0.1)
    assert len(_bedtime_screens(ui)) == 2                   # the next tick, not 5 min later


def test_dismiss_still_comes_back_on_its_escalation(tmp_path):
    db, ui, e = setup(tmp_path)
    _night(db)
    t = run(e, NIGHT, 0.1)
    e.answer("sleep", "dismiss")
    t = run(e, t, 4)
    assert len(_bedtime_screens(ui)) == 1
    run(e, t, 2)
    assert len(_bedtime_screens(ui)) == 2


class _Engine:
    def __init__(self):
        self.answers, self.now = [], NIGHT

    def answer(self, key, action):
        self.answers.append((key, action))


def _ui(monkeypatch, status="free"):
    """ReminderUI with a stand-in app: guard() runs the challenge's outcome straight away."""
    from gui import reminders_ui
    asked = []

    class App:
        db = None
        challenge = None

        def guard(self, changes, proceed, cancel=lambda: None):
            asked.append(changes)
            (proceed if status == "free" else cancel)()

        def after(self, ms, fn):
            pass

    dialogs = []

    class Dialog:
        def __init__(self, app, title, message, on_yes=None, on_no=lambda: None, **kw):
            self.on_no = on_no
            dialogs.append(self)
    monkeypatch.setattr(reminders_ui, "ConfirmDialog", Dialog)
    ui = reminders_ui.ReminderUI(App())
    ui.engine = _Engine()
    return ui, dialogs


@pytest.mark.parametrize("early, expected", [(True, "back"), (False, "dismiss")])
def test_disable_alerts_cancelled(monkeypatch, early, expected):
    ui, _ = _ui(monkeypatch, status="phrase")               # the challenge is cancelled
    ui.windows["sleep"] = SimpleNamespace(early=early)
    ui._answer("sleep", "disable")
    assert ui.engine.answers == [("sleep", expected)]


@pytest.mark.parametrize("early, expected", [(True, "back"), (False, "dismiss")])
def test_disable_alerts_question_answered_no(monkeypatch, early, expected):
    ui, dialogs = _ui(monkeypatch)                          # challenge passed, then "Cancel" on the question
    ui.windows["sleep"] = SimpleNamespace(early=early)
    ui._answer("sleep", "disable")
    dialogs[-1].on_no()
    assert ui.engine.answers == [("sleep", expected)]


def test_emergency_cancelled_while_waiting_comes_back(monkeypatch):
    import emergency
    monkeypatch.setattr(emergency, "available", lambda db, now: 2)
    monkeypatch.setattr(emergency, "left_by_tier", lambda db, now: [({"id": "a", "minutes": 5}, 2, True, None)])
    ui, dialogs = _ui(monkeypatch)
    ui.windows["sleep"] = SimpleNamespace(early=True)
    ui._answer("sleep", "emergency")
    assert ui.engine.answers == []                          # not dismissed at the click: no free way round the wait
    dialogs[-1].on_no()
    assert ui.engine.answers == [("sleep", "back")]


def test_emergency_after_the_wait_is_as_before(monkeypatch):
    import emergency
    monkeypatch.setattr(emergency, "available", lambda db, now: 2)
    monkeypatch.setattr(emergency, "left_by_tier", lambda db, now: [({"id": "a", "minutes": 5}, 2, True, None)])
    ui, dialogs = _ui(monkeypatch)
    ui.windows["sleep"] = SimpleNamespace(early=False)
    ui._answer("sleep", "emergency")
    assert ui.engine.answers == [("sleep", "dismiss")]      # counts as dismissed from the click on
    dialogs[-1].on_no()
    assert ui.engine.answers == [("sleep", "dismiss")]


def _tk_root():
    ctk = pytest.importorskip("customtkinter")
    import tkinter
    try:
        root = ctk.CTk()
    except tkinter.TclError:
        pytest.skip("no display")
    root.withdraw()
    return root


def test_the_bedtime_screen_s_dismiss_counts_down(monkeypatch):
    root = _tk_root()
    from gui import reminders_ui
    monkeypatch.setattr(screens, "monitors", lambda: [screens.Monitor((0, 0, 800, 600), True)])
    answers = []
    try:
        ov = reminders_ui.Overlay(root, "Time for bed", "Sleep well.", None,
                                  [("Dismiss", "dismiss"), ("Disable alerts", "disable")], answers.append,
                                  lambda: NIGHT, wait=25)
        root.update()
        assert ov._dismiss.cget("text") in ("Dismiss (25)", "Dismiss (24)")
        assert ov._dismiss.cget("state") == "disabled"
        ov._press("dismiss", answers.append)                 # too early: nothing happens
        root.update()
        assert answers == [] and ov.winfo_exists()
        ov._dismiss_end -= 25                                # the wait is over
        ov._dismiss_tick()
        assert (ov._dismiss.cget("text"), ov._dismiss.cget("state")) == ("Dismiss", "normal")
        ov._press("dismiss", answers.append)
        root.update()
        assert answers == ["dismiss"]
    finally:
        root.destroy()


def test_no_wait_leaves_dismiss_as_it_was(monkeypatch):
    root = _tk_root()
    from gui import reminders_ui
    monkeypatch.setattr(screens, "monitors", lambda: [screens.Monitor((0, 0, 800, 600), True)])
    try:
        ov = reminders_ui.Overlay(root, "Time for bed", "Sleep well.", None, [("Dismiss", "dismiss")],
                                  lambda a: None, lambda: NIGHT)
        root.update()
        assert ov._dismiss is None and ov._dismiss_end is None
        ov._press("dismiss", lambda a: None)
        assert ov.early is False
    finally:
        root.destroy()
