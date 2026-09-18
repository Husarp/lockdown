from datetime import datetime

import pytest

from db import Database
from rules import TIME_FMT


@pytest.fixture
def draft(tmp_path):
    from gui.draft import Draft
    db = Database(tmp_path / "t.db")
    return Draft(db)


def test_changes_are_staged_until_save(draft):
    item = draft.add_item("Reddit", ["reddit.com"], "manual")
    draft.set_rule(item["id"], {"rule_type": "permanent"})
    assert draft.dirty
    assert draft.db.list_items() == []                    # not applied yet
    assert draft.is_unsaved(item["id"])
    draft.save()
    assert not draft.dirty
    assert [i["display_name"] for i in draft.db.list_items()] == ["Reddit"]
    assert draft.db.history()[0]["hostname"] == "reddit.com"


def test_same_values_are_not_dirty(draft):
    item = draft.add_item("Reddit", ["reddit.com"], "manual")
    draft.set_rule(item["id"], {"rule_type": "time_limit", "daily_limit_min": 30})
    draft.save()
    saved_id = next(iter(draft.items))
    draft.set_rule(saved_id, {"rule_type": "time_limit", "daily_limit_min": 30})  # "edit" without a change
    assert not draft.dirty
    draft.set_rule(saved_id, {"rule_type": "time_limit", "daily_limit_min": 45})
    assert draft.dirty
    draft.discard()
    assert not draft.dirty and draft.db.list_items()[0]["rules"][0]["daily_limit_min"] == 30


def test_temporary_starts_at_save(draft):
    item = draft.add_item("YouTube", ["youtube.com"], "manual")
    draft.set_rule(item["id"], {"rule_type": "temporary", "duration_min": 60})
    draft.save()
    until = datetime.strptime(draft.db.list_items()[0]["rules"][0]["temp_until"], TIME_FMT)
    assert 3500 < (until - datetime.now()).total_seconds() <= 3600


def test_remove_rule_removes_item_when_last(draft):
    item = draft.add_item("Reddit", ["reddit.com"], "manual")
    draft.set_rule(item["id"], {"rule_type": "permanent"})
    draft.set_rule(item["id"], {"rule_type": "time_limit", "daily_limit_min": 10})
    draft.save()
    iid = next(iter(draft.items))
    draft.remove_rule(iid, "permanent")
    assert iid in draft.items
    draft.remove_rule(iid, "time_limit")
    assert iid not in draft.items
    draft.save()
    assert draft.db.list_items() == []


def test_autosave(draft):
    draft.autosave = True
    item = draft.add_item("Reddit", ["reddit.com"], "manual")
    draft.set_rule(item["id"], {"rule_type": "permanent"})
    assert not draft.dirty and len(draft.db.list_items()) == 1
    draft.set_setting("notify.format", "inapp")
    assert draft.db.get_setting("notify.format") == "inapp"


def test_save_skips_items_removed_by_service(draft):
    item = draft.add_item("Reddit", ["reddit.com"], "manual")
    draft.set_rule(item["id"], {"rule_type": "permanent"})
    draft.save()
    iid = next(iter(draft.items))
    draft.set_notify(iid, "off")
    draft.db.remove_item(iid)      # e.g. expired temporary block cleaned up by the service
    draft.save()                   # must not crash / resurrect
    assert draft.db.list_items() == []


def test_group_with_new_members_saves_and_maps_ids(draft):
    steam = draft.add_item("Steam", ["steam.exe"], "app-browser", "app", "kill", r"C:\\Steam\\steam.exe")
    yt = draft.add_item("YouTube", ["youtube.com"], "manual")
    gid = draft.set_group(None, "Games", [{"rule_type": "time_limit", "daily_limit_min": 60}],
                          {steam["id"]: {}, yt["id"]: {"time_limit": {"daily_limit_min": 10}}})
    assert draft.dirty and draft.item_not_applied(steam["id"])
    draft.save()
    assert not draft.dirty
    [g] = draft.db.list_groups()
    items = {i["display_name"]: i for i in draft.db.list_items()}
    assert set(g["members"]) == {items["Steam"]["id"], items["YouTube"]["id"]}
    assert g["members"][items["YouTube"]["id"]]["time_limit"]["daily_limit_min"] == 10
    assert items["Steam"]["block_type"] == "kill" and items["Steam"]["item_type"] == "app"


def test_removing_group_drops_items_only_in_it(draft):
    a = draft.add_item("Steam", ["steam.exe"], "manual", "app", "kill")
    b = draft.add_item("Reddit", ["reddit.com"], "manual")
    draft.set_rule(b["id"], {"rule_type": "permanent"})
    gid = draft.set_group(None, "G", [{"rule_type": "permanent"}], {a["id"]: {}, b["id"]: {}})
    draft.save()
    gid = next(iter(draft.groups))
    draft.remove_group(gid)
    assert [i["display_name"] for i in draft.items.values()] == ["Reddit"]
    draft.save()
    assert [i["display_name"] for i in draft.db.list_items()] == ["Reddit"]


def test_edit_group_same_values_not_dirty(draft):
    a = draft.add_item("Steam", ["steam.exe"], "manual", "app", "kill")
    draft.set_group(None, "G", [{"rule_type": "permanent"}], {a["id"]: {}})
    draft.save()
    g = next(iter(draft.groups.values()))
    draft.set_group(g["id"], "G", [{"rule_type": "permanent"}], {i: {} for i in g["members"]})
    assert not draft.dirty
