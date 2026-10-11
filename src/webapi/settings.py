"""Api for the Settings page (BLUEPRINT 3.10): when limits reset, the emergency unlock, backup and export, the daily goal,
and the switch that picks the new window.

The page is untrusted. It sends plain values (a time, a few numbers, a token); Python validates every one and decides -
with the same functions the old window used - whether a change loosens anything. What loosens waits under a challenge
ticket (ctx.guard.request) and the page only gets the ticket; nothing here reads a "confirmed" flag.

- Reset time: a LATER time or "From the end of the running day" is free; moving it BACK and starting a fresh limit day
  now (the limits start over at once) needs the challenge (`reset_is_looser`, as in gui/settings.py).
- Emergency unlock: more uses, longer, another period (per day -> week / month is free), a new kind or turning it on
  loosens (`antibypass.emergency_looser`); fewer, shorter, removing a kind or turning it off is free. The page sends
  the whole picture; kinds it names with an id Python does not know get a fresh id (a removed kind's count never
  comes back, and a made-up id can't claim a count).
- Import: ALWAYS goes through the guard (it replaces every block and setting). The page never names a file: the owner
  picks it in the native Open dialog (`backup_import_pick`), Python keeps the path and a hash of the bytes under a
  one-time token, shows what would change, and `backup_import_apply(token)` reads the file again - a file that changed
  since the preview is refused, both before the challenge and again right before it is restored. The file is checked
  for shape first (a damaged one must not half-restore), and an import never switches Lockdown off or on (that is
  `app.off`, which has its own gated switch).
- Export / CSV: the owner picks the place in the native Save dialog; the page never sends a path.
- Cosmetic (ui): the daily goal, the look (webapi/shell.py), Reduce motion, and the new-window switch (`ui.engine`).
  Reduce motion is saved with the generic `ui_setting` (allow-listed below).
"""
import hashlib
import json
import secrets
import time

import antibypass
import backup
import emergency
import reminders
from ui_theme import ENGINE_KEY, engine_of   # (= main.ENGINE_KEY; the default window is ui_theme.DEFAULT_ENGINE)
from rules import DAY_NAMES, RESET_KEY, apply_reset_now, change_reset, parse_hhmm, reset_is_looser

from . import categories, core, draft as drafts, shell
from .core import ApiError
from .dashboard import DEFAULT_GOAL_HOURS, GOAL_KEY

ENGINES = ("web", "legacy")
MOTION_KEY = "ui.reduce_motion"     # "1": no transitions or animations (the page sets data-motion="off")
core.allow_ui_settings(MOTION_KEY)

GOAL_HOURS = range(0, 13)           # Off, 1 h ... 12 h
NEW_KIND = {"uses": 1, "per": "week", "minutes": 20}      # "+ Add another kind" starts here
IMPORT_TTL_SEC = 600                # a picked backup file is forgotten after 10 minutes
MAX_BACKUP_BYTES = 32 * 1024 * 1024
PAGES = ("dashboard", "blocking", "antibypass", "screentime", "netlog", "modes", "reminders", "notifications",
         "settings", "about")
FILE_FILTER = "Lockdown backup (*.json)"


def _iso(value):
    return value.isoformat(timespec="seconds") if value else None


def _short_when(when) -> str:
    """"Mon 12 Oct 00:00" (gui/settings.short_when)."""
    return f"{DAY_NAMES[when.weekday()][:3]} {when.day} {when:%b %H:%M}"


def _poke(ctx):
    """Tell the status worker something changed (the tray, the mute rule and the Emergency button follow at once)."""
    worker = getattr(getattr(ctx, "hub", None), "status_worker", None)
    if worker is not None:
        worker.poke()


def _dirty(ctx, *pages):
    """Tell the pages that show this to read it again the next time they are looked at (or at once, if visible)."""
    publish = getattr(ctx, "publish", None)
    if publish is not None:
        for page in pages:
            publish("dirty", {"page": page}, page)


# ---------------------------------------------------------------- what the page shows

def _reset_info(db, now) -> dict:
    clock = db.limit_clock()
    _start, end = clock.day(now)
    carry = clock.carry_until if clock.carry_until and now < clock.carry_until else None
    note = (f"It runs to {_short_when(carry)} because you changed the reset time - a change never ends the day you "
            f"are in early, and never stretches it twice. {clock.time:%H:%M} applies from then on.") if carry else ""
    return {"time": f"{clock.time:%H:%M}", "ends": _iso(end), "ends_text": _short_when(end), "carry_note": note}


def _emergency_info(db, now) -> dict:
    kinds = [{"id": k["id"], "uses": k["uses"], "per": k["per"], "minutes": k["minutes"],
              "per_text": emergency.PERIODS[k["per"]], "left": left, "allowed": allowed, "resets": _iso(reset),
              "resets_text": _short_when(reset)}
             for k, left, allowed, reset in emergency.left_by_tier(db, now)]
    minutes = sorted({*emergency.MINUTE_OPTIONS, *(k["minutes"] for k in kinds)})
    return {"enabled": emergency.get(db, "emergency.enabled") == "1", "kinds": kinds, "max_kinds": emergency.MAX_TIERS,
            "max_uses": emergency.MAX_USES, "minute_options": minutes, "new_kind": NEW_KIND,
            "periods": list(emergency.PERIODS)}


def _goal(db) -> float:
    try:
        hours = float(db.get_setting(GOAL_KEY, DEFAULT_GOAL_HOURS) or 0)
    except ValueError:
        hours = 0.0
    return int(hours) if hours == int(hours) else hours


@core.read
def settings_get(ctx):
    """Everything the page shows in one answer: {now, look: {theme, accent, scale, accents}, motion, goal, goal_options,
    reset: {time, ends, ends_text, carry_note}, categories, emergency: {enabled, kinds: [{id, uses, per, minutes,
    per_text, left, allowed, resets_text}], ...}, engine: "web" | "legacy"}. Meters are worked out now, so showing the
    page again always shows today's uses."""
    db, now = ctx.db, ctx.now()
    goal = _goal(db)
    return {
        "now": _iso(now), "look": shell.get_theme(ctx), "motion": db.get_setting(MOTION_KEY) == "1",
        "goal": goal, "goal_options": sorted({*GOAL_HOURS, goal}),
        "reset": _reset_info(db, now), "categories": categories.load(db), "emergency": _emergency_info(db, now),
        "engine": engine_of(db.get_setting(ENGINE_KEY)),
    }


# ---------------------------------------------------------------- the goal and the new-window switch (cosmetic)

@core.ui
def set_goal(ctx, hours):
    """The daily screen-time goal: 0 = Off, else 1-12 whole hours. Not part of blocking, so it is free."""
    if type(hours) is not int or hours not in GOAL_HOURS:
        raise ApiError("Pick Off or 1 to 12 hours.")
    ctx.db.set_setting(GOAL_KEY, str(hours))
    _dirty(ctx, "dashboard", "screentime")
    return {"goal": hours}


@core.ui
def ui_engine_set(ctx, value):
    """"Use the new window (preview)": "web" opens the new window the next time Lockdown starts, "legacy" the old one.
    Only these two words are accepted. Nothing changes until the next start."""
    if value not in ENGINES:
        raise ApiError("Pick the new window or the old one.")
    ctx.db.set_setting(ENGINE_KEY, value)
    return {"engine": value}


# ---------------------------------------------------------------- when limits reset

def _time_text(value) -> str:
    if not isinstance(value, str) or len(value) > 5:
        raise ApiError("The time must look like 04:00.")
    try:
        return f"{parse_hhmm(value):%H:%M}"
    except ValueError:
        raise ApiError("The time must look like 04:00.") from None


def _reset_plan(ctx, value):
    """(the time as HH:MM, is it an earlier one?) - or an ApiError for a bad time or the time already in use."""
    db, now = ctx.db, ctx.now()
    text = _time_text(value)
    if text == f"{db.limit_clock().time:%H:%M}":
        raise ApiError("That's already the reset time.")
    saved = db.get_setting(RESET_KEY)
    try:
        change_reset(saved, text, now)
    except ValueError as e:
        raise ApiError(str(e)) from None
    return text, reset_is_looser(saved, text, now)


@core.read
def reset_change_preview(ctx, at):
    """What moving the reset to `at` would do: {time, looser, end, end_text}. `looser` (an earlier time) only tells the
    page to ask "Start now / From {end}"; reset_change_apply works it out again."""
    text, looser = _reset_plan(ctx, at)
    end = ctx.db.limit_clock().day(ctx.now())[1]
    return {"time": text, "looser": looser, "end": _iso(end), "end_text": _short_when(end)}


@core.loosen
def reset_change_apply(ctx, at, start_now=False):
    """Change the reset time. A later time, or an earlier one that takes over when the running limit day ends, is free.
    `start_now` (only meaningful for an earlier time): the running limit day ends now and today's limits start over -
    that loosens, so it needs the challenge. For a later time `start_now` changes nothing: the limits never restart.
    -> {reset} (what the page shows), or {needs_challenge, ticket}."""
    if type(start_now) is not bool:
        raise ApiError("Bad request: yes or no expected.")
    text, looser = _reset_plan(ctx, at)

    def save(value_for):
        db = ctx.db
        db.set_setting(RESET_KEY, value_for(db.get_setting(RESET_KEY), ctx.now()))
        _poke(ctx)
        _dirty(ctx, "dashboard", "blocking", "screentime")
        return {"reset": _reset_info(db, ctx.now())}

    if looser and start_now:
        return ctx.guard.request([f"Start a fresh limit day now ({text} reset)"],
                                 lambda: save(lambda saved, now: apply_reset_now(saved, text, now)))
    return core.ok(save(lambda saved, now: change_reset(saved, text, now)))


# ---------------------------------------------------------------- emergency unlock

def _int(value, low, high, what) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ApiError(f"{what} must be a whole number from {low} to {high}.")
    return value


def _emergency_config(ctx, cfg) -> dict:
    """The page's picture as {enabled, tiers}, checked. A kind keeps its id only when Python has that id now."""
    if not isinstance(cfg, dict) or not set(cfg) <= {"enabled", "tiers"}:
        raise ApiError("Bad request: the emergency settings are not in the expected shape.")
    if type(cfg.get("enabled")) is not bool:
        raise ApiError("Bad request: on or off expected.")
    tiers = cfg.get("tiers")
    if not isinstance(tiers, list) or len(tiers) > emergency.MAX_TIERS:
        raise ApiError(f"At most {emergency.MAX_TIERS} kinds.")
    known, out = {t["id"] for t in emergency.tiers(ctx.db)}, []
    for t in tiers:
        if not isinstance(t, dict):
            raise ApiError("Bad request: a kind is not in the expected shape.")
        per = t.get("per")
        if per not in emergency.PERIODS:
            raise ApiError("Pick per day, per week or per month.")
        tid = t.get("id")
        if not (isinstance(tid, str) and tid in known and all(tid != o["id"] for o in out)):
            tid = emergency.new_id()
        out.append({"id": tid, "uses": _int(t.get("uses"), 1, emergency.MAX_USES, "Uses"), "per": per,
                    "minutes": _int(t.get("minutes"), 1, emergency.MAX_MINUTES, "Minutes")})
    return {"enabled": cfg["enabled"], "tiers": out}


@core.loosen
def emergency_settings_save(ctx, cfg):
    """Save the emergency unlock card: {enabled, tiers: [{id?, uses 1-10, per day|week|month, minutes 1-60}]} (up to 5
    kinds; no id = a new kind). More uses, a longer unlock, another period (except day -> week / month), a new kind or
    turning it on goes through the challenge (antibypass.emergency_looser); fewer, shorter, removing a kind or turning
    it off is saved at once. -> {emergency} or {needs_challenge, ticket}."""
    new = _emergency_config(ctx, cfg)
    old = emergency.config(ctx.db)

    def save():
        db = ctx.db
        db.set_setting("emergency.enabled", "1" if new["enabled"] else "0")
        emergency.save_tiers(db, new["tiers"])
        _poke(ctx)
        _dirty(ctx, "dashboard", "blocking")           # (the Emergency button / card shows or hides)
        return {"emergency": _emergency_info(db, ctx.now())}

    if antibypass.emergency_looser(old, new):
        return ctx.guard.request(["Allow more or longer emergency unlocks"], save)
    return core.ok(save())


# ---------------------------------------------------------------- backup and export

def _window(ctx):
    return getattr(ctx, "main_window", None)


def _dialog_kind(name):
    import webview
    return getattr(getattr(webview, "FileDialog", None), name, None) or getattr(webview, f"{name}_DIALOG")


def _first(result):
    if not result:
        return None
    return result if isinstance(result, str) else result[0]


def _ask_save_path(window, name: str) -> str | None:
    """The file the owner picked in the native Save dialog, or None when cancelled (thin glue: replaced in the tests)."""
    if window is None:
        raise ApiError("The Save dialog isn't available.")
    return _first(window.create_file_dialog(_dialog_kind("SAVE"), save_filename=name, file_types=(FILE_FILTER,)))


def _ask_open_path(window) -> str | None:
    """The backup the owner picked in the native Open dialog, or None when cancelled (replaced in the tests)."""
    if window is None:
        raise ApiError("The Open dialog isn't available.")
    return _first(window.create_file_dialog(_dialog_kind("OPEN"), file_types=(FILE_FILTER,)))


@core.ui
def backup_export(ctx):
    """"Export...": ask where to save (the owner's own dialog - the page never names a path) and write everything that was
    set up as one JSON file. -> {cancelled: true} or {path}."""
    path = _ask_save_path(_window(ctx), f"lockdown-backup-{ctx.now():%Y-%m-%d}.json")
    if not path:
        return {"cancelled": True}
    if not path.lower().endswith(".json"):
        path += ".json"
    try:
        backup.save(ctx.db, path)
    except OSError as e:
        raise ApiError(f"Couldn't save the file: {e}") from None
    return {"path": path}


def _digest(path: str) -> str:
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_BACKUP_BYTES + 1)
    except OSError as e:
        raise ApiError(f"Couldn't read that file ({e}).") from None
    if len(raw) > MAX_BACKUP_BYTES:
        raise ApiError("That file is too big to be a Lockdown backup.")
    return hashlib.sha256(raw).hexdigest()


def _is_text_map(value) -> bool:
    return isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())


def _scalar(value) -> bool:
    """A value a database column takes as it is (what a rule field or a way is stored as)."""
    return value is None or (isinstance(value, (str, int, float)) and not isinstance(value, bool))


def _text_or_none(value) -> bool:
    return value is None or isinstance(value, str)


def _rules_ok(rules) -> bool:
    return isinstance(rules, list) and all(isinstance(r, dict) and isinstance(r.get("rule_type"), str)
                                           and all(_scalar(v) for v in r.values()) for r in rules)


def _group_ok(g) -> bool:
    if not isinstance(g, dict):
        return False
    members, ways = g.get("members", {}), g.get("member_blocks") or {}
    return (isinstance(g.get("name"), str) and _rules_ok(g.get("rules", []))
            and isinstance(members, dict) and all(o is None or isinstance(o, dict) for o in members.values())
            and isinstance(ways, dict) and all(_text_or_none(w) for w in ways.values())
            and _text_or_none(g.get("app_block")) and _text_or_none(g.get("site_block")))


def _break_allow_ok(sets) -> bool:
    """The break settings' "Allowed during breaks" list, when the backup has one: a list of at most
    reminders.BREAK_ALLOW_MAX short names. (A backup from before it existed has none: it reads as the default.)"""
    try:
        b = json.loads(sets.get(reminders.BREAK_KEY) or "null")
    except ValueError:
        return True                                       # (unreadable settings are read as the defaults)
    allow = b.get("allow") if isinstance(b, dict) else None
    return allow is None or (isinstance(allow, list) and len(allow) <= reminders.BREAK_ALLOW_MAX
                             and all(isinstance(e, str) and len(e) <= 260 for e in allow))


def _check_shape(data: dict):
    """A backup that would stop half-way is refused before anything is touched - every field restore() reads, of the
    kind it needs (backup.restore is also one transaction, so a file that still fails changes nothing)."""
    items, groups, cats, sets = data.get("items"), data.get("groups", []), data.get("categories", []), data.get("settings", {})
    ok = (isinstance(items, list) and isinstance(groups, list) and isinstance(cats, list) and _is_text_map(sets)
          and all(isinstance(i, dict) and isinstance(i.get("display_name"), str) and isinstance(i.get("target"), str)
                  and isinstance(i.get("item_type"), str) and "id" in i and _scalar(i["id"])
                  and _rules_ok(i.get("rules", [])) and all(_text_or_none(i.get(k)) for k in
                                                           ("source", "notify", "block_type", "app_path"))
                  for i in items)
          and all(isinstance(c, dict) and all(isinstance(c.get(k), str) for k in ("kind", "name", "category"))
                  for c in cats))
    ok = ok and all(_group_ok(g) for g in groups) and _break_allow_ok(sets)
    if not ok:
        raise ApiError("That backup file is damaged, so it can't be imported.")


def _load_checked(path: str, expected: str | None = None) -> tuple[dict, str]:
    """(the backup's data, the hash of the file). The file is read around the parse, so what was parsed is what was
    hashed; `expected` (the hash from the preview) refuses a file that changed since."""
    before = _digest(path)
    if expected is not None and before != expected:
        raise ApiError("That file has changed since you looked at it. Choose it again.")
    try:
        data = backup.load(path)
    except ValueError as e:
        raise ApiError(str(e)) from None
    if _digest(path) != before:
        raise ApiError("That file changed while it was being read. Choose it again.")
    _check_shape(data)
    return data, before


def _clock() -> float:
    return time.monotonic()


@core.ui
def backup_import_pick(ctx):
    """"Import...": the owner picks a backup in the native Open dialog. Nothing is changed. -> {cancelled: true} or
    {token, name, items, groups, lines} where `lines` is what importing would change (backup.diff); the token is the
    one thing backup_import_apply accepts (it stands for this file as it is right now)."""
    path = _ask_open_path(_window(ctx))
    if not path:
        return {"cancelled": True}
    data, sha = _load_checked(path)
    token = secrets.token_urlsafe(12)
    ctx.import_pending = {"token": token, "path": path, "sha": sha, "at": _clock()}
    return {"token": token, "name": path.replace("\\", "/").rsplit("/", 1)[-1], "items": len(data["items"]),
            "groups": len(data.get("groups", [])), "lines": backup.diff(ctx.db, data)}


@core.loosen
def backup_import_apply(ctx, token):
    """Replace every block and setting with the backup picked in backup_import_pick. ALWAYS goes through the guard (the
    challenge when Anti-Bypass is on). The file is read again by its token: if it changed since the preview, nothing
    happens. -> {items, groups} (what was restored) or {needs_challenge, ticket}."""
    pending = getattr(ctx, "import_pending", None)
    if (not isinstance(token, str) or not pending or not secrets.compare_digest(token, pending["token"])
            or _clock() - pending["at"] > IMPORT_TTL_SEC):
        ctx.import_pending = None
        raise ApiError("Choose the backup file again.")
    ctx.import_pending = None                         # one use
    path, sha = pending["path"], pending["sha"]
    _load_checked(path, sha)                          # (refused here if it changed since the preview)
    name = path.replace("\\", "/").rsplit("/", 1)[-1]

    def restore():
        again, _ = _load_checked(path, sha)           # (the challenge can take minutes: look again right before)
        again["settings"] = {k: v for k, v in again.get("settings", {}).items() if k != antibypass.OFF_KEY}
        backup.restore(ctx.db, again)
        d = drafts.draft_of(ctx)
        with drafts.lock:
            d.discard()                               # (the blocks are the backup's now: reload them everywhere)
        _poke(ctx)
        _dirty(ctx, *PAGES)
        return {"items": len(again["items"]), "groups": len(again.get("groups", []))}

    return ctx.guard.request([f"Import settings from {name} (replaces your blocks and settings)"], restore)
