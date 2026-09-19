"""Protection tab: the "Safe search" and "Blocked words" cards. Switching either off, removing one of your words or
adding an exception loosens protection, so those go through Anti-Bypass."""
import customtkinter as ctk

import keywords
from gui import theme
from gui.components import Card, Rows, Segmented
from gui.widgets import clear_entry

MUTED = theme.MUTED


def _chip(parent):
    f = ctk.CTkFrame(parent, fg_color=theme.SURFACE2, border_width=1, border_color=theme.BORDER, corner_radius=4)
    f.name = ctk.CTkLabel(f, text="", height=26)
    f.name.pack(side="left", padx=(8, 2))
    f.remove = ctk.CTkButton(f, text="×", width=22, height=22, fg_color="transparent", hover_color=theme.BORDER,
                             text_color=MUTED)
    f.remove.pack(side="left", padx=(0, 4))
    return f


class WordsCards:
    def __init__(self, parent, app):
        self.app, self.db = app, app.db
        safe = Card(parent, "Safe search")
        safe.pack(fill="x", pady=(0, 12))
        self.safe_sw = ctk.CTkSwitch(safe.body, text="Force SafeSearch", font=theme.semi(13), command=self._save_safe)
        self.safe_sw.pack(anchor="w")
        ctk.CTkLabel(safe.body, text="Google, Bing and DuckDuckGo always search with SafeSearch on (adult pictures and "
                                     "videos filtered out), YouTube runs in Restricted Mode - in every browser, also "
                                     "in private windows.", text_color=MUTED, wraplength=820, justify="left").pack(
            anchor="w", pady=(2, 0))

        words = Card(parent, "Blocked words")
        words.pack(fill="x", pady=(0, 12))
        self.words_sw = ctk.CTkSwitch(words.body, text="Check the browser tab for blocked words", font=theme.semi(13),
                                      command=self._save_enabled)
        self.words_sw.pack(anchor="w")
        ctk.CTkLabel(words.body, text=f"The address and title of the tab in front are checked every second - "
                                      f"{keywords.built_in_count()} built-in English and Polish adult words plus "
                                      f"yours. Whole words only (\"analysis\" is fine); a word ending in * also "
                                      f"matches longer ones (porn* -> pornhub).", text_color=MUTED, wraplength=820,
                     justify="left").pack(anchor="w", pady=(2, 8))
        line = ctk.CTkFrame(words.body, fg_color="transparent")
        line.pack(anchor="w", pady=(0, 8))
        ctk.CTkLabel(line, text="When a page has one").pack(side="left", padx=(0, 10))
        self.action = Segmented(line, values=list(keywords.ACTIONS.values()), command=lambda v: self._save_action())
        self.action.pack(side="left")
        ctk.CTkLabel(line, text="(a tab with nothing to go back to is closed)", text_color=MUTED).pack(
            side="left", padx=10)

        self.error = ctk.CTkLabel(words.body, text="", text_color=theme.DANGER, height=16)
        self.words_rows = self._list(words.body, "Your words", "None yet.", "a word or a few words", self._add_word)
        self.exc_rows = self._list(words.body, "Exceptions", "None.", "site (e.g. wikipedia.org) or word to ignore",
                                   self._add_exception)
        self.error.pack(anchor="w")
        self.refresh()

    def _list(self, parent, title: str, empty: str, hint: str, add) -> Rows:
        ctk.CTkLabel(parent, text=title, font=theme.semi(13)).pack(anchor="w", pady=(4, 0))
        rows = Rows(parent, _chip, empty, {"side": "left", "padx": (0, 6), "pady": 4})
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.pack(anchor="w", pady=(2, 6))
        entry = ctk.CTkEntry(line, width=300, placeholder_text=hint)
        entry.pack(side="left")
        entry.bind("<Return>", lambda e: add(entry))
        ctk.CTkButton(line, text="Add", width=70, **theme.OUTLINE, command=lambda: add(entry)).pack(side="left", padx=8)
        return rows

    def refresh(self):
        cfg = keywords.settings(self.db)
        self.safe_sw.select() if cfg["safesearch"] else self.safe_sw.deselect()
        self.words_sw.select() if cfg["enabled"] else self.words_sw.deselect()
        self.action.set(keywords.ACTIONS[cfg["action"]])
        for rows, key in ((self.words_rows, "words"), (self.exc_rows, "exceptions")):
            values = cfg[key]
            for chip, value in zip(rows.take(len(values)), values):
                chip.name.configure(text=value)
                chip.remove.configure(command=lambda k=key, v=value: self._remove(k, v))

    # ---------- changes ----------

    def _store(self, cfg: dict, loosens: str | None = None):
        def save():
            keywords.save(self.db, cfg)
            self.refresh()
        if loosens:
            self.app.guard([loosens], save, self.refresh)
        else:
            save()

    def _save_safe(self):
        cfg = keywords.settings(self.db)
        cfg["safesearch"] = bool(self.safe_sw.get())
        self._store(cfg, None if cfg["safesearch"] else "Turn forced SafeSearch off")

    def _save_enabled(self):
        cfg = keywords.settings(self.db)
        cfg["enabled"] = bool(self.words_sw.get())
        self._store(cfg, None if cfg["enabled"] else "Turn the blocked-words check off")

    def _save_action(self):
        cfg = keywords.settings(self.db)
        cfg["action"] = next(k for k, v in keywords.ACTIONS.items() if v == self.action.get())
        self._store(cfg)

    def _add(self, entry, key: str, loosens: str | None):
        value = " ".join(entry.get().split()).lower()
        if not value:
            self.error.configure(text="Type something first.")
            return
        self.error.configure(text="")
        clear_entry(entry)
        cfg = keywords.settings(self.db)
        if value not in cfg[key]:
            cfg[key] = cfg[key] + [value]
            self._store(cfg, loosens and loosens.format(value))

    def _add_word(self, entry):
        self._add(entry, "words", None)   # a new word: stricter

    def _add_exception(self, entry):
        self._add(entry, "exceptions", 'Don\'t check "{}" for blocked words')

    def _remove(self, key: str, value: str):
        cfg = keywords.settings(self.db)
        cfg[key] = [v for v in cfg[key] if v != value]
        self._store(cfg, f'Remove "{value}" from your blocked words' if key == "words" else None)
