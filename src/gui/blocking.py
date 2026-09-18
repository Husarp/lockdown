"""Blocking page: add sites (with stacked rules), popular quick-list, blocked items list."""
from datetime import datetime, timedelta

import customtkinter as ctk

from blocker.hosts import normalize_host
from importer.popular import POPULAR_SITES
from rules import DAY_NAMES, TIME_FMT, describe_rule, item_block, make_schedule

SUB_TABS = ["All", "By Hours", "By Limit", "By Switches", "Permanent", "Temporary"]
TAB_RULE = {"By Hours": "scheduled", "Permanent": "permanent", "Temporary": "temporary"}
NOT_YET = {"By Limit": "Daily time limits are coming soon.", "By Switches": "Switch limits are coming in a later phase."}
DURATIONS = {"15 min": 15, "30 min": 30, "1 hour": 60, "2 hours": 120, "3 hours": 180,
             "4 hours": 240, "8 hours": 480, "24 hours": 1440}
ALERTS = {"Default": None, "On": "on", "Off": "off"}
QUICK_COLUMNS = 4
REFRESH_MS = 30_000
MUTED = "gray60"
ERROR = "#f85149"


def _popular_entry(host: str) -> tuple[str, list[str]] | None:
    for sites in POPULAR_SITES.values():
        for name, hostnames in sites.items():
            if host in hostnames:
                return name, hostnames
    return None


def _guess_name(host: str) -> str:
    entry = _popular_entry(host)
    return entry[0] if entry else host.split(".")[-2].capitalize()


class BlockingPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.db = app.db
        self.name_edited = False
        self.quick_vars: dict[str, ctk.BooleanVar] = {}

        ctk.CTkLabel(self, text="Blocking", font=ctk.CTkFont(size=24, weight="bold")).pack(
            anchor="w", padx=30, pady=(24, 8))
        self.tabs = ctk.CTkSegmentedButton(self, values=SUB_TABS, command=self._show_tab)
        self.tabs.pack(anchor="w", padx=30, pady=(0, 12))

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.add_box = self._build_add_box()
        self.quick_box = self._build_quick_box()
        self.list_title = ctk.CTkLabel(self.body, font=ctk.CTkFont(size=16, weight="bold"))
        self.list_box = ctk.CTkFrame(self.body)
        self.placeholder = ctk.CTkLabel(self.body, text_color=MUTED)

        self.tabs.set("All")
        self._show_tab("All")
        self.after(REFRESH_MS, self._auto_refresh)

    # ---------- layout ----------

    def _section(self, title: str) -> ctk.CTkFrame:
        box = ctk.CTkFrame(self.body)
        ctk.CTkLabel(box, text=title, font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, columnspan=QUICK_COLUMNS + 1, padx=16, pady=(12, 8), sticky="w")
        return box

    def _build_add_box(self) -> ctk.CTkFrame:
        box = self._section("Add Site")
        inputs = ctk.CTkFrame(box, fg_color="transparent")
        inputs.grid(row=1, column=0, columnspan=3, padx=16, pady=4, sticky="w")
        self.site_entry = ctk.CTkEntry(inputs, width=260, placeholder_text="reddit.com or a full URL")
        self.site_entry.pack(side="left", padx=(0, 8))
        self.site_entry.bind("<KeyRelease>", self._on_site_typed)
        self.site_entry.bind("<Return>", lambda e: self._add_site())
        self.name_entry = ctk.CTkEntry(inputs, width=180, placeholder_text="Display name")
        self.name_entry.pack(side="left", padx=8)
        self.name_entry.bind("<KeyRelease>", lambda e: setattr(self, "name_edited", bool(self.name_entry.get())))
        self.name_entry.bind("<Return>", lambda e: self._add_site())
        ctk.CTkButton(inputs, text="+ Add", width=80, command=self._add_site).pack(side="left", padx=8)

        types = ctk.CTkFrame(box, fg_color="transparent")
        types.grid(row=2, column=0, columnspan=3, padx=16, pady=(6, 2), sticky="w")
        ctk.CTkLabel(types, text="Block type:").pack(side="left", padx=(0, 10))
        self.use_permanent = ctk.BooleanVar(value=True)
        self.use_hours = ctk.BooleanVar(value=False)
        self.use_temp = ctk.BooleanVar(value=False)
        for text, var in (("Permanent", self.use_permanent), ("By hours", self.use_hours), ("Temporary", self.use_temp)):
            ctk.CTkCheckBox(types, text=text, variable=var, command=self._update_rule_rows).pack(side="left", padx=(0, 14))
        ctk.CTkLabel(types, text="(can check several)", text_color=MUTED).pack(side="left")

        # "By hours" options
        self.hours_row = ctk.CTkFrame(box, fg_color="transparent")
        ctk.CTkLabel(self.hours_row, text="Blocked on").pack(side="left", padx=(0, 8))
        self.day_vars = []
        for i, day in enumerate(DAY_NAMES):
            var = ctk.BooleanVar(value=i < 5)
            ctk.CTkCheckBox(self.hours_row, text=day, variable=var, width=52).pack(side="left")
            self.day_vars.append(var)
        ctk.CTkLabel(self.hours_row, text="from").pack(side="left", padx=(10, 6))
        self.start_entry = ctk.CTkEntry(self.hours_row, width=64)
        self.start_entry.insert(0, "09:00")
        self.start_entry.pack(side="left")
        ctk.CTkLabel(self.hours_row, text="to").pack(side="left", padx=6)
        self.end_entry = ctk.CTkEntry(self.hours_row, width=64)
        self.end_entry.insert(0, "17:00")
        self.end_entry.pack(side="left")
        ctk.CTkLabel(self.hours_row, text="(end before start = overnight)", text_color=MUTED).pack(side="left", padx=8)

        # "Temporary" options
        self.temp_row = ctk.CTkFrame(box, fg_color="transparent")
        ctk.CTkLabel(self.temp_row, text="Block for").pack(side="left", padx=(0, 8))
        self.duration = ctk.CTkOptionMenu(self.temp_row, values=list(DURATIONS), width=110)
        self.duration.set("1 hour")
        self.duration.pack(side="left")
        ctk.CTkLabel(self.temp_row, text="starting now", text_color=MUTED).pack(side="left", padx=8)

        self.add_error = ctk.CTkLabel(box, text="", text_color=ERROR)
        self.add_error.grid(row=5, column=0, columnspan=3, padx=16, pady=(0, 8), sticky="w")
        return box

    def _update_rule_rows(self):
        if self.use_hours.get():
            self.hours_row.grid(row=3, column=0, columnspan=3, padx=16, pady=4, sticky="w")
        else:
            self.hours_row.grid_remove()
        if self.use_temp.get():
            self.temp_row.grid(row=4, column=0, columnspan=3, padx=16, pady=4, sticky="w")
        else:
            self.temp_row.grid_remove()

    def _build_quick_box(self) -> ctk.CTkFrame:
        box = self._section("Quick Add - Popular Sites (permanent)")
        row = 1
        for category, sites in POPULAR_SITES.items():
            ctk.CTkLabel(box, text=category, text_color=MUTED).grid(row=row, column=0, padx=16, pady=4, sticky="nw")
            for i, name in enumerate(sites):
                var = ctk.BooleanVar()
                self.quick_vars[name] = var
                ctk.CTkCheckBox(box, text=name, variable=var, command=lambda n=name, c=category: self._toggle_quick(c, n)
                                ).grid(row=row + i // QUICK_COLUMNS, column=1 + i % QUICK_COLUMNS, padx=8, pady=4, sticky="w")
            row += (len(sites) - 1) // QUICK_COLUMNS + 1
        ctk.CTkLabel(box, text="").grid(row=row, column=0, pady=2)
        return box

    def _show_tab(self, tab: str):
        for w in (self.add_box, self.quick_box, self.list_title, self.list_box, self.placeholder):
            w.pack_forget()
        if tab in NOT_YET:
            self.placeholder.configure(text=NOT_YET[tab])
            self.placeholder.pack(anchor="w", padx=10, pady=10)
            return
        if tab == "All":
            self.add_box.pack(fill="x", pady=(0, 12))
            self.quick_box.pack(fill="x", pady=(0, 12))
        self.list_title.pack(anchor="w", padx=10, pady=(4, 6))
        self.list_box.pack(fill="x")
        self.refresh()

    # ---------- data ----------

    def _auto_refresh(self):
        self.refresh()  # countdowns + "blocked now" status
        self.after(REFRESH_MS, self._auto_refresh)

    def refresh(self):
        now = datetime.now()
        items = self.db.list_items()
        rule_filter = TAB_RULE.get(self.tabs.get())
        if rule_filter:
            items = [i for i in items if any(r["rule_type"] == rule_filter for r in i["rules"])]
        self.list_title.configure(text=f"Blocked Items ({len(items)})")

        quick_names = {i["display_name"] for i in items if i["source"] == "quick-list"}
        for name, var in self.quick_vars.items():
            var.set(name in quick_names)

        for w in self.list_box.winfo_children():
            w.destroy()
        if not items:
            ctk.CTkLabel(self.list_box, text="Nothing here yet.", text_color=MUTED).pack(anchor="w", padx=16, pady=12)
            return
        self.list_box.grid_columnconfigure(2, weight=1)
        for col, head in enumerate(["Name", "Type", "Hostnames", "Rules", "Status", "Alerts", ""]):
            ctk.CTkLabel(self.list_box, text=head, text_color=MUTED).grid(row=0, column=col, padx=10, pady=(8, 2), sticky="w")
        for r, item in enumerate(items, start=1):
            ctk.CTkLabel(self.list_box, text=item["display_name"], font=ctk.CTkFont(weight="bold")).grid(
                row=r, column=0, padx=10, pady=4, sticky="w")
            ctk.CTkLabel(self.list_box, text=f"[{item['item_type']}]", text_color=MUTED).grid(row=r, column=1, padx=10, sticky="w")
            ctk.CTkLabel(self.list_box, text=", ".join(item["target"].split()), text_color=MUTED, wraplength=230,
                         justify="left", anchor="w").grid(row=r, column=2, padx=10, sticky="w")
            ctk.CTkLabel(self.list_box, text="\n".join(describe_rule(rule, now) for rule in item["rules"]),
                         justify="left").grid(row=r, column=3, padx=10, sticky="w")
            ctk.CTkLabel(self.list_box, **self._status(item, now)).grid(row=r, column=4, padx=10, sticky="w")
            alerts = ctk.CTkOptionMenu(self.list_box, values=list(ALERTS), width=90,
                                       command=lambda v, i=item["id"]: self.db.set_item_notify(i, ALERTS[v]))
            alerts.set(next(k for k, v in ALERTS.items() if v == item["notify"]))
            alerts.grid(row=r, column=5, padx=10)
            ctk.CTkButton(self.list_box, text="Remove", width=70, fg_color="transparent", border_width=1,
                          command=lambda i=item["id"]: self._remove(i)).grid(row=r, column=6, padx=10, pady=4)

    def _status(self, item: dict, now: datetime) -> dict:
        if not item_block(item["rules"], now):
            return {"text": "○ Allowed now", "text_color": MUTED}
        if not self.app.service_running:
            return {"text": "○ Pending - service\nnot running", "text_color": "#d29922"}
        return {"text": "● Blocked now", "text_color": "#3fb950"}

    def _on_site_typed(self, _event):
        if self.name_edited:
            return
        self.name_entry.delete(0, "end")
        try:
            self.name_entry.insert(0, _guess_name(normalize_host(self.site_entry.get())))
        except ValueError:
            pass

    def _selected_rules(self) -> list[dict]:
        """Rules from the form. Raises ValueError with a user-facing message."""
        rules = []
        if self.use_permanent.get():
            rules.append({"rule_type": "permanent"})
        if self.use_hours.get():
            days = [i for i, v in enumerate(self.day_vars) if v.get()]
            try:
                schedule = make_schedule(days, self.start_entry.get(), self.end_entry.get())
            except ValueError as e:
                raise ValueError(str(e) if not days else "Hours must look like 09:00 or 21:30.") from e
            rules.append({"rule_type": "scheduled", "schedule": schedule})
        if self.use_temp.get():
            until = datetime.now() + timedelta(minutes=DURATIONS[self.duration.get()])
            rules.append({"rule_type": "temporary", "temp_until": until.strftime(TIME_FMT)})
        if not rules:
            raise ValueError("Pick at least one block type.")
        return rules

    def _add_site(self):
        try:
            host = normalize_host(self.site_entry.get())
            rules = self._selected_rules()
        except ValueError as e:
            self.add_error.configure(text=str(e))
            return
        if any(host in i["target"].split() for i in self.db.list_items()):
            self.add_error.configure(text=f"{host} is already in the list.")
            return
        entry = _popular_entry(host)  # known site: block all of its hostnames
        self.db.add_site(self.name_entry.get().strip() or _guess_name(host), entry[1] if entry else [host], rules=rules)
        self.site_entry.delete(0, "end")
        self.name_entry.delete(0, "end")
        self.name_edited = False
        self.add_error.configure(text="")
        self.refresh()

    def _toggle_quick(self, category: str, name: str):
        if self.quick_vars[name].get():
            self.db.add_site(name, POPULAR_SITES[category][name], source="quick-list")
        else:
            for item in self.db.list_items():
                if item["source"] == "quick-list" and item["display_name"] == name:
                    self.db.remove_item(item["id"])
        self.refresh()

    def _remove(self, item_id: int):
        self.db.remove_item(item_id)
        self.refresh()
