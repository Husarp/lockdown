"""0.84.0 crash and stall fixes around the Tk thread: worker threads hand results to a queue instead of calling Tk,
garbage collection no longer sweeps the whole heap every 2 s, the tray is only touched when something changed,
`schtasks` is off the start-up path, and the Dashboard works out "coming up" once per refresh."""
import ast
import gc
import inspect
import textwrap
import threading
import time
import types
from datetime import datetime
from pathlib import Path

import pytest

from gui import mainthread

SRC = Path(__file__).resolve().parents[1] / "src"


# ---------- the call queue ----------

def test_worker_results_run_on_the_draining_thread():
    q = mainthread.CallQueue()
    ran = []
    workers = [threading.Thread(target=lambda i=i: q.post(lambda: ran.append((i, threading.get_ident()))))
               for i in range(20)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert ran == []                            # nothing runs on the worker threads
    assert q.drain() == 20
    assert sorted(i for i, _ in ran) == list(range(20))
    assert {t for _, t in ran} == {threading.get_ident()}


def test_progress_updates_are_coalesced():
    q = mainthread.CallQueue()
    bar = []
    for n in range(1000):                       # one per downloaded chunk
        q.post_latest("progress", bar.append, n / 1000)
    q.post(bar.append, "done")
    q.drain()
    assert bar == [0.999, "done"]
    q.post_latest("progress", bar.append, 1.0)
    q.drain()
    assert bar[-1] == 1.0


def test_one_failing_call_does_not_stop_the_rest():
    q = mainthread.CallQueue()
    errors, ran = [], []
    q.post(lambda: 1 / 0)
    q.post(ran.append, "ok")
    q.drain(on_error=errors.append)
    assert ran == ["ok"] and isinstance(errors[0], ZeroDivisionError)


def test_a_flood_is_drained_in_slices():
    q = mainthread.CallQueue()
    for _ in range(mainthread.MAX_PER_DRAIN + 5):
        q.post(lambda: None)
    assert q.drain() == mainthread.MAX_PER_DRAIN and q.drain() == 5


def _worker_functions():
    """(file, function) pairs that run on worker threads."""
    return [("gui/about_page.py", "_ask"), ("gui/about_page.py", "_fetch"), ("gui/about_page.py", "_progress"),
            ("gui/app.py", "_ask_github")]


@pytest.mark.parametrize("path, name", _worker_functions())
def test_worker_threads_never_call_tk(path, name):
    """about_page.py:131-177 and app.py:576 used to call self.after(...) from a worker thread - Tcl from the wrong
    thread, a crash / hang source. They must go through the app's call queue."""
    tree = ast.parse((SRC / path).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    tk_calls = {"after", "after_idle", "configure", "set", "pack", "update", "update_idletasks"}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in tk_calls:
            pytest.fail(f"{path}:{node.lineno} {name} calls .{node.func.attr}() from a worker thread")
    assert "call_soon" in ast.unparse(fn) or "call_latest" in ast.unparse(fn)


# ---------- garbage collection ----------

def test_no_full_collection_on_every_tick():
    """A full gc.collect() every 2 s was a stall that grew with every widget. Now: young generation when it has
    filled up, generation 1 now and then, a full collection once in FULL_EVERY ticks."""
    t0 = 700
    plan = [mainthread.collection_due(tick, 701, t0) for tick in range(1, mainthread.FULL_EVERY + 1)]
    assert plan.count(2) == 1 and plan[-1] == 2
    assert plan.count(1) == mainthread.FULL_EVERY // mainthread.MIDDLE_EVERY - 1
    assert mainthread.collection_due(3, 10, t0) is None          # nothing new: nothing to do
    assert mainthread.FULL_EVERY * mainthread.GC_MS >= 60_000     # full sweeps at most once a minute


def test_collect_and_freeze():
    gc.collect()
    try:
        mainthread.freeze_startup()
        assert gc.get_freeze_count() > 0
        assert mainthread.collect(mainthread.FULL_EVERY) == 2
    finally:
        gc.unfreeze()


def test_app_keeps_collection_on_the_tk_thread():
    """Automatic GC stays off in the window process (it could free a Tk object on another thread - the 0.4.0 hang)
    but the 2 s full sweep is gone, and startup objects are frozen once every page is built."""
    app = (SRC / "gui/app.py").read_text(encoding="utf-8")
    assert "gc.disable()" in app and "gc.collect()" not in app
    assert "mainthread.collect(" in app and "mainthread.freeze_startup()" in app


# ---------- tray ----------

class FakeIcon:
    def __init__(self):
        self.calls = []

    def update_menu(self):
        self.calls.append("menu")

    def __setattr__(self, name, value):
        if name != "calls":
            self.calls.append(name)
        object.__setattr__(self, name, value)


def test_tray_is_only_touched_when_something_changed(monkeypatch):
    from gui import tray as traymod
    monkeypatch.setattr(traymod.icon_art, "tray_icon", lambda state: state)
    t = traymod.Tray(lambda: None, lambda: None)
    t.icon = FakeIcon()
    assert t.update(True, "3 sites/apps blocked")
    assert t.icon.calls == ["icon", "title", "menu"]
    t.icon.calls.clear()
    for _ in range(100):                         # the 3-second status poll, with nothing new
        assert not t.update(True, "3 sites/apps blocked")
    assert t.icon.calls == []
    assert t.update(False, "3 sites/apps blocked")    # service stopped: red icon, new title
    assert t.icon.calls == ["icon", "title", "menu"]
    t.icon.calls.clear()
    t.set_modes([("deep", "Deep work")], None)
    t.set_modes([("deep", "Deep work")], None)
    assert t.icon.calls == ["menu"]


# ---------- autostart ----------

def test_watchdog_task_is_rewritten_on_every_launch(monkeypatch):
    """Anti-bypass: a watchdog task the user disabled (`schtasks /Change /DISABLE`, no admin needed) or edited must
    be replaced by a fresh, enabled one on the next launch - so `/Create /F` runs every time, whatever exists."""
    import main
    runs, values = [], []
    monkeypatch.setattr(main.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(main.subprocess, "run",
                        lambda args, **k: runs.append(args) or types.SimpleNamespace(returncode=0, stderr=b""))
    monkeypatch.setattr(main.winreg, "SetValueEx", lambda key, name, *a: values.append((name, a[-1])), raising=False)
    for _ in range(3):
        main.register_autostart()
    assert len(runs) == 3
    for args in runs:
        assert args[:2] == ["schtasks", "/Create"] and "/F" in args and main.WATCHDOG_TASK in args
        assert "/Query" not in args
    assert [name for name, _ in values] == ["Lockdown"] * 3          # the Run value too, every launch


def test_autostart_failure_is_logged(monkeypatch, tmp_path):
    import main
    import paths
    log = tmp_path / "lockdown.log"
    monkeypatch.setattr(paths, "LOG_PATH", log)

    def fail():
        raise OSError("schtasks /Create failed (1)")
    monkeypatch.setattr(main, "register_autostart", fail)
    main.register_autostart_later()
    for _ in range(100):
        if log.exists() and "registration failed" in log.read_text(encoding="utf-8"):
            break
        time.sleep(0.02)
    assert "schtasks /Create failed" in log.read_text(encoding="utf-8")


def test_autostart_runs_off_the_ui_thread(monkeypatch):
    import main
    seen = threading.Event()
    where = []

    def register():
        where.append(threading.current_thread() is threading.main_thread())
        seen.set()
    monkeypatch.setattr(main, "register_autostart", register)
    main.register_autostart_later()
    assert seen.wait(5) and where == [False]


def test_main_does_not_register_autostart_synchronously():
    source = (SRC / "main.py").read_text(encoding="utf-8")
    entry = source[source.index('if __name__ == "__main__":'):]
    assert "register_autostart_later()" in entry and "register_autostart()" not in entry


# ---------- dashboard ----------

def test_dashboard_works_out_coming_up_once_per_refresh():
    from gui import dashboard
    src = textwrap.dedent(inspect.getsource(dashboard.DashboardPage))
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and
             ((isinstance(n.func, ast.Name) and n.func.id == "upcoming") or
              (isinstance(n.func, ast.Attribute) and n.func.attr in ("upcoming", "_upcoming")))]
    assert len(calls) == 1


def test_upcoming_lists_starts_ends_and_limits(tmp_path):
    from db import Database
    from gui.dashboard import upcoming
    from rules import make_schedule
    db = Database(tmp_path / "t.db")
    night = make_schedule("block", [(list(range(7)), "21:00", "07:00")])
    db.add_item("Discord", ["discord.exe"], "app", rules=[{"rule_type": "scheduled", "schedule": night}])
    db.add_item("Steam", ["steam.exe"], "app", rules=[{"rule_type": "permanent"}])
    now = datetime(2026, 10, 1, 20, 0)
    out = upcoming(now, db.list_items(), db.list_groups(), db.usage_lookup(now))
    assert [(e["kind"], e["when"], e["title"]) for e in out] == [("start", datetime(2026, 10, 1, 21, 0),
                                                                   "Discord blocked")]


def test_tray_menu_is_rebuilt_on_pystrays_thread_only_when_shown(monkeypatch):
    """Review (threading #2): update_menu() from the Tk thread destroyed the native menu while pystray's thread
    could be showing it. Now it only marks it; the rebuild happens in the click handler, on pystray's thread."""
    from gui import tray as traymod
    built, shown = [], []
    monkeypatch.setattr(traymod._Icon.__mro__[1], "_on_notify",          # (pystray's handler: shows the menu)
                        lambda self, w, l: shown.append(list(built)), raising=False)
    icon = traymod._Icon("t")
    icon._update_menu = lambda: built.append(threading.current_thread().name)
    icon.update_menu()
    icon.update_menu()
    assert built == [] and icon.menu_dirty                     # nothing touched from the caller's thread

    def pystray_thread():
        icon._on_notify(0, 0x205)                                # (WM_RBUTTONUP)
        icon._on_notify(0, 0x205)
    t = threading.Thread(target=pystray_thread, name="pystray")
    t.start()
    t.join()
    assert built == ["pystray"] and not icon.menu_dirty          # once, on its thread, before showing
    assert shown == [["pystray"], ["pystray"]]
    assert type(traymod.Tray(lambda: None, lambda: None).icon) is traymod._Icon


@pytest.mark.parametrize("path, method", [
    ("gui/dashboard.py", "_auto_refresh"), ("gui/screen_time.py", "_auto_refresh"),
    ("gui/antibypass_page.py", "_live"), ("gui/modes_page.py", "_tick"), ("gui/blocking.py", "_auto_refresh"),
    ("gui/blocking.py", "_live_update"), ("gui/network.py", "_live"), ("gui/app.py", "_tick_clock"),
    ("gui/app.py", "_poll_events"), ("gui/app.py", "_collect_garbage"), ("gui/app.py", "_poll_status"),
    ("gui/app.py", "_poll_watcher"), ("gui/app.py", "_poll_block_events"), ("gui/app.py", "_poll_reminders"),
    ("gui/app.py", "_poll_updates"),
])
def test_refresh_loops_reschedule_even_after_an_error(path, method):
    """Review (threading #6): a page loop that raised once never ran again (the lock banner, a mode's lock state,
    the clock, tray Open ...). Its self.after(..., self.<loop>) must sit in a `finally`."""
    tree = ast.parse((SRC / path).read_text(encoding="utf-8-sig"))
    found = 0
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and fn.name == method):
            continue
        finals = [n for t in ast.walk(fn) if isinstance(t, ast.Try) for f in t.finalbody for n in ast.walk(f)]
        for call in ast.walk(fn):
            if isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "after" and len(call.args) > 1 \
                    and getattr(call.args[1], "attr", None) == method:
                found += 1
                assert call in finals, f"{path}:{call.lineno} {method} reschedules outside `finally`"
    assert found, (path, method)


def test_full_collection_frees_startup_objects_that_became_garbage():
    """Review (perf #10): after gc.freeze() an object alive at startup that later became cyclic garbage (a widget
    built at prebuild, destroyed later) was never collected. The 5-minute full sweep now unfreezes first."""
    import weakref

    class Node:
        pass
    gc.collect()
    try:
        a, b = Node(), Node()
        a.other, b.other = b, a
        gone = weakref.ref(a)
        mainthread.freeze_startup()
        del a, b                                                 # a cycle among frozen objects
        mainthread.collect(1)
        assert gone() is not None                                # (young collections leave the frozen alone)
        assert mainthread.collect(mainthread.FULL_EVERY) == 2
        assert gone() is None
        assert gc.get_freeze_count() > 0                         # and what is alive is frozen again
    finally:
        gc.unfreeze()


def test_restart_flushes_before_launching_the_new_copy():
    """Review (threading #4): the helper was started first, then the flush could wait for a lock past its delay;
    the new copy then found the old one still running and quit - nothing ran until the watchdog."""
    source = (SRC / "gui" / "app.py").read_text(encoding="utf-8")
    body = source[source.index("    def restart(self):"):source.index("    def report_callback_exception")]
    assert body.index("self._shutdown()") < body.index("subprocess.Popen(")
