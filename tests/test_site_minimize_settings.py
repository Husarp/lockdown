import json

import pytest

import backup
from blocker.site_block import MINIMIZE_INTERVAL_KEY, site_flags
from db import Database
from test_rebuild_modes import edit, new_mode, start_locked  # noqa: F401
from test_rebuild_notifications_settings import api, ctx, clock, data, error, locked, needs_ticket, pass_challenge  # noqa: F401


def _interval(api):
    return data(api.settings_get())["site_minimize_interval"]


def test_settings_default_boundaries_and_challenge_cancellation(ctx, api):
    assert _interval(api) == 3 and ctx.db.get_setting(MINIMIZE_INTERVAL_KEY) is None
    locked(ctx)
    assert data(api.set_site_minimize_interval(3))["site_minimize_interval"] == 3
    assert data(api.set_site_minimize_interval(2))["site_minimize_interval"] == 2
    ticket = needs_ticket(api.set_site_minimize_interval(10))
    assert _interval(api) == 2
    data(api.challenge_cancel(ticket))
    assert _interval(api) == 2

    ticket = needs_ticket(api.set_site_minimize_interval(10))
    ctx.db.set_setting(MINIMIZE_INTERVAL_KEY, "4")  # Another writer changed it while the challenge was open.
    pass_challenge(ctx, api, ticket)
    assert _interval(api) == 10
    assert data(api.set_site_minimize_interval(3))["site_minimize_interval"] == 3


@pytest.mark.parametrize("value", [None, False, True, "3", 3.0, 3.5, 0, 1, 11])
def test_settings_reject_invalid_saves(api, value):
    assert "whole number from 2 to 10" in error(api.set_site_minimize_interval(value))
    assert _interval(api) == 3


def test_damaged_saved_interval_reads_as_default(ctx, api):
    ctx.db.set_setting(MINIMIZE_INTERVAL_KEY, "11")
    assert _interval(api) == 3


def test_modes_save_site_minimize_and_only_guard_lost_effective_coverage(ctx, api):
    extra = data(api.mode_extra_check({"kind": "site", "text": "video.example.org",
                                      "ways": ["dns", "back", "minimize"]}))
    assert extra["block_type"] == "dns,back,minimize"
    mid = new_mode(api, extra=[extra], categories=[])
    start_locked(ctx, mid)
    tightened = {**extra, "name": "Videos", "block_type": "dns,close,minimize"}
    assert data(api.mode_save(edit(ctx, mid, extra=[tightened])))["saved_id"] == mid
    needs_ticket(api.mode_save(edit(ctx, mid, extra=[{**tightened, "block_type": "dns,close"}])))
    needs_ticket(api.mode_save(edit(ctx, mid, extra=[{**tightened, "block_type": "close,minimize"}])))
    wider = {**tightened, "targets": ["example.org"]}
    assert data(api.mode_save(edit(ctx, mid, extra=[wider])))["saved_id"] == mid
    [block] = ctx.db.blocks(ctx.now())
    assert site_flags(block["item"]["block_type"]) == {"dns", "close", "minimize"}


def test_mode_category_keeps_a_listed_sites_minimize_and_defaults_unlisted_to_dns(ctx, api):
    db = ctx.db
    db.add_item("Listed", ["example.org"], "site", block_type="minimize")
    db.set_category("site", "other.org", "distracting")
    mid = new_mode(api, categories=["distracting"])
    start_locked(ctx, mid)
    assert {b["item"]["target"]: site_flags(b["item"]["block_type"]) for b in db.blocks(ctx.now())} == {
        "example.org": {"minimize"}, "other.org": {"dns"},
    }


def test_blocked_category_keeps_a_listed_sites_minimize(ctx):
    db = ctx.db
    item = db.add_item("Listed", ["example.org"], "site", block_type="minimize")
    db.add_item("Distracting", ["distracting"], "category", rules=[{"rule_type": "permanent"}])
    block = next(b for b in db.blocks(ctx.now()) if b["item"]["id"] == item)
    assert block["category"] == "distracting"
    assert site_flags(block["item"]["block_type"]) == {"minimize"}


def test_round_trip_preserves_site_ways_and_interval(tmp_path):
    with_db = Database(tmp_path / "original.db")
    site = with_db.add_item("Videos", ["example.org"], "site", block_type="minimize")
    with_db.add_item("Old", ["old.org"], "site")
    with_db.add_group("Sites", [{"rule_type": "permanent"}], {site: {}},
                      ways={"site_block": "dns", "member_blocks": {site: "minimize"}})
    with_db.set_setting(MINIMIZE_INTERVAL_KEY, "10")
    data = json.loads(json.dumps(backup.export(with_db)))
    other = Database(tmp_path / "restored.db")
    backup.restore(other, data)
    items = {i["target"]: i for i in other.list_items()}
    assert items["example.org"]["block_type"] == "minimize"
    assert site_flags(items["old.org"]["block_type"]) == {"dns"}
    [group] = other.list_groups()
    assert group["member_blocks"] == {items["example.org"]["id"]: "minimize"}
    assert group["site_block"] == "dns"
    assert other.get_setting(MINIMIZE_INTERVAL_KEY) == "10"
    other.close()
    with_db.close()


def test_old_backup_defaults_to_three_seconds_after_import_challenge(tmp_path, monkeypatch, ctx, api):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"lockdown": 1, "items": [], "settings": {}}))
    monkeypatch.setattr("webapi.settings._ask_open_path", lambda window: str(path))
    ctx.db.set_setting(MINIMIZE_INTERVAL_KEY, "2")
    locked(ctx)
    preview = data(api.backup_import_pick())
    ticket = needs_ticket(api.backup_import_apply(preview["token"]))
    assert _interval(api) == 2
    pass_challenge(ctx, api, ticket)
    assert _interval(api) == 3


def test_import_refuses_an_invalid_site_minimize_interval(tmp_path, monkeypatch, api):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"lockdown": 2, "items": [], "settings": {MINIMIZE_INTERVAL_KEY: "12"}}))
    monkeypatch.setattr("webapi.settings._ask_open_path", lambda window: str(path))
    assert "damaged" in error(api.backup_import_pick())
