"""Network Log: which app connected to which site in the last hour (the service writes it; see service.py).

Table or graph, search, app filter, All / Allowed / Blocked, Windows' own and local-network traffic hidden by
default, live updates, CSV export. Click a row: block the site or the app (opens Add, filled in), copy the site
or app name, or show only that app. The table is one native Treeview (rows made of customtkinter widgets were ~11
windows each: slow to build and they tore while scrolling); it shows the newest PAGE rows ("Show more" for the rest)
and is only redrawn when something changed."""
import csv
import time
import tkinter as tk
from collections import Counter
from tkinter import ttk
from datetime import timedelta
from tkinter import filedialog

import customtkinter as ctk
from PIL import ImageTk

import search
from gui import appinfo, icons, theme
from gui.charts import MinuteBars
from gui.components import Card, Rows, Segmented, TabBar, page_head
from gui.target_picker import guess_name, popular_hosts
from trusted_time import now_from_db

LIVE_MS = 3000
PAGE = 100         # rows shown at first / added by "Show more"
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


class LogTable(ctk.CTkFrame):
    """The log as one ttk.Treeview in a card: App (icon + name) | Time | Site | Port | Status. Blocked visits in red.
    on_click(row index, x, y) when a row is clicked."""
    COLUMNS = (("time", "Time", 70, "w"), ("site", "Site", 320, "w"), ("rule", "Rule", 130, "w"),
               ("port", "Port", 60, "center"), ("status", "Status", 100, "w"))
    STYLE = "Log.Treeview"
    HINT = "Keeps the last hour · click a row to block it or copy it"

    def __init__(self, master, on_click):
        super().__init__(master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER, corner_radius=4)
        self.on_click = on_click
        self.scale = ctk.ScalingTracker.get_widget_scaling(self)
        self.images: dict[str, ImageTk.PhotoImage] = {}   # (Tk needs the references kept)
        self.mode = None
        self.hover = None
        # in-card footer (design 3f): the hint on the left, "Show more (N older)" on the right
        self.footer = ctk.CTkFrame(self, fg_color=theme.SURFACE2, corner_radius=0)
        self.footer.pack(side="bottom", fill="x", padx=1, pady=(0, 1))
        self.hint = ctk.CTkLabel(self.footer, text=self.HINT, text_color=theme.MUTED, font=theme.body(11))
        self.hint.pack(side="left", padx=14, pady=8)
        self.more = ctk.CTkButton(self.footer, text="", width=170, height=28, **theme.OUTLINE)
        self.tree = ttk.Treeview(self, columns=[c[0] for c in self.COLUMNS], style=self.STYLE, selectmode="none",
                                 show="tree headings")
        self.tree.heading("#0", text="APP", anchor="w")
        self.tree.column("#0", width=int(210 * self.scale), stretch=False)
        for key, text, width, anchor in self.COLUMNS:
            self.tree.heading(key, text=text.upper(), anchor=anchor)
            self.tree.column(key, width=int(width * self.scale), anchor=anchor, stretch=key == "site")
        self.scrollbar = ctk.CTkScrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y", padx=(0, 4), pady=8)
        self.tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=8)
        self.tree.bind("<ButtonRelease-1>", self._click)
        self.tree.bind("<Motion>", self._motion)
        self.tree.bind("<Leave>", lambda e: self._set_hover(None))
        self.restyle()

    def restyle(self):
        """Colours / fonts for the current theme (a Treeview isn't a customtkinter widget)."""
        mode = ctk.get_appearance_mode()
        if mode == self.mode:
            return
        self.mode = mode
        style, pick, px = ttk.Style(self), theme.pick, lambda n: -int(n * self.scale)
        style.theme_use("clam")   # (the only theme that lets a Treeview's colours be set)
        style.layout(self.STYLE, [("Treeview.treearea", {"sticky": "nswe"})])   # no frame around it
        style.configure(self.STYLE, background=pick(theme.SURFACE), fieldbackground=pick(theme.SURFACE),
                        foreground=pick(theme.TEXT), font=(theme.BODY, px(13)), rowheight=int(30 * self.scale),
                        borderwidth=0, indent=0)
        style.configure(f"{self.STYLE}.Heading", background=pick(theme.SURFACE), foreground=pick(theme.MUTED),
                        font=(theme.BODY_SEMI, px(10)), relief="flat", borderwidth=0, padding=(0, 4))
        style.map(f"{self.STYLE}.Heading", background=[("active", pick(theme.SURFACE))])
        # blocked rows: red text on a faint red tint (design 3f)
        self.tree.tag_configure("blocked", foreground=pick(theme.BLOCKED), background=pick(("#FBE5E3", "#2A1A19")))
        self.tree.tag_configure("hover", background=pick(theme.SURFACE2))

    def image(self, key: str, ctk_image) -> ImageTk.PhotoImage:
        if key not in self.images:
            size = int(16 * self.scale)
            self.images[key] = ImageTk.PhotoImage(ctk_image.cget("light_image").resize((size, size)), master=self)
        return self.images[key]

    def fill(self, rows: list[tuple]):
        """rows: (app text, image key, CTkImage, time, site, rule, port, status, blocked)."""
        self.hover = None
        self.tree.delete(*self.tree.get_children())
        for i, (app, key, img, when, site, rule, port, status, blocked) in enumerate(rows):
            self.tree.insert("", "end", iid=str(i), text=f"  {app}", image=self.image(key, img),
                             values=(when, site, rule, port, status), tags=("blocked",) if blocked else ())

    def set_more(self, older: int, empty: bool):
        """The footer: how many older rows "Show more" would add (0 = hide the button); the empty-state hint."""
        self.hint.configure(text="No connections in the last hour yet." if empty else self.HINT)
        if older:
            self.more.configure(text=f"Show more ({older} older)")
            self.more.pack(side="right", padx=12, pady=5)
        else:
            self.more.pack_forget()

    def _set_hover(self, iid):
        if iid == self.hover:
            return
        if self.hover and self.tree.exists(self.hover):
            self.tree.item(self.hover, tags=[t for t in self.tree.item(self.hover, "tags") if t != "hover"])
        if iid:
            self.tree.item(iid, tags=list(self.tree.item(iid, "tags")) + ["hover"])
        self.hover = iid

    def _motion(self, event):
        self._set_hover(self.tree.identify_row(event.y) or None)

    def _click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.on_click(int(iid), event.x_root, event.y_root)


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
        self.limit = PAGE                    # rows in the table ("Show more" adds PAGE)
        self.drawn = None                    # what the table / graph shows now (skip redrawing the same)
        self.refreshed = 0.0
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=30, pady=(12, 6))
        page_head(head, "Network Log").pack(side="left")
        self.view = TabBar(head, ["Table", "Graph"], command=lambda v: self._show_view())
        self.view.set("Table")
        self.view.pack(side="right", anchor="s")
        self.summary = ctk.CTkLabel(head, text="", text_color=theme.MUTED, font=theme.body(11))
        self.summary.pack(side="right", anchor="s", padx=(0, 16), pady=(0, 4))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=30, pady=(0, 6))
        self.status_filter = Segmented(bar, ["All", "Allowed", "Blocked"], command=lambda v: self._filtered())
        self.status_filter.set("All")
        self.status_filter.pack(side="left", padx=10)
        self.search = ctk.CTkEntry(bar, width=180, placeholder_text="Search site or app")
        self.search.pack(side="left")
        self.search.bind("<KeyRelease>", lambda e: self._filtered())
        ctk.CTkButton(bar, text="Export CSV", width=100, **theme.OUTLINE, command=self._export).pack(side="right")
        self.live = ctk.CTkSwitch(bar, text="Live", width=80)
        self.live.select()
        self.live.pack(side="right", padx=10)

        bar2 = ctk.CTkFrame(self, fg_color="transparent")
        bar2.pack(fill="x", padx=30, pady=(0, 8))
        self.apps = ctk.CTkOptionMenu(bar2, values=[ALL_APPS], width=200, command=self._app_chosen)
        self.apps.pack(side="left")
        self.windows = ctk.CTkSwitch(bar2, text="Windows' own connections", command=self._filtered)
        self.windows.pack(side="left", padx=16)
        self.local = ctk.CTkSwitch(bar2, text="Local network", command=self._filtered)
        self.local.pack(side="left")

        self.table_view = ctk.CTkFrame(self, fg_color="transparent")
        self.table = LogTable(self.table_view, self._row_clicked)
        self.table.pack(fill="both", expand=True)
        self.table.more.configure(command=self._show_more)
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")   # the graph view
        self.graph = ctk.CTkFrame(self.body, fg_color="transparent")
        self.graph.pack(fill="both", expand=True)
        card = Card(self.graph, "Connections per minute", note="last hour")
        card.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(card.note.master, text="■ minute with a blocked attempt", text_color=theme.DANGER,
                     font=theme.body(11)).pack(side="right", padx=(0, 12))
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

    def _load(self) -> list[dict]:
        """Connections + blocked visits of the last hour, newest first, filtered."""
        now = now_from_db(self.db)
        since = now - timedelta(hours=1)
        rows = [{**r, "blocked": False} for r in self.db.network_since(since.strftime("%Y-%m-%d %H:%M"))]
        rows += [{"minute": e["timestamp"][:16], "exe": "", "ip": "", "port": "", "domain": e["hostname"],
                  "count": 1, "windows": 0, "local": 0, "blocked": True, "name": e["display_name"],
                  "reason": e["reason"]}
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
            if text and search.score(text, f"{r['domain']} {r['ip']} {r['exe']} {self._app_name(r['exe'])}") is None:
                continue
            out.append(r)
        return out

    def _app_name(self, exe: str) -> str:
        return appinfo.name_of("app", exe, self.items) if exe else "Browser"

    def refresh(self):
        self.refreshed = time.monotonic()
        if self.table.mode != ctk.get_appearance_mode():   # light / dark switched
            self.table.restyle()
            self.drawn = None
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
        table = self.view.get() == "Table"
        drawn = (table, self.limit, [tuple(r.values()) for r in (self.shown[:self.limit] if table else self.shown)])
        if drawn == self.drawn:   # nothing new (live updates every 3 s): don't redraw
            return
        self.drawn = drawn
        if table:
            self._fill_table()
        else:
            self._fill_graph(per_app)

    def _fill_table(self):
        shown = self.shown[:self.limit]
        rows = []
        for r in shown:
            if r["exe"]:
                name, path = appinfo.app_name_path(r["exe"], self.items)
                app, key, img = name, f"app:{r['exe']}", icons.get_app(r["exe"], path, 16)
            else:
                app, key, img = "Blocked visit", f"site:{r['domain']}", icons.get(r["domain"], 16)
            site = (r["domain"] or r["ip"]) + (f"  ×{r['count']}" if r["count"] > 1 else "")
            rows.append((app, key, img, r["minute"][11:], site, self._rule_text(r), r["port"],
                         "✗ Blocked" if r["blocked"] else "✓ Allowed", r["blocked"]))
        self.table.fill(rows)
        self.table.set_more(len(self.shown) - len(shown), empty=not shown)

    @staticmethod
    def _rule_text(r: dict) -> str:
        """The "Rule" column: which rule blocked the visit (design 3f); allowed rows show a dash."""
        if not r["blocked"]:
            return "–"
        import alerts
        reason = r.get("reason") or ""
        base = alerts.base_reason(reason)
        return alerts.REASONS[base][0] if base in alerts.REASONS else (base or reason).replace("_", " ").capitalize()

    def _fill_graph(self, per_app: Counter):
        now = now_from_db(self.db)
        per_minute, blocked_minutes = Counter(), set()
        for r in self.shown:
            hm = r["minute"][11:]
            per_minute[hm] += r["count"]
            if r["blocked"]:
                blocked_minutes.add(hm)
        minutes = [(now - timedelta(minutes=59 - i)).strftime("%H:%M") for i in range(60)]
        self.chart.set([(m, per_minute.get(m, 0), m in blocked_minutes) for m in minutes])
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
        table = self.view.get() == "Table"
        (self.body if table else self.table_view).pack_forget()
        (self.table_view if table else self.body).pack(fill="both", expand=True, padx=(20, 12), pady=(0, 14))
        self.refresh()

    def _app_chosen(self, label: str):
        self.app_filter = None if label == ALL_APPS else self.exe_by_label.get(label)
        self._filtered()

    def _filtered(self):
        """A filter changed: start again from the newest rows."""
        self.limit = PAGE
        self.refresh()

    def _show_more(self):
        self.limit += PAGE
        self.refresh()

    def _live(self):
        if self.live.get() and getattr(self.app, "current_page", None) == "Network Log":
            self.refresh()
        self.after(LIVE_MS, self._live)

    def on_show(self):
        self.view.set("Table")   # back to the Table view, not the last one
        self._show_view()        # (this refreshes)

    def _row_clicked(self, index: int, x: int, y: int):
        if index < len(self.shown):
            self._menu(self.shown[index], x, y)

    def _menu(self, r: dict, x: int, y: int):
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
        menu.tk_popup(x, y)

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
