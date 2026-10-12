"""Grey it on the PC (resident/grayscale.py, workers.GreyWorker): the screen's colour - what GreyScreen asks of the
Magnification API (a fake backend here) and when the GreyWorker wants grey: an app blocked with "Grey it" in front, bedtime.

What only Windows can prove: the real Magnification API on real hardware (the matrix, its direction, MagUninitialize), several
monitors, HDR, exclusive full-screen games."""
import sys
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

import antibypass
import emergency
import pause
import reminders
from resident import grayscale, snapshot, workers
from resident.grayscale import GREY, IDENTITY, FakeBackend, GreyScreen, same
from test_rebuild_workers import NOW, Env

SEPIA = tuple(0.5 if i % 6 == 0 else 0.0 for i in range(25))      # some other colour effect (somebody else's)


def wait_for(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def screen(backend):
    s = GreyScreen(backend, reassert_sec=60)
    yield s
    s.release()


def settle(screen):
    assert screen.wait_idle(), "the grey thread did not finish"


# ---------------------------------------------------------------- the matrix

def multiply(colour, matrix):
    """What the Magnification API does: the colour as a row vector (R, G, B, A, 1) times the 5x5 matrix."""
    row = (*colour, 1.0, 1.0)
    return tuple(sum(row[i] * matrix[i * 5 + j] for i in range(5)) for j in range(5))


def test_the_grey_matrix_turns_every_colour_into_its_luma():
    assert grayscale.LUMA == (0.3, 0.59, 0.11) and sum(grayscale.LUMA) == pytest.approx(1.0)
    assert multiply((1, 1, 1), GREY)[:3] == pytest.approx((1, 1, 1))         # white stays white
    assert multiply((0, 0, 0), GREY)[:3] == (0, 0, 0)
    assert multiply((1, 0, 0), GREY)[:3] == pytest.approx((0.3, 0.3, 0.3))   # red -> dark grey
    assert multiply((0, 1, 0), GREY)[:3] == pytest.approx((0.59, 0.59, 0.59))
    assert multiply((0.2, 0.4, 0.6), GREY)[:3] == pytest.approx((0.2 * 0.3 + 0.4 * 0.59 + 0.6 * 0.11,) * 3)
    assert multiply((0.2, 0.4, 0.6), IDENTITY)[:3] == pytest.approx((0.2, 0.4, 0.6))
    assert multiply((0.2, 0.4, 0.6), GREY)[3] == 1.0                          # alpha untouched


def test_matrices_compare_with_a_little_room_for_32_bit_floats():
    assert same(GREY, tuple(float(__import__("struct").unpack("f", __import__("struct").pack("f", x))[0]) for x in GREY))
    assert not same(GREY, IDENTITY) and not same(GREY, None) and not same(None, None)


@pytest.mark.skipif(sys.platform == "win32", reason="on Windows the real API answers")
def test_without_windows_the_real_backend_just_says_no():
    assert grayscale.MagBackend().start() is False


# ---------------------------------------------------------------- the one place that owns the colour

def test_grey_while_anybody_wants_it_and_colour_back_when_nobody_does(screen, backend):
    screen.want("app", True)
    settle(screen)
    assert backend.grey and screen.applied
    screen.want("app", False)
    settle(screen)
    assert backend.screen == IDENTITY and not screen.applied


def test_two_wishes_never_fight(screen, backend):
    """Bedtime and an app both want grey: it is applied once, stays while either does, and goes only after both stop."""
    screen.want("bedtime", True)
    screen.want("app", True)
    settle(screen)
    screen.want("app", False)
    settle(screen)
    assert backend.grey, "bedtime still wants it"
    screen.want("app", True)
    screen.want("bedtime", False)
    settle(screen)
    assert backend.grey, "the app still wants it"
    screen.want("app", False)
    settle(screen)
    assert backend.screen == IDENTITY
    writes = [c[1] for c in backend.calls if c[0] == "apply"]
    assert [same(w, GREY) for w in writes] == [True, False], "one grey, one colour: nothing flickered in between"


def test_asking_again_for_what_is_already_so_costs_nothing(screen, backend):
    screen.want("app", True)
    settle(screen)
    before = len(backend.calls)
    for _ in range(20):
        screen.want("app", True)
        screen.want("bedtime", False)               # (never was on)
    settle(screen)
    assert len(backend.calls) == before


def test_a_screen_that_never_was_grey_is_never_touched(screen, backend):
    screen.want("app", False)
    screen.want("bedtime", False)
    settle(screen)
    assert backend.calls == []                      # not even the API started


def test_every_call_is_made_on_one_thread_of_its_own(screen, backend):
    for _ in range(3):
        screen.want("app", True)
        settle(screen)
        screen.want("app", False)
        settle(screen)
    assert backend.thread_names == {"lockdown-grey"}
    assert threading.current_thread().name not in backend.thread_names


def test_the_effect_that_was_there_before_is_what_comes_back(backend):
    backend.screen = SEPIA                          # Windows' Magnifier or another tool had its own colour effect
    s = GreyScreen(backend, reassert_sec=60)
    s.want("app", True)
    settle(s)
    assert backend.grey
    s.want("app", False)
    settle(s)
    assert same(backend.screen, SEPIA)              # given back, not replaced by plain colour
    s.release()


def test_an_effect_somebody_else_set_meanwhile_is_left_alone(backend):
    s = GreyScreen(backend, reassert_sec=60)
    s.want("app", True)
    settle(s)
    backend.screen = SEPIA                          # another program took the screen over while it was grey
    s.want("app", False)
    settle(s)
    assert same(backend.screen, SEPIA) and not s.applied
    s.release()


def test_nothing_is_written_when_the_colour_is_already_back(backend):
    s = GreyScreen(backend, reassert_sec=60)
    s.want("app", True)
    settle(s)
    backend.screen = IDENTITY                       # something else gave the colour back
    writes = len([c for c in backend.calls if c[0] == "apply"])
    s.want("app", False)
    settle(s)
    assert len([c for c in backend.calls if c[0] == "apply"]) == writes and not s.applied
    s.release()


def test_release_gives_the_colour_back_and_lets_go_of_the_api(screen, backend):
    screen.want("bedtime", True)
    settle(screen)
    assert backend.grey
    assert screen.release() is True
    assert backend.screen == IDENTITY and backend.calls[-1] == ("stop",) and not screen.applied
    assert not screen.wanted                         # (nobody wants grey after a release)
    screen.want("bedtime", True)                      # ... but it starts again when somebody does
    settle(screen)
    assert backend.grey
    assert threading.active_count() < 50


def test_release_before_anything_was_grey_is_harmless(screen, backend):
    assert screen.release() is True and backend.calls == []


def test_the_screen_is_made_grey_again_when_a_display_change_reset_it():
    backend = FakeBackend()
    s = GreyScreen(backend, reassert_sec=0.02)
    s.want("app", True)
    settle(s)
    backend.screen = IDENTITY                        # Windows reset the effect (a resolution change, the secure desktop)
    assert wait_for(lambda: backend.grey)
    s.release()


def test_a_refused_start_is_tried_again_and_logged_once():
    backend = FakeBackend(broken=True)
    said = []
    s = GreyScreen(backend, reassert_sec=0.02, retry_sec=0.1, log=said.append)
    s.want("app", True)
    settle(s)
    assert not s.applied and not backend.grey
    time.sleep(0.25)
    assert len([c for c in backend.calls if c[0] == "start"]) >= 2, "it retries"
    assert 1 <= len(said) <= 3 and "could not" in said[0]
    backend.broken = False                           # Windows lets it now
    assert wait_for(lambda: backend.grey)
    s.release()
    assert backend.screen == IDENTITY


def test_a_refused_colour_back_is_tried_again():
    backend = FakeBackend()
    s = GreyScreen(backend, reassert_sec=0.02, retry_sec=0.05, log=lambda text: None)
    s.want("app", True)
    settle(s)
    backend.broken = True                            # the API refuses the write that gives the colour back
    s.want("app", False)
    settle(s)
    assert s.applied and backend.grey
    backend.broken = False
    assert wait_for(lambda: backend.screen == IDENTITY and not s.applied)
    s.release()


# ---------------------------------------------------------------- exit and crash

def test_the_exit_hooks_give_the_colour_back_on_exit_and_on_a_crash(monkeypatch):
    registered, calls = [], []
    monkeypatch.setattr(grayscale.atexit, "register", registered.append)
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: seen.append("main"))
    monkeypatch.setattr(threading, "excepthook", lambda args: seen.append("thread"))
    fake = SimpleNamespace(release=lambda: calls.append("release"))
    grayscale.install_exit_hooks(fake)
    assert registered == [fake.release]                                  # normal exit
    sys.excepthook(ValueError, ValueError("boom"), None)                 # the main thread dies
    assert calls == ["release"] and seen == ["main"]                     # colour first, then the next hook (the app log's)
    threading.excepthook(SimpleNamespace(exc_type=RuntimeError, exc_value=None, exc_traceback=None, thread=None))
    assert calls == ["release", "release"] and seen == ["main", "thread"]
    threading.excepthook(SimpleNamespace(exc_type=SystemExit, exc_value=None, exc_traceback=None, thread=None))
    assert calls == ["release", "release"] and seen == ["main", "thread", "thread"]     # (a thread that just ends is no crash)


def test_the_agent_makes_the_screen_and_its_hooks():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "src" / "agent.py").read_text(encoding="utf-8")
    assert "hub.grey = grayscale.GreyScreen(" in text and "grayscale.install_exit_hooks(hub.grey)" in text
    assert text.index("hub.grey.release()") < text.index("os._exit(FAILED)")           # (os._exit skips atexit)


# ---------------------------------------------------------------- the worker

@pytest.fixture
def grey(tmp_path):
    env = Env(tmp_path)
    env.heartbeat()
    env.backend = FakeBackend()
    env.screen = env.hub.grey = GreyScreen(env.backend, reassert_sec=60)
    env.game = env.db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "permanent"}], block_type="grey")
    env.db.add_item("Closer", ["closer.exe"], "app", rules=[{"rule_type": "permanent"}])          # blocked, but closed
    env.db.add_item("Mini", ["mini.exe"], "app", rules=[{"rule_type": "permanent"}], block_type="minimize")
    env.worker = workers.GreyWorker(env.hub)
    yield env
    env.screen.release()


def tick(env, front=None):
    if front is not None:
        env.win.front = front
    env.worker.tick()
    settle(env.screen)


def test_the_screen_is_grey_while_a_grey_app_is_in_front_and_colour_comes_back_when_it_leaves(grey):
    grey.snap()
    tick(grey, (7, "game.exe"))
    assert grey.backend.grey
    tick(grey, (7, "notepad.exe"))
    assert grey.backend.screen == IDENTITY
    tick(grey, (7, "game.exe"))
    assert grey.backend.grey
    tick(grey, (0, ""))                                    # nothing in front (the desktop is locked)
    assert grey.backend.screen == IDENTITY


def test_only_grey_blocks_grey_the_screen(grey):
    grey.snap()
    for exe in ("closer.exe", "mini.exe", "notepad.exe"):
        tick(grey, (7, exe))
        assert grey.backend.screen == IDENTITY, exe
    assert grey.backend.calls == []


def test_the_game_behind_its_starter_is_the_app(grey):
    """Unreal games run as <Project>-Win64-Shipping.exe (apps.names_of): the app in front is the item."""
    grey.snap()
    tick(grey, (7, "game-win64-shipping.exe"))
    assert grey.backend.grey


def test_grey_and_more_ways_still_grey_but_never_close(grey):
    grey.db.add_item("Both", ["both.exe"], "app", rules=[{"rule_type": "permanent"}], block_type="grey,internet")
    grey.snap()
    tick(grey, (7, "both.exe"))
    assert grey.backend.grey


def test_nothing_happens_before_the_first_snapshot_and_without_a_screen(grey):
    grey.win.front = (7, "game.exe")
    tick(grey)                                             # no snapshot yet
    assert grey.backend.screen == IDENTITY
    grey.hub.grey = None
    grey.snap()
    grey.worker.tick()                                     # (a hub without a screen: nothing to do, nothing fails)


def test_switched_off_nothing_is_greyed_and_a_grey_screen_goes_back_to_colour(grey):
    grey.snap()
    tick(grey, (7, "game.exe"))
    assert grey.backend.grey
    antibypass.switch_off(grey.db, NOW)
    grey.snap()
    tick(grey)
    assert grey.backend.screen == IDENTITY
    antibypass.switch_on(grey.db)
    grey.snap()
    tick(grey)
    assert grey.backend.grey                               # switched on again: the app is still in front


def test_pausing_the_blocks_gives_the_colour_back(grey):
    grey.snap()
    tick(grey, (7, "game.exe"))
    assert grey.backend.grey
    pause.start(grey.db, NOW, 30, silent=False)
    grey.snap()
    tick(grey)
    assert grey.backend.screen == IDENTITY                 # nothing of yours blocks while paused
    pause.resume(grey.db)
    grey.snap()
    tick(grey)
    assert grey.backend.grey


def test_an_emergency_unlock_of_the_app_gives_the_colour_back(grey):
    from rules import make_schedule
    evening = make_schedule("block", [(list(range(7)), "21:00", "23:30")])
    night = grey.db.add_item("Night", ["night.exe"], "app", rules=[{"rule_type": "scheduled", "schedule": evening}],
                             block_type="grey")
    grey.snap()
    tick(grey, (7, "night.exe"))
    assert grey.backend.grey
    emergency.unlock(grey.db, [{"id": night, "display_name": "Night"}], NOW, tier="t1")
    grey.snap()
    tick(grey)
    assert grey.backend.screen == IDENTITY


def test_a_block_that_has_not_started_does_not_grey(grey):
    from rules import make_schedule
    night = make_schedule("block", [(list(range(7)), "23:00", "23:30")])
    grey.db.update_item(grey.game, "Game", ["game.exe"], None, [{"rule_type": "scheduled", "schedule": night}], block_type="grey")
    grey.snap()
    tick(grey, (7, "game.exe"))
    assert grey.backend.screen == IDENTITY                 # 22:00: not yet
    grey.clock.advance(65 * 60)
    grey.snap()
    tick(grey)
    assert grey.backend.grey


def test_a_group_that_greys_its_members_greys_the_screen(grey):
    other = grey.db.add_item("Other", ["other.exe"], "app", rules=[], block_type="close")
    grey.db.add_group("Fun", [{"rule_type": "permanent"}], {other: {}}, {"app_block": "grey", "site_block": None, "member_blocks": {}})
    grey.snap()
    tick(grey, (7, "other.exe"))
    assert grey.backend.grey                               # the group's way, not the app's own "close"


def test_a_mode_that_blocks_the_grey_app_closes_it_instead(grey):
    """A mode blocks it and the service closes it (the item's own way is "Grey it", the mode asks for more)."""
    import modes
    listed = modes.load(grey.db)
    listed[0]["items"] = [grey.game]
    modes.save(grey.db, listed)
    modes.start(grey.db, "work", NOW, None)
    snap = grey.snap()
    block = next(b for b in snap.block_list if b["item"]["id"] == grey.game)
    assert block["reason"] == "mode" and block["item"]["block_type"] == "close"
    tick(grey, (7, "game.exe"))
    assert grey.backend.screen == IDENTITY


def test_no_database_is_opened_and_the_foreground_is_only_asked_when_something_greys(grey):
    asked = []
    grey.win.foreground = lambda: asked.append(1) or (0, "")
    grey.snap()
    grey.worker.tick()
    assert asked == [1] and grey.worker._db is None
    grey.db.conn.execute("UPDATE blocked_items SET block_type = NULL WHERE block_type = 'grey'")
    grey.db.conn.commit()
    grey.snap()
    grey.worker.tick()
    assert asked == [1], "no app greys any more: Windows is not even asked what is in front"


def test_a_program_file_is_never_greyed(grey):
    """The window in front of a Java game is javaw.exe: the service closes the file instead (apps.hosted_way)."""
    grey.db.add_item("Tool", ["tool.jar"], "app", rules=[{"rule_type": "permanent"}], block_type="grey")
    snap = grey.snap()
    assert "tool.jar" not in workers.grey_set(snap.block_list) and "game.exe" in workers.grey_set(snap.block_list)
    tick(grey, (7, "javaw.exe"))
    assert grey.backend.screen == IDENTITY


def test_the_worker_stopping_gives_the_colour_back(grey):
    grey.real_pacing()
    grey.snap()
    grey.win.front = (7, "game.exe")
    grey.worker.start()
    assert wait_for(lambda: grey.backend.grey)
    grey.worker.stop(2.0)
    assert not grey.worker.is_alive() and grey.backend.screen == IDENTITY
    assert grey.backend.calls[-1] == ("stop",)


def test_workers_has_a_grey_worker():
    pool = workers.Workers(hub=workers.Hub(log_sink=lambda text: None))
    assert isinstance(pool.grey, workers.GreyWorker) and pool.grey in pool.workers
    assert pool.hub.grey is None                           # (the agent makes the screen; tests and headless runs have none)


# ---------------------------------------------------------------- bedtime

def sleep_cfg(**kw):
    return {**reminders.DEFAULT_SLEEP, "on": True, "grey": True, "bedtime": "21:00", "wake": "07:00", **kw}


def test_bedtime_grey_follows_the_sleep_window_in_trusted_time(grey):
    reminders.save(grey.db, reminders.SLEEP_KEY, sleep_cfg())
    for clock, wanted in (("20:59", False), ("21:00", True), ("22:00", True), ("23:59", True), ("00:00", True),
                          ("06:59", True), ("07:00", False), ("12:00", False)):
        hour, minute = map(int, clock.split(":"))
        grey.clock.t = grey.clock.t.replace(hour=hour, minute=minute)
        grey.snap()
        tick(grey, (7, "notepad.exe"))
        assert grey.backend.grey == wanted, clock
        assert grey.hub.snapshot.bedtime_grey == wanted, clock


def test_bedtime_grey_needs_both_switches(grey):
    for cfg in (sleep_cfg(grey=False), sleep_cfg(on=False), sleep_cfg(bedtime="22:00", wake="22:00")):
        reminders.save(grey.db, reminders.SLEEP_KEY, cfg)
        grey.snap()
        tick(grey, (7, "notepad.exe"))
        assert grey.backend.screen == IDENTITY, cfg


def test_a_same_day_window_works_too(grey):
    reminders.save(grey.db, reminders.SLEEP_KEY, sleep_cfg(bedtime="13:00", wake="15:00"))
    grey.snap()
    tick(grey, (7, "notepad.exe"))
    assert grey.backend.screen == IDENTITY                 # 22:00 is outside
    grey.clock.t = grey.clock.t.replace(hour=14)
    grey.snap()
    tick(grey)
    assert grey.backend.grey


def test_an_emergency_that_holds_the_alerts_or_a_silent_pause_holds_the_grey_too(grey):
    reminders.save(grey.db, reminders.SLEEP_KEY, sleep_cfg())
    grey.snap()
    tick(grey, (7, "notepad.exe"))
    assert grey.backend.grey
    emergency.unlock(grey.db, [], NOW, alerts=True, tier="t1")
    grey.snap()
    tick(grey)
    assert grey.backend.screen == IDENTITY                 # (the phone's rule: not while an emergency holds the alerts)
    grey.clock.advance(60 * 60)                            # the unlock is over
    grey.snap()
    tick(grey)
    assert grey.backend.grey
    pause.start(grey.db, grey.clock.t, 30, silent=True)
    grey.snap()
    tick(grey)
    assert grey.backend.screen == IDENTITY
    pause.start(grey.db, grey.clock.t, 30, silent=False)   # a pause that does not silence the alerts holds nothing
    grey.snap()
    tick(grey)
    assert grey.backend.grey


def test_switching_lockdown_off_gives_the_bedtime_colour_back(grey):
    reminders.save(grey.db, reminders.SLEEP_KEY, sleep_cfg())
    grey.snap()
    tick(grey, (7, "notepad.exe"))
    assert grey.backend.grey
    antibypass.switch_off(grey.db, NOW)
    grey.snap()
    tick(grey)
    assert grey.backend.screen == IDENTITY


def test_bedtime_and_the_app_together_never_fight(grey):
    reminders.save(grey.db, reminders.SLEEP_KEY, sleep_cfg())
    grey.snap()
    tick(grey, (7, "game.exe"))                            # both want it
    tick(grey, (7, "notepad.exe"))                         # the app leaves: bedtime keeps the screen grey
    assert grey.backend.grey
    tick(grey, (7, "game.exe"))
    tick(grey, (7, "notepad.exe"))
    reminders.save(grey.db, reminders.SLEEP_KEY, sleep_cfg(grey=False))
    grey.snap()
    tick(grey)
    assert grey.backend.screen == IDENTITY
    writes = [same(c[1], GREY) for c in grey.backend.calls if c[0] == "apply"]
    assert writes == [True, False], "greyed once, colour back once"
