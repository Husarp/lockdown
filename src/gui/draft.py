"""Unsaved-changes layer for the GUI.

Pages edit this draft (blocked items + their rules, notification settings). Nothing is enforced until
save() writes it to the database - or immediately, when auto-save is on. `dirty` is a real comparison with
the saved state, so opening an editor and cancelling doesn't count as a change.
"""
import copy
from datetime import timedelta

import alerts
from db import Database
from rules import TIME_FMT
from trusted_time import now_from_db

AUTOSAVE_KEY = "ui.autosave"
RULE_FIELDS = ("rule_type", "schedule", "temp_until", "daily_limit_min", "duration_min")


def _rule_key(rule: dict) -> tuple:
    return tuple(rule.get(f) for f in RULE_FIELDS)


def _item_key(item: dict) -> tuple:
    return (item["display_name"], item["target"], item["notify"], tuple(sorted(map(_rule_key, item["rules"]))))


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
        self.saved_settings = {k: alerts.get(self.db, k) for k in alerts.DEFAULTS}
        self.settings = dict(self.saved_settings)

    def refresh_if_clean(self):
        """Pick up changes made by the service (e.g. expired temporary blocks) when nothing is pending."""
        if not self.dirty:
            self.reload()

    @property
    def autosave(self) -> bool:
        return self.db.get_setting(AUTOSAVE_KEY, "0") == "1"

    @autosave.setter
    def autosave(self, on: bool):
        self.db.set_setting(AUTOSAVE_KEY, "1" if on else "0")
        if on and self.dirty:
            self.save()

    @property
    def dirty(self) -> bool:
        return self.settings != self.saved_settings or set(self.items) != set(self.saved_items) or any(
            _item_key(item) != _item_key(self.saved_items[i]) for i, item in self.items.items() if i in self.saved_items)

    def is_unsaved(self, item_id: int) -> bool:
        saved = self.saved_items.get(item_id)
        return saved is None or _item_key(saved) != _item_key(self.items[item_id])

    def sorted_items(self) -> list[dict]:
        return sorted(self.items.values(), key=lambda i: i["display_name"].lower())

    # ---------- edits ----------

    def _changed(self):
        if self.autosave:
            self.save()
        else:
            self._notify("changed")

    def _notify(self, kind: str):
        for fn in self.listeners:
            fn(kind)

    def add_item(self, display_name: str, hostnames: list[str], source: str) -> dict:
        item = {"id": self._next_new_id, "display_name": display_name, "target": " ".join(hostnames),
                "item_type": "site", "source": source, "notify": None, "rules": []}
        self._next_new_id -= 1
        self.items[item["id"]] = item
        return item

    def set_rule(self, item_id: int, rule: dict, name: str | None = None):
        """Add or replace the item's rule of rule['rule_type'] (one rule per type per item)."""
        item = self.items[item_id]
        if name:
            item["display_name"] = name
        item["rules"] = [r for r in item["rules"] if r["rule_type"] != rule["rule_type"]] + [rule]
        self._changed()

    def remove_rule(self, item_id: int, rule_type: str):
        item = self.items[item_id]
        item["rules"] = [r for r in item["rules"] if r["rule_type"] != rule_type]
        if not item["rules"]:
            del self.items[item_id]
        self._changed()

    def remove_item(self, item_id: int):
        del self.items[item_id]
        self._changed()

    def set_notify(self, item_id: int, notify: str | None):
        self.items[item_id]["notify"] = notify
        self._changed()

    def set_setting(self, key: str, value: str):
        self.settings[key] = value
        self._changed()

    # ---------- save / discard ----------

    def save(self):
        now = now_from_db(self.db)
        existing = self.db.item_ids()   # the service may have removed expired items meanwhile
        for item_id, item in self.items.items():
            rules = [self._finalize(r, now) for r in item["rules"]]
            if item_id < 0:
                self.db.add_site(item["display_name"], item["target"].split(), item["source"], rules, item["notify"])
                self.db.add_history(item["target"].split()[0], item["display_name"])
            elif item_id in existing and self.is_unsaved(item_id):
                self.db.update_item(item_id, item["display_name"], item["target"].split(), item["notify"], rules)
        for item_id in set(self.saved_items) - set(self.items):
            self.db.remove_item(item_id)
        for key, value in self.settings.items():
            if value != self.saved_settings.get(key):
                self.db.set_setting(key, value)
        self.reload()
        self._notify("saved")

    @staticmethod
    def _finalize(rule: dict, now) -> dict:
        """Temporary rules in the draft hold a duration; the clock starts when they are saved."""
        rule = dict(rule)
        if rule["rule_type"] == "temporary" and rule.get("duration_min"):
            rule["temp_until"] = (now + timedelta(minutes=rule.pop("duration_min"))).strftime(TIME_FMT)
        return rule

    def discard(self):
        self.reload()
        self._notify("discarded")
