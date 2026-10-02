"""0.84.7: no Lockdown window over a full-screen game - Windows notifications instead, with the same buttons.

Adam: a "10 min left" popup while gaming full-screen made the game lag. Lockdown drew a topmost Tk window over the
game (only reminders and the update notice waited for it to end - and even a held reminder still drew a corner popup
through ui.toast). Now App._show never builds a Tk popup while something is full-screen; it sends a Windows
notification - an ordinary one that Windows may hold back during a game (never one that breaks through Do not
disturb, since even a Windows toast made his game hitch) and that stays in the notification centre a long while.
Held reminders go out as a notification carrying their buttons; answering there answers the reminder.

Also here: the bedtime screen's "Disable alerts" challenge / question left unanswered no longer keeps the bedtime
screen away for free - after reminders_ui.ASK_LIMIT_MS it counts as Dismiss."""
import sys
import types
from datetime import datetime, timedelta

import pytest

import alerts
import reminders
from db import Database
from gui import toast
from tests.test_emergency_anytime import BED, _bedtime, overlays
from tests.test_reminders import run, setup

NOW = datetime(2026, 9, 14, 9, 0)


# ---------------------------------------------------------------- App._show: the one funnel for notices

class Toaster:
    def __init__(self, works=True):
        self.sent, self.removed, self._works = [], [], works

    def works(self):
        return self._works

    def send(self, text, actions=(), scenario=None, expire_min=toast.EXPIRE_MIN, title=None):
        self.sent.append({"text": text, "actions": [a[0] for a in actions], "scenario": scenario,
                          "expire": expire_min, "title": title})
        return f"toast-{len(self.sent)}"

    def remove(self, notice):
        self.removed.append(notice)


@pytest.fixture
def app(tmp_path, monkeypatch):
    from gui import app as appmod
    popups = []

    class Popup:
        def __init__(self, root, message, action=None, actions=None, sticky=False):
            popups.append((message, sticky))

    monkeypatch.setattr(appmod, "Popup", Popup)
    me = types.SimpleNamespace(db=Database(tmp_path / "t.db"), toaster=Toaster(), popup=None, popups=popups,
                               full=False)
    monkeypatch.setattr(appmod.win, "is_fullscreen", lambda: me.full)
    me._show = types.MethodType(appmod.LockdownApp._show, me)
    return me


def test_windows_notifications_are_the_default(app):
    assert alerts.DEFAULTS["notify.format"] == "toast"
    assert alerts.FORMATS == {"toast": "Windows notifications (recommended)", "inapp": "Lockdown pop-ups",
                              "both": "Both"}
    app._show("Reddit is blocked.")
    assert app.toaster.sent[-1]["text"] == "Reddit is blocked." and app.popups == []


@pytest.mark.parametrize("fmt", ["toast", "inapp", "both"])
def test_over_a_full_screen_app_never_a_tk_popup_whatever_the_setting(app, fmt):
    app.db.set_setting("notify.format", fmt)
    app.full = True
    assert app._show("Hollow Game is blocked in 10 min.", force=True)
    assert app.popups == []                                              # no window of ours over the game
    [sent] = app.toaster.sent
    assert sent["scenario"] is None                                      # never one that breaks through DND
    assert sent["expire"] == toast.HELD_EXPIRE_MIN                       # still in the bell after the game


def test_with_nothing_full_screen_the_setting_decides(app):
    app.db.set_setting("notify.format", "inapp")
    app._show("Focus mode on")
    assert app.popups == [("Focus mode on", False)] and app.toaster.sent == []
    app.db.set_setting("notify.format", "both")
    app._show("Focus mode off")
    assert len(app.popups) == 2 and app.toaster.sent[-1]["expire"] == toast.EXPIRE_MIN


def test_a_sticky_notice_stays_on_screen_only_when_nothing_is_full_screen(app):
    actions = [("Install", lambda: None), ("Remind me later", lambda: None)]
    app._show("Lockdown 0.84.8 is available", actions=actions, sticky=True)
    assert app.toaster.sent[-1]["scenario"] == "reminder"               # stays until answered
    assert app.toaster.sent[-1]["actions"] == ["Install", "Remind me later"]
    app.full = True
    app._show("Lockdown 0.84.8 is available", actions=actions, sticky=True)
    assert app.toaster.sent[-1]["scenario"] is None                     # over a game: an ordinary one
    app._show("Your week", action=("See the week", lambda: None))
    assert app.toaster.sent[-1]["actions"] == ["See the week"]           # the digest keeps its button


def test_the_update_notice_goes_out_over_a_game_but_waits_for_do_not_disturb(tmp_path, monkeypatch):
    """It used to wait for the game to end; as a Windows notification it needn't. Do not disturb still waits."""
    import updates
    from gui import app as appmod
    monkeypatch.setattr(updates, "VERSION", "0.84.3")
    db = Database(tmp_path / "t.db")
    found = {"version": "0.84.4", "url": "u", "newer": True, "asset": "http://x/s.exe", "size": 1}
    me = types.SimpleNamespace(db=db, update_popup=None, popup=None, update_later=lambda: None,
                               _install=lambda f=None: None, toaster=Toaster())
    me._show = lambda message, **k: (setattr(me, "last_toast", "the-toast") or True)
    notice = types.MethodType(appmod.LockdownApp._update_notice, me)
    monkeypatch.setattr(appmod.win, "is_fullscreen", lambda: True)
    monkeypatch.setattr(appmod.win, "do_not_disturb", lambda: True)
    notice(found)
    assert not hasattr(me, "update_toast")
    monkeypatch.setattr(appmod.win, "do_not_disturb", lambda: False)
    notice(found)
    assert me.update_toast == "the-toast"
    types.MethodType(appmod.LockdownApp._close_update_popup, me)()        # installed / later / skipped
    assert me.toaster.removed == ["the-toast"] and me.update_toast is None


def test_no_more_powershell_clean_up():
    from gui import app as appmod
    assert not hasattr(appmod.LockdownApp, "_clear_toast_history") and not hasattr(appmod, "TOAST_CLEAR_MS")


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


# ---------------------------------------------------------------- ReminderUI: notification <-> answer

def test_a_button_on_the_notification_answers_on_the_tk_thread(tmp_path):
    from gui import reminders_ui
    db, ui, e = setup(tmp_path)
    _water(db)
    queued = []
    toaster = toast.Toaster(call_soon=queued.append, fallback=lambda text: None)
    toaster._toaster = FakeWinToaster()
    screen = reminders_ui.ReminderUI(types.SimpleNamespace(db=db, toaster=toaster))
    screen.engine, e.ui = e, screen
    sys.modules["windows_toasts"] = fake_windows_toasts()
    try:
        run(e, NOW, 15, fullscreen=True)
        [shown] = toaster._toaster.shown
        assert shown.text_fields == ["Reminder", "Drink water"]
        assert [b.content for b in shown.actions] == ["Done", "Snooze 5 min", "Dismiss"]   # (not the bare ✕)
        assert shown.scenario == "Default"                             # held back by Windows during a game is fine
        shown.on_activated(types.SimpleNamespace(arguments=shown.actions[0].arguments))   # WinRT's thread
        assert reminders.counts(db, "water", NOW) == {}                # nothing touched off the Tk thread
        [fn] = queued
        fn()                                                           # the Tk thread runs it
        assert reminders.counts(db, "water", NOW)["done"] == 1 and "custom:water" not in e.waiting
    finally:
        del sys.modules["windows_toasts"]


def test_shown_as_a_popup_after_the_game_its_notification_is_taken_down(tmp_path, monkeypatch):
    from gui import reminders_ui
    db, ui, e = setup(tmp_path)
    _water(db)
    monkeypatch.setattr(reminders_ui, "ReminderPopup", lambda *a: types.SimpleNamespace(winfo_exists=lambda: 0))
    app = types.SimpleNamespace(db=db, toaster=Toaster())
    screen = reminders_ui.ReminderUI(app)
    screen.engine, e.ui = e, screen
    t = run(e, NOW, 15, fullscreen=True)
    assert app.toaster.sent[0]["title"] == "Reminder" and screen.notices == {"custom:water": "toast-1"}
    run(e, t, 1)
    assert app.toaster.removed == ["toast-1"] and screen.notices == {}


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


# ---------------------------------------------------------------- bedtime: "Disable alerts" left unanswered

class FakeApp:
    """guard() with the challenge on (it opens ChallengeWindow) or off (straight to the question); after() kept."""

    def __init__(self, db, challenge_on=True):
        self.db, self.challenge_on, self.challenge, self.timers = db, challenge_on, None, []

    def after(self, ms, fn):
        self.timers.append((ms, fn))

    def guard(self, changes, proceed, cancel=lambda: None):
        if not self.challenge_on:
            proceed()
            return
        self.challenge = Window(on_cancel=cancel, on_pass=proceed)


class Window:
    def __init__(self, on_cancel=None, on_pass=None, on_no=None, **kw):
        self.open, self.on_cancel, self.on_pass, self.kw = True, on_cancel, on_pass, kw
        self.on_no = on_no

    def winfo_exists(self):
        return self.open

    def bind(self, sequence, fn, add=None):   # (ChallengeWindow: a key typed in it)
        self.on_key = fn

    def _cancel(self):              # (ChallengeWindow: Cancel / closed)
        self.open = False
        self.on_cancel()


def _disable(tmp_path, monkeypatch, challenge_on=True):
    from gui import reminders_ui
    db, ui, e = _bedtime(tmp_path)
    questions = []

    def question(app, title, message, on_yes, on_no=lambda: None, on_alt=lambda: None, **kw):
        w = Window(**kw)
        w.on_yes, w.on_alt, w.on_no = on_yes, on_alt, on_no
        w._no = lambda: (setattr(w, "open", False), on_no())
        questions.append(w)
        return w

    monkeypatch.setattr(reminders_ui, "ConfirmDialog", question)
    app = FakeApp(db, challenge_on)
    screen = reminders_ui.ReminderUI(app)
    screen.engine = e
    e.tick(BED, 0, False)
    screen._answer("sleep", "disable")
    return e, ui, app, questions


def test_an_unanswered_challenge_counts_as_dismiss_after_two_minutes(tmp_path, monkeypatch):
    """It used to keep the bedtime screen away all night: the engine still thought the screen was up."""
    from gui import reminders_ui
    e, ui, app, _ = _disable(tmp_path, monkeypatch)
    [(ms, time_up)] = app.timers
    assert ms == reminders_ui.ASK_LIMIT_MS == 2 * 60_000
    t = run(e, BED, 2)                                                  # the challenge sits there, untouched
    assert len(overlays(ui)) == 1 and "sleep" in e.open
    time_up()
    assert not app.challenge.open and "sleep" not in e.open             # closed, counted as Dismiss
    t = run(e, t, 14)
    assert len(overlays(ui)) == 1
    run(e, t, 2)                                                        # back on its 15-min escalation
    assert len(overlays(ui)) == 2


def test_the_off_tonight_question_has_the_same_limit(tmp_path, monkeypatch):
    e, ui, app, questions = _disable(tmp_path, monkeypatch, challenge_on=False)
    [q] = questions
    t = run(e, BED, 2)
    app.timers[0][1]()
    assert not q.open and "sleep" not in e.open
    run(e, t, 16)
    assert len(overlays(ui)) == 2
    q.on_yes()                                                          # (a late answer, if one got through)
    assert len(overlays(ui)) == 2                                       # ... changes nothing


def test_answered_in_time_the_limit_does_nothing(tmp_path, monkeypatch):
    e, ui, app, questions = _disable(tmp_path, monkeypatch)
    app.challenge.on_pass()                                             # passed: the question
    questions[0].on_yes()                                               # "Off tonight"
    app.timers[0][1]()                                                  # the limit comes later: no dismiss
    run(e, BED, 120)
    assert len(overlays(ui)) == 1                                       # off for tonight, as asked


def test_cancelling_the_challenge_dismisses_once(tmp_path, monkeypatch):
    e, ui, app, _ = _disable(tmp_path, monkeypatch)
    app.challenge._cancel()
    first = e.sleep_next[BED.strftime(reminders.TIME_FMT)]
    run(e, BED, 1)
    app.timers[0][1]()                                                  # no second dismiss pushing it later
    assert e.sleep_next[BED.strftime(reminders.TIME_FMT)] == first


def test_still_typing_the_challenge_it_stays_open_but_not_for_ever(tmp_path, monkeypatch):
    """A long phrase can take more than 2 min to type: a key in the last 2 min keeps it open - up to 16 min."""
    from gui import reminders_ui
    e, ui, app, _ = _disable(tmp_path, monkeypatch)
    for _ in range(reminders_ui.ASK_MAX_MS // reminders_ui.ASK_LIMIT_MS - 1):
        app.challenge.on_key(None)                                      # typing
        app.timers.pop(0)[1]()
        assert app.challenge.open and "sleep" in e.open and len(app.timers) == 1
    app.challenge.on_key(None)
    app.timers.pop(0)[1]()                                              # 16 min: closed, typing or not
    assert not app.challenge.open and "sleep" not in e.open and app.timers == []


def test_passing_the_challenge_late_gives_the_question_its_own_two_minutes(tmp_path, monkeypatch):
    e, ui, app, questions = _disable(tmp_path, monkeypatch)
    app.challenge.on_key(None)
    app.challenge.on_pass()                                             # passed at 1:59: the question opens
    app.timers.pop(0)[1]()
    assert questions[0].open                                            # not cut off seconds later
    app.timers.pop(0)[1]()
    assert not questions[0].open and "sleep" not in e.open              # left 2 min: Dismiss


def test_a_muting_mode_sends_no_notification_for_a_held_reminder(tmp_path, monkeypatch):
    from gui import reminders_ui
    db, ui, e = setup(tmp_path)
    _water(db)
    monkeypatch.setattr(reminders_ui.modes, "active", lambda db, now: {"mode": {"mute": True}})
    app = types.SimpleNamespace(db=db, toaster=Toaster())
    screen = reminders_ui.ReminderUI(app)
    screen.engine, e.ui = e, screen
    t = NOW
    for _ in range(15 * 60 // reminders.TICK_SEC):                     # (a mode that mutes: the engine's "quiet")
        e.tick(t, 0, False, quiet=True)
        t += timedelta(seconds=reminders.TICK_SEC)
    assert app.toaster.sent == [] and "custom:water" in e.waiting      # said once the mode ends, as a popup


def test_without_windows_notifications_a_notice_with_buttons_keeps_them_on_the_desktop(app):
    app.toaster._works = False                                           # (WinRT missing or refused here)
    app._show("Lockdown 0.84.8 is available", actions=[("Install", lambda: None)], sticky=True)
    assert app.popups == [("Lockdown 0.84.8 is available", True)] and app.toaster.sent == []
    app._show("Reddit is blocked.")                                      # nothing to click: the balloon is fine
    assert app.toaster.sent[-1]["text"] == "Reddit is blocked." and len(app.popups) == 1
    app.full = True
    app._show("Lockdown 0.84.8 is available", actions=[("Install", lambda: None)], sticky=True)
    assert len(app.popups) == 1                                          # over a game still never a window
