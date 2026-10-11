"""Api for the shared pieces of Screen Time, Blocking, Modes and Settings: the categories (Productive / Neutral / Games /
Distracting and your own), the right-click menu of an app or a site, and "Show in Explorer".

Categories feed the modes that block a whole category, so changing which category an app or site is in - or adding or
deleting one - is a loosening change: while Anti-Bypass is locked it goes through the challenge, in either direction
(`ctx.guard.request`). Only recolouring is free (a colour blocks nothing). The page never says "confirmed": the change
is a closure built here from validated input, kept under a ticket until Python has checked the challenge.

The categories live in the same setting as the old window's (gui/categories.py KEY, same JSON), so both windows see the
same list. That module imports tkinter, so the little bit of it we need is repeated here; a test keeps the two equal.
"""
import json
import os
import re
import subprocess
import sys

from blocker import hosts
from blocker.apps import PROTECTED, exe_name, make_block_type

from . import core
from .core import ApiError
from . import shared

KEY = "stats.categories"      # JSON {"custom": [{"key", "name", "color"}], "colors": {key: "#rrggbb"}}
# key, name, (light colour, dark colour) - as gui/theme.py SUCCESS / MUTED / DANGER
BUILTIN = [("productive", "Productive", ("#1E7A46", "#27AE60")), ("neutral", "Neutral", ("#5E6874", "#8D9AA8")),
           ("games", "Games", ("#8E6CEF", "#8E6CEF")), ("distracting", "Distracting", ("#C0392B", "#E05A44"))]
BUILTIN_KEYS = tuple(k for k, _n, _c in BUILTIN)
NEW_COLORS = ["#8E6CEF", "#2F9FD8", "#D4A017", "#E0559B", "#20B2AA", "#9C6B3F"]
MAX_NAME = 30
MAX_TARGET = 260
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


# ---------------------------------------------------------------- the categories (plain Python)

def _config(db) -> dict:
    try:
        cfg = json.loads(db.get_setting(KEY, "{}")) or {}
    except ValueError:
        return {}
    return cfg if isinstance(cfg, dict) else {}


@core.internal
def load(db) -> list[dict]:
    """[{key, name, color, color_light, builtin}] - built-in ones first. `color` is for the dark themes, `color_light`
    for the light one (the same for a colour you chose). An own category with a built-in one's key is left out."""
    cfg = _config(db)
    colors = cfg.get("colors", {})
    out = [{"key": k, "name": n, "color": colors.get(k, c[1]), "color_light": colors.get(k, c[0]), "builtin": True}
           for k, n, c in BUILTIN]
    taken = set(BUILTIN_KEYS)
    for c in cfg.get("custom", []):
        if c["key"] not in taken:
            color = colors.get(c["key"], c["color"])
            out.append({"key": c["key"], "name": c["name"], "color": color, "color_light": color, "builtin": False})
    return out


def _add(db, name: str, color: str) -> str:
    cfg = _config(db)
    taken = set(BUILTIN_KEYS) | {c["key"] for c in cfg.get("custom", [])}
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "category"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    cfg.setdefault("custom", []).append({"key": key, "name": name, "color": color})
    db.set_setting(KEY, json.dumps(cfg))
    return key


def _set_color(db, key: str, color: str):
    cfg = _config(db)
    cfg.setdefault("colors", {})[key] = color
    db.set_setting(KEY, json.dumps(cfg))


def _remove(db, key: str):
    """Delete an own category; its apps and sites go back to their default category."""
    cfg = _config(db)
    cfg["custom"] = [c for c in cfg.get("custom", []) if c["key"] != key]
    cfg.get("colors", {}).pop(key, None)
    db.set_setting(KEY, json.dumps(cfg))
    db.write("DELETE FROM categories WHERE category = ?", (key,))


# ---------------------------------------------------------------- checking what the page sends

def _color(value) -> str:
    if not isinstance(value, str) or not _HEX.match(value):
        raise ApiError("Pick a colour as #rrggbb.")
    return value.lower()


def _name(value) -> str:
    if not isinstance(value, str):
        raise ApiError("Bad request: the name must be text.")
    text = " ".join(value.split())
    if not text:
        raise ApiError("Give the category a name.")
    if len(text) > MAX_NAME or _CONTROL.search(text):
        raise ApiError(f"A category name can be at most {MAX_NAME} letters.")
    return text


def _free_name(db, text: str):
    if text.lower() in {c["name"].lower() for c in load(db)}:
        raise ApiError(f"There is already a category called {text}.")


def _find(db, key) -> dict:
    found = next((c for c in load(db) if c["key"] == key), None) if isinstance(key, str) else None
    if found is None:
        raise ApiError("That category doesn't exist (any more).")
    return found


def _target(kind, name) -> tuple[str, str]:
    """('app', 'chrome.exe') / ('site', 'example.com'): what the categories table keys on (lower case)."""
    if kind not in ("app", "site"):
        raise ApiError("Bad request: an app or a site.")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_TARGET or _CONTROL.search(name):
        raise ApiError("Bad request: no such app or site.")
    text = exe_name(name) if kind == "app" else name.strip().lower()
    if not text or (kind == "site" and " " in text):
        raise ApiError("Bad request: no such app or site.")
    return kind, text


# ---------------------------------------------------------------- reads

@core.read
def categories_list(ctx):
    """[{key, name, color, color_light, builtin}]: Productive, Neutral, Games, Distracting, then your own."""
    return load(ctx.db)


@core.read
def blocklist_brief(ctx):
    """What a picker needs from the blocklist: {groups: [{id, name, count, disabled}], items: [{id, name, target, kind,
    disabled}]}, both sorted by name. (The saved list - not the unsaved draft.)"""
    db = ctx.db
    return {"groups": sorted(({"id": g["id"], "name": g["name"], "count": len(g["members"]),
                               "disabled": bool(g.get("disabled"))} for g in db.list_groups()),
                             key=lambda g: g["name"].lower()),
            "items": [{"id": i["id"], "name": i["display_name"], "target": i["target"], "kind": i["item_type"],
                       "disabled": bool(i.get("disabled"))} for i in db.list_items()]}


@core.read
def appmenu_info(ctx, kind, name):
    """What the right-click menu of an app or site needs: {category: its key, protected: Windows' own (can't be
    blocked), groups: [{id, name}] it could be added to}."""
    import stats
    kind, name = _target(kind, name)
    db = ctx.db
    keys = {c["key"] for c in load(db)}
    chosen = stats.category_of(kind, name, db.categories(), db.list_items())
    return {"category": chosen if chosen in keys else "neutral", "protected": kind == "app" and name in PROTECTED,
            "groups": [{"id": g["id"], "name": g["name"]} for g in _groups(ctx)]}


# ---------------------------------------------------------------- recolouring (free) and the guarded changes

@core.ui
def category_set_color(ctx, key, color):
    """Change a category's colour. Ungated: a colour blocks nothing."""
    _find(ctx.db, key)
    _set_color(ctx.db, key, _color(color))


@core.loosen
def category_add(ctx, name, color):
    """Make a category of your own -> {key, name, color}. Needs the challenge while Anti-Bypass is locked."""
    name, color = _name(name), _color(color)
    _free_name(ctx.db, name)

    def action():
        _free_name(ctx.db, name)
        return {"key": _add(ctx.db, name, color), "name": name, "color": color}
    return ctx.guard.request([f"Add the {name} category"], action)


@core.loosen
def category_remove(ctx, key):
    """Delete a category of your own (a built-in one can't be): the apps and sites in it go back to their default."""
    found = _find(ctx.db, key)
    if found["builtin"]:
        raise ApiError("The built-in categories can't be deleted.")

    def action():
        _find(ctx.db, key)
        _remove(ctx.db, key)
        return {"key": key}
    return ctx.guard.request([f"Delete the {found['name']} category (its apps and sites go back to their default)"], action)


@core.loosen
def category_assign(ctx, kind, name, key):
    """Put an app (its .exe) or a site (its host name) in a category. Guarded in either direction while locked: the
    modes that block a category block whatever is in it."""
    kind, name = _target(kind, name)
    found = _find(ctx.db, key)

    def action():
        _find(ctx.db, key)
        ctx.db.set_category(kind, name, key)
        return {"kind": kind, "name": name, "category": key}
    return ctx.guard.request([f"Move {name} to the {found['name']} category"], action)


@core.loosen
def category_add_and_assign(ctx, kind, name, cat_name, color):
    """"New category..." in the menu: make the category and put the app / site in it, as one change."""
    kind, name = _target(kind, name)
    cat_name, color = _name(cat_name), _color(color)
    _free_name(ctx.db, cat_name)

    def action():
        _free_name(ctx.db, cat_name)
        key = _add(ctx.db, cat_name, color)
        ctx.db.set_category(kind, name, key)
        return {"kind": kind, "name": name, "category": key, "color": color, "category_name": cat_name}
    return ctx.guard.request([f"Add the {cat_name} category and move {name} into it"], action)


# ---------------------------------------------------------------- Add to group (the draft) and Show in Explorer

def _shared_draft():
    """webapi/draft.py (the agent's one Draft with its lock, pushes and auto-save), or None while that isn't there."""
    try:
        from . import draft
    except ImportError:
        return None
    return draft


def _groups(ctx) -> list[dict]:
    shared_draft = _shared_draft()
    if shared_draft is not None:
        d = shared_draft.draft_of(ctx)
        with shared_draft.lock:
            d.refresh_if_clean()
            groups = list(d.groups.values())                     # (the unsaved ones too)
    else:
        groups = ctx.db.list_groups()
    return sorted(groups, key=lambda g: g["name"].lower())


def _edit_draft(ctx, mutate):
    """Run `mutate(draft)` on the agent's Draft, so the edit shows up as unsaved there like any other (and saves at once
    with auto-save). Without webapi/draft.py: on a fresh Draft over the saved list, saved at once."""
    shared_draft = _shared_draft()
    if shared_draft is not None:
        return shared_draft.run_edit(ctx, mutate)
    from gui.draft import Draft
    d = Draft(ctx.db)
    result = mutate(d)
    if d.dirty:
        d.save()
    return result


def _norm(path: str) -> str:
    return os.path.normcase(path.replace("/", "\\")).lower()


def _known_path(ctx, path) -> str | None:
    """`path` as the catalog / the open windows / one of your blocked apps knows it, or None for any other path."""
    if not isinstance(path, str) or not path.strip() or len(path) > 520 or _CONTROL.search(path):
        return None
    exe, want = exe_name(path), _norm(path.strip())
    if not exe:
        return None
    for app in shared._catalog(ctx).search("all", exe)["apps"]:
        if app["exe"] == exe and app["path"] and _norm(app["path"]) == want:
            return app["path"]
    for row in ctx.db.conn.execute("SELECT app_path FROM blocked_items WHERE item_type = 'app' AND app_path != ''"):
        if row[0] and _norm(row[0]) == want:
            return row[0]
    return None


@core.tighten
def draft_add_to_group(ctx, target, gid):
    """Put an app or site in one of your groups, so the group's shared rules cover it (it goes on the blocklist first if
    it isn't there). `target`: {kind: app | site, name, display?, path?}. Adding can only block more, so it is free; the
    draft decides when it is saved (auto-save, or Save changes). -> {added, notice}."""
    if not isinstance(target, dict):
        raise ApiError("Bad request: no such app or site.")
    kind, name = _target(target.get("kind"), target.get("name"))
    if kind == "app" and name in PROTECTED:
        raise ApiError(f"{name} can't be blocked (Part of Windows).")
    if kind == "site":
        try:
            name = hosts.normalize_host(name)             # (the same tidy-up as typing it into Blocking > Add)
        except ValueError:
            raise ApiError("That isn't a website address Lockdown can block.") from None
    display = target.get("display")
    display = " ".join(display.split())[:80] if isinstance(display, str) and display.strip() else name
    path = _known_path(ctx, target.get("path")) if kind == "app" else None
    make_type = make_block_type(["close"]) if kind == "app" else None

    def mutate(draft):
        draft.refresh_if_clean()
        group = draft.groups.get(gid) if isinstance(gid, int) and not isinstance(gid, bool) else None
        if group is None:
            raise ApiError("That group doesn't exist (any more).")
        item = draft.find_item(name) or draft.add_item(display, [name], "manual", kind, make_type, path)
        members = dict(group["members"])
        if item["id"] in members:
            return {"added": False, "notice": f"{display} is already in {group['name']}"}
        members[item["id"]] = {}
        draft.set_group(gid, group["name"], group["rules"], members)
        return {"added": True, "notice": f"{display} added to {group['name']}"}

    reply = _edit_draft(ctx, mutate)
    data = reply.get("data") if isinstance(reply, core.Envelope) and reply.get("ok") else None
    if isinstance(data, dict) and isinstance(data.get("result"), dict):      # run_edit: {dirty, autosave, ..., result}
        return {**data["result"], **{k: v for k, v in data.items() if k != "result"}}
    return reply


def _open_explorer(path: str):
    """Windows' Explorer with the file selected (thin glue; replaced in the tests)."""
    if sys.platform != "win32":
        raise ApiError("Show in Explorer only works on Windows.")
    subprocess.Popen(["explorer", "/select,", path])      # (explorer wants the comma glued to /select)


@core.ui
def reveal_in_explorer(ctx, path):
    """Show a program's file in Explorer. Only a path Lockdown already knows (the app list, an open window, one of your
    blocked apps) - the page can't make Explorer or anything else open an arbitrary path."""
    known = _known_path(ctx, path)
    if known is None:
        raise ApiError("Lockdown doesn't know that file.")
    _open_explorer(known)
