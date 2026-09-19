"""Building blocks of the new design: cards, stat cards, chips, tab bars, blocker cards (charts: charts.py)."""
import tkinter as tk

import customtkinter as ctk

from gui import theme


class PulseDot(tk.Canvas):
    """The sidebar service status dot: a solid dot with one expanding, fading ring every ~2.4 s while the service
    runs (green) - a small canvas redraw that stops when the window is hidden. Red and still when it's down."""
    SIZE = 18

    def __init__(self, master, **kw):
        self.bg = theme.pick(theme.SIDEBAR)
        super().__init__(master, width=self.SIZE, height=self.SIZE, bg=self.bg, highlightthickness=0, bd=0, **kw)
        self.color = theme.pick(theme.SUCCESS)
        self.on = True
        self.phase = 0.0
        self._tick()

    def set_state(self, running: bool):
        self.color = theme.pick(theme.SUCCESS if running else theme.DANGER)
        self.on = running
        self._draw()

    def _tick(self):
        if self.on and self.winfo_viewable():   # don't animate while hidden
            self.phase = (self.phase + 0.09) % 1.0
        self._draw()
        self.after(130, self._tick)

    def _draw(self):
        self.delete("all")
        c = self.SIZE / 2
        if self.on:
            r = 3 + self.phase * 5.5
            ring = theme._mix(self.color, self.bg, min(1.0, self.phase * 1.15))
            self.create_oval(c - r, c - r, c + r, c + r, outline=ring, width=1)
        self.create_oval(c - 3, c - 3, c + 3, c + 3, fill=self.color, outline="")


def eyebrow(parent, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text=text.upper(), font=theme.eyebrow(), text_color=theme.MUTED, height=14)


def accent_bar(parent, height: int = 16, width: int = 3, color=None):
    """A small vertical accent bar that sits before a title (round-2 look)."""
    bar = ctk.CTkFrame(parent, fg_color=color or theme.ACCENT, width=width, height=height, corner_radius=1)
    bar.pack_propagate(False)
    return bar


def page_head(parent, text: str) -> ctk.CTkFrame:
    """Page heading: a 4px accent bar + the Barlow-condensed title, in a row you pack where the label used to go."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    accent_bar(row, height=26, width=4).pack(side="left", padx=(0, 11))
    ctk.CTkLabel(row, text=text, font=theme.page_title()).pack(side="left")
    return row


_BADGE = {"site": ("MUTED", "BORDER"), "app": ("INFO", "INFO"), "group": ("WARNING", "WARNING")}


def type_badge(parent, kind: str) -> ctk.CTkFrame:
    """Small uppercase site / app / group badge (site = muted, app = blue, group = yellow)."""
    fg, bd = (getattr(theme, name) for name in _BADGE.get(kind, ("MUTED", "BORDER")))
    f = ctk.CTkFrame(parent, fg_color="transparent", border_width=1, border_color=bd, corner_radius=2)
    ctk.CTkLabel(f, text=kind.upper(), font=ctk.CTkFont(theme.BODY_SEMI, 9), text_color=fg, height=13).pack(
        padx=4, pady=0)
    return f


class LockedStrip(ctk.CTkFrame):
    """A slim bar on edit-heavy pages while Anti-Bypass is locked: "Locked - changes need the challenge" and an
    "Unlock to edit" button. It hides itself when Anti-Bypass is off or already unlocked. The page calls update()
    on show / refresh; the caller passes the widget to sit above (`anchor`)."""

    def __init__(self, master, app, anchor):
        super().__init__(master, corner_radius=4, border_width=1)
        self.app, self.anchor = app, anchor
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=9)
        self.icon = ctk.CTkLabel(row, text="", image=theme.icon("shield-check", theme.DANGER, 15), width=15)
        self.icon.pack(side="left", padx=(0, 9))
        self.label = ctk.CTkLabel(row, text="", font=theme.semi(12), anchor="w")
        self.label.pack(side="left")
        self.btn = ctk.CTkButton(row, text="Unlock to edit", width=120, command=self._unlock)
        self.btn.pack(side="right")
        c = theme.DANGER
        self.configure(fg_color=(theme._mix(c[0], theme.BG[0], 0.9), theme._mix(c[1], theme.BG[1], 0.88)),
                       border_color=c)

    def _unlock(self):
        import antibypass
        self.app.guard([f"Allow loosening changes for {antibypass.UNLOCK_MIN} minutes"], self.update, self.update)

    def update(self, *_):
        import antibypass
        from trusted_time import now_from_db
        status = antibypass.status(antibypass.settings(self.app.db), now_from_db(self.app.db))
        if status == "free":
            self.pack_forget()
            return
        closed = status == "closed"
        self.label.configure(text="Locked - only during the allowed hours" if closed else "Locked")
        self.btn.configure(state="disabled" if closed else "normal")
        # CTkScrollableFrame is packed through an internal wrapper - pack before that, not the object itself
        before = getattr(self.anchor, "_parent_frame", self.anchor)
        self.pack(fill="x", padx=30, pady=(0, 8), before=before)


class Card(ctk.CTkFrame):
    """Surface card with an optional title and a muted note on the right. Content goes in `self.body`.
    `accent_top`: a 3px accent bar along the top edge (squared top corners so it never looks clipped)."""

    def __init__(self, master, title: str | None = None, note: str = "", accent_top: bool = False, **kw):
        super().__init__(master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER, corner_radius=4,
                         **kw)
        if accent_top:
            top = ctk.CTkFrame(self, fg_color=theme.ACCENT, height=3, corner_radius=0)
            top.pack(fill="x", side="top")
        if title is not None:
            head = ctk.CTkFrame(self, fg_color="transparent")
            head.pack(fill="x", padx=15, pady=(12, 4))
            accent_bar(head).pack(side="left", padx=(0, 9))
            self.title = ctk.CTkLabel(head, text=title, font=theme.card_title(), height=20)
            self.title.pack(side="left")
            self.note = ctk.CTkLabel(head, text=note, font=theme.body(11), text_color=theme.MUTED, height=20)
            self.note.pack(side="right")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=15, pady=(0, 12))


class StatCard(Card):
    """EYEBROW / big number / coloured one-line note."""

    def __init__(self, master, label: str):
        super().__init__(master)
        self.body.pack_configure(pady=(10, 10))
        self.label = eyebrow(self.body, label)
        self.label.pack(anchor="w")
        self.value = ctk.CTkLabel(self.body, text="-", font=theme.numeral(30), height=34)
        self.value.pack(anchor="w")
        self.delta = ctk.CTkLabel(self.body, text="", font=theme.body(11), text_color=theme.MUTED,
                                  justify="left", anchor="w", wraplength=200)
        self.delta.pack(anchor="w", fill="x")

    def set(self, value: str, delta: str = "", color=theme.MUTED):
        self.value.configure(text=value)
        self.delta.configure(text=delta, text_color=color)


class Chip(ctk.CTkButton):
    """Small outlined label (a category); clickable when given a command."""

    def __init__(self, master, text: str, color, command=None, width: int = 80):
        super().__init__(master, text=text, width=width, height=22, corner_radius=2, border_width=1,
                         border_color=color, text_color=color, fg_color="transparent", hover_color=theme.SURFACE2,
                         font=theme.body(11), command=command)


class Segmented(ctk.CTkFrame):
    """Tab bar / option switch: rounded buttons on a rounded track; the chosen one in the accent colour.
    Same use as CTkSegmentedButton: values, command(value), set(), get()."""

    def __init__(self, master, values: list[str], command=None, height: int = 30, **_ignored):
        super().__init__(master, fg_color=theme.SURFACE2, corner_radius=3)
        self.command, self.value = command, None
        self.buttons = {}
        font = theme.body(13)
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        for i, v in enumerate(values):
            # text width + padding on both sides (measure() is in screen px, widths are in design px)
            b = ctk.CTkButton(self, text=v, width=int(font.measure(v) / scale) + 28, height=height - 6,
                              corner_radius=2, font=font, command=lambda v=v: self._clicked(v))
            b.pack(side="left", padx=(3 if i == 0 else 0, 3), pady=3)
            self.buttons[v] = b
        self._paint()

    def _clicked(self, value: str):
        self.set(value)
        if self.command:
            self.command(value)

    def set(self, value: str):
        self.value = value
        self._paint()

    def get(self) -> str:
        return self.value

    def _paint(self):
        for v, b in self.buttons.items():
            on = v == self.value
            b.configure(fg_color=theme.ACCENT if on else "transparent", text_color=theme.WHITE if on else theme.TEXT,
                        hover_color=theme.ACCENT_PRESS if on else theme.BORDER)


class TabBar(ctk.CTkFrame):
    """Top-of-page sub-tabs, Round-3 underline / ink-bar style: a row of labels over a thin baseline; the chosen
    one gets a 2px accent underline and brighter, semibold text. Sits on the page-title row, right-aligned.
    Same API as Segmented (values, command(value), set(), get())."""

    GAP = 22

    def __init__(self, master, values: list[str], command=None, **_ignored):
        super().__init__(master, fg_color="transparent")
        self.command, self.value = command, None
        self.tabs = {}
        row = ctk.CTkFrame(self, fg_color="transparent")   # grid: each column sizes to its label (no fixed width)
        row.pack(side="top", anchor="e")
        ctk.CTkFrame(self, height=2, fg_color=theme.BORDER, corner_radius=0).pack(fill="x", side="top")  # baseline
        for i, v in enumerate(values):
            pad = (0, self.GAP if i < len(values) - 1 else 0)
            lbl = ctk.CTkLabel(row, text=v, text_color=theme.MUTED, font=theme.body(13), height=22, cursor="hand2")
            lbl.grid(row=0, column=i, padx=pad, pady=(6, 2))
            underline = ctk.CTkFrame(row, height=3, width=1, fg_color="transparent", corner_radius=0)
            underline.grid(row=1, column=i, padx=pad, sticky="ew")
            for w in (lbl, underline):
                w.bind("<Button-1>", lambda _e, v=v: self._clicked(v))
            lbl.bind("<Enter>", lambda _e, v=v: self._hover(v, True))    # a subtle lighten so tabs feel clickable
            lbl.bind("<Leave>", lambda _e, v=v: self._hover(v, False))
            self.tabs[v] = (lbl, underline)
        self._paint()

    def _clicked(self, value: str):
        self.set(value)
        if self.command:
            self.command(value)

    def _hover(self, value: str, on: bool):
        lbl = self.tabs[value][0]
        if value != self.value:   # the selected tab already stands out
            lbl.configure(text_color=theme.TEXT if on else theme.MUTED)

    def set(self, value: str):
        self.value = value
        self._paint()

    def get(self) -> str:
        return self.value

    def _paint(self):
        for v, (lbl, underline) in self.tabs.items():
            on = v == self.value
            lbl.configure(text_color=theme.TEXT if on else theme.MUTED, font=theme.semi(13) if on else theme.body(13))
            underline.configure(fg_color=theme.ACCENT if on else "transparent")


class Rows:
    """List rows that are made once and reused on every refresh - creating and destroying Tk widgets is what
    makes pages slow. make(parent) returns a frame (with its widgets as attributes); take(n) shows n of them.
    `item_pack`: how each row is packed (default: full width, stacked)."""

    def __init__(self, parent, make, empty_text: str = "", item_pack: dict | None = None):
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.pack(fill="x")
        self.make, self.items = make, []
        self.item_pack = item_pack or {"fill": "x"}
        self.empty = ctk.CTkLabel(self.frame, text=empty_text, text_color=theme.MUTED) if empty_text else None
        self.packed = 0   # the first `packed` items are on screen (only the difference is (un)packed)

    def take(self, n: int) -> list:
        while len(self.items) < n:
            self.items.append(self.make(self.frame))
        for item in self.items[n:self.packed]:
            item.pack_forget()
        for item in self.items[self.packed:n]:
            item.pack(**self.item_pack)
        self.packed = n
        if self.empty:
            if n:
                self.empty.pack_forget()
            else:
                self.empty.pack(anchor="w")
        return self.items[:n]


def help_icon(parent, text: str) -> ctk.CTkLabel:
    """A small "?" that shows `text` while the mouse is over it (instead of a grey hint line under things).
    Returns it unpacked - pack / grid it next to what it explains."""
    import textwrap
    from gui.charts import Tooltip
    icon = ctk.CTkLabel(parent, text="?", width=18, height=18, corner_radius=9, fg_color=theme.SURFACE2,
                        text_color=theme.MUTED, font=theme.semi(11))
    tip = Tooltip(icon)
    wrapped = "\n".join(textwrap.fill(p, 60) for p in text.split("\n"))
    icon.bind("<Enter>", lambda e: tip.show(wrapped, e.x_root, e.y_root))
    icon.bind("<Leave>", lambda e: tip.hide())
    return icon


class Curtain:
    """Covers an area for a moment while its content is swapped (another page / tab), so the new content appears at
    once instead of being drawn piece by piece in front of you. cover(over) - `over` must be inside `master`."""
    HOLD_MS = 60

    def __init__(self, master):
        self.frame = ctk.CTkFrame(master, fg_color=theme.BG, corner_radius=0)
        self.job = None

    def cover(self, over=None):
        self.frame.place(in_=over or self.frame.master, x=0, y=0, relwidth=1, relheight=1)
        self.frame.lift()
        if self.job:
            self.frame.after_cancel(self.job)
        # open once the new content had time to lay out and draw (and Tk is idle again)
        self.job = self.frame.after(self.HOLD_MS, lambda: self.frame.after_idle(self._open))

    def _open(self):
        self.job = None
        self.frame.place_forget()


class Collapsible(ctk.CTkFrame):
    """A section you open by clicking its title. build(body) runs the first time it's opened (so a closed section
    costs nothing); on_open() runs every time it's opened."""

    def __init__(self, master, title: str, build, note: str = "", on_open=None, **kw):
        super().__init__(master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER, corner_radius=6,
                         **kw)
        self.title, self.build, self.on_open = title, build, on_open
        self.head = ctk.CTkButton(self, text="", anchor="w", height=40, fg_color="transparent", text_color=theme.TEXT,
                                  hover_color=theme.SURFACE2, font=theme.card_title(), command=self.toggle)
        self.head.pack(fill="x", padx=4, pady=4)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.opened = self.built = False
        self.set_note(note)

    def set_note(self, note: str):
        self.head.configure(text=f"{'▾' if self.opened else '▸'}  {self.title}" + (f"   ·   {note}" if note else ""))
        self.note = note

    def toggle(self):
        self.opened = not self.opened
        if self.opened:
            if not self.built:
                self.build(self.body)
                self.built = True
            self.body.pack(fill="x", padx=15, pady=(0, 12))
            if self.on_open:
                self.on_open()
        else:
            self.body.pack_forget()
        self.set_note(self.note)


class ProgressLine(ctk.CTkFrame):
    """A thin progress bar (limits)."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent", height=6)
        self.bar = ctk.CTkProgressBar(self, height=6, corner_radius=3)
        self.bar.pack(fill="x")

    def set(self, fraction: float, color):
        self.bar.configure(progress_color=color)
        self.bar.set(max(0.0, min(1.0, fraction)))


# ---------------------------------------------------------------- blocking page pieces

CHIP_STYLES = {   # kind -> (text colour, background)
    "group": (theme.ACCENT, ("#FBEAE3", "#2A1E19")),
    "warn": (theme.WARNING, ("#FBF1DE", "#241E15")),
    "danger": (theme.DANGER, ("#FBE5E3", "#2A1A19")),
    "ok": (theme.SUCCESS, ("#E3F3E9", "#16241C")),
    "neutral": (theme.MUTED, ("#EFF1F3", "#1F252D")),
}


def rule_chip(parent, text: str, kind: str = "neutral", wraplength: int = 200) -> ctk.CTkLabel:
    """A rule shown as a small tinted label."""
    fg, bg = CHIP_STYLES[kind]
    return ctk.CTkLabel(parent, text=f" {text} ", text_color=fg, fg_color=bg, corner_radius=3, font=theme.body(11),
                        wraplength=wraplength, justify="left", anchor="w", height=22)


class BlockerCard(ctk.CTkFrame):
    """A blocker as a collapsible card: tick box + name and a one-line summary; its settings open underneath.
    `summary()` gives the text shown on the right (called when anything changes). The editor is only built when
    it's first needed (building all of them up front made the Add tab slow to open)."""

    def __init__(self, master, name: str, make_editor, summary, off_text: str = "off", on_change=None):
        super().__init__(master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER, corner_radius=6)
        self.summary_fn, self.off_text, self.on_change = summary, off_text, on_change
        self.bar = ctk.CTkFrame(self, width=3, height=1, corner_radius=0, fg_color="transparent")
        self.bar.pack(side="left", fill="y", pady=1)
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(side="left", fill="both", expand=True)
        head = ctk.CTkFrame(inner, fg_color="transparent")
        head.pack(fill="x", padx=(10, 8), pady=6)
        self.check = ctk.CTkCheckBox(head, text=name, font=theme.semi(13), command=self._ticked)
        self.check.pack(side="left")
        self.chevron = ctk.CTkButton(head, text="▾", width=24, height=24, fg_color="transparent",
                                     text_color=theme.MUTED, hover_color=theme.SURFACE2, command=self.toggle)
        self.chevron.pack(side="right")
        self.summary = ctk.CTkLabel(head, text="", text_color=theme.MUTED, font=theme.body(11))
        self.summary.pack(side="right", padx=6)
        self.body = ctk.CTkFrame(inner, fg_color="transparent")
        self.make_editor, self._editor = make_editor, None
        self.open = False

    @property
    def editor(self):
        if self._editor is None:
            self._editor = self.make_editor(self.body)
            self._editor.pack(anchor="w")
        return self._editor

    def load(self, rule: dict | None):
        """Show a rule (None: off). An editor that was never opened stays unbuilt."""
        if rule is not None or self._editor is not None:
            self.editor.load(rule)
        self.set(rule is not None)

    def _ticked(self):
        self.open = bool(self.check.get())
        self.update_state()
        if self.on_change:
            self.on_change()

    def toggle(self):
        if self.check.get():
            self.open = not self.open
            self.update_state()

    def set(self, on: bool):
        self.check.select() if on else self.check.deselect()
        self.open = on
        self.update_state()

    def update_state(self):
        on = bool(self.check.get())
        self.bar.configure(fg_color=theme.ACCENT if on else "transparent")
        self.check.configure(text_color=theme.TEXT if on else theme.MUTED)
        if on and self.open:
            self.body.pack(fill="x", padx=(40, 12), pady=(0, 10))
        else:
            self.body.pack_forget()
        self.chevron.configure(text="▴" if on and self.open else "▾")
        self.refresh_summary()

    def refresh_summary(self):
        self.summary.configure(text=self.summary_fn() if self.check.get() else self.off_text)
