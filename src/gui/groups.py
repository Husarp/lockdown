"""Groups tab: named sets of rules (any combination) shared by member sites/apps.

Members inherit the group's rules; editing the group changes all of them. A member can be customized
(e.g. an "emergency" app gets a 5-minute allowance in the night block). A group daily limit is one
shared total for all members.
"""
import customtkinter as ctk

from gui import icons
from gui.rule_editors import EDITORS, RULE_NAMES, LimitEditor
from gui.target_picker import TargetPicker
from gui.widgets import ConfirmButton
from rules import describe_rule, effective_rules, item_block

MUTED = "gray60"
ERROR = "#f85149"
GREEN, ORANGE = "#3fb950", "#d29922"
CUSTOMIZABLE = ("scheduled", "time_limit", "temporary")


def _make_editor(parent, rule_type: str):
    return LimitEditor(parent, shared=True) if rule_type == "time_limit" else EDITORS[rule_type](parent)


class CustomizeMember(ctk.CTkToplevel):
    """Per-member versions of the group's rules. on_done(overrides) with {rule_type: rule}."""

    def __init__(self, master, member: dict, group_rules: list[dict], on_done):
        super().__init__(master)
        self.title(f"Customize {member['name']}")
        self.geometry("900x600")
        self.transient(master.winfo_toplevel())
        self.after(50, self.grab_set)
        self.on_done = on_done
        body = ctk.CTkScrollableFrame(self)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(body, text=f"{member['name']}: use the group's rule, or its own version",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(0, 8))
        self.parts = {}
        for rule in group_rules:
            t = rule["rule_type"]
            if t not in CUSTOMIZABLE:
                continue
            box = ctk.CTkFrame(body)
            box.pack(fill="x", pady=6)
            custom = ctk.CTkSwitch(box, text=f"Own {RULE_NAMES[t].lower()} for this member")
            custom.pack(anchor="w", padx=12, pady=(10, 4))
            editor = EDITORS[t](box)   # a customized limit counts for this member alone
            editor.load(member["overrides"].get(t) or rule)
            custom.configure(command=lambda s=custom, e=editor: e.pack(anchor="w", padx=12, pady=(0, 10))
                             if s.get() else e.pack_forget())
            if t in member["overrides"]:
                custom.select()
                editor.pack(anchor="w", padx=12, pady=(0, 10))
            self.parts[t] = (custom, editor)
        if not self.parts:
            ctk.CTkLabel(body, text="This group has no rules that can be customized (hours, daily limit, temporary).",
                         text_color=MUTED).pack(anchor="w")
        self.error = ctk.CTkLabel(body, text="", text_color=ERROR)
        self.error.pack(anchor="w")
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(anchor="e", padx=12, pady=(0, 12))
        ctk.CTkButton(buttons, text="Cancel", width=80, fg_color="transparent", border_width=1,
                      command=self._close).pack(side="left", padx=4)
        ctk.CTkButton(buttons, text="OK", width=80, command=self._ok).pack(side="left", padx=4)

    def _ok(self):
        try:
            overrides = {t: editor.value() for t, (switch, editor) in self.parts.items() if switch.get()}
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        self._close()
        self.on_done(overrides)

    def _close(self):
        self.grab_release()
        self.destroy()


class GroupEditor(ctk.CTkFrame):
    def __init__(self, master, tab):
        super().__init__(master)
        self.tab, self.draft = tab, tab.draft
        self.group_id: int | None = None
        self.members: list[dict] = []
        self.title = ctk.CTkLabel(self, font=ctk.CTkFont(size=16, weight="bold"))
        self.title.pack(anchor="w", padx=16, pady=(12, 8))
        name_row = ctk.CTkFrame(self, fg_color="transparent")
        name_row.pack(anchor="w", padx=16)
        ctk.CTkLabel(name_row, text="Name").pack(side="left", padx=(0, 8))
        self.name = ctk.CTkEntry(name_row, width=260, placeholder_text="e.g. Night schedule, Games")
        self.name.pack(side="left")

        ctk.CTkLabel(self, text="Rules (combine any)", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=16, pady=(14, 4))
        self.rule_parts = {}
        for t in EDITORS:
            box = ctk.CTkCheckBox(self, text=RULE_NAMES[t], command=lambda t=t: self._toggle(t))
            box.pack(anchor="w", padx=16, pady=(6, 2))
            editor = _make_editor(self, t)
            self.rule_parts[t] = (box, editor)

        ctk.CTkLabel(self, text="Members", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=16, pady=(14, 4))
        self.picker = TargetPicker(self, tab.page.app.db)
        self.picker.pack(anchor="w", padx=16)
        ctk.CTkButton(self.picker.buttons, text="+ Add member", width=110, command=self._add_member).pack(side="left", padx=4)
        self.members_box = ctk.CTkFrame(self, fg_color="transparent")
        self.members_box.pack(fill="x", padx=16, pady=6)
        self.error = ctk.CTkLabel(self, text="", text_color=ERROR)
        self.error.pack(anchor="w", padx=16)
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(anchor="w", padx=16, pady=(4, 14))
        ctk.CTkButton(buttons, text="Save group", width=110, command=self._save).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Cancel", width=80, fg_color="transparent", border_width=1,
                      command=self.tab.close_editor).pack(side="left")

    def _toggle(self, t: str):
        box, editor = self.rule_parts[t]
        if box.get():
            editor.pack(anchor="w", padx=40, pady=(0, 6), after=box)
        else:
            editor.pack_forget()

    def load(self, group: dict | None):
        self.group_id = group["id"] if group else None
        self.title.configure(text=f"Edit group {group['name']}" if group else "New group")
        self.name.delete(0, "end")
        if group:
            self.name.insert(0, group["name"])
        rules = {r["rule_type"]: r for r in (group or {}).get("rules", [])}
        for t, (box, editor) in self.rule_parts.items():
            editor.load(rules.get(t))
            box.select() if t in rules else box.deselect()
            self._toggle(t)
        self.members = []
        for item_id, overrides in (group or {}).get("members", {}).items():
            item = self.draft.items.get(item_id)
            if item:
                self.members.append({"item_id": item_id, "name": item["display_name"], "item": item,
                                     "overrides": dict(overrides or {})})
        self.picker.reset()
        self.error.configure(text="")
        self._render_members()

    def _current_rules(self) -> list[dict]:
        return [editor.value() for t, (box, editor) in self.rule_parts.items() if box.get()]

    def _add_member(self):
        self.picker.entry.hide()
        try:
            target = self.picker.get()
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        existing = self.draft.find_item(target["targets"][0])
        key = existing["id"] if existing else target["targets"][0]
        if any((m["item_id"] or m["target"]["targets"][0]) == key for m in self.members):
            self.error.configure(text=f"{target['name']} is already in this group.")
            return
        item = existing or {"display_name": target["name"], "target": " ".join(target["targets"]),
                            "item_type": target["kind"], "block_type": target["block_type"],
                            "app_path": target["app_path"]}
        self.members.append({"item_id": existing["id"] if existing else None, "name": item["display_name"],
                             "item": item, "target": target, "overrides": {}})
        self.picker.reset()
        self.error.configure(text="")
        self._render_members()

    def _render_members(self):
        for w in self.members_box.winfo_children():
            w.destroy()
        if not self.members:
            ctk.CTkLabel(self.members_box, text="No members yet.", text_color=MUTED).pack(anchor="w")
            return
        for m in self.members:
            row = ctk.CTkFrame(self.members_box, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=f"  {m['name']}", image=icons.for_item(m["item"], 18), compound="left",
                         width=220, anchor="w").pack(side="left")
            custom = ", ".join(RULE_NAMES[t] for t in m["overrides"])
            ctk.CTkLabel(row, text=f"customized: {custom}" if custom else "uses the group's rules",
                         text_color=ORANGE if custom else MUTED, width=260, anchor="w").pack(side="left", padx=8)
            ctk.CTkButton(row, text="Customize", width=90, fg_color="transparent", border_width=1,
                          command=lambda m=m: self._customize(m)).pack(side="left", padx=4)
            ctk.CTkButton(row, text="Remove", width=70, fg_color="transparent", border_width=1,
                          command=lambda m=m: self._remove_member(m)).pack(side="left", padx=4)

    def _customize(self, member):
        try:
            rules = self._current_rules()
        except ValueError as e:
            self.error.configure(text=str(e))
            return

        def done(overrides):
            member["overrides"] = overrides
            self._render_members()
        CustomizeMember(self, member, rules, done)

    def _remove_member(self, member):
        self.members.remove(member)
        self._render_members()

    def _save(self):
        name = self.name.get().strip()
        try:
            rules = self._current_rules()
            if not name:
                raise ValueError("Give the group a name.")
            if not rules:
                raise ValueError("Pick at least one rule.")
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        members = {}
        for m in self.members:
            item_id = m["item_id"]
            if item_id is None:
                t = m["target"]
                existing = self.draft.find_item(t["targets"][0])
                item_id = existing["id"] if existing else self.draft.add_item(
                    t["name"], t["targets"], t["source"], t["kind"], t["block_type"], t["app_path"])["id"]
            types = {r["rule_type"] for r in rules}
            members[item_id] = {k: v for k, v in m["overrides"].items() if k in types}
        self.draft.set_group(self.group_id, name, rules, members)
        self.tab.close_editor()


class GroupsTab(ctk.CTkScrollableFrame):
    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.draft = page, page.draft
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(4, 8))
        self.title = ctk.CTkLabel(top, font=ctk.CTkFont(size=16, weight="bold"))
        self.title.pack(side="left")
        self.new_btn = ctk.CTkButton(top, text="+ New group", width=110, command=lambda: self.open_editor(None))
        self.new_btn.pack(side="left", padx=16)
        ctk.CTkLabel(self, text="A group shares its rules with all members, e.g. \"Night schedule\" blocks all its "
                                "apps and sites at night; \"Games\" gives all games 2 h per day together.",
                     text_color=MUTED, wraplength=820, justify="left").pack(anchor="w", padx=10, pady=(0, 8))
        self.editor = GroupEditor(self, self)
        self.list_box = ctk.CTkFrame(self)
        self.list_box.pack(fill="x")

    def open_editor(self, group_id: int | None):
        self.editor.load(self.draft.groups.get(group_id) if group_id is not None else None)
        self.editor.pack(fill="x", pady=(0, 12), before=self.list_box)
        self._parent_canvas.yview_moveto(0)

    def edit(self, group_id: int):
        self.open_editor(group_id)

    def close_editor(self):
        self.editor.pack_forget()

    def refresh(self, now, usage):
        groups = self.draft.sorted_groups()
        self.title.configure(text=f"Groups ({len(groups)})")
        for w in self.list_box.winfo_children():
            w.destroy()
        if not groups:
            ctk.CTkLabel(self.list_box, text="No groups yet.", text_color=MUTED).pack(anchor="w", padx=16, pady=12)
            return
        saved_groups = list(self.draft.saved_groups.values())
        for g in groups:
            row = ctk.CTkFrame(self.list_box, fg_color="transparent")
            row.pack(fill="x", pady=4)
            left = ctk.CTkFrame(row, fg_color="transparent", width=300)
            left.pack(side="left", padx=8, anchor="n")
            ctk.CTkLabel(left, text=g["name"], font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
            names = [self.draft.items[i]["display_name"] for i in g["members"] if i in self.draft.items]
            ctk.CTkLabel(left, text=f"{len(names)} members: " + ", ".join(sorted(names)) if names else "no members",
                         text_color=MUTED, wraplength=280, justify="left", font=ctk.CTkFont(size=11)).pack(anchor="w")
            rules = "\n".join(describe_rule({**r, "usage_owner": f"group:{g['id']}"}, now, usage) for r in g["rules"])
            ctk.CTkLabel(row, text=rules, justify="left", width=280, anchor="w", wraplength=270).pack(side="left", padx=8)
            if self.draft.is_group_unsaved(g["id"]):
                status = {"text": "○ Not applied\n(unsaved)", "text_color": ORANGE}
            else:
                blocked = sum(1 for i in g["members"] if i in self.draft.saved_items and item_block(
                    effective_rules(self.draft.saved_items[i], saved_groups), now, usage))
                status = {"text": f"● {blocked} of {len(g['members'])} blocked now",
                          "text_color": GREEN if blocked else MUTED}
            ctk.CTkLabel(row, **status, width=150, anchor="w", justify="left").pack(side="left", padx=8)
            ctk.CTkButton(row, text="Edit", width=60, command=lambda gid=g["id"]: self.open_editor(gid)).pack(side="left", padx=4)
            ConfirmButton(row, lambda g=g: self._remove(g), width=70).pack(side="left", padx=4)

    def _remove(self, group):
        self.close_editor()
        self.draft.remove_group(group["id"])
