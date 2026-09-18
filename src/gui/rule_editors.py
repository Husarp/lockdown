"""Editors for one rule each (used by the rule tabs, the group editor and member customization).
Uniform interface: load(rule or None), value() -> rule dict (raises ValueError with a user-facing message)."""
import customtkinter as ctk

from rules import ALLOW, BLOCK, DAY_NAMES, load_schedule, make_schedule

DURATIONS = {"15 min": 15, "30 min": 30, "1 hour": 60, "2 hours": 120, "3 hours": 180,
             "4 hours": 240, "8 hours": 480, "24 hours": 1440}
MODES = {"Allow only during": ALLOW, "Block during": BLOCK}
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
    def __init__(self, master, days, start, end, on_remove):
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
        ctk.CTkButton(time_line, text="✕ Remove window", width=120, fg_color="transparent", border_width=1,
                      command=lambda: on_remove(self)).pack(side="left", padx=12)

    def value(self):
        return [i for i, b in enumerate(self.day_boxes) if b.get()], self.start.get(), self.end.get()


class HoursEditor(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(anchor="w")
        self.mode = ctk.CTkSegmentedButton(top, values=list(MODES))
        self.mode.pack(side="left")
        ctk.CTkLabel(top, text="these hours (end before start = overnight)", text_color=MUTED).pack(side="left", padx=10)
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
        row = WindowRow(self.rows_box, days, start, end, self._remove_row)
        row.pack(anchor="w", pady=2)
        self.rows.append(row)

    def _remove_row(self, row):
        row.destroy()
        self.rows.remove(row)

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
        return {"rule_type": "scheduled", "schedule": make_schedule(MODES[self.mode.get()], [r.value() for r in self.rows]),
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


class TemporaryEditor(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Block for").pack(side="left")
        self.duration = ctk.CTkOptionMenu(self, values=list(DURATIONS), width=110)
        self.duration.pack(side="left", padx=8)
        ctk.CTkLabel(self, text="starting when saved", text_color=MUTED).pack(side="left")
        self.load(None)

    def load(self, rule: dict | None):
        minutes = (rule or {}).get("duration_min")
        self.duration.set(next((k for k, v in DURATIONS.items() if v == minutes), "1 hour"))

    def value(self) -> dict:
        return {"rule_type": "temporary", "duration_min": DURATIONS[self.duration.get()]}


class PermanentEditor(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Always blocked.", text_color=MUTED).pack(side="left")

    def load(self, rule: dict | None):
        pass

    def value(self) -> dict:
        return {"rule_type": "permanent"}


EDITORS = {"scheduled": HoursEditor, "time_limit": LimitEditor, "permanent": PermanentEditor,
           "temporary": TemporaryEditor}
RULE_NAMES = {"scheduled": "Hours", "time_limit": "Daily limit", "permanent": "Permanent", "temporary": "Temporary"}
