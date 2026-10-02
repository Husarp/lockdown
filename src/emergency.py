"""Emergency unlock: pick one or more sites/apps and unblock them for a while - and/or pause the bedtime and
break alerts for that while.

Unlocking several at once counts as one use (pausing the alerts too, in the same go, is still that one use).
Uses are limited per day or per week (limit periods, see rules.LimitClock). Every unlock is kept in the database
(for history / graphs).

An item can be unlocked whether it is blocked right now or not (0.84.7): inside a blocked stretch with allowance
left, or before a limit runs out, the unlock still holds until it ends - the allowance or limit running out
meanwhile doesn't block it. Time used during it keeps counting toward the limits and allowances as always.
Permanently-blocked items (and the sites/apps of a permanently-blocked category) can never be unlocked.
"""
from datetime import datetime, timedelta

from rules import Usage, effective_rules

DEFAULTS = {"emergency.enabled": "1", "emergency.minutes": "20", "emergency.uses": "3", "emergency.per": "week"}
MINUTE_OPTIONS = [5, 10, 15, 20, 30, 45, 60]
PERIODS = {"day": "today", "week": "this week"}
HISTORY_DAYS = 40


def get(db, key: str) -> str:
    return db.get_setting(key, DEFAULTS[key])


def uses_left(db, now: datetime) -> tuple[int, int, datetime]:
    """(uses left, uses allowed, when the count resets) for the current day / week."""
    per, allowed = get(db, "emergency.per"), int(get(db, "emergency.uses"))
    clock = db.limit_clock()
    key, reset = clock.period(per, now)
    used = sum(1 for u in db.unlocks_since(now - timedelta(days=HISTORY_DAYS))
               if u["started"] <= now and clock.period(per, u["started"])[0] == key)
    return max(0, allowed - used), allowed, reset


def available(db, now: datetime) -> int:
    """Uses that can be spent right now: 0 when it is turned off in Settings."""
    return uses_left(db, now)[0] if get(db, "emergency.enabled") == "1" else 0


def choices(db, now: datetime) -> tuple[list[dict], list[dict]]:
    """(blocked now, not blocked now): every item an emergency unlock can actually free, by name. Not paused
    items (nothing applies to them), not permanently-blocked ones, and not the sites/apps of a permanently-blocked
    category - the emergency unlock is for limits, hours, modes and groups, never for what you blocked for good."""
    usage = db.usage_lookup(now)
    groups = db.list_groups()
    items = [i for i in db.list_items() if not i["disabled"]]
    candidates = [i for i in items if not any(r["rule_type"] == "permanent" for r in effective_rules(i, groups))]
    # what would still be blocked with every candidate unlocked: held by something the unlock doesn't lift
    far = now + timedelta(days=3650)
    trial = Usage(usage.data, usage.clock, {**usage.unlocks, **{f"item:{i['id']}": far for i in candidates}})
    held = {b["item"]["id"] for b in db.blocks(now, trial, items, groups)}
    blocked = {b["item"]["id"] for b in db.blocks(now, usage, items, groups)}
    free = sorted((i for i in candidates if i["id"] not in held), key=lambda i: i["display_name"].lower())
    return [i for i in free if i["id"] in blocked], [i for i in free if i["id"] not in blocked]


def unlock(db, items: list[dict], now: datetime, alerts: bool = False) -> datetime:
    """Unlock the items and/or pause the bedtime and break alerts (one use). Returns until when. Raises
    ValueError if it's off, nothing was chosen, something chosen can't be unlocked, or no uses are left."""
    if get(db, "emergency.enabled") != "1":
        raise ValueError("Emergency unlock is turned off (Settings).")
    if not items and not alerts:
        raise ValueError("Tick at least one.")
    if uses_left(db, now)[0] <= 0:
        raise ValueError("No emergency unlocks left.")
    if items:   # (the screen only offers what it may unlock; this is the check that counts)
        allowed = {i["id"] for part in choices(db, now) for i in part}
        never = {i["id"] for i in db.list_items() if not i["disabled"]} - allowed
        if any(i["id"] in never for i in items):
            raise ValueError("Permanently-blocked items can't be emergency-unlocked.")
    until = now + timedelta(minutes=int(get(db, "emergency.minutes")))
    db.add_unlock([i["id"] for i in items], [i["display_name"] for i in items], now, until, alerts=alerts)
    return until


def alerts_paused_until(db, now: datetime) -> datetime | None:
    """While an emergency unlock that paused the alerts runs: its end (the latest, if several), else None."""
    ends = [u["until"] for u in db.unlocks_since(now) if u["alerts"] and u["started"] <= now]
    return max(ends) if ends else None
