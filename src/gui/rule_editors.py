"""Editors for one rule each (used by the rule tabs, the group editor and member customization).
Uniform interface: load(rule or None), value() -> rule dict (raises ValueError with a user-facing message)."""
import re
from datetime import datetime

import customtkinter as ctk

from gui import theme
from gui.components import Segmented, help_icon
from rules import (ALLOW, BLOCK, DAY_LIMITS_FIELD, DAY_NAMES, DEFAULT_VISIT_GAP_MIN, OPEN_LIMIT_FIELDS, PERIODS,
                   SWITCH, TIME_FMT, TIME_LIMIT_FIELDS, VISIT, allowance_shared, day_limits, day_limits_text,
                   days_text, duration_text, load_schedule, make_day_limits, make_schedule)

DURATIONS = {"15 min": 15, "30 min": 30, "1 hour": 60, "2 hours": 120, "3 hours": 180,
             "4 hours": 240, "8 hours": 480, "24 hours": 1440}
CUSTOM = "Custom..."
UNITS = {"days": 1440, "hours": 60, "minutes": 1}   # biggest first (used to display a custom duration)
MAX_TEMPORARY_MIN = 30 * 1440
MODES = {"Allow only": ALLOW, "Block": BLOCK}   # short: these sit in a row that has to fit a narrow window
SWITCH_MODES = {"Launches / new visits": VISIT, "Every switch": SWITCH}
MUTED = theme.MUTED
PERIOD_LABELS = {"day": "per day", "week": "per week", "month": "per month"}
PERIOD_MAX_MIN = {"day": 1440, "week": 7 * 1440, "month": 31 * 1440}


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


def _minutes(text: str, low: int, high: int, what: str) -> int:
    try:
        value = int(text.strip() or 0)
    except ValueError:
        value = -1
    if not low <= value <= high:
        raise ValueError(f"{what} must be {low}-{high} minutes.")
    return value


class DayToggle(ctk.CTkButton):
    """A day as a small button: orange when on."""

    def __init__(self, master, text: str, on: bool):
        super().__init__(master, text=text, width=38, height=28, border_width=1, font=theme.semi(12),
                         command=self.flip)
        self.on = on
        self._paint()

    def flip(self):
        self.on = not self.on
        self._paint()

    def _paint(self):
        self.configure(fg_color=theme.ACCENT if self.on else "transparent",
                       border_color=theme.ACCENT if self.on else theme.BORDER,
                       text_color=theme.WHITE if self.on else theme.MUTED,
                       hover_color=theme.ACCENT_PRESS if self.on else theme.SURFACE2)

    def get(self) -> bool:
        return self.on


ALL_DAYS = list(range(7))


class WindowRow(ctk.CTkFrame):
    """One time window: day buttons + from/to (+ × to remove it, when on_remove is given). A window with no days on
    is ignored."""

    def __init__(self, master, days, start, end, on_remove=None):
        super().__init__(master, border_width=1, border_color=theme.BORDER, corner_radius=6, fg_color="transparent")
        # no Tk variables here: rows get destroyed, and orphaned variables warn when collected off the Tk thread
        line = ctk.CTkFrame(self, fg_color="transparent")
        line.pack(anchor="w", padx=10, pady=8)
        self.day_boxes = []
        for i, day in enumerate(DAY_NAMES):
            box = DayToggle(line, day[:3], i in days)
            box.pack(side="left", padx=(0, 4))
            self.day_boxes.append(box)
        self.start = ctk.CTkEntry(line, width=58, justify="center")
        self.start.insert(0, start)
        self.start.pack(side="left", padx=(10, 0))
        ctk.CTkLabel(line, text="to", text_color=MUTED).pack(side="left", padx=8)
        self.end = ctk.CTkEntry(line, width=58, justify="center")
        self.end.insert(0, end)
        self.end.pack(side="left")
        self.remove_btn = None
        if on_remove:
            self.remove_btn = ctk.CTkButton(line, text="×", width=28, height=28, fg_color="transparent",
                                            hover_color=theme.SURFACE2, text_color=MUTED, font=theme.body(16),
                                            command=lambda: on_remove(self))
            self.remove_btn.pack(side="left", padx=(8, 0))

    def set_removable(self, removable: bool):
        """Hide the × on the only window - removing the last one would make the setting do nothing."""
        if self.remove_btn:
            self.remove_btn.pack(side="left", padx=(8, 0)) if removable else self.remove_btn.pack_forget()

    def value(self):
        return [i for i, b in enumerate(self.day_boxes) if b.get()], self.start.get(), self.end.get()


class HoursEditor(ctk.CTkFrame):
    def __init__(self, master, shared: bool = False):
        super().__init__(master, fg_color="transparent")
        self.shared = shared
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(anchor="w")
        self.mode = Segmented(top, values=list(MODES))
        self.mode.pack(side="left")
        help_icon(top, "Allow only: the site / app works only in these times.\nBlock: it's blocked in these "
                       "times.\nAn end before the start means overnight (22:00 to 07:00). × removes a time "
                       "window.").pack(side="left", padx=8)
        self.rows_box = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_box.pack(anchor="w", pady=4)
        ctk.CTkButton(self, text="+ Add time window", width=140, **theme.OUTLINE,
                      command=lambda: self._add_row(ALL_DAYS, "09:00", "17:00")).pack(anchor="w")
        allowance = ctk.CTkFrame(self, fg_color="transparent")
        allowance.pack(anchor="w", pady=(8, 0))
        ctk.CTkLabel(allowance, text="During blocked times, still allow").pack(side="left")
        self.allowance = ctk.CTkEntry(allowance, width=56)
        self.allowance.pack(side="left", padx=8)
        ctk.CTkLabel(allowance, text="minutes").pack(side="left")
        help_icon(allowance, "0 = fully blocked. Otherwise you can still use it this many minutes in each blocked "
                             "period; they start again in the next one.").pack(side="left", padx=8)
        self.pot = None
        if shared:   # a group: one pot for everything in it, or that many minutes each
            pot_row = ctk.CTkFrame(self, fg_color="transparent")
            pot_row.pack(anchor="w", pady=(6, 0))
            self.pot = ctk.CTkCheckBox(pot_row, text="One pot shared by every member", checkbox_width=18,
                                       checkbox_height=18)
            self.pot.select()
            self.pot.pack(side="left")
            help_icon(pot_row, "On: the minutes are spent by whichever member you use, and run out for all of "
                               "them together.\nOff: every member gets this many minutes of its own.").pack(
                side="left", padx=8)
        self.rows: list[WindowRow] = []
        self.load(None)

    def _add_row(self, days, start, end):
        row = WindowRow(self.rows_box, days, start, end, on_remove=self._remove_row)
        row.pack(anchor="w", pady=2)
        self.rows.append(row)

    def _remove_row(self, row):
        self.rows.remove(row)
        row.destroy()

    def load(self, rule: dict | None):
        for row in self.rows:
            row.destroy()
        self.rows = []
        self.allowance.delete(0, "end")
        self.allowance.insert(0, str((rule or {}).get("allowance_min") or 0))
        if self.pot is not None:
            self.pot.select() if allowance_shared(rule or {}) else self.pot.deselect()
        if rule and rule.get("schedule"):
            s = load_schedule(rule["schedule"])
            self.mode.set(next(k for k, v in MODES.items() if v == s["mode"]))
            for w in s["windows"]:
                self._add_row(w["days"], w["start"], w["end"])
        else:
            self.mode.set("Allow only")
            self._add_row(ALL_DAYS, "09:00", "17:00")

    def value(self) -> dict:
        allowance = _minutes(self.allowance.get(), 0, 1440, "Allowance")
        windows = [w for w in (r.value() for r in self.rows) if w[0]]   # windows without days are ignored
        if not windows:
            raise ValueError("Add a time window (with at least one day on).")
        return {"rule_type": "scheduled", "schedule": make_schedule(MODES[self.mode.get()], windows),
                "allowance_min": allowance or None,
                "allowance_shared": None if self.pot is None else int(bool(self.pot.get()))}


def _period_entries(parent, label: str, width: int) -> dict:
    """"<label> [ ] per day  [ ] per week  [ ] per month" - any of them may be left empty."""
    line = ctk.CTkFrame(parent, fg_color="transparent")
    line.pack(anchor="w")
    ctk.CTkLabel(line, text=label).pack(side="left", padx=(0, 8))
    entries = {}
    for p in PERIODS:
        entries[p] = ctk.CTkEntry(line, width=width)
        entries[p].pack(side="left")
        ctk.CTkLabel(line, text=PERIOD_LABELS[p]).pack(side="left", padx=(4, 14))
    return entries


def _set(entry, text: str):
    entry.delete(0, "end")
    entry.insert(0, text)


class LimitEditor(ctk.CTkFrame):
    """Time limit per day / week / month - any combination; each one blocks until its period ends. The daily one
    can have its own amount on each weekday (0.84.12): untick "Same every day" for a Mon..Sun grid."""

    QUICK = {"Mon–Fri": range(5), "Sat–Sun": range(5, 7), "Every day": range(7)}

    def __init__(self, master, shared: bool = False):
        super().__init__(master, fg_color="transparent")
        self.entries = _period_entries(self, "Shared limit" if shared else "At most", 58 if shared else 64)
        same = ctk.CTkFrame(self, fg_color="transparent")
        same.pack(anchor="w", pady=(6, 0))
        self.same = ctk.CTkCheckBox(same, text="Same every day", checkbox_width=18, checkbox_height=18,
                                    command=self._same_changed)
        self.same.pack(side="left")
        help_icon(same, "Untick to give each weekday its own daily limit, e.g. 3h Monday to Friday and 2h at the "
                        "weekend. Leave a day empty for no daily limit that day.\nA day starts at the limit reset "
                        "time: with a 03:00 reset, Saturday 01:00 still counts as Friday.").pack(side="left", padx=8)
        # the Mon..Sun grid (shown when "Same every day" is off)
        self.grid_box = ctk.CTkFrame(self, fg_color="transparent")
        self.day_entries = []
        for i, day in enumerate(DAY_NAMES):
            ctk.CTkLabel(self.grid_box, text=day[:3], text_color=MUTED, height=18).grid(row=0, column=i, padx=2)
            entry = ctk.CTkEntry(self.grid_box, width=42 if shared else 50, justify="center")
            entry.grid(row=1, column=i, padx=2)
            self.day_entries.append(entry)
        fill = ctk.CTkFrame(self.grid_box, fg_color="transparent")
        fill.grid(row=2, column=0, columnspan=7, sticky="w", pady=(4, 0))
        ctk.CTkLabel(fill, text="Fill").pack(side="left", padx=(2, 6))
        self.fill = ctk.CTkEntry(fill, width=50, justify="center")
        self.fill.pack(side="left")
        ctk.CTkLabel(fill, text="into", text_color=MUTED).pack(side="left", padx=6)
        for label, days in self.QUICK.items():
            ctk.CTkButton(fill, text=label, width=10, height=26, **theme.OUTLINE,
                          command=lambda d=days: self._fill(d)).pack(side="left", padx=(0, 4))
        note = ("For all members together. " if shared else "") + \
            "E.g. 45m, 2h, 1h30; leave empty for no limit. Counted while the app is in front / the site is the " \
            "active browser tab; resets at the limit reset time (Settings)."
        # A fixed wrap width, NOT one measured from the panel: `shared` already tells us which panel we are in
        # (the group editor's is the narrow one). Measuring it in a <Configure> handler and re-wrapping the label
        # made the app hang - the taller label flipped the scrollbar on, the scrollbar took width away, the label
        # re-wrapped, and CTk's scrollbar redraws by calling update_idletasks, so it span forever.
        self.note = ctk.CTkLabel(self, text=note, text_color=MUTED, wraplength=300 if shared else 480,
                                 justify="left", anchor="w")
        self.note.pack(anchor="w", fill="x", pady=(4, 0))
        self.load(None)

    def _fill(self, days):
        for d in days:
            _set(self.day_entries[d], self.fill.get().strip())

    def _same_changed(self):
        """The tick was changed by hand: carry the amount over (one amount -> every day; back -> the first day's)."""
        day = self.entries["day"]
        if self.same.get():
            first = next((e.get().strip() for e in self.day_entries if e.get().strip()), "")
            self._show_grid(False)
            _set(day, first)
        else:
            for entry in (*self.day_entries, self.fill):
                _set(entry, day.get().strip())
            _set(day, "")
            self._show_grid(True)

    def _show_grid(self, on: bool):
        """Per weekday: the grid shows, and the single "per day" box is greyed out."""
        if on:   # (a disabled CTkEntry looks just like an empty one: drop its fill so it reads as off)
            day = self.entries["day"]
            day.configure(state="disabled", fg_color=day.cget("bg_color"))
            self.grid_box.pack(anchor="w", pady=(4, 0), before=self.note)
        else:
            self.entries["day"].configure(state="normal", fg_color=self.entries["week"].cget("fg_color"))
            self.grid_box.pack_forget()

    def load(self, rule: dict | None):
        rule = rule or {"daily_limit_min": 30}
        self._show_grid(False)
        for p, entry in self.entries.items():
            _set(entry, format_duration(rule.get(TIME_LIMIT_FIELDS[p])))
        days = day_limits(rule)
        for entry, minutes in zip(self.day_entries, days or [None] * 7):
            _set(entry, format_duration(minutes))
        _set(self.fill, "")
        self.same.deselect() if days else self.same.select()
        self._show_grid(bool(days))

    def value(self) -> dict:
        rule = {"rule_type": "time_limit", DAY_LIMITS_FIELD: None}
        for p, entry in self.entries.items():
            if p == "day" and not self.same.get():
                continue
            rule[TIME_LIMIT_FIELDS[p]] = self._read(entry.get(), p)
        if not self.same.get():
            rule[TIME_LIMIT_FIELDS["day"]], rule[DAY_LIMITS_FIELD] = make_day_limits(
                [self._read(e.get(), "day", DAY_NAMES[i]) for i, e in enumerate(self.day_entries)])
        if not any(rule[f] for f in (*TIME_LIMIT_FIELDS.values(), DAY_LIMITS_FIELD)):
            raise ValueError("Fill in at least one time limit (per day, week or month).")
        return rule

    @staticmethod
    def _read(text: str, period: str, day: str = "") -> int | None:
        minutes = parse_duration(text)
        if minutes is not None and not 1 <= minutes <= PERIOD_MAX_MIN[period]:
            what = f"on {day}" if day else PERIOD_LABELS[period]
            raise ValueError(f"The limit {what} must be between 1m and {PERIOD_MAX_MIN[period] // 60}h.")
        return minutes


class SwitchEditor(ctk.CTkFrame):
    """Opening limit per day / week / month (any combination), and what counts as opening it."""

    def __init__(self, master, shared: bool = False):
        super().__init__(master, fg_color="transparent")
        self.entries = _period_entries(self, "Open at most", 52)
        ctk.CTkLabel(self, text=("All members together. " if shared else "") + "Leave empty for no limit; "
                     "the next opening after a limit is blocked.", text_color=MUTED).pack(anchor="w", pady=(4, 0))
        line = ctk.CTkFrame(self, fg_color="transparent")
        line.pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(line, text="What counts").pack(side="left", padx=(0, 8))
        self.mode = Segmented(line, values=list(SWITCH_MODES), command=lambda v: self._mode_changed())
        self.mode.pack(side="left")
        self.visit_line = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.visit_line, text="Apps: each start of the program. Sites: coming back after").pack(side="left")
        self.gap = ctk.CTkEntry(self.visit_line, width=48)
        self.gap.pack(side="left", padx=6)
        ctk.CTkLabel(self.visit_line, text="minutes away (clicking away and back sooner doesn't count)",
                     text_color=MUTED).pack(side="left")
        self.switch_note = ctk.CTkLabel(self, text="every time it comes to the front counts (alt-tab, clicking its "
                                                   "window or browser tab)", text_color=MUTED)
        self.load(None)

    def _mode_changed(self):
        visit = SWITCH_MODES[self.mode.get()] == VISIT
        (self.visit_line if visit else self.switch_note).pack(anchor="w", pady=(6, 0))
        (self.switch_note if visit else self.visit_line).pack_forget()

    def load(self, rule: dict | None):
        rule = rule or {"daily_switch_limit": 10}
        for p, entry in self.entries.items():
            value = rule.get(OPEN_LIMIT_FIELDS[p])
            _set(entry, "" if value is None else str(value))
        _set(self.gap, str(rule.get("visit_gap_min") or DEFAULT_VISIT_GAP_MIN))
        mode = rule.get("switch_mode") or VISIT
        self.mode.set(next(k for k, v in SWITCH_MODES.items() if v == mode))
        self._mode_changed()

    def value(self) -> dict:
        rule = {"rule_type": "switch_limit"}
        for p, entry in self.entries.items():
            text = entry.get().strip()
            try:
                times = int(text) if text else None
            except ValueError:
                times = -1
            if times is not None and not 0 <= times <= 10000:
                raise ValueError(f"Openings {PERIOD_LABELS[p]} must be a number 0-10000.")
            rule[OPEN_LIMIT_FIELDS[p]] = times
        if all(rule[f] is None for f in OPEN_LIMIT_FIELDS.values()):
            raise ValueError("Fill in at least one opening limit (per day, week or month).")
        mode = SWITCH_MODES[self.mode.get()]
        rule["switch_mode"] = mode
        if mode == VISIT:
            rule["visit_gap_min"] = _minutes(self.gap.get(), 1, 1440, "Minutes away")
        return rule


class TemporaryEditor(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Block for").pack(side="left")
        self.duration = ctk.CTkOptionMenu(self, values=[*DURATIONS, CUSTOM], width=130, command=self._chosen)
        self.duration.pack(side="left", padx=8)
        self.custom = ctk.CTkFrame(self, fg_color="transparent")
        self.amount = ctk.CTkEntry(self.custom, width=64)
        self.amount.pack(side="left")
        self.unit = ctk.CTkOptionMenu(self.custom, values=list(UNITS), width=100)
        self.unit.pack(side="left", padx=(6, 0))
        self.note = ctk.CTkLabel(self, text="starting when saved", text_color=MUTED)
        self.note.pack(side="left", padx=8)
        self.keep_until: str | None = None   # running block being edited: "Keep" leaves its end time alone
        self.keep_label = ""
        self.load(None)

    def _chosen(self, choice: str):
        if choice == CUSTOM:
            self.custom.pack(side="left", before=self.note)
        else:
            self.custom.pack_forget()
        self.note.configure(text="" if choice == self.keep_label else "starting when saved")

    def load(self, rule: dict | None):
        rule = rule or {}
        minutes = rule.get("duration_min")
        preset = next((k for k, v in DURATIONS.items() if v == minutes), None)
        self.keep_until = rule.get("temp_until")
        values = [*DURATIONS, CUSTOM]
        if self.keep_until:
            left = datetime.strptime(self.keep_until, TIME_FMT) - datetime.now()
            self.keep_label = f"Keep ({duration_text(left.total_seconds())} left)"
            values.insert(0, self.keep_label)
        self.duration.configure(values=values)
        self.amount.delete(0, "end")
        if minutes and not preset:   # a custom duration: show it in the biggest whole unit
            unit = next(u for u, m in UNITS.items() if minutes % m == 0)
            self.amount.insert(0, str(minutes // UNITS[unit]))
            self.unit.set(unit)
            self.duration.set(CUSTOM)
        else:
            self.amount.insert(0, "1")
            self.unit.set("hours")
            self.duration.set(self.keep_label if self.keep_until else (preset or "1 hour"))
        self._chosen(self.duration.get())

    def value(self) -> dict:
        choice = self.duration.get()
        if self.keep_until and choice == self.keep_label:
            return {"rule_type": "temporary", "temp_until": self.keep_until}
        if choice != CUSTOM:
            return {"rule_type": "temporary", "duration_min": DURATIONS[choice]}
        try:
            minutes = int(self.amount.get().strip()) * UNITS[self.unit.get()]
        except ValueError:
            minutes = 0
        if not 1 <= minutes <= MAX_TEMPORARY_MIN:
            raise ValueError("Custom duration must be between 1 minute and 30 days.")
        return {"rule_type": "temporary", "duration_min": minutes}


class PermanentEditor(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Always blocked.", text_color=MUTED).pack(side="left")

    def load(self, rule: dict | None):
        pass

    def value(self) -> dict:
        return {"rule_type": "permanent"}


EDITORS = {"scheduled": HoursEditor, "time_limit": LimitEditor, "switch_limit": SwitchEditor,
           "permanent": PermanentEditor, "temporary": TemporaryEditor}
RULE_NAMES = {"scheduled": "By time", "time_limit": "Time limit", "switch_limit": "Opening limit",
              "permanent": "Permanent", "temporary": "Temporary"}
RULE_SUBTITLES = {"scheduled": "allowed only inside these windows - or blocked inside them",
                  "time_limit": "a cap on how long per day / week", "switch_limit": "a cap on how often it's opened",
                  "permanent": "blocked all the time, until removed", "temporary": "blocked for a while from now"}


def summary(rule_type: str, editor) -> str:
    """One line describing an editor's current settings (shown on a collapsed blocker card)."""
    try:
        rule = editor.value()
    except ValueError:
        return "check the settings"
    if rule_type == "scheduled":
        sched = load_schedule(rule["schedule"])
        first = sched["windows"][0]
        text = (f"{'Allowed only' if sched['mode'] == ALLOW else 'Blocked'} {first['start']}-{first['end']} "
                f"{days_text(first['days'])}")
        if len(sched["windows"]) > 1:
            text += f" +{len(sched['windows']) - 1} more"
        if rule.get("allowance_min"):
            text += f" · +{rule['allowance_min']} min" + ("" if allowance_shared(rule) else " each")
        return text
    if rule_type == "time_limit":
        parts = [f"{format_duration(rule[f])} {PERIOD_LABELS[p]}" for p, f in TIME_LIMIT_FIELDS.items() if rule.get(f)]
        if days := day_limits(rule):
            parts.insert(0, f"per day {day_limits_text(days, format_duration)}")
        return " · ".join(parts)
    if rule_type == "switch_limit":
        return " · ".join(f"{rule[f]} opens {PERIOD_LABELS[p]}" for p, f in OPEN_LIMIT_FIELDS.items()
                          if rule.get(f) is not None)
    if rule_type == "temporary":
        if rule.get("temp_until"):
            return "keeps its current end"
        return f"for {duration_text(rule['duration_min'] * 60)}"
    return "always blocked"
