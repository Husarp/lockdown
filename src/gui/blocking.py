"""Blocking page: add sites, popular quick-list, blocked items list."""
import customtkinter as ctk

from blocker.hosts import normalize_host
from importer.popular import POPULAR_SITES

SUB_TABS = ["All", "By Hours", "By Limit", "By Switches", "Permanent", "Temporary"]
PHASE2_TABS = {"By Hours", "By Limit", "By Switches", "Temporary"}
QUICK_COLUMNS = 4
MUTED = "gray60"
ERROR = "#f85149"


def _guess_name(host: str) -> str:
    for sites in POPULAR_SITES.values():
        for name, hostnames in sites.items():
            if host in hostnames:
                return name
    return host.split(".")[-2].capitalize()


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

    # ---------- layout ----------

    def _section(self, title: str) -> ctk.CTkFrame:
        box = ctk.CTkFrame(self.body)
        ctk.CTkLabel(box, text=title, font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, columnspan=QUICK_COLUMNS + 1, padx=16, pady=(12, 8), sticky="w")
        return box

    def _build_add_box(self) -> ctk.CTkFrame:
        box = self._section("Add Site")
        self.site_entry = ctk.CTkEntry(box, width=260, placeholder_text="reddit.com or a full URL")
        self.site_entry.grid(row=1, column=0, padx=(16, 8), pady=4, sticky="w")
        self.site_entry.bind("<KeyRelease>", self._on_site_typed)
        self.site_entry.bind("<Return>", lambda e: self._add_site())
        self.name_entry = ctk.CTkEntry(box, width=180, placeholder_text="Display name")
        self.name_entry.grid(row=1, column=1, padx=8, pady=4, sticky="w")
        self.name_entry.bind("<KeyRelease>", lambda e: setattr(self, "name_edited", bool(self.name_entry.get())))
        self.name_entry.bind("<Return>", lambda e: self._add_site())
        ctk.CTkButton(box, text="+ Add", width=80, command=self._add_site).grid(row=1, column=2, padx=8, pady=4)
        ctk.CTkLabel(box, text="Block type: Permanent (more types in Phase 2)", text_color=MUTED).grid(
            row=2, column=0, columnspan=3, padx=16, sticky="w")
        self.add_error = ctk.CTkLabel(box, text="", text_color=ERROR)
        self.add_error.grid(row=3, column=0, columnspan=3, padx=16, pady=(0, 8), sticky="w")
        return box

    def _build_quick_box(self) -> ctk.CTkFrame:
        box = self._section("Quick Add - Popular Sites")
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
        if tab in PHASE2_TABS:
            self.placeholder.configure(text=f"{tab} blocking is coming in Phase 2.")
            self.placeholder.pack(anchor="w", padx=10, pady=10)
            return
        if tab == "All":
            self.add_box.pack(fill="x", pady=(0, 12))
            self.quick_box.pack(fill="x", pady=(0, 12))
        self.list_title.pack(anchor="w", padx=10, pady=(4, 6))
        self.list_box.pack(fill="x")
        self.refresh()

    # ---------- data ----------

    def refresh(self):
        items = self.db.list_items()
        if self.tabs.get() == "Permanent":
            items = [i for i in items if "permanent" in (i["rules"] or "")]
        self.list_title.configure(text=f"Blocked Items ({len(items)})")

        quick_names = {i["display_name"] for i in items if i["source"] == "quick-list"}
        for name, var in self.quick_vars.items():
            var.set(name in quick_names)

        for w in self.list_box.winfo_children():
            w.destroy()
        if not items:
            ctk.CTkLabel(self.list_box, text="Nothing blocked yet.", text_color=MUTED).pack(anchor="w", padx=16, pady=12)
            return
        self.list_box.grid_columnconfigure(2, weight=1)
        for col, head in enumerate(["Name", "Type", "Hostnames", "Rules", "Status", ""]):
            ctk.CTkLabel(self.list_box, text=head, text_color=MUTED).grid(row=0, column=col, padx=12, pady=(8, 2), sticky="w")
        status = ("● Blocked", "#3fb950") if self.app.service_running else ("○ Pending - service not running", "#d29922")
        for r, item in enumerate(items, start=1):
            ctk.CTkLabel(self.list_box, text=item["display_name"], font=ctk.CTkFont(weight="bold")).grid(
                row=r, column=0, padx=12, pady=4, sticky="w")
            ctk.CTkLabel(self.list_box, text=f"[{item['item_type']}]", text_color=MUTED).grid(row=r, column=1, padx=12, sticky="w")
            ctk.CTkLabel(self.list_box, text=", ".join(item["target"].split()), text_color=MUTED, wraplength=320,
                         justify="left").grid(row=r, column=2, padx=12, sticky="w")
            ctk.CTkLabel(self.list_box, text=(item["rules"] or "").replace(",", ", ").capitalize()).grid(
                row=r, column=3, padx=12, sticky="w")
            ctk.CTkLabel(self.list_box, text=status[0], text_color=status[1]).grid(row=r, column=4, padx=12, sticky="w")
            ctk.CTkButton(self.list_box, text="Remove", width=70, fg_color="transparent", border_width=1,
                          command=lambda i=item["id"]: self._remove(i)).grid(row=r, column=5, padx=12, pady=4)

    def _on_site_typed(self, _event):
        if self.name_edited:
            return
        self.name_entry.delete(0, "end")
        try:
            self.name_entry.insert(0, _guess_name(normalize_host(self.site_entry.get())))
        except ValueError:
            pass

    def _add_site(self):
        try:
            host = normalize_host(self.site_entry.get())
        except ValueError as e:
            self.add_error.configure(text=str(e))
            return
        if host in self.db.blocked_hostnames():
            self.add_error.configure(text=f"{host} is already blocked.")
            return
        self.db.add_site(self.name_entry.get().strip() or _guess_name(host), [host])
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
