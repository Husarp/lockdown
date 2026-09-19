"""Main window (sidebar navigation + content area) and tray agent duties (blocked-visit notifications)."""
import gc
import queue
import time

# Import COM libraries on the main thread: background threads importing them at the same time can deadlock.
import comtypes.client  # noqa: F401
import uiautomation  # noqa: F401

import customtkinter as ctk

import alerts
import antibypass
import modes
import reminders
from blocker.apps import minimizes
from db import Database
from gui import theme
from gui.antibypass_page import AntiBypassPage, ChallengeWindow
from gui.blocking import BlockingPage
from gui.dashboard import DashboardPage
from gui.draft import Draft
from gui.modes_page import ModesPage
from gui.network import NetworkPage
from gui.notifications import NotificationsPage, Popup
from gui.reminders_ui import ReminderUI
from gui.screen_time import ScreenTimePage
from gui.settings import SettingsPage
from gui.tray import Tray
from monitor import win
from monitor.usage import UsageTracker
from rules import TIME_FMT
from service import HEARTBEAT_KEY
from trusted_time import now_from_db

# (sidebar label, page class or the phase in which the page gets built, icon)
PAGES = [
    ("Dashboard", DashboardPage, "layout-dashboard"),
    ("Blocking", BlockingPage, "ban"),
    ("Anti-Bypass", AntiBypassPage, "shield-check"),
    ("Screen Time", ScreenTimePage, "bar-chart-3"),
    ("Network Log", NetworkPage, "activity"),
    ("Modes", ModesPage, "sliders-horizontal"),
    ("Notifications", NotificationsPage, "bell"),
    ("Settings", SettingsPage, "settings"),
]
APPEARANCE_KEY = "ui.appearance"
APPEARANCES = {"Dark": "dark", "Light": "light", "Match Windows": "system"}
SERVICE_TIMEOUT_SEC = 15
EVENT_POLL_MS = 1000
WATCH_MS = 5000
MINIMIZE_MS = 250
GC_MS = 2000


class LockdownApp(ctk.CTk):
    def __init__(self, events: queue.Queue, start_hidden: bool = False):
        theme.apply()
        self.db = Database()
        ctk.set_appearance_mode(self.db.get_setting(APPEARANCE_KEY, "dark"))
        super().__init__()
        # Tk objects may only be touched from this thread. Automatic garbage collection can run in any thread
        # (and then free a Tk font/image there -> hang), so collect here, periodically, instead.
        gc.disable()
        self._collect_garbage()
        self.title("Lockdown")
        self.geometry("1100x720")
        self.minsize(900, 560)
        if start_hidden:
            self.withdraw()

        self.draft = Draft(self.db)
        self.draft.listeners.append(self._on_draft_change)
        self.draft.guard = self.guard
        self.challenge: ChallengeWindow | None = None
        self.exited = False
        self.db.set_setting(antibypass.EXITED_KEY, "0")
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
        self.tray = Tray(on_open=lambda: self.events.put("open"), on_exit=lambda: self.events.put("exit"),
                         on_mode=lambda mode_id: self.events.put(("mode", mode_id)))
        self.last_phase = None   # Pomodoro phase last announced
        self.tray.start()
        self.protocol("WM_DELETE_WINDOW", self.withdraw)  # close = minimize to tray

        self.show_page("Dashboard")
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
        self.reminder_ui = ReminderUI(self)
        self.reminders = self.reminder_ui.engine = reminders.Engine(self.db, self.reminder_ui)
        self.after(reminders.TICK_SEC * 1000, self._poll_reminders)

    def _collect_garbage(self):
        gc.collect()
        self.after(GC_MS, self._collect_garbage)

    def _build_save_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="e", padx=24, pady=(12, 0))
        self.autosave_var = ctk.BooleanVar(value=self.draft.autosave)
        ctk.CTkSwitch(bar, text="Auto-save", variable=self.autosave_var, command=self._toggle_autosave).pack(
            side="left", padx=(0, 16))
        self.discard_btn = ctk.CTkButton(bar, text="Discard", width=80, **theme.OUTLINE, command=self.draft.discard)
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
                                fg_color=theme.ACCENT if dirty else theme.SURFACE2)

    def _on_draft_change(self, kind: str):
        self._update_save_bar()
        if "Blocking" in self.pages:
            self.pages["Blocking"].refresh()
        if kind == "discarded" and "Notifications" in self.pages:
            self.pages["Notifications"].load()

    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, width=190, corner_radius=0, fg_color=theme.SIDEBAR)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        logo = ctk.CTkFrame(bar, fg_color="transparent")
        logo.grid(row=0, column=0, padx=20, pady=(20, 16), sticky="w")
        for text, color in (("LOCK", theme.TEXT), ("DOWN", theme.ACCENT)):
            ctk.CTkLabel(logo, text=text, text_color=color, font=ctk.CTkFont(theme.DISPLAY_HEAVY, 24)).pack(side="left")
        self.nav_icons = {}
        for i, (name, _, icon) in enumerate(PAGES, start=1):
            row = ctk.CTkFrame(bar, fg_color="transparent", corner_radius=6, height=36)
            row.grid(row=i, column=0, padx=12, pady=2, sticky="ew")
            marker = ctk.CTkFrame(row, width=3, height=36, corner_radius=0, fg_color="transparent")
            marker.pack(side="left", fill="y")
            self.nav_icons[name] = (theme.icon(icon, theme.MUTED, 17), theme.icon(icon, theme.TEXT, 17))
            btn = ctk.CTkButton(row, text=f"  {name}", image=self.nav_icons[name][0], anchor="w", height=36,
                                corner_radius=6, fg_color="transparent", text_color=theme.MUTED,
                                hover_color=theme.SURFACE2, font=ctk.CTkFont(theme.BODY, 13),
                                command=lambda n=name: self.show_page(n))
            btn.pack(side="left", fill="x", expand=True)
            self.nav_buttons[name] = (btn, marker)
        bar.grid_rowconfigure(len(PAGES) + 1, weight=1)
        self.status_label = ctk.CTkLabel(bar, text="", justify="left", anchor="w", font=ctk.CTkFont(theme.BODY, 12))
        self.status_label.grid(row=len(PAGES) + 2, column=0, padx=22, pady=16, sticky="w")

    def set_appearance(self, label: str):
        """Dark / Light / Match Windows. Charts (plain Tk canvases) are redrawn in the new colours."""
        mode = APPEARANCES[label]
        self.db.set_setting(APPEARANCE_KEY, mode)
        ctk.set_appearance_mode(mode)
        for page in self.pages.values():
            if hasattr(page, "refresh"):
                page.refresh()

    def show_page(self, name: str):
        if name not in self.pages:
            spec = next(p[1] for p in PAGES if p[0] == name)
            if isinstance(spec, int):
                page = ctk.CTkFrame(self.content, fg_color="transparent")
                ctk.CTkLabel(page, text=name, font=theme.page_title()).pack(anchor="w", padx=30, pady=(16, 8))
                ctk.CTkLabel(page, text=f"Coming in Phase {spec}.", text_color=theme.MUTED).pack(anchor="w", padx=30)
            else:
                page = spec(self.content, self)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page
        self.pages[name].tkraise()
        self.current_page = name
        if hasattr(self.pages[name], "on_show"):
            self.pages[name].on_show()
        for n, (btn, marker) in self.nav_buttons.items():
            on = n == name
            btn.configure(fg_color=theme.NAV_ACTIVE if on else "transparent",
                          text_color=theme.TEXT if on else theme.MUTED, image=self.nav_icons[n][1 if on else 0])
            marker.configure(fg_color=theme.ACCENT if on else "transparent")

    def _poll_events(self):
        while not self.events.empty():
            event = self.events.get()
            if event == "open":
                self.deiconify()
                self.lift()
                self.focus_force()
            elif event == "exit":
                self.guard(["Quit Lockdown (blocked-visit notices, time limits and reminders stop until you "
                            "start it again)"], self._exit)
                if self.exited:
                    return
            elif isinstance(event, tuple) and event[0] == "mode":   # from the tray menu
                try:
                    if event[1]:
                        self.start_mode(next(m for m in modes.load(self.db) if m["id"] == event[1]), None, False)
                    else:
                        self.stop_mode()
                except (ValueError, StopIteration) as e:
                    self._show(str(e) or "That mode doesn't exist any more.", force=True)
        self.after(200, self._poll_events)

    def _exit(self):
        self.exited = True
        self.db.set_setting(antibypass.EXITED_KEY, "1")
        self.tray.stop()
        self.destroy()

    def guard(self, changes: list[str], proceed, cancel=lambda: None):
        """Anti-Bypass: run `proceed` if loosening is allowed now, else show the challenge first."""
        if antibypass.status(antibypass.settings(self.db), now_from_db(self.db)) == "free":
            proceed()
            return
        if self.challenge and self.challenge.winfo_exists():
            self.challenge.destroy()
        self.deiconify()   # (tray Exit while the window is hidden)
        self.challenge = ChallengeWindow(self, changes, proceed, cancel)

    def refresh_antibypass(self):
        if "Anti-Bypass" in self.pages:
            self.pages["Anti-Bypass"].refresh()

    def _poll_status(self):
        heartbeat = float(self.db.get_setting(HEARTBEAT_KEY, "0"))
        running = time.time() - heartbeat < SERVICE_TIMEOUT_SEC
        blocked = len(self.db.list_items())
        if running != self.service_running:
            self.service_running = running
            for page in ("Blocking", "Dashboard"):   # statuses / the "not enforced" banner depend on it
                if page in self.pages:
                    self.pages[page].refresh()
        self.status_label.configure(
            text=("● Service running" if running else "● Service not running"),
            text_color=(theme.SUCCESS if running else theme.DANGER))
        state = modes.active(self.db, now_from_db(self.db))
        mode_text = f" · {state['mode']['name']} mode" if state else ""
        self.tray.update(running, f"{blocked} sites/apps blocked{mode_text}")
        self.tray.set_modes([(m["id"], m["name"]) for m in modes.load(self.db)],
                            state["mode"]["id"] if state else None)
        self.after(3000, self._poll_status)

    def _poll_status_once(self):
        state = modes.active(self.db, now_from_db(self.db))
        self.tray.set_modes([(m["id"], m["name"]) for m in modes.load(self.db)],
                            state["mode"]["id"] if state else None)

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
        reason = alerts.base_reason(event["reason"])
        enabled = alerts.get(self.db, f"notify.enabled.{reason}") == "1"
        cooldown = int(alerts.get(self.db, "notify.cooldown_min"))
        if not alerts.should_notify(event, item_notify, enabled, self.last_alert.get(event["item_id"]), now, cooldown):
            return
        self.last_alert[event["item_id"]] = now
        self._show(alerts.format_message(alerts.get(self.db, f"notify.msg.{reason}"), event, now_from_db(self.db)))

    def _poll_watcher(self):
        """Warnings before blocks start, reminders while in use, "block started" notices."""
        try:
            now = now_from_db(self.db)
            settings = {k: alerts.get(self.db, k) for k in alerts.DEFAULTS}
            self._announce_phase(now)
            for message in self.watcher.check(self.db.list_items(), self.db.list_groups(), self.db.usage_lookup(now),
                                              now, self.usage_tracker.in_use, settings):
                self._show(message)
            self.minimize_blocks = {b["item"]["target"].lower(): b for b in self.db.blocks(now)
                                    if b["item"]["item_type"] == "app" and minimizes(b["item"]["block_type"])}
        finally:
            self.after(WATCH_MS, self._poll_watcher)

    def _poll_reminders(self):
        """Sleep / break / your reminders. Popups wait while a full-screen app (a game) is in front."""
        try:
            now = now_from_db(self.db)
            state = modes.active(self.db, now)
            quiet = bool(state and state["mode"].get("mute"))   # Do Not Disturb: hold popups like in a game
            self.reminders.tick(now, win.idle_seconds(), win.is_fullscreen() or quiet)
        finally:
            self.after(reminders.TICK_SEC * 1000, self._poll_reminders)

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

    # ---------- modes ----------

    def start_mode(self, mode: dict, until, locked: bool):
        now = now_from_db(self.db)
        current = modes.active(self.db, now)
        if current and current["locked"] and not current["scheduled"]:
            raise ValueError(f"{current['mode']['name']} is locked until {current['until']:%H:%M}.")
        modes.start(self.db, mode["id"], now, until, locked)
        state = modes.active(self.db, now)
        end = state["until"] if state else until
        self._show(f"{mode['name']} mode on" + (f" until {end:%H:%M}" if end else "") +
                   (" (locked)" if locked and end else ""), force=True)
        self._mode_changed()

    def stop_mode(self):
        now = now_from_db(self.db)
        state = modes.active(self.db, now)
        modes.stop(self.db, now)
        if state and not state["scheduled"]:
            self._show(f"{state['mode']['name']} mode off", force=True)
        self._mode_changed()

    def _mode_changed(self):
        self.last_phase = None
        for page in ("Blocking", "Dashboard", "Modes"):
            if page in self.pages:
                self.pages[page].refresh()
        self._poll_status_once()

    def _announce_phase(self, now):
        """Pomodoro: say when a break starts and when focus is back."""
        state = modes.active(self.db, now)
        phase = (state["mode"]["id"], state["started"], state["phase"][0], state["phase"][2]) \
            if state and state["phase"] else None
        if phase and self.last_phase and phase != self.last_phase:
            name, end, rnd = state["phase"]
            if name == "focus":
                self._show(f"Focus - round {rnd} of {state['mode']['pomodoro']['rounds']}, until {end:%H:%M}. "
                           "Blocks are back on.", force=True)
            else:
                self._show(f"{name.capitalize()} until {end:%H:%M} - blocks are off.", force=True)
        elif self.last_phase and not phase:
            self._show("Focus session done.", force=True)
        self.last_phase = phase

    def _show(self, message: str, force: bool = False):
        """Notification in the chosen format. Muted while a mode with "mute" is on (unless force)."""
        if not force:
            state = modes.active(self.db, now_from_db(self.db))
            if state and state["mode"].get("mute"):
                return
        fmt = alerts.get(self.db, "notify.format")
        if fmt in ("toast", "both"):
            self.tray.notify(message)
        if fmt in ("inapp", "both"):
            if self.popup:
                self.popup.close()
            self.popup = Popup(self, message)
