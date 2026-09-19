"""Charts drawn with Pillow at 3x size and scaled down (smooth, anti-aliased edges, rounded bars), shown on a Tk
canvas; text is drawn by the canvas so it stays crisp. Hovering a bar / cell / segment shows a small tooltip.
Sizes are in design pixels and follow the Windows display scaling."""
import tkinter as tk
import tkinter.font as tkfont
from datetime import date

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

import blockcal
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


class TrendLine(Chart):
    """Daily active time as a line over a filled area, with a dashed 7-day average, a dashed goal line and a hollow
    marker on days with an emergency unlock. days: list of (label, seconds, tip, avg_seconds|None, unlocks)."""

    def __init__(self, master, height: int = 180):
        super().__init__(master, height=height)
        self.days: list[tuple] = []
        self.goal: float | None = None

    def set(self, days, goal: float | None):
        self.days, self.goal = days, goal
        self._schedule()

    def draw(self, w, h):
        if len(self.days) < 2:
            return
        top, bottom = self.px(12), h - self.fh - self.px(6)
        left, right = self.px(3), w - self.px(3)
        peak = max([d[1] for d in self.days] + [self.goal or 0, 3600])
        n = len(self.days)
        xs = [left + (right - left) * i / (n - 1) for i in range(n)]

        def y_of(sec):
            return bottom - (bottom - top) * sec / peak

        pts = [(xs[i], y_of(self.days[i][1])) for i in range(n)]
        area = [(pts[0][0], bottom)] + pts + [(pts[-1][0], bottom)]
        self.pen.polygon([(x * SS, y * SS) for x, y in area],
                         fill=theme._mix(theme.pick(theme.ACCENT), self.bg, 0.86))
        if self.goal:   # dashed goal line
            y, dash, gap, x = y_of(self.goal), self.px(4), self.px(3), 0.0
            while x < w:
                self.pen.line([x * SS, y * SS, min(w, x + dash) * SS, y * SS], fill=theme.pick(theme.NEUTRAL),
                              width=max(1, int(self.s * SS)))
                x += dash + gap
        avg_pts = [(xs[i], y_of(self.days[i][3])) for i in range(n) if self.days[i][3] is not None]
        if len(avg_pts) >= 2:   # dashed 7-day average
            for (x0, y0), (x1, y1) in zip(avg_pts, avg_pts[1:]):
                self.pen.line([x0 * SS, y0 * SS, x1 * SS, y1 * SS], fill=theme.pick(theme.MUTED),
                              width=max(1, int(1.4 * self.s * SS)))
        self.pen.line([(x * SS, y * SS) for x, y in pts], fill=theme.pick(theme.ACCENT),
                      width=max(1, int(2 * self.s * SS)), joint="curve")
        for i, (label, sec, tip, _avg, unlocks) in enumerate(self.days):
            x, y = pts[i]
            if unlocks:
                r = self.px(4)
                self.pen.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=self.bg,
                                 outline=theme.pick(theme.INFO), width=max(1, int(self.s * SS)))
            if i == 0 or i == n - 1 or i % 7 == 0:
                self.text(x, bottom + self.px(4), label)
            self.hit(xs[i] - (right - left) / (2 * n), 0, xs[i] + (right - left) / (2 * n), h, tip)


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


class MonthCalendar(Chart):
    """A month as a calendar (Monday first): each day coloured by its active screen time (5 levels, like the
    heatmap), with its number; days over the daily goal get a red dot. Hover a day for its time."""

    LEVELS = (1, 2, 4, 6)   # hours: below 1 = level 0 ... 6 h and more = level 4
    WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    def __init__(self, master):
        super().__init__(master, height=330)
        self.first: date | None = None
        self.per_day: dict[date, float] = {}
        self.goal: float | None = None
        self.today: date | None = None
        self.tip_of = lambda d, sec: ""

    def set(self, first: date, per_day: dict[date, float], goal: float | None, today: date, tip_of):
        """first: the month's 1st day; per_day: active seconds; tip_of(day, seconds) -> tooltip text."""
        self.first, self.per_day, self.goal, self.today, self.tip_of = first, per_day, goal, today, tip_of
        self._schedule()

    def draw(self, w, h):
        if not self.first:
            return
        import calendar
        from stats import hm
        palette = theme.HEAT[1] if ctk.get_appearance_mode() == "Dark" else theme.HEAT[0]
        weeks = calendar.Calendar().monthdatescalendar(self.first.year, self.first.month)
        top = self.fh + self.px(8)
        cw, ch = w / 7, (h - top) / len(weeks)
        gap = self.px(4)
        for i, name in enumerate(self.WEEKDAYS):
            self.text(i * cw + cw / 2, 0, name)
        big = (theme.BODY, 10)
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                if day.month != self.first.month:
                    continue
                x, y = c * cw, top + r * ch
                sec = self.per_day.get(day, 0)
                future = day > self.today
                level = 0 if future else sum(sec / 3600 >= t for t in self.LEVELS)
                self.rect(x, y, x + cw - gap, y + ch - gap, theme.TRACK if future else palette[level], self.px(4))
                if self.goal and sec > self.goal and not future:   # over the goal: a red dot in the corner
                    r, cx, cy = self.px(4.5), x + cw - gap - self.px(10), y + self.px(10)
                    self.pen.ellipse([(cx - r - self.px(1.5)) * SS, (cy - r - self.px(1.5)) * SS,
                                      (cx + r + self.px(1.5)) * SS, (cy + r + self.px(1.5)) * SS],
                                     fill=theme.pick(theme.SURFACE))
                    self.pen.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS],
                                     fill=theme.pick(theme.DANGER))
                color = theme.WHITE if level >= 3 else theme.TEXT if not future else theme.MUTED
                self.text(x + self.px(8), y + self.px(6), str(day.day), "nw", color, big)
                if sec >= 60 and not future:
                    self.text(x + cw - gap - self.px(8), y + ch - gap - self.px(6), hm(sec), "se",
                              theme.WHITE if level >= 3 else theme.MUTED)
                if not future:
                    self.hit(x, y, x + cw - gap, y + ch - gap, self.tip_of(day, sec))


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
    """Connections per minute over the last hour (oldest left); labels every 10 minutes. A minute that had a
    blocked attempt is drawn in red. Values: (HH:MM, count) or (HH:MM, count, blocked)."""

    def __init__(self, master, height: int = 220):
        super().__init__(master, height=height)
        self.values: list[tuple] = []

    def set(self, values):
        self.values = values
        self._schedule()

    def draw(self, w, h):
        vals = [(v[0], v[1], v[2] if len(v) > 2 else False) for v in self.values]
        if not any(n for _, n, _ in vals):
            self.text(w / 2, h / 2, "Nothing in the last hour", "center", font=(theme.BODY, 10))
            return
        peak = max(n for _, n, _ in vals)
        top, bottom = self.px(4), h - self.fh - self.px(6)
        slot = w / len(vals)
        bar = max(1.0, slot * 0.7)
        for i, (label, n, blocked) in enumerate(vals):
            x = slot * i + (slot - bar) / 2
            if n:
                color = theme.DANGER if blocked else theme.ACCENT if i == len(vals) - 1 else theme.BAR
                self.rect(x, bottom - (bottom - top) * n / peak, x + bar, bottom, color, self.px(2))
            if label.endswith("0"):
                self.text(x + bar / 2, bottom + self.px(4), label)
            self.hit(slot * i, 0, slot * (i + 1), h, f"{label}  {n} connection{'s' * (n != 1)}"
                     + ("  ·  blocked attempt" if blocked else ""))


class WeekCalendar(Chart):
    """A week (Mon..Sun rows) x 24 h. Each blocked item is a bar at the times it's blocked, lane-packed so
    overlapping items stack; hollow markers show emergency unlocks and a line shows 'now'. Hover a bar for the
    item + times; click it to open that item.
    set(week, color_of, today_weekday, now_minute, unlocks); on_click(item)."""
    GUTTER = 40
    TOPAX = 16
    LANE_H = 15
    LANE_GAP = 3
    ROW_GAP = 7
    LABEL_FONT = (theme.BODY_SEMI, 8)

    def __init__(self, master, on_click=None, height: int = 360):
        super().__init__(master, height=height)
        self.rows: list[list[dict]] = [[] for _ in range(7)]
        self.color_of = lambda item: theme.pick(theme.ACCENT)
        self.today, self.now_min = 0, 0
        self.unlocks: list[list[int]] = [[] for _ in range(7)]
        self.maxlanes = 1
        self.click_hits: list[tuple] = []
        self.on_click = on_click
        self.bind("<Button-1>", self._click)

    def set(self, week, color_of, today_weekday: int, now_minute: int, unlocks):
        self.color_of, self.today, self.now_min, self.unlocks = color_of, today_weekday, now_minute, unlocks
        self.rows, self.maxlanes = [], 1
        for day in week:
            bars = [{"start": a, "end": b, "item": e["item"]} for e in day for a, b in e["intervals"]]
            self.maxlanes = max(self.maxlanes, blockcal.lanes(bars))
            self.rows.append(bars)
        rowh = self.maxlanes * self.LANE_H + (self.maxlanes - 1) * self.LANE_GAP
        total = self.TOPAX + 7 * (rowh + self.ROW_GAP) + self.fh / self.s + 6
        self.configure(height=int(total * self.s))
        self._schedule()

    def _click(self, event):
        item = next((it for x0, y0, x1, y1, it in self.click_hits if x0 <= event.x <= x1 and y0 <= event.y <= y1),
                    None)
        if item and self.on_click:
            self.on_click(item)

    def draw(self, w, h):
        left, right = self.px(self.GUTTER), w - self.px(6)
        track = right - left
        rowh = self.maxlanes * self.px(self.LANE_H) + (self.maxlanes - 1) * self.px(self.LANE_GAP)
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        def X(minute):
            return left + track * minute / 1440

        for hr in range(0, 25, 3):
            self.text(X(hr * 60), self.px(1), f"{hr:02d}", anchor="n")
        self.click_hits = []
        line_w = max(1, int(self.s * SS))
        y = self.px(self.TOPAX)
        for wd in range(7):
            self.rect(left, y, right, y + rowh, theme.BG, self.px(2))
            for hr in range(3, 24, 3):
                x = X(hr * 60)
                self.pen.line([x * SS, y * SS, x * SS, (y + rowh) * SS], fill=theme.pick(theme.BORDER), width=line_w)
            self.text(left - self.px(7), y + rowh / 2, names[wd], anchor="e",
                      color=theme.ACCENT if wd == self.today else theme.MUTED)
            for bar in self.rows[wd]:
                by = y + bar["lane"] * self.px(self.LANE_H + self.LANE_GAP)
                x0, x1 = X(bar["start"]), X(bar["end"])
                col = self.color_of(bar["item"])
                self.rect(x0, by, x1, by + self.px(self.LANE_H), theme._mix(col, self.bg, 0.72), self.px(2))
                self.pen.rectangle([x0 * SS, by * SS, (x0 + self.px(2)) * SS, (by + self.px(self.LANE_H)) * SS],
                                   fill=col)
                if x1 - x0 > self.px(46):
                    self.text(x0 + self.px(6), by + self.px(self.LANE_H) / 2 - self.px(1),
                              bar["item"]["display_name"], anchor="w", color=col, font=self.LABEL_FONT)
                self.click_hits.append((x0, by, x1, by + self.px(self.LANE_H), bar["item"]))
                self.hit(x0, by, x1, by + self.px(self.LANE_H),
                         f'{bar["item"]["display_name"]}   {bar["start"] // 60:02d}:{bar["start"] % 60:02d}'
                         f'-{bar["end"] // 60:02d}:{bar["end"] % 60:02d}')
            for m in self.unlocks[wd]:
                cx, cy, r = X(m), y - self.px(1), self.px(4)
                self.pen.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS], fill=self.bg,
                                 outline=theme.pick(theme.INFO), width=line_w)
            if wd == self.today:
                x = X(self.now_min)
                self.pen.line([x * SS, y * SS, x * SS, (y + rowh) * SS], fill=theme.pick(theme.ACCENT),
                              width=max(1, int(1.6 * self.s * SS)))
            y += rowh + self.px(self.ROW_GAP)
