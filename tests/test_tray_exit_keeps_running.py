"""Tray Exit closes only the icon and the window: the agent (its workers) keeps enforcing, and opening Lockdown again
brings the window and the icon back in the same process. Fakes for the tray and the windows; what only Windows can show
(the real pystray icon disappearing, the real WebView2 window hiding / showing) is not proven here."""
import queue
import threading
from datetime import datetime

import pytest

import agent
import antibypass as ab
import webapi
from resident import windows, workers


class FakeTray:
    made = []

    def __init__(self):
        self.started = self.stopped = False
        self.updates = []
        FakeTray.made.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def notify(self, text):
        pass


class FakeToaster:
    fallback = None


class FakeMain:
    def __init__(self):
        self.shown = self.hidden = 0

    def show(self):
        self.shown += 1

    def hide(self):
        self.hidden += 1

    def restore(self):
        pass


class FakePublisher:
    def __init__(self):
        self.visible = []

    def set_visible(self, v):
        self.visible.append(v)

    def publish(self, *a, **k):
        pass


class Ticker:
    """A worker that counts its ticks on a thread: proof that it was not stopped."""

    def __init__(self):
        self.stop_event, self.ticks = threading.Event(), 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop_event.wait(0.01):
            self.ticks += 1


@pytest.fixture
def parts(tmp_path, monkeypatch):
    FakeTray.made.clear()
    monkeypatch.setattr(agent, "_log", lambda m: None)
    ctx = webapi.make_context(tmp_path / "t.db")
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": False})        # Anti-Bypass "free": Exit runs at once
    hub = workers.Hub(log_sink=lambda m: None)
    seen = []
    hub.listeners.append(lambda old, new: seen.append("publisher"))   # (the window's listener)
    toaster = FakeToaster()
    keeper = agent.TrayKeeper(FakeTray, hub, toaster, lambda tray: (lambda old, new: tray.updates.append(new)),
                              log=lambda m: None)
    win = windows.Windows(main=FakeMain(), publisher=FakePublisher())
    windows.page_ready(win, "main")
    return ctx, hub, toaster, keeper, win, seen


def test_exit_leaves_the_workers_running_and_the_tray_gone(parts, monkeypatch):
    ctx, hub, toaster, keeper, win, seen = parts
    quits = []
    monkeypatch.setattr(windows, "quit_agent", lambda w: quits.append(w))
    worker = Ticker()
    worker.thread.start()
    keeper.show()
    tray = FakeTray.made[0]
    assert tray.started and keeper.shown and len(hub.listeners) == 2 and toaster.fallback == tray.notify

    agent._tray_exit(win, keeper, ctx)

    assert tray.stopped and not keeper.shown and win.main.hidden == 1 and win.publisher.visible == [False]
    assert quits == [] and not win.quitting                       # the agent did not quit
    assert len(hub.listeners) == 1                                # only the tray's listener went
    assert toaster.fallback("x") is None                          # notices no longer try the (gone) balloon
    assert ctx.db.get_setting(ab.EXITED_KEY, "0") == "1"
    before = worker.ticks
    threading.Event().wait(0.1)
    assert worker.ticks > before and not worker.stop_event.is_set()   # the workers still tick
    worker.stop_event.set()


def test_exit_source_never_stops_the_workers_or_the_agent():
    import ast
    import inspect
    text = ast.unparse(ast.parse(inspect.getsource(agent._tray_exit)))
    body = text.split('"""')[-1]
    assert "quit_agent" not in body and "pool" not in body and ".stop(" not in body


def test_workers_keep_telling_the_rest_of_the_listeners_after_the_icon_is_gone(parts):
    ctx, hub, toaster, keeper, win, seen = parts
    keeper.show()
    agent._tray_exit(win, keeper, ctx)
    for listener in list(hub.listeners):                           # what StatusWorker.tick does
        listener(None, "snapshot")
    assert seen == ["publisher"] and FakeTray.made[0].updates == []


def test_opening_again_brings_window_and_tray_back_in_the_same_process(parts):
    ctx, hub, toaster, keeper, win, seen = parts
    keeper.show()
    agent._tray_exit(win, keeper, ctx)
    first = FakeTray.made[0]
    shown_before = win.main.shown

    agent.open_again(win, keeper, ctx)

    assert win.main.shown == shown_before + 1 and win.publisher.visible[-1] is True
    assert keeper.shown and len(FakeTray.made) == 2 and FakeTray.made[1] is not first
    assert FakeTray.made[1].started and toaster.fallback == FakeTray.made[1].notify
    assert len(hub.listeners) == 2
    assert ctx.db.get_setting(ab.EXITED_KEY, "0") == "0"
    agent.open_again(win, keeper, ctx)                             # a second open: no second icon
    assert len(FakeTray.made) == 2 and len(hub.listeners) == 2


def test_the_new_icon_gets_the_state_the_listener_would_only_hear_about_on_a_change(parts):
    ctx, hub, toaster, keeper, win, seen = parts
    hub.snapshot = "the newest snapshot"
    keeper.show()
    assert FakeTray.made[0].updates == ["the newest snapshot"]


def test_a_second_launch_goes_through_the_open_event_to_open_again(parts):
    ctx, hub, toaster, keeper, win, seen = parts
    agent._tray_exit(win, keeper, ctx)
    events, done = queue.Queue(), threading.Event()
    windows.pump_open_events(events, win, lambda: (agent.open_again(win, keeper, ctx), done.set()))
    events.put("open")
    assert done.wait(3) and keeper.shown and win.main.shown == 1


def test_hiding_twice_and_stopping_are_harmless(parts):
    ctx, hub, toaster, keeper, win, seen = parts
    assert keeper.hide() is False
    keeper.show()
    keeper.stop()
    assert keeper.hide() is False and FakeTray.made[0].stopped


def test_the_agent_wires_the_keeper_and_open_event():
    from pathlib import Path
    text = (Path(agent.__file__)).read_text(encoding="utf-8")
    assert "windows.pump_open_events(events, win, lambda: open_again(win, state[\"keeper\"], ctx))" in text
    assert "keeper.stop(), windows.quit_agent(win)" in text          # an update still quits everything
    assert '"--watchdog" in argv and ctx.db.get_setting(antibypass.EXITED_KEY' in text
