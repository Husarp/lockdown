"""Blocking > Calendar (design 4a / 4b): one row per blocked item. Day view: a 24 h track per item with the blocked
stretches as bars (no text inside), "Blocked today" in its own column, a legend footer and three summary cards
(next change / busiest stretch / free window). 3 days / Week: the same rows, each day a mini strip; click a day to
open it in Day view. Only permanent and by-time rules appear (limits and temporary blocks aren't tied to a clock);
E markers show emergency unlocks. Click a row to edit that item."""
import tkinter as tk
from datetime import date, datetime, timedelta

import customtkinter as ctk

import blockcal
import stats
from gui import categories, icons, theme
from gui.charts import Chart, SS
from gui.components import Rows, Segmented, eyebrow, hairline, type_badge
from rules import effective_rules
from trusted_time import now_from_db

MUTED = theme.MUTED
DAY = 1440
ITEM_W, RIGHT_W, RIGHT_W_WEEK = 196, 196, 104
RANGES = {"Day": 1, "3 days": 3, "Week": 7}
FILTERS = {"Everything": None, "Sites": "site", "Apps": "app"}


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def span(a: int, b: int) -> str:
    return f"{hhmm(a)}–{hhmm(b)}"


def hours_text(minutes: int) -> str:
    if minutes >= DAY:
        return "all day"
    h, m = divmod(minutes, 60)
    return f"{h} h {m} m" if h and m else f"{h} h" if h else f"{m} m"


class Strip(Chart):
    """One row's track(s): the day view's full-width 24 h track, or one mini strip per day (3 days / week).
    set(days, color, marks, today, now_minute); on_click(day_index)."""
    H_DAY, H_WEEK, GAP = 28, 22, 6

    def __init__(self, master, on_click=None):
        super().__init__(master, height=self.H_DAY)
        self.days: list[list[tuple[int, int]]] = [[]]
        self.marks: list[list[int]] = [[]]
        self.color, self.today, self.now = theme.pick(theme.ACCENT), -1, None
        self.on_click = on_click
        self.bind("<Button-1>", self._click)

    def set(self, days, color: str, marks, today: int, now_minute: int | None):
        self.days, self.color, self.marks, self.today, self.now = days, color, marks, today, now_minute
        self.configure(height=int(self.px(self.H_DAY if len(days) == 1 else self.H_WEEK)))
        self._schedule()

    def _geometry(self, w):
        n = len(self.days)
        gap = self.px(self.GAP) if n > 1 else 0
        return n, gap, (w - gap * (n - 1)) / n

    def _click(self, event):
        n, gap, sw = self._geometry(self.winfo_width())
        i = min(n - 1, max(0, int(event.x // (sw + gap))))
        if self.on_click:
            self.on_click(i)

    def draw(self, w, h):
        n, gap, sw = self._geometry(w)
        line_w = max(1, int(self.s * SS))
        accent = theme.pick(theme.ACCENT)
        fill = theme._mix(self.color, self.bg, 0.72)
        for i, ivs in enumerate(self.days):
            x0 = i * (sw + gap)
            self.rect(x0, 0, x0 + sw, h, theme.BG, self.px(2))
            if n == 1:   # hour ticks every 2 h
                for hr in range(2, 24, 2):
                    x = x0 + sw * hr / 24
                    self.pen.line([x * SS, 0, x * SS, h * SS], fill=theme.pick(("#E4E7EB", "#1B2229")), width=line_w)
            top, edge = (self.px(5), self.px(3)) if n == 1 else (self.px(3), self.px(2))
            for a, b in ivs:
                bx0, bx1 = x0 + sw * a / DAY, x0 + sw * b / DAY
                self.rect(bx0, top, bx1, h - top, fill, self.px(2))
                self.pen.rectangle([bx0 * SS, top * SS, (bx0 + edge) * SS, (h - top) * SS], fill=self.color)
                self.hit(bx0, top, bx1, h - top, span(a, b))
            for m in self.marks[i] if i < len(self.marks) else []:
                r = self.px(8 if n == 1 else 7)
                cx, cy = min(x0 + sw - r, max(x0 + r, x0 + sw * m / DAY)), h / 2
                self.pen.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS], fill=theme.pick(theme.BG),
                                 outline=theme.pick(theme.INFO), width=line_w)
                self.text(cx, cy, "E", anchor="center", color=theme.INFO, font=(theme.DISPLAY, 7, "bold"))
                self.hit(cx - r, cy - r, cx + r, cy + r, f"emergency unlock at {hhmm(m)}")
            if n > 1 and i == self.today:   # a thin accent ring round today's cell
                self.pen.rectangle([x0 * SS, 0, (x0 + sw) * SS - 1, h * SS - 1], outline=accent, width=line_w)
        if self.now is not None and 0 <= self.today < n and n == 1:
            x = self.today * (sw + gap) + sw * self.now / DAY
            self.pen.line([x * SS, 0, x * SS, h * SS], fill=accent, width=max(1, int(2 * self.s * SS)))


class CalendarTab(ctk.CTkScrollableFrame):
    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.draft, self.db = page, page.draft, page.app.db
        self.start: date | None = None   # first shown day (None = today / this week)
        self.days = 1
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", pady=(0, 12))
        ctk.CTkButton(head, text="‹", width=26, height=26, **theme.OUTLINE, command=lambda: self._shift(-1)).pack(
            side="left")
        ctk.CTkButton(head, text="›", width=26, height=26, **theme.OUTLINE, command=lambda: self._shift(1)).pack(
            side="left", padx=(6, 12))
        self.title = ctk.CTkLabel(head, text="", font=theme.semi(15))
        self.title.pack(side="left")
        self.today_tag = ctk.CTkLabel(head, text="TODAY", font=theme.semi(11), text_color=theme.ACCENT)
        self.filter = ctk.CTkOptionMenu(head, width=124, values=list(FILTERS), command=lambda v: self.refresh())
        self.filter.pack(side="right")
        self.range = Segmented(head, values=list(RANGES), command=self._range)
        self.range.pack(side="right", padx=(0, 12))
        self.range.set("Day")

        card = ctk.CTkFrame(self, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER, corner_radius=4)
        card.pack(fill="x")
        # header columns have the same fixed widths as the rows, so the hour / day labels sit over the tracks
        self.head = ctk.CTkFrame(card, fg_color=theme.SURFACE2, corner_radius=0)
        self.head.pack(fill="x", padx=1, pady=(1, 0))
        item_head = ctk.CTkFrame(self.head, fg_color="transparent", width=ITEM_W, height=30)
        item_head.pack(side="left", padx=(14, 0))
        item_head.pack_propagate(False)
        eyebrow(item_head, "Item").pack(side="left", pady=8)
        self.right_box = ctk.CTkFrame(self.head, fg_color="transparent", width=RIGHT_W, height=30)
        self.right_box.pack(side="right", padx=(0, 14))
        self.right_box.pack_propagate(False)
        self.right_head = eyebrow(self.right_box, "Blocked today")
        self.right_head.pack(side="right", pady=8)
        self.axis = ctk.CTkFrame(self.head, fg_color="transparent")
        self.axis.pack(side="left", fill="x", expand=True)
        hairline(card).pack(fill="x", padx=1)
        self.rows = Rows(card, self._make_row, "Nothing is blocked by a clock rule in this range - time / opening "
                                              "limits and temporary blocks aren't tied to a clock time.",
                         {"fill": "x"})
        self.rows.frame.pack_configure(fill="x", padx=1)
        if self.rows.empty:
            self.rows.empty.configure(text_color=MUTED, wraplength=700, justify="left")
            self.rows.empty.pack_configure(padx=14, pady=10)
        self.foot = ctk.CTkFrame(card, fg_color=theme.SURFACE2, corner_radius=0)
        self.foot.pack(fill="x", padx=1, pady=(0, 1))
        self.legend = ctk.CTkFrame(self.foot, fg_color="transparent")
        self.legend.pack(side="left", padx=14, pady=8)
        self.hint = ctk.CTkLabel(self.foot, text="", text_color=MUTED, font=theme.body(11))
        self.hint.pack(side="right", padx=14)

        # day view only: next change / busiest stretch / free window
        self.summary = ctk.CTkFrame(self, fg_color="transparent")
        self.summary_values = []
        for i, (label, color) in enumerate((("Next change", theme.ACCENT), ("Busiest stretch", theme.WARNING),
                                             ("Free window", theme.SUCCESS))):
            self.summary.grid_columnconfigure(i, weight=1, uniform="s")
            box = ctk.CTkFrame(self.summary, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER,
                               corner_radius=3)
            box.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0 if i == 2 else 6))
            tk.Frame(box, width=3, bg=theme.pick(color), bd=0, highlightthickness=0).pack(side="left", fill="y",
                                                                                         padx=(1, 0), pady=1)
            texts = ctk.CTkFrame(box, fg_color="transparent")
            texts.pack(side="left", fill="x", expand=True, padx=12, pady=10)
            eyebrow(texts, label).pack(anchor="w")
            line = ctk.CTkFrame(texts, fg_color="transparent")
            line.pack(anchor="w", pady=(3, 0))
            value = ctk.CTkLabel(line, text="", font=theme.body(12), anchor="w", justify="left")
            value.pack(side="left")
            self.summary_values.append(value)
        # "Next change" says the outcome in colour: green = allowed again, accent = blocked (plate 4a)
        self.next_outcome = ctk.CTkLabel(self.summary_values[0].master, text="", font=theme.body(12), anchor="w")
        self.next_outcome.pack(side="left")

    # ---------- rows ----------

    def _make_row(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        line = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0)
        line.pack(fill="x", padx=14)
        item = ctk.CTkFrame(line, fg_color="transparent", width=ITEM_W, height=46)
        item.pack(side="left")
        item.pack_propagate(False)
        row.item = item
        row.icon = ctk.CTkLabel(item, text="", width=20)
        row.icon.pack(side="left", padx=(0, 9))
        row.name = ctk.CTkLabel(item, text="", font=theme.semi(13), anchor="w")
        row.name.pack(side="left")
        row.badge = ctk.CTkFrame(item, fg_color="transparent")
        row.badge.pack(side="left", padx=(8, 0))
        row.right = ctk.CTkFrame(line, fg_color="transparent", width=RIGHT_W, height=46)
        row.right.pack(side="right")
        row.right.pack_propagate(False)
        row.sum = ctk.CTkLabel(row.right, text="", font=theme.body(12), anchor="e", height=16)
        row.sum.pack(anchor="e", pady=(7, 0))
        row.sub = ctk.CTkLabel(row.right, text="", font=theme.body(11), text_color=MUTED, anchor="e", height=14)
        row.sub.pack(anchor="e")
        row.strip = Strip(line)
        row.strip.pack(side="left", fill="x", expand=True, pady=9)
        hairline(row).pack(fill="x")
        for w in (item, row.icon, row.name):
            w.configure(cursor="hand2")
        return row

    # ---------- range ----------

    def _range(self, label: str):
        self.days = RANGES[label]
        self.refresh()

    def _shift(self, direction: int):
        self.start = self._first(now_from_db(self.db).date()) + timedelta(days=direction * self.days)
        self.refresh()

    def _first(self, today: date) -> date:
        start = self.start or today
        return start - timedelta(days=start.weekday()) if self.days == 7 else start

    def _open_day(self, day: date):
        self.start, self.days = day, 1
        self.range.set("Day")
        self.refresh()

    def on_show(self):
        self.refresh()

    # ---------- data ----------

    def refresh(self, *_):
        now = now_from_db(self.db)
        today, now_min = now.date(), now.hour * 60 + now.minute
        n = self.days
        start = self._first(today)
        dates = [start + timedelta(days=i) for i in range(n)]
        end = dates[-1]
        if n == 1:
            self.title.configure(text=f"{start:%A %d %B}")
        else:
            self.title.configure(text=f"{start:%d} – {end:%d %B}" if start.month == end.month
                                 else f"{start:%d %b} – {end:%d %b}")
        if n == 1 and start == today:
            self.today_tag.pack(side="left", padx=12)
        else:
            self.today_tag.pack_forget()
        today_i = dates.index(today) if today in dates else -1

        kind = FILTERS[self.filter.get()]
        items = [i for i in self.draft.items.values() if kind is None or i["item_type"] == kind]
        groups = list(self.draft.groups.values())
        cats = categories.load(self.db)
        colors, saved = categories.colors_of(cats), self.db.categories()
        all_items = list(self.draft.items.values())

        def color_of(item):
            name = item["target"].split()[0]
            return theme.pick(colors.get(stats.category_of(item["item_type"], name, saved, all_items)) or MUTED)

        marks: dict[int, list[list[int]]] = {}
        for u in self.db.unlocks_since(datetime.combine(start, datetime.min.time())):
            d = u["started"].date()
            if d in dates:
                for item_id in u["item_ids"]:
                    marks.setdefault(item_id, [[] for _ in range(n)])[dates.index(d)].append(
                        u["started"].hour * 60 + u["started"].minute)
        shown = []
        for item in sorted(items, key=lambda i: i["display_name"].lower()):
            rules = effective_rules(item, groups)
            days = [blockcal.item_day_intervals(rules, d.weekday()) for d in dates]
            if any(days) or item["id"] in marks:
                shown.append((item, days))

        for row, (item, days) in zip(self.rows.take(len(shown)), shown):
            row.icon.configure(image=icons.for_item(item, 20))
            row.name.configure(text=item["display_name"])
            for w in row.badge.winfo_children():
                w.destroy()
            type_badge(row.badge, item["item_type"]).pack()
            height = 46 if n == 1 else 40
            row.item.configure(height=height)
            row.right.configure(height=height, width=RIGHT_W if n == 1 else RIGHT_W_WEEK)
            row.strip.set(days, color_of(item), marks.get(item["id"], [[] for _ in range(n)]), today_i,
                          now_min if today_i >= 0 else None)
            total = sum(b - a for ivs in days for a, b in ivs)
            if n == 1:
                row.sum.configure(text=f"{hours_text(total)} blocked" if total else "not blocked today")
                row.sub.configure(text=", ".join(span(a, b) for a, b in days[0]))
            else:
                h, m = divmod(total, 60)
                row.sum.configure(text=(f"{h} h {m} m" if m else f"{h} h") if total else "-")
                row.sub.configure(text="blocked this week" if n == 7 else "blocked in 3 days")
            row.strip.on_click = lambda i, dates=dates: self._open_day(dates[i]) if n > 1 else self._edit(item)
            for w in (row.item, row.icon, row.name):
                w.bind("<Button-1>", lambda e, it=item: self._edit(it))

        self._axis(dates, today_i)
        self.right_box.configure(width=RIGHT_W if n == 1 else RIGHT_W_WEEK)
        self.right_head.configure(text="BLOCKED TODAY" if n == 1 and today_i == 0 else
                                  "BLOCKED THAT DAY" if n == 1 else "WEEK TOTAL" if n == 7 else "3-DAY TOTAL")
        self._legend(cats, now_min if n == 1 and today_i == 0 else None)
        self.hint.configure(text="Click a bar to edit that rule" if n == 1 else
                            "Each cell is one day, midnight → midnight · click a day to open it in Day view")
        if n == 1:
            self._summarize([days[0] for _, days in shown], [i["display_name"] for i, _ in shown],
                            now_min if today_i == 0 else None)
            self.summary.pack(fill="x", pady=(12, 0))
        else:
            self.summary.pack_forget()

    def _edit(self, item):
        self.page.edit_item(item["id"])

    def _axis(self, dates, today_i):
        for w in self.axis.winfo_children():
            w.destroy()
        if len(dates) == 1:
            for col in range(12):
                self.axis.grid_columnconfigure(col, weight=1, uniform="h")
                ctk.CTkLabel(self.axis, text=f"{col * 2:02d}", font=theme.body(10), text_color=MUTED, anchor="w",
                             height=14).grid(row=0, column=col, sticky="w")
        else:
            for col, d in enumerate(dates):
                self.axis.grid_columnconfigure(col, weight=1, uniform="d")
                ctk.CTkLabel(self.axis, text=f"{d:%a}" if len(dates) == 7 else f"{d:%a %d}", font=theme.semi(11),
                             text_color=theme.ACCENT if col == today_i else MUTED, height=14).grid(
                    row=0, column=col, sticky="ew")
        for col in range(12):
            if col >= len(dates) and len(dates) > 1:
                self.axis.grid_columnconfigure(col, weight=0, uniform="")

    def _legend(self, cats, now_min):
        for w in self.legend.winfo_children():
            w.destroy()

        def entry(text, swatch=None, mark=None, color=MUTED):
            f = ctk.CTkFrame(self.legend, fg_color="transparent")
            f.pack(side="left", padx=(0, 16))
            if swatch:
                tk.Frame(f, width=9, height=9, bg=theme.pick(swatch), bd=0, highlightthickness=0).pack(
                    side="left", padx=(0, 6))
            if mark:
                ctk.CTkLabel(f, text=mark, text_color=color, font=theme.semi(11), height=14).pack(side="left", padx=(0, 5))
            ctk.CTkLabel(f, text=text, text_color=MUTED, font=theme.body(11), height=14).pack(side="left")
        for c in cats:
            entry(c["name"], swatch=c["color"])
        if now_min is not None:
            entry(f"now {hhmm(now_min)}", mark="▌", color=theme.ACCENT)
        entry("emergency unlock", mark="Ⓔ", color=theme.INFO)

    def _summarize(self, rows: list[list[tuple[int, int]]], names: list[str], now_min: int | None):
        nxt, busiest, free = self.summary_values
        events = [(a, 0, name) for ivs, name in zip(rows, names) for a, _ in ivs if a > 0] \
            + [(b, 1, name) for ivs, name in zip(rows, names) for _, b in ivs if b < DAY]
        e = None if now_min is None else min((e for e in events if e[0] > now_min), default=None)
        if e:
            nxt.configure(text=f"{hhmm(e[0])} — ")
            self.next_outcome.configure(text=f"{e[2]} {'allowed again' if e[1] else 'blocked'}",
                                        text_color=theme.SUCCESS if e[1] else theme.ACCENT)
        else:
            nxt.configure(text="(not today)" if now_min is None else "no more changes today")
            self.next_outcome.configure(text="")
        bounds = sorted({0, DAY} | {a for ivs in rows for a, _ in ivs} | {b for ivs in rows for _, b in ivs})
        segments = []   # (start, end, how many items blocked), neighbours with the same count merged
        for a, b in zip(bounds, bounds[1:]):
            count = sum(1 for ivs in rows for x, y in ivs if x <= a and b <= y)
            if segments and segments[-1][2] == count:
                segments[-1] = (segments[-1][0], b, count)
            else:
                segments.append((a, b, count))
        top = max(segments, key=lambda s: (s[2], s[1] - s[0]))
        busiest.configure(text=f"{span(top[0], top[1])} — {top[2]} item{'s' * (top[2] != 1)} blocked"
                               + (" together" if top[2] > 1 else "") if top[2] else "nothing is blocked today")
        gaps = [s for s in segments if s[2] == 0]
        if gaps:
            g = max(gaps, key=lambda s: s[1] - s[0])
            free.configure(text=f"{span(g[0], g[1])} — nothing but the lists")
        else:
            free.configure(text="none today — something is blocked all day")
