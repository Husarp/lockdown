"""Screen Time: Overview · Apps · Websites · Switches, for Today / Yesterday / 7 days / 30 days; Calendar: a month
at a time.
Each tab is built once and then only updated (rebuilding Tk widgets is what makes switching slow).
Clicking an app's or site's category opens a small menu (pick / new category / edit colours)."""
from collections import Counter
from datetime import date, datetime, timedelta

import customtkinter as ctk

import stats
from gui import app_browser, appinfo, categories, theme
from gui.charts import DayBars, Donut, Heatmap, HourBars, MonthCalendar, TimelineBar
from gui.components import Curtain, Card, Chip, Rows, Segmented, StatCard, help_icon
from gui.dashboard import goal_seconds
from rules import DAY_NAMES
from trusted_time import now_from_db

TABS = ["Overview", "Apps", "Websites", "Switches", "Calendar"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"]
REFRESH_MS = 60_000
MAX_ROWS = 40
STYLE = {"checking": ("Short visits - often just checking", theme.WARNING),
         "focused": ("Long, focused stretches", theme.SUCCESS),
         "mixed": ("Mixed use", theme.MUTED)}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _stat_row(parent, labels: list[str]) -> dict[str, StatCard]:
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


def day_tip(d: date, seconds: float) -> str:
    return f"{DAY_NAMES[d.weekday()]} {d.day} {MONTHS[d.month - 1]}  {stats.hm(seconds)}"


def unlocks_per_day(db, start: date) -> Counter:
    return Counter(u["started"].date() for u in db.unlocks_since(datetime.combine(start, datetime.min.time())))


def _legend_row(parent):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    f.square = ctk.CTkFrame(f, width=10, height=10, corner_radius=2)
    f.square.pack(side="left", padx=(0, 8))
    f.name = ctk.CTkLabel(f, text="", height=18)
    f.name.pack(side="left")
    f.time = ctk.CTkLabel(f, text="", font=theme.semi(13), height=18)
    f.time.pack(side="right")
    return f


class Context:
    """Everything a tab needs for one refresh."""

    def __init__(self, page):
        db = page.db
        self.db, self.page = db, page
        self.now = now_from_db(db)
        self.today = self.now.date()
        self.range = page.range_bar.get()
        self.items = db.list_items()
        self.saved = db.categories()
        self.cats = categories.load(db)
        self.colors = categories.colors_of(self.cats)
        self.names = categories.names_of(self.cats)
        self.start, self.end = stats.range_dates(self.range, self.today)
        self.rows = stats.activity(db, self.start, self.end)
        self.events = stats.switches(db, self.start, self.end)

    def category(self, kind: str, name: str) -> str:
        cat = stats.category_of(kind, name, self.saved, self.items)
        return cat if cat in self.colors else "neutral"

    def category_of_row(self, exe: str, site: str) -> str:
        return self.category(*(("site", site) if site else ("app", exe)))


# ---------------------------------------------------------------- Overview

class OverviewView(ctk.CTkScrollableFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.cards = _stat_row(self, ["Active", "Idle", "Longest focus", "Sessions"])
        left, right = _columns(self)
        self.timeline_card = Card(left, "Day timeline")
        self.timeline_card.pack(fill="x", pady=(0, 12))
        self.timeline = TimelineBar(self.timeline_card.body)
        self.timeline.pack(fill="x")
        card = Card(left, "Active hours - last 7 days")
        card.pack(fill="x")
        self.heat = Heatmap(card.body)
        self.heat.pack(fill="x")
        self.bars_card = Card(right, "Last 7 days")
        self.bars_card.pack(fill="x", pady=(0, 12))
        self.bars = DayBars(self.bars_card.body, height=190)
        self.bars.pack(fill="x")
        card = Card(right, "Categories")
        card.pack(fill="x")
        inner = ctk.CTkFrame(card.body, fg_color="transparent")
        inner.pack(fill="x")
        self.donut = Donut(inner, 110)
        self.donut.pack(side="left")
        legend = ctk.CTkFrame(inner, fg_color="transparent")
        legend.pack(side="left", fill="x", expand=True, padx=(14, 0))
        self.legend = Rows(legend, _legend_row, item_pack={"fill": "x", "pady": 2})

    def update_view(self, c: Context):
        active, total = stats.totals(c.rows)
        self.cards["Active"].set(stats.hm(active), f"of {stats.hm(total)} at the PC")
        self.cards["Idle"].set(stats.hm(total - active), "screen on, no input")
        focus = stats.longest_focus(c.rows)
        if focus:
            sec, exe, start = focus
            self.cards["Longest focus"].set(stats.hm(sec), f"{appinfo.name_of('app', exe, c.items)}, {start:%H:%M}")
        else:
            self.cards["Longest focus"].set("-")
        sessions = stats.sessions(c.rows)
        avg = sum((e - s).total_seconds() for s, e in sessions) / len(sessions) if sessions else 0
        self.cards["Sessions"].set(str(len(sessions)), f"avg {stats.hm(avg)} each" if sessions else "")

        single_day = c.range in ("Today", "Yesterday")
        n = 30 if c.range == "30 days" else 7
        first = c.today - timedelta(days=n - 1)
        span_rows = stats.activity(c.db, first, c.today + timedelta(days=1))   # bars, heatmap, today's timeline
        day = c.start if single_day else c.today
        day_rows = c.rows if single_day else [r for r in span_rows if r["minute"][:10] == day.isoformat()]
        self.timeline_card.title.configure(text="Day timeline" if single_day else "Day timeline - today")
        start_hour = min(6, int(day_rows[0]["minute"][11:13])) if day_rows else 6
        self.timeline.set(stats.timeline(day_rows, day, c.category_of_row), start_hour,
                          {**c.colors, "idle": theme.TRACK}, {**c.names, "idle": "Idle"})

        week = [c.today - timedelta(days=6 - i) for i in range(7)]
        week_rows = [r for r in span_rows if r["minute"][:10] >= week[0].isoformat()]
        self.heat.set([(DAY_NAMES[d.weekday()][:3], d, m) for d, m in zip(week, stats.hourly_minutes(week_rows, week))])

        days = [first + timedelta(days=i) for i in range(n)]
        per_day = stats.per_day(span_rows)
        values = [per_day.get(d.isoformat(), 0) for d in days]
        with_data = [v for v in values if v]
        self.bars_card.title.configure(text=f"Last {n} days")
        self.bars_card.note.configure(text=f"avg {stats.hm(sum(with_data) / len(with_data))}" if with_data else "")
        unlocks = unlocks_per_day(c.db, first)
        label = (lambda d: DAY_NAMES[d.weekday()][:3]) if n == 7 else (lambda d: str(d.day))
        self.bars.set([(label(d), v, d == c.today, day_tip(d, v), unlocks.get(d, 0)) for d, v in zip(days, values)],
                      goal_seconds(c.db))

        split = Counter()
        for r in c.rows:
            if r["active"]:
                split[c.category_of_row(r["exe"], r["site"])] += r["active"]
        shown = [cat for cat in c.cats if split[cat["key"]] or cat["builtin"]]
        self.donut.set([(split[cat["key"]], cat["color"], f"{cat['name']}  {stats.hm(split[cat['key']])}")
                        for cat in shown], stats.hm(active).replace(" h ", "h").replace(" m", "") if active else "0")
        for row, cat in zip(self.legend.take(len(shown)), shown):
            row.square.configure(fg_color=cat["color"])
            row.name.configure(text=cat["name"])
            row.time.configure(text=stats.hm(split[cat["key"]]))


# ---------------------------------------------------------------- Apps / Websites

class TableRow:
    def __init__(self, parent, widths):
        self.sep = ctk.CTkFrame(parent, height=1, fg_color=theme.BORDER)
        self.frame = ctk.CTkFrame(parent, fg_color="transparent", height=44)
        for i, width in enumerate(widths):
            self.frame.grid_columnconfigure(i, minsize=width, weight=1 if i == 1 else 0)
        who = ctk.CTkFrame(self.frame, fg_color="transparent")
        who.grid(row=0, column=0, sticky="w", pady=6)
        self.icon = ctk.CTkLabel(who, text="", width=28)
        self.icon.pack(side="left")
        texts = ctk.CTkFrame(who, fg_color="transparent")
        texts.pack(side="left", padx=(6, 0))
        self.name = ctk.CTkLabel(texts, text="", height=16, anchor="w")
        self.name.pack(anchor="w")
        self.sub = ctk.CTkLabel(texts, text="", text_color=theme.MUTED, font=theme.body(10), height=12, anchor="w")
        self.sub.pack(anchor="w")
        self.share = ctk.CTkProgressBar(self.frame, height=6, corner_radius=3)
        self.share.grid(row=0, column=1, sticky="ew", padx=8)
        self.time = ctk.CTkLabel(self.frame, text="", font=theme.semi(13))
        self.time.grid(row=0, column=2, sticky="e", padx=8)
        self.count = ctk.CTkLabel(self.frame, text="", text_color=theme.MUTED)
        self.count.grid(row=0, column=3, sticky="e", padx=8)
        self.chip = Chip(self.frame, "", theme.MUTED, width=100)
        self.chip.grid(row=0, column=4, sticky="e", padx=(8, 0))

    def show(self, on: bool):
        if on:
            self.sep.pack(fill="x")
            self.frame.pack(fill="x")
        else:
            self.sep.pack_forget()
            self.frame.pack_forget()


class TableView(ctk.CTkScrollableFrame):
    WIDTHS = [230, 0, 90, 80, 120]

    def __init__(self, master, kind: str, headers: list[str]):
        super().__init__(master, fg_color="transparent")
        self.kind = kind
        card = Card(self)
        card.pack(fill="both", expand=True)
        head = ctk.CTkFrame(card.body, fg_color="transparent")
        head.pack(fill="x", pady=(6, 4))
        for i, (text, width) in enumerate(zip(headers, self.WIDTHS)):
            head.grid_columnconfigure(i, minsize=width, weight=1 if i == 1 else 0)
            ctk.CTkLabel(head, text=text.upper(), font=theme.eyebrow(), text_color=theme.MUTED, height=14).grid(
                row=0, column=i, sticky="w" if i < 2 else "e", padx=(36 if i == 0 else 8, 8))
        self.box = ctk.CTkFrame(card.body, fg_color="transparent")
        self.box.pack(fill="x")
        self.empty = ctk.CTkLabel(card.body, text="Nothing recorded for this period yet.", text_color=theme.MUTED)
        self.rows: list[TableRow] = []

    def update_view(self, c: Context):
        if self.kind == "app":
            times, counts = stats.per_app(c.rows), Counter(e["exe"] for e in c.events)
        else:
            times, counts = stats.per_site(c.rows), Counter(e["site"] for e in c.events if e["site"])
        ranked = [(n, s) for n, s in times.most_common(MAX_ROWS) if s >= 60]
        if ranked:
            self.empty.pack_forget()
        else:
            self.empty.pack(anchor="w", pady=12)
        top = ranked[0][1] if ranked else 1
        while len(self.rows) < len(ranked):
            self.rows.append(TableRow(self.box, self.WIDTHS))
        for row, (name, sec) in zip(self.rows, ranked):
            cat = c.category(self.kind, name)
            color = c.colors[cat]
            row.icon.configure(image=appinfo.icon_of(self.kind, name, c.items, 22))
            row.name.configure(text=appinfo.name_of(self.kind, name, c.items))
            row.sub.configure(text=name if self.kind == "app" else "")
            row.share.configure(progress_color=color)
            row.share.set(sec / top)
            row.time.configure(text=stats.hm(sec))
            row.count.configure(text=str(counts.get(name, 0)))
            row.chip.configure(text=f"{c.names[cat]}  ▾", border_color=color, text_color=color,
                               command=lambda chip=row.chip, n=name, k=cat: categories.open_menu(
                                   chip, c.db, self.kind, n, k, c.page.refresh))
            row.show(True)
        for row in self.rows[len(ranked):]:
            row.show(False)


# ---------------------------------------------------------------- Switches

class TargetEntry:
    def __init__(self, parent):
        self.frame = ctk.CTkFrame(parent, fg_color=theme.SURFACE2, corner_radius=4)
        self.stripe = ctk.CTkFrame(self.frame, width=3, height=1, corner_radius=0)
        self.stripe.pack(side="left", fill="y")
        self.icon = ctk.CTkLabel(self.frame, text="", width=28)
        self.icon.pack(side="left", padx=(8, 4), pady=8)
        texts = ctk.CTkFrame(self.frame, fg_color="transparent")
        texts.pack(side="left", fill="x", expand=True)
        self.name = ctk.CTkLabel(texts, text="", font=theme.semi(13), height=16, anchor="w")
        self.name.pack(anchor="w")
        self.style = ctk.CTkLabel(texts, text="", font=theme.body(11), height=14, anchor="w")
        self.style.pack(anchor="w")
        nums = ctk.CTkFrame(self.frame, fg_color="transparent")
        nums.pack(side="right", padx=10)
        self.count = ctk.CTkLabel(nums, text="", font=theme.numeral(18), height=18, anchor="e")
        self.count.pack(anchor="e")
        self.avg = ctk.CTkLabel(nums, text="", text_color=theme.MUTED, font=theme.body(10), height=12, anchor="e")
        self.avg.pack(anchor="e")


class SwitchesView(ctk.CTkScrollableFrame):
    LABELS = ["Switches", "Average time per visit", "Short visits (< 30 s)"]

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.cards = _stat_row(self, self.LABELS)
        left, right = _columns(self, (1, 1))
        card = Card(left, "Switches per hour")
        card.pack(fill="both", expand=True)
        self.peak = ctk.CTkLabel(card.body, text="", text_color=theme.MUTED, font=theme.body(11), height=14)
        self.peak.pack(anchor="w")
        self.chart = HourBars(card.body, height=330)
        self.chart.pack(fill="both", expand=True, pady=(6, 0))
        card = Card(right, "Most switched to")
        card.pack(fill="both", expand=True)
        help_icon(card.title.master, "Many switches with very short visits usually means checking out of habit.").pack(
            side="left", padx=8)
        self.none = ctk.CTkLabel(card.body, text="No switches yet.", text_color=theme.MUTED)
        self.entries = [TargetEntry(card.body) for _ in range(5)]

    def update_view(self, c: Context):
        summary = stats.switch_summary(c.events, c.now)
        title = "Switches today" if c.range == "Today" else "Switches yesterday" if c.range == "Yesterday" \
            else f"Switches ({c.range})"
        self.cards["Switches"].label.configure(text=title.upper())
        n = summary["count"]
        if c.range == "Today":
            avg = stats.average_daily_switches(c.db, c.today, until=c.now.time())
            if avg is None or abs(n - avg) < 1:
                self.cards["Switches"].set(str(n), "about usual for this time of day" if avg is not None else "")
            else:
                self.cards["Switches"].set(str(n), f"{abs(round(n - avg))} {'more' if n > avg else 'fewer'} than "
                                                   "usual by this time", theme.WARNING if n > avg else theme.SUCCESS)
        else:
            self.cards["Switches"].set(str(n))
        self.cards["Average time per visit"].set(stats.ms(summary["avg_visit"]) if n else "-")
        top = [appinfo.name_of(kind, name, c.items) for kind, name in summary["short_top"]]
        self.cards["Short visits (< 30 s)"].set(str(summary["short"]), f"mostly {' and '.join(top)}" if top else "")

        per_hour = summary["per_hour"]
        if per_hour:
            peak = max(per_hour, key=per_hour.get)
            self.peak.configure(text=f"Peak between {peak:02d}:00 and {peak + 1:02d}:00 - {per_hour[peak]} switches.")
        else:
            self.peak.configure(text="")
        self.chart.set(dict(per_hour))

        targets = summary["targets"][:5]
        if targets:
            self.none.pack_forget()
        else:
            self.none.pack(anchor="w", pady=8)
        for entry, ((kind, name), count, avg) in zip(self.entries, targets):
            text, color = STYLE[stats.visit_style(count, avg)]
            entry.stripe.configure(fg_color=color)
            entry.icon.configure(image=appinfo.icon_of(kind, name, c.items, 20))
            entry.name.configure(text=appinfo.name_of(kind, name, c.items))
            entry.style.configure(text=text, text_color=color)
            entry.count.configure(text=str(count))
            entry.avg.configure(text=f"avg {stats.ms(avg)}")
            entry.frame.pack(fill="x", pady=(8, 0))
        for entry in self.entries[len(targets):]:
            entry.frame.pack_forget()


# ---------------------------------------------------------------- Calendar

class CalendarView(ctk.CTkScrollableFrame):
    """A month as a calendar coloured by screen time (◀ / ▶ for other months), with the month's totals."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.month: date | None = None   # the 1st of the month shown
        card = Card(self, "")
        card.pack(fill="x", pady=(0, 12))
        head = card.title.master
        self.prev = ctk.CTkButton(head, text="‹", width=32, height=28, **theme.OUTLINE, command=lambda: self._step(-1))
        self.prev.pack(side="left", before=card.title)
        self.next = ctk.CTkButton(head, text="›", width=32, height=28, **theme.OUTLINE, command=lambda: self._step(1))
        self.next.pack(side="left", after=card.title, padx=(0, 8))
        card.title.pack_configure(padx=10)
        self.title = card.title
        help_icon(head, "Each day is coloured by its active screen time (darker = more); days over your daily goal "
                        "have a red dot. Hover a day for its time.").pack(side="left")
        self.cal = MonthCalendar(card.body)
        self.cal.pack(fill="x")
        self.stats = _stat_row(self, ["This month", "Average day", "Within goal", "Busiest day"])
        self.ctx = None

    def _step(self, months: int):
        m = self.month.month - 1 + months
        self.month = date(self.month.year + m // 12, m % 12 + 1, 1)
        self.update_view(self.ctx)

    def update_view(self, c: Context):
        self.ctx = c
        if self.month is None:
            self.month = c.today.replace(day=1)
        first = self.month
        nxt = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        rows = stats.activity(c.db, first, nxt)
        per_day = {date.fromisoformat(d): sec for d, sec in stats.per_day(rows).items()}
        goal = goal_seconds(c.db)
        self.title.configure(text=f"{MONTH_NAMES[first.month - 1]} {first.year}")
        self.next.configure(state="disabled" if nxt > c.today else "normal")
        self.cal.set(first, per_day, goal, c.today,
                     lambda d, sec: day_tip(d, sec) + ("  (over your goal)" if goal and sec > goal else ""))
        days = [d for d in per_day if d <= c.today]
        total = sum(per_day.values())
        past = max(1, min((c.today - first).days + 1, (nxt - first).days))
        self.stats["This month"].set(stats.hm(total))
        self.stats["Average day"].set(stats.hm(total / past), f"over {past} day{'s' * (past != 1)}")
        if goal:
            within = sum(1 for i in range(past) if per_day.get(first + timedelta(days=i), 0) <= goal)
            self.stats["Within goal"].set(f"{within} / {past}", f"goal {stats.hm(goal)} a day")
        else:
            self.stats["Within goal"].set("-", "no daily goal set (Settings)")
        if days:
            busiest = max(days, key=lambda d: per_day[d])
            self.stats["Busiest day"].set(stats.hm(per_day[busiest]), day_tip(busiest, per_day[busiest]).split("  ")[0])
        else:
            self.stats["Busiest day"].set("-")


# ---------------------------------------------------------------- page

class ScreenTimePage(ctk.CTkFrame):
    VIEWS = {"Overview": lambda m: OverviewView(m),
             "Apps": lambda m: TableView(m, "app", ["App", "Share of day", "Time", "Switches", "Category"]),
             "Websites": lambda m: TableView(m, "site", ["Website", "Share of browsing", "Time", "Visits", "Category"]),
             "Switches": lambda m: SwitchesView(m),
             "Calendar": lambda m: CalendarView(m)}

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        app_browser.preload()
        ctk.CTkLabel(self, text="Screen Time", font=theme.page_title()).pack(anchor="w", padx=30, pady=(12, 6))
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=30, pady=(0, 10))
        self.tab_bar = Segmented(bar, values=TABS, command=self.show_tab)
        self.tab_bar.pack(side="left")
        self.range_bar = Segmented(bar, values=list(stats.RANGES), command=lambda v: self.refresh())
        self.range_bar.pack(side="right")
        from gui.display_settings import ST_RANGE_KEY, ST_TAB_KEY, gear_button
        gear_button(bar, app).pack(side="right", padx=(8, 0), before=self.range_bar)
        self.range_bar.set(self.db.get_setting(ST_RANGE_KEY, "Today"))   # (as chosen in the ⚙ Display settings)
        start_tab = self.db.get_setting(ST_TAB_KEY, "Overview")
        self.holder = ctk.CTkFrame(self, fg_color="transparent")
        self.holder.pack(fill="both", expand=True, padx=(20, 12), pady=(0, 14))
        self.holder.grid_columnconfigure(0, weight=1)
        self.holder.grid_rowconfigure(0, weight=1)
        self.views: dict[str, ctk.CTkScrollableFrame] = {}
        self.tab_bar.set(start_tab if start_tab in TABS else "Overview")
        self._show_view(self.tab_bar.get())
        self.after(REFRESH_MS, self._auto_refresh)

    def _show_view(self, tab: str):
        if tab not in self.views:
            self.views[tab] = self.VIEWS[tab](self.holder)
        for name, view in self.views.items():
            if name != tab:
                view.grid_forget()
        self.views[tab].grid(row=0, column=0, sticky="nsew")
        if not hasattr(self, "curtain"):
            self.curtain = Curtain(self.holder)
        self.curtain.cover()

    def show_tab(self, tab: str):
        self.tab_bar.set(tab)
        self._show_view(tab)
        if tab == "Calendar":   # (a month at a time: the range buttons don't apply)
            self.range_bar.pack_forget()
        elif not self.range_bar.winfo_manager():
            self.range_bar.pack(side="right")
        self.refresh()

    def on_show(self):
        self.refresh()

    def _auto_refresh(self):
        if getattr(self.app, "current_page", None) == "Screen Time":
            self.refresh()
        self.after(REFRESH_MS, self._auto_refresh)

    def refresh(self):
        self.views[self.tab_bar.get()].update_view(Context(self))
