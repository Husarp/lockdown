"""Building blocks of the new design: cards, stat cards, and canvas charts (timeline, day bars, heatmap, donut,
hour bars). Charts redraw on resize and pick their colours for the current light/dark mode."""
import tkinter as tk

import customtkinter as ctk

from gui import theme

AXIS_FONT = (theme.BODY, 8)
LABEL_FONT = (theme.BODY, 9)


def eyebrow(parent, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(parent, text=text.upper(), font=theme.eyebrow(), text_color=theme.MUTED, height=14)


class Card(ctk.CTkFrame):
    """Surface card with an optional title and a muted note on the right. Content goes in `self.body`."""

    def __init__(self, master, title: str | None = None, note: str = "", **kw):
        super().__init__(master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER, corner_radius=6,
                         **kw)
        if title is not None:
            head = ctk.CTkFrame(self, fg_color="transparent")
            head.pack(fill="x", padx=15, pady=(12, 4))
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
        eyebrow(self.body, label).pack(anchor="w")
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


# ---------------------------------------------------------------- charts

class Chart(tk.Canvas):
    def __init__(self, master, height: int):
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=theme.pick(theme.SURFACE))
        self.bind("<Configure>", lambda e: self.redraw())

    def redraw(self):
        self.delete("all")
        self.configure(bg=theme.pick(theme.SURFACE))
        w, h = self.winfo_width(), self.winfo_height()
        if w > 20:
            self.draw(w, h)

    def draw(self, w: int, h: int):
        raise NotImplementedError


class TimelineBar(Chart):
    """One day as a horizontal bar coloured by what you did; hour labels underneath."""

    COLORS = {"productive": theme.SUCCESS, "neutral": theme.NEUTRAL, "distracting": theme.ACCENT, "idle": theme.TRACK}

    def __init__(self, master):
        super().__init__(master, height=44)
        self.segments: list[tuple[int, int, str]] = []
        self.start_hour = 6

    def set(self, segments, start_hour: int = 6):
        self.segments, self.start_hour = segments, start_hour
        self.redraw()

    def draw(self, w, h):
        span = (24 - self.start_hour) * 60
        x0 = lambda minute: (minute - self.start_hour * 60) / span * w
        self.create_rectangle(0, 0, w, 24, fill=theme.pick(theme.TRACK), width=0)
        for start, length, kind in self.segments:
            if start + length <= self.start_hour * 60:
                continue
            self.create_rectangle(max(0, x0(start)), 0, x0(start + length), 24, width=0,
                                  fill=theme.pick(self.COLORS.get(kind, theme.NEUTRAL)))
        step = 3 if 24 - self.start_hour > 12 else 2
        for hour in range(self.start_hour, 25, step):
            x = x0(hour * 60)
            anchor = "nw" if hour == self.start_hour else "ne" if hour == 24 else "n"
            self.create_text(x, 30, text=f"{hour:02d}:00", anchor=anchor, fill=theme.pick(theme.MUTED), font=AXIS_FONT)


class DayBars(Chart):
    """Active time per day; today in the accent colour, days over the goal darker; dashed goal line."""

    def __init__(self, master, height: int = 170):
        super().__init__(master, height=height)
        self.days: list[tuple[str, float, bool]] = []
        self.goal: float | None = None

    def set(self, days, goal: float | None):
        self.days, self.goal = days, goal
        self.redraw()

    def draw(self, w, h):
        if not self.days:
            return
        top, bottom = 8, h - 20
        peak = max([s for _, s, _ in self.days] + [self.goal or 0, 3600])
        n = len(self.days)
        slot = w / n
        bar = min(34, slot * 0.6)
        for i, (label, sec, today) in enumerate(self.days):
            x = slot * i + (slot - bar) / 2
            y = bottom - (bottom - top) * sec / peak
            over = self.goal and sec > self.goal
            color = theme.ACCENT if today else theme.BAR_OVER if over else theme.BAR
            if sec > 0:
                self.create_rectangle(x, y, x + bar, bottom, fill=theme.pick(color), width=0)
            if n <= 10 or i % 5 == 0 or today:
                self.create_text(x + bar / 2, h - 8, text=label, fill=theme.pick(theme.MUTED), font=AXIS_FONT)
        if self.goal:
            y = bottom - (bottom - top) * self.goal / peak
            self.create_line(0, y, w, y, fill=theme.pick(theme.NEUTRAL), dash=(4, 3))


class Heatmap(Chart):
    """Days x hours grid of activity levels 0-4, with hour labels and a less/more legend."""

    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def __init__(self, master):
        super().__init__(master, height=220)
        self.rows: list[tuple[str, list[int]]] = []

    def set(self, rows):
        self.rows = rows
        self.redraw()

    def draw(self, w, h):
        if not self.rows:
            return
        palette = theme.HEAT[1] if ctk.get_appearance_mode() == "Dark" else theme.HEAT[0]
        left, top = 34, 16
        cw, ch = (w - left) / 24, (h - top - 26) / len(self.rows)
        gap = 3
        for hour in range(0, 24, 2):
            self.create_text(left + hour * cw + cw / 2, 6, text=f"{hour:02d}", fill=theme.pick(theme.MUTED),
                             font=AXIS_FONT)
        for r, (label, levels) in enumerate(self.rows):
            y = top + r * ch
            self.create_text(0, y + ch / 2, text=label, anchor="w", fill=theme.pick(theme.MUTED), font=AXIS_FONT)
            for hour, level in enumerate(levels):
                x = left + hour * cw
                self.create_rectangle(x, y, x + cw - gap, y + ch - gap, fill=palette[level], width=0)
        y = top + len(self.rows) * ch + 8
        x = left + 24 * cw - 5 * 14 - 30
        self.create_text(x - 6, y + 5, text="less", anchor="e", fill=theme.pick(theme.MUTED), font=AXIS_FONT)
        for i, c in enumerate(palette):
            self.create_rectangle(x + i * 14, y, x + i * 14 + 10, y + 10, fill=c, width=0)
        self.create_text(x + 5 * 14 + 2, y + 5, text="more", anchor="w", fill=theme.pick(theme.MUTED), font=AXIS_FONT)


class Donut(Chart):
    """Ring split into parts, with a total in the middle."""

    def __init__(self, master, size: int = 110):
        super().__init__(master, height=size)
        self.configure(width=size)
        self.parts: list[tuple[float, tuple]] = []
        self.center = ""

    def set(self, parts, center: str):
        self.parts, self.center = parts, center
        self.redraw()

    def draw(self, w, h):
        size = min(w, h) - 4
        box = (2 + 9, 2 + 9, 2 + size - 9, 2 + size - 9)
        total = sum(v for v, _ in self.parts)
        if not total:
            self.create_oval(*box, outline=theme.pick(theme.TRACK), width=18)
        start = 90.0
        for value, color in self.parts:
            extent = -360 * value / total if total else 0
            if abs(extent) >= 359.9:
                self.create_oval(*box, outline=theme.pick(color), width=18)
            elif extent:
                self.create_arc(*box, start=start, extent=extent, style="arc", outline=theme.pick(color), width=18)
            start += extent
        c = 2 + size / 2
        self.create_text(c, c - 5, text=self.center, fill=theme.pick(theme.TEXT), font=(theme.DISPLAY, 16, "bold"))
        self.create_text(c, c + 12, text="total", fill=theme.pick(theme.MUTED), font=AXIS_FONT)


class HourBars(Chart):
    """Count per hour; the busiest hour in the accent colour, the next two darker."""

    def __init__(self, master, height: int = 380):
        super().__init__(master, height=height)
        self.counts: dict[int, int] = {}

    def set(self, counts):
        self.counts = counts
        self.redraw()

    def draw(self, w, h):
        hours = [hr for hr in range(24) if self.counts.get(hr)]
        if not hours:
            self.create_text(w / 2, h / 2, text="No switches yet", fill=theme.pick(theme.MUTED), font=LABEL_FONT)
            return
        hours = list(range(min(hours), max(hours) + 1))
        peak = max(self.counts.values())
        ranked = sorted(hours, key=lambda hr: -self.counts.get(hr, 0))
        top, bottom = 4, h - 18
        slot = w / len(hours)
        bar = slot * 0.78
        for i, hr in enumerate(hours):
            n = self.counts.get(hr, 0)
            x = slot * i + (slot - bar) / 2
            y = bottom - (bottom - top) * n / peak
            color = theme.ACCENT if hr == ranked[0] else theme.BAR_OVER if hr in ranked[1:3] else theme.BAR
            if n:
                self.create_rectangle(x, y, x + bar, bottom, fill=theme.pick(color), width=0)
            self.create_text(x + bar / 2, h - 7, text=f"{hr:02d}", fill=theme.pick(theme.MUTED), font=AXIS_FONT)


class ProgressLine(ctk.CTkFrame):
    """A thin progress bar (limits)."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent", height=6)
        self.bar = ctk.CTkProgressBar(self, height=6, corner_radius=3)
        self.bar.pack(fill="x")

    def set(self, fraction: float, color):
        self.bar.configure(progress_color=color)
        self.bar.set(max(0.0, min(1.0, fraction)))
