"""Popup for picking an app to block: Start Menu apps, Steam games + apps with an open window, with icons and search.
Rows are made once and reused (rebuilding hundreds of buttons on every key made searching slow); the search waits
until you pause typing and shows the first MAX_SHOWN matches. Design 3l: search + "Other .exe", a count line with an
All / Running / Games filter, the list on its own bordered surface (icon · name · exe · RUNNING), Cancel at the foot."""
import threading
from tkinter import filedialog

import customtkinter as ctk

import search
from gui import icons, theme
from gui.components import Rows, Segmented, hairline

MUTED = theme.MUTED
MAX_SHOWN = 60
SEARCH_DELAY_MS = 150
FILTERS = {"All": lambda a: True, "Running": lambda a: a["running"], "Games": lambda a: bool(a.get("steam"))}
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
            _tag_steam_games(apps)
            _cache = apps
        return _cache


def _tag_steam_games(apps: list[dict]):
    """Installed Steam games go into the Games category, unless you have already put one somewhere else.
    Its own connection: this runs on the background thread that builds the app list."""
    from db import Database
    try:
        db = Database()
    except Exception:
        return
    try:
        known = db.categories()
        for a in apps:
            if a.get("steam") and ("app", a["exe"]) not in known:
                db.set_category("app", a["exe"], "games")
    except Exception:
        pass
    finally:
        db.close()


def matches(apps: list[dict], text: str) -> list[dict]:
    """Apps matching `text` (small typos are fine), best first; then running ones, then by name. Steam games also
    match "steam" - listed after Steam itself."""
    return search.rank(apps, text, lambda a: f"{a['name']} {a['exe']}" + (" steam" if a.get("steam") else ""),
                       tie=lambda a: (bool(a.get("steam")), not a["running"], a["name"].lower()))


class AppBrowser(ctk.CTkToplevel):
    """Every installed app and Steam game, searchable. on_pick(app) picks one (the Add tab uses that); with
    on_pick None it is just a browser - right-click an app to set its category, block it or add it to a group.
    `app_window` is the LockdownApp, needed for the right-click menu."""

    def __init__(self, master, on_pick=None, app_window=None):
        super().__init__(master)
        self.on_pick = on_pick
        self.app_window = app_window or master.winfo_toplevel()
        self.title("Browse apps" if on_pick else "Apps")
        self.geometry("560x600")
        self.configure(fg_color=theme.BG)
        self.transient(master.winfo_toplevel())
        self.after(50, self.grab_set)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=13, pady=(13, 10))
        self.search = ctk.CTkEntry(top, placeholder_text="Search apps and Steam games...")
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind("<KeyRelease>", lambda e: self._search_soon())
        ctk.CTkButton(top, text="Other .exe...", width=110, **theme.OUTLINE, command=self._browse_file).pack(
            side="left", padx=(9, 0))
        line = ctk.CTkFrame(self, fg_color="transparent")
        line.pack(fill="x", padx=13, pady=(0, 10))
        self.status = ctk.CTkLabel(line, text="Loading apps...", text_color=MUTED, font=theme.body(12), height=18)
        self.status.pack(side="left")
        self.filter = Segmented(line, values=list(FILTERS), command=lambda v: self._render(), height=26)
        self.filter.pack(side="right")
        self.filter.set("All")
        surface = ctk.CTkFrame(self, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER, corner_radius=3)
        surface.pack(fill="both", expand=True, padx=13)
        self.body = ctk.CTkScrollableFrame(surface, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=1, pady=1)
        self.rows = Rows(self.body, self._make_row, "No apps found.", {"fill": "x"})
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=13, pady=(10, 13))
        ctk.CTkButton(foot, text="Close" if not self.on_pick else "Cancel", width=90, **theme.OUTLINE,
                      command=self._cancel).pack(side="right")
        ctk.CTkLabel(foot, text=("Click an app to pick it · right-click for more" if self.on_pick else
                                 "Right-click an app to set its category, block it or add it to a group"),
                     text_color=MUTED, font=theme.body(11)).pack(side="left")
        self.apps: list[dict] | None = None
        self._pending = None
        preload()
        self._wait_for_list()

    def _make_row(self, parent):
        """icon · name · exe (muted) · RUNNING at the right; a hairline under each row; hover tint."""
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        line = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0, cursor="hand2")
        line.pack(fill="x", padx=4, pady=(0, 0))
        row.line = line
        row.icon = ctk.CTkLabel(line, text="", width=18, height=30)
        row.icon.pack(side="left", padx=(8, 10))
        row.name = ctk.CTkLabel(line, text="", font=theme.semi(13), anchor="w")
        row.name.pack(side="left")
        row.exe = ctk.CTkLabel(line, text="", text_color=MUTED, font=theme.body(12), anchor="w")
        row.exe.pack(side="left", padx=(8, 0))
        row.running = ctk.CTkLabel(line, text="RUNNING", text_color=theme.SUCCESS, font=theme.semi(10), width=60,
                                   anchor="e")
        row.running.pack(side="right", padx=(0, 10))
        hairline(row).pack(fill="x")
        for w in (line, row.icon, row.name, row.exe, row.running):
            w.bind("<Enter>", lambda e, r=row: r.line.configure(fg_color=theme.SURFACE2))
            w.bind("<Leave>", lambda e, r=row: r.line.configure(fg_color="transparent"))
        return row

    def _menu(self, a: dict):
        from gui import app_actions
        app_actions.open_menu(self, self.app_window, "app", a["exe"], a["name"], a.get("path"), self._render)

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
        keep = FILTERS[self.filter.value or "All"]
        found = [a for a in matches(self.apps, self.search.get().strip()) if keep(a)]
        shown = found[:MAX_SHOWN]
        for row, a in zip(self.rows.take(len(shown)), shown):
            row.icon.configure(image=icons.get_app(a["exe"], a["path"], 18))
            row.name.configure(text=a["name"])
            row.exe.configure(text=a["exe"] + (" · Steam" if a.get("steam") else ""))
            row.running.configure(text="RUNNING" if a["running"] else "")
            for w in (row.line, row.icon, row.name, row.exe, row.running):
                w.bind("<Button-1>", lambda e, a=a: self._pick(a))
                w.bind("<Button-3>", lambda e, a=a: self._menu(a))
        more = len(found) - len(shown)
        count = f"{len(found)} app{'s' * (len(found) != 1)}"
        self.status.configure(text=count + (f" · first {len(shown)} shown - type to narrow down" if more else ""))
        self.body._parent_canvas.yview_moveto(0)   # back to the top (a shorter list keeps no stale scroll position)

    def _browse_file(self):
        from monitor import win
        path = filedialog.askopenfilename(parent=self, title="Choose an app", filetypes=[("Programs", "*.exe")])
        if path:
            path = path.replace("/", "\\")
            self._pick({"name": path.rsplit("\\", 1)[-1][:-4], "exe": win.exe_name(path), "path": path})

    def _cancel(self):
        self.grab_release()
        self.destroy()

    def _pick(self, app: dict):
        if self.on_pick is None:   # browsing, not picking: the right-click menu is what acts on a row
            return
        self.grab_release()
        self.destroy()
        self.on_pick(app)
