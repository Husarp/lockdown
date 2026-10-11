"""Headless UI test harness: the REAL page (src/ui/) against the REAL Python Api, in Chrome.

How it works
    UiHarness starts a loopback HTTP server that
      * serves src/ui/ with the production AssetServer rules (same MIME types, same path checks),
      * answers POST /rpc/<method> by calling the real `webapi` Api object (`core.build_api`, the object pywebview
        would get as js_api) on a temporary database, and
      * serves tests/ui/static/ under /__t/ (test-only pages and page modules; never part of the product).
    Playwright's add_init_script puts `window.pywebview = {api: {<name>: (...args) => fetch("/rpc/<name>")}}` into every
    page and fires `pywebviewready`, so bridge.js (`ld.call`) works exactly as inside pywebview. The shim exists only
    here: production src/ui never contains "/rpc" (a test checks that).

Fixtures (tests/ui/conftest.py)
    harness      a UiHarness on a fresh temp database (synthetic data, a fixed "now", the service shown as running).
                 harness.ctx / .api / .db   the Context, the Api object, this thread's Database (to arrange or check data)
                 harness.now                the trusted time (settable: harness.now = datetime(...))
                 harness.set_service(bool)  show the service running / stopped (heartbeat)
                 harness.calls              [(time, method, args)] of every rpc;  harness.rpc_count(name=None, since=0)
                 harness.delays             {"method": seconds} slows one rpc down (skeleton / slow-load tests)
                 harness.url(path)          http://127.0.0.1:PORT/path
    page         a Playwright Page (Chrome, 1280x800) with the shim, timer counters and a visibility switch installed.
                 page.errors collects console errors / page errors / failed requests; the test fails at teardown if any
                 (assert_clean(page) checks earlier; @pytest.mark.allow_console_errors turns the check off).
    shell        shell(width=None, height=None, route=None) opens the app (index.html) and waits until it is booted
                 (<html data-ready="1">: first status rendered, router started). Returns the page.
    push         push(event, payload=None) runs ld.push([[event, payload]]) in the page, as the Publisher would.
    perf         Perf(page): fcp(), click_to_frame(selector), click_until(click, ready), scroll_frames(selector, ...),
                 set_hidden(bool),
                 timer_calls(), reset_timer_calls() - the numbers the performance checks report.
    browser      one Chrome for the whole run (session scope).

Everything in this folder is skipped when playwright or /usr/bin/google-chrome is missing.
"""
import json
import os
import shutil
import threading
import time
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
CHROME = os.environ.get("LOCKDOWN_CHROME", "/usr/bin/google-chrome")
STATIC_DIR = Path(__file__).resolve().parent / "static"
NOW = datetime(2026, 10, 11, 15, 42, 17)          # the trusted time the page sees (matches tests/synthdata)


def available() -> bool:
    """Can the UI tests run here? (playwright importable and Chrome present)"""
    try:
        import importlib.util
        return importlib.util.find_spec("playwright") is not None and os.path.exists(CHROME)
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------- the server

def _handler_class():
    from resident.assets import Response, _Handler

    class Handler(_Handler):
        owner = None

        def do_POST(self):
            parsed = urlsplit(self.path)
            if not self._host_ok() or not parsed.path.startswith("/rpc/"):
                return self._refuse()
            length = int(self.headers.get("Content-Length") or 0)
            try:
                args = json.loads(self.rfile.read(length) or b"[]")
            except ValueError:
                args = []
            reply = self.owner.rpc(parsed.path[len("/rpc/"):], args)
            self._send(Response(json.dumps(reply).encode(), "application/json; charset=utf-8"), False)

    return Handler


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):      # (Chrome drops connections when a page goes away: not news)
        pass


class UiHarness:
    def __init__(self, tmp_path: Path, days: int = 14, extra_items: int = 0, now: datetime = NOW):
        import webapi
        from db import Database
        from resident.assets import AssetServer, Response
        from synthdata import build

        self.now = now
        self.calls: list[tuple[float, str, list]] = []
        self.delays: dict[str, float] = {}
        self._lock = threading.Lock()
        self.db_path = tmp_path / "config.db"
        Database(self.db_path)
        if days:
            build(self.db_path, now.date(), days=days, extra_items=extra_items)
        self.ctx = webapi.make_context(self.db_path, clock=lambda db: self.now)
        from webapi import core
        self.api = core.build_api(self.ctx)
        self.names = list(dir(self.api))
        # Errors written to the Lockdown log (log_client_error ...) go to the temp folder
        import paths
        self._old_log = paths.LOG_PATH
        paths.LOG_PATH = tmp_path / "lockdown.log"
        self.log_path = paths.LOG_PATH

        self.assets = AssetServer()
        self.assets.add_route("/__t/", lambda rest, query: self._static(rest, Response))
        handler = _handler_class()
        handler.owner = self
        self.httpd = _QuietServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, name="ui-harness", daemon=True).start()
        self._service_up = True
        self._beat_stop = threading.Event()
        self._beat = threading.Thread(target=self._heartbeat, name="ui-harness-beat", daemon=True)
        self._write_heartbeat(Database(self.db_path))
        self._beat.start()

    # -- plumbing
    def _static(self, rest, Response):
        path = (STATIC_DIR / rest).resolve()
        if not path.is_file() or not path.is_relative_to(STATIC_DIR):
            return None
        from resident.assets import MIME
        return Response(path.read_bytes(), MIME.get(path.suffix.lower(), "application/octet-stream"))

    def respond(self, url_path, query):
        return self.assets.respond(url_path, query)

    def rpc(self, name: str, args: list) -> dict:
        with self._lock:
            self.calls.append((time.monotonic(), name, args))
        delay = self.delays.get(name)
        if delay:
            time.sleep(delay)
        try:
            fn = getattr(self.api, name)
        except AttributeError:
            return {"ok": False, "error": f"Unknown Api method: {name}"}
        return fn(*args)

    def _write_heartbeat(self, db):
        db.set_setting("service_heartbeat", str(time.time()))

    def _heartbeat(self):
        from db import Database
        db = Database(self.db_path)
        while not self._beat_stop.wait(3):
            if self._service_up:
                self._write_heartbeat(db)

    # -- for tests
    @property
    def db(self):
        return self.ctx.db

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def set_service(self, running: bool):
        """Show the service as running (fresh heartbeat) or stopped (a stale one)."""
        self._service_up = running
        self.ctx.db.set_setting("service_heartbeat", str(time.time() if running else time.time() - 3600))

    def rpc_count(self, name: str | None = None, since: float = 0.0) -> int:
        with self._lock:
            return sum(1 for t, n, _ in self.calls if t >= since and (name is None or n == name))

    def init_script(self) -> str:
        """The test-only shim that stands in for pywebview, plus the counters / visibility switch the perf checks use."""
        return _INIT_SCRIPT.replace("__NAMES__", json.dumps(self.names))

    def stop(self):
        import paths
        self._beat_stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        paths.LOG_PATH = self._old_log


# The page-side part. Order matters: counters first (so every timer made by the page is counted), then the visibility
# switch, then window.pywebview.
_INIT_SCRIPT = r"""
(() => {
  const calls = { timeout: 0, interval: 0, raf: 0 };
  window.__ldCalls = calls;
  const wrap = (name, key) => {
    const orig = window[name].bind(window);
    window[name] = function (fn, ...rest) {
      if (typeof fn !== 'function') return orig(fn, ...rest);
      return orig(function () { calls[key]++; return fn.apply(this, arguments); }, ...rest);
    };
  };
  wrap('setTimeout', 'timeout'); wrap('setInterval', 'interval'); wrap('requestAnimationFrame', 'raf');

  window.__ldHidden = false;
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (window.__ldHidden ? 'hidden' : 'visible') });
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => !!window.__ldHidden });

  const api = {};
  for (const name of __NAMES__) {
    api[name] = (...args) => fetch('/rpc/' + name, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(args) }).then((r) => r.json());
  }
  window.pywebview = { api };
  document.addEventListener('DOMContentLoaded', () => window.dispatchEvent(new Event('pywebviewready')));
})();
"""


# ---------------------------------------------------------------- helpers used by the tests

class Perf:
    """Timing helpers for one Page. All numbers are milliseconds measured inside the page."""

    def __init__(self, page):
        self.page = page

    def fcp(self) -> float:
        """First contentful paint, from the start of the navigation."""
        return self.page.evaluate("""() => {
            const e = performance.getEntriesByName('first-contentful-paint')[0];
            return e ? e.startTime : -1; }""")

    def click_to_frame(self, selector: str) -> float:
        """From click() on `selector` to the next animation frame (what a page switch has to beat: 50 ms)."""
        return self.page.evaluate("""async (sel) => {
            const el = document.querySelector(sel);
            await new Promise((r) => requestAnimationFrame(() => r()));      // start from a frame edge
            const t0 = performance.now();
            el.click();
            await new Promise((r) => requestAnimationFrame(() => r()));
            return performance.now() - t0; }""", selector)

    def click_until(self, click: str, ready: str, timeout_ms: int = 3000) -> float:
        """From click() on `click` until an element matching `ready` exists (e.g. the first visit of a page)."""
        return self.page.evaluate("""async ({click, ready, timeout}) => {
            const t0 = performance.now();
            document.querySelector(click).click();
            while (!document.querySelector(ready)) {
              if (performance.now() - t0 > timeout) return -1;
              await new Promise((r) => requestAnimationFrame(() => r()));
            }
            return performance.now() - t0; }""", {"click": click, "ready": ready, "timeout": timeout_ms})

    def scroll_frames(self, selector: str, seconds: float = 3.0, steps_per_sec: int = 60, probe: str | None = None) -> dict:
        """Scroll `selector` from top to bottom over `seconds` (one programmatic step per frame, about `steps_per_sec`)
        and record every animation-frame gap. `probe` = a CSS selector whose matches are counted each frame.
        -> {frames, max_ms, p95_ms, mean_ms, live_max}"""
        return self.page.evaluate("""async ({sel, seconds, hz, probe}) => {
            const el = document.querySelector(sel);
            const total = el.scrollHeight - el.clientHeight;
            const deltas = []; let live = 0;
            return await new Promise((resolve) => {
              let t0 = 0, last = 0;
              const frame = (t) => {
                if (!t0) { t0 = last = t; }
                else deltas.push(t - last);
                last = t;
                const f = Math.min(1, (t - t0) / (seconds * 1000));
                el.scrollTop = Math.round(total * f);
                if (probe) live = Math.max(live, document.querySelectorAll(probe).length);
                if (f >= 1) {
                  const s = deltas.slice().sort((a, b) => a - b);
                  resolve({ frames: deltas.length, max_ms: s[s.length - 1], p95_ms: s[Math.floor(s.length * 0.95)],
                            mean_ms: deltas.reduce((a, b) => a + b, 0) / deltas.length, live_max: live });
                } else requestAnimationFrame(frame);
              };
              requestAnimationFrame(frame);
            }); }""", {"sel": selector, "seconds": seconds, "hz": steps_per_sec, "probe": probe})

    def set_hidden(self, hidden: bool):
        """Make the page believe its window is hidden (document.visibilityState) - or shown again."""
        self.page.evaluate("""(h) => { window.__ldHidden = h; document.dispatchEvent(new Event('visibilitychange')); }""", hidden)

    def timer_calls(self) -> dict:
        """How many setTimeout / setInterval / requestAnimationFrame callbacks the page has run so far."""
        return self.page.evaluate("() => ({...window.__ldCalls})")

    def reset_timer_calls(self):
        self.page.evaluate("() => { const c = window.__ldCalls; c.timeout = c.interval = c.raf = 0; }")


STUB_REGISTRY = "export const pages = {};\n"


def stub_all_pages(page):
    """Test-only: every page id shows the "Coming soon" stub, whatever other tickets have registered in
    js/pages/index.js - for tests that measure the shell/router itself, not one page."""
    page.route("**/js/pages/index.js", lambda route: route.fulfill(
        status=200, content_type="text/javascript", body=STUB_REGISTRY))


def open_shell(page, harness, width=None, height=None, route=None):
    """Open the app and wait until it is booted. The Playwright Page is returned."""
    if width and height:
        page.set_viewport_size({"width": width, "height": height})
    page.goto(harness.url("/index.html" + (f"#{route}" if route else "")), wait_until="load")
    page.wait_for_function("document.documentElement.dataset.ready === '1'", timeout=10_000)
    return page


def assert_clean(page):
    """No console errors, page errors or failed requests so far."""
    assert not page.errors, "errors in the page:\n  " + "\n  ".join(page.errors)
