"""Api for Blocking > Calendar (BLUEPRINT 3.2 Calendar): when each blocked thing is blocked, day by day.

`calendar_get(start, days, filter)` is the tab's one call. It works out in Python, once, everything the page paints:
the blocked minutes of every item and day (from its permanent and by-time rules, see blockcal.py), the emergency-unlock
marks, the right-hand totals and the column heading, and - in Day view - the three cards (next change, busiest stretch,
free window). The page only draws and handles clicks.

It reads the DRAFT (what is being edited, disabled items too - a disabled item is drawn faint, with "Disabled" instead of
a total, and stays out of the cards because it blocks nothing). Nothing here changes anything: a read. Changing the hours
(extending is free, shortening needs the challenge) is the Add / Edit tab's job and is decided there, in Python.
Limits that depend on use (time, opening) and temporary blocks have no clock time: they are listed by name, not drawn.
"""
from datetime import date, datetime, timedelta

import blockcal
import emergency
import stats
from rules import effective_rules

from . import categories as cats_api
from . import core
from . import draft as drafts
from .core import ApiError

DAY = blockcal.DAY
RANGES = (1, 3, 7)
FILTERS = {"all": None, "sites": "site", "apps": "app"}
HEADS = {1: "BLOCKED THAT DAY", 3: "3-DAY TOTAL", 7: "WEEK TOTAL"}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]
LIMIT_RULES = ("time_limit", "switch_limit")
MAX_LIMIT_NAMES = 30


@core.internal
def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


@core.internal
def span(a: int, b: int) -> str:
    return f"{hhmm(a)}–{hhmm(b)}"


@core.internal
def hours_text(minutes: int) -> str:
    if minutes >= DAY:
        return "all day"
    h, m = divmod(minutes, 60)
    return f"{h} h {m} m" if h and m else f"{h} h" if h else f"{m} m"


def _start_of(value, today: date, days: int) -> date:
    """The first shown day: `value` ("YYYY-MM-DD", or None = today); a week starts on Monday."""
    if value is None:
        start = today
    else:
        try:
            if type(value) is not str or len(value) != 10:
                raise ValueError
            start = date.fromisoformat(value)
        except ValueError:
            raise ApiError("Bad request: a date like 2026-10-11 expected.") from None
    return start - timedelta(days=start.weekday()) if days == 7 else start


def _title(dates: list[date]) -> str:
    first, last = dates[0], dates[-1]
    if len(dates) == 1:
        return f"{DAY_NAMES[first.weekday()]} {first.day:02d} {MONTH_NAMES[first.month - 1]}"
    if first.month == last.month:
        return f"{first.day:02d} – {last.day:02d} {MONTH_NAMES[first.month - 1]}"
    return f"{first.day:02d} {MONTH_NAMES[first.month - 1][:3]} – {last.day:02d} {MONTH_NAMES[last.month - 1][:3]}"


def _icon(item: dict) -> dict:
    kind = {"site": "site", "app": "app"}.get(item["item_type"], "category")
    return {"k": kind, "n": item["target"].split()[0] if item["target"].split() else item["display_name"]}


# ---------------------------------------------------------------- the Day-view cards

@core.internal
def summarize(rows: list[list[tuple[int, int]]], names: list[str], now_min: int | None, when: str) -> dict:
    """{next: {at, text, kind}, busiest, free} for one day. `rows`: each item's blocked intervals that day (items that
    block nothing - disabled ones - are left out by the caller). now_min: the minute of the day, or None when the day
    isn't today. `when`: "today" / "that day" for the sentences."""
    events = [(a, 0, name) for ivs, name in zip(rows, names) for a, _ in ivs if a > 0] \
        + [(b, 1, name) for ivs, name in zip(rows, names) for _, b in ivs if b < DAY]
    e = None if now_min is None else min((e for e in events if e[0] > now_min), default=None)
    if e:
        nxt = {"at": hhmm(e[0]), "text": f"{e[2]} {'allowed again' if e[1] else 'blocked'}",
               "kind": "allowed" if e[1] else "blocked"}
    else:
        nxt = {"at": None, "text": "(not today)" if now_min is None else "no more changes today", "kind": None}
    # how many items are blocked at each minute: a sweep over the interval ends
    changes: dict[int, int] = {}
    for ivs in rows:
        for a, b in ivs:
            changes[a] = changes.get(a, 0) + 1
            changes[b] = changes.get(b, 0) - 1
    segments: list[list[int]] = []       # [start, end, count], neighbours with the same count merged
    count, last = 0, 0
    for at in sorted(set(changes) | {0, DAY}):
        if at > last:
            if segments and segments[-1][2] == count:
                segments[-1][1] = at
            else:
                segments.append([last, at, count])
        count += changes.get(at, 0)
        last = at
    top = max(segments, key=lambda s: (s[2], s[1] - s[0]), default=[0, DAY, 0])
    if top[2]:
        busiest = f"{span(top[0], top[1])} — {top[2]} item{'s' * (top[2] != 1)} blocked" \
                  + (" together" if top[2] > 1 else "")
    else:
        busiest = f"nothing is blocked {when}"
    gaps = [s for s in segments if s[2] == 0]
    if gaps:
        g = max(gaps, key=lambda s: s[1] - s[0])
        free = f"{span(g[0], g[1])} — nothing but the lists"
    else:
        free = f"none {when} — something is blocked all day"
    return {"next": nxt, "busiest": busiest, "free": free}


# ---------------------------------------------------------------- Api

@core.read
def calendar_get(ctx, start=None, days=1, filter="all"):
    """The Calendar tab: {now, today, start, prev, next, days, title, is_today, head, dates, rows, limits, categories,
    summary}.
    start: "YYYY-MM-DD" (null = today; a week snaps to its Monday). days: 1, 3 or 7. filter: all / sites / apps.
    dates: [{date, label, today}]. rows (by name): {id, name, type, icon, cat, disabled, days: [[[from, to], ...]
    per day] (minutes 0..1440), marks: [[minute, ...] per day] (emergency unlocks) with mark_tips: [[text, ...] per day]
    (the same marks, "Emergency unlock 14:05–14:25 (YouTube, Reddit)"), sum, sub}. limits: {names,
    more} of items with a limit that depends on use (not drawn). categories: [{key, name, color, color_light}].
    summary: {next, busiest, free} in Day view, else null. prev / next: the `start` of the previous / next page."""
    if type(days) is not int or days not in RANGES:
        raise ApiError("Bad request: 1, 3 or 7 days expected.")
    if filter not in FILTERS:
        raise ApiError("Bad request: unknown filter.")
    kind = FILTERS[filter]
    db, now = ctx.db, ctx.now()
    today, now_min = now.date(), now.hour * 60 + now.minute
    first = _start_of(start, today, days)
    dates = [first + timedelta(days=i) for i in range(days)]
    today_i = dates.index(today) if today in dates else -1

    d = drafts.draft_of(ctx)
    with drafts.lock:
        d.refresh_if_clean()
        all_items = list(d.items.values())
        groups = list(d.groups.values())
    cats = cats_api.load(db)
    cat_keys = {c["key"] for c in cats}
    saved = db.categories()
    marks_of: dict[int, list[list[int]]] = {}
    tips_of: dict[int, list[list[str]]] = {}
    for day, unlocks in emergency.unlocks_by_day(db, first).items():
        if day <= dates[-1]:
            i = (day - first).days
            for u in unlocks:
                for item_id in u["item_ids"]:
                    marks_of.setdefault(item_id, [[] for _ in dates])[i].append(u["started"].hour * 60 + u["started"].minute)
                    tips_of.setdefault(item_id, [[] for _ in dates])[i].append(emergency.unlock_line(u, names=True))

    rows, limited, cards_rows, cards_names = [], [], [], []
    for item in sorted(all_items, key=lambda i: i["display_name"].lower()):
        if kind is not None and item["item_type"] != kind:
            continue
        disabled = bool(item.get("disabled"))
        # a disabled item is drawn as it would block (its rules, with its own pause lifted)
        rules = effective_rules({**item, "disabled": False} if disabled else item, groups)
        if any(r["rule_type"] in LIMIT_RULES for r in rules):
            limited.append(item["display_name"])
        per_day = [blockcal.item_day_intervals(rules, day.weekday()) for day in dates]
        marks = marks_of.get(item["id"])
        if not any(per_day) and not marks:
            continue
        total = sum(b - a for ivs in per_day for a, b in ivs)
        if days == 1:
            sub = ", ".join(span(a, b) for a, b in per_day[0])
            when = "today" if today_i == 0 else "that day"
            text = "Disabled" if disabled else (f"{hours_text(total)} blocked" if total < DAY else "blocked all day") \
                if total else f"not blocked {when}"
        else:
            h, m = divmod(total, 60)
            text = "Disabled" if disabled else ((f"{h} h {m} m" if m else f"{h} h") if total else "-")
            sub = "blocked this week" if days == 7 else "blocked in 3 days"
        if disabled:
            sub = sub if days == 1 and sub else "not enforced"
        if item["item_type"] == "category":
            cat = item["target"] if item["target"] in cat_keys else "neutral"
        else:
            cat = stats.category_of(item["item_type"], item["target"].split()[0] if item["target"].split() else "",
                                    saved, all_items)
        rows.append({"id": item["id"], "name": item["display_name"], "type": item["item_type"], "icon": _icon(item),
                     "cat": cat, "disabled": disabled, "days": per_day,
                     "marks": marks or [[] for _ in dates],
                     "mark_tips": tips_of.get(item["id"]) or [[] for _ in dates], "sum": text, "sub": sub})
        if not disabled:
            cards_rows.append(per_day[0])
            cards_names.append(item["display_name"])

    is_today = days == 1 and today_i == 0
    summary = None
    if days == 1:
        summary = summarize(cards_rows, cards_names, now_min if is_today else None, "today" if is_today else "that day")
    head = "BLOCKED TODAY" if is_today else HEADS[days]
    return {"now": now.strftime("%Y-%m-%dT%H:%M:%S"), "today": today.isoformat(), "start": first.isoformat(),
            "prev": (first - timedelta(days=days)).isoformat(), "next": (first + timedelta(days=days)).isoformat(),
            "days": days, "title": _title(dates), "is_today": is_today, "head": head, "filter": filter,
            "dates": [{"date": x.isoformat(), "label": DAY_NAMES[x.weekday()][:3] if days == 7 else
                       f"{DAY_NAMES[x.weekday()][:3]} {x.day:02d}", "today": x == today} for x in dates],
            "rows": rows, "limits": {"names": limited[:MAX_LIMIT_NAMES], "more": max(0, len(limited) - MAX_LIMIT_NAMES)},
            "categories": [{k: c[k] for k in ("key", "name", "color", "color_light")} for c in cats],
            "summary": summary}
