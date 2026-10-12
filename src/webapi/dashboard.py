"""Api for the Dashboard (BLUEPRINT 3.1) and the Display settings dialog (3.11).

`dashboard_get` is the one call the Dashboard makes: a status snapshot (resident/snapshot.py - items, groups, usage and
what is blocked now, read once), today's rows from the activity log, and a few cheap roll-up queries. It works out only
the cards that are shown (a card hidden in Display settings costs nothing) and `upcoming` once. Everything the page
needs is plain text and numbers: the page formats nothing but the clock-driven countdowns.

The logic is the old window's (limit_entries, upcoming, the stat cards), kept word for word where it decides what the
owner reads.

Nothing here can loosen a block. Hiding a card or picking Screen Time's first tab is cosmetic (`ui`).
"""
import json
from collections import Counter
from datetime import datetime, timedelta

import emergency
import group_look
import stats
from resident import snapshot
from rules import (DAY_NAMES, OPEN_LIMIT_FIELDS, PERIOD_WORDS, TIME_LIMIT_FIELDS, allowance_left, allowance_owner,
                   block_end, blocked_text, effective_rules, item_block, limits, next_block, opening_bucket,
                   period_words, rule_block, time_bucket)

from . import categories, core
from .core import ApiError

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
GOAL_KEY = "stats.goal_hours"          # daily screen-time goal in hours, "0" = off (Settings writes it)
DEFAULT_GOAL_HOURS = "5"
HIDDEN_KEY, ORDER_KEY = "dash.hidden", "dash.order"      # JSON lists of card keys (HIDDEN_KEY is the old window's too)
VISITS_OPEN_KEY = "dash.visits_open"   # "1": the list of blocked visits is shown (default: only the count)
ST_TAB_KEY, ST_RANGE_KEY = "screentime.tab", "screentime.range"
CACHE_SEC = 60                         # "time saved" and the streaks change slowly and cost the most to work out
MAX_TIMELINE_SEGMENTS = 150            # the timeline draws the longest ones anyway (charts.js MAX_SEGMENTS)

# key -> what the Display settings dialog calls it. This order is the default order of the cards.
CARDS = {"stats": "Numbers at the top", "today": "Today", "limits": "Limits today", "coming": "Coming up",
         "week": "Last 7 days", "visits": "Blocked visits today", "glance": "At a glance",
         "active": "Active blocks", "bedtime": "Bedtime", "pause": "Pause my blocks",
         "emergency": "Emergency unlocks left"}
ST_TABS = ("Overview", "Apps", "Websites", "Switches")     # (the Calendar tab is Screen Time's own page)
ST_RANGES = tuple(stats.RANGES)
COMING_MAX, VISITS_MAX, ACTIVE_MAX = 4, 4, 6
LOW_LIMIT_SEC = 30 * 60                # "about N minutes of use left" shows from this little left
GOOD, WARN, BAD = "ok", "warn", "bad"  # the tone of a number: green, amber, red (muted = no tone)


# ---------------------------------------------------------------- Display settings (which cards, in what order)

def _json_list(db, key) -> list:
    try:
        value = json.loads(db.get_setting(key, "[]") or "[]")
    except ValueError:
        return []
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


@core.internal
def layout(db) -> tuple[list, list]:
    """(order, hidden): every card key once, in the owner's order (new cards go last), and the hidden ones."""
    order = [k for k in dict.fromkeys(_json_list(db, ORDER_KEY)) if k in CARDS]
    order += [k for k in CARDS if k not in order]
    return order, [k for k in order if k in _json_list(db, HIDDEN_KEY)]


@core.read
def display_get(ctx):
    """The Display settings dialog: {cards: [{key, title, shown}] in order, st_tab, st_range, tabs, ranges}."""
    db = ctx.db
    order, hidden = layout(db)
    tab, rng = db.get_setting(ST_TAB_KEY, ST_TABS[0]), db.get_setting(ST_RANGE_KEY, ST_RANGES[0])
    return {"cards": [{"key": k, "title": CARDS[k], "shown": k not in hidden} for k in order],
            "st_tab": tab if tab in ST_TABS else ST_TABS[0], "st_range": rng if rng in ST_RANGES else ST_RANGES[0],
            "tabs": list(ST_TABS), "ranges": list(ST_RANGES)}


@core.ui
def display_set(ctx, hidden, order, st_tab=None, st_range=None):
    """Save the Display settings (the dialog saves at every click): the hidden cards, the order of all cards, and the
    tab / range Screen Time opens with (never Calendar; None = leave as it is). Cosmetic, so ungated; anything
    unknown is refused. Returns what display_get says now."""
    if not isinstance(hidden, list) or not isinstance(order, list) or not all(isinstance(k, str) for k in hidden + order):
        raise ApiError("Bad request.")
    if any(k not in CARDS for k in hidden + order):
        raise ApiError("Unknown card.")
    if st_tab is not None and st_tab not in ST_TABS:
        raise ApiError("Screen Time can open on Overview, Apps, Websites or Switches.")
    if st_range is not None and st_range not in ST_RANGES:
        raise ApiError("Pick Today, Yesterday, 7 days or 30 days.")
    order = list(dict.fromkeys(order))
    order += [k for k in CARDS if k not in order]
    db = ctx.db
    db.set_setting(HIDDEN_KEY, json.dumps([k for k in order if k in hidden]))
    db.set_setting(ORDER_KEY, json.dumps(order))
    if st_tab is not None:
        db.set_setting(ST_TAB_KEY, st_tab)
    if st_range is not None:
        db.set_setting(ST_RANGE_KEY, st_range)
    return display_get(ctx)


# ---------------------------------------------------------------- text

@core.internal
def when_text(when: datetime, now: datetime) -> str:
    return f"{when:%H:%M}" if when.date() == now.date() else f"{DAY_NAMES[when.weekday()][:3]} {when:%H:%M}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def _catalog_name(catalog, exe: str) -> str | None:
    """The app catalog's name for a program (resident/catalog.py, the Start Menu list), once it has loaded."""
    if catalog is None or not catalog.loaded.is_set():
        return None
    stem = exe[:-4] if exe.endswith(".exe") else exe
    try:
        found = catalog.search("all", stem, limit=30)["apps"]
    except Exception:
        return None
    return next((a["name"] for a in found if a["exe"].lower() == exe and a["name"]), None)


def _app_name(exe: str, items: list[dict], catalog=None, cached=None) -> str:
    """The blocklist's name for an app, else the app catalog's, else its file name made readable (Chrome, Code ...)."""
    if exe == "pythonw.exe":     # Lockdown's own window
        return "Lockdown"
    for item in items:
        if item["item_type"] == "app" and item["target"].lower() == exe:
            return item["display_name"]
    if catalog is not None:
        name = cached(("app-name", exe), lambda: _catalog_name(catalog, exe)) if cached else _catalog_name(catalog, exe)
        if name:
            return name
    stem = exe[:-4] if exe.endswith(".exe") else exe
    return stem[:1].upper() + stem[1:]


def _name_of(kind: str, name: str, items: list[dict], catalog=None, cached=None) -> str:
    return _app_name(name, items, catalog, cached) if kind == "app" else name


def _icon(item: dict | None, fallback: str = "?") -> list:
    """[kind, name] for the page's icon (js/appicon.js): an app's exe, a site's first host, else a letter tile."""
    if item and item.get("item_type") == "app":
        return ["app", item["target"].lower()]
    if item and item.get("item_type") == "site" and item["target"].split():
        return ["site", item["target"].split()[0].lower()]
    return ["letter", (item or {}).get("display_name") or fallback]


@core.internal
def iso(when) -> str | None:
    return snapshot.iso(when)


# ---------------------------------------------------------------- upcoming and the limits (the old window's logic)

@core.internal
def upcoming(now, items, groups, usage) -> list[dict]:
    """Blocks starting / ending within 24 h, and time limits about to run out (items in use)."""
    horizon = now + timedelta(hours=24)
    merged: dict[tuple, dict] = {}
    for item in items:
        rules = effective_rules(item, groups)
        block = item_block(rules, now, usage)
        if block:
            until, rule = block[1], block[2]
            if until and until <= horizon:
                group = rule.get("group")
                key = ("end", until, group["id"] if group else f"i{item['id']}")
                title = f"{group['name']} ends" if group else f"{item['display_name']} allowed again"
                merged.setdefault(key, {"kind": "end", "when": until, "title": title, "names": []})["names"].append(
                    item["display_name"])
            continue
        nb = next_block(rules, now, usage)
        if nb and nb[0] <= horizon:
            when, rule = nb
            key = ("start", when, rule["group"]["id"] if rule.get("group") else f"i{item['id']}")
            title = f"{rule['group']['name']} starts" if rule.get("group") else f"{item['display_name']} blocked"
            merged.setdefault(key, {"kind": "start", "when": when, "title": title, "names": []})["names"].append(
                item["display_name"])
        for r in rules:
            # (a limit set per weekday: today's amount)
            if r["rule_type"] == "time_limit" and (day := limits(r, TIME_LIMIT_FIELDS, now, usage.clock).get("day")):
                left = day * 60 - usage(r["usage_owner"], time_bucket("day", now, usage.clock))
                # several day limits (the group's, its own extra): the row is the one with least left
                if 0 < left <= LOW_LIMIT_SEC and left < merged.get(("limit", item["id"]), {}).get("left", left + 1):
                    merged[("limit", item["id"])] = {"kind": "limit", "when": None, "left": left,
                                                     "title": f"{item['display_name']} limit will be reached",
                                                     "names": []}
    return sorted(merged.values(), key=lambda e: (e["when"] is not None, e["when"] or now))


@core.internal
def limit_entries(now, items, groups, usage) -> list[dict]:
    """The Limits card's rows, fullest first: one per limit - a group's shared limit once, a member's own limit
    in a group (on top of the group's) as a row of its own - and one per allowance in use.
    Something blocked gets one row, blocked = "Blocked (until ...)", without used / left times; and only once:
    a group whose members are all blocked is that one row, so neither its allowance nor a member's own limit
    shows the same block again (nor a member's own limit and its allowance)."""
    clock = usage.clock
    seen, entries, allowances = set(), [], {}
    live = {i["id"] for i in items if not i.get("disabled")}
    members = {g["id"]: {i for i in g["members"] if i in live} for g in groups}
    blocks = {i["id"]: block_end(effective_rules(i, groups), now, usage) for i in items}   # how long each stays blocked

    def entry(name, item, rule, pot, kind, frac, period, used, limit):
        group = pot.startswith("group:")
        return {"name": name, "item": item, "group": rule["group"]["name"] if rule.get("group") else None,
                "rule": rule, "kind": kind, "frac": frac, "period": period, "used": used, "limit": limit,
                "members": members[rule["group"]["id"]] if group else {item["id"]}, "blocked": None}

    for item in items:
        for r in effective_rules(item, groups):
            spent = allowance_left(r, now, usage)   # "N min allowed during blocked hours", while inside them
            pot = allowance_owner(r)    # a group's allowance is one pot, so it is one row
            if spent:
                used, allowed, until = spent
                key = (pot, "allowance", r["rule_key"], until)
                # one pot, two allowances (a group's per-member one and the member's own extra, ending at the
                # same minute): the row shows the one with least left - the one that really limits it
                if key not in allowances or allowed - used < allowances[key][1]:
                    name = f"{r['group']['name']} (group)" if pot.startswith("group:") else item["display_name"]
                    row = entry(name, item, r, pot, "allowance", used / max(allowed, 1), until, used, allowed)
                    if key in allowances:
                        entries[allowances[key][0]] = row
                        allowances[key] = (allowances[key][0], allowed - used)
                    else:
                        allowances[key] = (len(entries), allowed - used)
                        entries.append(row)
            # one row per limit: a group's shared limit once, and a member's own limit in a group (on top of
            # the group's) as a row of its own besides the group's
            key = (r["usage_owner"], r["rule_key"])
            if r["rule_type"] not in ("time_limit", "switch_limit") or key in seen:
                continue
            seen.add(key)
            is_time = r["rule_type"] == "time_limit"
            best = None
            for period, limit in limits(r, TIME_LIMIT_FIELDS if is_time else OPEN_LIMIT_FIELDS, now, clock).items():
                bucket = time_bucket(period, now, clock) if is_time else opening_bucket(r, period, now, clock)
                used = usage(r["usage_owner"], bucket)
                frac = used / (limit * 60 if is_time else max(limit, 1))
                if best is None or frac > best[0]:
                    best = (frac, period, used, limit)
            if best:
                group = r["usage_owner"].startswith("group:")
                name = f"{r['group']['name']} (group)" if group else item["display_name"]
                if r.get("extra_of"):
                    name += f" (own, in {r['extra_of']['name']})"
                entries.append(entry(name, item, r, r["usage_owner"], "time" if is_time else "opens", *best))
    # blocked: every item the row is about is blocked now. Until: when this row's own rule lets go, if it is
    # the one blocking; otherwise (blocked by something else) when the item is allowed again. A row for one item
    # says when that item is allowed again, if that is later than its own rule's end (as its Status column does).
    covered, dropped = [], set()
    for n, e in sorted(enumerate(entries), key=lambda ne: -len(ne[1]["members"])):   # a group's row first
        if not all(blocks.get(i) for i in e["members"]):
            continue
        ends = [blocks[i] for i in e["members"]]
        if any(e["members"] <= c and max(ends) <= c_until for c, c_until in covered):
            dropped.add(n)   # the same block is already a row (a member blocked longer keeps its own row)
            continue
        own = rule_block(e["rule"], now, usage)
        until = (datetime.max if own[1] is None else own[1]) if own else max(ends)
        if len(e["members"]) == 1:
            until = max(until, ends[0])
        e["blocked"] = blocked_text(until, now)
        e["frac"] = 1.0
        covered.append((e["members"], until))
    shown = [e for n, e in enumerate(entries) if n not in dropped]
    shown.sort(key=lambda e: -e["frac"])
    return shown


# ---------------------------------------------------------------- one Dashboard computation

class _Day:
    """What the cards share, each worked out the first time a shown card asks for it."""

    def __init__(self, db, snap, cache=None, catalog=None):
        self.db, self.snap, self.catalog = db, snap, catalog
        self.cache = {} if cache is None else cache       # kept between calls (ctx.dash_cache): key -> (when, value)
        self.now = snap.now
        self.today = self.now.date()
        self.tomorrow = self.today + timedelta(days=1)
        self.items, self.groups, self.usage = list(snap.items), list(snap.groups), snap.usage
        self._memo = {}

    def cached(self, key, make):
        """make() at most once per CACHE_SEC for the same key (the key says what the answer depends on)."""
        import time
        now = time.monotonic()
        hit = self.cache.get(key)
        if hit is None or now - hit[0] > CACHE_SEC:
            if len(self.cache) > 20:
                self.cache.clear()
            hit = self.cache[key] = (now, make())
        return hit[1]

    def memo(self, key, make):
        if key not in self._memo:
            self._memo[key] = make()
        return self._memo[key]

    @property
    def unlocks(self):
        """{day: [unlock, ...]} of the last 7 days' emergency unlocks (what the week bars and the Emergency card show)."""
        return self.memo("unlocks", lambda: emergency.unlocks_by_day(self.db, self.today - timedelta(days=6)))

    @property
    def rows(self):
        return self.memo("rows", lambda: stats.activity(self.db, self.today, self.tomorrow))

    @property
    def events(self):
        return self.memo("events", lambda: stats.switches(self.db, self.today, self.tomorrow))

    @property
    def coming(self):
        return self.memo("coming", lambda: upcoming(self.now, self.items, self.groups, self.usage))

    @property
    def blocked_today(self):
        return self.memo("blocked_today", lambda: stats.blocked_events(self.db, self.today))

    @property
    def goal(self):
        def read():
            try:
                hours = float(self.db.get_setting(GOAL_KEY, DEFAULT_GOAL_HOURS) or 0)
            except ValueError:
                hours = 0.0
            return hours * 3600 or None
        return self.memo("goal", read)


# ---- the cards. Each takes the _Day and returns plain data for the page.

def _active_until(db, day, hhmm: str) -> int:
    """Active seconds on `day` up to HH:MM (a string compare, as the old window did), in SQL."""
    row = db.conn.execute("SELECT COALESCE(SUM(active_seconds), 0) FROM activity WHERE minute >= ? AND minute < ? "
                          "AND substr(minute, 12) <= ?", (f"{day} 00:00", f"{day + timedelta(days=1)} 00:00", hhmm)).fetchone()
    return row[0]


def _screen_stat(d: _Day) -> dict:
    active = stats.totals(d.rows)[0]
    yesterday = _active_until(d.db, d.today - timedelta(days=1), f"{d.now:%H:%M}")
    diff = active - yesterday
    if abs(diff) < 60:
        text, tone = "about the same as yesterday", "muted"
    else:
        text, tone = f"{stats.hm(abs(diff))} {'more' if diff > 0 else 'less'} than yesterday", WARN if diff > 0 else GOOD
    return {"sec": active, "yesterday_sec": yesterday, "value": stats.hm(active), "note": text, "tone": tone}


def _blocked_stat(d: _Day) -> dict:
    snap, total = d.snap, len(d.items)
    starts = [e for e in d.coming if e["kind"] == "start"]
    nxt = f"Next: {starts[0]['title']} at {when_text(starts[0]['when'], d.now)}" if starts else "Nothing else coming up today"
    if snap.off:    # rules still say "blocked", but nothing acts on them
        return {"value": f"0 of {total}", "note": "Lockdown is off - not enforced", "tone": BAD, "next": nxt}
    if not snap.running:
        return {"value": f"0 of {total}", "note": "Service stopped - not enforced", "tone": BAD, "next": nxt}
    return {"value": f"{snap.blocks['count']} of {total}", "note": nxt, "tone": "muted", "next": nxt}


def _switch_stat(d: _Day) -> dict:
    n = d.db.conn.execute("SELECT COUNT(*) FROM switch_events WHERE timestamp >= ? AND timestamp < ?",
                          (f"{d.today} 00:00:00", f"{d.tomorrow} 00:00:00")).fetchone()[0]
    avg = stats.average_daily_switches(d.db, d.today, until=d.now.time())
    if avg is None or abs(n - avg) < 1:
        text, tone = ("no history yet" if avg is None else "about usual for this time of day"), "muted"
    else:
        text = f"{abs(round(n - avg))} {'more' if n > avg else 'fewer'} than usual by this time"
        tone = WARN if n > avg else GOOD
    return {"value": str(n), "note": text, "tone": tone}


def _saved_stat(d: _Day) -> dict:
    events = d.blocked_today
    saved = 0.0
    if events:     # (the 30 days of visits are only needed to price a blocked visit)
        # The 30 days before today are summed in SQL and kept for the day; only the last old visit (it ends at
        # today's first switch) and today's own visits are worked out live - the same number as time_saved gives.
        totals, last = d.cached(("saved-past", d.today),
                                lambda: stats.visit_totals(d.db, d.today - timedelta(days=30), d.today))
        live = dict(totals)
        for (_, name), sec in stats.visits(([last] if last else []) + d.events, d.now):
            have = live.get(name, (0, 0))
            live[name] = [have[0] + sec, have[1] + 1]
        saved = stats.time_saved_totals(events, d.items, live)
    return {"value": stats.hm(saved), "note": f"{_plural(len(events), 'blocked visit')} turned away", "tone": "muted"}


def _card_stats(d: _Day) -> dict:
    return {"screen": _screen_stat(d), "blocked": _blocked_stat(d), "switches": _switch_stat(d), "saved": _saved_stat(d)}


def _css_color(cat: dict) -> str:
    """A built-in category keeps the theme's own colour until the owner picks one; their own colours are as chosen."""
    own = {"productive": "var(--cat-prod)", "neutral": "var(--cat-neutral)", "distracting": "var(--cat-dist)"}
    defaults = {c[0]: c[2] for c in categories.BUILTIN}
    if cat["key"] in own and cat["color"] in defaults[cat["key"]] and cat["color_light"] in defaults[cat["key"]]:
        return own[cat["key"]]
    return cat["color"]


def _card_today(d: _Day) -> dict:
    db = d.db
    saved, cats = db.categories(), categories.load(db)
    known = {c["key"] for c in cats}

    seen = {}

    def category(exe, site):
        if (exe, site) not in seen:
            cat = stats.category_of(*(("site", site) if site else ("app", exe)), saved, d.items)
            seen[exe, site] = cat if cat in known else "neutral"
        return seen[exe, site]
    segments = stats.timeline(d.rows, d.today, category)
    if len(segments) > MAX_TIMELINE_SEGMENTS:
        segments = sorted(sorted(segments, key=lambda g: -g[1])[:MAX_TIMELINE_SEGMENTS])
    start_hour = min(6, int(d.rows[0]["minute"][11:13])) if d.rows else 6
    used = {kind for _, _, kind in segments}
    colors = {c["key"]: _css_color(c) for c in cats}
    legend = [{"key": c["key"], "name": c["name"], "color": colors[c["key"]]} for c in cats
              if c["key"] in used or c["builtin"]] + [{"key": "idle", "name": "Idle", "color": "var(--cat-idle)"}]
    return {"segments": [{"start": s, "len": n, "kind": k} for s, n, k in segments], "start_hour": start_hour,
            "colors": colors, "names": {**{c["key"]: c["name"] for c in cats}, "idle": "Idle"}, "legend": legend}


def _limit_row(e: dict, now, usage, looks: dict | None = None) -> dict:
    """`looks`: {group id: group_look.look(group)} - a group's row also says its icon and colour ({look: {icon, color}})."""
    name, kind, frac, period, used, limit = (e[k] for k in ("name", "kind", "frac", "period", "used", "limit"))
    item = e["item"]
    icon = ["letter", e["group"]] if e["group"] else _icon(item)
    look = (looks or {}).get(((e["rule"].get("group") or {}).get("id")) if e["group"] else None)
    tone = GOOD if frac < 0.6 else WARN if frac < 0.85 else BAD
    if e["blocked"]:     # one line, no used / left times: they can't be spent
        text, left, tone, label = "", e["blocked"], BAD, name
    elif kind == "allowance":    # here period is the end of the blocked stretch, and limit is in seconds
        spare = limit - used
        text = f"{stats.hm(used)} of {stats.hm(limit)} allowed during blocked hours (until {period:%H:%M})"
        left, label = (f"{stats.hm(spare)} left" if spare > 0 else "used up"), name + " - allowance"
    elif kind == "time":
        words = period_words(period, now, usage.clock)
        when = "" if words == PERIOD_WORDS["day"] else f" {words}"    # ("today" goes without saying here)
        text, spare = f"{stats.hm(used)} of {stats.hm(limit * 60)}{when}", limit * 60 - used
        left, label = (f"{stats.hm(spare)} left" if spare > 0 else "limit reached"), name
    else:
        text = f"{used} opens of {limit} {period_words(period, now, usage.clock)}"
        left, label = (f"{limit - used} left" if used < limit else "limit reached"), name + " - opens"
    row = {"key": f"{label}|{kind}", "name": label, "icon": icon, "left": left, "text": text, "tone": tone,
           "frac": round(min(max(frac, 0.0), 1.0), 4), "blocked": bool(e["blocked"])}
    if look:
        row["look"] = {"icon": look["icon"], "color": look["color"]}
    return row


def _card_limits(d: _Day) -> dict:
    entries = limit_entries(d.now, d.items, d.groups, d.usage)
    looks = {g["id"]: group_look.look(g) for g in d.groups}
    rows, seen = [], Counter()
    for e in entries:
        row = _limit_row(e, d.now, d.usage, looks)
        seen[row["key"]] += 1
        if seen[row["key"]] > 1:         # (two rows alike: the page still needs a key for each)
            row["key"] += f"#{seen[row['key']]}"
        rows.append(row)
    return {"count": len(rows), "note": f"{len(rows)} active", "rows": rows}


def _card_week(d: _Day) -> dict:
    start = d.today - timedelta(days=6)
    per_day = stats.daily_active(d.db, start, d.tomorrow)     # summed in SQL
    unlocks = d.unlocks
    bars = []
    for day in (start + timedelta(days=i) for i in range(7)):
        sec = per_day.get(day.isoformat(), 0)
        tip = f"{DAY_NAMES[day.weekday()]} {day.day} {MONTHS[day.month - 1]}  {stats.hm(sec)}"
        bars.append({"label": DAY_NAMES[day.weekday()][:3], "value": round(sec / 60, 1), "today": day == d.today,
                     "tip": "\n".join([tip] + [emergency.unlock_line(u) for u in unlocks.get(day, [])]),
                     "unlocks": len(unlocks.get(day, []))})
    goal = d.goal
    return {"bars": bars, "goal": round(goal / 60, 1) if goal else 0,
            "goal_label": f"{goal / 3600:g} h" if goal else "", "note": f"daily goal {goal / 3600:g} h" if goal else ""}


def _card_coming(d: _Day) -> dict:
    rows = []
    for e in d.coming[:COMING_MAX]:
        sub = (f"about {round(e['left'] / 60)} minutes of use left" if e["kind"] == "limit"
               else " · ".join(e["names"]) if len(e["names"]) > 1 else "")
        when = "Today" if e["when"] is None else when_text(e["when"], d.now)
        rows.append({"key": f"{e['kind']}|{e['title']}|{when}", "kind": e["kind"], "time": when, "title": e["title"],
                     "sub": sub, "at": iso(e["when"])})
    return {"rows": rows}


def _card_visits(d: _Day) -> dict:
    events = d.blocked_today
    by_id = {i["id"]: i for i in d.items}
    top = Counter((e["item_id"], e["name"]) for e in events).most_common(VISITS_MAX)
    rows = []
    for (item_id, name), count in top:
        item = by_id.get(item_id)
        rows.append({"key": f"{item_id}|{name}", "name": name, "icon": _icon(item, name), "count": count,
                     "text": _plural(count, "attempt")})
    return {"count": len(events), "open": d.db.get_setting(VISITS_OPEN_KEY, "0") == "1", "rows": rows}


def _streaks(d: _Day, goal) -> dict:
    """stats.streaks(db, today, goal), with the days before today kept for the day (key: the day, the goal, when
    recording started and what the emergency unlocks look like) and only today worked out live: today ends a streak
    when it is over the goal / has an unlock, else it adds one day to the run of the days before."""
    db, today = d.db, d.today
    first = stats.first_activity(db)
    start = max(first.date(), today - timedelta(days=366)) if first else None
    if start is None or start > today:
        return {"goal": 0, "no_unlock": 0}
    unlocks = tuple(db.conn.execute("SELECT COUNT(*), MAX(started) FROM emergency_unlocks").fetchone())

    def earlier() -> tuple[int, int]:
        run_goal, end = 0, today
        # (the run of days within the goal usually stops soon: a week is looked at first, then further back)
        for size in (7, 60, 400) if goal else ():
            lo = max(start, end - timedelta(days=size))
            active = stats.daily_active(db, lo, end)
            days = [end - timedelta(days=i) for i in range(1, (end - lo).days + 1)]
            over = next((n for n, day in enumerate(days) if active.get(day.isoformat(), 0) > goal), None)
            if over is not None:
                run_goal += over
                break
            run_goal += len(days)
            end = lo
        unlocked = {u["started"].date() for u in db.unlocks_since(datetime.combine(start, datetime.min.time()))}
        run_unlock = 0
        for day in (today - timedelta(days=i) for i in range(1, (today - start).days + 1)):
            if day in unlocked:
                break
            run_unlock += 1
        return run_goal, run_unlock
    run_goal, run_unlock = d.cached(("streaks-past", today, goal, start, unlocks), earlier)
    unlocked_today = db.conn.execute("SELECT 1 FROM emergency_unlocks WHERE started >= ? AND started < ? LIMIT 1",
                                     (f"{today} 00:00:00", f"{d.tomorrow} 00:00:00")).fetchone()
    over = stats.active_on(db, today) > goal if goal else True
    return {"goal": 0 if over else run_goal + 1, "no_unlock": 0 if unlocked_today else run_unlock + 1}


def _card_glance(d: _Day) -> dict:
    entries, now = [], d.now
    apps = stats.per_app(d.rows)
    if apps:
        exe, sec = apps.most_common(1)[0]
        entries.append({"label": "Top app today", "main": _app_name(exe, d.items, d.catalog, d.cached), "extra": stats.hm(sec)})
    summary = stats.switch_summary(d.events, now)
    if summary["targets"]:
        (kind, name), count, avg = summary["targets"][0]
        entries.append({"label": "Most switched to", "main": _name_of(kind, name, d.items, d.catalog, d.cached),
                        "extra": f"{count} switches · avg {stats.ms(avg)} per visit"})
    hours = sorted(summary["per_hour"])
    if len(hours) >= 2:
        span = range(hours[0], hours[-1] + 1)
        quiet = min(span, key=lambda h: summary["per_hour"].get(h, 0))
        n = summary["per_hour"].get(quiet, 0)
        entries.append({"label": "Quietest hour", "main": f"{quiet:02d}:00 - {quiet + 1:02d}:00",
                        "extra": f"{n} switch{'es' * (n != 1)}"})
    goal = d.goal
    streak = _streaks(d, goal)
    if goal:
        n = streak["goal"]
        entries.append({"label": "Goal streak", "main": f"{_plural(n, 'day')} in a row", "extra": "within your daily goal"})
    n = streak["no_unlock"]
    used = ", ".join(emergency.unlock_span(u["started"], u["until"]) for u in d.unlocks.get(d.today, [])) if not n else ""
    entries.append({"label": "No emergency unlock", "main": f"{_plural(n, 'day')} in a row",
                    "extra": f"used today {used}" if used else ""})
    return {"rows": entries}


def _card_active(d: _Day) -> dict:
    """What is blocked right now (the snapshot's list - empty while "Pause my blocks" runs)."""
    rows = [{"key": str(b["item"]["id"]), "name": b["item"]["display_name"], "icon": _icon(b["item"]),
             "text": blocked_text(b["until"], d.now)} for b in d.snap.block_list]
    rows.sort(key=lambda r: r["name"].lower())
    return {"count": len(rows), "rows": rows[:ACTIVE_MAX], "more": max(0, len(rows) - ACTIVE_MAX)}


def _card_bedtime(d: _Day) -> dict:
    import reminders
    s = reminders.load(d.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    out = {"on": bool(s["on"]), "bedtime": s["bedtime"], "wake": s["wake"], "heads_up": int(s["before"] or 0),
           "mode": "", "at": None}
    try:
        hour, minute = (int(x) for x in s["bedtime"].split(":"))
    except (ValueError, AttributeError):
        return out
    at = d.now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    out["at"] = iso(at if at > d.now else at + timedelta(days=1))
    if s.get("mode"):
        import modes
        out["mode"] = next((m["name"] for m in modes.load(d.db) if m["id"] == s["mode"]), "")
    return out


def _card_pause(d: _Day) -> dict:
    p = d.snap.pause
    return {"paused": bool(p), "until": p["until"] if p else None, "silent": bool(p and p["silent"])}


def _card_emergency(d: _Day) -> dict:
    e = d.snap.emergency
    kinds = [{"key": k["id"], "minutes": k["minutes"], "per": k["per"], "allowed": k["allowed"], "left": k["left"],
              "resets": k["resets"],
              "text": f"{k['left']} of {k['allowed']} left {PERIOD_WORDS[k['per']]}",
              "detail": f"{k['minutes']} min each"} for k in e["kinds"]]
    return {"enabled": e["enabled"], "usable": e["usable"], "left": e["left"], "kinds": kinds,
            "used_today": [emergency.unlock_span(u["started"], u["until"]) for u in d.unlocks.get(d.today, [])]}


BUILDERS = {"stats": _card_stats, "today": _card_today, "limits": _card_limits, "coming": _card_coming,
            "week": _card_week, "visits": _card_visits, "glance": _card_glance, "active": _card_active,
            "bedtime": _card_bedtime, "pause": _card_pause, "emergency": _card_emergency}


# ---------------------------------------------------------------- Api

@core.internal
def fresh_snapshot(ctx):
    """One status snapshot for this call (about a millisecond): the same reading the shell's status uses."""
    return snapshot.build(ctx.db, ctx.now())


@core.read
def dashboard_get(ctx):
    """Everything the Dashboard shows, in one call: {now, date, since, titles, status, empty, order, hidden, cards}.
    `status` is the snapshot's page part (the page then follows `status` pushes), `cards` has data for every card that
    is not hidden. With nothing ever recorded `empty` is true and there are no cards."""
    db = ctx.db
    snap = fresh_snapshot(ctx)
    d = _Day(db, snap, ctx.__dict__.setdefault("dash_cache", {}), getattr(ctx, "catalog", None))
    order, hidden = layout(db)
    today = d.today
    out = {"now": iso(d.now), "date": f"{DAY_NAMES[today.weekday()]}, {today.day} {MONTHS[today.month - 1]}",
           "since": None, "titles": CARDS, "status": snap.public(), "empty": stats.first_activity(db) is None, "order": order,
           "hidden": hidden, "cards": {}}
    if out["empty"]:
        return out
    if d.rows:
        out["since"] = d.rows[0]["minute"][11:]
    for key in order:
        if key not in hidden:
            out["cards"][key] = BUILDERS[key](d)
    return out
