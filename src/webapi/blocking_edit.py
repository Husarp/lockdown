"""Api for Blocking > Add / Edit (BLUEPRINT 3.2, R20): the suggestions under the search box, reading one item to edit it,
adding / merging / saving an item, and disabling or enabling one.

Everything that changes the blocklist goes through the agent's Draft (webapi/draft.py `run_edit`), so what loosens a block
is judged where it always was - `antibypass.draft_changes` when the Draft saves - and nothing here compares old with new:
an edit that loosens (a looser rule on an item that is already there, a lighter way, disabling, deleting) comes back as
`{needs_challenge, ticket}` and nothing is applied until Python has checked the challenge. Adding something new, a
stricter rule and a stricter way are free.

The page is untrusted, so `draft_submit_item` takes what the owner typed and picked - never rule dicts or a block type:
the rule FORMS are read again by `rule_forms` (the same code as `rules_validate`), the ways are checked against the kind
(a program file can only be closed or minimised, Close and Minimize exclude each other ...), the address is tidied, a
known site brings its other host names and Windows' own programs (and Lockdown) are refused.
"""
import ntpath

import block_method
import rule_forms
import search
from blocker import hosts
from blocker.apps import (FLAGS as APP_FLAGS, PROTECTED, block_flags, exe_name, in_folder, is_hosted, make_block_type,
                          typed_path)
from blocker.site_block import FLAGS as SITE_FLAGS, make_site_block_type, site_flags
from gui.draft import RULE_FIELDS
from importer.popular import POPULAR_SITES
from paths import APP_DIR

from . import categories
from . import core
from . import draft as drafts
from . import shared
from .core import ApiError

SUGGEST_KINDS = ("all", "sites", "apps")
SUGGEST_LIMIT = 8               # the dropdown under the search box
MAX_LIST = 60                   # the picker's list
MAX_QUERY = 100
MAX_TARGET = 400                # an address, a file name or a full path
MAX_NAME = 80
MAX_FORMS = 20


class _Invalid(Exception):
    """Something the owner has to fix: {rule_type, field, message} for each, sent back as the answer (not an error)."""

    def __init__(self, errors):
        super().__init__(errors[0]["message"])
        self.errors = errors


def _bad(message: str, field: str = "target") -> _Invalid:
    return _Invalid([{"rule_type": None, "field": field, "message": message}])


def _id(value, what="item") -> int:
    if type(value) is not int:
        raise ApiError(f"Bad request: a {what} number expected.")
    return value


# ---------------------------------------------------------------- known sites

@core.internal
def popular_hosts(host: str) -> tuple[str, list[str]] | None:
    """(name, all its host names) of a popular site this host belongs to (gui/target_picker.popular_hosts)."""
    for sites in POPULAR_SITES.values():
        for name, names in sites.items():
            if host in names:
                return name, names
    return None


@core.internal
def guess_name(host: str) -> str:
    entry = popular_hosts(host)
    return entry[0] if entry else host.split(".")[-2].capitalize()


POPULAR = [(name, names[0]) for sites in POPULAR_SITES.values() for name, names in sites.items()]


# ---------------------------------------------------------------- suggestions

def _query(value) -> str:
    if not isinstance(value, str):
        raise ApiError("Bad request: the search must be text.")
    return value.strip()[:MAX_QUERY]


def _sites(db) -> list[tuple]:
    """(name, host, mine, group) for every site worth suggesting: the sites you used before (mine) first, then the popular
    ones, one entry per host."""
    seen, out = set(), []
    for h in db.history():
        if h["hostname"] not in seen:
            seen.add(h["hostname"])
            out.append((h["display_name"], h["hostname"], True, 0))
    for name, host in POPULAR:
        if host not in seen:
            seen.add(host)
            out.append((name, host, False, 1))
    return out


@core.read
def suggest(ctx, query, kind="all", limit=SUGGEST_LIMIT):
    """What fits the text typed, best first: your earlier sites (mine), the popular sites and the installed apps / Steam
    games, ranked like every search (every word found, small typos fine, exact first - search.rank). The dropdown under
    the search box asks for 8; the picker's list asks for up to 60 and may say `kind` "sites" or "apps". An empty text
    lists them in a steady order (mine, apps, popular).
    -> [{kind: "site", name, host, mine} | {kind: "app", name, exe, path, steam, running}]"""
    text = _query(query)
    if kind not in SUGGEST_KINDS:
        raise ApiError("Unknown kind.")
    if type(limit) is not int or not 1 <= limit <= MAX_LIST:
        raise ApiError("Bad request: how many.")
    rows = []
    if kind != "apps":
        for i, (name, host, mine, group) in enumerate(_sites(ctx.db)):
            score = search.score(text, f"{name} {host}") if text else 0
            if score is not None:
                rows.append((score, group if text else (0 if mine else 2), i,
                             {"kind": "site", "name": name, "host": host, "mine": mine}))
    if kind != "sites":
        for i, app in enumerate(shared._catalog(ctx).search("all", text, limit)["apps"]):
            score = search.score(text, f"{app['name']} {app['exe']}" + (" steam" if app["steam"] else "")) if text else 0
            rows.append((score or 0, 2 if text else 1, i, {"kind": "app", "name": app["name"], "exe": app["exe"],
                                                           "path": app["path"], "steam": app["steam"],
                                                           "running": app["running"]}))
    rows.sort(key=lambda r: r[:3])
    return [r[3] for r in rows[:limit]]


@core.ui
def suggest_clear_history(ctx):
    """"Clear my suggestions": forget the sites you added before. Ungated - it blocks nothing."""
    ctx.db.clear_history()


# ---------------------------------------------------------------- reading one item

def _ways_of(item: dict) -> list[str]:
    """The flags of the item's way, in the order the page lists them."""
    if item["item_type"] == "site":
        flags = site_flags(item.get("block_type"))
        return [f for f in SITE_FLAGS if f in flags]
    flags = block_flags(item.get("block_type"))
    return [f for f in APP_FLAGS if f in flags]


def _rule_view(rule: dict) -> dict:
    return {k: rule[k] for k in RULE_FIELDS if rule.get(k) is not None}


def _item_view(d, item: dict, now) -> dict:
    kind = block_method.kind(item)
    groups = [{"id": g["id"], "name": g["name"], "chose": block_method.group_way(g, kind) is not None}
              for g in d.groups_of(item["id"])]
    targets = item["target"].split()
    return {"id": item["id"], "name": item["display_name"], "kind": item["item_type"], "target": targets[0] if targets else "",
            "targets": targets, "source": item.get("source"), "ways": _ways_of(item), "app_path": item.get("app_path"),
            "notify": item.get("notify"), "disabled": bool(item.get("disabled")), "hosted": is_hosted(item["target"]),
            "rules": [_rule_view(r) for r in item["rules"]], "groups": groups, "unsaved": d.is_unsaved(item["id"]),
            "now": now.isoformat(timespec="seconds")}


@core.read
def item_get(ctx, item_id=None, target=None):
    """One item from the draft (what Edit shows): {id, name, kind: site | app | category, target, targets, ways: [flag],
    app_path, notify, disabled, hosted, rules: [rule], groups: [{id, name, chose}], unsaved, now}. `chose`: that group
    has its own way of blocking its members, which is used instead of the item's when the group blocks it.
    Ask by `item_id`, or by `target` (a host name or an .exe: null when nothing on the list covers it) - the way "Block
    it..." from another page finds out whether to open Edit instead."""
    d = drafts.draft_of(ctx)
    with drafts.lock:
        d.refresh_if_clean()
        if item_id is not None:
            item = d.items.get(_id(item_id))
            if item is None:
                raise ApiError("That item is already gone.")
        else:
            if not isinstance(target, str) or len(target) > MAX_TARGET:
                raise ApiError("Bad request: a site or an app.")
            text = target.strip().strip('"').lower()
            if "\\" in text or "/" in text:
                text = exe_name(text)                       # (a full path: it is the file that is on the list)
            item = d.find_item(text) if text else None
            if item is None:
                return None
        return _item_view(d, item, ctx.now())


# ---------------------------------------------------------------- reading what the page sends

def _name(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError("Bad request: the name must be text.")
    text = " ".join(value.split())
    if len(text) > MAX_NAME:
        raise _bad(f"A name can be at most {MAX_NAME} letters.", "name")
    return text


def _flag_set(value, allowed) -> set | None:
    """The ways the page ticked (None: it sent none)."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(w, str) and w in allowed for w in value):
        raise ApiError("Bad request: how it is blocked.")
    return set(value)


def _site_way(flags: set) -> str:
    if not flags:
        raise _bad("Tick at least one of Can't load it / Close the tab / Go back.", "ways")
    if {"close", "back"} <= flags:
        raise _bad("Close the tab and Go back do the same thing: pick one.", "ways")
    return make_site_block_type(flags)


def _app_way(flags: set, hosted: bool) -> str:
    """The block type for an app, a program file or a category. A program file runs inside java / python / cmd, which other
    programs use too: only closed or minimised (blocker.apps.hosted_way)."""
    if hosted:
        flags = flags - {"internet", "background"}
        if not flags & {"close", "minimize"}:
            raise _bad("Tick Close app or Minimize.", "ways")
    elif not flags - {"background"}:
        raise _bad("Tick at least one of Close app / Minimize / Block internet.", "ways")
    if {"close", "minimize"} <= flags:
        raise _bad("Close app and Minimize do the same thing: pick one.", "ways")
    if "close" not in flags:
        flags = flags - {"background"}           # (the background processes go with Close app)
    return make_block_type(flags)


def _default_app_ways(ctx, exe: str) -> set:
    """What an app is closed with when the page says nothing: Close app - and for a Steam game also its background
    processes, a game can run from several exes or a launcher (gui/target_picker._fill_app)."""
    games = shared._catalog(ctx).search("games", exe, 5)["apps"]
    return {"close", "background"} if any(a["exe"] == exe for a in games) else {"close"}


def _clean_path(path: str | None) -> str | None:
    """Where a program is, tidied so a path can't hide where it points (the check on Lockdown's own folder reads this):
    slashes turned round, "." and ".." resolved. It has to be a full path on a drive or on a network share (the old window
    took those too); a relative path, a "device" path or a character a file name can't have is refused."""
    if not path:
        return None
    drive, rest = ntpath.splitdrive(ntpath.normpath(path.strip().strip('"').strip().replace("/", "\\")))
    on_drive = len(drive) == 2 and drive[1] == ":" and drive[0].isascii() and drive[0].isalpha()
    on_share = drive.startswith("\\\\") and drive.count("\\") == 3 and drive[2] not in "?."
    if not (on_drive or on_share) or not rest.startswith("\\"):
        raise _bad("Write the program's full path, like C:\\Games\\Foo\\Foo.exe - or just its file name.")
    if any(c in '<>"|?*:' or ord(c) < 32 for c in rest):
        raise _bad("That path has a character a file name can't have.")
    return drive + rest


def _new_target(ctx, spec: dict) -> dict:
    """The thing to add, checked: {kind, targets, name, source, block_type, app_path}. Raises _Invalid."""
    kind = spec.get("kind")
    if kind not in ("site", "app", "category"):
        raise ApiError("Bad request: a site, an app or a category.")
    text = spec.get("target")
    if text is not None and (not isinstance(text, str) or len(text) > MAX_TARGET):
        raise ApiError("Bad request: the site or app.")
    text = (text or "").strip()
    if not text:
        raise _bad("Pick a site, an app or a category first.")
    name = _name(spec.get("name"))
    flags = _flag_set(spec.get("ways"), SITE_FLAGS if kind == "site" else APP_FLAGS)
    if kind == "site":
        try:
            host = hosts.normalize_host(text)
        except ValueError:
            raise _bad(f"Write a site like reddit.com ({text!r} isn't one).") from None
        entry = popular_hosts(host)          # a known site: all of its host names
        return {"kind": "site", "targets": list(entry[1]) if entry else [host], "name": name or guess_name(host),
                "source": "popular" if entry else "manual", "block_type": _site_way(flags if flags is not None else {"dns"}),
                "app_path": None}
    if kind == "category":
        found = next((c for c in categories.load(ctx.db) if c["key"] == text), None)
        if found is None:
            raise _bad("That category doesn't exist (any more).")
        return {"kind": "category", "targets": [found["key"]], "name": name or found["name"], "source": "category",
                "block_type": _app_way(flags if flags is not None else {"close"}, False), "app_path": None}
    path = spec.get("app_path")
    if path is not None and (not isinstance(path, str) or len(path) > MAX_TARGET):
        raise ApiError("Bad request: the app's path.")
    exe = exe_name(text)
    path = _clean_path(path or typed_path(text))
    if path and exe_name(path) != exe:
        path = None                          # (a path of some other file says nothing about this one)
    hosted = is_hosted(exe)
    if not exe or not (exe.endswith(".exe") or hosted):
        raise _bad("Write the program's file name, like discord.exe - or use Browse apps.")
    if exe in PROTECTED or in_folder(path, str(APP_DIR).lower()):
        raise _bad(f"{exe} is part of Windows (or Lockdown) and can't be blocked.")
    return {"kind": "app", "targets": [exe], "name": name or exe.rsplit(".", 1)[0].title(),
            "source": "app-browser" if spec.get("picked") is True else "manual",
            "block_type": _app_way(flags if flags is not None else _default_app_ways(ctx, exe), hosted),
            "app_path": path}


def _stored_end(d, item_id) -> str | None:
    """When the item's running temporary block ends, as saved - what "keep" means. Only the saved end counts: the page's
    own idea of it (the form's keep_until) could be any time, and "keep" with an end a minute short would shorten the
    block for free (rule_looser lets a minute through) again and again."""
    saved = d.saved_items.get(_id(item_id))
    rule = next((r for r in (saved or {}).get("rules", ()) if r["rule_type"] == "temporary"), None)
    return rule.get("temp_until") if rule else None


def _rules(ctx, spec: dict, d=None) -> list[dict]:
    """The rule dicts for the forms the page sent (rule_forms is the authority). Raises _Invalid. `d`: the draft, when an
    item is being edited - its "keep" takes the end from the saved block; adding something new has none to keep."""
    forms = spec.get("rules", [])
    if not isinstance(forms, list) or len(forms) > MAX_FORMS:
        raise ApiError("Bad request: rules must be a list of forms.")
    end = _stored_end(d, spec.get("id")) if d is not None else None
    forms = [{**f, "keep_until": end} if isinstance(f, dict) and f.get("rule_type") == "temporary"
             and f.get("choice") == "keep" else f for f in forms]
    found, errors = rule_forms.check_forms(forms, False, ctx.now(), False)
    if errors:
        raise _Invalid(errors)
    return found


def _merge(old: list[dict], new: list[dict]) -> list[dict]:
    """Adding something that is already on the list: keep the blockers it has, and let the ones just ticked replace those
    of the same kind (an item has one rule per kind). A replacement that is looser than what it replaces is a loosening
    like any other - the Draft asks for the challenge when it saves."""
    merged = {r["rule_type"]: r for r in old}
    merged.update({r["rule_type"]: r for r in new})
    return list(merged.values())


# ---------------------------------------------------------------- adding, merging, saving

def _add(d, spec: dict, target: dict, rules: list[dict]) -> dict:
    existing = d.find_item(target["targets"][0])
    if existing:
        name = existing["display_name"]
        if not rules:       # nothing ticked: open it for editing, its blockers loaded
            return {"result": "opened_existing", "id": existing["id"], "name": name,
                    "notice": f"{name} is already on the list - its blockers are loaded, change them and Save."}
        d.set_rules(existing["id"], _merge(existing["rules"], rules))
        note = " (it is disabled - enable it to apply them)" if existing.get("disabled") else ""
        return {"result": "merged", "id": existing["id"], "name": name,
                "notice": f"{name} was already on the list - blockers merged{note}"}
    if not rules:
        raise _Invalid([{"rule_type": None, "field": "rules", "message": "Tick at least one blocker."}])
    item = d.add_item(target["name"], target["targets"], target["source"], target["kind"], target["block_type"],
                      target["app_path"])
    d.set_rules(item["id"], rules)
    saved = d.find_item(target["targets"][0])            # (with Auto-save it has its real id now)
    return {"result": "added", "id": (saved or item)["id"], "name": target["name"],
            "notice": f"{target['name']} blocker added"}


def _edit(d, spec: dict, rules: list[dict]) -> dict:
    item_id = _id(spec.get("id"))
    item = d.items.get(item_id)
    if item is None:
        raise ApiError("That item is already gone.")
    name = _name(spec.get("name")) or item["display_name"]
    flags = _flag_set(spec.get("ways"), SITE_FLAGS if item["item_type"] == "site" else APP_FLAGS)
    if flags is not None:
        item["block_type"] = (_site_way(flags) if item["item_type"] == "site"
                              else _app_way(flags, is_hosted(item["target"])))
    if not rules and not d.groups_of(item_id):
        # nothing left on it and no group to carry it: it goes (Draft.set_rules) - a removal, so it is guarded
        d.set_rules(item_id, [], name)
        return {"result": "deleted", "id": item_id, "name": name, "notice": f"{name} removed"}
    d.set_rules(item_id, rules, name)
    return {"result": "saved", "id": item_id, "name": name, "notice": f"{name} saved"}


@core.loosen
def draft_submit_item(ctx, spec):
    """Add an item, or save the one being edited, over the draft.
    spec: {mode: "new" | "edit", rules: [form per ticked blocker (rule_forms)], name, ways: [flag]} and
      new:  kind "site" | "app" | "category", target (the address, the program's file name or path, the category's key),
            app_path, picked (chosen from the app list rather than typed);
      edit: id (the target itself can't change).
    -> {dirty, autosave, change_summary, result: {result: added | merged | opened_existing | saved | deleted | invalid,
       id, name, notice | errors: [{rule_type, field, message}]}}. A mistake the owner can fix is `invalid` with its errors
    (rule_type null: the target, ways or "rules" as a whole), not an error. Something already on the list gets the new
    blockers merged into it; what loosens it while Anti-Bypass is locked comes back as {needs_challenge, ticket} and the
    old blockers stay until the challenge is passed. Python re-checks everything - nothing the page says is trusted: a
    temporary blocker's "keep" keeps the SAVED end of the running block (the form's keep_until is not read; adding has none)."""
    if not isinstance(spec, dict) or spec.get("mode") not in ("new", "edit"):
        raise ApiError("Bad request: new or edit.")
    try:
        rules = _rules(ctx, spec) if spec["mode"] == "new" else None
        target = _new_target(ctx, spec) if spec["mode"] == "new" else None

        def mutate(d):
            d.refresh_if_clean()
            return _add(d, spec, target, rules) if target else _edit(d, spec, _rules(ctx, spec, d))
        return drafts.run_edit(ctx, mutate)
    except _Invalid as e:
        d = drafts.draft_of(ctx)
        return core.ok({**drafts.state_of(d), "result": {"result": "invalid", "errors": e.errors}})


@core.loosen
def draft_set_disabled(ctx, item_id, disabled):
    """Disable an item (it keeps its blockers, none of them apply, and it moves to Overview > Disabled) or enable it again.
    Disabling is a loosening: guarded. Enabling only blocks more."""
    item_id = _id(item_id)
    if type(disabled) is not bool:
        raise ApiError("Bad request: on or off expected.")

    def edit(d):
        d.refresh_if_clean()
        item = d.items.get(item_id)
        if item is None:
            raise ApiError("That item is already gone.")
        d.set_disabled(item_id, disabled)
        return {"id": item_id, "name": item["display_name"], "disabled": disabled}
    return drafts.run_edit(ctx, edit)
