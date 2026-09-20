"""Groups tab: named sets of rules (any combination) shared by member sites/apps.

Members inherit the group's rules; editing the group changes all of them. A member can be customized
(e.g. an "emergency" app gets a 5-minute allowance in the night block). A group daily limit is one
shared total for all members.
"""
import customtkinter as ctk

from gui import icons, theme
from gui.components import BlockerRail, eyebrow
from gui.rule_editors import EDITORS, RULE_NAMES, RULE_SUBTITLES, LimitEditor, SwitchEditor, summary
from gui.target_picker import TargetPicker
from gui.widgets import ConfirmButton, clear_entry
from rules import describe_rule, effective_rules, item_block

MUTED = theme.MUTED
ERROR = theme.DANGER
GREEN, ORANGE, RED = theme.ALLOWED, theme.PENDING, theme.BLOCKED
CUSTOMIZABLE = ("scheduled", "time_limit", "switch_limit", "temporary")


def _make_editor(parent, rule_type: str):
    shared = {"time_limit": LimitEditor, "switch_limit": SwitchEditor}   # group limits are one shared total
    return shared[rule_type](parent, shared=True) if rule_type in shared else EDITORS[rule_type](parent)


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
            ctk.CTkLabel(body, text="This group has no rules that can be customized (hours, limits, temporary).",
                         text_color=MUTED).pack(anchor="w")
        self.error = ctk.CTkLabel(body, text="", text_color=ERROR)
        self.error.pack(anchor="w")
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(anchor="e", padx=12, pady=(0, 12))
        ctk.CTkButton(buttons, text="Cancel", width=80, **theme.OUTLINE,
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
    """Name, blockers (collapsible cards) and members (chips) of one group."""

    def __init__(self, master, tab):
        super().__init__(master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER)
        self.tab, self.draft = tab, tab.draft
        self.group_id: int | None = None
        self.members: list[dict] = []
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 8))
        ctk.CTkFrame(head, width=3, height=24, corner_radius=0, fg_color=theme.ACCENT).pack(side="left", padx=(0, 10))
        self.title = ctk.CTkLabel(head, font=theme.body(18, "bold"))
        self.title.pack(side="left")
        ctk.CTkButton(head, text="Done", width=70, command=self._save).pack(side="right")
        ctk.CTkButton(head, text="Cancel", width=70, **theme.OUTLINE, command=self.tab.close_editor).pack(
            side="right", padx=6)
        self.remove_btn = ConfirmButton(head, self._remove, text="Remove group", confirm_text="Confirm remove",
                                        width=120)
        name_row = ctk.CTkFrame(self, fg_color="transparent")
        name_row.pack(fill="x", padx=16)
        ctk.CTkLabel(name_row, text="Name", width=60, anchor="w").pack(side="left")
        self.name = ctk.CTkEntry(name_row, placeholder_text="e.g. Night schedule, Games")
        self.name.pack(side="left", fill="x", expand=True)

        # blockers as a rail + one open panel (design 3b), like Blocking -> Add - not cards that grow downwards
        self.blockers = BlockerRail(self, _make_editor, RULE_NAMES, RULE_SUBTITLES, summary, rail_width=220)
        self.blockers.pack(fill="x", padx=16, pady=(14, 4))

        eyebrow(self, "Members").pack(anchor="w", padx=16, pady=(14, 4))
        self.members_box = ctk.CTkFrame(self, fg_color="transparent")
        self.members_box.pack(fill="x", padx=16)
        self.add_btn = ctk.CTkButton(self, text="+ Add member", width=120, **theme.OUTLINE, command=self._show_picker)
        self.add_btn.pack(anchor="w", padx=16, pady=(6, 0))
        self.picker = TargetPicker(self, tab.page.app.db)
        ctk.CTkButton(self.picker.buttons, text="Add", width=70, command=self._add_member).pack(side="left", padx=4)
        self.error = ctk.CTkLabel(self, text="", text_color=ERROR)
        self.error.pack(anchor="w", padx=16, pady=(4, 10))

    def _show_picker(self):
        self.picker.pack(anchor="w", padx=16, pady=(6, 0), before=self.error)

    def load(self, group: dict | None):
        self.group_id = group["id"] if group else None
        self.title.configure(text="Edit group" if group else "New group")
        if group:
            self.remove_btn.pack(side="right")
        else:
            self.remove_btn.pack_forget()
        clear_entry(self.name)
        if group:
            self.name.insert(0, group["name"])
        self.blockers.load((group or {}).get("rules", []))
        self.members = []
        for item_id, overrides in (group or {}).get("members", {}).items():
            item = self.draft.items.get(item_id)
            if item:
                self.members.append({"item_id": item_id, "name": item["display_name"], "item": item,
                                     "overrides": dict(overrides or {})})
        self.picker.reset()
        self.picker.pack_forget()
        self.error.configure(text="")
        self._render_members()

    def _current_rules(self) -> list[dict]:
        return self.blockers.rules()

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
        self.picker.pack_forget()
        self.error.configure(text="")
        self._render_members()

    def _render_members(self):
        for w in self.members_box.winfo_children():
            w.destroy()
        if not self.members:
            ctk.CTkLabel(self.members_box, text="No members yet.", text_color=MUTED).pack(anchor="w")
            return
        grid = ctk.CTkFrame(self.members_box, fg_color="transparent")
        grid.pack(anchor="w")
        for i, m in enumerate(self.members):
            chip = ctk.CTkFrame(grid, fg_color=theme.SURFACE2, border_width=1, border_color=theme.BORDER,
                                corner_radius=4)
            chip.grid(row=i // 3, column=i % 3, padx=(0, 8), pady=4, sticky="w")
            name = ctk.CTkButton(chip, text=f" {m['name']}", image=icons.for_item(m["item"], 16), compound="left",
                                 width=10, height=28, fg_color="transparent", hover_color=theme.BORDER,
                                 text_color=theme.TEXT, font=theme.semi(12), command=lambda m=m: self._customize(m))
            name.pack(side="left", padx=(4, 0))
            custom = bool(m["overrides"])
            ctk.CTkLabel(chip, text="customised" if custom else "group rules", font=theme.body(10),
                         text_color=theme.ACCENT if custom else MUTED).pack(side="left", padx=(2, 4))
            ctk.CTkButton(chip, text="×", width=22, height=22, fg_color="transparent", hover_color=theme.BORDER,
                          text_color=MUTED, command=lambda m=m: self._remove_member(m)).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(self.members_box, text="Click a member to give it its own version of the group's rules.",
                     text_color=MUTED, font=theme.body(11)).pack(anchor="w", pady=(2, 0))

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

    def _remove(self):
        if self.group_id is not None:
            name = self.name.get().strip()
            self.tab.close_editor()
            self.draft.remove_group(self.group_id)
            self.tab.page.confirm(f"Group {name} removed")

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
        added = self.group_id is None
        self.draft.set_group(self.group_id, name, rules, members)
        self.tab.close_editor()
        self.tab.page.confirm(f"Group {name} {'added' if added else 'saved'}")


class GroupsTab(ctk.CTkFrame):
    """Groups on the left, the selected group's editor on the right."""

    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.draft = page, page.draft
        self.selected: int | None = None
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", pady=(0, 10), padx=(0, 18))   # line up with the cards (scrollbar)
        ctk.CTkLabel(top, text="A group shares its rules with all members, e.g. \"Night schedule\" blocks its apps and "
                               "sites at night; \"Games\" gives all games 2 h a day together.",
                     text_color=MUTED, font=theme.body(11), wraplength=620, justify="left").pack(side="left")
        ctk.CTkButton(top, text="+ New group", width=120, command=lambda: self.open_editor(None)).pack(side="right")
        cols = ctk.CTkFrame(self, fg_color="transparent")
        cols.pack(fill="both", expand=True)
        cols.grid_columnconfigure(0, weight=1, uniform="g")
        cols.grid_columnconfigure(1, weight=3, uniform="g")   # the editor's rail + panel needs the room
        cols.grid_rowconfigure(0, weight=1)
        self.list_box = ctk.CTkScrollableFrame(cols, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER)
        self.list_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.right = ctk.CTkScrollableFrame(cols, fg_color="transparent")
        self.right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        # empty state: a quiet card instead of a bare line of text floating in the corner
        self.placeholder = ctk.CTkFrame(self.right, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER,
                                        corner_radius=4)
        ctk.CTkLabel(self.placeholder, text="No group open", font=theme.card_title()).pack(pady=(44, 4))
        ctk.CTkLabel(self.placeholder, text="Pick a group on the left to edit its rules and members,\n"
                                            "or make a new one.", text_color=MUTED, justify="center").pack()
        ctk.CTkButton(self.placeholder, text="+ New group", width=120, **theme.OUTLINE,
                      command=lambda: self.open_editor(None)).pack(pady=(14, 44))
        self.placeholder.pack(fill="x")
        self.editor = GroupEditor(self.right, self)
        self.last = (None, None)

    def open_editor(self, group_id: int | None):
        self.selected = group_id
        self.editor.load(self.draft.groups.get(group_id) if group_id is not None else None)
        self.placeholder.pack_forget()
        self.editor.pack(fill="x")
        self.right._parent_canvas.yview_moveto(0)
        self.refresh(*self.last)

    def edit(self, group_id: int):
        self.open_editor(group_id)

    def close_editor(self):
        self.selected = None
        self.editor.pack_forget()
        self.placeholder.pack(fill="x")

    def refresh(self, now, usage):
        if now is None:
            return
        self.last = (now, usage)
        for w in self.list_box.winfo_children():
            w.destroy()
        groups = self.draft.sorted_groups()
        if not groups:
            ctk.CTkLabel(self.list_box, text="No groups yet.", text_color=MUTED).pack(anchor="w", padx=12, pady=12)
            return
        saved_groups = list(self.draft.saved_groups.values())
        for g in groups:
            on = g["id"] == self.selected
            entry = ctk.CTkFrame(self.list_box, fg_color=theme.SURFACE2 if on else "transparent", corner_radius=4)
            entry.pack(fill="x", padx=4, pady=3)
            ctk.CTkFrame(entry, width=3, height=1, corner_radius=0,
                         fg_color=theme.ACCENT if on else "transparent").pack(side="left", fill="y")
            texts = ctk.CTkFrame(entry, fg_color="transparent")
            texts.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            names = sorted(self.draft.items[i]["display_name"] for i in g["members"] if i in self.draft.items)
            rules = " · ".join(describe_rule({**r, "usage_owner": f"group:{g['id']}"}, now, usage)
                               .replace(":\n", " ").replace("\n", " · ") for r in g["rules"])
            if self.draft.is_group_unsaved(g["id"]):
                status, color = "Not applied (unsaved)", ORANGE
            else:
                blocked = sum(1 for i in g["members"] if i in self.draft.saved_items and item_block(
                    effective_rules(self.draft.saved_items[i], saved_groups), now, usage))
                status = f"{blocked} of {len(g['members'])} blocked now" if blocked else "Allowed now"
                color = RED if blocked else GREEN
            lines = [(g["name"], theme.semi(13), theme.TEXT),
                     (f"{len(names)} members · " + ", ".join(names) if names else "no members", theme.body(11), MUTED),
                     (rules, theme.body(11), theme.TEXT), (status, theme.body(11), color)]
            widgets = [entry, texts]
            for text, font, fg in lines:
                label = ctk.CTkLabel(texts, text=text, font=font, text_color=fg, anchor="w", justify="left",
                                     wraplength=230)
                label.pack(anchor="w")
                widgets.append(label)
            for w in widgets:
                w.bind("<Button-1>", lambda e, gid=g["id"]: self.open_editor(gid))
