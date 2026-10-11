"""The agent's background workers: everything the old window did on its Tk thread every few hundred milliseconds now
runs here, each worker in a thread of its own with a database connection of its own. Nothing runs on the GUI thread.

- StatusWorker (1 s): reads the database ONCE (resident/snapshot.py) and hands the snapshot to the listeners (the
  publisher, the tray). Every other worker reads that snapshot instead of asking the database again.
- BlockEventWorker (1 s): a blocked app was opened -> close/minimise its windows; a blocked site was opened -> say so
  (only if you were really opening it), through the Alerter (per-reason switch, cooldown, per-item override).
- WatcherWorker (1 s): warnings before blocks start, "block started", Pomodoro phases, the weekly summary.
- MinimizeWorker (250 ms): apps blocked with "Minimize" are sent down when they come to the front; a strict break keeps
  everything down. Win32 only - it reads the snapshot and the reminder engine, not the database.
- ReminderWorker: reminders.Engine ticks (every few seconds, sooner when something is due), driving a ReminderUI.
- UpdateWorker: the automatic "is there a newer Lockdown" check and its notice.
- Also started here: the usage tracker and the bad-word check (monitor/), and the daily retention job.

Notices of all workers go through ONE callback, `hub.notify(message, force=False, action=None)`, which applies today's
mute rule and then hands the text to `hub.notify.show` - the sink R07 provides (the corner window / Windows
notifications). Until then they are only remembered in `hub.notify.recent`.

Threads: a Worker's `tick()` runs only on its own thread. Other threads ask it to do something with `submit(fn)` /
`poke()` (both wake it at once). The reminder engine is not thread-safe: answer it through ReminderWorker.answer.
Heavy things (uiautomation, PIL, Win32 modules) are imported only where they are used.
"""
import logging
import queue
import threading
import time
import traceback
import webbrowser
from collections import deque
from datetime import datetime, timedelta
from typing import Protocol

import alerts
import digest
import reminders
import stats
import updates
from blocker import protection
from blocker.apps import exe_name, is_hosted, minimizes
from db import Database
from resident import snapshot
from rules import TIME_FMT
from trusted_time import now_from_db
from version import VERSION

STATUS_SEC = 1.0           # one snapshot per second
BLOCK_EVENT_SEC = 1.0
WATCH_SEC = 1.0
MINIMIZE_SEC = 0.25
UPDATE_FIRST_SEC = 90      # let the agent settle before touching the network
UPDATE_POLL_SEC = 60       # is a check due (updates.due decides: every EVERY_HOURS), has a snooze run out
SETTINGS_SEC = 5           # the watcher reads the notification settings and the weekly summary's schedule this often
LAST_ALERT_SAVE_SEC = 5    # "last notified" times are written at most this often (not on every alert)
VISIT_FRONT_SEC = 10       # a blocked site is announced only if a browser window in front showed it within this long
VISIT_LAG_MAX_SEC = 30     # ... of the visit (looked for from up to this long before it was seen)
WORDS_BATCH_SEC = 2.5      # more tabs closed for blocked words within this: one summary notice instead of one each
BREAK_NOTICE_SEC = 12      # "Break in progress" at most this often
ERROR_REPEAT_SEC = 60      # the same worker error in a row is logged once per this long
MIN_GAP_SEC = 0.05         # a tick never starts sooner than this after the last one began (no busy loop)
STOP_WAIT_SEC = 1.0        # Workers.stop() waits this long for the workers to wind up
USAGE_TICK_SEC = 5.0       # R13: how often a shown window is told today's active seconds (`usage_tick`)
NETLOG_SEC = 3.0            # R16: how often the Network Log's live feed looks for new rows
NETLOG_IDLE_SEC = 60.0      # (and how often it wakes when nobody asked for it)
GOAL_KEY, DEFAULT_GOAL_HOURS = "stats.goal_hours", "5"   # the daily screen-time goal (the weekly summary names it)

log = logging.getLogger("lockdown.workers")


# ---------------------------------------------------------------- clock, interfaces

class Clock:
    """The three clocks the workers use, in one place so tests can drive them: trusted time (what blocks go by),
    the PC clock (time.time(): the service heartbeat, "last notified"), and a steady one for pacing."""

    def now(self, db) -> datetime:
        return now_from_db(db)

    def now_with(self, offset_sec: float) -> datetime:
        """Trusted time without a database read, from the offset a snapshot carries."""
        return datetime.now() + timedelta(seconds=offset_sec)

    def wall(self) -> float:
        return time.time()

    def mono(self) -> float:
        return time.monotonic()


class ReminderUI(Protocol):
    """What the reminder engine (reminders.Engine) uses to show things - R07 implements it (the corner window and the
    bedtime overlay). The engine calls these from the ReminderWorker's thread, so they must return quickly (hand the
    drawing to the window) and never block on the page.

    Answers come back the other way: a button on a popup -> `ui.worker.answer(key, action)` (a Windows
    notification's button on a held popup -> `ui.worker.held_answer(key, action)`); both run on the worker's thread.
    Never call `ui.engine.answer` from another thread. `ui.engine` is there to read (engine.now, ...).

    `buttons` is a list of (label, action) - the action strings reminders.Engine.answer understands ("start",
    "snooze", "dismiss", "done", "yes", ...). Both attributes are set by the ReminderWorker before the first tick."""
    engine: object
    worker: object

    def popup(self, key: str, title: str, text: str, buttons: list) -> None:
        """A corner popup that stays until answered."""

    def overlay(self, key: str, title: str, text: str, until: "datetime | None", buttons: list) -> None:
        """The full-screen bedtime / break cover."""

    def held(self, key: str, title: str, text: str, buttons: list) -> None:
        """A popup held back while a full-screen app is in front: a Windows notification with the same buttons
        instead (nothing while a mode that mutes is on - the popup still comes once it ends)."""

    def close(self, key: str) -> None:
        """Take down whatever is on screen (or held) under this key."""

    def toast(self, text: str) -> None:
        """A plain notice (the engine's own: "Skipped ... 3 times", "Emergency: ... paused until ...")."""

    def break_start(self, until: datetime) -> None:
        """A strict break begins: minimise everything now and say so (MinimizeWorker keeps windows down until
        `until`, and says "Break in progress" - the adapter does not loop)."""

    def break_end(self) -> None:
        """The break is over: say so."""

    def start_mode(self, mode_id: str, until: "datetime | None") -> None:
        """A reminder's "start a mode" (the bedtime's mode): start it unless another mode is locked on."""


class RecordingReminderUI:
    """The stand-in until R07: remembers every call as (name, args) in `calls` (newest last, the last 200)."""

    def __init__(self):
        self.engine = None
        self.worker = None
        self.calls: deque = deque(maxlen=200)

    def _note(self, name):
        return lambda *args: self.calls.append((name, args))

    def __getattr__(self, name):
        if name in ("popup", "overlay", "held", "close", "toast", "break_start", "break_end", "start_mode"):
            return self._note(name)
        raise AttributeError(name)


# ---------------------------------------------------------------- notices

class Notifier:
    """`hub.notify(message, force=False, action=None)`: the one way a worker tells the owner something.

    Today's mute rule (LockdownApp._show): "Pause my blocks" with "silence notifications" holds EVERYTHING, forced or
    not, until it ends; a mode that mutes (Do Not Disturb) holds everything that is not `force` - what you are using
    right now is about to be blocked, which is worth saying even then. Held notices are dropped, not queued (as before).
    Windows' own Do Not Disturb is not applied here (as before): Windows holds its toasts itself, and the
    callers that must wait for it (reminders, the update notice) ask `dnd()`.

    `action`: (label, callback) for the one thing worth doing about this message ("See the week"). `actions` (a list of
    them) and `sticky` (stays on screen until answered) are for the update notice. Returns False if it was held."""

    def __init__(self, hub):
        self.hub = hub
        self.show = self._remember        # R07 sets: show(message, action=None, actions=None, sticky=False)
        self.recent: deque = deque(maxlen=50)

    def _remember(self, message, action=None, actions=None, sticky=False):
        self.recent.append((message, action, actions, sticky))

    def muted(self, force: bool = False) -> bool:
        snap = self.hub.snapshot
        if snap is None:
            return False
        if snap.pause and snap.pause["silent"]:
            return True
        return bool(snap.mode and snap.mode["mute"] and not force)

    def dnd(self) -> bool:
        """Windows' Do Not Disturb is on."""
        return bool(self.hub.win.do_not_disturb())

    def __call__(self, message: str, force: bool = False, action=None, *, actions=None, sticky: bool = False) -> bool:
        if self.muted(force):
            return False
        self.show(message, action=action, actions=actions, sticky=sticky)
        return True


class Hub:
    """What the workers share: the newest snapshot, the listeners told about each new one, the notice callback, the
    clocks, the Win32 module, and the error log. One per agent."""

    def __init__(self, db_path=None, clock: Clock | None = None, win=None, usage=None, log_sink=None):
        self.db_path = db_path
        self.clock = clock or Clock()
        self._win, self._usage = win, usage
        self.log_sink = log_sink or (lambda text: log.error(text))
        self.snapshot: "snapshot.StatusSnapshot | None" = None   # the newest; replaced whole, read from any thread
        self.listeners: list = []                  # fn(old, new): called on the StatusWorker after every snapshot
        self.notify = Notifier(self)
        self.open_page = lambda page: None         # the window glue: show this page ("See the week")
        self.install_update = None                 # R17: install_update(found) - else the notice opens the page
        self.in_use = lambda: frozenset()          # item ids in use right now (the usage tracker's set)
        self.status_worker = None                  # set by Workers (poke() = "build a snapshot now")
        self.engine = None                         # the reminder engine (set by the ReminderWorker)
        self.netlog_feed = None                    # R16: the Network Log page's live feed (webapi/network.py), set by the Api
        self.netlog_worker = None                  # (set by Workers; the Api pokes it when a page starts a feed)
        self._errors: dict = {}
        self._errors_lock = threading.Lock()

    @property
    def win(self):
        """monitor.win (Win32), imported when first needed."""
        if self._win is None:
            from monitor import win
            self._win = win
        return self._win

    @property
    def usage(self):
        """monitor.usage (the tracker's module: limit_writes, pids_of, shown_since)."""
        if self._usage is None:
            from monitor import usage
            self._usage = usage
        return self._usage

    def open_db(self) -> Database:
        """A new connection (the short busy timeout, like the window's: a lock held by the service must not stall a
        worker for 10 s)."""
        return Database(self.db_path, ui=True) if self.db_path else Database(ui=True)

    def report(self, who: str, error: BaseException):
        """Write a worker's error to the log. The same error in a row is written once per ERROR_REPEAT_SEC."""
        text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        key = (who, text.strip().splitlines()[-1])
        now = self.clock.mono()
        with self._errors_lock:
            last = self._errors.get(key)
            if last is not None and 0 <= now - last < ERROR_REPEAT_SEC:
                return
            if len(self._errors) > 50:
                self._errors.clear()
            self._errors[key] = now
        self.log_sink(f"ERROR Lockdown {who}: {text}")


# ---------------------------------------------------------------- the base

class Worker(threading.Thread):
    """A background thread that calls `tick()` every `interval` seconds (or the number of seconds tick() returns).

    - Waits on an event between ticks - never a busy loop, and `stop()` ends the wait at once.
    - Its own Database (`self.db`), opened by its own thread.
    - A tick that raises is logged (hub.report: the same error in a row only once a minute) and the worker carries on.
    - `submit(fn)` runs fn on this thread soon (without a tick); `poke()` makes it tick now.
    - `start_delay`: seconds before the first tick."""
    label = "worker"
    interval = 1.0
    start_delay = 0.0
    uses_db = True          # False: no connection is opened up front (the worker asks for self.db only when it must)

    def __init__(self, hub: Hub, interval: float | None = None):
        super().__init__(name=f"lockdown-{self.label}", daemon=True)
        self.hub = hub
        if interval is not None:
            self.interval = interval
        self.stop_event = threading.Event()
        self._wake = threading.Event()
        self._commands: queue.Queue = queue.Queue()
        self._poked = False
        self._db = None

    # ----- for subclasses
    @property
    def db(self) -> Database:
        if self._db is None:
            self._db = self.hub.open_db()
        return self._db

    def setup(self):
        """Once, on the worker's thread, before the first tick."""

    def tick(self) -> "float | None":
        """One round of work. Returns the seconds to wait before the next (None: `interval`)."""
        raise NotImplementedError

    def teardown(self):
        """Once, on the worker's thread, when it stops."""

    # ----- for other threads
    def submit(self, fn):
        """Run fn() on this worker's thread soon. Errors are logged like a tick's."""
        self._commands.put(fn)
        self._wake.set()

    def poke(self):
        """Tick now rather than at the next due time."""
        self._poked = True
        self._wake.set()

    def stop(self, timeout: float | None = None):
        self.stop_event.set()
        self._wake.set()
        if timeout is not None and self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    # ----- the loop
    def mono(self) -> float:
        return self.hub.clock.mono()

    def _sleep(self, seconds: float):
        self._wake.wait(seconds)
        self._wake.clear()

    def _guard(self, fn):
        try:
            return fn()
        except Exception as error:
            self.hub.report(self.label, error)
            return None

    def _drain(self):
        while True:
            try:
                fn = self._commands.get_nowait()
            except queue.Empty:
                return
            self._guard(fn)

    def run(self):
        try:
            if self.uses_db:
                self._guard(lambda: self.db)
            self._guard(self.setup)
            next_at = self.mono() + self.start_delay
            while not self.stop_event.is_set():
                self._drain()
                started = self.mono()
                if self._poked or started >= next_at:
                    self._poked = False
                    delay = self._guard(self.tick)
                    next_at = max(started + (self.interval if delay is None else delay), self.mono() + MIN_GAP_SEC)
                remaining = next_at - self.mono()
                if remaining > 0 and not self.stop_event.is_set():
                    self._sleep(remaining)
        finally:
            self._guard(self.teardown)
            if self._db is not None:
                self._guard(lambda: self._db.flush(2))
                self._guard(self._db.close)


# ---------------------------------------------------------------- status

class StatusWorker(Worker):
    """One snapshot a second; the listeners (publisher, tray) get (old, new).

    A listener that also has `usage_tick(payload)` and `visible` (the publisher's) is told today's active seconds every
    USAGE_TICK_SEC, but only while its window is shown - a hidden window costs no query (once per hidden stretch it gets an
    empty tick, which the Publisher turns into `dirty` flags, so the pages reload on the next show)."""
    label = "status"
    interval = STATUS_SEC
    _usage_at = None             # when (hub.clock.mono) the last usage_tick went out
    _usage_flagged = False       # the empty tick of this hidden stretch has gone out

    def tick(self):
        hub = self.hub
        new = snapshot.build(self.db, hub.clock.now(self.db), hub.clock.wall())
        old, hub.snapshot = hub.snapshot, new
        for listener in list(hub.listeners):
            self._guard(lambda: listener(old, new))
        self._guard(lambda: self._usage_tick(new))

    def _usage_tick(self, snap):
        sinks = [l for l in list(self.hub.listeners) if hasattr(l, "usage_tick")]
        if not sinks:
            return
        if not any(s.visible for s in sinks):
            if not self._usage_flagged:
                self._usage_flagged = True
                for s in sinks:
                    s.usage_tick({})
            return
        self._usage_flagged = False
        mono = self.hub.clock.mono()
        if self._usage_at is not None and mono - self._usage_at < USAGE_TICK_SEC:
            return
        self._usage_at = mono
        payload = {"active_sec": stats.active_on(self.db, snap.now.date())}
        for s in sinks:
            if s.visible:
                s.usage_tick(payload)


def tray_listener(tray):
    """A snapshot listener that touches the tray only when what it shows changed (status line, colour, pause, the
    mode menu, the waiting update). Mode menu items need the tray's on_mode hook: without one they are left alone."""
    def on_snapshot(old, new):
        if not snapshot.tray_changed(old, new):
            return
        state = new.tray
        tray.update(state.running, state.text, off=state.off)
        tray.set_paused(state.paused_until)
        if getattr(tray, "on_mode", None):
            tray.set_modes(list(state.modes), state.mode_id)
        tray.set_update(state.update)
    return on_snapshot


class PublisherListener:
    """A snapshot listener that pushes what changed to the main window (`status` event, a delta of the page part) and
    carries the window's `usage_tick`. The publisher itself holds the pushes back while the window is hidden."""

    def __init__(self, win):
        self.win = win

    def __call__(self, old, new):
        delta = snapshot.changed(old, new)
        if delta and self.win.publisher is not None:
            self.win.publisher.publish("status", delta)

    @property
    def visible(self) -> bool:
        return self.win.publisher is not None and self.win.publisher.visible

    def usage_tick(self, payload):
        if self.win.publisher is not None:
            self.win.publisher.publish("usage_tick", payload)


def publisher_listener(win):
    return PublisherListener(win)


# ---------------------------------------------------------------- blocked visits

class Alerter:
    """"X is blocked" notices for blocked visits: the per-reason switch, the cooldown, the item's own On/Off, the
    message template (alerts.py rules). Shared by the BlockEventWorker and the MinimizeWorker; one connection, one lock.

    "Last notified" times survive a restart (setting notify.last_alert), written at most every LAST_ALERT_SAVE_SEC -
    the first alert writes at once."""
    KEY = "notify.last_alert"

    def __init__(self, hub: Hub):
        self.hub = hub
        self._lock = threading.Lock()
        self._db = None
        self.last_alert: dict = {}
        self._dirty = False
        self._saved_at: float | None = None

    def _open(self) -> Database:
        if self._db is None:
            self._db = self.hub.open_db()
            self.last_alert = self._load()
        return self._db

    def _load(self) -> dict:
        import json
        try:
            raw = json.loads(self._db.get_setting(self.KEY, "") or "{}")
            return {(None if k == "null" else int(k)): float(v) for k, v in raw.items()}
        except (ValueError, TypeError):
            return {}

    def _save(self, wall: float):
        import json
        keep = wall - 7 * 86400   # entries older than a week are dropped so it can't grow for ever
        data = {("null" if k is None else str(k)): v for k, v in self.last_alert.items() if v >= keep}
        self._db.set_setting(self.KEY, json.dumps(data))
        self._dirty, self._saved_at = False, wall

    def _save_if_due(self, wall: float, force: bool = False):
        if self._dirty and (force or self._saved_at is None or wall - self._saved_at >= LAST_ALERT_SAVE_SEC):
            self._save(wall)

    def alert(self, event: dict, item_notify: str | None) -> bool:
        """Maybe tell the owner about this blocked visit. True if a notice went out."""
        hub = self.hub
        with self._lock:
            db = self._open()
            wall = hub.clock.wall()
            reason = alerts.base_reason(event["reason"])
            enabled = alerts.get(db, f"notify.enabled.{reason}") == "1"
            cooldown = int(alerts.get(db, "notify.cooldown_min"))
            if not alerts.should_notify(event, item_notify, enabled, self.last_alert.get(event["item_id"]), wall,
                                        cooldown):
                return False
            self.last_alert[event["item_id"]] = wall
            self._dirty = True
            self._save_if_due(wall)
            message = alerts.format_message(alerts.get(db, f"notify.msg.{reason}"), event, hub.clock.now(db),
                                            protection.list_names(db))
        hub.notify(message)
        return True

    def flush_due(self):
        """Write the waiting "last notified" times if their turn has come (called every second by a worker)."""
        with self._lock:
            if self._dirty and self._db is not None:
                self._save_if_due(self.hub.clock.wall())

    def close(self):
        with self._lock:
            if self._db is not None:
                try:
                    if self._dirty:
                        self._save(self.hub.clock.wall())
                    self._db.flush(2)
                finally:
                    self._db.close()
                    self._db = None


class BlockEventWorker(Worker):
    """Block events the service recorded: an app that was opened while blocked -> its windows are asked to close (the
    service force-closes after 10 s); a blocked site -> a notice, but only if a browser window in front showed it (a
    page embedding a YouTube video, a preconnect ... stays silent)."""
    label = "block-events"
    interval = BLOCK_EVENT_SEC

    def __init__(self, hub: Hub, alerter: Alerter, interval: float | None = None):
        super().__init__(hub, interval)
        self.alerter = alerter
        self.last_event_id = 0
        self.visits: dict = {}      # (item id, hostname) -> (event, item, seen, since): waiting to be seen in front

    def setup(self):
        self.last_event_id = self.db.last_block_event_id()     # only notify about new visits

    def tick(self):
        hub, db = self.hub, self.db
        events = db.block_events_after(self.last_event_id)
        if events:
            self.last_event_id = events[-1]["id"]
            items = {i["id"]: i for i in db.list_items()}
            seen, now = hub.clock.mono(), hub.clock.now(db)
            for event in events:
                item = items.get(event["item_id"]) or {}
                if alerts.is_app_event(event, item):
                    # ask nicely; the service force-closes after 10 s (a Java game / a script: the windows of the
                    # processes the service says run it - they are javaw.exe / python.exe)
                    pids = hub.usage.pids_of(db, event["hostname"])
                    if pids:
                        hub.win.close_windows(pids)
                    else:
                        hub.win.close_app(event["hostname"])
                    self.alerter.alert(event, item.get("notify"))
                else:    # a site: only if you were opening it
                    # (looked for in front from the visit's own time: this worker may have been busy a while)
                    try:
                        lag = (now - datetime.strptime(event["timestamp"], TIME_FMT)).total_seconds()
                    except (TypeError, ValueError):
                        lag = 0.0
                    since = seen - min(max(lag, 0.0), VISIT_LAG_MAX_SEC) - VISIT_FRONT_SEC
                    self.visits[(event["item_id"], event["hostname"])] = (event, item, seen, since)
        self._check_visits()
        self.alerter.flush_due()

    def _check_visits(self):
        """Announce a blocked site only when a browser window in front showed it within VISIT_FRONT_SEC of the visit.
        The service records every connection to a blocked site, and most are nobody opening it."""
        hub = self.hub
        now = hub.clock.mono()
        for key, (event, item, seen, since) in list(self.visits.items()):
            targets = item.get("target", "").split()
            if alerts.opened_in_front(event, hub.usage.shown_since(since), targets):
                del self.visits[key]
                self.alerter.alert(event, item.get("notify"))
            elif not 0 <= now - seen <= VISIT_FRONT_SEC:
                del self.visits[key]


# ---------------------------------------------------------------- warnings, phases, the weekly summary

def goal_seconds(db) -> float | None:
    """The daily screen-time goal in seconds (None = off) - gui/dashboard.goal_seconds without the Tk import."""
    hours = float(db.get_setting(GOAL_KEY, DEFAULT_GOAL_HOURS) or 0)
    return hours * 3600 or None


def name_of(kind: str, name: str, items) -> str:
    """A friendly name for an app/site in the weekly summary: the blocklist's name for an app, else the exe's name
    without ".exe" (gui/appinfo.name_of without the Start Menu list)."""
    if kind != "app":
        return name
    if name == "pythonw.exe":    # Lockdown's own window
        return "Lockdown"
    for item in items:
        if item["item_type"] == "app" and item["target"].lower() == name:
            return item["display_name"]
    stem = name[:-4] if name.endswith(".exe") else name
    return stem[:1].upper() + stem[1:]


class WatcherWorker(Worker):
    """Warnings before blocks start (and again while you use the thing, every `repeat` seconds), "block started"
    notices, Pomodoro phases, and the weekly summary - from each new snapshot (the same items, groups and usage the
    StatusWorker read: no second load)."""
    label = "watcher"
    interval = WATCH_SEC

    def __init__(self, hub: Hub, interval: float | None = None):
        super().__init__(hub, interval)
        self.watcher = alerts.BlockWatcher()
        self.last_phase: tuple | None = None    # (mode id, started, phase, round) last announced
        self.last_end: str | None = None        # when that session ends as a whole (iso)
        self._seen = None
        self._settings: dict | None = None
        self._slow_at: float | None = None

    def tick(self):
        hub = self.hub
        snap = hub.snapshot
        if snap is None or snap is self._seen:
            return
        self._seen = snap
        if snap.off:     # switched off: nothing to warn about. Forget what was blocked, so switching back on takes
            self.watcher.prev_blocked = None      # a fresh baseline instead of announcing every rule as new
            return
        mono = hub.clock.mono()
        slow = self._slow_at is None or mono - self._slow_at >= SETTINGS_SEC
        if slow:
            self._slow_at = mono
            self._settings = {key: alerts.get(self.db, key) for key in alerts.DEFAULTS}
        self._announce_phase(snap)
        for message in self.watcher.check(snap.items, snap.groups, snap.usage, snap.now, hub.in_use(), self._settings):
            hub.notify(message, force=message in self.watcher.urgent)
        if slow and digest.due(self.db, snap.now):       # the weekly summary
            db = self.db
            names = lambda kind, name: name_of(kind, name, snap.items)   # noqa: E731
            text = digest.summary(db, snap.now.date(), goal_seconds(db), names)
            hub.notify(text, action=("See the week", lambda: hub.open_page("Screen Time")))
            digest.mark_shown(db, snap.now)

    def _announce_phase(self, snap):
        """Pomodoro: say when a break starts and when focus is back, and when the session is done. Starting or stopping
        a mode by hand says nothing here (the mode's own notice does): only the same session moving on, or one that
        ran out by itself."""
        mode = snap.mode
        phase = None
        if mode and mode["phase"]:
            phase = (mode["id"], mode["started"], mode["phase"]["name"], mode["phase"]["round"])
        last = self.last_phase
        if phase and last and phase != last and phase[:2] == last[:2]:
            ph = mode["phase"]
            end = datetime.fromisoformat(ph["ends_at"])
            if ph["name"] == "focus":
                self.hub.notify(f"Focus - round {ph['round']} of {ph['rounds']}, until {end:%H:%M}. "
                                "Blocks are back on.", force=True)
            else:
                self.hub.notify(f"{ph['name'].capitalize()} until {end:%H:%M} - blocks are off.", force=True)
        elif last and not phase and self.last_end and snap.now >= datetime.fromisoformat(self.last_end):
            self.hub.notify("Focus session done.", force=True)
        self.last_phase = phase
        self.last_end = mode["ends_at"] if phase else None


# ---------------------------------------------------------------- minimise, strict break

def minimize_set(block_list) -> dict:
    """exe -> block, for the apps that are blocked with "Minimize"."""
    return {exe_name(b["item"]["target"]): b for b in block_list
            if b["item"]["item_type"] == "app" and minimizes(b["item"]["block_type"])}


class MinimizeWorker(Worker):
    """Apps blocked with "Minimize" keep running, but go back down whenever they come to the front. Also enforces a
    strict break: while one is on, anything brought to the front is sent back down. Win32 only: the apps come from
    the newest snapshot (so a block that has just started is acted on within a second; when the usage tracker has
    written time that reaches a limit it asks for a new snapshot at once), the break from the reminder engine.
    No database reads - except for program files (game.jar), whose processes the service publishes in a setting."""
    label = "minimize"
    interval = MINIMIZE_SEC
    uses_db = False

    def __init__(self, hub: Hub, alerter: Alerter, interval: float | None = None):
        super().__init__(hub, interval)
        self.alerter = alerter
        self._set_for = None
        self._set: dict = {}
        self._writes = None
        self._break_notice_at = 0.0

    def tick(self):
        hub = self.hub
        snap = hub.snapshot
        self._enforce_break(snap)
        writes = hub.usage.limit_writes
        if self._writes is not None and writes != self._writes and hub.status_worker is not None:
            hub.status_worker.poke()     # the tracker wrote time reaching a limit: look again now
        self._writes = writes
        blocks = self._blocks(snap)
        if not blocks:
            return
        win = hub.win
        hwnd, exe = win.foreground()
        block, pids = blocks.get(exe), frozenset()
        files = [f for f in blocks if is_hosted(f)] if hwnd and not block else ()
        if files:     # a Java game / a script in front: javaw.exe / python.exe running a listed file
            published = hub.usage.members(self.db)
            front = published.get(str(win.content_pid(hwnd))) or ()
            exe = next((f for f in files if f in front), exe)
            block = blocks.get(exe)
            pids = hub.usage.pids_of(self.db, exe, published) if block else pids
        if block and (win.minimize_windows(pids) if pids else win.minimize_app(exe)):
            item = block["item"]
            until = block["until"].strftime(TIME_FMT) if block["until"] else None
            self.alerter.alert({"item_id": item["id"], "display_name": item["display_name"], "reason": block["reason"],
                                "until": until}, item["notify"])

    def _blocks(self, snap) -> dict:
        if snap is None or snap.off:
            return {}
        if snap is not self._set_for:
            self._set_for, self._set = snap, minimize_set(snap.block_list)
        return self._set

    def _enforce_break(self, snap):
        engine = self.hub.engine
        until = getattr(engine, "break_until", None)
        if not until:
            return
        now = self.hub.clock.now_with(snap.clock_offset if snap else 0.0)
        if now >= until:     # safety net; the reminder engine ends it on its own tick
            return
        if self.hub.win.minimize_foreground():     # you tried to open something - send it back and say why
            if now.timestamp() - self._break_notice_at > BREAK_NOTICE_SEC:
                self._break_notice_at = now.timestamp()
                self.hub.notify(f"Break in progress - {max(1, round((until - now).total_seconds() / 60))} min left.",
                                force=True)


# ---------------------------------------------------------------- reminders

class ReminderWorker(Worker):
    """Sleep / break / your reminders: reminders.Engine on its own thread. Popups wait while a full-screen app is in
    front, and everything waits - the bedtime screen too - while Do Not Disturb is on (the engine's `quiet`). It
    ticks every reminders.TICK_SEC, or sooner when something timed is due (Engine.next_tick)."""
    label = "reminders"
    interval = reminders.TICK_SEC

    def __init__(self, hub: Hub, ui: ReminderUI, interval: float | None = None):
        super().__init__(hub, interval)
        self.ui = ui
        self.engine = None
        self._planned = reminders.TICK_SEC      # the wait this tick was planned after (the engine's `dt`)
        self._last = None

    def setup(self):
        self.engine = reminders.Engine(self.db, self.ui)
        self.hub.engine = self.engine
        self.ui.engine, self.ui.worker = self.engine, self

    def answer(self, key: str, action: str):
        """A button on a popup / the bedtime screen (any thread): the engine hears it on this worker's thread."""
        self.submit(lambda: self.engine.answer(key, action))

    def held_answer(self, key: str, action: str):
        """A button on the Windows notification of a held popup (any thread)."""
        self.submit(lambda: self.engine.held_answer(key, action))

    def tick(self):
        hub = self.hub
        delay = reminders.TICK_SEC
        snap = hub.snapshot
        try:
            if snap is None or snap.off:      # switched off: bedtime, breaks and your reminders all stop
                return delay
            now = hub.clock.now(self.db)
            win = hub.win
            quiet = bool(snap.mode and snap.mode["mute"]) or bool(win.do_not_disturb())
            elapsed = self._planned if self._last is None else min(self._planned, hub.clock.mono() - self._last)
            self._last = hub.clock.mono()
            self.engine.tick(now, win.idle_seconds(), win.is_fullscreen(), dt=elapsed, quiet=quiet)
            delay = self.engine.next_tick(now)
            return delay
        finally:
            self._planned = delay


# ---------------------------------------------------------------- updates

class UpdateWorker(Worker):
    """The automatic "is there a newer Lockdown" check - the network request goes out on this thread only (it can take
    its time), never on the window's. Respects the About page's switches (updates.due / worth_saying): off = no
    request, "tell me" off = no notice. A found update stays in the snapshot (banner, About dot, tray line) until
    installed or snoozed; the notice (Install / Remind me later) comes once per version, and again after a snooze,
    when nothing mutes it.

    First look UPDATE_FIRST_SEC after the agent starts; then every UPDATE_POLL_SEC it asks whether a check is due
    (every updates.EVERY_HOURS, never twice within updates.RETRY_MIN) and whether a snooze has run out - so the network
    is touched hourly at the most, and a "Remind me later" comes back on time."""
    label = "updates"
    interval = UPDATE_POLL_SEC
    start_delay = UPDATE_FIRST_SEC

    def __init__(self, hub: Hub, latest=None, interval: float | None = None):
        super().__init__(hub, interval)
        self.latest = latest or updates.latest_release
        self._hours = updates.EVERY_HOURS

    def check_soon(self, hours: float = updates.OPEN_HOURS):
        """The window was opened (any thread): look again if the last check is older than `hours`."""
        self._hours = min(self._hours, hours)
        self.poke()

    def tick(self):
        db, clock = self.db, self.hub.clock
        hours, self._hours = self._hours, updates.EVERY_HOURS
        now = clock.now(db)
        if updates.due(db, now, hours):
            updates.trying(db, now)
            found = self.latest()          # the network
            if found:                      # a failed check is not written down, so it tries again later
                updates.checked(db, clock.now(db), found)
        self._tell(clock.now(db))

    def _tell(self, now: datetime):
        hub, db = self.hub, self.db
        found = updates.banner(db, now)
        if not (found and updates.worth_saying(db, found, now)):
            return
        if hub.notify.dnd():       # not now; the banner is on screen meanwhile
            return
        if updates.can_install(found) and hub.install_update:
            get = ("Install", lambda: hub.install_update(found))
        else:
            get = ("Open the page", lambda: webbrowser.open(found["url"]))
        if hub.notify(f"{updates.label(found)} - you have {VERSION}.", sticky=True,
                      actions=[get, ("Remind me later", self.remind_later)]):
            updates.said(db, found["version"])

    def remind_later(self):
        """"Remind me later" (any thread): popup and banner gone for updates.SNOOZE_HOURS, then both back."""
        self.submit(lambda: updates.snooze(self.db, self.hub.clock.now(self.db)))


# ---------------------------------------------------------------- Network Log live feed

class NetLogWorker(Worker):
    """Every NETLOG_SEC, while the Network Log page is shown and Live is on: tell the page the rows that are new
    (webapi/network.py _Feed.tick). Asleep (one wake-up a minute, doing nothing) when no page asked for it; the Api pokes
    it when one does, and the feed switches itself off when the window is hidden."""
    label = "netlog"
    interval = NETLOG_SEC
    uses_db = False

    def tick(self):
        feed = self.hub.netlog_feed
        if feed is None or not feed.on:
            return NETLOG_IDLE_SEC
        feed.tick()
        return None


# ---------------------------------------------------------------- bad-word notices

class WordNotices:
    """`add(word, action)` is called (from the tab check's thread) after a tab was closed / sent back for a blocked
    word. The first one is announced at once; more within WORDS_BATCH_SEC become one "Closed N more tabs" notice, and
    so on while tabs keep closing."""

    def __init__(self, hub: Hub, schedule=None):
        self.hub = hub
        self._schedule = schedule or self._timer       # schedule(delay, fn) -> something with .cancel()
        self._lock = threading.Lock()
        self._batch: list | None = None                # tabs closed since the last notice (None: no window open)
        self._timer_handle = None

    @staticmethod
    def _timer(delay, fn):
        timer = threading.Timer(delay, fn)
        timer.daemon = True
        timer.start()
        return timer

    def add(self, word: str, action: str):
        with self._lock:
            if self._batch is not None:
                self._batch.append(word)
                return
            self._batch = []
            self._timer_handle = self._schedule(WORDS_BATCH_SEC, self._summary)
        done = "closing the tab" if action == "close" else "going back"
        self.hub.notify(f'"{word.rstrip("*")}" is a blocked word - {done}.')

    def _summary(self):
        with self._lock:
            batch, self._batch = self._batch, None
            if batch:
                self._batch = []
                self._timer_handle = self._schedule(WORDS_BATCH_SEC, self._summary)
            else:
                self._timer_handle = None
        if batch:
            n = len(batch)
            self.hub.notify(f"Closed {n} more tab{'s' * (n > 1)} with blocked words.")

    def stop(self):
        with self._lock:
            handle, self._timer_handle, self._batch = self._timer_handle, None, None
        if handle is not None:
            handle.cancel()


# ---------------------------------------------------------------- all of them

class Workers:
    """Every background job of the agent, started and stopped together. Create with the pieces you want to replace
    (tests, R07's ReminderUI); `start()` makes the rest. Start only in the process that owns the single-instance port,
    so usage is never counted twice."""

    def __init__(self, db_path=None, hub: Hub | None = None, reminder_ui: ReminderUI | None = None, tracker=None,
                 word_guard=None, retention=None, log_sink=None):
        self.hub = hub or Hub(db_path, log_sink=log_sink)
        self.alerter = Alerter(self.hub)
        self.status = StatusWorker(self.hub)
        self.block_events = BlockEventWorker(self.hub, self.alerter)
        self.watcher = WatcherWorker(self.hub)
        self.minimize = MinimizeWorker(self.hub, self.alerter)
        self.reminder_ui = reminder_ui or RecordingReminderUI()
        self.reminders = ReminderWorker(self.hub, self.reminder_ui)
        self.updates = UpdateWorker(self.hub)
        self.netlog = NetLogWorker(self.hub)
        self.word_notices = WordNotices(self.hub)
        self.tracker, self.word_guard, self.retention = tracker, word_guard, retention
        self._startup = None
        self._stopping = False
        self.workers = [self.status, self.block_events, self.watcher, self.minimize, self.reminders, self.updates,
                        self.netlog]

    def start(self):
        self.hub.status_worker = self.status
        self.hub.netlog_worker = self.netlog
        for worker in self.workers:
            worker.start()
        self._startup = threading.Thread(target=self._start_desktop_jobs, name="lockdown-startup", daemon=True)
        self._startup.start()

    def _start_desktop_jobs(self):
        """(its own thread) The existing threads of monitor/ and retention.py - the usage tracker (time on blocked
        sites/apps, screen time), the bad-word check and the daily retention job - then what the old window did once
        when it opened. They need the desktop, so tests leave them out by passing their own (or fakes)."""
        try:
            if self.tracker is None or self.word_guard is None:
                # Both threads import the COM libraries; two threads importing them at the same time can deadlock,
                # so they are imported once, here, before either starts.
                import comtypes.client  # noqa: F401
                import uiautomation  # noqa: F401
            if self.tracker is None:
                from monitor.usage import UsageTracker
                self.tracker = UsageTracker()
            if self.word_guard is None:
                from monitor.word_guard import WordGuard
                self.word_guard = WordGuard(self.word_notices.add)
            if self.retention is None:
                import retention
                self.retention = retention.RetentionThread()
            tracker = self.tracker
            self.hub.in_use = lambda: tracker.in_use
            for thread in (self.tracker, self.word_guard, self.retention):
                if self._stopping:
                    return
                if not thread.is_alive():
                    thread.start()
            from importer import distracting
            db = self.hub.open_db()
            try:
                distracting.seed(db)            # games, streaming ... are Distracting by default
                updates.remove_downloads()      # the installer an in-app update downloaded has done its job
            finally:
                db.close()
        except Exception as error:
            self.hub.report("startup", error)

    def stop(self, timeout: float = STOP_WAIT_SEC):
        """Stop everything. The workers get `timeout` seconds in all; the usage tracker then writes what it has counted
        but not written yet (up to its own 2 s)."""
        self._stopping = True
        for worker in self.workers:
            worker.stop()
        self.word_notices.stop()
        for thread in (self.word_guard, self.retention):
            if thread is not None:
                thread.stop_event.set()
        deadline = time.monotonic() + timeout
        for worker in self.workers:
            if worker.is_alive():
                worker.join(max(0.0, deadline - time.monotonic()))
        self.alerter.close()                      # (writes the "last notified" times still waiting)
        if self._startup is not None and self._startup.is_alive():
            self._startup.join(max(0.0, deadline - time.monotonic()))
        if self.tracker is not None:
            self.tracker.stop()
