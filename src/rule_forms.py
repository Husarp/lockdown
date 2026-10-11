"""Rule editor forms <-> rule dicts, with no screen in sight (R19).

The new window's blocker editors (js/rules/editors.js) hold what the owner typed as plain text: a *form*. This module is
the AUTHORITY on what a form means. It parses and checks it - same ranges and the same messages as the old Tk editors
in gui/rule_editors.py, which stay as they are - and builds the rule dict the Draft stores. js/rules/summary.js is a
display-only copy, so the page can show an error and a one-line summary while the owner types; the cases in
tests/fixtures/rule_cases.json keep the two in step. Nothing here decides whether a change LOOSENS a block: that is
antibypass's job (rule_looser, via the Draft) and is never repeated in the page.

A form is a dict with "rule_type" and, per type (all amounts are text, exactly as typed):
  scheduled     mode "allow"|"block"; windows [{days [0..6], start "09:00", end "17:00"}]; allowance "0";
                pot true|false (groups only: one pot shared by every member)
  time_limit    day / week / month "30m" (any may be empty); same true|false ("Same every day");
                weekdays [7 texts Mon..Sun] (used when same is false)
  switch_limit  day / week / month "10"; counts "visit"|"switch"; gap "5" (minutes away, for "visit")
  temporary     choice "15"|"30"|"60"|"120"|"180"|"240"|"480"|"1440" (minutes) | "custom" | "keep";
                amount "1" + unit "days"|"hours"|"minutes" (custom); keep_until "YYYY-MM-DD HH:MM:SS" (keep)
  permanent     (nothing)

rule_from_form(form) -> the rule dict, or raises RuleFormError (a ValueError with .field, the field to mark).
check_forms(forms, shared, now) -> (rules, errors): every form at once, for the page to show all the mistakes.

An Api method that saves blockers (Add / Edit, Groups, a member's extra limits) takes the FORMS from the page, never rule
dicts, and does:
    rules, errors = rule_forms.check_forms(forms, shared=False, now=ctx.now(), require_one=False)
    if errors:
        return {"saved": False, "errors": errors}        # the page: rail.showErrors(reply.errors)
    draft.set_rules(item_id, rules)                      # the Draft judges loosening (antibypass), then stores it
Temporary rules come out holding a duration (duration_min); the Draft turns that into the end time when it SAVES, with
the trusted clock (gui/draft.py _finalize) - the page's clock never decides when a block ends. "keep" leaves the end
of a running block alone (temp_until).
"""
import re
from datetime import datetime

from rules import (ALLOW, BLOCK, DAY_LIMITS_FIELD, DAY_NAMES, DEFAULT_VISIT_GAP_MIN, OPEN_LIMIT_FIELDS, PERIODS,
                   SWITCH, TIME_FMT, TIME_LIMIT_FIELDS, VISIT, allowance_shared, day_limits, day_limits_text,
                   days_text, duration_text, load_schedule, make_day_limits, make_schedule, parse_hhmm)

RULE_TYPES = ("scheduled", "time_limit", "switch_limit", "permanent", "temporary")   # the order they are shown / saved in
RULE_NAMES = {"scheduled": "By time", "time_limit": "Time limit", "switch_limit": "Opening limit",
              "permanent": "Permanent", "temporary": "Temporary"}
RULE_SUBTITLES = {"scheduled": "allowed only inside these windows - or blocked inside them",
                  "time_limit": "a cap on how long per day / week", "switch_limit": "a cap on how often it's opened",
                  "permanent": "blocked all the time, until removed", "temporary": "blocked for a while from now"}
# What a member of a group can be given on top of the group's rules (gui/groups.py CUSTOMIZABLE)
CUSTOMIZABLE = ("scheduled", "time_limit", "switch_limit", "temporary")

DURATIONS = {"15 min": 15, "30 min": 30, "1 hour": 60, "2 hours": 120, "3 hours": 180,
             "4 hours": 240, "8 hours": 480, "24 hours": 1440}          # the temporary block's presets
UNITS = {"days": 1440, "hours": 60, "minutes": 1}                       # biggest first (shows a custom duration)
MAX_TEMPORARY_MIN = 30 * 1440
PERIOD_LABELS = {"day": "per day", "week": "per week", "month": "per month"}
PERIOD_MAX_MIN = {"day": 1440, "week": 7 * 1440, "month": 31 * 1440}
MAX_WINDOWS = 50
MAX_TEXT = 200                           # the longest thing a box may hold
BAD_SETTINGS = "check the settings"      # the one-liner of a blocker whose form can't be read


class RuleFormError(ValueError):
    """A form the owner has to fix: str(e) is the sentence to show, e.field the field it belongs to (see the
    field names in each _read_* function; "any" = none of the amounts is filled in)."""

    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.field = field


# ---------------------------------------------------------------- amounts (ported from gui/rule_editors.py)

def parse_duration(text: str) -> int | None:
    """'45' / '45m' / '2h' / '2h30' / '2h 30m' / '1:30' -> minutes; '' -> None. Raises ValueError."""
    text = text.strip().lower().replace(" ", "")
    if not text:
        return None
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m?)?", text) or re.fullmatch(r"(\d+):(\d{2})", text)
    if not m or not any(m.groups()):
        raise ValueError(f"Can't read {text!r} - write e.g. 45m, 2h or 1h30.")
    hours, minutes = (int(g or 0) for g in m.groups())
    return hours * 60 + minutes


def format_duration(minutes: int | None) -> str:
    if not minutes:
        return ""
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}" if h and m else f"{h}h" if h else f"{m}m"


def _whole(text: str) -> int:
    """The whole number in `text` (-1 when it is not one)."""
    try:
        return int(text) if re.fullmatch(r"\+?[0-9]+", text) else -1
    except ValueError:                  # (thousands of digits)
        return -1


def _int(text: str) -> int:
    """The number in a minutes box, read as the old editors did (empty = 0, anything else odd = -1)."""
    return _whole(text.strip()) if text.strip() else 0


def _minutes(text: str, low: int, high: int, what: str, field: str) -> int:
    value = _int(text)
    if not low <= value <= high:
        raise RuleFormError(f"{what} must be {low}-{high} minutes.", field)
    return value


def _read_duration(text: str, field: str) -> int | None:
    try:
        return parse_duration(text)
    except ValueError as e:
        raise RuleFormError(str(e), field) from None


def _read_limit(text: str, period: str, field: str, day: str = "") -> int | None:
    """One time limit box: None (empty) or minutes within 1m .. the period's largest."""
    minutes = _read_duration(text, field)
    if minutes is not None and not 1 <= minutes <= PERIOD_MAX_MIN[period]:
        what = f"on {day}" if day else PERIOD_LABELS[period]
        raise RuleFormError(f"The limit {what} must be between 1m and {PERIOD_MAX_MIN[period] // 60}h.", field)
    return minutes


# ---------------------------------------------------------------- reading a form (the page is untrusted)

def _as_text(value, field: str) -> str:
    """What a box holds, as text: None = empty; a number is fine (a stepper sends one); anything else is not a box value."""
    if value is None:
        return ""
    try:
        text = str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else None
    except ValueError:                  # (an int of thousands of digits)
        text = None
    if text is None or len(text) > MAX_TEXT:
        raise RuleFormError("A box holds something that isn't a short text.", field)
    return text


def _text(form: dict, key: str) -> str:
    return _as_text(form.get(key), key)


def _list(form: dict, key: str, limit: int) -> list:
    value = form.get(key)
    if not isinstance(value, list) or len(value) > limit:
        raise RuleFormError("The settings arrived in a form this window can't read.", key)
    return value


def _read_scheduled(form: dict, shared: bool) -> dict:
    allowance = _minutes(_text(form, "allowance"), 0, 1440, "Allowance", "allowance")
    mode = form.get("mode")
    if mode not in (ALLOW, BLOCK):
        raise RuleFormError("Choose Allow only or Block.", "mode")
    windows = []
    for i, w in enumerate(_list(form, "windows", MAX_WINDOWS)):
        days = w.get("days") if isinstance(w, dict) else None
        if not isinstance(days, list) or len(days) > 7 or any(isinstance(d, bool) or not isinstance(d, int)
                                                               or not 0 <= d <= 6 for d in days):
            raise RuleFormError("Pick days from Monday to Sunday.", f"windows.{i}")
        if not days:
            continue                        # a window with no days is ignored
        start, end = _text(w, "start"), _text(w, "end")
        try:
            parse_hhmm(start), parse_hhmm(end)
        except ValueError:
            raise RuleFormError("Hours must look like 09:00 or 21:30.", f"windows.{i}") from None
        windows.append((sorted(set(days)), start, end))
    if not windows:
        raise RuleFormError("Add a time window (with at least one day on).", "windows")
    pot = int(bool(form.get("pot", True))) if shared else None          # (a group: ticked unless said otherwise)
    return {"rule_type": "scheduled", "schedule": make_schedule(mode, windows),
            "allowance_min": allowance or None, "allowance_shared": pot}


def _read_time_limit(form: dict, shared: bool) -> dict:
    same = bool(form.get("same", True))
    rule = {"rule_type": "time_limit", DAY_LIMITS_FIELD: None}
    for p in PERIODS:
        if p == "day" and not same:
            continue
        rule[TIME_LIMIT_FIELDS[p]] = _read_limit(_text(form, p), p, p)
    if not same:
        texts = _list(form, "weekdays", 7)
        if len(texts) != 7:
            raise RuleFormError("The settings arrived in a form this window can't read.", "weekdays")
        amounts = [_read_limit(_as_text(t, f"weekdays.{i}"), "day", f"weekdays.{i}", DAY_NAMES[i])
                   for i, t in enumerate(texts)]
        rule[TIME_LIMIT_FIELDS["day"]], rule[DAY_LIMITS_FIELD] = make_day_limits(amounts)
    if not any(rule[f] for f in (*TIME_LIMIT_FIELDS.values(), DAY_LIMITS_FIELD)):
        raise RuleFormError("Fill in at least one time limit (per day, week or month).", "any")
    return rule


def _read_switch_limit(form: dict, shared: bool) -> dict:
    rule = {"rule_type": "switch_limit"}
    for p in PERIODS:
        text = _text(form, p).strip()
        if not text:
            times = None
        else:
            times = _whole(text)
            if not 0 <= times <= 10000:
                raise RuleFormError(f"Openings {PERIOD_LABELS[p]} must be a number 0-10000.", p)
        rule[OPEN_LIMIT_FIELDS[p]] = times
    if all(rule[f] is None for f in OPEN_LIMIT_FIELDS.values()):
        raise RuleFormError("Fill in at least one opening limit (per day, week or month).", "any")
    counts = form.get("counts")
    if counts not in (VISIT, SWITCH):
        raise RuleFormError("Choose what counts as opening it.", "counts")
    rule["switch_mode"] = counts
    if counts == VISIT:
        rule["visit_gap_min"] = _minutes(_text(form, "gap"), 1, 1440, "Minutes away", "gap")
    return rule


def _read_temporary(form: dict, shared: bool, now: datetime | None) -> dict:
    choice = _text(form, "choice") or "60"
    if choice == "keep":
        text = _text(form, "keep_until")
        try:
            ends = datetime.strptime(text, TIME_FMT)
        except ValueError:
            raise RuleFormError("There is no running block to keep.", "choice") from None
        if now is not None and ends <= now:
            raise RuleFormError("That block has already ended - pick a new length.", "choice")
        return {"rule_type": "temporary", "temp_until": ends.strftime(TIME_FMT)}
    if choice != "custom":
        if choice not in {str(m) for m in DURATIONS.values()}:
            raise RuleFormError("Pick how long to block.", "choice")
        return {"rule_type": "temporary", "duration_min": int(choice)}
    unit = _text(form, "unit")
    amount = _text(form, "amount").strip()
    minutes = _whole(amount) * UNITS.get(unit, 0)       # (a bad amount is -1: out of range below)
    if not 1 <= minutes <= MAX_TEMPORARY_MIN:
        raise RuleFormError("Custom duration must be between 1 minute and 30 days.", "amount")
    return {"rule_type": "temporary", "duration_min": minutes}


def rule_from_form(form: dict, shared: bool = False, now: datetime | None = None) -> dict:
    """The rule dict for one editor's form (see the top of this file). `shared`: the editor of a group (it has the
    "one pot" box). `now`: the trusted time, only to refuse "keep" on a block that has already ended.
    Raises RuleFormError."""
    if not isinstance(form, dict):
        raise RuleFormError("The settings arrived in a form this window can't read.", "rule_type")
    t = form.get("rule_type")
    if t == "scheduled":
        return _read_scheduled(form, shared)
    if t == "time_limit":
        return _read_time_limit(form, shared)
    if t == "switch_limit":
        return _read_switch_limit(form, shared)
    if t == "temporary":
        return _read_temporary(form, shared, now)
    if t == "permanent":
        return {"rule_type": "permanent"}
    raise RuleFormError("Unknown kind of blocker.", "rule_type")


def check_forms(forms: list, shared: bool = False, now: datetime | None = None, require_one: bool = False):
    """Every ticked editor's form -> (rules, errors). rules: the rule dicts of the forms that are fine, in the order
    the blockers are shown. errors: [{"rule_type", "field", "message"}], one per form that isn't (a kind of blocker
    given twice is an error too - a rule type is set once per item). require_one: no forms at all is an error."""
    rules, errors, seen = [], [], set()
    if not isinstance(forms, (list, tuple)):
        return rules, [{"rule_type": None, "field": "rules",
                        "message": "The settings arrived in a form this window can't read."}]
    for form in forms:
        t = form.get("rule_type") if isinstance(form, dict) else None
        t = t if isinstance(t, str) else None
        try:
            if t in seen:
                raise RuleFormError("Each blocker can only be set once.", "rule_type")
            rule = rule_from_form(form, shared, now)
            seen.add(t)
            rules.append(rule)
        except RuleFormError as e:
            errors.append({"rule_type": t, "field": e.field, "message": str(e)})
    if require_one and not forms:
        errors.append({"rule_type": None, "field": "rules", "message": "Tick at least one blocker."})
    rules.sort(key=lambda r: RULE_TYPES.index(r["rule_type"]))
    return rules, errors


def rules_from_forms(forms: list, shared: bool = False, now: datetime | None = None, require_one: bool = False) -> list:
    """check_forms for an Api method that saves: the rule dicts, or raises the first RuleFormError."""
    rules, errors = check_forms(forms, shared, now, require_one)
    if errors:
        raise RuleFormError(errors[0]["message"], errors[0]["field"])
    return rules


# ---------------------------------------------------------------- loading a rule into a form

def default_form(rule_type: str, shared: bool = False) -> dict:
    """What an editor shows when its blocker has just been ticked."""
    return form_from_rule(None, shared, rule_type)


def form_from_rule(rule: dict | None, shared: bool = False, rule_type: str | None = None) -> dict:
    """The form an editor shows for a saved (or drafted) rule; with no rule its starting values: all days 09:00-17:00
    allowed / 30m a day / 10 opens a day / 1 hour. (rule_type is only needed when there is no rule.)"""
    t = rule_type or (rule or {}).get("rule_type")
    if t == "scheduled":
        s = load_schedule(rule["schedule"]) if rule and rule.get("schedule") else None
        return {"rule_type": t, "mode": s["mode"] if s else ALLOW,
                "windows": [{"days": list(w["days"]), "start": w["start"], "end": w["end"]} for w in s["windows"]]
                if s else [{"days": list(range(7)), "start": "09:00", "end": "17:00"}],
                "allowance": str((rule or {}).get("allowance_min") or 0),
                **({"pot": bool(allowance_shared(rule or {}))} if shared else {})}
    if t == "time_limit":
        rule = rule or {"daily_limit_min": 30}
        days = day_limits(rule)
        return {"rule_type": t, "same": not days,
                **{p: format_duration(rule.get(TIME_LIMIT_FIELDS[p])) for p in PERIODS},
                "weekdays": [format_duration(m) for m in days] if days else [""] * 7}
    if t == "switch_limit":
        rule = rule or {"daily_switch_limit": 10}
        return {"rule_type": t, "counts": rule.get("switch_mode") or VISIT,
                **{p: "" if rule.get(OPEN_LIMIT_FIELDS[p]) is None else str(rule[OPEN_LIMIT_FIELDS[p]])
                   for p in PERIODS},
                "gap": str(rule.get("visit_gap_min") or DEFAULT_VISIT_GAP_MIN)}
    if t == "temporary":
        rule = rule or {}
        minutes, keep = rule.get("duration_min"), rule.get("temp_until")
        form = {"rule_type": t, "choice": "60", "amount": "1", "unit": "hours", "keep_until": keep or None}
        if keep:
            form["choice"] = "keep"
        elif minutes in DURATIONS.values():
            form["choice"] = str(minutes)
        elif minutes:           # a custom length: shown in the biggest whole unit
            unit = next(u for u, m in UNITS.items() if minutes % m == 0)
            form.update(choice="custom", amount=str(minutes // UNITS[unit]), unit=unit)
        return form
    if t == "permanent":
        return {"rule_type": t}
    raise ValueError(f"Unknown kind of blocker: {t!r}")


# ---------------------------------------------------------------- the words (what the rail and the sentence say)

def summary(rule: dict) -> str:
    """One line describing a rule, shown on a blocker's rail row (gui/rule_editors.summary for a rule dict)."""
    t = rule["rule_type"]
    if t == "scheduled":
        sched = load_schedule(rule["schedule"])
        first = sched["windows"][0]
        text = (f"{'Allowed only' if sched['mode'] == ALLOW else 'Blocked'} {first['start']}-{first['end']} "
                f"{days_text(first['days'])}")
        if len(sched["windows"]) > 1:
            text += f" +{len(sched['windows']) - 1} more"
        if rule.get("allowance_min"):
            text += f" · +{rule['allowance_min']} min" + ("" if allowance_shared(rule) else " each")
        return text
    if t == "time_limit":
        parts = [f"{format_duration(rule[f])} {PERIOD_LABELS[p]}" for p, f in TIME_LIMIT_FIELDS.items()
                 if rule.get(f)]
        if days := day_limits(rule):
            parts.insert(0, f"per day {day_limits_text(days, format_duration)}")
        return " · ".join(parts)
    if t == "switch_limit":
        return " · ".join(f"{rule[f]} opens {PERIOD_LABELS[p]}" for p, f in OPEN_LIMIT_FIELDS.items()
                          if rule.get(f) is not None)
    if t == "temporary":
        if rule.get("temp_until"):
            return "keeps its current end"
        return f"for {duration_text(rule['duration_min'] * 60)}"
    return "always blocked"


def summarize_form(form: dict, shared: bool = False, now: datetime | None = None) -> str:
    """summary() of a form, or "check the settings" when it can't be read."""
    try:
        return summary(rule_from_form(form, shared, now))
    except RuleFormError:
        return BAD_SETTINGS


def sentence(name: str, rules: list[dict]) -> str:
    """"YouTube will be blocked: by time (…); time limit (…)." - empty with no rules."""
    if not rules:
        return ""
    parts = "; ".join(f"{RULE_NAMES[r['rule_type']].lower()} ({summary(r)})" for r in rules)
    return f"{(name or '').strip() or 'It'} will be blocked: {parts}."
