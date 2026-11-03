"""F1 5: partial target recovery for old and stale backups, with visible warnings."""
import json

import backup
import modes
from db import Database


def test_old_format_backup_keeps_usable_choices_and_warns_for_each_dropped_reference(tmp_path):
    db = Database(tmp_path / 'config.db')
    data = {'lockdown': 1, 'items': [
        {'id': 7, 'display_name': 'Keep', 'target': 'keep.test', 'item_type': 'site', 'rules': []}],
        'groups': [{'name': 'Old group', 'members': {'7': {}}, 'rules': []}],
        'settings': {modes.LIST_KEY: json.dumps([{'id': 'custom1', 'name': 'Evening', 'items': [7, 99],
            'groups': [4, 8], 'categories': ['distracting'], 'extra': [], 'mute': True}])}}
    warnings = backup.diff(db, data)
    for text in ['Evening', 'items #99', 'groups #4', 'groups #8']:
        assert any(text in line for line in warnings), warnings
    restored = backup.restore(db, data)
    assert len(restored) == 3
    m = next(m for m in modes.load(db) if m['id'] == 'custom1')
    assert m['items'] == [db.list_items()[0]['id']]
    assert m['groups'] == [] and m['categories'] == ['distracting'] and m['mute']
    assert len(db.list_groups()) == 1
    db.close()


def test_fresh_export_with_deleted_targets_restores_remaining_targets(tmp_path):
    db = Database(tmp_path / 'config.db')
    iid = db.add_site('Keep', ['keep.test'], rules=[])
    gid = db.add_group('Keep group', [], {iid: {}})
    modes.save(db, [{'id': 'custom1', 'name': 'Work', 'items': [iid, 999], 'groups': [gid, 888]}])
    data = backup.export(db)
    warnings = backup.restore(db, data)
    assert len(warnings) == 2
    m = next(m for m in modes.load(db) if m['id'] == 'custom1')
    assert m['items'] == [db.list_items()[0]['id']]
    assert m['groups'] == [db.list_groups()[0]['id']]
    db.close()
