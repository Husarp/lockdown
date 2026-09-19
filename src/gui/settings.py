"""Settings page: when limits reset, and the emergency unlock. Changes here apply at once (not via Save)."""
import customtkinter as ctk

from gui import theme

import antibypass
import emergency
from rules import DAY_NAMES, RESET_KEY, change_reset
from gui.dashboard import DEFAULT_GOAL_HOURS, GOAL_KEY
from gui.widgets import ConfirmButton
from gui.components import Segmented
from trusted_time import now_from_db

MUTED = theme.MUTED
ERROR = theme.DANGER
PER_LABELS = {"per day": "day", "per week": "week"}
GOAL_OPTIONS = ["Off"] + [f"{h} h" for h in range(1, 13)]


def when_text(when) -> str:
    return f"{DAY_NAMES[when.weekday()]} {when:%Y-%m-%d %H:%M}"


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        ctk.CTkLabel(self, text="Settings", font=theme.page_title()).pack(
            anchor="w", padx=30, pady=(16, 8))
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")   # (tkraise needs a plain frame on top)
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self._build_appearance()
        self._build_reset()
        self._build_emergency()
        self.load()

    def _section(self, title: str) -> ctk.CTkFrame:
        box = ctk.CTkFrame(self.body)
        box.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(box, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=16, pady=(12, 4))
        return box

    # ---------- appearance / goal ----------

    def _build_appearance(self):
        from gui.app import APPEARANCES
        box = self._section("Appearance")
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", padx=16, pady=(4, 8))
        ctk.CTkLabel(line, text="Theme").pack(side="left", padx=(0, 10))
        self.appearance = Segmented(line, values=list(APPEARANCES), command=self.app.set_appearance)
        self.appearance.pack(side="left")
        mode = self.db.get_setting("ui.appearance", "dark")
        self.appearance.set(next(k for k, v in APPEARANCES.items() if v == mode))
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", padx=16, pady=(0, 12))
        ctk.CTkLabel(line, text="Daily screen-time goal").pack(side="left", padx=(0, 10))
        self.goal = ctk.CTkOptionMenu(line, width=90, values=GOAL_OPTIONS, command=self._goal_changed)
        self.goal.pack(side="left")
        hours = self.db.get_setting(GOAL_KEY, DEFAULT_GOAL_HOURS)
        self.goal.set("Off" if hours == "0" else f"{hours} h")
        ctk.CTkLabel(line, text="shown as a dashed line on the day charts", text_color=MUTED).pack(side="left", padx=10)

    def _goal_changed(self, value: str):
        self.db.set_setting(GOAL_KEY, "0" if value == "Off" else value.split()[0])

    # ---------- limit reset time ----------

    def _build_reset(self):
        box = self._section("When limits reset")
        ctk.CTkLabel(box, text="Time limits and opening limits start over at this time every day (weekly limits on "
                               "Monday, monthly ones on the 1st, at the same time). Screen-time stats keep normal days.",
                     text_color=MUTED, wraplength=760, justify="left").pack(anchor="w", padx=16)
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", padx=16, pady=(8, 0))
        ctk.CTkLabel(line, text="Limits reset at").pack(side="left")
        self.reset_entry = ctk.CTkEntry(line, width=70)
        self.reset_entry.pack(side="left", padx=8)
        self.reset_btn = ConfirmButton(line, self._change_reset, text="Change", confirm_text="Confirm change",
                                       width=130)
        self.reset_btn.pack(side="left")
        self.reset_info = ctk.CTkLabel(box, text="", text_color=MUTED, wraplength=760, justify="left")
        self.reset_info.pack(anchor="w", padx=16, pady=(6, 0))
        self.reset_error = ctk.CTkLabel(box, text="", text_color=ERROR)
        self.reset_error.pack(anchor="w", padx=16, pady=(0, 12))

    def _change_reset(self):
        now = now_from_db(self.db)
        clock = self.db.limit_clock()
        text = self.reset_entry.get().strip()
        try:
            if text == f"{clock.time:%H:%M}":
                raise ValueError("That's already the reset time.")
            value = change_reset(self.db.get_setting(RESET_KEY), text, now)
        except ValueError as e:
            self.reset_error.configure(text=str(e))
            return
        self.db.set_setting(RESET_KEY, value)
        self.load()

    # ---------- emergency unlock ----------

    def _build_emergency(self):
        box = self._section("Emergency unlock")
        ctk.CTkLabel(box, text="The \"Emergency unlock\" button (Blocking > Overview) unblocks the sites/apps you pick "
                               "for a while. Unlocking several at once counts as one use.",
                     text_color=MUTED, wraplength=760, justify="left").pack(anchor="w", padx=16)
        self.em_enabled = ctk.CTkSwitch(box, text="Allow emergency unlocks", command=self._save_emergency)
        self.em_enabled.pack(anchor="w", padx=16, pady=(8, 4))
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", padx=16, pady=4)
        ctk.CTkLabel(line, text="Each unlock lasts").pack(side="left")
        self.em_minutes = ctk.CTkOptionMenu(line, width=90, values=[f"{m} min" for m in emergency.MINUTE_OPTIONS],
                                            command=lambda v: self._save_emergency())
        self.em_minutes.pack(side="left", padx=8)
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", padx=16, pady=4)
        ctk.CTkLabel(line, text="Allowed").pack(side="left")
        self.em_uses = ctk.CTkOptionMenu(line, width=70, values=[str(n) for n in range(1, 11)],
                                         command=lambda v: self._save_emergency())
        self.em_uses.pack(side="left", padx=8)
        ctk.CTkLabel(line, text="times").pack(side="left")
        self.em_per = Segmented(line, values=list(PER_LABELS), command=lambda v: self._save_emergency())
        self.em_per.pack(side="left", padx=8)
        self.em_info = ctk.CTkLabel(box, text="", text_color=MUTED)
        self.em_info.pack(anchor="w", padx=16, pady=(4, 12))

    def _save_emergency(self):
        new = {"emergency.enabled": "1" if self.em_enabled.get() else "0",
               "emergency.minutes": self.em_minutes.get().split()[0], "emergency.uses": self.em_uses.get(),
               "emergency.per": PER_LABELS[self.em_per.get()]}
        old = {k: emergency.get(self.db, k) for k in new}

        def save():
            for key, value in new.items():
                self.db.set_setting(key, value)
            self.load()
            if "Blocking" in self.app.pages:
                self.app.pages["Blocking"].refresh()   # shows / hides the button
        if antibypass.emergency_looser(old, new):   # more / longer emergency unlocks: Anti-Bypass first
            self.app.guard(["Allow more or longer emergency unlocks"], save, self.load)
        else:
            save()

    # ---------- load ----------

    def load(self):
        now = now_from_db(self.db)
        clock = self.db.limit_clock()
        self.reset_entry.delete(0, "end")
        self.reset_entry.insert(0, f"{clock.time:%H:%M}")
        _start, end = clock.day(now)
        info = f"The current limit day runs until {when_text(end)}."
        if clock.carry_until and now < clock.carry_until:
            info += " It's longer than usual because the time was changed - a change never starts a new day early."
        self.reset_info.configure(text=info)
        self.reset_error.configure(text="")

        g = lambda k: emergency.get(self.db, k)
        self.em_enabled.select() if g("emergency.enabled") == "1" else self.em_enabled.deselect()
        self.em_minutes.set(f"{g('emergency.minutes')} min")
        self.em_uses.set(g("emergency.uses"))
        self.em_per.set(next(k for k, v in PER_LABELS.items() if v == g("emergency.per")))
        left, allowed, reset = emergency.uses_left(self.db, now)
        self.em_info.configure(text=f"{left} of {allowed} left {emergency.PERIODS[g('emergency.per')]} "
                                    f"(resets {when_text(reset)}).")
