"""Screen-time categories: Productive / Neutral / Distracting plus your own, each with a colour you can change.
Clicking an app's or site's category opens a small menu to pick one, add a new one or edit colours."""
import json
import re
import tkinter as tk
from tkinter import colorchooser

import customtkinter as ctk
from PIL import Image, ImageTk

from gui import theme
from gui.widgets import ConfirmButton, once

KEY = "stats.categories"   # JSON {"custom": [{"key", "name", "color"}], "colors": {key: "#rrggbb"}}
BUILTIN = [("productive", "Productive", theme.SUCCESS), ("neutral", "Neutral", theme.MUTED),
           ("games", "Games", "#8E6CEF"),
           ("distracting", "Distracting", theme.DANGER)]   # always red - it doesn't follow the accent colour
NEW_COLORS = ["#8E6CEF", "#2F9FD8", "#D4A017", "#E0559B", "#20B2AA", "#9C6B3F"]


def _config(db) -> dict:
    try:
        return json.loads(db.get_setting(KEY, "{}")) or {}
    except ValueError:
        return {}


def load(db) -> list[dict]:
    """[{key, name, color, builtin}] - colour is a (light, dark) pair or one "#rrggbb" for both.
    One of your own with the same key as a built-in one (a "Games" made before Games existed) is the same
    category, not a second one: it is left out, and anything tagged with that key keeps working."""
    cfg = _config(db)
    colors = cfg.get("colors", {})
    out = [{"key": k, "name": n, "color": colors.get(k, c), "builtin": True} for k, n, c in BUILTIN]
    taken = {k for k, _n, _c in BUILTIN}
    out += [{"key": c["key"], "name": c["name"], "color": colors.get(c["key"], c["color"]), "builtin": False}
            for c in cfg.get("custom", []) if c["key"] not in taken]
    return out


def colors_of(cats: list[dict]) -> dict:
    return {c["key"]: c["color"] for c in cats}


def names_of(cats: list[dict]) -> dict:
    return {c["key"]: c["name"] for c in cats}


def add(db, name: str, color: str) -> str:
    cfg = _config(db)
    taken = {k for k, _, _ in BUILTIN} | {c["key"] for c in cfg.get("custom", [])}
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "category"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    cfg.setdefault("custom", []).append({"key": key, "name": name, "color": color})
    db.set_setting(KEY, json.dumps(cfg))
    return key


def set_color(db, key: str, color: str):
    cfg = _config(db)
    cfg.setdefault("colors", {})[key] = color
    db.set_setting(KEY, json.dumps(cfg))


def remove(db, key: str):
    """Delete an own category; apps/sites in it go back to their default category."""
    cfg = _config(db)
    cfg["custom"] = [c for c in cfg.get("custom", []) if c["key"] != key]
    cfg.get("colors", {}).pop(key, None)
    db.set_setting(KEY, json.dumps(cfg))
    with db.conn:
        db.conn.execute("DELETE FROM categories WHERE category = ?", (key,))


# ---------------------------------------------------------------- GUI

def _swatch(color, size: int = 12) -> ImageTk.PhotoImage:
    return ImageTk.PhotoImage(Image.new("RGB", (size, size), theme.pick(color)))


def _ask_color(parent, title: str, initial) -> str | None:
    return colorchooser.askcolor(color=theme.pick(initial), title=title, parent=parent)[1]


def _guarded(guard, changes, proceed):
    """Run `proceed`, but if a guard is given (Anti-Bypass) it decides whether the challenge is needed first.
    Category changes feed the modes that block by category, so they're gated like any other loosening change."""
    if guard:
        guard(changes, proceed)
    else:
        proceed()


def build_menu(widget, db, kind: str, name: str, current: str, on_done, guard=None):
    """(menu, swatch images) for picking a category for (kind, name) - used on its own and as a submenu of the
    right-click menu. The caller has to keep the images, or Tk shows nothing next to the names.
    `guard` (app.guard) gates changing / adding a category behind the Anti-Bypass challenge when it's locked."""
    menu = tk.Menu(widget, tearoff=False)
    images = []
    for c in load(db):
        img = _swatch(c["color"])
        images.append(img)
        mark = "  ✓" if c["key"] == current else ""
        menu.add_command(label=f"  {c['name']}{mark}", image=img, compound="left",
                         command=lambda k=c["key"], nm=c["name"]: _guarded(
                             guard, [f"Move {name} to the {nm} category"],
                             lambda k=k: (db.set_category(kind, name, k), on_done())))
    menu.add_separator()
    menu.add_command(label="  New category...", command=lambda: _new(widget, db, kind, name, on_done, guard))
    menu.add_command(label="  Edit categories...",
                     command=lambda: once("categories", lambda: CategoryEditor(widget, db, on_done, guard)))
    return menu, images


def open_menu(widget, db, kind: str, name: str, current: str, on_done, guard=None):
    """Small menu under `widget`: pick a category for (kind, name), add a new one, or edit colours."""
    menu, images = build_menu(widget, db, kind, name, current, on_done, guard)
    widget._category_images = images   # Tk shows nothing if the images are garbage-collected
    menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())


def _new(widget, db, kind, name, on_done, guard=None):
    title = ctk.CTkInputDialog(text="Name of the new category:", title="New category").get_input()
    if not title or not title.strip():
        return
    count = len(load(db)) - len(BUILTIN)
    color = _ask_color(widget, f"Colour for {title.strip()}", NEW_COLORS[count % len(NEW_COLORS)]) \
        or NEW_COLORS[count % len(NEW_COLORS)]

    def do():
        key = add(db, title.strip(), color)
        db.set_category(kind, name, key)
        on_done()
    _guarded(guard, [f"Add the {title.strip()} category and move {name} into it"], do)


class CategoryEditor(ctk.CTkToplevel):
    """Change category colours; delete your own categories."""

    def __init__(self, master, db, on_done, guard=None):
        super().__init__(master)
        self.db, self.on_done, self.guard = db, on_done, guard
        self.title("Categories")
        self.geometry("420x360")
        self.transient(master.winfo_toplevel())
        self.after(50, self.grab_set)
        ctk.CTkLabel(self, text="Categories", font=theme.card_title()).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(self, text="Click a colour to change it. Deleting a category moves its apps and sites back "
                                "to their default.", text_color=theme.MUTED, font=theme.body(11), wraplength=380,
                     justify="left").pack(anchor="w", padx=16)
        self.rows = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.rows.pack(fill="both", expand=True, padx=10, pady=8)
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(buttons, text="+ New category", width=130, **theme.OUTLINE, command=self._new).pack(side="left")
        ctk.CTkButton(buttons, text="Done", width=80, command=self._close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._render()

    def _render(self):
        for w in self.rows.winfo_children():
            w.destroy()
        for c in load(self.db):
            row = ctk.CTkFrame(self.rows, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkButton(row, text="", width=28, height=22, corner_radius=4, fg_color=c["color"],
                          hover_color=c["color"], command=lambda c=c: self._recolor(c)).pack(side="left")
            ctk.CTkLabel(row, text=c["name"]).pack(side="left", padx=10)
            if not c["builtin"]:
                ConfirmButton(row, lambda k=c["key"]: self._remove(k), text="Delete", width=70, height=26).pack(
                    side="right")

    def _recolor(self, c):
        color = _ask_color(self, f"Colour for {c['name']}", c["color"])
        if color:
            set_color(self.db, c["key"], color)
            self._render()

    def _new(self):
        title = ctk.CTkInputDialog(text="Name of the new category:", title="New category").get_input()
        if title and title.strip():
            count = len(load(self.db)) - len(BUILTIN)

            def do():
                add(self.db, title.strip(), NEW_COLORS[count % len(NEW_COLORS)])
                self._render()
            _guarded(self.guard, [f"Add the {title.strip()} category"], do)

    def _remove(self, key: str):
        name = next((c["name"] for c in load(self.db) if c["key"] == key), key)
        _guarded(self.guard, [f"Delete the {name} category (its apps and sites go back to their default)"],
                 lambda: (remove(self.db, key), self._render()))

    def _close(self):
        self.grab_release()
        self.destroy()
        self.on_done()
