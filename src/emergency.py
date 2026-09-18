"""Emergency unlock: pick one or more blocked sites/apps and unblock them for a while.

Unlocking several at once counts as one use. Uses are limited per day or per week (limit periods, see
rules.LimitClock). Every unlock is kept in the database (for history / graphs).
"""
from datetime import datetime, timedelta

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


def unlock(db, items: list[dict], now: datetime) -> datetime:
    """Unlock the items (one use). Returns until when. Raises ValueError if it's off or no uses are left."""
    if get(db, "emergency.enabled") != "1":
        raise ValueError("Emergency unlock is turned off (Settings).")
    if not items:
        raise ValueError("Tick at least one.")
    if uses_left(db, now)[0] <= 0:
        raise ValueError("No emergency unlocks left.")
    until = now + timedelta(minutes=int(get(db, "emergency.minutes")))
    db.add_unlock([i["id"] for i in items], [i["display_name"] for i in items], now, until)
    return until
