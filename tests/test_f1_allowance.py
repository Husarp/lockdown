"""F1 4/6: retained full-week allowances and counters across an overlap upgrade."""
from datetime import datetime, timedelta

import pytest

from db import Database
from rules import BLOCK, allowance_bucket, effective_rules, make_schedule, rule_block, usage_targets

MON = datetime(2026, 9, 14, 9, 5)


def test_full_week_allowance_still_blocks_after_41_days(tmp_path):
    db = Database(tmp_path / 'config.db')
    iid = db.add_site('Always', ['always.test'], rules=[{
        'rule_type': 'scheduled', 'schedule': make_schedule(BLOCK, [(list(range(7)), '00:00', '00:00')]),
        'allowance_min': 5}])
    rule = effective_rules(next(i for i in db.list_items() if i["id"] == iid), [])[0]
    db.add_usage(usage_targets([rule], iid, MON), 300, MON.date())
    assert db.blocks(MON)
    assert db.blocks(MON + timedelta(days=41))
    db.close()


@pytest.mark.parametrize('owner_kind', ['item', 'group', 'member'])
def test_overlap_upgrade_carries_old_counters_once_and_keeps_real_gaps(tmp_path, owner_kind):
    path = tmp_path / 'config.db'
    db = Database(path)
    spec = {'rule_type': 'scheduled', 'schedule': make_schedule(BLOCK, [
        ([0], '09:00', '11:00'), ([0], '10:00', '12:00'), ([0], '14:00', '15:00')]), 'allowance_min': 5}
    iid = db.add_site('Site', ['site.test'], rules=[spec] if owner_kind == 'item' else [])
    if owner_kind != 'item':
        db.add_group('Group', [spec] if owner_kind == 'group' else [],
                     {iid: {'scheduled': spec} if owner_kind == 'member' else {}})
    rule = next(r for r in effective_rules(next(i for i in db.list_items() if i["id"] == iid), db.list_groups())
                if r["rule_type"] == "scheduled")
    owner = rule.get('allowance_owner', f'item:{iid}')
    # Two distinct old buckets in the same continuous stretch, plus yesterday's and a real later gap.
    for end, seconds in [(MON.replace(hour=11, minute=0), 180), (MON.replace(hour=12, minute=0), 120),
                         (MON.replace(hour=15, minute=0), 60), (MON - timedelta(days=7), 999)]:
        db.add_usage({(owner, allowance_bucket(rule, end))}, seconds, MON.date())
    db.conn.execute('PRAGMA user_version=10')
    db.close()
    for _ in range(2):
        db = Database(path)
        rule = next(r for r in effective_rules(next(i for i in db.list_items() if i["id"] == iid), db.list_groups())
                if r["rule_type"] == "scheduled")
        assert rule_block(rule, MON, db.usage_lookup(MON))
        key = (owner, allowance_bucket(rule, MON.replace(hour=12, minute=0)))
        assert db.usage_of({key})[key] == 300
        assert rule_block(rule, MON.replace(hour=14), db.usage_lookup(MON)) is None
        db.close()
