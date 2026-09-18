"""Network Log: which app connected to which site in the last hour (the service writes it; see service.py).

Table or graph, search, app filter, All / Allowed / Blocked, Windows' own and local-network traffic hidden by
default, live updates, CSV export. Click a row: block the site or the app (opens Add, filled in), copy the site
or app name, or show only that app."""
import csv
import tkinter as tk
from collections import Counter
from datetime import timedelta
from tkinter import filedialog

import customtkinter as ctk

from gui import appinfo, icons, theme
from gui.charts import MinuteBars
from gui.components import Card, Rows, Segmented
from gui.target_picker import guess_name, popular_hosts
from trusted_time import now_from_db

LIVE_MS = 3000
MAX_ROWS = 150
ALL_APPS = "All apps"
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}   # example.co.uk -> three labels


def site_to_block(host: str) -> str:
    """The domain to block for a host seen in the log (rr3.googlevideo.com -> googlevideo.com)."""
    known = popular_hosts(host)
    if known:
        return known[1][0]
    labels = host.removeprefix("www.").split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def _row(parent):
    f = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=4)
    for col, width in enumerate((60, 190, 0, 60, 90)):
        f.grid_columnconfigure(col, minsize=width, weight=1 if col == 2 else 0)
    f.time = ctk.CTkLabel(f, text="", text_color=theme.MUTED, font=theme.body(12), anchor="w")
    f.time.grid(row=0, column=0, sticky="w", padx=(8, 0))
    f.app = ctk.CTkLabel(f, text="", compound="left", anchor="w")
    f.app.grid(row=0, column=1, sticky="w")
    f.site = ctk.CTkLabel(f, text="", anchor="w")
    f.site.grid(row=0, column=2, sticky="w", padx=8)
    f.port = ctk.CTkLabel(f, text="", text_color=theme.MUTED, font=theme.body(12))
    f.port.grid(row=0, column=3, sticky="e", padx=8)
    f.status = ctk.CTkLabel(f, text="", font=theme.body(12))
    f.status.grid(row=0, column=4, sticky="e", padx=(0, 8))
    return f


def _count_row(parent):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    f.name = ctk.CTkLabel(f, text="", compound="left", height=22, anchor="w")
    f.name.pack(side="left")
    f.count = ctk.CTkLabel(f, text="", text_color=theme.MUTED, height=22)
    f.count.pack(side="right")
    return f


class NetworkPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app, self.db = app, app.db
        self.app_filter: str | None = None   # exe
        self.shown: list[dict] = []
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=30, pady=(12, 6))
        ctk.CTkLabel(head, text="Network Log", font=theme.page_title()).pack(side="left")
        self.summary = ctk.CTkLabel(head, text="", text_color=theme.MUTED, font=theme.body(11))
        self.summary.pack(side="right", anchor="s", pady=(0, 4))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=30, pady=(0, 6))
        self.view = Segmented(bar, ["Table", "Graph"], command=lambda v: self._show_view())
        self.view.set("Table")
        self.view.pack(side="left")
        self.status_filter = Segmented(bar, ["All", "Allowed", "Blocked"], command=lambda v: self.refresh())
        self.status_filter.set("All")
        self.status_filter.pack(side="left", padx=10)
        self.search = ctk.CTkEntry(bar, width=180, placeholder_text="Search site or app")
        self.search.pack(side="left")
        self.search.bind("<KeyRelease>", lambda e: self.refresh())
        ctk.CTkButton(bar, text="Export CSV", width=100, **theme.OUTLINE, command=self._export).pack(side="right")
        self.live = ctk.CTkSwitch(bar, text="Live", width=60)
        self.live.select()
        self.live.pack(side="right", padx=10)

        bar2 = ctk.CTkFrame(self, fg_color="transparent")
        bar2.pack(fill="x", padx=30, pady=(0, 8))
        self.apps = ctk.CTkOptionMenu(bar2, values=[ALL_APPS], width=200, command=self._app_chosen)
        self.apps.pack(side="left")
        self.windows = ctk.CTkSwitch(bar2, text="Windows' own connections", command=self.refresh)
        self.windows.pack(side="left", padx=16)
        self.local = ctk.CTkSwitch(bar2, text="Local network", command=self.refresh)
        self.local.pack(side="left")
        ctk.CTkLabel(bar2, text="Keeps the last hour. Click a row to block or copy it.", text_color=theme.MUTED,
                     font=theme.body(11)).pack(side="right")

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=(20, 12), pady=(0, 14))
        self.table = Card(self.body)
        head_row = _row(self.table.body)
        head_row.pack(fill="x", pady=(4, 2))
        for widget, text in ((head_row.time, "Time"), (head_row.app, "App"), (head_row.site, "Site"),
                             (head_row.port, "Port"), (head_row.status, "Status")):
            widget.configure(text=text.upper(), font=theme.eyebrow(), text_color=theme.MUTED)
        self.rows = Rows(self.table.body, self._make_row, "No connections in the last hour yet.",
                         {"fill": "x", "pady": 1})
        self.graph = ctk.CTkFrame(self.body, fg_color="transparent")
        card = Card(self.graph, "Connections per minute", note="last hour")
        card.pack(fill="x", pady=(0, 12))
        self.chart = MinuteBars(card.body)
        self.chart.pack(fill="x")
        cols = ctk.CTkFrame(self.graph, fg_color="transparent")
        cols.pack(fill="x")
        cols.grid_columnconfigure((0, 1), weight=1, uniform="c")
        left, right = Card(cols, "Top apps"), Card(cols, "Top sites")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.top_apps = Rows(left.body, _count_row, "Nothing yet.")
        self.top_sites = Rows(right.body, _count_row, "Nothing yet.")
        self._show_view()
        self.after(LIVE_MS, self._live)

    # ---------- data ----------

    def _make_row(self, parent):
        row = _row(parent)
        for w in (row, row.time, row.app, row.site, row.port, row.status):
            w.bind("<Button-1>", lambda e, r=row: self._menu(r))
            w.bind("<Enter>", lambda e, r=row: r.configure(fg_color=theme.SURFACE2))
            w.bind("<Leave>", lambda e, r=row: r.configure(fg_color="transparent"))
        return row

    def _load(self) -> list[dict]:
        """Connections + blocked visits of the last hour, newest first, filtered."""
        now = now_from_db(self.db)
        since = now - timedelta(hours=1)
        rows = [{**r, "blocked": False} for r in self.db.network_since(since.strftime("%Y-%m-%d %H:%M"))]
        rows += [{"minute": e["timestamp"][:16], "exe": "", "ip": "", "port": "", "domain": e["hostname"],
                  "count": 1, "windows": 0, "local": 0, "blocked": True, "name": e["display_name"]}
                 for e in self.db.block_events_since(since) if not e["hostname"].endswith(".exe")]
        rows.sort(key=lambda r: r["minute"], reverse=True)
        self.all_rows = rows
        text = self.search.get().strip().lower()
        status = self.status_filter.get()
        out = []
        for r in rows:
            if (r["windows"] and not self.windows.get()) or (r["local"] and not self.local.get()):
                continue
            if self.app_filter and r["exe"] != self.app_filter:
                continue
            if status == "Allowed" and r["blocked"] or status == "Blocked" and not r["blocked"]:
                continue
            if text and text not in f"{r['domain']} {r['ip']} {r['exe']} {self._app_name(r['exe'])}".lower():
                continue
            out.append(r)
        return out

    def _app_name(self, exe: str) -> str:
        return appinfo.name_of("app", exe, self.items) if exe else "Browser"

    def refresh(self):
        self.items = self.db.list_items()
        self.shown = self._load()
        exes = sorted({r["exe"] for r in self.all_rows if r["exe"] and (self.windows.get() or not r["windows"])},
                      key=lambda e: self._app_name(e).lower())
        self.exe_by_label = {f"{self._app_name(e)}": e for e in exes}
        self.apps.configure(values=[ALL_APPS] + list(self.exe_by_label))
        self.apps.set(self._app_name(self.app_filter) if self.app_filter else ALL_APPS)
        total = sum(r["count"] for r in self.shown)
        blocked = sum(1 for r in self.shown if r["blocked"])
        sites = {r["domain"] or r["ip"] for r in self.shown}
        per_app = Counter()
        for r in self.shown:
            if r["exe"]:
                per_app[r["exe"]] += r["count"]
        top = f" · top: {self._app_name(per_app.most_common(1)[0][0])}" if per_app else ""
        self.summary.configure(text=f"Last hour: {total} connections · {blocked} blocked · {len(sites)} sites · "
                                    f"{len(per_app)} apps{top}")
        if self.view.get() == "Table":
            self._fill_table()
        else:
            self._fill_graph(per_app)

    def _fill_table(self):
        shown = self.shown[:MAX_ROWS]
        for row, r in zip(self.rows.take(len(shown)), shown):
            row.data = r
            row.time.configure(text=r["minute"][11:])
            if r["exe"]:
                name, path = appinfo.app_name_path(r["exe"], self.items)
                row.app.configure(text=f"  {name}", image=icons.get_app(r["exe"], path, 16))
            else:
                row.app.configure(text="  Blocked visit", image=icons.get(r["domain"], 16))
            site = r["domain"] or r["ip"]
            row.site.configure(text=site + (f"  ×{r['count']}" if r["count"] > 1 else ""))
            row.port.configure(text=str(r["port"]))
            row.status.configure(text="✗ Blocked" if r["blocked"] else "✓ Allowed",
                                 text_color=theme.BLOCKED if r["blocked"] else theme.ALLOWED)

    def _fill_graph(self, per_app: Counter):
        now = now_from_db(self.db)
        per_minute = Counter()
        for r in self.shown:
            per_minute[r["minute"][11:]] += r["count"]
        minutes = [(now - timedelta(minutes=59 - i)).strftime("%H:%M") for i in range(60)]
        self.chart.set([(m, per_minute.get(m, 0)) for m in minutes])
        top_apps = per_app.most_common(8)
        for row, (exe, n) in zip(self.top_apps.take(len(top_apps)), top_apps):
            name, path = appinfo.app_name_path(exe, self.items)
            row.name.configure(text=f"  {name}", image=icons.get_app(exe, path, 16))
            row.count.configure(text=str(n))
        per_site = Counter()
        for r in self.shown:
            if r["domain"]:
                per_site[r["domain"]] += r["count"]
        top_sites = per_site.most_common(8)
        for row, (site, n) in zip(self.top_sites.take(len(top_sites)), top_sites):
            row.name.configure(text=f"  {site}", image=icons.get(site, 16))
            row.count.configure(text=str(n))

    # ---------- actions ----------

    def _show_view(self):
        (self.graph if self.view.get() == "Table" else self.table).pack_forget()
        (self.table if self.view.get() == "Table" else self.graph).pack(fill="both", expand=True)
        self.refresh()

    def _app_chosen(self, label: str):
        self.app_filter = None if label == ALL_APPS else self.exe_by_label.get(label)
        self.refresh()

    def _live(self):
        if self.live.get() and getattr(self.app, "current_page", None) == "Network Log":
            self.refresh()
        self.after(LIVE_MS, self._live)

    def on_show(self):
        self.refresh()

    def _menu(self, row):
        r = getattr(row, "data", None)
        if not r:
            return
        menu = tk.Menu(self, tearoff=False)
        host = r["domain"]
        if host:
            menu.add_command(label=f"Block site  {site_to_block(host)}...", command=lambda: self._block_site(host))
        if r["exe"] and not r["windows"]:
            menu.add_command(label=f"Block app  {self._app_name(r['exe'])}...",
                             command=lambda: self._block_app(r["exe"]))
        menu.add_separator()
        if host or r["ip"]:
            menu.add_command(label="Copy site", command=lambda: self._copy(host or r["ip"]))
        if r["exe"]:
            menu.add_command(label="Copy app name", command=lambda: self._copy(self._app_name(r["exe"])))
            if self.app_filter:
                menu.add_command(label="Show all apps", command=lambda: self._app_chosen(ALL_APPS))
            else:
                menu.add_command(label=f"Show only {self._app_name(r['exe'])}",
                                 command=lambda: self._app_chosen(self._app_name(r["exe"])))
        menu.tk_popup(row.winfo_pointerx(), row.winfo_pointery())

    def _copy(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _block_site(self, host: str):
        domain = site_to_block(host)
        known = popular_hosts(domain)
        self.app.show_page("Blocking")
        self.app.pages["Blocking"].add_prefilled(site=(known[0] if known else guess_name(domain), domain))

    def _block_app(self, exe: str):
        name, path = appinfo.app_name_path(exe, self.items)
        self.app.show_page("Blocking")
        self.app.pages["Blocking"].add_prefilled(app={"name": name, "exe": exe, "path": path})

    def _export(self):
        path = filedialog.asksaveasfilename(parent=self, title="Export network log", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")], initialfile="network-log.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "app", "exe", "site", "ip", "port", "connections", "status"])
            for r in self.shown:
                w.writerow([r["minute"], self._app_name(r["exe"]), r["exe"], r["domain"], r["ip"], r["port"],
                            r["count"], "blocked" if r["blocked"] else "allowed"])
