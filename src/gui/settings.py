"""Settings page: when limits reset, and the emergency unlock. Changes here apply at once (not via Save)."""
import customtkinter as ctk

from gui import theme

import antibypass
import backup
import emergency
from rules import DAY_NAMES, RESET_KEY, change_reset
from gui.dashboard import DEFAULT_GOAL_HOURS, GOAL_KEY
from gui.widgets import ConfirmButton
from gui.components import Segmented, help_icon, page_head
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
        page_head(self, "Settings").pack(
            anchor="w", padx=30, pady=(16, 8))
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")   # (tkraise needs a plain frame on top)
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self._build_appearance()
        self._build_reset()
        self._build_emergency()
        self._build_backup()
        self.load()

    def _section(self, title: str, help_text: str = "") -> ctk.CTkFrame:
        box = ctk.CTkFrame(self.body)
        box.pack(fill="x", pady=(0, 14))
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(anchor="w", padx=16, pady=(12, 4))
        ctk.CTkLabel(head, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        if help_text:
            help_icon(head, help_text).pack(side="left", padx=8)
        return box

    # ---------- appearance / goal ----------

    def _build_appearance(self):
        box = self._section("Appearance")
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", padx=16, pady=(4, 8))
        ctk.CTkLabel(line, text="Theme", width=110, anchor="w").pack(side="left")
        self.appearance = Segmented(line, values=list(theme.THEMES), command=self._theme_changed)
        self.appearance.pack(side="left")
        self.appearance.set(next(k for k, v in theme.THEMES.items() if v == theme.THEME))
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", padx=16, pady=(0, 8))
        ctk.CTkLabel(line, text="Accent colour", width=110, anchor="w").pack(side="left")
        self.swatches = {}
        for name, color in theme.ACCENTS.items():
            b = ctk.CTkButton(line, text="", width=26, height=26, corner_radius=13, fg_color=color, hover_color=color,
                              border_width=2, command=lambda c=color: self._accent_changed(c))
            b.pack(side="left", padx=3)
            self.swatches[color] = b
        ctk.CTkButton(line, text="Custom...", width=90, **theme.OUTLINE, command=self._custom_accent).pack(
            side="left", padx=(10, 0))
        self.restart_line = ctk.CTkFrame(box, fg_color="transparent")
        ctk.CTkLabel(self.restart_line, text="Restart Lockdown to use the new colours everywhere.",
                     text_color=theme.WARNING).pack(side="left")
        ctk.CTkButton(self.restart_line, text="Restart now", width=110, command=self.app.restart).pack(
            side="left", padx=10)
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", padx=16, pady=(0, 12))
        ctk.CTkLabel(line, text="Daily screen-time goal").pack(side="left", padx=(0, 10))
        self.goal = ctk.CTkOptionMenu(line, width=90, values=GOAL_OPTIONS, command=self._goal_changed)
        self.goal.pack(side="left")
        hours = self.db.get_setting(GOAL_KEY, DEFAULT_GOAL_HOURS)
        self.goal.set("Off" if hours == "0" else f"{hours} h")
        help_icon(line, "Shown as a dashed line on the day charts.").pack(side="left", padx=8)
        self.restart_anchor = line   # (the restart hint goes above the goal line)
        self._show_accent()

    def _theme_changed(self, label: str):
        self.app.set_appearance(label)
        self._show_accent()

    def _accent_changed(self, color: str):
        self.db.set_setting(theme.ACCENT_KEY, color.upper())
        self._show_accent()

    def _custom_accent(self):
        from tkinter import colorchooser
        color = colorchooser.askcolor(color=self.db.get_setting(theme.ACCENT_KEY, theme.ACCENT_HEX), parent=self,
                                      title="Accent colour")[1]
        if color:
            self._accent_changed(color)

    def _show_accent(self):
        """Ring around the chosen swatch; "restart" hint while the saved theme / colour differs from what's showing."""
        saved = self.db.get_setting(theme.ACCENT_KEY, theme.ACCENT_HEX).upper()
        for color, b in self.swatches.items():
            b.configure(border_color=theme.TEXT if color.upper() == saved else theme.SURFACE)
        amoled_now = theme.THEME == "amoled"
        amoled_saved = self.db.get_setting(theme.THEME_KEY, theme.THEME) == "amoled"
        if saved != theme.ACCENT_HEX.upper() or amoled_now != amoled_saved:
            self.restart_line.pack(anchor="w", padx=16, pady=(0, 8), before=self.restart_anchor)
        else:
            self.restart_line.pack_forget()

    def _goal_changed(self, value: str):
        self.db.set_setting(GOAL_KEY, "0" if value == "Off" else value.split()[0])

    # ---------- limit reset time ----------

    def _build_reset(self):
        box = self._section("When limits reset", "Time limits and opening limits start over at this time every day "
                                                  "(weekly limits on Monday, monthly ones on the 1st, at the same "
                                                  "time). Screen-time stats keep normal days.")
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
        box = self._section("Emergency unlock", "The \"Emergency unlock\" button (Blocking > Overview) unblocks the "
                                                "sites / apps you pick for a while. Unlocking several at once counts as "
                                                "one use.")
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

    # ---------- backup / export ----------

    def _build_backup(self):
        box = self._section("Backup & export", "A backup has everything you set up: blocked sites and apps with their "
                                               "rules, groups, categories, modes, reminders and settings. Importing "
                                               "one replaces yours (needs the Anti-Bypass challenge if it's on).")
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", padx=16, pady=(4, 4))
        ctk.CTkButton(line, text="Export settings...", width=150, **theme.OUTLINE, command=self._export).pack(
            side="left")
        ctk.CTkButton(line, text="Import settings...", width=150, **theme.OUTLINE, command=self._import).pack(
            side="left", padx=8)
        ctk.CTkButton(line, text="Export screen time (CSV)...", width=200, **theme.OUTLINE,
                      command=self._export_screen_time).pack(side="left")
        self.backup_info = ctk.CTkLabel(box, text="", anchor="w", justify="left", wraplength=760)
        self.backup_info.pack(anchor="w", padx=16, pady=(4, 12))

    def _export(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self, title="Export Lockdown settings", defaultextension=".json",
                                            filetypes=[("Lockdown backup", "*.json")],
                                            initialfile=f"lockdown-backup-{now_from_db(self.db):%Y-%m-%d}.json")
        if path:
            backup.save(self.db, path)
            self.backup_info.configure(text=f"Saved to {path}", text_color=theme.ALLOWED)

    def _import(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self, title="Import Lockdown settings",
                                          filetypes=[("Lockdown backup", "*.json")])
        if not path:
            return
        try:
            data = backup.load(path)
        except ValueError as e:
            self.backup_info.configure(text=str(e), text_color=theme.DANGER)
            return

        def restore():
            backup.restore(self.db, data)
            self.app.draft.discard()   # (reloads the blocks everywhere)
            self.load()
            self.backup_info.configure(text=f"Imported {len(data['items'])} blocked sites / apps and your settings. "
                                            "Restart Lockdown to see a different theme or colour.",
                                       text_color=theme.ALLOWED)
        from pathlib import Path
        self.app.guard([f"Import settings from {Path(path).name} (replaces your blocks and settings)"], restore)

    def _export_screen_time(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self, title="Export screen time", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], initialfile="screen-time.csv")
        if path:
            rows = backup.screen_time_csv(self.db, path)
            self.backup_info.configure(text=f"{rows} rows saved to {path}", text_color=theme.ALLOWED)

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
