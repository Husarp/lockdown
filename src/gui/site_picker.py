"""Site input with suggestions (popular + previously blocked sites) and the "Popular sites" popup."""
import customtkinter as ctk

import search

from gui import icons, theme
from importer.popular import POPULAR_SITES

MAX_SUGGESTIONS = 8
MUTED = theme.MUTED


def popular_entries() -> list[tuple[str, str]]:
    """(display name, main hostname) for every popular site."""
    return [(name, hosts[0]) for sites in POPULAR_SITES.values() for name, hosts in sites.items()]


class SuggestionList(ctk.CTkToplevel):
    """Borderless dropdown shown under the site entry."""

    def __init__(self, entry: ctk.CTkEntry, matches: list[tuple[str, str, bool]], on_pick, on_clear_history):
        super().__init__(entry)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        frame = ctk.CTkFrame(self, border_width=1)
        frame.pack(fill="both", expand=True)
        for name, host, _mine in matches:
            ctk.CTkButton(frame, text=f"{name}   {host}", image=icons.get(host, 18), anchor="w", height=30,
                          fg_color="transparent", hover_color=theme.SURFACE2, text_color=theme.TEXT,
                          command=lambda n=name, h=host: on_pick(n, h)).pack(fill="x", padx=4, pady=1)
        if on_clear_history:
            ctk.CTkButton(frame, text="Clear my suggestions", height=24, fg_color="transparent", text_color=MUTED,
                          hover_color=theme.SURFACE2, command=on_clear_history).pack(anchor="e", padx=6, pady=(2, 4))
        x, y = entry.winfo_rootx(), entry.winfo_rooty() + entry.winfo_height() + 2
        self.geometry(f"+{x}+{y}")
        self.update_idletasks()
        self.geometry(f"{max(entry.winfo_width(), 320)}x{self.winfo_reqheight()}+{x}+{y}")


class SiteEntry(ctk.CTkEntry):
    """Entry that suggests sites while typing. on_pick(name, host) is called when a suggestion is chosen."""

    def __init__(self, master, db, on_pick, **kw):
        super().__init__(master, **kw)
        self.db = db
        self.on_pick = on_pick
        self.dropdown: SuggestionList | None = None
        self.bind("<KeyRelease>", self._on_key, add="+")
        self.bind("<FocusOut>", lambda e: self.after(300, self.hide), add="+")
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
        self.hide()
        if matches:
            has_history = any(m[2] for m in matches)
            self.dropdown = SuggestionList(self, matches, self._pick, self._clear_history if has_history else None)

    def hide(self):
        if self.dropdown and self.dropdown.winfo_exists():
            self.dropdown.destroy()
        self.dropdown = None

    def _pick(self, name: str, host: str):
        self.hide()
        self.on_pick(name, host)

    def _clear_history(self):
        self.db.clear_history()
        self.show(self._matches(self.get()))


class PopularSitesPopup(ctk.CTkToplevel):
    """Popular sites by category; clicking one calls on_pick(name, host) and closes the popup."""

    def __init__(self, master, on_pick):
        super().__init__(master)
        self.title("Popular sites")
        self.geometry("620x520")
        self.transient(master.winfo_toplevel())
        self.after(50, self.grab_set)
        body = ctk.CTkScrollableFrame(self)
        body.pack(fill="both", expand=True, padx=10, pady=10)
        for category, sites in POPULAR_SITES.items():
            ctk.CTkLabel(body, text=category, font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(10, 4))
            grid = ctk.CTkFrame(body, fg_color="transparent")
            grid.pack(fill="x", padx=4)
            for i, (name, hosts) in enumerate(sites.items()):
                ctk.CTkButton(grid, text=name, image=icons.get(hosts[0], 20), anchor="w", width=180, height=34,
                              fg_color=theme.SURFACE2, hover_color=theme.BORDER,
                              text_color=theme.TEXT,
                              command=lambda n=name, h=hosts[0]: self._pick(on_pick, n, h)
                              ).grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="w")

    def _pick(self, on_pick, name, host):
        self.grab_release()
        self.destroy()
        on_pick(name, host)
