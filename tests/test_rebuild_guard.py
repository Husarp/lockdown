"""R02: GuardService - the Anti-Bypass challenge decided in Python (ticket, phrase, typing time, unlock, grace)."""
import random
import threading
from datetime import datetime, timedelta

import pytest

import antibypass as ab
import webapi
from webapi import core, guard
from webapi.guard import GuardService

NOW = datetime(2026, 9, 20, 19, 0)            # a Sunday evening
SUN_EVENING = [{"days": [6], "start": "18:00", "end": "20:00"}]


class Clock:
    """Trusted time and the monotonic clock, both set by the test."""

    def __init__(self):
        self.now, self.mono = NOW, 1000.0

    def __call__(self, db=None):
        return self.now

    def tick(self):
        return self.mono


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    ctx = webapi.make_context(tmp_path / "t.db", clock=clock)
    ctx.guard = GuardService(lambda: ctx.db, clock, monotonic=clock.tick, rng=random.Random(7))
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})
    ctx.clock = clock
    ctx.api = core.build_api(ctx)
    return ctx


class Change:
    """A pending change that records whether it ran or was cancelled."""

    def __init__(self):
        self.ran, self.cancelled = 0, 0

    def action(self):
        self.ran += 1
        return {"saved": self.ran}

    def cancel(self):
        self.cancelled += 1


def ask(ctx, change, changes=("Remove YouTube",), force=False):
    return ctx.guard.request(list(changes), change.action, change.cancel, force=force)


def typing_time(ctx, text_or_words):
    """Let enough time pass for a person to type it."""
    chars = len(text_or_words) if isinstance(text_or_words, str) else sum(map(len, text_or_words))
    ctx.clock.mono += chars * guard.MIN_MS_PER_CHAR / 1000 + 1


def open_challenge(ctx, change, **kw):
    ticket = ask(ctx, change, **kw)["data"]["ticket"]
    return ticket, ctx.guard.begin(ticket)["data"]


# ---------------------------------------------------------------- asking

def test_loosening_while_locked_returns_a_ticket_and_does_not_run(env):
    change = Change()
    got = ask(env, change, changes=[f"Remove site {i}" for i in range(7)])
    assert got["ok"] is True
    data = got["data"]
    assert data["needs_challenge"] is True and data["ticket"]
    assert data["changes"] == [f"Remove site {i}" for i in range(5)] and data["more"] == 2
    assert change.ran == 0 and change.cancelled == 0


def test_a_short_list_has_no_more(env):
    assert ask(env, Change(), changes=["a", "b"])["data"]["more"] == 0


def test_status_free_runs_at_once(env):
    ab.save(env.db, {**ab.settings(env.db), "phrase": False})
    change = Change()
    got = ask(env, change)
    assert got == {"ok": True, "data": {"saved": 1}} and change.ran == 1


def test_free_inside_an_unlock_runs_at_once(env):
    ab.unlock(env.db, NOW)
    change = Change()
    assert ask(env, change)["data"] == {"saved": 1}


def test_force_without_an_unlock_asks_even_when_free(env):
    ab.save(env.db, {**ab.settings(env.db), "phrase": False})
    change = Change()
    got = ask(env, change, force=True)
    assert got["data"]["needs_challenge"] is True and change.ran == 0


def test_force_with_an_active_unlock_runs(env):
    ab.unlock(env.db, NOW)
    change = Change()
    assert ask(env, change, force=True)["data"] == {"saved": 1}


def test_an_exception_in_the_action_run_now_comes_back_as_an_error(env, tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "log.txt")
    ab.lock(env.db)
    ab.save(env.db, {**ab.settings(env.db), "phrase": False})
    reg = core.Registry()

    @reg._decorator("loosen")
    def risky(ctx):
        return ctx.guard.request(["x"], lambda: 1 / 0)
    got = core.build_api(env, reg).risky()
    assert got["ok"] is False and "division" in got["error"]


# ---------------------------------------------------------------- the challenge

def test_wrong_phrase_runs_nothing(env):
    change = Change()
    ticket, shown = open_challenge(env, change)
    assert shown["mode"] == "phrase"
    typing_time(env, shown["phrase"])
    got = env.guard.submit(ticket, shown["phrase"][:-1] + "!")
    assert got["ok"] is False and change.ran == 0
    assert env.guard.submit(ticket, "")["ok"] is False and change.ran == 0
    assert env.guard.submit(ticket, None)["ok"] is False
    assert env.guard.submit(ticket, ["a", "b"])["ok"] is False
    assert ab.status(ab.settings(env.db), NOW) == "phrase"       # nothing was unlocked
    got = env.guard.submit(ticket, shown["phrase"])              # (a typo can be fixed: the ticket stays open)
    assert got["ok"] is True and change.ran == 1


def test_right_phrase_typed_too_fast_is_refused(env):
    change = Change()
    ticket, shown = open_challenge(env, change)
    got = env.guard.submit(ticket, shown["phrase"])
    assert got["ok"] is False and "Too fast" in got["error"] and change.ran == 0
    assert ab.status(ab.settings(env.db), NOW) == "phrase"
    # the clock started over: just under the time is still too fast, then it passes
    env.clock.mono += len(shown["phrase"]) * guard.MIN_MS_PER_CHAR / 1000 - 0.05
    assert env.guard.submit(ticket, shown["phrase"])["ok"] is False
    env.clock.mono += len(shown["phrase"]) * guard.MIN_MS_PER_CHAR / 1000
    assert env.guard.submit(ticket, shown["phrase"])["ok"] is True and change.ran == 1


def test_right_phrase_with_plausible_time_runs_once_and_the_ticket_is_spent(env):
    change = Change()
    ticket, shown = open_challenge(env, change)
    typing_time(env, shown["phrase"])
    got = env.guard.submit(ticket, shown["phrase"])
    assert got == {"ok": True, "data": {"passed": True, "result": {"saved": 1}}}
    assert change.ran == 1 and change.cancelled == 0
    assert ab.status(ab.settings(env.db), NOW) == "free"          # unlocked for a few minutes
    again = env.guard.submit(ticket, shown["phrase"])
    assert again["ok"] is False and change.ran == 1
    assert env.guard.begin(ticket)["ok"] is False
    assert env.guard.cancel(ticket)["ok"] is True and change.cancelled == 0


def test_a_ticket_cannot_be_used_before_begin(env):
    change = Change()
    ticket = ask(env, change)["data"]["ticket"]
    env.clock.mono += 100
    assert env.guard.submit(ticket, "anything")["ok"] is False and change.ran == 0


def test_made_up_tickets_are_refused(env):
    change = Change()
    ask(env, change)
    for ticket in ("nope", "", None, 5, ["x"], {"a": 1}):
        assert env.guard.begin(ticket)["ok"] is False
        assert env.guard.submit(ticket, "x")["ok"] is False
        assert env.guard.cancel(ticket)["ok"] is True
    assert change.ran == 0 and change.cancelled == 0


def test_expired_ticket_is_refused_and_cancelled(env):
    change = Change()
    ticket, shown = open_challenge(env, change)
    env.clock.mono += guard.TICKET_TTL_SEC + 1
    got = env.guard.submit(ticket, shown["phrase"])
    assert got["ok"] is False and "expired" in got["error"]
    assert change.ran == 0 and change.cancelled == 1
    assert ab.status(ab.settings(env.db), NOW) == "phrase"


def test_just_inside_ten_minutes_still_works(env):
    change = Change()
    ticket, shown = open_challenge(env, change)
    env.clock.mono += guard.TICKET_TTL_SEC - 1
    assert env.guard.submit(ticket, shown["phrase"])["ok"] is True


def test_a_second_request_cancels_the_first(env):
    first, second = Change(), Change()
    t1, shown1 = open_challenge(env, first)
    t2 = ask(env, second, changes=["Disable Reddit"])["data"]["ticket"]
    assert t1 != t2 and first.cancelled == 1 and second.cancelled == 0
    typing_time(env, shown1["phrase"])
    assert env.guard.submit(t1, shown1["phrase"])["ok"] is False and first.ran == 0
    shown2 = env.guard.begin(t2)["data"]
    assert shown2["changes"] == ["Disable Reddit"]
    typing_time(env, shown2["phrase"])
    assert env.guard.submit(t2, shown2["phrase"])["ok"] is True
    assert second.ran == 1 and first.ran == 0


def test_cancel_runs_the_stored_cancel_once(env):
    change = Change()
    ticket, _ = open_challenge(env, change)
    assert env.guard.cancel(ticket)["ok"] is True
    assert env.guard.cancel(ticket)["ok"] is True
    assert change.cancelled == 1 and change.ran == 0
    assert env.guard.begin(ticket)["ok"] is False


def test_a_failing_cancel_does_not_break_the_next_request(env, tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "log.txt")
    monkeypatch.setattr(core, "_last_error", ["", 0.0])

    def bad_cancel():
        raise RuntimeError("revert failed")
    env.guard.request(["a"], lambda: None, bad_cancel)
    assert ask(env, Change())["data"]["needs_challenge"] is True
    assert "revert failed" in (tmp_path / "log.txt").read_text(encoding="utf-8")


def test_a_wait_cancels_the_action_and_says_when(env):
    ab.save(env.db, {**ab.settings(env.db), "wait_min": 10})
    change = Change()
    ticket, shown = open_challenge(env, change)
    typing_time(env, shown["phrase"])
    got = env.guard.submit(ticket, shown["phrase"])
    assert got == {"ok": True, "data": {"passed": True,
                                        "waiting_until": (NOW + timedelta(minutes=10)).isoformat(timespec="seconds")}}
    assert change.ran == 0 and change.cancelled == 1
    # while it waits: a new request shows the waiting note, never the phrase again
    ticket2 = ask(env, Change())["data"]["ticket"]
    waiting = env.guard.begin(ticket2)["data"]
    assert waiting["mode"] == "waiting" and "phrase" not in waiting
    assert waiting["waiting_until"] == (NOW + timedelta(minutes=10)).isoformat(timespec="seconds")
    assert env.guard.submit(ticket2, "x")["ok"] is False
    # once the wait is over the change goes straight through
    env.clock.now = NOW + timedelta(minutes=11)
    assert ask(env, change)["data"] == {"saved": 1}


def test_closed_hours_show_the_next_chance(env):
    ab.save(env.db, {**ab.settings(env.db), "phrase": False, "hours": True, "windows": SUN_EVENING})
    env.clock.now = NOW + timedelta(hours=2)                     # 21:00, outside the hours
    change = Change()
    ticket, shown = open_challenge(env, change)
    assert shown["mode"] == "closed" and "phrase" not in shown and "grid_words" not in shown
    assert shown["next_open"] == "2026-09-27T18:00:00"
    assert "18:00-20:00" in shown["hours_text"]
    assert env.guard.submit(ticket, "x")["ok"] is False and change.ran == 0
    assert env.guard.cancel(ticket)["ok"] is True and change.cancelled == 1


def test_the_hours_closing_during_the_challenge_stops_it(env):
    ab.save(env.db, {**ab.settings(env.db), "hours": True, "windows": SUN_EVENING})
    change = Change()
    ticket, shown = open_challenge(env, change)
    assert shown["mode"] == "phrase"
    env.clock.now = NOW + timedelta(hours=2)
    typing_time(env, shown["phrase"])
    assert env.guard.submit(ticket, shown["phrase"])["ok"] is False and change.ran == 0


def test_force_asks_for_the_phrase_even_with_no_challenge_on(env):
    ab.save(env.db, {**ab.settings(env.db), "phrase": False})
    change = Change()
    ticket, shown = open_challenge(env, change, force=True)
    assert shown["mode"] == "phrase" and len(shown["phrase"].replace(" ", "")) == 30
    typing_time(env, shown["phrase"])
    assert env.guard.submit(ticket, shown["phrase"])["ok"] is True and change.ran == 1


def test_random_phrase_is_fresh_on_every_begin_and_custom_stays(env):
    ticket = ask(env, Change())["data"]["ticket"]
    seen = {env.guard.begin(ticket)["data"]["phrase"] for _ in range(5)}
    assert len(seen) == 5 and all(len(p.replace(" ", "")) == 30 for p in seen)
    ab.save(env.db, {**ab.settings(env.db), "custom_phrase": "my own phrase"})          # saved, switched off
    assert len({env.guard.begin(ticket)["data"]["phrase"] for _ in range(3)}) == 3
    ab.save(env.db, {**ab.settings(env.db), "use_own": True})
    assert {env.guard.begin(ticket)["data"]["phrase"] for _ in range(3)} == {"my own phrase"}


def test_a_blank_own_phrase_cannot_be_passed_by_typing_nothing(env):
    ab.save(env.db, {**ab.settings(env.db), "use_own": True, "custom_phrase": "   ", "grid": True})
    change = Change()
    ticket, shown = open_challenge(env, change)
    assert shown["grid_words"] and all(shown["grid_words"])
    assert env.guard.submit(ticket, [])["ok"] is False and change.ran == 0


def test_grid_mode_gives_the_words_and_checks_them_in_order(env):
    ab.save(env.db, {**ab.settings(env.db), "grid": True, "length": 45})
    change = Change()
    ticket, shown = open_challenge(env, change)
    words = shown["grid_words"]
    assert shown["mode"] == "grid" and "phrase" not in shown
    assert len(words) == 9 and all(len(w) == 5 for w in words)
    typing_time(env, words)
    assert env.guard.submit(ticket, " ".join(words))["ok"] is False                   # a string isn't the words
    assert env.guard.submit(ticket, list(reversed(words)))["ok"] is False            # wrong order
    assert env.guard.submit(ticket, words[:-1])["ok"] is False                       # one missing
    assert env.guard.submit(ticket, words + [words[0]])["ok"] is False               # one too many
    assert env.guard.submit(ticket, [*words[:-1], 7])["ok"] is False                 # not text
    assert change.ran == 0
    assert env.guard.submit(ticket, words)["ok"] is True and change.ran == 1


def test_grid_typed_too_fast_is_refused(env):
    ab.save(env.db, {**ab.settings(env.db), "grid": True})
    change = Change()
    ticket, shown = open_challenge(env, change)
    got = env.guard.submit(ticket, shown["grid_words"])
    assert got["ok"] is False and "Too fast" in got["error"] and change.ran == 0


def test_the_ticket_works_for_only_one_of_many_racing_submits(env):
    change = Change()
    ticket, shown = open_challenge(env, change)
    typing_time(env, shown["phrase"])
    start, results = threading.Barrier(8), []

    def go():
        start.wait()
        results.append(env.guard.submit(ticket, shown["phrase"])["ok"])
    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and change.ran == 1


def test_an_action_that_fails_still_spends_the_ticket(env, tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "log.txt")
    reg = core.Registry()
    calls = []

    @reg._decorator("loosen")
    def risky(ctx):
        def boom():
            calls.append(1)
            raise RuntimeError("disk full")
        return ctx.guard.request(["x"], boom)
    api = core.build_api(env, reg)
    ticket = api.risky()["data"]["ticket"]
    shown = env.guard.begin(ticket)["data"]
    typing_time(env, shown["phrase"])
    reg2 = core.build_api(env)
    got = reg2.challenge_submit(ticket, shown["phrase"])
    assert got["ok"] is False and "disk full" in got["error"] and calls == [1]
    assert reg2.challenge_submit(ticket, shown["phrase"])["ok"] is False and calls == [1]


# ---------------------------------------------------------------- grace

def test_grace_exact_revert_inside_ten_seconds_is_free_once(env):
    env.guard.grace_note("protection", {"lists": ["a"]})
    env.clock.mono += guard.GRACE_SEC - 1
    assert env.guard.grace_ok("protection", {"lists": ["a"]}) is True
    assert env.guard.grace_ok("protection", {"lists": ["a"]}) is False        # spent


def test_grace_after_ten_seconds_is_not(env):
    env.guard.grace_note("protection", "on")
    env.clock.mono += guard.GRACE_SEC + 0.01
    assert env.guard.grace_ok("protection", "on") is False


def test_grace_needs_the_same_state_and_domain(env):
    env.guard.grace_note("keywords", ("a", "b"))
    assert env.guard.grace_ok("keywords", ("a",)) is False                   # not an exact revert
    assert env.guard.grace_ok("protection", ("a", "b")) is False             # another domain
    assert env.guard.grace_ok("keywords", ("a", "b")) is True                # (a wrong guess didn't spend it)
    assert env.guard.grace_ok("never", None) is False


# ---------------------------------------------------------------- through the Api

def test_the_challenge_api_end_to_end(env):
    change = Change()
    ticket = ask(env, change)["data"]["ticket"]
    shown = env.api.challenge_begin(ticket)
    assert shown["ok"] and shown["data"]["mode"] == "phrase"
    typing_time(env, shown["data"]["phrase"])
    assert env.api.challenge_submit(ticket, "wrong")["ok"] is False
    done = env.api.challenge_submit(ticket, shown["data"]["phrase"])
    assert done == {"ok": True, "data": {"passed": True, "result": {"saved": 1}}}
    assert env.api.challenge_cancel(ticket) == {"ok": True, "data": None}


def test_api_rejects_non_json_challenge_arguments(env):
    ticket = ask(env, Change())["data"]["ticket"]
    shown = env.api.challenge_begin(ticket)["data"]
    assert env.api.challenge_submit(ticket, float("nan"))["ok"] is False
    assert env.api.challenge_submit(ticket, {"phrase": shown["phrase"]})["ok"] is False


def test_the_challenge_methods_take_no_confirmed_flag():
    import inspect
    for name in ("challenge_begin", "challenge_submit", "challenge_cancel"):
        params = inspect.signature(getattr(guard, name)).parameters
        assert list(params)[0] == "ctx" and set(params) <= {"ctx", "ticket", "typed"}


def test_antibypass_status_is_readable_and_hides_the_phrase(env):
    ab.save(env.db, {**ab.settings(env.db), "use_own": True, "custom_phrase": "top secret words", "hours": True,
                     "windows": SUN_EVENING})
    got = env.api.antibypass_status()
    assert got["ok"] and got["data"]["status"] == "phrase" and got["data"]["custom_phrase"] is True
    assert "top secret" not in str(got)
    env.clock.now = NOW + timedelta(hours=2)
    assert env.api.antibypass_status()["data"]["status"] == "closed"
    assert env.api.antibypass_status()["data"]["next_open"] == "2026-09-27T18:00:00"
    ab.unlock(env.db, env.clock.now)
    ab.save(env.db, {**ab.settings(env.db), "hours": False})
    got = env.api.antibypass_status()["data"]
    assert got["status"] == "free" and got["unlocked_until"] == "2026-09-20T21:05:00"
