"""Popup for picking an app to block: Start Menu apps + apps with an open window, with icons and search."""
import threading
from tkinter import filedialog

import customtkinter as ctk

from gui import icons, theme
from monitor import win

MUTED = theme.MUTED
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
                _cache = list_apps()
            except Exception:
                _cache = []
        return _cache


class AppBrowser(ctk.CTkToplevel):
    """on_pick(app) with app = {name, exe, path}."""

    def __init__(self, master, on_pick):
        super().__init__(master)
        self.on_pick = on_pick
        self.title("Browse apps")
        self.geometry("560x560")
        self.transient(master.winfo_toplevel())
        self.after(50, self.grab_set)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(12, 6))
        self.search = ctk.CTkEntry(top, placeholder_text="Search apps...")
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind("<KeyRelease>", lambda e: self._render())
        ctk.CTkButton(top, text="Other .exe...", width=110, fg_color="transparent", border_width=1,
                      command=self._browse_file).pack(side="left", padx=(8, 0))
        self.body = ctk.CTkScrollableFrame(self)
        self.body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.apps: list[dict] | None = None
        ctk.CTkLabel(self.body, text="Loading apps...", text_color=MUTED).pack(pady=20)
        preload()
        self._wait_for_list()

    def _wait_for_list(self):
        """Poll from the Tk thread (Tk must not be called from the loader thread)."""
        if not self.winfo_exists():
            return
        if _cache is None:
            self.after(200, self._wait_for_list)
            return
        self.apps = _cache
        self._render()

    def _render(self):
        if self.apps is None:
            return
        for w in self.body.winfo_children():
            w.destroy()
        text = self.search.get().strip().lower()
        shown = [a for a in self.apps if text in a["name"].lower() or text in a["exe"]]
        shown.sort(key=lambda a: (not a["running"], a["name"].lower()))   # running apps first
        if not shown:
            ctk.CTkLabel(self.body, text="No apps found.", text_color=MUTED).pack(pady=20)
        for a in shown:
            label = f"  {a['name']}   ·   {a['exe']}" + ("   ● running" if a["running"] else "")
            ctk.CTkButton(self.body, text=label, image=icons.get_app(a["exe"], a["path"], 20), anchor="w",
                          height=32, fg_color="transparent", hover_color=theme.SURFACE2,
                          text_color=theme.TEXT, command=lambda a=a: self._pick(a)).pack(fill="x", pady=1)

    def _browse_file(self):
        path = filedialog.askopenfilename(parent=self, title="Choose an app", filetypes=[("Programs", "*.exe")])
        if path:
            path = path.replace("/", "\\")
            self._pick({"name": path.rsplit("\\", 1)[-1][:-4], "exe": win.exe_name(path), "path": path})

    def _pick(self, app: dict):
        self.grab_release()
        self.destroy()
        self.on_pick(app)
