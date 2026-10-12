"""Api for the Modes page (BLUEPRINT 3.7): what is on, every mode, start / stop, save / delete, and the little check
behind "+ Other site or app".

The page is untrusted. It sends plain values (a mode's boxes, "30 minutes" / "17:00" / until stopped), Python validates
every one, works out the end time itself on the trusted clock, and decides - here, never in JS - whether a change
weakens a block:

- stopping a mode that was started by hand and locked, or starting something over it: the challenge, even when no
  challenge is turned on (`force`), as the old window did. Starting another mode while a mode's automatic times are
  running asks too (a mode started by hand wins over a schedule, so it would switch that schedule's blocks off);
- deleting the mode that is on and locked, or saving it so that it blocks less (fewer categories / items / groups /
  extras, a lighter Pomodoro, a schedule that runs less): the same challenge. This closes the old window's bypass
  (delete or edit the locked mode, then it was gone for free);
- a mode with a schedule is a standing commitment: a schedule that runs fewer minutes, a lighter Pomodoro, and
  deleting it need the challenge (not forced - only when a challenge is turned on); and while its window is running
  it is held like a locked mode, so blocking less needs it too.
Longer, more, or newly added is free. The change waits under a challenge ticket (ctx.guard.request) and the page only
gets the ticket; no "confirmed" flag is ever read.

The page learns about changes from the status push; every method that changes the mode also pokes the status worker,
so the tray and the mute rule see it at once. Names: `modes_brief` (bedtime mode list) lives in webapi/reminders.py.
"""
import itertools
import json
from datetime import datetime, timedelta

import antibypass
import modes
from blocker import hosts
from blocker.apps import PROTECTED, block_flags, exe_name, in_folder, is_hosted, make_block_type, typed_path
from blocker.site_block import make_site_block_type, site_flags
from paths import APP_DIR
from resident.tray_actions import locked_by_hand
from rules import days_text, load_schedule, make_schedule, parse_hhmm, schedule_until

from . import categories, core, shared, sites as site_list
from .core import ApiError

MAX_NAME = 40
MAX_EXTRA_NAME = 60
MAX_ITEMS = 500
MAX_GROUPS = 200
MAX_EXTRAS = 100
MAX_WINDOWS = 14
MAX_MINUTES = 24 * 60            # "30 min" ... a day; longer than that is "Until I stop"
POMODORO_KEYS = ("work", "break", "rounds", "long")
POMODORO_LOW, POMODORO_HIGH = 1, 240
WAYS_APP, WAYS_SITE = ("close", "background", "minimize", "internet"), ("dns", "close", "back")
DEFAULT_WINDOW = {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}


# ---------------------------------------------------------------- small helpers

def _iso(when) -> str | None:
    return when.isoformat(timespec="seconds") if when and when != datetime.max else None


def _poke(ctx):
    """Tell the status worker something changed (the tray and the mute rule then see it at once). Only the agent has one."""
    worker = getattr(getattr(ctx, "hub", None), "status_worker", None)
    if worker is not None:
        worker.poke()


_rev = itertools.count(1)


def _changed(ctx):
    """Tell the pages that show a list of modes (Dashboard, Modes, Reminders) that it changed. The rev keeps the push from
    being dropped as "the same as before" by the Publisher. `ctx.publish` is only there in the agent."""
    fn = getattr(ctx, "publish", None)
    if fn is not None:
        try:
            fn("mode_changed", {"rev": next(_rev)})
        except Exception:
            import traceback
            core.log_error(f"Publishing mode_changed failed: {traceback.format_exc()}")


def _flag(value, label) -> bool:
    if not isinstance(value, bool):
        raise ApiError(f"{label}: yes or no expected.")
    return value


def _text(value, label, limit, required=False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ApiError(f"{label}: text expected.")
    value = " ".join(value.split())
    if required and not value:
        raise ApiError(f"Give the {label} a name.")
    if len(value) > limit:
        raise ApiError(f"{label}: at most {limit} characters, please.")
    return value


def _find(db, mode_id) -> dict:
    if not isinstance(mode_id, str):
        raise ApiError("Bad request.")
    mode = next((m for m in modes.load(db) if m["id"] == mode_id), None)
    if mode is None:
        raise ApiError("That mode doesn't exist any more.")
    return mode


def _windows_of(mode: dict) -> list | None:
    """The mode's schedule as [{days, start, end}], or None (no schedule / one that can't be read)."""
    if not mode.get("schedule"):
        return None
    try:
        return [{"days": list(w["days"]), "start": w["start"], "end": w["end"]}
                for w in load_schedule(mode["schedule"])["windows"]]
    except (ValueError, KeyError, TypeError):
        return None


def _window_running(mode: dict, now: datetime) -> bool:
    if not mode.get("schedule"):
        return False
    try:
        return bool(schedule_until(mode["schedule"], now))
    except (ValueError, KeyError, TypeError):
        return False


# ---------------------------------------------------------------- is this change weaker? (decided here, not in JS)

@core.internal
def held_now(mode_list: list, state: dict | None, mode_id: str, now: datetime) -> bool:
    """Is this mode a commitment right now? Started by hand and locked, or its schedule window is running."""
    if state and state["mode"]["id"] == mode_id and state["locked"] and not state["scheduled"]:
        return True
    mode = next((m for m in mode_list if m["id"] == mode_id), None)
    return bool(mode) and _window_running(mode, now)


def _pomodoro_lighter(old: dict | None, new: dict | None) -> bool:
    """Shorter focus, fewer rounds, longer breaks, or no Pomodoro any more."""
    if not old:
        return False
    return (not new or new["work"] < old["work"] or new["rounds"] < old["rounds"] or new["break"] > old["break"]
            or new["long"] > old["long"])


def _extras_key(extra: dict) -> str:
    return json.dumps(extra, sort_keys=True)


@core.internal
def loosenings(old: dict, new: dict, held: bool, categories_known: set | None = None) -> list[str]:
    """Why saving `new` over `old` weakens the mode (each reason is a line for the challenge), [] when it doesn't.
    `held`: the mode is on and locked, or its schedule window is running. What no longer exists (a deleted category,
    item or group) is not counted as taken away."""
    out = []
    scheduled = bool(old.get("schedule"))
    if scheduled and antibypass.schedule_looser(old["schedule"], new.get("schedule")):
        out.append("make its schedule run less (fewer hours or days, or none)")
    if held or scheduled:
        if _pomodoro_lighter(old.get("pomodoro"), new.get("pomodoro")):
            out.append("make its Pomodoro lighter (shorter focus, fewer rounds, longer breaks, or none)")
        elif new.get("pomodoro") and not old.get("pomodoro"):
            out.append("add Pomodoro breaks, so it blocks less")
    if held:
        old_cats = [c for c in old.get("categories", []) if categories_known is None or c in categories_known]
        if (set(old_cats) - set(new.get("categories", [])) or set(old.get("items", [])) - set(new.get("items", []))
                or set(old.get("groups", [])) - set(new.get("groups", []))
                or {_extras_key(e) for e in old.get("extra", [])} - {_extras_key(e) for e in new.get("extra", [])}):
            out.append("make it block less (take a category, site, app or group out of it)")
    return out


@core.internal
def delete_loosens(old: dict, held: bool) -> bool:
    """Deleting a mode takes away what it blocks: a commitment (held now, or a schedule) can't be deleted for free."""
    return held or bool(old.get("schedule"))


# ---------------------------------------------------------------- what the page shows

def _active_view(state: dict | None) -> dict | None:
    """The mode that is on, in the words of the status snapshot's `mode` (the page updates it from either)."""
    if not state:
        return None
    mode, phase = state["mode"], state["phase"]
    return {"id": mode["id"], "name": mode["name"],
            "started": None if state["scheduled"] else _iso(state["started"]), "ends_at": _iso(state["until"]),
            "locked": bool(state["locked"]), "by_schedule": bool(state["scheduled"]),
            "mute": bool(mode.get("mute")), "blocking": modes.blocking(state),
            "phase": None if not phase else {"name": phase[0], "ends_at": _iso(phase[1]), "round": phase[2],
                                             "rounds": mode["pomodoro"]["rounds"]}}


def _pomodoro_text(p: dict) -> str:
    return f"Pomodoro {p['work']} / {p['break']} min × {p['rounds']}, then {p['long']} min"


def _mode_view(mode: dict, names: dict, held: bool) -> dict:
    windows = _windows_of(mode)
    p = mode.get("pomodoro")
    return {"id": mode["id"], "name": mode["name"], "builtin": bool(mode.get("builtin")),
            "categories": list(mode.get("categories", [])), "items": list(mode.get("items", [])),
            "groups": list(mode.get("groups", [])), "extra": [dict(e) for e in mode.get("extra", [])],
            "mute": bool(mode.get("mute")), "pomodoro": dict(p) if p else None, "windows": windows,
            "describe": modes.describe(mode, names) + (" · mutes notifications" if mode.get("mute") else ""),
            "pomodoro_text": _pomodoro_text(p) if p else "",
            "run_min": int(modes.pomodoro_length(p).total_seconds() // 60) if p else 0,
            "auto_text": "Automatically: " + "; ".join(f"{days_text(w['days'])} {w['start']}-{w['end']}"
                                                       for w in windows) if windows else "",
            "held": held}


def _page(ctx) -> dict:
    db, now = ctx.db, ctx.now()
    mode_list = modes.load(db)
    state = modes.active(db, now, mode_list)
    cats = categories.load(db)
    names = {c["key"]: c["name"] for c in cats}
    return {"now": _iso(now), "active": _active_view(state),
            "modes": [_mode_view(m, names, held_now(mode_list, state, m["id"], now)) for m in mode_list],
            "categories": cats, "pomodoro_defaults": dict(modes.DEFAULT_POMODORO),
            "default_window": dict(DEFAULT_WINDOW), "limits": {"low": POMODORO_LOW, "high": POMODORO_HIGH}}


@core.read
def modes_get(ctx):
    """Everything the page shows in one call: {now, active (the status snapshot's `mode` shape, or null), modes,
    categories, ...}. The page counts the countdowns down itself from the end times."""
    return _page(ctx)


# ---------------------------------------------------------------- start and stop

def _until_spec(until):
    """What the page asked for -> ("minutes", n) | ("at", time) | None (until I stop). Python makes the end time."""
    if until is None:
        return None
    if isinstance(until, bool):
        raise ApiError("Bad request.")
    if isinstance(until, (int, float)):
        if until != int(until) or not 1 <= int(until) <= MAX_MINUTES:
            raise ApiError("Pick 30 min, 1 h, 2 h, a time, or until you stop it.")
        return ("minutes", int(until))
    if isinstance(until, str):
        try:
            return ("at", parse_hhmm(until))
        except (ValueError, AttributeError):
            raise ApiError("Write the time like 17:00.") from None
    raise ApiError("Bad request.")


def _end_for(mode: dict, spec, now: datetime) -> datetime | None:
    """A Pomodoro runs all its rounds; else minutes from now, or the next time the clock shows that time."""
    if mode.get("pomodoro"):
        return now + modes.pomodoro_length(mode["pomodoro"])
    if spec is None:
        return None
    if spec[0] == "minutes":
        return now + timedelta(minutes=spec[1])
    end = datetime.combine(now.date(), spec[1])
    return end if end > now else end + timedelta(days=1)       # a time already gone today means tomorrow


@core.loosen
def mode_start(ctx, mode_id, until=None, locked=False):
    """Start a mode now. `until`: minutes (30 / 60 / 120 ...), "17:00" (today, or tomorrow when it has passed - on the
    trusted clock) or null (until you stop it); a Pomodoro always runs all its rounds. `locked` (needs an end): stopping
    it early then needs the challenge. Starting over a mode that is locked asks for the challenge first."""
    db = ctx.db
    mode = _find(db, mode_id)
    locked = _flag(locked, "Lock")
    spec = _until_spec(until)

    def start():
        d, now = ctx.db, ctx.now()
        end = _end_for(mode, spec, now)
        modes.start(d, mode["id"], now, end, locked)
        state = modes.active(d, now)
        shown = state["until"] if state else end
        _poke(ctx)
        page = _page(ctx)
        page["message"] = (f"{mode['name']} mode on" + (f" until {shown:%H:%M}" if shown else "")
                           + (" (locked)" if locked and end else ""))
        return page

    now = ctx.now()
    current = locked_by_hand(db, now)
    running = next((m for m in modes.load(db) if _window_running(m, now)), None)
    why = []
    if current is not None:
        why.append(f"End the locked {current['mode']['name']} mode (locked until {current['until']:%H:%M}) "
                   f"and start {mode['name']}")
    if running is not None and running["id"] != mode["id"]:
        # a mode started by hand wins over a schedule: starting another one would switch the running schedule's blocks off
        why.append(f"Start {mode['name']} while {running['name']} is on by its schedule (its blocks would stop)")
    if not why:
        return start()
    return ctx.guard.request(why, start, force=current is not None)


@core.loosen
def mode_stop(ctx):
    """Stop the mode that was started by hand. Locked: the challenge first, even with none turned on. A mode that is on
    by its schedule is stopped by changing its times, not here."""
    db, now = ctx.db, ctx.now()
    state = modes.active(db, now)
    if state is None:
        return _page(ctx)
    if state["scheduled"]:
        raise ApiError(f"{state['mode']['name']} is on by its schedule - change its times to turn it off.")

    def stop(force):
        d, n = ctx.db, ctx.now()
        current = modes.active(d, n)
        try:
            modes.stop(d, n, force)
        except ValueError as e:
            raise ApiError(str(e)) from None
        _poke(ctx)
        page = _page(ctx)
        if current and not current["scheduled"]:
            page["message"] = f"{current['mode']['name']} mode off"
        return page

    if state["locked"]:
        return ctx.guard.request([f"Stop the locked {state['mode']['name']} mode (locked until {state['until']:%H:%M})"],
                                 lambda: stop(True), force=True)
    return stop(False)


# ---------------------------------------------------------------- saving a mode

def _whole(value) -> int:
    """A Pomodoro box: 25, "25" or 25.0 - never 2.5, "-3" or "1e3"."""
    if isinstance(value, bool):
        raise ApiError("Pomodoro numbers must be whole numbers.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit() and len(value.strip()) <= 6:
        return int(value.strip())
    raise ApiError("Pomodoro numbers must be whole numbers.")


def _pomodoro(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ApiError("Bad request: the Pomodoro boxes.")
    p = {k: _whole(value.get(k)) for k in POMODORO_KEYS}
    if not all(POMODORO_LOW <= v <= POMODORO_HIGH for v in p.values()):
        raise ApiError(f"Pomodoro numbers must be between {POMODORO_LOW} and {POMODORO_HIGH}.")
    return p


def _schedule(value) -> str | None:
    """The 'turn on automatically' windows -> the stored schedule JSON (as the old editor built it), or None."""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > MAX_WINDOWS:
        raise ApiError("Bad request: the automatic times.")
    windows = []
    for w in value:
        if not isinstance(w, dict) or not isinstance(w.get("days"), list) or \
                not all(type(d) is int and 0 <= d <= 6 for d in w["days"]):
            raise ApiError("Bad request: each time window needs days and a start and end time.")
        if not w["days"]:                    # (a row with no day on is ignored, as in the old window)
            continue
        try:
            start, end = parse_hhmm(w.get("start")), parse_hhmm(w.get("end"))
        except (ValueError, AttributeError):
            raise ApiError("Hours must look like 09:00 or 21:30.") from None
        windows.append((sorted(set(w["days"])), f"{start:%H:%M}", f"{end:%H:%M}"))
    if not windows:
        raise ApiError("Turn on at least one day for the automatic times.")
    return make_schedule("block", windows)


def _ids(value, known: set, label, limit) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit or not all(type(i) is int for i in value):
        raise ApiError(f"Bad request: {label}.")
    return [i for i in dict.fromkeys(value) if i in known]       # (what was deleted meanwhile just drops out)


def _popular(host: str):
    """(name, all its host names) of the known site this host is: the ~500 bundled sites (webapi/sites.py)."""
    return site_list.lookup(host)


def _guess_name(host: str) -> str:
    entry = _popular(host)
    if entry:
        return entry[0]
    parts = host.split(".")
    return parts[-2].capitalize() if len(parts) > 1 else host


def _site_target(text) -> str:
    try:
        return hosts.normalize_host(text)
    except (ValueError, AttributeError):
        raise ApiError(f"Write a site like reddit.com ({text!r} isn't one).") from None


def _ways(value, allowed, default) -> set:
    if value is None:
        return set(default)
    if not isinstance(value, list) or not all(isinstance(w, str) and w in allowed for w in value):
        raise ApiError("Bad request: how it is blocked.")
    return set(value)


def _build_extra(spec: dict, cats: dict | None = None) -> dict:
    """One "also block" site, app or category from what the page typed / picked -> {kind, name, targets, block_type,
    app_path}. The same rules as the old picker: a site is tidied to its address (a known site brings its other hostnames),
    an app is its .exe (or a program file), Windows' own processes and Lockdown can't be blocked, a category is one of
    yours (`cats`: {key: name}; its apps are closed / minimised the way ticked), and one way of blocking at least must be
    ticked."""
    kind = spec.get("kind")
    text = spec.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 260:
        raise ApiError("Write a site (reddit.com) or pick an app.")
    name = _text(spec.get("name"), "Display name", MAX_EXTRA_NAME)
    if kind == "category":
        key = text.strip()
        if not cats or key not in cats:
            raise ApiError("Pick one of your categories.")
        flags = _ways(spec.get("ways"), WAYS_APP, ("close",))
        if not flags - {"background"}:
            raise ApiError("Tick at least one of Close app / Minimize / Block internet.")
        if {"close", "minimize"} <= flags:
            raise ApiError("Close app and Minimize do the same thing: pick one.")
        if "close" not in flags:
            flags.discard("background")
        return {"kind": "category", "name": name or cats[key], "targets": [key], "block_type": make_block_type(flags),
                "app_path": None}
    if kind == "site":
        host = _site_target(text)
        flags = _ways(spec.get("ways"), WAYS_SITE, ("dns",))
        if not flags:
            raise ApiError("Tick at least one of Can't load it / Close the tab / Go back.")
        if {"close", "back"} <= flags:
            raise ApiError("Close the tab and Go back do the same thing: pick one.")
        entry = _popular(host)
        return {"kind": "site", "name": name or _guess_name(host), "targets": list(entry[1]) if entry else [host],
                "block_type": make_site_block_type(flags), "app_path": None}
    if kind != "app":
        raise ApiError("Bad request: a site or an app.")
    path = spec.get("app_path")
    if path is not None and (not isinstance(path, str) or len(path) > 400):
        raise ApiError("Bad request: the app's path.")
    path = shared.long_path(path or typed_path(text) or None)      # (8.3 short names spelled out for the check below)
    exe = exe_name(text)
    hosted = is_hosted(exe)
    if not exe or not (exe.endswith(".exe") or hosted):
        raise ApiError("Write the program's file name, like discord.exe - or use Browse apps.")
    if exe in PROTECTED or (hosted and in_folder(path, str(APP_DIR).lower())):
        raise ApiError(f"{exe} is part of Windows (or Lockdown) and can't be blocked.")
    flags = _ways(spec.get("ways"), WAYS_APP, ("close",))
    if hosted:
        flags -= {"internet", "background"}
    if not flags - {"background"}:
        raise ApiError("Tick Close app or Minimize." if hosted else "Tick at least one of Close app / Minimize / Block internet.")
    if {"close", "minimize"} <= flags:
        raise ApiError("Close app and Minimize do the same thing: pick one.")
    if "close" not in flags:
        flags.discard("background")
    return {"kind": "app", "name": name or exe.rsplit(".", 1)[0].title(), "targets": [exe],
            "block_type": make_block_type(flags), "app_path": path}


@core.read
def mode_extra_check(ctx, spec):
    """Check one "+ Other site or app" and return it ready to add: {kind, name, targets, block_type, app_path}.
    `spec`: {kind: "site" | "app" | "category", text (a category: its key), name, ways: ["dns" | "close" | "back"] or
    ["close" | "background" | "minimize" | "internet"], app_path}. Changes nothing; mode_save checks every extra again."""
    if not isinstance(spec, dict):
        raise ApiError("Bad request.")
    return _build_extra(spec, _category_names(ctx))


def _site_targets(targets) -> list:
    """The hostnames of a known site (youtube.com + its other hostnames): each one must be an address."""
    if not isinstance(targets, list) or not 1 <= len(targets) <= 40:
        raise ApiError("Bad request: a site address.")
    out = []
    for t in targets:
        host = _site_target(t)
        out.append(t.strip().lower() if t.strip().lower() in (host, "www." + host) else host)
    return out


def _category_names(ctx) -> dict:
    return {c["key"]: c["name"] for c in categories.load(ctx.db)}


def _recheck_extra(e: dict, cats: dict) -> dict:
    """A new or changed entry of the "also block" list, checked again as if it had just been typed."""
    kind, targets = e.get("kind"), e.get("targets")
    if kind not in ("site", "app", "category") or not isinstance(targets, list) or not targets or not isinstance(targets[0], str):
        raise ApiError("Bad request: a site, an app or a category.")
    way = e.get("block_type") if isinstance(e.get("block_type"), str) or e.get("block_type") is None else None
    ways = sorted(site_flags(way) if kind == "site" else block_flags(way))
    built = _build_extra({"kind": kind, "text": targets[0], "name": e.get("name"), "app_path": e.get("app_path"),
                          "ways": ways}, cats)
    if kind == "site" and len(targets) > 1:
        built["targets"] = _site_targets(targets)
    return built


def _extras(value, old: dict | None, cats: dict) -> list:
    """The "also block" list. An entry exactly as already stored is kept as it is; a new or changed one is checked."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_EXTRAS or not all(isinstance(e, dict) for e in value):
        raise ApiError("Bad request: the extra sites and apps.")
    stored = {_extras_key(e) for e in (old or {}).get("extra", [])}
    return [e if _extras_key(e) in stored else _recheck_extra(e, cats) for e in value]


def _mode_from_page(ctx, sent: dict, old: dict | None, known_cats: set) -> dict:
    """Validate the editor's fields (the old window's messages) and build the mode to store."""
    db = ctx.db
    name = _text(sent.get("name"), "mode", MAX_NAME)
    if old and old.get("builtin"):
        if name and name != old["name"]:
            raise ApiError("A built-in mode's name can't be changed.")
        name = old["name"]
    elif not name:
        if not old:
            raise ApiError("Give the mode a name.")
        name = old["name"]
    cats = sent.get("categories")
    if cats is None:
        cats = []
    if not isinstance(cats, list) or len(cats) > 50 or not all(isinstance(c, str) for c in cats):
        raise ApiError("Bad request: the categories.")
    return {**(old or {"id": None, "builtin": False}), "name": name,
            "categories": [c for c in dict.fromkeys(cats) if c in known_cats],
            "items": _ids(sent.get("items"), {i["id"] for i in db.list_items()}, "the blocklist items", MAX_ITEMS),
            "groups": _ids(sent.get("groups"), {g["id"] for g in db.list_groups()}, "the groups", MAX_GROUPS),
            "extra": _extras(sent.get("extra"), old, _category_names(ctx)), "mute": _flag(sent.get("mute", False), "Mute notifications"),
            "pomodoro": _pomodoro(sent.get("pomodoro")), "schedule": _schedule(sent.get("windows"))}


@core.loosen
def mode_save(ctx, mode):
    """Add (mode.id empty) or change a mode: {id, name, categories, items, groups, extra, mute, pomodoro, windows}.
    A built-in mode keeps its name. Weakening a mode that is held (on and locked, or its window running) or has a
    schedule needs the challenge - asked even with none turned on when it is the locked one."""
    if not isinstance(mode, dict):
        raise ApiError("Bad request.")
    db, now = ctx.db, ctx.now()
    mode_list = modes.load(db)
    mode_id = mode.get("id")
    old = None
    if mode_id:
        old = next((m for m in mode_list if m["id"] == mode_id), None) if isinstance(mode_id, str) else None
        if old is None:
            raise ApiError("That mode was deleted - close this and start again.")
    known = {c["key"] for c in categories.load(db)}
    new = _mode_from_page(ctx, mode, old, known)

    def save():
        d = ctx.db
        current = modes.load(d)
        if old is None:
            new["id"] = modes.new_id(current)
            current.append(new)
        else:
            if not any(m["id"] == new["id"] for m in current):
                raise ApiError("That mode was deleted - close this and start again.")
            current = [new if m["id"] == new["id"] else m for m in current]
        modes.save(d, current)
        _poke(ctx)
        _changed(ctx)
        page = _page(ctx)
        page["saved_id"] = new["id"]
        return page

    if old is None:
        return save()
    state = modes.active(db, now, mode_list)
    held = held_now(mode_list, state, old["id"], now)
    why = loosenings(old, new, held, known)
    if not why:
        return save()
    by_hand = bool(state and state["mode"]["id"] == old["id"] and state["locked"] and not state["scheduled"])
    return ctx.guard.request([f"{old['name']} mode: {reason}" for reason in why], save, force=by_hand)


@core.loosen
def mode_delete(ctx, mode_id):
    """Delete one of your own modes. The mode that is on and locked, or one with a schedule, asks for the challenge."""
    db, now = ctx.db, ctx.now()
    mode_list = modes.load(db)
    old = next((m for m in mode_list if m["id"] == mode_id), None) if isinstance(mode_id, str) else None
    if old is None:
        return _page(ctx)                                    # (already gone)
    if old.get("builtin"):
        raise ApiError("The built-in modes can't be deleted.")
    state = modes.active(db, now, mode_list)
    held = held_now(mode_list, state, old["id"], now)

    def delete():
        d = ctx.db
        modes.save(d, [m for m in modes.load(d) if m["id"] != old["id"]])
        try:
            running = json.loads(d.get_setting(modes.ACTIVE_KEY, "") or "null")
        except ValueError:
            running = None
        if running and running.get("id") == old["id"]:       # (a new mode may get this id again: it must not start on)
            d.set_setting(modes.ACTIVE_KEY, "")
        _poke(ctx)
        _changed(ctx)
        return _page(ctx)

    if not delete_loosens(old, held):
        return delete()
    by_hand = bool(state and state["mode"]["id"] == old["id"] and state["locked"] and not state["scheduled"])
    what = (f"Delete the locked {old['name']} mode" if by_hand else
            f"Delete the {old['name']} mode (its schedule is running)" if held else
            f"Delete the {old['name']} mode, which turns on by a schedule")
    return ctx.guard.request([what], delete, force=by_hand)
