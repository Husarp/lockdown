"""Blocking > Protection: the always-on community lists (scam, phishing, malware, adult, gambling), forced SafeSearch,
the blocked-words check, a "check a site" box and sites allowed anyway. Changes apply at once; the service downloads / updates the lists."""
import threading
from datetime import datetime

import customtkinter as ctk

import antibypass
from blocker import protection
from blocker.hosts import normalize_host
from gui import icons, theme
from gui.components import Card, Rows, help_icon
from gui.widgets import clear_entry
from gui.words_cards import WordsCards
from trusted_time import now_from_db

MUTED = theme.MUTED


def ago(stamp: str | None, now: datetime) -> str:
    if not stamp:
        return ""
    minutes = int((now - datetime.fromisoformat(stamp)).total_seconds() // 60)
    return "just now" if minutes < 2 else f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"


def _allowed_chip(parent):
    f = ctk.CTkFrame(parent, fg_color=theme.SURFACE2, border_width=1, border_color=theme.BORDER, corner_radius=4)
    f.name = ctk.CTkLabel(f, text="", compound="left", height=26)
    f.name.pack(side="left", padx=(8, 2))
    f.remove = ctk.CTkButton(f, text="×", width=22, height=22, fg_color="transparent", hover_color=theme.BORDER,
                             text_color=MUTED)
    f.remove.pack(side="left", padx=(0, 4))
    return f


def download_text(p: dict) -> str:
    """"12.3 of 45.0 MB (part 1 of 2)"."""
    big = max(p["done"], p.get("total") or 0) >= 1_048_576
    size = (lambda n: f"{n / 1_048_576:.1f}") if big else (lambda n: f"{n // 1024}")
    unit = "MB" if big else "KB"
    text = f"{size(p['done'])} of {size(p['total'])} {unit}" if p.get("total") else f"{size(p['done'])} {unit}"
    return text + (f" (part {p['part']} of {p['parts']})" if p.get("parts", 1) > 1 else "")


class ManualListWindow(ctk.CTkToplevel):
    """Edit one of your hand-made blocking lists: add a site (name + address), remove sites. Saved on Save;
    on_save(entries) with entries = [{"name", "host"}]. Removing sites loosens a block, so the caller guards it."""

    def __init__(self, app, name: str, entries: list[dict], on_save):
        super().__init__(app)
        self.on_save = on_save
        self.entries = [dict(e) for e in entries]
        self.title(name)
        self.geometry("470x520")
        self.configure(fg_color=theme.BG)
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=18, pady=16)
        ctk.CTkLabel(box, text=name, font=theme.card_title()).pack(anchor="w")
        self.count = ctk.CTkLabel(box, text="", text_color=MUTED)
        self.count.pack(anchor="w", pady=(0, 6))
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(fill="x", pady=(0, 4))
        self.name_e = ctk.CTkEntry(line, width=130, placeholder_text="Name (optional)")
        self.name_e.pack(side="left")
        self.addr_e = ctk.CTkEntry(line, placeholder_text="site address, e.g. reddit.com")
        self.addr_e.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.addr_e.bind("<Return>", lambda e: self._add())
        ctk.CTkButton(line, text="Add", width=70, command=self._add).pack(side="left", padx=(8, 0))
        self.error = ctk.CTkLabel(box, text="", text_color=theme.DANGER, height=14)
        self.error.pack(anchor="w")
        self.list = ctk.CTkScrollableFrame(box, fg_color=theme.SURFACE, corner_radius=6)
        self.list.pack(fill="both", expand=True)
        buttons = ctk.CTkFrame(box, fg_color="transparent")
        buttons.pack(fill="x", pady=(10, 0))
        ctk.CTkButton(buttons, text="Save", width=90, command=self._save).pack(side="right")
        ctk.CTkButton(buttons, text="Cancel", width=90, **theme.OUTLINE, command=self.destroy).pack(side="right",
                                                                                                    padx=8)
        self.transient(app)
        self._fill()
        self.after(50, self._modal)

    def _modal(self):
        try:
            self.grab_set()
        except Exception:
            self.after(50, self._modal)

    def _fill(self):
        for w in self.list.winfo_children():
            w.destroy()
        for i, e in enumerate(self.entries):
            row = ctk.CTkFrame(self.list, fg_color="transparent")
            row.pack(fill="x")
            text = e["host"] + (f"   ·   {e['name']}" if e.get("name") else "")
            ctk.CTkLabel(row, text=text, anchor="w", height=24).pack(side="left", padx=6)
            ctk.CTkButton(row, text="×", width=24, height=22, fg_color="transparent", hover_color=theme.BORDER,
                          text_color=MUTED, command=lambda i=i: self._remove(i)).pack(side="right", padx=4)
        n = len(self.entries)
        self.count.configure(text=f"{n} site{'s' * (n != 1)}")

    def _add(self):
        try:
            host = normalize_host(self.addr_e.get())
        except ValueError as ex:
            self.error.configure(text=str(ex))
            return
        self.error.configure(text="")
        if not any(e["host"] == host for e in self.entries):
            self.entries.insert(0, {"name": self.name_e.get().strip(), "host": host})
            clear_entry(self.name_e)
            clear_entry(self.addr_e)
            self._fill()

    def _remove(self, i: int):
        self.entries.pop(i)
        self._fill()

    def _save(self):
        self.destroy()
        self.on_save(self.entries)


class ProtectionTab(ctk.CTkScrollableFrame):
    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.db = page, page.app.db
        ctk.CTkLabel(self, text="Always-on lists of harmful sites, kept up to date by the community and downloaded "
                                "automatically (daily by default). They work next to your own blocklist; modes and the emergency unlock "
                                "don't switch them off.", text_color=MUTED, wraplength=820, justify="left").pack(
            anchor="w", pady=(0, 10))
        lists = Card(self, "Lists")
        lists.pack(fill="x", pady=(0, 12))
        ctk.CTkButton(lists.title.master, text="Update now", width=110, **theme.OUTLINE,
                      command=self._update_now).pack(side="right")
        self.every = ctk.CTkOptionMenu(lists.title.master, width=130, values=list(protection.UPDATE_CHOICES),
                                       command=lambda v: self._save_updates())
        self.every.pack(side="right", padx=(0, 12))
        self.auto = ctk.CTkSwitch(lists.title.master, text="Update automatically", command=self._save_updates)
        self.auto.pack(side="right", padx=(0, 8))
        self.switches, self.infos = {}, {}
        self.list_rows = ctk.CTkFrame(lists.body, fg_color="transparent")
        self.list_rows.pack(fill="x")
        self.row_keys: list[str] = []
        self._build_list_rows(self._community(protection.settings(self.db)))
        own = ctk.CTkFrame(lists.body, fg_color="transparent")
        own.pack(fill="x", pady=(10, 0))
        head = ctk.CTkFrame(own, fg_color="transparent")
        head.pack(anchor="w")
        ctk.CTkLabel(head, text="Add your own blocking list", font=theme.semi(13)).pack(side="left")
        help_icon(head, "Any block list on the internet: a hosts file, a plain list of domains or an adblock-style "
                        "list (||site.com^ - blocks its subdomains too). It's downloaded and updated once a day like "
                        "the others. Preview it first to see what's in it.").pack(side="left", padx=6)
        line = ctk.CTkFrame(own, fg_color="transparent")
        line.pack(anchor="w", pady=(4, 0))
        self.own_name = ctk.CTkEntry(line, width=150, placeholder_text="Name (e.g. Crypto)")
        self.own_name.pack(side="left")
        self.own_url = ctk.CTkEntry(line, width=380, placeholder_text="https://... list address")
        self.own_url.pack(side="left", padx=8)
        ctk.CTkButton(line, text="Preview", width=90, **theme.OUTLINE, command=self._preview).pack(side="left")
        self.own_add = ctk.CTkButton(line, text="Add list", width=90, command=self._add_own)
        self.own_result = ctk.CTkLabel(own, text="", anchor="w", justify="left", wraplength=820)
        self.own_result.pack(anchor="w", pady=(4, 0))
        self.previewed: tuple[str, int] | None = None   # (url, count) of the last good preview

        mine = Card(self, "Your blocking lists")
        mine.pack(fill="x", pady=(0, 12))
        help_icon(mine.title.master, "Your own lists of sites to block. Type a site into a list; turn the list on to "
                                     "block everything in it (and its subdomains). Adding sites is instant; removing "
                                     "them or turning a list off needs the Anti-Bypass challenge.").pack(side="left",
                                                                                                        padx=8)
        self.manual_rows = ctk.CTkFrame(mine.body, fg_color="transparent")
        self.manual_rows.pack(fill="x")
        line = ctk.CTkFrame(mine.body, fg_color="transparent")
        line.pack(anchor="w", pady=(8, 0))
        self.new_list = ctk.CTkEntry(line, width=220, placeholder_text="New list name (e.g. Distractions)")
        self.new_list.pack(side="left")
        self.new_list.bind("<Return>", lambda e: self._create_manual())
        ctk.CTkButton(line, text="Create list", width=110, **theme.OUTLINE, command=self._create_manual).pack(
            side="left", padx=8)

        self.words = WordsCards(self, page.app)

        check = Card(self, "Check a site")
        check.pack(fill="x", pady=(0, 12))
        line = ctk.CTkFrame(check.body, fg_color="transparent")
        line.pack(anchor="w")
        self.check_entry = ctk.CTkEntry(line, width=260, placeholder_text="e.g. suspicious-shop.com")
        self.check_entry.pack(side="left")
        self.check_entry.bind("<Return>", lambda e: self._check())
        ctk.CTkButton(line, text="Check", width=80, command=self._check).pack(side="left", padx=8)
        self.check_action = ctk.CTkButton(line, text="", width=110, **theme.OUTLINE)   # one-click allow / block
        self.check_result = ctk.CTkLabel(check.body, text="", anchor="w")
        self.check_result.pack(anchor="w", pady=(6, 0))

        allowed = Card(self, "Allowed anyway")
        allowed.pack(fill="x", pady=(0, 12))
        help_icon(allowed.title.master, "Sites a list blocks by mistake (their subdomains too). They open again "
                                        "within a minute.").pack(side="left", padx=8)
        self.chips = Rows(allowed.body, _allowed_chip, "None.", {"side": "left", "padx": (0, 6), "pady": 4})
        line = ctk.CTkFrame(allowed.body, fg_color="transparent")
        line.pack(anchor="w", pady=(4, 0))
        self.allow_entry = ctk.CTkEntry(line, width=260, placeholder_text="site to allow")
        self.allow_entry.pack(side="left")
        self.allow_entry.bind("<Return>", lambda e: self._allow())
        ctk.CTkButton(line, text="Allow", width=80, **theme.OUTLINE, command=self._allow).pack(side="left", padx=8)
        self.allow_error = ctk.CTkLabel(allowed.body, text="", text_color=theme.DANGER, height=16)
        self.allow_error.pack(anchor="w")
        self._poll = None

    def _build_list_rows(self, lists: dict):
        """One row per list (switch, what it blocks, status); your own lists also get a remove button."""
        for w in self.list_rows.winfo_children():
            w.destroy()
        self.switches, self.infos = {}, {}
        for key, (name, what, _sources, _default) in lists.items():
            row = ctk.CTkFrame(self.list_rows, fg_color="transparent")
            row.pack(fill="x", pady=4)
            sw = ctk.CTkSwitch(row, text=name, font=theme.semi(13), width=150, command=self._save)
            sw.pack(side="left")
            texts = ctk.CTkFrame(row, fg_color="transparent")
            texts.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(texts, text=what if key in protection.LISTS else f"your list · {what}", anchor="w",
                         height=18).pack(anchor="w")
            info = ctk.CTkLabel(texts, text="", text_color=MUTED, font=theme.body(11), anchor="w", height=14)
            info.pack(anchor="w")
            if key not in protection.LISTS:
                ctk.CTkButton(row, text="Remove", width=80, **theme.OUTLINE,
                              command=lambda k=key, n=name: self._remove_own(k, n)).pack(side="right")
            self.switches[key], self.infos[key] = sw, info
        self.row_keys = list(lists)

    # ---------- your own lists ----------

    def _preview(self):
        url = self.own_url.get().strip()
        if not url.lower().startswith(("http://", "https://")):
            self.own_result.configure(text="Paste the list's full address (starting with https://).",
                                      text_color=theme.DANGER)
            return
        self.own_add.pack_forget()
        self.previewed = None
        self.own_result.configure(text="Downloading the list to have a look...", text_color=MUTED)
        result = []

        def work():
            try:
                result.append(protection.preview(url))
            except Exception as e:   # offline, not a list, too big
                result.append(e)
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        self._show_preview(url, worker, result)

    def _show_preview(self, url: str, worker, result: list):
        if worker.is_alive():
            self.after(100, self._show_preview, url, worker, result)
            return
        if isinstance(result[0], Exception):
            text = str(result[0]) if isinstance(result[0], ValueError) else f"Couldn't download it: {result[0]}"
            self.own_result.configure(text=text, text_color=theme.DANGER)
            return
        count, sample = result[0]
        self.previewed = (url, count)
        self.own_result.configure(text=f"{count:,} sites, e.g. {', '.join(sample)}", text_color=theme.TEXT)
        self.own_add.pack(side="left", padx=8)

    def _add_own(self):
        if not self.previewed:
            return
        url, count = self.previewed
        cfg = protection.settings(self.db)
        name = self.own_name.get().strip() or url.split("/")[2]
        key = protection.new_custom_key(cfg)
        cfg["custom"].append({"key": key, "name": name, "url": url})
        cfg["enabled"].append(key)
        protection.save_settings(self.db, cfg)   # (a new list only blocks more: no challenge)
        self.own_add.pack_forget()
        self.previewed = None
        for entry in (self.own_name, self.own_url):
            clear_entry(entry)
        self.own_result.configure(text=f"Added {name} ({count:,} sites) - it's downloaded in a few seconds.",
                                  text_color=theme.ALLOWED)
        self.refresh()

    def _remove_own(self, key: str, name: str):
        cfg = protection.settings(self.db)
        cfg["custom"] = [c for c in cfg["custom"] if c["key"] != key]
        cfg["enabled"] = [k for k in cfg["enabled"] if k != key]
        cfg["info"].pop(key, None)
        self._store(cfg, f"Remove your {name} list")

    # ---------- your own hand-made lists ----------

    def _community(self, cfg: dict) -> dict:
        """The lists shown in the community "Lists" card: everything except your hand-made lists (own card)."""
        manual = protection.manual_lists(cfg)
        return {k: v for k, v in protection.all_lists(cfg).items() if k not in manual}

    def _refresh_manual(self, cfg: dict):
        for w in self.manual_rows.winfo_children():
            w.destroy()
        if not cfg["manual"]:
            ctk.CTkLabel(self.manual_rows, text="No lists yet - create one below, then open it to add sites.",
                         text_color=MUTED).pack(anchor="w", pady=2)
            return
        for m in cfg["manual"]:
            row = ctk.CTkFrame(self.manual_rows, fg_color="transparent")
            row.pack(fill="x", pady=4)
            sw = ctk.CTkSwitch(row, text=m["name"], font=theme.semi(13), width=220)
            sw.configure(command=lambda k=m["key"], s=sw: self._toggle_manual(k, s))
            sw.select() if m["key"] in cfg["enabled"] else sw.deselect()
            sw.pack(side="left")
            n = len(m["entries"])
            ctk.CTkLabel(row, text=f"{n} site{'s' * (n != 1)}", text_color=MUTED, width=80, anchor="w").pack(
                side="left", padx=10)
            ctk.CTkButton(row, text="Remove list", width=110, **theme.OUTLINE,
                          command=lambda k=m["key"], nm=m["name"]: self._remove_manual(k, nm)).pack(side="right")
            ctk.CTkButton(row, text="Open", width=70, **theme.OUTLINE,
                          command=lambda k=m["key"]: self._open_manual(k)).pack(side="right", padx=8)

    def _create_manual(self):
        name = " ".join(self.new_list.get().split())
        if not name:
            return
        cfg = protection.settings(self.db)
        key = protection.new_manual_key(cfg)
        cfg["manual"] = cfg["manual"] + [{"key": key, "name": name, "entries": []}]
        cfg["enabled"] = cfg["enabled"] + [key]          # a new, empty list blocks nothing yet: no challenge
        protection.save_settings(self.db, cfg)
        clear_entry(self.new_list)
        self.refresh()

    def _toggle_manual(self, key: str, sw):
        cfg = protection.settings(self.db)
        enabled = [k for k in cfg["enabled"] if k != key] + ([key] if sw.get() else [])
        name = protection.manual_lists(cfg)[key]["name"]
        self._store({**cfg, "enabled": enabled}, f"Turn your {name} list off")

    def _remove_manual(self, key: str, name: str):
        cfg = protection.settings(self.db)
        self._store({**cfg, "manual": [m for m in cfg["manual"] if m["key"] != key],
                     "enabled": [k for k in cfg["enabled"] if k != key]}, f"Remove your {name} list")

    def _open_manual(self, key: str):
        m = protection.manual_lists(protection.settings(self.db))[key]

        def save(entries):
            latest = protection.settings(self.db)
            manual = [{**x, "entries": entries} if x["key"] == key else x for x in latest["manual"]]
            self._store({**latest, "manual": manual}, f"Remove sites from your {m['name']} list")
        ManualListWindow(self.page.app, m["name"], m["entries"], save)

    # ---------- data ----------

    def refresh(self, now=None, usage=None):
        now = now_from_db(self.db)
        cfg = protection.settings(self.db)
        self._refresh_manual(cfg)
        lists = self._community(cfg)
        if list(lists) != self.row_keys:   # your own URL list added / removed
            self._build_list_rows(lists)
        progress = protection.download_progress(self.db)
        for key, sw in self.switches.items():
            sw.select() if key in cfg["enabled"] else sw.deselect()
            info = cfg["info"].get(key, {})
            if progress and progress["key"] == key:
                text = "Downloading... " + download_text(progress)
            elif info.get("count"):
                text = f"{info['count']:,} sites · updated {ago(info.get('updated'), now)}"
                if info.get("failed") and info["failed"] > info.get("updated", ""):
                    text += " · last update failed (offline?) - trying again in an hour"
            elif info.get("failed"):
                text = "Couldn't download it yet (offline?) - trying again in an hour."
            elif key in cfg["enabled"]:
                text = "Waiting to download (the service does it within a few seconds; it needs to be running)"
            else:
                text = "Off"
            self.infos[key].configure(text=text)
        asked = cfg.get("update_now") or ""
        waiting = progress or any(not cfg["info"].get(k, {}).get("count") or cfg["info"][k].get("updated", "") < asked
                                  for k in cfg["enabled"] if k in self.switches)
        if waiting and self._poll is None:   # follow the download
            self._poll = self.after(1000, self._tick)
        self.words.refresh()
        self.auto.select() if cfg["auto"] else self.auto.deselect()
        self.every.set(next((k for k, v in protection.UPDATE_CHOICES.items() if v == cfg["every_hours"]), "daily"))
        self.every.configure(state="normal" if cfg["auto"] else "disabled")
        allowed = sorted(cfg["allowed"])
        for chip, site in zip(self.chips.take(len(allowed)), allowed):
            chip.name.configure(text=f" {site}", image=icons.get(site, 16))
            chip.remove.configure(command=lambda s=site: self._unallow(s))

    def _tick(self):
        self._poll = None
        if self.winfo_ismapped():
            self.refresh()

    def _store(self, cfg: dict, what: str):
        """Save; switching a list off / allowing a site loosens blocks, so that goes through Anti-Bypass (unless it
        undoes a tightening from a few seconds ago - a mis-click grace)."""
        old = protection.settings(self.db)

        def save():   # (the service may have updated the lists' info meanwhile: keep that)
            protection.save_settings(self.db, {**protection.settings(self.db), "enabled": cfg["enabled"],
                                               "allowed": cfg["allowed"], "custom": cfg["custom"],
                                               "manual": cfg["manual"]})
            self.refresh()
        key = (sorted(cfg["enabled"]), sorted(cfg["allowed"]), cfg["custom"])
        if antibypass.protection_looser(old, cfg) and not self.page.app.grace_ok("protection", key):
            self.page.app.guard([what], save, self.refresh)
        else:
            if not antibypass.protection_looser(old, cfg):   # a tightening: remember how to undo it quickly
                self.page.app.grace_note("protection", (sorted(old["enabled"]), sorted(old["allowed"]), old["custom"]))
            save()

    def _save(self):
        cfg = protection.settings(self.db)
        lists = protection.all_lists(cfg)
        off = [lists[k][0] for k in cfg["enabled"] if k in self.switches and not self.switches[k].get()]
        cfg["enabled"] = [k for k, sw in self.switches.items() if sw.get()]
        self._store(cfg, f"Switch the {', '.join(off)} protection list off")

    def _save_updates(self):
        cfg = protection.settings(self.db)
        cfg["auto"] = bool(self.auto.get())
        cfg["every_hours"] = protection.UPDATE_CHOICES[self.every.get()]
        protection.save_settings(self.db, cfg)
        self.refresh()

    def _update_now(self):
        cfg = protection.settings(self.db)
        cfg["update_now"] = now_from_db(self.db).isoformat(timespec="seconds")
        protection.save_settings(self.db, cfg)
        self.page.confirm("Lists will update in a few seconds", immediate=True)
        if self._poll is None:
            self._poll = self.after(1000, self._tick)

    def _check(self):
        try:
            host = normalize_host(self.check_entry.get())
        except ValueError as e:
            self.check_result.configure(text=str(e), text_color=theme.DANGER)
            return
        self.check_result.configure(text="Checking...", text_color=MUTED)
        result = []   # the lists are big (~70 MB): searched in a thread so the window doesn't freeze
        keys = list(protection.all_lists(protection.settings(self.db)))
        worker = threading.Thread(target=lambda: result.append(protection.lists_with(host, keys)), daemon=True)
        worker.start()
        self._show_check(host, worker, result)

    def _show_check(self, host: str, worker, result: list):
        if worker.is_alive():
            self.after(50, self._show_check, host, worker, result)
            return
        cfg = protection.settings(self.db)
        lists = protection.all_lists(cfg)
        found = [lists[k][0] + ("" if k in cfg["enabled"] else " (list is off)") for k in result[0]]
        found += [f"{m['name']} (your list)" for m in cfg["manual"]
                  if m["key"] in cfg["enabled"] and self._host_on(host, m["entries"])]
        self.check_action.pack_forget()
        if host in cfg["allowed"]:
            text, color = f"{host} is allowed anyway by you.", theme.ALLOWED
            self.check_action.configure(text="Block again", command=lambda: self._block_again(host))
            self.check_action.pack(side="left")
        elif found:
            text, color = f"{host} is on the {', '.join(found)} list{'s' * (len(found) > 1)}.", theme.BLOCKED
            self.check_action.configure(text="Allow anyway", command=lambda: self._allow_host(host))
            self.check_action.pack(side="left")   # one click to unblock it everywhere, no matter which list
        else:
            text, color = f"{host} isn't on any list.", theme.ALLOWED
        self.check_result.configure(text=text, text_color=color)

    @staticmethod
    def _host_on(host: str, entries: list[dict]) -> bool:
        return any(host == e["host"] or host.endswith("." + e["host"]) for e in entries if e.get("host"))

    def _allow_host(self, host: str):
        """One-click "Allow anyway" from Check a site - overrides every list (loosening -> the challenge)."""
        cfg = protection.settings(self.db)
        if host not in cfg["allowed"]:
            cfg["allowed"].append(host)
            self._store(cfg, f"Allow {host} (and its subdomains) although a list blocks it")
        self.after(400, self._check)

    def _block_again(self, host: str):
        cfg = protection.settings(self.db)
        cfg["allowed"] = [a for a in cfg["allowed"] if a != host]
        self._store(cfg, f"Stop allowing {host}")   # tightening: applies at once
        self.after(400, self._check)

    def _allow(self):
        try:
            host = normalize_host(self.allow_entry.get())
        except ValueError as e:
            self.allow_error.configure(text=str(e))
            return
        cfg = protection.settings(self.db)
        self.allow_entry.delete(0, "end")
        self.allow_error.configure(text="")
        if host not in cfg["allowed"]:
            cfg["allowed"].append(host)
            self._store(cfg, f"Allow {host} (and its subdomains) although a protection list blocks it")

    def _unallow(self, host: str):
        cfg = protection.settings(self.db)
        cfg["allowed"] = [a for a in cfg["allowed"] if a != host]
        protection.save_settings(self.db, cfg)
        self.refresh()
