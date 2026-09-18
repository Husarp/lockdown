"""Block rule evaluation: is a rule blocking right now, why, and until when."""
import json
from datetime import datetime, time, timedelta

DAY_NAMES = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
# When several rules on one item are active, the reason shown is the first in this order.
REASON_ORDER = ["permanent", "temporary", "schedule"]
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def parse_hhmm(text: str) -> time:
    """'9:00' / '21:30' -> time. Raises ValueError."""
    h, m = text.strip().split(":")
    return time(int(h), int(m))


def make_schedule(days: list[int], start: str, end: str) -> str:
    """Validated schedule JSON. days: 0=Monday..6=Sunday. end <= start means it runs past midnight."""
    if not days:
        raise ValueError("Pick at least one day.")
    parse_hhmm(start), parse_hhmm(end)
    return json.dumps({"days": sorted(days), "start": start.strip(), "end": end.strip()})


def schedule_until(schedule_json: str, now: datetime) -> datetime | None:
    """If the schedule is blocking at `now`, return when that block window ends, else None.
    A window belongs to the day it starts on (Mon 21:00-07:00 blocks Mon night into Tue morning)."""
    s = json.loads(schedule_json)
    start, end, days = parse_hhmm(s["start"]), parse_hhmm(s["end"]), s["days"]
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


def rule_block(rule: dict, now: datetime) -> tuple[str, datetime | None] | None:
    """(reason, until) if this rule blocks at `now`, else None. until=None means indefinitely."""
    kind = rule["rule_type"]
    if kind == "permanent":
        return "permanent", None
    if kind == "temporary" and rule["temp_until"]:
        until = datetime.strptime(rule["temp_until"], TIME_FMT)
        return ("temporary", until) if now < until else None
    if kind == "scheduled" and rule["schedule"]:
        until = schedule_until(rule["schedule"], now)
        return ("schedule", until) if until else None
    return None  # time_limit / switch_limit: not implemented yet


def item_block(rules: list[dict], now: datetime) -> tuple[str, datetime | None] | None:
    active = [b for b in (rule_block(r, now) for r in rules) if b]
    if not active:
        return None
    return min(active, key=lambda b: REASON_ORDER.index(b[0]))


def _days_text(days: list[int]) -> str:
    if days == list(range(7)):
        return "Every day"
    if days == list(range(5)):
        return "Mo-Fr"
    if days == [5, 6]:
        return "Sa-Su"
    return ",".join(DAY_NAMES[d] for d in days)


def _left_text(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m left" if h else f"{m}m left"


def describe_rule(rule: dict, now: datetime) -> str:
    kind = rule["rule_type"]
    if kind == "permanent":
        return "Permanent"
    if kind == "scheduled":
        s = json.loads(rule["schedule"])
        return f"Hours: {_days_text(s['days'])} {s['start']}-{s['end']}"
    if kind == "temporary":
        return "Temporary: " + _left_text(datetime.strptime(rule["temp_until"], TIME_FMT) - now)
    return kind
