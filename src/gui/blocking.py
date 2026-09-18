"""Blocking page - three tabs:

- Overview: every blocked site/app with all its rules (own + groups), sortable; Edit opens it in "Add",
  Remove deletes it (two clicks).
- Groups: shared rule sets (see groups.py).
- Add: pick a site or app, then tick any number of blockers at once (hours, time limit, switch limit,
  permanent, temporary). Also used to edit an existing item.
All edits go into the draft (see draft.py).
"""
import tkinter as tk
from datetime import datetime

import customtkinter as ctk

import emergency
from blocker.apps import block_flags
from gui import app_browser, icons, theme
from gui.groups import GroupsTab
from gui.rule_editors import EDITORS, RULE_NAMES
from gui.target_picker import TargetPicker
from gui.widgets import ConfirmButton
from importer.popular import POPULAR_SITES
from rules import DAY_NAMES, describe_rule, duration_text, effective_rules, item_block, next_block
from trusted_time import now_from_db

TABS = ["Overview", "Groups", "Add"]
SORTS = ["Blocked now first", "Next block", "Date added", "Name"]
SORT_KEY = "ui.blocking.sort"
ALERTS = {"Default": None, "On": "on", "Off": "off"}
REFRESH_MS = 30_000   # full rebuild (sorting, service cleanup)
LIVE_MS = 2_000       # in-place update of counters / countdowns / status
NOTICE_MS = 5_000     # how long "✓ ... added" stays
MUTED = theme.MUTED
ERROR = theme.DANGER
GREEN, ORANGE, RED, BLUE = theme.ALLOWED, theme.PENDING, theme.BLOCKED, theme.INFO
COLS = [220, 290, 140]   # name + targets, rules, status (then action buttons)


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
        return {"text": "○ Not applied\n(unsaved)", "text_color": ORANGE}
    unlocked = getattr(usage, "unlocks", {}).get(f"item:{item['id']}")
    if unlocked and now < unlocked:
        return {"text": f"◐ Emergency unlock\n{duration_text((unlocked - now).total_seconds())} left",
                "text_color": BLUE}
    if not saved_block(draft, item, now, usage):
        return {"text": "○ Allowed now", "text_color": GREEN}
    if not page.app.service_running:
        return {"text": "○ Pending - service\nnot running", "text_color": ORANGE}
    return {"text": "● Blocked now", "text_color": RED}


def make_row(parent) -> ctk.CTkFrame:
    row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=6)
    row.pack(fill="x", pady=1)
    for col, width in enumerate(COLS):
        row.grid_columnconfigure(col, minsize=width)
    return row


def targets_text(item: dict) -> str:
    if item["item_type"] == "app":
        flags = block_flags(item.get("block_type"))
        words = (("close", "closed"), ("background", "background processes"), ("minimize", "minimized"),
                 ("internet", "internet blocked"))
        how = " + ".join(w for f, w in words if f in flags)
        return f"app · {item['target']} · {how}"
    return ", ".join(item["target"].split())


def name_cell(row, item):
    """Icon + name, with the hostnames / exe in small text underneath."""
    cell = ctk.CTkFrame(row, fg_color="transparent")
    cell.grid(row=0, column=0, padx=8, pady=6, sticky="w")
    ctk.CTkLabel(cell, text=f"  {item['display_name']}", image=icons.for_item(item, 18), compound="left",
                 font=ctk.CTkFont(weight="bold"), anchor="w").pack(anchor="w")
    ctk.CTkLabel(cell, text=targets_text(item), text_color=MUTED, font=ctk.CTkFont(size=11),
                 wraplength=COLS[0] - 16, justify="left", anchor="w").pack(anchor="w")


def header_row(parent, titles):
    row = make_row(parent)
    for col, title in enumerate(titles):
        ctk.CTkLabel(row, text=title, text_color=MUTED).grid(row=0, column=col, padx=8, pady=(6, 0), sticky="w")


# ---------------------------------------------------------------- Overview

class OverviewTab(ctk.CTkScrollableFrame):
    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.draft = page, page.draft
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(4, 8))
        self.title = ctk.CTkLabel(top, font=ctk.CTkFont(size=16, weight="bold"))
        self.title.pack(side="left")
        ctk.CTkButton(top, text="+ Add", width=80, command=lambda: page.show_tab("Add")).pack(side="left", padx=16)
        self.unlock_btn = ctk.CTkButton(top, text="Emergency unlock", width=140,
                                        **{**theme.OUTLINE, "border_color": ORANGE}, command=self._toggle_unlock)
        self.sort = ctk.CTkOptionMenu(top, values=SORTS, width=170, command=self._sort_changed)
        self.sort.set(page.app.db.get_setting(SORT_KEY, SORTS[0]))
        self.sort.pack(side="right")
        ctk.CTkLabel(top, text="Sort by", text_color=MUTED).pack(side="right", padx=8)
        self.unlock_panel = ctk.CTkFrame(self, border_width=1, border_color=ORANGE)
        self.list_box = ctk.CTkFrame(self)
        self.list_box.pack(fill="x")

    # ---------- emergency unlock ----------

    def _toggle_unlock(self):
        if self.unlock_panel.winfo_ismapped():
            self.unlock_panel.pack_forget()
        else:
            self._build_unlock_panel()
            self.unlock_panel.pack(fill="x", pady=(0, 10), before=self.list_box)

    def _build_unlock_panel(self):
        """Everything blocked right now, with checkboxes; unlocking any number of them is one use."""
        panel, db = self.unlock_panel, self.page.app.db
        for w in panel.winfo_children():
            w.destroy()
        now = now_from_db(db)
        left, allowed, reset = emergency.uses_left(db, now)
        minutes = int(emergency.get(db, "emergency.minutes"))
        per = emergency.PERIODS[emergency.get(db, "emergency.per")]
        ctk.CTkLabel(panel, text="Emergency unlock", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=14, pady=(10, 0))
        ctk.CTkLabel(panel, text=f"{left} of {allowed} left {per} (resets {DAY_NAMES[reset.weekday()]} {reset:%H:%M}). "
                                 f"Pick what to unblock for {minutes} min - several at once still count as one use.",
                     text_color=MUTED, wraplength=760, justify="left").pack(anchor="w", padx=14)
        blocked = sorted((b["item"] for b in db.blocks(now)), key=lambda i: i["display_name"].lower())
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
            self.unlock_btn.pack(side="left")
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
                anchor="w", padx=16, pady=12)
            return
        header_row(self.list_box, ["Name", "Rules", "Status", "Alerts"])
        groups = list(self.draft.groups.values())
        for item in items:
            row = make_row(self.list_box)
            name_cell(row, item)
            rules_box = ctk.CTkFrame(row, fg_color="transparent")
            rules_box.grid(row=0, column=1, padx=8, sticky="w")
            for r, rule in enumerate(effective_rules(item, groups)):
                label = ctk.CTkLabel(rules_box, text=self._rule_text(rule, now, usage), wraplength=COLS[1] - 16,
                                     justify="left", anchor="w")
                label.grid(row=r, column=0, sticky="w", pady=1)
                self.live_rules.append((label, rule))
            status = ctk.CTkLabel(row, **status_of(self.page, item, now, usage), justify="left")
            status.grid(row=0, column=2, padx=8, sticky="w")
            self.live_status.append((status, item))
            alerts = ctk.CTkOptionMenu(row, values=list(ALERTS), width=90,
                                       command=lambda v, i=item["id"]: self.draft.set_notify(i, ALERTS[v]))
            alerts.set(next(k for k, v in ALERTS.items() if v == item["notify"]))
            alerts.grid(row=0, column=3, padx=4)
            choices = [("Edit its blockers", lambda i=item["id"]: self.page.edit_item(i))]
            choices += [(f"Edit group {g['name']}", lambda g=g["id"]: self.page.edit_group(g))
                        for g in self.draft.groups_of(item["id"])]
            edit_btn = ctk.CTkButton(row, text="Edit ▾" if len(choices) > 1 else "Edit", width=70)
            edit_btn.configure(command=lambda b=edit_btn, c=choices: self._edit(b, c))
            edit_btn.grid(row=0, column=4, padx=4)
            ConfirmButton(row, lambda i=item["id"]: self.draft.remove_item(i), width=70).grid(row=0, column=5, padx=4)

    @staticmethod
    def _rule_text(rule, now, usage) -> str:
        text = describe_rule(rule, now, usage)
        return f"[{rule['group']['name']}] {text}" if rule["group"] else text

    def update_live(self, now, usage):
        """Refresh counters, countdowns and statuses without rebuilding the list."""
        for label, rule in getattr(self, "live_rules", []):
            if label.winfo_exists():
                label.configure(text=self._rule_text(rule, now, usage))
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
        box = ctk.CTkFrame(self)
        box.pack(fill="x")
        self.title = ctk.CTkLabel(box, font=ctk.CTkFont(size=16, weight="bold"))
        self.title.pack(anchor="w", padx=16, pady=(12, 8))
        self.picker = TargetPicker(box, page.app.db)
        self.picker.pack(anchor="w", padx=16)
        self.picker.entry.bind("<Return>", lambda e: self._submit(), add="+")
        self.info = ctk.CTkLabel(box, text="", text_color=MUTED)
        self.info.pack(anchor="w", padx=16)

        ctk.CTkLabel(box, text="Blockers (tick any number)", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=16, pady=(12, 2))
        self.parts = {}
        for t in EDITORS:
            check = ctk.CTkCheckBox(box, text=RULE_NAMES[t], command=lambda t=t: self._toggle(t))
            check.pack(anchor="w", padx=16, pady=(6, 2))
            self.parts[t] = (check, EDITORS[t](box))

        self.error = ctk.CTkLabel(box, text="", text_color=ERROR)
        self.error.pack(anchor="w", padx=16, pady=(6, 0))
        buttons = ctk.CTkFrame(box, fg_color="transparent")
        buttons.pack(anchor="w", padx=16, pady=(4, 14))
        self.submit_btn = ctk.CTkButton(buttons, width=110, command=self._submit)
        self.submit_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Cancel", width=80, **theme.OUTLINE,
                      command=self.cancel).pack(side="left")
        self.reset()

    def _toggle(self, t: str):
        check, editor = self.parts[t]
        if check.get() and t != "permanent":
            editor.pack(anchor="w", padx=44, pady=(0, 6), after=check)
        else:
            editor.pack_forget()

    def _load_rules(self, rules: list[dict]):
        by_type = {r["rule_type"]: r for r in rules}
        for t, (check, editor) in self.parts.items():
            editor.load(by_type.get(t))
            check.select() if t in by_type else check.deselect()
            self._toggle(t)

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
        return [editor.value() for t, (check, editor) in self.parts.items() if check.get()]

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


# ---------------------------------------------------------------- page

class BlockingPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.draft = app, app.draft
        ctk.CTkLabel(self, text="Blocking", font=theme.page_title()).pack(
            anchor="w", padx=30, pady=(16, 8))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=30, pady=(0, 12))
        self.tab_bar = ctk.CTkSegmentedButton(bar, values=TABS, command=self.show_tab, width=240, height=30,
                                              dynamic_resizing=False)
        self.tab_bar.pack(side="left")
        # short confirmation after adding / saving ("✓ YouTube blocker added"), hidden after a few seconds
        self.notice = ctk.CTkLabel(bar, text="", text_color=GREEN, font=ctk.CTkFont(weight="bold"))
        self.notice.pack(side="left", padx=16)
        self._notice_job = None
        holder = ctk.CTkFrame(self, fg_color="transparent")
        holder.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)
        self.tabs = {"Overview": OverviewTab(holder, self), "Groups": GroupsTab(holder, self),
                     "Add": AddTab(holder, self)}
        icons.prefetch([hosts[0] for sites in POPULAR_SITES.values() for hosts in sites.values()]
                       + [i["target"].split()[0] for i in self.draft.items.values() if i["item_type"] == "site"])
        app_browser.preload()
        self.show_tab("Overview")
        self.after(REFRESH_MS, self._auto_refresh)
        self.after(LIVE_MS, self._live_update)

    def show_tab(self, tab: str):
        self.tab_bar.set(tab)
        # show only the chosen tab (tkraise doesn't work for scrollable frames: it raises the inner frame only)
        for frame in self.tabs.values():
            frame.grid_forget()
        self.tabs[tab].grid(row=0, column=0, sticky="nsew")
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
