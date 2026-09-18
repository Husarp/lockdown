"""'What to block' input shared by the rule tabs and the group editor: a site or an app."""
import customtkinter as ctk

from blocker.apps import PROTECTED
from blocker.hosts import normalize_host
from gui.app_browser import AppBrowser
from gui.site_picker import PopularSitesPopup, SiteEntry
from importer.popular import POPULAR_SITES

BLOCK_TYPES = {"Close app": "kill", "Block internet": "firewall", "Both": "both"}
MUTED = "gray60"


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
    """Site entry (with suggestions) or app, display name, "+ Popular sites", "Browse apps", and for apps
    the block type. Extra widgets (Add/Update/Cancel buttons) can be packed into `self.buttons`."""

    def __init__(self, master, db, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.app: dict | None = None      # picked app {name, exe, path}
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(anchor="w")
        self.entry = SiteEntry(row, db, self._fill_site, width=240, placeholder_text="site (reddit.com) or app.exe")
        self.entry.pack(side="left", padx=(0, 8))
        self.entry.bind("<KeyRelease>", lambda e: self._typed(), add="+")
        self.name = ctk.CTkEntry(row, width=160, placeholder_text="Display name")
        self.name.pack(side="left", padx=4)
        self.pickers = ctk.CTkFrame(row, fg_color="transparent")
        self.pickers.pack(side="left")
        ctk.CTkButton(self.pickers, text="+ Popular sites", width=120, fg_color="transparent", border_width=1,
                      command=lambda: PopularSitesPopup(self, self._fill_site)).pack(side="left", padx=4)
        ctk.CTkButton(self.pickers, text="Browse apps", width=110, fg_color="transparent", border_width=1,
                      command=lambda: AppBrowser(self, self._fill_app)).pack(side="left", padx=4)
        self.buttons = ctk.CTkFrame(row, fg_color="transparent")
        self.buttons.pack(side="left")
        self.block_row = ctk.CTkFrame(self, fg_color="transparent")
        ctk.CTkLabel(self.block_row, text="When blocked:").pack(side="left", padx=(0, 8))
        self.block_type = ctk.CTkSegmentedButton(self.block_row, values=list(BLOCK_TYPES))
        self.block_type.pack(side="left")
        ctk.CTkLabel(self.block_row, text="(closing asks the app nicely first, force-closes after 10 s)",
                     text_color=MUTED).pack(side="left", padx=8)
        self.reset()

    # ---------- filling ----------

    def _fill_site(self, name: str, host: str):
        self.app = None
        self._set(self.entry, host)
        self._set(self.name, name)
        self._update_block_row()

    def _fill_app(self, app: dict):
        self.app = app
        self._set(self.entry, app["exe"])
        self._set(self.name, app["name"])
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
        return bool(self.app) or self.entry.get().strip().lower().endswith(".exe")

    def _update_block_row(self):
        if self._is_app():
            self.block_row.pack(anchor="w", pady=(8, 0))
        else:
            self.block_row.pack_forget()

    # ---------- public ----------

    def reset(self):
        self.app = None
        self.entry.configure(state="normal")
        self.entry.delete(0, "end")
        self.name.delete(0, "end")
        self.block_type.set("Close app")
        self.pickers.pack(side="left", before=self.buttons)
        self._update_block_row()

    def load_item(self, item: dict):
        """Edit mode: show the item; its site/app can't be changed, name and block type can."""
        self.app = {"exe": item["target"], "path": item.get("app_path"), "name": item["display_name"]} \
            if item["item_type"] == "app" else None
        self.entry.configure(state="normal")
        self._set(self.entry, item["target"].split()[0])
        self.entry.configure(state="disabled")
        self._set(self.name, item["display_name"])
        self.block_type.set(next(k for k, v in BLOCK_TYPES.items() if v == (item.get("block_type") or "kill")))
        self.pickers.pack_forget()
        self._update_block_row()

    def selected_block_type(self) -> str:
        return BLOCK_TYPES[self.block_type.get()]

    def get(self) -> dict:
        """{kind, targets, name, source, block_type, app_path}. Raises ValueError with a user-facing message."""
        text = self.entry.get().strip()
        name = self.name.get().strip()
        if self._is_app():
            exe = (self.app["exe"] if self.app else text).lower()
            if exe in PROTECTED:
                raise ValueError(f"{exe} is part of Windows (or Lockdown) and can't be blocked.")
            return {"kind": "app", "targets": [exe], "name": name or (self.app or {}).get("name") or exe[:-4].title(),
                    "source": "app-browser" if self.app else "manual", "block_type": self.selected_block_type(),
                    "app_path": (self.app or {}).get("path")}
        host = normalize_host(text)
        entry = popular_hosts(host)   # known site: block all of its hostnames
        return {"kind": "site", "targets": entry[1] if entry else [host], "name": name or guess_name(host),
                "source": "popular" if entry else "manual", "block_type": None, "app_path": None}
