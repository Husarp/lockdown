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
