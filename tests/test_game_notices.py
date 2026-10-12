"""0.84.7: no Lockdown window over a full-screen game - Windows notifications instead, with the same buttons.

Adam: a "10 min left" popup while gaming full-screen made the game lag. Lockdown drew a topmost window over the
game (only reminders and the update notice waited for it to end - and even a held reminder still drew a corner popup).
Now a notice never builds a window of ours while something is full-screen; it sends a Windows notification - an
ordinary one that Windows may hold back during a game (never one that breaks through Do not disturb, since even a
Windows toast made his game hitch) and that stays in the notification centre a long while. Held reminders go out as
a notification carrying their buttons; answering there answers the reminder.

Here: the reminders engine holding a reminder over a game, and the Windows-notification wrapper (gui/toast.py). Where
a notice goes (the setting, a full-screen app, buttons) is resident/notices.py - tests/test_rebuild_reminder_ui.py."""
import sys
import types
from datetime import datetime, timedelta

import pytest

import alerts
import reminders
from gui import toast
from tests.test_reminders import run, setup

NOW = datetime(2026, 9, 14, 9, 0)


def test_windows_notifications_are_the_default():
    assert alerts.DEFAULTS["notify.format"] == "toast"
    assert alerts.FORMATS == {"toast": "Windows notifications (recommended)", "inapp": "Lockdown pop-ups",
                              "both": "Both"}


# ---------------------------------------------------------------- the reminders engine over a game

def _water(db):
    reminders.save(db, reminders.CUSTOM_KEY, [{"id": "water", "text": "Drink water", "kind": "times",
                                              "times": ["09:10"]}])


def test_a_held_reminder_goes_out_once_with_its_buttons_and_no_popup(tmp_path):
    db, ui, e = setup(tmp_path)
    _water(db)
    run(e, NOW, 15, fullscreen=True)
    assert ui.held_back == [("custom:water", "Reminder", ["done", "snooze", "dismiss"])]
    assert not [s for s in ui.shown if s[1] == "custom:water"] and ui.toasts == []   # no Tk popup, no double


def test_answered_on_the_notification_it_is_done_and_no_popup_follows(tmp_path):
    db, ui, e = setup(tmp_path)
    _water(db)
    t = run(e, NOW, 15, fullscreen=True)
    assert e.held_answer("custom:water", "done")
    assert "custom:water" not in e.waiting and reminders.counts(db, "water", NOW)["done"] == 1
    run(e, t, 1)                                                       # the game is over: nothing left to show
    assert not [s for s in ui.shown if s[1] == "custom:water"]
    assert not e.held_answer("custom:water", "done")                   # a second (late) click does nothing
    assert reminders.counts(db, "water", NOW)["done"] == 1


def test_unanswered_the_popup_comes_after_the_game(tmp_path):
    db, ui, e = setup(tmp_path)
    _water(db)
    t = run(e, NOW, 15, fullscreen=True)
    run(e, t, 1)
    assert [s for s in ui.shown if s[1] == "custom:water"]
    assert not e.held_answer("custom:water", "done")                   # the notice's button: too late now


# ---------------------------------------------------------------- gui/toast.py

def fake_windows_toasts():
    mod = types.ModuleType("windows_toasts")

    class Toast:
        count = 0

        def __init__(self, **kw):
            self.__dict__.update(kw)
            Toast.count += 1
            self.tag = f"t{Toast.count}"

    mod.Toast = Toast
    mod.ToastButton = lambda content, arguments: types.SimpleNamespace(content=content, arguments=arguments)
    mod.ToastScenario = types.SimpleNamespace(Default="Default", Reminder="Reminder", Important="Important")
    mod.InteractableWindowsToaster = lambda name, notifierAUMID=None: FakeWinToaster(name, notifierAUMID)
    return mod


class FakeWinToaster:
    def __init__(self, name="Lockdown", aumid=toast.APP_ID):
        self.name, self.aumid, self.shown, self.removed = name, aumid, [], []

    def show_toast(self, t):
        self.shown.append(t)

    def remove_toast(self, t):
        self.removed.append(t)


@pytest.fixture
def winrt(monkeypatch):
    monkeypatch.setitem(sys.modules, "windows_toasts", fake_windows_toasts())


def test_buttons_call_back_through_call_soon_and_the_body_opens_lockdown(winrt):
    queued, said, clicks = [], [], []
    t = toast.Toaster(queued.append, said.append, on_click=lambda: clicks.append("open"))
    notice = t.send("Install now?", [("Install", lambda: clicks.append("install")),
                                     ("Later", lambda: clicks.append("later"))], scenario="reminder")
    assert t._toaster.aumid == "com.husarp.lockdown" and said == []
    assert notice.scenario == "Reminder" and [b.content for b in notice.actions] == ["Install", "Later"]
    notice.on_activated(types.SimpleNamespace(arguments=notice.actions[1].arguments))
    notice.on_activated(types.SimpleNamespace(arguments=""))             # a click on the text itself
    assert clicks == []                                                   # (not on WinRT's thread)
    for fn in queued:
        fn()
    assert clicks == ["later", "open"]


def test_it_expires_by_itself_and_never_breaks_through_do_not_disturb(winrt):
    t = toast.Toaster(lambda fn: None, lambda text: None)
    before = datetime.now().astimezone()
    plain = t.send("10 min left", expire_min=toast.HELD_EXPIRE_MIN)
    assert plain.scenario == "Default" and plain.expiration_time.tzinfo is not None
    assert timedelta(minutes=119) < plain.expiration_time - before <= timedelta(minutes=120, seconds=5)
    assert t.send("No button", scenario="reminder").scenario == "Default"   # (Windows ignores it without one)
    assert t.send("x", scenario="urgent").scenario == "Default"           # no "urgent" toasts at all
    t.remove(plain)
    assert t._toaster.removed == [plain]


def test_without_windows_toasts_the_balloon_says_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "windows_toasts", None)              # import fails
    said = []
    t = toast.Toaster(lambda fn: None, said.append)
    assert t.send("Bedtime soon", [("OK", lambda: None)], title="Sleep") is None
    assert said == ["Sleep: Bedtime soon"]
    t.remove(None)                                                        # (nothing to take down: fine)


def test_a_refused_toast_is_still_said(winrt):
    said = []
    t = toast.Toaster(lambda fn: None, said.append)
    t._get().show_toast = lambda notice: (_ for _ in ()).throw(OSError("WinRT says no"))
    assert t.send("Reddit is blocked.") is None and said == ["Reddit is blocked."]
