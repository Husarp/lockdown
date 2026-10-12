"""R04: the Publisher (coalesced pushes) - fake window, fake clock."""
import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from resident import windows  # noqa: E402
from resident.publisher import Publisher  # noqa: E402


class FakeWindow:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)

    def batches(self):
        """Every pushed batch as a list of [event, payload]."""
        out = []
        for s in self.scripts:
            m = re.fullmatch(r"ld\.push\((.*)\)", s)
            out.append(json.loads(m.group(1)))
        return out

    def events(self):
        return [item for batch in self.batches() for item in batch]


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def monotonic(self):
        return self.t

    def sleep(self, sec):
        self.t += sec

    def advance(self, sec):
        self.t += sec


def make(visible=True):
    win, clock = FakeWindow(), FakeClock()
    return Publisher(win, clock, start=False, visible=visible), win, clock


def test_1000_publishes_in_one_second_make_at_most_10_calls():
    pub, win, clock = make()
    for i in range(1000):
        pub.publish("usage_tick", {"n": i}, key="a")
        pub.publish("draft", {"dirty": i % 2 == 0})
        clock.advance(0.001)
        pub.tick()
    pub.flush()
    assert 1 <= len(win.scripts) <= 11   # 10 intervals in 1 s, plus the final flush
    assert win.events()[-2:] == [["usage_tick", {"n": 999}], ["draft", {"dirty": False}]]


def test_coalescing_keeps_the_last_payload_per_key():
    pub, win, clock = make()
    pub.publish("usage_tick", {"v": 1}, key="x")
    pub.publish("usage_tick", {"v": 2}, key="y")
    pub.publish("usage_tick", {"v": 3}, key="x")
    pub.flush()
    assert win.events() == [["usage_tick", {"v": 3}], ["usage_tick", {"v": 2}]]
    assert len(win.scripts) == 1


def test_tick_waits_for_the_interval():
    pub, win, clock = make()
    pub.publish("draft", {"a": 1})
    assert pub.tick() is True
    pub.publish("draft", {"a": 2})
    clock.advance(0.05)
    assert pub.tick() is False
    clock.advance(0.06)
    assert pub.tick() is True
    assert len(win.scripts) == 2


def test_unchanged_payload_is_dropped_but_one_shots_are_not():
    pub, win, clock = make()
    pub.publish("draft", {"dirty": True})
    pub.flush()
    pub.publish("draft", {"dirty": True})
    pub.flush()
    assert len(win.scripts) == 1
    pub.publish("draft", {"dirty": False})
    pub.flush()
    assert len(win.scripts) == 2
    pub.publish("notice", {"text": "hi"})
    pub.flush()
    pub.publish("notice", {"text": "hi"})
    pub.flush()
    assert [e for e in win.events() if e[0] == "notice"] == [["notice", {"text": "hi"}]] * 2


def test_status_is_sent_as_deltas_and_unchanged_status_is_dropped():
    pub, win, clock = make()
    pub.publish("status", {"a": 1, "b": 2})
    pub.flush()
    pub.publish("status", {"a": 1, "b": 3})
    pub.flush()
    pub.publish("status", {"a": 1, "b": 3})
    pub.flush()
    assert win.events() == [["status", {"a": 1, "b": 2}], ["status", {"b": 3}]]


def test_hidden_sends_only_dirty_flags_once_per_page():
    pub, win, clock = make(visible=False)
    for i in range(50):
        pub.publish("blocks_changed", {"n": i})
        pub.publish("usage_tick", {"n": i})
        pub.publish("status", {"tick": i})
        pub.publish("dirty", {"page": "network"})
    pub.flush()
    assert win.scripts == []


def test_show_sends_held_flags_and_a_fresh_status():
    pub, win, clock = make(visible=False)
    pub.publish("status", {"mode": "A", "running": True})
    pub.publish("status", {"mode": "B"})
    pub.publish("blocks_changed", {"n": 1})
    pub.publish("download_progress", {"pct": 5})
    pub.publish("dirty", {"page": "network"})
    pub.set_visible(True)
    pub.flush()
    events = win.events()
    assert ["visible", {"visible": True}] in events
    dirty_pages = sorted(p["page"] for e, p in events if e == "dirty")
    assert dirty_pages == ["about", "blocking", "dashboard", "network"]
    assert ["status", {"mode": "B", "running": True}] in events
    # a second show with nothing new sends no dirty flags again
    pub.set_visible(False)
    pub.flush()
    pub.set_visible(True)
    pub.flush()
    assert [e for e in win.events()[len(events):] if e[0] == "dirty"] == []


def test_going_hidden_turns_unsent_page_events_into_flags():
    pub, win, clock = make()
    pub.publish("usage_tick", {"n": 1})
    pub.set_visible(False)
    pub.flush()
    assert win.events() == [["visible", {"visible": False}]]
    pub.set_visible(True)
    pub.flush()
    assert sorted(p["page"] for e, p in win.events() if e == "dirty") == ["dashboard", "screentime"]


def test_events_that_must_get_through_while_hidden():
    pub, win, clock = make(visible=False)
    pub.publish("notice", {"text": "x"})
    pub.publish("show_page", {"page": "calendar"})
    pub.flush()
    assert [e for e, _ in win.events()] == ["notice", "show_page"]


def test_datetime_payload_is_serialised():
    pub, win, clock = make()
    pub.publish("status", {"clock_now": datetime(2026, 10, 11, 14, 5, 3)})
    pub.publish("draft", {"when": datetime(2026, 1, 2, 3, 4)})
    pub.flush()
    assert ["status", {"clock_now": "2026-10-11T14:05:03"}] in win.events()
    assert ["draft", {"when": "2026-01-02T03:04:00"}] in win.events()


def test_a_failing_window_does_not_kill_the_publisher():
    class Broken(FakeWindow):
        def evaluate_js(self, script):
            raise RuntimeError("window is gone")
    logs = []
    pub = Publisher(Broken(), FakeClock(), start=False, log=logs.append)
    pub.publish("draft", {"a": 1})
    pub.flush()
    pub.publish("draft", {"a": 2})
    assert pub.flush() is True
    assert logs and "window is gone" in logs[0]


def test_flusher_thread_sends_and_exits_on_stop():
    win = FakeWindow()
    pub = Publisher(win)   # real clock, real thread
    start = time.monotonic()
    for i in range(300):
        pub.publish("usage_tick", {"n": i})
        time.sleep(0.003)
    deadline = time.monotonic() + 2
    while (not win.scripts or win.events()[-1] != ["usage_tick", {"n": 299}]) and time.monotonic() < deadline:
        time.sleep(0.01)
    elapsed = time.monotonic() - start
    assert win.events()[-1] == ["usage_tick", {"n": 299}]
    assert len(win.scripts) <= elapsed / 0.1 + 2   # never faster than one per 100 ms
    pub.stop()
    assert not pub._thread.is_alive()
    assert not any(t.name == "lockdown-publisher" and t.is_alive() for t in threading.enumerate())


def test_publish_is_cheap():
    pub, win, clock = make()
    start = time.perf_counter()
    for i in range(10000):
        pub.publish("usage_tick", {"n": i, "rows": [1, 2, 3]}, key=i % 20)
    per_call_us = (time.perf_counter() - start) / 10000 * 1e6
    assert per_call_us < 100   # measured ~10 us; this is the GUI/worker thread's whole cost


def test_windows_hooks_tell_the_publisher_when_shown_or_hidden():
    class Win:
        def __init__(self):
            self.calls = []

        def show(self): self.calls.append("show")
        def hide(self): self.calls.append("hide")
        def restore(self): self.calls.append("restore")

    pub, fake, clock = make(visible=False)
    w = windows.Windows(main=Win(), publisher=pub)
    pub.publish("blocks_changed", {})
    windows.show_main(w)
    assert pub._visible is False and w.main.calls == []      # (its page has not said it works yet: not shown)
    windows.page_ready(w, "main")                            # ... and now it has: shown at once
    assert w.main.calls == ["show", "restore"]
    assert pub._visible is True
    assert windows.close_means_hide(w) is False
    assert pub._visible is False
    pub.flush()
    assert fake.events() == [["visible", {"visible": False}]]
    assert "blocking" in pub._dirty   # held for the next show
