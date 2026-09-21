"""Anti-Bypass page (the challenges, what they protect) and the challenge window shown before a loosening change."""
import tkinter as tk
from datetime import datetime

import customtkinter as ctk

import antibypass
import reminders   # (parse_minutes / minutes_text: the same "45s", "10", "1h30" boxes)
from gui import theme
from gui.components import Card, Segmented, help_icon, page_head
from gui.word_grid import WordGrid, block_paste
from gui.rule_editors import WindowRow
from gui.widgets import ConfirmDialog, modal, once
from rules import days_text, make_schedule, load_schedule
from trusted_time import now_from_db

MUTED = theme.MUTED
LIVE_MS = 5_000   # re-check the lock state on a timer so the banner colour follows the clock (allowed-hours edges)
PROTECTS = ["Removing a blocked site / app / group, or taking sites or members out of it",
            "Weaker rules: higher or no limits, other blocked hours, a shorter temporary block, gentler app blocking",
            "Switching a protection list off, or allowing a site it blocks",
            "Turning SafeSearch or the blocked-words check off, removing your words, adding exceptions",
            "Emergency unlock: turning it on, longer unlocks, more uses",
            "Quitting Lockdown from the tray",
            "Weakening Anti-Bypass itself (these settings)"]


def hours_text(cfg: dict) -> str:
    return ", ".join(f"{days_text(w['days'])} {w['start']}-{w['end']}" for w in cfg["windows"])


def describe(cfg: dict) -> str:
    parts = []
    if cfg["phrase"]:
        which = "your own phrase" if cfg.get("custom_phrase") else (
            f"a {cfg['length']}-character phrase" + (" (with numbers & capitals)" if cfg.get("complex") else ""))
        parts.append(f"typing {which}" + (" in a 3×3 grid" if cfg["grid"] else ""))
    if cfg["hours"]:
        parts.append(f"only {hours_text(cfg)}")
    return "Loosening a block needs: " + " · ".join(parts) if parts else "Off - loosening a block needs nothing."


def when(dt: datetime, now: datetime) -> str:
    return f"{dt:%H:%M}" if dt.date() == now.date() else f"{dt:%a %d %b %H:%M}"


class ChallengeWindow(ctk.CTkToplevel):
    """Shown before a loosening change: outside the allowed hours it just says when; otherwise the phrase to type.
    on_pass() once it's typed correctly (loosening is then unlocked for a few minutes), on_cancel() otherwise."""

    def __init__(self, app, changes: list[str], on_pass, on_cancel):
        super().__init__(app)
        self.app, self.db, self.on_pass, self.on_cancel = app, app.db, on_pass, on_cancel
        self.title("Anti-Bypass")
        self.resizable(False, False)
        self.configure(fg_color=theme.BG)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        now = now_from_db(self.db)
        cfg = antibypass.settings(self.db)
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=24, pady=20)
        title = ctk.CTkLabel(box, text="This loosens your blocks", font=theme.card_title())
        title.pack(anchor="w")
        shown = changes[:5] + ([f"and {len(changes) - 5} more"] if len(changes) > 5 else [])
        items = ctk.CTkFrame(box, fg_color="transparent")
        items.pack(anchor="w", fill="x", pady=(5, 12))
        for c in shown:
            row = ctk.CTkFrame(items, fg_color="transparent")
            row.pack(anchor="w", fill="x")
            ctk.CTkLabel(row, text="→", text_color=theme.ACCENT, font=theme.semi(13), width=16, anchor="w").pack(
                side="left", anchor="n")
            ctk.CTkLabel(row, text=c, text_color=MUTED, justify="left", wraplength=496, anchor="w").pack(side="left")
        buttons = ctk.CTkFrame(box, fg_color="transparent")
        self.phrase = None
        waiting = antibypass.waiting_until(cfg, now)
        if waiting:
            # the challenge is already passed and the wait is running: show it, never the phrase again (typing
            # it a second time would start the wait from the beginning)
            title.configure(text="  Waiting", image=theme.icon("shield-check", theme.PENDING, 19), compound="left")
            minutes = max(1, int((waiting - now).total_seconds() // 60) + 1)
            self._note(box, theme.PENDING,
                       f"You passed the challenge. Changes like this are possible from {waiting:%H:%M} "
                       f"- about {minutes} min from now - and then for {antibypass.UNLOCK_MIN} minutes.")
            buttons.pack(fill="x", pady=(16, 0))
            ctk.CTkButton(buttons, text="OK", width=90, command=self._cancel).pack(side="right")
        elif antibypass.status(cfg, now) == "closed":
            # design 3l: "Closed right now" + a red-tinted note with the hours and the next chance
            title.configure(text="  Closed right now", image=theme.icon("shield-check", theme.DANGER, 19),
                            compound="left")
            nxt = antibypass.next_hours(cfg, now)
            self._note(box, theme.DANGER, f"Changes like this are only possible {hours_text(cfg)}."
                       + (f"\nNext chance: {when(nxt, now)}." if nxt else ""))
            buttons.pack(fill="x", pady=(16, 0))
            ctk.CTkButton(buttons, text="OK", width=90, command=self._cancel).pack(side="right")
        else:
            self.title("Anti-Bypass · challenge")
            self.phrase = antibypass.phrase_for(cfg)
            self.grid = None
            self.bar = None
            if cfg["grid"]:
                ctk.CTkLabel(box, text=f"Type each word into the highlighted box (click it first, no pasting). "
                                       f"Passing unlocks changes like these for {antibypass.UNLOCK_MIN} minutes.",
                             justify="left", wraplength=520, text_color=MUTED, font=theme.body(12)).pack(
                    anchor="w", pady=(0, 10))
                self.grid = WordGrid(box, self.phrase.split(), self._pass, self._grid_status)   # all typed: go on
                self.grid.pack(anchor="w", fill="x")
            else:
                ctk.CTkLabel(box, text=f"Type this phrase to continue (no pasting). Passing unlocks changes like "
                                       f"these for {antibypass.UNLOCK_MIN} minutes.", justify="left", wraplength=520,
                             text_color=MUTED, font=theme.body(12)).pack(anchor="w")
                shown_phrase = ctk.CTkFrame(box, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER,
                                            corner_radius=3)
                shown_phrase.pack(anchor="w", fill="x", pady=10)
                ctk.CTkLabel(shown_phrase, text=self.phrase, font=ctk.CTkFont(theme.DISPLAY, 15), wraplength=490,
                             justify="left", anchor="w").pack(anchor="w", padx=12, pady=8)
                self.entry = ctk.CTkEntry(box, font=ctk.CTkFont(theme.DISPLAY, 15), height=36, corner_radius=2,
                                          border_color=theme.ACCENT)
                self.entry.pack(fill="x")
                block_paste(self.entry)   # the phrase must be typed
                self.entry._entry.bind("<KeyRelease>", lambda e: self._typed())
            if self.grid:   # the count sits at the right of the "Word 1 of 12" row (3l)
                self.progress = ctk.CTkLabel(self.grid.head, text=f"0 / {len(self.phrase)}", text_color=MUTED,
                                             height=18, anchor="e", font=theme.body(12))
                self.progress.pack(side="right")
            else:           # a thin progress bar with the count at its right (3l)
                foot = ctk.CTkFrame(box, fg_color="transparent")
                foot.pack(fill="x", pady=(10, 0))
                self.bar = ctk.CTkProgressBar(foot, height=4, corner_radius=1, fg_color=theme.SURFACE2,
                                              progress_color=theme.ACCENT)
                self.bar.set(0)
                self.bar.pack(side="left", fill="x", expand=True, padx=(0, 10))
                self.progress = ctk.CTkLabel(foot, text=f"0 / {len(self.phrase)}", text_color=MUTED, height=18,
                                             anchor="e", font=theme.body(12))
                self.progress.pack(side="left")
            buttons.pack(fill="x", pady=(12, 0))
            # disabled Continue: a faded accent with pale text (3l), not a full-strength orange that looks clickable
            self.go = ctk.CTkButton(buttons, text="Continue", width=110, command=self._pass,
                                    text_color_disabled="#F3D9CF")
            self._enable_go(False)
            self.go.pack(side="right")
            ctk.CTkButton(buttons, text="Cancel", width=90, **theme.OUTLINE, command=self._cancel).pack(
                side="right", padx=8)
            if not self.grid:
                self.after(100, self.entry.focus_force)
        modal(self, app)   # (no transient when shown alone, e.g. by the uninstaller)

    @staticmethod
    def _note(box, color, text: str):
        """The tinted note under the title (design 3l), with a coloured edge down its left side."""
        note = ctk.CTkFrame(box, corner_radius=3, border_width=1, border_color=color,
                            fg_color=(theme._mix(color[0], theme.BG[0], 0.9),
                                      theme._mix(color[1], theme.BG[1], 0.9)))
        note.pack(fill="x")
        tk.Frame(note, width=3, bg=theme.pick(color), bd=0, highlightthickness=0).pack(side="left", fill="y",
                                                                                       padx=(1, 0), pady=1)
        ctk.CTkLabel(note, text=text, justify="left", wraplength=470, font=theme.body(12)).pack(
            anchor="w", padx=12, pady=10)

    def _enable_go(self, enabled: bool):
        faded = (theme._mix(theme.ACCENT[0], theme.BG[0], 0.6), theme._mix(theme.ACCENT[1], theme.BG[1], 0.6))
        self.go.configure(state="normal" if enabled else "disabled", fg_color=theme.ACCENT if enabled else faded)

    def _grid_status(self, text: str, error: bool):
        if hasattr(self, "progress"):
            self.progress.configure(text=text, text_color=theme.DANGER if error else MUTED)
            if self.grid and self.grid.done:
                self._enable_go(True)

    def _typed(self):
        typed = self.entry.get()
        correct = next((i for i, (a, b) in enumerate(zip(typed, self.phrase)) if a != b), min(len(typed), len(self.phrase)))
        if correct < len(typed):
            self.progress.configure(text=f"Mistake at character {correct + 1} - fix it to go on", text_color=theme.DANGER)
        else:
            self.progress.configure(text=f"{correct} / {len(self.phrase)}", text_color=MUTED)
        self.bar.set(correct / max(1, len(self.phrase)))
        self._enable_go(typed == self.phrase)

    def _pass(self):
        if not (self.grid.done if self.grid else self.entry.get() == self.phrase):
            return
        waiting = antibypass.unlock(self.db, now_from_db(self.db))
        self.destroy()
        if waiting:   # a cooling-off period is set: come back when it ends and do it then
            self.on_cancel()
            once("waited", lambda: ConfirmDialog(
                self.app, "Passed - now the wait",
                f"You can loosen a block from {waiting:%H:%M}, for {antibypass.UNLOCK_MIN} minutes.\n\n"
                f"Nothing has changed yet - come back then and make the change.",
                on_yes=lambda: None, yes_text="OK"))
        else:
            self.on_pass()
        if hasattr(self.app, "refresh_antibypass"):
            self.app.refresh_antibypass()

    def _cancel(self):
        self.destroy()
        self.on_cancel()


class AntiBypassPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        page_head(self, "Anti-Bypass").pack(anchor="w", padx=30, pady=(16, 8))
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.banner = ctk.CTkFrame(body, corner_radius=4, border_width=1)
        self.banner.pack(fill="x", pady=(0, 12))
        strip = ctk.CTkFrame(self.banner, fg_color="transparent")
        strip.pack(fill="x", padx=16, pady=13)
        self.banner_icon = ctk.CTkLabel(strip, text="", width=22)
        self.banner_icon.pack(side="left", padx=(0, 12), anchor="n")
        texts = ctk.CTkFrame(strip, fg_color="transparent")
        texts.pack(side="left", fill="x", expand=True)
        self.banner_title = ctk.CTkLabel(texts, text="", font=theme.semi(14), anchor="w", justify="left",
                                         wraplength=740)
        self.banner_title.pack(anchor="w")
        self.summary = ctk.CTkLabel(texts, text="", text_color=MUTED, anchor="w", justify="left", wraplength=740)
        self.summary.pack(anchor="w", pady=(3, 0))
        self.lock_btn = ctk.CTkButton(strip, text="Lock now", width=100, **theme.OUTLINE, command=self._lock)
        self.unlock_btn = ctk.CTkButton(strip, text="Unlock to edit", width=130, command=self._unlock,
                                        fg_color="transparent", border_width=1, border_color=theme.DANGER,
                                        text_color=theme.DANGER, hover_color=("#FBE5E3", "#2A1A19"))   # red outline (3d)
        self.unlock_bar = ctk.CTkProgressBar(self.banner, height=3, corner_radius=0, progress_color=theme.SUCCESS)
        self._unlock_job = None

        # two columns (design 3d): challenges + "keeping Lockdown running" on the left, "what needs the
        # challenge" on the right - instead of one long stack
        cols = ctk.CTkFrame(body, fg_color="transparent")
        cols.pack(fill="both", expand=True)
        cols.grid_columnconfigure(0, weight=5, uniform="ab")
        cols.grid_columnconfigure(1, weight=4, uniform="ab")
        cols.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(cols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="new", padx=(0, 7))
        right = ctk.CTkFrame(cols, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))   # stretches to the left column's height
        challenges = Card(left, "Challenges")
        challenges.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(challenges.body, text="Making blocks stricter is always instant. Anything that loosens them "
                                           "(including these settings) needs the challenges you turn on here.",
                     text_color=MUTED, wraplength=520, justify="left").pack(anchor="w", pady=(0, 8))
        self.phrase_sw = ctk.CTkSwitch(challenges.body, text="Type a random phrase (no pasting)", font=theme.semi(13),
                                       command=self._apply)
        self.phrase_sw.pack(anchor="w")
        line = ctk.CTkFrame(challenges.body, fg_color="transparent")
        line.pack(anchor="w", padx=(46, 0), pady=(4, 12))
        ctk.CTkLabel(line, text="Length").pack(side="left", padx=(0, 8))
        self.length = Segmented(line, values=list(antibypass.LENGTHS), command=lambda v: self._apply())
        self.length.pack(side="left")
        self.length_note = ctk.CTkLabel(line, text="", text_color=MUTED)
        self.length_note.pack(side="left", padx=10)
        self.complex_box = ctk.CTkCheckBox(challenges.body, text="Include numbers and CAPITAL letters (harder to type "
                                           "quickly)", command=self._apply)
        self.complex_box.pack(anchor="w", padx=(46, 0), pady=(0, 8))
        self.grid_box = ctk.CTkCheckBox(challenges.body, text="3×3 grid - one word at a time into a random box",
                                        command=self._apply)
        self.grid_box.pack(anchor="w", padx=(46, 0), pady=(0, 12))
        line.pack_configure(pady=(4, 6))
        custom = ctk.CTkFrame(challenges.body, fg_color="transparent")
        custom.pack(anchor="w", fill="x", padx=(46, 0), pady=(0, 12))
        ctk.CTkLabel(custom, text="Your own phrase").pack(side="left", padx=(0, 8))
        self.custom_entry = ctk.CTkEntry(custom, width=160, placeholder_text="leave empty for a random one")
        self.custom_entry.pack(side="left", fill="x", expand=True)   # takes what the column leaves over
        ctk.CTkButton(custom, text="Save phrase", width=100, **theme.OUTLINE, command=self._apply).pack(side="left",
                                                                                                       padx=8)
        help_icon(custom, "Set a phrase only you know (e.g. a long sentence). You still type it exactly each time - "
                          "no pasting. With the 3×3 grid it's split into words by the spaces. While a custom phrase "
                          "is set, the length and the numbers/capitals option don't apply.").pack(side="left", padx=4)
        wait_line = ctk.CTkFrame(challenges.body, fg_color="transparent")
        wait_line.pack(anchor="w", pady=(0, 10))
        ctk.CTkLabel(wait_line, text="Then wait").pack(side="left", padx=(0, 8))
        self.wait_entry = ctk.CTkEntry(wait_line, width=72, justify="center")
        self.wait_entry.pack(side="left")
        self.wait_entry.bind("<Return>", lambda e: self._apply())
        self.wait_entry.bind("<FocusOut>", lambda e: self._apply())
        ctk.CTkLabel(wait_line, text="before it actually unlocks", text_color=MUTED).pack(side="left", padx=8)
        help_icon(wait_line, "\"Off\", or a time like 10 or 1h. Passing the challenge starts the wait instead of "
                             "unlocking at once, and you come back when it ends - an easy phrase plus a long wait "
                             "stops more than a hard phrase does.\nThe wait is kept in the database, so closing "
                             "Lockdown or restarting the PC doesn't skip it.").pack(side="left", padx=4)
        hours_line = ctk.CTkFrame(challenges.body, fg_color="transparent")
        hours_line.pack(anchor="w")
        self.hours_sw = ctk.CTkSwitch(hours_line, text="Only during these hours", font=theme.semi(13),
                                      command=self._apply)
        self.hours_sw.pack(side="left")
        help_icon(hours_line, "Outside these times nothing can be loosened at all. An end before the start means "
                              "overnight.").pack(side="left", padx=4)
        self.rows_box = ctk.CTkFrame(challenges.body, fg_color="transparent")
        self.rows_box.pack(anchor="w", padx=(46, 0), pady=4)
        line = ctk.CTkFrame(challenges.body, fg_color="transparent")
        line.pack(anchor="w", padx=(46, 0))
        ctk.CTkButton(line, text="+ Add time window", width=140, **theme.OUTLINE,
                      command=lambda: self._add_row([6], "18:00", "20:00")).pack(side="left")
        ctk.CTkButton(line, text="Save hours", width=110, command=self._apply).pack(side="left", padx=8)
        self.error = ctk.CTkLabel(challenges.body, text="", text_color=theme.DANGER, height=18)
        self.error.pack(anchor="w", padx=(46, 0))
        self.rows: list[WindowRow] = []

        protects = Card(right, "What needs the challenge")
        protects.pack(fill="both", expand=True, pady=(0, 12))   # fills the column; the note sits at its foot
        ctk.CTkLabel(protects.body, text="\n".join(f"•  {p}" for p in PROTECTS), justify="left", anchor="w",
                     wraplength=400).pack(anchor="w")
        note = ctk.CTkFrame(protects.body, fg_color=theme.BG, border_width=1, border_color=theme.BORDER,
                            corner_radius=3)   # the plate's tinted note at the bottom of the card
        note.pack(side="bottom", fill="x", pady=(10, 0))
        ctk.CTkLabel(note, text="Making blocks stricter is always instant. Not included: the emergency unlock (it has "
                                "its own limit) and changing when limits reset (it never shortens a limit day). "
                                "Stopping a locked mode always needs the phrase, even with no challenge turned on here.",
                     text_color=MUTED, wraplength=380, justify="left", anchor="w").pack(anchor="w", padx=12, pady=9)

        itself = Card(left, "Keeping Lockdown running")
        itself.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(itself.body, text="\n".join([
            "•  The tray app comes back within a minute if it's closed any other way than Exit (e.g. Task Manager).",
            "•  The service comes back within a minute if it's stopped.",
            "•  Changing the Windows clock does nothing; a new time zone counts only after 24 hours.",
            "•  Uninstalling Lockdown asks for the challenge too."]), justify="left", anchor="w",
            wraplength=520).pack(anchor="w")
        self._banner_sig = None
        self.refresh()
        self.after(LIVE_MS, self._live)

    def _add_row(self, days, start, end):
        row = WindowRow(self.rows_box, days, start, end,
                        on_remove=lambda r: (self.rows.remove(r), r.destroy(), self._update_removes()))
        row.pack(anchor="w", pady=2)
        self.rows.append(row)
        self._update_removes()

    def _update_removes(self):
        for row in self.rows:   # the last remaining window keeps no × (removing it would make the hours pointless)
            row.set_removable(len(self.rows) > 1)

    def on_show(self):
        self.refresh()

    def refresh(self, *_):
        cfg = antibypass.settings(self.db)
        now = now_from_db(self.db)
        self.phrase_sw.select() if cfg["phrase"] else self.phrase_sw.deselect()
        self.grid_box.select() if cfg["grid"] else self.grid_box.deselect()
        self.complex_box.select() if cfg.get("complex") else self.complex_box.deselect()
        self.length.set(next((k for k, v in antibypass.LENGTHS.items() if v == cfg["length"]), "Medium"))
        self.length_note.configure(text=f"{cfg['length']} characters")
        if self.custom_entry.get() != (cfg.get("custom_phrase") or ""):
            self.custom_entry.delete(0, "end")
            self.custom_entry.insert(0, cfg.get("custom_phrase") or "")
        self.hours_sw.select() if cfg["hours"] else self.hours_sw.deselect()
        if self.wait_entry.get() != reminders.minutes_text(cfg.get("wait_min") or 0):
            self.wait_entry.delete(0, "end")
            self.wait_entry.insert(0, reminders.minutes_text(cfg.get("wait_min") or 0))
        for row in self.rows:
            row.destroy()
        self.rows = []
        for w in cfg["windows"]:
            self._add_row(w["days"], w["start"], w["end"])
        self.error.configure(text="")
        self._set_banner()

    def _set_banner(self):
        """Paint the status banner (lock / unlock / off) for the time right now. Split out from refresh() so a light
        timer can update it when time crosses an allowed-hours boundary - without rebuilding the whole page."""
        cfg = antibypass.settings(self.db)
        now = now_from_db(self.db)
        status = antibypass.status(cfg, now)
        until = antibypass.unlocked_until(cfg, now)
        self._banner_sig = (antibypass.active(cfg), status, bool(until))
        self.lock_btn.pack_forget()
        self.unlock_btn.pack_forget()
        if not antibypass.active(cfg):
            self._banner(theme.MUTED, "Anti-Bypass is off",
                         "Turn on a challenge below - then anything that loosens a block will need it first.")
        elif status == "closed":
            nxt = antibypass.next_hours(cfg, now)
            self._banner(theme.DANGER, "Locked - outside the allowed hours",
                         describe(cfg) + (f"   ·   next chance {when(nxt, now)}" if nxt else ""))
        elif until:
            self._banner(theme.SUCCESS, "", "Changes that loosen your blocks are allowed until the timer runs out.")
            self.lock_btn.pack(side="right")
            self._tick_unlock()   # live mm:ss countdown + a draining bar
        elif status == "waiting":
            self._banner(theme.PENDING, "Passed - waiting", "The unlock opens when the wait ends.")
            self._tick_wait()
        elif status == "phrase":
            self._banner(theme.DANGER, "Locked", describe(cfg))
            self.unlock_btn.pack(side="right")
        else:   # only allowed hours, and they're now
            self._banner(theme.SUCCESS, "Unlocked - you're in the allowed hours", describe(cfg))

    def _live(self):
        """Re-paint the banner when the lock state changes with the clock (e.g. an allowed-hours window opens or
        closes), so its colour is right even if you never leave the tab. Cheap: only repaints on a real change."""
        if not self.winfo_exists():
            return
        cfg = antibypass.settings(self.db)
        now = now_from_db(self.db)
        sig = (antibypass.active(cfg), antibypass.status(cfg, now), bool(antibypass.unlocked_until(cfg, now)))
        if sig != getattr(self, "_banner_sig", None):
            self._set_banner()
        self.after(LIVE_MS, self._live)

    def _banner(self, color, title: str, sub: str):
        """Tint the status banner for the current lock state (red = locked, green = unlocked, grey = off)."""
        if self._unlock_job:
            self.after_cancel(self._unlock_job)
            self._unlock_job = None
        self.unlock_bar.pack_forget()
        tint = (theme._mix(color[0], theme.BG[0], 0.88), theme._mix(color[1], theme.BG[1], 0.86))
        self.banner.configure(fg_color=tint, border_color=color)
        self.banner_icon.configure(image=theme.icon("shield-check", color, 20), text="")
        self.banner_title.configure(text=title, text_color=theme.TEXT)
        self.summary.configure(text=sub, text_color=MUTED)

    def _tick_unlock(self):
        """While unlocked: show the minutes:seconds left and a bar that drains, then re-lock when it runs out."""
        if not self.winfo_exists():
            return
        now = now_from_db(self.db)
        until = antibypass.unlocked_until(antibypass.settings(self.db), now)
        if not until:
            self.refresh()
            return
        left = (until - now).total_seconds()
        m, s = divmod(max(0, int(left)), 60)
        self.banner_title.configure(text=f"Unlocked for {m}:{s:02d} - loosening changes are allowed")
        self.unlock_bar.pack(fill="x", side="bottom")
        self.unlock_bar.set(max(0.0, min(1.0, left / (antibypass.UNLOCK_MIN * 60))))
        self._unlock_job = self.after(1000, self._tick_unlock)

    def _tick_wait(self):
        """The cooling-off period between passing the challenge and being able to change anything."""
        if not self.winfo_exists():
            return
        now = now_from_db(self.db)
        cfg = antibypass.settings(self.db)
        start = antibypass.waiting_until(cfg, now)
        if not start:
            self._set_banner()
            return
        m, s = divmod(max(0, int((start - now).total_seconds())), 60)
        self.banner_title.configure(text=f"Passed - unlocks in {m}:{s:02d} (at {start:%H:%M})")
        total = max(1.0, (cfg.get("wait_min") or 0) * 60)
        self.unlock_bar.pack(fill="x", side="bottom")
        self.unlock_bar.set(max(0.0, min(1.0, 1 - (start - now).total_seconds() / total)))
        self._unlock_job = self.after(1000, self._tick_wait)

    def _read(self) -> dict:
        cfg = antibypass.settings(self.db)
        windows = [w for w in (r.value() for r in self.rows) if w[0]]
        if self.hours_sw.get() and not windows:
            raise ValueError("Turn on at least one day.")
        return {**cfg, "wait_min": reminders.parse_minutes(self.wait_entry.get(), least=1),
                "phrase": bool(self.phrase_sw.get()), "length": antibypass.LENGTHS[self.length.get()],
                "grid": bool(self.grid_box.get()), "complex": bool(self.complex_box.get()),
                "custom_phrase": " ".join(self.custom_entry.get().split()),
                "hours": bool(self.hours_sw.get()),
                "windows": load_schedule(make_schedule("allow", windows))["windows"] if windows else cfg["windows"]}

    def _apply(self):
        old = antibypass.settings(self.db)
        try:
            new = self._read()
        except ValueError as e:
            self.error.configure(text=str(e))
            return

        def save():
            antibypass.save(self.db, {**new, "unlocked_until": antibypass.settings(self.db)["unlocked_until"]})
            self.refresh()
        if antibypass.settings_looser(old, new):
            self.app.guard(["Weaken Anti-Bypass"], save, self.refresh)
        elif self._challenge_changed(old, new):
            # tightening the challenge is instant, but a phrase you can't reproduce (or hours you can't reach) would
            # lock you out of ever loosening a block - so confirm the new challenge first
            once("confirm", lambda: ConfirmDialog(
                self.app, "Change the Anti-Bypass challenge?",
                "This is what you'll need to loosen a block from now on. Make sure you can actually do it - "
                "if you can't, you won't be able to unlock anything.",
                on_yes=save, on_no=self.refresh, yes_text="Change it", lines=[describe(new)]))
        else:
            save()

    def _challenge_changed(self, old: dict, new: dict) -> bool:
        return any(old.get(k) != new.get(k) for k in ("phrase", "length", "grid", "complex", "custom_phrase",
                                                       "hours", "windows", "wait_min"))

    def _lock(self):
        antibypass.lock(self.db)
        self.refresh()

    def _unlock(self):
        self.app.guard([f"Allow loosening changes for {antibypass.UNLOCK_MIN} minutes"], self.refresh, self.refresh)
