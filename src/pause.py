"""Pause my blocks (0.84.8): every block from your own list stops for a while you choose, then comes back by itself.

What it lifts: every site and app blocked by your items, groups, modes and categories - hours, time limits,
opening limits, temporary and permanent blocks alike. What it never lifts: the protection lists (the DNS filter),
the blocked words, SafeSearch / YouTube Restricted. Activity during an authorized pause spends no quota.

Starting it is loosening, so it needs the Anti-Bypass challenge (LockdownApp.pause_blocks, through app.guard);
ending it early is free. It is not the whole-app off switch (antibypass.OFF_KEY - that stops everything, the
protection lists too, until you switch back on) and not an emergency unlock (those pick items and spend a use).

Kept in one setting (KEY), in trusted time like the emergency unlocks: it survives a restart, and the service and
the tray app both read it (db.usage_lookup puts it in the Usage every rule check gets), so it ends on the dot for
both. It counts only from `started` to `until`: a clock that reads earlier than when it began (put back) does not
stretch it - and a pause can never run longer than MAX_SPAN, whatever is written.
Optionally it silences every notification too (warnings, reminders, bedtime and break alerts and screens,
pop-ups, Windows notifications) for the same while.
"""
import json
from datetime import datetime, timedelta

KEY = "pause.blocks"   # JSON {"started", "until" (trusted local time), "silent": bool}; "" = not paused
REST_OF_DAY = "Rest of the day"
DURATIONS = {"30 min": 30, "1 h": 60, "2 h": 120, "4 h": 240, REST_OF_DAY: None}   # label -> minutes (None: the day)
MAX_SPAN = timedelta(days=1)   # the longest a pause can run ("Rest of the day" ends at the next reset time)


def _load(text: str | None) -> dict | None:
    try:
        raw = json.loads(text or "null")
        started, until = (datetime.fromisoformat(raw[key]) for key in ("started", "until"))
        if started.tzinfo is None and until.tzinfo is None and timedelta(0) < until - started <= MAX_SPAN:
            return {"started": started, "until": until, "silent": bool(raw.get("silent"))}
    except (ValueError, TypeError, KeyError):
        return None


def state(db, now: datetime) -> dict | None:
    """{"started", "until", "silent"} while a pause is running at `now`, else None."""
    parsed = getattr(db, "parsed", None)
    p = parsed(KEY, _load, "") if parsed else _load(db.get_setting(KEY, ""))
    if p and p["started"] <= now < p["until"]:
        return p
    return None


def until(db, now: datetime) -> datetime | None:
    p = state(db, now)
    return p["until"] if p else None


def silent_until(db, now: datetime) -> datetime | None:
    """While a pause that silences the notifications is running: its end, else None."""
    p = state(db, now)
    return p["until"] if p and p["silent"] else None


def end_for(db, minutes: int | None, now: datetime) -> datetime:
    """When a pause started now ends: after `minutes`, or (None) at the next reset time (Settings' "limits reset
    at") - always within a day. Not the end of the running limit day: changing the reset time (no challenge)
    stretches that day up to two days (rules.LimitClock), which would have made "Rest of the day" a two-day pause."""
    if minutes is None:
        reset = datetime.combine(now.date(), db.limit_clock().time)
        return reset if reset > now else reset + timedelta(days=1)
    return now + timedelta(minutes=minutes)


def start(db, now: datetime, minutes: int | None, silent: bool) -> datetime:
    """Pause the blocks (the caller has had the challenge passed). Replaces a pause already running."""
    end = end_for(db, minutes, now)
    db.change_pause(now, json.dumps({"started": now.isoformat(" "), "until": end.isoformat(" "),
                                     "silent": bool(silent)}))
    return end


def resume(db, now: datetime | None = None):
    """Blocking back on now - always free."""
    from trusted_time import now_from_db
    db.change_pause(now if now is not None else now_from_db(db), "")


def label(minutes: int | None) -> str:
    return next(k for k, v in DURATIONS.items() if v == minutes)
