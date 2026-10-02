"""Reminders on screen (popup + full-screen overlay for the engine in reminders.py) and the Reminders tab of the
Modes page (sleep, breaks, your own reminders). Changes apply at once."""
import uuid
from datetime import datetime, timedelta

import customtkinter as ctk

import modes
import reminders
from gui import theme
from gui.components import Card, Rows, Segmented, eyebrow, hairline, page_head
from gui.rule_editors import DayToggle
from gui.widgets import ConfirmButton, ConfirmDialog, Corner
from rules import DAY_NAMES, parse_hhmm
from trusted_time import now_from_db

MUTED = theme.MUTED
KINDS = {"Every X min of use": "interval", "At set times": "times", "Random time": "random"}
SNOOZES = [1, 5, 10, 15, 30]
# the dismiss button: darker than Snooze and a third of the width, so "skip it" never reads as the answer
DISMISS = {"fg_color": ("#E4E7EC", "#161B21"), "hover_color": ("#D2D7DE", "#222A33"),
           "border_width": 1, "border_color": ("#C4CBD4", "#2B333D"), "text_color": MUTED}
CHECKS = {"Off": 0, "5 min": 5, "10 min": 10, "15 min": 15, "30 min": 30}


def _finish(win, action, on_answer):
    """Tear the overlay/popup DOWN first, then run the answer on the next idle tick. If we ran the answer first
    (as before), a slow/raising handler - or the Anti-Bypass challenge it opens - would sit *behind* the still-up
    full-screen cover and the app looked frozen. Destroying first guarantees a clean screen for whatever follows."""
    root = win.master
    try:
        win.destroy()
    finally:
        root.after_idle(lambda: on_answer(action))


# ---------------------------------------------------------------- on screen

class ReminderPopup(ctk.CTkToplevel):
    """Bottom-right, always on top, stays until you answer."""
    escape_closes = False   # (it waits for an answer)

    def __init__(self, root, title: str, text: str, buttons, on_answer):
        super().__init__(root)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        frame = ctk.CTkFrame(self, border_width=1, border_color=theme.ACCENT, corner_radius=8)
        frame.pack(fill="both", expand=True)
        ctk.CTkLabel(frame, text=title, font=theme.semi(14)).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(frame, text=text, wraplength=320, justify="left").pack(anchor="w", padx=16, pady=(4, 8))
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(anchor="e", padx=12, pady=(0, 12))
        for i, (label, action) in enumerate(buttons):
            if action == "dismiss":      # small and dark on purpose: a way out, not an answer
                ctk.CTkButton(row, text=label, width=34, **DISMISS,
                              command=lambda a=action: _finish(self, a, on_answer)).pack(side="left", padx=4)
                continue
            style = {} if i == 0 else theme.OUTLINE
            ctk.CTkButton(row, text=label, width=110, **style,
                          command=lambda a=action: _finish(self, a, on_answer)).pack(side="left", padx=4)
        self.update_idletasks()
        Corner.add(self, self.winfo_reqwidth(), self.winfo_reqheight())

    def corner_place(self, x: int, y: int):
        self.geometry(f"+{x}+{y}")


class Overlay(ctk.CTkToplevel):
    """Covers the screen (sleep / breaks). With no buttons it can't be closed - it goes away by itself."""
    escape_closes = False

    def __init__(self, root, title: str, text: str, until: datetime | None, buttons, on_answer, now):
        super().__init__(root, fg_color=("#101316", "#0B0D10"))
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.94)
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.until, self.now = until, now
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.place(relx=0.5, rely=0.45, anchor="center")
        ctk.CTkLabel(box, text=title, font=theme.numeral(54), text_color="#E8ECF1").pack()
        ctk.CTkLabel(box, text=text, font=theme.body(16), text_color="#93A0AE", wraplength=600).pack(pady=(6, 10))
        self.clock = ctk.CTkLabel(box, text="", font=theme.numeral(40), text_color=theme.ACCENT[1])
        self.clock.pack()
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(pady=18)
        for i, (label, action) in enumerate(buttons):
            style = {} if i == 0 else {**theme.OUTLINE, "text_color": "#E8ECF1"}
            ctk.CTkButton(row, text=label, width=150, height=36, **style,
                          command=lambda a=action: _finish(self, a, on_answer)).pack(side="left", padx=6)
        self._count()

    def _count(self):
        if self.until and self.winfo_exists():
            left = max(0, int((self.until - self.now()).total_seconds()))
            self.clock.configure(text=f"{left // 60}:{left % 60:02d}")
            self.after(500, self._count)


class ReminderUI:
    """What the engine uses to show things; lives in the tray agent (app.py)."""

    def __init__(self, app):
        self.app, self.windows = app, {}
        self.engine = None
        self.break_until = None   # set during a strict break: the app minimises windows until then

    def popup(self, key, title, text, buttons):
        self.close(key)
        self.windows[key] = ReminderPopup(self.app, title, text, buttons, lambda a: self._answer(key, a))

    def overlay(self, key, title, text, until, buttons):
        self.close(key)
        self.windows[key] = Overlay(self.app, title, text, until, buttons, lambda a: self._answer(key, a),
                                    lambda: now_from_db(self.app.db))

    def close(self, key):
        win = self.windows.pop(key, None)
        if win is not None and win.winfo_exists():
            win.destroy()

    def toast(self, text):
        self.app._show(text)

    def break_start(self, until):
        """Strict break: minimise everything now; the app keeps windows down until `until` (see _poll_minimize)."""
        from monitor import win
        self.break_until = until
        win.minimize_all()
        left = max(1, round((until - now_from_db(self.app.db)).total_seconds() / 60))
        self.app._show(f"Break started - your windows are minimised for {left} min. Step away from the screen.",
                       force=True)

    def break_end(self):
        self.break_until = None
        self.app._show("Break over - welcome back.", force=True)

    def start_mode(self, mode_id, until):
        mode = next((m for m in modes.load(self.app.db) if m["id"] == mode_id), None)
        if mode:
            try:
                self.app.start_mode(mode, until, False)
            except ValueError:
                pass   # another mode is locked on

    def _answer(self, key, action):
        self.windows.pop(key, None)
        if key == "sleep" and action == "disable":
            self._disable_sleep()      # the escape hatch: challenge first, then off-tonight or snooze
            return
        self.engine.answer(key, action)

    def _disable_sleep(self):
        """"Disable alerts" on the bedtime screen needs the anti-bypass challenge; passing it offers "off for
        tonight" or a snooze. Cancelling just dismisses it, so it returns on the escalation - never a free out."""
        def choose():
            ConfirmDialog(self.app, "Bedtime alerts off", "Off for the rest of tonight, or snooze a while?",
                          on_yes=lambda: self.engine.answer("sleep", "off_tonight"), yes_text="Off tonight",
                          alt_text="Snooze 15 min", on_alt=lambda: self.engine.answer("sleep", "snooze:15"),
                          on_no=lambda: self.engine.answer("sleep", "dismiss"))
        self.app.guard(["Turn off tonight's bedtime alerts"], choose,
                       cancel=lambda: self.engine.answer("sleep", "dismiss"))


# ---------------------------------------------------------------- the Reminders tab

def _reminder_row(parent):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    f.on = ctk.CTkSwitch(f, text="", width=46)
    f.on.pack(side="left")
    texts = ctk.CTkFrame(f, fg_color="transparent")
    texts.pack(side="left", fill="x", expand=True)
    f.text = ctk.CTkLabel(texts, text="", font=theme.semi(13), anchor="w", height=18)
    f.text.pack(anchor="w")
    f.sub = ctk.CTkLabel(texts, text="", text_color=MUTED, font=theme.body(11), anchor="w", height=14)
    f.sub.pack(anchor="w")
    f.delete = ConfirmButton(f, lambda: None, text="Delete", width=70, height=28)
    f.delete.pack(side="right")
    f.edit = ctk.CTkButton(f, text="Edit", width=60, height=28, **theme.SECONDARY)
    f.edit.pack(side="right", padx=6)
    return f


LABEL_W = 104   # Sleep / Breaks: muted labels in one column, controls lined up after it (like Settings)


def _option(parent, label: str, values: list[str], after: str = "", label_w: int = 0) -> ctk.CTkOptionMenu:
    line = ctk.CTkFrame(parent, fg_color="transparent")
    line.pack(anchor="w", pady=3 if not label_w else 5)
    if label_w:
        ctk.CTkLabel(line, text=label, width=label_w, anchor="w", text_color=MUTED).pack(side="left", padx=(0, 12))
    else:
        ctk.CTkLabel(line, text=label).pack(side="left", padx=(0, 8))
    menu = ctk.CTkOptionMenu(line, values=values, width=100)
    menu.pack(side="left")
    if after:
        ctk.CTkLabel(line, text=after, text_color=MUTED).pack(side="left", padx=8)
    return menu


def _duration(parent, label: str, after: str, label_w: int, on_save) -> ctk.CTkEntry:
    """"Heads-up [ 30 min ] before bedtime" - type any time ("45s", "30", "1h30"), not a fixed list."""
    line = ctk.CTkFrame(parent, fg_color="transparent")
    line.pack(anchor="w", pady=5)
    ctk.CTkLabel(line, text=label, width=label_w, anchor="w", text_color=MUTED).pack(side="left", padx=(0, 12))
    entry = ctk.CTkEntry(line, width=72, justify="center")
    entry.pack(side="left")
    entry.bind("<Return>", lambda e: on_save())
    entry.bind("<FocusOut>", lambda e: on_save())
    ctk.CTkLabel(line, text=after, text_color=MUTED).pack(side="left", padx=8)
    return entry


def _message(parent, label: str, default: str, label_w: int, on_save) -> ctk.CTkEntry:
    """"Message [ ....... ]" - leave it empty and the reminder says what it always said."""
    line = ctk.CTkFrame(parent, fg_color="transparent")
    line.pack(anchor="w", fill="x", pady=5)
    ctk.CTkLabel(line, text=label, width=label_w, anchor="w", text_color=MUTED).pack(side="left", padx=(0, 12))
    entry = ctk.CTkEntry(line, placeholder_text=default)
    entry.pack(side="left", fill="x", expand=True)
    entry.bind("<Return>", lambda e: on_save())
    entry.bind("<FocusOut>", lambda e: on_save())
    return entry


class RemindersView(ctk.CTkScrollableFrame):
    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.db = page, page.db

        # Sleep and Breaks side by side (the page used to be one long stack of ragged lines)
        cols = ctk.CTkFrame(self, fg_color="transparent")
        cols.pack(fill="x", pady=(0, 12))
        cols.grid_columnconfigure((0, 1), weight=1, uniform="r")
        cols.grid_rowconfigure(0, weight=1)

        def note(parent, text):
            ctk.CTkLabel(parent, text=text, text_color=MUTED, font=theme.body(11), wraplength=440, justify="left",
                         anchor="w").pack(anchor="w", padx=(42, 0), pady=(0, 6))   # under the switch's text

        sleep = Card(cols, "Sleep")
        sleep.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        b = sleep.body
        self.sleep_on = ctk.CTkSwitch(b, text="Remind me to go to bed", font=theme.semi(13), command=self._save_sleep)
        self.sleep_on.pack(anchor="w", pady=(4, 4))
        self.sleep_guarded = ctk.CTkCheckBox(b, text="Important — needs the challenge to turn off",
                                             checkbox_width=18, checkbox_height=18, command=self._save_sleep)
        self.sleep_guarded.pack(anchor="w", pady=(0, 8))
        line = ctk.CTkFrame(b, fg_color="transparent")
        line.pack(anchor="w", pady=5)
        ctk.CTkLabel(line, text="Bedtime", width=LABEL_W, anchor="w", text_color=MUTED).pack(side="left", padx=(0, 12))
        self.bedtime = ctk.CTkEntry(line, width=64, justify="center")
        self.bedtime.pack(side="left")
        ctk.CTkLabel(line, text="Wake up", text_color=MUTED).pack(side="left", padx=(16, 8))
        self.wake = ctk.CTkEntry(line, width=64, justify="center")
        self.wake.pack(side="left")
        ctk.CTkButton(line, text="Save times", width=90, height=28, **theme.OUTLINE, command=self._save_sleep).pack(
            side="left", padx=12)
        self.before = _duration(b, "Heads-up", "before bedtime (\"Off\", \"30\", \"1h\")", LABEL_W,
                                self._save_sleep)
        esc = ctk.CTkFrame(b, fg_color="transparent")
        esc.pack(anchor="w", fill="x", pady=5)
        ctk.CTkLabel(esc, text="Comes back", width=LABEL_W, anchor="w", text_color=MUTED).pack(side="left",
                                                                                              padx=(0, 12), anchor="n")
        right = ctk.CTkFrame(esc, fg_color="transparent")
        right.pack(side="left", fill="x")
        self.tier_box = ctk.CTkFrame(right, fg_color="transparent")
        self.tier_box.pack(anchor="w")
        self.tier_rows = []
        ctk.CTkButton(right, text="+ Add step", width=90, height=26, **theme.OUTLINE,
                      command=self._add_tier).pack(anchor="w", pady=(4, 0))
        ctk.CTkLabel(right, text="The later it is, the more often the bedtime screen returns after you dismiss it.",
                     text_color=MUTED, font=theme.body(11), wraplength=330, justify="left").pack(anchor="w",
                                                                                                 pady=(2, 0))
        self.sleep_mode = _option(b, "Turn on", ["No mode"], "at bedtime, until wake-up time", LABEL_W)
        self.sleep_mode.configure(command=lambda v: self._save_sleep())
        self.sleep_warn_text = _message(b, "Heads-up says", reminders.SLEEP_WARN_TEXT, LABEL_W, self._save_sleep)
        self.sleep_text = _message(b, "Bedtime says", reminders.SLEEP_TEXT, LABEL_W, self._save_sleep)
        ctk.CTkLabel(b, text="Your own words, or leave empty for these. {bedtime}, {wake} and {time} are filled "
                             "in.", text_color=MUTED, font=theme.body(11), wraplength=330, justify="left").pack(
            anchor="w", padx=(LABEL_W + 12, 0))
        self.sleep_error = ctk.CTkLabel(b, text="", text_color=theme.DANGER, height=16)   # packed while it says something
        tip = ctk.CTkFrame(b, fg_color=theme.BG, border_width=1, border_color=theme.BORDER, corner_radius=3)
        tip.pack(side="bottom", fill="x", pady=(10, 0))
        ctk.CTkLabel(tip, text="At bedtime the screen dims with a \"Time for bed\" message (also over games).",
                     text_color=MUTED, font=theme.body(11), wraplength=500, justify="left").pack(
            anchor="w", padx=12, pady=8)

        brk = Card(cols, "Breaks")
        brk.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        b = brk.body
        self.break_on = ctk.CTkSwitch(b, text="Remind me to take breaks", font=theme.semi(13),
                                      command=self._save_break)
        self.break_on.pack(anchor="w", pady=(4, 8))
        self.every = _option(b, "After", ["20 min", "30 min", "45 min", "60 min", "90 min"], "of use without a break",
                             LABEL_W)
        self.length = _option(b, "Break length", ["2 min", "5 min", "10 min", "15 min"], label_w=LABEL_W)
        self.snooze = _option(b, "Snooze", ["5 min", "10 min", "15 min", "20 min"], "each time", LABEL_W)
        for menu in (self.every, self.length, self.snooze):
            menu.configure(command=lambda v: self._save_break())
        self.break_text = _message(b, "It says", reminders.BREAK_TEXT, LABEL_W, self._save_break)
        ctk.CTkLabel(b, text="Your own words, or leave empty for this one. {every} and {length} are filled in.",
                     text_color=MUTED, font=theme.body(11), wraplength=330, justify="left").pack(
            anchor="w", padx=(LABEL_W + 12, 0))
        hairline(b).pack(fill="x", pady=(8, 10))
        self.break_guarded = ctk.CTkSwitch(b, text="Important — needs the challenge to turn off", font=theme.semi(13),
                                           command=self._save_break)
        self.break_guarded.pack(anchor="w", pady=(0, 8))
        self.strict = ctk.CTkSwitch(b, text="Strict break", font=theme.semi(13), command=self._save_break)
        self.strict.pack(anchor="w")
        note(b, "Minimises everything until the break is over - you can't just dismiss it.")
        self.max_snooze = _option(b, "Snoozes first", ["1", "2", "3", "5"], "before a strict break starts on its own",
                                  LABEL_W)
        self.max_snooze.configure(command=lambda v: self._save_break())
        self.twenty = ctk.CTkSwitch(b, text="20-20-20", font=theme.semi(13), command=self._save_break)
        self.twenty.pack(anchor="w", pady=(8, 0))
        note(b, "Every 20 min, look 20 feet (6 m) away for 20 seconds.")
        self.twenty_text = _message(b, "It says", reminders.TWENTY_TEXT, LABEL_W, self._save_break)
        self.break_stats = ctk.CTkLabel(brk.title.master, text="", text_color=MUTED, font=theme.body(11), height=20)
        self.break_stats.pack(side="right")

        own = self.own = Card(self, "Your reminders")
        own.pack(fill="x", pady=(0, 12))
        ctk.CTkButton(own.title.master, text="+ New reminder", width=130, command=lambda: self._edit(None)).pack(
            side="right")
        self.rows = Rows(own.body, _reminder_row, "No reminders yet - e.g. \"Drink water\" every 60 min.",
                         {"fill": "x", "pady": 3})
        self.editor = Card(self, "Reminder")
        self._editor_built = False   # its ~200 widgets are built the first time you open it, not on page load
                                     # (customtkinter widget creation is the page's main cost - this ~halves it)

    # ---------- load ----------

    def refresh(self):
        s = reminders.load(self.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
        self.sleep_on.select() if s["on"] else self.sleep_on.deselect()
        for entry, value in ((self.bedtime, s["bedtime"]), (self.wake, s["wake"])):
            entry.delete(0, "end")
            entry.insert(0, value)
        self.before.delete(0, "end")
        self.before.insert(0, reminders.minutes_text(s["before"]))
        self._load_tiers(reminders.tiers_of(s))
        for entry, value in ((self.sleep_warn_text, s.get("warn_text")), (self.sleep_text, s.get("text"))):
            if entry.get() != (value or ""):
                entry.delete(0, "end")
                entry.insert(0, value or "")
        all_modes = modes.load(self.db)
        self.mode_ids = {"No mode": ""} | {m["name"]: m["id"] for m in all_modes}
        self.sleep_mode.configure(values=list(self.mode_ids))
        self.sleep_mode.set(next((n for n, i in self.mode_ids.items() if i == s["mode"]), "No mode"))
        self.sleep_guarded.select() if s.get("guarded") else self.sleep_guarded.deselect()
        b = reminders.load(self.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)
        self.break_on.select() if b["on"] else self.break_on.deselect()
        self.every.set(f"{b['every']} min")
        self.length.set(f"{b['length']} min")
        self.snooze.set(f"{b.get('snooze', 5)} min")
        self.strict.select() if b.get("strict") else self.strict.deselect()
        self.break_guarded.select() if b.get("guarded") else self.break_guarded.deselect()
        self.max_snooze.set(str(b.get("max_snooze", 2)))
        self.twenty.select() if b["twenty"] else self.twenty.deselect()
        for entry, value in ((self.break_text, b.get("text")), (self.twenty_text, b.get("twenty_text"))):
            if entry.get() != (value or ""):
                entry.delete(0, "end")
                entry.insert(0, value or "")
        today = datetime.combine(now_from_db(self.db).date(), datetime.min.time())
        c = reminders.counts(self.db, "break", today)
        self.break_stats.configure(text=f"Breaks today: {c.get('taken', 0) + c.get('away', 0)}")

        items = reminders.custom_list(self.db)
        week = today - timedelta(days=7)
        week_counts = reminders.counts_many(self.db, [r["id"] for r in items], week)   # one query for all of them
        for row, r in zip(self.rows.take(len(items)), items):
            row.text.configure(text=r["text"] or "(no text)")
            c = week_counts.get(r["id"], {})
            stats = f"done {c.get('done', 0)}x in 7 days" + (f", 'not done' {c['not done']}x" if c.get("not done")
                                                                else "")
            row.sub.configure(text=f"{reminders.schedule_text(r)} · {stats}")
            row.on.configure(command=lambda r=r, sw=row.on: self._toggle(r, sw.get()))
            row.on.select() if r["on"] else row.on.deselect()
            row.edit.configure(command=lambda r=r: self._edit(r))
            row.delete._on_confirm = lambda r=r: self._delete(r)

    # ---------- sleep / breaks ----------

    def _tier_row(self, every: str = "15", after: str = "21:00"):
        row = ctk.CTkFrame(self.tier_box, fg_color="transparent")
        row.pack(anchor="w", pady=2)
        ctk.CTkLabel(row, text="every", text_color=MUTED).pack(side="left")
        row.every = ctk.CTkEntry(row, width=44, justify="center")
        row.every.insert(0, every)
        row.every.pack(side="left", padx=6)
        ctk.CTkLabel(row, text="min, after", text_color=MUTED).pack(side="left")
        row.after = ctk.CTkEntry(row, width=60, justify="center")
        row.after.insert(0, after)
        row.after.pack(side="left", padx=6)
        ctk.CTkButton(row, text="\u00d7", width=26, height=26, **theme.OUTLINE,
                      command=lambda: self._remove_tier(row)).pack(side="left")
        for e in (row.every, row.after):
            e.bind("<Return>", lambda ev: self._save_sleep())
            e.bind("<FocusOut>", lambda ev: self._save_sleep())
        self.tier_rows.append(row)

    def _add_tier(self):
        self._tier_row()
        self._save_sleep()

    def _remove_tier(self, row):
        self.tier_rows.remove(row)
        row.destroy()
        self._save_sleep()

    def _load_tiers(self, tiers):
        for row in self.tier_rows:
            row.destroy()
        self.tier_rows = []
        for t in tiers:
            self._tier_row(str(t.get("every", 5)), t.get("from", "21:00"))

    def _read_tiers(self):
        tiers = []
        for row in self.tier_rows:
            after, every = parse_hhmm(row.after.get()), int(reminders.parse_minutes(row.every.get(), allow_off=False))
            tiers.append({"from": f"{after:%H:%M}", "every": max(1, every)})
        return sorted(tiers, key=lambda t: parse_hhmm(t["from"]))

    def _save_guarded(self, name, old, key, new):
        """Save reminder settings. Turning an important alert off (or removing its \"important\" flag) is
        loosening, so it needs the Anti-Bypass challenge first; on cancel the switches snap back and nothing
        is saved."""
        loosens = reminders.loosens_reminder(old, new)
        if loosens:
            self.page.guard([f"Turn off / un-flag the important {name} reminder"],
                            lambda: reminders.save(self.db, key, new), cancel=self.refresh)
        else:
            reminders.save(self.db, key, new)

    def _save_sleep(self):
        try:
            bedtime, wake = parse_hhmm(self.bedtime.get()), parse_hhmm(self.wake.get())
        except ValueError:
            self._sleep_says("Write times like 23:00.")
            return
        try:
            before = reminders.parse_minutes(self.before.get(), most=12 * 60)
            tiers = self._read_tiers()
        except ValueError:
            self._sleep_says("Steps need minutes like 5 and a time like 21:00.")
            return
        self.sleep_error.pack_forget()
        old = reminders.load(self.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
        self._save_guarded("bedtime", old, reminders.SLEEP_KEY, {
            "on": bool(self.sleep_on.get()), "bedtime": f"{bedtime:%H:%M}", "wake": f"{wake:%H:%M}",
            "before": before, "repeat": 5, "tiers": tiers, "warn_text": self.sleep_warn_text.get().strip(),
            "text": self.sleep_text.get().strip(),
            "guarded": bool(self.sleep_guarded.get()),
            "mode": self.mode_ids.get(self.sleep_mode.get(), "")})

    def _sleep_says(self, text: str):
        self.sleep_error.configure(text=text)
        self.sleep_error.pack(anchor="w")

    def _save_break(self):
        old = reminders.load(self.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)
        self._save_guarded("break", old, reminders.BREAK_KEY, {
            "on": bool(self.break_on.get()), "every": int(self.every.get().split()[0]),
            "length": int(self.length.get().split()[0]), "strict": bool(self.strict.get()),
            "snooze": int(self.snooze.get().split()[0]), "max_snooze": int(self.max_snooze.get()),
            "twenty": bool(self.twenty.get()), "text": self.break_text.get().strip(),
            "twenty_text": self.twenty_text.get().strip(), "guarded": bool(self.break_guarded.get())})

    # ---------- your reminders ----------

    def _build_editor(self, b):
        line = ctk.CTkFrame(b, fg_color="transparent")
        line.pack(fill="x")
        ctk.CTkLabel(line, text="Text", width=60, anchor="w").pack(side="left")
        self.r_text = ctk.CTkEntry(line, placeholder_text="e.g. Drink water, Stretch, Go outside")
        self.r_text.pack(side="left", fill="x", expand=True)
        eyebrow(b, "When").pack(anchor="w", pady=(12, 4))
        self.r_kind = Segmented(b, list(KINDS), command=lambda v: self._kind_changed())
        self.r_kind.pack(anchor="w")
        self.r_interval = ctk.CTkFrame(b, fg_color="transparent")
        every_row = ctk.CTkFrame(self.r_interval, fg_color="transparent")
        every_row.pack(anchor="w")
        ctk.CTkLabel(every_row, text="Every").pack(side="left")
        self.r_every = ctk.CTkEntry(every_row, width=56, justify="center")
        self.r_every.pack(side="left", padx=8)
        ctk.CTkLabel(every_row, text="minutes of use (time away from the PC doesn't count)",
                     text_color=MUTED).pack(side="left")
        days = ctk.CTkFrame(b, fg_color="transparent")      # which days - for every kind of reminder
        days.pack(anchor="w", pady=(8, 0))
        ctk.CTkLabel(days, text="On", text_color=MUTED, width=28, anchor="w").pack(side="left")
        self.r_days = [DayToggle(days, d[:3], True) for d in DAY_NAMES]
        for d in self.r_days:
            d.pack(side="left", padx=(0, 4))
        self.r_times = ctk.CTkFrame(b, fg_color="transparent")
        line = ctk.CTkFrame(self.r_times, fg_color="transparent")
        line.pack(anchor="w")
        ctk.CTkLabel(line, text="At").pack(side="left")
        self.r_at = ctk.CTkEntry(line, width=200, placeholder_text="09:00, 13:00, 21:00")
        self.r_at.pack(side="left", padx=8)
        self.r_hours_row = ctk.CTkFrame(self.r_interval, fg_color="transparent")
        self.r_hours = ctk.CTkCheckBox(self.r_hours_row, text="Only between", checkbox_width=18,
                                       checkbox_height=18, width=120)
        self.r_hours.pack(side="left")
        self.r_hours_from = ctk.CTkEntry(self.r_hours_row, width=64, justify="center")
        self.r_hours_from.pack(side="left", padx=8)
        ctk.CTkLabel(self.r_hours_row, text="and").pack(side="left")
        self.r_hours_to = ctk.CTkEntry(self.r_hours_row, width=64, justify="center")
        self.r_hours_to.pack(side="left", padx=8)
        ctk.CTkLabel(self.r_hours_row, text="(so it doesn't ask you to do push-ups at 3 a.m.)",
                     text_color=MUTED).pack(side="left")
        self.r_random = ctk.CTkFrame(b, fg_color="transparent")
        ctk.CTkLabel(self.r_random, text="Once a day, at a random time between").pack(side="left")
        self.r_from = ctk.CTkEntry(self.r_random, width=64, justify="center")
        self.r_from.pack(side="left", padx=8)
        ctk.CTkLabel(self.r_random, text="and").pack(side="left")
        self.r_to = ctk.CTkEntry(self.r_random, width=64, justify="center")
        self.r_to.pack(side="left", padx=8)
        self.r_kind_after = ctk.CTkFrame(b, fg_color="transparent", height=1)
        self.r_kind_after.pack(fill="x")
        eyebrow(b, "Options").pack(anchor="w", pady=(12, 4))
        self.r_snooze = _option(b, "Snooze for", [f"{m} min" for m in SNOOZES])
        self.r_max = _option(b, "At most", [str(n) for n in range(0, 6)], "snoozes - then it stays until Done")
        self.r_check = _option(b, "Ask \"did you actually do it?\"", list(CHECKS), "after Done")
        self.r_per_day = _option(b, "Stop for the day after", ["No limit"] + [str(n) for n in range(1, 13)],
                                 "times DONE (not times shown: snoozing or ignoring it doesn't count)")
        self.r_guarded = ctk.CTkCheckBox(b, text="Important — needs the challenge to turn off", checkbox_width=18, checkbox_height=18)
        self.r_guarded.pack(anchor="w", pady=(8, 0))
        eyebrow(b, "Quotes (a random one is shown with the reminder)").pack(anchor="w", pady=(12, 4))
        packs = ctk.CTkFrame(b, fg_color="transparent")
        packs.pack(anchor="w")
        self.r_packs = {}
        for name in reminders.PACKS:
            box = ctk.CTkCheckBox(packs, text=name, width=110)
            box.pack(side="left", padx=(0, 8))
            self.r_packs[name] = box
        ctk.CTkLabel(b, text="Your own quotes, one per line:", text_color=MUTED).pack(anchor="w", pady=(6, 2))
        self.r_quotes = ctk.CTkTextbox(b, height=70)
        self.r_quotes.pack(fill="x")
        self.r_error = ctk.CTkLabel(b, text="", text_color=theme.DANGER)
        self.r_error.pack(anchor="w")
        buttons = ctk.CTkFrame(b, fg_color="transparent")
        buttons.pack(anchor="w")
        ctk.CTkButton(buttons, text="Save", width=90, command=self._save_reminder).pack(side="left")
        ctk.CTkButton(buttons, text="Cancel", width=80, **theme.OUTLINE,
                      command=lambda: self.editor.pack_forget()).pack(side="left", padx=6)

    def _kind_changed(self):
        kind = KINDS[self.r_kind.get()]
        for frame, k in ((self.r_interval, "interval"), (self.r_times, "times"), (self.r_random, "random")):
            if k == kind:
                frame.pack(anchor="w", pady=(8, 0), before=self.r_kind_after)
            else:
                frame.pack_forget()
        # "at 09:00" and "a random time between" already say which hours; only "every N minutes" needs it asked
        self.r_hours_row.pack(anchor="w", pady=(6, 0)) if kind == "interval" else self.r_hours_row.pack_forget()

    def _edit(self, r: dict | None):
        if not self._editor_built:        # build the editor on first use (see __init__)
            self._build_editor(self.editor.body)
            self._editor_built = True
        self.editing = r
        r = r or {**reminders.DEFAULT_CUSTOM}
        self.editor.title.configure(text="Edit reminder" if self.editing else "New reminder")
        self.r_text.delete(0, "end")
        if r["text"]:
            self.r_text.insert(0, r["text"])
        self.r_kind.set(next(k for k, v in KINDS.items() if v == r["kind"]))
        self.r_hours.select() if r.get("hours") else self.r_hours.deselect()
        self.r_per_day.set(str(r.get("per_day") or "No limit"))
        self.r_guarded.select() if r.get("guarded") else self.r_guarded.deselect()
        for entry, value in ((self.r_every, str(r["every"])), (self.r_at, ", ".join(r["times"])),
                             (self.r_from, r["window"][0]), (self.r_to, r["window"][1]),
                             (self.r_hours_from, r["window"][0]), (self.r_hours_to, r["window"][1])):
            entry.delete(0, "end")
            entry.insert(0, value)
        for i, d in enumerate(self.r_days):
            d.on = i in r["days"]
            d._paint()
        self.r_snooze.set(f"{r['snooze']} min")
        self.r_max.set(str(r["max_snooze"]))
        self.r_check.set(next((k for k, v in CHECKS.items() if v == r["check"]), "Off"))
        for name, box in self.r_packs.items():
            box.select() if name in r["packs"] else box.deselect()
        self.r_quotes.delete("1.0", "end")
        self.r_quotes.insert("1.0", r["quotes"])
        self.r_error.configure(text="")
        self._kind_changed()
        self.editor.pack(fill="x", pady=(0, 12), after=self.own)

    def _save_reminder(self):
        text = self.r_text.get().strip()
        kind = KINDS[self.r_kind.get()]
        try:
            if not text:
                raise ValueError("Write what to remind you of.")
            try:
                every = int(self.r_every.get().strip())
            except ValueError:
                raise ValueError("Minutes must be a whole number.") from None
            if kind == "interval" and not 1 <= every <= 1440:
                raise ValueError("Minutes must be between 1 and 1440.")
            try:
                times = [f"{parse_hhmm(t):%H:%M}" for t in self.r_at.get().replace(";", ",").split(",") if t.strip()]
                window = [f"{parse_hhmm(self.r_from.get()):%H:%M}", f"{parse_hhmm(self.r_to.get()):%H:%M}"]
                if kind == "interval":      # its own "only between" boxes hold the same window
                    window = [f"{parse_hhmm(self.r_hours_from.get()):%H:%M}",
                              f"{parse_hhmm(self.r_hours_to.get()):%H:%M}"]
            except ValueError:
                raise ValueError("Write times like 09:00 (several: 09:00, 13:00).") from None
            days = [i for i, d in enumerate(self.r_days) if d.get()]
            if not days:
                raise ValueError("Pick at least one day.")
            if kind == "times" and not times:
                raise ValueError("Pick at least one time.")
            if kind == "random" and window[0] >= window[1]:
                raise ValueError("The random window must start before it ends.")
        except ValueError as e:
            self.r_error.configure(text=str(e))
            return
        items = reminders.load(self.db, reminders.CUSTOM_KEY, [])
        new = {"id": self.editing["id"] if self.editing else uuid.uuid4().hex[:8], "on": True, "text": text,
               "kind": kind, "every": every, "times": times or ["12:00"], "days": days, "window": window,
               "snooze": int(self.r_snooze.get().split()[0]), "max_snooze": int(self.r_max.get()),
               "check": CHECKS[self.r_check.get()], "packs": [n for n, b in self.r_packs.items() if b.get()],
               "quotes": self.r_quotes.get("1.0", "end").strip(),
               "hours": bool(self.r_hours.get()) and kind == "interval",
               "per_day": 0 if self.r_per_day.get() == "No limit" else int(self.r_per_day.get()),
               "guarded": bool(self.r_guarded.get())}
        if self.editing:
            new["on"] = self.editing["on"]
            items = [new if x["id"] == new["id"] else x for x in items]
        else:
            items.append(new)
        def do():
            reminders.save(self.db, reminders.CUSTOM_KEY, items)
            self.editor.pack_forget()
            self.refresh()
        if self.editing and self.editing.get("guarded") and not new.get("guarded"):
            self.page.guard([f'Remove "important" from "{new["text"]}"'], do,
                            cancel=lambda: self.r_guarded.select())
            return
        do()

    def _toggle(self, r: dict, on):
        if not on and r.get("guarded"):     # turning an important reminder off needs the challenge
            self.page.guard([f'Turn off the important reminder "{r["text"]}"'],
                            lambda: self._set_on(r, False), cancel=self.refresh)
            return
        self._set_on(r, on)

    def _set_on(self, r: dict, on):
        items = reminders.load(self.db, reminders.CUSTOM_KEY, [])
        reminders.save(self.db, reminders.CUSTOM_KEY, [{**x, "on": bool(on)} if x["id"] == r["id"] else x
                                                       for x in items])

    def _delete(self, r: dict):
        if r.get("guarded"):
            self.page.guard([f'Delete the important reminder "{r["text"]}"'], lambda: self._do_delete(r))
            return
        self._do_delete(r)

    def _do_delete(self, r: dict):
        items = reminders.load(self.db, reminders.CUSTOM_KEY, [])
        reminders.save(self.db, reminders.CUSTOM_KEY, [x for x in items if x["id"] != r["id"]])
        self.editor.pack_forget()
        self.refresh()

    def reset(self):
        """Close the open reminder editor (so leaving the page returns it to the default view)."""
        self.editor.pack_forget()


class RemindersPage(ctk.CTkFrame):
    """Sidebar page for breaks, sleep and your own reminders (was the Reminders tab under Modes)."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        page_head(self, "Reminders").pack(anchor="w", padx=30, pady=(12, 8))
        self.view = RemindersView(self, app)   # RemindersView only needs .db, which the app has
        self.view.pack(fill="both", expand=True, padx=(20, 12), pady=(0, 14))

    def on_show(self):
        self.view.reset()
        self.view.refresh()

    def refresh(self):
        self.view.refresh()
