"""Small shared widgets."""
import customtkinter as ctk

CONFIRM_RED = "#b62324"
CONFIRM_MS = 3000


class ConfirmButton(ctk.CTkButton):
    """A button that needs two clicks: the first turns it into "Confirm" (red) for 3 s, the second runs on_confirm."""

    def __init__(self, master, on_confirm, text: str = "Remove", confirm_text: str = "Confirm", **kw):
        kw.setdefault("fg_color", "transparent")
        kw.setdefault("border_width", 1)
        super().__init__(master, text=text, command=self._click, **kw)
        self._on_confirm = on_confirm
        self._normal = {"text": text, "fg_color": kw["fg_color"], "hover_color": self.cget("hover_color")}
        self._confirm_text = confirm_text
        self._job = None

    def _click(self):
        if self._job is None:   # first click: arm
            self.configure(text=self._confirm_text, fg_color=CONFIRM_RED, hover_color=CONFIRM_RED)
            self._job = self.after(CONFIRM_MS, self._disarm)
            return
        self._disarm()
        self._on_confirm()      # may destroy this button (lists get rebuilt)

    def _disarm(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        if self.winfo_exists():
            self.configure(**self._normal)


def clear_entry(entry: ctk.CTkEntry):
    """Empty an entry and keep its placeholder visible. CTkEntry.delete() alone hides the placeholder until
    the entry is clicked (it treats every new entry as focused until its first focus-out)."""
    entry.delete(0, "end")
    focus = entry.focus_get()
    if not (focus and str(focus).startswith(str(entry))):
        entry.configure(placeholder_text=entry.cget("placeholder_text"))   # re-shows it when empty
