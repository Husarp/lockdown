"""Block rule evaluation: is a rule blocking right now, why, and until when."""
import json
from datetime import datetime, time, timedelta

DAY_NAMES = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
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


def rule_block(rule: dict, now: datetime, used_sec: int = 0) -> tuple[str, datetime | None] | None:
    """(reason, until) if this rule blocks at `now`, else None. until=None means indefinitely.
    used_sec: today's usage of the item (for time_limit rules)."""
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
        return "schedule", (None if until == datetime.max else until)
    if kind == "time_limit" and rule.get("daily_limit_min") is not None:
        return ("limit", next_midnight(now)) if used_sec >= rule["daily_limit_min"] * 60 else None
    return None  # switch_limit: not implemented yet


def item_block(rules: list[dict], now: datetime, used_sec: int = 0) -> tuple[str, datetime | None] | None:
    active = [b for b in (rule_block(r, now, used_sec) for r in rules) if b]
    if not active:
        return None
    return min(active, key=lambda b: REASON_ORDER.index(b[0]))


def days_text(days: list[int]) -> str:
    if days == list(range(7)):
        return "Every day"
    if days == list(range(5)):
        return "Mo-Fr"
    if days == [5, 6]:
        return "Sa-Su"
    return ",".join(DAY_NAMES[d] for d in days)


def duration_text(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def describe_rule(rule: dict, now: datetime, used_sec: int = 0) -> str:
    kind = rule["rule_type"]
    if kind == "permanent":
        return "Permanent"
    if kind == "scheduled":
        s = load_schedule(rule["schedule"])
        label = "Allowed only" if s["mode"] == ALLOW else "Blocked"
        return f"{label}:\n" + "\n".join(f"{days_text(w['days'])} {w['start']}-{w['end']}" for w in s["windows"])
    if kind == "temporary":
        if rule.get("temp_until"):
            left = datetime.strptime(rule["temp_until"], TIME_FMT) - now
            return f"Temporary: {duration_text(left.total_seconds())} left"
        return f"Temporary: {duration_text(rule['duration_min'] * 60)} (starts when saved)"
    if kind == "time_limit":
        return f"Limit: {duration_text(used_sec)} / {duration_text(rule['daily_limit_min'] * 60)} today"
    return kind
