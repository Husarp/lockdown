"""Settings page: when limits reset, and the emergency unlock. Changes here apply at once (not via Save).
Design 3j: two columns - left: Appearance (theme, accents, daily goal) + "When limits reset" in one card, then
Categories; right (400px): Emergency unlock (yellow bar; its kinds, each with a "2 of 3 left" meter) and
Backup & export."""
import customtkinter as ctk

from gui import theme

import antibypass
import backup
import emergency
from rules import DAY_NAMES, RESET_KEY, apply_reset_now, change_reset, reset_is_looser
from gui.dashboard import DEFAULT_GOAL_HOURS, GOAL_KEY
from gui.widgets import ConfirmButton, ConfirmDialog, once
from gui.components import Card, Segmented, accent_bar, hairline, help_icon, page_head, LockedStrip
from trusted_time import now_from_db
from ui_theme import ENGINE_KEY, engine_of   # (= main.ENGINE_KEY; default window: ui_theme.DEFAULT_ENGINE)

MUTED = theme.MUTED
ERROR = theme.DANGER
PER_LABELS = {"per day": "day", "per week": "week", "per month": "month"}
NEW_KIND = {"uses": 1, "per": "week", "minutes": 20}   # "+ Add another kind" starts here
SIZES = {"Auto": "auto", "100%": "100", "90%": "90", "80%": "80", "70%": "70"}
GOAL_OPTIONS = ["Off"] + [f"{h} h" for h in range(1, 13)]
RIGHT_W = 400
LABEL_W = 104


def when_text(when) -> str:
    return f"{DAY_NAMES[when.weekday()]} {when:%Y-%m-%d %H:%M}"


def short_when(when) -> str:
    return f"{DAY_NAMES[when.weekday()][:3]} {when.day} {when:%b %H:%M}"


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        page_head(self, "Settings").pack(
            anchor="w", padx=30, pady=(16, 8))
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")   # (tkraise needs a plain frame on top)
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.locked = LockedStrip(self, self.app, self.body)
        cols = ctk.CTkFrame(self.body, fg_color="transparent")
        cols.pack(fill="x")
        cols.grid_columnconfigure(0, weight=1)
        cols.grid_columnconfigure(1, minsize=RIGHT_W)
        left = ctk.CTkFrame(cols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="new", padx=(0, 14))
        right = ctk.CTkFrame(cols, fg_color="transparent")
        right.grid(row=0, column=1, sticky="new")
        self._build_appearance(left)
        self._build_categories(left)
        self._build_emergency(right)
        self._build_backup(right)
        self._build_engine(right)
        self.load()

    def _row(self, parent, label: str, pady=(0, 10)) -> ctk.CTkFrame:
        """A settings line: muted label in a fixed column, controls after it."""
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.pack(anchor="w", fill="x", pady=pady)
        ctk.CTkLabel(line, text=label, width=LABEL_W, anchor="w", text_color=MUTED).pack(side="left", padx=(0, 12))
        return line

    def _subhead(self, parent, title: str, help_text: str = ""):
        """A second card title inside a card (after a hairline), like the plate's "When limits reset"."""
        hairline(parent).pack(fill="x", pady=(4, 12))
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(anchor="w", pady=(0, 10))
        accent_bar(head).pack(side="left", padx=(0, 9))
        ctk.CTkLabel(head, text=title, font=theme.card_title(), height=20).pack(side="left")
        if help_text:
            help_icon(head, help_text).pack(side="left", padx=8)

    # ---------- categories ----------

    def _build_categories(self, parent):
        card = Card(parent, "Categories")
        card.pack(fill="x", pady=(0, 14))
        help_icon(card.title.master, "Screen time is split into Productive / Neutral / Distracting (plus any of "
                                     "your own, each with a colour). Modes that block \"Distracting\" follow these. "
                                     "Set an app's or site's category on Screen Time > Apps / Websites.").pack(
            side="left", padx=8)
        self.cat_row = ctk.CTkFrame(card.body, fg_color="transparent")
        self.cat_row.pack(anchor="w", pady=(4, 4))

    def _load_categories(self):
        from gui import categories
        for w in self.cat_row.winfo_children():
            w.destroy()
        for c in categories.load(self.db):
            chip = ctk.CTkFrame(self.cat_row, fg_color="transparent")
            chip.pack(side="left", padx=(0, 14))
            ctk.CTkFrame(chip, width=12, height=12, corner_radius=3, fg_color=c["color"]).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(chip, text=c["name"], font=theme.body(12)).pack(side="left")
        ctk.CTkButton(self.cat_row, text="Manage categories…", width=170, **theme.OUTLINE,
                      command=self._manage_categories).pack(side="left", padx=(4, 0))

    def _manage_categories(self):
        from gui.categories import CategoryEditor
        once("categories", lambda: CategoryEditor(self, self.db, self._load_categories, self.app.guard))

    # ---------- appearance / goal / reset (one card) ----------

    def _build_appearance(self, parent):
        card = Card(parent, "Appearance")
        card.pack(fill="x", pady=(0, 14))
        box = card.body
        line = self._row(box, "Theme", pady=(6, 10))
        self.appearance = Segmented(line, values=list(theme.THEMES), command=self._theme_changed)
        self.appearance.pack(side="left")
        self.appearance.set(next(k for k, v in theme.THEMES.items() if v == theme.THEME))
        line = self._row(box, "Accent colour")
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
        self._show_accent()

        line = self._row(box, "Interface size")
        self.size = ctk.CTkOptionMenu(line, width=90, values=list(SIZES), command=self._size_changed)
        self.size.pack(side="left")
        chosen = self.db.get_setting(theme.SIZE_KEY, "auto")
        self.size.set(next((k for k, v in SIZES.items() if v == chosen), "Auto"))
        ctk.CTkLabel(line, text="how big everything is drawn", text_color=MUTED,
                     font=theme.body(12)).pack(side="left", padx=10)
        help_icon(line, "Auto fits the pages to your screen. Everything is drawn at this size from the next "
                        "start - changing it while the app runs would mean laying out every page again, which "
                        "takes seconds.").pack(side="left")
        self.size_line = ctk.CTkFrame(box, fg_color="transparent")
        ctk.CTkLabel(self.size_line, text="Restart Lockdown to use the new size.",
                     text_color=theme.WARNING).pack(side="left")
        ctk.CTkButton(self.size_line, text="Restart now", width=110, command=self.app.restart).pack(
            side="left", padx=10)

        line = self._row(box, "Daily goal")
        self.goal = ctk.CTkOptionMenu(line, width=90, values=GOAL_OPTIONS, command=self._goal_changed)
        self.goal.pack(side="left")
        hours = self.db.get_setting(GOAL_KEY, DEFAULT_GOAL_HOURS)
        self.goal.set("Off" if hours == "0" else f"{hours} h")
        ctk.CTkLabel(line, text="drawn as the dashed line on every chart", text_color=MUTED,
                     font=theme.body(12)).pack(side="left", padx=10)
        help_icon(line, "Not part of blocking, so it stays editable even while Anti-Bypass is locked.").pack(
            side="left")

        self._subhead(box, "When limits reset", "Time limits and opening limits start over at this time every day "
                                                "(weekly limits on Monday, monthly ones on the 1st, at the same "
                                                "time). Screen-time stats keep normal days.")
        line = self._row(box, "Reset at", pady=(0, 4))
        self.reset_entry = ctk.CTkEntry(line, width=70)
        self.reset_entry.pack(side="left")
        self.reset_btn = ConfirmButton(line, self._change_reset, text="Change", confirm_text="Confirm change",
                                       width=130)
        self.reset_btn.pack(side="left", padx=8)
        self.reset_info = ctk.CTkLabel(line, text="", text_color=MUTED, font=theme.body(12))
        self.reset_info.pack(side="left")
        self.reset_note = ctk.CTkLabel(box, text="", text_color=MUTED, wraplength=560, justify="left", anchor="w")
        self.reset_error = ctk.CTkLabel(box, text="", text_color=ERROR, anchor="w")   # packed while it says something

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
            self.restart_line.pack(anchor="w", pady=(0, 10))
        else:
            self.restart_line.pack_forget()

    def _size_changed(self, label: str):
        self.db.set_setting(theme.SIZE_KEY, SIZES[label])
        self.size_line.pack(anchor="w", padx=(LABEL_W + 12, 0), pady=(0, 8))

    def _goal_changed(self, value: str):
        self.db.set_setting(GOAL_KEY, "0" if value == "Off" else value.split()[0])

    def _change_reset(self):
        now = now_from_db(self.db)
        clock = self.db.limit_clock()
        text = self.reset_entry.get().strip()
        saved = self.db.get_setting(RESET_KEY)
        try:
            if text == f"{clock.time:%H:%M}":
                raise ValueError("That's already the reset time.")
            value = change_reset(saved, text, now)
        except ValueError as e:
            self.reset_error.configure(text=str(e))
            self.reset_error.pack(anchor="w", pady=(4, 0))
            return

        def save(new_value=value):
            self.db.set_setting(RESET_KEY, new_value)
            self.load()

        if not reset_is_looser(saved, text, now):
            save()
            return
        # an earlier time means a limit day ends sooner than it would have: start it now (Anti-Bypass first,
        # because the limits start over at once) or let it take over when the day you are in ends (free)
        end = clock.day(now)[1]
        now_value = apply_reset_now(saved, text, now)
        once("reset", lambda: ConfirmDialog(
            self.app, f"Move the reset back to {text}",
            f"The limit day you are in runs until {short_when(end)}.\n\n"
            f"Start now: it ends at once and today's limits start over - Anti-Bypass asks first.\n"
            f"From {short_when(end)}: {text} takes over when it ends, and nothing starts over.",
            on_yes=lambda: self.app.guard([f"Start a fresh limit day now ({text} reset)"],
                                          lambda: save(now_value), self.load),
            yes_text="Start now",
            alt_text=f"From {short_when(end)}", on_alt=lambda: save(value), on_no=self.load))

    # ---------- emergency unlock ----------

    def _build_emergency(self, parent):
        card = Card(parent, "Emergency unlock", accent=theme.WARNING)
        card.pack(fill="x", pady=(0, 14))
        head = card.title.master
        ctk.CTkLabel(head, text="", image=theme.icon("shield-check", MUTED, 13), width=13).pack(side="left", padx=(8, 0))
        help_icon(head, "The \"Emergency unlock\" button (Blocking > Overview) unblocks the sites / apps you pick "
                        "for a while. Unlocking several at once counts as one use. With more than one kind (e.g. 3 a "
                        "week for 5 min and 1 a month for 60 min) you pick which kind to use; each counts its own "
                        "uses.").pack(side="left", padx=8)
        box = card.body
        self.em_enabled = ctk.CTkSwitch(box, text="Allow emergency unlocks",
                                        command=lambda: self._save_emergency(enabled=bool(self.em_enabled.get())))
        self.em_enabled.pack(anchor="w", pady=(6, 10))
        self.em_rows = ctk.CTkFrame(box, fg_color="transparent")   # one box per kind, filled by load()
        self.em_rows.pack(fill="x")
        self.em_draft = ctk.CTkFrame(box, fg_color="transparent")  # "+ Add another kind": packed while it is open
        self.em_add = ctk.CTkButton(box, text="+ Add another kind", width=160, **theme.OUTLINE,
                                    command=self._open_draft)
        self.em_note = ctk.CTkLabel(box, text="Fewer, shorter, per day to per week or month, or removing a kind "
                                              "saves at once. More, longer, any other period, a new kind or "
                                              "turning it on needs the challenge.",
                                    text_color=MUTED, font=theme.body(11), anchor="w", justify="left",
                                    wraplength=RIGHT_W - 40)
        self.em_note.pack(anchor="w", pady=(4, 0))
        self._draft: dict | None = None

    def _kind_line(self, parent, kind: dict, on_change) -> ctk.CTkFrame:
        """[N ▾] [per week ▾] · [M min ▾] for one kind; on_change(field, value) after each pick."""
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.pack(fill="x")
        uses = ctk.CTkOptionMenu(line, width=58, values=[str(n) for n in range(1, emergency.MAX_USES + 1)],
                                 command=lambda v: on_change("uses", int(v)))
        uses.set(str(kind["uses"]))
        uses.pack(side="left")
        per = ctk.CTkOptionMenu(line, width=108, values=list(PER_LABELS),
                                command=lambda v: on_change("per", PER_LABELS[v]))
        per.set(next(k for k, v in PER_LABELS.items() if v == kind["per"]))
        per.pack(side="left", padx=6)
        ctk.CTkLabel(line, text="·", text_color=MUTED).pack(side="left", padx=(0, 6))
        minutes = ctk.CTkOptionMenu(line, width=84, values=[f"{m} min" for m in sorted(
            {*emergency.MINUTE_OPTIONS, kind["minutes"]})], command=lambda v: on_change("minutes", int(v.split()[0])))
        minutes.set(f"{kind['minutes']} min")
        minutes.pack(side="left")
        return line

    def _load_emergency(self, now):
        self.em_enabled.select() if emergency.get(self.db, "emergency.enabled") == "1" else self.em_enabled.deselect()
        for w in self.em_rows.winfo_children():
            w.destroy()
        kinds = emergency.left_by_tier(self.db, now)
        if not kinds:
            ctk.CTkLabel(self.em_rows, text="No kinds - add one.", text_color=MUTED).pack(anchor="w", pady=(0, 8))
        for kind, left, allowed, reset in kinds:
            # one box per kind: its choices (they save at once), x to remove it, and the meter - one yellow bar per
            # unlock left, grey for used ones, "2 of 3 left this week"
            row = ctk.CTkFrame(self.em_rows, fg_color=theme.BG, border_width=1, border_color=theme.BORDER,
                               corner_radius=3)
            row.pack(fill="x", pady=(0, 8))
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=10, pady=8)
            line = self._kind_line(inner, kind, lambda field, value, k=kind["id"]: self._edit_kind(k, field, value))
            ConfirmButton(line, lambda k=kind["id"]: self._remove_kind(k), text="×", confirm_text="Remove?",
                          width=30, height=28).pack(side="right")
            meter = ctk.CTkFrame(inner, fg_color="transparent")
            meter.pack(fill="x", pady=(6, 0))
            bars = ctk.CTkFrame(meter, fg_color="transparent")
            bars.pack(side="left", padx=(2, 8))
            for i in range(allowed):
                ctk.CTkFrame(bars, width=8, height=6, corner_radius=1,
                             fg_color=theme.WARNING if i < left else theme.BORDER).pack(side="left", padx=(0, 3))
            ctk.CTkLabel(meter, text=f"{left} of {allowed} left {emergency.PERIODS[kind['per']]} · resets "
                                     f"{short_when(reset)}", text_color=MUTED, font=theme.body(11), height=14,
                         anchor="w", justify="left", wraplength=RIGHT_W - 80 - allowed * 11).pack(side="left")
        if self._draft is None and len(kinds) < emergency.MAX_TIERS:
            self.em_add.pack(anchor="w", pady=(0, 4), before=self.em_note)
        else:
            self.em_add.pack_forget()

    def _open_draft(self):
        """A new kind is chosen first and saved once with Add - one challenge, not one for adding it and more for
        raising it afterwards."""
        self._draft = dict(NEW_KIND)
        for w in self.em_draft.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.em_draft, text="New kind", font=theme.semi(12)).pack(anchor="w", pady=(0, 4))
        self._kind_line(self.em_draft, self._draft, lambda field, value: self._draft.__setitem__(field, value))
        buttons = ctk.CTkFrame(self.em_draft, fg_color="transparent")
        buttons.pack(anchor="w", pady=(8, 0))
        ctk.CTkButton(buttons, text="Add", width=80, command=self._add_kind).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Cancel", width=80, **theme.OUTLINE, command=self._close_draft).pack(side="left")
        self.em_add.pack_forget()
        self.em_draft.pack(fill="x", pady=(0, 8), before=self.em_note)

    def _hide_draft(self):
        self._draft = None
        self.em_draft.pack_forget()

    def _close_draft(self):
        self._hide_draft()
        self.load()

    def _add_kind(self):
        kinds = emergency.tiers(self.db)
        if self._draft is None or len(kinds) >= emergency.MAX_TIERS:
            self._close_draft()
            return
        self._save_emergency(tiers=kinds + [{"id": emergency.new_id(), **self._draft}], done=self._hide_draft)

    def _edit_kind(self, kind_id: str, field: str, value):
        self._save_emergency(tiers=[{**t, field: value} if t["id"] == kind_id else t for t in emergency.tiers(self.db)])

    def _remove_kind(self, kind_id: str):
        self._save_emergency(tiers=[t for t in emergency.tiers(self.db) if t["id"] != kind_id])

    def _save_emergency(self, enabled: bool | None = None, tiers: list[dict] | None = None, done=None):
        """The switch and / or the kinds: through Anti-Bypass first when that gives more or longer emergency
        unlocks (antibypass.emergency_looser), at once otherwise. Cancelling the challenge puts the card back."""
        old = emergency.config(self.db)
        new = {"enabled": old["enabled"] if enabled is None else enabled,
               "tiers": old["tiers"] if tiers is None else emergency.clean(tiers)}

        def save():
            self.db.set_setting("emergency.enabled", "1" if new["enabled"] else "0")
            if tiers is not None:
                emergency.save_tiers(self.db, new["tiers"])
            if done:
                done()
            self.load()
            if "Blocking" in self.app.pages:
                self.app.pages["Blocking"].refresh()   # shows / hides the button
        if antibypass.emergency_looser(old, new):   # more / longer emergency unlocks: Anti-Bypass first
            self.app.guard(["Allow more or longer emergency unlocks"], save, self.load)
        else:
            save()

    # ---------- backup / export ----------

    def _build_backup(self, parent):
        card = Card(parent, "Backup & export")
        card.pack(fill="x", pady=(0, 14))
        help_icon(card.title.master, "A backup has everything you set up: blocked sites and apps with their rules, "
                                     "groups, categories, modes, reminders and settings. Importing one replaces "
                                     "yours (needs the Anti-Bypass challenge if it's on).").pack(side="left", padx=8)
        box = card.body
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", pady=(4, 8))
        ctk.CTkButton(line, text="Export settings...", width=150, **theme.OUTLINE, command=self._export).pack(
            side="left")
        ctk.CTkButton(line, text="Import settings...", width=150, **theme.OUTLINE, command=self._import).pack(
            side="left", padx=8)
        ctk.CTkButton(box, text="Screen time (CSV)...", width=150, **theme.OUTLINE,
                      command=self._export_screen_time).pack(anchor="w", pady=(0, 8))
        ctk.CTkLabel(box, text="Importing settings can loosen blocks, so it asks for the challenge first.",
                     text_color=MUTED, font=theme.body(12), anchor="w", justify="left",
                     wraplength=RIGHT_W - 40).pack(anchor="w")
        self.backup_info = ctk.CTkLabel(box, text="", anchor="w", justify="left", wraplength=RIGHT_W - 40)

    def _backup_msg(self, text: str, color):
        self.backup_info.configure(text=text, text_color=color)
        self.backup_info.pack(anchor="w", pady=(4, 0))

    def _export(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self, title="Export Lockdown settings", defaultextension=".json",
                                            filetypes=[("Lockdown backup", "*.json")],
                                            initialfile=f"lockdown-backup-{now_from_db(self.db):%Y-%m-%d}.json")
        if path:
            backup.save(self.db, path)
            self._backup_msg(f"Saved to {path}", theme.ALLOWED)

    def _import(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self, title="Import Lockdown settings",
                                          filetypes=[("Lockdown backup", "*.json")])
        if not path:
            return
        try:
            data = backup.load(path)
        except ValueError as e:
            self._backup_msg(str(e), theme.DANGER)
            return

        def restore():
            backup.restore(self.db, data)
            self.app.draft.discard()   # (reloads the blocks everywhere)
            self.load()
            self._backup_msg(f"Imported {len(data['items'])} blocked sites / apps and your settings. "
                             "Restart Lockdown to see a different theme or colour.", theme.ALLOWED)
        from pathlib import Path
        # show a review of what changes first, then the Anti-Bypass challenge, then apply
        once("confirm", lambda: ConfirmDialog(
            self.app, "Import these settings?",
            f"Importing {Path(path).name} replaces your current setup with the backup. What changes:",
            on_yes=lambda: self.app.guard(
                [f"Import settings from {Path(path).name} (replaces your blocks and settings)"], restore),
            yes_text="Import", lines=backup.diff(self.db, data)))

    def _export_screen_time(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self, title="Export screen time", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], initialfile="screen-time.csv")
        if path:
            rows = backup.screen_time_csv(self.db, path)
            self._backup_msg(f"{rows} rows saved to {path}", theme.ALLOWED)

    # ---------- the new window (preview) ----------

    def _build_engine(self, parent):
        card = Card(parent, "New window (preview)")
        card.pack(fill="x", pady=(0, 14))
        box = card.body
        self.engine_switch = ctk.CTkSwitch(box, text="Use the new window (preview)", command=self._engine_changed)
        self.engine_switch.pack(anchor="w", pady=(6, 6))
        if engine_of(self.db.get_setting(ENGINE_KEY)) == "web":
            self.engine_switch.select()
        ctk.CTkLabel(box, text="Lockdown opens in the new window the next time it starts. Switch it off to come "
                               "back to this one.", text_color=MUTED, font=theme.body(12), anchor="w", justify="left",
                     wraplength=RIGHT_W - 40).pack(anchor="w")
        self.engine_line = ctk.CTkFrame(box, fg_color="transparent")
        ctk.CTkLabel(self.engine_line, text="Restart Lockdown to switch now.", text_color=theme.WARNING).pack(
            side="left")
        ctk.CTkButton(self.engine_line, text="Restart now", width=110, command=self.app.restart).pack(
            side="left", padx=10)

    def _engine_changed(self):
        """The switch writes ui.engine ("web" / "legacy") and offers a restart; nothing else changes."""
        self.db.set_setting(ENGINE_KEY, "web" if self.engine_switch.get() else "legacy")
        self.engine_line.pack(anchor="w", pady=(6, 0))

    # ---------- load ----------

    def on_show(self):
        self.locked.update()

    def load(self):
        self.locked.update()
        self._load_categories()
        now = now_from_db(self.db)
        clock = self.db.limit_clock()
        self.reset_entry.delete(0, "end")
        self.reset_entry.insert(0, f"{clock.time:%H:%M}")
        _start, end = clock.day(now)
        self.reset_info.configure(text=f"ends {short_when(end)}")
        if clock.carry_until and now < clock.carry_until:
            self.reset_note.configure(text=f"It runs to {short_when(clock.carry_until)} because you changed the reset "
                                           f"time - a change never ends the day you are in early, and never stretches "
                                           f"it twice. {f'{clock.time:%H:%M}'} applies from then on.")
            self.reset_note.pack(anchor="w", pady=(6, 0))
        else:
            self.reset_note.pack_forget()
        self.reset_error.pack_forget()

        self._load_emergency(now)
