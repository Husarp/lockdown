"""Modes page: the mode that's on (with its timer), every mode as a card (Start / Edit), and the mode editor
(categories to block, extra sites / apps / groups, Pomodoro, mute, schedule). Changes apply at once."""
from datetime import datetime, timedelta

import customtkinter as ctk

import modes
from gui import categories, icons, theme
from gui.components import Card, Rows, Segmented, eyebrow
from gui.reminders_ui import RemindersView
from gui.rule_editors import MUTED, WindowRow
from gui.target_picker import TargetPicker
from gui.widgets import ConfirmButton
from rules import days_text, load_schedule, make_schedule, parse_hhmm
from trusted_time import now_from_db

TICK_MS = 1000
DURATIONS = {"30 min": 30, "1 h": 60, "2 h": 120, "Until...": None, "Until I stop": 0}
COLUMNS = 3


def left_text(until: datetime, now: datetime) -> str:
    sec = max(0, int((until - now).total_seconds()))
    h, rest = divmod(sec, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


def state_text(state: dict | None, now: datetime) -> str:
    if not state:
        return "No mode is on."
    mode, phase, until = state["mode"], state["phase"], state["until"]
    if phase:
        name, end, rnd = phase
        return f"{name.capitalize()} - {left_text(end, now)} left (round {rnd} of {mode['pomodoro']['rounds']})"
    if state["scheduled"]:
        return f"On by its schedule" + (f" until {until:%H:%M}" if until else "")
    return f"Until {until:%H:%M} - {left_text(until, now)} left" if until else "Until you stop it"


def _extra_chip(parent):
    f = ctk.CTkFrame(parent, fg_color=theme.SURFACE2, border_width=1, border_color=theme.BORDER, corner_radius=4)
    f.name = ctk.CTkLabel(f, text="", compound="left", height=26)
    f.name.pack(side="left", padx=(8, 2))
    f.remove = ctk.CTkButton(f, text="×", width=22, height=22, fg_color="transparent", hover_color=theme.BORDER,
                             text_color=MUTED)
    f.remove.pack(side="left", padx=(0, 4))
    return f


class ModeEditor(ctk.CTkFrame):
    def __init__(self, master, page):
        super().__init__(master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER)
        self.page, self.db = page, page.db
        self.mode: dict | None = None
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 8))
        ctk.CTkFrame(head, width=3, height=24, corner_radius=0, fg_color=theme.ACCENT).pack(side="left", padx=(0, 10))
        self.title = ctk.CTkLabel(head, text="", font=theme.body(18, "bold"))
        self.title.pack(side="left")
        ctk.CTkButton(head, text="Done", width=70, command=self._save).pack(side="right")
        ctk.CTkButton(head, text="Cancel", width=70, **theme.OUTLINE, command=page.close_editor).pack(side="right",
                                                                                                    padx=6)
        self.delete_btn = ConfirmButton(head, self._delete, text="Delete mode", confirm_text="Confirm delete",
                                        width=110)
        line = ctk.CTkFrame(self, fg_color="transparent")
        line.pack(fill="x", padx=16)
        ctk.CTkLabel(line, text="Name", width=60, anchor="w").pack(side="left")
        self.name = ctk.CTkEntry(line, placeholder_text="e.g. Gaming-free evening")
        self.name.pack(side="left", fill="x", expand=True)

        eyebrow(self, "Block these categories").pack(anchor="w", padx=16, pady=(14, 4))
        self.cat_box = ctk.CTkFrame(self, fg_color="transparent")
        self.cat_box.pack(anchor="w", padx=16)
        ctk.CTkLabel(self, text="Categories are set on Screen Time > Apps / Websites; blocked sites and apps count as "
                                "Distracting unless you chose otherwise.", text_color=MUTED, font=theme.body(11),
                     wraplength=640, justify="left").pack(anchor="w", padx=16)

        eyebrow(self, "Also block").pack(anchor="w", padx=16, pady=(14, 4))
        self.chips = Rows(self, _extra_chip, "Nothing else.", {"side": "left", "padx": (0, 6), "pady": 2})
        self.chips.frame.pack_configure(padx=16)
        add = ctk.CTkFrame(self, fg_color="transparent")
        add.pack(anchor="w", padx=16, pady=(6, 0))
        self.pick = ctk.CTkOptionMenu(add, values=["+ From your blocklist..."], width=220, command=self._picked)
        self.pick.pack(side="left")
        ctk.CTkButton(add, text="+ Other site or app", width=150, **theme.OUTLINE,
                      command=lambda: self.picker.pack(anchor="w", padx=16, pady=(6, 0), after=add)).pack(
            side="left", padx=8)
        self.picker = TargetPicker(self, self.db)
        ctk.CTkButton(self.picker.buttons, text="Add", width=70, command=self._add_extra).pack(side="left", padx=4)

        eyebrow(self, "Options").pack(anchor="w", padx=16, pady=(14, 4))
        self.pomo_on = ctk.CTkSwitch(self, text="Pomodoro (blocked in focus rounds, free in breaks)",
                                     command=self._toggle_pomo)
        self.pomo_on.pack(anchor="w", padx=16, pady=2)
        self.pomo = ctk.CTkFrame(self, fg_color="transparent")
        self.pomo_entries = {}
        for key, label in (("work", "focus min"), ("break", "break min"), ("rounds", "rounds"),
                           ("long", "long break min")):
            e = ctk.CTkEntry(self.pomo, width=50)
            e.pack(side="left")
            ctk.CTkLabel(self.pomo, text=label).pack(side="left", padx=(4, 14))
            self.pomo_entries[key] = e
        self.mute = ctk.CTkSwitch(self, text="Mute Lockdown's notifications while it's on")
        self.mute.pack(anchor="w", padx=16, pady=2)
        self.auto = ctk.CTkSwitch(self, text="Turn on automatically at these times", command=self._toggle_auto)
        self.auto.pack(anchor="w", padx=16, pady=2)
        self.windows_box = ctk.CTkFrame(self, fg_color="transparent")
        self.rows: list[WindowRow] = []
        ctk.CTkButton(self.windows_box, text="+ Add time window", width=140, **theme.OUTLINE,
                      command=lambda: self._add_window([0, 1, 2, 3, 4], "09:00", "17:00")).pack(side="bottom",
                                                                                                anchor="w")
        self.error = ctk.CTkLabel(self, text="", text_color=theme.DANGER)
        self.error.pack(anchor="w", padx=16, pady=(4, 10))

    # ---------- load ----------

    def load(self, mode: dict | None):
        self.mode = dict(mode) if mode else {"id": None, "name": "", "builtin": False, "categories": ["distracting"],
                                             "items": [], "groups": [], "extra": [], "mute": False,
                                             "pomodoro": None, "schedule": None}
        m = self.mode
        self.title.configure(text=f"Edit {m['name']}" if mode else "New mode")
        self.name.configure(state="normal")
        self.name.delete(0, "end")
        self.name.insert(0, m["name"])
        if m["builtin"]:
            self.name.configure(state="disabled")
        if mode and not m["builtin"]:
            self.delete_btn.pack(side="right")
        else:
            self.delete_btn.pack_forget()
        for w in self.cat_box.winfo_children():
            w.destroy()
        self.cat_checks = {}
        for c in categories.load(self.db):
            box = ctk.CTkCheckBox(self.cat_box, text=c["name"], fg_color=c["color"], width=110)
            box.pack(side="left", padx=(0, 10))
            if c["key"] in m["categories"]:
                box.select()
            self.cat_checks[c["key"]] = box
        self.items, self.groups = self.db.list_items(), self.db.list_groups()
        choices = [f"Group: {g['name']}" for g in self.groups] + [i["display_name"] for i in self.items]
        self.pick.configure(values=choices or ["(your blocklist is empty)"])
        self.pick.set("+ From your blocklist...")
        self._render_chips()
        self.picker.reset()
        self.picker.pack_forget()
        p = m["pomodoro"] or modes.DEFAULT_POMODORO
        for key, e in self.pomo_entries.items():
            e.delete(0, "end")
            e.insert(0, str(p[key]))
        self.pomo_on.select() if m["pomodoro"] else self.pomo_on.deselect()
        self._toggle_pomo()
        self.mute.select() if m["mute"] else self.mute.deselect()
        for row in self.rows:
            row.destroy()
        self.rows = []
        if m["schedule"]:
            for w in load_schedule(m["schedule"])["windows"]:
                self._add_window(w["days"], w["start"], w["end"])
            self.auto.select()
        else:
            self._add_window([0, 1, 2, 3, 4], "09:00", "17:00")
            self.auto.deselect()
        self._toggle_auto()
        self.error.configure(text="")

    def _render_chips(self):
        m = self.mode
        entries = [("group", g) for g in self.groups if g["id"] in m["groups"]] + \
                  [("item", i) for i in self.items if i["id"] in m["items"]] + [("extra", e) for e in m["extra"]]
        for chip, (kind, obj) in zip(self.chips.take(len(entries)), entries):
            if kind == "group":
                chip.name.configure(text=f" Group: {obj['name']}", image=icons.get(obj["name"], 16))
            elif kind == "item":
                chip.name.configure(text=f" {obj['display_name']}", image=icons.for_item(obj, 16))
            else:
                item = {"item_type": obj["kind"], "target": " ".join(obj["targets"]), "app_path": obj.get("app_path")}
                chip.name.configure(text=f" {obj['name']}", image=icons.for_item(item, 16))
            chip.remove.configure(command=lambda k=kind, o=obj: self._remove(k, o))

    def _picked(self, label: str):
        if label.startswith("Group: "):
            g = next(g for g in self.groups if f"Group: {g['name']}" == label)
            if g["id"] not in self.mode["groups"]:
                self.mode["groups"] = self.mode["groups"] + [g["id"]]
        else:
            item = next((i for i in self.items if i["display_name"] == label), None)
            if item and item["id"] not in self.mode["items"]:
                self.mode["items"] = self.mode["items"] + [item["id"]]
        self.pick.set("+ From your blocklist...")
        self._render_chips()

    def _add_extra(self):
        self.picker.entry.hide()
        try:
            t = self.picker.get()
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        self.mode["extra"] = self.mode["extra"] + [{"kind": t["kind"], "name": t["name"], "targets": t["targets"],
                                                   "block_type": t["block_type"], "app_path": t["app_path"]}]
        self.picker.reset()
        self.picker.pack_forget()
        self.error.configure(text="")
        self._render_chips()

    def _remove(self, kind: str, obj):
        if kind == "group":
            self.mode["groups"] = [g for g in self.mode["groups"] if g != obj["id"]]
        elif kind == "item":
            self.mode["items"] = [i for i in self.mode["items"] if i != obj["id"]]
        else:
            self.mode["extra"] = [e for e in self.mode["extra"] if e is not obj]
        self._render_chips()

    def _toggle_pomo(self):
        if self.pomo_on.get():
            self.pomo.pack(anchor="w", padx=(56, 16), pady=(0, 6), after=self.pomo_on)
        else:
            self.pomo.pack_forget()

    def _add_window(self, days, start, end):
        row = WindowRow(self.windows_box, days, start, end, on_remove=lambda r: (self.rows.remove(r), r.destroy()))
        row.pack(anchor="w", pady=2)
        self.rows.append(row)

    def _toggle_auto(self):
        if self.auto.get():
            self.windows_box.pack(anchor="w", padx=(56, 16), pady=(0, 6), after=self.auto)
        else:
            self.windows_box.pack_forget()

    # ---------- save ----------

    def _save(self):
        m = self.mode
        try:
            name = self.name.get().strip() or m["name"]
            if not name:
                raise ValueError("Give the mode a name.")
            pomodoro = None
            if self.pomo_on.get():
                try:
                    pomodoro = {k: int(e.get().strip()) for k, e in self.pomo_entries.items()}
                except ValueError:
                    raise ValueError("Pomodoro numbers must be whole numbers.") from None
                if not all(1 <= v <= 240 for v in pomodoro.values()):
                    raise ValueError("Pomodoro numbers must be between 1 and 240.")
            schedule = None
            if self.auto.get():
                windows = [w for w in (r.value() for r in self.rows) if w[0]]
                if not windows:
                    raise ValueError("Turn on at least one day for the automatic times.")
                schedule = make_schedule("block", windows)
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        all_modes = modes.load(self.db)
        m = {**m, "name": name, "categories": [k for k, b in self.cat_checks.items() if b.get()],
             "pomodoro": pomodoro, "mute": bool(self.mute.get()), "schedule": schedule}
        if m["id"] is None:
            m["id"] = modes.new_id(all_modes)
            all_modes.append(m)
        else:
            all_modes = [m if x["id"] == m["id"] else x for x in all_modes]
        modes.save(self.db, all_modes)
        self.page.close_editor()
        self.page.refresh()

    def _delete(self):
        modes.save(self.db, [x for x in modes.load(self.db) if x["id"] != self.mode["id"]])
        state = modes.active(self.db, now_from_db(self.db))
        if state and state["mode"]["id"] == self.mode["id"] and not state["scheduled"]:
            self.db.set_setting(modes.ACTIVE_KEY, "")
        self.page.close_editor()
        self.page.refresh()


def _mode_card(parent):
    c = Card(parent)
    c.name = ctk.CTkLabel(c.body, text="", font=theme.semi(15), anchor="w")
    c.name.pack(anchor="w", pady=(4, 0))
    c.what = ctk.CTkLabel(c.body, text="", text_color=MUTED, font=theme.body(11), anchor="w", justify="left",
                          wraplength=230)
    c.what.pack(anchor="w")
    c.extra = ctk.CTkLabel(c.body, text="", text_color=MUTED, font=theme.body(11), anchor="w", justify="left",
                           wraplength=230)
    c.extra.pack(anchor="w")
    buttons = ctk.CTkFrame(c.body, fg_color="transparent")
    buttons.pack(anchor="w", pady=(8, 0))
    c.start = ctk.CTkButton(buttons, text="Start", width=80)
    c.start.pack(side="left")
    c.edit = ctk.CTkButton(buttons, text="Edit", width=70, **theme.SECONDARY)
    c.edit.pack(side="left", padx=6)
    return c


class ModesPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=30, pady=(12, 6))
        ctk.CTkLabel(head, text="Modes", font=theme.page_title()).pack(side="left")
        self.new_btn = ctk.CTkButton(head, text="+ New mode", width=120, command=lambda: self.open_editor(None))
        self.new_btn.pack(side="right")
        self.tab = Segmented(self, ["Modes", "Reminders"], command=lambda v: self._switch())
        self.tab.set("Modes")
        self.tab.pack(anchor="w", padx=30, pady=(0, 10))
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=(20, 12), pady=(0, 14))
        self.reminders = RemindersView(self, self)

        self.now_card = Card(self.body, "Now")
        self.now_card.pack(fill="x", pady=(0, 12))
        line = ctk.CTkFrame(self.now_card.body, fg_color="transparent")
        line.pack(fill="x")
        self.now_name = ctk.CTkLabel(line, text="", font=theme.numeral(28))
        self.now_name.pack(side="left")
        self.stop_btn = ctk.CTkButton(line, text="Stop", width=90, **theme.OUTLINE, command=self._stop)
        self.stop_btn.pack(side="right")
        self.now_text = ctk.CTkLabel(self.now_card.body, text="", text_color=MUTED, anchor="w")
        self.now_text.pack(anchor="w")

        self.start_panel = Card(self.body, "Start")
        sp = self.start_panel.body
        self.duration = Segmented(sp, list(DURATIONS), command=lambda v: self._duration_changed())
        self.duration.set("1 h")
        self.duration.pack(anchor="w")
        self.until = ctk.CTkEntry(sp, width=70, placeholder_text="17:00")
        self.lock = ctk.CTkCheckBox(sp, text="Lock until it ends (can't be stopped early; the emergency unlock "
                                             "still works)")
        self.lock.pack(anchor="w", pady=(8, 0))
        self.start_error = ctk.CTkLabel(sp, text="", text_color=theme.DANGER)
        self.start_error.pack(anchor="w")
        buttons = ctk.CTkFrame(sp, fg_color="transparent")
        buttons.pack(anchor="w")
        self.start_btn = ctk.CTkButton(buttons, text="Start", width=90, command=self._start)
        self.start_btn.pack(side="left")
        ctk.CTkButton(buttons, text="Cancel", width=80, **theme.OUTLINE,
                      command=lambda: self.start_panel.pack_forget()).pack(side="left", padx=6)
        self.starting: dict | None = None

        self.editor = ModeEditor(self.body, self)
        eyebrow(self.body, "Your modes").pack(anchor="w", pady=(4, 6))
        self.grid_box = ctk.CTkFrame(self.body, fg_color="transparent")
        self.grid_box.pack(fill="x")
        for col in range(COLUMNS):
            self.grid_box.grid_columnconfigure(col, weight=1, uniform="m")
        self.cards: list = []
        self.after(TICK_MS, self._tick)

    # ---------- showing ----------

    def on_show(self):
        self.refresh()

    def _switch(self):
        reminders_tab = self.tab.get() == "Reminders"
        (self.body if reminders_tab else self.reminders).pack_forget()
        (self.reminders if reminders_tab else self.body).pack(fill="both", expand=True, padx=(20, 12), pady=(0, 14))
        if reminders_tab:
            self.new_btn.pack_forget()
        else:
            self.new_btn.pack(side="right")
        self.refresh()

    def refresh(self):
        if self.tab.get() == "Reminders":
            self.reminders.refresh()
            return
        now = now_from_db(self.db)
        all_modes = modes.load(self.db)
        names = categories.names_of(categories.load(self.db))
        while len(self.cards) < len(all_modes):
            self.cards.append(_mode_card(self.grid_box))
        for i, card in enumerate(self.cards):
            if i >= len(all_modes):
                card.grid_forget()
                continue
            m = all_modes[i]
            card.grid(row=i // COLUMNS, column=i % COLUMNS, sticky="nsew", padx=6, pady=6)
            card.name.configure(text=m["name"])
            card.what.configure(text=modes.describe(m, names) + (" · mutes notifications" if m["mute"] else ""))
            extra = []
            if m["pomodoro"]:
                p = m["pomodoro"]
                extra.append(f"Pomodoro {p['work']} / {p['break']} min × {p['rounds']}, then {p['long']} min")
            if m["schedule"]:
                s = load_schedule(m["schedule"])
                extra.append("Automatically: " + "; ".join(f"{days_text(w['days'])} {w['start']}-{w['end']}"
                                                            for w in s["windows"]))
            card.extra.configure(text="\n".join(extra))
            card.start.configure(command=lambda m=m: self._open_start(m))
            card.edit.configure(command=lambda m=m: self.open_editor(m))
        self._update_now(now)

    def _update_now(self, now):
        state = modes.active(self.db, now)
        self.now_name.configure(text=state["mode"]["name"] if state else "No mode")
        self.now_text.configure(text=state_text(state, now))
        if state and not state["scheduled"]:
            locked = state["locked"]
            self.stop_btn.configure(state="disabled" if locked else "normal",
                                    text=f"Locked until {state['until']:%H:%M}" if locked else "Stop",
                                    width=160 if locked else 90)
            self.stop_btn.pack(side="right")
        else:
            self.stop_btn.pack_forget()

    def _tick(self):
        if getattr(self.app, "current_page", None) == "Modes":
            self._update_now(now_from_db(self.db))
        self.after(TICK_MS, self._tick)

    # ---------- start / stop ----------

    def _open_start(self, mode: dict):
        self.starting = mode
        self.start_panel.title.configure(text=f"Start {mode['name']}")
        pomodoro = bool(mode["pomodoro"])
        self.duration.pack_forget() if pomodoro else self.duration.pack(anchor="w", before=self.lock)
        self._duration_changed()
        self.lock.deselect()
        self.start_error.configure(text=(f"Runs {int(modes.pomodoro_length(mode['pomodoro']).total_seconds() // 60)}"
                                         " min (all rounds).") if pomodoro else "", text_color=MUTED if pomodoro
                                   else theme.DANGER)
        self.start_panel.pack(fill="x", pady=(0, 12), after=self.now_card)

    def _duration_changed(self):
        if self.duration.get() == "Until..." and not (self.starting or {}).get("pomodoro"):
            self.until.pack(anchor="w", pady=(6, 0), after=self.duration)
        else:
            self.until.pack_forget()
        self.lock.configure(state="disabled" if self.duration.get() == "Until I stop" and
                            not (self.starting or {}).get("pomodoro") else "normal")

    def _start(self):
        mode, now = self.starting, now_from_db(self.db)
        if mode["pomodoro"]:
            until = now + modes.pomodoro_length(mode["pomodoro"])
        else:
            choice = self.duration.get()
            if choice == "Until...":
                try:
                    t = parse_hhmm(self.until.get())
                except ValueError:
                    self.start_error.configure(text="Write the time like 17:00.", text_color=theme.DANGER)
                    return
                until = datetime.combine(now.date(), t)
                if until <= now:
                    until += timedelta(days=1)
            else:
                until = now + timedelta(minutes=DURATIONS[choice]) if DURATIONS[choice] else None
        try:
            self.app.start_mode(mode, until, bool(self.lock.get()))
        except ValueError as e:
            self.start_error.configure(text=str(e), text_color=theme.DANGER)
            return
        self.start_panel.pack_forget()
        self.refresh()

    def _stop(self):
        try:
            self.app.stop_mode()
        except ValueError as e:
            self.now_text.configure(text=str(e))
            return
        self.refresh()

    # ---------- editor ----------

    def open_editor(self, mode: dict | None):
        self.editor.load(mode)
        self.editor.pack(fill="x", pady=(0, 12), after=self.now_card)
        self.body._parent_canvas.yview_moveto(0)

    def close_editor(self):
        self.editor.pack_forget()
