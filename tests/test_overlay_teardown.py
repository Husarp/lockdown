"""Phase 0 fix: the sleep/break overlay must be torn down BEFORE its answer runs, so a slow or raising handler
(or the Anti-Bypass challenge it opens) can never sit behind the still-up full-screen cover and freeze the app."""
from gui.reminders_ui import _finish


class FakeMaster:
    def __init__(self):
        self.scheduled = []

    def after_idle(self, fn):
        self.scheduled.append(fn)


class FakeWin:
    def __init__(self):
        self.master = FakeMaster()
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


def test_destroyed_before_answer_runs():
    win = FakeWin()
    ran = []
    _finish(win, "disable", lambda a: ran.append(a))
    assert win.destroyed          # cover is gone immediately
    assert ran == []              # the answer is deferred, not run while the cover is up
    assert win.master.scheduled   # it was scheduled for the next idle tick
    win.master.scheduled[0]()     # simulate the idle tick
    assert ran == ["disable"]     # now the answer runs, on a clean screen


def test_cover_gone_even_if_answer_would_raise():
    win = FakeWin()
    _finish(win, "x", lambda a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert win.destroyed          # destroyed regardless of what the deferred handler does
