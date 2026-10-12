"""Api for Blocking > Overview and the emergency unlock (BLUEPRINT 3.2), over the agent's draft (webapi/draft.py).

`blocking_overview_get` is the page's one call: every blocked thing with its rule chips and its status, the groups as
boxes with their members, the disabled ones, the counts. It is the Overview of the old window (gui/blocking.py) worked
out in Python once per load - the page only sorts, filters and paints. Statuses and "blocked now" use the SAVED state
(what is enforced); the chips show the draft. The wording is the old window's, the chip colours too: red while a rule is
blocking, orange while it is about to, green while it isn't.

Classification: removing an item or a group and disabling a group edit the draft and loosen only when SAVED, which
Draft.save does through the guard (the challenge) - they are `loosen`. The emergency unlock is bounded by its quota
(uses per day / week / month) instead of a challenge: its minutes come from the kind, never from the page.
"""
import threading
from datetime import datetime

import emergency
import group_look
from blocker import site_block
from blocker.apps import block_flags
from rules import (DAY_NAMES, allowance_left, allowance_note, block_end, blocked_text, describe_rule, duration_text,
                   effective_rules, item_block, next_block, rule_state)

from . import core
from . import draft as drafts
from .core import ApiError

SORTS = ["Blocked now first", "Next block", "Date added", "Name"]
SORT_KEY = "ui.blocking.sort"
HEARTBEAT_KEY = "service_heartbeat"     # service.HEARTBEAT_KEY
SERVICE_TIMEOUT_SEC = 15
CHIP_STATES = {"blocked": "danger", "soon": "warn", "allowed": "ok"}
RULE_WORDS = {"scheduled": "hours", "time_limit": "limit", "switch_limit": "opening limit", "temporary": "temporary block",
              "permanent": "permanent block"}
MAX_UNLOCK_ITEMS = 500
_unlock_lock = threading.Lock()         # two unlocks at once must not both spend the last use


def _iso(when):
    return when.isoformat(timespec="seconds") if when and when != datetime.max else None


def _look(group: dict) -> dict:
    """The group's icon and colour as the page paints them: {icon, color} (picked, or else automatic - group_look)."""
    shown = group_look.look(group)
    return {"icon": shown["icon"], "color": shown["color"]}


def _short_when(when) -> str:
    """"Mon 12 Oct 00:00" (gui/settings.short_when)."""
    return f"{DAY_NAMES[when.weekday()][:3]} {when.day} {when:%b %H:%M}"


def _service_running(db) -> bool:
    import time
    try:
        return time.time() - float(db.get_setting(HEARTBEAT_KEY, "0") or 0) < SERVICE_TIMEOUT_SEC
    except ValueError:
        return False


def _id(value, what="item") -> int:
    if type(value) is not int:
        raise ApiError(f"Bad request: {'an' if what[:1] in 'aeiou' else 'a'} {what} number expected.")
    return value


# ---------------------------------------------------------------- how one thing is described (gui/blocking.py, no Tk)

def _targets_text(item: dict) -> str:
    """The grey line under a name: what it covers and how it is blocked."""
    if item["item_type"] == "category":
        return "everything in this category - on your list or not"
    if item["item_type"] == "app":
        flags = block_flags(item.get("block_type"))
        words = (("close", "closes"), ("background", "background processes"), ("minimize", "minimises"),
                 ("grey", "greys the screen"), ("internet", "internet blocked"))
        how = " + ".join(w for f, w in words if f in flags)
        return f"app · {item['target']} · {how}"
    return " · ".join(item["target"].split()) + " · " + site_block.text(item.get("block_type"))


def _rule_text(rule, now, usage, used: bool = True) -> str:
    """"→ Games · Limit (group): ..." for a group's rule on a member and for a member's extra rule in a group; the
    box of the group itself leaves the prefix out. used=False: a limit without its used time."""
    text = describe_rule(rule, now, usage, allowance=False, used=used).replace(":\n", ": ").replace("\n", " · ")
    group = rule["group"] or rule.get("extra_of")
    return f"→ {group['name']} · {text}" if group and not rule.get("in_box") else text


def _chips(rules: list[dict], now, usage, siblings=None) -> list[dict]:
    """One chip per rule (two for a rule with an allowance): {t: text, k: danger|warn|ok|allowance|neutral, r: rule
    type}. `siblings`: the other rules on the same thing - while any of them blocks, what a rule still allows is no
    use, so it goes red with the rest (and shows its limit without the time used or left)."""
    inert = bool(siblings) and bool(item_block(siblings, now, usage))
    out = []
    for rule in rules:
        kind = "danger" if inert else CHIP_STATES[rule_state(rule, now, usage)]
        out.append({"t": _rule_text(rule, now, usage, not (inert or kind == "danger")), "k": kind,
                    "r": rule["rule_type"]})
        if rule.get("allowance_min"):
            note = allowance_note(rule, now, usage, not inert)
            if note:
                out.append({"t": note, "k": "allowance", "r": "allowance"})
    return out


def _group_rule(group: dict, rule: dict) -> dict:
    """A group's own rule as the box wants it: counted against the group, with no "→ group" prefix."""
    owner = f"group:{group['id']}"
    return {**rule, "usage_owner": owner, "item_owner": owner, "group": None,
            "rule_key": f"g{group['id']}{rule['rule_type']}"}


def _icon(item: dict) -> dict:
    kind = {"site": "site", "app": "app"}.get(item["item_type"], "category")
    return {"k": kind, "n": item["target"].split()[0] if item["target"].split() else item["display_name"]}


# ---------------------------------------------------------------- one Overview, built

class _Build:
    """Everything one overview needs, worked out once: the saved rules of each item (what is enforced), who is not
    applied yet, the order. Built with the draft's lock held."""

    def __init__(self, d, now, usage, running: bool):
        self.d, self.now, self.usage, self.running = d, now, usage, running
        self.saved_groups = list(d.saved_groups.values())
        self.groups = list(d.groups.values())
        self.dirty = d.dirty
        self.not_applied = self._not_applied() if self.dirty else set()
        self._rules: dict = {}
        self._block: dict = {}

    def _not_applied(self) -> set:
        """Items that are, or sit in a group that is, changed, new or removed in the draft (Draft.item_not_applied for
        all of them at once)."""
        d = self.d
        out = {i for i in d.items if d.is_unsaved(i)}
        for gid in set(d.groups) | set(d.saved_groups):
            if gid not in d.groups or d.is_group_unsaved(gid):
                for g in (d.groups.get(gid), d.saved_groups.get(gid)):
                    if g:
                        out.update(g["members"])
        return out

    # ---- the saved (enforced) side
    def saved_rules(self, item_id):
        if item_id not in self._rules:
            saved = self.d.saved_items.get(item_id)
            self._rules[item_id] = effective_rules(saved, self.saved_groups) if saved else None
        return self._rules[item_id]

    def block(self, item_id):
        if item_id not in self._block:
            rules = self.saved_rules(item_id)
            self._block[item_id] = item_block(rules, self.now, self.usage) if rules is not None else None
        return self._block[item_id]

    def allowance_left(self, item_id):
        """Seconds left of the allowance it is being used on right now, or None: why something can say "allowed" while
        its hours are on."""
        for rule in self.saved_rules(item_id) or []:
            spent = allowance_left(rule, self.now, self.usage)
            if spent and spent[0] < spent[1]:
                return spent[1] - spent[0]
        return None

    def next_time(self, item) -> datetime:
        """datetime.min = blocked now; datetime.max = nothing coming up."""
        if self.block(item["id"]):
            return datetime.min
        rules = self.saved_rules(item["id"])
        nb = next_block(rules, self.now, self.usage) if rules else None
        return nb[0] if nb else datetime.max

    # ---- statuses (today's order)
    def status(self, item) -> dict:
        now, usage, iid = self.now, self.usage, item["id"]
        if iid in self.not_applied:
            return {"k": "unsaved", "text": "Not applied (unsaved)"}
        if item.get("disabled"):
            return {"k": "disabled", "text": "Disabled"}
        paused = getattr(usage, "paused", None)
        if paused and now < paused:        # "Pause my blocks"
            return {"k": "paused", "text": f"Paused · {duration_text((paused - now).total_seconds())} left",
                    "until": _iso(paused)}
        unlocked = usage.unlocks.get(f"item:{iid}")
        if unlocked and now < unlocked:
            return {"k": "unlocked", "text": f"Unlocked · {duration_text((unlocked - now).total_seconds())} left",
                    "until": _iso(unlocked)}
        if not self.block(iid):
            left = self.allowance_left(iid)      # inside its blocked hours, on the allowance
            if left is not None:
                return {"k": "allowance", "text": f"On the allowance · {duration_text(left)} left"}
            return {"k": "allowed", "text": "Allowed now"}
        if not self.running:
            return {"k": "pending", "text": "Pending · service off"}
        end = block_end(self.saved_rules(iid), now, usage)      # its longest block
        return {"k": "blocked", "text": blocked_text(end, now), "until": _iso(end)}

    def group_status(self, group) -> dict:
        d = self.d
        if d.is_group_unsaved(group["id"]):
            return {"k": "unsaved", "text": "Not applied (unsaved)"}
        members = [d.items[i] for i in group["members"] if i in d.items]
        if not members:
            return {"k": "empty", "text": "No members yet"}
        blocked = sum(1 for m in members if self.block(m["id"]))
        if not blocked:
            return {"k": "allowed", "text": "Allowed now"}
        if not self.running:
            return {"k": "pending", "text": "Pending · service off"}
        return {"k": "blocked", "text": f"{blocked} of {len(members)} blocked now"}

    # ---- the pieces of the page
    def item_entry(self, item) -> dict:
        d, now, usage = self.d, self.now, self.usage
        rules = effective_rules(item, self.groups)
        chips = _chips(rules, now, usage, rules)
        for g in self.groups:       # a paused group: say so, or the item looks like it has no blockers at all
            if g.get("disabled") and item["id"] in g["members"]:
                chips.append({"t": f"→ {g['name']} · disabled", "k": "neutral", "r": "group"})
        status = self.status(item)
        return {"t": "item", "id": item["id"], "name": item["display_name"], "type": item["item_type"],
                "icon": _icon(item), "sub": _targets_text(item), "notify": item.get("notify"), "status": status,
                "chips": chips, "groups": [{"id": g["id"], "name": g["name"]} for g in d.groups_of(item["id"])],
                "low": status["k"] not in ("blocked", "pending") and any(c["k"] == "warn" for c in chips)}

    def member_entry(self, item, group) -> dict:
        now, usage = self.now, self.usage
        status = self.status(item)
        own, note = [], ""
        if not item.get("disabled"):
            own = _chips(effective_rules({**item, "rules": item["rules"]}, []), now, usage,
                         effective_rules(item, [group]))
            # what the group gives this member on top of its own rules ("customised")
            siblings = effective_rules(item, [group])
            extras = [{**r, "in_box": True} for r in siblings if (r.get("extra_of") or {}).get("id") == group["id"]]
            if extras:
                own += _chips(extras, now, usage, siblings)
                words = ", ".join(RULE_WORDS.get(r["rule_type"], r["rule_type"]) for r in extras)
                note = f"its own {words} in this group"
        return {"id": item["id"], "name": item["display_name"], "type": item["item_type"], "icon": _icon(item),
                "status": status, "chips": own, "note": note}

    def group_entry(self, group) -> dict:
        d, now, usage = self.d, self.now, self.usage
        members = sorted((d.items[i] for i in group["members"] if i in d.items), key=lambda i: i["display_name"].lower())
        own_rules = [_group_rule(group, r) for r in group["rules"]]
        return {"t": "group", "id": group["id"], "name": group["name"], "count": len(members),
                **_look(group),
                "status": self.group_status(group), "chips": _chips(own_rules, now, usage, own_rules),
                "members": [self.member_entry(m, group) for m in members],
                "created": group.get("created_at")}

    # ---- the order
    def sort_entries(self, entries: list[tuple], how: str) -> list[tuple]:
        """("group", g) / ("item", i) in the order asked for. A group counts as blocked as soon as one of its members is
        and its next block is the soonest of theirs."""
        def name(e):
            return (e[1]["name"] if e[0] == "group" else e[1]["display_name"]).lower()

        if how == "Name":
            return sorted(entries, key=name)
        if how == "Date added":     # newest first; unsaved ones on top
            return sorted(entries, key=lambda e: e[1].get("created_at") or "~", reverse=True)

        def time_of(e) -> datetime:
            kind, thing = e
            if kind == "item":
                return self.next_time(thing)
            return min((self.next_time(self.d.items[i]) for i in thing["members"] if i in self.d.items),
                       default=datetime.max)
        times = {id(e[1]): time_of(e) for e in entries}
        if how == "Next block":     # soonest first, the ones blocked already after them
            return sorted(entries, key=lambda e: (times[id(e[1])] == datetime.min, times[id(e[1])], name(e)))
        return sorted(entries, key=lambda e: (times[id(e[1])] != datetime.min, name(e)))


def _sort(db, asked) -> str:
    if asked in SORTS:
        return asked
    saved = db.get_setting(SORT_KEY, SORTS[0])
    return saved if saved in SORTS else SORTS[0]


# ---------------------------------------------------------------- Api: Overview

@core.read
def blocking_overview_get(ctx, sort=None):
    """The whole Overview: {now, sort, sorts, autosave, emergency, counts, entries, disabled}.
    entries: the boxes and loose rows in the chosen order - {t: "item", id, name, type, icon, sub, notify, status,
    chips, groups, low} or {t: "group", id, name, count, status, chips, members: [...]}; status = {k, text, until?}
    (k: unsaved / disabled / paused / unlocked / allowance / allowed / pending / blocked); chips = [{t, k, r}].
    disabled: [{t, id, name, type, icon?}]. `sort` (optional) is one of SORTS, else the saved choice."""
    if sort is not None and sort not in SORTS:
        raise ApiError("Unknown sort order.")
    db, now = ctx.db, ctx.now()
    how = _sort(db, sort)
    d = drafts.draft_of(ctx)
    usage = db.usage_lookup(now)
    running = _service_running(db)
    with drafts.lock:
        d.refresh_if_clean()
        b = _Build(d, now, usage, running)
        groups = [g for g in d.sorted_groups() if not g.get("disabled")]
        in_group = {i for g in groups for i in g["members"] if i in d.items}
        loose = [i for i in d.sorted_items() if not i.get("disabled") and i["id"] not in in_group]
        ordered = b.sort_entries([("group", g) for g in groups] + [("item", i) for i in loose], how)
        entries = [b.group_entry(t) if kind == "group" else b.item_entry(t) for kind, t in ordered]
        seen = {}                       # item id -> its status (an item in two boxes counts once)
        for e in entries:
            for row in e["members"] if e["t"] == "group" else [e]:
                seen[row["id"]] = row["status"]["k"]
        blocked = sum(1 for k in seen.values() if k in ("blocked", "pending"))
        disabled = [{"t": "item", "id": i["id"], "name": i["display_name"], "type": i["item_type"], "icon": _icon(i)}
                    for i in d.sorted_items() if i.get("disabled")]
        disabled += [{"t": "group", "id": g["id"], "name": g["name"], **_look(g)} for g in d.sorted_groups() if g.get("disabled")]
        autosave = d.autosave
    return {"now": _iso(now), "sort": how, "sorts": SORTS, "autosave": autosave,
            "emergency": emergency.usable(db), "counts": {"total": len(seen), "blocked": blocked, "groups": len(groups),
                                  "disabled": len(disabled)},
            "entries": entries, "disabled": disabled}


@core.ui
def blocking_set_sort(ctx, value):
    """Remember how the Overview is sorted (the same on the next visit)."""
    if value not in SORTS:
        raise ApiError("Unknown sort order.")
    ctx.db.set_setting(SORT_KEY, value)


# ---------------------------------------------------------------- Api: edits of the draft

@core.ui
def draft_set_notify(ctx, item_id, notify):
    """The item's blocked-visit alerts: null (default), "on" or "off". A cosmetic choice."""
    item_id = _id(item_id)
    if notify not in (None, "on", "off"):
        raise ApiError("Pick Default, On or Off.")

    def edit(d):
        if item_id not in d.items:
            raise ApiError("That item is already gone.")
        d.set_notify(item_id, notify)
    return drafts.run_edit(ctx, edit)


@core.loosen
def draft_remove_item(ctx, item_id):
    """Take the item off the list. Saved at once with Auto-save, else when Save is pressed - and either way through the
    guard: a locked Anti-Bypass asks for the challenge first and the item stays until it is passed."""
    item_id = _id(item_id)

    def edit(d):
        if item_id not in d.items:
            raise ApiError("That item is already gone.")
        d.remove_item(item_id)
    return drafts.run_edit(ctx, edit)


@core.loosen
def draft_set_group_disabled(ctx, group_id, disabled):
    """Disable a group (it keeps its rules, none of them apply) or enable it again. Disabling is a loosening: guarded."""
    group_id = _id(group_id, "group")
    if type(disabled) is not bool:
        raise ApiError("Bad request: on or off expected.")

    def edit(d):
        if group_id not in d.groups:
            raise ApiError("That group is already gone.")
        d.set_group_disabled(group_id, disabled)
    return drafts.run_edit(ctx, edit)


@core.loosen
def draft_remove_group(ctx, group_id):
    """Remove a group (its members stay if they have blockers of their own). Guarded."""
    group_id = _id(group_id, "group")

    def edit(d):
        if group_id not in d.groups:
            raise ApiError("That group is already gone.")
        d.remove_group(group_id)
    return drafts.run_edit(ctx, edit)


# ---------------------------------------------------------------- Api: emergency unlock

def _unlockable(item: dict) -> dict:
    return {"id": item["id"], "name": item["display_name"], "type": item["item_type"], "icon": _icon(item)}


@core.read
def emergency_get_candidates(ctx):
    """What the Emergency unlock dialog offers: {usable, kinds: [{id, minutes, per, per_text, left, allowed, resets,
    resets_text}], blocked: [item], open: [item], permanent, alerts_until}. `blocked`: items blocked right now that an
    emergency unlock can free (never a permanent block, never a made-up item of a mode); `open`: the same kind of item
    that is not blocked yet (an unlock holds until it ends, even if a limit runs out meanwhile); `permanent`: some
    block right now is permanent (the dialog says those can't be unlocked)."""
    db, now = ctx.db, ctx.now()
    if not emergency.usable(db):
        return {"usable": False, "kinds": [], "blocked": [], "open": [], "permanent": False, "alerts_until": None}
    kinds = [{"id": k["id"], "minutes": k["minutes"], "per": k["per"], "per_text": emergency.PERIODS[k["per"]],
              "left": left, "allowed": allowed, "resets": _iso(reset), "resets_text": _short_when(reset)}
             for k, left, allowed, reset in emergency.left_by_tier(db, now)]
    blocked, open_now = emergency.choices(db, now)
    permanent = any(b["reason"] == "permanent" for b in db.blocks(now) if b["item"]["id"] is not None)
    return {"usable": True, "kinds": kinds, "blocked": [_unlockable(i) for i in blocked],
            "open": [_unlockable(i) for i in open_now], "permanent": permanent,
            "alerts_until": _iso(emergency.alerts_paused_until(db, now))}


@core.loosen
def emergency_unlock(ctx, item_ids, kind, pause_alerts=False):
    """Unblock the ticked items (and/or pause the bedtime and break alerts) for the length of emergency kind `kind`:
    ONE use of that kind however many items. Python checks everything again: emergency unlock is turned on, the kind
    exists and has a use left, the ids are real items, none is permanent, and the minutes are the kind's own - the page
    never sends them. This is not a challenge (the quota is the limit) -> {until, names, alerts, left, text}."""
    ids = []
    if not isinstance(item_ids, list) or len(item_ids) > MAX_UNLOCK_ITEMS:
        raise ApiError("Bad request: a list of items expected.")
    for v in item_ids:
        if _id(v) not in ids:
            ids.append(v)
    if not isinstance(kind, str):
        raise ApiError("Pick how long.")
    if type(pause_alerts) is not bool:
        raise ApiError("Bad request: on or off expected.")
    db, now = ctx.db, ctx.now()
    if not ids and not pause_alerts:
        raise ApiError("Tick at least one.")
    by_id = {i["id"]: i for i in db.list_items()}
    if any(i not in by_id for i in ids):
        raise ApiError("That item isn't on your list anymore.")
    items = [by_id[i] for i in ids]
    if any(i.get("disabled") for i in items):
        raise ApiError("A disabled item has nothing to unlock.")
    with _unlock_lock:
        try:
            until = emergency.unlock(db, items, now, alerts=pause_alerts, tier=kind)
        except ValueError as e:
            raise ApiError(str(e)) from None
        left = next((left for k, left, _a, _r in emergency.left_by_tier(db, now) if k["id"] == kind), 0)
    names = [i["display_name"] for i in items]
    text = [f"{', '.join(names)} unlocked until {until:%H:%M}"] if names else []
    if pause_alerts:
        text.append(f"Bedtime and break alerts paused until {until:%H:%M}")
    drafts._publish(ctx, "blocks_changed", {"rev": next(drafts._rev)})
    drafts._poke(ctx)
    return {"until": _iso(until), "names": names, "alerts": pause_alerts, "left": left, "text": ". ".join(text)}
