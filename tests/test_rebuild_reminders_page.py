"""R26: the Reminders page's Api (webapi/reminders.py) - parsing like the old window, the Important rule decided in
Python (turn off / un-flag / delete / weaken -> challenge ticket, everything else free), one counts query."""
import random
import time
from datetime import datetime, timedelta

import pytest

import antibypass as ab
import reminders
import webapi
from webapi import core
from webapi import reminders as page
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


@pytest.fixture
def ctx(tmp_path):
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    c.guard = GuardService(lambda: c.db, lambda db: NOW, rng=random.Random(3))
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


SLEEP = {"on": True, "guarded": False, "bedtime": "23:00", "wake": "07:00", "before": "30", "mode": "",
         "tiers": [{"every": "15 min", "after": "21:00"}, {"every": "30s", "after": "00:00"}], "dismiss_wait": 0,
         "warn_text": "", "text": ""}
BREAK = {"on": True, "guarded": False, "every": "45 min", "snooze": "5 min", "length": 5, "strict": False,
         "max_snooze": 2, "twenty": False, "text": "", "twenty_text": ""}
CUSTOM = {"id": "", "text": "Drink water", "kind": "interval", "every": "1h", "times": ["12:00"], "days": list(range(7)),
          "window": ["10:00", "18:00"], "hours": False, "snooze": 5, "max_snooze": 3, "check": 0, "per_day": 0,
          "packs": [], "quotes": "", "guarded": False}


def add(api, **kw):
    out = data(api.reminder_save_custom({**CUSTOM, **kw}))
    assert out.get("saved"), out
    return out["id"]


def stored(ctx):
    return {r["id"]: r for r in reminders.custom_list(ctx.db)}


def pass_challenge(ctx, ticket, api):
    """Do what the page does after the Python challenge: begin, type it, submit (a person's typing time)."""
    begin = data(api.challenge_begin(ticket))
    mono = ctx.guard._mono
    ctx.guard._mono = lambda: mono() + 60            # (a person took a minute to type it)
    return data(api.challenge_submit(ticket, begin["phrase"]))


# ---------------------------------------------------------------- classification

def test_methods_are_classified():
    kinds = dict(webapi.api_methods())
    assert kinds["reminders_get"] == "read" and kinds["modes_brief"] == "read"
    for name in ("reminder_save_sleep", "reminder_save_break", "reminder_save_custom", "reminder_set_on",
                 "reminder_delete"):
        assert kinds[name] == "loosen", name


# ---------------------------------------------------------------- parsing like the old window

@pytest.mark.parametrize("typed,seconds", [("30s", 30), ("5 min", 300), ("5", 300), ("1h30", 5400), ("45 s", 45)])
def test_the_break_boxes_parse_like_the_old_window(api, ctx, typed, seconds):
    assert reminders.parse_interval(typed) == seconds
    data(api.reminder_save_break({**BREAK, "every": typed, "snooze": typed}))
    b = reminders.load(ctx.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)
    assert b["every_sec"] == seconds and b["snooze_sec"] == seconds
    assert data(api.reminders_get())["break"]["every"] == reminders.seconds_text(seconds)


@pytest.mark.parametrize("typed", ["4s", "0", "off", "soon", "25h", ""])
def test_less_than_five_seconds_and_nonsense_are_refused(api, ctx, typed):
    for call in (lambda: api.reminder_save_break({**BREAK, "every": typed}),
                 lambda: api.reminder_save_break({**BREAK, "snooze": typed}),
                 lambda: api.reminder_save_custom({**CUSTOM, "every": typed}),
                 lambda: api.reminder_save_sleep({**SLEEP, "tiers": [{"every": typed, "after": "21:00"}]})):
        r = call()
        assert not r["ok"] and r["error"]
    assert reminders.load(ctx.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK) == reminders.DEFAULT_BREAK
    assert stored(ctx) == {}


def test_sleep_boxes_round_trip(api, ctx):
    data(api.reminder_save_sleep({**SLEEP, "before": "45s", "dismiss_wait": "90", "warn_text": "Soon {bedtime}",
                                  "text": "Bed!"}))
    s = reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    assert s["before"] == 0.75 and s["dismiss_wait"] == 90 and s["warn_text"] == "Soon {bedtime}"
    assert [(t["from"], t["every_sec"]) for t in s["tiers"]] == [("00:00", 30), ("21:00", 900)]
    got = data(api.reminders_get())["sleep"]
    assert got["before"] == "45s" and got["dismiss_wait"] == 90
    assert got["tiers"] == [{"every": "30s", "after": "00:00"}, {"every": "15 min", "after": "21:00"}]


def test_heads_up_goes_up_to_12_hours_and_dismiss_wait_to_600(api):
    assert data(api.reminder_save_sleep({**SLEEP, "before": "12h", "dismiss_wait": 600}))["saved"]
    assert not api.reminder_save_sleep({**SLEEP, "before": "13h"})["ok"]
    r = api.reminder_save_sleep({**SLEEP, "dismiss_wait": 601})
    assert not r["ok"] and "600" in r["error"]
    assert not api.reminder_save_sleep({**SLEEP, "dismiss_wait": "soon"})["ok"]


def test_bad_times_and_modes_and_types_are_refused(api):
    assert "23:00" in api.reminder_save_sleep({**SLEEP, "bedtime": "late"})["error"]
    assert not api.reminder_save_sleep({**SLEEP, "mode": "no-such-mode"})["ok"]
    assert not api.reminder_save_sleep({**SLEEP, "on": "yes"})["ok"]
    assert not api.reminder_save_sleep("x")["ok"]
    assert not api.reminder_save_break({**BREAK, "length": 7})["ok"]
    assert not api.reminder_save_break({**BREAK, "max_snooze": 4})["ok"]
    assert not api.reminder_save_custom({**CUSTOM, "text": "  "})["ok"]
    assert not api.reminder_save_custom({**CUSTOM, "days": []})["ok"]
    assert not api.reminder_save_custom({**CUSTOM, "kind": "times", "times": []})["ok"]
    assert not api.reminder_save_custom({**CUSTOM, "kind": "random", "window": ["18:00", "10:00"]})["ok"]
    assert not api.reminder_save_custom({**CUSTOM, "snooze": 7})["ok"]
    assert not api.reminder_save_custom({**CUSTOM, "max_snooze": 6})["ok"]
    assert not api.reminder_save_custom({**CUSTOM, "per_day": 13})["ok"]
    assert not api.reminder_save_custom({**CUSTOM, "packs": ["Nope"]})["ok"]
    assert not api.reminder_save_custom({**CUSTOM, "text": "x" * 500})["ok"]


def test_a_new_reminder_gets_a_server_id_and_editing_keeps_on(api, ctx):
    a = add(api)
    assert len(a) == 8 and stored(ctx)[a]["on"] is True
    data(api.reminder_set_on(a, False))
    data(api.reminder_save_custom({**CUSTOM, "id": a, "text": "Stretch", "kind": "times", "times": ["09:00", "13:00"]}))
    r = stored(ctx)[a]
    assert r["text"] == "Stretch" and r["on"] is False and r["times"] == ["09:00", "13:00"]
    assert not api.reminder_save_custom({**CUSTOM, "id": "nope"})["ok"]


def test_the_stored_reminder_is_what_the_engine_reads(api, ctx):
    add(api, text="Water", every="30s", hours=True, window=["08:00", "20:00"], per_day=4, packs=["Stoic"], quotes="a\nb")
    r = next(iter(stored(ctx).values()))
    assert r["every_sec"] == 30 and r["hours"] is True and r["per_day"] == 4 and r["packs"] == ["Stoic"]
    assert reminders.schedule_text({**reminders.DEFAULT_CUSTOM, **r}).startswith("every 30s of use, 08:00-20:00")


# ---------------------------------------------------------------- the Important rule (Python decides)

def test_free_while_anti_bypass_is_not_locked(api, ctx):
    a = add(api, guarded=True)
    out = data(api.reminder_set_on(a, False))                 # status "free": the guard runs it at once
    assert out == {"saved": True} and stored(ctx)[a]["on"] is False
    b = add(api, text="Casual")
    assert data(api.reminder_delete(b)) == {"saved": True} and b not in stored(ctx)


def test_turning_an_important_one_off_needs_the_challenge(api, ctx, locked):
    a = add(api, guarded=True)
    out = data(api.reminder_set_on(a, False))
    assert out["needs_challenge"] and out["ticket"]
    assert stored(ctx)[a]["on"] is True                       # nothing changed yet
    assert data(api.challenge_cancel(out["ticket"])) is None
    assert stored(ctx)[a]["on"] is True
    out = data(api.reminder_set_on(a, False))
    assert pass_challenge(locked, out["ticket"], api)["passed"]
    assert stored(ctx)[a]["on"] is False


def test_removing_important_and_deleting_it_need_the_challenge(api, ctx, locked):
    a = add(api, guarded=True)
    out = data(api.reminder_save_custom({**CUSTOM, "id": a, "guarded": False}))
    assert out["needs_challenge"] and stored(ctx)[a]["guarded"] is True
    out = data(api.reminder_delete(a))
    assert out["needs_challenge"] and a in stored(ctx)
    pass_challenge(locked, out["ticket"], api)
    assert stored(ctx) == {}


@pytest.mark.parametrize("change", [
    {"every": "2h"}, {"days": [0, 1, 2, 3, 4]}, {"per_day": 3}, {"max_snooze": 5}, {"kind": "times"},
    {"hours": True}])
def test_a_longer_interval_or_other_weakened_schedule_of_an_important_one_asks(api, ctx, locked, change):
    a = add(api, guarded=True)
    out = data(api.reminder_save_custom({**CUSTOM, "id": a, "guarded": True, **change}))
    assert out.get("needs_challenge"), change
    assert stored(ctx)[a]["every_sec"] == 3600 and stored(ctx)[a]["kind"] == "interval"


def test_a_shorter_interval_text_and_new_important_ones_are_free(api, ctx, locked):
    a = add(api, guarded=True)                                # a NEW important reminder only adds protection
    assert data(api.reminder_save_custom({**CUSTOM, "id": a, "guarded": True, "every": "30 min"}))["saved"]
    assert data(api.reminder_save_custom({**CUSTOM, "id": a, "guarded": True, "every": "30 min", "text": "New",
                                          "days": list(range(7)), "per_day": 0, "snooze": 1}))["saved"]
    assert stored(ctx)[a]["every_sec"] == 1800 and stored(ctx)[a]["text"] == "New"
    assert data(api.reminder_set_on(a, True))["saved"]        # turning ON is free


def test_non_important_changes_are_free_even_when_locked(api, ctx, locked):
    a = add(api)
    assert data(api.reminder_save_custom({**CUSTOM, "id": a, "every": "3h", "days": [1]}))["saved"]
    assert data(api.reminder_set_on(a, False))["saved"]
    assert data(api.reminder_save_custom({**CUSTOM, "id": a, "guarded": True}))["saved"]    # flag ADDED: free
    assert data(api.reminder_save_break(BREAK))["saved"]
    assert data(api.reminder_save_sleep({**SLEEP, "on": False}))["saved"]
    b = add(api, text="Casual")
    assert data(api.reminder_delete(b)) == {"saved": True} and b not in stored(ctx)


def test_sleep_and_break_important_rules(api, ctx, locked):
    data(api.reminder_save_sleep({**SLEEP, "guarded": True}))
    data(api.reminder_save_break({**BREAK, "guarded": True, "strict": True}))
    for call in (lambda: api.reminder_set_on("sleep", False), lambda: api.reminder_set_on("break", False),
                 lambda: api.reminder_save_sleep({**SLEEP, "guarded": False}),
                 lambda: api.reminder_save_sleep({**SLEEP, "guarded": True, "on": False}),
                 lambda: api.reminder_save_sleep({**SLEEP, "guarded": True, "before": "10"}),
                 lambda: api.reminder_save_break({**BREAK, "guarded": True, "strict": False}),
                 lambda: api.reminder_save_break({**BREAK, "guarded": True, "strict": True, "every": "2h"}),
                 lambda: api.reminder_save_break({**BREAK, "guarded": False, "strict": True})):
        assert data(call()).get("needs_challenge")
    s = reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    assert s["on"] is True and s["guarded"] is True
    # tightening stays free
    assert data(api.reminder_save_sleep({**SLEEP, "guarded": True, "before": "60"}))["saved"]
    assert data(api.reminder_save_break({**BREAK, "guarded": True, "strict": True, "every": "30 min"}))["saved"]


G_SLEEP = {**SLEEP, "guarded": True, "tiers": [{"every": "15 min", "after": "21:00"},
                                               {"every": "5 min", "after": "00:00"}, {"every": "60s", "after": "03:00"}]}
G_BREAK = {**BREAK, "guarded": True, "strict": True, "max_snooze": 2, "snooze": "5 min"}


def _asks(reply):
    return bool(data(reply).get("needs_challenge"))


def _sleep_change(api, **ch):
    return api.reminder_save_sleep({**G_SLEEP, **ch})


def _tiers(*pairs):
    return [{"every": e, "after": a} for e, a in pairs]


@pytest.mark.parametrize("change", [
    {"tiers": _tiers(("15 min", "21:00"), ("5 min", "00:30"), ("60s", "03:00"))},          # a step starts later
    {"tiers": _tiers(("30 min", "21:00"), ("5 min", "00:00"), ("60s", "03:00"))},          # a longer interval
    {"tiers": _tiers(("15 min", "21:00"), ("5 min", "00:00"))},                            # last step removed
    {"bedtime": "01:00"}, {"wake": "05:00"},                                               # a shorter covered night
], ids=["step-later", "interval-longer", "step-removed", "bedtime-later", "wake-earlier"])
def test_sleep_weaker_asks(api, ctx, locked, change):
    data(api.reminder_save_sleep(G_SLEEP))
    assert _asks(_sleep_change(api, **change))


def test_sleep_without_a_mode_at_bedtime_asks_but_another_mode_is_free(api, ctx, locked):
    import modes
    ids = [m["id"] for m in modes.load(ctx.db)]
    assert len(ids) >= 2, ids
    data(api.reminder_save_sleep({**G_SLEEP, "mode": ids[0]}))
    assert _asks(_sleep_change(api, mode=""))
    assert data(_sleep_change(api, mode=ids[1]))["saved"]
    assert data(_sleep_change(api, mode=ids[1]))["saved"]


@pytest.mark.parametrize("change", [
    {"tiers": _tiers(("5 min", "20:00"), ("15 min", "21:00"), ("5 min", "00:00"), ("60s", "03:00"))},   # extra earlier step
    {"tiers": _tiers(("15 min", "20:00"), ("5 min", "00:00"), ("60s", "03:00"))},                       # a step starts earlier
    {"tiers": _tiers(("10 min", "21:00"), ("5 min", "00:00"), ("30s", "03:00"))},                       # shorter intervals
    {"tiers": _tiers(("15 min", "21:00"), ("5 min", "00:00"), ("60s", "03:00"), ("30s", "04:00"))},    # a step added
    {"bedtime": "22:00"}, {"wake": "09:00"}, {"before": "60"},
], ids=["extra-earlier-step", "step-earlier", "shorter", "step-added", "bedtime-earlier", "wake-later", "heads-up-longer"])
def test_sleep_stricter_is_free(api, ctx, locked, change):
    data(api.reminder_save_sleep(G_SLEEP))
    assert data(_sleep_change(api, **change))["saved"]


def test_sleep_semantics_match_a_minute_by_minute_check(api, ctx, locked):
    """The shortcut (only look where something changes) agrees with looking at every minute of the night."""
    data(api.reminder_save_sleep(G_SLEEP))
    cases = [dict(tiers=_tiers(("15 min", "21:00"), ("5 min", "00:30"), ("60s", "03:00"))),
             dict(tiers=_tiers(("15 min", "21:00"), ("5 min", "00:00"), ("60s", "02:59"))),
             dict(tiers=_tiers(("20 min", "21:00"), ("5 min", "00:00"), ("60s", "03:00"))),
             dict(tiers=[]), dict(bedtime="23:01"), dict(bedtime="22:59"), dict(wake="06:59"), dict(wake="07:01")]
    old = {**reminders.DEFAULT_SLEEP, **reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)}
    for ch in cases:
        out = data(_sleep_change(api, **ch))
        new_tiers = [{"from": t["after"], "every_sec": reminders.parse_interval(t["every"])} for t in ch.get("tiers", [])] \
            if "tiers" in ch else old["tiers"]
        new = {**old, **{k: v for k, v in ch.items() if k != "tiers"}, "tiers": new_tiers}
        brute = False
        for m in range(1440):
            was = page._night_interval(old, m)
            if was is None:
                continue
            now = page._night_interval(new, m)
            brute = brute or now is None or now > was
        assert bool(out.get("needs_challenge")) == brute, ch
        if brute:
            api.challenge_cancel(out["ticket"])
        reminders.save(ctx.db, reminders.SLEEP_KEY, old)     # (back to the base for the next case)


@pytest.mark.parametrize("change", [
    {"strict": False}, {"snooze": "15 min"}, {"length": 2}, {"every": "60 min"}, {"twenty": False},
    {"max_snooze": 3},
], ids=["strict-off", "snooze-longer", "length-shorter", "every-longer", "twenty-off", "more-snoozes-when-strict"])
def test_break_weaker_asks(api, ctx, locked, change):
    data(api.reminder_save_break({**G_BREAK, "twenty": True, "length": 5}))
    assert _asks(api.reminder_save_break({**G_BREAK, "twenty": True, "length": 5, **change}))


def test_break_stricter_is_free_and_strict_on_is_free_even_with_more_snoozes(api, ctx, locked):
    data(api.reminder_save_break({**G_BREAK, "strict": False, "max_snooze": 1}))
    # turning Strict ON is free - the snooze count is only compared when it WAS strict
    assert data(api.reminder_save_break({**G_BREAK, "strict": True, "max_snooze": 5}))["saved"]
    base = reminders.load(ctx.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)
    for ch in ({"snooze": "1 min"}, {"length": 10}, {"every": "30 min"}, {"max_snooze": 1}, {"twenty": True}):
        assert data(api.reminder_save_break({**G_BREAK, "strict": True, "max_snooze": 5, **ch}))["saved"], ch
        reminders.save(ctx.db, reminders.BREAK_KEY, base)


def test_non_strict_break_snoozes_are_not_compared(api, ctx, locked):
    data(api.reminder_save_break({**G_BREAK, "strict": False, "max_snooze": 1}))
    assert data(api.reminder_save_break({**G_BREAK, "strict": False, "max_snooze": 5}))["saved"]


@pytest.mark.parametrize("change", [{"snooze": 15}, {"kind": "random"}, {"kind": "times"}], ids=["snooze-longer",
                         "kind-random", "kind-times"])
def test_custom_snooze_and_kind_changes_ask(api, ctx, locked, change):
    a = add(api, guarded=True)
    assert _asks(api.reminder_save_custom({**CUSTOM, "id": a, "guarded": True, **change}))


def test_custom_random_window_wider_is_free_narrower_or_moved_asks(api, ctx, locked):
    a = add(api, guarded=True, kind="random", window=["10:00", "18:00"])
    base = {**CUSTOM, "id": a, "guarded": True, "kind": "random"}
    assert data(api.reminder_save_custom({**base, "window": ["09:00", "19:00"]}))["saved"]
    assert data(api.reminder_save_custom({**base, "window": ["09:00", "19:00"], "snooze": 1}))["saved"]
    assert _asks(api.reminder_save_custom({**base, "window": ["11:00", "19:00"]}))
    assert _asks(api.reminder_save_custom({**base, "window": ["09:00", "17:00"]}))
    assert _asks(api.reminder_save_custom({**base, "window": ["20:00", "22:00"]}))
    assert _asks(api.reminder_save_custom({**base, "window": ["09:00", "19:00"], "per_day": 1}))     # still a limit


def test_custom_interval_hours_wider_or_off_is_free_narrower_or_on_asks(api, ctx, locked):
    a = add(api, guarded=True, hours=True)
    base = {**CUSTOM, "id": a, "guarded": True, "hours": True}
    assert data(api.reminder_save_custom({**base, "window": ["09:00", "19:00"]}))["saved"]
    assert _asks(api.reminder_save_custom({**base, "window": ["11:00", "19:00"]}))
    assert data(api.reminder_save_custom({**base, "hours": False}))["saved"]
    assert _asks(api.reminder_save_custom({**base, "hours": True}))          # hours ON: fewer minutes covered


def test_a_shorter_dismiss_wait_needs_the_challenge_even_if_not_important(api, ctx, locked):
    data(api.reminder_save_sleep({**SLEEP, "dismiss_wait": 60}))        # longer than none: free
    out = data(api.reminder_save_sleep({**SLEEP, "dismiss_wait": 30}))
    assert out["needs_challenge"]
    assert reminders.dismiss_wait(reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)) == 60
    assert data(api.reminder_save_sleep({**SLEEP, "dismiss_wait": 120}))["saved"]    # longer: free
    assert data(api.reminder_save_sleep({**SLEEP, "dismiss_wait": 0}))["needs_challenge"]


def test_a_stale_ticket_cannot_be_used_twice_and_the_page_has_no_confirmed_flag(api, ctx, locked):
    a = add(api, guarded=True)
    r = api.reminder_set_on(a, False, True)                   # an extra "confirmed" argument is simply an error
    assert not r["ok"]
    out = data(api.reminder_set_on(a, False))
    pass_challenge(locked, out["ticket"], api)
    assert not api.challenge_submit(out["ticket"], "again")["ok"]


# ---------------------------------------------------------------- reading

def test_reminders_get_shape_and_texts(api, ctx):
    a = add(api, text="Stretch", kind="times", times=["09:00", "21:00"], days=[0, 1, 2])
    got = data(api.reminders_get())
    assert set(got) == {"sleep", "break", "custom", "modes", "defaults", "packs", "max_dismiss_wait"}
    row = got["custom"][0]
    assert row["id"] == a and row["schedule"].endswith("at 09:00, 21:00")
    assert row["stats"] == "done 0x in 7 days"
    assert got["packs"] == list(reminders.PACKS) and got["break"]["today"] == 0
    assert got["defaults"]["break_text"] == reminders.BREAK_TEXT


def test_stats_and_breaks_today(api, ctx):
    a = add(api)
    today = datetime.combine(NOW.date(), datetime.min.time())
    for what, result, when in [(a, "done", today - timedelta(days=2)), (a, "done", today - timedelta(days=1)),
                               (a, "not done", today - timedelta(days=1)), (a, "done", today - timedelta(days=9)),
                               ("break", "taken", today + timedelta(hours=9)),
                               ("break", "away", today + timedelta(hours=11)),
                               ("break", "snoozed", today + timedelta(hours=12)),
                               ("break", "taken", today - timedelta(hours=3))]:
        reminders.log(ctx.db, what, result, when)
    got = data(api.reminders_get())
    assert got["custom"][0]["stats"] == "done 2x in 7 days, 'not done' 1x"
    assert got["break"]["today"] == 2


def test_reminders_get_runs_one_counts_query(api, ctx, monkeypatch):
    for i in range(6):
        add(api, text=f"R{i}")
    calls = []
    real = reminders.counts_many
    monkeypatch.setattr(reminders, "counts_many", lambda *a, **k: (calls.append(a[1]), real(*a, **k))[1])
    got = data(api.reminders_get())
    assert len(calls) == 1 and len(calls[0]) == 6 and len(got["custom"]) == 6


def test_reminders_get_is_fast_on_the_two_year_database(tmp_path):
    from synthdata import build
    from db import Database
    path = tmp_path / "big.db"
    Database(path)
    build(path, NOW.date(), days=730)
    c = webapi.make_context(path, clock=lambda db: NOW)
    api = core.build_api(c)
    for i in range(30):
        data(api.reminder_save_custom({**CUSTOM, "text": f"R{i}"}))
        for n in range(20):
            reminders.log(c.db, reminders.custom_list(c.db)[-1]["id"], "done", NOW - timedelta(hours=n * 5))
    times = []
    for _ in range(40):
        t = time.perf_counter()
        data(api.reminders_get())
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    p95 = times[int(len(times) * 0.95)]
    print(f"\n  reminders_get: p50 {times[len(times) // 2]:.1f} ms, p95 {p95:.1f} ms (30 reminders, 2-year DB)")
    assert p95 <= 50


def test_modes_brief(api, ctx):
    import modes
    assert data(api.modes_brief()) == [{"id": m["id"], "name": m["name"]} for m in modes.load(ctx.db)]
    assert data(api.reminders_get())["modes"] == data(api.modes_brief())
