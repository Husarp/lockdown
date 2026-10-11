"""R08: the full-screen cover - the bedtime screen and a running strict break (resident/overlay.py, webapi/overlay.py).

The reminder engine is the real one and drives the real Overlay (through a small ReminderUI); the window, the publisher
and the reminder worker are fakes. What can only be proven on Windows: the window really filling every monitor on top
of everything, the covers on the other monitors, taking the keyboard, Alt+F4 doing nothing."""
import random
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import antibypass as ab
import emergency
import reminders
import webapi
from db import Database
from gui import screens
from resident import overlay as overlay_mod
from resident import reminder_ui, windows
from resident.overlay import Overlay, OverlayHost, overlay_background, plan_rects
from webapi import core, guard
from webapi.guard import GuardService

NIGHT = datetime(2026, 10, 8, 23, 5)       # a Thursday night, after a 23:00 bedtime
ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- fakes

class Mono:
    """The steady clock (seconds), set by the test."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Log:
    def __init__(self):
        self.events = []


class FakePublisher:
    def __init__(self, log):
        self.log, self.sent = log, []

    def publish(self, event, payload=None, key=None):
        self.sent.append((event, payload))
        self.log.events.append(("push", payload.get("shown") if isinstance(payload, dict) else None))


class FakeHost:
    def __init__(self, log):
        self.log, self.shown, self.refreshes, self.backgrounds = log, False, 0, []

    def show(self, background):
        self.shown = True
        self.backgrounds.append(background)
        self.log.events.append(("host", "show"))

    def hide(self):
        self.shown = False
        self.log.events.append(("host", "hide"))

    def refresh(self):
        self.refreshes += 1


class Env:
    """A real engine on a temporary database, a real Overlay, a guard whose clock the test moves."""

    def __init__(self, tmp_path):
        self.log = Log()
        self.mono = Mono()
        self.now = NIGHT
        self.path = tmp_path / "t.db"
        self.db = Database(self.path)
        reminders.save(self.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                                      "wake": "07:00", "tiers": [], "repeat_sec": 300,
                                                      "dismiss_wait": 25})
        self.publisher, self.host = FakePublisher(self.log), FakeHost(self.log)
        self.answers = []
        self.overlay = Overlay(self.publisher, self.host, self._answer, now=lambda: self.now,
                               wait_for=self._wait_for, clock=self.mono, background=lambda: "#191817", log=print)
        self.ui = EngineUI(self.overlay)
        self.engine = reminders.Engine(self.db, self.ui)
        self.ctx = webapi.make_context(self.path, clock=lambda db=None: self.now)
        self.ctx.guard = GuardService(lambda: self.ctx.db, lambda db=None: self.now, monotonic=self.mono,
                                      rng=random.Random(7))
        self.ctx.overlay = self.overlay
        self.api = core.build_api(self.ctx)

    def _wait_for(self, key):
        return reminders.dismiss_wait(reminders.load(self.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)) \
            if key == "sleep" else 0

    def _answer(self, key, action):
        """The reminder worker would run this on its own thread; here it runs at once."""
        self.log.events.append(("answer", key, action))
        self.answers.append((key, action))
        self.engine.answer(key, action)

    def tick(self, seconds=0, quiet=False):
        self.now += timedelta(seconds=seconds)
        self.engine.tick(self.now, 0, False, quiet=quiet)

    def lock(self, **kw):
        ab.save(self.db, {**ab.settings(self.db), "phrase": True, "length": 30, **kw})

    def pass_challenge(self, ticket):
        data = self.ctx.guard.begin(ticket)["data"]
        self.mono.t += len(data["phrase"]) * guard.MIN_MS_PER_CHAR / 1000 + 1
        return self.ctx.guard.submit(ticket, data["phrase"])

    def call(self, name, *args):
        return getattr(self.api, name)(*args)

    def kinds(self):
        return {k["id"]: left for k, left, _a, _r in emergency.left_by_tier(self.db, self.now)}


class EngineUI:
    """The engine's ReminderUI as the agent builds it, minus the corner: the cover goes to the Overlay."""

    def __init__(self, overlay):
        self.overlay_ctl, self.toasts, self.closed = overlay, [], []

    def popup(self, *args):
        pass

    def overlay(self, key, title, text, until, buttons):
        self.overlay_ctl.show(key, title, text, until, buttons)

    def close(self, key):
        self.closed.append(key)
        self.overlay_ctl.close(key)

    def held(self, *args):
        pass

    def toast(self, text):
        self.toasts.append(text)

    def break_start(self, until):
        pass

    def break_end(self):
        pass

    def start_mode(self, *args):
        pass


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def up(env, wait=25):
    """The bedtime screen comes up (the engine's first tick of the night)."""
    reminders.save(env.db, reminders.SLEEP_KEY, {**reminders.load(env.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP),
                                                 "dismiss_wait": wait})
    env.tick()
    assert env.overlay.key() == "sleep"
    return env.overlay.payload()


# ---------------------------------------------------------------- what the cover shows

def test_the_bedtime_screen_has_the_engines_text_buttons_and_the_wait(env):
    p = up(env)
    assert p["shown"] and p["kind"] == "sleep" and p["title"] == "Time for bed"
    assert "23:05" in p["text"]
    assert [(b["label"], b["action"]) for b in p["buttons"]][:2] == [("Dismiss", "dismiss"), ("Disable alerts", "disable")]
    assert p["buttons"][2]["action"] == "emergency" and p["buttons"][2]["label"].startswith("Emergency (")
    assert p["wait_ms"] == 25_000 and p["until_ms"] is None
    assert p["clock_sec"] == 23 * 3600 + 5 * 60                       # the big time is the trusted time
    assert env.host.shown and env.host.backgrounds == ["#191817"]
    assert env.publisher.sent[-1][0] == "overlay" and env.publisher.sent[-1][1]["seq"] == p["seq"]


def test_without_a_wait_dismiss_works_at_once(env):
    up(env, wait=0)
    assert env.call("overlay_answer", "dismiss")["data"] == {"done": True, "key": "sleep"}
    assert env.answers == [("sleep", "dismiss")]


def test_an_emergency_less_screen_has_no_emergency_button(env):
    env.db.set_setting("emergency.enabled", "0")
    p = up(env)
    assert [b["action"] for b in p["buttons"]] == ["dismiss", "disable"]


def test_the_wait_counts_down_in_ms_left_and_restarts_each_time_the_screen_comes_up(env):
    up(env)
    env.mono.t += 10
    assert env.overlay.payload()["wait_ms"] == 15_000
    env.mono.t += 20
    assert env.overlay.payload()["wait_ms"] == 0
    env.overlay.show("sleep", "Time for bed", "again", None, [("Dismiss", "dismiss")])       # it came back
    assert env.overlay.payload()["wait_ms"] == 25_000


def test_every_change_is_numbered_and_pushed_whole(env):
    up(env)
    first = env.publisher.sent[-1][1]["seq"]
    env.overlay.close("sleep")
    event, payload = env.publisher.sent[-1]
    assert event == "overlay" and payload == {"seq": first + 1, "shown": False}


# ---------------------------------------------------------------- Dismiss and its wait

def test_an_early_dismiss_is_refused_and_changes_nothing(env):
    up(env)
    env.mono.t += 5
    reply = env.call("overlay_answer", "dismiss")["data"]
    assert reply == {"early": True, "wait_ms": 20_000}
    assert env.answers == [] and env.overlay.shown and env.host.shown


def test_dismiss_after_the_wait_takes_the_screen_down_first_then_the_engine_hears_it(env):
    up(env)
    env.mono.t += 25.1
    assert env.call("overlay_answer", "dismiss")["data"] == {"done": True, "key": "sleep"}
    events = env.log.events
    assert events.index(("host", "hide")) < events.index(("answer", "sleep", "dismiss"))
    assert not env.overlay.shown and not env.host.shown
    assert list(env.engine.sleep_next.values()) == [env.now + timedelta(seconds=300)]    # it comes back on its interval
    assert not env.call("overlay_answer", "dismiss")["ok"]                              # a second press finds it gone


def test_the_same_answer_works_only_once(env):
    up(env, wait=0)
    assert env.call("overlay_answer", "dismiss")["ok"]
    again = env.call("overlay_answer", "dismiss")
    assert not again["ok"] and "not on screen" in again["error"]
    assert env.answers == [("sleep", "dismiss")]


def test_backing_out_while_the_wait_runs_keeps_the_screen_and_starts_the_wait_over(env):
    up(env)
    env.mono.t += 10
    assert env.call("overlay_answer", "cancel")["data"] == {"back": True}
    assert env.overlay.shown and env.answers == []
    assert env.overlay.payload()["wait_ms"] == 25_000                                  # afresh, as when it comes up


def test_cancel_after_the_wait_is_a_dismiss(env):
    up(env)
    env.mono.t += 26
    assert env.call("overlay_answer", "cancel")["data"] == {"done": True}
    assert env.answers == [("sleep", "dismiss")]


def test_a_cancelled_challenge_during_the_wait_starts_the_wait_over_but_never_dismisses(env):
    env.lock()
    up(env)
    env.mono.t += 12
    ticket = env.call("overlay_disable")["data"]["ticket"]
    env.mono.t += 5
    env.ctx.guard.cancel(ticket)
    assert env.overlay.shown and env.answers == [] and env.overlay.payload()["wait_ms"] == 25_000


def test_a_cancelled_challenge_after_the_wait_leaves_the_screen_alone(env):
    env.lock()
    up(env)
    ticket = env.call("overlay_disable")["data"]["ticket"]
    env.mono.t += 40
    env.ctx.guard.cancel(ticket)
    assert env.overlay.shown and env.answers == [] and env.overlay.payload()["wait_ms"] == 0


# ---------------------------------------------------------------- Disable alerts (behind the challenge)

def test_disable_alerts_asks_for_the_challenge_first_then_offers_the_choice(env):
    env.lock()
    up(env)
    reply = env.call("overlay_disable")["data"]
    assert reply["needs_challenge"] and reply["changes"] == ["Turn off tonight's bedtime alerts"]
    assert env.answers == []
    done = env.pass_challenge(reply["ticket"])["data"]
    assert done["passed"] and done["result"] == {"choose": True}
    assert env.answers == [] and env.overlay.shown                                   # only a choice so far


def test_off_tonight_while_locked_is_a_ticket_and_is_not_applied(env):
    env.lock()
    up(env)
    before = dict(env.engine.sleep_next)
    reply = env.call("overlay_answer", "off_tonight")["data"]
    assert reply["needs_challenge"] and reply["ticket"]
    assert env.answers == [] and env.overlay.shown and env.engine.sleep_next == before


def test_off_tonight_after_a_passed_challenge_is_applied_and_ends_the_night(env):
    env.lock()
    up(env)
    ticket = env.call("overlay_answer", "off_tonight")["data"]["ticket"]
    done = env.pass_challenge(ticket)["data"]
    assert done["passed"] and done["result"] == {"done": True}
    assert env.answers == [("sleep", "off_tonight")]
    assert not env.overlay.shown
    wake = datetime(2026, 10, 9, 7, 0)
    assert list(env.engine.sleep_next.values()) == [wake]                            # no more bedtime screens tonight


def test_the_choice_after_a_passed_challenge_needs_no_second_one(env):
    env.lock()
    up(env)
    env.pass_challenge(env.call("overlay_disable")["data"]["ticket"])
    reply = env.call("overlay_answer", "snooze:15")["data"]
    assert reply == {"done": True}
    assert env.answers == [("sleep", "snooze:15")]
    assert list(env.engine.sleep_next.values()) == [env.now + timedelta(minutes=15)]


def test_when_anti_bypass_is_free_there_is_no_challenge(env):
    up(env)
    assert env.call("overlay_disable")["data"] == {"choose": True}
    assert env.call("overlay_answer", "off_tonight")["data"] == {"done": True}
    assert env.answers == [("sleep", "off_tonight")]


def test_the_challenge_comes_back_when_the_unlock_has_run_out(env):
    env.lock()
    up(env)
    env.pass_challenge(env.call("overlay_disable")["data"]["ticket"])
    env.now += timedelta(minutes=ab.UNLOCK_MIN + 1)                                  # the choice was left open too long
    assert env.call("overlay_answer", "off_tonight")["data"]["needs_challenge"]
    assert env.answers == []


def test_a_challenge_passed_for_a_screen_that_has_gone_does_nothing(env):
    env.lock()
    up(env)
    ticket = env.call("overlay_answer", "off_tonight")["data"]["ticket"]
    env.overlay.close("sleep")                                                       # the night ended meanwhile
    phrase = env.call("challenge_begin", ticket)["data"]["phrase"]
    env.mono.t += 60
    result = env.call("challenge_submit", ticket, phrase)
    assert not result["ok"] and "not on screen" in result["error"]
    assert env.answers == []


def test_a_screen_that_came_back_is_not_the_one_the_old_challenge_was_for(env):
    env.lock()
    up(env)
    ticket = env.call("overlay_answer", "off_tonight")["data"]["ticket"]
    env.overlay.show("sleep", "Time for bed", "again", None, [("Dismiss", "dismiss"), ("Disable alerts", "disable")])
    phrase = env.call("challenge_begin", ticket)["data"]["phrase"]
    env.mono.t += 60
    assert not env.call("challenge_submit", ticket, phrase)["ok"]
    assert env.answers == []


@pytest.mark.parametrize("action", ["snooze:20", "snooze", "off", "emergency", "", None, 5, ["dismiss"], "Dismiss"])
def test_nothing_but_the_known_buttons_is_accepted(env, action):
    env.lock()
    up(env, wait=0)
    reply = env.call("overlay_answer", action)
    assert not reply["ok"]
    assert env.answers == [] and env.overlay.shown


def test_the_page_cannot_use_a_button_the_screen_does_not_have(env):
    env.db.set_setting("emergency.enabled", "0")
    env.overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])      # no Disable alerts, no Emergency
    assert not env.call("overlay_answer", "off_tonight")["ok"]
    assert not env.call("overlay_disable")["ok"]
    assert not env.call("overlay_emergency", "t1")["ok"]
    assert env.answers == []


def test_calls_with_no_cover_up_are_refused(env):
    for name, args in [("overlay_answer", ("dismiss",)), ("overlay_answer", ("off_tonight",)), ("overlay_disable", ()),
                       ("overlay_emergency", ("t1",))]:
        reply = env.call(name, *args)
        assert not reply["ok"] and "not on screen" in reply["error"], name
    assert env.answers == []


# ---------------------------------------------------------------- Emergency

TIERS = [{"id": "quick", "uses": 1, "per": "day", "minutes": 5}, {"id": "work", "uses": 2, "per": "week", "minutes": 30}]


def test_the_kinds_show_uses_left_and_grey_out_spent_ones(env):
    emergency.save_tiers(env.db, TIERS)
    emergency.unlock(env.db, [], env.now - timedelta(hours=3), alerts=True, tier="quick")      # today's quick one is spent
    up(env)
    data = env.call("overlay_emergency_kinds")["data"]
    assert data["usable"]
    quick, work = data["kinds"]
    assert (quick["id"], quick["minutes"], quick["left"], quick["allowed"], quick["per_text"]) == ("quick", 5, 0, 1, "today")
    assert re.fullmatch(r"\w{3} \d{1,2} \w{3} \d\d:\d\d", quick["resets_text"])
    assert (work["id"], work["minutes"], work["left"], work["allowed"], work["per_text"]) == ("work", 30, 2, 2, "this week")


def test_emergency_off_lists_nothing(env):
    env.db.set_setting("emergency.enabled", "0")
    assert env.call("overlay_emergency_kinds")["data"] == {"usable": False, "kinds": []}


def test_an_emergency_spends_exactly_one_use_of_the_chosen_kind_and_pauses_the_alerts(env):
    emergency.save_tiers(env.db, TIERS)
    up(env)
    before = env.kinds()
    reply = env.call("overlay_emergency", "work")["data"]
    assert reply == {"done": True, "minutes": 30, "text": "Bedtime and break alerts paused for 30 min."}
    after = env.kinds()
    assert after == {"quick": before["quick"], "work": before["work"] - 1}
    assert env.answers == [("sleep", "emergency:work")]
    assert not env.overlay.shown
    assert env.engine.paused == env.now + timedelta(minutes=30)
    assert "Emergency: bedtime and break alerts paused until 23:35." in env.ui.toasts
    assert not env.call("overlay_emergency", "work")["ok"]                              # a second click finds it gone
    assert env.kinds() == after


def test_a_kind_with_none_left_is_refused_and_spends_nothing(env):
    emergency.save_tiers(env.db, TIERS)
    emergency.unlock(env.db, [], env.now - timedelta(hours=3), alerts=True, tier="quick")
    up(env)
    reply = env.call("overlay_emergency", "quick")
    assert not reply["ok"] and "No 5-min emergency unlocks left today" in reply["error"]
    assert env.kinds() == {"quick": 0, "work": 2}
    assert env.answers == [] and env.overlay.shown


def test_an_unknown_kind_or_a_bad_one_is_refused(env):
    emergency.save_tiers(env.db, TIERS)
    up(env)
    for bad in ("nope", "", None, 7, ["work"], "x" * 80):
        assert not env.call("overlay_emergency", bad)["ok"], bad
    assert env.kinds() == {"quick": 1, "work": 2} and env.answers == []


def test_emergency_turned_off_in_settings_is_refused_even_with_a_stale_button(env):
    up(env)                                                                             # the screen has Emergency ...
    env.db.set_setting("emergency.enabled", "0")                                        # ... and then it was turned off
    reply = env.call("overlay_emergency", "t1")
    assert not reply["ok"] and "turned off" in reply["error"]
    assert env.answers == []


def test_emergency_needs_no_challenge_but_waits_for_nothing(env):
    env.lock()
    up(env)                                                                             # the Dismiss wait is still running
    assert env.call("overlay_emergency", "t1")["data"]["done"]
    assert not env.overlay.shown


def test_backing_out_of_the_emergency_list_during_the_wait_brings_the_screen_back_fresh(env):
    up(env)
    env.mono.t += 9
    assert env.call("overlay_emergency_kinds")["ok"]                                    # (the list was opened)
    assert env.call("overlay_answer", "cancel")["data"] == {"back": True}
    assert env.overlay.payload()["wait_ms"] == 25_000 and env.answers == [] and env.kinds()["t1"] == 3


# ---------------------------------------------------------------- the engine closes it / DND / mute

def test_do_not_disturb_or_a_muting_mode_holds_the_screen_back_until_it_ends(env):
    env.tick(quiet=True)
    assert not env.overlay.shown and not env.host.shown
    env.tick(5, quiet=False)
    assert env.overlay.shown


def test_the_engine_taking_the_screen_down_hides_the_window(env):
    up(env)
    env.now = datetime(2026, 10, 9, 7, 30)                                              # morning: the night is over
    env.engine.tick(env.now, 0, False)
    assert not env.overlay.shown and "sleep" in env.ui.closed and not env.host.shown


def test_an_emergency_pause_started_elsewhere_takes_the_screen_down(env):
    up(env)
    emergency.unlock(env.db, [], env.now, alerts=True, tier="t1")
    env.tick(1)
    assert not env.overlay.shown


def test_closing_another_key_leaves_the_screen_alone(env):
    up(env)
    assert env.overlay.close("break-run") is False and env.overlay.shown


# ---------------------------------------------------------------- the strict break

BREAK_END = NIGHT + timedelta(minutes=5)


def break_cover(env, buttons=()):
    env.overlay.show("break-run", "Break time", "Look away.", BREAK_END, list(buttons))
    return env.overlay.payload()


def test_a_running_break_shows_a_countdown_to_its_end_and_no_dismiss_wait(env):
    p = break_cover(env)
    assert p["kind"] == "break" and p["title"] == "Break time" and p["until_ms"] == 300_000
    assert p["wait_ms"] == 0 and p["buttons"] == [] and "clock_sec" not in p
    env.mono.t += 60
    assert env.overlay.payload()["until_ms"] == 240_000


def test_a_break_cover_cannot_be_dismissed_or_snoozed_or_turned_off(env):
    env.lock()
    break_cover(env)
    for action in ("dismiss", "off_tonight", "snooze:15"):
        assert not env.call("overlay_answer", action)["ok"], action
    assert not env.call("overlay_disable")["ok"]
    assert env.call("overlay_answer", "cancel")["data"] == {"back": True}                # nothing to dismiss: it goes on
    assert env.overlay.shown and env.answers == []


def test_a_break_goes_in_front_of_the_bedtime_screen_which_comes_back_after_it(env):
    up(env)
    env.mono.t += 30                                                                    # (its wait is over)
    break_cover(env, [("Emergency (3 left)", "emergency")])
    assert env.overlay.key() == "break-run"
    env.overlay.close("break-run")
    assert env.overlay.key() == "sleep" and env.host.shown
    assert env.overlay.payload()["wait_ms"] == 25_000                                   # it came up again: a fresh wait
    env.mono.t += 26
    assert env.call("overlay_answer", "dismiss")["data"]["done"]
    assert env.answers == [("sleep", "dismiss")]


def test_a_bedtime_screen_that_falls_due_during_a_break_waits_behind_it(env):
    break_cover(env)
    token = env.overlay.token()
    env.overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss"), ("Disable alerts", "disable")])
    assert env.overlay.key() == "break-run" and env.overlay.token() == token            # the break cover is untouched
    env.overlay.close("break-run")
    assert env.overlay.key() == "sleep"


def test_the_engine_closing_the_waiting_bedtime_screen_leaves_nothing_behind_the_break(env):
    up(env)
    break_cover(env)
    env.overlay.close("sleep")
    env.overlay.close("break-run")
    assert not env.overlay.shown and not env.host.shown


def test_an_emergency_on_the_break_cover_takes_the_bedtime_screen_behind_it_too(env):
    up(env)
    break_cover(env, [("Emergency (3 left)", "emergency")])
    env.engine.now = env.now
    assert env.call("overlay_emergency", "t1")["data"]["done"]
    assert not env.overlay.shown and not env.host.shown                                 # no flash of the bedtime screen


def test_the_break_cover_goes_when_the_engine_ends_the_break(env):
    break_cover(env)
    env.overlay.close("break-run")
    assert not env.overlay.shown and not env.host.shown


def test_a_lost_break_end_message_cannot_keep_the_cover_up(env, monkeypatch):
    monkeypatch.setattr(overlay_mod, "BREAK_FAILSAFE_SEC", 0.05)
    env.overlay.show("break-run", "Break time", "x", env.now + timedelta(seconds=0.1), [])
    deadline = time.time() + 3
    while env.overlay.shown and time.time() < deadline:
        time.sleep(0.02)
    assert not env.overlay.shown and not env.host.shown


def test_the_failsafe_is_taken_back_when_the_cover_goes_normally(env):
    break_cover(env)
    timer = env.overlay._failsafe
    env.overlay.close("break-run")
    assert timer is not None and env.overlay._failsafe is None


def test_emergency_on_a_break_cover_pauses_the_alerts_and_ends_the_break(env):
    reminders.save(env.db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "strict": True})
    env.engine.now = env.now                                                           # (as after the engine's last tick)
    env.engine.break_until = BREAK_END
    break_cover(env, [("Emergency (3 left)", "emergency")])
    assert env.call("overlay_emergency", "t1")["data"]["done"]
    assert env.answers == [("sleep", "emergency:t1")]                                  # the engine hears every emergency as "sleep"
    assert env.kinds()["t1"] == 2 and not env.overlay.shown
    ended = []
    env.ui.break_end = lambda: ended.append(1)
    env.tick(1)                                                                        # the engine's next tick ends the break
    assert env.engine.break_until is None and ended == [1]


# ---------------------------------------------------------------- the ReminderUI: the strict break's cover

class Handler:
    def __init__(self):
        self.calls = []

    def show(self, *args):
        self.calls.append(("show", *args))

    def close(self, key):
        self.calls.append(("close", key))


def reminder_ui_env(tmp_path, quiet=False):
    db = Database(tmp_path / "r.db")
    hub = SimpleNamespace(snapshot=None, notify=lambda *a, **k: None, win=SimpleNamespace(minimize_all=lambda: 1))
    corner = SimpleNamespace(popup_sink=None, remove=lambda key: None)
    ui = reminder_ui.CornerReminderUI(hub, corner, overlay_handler=Handler())
    ui.engine = SimpleNamespace(now=NIGHT, quiet=quiet)
    ui.worker = SimpleNamespace(db=db)
    return ui, db


def test_a_strict_break_covers_the_screen_until_it_ends_with_emergency_while_uses_are_left(tmp_path):
    ui, _db = reminder_ui_env(tmp_path)
    ui.break_start(BREAK_END)
    key, title, text, until, buttons = ui.overlay_handler.calls[-1][1:]
    assert (key, title, until) == ("break-run", "Break time", BREAK_END) and text == reminder_ui.BREAK_COVER_TEXT
    assert buttons == [("Emergency (3 left)", "emergency")]
    ui.break_end()
    assert ui.overlay_handler.calls[-1] == ("close", "break-run")


def test_no_emergency_button_on_the_break_cover_when_none_is_left(tmp_path):
    ui, db = reminder_ui_env(tmp_path)
    db.set_setting("emergency.enabled", "0")
    ui.break_start(BREAK_END)
    assert ui.overlay_handler.calls[-1][-1] == []


def test_a_break_under_do_not_disturb_still_minimises_but_has_no_cover(tmp_path):
    ui, _db = reminder_ui_env(tmp_path, quiet=True)
    minimised = []
    ui.hub.win.minimize_all = lambda: minimised.append(1)
    ui.break_start(BREAK_END)
    assert minimised == [1] and ui.overlay_handler.calls == []


def test_with_no_overlay_handler_the_break_is_as_before(tmp_path):
    ui, _db = reminder_ui_env(tmp_path)
    ui.overlay_handler = None
    ui.break_start(BREAK_END)
    ui.break_end()


def test_the_bedtime_overlay_goes_through_the_handler_and_is_closed_by_key(tmp_path):
    ui, _db = reminder_ui_env(tmp_path)
    ui.overlay("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
    ui.close("sleep")
    assert [c[0] for c in ui.overlay_handler.calls] == ["show", "close"]


# ---------------------------------------------------------------- the monitors and the colour

def mon(rect, primary=False):
    return screens.Monitor(rect, primary)


@pytest.mark.parametrize("found, main_rect, other_rects", [        # (the layouts tests/test_overlay_screens.py plans)
    ([screens.Monitor((0, 0, 2880, 1800), True, 1.5), screens.Monitor((2880, 200, 4800, 1280), False, 1.0)],
     (0, 0, 2880, 1800), [(2880, 200, 4800, 1280)]),
    ([screens.Monitor((-1920, 0, 0, 1080), False, 1.0), screens.Monitor((0, 0, 2560, 1440), True, 1.25)],
     (0, 0, 2560, 1440), [(-1920, 0, 0, 1080)]),
    ([screens.Monitor((-2560, -360, 0, 1080), False, 1.5), screens.Monitor((0, 0, 1920, 1080), True, 1.0)],
     (0, 0, 1920, 1080), [(-2560, -360, 0, 1080)]),
    ([screens.Monitor((0, 0, 1920, 1080), True, 1.0), screens.Monitor((0, -1440, 2560, 0), False, 1.5)],
     (0, 0, 1920, 1080), [(0, -1440, 2560, 0)]),
    ([screens.Monitor((0, 0, 3840, 2160), True, 1.5)], (0, 0, 3840, 2160), []),
])
def test_one_main_screen_and_a_cover_for_every_other_monitor(found, main_rect, other_rects):
    assert plan_rects(found) == (main_rect, other_rects)


def test_three_monitors_give_two_covers():
    found = [mon((0, 0, 1920, 1080), True), mon((1920, 0, 3840, 1080)), mon((-1280, 0, 0, 1024))]
    assert plan_rects(found) == ((0, 0, 1920, 1080), [(1920, 0, 3840, 1080), (-1280, 0, 0, 1024)])


def test_a_single_monitor_has_no_covers_and_no_monitors_falls_back():
    assert plan_rects([mon((0, 0, 2560, 1440), True)]) == ((0, 0, 2560, 1440), [])
    assert plan_rects([]) == (overlay_mod.DEFAULT_RECT, [])
    assert plan_rects([], (0, 0, 800, 600)) == ((0, 0, 800, 600), [])


def test_mirrored_displays_are_one_cover_and_no_primary_flag_takes_the_origin_one():
    found = [mon((0, 0, 1920, 1080)), mon((0, 0, 1920, 1080)), mon((1920, 0, 3840, 1080))]
    assert plan_rects(found) == ((0, 0, 1920, 1080), [(1920, 0, 3840, 1080)])


def test_the_cover_colour_is_the_pages_background_in_every_theme():
    tokens = (ROOT / "src/ui/css/tokens.css").read_text(encoding="utf-8")

    def bg(selector_start):
        block = tokens[tokens.index(selector_start):]
        return re.search(r"--bg:\s*(#[0-9A-Fa-f]{6})", block).group(1).upper()
    assert overlay_background("light") == bg(":root, [data-theme=\"light\"]")
    assert overlay_background("dark") == bg("[data-theme=\"dark\"], [data-theme=\"amoled\"]")
    assert overlay_background("amoled") == bg("AMOLED: the dark tokens")
    assert overlay_background("system", system_is_light=True) == overlay_background("light")
    assert overlay_background("system") == overlay_background("dark")
    assert overlay_background("nonsense") == overlay_background("dark")
    assert windows.OVERLAY_BG == overlay_background("dark")


def test_the_saved_theme_picks_the_cover_colour(monkeypatch):
    import ui_theme
    monkeypatch.setattr(ui_theme, "saved_theme", lambda: "light")
    assert overlay_mod.saved_background() == overlay_background("light")
    monkeypatch.setattr(ui_theme, "saved_theme", lambda: "system")
    monkeypatch.setattr(ui_theme, "system_is_light", lambda: True)
    assert overlay_mod.saved_background() == overlay_background("light")
    monkeypatch.setattr(ui_theme, "system_is_light", lambda: False)
    assert overlay_mod.saved_background() == overlay_background("dark")


# ---------------------------------------------------------------- the window glue (off Windows: pywebview's own calls)

class FakeWindow:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def resize(self, w, h):
        self.calls.append(("resize", w, h))


class Monitors:
    def __init__(self, found):
        self.found = found

    def __call__(self):
        return self.found


def eventually(check, seconds=3):
    deadline = time.time() + seconds
    while not check() and time.time() < deadline:
        time.sleep(0.02)
    return check()


def host_env(found):
    window, made, destroyed = FakeWindow(), [], []

    def make_cover(title, background):
        cover = SimpleNamespace(title=title, background=background, destroy=lambda: destroyed.append(title))
        made.append(cover)
        return cover
    monitors = Monitors(found)
    return OverlayHost(window, monitors=monitors, make_cover=make_cover), window, made, destroyed, monitors


TWO = [mon((0, 0, 1920, 1080), True), mon((1920, 0, 3840, 1080))]


def test_the_host_fills_the_primary_monitor_and_covers_the_other_in_the_same_colour():
    host, window, made, destroyed, _m = host_env(TWO)
    host.show("#101010")
    assert "show" in window.calls and ("move", 0, 0) in window.calls and ("resize", 1920, 1080) in window.calls
    assert eventually(lambda: len(made) == 1)
    assert made[0].background == "#101010" and made[0].title == "Lockdown overlay cover 1"
    host.hide()
    assert eventually(lambda: destroyed == ["Lockdown overlay cover 1"]) and window.calls[-1] == "hide"


def test_a_monitor_unplugged_or_plugged_in_while_it_is_up_is_followed():
    host, window, made, destroyed, monitors = host_env(TWO)
    host.show("#101010")
    assert eventually(lambda: len(made) == 1)
    monitors.found = [mon((0, 0, 1920, 1080), True)]                                   # the second monitor is unplugged
    host.refresh()
    assert eventually(lambda: destroyed == ["Lockdown overlay cover 1"])
    monitors.found = TWO + [mon((3840, 0, 5760, 1080))]                                # two others appear
    host.refresh()
    assert eventually(lambda: len(made) == 3)
    assert {c.title for c in made[1:]} == {"Lockdown overlay cover 1", "Lockdown overlay cover 2"}


def test_a_refresh_that_changes_nothing_makes_no_new_cover():
    host, _w, made, _d, _m = host_env(TWO)
    host.show("#101010")
    assert eventually(lambda: len(made) == 1)
    host.refresh()
    time.sleep(0.2)
    assert len(made) == 1


def test_covers_hidden_before_they_were_made_do_not_stay_up():
    gate, made, destroyed = threading.Event(), [], []

    def slow_cover(title, background):
        gate.wait(3)
        cover = SimpleNamespace(destroy=lambda: destroyed.append(title))
        made.append(cover)
        return cover
    host = OverlayHost(FakeWindow(), monitors=Monitors(TWO), make_cover=slow_cover)
    host.show("#101010")
    host.hide()                                                                         # the bedtime screen is gone already
    gate.set()
    assert eventually(lambda: len(destroyed) == 1 and not host._covers)


def test_quitting_the_agent_takes_the_covers_down_so_the_gui_loop_can_end():
    host, window, made, destroyed, _m = host_env(TWO)
    host.show("#101010")
    assert eventually(lambda: len(made) == 1)
    win = windows.Windows(main=window, corner=window, overlay=window, overlay_ctl=SimpleNamespace(host=host))
    window.destroy = lambda: None
    windows.quit_agent(win)
    assert destroyed == ["Lockdown overlay cover 1"] and win.quitting
    assert host.closing() is False                                                      # (back to ignoring a stray Alt+F4)


def test_alt_f4_on_a_cover_is_ignored_unless_the_host_itself_is_closing_it():
    host, *_ = host_env(TWO)
    assert host.closing() is False


def test_a_failing_window_never_stops_a_reminder(tmp_path):
    log, errors = Log(), []
    host = FakeHost(log)
    host.show = lambda background: (_ for _ in ()).throw(RuntimeError("no window"))
    overlay = Overlay(None, host, lambda k, a: None, background=lambda: "#000000", log=errors.append)
    overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
    assert overlay.shown and errors and "no window" in errors[0]


def test_while_the_cover_is_up_the_monitors_are_checked_now_and_then(env, monkeypatch):
    monkeypatch.setattr(overlay_mod, "GUARD_SEC", 0.02)
    env.overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
    assert eventually(lambda: env.host.refreshes >= 2)
    env.overlay.close("sleep")
    time.sleep(0.1)
    seen = env.host.refreshes
    time.sleep(0.15)
    assert env.host.refreshes == seen                                                   # nothing runs once it is down


# ---------------------------------------------------------------- the challenge is never hidden behind the cover

class FakePub:
    def __init__(self):
        self.sent = []

    def publish(self, event, payload=None, key=None):
        self.sent.append((event, payload))

    def set_visible(self, visible):
        pass


def test_a_challenge_asked_for_by_the_tray_opens_on_the_cover_while_it_is_up(env):
    main = SimpleNamespace(shown=0, show=lambda: None, restore=lambda: None)
    win = SimpleNamespace(main=main, publisher=FakePub(), overlay_ctl=env.overlay, overlay_publisher=FakePub())
    up(env)
    windows.open_challenge(win, {"needs_challenge": True, "ticket": "T"})
    assert win.overlay_publisher.sent == [("challenge_open", {"needs_challenge": True, "ticket": "T"})]
    assert win.publisher.sent == []                                                      # not in the main window behind it
    env.overlay.close("sleep")
    shown = []
    win.main = SimpleNamespace(show=lambda: shown.append(1), restore=lambda: None)
    windows.open_challenge(win, {"needs_challenge": True, "ticket": "U"})
    assert win.publisher.sent == [("challenge_open", {"needs_challenge": True, "ticket": "U"})] and shown == [1]


def test_the_overlay_window_ignores_the_close_button_but_not_the_agent_quitting(monkeypatch):
    created = []

    class Events:
        def __init__(self):
            self.closing, self.minimized, self.restored = HandlerList(), HandlerList(), HandlerList()

    class HandlerList(list):
        def __iadd__(self, fn):
            self.append(fn)
            return self

    def create_window(title, url=None, **kw):
        created.append((title, kw))
        return SimpleNamespace(events=Events())

    import sys
    import types
    fake = types.ModuleType("webview")
    fake.create_window = create_window
    monkeypatch.setitem(sys.modules, "webview", fake)
    win = windows.create("http://x", "#000000", None, "#191817")
    overlay_kw = dict(created)["Lockdown overlay"]
    assert overlay_kw["frameless"] and overlay_kw["on_top"] and overlay_kw["hidden"] and not overlay_kw.get("fullscreen")
    assert overlay_kw["background_color"] == "#191817"
    (closing,) = win.overlay.events.closing
    assert closing() is False                                                            # Alt+F4: ignored
    win.quitting = True
    assert closing() is True                                                             # the agent really quitting: allowed


# ---------------------------------------------------------------- wiring into the agent

def test_attach_wires_the_cover_the_wait_the_clock_and_the_answers(tmp_path):
    db = Database(tmp_path / "w.db")
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "dismiss_wait": 40})
    sent, answered, poked = [], [], []
    overlay_window = SimpleNamespace(evaluate_js=lambda script: sent.append(script), show=lambda: None, hide=lambda: None)
    win = windows.Windows(overlay=overlay_window)
    snap = SimpleNamespace(clock_offset=3600.0)
    hub = SimpleNamespace(snapshot=snap, clock=SimpleNamespace(now_with=lambda off: NIGHT + timedelta(seconds=off)))
    ui = SimpleNamespace(worker=SimpleNamespace(db=db, answer=lambda k, a: answered.append((k, a)),
                                                poke=lambda: poked.append(1)), overlay_handler=None)
    ctx = SimpleNamespace()
    publisher = overlay_mod.attach(win, hub, ctx, ui)
    try:
        assert ctx.overlay is ui.overlay_handler is win.overlay_ctl and win.overlay_publisher is publisher
        ctx.overlay.host = None                                                          # (no real window here)
        ctx.overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
        p = ctx.overlay.payload()
        assert p["wait_ms"] == 40_000 and p["clock_sec"] == 5 * 60                      # 23:05 + the 1 h offset = 00:05
        ctx.overlay.show("break-run", "Break time", "x", NIGHT + timedelta(seconds=3605), [])
        assert ctx.overlay.payload()["wait_ms"] == 0                                     # only the bedtime screen waits
        assert 0 < ctx.overlay.payload()["until_ms"] <= 5000                             # (the trusted time carries the offset)
        ctx.overlay.close("break-run")
        ctx.overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
        ctx.overlay.accept("dismiss", ctx.overlay.token())
        assert answered == [("sleep", "dismiss")] and poked == [1]                       # the worker ticks again at once
        publisher.flush()
        assert any("ld.push" in s and '"overlay"' in s for s in sent)
    finally:
        publisher.stop()
