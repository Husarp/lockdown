"""Main window (sidebar navigation + content area) and tray agent duties (blocked-visit notifications)."""
import gc
import queue
import time

# Import COM libraries on the main thread: background threads importing them at the same time can deadlock.
import comtypes.client  # noqa: F401
import uiautomation  # noqa: F401

import customtkinter as ctk

import alerts
from blocker.apps import minimizes
from db import Database
from gui.blocking import BlockingPage
from gui.draft import Draft
from gui.notifications import NotificationsPage, Popup
from gui.tray import Tray
from monitor import win
from monitor.usage import UsageTracker
from rules import TIME_FMT
from service import HEARTBEAT_KEY
from trusted_time import now_from_db

# (sidebar label, page class, or the phase in which the page gets built)
PAGES = [
    ("Dashboard", 4),
    ("Blocking", BlockingPage),
    ("Anti-Bypass", 7),
    ("Screen Time", 4),
    ("Network Log", 5),
    ("Modes", 6),
    ("Notifications", NotificationsPage),
    ("Settings", 8),
]
SERVICE_TIMEOUT_SEC = 15
EVENT_POLL_MS = 1000
WATCH_MS = 5000
MINIMIZE_MS = 250
GC_MS = 2000


class LockdownApp(ctk.CTk):
    def __init__(self, events: queue.Queue, start_hidden: bool = False):
        super().__init__()
        # Tk objects may only be touched from this thread. Automatic garbage collection can run in any thread
        # (and then free a Tk font/image there -> hang), so collect here, periodically, instead.
        gc.disable()
        self._collect_garbage()
        ctk.set_appearance_mode("dark")
        self.title("Lockdown")
        self.geometry("1100x720")
        self.minsize(900, 560)
        if start_hidden:
            self.withdraw()

        self.db = Database()
        self.draft = Draft(self.db)
        self.draft.listeners.append(self._on_draft_change)
        self.service_running = False
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.last_event_id = self.db.last_block_event_id()  # only notify about new visits
        self.last_alert: dict[int, float] = {}               # item id -> when last notified
        self.popup: Popup | None = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        right = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        self._build_save_bar(right)
        self.content = ctk.CTkFrame(right, corner_radius=0, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        # Tray / second-launch callbacks arrive on other threads; hand them to Tk via a queue.
        self.events = events
        self.tray = Tray(on_open=lambda: self.events.put("open"), on_exit=lambda: self.events.put("exit"))
        self.tray.start()
        self.protocol("WM_DELETE_WINDOW", self.withdraw)  # close = minimize to tray

        self.show_page("Blocking")
        self._update_save_bar()
        self._poll_events()
        self._poll_status()
        self._poll_block_events()
        self.usage_tracker = UsageTracker()   # counts time on blocked sites/apps (limits, allowances)
        self.usage_tracker.start()
        self.watcher = alerts.BlockWatcher()
        self.minimize_blocks: dict[str, dict] = {}   # exe -> block, for apps blocked with "Minimize"
        self._poll_watcher()
        self._poll_minimize()

    def _collect_garbage(self):
        gc.collect()
        self.after(GC_MS, self._collect_garbage)

    def _build_save_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="e", padx=24, pady=(12, 0))
        self.autosave_var = ctk.BooleanVar(value=self.draft.autosave)
        ctk.CTkSwitch(bar, text="Auto-save", variable=self.autosave_var, command=self._toggle_autosave).pack(
            side="left", padx=(0, 16))
        self.discard_btn = ctk.CTkButton(bar, text="Discard", width=80, fg_color="transparent", border_width=1,
                                         command=self.draft.discard)
        self.save_btn = ctk.CTkButton(bar, text="Save changes", width=120, command=self.draft.save)
        self.save_widgets = [self.discard_btn, self.save_btn]

    def _toggle_autosave(self):
        self.draft.autosave = self.autosave_var.get()
        self._update_save_bar()

    def _update_save_bar(self):
        for w in self.save_widgets:
            w.pack_forget()
        if self.draft.autosave:   # every change is saved at once: no Save/Discard needed
            return
        dirty = self.draft.dirty
        if dirty:
            self.discard_btn.pack(side="left", padx=4)
        self.save_btn.pack(side="left", padx=4)
        self.save_btn.configure(state="normal" if dirty else "disabled",
                                text="Save changes" if dirty else "Saved",
                                fg_color=("#3a7ebf", "#1f6aa5") if dirty else ("gray70", "gray30"))

    def _on_draft_change(self, kind: str):
        self._update_save_bar()
        if "Blocking" in self.pages:
            self.pages["Blocking"].refresh()
        if kind == "discarded" and "Notifications" in self.pages:
            self.pages["Notifications"].load()

    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, width=190, corner_radius=0)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="LOCKDOWN", font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=0, column=0, padx=20, pady=(24, 20), sticky="w")
        for i, (name, _) in enumerate(PAGES, start=1):
            btn = ctk.CTkButton(bar, text=name, anchor="w", height=36, corner_radius=6,
                                fg_color="transparent", text_color=("gray10", "gray90"),
                                hover_color=("gray75", "gray25"), command=lambda n=name: self.show_page(n))
            btn.grid(row=i, column=0, padx=10, pady=2, sticky="ew")
            self.nav_buttons[name] = btn
        bar.grid_rowconfigure(len(PAGES) + 1, weight=1)
        self.status_label = ctk.CTkLabel(bar, text="", justify="left", anchor="w")
        self.status_label.grid(row=len(PAGES) + 2, column=0, padx=20, pady=20, sticky="w")

    def show_page(self, name: str):
        if name not in self.pages:
            spec = dict(PAGES)[name]
            if isinstance(spec, int):
                page = ctk.CTkFrame(self.content, fg_color="transparent")
                ctk.CTkLabel(page, text=name, font=ctk.CTkFont(size=24, weight="bold")).pack(
                    anchor="w", padx=30, pady=(24, 8))
                ctk.CTkLabel(page, text=f"Coming in Phase {spec}.", text_color="gray60").pack(anchor="w", padx=30)
            else:
                page = spec(self.content, self)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page
        self.pages[name].tkraise()
        for n, btn in self.nav_buttons.items():
            btn.configure(fg_color=("gray75", "gray25") if n == name else "transparent")

    def _poll_events(self):
        while not self.events.empty():
            event = self.events.get()
            if event == "open":
                self.deiconify()
                self.lift()
                self.focus_force()
            elif event == "exit":
                self.tray.stop()
                self.destroy()
                return
        self.after(200, self._poll_events)

    def _poll_status(self):
        heartbeat = float(self.db.get_setting(HEARTBEAT_KEY, "0"))
        running = time.time() - heartbeat < SERVICE_TIMEOUT_SEC
        blocked = len(self.db.list_items())
        if running != self.service_running:
            self.service_running = running
            if "Blocking" in self.pages:
                self.pages["Blocking"].refresh()  # status column depends on it
        self.status_label.configure(
            text=("● Service running" if running else "● Service not running"),
            text_color=("#3fb950" if running else "#f85149"))
        self.tray.update(running, f"{blocked} sites/apps blocked")
        self.after(3000, self._poll_status)

    def _poll_block_events(self):
        events = self.db.block_events_after(self.last_event_id)
        if events:
            self.last_event_id = events[-1]["id"]
            notify_override = {i["id"]: i["notify"] for i in self.db.list_items()}
            for event in events:
                if event["hostname"].endswith(".exe"):
                    win.close_app(event["hostname"])   # ask nicely; the service force-closes after 10 s
                self._alert(event, notify_override.get(event["item_id"]))
            if "Notifications" in self.pages:
                self.pages["Notifications"].refresh()
        self.after(EVENT_POLL_MS, self._poll_block_events)

    def _alert(self, event: dict, item_notify: str | None):
        now = time.time()
        enabled = alerts.get(self.db, f"notify.enabled.{event['reason']}") == "1"
        cooldown = int(alerts.get(self.db, "notify.cooldown_min"))
        if not alerts.should_notify(event, item_notify, enabled, self.last_alert.get(event["item_id"]), now, cooldown):
            return
        self.last_alert[event["item_id"]] = now
        self._show(alerts.format_message(alerts.get(self.db, f"notify.msg.{event['reason']}"), event, now_from_db(self.db)))

    def _poll_watcher(self):
        """Warnings before blocks start, reminders while in use, "block started" notices."""
        try:
            now = now_from_db(self.db)
            settings = {k: alerts.get(self.db, k) for k in alerts.DEFAULTS}
            for message in self.watcher.check(self.db.list_items(), self.db.list_groups(), self.db.usage_lookup(now),
                                              now, self.usage_tracker.in_use, settings):
                self._show(message)
            self.minimize_blocks = {b["item"]["target"].lower(): b for b in self.db.blocks(now)
                                    if b["item"]["item_type"] == "app" and minimizes(b["item"]["block_type"])}
        finally:
            self.after(WATCH_MS, self._poll_watcher)

    def _poll_minimize(self):
        """Apps blocked with "Minimize": keep them running, but minimize them whenever they come to the front."""
        try:
            if self.minimize_blocks:
                _hwnd, exe = win.foreground()
                block = self.minimize_blocks.get(exe)
                if block and win.minimize_app(exe):
                    item = block["item"]
                    until = block["until"].strftime(TIME_FMT) if block["until"] else None
                    self._alert({"item_id": item["id"], "display_name": item["display_name"], "reason": block["reason"],
                                 "until": until}, item["notify"])
        finally:
            self.after(MINIMIZE_MS, self._poll_minimize)

    def _show(self, message: str):
        fmt = alerts.get(self.db, "notify.format")
        if fmt in ("toast", "both"):
            self.tray.notify(message)
        if fmt in ("inapp", "both"):
            if self.popup:
                self.popup.close()
            self.popup = Popup(self, message)
