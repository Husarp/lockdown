"""The performance measurements of the rebuilt UI, in one place: used by scripts/perf_ui.py (prints the table, writes
perf-report.json) and tests/ui/test_perf_budget.py (the same numbers as pass / fail tests).

What is measured (BLUEPRINT section 2 budget; "target" = what we aim for, "limit" = what a test allows on a slow CI box)
    api_read_p95_ms      every registered `read` Api method, 20 runs on a synthetic database, p50 / p95
    page_first_ms        every sidebar page: click -> mounted + load() done + one frame (headless Chrome)
    page_reshow_ms       every page already mounted: click -> next frame
    view_first_ms        tabs / sub-views registered in VIEWS: click -> their content is there
    scroll_gap_ms        long lists: worst gap between two frames while scrolling (the synthetic 10 000-row vlist, plus
                         every list registered in SCROLLS)
    heap_growth_mb       JS heap growth over 10 page switches (after a garbage collection)
    hidden_timers / hidden_rpc   timer callbacks / Api calls in 5 s while the window is hidden: must be 0
    fcp_ms               first contentful paint of the shell

NEW PAGES PLUG IN HERE - everything is picked up automatically, nothing to register for the ten sidebar pages (their
ids come from the router; a page that isn't built yet is measured as its stub). For the parts INSIDE a page add ONE
line to the lists below:
    VIEWS.append(("screentime/apps", "screentime", '[data-tab="apps"]', ".st-apps .row"))
              # label,             page id,      click this,         wait for this to exist
    SCROLLS.append(("netlog 20k rows", "netlog", "#nl-list", ".vl-row:not([hidden])"))
              # label,                 page id,  the scrolling element,  rows to count (live rows must stay small)
and for a `read` Api method that takes arguments add a line to READ_ARGS in src/resident/selftest.py.
A VIEWS / SCROLLS line whose selector matches nothing is NOT a crash and NOT silently ignored: it is listed under
`skipped` in the report ("NOT MEASURED - selector matched nothing") and test_perf_budget fails on it.

R29: the agent warms things up a few seconds after it starts (Network Log, Screen Time, Site protection tables - see
`warm_up`). The page first-visit numbers are therefore taken twice on the same database: `page_first_cold_ms` = the first
visit before any warm-up (the window opened in the first seconds), `page_first_ms` = after `warm_up` (what the owner
normally gets). Both count against the budget.
"""
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 11, 15, 42, 17)      # = harness.NOW = the end of the synthetic data

# name: (target, limit). A value over the limit FAILS, over the target only warns.
BUDGET = {
    "api_read_p95_ms": (50, 100),
    "page_first_ms": (150, 300),
    "view_first_ms": (150, 300),
    "page_reshow_ms": (50, 100),
    "scroll_gap_ms": (50, 100),
    "heap_growth_mb": (5, 5),
    "hidden_timers": (0, 0),
    "hidden_rpc": (0, 0),
    "fcp_ms": (300, 600),
    "push_per_s": (1, 2),
}

VIEWS: list = []        # (label, page id, click selector, ready selector)
SCROLLS: list = []      # (label, page id, scroll selector, probe selector)
SCROLLS.append(("blocking overview", "blocking", "#bo-list", ".vl-row:not([hidden])"))     # R18: the Overview's virtual list
VIEWS.append(("blocking/add", "blocking", '[data-tab="add"]', ".ad-row"))     # R20: the Add / Edit tab, first visit (picker list on screen)
VIEWS.append(("screentime/websites", "screentime", '[data-tab="websites"]', '[data-pane="websites"] .st-vl .vl-row'))     # R14: a tab, first visit
VIEWS.append(("screentime/switches", "screentime", '[data-tab="switches"]', '[data-pane="switches"] svg path.ch-bar'))     # R15: a tab, first visit
VIEWS.append(("screentime/calendar", "screentime", '.page-host[data-page="screentime"] [data-tab="calendar"]', '[data-pane="calendar"] .dc:not(.blank)'))     # R15: a tab, first visit (the month is on screen)
VIEWS.append(("screentime/apps", "screentime", '[data-tab="apps"]', '[data-pane="apps"] .st-vl .vl-row'))                     # (apps last: the page then stays on it for the scroll below)
SCROLLS.append(("screentime apps", "screentime", '[data-pane="apps"] .st-vl', ".vl-row:not([hidden])"))
VIEWS.append(("blocking/calendar", "blocking", '[data-tab="calendar"]', ".cal-row"))     # R22: a tab, first visit
VIEWS.append(("blocking/groups", "blocking", '[data-tab="groups"]', ".gp-card[data-id]"))     # R21: a tab, first visit
VIEWS.append(("blocking/protect", "blocking", '[data-tab="protect"]', '.pl-row[data-key="scam"]'))     # R23: Site protection, first visit
VIEWS.append(("modes/editor", "modes", '[data-act="new"]', '.md-editor:not([hidden]) [data-k="name"]'))     # R25: the editor module, first open
VIEWS.append(("netlog/graph", "netlog", '[data-seg="nlview"] [data-v="graph"]', ".nl-graph svg path.ch-bar"))     # R16: the Graph tab, first visit
VIEWS.append(("notifications/messages", "notifications", '[data-act="messages"]', '[data-k="msgs"]:not([hidden]) .nt-msg'))     # R27: the Messages section opens
VIEWS.append(("settings/new-kind", "settings", '[data-k="em-add"]', '[data-k="em-new"]:not([hidden])'))     # R27: the emergency card's "Add another kind" form opens
SCROLLS.append(("netlog 20k rows", "netlog", "#nl-list", ".vl-row:not([hidden])"))     # R16: the log (the next batch is fetched near the end)

# the databases: (label, days, rolled up by the retention job, counts against the budget)
DATABASES = [("60d", 60, False, True), ("730d-raw", 730, False, False), ("730d-rolled", 730, True, True)]
ITEMS, NETWORK_ROWS = 40, 20_000
RUNS = 20
HIDDEN_SECONDS = 5
CHROME_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required"]


def setup_paths():
    """Make src/ and tests/ importable and, off Windows, install the inert Windows fakes (tests/conftest.py)."""
    for p in (ROOT / "src", ROOT / "tests", ROOT / "tests" / "ui"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    if sys.platform != "win32":
        import conftest  # noqa: F401


LIST_KEYS = ("scam", "phishing", "malware", "adult")      # built-in lists the synthetic files stand in for
LIST_HOSTS = 600_000                                      # sites over all of them (the real lists hold about a million)


def build_lists(folder: Path, hosts: int = LIST_HOSTS) -> Path:
    """Synthetic list files (one site per line, some "*." lines, like the downloaded ones) so that "Check a site" has real
    tables to load: `hosts` sites over LIST_KEYS. -> the folder (set it as ctx.protection_dir)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    each = max(1, hosts // len(LIST_KEYS))
    for n, key in enumerate(LIST_KEYS):
        with open(folder / f"{key}.txt", "w", encoding="utf-8") as f:
            f.writelines((f"*.w{n}x{i}.example\n" if i % 50 == 0 else f"w{n}x{i}.example\n") for i in range(each))
    return folder


def warm_up(ctx) -> dict:
    """What the agent does by itself a few seconds after it started: the Network Log reads the last hour ahead (workers
    NetLogWorker.setup -> network.prewarm), Screen Time works out its day sums (agent.py -> screentime.start_warmup) and
    Site protection loads the lists' tables (-> protection.start_warmup). -> {what: ms}."""
    setup_paths()
    from webapi import network, protection, screentime
    took = {}
    for name, fn in (("network", network.prewarm), ("screentime", screentime.warm), ("protection", protection.warm)):
        t = time.perf_counter()
        fn(ctx)
        took[name] = round((time.perf_counter() - t) * 1000, 1)
    return took


# ---------------------------------------------------------------- numbers

def percentile(values, q: float) -> float:
    """Nearest-rank percentile (q in 0..100) of a non-empty list."""
    s = sorted(values)
    return s[max(0, min(len(s) - 1, int(-(-q * len(s) // 100)) - 1))]


def summarize(samples) -> dict:
    return {"n": len(samples), "p50": round(statistics.median(samples), 2), "p95": round(percentile(samples, 95), 2),
            "max": round(max(samples), 2)}


# ---------------------------------------------------------------- databases

def build_db(path: Path, days: int, rolled: bool = False) -> dict:
    """A synthetic database: `days` of history, 40 blocked items, 20 000 network rows; optionally after the retention
    job (30+ days of detail, daily totals for the rest - what a real install looks like). -> facts about it."""
    setup_paths()
    from db import Database
    from synthdata import build
    t = time.perf_counter()
    Database(path).close()
    build(path, NOW.date(), days=days, extra_items=ITEMS, network_rows=NETWORK_ROWS)
    rolled_rows = 0
    if rolled:
        import retention
        db = Database(path)
        rolled_rows = retention.run(db, NOW.date())
        db.close()
    db = Database(path)
    facts = {"days": days, "rolled_up": rolled, "build_s": round(time.perf_counter() - t, 1),
             "activity_rows": db.conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0],
             "network_rows": db.conn.execute("SELECT COUNT(*) FROM network_log").fetchone()[0],
             "items": db.item_count(), "detail_rows_rolled_up": rolled_rows}
    db.close()
    facts["size_mb"] = round(Path(path).stat().st_size / 1e6, 1)
    return facts


# ---------------------------------------------------------------- Python: the Api

def time_api(db_path: Path, runs: int = RUNS, lists_dir: Path | None = None) -> dict:
    """Every registered `read` method through the real exposed Api object (validation + JSON conversion included), on
    the database at `db_path`. -> {"methods": {name: {first, n, p50, p95, max}}, "not_measured": {name: why}, "skipped_on_purpose": {...}}"""
    setup_paths()
    import webapi
    from resident import selftest
    from webapi import core
    ctx = webapi.make_context(db_path, clock=lambda db: NOW)
    if lists_dir is not None:
        ctx.protection_dir = lists_dir          # ("Check a site" then loads real tables: its first call is the cold one)
    api = core.build_api(ctx)
    methods, skipped, intended = {}, {}, {}
    for name, kind in core.api_methods():
        if kind != "read":
            continue
        if name in selftest.SKIP_READS:
            intended[name] = selftest.SKIP_READS[name]
            continue
        try:
            args = selftest.read_args(name, core.registry.get(name)[0], ctx)
        except selftest.SelftestError as e:
            skipped[name] = str(e)
            continue
        fn = getattr(api, name)
        t = time.perf_counter()
        reply = fn(*args)
        first = (time.perf_counter() - t) * 1000
        if not reply.get("ok"):
            skipped[name] = f"returned an error: {reply.get('error')}"
            continue
        samples = []
        for _ in range(runs):
            t = time.perf_counter()
            fn(*args)
            samples.append((time.perf_counter() - t) * 1000)
        methods[name] = {"first": round(first, 2), **summarize(samples)}
    return {"methods": methods, "not_measured": skipped, "skipped_on_purpose": intended}


# ---------------------------------------------------------------- Python: pushes while the window is shown

def measure_pushes(db_path: Path, seconds: float = 12.0) -> dict:
    """"Push frequency while visible <= 1 per second normally" (BLUEPRINT 2): the real StatusWorker (one snapshot a second)
    and the real Publisher (window shown) in front of a window that only counts `evaluate_js` calls, over `seconds` of
    an idle database - the agent without any page doing anything. The first call (the whole status, sent once when the
    page connects) is counted apart. -> {seconds, sends, first_at_s, per_second, busiest_second, events}"""
    setup_paths()
    from resident import publisher, workers
    sent = []

    class Window:
        def evaluate_js(self, script):
            sent.append((time.monotonic(), script))

    class Win:
        pass
    win = Win()
    win.publisher = pub = publisher.Publisher(Window(), visible=True)
    hub = workers.Hub(db_path)
    hub.listeners.append(workers.publisher_listener(win))
    worker = workers.StatusWorker(hub)
    t0 = time.monotonic()
    worker.start()
    time.sleep(seconds)
    worker.stop(3)
    pub.stop()
    first = sent[0][0] - t0 if sent else None
    later = [t for t, _ in sent[1:]]
    events = {}
    for _, script in sent:
        for name in re.findall(r'\["([a-z_]+)",', script):
            events[name] = events.get(name, 0) + 1
    busiest = max((sum(1 for u in later if t <= u < t + 1) for t in later), default=0)
    return {"seconds": seconds, "sends": len(sent), "first_at_s": None if first is None else round(first, 2),
            "per_second": round(len(later) / seconds, 3), "busiest_second": busiest, "events": events}


# ---------------------------------------------------------------- the browser

def open_page(browser, harness):
    """A fresh Playwright context + page on the harness (Chrome 1280x800, the pywebview shim, error collection)."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script(harness.init_script())
    page = ctx.new_page()
    page.errors = []
    page.on("console", lambda m: page.errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: page.errors.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: page.errors.append(f"requestfailed: {r.url} {r.failure}") if "ERR_ABORTED" not in (r.failure or "") else None)   # (a cancelled <img> of a reused row is not a failure)
    return ctx, page


def go_page(page, page_id: str):
    page.click(f'#nav [data-nav="{page_id}"]')
    page.wait_for_selector(f'.page-host[data-page="{page_id}"]', state="visible")


def _heap_mb(cdp) -> float:
    cdp.send("HeapProfiler.collectGarbage")
    metrics = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
    return metrics["JSHeapUsedSize"] / 1e6


def measure_browser(browser, harness, fcp_runs: int = 5, scroll_seconds: float = 3.0,
                    hidden_seconds: float = HIDDEN_SECONDS, log=lambda text: None) -> dict:
    """Everything that needs Chrome. `harness` = a tests/ui/harness.UiHarness on the database to measure on."""
    setup_paths()
    import harness as H
    from resident.selftest import PAGE_TIMING_JS
    out = {"errors": []}

    def boot(page, route=None):
        page.goto("about:blank")
        page.goto(harness.url("/index.html" + (f"#{route}" if route else "")), wait_until="load")
        return page.evaluate("""() => new Promise((resolve) => {
            const done = () => resolve(performance.now());
            if (document.documentElement.dataset.ready === '1') return done();
            new MutationObserver((_, o) => { if (document.documentElement.dataset.ready === '1') { o.disconnect(); done(); } })
              .observe(document.documentElement, {attributes: true});
        })""")

    # -- shell first paint and boot
    ctx, page = open_page(browser, harness)
    perf = H.Perf(page)
    fcp, ready = [], []
    for _ in range(fcp_runs):
        ready.append(boot(page))
        page.wait_for_function("performance.getEntriesByName('first-contentful-paint').length > 0")
        fcp.append(perf.fcp())
    out["fcp_ms"] = [round(v, 1) for v in fcp]
    out["shell_ready_ms"] = [round(v, 1) for v in ready]
    log("shell boot done")

    # -- every page: first load and re-show (opened on a stub page so the dashboard's own first load is measured too)
    boot(page, "about")
    timing = page.evaluate(PAGE_TIMING_JS)
    first, reshow = dict(timing["first"]), dict(timing["reshow"])
    ids = page.eval_on_selector_all("#nav [data-nav]", "els => els.map(e => e.dataset.nav)")
    out["pages"] = ids
    log("pages done")

    # -- tabs / sub-views and long lists registered by the page tickets
    views, scrolls, skipped = {}, {}, {}
    for label, page_id, click, ready_sel in VIEWS:
        go_page(page, page_id)
        ms = perf.click_until(click, ready_sel)
        if ms == -2:
            skipped[label] = f"the click selector matched nothing: {click}"
        else:
            views[label] = round(ms, 1)
    for label, page_id, selector, probe in SCROLLS:
        go_page(page, page_id)
        r = perf.scroll_frames(selector, seconds=scroll_seconds, probe=probe)
        if r.get("missing"):
            skipped[label] = f"the scrolling element matched nothing: {selector}"
        else:
            scrolls[label] = {k: round(v, 1) for k, v in r.items()}

    # -- heap growth over 10 page switches (everything visited once first, so lazy loading isn't counted)
    cdp = ctx.new_cdp_session(page)
    cdp.send("Performance.enable")
    go_page(page, ids[0])
    before = _heap_mb(cdp)
    for i in range(10):
        page.click(f'#nav [data-nav="{ids[(i + 1) % len(ids)]}"]')
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
    after = _heap_mb(cdp)
    out["heap"] = {"before_mb": round(before, 2), "after_mb": round(after, 2), "growth_mb": round(after - before, 2),
                   "switches": 10}

    # -- a hidden window does nothing
    perf.set_hidden(True)
    page.wait_for_timeout(300)
    perf.reset_timer_calls()
    calls = len(harness.calls)
    page.wait_for_timeout(int(hidden_seconds * 1000))
    timers = perf.timer_calls()
    out["hidden"] = {"seconds": hidden_seconds, "timers": timers, "timer_total": sum(timers.values()),
                     "rpc": len(harness.calls) - calls}
    perf.set_hidden(False)
    out["errors"] += page.errors
    ctx.close()
    log("heap + hidden done")

    # -- the dashboard is the page the app opens on: measure the first visit of "about" opened on the dashboard
    ctx, page = open_page(browser, harness)
    boot(page, "dashboard")
    timing = page.evaluate(PAGE_TIMING_JS)
    if "about" in timing["first"]:
        first["about"] = timing["first"]["about"]
    out["errors"] += page.errors
    ctx.close()

    # -- the virtual list with 10 000 rows (the component every long list uses)
    ctx, page = open_page(browser, harness)
    page.goto(harness.url("/__t/vlist.html"))
    page.wait_for_function("window.__ready === true")
    r = H.Perf(page).scroll_frames("#list", seconds=scroll_seconds, probe="#list > .vl-row:not([hidden])")
    scrolls["vlist 10 000 rows"] = {k: round(v, 1) for k, v in r.items()}
    out["errors"] += page.errors
    ctx.close()

    # -- the same first visits once more, after the agent's own warm-ups (R29): cold = the window opened right after the
    #    agent started, warm = what the owner normally gets
    out["warmed_ms"] = warm_up(harness.ctx)
    ctx, page = open_page(browser, harness)
    boot(page, "about")
    warm_first = dict(page.evaluate(PAGE_TIMING_JS)["first"])
    boot(page, "dashboard")
    timing = page.evaluate(PAGE_TIMING_JS)
    if "about" in timing["first"]:
        warm_first["about"] = timing["first"]["about"]
    out["errors"] += page.errors
    ctx.close()

    out["page_first_cold_ms"] = {k: round(v, 1) for k, v in first.items()}
    out["page_first_ms"] = {k: round(v, 1) for k, v in warm_first.items()}
    out["page_reshow_ms"] = {k: round(v, 1) for k, v in reshow.items()}
    out["views_first_ms"] = views
    out["scrolls"] = scrolls
    out["skipped"] = skipped
    return out


# ---------------------------------------------------------------- the budget as rows

def _status(value, target, limit) -> str:
    return "ok" if value <= target else "WARN" if value <= limit else "FAIL"


def budget_rows(report: dict) -> list:
    """[{name, what, value, target, limit, status}] for everything measured (what the table prints and the tests
    assert). Databases marked as not budgeted (the un-rolled 2-year one) are shown in the table only."""
    rows = []

    def add(name, what, value, unit="ms"):
        target, limit = BUDGET[name]
        rows.append({"name": name, "what": what, "value": value, "unit": unit, "target": target, "limit": limit,
                     "status": _status(value, target, limit)})

    for label, info in report.get("api", {}).items():
        if not info.get("budgeted", True):
            continue
        for method, s in sorted(info["methods"].items()):
            add("api_read_p95_ms", f"Api {method} p95 [{label}]", s["p95"])
    if report.get("pushes"):
        add("push_per_s", f"pushes per second while shown, idle ({report['pushes']['seconds']:g} s)", report["pushes"]["per_second"], "/s")
    b = report.get("browser")
    if b:
        add("fcp_ms", "shell first contentful paint (worst of runs)", max(b["fcp_ms"]))
        for page, v in sorted(b.get("page_first_cold_ms", {}).items()):
            add("page_first_ms", f"page first visit, cold: {page}", v)
        for page, v in sorted(b["page_first_ms"].items()):
            add("page_first_ms", f"page first visit, warm: {page}", v)
        for page, v in sorted(b["page_reshow_ms"].items()):
            add("page_reshow_ms", f"page re-show: {page}", v)
        for label, v in sorted(b["views_first_ms"].items()):
            add("view_first_ms", f"view first visit: {label}", v)
        for label, s in sorted(b["scrolls"].items()):
            add("scroll_gap_ms", f"scroll, worst frame gap: {label}", s["max_ms"])
        add("heap_growth_mb", "JS heap growth over 10 page switches", b["heap"]["growth_mb"], "MB")
        add("hidden_timers", f"timer callbacks in {b['hidden']['seconds']:g} s hidden", b["hidden"]["timer_total"], "")
        add("hidden_rpc", f"Api calls in {b['hidden']['seconds']:g} s hidden", b["hidden"]["rpc"], "")
    return rows


def format_report(report: dict) -> str:
    """The table for the terminal."""
    lines = []
    for label, info in report.get("databases", {}).items():
        lines.append(f"database {label}: {info['activity_rows']:,} activity rows, {info['network_rows']:,} network rows, "
                     f"{info['items']} items, {info['size_mb']} MB (built in {info['build_s']} s)"
                     + ("" if info.get("budgeted", True) else "  [not budgeted: the worst case before retention]"))
    for label, info in report.get("api", {}).items():
        lines += ["", f"Api read methods on {label} ({RUNS} runs, ms):",
                  f"  {'method':<28}{'first':>8}{'p50':>8}{'p95':>8}{'max':>8}"]
        for name, s in sorted(info["methods"].items()):
            lines.append(f"  {name:<28}{s['first']:>8.2f}{s['p50']:>8.2f}{s['p95']:>8.2f}{s['max']:>8.2f}")
        for name, why in sorted(info["not_measured"].items()):
            lines.append(f"  {name:<28}NOT MEASURED - {why}")
        for name, why in sorted(info.get("skipped_on_purpose", {}).items()):
            lines.append(f"  {name:<28}skipped on purpose - {why}")
    if report.get("pushes"):
        p = report["pushes"]
        lines += ["", f"Pushes to a shown window, idle, {p['seconds']:g} s: {p['sends']} evaluate_js calls (the first, the whole status, at "
                      f"{p['first_at_s']} s), then {p['per_second']}/s, busiest second {p['busiest_second']}; events {p['events']}"]
    b = report.get("browser")
    if b:
        lines += ["", "Headless Chrome:", f"  shell first contentful paint: {b['fcp_ms']} ms; booted (data in) at {b['shell_ready_ms']} ms",
                  f"  {'page':<16}{'cold':>10}{'warm':>10}{'re-show':>10}   (first visit before / after the agent's warm-ups)"]
        for page in b["pages"]:
            lines.append(f"  {page:<16}{b.get('page_first_cold_ms', {}).get(page, float('nan')):>10.1f}"
                         f"{b['page_first_ms'].get(page, float('nan')):>10.1f}{b['page_reshow_ms'].get(page, float('nan')):>10.1f}")
        if b.get("warmed_ms"):
            lines.append(f"  warm-ups took (ms): {b['warmed_ms']}")
        for label, why in sorted(b.get("skipped", {}).items()):
            lines.append(f"  {label}: NOT MEASURED - {why}")
        for label, v in b["views_first_ms"].items():
            lines.append(f"  view {label}: {v} ms")
        for label, s in b["scrolls"].items():
            lines.append(f"  scroll {label}: {s['frames']} frames, max gap {s['max_ms']} ms, p95 {s['p95_ms']} ms, "
                         f"mean {s['mean_ms']} ms, live rows max {s['live_max']}")
        h = b["heap"]
        lines.append(f"  heap over {h['switches']} switches: {h['before_mb']} -> {h['after_mb']} MB ({h['growth_mb']:+} MB)")
        hd = b["hidden"]
        lines.append(f"  hidden {hd['seconds']:g} s: timer callbacks {hd['timers']}, Api calls {hd['rpc']}")
        if b["errors"]:
            lines.append(f"  PAGE ERRORS: {b['errors']}")
    elif "browser_skipped" in report:
        lines += ["", f"Headless Chrome: skipped ({report['browser_skipped']})"]
    rows = budget_rows(report)
    bad = [r for r in rows if r["status"] != "ok"]
    lines += ["", f"Budget: {len(rows) - len(bad)} of {len(rows)} measurements within target"]
    for r in bad:
        lines.append(f"  {r['status']:<5}{r['what']}: {r['value']}{r['unit']} (target {r['target']}, limit {r['limit']})")
    return "\n".join(lines)


def temp_dir(prefix="lockdown-perf-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def chrome_missing() -> str | None:
    """Why the browser part can't run here (None = it can)."""
    setup_paths()
    import harness as H
    if not H.available():
        return f"playwright or Chrome ({H.CHROME}) is missing"
    return None


def launch(playwright):
    import harness as H
    return playwright.chromium.launch(executable_path=H.CHROME, headless=True, args=CHROME_ARGS)


def make_harness(tmp_path: Path, days: int = 730, rolled: bool = True):
    """A UiHarness whose database is the synthetic one (730 days, rolled up by default)."""
    setup_paths()
    import harness as H
    h = H.UiHarness(tmp_path, days=0)
    info = build_db(h.db_path, days, rolled)
    h.ctx.protection_dir = build_lists(tmp_path / "lists")
    return h, info
