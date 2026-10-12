"""R05: the shell, headless in Chrome against the real Python Api (see harness.py).

Covers: first paint and the ten pages, theme / accent / size applied live, no horizontal scroll, the router (lazy
pages, dirty flags, skeleton, errors, show_page), the top line / banners / save bar / clock, dialog and menu
components, the virtual list, and the performance numbers (printed; budgets asserted).
"""
import time
from datetime import timedelta

import pytest

import antibypass
import modes
import pause
from harness import assert_clean, stub_all_pages

NAV_IDS = ["dashboard", "blocking", "antibypass", "screentime", "netlog", "modes", "reminders", "notifications",
           "settings", "about"]
NAV_TITLES = ["Dashboard", "Blocking", "Anti-Bypass", "Screen Time", "Network Log", "Modes", "Reminders",
              "Notifications", "Settings", "About"]


def say(capsys, text):
    with capsys.disabled():
        print("\n  " + text)


def theme_of(page):
    return page.evaluate("document.documentElement.dataset.theme")


def var(page, name):
    return page.evaluate("(n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim()", name)


def body_bg(page):
    return page.evaluate("getComputedStyle(document.body).backgroundColor")


def set_look(page, **look):
    return page.evaluate("async (look) => (await import('/js/shell.js')).setLook(look)", look)


def register(page, page_id, module="probe-page.js", fresh=0):
    """Register a test page module for `page_id` - the same one-line registration a page ticket makes."""
    page.evaluate("""async ([id, module, fresh]) => {
        window.__probeFresh = fresh;
        const m = await import('/js/pages/index.js');
        m.pages[id] = () => import('/__t/' + module + '?id=' + id);
    }""", [page_id, module, fresh])


def loaded(page, page_id, n):
    """Wait until the probe page for `page_id` has finished its n-th load (data in, not just started)."""
    page.wait_for_function(f"window.__probe && window.__probe['{page_id}'] && window.__probe['{page_id}'].done === {n}", timeout=8000)


def probe(page, page_id):
    return page.evaluate("(id) => JSON.parse(JSON.stringify(window.__probe[id]))", page_id)


def go(page, page_id):
    page.click(f'#nav [data-nav="{page_id}"]')


def visible_host(page):
    return page.evaluate("[...document.querySelectorAll('.page-host')].filter(h => !h.hidden).map(h => h.dataset.page)")


# ================================================================ first paint and the ten pages

def test_shell_paints_with_no_errors(shell, page, harness):
    stub_all_pages(page)                     # the boot call count is the shell's own, not the dashboard page's
    shell()
    assert_clean(page)
    ids = page.eval_on_selector_all("#nav [data-nav]", "els => els.map(e => e.dataset.nav)")
    assert ids == NAV_IDS
    titles = page.eval_on_selector_all("#nav [data-nav] span", "els => els.map(e => e.textContent)")
    assert titles == NAV_TITLES
    # the separator sits right before Settings
    assert page.evaluate("document.querySelector('#nav [data-nav=settings]').previousElementSibling.className") == "sep"
    assert page.inner_text("#tb-prot") == "Protection on"
    assert page.inner_text("#svc-text") == "Service running"
    assert page.inner_text("#tb-clock") == "Sun 11 Oct · 15:42"
    assert page.get_attribute("#topbar", "data-ready") == "1"
    assert page.query_selector("#boot-skeleton") is None
    assert visible_host(page) == ["dashboard"]
    # boot makes exactly these rpc calls (page_ready: the agent may show the window now)
    names = sorted(n for _, n, _ in harness.calls)
    assert {"get_status", "get_theme", "ui_ready", "page_ready"} <= set(names) and len(names) == len(set(names))
    assert set(names) <= {"get_status", "get_theme", "ui_ready", "page_ready", "update_opened", "update_state"}   # (update_* are the shell's own)
    assert [a for _, n, a in harness.calls if n == "page_ready"] == [["main"]]
    assert page.evaluate("typeof window.ld.call") == "function"


def test_skeleton_is_in_the_html_before_any_script(browser, harness):
    """The first paint needs no JS: with scripts off the static shell (nav, top line, skeleton) is already there."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, java_script_enabled=False)
    pg = ctx.new_page()
    pg.goto(harness.url("/index.html"))
    assert pg.eval_on_selector_all("#nav [data-nav]", "els => els.length") == 10
    assert pg.is_visible("#boot-skeleton")
    assert pg.is_visible("#nav")
    ctx.close()


@pytest.mark.parametrize("page_id", NAV_IDS)
def test_each_nav_item_switches_page(shell, page, page_id):
    shell()
    go(page, page_id)
    page.wait_for_selector(f'.page-host[data-page="{page_id}"]', state="visible")
    assert visible_host(page) == [page_id]
    current = page.eval_on_selector_all("#nav [data-nav]", "els => els.filter(e => e.getAttribute('aria-current') === 'page').map(e => e.dataset.nav)")
    assert current == [page_id]
    assert page.evaluate("location.hash") == "#" + page_id


@pytest.mark.parametrize("page_id, title", list(zip(NAV_IDS, NAV_TITLES)))
def test_an_unregistered_page_shows_the_titled_stub(shell, page, page_id, title):
    stub_all_pages(page)
    shell()
    go(page, page_id)
    page.wait_for_selector(f'.page-host[data-page="{page_id}"] h1', state="visible")
    assert page.inner_text(f'.page-host[data-page="{page_id}"] h1') == title
    assert "Coming soon" in page.inner_text(f'.page-host[data-page="{page_id}"]')


def test_pages_stay_mounted_and_switching_is_hide_show(shell, page):
    shell()
    go(page, "modes")
    page.wait_for_selector('.page-host[data-page="modes"]')
    page.evaluate("document.querySelector('.page-host[data-page=modes]').__mine = 42")
    go(page, "about")
    page.wait_for_selector('.page-host[data-page="about"]')
    assert page.evaluate("document.querySelector('.page-host[data-page=modes]').hidden") is True
    go(page, "modes")
    assert page.evaluate("document.querySelector('.page-host[data-page=modes]').__mine") == 42
    assert page.evaluate("document.querySelectorAll('.page-host').length") == 3          # dashboard, modes, about


def test_hash_route_opens_that_page(shell, page):
    shell(route="reminders")
    page.wait_for_selector('.page-host[data-page="reminders"]')
    assert visible_host(page) == ["reminders"]
    page.evaluate("location.hash = '#about'")
    page.wait_for_selector('.page-host[data-page="about"]', state="visible")
    assert visible_host(page) == ["about"]


def test_show_page_push_navigates(shell, page, push):
    shell()
    push("show_page", {"page": "screentime"})
    page.wait_for_selector('.page-host[data-page="screentime"]', state="visible")
    assert visible_host(page) == ["screentime"]
    push("show_page", {"page": "no-such-page"})
    push("show_page", "about")
    page.wait_for_selector('.page-host[data-page="about"]', state="visible")
    assert visible_host(page) == ["about"]


def test_leaving_a_page_never_blocks_with_a_confirm(shell, page, push):
    shell()
    page.once("dialog", lambda d: pytest.fail("a blocking dialog opened: " + d.message))
    push("draft", {"dirty": True, "autosave": False, "change_summary": "1 change"})
    go(page, "blocking")
    go(page, "about")
    page.wait_for_selector('.page-host[data-page="about"]', state="visible")
    assert page.inner_text("#sb-text").startswith("Unsaved changes")           # the draft stays pending


# ================================================================ look: theme, accent, size - live

def test_theme_dark_amoled_light_apply_live(shell, page, harness):
    shell()
    page.evaluate("window.__marker = 'same-page'")
    seen = {}
    for theme in ("amoled", "light", "dark"):
        set_look(page, theme=theme)
        assert theme_of(page) == theme
        seen[theme] = body_bg(page)
        assert harness.db.get_setting("ui.theme") == theme
    assert seen["amoled"] == "rgb(0, 0, 0)"
    assert len(set(seen.values())) == 3
    assert page.evaluate("window.__marker") == "same-page"                        # no reload
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1
    # the main surface follows too, not only the body
    set_look(page, theme="amoled")
    assert page.evaluate("getComputedStyle(document.querySelector('.main')).backgroundColor") == "rgb(0, 0, 0)"


def test_system_theme_follows_the_os_live(shell, page):
    shell()
    page.emulate_media(color_scheme="dark")
    set_look(page, theme="system")
    assert theme_of(page) == "dark"
    page.emulate_media(color_scheme="light")
    page.wait_for_function("document.documentElement.dataset.theme === 'light'")
    page.emulate_media(color_scheme="dark")
    page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
    set_look(page, theme="amoled")                       # a fixed theme no longer follows the OS
    page.emulate_media(color_scheme="light")
    page.wait_for_timeout(200)
    assert theme_of(page) == "amoled"


def test_accent_applies_live_and_derives_the_other_colours(shell, page, harness):
    shell()
    assert var(page, "--brand") == "#DB5126"                                # Python's default (Orange)
    page.evaluate("window.__marker = 1")
    before_soft = var(page, "--brand-soft")
    set_look(page, accent="#2f6feb")
    assert var(page, "--brand") == "#2F6FEB"
    assert var(page, "--on-brand") == "#FFFFFF"
    assert var(page, "--brand-soft") != before_soft
    assert harness.db.get_setting("ui.accent") == "#2F6FEB"
    # the pages really use it: the active nav icon and the Auto-save switch
    assert page.evaluate("getComputedStyle(document.querySelector('#nav [aria-current=page] .ic')).color") == "rgb(47, 111, 235)"
    page.wait_for_function("getComputedStyle(document.querySelector('#sb-auto + span')).backgroundColor === 'rgb(47, 111, 235)'")   # (120 ms colour transition)
    # a pale accent gets dark text on it
    set_look(page, accent="#C99A0E")
    assert var(page, "--on-brand") == "#1A1918"
    soft_dark = var(page, "--brand-soft")
    set_look(page, theme="light")
    assert var(page, "--brand-soft") != soft_dark                           # derived per theme
    assert page.evaluate("window.__marker") == 1
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1


def test_a_refused_accent_puts_the_old_one_back(shell, page, harness):
    shell()
    with pytest.raises(Exception):
        set_look(page, accent="javascript:alert(1)")
    assert var(page, "--brand") == "#DB5126"
    assert harness.db.get_setting("ui.accent") is None


def test_size_70_percent_changes_the_scale_live(shell, page, harness):
    shell()
    assert var(page, "--s") == "1"
    h100 = page.evaluate("document.querySelector('#nav [data-nav=blocking]').getBoundingClientRect().height")
    set_look(page, scale="70")
    assert var(page, "--s") == "0.7"
    h70 = page.evaluate("document.querySelector('#nav [data-nav=blocking]').getBoundingClientRect().height")
    assert abs(h70 - h100 * 0.7) < 0.6
    assert harness.db.get_setting("ui.scale") == "70"
    for value, s in (("90", "0.9"), ("80", "0.8"), ("100", "1"), ("auto", "1")):
        set_look(page, scale=value)
        assert var(page, "--s") == s
    assert page.evaluate("performance.getEntriesByType('navigation').length") == 1


def test_the_look_comes_back_after_a_restart(shell, page, harness):
    harness.db.set_setting("ui.theme", "amoled")
    harness.db.set_setting("ui.accent", "#12948A")
    harness.db.set_setting("ui.scale", "80")
    shell()
    assert theme_of(page) == "amoled" and var(page, "--brand") == "#12948A" and var(page, "--s") == "0.8"
    page.reload()                                       # (the cosmetic cache paints it before the first frame)
    assert page.evaluate("document.documentElement.dataset.theme") == "amoled"
    assert page.evaluate("document.documentElement.style.getPropertyValue('--brand')") == "#12948A"


# ================================================================ layout

@pytest.mark.parametrize("width, height", [(1000, 650), (1280, 800)])
def test_no_horizontal_scroll_on_any_page(shell, page, width, height):
    shell(width, height)
    for page_id in NAV_IDS:
        go(page, page_id)
        page.wait_for_selector(f'.page-host[data-page="{page_id}"]', state="visible")
        over = page.evaluate("""() => ['html', '#view', '.main', '#topbar', '.app'].map((s) => {
            const e = document.querySelector(s); return [s, e.scrollWidth, e.clientWidth]; }).filter(([, sw, cw]) => sw > cw)""")
        assert not over, f"{page_id} at {width}x{height}: {over}"
    if width == 1000:                                        # the sidebar is an icon rail here: the service dot stays
        assert page.is_visible("#svc-dot") and not page.is_visible("#svc-text")
    # and with the biggest interface size the top line still fits
    set_look(page, scale="100")
    assert page.evaluate("document.querySelector('#topbar').scrollWidth <= document.querySelector('#topbar').clientWidth")


# ================================================================ top line, sidebar, banners, save bar

def test_top_line_shows_the_active_mode(shell, page, harness):
    now = harness.now
    modes.start(harness.db, "work", now - timedelta(minutes=5), now + timedelta(hours=1), locked=True)
    shell()
    assert page.inner_text("#tb-mode .n") == "Work"
    assert page.inner_text("#tb-mode .u") == "until 16:42"
    modes.start(harness.db, "relax", now - timedelta(minutes=5), None)
    page.evaluate("window.ld.call('get_status').then((s) => window.ld.push([['status', s]]))")
    page.wait_for_function("document.querySelector('#tb-mode .n').textContent === 'Relax'")
    assert page.inner_text("#tb-mode .u") == "until you stop"


def settled(page, harness):
    """The number of Api calls once the boot (the shell's and the Dashboard page's first load) has stopped calling."""
    last = -1
    while len(harness.calls) != last:
        last = len(harness.calls)
        page.wait_for_timeout(300)
    return last


def test_status_pushes_patch_in_place_without_rpc(shell, page, harness, push):
    stub_all_pages(page)                     # (this tests the shell: the Dashboard page asks again when the status changes)
    shell()
    n = settled(page, harness)
    node = page.evaluate_handle("document.querySelector('#tb-mode .n')")
    push("status", {"mode": {"id": "study", "name": "Study", "phase": None, "ends_at": "2026-10-11T17:00:00",
                             "locked": False, "by_schedule": False}})
    assert page.inner_text("#tb-mode .n") == "Study"
    assert page.inner_text("#tb-mode .u") == "until 17:00"
    assert page.evaluate("(n) => n === document.querySelector('#tb-mode .n')", node)          # same node, text updated
    push("status", {"mode": None})
    assert page.inner_text("#tb-mode .n") == "No mode"
    assert len(harness.calls) == n                                                    # a push costs no rpc


def snapshot_public(harness):
    """What the StatusWorker would push: resident/snapshot.py's page part for the harness database now."""
    import time
    from datetime import datetime
    from resident import snapshot
    from trusted_time import OFFSET_KEY
    # (the harness has a fixed "now": say so the way the service would - trusted time minus this PC's clock)
    harness.db.set_setting(OFFSET_KEY, str((harness.now - datetime.now()).total_seconds()))
    return snapshot.build(harness.db, harness.now, wall=time.time()).public()


def test_status_pushes_from_the_real_snapshot_render_in_the_same_places(shell, page, harness, push):
    """get_status and the status pushes speak one vocabulary (running, off, mode.phase ...): a push renders in the same places."""
    now = harness.now
    stub_all_pages(page)                     # (this tests the shell: the Dashboard page asks again when the status changes)
    shell()
    n = settled(page, harness)
    # a mode with a Pomodoro phase, Lockdown paused
    modes.start(harness.db, "focus", now - timedelta(minutes=3), None)
    pause.start(harness.db, now, 30, silent=False)
    push("status", snapshot_public(harness))
    assert page.inner_text("#tb-mode .n") == "Focus"
    assert page.inner_text("#tb-mode .u").startswith("\u00b7 focus until ")
    assert page.inner_text("#tb-paused-t") == "Blocks paused \u00b7 30 min left"
    assert page.is_visible("#tb-resume")
    # Lockdown switched off, the service down
    pause.resume(harness.db)
    antibypass.switch_off(harness.db, now)
    harness.set_service(False)
    push("status", snapshot_public(harness))
    assert page.is_visible('[data-banner="off"]') and page.is_hidden('[data-banner="service"]')       # (while off, only "off" is said)
    assert "since 15:42" in page.inner_text('[data-banner="off"]')
    assert page.inner_text("#svc-text") == "Service not running"
    assert page.is_visible("#tb-prot-off")
    # switched back on while the service is still stopped: now the service banner shows
    antibypass.switch_on(harness.db)
    push("status", {"off": None})
    assert page.is_hidden('[data-banner="off"]') and page.is_visible('[data-banner="service"]')
    # everything back to normal: a delta with only what changed
    harness.set_service(True)
    push("status", {"off": None, "running": True})
    assert page.is_hidden('[data-banner="off"]') and page.is_hidden('[data-banner="service"]')
    assert page.inner_text("#svc-text") == "Service running"
    assert len(harness.calls) == n


def test_clock_offset_in_a_status_push_sets_the_trusted_clock(shell, page, push):
    shell()
    push("status", {"clock_offset": 3600.0})                     # trusted time = this PC's clock + 1 h
    expected = page.evaluate("""() => { const d = new Date(Date.now() + 3600e3 - new Date().getTimezoneOffset() * 60000);
        const p = (n) => String(n).padStart(2, '0'); return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()); }""")
    shown = page.inner_text("#tb-clock")
    assert shown.endswith(expected) or shown.endswith(page.evaluate("""() => { const d = new Date(Date.now() + 3600e3 - new Date().getTimezoneOffset() * 60000 - 1500);
        const p = (n) => String(n).padStart(2, '0'); return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()); }"""))


def test_a_later_status_push_does_not_wind_the_clock_back(shell, page, push):
    shell()
    push("status", {"clock_now": "2026-10-11T18:30:00"})
    assert page.inner_text("#tb-clock").endswith("18:30")
    push("status", {"mode": None})                               # an unrelated push must leave the clock alone
    push("status", {"pause": None})
    assert page.inner_text("#tb-clock").endswith("18:30")


def test_service_stopped_banner_and_start_button(shell, page, harness, monkeypatch):
    import webapi.shell as shell_api
    started = []
    monkeypatch.setattr(shell_api, "_run_elevated", lambda program, params: started.append(program))
    harness.set_service(False)
    shell()
    assert page.is_visible('[data-banner="service"]')
    assert "Blocking isn't enforced" in page.inner_text('[data-banner="service"]')
    assert page.inner_text("#svc-text") == "Service not running"
    assert page.evaluate("document.querySelector('#svc-dot').classList.contains('pulse')") is False
    page.click('[data-banner="service"] button')
    page.wait_for_selector("#toast:not([hidden])")
    assert started == ["cmd.exe"]
    assert "Starting the service" in page.inner_text("#toast")
    harness.set_service(True)
    page.evaluate("window.ld.call('get_status').then((s) => window.ld.push([['status', s]]))")
    page.wait_for_function("document.querySelector('[data-banner=service]').hidden")
    assert page.inner_text("#svc-text") == "Service running"
    assert page.evaluate("document.querySelector('#svc-dot').classList.contains('pulse')") is True


def test_start_service_says_pythons_words_when_it_is_not_starting(shell, page, harness, monkeypatch):
    import webapi.shell as shell_api
    started = []
    monkeypatch.setattr(shell_api, "_run_elevated", lambda program, params: started.append(program))
    monkeypatch.setitem(shell_api._service_start, "last", time.monotonic())            # "just requested"
    harness.set_service(False)
    shell()
    page.click('[data-banner="service"] button')
    page.wait_for_selector("#toast:not([hidden])")
    assert page.inner_text("#toast") == "Starting was just requested. Give it a moment."
    assert started == []                                                              # (nothing was started again)


def test_off_banner_and_turn_back_on(shell, page, harness):
    antibypass.save(harness.db, {**antibypass.settings(harness.db), "phrase": True, "length": 250})   # challenge on, not passed
    antibypass.switch_off(harness.db, harness.now)
    shell()
    assert page.is_visible('[data-banner="off"]')
    assert "Lockdown is switched off" in page.inner_text('[data-banner="off"]')
    assert "since 15:42" in page.inner_text('[data-banner="off"]')
    assert page.is_visible("#tb-prot-off") and page.is_visible("#tb-prot-on")
    assert not page.is_visible("#tb-pause")                                  # no pausing what is off
    page.click('[data-banner="off"] button')
    page.wait_for_function("document.querySelector('[data-banner=off]').hidden")
    assert not antibypass.is_off(harness.db)                                 # (free: never needs the challenge)
    assert page.is_visible("#tb-prot") and not page.is_visible("#tb-prot-on")
    assert page.is_visible("#tb-pause")


def test_protection_off_turn_on_button_in_the_top_line(shell, page, harness):
    antibypass.switch_off(harness.db, harness.now)
    shell()
    page.click("#tb-prot-on")
    page.wait_for_function("!document.querySelector('#tb-prot').hidden")
    assert not antibypass.is_off(harness.db)


def test_pause_shows_time_left_and_resume_works(shell, page, harness):
    pause.start(harness.db, harness.now, 30, silent=False)
    shell()
    assert page.is_visible("#tb-paused") and page.is_visible("#tb-resume")
    assert page.inner_text("#tb-paused-t") == "Blocks paused · 30 min left"
    assert not page.is_visible("#tb-pause")
    page.click("#tb-resume")
    page.wait_for_function("document.querySelector('#tb-paused').hidden")
    assert pause.state(harness.db, harness.now) is None
    assert page.is_visible("#tb-pause") and not page.is_visible("#tb-resume")


def test_pause_my_blocks_opens_the_anti_bypass_page(shell, page):
    shell()
    page.click("#tb-pause")
    page.wait_for_selector('.page-host[data-page="antibypass"]', state="visible")
    assert visible_host(page) == ["antibypass"]


def test_trusted_clock_ticks_locally(shell, page, harness, push):
    shell()
    n = settled(page, harness)
    push("status", {"clock_now": "2026-10-11T15:42:57"})
    assert page.inner_text("#tb-clock").endswith("15:42")
    page.wait_for_function("document.querySelector('#tb-clock').textContent.endsWith('15:43')", timeout=6000)
    assert len(harness.calls) == n                                                    # it ticked on its own, no rpc


def test_pulse_dot_is_css_only_and_paused_while_hidden(shell, page, perf):
    shell()
    anim = page.evaluate("getComputedStyle(document.querySelector('#svc-dot'), '::after').animationName")
    assert anim == "pulse"
    perf.set_hidden(True)
    assert page.evaluate("document.documentElement.hasAttribute('data-hidden')") is True
    assert page.evaluate("getComputedStyle(document.querySelector('#svc-dot'), '::after').animationPlayState") == "paused"
    perf.set_hidden(False)
    assert page.evaluate("getComputedStyle(document.querySelector('#svc-dot'), '::after').animationPlayState") == "running"


def test_save_bar_follows_the_draft(shell, page, harness, push):
    shell()
    # auto-save on and nothing pending: just the switch
    assert page.is_checked("#sb-auto") and not page.is_visible("#sb-save") and not page.is_visible("#sb-discard")
    push("draft", {"dirty": True, "autosave": False, "change_summary": "Added YouTube · 2 rules"})
    assert not page.is_checked("#sb-auto")
    assert page.is_visible("#sb-discard") and page.is_enabled("#sb-save")
    assert page.inner_text("#sb-save") == "Save changes"
    assert page.inner_text("#sb-text") == "Unsaved changes · Added YouTube · 2 rules"
    push("draft", {"dirty": False})
    assert not page.is_visible("#sb-discard")
    assert page.inner_text("#sb-save") == "Saved" and page.is_disabled("#sb-save")
    assert page.inner_text("#sb-text") == ""
    # a status carries the draft too
    push("status", {"draft": {"dirty": True, "autosave": True, "change_summary": "1 change pending"}})
    assert page.is_visible("#sb-discard") and page.is_visible("#sb-save")


def test_save_bar_buttons_call_the_draft_api(shell, page, push):
    """R18 provides draft_save / draft_discard / draft_set_autosave; here stand-ins record what the bar calls."""
    shell()
    page.evaluate("""() => { window.__draft = [];
        for (const n of ['draft_save', 'draft_discard', 'draft_set_autosave'])
          window.pywebview.api[n] = (...a) => { window.__draft.push([n, a]); return Promise.resolve({ok: true, data: {}}); }; }""")
    push("draft", {"dirty": True, "autosave": False, "change_summary": ""})
    page.click("#sb-save")
    page.click("#sb-discard")
    page.click("#sb-auto")
    page.wait_for_function("window.__draft.length === 3")
    assert page.evaluate("window.__draft") == [["draft_save", []], ["draft_discard", []], ["draft_set_autosave", [True]]]


def test_save_bar_switch_goes_back_when_the_api_refuses(shell, page, push):
    shell()
    page.evaluate("""() => { window.pywebview.api.draft_set_autosave = () => Promise.resolve({ok: false, error: 'Not now.'}); }""")
    assert page.is_checked("#sb-auto")
    page.click("#sb-auto")                                    # tries to turn it off ...
    page.wait_for_selector("#toast:not([hidden])")
    assert "Not now." in page.inner_text("#toast")
    assert page.is_checked("#sb-auto")                        # ... and it is back on


def test_a_needs_challenge_reply_is_announced(shell, page, harness, monkeypatch):
    """draft_save may answer {needs_challenge, ticket}: the shell hands it to whoever shows the challenge."""
    shell()
    page.evaluate("""() => { window.pywebview.api.draft_save = () => Promise.resolve(
        {ok: true, data: {needs_challenge: true, ticket: 'T1', changes: ['x'], more: 0}});
        window.__challenges = []; window.addEventListener('ld:needs-challenge', (e) => window.__challenges.push(e.detail)); }""")
    page.evaluate("window.ld.push([['draft', {dirty: true, autosave: false, change_summary: ''}]])")
    page.click("#sb-save")
    page.wait_for_function("window.__challenges.length === 1")
    assert page.evaluate("window.__challenges[0].ticket") == "T1"


# ================================================================ the router

def test_dirty_flag_on_a_hidden_page_causes_no_fetch_until_shown(shell, page, harness, push):
    shell()
    register(page, "blocking", fresh=0)
    go(page, "blocking")
    loaded(page, "blocking", 1)
    assert probe(page, "blocking")["mounts"] == 1
    go(page, "dashboard")
    page.wait_for_selector('.page-host[data-page="dashboard"]', state="visible")
    before = harness.rpc_count()
    push("dirty", {"page": "blocking"})
    page.wait_for_timeout(500)
    assert harness.rpc_count() == before                          # hidden page: nothing fetched
    assert probe(page, "blocking")["loads"] == 1
    go(page, "blocking")                                          # shown: stale and older than freshMs (0) -> one reload
    loaded(page, "blocking", 2)
    assert harness.rpc_count() == before + 1
    assert probe(page, "blocking")["mounts"] == 1                 # same mount, not rebuilt


def test_a_stale_page_younger_than_freshms_is_not_reloaded_on_show(shell, page, harness, push):
    shell()
    register(page, "blocking", fresh=60_000)
    go(page, "blocking")
    loaded(page, "blocking", 1)
    go(page, "about")
    push("dirty", {"page": "blocking"})
    go(page, "blocking")
    page.wait_for_timeout(400)
    assert probe(page, "blocking")["loads"] == 1
    assert probe(page, "blocking")["shows"] == [None, None]


def test_dirty_flag_on_the_visible_page_reloads_it_and_coalesces(shell, page, harness, push):
    shell()
    register(page, "blocking")
    go(page, "blocking")
    loaded(page, "blocking", 1)
    harness.delays["get_status"] = 0.4
    push("dirty", {"page": "blocking"})                           # starts load 2 (slow)
    page.wait_for_timeout(100)
    for _ in range(3):
        push("dirty", {"page": "blocking"})                       # three more while it is running
    loaded(page, "blocking", 3)
    page.wait_for_timeout(1000)
    assert probe(page, "blocking")["loads"] == 3                  # one follow-up load, not three


def test_a_dirty_flag_that_arrives_during_the_first_load_is_not_lost(shell, page, harness, push):
    shell()
    register(page, "blocking")
    harness.delays["get_status"] = 0.6
    go(page, "blocking")
    page.wait_for_function("window.__probe && window.__probe.blocking && window.__probe.blocking.loads === 1")
    push("dirty", {"page": "blocking"})                           # the first load's data may already be old
    loaded(page, "blocking", 2)                                   # so it loads once more
    page.wait_for_timeout(900)
    assert probe(page, "blocking")["loads"] == 2


def test_dirty_for_a_page_never_visited_does_nothing(shell, page, harness, push):
    shell()
    register(page, "blocking")
    n = settled(page, harness)
    push("dirty", {"page": "blocking"})
    push("dirty", {"page": "nonsense"})
    push("dirty", None)
    page.wait_for_timeout(300)
    assert harness.rpc_count() == n
    go(page, "blocking")                                          # first visit loads fresh data once
    loaded(page, "blocking", 1)


def test_page_events_patch_the_visible_page_and_mark_a_hidden_one_stale(shell, page, push):
    shell()
    register(page, "blocking", fresh=0)
    go(page, "blocking")
    loaded(page, "blocking", 1)
    push("usage_tick", {"seconds": 5})
    assert probe(page, "blocking")["patches"] == [{"event": "usage_tick", "payload": {"seconds": 5}}]
    go(page, "about")
    push("usage_tick", {"seconds": 9})
    assert len(probe(page, "blocking")["patches"]) == 1            # hidden: no patch ...
    go(page, "blocking")                                           # ... but it reloads on show (stale, freshMs 0)
    loaded(page, "blocking", 2)


def test_show_and_hide_are_called_and_show_gets_the_push_options(shell, page, push):
    shell()
    register(page, "blocking")
    push("show_page", {"page": "blocking", "tab": "calendar"})
    page.wait_for_function("window.__probe && window.__probe.blocking && window.__probe.blocking.shows.length === 1")
    assert probe(page, "blocking")["shows"] == [{"page": "blocking", "tab": "calendar"}]
    go(page, "about")
    assert probe(page, "blocking")["hides"] == 1


def test_skeleton_only_shows_when_load_takes_longer_than_300ms(shell, page, harness):
    shell()
    register(page, "blocking")
    harness.delays["get_status"] = 0.9
    go(page, "blocking")
    page.wait_for_timeout(150)
    assert page.query_selector(".page-skeleton") is None          # fast enough: no skeleton yet
    page.wait_for_selector(".page-skeleton", timeout=1000)
    loaded(page, "blocking", 1)
    page.wait_for_function("!document.querySelector('.page-skeleton')")
    assert page.inner_text('.page-host[data-page="blocking"] .n') == "1"
    # a fast page never shows one
    harness.delays.clear()
    register(page, "modes")
    page.evaluate("window.__sk = 0; new MutationObserver(() => { if (document.querySelector('.page-skeleton')) window.__sk++; }).observe(document.body, {childList: true, subtree: true})")
    go(page, "modes")
    loaded(page, "modes", 1)
    page.wait_for_timeout(500)
    assert page.evaluate("window.__sk") == 0


def test_a_page_that_fails_to_load_shows_an_error_card_with_retry(shell, page, harness):
    shell()
    register(page, "blocking", module="broken-page.js")
    go(page, "blocking")
    page.wait_for_selector(".err-card")
    assert "the data could not be read" in page.inner_text(".err-card")
    deadline = time.time() + 3
    while time.time() < deadline and "first visit" not in (harness.log_path.read_text("utf-8") if harness.log_path.exists() else ""):
        time.sleep(0.05)
    assert "page error: " in harness.log_path.read_text("utf-8")      # it went to the Lockdown log
    # the rest of the shell still works
    go(page, "about")
    page.wait_for_selector('.page-host[data-page="about"]', state="visible")
    go(page, "blocking")
    assert page.is_visible(".err-card")
    page.click(".err-card button")                                   # retry (fails again, same card)
    page.wait_for_function("window.__broken.loads === 2")
    page.wait_for_selector(".err-card")
    assert page.evaluate("window.__broken") == {"mounts": 1, "loads": 2}     # a retry loads again, it doesn't mount again
    assert page.evaluate("document.querySelectorAll('.err-card').length") == 1
    assert_clean(page)


@pytest.mark.allow_console_errors
def test_javascript_errors_in_the_page_are_logged_not_swallowed(shell, page, harness):
    shell()
    page.evaluate("setTimeout(() => { throw new Error('boom from a test') }, 0)")
    deadline = time.time() + 3
    text = ""
    while time.time() < deadline and "boom from a test" not in text:
        time.sleep(0.05)
        text = harness.log_path.read_text("utf-8") if harness.log_path.exists() else ""
    assert "boom from a test" in text
    page.wait_for_function("true")
    for _ in range(40):                                              # the browser console saw it too, on purpose
        if any('boom from a test' in e for e in page.errors):
            break
        time.sleep(0.05)
    assert any('boom from a test' in e for e in page.errors)


def test_navigating_during_a_slow_first_load_mounts_once(shell, page, harness):
    shell()
    register(page, "blocking")
    harness.delays["get_status"] = 0.5
    go(page, "blocking")
    go(page, "about")
    go(page, "blocking")
    go(page, "dashboard")
    go(page, "blocking")
    loaded(page, "blocking", 1)
    page.wait_for_timeout(300)
    assert probe(page, "blocking")["mounts"] == 1 and probe(page, "blocking")["loads"] == 1
    assert visible_host(page) == ["blocking"]
    assert page.evaluate("document.querySelectorAll('.page-host[data-page=blocking]').length") == 1


def test_page_every_ticks_only_while_the_page_is_visible(shell, page):
    shell()
    register(page, "blocking")
    go(page, "blocking")
    loaded(page, "blocking", 1)
    page.wait_for_function("window.__probe.blocking.ticks >= 2", timeout=5000)
    go(page, "about")
    page.wait_for_timeout(200)
    n = probe(page, "blocking")["ticks"]
    page.wait_for_timeout(2300)
    assert probe(page, "blocking")["ticks"] == n                  # left: its tick is off
    go(page, "blocking")
    page.wait_for_function(f"window.__probe.blocking.ticks > {n}", timeout=5000)


def test_a_draft_push_survives_a_later_status_push(shell, page, push):
    shell()
    push("draft", {"dirty": True, "autosave": False, "change_summary": "3 changes"})
    push("status", {"mode": None})
    assert page.is_visible("#sb-discard") and page.inner_text("#sb-text") == "Unsaved changes \u00b7 3 changes"


# ================================================================ idle: a hidden window does nothing

@pytest.mark.parametrize("how", ["visibility", "python-push"])
def test_hidden_window_runs_no_timers_and_makes_no_rpc(shell, page, harness, perf, push, how, capsys):
    shell()
    register(page, "blocking")
    go(page, "blocking")                                          # a page that might want to tick
    loaded(page, "blocking", 1)
    page.wait_for_timeout(1500)                                   # the clock is ticking while visible
    ticking = perf.timer_calls()
    assert ticking["timeout"] > 0
    if how == "visibility":
        perf.set_hidden(True)
    else:
        push("visible", {"visible": False})
    page.wait_for_timeout(300)
    perf.reset_timer_calls()
    calls_before = len(harness.calls)
    hidden_ms = 5000 if how == "visibility" else 2000             # (the Python-push variant needs less proof)
    page.wait_for_timeout(hidden_ms)
    after = perf.timer_calls()
    say(capsys, f"hidden for {hidden_ms // 1000} s ({how}): timer callbacks {after}, rpc calls {len(harness.calls) - calls_before}")
    assert after == {"timeout": 0, "interval": 0, "raf": 0}
    assert len(harness.calls) == calls_before
    # shown again: the clock runs again and the shell asks for one fresh status
    if how == "visibility":
        perf.set_hidden(False)
    else:
        push("visible", {"visible": True})
    page.wait_for_function("window.__ldCalls.timeout > 0", timeout=3000)
    if how == "visibility":
        page.wait_for_function("true")
        deadline = time.time() + 2
        while time.time() < deadline and harness.rpc_count("get_status", 0) < 3:
            time.sleep(0.05)
        assert harness.rpc_count("get_status") >= 3                # boot, the probe's load, and the one after showing


def test_a_window_that_loads_hidden_boots_quietly(browser, harness):
    """The agent loads the main window hidden: it still gets its data, but nothing ticks and nothing animates."""
    import harness as H
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script(harness.init_script())
    ctx.add_init_script("window.__ldHidden = true")
    pg = ctx.new_page()
    H.open_shell(pg, harness)
    assert pg.evaluate("document.documentElement.hasAttribute('data-hidden')") is True
    assert pg.inner_text("#svc-text") == "Service running"                       # it did get its data
    pg.wait_for_timeout(300)
    pg.evaluate("() => { const c = window.__ldCalls; c.timeout = c.interval = c.raf = 0; }")
    pg.wait_for_timeout(2500)
    assert pg.evaluate("({...window.__ldCalls})") == {"timeout": 0, "interval": 0, "raf": 0}
    pg.evaluate("() => { window.__ldHidden = false; document.dispatchEvent(new Event('visibilitychange')); }")
    pg.wait_for_function("!document.documentElement.hasAttribute('data-hidden')")
    pg.wait_for_function("window.__ldCalls.timeout > 0", timeout=3000)           # the clock runs again
    ctx.close()


# ================================================================ components

def test_dialog_focus_trap_esc_and_once(shell, page):
    shell()
    page.evaluate("""async () => {
        const d = await import('/js/dialog.js');
        window.D = d; window.__closed = [];
        document.querySelector('[data-nav=blocking]').focus();
        window.H1 = d.openDialog({key: 'k1', title: 'First', body: '<p>Hello</p><input id="in1">', onClose: (r) => window.__closed.push(r),
          buttons: [{label: 'Cancel', value: 'cancel'}, {label: 'Save', kind: 'primary', value: 'save'}]});
        window.H2 = d.openDialog({key: 'k1', title: 'Again'});             // once: same key, nothing new
    }""")
    assert page.evaluate("window.H1 === window.H2")
    assert page.evaluate("document.getElementById('dlg').open")
    assert page.inner_text("#dlg h2") == "First"
    assert page.evaluate("document.activeElement.textContent") == "Save"          # the primary button has the focus
    inside = []
    for _ in range(8):                                                            # Tab never leaves the dialog
        page.keyboard.press("Tab")
        inside.append(page.evaluate("document.getElementById('dlg').contains(document.activeElement)"))
    assert all(inside)
    for _ in range(8):
        page.keyboard.press("Shift+Tab")
        inside.append(page.evaluate("document.getElementById('dlg').contains(document.activeElement)"))
    assert all(inside)
    page.keyboard.press("Escape")
    assert not page.evaluate("document.getElementById('dlg').open")
    page.wait_for_function("window.__closed.length === 1")                       # (the close event comes a tick later)
    assert page.evaluate("window.__closed") == [None]
    assert page.evaluate("document.activeElement.dataset.nav") == "blocking"          # focus went back to the opener


def test_dialog_buttons_close_with_their_value_and_confirm_resolves(shell, page):
    shell()
    value = page.evaluate("""async () => {
        const d = await import('/js/dialog.js');
        const h = d.openDialog({title: 'T', buttons: [{label: 'Yes', kind: 'primary', value: 'yes'}]});
        setTimeout(() => document.querySelector('#dlg .dlg-f button').click(), 50);
        return await h.done; }""")
    assert value == "yes"
    ok = page.evaluate("""async () => {
        const d = await import('/js/dialog.js');
        const p = d.confirmDialog({title: 'Remove?', text: 'Really <b>remove</b> it?', ok: 'Remove', danger: true});
        setTimeout(() => document.querySelector('#dlg [data-primary]').click(), 50);
        return await p; }""")
    assert ok is True
    assert page.evaluate("document.querySelector('#dlg').innerHTML") == ""
    no = page.evaluate("""async () => {
        const d = await import('/js/dialog.js');
        const p = d.confirmDialog({title: 'Remove?', text: 'x'});
        setTimeout(() => document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})), 10);
        setTimeout(() => d.closeDialog(), 60);
        return await p; }""")
    assert no is False


def test_an_undismissable_dialog_ignores_esc_and_cannot_be_replaced(shell, page):
    shell()
    page.evaluate("""async () => {
        const d = await import('/js/dialog.js');
        window.D = d;
        d.openDialog({key: 'challenge', title: 'Challenge', dismissable: false, body: '<input id="typed">',
                      buttons: [{label: 'OK', kind: 'primary', value: 1}]});
    }""")
    assert page.query_selector("#dlg [data-close]") is None
    page.keyboard.press("Escape")
    assert page.evaluate("document.getElementById('dlg').open")
    assert page.evaluate("window.D.openDialog({title: 'Other'})") is None
    assert page.inner_text("#dlg h2") == "Challenge"
    page.evaluate("window.D.closeDialog('done')")
    assert not page.evaluate("document.getElementById('dlg').open")


def test_a_second_dialog_replaces_the_first_in_the_same_element(shell, page):
    shell()
    r = page.evaluate("""async () => {
        const d = await import('/js/dialog.js'); const out = [];
        d.openDialog({key: 'a', title: 'A', onClose: (v) => out.push(['A', v])});
        const b = d.openDialog({key: 'b', title: 'B', onClose: (v) => out.push(['B', v])});
        out.push(document.querySelectorAll('dialog').length, document.querySelector('#dlg h2').textContent);
        d.closeDialog('x'); await b.done; return out; }""")
    assert r == [["A", "replaced"], 1, "B", ["B", "x"]]


def test_menu_keyboard_click_away_and_esc(shell, page):
    shell()
    page.evaluate("""async () => {
        const m = await import('/js/menu.js'); window.M = m; window.__picked = [];
        const btn = document.querySelector('#tb-pause'); btn.focus();
        m.openMenu(btn, [{heading: 'Edit'}, {label: 'Rename', icon: 'pencil', onClick: () => window.__picked.push('rename')},
          {label: 'Disabled', disabled: true}, {sep: true}, {label: 'Remove', danger: true, icon: 'trash', onClick: () => window.__picked.push('remove')}]);
    }""")
    assert page.is_visible("#menu")
    assert page.evaluate("document.activeElement.textContent") == "Rename"        # first enabled item has the focus
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.textContent") == "Remove"        # skips the disabled one
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.textContent") == "Rename"        # wraps
    page.keyboard.press("End")
    assert page.evaluate("document.activeElement.textContent") == "Remove"
    page.keyboard.press("Enter")
    assert page.evaluate("window.__picked") == ["remove"]
    assert not page.is_visible("#menu")
    # Esc closes and gives the focus back
    page.evaluate("() => { const b = document.querySelector('#tb-pause'); b.focus(); window.M.openMenu(b, [{label: 'One', onClick() {}}]); }")
    page.keyboard.press("Escape")
    assert not page.is_visible("#menu")
    assert page.evaluate("document.activeElement.id") == "tb-pause"
    # click away closes without choosing anything
    page.evaluate("() => window.M.openMenu({x: 400, y: 300}, [{label: 'One', onClick: () => window.__picked.push('one')}])")
    assert page.is_visible("#menu")
    page.mouse.click(900, 600)
    assert not page.is_visible("#menu")
    assert page.evaluate("window.__picked") == ["remove"]
    # a click on an item runs it
    page.evaluate("() => window.M.openMenu({x: 400, y: 300}, [{label: 'Pick me', checked: true, onClick: () => window.__picked.push('clicked')}])")
    page.click("#menu button")
    assert page.evaluate("window.__picked") == ["remove", "clicked"]


def test_menu_stays_inside_the_window_and_contextmenu_helper(shell, page):
    shell()
    box = page.evaluate("""async () => {
        const m = await import('/js/menu.js');
        m.openMenu({x: 1275, y: 795}, [{label: 'A long enough label for a menu', onClick() {}}, {label: 'B', onClick() {}}]);
        const r = document.getElementById('menu').getBoundingClientRect();
        return [r.right, r.bottom, innerWidth, innerHeight]; }""")
    assert box[0] <= box[2] and box[1] <= box[3]
    page.keyboard.press("Escape")
    page.evaluate("""async () => {
        const m = await import('/js/menu.js'); window.__ctx = [];
        m.contextMenu(document.getElementById('nav'), '[data-nav]', (t) => [{label: 'Open ' + t.dataset.nav, onClick: () => window.__ctx.push(t.dataset.nav)}]);
    }""")
    page.click('#nav [data-nav="netlog"]', button="right")
    assert page.inner_text("#menu button") == "Open netlog"
    page.click("#menu button")
    assert page.evaluate("window.__ctx") == ["netlog"]


def test_confirm_button_needs_two_clicks_and_drains_back(shell, page):
    shell()
    page.evaluate("""async () => {
        const ui = await import('/js/ui.js'); window.__n = 0;
        const b = ui.confirmButton({label: 'Remove', confirmLabel: 'Sure?', ms: 400, onConfirm: () => window.__n++});
        b.id = 'cb'; document.querySelector('.topbar').append(b); }""")
    page.click("#cb")
    assert page.inner_text("#cb") == "Sure?" and page.evaluate("window.__n") == 0
    assert page.evaluate("getComputedStyle(document.querySelector('#cb'), '::after').animationName") == "drain"
    page.wait_for_function("document.querySelector('#cb').textContent === 'Remove'", timeout=3000)   # drained: disarmed
    page.click("#cb")
    page.click("#cb")
    assert page.evaluate("window.__n") == 1 and page.inner_text("#cb") == "Remove"


def test_controls_seg_stepper_days_and_time_picker(shell, page):
    shell()
    page.evaluate("""async () => {
        const ui = await import('/js/ui.js'); window.__ev = [];
        const box = document.createElement('div'); box.id = 'ctl';
        box.innerHTML = ui.seg('mode', [['a', 'A'], ['b', 'B']], 'a') + ui.stepper(5, 'min', {min: 1, max: 7}) + ui.days([1, 0, 0, 0, 0, 0, 0]) + ui.tp('09:30', 'From');
        document.body.append(box);
        for (const t of ['seg', 'input', 'press']) box.addEventListener(t, (e) => window.__ev.push([t, e.detail || e.target.value])); }""")
    page.click('#ctl [data-v="b"]')
    assert page.evaluate("[...document.querySelectorAll('#ctl .seg button')].map(b => b.getAttribute('aria-pressed'))") == ["false", "true"]
    for _ in range(4):
        page.click('#ctl [data-act="step"][data-d="1"]')
    assert page.input_value("#ctl .stepper input") == "7"                          # clamped at max
    page.click('#ctl .days button:nth-child(2)')
    assert page.get_attribute('#ctl .days button:nth-child(2)', "aria-pressed") == "true"
    page.focus("#ctl .tp-m")
    page.keyboard.press("ArrowUp")
    assert page.input_value("#ctl .tp-m") == "35"
    page.focus("#ctl .tp-h")
    for _ in range(10):
        page.keyboard.press("ArrowDown")
    assert page.input_value("#ctl .tp-h") == "23"
    events = page.evaluate("window.__ev")
    assert ["seg", {"name": "mode", "value": "b"}] in events
    assert any(e[0] == "press" for e in events)


def test_toast_shows_one_message_at_a_time(shell, page):
    shell()
    page.evaluate("async () => { const ui = await import('/js/ui.js'); ui.toast('First'); ui.toast('Second', ['Undo', () => { window.__undo = 1; }]); }")
    assert page.inner_text("#toast span") == "Second"
    page.click("#toast .btn-text")
    assert page.evaluate("window.__undo") == 1
    assert page.is_hidden("#toast")


def test_html_is_escaped_in_the_builders(shell, page):
    shell()
    out = page.evaluate("""async () => { const ui = await import('/js/ui.js');
        return [ui.esc('<img src=x onerror=alert(1)>"\\''), ui.tag('block', '<b>x</b>'), ui.status({s: 'block', until: '<i>'}), ui.fmt(65), ui.fmt(5)]; }""")
    assert "<" not in out[0] and "&lt;img" in out[0] and "&quot;" in out[0] and "&#39;" in out[0]
    assert "<b>x</b>" not in out[1] and "&lt;b&gt;" in out[1]
    assert "Blocked (until &lt;i&gt;)" in out[2]
    assert out[3] == "1 h 05 m" and out[4] == "5 min"


# ================================================================ the virtual list

def open_vlist(page, harness):
    page.goto(harness.url("/__t/vlist.html"))
    page.wait_for_function("window.__ready === true")


def test_vlist_keeps_few_live_rows_and_reuses_nodes(page, harness):
    open_vlist(page, harness)
    assert page.evaluate("vl.liveCount") <= 40
    assert page.evaluate("document.querySelectorAll('#list > .vl-row:not([hidden])').length") == page.evaluate("vl.liveCount")
    assert page.evaluate("document.getElementById('list').scrollHeight") == 10000 * page.evaluate("vl.rowHeight")
    # a row still in view after a small scroll keeps its node and is not rendered again
    page.evaluate("""() => { const row = [...document.querySelectorAll('#list > .vl-row')].find((n) => n.querySelector('b').textContent === 'Row 8');
        row.dataset.mark = 'mine'; window.__r0 = window.__renders(); }""")
    page.evaluate("document.getElementById('list').scrollTop = 3 * vl.rowHeight")
    page.wait_for_function("document.querySelector('#list > .vl-row:not([hidden]) b') && true")
    page.wait_for_timeout(100)
    assert page.evaluate("[...document.querySelectorAll('#list > .vl-row')].filter((n) => n.dataset.mark === 'mine' && n.querySelector('b').textContent === 'Row 8').length") == 1
    assert page.evaluate("window.__renders() - window.__r0") <= 4                  # only the few new rows were drawn
    # jump to the end: the same few nodes now show the last rows
    page.evaluate("document.getElementById('list').scrollTop = 1e9")
    page.wait_for_function("[...document.querySelectorAll('#list > .vl-row:not([hidden]) b')].some(b => b.textContent === 'Row 9999')")
    assert page.evaluate("vl.liveCount") <= 40
    assert page.evaluate("document.querySelectorAll('#list > .vl-row').length") <= 60    # nodes made in total


def test_vlist_set_rows_and_scale_change(page, harness):
    open_vlist(page, harness)
    page.evaluate("vl.setRows(vl.rows.slice(0, 5))")
    assert page.evaluate("vl.liveCount") == 5
    assert page.evaluate("document.getElementById('list').scrollHeight") <= 600
    page.evaluate("vl.setRows([])")
    assert page.evaluate("vl.liveCount") == 0
    page.evaluate("document.documentElement.style.setProperty('--s', '0.7'); window.dispatchEvent(new Event('ld:scale'))")
    assert page.evaluate("vl.rowHeight") < 36


def test_vlist_scroll_performance(page, harness, perf, capsys):
    open_vlist(page, harness)
    r = perf.scroll_frames("#list", seconds=3, steps_per_sec=60, probe="#list > .vl-row:not([hidden])")
    say(capsys, f"vlist 10 000 rows, 3 s scroll: {r['frames']} frames, max gap {r['max_ms']:.1f} ms, "
                f"p95 {r['p95_ms']:.1f} ms, mean {r['mean_ms']:.1f} ms, live rows max {r['live_max']}")
    assert r["frames"] >= 60
    assert r["max_ms"] <= 50
    assert r["live_max"] <= 80
    assert page.evaluate("[...document.querySelectorAll('#list > .vl-row:not([hidden]) b')].some(b => b.textContent === 'Row 9999')")


# ================================================================ performance numbers

def test_first_contentful_paint_of_the_shell(page, harness, capsys):
    page.goto(harness.url("/index.html"), wait_until="load")
    page.wait_for_function("document.documentElement.dataset.ready === '1'")
    runs = []
    for _ in range(3):
        page.goto("about:blank")
        page.goto(harness.url("/index.html"), wait_until="load")
        page.wait_for_function("performance.getEntriesByName('first-contentful-paint').length > 0")
        runs.append(page.evaluate("performance.getEntriesByName('first-contentful-paint')[0].startTime"))
    ready = page.evaluate("performance.now()")
    say(capsys, f"first contentful paint: {', '.join(f'{r:.0f}' for r in runs)} ms (budget 300); booted at ~{ready:.0f} ms")
    assert min(runs) > 0 and max(runs) <= 300


def test_page_switch_and_first_visit_timings(shell, page, perf, capsys):
    stub_all_pages(page)                     # the budget is for the router's first visit of a light page
    shell()
    first = {}
    for page_id in NAV_IDS[1:]:
        first[page_id] = perf.click_until(f'#nav [data-nav="{page_id}"]', f'.page-host[data-page="{page_id}"] h1')
    switch = {}
    for page_id in NAV_IDS[1:] + ["dashboard"]:
        switch[page_id] = perf.click_to_frame(f'#nav [data-nav="{page_id}"]')
    say(capsys, f"first visit of a stub page: worst {max(first.values()):.1f} ms, median {sorted(first.values())[len(first) // 2]:.1f} ms (budget 150)")
    say(capsys, f"switch to a mounted page, click -> next frame: worst {max(switch.values()):.1f} ms, "
                f"median {sorted(switch.values())[len(switch) // 2]:.1f} ms (budget 50)")
    assert all(v >= 0 for v in first.values()) and max(first.values()) <= 150
    assert max(switch.values()) <= 50


def test_idle_shell_makes_no_rpc_and_one_timer_per_second(shell, page, harness, perf, capsys):
    shell()
    page.wait_for_timeout(1200)
    n, t0 = len(harness.calls), perf.timer_calls()
    page.wait_for_timeout(3000)
    t1 = perf.timer_calls()
    say(capsys, f"idle, window shown, 3 s: timeouts {t1['timeout'] - t0['timeout']}, rAF {t1['raf'] - t0['raf']}, "
                f"intervals {t1['interval'] - t0['interval']}, rpc {len(harness.calls) - n}")
    assert len(harness.calls) == n                                  # no polling at all: the clock ticks locally
    assert t1["raf"] == t0["raf"] and t1["interval"] == t0["interval"]
    assert t1["timeout"] - t0["timeout"] <= 4                       # the one-second clock tick


# ================================================================ the harness itself and the product's files

def test_unknown_rpc_names_are_refused(shell, page):
    shell()
    reply = page.evaluate("fetch('/rpc/__class__', {method: 'POST', body: '[]'}).then(r => r.json())")
    assert reply["ok"] is False
    reply = page.evaluate("fetch('/rpc/not_a_method', {method: 'POST', body: '[]'}).then(r => r.json())")
    assert reply["ok"] is False


def test_production_ui_files_have_no_test_shim():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "src" / "ui"
    bad = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.suffix in (".html", ".js", ".css")
           and ("/rpc/" in p.read_text("utf-8") or "localStorage.setItem(\"ld.state" in p.read_text("utf-8"))]
    assert bad == []


def test_the_asset_server_serves_the_shell_with_javascript_mime_types(harness):
    import urllib.request
    for path, kind in (("/index.html", "text/html"), ("/js/shell.js", "text/javascript"), ("/js/pages/index.js", "text/javascript"),
                       ("/css/app.css", "text/css")):
        with urllib.request.urlopen(harness.url(path)) as r:
            assert r.headers["Content-Type"].startswith(kind), path
    with pytest.raises(Exception):
        urllib.request.urlopen(harness.url("/js/../../src/db.py"))
