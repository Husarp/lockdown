"""Today-only grants use the same owner and limit clock as enforcement, behind the real guard."""
import copy
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import antibypass
import challenge_secrets
import webapi
from db import Database
from rules import (RESET_KEY, Usage, block_targets, describe_rule, effective_rules, extra_time,
                   item_block, limit_targets, make_schedule, next_block, rule_state, time_bucket)
from webapi import core, draft as drafts

NOW = datetime(2026, 11, 3, 23, 30)
LIMIT = {"rule_type": "time_limit", "daily_limit_min": 30}


@pytest.fixture
def ctx(tmp_path):
    now = [NOW]
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: now[0])
    c.time = now
    c.api = core.build_api(c)
    c.events, c.pokes = [], []
    c.publish = lambda *event: c.events.append(event)
    c.hub = SimpleNamespace(status_worker=SimpleNamespace(poke=lambda: c.pokes.append(True)))
    return c


def add(c, kind="site", rules=None):
    return c.db.add_item("Video", ["video.exe" if kind == "app" else "video.test"], kind, "manual",
                         rules if rules is not None else [LIMIT])


def rules_of(c, iid):
    item = next(i for i in c.db.list_items() if i["id"] == iid)
    return effective_rules(item, c.db.list_groups())


def spend(c, owner, minutes, now=None):
    now = now or c.now()
    c.db.add_usage([(owner, time_bucket("day", now, c.db.limit_clock()))], minutes * 60, now.date())


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


@pytest.mark.parametrize("kind", ["site", "app", "group"])
def test_grants_stack_persist_and_only_change_the_owner_daily_limit(ctx, kind):
    iid = add(ctx, "app" if kind == "app" else "site")
    group = kind == "group"
    oid = ctx.db.add_group("Shared", [LIMIT], {iid: {}}) if group else iid
    owner = f"{'group' if group else 'item'}:{oid}"
    data(ctx.api.blocking_add_time(oid, 15, group))
    data(ctx.api.blocking_add_time(oid, 45, group))
    restarted = Database(ctx._path)
    usage = restarted.usage_lookup(NOW)
    assert extra_time(owner, NOW, usage) == 3600
    assert extra_time(f"{'item' if group else 'group'}:{oid}", NOW, usage) == 0
    assert usage(owner, time_bucket("day", NOW, usage.clock)) == 0  # actual time used is untouched
    saved = (restarted.list_groups() if group else restarted.list_items())[0]
    assert saved["rules"][0]["daily_limit_min"] == 30
    overview = data(ctx.api.blocking_overview_get())["entries"][0]
    assert overview["add_time"]
    assert any("30m + 1h 00m extra today" in chip["t"] for chip in overview["chips"])
    assert all("today today" not in chip["t"] for chip in overview["chips"])
    editor = data(ctx.api.group_get(oid) if group else ctx.api.item_get(oid))
    assert "1h 00m extra today" in editor["extra_today"]
    assert "00:00" in editor["extra_today"]
    restarted.close()


@pytest.mark.parametrize("reset", ["00:00", "03:00"])
def test_grant_expires_at_configured_daily_reset(ctx, reset):
    ctx.db.set_setting(RESET_KEY, json.dumps({"time": reset}))
    iid = add(ctx)
    owner = f"item:{iid}"
    end = ctx.db.limit_clock().day(NOW)[1]
    data(ctx.api.blocking_add_time(iid, 30))
    before = end - timedelta(seconds=1)
    # A 03:00 reset retains the grant across calendar midnight and a restart.
    fresh = Database(ctx._path)
    assert extra_time(owner, before, fresh.usage_lookup(before)) == 1800
    assert extra_time(owner, end, fresh.usage_lookup(end)) == 0
    assert data(ctx.api.blocking_add_time_options(iid))["until"] == end.isoformat()
    spend(ctx, owner, 30, end)
    assert item_block(rules_of(ctx, iid), end, fresh.usage_lookup(end))
    assert "extra today" not in describe_rule(rules_of(ctx, iid)[0], end, fresh.usage_lookup(end))
    fresh.close()


def test_used_up_limit_unblocks_immediately_and_blocks_again_when_extra_is_spent(ctx, monkeypatch):
    import enforcement_wake
    wake = []
    monkeypatch.setattr(enforcement_wake, "request", lambda: wake.append(True))
    iid = add(ctx)
    owner = f"item:{iid}"
    spend(ctx, owner, 30)
    assert ctx.db.blocks(NOW)
    data(ctx.api.blocking_add_time(iid, 15))
    assert ctx.db.blocks(NOW) == []
    assert ctx.pokes == [True] and ctx.events[-1][0] == "blocks_changed"
    assert wake == [True]
    rules = rules_of(ctx, iid)
    usage = ctx.db.usage_lookup(NOW)
    assert rule_state(rules[0], NOW, usage) == "allowed"
    assert next_block(rules, NOW, usage, in_use=True)[0] == NOW + timedelta(minutes=15)
    assert limit_targets(rules, NOW, usage.clock, usage)[(owner, time_bucket("day", NOW))] == 45 * 60
    # The fast lookup used for app openings must load grants too.
    fast = Usage(ctx.db.usage_of(block_targets(rules, NOW, usage.clock)), usage.clock)
    assert item_block(rules, NOW, fast) is None
    spend(ctx, owner, 15)
    assert ctx.db.blocks(NOW)[0]["reason"] == "limit"


@pytest.mark.parametrize("rule,reason,until", [
    ({"rule_type": "scheduled", "schedule": make_schedule("block", [(list(range(7)), "22:00", "02:00")])},
     "scheduled blocked hours", "02:00"),
    ({"rule_type": "temporary", "temp_until": "2026-11-04 01:00:00"}, "Block now", "01:00"),
    ({"rule_type": "permanent"}, "an always blocked rule", "it is changed"),
])
def test_other_blocks_are_disclosed_and_never_lifted(ctx, rule, reason, until):
    iid = add(ctx, rules=[LIMIT, rule])
    note = data(ctx.api.blocking_add_time_options(iid))["note"]
    assert "Extra time only applies to the time limit" in note
    assert reason in note and until in note
    data(ctx.api.blocking_add_time(iid, 120))
    assert ctx.db.blocks(NOW)


def test_schedule_allowance_is_not_extended(ctx):
    schedule = {"rule_type": "scheduled", "allowance_min": 5,
                "schedule": make_schedule("block", [(list(range(7)), "22:00", "02:00")])}
    iid = add(ctx, rules=[LIMIT, schedule])
    from rules import usage_targets
    ctx.db.add_usage(usage_targets(rules_of(ctx, iid), iid, NOW), 5 * 60, NOW.date())
    assert ctx.db.blocks(NOW)[0]["reason"] == "schedule"
    data(ctx.api.blocking_add_time(iid, 30))
    assert ctx.db.blocks(NOW)[0]["reason"] == "schedule"


def test_group_grant_frees_all_members_but_keeps_own_and_other_group_limits(ctx):
    first, second = add(ctx, rules=[]), ctx.db.add_item("Other", ["other.test"], "site", "manual", [])
    gid = ctx.db.add_group("Shared", [LIMIT], {first: {}, second: {}})
    spend(ctx, f"group:{gid}", 30)
    assert len(ctx.db.blocks(NOW)) == 2
    data(ctx.api.blocking_add_time(gid, 30, True))
    assert not ctx.db.blocks(NOW)
    another = ctx.db.add_group("Separate", [LIMIT], {first: {}})
    spend(ctx, f"group:{another}", 30)
    assert [b["item"]["id"] for b in ctx.db.blocks(NOW)] == [first]


def test_group_dialog_discloses_member_schedule_that_extra_time_cannot_lift(ctx):
    iid = add(ctx, rules=[{"rule_type": "scheduled",
                          "schedule": make_schedule("block", [(list(range(7)), "22:00", "02:00")])}])
    gid = ctx.db.add_group("Shared", [LIMIT], {iid: {}})
    note = data(ctx.api.blocking_add_time_options(gid, True))["note"]
    assert "Video:" in note and "scheduled blocked hours" in note and "02:00" in note
    data(ctx.api.blocking_add_time(gid, 30, True))
    assert ctx.db.blocks(NOW)[0]["reason"] == "schedule"


def test_item_grant_extends_its_own_custom_limit_in_a_group(ctx):
    iid = add(ctx, rules=[])
    gid = ctx.db.add_group("Shared", [{**LIMIT, "daily_limit_min": 60}], {iid: {"time_limit": LIMIT}})
    spend(ctx, f"item:{iid}", 30)
    spend(ctx, f"group:{gid}", 30)
    assert ctx.db.blocks(NOW)
    member = data(ctx.api.blocking_overview_get())["entries"][0]["members"][0]
    assert member["add_time"]
    data(ctx.api.blocking_add_time(iid, 15))
    assert not ctx.db.blocks(NOW)
    usage = ctx.db.usage_lookup(NOW)
    assert extra_time(f"item:{iid}", NOW, usage) == 900
    assert extra_time(f"group:{gid}", NOW, usage) == 0


@pytest.mark.parametrize("custom", [False, True])
def test_grant_rechecks_real_service_and_tracker_enforces_the_extended_limit(tmp_path, monkeypatch, custom):
    import enforcement_wake
    from test_app_limit_enforcement import World, add_game, BOOT
    w = World(tmp_path, monkeypatch, NOW.replace(hour=12))
    own = {**LIMIT, "daily_limit_min": 1}
    iid = add_game(w, [] if custom else [own])
    if custom:
        w.gui.add_group("Games", [{**LIMIT, "daily_limit_min": 3}], {iid: {"time_limit": own}})
    w.gui.add_usage([(f"item:{iid}", time_bucket("day", w.now))], 60, w.now.date())
    w.enforcer.enforce_once()
    assert "hollowgame.exe" in w.enforcer.app_blocks
    c = webapi.make_context(w.path, clock=lambda db: w.now)
    monkeypatch.setattr(enforcement_wake, "request", w.enforcer.enforce_once)
    data(core.build_api(c).blocking_add_time(iid, 1))
    assert w.enforcer.app_blocks == {} and not w.gui.blocks(w.now)
    pid = w.launch("hollowgame.exe", BOOT)
    w.play(pid)
    w.run(0.5, every=1)
    assert w.alive(pid)
    assert w.tracker.limits[(f"item:{iid}", time_bucket("day", w.now))] == 120
    w.run(0.7, every=1)
    assert w.reason(iid) == "limit" and not w.alive(pid)


def test_weekly_and_monthly_limits_still_block(ctx):
    iid = add(ctx, rules=[{**LIMIT, "weekly_limit_min": 60, "monthly_limit_min": 90}])
    owner = f"item:{iid}"
    data(ctx.api.blocking_add_time(iid, 120))
    for period, minutes in [("week", 60), ("month", 90)]:
        ctx.db.add_usage([(owner, time_bucket(period, NOW))], minutes * 60, NOW.date())
        assert ctx.db.blocks(NOW)[0]["reason"] == "limit"


@pytest.mark.parametrize("rules", [[{"rule_type": "permanent"}],
                                  [{"rule_type": "time_limit", "weekly_limit_min": 30}],
                                  [{"rule_type": "time_limit", "daily_limit_days": json.dumps([30, None] + [30] * 5)}]])
def test_no_daily_limit_today_disables_action_and_refuses_direct_calls(ctx, rules):
    iid = add(ctx, rules=rules)
    assert not data(ctx.api.blocking_overview_get())["entries"][0]["add_time"]
    reply = ctx.api.blocking_add_time(iid, 15)
    assert not reply["ok"] and "No daily time limit" in reply["error"]


@pytest.mark.parametrize("minutes", [0, -1, 1441, True, "15", 1.5, None])
def test_invalid_grants_are_refused_without_writes(ctx, minutes):
    iid = add(ctx)
    changes = ctx.db.conn.total_changes
    assert not ctx.api.blocking_add_time(iid, minutes)["ok"]
    assert ctx.db.conn.total_changes == changes


def test_pending_drafts_are_preserved_and_discard_does_not_remove_grant(ctx):
    iid = add(ctx)
    d = drafts.draft_of(ctx)
    d.autosave = False
    d.set_rules(iid, [{**LIMIT, "daily_limit_min": 120}])
    pending = copy.deepcopy(d.items)
    data(ctx.api.blocking_add_time(iid, 15))
    assert d.items == pending and d.dirty and d.loosening()
    d.discard()
    assert extra_time(f"item:{iid}", NOW, ctx.db.usage_lookup(NOW)) == 900
    assert ctx.db.list_items()[0]["rules"][0]["daily_limit_min"] == 30


def lock(ctx):
    antibypass.save(ctx.db, {**antibypass.settings(ctx.db), "phrase": True, "length": 30})
    mono = [1000.0]
    ctx.guard._mono = lambda: mono[0]
    return mono


def test_challenge_required_cancellation_does_not_grant_and_pass_runs_once(ctx):
    iid = add(ctx)
    mono = lock(ctx)
    ticket = data(ctx.api.blocking_add_time(iid, 15))["ticket"]
    assert extra_time(f"item:{iid}", NOW, ctx.db.usage_lookup(NOW)) == 0
    data(ctx.api.challenge_cancel(ticket))
    assert not ctx.pokes
    ticket = data(ctx.api.blocking_add_time(iid, 15))["ticket"]
    view = data(ctx.api.challenge_begin(ticket))
    mono[0] += 100
    assert data(ctx.api.challenge_submit(ticket, view["phrase"]))["passed"]
    assert extra_time(f"item:{iid}", NOW, ctx.db.usage_lookup(NOW)) == 900
    assert not ctx.api.challenge_submit(ticket, view["phrase"])["ok"]
    assert dict(webapi.api_methods())["blocking_add_time"] == "loosen"


def test_supervisor_code_grants_only_requested_time_even_during_wait(ctx, monkeypatch):
    iid = add(ctx)
    lock(ctx)
    antibypass.save(ctx.db, {**antibypass.settings(ctx.db), "wait_min": 60})
    antibypass.unlock(ctx.db, NOW)
    before = antibypass.settings(ctx.db)
    secret = challenge_secrets.new_totp_secret()
    challenge_secrets.set_totp(ctx.db, secret, -1, NOW, "supervisor")
    epoch = NOW.timestamp()
    monkeypatch.setattr(challenge_secrets, "trusted_epoch", lambda db: epoch)
    ticket = data(ctx.api.blocking_add_time(iid, 30))["ticket"]
    assert data(ctx.api.challenge_begin(ticket))["supervisor"]
    code = challenge_secrets.totp(challenge_secrets.secret_key(secret), epoch)
    assert data(ctx.api.challenge_submit(ticket, {"supervisor": code}))["passed"]
    assert extra_time(f"item:{iid}", NOW, ctx.db.usage_lookup(NOW)) == 1800
    assert antibypass.settings(ctx.db) == before


def test_challenge_crossing_reset_cannot_grant_for_the_next_day(ctx):
    iid = add(ctx)
    mono = lock(ctx)
    ticket = data(ctx.api.blocking_add_time(iid, 15))["ticket"]
    view = data(ctx.api.challenge_begin(ticket))
    mono[0] += 100
    ctx.time[0] = NOW + timedelta(hours=1)
    reply = ctx.api.challenge_submit(ticket, view["phrase"])
    assert not reply["ok"] and "reset" in reply["error"]
    assert extra_time(f"item:{iid}", ctx.now(), ctx.db.usage_lookup(ctx.now())) == 0
