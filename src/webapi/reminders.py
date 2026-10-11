"""Api for the Reminders page (BLUEPRINT 3.8): sleep, breaks and your own reminders.

The page sends what is in its boxes as TEXT ("30s", "5 min", "1h30", "23:00") and Python parses it with the same
functions the old window used (reminders.parse_interval / parse_minutes / parse_seconds / parse_hhmm), so the rules
(5 s at least, 24 h at most, the Dismiss wait at most 600 s) can't differ between the two windows.

Everything that saves is classified `loosen`: whether a change weakens an IMPORTANT ("guarded") reminder is decided
here, in Python, by comparing what is stored with what would be stored (reminders.loosens_reminder: turning it off,
removing the flag, a shorter Dismiss wait; plus _weaker below: a longer interval, fewer days or times, a lower daily
limit ...). If it does, the change waits under a challenge ticket (ctx.guard.request) and the page only gets the
ticket; otherwise it is saved at once. The page never says "confirmed". Deleting an important reminder is the same.
"""
import uuid
from datetime import datetime, timedelta

import modes
import reminders
from rules import TIME_FMT, parse_hhmm

from . import core
from .core import ApiError

MAX_TEXT = 300               # a reminder's text / the messages
MAX_QUOTES = 3000            # your own quotes (all lines)
MAX_REMINDERS = 200
SNOOZES = (1, 5, 10, 15, 30)             # minutes; what the editor offers
CHECKS = (0, 5, 10, 15, 30)
MAX_SNOOZES = 5
MAX_PER_DAY = 12
MAX_TIERS = 12
BREAK_LENGTHS = (2, 5, 10, 15)
BREAK_SNOOZES = (1, 2, 3, 5)             # "Snoozes first"
MAX_HEADS_UP_MIN = 12 * 60
KINDS = ("interval", "times", "random")
NAMED = {"sleep": reminders.SLEEP_KEY, "break": reminders.BREAK_KEY}


# ---------------------------------------------------------------- small helpers

def _text(value, label, limit=MAX_TEXT, required=False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ApiError(f"{label}: text expected.")
    value = value.strip()
    if required and not value:
        raise ApiError("Write what to remind you of.")
    if len(value) > limit:
        raise ApiError(f"{label}: at most {limit} characters, please.")
    return value


def _flag(value, label) -> bool:
    if not isinstance(value, bool):
        raise ApiError(f"{label}: yes or no expected.")
    return value


def _whole(value, label, allowed=None, low=None, high=None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise ApiError(f"{label}: a whole number expected.")
    value = int(value)
    if allowed is not None and value not in allowed:
        raise ApiError(f"{label}: pick one of {', '.join(str(a) for a in allowed)}.")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ApiError(f"{label}: between {low} and {high}, please.")
    return value


def _clock(value, error="Write times like 23:00.") -> str:
    """'9:5' / '09:05' -> '09:05' (rules.parse_hhmm decides what a time is)."""
    if not isinstance(value, str):
        raise ApiError(error)
    try:
        return f"{parse_hhmm(value):%H:%M}"
    except ValueError:
        raise ApiError(error) from None


def _stored(db, key, default):
    return reminders.load(db, key, default)


def _customs(db) -> list:
    """The stored list as it is (not merged with defaults, so saving writes back what was there)."""
    saved = reminders.load(db, reminders.CUSTOM_KEY, [])
    return [r for r in saved if isinstance(r, dict) and r.get("id")]


def _merged(r: dict) -> dict:
    return {**reminders.DEFAULT_CUSTOM, **r}


def _mode_ids(db) -> set:
    return {m["id"] for m in modes.load(db)}


# ---------------------------------------------------------------- is this change weaker? (decided here, not in JS)

def _secs(r, key="every_sec"):
    try:
        return int(r.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _minute_of(hhmm: str) -> int:
    t = parse_hhmm(hhmm)
    return t.hour * 60 + t.minute


def _night_interval(s: dict, minute: int):
    """Seconds between two bedtime screens at this minute of the day, or None when the night doesn't cover it
    (bedtime up to wake). Asks the engine itself (reminders.Engine._sleep_interval), so the rule can't drift."""
    bed_min, wake_min = _minute_of(s["bedtime"]), _minute_of(s["wake"])
    span = (wake_min - bed_min) % 1440 or 1440
    off = (minute - bed_min) % 1440
    if off >= span:
        return None
    bed = datetime(2000, 1, 1, bed_min // 60, bed_min % 60)
    return reminders.Engine._sleep_interval(None, s, bed, bed + timedelta(minutes=off))


def _sleep_weaker(old: dict, new: dict) -> bool:
    """Weaker when at ANY minute of the old night the new settings are silent or come back less often (a step that
    starts later, a later bedtime, an earlier wake, a longer interval); an extra, earlier step is stricter."""
    if float(new.get("before") or 0) < float(old.get("before") or 0):
        return True
    if old.get("mode") and not new.get("mode"):
        return True
    # the interval only changes where a step starts or the night begins / ends - look at exactly those minutes
    marks = {_minute_of(x[k]) for x in (old, new) for k in ("bedtime", "wake")}
    marks |= {_minute_of(t["from"]) for x in (old, new) for t in reminders.tiers_of(x)}
    for m in marks:
        was = _night_interval(old, m)
        if was is None:
            continue
        now = _night_interval(new, m)
        if now is None or now > was:
            return True
    return False


def _active_minutes(r: dict) -> tuple:
    """(first, last) minute of the day a custom reminder can fire in, for 'only between' / a random time."""
    lo, hi = _minute_of(r["window"][0]), _minute_of(r["window"][1])
    return (lo, max(lo, hi - 1)) if r["kind"] == "random" else (lo, hi)


def _hours_narrower(o: dict, n: dict) -> bool:
    """True when the new 'only between' hours cover less of the day than the old ones (every day = no hours)."""
    if not o.get("hours"):
        return bool(n.get("hours"))
    if not n.get("hours"):
        return False
    olo, ohi = _minute_of(o["window"][0]), _minute_of(o["window"][1])
    nlo, nhi = _minute_of(n["window"][0]), _minute_of(n["window"][1])
    o_set = set(range(olo, ohi)) if olo < ohi else set(range(olo, 1440)) | set(range(0, ohi))
    n_set = set(range(nlo, nhi)) if nlo < nhi else set(range(nlo, 1440)) | set(range(0, nhi))
    return not o_set <= n_set


def _weaker(old: dict, new: dict, kind: str) -> bool:
    """True when an IMPORTANT reminder is changed so that it asks for less. Only an important one counts (a casual
    reminder can be edited freely). `kind` is "sleep", "break" or "custom". Turning something stricter ON (Strict
    break, a daily limit ...) is always free."""
    if not old.get("guarded"):
        return False
    if kind == "sleep":
        return _sleep_weaker({**reminders.DEFAULT_SLEEP, **old}, {**reminders.DEFAULT_SLEEP, **new})
    if kind == "break":
        o, n = {**reminders.DEFAULT_BREAK, **old}, {**reminders.DEFAULT_BREAK, **new}
        return (_secs(n) > _secs(o) or _secs(n, "snooze_sec") > _secs(o, "snooze_sec")
                or int(n["length"]) < int(o["length"]) or (o["strict"] and not n["strict"])
                or (o["twenty"] and not n["twenty"])
                or (o["strict"] and n["strict"] and int(n["max_snooze"]) > int(o["max_snooze"])))
    o, n = _merged(old), _merged(new)
    if o["kind"] != n["kind"]:                              # (can't be compared cheaply: ask)
        return True
    if not set(o["days"]) <= set(n["days"]):                # (a day dropped)
        return True
    if n["snooze"] > o["snooze"] or int(n["max_snooze"]) > int(o["max_snooze"]):
        return True
    if n["kind"] == "interval" and (_secs(n) > _secs(o) or _hours_narrower(o, n)):
        return True
    if n["kind"] == "times" and not set(o["times"]) <= set(n["times"]):
        return True
    if n["kind"] == "random":
        # once a day either way; a wider (or equal) window of possible times asks just as much, a narrower or
        # moved one is a different promise
        (olo, ohi), (nlo, nhi) = _active_minutes(o), _active_minutes(n)
        if nlo > olo or nhi < ohi:
            return True
    return bool(n["per_day"]) and (not o["per_day"] or n["per_day"] < o["per_day"])


def _loosens(old: dict, new: dict, kind: str) -> bool:
    return reminders.loosens_reminder(old, new) or _weaker(old, new, kind)


def _changes_text(name, old, new, kind) -> list:
    out = []
    if reminders.loosens_reminder(old, {**new, "dismiss_wait": reminders.dismiss_wait(old)}):
        out.append(f"Turn off / un-flag the important {name}")
    if reminders.dismiss_wait(new) < reminders.dismiss_wait(old):
        out.append("Shorten the wait before Dismiss on the bedtime screen")
    if _weaker(old, new, kind):
        out.append(f"Make the important {name} ask for less")
    return out or [f"Change the important {name}"]


# ---------------------------------------------------------------- reading

def _counts(db, ids, today):
    """Done / not done per reminder over the last 7 days: ONE grouped query (reminders.counts_many)."""
    return reminders.counts_many(db, ids, today - timedelta(days=7)) if ids else {}


def _breaks_today(db, today) -> int:
    """'Breaks today: N' - taken + away since midnight (today is always inside the minute detail)."""
    rows = db.conn.execute("SELECT result, COUNT(*) FROM reminder_log WHERE what = 'break' AND timestamp >= ? "
                           "GROUP BY result", (today.strftime(TIME_FMT),)).fetchall()
    got = {result: n for result, n in rows}
    return got.get("taken", 0) + got.get("away", 0)


def _sleep_view(s: dict) -> dict:
    return {"on": bool(s["on"]), "guarded": bool(s.get("guarded")), "bedtime": s["bedtime"], "wake": s["wake"],
            "before": reminders.minutes_text(s["before"]),
            "tiers": [{"every": reminders.seconds_text(t.get("every_sec", 300)), "after": t.get("from", "21:00")}
                      for t in reminders.tiers_of(s)],
            "dismiss_wait": reminders.dismiss_wait(s), "warn_text": s.get("warn_text") or "",
            "text": s.get("text") or "", "mode": s.get("mode") or ""}


def _break_view(b: dict, today_count: int) -> dict:
    return {"on": bool(b["on"]), "guarded": bool(b.get("guarded")), "every": reminders.seconds_text(b["every_sec"]),
            "length": b["length"], "snooze": reminders.seconds_text(b.get("snooze_sec", 300)),
            "strict": bool(b.get("strict")), "max_snooze": int(b.get("max_snooze", 2)),
            "twenty": bool(b.get("twenty")), "text": b.get("text") or "", "twenty_text": b.get("twenty_text") or "",
            "today": today_count}


def _custom_view(r: dict, counts: dict) -> dict:
    r = _merged(r)
    c = counts.get(r["id"], {})
    stats = f"done {c.get('done', 0)}x in 7 days" + (f", 'not done' {c['not done']}x" if c.get("not done") else "")
    return {"id": r["id"], "on": bool(r["on"]), "text": r["text"], "guarded": bool(r.get("guarded")),
            "kind": r["kind"], "every": reminders.seconds_text(r["every_sec"]), "times": list(r["times"]),
            "days": list(r["days"]), "window": list(r["window"]), "hours": bool(r.get("hours")),
            "snooze": r["snooze"], "max_snooze": r["max_snooze"], "check": r["check"],
            "per_day": r.get("per_day") or 0, "packs": list(r["packs"]), "quotes": r["quotes"],
            "schedule": reminders.schedule_text(r), "stats": stats}


@core.read
def reminders_get(ctx):
    """Everything the page shows, in one call and one counts query: {sleep, break, custom, modes, defaults}."""
    db = ctx.db
    today = datetime.combine(ctx.now().date(), datetime.min.time())
    sleep = _stored(db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    brk = _stored(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)
    items = _customs(db)
    counts = _counts(db, [r["id"] for r in items], today)
    return {"sleep": _sleep_view(sleep), "break": _break_view(brk, _breaks_today(db, today)),
            "custom": [_custom_view(r, counts) for r in items], "modes": modes_brief(ctx),
            "defaults": {"sleep_warn_text": reminders.SLEEP_WARN_TEXT, "sleep_text": reminders.SLEEP_TEXT,
                         "break_text": reminders.BREAK_TEXT, "twenty_text": reminders.TWENTY_TEXT},
            "packs": list(reminders.PACKS), "max_dismiss_wait": reminders.MAX_DISMISS_WAIT}


@core.read
def modes_brief(ctx):
    """[{id, name}] of the modes, for "Turn on [mode] at bedtime"."""
    return [{"id": m["id"], "name": m["name"]} for m in modes.load(ctx.db)]


# ---------------------------------------------------------------- saving

def _save_or_ask(ctx, old, new, kind, name, apply):
    """Free change -> apply now. Weakening an important reminder -> keep it under a challenge ticket."""
    if not _loosens(old, new, kind):
        return apply()
    return ctx.guard.request(_changes_text(name, old, new, kind), apply)


@core.loosen
def reminder_save_sleep(ctx, cfg):
    """Save the Sleep card (cfg = what is in its boxes, as text). Weakening an important one needs the challenge."""
    if not isinstance(cfg, dict):
        raise ApiError("Bad request.")
    db = ctx.db
    bedtime, wake = _clock(cfg.get("bedtime")), _clock(cfg.get("wake"))
    try:
        before = reminders.parse_minutes(str(cfg.get("before") or "off"), most=MAX_HEADS_UP_MIN)
    except ValueError as e:
        raise ApiError(f"Heads-up: {e}") from None
    tiers_in = cfg.get("tiers") or []
    if not isinstance(tiers_in, list) or len(tiers_in) > MAX_TIERS or not all(isinstance(t, dict) for t in tiers_in):
        raise ApiError("Steps need how often (5 min, 30s - at least 5s) and a time like 21:00.")
    tiers = []
    for t in tiers_in:
        try:
            every = reminders.parse_interval(str(t.get("every", "")))
            after = _clock(t.get("after"), error="")
        except (ValueError, ApiError):
            raise ApiError("Steps need how often (5 min, 30s - at least 5s) and a time like 21:00.") from None
        tiers.append({"from": after, "every_sec": every})
    tiers.sort(key=lambda t: parse_hhmm(t["from"]))
    try:
        wait = reminders.parse_seconds(str(cfg.get("dismiss_wait", 0)))
    except ValueError as e:
        raise ApiError(str(e)) from None
    mode = cfg.get("mode") or ""
    if not isinstance(mode, str) or (mode and mode not in _mode_ids(db)):
        raise ApiError("That mode doesn't exist any more.")
    old = _stored(db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    new = {**old, "on": _flag(cfg.get("on"), "Remind me to go to bed"), "bedtime": bedtime, "wake": wake,
           "before": before, "tiers": tiers, "warn_text": _text(cfg.get("warn_text"), "Heads-up says"),
           "text": _text(cfg.get("text"), "Bedtime says"), "guarded": _flag(cfg.get("guarded"), "Important"),
           "dismiss_wait": wait, "mode": mode}
    new.setdefault("repeat_sec", 300)

    def apply():
        reminders.save(ctx.db, reminders.SLEEP_KEY, new)
        return {"saved": True}
    return _save_or_ask(ctx, old, new, "sleep", "bedtime reminder", apply)


@core.loosen
def reminder_save_break(ctx, cfg):
    """Save the Breaks card (cfg = what is in its boxes, as text). Weakening an important one needs the challenge."""
    if not isinstance(cfg, dict):
        raise ApiError("Bad request.")
    db = ctx.db
    try:
        every = reminders.parse_interval(str(cfg.get("every", "")))
        snooze = reminders.parse_interval(str(cfg.get("snooze", "")))
    except ValueError as e:
        raise ApiError(str(e)) from None
    old = _stored(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)
    new = {**old, "on": _flag(cfg.get("on"), "Remind me to take breaks"), "every_sec": every,
           "length": _whole(cfg.get("length"), "Break length", allowed=BREAK_LENGTHS),
           "strict": _flag(cfg.get("strict"), "Strict break"), "snooze_sec": snooze,
           "max_snooze": _whole(cfg.get("max_snooze"), "Snoozes first", allowed=BREAK_SNOOZES),
           "twenty": _flag(cfg.get("twenty"), "20-20-20"), "text": _text(cfg.get("text"), "It says"),
           "twenty_text": _text(cfg.get("twenty_text"), "It says"), "guarded": _flag(cfg.get("guarded"), "Important")}
    new.pop("every", None)
    new.pop("snooze", None)           # (minutes keys from an old copy would win over the seconds ones)

    def apply():
        reminders.save(ctx.db, reminders.BREAK_KEY, new)
        return {"saved": True}
    return _save_or_ask(ctx, old, new, "break", "break reminder", apply)


def _custom_from_page(r: dict, editing: dict | None) -> dict:
    """Validate the editor's fields (same messages as the old window) and build the stored reminder."""
    text = _text(r.get("text"), "Text", required=True)
    kind = r.get("kind")
    if kind not in KINDS:
        raise ApiError("Pick Interval, Times or Random.")
    default = editing or reminders.DEFAULT_CUSTOM
    try:
        every = reminders.parse_interval(str(r.get("every", "")))
    except ValueError as e:
        if kind == "interval":
            raise ApiError(f"How often: {e}") from None
        every = default["every_sec"]                      # (not used by this kind)
    error = "Write times like 09:00 (several: 09:00, 13:00)."
    times_in = r.get("times") or []
    if isinstance(times_in, str):
        times_in = times_in.replace(";", ",").split(",")
    if not isinstance(times_in, list) or len(times_in) > 48:
        raise ApiError(error)
    times = sorted({_clock(t, error) for t in times_in if isinstance(t, str) and t.strip()})
    window_in = r.get("window")
    if not isinstance(window_in, list) or len(window_in) != 2:
        raise ApiError(error)
    window = [_clock(window_in[0], error), _clock(window_in[1], error)]
    days_in = r.get("days")
    if not isinstance(days_in, list) or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6
                                                for d in days_in):
        raise ApiError("Pick at least one day.")
    days = sorted(set(days_in))
    if not days:
        raise ApiError("Pick at least one day.")
    if kind == "times" and not times:
        raise ApiError("Pick at least one time.")
    if kind == "random" and window[0] >= window[1]:
        raise ApiError("The random window must start before it ends.")
    packs = r.get("packs") or []
    if not isinstance(packs, list) or any(p not in reminders.PACKS for p in packs):
        raise ApiError("That quote pack doesn't exist.")
    return {"id": editing["id"] if editing else uuid.uuid4().hex[:8], "on": editing["on"] if editing else True,
            "text": text, "kind": kind, "every_sec": every, "times": times or ["12:00"], "days": days,
            "window": window, "snooze": _whole(r.get("snooze"), "Snooze", allowed=SNOOZES),
            "max_snooze": _whole(r.get("max_snooze"), "At most", low=0, high=MAX_SNOOZES),
            "check": _whole(r.get("check"), "Ask 'did you actually do it?'", allowed=CHECKS),
            "packs": [p for p in reminders.PACKS if p in packs],
            "quotes": _text(r.get("quotes"), "Your own quotes", MAX_QUOTES),
            "hours": _flag(r.get("hours", False), "Only between") and kind == "interval",
            "per_day": _whole(r.get("per_day", 0), "Stop for the day after", low=0, high=MAX_PER_DAY),
            "guarded": _flag(r.get("guarded", False), "Important")}


@core.loosen
def reminder_save_custom(ctx, r):
    """Add (r.id empty) or change one of your reminders. A new one gets its id here; editing keeps `on`. Removing
    "important" or weakening the schedule of an important one needs the challenge."""
    if not isinstance(r, dict):
        raise ApiError("Bad request.")
    db = ctx.db
    items = _customs(db)
    rid = r.get("id")
    editing = None
    if rid:
        editing = next((_merged(x) for x in items if x["id"] == rid), None)
        if editing is None:
            raise ApiError("That reminder was deleted - close this and start again.")
    elif len(items) >= MAX_REMINDERS:
        raise ApiError("That's a lot of reminders already.")
    new = _custom_from_page(r, editing)

    def apply():
        current = _customs(ctx.db)
        if editing:
            if not any(x["id"] == new["id"] for x in current):
                raise ApiError("That reminder was deleted - close this and start again.")
            current = [new if x["id"] == new["id"] else x for x in current]
        else:
            current.append(new)
        reminders.save(ctx.db, reminders.CUSTOM_KEY, current)
        return {"saved": True, "id": new["id"]}
    if editing is None or not _loosens(editing, new, "custom"):
        return apply()
    return ctx.guard.request(_changes_text(f'"{editing["text"]}"', editing, new, "custom"), apply)


@core.loosen
def reminder_set_on(ctx, rid, on):
    """The switch on a row, or on the Sleep ("sleep") / Breaks ("break") card. Turning an important one off asks."""
    if not isinstance(rid, str):
        raise ApiError("Bad request.")
    on = _flag(on, "Switch")
    db = ctx.db
    if rid in NAMED:
        key = NAMED[rid]
        old = _stored(db, key, reminders.DEFAULT_SLEEP if rid == "sleep" else reminders.DEFAULT_BREAK)
        name, kind = ("bedtime reminder" if rid == "sleep" else "break reminder"), rid
    else:
        key, kind = reminders.CUSTOM_KEY, "custom"
        old = next((_merged(x) for x in _customs(db) if x["id"] == rid), None)
        if old is None:
            raise ApiError("That reminder was deleted.")
        name = f'reminder "{old["text"]}"'

    def apply():
        d = ctx.db
        if key == reminders.CUSTOM_KEY:
            reminders.save(d, key, [{**x, "on": on} if x["id"] == rid else x for x in _customs(d)])
        else:
            reminders.save(d, key, {**_stored(d, key, old), "on": on})
        return {"saved": True}
    if not reminders.loosens_reminder(old, {**old, "on": on}):
        return apply()
    return ctx.guard.request([f"Turn off the important {name}"], apply)


@core.loosen
def reminder_delete(ctx, rid):
    """Delete one of your reminders. An important one asks for the challenge first."""
    if not isinstance(rid, str):
        raise ApiError("Bad request.")
    old = next((_merged(x) for x in _customs(ctx.db) if x["id"] == rid), None)
    if old is None:
        return {"saved": True}                               # (already gone)

    def apply():
        reminders.save(ctx.db, reminders.CUSTOM_KEY, [x for x in _customs(ctx.db) if x["id"] != rid])
        return {"saved": True}
    if not old.get("guarded"):
        return apply()
    return ctx.guard.request([f'Delete the important reminder "{old["text"]}"'], apply)
