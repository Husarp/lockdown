"""R03: the status snapshot and the background workers (resident/snapshot.py, resident/workers.py).

Everything runs on a temp database with a fake clock and a fake Win32 module, so no test waits for real time (the few
that use real threads wait for milliseconds) and none touches the desktop. What can only be proven on Windows: real
minimise / close of windows, the tray's colours, the usage tracker and the bad-word check on a live desktop."""
import ast
import json
import statistics
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import antibypass
import digest
import emergency
import modes
import pause
import reminders
import synthdata
import updates
from db import Database
from resident import snapshot, workers
from rules import TIME_FMT, make_schedule

SRC = Path(__file__).resolve().parents[1] / "src"
NOW = datetime(2026, 9, 14, 22, 0, 0)    # a Monday evening
EVERY_DAY = list(range(7))


# ---------------------------------------------------------------- fakes

class FakeClock:
    """Trusted time, the PC clock and the steady clock, all moved by hand."""

    def __init__(self, now=NOW):
        self.t, self._wall, self._mono = now, 1_000_000.0, 0.0

    def now(self, db):
        return self.t

    def now_with(self, offset):
        return self.t + timedelta(seconds=offset)

    def wall(self):
        return self._wall

    def mono(self):
        return self._mono

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)
        self._wall += seconds
        self._mono += seconds


class FakeWin:
    """monitor.win with nothing behind it: it records what the workers asked for."""

    def __init__(self):
        self.front = (0, "")
        self.closed_apps, self.closed_windows, self.minimized_apps, self.minimized_windows = [], [], [], []
        self.minimize_result = True
        self.break_minimizes, self.break_result, self.break_allowed = 0, False, []
        self.dnd, self.idle, self.fullscreen = False, 0.0, False

    def foreground(self):
        return self.front

    def close_app(self, exe):
        self.closed_apps.append(exe)
        return 1

    def close_windows(self, pids):
        self.closed_windows.append(set(pids))
        return 1

    def minimize_app(self, exe):
        self.minimized_apps.append(exe)
        return self.minimize_result

    def minimize_windows(self, pids):
        self.minimized_windows.append(set(pids))
        return self.minimize_result

    def minimize_foreground(self, allowed=None):
        self.break_minimizes += 1
        self.break_allowed.append(allowed)
        return self.break_result

    def content_pid(self, hwnd):
        return 0

    def do_not_disturb(self):
        return self.dnd

    def idle_seconds(self):
        return self.idle

    def is_fullscreen(self):
        return self.fullscreen


class FakeUsage:
    """monitor.usage: what the block-event and minimise workers read from it."""

    def __init__(self):
        self.limit_writes = 0
        self.shown = set()
        self.pids = {}

    def pids_of(self, db, exe, published=None):
        return set(self.pids.get(exe, ()))

    def shown_since(self, since):
        return set(self.shown)

    def members(self, db):
        return {}


class Env:
    def __init__(self, tmp_path):
        self.path = tmp_path / "t.db"
        self.db = Database(self.path)
        self.clock, self.win, self.usage, self.errors = FakeClock(), FakeWin(), FakeUsage(), []
        self.hub = workers.Hub(self.path, clock=self.clock, win=self.win, usage=self.usage,
                               log_sink=self.errors.append)
        self.shown = []        # (message, action, actions, sticky) as the notice sink got them
        self.hub.notify.show = lambda message, action=None, actions=None, sticky=False: self.shown.append(
            (message, action, actions, sticky))

    @property
    def messages(self):
        return [s[0] for s in self.shown]

    def snap(self):
        """What the StatusWorker does each second."""
        snap = snapshot.build(self.db, self.clock.t, self.clock.wall())
        self.hub.snapshot = snap
        return snap

    def heartbeat(self, ago=1.0):
        self.db.set_setting(snapshot.HEARTBEAT_KEY, str(self.clock.wall() - ago))

    def real_pacing(self):
        """For tests that start real threads: the workers' pacing clock must really move."""
        self.clock.mono = time.monotonic
        return self


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def sched(start, end):
    return {"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, start, end)])}


def iso(t):
    return t.isoformat(timespec="seconds")


# ---------------------------------------------------------------- the snapshot

def test_a_fresh_database(env):
    snap = env.snap()
    assert snap.running is False and snap.off is None and snap.pause is None and snap.mode is None
    assert snap.blocks["count"] == 0 and snap.blocks["total"] == 0 and snap.blocks["next_change"] is None
    assert snap.antibypass == {"status": "free", "active": False, "unlocked_until": None, "waiting_until": None,
                               "next_open": None}
    assert snap.update is None and snap.clock_offset == 0.0
    assert snap.tray.colour == "red" and snap.tray.text == "0 sites/apps blocked"


def test_the_page_part_is_plain_json(env):
    env.heartbeat()
    env.db.add_site("YouTube", ["youtube.com"])
    modes.start(env.db, "focus", NOW, None)
    pause_ = env.snap().public()
    text = json.dumps(pause_)          # no datetimes anywhere
    assert set(json.loads(text)) == set(snapshot.PUBLIC_KEYS)


def test_the_heartbeat_decides_whether_the_service_runs(env):
    env.heartbeat(ago=5)
    assert env.snap().running is True
    env.heartbeat(ago=20)
    assert env.snap().running is False                     # older than 15 s: service down
    env.db.set_setting(snapshot.HEARTBEAT_KEY, "not a number")
    assert env.snap().running is False
    env.db.set_setting(snapshot.HEARTBEAT_KEY, str(env.clock.wall() - 14))
    assert env.snap().running is True


def test_heartbeat_key_is_the_services(env):
    import service
    assert snapshot.HEARTBEAT_KEY == service.HEARTBEAT_KEY


def test_service_down_shows_in_the_tray(env):
    env.db.add_site("YouTube", ["youtube.com"])
    env.heartbeat(ago=60)
    snap = env.snap()
    assert snap.running is False and snap.tray.colour == "red"
    assert snap.tray.text == "1 sites/apps blocked"        # (the tray's old wording)
    env.heartbeat(ago=1)
    assert env.snap().tray.colour == "green"


def test_switched_off(env):
    env.heartbeat()
    env.db.add_site("YouTube", ["youtube.com"])
    antibypass.switch_off(env.db, NOW)
    snap = env.snap()
    assert snap.off == NOW.strftime(TIME_FMT)
    assert snap.tray.colour == "grey" and snap.tray.text == "OFF - nothing is enforced" and snap.tray.off
    antibypass.switch_on(env.db)
    assert env.snap().off is None


def test_paused_blocks(env):
    env.heartbeat()
    item = env.db.add_site("YouTube", ["youtube.com"])
    assert env.snap().item_states[item]["blocked"] is True
    pause.start(env.db, NOW, 30, silent=True)
    snap = env.snap()
    assert snap.pause == {"started": iso(NOW), "until": iso(NOW + timedelta(minutes=30)), "silent": True}
    assert snap.block_list == () and snap.blocks["count"] == 0       # nothing of yours blocks while paused
    state = snap.item_states[item]
    assert state["blocked"] is False and state["next"] == iso(NOW + timedelta(minutes=30)) and state["rule"] == "pause"
    assert snap.tray.colour == "blue" and snap.tray.text == "Blocking paused until 22:30"
    pause.resume(env.db)
    assert env.snap().pause is None


def test_a_locked_mode(env):
    env.heartbeat()
    item = env.db.add_item("Reddit", ["reddit.com"], "site", rules=[])    # no rule of its own: only the mode blocks it
    modes.start(env.db, "work", NOW, NOW + timedelta(hours=1), locked=True)
    snap = env.snap()
    assert snap.mode == {"id": "work", "name": "Work", "started": iso(NOW), "ends_at": iso(NOW + timedelta(hours=1)),
                         "locked": True, "by_schedule": False, "mute": False, "blocking": True, "phase": None}
    assert snap.item_states[item]["blocked"] and snap.item_states[item]["reason"] == "mode"
    assert snap.tray.colour == "yellow" and snap.tray.text == "1 sites/apps blocked · Work mode"
    assert snap.tray.mode_id == "work" and ("work", "Work") in snap.tray.modes


def test_pomodoro_phase(env):
    modes.start(env.db, "focus", NOW, None)
    phase = env.snap().mode["phase"]
    assert phase == {"name": "focus", "ends_at": iso(NOW + timedelta(minutes=25)), "round": 1, "rounds": 4}
    env.clock.t = NOW + timedelta(minutes=27)
    snap = env.snap()
    assert snap.mode["phase"]["name"] == "break" and snap.mode["blocking"] is False


def test_a_mode_on_by_its_schedule_does_not_change_every_tick(env):
    listed = modes.load(env.db)
    listed[1]["schedule"] = make_schedule("block", [(EVERY_DAY, "09:00", "23:00")])
    modes.save(env.db, listed)
    first = env.snap()
    assert first.mode["by_schedule"] is True and first.mode["started"] is None
    env.clock.advance(1)
    assert snapshot.changed(first, env.snap()) == {}


def test_anti_bypass_states(env):
    cfg = antibypass.settings(env.db)
    antibypass.save(env.db, {**cfg, "phrase": True})
    snap = env.snap()
    assert snap.antibypass["status"] == "phrase" and snap.antibypass["active"] is True
    until = antibypass.unlock(env.db, NOW)
    assert until is None
    snap = env.snap()
    assert snap.antibypass["status"] == "free"
    assert snap.antibypass["unlocked_until"] == iso(NOW + timedelta(minutes=antibypass.UNLOCK_MIN))
    antibypass.lock(env.db)
    antibypass.save(env.db, {**antibypass.settings(env.db), "wait_min": 2})
    antibypass.unlock(env.db, NOW)
    snap = env.snap()
    assert snap.antibypass["status"] == "waiting" and snap.antibypass["waiting_until"] == iso(NOW + timedelta(minutes=2))
    # only in the allowed hours: Monday 22:00 is outside 18:00-20:00 on Sunday only -> closed, with the next chance
    antibypass.save(env.db, {**antibypass.DEFAULTS, "hours": True,
                             "windows": [{"days": [0], "start": "23:00", "end": "23:30"}]})
    snap = env.snap()
    assert snap.antibypass["status"] == "closed" and snap.antibypass["next_open"] == iso(NOW.replace(hour=23))


def test_emergency_uses_left_per_kind(env):
    snap = env.snap()
    assert snap.emergency["enabled"] and snap.emergency["usable"] and snap.emergency["left"] == 3
    assert [(k["id"], k["minutes"], k["per"], k["left"], k["allowed"]) for k in snap.emergency["kinds"]] \
        == [("t1", 20, "week", 3, 3)]
    emergency.unlock(env.db, [], NOW, alerts=True, tier="t1")
    snap = env.snap()
    assert snap.emergency["kinds"][0]["left"] == 2 and snap.emergency["left"] == 2
    assert snap.emergency["kinds"][0]["resets"] is not None
    env.db.set_setting("emergency.enabled", "0")
    snap = env.snap()
    assert snap.emergency["enabled"] is False and snap.emergency["usable"] is False and snap.emergency["left"] == 0


def test_two_emergency_kinds(env):
    emergency.save_tiers(env.db, [{"id": "a", "uses": 2, "per": "day", "minutes": 5},
                                  {"id": "b", "uses": 1, "per": "month", "minutes": 30}])
    emergency.unlock(env.db, [], NOW, alerts=True, tier="b")
    kinds = {k["id"]: k["left"] for k in env.snap().emergency["kinds"]}
    assert kinds == {"a": 2, "b": 0}


def test_a_waiting_update_and_its_snooze(env):
    found = {"version": "99.0.0", "url": "https://example.test/r", "asset": "LockdownSetup.exe", "size": 5}
    env.db.set_setting(updates.FOUND_KEY, json.dumps(found))
    snap = env.snap()
    assert snap.update == {"version": "99.0.0", "installable": True} and snap.tray.update == "99.0.0"
    updates.snooze(env.db, NOW)
    snap = env.snap()
    assert snap.update is None and snap.tray.update is None


def test_per_item_state_with_the_next_change(env):
    perm = env.db.add_site("Perm", ["perm.test"])
    window = env.db.add_item("Night", ["night.test"], "site", rules=[sched("23:00", "23:30")])
    temp = env.db.add_item("Temp", ["temp.test"], "site",
                           rules=[{"rule_type": "temporary", "temp_until": "2026-09-14 23:15:00"}])
    off_item = env.db.add_site("Off", ["off.test"])
    env.db.conn.execute("UPDATE blocked_items SET disabled = 1 WHERE id = ?", (off_item,))
    env.db.conn.commit()
    snap = env.snap()
    assert snap.item_states[perm] == {"blocked": True, "reason": "permanent", "until": None, "next": None,
                                      "rule": "permanent"}
    assert snap.item_states[window] == {"blocked": False, "reason": None, "until": None,
                                        "next": iso(NOW.replace(hour=23)), "rule": "scheduled"}
    assert snap.item_states[temp]["blocked"] and snap.item_states[temp]["until"] == "2026-09-14T23:15:00"
    assert off_item not in snap.item_states                         # a disabled item has no state
    assert snap.blocks["count"] == 2 and snap.blocks["total"] == 4
    assert snap.blocks["next_change"] == iso(NOW.replace(hour=23))   # the soonest of all the changes


def test_blocks_rev_changes_only_when_a_block_does(env):
    item = env.db.add_item("Night", ["night.test"], "site", rules=[sched("23:00", "23:30")])
    first = env.snap()
    env.clock.advance(1)
    assert env.snap().blocks == first.blocks
    env.clock.t = NOW.replace(hour=23, minute=5)
    started = env.snap()
    assert started.item_states[item]["blocked"] and started.blocks["rev"] != first.blocks["rev"]
    assert started.blocks["count"] == 1


def test_changed_gives_only_the_keys_that_changed(env):
    env.heartbeat()
    first = env.snap()
    env.clock.advance(1)
    assert snapshot.changed(first, env.snap()) == {}
    env.heartbeat(ago=30)
    flipped = env.snap()
    assert snapshot.changed(first, flipped) == {"running": False}
    antibypass.switch_off(env.db, env.clock.t)
    switched = env.snap()
    assert snapshot.changed(flipped, switched) == {"off": env.clock.t.strftime(TIME_FMT)}
    assert snapshot.changed(None, switched) == switched.public()           # the first one: everything
    assert snapshot.changed(first, None) == {}


def test_a_snapshot_is_frozen(env):
    snap = env.snap()
    with pytest.raises(Exception):
        snap.running = True


def test_the_clock_offset_is_carried(env):
    env.db.set_setting("clock_offset", "-125.5")
    assert env.snap().clock_offset == -125.5
    env.db.set_setting("clock_offset", "junk")
    assert env.snap().clock_offset == 0.0


def test_build_on_a_two_year_database_is_fast(tmp_path):
    path = tmp_path / "synth.db"
    Database(path).close()
    info = synthdata.build(path, date(2026, 10, 11), days=730, extra_items=40)
    db = Database(path, ui=True)
    for _ in range(3):
        snapshot.build(db, info["now"])
    times = []
    for _ in range(40):
        started = time.perf_counter()
        snap = snapshot.build(db, info["now"])
        times.append((time.perf_counter() - started) * 1000)
    times.sort()
    p95 = times[int(len(times) * 0.95) - 1]
    print(f"snapshot.build, 730-day synthetic DB with {len(snap.items)} items: "
          f"p50 {statistics.median(times):.1f} ms, p95 {p95:.1f} ms, max {times[-1]:.1f} ms")
    assert p95 <= 60
    assert len(snap.items) == 46 and snap.blocks["total"] == 46


# ---------------------------------------------------------------- the base worker

class Counter(workers.Worker):
    label = "counter"

    def __init__(self, hub, interval=30.0, fail=0):
        super().__init__(hub, interval)
        self.ticks, self.fail, self.thread_ids, self.db_seen = 0, fail, [], []

    def setup(self):
        self.db_seen.append(self.db)
        self.thread_ids.append(threading.get_ident())

    def tick(self):
        self.ticks += 1
        if self.ticks <= self.fail:
            raise RuntimeError("boom")


def wait_for(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def test_workers_stop_within_a_second(env):
    env.real_pacing()
    w = Counter(env.hub, interval=30)       # waiting for its next tick half a minute away
    w.start()
    assert wait_for(lambda: w.ticks == 1)
    started = time.monotonic()
    w.stop(timeout=1)
    assert not w.is_alive() and time.monotonic() - started < 1


def test_a_worker_waits_instead_of_spinning(env):
    env.real_pacing()
    w = Counter(env.hub, interval=0.1)
    w.start()
    time.sleep(0.55)
    w.stop(timeout=1)
    assert 3 <= w.ticks <= 8


def test_the_loop_waits_a_full_interval_between_ticks(env):
    w = Counter(env.hub, interval=1.0)
    waits = []

    def fake_sleep(seconds):
        waits.append(seconds)
        env.clock.advance(seconds)
        if len(waits) == 4:
            w.stop_event.set()
    w._sleep = fake_sleep
    w.run()
    assert w.ticks == 4 and waits == [1.0] * 4


def test_each_worker_has_its_own_database_made_on_its_own_thread(env):
    env.real_pacing()
    a, b = Counter(env.hub), Counter(env.hub)
    a.start()
    b.start()
    assert wait_for(lambda: a.thread_ids and b.thread_ids)
    a.stop(timeout=1)
    b.stop(timeout=1)
    assert a.db_seen[0] is not b.db_seen[0]
    assert a.thread_ids == [a.ident] and b.thread_ids == [b.ident]


def test_an_exception_is_logged_once_and_the_worker_keeps_running(env):
    env.real_pacing()
    w = Counter(env.hub, interval=0.01, fail=3)
    w.start()
    assert wait_for(lambda: w.ticks >= 6)
    w.stop(timeout=1)
    assert len(env.errors) == 1 and "RuntimeError: boom" in env.errors[0] and env.errors[0].startswith(
        "ERROR Lockdown counter: ")
    assert w.ticks >= 6                      # it went on after the failures


def test_the_same_error_is_logged_again_after_a_minute(env):
    error = RuntimeError("same")
    env.hub.report("x", error)
    env.clock.advance(30)
    env.hub.report("x", error)
    assert len(env.errors) == 1
    env.clock.advance(31)
    env.hub.report("x", error)
    env.hub.report("y", error)                # another worker's is its own
    assert len(env.errors) == 3


def test_submit_runs_on_the_workers_thread_at_once(env):
    env.real_pacing()
    w = Counter(env.hub, interval=30)
    w.start()
    assert wait_for(lambda: w.ticks == 1)
    seen = []
    w.submit(lambda: seen.append(threading.get_ident()))
    assert wait_for(lambda: seen)
    assert seen == [w.ident] and w.ticks == 1             # no extra tick
    w.poke()
    assert wait_for(lambda: w.ticks == 2)
    w.stop(timeout=1)


def test_a_failing_command_is_logged_not_fatal(env):
    env.real_pacing()
    w = Counter(env.hub, interval=30)
    w.start()
    assert wait_for(lambda: w.ticks == 1)
    w.submit(lambda: 1 / 0)
    done = []
    w.submit(lambda: done.append(1))
    assert wait_for(lambda: done)
    w.stop(timeout=1)
    assert any("ZeroDivisionError" in e for e in env.errors)


# ---------------------------------------------------------------- the status worker

class CountingDatabase(Database):
    usage_lookups = 0

    def usage_lookup(self, now):
        type(self).usage_lookups += 1
        return super().usage_lookup(now)


def run_status(env, seconds, listeners=()):
    """Run a StatusWorker's real loop on a fake clock for `seconds` seconds. Returns (worker, statements per tick)."""
    w = workers.StatusWorker(env.hub)
    env.hub.listeners.extend(listeners)
    statements, per_tick = [], []

    def fake_sleep(delay):
        per_tick.append(len(statements))
        env.clock.advance(delay)
        if env.clock.mono() >= seconds:
            w.stop_event.set()
    w._sleep = fake_sleep
    w.db.conn.set_trace_callback(statements.append)
    w.run()
    return w, [b - a for a, b in zip([0] + per_tick, per_tick)]


def test_the_status_worker_reads_usage_once_per_tick(env, monkeypatch):
    env.hub.open_db = lambda: CountingDatabase(env.path, ui=True)
    CountingDatabase.usage_lookups = 0
    env.heartbeat()
    env.db.add_site("YouTube", ["youtube.com"])
    run_status(env, 3)
    assert CountingDatabase.usage_lookups == 3


def test_idle_one_snapshot_per_second_and_no_extra_queries(env):
    env.heartbeat()
    env.db.add_site("YouTube", ["youtube.com"])
    builds = []
    original = snapshot.build

    def spy(db, now, wall=None):
        builds.append(now)
        return original(db, now, wall)
    snapshot_build, snapshot.build = snapshot.build, spy
    try:
        w, per_tick = run_status(env, 5)
    finally:
        snapshot.build = snapshot_build
    assert len(builds) == 5 and len(per_tick) == 5          # exactly one snapshot per (fake) second
    assert len(set(per_tick[1:])) == 1                      # ... and every later tick is the same handful of queries
    print(f"database statements per idle status tick: {per_tick[-1]}")
    assert per_tick[-1] < 40
    assert env.hub.snapshot is not None and env.errors == []


def test_listeners_hear_old_and_new_and_a_failing_one_changes_nothing(env):
    heard = []

    def bad(old, new):
        raise ValueError("listener")
    run_status(env, 3, listeners=[bad, lambda old, new: heard.append((old, new))])
    assert len(heard) == 3 and heard[0][0] is None and heard[1][0] is heard[0][1]
    assert len(env.errors) == 1 and "ValueError: listener" in env.errors[0]


def test_poke_builds_a_snapshot_now(env):
    env.real_pacing()
    w = workers.StatusWorker(env.hub, interval=30)
    w.start()
    assert wait_for(lambda: env.hub.snapshot is not None)
    first = env.hub.snapshot
    env.heartbeat()
    w.poke()
    assert wait_for(lambda: env.hub.snapshot is not first)
    w.stop(timeout=1)
    assert env.hub.snapshot.running is True


class UsageSink:
    """A listener with the publisher's usage_tick surface: `visible` is the window being shown."""

    def __init__(self, visible=True):
        self.visible, self.ticks = visible, []

    def __call__(self, old, new):
        pass

    def usage_tick(self, payload):
        self.ticks.append(payload)


def test_a_shown_window_hears_todays_active_seconds_every_five_seconds(env):
    env.heartbeat()
    env.db.add_activity(f"{NOW.date()} 09:00", "code.exe", "", 600, 480)
    env.db.add_activity(f"{NOW.date()} 21:59", "code.exe", "", 60, 30)
    env.db.add_activity(f"{NOW.date() - timedelta(days=1)} 09:00", "code.exe", "", 600, 600)     # (not today's)
    sink = UsageSink()
    run_status(env, 12, listeners=[sink])
    assert sink.ticks == [{"active_sec": 510}] * 3                    # at 0 s, 5 s and 10 s
    assert env.errors == []


def test_a_hidden_window_costs_no_usage_query_and_gets_one_empty_tick(env):
    env.heartbeat()
    sink = UsageSink(visible=False)
    w, per_tick = run_status(env, 8, listeners=[sink])
    assert sink.ticks == [{}]                                         # once per hidden stretch: becomes dirty flags
    assert len(set(per_tick[1:])) == 1                                # (the same handful of statements as without a sink)


def test_the_ticks_resume_when_the_window_is_shown_again(env):
    env.heartbeat()
    sink = UsageSink(visible=False)
    w = workers.StatusWorker(env.hub)
    env.hub.listeners.append(sink)
    w.tick()
    w.tick()
    assert sink.ticks == [{}]
    sink.visible = True
    w.tick()                                                          # (the first shown tick answers at once)
    env.clock.advance(1)
    w.tick()
    assert sink.ticks == [{}, {"active_sec": 0}]
    sink.visible = False
    env.clock.advance(1)
    w.tick()
    assert sink.ticks == [{}, {"active_sec": 0}, {}]                  # a new hidden stretch flags again


def test_the_publisher_listener_pushes_usage_ticks_only_while_the_window_is_shown(env):
    from resident.publisher import Publisher

    class Window:
        def __init__(self):
            self.scripts = []

        def evaluate_js(self, script):
            self.scripts.append(script)
    window = Window()
    pub = Publisher(window, start=False, visible=False)
    listener = workers.publisher_listener(SimpleNamespace(publisher=pub))
    env.heartbeat()
    env.db.add_activity(f"{NOW.date()} 09:00", "code.exe", "", 600, 480)
    w = workers.StatusWorker(env.hub)
    env.hub.listeners.append(listener)
    w.tick()
    assert pub.flush() is False and window.scripts == []              # hidden: no push, only dirty flags kept for later
    assert pub._dirty == {"dashboard": True, "screentime": True}
    pub.set_visible(True)
    pub.flush()
    window.scripts.clear()
    env.clock.advance(5)
    w.tick()
    pub.flush()
    assert len(window.scripts) == 1 and '["usage_tick",{"active_sec":480}]' in window.scripts[0]
    env.clock.advance(5)
    w.tick()                                                          # (the same number again: dropped by the publisher)
    assert pub.flush() is False


class FakeTray:
    on_mode = None

    def __init__(self):
        self.calls = []

    def update(self, running, text, off=False):
        self.calls.append(("update", running, text, off))

    def set_paused(self, until):
        self.calls.append(("paused", until))

    def set_modes(self, modes_, active):
        self.calls.append(("modes", active))

    def set_update(self, version):
        self.calls.append(("update_available", version))


def test_the_tray_is_touched_only_when_its_fields_change(env):
    tray = FakeTray()
    listener = workers.tray_listener(tray)
    env.heartbeat()
    first = env.snap()
    listener(None, first)
    assert [c[0] for c in tray.calls] == ["update", "paused", "update_available"]       # (no mode hook: no menu)
    tray.calls.clear()
    env.clock.advance(1)
    listener(first, env.snap())
    assert tray.calls == []                                   # nothing changed: the icon is left alone
    pause.start(env.db, env.clock.t, 30, silent=False)
    listener(first, env.snap())
    assert ("paused", env.clock.t + timedelta(minutes=30)) in tray.calls
    tray.on_mode = lambda mode_id: None
    tray.calls.clear()
    modes.start(env.db, "work", env.clock.t, None)
    listener(first, env.snap())
    assert ("modes", "work") in tray.calls


def test_the_publisher_gets_only_the_delta(env):
    pushed = []
    win = SimpleNamespace(publisher=SimpleNamespace(publish=lambda event, payload: pushed.append((event, payload))))
    listener = workers.publisher_listener(win)
    env.heartbeat()
    first = env.snap()
    listener(None, first)
    assert pushed[0] == ("status", first.public())
    pushed.clear()
    env.clock.advance(1)
    listener(first, env.snap())
    assert pushed == []                                       # unchanged: nothing is sent
    env.heartbeat(ago=40)
    listener(first, env.snap())
    assert pushed == [("status", {"running": False})]


# ---------------------------------------------------------------- notices and the mute rule

def test_notices_pass_when_nothing_mutes(env):
    assert env.hub.notify("hello", action=("Go", print)) is True
    assert env.shown == [("hello", ("Go", print), None, False)]


def test_a_muting_mode_holds_notices_but_not_forced_ones(env):
    modes.start(env.db, "dnd", NOW, None)             # Do Not Disturb: a mode with mute
    env.snap()
    assert env.hub.notify("quiet") is False
    assert env.hub.notify("urgent", force=True) is True
    assert env.messages == ["urgent"]


def test_a_silent_pause_holds_everything_even_forced(env):
    pause.start(env.db, NOW, 30, silent=True)
    env.snap()
    assert env.hub.notify("a") is False and env.hub.notify("b", force=True) is False
    assert env.shown == []
    pause.start(env.db, NOW, 30, silent=False)         # a pause that does not silence
    env.snap()
    assert env.hub.notify("c") is True


def test_a_mode_that_does_not_mute_lets_notices_through(env):
    modes.start(env.db, "work", NOW, None)
    env.snap()
    assert env.hub.notify("fine") is True


def test_windows_do_not_disturb_is_asked_for_not_applied(env):
    env.win.dnd = True
    assert env.hub.notify.dnd() is True
    assert env.hub.notify("still shown, as before") is True


# ---------------------------------------------------------------- block events and alerts

@pytest.fixture
def blocks(env):
    """A block-event worker over a database with an app (Discord) and a site (YouTube) blocked for good."""
    app = env.db.add_item("Discord", ["discord.exe"], "app", rules=[{"rule_type": "permanent"}])
    site = env.db.add_site("YouTube", ["youtube.com"])
    alerter = workers.Alerter(env.hub)
    w = workers.BlockEventWorker(env.hub, alerter)
    w.setup()
    env.app, env.site, env.alerter, env.worker = app, site, alerter, w
    yield env
    alerter.close()


def event(env, host, item_id, reason="permanent", until=None):
    env.db.add_block_event(host, item_id, {"discord.exe": "Discord"}.get(host, "YouTube"), reason, until,
                           now=env.clock.t)


def test_an_opened_blocked_app_is_closed_and_announced(blocks):
    event(blocks, "discord.exe", blocks.app)
    blocks.worker.tick()
    assert blocks.win.closed_apps == ["discord.exe"]
    assert blocks.messages == ["Discord is permanently blocked."]


def test_old_events_are_not_announced_after_a_start(env):
    env.db.add_block_event("discord.exe", None, "Discord", "permanent", None, now=NOW)
    alerter = workers.Alerter(env.hub)
    w = workers.BlockEventWorker(env.hub, alerter)
    w.setup()
    w.tick()
    assert env.win.closed_apps == [] and env.shown == []


def test_a_program_file_closes_the_windows_of_its_processes(blocks):
    blocks.usage.pids["game.jar"] = {41, 42}
    blocks.db.add_item("Game", ["game.jar"], "app", rules=[{"rule_type": "permanent"}])
    blocks.db.add_block_event("game.jar", None, "Game", "permanent", None, now=NOW)
    blocks.worker.tick()
    assert blocks.win.closed_windows == [{41, 42}] and blocks.win.closed_apps == []


def test_cooldown_holds_the_same_item_but_not_another(blocks):
    other = blocks.db.add_item("Steam", ["steam.exe"], "app", rules=[{"rule_type": "permanent"}])
    event(blocks, "discord.exe", blocks.app)
    blocks.worker.tick()
    blocks.clock.advance(60)
    event(blocks, "discord.exe", blocks.app)
    blocks.db.add_block_event("steam.exe", other, "Steam", "permanent", None, now=NOW)
    blocks.worker.tick()
    assert blocks.win.closed_apps == ["discord.exe", "discord.exe", "steam.exe"]   # still closed every time
    assert blocks.messages == ["Discord is permanently blocked.", "Steam is permanently blocked."]
    blocks.clock.advance(30 * 60)                                       # the default cooldown is 30 minutes
    event(blocks, "discord.exe", blocks.app)
    blocks.worker.tick()
    assert blocks.messages[-1] == "Discord is permanently blocked." and len(blocks.messages) == 3


def test_the_cooldown_survives_a_restart(blocks):
    event(blocks, "discord.exe", blocks.app)
    blocks.worker.tick()
    blocks.alerter.close()
    restarted = workers.BlockEventWorker(blocks.hub, workers.Alerter(blocks.hub))
    restarted.setup()
    blocks.clock.advance(60)
    event(blocks, "discord.exe", blocks.app)
    restarted.tick()
    assert blocks.messages == ["Discord is permanently blocked."]
    restarted.alerter.close()


def test_an_item_set_to_off_never_notifies_and_one_set_to_on_ignores_the_reason_switch(blocks):
    quiet = blocks.db.add_item("Quiet", ["quiet.exe"], "app", rules=[{"rule_type": "permanent"}], notify="off")
    loud = blocks.db.add_item("Loud", ["loud.exe"], "app", rules=[{"rule_type": "permanent"}], notify="on")
    blocks.db.set_setting("notify.enabled.permanent", "0")             # the reason is switched off ...
    blocks.db.add_block_event("quiet.exe", quiet, "Quiet", "permanent", None, now=NOW)
    blocks.db.add_block_event("discord.exe", blocks.app, "Discord", "permanent", None, now=NOW)
    blocks.db.add_block_event("loud.exe", loud, "Loud", "permanent", None, now=NOW)
    blocks.worker.tick()
    assert blocks.win.closed_apps == ["quiet.exe", "discord.exe", "loud.exe"]
    assert blocks.messages == ["Loud is permanently blocked."]         # ... only the item set to On still speaks


def test_the_message_template_and_until(blocks):
    blocks.db.set_setting("notify.msg.schedule", "{site}: {reason} - back at {until}.")
    event(blocks, "discord.exe", blocks.app, "schedule", NOW + timedelta(minutes=90))
    blocks.worker.tick()
    assert blocks.messages == ["Discord: blocked at this time - back at 23:30."]


def test_a_site_is_announced_only_when_you_were_opening_it(blocks):
    event(blocks, "youtube.com", blocks.site)
    blocks.worker.tick()
    assert blocks.messages == []                                # a background hit: nothing in front showed it
    blocks.usage.shown = {"youtube.com"}                        # the tab check saw it in a browser window
    blocks.clock.advance(1)
    blocks.worker.tick()
    assert blocks.messages == ["YouTube is permanently blocked."]
    blocks.clock.advance(1)
    blocks.worker.tick()
    assert len(blocks.messages) == 1                            # said once


def test_a_background_site_hit_is_forgotten_after_ten_seconds(blocks):
    event(blocks, "youtube.com", blocks.site)
    blocks.worker.tick()
    assert len(blocks.worker.visits) == 1
    blocks.clock.advance(11)
    blocks.worker.tick()
    assert blocks.worker.visits == {}
    blocks.usage.shown = {"youtube.com"}
    blocks.clock.advance(1)
    blocks.worker.tick()
    assert blocks.messages == []


def test_last_alert_is_written_at_most_every_five_seconds(blocks, monkeypatch):
    writes = []
    original = workers.Alerter._save
    monkeypatch.setattr(workers.Alerter, "_save", lambda self, wall: (writes.append(wall), original(self, wall))[1])
    ids = [blocks.db.add_item(f"App{i}", [f"app{i}.exe"], "app", rules=[{"rule_type": "permanent"}])
           for i in range(6)]
    for i, item in enumerate(ids):                     # six different items in three seconds
        blocks.db.add_block_event(f"app{i}.exe", item, f"App{i}", "permanent", None, now=NOW)
        blocks.worker.tick()
        blocks.clock.advance(0.5)
    assert len(blocks.messages) == 6 and len(writes) == 1          # the first alert wrote at once, the rest wait
    blocks.clock.advance(5)
    blocks.worker.tick()                                           # (the tick flushes what is due)
    assert len(writes) == 2
    saved = json.loads(blocks.db.get_setting("notify.last_alert"))
    assert set(saved) == {str(i) for i in ids}
    blocks.worker.tick()
    assert len(writes) == 2                                        # nothing new to write


def test_the_final_flush_writes_what_is_waiting(blocks):
    event(blocks, "discord.exe", blocks.app)
    blocks.worker.tick()
    blocks.clock.advance(1)
    other = blocks.db.add_item("Steam", ["steam.exe"], "app", rules=[{"rule_type": "permanent"}])
    blocks.db.add_block_event("steam.exe", other, "Steam", "permanent", None, now=NOW)
    blocks.worker.tick()
    assert str(other) not in json.loads(blocks.db.get_setting("notify.last_alert"))
    blocks.alerter.close()
    assert str(other) in json.loads(blocks.db.get_setting("notify.last_alert"))


# ---------------------------------------------------------------- the watcher

@pytest.fixture
def watch(env):
    env.db.set_setting("notify.warn.minutes", "15")
    env.db.set_setting("notify.warn.repeat_sec", "30")
    item = env.db.add_item("Reddit", ["reddit.com"], "site", rules=[sched("22:10", "23:00")])
    w = workers.WatcherWorker(env.hub)
    w.setup()
    env.item, env.watcher = item, w
    env.calls = []
    env.hub.notify = lambda message, force=False, action=None, **kw: env.calls.append((message, force, action))
    return env


def step(env, seconds=1):
    env.clock.advance(seconds)
    env.snap()
    env.watcher.tick()


def test_upcoming_warning_repeats_every_30_seconds_while_you_use_it(watch):
    watch.hub.in_use = lambda: {watch.item}
    times = []
    watch.snap()
    watch.watcher.tick()
    for second in range(1, 100):
        before = len(watch.calls)
        step(watch)
        if len(watch.calls) > before:
            times.append(second)
    assert [c[0] for c in watch.calls][0] == "Reddit will be blocked in 10 min (22:10)."
    assert [0] + times == [0, 30, 60, 90]                        # the first at once, then every 30 s
    assert all(force for _, force, _ in watch.calls)             # it is what you are using: gets through a mute


def test_upcoming_warning_is_once_when_you_are_not_using_it(watch):
    watch.snap()
    watch.watcher.tick()
    for _ in range(100):
        step(watch)
    assert len(watch.calls) == 1 and watch.calls[0][1] is False


def test_warnings_can_be_switched_off(watch):
    watch.db.set_setting("notify.warn.enabled", "0")
    watch.watcher.tick()
    watch.snap()
    watch.watcher.tick()
    assert watch.calls == []


def test_block_started_notice(watch):
    watch.hub.in_use = lambda: {watch.item}
    watch.db.set_setting("notify.warn.enabled", "0")
    watch.snap()
    watch.watcher.tick()
    watch.clock.t = NOW.replace(minute=10, second=1)
    watch.snap()
    watch.watcher.tick()
    assert [c[0] for c in watch.calls] == ["Reddit is now blocked until 23:00."]
    assert watch.calls[0][1] is True                              # it went while you were in it


def test_switching_off_resets_the_baseline(watch):
    watch.db.set_setting("notify.warn.enabled", "0")
    watch.db.add_site("Always", ["always.test"])
    watch.snap()
    watch.watcher.tick()
    antibypass.switch_off(watch.db, NOW)
    watch.clock.advance(1)
    watch.snap()
    watch.watcher.tick()
    antibypass.switch_on(watch.db)
    watch.clock.advance(1)
    watch.snap()
    watch.watcher.tick()
    assert watch.calls == []                                      # rules you already had are not "just started"


def test_the_same_snapshot_is_checked_once(watch):
    watch.snap()
    spy = []
    original = watch.watcher.watcher.check
    watch.watcher.watcher.check = lambda *a, **k: (spy.append(1), original(*a, **k))[1]
    watch.watcher.tick()
    watch.watcher.tick()
    assert spy == [1]


def test_pomodoro_phase_notices(watch):
    modes.start(watch.db, "focus", NOW, None)
    forced = []
    for minutes, expected in ((10, None), (26, "Break until 22:30 - blocks are off."),
                              (27, None), (31, "Focus - round 2 of 4, until 22:55. Blocks are back on.")):
        watch.clock.t = NOW + timedelta(minutes=minutes)
        watch.calls.clear()
        watch.snap()
        watch.watcher.tick()
        phase = [c for c in watch.calls if c[0].startswith(("Focus -", "Break", "Long break"))]
        assert [c[0] for c in phase] == ([expected] if expected else [])
        forced += [c[1] for c in phase]
    assert forced == [True, True]                         # phase notices get through a mute
    watch.calls.clear()
    watch.clock.t = NOW + timedelta(minutes=131)                   # 4 rounds are 130 minutes
    watch.snap()
    watch.watcher.tick()
    assert [c[0] for c in watch.calls if "session" in c[0]] == ["Focus session done."]


def test_starting_or_stopping_a_mode_by_hand_announces_no_phase(watch):
    modes.start(watch.db, "focus", NOW, None)
    watch.snap()
    watch.watcher.tick()
    modes.stop(watch.db, NOW + timedelta(minutes=5))              # stopped early: no "session done"
    watch.clock.t = NOW + timedelta(minutes=5)
    watch.snap()
    watch.watcher.tick()
    modes.start(watch.db, "focus", NOW + timedelta(minutes=6), None)       # started again: its first phase is silent
    watch.clock.t = NOW + timedelta(minutes=6)
    watch.snap()
    watch.watcher.tick()
    assert [c for c in watch.calls if "session" in c[0] or "Focus -" in c[0] or "Break" in c[0]] == []


def test_the_weekly_summary_once_with_see_the_week(watch):
    watch.db.set_setting("notify.warn.enabled", "0")
    watch.db.set_setting("digest.day", "0")
    watch.db.set_setting("digest.time", "19:00")
    opened = []
    watch.hub.open_page = opened.append
    watch.snap()
    watch.watcher.tick()
    summaries = [c for c in watch.calls if c[0].startswith("This week:")]
    assert len(summaries) == 1 and summaries[0][2][0] == "See the week"
    summaries[0][2][1]()
    assert opened == ["Screen Time"]
    for _ in range(12):                                            # past the settings refresh: not again this week
        step(watch)
    assert len([c for c in watch.calls if c[0].startswith("This week:")]) == 1
    assert digest.due(watch.db, watch.clock.t) is False


# ---------------------------------------------------------------- the warning for the app in front

def front_calls(watch):
    """Replace hub.notify with a recorder that keeps the keywords too: (message, force, front/sound keywords)."""
    got = []
    watch.hub.notify = lambda message, force=False, action=None, **kw: got.append((message, force, kw))
    return got


def to_last_minute(watch):
    """From 22:00 (10 min before Reddit's 22:10 block) to 22:09:05."""
    watch.snap()
    watch.watcher.tick()
    watch.clock.advance(9 * 60 + 5)
    watch.snap()
    watch.watcher.tick()


def test_the_app_in_front_is_warned_forced_on_the_front_road_with_the_sound(watch):
    watch.db.set_setting("notify.warn.repeat_sec", "0")
    watch.hub.in_use = lambda: {watch.item}
    got = front_calls(watch)
    to_last_minute(watch)
    assert got == [("Reddit will be blocked in 10 min (22:10).", True, {}),                        # 10 min: the ordinary one
                   ("Reddit will be blocked in 1 min (22:10).", True, {"front": True, "sound": True})]
    for _ in range(3):                                                                              # once per step
        step(watch, 5)
    assert len(got) == 2


def test_the_sound_follows_its_setting(watch):
    watch.db.set_setting("notify.warn.repeat_sec", "0")
    watch.db.set_setting("notify.front.sound", "0")
    watch.hub.in_use = lambda: {watch.item}
    got = front_calls(watch)
    to_last_minute(watch)
    assert got[-1][2] == {"front": True, "sound": False}


def test_switched_off_the_front_road_is_never_used(watch):
    watch.db.set_setting("notify.warn.repeat_sec", "0")
    watch.db.set_setting("notify.front.enabled", "0")
    watch.hub.in_use = lambda: {watch.item}
    got = front_calls(watch)
    to_last_minute(watch)
    assert got == [("Reddit will be blocked in 10 min (22:10).", True, {})]                         # as before: nothing more


def test_what_is_not_in_front_gets_no_front_warning(watch):
    watch.db.set_setting("notify.warn.repeat_sec", "0")
    got = front_calls(watch)
    to_last_minute(watch)
    assert all(kw == {} and force is False for _m, force, kw in got) and len(got) == 1


def real_notifier(watch):
    """The real Notifier (the `watch` fixture swaps it for a recorder) with a sink that records what it is told."""
    shown = []
    watch.hub.notify = workers.Notifier(watch.hub)
    watch.hub.notify.show = lambda message, action=None, actions=None, sticky=False, front=False, sound=False: \
        shown.append((message, front, sound))
    return shown


def test_the_front_warning_gets_through_a_muting_mode_and_asks_for_the_front_road(watch):
    watch.db.set_setting("notify.warn.repeat_sec", "0")
    watch.hub.in_use = lambda: {watch.item}
    shown = real_notifier(watch)
    modes.start(watch.db, "dnd", NOW, None)                       # Do Not Disturb: a mode that mutes
    to_last_minute(watch)
    assert watch.hub.snapshot.mode["mute"] is True
    assert shown == [("Reddit will be blocked in 10 min (22:10).", False, False),     # (what you are using: forced too)
                     ("Reddit will be blocked in 1 min (22:10).", True, True)]


def test_a_muting_mode_holds_what_you_are_not_in_but_not_the_front_warning(watch):
    shown = real_notifier(watch)
    modes.start(watch.db, "dnd", NOW, None)
    watch.snap()
    assert watch.hub.notify("quiet") is False
    assert watch.hub.notify("front", front=True, sound=True) is True                # (front counts as forced)
    assert shown == [("front", True, True)]


def test_a_silent_pause_still_holds_the_front_warning(watch):
    watch.db.set_setting("notify.warn.repeat_sec", "0")
    watch.hub.in_use = lambda: {watch.item}
    shown = real_notifier(watch)
    pause.start(watch.db, NOW, 30, silent=True)                   # nothing gets blocked meanwhile - and nothing is said
    to_last_minute(watch)
    assert shown == [] and watch.hub.notify("x", front=True) is False


# ---------------------------------------------------------------- minimise and the strict break

@pytest.fixture
def mini(env):
    env.heartbeat()
    item = env.db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "permanent"}], block_type="minimize")
    env.db.add_item("Closer", ["closer.exe"], "app", rules=[{"rule_type": "permanent"}])      # blocked, but closed
    alerter = workers.Alerter(env.hub)
    w = workers.MinimizeWorker(env.hub, alerter)
    env.item, env.alerter, env.worker = item, alerter, w
    yield env
    alerter.close()


def test_a_minimised_app_in_front_is_sent_down_and_announced(mini):
    mini.snap()
    mini.win.front = (7, "game.exe")
    mini.worker.tick()
    assert mini.win.minimized_apps == ["game.exe"]
    assert mini.messages == ["Game is permanently blocked."]
    assert mini.worker._db is None                                  # no database was opened for any of that


def test_other_apps_in_front_are_left_alone(mini):
    mini.snap()
    mini.win.front = (7, "notepad.exe")
    mini.worker.tick()
    mini.win.front = (7, "closer.exe")                              # blocked, but not a "Minimize" one
    mini.worker.tick()
    assert mini.win.minimized_apps == []


def test_nothing_is_asked_of_windows_when_no_app_is_minimised(mini):
    mini.db.execute = None
    mini.db.conn.execute("UPDATE blocked_items SET block_type = NULL")
    mini.db.conn.commit()
    mini.snap()
    asked = []
    mini.win.foreground = lambda: asked.append(1) or (0, "")
    mini.worker.tick()
    assert asked == []


def test_nothing_is_minimised_while_off_or_paused_or_before_the_first_snapshot(mini):
    mini.win.front = (7, "game.exe")
    mini.worker.tick()                                              # no snapshot yet
    antibypass.switch_off(mini.db, NOW)
    mini.snap()
    mini.worker.tick()
    antibypass.switch_on(mini.db)
    pause.start(mini.db, NOW, 30, silent=False)
    mini.snap()
    mini.worker.tick()
    assert mini.win.minimized_apps == []
    pause.resume(mini.db)
    mini.snap()
    mini.worker.tick()
    assert mini.win.minimized_apps == ["game.exe"]


def test_a_limit_written_by_the_tracker_asks_for_a_new_snapshot_at_once(mini):
    pokes = []
    mini.hub.status_worker = SimpleNamespace(poke=lambda: pokes.append(1))
    mini.snap()
    mini.worker.tick()
    mini.worker.tick()
    assert pokes == []
    mini.usage.limit_writes += 1
    mini.worker.tick()
    mini.worker.tick()
    assert pokes == [1]


def test_a_strict_break_sends_windows_down_and_says_so_every_12_seconds(mini):
    mini.hub.engine = SimpleNamespace(break_until=NOW + timedelta(minutes=5))
    mini.win.break_result = True
    mini.snap()
    mini.worker.tick()
    assert mini.messages == ["Break in progress - 5 min left."] and mini.shown[0][1] is None
    for _ in range(11):
        mini.clock.advance(1)
        mini.worker.tick()
    assert len(mini.messages) == 1 and mini.win.break_minimizes == 12   # minimised each time, said once
    mini.clock.advance(2)
    mini.worker.tick()
    assert len(mini.messages) == 2
    mini.clock.advance(6 * 60)                                          # the break is over
    before = mini.win.break_minimizes
    mini.worker.tick()
    assert mini.win.break_minimizes == before


def test_the_break_notice_is_forced_through_a_mute(mini):
    modes.start(mini.db, "dnd", NOW, None)
    mini.snap()
    mini.hub.engine = SimpleNamespace(break_until=NOW + timedelta(minutes=2))
    mini.win.break_result = True
    mini.worker.tick()
    assert mini.messages == ["Break in progress - 2 min left."]


# ---------------------------------------------------------------- reminders

@pytest.fixture
def rem(env):
    env.heartbeat()
    ui = workers.RecordingReminderUI()
    w = workers.ReminderWorker(env.hub, ui)
    w.setup()
    env.ui, env.worker = ui, w
    return env


def test_the_reminder_engine_is_driven_and_shows_through_the_ui(rem):
    reminders.save(rem.db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "every_sec": 60})
    rem.snap()
    assert rem.ui.engine is rem.worker.engine and rem.ui.worker is rem.worker and rem.hub.engine is rem.ui.engine
    delays = []
    for _ in range(40):
        delay = rem.worker.tick()
        delays.append(delay)
        rem.clock.advance(delay)
    popups = [c for c in rem.ui.calls if c[0] == "popup"]
    assert popups and popups[0][1][:3] == ("break", "Time for a break", popups[0][1][2])
    assert delays[0] == reminders.TICK_SEC and all(0.5 <= d <= reminders.TICK_SEC for d in delays)


def test_no_reminders_while_off_or_before_the_first_snapshot(rem):
    ticks = []
    rem.worker.engine.tick = lambda *a, **k: ticks.append(k)
    assert rem.worker.tick() == reminders.TICK_SEC                  # no snapshot yet
    antibypass.switch_off(rem.db, NOW)
    rem.snap()
    assert rem.worker.tick() == reminders.TICK_SEC
    assert ticks == []


def test_quiet_when_a_mode_mutes_or_windows_says_do_not_disturb(rem):
    ticks = []
    rem.worker.engine.tick = lambda now, idle, full, dt, quiet: ticks.append((idle, full, dt, quiet))
    rem.worker.engine.next_tick = lambda now: 5.0
    rem.win.idle, rem.win.fullscreen = 12.0, True
    rem.snap()
    rem.worker.tick()
    modes.start(rem.db, "dnd", NOW, None)
    rem.snap()
    rem.worker.tick()
    modes.stop(rem.db, NOW)
    rem.win.dnd = True
    rem.snap()
    rem.worker.tick()
    assert [t[3] for t in ticks] == [False, True, True] and ticks[0][:2] == (12.0, True)


def test_dt_is_the_time_that_really_passed_up_to_the_planned_wait(rem):
    ticks = []
    rem.worker.engine.tick = lambda now, idle, full, dt, quiet: ticks.append(dt)
    rem.worker.engine.next_tick = lambda now: 5.0
    rem.snap()
    rem.worker.tick()
    rem.clock.advance(2)                       # woken early (an answer came in)
    rem.worker.tick()
    rem.clock.advance(60)                      # (the PC slept)
    rem.worker.tick()
    assert ticks == [5.0, 2.0, 5.0]


def test_answers_run_on_the_workers_thread(env):
    env.real_pacing()
    ui = workers.RecordingReminderUI()
    w = workers.ReminderWorker(env.hub, ui, interval=30)
    env.heartbeat()
    env.snap()
    w.start()
    assert wait_for(lambda: w.engine is not None and env.hub.engine is w.engine)
    answered = []
    w.engine.answer = lambda key, action: answered.append((key, action, threading.get_ident()))
    w.engine.held_answer = lambda key, action: answered.append(("held", key, action, threading.get_ident()))
    w.answer("break", "snooze")
    w.held_answer("sleep", "dismiss")
    assert wait_for(lambda: len(answered) == 2)
    w.stop(timeout=1)
    assert answered == [("break", "snooze", w.ident), ("held", "sleep", "dismiss", w.ident)]


def test_the_stand_in_ui_remembers_calls():
    ui = workers.RecordingReminderUI()
    ui.popup("break", "Time", "text", [("Start", "start")])
    ui.toast("hi")
    ui.break_end()
    assert [c[0] for c in ui.calls] == ["popup", "toast", "break_end"]
    with pytest.raises(AttributeError):
        ui.nonsense()


# ---------------------------------------------------------------- updates

FOUND = {"version": "99.0.0", "url": "https://example.test/r", "asset": "LockdownSetup.exe", "size": 123, "newer": True}


class Latest:
    def __init__(self, answer=FOUND):
        self.answer, self.calls = answer, 0

    def __call__(self):
        self.calls += 1
        return self.answer


@pytest.fixture
def upd(env):
    latest = Latest()
    w = workers.UpdateWorker(env.hub, latest=latest)
    env.latest, env.worker = latest, w
    return env


def test_update_timing():
    assert workers.UpdateWorker.start_delay == 90 and workers.UpdateWorker.interval == 60


def test_a_due_check_finds_an_update_and_says_so_once(upd):
    upd.worker.tick()
    assert upd.latest.calls == 1
    assert json.loads(upd.db.get_setting(updates.FOUND_KEY))["version"] == "99.0.0"
    assert upd.db.get_setting(updates.LAST_KEY) == NOW.strftime(TIME_FMT)
    message, action, actions, sticky = upd.shown[0]
    assert message == f"Lockdown 99.0.0 is available - you have {workers.VERSION}." and sticky is True
    assert [label for label, _ in actions] == ["Open the page", "Remind me later"]       # (no installer wired yet)
    assert upd.db.get_setting(updates.SEEN_KEY) == "99.0.0"
    upd.clock.advance(60)
    upd.worker.tick()
    assert upd.latest.calls == 1 and len(upd.shown) == 1            # not asked again, not said again


def test_install_is_offered_when_it_is_wired(upd):
    installed = []
    upd.hub.install_update = installed.append
    upd.worker.tick()
    label, call = upd.shown[0][2][0]
    assert label == "Install"
    call()
    assert installed[0]["version"] == "99.0.0"


def test_auto_check_off_makes_no_request(upd):
    upd.db.set_setting(updates.AUTO_KEY, "0")
    upd.worker.tick()
    assert upd.latest.calls == 0 and upd.shown == []


def test_tell_me_off_checks_but_does_not_say_it(upd):
    upd.db.set_setting(updates.NOTIFY_KEY, "0")
    upd.worker.tick()
    assert upd.latest.calls == 1 and upd.shown == []
    assert updates.banner(upd.db, NOW)["version"] == "99.0.0"       # the banner / About dot / tray still know


def test_a_failed_check_is_not_written_down_and_waits_five_minutes(upd):
    upd.latest.answer = None
    upd.worker.tick()
    assert upd.latest.calls == 1 and upd.db.get_setting(updates.LAST_KEY) is None
    upd.clock.advance(60)
    upd.worker.tick()
    assert upd.latest.calls == 1                                    # (updates.RETRY_MIN)
    upd.clock.advance(5 * 60)
    upd.worker.tick()
    assert upd.latest.calls == 2


def test_a_check_is_not_repeated_within_the_hours(upd):
    upd.latest.answer = {**FOUND, "newer": False}
    upd.worker.tick()
    upd.clock.advance(3600)
    upd.worker.tick()
    assert upd.latest.calls == 1
    upd.clock.advance(6 * 3600)                                     # updates.EVERY_HOURS
    upd.worker.tick()
    assert upd.latest.calls == 2


def test_opening_the_window_checks_again_if_it_has_been_a_while(upd):
    upd.latest.answer = {**FOUND, "newer": False}
    upd.worker.tick()
    upd.clock.advance(10 * 60)
    upd.worker.check_soon()
    assert upd.worker._poked is True
    upd.worker.tick()
    assert upd.latest.calls == 2
    upd.worker.tick()                                               # (the next ordinary tick is back to the hours)
    assert upd.latest.calls == 2


def test_a_muted_notice_is_not_marked_as_said(upd):
    modes.start(upd.db, "dnd", NOW, None)
    upd.snap()
    upd.worker.tick()
    assert upd.shown == [] and upd.db.get_setting(updates.SEEN_KEY, "") == ""
    modes.stop(upd.db, NOW)
    upd.snap()
    upd.clock.advance(60)
    upd.worker.tick()
    assert len(upd.shown) == 1                                      # it comes once nothing mutes


def test_windows_do_not_disturb_holds_the_notice_back(upd):
    upd.win.dnd = True
    upd.worker.tick()
    assert upd.shown == [] and upd.db.get_setting(updates.SEEN_KEY, "") == ""
    upd.win.dnd = False
    upd.clock.advance(60)
    upd.worker.tick()
    assert len(upd.shown) == 1


def test_remind_me_later_snoozes_on_the_worker_and_comes_back(upd):
    upd.worker.tick()
    upd.shown[0][2][1][1]()                                          # "Remind me later"
    upd.worker._drain()
    assert updates.snoozed(upd.db, NOW) and updates.banner(upd.db, NOW) is None
    assert upd.db.get_setting(updates.SEEN_KEY) == ""                # so it will say it again afterwards
    upd.clock.advance(60)
    upd.worker.tick()
    assert len(upd.shown) == 1                                       # still snoozed
    upd.clock.advance(updates.SNOOZE_HOURS * 3600)
    upd.worker.tick()
    assert len(upd.shown) == 2


# ---------------------------------------------------------------- bad-word notices

class Scheduler:
    def __init__(self):
        self.jobs = []

    def __call__(self, delay, fn):
        job = SimpleNamespace(delay=delay, fn=fn, cancelled=False)
        job.cancel = lambda: setattr(job, "cancelled", True)
        self.jobs.append(job)
        return job

    def fire(self):
        job = self.jobs[-1]
        job.fn()


def test_the_first_word_notice_is_at_once_and_the_rest_are_summed_up(env):
    schedule = Scheduler()
    notices = workers.WordNotices(env.hub, schedule=schedule)
    notices.add("porn*", "close")
    assert env.messages == ['"porn" is a blocked word - closing the tab.']
    assert schedule.jobs[0].delay == 2.5
    for word in ("sex", "nsfw", "xxx"):
        notices.add(word, "back")
    assert len(env.messages) == 1                                   # held for the summary
    schedule.fire()
    assert env.messages[-1] == "Closed 3 more tabs with blocked words." and len(schedule.jobs) == 2
    notices.add("one", "close")
    schedule.fire()
    assert env.messages[-1] == "Closed 1 more tab with blocked words."
    schedule.fire()                                                 # nothing more came: the window closes
    assert len(env.messages) == 3 and len(schedule.jobs) == 3
    notices.add("again", "back")
    assert env.messages[-1] == '"again" is a blocked word - going back.'


def test_word_notices_follow_the_mute_rule_and_stop_cancels(env):
    schedule = Scheduler()
    notices = workers.WordNotices(env.hub, schedule=schedule)
    modes.start(env.db, "dnd", NOW, None)
    env.snap()
    notices.add("word", "close")
    assert env.messages == []
    notices.stop()
    assert schedule.jobs[0].cancelled


# ---------------------------------------------------------------- all of them together

class FakeThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.in_use = {5}
        self.stopped = False

    def run(self):
        self.stop_event.wait()

    def stop(self):
        self.stopped = True
        self.stop_event.set()


def test_workers_start_and_all_stop_within_a_second(env, monkeypatch):
    env.real_pacing()
    monkeypatch.setattr(updates, "remove_downloads", lambda *a, **k: [])
    tracker, guard, retention = FakeThread(), FakeThread(), FakeThread()
    pool = workers.Workers(hub=env.hub, tracker=tracker, word_guard=guard, retention=retention)
    pool.start()
    assert wait_for(lambda: env.hub.snapshot is not None)
    assert env.hub.status_worker is pool.status
    assert wait_for(lambda: pool.reminders.engine is not None)
    assert wait_for(lambda: tracker.is_alive() and guard.is_alive() and retention.is_alive())
    assert env.hub.in_use() == {5}
    started = time.monotonic()
    pool.stop()
    assert time.monotonic() - started < 1
    assert not any(w.is_alive() for w in pool.workers)
    assert tracker.stopped and guard.stop_event.is_set() and retention.stop_event.is_set()
    assert pool.alerter._db is None
    assert env.errors == []


def test_a_desktop_job_that_cannot_start_is_logged_and_the_workers_carry_on(env, monkeypatch):
    import sys
    env.real_pacing()
    monkeypatch.setitem(sys.modules, "uiautomation", None)         # (importing it now raises ImportError)
    pool = workers.Workers(hub=env.hub, word_guard=FakeThread(), retention=FakeThread())
    pool.start()
    assert wait_for(lambda: env.hub.snapshot is not None and env.errors)
    pool.stop()
    assert "ModuleNotFoundError" in env.errors[0] and env.errors[0].startswith("ERROR Lockdown startup: ")
    assert pool.tracker is None and not pool.word_guard.is_alive()


def test_stopping_workers_that_never_started_is_harmless(env):
    pool = workers.Workers(hub=env.hub)
    pool.stop()


# ---------------------------------------------------------------- hygiene

HEAVY = {"customtkinter", "tkinter", "uiautomation", "comtypes", "PIL", "pystray", "webview", "win32api", "win32gui",
         "gui"}


@pytest.mark.parametrize("name", ["resident/snapshot.py", "resident/workers.py", "agent.py"])
def test_no_heavy_module_is_imported_at_the_top(name):
    tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            top.add(node.module.split(".")[0])
    assert not top & HEAVY, top & HEAVY


def test_the_agent_starts_the_workers_once_the_gui_is_up_and_stops_them_on_exit():
    tree = ast.parse((SRC / "agent.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run")
    ready = next(n for n in ast.walk(run) if isinstance(n, ast.FunctionDef) and n.name == "ready")
    start_up = next(n for n in ast.walk(run) if isinstance(n, ast.FunctionDef) and n.name == "start_up")

    def calls(node):
        return {ast.unparse(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)}
    assert "start_up" in calls(ready)                       # (ready = the GUI loop is up; start_up = its work)
    assert "pool.start" in calls(start_up) and "workers.tray_listener" in ast.unparse(start_up)   # (the TrayKeeper's listener)
    assert "pool.stop" in calls(run)
    # (and `main` returns before any of it when another instance owns the port)
    first_acquire = next(i for i, n in enumerate(main.body) if "single_instance.acquire" in ast.unparse(n))
    assert all("pool" not in ast.unparse(n) and "_run" not in ast.unparse(n) for n in main.body[:first_acquire])
