"""Small shared widgets."""
import customtkinter as ctk

from gui import theme

CONFIRM_RED = theme.DANGER
CONFIRM_MS = 3000

_open: dict[str, ctk.CTkToplevel] = {}   # one window per kind (see `once`)


def once(key: str, make):
    """Open a window only if one of that kind isn't already open - otherwise bring the open one to the front.
    Holding down (or double-clicking) a button that opens a window used to stack a pile of them, because a
    window only takes the click grab once it is actually on screen."""
    win = _open.get(key)
    try:
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            win.focus_force()
            return win
    except Exception:
        pass
    win = make()
    _open[key] = win
    return win


def modal(win, app):
    """Make `win` the window to deal with first, as soon as it can take the grab. Waiting a fixed 50 ms before
    even trying (what this replaces) left a gap where more clicks got through to the app behind it."""
    try:
        if app.winfo_viewable():
            win.transient(app)
    except Exception:
        pass

    def grab():
        if not win.winfo_exists():
            return
        try:
            win.grab_set()
        except Exception:
            win.after(30, grab)   # not on screen yet
    grab()


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


class ConfirmDialog(ctk.CTkToplevel):
    """A small modal yes/no. `lines` are shown as → bullets; on_no runs on Cancel / closing the window."""

    def __init__(self, app, title: str, message: str, on_yes, yes_text: str = "Continue", danger: bool = False,
                 lines: list[str] | None = None, on_no=lambda: None, alt_text: str | None = None,
                 on_alt=lambda: None):
        super().__init__(app)
        self.on_yes, self.on_no, self._answered = on_yes, on_no, False
        self.on_alt = on_alt
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=theme.BG)
        self.protocol("WM_DELETE_WINDOW", self._no)
        ctk.CTkFrame(self, height=3, corner_radius=0, fg_color=theme.DANGER if danger else theme.ACCENT).pack(
            fill="x", side="top")   # a thin accent edge, like the round-2 cards
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=24, pady=20)
        ctk.CTkLabel(box, text=title, font=theme.card_title()).pack(anchor="w")
        ctk.CTkLabel(box, text=message, text_color=theme.MUTED, justify="left", wraplength=460, anchor="w").pack(
            anchor="w", pady=(6, 0))
        if lines:
            items = ctk.CTkFrame(box, fg_color="transparent")
            items.pack(anchor="w", fill="x", pady=(8, 0))
            for line in lines:
                row = ctk.CTkFrame(items, fg_color="transparent")
                row.pack(anchor="w", fill="x")
                ctk.CTkLabel(row, text="→", text_color=theme.ACCENT, font=theme.semi(13), width=16, anchor="w").pack(
                    side="left", anchor="n")
                ctk.CTkLabel(row, text=line, text_color=theme.TEXT, justify="left", wraplength=440, anchor="w").pack(
                    side="left")
        buttons = ctk.CTkFrame(box, fg_color="transparent")
        buttons.pack(fill="x", pady=(16, 0))
        extra = {"fg_color": theme.DANGER, "hover_color": theme.DANGER} if danger else {}
        ctk.CTkButton(buttons, text=yes_text, width=120, command=self._yes, **extra).pack(side="right")
        if alt_text:   # a second way out that isn't "no" (e.g. "do it, but not until tomorrow")
            ctk.CTkButton(buttons, text=alt_text, width=150, **theme.OUTLINE, command=self._alt).pack(
                side="right", padx=8)
        ctk.CTkButton(buttons, text="Cancel", width=90, **theme.OUTLINE, command=self._no).pack(side="right", padx=8)
        modal(self, app)

    def _yes(self):
        self._answered = True
        self.destroy()
        self.on_yes()

    def _alt(self):
        self._answered = True
        self.destroy()
        self.on_alt()

    def _no(self):
        if self._answered:
            return
        self._answered = True
        self.destroy()
        self.on_no()


def clear_entry(entry: ctk.CTkEntry):
    """Empty an entry and keep its placeholder visible. CTkEntry.delete() alone hides the placeholder until
    the entry is clicked (it treats every new entry as focused until its first focus-out)."""
    entry.delete(0, "end")
    focus = entry.focus_get()
    if not (focus and str(focus).startswith(str(entry))):
        entry.configure(placeholder_text=entry.cget("placeholder_text"))   # re-shows it when empty


# ---------------------------------------------------------------- the bottom-right corner

class Corner:
    """Every little window that lives in the bottom-right corner - a notification, a reminder - in one stack,
    so they never cover each other. The newest sits in the corner and the ones before it are pushed up; when
    one goes, the rest slide back down. If they ever fill the screen the topmost stays put rather than
    disappearing off it."""

    GAP, MARGIN_X, MARGIN_Y, TOP = 10, 16, 64, 8
    _live: list[tuple] = []

    @classmethod
    def add(cls, window, width: int, height: int):
        cls._live = [e for e in cls._live if e[0].winfo_exists()]
        cls._live.append((window, width, height))
        window.bind("<Destroy>", lambda e, w=window: cls.remove(w) if e.widget is w else None, add="+")
        cls.arrange()

    @classmethod
    def remove(cls, window):
        cls._live = [e for e in cls._live if e[0] is not window and e[0].winfo_exists()]
        cls.arrange()

    @classmethod
    def arrange(cls):
        if not cls._live:
            return
        screen = cls._live[-1][0]
        bottom = screen.winfo_screenheight() - cls.MARGIN_Y
        right = screen.winfo_screenwidth() - cls.MARGIN_X
        y = bottom
        for window, width, height in reversed(cls._live):   # newest last in the list = lowest on the screen
            y = max(cls.TOP, y - height)
            try:
                window.corner_place(right - width, y)
            except Exception:
                pass
            y -= cls.GAP
