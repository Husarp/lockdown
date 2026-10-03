"""Main window (sidebar navigation + content area) and tray agent duties (blocked-visit notifications)."""
import gc
import queue
import threading
import time
import traceback
import webbrowser
from datetime import datetime

# Import COM libraries on the main thread: background threads importing them at the same time can deadlock.
import comtypes.client  # noqa: F401
import uiautomation  # noqa: F401

import customtkinter as ctk

import alerts
import antibypass
import digest
from importer import distracting
import modes
import pause
import reminders
import retention
import updates
from blocker import protection
from blocker.apps import exe_name, minimizes
from db import Database
from gui import mainthread, shortcuts, theme, toast
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
from monitor import usage as usage_mod, win
from monitor.usage import UsageTracker
from monitor.word_guard import WordGuard
from rules import DAY_NAMES, TIME_FMT
from service import HEARTBEAT_KEY
from trusted_time import now_from_db
from version import VERSION

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
# A blocked visit to a site is announced only if a browser window in front showed that site from this long before
# the visit was seen until this long after (the tab check sends the tab back within a second; the address bar may
# be read a moment after the browser connected) - else it was a background hit and stays silent (0.84.10).
VISIT_FRONT_SEC = 10
VISIT_LAG_MAX_SEC = 30   # a visit seen late (this window busy) is looked for in front from up to this long before
WATCH_MS = 5000
UPDATE_FIRST_MS = 60_000      # let the app settle before touching the network, then check (0.84.4: every start)
UPDATE_POLL_MS = 60_000       # then every minute: is a check due (every updates.EVERY_HOURS), has a snooze run out
MINIMIZE_MS = 250
GRACE_SEC = 10   # after a tightening change, this long to undo it (revert only) without the Anti-Bypass challenge
WORDS_BATCH_MS = 2500   # more tabs closed for blocked words within this: one summary notice instead of one each
PREBUILD_MS = (3000, 500)   # build the other pages in the background: first after 3 s, then one every 0.5 s


class LockdownApp(ctk.CTk):
    def __init__(self, events: queue.Queue, start_hidden: bool = False):
        theme.apply()
        self.db = Database(ui=True)   # short busy timeout: a lock elsewhere must not freeze the window
        ctk.set_appearance_mode(self.db.get_setting(APPEARANCE_KEY, "dark"))
        super().__init__()
        # Tk objects may only be touched from this thread. Automatic garbage collection can run in any thread
        # (and then free a Tk font/image there -> hang), so it stays off and this thread collects instead -
        # generationally, with everything from startup frozen (see gui/mainthread.py), not a full sweep every 2 s.
        gc.disable()
        self._gc_tick = 0
        self._gc_frozen = False
        self._collect_garbage()
        # Worker threads never touch Tk: they hand their results to this queue, drained here on the Tk thread.
        self.calls = mainthread.CallQueue()
        self._drain_calls()
        self.title("Lockdown")
        self.iconbitmap(default=str(theme.APP_ICON))   # (default=: every Lockdown window gets the logo)
        shortcuts.install(self)   # Esc closes pop-ups; Ctrl+Z / Ctrl+Backspace / ... in text boxes
        self.geometry("1100x720")
        self.minsize(560, 420)
        self.scaling = self._pick_scaling()
        # Always maximized and non-resizable: the window opens full-size and can't be shrunk (it still keeps its
        # title bar, so minimise / close-to-tray work as normal). Set after the window exists so "zoomed" sticks.
        self.after(0, self._maximize)
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
        self.visits: dict[tuple, tuple] = {}                 # site visits waiting to be seen in front (_check_visits)
        self.last_alert = self._load_last_alert()            # item id -> when last notified (kept across restarts)
        self._grace: dict[str, tuple] = {}                   # domain -> (revert-to state, expiry) for grace-undo
        self.popup: Popup | None = None
        self.update_popup: Popup | None = None   # the "new version" notice, while it is on screen
        self.update_popup_for = None             # ... and which version it offers
        self.update_shown = None                 # (version, installable) the banner / dot / tray show now, or None
        self.update_bar = None                   # ... what the banner itself shows (None: hidden)
        self.update_closed = None                # the version whose banner was closed with x (until the next start)
        self.update_asking = False               # a check is out on its thread

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        right = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1)
        self._build_update_banner(right)
        self._build_pause_banner(right)
        self._build_save_bar(right)
        self.content = ctk.CTkFrame(right, corner_radius=0, fg_color="transparent")
        self.content.grid(row=3, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.curtain = Curtain(self.content)   # hides a page while it's being drawn

        # Tray / second-launch callbacks arrive on other threads; hand them to Tk via a queue.
        self.events = events
        self.tray = Tray(on_open=lambda: self.events.put("open"), on_exit=lambda: self.events.put("exit"),
                         on_mode=lambda mode_id: self.events.put(("mode", mode_id)),
                         on_update=lambda: self.events.put("update"),
                         on_pause=lambda: self.events.put("pause"), on_resume=lambda: self.events.put("resume"))
        self.last_phase = None   # Pomodoro phase last announced
        self.tray.start()
        # Windows notifications (with buttons); the tray's balloon if they can't be had here
        self.toaster = toast.Toaster(self.call_soon, self.tray.notify, on_click=lambda: self.events.put("open"))
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
        self.minimize_writes = usage_mod.limit_writes   # (re-read at once when the tracker reaches a limit)
        self._poll_watcher()
        self._poll_minimize()
        self.reminder_ui = ReminderUI(self)
        self.reminders = self.reminder_ui.engine = reminders.Engine(self.db, self.reminder_ui)
        self.after(reminders.TICK_SEC * 1000, self._poll_reminders)
        self.after(PREBUILD_MS[0], self._prebuild)
        distracting.seed(self.db)          # games, streaming ... are Distracting by default
        self.after(5000, self._seed_games)
        updates.remove_downloads()         # the installer an in-app update downloaded has done its job
        self.refresh_update(popup=False)   # a newer version found before: the banner is there from the start
        self.after(UPDATE_FIRST_MS, lambda: self._poll_updates(first=True))
        # old per-minute / per-event detail -> daily totals, once a day, on its own thread and connection
        self.retention = retention.RetentionThread()
        self.retention.start()

    def call_soon(self, fn, *args):
        """From any thread: run fn(*args) on the Tk thread shortly."""
        self.calls.post(fn, *args)

    def call_latest(self, key, fn, *args):
        """From any thread: like call_soon, but only the newest call per key runs (progress updates)."""
        self.calls.post_latest(key, fn, *args)

    def _drain_calls(self):
        try:
            self.calls.drain(on_error=lambda e: self.report_callback_exception(type(e), e, e.__traceback__))
        finally:
            self.after(mainthread.DRAIN_MS, self._drain_calls)

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

    def _maximize(self):
        """Open maximized and lock the size, so the window can't be made smaller (it keeps its title bar)."""
        try:
            self.resizable(False, False)
            self.state("zoomed")
        except Exception:
            pass

    def _tick_clock(self):
        if self.clock.winfo_exists():
            try:
                now = now_from_db(self.db)
                self.clock.configure(text=f"{DAY_NAMES[now.weekday()][:3]} {now:%H:%M}")
            finally:   # (one error must not stop the clock for the rest of the session)
                self.after(10_000, self._tick_clock)

    def _collect_garbage(self):
        try:
            self._gc_tick += 1
            mainthread.collect(self._gc_tick)
        finally:
            self.after(mainthread.GC_MS, self._collect_garbage)

    def _build_save_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="e", padx=24, pady=(12, 0))
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
        self.nav_plain = {}   # (the About icons without / with the "update waiting" dot - see refresh_update)
        for i, (name, _, icon) in enumerate(PAGES, start=1):
            row = ctk.CTkFrame(bar, fg_color="transparent", corner_radius=6, height=36)
            row.grid(row=i, column=0, padx=12, pady=2, sticky="ew")
            marker = ctk.CTkFrame(row, width=3, height=36, corner_radius=0, fg_color="transparent")
            marker.pack(side="left", fill="y")
            self.nav_icons[name] = (theme.icon(icon, theme.MUTED, 17), theme.icon(icon, theme.TEXT, 17))
            if name == "About":
                self.nav_plain = {False: self.nav_icons[name],
                                  True: (theme.icon(icon, theme.MUTED, 17, dot=theme.ACCENT),
                                         theme.icon(icon, theme.TEXT, 17, dot=theme.ACCENT))}
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
        elif not self._gc_frozen:   # every page is built: what is alive now lives as long as the app
            self._gc_frozen = True
            mainthread.freeze_startup()

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
        try:
            self._check_grab()
            while not self.events.empty():
                event = self.events.get()
                if event == "open":
                    self.deiconify()
                    self._maximize()   # reopen from the tray full-size, not at some leftover small size
                    self.lift()
                    self.focus_force()
                    self._check_updates(updates.OPEN_HOURS)   # opening the window: look again if it's been a while
                elif event == "pause":    # the tray's "Pause my blocks...": the card on the Anti-Bypass page
                    self._open_on("Anti-Bypass")
                elif event == "resume":   # the tray's "Resume blocking"
                    self.resume_blocks()
                elif event == "update":   # the tray's "Install update"
                    if found := updates.available(self.db):
                        self._install(found)
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
        finally:   # (an error must not stop tray Open / Exit / modes for the rest of the session)
            if not self.exited:
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
        self._shutdown()
        self.destroy()

    def _shutdown(self):
        """Before the window goes: stop the background jobs and write any setting still waiting for a lock."""
        self.retention.stop_event.set()
        self.usage_tracker.stop()   # (writes the time it has counted but not written yet - at most FLUSH_SEC of it)
        self.db.flush()
        self.tray.stop()

    def restart(self):
        """Start Lockdown again (new theme / accent colour): a helper waits until this one is gone, then launches
        a fresh copy. CREATE_NO_WINDOW keeps the helper's console hidden (no flash); `ping` is the delay because
        it needs no console input, and it must outlast this process freeing its single-instance port."""
        import subprocess
        from paths import command_line, gui_command
        self._shutdown()   # first: its flush can wait for a lock, and the helper's delay must start after it
        subprocess.Popen(f'ping -n 4 127.0.0.1 >nul & start "" {command_line(gui_command())}',
                         shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
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
        self.challenge.attributes("-topmost", True)   # sit above any overlay/popup so it's never hidden behind one
        self.challenge.lift()
        self.challenge.focus_force()

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
        try:
            heartbeat = float(self.db.get_setting(HEARTBEAT_KEY, "0"))
            running = time.time() - heartbeat < SERVICE_TIMEOUT_SEC
            blocked = self.db.item_count()   # (not list_items: 45 ms with 6 000 items, every 3 s)
            off = antibypass.is_off(self.db)
            paused = self.refresh_pause()
            if running != self.service_running or off != getattr(self, "was_off", None):
                self.service_running, self.was_off = running, off
                for page in ("Blocking", "Dashboard"):   # statuses / the "not enforced" banners depend on it
                    if page in self.pages:
                        self.pages[page].refresh()
            self.status_dot.set_state(running)
            self.status_label.configure(
                text=("Service running" if running else "Service not running"),
                text_color=(theme.SUCCESS if running else theme.DANGER))
            mode_list = modes.load(self.db)
            state = modes.active(self.db, now_from_db(self.db), mode_list)
            mode_text = f" · {state['mode']['name']} mode" if state else ""
            # (the tray only talks to its icon when something actually changed - see Tray.update)
            self.tray.update(running, "OFF - nothing is enforced" if off else
                             f"Blocking paused until {paused:%H:%M}" if paused else
                             f"{blocked} sites/apps blocked{mode_text}", off=off)
            self.tray.set_modes([(m["id"], m["name"]) for m in mode_list], state["mode"]["id"] if state else None)
        finally:
            self.after(3000, self._poll_status)

    def _poll_status_once(self):
        state = modes.active(self.db, now_from_db(self.db))
        self.tray.set_modes([(m["id"], m["name"]) for m in modes.load(self.db)],
                            state["mode"]["id"] if state else None)

    _ALERT_KEY = "notify.last_alert"

    def _load_last_alert(self) -> dict:
        """The per-item last-notified times, kept in settings so a restart honours the cooldown."""
        import json
        try:
            raw = json.loads(self.db.get_setting(self._ALERT_KEY, "") or "{}")
        except ValueError:
            return {}
        return {(None if k == "null" else int(k)): float(v) for k, v in raw.items()}

    def _save_last_alert(self):
        import json
        keep = time.time() - 7 * 86400   # drop entries older than a week so it can't grow forever
        data = {("null" if k is None else str(k)): v for k, v in self.last_alert.items() if v >= keep}
        self.db.set_setting(self._ALERT_KEY, json.dumps(data))

    def _poll_block_events(self):
        try:
            events = self.db.block_events_after(self.last_event_id)
            if events:
                self.last_event_id = events[-1]["id"]
                items = {i["id"]: i for i in self.db.list_items()}
                seen, now = time.monotonic(), now_from_db(self.db)
                for event in events:
                    item = items.get(event["item_id"]) or {}
                    if event["hostname"].endswith(".exe"):
                        win.close_app(event["hostname"])   # ask nicely; the service force-closes after 10 s
                        self._alert(event, item.get("notify"))
                    else:   # a site: only if you were opening it (0.84.10)
                        # (looked for in front from the visit's own time: this window may have been busy a while)
                        try:
                            lag = (now - datetime.strptime(event["timestamp"], TIME_FMT)).total_seconds()
                        except (TypeError, ValueError):
                            lag = 0.0
                        since = seen - min(max(lag, 0.0), VISIT_LAG_MAX_SEC) - VISIT_FRONT_SEC
                        self.visits[(event["item_id"], event["hostname"])] = (event, item, seen, since)
            self._check_visits()
        finally:
            self.after(EVENT_POLL_MS, self._poll_block_events)

    def _check_visits(self):
        """Announce a blocked site only when you tried to open it: a browser window in front (the focused one, or
        the one in front on another monitor) showed it within VISIT_FRONT_SEC of the visit. The service records
        every connection to a blocked site, and most are nobody opening it - a page embedding a YouTube video,
        thumbnails, Discord's link previews, a browser's preconnect, and since 0.84.9 the DNS filter answering
        for youtube.com whatever asks - so "YouTube is blocked" came up with YouTube nowhere on screen. Those
        stay in the network log and Blocked visits, silently."""
        now = time.monotonic()
        for key, (event, item, seen, since) in list(self.visits.items()):
            targets = item.get("target", "").split()
            if alerts.opened_in_front(event, usage_mod.shown_since(since), targets):
                del self.visits[key]
                self._alert(event, item.get("notify"))
            elif not 0 <= now - seen <= VISIT_FRONT_SEC:
                del self.visits[key]

    def _alert(self, event: dict, item_notify: str | None):
        now = time.time()
        reason = alerts.base_reason(event["reason"])
        enabled = alerts.get(self.db, f"notify.enabled.{reason}") == "1"
        cooldown = int(alerts.get(self.db, "notify.cooldown_min"))
        if not alerts.should_notify(event, item_notify, enabled, self.last_alert.get(event["item_id"]), now, cooldown):
            return
        self.last_alert[event["item_id"]] = now
        self._save_last_alert()   # so a restart doesn't forget the cooldown and re-notify at once
        lists = protection.list_names(self.db)   # (your own lists have their own names; parsed once, not per alert)
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
            now = now_from_db(self.db)   # one clock reading, one load of items / groups / usage for this tick
            settings = {k: alerts.get(self.db, k) for k in alerts.DEFAULTS}
            self._announce_phase(now)
            items, groups, usage = self.db.list_items(), self.db.list_groups(), self.db.usage_lookup(now)
            for message in self.watcher.check(items, groups, usage, now, self.usage_tracker.in_use, settings):
                self._show(message, force=message in self.watcher.urgent)
            self._read_minimize_blocks(now, usage, items, groups)
            if digest.due(self.db, now):   # the weekly summary
                from gui import appinfo
                from gui.dashboard import goal_seconds
                self._show(digest.summary(self.db, now.date(), goal_seconds(self.db),
                                          lambda kind, name: appinfo.name_of(kind, name, items)),
                           action=("See the week", lambda: self._open_on("Screen Time")))
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

    # ---------- a newer Lockdown ----------
    # Checked every time you open the window (at most once per updates.RETRY_MIN minutes), and in the background
    # every updates.EVERY_HOURS hours. What it finds stays on screen until installed or snoozed (the banner's x
    # hides only the banner, until the next start): the banner over every page, a dot on About, "Update available"
    # in the tray menu - plus the corner popup once per version (once more after "Remind me later"), which waits
    # while you don't want interruptions.

    def _poll_updates(self, first: bool = False):
        """Every minute, on the Tk thread: start a check if one is due (only the request goes out on its own
        thread - the network must never hold up the window), and bring the banner / popup back once a "Remind
        me later" has run out. Off entirely (no checks) when you untick it on the About page."""
        try:
            self._check_updates(updates.EVERY_HOURS)   # in the background only every EVERY_HOURS (Adam, 0.84.4)
            self.refresh_update()
        finally:
            self.after(UPDATE_POLL_MS, self._poll_updates)

    def _check_updates(self, hours: float):
        if self.update_asking:
            return
        now = now_from_db(self.db)
        if updates.due(self.db, now, hours):
            self.update_asking = True
            updates.trying(self.db, now)
            threading.Thread(target=self._ask_github, daemon=True).start()

    def _ask_github(self):
        """(worker thread) Never self.after() here - that is a Tk call from the wrong thread."""
        found = None
        try:
            found = updates.latest_release()
        finally:
            self.call_soon(self._update_found, found)

    def _update_found(self, found):
        self.update_asking = False
        if found:   # a failed check is not written down, so it tries again on a later poll
            updates.checked(self.db, now_from_db(self.db), found)
        self.refresh_update()

    def refresh_update(self, popup: bool = True):
        """Banner, About dot and tray line from what was found (cheap: nothing is rebuilt, and nothing at all is
        touched unless what to show changed). Then the popup, if this version still has to be told about."""
        now = now_from_db(self.db)
        found = updates.banner(self.db, now)
        shown = (found["version"], updates.can_install(found)) if found else None
        # the banner's x hides it until Lockdown is next started - kept here in memory, never in the database
        bar = shown if shown and shown[0] != getattr(self, "update_closed", None) else None
        if bar != getattr(self, "update_bar", None):
            self.update_bar = bar
            if bar:
                self.update_text.configure(text=updates.label(found))
                self.update_get.configure(text="Install" if bar[1] else "Open the page")
                self.update_banner.grid()
            else:
                self.update_banner.grid_remove()
        if shown != self.update_shown:
            self.update_shown = shown
            self.nav_icons["About"] = self.nav_plain[bool(found)]
            self.nav_buttons["About"][0].configure(
                image=self.nav_icons["About"][1 if getattr(self, "current_page", None) == "About" else 0])
            self.tray.set_update(found["version"] if found else None)
            if self.update_popup is not None and getattr(self, "update_popup_for", None) != (shown or (None,))[0]:
                # the popup still open is about a version no longer to be shown (a newer one came out while it
                # waited, or it was installed / snoozed): its Install would fetch the wrong release
                self._close_update_popup()   # (test_a_newer_release_replaces_the_open_popup)
        if popup and found and updates.worth_saying(self.db, found, now):
            self._update_notice(found)

    def _update_notice(self, found):
        """The notice (a Windows notification that stays until answered, or the corner popup). It offers Install /
        Remind me later, and it waits - not written down as said - while a muted mode or Do not disturb means
        "not now"; the banner is on screen meanwhile either way. Over a full-screen app it goes out as an ordinary
        Windows notification (no window of ours over a game), which waits in the notification centre."""
        if self.update_popup is not None and self.update_popup.winfo_exists():
            return
        if win.do_not_disturb():
            return
        get = (("Install", lambda: self._install(found)) if updates.can_install(found) else
               ("Open the page", lambda: webbrowser.open(found["url"])))
        before = self.popup
        if self._show(f"{updates.label(found)} - you have {VERSION}.", sticky=True,
                      actions=[get, ("Remind me later", self.update_later)]):
            updates.said(self.db, found["version"])
            self.update_toast = getattr(self, "last_toast", None)
            if self.popup is not before:
                self.update_popup, self.update_popup_for = self.popup, found["version"]

    def update_later(self):
        """"Remind me later": popup and banner gone for updates.SNOOZE_HOURS, then both back."""
        updates.snooze(self.db, now_from_db(self.db))
        self._close_update_popup()
        self.refresh_update(popup=False)

    def update_close(self):
        """The banner's ✕: the banner goes until Lockdown is next started (APP-STANDARDS 2) - not for good, and
        not when the window is only closed to the tray and opened again. The About dot and the tray's line stay."""
        if self.update_shown:
            self.update_closed = self.update_shown[0]
        self._close_update_popup()
        self.refresh_update(popup=False)

    def _close_update_popup(self):
        if self.update_popup is not None and self.update_popup.winfo_exists():
            self.update_popup.close()
        self.update_popup = None
        if getattr(self, "update_toast", None) is not None:   # (and the Windows notification, if it is still up)
            self.toaster.remove(self.update_toast)
            self.update_toast = None

    def _install(self, found=None):
        """Install (popup, banner, tray): the About page's download - with its progress bar - then the
        installer. Never a second download while one is running."""
        found = found or updates.available(self.db)
        if not found:
            return
        self._close_update_popup()
        if not updates.can_install(found):   # a release with no installer attached: only the page can help
            webbrowser.open(found["url"])
            return
        self._open_on("About")
        about = self.pages["About"]
        if not about.downloading:
            about.show_found(found)
            about._get()

    def _build_update_banner(self, parent):
        """"Lockdown X is available  [Install] [Later] ✕" over every page. Built once, shown / hidden and
        relabelled by refresh_update - never rebuilt."""
        self.update_banner = bar = ctk.CTkFrame(parent, fg_color=theme.SURFACE, border_width=1,
                                                border_color=theme.ACCENT, corner_radius=6)
        bar.grid(row=0, column=0, sticky="ew", padx=24, pady=(12, 0))
        # The buttons are packed first: in a narrow window pack squeezes whatever was packed last, and that must
        # be "you have X" and the text - never Install / Later / ✕ (test_banner_buttons_survive_a_narrow_window)
        close = ctk.CTkLabel(bar, text="✕", text_color=theme.MUTED, font=theme.body(13), cursor="hand2", width=24)
        close.pack(side="right", padx=(4, 10))
        close.bind("<Button-1>", lambda e: self.update_close())   # hidden until the next start
        ctk.CTkButton(bar, text="Later", width=74, height=28, **theme.OUTLINE,
                      command=self.update_later).pack(side="right", padx=(0, 14))
        self.update_get = ctk.CTkButton(bar, text="Install", width=90, height=28, command=self._install)
        self.update_get.pack(side="right", padx=(0, 8))
        ctk.CTkLabel(bar, text="", image=theme.icon("shield-check", theme.ACCENT, 18)).pack(side="left",
                                                                                          padx=(14, 8), pady=8)
        self.update_text = ctk.CTkLabel(bar, text="", font=theme.semi(13), anchor="w")
        self.update_text.pack(side="left")
        you = ctk.CTkLabel(bar, text=f"you have {VERSION}", text_color=theme.MUTED, font=theme.body(12))
        you.pack(side="left", padx=10)
        # (and in a really narrow window "you have X" goes)
        extras = ((you, dict(side="left", padx=10, after=self.update_text)),)

        def fit(_event=None):
            room = bar.winfo_width()
            if room <= 1:
                return
            need = 60 + sum(w.winfo_reqwidth() for w in bar.winfo_children()   # (60: the gaps between them)
                            if w.winfo_manager() == "pack" or any(w is x for x, _ in extras))
            for widget, how in extras:
                show = need <= room
                if not show:
                    need -= widget.winfo_reqwidth()
                if show and not widget.winfo_manager():
                    widget.pack(**how)
                elif not show and widget.winfo_manager():
                    widget.pack_forget()
        bar.bind("<Configure>", fit, add="+")
        self.update_text.bind("<Configure>", fit)   # (a longer version number: check again)
        bar.grid_remove()

    # ---------- "Pause my blocks" (pause.py) ----------
    # Started from the card on the Anti-Bypass page (the tray's "Pause my blocks..." opens it), with the challenge;
    # ended by the clock, or early - free - from the banner, the tray or that card.

    def pause_blocks(self, minutes: int | None, silent: bool, done=lambda: None):
        """Pause all your blocks for `minutes` (None: until the next reset time) - loosening, so through the
        Anti-Bypass challenge. While a locked mode is on it asks even with no challenge turned on (as stopping that
        mode does), since the pause would lift the mode's blocks too. done() runs either way (the card repaints)."""
        now = now_from_db(self.db)
        state = modes.active(self.db, now)
        locked = bool(state and state["locked"] and not state["scheduled"])
        end = pause.end_for(self.db, minutes, now)
        how = "for the rest of the day" if minutes is None else f"for {pause.label(minutes)}"
        change = (f"Pause all your blocks {how} (until {end:%H:%M}): every blocked site and app, limits, hours, "
                  "modes, temporary and permanent blocks" + (", and silence all notifications" if silent else ""))

        def go():
            pause.start(self.db, now_from_db(self.db), minutes, silent)   # (the time it is once the challenge is done)
            self.refresh_pause()
            done()
        self.guard([change], go, done, force=locked)

    def resume_blocks(self):
        """"Resume now" / "Resume blocking": your blocks are back at once. Never needs the challenge."""
        pause.resume(self.db)
        self.refresh_pause()

    def refresh_pause(self):
        """The slim "Blocking paused until HH:MM · Resume now" banner over every page, and the tray's state, from
        what is saved (cheap: nothing is touched unless it changed - it runs with the status poll every 3 s, which
        is how a pause that runs out takes its banner with it). Returns the pause's end, or None."""
        now = now_from_db(self.db)
        until = pause.until(self.db, now)
        shown = (until, bool(pause.silent_until(self.db, now))) if until else None
        if shown != getattr(self, "pause_shown", None):
            self.pause_shown = shown
            if until:
                self.pause_text.configure(text=f"Blocking paused until {until:%H:%M}" +
                                               (" · notifications silenced" if shown[1] else ""))
                self.pause_banner.grid()
            else:
                self.pause_banner.grid_remove()
            self.tray.set_paused(until)
            for page in ("Blocking", "Dashboard", "Anti-Bypass"):   # statuses and the card follow it
                if page in self.pages:
                    self.pages[page].refresh()
        return until

    def _build_pause_banner(self, parent):
        """Built once, shown / hidden and relabelled by refresh_pause."""
        self.pause_shown = None
        self.pause_banner = bar = ctk.CTkFrame(parent, fg_color=theme.SURFACE, border_width=1,
                                               border_color=theme.INFO, corner_radius=6)
        bar.grid(row=1, column=0, sticky="ew", padx=24, pady=(12, 0))
        ctk.CTkButton(bar, text="Resume now", width=110, height=26, command=self.resume_blocks).pack(
            side="right", padx=(0, 10), pady=5)
        ctk.CTkLabel(bar, text="", image=theme.icon("hourglass", theme.INFO, 16)).pack(side="left", padx=(14, 8))
        self.pause_text = ctk.CTkLabel(bar, text="", font=theme.semi(13), anchor="w")
        self.pause_text.pack(side="left")
        ctk.CTkLabel(bar, text="protection lists and blocked words still apply", text_color=theme.MUTED,
                     font=theme.body(12)).pack(side="left", padx=10)
        bar.grid_remove()

    def _read_minimize_blocks(self, now, usage=None, items=None, groups=None):
        self.minimize_writes = usage_mod.limit_writes
        self.minimize_blocks = {exe_name(b["item"]["target"]): b
                                for b in self.db.blocks(now, usage=usage, items=items, groups=groups)
                                if b["item"]["item_type"] == "app" and minimizes(b["item"]["block_type"])}

    def _poll_minimize(self):
        """Apps blocked with "Minimize": keep them running, but minimize them whenever they come to the front.
        Also enforces a strict break: while one is on, anything brought to the front is sent back down.
        The list of those apps comes with the watcher (every WATCH_MS) - and at once when the usage tracker has
        just written time reaching a limit or an allowance (0.84.2), so one whose time ran out is minimized
        straight away rather than up to WATCH_MS later."""
        try:
            self._enforce_break()
            if usage_mod.limit_writes != self.minimize_writes and not antibypass.is_off(self.db):
                self._read_minimize_blocks(now_from_db(self.db))
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

    def _show(self, message: str, force: bool = False, action=None, actions=None, sticky: bool = False) -> bool:
        """Notification in the chosen format. Muted while a mode with "mute" is on (unless force - what you
        are using right now is about to be blocked, which is worth saying even in a game).
        action: (label, callback) for the one thing worth doing about this particular message, or None;
        actions / sticky: see Popup. Returns False if it was muted (nothing shown).

        While a full-screen app (a game) is in front, no window of ours is drawn - a topmost popup over a game made
        it stutter. It goes out as a Windows notification whatever the setting: an ordinary one, which Windows may
        hold back into the notification centre while you play (it stays there toast.HELD_EXPIRE_MIN), never one
        that breaks through Do not disturb. With nothing full-screen a sticky notice is a Windows "reminder",
        which stays on screen until answered. If Windows notifications can't be had here (the balloon has no
        buttons), a notice with buttons on the desktop is Lockdown's pop-up instead, so its buttons are kept.
        "Pause my blocks" with "Silence all notifications" mutes everything, force or not, until it ends."""
        if pause.silent_until(self.db, now_from_db(self.db)):
            return False
        if not force:
            state = modes.active(self.db, now_from_db(self.db))
            if state and state["mode"].get("mute"):
                return False
        fmt = alerts.get(self.db, "notify.format")
        full = win.is_fullscreen()
        buttons = actions or ([action] if action else [])
        popup = not full and (fmt in ("inapp", "both") or (bool(buttons) and not self.toaster.works()))
        self.last_toast = None
        if fmt == "both" or not popup:
            self.last_toast = self.toaster.send(
                message, buttons, scenario="reminder" if sticky and not full else None,
                expire_min=toast.HELD_EXPIRE_MIN if full else toast.EXPIRE_MIN)
        if popup:
            self.popup = Popup(self, message, action=action, actions=actions, sticky=sticky)
        return True

    def _open_on(self, page: str):
        """Bring the window up on one particular page - what a notice's button is for."""
        self.deiconify()
        self.lift()
        self.show_page(page)
        self.focus_force()
