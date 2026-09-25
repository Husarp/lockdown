"""Main window (sidebar navigation + content area) and tray agent duties (blocked-visit notifications)."""
import gc
import queue
import threading
import time
import traceback

# Import COM libraries on the main thread: background threads importing them at the same time can deadlock.
import comtypes.client  # noqa: F401
import uiautomation  # noqa: F401

import customtkinter as ctk

import alerts
import antibypass
import digest
from importer import distracting
import modes
import reminders
import updates
from blocker import protection
from blocker.apps import minimizes
from db import Database
from gui import shortcuts, theme
from gui.about_page import AboutPage
from gui.antibypass_page import AntiBypassPage, ChallengeWindow
from gui.blocking import BlockingPage
from gui.components import Curtain, page_head, PulseDot
from gui.dashboard import DashboardPage
from gui.draft import Draft
from gui.modes_page import ModesPage
from gui.network import NetworkPage
from gui.notifications import NotificationsPage, Popup
from gui.reminders_ui import ReminderUI, RemindersPage
from gui.screen_time import ScreenTimePage
from gui.settings import SettingsPage
from gui.tray import Tray
from monitor import win
from monitor.usage import UsageTracker
from monitor.word_guard import WordGuard
from rules import DAY_NAMES, TIME_FMT
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
    ("Reminders", RemindersPage, "hourglass"),
    ("Notifications", NotificationsPage, "bell"),
    ("Settings", SettingsPage, "settings"),
    ("About", AboutPage, "book-open"),
]
APPEARANCE_KEY = "ui.appearance"   # customtkinter mode (dark / light / system); the theme itself: theme.THEME_KEY
# The pages are laid out for this much room at 100%. On a screen that can't give them that (a laptop, or a
# high-DPI one where Windows makes every widget bigger) everything is drawn smaller so the cards still fit.
# It is decided ONCE, when the app starts: customtkinter re-scales by walking every widget it has ever made,
# which takes about 4 ms each - some 8 seconds for a window with every page built. Not something to do while
# you drag a window. "Interface size" in Settings overrides it, and applies when you restart.
LAYOUT_W, LAYOUT_H = 1280, 780
MIN_SCALE = 0.62        # below this it would be unreadable
SERVICE_TIMEOUT_SEC = 15
EVENT_POLL_MS = 1000
WATCH_MS = 5000
UPDATE_FIRST_MS = 90_000      # let the app settle before touching the network
UPDATE_POLL_MS = 3_600_000    # then look every hour whether a day has passed since the last check
MINIMIZE_MS = 250
GC_MS = 2000
GRACE_SEC = 10   # after a tightening change, this long to undo it (revert only) without the Anti-Bypass challenge
WORDS_BATCH_MS = 2500   # more tabs closed for blocked words within this: one summary notice instead of one each
TOAST_CLEAR_MS = 7000   # after a Windows notification, remove Lockdown's Action Center entries (bell) this much later
MUTE_S = 3600           # the popup's "Mute 1 h"
APP_ID = "com.husarp.lockdown"   # Windows app identity (matches main.py); used to clear only our own notifications
PREBUILD_MS = (3000, 500)   # build the other pages in the background: first after 3 s, then one every 0.5 s


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
        self.iconbitmap(default=str(theme.APP_ICON))   # (default=: every Lockdown window gets the logo)
        shortcuts.install(self)   # Esc closes pop-ups; Ctrl+Z / Ctrl+Backspace / ... in text boxes
        self.geometry("1100x720")
        self.minsize(560, 420)
        self.scaling = self._pick_scaling()
        if start_hidden:
            self.withdraw()

        self.draft = Draft(self.db)
        self.draft.listeners.append(self._on_draft_change)
        self.draft.guard = self.guard
        self.challenge: ChallengeWindow | None = None
        self._last_error = ("", 0.0)     # (last logged error, when) - see report_callback_exception
        self.word_batch: list | None = None   # tabs closed for blocked words since the last notice (None = none open)
        self.exited = False
        self.db.set_setting(antibypass.EXITED_KEY, "0")
        self.service_running = False
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.last_event_id = self.db.last_block_event_id()  # only notify about new visits
        self.last_alert: dict[int, float] = {}               # item id -> when last notified
        self._grace: dict[str, tuple] = {}                   # domain -> (revert-to state, expiry) for grace-undo
        self.popup: Popup | None = None
        self.muted_until = 0.0                               # popup "Mute 1 h": no alerts until this time.time()

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
        self.curtain = Curtain(self.content)   # hides a page while it's being drawn

        # Tray / second-launch callbacks arrive on other threads; hand them to Tk via a queue.
        self.events = events
        self.tray = Tray(on_open=lambda: self.events.put("open"), on_exit=lambda: self.events.put("exit"),
                         on_mode=lambda mode_id: self.events.put(("mode", mode_id)))
        self.last_phase = None   # Pomodoro phase last announced
        self.tray.start()
        self.protocol("WM_DELETE_WINDOW", self.withdraw)  # close = minimize to tray
        # A pop-up that holds the focus (grab) makes Tk ignore the taskbar's / Alt+Tab's "restore" of the minimized
        # window: let go of it while minimized, take it back when the window is restored.
        self.held_grab = None   # (checked in _poll_events; doing it in <Unmap> / <Map> events can crash Tk)

        self.show_page("Dashboard")
        self._update_save_bar()
        self._poll_events()
        self._poll_status()
        self._poll_block_events()
        self.usage_tracker = UsageTracker()   # counts time on blocked sites/apps (limits, allowances)
        self.usage_tracker.start()
        self.word_guard = WordGuard(lambda word, action: self.events.put(("words", word, action)))
        self.word_guard.start()   # bad-word check of the browser tab in front (Protection tab)
        self.watcher = alerts.BlockWatcher()
        self.minimize_blocks: dict[str, dict] = {}   # exe -> block, for apps blocked with "Minimize"
        self._poll_watcher()
        self._poll_minimize()
        self.reminder_ui = ReminderUI(self)
        self.reminders = self.reminder_ui.engine = reminders.Engine(self.db, self.reminder_ui)
        self.after(reminders.TICK_SEC * 1000, self._poll_reminders)
        self.after(PREBUILD_MS[0], self._prebuild)
        distracting.seed(self.db)          # games, streaming ... are Distracting by default
        self.after(5000, self._seed_games)
        self.after(UPDATE_FIRST_MS, self._poll_updates)

    def _check_grab(self):
        """Minimized with a pop-up holding the focus: let go (else the taskbar / Alt+Tab can't restore the window);
        take it back once the window is restored."""
        iconic = self.state() == "iconic"
        if iconic and self.held_grab is None and (grab := self.grab_current()):
            self.held_grab = grab
            grab.grab_release()
        elif not iconic and self.held_grab is not None:
            grab, self.held_grab = self.held_grab, None
            if grab.winfo_exists():
                grab.grab_set()
                grab.lift()

    def _pick_scaling(self) -> float:
        """How big to draw everything. "Interface size" if you set one, otherwise as much as this screen can
        show: the layout wants LAYOUT_W x LAYOUT_H of its own units, each `base_scaling` pixels wide."""
        self.base_scaling = ctk.ScalingTracker.get_widget_scaling(self)   # what Windows' own DPI asks for
        chosen = self.db.get_setting(theme.SIZE_KEY, "auto")
        if chosen != "auto" and chosen.isdigit():
            want = max(MIN_SCALE, min(1.0, int(chosen) / 100))
        else:
            want = max(MIN_SCALE, min(1.0, self.winfo_screenwidth() / (LAYOUT_W * self.base_scaling),
                                      (self.winfo_screenheight() - 80) / (LAYOUT_H * self.base_scaling)))
        if abs(want - 1.0) > 0.01:
            ctk.set_widget_scaling(want)
        return want

    def _tick_clock(self):
        if self.clock.winfo_exists():
            now = now_from_db(self.db)
            self.clock.configure(text=f"{DAY_NAMES[now.weekday()][:3]} {now:%H:%M}")
            self.after(10_000, self._tick_clock)

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
        status_row = ctk.CTkFrame(bar, fg_color="transparent")
        status_row.grid(row=len(PAGES) + 2, column=0, padx=18, pady=16, sticky="w")
        self.status_dot = PulseDot(status_row)
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_label = ctk.CTkLabel(status_row, text="", justify="left", anchor="w",
                                         font=ctk.CTkFont(theme.BODY, 12))
        self.status_label.pack(side="left")
        # the time, from Lockdown's own clock - the one the blocks go by, not Windows'
        self.clock = ctk.CTkLabel(bar, text="", text_color=theme.MUTED, anchor="w",
                                  font=ctk.CTkFont(theme.BODY, 12))
        self.clock.grid(row=len(PAGES) + 3, column=0, padx=18, pady=(0, 14), sticky="w")
        self._tick_clock()

    def set_appearance(self, label: str):
        """Dark / AMOLED / Light / Match Windows. Light / dark switch at once (charts - plain Tk canvases - are
        redrawn); AMOLED's black needs a restart (colours are fixed when widgets are made)."""
        choice = theme.THEMES[label]
        mode = theme.MODES[choice]
        self.db.set_setting(theme.THEME_KEY, choice)
        self.db.set_setting(APPEARANCE_KEY, mode)
        ctk.set_appearance_mode(mode)
        for page in self.pages.values():
            if hasattr(page, "refresh"):
                page.refresh()

    def _build_page(self, name: str):
        spec = next(p[1] for p in PAGES if p[0] == name)
        if isinstance(spec, int):
            page = ctk.CTkFrame(self.content, fg_color="transparent")
            page_head(page, name).pack(anchor="w", padx=30, pady=(16, 8))
            ctk.CTkLabel(page, text=f"Coming in Phase {spec}.", text_color=theme.MUTED).pack(anchor="w", padx=30)
        else:
            page = spec(self.content, self)
        page.grid(row=0, column=0, sticky="nsew")
        if name != getattr(self, "current_page", None):
            # built in the background, so take it back out of the layout: a page that is only hidden still gets
            # laid out and redrawn on every window resize, and with nine of them that is most of the work
            page.grid_remove()
        self.pages[name] = page

    def _seed_games(self):
        """Once the app list (loaded in the background) is there: Steam games are Distracting by default."""
        from gui import app_browser
        if app_browser._cache is None:
            self.after(5000, self._seed_games)
            return
        distracting.seed_games(self.db, [a["exe"] for a in app_browser._cache if a.get("steam")])

    def _prebuild(self):
        """Build pages you haven't opened yet, one at a time, so switching to them later is instant."""
        todo = [name for name, _spec, _icon in PAGES if name not in self.pages]
        if todo and not self.exited:
            self._build_page(todo[0])
            self.after(PREBUILD_MS[1], self._prebuild)

    def show_page(self, name: str):
        if name not in self.pages:
            self._build_page(name)
        for other, page in self.pages.items():
            if other != name and page.winfo_manager():
                page.grid_remove()
        self.pages[name].grid()
        self.pages[name].tkraise()
        if name != getattr(self, "current_page", None):
            self.curtain.cover()
        self.current_page = name
        if hasattr(self.pages[name], "on_show"):
            self.pages[name].on_show()
        for n, (btn, marker) in self.nav_buttons.items():
            on = n == name
            btn.configure(fg_color=theme.NAV_ACTIVE if on else "transparent",
                          text_color=theme.TEXT if on else theme.MUTED, image=self.nav_icons[n][1 if on else 0])
            marker.configure(fg_color=theme.ACCENT if on else "transparent")

    def _poll_events(self):
        self._check_grab()
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
            elif isinstance(event, tuple) and event[0] == "words":   # from the bad-word check
                self._word_notice(event[1], event[2])
            elif isinstance(event, tuple) and event[0] == "mode":   # from the tray menu
                try:
                    if event[1]:
                        self.start_mode(next(m for m in modes.load(self.db) if m["id"] == event[1]), None, False)
                    else:
                        self.stop_mode()
                except (ValueError, StopIteration) as e:
                    self._show(str(e) or "That mode doesn't exist any more.", force=True)
        self.after(200, self._poll_events)

    def _word_notice(self, word: str, action: str):
        """The first tab closed shows a notice at once; more within WORDS_BATCH_MS become one summary."""
        if self.word_batch is not None:
            self.word_batch.append(word)
            return
        done = "closing the tab" if action == "close" else "going back"
        self._show(f'"{word.rstrip("*")}" is a blocked word - {done}.')
        self.word_batch = []
        self.after(WORDS_BATCH_MS, self._word_summary)

    def _word_summary(self):
        batch, self.word_batch = self.word_batch, None
        if batch:
            n = len(batch)
            self._show(f"Closed {n} more tab{'s' * (n > 1)} with blocked words.")
            self.word_batch = []
            self.after(WORDS_BATCH_MS, self._word_summary)

    def _exit(self):
        self.exited = True
        self.db.set_setting(antibypass.EXITED_KEY, "1")
        self.tray.stop()
        self.destroy()

    def restart(self):
        """Start Lockdown again (new theme / accent colour): a helper waits until this one is gone, then launches
        a fresh copy. CREATE_NO_WINDOW keeps the helper's console hidden (no flash); `ping` is the delay because
        it needs no console input, and it must outlast this process freeing its single-instance port."""
        import subprocess
        from paths import command_line, gui_command
        subprocess.Popen(f'ping -n 4 127.0.0.1 >nul & start "" {command_line(gui_command())}',
                         shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.tray.stop()
        self.destroy()

    def report_callback_exception(self, exc, value, tb):
        """Tk runs the whole GUI out of callbacks and throws away anything they raise, so a bug in one left no
        trace at all - the window just froze or went. Write it to the same log the service uses instead. The
        same error in a row is only written once, so a callback that fails on every frame can't fill the disk."""
        text = "".join(traceback.format_exception(exc, value, tb))
        first = text.strip().splitlines()[-1]
        now = time.time()
        if first == self._last_error[0] and now - self._last_error[1] < 60:
            return
        self._last_error = (first, now)
        try:
            from paths import LOG_PATH
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},000 ERROR Lockdown window: {text}")
        except OSError:
            pass

    def guard(self, changes: list[str], proceed, cancel=lambda: None, force: bool = False):
        """Anti-Bypass: run `proceed` if loosening is allowed now, else show the challenge first. force: ask even when
        no challenge is turned on (a locked mode) - unless the challenge was passed a few minutes ago."""
        cfg, now = antibypass.settings(self.db), now_from_db(self.db)
        if antibypass.status(cfg, now) == "free" and not (force and not antibypass.unlocked_until(cfg, now)):
            proceed()
            return
        if self.challenge and self.challenge.winfo_exists():
            # already asking about something else: keep that one (you may be halfway through typing the phrase)
            # and turn this request down, so whatever asked for it puts itself back
            self.challenge.lift()
            self.challenge.focus_force()
            cancel()
            return
        self.deiconify()   # (tray Exit while the window is hidden)
        self.challenge = ChallengeWindow(self, changes, proceed, cancel)

    def grace_note(self, domain: str, revert_to):
        """Remember the state to revert to after a tightening change, so an accidental toggle can be undone within
        a few seconds without the Anti-Bypass challenge (see grace_ok)."""
        self._grace[domain] = (revert_to, time.monotonic() + GRACE_SEC)

    def grace_ok(self, domain: str, new_state) -> bool:
        """True if `new_state` exactly reverts a tightening made in this domain in the last few seconds - then the
        loosening is allowed without the challenge (and the grace is spent)."""
        state, until = self._grace.get(domain, (None, 0))
        if state == new_state and time.monotonic() < until:
            del self._grace[domain]
            return True
        return False

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
        self.status_dot.set_state(running)
        self.status_label.configure(
            text=("Service running" if running else "Service not running"),
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
        self.after(EVENT_POLL_MS, self._poll_block_events)

    def _alert(self, event: dict, item_notify: str | None):
        now = time.time()
        reason = alerts.base_reason(event["reason"])
        enabled = alerts.get(self.db, f"notify.enabled.{reason}") == "1"
        cooldown = int(alerts.get(self.db, "notify.cooldown_min"))
        if not alerts.should_notify(event, item_notify, enabled, self.last_alert.get(event["item_id"]), now, cooldown):
            return
        self.last_alert[event["item_id"]] = now
        lists = protection.all_lists(protection.settings(self.db))   # (your own lists have their own names)
        self._show(alerts.format_message(alerts.get(self.db, f"notify.msg.{reason}"), event, now_from_db(self.db),
                                         lists))

    def _poll_watcher(self):
        """Warnings before blocks start, reminders while in use, "block started" notices."""
        try:
            if antibypass.is_off(self.db):     # switched off: nothing to warn about, nothing to minimise
                self.minimize_blocks = {}
                # forget what was blocked, so switching back on takes a fresh baseline instead of
                # announcing every rule you already had as if it had just started
                self.watcher.prev_blocked = None
                return
            now = now_from_db(self.db)
            settings = {k: alerts.get(self.db, k) for k in alerts.DEFAULTS}
            self._announce_phase(now)
            for message in self.watcher.check(self.db.list_items(), self.db.list_groups(), self.db.usage_lookup(now),
                                              now, self.usage_tracker.in_use, settings):
                self._show(message, force=message in self.watcher.urgent)
            self.minimize_blocks = {b["item"]["target"].lower(): b for b in self.db.blocks(now)
                                    if b["item"]["item_type"] == "app" and minimizes(b["item"]["block_type"])}
            if digest.due(self.db, now):   # the weekly summary
                from gui import appinfo
                from gui.dashboard import goal_seconds
                items = self.db.list_items()
                self._show(digest.summary(self.db, now.date(), goal_seconds(self.db),
                                          lambda kind, name: appinfo.name_of(kind, name, items)))
                digest.mark_shown(self.db, now)
        finally:
            self.after(WATCH_MS, self._poll_watcher)

    def _poll_reminders(self):
        """Sleep / break / your reminders. Popups wait while a full-screen app (a game) is in front, and
        everything waits - the bedtime screen too - while Do not disturb is on."""
        try:
            if antibypass.is_off(self.db):     # switched off: bedtime, breaks and your reminders all stop
                return
            now = now_from_db(self.db)
            state = modes.active(self.db, now)
            quiet = bool(state and state["mode"].get("mute")) or win.do_not_disturb()
            self.reminders.tick(now, win.idle_seconds(), win.is_fullscreen(), quiet=quiet)
        finally:
            self.after(reminders.TICK_SEC * 1000, self._poll_reminders)

    def _poll_updates(self):
        """Ask GitHub once a day whether there is a newer Lockdown, and say so once per version. Off entirely
        when you untick it on the About page. The request goes out on its own thread - the network must never
        hold up the window."""
        try:
            if updates.due(self.db, now_from_db(self.db)):
                threading.Thread(target=self._ask_github, daemon=True).start()
        finally:
            self.after(UPDATE_POLL_MS, self._poll_updates)

    def _ask_github(self):
        found = updates.latest_release()
        self.after(0, self._update_found, found)

    def _update_found(self, found):
        if found:                       # a failed check is not written down, so it tries again on the next poll
            updates.checked(self.db, now_from_db(self.db))
        if not updates.worth_saying(self.db, found):
            return
        updates.said(self.db, found["version"])
        where = "About" if updates.can_install(found) else "the GitHub page"
        self._show(f"Lockdown {found['version']} is out - open Lockdown and go to {where} to install it.",
                   actions=False)      # "Mute 1 h" is not an answer to this, and it is not urgent either

    def _poll_minimize(self):
        """Apps blocked with "Minimize": keep them running, but minimize them whenever they come to the front.
        Also enforces a strict break: while one is on, anything brought to the front is sent back down."""
        try:
            self._enforce_break()
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

    def _enforce_break(self):
        ui = getattr(self, "reminder_ui", None)
        until = getattr(ui, "break_until", None) if ui else None
        if not until:
            return
        now = now_from_db(self.db)
        if now >= until:   # safety net; the reminders engine also ends it on its own tick
            return
        if win.minimize_foreground():   # you tried to open something - send it back and say why
            if now.timestamp() - getattr(self, "_break_notice_at", 0) > 12:
                self._break_notice_at = now.timestamp()
                self._show(f"Break in progress - {max(1, round((until - now).total_seconds() / 60))} min left.",
                           force=True)

    # ---------- modes ----------

    def start_mode(self, mode: dict, until, locked: bool):
        now = now_from_db(self.db)
        current = modes.active(self.db, now)
        if current and current["locked"] and not current["scheduled"]:   # replacing a locked mode: challenge first
            self.guard([f"End the locked {current['mode']['name']} mode (locked until {current['until']:%H:%M}) "
                        f"and start {mode['name']}"], lambda: self._start_mode(mode, until, locked), force=True)
            return
        self._start_mode(mode, until, locked)

    def _start_mode(self, mode: dict, until, locked: bool):
        now = now_from_db(self.db)
        modes.start(self.db, mode["id"], now, until, locked)
        state = modes.active(self.db, now)
        end = state["until"] if state else until
        self._show(f"{mode['name']} mode on" + (f" until {end:%H:%M}" if end else "") +
                   (" (locked)" if locked and end else ""), force=True)
        self._mode_changed()

    def stop_mode(self):
        now = now_from_db(self.db)
        state = modes.active(self.db, now)
        if state and state["locked"] and not state["scheduled"]:   # locked: only with the challenge
            self.guard([f"Stop the locked {state['mode']['name']} mode (locked until {state['until']:%H:%M})"],
                       lambda: self._stop_mode(force=True), force=True)
            return
        self._stop_mode()

    def _stop_mode(self, force: bool = False):
        now = now_from_db(self.db)
        state = modes.active(self.db, now)
        modes.stop(self.db, now, force)
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

    def _show(self, message: str, force: bool = False, actions: bool = True):
        """Notification in the chosen format. Muted while a mode with "mute" is on (unless force - what you
        are using right now is about to be blocked, which is worth saying even in a game).
        actions=False leaves off "Open Lockdown" / "Mute 1 h": a reminder telling you to look out of the
        window has nothing to open, and muting Lockdown is not the answer to it."""
        if not force:
            if time.time() < self.muted_until:
                return
            state = modes.active(self.db, now_from_db(self.db))
            if state and state["mode"].get("mute"):
                return
        fmt = alerts.get(self.db, "notify.format")
        if fmt in ("toast", "both"):
            self.tray.notify(message)
            self.after(TOAST_CLEAR_MS, self._clear_toast_history)   # don't let one-time alerts pile up as unread
        if fmt in ("inapp", "both"):
            self.popup = Popup(self, message,
                               on_open=(lambda: self.events.put("open")) if actions else None,
                               on_mute=self._mute if actions else None)

    def _mute(self):
        self.muted_until = time.time() + MUTE_S

    def _clear_toast_history(self):
        """Remove Lockdown's own notifications from the Windows Action Center (the bell), a few seconds after they
        show, so these one-time alerts don't stack up as "unread". Only Lockdown's AUMID is cleared - never any
        other app's notifications."""
        import os
        import subprocess
        ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          r"System32\WindowsPowerShell\v1.0\powershell.exe")
        cmd = ("$null=[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
               "ContentType=WindowsRuntime];"
               f"[Windows.UI.Notifications.ToastNotificationManager]::History.Clear('{APP_ID}')")
        try:
            subprocess.Popen([ps, "-NoProfile", "-NonInteractive", "-Command", cmd],
                             creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
        except OSError:
            pass
