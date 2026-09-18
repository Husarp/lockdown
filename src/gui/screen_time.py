"""Screen Time: Overview · Apps · Websites · Switches, for Today / Yesterday / 7 days / 30 days.
Categories (productive / neutral / distracting) are changed by clicking an app's or site's category label."""
from collections import Counter
from datetime import timedelta

import customtkinter as ctk

import stats
from gui import app_browser, appinfo, theme
from gui.components import Card, Chip, DayBars, Donut, Heatmap, HourBars, StatCard, TimelineBar
from gui.dashboard import goal_seconds
from rules import DAY_NAMES
from trusted_time import now_from_db

TABS = ["Overview", "Apps", "Websites", "Switches"]
REFRESH_MS = 60_000
MAX_ROWS = 40
STYLE = {"checking": ("Short visits - often just checking", theme.WARNING),
         "focused": ("Long, focused stretches", theme.SUCCESS),
         "mixed": ("Mixed use", theme.MUTED)}


def _grid_cards(parent, labels: list[str]) -> dict[str, StatCard]:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=(0, 12))
    out = {}
    for i, label in enumerate(labels):
        row.grid_columnconfigure(i, weight=1, uniform="stat")
        out[label] = StatCard(row, label)
        out[label].grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0 if i == len(labels) - 1 else 6))
    return out


def _columns(parent, weights=(3, 2)) -> list[ctk.CTkFrame]:
    grid = ctk.CTkFrame(parent, fg_color="transparent")
    grid.pack(fill="both", expand=True)
    cols = []
    for i, w in enumerate(weights):
        grid.grid_columnconfigure(i, weight=w, uniform="col")
        col = ctk.CTkFrame(grid, fg_color="transparent")
        col.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0 if i == len(weights) - 1 else 6))
        cols.append(col)
    return cols


class ScreenTimePage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        app_browser.preload()
        ctk.CTkLabel(self, text="Screen Time", font=theme.page_title()).pack(anchor="w", padx=30, pady=(12, 6))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=30, pady=(0, 10))
        self.tab_bar = ctk.CTkSegmentedButton(bar, values=TABS, command=self.show_tab, width=330, height=30,
                                              dynamic_resizing=False)
        self.tab_bar.pack(side="left")
        self.range_bar = ctk.CTkSegmentedButton(bar, values=list(stats.RANGES), command=lambda v: self.refresh(),
                                                width=300, height=30, dynamic_resizing=False)
        self.range_bar.pack(side="right")
        self.tab_bar.set("Overview")
        self.range_bar.set("Today")
        self.content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=(20, 12), pady=(0, 14))
        self.after(REFRESH_MS, self._auto_refresh)

    def show_tab(self, tab: str):
        self.tab_bar.set(tab)
        self.refresh()

    def on_show(self):
        self.refresh()

    def _auto_refresh(self):
        if getattr(self.app, "current_page", None) == "Screen Time":
            self.refresh()
        self.after(REFRESH_MS, self._auto_refresh)

    def refresh(self):
        for w in self.content.winfo_children():
            w.destroy()
        db = self.db
        self.now = now_from_db(db)
        self.items = db.list_items()
        self.saved_categories = db.categories()
        self.start, self.end = stats.range_dates(self.range_bar.get(), self.now.date())
        self.rows = stats.activity(db, self.start, self.end)
        self.events = stats.switches(db, self.start, self.end)
        getattr(self, "_" + self.tab_bar.get().lower())()

    def _category(self, kind: str, name: str) -> str:
        return stats.category_of(kind, name, self.saved_categories, self.items)

    def _cycle(self, kind: str, name: str):
        self.db.set_category(kind, name, stats.next_category(self._category(kind, name)))
        self.refresh()

    # ---------- Overview ----------

    def _overview(self):
        active, total = stats.totals(self.rows)
        cards = _grid_cards(self.content, ["Active", "Idle", "Longest focus", "Sessions"])
        cards["Active"].set(stats.hm(active), f"of {stats.hm(total)} at the PC")
        cards["Idle"].set(stats.hm(total - active), "screen on, no input")
        focus = stats.longest_focus(self.rows)
        if focus:
            sec, exe, start = focus
            cards["Longest focus"].set(stats.hm(sec), f"{appinfo.name_of('app', exe, self.items)}, {start:%H:%M}")
        else:
            cards["Longest focus"].set("-")
        sessions = stats.sessions(self.rows)
        avg = sum((e - s).total_seconds() for s, e in sessions) / len(sessions) if sessions else 0
        cards["Sessions"].set(str(len(sessions)), f"avg {stats.hm(avg)} each" if sessions else "")

        left, right = _columns(self.content)
        day = self.start if self.range_bar.get() in ("Today", "Yesterday") else self.now.date()
        title = "Day timeline" if day == self.start else "Day timeline - today"
        card = Card(left, title)
        card.pack(fill="x", pady=(0, 12))
        day_rows = self.rows if day == self.start else stats.activity(self.db, day, day + timedelta(days=1))
        category = lambda exe, site: self._category(*(("site", site) if site else ("app", exe)))
        start_hour = min(6, int(day_rows[0]["minute"][11:13])) if day_rows else 6
        timeline = TimelineBar(card.body)
        timeline.pack(fill="x")
        timeline.set(stats.timeline(day_rows, day, category), start_hour)

        week = [self.now.date() - timedelta(days=6 - i) for i in range(7)]
        heat_rows = stats.activity(self.db, week[0], week[-1] + timedelta(days=1))
        card = Card(left, "Active hours - last 7 days")
        card.pack(fill="x")
        heat = Heatmap(card.body)
        heat.pack(fill="x")
        heat.set([(DAY_NAMES[d.weekday()][:3], levels) for d, levels in zip(week, stats.heatmap(heat_rows, week))])

        n = 30 if self.range_bar.get() == "30 days" else 7
        days = [self.now.date() - timedelta(days=n - 1 - i) for i in range(n)]
        per_day = stats.per_day(stats.activity(self.db, days[0], days[-1] + timedelta(days=1)))
        values = [per_day.get(d.isoformat(), 0) for d in days]
        with_data = [v for v in values if v]
        card = Card(right, f"Last {n} days", note=f"avg {stats.hm(sum(with_data) / len(with_data))}" if with_data else "")
        card.pack(fill="x", pady=(0, 12))
        bars = DayBars(card.body, height=190)
        bars.pack(fill="x")
        label = (lambda d: DAY_NAMES[d.weekday()][:3]) if n == 7 else (lambda d: str(d.day))
        bars.set([(label(d), v, d == self.now.date()) for d, v in zip(days, values)], goal_seconds(self.db))

        split = Counter()
        for r in self.rows:
            if r["active"]:
                split[category(r["exe"], r["site"])] += r["active"]
        card = Card(right, "Categories")
        card.pack(fill="x")
        inner = ctk.CTkFrame(card.body, fg_color="transparent")
        inner.pack(fill="x")
        donut = Donut(inner, 110)
        donut.pack(side="left")
        parts = [(split[c], theme.CATEGORY_COLORS[c]) for c in ("productive", "neutral", "distracting")]
        donut.set(parts, stats.hm(active).replace(" h ", "h").replace(" m", "") if active else "0")
        legend = ctk.CTkFrame(inner, fg_color="transparent")
        legend.pack(side="left", fill="x", expand=True, padx=(14, 0))
        for c in ("productive", "neutral", "distracting"):
            line = ctk.CTkFrame(legend, fg_color="transparent")
            line.pack(fill="x", pady=2)
            ctk.CTkFrame(line, width=10, height=10, corner_radius=2, fg_color=theme.CATEGORY_COLORS[c]).pack(
                side="left", padx=(0, 8))
            ctk.CTkLabel(line, text=c.capitalize(), height=18).pack(side="left")
            ctk.CTkLabel(line, text=stats.hm(split[c]), font=theme.semi(13), height=18).pack(side="right")

    # ---------- Apps / Websites ----------

    def _apps(self):
        switches = Counter(e["exe"] for e in self.events)
        self._table("app", stats.per_app(self.rows), switches, ["App", "Share of day", "Time", "Switches", "Category"])

    def _websites(self):
        visits = Counter(e["site"] for e in self.events if e["site"])
        self._table("site", stats.per_site(self.rows), visits,
                    ["Website", "Share of browsing", "Time", "Visits", "Category"])

    def _table(self, kind: str, times: Counter, counts: Counter, headers: list[str]):
        card = Card(self.content)
        card.pack(fill="both", expand=True)
        widths = [230, 0, 90, 80, 110]
        head = ctk.CTkFrame(card.body, fg_color="transparent")
        head.pack(fill="x", pady=(6, 4))
        for i, (text, width) in enumerate(zip(headers, widths)):
            head.grid_columnconfigure(i, minsize=width, weight=1 if i == 1 else 0)
            ctk.CTkLabel(head, text=text.upper(), font=theme.eyebrow(), text_color=theme.MUTED, height=14).grid(
                row=0, column=i, sticky="w" if i < 2 else "e", padx=(36 if i == 0 else 8, 8))
        ranked = [(n, s) for n, s in times.most_common(MAX_ROWS) if s >= 60]
        if not ranked:
            ctk.CTkLabel(card.body, text="Nothing recorded for this period yet.", text_color=theme.MUTED).pack(
                anchor="w", pady=12)
            return
        top = ranked[0][1]
        for name, sec in ranked:
            cat = self._category(kind, name)
            color = theme.CATEGORY_COLORS[cat]
            ctk.CTkFrame(card.body, height=1, fg_color=theme.BORDER).pack(fill="x")
            row = ctk.CTkFrame(card.body, fg_color="transparent", height=44)
            row.pack(fill="x")
            for i, width in enumerate(widths):
                row.grid_columnconfigure(i, minsize=width, weight=1 if i == 1 else 0)
            who = ctk.CTkFrame(row, fg_color="transparent")
            who.grid(row=0, column=0, sticky="w", pady=6)
            ctk.CTkLabel(who, text="", image=appinfo.icon_of(kind, name, self.items, 22), width=28).pack(side="left")
            texts = ctk.CTkFrame(who, fg_color="transparent")
            texts.pack(side="left", padx=(6, 0))
            ctk.CTkLabel(texts, text=appinfo.name_of(kind, name, self.items), height=16, anchor="w").pack(anchor="w")
            if kind == "app":
                ctk.CTkLabel(texts, text=name, text_color=theme.MUTED, font=theme.body(10), height=12,
                             anchor="w").pack(anchor="w")
            share = ctk.CTkProgressBar(row, height=6, corner_radius=3, progress_color=color)
            share.grid(row=0, column=1, sticky="ew", padx=8)
            share.set(sec / top)
            ctk.CTkLabel(row, text=stats.hm(sec), font=theme.semi(13)).grid(row=0, column=2, sticky="e", padx=8)
            ctk.CTkLabel(row, text=str(counts.get(name, 0)), text_color=theme.MUTED).grid(row=0, column=3, sticky="e",
                                                                                          padx=8)
            Chip(row, cat.capitalize(), color, command=lambda k=kind, n=name: self._cycle(k, n), width=84).grid(
                row=0, column=4, sticky="e", padx=(8, 0))
        ctk.CTkLabel(card.body, text="Click a category to change it (productive → neutral → distracting).",
                     text_color=theme.MUTED, font=theme.body(11)).pack(anchor="w", pady=(8, 0))

    # ---------- Switches ----------

    def _switches(self):
        summary = stats.switch_summary(self.events, self.now)
        name = self.range_bar.get()
        label = "Switches today" if name == "Today" else "Switches yesterday" if name == "Yesterday" \
            else f"Switches ({name})"
        cards = _grid_cards(self.content, [label, "Average time per visit", "Short visits (< 30 s)"])
        if name == "Today":
            avg = stats.average_daily_switches(self.db, self.now.date())
            n = summary["count"]
            if avg is None or abs(n - avg) < 1:
                cards[label].set(str(n), "about your average" if avg else "")
            else:
                cards[label].set(str(n), f"{abs(round(n - avg))} {'more' if n > avg else 'fewer'} than your average",
                                 theme.WARNING if n > avg else theme.SUCCESS)
        else:
            cards[label].set(str(summary["count"]))
        cards["Average time per visit"].set(stats.ms(summary["avg_visit"]) if summary["count"] else "-")
        top = [appinfo.name_of(kind, n, self.items) for kind, n in summary["short_top"]]
        cards["Short visits (< 30 s)"].set(str(summary["short"]), f"mostly {' and '.join(top)}" if top else "")

        left, right = _columns(self.content, (1, 1))
        card = Card(left, "Switches per hour")
        card.pack(fill="both", expand=True)
        per_hour = summary["per_hour"]
        if per_hour:
            peak = max(per_hour, key=per_hour.get)
            ctk.CTkLabel(card.body, text=f"Peak between {peak:02d}:00 and {peak + 1:02d}:00 - {per_hour[peak]} switches.",
                         text_color=theme.MUTED, font=theme.body(11), height=14).pack(anchor="w")
        chart = HourBars(card.body, height=330)
        chart.pack(fill="both", expand=True, pady=(6, 0))
        chart.set(dict(per_hour))

        card = Card(right, "Most switched to")
        card.pack(fill="both", expand=True)
        ctk.CTkLabel(card.body, text="Many switches with very short visits usually means checking out of habit.",
                     text_color=theme.MUTED, font=theme.body(11), wraplength=380, justify="left").pack(anchor="w")
        if not summary["targets"]:
            ctk.CTkLabel(card.body, text="No switches yet.", text_color=theme.MUTED).pack(anchor="w", pady=8)
        for (kind, target), count, avg in summary["targets"][:5]:
            text, color = STYLE[stats.visit_style(count, avg)]
            entry = ctk.CTkFrame(card.body, fg_color=theme.SURFACE2, corner_radius=4)
            entry.pack(fill="x", pady=(8, 0))
            ctk.CTkFrame(entry, width=3, height=1, fg_color=color, corner_radius=0).pack(side="left", fill="y")
            ctk.CTkLabel(entry, text="", image=appinfo.icon_of(kind, target, self.items, 20), width=28).pack(
                side="left", padx=(8, 4), pady=8)
            texts = ctk.CTkFrame(entry, fg_color="transparent")
            texts.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(texts, text=appinfo.name_of(kind, target, self.items), font=theme.semi(13), height=16,
                         anchor="w").pack(anchor="w")
            ctk.CTkLabel(texts, text=text, text_color=color, font=theme.body(11), height=14, anchor="w").pack(anchor="w")
            nums = ctk.CTkFrame(entry, fg_color="transparent")
            nums.pack(side="right", padx=10)
            ctk.CTkLabel(nums, text=str(count), font=theme.numeral(18), height=18, anchor="e").pack(anchor="e")
            ctk.CTkLabel(nums, text=f"avg {stats.ms(avg)}", text_color=theme.MUTED, font=theme.body(10), height=12,
                         anchor="e").pack(anchor="e")
