"""What changed, in plain words - for the App log (applog.py).

Two places feed it, so a change is written once wherever it was made:
- `Database.set_setting` calls `setting_changed(db, key, old, new)` for every setting that is a choice of yours (the key
  is in `watched`): Anti-Bypass, protection lists, blocked words, modes, reminders, categories, emergency unlock,
  notifications, the look ... The old value is read from the database, so the line can say "30 min -> 45 min".
- `Draft._save` (gui/draft.py) calls `draft_lines(...)` with the saved and the new blocked sites / apps / groups and writes
  "Rule changed: YouTube - Time limit 30m per day -> 45m per day".

Only your own setup is described: names of the things you block, rules, switches. Never a site or app you visited, never a
phrase or a list of blocked words (only how many). Imports of the heavier modules are inside the functions, so this file
can be loaded by db.py.
"""
import json

ARROW = "→"

# ---------------------------------------------------------------- the settings that are written down

SIMPLE = {   # key -> (label, how a value reads)
    "stats.goal_hours": ("Daily goal", "hours"),
    "icons.download": ("Download icons for my sites", "onoff_default_on"),
    "updates.auto": ("Check for updates automatically", "onoff_default_on"),
    "updates.notify": ("Tell me when a new version is found", "onoff_default_on"),
    "ui.theme": ("Theme", "text"), "ui.accent": ("Accent colour", "text"), "ui.scale": ("Interface size", "scale"),
    "ui.reduce_motion": ("Reduce motion", "onoff"), "ui.autosave": ("Auto-save", "onoff_default_on"),
    "digest.enabled": ("Weekly summary", "onoff"), "digest.day": ("Weekly summary day", "text"),
    "digest.time": ("Weekly summary time", "text"),
    "emergency.enabled": ("Emergency unlock", "onoff_default_on"),
    "emergency.minutes": ("Emergency unlock minutes", "text"), "emergency.uses": ("Emergency unlock uses", "text"),
    "emergency.per": ("Emergency unlock period", "text"),
}
NOTIFY = {   # notify.* -> (label, kind)
    "notify.cooldown_min": ("Same alert again after (min)", "text"), "notify.format": ("Alerts shown as", "text"),
    "notify.warn.enabled": ("Warning before a block", "onoff"), "notify.warn.minutes": ("Warn (min) before", "text"),
    "notify.warn.repeat_sec": ("Remind again every (s)", "text"),
    "notify.started.enabled": ("Alert when a block starts", "onoff"),
    "notify.front.enabled": ("Warning for the app in front", "onoff"),
    "notify.front.steps": ("Warning for the app in front, minutes before", "text"),
    "notify.front.sound": ("Warning sound", "onoff"),
}
EXACT = {"antibypass", "protection", "words", "modes.list", "modes.active", "pause.blocks", "app.off", "agent.exited",
         "stats.categories", "limits.reset", "emergency.tiers", "reminders.sleep", "reminders.break",
         "reminders.custom", *SIMPLE}
IGNORED = {"notify.last_alert"}   # (a timestamp the program keeps)


def watched(key: str) -> bool:
    """Is a change of this setting written to the App log? (Called on every set_setting: a set lookup and a prefix test.)"""
    return key in EXACT or (key.startswith("notify.") and key not in IGNORED)


def setting_changed(db, key: str, old, new):
    """Write what changed in setting `key` (old text -> new text) to the App log. Never raises."""
    import applog
    try:
        for kind, text in describe_setting(key, old, new):
            applog.record(kind, text, db)
    except Exception:
        pass


# ---------------------------------------------------------------- small helpers

def _json(text, default=None):
    if not isinstance(text, str) or not text.strip():
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _dict(text) -> dict:
    value = _json(text, {})
    return value if isinstance(value, dict) else {}


def _onoff(value) -> str:
    return "on" if value else "off"


def _show(value, limit: int = 40) -> str:
    """A value in a sentence."""
    if value is None or value == "":
        return "none"
    if isinstance(value, bool):
        return _onoff(value)
    if isinstance(value, (list, tuple)):
        if all(isinstance(v, (str, int, float)) for v in value) and len(value) <= 4:
            return ", ".join(str(v) for v in value) or "none"
        return f"{len(value)} items"
    if isinstance(value, dict):
        return "changed"
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _names(names, limit: int = 3) -> str:
    names = [str(n) for n in names]
    return ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit else "")


def _split(old, new) -> tuple[list, list]:
    """(added, removed) between two lists, in order."""
    old, new = list(old or []), list(new or [])
    return [x for x in new if x not in old], [x for x in old if x not in new]


def _list_change(label: str, old, new) -> str:
    added, removed = _split(old, new)
    parts = ([f"added {_names(added)}"] if added else []) + ([f"removed {_names(removed)}"] if removed else [])
    return f"{label}: {'; '.join(parts)}" if parts else ""


def _fields(old: dict, new: dict, labels: dict, flags=(), hidden=(), counted=()) -> list[str]:
    """The fields of two dicts that differ, as short phrases: "bedtime 23:00 -> 22:30", "wake-up turned on" ...
    `flags` read on / off, `hidden` only say "changed" (their text is private or long), `counted` only say how many."""
    out = []
    for key, label in labels.items():
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        if key in hidden:
            out.append(f"{label} changed")
        elif key in flags:
            out.append(f"{label} turned {_onoff(b)}")
        elif key in counted:
            out.append(f"{label}: {len(a or [])} {ARROW} {len(b or [])}")
        elif isinstance(a, list) and isinstance(b, list) and all(isinstance(v, (str, int)) for v in a + b):
            out.append(_list_change(label, a, b))
        elif isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
            out.append(f"{label} changed")
        else:
            out.append(f"{label} {_show(a)} {ARROW} {_show(b)}")
    return [o for o in out if o]


def _line(kind: str, head: str, parts: list[str]):
    return [(kind, f"{head}: {'; '.join(parts)}")] if parts else []


# ---------------------------------------------------------------- one setting

def describe_setting(key: str, old, new) -> list[tuple[str, str]]:
    """[(kind, text)] for a setting that went from `old` to `new` (both text, or None)."""
    handler = HANDLERS.get(key)
    if handler:
        return handler(old, new)
    if key in SIMPLE:
        return _simple(key, old, new)
    if key in NOTIFY:
        label, how = NOTIFY[key]
        return [("change", _simple_text("Notifications - " + label, how, old, new, default=_default_notify(key)))]
    if key.startswith("notify.msg."):
        return [("change", f"Notifications: the message for \"{key.split('.', 2)[2]}\" was changed")]
    if key.startswith("notify.enabled."):
        return [("change", f"Notifications: the alert for \"{key.split('.', 2)[2]}\" turned {_onoff(new != '0')}")]
    return []


def _default_notify(key):
    import alerts
    return alerts.DEFAULTS.get(key)


DEFAULT_OF = {"ui.theme": "dark"}      # what a setting reads as until it is first saved (where "none" would confuse)


def _simple(key, old, new):
    label, how = SIMPLE[key]
    return [("change", _simple_text(label, how, old, new, default=DEFAULT_OF.get(key)))]


def _simple_text(label: str, how: str, old, new, default=None) -> str:
    if how.startswith("onoff"):
        on_by_default = how == "onoff_default_on" or default == "1"
        value = (lambda v: (v if v is not None else ("1" if on_by_default else "0")) not in ("0", ""))
        return f"Setting changed: {label} turned {_onoff(value(new))}"
    shown = {"hours": lambda v: "off" if v in (None, "", "0") else f"{v} h",
             "scale": lambda v: "automatic" if v in (None, "", "auto") else f"{v}%"}.get(how, lambda v: _show(v))
    was = shown(old if old is not None else default)
    return f"Setting changed: {label} {was} {ARROW} {shown(new)}"


# ---- Anti-Bypass

AB_LABELS = {"phrase": "challenge", "length": "phrase length", "grid": "word grid", "complex": "capitals and digits",
             "use_own": "own phrase", "custom_phrase": "own phrase", "hours": "allowed hours", "windows": "the hours",
             "wait_min": "wait (min)"}


def _steps_line(steps, empty: str = "none") -> str:
    """A challenge's steps in a line: "Math Hard x5, Typed phrase Medium x1"."""
    import challenges
    return ", ".join(" ".join(filter(None, (challenges.LABELS[s["kind"]], challenges.level_label(s["kind"], s["level"]))))
                     + f" x{s['times']}" for s in steps) or empty


def _steps_part(label: str, old, new, empty: str = "none") -> list[str]:
    """["challenge steps: Typed phrase Medium x1 → Math Hard x5, Typed phrase Medium x1"] when the lists differ."""
    return [f"{label}: {_steps_line(old, empty)} {ARROW} {_steps_line(new, empty)}"] if old != new else []


def _antibypass(old, new):
    import antibypass
    from rules import days_text
    a, b = antibypass.with_defaults(_dict(old)), antibypass.with_defaults(_dict(new))
    parts = _fields(a, b, {k: v for k, v in AB_LABELS.items() if k != "windows"},
                    flags=("phrase", "grid", "complex", "use_own", "hours"), hidden=("custom_phrase",))
    parts += _steps_part("challenge steps", a["steps"], b["steps"])
    if a["windows"] != b["windows"]:
        parts.append("hours " + "; ".join(f"{days_text(w['days'])} {w['start']}-{w['end']}"
                                          for w in b["windows"]) if b["windows"] else "hours: none")
    return _line("antibypass_change", "Anti-Bypass changed", parts)    # (an unlock's own times are not a change)


# ---- protection lists

def _protection_names(cfg: dict) -> dict:
    from blocker import protection
    names = {k: v[0] for k, v in protection.LISTS.items()}
    names.update({c.get("key"): c.get("name") for c in cfg.get("custom") or [] if isinstance(c, dict)})
    names.update({m.get("key"): m.get("name") for m in cfg.get("manual") or [] if isinstance(m, dict)})
    return names


def _protection(old, new):
    from blocker import protection
    a, b = _dict(old), _dict(new)
    default_on = [k for k, v in protection.LISTS.items() if v[3]]
    names = {**_protection_names(a), **_protection_names(b)}
    out = []
    on_a, on_b = a.get("enabled", default_on), b.get("enabled", default_on)
    added, removed = _split(on_a, on_b)
    out += [("change", f"Protection list switched on: {names.get(k, k)}") for k in added]
    out += [("change", f"Protection list switched off: {names.get(k, k)}") for k in removed]
    added, removed = _split(a.get("allowed", []), b.get("allowed", []))
    out += [("change", f"Protection: site allowed anyway: {h}") for h in added]
    out += [("change", f"Protection: site no longer allowed anyway: {h}") for h in removed]
    added, removed = _split(*(x if isinstance(x, list) else [] for x in (a.get("allowed_words"), b.get("allowed_words"))))
    out += [("change", f"Protection: blocked words don't apply on allowed site {h}") for h in added]
    out += [("change", f"Protection: blocked words apply again on allowed site {h}") for h in removed]
    ca = {c.get("key"): c for c in a.get("custom") or [] if isinstance(c, dict)}
    cb = {c.get("key"): c for c in b.get("custom") or [] if isinstance(c, dict)}
    out += [("change", f"Protection: connected your own list \"{c.get('name')}\"") for k, c in cb.items() if k not in ca]
    out += [("change", f"Protection: removed your own list \"{c.get('name')}\"") for k, c in ca.items() if k not in cb]
    ma = {m.get("key"): m for m in a.get("manual") or [] if isinstance(m, dict)}
    mb = {m.get("key"): m for m in b.get("manual") or [] if isinstance(m, dict)}
    for k, m in mb.items():
        entries = {e.get("host") for e in m.get("entries") or [] if isinstance(e, dict)}
        if k not in ma:
            out.append(("change", f"Protection: made your own list \"{m.get('name')}\" ({len(entries)} sites)"))
            continue
        before = {e.get("host") for e in ma[k].get("entries") or [] if isinstance(e, dict)}
        if entries != before:
            out.append(("change", f"Protection: your list \"{m.get('name')}\": {len(entries - before)} sites added, "
                                  f"{len(before - entries)} removed"))
        if ma[k].get("name") != m.get("name"):
            out.append(("change", f"Protection: your list \"{ma[k].get('name')}\" renamed to \"{m.get('name')}\""))
    out += [("change", f"Protection: deleted your own list \"{m.get('name')}\"") for k, m in ma.items() if k not in mb]
    parts = _fields({"auto": a.get("auto", True), "every_hours": a.get("every_hours", 24)},
                    {"auto": b.get("auto", True), "every_hours": b.get("every_hours", 24)},
                    {"auto": "automatic list updates", "every_hours": "update every (h)"}, flags=("auto",))
    return out + _line("change", "Protection", parts)


# ---- blocked words, safe search

WORD_LABELS = {"enabled": "word check", "safesearch": "SafeSearch", "youtube": "YouTube Restricted Mode",
               "action": "action", "lists": "ready-made word lists", "off": "single words turned off",
               "words": "your own words", "exceptions": "exceptions", "words_on": "your own words",
               "exceptions_on": "exceptions"}


def _words(old, new):
    import keywords
    a, b = {**keywords.DEFAULTS, **_dict(old)}, {**keywords.DEFAULTS, **_dict(new)}
    parts = _fields(a, b, {k: WORD_LABELS[k] for k in ("enabled", "safesearch", "youtube", "action")},
                    flags=("enabled", "safesearch", "youtube"))
    for key in ("words_on", "exceptions_on"):
        if a[key] != b[key]:
            parts.append(f"{WORD_LABELS[key]} turned {_onoff(b[key])}")
    for lst in ("lists",):    # (the lists' names, not their words)
        for name, on in b[lst].items():
            if a[lst].get(name, True) != on:
                parts.append(f"ready-made list {name} turned {_onoff(on)}")
    for key in ("words", "exceptions", "off"):   # (how many - the words themselves are private)
        added, removed = _split(a[key], b[key])
        if added or removed:
            parts.append(f"{WORD_LABELS[key]}: {len(added)} added, {len(removed)} removed")
    return _line("change", "Blocked words and SafeSearch", parts)


# ---- modes

MODE_LABELS = {"name": "name", "categories": "categories", "items": "sites and apps", "groups": "groups",
               "extra": "other sites and apps", "mute": "mutes alerts", "pomodoro": "Pomodoro", "schedule": "schedule"}


def _modes_with_builtins(text) -> dict:
    """id -> mode, the five built-in modes as they are with what was saved over them (modes.load), without the flag."""
    import modes
    saved = {m.get("id"): m for m in _json(text, []) if isinstance(m, dict)}
    out = {d["id"]: {**d, **saved.get(d["id"], {})} for d in modes.DEFAULT_MODES}
    out.update({k: m for k, m in saved.items() if k not in out})
    return {k: {f: v for f, v in m.items() if f != "builtin"} for k, m in out.items()}


def _modes_list(old, new):
    a, b = _modes_with_builtins(old), _modes_with_builtins(new)
    out = []
    for mid, m in b.items():
        if mid not in a:
            out.append(("change", f"Mode added: {m.get('name', mid)}"))
            continue
        parts = [f"{label} changed" for key, label in MODE_LABELS.items()
                 if a[mid].get(key) != m.get(key) and key != "name"]
        if a[mid].get("name") != m.get("name"):
            parts.insert(0, f"renamed from {a[mid].get('name')}")
        out += _line("change", f"Mode changed: {m.get('name', mid)}", parts)
    out += [("change", f"Mode removed: {m.get('name', mid)}") for mid, m in a.items() if mid not in b]
    return out


def _modes_active(old, new):
    import modes
    a, b = _dict(old), _dict(new)
    names = {m["id"]: m["name"] for m in modes.DEFAULT_MODES}

    def name(state):
        return names.get(state.get("id"), state.get("id"))
    if b and (not a or a.get("id") != b.get("id")):
        until = f" until {b['until'][-8:-3]}" if b.get("until") else ""
        return [("mode", f"Mode started: {name(b)}{until}" + (" (locked)" if b.get("locked") else ""))]
    if a and not b:
        return [("mode", f"Mode stopped: {name(a)}")]
    return []


# ---- Pause, switched off, tray exit

def _pause(old, new):
    a, b = _dict(old), _dict(new)
    if b and not a:
        return [("pause", f"Pause my blocks started, until {str(b.get('until', ''))[-8:-3]}"
                          + (" (silent)" if b.get("silent") else ""))]
    if b and a:
        return [("pause", f"Pause my blocks changed, until {str(b.get('until', ''))[-8:-3]}")]
    if a and not b:
        return [("pause", "Pause my blocks ended: your blocks are back on")]
    return []


def _off(old, new):
    if new and not old:
        return [("off", "Lockdown switched off: nothing is blocked until it is switched on again")]
    if old and not new:
        return [("on", "Lockdown switched back on")]
    return []


def _exited(old, new):
    if new == "1" and old != "1":
        return [("tray_exit", "Quit from the tray (Exit): the tray icon and the window are closed, blocking keeps running")]
    return []


# ---- categories

def _categories(old, new):
    a, b = _dict(old), _dict(new)
    out = []
    ka = {c.get("key"): c for c in a.get("custom") or [] if isinstance(c, dict)}
    kb = {c.get("key"): c for c in b.get("custom") or [] if isinstance(c, dict)}
    out += [("change", f"Category added: {c.get('name')}") for k, c in kb.items() if k not in ka]
    out += [("change", f"Category removed: {c.get('name')}") for k, c in ka.items() if k not in kb]
    for k, c in kb.items():
        if k in ka and ka[k].get("name") != c.get("name"):
            out.append(("change", f"Category renamed: {ka[k].get('name')} {ARROW} {c.get('name')}"))
    names = {**{k: c.get("name") for k, c in ka.items()}, **{k: c.get("name") for k, c in kb.items()}}
    ca, cb = a.get("colors") or {}, b.get("colors") or {}
    for k in sorted(set(ca) | set(cb)):
        if ca.get(k) != cb.get(k):
            out.append(("change", f"Category colour changed: {names.get(k, k.capitalize())}"))
    return out


# ---- When limits reset, emergency unlock kinds

def _reset(old, new):
    def hhmm(text):
        data = _json(text)
        if isinstance(data, dict):
            return data.get("time")
        return text if isinstance(text, str) and text else "00:00"
    a, b = hhmm(old), hhmm(new)
    if a == b:
        return [("change", f"Limits reset: a fresh day was started now (resets at {b})")] if old != new else []
    return [("change", f"Setting changed: when limits reset {a} {ARROW} {b}")]


def _tier_text(t: dict) -> str:
    return f"{t.get('uses')} per {t.get('per')} for {t.get('minutes')} min"


def _emergency_tiers(old, new):
    import emergency
    a = {t["id"]: t for t in emergency.clean(_json(old, []))}
    b = {t["id"]: t for t in emergency.clean(_json(new, []))}
    out = [("change", f"Emergency unlock kind added: {_tier_text(t)}") for k, t in b.items() if k not in a]
    out += [("change", f"Emergency unlock kind removed: {_tier_text(t)}") for k, t in a.items() if k not in b]
    out += [("change", f"Emergency unlock kind changed: {_tier_text(a[k])} {ARROW} {_tier_text(t)}")
            for k, t in b.items() if k in a and a[k] != t]
    return out


# ---- reminders

SLEEP_LABELS = {"on": "reminder", "bedtime": "bedtime", "wake": "wake-up", "before": "warn (min) before",
                "repeat_sec": "repeat every (s)", "mode": "mode", "warn_text": "warning text", "text": "text",
                "tiers": "bedtime steps", "guarded": "important", "dismiss_wait": "Dismiss waits (s)",
                "grey": "grey screen", "block_behind": "block clicks behind",
                "challenge": "challenge before Dismiss", "challenge_complex": "challenge capitals and digits",
                "challenge_grid": "challenge word grid"}
BREAK_LABELS = {"on": "reminder", "every_sec": "every (s)", "length": "length (min)", "strict": "strict", "snooze_sec": "snooze (s)",
                "max_snooze": "snoozes", "twenty": "20-20-20", "text": "text", "twenty_text": "20-20-20 text",
                "guarded": "important", "allow": "apps allowed during breaks"}
CUSTOM_LABELS = {"on": "reminder", "text": "text", "kind": "kind", "every_sec": "every (s)", "times": "times", "days": "days",
                 "window": "window", "snooze": "snooze (min)", "max_snooze": "snoozes", "check": "check", "packs": "quotes",
                 "quotes": "own quotes", "hours": "only between the hours", "per_day": "stop after (per day)",
                 "guarded": "important"}
FLAGS = ("on", "strict", "twenty", "guarded", "hours", "grey", "block_behind", "challenge", "challenge_complex",
         "challenge_grid")
TEXTS = ("warn_text", "text", "twenty_text", "quotes")


def _reminder_named(head: str, labels: dict, defaults: dict, old, new):
    a, b = {**defaults, **_dict(old)}, {**defaults, **_dict(new)}
    parts = _fields(a, b, labels, flags=FLAGS, hidden=TEXTS)
    if "challenge_steps" in defaults:       # (the bedtime challenge's lists: the general one, then each step's own)
        import reminders
        parts += _steps_part("challenge steps", reminders.general_steps(a), reminders.general_steps(b))
        was, now = ({t.get("from"): reminders.tier_steps(t) for t in reminders.tiers_of(x)} for x in (a, b))
        parts += [p for at in dict.fromkeys([*was, *now]) for p in
                  _steps_part(f"challenge steps from {at}", was.get(at, []), now.get(at, []), "the general list")]
    return _line("change", f"Reminder - {head}", parts)


def _custom(old, new):
    a = {r.get("id"): r for r in _json(old, []) if isinstance(r, dict)}
    b = {r.get("id"): r for r in _json(new, []) if isinstance(r, dict)}

    def name(r):
        return " ".join(str(r.get("text") or "own reminder").split())[:30] or "own reminder"
    out = [("change", f"Reminder added: {name(r)}") for k, r in b.items() if k not in a]
    out += [("change", f"Reminder removed: {name(r)}") for k, r in a.items() if k not in b]
    for k, r in b.items():
        if k in a and a[k] != r:
            parts = _fields(a[k], r, CUSTOM_LABELS, flags=FLAGS, hidden=TEXTS)
            out += _line("change", f"Reminder changed: {name(r)}", parts or ["changed"])
    return out


def _reminder_defaults(name: str) -> dict:
    import reminders
    return getattr(reminders, name)


HANDLERS = {
    "antibypass": _antibypass, "protection": _protection, "words": _words, "modes.list": _modes_list,
    "modes.active": _modes_active, "pause.blocks": _pause, "app.off": _off, "agent.exited": _exited,
    "stats.categories": _categories, "limits.reset": _reset, "emergency.tiers": _emergency_tiers,
    "reminders.sleep": lambda o, n: _reminder_named("bedtime", SLEEP_LABELS, _reminder_defaults("DEFAULT_SLEEP"), o, n),
    "reminders.break": lambda o, n: _reminder_named("breaks", BREAK_LABELS, _reminder_defaults("DEFAULT_BREAK"), o, n),
    "reminders.custom": _custom,
}


# ---------------------------------------------------------------- blocked sites / apps and groups (the Draft)

def rule_parts(rule: dict) -> tuple[str, str]:
    """(the rule's kind, what it says): ("Time limit", "30m per day")."""
    import rule_forms
    kind = rule.get("rule_type")
    name = rule_forms.RULE_NAMES.get(kind, str(kind))
    try:
        if kind == "temporary":
            if rule.get("duration_min"):
                from rules import duration_text
                return name, f"for {duration_text(rule['duration_min'] * 60)}"
            return name, f"until {str(rule.get('temp_until') or '')[:16]}"
        return name, rule_forms.summary(rule)
    except Exception:
        return name, "changed"


def rule_text(rule: dict) -> str:
    """One rule in words ("Time limit: 30m per day")."""
    name, detail = rule_parts(rule)
    return f"{name}: {detail}"


def _rule_set(rules) -> dict:
    return {r.get("rule_type"): r for r in rules or [] if isinstance(r, dict)}


def _comparable(rule: dict) -> tuple:
    from gui.draft import RULE_FIELDS
    return tuple(rule.get(f) for f in RULE_FIELDS)


def rule_lines(who: str, old_rules, new_rules) -> list[str]:
    """What changed between two lists of rules of `who` ("YouTube", "group Work")."""
    a, b = _rule_set(old_rules), _rule_set(new_rules)
    out = []
    for kind, rule in b.items():
        if kind not in a:
            out.append(f"Rule added: {who} - {rule_text(rule)}")
        elif _comparable(a[kind]) != _comparable(rule):
            name, before = rule_parts(a[kind])
            out.append(f"Rule changed: {who} - {name.lower()} {before} {ARROW} {rule_parts(rule)[1]}")
    out += [f"Rule removed: {who} - {rule_text(rule)}" for kind, rule in a.items() if kind not in b]
    return out


def item_lines(old: dict | None, new: dict) -> list[str]:
    """One blocked site / app, before (None = new) and after."""
    name = new["display_name"]
    word = "app" if new.get("item_type") == "app" else "site"
    if old is None:
        rules = "; ".join(rule_text(r) for r in new["rules"]) or "no rules of its own"
        return [f"Added {word} {name} ({rules})"]
    out = []
    if old["display_name"] != name:
        out.append(f"Renamed {word} {old['display_name']} {ARROW} {name}")
    if bool(old.get("disabled")) != bool(new.get("disabled")):
        out.append(f"{word.capitalize()} {name} {'disabled (kept, nothing is enforced)' if new.get('disabled') else 'enabled'}")
    added, removed = _split(old["target"].split(), new["target"].split())
    if added or removed:
        out.append(f"{word.capitalize()} {name}: addresses changed ({_list_change('', old['target'].split(), new['target'].split()).lstrip(': ')})")
    if old.get("notify") != new.get("notify"):
        out.append(f"{word.capitalize()} {name}: its alert message setting changed")
    if old.get("block_type") != new.get("block_type"):
        out.append(f"{word.capitalize()} {name}: how it is blocked {_show(old.get('block_type') or 'default')} {ARROW} "
                   f"{_show(new.get('block_type') or 'default')}")
    if old.get("app_path") != new.get("app_path"):
        out.append(f"{word.capitalize()} {name}: the program file changed")
    return out + rule_lines(name, old["rules"], new["rules"])


def group_lines(old: dict | None, new: dict, names: dict) -> list[str]:
    """One group, before (None = new) and after. `names`: item id -> name, for its members."""
    name = new["name"]
    member_names = lambda ids: [names.get(i, f"#{i}") for i in ids]
    if old is None:
        return [f"Added group {name} ({len(new['members'])} members" +
                (f", {'; '.join(rule_text(r) for r in new['rules'])}" if new["rules"] else "") + ")"]
    out = []
    if old["name"] != name:
        out.append(f"Renamed group {old['name']} {ARROW} {name}")
    if bool(old.get("disabled")) != bool(new.get("disabled")):
        out.append(f"Group {name} {'disabled (kept, nothing is enforced)' if new.get('disabled') else 'enabled'}")
    added, removed = _split(old["members"], new["members"])
    if added:
        out.append(f"Group {name}: added {_names(member_names(added))}")
    if removed:
        out.append(f"Group {name}: removed {_names(member_names(removed))}")
    for i, extra in new["members"].items():
        before = old["members"].get(i)
        if i in old["members"] and (before or {}) != (extra or {}):
            mine = lambda rules_: [{**r, "rule_type": t} for t, r in (rules_ or {}).items()]     # (typed by their key)
            out += rule_lines(f"{names.get(i, '#' + str(i))} in group {name}", mine(before), mine(extra)) \
                or [f"Group {name}: {names.get(i, '#' + str(i))}'s own limits changed"]
    if (old.get("app_block"), old.get("site_block")) != (new.get("app_block"), new.get("site_block")) or \
            (old.get("member_blocks") or {}) != (new.get("member_blocks") or {}):
        out.append(f"Group {name}: how its members are blocked changed")
    if (old.get("icon"), old.get("color")) != (new.get("icon"), new.get("color")):
        out.append(f"Group {name}: icon or colour changed")
    return out + rule_lines(f"group {name}", old["rules"], new["rules"])


def draft_lines(saved_items: dict, items: dict, saved_groups: dict, groups: dict, is_unsaved, is_group_unsaved) -> list[str]:
    """Everything the Draft is about to save, in words. `is_unsaved(id)` / `is_group_unsaved(id)` say which ones differ."""
    names = {i: it["display_name"] for i, it in {**saved_items, **items}.items()}
    out = []
    for i, item in sorted(items.items(), key=lambda kv: kv[1]["display_name"].lower()):
        if i < 0 or i not in saved_items:
            out += item_lines(None, item)
        elif is_unsaved(i):
            out += item_lines(saved_items[i], item)
    for i in sorted(set(saved_items) - set(items), key=lambda i: saved_items[i]["display_name"].lower()):
        word = "app" if saved_items[i].get("item_type") == "app" else "site"
        out.append(f"Removed {word} {saved_items[i]['display_name']}")
    for g, group in sorted(groups.items(), key=lambda kv: kv[1]["name"].lower()):
        if g < 0 or g not in saved_groups:
            out += group_lines(None, group, names)
        elif is_group_unsaved(g):
            out += group_lines(saved_groups[g], group, names)
    out += [f"Removed group {saved_groups[g]['name']}" for g in sorted(set(saved_groups) - set(groups))]
    return out
