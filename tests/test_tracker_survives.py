"""The usage tracker must never stop counting silently.

On 2026-09-22 the service restarted at 14:04:34 and the app five seconds later; both opened config.db at
once. Opening the database is part of the tracker's SETUP, and only its tick was guarded - so the thread died
there, logged nothing (the logging lived inside the loop it never reached), and nothing restarted it. Blocking
carried on, because the service does that, and time quietly stopped being counted for 4 hours 20 minutes. The
2-hour group limit read 39 minutes after a day of use."""
import threading
import time

from monitor.usage import UsageTracker


class Boom(UsageTracker):
    """Fails while starting up, the way a locked database or a COM initializer does."""

    def __init__(self, failures: int):
        super().__init__()
        self.left = failures
        self.started = 0
        self.counted = threading.Event()

    def _count(self, db):
        self.started += 1
        if self.left > 0:
            self.left -= 1
            raise OSError("database is locked")
        self.last_tick = time.time()
        self.counted.set()
        self.stop_event.wait()


def run_briefly(tracker, monkeypatch, seconds=2.0):
    monkeypatch.setattr("monitor.usage.RESTART_SEC", 0.05)
    monkeypatch.setattr("monitor.usage.Database", lambda *a, **k: object())
    thread = threading.Thread(target=tracker.run, daemon=True)
    thread.start()
    ok = tracker.counted.wait(seconds)
    tracker.stop_event.set()
    thread.join(timeout=2)
    return ok


def test_a_crash_while_starting_does_not_end_the_tracker(monkeypatch):
    tracker = Boom(failures=3)
    assert run_briefly(tracker, monkeypatch), "the tracker gave up instead of starting again"
    assert tracker.started == 4          # three failures, then it counted


def test_it_is_written_down_rather_than_swallowed(monkeypatch, caplog):
    tracker = Boom(failures=1)
    with caplog.at_level("ERROR"):
        assert run_briefly(tracker, monkeypatch)
    assert any("Usage tracking stopped" in r.message for r in caplog.records), \
        "a tracker that fell over must say so in the log"


def test_stopping_it_still_stops_it(monkeypatch):
    """The retry loop must not keep a closing app alive."""
    tracker = Boom(failures=0)
    assert run_briefly(tracker, monkeypatch)
    assert tracker.stop_event.is_set()


def test_stalled_for_reports_the_silence(monkeypatch):
    tracker = UsageTracker()
    assert tracker.stalled_for() == 0.0           # nothing counted yet: not a stall, just not started
    tracker.last_tick = 1000.0
    assert tracker.stalled_for(now=1000.0 + 4 * 60 * 60) == 4 * 60 * 60
