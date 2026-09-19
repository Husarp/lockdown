"""Small shared widgets."""
import customtkinter as ctk

from gui import theme

CONFIRM_RED = theme.DANGER
CONFIRM_MS = 3000


class ConfirmButton(ctk.CTkButton):
    """A button that needs two clicks: the first turns it into "Confirm" (red) for 3 s, the second runs on_confirm."""

    def __init__(self, master, on_confirm, text: str = "Remove", confirm_text: str = "Confirm", **kw):
        for key, value in theme.OUTLINE.items():
            kw.setdefault(key, value)
        super().__init__(master, text=text, command=self._click, **kw)
        self._on_confirm = on_confirm
        self._normal = {"text": text, "fg_color": kw["fg_color"], "hover_color": self.cget("hover_color"),
                        "text_color": self.cget("text_color")}
        self._confirm_text = confirm_text
        self._job = None

    def _click(self):
        if self._job is None:   # first click: arm
            self.configure(text=self._confirm_text, fg_color=CONFIRM_RED, hover_color=CONFIRM_RED,
                           text_color=theme.WHITE)
            self._bar = ctk.CTkFrame(self, height=2, fg_color=theme.WHITE, corner_radius=0)
            self._bar.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0)
            self._drain(0)
            self._job = self.after(CONFIRM_MS, self._disarm)
            return
        self._disarm()
        self._on_confirm()      # may destroy this button (lists get rebuilt)

    def _drain(self, step: int):
        """The 2px bar empties over the 3 s confirm window, then the button falls back."""
        bar = getattr(self, "_bar", None)
        if bar is None or not bar.winfo_exists() or self._job is None:
            return
        frac = max(0.0, 1 - step * 40 / CONFIRM_MS)
        bar.place_configure(relwidth=frac)
        if frac > 0:
            self.after(40, lambda: self._drain(step + 1))

    def _disarm(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        bar = getattr(self, "_bar", None)
        if bar is not None and bar.winfo_exists():
            bar.destroy()
        self._bar = None
        if self.winfo_exists():
            self.configure(**self._normal)


def clear_entry(entry: ctk.CTkEntry):
    """Empty an entry and keep its placeholder visible. CTkEntry.delete() alone hides the placeholder until
    the entry is clicked (it treats every new entry as focused until its first focus-out)."""
    entry.delete(0, "end")
    focus = entry.focus_get()
    if not (focus and str(focus).startswith(str(entry))):
        entry.configure(placeholder_text=entry.cget("placeholder_text"))   # re-shows it when empty
