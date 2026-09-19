"""Site input with suggestions (popular + previously blocked sites) while typing."""
import os
import tkinter as tk

import customtkinter as ctk

import search
from gui import icons, theme
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

    def show(self, matches: list[tuple[str, str, bool]]):
        if not matches:
            self.hide()
            return
        for row, (name, host, _mine) in zip(self.rows.take(len(matches)), matches):
            text = f"{name}   {host}"
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS - 1] + "…"
            row.configure(text=text, image=icons.get(host, 18), command=lambda n=name, h=host: self.on_pick(n, h))
        self.clear.pack_forget()
        if any(m[2] for m in matches):
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
    """Entry that suggests sites while typing. on_pick(name, host) is called when a suggestion is chosen."""

    def __init__(self, master, db, on_pick, **kw):
        super().__init__(master, **kw)
        self.db = db
        self.on_pick = on_pick
        self.dropdown: SuggestionList | None = None
        self.bind("<KeyRelease>", self._on_key, add="+")
        self.bind("<Escape>", lambda e: self.hide(), add="+")

    def _matches(self, text: str) -> list[tuple[str, str, bool]]:
        text = text.strip().lower()
        if not text:
            return []
        seen, entries = set(), []
        mine = [(h["display_name"], h["hostname"], True) for h in self.db.history()]
        for name, host, is_mine in mine + [(n, h, False) for n, h in popular_entries()]:
            if host not in seen:
                seen.add(host)
                entries.append((name, host, is_mine))
        # small typos are fine ("yotube" -> youtube.com); exact matches first
        return search.rank(entries, text, lambda e: f"{e[0]} {e[1]}")[:MAX_SUGGESTIONS]

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

    def _pick(self, name: str, host: str):
        self.hide()
        self.on_pick(name, host)

    def _clear_history(self):
        self.db.clear_history()
        self.show(self._matches(self.get()))
