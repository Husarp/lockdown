"""Modes: while a mode is on, it blocks a category (e.g. everything marked Distracting) plus picked sites / apps /
groups - on top of the normal blockers. One mode at a time; started by hand (for a while / until a time / until
stopped, optionally locked until it ends) or by its schedule. Focus is a Pomodoro: blocked in focus rounds only.
Used by the service (db.blocks) and the GUI - standard library only."""
import json
from datetime import datetime, timedelta

from rules import TIME_FMT, schedule_until

LIST_KEY = "modes.list"       # JSON list of modes
ACTIVE_KEY = "modes.active"   # JSON {"id", "started", "until" (None = until stopped), "locked"} or ""
DEFAULT_POMODORO = {"work": 25, "break": 5, "rounds": 4, "long": 15}

DEFAULT_MODES = [
    {"id": "work", "name": "Work", "builtin": True, "categories": ["distracting"], "items": [], "groups": [],
     "extra": [], "mute": False, "pomodoro": None, "schedule": None},
    {"id": "study", "name": "Study", "builtin": True, "categories": ["distracting"], "items": [], "groups": [],
     "extra": [], "mute": False, "pomodoro": None, "schedule": None},
    {"id": "focus", "name": "Focus", "builtin": True, "categories": ["distracting"], "items": [], "groups": [],
     "extra": [], "mute": False, "pomodoro": DEFAULT_POMODORO, "schedule": None},
    {"id": "dnd", "name": "Do Not Disturb", "builtin": True, "categories": ["distracting"], "items": [],
     "groups": [], "extra": [], "mute": True, "pomodoro": None, "schedule": None},
    {"id": "relax", "name": "Relax", "builtin": True, "categories": [], "items": [], "groups": [], "extra": [],
     "mute": False, "pomodoro": None, "schedule": None},
]


def _dt(text: str | None) -> datetime | None:
    return datetime.strptime(text, TIME_FMT) if text else None


def load(db) -> list[dict]:
    try:
        saved = json.loads(db.get_setting(LIST_KEY, "") or "[]")
    except ValueError:
        saved = []
    by_id = {m["id"]: m for m in saved}
    out = [{**d, **by_id.get(d["id"], {}), "builtin": True} for d in DEFAULT_MODES]
    return out + [m for m in saved if m["id"] not in {d["id"] for d in DEFAULT_MODES}]


def save(db, modes: list[dict]):
    db.set_setting(LIST_KEY, json.dumps(modes))


def new_id(modes: list[dict]) -> str:
    n = 1
    while f"custom{n}" in {m["id"] for m in modes}:
        n += 1
    return f"custom{n}"


# ---------- which mode is on ----------

def pomodoro_phase(p: dict, started: datetime, now: datetime) -> tuple[str, datetime, int] | None:
    """("focus" | "break" | "long break", phase end, round) - or None once all rounds are done."""
    t = started
    for rnd in range(1, p["rounds"] + 1):
        for name, minutes in (("focus", p["work"]), ("break" if rnd < p["rounds"] else "long break",
                                                     p["break"] if rnd < p["rounds"] else p["long"])):
            end = t + timedelta(minutes=minutes)
            if now < end:
                return name, end, rnd
            t = end
    return None


def pomodoro_length(p: dict) -> timedelta:
    return timedelta(minutes=p["rounds"] * p["work"] + (p["rounds"] - 1) * p["break"] + p["long"])


def active(db, now: datetime, modes: list[dict] | None = None) -> dict | None:
    """The mode that's on: {mode, started, until, locked, scheduled, phase} - started by hand wins over a schedule."""
    modes = modes if modes is not None else load(db)
    by_id = {m["id"]: m for m in modes}
    try:
        state = json.loads(db.get_setting(ACTIVE_KEY, "") or "null")
    except ValueError:
        state = None
    if state and state.get("id") in by_id:
        mode, started, until = by_id[state["id"]], _dt(state["started"]), _dt(state.get("until"))
        if mode.get("pomodoro"):
            until = min(until, started + pomodoro_length(mode["pomodoro"])) if until else \
                started + pomodoro_length(mode["pomodoro"])
        if started <= now and (until is None or now < until):
            phase = pomodoro_phase(mode["pomodoro"], started, now) if mode.get("pomodoro") else None
            return {"mode": mode, "started": started, "until": until, "locked": bool(state.get("locked")),
                    "scheduled": False, "phase": phase}
    for mode in modes:
        if mode.get("schedule"):
            until = schedule_until(mode["schedule"], now)   # the schedule's windows are when the mode is on
            if until:
                return {"mode": mode, "started": now, "until": None if until == datetime.max else until,
                        "locked": False, "scheduled": True, "phase": None}
    return None


def blocking(state: dict | None) -> bool:
    """Does the active mode block right now? (Not during Pomodoro breaks; Relax blocks nothing.)"""
    if not state:
        return False
    phase = state["phase"]
    return not (phase and phase[0] != "focus")


def start(db, mode_id: str, now: datetime, until: datetime | None, locked: bool = False):
    db.set_setting(ACTIVE_KEY, json.dumps({"id": mode_id, "started": now.strftime(TIME_FMT),
                                           "until": until.strftime(TIME_FMT) if until else None,
                                           "locked": bool(locked and until)}))


def stop(db, now: datetime):
    """Stop the mode started by hand. Raises ValueError while it's locked."""
    state = active(db, now)
    if state and state["locked"] and not state["scheduled"]:
        raise ValueError(f"{state['mode']['name']} is locked until {state['until']:%H:%M}.")
    db.set_setting(ACTIVE_KEY, "")


# ---------- what it blocks ----------

def _item_category(item: dict, categories: dict[tuple[str, str], str]) -> str:
    """A blocklist item's category: chosen for its exe / one of its sites (or a subdomain), else Distracting."""
    if item["item_type"] == "app":
        return categories.get(("app", item["target"].lower()), "distracting")
    hosts = item["target"].lower().split()
    for (kind, name), cat in categories.items():
        if kind == "site" and any(name == h or name.endswith("." + h) for h in hosts):
            return cat
    return "distracting"


def targets(mode: dict, items: list[dict], groups: list[dict], categories: dict[tuple[str, str], str]) -> list[dict]:
    """Items to block (existing blocklist items, or made-up ones with id None for things not on the list).
    categories: {(kind, name): category} as chosen on the Screen Time page; things on the blocklist count as
    Distracting unless you chose otherwise."""
    cats = set(mode.get("categories", []))
    out, seen = [], set()

    def add(item):
        key = item["target"].lower()
        if key not in seen:
            seen.add(key)
            out.append(item)

    for item in items:
        in_cat = _item_category(item, categories) in cats
        in_groups = any(item["id"] in g["members"] for g in groups if g["id"] in mode.get("groups", []))
        if in_cat or item["id"] in mode.get("items", []) or in_groups:
            add(item)
    for (kind, name), cat in categories.items():
        if cat in cats:
            add({"id": None, "display_name": name, "target": name, "item_type": kind,
                 "block_type": "close" if kind == "app" else None, "app_path": None, "notify": None})
    for extra in mode.get("extra", []):
        add({"id": None, "display_name": extra["name"], "target": " ".join(extra["targets"]),
             "item_type": extra["kind"], "block_type": extra.get("block_type") or ("close" if extra["kind"] == "app"
                                                                                  else None),
             "app_path": extra.get("app_path"), "notify": None})
    return out


def describe(mode: dict, category_names: dict[str, str]) -> str:
    parts = [category_names.get(c, c) for c in mode.get("categories", [])]
    n = len(mode.get("items", [])) + len(mode.get("groups", [])) + len(mode.get("extra", []))
    if n:
        parts.append(f"{n} more")
    return "Blocks: " + (" + ".join(parts) if parts else "nothing extra")
