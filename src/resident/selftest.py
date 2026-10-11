"""`Lockdown.exe --new-ui --selftest REPORT`: the build check of the new window, with real timings.

Run with a temporary data folder (main.py sets LOCKDOWN_DATA_DIR before anything is imported, as for the old
self-test), so it never touches the running Lockdown, its autostart or its tasks. It boots the windows hidden,
checks that the three windows and the page files exist, calls every `read` Api method once on the temporary
database and - on Windows, where there is a real window - times what Adam feels:

    tray_icon_ms        agent start -> tray icon is up                        (BLUEPRINT: <= 1000)
    main_hydrated_ms    agent start -> the hidden main window has its data    (<= 3000)
    window_show_ms      window.show() -> its first frame is drawn (warm open) (<= 150, hard limit 300)
    page_first_ms       every page: click -> painted, first visit             (<= 150)
    page_reshow_ms      every page: click -> painted, page already mounted    (<= 50)

REPORT gets "OK - ..." and a JSON block of the numbers, or the traceback. Exit code 0 / 1. The numbers are only
reported (`over_budget` lists what missed its target): a slow PC must not make an installer build fail.

Kept thin on purpose: `run()` is plain Python (tested on Linux with a stand-in runtime); `WebviewRuntime` is the
Windows glue (pywebview, tray). `PAGE_TIMING_JS` is also what scripts/perf_ui.py runs in headless Chrome, so the
same measurement is used here and on the Windows PC.
"""
import inspect
import json
import sys
import threading
import time
import traceback

# Sample arguments for a `read` Api method that takes some: name -> [args] or fn(ctx) -> [args]. A page ticket that
# adds such a method adds ONE line here (the self-test and scripts/perf_ui.py then time it with these arguments).
READ_ARGS: dict = {
    "update_notes": ["0.0.0"],     # About: the changelog lines of a waiting release
    "rules_validate": [[{"rule_type": "permanent"}, {"rule_type": "time_limit", "same": True, "day": "1h"}]],   # rule editors: two forms
    "appmenu_info": ["app", "chrome.exe"],     # right-click menu of an app: category, protected, groups
    "blocking_overview_get": ["Next block"],   # Blocking > Overview, in the order that works out the most
    "item_get": [None, "reddit.com"],          # Blocking > Add / Edit: is this site on the list already (null when not)
    "suggest": ["redd"],                       # Blocking > Add: sites and apps for what is typed
    "screentime_get": ["overview", "30 days"],     # Screen Time: the heaviest answer (30 days of days to sum up)
    "screentime_month": [2026, 10],            # Screen Time > Calendar: a month of per-day totals
    "screentime_day": lambda ctx: [ctx.now().date().isoformat()],     # Calendar: a day's top apps (today)
    "group_get": lambda ctx: [(next(iter(ctx.db.list_groups()), None) or {}).get("id")],    # Blocking > Groups: the first group (or a new one)
    "group_member_search": ["you"],            # Blocking > Groups: typing in the Members box
    "calendar_get": [None, 7, "all"],           # Blocking > Calendar: the week, the busiest view
    "protection_check": ["example.com"],       # Blocking > Site protection: "Check a site" (loads the list tables the first time)
    "manual_get": ["mine1"],                   # Blocking > Site protection: one of your own lists (null when it isn't there)
    "wordlist_get": ["adult_en"],              # Blocking > Site protection: a word-list dialog
    "host_normalize": ["https://www.Example.com/x"],     # Blocking > Site protection: the site address of a new entry
    "reset_change_preview": lambda ctx: ["04:30" if f"{ctx.db.limit_clock().time:%H:%M}" != "04:30" else "05:30"],    # Settings > When limits reset: a time other than the saved one
    "mode_extra_check": [{"kind": "site", "text": "reddit.com", "name": "", "ways": ["dns"]}],   # Modes: "+ Other site or app"
    "network_get": [{}, 100],                  # Network Log: the table (the first 100 of the last hour)
    "network_graph": [{}],                     # Network Log: the Graph tab
    "network_block_target": ["site", "rr3.googlevideo.com"],    # Network Log: "Block site..." / "Block app..." from a row
}

# `read` methods that must NOT be called by the self-test or the timing run, with the reason (they open a native
# window, or wait for the owner). One line each.
SKIP_READS: dict = {
    "pick_program_file": "opens Windows' Open dialog",
}

# The page files every build must carry.
UI_FILES = ("index.html", "corner.html", "overlay.html", "js/shell.js", "js/router.js", "css/app.css")

# name: (target, hard limit) in milliseconds - BLUEPRINT section 2 "Startup budget".
BUDGET_MS = {
    "tray_icon_ms": (1000, 1000),
    "main_hydrated_ms": (3000, 3000),
    "window_show_ms": (150, 300),
    "page_first_ms": (150, 300),
    "page_reshow_ms": (50, 100),
}

CHECK_TIMEOUT_SEC = 90     # the whole check, on the GUI side: a hung window ends the self-test instead of waiting for ever
STEP_TIMEOUT_SEC = 20


class SelftestError(Exception):
    """A check failed (the message says what)."""


# ---------------------------------------------------------------- the page-side measurement (also used in Chrome)

# An expression that evaluates to a Promise of {first: {page: ms}, reshow: {page: ms}}. For every sidebar page that
# is not mounted yet: click -> the page has mounted AND finished load() -> one more frame is drawn. Then, for every
# page: click on the nav item -> the next frame (a page that is already mounted just switches).
PAGE_TIMING_JS = r"""(async () => {
  const src = document.querySelector('script[type=module][src*="shell"]').src;
  const { router, PAGE_IDS } = await import(new URL('router.js', src).href);
  const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
  const ready = (id) => { const s = router.state(id); return !!s && s.status === 'ready'; };
  const click = (id) => document.querySelector('#nav [data-nav="' + id + '"]').click();
  const first = {}, reshow = {};
  for (const id of PAGE_IDS) {
    if (ready(id)) continue;
    await frame();
    const t0 = performance.now();
    click(id);
    while (!ready(id) && performance.now() - t0 < 5000) await frame();
    await frame();
    first[id] = ready(id) ? performance.now() - t0 : -1;
  }
  await new Promise((r) => setTimeout(r, 200));
  for (const id of PAGE_IDS) {
    if (router.current === id) continue;
    await frame();
    const t0 = performance.now();
    click(id);
    await frame();
    reshow[id] = performance.now() - t0;
  }
  return { first, reshow };
})()"""

# Starts a rAF loop that remembers the wall-clock time of the latest frame. While the window is hidden Chromium draws no
# frames, so the first stamp after window.show() is the first frame of the warm open.
FRAME_PROBE_JS = ("window.__ldStop = false; window.__ldFrame = 0;"
                  "(function f() { requestAnimationFrame(function () {"
                  " window.__ldFrame = performance.timeOrigin + performance.now(); if (!window.__ldStop) f(); }); })(); 0")


# ---------------------------------------------------------------- the portable part

def read_args(name: str, fn, ctx) -> list:
    """The arguments to call read method `name` with: from READ_ARGS, or none when it needs none."""
    spec = READ_ARGS.get(name)
    if spec is not None:
        return list(spec(ctx) if callable(spec) else spec)
    needed = [p.name for p in list(inspect.signature(fn).parameters.values())[1:]
              if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if needed:
        raise SelftestError(f"read method {name} needs {needed}: add a line for it to READ_ARGS in "
                            "src/resident/selftest.py")
    return []


def call_reads(ctx, registry=None, strict=False) -> dict:
    """Call every `read` method once -> {name: ms}. An exception is NOT caught here: its traceback goes to the report.
    A method that needs arguments nobody listed in READ_ARGS can't be called: strict=True raises SelftestError for it
    (tests/test_rebuild_selftest.py does, so a forgotten line is caught when the method is added); otherwise it is
    left out and run() names it under "not_called" (a build check must not fail for a missing sample)."""
    from webapi import core
    registry = registry or core.registry
    timings = {}
    for name, kind in registry.methods():
        if kind != "read" or name in SKIP_READS:
            continue
        fn = registry.get(name)[0]
        try:
            args = read_args(name, fn, ctx)
        except SelftestError:
            if strict:
                raise
            timings[name] = None
            continue
        t = time.perf_counter()
        core.jsonable(fn(ctx, *args))
        timings[name] = round((time.perf_counter() - t) * 1000, 1)
    return timings


def check_ui_files() -> list:
    from resident.assets import ui_dir
    root = ui_dir()
    return [name for name in UI_FILES if not (root / name).is_file()]


def over_budget(result: dict) -> list:
    """['name: 412 ms (target 150, limit 300)'] for each timing over its target (page maps: the worst page)."""
    out = []
    for name, (target, limit) in BUDGET_MS.items():
        value = result.get(name)
        if isinstance(value, dict):
            value = max(value.values(), default=None)
        if isinstance(value, (int, float)) and value > target:
            out.append(f"{name}: {value:.0f} ms (target {target}, limit {limit})" + (" - OVER THE LIMIT" if value > limit else ""))
    return out


def run(report: str, runtime=None, registry=None, ctx=None) -> int:
    """The self-test. `runtime` boots the windows (WebviewRuntime on Windows; tests pass a stand-in); `registry`
    and `ctx` are the Api's (tests pass their own). Writes the result to `report`, returns the exit code."""
    result = {}
    try:
        if ctx is None:
            import webapi
            ctx = webapi.make_context()
        runtime = runtime or WebviewRuntime(ctx)

        def check(env):
            windows = env.windows
            missing = [n for n in ("main", "corner", "overlay") if getattr(windows, n, None) is None]
            if missing:
                raise SelftestError(f"windows not created: {missing}")
            absent = check_ui_files()
            if absent:
                raise SelftestError(f"missing from the build: {absent}")
            reads = call_reads(ctx, registry)
            result["read_methods"] = {n: ms for n, ms in reads.items() if ms is not None}
            result["skipped_on_purpose"] = dict(SKIP_READS)
            result["not_called"] = sorted(n for n, ms in reads.items() if ms is None)   # (needs a READ_ARGS line)
            result.update(env.timings())

        runtime.run(check)
        result["over_budget"] = over_budget(result)
        text = f"OK - 3 windows, {len(result['read_methods'])} read methods\n" + json.dumps(result, indent=2, sort_keys=True)
    except BaseException:
        text = traceback.format_exc()
    try:
        with open(report, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        return 1
    return 0 if text.startswith("OK") else 1


# ---------------------------------------------------------------- the Windows part (pywebview, tray)

class _IdleThread:
    """Stands in for the usage tracker / word guard / retention thread, so the self-test's timings aren't disturbed by
    them watching the real desktop. (Their libraries are import-checked in WebviewRuntime.run instead.)"""
    in_use = False

    def __init__(self):
        self.stop_event = threading.Event()

    def is_alive(self):
        return True

    def start(self):
        pass

    def stop(self):
        pass


class WebviewRuntime:
    """Boots the agent's pieces the way agent.main does (asset server, Api, three hidden windows, publisher, workers,
    tray), runs `check(env)` once the GUI loop is up, then closes everything."""

    def __init__(self, ctx, log=None):
        self.ctx, self.log = ctx, log
        self.windows = None
        self.t0 = time.time()          # "agent start"
        self.tray_ms = None
        self.tray = None

    def run(self, check):
        import ui_theme
        import webapi
        from gui.tray import Tray
        from resident import windows, workers
        from resident.assets import AssetServer
        if sys.platform == "win32":    # the build must carry what the old self-test checked too
            import comtypes.client  # noqa: F401
            import uiautomation as auto
            auto.GetRootControl().Name
        self.t0 = time.time()
        server = AssetServer()
        base_url = server.start()
        win = self.windows = windows.create(base_url, ui_theme.background_color(), js_api=webapi.build_api(self.ctx))
        windows.attach_publisher(win, self.log)
        pool = workers.Workers(tracker=_IdleThread(), word_guard=_IdleThread(), retention=_IdleThread(),
                               log_sink=self.log)
        failure = []

        def finish():
            for stop in (lambda: self.tray and self.tray.stop(), pool.stop, win.publisher.stop):
                try:
                    stop()
                except Exception:
                    pass
            windows.quit_agent(win)

        def ready():
            try:
                self.tray = Tray(on_open=lambda: None, on_exit=lambda: None)
                self.tray.start()
                self._wait(lambda: getattr(self.tray.icon, "visible", False), "the tray icon")
                self.tray_ms = (time.time() - self.t0) * 1000
                pool.hub.listeners += [workers.tray_listener(self.tray), workers.publisher_listener(win)]
                pool.start()
                check(Env(self))
            except BaseException:
                failure.append(traceback.format_exc())
            finally:
                watchdog.cancel()
                finish()

        def timed_out():
            failure.append(f"The self-test did not finish within {CHECK_TIMEOUT_SEC} s.")
            finish()

        watchdog = threading.Timer(CHECK_TIMEOUT_SEC, timed_out)
        watchdog.daemon = True
        watchdog.start()
        try:
            windows.start(ready)
        finally:
            watchdog.cancel()
            server.stop()
        if failure:
            raise SelftestError(failure[0])

    @staticmethod
    def _wait(condition, what, timeout=STEP_TIMEOUT_SEC):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.01)
        raise SelftestError(f"timed out waiting for {what} ({timeout} s)")


class Env:
    """What `check` gets: the windows, and the Windows timings."""

    def __init__(self, runtime: WebviewRuntime):
        self.rt = runtime
        self.windows = runtime.windows

    def _js(self, script):
        return self.windows.main.evaluate_js(script)

    def timings(self) -> dict:
        from resident import windows
        rt, ctx, win = self.rt, self.rt.ctx, self.windows
        out = {"tray_icon_ms": round(rt.tray_ms)}
        rt._wait(lambda: getattr(ctx, "ui_ready_at", None), "the hidden main window to load its data")
        out["main_hydrated_ms"] = round((ctx.ui_ready_at - rt.t0) * 1000)
        time.sleep(0.5)                                  # let the start-up work settle: this is a WARM open
        self._js(FRAME_PROBE_JS)
        shown_at = time.time() * 1000
        windows.show_main(win)
        frame = rt._wait(lambda: (self._js("window.__ldFrame") or 0) > shown_at and self._js("window.__ldFrame"),
                         "the first frame after show()")
        out["window_show_ms"] = round(frame - shown_at)
        self._js("window.__ldTiming = null; (" + PAGE_TIMING_JS + ").then(function (r) { window.__ldTiming = JSON.stringify(r); }); 0")
        raw = rt._wait(lambda: self._js("window.__ldTiming"), "the page timings")
        pages = json.loads(raw) if isinstance(raw, str) else raw
        out["page_first_ms"] = {k: round(v, 1) for k, v in pages["first"].items()}
        out["page_reshow_ms"] = {k: round(v, 1) for k, v in pages["reshow"].items()}
        self._js("window.__ldStop = true")
        windows.close_means_hide(win)
        return out
