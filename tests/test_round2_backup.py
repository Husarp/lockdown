"""Round 2a items 5 and 18: backup targets and honest import previews."""
import json

import pytest

import backup
import modes
from db import Database


@pytest.fixture
def setup_db(tmp_path):
    db = Database(tmp_path / "source.db")
    # Leave gaps, and make group creation order differ from export's name order.
    discarded = db.add_site("Discarded", ["discarded.test"], rules=[])
    first = db.add_site("First", ["first.test"], rules=[{"rule_type": "time_limit", "daily_limit_min": 30}])
    second = db.add_item("Second", ["second.exe"], "app", "manual", [], None, "close")
    db.remove_item(discarded)
    group = db.add_group("Zulu", [{"rule_type": "time_limit", "daily_limit_min": 60}],
                         {second: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 10}}},
                         {"app_block": "close", "member_blocks": {second: "background"}})
    db.add_group("Alpha", [], {first: {}})
    modes.save(db, [{"id": "custom1", "name": "Picked", "categories": [], "items": [first],
                     "groups": [group], "extra": [], "mute": True}])
    yield db
    db.conn.close()


def test_restore_remaps_mode_item_and_group_targets(setup_db, tmp_path):
    data = json.loads(json.dumps(backup.export(setup_db)))
    other = Database(tmp_path / "destination.db")
    try:
        for n in range(4):
            other.add_site(str(n), [f"{n}.test"], rules=[])
            other.add_group(str(n), [], {})
        backup.restore(other, data)
        mode = next(m for m in modes.load(other) if m["id"] == "custom1")
        items = {i["display_name"]: i for i in other.list_items()}
        groups = {g["name"]: g for g in other.list_groups()}
        assert mode["items"] == [items["First"]["id"]]
        assert mode["groups"] == [groups["Zulu"]["id"]]
        assert mode["mute"] is True
        assert {i["target"] for i in modes.targets(mode, other.list_items(), other.list_groups(), {})} == {
            "first.test", "second.exe"}
    finally:
        other.conn.close()


@pytest.mark.parametrize("change", ["item_rule", "item_way", "group_rule", "group_way", "members",
                                  "member_override", "member_way"])
def test_preview_reports_rule_way_and_membership_changes(setup_db, change):
    data = json.loads(json.dumps(backup.export(setup_db)))
    site = next(i for i in data["items"] if i["item_type"] == "site")
    app = next(i for i in data["items"] if i["item_type"] == "app")
    group = next(g for g in data["groups"] if g["name"] == "Zulu")
    if change == "item_rule":
        site["rules"][0]["daily_limit_min"] = 90
    elif change == "item_way":
        app["block_type"] = "minimize"
    elif change == "group_rule":
        group["rules"][0]["daily_limit_min"] = 90
    elif change == "group_way":
        group["app_block"] = "minimize"
    elif change == "members":
        group["members"][str(site["id"])] = {}
    elif change == "member_override":
        group["members"][str(app["id"])]["time_limit"]["daily_limit_min"] = 20
    elif change == "member_way":
        group["member_blocks"][str(app["id"])] = "minimize"
    lines = backup.diff(setup_db, data)
    assert not any("No differences" in line for line in lines), (change, lines)


def test_preview_ignores_database_ids(setup_db):
    data = json.loads(json.dumps(backup.export(setup_db)))
    item_ids = {str(i["id"]): i["id"] + 100 for i in data["items"]}
    for item in data["items"]:
        item["id"] += 100
    group_ids = {}
    for group in data["groups"]:
        if "id" in group:
            group_ids[group["id"]] = group["id"] + 100
            group["id"] += 100
        for field in ("members", "member_blocks"):
            group[field] = {str(item_ids[i]): value for i, value in group[field].items()}
    saved_modes = json.loads(data["settings"][modes.LIST_KEY])
    for mode in saved_modes:
        mode["items"] = [item_ids[str(i)] for i in mode["items"]]
        mode["groups"] = [group_ids.get(i, i) for i in mode["groups"]]
    data["settings"][modes.LIST_KEY] = json.dumps(saved_modes)
    assert backup.diff(setup_db, data) == ["No differences - it matches your current setup."]


def test_preview_detects_changed_mode_targets_with_identical_setting_text(setup_db):
    data = json.loads(json.dumps(backup.export(setup_db)))
    first, second = data["items"]
    first["id"], second["id"] = second["id"], first["id"]
    # Keep each group's actual members the same while the same mode ID now means the other item.
    swaps = {str(first["id"]): str(second["id"]), str(second["id"]): str(first["id"])}
    for group in data["groups"]:
        for field in ("members", "member_blocks"):
            group[field] = {swaps[i]: value for i, value in group[field].items()}
    assert any("setting" in line for line in backup.diff(setup_db, data))


def test_legacy_group_targets_are_dropped_with_a_warning(setup_db):
    # F1 explicitly allows recovery from old exports: keep everything identifiable, warn for each dropped pick.
    data = json.loads(json.dumps(backup.export(setup_db)))
    for group in data["groups"]:
        group.pop("id")
    warnings = backup.restore(setup_db, data)
    assert len(warnings) == 1 and "Picked" in warnings[0] and "groups #" in warnings[0]
    mode = next(m for m in modes.load(setup_db) if m["id"] == "custom1")
    assert mode["groups"] == [] and len(mode["items"]) == 1 and mode["mute"]
    assert len(setup_db.list_groups()) == 2


def test_legacy_backup_without_group_mode_targets_still_restores(setup_db):
    data = json.loads(json.dumps(backup.export(setup_db)))
    for group in data["groups"]:
        group.pop("id")
    saved = json.loads(data["settings"][modes.LIST_KEY])
    saved[0]["groups"] = []
    data["settings"][modes.LIST_KEY] = json.dumps(saved)
    backup.restore(setup_db, data)
    mode = next(m for m in modes.load(setup_db) if m["id"] == "custom1")
    assert {i["target"] for i in modes.targets(mode, setup_db.list_items(), setup_db.list_groups(), {})} == {"first.test"}


def test_backup_round_trip_keeps_preview_identical(setup_db):
    # Legacy default settings added by restore aren't the subject of the preview comparison.
    for key, value in backup.emergency.DEFAULTS.items():
        setup_db.set_setting(key, value)
    setup_db.set_setting("emergency.tiers", "")
    data = json.loads(json.dumps(backup.export(setup_db)))
    backup.restore(setup_db, data)
    assert backup.diff(setup_db, data) == ["No differences - it matches your current setup."]
