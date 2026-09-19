"""When each blocked item is blocked across a week, for the Blocking > Calendar view.

Only rules that map to a time of day are shown: permanent (all day) and scheduled hours (allow / block). Time and
opening limits and temporary blocks aren't tied to a clock time, so they don't appear here. Minutes are 0..1440
from midnight; a window belongs to the day it starts on and spills into the next morning if it runs past midnight.
"""
from rules import effective_rules, load_schedule, parse_hhmm

DAY = 1440


def _minutes(hhmm: str) -> int:
    t = parse_hhmm(hhmm)
    return t.hour * 60 + t.minute


def _window_on(win: dict, weekday: int) -> list[tuple[int, int]]:
    """Minutes on `weekday` (0=Monday) that a single window covers, including overnight spill."""
    start, end, days = _minutes(win["start"]), _minutes(win["end"]), win["days"]
    if start < end:
        return [(start, end)] if weekday in days else []
    # overnight, or full 24h when start == end
    if start == end:
        return [(0, DAY)] if (weekday in days or (weekday - 1) % 7 in days) else []
    out = []
    if weekday in days:
        out.append((start, DAY))               # evening part on the start day
    if (weekday - 1) % 7 in days and end > 0:
        out.append((0, end))                   # morning part spilled from the day before
    return out


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _complement(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """[0, DAY] minus the (merged) intervals - i.e. blocked = outside the allowed windows."""
    out, cur = [], 0
    for a, b in _merge(intervals):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < DAY:
        out.append((cur, DAY))
    return out


def item_day_intervals(rules: list[dict], weekday: int) -> list[tuple[int, int]]:
    """Merged blocked minute-intervals for an item on `weekday`, from its permanent + scheduled rules."""
    blocked: list[tuple[int, int]] = []
    for r in rules:
        if r["rule_type"] == "permanent":
            return [(0, DAY)]
        if r["rule_type"] == "scheduled" and r.get("schedule"):
            s = load_schedule(r["schedule"])
            wins = [iv for w in s["windows"] for iv in _window_on(w, weekday)]
            blocked += wins if s["mode"] == "block" else _complement(wins)
    return _merge(blocked)


def week(items: list[dict], groups: list[dict]) -> list[list[dict]]:
    """For each weekday (Mon..Sun), a list of {item, intervals} for items blocked at some time that day (by a
    permanent or scheduled rule). Items with only limits / temporary blocks are left out."""
    out: list[list[dict]] = [[] for _ in range(7)]
    for item in items:
        rules = effective_rules(item, groups)
        for weekday in range(7):
            intervals = item_day_intervals(rules, weekday)
            if intervals:
                out[weekday].append({"item": item, "intervals": intervals})
    return out


def lanes(bars: list[dict]) -> int:
    """Greedy lane packing so overlapping bars stack; sets bar['lane'] and returns the lane count."""
    ends: list[int] = []   # end minute of the last bar in each lane
    for bar in sorted(bars, key=lambda b: b["start"]):
        for i, end in enumerate(ends):
            if bar["start"] >= end:
                bar["lane"], ends[i] = i, bar["end"]
                break
        else:
            bar["lane"] = len(ends)
            ends.append(bar["end"])
    return len(ends)
