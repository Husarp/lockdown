"""R07: the corner window's cards, the ReminderUI adapter, notice routing and the tray's handlers.

Everything runs with fakes for the window, the publisher, the worker and Windows notifications; the real reminder
engine produces the popups. What can only be proven on Windows: placing the window in the work area of the monitor
with the foreground window, never taking the focus, and toasts in the Action Center."""
import random
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import antibypass as ab
import modes
import pause
import reminders
import webapi
from db import Database
from resident import corner as corner_mod
from resident import notices, reminder_ui, tray_actions, workers
from resident.corner import Corner, CornerHost, corner_rect
from webapi import core
from webapi.guard import GuardService

NOW = datetime(2026, 9, 14, 9, 0)      # a Monday morning


# ---------------------------------------------------------------- fakes

class Log:
    """One list that every fake writes to, so the ORDER of things can be checked."""

    def __init__(self):
        self.events = []

    def kinds(self):
        return [e[0] for e in self.events]


class FakePublisher:
    def __init__(self, log):
        self.log = log
        self.sent = []

    def publish(self, event, payload=None, key=None):
        self.sent.append((event, payload))
        self.log.events.append(("push", event))


class FakeHost:
    def __init__(self, log):
        self.log = log
        self.placed = []

    def show(self):
        self.log.events.append(("show",))

    def hide(self):
        self.log.events.append(("hide",))

    def place(self, height, dpr=1.0):
        self.placed.append((height, dpr))


class FakeWorker:
    """What the ReminderWorker offers: answer / held_answer, and its own database."""

    def __init__(self, log, db):
        self.log, self.db = log, db
        self.answers, self.held = [], []

    def answer(self, key, action):
        self.log.events.append(("answer", key, action))
        self.answers.append((key, action))

    def held_answer(self, key, action):
        self.held.append((key, action))


class FakeToaster:
    def __init__(self, works=True):
        self.sent, self.removed, self._works = [], [], works
        self.fallback = lambda text: None

    def works(self):
        return self._works

    def send(self, text, actions=(), scenario=None, expire_min=10, title=None):
        toast = object()
        self.sent.append({"text": text, "title": title, "buttons": [label for label, _ in actions], "actions": list(actions),
                          "scenario": scenario, "expire": expire_min, "toast": toast})
        return toast

    def remove(self, toast):
        self.removed.append(toast)


class FakeWin:
    def __init__(self):
        self.dnd, self.fullscreen, self.minimized = False, False, 0

    def do_not_disturb(self):
        return self.dnd

    def is_fullscreen(self):
        return self.fullscreen

    def minimize_all(self):
        self.minimized += 1
        return 1


class Env:
    def __init__(self, tmp_path):
        self.log = Log()
        self.path = tmp_path / "t.db"
        self.db = Database(self.path)
        self.publisher, self.host = FakePublisher(self.log), FakeHost(self.log)
        self.corner = Corner(self.publisher, self.host, spawn=lambda fn: fn())   # (callbacks run at once)
        self.win = FakeWin()
        self.hub = workers.Hub(self.path, clock=SimpleNamespace(now=lambda db: NOW, now_with=lambda o: NOW,
                                                                wall=lambda: 1e6, mono=lambda: 0.0), win=self.win,
                               log_sink=lambda text: None)
        self.shown = []
        self.hub.notify.show = lambda message, action=None, actions=None, sticky=False: self.shown.append(
            (message, action, actions, sticky))
        self.toaster = FakeToaster()
        self.notices = notices.Notices(self.hub, self.corner, self.toaster, read_format=lambda: "toast",
                                       spawn=lambda fn: fn())
        self.ui = reminder_ui.CornerReminderUI(self.hub, self.corner, self.toaster, self.notices)
        self.engine = reminders.Engine(self.db, self.ui)
        self.worker = FakeWorker(self.log, self.db)
        self.ui.engine, self.ui.worker = self.engine, self.worker

    def tick(self, minutes=0.0, now=NOW, fullscreen=False):
        t = now
        for _ in range(int(minutes * 60 / reminders.TICK_SEC) or 1):
            self.engine.tick(t, 0, fullscreen)
            t += timedelta(seconds=reminders.TICK_SEC)
        return t

    def card(self, key):
        return next((c for c in self.corner.cards() if c["key"] == key), None)

    def actions(self, key):
        return [b["action"] for b in self.card(key)["buttons"]]


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def custom(**kw):
    return {**reminders.DEFAULT_CUSTOM, "id": "water", "text": "Drink water", "kind": "times", "times": ["09:00"], **kw}


# ---------------------------------------------------------------- placing the window

def test_the_window_sits_in_the_bottom_right_corner_of_the_work_area():
    work = (0, 0, 1920, 1040)
    assert corner_rect(work, 200) == (1920 - 12 - 360, 1040 - 12 - 200, 360, 200)


def test_a_scaled_screen_and_a_monitor_left_of_the_primary():
    assert corner_rect((0, 0, 2560, 1400), 200, dpr=1.5) == (2560 - 18 - 540, 1400 - 18 - 300, 540, 300)
    x, y, w, h = corner_rect((-1920, 0, 0, 1040), 100)
    assert x == -12 - 360 and x + w + 12 == 0 and y + h + 12 == 1040


def test_a_tall_stack_is_cut_to_the_work_area():
    x, y, w, h = corner_rect((0, 0, 1920, 600), 5000)
    assert y >= 12 and y + h == 600 - 12


def test_the_host_places_without_taking_the_focus_off_windows():
    """Off Windows the host falls back to pywebview's own calls (on Windows it uses SWP_NOACTIVATE: not provable here)."""
    calls = []
    window = SimpleNamespace(show=lambda: calls.append("show"), hide=lambda: calls.append("hide"),
                             move=lambda x, y: calls.append(("move", x, y)),
                             resize=lambda w, h: calls.append(("resize", w, h)))
    host = CornerHost(window, work_area=lambda: (0, 0, 1000, 800))
    host.show()
    host.place(150, 1.0)
    host.hide()
    assert ("resize", 360, 150) in calls and ("move", 1000 - 12 - 360, 800 - 12 - 150) in calls
    assert calls[-1] == "hide"


# ---------------------------------------------------------------- the stack of cards

def test_cards_stack_oldest_first_so_the_newest_is_lowest(env):
    c = env.corner
    c.popup("custom:a", "Reminder", "first", [("Done", "done")])
    c.notice("second")
    c.popup("break", "Time for a break", "third", [("Start break", "start")])
    assert [card["key"] for card in c.cards()][0] == "custom:a" and c.cards()[-1]["key"] == "break"
    assert c.cards()[1]["kind"] == "notice"


def test_a_popup_with_the_same_key_comes_back_as_the_newest(env):
    c = env.corner
    c.popup("custom:a", "Reminder", "first", [("Done", "done")])
    c.popup("break", "Time for a break", "x", [("Start break", "start")])
    c.popup("custom:a", "Reminders", "• first\n• second", [("Done", "done")])
    assert [card["key"] for card in c.cards()] == ["break", "custom:a"]
    assert c.cards()[-1]["title"] == "Reminders"


def test_the_window_shows_with_the_first_card_and_hides_after_the_last(env):
    c = env.corner
    c.popup("a", "t", "x", [("OK", "ok")])
    c.popup("b", "t", "y", [("OK", "ok")])
    assert env.log.kinds().count("show") == 1                 # shown once, not per card
    c.remove("a")
    assert "hide" not in env.log.kinds()
    c.remove("b")
    assert env.log.kinds()[-1] == "hide"
    assert c.remove("b") is False                              # nothing to take down any more


def test_every_change_pushes_the_whole_list(env):
    c = env.corner
    c.popup("a", "t", "x", [("OK", "ok")])
    c.notice("hello")
    event, payload = env.publisher.sent[-1]
    assert event == "corner" and [x["key"] for x in payload["cards"]][0] == "a" and len(payload["cards"]) == 2
    assert payload["seq"] == env.publisher.sent[-2][1]["seq"] + 1                  # numbered: the page drops a late old list
    assert payload["cards"][1]["text"] == "hello" and payload["cards"][1]["ms"] == corner_mod.NOTICE_MS


def test_the_pages_height_resizes_the_window_only_while_it_is_up(env):
    c = env.corner
    c.page_size(120, 1.25)
    assert env.host.placed == []                              # nothing up yet
    c.popup("a", "t", "x", [("OK", "ok")])
    c.page_size(120, 1.25)
    c.page_size(0, 1.0)                                       # an empty stack reports 0: ignored
    assert env.host.placed == [(120, 1.25)]


def test_a_failing_window_never_stops_a_reminder(env):
    env.host.show = lambda: (_ for _ in ()).throw(RuntimeError("no window"))
    errors = []
    env.corner.log = errors.append
    env.corner.popup("a", "t", "x", [("OK", "ok")])
    assert env.corner.has("a") and errors and "no window" in errors[0]


def test_ready_gives_the_cards_that_are_up(env):
    env.corner.popup("a", "t", "x", [("OK", "ok")])
    assert [c["key"] for c in env.corner.ready()["cards"]] == ["a"]


# ---------------------------------------------------------------- the popups the engine makes

def test_break_popup_buttons_gentle(env):
    env.tick(46)
    assert env.card("break")["title"] == "Time for a break"
    assert env.actions("break") == ["start", "snooze", "dismiss"]
    labels = [b["label"] for b in env.card("break")["buttons"]]
    assert labels[0] == "Start break" and labels[1].startswith("Snooze ") and labels[2] == "✕"
    assert [b["style"] for b in env.card("break")["buttons"]] == ["primary", "outline", "dismiss"]


def test_strict_break_has_no_dismiss_and_snooze_only_while_snoozes_are_left(env):
    reminders.save(env.db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "strict": True, "max_snooze": 2})
    env.engine.break_snoozes = 1
    env.engine._show_break(reminders.load(env.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK))
    assert env.actions("break") == ["start", "snooze"]
    assert "Strict break: 1 snooze left" in env.card("break")["text"]
    env.engine.break_snoozes = 2
    env.engine._show_break(reminders.load(env.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK))
    assert env.actions("break") == ["start"]
    with pytest.raises(ValueError):                             # the page can't invent a ✕ the card doesn't have
        env.corner.answer("break", "dismiss")
    assert env.worker.answers == [] and env.corner.has("break")


def test_break_popup_carries_the_reminders_riding_along(env):
    reminders.save(env.db, reminders.CUSTOM_KEY, [custom(id="eyes", text="Rest your eyes")])
    env.engine.folded = ["eyes"]
    env.engine._show_break(reminders.load(env.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK))
    assert env.card("break")["text"].endswith("While you're up:\n• Rest your eyes")


def test_custom_reminder_buttons_and_max_snooze(env):
    reminders.save(env.db, reminders.CUSTOM_KEY, [custom(max_snooze=1, snooze=7)])
    env.tick(1)
    assert env.actions("custom:water") == ["done", "snooze", "dismiss"]
    assert env.card("custom:water")["buttons"][1]["label"] == "Snooze 7 min"
    env.corner.answer("custom:water", "snooze")
    assert env.worker.answers == [("custom:water", "snooze")]
    env.engine.answer("custom:water", "snooze")                 # (the worker would have done this)
    env.engine.snoozes_used["custom:water"] = 1
    env.engine.snoozed.clear()
    env.ui.close("custom:water")
    env.engine.open.discard("custom:water")
    env.engine.fired.clear()
    env.engine._fire(reminders.custom_list(env.db)[0], NOW, paced=False)
    assert env.actions("custom:water") == ["done", "dismiss"]    # out of snoozes: Done / ✕ only


def test_a_grouped_popup_has_one_done_and_bullet_lines(env):
    reminders.save(env.db, reminders.CUSTOM_KEY, [custom(id="a", text="Water"), custom(id="b", text="Stretch")])
    env.engine.grouped["custom:a"] = ["a", "b"]
    env.engine._regroup("custom:a", NOW)
    card = env.card("custom:a")
    assert card["title"] == "Reminders" and card["text"] == "• Water\n• Stretch"
    assert [b["action"] for b in card["buttons"]].count("done") == 1


def test_did_you_actually_do_it_asks_yes_or_no(env):
    reminders.save(env.db, reminders.CUSTOM_KEY, [custom(check=5)])
    env.engine.checks["water"] = NOW
    env.tick(1)
    card = env.card("check:water")
    assert card["title"] == "Did you actually do it?"
    assert [(b["label"], b["action"]) for b in card["buttons"]] == [("Yes", "yes"), ("No", "no")]


def test_bedtime_soon_is_a_single_ok(env):
    reminders.save(env.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "09:20",
                                                 "wake": "07:00", "before": 30})
    env.tick(1)
    card = env.card("sleep-warn")
    assert card["title"] == "Bedtime soon"
    assert [(b["label"], b["action"]) for b in card["buttons"]] == [("OK", "ok")]


def test_the_engine_closing_a_popup_takes_the_card_down(env):
    env.corner.popup("break", "Time for a break", "x", [("Start break", "start")])
    env.ui.close("break")
    assert env.card("break") is None and env.log.kinds()[-1] == "hide"


# ---------------------------------------------------------------- answering

def test_the_card_and_window_go_before_the_engine_hears_the_answer(env):
    env.corner.popup("custom:water", "Reminder", "Drink", [("Done", "done"), ("✕", "dismiss")])
    env.corner.answer("custom:water", "done")
    kinds = env.log.kinds()
    assert kinds.index("hide") < kinds.index("answer")           # the ordering fix: a challenge never ends up behind a popup
    assert env.worker.answers == [("custom:water", "done")]


def test_an_unknown_card_or_button_is_refused(env):
    env.corner.popup("custom:water", "Reminder", "Drink", [("Done", "done")])
    with pytest.raises(ValueError):
        env.corner.answer("custom:nope", "done")
    with pytest.raises(ValueError):
        env.corner.answer("custom:water", "emergency")           # not a button of that card
    with pytest.raises(ValueError):
        env.corner.answer("custom:water", "off_tonight")
    assert env.worker.answers == [] and env.corner.has("custom:water")


def test_a_second_press_does_nothing(env):
    env.corner.popup("custom:water", "Reminder", "Drink", [("Done", "done")])
    env.corner.answer("custom:water", "done")
    with pytest.raises(ValueError):
        env.corner.answer("custom:water", "done")
    assert env.worker.answers == [("custom:water", "done")]


def test_a_notice_closes_and_its_button_runs_once(env):
    ran = []
    key = env.corner.notice("Week ready", [("See the week", lambda: ran.append(1))])
    assert [b["action"] for b in env.corner.cards()[0]["buttons"]] == ["act:0"]
    env.corner.answer(key, "act:0")
    assert ran == [1] and env.corner.cards() == [] and env.log.kinds()[-1] == "hide"
    key2 = env.corner.notice("Passing")
    env.corner.answer(key2, "close")
    assert env.corner.cards() == [] and ran == [1]


def test_several_buttons_on_a_notice_make_the_first_the_main_one(env):
    env.corner.notice("Lockdown 1.2 is out", [("Install", lambda: None), ("Remind me later", lambda: None)], sticky=True)
    card = env.corner.cards()[0]
    assert card["sticky"] is True and [b["style"] for b in card["buttons"]] == ["primary", "outline"]


@pytest.fixture
def locked(env):
    """Anti-Bypass on (a phrase), the guard real; the reminder 'water' is Important. Dismissing its popup is still free."""
    ab.save(env.db, {**ab.settings(env.db), "phrase": True, "length": 30})
    reminders.save(env.db, reminders.CUSTOM_KEY, [custom(guarded=True)])
    return env


def test_dismissing_an_important_reminder_answers_at_once_with_no_ticket(locked):
    env = locked
    env.tick(1)
    assert env.card("custom:water") is not None
    reply = env.corner.answer("custom:water", "dismiss")
    assert reply is None                                         # no ticket, no challenge
    assert env.worker.answers == [("custom:water", "dismiss")]   # answered right away
    assert env.card("custom:water") is None and env.log.kinds().index("hide") < env.log.kinds().index("answer")


def test_other_answers_to_an_important_reminder_are_free(locked):
    env = locked
    env.tick(1)
    assert env.corner.answer("custom:water", "done") is None
    assert env.worker.answers == [("custom:water", "done")]


# ---------------------------------------------------------------- the rest of the adapter

def test_held_popup_becomes_a_windows_notification_with_the_same_buttons(env):
    reminders.save(env.db, reminders.CUSTOM_KEY, [custom()])
    env.tick(1, fullscreen=True)
    assert env.card("custom:water") is None                       # nothing of ours over the game
    sent = env.toaster.sent[0]
    assert sent["title"] == "Reminder" and sent["buttons"] == ["Done", "Snooze 5 min", "Dismiss"]
    assert sent["expire"] == 120
    sent["actions"][0][1]()                                       # the Done button on the notification
    assert env.worker.held == [("custom:water", "done")]


def test_a_held_popup_is_not_notified_while_a_muting_mode_is_on(env):
    env.hub.snapshot = SimpleNamespace(mode={"mute": True}, pause=None)
    env.ui.held("custom:water", "Reminder", "Drink", [("Done", "done")])
    assert env.toaster.sent == []


def test_closing_a_held_popup_takes_its_notification_out_of_the_action_center(env):
    env.ui.held("custom:water", "Reminder", "Drink", [("Done", "done")])
    toast = env.toaster.sent[0]["toast"]
    env.ui.close("custom:water")
    assert env.toaster.removed == [toast]


def test_toast_goes_through_the_normal_mute_rule(env):
    env.ui.toast("Skipped it")
    env.ui.break_end()
    assert [m[0] for m in env.shown] == ["Skipped it", "Break over - welcome back."]


def test_break_start_minimises_everything_and_says_so_forcibly(env):
    forced = []
    env.hub.notify = lambda message, force=False, **kw: forced.append((message, force))
    env.engine.now = NOW
    env.ui.break_start(NOW + timedelta(minutes=5))
    assert env.win.minimized == 1
    assert forced == [("Break started - your windows are minimised for 5 min. Step away from the screen.", True)]


def test_the_overlay_goes_to_the_overlay_handler(env):
    seen = []
    env.ui.overlay_handler = SimpleNamespace(show=lambda *a: seen.append(("show", a[0])),
                                             close=lambda key: seen.append(("close", key)))
    env.ui.overlay("sleep", "Time for bed", "Sleep well", None, [("Dismiss", "dismiss")])
    env.ui.close("sleep")
    assert seen == [("show", "sleep"), ("close", "sleep")]
    assert env.corner.cards() == []


def test_a_reminder_starts_a_mode_unless_another_is_locked_on(env):
    mode = modes.load(env.db)[0]
    env.ui.start_mode(mode["id"], NOW + timedelta(hours=8))
    assert modes.active(env.db, NOW)["mode"]["id"] == mode["id"]
    assert [m[0] for m in env.shown][-1].startswith(f"{mode['name']} mode on until 17:00")
    other = modes.load(env.db)[1]
    modes.start(env.db, mode["id"], NOW, NOW + timedelta(hours=2), locked=True)
    env.ui.start_mode(other["id"], None)
    assert modes.active(env.db, NOW)["mode"]["id"] == mode["id"]    # the locked one stays
    env.ui.start_mode("no-such-mode", None)                           # a deleted mode: nothing happens


# ---------------------------------------------------------------- notices: where they go

@pytest.mark.parametrize("fmt, full, buttons, works, expected", [
    ("toast", False, False, True, (True, False)),
    ("inapp", False, False, True, (False, True)),
    ("both", False, False, True, (True, True)),
    ("toast", True, False, True, (True, False)),      # a full-screen app: never a window of ours
    ("inapp", True, False, True, (True, False)),
    ("both", True, True, True, (True, False)),
    ("toast", False, True, False, (False, True)),     # the balloon has no buttons: the card keeps them
    ("toast", False, False, False, (True, False)),    # no buttons: the balloon will do
])
def test_routing(fmt, full, buttons, works, expected):
    assert notices.route(fmt, full, buttons, works) == expected


def make_notices(env, fmt, works=True):
    env.toaster._works = works
    return notices.Notices(env.hub, env.corner, env.toaster, read_format=lambda: fmt, spawn=lambda fn: fn())


def test_windows_only(env):
    make_notices(env, "toast").show("Hello")
    assert [s["text"] for s in env.toaster.sent] == ["Hello"] and env.corner.cards() == []
    assert env.toaster.sent[0]["scenario"] is None and env.toaster.sent[0]["expire"] == 10


def test_lockdown_only(env):
    make_notices(env, "inapp").show("Hello", action=("See the week", lambda: None))
    assert env.toaster.sent == [] and env.corner.cards()[0]["text"] == "Hello"
    assert env.corner.cards()[0]["buttons"][0]["label"] == "See the week"


def test_both(env):
    make_notices(env, "both").show("Hello")
    assert len(env.toaster.sent) == 1 and len(env.corner.cards()) == 1


def test_a_full_screen_app_gets_only_a_windows_notification_that_waits_long(env):
    env.win.fullscreen = True
    make_notices(env, "both").show("Hello", sticky=True)
    assert env.corner.cards() == []
    assert env.toaster.sent[0]["expire"] == 120 and env.toaster.sent[0]["scenario"] is None


def test_a_sticky_notice_is_a_windows_reminder_on_the_desktop(env):
    make_notices(env, "toast").show("Update", actions=[("Install", lambda: None)], sticky=True)
    assert env.toaster.sent[0]["scenario"] == "reminder"


def test_buttons_without_working_windows_notifications_become_a_card(env):
    make_notices(env, "toast", works=False).show("Update", actions=[("Install", lambda: None)])
    assert env.toaster.sent == [] and env.corner.cards()[0]["buttons"][0]["label"] == "Install"


def test_clearing_a_notification_runs_in_a_thread_of_its_own(env):
    seen = []
    n = notices.Notices(env.hub, env.corner, env.toaster, read_format=lambda: "toast", spawn=seen.append)
    n.clear("toast-1")
    assert env.toaster.removed == [] and len(seen) == 1           # handed off, not done on the caller's thread
    seen[0]()
    assert env.toaster.removed == ["toast-1"]


def test_the_format_is_read_now_and_then_not_for_every_notice(env):
    reads, t = [], [0.0]
    n = notices.Notices(env.hub, env.corner, env.toaster, read_format=lambda: reads.append(1) or "toast",
                        clock=lambda: t[0])
    n.fmt(), n.fmt(), n.fmt()
    assert len(reads) == 1
    t[0] = 10
    n.fmt()
    n.refresh()
    n.fmt()
    assert len(reads) == 3


def test_the_real_setting_is_read(env):
    n = notices.Notices(env.hub, env.corner, env.toaster)
    assert n.fmt() == "toast"                                      # alerts.DEFAULTS
    env.db.set_setting("notify.format", "both")
    n.refresh()
    assert n.fmt() == "both"


# ---------------------------------------------------------------- the mute rule (Notifier -> Notices)

def muted_env(env, mode_mute=False, pause_silent=None):
    env.hub.notify.show = make_notices(env, "inapp").show
    env.hub.snapshot = SimpleNamespace(mode={"mute": mode_mute} if mode_mute else None,
                                       pause={"silent": pause_silent} if pause_silent is not None else None)
    return env.hub.notify


def test_a_muting_mode_holds_notices_unless_forced(env):
    notify = muted_env(env, mode_mute=True)
    assert notify("quiet please") is False and env.corner.cards() == []
    assert notify("you are about to be blocked", force=True) is True
    assert [c["text"] for c in env.corner.cards()] == ["you are about to be blocked"]


def test_a_silent_pause_holds_everything_even_forced(env):
    notify = muted_env(env, pause_silent=True)
    assert notify("x") is False and notify("y", force=True) is False and env.corner.cards() == []


def test_a_pause_that_is_not_silent_holds_nothing(env):
    notify = muted_env(env, pause_silent=False)
    assert notify("x") is True and len(env.corner.cards()) == 1


# ---------------------------------------------------------------- the Api

@pytest.fixture
def api_env(env):
    ctx = webapi.make_context(env.path, clock=lambda db: NOW)
    ctx.corner = env.corner
    env.ctx, env.api = ctx, core.build_api(ctx)
    return env


def test_the_corner_methods_are_classified_and_registered():
    kinds = dict(core.api_methods())
    assert kinds["corner_answer"] == "ui" and kinds["corner_ready"] == "ui" and kinds["corner_size"] == "ui"


def test_corner_ready_gives_the_look_and_the_cards(api_env):
    api_env.corner.popup("a", "t", "x", [("OK", "ok")])
    reply = api_env.api.corner_ready()
    assert reply["ok"] and reply["data"]["look"]["theme"] and [c["key"] for c in reply["data"]["cards"]] == ["a"]


def test_corner_answer_through_the_api_hides_first(api_env):
    env = api_env
    env.corner.popup("custom:water", "Reminder", "Drink", [("Done", "done")])
    reply = env.api.corner_answer("custom:water", "done")
    assert reply == {"ok": True, "data": {"done": True}}
    assert env.log.kinds().index("hide") < env.log.kinds().index("answer")


def test_corner_answer_refuses_what_is_not_on_screen(api_env):
    env = api_env
    env.corner.popup("custom:water", "Reminder", "Drink", [("Done", "done")])
    for key, action in (("sleep", "off_tonight"), ("custom:water", "snooze"), (1, "done"), ("custom:water", None)):
        reply = env.api.corner_answer(key, action)
        assert reply["ok"] is False
    assert env.worker.answers == []


def test_important_dismiss_through_the_api_is_free(api_env):
    env = api_env
    ab.save(env.db, {**ab.settings(env.db), "phrase": True, "length": 30})
    env.corner.popup("custom:water", "Reminder", "Drink", [("Done", "done"), ("✕", "dismiss")])
    assert env.api.corner_answer("custom:water", "dismiss") == {"ok": True, "data": {"done": True}}
    assert env.worker.answers == [("custom:water", "dismiss")]


def test_corner_size_checks_its_numbers(api_env):
    env = api_env
    env.corner.popup("a", "t", "x", [("OK", "ok")])
    assert env.api.corner_size(140, 1.5)["ok"] is True
    assert env.host.placed == [(140.0, 1.5)]
    for bad in ((-1, 1), (99999, 1), ("tall", 1), (True, 1), (100, 0), (100, 50), (100, "x")):
        assert env.api.corner_size(*bad)["ok"] is False
    assert env.host.placed == [(140.0, 1.5)]


# ---------------------------------------------------------------- the tray's handlers

class Main:
    """show_main + publish_main, recorded in order."""

    def __init__(self):
        self.events = []

    def show(self):
        self.events.append("show")

    def publish(self, event, payload=None):
        self.events.append((event, payload))


@pytest.fixture
def tray(env):
    ctx = webapi.make_context(env.path, clock=lambda db: NOW)
    clock = SimpleNamespace(mono=1000.0)
    ctx.guard = GuardService(lambda: ctx.db, lambda db: NOW, monotonic=lambda: clock.mono, rng=random.Random(5))
    env.main, env.pokes, env.browsed = Main(), [], []
    env.hub.status_worker = SimpleNamespace(poke=lambda: env.pokes.append(1))
    env.tray = tray_actions.TrayActions(env.hub, ctx.guard, env.main.show, env.main.publish, browse=env.browsed.append)
    env.guard, env.clock = ctx.guard, clock
    return env


def test_tray_mode_starts_a_mode_until_stopped_and_pokes_the_status_worker(tray):
    mode = modes.load(tray.db)[0]
    tray.tray.mode(mode["id"])
    assert modes.active(tray.db, NOW)["mode"]["id"] == mode["id"]
    assert tray.shown[-1][0] == f"{mode['name']} mode on" and tray.pokes
    tray.tray.mode(None)
    assert modes.active(tray.db, NOW) is None and tray.shown[-1][0] == f"{mode['name']} mode off"


def test_tray_cannot_replace_or_stop_a_locked_mode_without_the_challenge(tray):
    first, second = modes.load(tray.db)[:2]
    modes.start(tray.db, first["id"], NOW, NOW + timedelta(hours=2), locked=True)
    ab.save(tray.db, {**ab.settings(tray.db), "phrase": True, "length": 30})
    tray.tray.mode(second["id"])
    assert modes.active(tray.db, NOW)["mode"]["id"] == first["id"]          # still the locked one
    event = tray.main.events[-1]
    assert event[0] == "challenge_open" and event[1]["needs_challenge"] and tray.main.events[0] == "show"
    tray.tray.mode(None)
    assert modes.active(tray.db, NOW)["mode"]["id"] == first["id"]
    ticket = tray.main.events[-1][1]["ticket"]
    phrase = tray.guard.begin(ticket)["data"]["phrase"]
    tray.clock.mono += len(phrase) * 0.05 + 1
    assert tray.guard.submit(ticket, phrase)["data"]["passed"] is True      # passing the challenge stops it
    assert modes.active(tray.db, NOW) is None
    assert tray.shown[-1][0] == f"{first['name']} mode off"


def test_tray_replacing_a_locked_mode_runs_once_the_challenge_is_passed(tray):
    first, second = modes.load(tray.db)[:2]
    modes.start(tray.db, first["id"], NOW, NOW + timedelta(hours=2), locked=True)
    ab.save(tray.db, {**ab.settings(tray.db), "phrase": True, "length": 30})
    tray.tray.mode(second["id"])
    ticket = tray.main.events[-1][1]["ticket"]
    phrase = tray.guard.begin(ticket)["data"]["phrase"]
    tray.clock.mono += len(phrase) * 0.05 + 1
    tray.guard.submit(ticket, phrase)
    assert modes.active(tray.db, NOW)["mode"]["id"] == second["id"]


def test_tray_a_locked_mode_asks_even_without_a_phrase(tray):
    """A locked mode asks even without a phrase (force) - the guard decides, not the tray."""
    first, second = modes.load(tray.db)[:2]
    modes.start(tray.db, first["id"], NOW, NOW + timedelta(hours=2), locked=True)
    tray.tray.mode(second["id"])
    assert tray.main.events[-1][0] == "challenge_open"


def test_tray_mode_that_no_longer_exists_says_so(tray):
    tray.tray.mode("gone")
    assert tray.shown[-1][0] == "That mode doesn't exist any more."


def test_tray_resume_is_free_and_pokes(tray):
    pause.start(tray.db, NOW, 30, silent=False)
    ab.save(tray.db, {**ab.settings(tray.db), "phrase": True, "length": 30})    # locked: resume still works
    tray.tray.resume()
    assert pause.state(tray.db, NOW) is None and tray.pokes


def test_tray_pause_opens_the_pause_card_where_starting_a_pause_asks_for_the_challenge(tray):
    tray.tray.pause()
    assert tray.main.events == ["show", ("show_page", {"page": "antibypass", "focus": "pause"})]
    assert pause.state(tray.db, NOW) is None                                      # nothing was paused by the tray itself


def test_tray_update_uses_the_installer_flow_when_there_is_one(tray, monkeypatch):
    found = {"version": "9.9.9", "url": "https://example.test/r", "installer": "x"}
    monkeypatch.setattr(tray_actions.updates, "available", lambda db: found)
    monkeypatch.setattr(tray_actions.updates, "can_install", lambda f: True)
    tray.tray.update()
    assert tray.main.events == ["show", ("show_page", {"page": "about"})]          # until R17 sets hub.install_update
    installs = []
    tray.hub.install_update = installs.append
    tray.tray.update()
    assert installs == [found]
    monkeypatch.setattr(tray_actions.updates, "can_install", lambda f: False)
    tray.tray.update()
    assert tray.browsed == ["https://example.test/r"]
    monkeypatch.setattr(tray_actions.updates, "available", lambda db: None)
    tray.tray.update()
    assert len(installs) == 1 and len(tray.browsed) == 1


def test_open_page_maps_legacy_names_to_router_ids(tray):
    assert tray_actions.page_id("Screen Time") == "screentime" and tray_actions.page_id("Anti-Bypass") == "antibypass"
    assert tray_actions.page_id("about") == "about" and tray_actions.page_id("Nowhere") is None
    tray.tray.open_page("Screen Time")
    assert tray.main.events == ["show", ("show_page", {"page": "screentime"})]


# ---------------------------------------------------------------- wiring in the agent

def test_attach_wires_everything(env):
    pushes = []
    corner_window = SimpleNamespace(evaluate_js=pushes.append, show=lambda: None, hide=lambda: None,
                                    move=lambda x, y: None, resize=lambda w, h: None)
    main_pub = FakePublisher(env.log)
    win = SimpleNamespace(corner=corner_window, publisher=main_pub)
    ctx = webapi.make_context(env.path, clock=lambda db: NOW)
    hub = workers.Hub(env.path, win=env.win, log_sink=lambda t: None)
    ui, actions, toaster, publisher = reminder_ui.attach(win, hub, ctx, lambda: None)
    try:
        assert ctx.corner is not None and isinstance(ui, reminder_ui.CornerReminderUI)
        assert hub.open_page == actions.open_page
        hub.notify.show("Hello", sticky=False)                      # default format "toast": the (fake) notification path
        ctx.corner.popup("a", "t", "x", [("OK", "ok")])
        publisher.flush()
        assert pushes and pushes[-1].startswith('ld.push([["corner",{"seq":1,"cards":[{"key":"a"')
    finally:
        publisher.stop()


def test_the_pushes_run_within_a_frame_or_two():
    assert reminder_ui.CORNER_PUSH_SEC <= 0.05
