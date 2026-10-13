"""R24: the Anti-Bypass page's Api (webapi/antibypass_page.py) - what loosens is decided in Python (settings_looser,
pause rules), the page's "confirmed" only answers "are you sure?" and never skips that check, the unlock state and every
other setting can't be written from the page, and each banner state has its data."""
import random
from datetime import datetime, timedelta

import pytest

import antibypass as ab
import modes
import pause
import webapi
from webapi import antibypass_page as page
from webapi import core
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)          # a Sunday
SUN_EVENING = [{"days": [6], "start": "18:00", "end": "20:00"}]


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


@pytest.fixture
def clock():
    """The trusted time, settable: clock.now = ..."""
    class Clock:
        now = NOW
    return Clock


@pytest.fixture
def ctx(tmp_path, clock):
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: clock.now)
    c.guard = GuardService(lambda: c.db, lambda db: clock.now, rng=random.Random(3))
    return c


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def error(reply):
    assert not reply["ok"], reply
    return reply["error"]


def stored(ctx):
    return ab.settings(ctx.db)


def setup_ab(ctx, **kw):
    ab.save(ctx.db, {**ab.settings(ctx.db), **kw})


def locked(ctx, **kw):
    """Anti-Bypass on with a phrase of 60 characters (or what you pass): a loosening change must pass the challenge."""
    setup_ab(ctx, **{"phrase": True, "length": 60, **kw})


def pass_challenge(ctx, api, ticket):
    """What the page does after the Python challenge: begin, type what Python showed, submit (a person's typing time)."""
    begin = data(api.challenge_begin(ticket))
    mono = ctx.guard._mono
    ctx.guard._mono = lambda: mono() + 60
    typed = begin["grid_words"] if begin["mode"] == "grid" else begin["phrase"]
    return data(api.challenge_submit(ticket, typed))


def needs_ticket(reply):
    d = data(reply)
    assert d.get("needs_challenge") and d.get("ticket"), d
    return d["ticket"]


# ---------------------------------------------------------------- classification

def test_methods_are_classified():
    kinds = dict(webapi.api_methods())
    assert kinds["antibypass_get"] == "read" and kinds["pause_get"] == "read"
    assert kinds["antibypass_lock"] == "tighten"
    for name in ("antibypass_save", "antibypass_unlock_to_edit", "antibypass_try", "pause_start",
                 "lockdown_switch_off"):
        assert kinds[name] == "loosen", name
    # (the free ones the page also calls live in webapi/shell.py)
    assert kinds["pause_resume"] == "tighten" and kinds["antibypass_switch_on"] == "tighten"


def test_there_is_no_confirmed_argument_on_any_other_method():
    import inspect
    for name, kind in webapi.api_methods():
        fn = core.registry.get(name)[0]
        if "confirmed" in inspect.signature(fn).parameters:
            assert name == "antibypass_save", name


# ---------------------------------------------------------------- the page data

def test_the_page_data_has_everything(api, ctx):
    d = data(api.antibypass_get())
    assert d["now"] == "2026-10-11T15:42:17"
    assert d["config"] == {"phrase": False, "length": 60, "grid": False, "complex": False, "use_own": False,
                           "custom_phrase": "", "hours": False, "windows": SUN_EVENING, "wait_min": 0, "wait_text": "Off",
                           "steps": [{"id": "s0", "kind": "phrase", "level": 60, "times": 1}]}
    assert d["status"] == "free" and d["banner"]["kind"] == "off"
    assert d["lengths"] == {"Short": 30, "Medium": 60, "Long": 120, "Very long": 250}
    assert (d["min_length"], d["max_length"], d["unlock_min"]) == (30, 2000, 5)
    assert d["pause"]["running"] is False and d["lockdown"] == {"off": False, "since": None}
    assert [(o["label"], o["minutes"]) for o in d["pause"]["options"]] == [
        ("30 min", 30), ("1 h", 60), ("2 h", 120), ("4 h", 240), ("Rest of the day", None)]


def test_banner_off(api):
    b = data(api.antibypass_get())["banner"]
    assert b["kind"] == "off" and b["ends_at"] is None
    assert b["summary"] == "Off - loosening a block needs nothing."


def test_banner_locked(api, ctx):
    locked(ctx)
    b = data(api.antibypass_get())["banner"]
    assert b["kind"] == "locked" and b["ends_at"] is None
    assert b["summary"] == "Loosening a block needs: typing a 60-character phrase"


def test_banner_unlocked_counts_down_to_the_end_of_the_unlock(api, ctx, clock):
    locked(ctx)
    ab.unlock(ctx.db, NOW)
    b = data(api.antibypass_get())["banner"]
    assert b["kind"] == "unlocked" and b["ends_at"] == "2026-10-11T15:47:17" and b["total_sec"] == 300
    clock.now = NOW + timedelta(minutes=6)
    assert data(api.antibypass_get())["banner"]["kind"] == "locked"          # ran out


def test_banner_waiting_has_the_unlock_time_and_the_whole_wait(api, ctx, clock):
    locked(ctx, wait_min=10)
    ab.unlock(ctx.db, NOW)
    d = data(api.antibypass_get())
    assert d["status"] == "waiting"
    assert d["banner"]["kind"] == "waiting" and d["banner"]["ends_at"] == "2026-10-11T15:52:17"
    assert d["banner"]["total_sec"] == 600
    clock.now = NOW + timedelta(minutes=11)                                   # the wait is over: unlocked
    assert data(api.antibypass_get())["banner"]["kind"] == "unlocked"


def test_banner_closed_has_the_next_chance(api, ctx, clock):
    setup_ab(ctx, hours=True, windows=SUN_EVENING)
    d = data(api.antibypass_get())
    assert d["status"] == "closed"
    assert d["banner"]["kind"] == "closed" and d["banner"]["next_open"] == "2026-10-11T18:00:00"
    assert d["banner"]["ends_at"] == "2026-10-11T18:00:00"
    assert d["banner"]["hours"] == "Sunday 18:00-20:00"


def test_banner_free_inside_the_allowed_hours(api, ctx, clock):
    setup_ab(ctx, hours=True, windows=SUN_EVENING)
    clock.now = datetime(2026, 10, 11, 19, 0)
    b = data(api.antibypass_get())["banner"]
    assert b["kind"] == "free" and b["ends_at"] == "2026-10-11T20:00:00" and b["next_open"] is None


def test_the_phrase_is_asked_even_inside_the_hours_when_both_are_on(api, ctx, clock):
    locked(ctx, hours=True, windows=SUN_EVENING)
    clock.now = datetime(2026, 10, 11, 19, 0)
    assert data(api.antibypass_get())["banner"]["kind"] == "locked"


# ---------------------------------------------------------------- saving: what is free, what needs a yes, what needs the challenge

def test_a_stricter_change_needs_the_yes_and_then_saves(api, ctx):
    out = data(api.antibypass_save({"phrase": True}))
    assert out == {"needs_confirm": True, "line": "Loosening a block needs: typing a 60-character phrase"}
    assert stored(ctx)["phrase"] is False                                     # nothing was written
    saved = data(api.antibypass_save({"phrase": True}, True))
    assert saved["config"]["phrase"] is True and stored(ctx)["phrase"] is True


def test_a_longer_phrase_has_no_challenge_only_the_yes(api, ctx):
    locked(ctx)
    assert data(api.antibypass_save({"length": 250}))["needs_confirm"] is True
    out = data(api.antibypass_save({"length": 250}, True))
    assert out["config"]["length"] == 250 and stored(ctx)["length"] == 250


def test_the_longest_phrase_is_accepted_and_a_too_short_one_refused(api, ctx):
    assert data(api.antibypass_save({"length": 2000}, True))["config"]["length"] == 2000
    assert "30 to 2000" in error(api.antibypass_save({"length": 29}, True))
    assert "30 to 2000" in error(api.antibypass_save({"length": 2001}, True))
    assert "30 to 2000" in error(api.antibypass_save({"length": "many"}, True))
    assert "30 to 2000" in error(api.antibypass_save({"length": True}, True))
    assert stored(ctx)["length"] == 2000


def test_a_shorter_phrase_while_locked_gets_a_ticket_even_with_confirmed_true(api, ctx):
    locked(ctx)
    for confirmed in (False, True):
        d = data(api.antibypass_save({"length": 30}, confirmed))
        assert d["needs_challenge"] and d["ticket"], d
        assert d["changes"] == ["Weaken Anti-Bypass"]
    assert stored(ctx)["length"] == 60                                        # nothing written before the challenge


def test_the_ticket_saves_the_change_once_the_challenge_is_passed(api, ctx):
    locked(ctx)
    ticket = needs_ticket(api.antibypass_save({"length": 30}))
    out = pass_challenge(ctx, api, ticket)
    assert out["passed"] and out["result"]["config"]["length"] == 30
    cfg = stored(ctx)
    assert cfg["length"] == 30 and cfg["unlocked_until"]                      # the unlock the challenge opened is kept


def test_a_shorter_phrase_is_free_while_unlocked(api, ctx):
    locked(ctx)
    ab.unlock(ctx.db, NOW)
    d = data(api.antibypass_save({"length": 30}))
    assert d["config"]["length"] == 30                                         # (loosening, but the guard says free)


def test_turning_the_phrase_off_while_locked_is_a_ticket(api, ctx):
    locked(ctx)
    needs_ticket(api.antibypass_save({"phrase": False}, True))
    assert stored(ctx)["phrase"] is True


def test_dropping_numbers_and_the_grid_while_locked_are_tickets(api, ctx):
    locked(ctx, complex=True, grid=True)
    needs_ticket(api.antibypass_save({"complex": False}))
    needs_ticket(api.antibypass_save({"grid": False}))
    assert data(api.antibypass_save({"complex": True, "grid": True}, True))["config"]["grid"] is True   # no change


def test_adding_numbers_or_the_grid_is_only_a_yes(api, ctx):
    locked(ctx)
    assert data(api.antibypass_save({"complex": True}))["needs_confirm"]
    assert data(api.antibypass_save({"complex": True, "grid": True}, True))["config"]["grid"] is True


def test_switching_your_own_phrase_on_is_looser_whatever_its_length(api, ctx):
    locked(ctx)
    needs_ticket(api.antibypass_save({"use_own": True, "custom_phrase": "short one"}, True))
    long_phrase = "I promised myself one focused evening, and I keep my promises. " * 2
    needs_ticket(api.antibypass_save({"use_own": True, "custom_phrase": long_phrase}, True))
    assert stored(ctx)["use_own"] is False and stored(ctx)["custom_phrase"] == ""


def test_your_own_phrase_is_kept_while_it_is_off_and_saving_it_then_asks_nothing(api, ctx):
    locked(ctx)
    out = data(api.antibypass_save({"custom_phrase": "short one"}))           # not asked: saved at once
    assert out["config"]["custom_phrase"] == "short one" and out["config"]["use_own"] is False
    assert len(ctx.guard.begin(needs_ticket(api.antibypass_unlock_to_edit()))["data"]["phrase"].replace(" ", "")) == 60


def test_switching_your_own_phrase_off_is_a_yes_unless_the_random_one_is_shorter(api, ctx):
    locked(ctx, use_own=True, custom_phrase="x" * 40)
    assert data(api.antibypass_save({"use_own": False}))["needs_confirm"]      # a random 60 instead of your 40
    out = data(api.antibypass_save({"use_own": False}, True))
    assert out["config"]["use_own"] is False and out["config"]["custom_phrase"] == "x" * 40   # kept
    locked(ctx, use_own=True, custom_phrase="x" * 100)
    needs_ticket(api.antibypass_save({"use_own": False}, True))                # a random 60 instead of your 100
    assert data(api.antibypass_save({"length": 30}))["config"]["length"] == 30   # not in use while yours is on: no question


def test_switching_your_own_phrase_on_needs_one(api, ctx):
    assert "write it first" in error(api.antibypass_save({"use_own": True}, True))
    assert "yes or no" in error(api.antibypass_save({"use_own": "yes"}, True))
    assert data(api.antibypass_save({"use_own": True, "custom_phrase": "my own words"}, True))["config"]["use_own"] is True


def test_removing_the_own_phrase_that_was_longer_than_the_random_one_is_looser(api, ctx):
    locked(ctx, use_own=True, custom_phrase="x" * 100)
    needs_ticket(api.antibypass_save({"custom_phrase": ""}, True))


def test_emptying_your_own_phrase_switches_it_off(api, ctx):
    locked(ctx, use_own=True, custom_phrase="x" * 40)
    out = data(api.antibypass_save({"custom_phrase": ""}, True))
    assert out["config"]["use_own"] is False and stored(ctx)["use_own"] is False


def test_an_own_phrase_is_checked(api, ctx):
    assert "text" in error(api.antibypass_save({"custom_phrase": 5}, True))
    assert "at most" in error(api.antibypass_save({"custom_phrase": "x" * 2001}, True))
    assert "punctuation" in error(api.antibypass_save({"custom_phrase": "bad\x00phrase"}, True))
    assert data(api.antibypass_save({"custom_phrase": "  two   spaces\nand a line  "}, True))["config"][
        "custom_phrase"] == "two spaces and a line"


def test_less_waiting_while_locked_is_a_ticket_and_more_waiting_is_a_yes(api, ctx):
    locked(ctx, wait_min=60)
    needs_ticket(api.antibypass_save({"wait": "10"}, True))
    needs_ticket(api.antibypass_save({"wait": "Off"}, True))
    assert data(api.antibypass_save({"wait": "1h30"}))["needs_confirm"]
    assert data(api.antibypass_save({"wait": "1h30"}, True))["config"]["wait_min"] == 90


def test_the_wait_is_written_like_the_old_box(api, ctx):
    assert data(api.antibypass_save({"wait": "10"}, True))["config"]["wait_text"] == "10 min"
    assert data(api.antibypass_save({"wait": "1h"}, True))["config"]["wait_min"] == 60
    assert data(api.antibypass_save({"wait": "Off"}, True))["config"]["wait_min"] == 0
    assert "Between" in error(api.antibypass_save({"wait": "45s"}, True))       # (a wait is at least a minute)
    assert "Write a time" in error(api.antibypass_save({"wait": "soon"}, True))
    assert "write a time" in error(api.antibypass_save({"wait": True}, True))
    assert stored(ctx)["wait_min"] == 0


def test_other_hours_are_a_ticket_while_it_is_locked(api, ctx, clock):
    two = SUN_EVENING + [{"days": [2], "start": "10:00", "end": "12:00"}]
    setup_ab(ctx, hours=True, windows=two)                                    # Sunday 15:42: outside both: closed
    needs_ticket(api.antibypass_save({"windows": SUN_EVENING}))               # a window less: looser
    needs_ticket(api.antibypass_save({"hours": False}))                       # the hours switched off: looser
    more = two + [{"days": [4], "start": "09:00", "end": "10:00"}]
    needs_ticket(api.antibypass_save({"windows": more}))                      # any other hours: looser (as before)
    assert stored(ctx)["windows"] == two
    # inside the hours with the phrase on too: the phrase is what is asked
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True})
    clock.now = datetime(2026, 10, 11, 19, 0)
    needs_ticket(api.antibypass_save({"windows": SUN_EVENING}))


def test_the_same_hours_written_another_way_are_not_a_change(api, ctx):
    setup_ab(ctx, hours=True, windows=SUN_EVENING)
    out = data(api.antibypass_save({"windows": [{"days": [6], "start": "18:0", "end": "20:00"}]}))
    assert out["config"]["windows"] == SUN_EVENING                             # nothing to confirm, no ticket


def test_turning_the_hours_on_is_only_a_yes_and_says_the_hours(api, ctx):
    out = data(api.antibypass_save({"hours": True}))
    assert out == {"needs_confirm": True, "line": "Loosening a block needs: only Sunday 18:00-20:00"}
    assert data(api.antibypass_save({"hours": True}, True))["status"] == "closed"


def test_hours_are_checked(api, ctx):
    assert "at least one day" in error(api.antibypass_save({"windows": [{"days": [], "start": "18:00", "end": "20:00"}]}, True))
    assert "Hours must look like" in error(api.antibypass_save({"windows": [{"days": [1], "start": "6pm", "end": "20:00"}]}, True))
    assert "Hours" in error(api.antibypass_save({"windows": [{"days": [7], "start": "18:00", "end": "20:00"}]}, True))
    assert "Hours" in error(api.antibypass_save({"windows": [{"days": ["1"], "start": "18:00", "end": "20:00"}]}, True))
    assert "Hours" in error(api.antibypass_save({"windows": "always"}, True))
    assert "Hours" in error(api.antibypass_save({"windows": [{"days": [1], "start": "18:00", "end": "20:00"}] * 15}, True))
    ok = data(api.antibypass_save({"windows": [{"days": [4, 0, 4], "start": "9:30", "end": "21:00"},
                                               {"days": [], "start": "x", "end": "y"}]}, True))
    assert ok["config"]["windows"] == [{"days": [0, 4], "start": "09:30", "end": "21:00"}]   # sorted, padded, empty one dropped


def test_only_the_challenge_settings_can_be_written(api, ctx):
    locked(ctx)
    for key in ("unlocked_until", "unlocked_from", "active", "windows_x", "__proto__"):
        assert "unknown setting" in error(api.antibypass_save({key: "2099-01-01 00:00:00"}, True))
    assert stored(ctx)["unlocked_until"] is None
    assert "settings expected" in error(api.antibypass_save("phrase", True))
    assert "yes or no" in error(api.antibypass_save({"phrase": "false"}, True))
    assert "yes or no" in error(api.antibypass_save({"phrase": True}, "yes"))


def test_nothing_to_change_is_not_a_change(api, ctx):
    locked(ctx)
    assert data(api.antibypass_save({}))["config"]["phrase"] is True
    assert data(api.antibypass_save({"phrase": True, "length": 60}))["status"] == "phrase"


def test_a_change_while_the_hours_are_closed_is_only_ever_closed(api, ctx):
    setup_ab(ctx, hours=True, windows=SUN_EVENING)                              # 15:42 on Sunday: closed
    ticket = needs_ticket(api.antibypass_save({"hours": False}))
    begin = data(api.challenge_begin(ticket))
    assert begin["mode"] == "closed" and begin["next_open"] == "2026-10-11T18:00:00"
    assert not api.challenge_submit(ticket, "anything")["ok"]
    assert stored(ctx)["hours"] is True


def test_a_challenge_ticket_is_the_only_way_to_a_looser_save_during_a_wait(api, ctx):
    locked(ctx, wait_min=10)
    ticket = needs_ticket(api.antibypass_save({"wait": "Off"}))
    out = pass_challenge(ctx, api, ticket)
    assert out["passed"] and out["waiting_until"] == "2026-10-11T15:52:17"      # a cooling-off: nothing saved yet
    assert stored(ctx)["wait_min"] == 10


# ---------------------------------------------------------------- Lock now / Unlock to edit / Try

def test_lock_now_is_free_and_ends_the_unlock(api, ctx):
    locked(ctx)
    ab.unlock(ctx.db, NOW)
    assert data(api.antibypass_get())["banner"]["kind"] == "unlocked"
    d = data(api.antibypass_lock())
    assert d["banner"]["kind"] == "locked" and stored(ctx)["unlocked_until"] is None


def test_unlock_to_edit_needs_the_challenge_and_then_opens_the_unlock(api, ctx):
    locked(ctx)
    ticket = needs_ticket(api.antibypass_unlock_to_edit())
    begin = data(api.challenge_begin(ticket))
    assert begin["changes"] == ["Allow loosening changes for 5 minutes"]
    out = pass_challenge(ctx, api, ticket)
    assert out["passed"] and out["result"]["banner"]["kind"] == "unlocked"


def test_unlock_to_edit_with_a_wait_starts_the_wait(api, ctx):
    locked(ctx, wait_min=10)
    out = pass_challenge(ctx, api, needs_ticket(api.antibypass_unlock_to_edit()))
    assert out["waiting_until"] == "2026-10-11T15:52:17"
    assert data(api.antibypass_get())["banner"]["kind"] == "waiting"


def test_unlock_to_edit_while_closed_is_only_the_closed_note(api, ctx):
    setup_ab(ctx, hours=True, windows=SUN_EVENING)
    ticket = needs_ticket(api.antibypass_unlock_to_edit())
    assert data(api.challenge_begin(ticket))["mode"] == "closed"


def test_try_the_challenge_shows_it_even_with_nothing_turned_on_and_changes_no_setting(api, ctx):
    before = stored(ctx)
    ticket = needs_ticket(api.antibypass_try())
    begin = data(api.challenge_begin(ticket))
    assert begin["mode"] == "phrase" and begin["changes"] == ["Try the challenge - nothing is changed"]
    out = pass_challenge(ctx, api, ticket)
    assert out["passed"] and out["dry"]
    assert stored(ctx) == before                         # a dry run (R28): no unlock opened, no wait started either


def test_try_is_a_dry_run_even_when_locked_with_a_wait(api, ctx):
    locked(ctx, wait_min=30)
    before = stored(ctx)
    out = pass_challenge(ctx, api, needs_ticket(api.antibypass_try()))
    assert out["passed"] and "waiting_until" not in out
    assert stored(ctx) == before and ab.status(stored(ctx), NOW) == "phrase"     # still locked, no wait running


def test_try_while_unlocked_still_shows_the_challenge_and_leaves_the_unlock_as_it_is(api, ctx):
    locked(ctx)
    ab.unlock(ctx.db, NOW)
    before = stored(ctx)
    out = pass_challenge(ctx, api, needs_ticket(api.antibypass_try()))
    assert out["passed"] and stored(ctx) == before


# ---------------------------------------------------------------- Pause my blocks

def test_starting_a_pause_with_the_challenge_on_is_a_ticket_and_free_without(api, ctx):
    d = data(api.pause_start(60, False))                                        # nothing turned on: free
    assert d["running"] and d["until"] == "2026-10-11T16:42:17" and d["silent"] is False
    pause.resume(ctx.db)
    locked(ctx)
    ticket = needs_ticket(api.pause_start(60, True))
    assert pause.state(ctx.db, NOW) is None                                     # nothing before the challenge
    begin = data(api.challenge_begin(ticket))
    assert begin["changes"][0].startswith("Pause all your blocks for 1 h (until 16:42)")
    assert begin["changes"][0].endswith("and silence all notifications")
    out = pass_challenge(ctx, api, ticket)
    assert out["result"]["running"] and out["result"]["silent"] is True
    assert pause.state(ctx.db, NOW + timedelta(minutes=1))["silent"] is True


def test_every_option_starts_a_pause_and_none_can_run_longer_than_a_day(api, ctx):
    for label, minutes in pause.DURATIONS.items():
        d = data(api.pause_start(minutes, False))
        end = datetime.fromisoformat(d["until"])
        assert end - NOW <= pause.MAX_SPAN, label
        assert pause.state(ctx.db, NOW + timedelta(seconds=1)) is not None
    d = data(api.pause_start(None, False))                                      # the rest of the day: the next reset time
    assert d["until"] == "2026-10-12T00:00:00"


@pytest.mark.parametrize("minutes", [0, 1, 45, 61, 1440, 1500, 10 ** 9, -30, 30.5, "30", True, False, [30]])
def test_a_pause_that_is_not_one_of_the_five_is_refused(api, ctx, minutes):
    assert "Pick 30 min" in error(api.pause_start(minutes, False))
    assert pause.state(ctx.db, NOW) is None


def test_the_pause_flag_must_be_a_flag(api, ctx):
    assert "yes or no" in error(api.pause_start(30, "yes"))


def test_a_pause_while_a_locked_mode_is_on_asks_even_with_no_challenge(api, ctx):
    modes.start(ctx.db, "work", NOW, NOW + timedelta(hours=2), locked=True)
    assert data(api.pause_get())["locked_mode"] is True
    ticket = needs_ticket(api.pause_start(30, False))
    assert pause.state(ctx.db, NOW) is None
    assert pass_challenge(ctx, api, ticket)["passed"]


def test_resuming_is_free_and_ends_the_pause(api, ctx):
    data(api.pause_start(240, False))
    assert data(api.pause_get())["running"] is True
    locked(ctx)
    data(api.pause_resume())                                                    # no challenge, even while locked
    assert data(api.pause_get())["running"] is False


def test_pause_get_when_running(api, ctx):
    data(api.pause_start(30, True))
    d = data(api.pause_get())
    assert (d["running"], d["silent"], d["until"], d["started"]) == (True, True, "2026-10-11T16:12:17", "2026-10-11T15:42:17")


# ---------------------------------------------------------------- Turn Lockdown off

def test_switching_off_always_asks_even_with_nothing_turned_on(api, ctx):
    ticket = needs_ticket(api.lockdown_switch_off())
    assert not ab.is_off(ctx.db)
    begin = data(api.challenge_begin(ticket))
    assert begin["mode"] == "phrase" and begin["changes"][0].startswith("Turn Lockdown off entirely")
    out = pass_challenge(ctx, api, ticket)
    assert out["result"] == {"off": True, "since": "2026-10-11 15:42:17"}
    assert ab.is_off(ctx.db)


def test_switching_off_while_unlocked_is_at_once(api, ctx):
    locked(ctx)
    ab.unlock(ctx.db, NOW)
    assert data(api.lockdown_switch_off())["off"] is True


def test_switching_off_again_changes_nothing_and_switching_on_is_free(api, ctx):
    ab.switch_off(ctx.db, NOW)
    since = ab.off_since(ctx.db)
    assert data(api.lockdown_switch_off()) == {"off": True, "since": since}
    locked(ctx)
    data(api.antibypass_switch_on())                                            # no challenge
    assert not ab.is_off(ctx.db)
    assert data(api.antibypass_get())["lockdown"] == {"off": False, "since": None}


def test_the_off_card_reports_since_when(api, ctx):
    ab.switch_off(ctx.db, NOW)
    assert data(api.antibypass_get())["lockdown"] == {"off": True, "since": "2026-10-11 15:42:17"}


# ---------------------------------------------------------------- describe (the old window's words, kept)

def test_describe_is_the_old_windows_line():
    base = {**ab.DEFAULTS}
    assert page.describe(base) == "Off - loosening a block needs nothing."
    assert page.describe({**base, "phrase": True, "grid": True, "complex": True, "length": 120}) == \
        "Loosening a block needs: typing a 120-character phrase (with numbers & capitals) in a 3×3 grid"
    assert page.describe({**base, "phrase": True, "custom_phrase": "x", "hours": True}) == \
        "Loosening a block needs: typing your own phrase · only Sunday 18:00-20:00"


def test_describe_more_settings():
    base = {**ab.DEFAULTS}
    assert page.describe({**base, "phrase": True, "custom_phrase": "my own"}) == \
        "Loosening a block needs: typing your own phrase"
    assert page.describe({**base, "hours": True}) == "Loosening a block needs: only Sunday 18:00-20:00"
    assert page.describe({**base, "phrase": True, "hours": True,
                          "windows": [{"days": [0, 1, 2], "start": "09:00", "end": "17:00"}]}) == \
        "Loosening a block needs: typing a 60-character phrase · only Monday–Wednesday 09:00-17:00"
