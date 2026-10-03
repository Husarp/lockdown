"""Alert settings, message formatting and the upcoming-block watcher (used by the tray agent)."""
import math
from datetime import datetime

from blocker.protection import LISTS
from rules import (DAY_NAMES, TIME_FMT, allowance_left, allowance_owner, effective_rules, item_block,
                   next_block)

REASONS = {  # reason -> (label in settings, text for {reason})
    "permanent": ("Permanently blocked", "permanently blocked"),
    "schedule": ("Outside its allowed hours", "blocked at this time"),
    "limit": ("Over its time limit", "over its time limit"),
    "switches": ("Opened too often", "opened too many times"),
    "temporary": ("Temporarily blocked", "temporarily blocked"),
    "mode": ("Blocked by a mode", "blocked while a mode is on"),
    "protection": ("On a protection list", "on a blocked list"),
}
DEFAULT_MESSAGES = {
    "permanent": "{site} is permanently blocked.",
    "schedule": "{site} is blocked until {until}.",
    "limit": "{site}: time limit reached - blocked until {until}.",
    "switches": "{site}: opened too many times - blocked until {until}.",
    "temporary": "{site} is blocked for now - until {until}.",
    "mode": "{site} is blocked while this mode is on - until {until}.",
    "protection": "{site} is blocked - it's {reason}.",
}
FORMATS = {"toast": "Windows notifications (recommended)", "inapp": "Lockdown pop-ups", "both": "Both"}   # "Show as"
COOLDOWN_OPTIONS = [1, 5, 15, 30, 60]
WARN_MINUTE_OPTIONS = [1, 2, 5, 10, 15, 20, 30, 60]
REPEAT_OPTIONS = [0, 1, 2, 5, 10]   # 0 = don't repeat
# notify.format defaults to Windows' own notifications (0.84.7): Windows draws them, so they never go over a game
# the way Lockdown's pop-up window did (it made full-screen games stutter). While something is full-screen even
# "Lockdown pop-ups" / "Both" use them (App._show).
DEFAULTS = {"notify.cooldown_min": "30", "notify.format": "toast",
            "notify.warn.enabled": "1", "notify.warn.minutes": "5", "notify.warn.repeat_min": "0",
            "notify.started.enabled": "1"}
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


def format_message(template: str, event: dict, now: datetime, lists: dict = LISTS) -> str:
    """lists: the protection lists by key (built-in + your own), to name the list a site is on."""
    reason = REASONS.get(event["reason"], ("", event["reason"]))[1]
    if event["reason"].startswith("protection:"):   # which list: "on the scam list"
        key = event["reason"].split(":", 1)[1]
        reason = f"on the {lists[key][0].lower()} list" if key in lists else REASONS["protection"][1]
    values = {"site": event["display_name"], "reason": reason,
              "until": until_text(event["until"], now)}
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):  # user typed an unknown {placeholder}
        return template


def base_reason(reason: str) -> str:
    """"protection:scam" -> "protection" (the settings are per kind of reason)."""
    return reason.split(":", 1)[0]


def should_notify(event: dict, item_notify: str | None, enabled: bool, last_shown: float | None,
                  now_ts: float, cooldown_min: int) -> bool:
    """item_notify: per-item override ('on' / 'off' / None = follow the per-reason setting)."""
    if item_notify == "off" or (item_notify != "on" and not enabled):
        return False
    return last_shown is None or now_ts - last_shown >= cooldown_min * 60


def opened_in_front(event: dict, shown: set[str], targets=()) -> bool:
    """Was a blocked visit you opening the site? Only if a browser window in front showed it (`shown`:
    usage.shown_since) - the blocked item's own sites (`targets`; a hit on googlevideo.com while youtube.com is in
    front is YouTube) or, for a protection list, the name visited. A background hit - a page in front embedding a
    YouTube video, thumbnails, another program, the DNS filter answering for something - is still recorded (the
    network log, Blocked visits) but says nothing (0.84.10)."""
    names = {h.lower().removeprefix("www.") for h in (*targets, event["hostname"]) if h}
    return any(s == n or s.endswith("." + n) for s in shown for n in names)


def _when_text(when: datetime, now: datetime) -> str:
    return when.strftime("%H:%M") if when.date() == now.date() else f"{DAY_NAMES[when.weekday()]} {when:%H:%M}"


def _names(names: list[str]) -> str:
    return ", ".join(sorted(set(names)))


class BlockWatcher:
    """Warnings before blocks start, repeated reminders while you use the item, and "block started" notices.
    Blocks coming from the same group are announced together."""

    def __init__(self):
        self.warned: dict[tuple, datetime] = {}   # warning key -> when last shown
        self.prev_blocked: set[int] | None = None
        # the messages from the last check that are about something you are using right now. Those get through
        # a mode that mutes, or Do not disturb: being told the thing in front of you
        # is about to go is the one notice that is worth interrupting a game for.
        self.urgent: set[str] = set()

    @staticmethod
    def _key(item: dict, rule: dict, when: datetime) -> tuple:
        source = ("group", rule["group"]["id"]) if rule.get("group") else ("item", item["id"])
        if rule["rule_type"] == "time_limit":      # usage-based: the predicted time drifts, key by day
            return source, "limit", when.date()
        if rule["rule_type"] in ("unlock", "pause"):   # items unlocked together are announced together
            return rule["rule_type"], when
        if rule.get("allowance_min") and rule["rule_type"] == "scheduled":
            return source, "allowance", when.strftime("%Y%m%d")
        return source, "schedule", when.strftime("%Y%m%d%H%M")

    def check(self, items: list[dict], groups: list[dict], usage, now: datetime, in_use: set[int],
              settings: dict) -> list[str]:
        messages = []
        self.urgent = set()
        warn_on = settings["notify.warn.enabled"] == "1"
        warn_sec = int(settings["notify.warn.minutes"]) * 60
        repeat_sec = int(settings["notify.warn.repeat_min"]) * 60
        upcoming: dict[tuple, dict] = {}
        blocked: dict[int, tuple] = {}
        for item in items:
            rules = effective_rules(item, groups)
            block = item_block(rules, now, usage)
            if block:
                blocked[item["id"]] = (item, block)
                continue
            nb = next_block(rules, now, usage, in_use=item["id"] in in_use)
            if not nb or (nb[0] - now).total_seconds() > warn_sec:
                continue
            when, rule = nb
            entry = upcoming.setdefault(self._key(item, rule, when),
                                        {"when": when, "rule": rule, "names": [], "in_use": False})
            entry["names"].append(item["display_name"])
            entry["in_use"] |= item["id"] in in_use

        if warn_on:
            spending = self._allowance_notices(items, groups, usage, now, in_use)
            self.urgent.update(spending)          # you are using it right now, by definition
            messages += spending
            for key, e in upcoming.items():
                last = self.warned.get(key)
                if last is None or (e["in_use"] and repeat_sec and (now - last).total_seconds() >= repeat_sec):
                    self.warned[key] = now
                    text = self._warning(e, now)
                    if e["in_use"]:               # what you are using is the thing about to be blocked
                        self.urgent.add(text)
                    messages.append(text)

        if self.prev_blocked is not None and settings["notify.started.enabled"] == "1":
            started: dict[tuple, dict] = {}
            for item_id, (item, (reason, until, rule)) in blocked.items():
                if item_id in self.prev_blocked:
                    continue
                # one line per group, and one for everything blocked by its own rules
                source = ("group", rule["group"]["id"]) if rule.get("group") else ("item",)
                entry = started.setdefault(source, {"rule": rule, "reason": reason, "until": until, "names": []})
                entry["names"].append(item["display_name"])
                entry["in_use"] = entry.get("in_use", False) or item_id in in_use
                if entry["until"] != until or entry["reason"] != reason:
                    entry["until"], entry["reason"] = None, ""   # they don't share an end: keep the line short
            for e in started.values():
                text = self._started(e, now)
                if e.get("in_use"):               # it just went while you were in it
                    self.urgent.add(text)
                messages.append(text)
        self.prev_blocked = set(blocked)
        return messages

    def _allowance_notices(self, items: list[dict], groups: list[dict], usage, now: datetime,
                           in_use: set[int]) -> list[str]:
        """You opened something inside its blocked hours and it has "N minutes allowed" left: say so once per
        stretch, so you know you are spending the allowance rather than wondering why it isn't blocked."""
        found = {}
        for item in items:
            if item["id"] not in in_use:
                continue
            for rule in effective_rules(item, groups):
                spent = allowance_left(rule, now, usage)
                if not spent:
                    continue
                used, allowed, until = spent
                left = allowed - used
                if left <= 0:
                    continue
                pot = allowance_owner(rule)   # a shared group allowance is one notice, not one per member
                key = (pot, "allowance", until)
                if key in self.warned:
                    continue
                # two allowances on one pot (a group's per-member one and the member's own extra, ending at the
                # same minute): the one with least left is what really limits it
                if key not in found or left < found[key][0]:
                    name = rule["group"]["name"] if pot.startswith("group:") else item["display_name"]
                    found[key] = (left, allowed, until, name)
        out = []
        for key, (left, allowed, until, name) in found.items():
            self.warned[key] = now
            out.append(f"{name} is blocked now - you have {max(1, math.ceil(left / 60))} min of your "
                       f"{allowed // 60} min allowance left (until {until:%H:%M}).")
        return out

    @staticmethod
    def _warning(e: dict, now: datetime) -> str:
        minutes = max(1, math.ceil((e["when"] - now).total_seconds() / 60))
        rule, names = e["rule"], _names(e["names"])
        if rule["rule_type"] == "time_limit":
            scope = f"{rule['group']['name']} ({names})" if rule.get("group") else names
            return f"{scope}: {minutes} min of the time limit left."
        if rule["rule_type"] == "unlock":
            return f"Emergency unlock ends in {minutes} min: {names} will be blocked again."
        if rule["rule_type"] == "pause":
            count = len(set(e["names"]))
            what = names if count <= 3 else f"{count} things"
            return (f"Your blocks are back on in {minutes} min ({_when_text(e['when'], now)}): {what} will be "
                    "blocked again.")
        if rule.get("allowance_min"):
            return f"{names}: {minutes} min of your allowance left - then it's blocked."
        if rule.get("group"):
            return f"{rule['group']['name']} starts in {minutes} min ({_when_text(e['when'], now)}): {names} will be blocked."
        return f"{names} will be blocked in {minutes} min ({_when_text(e['when'], now)})."

    @staticmethod
    def _started(e: dict, now: datetime) -> str:
        """Summarized on purpose: a block starting is not something you did, so it is one line for the group
        (and one for everything else), not a popup per site and app. What you actually try to open still gets
        its own alert, from the service."""
        until = f" until {_when_text(e['until'], now)}" if e["until"] else ""
        why = {"limit": " - time limit reached", "switches": " - opened too many times",
               "temporary": " (temporary block)"}.get(e["reason"], "")
        names = _names(e["names"])
        count = len(set(e["names"]))
        if e["rule"].get("group"):
            return (f"{e['rule']['group']['name']} started - "
                    f"{names if count == 1 else f'{count} things'} blocked{until}{why}.")
        if count == 1:
            return f"{names} is now blocked{until}{why}."
        return f"{count} things are now blocked{until}{why}."
