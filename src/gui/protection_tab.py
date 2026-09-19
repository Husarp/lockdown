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


class ProtectionTab(ctk.CTkScrollableFrame):
    def __init__(self, master, page):
        super().__init__(master, fg_color="transparent")
        self.page, self.db = page, page.app.db
        ctk.CTkLabel(self, text="Always-on lists of harmful sites, kept up to date by the community and downloaded "
                                "once a day. They work next to your own blocklist; modes and the emergency unlock "
                                "don't switch them off.", text_color=MUTED, wraplength=820, justify="left").pack(
            anchor="w", pady=(0, 10))
        lists = Card(self, "Lists")
        lists.pack(fill="x", pady=(0, 12))
        ctk.CTkButton(lists.title.master, text="Update now", width=110, **theme.OUTLINE,
                      command=self._update_now).pack(side="right")
        self.switches, self.infos = {}, {}
        for key, (name, what, _url, _default) in protection.LISTS.items():
            row = ctk.CTkFrame(lists.body, fg_color="transparent")
            row.pack(fill="x", pady=4)
            sw = ctk.CTkSwitch(row, text=name, font=theme.semi(13), width=150, command=self._save)
            sw.pack(side="left")
            texts = ctk.CTkFrame(row, fg_color="transparent")
            texts.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(texts, text=what, anchor="w", height=18).pack(anchor="w")
            info = ctk.CTkLabel(texts, text="", text_color=MUTED, font=theme.body(11), anchor="w", height=14)
            info.pack(anchor="w")
            self.switches[key], self.infos[key] = sw, info

        self.words = WordsCards(self, page.app)

        check = Card(self, "Check a site")
        check.pack(fill="x", pady=(0, 12))
        line = ctk.CTkFrame(check.body, fg_color="transparent")
        line.pack(anchor="w")
        self.check_entry = ctk.CTkEntry(line, width=260, placeholder_text="e.g. suspicious-shop.com")
        self.check_entry.pack(side="left")
        self.check_entry.bind("<Return>", lambda e: self._check())
        ctk.CTkButton(line, text="Check", width=80, command=self._check).pack(side="left", padx=8)
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

    # ---------- data ----------

    def refresh(self, now=None, usage=None):
        now = now_from_db(self.db)
        cfg = protection.settings(self.db)
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
        allowed = sorted(cfg["allowed"])
        for chip, site in zip(self.chips.take(len(allowed)), allowed):
            chip.name.configure(text=f" {site}", image=icons.get(site, 16))
            chip.remove.configure(command=lambda s=site: self._unallow(s))

    def _tick(self):
        self._poll = None
        if self.winfo_ismapped():
            self.refresh()

    def _store(self, cfg: dict, what: str):
        """Save; switching a list off / allowing a site loosens blocks, so that goes through Anti-Bypass."""
        def save():   # (the service may have updated the lists' info meanwhile: keep that)
            protection.save_settings(self.db, {**protection.settings(self.db), "enabled": cfg["enabled"],
                                               "allowed": cfg["allowed"]})
            self.refresh()
        if antibypass.protection_looser(protection.settings(self.db), cfg):
            self.page.app.guard([what], save, self.refresh)
        else:
            save()

    def _save(self):
        cfg = protection.settings(self.db)
        off = [protection.LISTS[k][0] for k in cfg["enabled"] if k in self.switches and not self.switches[k].get()]
        cfg["enabled"] = [k for k, sw in self.switches.items() if sw.get()]
        self._store(cfg, f"Switch the {', '.join(off)} protection list off")

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
        worker = threading.Thread(target=lambda: result.append(protection.lists_with(host, protection.LISTS)),
                                  daemon=True)
        worker.start()
        self._show_check(host, worker, result)

    def _show_check(self, host: str, worker, result: list):
        if worker.is_alive():
            self.after(50, self._show_check, host, worker, result)
            return
        cfg = protection.settings(self.db)
        found = [protection.LISTS[k][0] + ("" if k in cfg["enabled"] else " (list is off)") for k in result[0]]
        if host in cfg["allowed"]:
            text, color = f"{host} is allowed anyway by you.", theme.ALLOWED
        elif found:
            text, color = f"{host} is on the {', '.join(found)} list{'s' * (len(found) > 1)}.", theme.BLOCKED
        else:
            text, color = f"{host} isn't on any list.", theme.ALLOWED
        self.check_result.configure(text=text, text_color=color)

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
