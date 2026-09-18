"""Blocking page.

Each rule tab (By Hours / By Limit / Permanent / Temporary) has its own add/edit form and lists the sites
that have that kind of rule. "All" is an overview of every site with all its rules; Edit jumps to the
rule's tab, Remove deletes the whole site (after a confirmation). All edits go into the draft (see draft.py).
"""
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from blocker.hosts import normalize_host
from gui import icons
from gui.site_picker import PopularSitesPopup, SiteEntry
from importer.popular import POPULAR_SITES
from rules import ALLOW, BLOCK, DAY_NAMES, describe_rule, item_block, load_schedule, make_schedule
from trusted_time import now_from_db

TABS = ["All", "By Hours", "By Limit", "By Switches", "Permanent", "Temporary"]
TAB_RULE = {"By Hours": "scheduled", "By Limit": "time_limit", "Permanent": "permanent", "Temporary": "temporary"}
RULE_TAB = {v: k for k, v in TAB_RULE.items()}
RULE_NAME = {"scheduled": "hours rule", "time_limit": "daily limit", "permanent": "permanent block",
             "temporary": "temporary block"}
DURATIONS = {"15 min": 15, "30 min": 30, "1 hour": 60, "2 hours": 120, "3 hours": 180,
             "4 hours": 240, "8 hours": 480, "24 hours": 1440}
MODES = {"Allow only during": ALLOW, "Block during": BLOCK}
ALERTS = {"Default": None, "On": "on", "Off": "off"}
REFRESH_MS = 30_000
HIGHLIGHT_MS = 2500
MUTED = "gray60"
ERROR = "#f85149"
GREEN, ORANGE = "#3fb950", "#d29922"
HIGHLIGHT = ("#dbe9ff", "#1f3a5f")
COLS = [220, 270, 140]   # name + hostnames, rules, status (then action buttons)


def popular_hosts(host: str) -> tuple[str, list[str]] | None:
    for sites in POPULAR_SITES.values():
        for name, hostnames in sites.items():
            if host in hostnames:
                return name, hostnames
    return None


def guess_name(host: str) -> str:
    entry = popular_hosts(host)
    return entry[0] if entry else host.split(".")[-2].capitalize()


# ---------------------------------------------------------------- rule option editors

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
        self.rows: list[WindowRow] = []
        self.load(None)

    def _add_row(self, days, start, end):
        row = WindowRow(self.rows_box, days, start, end, self._remove_row)
        row.pack(anchor="w", pady=2)
        self.rows.append(row)

    def _remove_row(self, row):
        row.destroy()
        self.rows.remove(row)

    def load(self, schedule_json: str | None):
        for row in self.rows:
            row.destroy()
        self.rows = []
        if schedule_json:
            s = load_schedule(schedule_json)
            self.mode.set(next(k for k, v in MODES.items() if v == s["mode"]))
            for w in s["windows"]:
                self._add_row(w["days"], w["start"], w["end"])
        else:
            self.mode.set("Allow only during")
            self._add_row([0, 1, 2, 3, 4], "09:00", "17:00")

    def value(self) -> str:
        return make_schedule(MODES[self.mode.get()], [r.value() for r in self.rows])


class LimitEditor(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Daily limit").pack(side="left")
        self.minutes = ctk.CTkEntry(self, width=64)
        self.minutes.pack(side="left", padx=8)
        ctk.CTkLabel(self, text="minutes - counted while the site is the active tab in Chrome/Edge/Brave/Firefox "
                                "(not while you're away 15+ min); resets at midnight",
                     text_color=MUTED, wraplength=560, justify="left").pack(side="left")
        self.load(None)

    def load(self, minutes: int | None):
        self.minutes.delete(0, "end")
        self.minutes.insert(0, str(minutes or 30))

    def value(self) -> int:
        try:
            minutes = int(self.minutes.get())
        except ValueError:
            minutes = 0
        if not 1 <= minutes <= 1440:
            raise ValueError("Daily limit must be 1-1440 minutes.")
        return minutes


class TemporaryEditor(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text="Block for").pack(side="left")
        self.duration = ctk.CTkOptionMenu(self, values=list(DURATIONS), width=110)
        self.duration.pack(side="left", padx=8)
        ctk.CTkLabel(self, text="starting when saved", text_color=MUTED).pack(side="left")
        self.load(None)

    def load(self, minutes: int | None):
        self.duration.set(next((k for k, v in DURATIONS.items() if v == minutes), "1 hour"))

    def value(self) -> int:
        return DURATIONS[self.duration.get()]


# ---------------------------------------------------------------- shared list helpers

def status_of(page, item: dict, now, usage: dict) -> dict:
    if page.draft.is_unsaved(item["id"]):
        return {"text": "○ Not applied\n(unsaved)", "text_color": ORANGE}
    if not item_block(item["rules"], now, usage.get(item["id"], 0)):
        return {"text": "○ Allowed now", "text_color": MUTED}
    if not page.app.service_running:
        return {"text": "○ Pending - service\nnot running", "text_color": ORANGE}
    return {"text": "● Blocked now", "text_color": GREEN}


def make_row(parent) -> ctk.CTkFrame:
    row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=6)
    row.pack(fill="x", pady=1)
    for col, width in enumerate(COLS):
        row.grid_columnconfigure(col, minsize=width)
    return row


def name_cell(row, item):
    """Icon + name, with the hostnames in small text underneath."""
    host = item["target"].split()[0]
    cell = ctk.CTkFrame(row, fg_color="transparent")
    cell.grid(row=0, column=0, padx=8, pady=6, sticky="w")
    ctk.CTkLabel(cell, text=f"  {item['display_name']}", image=icons.get(host, 18), compound="left",
                 font=ctk.CTkFont(weight="bold"), anchor="w").pack(anchor="w")
    ctk.CTkLabel(cell, text=", ".join(item["target"].split()), text_color=MUTED, font=ctk.CTkFont(size=11),
                 wraplength=COLS[0] - 16, justify="left", anchor="w").pack(anchor="w")


def header_row(parent, titles):
    row = make_row(parent)
    for col, title in enumerate(titles):
        ctk.CTkLabel(row, text=title, text_color=MUTED).grid(row=0, column=col, padx=8, pady=(6, 0), sticky="w")


def confirm(title: str, message: str) -> bool:
    return messagebox.askyesno(title, message, icon="warning")


# ---------------------------------------------------------------- tabs

class RuleTab(ctk.CTkScrollableFrame):
    """Add/edit form + list for one rule type."""

    def __init__(self, master, page, rule_type: str):
        super().__init__(master, fg_color="transparent")
        self.page, self.draft, self.rule_type = page, page.draft, rule_type
        self.edit_id: int | None = None
        self.rows: dict[int, ctk.CTkFrame] = {}
        self._build_form()
        self.list_title = ctk.CTkLabel(self, font=ctk.CTkFont(size=16, weight="bold"))
        self.list_title.pack(anchor="w", padx=10, pady=(4, 6))
        self.list_box = ctk.CTkFrame(self)
        self.list_box.pack(fill="x")

    def _build_form(self):
        box = ctk.CTkFrame(self)
        box.pack(fill="x", pady=(0, 12))
        self.form_title = ctk.CTkLabel(box, font=ctk.CTkFont(size=16, weight="bold"))
        self.form_title.pack(anchor="w", padx=16, pady=(12, 8))
        inputs = ctk.CTkFrame(box, fg_color="transparent")
        inputs.pack(anchor="w", padx=16)
        self.site_entry = SiteEntry(inputs, self.page.app.db, self._fill, width=260,
                                    placeholder_text="reddit.com or a full URL")
        self.site_entry.pack(side="left", padx=(0, 8))
        self.site_entry.bind("<Return>", lambda e: self._submit(), add="+")
        self.name_entry = ctk.CTkEntry(inputs, width=180, placeholder_text="Display name")
        self.name_entry.pack(side="left", padx=8)
        self.popular_btn = ctk.CTkButton(inputs, text="+ Popular sites", width=120, fg_color="transparent",
                                         border_width=1, command=lambda: PopularSitesPopup(self, self._fill))
        self.popular_btn.pack(side="left", padx=8)
        self.submit_btn = ctk.CTkButton(inputs, width=90, command=self._submit)
        self.submit_btn.pack(side="left", padx=8)
        self.cancel_btn = ctk.CTkButton(inputs, text="Cancel", width=80, fg_color="transparent", border_width=1,
                                        command=self.reset_form)

        editor_cls = {"scheduled": HoursEditor, "time_limit": LimitEditor, "temporary": TemporaryEditor}.get(self.rule_type)
        self.editor = editor_cls(box) if editor_cls else None
        if self.editor:
            self.editor.pack(anchor="w", padx=16, pady=(10, 0))
        self.error = ctk.CTkLabel(box, text="", text_color=ERROR)
        self.error.pack(anchor="w", padx=16, pady=(4, 8))
        self.reset_form()

    def _fill(self, name: str, host: str):
        """A suggestion or popular site was picked."""
        if self.edit_id is not None:
            return
        self.site_entry.delete(0, "end")
        self.site_entry.insert(0, host)
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, name)

    def reset_form(self):
        self.edit_id = None
        self.form_title.configure(text="Add Site")
        self.submit_btn.configure(text="+ Add")
        self.cancel_btn.pack_forget()
        self.popular_btn.pack(side="left", padx=8, before=self.submit_btn)
        self.site_entry.configure(state="normal")
        self.site_entry.delete(0, "end")
        self.name_entry.delete(0, "end")
        self.error.configure(text="")
        if self.editor:
            self.editor.load(None)

    def edit(self, item_id: int):
        item = self.draft.items[item_id]
        rule = next(r for r in item["rules"] if r["rule_type"] == self.rule_type)
        self.edit_id = item_id
        self.form_title.configure(text=f"Edit {item['display_name']}")
        self.submit_btn.configure(text="Update")
        self.popular_btn.pack_forget()
        self.cancel_btn.pack(side="left", padx=8)
        self.site_entry.configure(state="normal")
        self.site_entry.delete(0, "end")
        self.site_entry.insert(0, item["target"].split()[0])
        self.site_entry.configure(state="disabled")
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, item["display_name"])
        self.error.configure(text="")
        if self.rule_type == "scheduled":
            self.editor.load(rule["schedule"])
        elif self.rule_type == "time_limit":
            self.editor.load(rule["daily_limit_min"])
        elif self.rule_type == "temporary":
            self.editor.load(rule.get("duration_min"))
        self._parent_canvas.yview_moveto(0)
        self.highlight(item_id)

    def _rule(self) -> dict:
        if self.rule_type == "scheduled":
            return {"rule_type": "scheduled", "schedule": self.editor.value()}
        if self.rule_type == "time_limit":
            return {"rule_type": "time_limit", "daily_limit_min": self.editor.value()}
        if self.rule_type == "temporary":
            return {"rule_type": "temporary", "duration_min": self.editor.value()}
        return {"rule_type": "permanent"}

    def _submit(self):
        self.site_entry.hide()
        try:
            rule = self._rule()
            if self.edit_id is None:
                host = normalize_host(self.site_entry.get())
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        name = self.name_entry.get().strip()
        if self.edit_id is not None and self.edit_id not in self.draft.items:  # expired meanwhile
            self.reset_form()
            return
        if self.edit_id is not None:
            # identical values leave the draft clean (Save stays grey)
            self.draft.set_rule(self.edit_id, rule, name or self.draft.items[self.edit_id]["display_name"])
            self.reset_form()
            return
        existing = next((i for i in self.draft.items.values() if host in i["target"].split()), None)
        if existing and any(r["rule_type"] == self.rule_type for r in existing["rules"]):
            self.error.configure(text=f"{existing['display_name']} already has a {RULE_NAME[self.rule_type]} - use Edit.")
            return
        if existing is None:
            entry = popular_hosts(host)   # known site: block all of its hostnames
            existing = self.draft.add_item(name or guess_name(host), entry[1] if entry else [host],
                                           "popular" if entry else "manual")
        self.draft.set_rule(existing["id"], rule)
        self.reset_form()

    def highlight(self, item_id: int):
        row = self.rows.get(item_id)
        if row and row.winfo_exists():
            row.configure(fg_color=HIGHLIGHT)
            self.after(HIGHLIGHT_MS, lambda: row.winfo_exists() and row.configure(fg_color="transparent"))

    def refresh(self, now, usage):
        items = [i for i in self.draft.sorted_items() if any(r["rule_type"] == self.rule_type for r in i["rules"])]
        self.list_title.configure(text=f"{RULE_TAB[self.rule_type]} ({len(items)})")
        for w in self.list_box.winfo_children():
            w.destroy()
        self.rows = {}
        if not items:
            ctk.CTkLabel(self.list_box, text="Nothing here yet.", text_color=MUTED).pack(anchor="w", padx=16, pady=12)
            return
        header_row(self.list_box, ["Name", "Rule", "Status"])
        for item in items:
            rule = next(r for r in item["rules"] if r["rule_type"] == self.rule_type)
            row = make_row(self.list_box)
            self.rows[item["id"]] = row
            name_cell(row, item)
            ctk.CTkLabel(row, text=describe_rule(rule, now, usage.get(item["id"], 0)), wraplength=COLS[1] - 16,
                         justify="left", anchor="w").grid(row=0, column=1, padx=8, sticky="w")
            ctk.CTkLabel(row, **status_of(self.page, item, now, usage), justify="left").grid(
                row=0, column=2, padx=8, sticky="w")
            ctk.CTkButton(row, text="Edit", width=60, command=lambda i=item["id"]: self.edit(i)).grid(
                row=0, column=3, padx=4)
            ctk.CTkButton(row, text="Remove", width=70, fg_color="transparent", border_width=1,
                          command=lambda it=item: self._remove(it)).grid(row=0, column=4, padx=4)

    def _remove(self, item):
        if confirm("Remove", f"Remove the {RULE_NAME[self.rule_type]} from {item['display_name']}?"):
            if self.edit_id == item["id"]:
                self.reset_form()
            self.draft.remove_rule(item["id"], self.rule_type)


class AllTab(ctk.CTkScrollableFrame):
    """Overview of every site with all its rules. Edit jumps to the rule's tab."""

    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.draft = page, page.draft
        self.title = ctk.CTkLabel(self, font=ctk.CTkFont(size=16, weight="bold"))
        self.title.pack(anchor="w", padx=10, pady=(4, 2))
        ctk.CTkLabel(self, text="To add sites, open the tab for the kind of block you want (By Hours, By Limit, ...).",
                     text_color=MUTED).pack(anchor="w", padx=10, pady=(0, 8))
        self.list_box = ctk.CTkFrame(self)
        self.list_box.pack(fill="x")

    def refresh(self, now, usage):
        items = self.draft.sorted_items()
        self.title.configure(text=f"All Blocked Sites ({len(items)})")
        for w in self.list_box.winfo_children():
            w.destroy()
        if not items:
            ctk.CTkLabel(self.list_box, text="Nothing blocked yet.", text_color=MUTED).pack(anchor="w", padx=16, pady=12)
            return
        header_row(self.list_box, ["Name", "Rules", "Status", "Alerts"])
        for item in items:
            row = make_row(self.list_box)
            name_cell(row, item)
            rules_box = ctk.CTkFrame(row, fg_color="transparent")
            rules_box.grid(row=0, column=1, padx=8, sticky="w")
            for r, rule in enumerate(item["rules"]):
                ctk.CTkLabel(rules_box, text=describe_rule(rule, now, usage.get(item["id"], 0)),
                             wraplength=COLS[1] - 16, justify="left", anchor="w").grid(row=r, column=0, sticky="w", pady=1)
            ctk.CTkLabel(row, **status_of(self.page, item, now, usage), justify="left").grid(
                row=0, column=2, padx=8, sticky="w")
            alerts = ctk.CTkOptionMenu(row, values=list(ALERTS), width=90,
                                       command=lambda v, i=item["id"]: self.draft.set_notify(i, ALERTS[v]))
            alerts.set(next(k for k, v in ALERTS.items() if v == item["notify"]))
            alerts.grid(row=0, column=3, padx=4)
            editable = [r["rule_type"] for r in item["rules"] if r["rule_type"] in RULE_TAB]
            edit_btn = ctk.CTkButton(row, text="Edit ▾" if len(editable) > 1 else "Edit", width=70)
            edit_btn.configure(command=lambda b=edit_btn, i=item["id"], t=editable: self._edit(b, i, t))
            edit_btn.grid(row=0, column=4, padx=4)
            ctk.CTkButton(row, text="Remove", width=70, fg_color="transparent", border_width=1,
                          command=lambda it=item: self._remove(it)).grid(row=0, column=5, padx=4)

    def _edit(self, button, item_id: int, rule_types: list[str]):
        """One rule: jump straight to its tab. Several: ask which one with a small menu under the button."""
        if len(rule_types) == 1:
            self.page.edit_rule(item_id, rule_types[0])
            return
        menu = tk.Menu(self, tearoff=False)
        for t in rule_types:
            menu.add_command(label=f"Edit {RULE_NAME[t]}", command=lambda t=t: self.page.edit_rule(item_id, t))
        menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())

    def _remove(self, item):
        if confirm("Remove site", f"Remove {item['display_name']} and all its rules?"):
            self.draft.remove_item(item["id"])


class BlockingPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.draft = app, app.draft
        ctk.CTkLabel(self, text="Blocking", font=ctk.CTkFont(size=24, weight="bold")).pack(
            anchor="w", padx=30, pady=(16, 8))
        self.tab_bar = ctk.CTkSegmentedButton(self, values=TABS, command=self.show_tab)
        self.tab_bar.pack(anchor="w", padx=30, pady=(0, 12))
        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)
        self.tabs = {"All": AllTab(holder, self)}
        for tab, rule_type in TAB_RULE.items():
            self.tabs[tab] = RuleTab(holder, self, rule_type)
        switches = ctk.CTkFrame(holder, fg_color="transparent")
        ctk.CTkLabel(switches, text="Switch limits are coming in a later phase.", text_color=MUTED).pack(
            anchor="w", padx=10, pady=10)
        self.tabs["By Switches"] = switches
        icons.prefetch([hosts[0] for sites in POPULAR_SITES.values() for hosts in sites.values()]
                       + [i["target"].split()[0] for i in self.draft.items.values()])
        self.tab_bar.set("All")
        self.show_tab("All")
        self.after(REFRESH_MS, self._auto_refresh)

    def show_tab(self, tab: str):
        self.tab_bar.set(tab)
        # show only the chosen tab (tkraise doesn't work for scrollable frames: it raises the inner frame only)
        for frame in self.tabs.values():
            frame.grid_forget()
        self.tabs[tab].grid(row=0, column=0, sticky="nsew")
        self.refresh()

    def edit_rule(self, item_id: int, rule_type: str):
        """From the All tab: open the rule's tab with the site loaded in the form."""
        tab = RULE_TAB[rule_type]
        self.show_tab(tab)
        self.tabs[tab].edit(item_id)

    def refresh(self):
        tab = self.tabs[self.tab_bar.get()]
        if hasattr(tab, "refresh"):
            now = now_from_db(self.app.db)
            tab.refresh(now, self.app.db.usage_on(now.date()))

    def _auto_refresh(self):
        self.draft.refresh_if_clean()   # service may have removed expired blocks
        self.refresh()                  # countdowns, usage, "blocked now"
        self.after(REFRESH_MS, self._auto_refresh)
