"""Unsaved-changes layer for the GUI.

Pages edit this draft (blocked items + their rules, groups, notification settings). Nothing is enforced
until save() writes it to the database - or immediately, when auto-save is on. `dirty` is a real comparison
with the saved state, so opening an editor and cancelling doesn't count as a change.
"""
import copy
import json
from datetime import timedelta

import alerts
from db import Database
from rules import TIME_FMT
from trusted_time import now_from_db

AUTOSAVE_KEY = "ui.autosave"
RULE_FIELDS = ("rule_type", "schedule", "temp_until", "daily_limit_min", "duration_min", "allowance_min",
               "daily_switch_limit")


def _rule_key(rule: dict) -> tuple:
    return tuple(rule.get(f) for f in RULE_FIELDS)


def _rules_key(rules: list[dict]) -> tuple:
    return tuple(sorted(map(_rule_key, rules), key=repr))


def _item_key(item: dict) -> tuple:
    return (item["display_name"], item["target"], item["notify"], item.get("block_type"), item.get("app_path"),
            _rules_key(item["rules"]))


def _group_key(group: dict) -> tuple:
    members = tuple(sorted((i, json.dumps({t: _rule_key(r) for t, r in (o or {}).items()}, sort_keys=True))
                           for i, o in group["members"].items()))
    return group["name"], _rules_key(group["rules"]), members


def _finalize(rule: dict, now) -> dict:
    """Temporary rules in the draft hold a duration; the clock starts when they are saved."""
    rule = {k: v for k, v in rule.items() if k in RULE_FIELDS}
    if rule["rule_type"] == "temporary" and rule.get("duration_min"):
        rule["temp_until"] = (now + timedelta(minutes=rule.pop("duration_min"))).strftime(TIME_FMT)
    return rule


class Draft:
    def __init__(self, db: Database):
        self.db = db
        self.listeners = []       # fn(kind) after every change; kind: "changed" / "saved" / "discarded"
        self._next_new_id = -1
        self.reload()

    # ---------- state ----------

    def reload(self):
        """Throw away the draft and load the saved state."""
        self.saved_items = {i["id"]: i for i in self.db.list_items()}
        self.items = copy.deepcopy(self.saved_items)
        self.saved_groups = {g["id"]: g for g in self.db.list_groups()}
        self.groups = copy.deepcopy(self.saved_groups)
        self.saved_settings = {k: alerts.get(self.db, k) for k in alerts.DEFAULTS}
        self.settings = dict(self.saved_settings)

    def refresh_if_clean(self):
        """Pick up changes made by the service (e.g. expired temporary blocks) when nothing is pending."""
        if not self.dirty:
            self.reload()

    @property
    def autosave(self) -> bool:
        return self.db.get_setting(AUTOSAVE_KEY, "1") == "1"   # on by default

    @autosave.setter
    def autosave(self, on: bool):
        self.db.set_setting(AUTOSAVE_KEY, "1" if on else "0")
        if on and self.dirty:
            self.save()

    @property
    def dirty(self) -> bool:
        return (self.settings != self.saved_settings
                or set(self.items) != set(self.saved_items) or set(self.groups) != set(self.saved_groups)
                or any(self.is_unsaved(i) for i in self.items)
                or any(self.is_group_unsaved(g) for g in self.groups))

    def is_unsaved(self, item_id: int) -> bool:
        saved = self.saved_items.get(item_id)
        return saved is None or _item_key(saved) != _item_key(self.items[item_id])

    def is_group_unsaved(self, group_id: int) -> bool:
        saved = self.saved_groups.get(group_id)
        return saved is None or _group_key(saved) != _group_key(self.groups[group_id])

    def item_not_applied(self, item_id: int) -> bool:
        """The item, or a group it's in (or was in), has unsaved changes."""
        if self.is_unsaved(item_id):
            return True
        for gid in set(self.groups) | set(self.saved_groups):
            in_draft = gid in self.groups and item_id in self.groups[gid]["members"]
            in_saved = gid in self.saved_groups and item_id in self.saved_groups[gid]["members"]
            if (in_draft or in_saved) and (gid not in self.groups or self.is_group_unsaved(gid)):
                return True
        return False

    def sorted_items(self) -> list[dict]:
        return sorted(self.items.values(), key=lambda i: i["display_name"].lower())

    def sorted_groups(self) -> list[dict]:
        return sorted(self.groups.values(), key=lambda g: g["name"].lower())

    def groups_of(self, item_id: int) -> list[dict]:
        return [g for g in self.sorted_groups() if item_id in g["members"]]

    # ---------- edits ----------

    def _new_id(self) -> int:
        self._next_new_id -= 1
        return self._next_new_id + 1

    def _changed(self):
        if self.autosave:
            self.save()
        else:
            self._notify("changed")

    def _notify(self, kind: str):
        for fn in self.listeners:
            fn(kind)

    def add_item(self, display_name: str, targets: list[str], source: str, item_type: str = "site",
                 block_type: str | None = None, app_path: str | None = None) -> dict:
        item = {"id": self._new_id(), "display_name": display_name, "target": " ".join(targets),
                "item_type": item_type, "source": source, "notify": None, "block_type": block_type,
                "app_path": app_path, "rules": []}
        self.items[item["id"]] = item
        return item

    def find_item(self, target: str) -> dict | None:
        target = target.lower()
        return next((i for i in self.items.values() if target in i["target"].lower().split()), None)

    def set_rule(self, item_id: int, rule: dict, name: str | None = None, block_type: str | None = None):
        """Add or replace the item's rule of rule['rule_type'] (one rule per type per item)."""
        item = self.items[item_id]
        if name:
            item["display_name"] = name
        if block_type and item["item_type"] == "app":
            item["block_type"] = block_type
        item["rules"] = [r for r in item["rules"] if r["rule_type"] != rule["rule_type"]] + [rule]
        self._changed()

    def set_rules(self, item_id: int, rules: list[dict], name: str | None = None, block_type: str | None = None):
        """Replace all of the item's own rules (an item left with none and in no group is removed)."""
        item = self.items[item_id]
        if name:
            item["display_name"] = name
        if block_type and item["item_type"] == "app":
            item["block_type"] = block_type
        item["rules"] = rules
        if not rules and not self.groups_of(item_id):
            del self.items[item_id]
        self._changed()

    def remove_rule(self, item_id: int, rule_type: str):
        item = self.items[item_id]
        item["rules"] = [r for r in item["rules"] if r["rule_type"] != rule_type]
        if not item["rules"] and not self.groups_of(item_id):
            del self.items[item_id]
        self._changed()

    def remove_item(self, item_id: int):
        del self.items[item_id]
        for g in self.groups.values():
            g["members"].pop(item_id, None)
        self._changed()

    def set_notify(self, item_id: int, notify: str | None):
        self.items[item_id]["notify"] = notify
        self._changed()

    def set_setting(self, key: str, value: str):
        self.settings[key] = value
        self._changed()

    def set_group(self, group_id: int | None, name: str, rules: list[dict], members: dict[int, dict]) -> int:
        """Create (group_id None) or replace a group. Members: {item_id: {rule_type: customized rule}}."""
        if group_id is None:
            group_id = self._new_id()
        self.groups[group_id] = {"id": group_id, "name": name, "rules": rules, "members": members}
        self._drop_orphans()
        self._changed()
        return group_id

    def remove_group(self, group_id: int):
        del self.groups[group_id]
        self._drop_orphans()
        self._changed()

    def _drop_orphans(self):
        """Items that were only in groups and aren't in any anymore disappear, like items with no rules."""
        for item_id in [i for i, it in self.items.items() if not it["rules"] and not self.groups_of(i)]:
            del self.items[item_id]

    # ---------- save / discard ----------

    def save(self):
        now = now_from_db(self.db)
        existing = self.db.item_ids()   # the service may have removed expired items meanwhile
        new_ids = {}
        for item_id, item in self.items.items():
            args = (item["display_name"], item["target"].split())
            rules = [_finalize(r, now) for r in item["rules"]]
            if item_id < 0:
                new_ids[item_id] = self.db.add_item(*args, item["item_type"], item["source"], rules, item["notify"],
                                                    item.get("block_type"), item.get("app_path"))
                if item["item_type"] == "site":
                    self.db.add_history(item["target"].split()[0], item["display_name"])
            elif item_id in existing and self.is_unsaved(item_id):
                self.db.update_item(item_id, *args, item["notify"], rules, item.get("block_type"),
                                    item.get("app_path"))
        for item_id in set(self.saved_items) - set(self.items):
            self.db.remove_item(item_id)

        existing = self.db.item_ids()
        saved_group_ids = self.db.group_ids()
        for group_id, g in self.groups.items():
            members = {new_ids.get(i, i): {t: _finalize({**r, "rule_type": t}, now) for t, r in (o or {}).items()}
                       for i, o in g["members"].items()}
            members = {i: o for i, o in members.items() if i in existing}
            rules = [_finalize(r, now) for r in g["rules"]]
            if group_id < 0:
                self.db.add_group(g["name"], rules, members)
            elif group_id in saved_group_ids and self.is_group_unsaved(group_id):
                self.db.update_group(group_id, g["name"], rules, members)
        for group_id in set(self.saved_groups) - set(self.groups):
            self.db.remove_group(group_id)

        for key, value in self.settings.items():
            if value != self.saved_settings.get(key):
                self.db.set_setting(key, value)
        self.reload()
        self._notify("saved")

    def discard(self):
        self.reload()
        self._notify("discarded")
