"""The status snapshot: everything the shell, the tray and the workers need to know "right now", read from the
database ONCE per tick (StatusWorker, every second) instead of every page and poller asking for itself.

`build(db, now)` takes one trusted-time reading, one `usage_lookup`, one load of items and groups, and works out from
them: is the service running (heartbeat younger than 15 s), is Lockdown switched off, "Pause my blocks", the active mode
(with its Pomodoro phase), what is blocked now and what changes next for each item, the Anti-Bypass state, the
emergency uses left per kind, a waiting update, and what the tray should say and colour. Standard library + the
engine modules only - no windows, no Win32.

A snapshot is frozen. Its "page part" (PUBLIC_KEYS) is plain JSON-ready data (ISO times without a zone, in Lockdown's
trusted time; `clock_offset` is trusted time minus the PC clock in seconds, so the page can tick countdowns locally
from the end times). `changed(old, new)` gives only the top-level keys of that part that differ - what the publisher
sends. The Python-only part (`now`, `items`, `groups`, `usage`, `block_list`, `item_states`, `tray`) is what the
workers and Api methods read in-process. Dicts inside a snapshot are shared: treat them as read-only.
"""
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple

import antibypass
import emergency
import modes
import pause
import reminders
import updates
from rules import effective_rules, next_block
from trusted_time import OFFSET_KEY

HEARTBEAT_KEY = "service_heartbeat"   # = service.HEARTBEAT_KEY (not imported: that pulls in the whole service);
#                                       tests/test_rebuild_workers.py checks the two are the same
SERVICE_TIMEOUT_SEC = 15              # the service writes its heartbeat every 2 s; older than this = not running

# What the page gets (the `status` push). Order = the order of the keys in `public()`.
PUBLIC_KEYS = ("running", "off", "pause", "mode", "blocks", "antibypass", "emergency", "update", "clock_offset")


class TrayState(NamedTuple):
    """What the tray icon shows. The tray is only touched when this changes (workers.tray_listener)."""
    text: str                     # the status line / tooltip
    colour: str                   # grey = off, red = service down, blue = paused, yellow = a mode is on, green
    running: bool
    off: bool
    paused_until: datetime | None
    modes: tuple                  # ((id, name), ...) for the tray's mode menu
    mode_id: str | None
    update: str | None            # the version of a waiting update (the menu's "Install update" line)


def tray_colour(running: bool, off: bool, paused: bool, mode_on: bool) -> str:
    """The same order gui/tray.py uses for the icon: grey = off, red = service down, blue = paused, yellow = a mode
    is on, green = blocking."""
    return "grey" if off else "red" if not running else "blue" if paused else "yellow" if mode_on else "green"


def iso(when: datetime | None) -> str | None:
    """A trusted-time datetime as the page gets it ("2026-10-11T15:42:17", no zone); None and "no end" are None."""
    if when is None or when == datetime.max:
        return None
    return when.isoformat(timespec="seconds")


@dataclass(frozen=True, eq=False)
class StatusSnapshot:
    # ----- the page part (JSON-ready) -----
    running: bool                     # service heartbeat younger than SERVICE_TIMEOUT_SEC
    off: str | None                   # when Lockdown was switched off (its own text), None while it is on
    pause: dict | None                # {"started", "until", "silent"} while "Pause my blocks" runs
    mode: dict | None                 # {id, name, started, ends_at, locked, by_schedule, mute, blocking, phase}
    blocks: dict                      # {count: blocked now, total: items, next_change, rev}: rev changes whenever
    #                                   any item's blocked state / reason / end / next change does
    antibypass: dict                  # {status, active, unlocked_until, waiting_until, next_open}
    emergency: dict                   # {enabled, usable, left, kinds: [{id, minutes, per, left, allowed, resets}]}
    update: dict | None               # {version, installable} while a newer Lockdown is waiting (not snoozed)
    clock_offset: float               # trusted time minus the PC clock, seconds
    # ----- Python only -----
    now: datetime                     # the trusted time this was built for
    wall: float                       # time.time() then (the service heartbeat is compared with this)
    items: tuple                      # db.list_items()
    groups: tuple                     # db.list_groups()
    usage: Any                        # the ONE db.usage_lookup(now) of this tick
    block_list: tuple                 # db.blocks(...): [{item, reason, until, rule}] (empty while paused)
    item_states: dict                 # item id -> {blocked, reason, until, next, rule}; enabled items only
    tray: TrayState
    break_allow: frozenset | None = None   # process names a strict break leaves up (reminders.allowed_exes); None = the default
    bedtime_grey: bool = False        # the screen should be grey for bedtime (GreyWorker): in the sleep window, not held (_bedtime_grey)

    def public(self) -> dict:
        """The page part as one dict (the `status` event's payload)."""
        return {key: getattr(self, key) for key in PUBLIC_KEYS}


def changed(old: "StatusSnapshot | None", new: "StatusSnapshot | None") -> dict:
    """The top-level keys of the page part that differ between two snapshots, with their new values: {} when
    nothing changed (the publisher then sends nothing). With no `old` (the first snapshot, or the page just
    opened): all of it."""
    if new is None:
        return {}
    if old is None:
        return new.public()
    return {key: getattr(new, key) for key in PUBLIC_KEYS if getattr(new, key) != getattr(old, key)}


def break_allowed(snap) -> frozenset:
    """The process names a strict break leaves up, from a snapshot (no database read on MinimizeWorker's tick): the
    user's "Allowed during breaks" list; the default when there is no snapshot yet."""
    allowed = getattr(snap, "break_allow", None)
    return allowed if allowed is not None else reminders.allowed_exes(reminders.BREAK_ALLOW_DEFAULT)


def tray_changed(old: "StatusSnapshot | None", new: "StatusSnapshot") -> bool:
    return old is None or old.tray != new.tray


# ---------------------------------------------------------------- building one

def _service_running(db, wall: float) -> bool:
    try:
        return wall - float(db.get_setting(HEARTBEAT_KEY, "0")) < SERVICE_TIMEOUT_SEC
    except (TypeError, ValueError):
        return False


def _mode_part(state: dict | None) -> dict | None:
    if not state:
        return None
    mode, phase = state["mode"], state["phase"]
    return {"id": mode["id"], "name": mode["name"],
            # (a mode on by its schedule is "started" at every reading: not a fact, and it would change each tick)
            "started": None if state["scheduled"] else iso(state["started"]),
            "ends_at": iso(state["until"]), "locked": bool(state["locked"]), "by_schedule": bool(state["scheduled"]),
            "mute": bool(mode.get("mute")), "blocking": modes.blocking(state),
            "phase": None if not phase else {"name": phase[0], "ends_at": iso(phase[1]), "round": phase[2],
                                             "rounds": mode["pomodoro"]["rounds"]}}


def _antibypass_part(db, now: datetime) -> dict:
    cfg = antibypass.settings(db)
    status = antibypass.status(cfg, now)
    return {"status": status, "active": antibypass.active(cfg),
            "unlocked_until": iso(antibypass.unlocked_until(cfg, now)),
            "waiting_until": iso(antibypass.waiting_until(cfg, now)),
            "next_open": iso(antibypass.next_hours(cfg, now)) if status == "closed" else None}


def _emergency_part(db, now: datetime) -> dict:
    enabled = emergency.get(db, "emergency.enabled") == "1"
    kinds = [{"id": kind["id"], "minutes": kind["minutes"], "per": kind["per"], "left": left, "allowed": allowed,
              "resets": iso(reset)} for kind, left, allowed, reset in emergency.left_by_tier(db, now)]
    return {"enabled": enabled, "usable": enabled and bool(kinds),
            "left": sum(k["left"] for k in kinds) if enabled else 0, "kinds": kinds}


def _bedtime_grey(db, now: datetime, paused: dict | None) -> bool:
    """Bedtime grayscale (the phone's rule): the bedtime reminder and "Grey screen during bedtime" are on and trusted time
    is from bedtime to wake-up time - but not while an emergency unlock or a silent "Pause my blocks" holds the alerts."""
    try:
        sleep = reminders.load(db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
        if not reminders.bedtime_grey_wanted(sleep, now):
            return False
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
    return not ((paused and paused["silent"]) or emergency.alerts_paused_until(db, now))


def _item_states(items: list[dict], groups: list[dict], blocks: list[dict], now: datetime, usage) -> dict:
    """item id -> {blocked, reason, until, next, rule}. Blocked: why, and when it stops (None = no end). Not blocked:
    when and by what rule it will next be (a schedule window, the end of an emergency unlock or a pause) - or None.
    (Usage-based blocks - a time limit running out - are only predicted while the item is in use: that is the
    watcher's job, not a fact about the item.)"""
    blocked = {}
    for block in blocks:
        item_id = block["item"]["id"]
        if item_id is not None and item_id not in blocked:
            blocked[item_id] = block
    out = {}
    for item in items:
        if item["disabled"]:
            continue
        block = blocked.get(item["id"])
        if block:
            out[item["id"]] = {"blocked": True, "reason": block["reason"], "until": iso(block["until"]),
                               "next": None, "rule": block["rule"].get("rule_type")}
            continue
        found = next_block(effective_rules(item, groups), now, usage)
        out[item["id"]] = {"blocked": False, "reason": None, "until": None,
                           "next": iso(found[0]) if found else None,
                           "rule": found[1].get("rule_type") if found else None}
    return out


def _blocks_part(items, blocks, states: dict) -> dict:
    changes = sorted(when for s in states.values() for when in (s["until"], s["next"]) if when)
    rev = hash((tuple((b["item"]["id"], b["item"]["target"], b["reason"], iso(b["until"])) for b in blocks),
                tuple((i, s["blocked"], s["reason"], s["until"], s["next"], s["rule"]) for i, s in states.items())))
    return {"count": len(blocks), "total": len(items), "next_change": changes[0] if changes else None,
            "rev": rev & 0xFFFFFFFF}


def _tray(total: int, running: bool, off: bool, paused_until: datetime | None, state: dict | None,
          mode_list: list[dict], update: dict | None) -> TrayState:
    mode_text = f" · {state['mode']['name']} mode" if state else ""
    text = ("OFF - nothing is enforced" if off else
            f"Blocking paused until {paused_until:%H:%M}" if paused_until else
            f"{total} sites/apps blocked{mode_text}")
    return TrayState(text, tray_colour(running, off, bool(paused_until), bool(state)), running, off, paused_until,
                     tuple((m["id"], m["name"]) for m in mode_list), state["mode"]["id"] if state else None,
                     update["version"] if update else None)


def build(db, now: datetime, wall: float | None = None) -> StatusSnapshot:
    """One snapshot for `now` (trusted time) - the whole tick's reading of the database. `wall` is time.time()
    (given by tests; the heartbeat is the PC clock's, not trusted time)."""
    wall = time.time() if wall is None else wall
    running = _service_running(db, wall)
    off = antibypass.off_since(db) or None
    paused = pause.state(db, now)
    mode_list = modes.load(db)
    state = modes.active(db, now, mode_list)
    usage = db.usage_lookup(now)                       # the ONE usage read of this tick
    items, groups = db.list_items(), db.list_groups()
    block_list = db.blocks(now, usage, items, groups)
    states = _item_states(items, groups, block_list, now, usage)
    found = updates.banner(db, now)
    update = {"version": found["version"], "installable": updates.can_install(found)} if found else None
    try:
        offset = float(db.get_setting(OFFSET_KEY, "0") or 0)
    except ValueError:
        offset = 0.0
    paused_until = paused["until"] if paused else None
    return StatusSnapshot(
        running=running, off=off,
        pause={"started": iso(paused["started"]), "until": iso(paused["until"]), "silent": paused["silent"]}
        if paused else None,
        mode=_mode_part(state), blocks=_blocks_part(items, block_list, states),
        antibypass=_antibypass_part(db, now), emergency=_emergency_part(db, now), update=update, clock_offset=offset,
        now=now, wall=wall, items=tuple(items), groups=tuple(groups), usage=usage, block_list=tuple(block_list),
        item_states=states, tray=_tray(len(items), running, bool(off), paused_until, state, mode_list, update),
        break_allow=reminders.allowed_exes(reminders.allowed_apps(reminders.load(db, reminders.BREAK_KEY,
                                                                                 reminders.DEFAULT_BREAK))),
        bedtime_grey=_bedtime_grey(db, now, paused))
