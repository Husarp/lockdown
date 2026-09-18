"""Editors for one rule each (used by the rule tabs, the group editor and member customization).
Uniform interface: load(rule or None), value() -> rule dict (raises ValueError with a user-facing message)."""
from datetime import datetime

import customtkinter as ctk

from rules import (ALLOW, BLOCK, DAY_NAMES, DEFAULT_VISIT_GAP_MIN, SWITCH, TIME_FMT, VISIT, duration_text,
                   load_schedule, make_schedule)

DURATIONS = {"15 min": 15, "30 min": 30, "1 hour": 60, "2 hours": 120, "3 hours": 180,
             "4 hours": 240, "8 hours": 480, "24 hours": 1440}
CUSTOM = "Custom..."
UNITS = {"days": 1440, "hours": 60, "minutes": 1}   # biggest first (used to display a custom duration)
MAX_TEMPORARY_MIN = 30 * 1440
MODES = {"Allow only during": ALLOW, "Block during": BLOCK}
SWITCH_MODES = {"Launches / new visits": VISIT, "Every switch": SWITCH}
MUTED = "gray60"


def _minutes(text: str, low: int, high: int, what: str) -> int:
    try:
        value = int(text.strip() or 0)
    except ValueError:
        value = -1
    if not low <= value <= high:
        raise ValueError(f"{what} must be {low}-{high} minutes.")
    return value


class WindowRow(ctk.CTkFrame):
    """One time window: days + from/to. A window with no days ticked is ignored."""

    def __init__(self, master, days, start, end):
        super().__init__(master, border_width=1, corner_radius=6)
        # no Tk variables here: rows get destroyed, and orphaned variables warn when collected off the Tk thread
        day_line = ctk.CTkFrame(self, fg_color="transparent")
        day_line.pack(anchor="w", padx=8, pady=(6, 2))
        self.day_boxes = []
        for i, day in enumerate(DAY_NAMES):
            box = ctk.CTkCheckBox(day_line, text=day, width=20)
            if i in days:
                box.select()
            box.pack(side="left", padx=(0, 10))
            self.day_boxes.append(box)
        time_line = ctk.CTkFrame(self, fg_color="transparent")
        time_line.pack(anchor="w", padx=8, pady=(2, 6))
        ctk.CTkLabel(time_line, text="from").pack(side="left", padx=(0, 6))
        self.start = ctk.CTkEntry(time_line, width=64)
        self.start.insert(0, start)
        self.start.pack(side="left")
        ctk.CTkLabel(time_line, text="to").pack(side="left", padx=6)
        self.end = ctk.CTkEntry(time_line, width=64)
        self.end.insert(0, end)
        self.end.pack(side="left")

    def value(self):
        return [i for i, b in enumerate(self.day_boxes) if b.get()], self.start.get(), self.end.get()


class HoursEditor(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(anchor="w")
        self.mode = ctk.CTkSegmentedButton(top, values=list(MODES))
        self.mode.pack(side="left")
        ctk.CTkLabel(top, text="these hours (end before start = overnight; untick all days to drop a window)",
                     text_color=MUTED).pack(side="left", padx=10)
        self.rows_box = ctk.CTkFrame(self, fg_color="transparent")
        self.rows_box.pack(anchor="w", pady=4)
        ctk.CTkButton(self, text="+ Add time window", width=140, fg_color="transparent", border_width=1,
                      command=lambda: self._add_row([0, 1, 2, 3, 4], "09:00", "17:00")).pack(anchor="w")
        allowance = ctk.CTkFrame(self, fg_color="transparent")
        allowance.pack(anchor="w", pady=(8, 0))
        ctk.CTkLabel(allowance, text="During blocked hours, still allow").pack(side="left")
        self.allowance = ctk.CTkEntry(allowance, width=56)
        self.allowance.pack(side="left", padx=8)
        ctk.CTkLabel(allowance, text="minutes (0 = fully blocked; resets each blocked period)",
                     text_color=MUTED).pack(side="left")
        self.rows: list[WindowRow] = []
        self.load(None)

    def _add_row(self, days, start, end):
        row = WindowRow(self.rows_box, days, start, end)
        row.pack(anchor="w", pady=2)
        self.rows.append(row)

    def load(self, rule: dict | None):
        for row in self.rows:
            row.destroy()
        self.rows = []
        self.allowance.delete(0, "end")
        self.allowance.insert(0, str((rule or {}).get("allowance_min") or 0))
        if rule and rule.get("schedule"):
            s = load_schedule(rule["schedule"])
            self.mode.set(next(k for k, v in MODES.items() if v == s["mode"]))
            for w in s["windows"]:
                self._add_row(w["days"], w["start"], w["end"])
        else:
            self.mode.set("Allow only during")
            self._add_row([0, 1, 2, 3, 4], "09:00", "17:00")

    def value(self) -> dict:
        allowance = _minutes(self.allowance.get(), 0, 1440, "Allowance")
        windows = [w for w in (r.value() for r in self.rows) if w[0]]   # windows without days are ignored
        if not windows:
            raise ValueError("Tick at least one day.")
        return {"rule_type": "scheduled", "schedule": make_schedule(MODES[self.mode.get()], windows),
                "allowance_min": allowance or None}


class LimitEditor(ctk.CTkFrame):
    def __init__(self, master, shared: bool = False):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Shared daily limit" if shared else "Daily limit").pack(side="left")
        self.minutes = ctk.CTkEntry(self, width=64)
        self.minutes.pack(side="left", padx=8)
        note = ("minutes for all members together" if shared else "minutes") + \
            " - counted while the app is in front / the site is the active browser tab; resets at midnight"
        ctk.CTkLabel(self, text=note, text_color=MUTED, wraplength=560, justify="left").pack(side="left")
        self.load(None)

    def load(self, rule: dict | None):
        self.minutes.delete(0, "end")
        self.minutes.insert(0, str((rule or {}).get("daily_limit_min") or 30))

    def value(self) -> dict:
        return {"rule_type": "time_limit", "daily_limit_min": _minutes(self.minutes.get(), 1, 1440, "Daily limit")}


class SwitchEditor(ctk.CTkFrame):
    """Opening limit: how many times per day it may be opened, and what counts as opening it."""

    def __init__(self, master, shared: bool = False):
        super().__init__(master, fg_color="transparent")
        line = ctk.CTkFrame(self, fg_color="transparent")
        line.pack(anchor="w")
        ctk.CTkLabel(line, text="Open at most").pack(side="left")
        self.times = ctk.CTkEntry(line, width=56)
        self.times.pack(side="left", padx=8)
        ctk.CTkLabel(line, text="times per day" + (" (all members together)" if shared else "") +
                     " - the next opening is blocked", text_color=MUTED).pack(side="left")
        line = ctk.CTkFrame(self, fg_color="transparent")
        line.pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(line, text="What counts").pack(side="left", padx=(0, 8))
        self.mode = ctk.CTkSegmentedButton(line, values=list(SWITCH_MODES), command=lambda v: self._mode_changed())
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
        rule = rule or {}
        self.times.delete(0, "end")
        self.times.insert(0, str(rule.get("daily_switch_limit") or 10))
        self.gap.delete(0, "end")
        self.gap.insert(0, str(rule.get("visit_gap_min") or DEFAULT_VISIT_GAP_MIN))
        mode = rule.get("switch_mode") or VISIT
        self.mode.set(next(k for k, v in SWITCH_MODES.items() if v == mode))
        self._mode_changed()

    def value(self) -> dict:
        try:
            times = int(self.times.get().strip())
        except ValueError:
            times = -1
        if not 0 <= times <= 1000:
            raise ValueError("Openings per day must be 0-1000.")
        mode = SWITCH_MODES[self.mode.get()]
        rule = {"rule_type": "switch_limit", "daily_switch_limit": times, "switch_mode": mode}
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
RULE_NAMES = {"scheduled": "By hours", "time_limit": "Daily time limit", "switch_limit": "Daily opening limit",
              "permanent": "Permanent", "temporary": "Temporary"}
