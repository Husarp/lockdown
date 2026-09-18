"""Block rule evaluation: is a rule blocking right now, why, and until when."""
import json
from datetime import datetime, time, timedelta

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# When several rules on one item are active, the reason shown is the first in this order.
REASON_ORDER = ["permanent", "temporary", "limit", "schedule"]
TIME_FMT = "%Y-%m-%d %H:%M:%S"
ALLOW, BLOCK = "allow", "block"   # hours rule mode: allow only during the windows / block during them


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


def next_midnight(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), time(0, 0))


# ---------- usage buckets ----------
# Usage is stored per (owner, bucket): owner "item:<id>" or "group:<id>" (shared group limit);
# bucket "day:YYYY-MM-DD" (daily limits) or "win:<rule key>:<end of blocked stretch>" (allowance in blocked hours).

def no_usage(owner: str, bucket: str) -> int:
    return 0


def day_bucket(now: datetime) -> str:
    return f"day:{now.date().isoformat()}"


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
            else:             # inherited: a group daily limit is one shared total
                rule = {**r, "usage_owner": f"group:{g['id']}" if t == "time_limit" else me}
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
    if kind == "time_limit" and rule.get("daily_limit_min") is not None:
        used = usage(_owner(rule), day_bucket(now))
        return ("limit", next_midnight(now)) if used >= rule["daily_limit_min"] * 60 else None
    return None  # switch_limit: not implemented yet


def item_block(rules: list[dict], now: datetime, usage=no_usage) -> tuple[str, datetime | None, dict] | None:
    """(reason, until, rule) for the rule that blocks the item now (most important reason first), else None."""
    active = [(*b, r) for r in rules if (b := rule_block(r, now, usage))]
    if not active:
        return None
    return min(active, key=lambda b: REASON_ORDER.index(b[0]))


def usage_targets(rules: list[dict], item_id: int, now: datetime) -> set[tuple[str, str]]:
    """(owner, bucket) pairs to add time to while the item is being used."""
    me = f"item:{item_id}"
    targets = {(me, day_bucket(now))}
    for r in rules:
        if r["rule_type"] == "time_limit":
            targets.add((_owner(r), day_bucket(now)))
        elif r["rule_type"] == "scheduled" and r.get("allowance_min"):
            until = schedule_until(r["schedule"], now)
            if until:
                targets.add((me, allowance_bucket(r, until)))
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
    Usage-based blocks (daily limit, allowance) are only predicted while the item is in use."""
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
        elif kind == "time_limit" and in_use and r.get("daily_limit_min") is not None:
            left = r["daily_limit_min"] * 60 - usage(_owner(r), day_bucket(now))
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
    if kind == "time_limit":
        used = usage(_owner(rule), day_bucket(now))
        shared = " (shared)" if _owner(rule).startswith("group:") else ""
        return f"Limit{shared}: {duration_text(used)} / {duration_text(rule['daily_limit_min'] * 60)} today"
    return kind
