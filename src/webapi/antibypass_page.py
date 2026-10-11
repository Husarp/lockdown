"""Api for the Anti-Bypass page (BLUEPRINT 3.4): the challenges, "Pause my blocks" and "Turn Lockdown off".

The page is untrusted, so nothing here believes it. What the page sends is a few plain values (a switch, a length, a
phrase, a wait as TEXT like "1h30", the hours windows); Python validates every one, builds the new settings from the
STORED ones plus only those values (the unlock state and every other key can't be smuggled in), and decides - with the
same functions the old window used (antibypass.settings_looser, pause.end_for ...) - whether it loosens anything:

- weaker Anti-Bypass (a challenge off, a shorter / simpler phrase, no grid, no wait, other hours): waits under a
  challenge ticket (ctx.guard.request) and the page only gets the ticket;
- a stricter change of the challenge: needs a yes first, because a phrase you can't reproduce (or hours you can't
  reach) would lock you out of ever loosening a block. The page asks, then sends `confirmed=True`. That flag ONLY
  answers "are you sure?" for a change that is not loosening - the loosening check is made first, every time, and
  a loosening change gets its ticket whatever the flag says;
- everything else (and a longer phrase) is saved at once.

Starting "Pause my blocks" and switching Lockdown off are loosening too (the second one even asks while no challenge is
turned on, `force`); ending a pause and switching Lockdown on are free (`pause_resume` and `antibypass_switch_on`
live in webapi/shell.py, as the top bar uses them).
"""
import antibypass
import modes
import pause
import reminders
from rules import days_text, parse_hhmm, window_until

from . import core
from .core import ApiError

MAX_PHRASE_CHARS = 2000              # the longest own phrase (as long as the longest random one)
MAX_WINDOWS = 14
SAVE_KEYS = ("phrase", "length", "grid", "complex", "custom_phrase", "hours", "windows", "wait")
OFF_CHANGE = "Turn Lockdown off entirely (no blocking, no limits, no reminders, until you turn it back on)"


def _iso(value):
    return value.isoformat(timespec="seconds") if value else None


@core.internal
def hours_text(cfg: dict) -> str:
    return ", ".join(f"{days_text(w['days'])} {w['start']}-{w['end']}" for w in cfg["windows"])


@core.internal
def describe(cfg: dict) -> str:
    """What loosening a block needs, in one line (the same words as the old window's confirm)."""
    parts = []
    if cfg["phrase"]:
        which = "your own phrase" if cfg.get("custom_phrase") else (
            f"a {cfg['length']}-character phrase" + (" (with numbers & capitals)" if cfg.get("complex") else ""))
        parts.append(f"typing {which}" + (" in a 3×3 grid" if cfg["grid"] else ""))
    if cfg["hours"]:
        parts.append(f"only {hours_text(cfg)}")
    return "Loosening a block needs: " + " · ".join(parts) if parts else "Off - loosening a block needs nothing."


def _poke(ctx):
    """Tell the status worker something changed (the tray and the mute rule then see it at once). Only the agent has one."""
    hub = getattr(ctx, "hub", None)
    worker = getattr(hub, "status_worker", None)
    if worker is not None:
        worker.poke()


# ---------------------------------------------------------------- what the page shows

def _banner(cfg: dict, now) -> dict:
    """The state of the lock banner. `kind`: off / closed / unlocked / waiting / locked / free. `ends_at` is when it
    changes by itself (the page counts down to it and then asks again); `total_sec` the whole length of an unlock or a
    wait (the bar's scale)."""
    status = antibypass.status(cfg, now)
    until = antibypass.unlocked_until(cfg, now)
    waiting = antibypass.waiting_until(cfg, now)
    nxt = antibypass.next_hours(cfg, now) if cfg["hours"] else None
    ends, total = None, 0
    if not antibypass.active(cfg):
        kind = "off"
    elif status == "closed":
        kind, ends = "closed", nxt
    elif until:
        kind, ends, total = "unlocked", until, antibypass.UNLOCK_MIN * 60
    elif status == "waiting":
        kind, ends, total = "waiting", waiting, max(1, round((cfg.get("wait_min") or 0) * 60))
    elif status == "phrase":
        kind = "locked"
    else:                                   # only allowed hours, and they're on now
        kind = "free"
        ends = min((e for e in (window_until(w, now) for w in cfg["windows"]) if e), default=None)
    return {"kind": kind, "ends_at": _iso(ends), "total_sec": total, "next_open": _iso(nxt) if kind == "closed" else None,
            "summary": describe(cfg), "hours": hours_text(cfg)}


def _pause_data(db, now) -> dict:
    state = pause.state(db, now)
    mode = modes.active(db, now)
    return {"running": bool(state), "started": _iso(state["started"]) if state else None,
            "until": _iso(state["until"]) if state else None, "silent": bool(state and state["silent"]),
            "options": [{"label": label, "minutes": minutes} for label, minutes in pause.DURATIONS.items()],
            "locked_mode": bool(mode and mode["locked"] and not mode["scheduled"])}


def _off_data(db) -> dict:
    since = antibypass.off_since(db)
    return {"off": bool(since), "since": since or None}


def _page(ctx) -> dict:
    db, now = ctx.db, ctx.now()
    cfg = antibypass.settings(db)
    return {
        "now": _iso(now),
        "config": {"phrase": bool(cfg["phrase"]), "length": cfg["length"], "grid": bool(cfg["grid"]),
                   "complex": bool(cfg.get("complex")), "custom_phrase": cfg.get("custom_phrase") or "",
                   "hours": bool(cfg["hours"]), "windows": cfg["windows"], "wait_min": cfg.get("wait_min") or 0,
                   "wait_text": reminders.minutes_text(cfg.get("wait_min") or 0)},
        "status": antibypass.status(cfg, now),
        "unlocked_until": _iso(antibypass.unlocked_until(cfg, now)),
        "waiting_until": _iso(antibypass.waiting_until(cfg, now)),
        "banner": _banner(cfg, now),
        "unlock_min": antibypass.UNLOCK_MIN,
        "lengths": antibypass.LENGTHS, "min_length": antibypass.MIN_LENGTH, "max_length": antibypass.MAX_LENGTH,
        "pause": _pause_data(db, now),
        "lockdown": _off_data(db),
    }


@core.read
def antibypass_get(ctx):
    """Everything the page shows: the settings, the lock banner, the pause card and the Lockdown on / off card."""
    return _page(ctx)


@core.read
def pause_get(ctx):
    """The Pause my blocks card: {running, started, until, silent, options: [{label, minutes}], locked_mode}."""
    return _pause_data(ctx.db, ctx.now())


# ---------------------------------------------------------------- reading what the page sent

def _flag(value, label) -> bool:
    if not isinstance(value, bool):
        raise ApiError(f"{label}: yes or no expected.")
    return value


def _length(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ApiError(f"Phrase length: a number from {antibypass.MIN_LENGTH} to {antibypass.MAX_LENGTH}.")
    try:
        return antibypass.parse_length(value)
    except ValueError as e:
        raise ApiError(str(e)) from None


def _own_phrase(value) -> str:
    if not isinstance(value, str):
        raise ApiError("Your own phrase: text expected.")
    text = " ".join(value.split())
    if len(text) > MAX_PHRASE_CHARS:
        raise ApiError(f"Your own phrase: at most {MAX_PHRASE_CHARS} characters.")
    if not text.isprintable():
        raise ApiError("Your own phrase: letters, numbers and punctuation only.")
    return text


def _wait(value):
    """The wait after passing, in minutes: typed as "Off", "10", "1h", "1h30" (like the old box) or a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ApiError("Then wait: write a time like 10, 1h or 1h30, or Off.")
    try:
        minutes = reminders.parse_minutes(str(value), least=1)
    except ValueError as e:
        raise ApiError(str(e)) from None
    return int(minutes) if minutes == int(minutes) else minutes


def _windows(value) -> list:
    """The allowed hours as stored: [{days, start, end}], times as HH:MM. A window with no day is ignored (as before)."""
    if not isinstance(value, list) or len(value) > MAX_WINDOWS:
        raise ApiError("Hours: a list of time windows expected.")
    out = []
    for w in value:
        if not isinstance(w, dict) or not isinstance(w.get("days"), list) or \
                not all(type(d) is int and 0 <= d <= 6 for d in w["days"]):
            raise ApiError("Hours: each window needs days and a start and end time.")
        if not w["days"]:
            continue
        try:
            start, end = parse_hhmm(w.get("start")), parse_hhmm(w.get("end"))
        except (ValueError, AttributeError):
            raise ApiError("Hours must look like 09:00 or 21:30.") from None
        out.append({"days": sorted(set(w["days"])), "start": f"{start:%H:%M}", "end": f"{end:%H:%M}"})
    return out


def _normal(windows) -> list:
    """Stored windows with their times as HH:MM, so "9:00" and "09:00" compare equal."""
    try:
        return _windows(windows)
    except ApiError:
        return windows


def _fields(sent: dict, old: dict) -> dict:
    """The challenge settings the page asked to change, each one checked. Anything not in SAVE_KEYS is refused."""
    if not isinstance(sent, dict):
        raise ApiError("Bad request: settings expected.")
    unknown = sorted(set(sent) - set(SAVE_KEYS))
    if unknown:
        raise ApiError(f"Bad request: unknown setting {unknown[0]!r}.")
    new = {}
    if "phrase" in sent:
        new["phrase"] = _flag(sent["phrase"], "Random phrase")
    if "length" in sent:
        new["length"] = _length(sent["length"])
    if "grid" in sent:
        new["grid"] = _flag(sent["grid"], "3×3 grid")
    if "complex" in sent:
        new["complex"] = _flag(sent["complex"], "Numbers & CAPITALS")
    if "custom_phrase" in sent:
        new["custom_phrase"] = _own_phrase(sent["custom_phrase"])
    if "hours" in sent:
        new["hours"] = _flag(sent["hours"], "Only during these hours")
    if "wait" in sent:
        new["wait_min"] = _wait(sent["wait"])
    if "windows" in sent:
        windows = _windows(sent["windows"])
        if not windows:
            raise ApiError("Turn on at least one day.")
        if windows != _normal(old["windows"]):          # (the same hours written another way aren't a change)
            new["windows"] = windows
    return new


# ---------------------------------------------------------------- saving

def _apply(ctx, fields: dict) -> dict:
    """Write the challenge settings. Runs now, or - after a passed challenge - on whatever thread the challenge ends
    on: it reads the stored settings again, so the unlock the challenge just opened is kept."""
    antibypass.save(ctx.db, {**antibypass.settings(ctx.db), **fields})
    _poke(ctx)
    return _page(ctx)


@core.loosen
def antibypass_save(ctx, cfg, confirmed=False):
    """Change the challenges: `cfg` holds only what changed (phrase, length, grid, complex, custom_phrase, hours,
    windows, wait). -> the page data once saved; {needs_challenge, ticket, ...} when it weakens Anti-Bypass;
    {needs_confirm, line} when it is a stricter change that wants a yes first (send it again with confirmed=True)."""
    confirmed = _flag(confirmed, "Confirmed")
    old = antibypass.settings(ctx.db)
    fields = {k: v for k, v in _fields(cfg, old).items() if old.get(k) != v}
    if not fields:
        return _page(ctx)
    new = {**old, **fields}
    if antibypass.settings_looser(old, new):                # checked first, always: `confirmed` never skips it
        return ctx.guard.request(["Weaken Anti-Bypass"], lambda: _apply(ctx, fields))
    if not confirmed:                                       # (every field is a challenge setting)
        return {"needs_confirm": True, "line": describe(new)}
    return _apply(ctx, fields)


@core.tighten
def antibypass_lock(ctx):
    """Lock now: end the unlock that is running. Always free (it only makes things stricter)."""
    antibypass.lock(ctx.db)
    _poke(ctx)
    return _page(ctx)


def _unlock_request(ctx, line: str, force: bool):
    return ctx.guard.request([line], lambda: _page(ctx), force=force)


@core.loosen
def antibypass_unlock_to_edit(ctx):
    """"Unlock to edit": the challenge, with nothing to change - passing it opens the unlock (for a few minutes, after
    the wait if one is set)."""
    return _unlock_request(ctx, f"Allow loosening changes for {antibypass.UNLOCK_MIN} minutes", False)


@core.loosen
def antibypass_try(ctx):
    """"Try the challenge": the same challenge as a loosening change would meet, even when none is turned on, with
    nothing to change. (Passing it opens the usual unlock - that is what a passed challenge does.)"""
    return _unlock_request(ctx, "Try the challenge - nothing is changed", True)


# ---------------------------------------------------------------- Pause my blocks

@core.loosen
def pause_start(ctx, minutes, silent=False):
    """Pause every block from your own list for 30 min / 1 h / 2 h / 4 h / the rest of the day (`minutes` null), and
    optionally silence all notifications. Starting it loosens, so it asks for the challenge (even with none turned on
    while a locked mode is on - the pause would lift that mode's blocks too). Resuming is free (pause_resume)."""
    if not (minutes is None or (type(minutes) is int and minutes in pause.DURATIONS.values())):
        raise ApiError("Pick 30 min, 1 h, 2 h, 4 h or the rest of the day.")
    silent = _flag(silent, "Silence all notifications")
    db, now = ctx.db, ctx.now()
    end = pause.end_for(db, minutes, now)
    how = "for the rest of the day" if minutes is None else f"for {pause.label(minutes)}"
    change = (f"Pause all your blocks {how} (until {end:%H:%M}): every blocked site and app, limits, hours, "
              "modes, temporary and permanent blocks" + (", and silence all notifications" if silent else ""))

    def start():
        pause.start(ctx.db, ctx.now(), minutes, silent)       # (the time it is once the challenge is done)
        _poke(ctx)
        return _pause_data(ctx.db, ctx.now())
    return ctx.guard.request([change], start, force=_pause_data(db, now)["locked_mode"])


# ---------------------------------------------------------------- Turn Lockdown off

@core.loosen
def lockdown_switch_off(ctx):
    """Switch Lockdown off entirely (nothing is enforced until it is switched on). Always asks for the challenge, even
    with none turned on. Switching it back on is free: `antibypass_switch_on` (webapi/shell.py)."""
    if antibypass.is_off(ctx.db):
        return _off_data(ctx.db)

    def off():
        antibypass.switch_off(ctx.db, ctx.now())
        _poke(ctx)
        return _off_data(ctx.db)
    return ctx.guard.request([OFF_CHANGE], off, force=True)
