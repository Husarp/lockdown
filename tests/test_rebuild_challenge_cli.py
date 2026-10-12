"""R06: the challenge outside the main page - `--challenge TEXT` (exit code logic), its restricted Api, and the tray
asking for the challenge in the main window. The window itself (WebView2) is Windows-only; everything decided here is
plain Python with a fake `show`."""
import random
import threading
from datetime import datetime

import pytest

import antibypass as ab
import webapi
from resident import challenge_cli, windows
from webapi import core, guard
from webapi.guard import GuardService

NOW = datetime(2026, 9, 20, 19, 0)            # a Sunday evening
SUN_EVENING = [{"days": [6], "start": "18:00", "end": "20:00"}]


class Clock:
    def __init__(self):
        self.now, self.mono = NOW, 1000.0

    def __call__(self, db=None):
        return self.now

    def tick(self):
        return self.mono


@pytest.fixture
def ctx(tmp_path):
    clock = Clock()
    c = webapi.make_context(tmp_path / "t.db", clock=clock)
    c.guard = GuardService(lambda: c.db, clock, monotonic=clock.tick, rng=random.Random(3))
    c.clock = clock
    return c


def turn_on(ctx, **kw):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30, **kw})


def typist(ctx, ticket):
    """A window that shows the challenge, lets a person type the phrase (time passes), and passes it."""
    data = ctx.guard.begin(ticket)["data"]
    typed = data["grid_words"] if data["mode"] == "grid" else data["phrase"]
    ctx.clock.mono += 60
    return ctx.guard.submit(ticket, typed)


class Show:
    """A fake challenge window: records the call and runs `how(ctx, ticket)` as the person would."""

    def __init__(self, ctx, how=None):
        self.ctx, self.how, self.tickets = ctx, how, []

    def __call__(self, ticket):
        self.tickets.append(ticket)
        if self.how:
            self.how(self.ctx, ticket)


# ---------------------------------------------------------------- the words after --challenge

def test_challenge_text():
    assert challenge_cli.challenge_text(["--challenge", "Uninstall Lockdown"]) == "Uninstall Lockdown"
    assert challenge_cli.challenge_text(["--new-ui", "--challenge", " Uninstall Lockdown ", "--x"]) == "Uninstall Lockdown"
    assert challenge_cli.challenge_text(["--challenge"]) == challenge_cli.DEFAULT_TEXT
    assert challenge_cli.challenge_text(["--challenge", "--tray"]) == challenge_cli.DEFAULT_TEXT


# ---------------------------------------------------------------- the exit code

def test_anti_bypass_off_passes_at_once_without_a_window(ctx):
    show = Show(ctx)
    assert challenge_cli.exit_code(ctx, "Uninstall Lockdown", show) == 0
    assert show.tickets == []


def test_typing_the_phrase_exits_0_and_unlocks(ctx):
    turn_on(ctx)
    show = Show(ctx, lambda c, t: typist(c, t))
    assert challenge_cli.exit_code(ctx, "Uninstall Lockdown", show) == 0
    assert len(show.tickets) == 1
    assert ab.unlocked_until(ab.settings(ctx.db), ctx.now())          # the pass is remembered like any other


def test_the_grid_works_too(ctx):
    turn_on(ctx, grid=True)
    assert challenge_cli.exit_code(ctx, "Uninstall Lockdown", Show(ctx, typist)) == 0


def test_the_window_shows_what_the_uninstaller_asked_for(ctx):
    turn_on(ctx)
    seen = []
    challenge_cli.exit_code(ctx, "Uninstall Lockdown", Show(ctx, lambda c, t: seen.append(c.guard.begin(t)["data"])))
    assert seen[0]["changes"] == ["Uninstall Lockdown"] and seen[0]["mode"] == "phrase"


def test_cancel_or_closing_the_window_exits_1(ctx):
    turn_on(ctx)
    assert challenge_cli.exit_code(ctx, "Uninstall Lockdown", Show(ctx)) == 1                 # closed untouched
    assert challenge_cli.exit_code(ctx, "Uninstall Lockdown", Show(ctx, lambda c, t: c.guard.cancel(t))) == 1
    assert not ab.unlocked_until(ab.settings(ctx.db), ctx.now())


def test_a_wrong_phrase_or_a_macro_does_not_pass(ctx):
    turn_on(ctx)

    def wrong(c, t):
        c.guard.begin(t)
        c.clock.mono += 60
        assert not c.guard.submit(t, "nope")["ok"]

    def too_fast(c, t):
        data = c.guard.begin(t)["data"]
        assert c.guard.submit(t, data["phrase"]) == {"ok": False, "error": "Too fast - type it yourself."}

    assert challenge_cli.exit_code(ctx, "x", Show(ctx, wrong)) == 1
    assert challenge_cli.exit_code(ctx, "x", Show(ctx, too_fast)) == 1
    assert not ab.unlocked_until(ab.settings(ctx.db), ctx.now())


def test_a_wait_after_passing_exits_1(ctx):
    turn_on(ctx, wait_min=10)
    assert challenge_cli.exit_code(ctx, "x", Show(ctx, typist)) == 1       # passed, but the change must wait
    assert ab.waiting_until(ab.settings(ctx.db), ctx.now())
    seen = []                                                              # coming back during the wait: no phrase
    challenge_cli.exit_code(ctx, "x", Show(ctx, lambda c, t: seen.append(c.guard.begin(t)["data"]["mode"])))
    assert seen == ["waiting"]


def test_outside_the_allowed_hours_exits_1(ctx):
    turn_on(ctx, phrase=False, hours=True, windows=[{"days": [0], "start": "09:00", "end": "10:00"}])
    seen = []
    code = challenge_cli.exit_code(ctx, "x", Show(ctx, lambda c, t: seen.append(c.guard.begin(t)["data"]["mode"])))
    assert code == 1 and seen == ["closed"]


def test_a_ticket_left_open_is_dropped_when_the_window_closes(ctx):
    turn_on(ctx)
    tickets = []
    challenge_cli.exit_code(ctx, "x", Show(ctx, lambda c, t: tickets.append(t)))
    assert ctx.guard.begin(tickets[0]) == {"ok": False, "error": "This challenge has expired. Start the change again."}


# ---------------------------------------------------------------- the standalone window's Api

def test_standalone_api_has_only_the_challenge_methods(ctx):
    closed = threading.Event()
    api = challenge_cli.standalone_api(ctx, closed.set)
    assert set(dir(api)) == set(challenge_cli.STANDALONE_METHODS) | {"challenge_window_close"}
    for forbidden in ("draft_save", "ui_setting", "antibypass_switch_on", "db", "ctx", "__class__"):
        with pytest.raises(AttributeError):
            getattr(api, forbidden)
    assert not closed.is_set()
    assert api.challenge_window_close(True) == {"ok": True, "data": None}
    assert closed.wait(2)


def test_standalone_api_runs_the_real_challenge(ctx):
    turn_on(ctx)
    api = challenge_cli.standalone_api(ctx, lambda: None)
    done = []
    ticket = ctx.guard.request(["Uninstall Lockdown"], lambda: done.append(1))["data"]["ticket"]
    data = api.challenge_begin(ticket)["data"]
    ctx.clock.mono += 60
    assert api.challenge_submit(ticket, data["phrase"])["data"]["passed"] is True
    assert done == [1]


# ---------------------------------------------------------------- the tray asks for the challenge in the main window

class FakePublisher:
    def __init__(self):
        self.events, self.visible = [], []

    def publish(self, event, payload=None, key=None):
        self.events.append((event, payload))

    def set_visible(self, v):
        self.visible.append(v)


class FakeWindow:
    def __init__(self):
        self.shown = 0

    def show(self):
        self.shown += 1

    def restore(self):
        pass


class FakeWin(windows.Windows):
    def __init__(self):
        super().__init__(main=FakeWindow(), publisher=FakePublisher())
        windows.page_ready(self, "main")         # (its page works: show_main shows at once)


def test_tray_free_runs_the_action_without_showing_anything(ctx):
    win, ran = FakeWin(), []
    assert challenge_cli.ask_in_main(win, ctx, ["Quit Lockdown"], lambda: ran.append(1)) is True
    assert ran == [1] and win.main.shown == 0 and win.publisher.events == []


def test_tray_locked_shows_main_and_pushes_the_challenge(ctx):
    turn_on(ctx)
    win, ran, cancelled = FakeWin(), [], []
    assert challenge_cli.ask_in_main(win, ctx, ["Quit Lockdown"], lambda: ran.append(1),
                                     cancel=lambda: cancelled.append(1)) is False
    assert ran == [] and win.main.shown == 1 and win.publisher.visible == [True]
    event, payload = win.publisher.events[0]
    assert event == "challenge_open" and payload["changes"] == ["Quit Lockdown"] and payload["ticket"]
    # the page passes it: the stored quit runs in Python
    ctx.guard.begin(payload["ticket"])
    ctx.clock.mono += 60
    typed = ctx.guard._open.phrase
    assert ctx.guard.submit(payload["ticket"], typed)["data"]["passed"] is True
    assert ran == [1] and cancelled == []


def test_agent_tray_exit_goes_through_the_guard(ctx, monkeypatch):
    import agent
    from resident import windows
    quits, stopped = [], []

    class Tray:
        def stop(self):
            stopped.append(1)

    monkeypatch.setattr(windows, "quit_agent", lambda win: quits.append(win))
    monkeypatch.setattr(agent, "_log", lambda m: None)
    real_timer = threading.Timer
    monkeypatch.setattr(threading, "Timer", lambda t, f, args=(): real_timer(0, f, args=args))

    turn_on(ctx)
    win = FakeWin()
    agent._tray_exit(win, Tray(), ctx)                                  # locked: the challenge, not the exit
    assert win.main.shown == 1 and win.publisher.events[0][0] == "challenge_open" and not stopped
    assert ctx.db.get_setting(ab.EXITED_KEY, "0") != "1"

    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": False})           # Anti-Bypass off: straight out
    agent._tray_exit(win, Tray(), ctx)
    threading.Event().wait(0.3)
    assert stopped == [1] and quits == [win] and ctx.db.get_setting(ab.EXITED_KEY, "0") == "1"
