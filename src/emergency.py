"""Emergency unlock: pick one or more sites/apps and unblock them for a while - and/or pause the bedtime and
break alerts for that while.

Unlocking several at once counts as one use (pausing the alerts too, in the same go, is still that one use).
There can be several kinds of emergency at once (TIERS_KEY), each with its own uses per day, week or month (limit
periods, see rules.LimitClock) and its own length - e.g. 3 a week for 5 min and 1 a month for 60 min. A use is
counted for the kind it was spent on (by its id, so a kind keeps its count when it is edited, and one added again
starts afresh). Every unlock is kept in the database (for history / graphs).

An item can be unlocked whether it is blocked right now or not (0.84.7): inside a blocked stretch with allowance
left, or before a limit runs out, the unlock still holds until it ends - the allowance or limit running out
meanwhile doesn't block it. Time used during it keeps counting toward the limits and allowances as always.
Permanently-blocked items (and the sites/apps of a permanently-blocked category) can never be unlocked.
"""
import json
import secrets
from datetime import datetime, timedelta

import applog
from rules import Usage, effective_rules

# the single kind older versions had: only read while TIERS_KEY is missing or "" (it becomes the kind LEGACY_TIER)
DEFAULTS = {"emergency.enabled": "1", "emergency.minutes": "20", "emergency.uses": "3", "emergency.per": "week"}
TIERS_KEY = "emergency.tiers"   # JSON list of kinds: [{"id", "uses", "per", "minutes"}]
LEGACY_TIER = "t1"              # the kind the single setting becomes; old unlocks (no kind stored) are its uses
MAX_TIERS = 5
MAX_USES = 10
MINUTE_OPTIONS = [5, 10, 15, 20, 30, 45, 60]
MAX_MINUTES = max(MINUTE_OPTIONS)
PERIODS = {"day": "today", "week": "this week", "month": "this month"}
HISTORY_DAYS = 40   # the longest month plus a held one (a reset-time change)


def get(db, key: str) -> str:
    return db.get_setting(key, DEFAULTS[key])


def clean(kinds) -> list[dict]:
    """Only sound kinds: an id, 1..MAX_USES uses, a known period, 1..MAX_MINUTES min - the first of a repeated id,
    at most MAX_TIERS. (A hand-edited or broken setting can't give more than the editor could.)"""
    out, seen = [], set()
    for t in kinds if isinstance(kinds, list) else []:
        try:
            tid, uses, per, minutes = t["id"], int(t["uses"]), t["per"], int(t["minutes"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(tid, str) or not tid or tid in seen or uses < 1 or not isinstance(per, str) \
                or per not in PERIODS:
            continue
        seen.add(tid)
        out.append({"id": tid, "uses": min(uses, MAX_USES), "per": per,
                    "minutes": max(1, min(minutes, MAX_MINUTES))})
    return out[:MAX_TIERS]


def tiers(db) -> list[dict]:
    """The kinds of emergency unlock, in their order. Until the kinds are first saved: one kind, LEGACY_TIER, from
    the single setting older versions had (or its defaults, 3 a week for 20 min) - so it works exactly as before."""
    raw = db.get_setting(TIERS_KEY, "")
    if not raw:
        return clean([{"id": LEGACY_TIER, "uses": get(db, "emergency.uses"), "per": get(db, "emergency.per"),
                       "minutes": get(db, "emergency.minutes")}])
    try:
        return clean(json.loads(raw))
    except ValueError:
        return []   # (unreadable: none, the strict side)


def config(db) -> dict:
    """{"enabled", "tiers"}: what antibypass.emergency_looser compares."""
    return {"enabled": get(db, "emergency.enabled") == "1", "tiers": tiers(db)}


def save_tiers(db, kinds: list[dict]):
    db.set_setting(TIERS_KEY, json.dumps(clean(kinds)))


def new_id() -> str:
    """A new kind's id: random, so the id of a removed kind (and its count) never comes back."""
    return secrets.token_hex(4)


def unlock_span(started: datetime, until: datetime) -> str:
    """An emergency unlock as local clock times, "14:05–14:25" (one past midnight shows its real end: "23:50–00:20")."""
    return f"{started:%H:%M}–{until:%H:%M}"


def unlock_line(unlock: dict, names: bool = False) -> str:
    """One line for a use, "Emergency unlock 14:05–14:25", with the sites / apps it freed after it when `names`
    ("Emergency unlock 14:05–14:25 (YouTube, Reddit)")."""
    line = f"Emergency unlock {unlock_span(unlock['started'], unlock['until'])}"
    return f"{line} ({', '.join(unlock['names'])})" if names and unlock.get("names") else line


def unlocks_by_day(db, first) -> dict:
    """{day: [unlock, ...]} of the emergency unlocks that STARTED on each day from `first` (a date) on, earliest first -
    the day an unlock counts for is the one it started on, and `until` is its real end (never ended early)."""
    out: dict = {}
    for u in db.unlocks_since(datetime.combine(first, datetime.min.time())):
        if u["started"].date() >= first:
            out.setdefault(u["started"].date(), []).append(u)
    return out


def tier_of(unlock: dict) -> str:
    """The kind an unlock was spent on (unlocks from before kinds existed: LEGACY_TIER)."""
    return unlock.get("tier") or LEGACY_TIER


def usable(db) -> bool:
    """Turned on, with at least one kind (spent or not): the Emergency unlock button shows."""
    return get(db, "emergency.enabled") == "1" and bool(tiers(db))


def _left(tier: dict, history: list[dict], clock, now: datetime) -> tuple[int, int, datetime]:
    key, reset = clock.period(tier["per"], now)
    # (one dated after now - the clock was set back - counts too: it was spent)
    used = sum(1 for u in history if tier_of(u) == tier["id"]
               and (u["started"] > now or clock.period(tier["per"], u["started"])[0] == key))
    return max(0, tier["uses"] - used), tier["uses"], reset


def left_by_tier(db, now: datetime) -> list[tuple[dict, int, int, datetime]]:
    """(kind, uses left, uses allowed, when its count resets) for each kind, in their order."""
    history, clock = db.unlocks_since(now - timedelta(days=HISTORY_DAYS)), db.limit_clock()
    return [(t, *_left(t, history, clock, now)) for t in tiers(db)]


def used_by_tier(db, now: datetime) -> dict[str, list[dict]]:
    """{kind id: [the unlocks spent on it in its current day / week / month, earliest first]} (the same uses `left_by_tier`
    counts)."""
    history, clock = db.unlocks_since(now - timedelta(days=HISTORY_DAYS)), db.limit_clock()
    out = {}
    for t in tiers(db):
        key = clock.period(t["per"], now)[0]
        out[t["id"]] = [u for u in history if tier_of(u) == t["id"]
                        and (u["started"] > now or clock.period(t["per"], u["started"])[0] == key)]
    return out


def uses_left(db, now: datetime, tier: str | None = None) -> tuple[int, int, datetime | None]:
    """(uses left, uses allowed, when the count resets) of the kind with this id, for its current day / week /
    month. Without one: all kinds together, with the earliest reset (just the kind itself when there is one;
    None as the reset when there are none)."""
    rows = [r for r in left_by_tier(db, now) if tier is None or r[0]["id"] == tier]
    if not rows:
        return 0, 0, None
    return sum(r[1] for r in rows), sum(r[2] for r in rows), min(r[3] for r in rows)


def available(db, now: datetime) -> int:
    """Uses that can be spent right now, all kinds together: 0 when it is turned off in Settings."""
    return uses_left(db, now)[0] if get(db, "emergency.enabled") == "1" else 0


def shortest_left(db, now: datetime) -> str | None:
    """The id of the shortest kind with a use left (the first of equal ones), or None: what a plain "Emergency"
    on the bedtime screen spends - the smallest way out."""
    left = [t for t, n, _allowed, _reset in left_by_tier(db, now) if n > 0]
    return min(left, key=lambda t: t["minutes"])["id"] if left else None


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
    trial = Usage(usage.data, usage.clock, {**usage.unlocks, **{f"item:{i['id']}": far for i in candidates}},
                  usage.paused)
    held = {b["item"]["id"] for b in db.blocks(now, trial, items, groups)}
    blocked = {b["item"]["id"] for b in db.blocks(now, usage, items, groups)}
    free = sorted((i for i in candidates if i["id"] not in held), key=lambda i: i["display_name"].lower())
    return [i for i in free if i["id"] in blocked], [i for i in free if i["id"] not in blocked]


def unlock(db, items: list[dict], now: datetime, alerts: bool = False, *, tier: str | None = None) -> datetime:
    """Unlock the items and/or pause the bedtime and break alerts: one use of the kind with this id, for its
    length. Returns until when. No kind given is only for when there is just one. Raises ValueError if it's off,
    nothing was chosen, the kind is gone or has no uses left, or something chosen can't be unlocked."""
    if get(db, "emergency.enabled") != "1":
        raise ValueError("Emergency unlock is turned off (Settings).")
    if not items and not alerts:
        raise ValueError("Tick at least one.")
    kinds = left_by_tier(db, now)
    if tier is None:
        if not any(left for _t, left, _a, _r in kinds):
            raise ValueError("No emergency unlocks left.")
        if len(kinds) != 1:
            raise ValueError("Pick how long.")
        chosen, left = kinds[0][:2]
    else:
        found = next(((t, left) for t, left, _a, _r in kinds if t["id"] == tier), None)
        if found is None:
            raise ValueError("That kind is gone - pick another.")
        chosen, left = found
    if left <= 0:
        raise ValueError("No emergency unlocks left." if len(kinds) == 1 else
                         f"No {chosen['minutes']}-min emergency unlocks left {PERIODS[chosen['per']]}.")
    if items:   # (the screen only offers what it may unlock; this is the check that counts)
        allowed = {i["id"] for part in choices(db, now) for i in part}
        never = {i["id"] for i in db.list_items() if not i["disabled"]} - allowed
        if any(i["id"] in never for i in items):
            raise ValueError("Permanently-blocked items can't be emergency-unlocked.")
    until = now + timedelta(minutes=chosen["minutes"])
    db.add_unlock([i["id"] for i in items], [i["display_name"] for i in items], now, until, alerts=alerts,
                  tier=chosen["id"])
    what = ", ".join(i["display_name"] for i in items[:3]) + (f" and {len(items) - 3} more" if len(items) > 3 else "")
    applog.record("emergency", f"Emergency unlock used for {chosen['minutes']} min until {until:%H:%M}: "
                  + (what if items else "") + (" + bedtime and break alerts paused" if alerts and items else
                                               "bedtime and break alerts paused" if alerts else ""), db)
    return until


def alerts_paused_until(db, now: datetime) -> datetime | None:
    """While an emergency unlock that paused the alerts runs: its end (the latest, if several), else None."""
    ends = [u["until"] for u in db.unlocks_since(now) if u["alerts"] and u["started"] <= now]
    return max(ends) if ends else None
