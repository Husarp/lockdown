"""Blocked-visit alert settings and message formatting (used by the tray agent)."""
from datetime import datetime

from rules import DAY_NAMES, TIME_FMT

REASONS = {  # reason -> (label in settings, text for {reason})
    "permanent": ("Permanently blocked", "permanently blocked"),
    "schedule": ("Outside its allowed hours", "blocked at this time"),
    "limit": ("Over its daily limit", "over its daily limit"),
    "temporary": ("Temporarily blocked", "temporarily blocked"),
}
DEFAULT_MESSAGES = {
    "permanent": "{site} is permanently blocked.",
    "schedule": "{site} is blocked until {until}.",
    "limit": "{site}: daily limit reached - blocked until {until}.",
    "temporary": "{site} is blocked for now - until {until}.",
}
FORMATS = {"toast": "Windows notification", "inapp": "Lockdown popup", "both": "Both"}
COOLDOWN_OPTIONS = [1, 5, 15, 30, 60]
DEFAULTS = {"notify.cooldown_min": "5", "notify.format": "toast"}
for _r in REASONS:
    DEFAULTS[f"notify.enabled.{_r}"] = "1"
    DEFAULTS[f"notify.msg.{_r}"] = DEFAULT_MESSAGES[_r]


def get(db, key: str) -> str:
    return db.get_setting(key, DEFAULTS[key])


def until_text(until: str | None, now: datetime) -> str:
    if not until:
        return "further notice"
    u = datetime.strptime(until, TIME_FMT)
    if u.date() == now.date():
        return u.strftime("%H:%M")
    return f"{DAY_NAMES[u.weekday()]} {u:%H:%M}"   # not strftime("%A"): that follows the system language


def format_message(template: str, event: dict, now: datetime) -> str:
    values = {"site": event["display_name"], "reason": REASONS.get(event["reason"], ("", event["reason"]))[1],
              "until": until_text(event["until"], now)}
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):  # user typed an unknown {placeholder}
        return template


def should_notify(event: dict, item_notify: str | None, enabled: bool, last_shown: float | None,
                  now_ts: float, cooldown_min: int) -> bool:
    """item_notify: per-item override ('on' / 'off' / None = follow the per-reason setting)."""
    if item_notify == "off" or (item_notify != "on" and not enabled):
        return False
    return last_shown is None or now_ts - last_shown >= cooldown_min * 60
