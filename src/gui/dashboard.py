"""Dashboard: "how am I doing today?" - stat cards, limits in progress, today's timeline, the last 7 days,
what's coming up, blocked visits and a quick glance. Refreshed when shown and every 30 s while shown."""
import ctypes
import time
from collections import Counter
from datetime import datetime, timedelta

import customtkinter as ctk

import modes
import stats
from gui import app_browser, appinfo, categories, icons, theme
from gui.charts import DayBars, TimelineBar
from gui.components import Card, ProgressLine, Rows, StatCard, eyebrow
from rules import (DAY_NAMES, OPEN_LIMIT_FIELDS, PERIOD_WORDS, TIME_LIMIT_FIELDS, effective_rules, item_block,
                   limits, next_block, opening_bucket, time_bucket)
from trusted_time import now_from_db

REFRESH_MS = 30_000
FRESH_SEC = 10      # showing the page again within this time doesn't rebuild it
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
GOAL_KEY = "stats.goal_hours"      # daily screen-time goal, "0" = off
DEFAULT_GOAL_HOURS = "5"
TASK_NAME = "Lockdown Enforcer"
BANNER_BG = ("#FBEEE8", "#2A1E19")


def start_service():
    """Start the enforcement service (Windows asks for admin rights)."""
    ctypes.windll.shell32.ShellExecuteW(None, "runas", "schtasks.exe", f'/Run /TN "{TASK_NAME}"', None, 0)


def goal_seconds(db) -> float | None:
    hours = float(db.get_setting(GOAL_KEY, DEFAULT_GOAL_HOURS) or 0)
    return hours * 3600 or None


def _legend_entry(parent):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    f.square = ctk.CTkFrame(f, width=9, height=9, corner_radius=2)
    f.square.pack(side="left", padx=(8, 4))
    f.label = ctk.CTkLabel(f, text="", text_color=theme.MUTED, font=theme.body(11))
    f.label.pack(side="left")
    return f


def _limit_row(parent):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    line = ctk.CTkFrame(f, fg_color="transparent")
    line.pack(fill="x", pady=(2, 0))
    f.name = ctk.CTkLabel(line, text="", compound="left", height=20)
    f.name.pack(side="left")
    f.left = ctk.CTkLabel(line, text="", font=theme.semi(12), height=20)
    f.left.pack(side="right")
    f.text = ctk.CTkLabel(line, text="", text_color=theme.MUTED, font=theme.body(12), height=20)
    f.text.pack(side="right", padx=12)
    f.bar = ProgressLine(f)
    f.bar.pack(fill="x", pady=(2, 6))
    return f


def _coming_row(parent):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    f.when = ctk.CTkLabel(f, text="", text_color=theme.ACCENT, font=theme.semi(13), width=84, anchor="nw")
    f.when.pack(side="left", anchor="n")
    texts = ctk.CTkFrame(f, fg_color="transparent")
    texts.pack(side="left", fill="x", expand=True)
    f.title = ctk.CTkLabel(texts, text="", anchor="w", height=18)
    f.title.pack(anchor="w")
    f.sub = ctk.CTkLabel(texts, text="", text_color=theme.MUTED, font=theme.body(11), anchor="w", height=14,
                         wraplength=260, justify="left")
    f.sub.pack(anchor="w")
    return f


def _visit_row(parent):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    f.name = ctk.CTkLabel(f, text="", compound="left", height=20)
    f.name.pack(side="left")
    f.count = ctk.CTkLabel(f, text="", text_color=theme.MUTED, font=theme.body(11), height=20)
    f.count.pack(side="right")
    return f


def _glance_entry(parent):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    f.label = eyebrow(f, "")
    f.label.pack(anchor="w", pady=(4, 0))
    line = ctk.CTkFrame(f, fg_color="transparent")
    line.pack(anchor="w")
    f.main = ctk.CTkLabel(line, text="", font=theme.semi(13), height=18)
    f.main.pack(side="left")
    f.extra = ctk.CTkLabel(line, text="", text_color=theme.MUTED, font=theme.body(11), height=18)
    f.extra.pack(side="left")
    return f


def when_text(when, now) -> str:
    return f"{when:%H:%M}" if when.date() == now.date() else f"{DAY_NAMES[when.weekday()][:3]} {when:%H:%M}"


class DashboardPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        app_browser.preload()   # app names / icons
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=30, pady=(12, 6))
        ctk.CTkLabel(head, text="Dashboard", font=theme.page_title()).pack(side="left")
        self.date = ctk.CTkLabel(head, text="", text_color=theme.MUTED, font=theme.body(11))
        self.date.pack(side="right", anchor="s", pady=(0, 4))
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=(20, 12), pady=(0, 14))
        self._build()
        self.after(REFRESH_MS, self._auto_refresh)
        self.last_refresh = 0.0

    # ---------- layout ----------

    def _build(self):
        b = self.body
        self.banner = ctk.CTkFrame(b, fg_color=BANNER_BG, border_width=1, border_color=theme.WARNING, corner_radius=6)
        ctk.CTkLabel(self.banner, text="", image=theme.icon("alert-triangle", theme.WARNING, 18)).pack(
            side="left", padx=(14, 8), pady=10)
        texts = ctk.CTkFrame(self.banner, fg_color="transparent")
        texts.pack(side="left", pady=8)
        ctk.CTkLabel(texts, text="Blocking isn't being enforced right now", font=theme.semi(13), height=18).pack(
            anchor="w")
        ctk.CTkLabel(texts, text="The Lockdown service isn't running. Screen time is still being recorded.",
                     text_color=theme.MUTED, font=theme.body(11), height=16).pack(anchor="w")
        ctk.CTkButton(self.banner, text="Start service", width=120, command=start_service).pack(side="right", padx=14)

        self.main = ctk.CTkFrame(b, fg_color="transparent")
        self.main.pack(fill="both", expand=True)
        cards = ctk.CTkFrame(self.main, fg_color="transparent")
        cards.pack(fill="x", pady=(0, 12))
        self.stat = {}
        for i, label in enumerate(["Screen time today", "Blocked now", "Switches today", "Time saved"]):
            cards.grid_columnconfigure(i, weight=1, uniform="stat")
            self.stat[label] = StatCard(cards, label)
            self.stat[label].grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0 if i == 3 else 6))

        grid = ctk.CTkFrame(self.main, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        grid.grid_columnconfigure(0, weight=3, uniform="col")
        grid.grid_columnconfigure(1, weight=2, uniform="col")
        left = ctk.CTkFrame(grid, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right = ctk.CTkFrame(grid, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self.limits = Card(left, "Limits today")
        self.limits.pack(fill="x", pady=(0, 12))
        self.limit_rows = Rows(self.limits.body, _limit_row, "No time or opening limits set.")
        self.today = Card(left, "Today")
        self.today.pack(fill="x", pady=(0, 12))
        legend = ctk.CTkFrame(self.today.note.master, fg_color="transparent")
        legend.pack(side="right")
        self.today.note.destroy()
        self.legend = Rows(legend, _legend_entry, item_pack={"side": "left"})
        self.timeline = TimelineBar(self.today.body)
        self.timeline.pack(fill="x")
        self.week = Card(left, "Last 7 days")
        self.week.pack(fill="x")
        self.bars = DayBars(self.week.body, height=150)
        self.bars.pack(fill="x")

        self.coming = Card(right, "Coming up")
        self.coming.pack(fill="x", pady=(0, 12))
        self.coming_rows = Rows(self.coming.body, _coming_row, "Nothing in the next 24 hours.",
                                {"fill": "x", "pady": 3})
        self.visits = Card(right, "Blocked visits today")
        self.visits.pack(fill="x", pady=(0, 12))
        self.visits.note.configure(font=theme.numeral(22), text_color=theme.ACCENT)
        self.visit_rows = Rows(self.visits.body, _visit_row, "No blocked visits today.", {"fill": "x", "pady": 1})
        self.glance = Card(right, "At a glance")
        self.glance.pack(fill="x")
        self.glance_rows = Rows(self.glance.body, _glance_entry, "Not enough data yet.", {"anchor": "w"})

        self.empty = Card(b)
        inner = ctk.CTkFrame(self.empty.body, fg_color="transparent")
        inner.pack(expand=True, pady=150)
        ctk.CTkLabel(inner, text="", image=theme.icon("hourglass", theme.ACCENT, 40)).pack()
        ctk.CTkLabel(inner, text="Tracking started", font=theme.body(22, "bold")).pack(pady=(8, 4))
        ctk.CTkLabel(inner, text="Your first numbers show up here in a few minutes, and the 7-day chart fills in\n"
                                 "as the week goes on. Nothing to do in the meantime.", text_color=theme.MUTED).pack()
        buttons = ctk.CTkFrame(inner, fg_color="transparent")
        buttons.pack(pady=14)
        ctk.CTkButton(buttons, text="Set up your first block", command=self._first_block).pack(side="left", padx=4)
        ctk.CTkButton(buttons, text="Choose categories for your apps", **theme.OUTLINE,
                      command=self._categories).pack(side="left", padx=4)

    def _first_block(self):
        self.app.show_page("Blocking")
        self.app.pages["Blocking"].show_tab("Add")

    def _categories(self):
        self.app.show_page("Screen Time")
        self.app.pages["Screen Time"].show_tab("Apps")

    # ---------- data ----------

    def on_show(self):
        if time.monotonic() - self.last_refresh > FRESH_SEC:
            self.refresh()

    def _auto_refresh(self):
        if getattr(self.app, "current_page", None) == "Dashboard":
            self.refresh()
        self.after(REFRESH_MS, self._auto_refresh)

    def refresh(self):
        self.last_refresh = time.monotonic()
        db = self.db
        now = now_from_db(db)
        today = now.date()
        first = stats.first_activity(db)
        self.date.configure(text=f"{DAY_NAMES[today.weekday()]}, {today.day} {MONTHS[today.month - 1]}")
        for w in (self.banner, self.main, self.empty):
            w.pack_forget()
        if not self.app.service_running:
            self.banner.pack(fill="x", pady=(0, 12))
        if first is None:
            self.empty.pack(fill="both", expand=True)
            return
        self.main.pack(fill="both", expand=True)

        items, groups = db.list_items(), db.list_groups()
        usage = db.usage_lookup(now)
        saved = db.categories()
        cats = categories.load(db)
        colors = categories.colors_of(cats)

        def category(exe, site):
            cat = stats.category_of(*(("site", site) if site else ("app", exe)), saved, items)
            return cat if cat in colors else "neutral"
        rows = stats.activity(db, today, today + timedelta(days=1))
        events = stats.switches(db, today, today + timedelta(days=1))
        if rows:
            self.date.configure(text=self.date.cget("text") + f"  ·  tracking since {rows[0]['minute'][11:]}")
        state = modes.active(db, now)
        if state:
            end = state["phase"][1] if state["phase"] else state["until"]
            what = f"{state['mode']['name']} mode" + (f", {state['phase'][0]}" if state["phase"] else "")
            self.date.configure(text=self.date.cget("text") + f"  ·  {what}" + (f" until {end:%H:%M}" if end else ""))

        self._stat_cards(now, rows, events, items, groups, usage)
        self._limits(now, items, groups, usage)
        start_hour = min(6, int(rows[0]["minute"][11:13])) if rows else 6
        segments = stats.timeline(rows, today, category)
        self.timeline.set(segments, start_hour, {**colors, "idle": theme.TRACK},
                          {**categories.names_of(cats), "idle": "Idle"})
        used = {kind for _, _, kind in segments}
        shown = [c for c in cats if c["key"] in used or c["builtin"]] + [{"name": "Idle", "color": theme.TRACK}]
        for entry, c in zip(self.legend.take(len(shown)), shown):
            entry.square.configure(fg_color=c["color"])
            entry.label.configure(text=c["name"])
        self._week(now)
        self._coming(now, items, groups, usage)
        self._visits(today, items)
        self._glance(now, rows, events, items)

    def _stat_cards(self, now, rows, events, items, groups, usage):
        db, today = self.db, now.date()
        active = stats.totals(rows)[0]
        y = today - timedelta(days=1)
        y_rows = [r for r in stats.activity(db, y, today) if r["minute"][11:] <= f"{now:%H:%M}"]
        diff = active - stats.totals(y_rows)[0]
        if abs(diff) < 60:
            delta, color = "about the same as yesterday", theme.MUTED
        else:
            delta = f"{stats.hm(abs(diff))} {'more' if diff > 0 else 'less'} than yesterday"
            color = theme.WARNING if diff > 0 else theme.SUCCESS
        self.stat["Screen time today"].set(stats.hm(active), delta, color)

        blocked = [i for i in items if item_block(effective_rules(i, groups), now, usage)]
        if not self.app.service_running:
            self.stat["Blocked now"].set(f"0 of {len(items)}", "Service stopped - not enforced", theme.DANGER)
        else:
            upcoming = self._upcoming(now, items, groups, usage)
            starts = [e for e in upcoming if e["kind"] == "start"]
            note = f"Next: {starts[0]['title']} at {when_text(starts[0]['when'], now)}" if starts \
                else "Nothing else coming up today"
            self.stat["Blocked now"].set(f"{len(blocked)} of {len(items)}", note)

        avg = stats.average_daily_switches(db, today, until=now.time())
        n = len(events)
        if avg is None or abs(n - avg) < 1:
            delta, color = ("no history yet" if avg is None else "about usual for this time of day"), theme.MUTED
        else:
            delta = f"{abs(round(n - avg))} {'more' if n > avg else 'fewer'} than usual by this time"
            color = theme.WARNING if n > avg else theme.SUCCESS
        self.stat["Switches today"].set(str(n), delta, color)

        blocked_today = stats.blocked_events(db, today)
        history = stats.switches(db, today - timedelta(days=30), today + timedelta(days=1))
        saved_sec = stats.time_saved(blocked_today, items, [], history, now)
        self.stat["Time saved"].set(stats.hm(saved_sec),
                                    f"{len(blocked_today)} blocked visit{'s' * (len(blocked_today) != 1)} turned away")

    def _limits(self, now, items, groups, usage):
        clock = usage.clock
        seen, entries = set(), []
        for item in items:
            for r in effective_rules(item, groups):
                if r["rule_type"] not in ("time_limit", "switch_limit") or (r["usage_owner"], r["rule_type"]) in seen:
                    continue
                seen.add((r["usage_owner"], r["rule_type"]))
                is_time = r["rule_type"] == "time_limit"
                group = r["usage_owner"].startswith("group:")
                best = None
                for period, limit in limits(r, TIME_LIMIT_FIELDS if is_time else OPEN_LIMIT_FIELDS).items():
                    bucket = time_bucket(period, now, clock) if is_time else opening_bucket(r, period, now, clock)
                    used = usage(r["usage_owner"], bucket)
                    frac = used / (limit * 60 if is_time else max(limit, 1))
                    if best is None or frac > best[0]:
                        best = (frac, period, used, limit)
                if best:
                    name = f"{r['group']['name']} (group)" if group else item["display_name"]
                    icon = icons.get(r["group"]["name"], 16) if group else icons.for_item(item, 16)
                    entries.append((name, icon, is_time, *best))
        self.limits.note.configure(text=f"{len(entries)} active")
        entries.sort(key=lambda e: -e[3])
        for row, (name, icon, is_time, frac, period, used, limit) in zip(self.limit_rows.take(len(entries)),
                                                                           entries):
            color = theme.SUCCESS if frac < 0.6 else theme.WARNING if frac < 0.85 else theme.DANGER
            when = "" if period == "day" else f" {PERIOD_WORDS[period]}"
            if is_time:
                text, left = f"{stats.hm(used)} of {stats.hm(limit * 60)}{when}", limit * 60 - used
                left_text = f"{stats.hm(left)} left" if left > 0 else "limit reached"
            else:
                text = f"{used} opens of {limit} {PERIOD_WORDS[period]}"
                left_text = f"{limit - used} left" if used < limit else "limit reached"
                name += " - opens"
            row.name.configure(text=f"  {name}", image=icon)
            row.left.configure(text=left_text, text_color=color)
            row.text.configure(text=text)
            row.bar.set(frac, color)

    def _week(self, now):
        today = now.date()
        start = today - timedelta(days=6)
        per_day = stats.per_day(stats.activity(self.db, start, today + timedelta(days=1)))
        since = datetime.combine(start, datetime.min.time())
        unlocks = Counter(u["started"].date() for u in self.db.unlocks_since(since))
        days = []
        for d in (start + timedelta(days=i) for i in range(7)):
            sec = per_day.get(d.isoformat(), 0)
            days.append((DAY_NAMES[d.weekday()][:3], sec, d == today,
                         f"{DAY_NAMES[d.weekday()]} {d.day} {MONTHS[d.month - 1]}  {stats.hm(sec)}", unlocks.get(d, 0)))
        goal = goal_seconds(self.db)
        self.week.note.configure(text=f"- - daily goal {goal / 3600:g} h" if goal else "")
        self.bars.set(days, goal)

    def _upcoming(self, now, items, groups, usage) -> list[dict]:
        """Blocks starting / ending within 24 h, and time limits about to run out (items in use)."""
        horizon = now + timedelta(hours=24)
        merged: dict[tuple, dict] = {}
        for item in items:
            rules = effective_rules(item, groups)
            block = item_block(rules, now, usage)
            if block:
                until, rule = block[1], block[2]
                if until and until <= horizon:
                    group = rule.get("group")
                    key = ("end", until, group["id"] if group else f"i{item['id']}")
                    title = f"{group['name']} ends" if group else f"{item['display_name']} allowed again"
                    merged.setdefault(key, {"kind": "end", "when": until, "title": title, "names": []})["names"].append(
                        item["display_name"])
                continue
            nb = next_block(rules, now, usage)
            if nb and nb[0] <= horizon:
                when, rule = nb
                key = ("start", when, rule["group"]["id"] if rule.get("group") else f"i{item['id']}")
                title = f"{rule['group']['name']} starts" if rule.get("group") else f"{item['display_name']} blocked"
                merged.setdefault(key, {"kind": "start", "when": when, "title": title, "names": []})["names"].append(
                    item["display_name"])
            for r in rules:
                if r["rule_type"] == "time_limit" and r.get("daily_limit_min"):
                    left = r["daily_limit_min"] * 60 - usage(r["usage_owner"], time_bucket("day", now, usage.clock))
                    if 0 < left <= 30 * 60:
                        merged[("limit", item["id"])] = {"kind": "limit", "when": None, "left": left,
                                                         "title": f"{item['display_name']} limit will be reached",
                                                         "names": []}
        return sorted(merged.values(), key=lambda e: (e["when"] is not None, e["when"] or now))

    def _coming(self, now, items, groups, usage):
        upcoming = self._upcoming(now, items, groups, usage)[:4]
        for row, e in zip(self.coming_rows.take(len(upcoming)), upcoming):
            row.when.configure(text="Today" if e["when"] is None else when_text(e["when"], now))
            row.title.configure(text=e["title"])
            sub = f"about {round(e['left'] / 60)} minutes of use left" if e["kind"] == "limit" else \
                " · ".join(e["names"]) if len(e["names"]) > 1 else ""
            row.sub.configure(text=sub)
            if sub:
                row.sub.pack(anchor="w")
            else:
                row.sub.pack_forget()

    def _visits(self, today, items):
        events = stats.blocked_events(self.db, today)
        self.visits.note.configure(text=str(len(events)) if events else "")
        by_id = {i["id"]: i for i in items}
        top = Counter((e["item_id"], e["name"]) for e in events).most_common(4)
        for row, ((item_id, name), count) in zip(self.visit_rows.take(len(top)), top):
            item = by_id.get(item_id)
            row.name.configure(text=f"  {name}", image=icons.for_item(item, 16) if item else icons.get(name, 16))
            row.count.configure(text=f"{count} attempt{'s' * (count != 1)}")

    def _glance(self, now, rows, events, items):
        entries = []
        apps = stats.per_app(rows)
        if apps:
            exe, sec = apps.most_common(1)[0]
            entries.append(("Top app today", appinfo.name_of("app", exe, items), stats.hm(sec)))
        summary = stats.switch_summary(events, now)
        if summary["targets"]:
            (kind, name), count, avg = summary["targets"][0]
            entries.append(("Most switched to", appinfo.name_of(kind, name, items),
                            f"{count} switches · avg {stats.ms(avg)} per visit"))
        hours = sorted(summary["per_hour"])
        if len(hours) >= 2:
            span = range(hours[0], hours[-1] + 1)
            quiet = min(span, key=lambda h: summary["per_hour"].get(h, 0))
            n = summary["per_hour"].get(quiet, 0)
            entries.append(("Quietest hour", f"{quiet:02d}:00 - {quiet + 1:02d}:00", f"{n} switch{'es' * (n != 1)}"))
        for row, (label, main, extra) in zip(self.glance_rows.take(len(entries)), entries):
            row.label.configure(text=label.upper())
            row.main.configure(text=main)
            row.extra.configure(text=f"  {extra}")
