"""Groups tab: named sets of rules (any combination) shared by member sites/apps.

Members inherit the group's rules; editing the group changes all of them. A group daily limit is one
shared total for all members. A member can get extra rules ON TOP of the group's (e.g. YouTube at most 1 h a
day inside a "Fun" group of 2 h): the group's rules still apply to it and its time still fills the group's
pot - an extra rule only ever makes that member stricter, never the group looser (0.84.3).
"""
from datetime import datetime

import customtkinter as ctk

import block_method
from blocker.apps import make_block_type
from blocker.site_block import make_site_block_type
from blocker.site_block import text as site_block_text
from gui import icons, theme
from gui.components import BlockerRail, eyebrow
from gui.rule_editors import EDITORS, RULE_NAMES, RULE_SUBTITLES, HoursEditor, LimitEditor, SwitchEditor, summary
from gui.target_picker import ACTIONS, SITE_ACTIONS, TargetPicker
from gui.widgets import ConfirmButton, clear_entry, once
from rules import TIME_FMT, describe_rule, effective_rules, item_block

MUTED = theme.MUTED
ERROR = theme.DANGER
GREEN, ORANGE, RED = theme.ALLOWED, theme.PENDING, theme.BLOCKED
CUSTOMIZABLE = ("scheduled", "time_limit", "switch_limit", "temporary")


def _live(overrides: dict) -> dict:
    """A member's extra rules, less an extra temporary block that has run out (it blocks nothing, and clearing it
    away is free): it would otherwise show as "Keep (-3h left)" and keep the "+ extra limits" chip."""
    now = datetime.now()
    return {t: r for t, r in overrides.items()
            if not (t == "temporary" and r.get("temp_until") and datetime.strptime(r["temp_until"], TIME_FMT) <= now)}


def _make_editor(parent, rule_type: str):
    # group limits - and the allowance in blocked hours - are one shared total
    shared = {"time_limit": LimitEditor, "switch_limit": SwitchEditor, "scheduled": HoursEditor}
    return shared[rule_type](parent, shared=True) if rule_type in shared else EDITORS[rule_type](parent)


class WayBoxes(ctk.CTkFrame):
    """How one kind of member is blocked: the same tick boxes as an item's "When blocked" (apps: close / its
    background processes / minimize / cut internet; sites: can't load / close the tab / go back). `locked`: flags
    that already apply (the group's, in a member's window) - shown ticked and greyed, since a member can only add
    to the group's way, never take from it (0.84.11)."""

    def __init__(self, master, kind: str, locked: set[str] = frozenset(), on_change=None):
        super().__init__(master, fg_color="transparent")
        self.kind, self.locked, self.on_change = kind, set(locked), on_change
        self.boxes = TargetPicker._boxes(self, SITE_ACTIONS if kind == "site" else ACTIONS, self._ticked)
        for flag in self.locked & set(self.boxes):
            self.boxes[flag].select()
            self.boxes[flag].configure(state="disabled")
        self._update()

    def set(self, block_type: str | None):
        """Tick what `block_type` says (None: nothing of its own ticked)."""
        flags = block_method.flags(self.kind, block_type) if block_type else set()
        for flag, box in self.boxes.items():
            if flag not in self.locked:
                box.select() if flag in flags else box.deselect()
        self._update()

    def _on(self, flag: str) -> bool:
        return bool(self.boxes[flag].get())

    def _ticked(self, flag: str):
        other = {"close": "minimize", "minimize": "close"} if self.kind == "app" else {"close": "back", "back": "close"}
        if flag in other and self._on(flag) and other[flag] not in self.locked:
            self.boxes[other[flag]].deselect()   # the two exclude each other (closing covers the other)
        self._update()
        if self.on_change:
            self.on_change()

    def _update(self):
        """Closing covers minimizing / going back; "its background processes" only goes with closing."""
        closing = self._on("close")
        weaker = "minimize" if self.kind == "app" else "back"
        if weaker not in self.locked:
            if closing:
                self.boxes[weaker].deselect()
            self.boxes[weaker].configure(state="disabled" if closing else "normal")
        if self.kind == "app" and "background" not in self.locked:
            if not closing:
                self.boxes["background"].deselect()
            self.boxes["background"].configure(state="normal" if closing else "disabled")

    def value(self) -> str | None:
        """The ticked flags that aren't locked (None: none). Apps need close, minimize or cut internet - unless
        the rest comes from what is locked (e.g. "its background processes" on top of a group that closes)."""
        flags = [f for f in self.boxes if self._on(f) and f not in self.locked]
        if not flags:
            return None
        if self.kind == "site":
            return make_site_block_type(flags)
        if not (set(flags) | self.locked) - {"background"}:
            return None
        return make_block_type(flags)


WAY_NOTE = {"app": "Apps", "site": "Sites"}
APP_WORDS = (("close", "closed"), ("background", "background processes"), ("minimize", "minimised"),
             ("internet", "internet cut"))


def ways_text(group: dict) -> str:
    """How its members are blocked, for the list: "Apps closed · sites can't load"."""
    app, site = group.get("app_block"), group.get("site_block")
    parts = [f"apps {' + '.join(w for f, w in APP_WORDS if f in block_method.flags('app', app))}" if app else
             "apps as each is set",
             f"sites {site_block_text(site)}" if site else "sites as each is set"]
    return " · ".join(parts).capitalize()


class CustomizeMember(ctk.CTkToplevel):
    """Extra rules for one member, on top of the group's, and what it adds to how the group blocks it.
    on_done(overrides, way) with {rule_type: rule} and the member's own way (None: just the group's).
    group_way: the group's way for this member's kind as the editor has it (None: not chosen)."""

    def __init__(self, master, member: dict, group_rules: list[dict], on_done, group_way: str | None = None):
        super().__init__(master)
        self.title(f"Extra limits for {member['name']}")
        self.geometry("900x600")
        self.transient(master.winfo_toplevel())
        self.after(50, self.grab_set)
        self.on_done = on_done
        body = ctk.CTkScrollableFrame(self)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(body, text=f"Extra limits for {member['name']}, on top of the group's",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(body, text="The group's rules always apply to it too, and its time still counts towards the "
                                "group's shared limits. These can only make it stricter: blocked hours add to the "
                                "group's, allowed hours narrow them, and the first limit to run out blocks it.",
                     text_color=MUTED, wraplength=840, justify="left").pack(anchor="w", pady=(0, 8))
        self.parts = {}
        # every kind a member can be tightened with - not only the kinds the group has: a group with only blocked
        # hours can still give YouTube 1 h a day of its own
        of_group = {r["rule_type"]: r for r in group_rules}
        for t in CUSTOMIZABLE:
            rule = of_group.get(t)
            box = ctk.CTkFrame(body)
            box.pack(fill="x", pady=6)
            custom = ctk.CTkSwitch(box, text=f"Also limit this member: {RULE_NAMES[t].lower()}")
            custom.pack(anchor="w", padx=12, pady=(10, 4))
            editor = EDITORS[t](box)   # counts this member's own use - on top of the group's, which still applies
            editor.load(member["overrides"].get(t) or rule)
            custom.configure(command=lambda s=custom, e=editor: e.pack(anchor="w", padx=12, pady=(0, 10))
                             if s.get() else e.pack_forget())
            if t in member["overrides"]:
                custom.select()
                editor.pack(anchor="w", padx=12, pady=(0, 10))
            self.parts[t] = (custom, editor)
        # how it is blocked: the group's way always applies (greyed), this member can only add to it
        kind = block_method.kind(member["item"])
        base = group_way if group_way is not None else member["item"].get("block_type")
        box = ctk.CTkFrame(body)
        box.pack(fill="x", pady=6)
        ctk.CTkLabel(box, text="How this member is blocked", font=theme.semi(13)).pack(anchor="w", padx=12,
                                                                                       pady=(10, 0))
        ctk.CTkLabel(box, text="Greyed: the group's way, which always applies. Tick more to make it stricter for "
                               "this member only." if group_way is not None else
                               "The group hasn't chosen, so it is blocked the way it was set when it was added "
                               "(greyed). Tick more to make it stricter in this group.",
                     text_color=MUTED, wraplength=820, justify="left").pack(anchor="w", padx=12, pady=(0, 4))
        self.way = WayBoxes(box, kind, block_method.flags(kind, base))
        self.way.set(member.get("way"))
        self.way.pack(anchor="w", padx=12, pady=(0, 10))
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
        self.on_done(overrides, self.way.value())

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
        # pausing a group instead of removing it: the rules stay, they just stop applying
        self.disable_btn = ctk.CTkButton(head, text="Disable", width=90, **theme.SECONDARY,
                                         command=self._toggle_disabled)
        name_row = ctk.CTkFrame(self, fg_color="transparent")
        name_row.pack(fill="x", padx=16)
        ctk.CTkLabel(name_row, text="Name", width=60, anchor="w").pack(side="left")
        self.name = ctk.CTkEntry(name_row, placeholder_text="e.g. Night schedule, Games")
        self.name.pack(side="left", fill="x", expand=True)

        # blockers as a rail + one open panel (design 3b), like Blocking -> Add - not cards that grow downwards
        self.blockers = BlockerRail(self, _make_editor, RULE_NAMES, RULE_SUBTITLES, summary, rail_width=220)
        self.blockers.pack(fill="x", padx=16, pady=(14, 4))

        # how its members are blocked (0.84.11): before, each member kept the way it was set when it was added
        eyebrow(self, "How members are blocked").pack(anchor="w", padx=16, pady=(14, 4))
        ways = ctk.CTkFrame(self, fg_color="transparent")
        ways.pack(fill="x", padx=16)
        self.ways = {}
        for col, kind in enumerate(("app", "site")):
            part = ctk.CTkFrame(ways, fg_color="transparent")
            part.grid(row=0, column=col, sticky="nw", padx=(0, 32))
            ctk.CTkLabel(part, text=WAY_NOTE[kind], font=theme.semi(12)).pack(anchor="w")
            self.ways[kind] = WayBoxes(part, kind, on_change=self._way_note)
            self.ways[kind].pack(anchor="w")
        self.ways_note = ctk.CTkLabel(self, text="", text_color=MUTED, font=theme.body(11), wraplength=620,
                                      justify="left")
        self.ways_note.pack(anchor="w", padx=16, pady=(2, 0))

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
            self.disable_btn.configure(text="Enable" if group.get("disabled") else "Disable")
            self.disable_btn.pack(side="right", padx=6)
        else:
            self.remove_btn.pack_forget()
            self.disable_btn.pack_forget()
        clear_entry(self.name)
        if group:
            self.name.insert(0, group["name"])
        self.blockers.load((group or {}).get("rules", []))
        # a new group starts as an item does (close the app / can't load the site); an older one may have no choice
        for kind, default in (("app", "close"), ("site", "dns")):
            self.ways[kind].set(group.get(block_method.GROUP_KEYS[kind]) if group else default)
        self._way_note()
        self.members = []
        ways = (group or {}).get("member_blocks") or {}
        for item_id, overrides in (group or {}).get("members", {}).items():
            item = self.draft.items.get(item_id)
            if item:
                self.members.append({"item_id": item_id, "name": item["display_name"], "item": item,
                                     "overrides": _live(overrides or {}), "way": ways.get(item_id)})
        self.picker.reset()
        self.picker.pack_forget()
        self.error.configure(text="")
        self._render_members()

    def _current_rules(self) -> list[dict]:
        return self.blockers.rules()

    def _way_note(self):
        unset = [WAY_NOTE[k].lower() for k, w in self.ways.items() if w.value() is None]
        self.ways_note.configure(text=f"Nothing ticked for {' and '.join(unset)}: each is blocked the way it was "
                                      "set when it was added." if unset else "")

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
                             "item": item, "target": target, "overrides": {}, "way": None})
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
            custom = bool(m["overrides"]) or bool(m.get("way"))
            ctk.CTkLabel(chip, text="+ extra limits" if custom else "group rules", font=theme.body(10),
                         text_color=theme.ACCENT if custom else MUTED).pack(side="left", padx=(2, 4))
            ctk.CTkButton(chip, text="×", width=22, height=22, fg_color="transparent", hover_color=theme.BORDER,
                          text_color=MUTED, command=lambda m=m: self._remove_member(m)).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(self.members_box, text="Click a member to give it extra limits - or a stricter way of being "
                                            "blocked - on top of the group's.",
                     text_color=MUTED, font=theme.body(11)).pack(anchor="w", pady=(2, 0))

    def _customize(self, member):
        try:
            rules = self._current_rules()
        except ValueError as e:
            self.error.configure(text=str(e))
            return

        def done(overrides, way):
            member["overrides"], member["way"] = overrides, way
            self._render_members()
        group_way = self.ways[block_method.kind(member["item"])].value()
        once("member", lambda: CustomizeMember(self, member, rules, done, group_way))

    def _remove_member(self, member):
        self.members.remove(member)
        self._render_members()

    def _toggle_disabled(self):
        """Disabled groups keep everything and move to Overview > Disabled; enabling them again is one click."""
        if self.group_id is None:
            return
        group = self.draft.groups[self.group_id]
        name, off = group["name"], not group.get("disabled")
        self.tab.close_editor()
        self.draft.set_group_disabled(self.group_id, off)
        self.tab.page.confirm(f"Group {name} {'disabled' if off else 'enabled'}")

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
        members, member_ways = {}, {}
        for m in self.members:
            item_id = m["item_id"]
            if item_id is None:
                t = m["target"]
                existing = self.draft.find_item(t["targets"][0])
                item_id = existing["id"] if existing else self.draft.add_item(
                    t["name"], t["targets"], t["source"], t["kind"], t["block_type"], t["app_path"])["id"]
            # a member's extra rules stay whatever the group's rules are: dropping one when the group no longer
            # has a rule of its kind removed a limit without a word (and asked for the challenge with no reason
            # to see)
            members[item_id] = {k: v for k, v in m["overrides"].items() if k in CUSTOMIZABLE}
            if m.get("way"):
                member_ways[item_id] = m["way"]
        added = self.group_id is None
        ways = {"app_block": self.ways["app"].value(), "site_block": self.ways["site"].value(),
                "member_blocks": member_ways}
        self.draft.set_group(self.group_id, name, rules, members, ways)
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
        # the list card is as tall as its groups (see _fit_list) - not a full-height panel that's mostly empty
        self.cols = cols
        self.list_box = ctk.CTkScrollableFrame(cols, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER)
        self.list_box.grid(row=0, column=0, sticky="new", padx=(0, 6))
        cols.bind("<Configure>", lambda e: self._fit_list())
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

    def _fit_list(self):
        """Size the list to its content, up to the room there is; the scrollbar only shows when it has to scroll."""
        if not self.winfo_exists():
            return
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        need = self.list_box.winfo_reqheight() / scale + 4
        room = max(120, self.cols.winfo_height() / scale - 14)
        height = int(min(need, room))
        if height == getattr(self, "_list_height", None):
            return
        self._list_height = height
        self.list_box.configure(height=height)
        if need > room:
            self.list_box._scrollbar.grid()
        else:
            self.list_box._scrollbar.grid_remove()

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
        self.after_idle(self._fit_list)   # (once the new rows have a size)
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
            pot = {"usage_owner": f"group:{g['id']}"}
            rules = " · ".join(describe_rule({**r, **pot, "rule_key": f"g{g['id']}{r['rule_type']}"}, now, usage)
                               .replace(":\n", " ").replace("\n", " · ") for r in g["rules"])
            if self.draft.is_group_unsaved(g["id"]):
                status, color = "Not applied (unsaved)", ORANGE
            elif g.get("disabled"):
                status, color = "Disabled - nothing is enforced", MUTED
            else:
                blocked = sum(1 for i in g["members"] if i in self.draft.saved_items and item_block(
                    effective_rules(self.draft.saved_items[i], saved_groups), now, usage))
                status = f"{blocked} of {len(g['members'])} blocked now" if blocked else "Allowed now"
                color = RED if blocked else GREEN
            lines = [(g["name"], theme.semi(13), theme.TEXT),
                     (f"{len(names)} members · " + ", ".join(names) if names else "no members", theme.body(11), MUTED),
                     (rules, theme.body(11), theme.TEXT), (ways_text(g), theme.body(11), MUTED),
                     (status, theme.body(11), color)]
            widgets = [entry, texts]
            for text, font, fg in lines:
                label = ctk.CTkLabel(texts, text=text, font=font, text_color=fg, anchor="w", justify="left",
                                     wraplength=230)
                label.pack(anchor="w")
                widgets.append(label)
            for w in widgets:
                w.bind("<Button-1>", lambda e, gid=g["id"]: self.open_editor(gid))
