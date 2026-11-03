"""Immediate temporary tightening must not commit or discard unrelated drafts."""
import copy
import time
from datetime import datetime, timedelta

import pytest

import antibypass
import webapi
from rules import TIME_FMT, effective_rules, item_block
from webapi import core, draft as drafts

NOW = datetime(2026, 11, 3, 23, 42, 17)
LIMIT = {"rule_type": "time_limit", "daily_limit_min": 60}


@pytest.fixture
def ctx(tmp_path):
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    c.api = core.build_api(c)
    c.publish = lambda *a: None
    c.db.set_setting("ui.autosave", "0")
    c.db.set_setting("service_heartbeat", str(time.time()))
    return c


def add(c):
    return c.db.add_item("Video", ["video.test"], rules=[LIMIT])


def temp(owner):
    return next(r for r in owner["rules"] if r["rule_type"] == "temporary")


@pytest.mark.parametrize("choice,end", [("hour", "2026-11-04T00:42:17"), ("today", "2026-11-04T00:00:00"),
                                      ("until", "2026-11-04T14:00:00")])
def test_trusted_ends_and_enforcement(ctx, choice, end):
    iid = add(ctx)
    reply = ctx.api.blocking_block_now(iid, choice, end)
    assert reply["ok"], reply
    assert reply["data"]["until"] == end
    item = ctx.db.list_items()[0]
    assert item["rules"][0]["daily_limit_min"] == 60
    assert item_block(effective_rules(item, []), NOW, ctx.db.usage_lookup(NOW))
    assert not drafts.draft_of(ctx).dirty


def test_only_temporary_saved_draft_survives_and_discard_keeps_block(ctx):
    iid = add(ctx)
    d = drafts.draft_of(ctx)
    d.set_rules(iid, [{**LIMIT, "daily_limit_min": 120}])
    d.set_notify(iid, "off")
    d.set_setting("notify.format", "inapp")
    new = d.add_item("New", ["new.test"], "manual")
    before = copy.deepcopy(d.items)
    assert ctx.api.blocking_block_now(iid)["ok"]
    assert d.items[iid]["notify"] == "off"
    assert d.items[new["id"]] == before[new["id"]]
    assert d.settings["notify.format"] == "inapp" and d.dirty
    assert ctx.db.list_items()[0]["rules"][0]["daily_limit_min"] == 60
    assert ctx.db.list_items()[0]["notify"] is None
    assert d.loosening()  # pending larger limit remains guarded
    end = temp(ctx.db.list_items()[0])["temp_until"]
    d.discard()
    assert temp(d.items[iid])["temp_until"] == end and not d.dirty


@pytest.mark.parametrize("group", [False, True])
def test_extends_max_and_early_removal_guarded(ctx, group):
    iid = add(ctx)
    oid = ctx.db.add_group("Fun", [LIMIT], {iid: {}}) if group else iid
    assert ctx.api.blocking_block_now(oid, "until", "2026-11-04T18:00:00", group)["ok"]
    assert ctx.api.blocking_block_now(oid, "hour", None, group)["data"]["until"] == "2026-11-04T18:00:00"
    d = drafts.draft_of(ctx)
    owner = (d.groups if group else d.items)[oid]
    owner["rules"] = [LIMIT]
    assert d.loosening()
    guarded = []
    d.guard = lambda changes, proceed, cancel: guarded.append(changes)
    d.save()
    assert guarded
    saved = (ctx.db.list_groups() if group else ctx.db.list_items())[0]
    assert temp(saved)["temp_until"] == "2026-11-04 18:00:00"
    old = temp(saved)
    assert antibypass.rule_looser(old, {**old, "temp_until": "2026-11-04 17:59:59"}, NOW)


def test_pending_longer_temporary_preserved(ctx):
    iid = add(ctx)
    d = drafts.draft_of(ctx)
    d.set_rule(iid, {"rule_type": "temporary", "duration_min": 300})
    assert ctx.api.blocking_block_now(iid)["ok"]
    assert temp(d.items[iid])["duration_min"] == 300 and d.dirty
    assert temp(d.saved_items[iid])["temp_until"] == "2026-11-04 00:42:17"


@pytest.mark.parametrize("until", [None, "garbage", "2026-11-03T22:00", "2026-11-05T01:00", "2026-11-04T14:00Z", True])
def test_bad_dates_do_not_write(ctx, until):
    iid = add(ctx)
    assert not ctx.api.blocking_block_now(iid, "until", until)["ok"]
    assert len(ctx.db.list_items()[0]["rules"]) == 1


def test_unsaved_or_missing_owner_refused(ctx):
    d = drafts.draft_of(ctx)
    item = d.add_item("New", ["new.test"], "manual")
    for iid in [item["id"], 999]:
        assert not ctx.api.blocking_block_now(iid)["ok"]
    assert ctx.db.list_items() == [] and d.dirty


def test_inactive_service_pause_unlock_and_disabled_are_disclosed(ctx):
    import pause
    iid = add(ctx)
    pause.start(ctx.db, NOW, 30, False)
    assert "paused" in ctx.api.blocking_block_now(iid)["data"]["text"]
    pause.resume(ctx.db)
    ctx.db.add_unlock([iid], ["Video"], NOW, NOW + timedelta(minutes=20))
    assert "Emergency unlock" in ctx.api.blocking_block_now(iid)["data"]["text"]
    antibypass.switch_off(ctx.db, NOW)
    assert "Lockdown is off" in ctx.api.blocking_block_now(iid)["data"]["text"]
    ctx.db.set_setting("service_heartbeat", "0")
    assert "Service off" in ctx.api.blocking_block_now(iid)["data"]["text"]
    d = drafts.draft_of(ctx)
    ctx.db.update_item(iid, "Video", ["video.test"], None, ctx.db.list_items()[0]["rules"], disabled=True)
    d.discard()
    assert "Disabled" in ctx.api.blocking_block_now(iid)["data"]["text"]


def test_expiry_keeps_original_limit(ctx):
    iid = add(ctx)
    assert ctx.api.blocking_block_now(iid)["ok"]
    ctx.db.delete_expired_temporary(NOW + timedelta(hours=2))
    assert ctx.db.list_items()[0]["rules"][0]["rule_type"] == "time_limit"
    assert len(ctx.db.list_items()[0]["rules"]) == 1


def test_options_use_saved_state_and_disclose_off(ctx):
    iid = add(ctx)
    d = drafts.draft_of(ctx)
    d.set_rule(iid, {"rule_type": "temporary", "duration_min": 300})
    antibypass.switch_off(ctx.db, NOW)
    reply = ctx.api.blocking_block_now_options(iid)["data"]
    assert reply["now"] == NOW.isoformat() and reply["until"] is None
    assert "Lockdown is off" in reply["note"]
    assert ctx.api.blocking_block_now(iid)["ok"]
    assert ctx.api.blocking_block_now_options(iid)["data"]["until"] == "2026-11-04T00:42:17"


def test_group_block_does_not_save_pending_members_or_name(ctx):
    iid = add(ctx)
    gid = ctx.db.add_group("Fun", [LIMIT], {iid: {}})
    d = drafts.draft_of(ctx)
    d.set_group(gid, "Renamed", [{**LIMIT, "daily_limit_min": 120}], {})
    assert ctx.api.blocking_block_now(gid, "today", None, True)["ok"]
    saved = ctx.db.list_groups()[0]
    assert saved["name"] == "Fun" and iid in saved["members"]
    assert d.groups[gid]["name"] == "Renamed" and not d.groups[gid]["members"]
    assert d.dirty and d.loosening()
    d.discard()
    assert temp(d.groups[gid])["temp_until"] == "2026-11-04 00:00:00"


def test_atomic_merge_uses_current_database_not_stale_draft(ctx):
    from db import Database
    iid = add(ctx)
    d = drafts.draft_of(ctx)
    other = Database(ctx._path)
    other.block_now(iid, "2026-11-08 12:00:00")
    assert ctx.api.blocking_block_now(iid)["data"]["until"] == "2026-11-08T12:00:00"
    assert temp(d.items[iid])["temp_until"] == "2026-11-08 12:00:00"


def test_options_on_an_empty_list_are_readable_without_creating_an_owner(ctx):
    changes = ctx.db.conn.total_changes
    assert ctx.api.blocking_block_now_options(None)["data"] == {
        "now": NOW.isoformat(), "until": None, "note": "", "name": ""}
    assert ctx.db.list_items() == [] and ctx.db.conn.total_changes == changes
    assert not ctx.api.blocking_block_now(None)["ok"]
