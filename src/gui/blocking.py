"""Blocking page - four tabs:

- Overview: every blocked site/app with all its rules (own + groups) as chips, sortable; Edit opens it in "Add",
  Remove deletes it (two clicks). Emergency unlock.
- Groups: shared rule sets (see groups.py).
- Add: pick a site or app, then tick any number of blockers at once (collapsible cards: hours, time limit,
  opening limit, permanent, temporary). Also used to edit an existing item.
- Protection: always-on scam / phishing / malware / adult lists (see protection_tab.py).
All edits go into the draft (see draft.py).
"""
import tkinter as tk
from datetime import date, datetime, timedelta

import customtkinter as ctk

import blockcal
import emergency
import stats
from blocker.apps import block_flags
from gui import app_browser, categories, icons, theme
from gui.charts import WeekCalendar
from gui.components import Curtain, Card, BlockerCard, Segmented, eyebrow, rule_chip, help_icon, page_head, type_badge
from gui.groups import GroupsTab
from gui.protection_tab import ProtectionTab
from gui.rule_editors import EDITORS, RULE_NAMES, summary
from gui.target_picker import TargetPicker
from gui.widgets import ConfirmButton
from importer.popular import POPULAR_SITES
from rules import DAY_NAMES, describe_rule, duration_text, effective_rules, item_block, next_block
from trusted_time import now_from_db

TABS = ["Overview", "Groups", "Add", "Protection", "Calendar"]
SORTS = ["Blocked now first", "Next block", "Date added", "Name"]
SORT_KEY = "ui.blocking.sort"
ALERTS = {"Default": None, "On": "on", "Off": "off"}
REFRESH_MS = 30_000   # full rebuild (sorting, service cleanup)
LIVE_MS = 2_000       # in-place update of counters / countdowns / status
NOTICE_MS = 5_000     # how long "✓ ... added" stays
SUMMARY_MS = 1_000    # blocker card summaries follow what's typed
MUTED = theme.MUTED
ERROR = theme.DANGER
GREEN, ORANGE, RED, BLUE = theme.ALLOWED, theme.PENDING, theme.BLOCKED, theme.INFO
COLS = [230, 250]   # wrap widths: item text, rule chips


# ---------------------------------------------------------------- shared helpers

def saved_block(draft, item: dict, now, usage):
    """The block that's enforced now (from the SAVED state), or None."""
    saved = draft.saved_items.get(item["id"])
    if saved is None:
        return None
    return item_block(effective_rules(saved, list(draft.saved_groups.values())), now, usage)


def status_of(page, item: dict, now, usage) -> dict:
    draft = page.draft
    if draft.item_not_applied(item["id"]):
        return {"text": "● Not applied\n(unsaved)", "text_color": ORANGE}
    unlocked = getattr(usage, "unlocks", {}).get(f"item:{item['id']}")
    if unlocked and now < unlocked:
        return {"text": f"● Emergency unlock\n{duration_text((unlocked - now).total_seconds())} left",
                "text_color": BLUE}
    if not saved_block(draft, item, now, usage):
        return {"text": "● Allowed now", "text_color": GREEN}
    if not page.app.service_running:
        return {"text": "● Pending - service\nnot running", "text_color": ORANGE}
    return {"text": "● Blocked now", "text_color": RED}


def targets_text(item: dict) -> str:
    if item["item_type"] == "app":
        flags = block_flags(item.get("block_type"))
        words = (("close", "closes"), ("background", "background processes"), ("minimize", "minimises"),
                 ("internet", "internet blocked"))
        how = " + ".join(w for f, w in words if f in flags)
        return f"app · {item['target']} · {how}"
    return " · ".join(item["target"].split())


def rule_text(rule, now, usage) -> str:
    text = describe_rule(rule, now, usage).replace(":\n", ": ").replace("\n", " · ")
    return f"→ {rule['group']['name']}  {text}" if rule["group"] else text


def chip_kind(rule) -> str:
    if rule["group"]:
        return "group"
    return {"permanent": "danger", "temporary": "warn", "time_limit": "warn"}.get(rule["rule_type"], "neutral")


# ---------------------------------------------------------------- Overview

class OverviewTab(ctk.CTkScrollableFrame):
    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.draft = page, page.draft
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", pady=(0, 10))
        self.title = ctk.CTkLabel(top, font=theme.card_title())
        self.title.pack(side="left")
        self.unlock_btn = ctk.CTkButton(top, text="Emergency unlock", width=140,
                                        **{**theme.OUTLINE, "border_color": ORANGE}, command=self._toggle_unlock)
        ctk.CTkButton(top, text="+ Add", width=90, command=lambda: page.show_tab("Add")).pack(side="right")
        self.sort = ctk.CTkOptionMenu(top, values=SORTS, width=170, command=self._sort_changed)
        self.sort.set(page.app.db.get_setting(SORT_KEY, SORTS[0]))
        self.sort.pack(side="right", padx=10)
        ctk.CTkLabel(top, text="Sort by", text_color=MUTED).pack(side="right")
        self.unlock_panel = ctk.CTkFrame(self, border_width=1, border_color=ORANGE)
        self.list_card = ctk.CTkFrame(self, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER)
        self.list_card.pack(fill="x")
        self.list_box = ctk.CTkFrame(self.list_card, fg_color="transparent")
        self.list_box.pack(fill="x", padx=15, pady=(8, 4))
        self.list_box.pack_configure(pady=(8, 10))
        help_icon(top, "Rules from a group are marked with → and can only be changed in Groups.").pack(
            side="left", padx=8, after=self.title)

    # ---------- emergency unlock ----------

    def _toggle_unlock(self):
        if self.unlock_panel.winfo_ismapped():
            self.unlock_panel.pack_forget()
        else:
            self._build_unlock_panel()
            self.unlock_panel.pack(fill="x", pady=(0, 10), before=self.list_card)

    def _build_unlock_panel(self):
        """Everything blocked right now, with checkboxes; unlocking any number of them is one use."""
        panel, db = self.unlock_panel, self.page.app.db
        for w in panel.winfo_children():
            w.destroy()
        now = now_from_db(db)
        left, allowed, reset = emergency.uses_left(db, now)
        minutes = int(emergency.get(db, "emergency.minutes"))
        per = emergency.PERIODS[emergency.get(db, "emergency.per")]
        ctk.CTkLabel(panel, text="Emergency unlock", font=theme.card_title()).pack(anchor="w", padx=14, pady=(10, 0))
        ctk.CTkLabel(panel, text=f"{left} of {allowed} left {per} (resets {DAY_NAMES[reset.weekday()]} {reset:%H:%M}). "
                                 f"Pick what to unblock for {minutes} min - several at once still count as one use.",
                     text_color=MUTED, wraplength=760, justify="left").pack(anchor="w", padx=14)
        # things on your list (a mode can also block made-up items, without an id, that can't be unlocked)
        blocked = sorted({b["item"]["id"]: b["item"] for b in db.blocks(now) if b["item"]["id"] is not None}.values(),
                         key=lambda i: i["display_name"].lower())
        boxes = []
        for item in blocked:
            line = ctk.CTkFrame(panel, fg_color="transparent")
            line.pack(anchor="w", padx=14, pady=2)
            box = ctk.CTkCheckBox(line, text="", width=24)
            box.pack(side="left")
            ctk.CTkLabel(line, text=f"  {item['display_name']}", image=icons.for_item(item, 16),
                         compound="left").pack(side="left")
            boxes.append((box, item))
        if not blocked:
            ctk.CTkLabel(panel, text="Nothing is blocked right now.").pack(anchor="w", padx=14, pady=4)
        error = ctk.CTkLabel(panel, text="", text_color=ERROR)
        error.pack(anchor="w", padx=14)
        buttons = ctk.CTkFrame(panel, fg_color="transparent")
        buttons.pack(anchor="w", padx=14, pady=(0, 10))
        if blocked and left:
            ConfirmButton(buttons, lambda: self._unlock([i for b, i in boxes if b.get()], error),
                          text=f"Unlock for {minutes} min", confirm_text=f"Confirm - uses 1 of {left}",
                          width=170).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Close", width=80, **theme.OUTLINE,
                      command=self._toggle_unlock).pack(side="left")

    def _unlock(self, items: list[dict], error):
        db = self.page.app.db
        try:
            until = emergency.unlock(db, items, now_from_db(db))
        except ValueError as e:
            error.configure(text=str(e))
            return
        self.unlock_panel.pack_forget()
        names = ", ".join(i["display_name"] for i in items)
        self.page.confirm(f"{names} unlocked until {until:%H:%M}", immediate=True)
        self.page.refresh()

    # ---------- list ----------

    def _sort_changed(self, value: str):
        self.page.app.db.set_setting(SORT_KEY, value)
        self.page.refresh()

    def _sorted(self, items: list[dict], now, usage) -> list[dict]:
        how = self.sort.get()
        if how == "Name":
            return sorted(items, key=lambda i: i["display_name"].lower())
        if how == "Date added":   # newest first; unsaved ones on top
            return sorted(items, key=lambda i: i.get("created_at") or "~", reverse=True)
        groups = list(self.draft.saved_groups.values())

        def next_time(item) -> datetime:
            """datetime.min = blocked now; datetime.max = nothing coming up."""
            if saved_block(self.draft, item, now, usage):
                return datetime.min
            saved = self.draft.saved_items.get(item["id"])
            nb = next_block(effective_rules(saved, groups), now, usage) if saved else None
            return nb[0] if nb else datetime.max
        times = {i["id"]: next_time(i) for i in items}
        if how == "Next block":   # soonest first, the ones blocked already after them
            return sorted(items, key=lambda i: (times[i["id"]] == datetime.min, times[i["id"]]))
        return sorted(items, key=lambda i: (times[i["id"]] != datetime.min, i["display_name"].lower()))

    def refresh(self, now, usage):
        if emergency.get(self.page.app.db, "emergency.enabled") == "1":
            self.unlock_btn.pack(side="left", padx=16)
        else:
            self.unlock_btn.pack_forget()
            self.unlock_panel.pack_forget()
        items = self._sorted(self.draft.sorted_items(), now, usage)
        self.title.configure(text=f"Everything blocked ({len(items)})")
        for w in self.list_box.winfo_children():
            w.destroy()
        self.live_rules: list[tuple] = []    # (label, rule) - texts updated in place every 2 s
        self.live_status: list[tuple] = []   # (label, item)
        if not items:
            ctk.CTkLabel(self.list_box, text="Nothing blocked yet - use + Add.", text_color=MUTED).pack(
                anchor="w", pady=12)
            return
        table = ctk.CTkFrame(self.list_box, fg_color="transparent")
        table.pack(fill="x")
        table.grid_columnconfigure(1, weight=1)
        for col, title in enumerate(["Item", "Rules", "Status", "Alerts"]):
            eyebrow(table, title).grid(row=0, column=col, padx=(0, 12), pady=(4, 6), sticky="w")
        groups = list(self.draft.groups.values())
        for n, item in enumerate(items):
            r = 2 * n + 1
            ctk.CTkFrame(table, height=1, fg_color=theme.BORDER).grid(row=r, column=0, columnspan=6, sticky="ew")
            r += 1
            cell = ctk.CTkFrame(table, fg_color="transparent")
            cell.grid(row=r, column=0, pady=10, padx=(0, 12), sticky="w")
            ctk.CTkLabel(cell, text="", image=icons.for_item(item, 22), width=28).pack(side="left", anchor="n")
            texts = ctk.CTkFrame(cell, fg_color="transparent")
            texts.pack(side="left", padx=(6, 0))
            name_row = ctk.CTkFrame(texts, fg_color="transparent")
            name_row.pack(anchor="w")
            ctk.CTkLabel(name_row, text=item["display_name"], font=theme.semi(13), height=18, anchor="w").pack(
                side="left")
            type_badge(name_row, item["item_type"]).pack(side="left", padx=(7, 0))
            ctk.CTkLabel(texts, text=targets_text(item), text_color=MUTED, font=theme.body(10), height=14,
                         wraplength=COLS[0] - 44, justify="left", anchor="w").pack(anchor="w")
            rules_box = ctk.CTkFrame(table, fg_color="transparent")
            rules_box.grid(row=r, column=1, pady=8, padx=(0, 12), sticky="w")
            for rule in effective_rules(item, groups):
                chip = rule_chip(rules_box, rule_text(rule, now, usage), chip_kind(rule), COLS[1] - 20)
                chip.pack(anchor="w", pady=2)
                self.live_rules.append((chip, rule))
            status = ctk.CTkLabel(table, **status_of(self.page, item, now, usage), justify="left", font=theme.body(12))
            status.grid(row=r, column=2, padx=(0, 12), sticky="w")
            self.live_status.append((status, item))
            alerts = ctk.CTkOptionMenu(table, values=list(ALERTS), width=86, height=28,
                                       command=lambda v, i=item["id"]: self.draft.set_notify(i, ALERTS[v]))
            alerts.set(next(k for k, v in ALERTS.items() if v == item["notify"]))
            alerts.grid(row=r, column=3, padx=(0, 10))
            choices = [("Edit its blockers", lambda i=item["id"]: self.page.edit_item(i))]
            choices += [(f"Edit group {g['name']}", lambda g=g["id"]: self.page.edit_group(g))
                        for g in self.draft.groups_of(item["id"])]
            edit_btn = ctk.CTkButton(table, text="Edit ▾" if len(choices) > 1 else "Edit", width=66, height=28,
                                     **theme.SECONDARY)
            edit_btn.configure(command=lambda b=edit_btn, c=choices: self._edit(b, c))
            edit_btn.grid(row=r, column=4, padx=4)
            ConfirmButton(table, lambda i=item["id"]: self.draft.remove_item(i), width=76, height=28,
                          **theme.SECONDARY).grid(row=r, column=5, padx=(4, 0))

    def update_live(self, now, usage):
        """Refresh counters, countdowns and statuses without rebuilding the list."""
        for label, rule in getattr(self, "live_rules", []):
            if label.winfo_exists():
                label.configure(text=f" {rule_text(rule, now, usage)} ")
        for label, item in getattr(self, "live_status", []):
            if label.winfo_exists():
                label.configure(**status_of(self.page, item, now, usage))

    def _edit(self, button, choices):
        """One choice: do it. Several: ask which one with a small menu under the button."""
        if len(choices) == 1:
            choices[0][1]()
            return
        menu = tk.Menu(self, tearoff=False)
        for label, action in choices:
            menu.add_command(label=label, command=action)
        menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())


# ---------------------------------------------------------------- Add / edit

class AddTab(ctk.CTkScrollableFrame):
    """Pick a site or app, tick any number of blockers. Also edits an existing item."""

    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.draft = page, page.draft
        self.edit_id: int | None = None
        box = ctk.CTkFrame(self, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER)
        box.pack(fill="x")
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 8))
        ctk.CTkFrame(head, width=3, height=24, corner_radius=0, fg_color=theme.ACCENT).pack(side="left", padx=(0, 10))
        self.title = ctk.CTkLabel(head, font=theme.body(18, "bold"))
        self.title.pack(side="left")
        self.picker = TargetPicker(box, page.app.db)
        self.picker.pack(anchor="w", padx=16, pady=(0, 4))
        self.picker.entry.bind("<Return>", lambda e: self._submit(), add="+")
        self.info = ctk.CTkLabel(box, text="", text_color=MUTED, height=18)
        self.info.pack(anchor="w", padx=16, pady=(0, 8))

        line = ctk.CTkFrame(self, fg_color="transparent")
        line.pack(fill="x", pady=(14, 6))
        eyebrow(line, "Blockers - tick any number, they combine").pack(side="left")
        self.count = ctk.CTkLabel(line, text="", text_color=MUTED, font=theme.body(11))
        self.count.pack(side="right")
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="x")
        content.grid_columnconfigure(0, minsize=318)
        content.grid_columnconfigure(1, weight=1)
        self.rail = ctk.CTkFrame(content, fg_color="transparent")
        self.rail.grid(row=0, column=0, sticky="new")
        self.editors: dict[str, object] = {}   # built lazily, into the panel
        self.ticked: set[str] = set()
        self.open_t: str | None = None
        self.rows = {t: self._rail_row(t) for t in EDITORS}
        self.panel = Card(content, title="Blocker settings", accent_top=True)
        self.panel.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        self.placeholder = ctk.CTkLabel(self.panel.body, text="Tick a blocker on the left to set it up.",
                                        text_color=MUTED)
        self.placeholder.pack(anchor="w", pady=6)

        self.error = ctk.CTkLabel(self, text="", text_color=ERROR)
        self.error.pack(anchor="w", pady=(6, 0))
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", pady=(2, 14))
        self.submit_btn = ctk.CTkButton(bottom, width=110, height=34, command=self._submit)
        self.submit_btn.pack(side="right")
        ctk.CTkButton(bottom, text="Cancel", width=90, height=34, **theme.OUTLINE,
                      command=self.cancel).pack(side="right", padx=8)
        self.sentence = ctk.CTkLabel(bottom, text="", text_color=MUTED, font=theme.body(11), wraplength=560,
                                     justify="left", anchor="w")
        self.sentence.pack(side="left", fill="x", expand=True)
        self.reset()
        self.after(SUMMARY_MS, self._follow_summaries)

    def on_show(self):
        # repaint the buttons: made while the tab was hidden (built in the background) they could stay unpainted
        self.after(50, lambda: self.submit_btn.configure(fg_color=theme.ACCENT))

    def _rail_row(self, t: str):
        """A compact blocker in the left rail: tick + name + one-line summary + chevron. Clicking it opens its
        settings in the panel on the right (and ticks it on)."""
        row = ctk.CTkFrame(self.rail, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER,
                           corner_radius=3)
        row.pack(fill="x", pady=3)
        row.bar = ctk.CTkFrame(row, width=3, height=1, fg_color="transparent", corner_radius=0)
        row.bar.pack(side="left", fill="y")
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(side="left", fill="both", expand=True, padx=(9, 10), pady=8)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        row.check = ctk.CTkCheckBox(top, text=RULE_NAMES[t], font=theme.semi(13), command=lambda: self._tick(t))
        row.check.pack(side="left")
        row.chev = ctk.CTkLabel(top, text="▸", text_color=MUTED, font=theme.body(11), width=14)
        row.chev.pack(side="right")
        row.summary = ctk.CTkLabel(inner, text="off", text_color=MUTED, font=theme.body(11), anchor="w",
                                   justify="left")
        row.summary.pack(anchor="w", pady=(3, 0))
        for w in (inner, top, row.summary):
            w.bind("<Button-1>", lambda e, t=t: self._open_panel(t, tick=True))
        return row

    def editor_of(self, t: str):
        if t not in self.editors:
            self.editors[t] = EDITORS[t](self.panel.body)
        return self.editors[t]

    def _tick(self, t: str):
        if self.rows[t].check.get():
            self.ticked.add(t)
            self._open_panel(t)
        else:
            self.ticked.discard(t)
            if self.open_t == t:
                nxt = next(iter(self.ticked), None)
                self._show_editor(nxt)
        self._update_rail()
        self._changed()

    def _open_panel(self, t: str | None, tick: bool = False):
        if t is not None and tick and t not in self.ticked:
            self.ticked.add(t)
            self.rows[t].check.select()
        self._show_editor(t)
        self._update_rail()
        self._changed()

    def _show_editor(self, t: str | None):
        self.placeholder.pack_forget()
        for ed in self.editors.values():
            ed.pack_forget()
        self.open_t = t
        if t is None:
            self.panel.title.configure(text="Blocker settings")
            self.placeholder.pack(anchor="w", pady=6)
        else:
            self.panel.title.configure(text=RULE_NAMES[t])
            self.editor_of(t).pack(anchor="w", fill="x")

    def _update_rail(self):
        for t, row in self.rows.items():
            on = t in self.ticked
            row.bar.configure(fg_color=theme.ACCENT if self.open_t == t else "transparent")
            row.check.configure(text_color=theme.TEXT if on else MUTED)
            row.chev.configure(text="▾" if self.open_t == t else "▸")
            row.summary.configure(text=summary(t, self.editors[t]) if on and t in self.editors else "off")

    def _changed(self):
        on = [t for t in EDITORS if t in self.ticked]
        self.count.configure(text=f"{len(on)} of {len(self.rows)} on · blocked when any of them applies")
        name = self.picker.name.get().strip() or "It"
        self.sentence.configure(text=f"{name} will be blocked: " + "; ".join(
            f"{RULE_NAMES[t].lower()} ({summary(t, self.editor_of(t))})" for t in on) + "." if on else "")

    def _follow_summaries(self):
        if self.winfo_ismapped():
            self._update_rail()
            self._changed()
        self.after(SUMMARY_MS, self._follow_summaries)

    def _load_rules(self, rules: list[dict]):
        by_type = {r["rule_type"]: r for r in rules}
        self.ticked = set(by_type)
        for t in EDITORS:
            if t in by_type:
                self.editor_of(t).load(by_type[t])
                self.rows[t].check.select()
            else:
                if t in self.editors:
                    self.editors[t].load(None)
                self.rows[t].check.deselect()
        first = next((t for t in EDITORS if t in self.ticked), None)
        self._open_panel(first)

    def reset(self):
        self.edit_id = None
        self.title.configure(text="Add a site or app")
        self.submit_btn.configure(text="+ Add")
        self.picker.reset()
        self.info.configure(text="")
        self.error.configure(text="")
        self._load_rules([])

    def cancel(self):
        editing = self.edit_id is not None
        self.reset()
        if editing:
            self.page.show_tab("Overview")

    def edit(self, item_id: int):
        item = self.draft.items[item_id]
        self.edit_id = item_id
        self.title.configure(text=f"Edit {item['display_name']}")
        self.submit_btn.configure(text="Save")
        self.picker.load_item(item)
        groups = ", ".join(g["name"] for g in self.draft.groups_of(item_id))
        self.info.configure(text=f"Also in groups: {groups} (edit those in the Groups tab)" if groups else "")
        self.error.configure(text="")
        self._load_rules(item["rules"])
        self._parent_canvas.yview_moveto(0)

    def _rules(self) -> list[dict]:
        return [self.editor_of(t).value() for t in EDITORS if t in self.ticked]

    def _submit(self):
        self.picker.entry.hide()
        if self.edit_id is not None and self.edit_id not in self.draft.items:  # expired meanwhile
            self.reset()
            return
        try:
            rules = self._rules()
            target = self.picker.get() if self.edit_id is None else None
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        if target:
            existing = self.draft.find_item(target["targets"][0])
            if existing:   # already in the list: load it for editing instead of adding a duplicate
                self.edit(existing["id"])
                self.info.configure(text=f"{existing['display_name']} is already in the list - its blockers are "
                                         "loaded, change them and Save.")
                return
            if not rules:
                self.error.configure(text="Tick at least one blocker.")
                return
            item = self.draft.add_item(target["name"], target["targets"], target["source"], target["kind"],
                                       target["block_type"], target["app_path"])
            self.draft.set_rules(item["id"], rules, block_type=target["block_type"])
            self.reset()
            self.page.confirm(f"{target['name']} blocker added")
            return
        item = self.draft.items[self.edit_id]
        if item["item_type"] == "app" and self.picker.selected_block_type() is None:
            self.error.configure(text="Tick at least one of Close app / Minimize / Block internet.")
            return
        if not rules and not self.draft.groups_of(self.edit_id):
            self.error.configure(text="Tick at least one blocker (or remove it in Overview).")
            return
        name = self.picker.name.get().strip() or item["display_name"]
        self.draft.set_rules(self.edit_id, rules, name, self.picker.selected_block_type())
        self.reset()
        self.page.show_tab("Overview")
        self.page.confirm(f"{name} saved")


# ---------------------------------------------------------------- calendar

class CalendarTab(ctk.CTkScrollableFrame):
    """A week (Mon..Sun) x 24 h showing when each site/app is blocked. Only permanent and by-time rules appear
    (limits / temporary blocks aren't tied to a clock time). Click a bar to edit that item."""

    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.draft, self.db = page, page.draft, page.app.db
        self.week_start: date | None = None   # Monday of the shown week (None = this week)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))
        ctk.CTkButton(header, text="‹", width=30, **theme.OUTLINE, command=lambda: self._shift(-1)).pack(side="left")
        ctk.CTkButton(header, text="›", width=30, **theme.OUTLINE, command=lambda: self._shift(1)).pack(
            side="left", padx=(6, 12))
        self.range_label = ctk.CTkLabel(header, text="", font=theme.semi(13))
        self.range_label.pack(side="left")
        ctk.CTkButton(header, text="This week", width=100, **theme.OUTLINE, command=self._this_week).pack(side="right")
        card = Card(self, "When each block applies")
        card.pack(fill="x")
        self.cal = WeekCalendar(card.body, on_click=self._open, height=340)
        self.cal.pack(fill="x")
        ctk.CTkLabel(card.body, text="Only permanent and by-time blocks show here - time / opening limits and "
                     "temporary blocks aren't tied to a clock time. Click a bar to edit that item; Ⓔ marks an "
                     "emergency unlock; the accent line is now.", text_color=MUTED, wraplength=900,
                     justify="left").pack(anchor="w", pady=(8, 0))

    @staticmethod
    def _monday(d: date) -> date:
        return d - timedelta(days=d.weekday())

    def _shift(self, weeks: int):
        base = self.week_start or self._monday(now_from_db(self.db).date())
        self.week_start = base + timedelta(weeks=weeks)
        self.refresh()

    def _this_week(self):
        self.week_start = None
        self.refresh()

    def on_show(self):
        self.refresh()

    def refresh(self, *_):
        now = now_from_db(self.db)
        today = now.date()
        start = self.week_start or self._monday(today)
        end = start + timedelta(days=6)
        self.range_label.configure(text=f"{start:%d %b} - {end:%d %b %Y}")
        items = list(self.draft.items.values())
        groups = list(self.draft.groups.values())
        cats = categories.load(self.db)
        colors, saved = categories.colors_of(cats), self.db.categories()

        def color_of(item):
            name = item["target"].split()[0]
            return theme.pick(colors.get(stats.category_of(item["item_type"], name, saved, items)) or theme.MUTED)

        unlocks: list[list[int]] = [[] for _ in range(7)]
        for u in self.db.unlocks_since(datetime.combine(start, datetime.min.time())):
            d = u["started"].date()
            if start <= d <= end:
                unlocks[(d - start).days].append(u["started"].hour * 60 + u["started"].minute)
        today_wd = (today - start).days if start <= today <= end else -1
        self.cal.set(blockcal.week(items, groups), color_of, today_wd, now.hour * 60 + now.minute, unlocks)

    def _open(self, item):
        self.page.edit_item(item["id"])


# ---------------------------------------------------------------- page

class BlockingPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.draft = app, app.draft
        page_head(self, "Blocking").pack(anchor="w", padx=30, pady=(12, 6))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=30, pady=(0, 10))
        self.tab_bar = Segmented(bar, values=TABS, command=self.show_tab, width=240, height=30,
                                              dynamic_resizing=False)
        self.tab_bar.pack(side="left")
        # short confirmation after adding / saving ("✓ YouTube blocker added"), hidden after a few seconds
        self.notice = ctk.CTkLabel(bar, text="", text_color=GREEN, font=theme.semi(13))
        self.notice.pack(side="left", padx=16)
        self._notice_job = None
        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="both", expand=True, padx=(20, 12), pady=(0, 14))
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)
        self.holder = holder
        self.tabs: dict[str, ctk.CTkFrame] = {}   # built the first time each tab is shown
        icons.prefetch([hosts[0] for sites in POPULAR_SITES.values() for hosts in sites.values()]
                       + [i["target"].split()[0] for i in self.draft.items.values() if i["item_type"] == "site"])
        app_browser.preload()
        self.show_tab("Overview")
        self.after(REFRESH_MS, self._auto_refresh)
        self.after(LIVE_MS, self._live_update)

    TAB_CLASSES = {"Overview": OverviewTab, "Groups": GroupsTab, "Add": AddTab, "Protection": ProtectionTab,
                   "Calendar": CalendarTab}

    def show_tab(self, tab: str):
        if tab not in self.tabs:
            self.tabs[tab] = self.TAB_CLASSES[tab](self.holder, self)
        self.tab_bar.set(tab)
        # show only the chosen tab (tkraise doesn't work for scrollable frames: it raises the inner frame only)
        for frame in self.tabs.values():
            frame.grid_forget()
        self.tabs[tab].grid(row=0, column=0, sticky="nsew")
        if not hasattr(self, "curtain"):
            self.curtain = Curtain(self.holder)
        self.curtain.cover()
        if hasattr(self.tabs[tab], "on_show"):
            self.tabs[tab].on_show()
        self.refresh()

    def confirm(self, text: str, immediate: bool = False):
        if not immediate and not self.draft.autosave:
            text += " - press Save changes to apply"
        self.notice.configure(text=f"✓ {text}")
        if self._notice_job:
            self.after_cancel(self._notice_job)
        self._notice_job = self.after(NOTICE_MS, lambda: self.notice.configure(text=""))

    def edit_item(self, item_id: int):
        self.show_tab("Add")
        self.tabs["Add"].edit(item_id)

    def add_prefilled(self, site: tuple[str, str] | None = None, app: dict | None = None):
        """Open Add with a site (name, host) or an app {name, exe, path} filled in (e.g. from the network log);
        something already on the list opens for editing instead."""
        self.show_tab("Add")
        add = self.tabs["Add"]
        existing = self.draft.find_item(site[1] if site else app["exe"])
        if existing:
            add.edit(existing["id"])
            return
        add.reset()
        if site:
            add.picker._fill_site(*site)
        else:
            add.picker._fill_app(app)
        add._changed()

    def edit_group(self, group_id: int):
        self.show_tab("Groups")
        self.tabs["Groups"].edit(group_id)

    def refresh(self):
        tab = self.tabs[self.tab_bar.get()]
        if hasattr(tab, "refresh"):
            now = now_from_db(self.app.db)
            tab.refresh(now, self.app.db.usage_lookup(now))

    def _live_update(self):
        try:
            tab = self.tabs[self.tab_bar.get()]
            if hasattr(tab, "update_live") and self.winfo_ismapped():
                now = now_from_db(self.app.db)
                tab.update_live(now, self.app.db.usage_lookup(now))
        finally:
            self.after(LIVE_MS, self._live_update)

    def _auto_refresh(self):
        self.draft.refresh_if_clean()   # service may have removed expired blocks
        self.refresh()                  # countdowns, usage, "blocked now"
        self.after(REFRESH_MS, self._auto_refresh)
