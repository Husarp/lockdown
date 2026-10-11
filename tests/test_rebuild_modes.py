"""R25: the Modes page's Api (webapi/modes.py) - start / stop / save / delete decided in Python, the locked-mode bypasses
closed (delete or weaken the mode that is on and locked -> challenge), a scheduled mode's commitments, "Until..." on the
trusted clock, built-in names fixed, and the little check behind "+ Other site or app"."""
import json
import random
from datetime import datetime, timedelta

import pytest

import antibypass as ab
import modes
import webapi
from rules import make_schedule
from webapi import core
from webapi import modes as page
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)       # a Sunday


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


class Poked:
    def __init__(self):
        self.pokes = 0

    def poke(self):
        self.pokes += 1


@pytest.fixture
def ctx(tmp_path):
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    c.guard = GuardService(lambda: c.db, lambda db: NOW, rng=random.Random(3))
    hub = type("Hub", (), {})()
    hub.status_worker = Poked()
    c.hub = hub
    return c


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


@pytest.fixture
def locked(ctx):
    """Anti-Bypass on with a phrase: a loosening change now has to pass the challenge."""
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})
    return ctx


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def refused(reply):
    assert not reply["ok"], reply
    return reply["error"]


def pass_challenge(ctx, ticket, api):
    """Do what the page does after the Python challenge: begin, type it (a person's typing time), submit."""
    begin = data(api.challenge_begin(ticket))
    mono = ctx.guard._mono
    ctx.guard._mono = lambda: mono() + 60
    return data(api.challenge_submit(ticket, begin["phrase"]))


def stored(ctx, mode_id):
    return next(m for m in modes.load(ctx.db) if m["id"] == mode_id)


def active(ctx):
    return modes.active(ctx.db, NOW)


def start_locked(ctx, mode_id="work", hours=1):
    modes.start(ctx.db, mode_id, NOW, NOW + timedelta(hours=hours), locked=True)


def edit(ctx, mode_id, **changes):
    """The page's version of a stored mode (as modes_get sends it), with some boxes changed."""
    m = stored(ctx, mode_id)
    windows = None
    if m.get("schedule"):
        windows = json.loads(m["schedule"])["windows"]
    sent = {"id": m["id"], "name": m["name"], "categories": m["categories"], "items": m["items"],
            "groups": m["groups"], "extra": m["extra"], "mute": m["mute"], "pomodoro": m["pomodoro"],
            "windows": windows}
    sent.update(changes)
    return sent


WEEKDAYS = [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}]
POMO = {"work": 25, "break": 5, "rounds": 4, "long": 15}


def set_pomodoro(ctx, mode_id, pomodoro):
    ms = modes.load(ctx.db)
    for m in ms:
        if m["id"] == mode_id:
            m["pomodoro"] = pomodoro
    modes.save(ctx.db, ms)


def schedule_mode(ctx, mode_id="study", windows=WEEKDAYS, pomodoro=None):
    """Give a built-in mode a schedule (and maybe a Pomodoro), straight into the database."""
    ms = modes.load(ctx.db)
    for m in ms:
        if m["id"] == mode_id:
            m["schedule"] = make_schedule("block", [(w["days"], w["start"], w["end"]) for w in windows])
            m["pomodoro"] = pomodoro
    modes.save(ctx.db, ms)


# ---------------------------------------------------------------- classification and the page

def test_methods_are_classified():
    kinds = dict(webapi.api_methods())
    assert kinds["modes_get"] == "read" and kinds["mode_extra_check"] == "read"
    for name in ("mode_start", "mode_stop", "mode_save", "mode_delete"):
        assert kinds[name] == "loosen", name
    assert kinds["modes_brief"] == "read"          # R26's; this module does not register it again


def test_the_page_lists_the_built_in_modes_and_nothing_is_on(api):
    d = data(api.modes_get())
    assert [m["id"] for m in d["modes"]] == ["work", "study", "focus", "dnd", "relax"]
    assert d["active"] is None
    by = {m["id"]: m for m in d["modes"]}
    assert by["work"]["describe"] == "Blocks: Distracting" and by["relax"]["describe"] == "Blocks: nothing extra"
    assert by["dnd"]["describe"].endswith(" · mutes notifications")
    assert by["focus"]["pomodoro_text"] == "Pomodoro 25 / 5 min × 4, then 15 min" and by["focus"]["run_min"] == 130
    assert all(m["builtin"] for m in d["modes"]) and [c["key"] for c in d["categories"]][:2] == ["productive", "neutral"]


def test_the_page_shows_a_running_mode_in_the_status_words(ctx, api):
    start_locked(ctx)
    a = data(api.modes_get())["active"]
    assert (a["id"], a["locked"], a["by_schedule"], a["phase"]) == ("work", True, False, None)
    assert a["ends_at"] == "2026-10-11T16:42:17" and a["started"] == "2026-10-11T15:42:17"
    modes.start(ctx.db, "focus", NOW, None)
    ph = data(api.modes_get())["active"]["phase"]
    assert (ph["name"], ph["round"], ph["rounds"], ph["ends_at"]) == ("focus", 1, 4, "2026-10-11T16:07:17")


def test_a_scheduled_mode_shows_its_lines(ctx, api):
    schedule_mode(ctx, "study")
    study = {m["id"]: m for m in data(api.modes_get())["modes"]}["study"]
    assert study["auto_text"] == "Automatically: Monday–Friday 09:00-17:00"
    assert study["windows"] == WEEKDAYS


# ---------------------------------------------------------------- start and stop

def test_start_an_hour_unlocked_is_free_and_pokes_the_status_worker(ctx, api):
    out = data(api.mode_start("work", 60, False))
    assert out["message"] == "Work mode on until 16:42"
    st = active(ctx)
    assert (st["mode"]["id"], st["until"], st["locked"]) == ("work", NOW + timedelta(hours=1), False)
    assert ctx.hub.status_worker.pokes == 1 and out["active"]["id"] == "work"


def test_start_locked_says_so_and_stores_the_lock(ctx, api):
    out = data(api.mode_start("study", 30, True))
    assert out["message"] == "Study mode on until 16:12 (locked)"
    assert active(ctx)["locked"] is True


def test_until_i_stop_cannot_be_locked(ctx, api):
    out = data(api.mode_start("work", None, True))
    assert out["message"] == "Work mode on"
    st = active(ctx)
    assert st["until"] is None and st["locked"] is False


def test_a_pomodoro_runs_all_its_rounds_whatever_was_asked(ctx, api):
    out = data(api.mode_start("focus", 30, True))
    assert active(ctx)["until"] == NOW + timedelta(minutes=130)
    assert out["message"] == "Focus mode on until 17:52 (locked)"


@pytest.mark.parametrize("typed,expected", [
    ("17:00", datetime(2026, 10, 11, 17, 0)),         # later today
    ("9:30", datetime(2026, 10, 12, 9, 30)),          # already gone: tomorrow
    ("15:42", datetime(2026, 10, 12, 15, 42)),        # this very minute is gone too
])
def test_until_a_time_rolls_to_tomorrow_on_the_trusted_clock(ctx, api, typed, expected):
    data(api.mode_start("work", typed, False))
    assert active(ctx)["until"] == expected


@pytest.mark.parametrize("bad", ["soon", "25:00:00:1", "", "17", 0, -5, 2000, 1.5, True, [30]])
def test_a_bad_until_is_refused_and_nothing_starts(ctx, api, bad):
    assert refused(api.mode_start("work", bad, False))
    assert active(ctx) is None


def test_start_checks_the_mode_and_the_lock_flag(ctx, api):
    assert "doesn't exist" in refused(api.mode_start("nope", 30, False))
    assert refused(api.mode_start("work", 30, "yes"))
    assert refused(api.mode_start(5, 30, False))


def test_stop_unlocked_is_free(ctx, api):
    data(api.mode_start("work", 60, False))
    out = data(api.mode_stop())
    assert out["message"] == "Work mode off" and out["active"] is None and active(ctx) is None
    assert ctx.hub.status_worker.pokes == 2


def test_stop_with_nothing_on_is_quiet(ctx, api):
    out = data(api.mode_stop())
    assert out["active"] is None and "message" not in out


def test_stop_locked_needs_the_challenge_even_with_none_turned_on(ctx, api):
    start_locked(ctx)
    reply = data(api.mode_stop())
    assert reply["needs_challenge"] and "Stop the locked Work mode (locked until 16:42)" in reply["changes"][0]
    assert active(ctx)["mode"]["id"] == "work"           # nothing changed yet
    out = pass_challenge(ctx, reply["ticket"], api)
    assert out["result"]["message"] == "Work mode off" and active(ctx) is None


def test_stop_locked_cancelled_changes_nothing(ctx, api):
    start_locked(ctx)
    ticket = data(api.mode_stop())["ticket"]
    api.challenge_cancel(ticket)
    assert active(ctx)["locked"] is True
    assert "expired" in refused(api.challenge_submit(ticket, "x"))


def test_stop_cannot_end_a_scheduled_mode(ctx, api):
    schedule_mode(ctx, "study", [{"days": list(range(7)), "start": "00:00", "end": "23:59"}])
    assert "by its schedule" in refused(api.mode_stop())


def test_start_over_a_locked_mode_needs_the_challenge(ctx, api):
    start_locked(ctx, "work")
    reply = data(api.mode_start("study", 30, False))
    assert reply["needs_challenge"] and "End the locked Work mode (locked until 16:42) and start Study" in reply["changes"][0]
    assert active(ctx)["mode"]["id"] == "work"
    out = pass_challenge(ctx, reply["ticket"], api)
    assert out["result"]["message"] == "Study mode on until 16:12" and active(ctx)["mode"]["id"] == "study"


def test_start_over_the_same_locked_mode_asks_too(ctx, api):
    start_locked(ctx, "work")
    assert data(api.mode_start("work", 5, True))["needs_challenge"]


def test_start_over_an_unlocked_mode_is_free(ctx, api):
    data(api.mode_start("work", 60, False))
    assert data(api.mode_start("study", 30, False))["message"].startswith("Study mode on")


def test_starting_another_mode_while_a_schedule_runs_asks_because_by_hand_wins_over_a_schedule(locked, api):
    schedule_mode(locked, "study", [{"days": list(range(7)), "start": "00:00", "end": "23:59"}])     # on now
    reply = data(api.mode_start("relax", 30, False))                       # (Relax blocks nothing: it would end Study's blocks)
    assert reply["needs_challenge"] and "while Study is on by its schedule" in reply["changes"][0]
    assert active(locked)["mode"]["id"] == "study" and active(locked)["scheduled"]
    out = pass_challenge(locked, reply["ticket"], api)
    assert out["result"]["message"].startswith("Relax mode on") and active(locked)["mode"]["id"] == "relax"
    # the scheduled mode itself, by hand, hides nothing: free
    assert "message" in data(api.mode_start("study", 30, False))


def test_starting_over_a_schedule_is_free_when_no_challenge_is_turned_on(ctx, api):
    schedule_mode(ctx, "study", [{"days": list(range(7)), "start": "00:00", "end": "23:59"}])
    assert "message" in data(api.mode_start("relax", 30, False))             # (only a locked mode asks with none set)


def test_starting_a_mode_when_the_schedule_is_not_running_is_free(locked, api):
    schedule_mode(locked, "study")                                           # Monday-Friday: now is Sunday
    assert "message" in data(api.mode_start("relax", 30, False))


# ---------------------------------------------------------------- delete

def new_mode(api, **kw):
    sent = {"id": "", "name": "Evening", "categories": ["distracting"], "items": [], "groups": [], "extra": [],
            "mute": False, "pomodoro": None, "windows": None}
    sent.update(kw)
    return data(api.mode_save(sent))["saved_id"]


def test_delete_an_inactive_mode_is_free(ctx, api):
    mid = new_mode(api)
    out = data(api.mode_delete(mid))
    assert mid not in [m["id"] for m in out["modes"]] and mid not in [m["id"] for m in modes.load(ctx.db)]


def test_delete_the_active_locked_mode_needs_the_challenge_even_with_none_turned_on(ctx, api):
    mid = new_mode(api)
    start_locked(ctx, mid)
    reply = data(api.mode_delete(mid))
    assert reply["needs_challenge"] and "Delete the locked Evening mode" in reply["changes"][0]
    assert mid in [m["id"] for m in modes.load(ctx.db)] and active(ctx)["mode"]["id"] == mid
    out = pass_challenge(ctx, reply["ticket"], api)
    assert mid not in [m["id"] for m in out["result"]["modes"]]
    assert active(ctx) is None and ctx.db.get_setting(modes.ACTIVE_KEY, "x") == ""


def test_delete_an_active_but_unlocked_mode_is_free_and_clears_it(ctx, api):
    mid = new_mode(api)
    data(api.mode_start(mid, 60, False))
    data(api.mode_delete(mid))
    assert active(ctx) is None
    again = new_mode(api)                                  # the id is used again: it must not start on its own
    assert again == mid and active(ctx) is None


def test_delete_a_mode_with_a_schedule_needs_the_challenge(locked, api):
    mid = new_mode(api, windows=WEEKDAYS)
    reply = data(api.mode_delete(mid))
    assert reply["needs_challenge"] and "schedule" in reply["changes"][0]
    assert mid in [m["id"] for m in modes.load(locked.db)]


def test_delete_a_built_in_mode_is_refused(api):
    assert "built-in" in refused(api.mode_delete("work"))


def test_delete_a_mode_that_is_gone_is_fine(api):
    assert data(api.mode_delete("custom9"))["modes"]


# ---------------------------------------------------------------- save

def test_a_new_mode_gets_an_id_and_is_free(ctx, api):
    mid = new_mode(api, name="  Gaming   free ", mute=True)
    m = stored(ctx, mid)
    assert mid == "custom1" and m["name"] == "Gaming free" and m["mute"] is True and m["builtin"] is False
    assert ctx.hub.status_worker.pokes == 1
    assert new_mode(api, name="Second") == "custom2"


def test_a_built_in_mode_cannot_be_renamed(ctx, api):
    assert "can't be changed" in refused(api.mode_save(edit(ctx, "work", name="Job")))
    assert stored(ctx, "work")["name"] == "Work"


def test_an_empty_name_keeps_the_old_one_for_an_existing_mode_but_not_a_new_one(ctx, api):
    mid = new_mode(api, name="Quiet")
    data(api.mode_save(edit(ctx, mid, name="")))
    assert stored(ctx, mid)["name"] == "Quiet"
    assert refused(api.mode_save({"id": "", "name": "  "})) == "Give the mode a name."
    data(api.mode_save(edit(ctx, "work", name="")))              # a built-in keeps its name too


def test_save_checks_what_the_boxes_say(ctx, api):
    assert refused(api.mode_save(edit(ctx, "work", pomodoro={"work": 25, "break": 5, "rounds": 4, "long": "x"}))) \
        == "Pomodoro numbers must be whole numbers."
    assert refused(api.mode_save(edit(ctx, "work", pomodoro={"work": 25, "break": 5, "rounds": 4, "long": 2.5}))) \
        == "Pomodoro numbers must be whole numbers."
    assert refused(api.mode_save(edit(ctx, "work", pomodoro={"work": 0, "break": 5, "rounds": 4, "long": 15}))) \
        == "Pomodoro numbers must be between 1 and 240."
    assert refused(api.mode_save(edit(ctx, "work", pomodoro={"work": 25, "break": 5, "rounds": 241, "long": 15}))) \
        == "Pomodoro numbers must be between 1 and 240."
    assert refused(api.mode_save(edit(ctx, "work", windows=[{"days": [], "start": "09:00", "end": "17:00"}]))) \
        == "Turn on at least one day for the automatic times."
    assert refused(api.mode_save(edit(ctx, "work", windows=[{"days": [0], "start": "9am", "end": "17:00"}]))) \
        == "Hours must look like 09:00 or 21:30."
    assert refused(api.mode_save(edit(ctx, "work", windows=[{"days": [9], "start": "09:00", "end": "17:00"}])))
    assert refused(api.mode_save(edit(ctx, "work", mute="yes")))
    assert refused(api.mode_save("work")) and refused(api.mode_save(edit(ctx, "work", categories="distracting")))
    assert "was deleted" in refused(api.mode_save({"id": "custom7", "name": "x"}))
    assert stored(ctx, "work")["pomodoro"] is None and stored(ctx, "work")["schedule"] is None


def test_pomodoro_numbers_may_be_typed_as_text(ctx, api):
    mid = new_mode(api, pomodoro={"work": "30", "break": " 5 ", "rounds": "2", "long": "10"})
    assert stored(ctx, mid)["pomodoro"] == {"work": 30, "break": 5, "rounds": 2, "long": 10}


def test_the_automatic_times_are_saved_as_the_old_window_stored_them(ctx, api):
    mid = new_mode(api, windows=[{"days": [4, 0, 0], "start": "9:00", "end": "17:30"},
                                 {"days": [], "start": "01:00", "end": "02:00"}])
    assert json.loads(stored(ctx, mid)["schedule"]) == {"mode": "block",
                                                         "windows": [{"days": [0, 4], "start": "09:00", "end": "17:30"}]}


def test_things_that_do_not_exist_drop_out_of_a_mode(ctx, api):
    item = ctx.db.add_site("YouTube", ["youtube.com"], rules=[{"rule_type": "permanent"}])
    mid = new_mode(api, categories=["distracting", "nope", "distracting"], items=[item, 9999, item], groups=[77])
    m = stored(ctx, mid)
    assert m["categories"] == ["distracting"] and m["items"] == [item] and m["groups"] == []
    assert refused(api.mode_save(edit(ctx, mid, items=["1"])))


def test_save_looser_the_active_locked_mode_needs_the_challenge_even_with_none_turned_on(ctx, api):
    start_locked(ctx, "work")
    reply = data(api.mode_save(edit(ctx, "work", categories=[])))
    assert reply["needs_challenge"] and reply["changes"][0].startswith("Work mode: make it block less")
    assert stored(ctx, "work")["categories"] == ["distracting"]          # not saved yet
    out = pass_challenge(ctx, reply["ticket"], api)
    assert out["result"]["saved_id"] == "work" and stored(ctx, "work")["categories"] == []


def test_save_looser_cancelled_stays_as_it_was(ctx, api):
    start_locked(ctx, "work")
    ticket = data(api.mode_save(edit(ctx, "work", categories=[])))["ticket"]
    api.challenge_cancel(ticket)
    assert stored(ctx, "work")["categories"] == ["distracting"]


def test_save_tighter_the_active_locked_mode_is_free(ctx, api):
    start_locked(ctx, "work")
    out = data(api.mode_save(edit(ctx, "work", categories=["distracting", "neutral"], mute=True,
                                  extra=[{"kind": "site", "name": "Reddit", "targets": ["reddit.com"],
                                          "block_type": "dns", "app_path": None}])))
    assert out["saved_id"] == "work"
    assert stored(ctx, "work")["categories"] == ["distracting", "neutral"]


def test_save_looser_a_mode_that_is_not_on_is_free(ctx, api):
    assert data(api.mode_save(edit(ctx, "work", categories=[])))["saved_id"] == "work"


def test_save_looser_a_mode_that_is_on_but_not_locked_is_free(ctx, api):
    data(api.mode_start("work", 60, False))
    assert data(api.mode_save(edit(ctx, "work", categories=[])))["saved_id"] == "work"


def test_taking_an_item_a_group_or_an_extra_out_of_the_locked_mode_asks(ctx, api):
    item = ctx.db.add_site("YouTube", ["youtube.com"], rules=[{"rule_type": "permanent"}])
    extra = {"kind": "site", "name": "Reddit", "targets": ["reddit.com"], "block_type": "dns", "app_path": None}
    mid = new_mode(api, items=[item], extra=[extra])
    start_locked(ctx, mid)
    assert data(api.mode_save(edit(ctx, mid, items=[])))["needs_challenge"]
    assert data(api.mode_save(edit(ctx, mid, extra=[])))["needs_challenge"]
    assert data(api.mode_save(edit(ctx, mid, name="Renamed", mute=True)))["saved_id"] == mid     # nothing taken out


def test_a_category_that_is_gone_is_not_counted_as_taken_away(ctx, api):
    start_locked(ctx, "work")
    ms = modes.load(ctx.db)
    ms[0]["categories"] = ["distracting", "deleted-cat"]
    modes.save(ctx.db, ms)
    assert data(api.mode_save(edit(ctx, "work", categories=["distracting"])))["saved_id"] == "work"


def test_a_lighter_pomodoro_on_the_locked_mode_asks_and_a_heavier_one_is_free(ctx, api):
    modes.start(ctx.db, "focus", NOW, NOW + timedelta(hours=1), locked=True)
    for lighter in ({**POMO, "work": 5}, {**POMO, "rounds": 2}, {**POMO, "break": 30}, {**POMO, "long": 60}, None):
        assert data(api.mode_save(edit(ctx, "focus", pomodoro=lighter)))["needs_challenge"], lighter
    for heavier in ({**POMO, "work": 50}, {**POMO, "rounds": 6}, {**POMO, "break": 1}, {**POMO, "long": 5}):
        set_pomodoro(ctx, "focus", POMO)                     # (each one is compared with the standard Pomodoro)
        assert data(api.mode_save(edit(ctx, "focus", pomodoro=heavier)))["saved_id"] == "focus", heavier


def test_adding_breaks_to_the_locked_mode_asks(ctx, api):
    start_locked(ctx, "work")                                    # no Pomodoro: it blocks the whole time
    assert data(api.mode_save(edit(ctx, "work", pomodoro={**POMO, "work": 1, "break": 240})))["needs_challenge"]


# ---------------------------------------------------------------- a scheduled mode is a standing commitment

def test_a_scheduled_pomodoro_looser_needs_the_challenge(locked, api):
    schedule_mode(locked, "study", pomodoro=POMO)               # (Monday-Friday 9-17; now is Sunday: not running)
    for lighter in ({**POMO, "work": 5}, {**POMO, "rounds": 2}, {**POMO, "break": 60}, {**POMO, "long": 100}, None):
        assert data(api.mode_save(edit(locked, "study", pomodoro=lighter)))["needs_challenge"], lighter
    for heavier in ({**POMO, "work": 30}, {"work": 30, "break": 3, "rounds": 5, "long": 10}):
        set_pomodoro(locked, "study", POMO)
        assert data(api.mode_save(edit(locked, "study", pomodoro=heavier)))["saved_id"] == "study", heavier


def test_a_plain_pomodoro_mode_that_is_not_held_can_be_made_lighter_freely(locked, api):
    assert data(api.mode_save(edit(locked, "focus", pomodoro={**POMO, "work": 1, "break": 200})))["saved_id"] == "focus"


def test_adding_a_pomodoro_to_a_scheduled_mode_asks(locked, api):
    schedule_mode(locked, "study")
    assert data(api.mode_save(edit(locked, "study", pomodoro={**POMO, "work": 1, "break": 240})))["needs_challenge"]


def test_a_schedule_that_runs_less_needs_the_challenge_and_one_that_runs_more_is_free(locked, api):
    schedule_mode(locked, "study")
    for less in ([{"days": [0, 1, 2, 3], "start": "09:00", "end": "17:00"}],
                 [{"days": [0, 1, 2, 3, 4], "start": "10:00", "end": "17:00"}],
                 [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "16:00"}], None):
        assert data(api.mode_save(edit(locked, "study", windows=less)))["needs_challenge"], less
    for more in ([{"days": [0, 1, 2, 3, 4, 5], "start": "09:00", "end": "17:00"}],
                 [{"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00"}],
                 WEEKDAYS + [{"days": [5], "start": "10:00", "end": "12:00"}]):
        schedule_mode(locked, "study")                            # (each one is compared with Monday-Friday 9-17)
        assert data(api.mode_save(edit(locked, "study", windows=more)))["saved_id"] == "study", more


def test_a_schedule_is_free_to_add_to_a_mode_that_had_none(locked, api):
    assert data(api.mode_save(edit(locked, "work", windows=WEEKDAYS)))["saved_id"] == "work"


def test_while_the_window_runs_blocking_less_asks_but_outside_it_does_not(locked, ctx, api):
    schedule_mode(locked, "study")
    assert data(api.mode_save(edit(locked, "study", categories=[])))["saved_id"] == "study"       # Sunday: not running
    schedule_mode(locked, "study", [{"days": list(range(7)), "start": "00:00", "end": "23:59"}])   # running now
    ms = modes.load(locked.db)
    ms[1]["categories"] = ["distracting"]
    modes.save(locked.db, ms)
    assert data(api.mode_save(edit(locked, "study", categories=[]))).get("needs_challenge")
    assert data(api.mode_save(edit(locked, "study", categories=["distracting", "neutral"])))["saved_id"] == "study"


def test_a_scheduled_mode_without_a_challenge_turned_on_is_not_forced(ctx, api):
    schedule_mode(ctx, "study")
    out = data(api.mode_save(edit(ctx, "study", windows=[{"days": [0], "start": "09:00", "end": "10:00"}])))
    assert out["saved_id"] == "study"                  # (only a mode locked by hand asks with no challenge set)


# ---------------------------------------------------------------- the pure rule

def mode(**kw):
    return {"id": "m", "name": "M", "categories": ["distracting"], "items": [1], "groups": [], "extra": [], "mute": False,
            "pomodoro": None, "schedule": None, **kw}


def test_the_rule_in_one_place():
    old = mode()
    assert page.loosenings(old, mode(), True) == []
    assert page.loosenings(old, mode(categories=[]), False) == []              # not held, no schedule: free
    assert page.loosenings(old, mode(categories=[]), True)
    assert page.loosenings(old, mode(categories=["distracting", "games"], items=[1, 2]), True) == []
    sched = make_schedule("block", [([0, 1], "09:00", "17:00")])
    assert page.loosenings(mode(schedule=sched), mode(schedule=None), False)
    assert page.loosenings(mode(schedule=sched), mode(schedule=sched, categories=[]), False) == []
    assert page.delete_loosens(old, False) is False and page.delete_loosens(old, True)
    assert page.delete_loosens(mode(schedule=sched), False)


# ---------------------------------------------------------------- "+ Other site or app"

def check(api, **spec):
    return api.mode_extra_check({"kind": "site", "text": "", "name": "", **spec})


def test_a_site_is_tidied_to_its_address(api):
    out = data(check(api, text=" https://www.Example.org/r/all ", ways=["dns"]))
    assert out == {"kind": "site", "name": "Example", "targets": ["example.org"], "block_type": "dns", "app_path": None}
    assert data(check(api, text="example.org", name="Time sink", ways=["dns", "close"]))["block_type"] == "dns,close"
    assert data(check(api, text="example.org"))["block_type"] == "dns"          # nothing said: Can't load it


def test_a_known_site_brings_its_other_hostnames(api):
    assert data(check(api, text="reddit.com"))["targets"][0] == "reddit.com"
    out = data(check(api, text="youtube.com"))
    assert out["name"] == "YouTube" and "youtube.com" in out["targets"] and len(out["targets"]) > 1


@pytest.mark.parametrize("spec,words", [
    ({"text": "not a site"}, "Write a site like reddit.com"),
    ({"text": ""}, "Write a site"),
    ({"text": "reddit.com", "ways": []}, "Tick at least one of Can't load it"),
    ({"text": "reddit.com", "ways": ["close", "back"]}, "pick one"),
    ({"text": "reddit.com", "ways": ["bogus"]}, "Bad request"),
    ({"kind": "category", "text": "x"}, "site or an app"),
])
def test_a_bad_site_is_refused_with_words(api, spec, words):
    assert words in refused(check(api, **spec))


def test_an_app_is_its_exe(api):
    out = data(check(api, kind="app", text="C:\\Games\\Foo\\Foo.exe", ways=["close", "background"]))
    assert out == {"kind": "app", "name": "Foo", "targets": ["foo.exe"], "block_type": "close,background",
                   "app_path": "C:\\Games\\Foo\\Foo.exe"}
    picked = data(check(api, kind="app", text="discord.exe", name="Discord", app_path="C:\\D\\discord.exe"))
    assert picked["block_type"] == "close" and picked["app_path"] == "C:\\D\\discord.exe"
    assert data(check(api, kind="app", text="chrome.exe", ways=["minimize", "internet"]))["block_type"] == "minimize,internet"


@pytest.mark.parametrize("spec,words", [
    ({"text": "explorer.exe"}, "can't be blocked"),
    ({"text": "LockdownService.exe"}, "can't be blocked"),
    ({"text": "discord"}, "file name"),
    ({"text": "discord.exe", "ways": []}, "Tick at least one"),
    ({"text": "discord.exe", "ways": ["close", "minimize"]}, "pick one"),
    ({"text": "game.jar", "ways": ["internet"]}, "Tick Close app or Minimize"),
])
def test_a_bad_app_is_refused_with_words(api, spec, words):
    assert words in refused(check(api, kind="app", **spec))


def test_a_program_file_can_only_be_closed_or_minimised(api):
    out = data(check(api, kind="app", text="game.jar", ways=["close", "background", "internet"]))
    assert out["targets"] == ["game.jar"] and out["block_type"] == "close"


def test_save_checks_every_new_extra_again_but_keeps_stored_ones_as_they_are(ctx, api):
    good = data(check(api, text="example.org"))
    mid = new_mode(api, extra=[good])
    assert stored(ctx, mid)["extra"] == [good]
    for bad in ({"kind": "app", "name": "x", "targets": ["explorer.exe"], "block_type": "close", "app_path": None},
                {"kind": "site", "name": "x", "targets": ["not a site"], "block_type": "dns", "app_path": None},
                {"kind": "site", "name": "x", "targets": [], "block_type": "dns", "app_path": None},
                {"kind": "bogus", "name": "x", "targets": ["a.com"]}, "reddit.com"):
        assert refused(api.mode_save(edit(ctx, mid, extra=[good, bad])))
    # an entry the old window stored (a category, an odd block type) goes back untouched when it is unchanged
    legacy = {"kind": "category", "name": "Games", "targets": ["games"], "block_type": "kill", "app_path": None}
    ms = modes.load(ctx.db)
    ms[-1]["extra"] = [good, legacy]
    modes.save(ctx.db, ms)
    data(api.mode_save(edit(ctx, mid, extra=[good, legacy], mute=True)))
    assert stored(ctx, mid)["extra"] == [good, legacy] and stored(ctx, mid)["mute"] is True


def test_a_changed_extra_is_checked_like_a_new_one(ctx, api):
    good = data(check(api, text="example.org"))
    mid = new_mode(api, extra=[good])
    out = data(api.mode_save(edit(ctx, mid, extra=[{**good, "block_type": "dns,close"}])))
    assert out["saved_id"] == mid and stored(ctx, mid)["extra"][0]["block_type"] == "dns,close"
    assert refused(api.mode_save(edit(ctx, mid, extra=[{**good, "block_type": "dns,close,back"}])))
