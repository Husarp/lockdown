"""Protection tab: the "Safe search" and "Blocked words" cards. The words are lists (the ready-made adult lists, your
words, exceptions) shown as one line each; "Open" shows the words to tick off / remove / add. Anything that weakens
the check (switching off, a list or word off, an exception) goes through Anti-Bypass."""
import customtkinter as ctk

import keywords
from gui import theme
from gui.components import Card, Segmented
from gui.widgets import clear_entry

MUTED = theme.MUTED


class WordListWindow(ctk.CTkToplevel):
    """One list's words. Ready-made list: a tick per word (untick = don't block it). Your words / exceptions: remove
    with × and add new ones. Nothing is saved until Save; on_save(words kept, words turned off)."""

    def __init__(self, app, title: str, words: list[str], off: set[str], on_save, can_add: bool, hint: str = ""):
        super().__init__(app)
        self.title(title)
        self.geometry("460x560")
        self.configure(fg_color=theme.BG)
        self.on_save, self.can_add = on_save, can_add
        self.words, self.off = list(words), set(off)
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=18, pady=16)
        ctk.CTkLabel(box, text=title, font=theme.card_title()).pack(anchor="w")
        self.count = ctk.CTkLabel(box, text="", text_color=MUTED)
        self.count.pack(anchor="w", pady=(0, 6))
        if can_add:
            line = ctk.CTkFrame(box, fg_color="transparent")
            line.pack(fill="x", pady=(0, 6))
            self.entry = ctk.CTkEntry(line, placeholder_text=hint)
            self.entry.pack(side="left", fill="x", expand=True)
            self.entry.bind("<Return>", lambda e: self._add())
            ctk.CTkButton(line, text="Add", width=70, **theme.OUTLINE, command=self._add).pack(side="left", padx=(8, 0))
        self.search = ctk.CTkEntry(box, placeholder_text="Search")
        self.search.pack(fill="x", pady=(0, 6))
        self.search.bind("<KeyRelease>", lambda e: self._fill())
        self.list = ctk.CTkScrollableFrame(box, fg_color=theme.SURFACE, corner_radius=6)
        self.list.pack(fill="both", expand=True)
        buttons = ctk.CTkFrame(box, fg_color="transparent")
        buttons.pack(fill="x", pady=(10, 0))
        ctk.CTkButton(buttons, text="Save", width=90, command=self._save).pack(side="right")
        ctk.CTkButton(buttons, text="Cancel", width=90, **theme.OUTLINE, command=self.destroy).pack(side="right", padx=8)
        if not can_add:
            ctk.CTkButton(buttons, text="All on", width=80, **theme.OUTLINE,
                          command=lambda: self._set_all(True)).pack(side="left")
        self.transient(app)
        self._fill()
        self.after(50, self._modal)

    def _modal(self):
        try:
            self.grab_set()
        except Exception:
            self.after(50, self._modal)

    def _fill(self):
        for w in self.list.winfo_children():
            w.destroy()
        text = keywords.normalize(self.search.get().strip())
        for word in [w for w in self.words if text in keywords.normalize(w)]:
            if self.can_add:
                row = ctk.CTkFrame(self.list, fg_color="transparent")
                row.pack(fill="x")
                ctk.CTkLabel(row, text=word, anchor="w", height=24).pack(side="left", padx=6)
                ctk.CTkButton(row, text="×", width=24, height=22, fg_color="transparent", hover_color=theme.BORDER,
                              text_color=MUTED, command=lambda w=word: self._remove(w)).pack(side="right", padx=4)
            else:
                box = ctk.CTkCheckBox(self.list, text=word, height=24, command=lambda w=word: self._flip(w))
                box.pack(anchor="w", padx=6, pady=1)
                box.select() if word not in self.off else box.deselect()
        self._show_count()

    def _show_count(self):
        if self.can_add:
            n = len(self.words)
            self.count.configure(text=f"{n} entr{'ies' if n != 1 else 'y'}")
        else:
            on = len([w for w in self.words if w not in self.off])
            self.count.configure(text=f"{on} of {len(self.words)} words blocked")

    def _flip(self, word: str):
        self.off ^= {word}
        self._show_count()

    def _set_all(self, on: bool):
        self.off = set() if on else set(self.words)
        self._fill()

    def _add(self):
        value = " ".join(self.entry.get().split()).lower()
        clear_entry(self.entry)
        if value and value not in self.words:
            self.words.insert(0, value)
            self._fill()

    def _remove(self, word: str):
        self.words.remove(word)
        self._fill()

    def _save(self):
        self.destroy()
        self.on_save(self.words, self.off)


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
        ctk.CTkLabel(words.body, text="The address and title of the tab in front are checked twice a second. Whole "
                                      "words only (\"analysis\" is fine); a word ending in * also matches longer ones "
                                      "(porn* -> pornhub).", text_color=MUTED, wraplength=820,
                     justify="left").pack(anchor="w", pady=(2, 8))
        line = ctk.CTkFrame(words.body, fg_color="transparent")
        line.pack(anchor="w", pady=(0, 10))
        ctk.CTkLabel(line, text="When a page has one").pack(side="left", padx=(0, 10))
        self.action = Segmented(line, values=list(keywords.ACTIONS.values()), command=lambda v: self._save_action())
        self.action.pack(side="left")
        ctk.CTkLabel(line, text="(a tab with nothing to go back to is closed)", text_color=MUTED).pack(
            side="left", padx=10)

        ctk.CTkLabel(words.body, text="Word lists", font=theme.semi(13)).pack(anchor="w")
        self.list_rows = {}
        for key, (name, _words) in keywords.READY.items():
            self.list_rows[key] = self._list_row(words.body, name, lambda k=key: self._open_ready(k), switch=key)
        self.list_rows["words"] = self._list_row(words.body, "Your words", self._open_words)
        self.list_rows["exceptions"] = self._list_row(words.body, "Exceptions", self._open_exceptions)
        self.refresh()

    def _list_row(self, parent, name: str, open_, switch: str | None = None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=2)
        if switch:
            row.switch = ctk.CTkSwitch(row, text=name, width=240, command=lambda: self._save_list(switch))
            row.switch.pack(side="left")
        else:
            ctk.CTkLabel(row, text=name, width=194, anchor="w").pack(side="left", padx=(46, 0))
        row.info = ctk.CTkLabel(row, text="", text_color=MUTED, width=170, anchor="w")
        row.info.pack(side="left", padx=10)
        ctk.CTkButton(row, text="Open", width=70, **theme.OUTLINE, command=open_).pack(side="left")
        return row

    def refresh(self):
        cfg = keywords.settings(self.db)
        self.safe_sw.select() if cfg["safesearch"] else self.safe_sw.deselect()
        self.words_sw.select() if cfg["enabled"] else self.words_sw.deselect()
        self.action.set(keywords.ACTIONS[cfg["action"]])
        off = set(cfg["off"])
        for key, (_name, words) in keywords.READY.items():
            row = self.list_rows[key]
            row.switch.select() if cfg["lists"].get(key) else row.switch.deselect()
            turned_off = len([w for w in words if w in off])
            row.info.configure(text=f"{len(words)} words" + (f" ({turned_off} off)" if turned_off else ""))
        n = len(cfg["words"])
        self.list_rows["words"].info.configure(text=f"{n} word{'s' * (n != 1)}" if n else "none yet")
        n = len(cfg["exceptions"])
        self.list_rows["exceptions"].info.configure(text=f"{n} entr{'ies' if n != 1 else 'y'}" if n else "none")

    # ---------- changes ----------

    def _store(self, cfg: dict):
        """Save; anything that weakens the check goes through Anti-Bypass first."""
        def save():
            keywords.save(self.db, cfg)
            self.refresh()
        changes = keywords.looser(keywords.settings(self.db), cfg)
        if changes:
            self.app.guard(changes, save, self.refresh)
        else:
            save()

    def _changed(self, **values):
        self._store({**keywords.settings(self.db), **values})

    def _save_safe(self):
        self._changed(safesearch=bool(self.safe_sw.get()))

    def _save_enabled(self):
        self._changed(enabled=bool(self.words_sw.get()))

    def _save_action(self):
        self._changed(action=next(k for k, v in keywords.ACTIONS.items() if v == self.action.get()))

    def _save_list(self, key: str):
        self._changed(lists={**keywords.settings(self.db)["lists"], key: bool(self.list_rows[key].switch.get())})

    def _open_ready(self, key: str):
        name, words = keywords.READY[key]
        cfg = keywords.settings(self.db)

        def save(_words, off):
            others = [w for w in cfg["off"] if w not in words]   # turned-off words of the other list stay
            self._changed(off=others + sorted(off))
        WordListWindow(self.app, name, words, set(cfg["off"]) & set(words), save, can_add=False)

    def _open_words(self):
        WordListWindow(self.app, "Your words", keywords.settings(self.db)["words"], set(),
                       lambda words, _off: self._changed(words=words), can_add=True, hint="a word or a few words")

    def _open_exceptions(self):
        WordListWindow(self.app, "Exceptions", keywords.settings(self.db)["exceptions"], set(),
                       lambda words, _off: self._changed(exceptions=words), can_add=True,
                       hint="site (e.g. wikipedia.org) or word to ignore")
