"""Api for Blocking > Groups (R21): the list of groups, one group for the editor, the member search, and saving a group.

A group is named rules shared by its members. Everything here edits the agent's ONE Draft (webapi/draft.py), so the
checks on what loosens a block are the existing ones and run in Python when the draft is saved:
    antibypass.draft_changes -> group_looser: looser group rules (a raised shared limit ...), a member taken out, a
    member's extra rule removed or relaxed, a group switched off, and ways_looser (the group's "How members are blocked"
    or a member's own way got lighter). Adding a member, an extra rule or a stricter way is free.
The page never says what loosens and never says "confirmed": `draft_set_group` takes what the owner typed - FORMS for the
rules (src/rule_forms.py reads them, same code as Add / Edit), ways as plain flags - checks it all again here, and the
Draft's guard asks for the challenge (a ticket) when the save would loosen something.

Disable / Enable and Remove group are R18's `draft_set_group_disabled` / `draft_remove_group` (webapi/blocking.py).
The group's icon and colour of the designer's mock have no PC data: left out.

Classification: `draft_set_group` is `loosen` (it can take a member out or lighten a way); the rest only read.
"""
import re
from datetime import datetime

import block_method
import rule_forms
import search
from blocker import hosts, site_block
from blocker.apps import FLAGS as APP_FLAGS
from blocker.apps import PROTECTED, exe_name, is_hosted, make_block_type
from gui.draft import RULE_FIELDS
from rules import TIME_FMT, block_end, blocked_text, item_block

from . import blocking, categories, core, shared
from . import draft as drafts
from .core import ApiError

CUSTOMIZABLE = rule_forms.CUSTOMIZABLE          # what a member can be given on top of the group's rules
MAX_NAME = 100
MAX_DISPLAY = 80
MAX_MEMBERS = 500
MAX_PENDING = 100
MAX_NAMES = 8                                   # member names on a list card, then "and N more" (the page adds it)
MAX_FOUND = 30                                  # rows the member search gives back, per kind
NEW_WAYS = {"app": "close", "site": "dns"}      # a new group starts as an item does (close the app / can't load the site)
APP_DEFAULT = "close"                           # what a new app item (made by adding a member) does when blocked
APP_WORDS = (("close", "closed"), ("background", "background processes"), ("minimize", "minimised"),
             ("internet", "internet cut"))
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _id(value, what="group") -> int:
    if type(value) is not int:
        raise ApiError(f"Bad request: a {what} number expected.")
    return value


# ---------------------------------------------------------------- how a group is described (gui/groups.py, no Tk)

def _ways_text(group: dict) -> str:
    """How its members are blocked, for the list: "Apps closed · sites can't load"."""
    app, site = group.get("app_block"), group.get("site_block")
    parts = [f"apps {' + '.join(w for f, w in APP_WORDS if f in block_method.flags('app', app))}" if app
             else "apps as each is set",
             f"sites {site_block.text(site)}" if site else "sites as each is set"]
    text = " · ".join(parts)
    return text[:1].upper() + text[1:]


def _clean_rule(rule: dict) -> dict:
    """A rule as the editors take it: only the fields a rule has (the database row also holds ids)."""
    return {k: rule[k] for k in RULE_FIELDS if rule.get(k) is not None}


def _live(overrides: dict, now) -> dict:
    """A member's extra rules, less an extra temporary block that has run out (it blocks nothing, and clearing it away
    is free): it would otherwise show as "Keep (-3h left)" and keep the "+ extra limits" mark."""
    return {t: r for t, r in (overrides or {}).items()
            if not (t == "temporary" and r.get("temp_until") and _ended(r["temp_until"], now))}


def _ended(stamp: str, now) -> bool:
    try:
        return datetime.strptime(stamp, TIME_FMT) <= now
    except ValueError:
        return False


def _sub(item: dict) -> str:
    """The grey line of a member: what it covers."""
    if item["item_type"] == "category":
        return "category · everything in it"
    return f"{item['item_type']} · " + " · ".join(item["target"].split())


def _row(item: dict) -> dict:
    """A thing on the blocklist as the Members box shows it."""
    return {"id": item["id"], "name": item["display_name"], "kind": block_method.kind(item), "type": item["item_type"],
            "icon": blocking._icon(item), "sub": _sub(item), "targets": item["target"].lower().split(),
            "hosted": item["item_type"] == "app" and is_hosted(exe_name(item.get("target") or "")),
            "item_way": item.get("block_type")}


def _member(group: dict, item: dict, now) -> dict:
    return {**_row(item), "way": block_method.member_own(group, item["id"]),
            "overrides": {t: _clean_rule(r) for t, r in _live(group["members"].get(item["id"]), now).items()}}


# ---------------------------------------------------------------- the list

def _status(b, group: dict) -> dict:
    """{k, text, until?}: Not applied / Disabled / k of n blocked now / Allowed now. A group whose own rules are
    blocking now also says until when (the card shows it as "Blocked (until ...)" in the tooltip, once)."""
    d = b.d
    if d.is_group_unsaved(group["id"]):
        return {"k": "unsaved", "text": "Not applied (unsaved)"}
    if group.get("disabled"):
        return {"k": "disabled", "text": "Disabled - nothing is enforced"}
    status = b.group_status(group)
    if status["k"] == "blocked":
        own = [blocking._group_rule(group, r) for r in group["rules"]]
        if item_block(own, b.now, b.usage):
            end = block_end(own, b.now, b.usage)
            status = {**status, "until": blocking._iso(end), "title": blocked_text(end, b.now)}
    return status


def _card(b, group: dict) -> dict:
    d = b.d
    names = sorted((d.items[i]["display_name"] for i in group["members"] if i in d.items), key=str.lower)
    own = [blocking._group_rule(group, r) for r in group["rules"]]
    return {"id": group["id"], "name": group["name"], "disabled": bool(group.get("disabled")), "count": len(names),
            "names": names[:MAX_NAMES], "chips": blocking._chips(own, b.now, b.usage, own),
            "ways": _ways_text(group), "status": _status(b, group)}


@core.read
def groups_list(ctx):
    """The Groups tab: {now, groups: [{id, name, disabled, count, names, chips, ways, status}]}, by name. `names`: the
    first members (the card says "N members · a, b, c"); chips: [{t, k, r}] as in the Overview; status: {k, text,
    until?, title?} with k unsaved / disabled / empty / allowed / pending / blocked. The unsaved draft is shown, with
    "Not applied (unsaved)" on what is still waiting for Save."""
    db, now = ctx.db, ctx.now()
    d = drafts.draft_of(ctx)
    usage = db.usage_lookup(now)
    running = blocking._service_running(db)
    with drafts.lock:
        d.refresh_if_clean()
        b = blocking._Build(d, now, usage, running)
        cards = [_card(b, g) for g in d.sorted_groups()]
    return {"now": blocking._iso(now), "groups": cards}


# ---------------------------------------------------------------- one group, for the editor

@core.read
def group_get(ctx, group_id=None):
    """One group for the editor (`group_id` None: the starting values of a new group):
    {id, name, disabled, rules: [rule dict], ways: {app, site}, members: [{id, name, kind, type, icon, sub, targets,
    hosted, item_way, way, overrides: {rule_type: rule dict}}], now, new}. `ways` is what the group chose for its members ("how
    members are blocked", None = each member keeps the way it was set when it was added). `member.way` is what that
    member does on its own in this group (an app's replaces the group's, a site's is added on top); `item_way` the
    member's own setting (what applies while the group has not chosen). Reads the draft (unsaved groups too)."""
    now = ctx.now()
    d = drafts.draft_of(ctx)
    with drafts.lock:
        d.refresh_if_clean()
        if group_id is None:
            return {"id": None, "name": "", "disabled": False, "rules": [], "ways": dict(NEW_WAYS), "members": [],
                    "now": blocking._iso(now), "new": True}
        g = d.groups.get(_id(group_id))
        if g is None:
            raise ApiError("That group isn't there (any more).")
        members = sorted((_member(g, d.items[i], now) for i in g["members"] if i in d.items),
                         key=lambda m: m["name"].lower())
        return {"id": g["id"], "name": g["name"], "disabled": bool(g.get("disabled")),
                "rules": [_clean_rule(r) for r in g["rules"]],
                "ways": {"app": g.get("app_block") or None, "site": g.get("site_block") or None},
                "members": members, "now": blocking._iso(now), "new": False}


# ---------------------------------------------------------------- finding members to add

def _clip(text) -> str:
    if not isinstance(text, str) or len(text) > 200:
        raise ApiError("Bad request: the search must be text.")
    return " ".join(text.split())


@core.read
def group_member_search(ctx, query=""):
    """What typing in the Members box finds: {items, apps, site, loading}.
      items  things already on your blocklist (the draft) that match, each {id, name, kind, type, icon, sub, targets, hosted, item_way}
              (best first);
      apps   installed apps / Steam games that match and are not on the list yet, each {name, exe, path, steam};
      site   {host} when the text reads as a website address that is not on the list yet, else null.
    Ticking an item adds it; ticking an app or the site makes a new blocklist item when the group is saved."""
    query = _clip(query or "")
    if not query:
        return {"items": [], "apps": [], "site": None, "loading": False}
    d = drafts.draft_of(ctx)
    with drafts.lock:
        d.refresh_if_clean()
        items = list(d.items.values())
        tokens = {t for i in items for t in i["target"].lower().split()}
        found = search.rank(items, query, lambda i: f"{i['display_name']} {i['target']}",
                            tie=lambda i: i["display_name"].lower())[:MAX_FOUND]
        rows = [_row(i) for i in found]
    listed = shared._catalog(ctx).search("all", query, limit=MAX_FOUND + len(tokens))
    apps = [{"name": a["name"], "exe": a["exe"], "path": a["path"], "steam": a["steam"]}
            for a in listed["apps"] if a["exe"] not in tokens][:MAX_FOUND]
    site = None
    if "." in query and " " not in query:
        try:
            host = hosts.normalize_host(query)
        except ValueError:
            host = None
        if host and host not in tokens:
            site = {"host": host}
    return {"items": rows, "apps": apps, "site": site, "loading": bool(listed.get("loading"))}


# ---------------------------------------------------------------- saving a group (the page is untrusted)

def _way(kind: str, value, who: str, own: bool = False, hosted: bool = False):
    """A way read from the page, tidied the way the tick boxes do (closing covers minimizing / going back; "its
    background processes" only goes with closing). None / "" = nothing chosen. `own`: a member's own way - an app's
    needs close, minimize or internet; a program file (`hosted`) is closed or minimised only."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 60:
        raise ApiError(f"Bad request: {who} must be a way of blocking.")
    flags = {f.strip() for f in value.split(",")}
    allowed = set(APP_FLAGS) if kind == "app" else set(site_block.FLAGS)
    if flags - allowed:
        raise ApiError(f"Bad request: {who} has a way Lockdown doesn't know.")
    if kind == "site":
        if "close" in flags:
            flags.discard("back")
        return site_block.make_site_block_type(flags) or None
    if hosted and flags & {"internet", "background"}:
        raise ApiError(f"{who}: a program file is closed or minimised only.")
    if "close" in flags:
        flags.discard("minimize")
    else:
        flags.discard("background")
    if own and not flags:
        raise ApiError(f"{who}: tick Close app, Minimize or Block internet for its own way.")
    return make_block_type(flags) or None


def _overrides(value, name: str, now, errors: list):
    """{rule_type: form} -> {rule_type: rule dict} for one member; what is wrong goes to `errors` (with the member)."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > len(CUSTOMIZABLE):
        raise ApiError("Bad request: a member's own limits must be a list of forms.")
    out = {}
    for t, form in value.items():
        if t not in CUSTOMIZABLE or not isinstance(form, dict) or form.get("rule_type") != t:
            raise ApiError(f"Bad request: {name} can't be given an own {t}.")
        try:
            out[t] = rule_forms.rule_from_form(form, False, now)
        except rule_forms.RuleFormError as e:
            errors.append({"rule_type": t, "field": e.field, "message": str(e), "member": name})
    return out


def _pending_target(ctx, entry) -> dict:
    """A new thing to put on the blocklist as a member: {kind, name (lower case), display, path}. Raises ApiError."""
    if not isinstance(entry, dict):
        raise ApiError("Bad request: no such app or site.")
    kind, name = categories._target(entry.get("kind"), entry.get("name"))
    if kind == "app" and name in PROTECTED:
        raise ApiError(f"{name} can't be blocked (Lockdown needs it, or Windows does).")
    if kind == "site":
        try:
            name = hosts.normalize_host(name)
        except ValueError:
            raise ApiError("That isn't a website address Lockdown can block.") from None
    display = entry.get("display")
    if display is not None and (not isinstance(display, str) or len(display) > 400 or _CONTROL.search(display)):
        raise ApiError("Bad request: the name is not valid.")
    display = " ".join(display.split())[:MAX_DISPLAY] if isinstance(display, str) and display.strip() else name
    path = categories._known_path(ctx, entry.get("path")) if kind == "app" else None
    return {"kind": kind, "name": name, "display": display, "path": path}


def _group_name(value) -> str:
    if not isinstance(value, str) or len(value) > MAX_NAME * 4 or _CONTROL.search(value):
        raise ApiError("Bad request: the name must be text.")
    return " ".join(value.split())


@core.loosen
def draft_set_group(ctx, group_id, name, rules, members, ways, pending_targets=None):
    """Create (`group_id` None) or change a group in the draft - "Done" in the group editor.
      name             the group's name (required)
      rules            the group's blockers as FORMS (what the rail's rail.forms() holds); at least one
      members          [{id, overrides: {rule_type: form}, way}] for what is on the blocklist already: `overrides` are the
                       member's own extra rules on top of the group's (scheduled / time_limit / switch_limit / temporary),
                       `way` its own way of being blocked in this group (null = the group's)
      ways             {app_block, site_block}: how the group blocks its members; null = each as it was set
      pending_targets  [{kind: app|site, name, display, path, overrides, way}] new members, put on the blocklist with the
                       group (an existing item with that name is used instead; the same member twice is refused)
    Everything is read again here (rule_forms for the blockers). A mistake comes back as result {saved: false, errors:
    [{rule_type, field, message, member?}]} and nothing changes. Otherwise the draft changes and result is {saved: true,
    added, name}; if the save would loosen a block (a raised shared limit, a member taken out, a member's extra rule
    removed, a lighter way ...) the reply is {needs_challenge, ticket, changes} and nothing is saved until the challenge
    has passed. A group's disabled flag is kept; items left with no blockers and no group are dropped."""
    gid = None if group_id is None else _id(group_id)
    name = _group_name(name)
    if not isinstance(members, list) or len(members) > MAX_MEMBERS:
        raise ApiError("Bad request: members must be a list.")
    pending = [] if pending_targets is None else pending_targets
    if not isinstance(pending, list) or len(pending) > MAX_PENDING:
        raise ApiError("Bad request: new members must be a list.")
    if not isinstance(ways, dict) or set(ways) - {"app_block", "site_block"}:
        raise ApiError("Bad request: ways must say how apps and sites are blocked.")
    now = ctx.now()

    def edit(d):
        if gid is not None and gid not in d.groups:
            raise ApiError("That group is already gone.")
        errors = []
        if not name or len(name) > MAX_NAME:
            errors.append({"rule_type": None, "field": "name", "message": "Give the group a name." if not name
                           else f"The name can be at most {MAX_NAME} letters."})
        group_rules, rule_errors = rule_forms.check_forms(rules, True, now, True)
        errors += rule_errors
        app_way = _way("app", ways.get("app_block"), "How apps are blocked")
        site_way = _way("site", ways.get("site_block"), "How sites are blocked")
        chosen, targets, member_ways = {}, [], {}          # item id -> overrides; new ones; item id / key -> own way

        def check_member(label, kind, hosted, entry_overrides, entry_way):
            overrides = _overrides(entry_overrides, label, now, errors)
            return overrides, _way(kind, entry_way, label, own=True, hosted=hosted)

        for entry in members:
            if not isinstance(entry, dict):
                raise ApiError("Bad request: a member must be an object.")
            item = d.items.get(_id(entry.get("id"), "member"))
            if item is None:
                raise ApiError("One of the members isn't on your list any more - close the editor and open it again.")
            if item["id"] in chosen:
                errors.append({"rule_type": None, "field": "members", "message": f"{item['display_name']} is in this group twice.",
                               "member": item["display_name"]})
                continue
            hosted = item["item_type"] == "app" and is_hosted(exe_name(item.get("target") or ""))
            chosen[item["id"]], member_ways[item["id"]] = check_member(
                item["display_name"], block_method.kind(item), hosted, entry.get("overrides"), entry.get("way"))
        seen = set()
        for entry in pending:
            target = _pending_target(ctx, entry)
            existing = d.find_item(target["name"])
            key = existing["id"] if existing else target["name"]
            if key in chosen or key in seen:
                errors.append({"rule_type": None, "field": "members", "message": f"{target['display']} is already in this group.",
                               "member": target["display"]})
                continue
            seen.add(key)
            hosted = target["kind"] == "app" and is_hosted(target["name"])
            overrides, way = check_member(target["display"], target["kind"], hosted, entry.get("overrides"),
                                          entry.get("way"))
            targets.append((target, existing, overrides, way))
        if errors:
            return {"saved": False, "errors": errors}

        # nothing is wrong: change the draft
        member_rules = dict(chosen)
        for target, existing, overrides, way in targets:
            item = existing or d.add_item(target["display"], [target["name"]], "manual", target["kind"],
                                          APP_DEFAULT if target["kind"] == "app" else None, target["path"])
            member_rules[item["id"]] = overrides
            member_ways[item["id"]] = way
        new_ways = {"app_block": app_way, "site_block": site_way,
                    "member_blocks": {i: w for i, w in member_ways.items() if w}}
        d.set_group(gid, name, group_rules, member_rules, new_ways)
        return {"saved": True, "added": gid is None, "name": name}
    return drafts.run_edit(ctx, edit)

