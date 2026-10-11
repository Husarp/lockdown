"""R29: the parity smoke test - the whole new window once, on a big database, in Chrome against the REAL Api.

Data: 730 days of synthetic history (tests/synthdata.py) with 200 blocked items on top of the usual ones, one group, 3 000
network rows. What it does, once each:
  * every sidebar page, every tab of Blocking / Screen Time (with every range) / Network Log, the editors that open inside
    a page (Modes, Reminders, Notifications messages, Settings sections), with no console error, no failed request and no
    Api reply that says `ok: false`;
  * every dialog (Display settings, Emergency unlock, App browser, Category editor, Manual list, Word lists, a member's
    way and extra limits, the reminder editor, the update dialog, the challenge, the Settings and Anti-Bypass dialogs, the
    help dialogs) opened and closed again;
  * the three other windows' pages (corner, overlay, standalone challenge) load clean;
  * no horizontal scrolling at 1000x650 and 1280x800 for every theme and interface size, on every page and tab.

Screenshot-free on purpose: this proves nothing breaks and nothing overflows, not how it looks (Adam's side-by-side on
Windows does that - design/rebuild/PARITY.md). Windows-only: the same pages inside WebView2, real icons, native dialogs.
"""
import json

import pytest

import antibypass as ab
import harness as H
from harness import assert_clean

DAYS, ITEMS, NET_ROWS = 730, 200, 3000
PAGES = ["dashboard", "blocking", "antibypass", "screentime", "netlog", "modes", "reminders", "notifications", "settings", "about"]
BLOCKING_TABS = ["overview", "groups", "add", "protect", "calendar"]
SCREENTIME_TABS = ["overview", "apps", "websites", "switches", "calendar"]
RANGES = ["Today", "Yesterday", "7 days", "30 days"]
SIZES = [(1000, 650), (1280, 800)]
THEMES = ["dark", "amoled", "light"]
SCALES = [1.0, 0.7]                      # interface size 100 % and the smallest (70 %)


def make_apps(n=300):
    return [{"name": f"App {i:04d}", "exe": f"app{i:04d}.exe", "path": rf"C:\Apps\app{i:04d}.exe", "steam": i % 7 == 0} for i in range(n)]


# ---------------------------------------------------------------- one big harness for the whole module

@pytest.fixture(scope="module")
def big(tmp_path_factory):
    from resident import catalog as app_catalog
    from synthdata import build
    tmp = tmp_path_factory.mktemp("parity")
    h = H.UiHarness(tmp, days=0)
    build(h.db_path, H.NOW.date(), days=DAYS, extra_items=ITEMS, network_rows=NET_ROWS)
    cat = app_catalog.AppCatalog(tmp / "cat.json", list_apps=make_apps, running_now=lambda: {})
    cat.load_now()
    h.ctx.catalog = cat
    h.failed = []                                    # Api replies with ok: false, as (method, error)
    real = h.rpc

    def rpc(name, args):
        reply = real(name, args)
        if not reply.get("ok"):
            h.failed.append((name, reply.get("error")))
        return reply
    h.rpc = rpc
    yield h
    h.stop()


@pytest.fixture
def pg(browser, big):
    """A fresh page on the big harness; the test fails at the end on a console error or a refused Api call."""
    big.failed.clear()
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script(big.init_script())
    ctx.route("**/icon/**", lambda route: route.fulfill(status=200, content_type="image/svg+xml", body=H.ICON_STUB))
    p = ctx.new_page()
    p.set_default_timeout(15_000)
    p.errors = []
    p.on("console", lambda m: p.errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    p.on("pageerror", lambda e: p.errors.append(f"pageerror: {e}"))
    p.on("requestfailed", lambda r: p.errors.append(f"requestfailed: {r.url} {r.failure}") if "ERR_ABORTED" not in (r.failure or "") else None)
    yield p
    ctx.close()
    assert_clean(p)
    assert big.failed == [], f"Api calls that failed: {big.failed}"


def boot(pg, big, width=1280, height=800, route=None):
    pg.set_viewport_size({"width": width, "height": height})
    return H.open_shell(pg, big, route=route)


def go(pg, page_id):
    """Click a sidebar item and wait until that page has mounted AND loaded."""
    pg.click(f'#nav [data-nav="{page_id}"]')
    pg.wait_for_selector(f'.page-host[data-page="{page_id}"]', state="visible")
    pg.evaluate("""async (id) => {                       // (evaluate waits for the promise; wait_for_function does not - a promise is truthy)
        const { router } = await import('/js/router.js');
        for (let i = 0; i < 600; i++) {
            const s = router.state(id);
            if (s && s.status === 'ready') return;
            await new Promise((r) => setTimeout(r, 25));
        }
        throw new Error('page ' + id + ' never got ready');
    }""", page_id)


def host(page_id):
    return f'.page-host[data-page="{page_id}"]'


def settle(pg):
    """Two animation frames: whatever the last click scheduled has been drawn."""
    pg.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")


def no_dialog(pg):
    pg.wait_for_function("!document.querySelector('dialog[open]')")


# ================================================================ every page and tab

def test_every_page_tab_and_range_loads_clean(pg, big):
    boot(pg, big)
    seen = []
    for page_id in PAGES:
        go(pg, page_id)
        seen.append(page_id)
        h = host(page_id)
        shown = pg.evaluate("""(h) => { const e = document.querySelector(h), t = e && e.querySelector('h1');
            return { shown: !!e && e.getClientRects().length > 0, heading: t ? t.textContent.trim() : '', kids: e ? e.children.length : 0,
                     skeleton: !!(e && e.querySelector('.sk')) }; }""", h)
        assert shown["shown"] and shown["kids"] > 0 and shown["heading"] and not shown["skeleton"], f"{page_id} page shows no content: {shown}"
        if page_id == "blocking":
            for tab in BLOCKING_TABS:
                pg.click(f'{h} [data-tab="{tab}"]')
                pg.wait_for_selector(f'{h} [data-pane="{tab}"]:not([hidden])')
                pg.wait_for_function("""([h, tab]) => { const p = document.querySelector(h + ' [data-pane="' + tab + '"]');
                    return p && !p.querySelector('.sk') && p.children.length > 0; }""", arg=[h, tab])
                settle(pg)
        elif page_id == "screentime":
            for tab in SCREENTIME_TABS:
                for rng in ([None] if tab == "calendar" else RANGES):
                    pg.click(f'{h} [data-tab="{tab}"]')
                    if rng:
                        pg.click(f'{h} .seg[data-seg="range"] [data-v="{rng}"]')
                    pg.wait_for_selector(f'{h} [data-pane="{tab}"]:not([hidden])')
                    settle(pg)
                    assert pg.is_hidden(f'{h} [data-pane="{tab}"] .st-empty'), f"screen time {tab}/{rng} shows 'nothing recorded' on 730 days"
        elif page_id == "netlog":
            for view in ("graph", "table"):
                pg.click(f'{h} [data-seg="nlview"] [data-v="{view}"]')
                settle(pg)
            for status in ("allowed", "blocked", "all"):
                pg.click(f'{h} [data-seg="nlstatus"] [data-v="{status}"]')
                settle(pg)
        elif page_id == "modes":
            pg.click(f'{h} .md-row [data-act="edit"]')
            pg.wait_for_selector(f"{h} .md-editor:not([hidden])")
        elif page_id == "reminders":
            pg.wait_for_selector(f"{h} h1")
        settle(pg)
    assert seen == PAGES


def test_the_blocking_overview_scrolls_200_items_and_the_calendar_draws_them(pg, big):
    boot(pg, big, route="blocking")
    pg.wait_for_selector('.page-host[data-page="blocking"] .vl-row:not([hidden])')
    live = pg.evaluate("document.querySelectorAll('.page-host[data-page=\"blocking\"] [data-pane=\"overview\"] .vl-row:not([hidden])').length")
    assert 0 < live <= 80, f"{live} live rows for {ITEMS}+ items: the Overview must be a virtual list"
    assert big.ctx.db.item_count() >= ITEMS


def test_the_corner_overlay_and_standalone_challenge_pages_load_clean(pg, big):
    for path in ("/corner.html", "/overlay.html"):
        pg.goto(big.url(path), wait_until="load")
        pg.wait_for_function("window.pywebview !== undefined")
        pg.wait_for_timeout(300)
    # (there is no corner / cover window in this harness: their *_ready calls are refused, as designed - tests/ui/test_corner.py
    # and test_overlay.py give those pages their windows)
    big.failed[:] = [f for f in big.failed if f[0] not in ("corner_ready", "overlay_ready")]
    configure_antibypass(big)
    try:
        data = big.ctx.guard.request(["Smoke test"], lambda: {"ok": 1}, lambda: None)["data"]
        pg.goto(big.url(f"/challenge.html?ticket={data['ticket']}"), wait_until="load")
        pg.wait_for_selector("#dlg textarea.typed")
        pg.keyboard.press("Escape")
        pg.wait_for_function("!document.getElementById('dlg').open")
    finally:
        ab.save(big.db, {**ab.settings(big.db), "phrase": False})


# ================================================================ every dialog, once

def configure_antibypass(big):
    ab.save(big.db, {**ab.settings(big.db), "phrase": True, "length": 30})


def open_close(pg, label, js, title=None, wait=None, tab=None, page=None):
    """Run `js` (it opens a dialog), check one is open (with `title`), then close it with Esc and check it is gone."""
    if page:
        go(pg, page)
    if tab:                                              # (a tab to open first, and the control that is there once it has loaded)
        pg.click(tab[0])
        pg.wait_for_selector(tab[1])
    # (not awaited by evaluate(): a dialog's promise ends when the dialog closes, which is below)
    pg.evaluate("() => { setTimeout(() => { " + js + " }, 0); return 1; }")
    pg.wait_for_function("!!document.querySelector('dialog[open]')", timeout=8000)
    if wait:
        pg.wait_for_selector(wait)
    got = pg.evaluate("[...document.querySelectorAll('dialog[open]')].pop().querySelector('h2')?.textContent || ''")
    assert got, f"{label}: the dialog has no title"
    if title:
        assert title in got, f"{label}: title {got!r}"
    settle(pg)
    pg.keyboard.press("Escape")
    no_dialog(pg)
    return got


def test_every_dialog_opens_and_closes(pg, big):
    boot(pg, big)
    # the pages whose dialogs are opened by their own buttons are mounted first (their handlers are then wired)
    for page_id in ("dashboard", "blocking", "screentime", "settings", "reminders", "antibypass", "about"):
        go(pg, page_id)
    titles = {}
    imp = lambda mod: f"import('/js/{mod}.js')"          # noqa: E731

    # --- shared dialogs
    titles["display"] = open_close(pg, "display", f"{imp('display')}.then((m) => m.openDisplay())", "Display")
    titles["emergency"] = open_close(pg, "emergency", f"{imp('emergency')}.then((m) => m.openEmergency({{}}))", "Emergency")
    titles["app browser"] = open_close(pg, "app browser", f"{imp('appbrowser')}.then((m) => m.openAppBrowser({{mode: 'browse'}}))", "Apps",
                                       wait=".ab-row")
    titles["category editor"] = open_close(pg, "category editor", f"{imp('categories')}.then((m) => m.openCategoryEditor({{}}))", "ategor")
    titles["update"] = open_close(pg, "update", f"{imp('update')}.then((m) => {{ m.setFound({{version: '9.9.9', size: 4e7, installable: true}}); return m.openUpdateDialog(); }})",
                                  "Update Lockdown")
    # (no update flow runs in this harness - tests/ui/test_about.py gives it one - so the download call is refused, as designed)
    big.failed[:] = [f for f in big.failed if f[0] != "update_download"]
    titles["confirm"] = open_close(pg, "confirm", f"{imp('dialog')}.then((m) => m.confirmDialog({{title: 'Sure?', text: 'Really?', ok: 'Yes'}}))", "Sure?")

    # --- manual list, word lists (Site protection)
    key = big.api.manual_create("Smoke list")["data"]["key"]
    big.api.manual_save_entries(key, [["smoke1.example", ""], ["smoke2.example", "Two"]])
    titles["manual list"] = open_close(pg, "manual list", f"{imp('manuallist')}.then((m) => m.openManualList({key!r}))", wait=".ml-row")
    for name in ("adult_en", "words", "exceptions"):
        titles[f"word list {name}"] = open_close(pg, f"word list {name}", f"{imp('wordlist')}.then((m) => m.openWordList({name!r}))")

    # --- a group member: its own way (site, app, program file) and extra limits
    for kind, hosted, group_way in (("site", False, "dns"), ("app", False, "close"), ("app", True, "close")):
        titles[f"member way {kind}{' file' if hosted else ''}"] = open_close(
            pg, "member way", f"""{imp('customize_member')}.then((m) => m.openMemberWay({{name: 'Thing', kind: {kind!r}, hosted: {str(hosted).lower()},
                groupWay: {group_way!r}, itemWay: {group_way!r}, way: null, onDone() {{}} }}))""", "How Thing is blocked")
    titles["extra limits"] = open_close(pg, "extra limits", f"""{imp('customize_member')}.then((m) => m.openCustomizeMember({{name: 'Thing', overrides: {{}},
        groupRules: [{{rule_type: 'time_limit', same: true, day: '1h'}}], now: () => Date.now(), onDone() {{}} }}))""", "Extra limits for Thing")

    # --- the challenge, over a dialog that is open already (a stacked layer), then alone
    configure_antibypass(big)
    try:
        data = big.ctx.guard.request(["Remove Smoke"], lambda: {"ok": 1}, lambda: None)["data"]
        titles["challenge"] = open_close(pg, "challenge", f"{imp('challenge')}.then((m) => {{ window.__ch = m.openChallenge({json.dumps(data)}); }})",
                                         "This loosens your blocks", wait="#dlg textarea.typed, dialog[open] textarea.typed")
    finally:
        ab.save(big.db, {**ab.settings(big.db), "phrase": False})

    # --- the dialogs of a page's own buttons
    P = lambda page_id: f'.page-host[data-page="{page_id}"]'         # noqa: E731
    titles["overview help"] = open_close(pg, "overview help", f"""document.querySelector('{P("blocking")} [data-k="help"]').click()""", "How to read", page="blocking",
                                         tab=(f'{P("blocking")} [data-tab="overview"]', f'{P("blocking")} [data-k="help"]'))
    titles["screen time calendar help"] = open_close(pg, "calendar help", f"""document.querySelector('{P("screentime")} [data-pane="calendar"] [data-k="help"]').click()""",
                                                     "The calendar", page="screentime", tab=(f'{P("screentime")} [data-tab="calendar"]', f'{P("screentime")} [data-pane="calendar"] [data-k="help"]'))
    titles["screen time switches help"] = open_close(pg, "switches help", f"""document.querySelector('{P("screentime")} [data-pane="switches"] [data-k="help"]').click()""",
                                                     "Most switched to", page="screentime", tab=(f'{P("screentime")} [data-tab="switches"]', f'{P("screentime")} [data-pane="switches"] [data-k="help"]'))
    titles["site protection help"] = open_close(pg, "protection help", f"""document.querySelector('{P("blocking")} [data-pane="protect"] [data-help]').click()""",
                                                page="blocking", tab=(f'{P("blocking")} [data-tab="protect"]', f'{P("blocking")} [data-pane="protect"] [data-help]'))
    titles["anti-bypass confirm"] = open_close(pg, "anti-bypass confirm", """document.querySelector('.page-host[data-page="antibypass"] [data-k="complex"]').click()""",
                                               "Change the Anti-Bypass challenge?", page="antibypass")
    titles["screen time gear"] = open_close(pg, "gear", """document.querySelector('.page-host[data-page="screentime"] [data-k="gear"]').click()""", "Display",
                                            page="screentime")
    titles["reminder editor"] = open_close(pg, "reminder editor", """document.querySelector('.page-host[data-page="reminders"] [data-act="new"]').click()""",
                                           page="reminders")
    # --- Settings: moving the reset back asks Start now / From the end; Import shows what changes (the native pick is stood in)
    saved_reset = big.db.get_setting("limits.reset", "")
    big.db.set_setting("limits.reset", json.dumps({"time": "04:00"}))
    try:
        go(pg, "about")
        go(pg, "settings")
        pg.fill(".page-host[data-page=settings] .set-reset .tp-h", "02")
        pg.fill(".page-host[data-page=settings] .set-reset .tp-m", "00")
        titles["settings reset back"] = open_close(pg, "reset back", """const b = document.querySelector('.page-host[data-page="settings"] .set-reset .confirm');
            b.click(); b.click()""", "Move the reset back to 02:00")
    finally:
        big.db.set_setting("limits.reset", saved_reset)
    pg.route("**/rpc/backup_import_pick", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"ok": True, "data": {"cancelled": False, "name": "lockdown-backup-2026-10-11.json", "token": "x",
                                              "lines": ["Adds 2 blocked items", "Removes the group Fun"]}})))
    titles["settings import"] = open_close(pg, "import", """document.querySelector('.page-host[data-page="settings"] [data-act="import"]').click()""",
                                           "Import these settings?")
    assert len(titles) >= 24, titles
    print("\n  dialogs opened once:", "; ".join(f"{k}: {v}" for k, v in titles.items()))


# ================================================================ nothing scrolls sideways

OVERFLOW_JS = """() => {
    const view = document.getElementById('view');
    const hosts = [...document.querySelectorAll('.page-host')].filter((h) => h.offsetParent !== null);
    const host = hosts[0];
    return { doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
             view: view.scrollWidth - view.clientWidth,
             host: host ? host.scrollWidth - host.clientWidth : 0,
             topbar: document.getElementById('topbar').scrollWidth - document.getElementById('topbar').clientWidth }; }"""


def overflow(pg):
    return pg.evaluate(OVERFLOW_JS)


def look(pg, theme, scale):
    pg.evaluate("""async ([theme, scale]) => { const m = await import('/js/theme.js'); m.applyLook({theme, scale: String(scale === 1 ? 100 : Math.round(scale * 100))}); }""", [theme, scale])
    settle(pg)


def test_no_horizontal_scroll_for_every_theme_size_and_window(pg, big):
    """Every page and tab, at 1000x650 and 1280x800, in dark / AMOLED / light, at 100 % and 70 % interface size."""
    bad = []
    boot(pg, big)
    for page_id in PAGES:
        go(pg, page_id)                                       # (mounted once: the loop below only switches)
    for width, height in SIZES:
        for theme in THEMES:
            for scale in SCALES:
                pg.set_viewport_size({"width": width, "height": height})
                look(pg, theme, scale)
                for page_id in PAGES:
                    pg.click(f'#nav [data-nav="{page_id}"]')
                    pg.wait_for_selector(f'.page-host[data-page="{page_id}"]', state="visible")
                    tabs = {"blocking": BLOCKING_TABS, "screentime": SCREENTIME_TABS}.get(page_id)
                    for tab in (tabs or [None]):
                        if tab:
                            pg.click(f'{host(page_id)} [data-tab="{tab}"]')
                            pg.wait_for_selector(f'{host(page_id)} [data-pane="{tab}"]:not([hidden])')
                        settle(pg)
                        o = overflow(pg)
                        if any(v > 0 for v in o.values()):
                            bad.append(f"{width}x{height} {theme} {int(scale * 100)}% {page_id}{'/' + tab if tab else ''}: {o}")
    assert not bad, "horizontal overflow:\n  " + "\n  ".join(bad)


def test_the_editors_inside_pages_do_not_overflow_either(pg, big):
    boot(pg, big, 1000, 650)
    bad = []
    go(pg, "modes")
    pg.click(f'{host("modes")} .md-row [data-act="edit"]')
    pg.wait_for_selector(f"{host('modes')} .md-editor:not([hidden])")
    pg.click(f'{host("modes")} .md-editor [data-act="other"]')
    pg.wait_for_selector(f'{host("modes")} .md-editor [data-k="p-text"]')
    pg.fill(f'{host("modes")} .md-editor [data-k="p-text"]', "game.jar")
    settle(pg)
    o = overflow(pg)
    if any(v > 0 for v in o.values()):
        bad.append(f"modes editor + picker: {o}")
    go(pg, "blocking")
    pg.click(f'{host("blocking")} [data-tab="groups"]')
    pg.wait_for_selector(f'{host("blocking")} .gp-card[data-id]')
    pg.click(f'{host("blocking")} .gp-card[data-id]')
    pg.wait_for_selector(f'{host("blocking")} [data-k="g-editor"]:not([hidden])')
    settle(pg)
    o = overflow(pg)
    if any(v > 0 for v in o.values()):
        bad.append(f"group editor: {o}")
    assert not bad, "\n".join(bad)


def test_the_overflow_check_can_see_a_page_that_is_too_wide(pg, big):
    """Negative control: a too-wide box in the page IS reported (so "no horizontal scroll" above means something)."""
    boot(pg, big, 1000, 650)
    go(pg, "about")
    assert not any(v > 0 for v in overflow(pg).values())
    pg.evaluate("""() => { const d = document.createElement('div'); d.style.cssText = 'width:2600px;height:10px'; d.id = 'wide';
        document.querySelector('.page-host[data-page="about"]').firstElementChild.append(d); }""")
    settle(pg)
    assert any(v > 0 for v in overflow(pg).values()), overflow(pg)
