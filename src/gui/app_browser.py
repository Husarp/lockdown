"""Popup for picking an app to block: Start Menu apps, Steam games + apps with an open window, with icons and search.
Rows are made once and reused (rebuilding hundreds of buttons on every key made searching slow); the search waits
until you pause typing and shows the first MAX_SHOWN matches."""
import threading
from tkinter import filedialog

import customtkinter as ctk

import search
from gui import icons, theme
from gui.components import Rows
from monitor import win

MUTED = theme.MUTED
MAX_SHOWN = 60
SEARCH_DELAY_MS = 150
_cache: list[dict] | None = None   # the app list takes a few seconds; keep it for the session
_loading = threading.Lock()


def preload():
    """Start loading the app list in the background (so the popup opens fast later)."""
    threading.Thread(target=_load, daemon=True).start()


def _load() -> list[dict]:
    global _cache
    with _loading:
        if _cache is None:
            from monitor.applist import list_apps
            try:
                apps = list_apps()
            except Exception:
                apps = []
            icons.cache_app_icons(apps)   # (first search of a broad word would otherwise extract dozens at once)
            _cache = apps
        return _cache


def matches(apps: list[dict], text: str) -> list[dict]:
    """Apps matching `text` (small typos are fine), best first; then running ones, then by name. Steam games also
    match "steam" - listed after Steam itself."""
    return search.rank(apps, text, lambda a: f"{a['name']} {a['exe']}" + (" steam" if a.get("steam") else ""),
                       tie=lambda a: (bool(a.get("steam")), not a["running"], a["name"].lower()))


class AppBrowser(ctk.CTkToplevel):
    """on_pick(app) with app = {name, exe, path, steam}."""

    def __init__(self, master, on_pick):
        super().__init__(master)
        self.on_pick = on_pick
        self.title("Browse apps")
        self.geometry("560x560")
        self.transient(master.winfo_toplevel())
        self.after(50, self.grab_set)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 6))
        self.search = ctk.CTkEntry(top, placeholder_text="Search apps and Steam games...")
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind("<KeyRelease>", lambda e: self._search_soon())
        ctk.CTkButton(top, text="Other .exe...", width=110, **theme.OUTLINE, command=self._browse_file).pack(
            side="left", padx=(8, 0))
        self.status = ctk.CTkLabel(self, text="Loading apps...", text_color=MUTED, height=18)
        self.status.pack(anchor="w", padx=14)
        self.body = ctk.CTkScrollableFrame(self)
        self.body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.rows = Rows(self.body, self._make_row, "No apps found.", {"fill": "x", "pady": 1})
        self.apps: list[dict] | None = None
        self._pending = None
        preload()
        self._wait_for_list()

    def _make_row(self, parent):
        row = ctk.CTkButton(parent, text="", anchor="w", height=32, fg_color="transparent",
                            hover_color=theme.SURFACE2, text_color=theme.TEXT)
        row.running = ctk.CTkLabel(row, text="● running", text_color=theme.SUCCESS, font=theme.body(11),
                                   fg_color="transparent", height=16)
        row.running.bind("<Button-1>", lambda e, r=row: r.invoke())
        return row

    def _wait_for_list(self):
        """Poll from the Tk thread (Tk must not be called from the loader thread)."""
        if not self.winfo_exists():
            return
        if _cache is None:
            self.after(200, self._wait_for_list)
            return
        self.apps = _cache
        self._render()

    def _search_soon(self):
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(SEARCH_DELAY_MS, self._render)

    def _render(self):
        self._pending = None
        if self.apps is None:
            return
        found = matches(self.apps, self.search.get().strip())
        shown = found[:MAX_SHOWN]
        for row, a in zip(self.rows.take(len(shown)), shown):
            label = f"  {a['name']}   ·   {a['exe']}" + ("   · Steam" if a.get("steam") else "")
            row.configure(text=label, image=icons.get_app(a["exe"], a["path"], 20), command=lambda a=a: self._pick(a))
            if a["running"]:   # small green tag at the right end
                row.running.place(relx=1.0, rely=0.5, x=-12, anchor="e")
            else:
                row.running.place_forget()
        more = len(found) - len(shown)
        count = f"{len(found)} app{'s' * (len(found) != 1)}"
        self.status.configure(text=count + (f" - showing the first {len(shown)}, type to narrow down"
                                                            if more else ""))
        self.body._parent_canvas.yview_moveto(0)   # back to the top (a shorter list keeps no stale scroll position)

    def _browse_file(self):
        path = filedialog.askopenfilename(parent=self, title="Choose an app", filetypes=[("Programs", "*.exe")])
        if path:
            path = path.replace("/", "\\")
            self._pick({"name": path.rsplit("\\", 1)[-1][:-4], "exe": win.exe_name(path), "path": path})

    def _pick(self, app: dict):
        self.grab_release()
        self.destroy()
        self.on_pick(app)
