"""How a blocked site or app is dealt with when a GROUP blocks it (0.84.11): the group's "How members are blocked".

A group can say how its members are blocked - apps: close (+ background) / minimize / cut internet; sites: can't
load / close the tab / go back / minimize - the same flags as an item's own ("block_type", blocker.apps / blocker.site_block).
A member may add to that in its extra-limits window, and only add: its own choice is merged ON TOP of the group's,
like its extra rules (0.84.3), so it can make the member stricter, never weaker.

An APP member is different (schema 8): the way it is given in the group REPLACES the group's - e.g. a game only
minimised to save and quit while the rest of the group is closed. Its effective way is its own if it has one, else
the group's; looser than before is the Anti-Bypass challenge (antibypass.ways_looser compares effective ways).

- A block by the item's own rules: the item's own way, as always.
- A block by a group (its rules or a member's extra rule in it): the group's way for that kind + the member's own.
  A group that hasn't chosen (every group before 0.84.11) blocks each member the way the member is set itself -
  nothing changes on upgrade.
- Blocked by several at once: everything any of them asks for (closing wins over minimizing / going back, and
  minimizing over "Grey it" - the app stays open and the screen goes grey while it is in front).

Standard library only: the service reads this too.
"""
from blocker.apps import block_flags, make_block_type
from blocker.site_block import make_site_block_type, site_flags

GROUP_KEYS = {"app": "app_block", "site": "site_block"}


def kind(item: dict) -> str:
    """"site" or "app" (a category blocks apps: its block_type is an app one)."""
    return "site" if item["item_type"] == "site" else "app"


def flags(k: str, block_type: str | None) -> set[str]:
    return site_flags(block_type) if k == "site" else block_flags(block_type)


def strength(k: str, block_type: str | None) -> set[str]:
    """Effective coverage for comparing ways, including independent background site coverage."""
    f = flags(k, block_type)
    if k == "site":
        if "close" in f:
            f.add("back")
        if f & {"back", "minimize"}:
            f.add("foreground")
        return f
    if "close" in f:
        f.update({"minimize", "grey"})
    elif "minimize" in f:
        f.add("grey")
    return f


def merge(k: str, *block_types: str | None) -> str:
    """One way that does everything these ask for."""
    f = set().union(*(flags(k, b) for b in block_types))
    if k == "site":
        if "close" in f:
            f.discard("back")
        return make_site_block_type(f)
    if "close" in f:
        f -= {"minimize", "grey"}
    else:
        f.discard("background")
        if "minimize" in f:
            f.discard("grey")
    return make_block_type(f)


def matching_sites(host: str, ways: dict[str, str | None]) -> dict[str, str | None]:
    """All rules for the host and its parent domains, starting with the most specific."""
    host = host.lower().rstrip(".")
    matches = {}
    while host:
        if host in ways:
            matches[host] = ways[host]
        host = host.partition(".")[2]
    return matches


def site_ways(items) -> dict[str, str | None]:
    """Combine the ways of active site items by hostname, including duplicate targets."""
    ways = {}
    for item in items:
        if item["item_type"] != "site":
            continue
        way = item.get("block_type")
        for host in item["target"].split():
            host = host.lower().rstrip(".")
            ways[host] = merge("site", ways[host], way) if host in ways else way
    return ways


def site_coverage_looser(old: dict[str, str | None], new: dict[str, str | None]) -> bool:
    """A lost way at any old hostname also weakens coverage of its subtree."""
    for host in old:
        before = matching_sites(host, old)
        after = matching_sites(host, new)
        if not after or not strength("site", merge("site", *before.values())) <= strength(
                "site", merge("site", *after.values())):
            return True
    return False


def own_app_way(group_app_block: str | None, item_block_type: str | None, extra: str | None) -> str | None:
    """The own way an app member has since 0.84.13, from an old "extra on top" way: the merge of what it was blocked
    with (the group's way, else its own setting) and the extra - blocked exactly as before."""
    return merge("app", group_app_block or item_block_type, extra) if extra else extra


def group_way(group: dict, k: str) -> str | None:
    """What the group chose for its members of this kind (None: not chosen)."""
    return group.get(GROUP_KEYS[k])


def member_own(group: dict, item_id) -> str | None:
    """What the member adds in this group - for an app, the way it has instead of the group's (None: nothing)."""
    return (group.get("member_blocks") or {}).get(item_id)


def in_group(item: dict, group: dict, own: bool = True) -> str | None:
    """How `group` blocks this member: the group's way (else the member's own setting, as before 0.84.11) plus,
    with own=True, what the member adds on top."""
    k = kind(item)
    base = group_way(group, k)
    base = base if base is not None else item.get("block_type")
    extra = member_own(group, item["id"]) if own else None
    if extra and k == "app":
        return extra          # an app's own way replaces the group's
    return merge(k, base, extra) if extra else base


def rule_way(item: dict, groups_by_id: dict, rule: dict) -> str | None:
    """The way the item is blocked while this rule (one of effective_rules) blocks it."""
    g = rule.get("group") or rule.get("extra_of")
    if g and g["id"] in groups_by_id:
        return in_group(item, groups_by_id[g["id"]])
    return item.get("block_type")


def blocking_way(item: dict, groups: list[dict], blocking: list[dict]) -> str | None:
    """The way the item is blocked while these rules (the ones blocking it now) block it."""
    by_id = {g["id"]: g for g in groups}
    ways = {rule_way(item, by_id, r) for r in blocking}
    if not ways:
        return item.get("block_type")
    if len(ways) == 1:
        return ways.pop()
    return merge(kind(item), *ways)
