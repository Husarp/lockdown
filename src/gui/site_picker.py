"""Site-or-app input with suggestions while typing: your earlier sites, popular sites, installed apps and Steam
games in one list (apps come from the app browser's list, loaded in the background at start)."""
import os
import tkinter as tk

import customtkinter as ctk

import search
from gui import app_browser, icons, theme
from gui.components import Rows
from importer.popular import POPULAR_SITES
from monitor import win

MAX_SUGGESTIONS = 8
WIDTH = 380        # px (the text is shortened to fit, so the icon never gets pushed out)
MAX_CHARS = 42
WATCH_MS = 250
MUTED = theme.MUTED


def popular_entries() -> list[tuple[str, str]]:
    """(display name, main hostname) for every popular site."""
    return [(name, hosts[0]) for sites in POPULAR_SITES.values() for name, hosts in sites.items()]


def _search_text(match: dict) -> str:
    if match["kind"] == "app":
        app = match["app"]
        return f"{app['name']} {app['exe']}" + (" steam" if app.get("steam") else "")
    return f"{match['name']} {match['host']}"


class SuggestionList:
    """Borderless dropdown under the site entry. One window, made once and only updated while typing (a new window
    per key press flashed); it hides itself when the entry loses the focus or Lockdown isn't the app in front."""

    def __init__(self, entry: ctk.CTkEntry, on_pick, on_clear_history):
        self.entry, self.on_pick = entry, on_pick
        self.win = tk.Toplevel(entry)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.transient(entry.winfo_toplevel())   # (goes away with Lockdown's window, never over other apps)
        self.frame = ctk.CTkFrame(self.win, corner_radius=0, fg_color=theme.SURFACE)
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)
        self.rows = Rows(self.frame, self._make_row, "", {"fill": "x", "padx": 4, "pady": 1})
        self.clear = ctk.CTkButton(self.frame, text="Clear my suggestions", height=24, fg_color="transparent",
                                   text_color=MUTED, hover_color=theme.SURFACE2, command=on_clear_history)
        self.shown = False

    def _make_row(self, parent):
        return ctk.CTkButton(parent, text="", anchor="w", height=30, fg_color="transparent",
                             hover_color=theme.SURFACE2, text_color=theme.TEXT)

    def show(self, matches: list[dict]):
        if not matches:
            self.hide()
            return
        for row, m in zip(self.rows.take(len(matches)), matches):
            if m["kind"] == "app":
                app = m["app"]
                text = f"{app['name']}   {app['exe']} · {'Steam game' if app.get('steam') else 'app'}"
                image = icons.get_app(app["exe"], app["path"], 18)
            else:
                text, image = f"{m['name']}   {m['host']}", icons.get(m["host"], 18)
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS - 1] + "…"
            row.configure(text=text, image=image, command=lambda m=m: self.on_pick(m))
        self.clear.pack_forget()
        if any(m.get("mine") for m in matches):
            self.clear.pack(anchor="e", padx=6, pady=(2, 4))
        self.win.configure(bg=theme.pick(theme.BORDER))   # (the 1-px frame around it)
        self.win.update_idletasks()
        e = self.entry
        width = max(e.winfo_width(), int(WIDTH * ctk.ScalingTracker.get_widget_scaling(e)))
        self.win.geometry(f"{width}x{self.frame.winfo_reqheight() + 2}+{e.winfo_rootx()}+"
                          f"{e.winfo_rooty() + e.winfo_height() + 2}")
        if not self.shown:
            self.shown = True
            self.win.deiconify()
            self.win.lift()
            self.win.after(WATCH_MS, self._watch)

    def hide(self):
        if self.shown:
            self.shown = False
            self.win.withdraw()

    def _watch(self):
        """Hide once the entry lost the focus (clicking a suggestion keeps it), the entry isn't on screen any more, or
        another app is in front."""
        if not self.shown:
            return
        focus = self.win.focus_get()
        mine = focus is not None and (str(focus).startswith(str(self.entry)) or str(focus).startswith(str(self.win)))
        in_front = win.window_pid(win.foreground()[0]) == os.getpid()
        if not (mine and in_front and self.entry.winfo_viewable()):
            self.hide()
            return
        self.win.after(WATCH_MS, self._watch)


class SiteEntry(ctk.CTkEntry):
    """Entry that suggests sites and apps while typing. on_pick(name, host) is called when a site is chosen,
    on_pick_app(app) when an app is."""

    def __init__(self, master, db, on_pick, on_pick_app=None, **kw):
        super().__init__(master, **kw)
        self.db = db
        self.on_pick, self.on_pick_app = on_pick, on_pick_app
        self.dropdown: SuggestionList | None = None
        self.bind("<KeyRelease>", self._on_key, add="+")
        self.bind("<Escape>", lambda e: self.hide(), add="+")

    def _matches(self, text: str) -> list[dict]:
        text = text.strip().lower()
        if not text:
            return []
        seen, entries = set(), []
        mine = [(h["display_name"], h["hostname"], True) for h in self.db.history()]
        for name, host, is_mine in mine + [(n, h, False) for n, h in popular_entries()]:
            if host not in seen:
                seen.add(host)
                entries.append({"kind": "site", "name": name, "host": host, "mine": is_mine})
        if self.on_pick_app:
            entries += [{"kind": "app", "app": a} for a in app_browser._cache or []]
        # small typos are fine ("yotube" -> youtube.com); exact matches first
        return search.rank(entries, text, _search_text)[:MAX_SUGGESTIONS]

    def _on_key(self, event):
        if event.keysym in ("Return", "Escape", "Tab"):
            return
        self.show(self._matches(self.get()))

    def show(self, matches):
        if self.dropdown is None:
            self.dropdown = SuggestionList(self, self._pick, self._clear_history)
        self.dropdown.show(matches)

    def hide(self):
        if self.dropdown:
            self.dropdown.hide()

    def _pick(self, match: dict):
        self.hide()
        if match["kind"] == "app":
            self.on_pick_app(match["app"])
        else:
            self.on_pick(match["name"], match["host"])

    def _clear_history(self):
        self.db.clear_history()
        self.show(self._matches(self.get()))
