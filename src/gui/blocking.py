"""Blocking page.

Each rule tab (By Hours / By Limit / Permanent / Temporary) has its own add/edit form and lists the sites
and apps that have that kind of rule. "All" is an overview of everything with all its rules (own + groups);
Edit jumps to the rule's tab (or the group), Remove deletes the item (after a confirmation). "Groups" holds
shared rule sets. All edits go into the draft (see draft.py).
"""
import tkinter as tk

import customtkinter as ctk

from gui import app_browser, icons
from gui.groups import GroupsTab
from gui.rule_editors import EDITORS
from gui.target_picker import TargetPicker
from gui.widgets import ConfirmButton
from importer.popular import POPULAR_SITES
from rules import describe_rule, effective_rules, item_block
from trusted_time import now_from_db

TABS = ["All", "Groups", "By Hours", "By Limit", "By Switches", "Permanent", "Temporary"]
TAB_RULE = {"By Hours": "scheduled", "By Limit": "time_limit", "Permanent": "permanent", "Temporary": "temporary"}
RULE_TAB = {v: k for k, v in TAB_RULE.items()}
RULE_NAME = {"scheduled": "hours rule", "time_limit": "daily limit", "permanent": "permanent block",
             "temporary": "temporary block"}
ALERTS = {"Default": None, "On": "on", "Off": "off"}
REFRESH_MS = 30_000
HIGHLIGHT_MS = 2500
MUTED = "gray60"
ERROR = "#f85149"
GREEN, ORANGE = "#3fb950", "#d29922"
HIGHLIGHT = ("#dbe9ff", "#1f3a5f")
COLS = [220, 270, 140]   # name + targets, rules, status (then action buttons)


# ---------------------------------------------------------------- shared list helpers

def status_of(page, item: dict, now, usage) -> dict:
    """Status from the SAVED state (that's what is enforced)."""
    draft = page.draft
    if draft.item_not_applied(item["id"]):
        return {"text": "○ Not applied\n(unsaved)", "text_color": ORANGE}
    saved = draft.saved_items[item["id"]]
    if not item_block(effective_rules(saved, list(draft.saved_groups.values())), now, usage):
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


def targets_text(item: dict) -> str:
    if item["item_type"] == "app":
        how = {"firewall": "internet blocked", "both": "closed + internet blocked"}.get(item.get("block_type"), "closed")
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
        self.picker = TargetPicker(box, self.page.app.db)
        self.picker.pack(anchor="w", padx=16)
        self.picker.entry.bind("<Return>", lambda e: self._submit(), add="+")
        self.submit_btn = ctk.CTkButton(self.picker.buttons, width=90, command=self._submit)
        self.submit_btn.pack(side="left", padx=4)
        self.cancel_btn = ctk.CTkButton(self.picker.buttons, text="Cancel", width=80, fg_color="transparent",
                                        border_width=1, command=self.reset_form)
        self.editor = EDITORS[self.rule_type](box)
        if self.rule_type != "permanent":
            self.editor.pack(anchor="w", padx=16, pady=(10, 0))
        self.error = ctk.CTkLabel(box, text="", text_color=ERROR)
        self.error.pack(anchor="w", padx=16, pady=(4, 8))
        self.reset_form()

    def reset_form(self):
        self.edit_id = None
        self.form_title.configure(text="Add Site or App")
        self.submit_btn.configure(text="+ Add")
        self.cancel_btn.pack_forget()
        self.picker.reset()
        self.error.configure(text="")
        self.editor.load(None)

    def edit(self, item_id: int):
        item = self.draft.items[item_id]
        rule = next(r for r in item["rules"] if r["rule_type"] == self.rule_type)
        self.edit_id = item_id
        self.form_title.configure(text=f"Edit {item['display_name']}")
        self.submit_btn.configure(text="Update")
        self.cancel_btn.pack(side="left", padx=4)
        self.picker.load_item(item)
        self.error.configure(text="")
        self.editor.load(rule)
        self._parent_canvas.yview_moveto(0)
        self.highlight(item_id)

    def _submit(self):
        self.picker.entry.hide()
        if self.edit_id is not None and self.edit_id not in self.draft.items:  # expired meanwhile
            self.reset_form()
            return
        try:
            rule = self.editor.value()
            if self.edit_id is not None:
                # identical values leave the draft clean (Save stays grey)
                name = self.picker.name.get().strip() or self.draft.items[self.edit_id]["display_name"]
                self.draft.set_rule(self.edit_id, rule, name, self.picker.selected_block_type())
                self.reset_form()
                return
            target = self.picker.get()
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        existing = self.draft.find_item(target["targets"][0])
        if existing and any(r["rule_type"] == self.rule_type for r in existing["rules"]):
            self.error.configure(text=f"{existing['display_name']} already has a {RULE_NAME[self.rule_type]} - use Edit.")
            return
        if existing is None:
            existing = self.draft.add_item(target["name"], target["targets"], target["source"], target["kind"],
                                           target["block_type"], target["app_path"])
        self.draft.set_rule(existing["id"], rule, block_type=target["block_type"])
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
        groups = list(self.draft.groups.values())
        for item in items:
            rule = next(r for r in effective_rules(item, groups) if r["rule_type"] == self.rule_type and not r["group"])
            row = make_row(self.list_box)
            self.rows[item["id"]] = row
            name_cell(row, item)
            ctk.CTkLabel(row, text=describe_rule(rule, now, usage), wraplength=COLS[1] - 16,
                         justify="left", anchor="w").grid(row=0, column=1, padx=8, sticky="w")
            ctk.CTkLabel(row, **status_of(self.page, item, now, usage), justify="left").grid(
                row=0, column=2, padx=8, sticky="w")
            ctk.CTkButton(row, text="Edit", width=60, command=lambda i=item["id"]: self.edit(i)).grid(
                row=0, column=3, padx=4)
            ConfirmButton(row, lambda it=item: self._remove(it), width=70).grid(row=0, column=4, padx=4)

    def _remove(self, item):
        if self.edit_id == item["id"]:
            self.reset_form()
        self.draft.remove_rule(item["id"], self.rule_type)


class AllTab(ctk.CTkScrollableFrame):
    """Overview of every site/app with all its rules. Edit jumps to the rule's tab or group."""

    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.draft = page, page.draft
        self.title = ctk.CTkLabel(self, font=ctk.CTkFont(size=16, weight="bold"))
        self.title.pack(anchor="w", padx=10, pady=(4, 2))
        ctk.CTkLabel(self, text="To add sites or apps, open the tab for the kind of block you want (By Hours, "
                                "By Limit, ...) or put them in a group.", text_color=MUTED).pack(anchor="w", padx=10, pady=(0, 8))
        self.list_box = ctk.CTkFrame(self)
        self.list_box.pack(fill="x")

    def refresh(self, now, usage):
        items = self.draft.sorted_items()
        self.title.configure(text=f"All Blocked Sites & Apps ({len(items)})")
        for w in self.list_box.winfo_children():
            w.destroy()
        if not items:
            ctk.CTkLabel(self.list_box, text="Nothing blocked yet.", text_color=MUTED).pack(anchor="w", padx=16, pady=12)
            return
        header_row(self.list_box, ["Name", "Rules", "Status", "Alerts"])
        groups = list(self.draft.groups.values())
        for item in items:
            row = make_row(self.list_box)
            name_cell(row, item)
            rules_box = ctk.CTkFrame(row, fg_color="transparent")
            rules_box.grid(row=0, column=1, padx=8, sticky="w")
            for r, rule in enumerate(effective_rules(item, groups)):
                text = describe_rule(rule, now, usage)
                if rule["group"]:
                    text = f"[{rule['group']['name']}] {text}"
                ctk.CTkLabel(rules_box, text=text, wraplength=COLS[1] - 16, justify="left", anchor="w").grid(
                    row=r, column=0, sticky="w", pady=1)
            ctk.CTkLabel(row, **status_of(self.page, item, now, usage), justify="left").grid(
                row=0, column=2, padx=8, sticky="w")
            alerts = ctk.CTkOptionMenu(row, values=list(ALERTS), width=90,
                                       command=lambda v, i=item["id"]: self.draft.set_notify(i, ALERTS[v]))
            alerts.set(next(k for k, v in ALERTS.items() if v == item["notify"]))
            alerts.grid(row=0, column=3, padx=4)
            choices = [(f"Edit {RULE_NAME[r['rule_type']]}", lambda i=item["id"], t=r["rule_type"]: self.page.edit_rule(i, t))
                       for r in item["rules"] if r["rule_type"] in RULE_TAB]
            choices += [(f"Edit group {g['name']}", lambda g=g["id"]: self.page.edit_group(g))
                        for g in self.draft.groups_of(item["id"])]
            edit_btn = ctk.CTkButton(row, text="Edit ▾" if len(choices) > 1 else "Edit", width=70)
            edit_btn.configure(command=lambda b=edit_btn, c=choices: self._edit(b, c))
            edit_btn.grid(row=0, column=4, padx=4)
            ConfirmButton(row, lambda i=item["id"]: self.draft.remove_item(i), width=70).grid(row=0, column=5, padx=4)

    def _edit(self, button, choices):
        """One choice: do it. Several: ask which one with a small menu under the button."""
        if len(choices) == 1:
            choices[0][1]()
            return
        menu = tk.Menu(self, tearoff=False)
        for label, action in choices:
            menu.add_command(label=label, command=action)
        menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())


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
        self.tabs = {"All": AllTab(holder, self), "Groups": GroupsTab(holder, self)}
        for tab, rule_type in TAB_RULE.items():
            self.tabs[tab] = RuleTab(holder, self, rule_type)
        switches = ctk.CTkFrame(holder, fg_color="transparent")
        ctk.CTkLabel(switches, text="Switch limits are coming in a later phase.", text_color=MUTED).pack(
            anchor="w", padx=10, pady=10)
        self.tabs["By Switches"] = switches
        icons.prefetch([hosts[0] for sites in POPULAR_SITES.values() for hosts in sites.values()]
                       + [i["target"].split()[0] for i in self.draft.items.values() if i["item_type"] == "site"])
        app_browser.preload()
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
        """From the All tab: open the rule's tab with the item loaded in the form."""
        tab = RULE_TAB[rule_type]
        self.show_tab(tab)
        self.tabs[tab].edit(item_id)

    def edit_group(self, group_id: int):
        self.show_tab("Groups")
        self.tabs["Groups"].edit(group_id)

    def refresh(self):
        tab = self.tabs[self.tab_bar.get()]
        if hasattr(tab, "refresh"):
            now = now_from_db(self.app.db)
            tab.refresh(now, self.app.db.usage_lookup(now))

    def _auto_refresh(self):
        self.draft.refresh_if_clean()   # service may have removed expired blocks
        self.refresh()                  # countdowns, usage, "blocked now"
        self.after(REFRESH_MS, self._auto_refresh)
