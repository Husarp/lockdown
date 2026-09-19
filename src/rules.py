"""Block rule evaluation: is a rule blocking right now, why, and until when."""
import json
from datetime import datetime, time, timedelta

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# When several rules on one item are active, the reason shown is the first in this order.
REASON_ORDER = ["permanent", "temporary", "limit", "switches", "schedule"]
TIME_FMT = "%Y-%m-%d %H:%M:%S"
ALLOW, BLOCK = "allow", "block"   # hours rule mode: allow only during the windows / block during them
VISIT, SWITCH = "visit", "switch"  # opening limit counts: launches / new visits (default), or every switch to it
DEFAULT_VISIT_GAP_MIN = 5
# Limits can be set per day, week and month, in any combination (they stack).
PERIODS = ("day", "week", "month")
PERIOD_WORDS = {"day": "today", "week": "this week", "month": "this month"}
TIME_LIMIT_FIELDS = {"day": "daily_limit_min", "week": "weekly_limit_min", "month": "monthly_limit_min"}
OPEN_LIMIT_FIELDS = {"day": "daily_switch_limit", "week": "weekly_switch_limit", "month": "monthly_switch_limit"}
RESET_KEY = "limits.reset"   # setting: JSON written by change_reset()


def parse_hhmm(text: str) -> time:
    """'9:00' / '21:30' -> time. Raises ValueError."""
    h, m = text.strip().split(":")
    return time(int(h), int(m))


def make_schedule(mode: str, windows: list[tuple[list[int], str, str]]) -> str:
    """Validated schedule JSON. Each window: (days 0=Monday..6=Sunday, "HH:MM" start, "HH:MM" end);
    end <= start means the window runs past midnight."""
    if mode not in (ALLOW, BLOCK):
        raise ValueError(f"Unknown mode {mode!r}")
    if not windows:
        raise ValueError("Add at least one time window.")
    out = []
    for days, start, end in windows:
        if not days:
            raise ValueError("Every time window needs at least one day.")
        try:
            parse_hhmm(start), parse_hhmm(end)
        except ValueError:
            raise ValueError("Hours must look like 09:00 or 21:30.") from None
        out.append({"days": sorted(days), "start": start.strip(), "end": end.strip()})
    return json.dumps({"mode": mode, "windows": out})


def load_schedule(schedule_json: str) -> dict:
    s = json.loads(schedule_json)
    if "windows" not in s:  # 0.2.0 format: a single "block during" window
        s = {"mode": BLOCK, "windows": [{"days": s["days"], "start": s["start"], "end": s["end"]}]}
    return s


def window_until(win: dict, now: datetime) -> datetime | None:
    """If the window covers `now`, when it ends; else None.
    A window belongs to the day it starts on (Mon 21:00-07:00 covers Mon night into Tue morning)."""
    start, end, days = parse_hhmm(win["start"]), parse_hhmm(win["end"]), win["days"]
    today, t = now.date(), now.time().replace(second=0, microsecond=0)
    if start < end:
        if now.weekday() in days and start <= t < end:
            return datetime.combine(today, end)
        return None
    # overnight (or full 24h when start == end)
    if now.weekday() in days and t >= start:
        return datetime.combine(today + timedelta(days=1), end)
    if (now.weekday() - 1) % 7 in days and t < end:
        return datetime.combine(today, end)
    return None


def next_window_start(windows: list[dict], now: datetime) -> datetime | None:
    starts = []
    for offset in range(8):
        d = now.date() + timedelta(days=offset)
        for win in windows:
            if d.weekday() in win["days"]:
                start = datetime.combine(d, parse_hhmm(win["start"]))
                if start > now:
                    starts.append(start)
    return min(starts) if starts else None


def schedule_until(schedule_json: str, now: datetime) -> datetime | None:
    """If the hours rule blocks at `now`, until when (a datetime), else None."""
    s = load_schedule(schedule_json)
    ends = [u for u in (window_until(w, now) for w in s["windows"]) if u]
    if s["mode"] == BLOCK:
        return max(ends) if ends else None
    if ends:          # allow mode, inside an allowed window
        return None
    return next_window_start(s["windows"], now) or datetime.max


# ---------- limit periods ----------

def _parse_dt(text: str | None) -> datetime | None:
    return datetime.strptime(text, TIME_FMT) if text else None


class LimitClock:
    """When limit periods start and end. A limit day starts at the reset time (default midnight); a week starts
    on Monday and a month on the 1st, at that time (with a reset at 12:00 or later, the evening before: a day
    belongs to the date most of it falls on). Screen-time stats keep normal calendar days.

    After the reset time is changed, the day that was running goes on until the new time comes round after its
    normal end: that day is longer, never shorter. The running week and month are held the same way (they don't
    end before they would have). So changing the time - any number of times - never resets limits early."""

    def __init__(self, config: str | None = None):
        c = json.loads(config) if config else {}
        self.time = parse_hhmm(c.get("time", "00:00"))
        self.carry_start = _parse_dt(c.get("day_start"))   # the day running when the time was changed ...
        self.carry_until = _parse_dt(c.get("switch"))      # ... lasts until here
        self.holds = {k: (key, _parse_dt(until)) for k, (key, until) in c.get("hold", {}).items()}

    def day(self, now: datetime) -> tuple[datetime, datetime]:
        """(start, end) of the limit day containing now."""
        if self.carry_until and self.carry_start <= now < self.carry_until:
            return self.carry_start, self.carry_until
        start = datetime.combine(now.date(), self.time)
        if start > now:
            start -= timedelta(days=1)
        return start, start + timedelta(days=1)

    def period(self, kind: str, now: datetime) -> tuple[str, datetime]:
        """(key, end) of the day / week / month limit period containing now."""
        key, end = self._natural_period(kind, now)
        if kind in self.holds:   # the week / month running when the time was changed doesn't end early
            hold_key, until = self.holds[kind]
            if now < until and key != hold_key:
                return hold_key, until
        return key, end

    def _natural_period(self, kind: str, now: datetime) -> tuple[str, datetime]:
        start, end = self.day(now)
        if kind == "day":   # midnight days keep the plain date (the keys used before reset times existed)
            return (start.date().isoformat() if start.time() == time(0) else f"{start:%Y-%m-%dT%H:%M}"), end
        d = (start + timedelta(hours=12)).date()
        if kind == "week":
            first = d - timedelta(days=d.weekday())
            following, key = first + timedelta(days=7), f"w{first.isoformat()}"
        else:
            first = d.replace(day=1)
            following, key = (first + timedelta(days=32)).replace(day=1), f"m{d:%Y-%m}"
        boundary = datetime.combine(following, self.time)
        if self.time >= time(12):   # late reset: the period starts the evening before
            boundary -= timedelta(days=1)
        return key, max(end, boundary)


DEFAULT_CLOCK = LimitClock()


def change_reset(config: str | None, new_time: str, now: datetime) -> str:
    """New RESET_KEY value for a changed reset time. Raises ValueError for a badly written time.

    A change never ends the running day early, but it also never stacks: the running day is capped so it can't
    run past the end of the next day, no matter how many times the reset time is toggled."""
    clock = LimitClock(config)
    try:
        new = parse_hhmm(new_time)
    except ValueError:
        raise ValueError("The time must look like 04:00.") from None
    start, end = clock.day(now)
    switch = datetime.combine(end.date(), new)
    if switch < end:
        switch += timedelta(days=1)
    natural = LimitClock(json.dumps({"time": f"{clock.time:%H:%M}"}))   # the day as it would run with no carry
    cap = natural.day(now)[1] + timedelta(days=1)                       # never past the end of the next day
    switch = min(switch, cap)
    hold = {}
    for kind in ("week", "month"):
        key, until = clock.period(kind, now)
        hold[kind] = [key, until.strftime(TIME_FMT)]
    return json.dumps({"time": f"{new:%H:%M}", "day_start": start.strftime(TIME_FMT),
                       "switch": switch.strftime(TIME_FMT), "hold": hold})


class Usage:
    """usage(owner, bucket) -> seconds (or openings) used. Also carries what the rules need besides usage:
    the limit clock and the active emergency unlocks ({"item:<id>": until})."""

    def __init__(self, data: dict | None = None, clock: LimitClock = DEFAULT_CLOCK, unlocks: dict | None = None):
        self.data, self.clock, self.unlocks = data or {}, clock, unlocks or {}

    def __call__(self, owner: str, bucket: str) -> int:
        return self.data.get((owner, bucket), 0)


def _clock(usage) -> LimitClock:
    return getattr(usage, "clock", DEFAULT_CLOCK)


def _unlocked_until(rules: list[dict], now: datetime, usage) -> datetime | None:
    until = getattr(usage, "unlocks", {}).get(rules[0].get("item_owner")) if rules else None
    return until if until and now < until else None


# ---------- usage buckets ----------
# Usage is stored per (owner, bucket): owner "item:<id>" or "group:<id>" (shared group limit); bucket
# "day:<limit day>" / "week:w<monday>" / "month:m<YYYY-MM>" (time limits), "op:<rule key>:<period>" (opening
# limits), "win:<rule key>:<end of blocked stretch>" (allowance in blocked hours), "sw:<date>" (switch stats).

no_usage = Usage()


def day_bucket(now: datetime) -> str:
    return f"day:{now.date().isoformat()}"


def switch_bucket(now: datetime) -> str:
    return f"sw:{now.date().isoformat()}"


def switch_mode(rule: dict) -> str:
    return rule.get("switch_mode") or VISIT


def limits(rule: dict, fields: dict) -> dict[str, int]:
    """{period: limit} for the periods the rule sets."""
    return {p: rule[f] for p, f in fields.items() if rule.get(f) is not None}


def time_bucket(period: str, now: datetime, clock: LimitClock = DEFAULT_CLOCK) -> str:
    return f"{period}:{clock.period(period, now)[0]}"


def opening_bucket(rule: dict, period: str, now: datetime, clock: LimitClock = DEFAULT_CLOCK) -> str:
    return f"op:{rule.get('rule_key', '')}:{clock.period(period, now)[0]}"


def allowance_bucket(rule: dict, until: datetime) -> str:
    return f"win:{rule.get('rule_key', '')}:{until:%Y-%m-%dT%H:%M}"


def _owner(rule: dict) -> str:
    return rule.get("usage_owner", "item")


def _item_owner(rule: dict) -> str:
    return rule.get("item_owner", _owner(rule))


def effective_rules(item: dict, groups: list[dict]) -> list[dict]:
    """The item's own rules + the rules of every group it's in (with per-member customizations applied).
    Each rule gets: usage_owner (whose time counts), item_owner, rule_key (stable id), group (None or {id, name})."""
    me = f"item:{item['id']}"
    out = [{**r, "usage_owner": me, "item_owner": me, "rule_key": f"i{item['id']}{r['rule_type']}", "group": None}
           for r in item["rules"]]
    for g in groups:
        if item["id"] not in g["members"]:
            continue
        custom = g["members"][item["id"]] or {}
        for r in g["rules"]:
            t = r["rule_type"]
            if t in custom:   # customized for this member: counted for the member alone
                rule = {**custom[t], "rule_type": t, "usage_owner": me}
            else:             # inherited: a group daily (time or switch) limit is one shared total
                rule = {**r, "usage_owner": f"group:{g['id']}" if t in ("time_limit", "switch_limit") else me}
            out.append({**rule, "item_owner": me, "rule_key": f"g{g['id']}{t}",
                        "group": {"id": g["id"], "name": g["name"]}})
    return out


def rule_block(rule: dict, now: datetime, usage=no_usage) -> tuple[str, datetime | None] | None:
    """(reason, until) if this rule blocks at `now`, else None. until=None means indefinitely.
    usage(owner, bucket) -> seconds used."""
    kind = rule["rule_type"]
    if kind == "permanent":
        return "permanent", None
    if kind == "temporary" and rule.get("temp_until"):
        until = datetime.strptime(rule["temp_until"], TIME_FMT)
        return ("temporary", until) if now < until else None
    if kind == "scheduled" and rule.get("schedule"):
        until = schedule_until(rule["schedule"], now)
        if until is None:
            return None
        allowance = rule.get("allowance_min") or 0
        if allowance and usage(_item_owner(rule), allowance_bucket(rule, until)) < allowance * 60:
            return None   # still has allowance left in this blocked stretch
        return "schedule", (None if until == datetime.max else until)
    clock = _clock(usage)
    if kind == "time_limit":   # blocked until the end of every period whose limit is used up
        ends = [clock.period(p, now)[1] for p, limit in limits(rule, TIME_LIMIT_FIELDS).items()
                if usage(_owner(rule), time_bucket(p, now, clock)) >= limit * 60]
        return ("limit", max(ends)) if ends else None
    if kind == "switch_limit":  # the openings up to the limit are allowed; the next one is blocked
        ends = [clock.period(p, now)[1] for p, limit in limits(rule, OPEN_LIMIT_FIELDS).items()
                if usage(_owner(rule), opening_bucket(rule, p, now, clock)) > limit]
        return ("switches", max(ends)) if ends else None
    return None


def item_block(rules: list[dict], now: datetime, usage=no_usage) -> tuple[str, datetime | None, dict] | None:
    """(reason, until, rule) for the rule that blocks the item now (most important reason first), else None.
    Nothing blocks during an emergency unlock."""
    if _unlocked_until(rules, now, usage):
        return None
    active = [(*b, r) for r in rules if (b := rule_block(r, now, usage))]
    if not active:
        return None
    return min(active, key=lambda b: REASON_ORDER.index(b[0]))


def usage_targets(rules: list[dict], item_id: int, now: datetime,
                  clock: LimitClock = DEFAULT_CLOCK) -> set[tuple[str, str]]:
    """(owner, bucket) pairs to add time to while the item is being used."""
    me = f"item:{item_id}"
    targets = {(me, day_bucket(now))}
    for r in rules:
        if r["rule_type"] == "time_limit":
            targets |= {(_owner(r), time_bucket(p, now, clock)) for p in limits(r, TIME_LIMIT_FIELDS)}
        elif r["rule_type"] == "scheduled" and r.get("allowance_min"):
            until = schedule_until(r["schedule"], now)
            if until:
                targets.add((me, allowance_bucket(r, until)))
    return targets


def _opening_targets(rule: dict, now: datetime, clock: LimitClock) -> set[tuple[str, str]]:
    return {(_owner(rule), opening_bucket(rule, p, now, clock)) for p in limits(rule, OPEN_LIMIT_FIELDS)}


def switch_targets(rules: list[dict], item_id: int, now: datetime,
                   clock: LimitClock = DEFAULT_CLOCK) -> set[tuple[str, str]]:
    """(owner, bucket) pairs to add 1 to when you switch to the item."""
    targets = {(f"item:{item_id}", switch_bucket(now))}
    for r in rules:
        if r["rule_type"] == "switch_limit" and switch_mode(r) == SWITCH:
            targets |= _opening_targets(r, now, clock)
    return targets


def visit_targets(rules: list[dict], item: dict, now: datetime, launched: bool,
                  away_sec: float | None, clock: LimitClock = DEFAULT_CLOCK) -> set[tuple[str, str]]:
    """(owner, bucket) pairs to add 1 to for "launches / new visits" opening limits.
    Apps: counts when the program was just started. Sites: counts when it's in use again after being away
    for at least the rule's gap (away_sec None = not used before)."""
    targets = set()
    for r in rules:
        if r["rule_type"] != "switch_limit" or switch_mode(r) != VISIT:
            continue
        if item["item_type"] == "app":
            opened = launched
        else:
            gap = (r.get("visit_gap_min") or DEFAULT_VISIT_GAP_MIN) * 60
            opened = away_sec is None or away_sec >= gap
        if opened:
            targets |= _opening_targets(r, now, clock)
    return targets


def _schedule_next_start(schedule_json: str, now: datetime) -> datetime | None:
    """When the next blocked stretch starts (the rule isn't blocking now)."""
    s = load_schedule(schedule_json)
    if s["mode"] == BLOCK:
        return next_window_start(s["windows"], now)
    ends = [u for u in (window_until(w, now) for w in s["windows"]) if u]
    return min(ends) if ends else None


def next_block(rules: list[dict], now: datetime, usage=no_usage, in_use: bool = False) -> tuple[datetime, dict] | None:
    """(when, rule) of the next block for an item that isn't blocked now, or None.
    Usage-based blocks (time limit, allowance) are only predicted while the item is in use.
    During an emergency unlock: its end, if the item is blocked then (rule {"rule_type": "unlock"})."""
    if until := _unlocked_until(rules, now, usage):
        after = Usage(getattr(usage, "data", {}), _clock(usage))
        return (until, {"rule_type": "unlock", "group": None}) if item_block(rules, until, after) else None
    found = []
    for r in rules:
        kind = r["rule_type"]
        if kind == "scheduled" and r.get("schedule"):
            until = schedule_until(r["schedule"], now)
            if until is None:
                start = _schedule_next_start(r["schedule"], now)
                if start:
                    found.append((start, r))
            elif in_use and r.get("allowance_min"):   # inside the stretch, using the allowance
                left = r["allowance_min"] * 60 - usage(_item_owner(r), allowance_bucket(r, until))
                found.append((now + timedelta(seconds=max(0, left)), r))
        elif kind == "time_limit" and in_use and (lims := limits(r, TIME_LIMIT_FIELDS)):
            left = min(limit * 60 - usage(_owner(r), time_bucket(p, now, _clock(usage))) for p, limit in lims.items())
            found.append((now + timedelta(seconds=max(0, left)), r))
    return min(found, key=lambda f: f[0]) if found else None


def days_text(days: list[int]) -> str:
    """[0,1,2,3,4] -> 'Monday–Friday', [0,2,5,6] -> 'Monday, Wednesday, Saturday–Sunday'."""
    if days == list(range(7)):
        return "Every day"
    runs: list[list[int]] = []
    for d in sorted(days):
        if runs and d == runs[-1][-1] + 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    return ", ".join(DAY_NAMES[r[0]] if len(r) == 1 else f"{DAY_NAMES[r[0]]}–{DAY_NAMES[r[-1]]}" for r in runs)


def duration_text(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    h, m = divmod(minutes, 60)
    d, h = divmod(h, 24)
    if d:
        return f"{d}d {h}h"
    return f"{h}h {m:02d}m" if h else f"{m}m"


def describe_rule(rule: dict, now: datetime, usage=no_usage) -> str:
    kind = rule["rule_type"]
    if kind == "permanent":
        return "Permanent"
    if kind == "scheduled":
        s = load_schedule(rule["schedule"])
        label = "Allowed only" if s["mode"] == ALLOW else "Blocked"
        text = f"{label}:\n" + "\n".join(f"{days_text(w['days'])} {w['start']}-{w['end']}" for w in s["windows"])
        if rule.get("allowance_min"):
            text += f"\n+ {rule['allowance_min']} min allowed during blocked hours"
        return text
    if kind == "temporary":
        if rule.get("temp_until"):
            left = datetime.strptime(rule["temp_until"], TIME_FMT) - now
            return f"Temporary: {duration_text(left.total_seconds())} left"
        return f"Temporary: {duration_text(rule['duration_min'] * 60)} (starts when saved)"
    clock = _clock(usage)
    shared = " (shared)" if _owner(rule).startswith("group:") else ""
    if kind == "time_limit":
        parts = [f"{duration_text(usage(_owner(rule), time_bucket(p, now, clock)))} / {duration_text(limit * 60)} "
                 f"{PERIOD_WORDS[p]}" for p, limit in limits(rule, TIME_LIMIT_FIELDS).items()]
        return f"Limit{shared}: " + "\n".join(parts)
    if kind == "switch_limit":
        parts = [f"{usage(_owner(rule), opening_bucket(rule, p, now, clock))} / {limit} {PERIOD_WORDS[p]}"
                 for p, limit in limits(rule, OPEN_LIMIT_FIELDS).items()]
        what = "Switches" if switch_mode(rule) == SWITCH else "Openings"
        return f"{what}{shared}: " + "\n".join(parts)
    return kind
