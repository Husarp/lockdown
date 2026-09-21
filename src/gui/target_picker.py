"""'What to block' input shared by the rule tabs and the group editor: a site, an app or a whole category."""
import tkinter as tk

import customtkinter as ctk

from gui import theme

from gui.components import help_icon

from blocker.apps import PROTECTED, block_flags, make_block_type
from blocker.site_block import make_site_block_type, site_flags
from blocker.hosts import normalize_host
from gui.app_browser import AppBrowser
from gui.site_picker import SiteEntry
from gui.widgets import clear_entry, once
from importer.popular import POPULAR_SITES

ACTIONS = {"close": ("Close app", "asked to close first (10 s to save), then force-closed; started while blocked: "
                                  "closed at once"),
           "background": ("Also close its background processes", "once the app is closed: what it started and what "
                                                                   "runs from its install folder"),
           "minimize": ("Minimize", "keeps it running (e.g. a browser with many tabs) but minimizes it whenever it's opened"),
           "internet": ("Block internet", "it can't connect to the internet")}
# the same idea for a website: it can be sent nowhere, and / or its tab dealt with when you open it anyway
SITE_ACTIONS = {"dns": ("Can't load it", "the address goes nowhere, in every browser - what Lockdown has "
                                         "always done"),
                "close": ("Close the tab", "if you open it anyway, the tab is closed (a browser with one tab "
                                           "gets a fresh tab first)"),
                "back": ("Go back", "the browser goes back instead; if that doesn't leave the page, the tab is "
                                    "closed")}
MUTED = theme.MUTED


def popular_hosts(host: str) -> tuple[str, list[str]] | None:
    for sites in POPULAR_SITES.values():
        for name, hostnames in sites.items():
            if host in hostnames:
                return name, hostnames
    return None


def guess_name(host: str) -> str:
    entry = popular_hosts(host)
    return entry[0] if entry else host.split(".")[-2].capitalize()


class TargetPicker(ctk.CTkFrame):
    """Site entry (with suggestions, popular sites included) or app, display name, "Browse apps", and for apps
    the block type. Extra widgets (Add/Update/Cancel buttons) can be packed into `self.buttons`."""

    def __init__(self, master, db, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.db = db
        self.app: dict | None = None      # picked app {name, exe, path}
        self.category: str | None = None  # picked category (blocks everything in it)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(anchor="w")
        self.entry = SiteEntry(row, db, self._fill_site, self._fill_app, width=240,
                               placeholder_text="site or app (reddit.com, Discord...)")
        self.entry.pack(side="left", padx=(0, 8))
        self.entry.bind("<KeyRelease>", lambda e: self._typed(), add="+")
        self.name = ctk.CTkEntry(row, width=160, placeholder_text="Display name")
        self.name.pack(side="left", padx=4)
        self.pickers = ctk.CTkFrame(row, fg_color="transparent")
        self.pickers.pack(side="left")
        ctk.CTkButton(self.pickers, text="Browse apps", width=110, **theme.OUTLINE,
                      command=lambda: once("apps", lambda: AppBrowser(self, self._fill_app))).pack(
            side="left", padx=4)
        ctk.CTkButton(self.pickers, text="Category...", width=100, **theme.OUTLINE,
                      command=self._pick_category).pack(side="left", padx=(0, 4))
        # optional slot for Add/Update buttons; tiny when empty (an empty frame would default to 200x200)
        self.buttons = ctk.CTkFrame(row, fg_color="transparent", width=1, height=1)
        self.buttons.pack(side="left")
        self.block_row = ctk.CTkFrame(self, fg_color="transparent")
        self.block_label = ctk.CTkLabel(self.block_row, text="")
        self.block_label.pack(anchor="w")
        self.app_box = ctk.CTkFrame(self.block_row, fg_color="transparent")
        self.site_box = ctk.CTkFrame(self.block_row, fg_color="transparent")
        self.flag_boxes = self._boxes(self.app_box, ACTIONS, self._flag_ticked)
        self.site_boxes = self._boxes(self.site_box, SITE_ACTIONS, self._site_flag_ticked)
        self.reset()

    @staticmethod
    def _boxes(parent, actions: dict, on_tick) -> dict:
        boxes = {}
        for flag, (label, note) in actions.items():
            line = ctk.CTkFrame(parent, fg_color="transparent")
            line.pack(anchor="w", pady=1, padx=(28 if flag == "background" else 0, 0))   # goes with Close app
            box = ctk.CTkCheckBox(line, text=label, command=lambda f=flag: on_tick(f))
            box.pack(side="left")
            help_icon(line, note[0].upper() + note[1:] + ".").pack(side="left", padx=(4, 0))
            boxes[flag] = box
        return boxes

    # ---------- filling ----------

    def _pick_category(self):
        """Block a whole category instead of one site or app - everything in it, on the blocklist or not."""
        from gui import categories
        menu = tk.Menu(self, tearoff=False)
        images = []
        for c in categories.load(self.db):
            img = categories._swatch(c["color"])
            images.append(img)
            menu.add_command(label=f"  {c['name']}", image=img, compound="left",
                             command=lambda c=c: self._fill_category(c["key"], c["name"]))
        self._category_images = images   # Tk shows nothing if the images are garbage-collected
        menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())

    def _fill_category(self, key: str, name: str):
        self.app = None
        self.category = key
        self.entry.configure(state="normal")
        self._set(self.entry, f"category \u00b7 {name}")
        self.entry.configure(state="disabled")   # a category isn't typed, it's chosen
        self._set(self.name, name)
        self._update_block_row()

    def _fill_site(self, name: str, host: str):
        self.app = None
        self.category = None
        self.entry.configure(state="normal")
        self._set(self.entry, host)
        self._set(self.name, name)
        self._update_block_row()

    def _fill_app(self, app: dict):
        self.app = app
        self.category = None
        self.entry.configure(state="normal")
        self._set(self.entry, app["exe"])
        self._set(self.name, app["name"])
        if app.get("steam"):   # a game can run from several exes / a launcher: close everything in its folder
            self._set_block_type("close,background")
        self._update_block_row()

    def _typed(self):
        if self.app and self.entry.get().strip().lower() != self.app["exe"]:
            self.app = None
        self._update_block_row()

    @staticmethod
    def _set(entry, text):
        entry.delete(0, "end")
        entry.insert(0, text)

    def _is_app(self) -> bool:
        return not self.category and (bool(self.app) or self.entry.get().strip().lower().endswith(".exe"))

    def _wants_block_row(self) -> bool:
        """Apps - and a category, whose apps get closed / minimised the same way."""
        return self._is_app() or bool(self.category)

    def _is_site(self) -> bool:
        """A site, once there is something in the box (an empty box is neither a site nor an app yet)."""
        return not self.category and not self._is_app() and bool(self.entry.get().strip())

    def _update_block_row(self):
        for box in (self.app_box, self.site_box):
            box.pack_forget()
        if self._wants_block_row():
            self.block_label.configure(text="When blocked, its apps (tick one or more; Close and Minimize exclude "
                                            "each other):" if self.category else
                                            "When blocked (tick one or more; Close and Minimize exclude each other):")
            self.app_box.pack(anchor="w")
            self.block_row.pack(anchor="w", pady=(8, 0))
        elif self._is_site():
            self.block_label.configure(text="When blocked (tick one or more; Close the tab and Go back exclude "
                                            "each other):")
            self.site_box.pack(anchor="w")
            self.block_row.pack(anchor="w", pady=(8, 0))
        else:
            self.block_row.pack_forget()

    # ---------- public ----------

    def reset(self):
        self.app = None
        self.category = None
        self.entry.configure(state="normal")
        clear_entry(self.entry)
        clear_entry(self.name)
        self._set_block_type("kill")
        self._set_site_block_type(None)
        self.pickers.pack(side="left", before=self.buttons)
        self._update_block_row()

    def load_item(self, item: dict):
        """Edit mode: show the item; its site/app can't be changed, name and block type can."""
        self.app = {"exe": item["target"], "path": item.get("app_path"), "name": item["display_name"]} \
            if item["item_type"] == "app" else None
        self.category = item["target"] if item["item_type"] == "category" else None
        if self.category:
            self.entry.configure(state="normal")
            self._set(self.entry, f"category \u00b7 {item['display_name']}")
            self.entry.configure(state="disabled")
            self._set(self.name, item["display_name"])
            self.pickers.pack_forget()
            self._update_block_row()
            return
        self.entry.configure(state="normal")
        self._set(self.entry, item["target"].split()[0])
        self.entry.configure(state="disabled")
        self._set(self.name, item["display_name"])
        if item["item_type"] == "app":
            self._set_block_type(item.get("block_type"))
        else:
            self._set_site_block_type(item.get("block_type"))
        self.pickers.pack_forget()
        self._update_block_row()

    def _set_block_type(self, block_type: str | None):
        flags = block_flags(block_type)
        for flag, box in self.flag_boxes.items():
            box.select() if flag in flags else box.deselect()
        self._update_background()

    def _set_site_block_type(self, block_type: str | None):
        flags = site_flags(block_type)
        for flag, box in self.site_boxes.items():
            box.select() if flag in flags else box.deselect()

    def _site_flag_ticked(self, flag: str):
        other = {"close": "back", "back": "close"}.get(flag)
        if other and self.site_boxes[flag].get():
            self.site_boxes[other].deselect()   # closing the tab and going back are two ways of doing one thing

    def _flag_ticked(self, flag: str):
        other = {"close": "minimize", "minimize": "close"}.get(flag)
        if other and self.flag_boxes[flag].get():
            self.flag_boxes[other].deselect()   # closing makes minimizing pointless
        self._update_background()

    def _update_background(self):
        """"Also close its background processes" only goes with Close app."""
        box = self.flag_boxes["background"]
        if not self.flag_boxes["close"].get():
            box.deselect()
        box.configure(state="normal" if self.flag_boxes["close"].get() else "disabled")

    def selected_block_type(self) -> str | None:
        """None if nothing is ticked."""
        flags = [f for f, box in self.flag_boxes.items() if box.get()]
        return make_block_type(flags) if set(flags) - {"background"} else None

    def selected_site_block_type(self) -> str | None:
        """None if nothing is ticked (the caller then says so)."""
        flags = [f for f, box in self.site_boxes.items() if box.get()]
        return make_site_block_type(flags) if flags else None

    def get(self) -> dict:
        """{kind, targets, name, source, block_type, app_path}. Raises ValueError with a user-facing message."""
        text = self.entry.get().strip()
        name = self.name.get().strip()
        if self.category:
            return {"kind": "category", "targets": [self.category], "name": name or text,
                    "source": "category", "block_type": self.selected_block_type(), "app_path": None}
        if self._is_app():
            exe = (self.app["exe"] if self.app else text).lower()
            if exe in PROTECTED:
                raise ValueError(f"{exe} is part of Windows (or Lockdown) and can't be blocked.")
            if self.selected_block_type() is None:
                raise ValueError("Tick at least one of Close app / Minimize / Block internet.")
            return {"kind": "app", "targets": [exe], "name": name or (self.app or {}).get("name") or exe[:-4].title(),
                    "source": "app-browser" if self.app else "manual", "block_type": self.selected_block_type(),
                    "app_path": (self.app or {}).get("path")}
        host = normalize_host(text)
        entry = popular_hosts(host)   # known site: block all of its hostnames
        if self.selected_site_block_type() is None:
            raise ValueError("Tick at least one of Can't load it / Close the tab / Go back.")
        return {"kind": "site", "targets": entry[1] if entry else [host], "name": name or guess_name(host),
                "source": "popular" if entry else "manual",
                "block_type": self.selected_site_block_type(), "app_path": None}
