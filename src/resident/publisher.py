"""Tells the page about changes cheaply (BLUEPRINT 2, 3.0): events from any thread are queued and ONE flusher thread
sends them as a single `window.evaluate_js("ld.push([...])")` at most every 100 ms.

- Coalescing: a later payload for the same (event, key) replaces an earlier one that has not been sent yet.
- Only changes: a state event whose payload equals what the page already has is dropped; `status` is sent as a
  delta (only the keys that changed since the page last got them).
- Hidden window: nothing is sent except `dirty` flags (once per page, held until the window is shown again - the
  page then re-fetches what it shows) and a last `visible: false`. `status` is still remembered, so showing the
  window sends one fresh full `status`.
- Always JSON-safe: datetimes become ISO strings.
The page side is src/ui/js/bridge.js (`ld.push`). One Publisher per window; `window` only needs `evaluate_js(str)`."""
import json
import threading
import time
from datetime import date, datetime, time as dtime

INTERVAL = 0.1   # seconds between two sends, at most

# One-off happenings: repeating the same payload is meaningful, so they are never dropped as "unchanged".
ONE_SHOT = frozenset({"notice", "show_page", "challenge_open", "dirty", "icon_ready", "visible"})
# Events the page must get even while hidden (they wake the window up, or are for another window).
ALWAYS = frozenset({"notice", "show_page", "challenge_open", "visible"})
# While hidden, any other page-data event becomes a `dirty` flag for these pages.
DIRTY_PAGES = {
    "draft": ("blocking",),
    "mode_changed": ("dashboard", "modes"),
    "blocks_changed": ("dashboard", "blocking"),
    "usage_tick": ("dashboard", "screentime"),
    "icon_ready": ("dashboard", "screentime", "blocking"),
    "download_progress": ("about",),
    "protection_progress": ("blocking",),
}


class RealClock:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


def _json_default(value):
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def dumps(value) -> str:
    return json.dumps(value, default=_json_default, separators=(",", ":"))


class Publisher:
    def __init__(self, window, clock=None, interval: float = INTERVAL, start: bool = True, visible: bool = True,
                 log=None):
        """`visible`: is the window shown right now? (the agent starts it hidden: visible=False)."""
        self.window = window
        self.clock = clock or RealClock()
        self.interval = interval
        self.log = log or (lambda msg: None)
        self._lock = threading.Lock()        # guards the fields below
        self._send_lock = threading.Lock()   # one evaluate_js at a time, in order
        self._wake = threading.Event()
        self._stop = False
        self._visible = bool(visible)
        self._pending: dict = {}      # (event, key) -> payload JSON string (None for status: built when sending)
        self._sent: dict = {}         # (event, key) -> JSON string the page has now (for "unchanged")
        self._dirty: dict = {}        # page -> True: held while hidden
        self._status: dict = {}       # the whole status as Python knows it
        self._status_sent: dict = {}  # the part of it the page has
        self._last_send = -1e9
        self.sends = 0                # evaluate_js calls made (tests / the performance harness)
        self._thread = None
        if start:
            self._thread = threading.Thread(target=self._run, name="lockdown-publisher", daemon=True)
            self._thread.start()

    @property
    def visible(self) -> bool:
        """Is the window shown right now? (jobs that only feed the page can sleep while it is not)"""
        return self._visible

    # ------------------------------------------------------------------ queueing (any thread)
    def publish(self, event: str, payload=None, key=None):
        """Queue an event for the page. Cheap: no I/O here."""
        with self._lock:
            if event == "status":
                if isinstance(payload, dict):
                    self._status.update(json.loads(dumps(payload)))
                if not self._visible:
                    return
                self._pending[("status", None)] = None
            elif not self._visible and event not in ALWAYS:
                if event == "dirty" and isinstance(payload, dict):
                    pages = (payload.get("page"),)
                else:
                    pages = DIRTY_PAGES.get(event, ())
                for page in pages:
                    if page:
                        self._dirty[page] = True
                return
            else:
                self._pending[(event, key)] = dumps(payload)
        self._wake.set()

    def set_visible(self, visible: bool):
        """The main window was shown / hidden / minimised / restored."""
        with self._lock:
            visible = bool(visible)
            if visible == self._visible:
                return
            self._visible = visible
            if visible:
                self._pending[("visible", None)] = dumps({"visible": True})
                for page in self._dirty:
                    self._pending[("dirty", page)] = dumps({"page": page})
                self._dirty.clear()
                self._status_sent = {}                    # the page gets the whole status again
                if self._status:
                    self._pending[("status", None)] = None
            else:
                # what was queued but not sent turns into dirty flags; only a last "visible: false" goes out
                for (event, key) in list(self._pending):
                    if event in ALWAYS:
                        continue
                    del self._pending[(event, key)]
                    if event == "dirty":
                        self._dirty[key] = True
                    else:
                        for page in DIRTY_PAGES.get(event, ()):
                            self._dirty[page] = True
                self._pending[("visible", None)] = dumps({"visible": False})
        self._wake.set()

    # ------------------------------------------------------------------ sending
    def tick(self) -> bool:
        """Send what is queued if >= interval has passed since the last send. True if it called evaluate_js."""
        return self._send(respect_interval=True)

    def flush(self) -> bool:
        """Send what is queued now, ignoring the interval (shutdown, tests)."""
        return self._send(respect_interval=False)

    def _build(self, respect_interval: bool):
        """The script for everything queued, or None. Called with self._lock held."""
        if not self._pending:
            return None
        if respect_interval and self.clock.monotonic() - self._last_send < self.interval:
            return None
        items = []
        for (event, key), body in self._pending.items():
            if event == "status":
                delta = {k: v for k, v in self._status.items() if self._status_sent.get(k) != v}
                if delta:
                    self._status_sent = json.loads(dumps(self._status))
                    items.append(f'["status",{dumps(delta)}]')
            elif event in ONE_SHOT or self._sent.get((event, key)) != body:
                self._sent[(event, key)] = body
                items.append(f'["{event}",{body}]')
        self._pending.clear()
        if not items:
            return None
        self._last_send = self.clock.monotonic()
        return "ld.push([" + ",".join(items) + "])"

    def _send(self, respect_interval: bool) -> bool:
        with self._send_lock:
            with self._lock:
                script = self._build(respect_interval)
            if script is None:
                return False
            self.sends += 1
            try:
                self.window.evaluate_js(script)   # may block until the page answers: not under self._lock
            except Exception as exc:              # a closed window / page not loaded yet must not kill the flusher
                self.log(f"publisher: evaluate_js failed: {exc}")
            return True

    def _run(self):
        while not self._stop:
            self._wake.wait(1.0)
            if self._stop:
                break
            self._wake.clear()
            with self._lock:
                has = bool(self._pending)
                wait = self._last_send + self.interval - self.clock.monotonic()
            if not has:
                continue
            if wait > 0:
                self.clock.sleep(wait)
            self.tick()
            with self._lock:
                if self._pending:   # not sent after all (the interval had not passed): try again
                    self._wake.set()

    def stop(self, timeout: float = 2.0):
        """Stop the flusher thread (what is still queued is dropped)."""
        self._stop = True
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)
