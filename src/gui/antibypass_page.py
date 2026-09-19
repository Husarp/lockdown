"""Anti-Bypass page (the challenges, what they protect) and the challenge window shown before a loosening change."""
from datetime import datetime

import customtkinter as ctk

import antibypass
from gui import theme
from gui.components import Card, Segmented, help_icon, page_head
from gui.word_grid import WordGrid, block_paste
from gui.rule_editors import WindowRow
from rules import days_text, make_schedule, load_schedule
from trusted_time import now_from_db

MUTED = theme.MUTED
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
        parts.append(f"typing a {cfg['length']}-character phrase" + (" in a 3×3 grid" if cfg["grid"] else ""))
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
        ctk.CTkLabel(box, text="This loosens your blocks", font=theme.card_title()).pack(anchor="w")
        shown = changes[:5] + ([f"... and {len(changes) - 5} more"] if len(changes) > 5 else [])
        ctk.CTkLabel(box, text="\n".join(f"•  {c}" for c in shown), justify="left", text_color=MUTED,
                     wraplength=520).pack(anchor="w", pady=(4, 12))
        buttons = ctk.CTkFrame(box, fg_color="transparent")
        self.phrase = None
        if antibypass.status(cfg, now) == "closed":
            nxt = antibypass.next_hours(cfg, now)
            ctk.CTkLabel(box, text=f"Changes like this are only possible {hours_text(cfg)}."
                                   + (f"\nNext chance: {when(nxt, now)}." if nxt else ""),
                         justify="left", wraplength=520).pack(anchor="w")
            buttons.pack(fill="x", pady=(16, 0))
            ctk.CTkButton(buttons, text="OK", width=90, command=self._cancel).pack(side="right")
        else:
            self.phrase = antibypass.new_phrase(cfg["length"])
            self.grid = None
            if cfg["grid"]:
                ctk.CTkLabel(box, text=f"Type each word into the orange box (click it first; no pasting). It unlocks "
                                       f"changes like this for {antibypass.UNLOCK_MIN} minutes.", justify="left",
                             wraplength=520).pack(anchor="w", pady=(0, 8))
                self.grid = WordGrid(box, self.phrase.split(), self._pass, self._grid_status)   # all typed: go on
                self.grid.pack(anchor="w")
            else:
                ctk.CTkLabel(box, text=f"Type this phrase to continue (no pasting). It unlocks changes like this for "
                                       f"{antibypass.UNLOCK_MIN} minutes.", justify="left", wraplength=520).pack(anchor="w")
                ctk.CTkLabel(box, text=self.phrase, font=ctk.CTkFont("Consolas", 16), wraplength=520, justify="left",
                             fg_color=theme.SURFACE, corner_radius=6).pack(anchor="w", fill="x", pady=8, ipadx=10,
                                                                          ipady=8)
                self.entry = ctk.CTkEntry(box, font=ctk.CTkFont("Consolas", 14))
                self.entry.pack(fill="x")
                block_paste(self.entry)   # the phrase must be typed
                self.entry._entry.bind("<KeyRelease>", lambda e: self._typed())
            self.progress = ctk.CTkLabel(box, text=f"0 / {len(self.phrase)}", text_color=MUTED, height=18, anchor="w")
            self.progress.pack(anchor="w", fill="x", pady=(4, 0))
            buttons.pack(fill="x", pady=(12, 0))
            self.go = ctk.CTkButton(buttons, text="Continue", width=110, state="disabled", command=self._pass)
            self.go.pack(side="right")
            ctk.CTkButton(buttons, text="Cancel", width=90, **theme.OUTLINE, command=self._cancel).pack(
                side="right", padx=8)
            if not self.grid:
                self.after(100, self.entry.focus_force)
        if app.winfo_viewable():   # (not when shown alone, e.g. by the uninstaller)
            self.transient(app)
        self.after(50, self._modal)

    def _modal(self):
        try:
            self.grab_set()
        except Exception:   # window not viewable yet
            self.after(50, self._modal)

    def _grid_status(self, text: str, error: bool):
        if hasattr(self, "progress"):
            self.progress.configure(text=text, text_color=theme.DANGER if error else MUTED)

    def _typed(self):
        typed = self.entry.get()
        correct = next((i for i, (a, b) in enumerate(zip(typed, self.phrase)) if a != b), min(len(typed), len(self.phrase)))
        if correct < len(typed):
            self.progress.configure(text=f"Mistake at character {correct + 1} - fix it to go on", text_color=theme.DANGER)
        else:
            self.progress.configure(text=f"{correct} / {len(self.phrase)}", text_color=MUTED)
        self.go.configure(state="normal" if typed == self.phrase else "disabled")

    def _pass(self):
        if not (self.grid.done if self.grid else self.entry.get() == self.phrase):
            return
        antibypass.unlock(self.db, now_from_db(self.db))
        self.destroy()
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

        status = Card(body, "Status")
        status.pack(fill="x", pady=(0, 12))
        self.summary = ctk.CTkLabel(status.body, text="", font=theme.semi(14), anchor="w", justify="left",
                                    wraplength=820)
        self.summary.pack(anchor="w")
        line = ctk.CTkFrame(status.body, fg_color="transparent")
        line.pack(fill="x", pady=(6, 0))
        self.state = ctk.CTkLabel(line, text="", anchor="w")
        self.state.pack(side="left")
        self.lock_btn = ctk.CTkButton(line, text="Lock now", width=100, **theme.OUTLINE, command=self._lock)
        self.unlock_btn = ctk.CTkButton(line, text=f"Unlock for {antibypass.UNLOCK_MIN} minutes", width=170,
                                        **theme.OUTLINE, command=self._unlock)

        challenges = Card(body, "Challenges")
        challenges.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(challenges.body, text="Making blocks stricter is always instant. Anything that loosens them "
                                           "(including these settings) needs the challenges you turn on here.",
                     text_color=MUTED, wraplength=820, justify="left").pack(anchor="w", pady=(0, 8))
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
        self.grid_box = ctk.CTkCheckBox(challenges.body, text="3×3 grid - one word at a time into a box picked at "
                                        "random (you click it; macros can't just type blindly)", command=self._apply)
        self.grid_box.pack(anchor="w", padx=(46, 0), pady=(0, 12))
        line.pack_configure(pady=(4, 6))
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

        protects = Card(body, "What needs the challenge")
        protects.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(protects.body, text="\n".join(f"•  {p}" for p in PROTECTS), justify="left", anchor="w",
                     wraplength=820).pack(anchor="w")
        ctk.CTkLabel(protects.body, text="Not included: the emergency unlock (it has its own limit) and changing when "
                                         "limits reset (it never shortens a limit day). Stopping a locked mode always "
                                         "needs the phrase, even with no challenge turned on here.", text_color=MUTED, wraplength=820,
                     justify="left").pack(anchor="w", pady=(6, 0))

        itself = Card(body, "Keeping Lockdown running")
        itself.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(itself.body, text="\n".join([
            "•  The tray app comes back within a minute if it's closed any other way than Exit (e.g. Task Manager).",
            "•  The service comes back within a minute if it's stopped.",
            "•  Changing the Windows clock does nothing; a new time zone counts only after 24 hours.",
            "•  Uninstalling Lockdown asks for the challenge too."]), justify="left", anchor="w",
            wraplength=820).pack(anchor="w")
        self.refresh()

    def _add_row(self, days, start, end):
        row = WindowRow(self.rows_box, days, start, end, on_remove=lambda r: (self.rows.remove(r), r.destroy()))
        row.pack(anchor="w", pady=2)
        self.rows.append(row)

    def on_show(self):
        self.refresh()

    def refresh(self, *_):
        cfg = antibypass.settings(self.db)
        now = now_from_db(self.db)
        self.summary.configure(text=describe(cfg), text_color=theme.TEXT if antibypass.active(cfg) else MUTED)
        self.phrase_sw.select() if cfg["phrase"] else self.phrase_sw.deselect()
        self.grid_box.select() if cfg["grid"] else self.grid_box.deselect()
        self.length.set(next((k for k, v in antibypass.LENGTHS.items() if v == cfg["length"]), "Medium"))
        self.length_note.configure(text=f"{cfg['length']} characters")
        self.hours_sw.select() if cfg["hours"] else self.hours_sw.deselect()
        for row in self.rows:
            row.destroy()
        self.rows = []
        for w in cfg["windows"]:
            self._add_row(w["days"], w["start"], w["end"])
        self.error.configure(text="")
        self.lock_btn.pack_forget()
        self.unlock_btn.pack_forget()
        status = antibypass.status(cfg, now)
        until = antibypass.unlocked_until(cfg, now)
        if not antibypass.active(cfg):
            self.state.configure(text="Turn on a challenge below.", text_color=MUTED)
        elif status == "closed":
            nxt = antibypass.next_hours(cfg, now)
            self.state.configure(text="Locked - outside the allowed hours" + (f" (next: {when(nxt, now)})" if nxt else ""),
                                 text_color=theme.BLOCKED)
        elif until:
            self.state.configure(text=f"Unlocked until {until:%H:%M} - loosening changes are allowed",
                                 text_color=theme.WARNING)
            self.lock_btn.pack(side="left", padx=12)
        elif status == "phrase":
            self.state.configure(text="Locked", text_color=theme.ALLOWED)
            self.unlock_btn.pack(side="left", padx=12)
        else:   # only allowed hours, and they're now
            self.state.configure(text="Inside the allowed hours - loosening changes are allowed", text_color=theme.WARNING)

    def _read(self) -> dict:
        cfg = antibypass.settings(self.db)
        windows = [w for w in (r.value() for r in self.rows) if w[0]]
        if self.hours_sw.get() and not windows:
            raise ValueError("Turn on at least one day.")
        return {**cfg, "phrase": bool(self.phrase_sw.get()), "length": antibypass.LENGTHS[self.length.get()],
                "grid": bool(self.grid_box.get()),
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
        else:
            save()

    def _lock(self):
        antibypass.lock(self.db)
        self.refresh()

    def _unlock(self):
        self.app.guard([f"Allow loosening changes for {antibypass.UNLOCK_MIN} minutes"], self.refresh, self.refresh)
