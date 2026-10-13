"""C4: "Challenge before Dismiss" on the PC's bedtime screen (resident/overlay.py, webapi/overlay.py, webapi/guard.py's
plain variant, the Sleep card's save in webapi/reminders.py).

The reminder engine is the real one and drives the real Overlay (as tests/test_rebuild_overlay.py does); the window, the
publisher and the reminder worker are fakes. What can only be proven on Windows: typing into the challenge while the
cover blocks the clicks behind it ("Block clicks behind the bedtime screen")."""
import random
from datetime import datetime, timedelta

import pytest

import antibypass as ab
import applog
import challenges
import emergency
import reminders
from resident.overlay import bedtime_challenge
from tests.test_rebuild_overlay import Env as BaseEnv
from webapi import core, guard
from webapi import overlay as page

GENERAL = [{"id": "g1", "kind": "math", "level": 0, "times": 2}]
SORT = [{"id": "o1", "kind": "sort", "level": 0, "times": 1}]
MEMORY = [{"id": "o2", "kind": "memory", "level": 0, "times": 1}]
# 22:00 -> the general list, 00:00 -> a Sorting round, 02:00 -> a Memory grid round, 04:00 -> the general list again
NIGHT_CFG = {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "22:00", "wake": "07:00", "before": 30, "dismiss_wait": 25,
             "challenge": True, "challenge_steps": GENERAL,
             "tiers": [{"from": "22:00", "every_sec": 900}, {"from": "00:00", "every_sec": 300, "challenge_steps": SORT},
                       {"from": "02:00", "every_sec": 60, "challenge_steps": MEMORY}, {"from": "04:00", "every_sec": 30}]}
AT_22 = datetime(2026, 10, 8, 22, 5)
AT_02 = datetime(2026, 10, 9, 2, 5)


class Env(BaseEnv):
    """The overlay test env with the bedtime challenge wired as resident.overlay.attach wires it."""

    def __init__(self, tmp_path, at=AT_22, **sleep):
        super().__init__(tmp_path)
        self.now = at
        self.sleep(**{**NIGHT_CFG, **sleep})
        self.overlay._challenge_for = lambda key: bedtime_challenge(self.stored(), self.now) if key == "sleep" else None

    def stored(self):
        return reminders.load(self.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)

    def sleep(self, **fields):
        reminders.save(self.db, reminders.SLEEP_KEY, {**self.stored(), **fields})

    def up(self):
        """The bedtime screen comes up (the engine's tick), and its Dismiss wait is over."""
        self.tick()
        assert self.overlay.key() == "sleep"
        self.mono.t += 30
        return self.overlay.payload()

    def dismiss(self):
        return self.call("overlay_answer", "dismiss")

    def open(self):
        """Dismiss after the wait -> the challenge ticket, opened (guard.begin) -> (ticket, what the page gets)."""
        reply = self.dismiss()["data"]
        assert reply["needs_challenge"], reply
        return reply["ticket"], self.ctx.guard.begin(reply["ticket"])["data"]

    def run(self):
        return self.ctx.guard._open.run

    def solve(self, ticket):
        """Answer the puzzle that is up (a person takes a few seconds)."""
        self.mono.t += 10
        return self.ctx.guard.submit(ticket, self.run().puzzle.answer)["data"]

    def solve_all(self, ticket):
        out = self.solve(ticket)
        while not out.get("passed"):
            out = self.solve(ticket)
        return out


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def kinds_of(data):
    return [(s["kind"], s["level"], s["times"]) for s in data["run"]["steps"]]


# ---------------------------------------------------------------- which list: the step active when the screen came up

def test_the_list_is_the_one_of_the_step_active_then_or_the_general_one():
    assert bedtime_challenge({**NIGHT_CFG, "challenge": False}, AT_22) is None            # off: no challenge
    at = lambda *hm: bedtime_challenge(NIGHT_CFG, datetime(2026, 10, 8, *hm) if hm[0] >= 22 else datetime(2026, 10, 9, *hm))
    assert at(21, 45)["steps"] == GENERAL                                                  # heads-up: before the first step
    assert at(22, 5)["steps"] == GENERAL                                                   # the 22:00 step has no list of its own
    assert at(23, 59)["steps"] == GENERAL
    assert at(0, 0)["steps"] == SORT and at(1, 30)["steps"] == SORT                        # 00:00: its own list
    assert at(2, 5)["steps"] == MEMORY and at(3, 59)["steps"] == MEMORY                    # 02:00: another own list
    assert at(4, 0)["steps"] == GENERAL and at(6, 0)["steps"] == GENERAL                   # 04:00: the general one again


def test_the_phrase_extras_are_the_bedtime_ones_and_never_an_own_phrase():
    cfg = {**NIGHT_CFG, "challenge_complex": True, "challenge_grid": True}
    got = bedtime_challenge(cfg, AT_22)
    assert got["complex"] is True and got["grid"] is True and set(got) == {"steps", "complex", "grid"}


def test_the_screen_shows_what_dismiss_will_ask_and_the_numbers_of_22_00_vs_02_00(tmp_path):
    at_22, at_02 = Env(tmp_path / "a", AT_22), Env(tmp_path / "b", AT_02)
    for env_ in (at_22, at_02):
        env_.tick()
    assert at_22.overlay.payload()["asks"] == "Math (Easy) ×2"
    assert at_02.overlay.payload()["asks"] == "Memory grid (Easy)"
    for env_ in (at_22, at_02):
        env_.mono.t += 30
    _, first = at_22.open()
    _, second = at_02.open()
    assert kinds_of(first) == [("math", 0, 2)] and first["mode"] == "math"
    assert kinds_of(second) == [("memory", 0, 1)] and second["mode"] == "memory"


def test_no_asks_line_and_a_plain_dismiss_with_the_challenge_off(tmp_path):
    env = Env(tmp_path, challenge=False)
    env.tick()
    assert "asks" not in env.overlay.payload()
    env.mono.t += 30
    assert env.dismiss()["data"] == {"done": True, "key": "sleep"}
    assert env.answers == [("sleep", "dismiss")]


def test_a_screen_keeps_the_list_it_came_up_with_when_the_next_step_starts(tmp_path):
    """Up at 23:58 (the general list), still up at 00:03 when the 00:00 step (Sorting) has begun: it asks the 23:58 list;
    the screen that comes back after that one asks the 00:00 step's own list."""
    env = Env(tmp_path, datetime(2026, 10, 8, 23, 58))
    env.tick()
    env.tick(5 * 60)                                                      # (no new screen: it is the same one, still up)
    env.mono.t += 400
    ticket, first = env.open()
    assert kinds_of(first) == [("math", 0, 2)]
    env.solve_all(ticket)
    env.tick(301)                                                         # the 00:00 step comes back every 5 min
    assert env.overlay.key() == "sleep"
    env.mono.t += 30
    _, second = env.open()
    assert kinds_of(second) == [("sort", 0, 1)]


# ---------------------------------------------------------------- Dismiss: wait, then the challenge, then it dismisses once

def test_an_early_dismiss_is_still_refused_and_asks_nothing(env):
    env.tick()
    env.mono.t += 10                                                      # (15 of the 25 s are left)
    reply = env.dismiss()["data"]
    assert reply == {"early": True, "wait_ms": 15_000}
    assert env.ctx.guard._open is None and env.answers == []


def test_after_the_wait_dismiss_asks_the_challenge_inside_the_screen(env):
    env.up()
    reply = env.dismiss()["data"]
    assert reply["needs_challenge"] and reply["ticket"] and reply["changes"] == [] and reply["more"] == 0
    assert reply["plain"] is True and reply["title"] == page.DISMISS_TITLE == "Solve this to dismiss"
    assert reply["note"] == "Passing dismisses the bedtime screen once."
    assert env.answers == [] and env.overlay.shown                        # nothing is dismissed by asking
    data = env.ctx.guard.begin(reply["ticket"])["data"]
    assert data["plain"] is True and data["title"] == "Solve this to dismiss" and data["note"] == reply["note"]
    assert data["mode"] == "math" and data["expr"] and data["changes"] == []
    assert data["run"]["steps"][0]["label"] == "Math, Easy" and data["run"]["step"] == 0 and data["run"]["done"] == 0
    assert "waiting_until" not in data and "next_open" not in data


def test_passing_the_list_dismisses_once_and_it_asks_the_whole_list_again_next_time(env):
    env.up()
    ticket, data = env.open()
    out = env.solve(ticket)                                               # 1 of 2: next puzzle, nothing dismissed
    assert out["passed"] is False and out["next"]["run"]["done"] == 1 and env.answers == []
    out = env.solve(ticket)                                               # 2 of 2: passed -> the Dismiss runs, once
    assert out["passed"] is True and out["result"] == {"done": True, "key": "sleep"}
    assert env.answers == [("sleep", "dismiss")] and not env.overlay.shown
    assert "unlocked" not in out and "waiting_until" not in out
    again = env.call("challenge_submit", ticket, "1")                     # the ticket works once
    assert not again["ok"] and env.answers == [("sleep", "dismiss")]
    # the screen comes back on its escalation (15 min at 22:05): the wait, then the WHOLE list again from step 1
    env.tick(15 * 60)
    assert env.overlay.key() == "sleep"
    env.mono.t += 30
    _, second = env.open()
    assert second["run"]["step"] == 0 and second["run"]["done"] == 0 and kinds_of(second) == [("math", 0, 2)]


def test_a_wrong_answer_gives_a_new_puzzle_and_dismisses_nothing(env):
    env.up()
    ticket, data = env.open()
    env.mono.t += 10
    out = env.ctx.guard.submit(ticket, "-1")["data"]
    assert out["wrong"] is True and out["next"]["expr"] and env.answers == [] and env.overlay.shown
    assert out["next"]["run"]["done"] == 0


def test_cancelling_the_challenge_leaves_the_screen_and_the_next_press_starts_over(env):
    env.up()
    ticket, _ = env.open()
    env.solve(ticket)                                                     # one sum done ...
    env.ctx.guard.cancel(ticket)                                          # ... then Cancel: the run is dropped
    assert env.overlay.shown and env.answers == []
    assert env.overlay.payload()["wait_ms"] == 0                          # (the wait is not asked again: it was over)
    ticket2, second = env.open()
    assert ticket2 != ticket and second["run"]["done"] == 0               # step 1 again, new puzzles
    assert not env.call("challenge_submit", ticket, "1")["ok"]


def test_the_challenge_is_asked_whatever_anti_bypass_says(tmp_path):
    for label, setup in (("free", lambda e: ab.save(e.db, {**ab.settings(e.db), "phrase": False})),
                         ("locked", lambda e: e.lock()),
                         ("closed", lambda e: e.lock(hours=True, windows=[{"days": [0], "start": "03:00", "end": "04:00"}])),
                         ("waiting", lambda e: (e.lock(wait_min=30), ab.unlock(e.db, e.now)))):
        env = Env(tmp_path / label)
        setup(env)
        env.up()
        ticket, data = env.open()
        assert data["plain"] is True and data["mode"] == "math", label      # never "closed" / "waiting" / the Anti-Bypass phrase
        out = env.solve_all(ticket)
        assert out["result"] == {"done": True, "key": "sleep"}, label
        assert env.answers == [("sleep", "dismiss")], label


def test_passing_it_opens_no_unlock_and_starts_no_wait(env):
    env.lock(wait_min=30)
    cfg = ab.settings(env.db)
    env.up()
    ticket, _ = env.open()
    env.solve_all(ticket)
    assert ab.unlocked_until(ab.settings(env.db), env.now) is None
    assert ab.waiting_until(ab.settings(env.db), env.now) is None and ab.settings(env.db) == cfg


def test_the_app_log_says_bedtime_challenge_not_anti_bypass(env):
    env.up()
    ticket, _ = env.open()
    env.mono.t += 10
    env.ctx.guard.submit(ticket, "-1")
    env.solve_all(ticket)
    events = applog.query(env.db.conn, limit=100)
    texts = [e["text"] for e in events]
    assert "Bedtime challenge failed: wrong answer in Math, Easy" in texts
    assert "Bedtime challenge passed (before Dismiss)" in texts
    assert not [t for t in texts if t.startswith("Anti-Bypass challenge")]
    assert {"challenge_failed", "challenge_passed"} <= {e["kind"] for e in events}


# ---------------------------------------------------------------- the ways round it are closed

def test_cancel_after_the_wait_does_not_dismiss_while_the_challenge_is_on(env):
    """Backing out of the Disable alerts choice / the emergency list used to be a Dismiss once the wait was over."""
    env.up()
    assert env.call("overlay_answer", "cancel")["data"] == {"back": True}
    assert env.overlay.shown and env.answers == []
    ticket, _ = env.open()                                                 # Dismiss still asks
    assert ticket


def test_cancel_during_the_wait_still_starts_the_wait_over(env):
    env.tick()
    env.mono.t += 10
    assert env.call("overlay_answer", "cancel")["data"] == {"back": True}
    assert env.overlay.payload()["wait_ms"] == 25_000 and env.answers == []


def test_cancel_after_the_wait_is_still_a_dismiss_with_the_challenge_off(tmp_path):
    env = Env(tmp_path, challenge=False)
    env.up()
    assert env.call("overlay_answer", "cancel")["data"] == {"done": True}
    assert env.answers == [("sleep", "dismiss")]


def solve_last(env, ticket):
    """Answer right until the last puzzle is done (as the page does, through the Api): the reply that is not "next"."""
    for _ in range(20):
        env.mono.t += 10
        reply = env.call("challenge_submit", ticket, env.run().puzzle.answer)
        if not reply["ok"] or reply["data"].get("passed"):
            return reply
    raise AssertionError("never passed")


def test_a_passed_challenge_for_a_screen_that_has_gone_dismisses_nothing(env):
    env.up()
    ticket, _ = env.open()
    env.overlay.close("sleep")                                             # the night ended meanwhile
    reply = solve_last(env, ticket)
    assert not reply["ok"] and "not on screen" in reply["error"]
    assert env.answers == []


def test_a_screen_that_came_back_is_not_the_one_the_old_challenge_was_for(env):
    env.up()
    ticket, _ = env.open()
    env.overlay.show("sleep", "Time for bed", "again", None, [("Dismiss", "dismiss"), ("Disable alerts", "disable")])
    reply = solve_last(env, ticket)
    assert not reply["ok"] and "not on screen" in reply["error"]
    assert env.answers == [] and env.overlay.shown


def test_the_overlay_has_no_other_way_to_dismiss_a_screen_that_asks(env):
    """Only dismiss_passed(token) after a passed challenge takes such a screen down; the page's actions cannot."""
    env.up()
    for action in ("cancel", "dismiss"):
        env.call("overlay_answer", action)
    for bad in ("accept", "dismiss_passed", "dismiss:1", "passed"):
        assert not env.call("overlay_answer", bad)["ok"]
    assert env.answers == [] and env.overlay.shown
    with pytest.raises(ValueError):
        env.overlay.dismiss_passed(env.overlay.token() + 1)                # a token of another screen


def test_a_screen_without_a_challenge_cannot_be_dismissed_through_dismiss_passed(tmp_path):
    env = Env(tmp_path, challenge=False)
    env.up()
    with pytest.raises(ValueError):
        env.overlay.dismiss_passed(env.overlay.token())
    assert env.answers == []


def test_the_break_cover_never_asks_a_bedtime_challenge(env):
    env.overlay.show("break-run", "Break time", "x", env.now + timedelta(minutes=5), [("Dismiss", "dismiss")])
    env.mono.t += 1
    assert "asks" not in env.overlay.payload()
    assert env.dismiss()["data"] == {"done": True, "key": "break-run"}


def test_a_bedtime_screen_waiting_behind_a_break_keeps_asking(env):
    env.tick()                                                              # bedtime screen up
    env.overlay.show("break-run", "Break time", "x", env.now + timedelta(minutes=5), [("Dismiss", "dismiss")])
    env.mono.t += 1
    env.dismiss()                                                           # the break cover goes; bedtime comes back
    assert env.overlay.key() == "sleep"
    env.mono.t += 30
    assert env.dismiss()["data"]["needs_challenge"]


# ---------------------------------------------------------------- Disable alerts and Emergency are as they were

def test_disable_alerts_is_still_the_anti_bypass_challenge(env):
    env.lock()
    env.up()
    reply = env.call("overlay_disable")["data"]
    assert reply["needs_challenge"] and reply["changes"] == [page.OFF_TEXT] and "plain" not in reply
    data = env.ctx.guard.begin(reply["ticket"])["data"]
    assert data["mode"] == "phrase" and "plain" not in data and "title" not in data     # the Anti-Bypass phrase, as before
    env.ctx.guard.cancel(reply["ticket"])


def test_disable_alerts_with_anti_bypass_free_needs_no_challenge_even_with_the_bedtime_one_on(env):
    env.up()
    assert env.call("overlay_disable")["data"] == {"choose": True}


def test_emergency_stays_free(env):
    env.lock()
    env.up()
    assert env.call("overlay_emergency", "t1")["data"]["done"] is True
    assert not env.overlay.shown and env.ctx.guard._open is None


def test_the_guard_asks_for_its_own_steps_only_when_the_bedtime_variant_is_used(env):
    """The Anti-Bypass path is the same as before: free -> runs at once; the bedtime variant needs steps."""
    ran = []
    assert env.ctx.guard.request(["x"], lambda: ran.append(1) or {"ran": True})["data"] == {"ran": True}
    with pytest.raises(ValueError):
        env.ctx.guard.request([], lambda: None, plain=True)
    with pytest.raises(ValueError):
        env.ctx.guard.request([], lambda: None, plain=True, steps=[{"kind": "nope"}])
    assert ran == [1]


# ---------------------------------------------------------------- Reminders > Sleep: saving the lists

SLEEP_BOXES = {"on": True, "guarded": True, "bedtime": "22:00", "wake": "07:00", "before": "30", "mode": "",
               "tiers": [{"every": "15 min", "after": "22:00"}, {"every": "5 min", "after": "00:00"}],
               "dismiss_wait": 0, "warn_text": "", "text": ""}


@pytest.fixture
def ctx(tmp_path):
    import webapi
    from webapi.guard import GuardService
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db=None: AT_22)
    c.guard = GuardService(lambda: c.db, lambda db=None: AT_22, rng=random.Random(5))
    return c


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


def save(api, **boxes):
    return api.reminder_save_sleep({**SLEEP_BOXES, **boxes})


def saved(ctx):
    return reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)


def test_the_card_saves_the_switch_the_general_list_and_each_steps_own_list(api, ctx):
    own = [{"id": "o1", "kind": "sort", "level": 1, "times": 2}]
    out = save(api, challenge=True, challenge_steps=[{"id": "g1", "kind": "math", "level": 1, "times": 3}],
               challenge_complex=True, challenge_grid=False,
               tiers=[{"every": "15 min", "after": "22:00", "challenge_steps": None},
                      {"every": "5 min", "after": "00:00", "challenge_steps": own}])
    assert out["ok"] and out["data"].get("saved"), out
    s = saved(ctx)
    assert s["challenge"] is True and s["challenge_complex"] is True and s["challenge_grid"] is False
    assert reminders.general_steps(s) == [{"id": "g1", "kind": "math", "level": 1, "times": 3}]
    assert [reminders.tier_steps(t) for t in reminders.tiers_of(s)] == [[], own]
    view = api.reminders_get()["data"]
    assert view["sleep"]["challenge"] is True and view["sleep"]["tiers"][1]["challenge_steps"] == own
    assert view["challenge_options"]["max_steps"] == 6


def test_a_list_the_page_makes_wrong_is_refused_and_nothing_changes(api, ctx):
    before = saved(ctx)
    for bad in ([], [{"id": "a", "kind": "nope", "level": 0, "times": 1}], [{"id": "a", "kind": "math", "level": 9, "times": 1}],
                [{"id": "a", "kind": "math", "level": 0, "times": 99}], "text"):
        assert not save(api, challenge=True, challenge_steps=bad)["ok"], bad
    assert not save(api, tiers=[{"every": "15 min", "after": "22:00", "challenge_steps": [{"kind": "x"}]}])["ok"]
    assert saved(ctx) == before


def _stored(ctx, **fields):
    """The saved Sleep settings, set straight in the database (the start of a test)."""
    reminders.save(ctx.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "guarded": True, "bedtime": "22:00",
                                                 "wake": "07:00", "challenge": True, "challenge_steps": GENERAL,
                                                 "tiers": [{"from": "22:00", "every_sec": 900},
                                                           {"from": "00:00", "every_sec": 300}], **fields})


def _lock(ctx):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})


def test_making_the_bedtime_challenge_easier_asks_only_while_the_bedtime_is_important(api, ctx):
    _lock(ctx)
    _stored(ctx)
    easier = [{"id": "g1", "kind": "math", "level": 0, "times": 1}]                           # 2 sums -> 1
    reply = save(api, guarded=True, challenge=True, challenge_steps=easier)["data"]
    assert reply.get("needs_challenge") and "challenge before dismiss" in " ".join(reply["changes"]).lower()
    assert reminders.general_steps(saved(ctx)) == GENERAL                                     # nothing saved yet
    for harder in ([{"id": "g1", "kind": "math", "level": 2, "times": 2}],                    # a higher level: free
                   GENERAL + [{"id": "g2", "kind": "sort", "level": 0, "times": 1}],          # a step added: free
                   [{"id": "g1", "kind": "math", "level": 0, "times": 5}],                    # more times: free
                   list(reversed(GENERAL + [{"id": "g2", "kind": "sort", "level": 0, "times": 1}]))):   # reordered: free
        out = save(api, guarded=True, challenge=True, challenge_steps=harder)
        assert out["data"].get("saved"), out
        _stored(ctx)
    assert save(api, guarded=True, challenge=False)["data"].get("needs_challenge")            # off: asks
    assert save(api, guarded=True, challenge=True, challenge_steps=[{"id": "g1", "kind": "sort", "level": 2, "times": 9}]
                )["data"].get("needs_challenge")                                              # another kind: asks
    _stored(ctx, guarded=False)                                                               # not Important: free
    assert save(api, guarded=False, challenge=True, challenge_steps=easier)["data"].get("saved")
    assert save(api, guarded=False, challenge=False)["data"].get("saved")


def test_turning_it_on_and_changing_the_lists_before_it_was_on_is_free(api, ctx):
    _lock(ctx)
    _stored(ctx, challenge=False)
    assert save(api, guarded=True, challenge=True, challenge_steps=GENERAL)["data"].get("saved")
    _stored(ctx, challenge=False)
    easy = [{"id": "z", "kind": "sort", "level": 0, "times": 1}]
    assert save(api, guarded=True, challenge=False, challenge_steps=easy)["data"].get("saved")   # (it was off: nothing to loosen)


def test_an_own_list_made_easier_asks_too_and_so_does_giving_a_step_back_to_the_general_list(api, ctx):
    _lock(ctx)
    hard = [{"id": "h1", "kind": "math", "level": 2, "times": 5}]
    tiers = [{"every": "15 min", "after": "22:00", "challenge_steps": None},
             {"every": "5 min", "after": "00:00", "challenge_steps": hard}]
    _stored(ctx, tiers=[{"from": "22:00", "every_sec": 900}, {"from": "00:00", "every_sec": 300, "challenge_steps": hard}])
    soft = [{"id": "h1", "kind": "math", "level": 0, "times": 1}]
    easier = [tiers[0], {**tiers[1], "challenge_steps": soft}]                    # 00:00's own list: Hard x5 -> Easy x1
    assert save(api, challenge=True, challenge_steps=GENERAL, tiers=easier)["data"].get("needs_challenge")
    back = [tiers[0], {**tiers[1], "challenge_steps": None}]                      # its own list taken away: the general list
    assert save(api, challenge=True, challenge_steps=GENERAL, tiers=back)["data"].get("needs_challenge")
    harder = [{**tiers[0], "challenge_steps": [{"id": "g1", "kind": "math", "level": 3, "times": 4}]}, tiers[1]]
    assert save(api, challenge=True, challenge_steps=GENERAL, tiers=harder)["data"].get("saved")   # 22:00 gets a harder own list


def test_the_general_list_made_easier_asks_only_where_the_general_list_is_in_force(api, ctx):
    """Both steps have their own list: the general list is not asked at any minute of the night, so editing it is free."""
    _lock(ctx)
    own = [{"id": "h1", "kind": "math", "level": 2, "times": 5}]
    both = [{"from": "22:00", "every_sec": 900, "challenge_steps": own}, {"from": "00:00", "every_sec": 300, "challenge_steps": own}]
    _stored(ctx, tiers=both)
    tiers = [{"every": "15 min", "after": "22:00", "challenge_steps": own}, {"every": "5 min", "after": "00:00", "challenge_steps": own}]
    easy = [{"id": "g1", "kind": "math", "level": 0, "times": 1}]
    out = save(api, challenge=True, challenge_steps=easy, tiers=tiers)
    assert out["data"].get("saved"), out


def test_the_phrase_extras_off_ask_only_with_a_phrase_step_in_a_list(api, ctx):
    _lock(ctx)
    phrase = [{"id": "p1", "kind": "phrase", "level": 30, "times": 1}]
    _stored(ctx, challenge_steps=phrase, challenge_complex=True, challenge_grid=True)
    assert save(api, guarded=True, challenge=True, challenge_steps=phrase, challenge_complex=False,
                challenge_grid=True)["data"].get("needs_challenge")
    assert save(api, guarded=True, challenge=True, challenge_steps=phrase, challenge_complex=True,
                challenge_grid=False)["data"].get("needs_challenge")
    assert save(api, guarded=True, challenge=True, challenge_steps=phrase, challenge_complex=True,
                challenge_grid=True)["data"].get("saved")
    _stored(ctx, challenge_complex=True, challenge_grid=True)                                 # no phrase step in any list
    assert save(api, guarded=True, challenge=True, challenge_steps=GENERAL, challenge_complex=False,
                challenge_grid=False)["data"].get("saved")


def test_a_step_sent_without_the_key_keeps_its_own_list(api, ctx):
    own = [{"id": "o1", "kind": "sort", "level": 1, "times": 2}]
    _stored(ctx, tiers=[{"from": "22:00", "every_sec": 900}, {"from": "00:00", "every_sec": 300, "challenge_steps": own}])
    out = save(api, tiers=[{"every": "15 min", "after": "22:00"}, {"every": "10 min", "after": "00:00"}])      # (key not sent)
    assert out["data"].get("saved"), out
    assert [reminders.tier_steps(t) for t in reminders.tiers_of(saved(ctx))] == [[], own]
