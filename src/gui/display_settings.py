"""Display settings (⚙ on the Dashboard and Screen Time): which Dashboard cards are shown, and which tab / range
Screen Time opens with. Changes apply at once."""
import json

import customtkinter as ctk

from gui import theme
from gui.components import Segmented

HIDDEN_KEY = "dash.hidden"            # JSON list of hidden Dashboard cards
ST_RANGE_KEY, ST_TAB_KEY = "screentime.range", "screentime.tab"
DASH_CARDS = {"stats": "Numbers at the top", "limits": "Limits today", "today": "Today", "week": "Last 7 days",
              "coming": "Coming up", "visits": "Blocked visits today", "glance": "At a glance"}


def hidden(db) -> set[str]:
    try:
        return set(json.loads(db.get_setting(HIDDEN_KEY, "[]")))
    except ValueError:
        return set()


def gear_button(parent, app) -> ctk.CTkButton:
    """The gear that opens "which cards to show". It used to be a 16px muted glyph that was easy to miss."""
    return ctk.CTkButton(parent, text="", image=theme.icon("settings", theme.TEXT, 22), width=38, height=34,
                         fg_color="transparent", hover_color=theme.SURFACE2, command=lambda: DisplayWindow(app))


class DisplayWindow(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app, self.db = app, app.db
        self.title("Display")
        self.resizable(False, False)
        self.configure(fg_color=theme.BG)
        self.transient(app)
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=20, pady=16)
        ctk.CTkLabel(box, text="Dashboard shows", font=theme.card_title()).pack(anchor="w")
        off = hidden(self.db)
        self.boxes = {}
        for key, label in DASH_CARDS.items():
            b = ctk.CTkCheckBox(box, text=label, command=self._save)
            b.pack(anchor="w", pady=2)
            b.select() if key not in off else b.deselect()
            self.boxes[key] = b
        from gui.screen_time import TABS
        import stats
        ctk.CTkLabel(box, text="Screen Time opens with", font=theme.card_title()).pack(anchor="w", pady=(14, 4))
        self.tab = Segmented(box, values=[t for t in TABS if t != "Calendar"], command=lambda v: self._save())
        self.tab.pack(anchor="w", pady=2)
        self.tab.set(self.db.get_setting(ST_TAB_KEY, "Overview"))
        self.range = Segmented(box, values=list(stats.RANGES), command=lambda v: self._save())
        self.range.pack(anchor="w", pady=(6, 2))
        self.range.set(self.db.get_setting(ST_RANGE_KEY, "Today"))
        ctk.CTkButton(box, text="Done", width=90, command=self.destroy).pack(anchor="e", pady=(14, 0))

    def _save(self):
        self.db.set_setting(HIDDEN_KEY, json.dumps([k for k, b in self.boxes.items() if not b.get()]))
        self.db.set_setting(ST_TAB_KEY, self.tab.get())
        self.db.set_setting(ST_RANGE_KEY, self.range.get())
        if "Dashboard" in self.app.pages:
            self.app.pages["Dashboard"].apply_layout()
