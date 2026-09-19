"""Reminders on screen (popup + full-screen overlay for the engine in reminders.py) and the Reminders tab of the
Modes page (sleep, breaks, your own reminders). Changes apply at once."""
import uuid
from datetime import datetime, timedelta

import customtkinter as ctk

import modes
import reminders
from gui import theme
from gui.components import Card, Rows, Segmented, eyebrow, page_head
from gui.rule_editors import DayToggle
from gui.widgets import ConfirmButton
from rules import DAY_NAMES, parse_hhmm
from trusted_time import now_from_db

MUTED = theme.MUTED
KINDS = {"Every X min of use": "interval", "At set times": "times", "Random time": "random"}
SNOOZES = [1, 5, 10, 15, 30]
CHECKS = {"Off": 0, "5 min": 5, "10 min": 10, "15 min": 15, "30 min": 30}


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
            style = {} if i == 0 else theme.OUTLINE
            ctk.CTkButton(row, text=label, width=110, **style,
                          command=lambda a=action: (on_answer(a), self.destroy())).pack(side="left", padx=4)
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{self.winfo_screenwidth() - w - 16}+{self.winfo_screenheight() - h - 64}")


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
                          command=lambda a=action: (on_answer(a), self.destroy())).pack(side="left", padx=6)
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
        self.engine.answer(key, action)


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


def _option(parent, label: str, values: list[str], after: str = "") -> ctk.CTkOptionMenu:
    line = ctk.CTkFrame(parent, fg_color="transparent")
    line.pack(anchor="w", pady=3)
    ctk.CTkLabel(line, text=label).pack(side="left", padx=(0, 8))
    menu = ctk.CTkOptionMenu(line, values=values, width=100)
    menu.pack(side="left")
    if after:
        ctk.CTkLabel(line, text=after, text_color=MUTED).pack(side="left", padx=8)
    return menu


class RemindersView(ctk.CTkScrollableFrame):
    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.db = page, page.db

        sleep = Card(self, "Sleep")
        sleep.pack(fill="x", pady=(0, 12))
        b = sleep.body
        self.sleep_on = ctk.CTkSwitch(b, text="Remind me to go to bed", command=self._save_sleep)
        self.sleep_on.pack(anchor="w", pady=(0, 4))
        line = ctk.CTkFrame(b, fg_color="transparent")
        line.pack(anchor="w", pady=3)
        ctk.CTkLabel(line, text="Bedtime").pack(side="left", padx=(0, 8))
        self.bedtime = ctk.CTkEntry(line, width=64, justify="center")
        self.bedtime.pack(side="left")
        ctk.CTkLabel(line, text="Wake up").pack(side="left", padx=(16, 8))
        self.wake = ctk.CTkEntry(line, width=64, justify="center")
        self.wake.pack(side="left")
        ctk.CTkButton(line, text="Save times", width=90, **theme.OUTLINE, command=self._save_sleep).pack(side="left",
                                                                                                       padx=12)
        self.before = _option(b, "Heads-up", ["Off", "10 min", "15 min", "30 min", "60 min"], "before bedtime")
        self.repeat = _option(b, "After bedtime, show it again every", ["5 min", "10 min", "15 min", "30 min"])
        self.sleep_mode = _option(b, "At bedtime, turn on", ["No mode"], "until wake-up time")
        for menu in (self.before, self.repeat, self.sleep_mode):
            menu.configure(command=lambda v: self._save_sleep())
        ctk.CTkLabel(b, text="At bedtime the screen dims with a \"Time for bed\" message (also over games).",
                     text_color=MUTED, font=theme.body(11)).pack(anchor="w", pady=(4, 0))
        self.sleep_error = ctk.CTkLabel(b, text="", text_color=theme.DANGER, height=16)
        self.sleep_error.pack(anchor="w")

        brk = Card(self, "Breaks")
        brk.pack(fill="x", pady=(0, 12))
        b = brk.body
        self.break_on = ctk.CTkSwitch(b, text="Remind me to take breaks", command=self._save_break)
        self.break_on.pack(anchor="w", pady=(0, 4))
        self.every = _option(b, "After", ["20 min", "30 min", "45 min", "60 min", "90 min"], "of use without a break")
        self.length = _option(b, "Break length", ["2 min", "5 min", "10 min", "15 min"])
        self.snooze = _option(b, "Snooze", ["5 min", "10 min", "15 min", "20 min"], "each time")
        for menu in (self.every, self.length, self.snooze):
            menu.configure(command=lambda v: self._save_break())
        self.strict = ctk.CTkSwitch(b, text="Strict break: minimise everything until it's over (you can't just "
                                    "dismiss it)", command=self._save_break)
        self.strict.pack(anchor="w", pady=3)
        self.max_snooze = _option(b, "In a strict break, snoozes before it starts on its own", ["1", "2", "3", "5"])
        self.max_snooze.configure(command=lambda v: self._save_break())
        self.twenty = ctk.CTkSwitch(b, text="20-20-20: every 20 min, look 20 feet (6 m) away for 20 seconds",
                                    command=self._save_break)
        self.twenty.pack(anchor="w", pady=3)
        self.break_stats = ctk.CTkLabel(b, text="", text_color=MUTED, font=theme.body(11))
        self.break_stats.pack(anchor="w", pady=(4, 0))

        own = self.own = Card(self, "Your reminders")
        own.pack(fill="x", pady=(0, 12))
        ctk.CTkButton(own.title.master, text="+ New reminder", width=130, command=lambda: self._edit(None)).pack(
            side="right")
        self.rows = Rows(own.body, _reminder_row, "No reminders yet - e.g. \"Drink water\" every 60 min.",
                         {"fill": "x", "pady": 3})
        self.editor = Card(self, "Reminder")
        self._build_editor(self.editor.body)

    # ---------- load ----------

    def refresh(self):
        s = reminders.load(self.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
        self.sleep_on.select() if s["on"] else self.sleep_on.deselect()
        for entry, value in ((self.bedtime, s["bedtime"]), (self.wake, s["wake"])):
            entry.delete(0, "end")
            entry.insert(0, value)
        self.before.set("Off" if not s["before"] else f"{s['before']} min")
        self.repeat.set(f"{s['repeat']} min")
        all_modes = modes.load(self.db)
        self.mode_ids = {"No mode": ""} | {m["name"]: m["id"] for m in all_modes}
        self.sleep_mode.configure(values=list(self.mode_ids))
        self.sleep_mode.set(next((n for n, i in self.mode_ids.items() if i == s["mode"]), "No mode"))
        b = reminders.load(self.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)
        self.break_on.select() if b["on"] else self.break_on.deselect()
        self.every.set(f"{b['every']} min")
        self.length.set(f"{b['length']} min")
        self.snooze.set(f"{b.get('snooze', 5)} min")
        self.strict.select() if b.get("strict") else self.strict.deselect()
        self.max_snooze.set(str(b.get("max_snooze", 2)))
        self.twenty.select() if b["twenty"] else self.twenty.deselect()
        today = datetime.combine(now_from_db(self.db).date(), datetime.min.time())
        c = reminders.counts(self.db, "break", today)
        self.break_stats.configure(text=f"Breaks today: {c.get('taken', 0) + c.get('away', 0)}")

        items = reminders.custom_list(self.db)
        week = today - timedelta(days=7)
        for row, r in zip(self.rows.take(len(items)), items):
            row.text.configure(text=r["text"] or "(no text)")
            c = reminders.counts(self.db, r["id"], week)
            stats = f"done {c.get('done', 0)}x in 7 days" + (f", 'not done' {c['not done']}x" if c.get("not done")
                                                                else "")
            row.sub.configure(text=f"{reminders.schedule_text(r)} · {stats}")
            row.on.configure(command=lambda r=r, sw=row.on: self._toggle(r, sw.get()))
            row.on.select() if r["on"] else row.on.deselect()
            row.edit.configure(command=lambda r=r: self._edit(r))
            row.delete._on_confirm = lambda r=r: self._delete(r)

    # ---------- sleep / breaks ----------

    def _save_sleep(self):
        try:
            bedtime, wake = parse_hhmm(self.bedtime.get()), parse_hhmm(self.wake.get())
        except ValueError:
            self.sleep_error.configure(text="Write times like 23:00.")
            return
        self.sleep_error.configure(text="")
        before = self.before.get()
        reminders.save(self.db, reminders.SLEEP_KEY, {
            "on": bool(self.sleep_on.get()), "bedtime": f"{bedtime:%H:%M}", "wake": f"{wake:%H:%M}",
            "before": 0 if before == "Off" else int(before.split()[0]), "repeat": int(self.repeat.get().split()[0]),
            "mode": self.mode_ids.get(self.sleep_mode.get(), "")})

    def _save_break(self):
        reminders.save(self.db, reminders.BREAK_KEY, {
            "on": bool(self.break_on.get()), "every": int(self.every.get().split()[0]),
            "length": int(self.length.get().split()[0]), "strict": bool(self.strict.get()),
            "snooze": int(self.snooze.get().split()[0]), "max_snooze": int(self.max_snooze.get()),
            "twenty": bool(self.twenty.get())})

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
        ctk.CTkLabel(self.r_interval, text="Every").pack(side="left")
        self.r_every = ctk.CTkEntry(self.r_interval, width=56, justify="center")
        self.r_every.pack(side="left", padx=8)
        ctk.CTkLabel(self.r_interval, text="minutes of use (time away from the PC doesn't count)",
                     text_color=MUTED).pack(side="left")
        self.r_times = ctk.CTkFrame(b, fg_color="transparent")
        days = ctk.CTkFrame(self.r_times, fg_color="transparent")
        days.pack(anchor="w")
        self.r_days = [DayToggle(days, d[:3], True) for d in DAY_NAMES]
        for d in self.r_days:
            d.pack(side="left", padx=(0, 4))
        line = ctk.CTkFrame(self.r_times, fg_color="transparent")
        line.pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(line, text="At").pack(side="left")
        self.r_at = ctk.CTkEntry(line, width=200, placeholder_text="09:00, 13:00, 21:00")
        self.r_at.pack(side="left", padx=8)
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

    def _edit(self, r: dict | None):
        self.editing = r
        r = r or {**reminders.DEFAULT_CUSTOM}
        self.editor.title.configure(text="Edit reminder" if self.editing else "New reminder")
        self.r_text.delete(0, "end")
        if r["text"]:
            self.r_text.insert(0, r["text"])
        self.r_kind.set(next(k for k, v in KINDS.items() if v == r["kind"]))
        for entry, value in ((self.r_every, str(r["every"])), (self.r_at, ", ".join(r["times"])),
                             (self.r_from, r["window"][0]), (self.r_to, r["window"][1])):
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
            except ValueError:
                raise ValueError("Write times like 09:00 (several: 09:00, 13:00).") from None
            days = [i for i, d in enumerate(self.r_days) if d.get()]
            if kind == "times" and (not times or not days):
                raise ValueError("Pick at least one day and one time.")
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
               "quotes": self.r_quotes.get("1.0", "end").strip()}
        if self.editing:
            new["on"] = self.editing["on"]
            items = [new if x["id"] == new["id"] else x for x in items]
        else:
            items.append(new)
        reminders.save(self.db, reminders.CUSTOM_KEY, items)
        self.editor.pack_forget()
        self.refresh()

    def _toggle(self, r: dict, on):
        items = reminders.load(self.db, reminders.CUSTOM_KEY, [])
        reminders.save(self.db, reminders.CUSTOM_KEY, [{**x, "on": bool(on)} if x["id"] == r["id"] else x
                                                       for x in items])

    def _delete(self, r: dict):
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
