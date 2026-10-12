"""R18: the draft over the bridge (webapi/draft.py) - saving, discarding, autosave, the challenge for what loosens, the
notification settings, and that the page can't say "confirmed"."""
import random
import threading
from datetime import datetime

import pytest

import antibypass as ab
import webapi
from webapi import core, draft as drafts, guard
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)


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
    ctx.clock = clock
    ctx.api = core.build_api(ctx)
    ctx.pushed = []
    ctx.publish = lambda event, payload=None, key=None: ctx.pushed.append((event, payload))
    ctx.reddit = ctx.db.add_item("Reddit", ["reddit.com"], "site", "manual", [{"rule_type": "permanent"}])
    ctx.twitch = ctx.db.add_item("Twitch", ["twitch.tv"], "site", "manual",
                                 [{"rule_type": "time_limit", "daily_limit_min": 60}])
    return ctx


def lock(ctx, **kw):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30, **kw})


def autosave(ctx, on):
    ctx.db.set_setting("ui.autosave", "1" if on else "0")
    return ctx


def call(ctx, name, *args):
    return getattr(ctx.api, name)(*args)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def names(ctx):
    return sorted(i["display_name"] for i in ctx.db.list_items())


def pass_challenge(ctx, ticket):
    """What the page does once Python has issued the phrase: type it (slowly enough) and send it."""
    shown = data(ctx.api.challenge_begin(ticket))
    ctx.clock.mono += len(shown["phrase"]) * guard.MIN_MS_PER_CHAR / 1000 + 1
    return ctx.api.challenge_submit(ticket, shown["phrase"])


# ---------------------------------------------------------------- what is registered

def test_the_draft_methods_are_classified():
    kinds = dict(webapi.api_methods())
    assert kinds["draft_get_state"] == "read"
    assert kinds["draft_save"] == kinds["draft_set_autosave"] == "loosen"
    assert kinds["draft_discard"] == kinds["draft_set_setting"] == "ui"


# ---------------------------------------------------------------- saving

def test_saving_a_loosening_edit_while_locked_gives_a_ticket_and_saves_nothing(env):
    autosave(env, False)
    lock(env)
    data(call(env, "draft_remove_item", env.reddit))            # (autosave off: only the draft changes)
    assert names(env) == ["Reddit", "Twitch"]
    reply = call(env, "draft_save")
    got = data(reply)
    assert got["needs_challenge"] is True and got["ticket"] and "Remove Reddit" in got["changes"]
    assert names(env) == ["Reddit", "Twitch"]                   # nothing saved while the challenge is open
    assert data(call(env, "draft_get_state"))["dirty"] is True  # ... and the edit is still waiting
    passed = data(pass_challenge(env, got["ticket"]))
    assert passed["passed"] is True
    assert names(env) == ["Twitch"]
    assert data(call(env, "draft_get_state"))["dirty"] is False


def test_a_cancelled_challenge_leaves_the_edit_pending_when_autosave_is_off(env):
    autosave(env, False)
    lock(env)
    data(call(env, "draft_remove_item", env.reddit))
    ticket = data(call(env, "draft_save"))["ticket"]
    data(env.api.challenge_cancel(ticket))
    assert names(env) == ["Reddit", "Twitch"]
    state = data(call(env, "draft_get_state"))
    assert state["dirty"] is True and state["changes"] == ["Removed Reddit"]


def test_a_tightening_edit_saves_at_once_even_while_locked(env):
    autosave(env, False)
    lock(env)
    drafts.run_edit(env, lambda d: d.set_rules(env.twitch, [{"rule_type": "time_limit", "daily_limit_min": 30}]))
    assert data(call(env, "draft_get_state"))["dirty"] is True
    saved = data(call(env, "draft_save"))
    assert "needs_challenge" not in saved and saved["dirty"] is False
    assert env.db.list_items()[1]["rules"][0]["daily_limit_min"] == 30     # (a limit cut in half: no challenge)


def test_a_loosening_edit_saves_without_a_challenge_when_nothing_is_locked(env):
    autosave(env, False)
    data(call(env, "draft_remove_item", env.reddit))
    assert "needs_challenge" not in data(call(env, "draft_save"))
    assert names(env) == ["Twitch"]


def test_autosave_on_a_loosening_edit_asks_at_once_and_the_item_stays_until_it_is_passed(env):
    autosave(env, True)
    lock(env)
    got = data(call(env, "draft_remove_item", env.reddit))
    assert got["needs_challenge"] is True and got["ticket"]
    assert names(env) == ["Reddit", "Twitch"]
    data(pass_challenge(env, got["ticket"]))
    assert names(env) == ["Twitch"]


def test_autosave_on_a_loosening_edit_with_the_challenge_cancelled_is_discarded(env):
    autosave(env, True)
    lock(env)
    got = data(call(env, "draft_remove_item", env.reddit))
    data(env.api.challenge_cancel(got["ticket"]))
    assert names(env) == ["Reddit", "Twitch"]
    state = data(call(env, "draft_get_state"))
    assert state["dirty"] is False                                           # the edit is gone from the draft too
    overview = data(call(env, "blocking_overview_get"))
    assert {e["name"] for e in overview["entries"]} == {"Reddit", "Twitch"}


def test_turning_autosave_on_with_a_loosening_edit_waiting_asks_and_cancel_discards(env):
    autosave(env, False)
    lock(env)
    data(call(env, "draft_remove_item", env.reddit))
    got = data(call(env, "draft_set_autosave", True))
    assert got["needs_challenge"] is True
    data(env.api.challenge_cancel(got["ticket"]))
    assert names(env) == ["Reddit", "Twitch"] and data(call(env, "draft_get_state"))["dirty"] is False
    assert env.db.get_setting("ui.autosave") == "1"


def test_autosave_can_be_turned_off_and_on_and_must_be_a_bool(env):
    data(call(env, "draft_set_autosave", False))
    assert data(call(env, "draft_get_state"))["autosave"] is False
    data(call(env, "draft_set_autosave", True))
    assert data(call(env, "draft_get_state"))["autosave"] is True
    for bad in ("1", 1, None, [True]):
        assert call(env, "draft_set_autosave", bad)["ok"] is False


def test_discard_throws_the_edits_away(env):
    autosave(env, False)
    data(call(env, "draft_remove_item", env.reddit))
    data(call(env, "draft_discard"))
    assert data(call(env, "draft_get_state"))["dirty"] is False and names(env) == ["Reddit", "Twitch"]


# ---------------------------------------------------------------- the page can't confirm anything

def test_a_confirmed_flag_from_the_page_is_ignored(env):
    autosave(env, False)
    lock(env)
    data(call(env, "draft_remove_item", env.reddit))
    for extra in (True, {"confirmed": True}, "confirmed"):
        refused = call(env, "draft_save", extra)            # no such argument: the call is refused, nothing saved
        assert refused["ok"] is False and "Bad arguments" in refused["error"]
    assert call(env, "draft_remove_item", env.twitch, True)["ok"] is False
    assert call(env, "draft_remove_item", env.twitch, {"confirmed": True})["ok"] is False
    assert call(env, "draft_set_group_disabled", 1, True, True)["ok"] is False
    assert names(env) == ["Reddit", "Twitch"]
    assert data(call(env, "draft_save"))["needs_challenge"] is True           # still needs the real challenge
    assert names(env) == ["Reddit", "Twitch"]


def test_a_ticket_only_runs_after_the_phrase_is_typed_in_python(env):
    autosave(env, True)
    lock(env)
    ticket = data(call(env, "draft_remove_item", env.reddit))["ticket"]
    wrong = env.api.challenge_submit(ticket, "not the phrase")
    assert wrong["ok"] is False and names(env) == ["Reddit", "Twitch"]


# ---------------------------------------------------------------- settings in the draft

def test_draft_set_setting_only_takes_notification_settings(env):
    autosave(env, False)
    for key in ("ui.autosave", "antibypass.phrase", "emergency.enabled", "notify", "dash.hidden", "", None, 5, "notify.nope"):
        assert call(env, "draft_set_setting", key, "1")["ok"] is False
    assert call(env, "draft_set_setting", "notify.format", 5)["ok"] is False
    assert call(env, "draft_set_setting", "notify.msg.permanent", "x" * 3000)["ok"] is False
    assert data(call(env, "draft_get_state"))["dirty"] is False
    got = data(call(env, "draft_set_setting", "notify.format", "inapp"))
    assert got["dirty"] is True and got["change_summary"] == "Notification settings"
    assert env.db.get_setting("notify.format") is None                      # pending until saved
    data(call(env, "draft_save"))
    assert env.db.get_setting("notify.format") == "inapp"


@pytest.mark.parametrize("key, good, bad", [
    ("notify.cooldown_min", ["1", "30", "1440"], ["0", "-5", "1441", "abc", "", "5.5", " 5", "1e3", "٣"]),
    ("notify.warn.minutes", ["1", "5", "60"], ["0", "-1", "99999", "x", "", "2 min"]),
    ("notify.warn.repeat_sec", ["0", "5", "300", "86400"], ["1", "4", "86401", "-1", "off", "", "30s"]),
    ("notify.warn.enabled", ["1", "0"], ["2", "true", "", "on", "11"]),
    ("notify.started.enabled", ["1", "0"], ["yes", ""]),
    ("notify.front.enabled", ["1", "0"], ["yes", "", "2"]),
    ("notify.front.sound", ["1", "0"], ["on", "", "-1"]),
    ("notify.front.steps", ["1", "5, 1", "30", "15, 5, 1"], ["", "0", "31", "5 1", "1,5", "1, 5", "1, 2, 3, 4", "soon", "5 min"]),
    ("notify.enabled.permanent", ["1", "0"], ["maybe", "-1"]),
    ("notify.format", ["toast", "inapp", "both"], ["", "popup", "Toast", "x" * 20]),
])
def test_draft_set_setting_checks_each_keys_value(env, key, good, bad):
    autosave(env, False)
    for value in bad:
        assert call(env, "draft_set_setting", key, value)["ok"] is False, (key, value)
    assert data(call(env, "draft_get_state"))["dirty"] is False
    for value in good:
        assert call(env, "draft_set_setting", key, value)["ok"] is True, (key, value)


def test_draft_set_setting_message_text_is_free_but_bounded(env):
    autosave(env, False)
    assert call(env, "draft_set_setting", "notify.msg.permanent", "{site} is blocked until {until}")["ok"] is True
    assert call(env, "draft_set_setting", "notify.msg.permanent", "")["ok"] is True


def test_draft_set_setting_saves_at_once_with_autosave_and_does_not_reload_blocks(env):
    autosave(env, True)
    lock(env)                                                               # (a setting is not a loosening)
    data(call(env, "draft_set_setting", "notify.cooldown_min", "15"))
    assert env.db.get_setting("notify.cooldown_min") == "15"
    pushed = [p for e, p in env.pushed if e == "draft"]
    assert pushed and all(p["blocks"] is False for p in pushed)             # (the Blocking page keeps its list)
    assert "blocks_changed" not in [e for e, _ in env.pushed]


# ---------------------------------------------------------------- what the page is told

def test_every_change_pushes_draft_and_a_save_pushes_blocks_changed(env):
    autosave(env, False)
    data(call(env, "draft_set_notify", env.reddit, "on"))
    pushed = [p for e, p in env.pushed if e == "draft"]
    assert pushed[-1]["what"] == "changed" and pushed[-1]["dirty"] is True and pushed[-1]["blocks"] is True
    assert pushed[-1]["change_summary"] == "Changed Reddit"
    data(call(env, "draft_save"))
    assert [e for e, _ in env.pushed][-2:] == ["draft", "blocks_changed"]
    assert [p for e, p in env.pushed if e == "draft"][-1]["what"] == "saved"
    data(call(env, "draft_set_notify", env.reddit, "off"))
    data(call(env, "draft_discard"))
    assert [p for e, p in env.pushed if e == "draft"][-1]["what"] == "discarded"
    revs = [p["rev"] for e, p in env.pushed if e == "draft"]
    assert len(set(revs)) == len(revs)                                       # (each push differs: none is dropped as a repeat)


def test_the_shell_status_carries_the_drafts_summary(env):
    autosave(env, False)
    assert data(call(env, "get_status"))["draft"] == {"dirty": False, "autosave": False, "change_summary": ""}
    data(call(env, "draft_remove_item", env.reddit))
    data(call(env, "draft_set_notify", env.twitch, "off"))
    status = data(call(env, "get_status"))["draft"]
    assert status["dirty"] is True and status["change_summary"] == "Changed Twitch, Removed Reddit"


def test_the_summary_names_a_few_and_counts_the_rest(env):
    autosave(env, False)
    for n in range(6):
        drafts.run_edit(env, lambda d, n=n: d.add_item(f"Site {n}", [f"s{n}.example"], "manual"))
    state = data(call(env, "draft_get_state"))
    assert state["change_summary"] == "Added Site 0, Added Site 1, Added Site 2 and 3 more"
    assert len(state["changes"]) == 6


# ---------------------------------------------------------------- the edits of the Overview

def test_edits_of_things_that_are_gone_are_refused(env):
    assert call(env, "draft_remove_item", 999)["ok"] is False
    assert call(env, "draft_remove_group", 999)["ok"] is False
    assert call(env, "draft_set_group_disabled", 999, True)["ok"] is False
    assert call(env, "draft_set_notify", 999, "on")["ok"] is False
    for bad in ("1", None, True, 1.5, [1]):
        assert call(env, "draft_remove_item", bad)["ok"] is False


def test_notify_takes_default_on_off_only(env):
    autosave(env, True)
    for value in ("on", "off", None):
        data(call(env, "draft_set_notify", env.reddit, value))
        assert env.db.list_items()[0]["notify"] == value
    assert call(env, "draft_set_notify", env.reddit, "sometimes")["ok"] is False
    assert call(env, "draft_set_notify", env.reddit, True)["ok"] is False


def test_disabling_a_group_is_a_loosening_and_enabling_is_free(env):
    autosave(env, True)
    gid = env.db.add_group("Fun", [{"rule_type": "time_limit", "daily_limit_min": 60}], {env.reddit: {}})
    lock(env)
    got = data(call(env, "draft_set_group_disabled", gid, True))
    assert got["needs_challenge"] is True
    data(env.api.challenge_cancel(got["ticket"]))
    assert env.db.list_groups()[0]["disabled"] in (0, None)
    ab.save(env.db, {**ab.settings(env.db), "phrase": False})               # (free)
    data(call(env, "draft_set_group_disabled", gid, True))
    assert env.db.list_groups()[0]["disabled"] == 1
    lock(env)
    data(call(env, "draft_set_group_disabled", gid, False))                  # enabling needs no challenge
    assert env.db.list_groups()[0]["disabled"] == 0


def test_removing_a_group_while_locked_asks(env):
    autosave(env, True)
    gid = env.db.add_group("Fun", [{"rule_type": "permanent"}], {env.reddit: {}})
    lock(env)
    got = data(call(env, "draft_remove_group", gid))
    assert got["needs_challenge"] is True and "Remove group Fun" in got["changes"]
    assert len(env.db.list_groups()) == 1
    data(pass_challenge(env, got["ticket"]))
    assert env.db.list_groups() == []


# ---------------------------------------------------------------- threads

def test_edits_from_many_threads_do_not_break_the_draft(env):
    autosave(env, False)
    errors = []

    def work(n):
        try:
            for _ in range(10):
                env.api.draft_set_notify(env.reddit if n % 2 else env.twitch, "on" if n % 3 else "off")
                env.api.draft_get_state()
                env.api.blocking_overview_get()
        except Exception as e:      # pragma: no cover - the assertion below reports it
            errors.append(e)
    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors
    assert data(call(env, "draft_get_state"))["dirty"] is True
