"""Charts drawn with Pillow at 3x size and scaled down (smooth, anti-aliased edges, rounded bars), shown on a Tk
canvas; text is drawn by the canvas so it stays crisp. Hovering a bar / cell / segment shows a small tooltip.
Sizes are in design pixels and follow the Windows display scaling."""
import tkinter as tk
import tkinter.font as tkfont
from datetime import date

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from gui import theme

SS = 3                                  # supersampling factor
AXIS_FONT = (theme.BODY, 8)
TIP_FONT = (theme.BODY, 9)
REDRAW_DELAY_MS = 40


class Chart(tk.Canvas):
    def __init__(self, master, height: int):
        self.s = ctk.ScalingTracker.get_widget_scaling(master)   # design px -> screen px
        super().__init__(master, height=int(height * self.s), highlightthickness=0, bd=0,
                         bg=theme.pick(theme.SURFACE))
        self.fh = tkfont.Font(font=AXIS_FONT).metrics("linespace")   # axis text height in screen px
        self.hits: list[tuple[float, float, float, float, str]] = []
        self._job = None
        self._photo = None
        self.bind("<Configure>", lambda e: self._schedule())
        self.tooltip = Tooltip(self)
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.tooltip.hide())

    def px(self, n: float) -> float:
        return n * self.s

    def _schedule(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(REDRAW_DELAY_MS, self.redraw)

    def redraw(self):
        self._job = None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 20:
            return
        self.bg = theme.pick(theme.SURFACE)
        self.configure(bg=self.bg)
        self.img = Image.new("RGB", (w * SS, h * SS), self.bg)
        self.pen = ImageDraw.Draw(self.img)
        self.hits, self.texts = [], []
        self.draw(w, h)
        self._photo = ImageTk.PhotoImage(self.img.resize((w, h), Image.LANCZOS))
        self.delete("all")
        self.create_image(0, 0, image=self._photo, anchor="nw")
        for x, y, text, anchor, color, font in self.texts:
            self.create_text(x, y, text=text, anchor=anchor, fill=theme.pick(color), font=font)

    def draw(self, w: int, h: int):
        raise NotImplementedError

    # ---------- drawing helpers (screen px) ----------

    def rect(self, x0, y0, x1, y1, color, radius: float = 0):
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            return
        box = [x0 * SS, y0 * SS, x1 * SS, y1 * SS]
        r = min(radius * SS, (box[2] - box[0]) / 2, (box[3] - box[1]) / 2)
        self.pen.rounded_rectangle(box, radius=r, fill=theme.pick(color))

    def text(self, x, y, text, anchor="n", color=theme.MUTED, font=AXIS_FONT):
        self.texts.append((x, y, text, anchor, color, font))

    def hit(self, x0, y0, x1, y1, text: str):
        self.hits.append((x0, y0, x1, y1, text))

    # ---------- tooltip ----------

    def _hover(self, event):
        tip = next((t for x0, y0, x1, y1, t in self.hits if x0 <= event.x <= x1 and y0 <= event.y <= y1), None)
        if tip:
            self.tooltip.show(tip, event.x_root, event.y_root)
        else:
            self.tooltip.hide()


class Tooltip:
    """A small borderless window next to the mouse - outside the chart, so it's never cut off."""

    def __init__(self, widget):
        self.widget, self.win, self.text = widget, None, ""

    def show(self, text: str, x_root: int, y_root: int):
        if self.win is None:
            self.win = tk.Toplevel(self.widget)
            self.win.overrideredirect(True)
            self.win.attributes("-topmost", True)
            self.label = tk.Label(self.win, justify="left", font=TIP_FONT, padx=8, pady=5, bd=0,
                                  highlightthickness=1)
            self.label.pack()
        self.text = text
        self.label.configure(text=text, bg=theme.pick(theme.SURFACE2), fg=theme.pick(theme.TEXT),
                             highlightbackground=theme.pick(theme.BORDER))
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        x = min(x_root + 14, self.widget.winfo_screenwidth() - w - 4)
        y = y_root - h - 10 if y_root - h - 10 > 0 else y_root + 18
        self.win.geometry(f"+{x}+{y}")
        self.win.deiconify()

    def hide(self):
        if self.win is not None:
            self.win.withdraw()


class TimelineBar(Chart):
    """One day as a horizontal bar coloured by what you did (rounded ends); hour labels underneath."""

    def __init__(self, master):
        super().__init__(master, height=46)
        self.segments: list[tuple[int, int, str]] = []
        self.start_hour = 6
        self.colors: dict = {}
        self.names: dict = {}

    def set(self, segments, start_hour: int, colors: dict, names: dict):
        """segments (start minute, minutes, kind); colors / names per kind ("idle" included)."""
        self.segments, self.start_hour, self.colors, self.names = segments, start_hour, colors, names
        self._schedule()

    def draw(self, w, h):
        bar = self.px(24)
        span = (24 - self.start_hour) * 60
        x_of = lambda minute: (minute - self.start_hour * 60) / span * w
        layer = Image.new("RGB", (w * SS, int(bar * SS)), theme.pick(theme.TRACK))
        pen = ImageDraw.Draw(layer)
        for start, length, kind in self.segments:
            end = start + length
            if end <= self.start_hour * 60:
                continue
            x0, x1 = max(0.0, x_of(start)), x_of(end)
            pen.rectangle([x0 * SS, 0, x1 * SS, bar * SS], fill=theme.pick(self.colors.get(kind, theme.NEUTRAL)))
            self.hit(x0, 0, max(x1, x0 + 2), bar,
                     f"{start // 60:02d}:{start % 60:02d}-{end // 60 % 24:02d}:{end % 60:02d}  "
                     f"{self.names.get(kind, kind)}")
        mask = Image.new("L", layer.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, layer.size[0] - 1, layer.size[1] - 1], radius=3 * self.s * SS,
                                               fill=255)
        self.img.paste(layer, (0, 0), mask)
        step = 3 if 24 - self.start_hour > 12 else 2
        for hour in range(self.start_hour, 25, step):
            anchor = "nw" if hour == self.start_hour else "ne" if hour == 24 else "n"
            self.text(x_of(hour * 60), bar + self.px(5), f"{hour:02d}:00", anchor)


class DayBars(Chart):
    """Active time per day (rounded bars): today in the accent colour, days over the goal darker, a dashed goal
    line, and a small mark over days with an emergency unlock. days: (label, seconds, today, tooltip, unlocks)."""

    def __init__(self, master, height: int = 170):
        super().__init__(master, height=height)
        self.days: list[tuple] = []
        self.goal: float | None = None

    def set(self, days, goal: float | None):
        self.days, self.goal = days, goal
        self._schedule()

    def draw(self, w, h):
        if not self.days:
            return
        top, bottom = self.px(12), h - self.fh - self.px(6)
        peak = max([d[1] for d in self.days] + [self.goal or 0, 3600])
        n = len(self.days)
        slot = w / n
        bar = min(self.px(34), slot * 0.6)
        for i, (label, sec, today, tip, unlocks) in enumerate(self.days):
            x = slot * i + (slot - bar) / 2
            y = bottom - (bottom - top) * sec / peak
            over = self.goal and sec > self.goal
            color = theme.ACCENT if today else theme.BAR_OVER if over else theme.BAR
            if sec > 0:
                self.rect(x, y, x + bar, bottom, color, self.px(3))
            if unlocks:
                r = self.px(3.5)
                cx, cy = x + bar / 2, max(r + 1, y - self.px(7))
                self.pen.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS],
                                 fill=theme.pick(theme.INFO))
                tip += f"\nEmergency unlock used {unlocks}x"
            if n <= 10 or i % 5 == 0 or today:
                self.text(x + bar / 2, bottom + self.px(4), label)
            self.hit(slot * i, 0, slot * (i + 1), h, tip)
        if self.goal:
            y = bottom - (bottom - top) * self.goal / peak
            dash, gap, x = self.px(4), self.px(3), 0.0
            while x < w:
                self.pen.line([x * SS, y * SS, min(w, x + dash) * SS, y * SS], fill=theme.pick(theme.NEUTRAL),
                              width=max(1, int(self.s * SS)))
                x += dash + gap


class Heatmap(Chart):
    """Days x hours grid of activity (rounded cells, 5 levels), hour labels, a less/more legend at the bottom right.
    rows: (label, date, [active minutes per hour])."""

    LEVELS = (1, 15, 30, 45)   # minutes: below 1 = level 0, then 1..4

    def __init__(self, master):
        super().__init__(master, height=230)
        self.rows: list[tuple[str, date, list[float]]] = []

    def set(self, rows):
        self.rows = rows
        self._schedule()

    @classmethod
    def level(cls, minutes: float) -> int:
        return sum(minutes >= t for t in cls.LEVELS)

    def draw(self, w, h):
        if not self.rows:
            return
        palette = theme.HEAT[1] if ctk.get_appearance_mode() == "Dark" else theme.HEAT[0]
        left, top = self.px(34), self.fh + self.px(4)
        legend_h = self.fh + self.px(10)
        cw, ch = (w - left) / 24, (h - top - legend_h) / len(self.rows)
        gap = self.px(3)
        for hour in range(0, 24, 2):
            self.text(left + hour * cw + cw / 2, 0, f"{hour:02d}")
        for r, (label, day, minutes) in enumerate(self.rows):
            y = top + r * ch
            self.text(0, y + ch / 2, label, "w")
            for hour, m in enumerate(minutes):
                x = left + hour * cw
                self.rect(x, y, x + cw - gap, y + ch - gap, palette[self.level(m)], self.px(2))
                self.hit(x, y, x + cw, y + ch, f"{label} {hour:02d}:00-{(hour + 1) % 24:02d}:00  "
                                               f"{int(m)} min active")
        # legend, right-aligned inside the chart: less [][][][][] more
        box, step = self.px(10), self.px(14)
        more_w = tkfont.Font(font=AXIS_FONT).measure("more")
        y = h - legend_h / 2
        x_end = w - more_w - self.px(6)
        x0 = x_end - 5 * step
        self.text(w, y, "more", "e")
        for i, c in enumerate(palette):
            self.pen.rounded_rectangle([(x0 + i * step) * SS, (y - box / 2) * SS, (x0 + i * step + box) * SS,
                                        (y + box / 2) * SS], radius=self.px(2) * SS, fill=c)
        self.text(x0 - self.px(6), y, "less", "e")


class Donut(Chart):
    """Smooth ring split into parts, with a total in the middle; hover a part for its name and time."""

    def __init__(self, master, size: int = 110):
        super().__init__(master, height=size)
        self.configure(width=int(size * self.s))
        self.parts: list[tuple[float, object, str]] = []
        self.center = ""

    def set(self, parts, center: str):
        """parts: (value, colour, tooltip)."""
        self.parts, self.center = parts, center
        self._schedule()

    def draw(self, w, h):
        size = min(w, h) - 2
        ring = self.px(18)
        box = [1 * SS, 1 * SS, (1 + size) * SS, (1 + size) * SS]
        total = sum(v for v, _, _ in self.parts)
        if not total:
            self.pen.ellipse(box, fill=theme.pick(theme.TRACK))
        start = -90.0
        c = 1 + size / 2
        for value, color, tip in self.parts:
            if not value:
                continue
            extent = 360 * value / total
            self.pen.pieslice(box, start, start + extent, fill=theme.pick(color))
            start += extent
        inner = [(1 + ring) * SS, (1 + ring) * SS, (1 + size - ring) * SS, (1 + size - ring) * SS]
        self.pen.ellipse(inner, fill=self.bg)
        self.text(c, c - self.px(6), self.center, "center", theme.TEXT, (theme.DISPLAY, 16, "bold"))
        self.text(c, c + self.px(11), "total", "center")
        tips = [tip for v, _, tip in self.parts if v]
        if tips:
            self.hit(0, 0, w, h, "\n".join(tips))


class HourBars(Chart):
    """Count per hour (rounded bars); the busiest hour in the accent colour, the next two darker. Always shows at
    least 07-22 so a single busy hour doesn't turn into one huge block."""

    def __init__(self, master, height: int = 380):
        super().__init__(master, height=height)
        self.counts: dict[int, int] = {}

    def set(self, counts):
        self.counts = counts
        self._schedule()

    def draw(self, w, h):
        used = [hr for hr in range(24) if self.counts.get(hr)]
        if not used:
            self.text(w / 2, h / 2, "No switches yet", "center", font=(theme.BODY, 10))
            return
        hours = list(range(min(used + [7]), max(used + [22]) + 1))
        peak = max(self.counts.values())
        ranked = sorted(used, key=lambda hr: -self.counts[hr])
        top, bottom = self.px(4), h - self.fh - self.px(6)
        slot = w / len(hours)
        bar = min(self.px(30), slot * 0.72)
        for i, hr in enumerate(hours):
            n = self.counts.get(hr, 0)
            x = slot * i + (slot - bar) / 2
            y = bottom - (bottom - top) * n / peak
            color = theme.ACCENT if hr == ranked[0] else theme.BAR_OVER if hr in ranked[1:3] else theme.BAR
            if n:
                self.rect(x, y, x + bar, bottom, color, self.px(3))
            if len(hours) <= 18 or hr % 2 == 0:
                self.text(x + bar / 2, bottom + self.px(4), f"{hr:02d}")
            self.hit(slot * i, 0, slot * (i + 1), h, f"{hr:02d}:00-{(hr + 1) % 24:02d}:00  "
                                                     f"{n} switch{'es' * (n != 1)}")


class MinuteBars(Chart):
    """Connections per minute over the last hour (oldest left); labels every 10 minutes."""

    def __init__(self, master, height: int = 220):
        super().__init__(master, height=height)
        self.values: list[tuple[str, int]] = []   # (HH:MM, count) per minute

    def set(self, values):
        self.values = values
        self._schedule()

    def draw(self, w, h):
        if not any(n for _, n in self.values):
            self.text(w / 2, h / 2, "Nothing in the last hour", "center", font=(theme.BODY, 10))
            return
        peak = max(n for _, n in self.values)
        top, bottom = self.px(4), h - self.fh - self.px(6)
        slot = w / len(self.values)
        bar = max(1.0, slot * 0.7)
        for i, (label, n) in enumerate(self.values):
            x = slot * i + (slot - bar) / 2
            if n:
                self.rect(x, bottom - (bottom - top) * n / peak, x + bar, bottom,
                          theme.ACCENT if i == len(self.values) - 1 else theme.BAR, self.px(2))
            if label.endswith("0"):
                self.text(x + bar / 2, bottom + self.px(4), label)
            self.hit(slot * i, 0, slot * (i + 1), h, f"{label}  {n} connection{'s' * (n != 1)}")
