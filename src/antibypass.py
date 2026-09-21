"""Anti-Bypass: anything that loosens a block needs a challenge first; tightening is always instant.

Challenges (any combination, both off = Anti-Bypass off):
- phrase: type a random phrase (no pasting). Passing it unlocks loosening changes for UNLOCK_MIN minutes.
  Optional "3×3 grid" (off by default): one word at a time into a box picked at random (you have to click it).
- hours: loosening changes only during the chosen time windows (e.g. Sunday 18:00-20:00).
- wait: passing the challenge doesn't open at once - the unlock starts this many minutes later (a cooling-off
  period; the wait runs in the database, so closing Lockdown or rebooting doesn't skip it).
The emergency unlock stays outside (it has its own weekly limit). UI-free, so it can be tested."""
import json
import random
import string
from datetime import datetime, timedelta

from blocker.apps import block_flags
from blocker.site_block import site_flags
from rules import (OPEN_LIMIT_FIELDS, TIME_FMT, TIME_LIMIT_FIELDS, allowance_shared, next_window_start,
                   window_until)

SETTINGS_KEY = "antibypass"   # JSON {"phrase": bool, "length": chars, "hours": bool, "windows": [...],
#                                     "wait_min": minutes, "unlocked_from"/"unlocked_until": "Y-m-d H:M:S"}
UNLOCK_MIN = 5
EXITED_KEY = "agent.exited"   # "1" after tray Exit: the watchdog doesn't bring the tray app back until next login
LENGTHS = {"Short": 30, "Medium": 60, "Long": 120, "Very long": 250}
DEFAULTS = {"phrase": False, "length": 60, "grid": False, "complex": False, "custom_phrase": "", "hours": False,
            "windows": [{"days": [6], "start": "18:00", "end": "20:00"}], "wait_min": 0,
            "unlocked_from": None, "unlocked_until": None}


def settings(db) -> dict:
    try:
        cfg = json.loads(db.get_setting(SETTINGS_KEY, "") or "{}")
    except ValueError:
        cfg = {}
    return {**DEFAULTS, **cfg}


def save(db, cfg: dict):
    db.set_setting(SETTINGS_KEY, json.dumps(cfg))


def active(cfg: dict) -> bool:
    return bool(cfg["phrase"] or cfg["hours"])


def in_hours(cfg: dict, now: datetime) -> bool:
    return any(window_until(w, now) for w in cfg["windows"])


def next_hours(cfg: dict, now: datetime) -> datetime | None:
    return next_window_start(cfg["windows"], now)


def _at(cfg: dict, key: str) -> datetime | None:
    value = cfg.get(key)
    return datetime.strptime(value, TIME_FMT) if value else None


def waiting_until(cfg: dict, now: datetime) -> datetime | None:
    """The challenge is passed but the wait hasn't run out yet: when it does."""
    start = _at(cfg, "unlocked_from")
    return start if start and now < start and unlocked_until(cfg, now, waiting=True) else None


def unlocked_until(cfg: dict, now: datetime, waiting: bool = False) -> datetime | None:
    """When the unlock runs out, or None if you aren't unlocked. During the wait this is None (nothing is
    allowed yet) unless the caller is asking about the wait itself."""
    until = _at(cfg, "unlocked_until")
    if not until or now >= until:
        return None
    start = _at(cfg, "unlocked_from")
    return until if waiting or not start or now >= start else None


def status(cfg: dict, now: datetime) -> str:
    """"free" (loosening allowed now), "closed" (outside the allowed hours) or "phrase" (type the phrase first)."""
    if not active(cfg):
        return "free"
    if cfg["hours"] and not in_hours(cfg, now):
        return "closed"
    if waiting_until(cfg, now):
        return "waiting"
    if cfg["phrase"] and not unlocked_until(cfg, now):
        return "phrase"
    return "free"


def unlock(db, now: datetime) -> datetime | None:
    """The challenge was passed: open the unlock, after the wait if one is set. Returns when the wait ends."""
    cfg = settings(db)
    start = now + timedelta(minutes=cfg.get("wait_min") or 0)
    cfg["unlocked_from"] = start.strftime(TIME_FMT)
    cfg["unlocked_until"] = (start + timedelta(minutes=UNLOCK_MIN)).strftime(TIME_FMT)
    save(db, cfg)
    return start if start > now else None


def lock(db):
    cfg = settings(db)
    cfg["unlocked_from"] = cfg["unlocked_until"] = None
    save(db, cfg)


def new_phrase(length: int, complex: bool = False, rng=random) -> str:
    """Random phrase in groups of 5 ("kqzph mxacd ..."). Lowercase letters by default; complex also adds capital
    letters and digits (harder to read and type)."""
    charset = string.ascii_lowercase + (string.ascii_uppercase + string.digits if complex else "")
    chars = "".join(rng.choice(charset) for _ in range(length))
    return " ".join(chars[i:i + 5] for i in range(0, length, 5))


def phrase_for(cfg: dict, rng=random) -> str:
    """The phrase to type for a challenge: the user's own if set, else a fresh random one."""
    return cfg.get("custom_phrase") or new_phrase(cfg["length"], cfg.get("complex", False), rng)


def _phrase_strength(cfg: dict) -> int:
    """How long the phrase is in characters (a custom phrase counts its non-space characters)."""
    custom = cfg.get("custom_phrase") or ""
    return len(custom.replace(" ", "")) if custom else cfg["length"]


# ---------- what loosens a block ----------

def _temp_end(rule: dict, now: datetime) -> datetime:
    if rule.get("duration_min"):   # not saved yet: the clock starts on save
        return now + timedelta(minutes=rule["duration_min"])
    return datetime.strptime(rule["temp_until"], TIME_FMT) if rule.get("temp_until") else now


def rule_looser(old: dict, new: dict | None, now: datetime) -> bool:
    """Is `new` (same rule type, None = removed) weaker than `old`?"""
    if new is None:
        return True
    kind = old["rule_type"]
    if kind == "temporary":
        return _temp_end(new, now) < _temp_end(old, now) - timedelta(minutes=1)
    if kind == "scheduled":
        return (new.get("schedule") != old.get("schedule")
                or (new.get("allowance_min") or 0) > (old.get("allowance_min") or 0)
                or (allowance_shared(old) and not allowance_shared(new)))
    if kind in ("time_limit", "switch_limit"):
        fields = TIME_LIMIT_FIELDS if kind == "time_limit" else OPEN_LIMIT_FIELDS
        for field in fields.values():
            if old.get(field) is not None and (new.get(field) is None or new[field] > old[field]):
                return True
        if kind == "switch_limit":
            return (new.get("switch_mode") != old.get("switch_mode")
                    or (new.get("visit_gap_min") or 0) > (old.get("visit_gap_min") or 0))
    return False


def rules_looser(old: list[dict], new: list[dict], now: datetime) -> bool:
    by_type = {r["rule_type"]: r for r in new}
    return any(rule_looser(r, by_type.get(r["rule_type"]), now) for r in old)


def _strength(block_type: str | None) -> set[str]:
    flags = block_flags(block_type)
    return flags | {"minimize"} if "close" in flags else flags   # closing is stronger than minimizing


def item_looser(old: dict, new: dict | None, now: datetime) -> bool:
    if new is None:
        return True
    return (newly_disabled(old, new)
            or not set(old["target"].split()) <= set(new["target"].split())
            or (old["item_type"] == "app" and not _strength(old.get("block_type")) <= _strength(new.get("block_type")))
            or (old["item_type"] == "site"
                and not site_flags(old.get("block_type")) <= site_flags(new.get("block_type")))
            or rules_looser(old["rules"], new["rules"], now))


def newly_disabled(old: dict, new: dict) -> bool:
    """Pausing something stops its rules applying, so it goes through the challenge like removing it."""
    return bool(new.get("disabled")) and not old.get("disabled")


def group_looser(old: dict, new: dict | None, now: datetime) -> bool:
    if new is None:
        return True
    if newly_disabled(old, new):
        return True
    if rules_looser(old["rules"], new["rules"], now) or not set(old["members"]) <= set(new["members"]):
        return True
    for member, custom in old["members"].items():   # a member's own version of a group rule
        before = {r["rule_type"]: {**r, **(custom or {}).get(r["rule_type"], {})} for r in old["rules"]}
        after = {r["rule_type"]: {**r, **(new["members"][member] or {}).get(r["rule_type"], {})} for r in new["rules"]}
        if any(rule_looser(r, after.get(t), now) for t, r in before.items()):
            return True
    return False


def draft_changes(saved_items: dict, items: dict, saved_groups: dict, groups: dict, now: datetime) -> list[str]:
    """What in these edits loosens a block, as short descriptions (empty = nothing, save freely)."""
    out = []
    for item_id, old in saved_items.items():
        new = items.get(item_id)
        if item_looser(old, new, now):
            what = "Remove" if new is None else "Disable" if newly_disabled(old, new) else "Loosen"
            out.append(f"{what} {old['display_name']}")
    for group_id, old in saved_groups.items():
        new = groups.get(group_id)
        if group_looser(old, new, now):
            what = "Remove" if new is None else "Disable" if newly_disabled(old, new) else "Loosen"
            out.append(f"{what} group {old['name']}")
    return out


def emergency_looser(old: dict, new: dict) -> bool:
    """Emergency-unlock settings ({"emergency.enabled": "1", ...}): on, longer, more uses, per day instead of week."""
    return ((new["emergency.enabled"] == "1" and old["emergency.enabled"] != "1")
            or int(new["emergency.minutes"]) > int(old["emergency.minutes"])
            or int(new["emergency.uses"]) > int(old["emergency.uses"])
            or (new["emergency.per"] == "day" and old["emergency.per"] != "day"))


def protection_looser(old: dict, new: dict) -> bool:
    """Protection lists: a list switched off, a site allowed anyway, or a hand-made list that's on losing sites."""
    if not set(old["enabled"]) <= set(new["enabled"]) or not set(new["allowed"]) <= set(old["allowed"]):
        return True
    old_manual = {m["key"]: {e["host"] for e in m["entries"]} for m in old.get("manual", [])}
    new_manual = {m["key"]: {e["host"] for e in m.get("entries", [])} for m in new.get("manual", [])}
    return any(key in old_manual and not old_manual[key] <= new_manual.get(key, set())
               for key in old["enabled"])   # a list still on lost some of its sites (or was deleted)


def settings_looser(old: dict, new: dict) -> bool:
    """Anti-Bypass itself: a challenge switched off, a shorter / simpler phrase, the grid dropped, other hours,
    or less waiting before the unlock opens."""
    return (((new.get("wait_min") or 0) < (old.get("wait_min") or 0))
            or (old["phrase"] and (not new["phrase"] or _phrase_strength(new) < _phrase_strength(old)
                                or (old["grid"] and not new["grid"])
                                or (old.get("complex") and not new.get("complex"))))
            or (old["hours"] and (not new["hours"] or new["windows"] != old["windows"])))
