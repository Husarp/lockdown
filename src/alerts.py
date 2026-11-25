"""Alert settings, message formatting and the upcoming-block watcher (used by the tray agent)."""
import itertools
import math
import re
from datetime import datetime

from blocker.apps import is_hosted
from blocker.protection import LISTS
from notification_batch import AdvanceWarning
from rules import (DAY_NAMES, DEFAULT_CLOCK, TIME_FMT, allowance_left, allowance_owner,
                   effective_rules, item_block, next_block, time_bucket, time_limits)

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
# notify.format defaults to Windows' own notifications (0.84.7): Windows draws them, so they never go over a game
# the way Lockdown's pop-up window did (it made full-screen games stutter). While something is full-screen even
# "Lockdown pop-ups" / "Both" use them (App._show).
FULLSCREEN_WARNINGS = "notify.warn.fullscreen"
DEFAULTS = {FULLSCREEN_WARNINGS: "1","notify.cooldown_min": "30", "notify.format": "toast",
            "notify.warn.enabled": "1", "notify.warn.minutes": "5",
            "notify.warn.repeat_sec": "0",   # "remind me again every", in seconds (0 = don't); see get
            "notify.started.enabled": "1",
            # Extra warning steps while in use, plus the existing warning sound preference.
            "notify.front.enabled": "1", "notify.front.steps": "1", "notify.front.sound": "1"}
for _r in REASONS:
    DEFAULTS[f"notify.enabled.{_r}"] = "1"
    DEFAULTS[f"notify.msg.{_r}"] = DEFAULT_MESSAGES[_r]


def get(db, key: str) -> str:
    value = db.get_setting(key)
    if value is None and key == "notify.warn.repeat_sec":   # saved before seconds: notify.warn.repeat_min, minutes
        minutes = db.get_setting("notify.warn.repeat_min")
        value = str(int(minutes) * 60) if minutes and minutes.isdigit() else None
    return DEFAULTS[key] if value is None else value


FRONT_STEP_MIN, FRONT_STEP_MAX, FRONT_STEPS_MAX = 1, 30, 3
FRONT_STEPS_HELP = (f"Write up to {FRONT_STEPS_MAX} times from {FRONT_STEP_MIN} to {FRONT_STEP_MAX} minutes, "
                    "like 1 or 5, 1.")


def parse_front_steps(text) -> list[int]:
    """The "minutes before" of the warning for the app in front, as typed ("1", "5, 1", "5 min 1 min"): each step
    from 1 to 30 whole minutes, at most 3, no repeats -> [5, 1] (largest first). Raises ValueError (with words for
    the page) for anything else - the page is not trusted, so this is the one place that decides."""
    if not isinstance(text, str) or len(text) > 60:
        raise ValueError(FRONT_STEPS_HELP)
    words = re.sub(r"\s*(?:minutes?|mins?|m)\b", " ", text.lower())
    parts = [p for p in re.split(r"[\s,;]+", words) if p]
    if not parts or not all(re.fullmatch(r"[0-9]{1,3}", p) for p in parts):
        raise ValueError(FRONT_STEPS_HELP)
    steps = sorted({int(p) for p in parts}, reverse=True)
    if len(steps) > FRONT_STEPS_MAX or steps[-1] < FRONT_STEP_MIN or steps[0] > FRONT_STEP_MAX:
        raise ValueError(FRONT_STEPS_HELP)
    return steps


def front_steps_text(steps: list[int]) -> str:
    """[5, 1] -> "5, 1": the form that is saved."""
    return ", ".join(str(s) for s in steps)


def front_steps(value: str | None) -> list[int]:
    """The saved steps; a value nobody could have saved through the page (hand-edited) counts as the default."""
    try:
        return parse_front_steps(value)
    except ValueError:
        return parse_front_steps(DEFAULTS["notify.front.steps"])


def until_text(until: str | None, now: datetime) -> str:
    if not until:
        return "further notice"
    u = datetime.strptime(until, TIME_FMT)
    if u.date() == now.date():
        return u.strftime("%H:%M")
    return f"{DAY_NAMES[u.weekday()]} {u:%H:%M}"   # not strftime("%A"): that follows the system language


def tag_reason(reason: str, category: str | None) -> str:
    """The reason a block event is stored with: "permanent" -> "permanent@distracting" when the site or app is blocked
    because it is in the category Distracting (a blocked category item, db.blocks). Everything else is unchanged."""
    return f"{reason}@{category}" if category else reason


def reason_category(reason: str) -> str | None:
    """"permanent@distracting" -> "distracting" (None when the block is not from a category)."""
    return reason.split("@", 1)[1] if "@" in reason else None


def format_message(template: str, event: dict, now: datetime, lists: dict = LISTS,
                   categories: dict | None = None) -> str:
    """lists: the protection lists by key (built-in + your own), to name the list a site is on.
    categories: category key -> name; a block that comes from a category says so ("it's in Distracting") in {reason},
    or at the end when the message has no {reason} in it."""
    category = reason_category(event["reason"])
    event = {**event, "reason": base_reason(event["reason"]) if category else event["reason"]}
    reason = REASONS.get(event["reason"], ("", event["reason"]))[1]
    if event["reason"].startswith("protection:"):   # which list: "on the scam list"
        key = event["reason"].split(":", 1)[1]
        reason = f"on the {lists[key][0].lower()} list" if key in lists else REASONS["protection"][1]
    note = ""
    if category:
        note = f"it's in {(categories or {}).get(category, category)}"
        if "{reason}" in template:
            reason = f"{reason} ({note})"
    values = {"site": event["display_name"], "reason": reason,
              "until": until_text(event["until"], now)}
    try:
        text = template.format(**values)
    except (KeyError, IndexError, ValueError):  # user typed an unknown {placeholder}
        text = template
    if note and "{reason}" not in template:
        text += f"{' ' if text.endswith(('.', '!', '?')) else '. '}{note[:1].upper()}{note[1:]}."
    return text


def base_reason(reason: str) -> str:
    """"protection:scam" -> "protection", "permanent@distracting" -> "permanent" (the settings are per kind of
    reason)."""
    return reason.split("@", 1)[0].split(":", 1)[0]


def should_notify(event: dict, item_notify: str | None, enabled: bool, last_shown: float | None,
                  now_ts: float, cooldown_min: int) -> bool:
    """item_notify: per-item override ('on' / 'off' / None = follow the per-reason setting)."""
    if item_notify == "off" or (item_notify != "on" and not enabled):
        return False
    return last_shown is None or now_ts - last_shown >= cooldown_min * 60


def is_app_event(event: dict, item: dict | None = None) -> bool:
    """Was this block event an app being closed rather than a site being opened? Its key is the app's exe - or a
    program file (game.jar, tool.py). A program file's name can look like a web address (.py is Paraguay's), so
    it is one unless a protection list or a site `item` recorded it."""
    key = event["hostname"]
    if key.endswith(".exe"):
        return True
    return is_hosted(key) and not str(event.get("reason") or "").startswith("protection") \
        and (item or {}).get("item_type") != "site"


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
        self.episodes: dict[tuple, dict] = {}
        self._occurrences = itertools.count(1)
        self._blocked_limits = set()
        self.prev_blocked: set | None = None
        self.urgent: set[str] = set()
        self.front: set[str] = set()  # warnings from an in-use step, for callers inspecting a check

    @staticmethod
    def _limit_source(rule):
        return rule["usage_owner"], rule["rule_key"]

    @classmethod
    def _episode(cls, rule, when, now, usage):
        kind = rule["rule_type"]
        if kind == "time_limit":
            clock = getattr(usage, "clock", DEFAULT_CLOCK)
            owner, key = cls._limit_source(rule)
            limits = time_limits(rule, now, usage)
            period = min(limits, key=lambda p: limits[p] * 60 - usage(owner, time_bucket(p, now, clock)))
            bucket = time_bucket(period, now, clock)
            return (kind, (owner, key), bucket), clock.period(period, now)[1]
        if kind in ("unlock", "pause"):
            return (kind, when), when
        if spent := allowance_left(rule, now, usage):
            return ("allowance", allowance_owner(rule), spent[2]), spent[2]
        return ("schedule", rule["rule_key"], when), when

    def _episode_state(self, key, expires):
        state = self.episodes.setdefault(key, {"steps": set(), "last": None, "allowance": False, "expires": expires})
        state["expires"] = expires
        return state

    def _warning_message(self, text, key, triggers, in_use, timing):
        message = AdvanceWarning(text, kind=key[0], episode=key, occurrence=next(self._occurrences),
                                 triggers=tuple(triggers), in_use=in_use, timing=timing)
        if in_use:
            self.urgent.add(message)
        return message

    def check(self, items: list[dict], groups: list[dict], usage, now: datetime, in_use: set[int],
              settings: dict, active_blocks=None) -> list[str]:
        messages = []
        self.urgent = set()
        self.front = set()
        warn_on = settings["notify.warn.enabled"] == "1"
        warn_sec = int(settings["notify.warn.minutes"]) * 60
        repeat_sec = int(settings.get("notify.warn.repeat_sec") or 0)
        steps = [m * 60 for m in front_steps(settings.get("notify.front.steps"))] \
            if settings.get("notify.front.enabled") == "1" else []
        timing = (warn_sec if warn_on else None, tuple(steps), repeat_sec)
        upcoming: dict[tuple, dict] = {}
        blocked: dict[int, tuple] = {}
        blocked_limits = set()
        allowance_rules = {}
        for item in items:
            rules = effective_rules(item, groups)
            block = item_block(rules, now, usage)
            if block:
                blocked[item["id"]] = (item, block)
                if block[0] == "limit":
                    blocked_limits.add(self._limit_source(block[2]))
                continue
            using = item["id"] in in_use
            nb = next_block(rules, now, usage, in_use=using)
            if nb:
                when, rule = nb
                left = (when - now).total_seconds()
                if 0 < left <= max(warn_sec if warn_on else 0, steps[0] if using and steps else 0):
                    key, expires = self._episode(rule, when, now, usage)
                    entry = upcoming.setdefault(key, {"when": when, "rule": rule, "names": [],
                                                     "in_use": False, "in_use_names": [], "expires": expires})
                    entry["when"] = min(entry["when"], when)
                    entry["names"].append(item["display_name"])
                    entry["in_use"] |= using
                    if using:
                        entry["in_use_names"].append(item["display_name"])
            if warn_on and using and (not nb or nb[1]["rule_type"] not in ("pause", "unlock")):
                allowance_rules[item["id"]] = rules

        ended = self._blocked_limits - blocked_limits
        self._blocked_limits = blocked_limits
        self.episodes = {key: state for key, state in self.episodes.items()
                         if state["expires"] > now and not (key[0] == "time_limit" and key[1] in ended)}
        allowance_messages = self._allowance_notices(items, groups, usage, now, in_use,
                                                     eligible_rules=allowance_rules, timing=timing)
        messages.extend(allowance_messages)
        allowance_starts = {message.episode: message for message in allowance_messages}

        for key, entry in upcoming.items():
            state = self._episode_state(key, entry["expires"])
            left = (entry["when"] - now).total_seconds()
            front_due = {s for s in steps if entry["in_use"] and left <= s}
            general_due = warn_on and left <= warn_sec
            due = front_due | ({warn_sec} if general_due else set())
            fresh = due - state["steps"]
            triggers = []
            front_step = False
            if fresh:
                nearest = min(fresh)
                front_step = nearest in front_due
                if front_step:
                    triggers.append(f"in-use:{nearest}s")
                if general_due and nearest == warn_sec:
                    triggers.append(f"lead:{nearest}s")
                state["steps"].update(due)
            elif (general_due and entry["in_use"] and repeat_sec and state["last"] is not None
                  and (now - state["last"]).total_seconds() >= repeat_sec):
                triggers.append("repeat")
            if not triggers:
                continue
            message = allowance_starts.get(key)
            if message is not None:
                message.triggers += tuple(triggers)
            else:
                named = {**entry, "names": entry["in_use_names"]} if front_step else entry
                text = self._warning(named, now, key[0])
                message = self._warning_message(text, key, triggers, entry["in_use"], timing)
                messages.append(message)
            if front_step:
                self.front.add(message)
            state["last"] = now

        if active_blocks is not None:
            blocked = {(b["item"]["id"] if b["item"]["id"] is not None else
                        (b["item"]["item_type"], b["item"]["target"])):
                       (b["item"], (b["reason"], b["until"], b["rule"])) for b in active_blocks}
        if self.prev_blocked is not None and settings["notify.started.enabled"] == "1":
            started: dict[tuple, dict] = {}
            for item_id, (item, (reason, until, rule)) in blocked.items():
                if item_id in self.prev_blocked:
                    continue
                # Only items with the same reason and end time can share a line.
                source = (("group", rule["group"]["id"]) if rule.get("group") else ("item",)) + (reason, until)
                entry = started.setdefault(source, {"rule": rule, "reason": reason, "until": until, "names": []})
                entry["names"].append(item["display_name"])
                entry["in_use"] = entry.get("in_use", False) or item_id in in_use
            for e in started.values():
                text = self._started(e, now)
                if e.get("in_use"):               # it just went while you were in it
                    self.urgent.add(text)
                messages.append(text)
        self.prev_blocked = set(blocked)
        return messages

    def _allowance_notices(self, items, groups, usage, now, in_use, *, eligible_rules=None, timing=(None, (), 0)):
        """Announce the smallest remaining allowance once per pot and blocked stretch."""
        found = {}
        for item in items:
            if item["id"] not in in_use:
                continue
            rules = effective_rules(item, groups) if eligible_rules is None else eligible_rules.get(item["id"], ())
            for rule in rules:
                if not (spent := allowance_left(rule, now, usage)):
                    continue
                used, allowed, until = spent
                left = allowed - used
                if left <= 0:
                    continue
                pot = allowance_owner(rule)
                key = ("allowance", pot, until)
                if key not in found or left < found[key][0]:
                    name = rule["group"]["name"] if pot.startswith("group:") else item["display_name"]
                    found[key] = (left, allowed, until, name)
        messages = []
        for key, (left, allowed, until, name) in found.items():
            state = self._episode_state(key, until)
            if state["allowance"]:
                continue
            text = (f"{name} is blocked now - you have {max(1, math.ceil(left / 60))} min of your "
                    f"{allowed // 60} min allowance left (until {until:%H:%M}).")
            messages.append(self._warning_message(text, key, ("start",), True, timing))
            state["allowance"] = True
            state["last"] = now
        return messages

    @staticmethod
    def _warning(e: dict, now: datetime, kind: str) -> str:
        minutes = max(1, math.ceil((e["when"] - now).total_seconds() / 60))
        rule, names = e["rule"], _names(e["names"])
        if rule["rule_type"] == "time_limit":
            scope = f"{rule['group']['name']} ({names})" if rule.get("group") else names
            return f"{scope}: {minutes} min of the time limit left."
        if rule["rule_type"] == "unlock":
            return f"Emergency unlock ends in {minutes} min: {names} will be blocked again."
        if rule["rule_type"] == "pause":
            what = names
            return (f"Your blocks are back on in {minutes} min ({_when_text(e['when'], now)}): {what} will be "
                    "blocked again.")
        if kind == "allowance":
            return f"{names}: {minutes} min of your allowance left - then it's blocked."
        if rule.get("group"):
            return f"{rule['group']['name']} starts in {minutes} min ({_when_text(e['when'], now)}): {names} will be blocked."
        return f"{names} will be blocked in {minutes} min ({_when_text(e['when'], now)})."

    @staticmethod
    def _started(e: dict, now: datetime) -> str:
        """One line per shared reason and end time, naming every affected app and site."""
        until = f" until {_when_text(e['until'], now)}" if e["until"] else ""
        why = {"limit": " - time limit reached", "switches": " - opened too many times",
               "temporary": " (temporary block)",
               "mode": f" - {e['rule'].get('mode', 'active')} mode"}.get(e["reason"], "")
        names = _names(e["names"])
        count = len(set(e["names"]))
        if e["rule"].get("group"):
            return (f"{e['rule']['group']['name']} started - "
                    f"{names} blocked{until}{why}.")
        if count == 1:
            return f"{names} is now blocked{until}{why}."
        return f"{names} are now blocked{until}{why}."
