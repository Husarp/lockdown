"""R14: Screen Time (js/pages/screentime.js + screentime/overview.js, apps.js) in Chrome against the real Python Api (see
harness.py): the Overview, Apps and Websites tabs under every range, opening on the Display settings' tab and range and then
remembering the last ones, tab switches that don't reload, range changes that update the charts in place, the category chip
and the right-click menu of a row, Browse all apps, Export CSV, the 60 s refresh of the shown tab only, and a long table that
scrolls smoothly.

What can only be proven on Windows: real icons, the native Save dialog, WebView2 (this is Chrome)."""
import re
from datetime import timedelta

import pytest

from harness import NOW
from webapi import screentime as st_api

TABS = ["overview", "apps", "websites"]
RANGES = ["Today", "Yesterday", "7 days", "30 days"]
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'


def open_st(shell, page, **kw):
    shell(route="screentime", **kw)
    wait_loaded(page)
    return page


def wait_loaded(page, timeout=8000):
    """The shown tab has its data for the range that is selected (the pane says which range it shows). The Overview draws its
    lower charts a frame or two after its numbers (data-charts "pending" -> "done"): that is waited for as well."""
    page.wait_for_function("""() => { const p = document.querySelector('.st-pane:not([hidden])');
        const want = document.querySelector('[data-seg="range"] [aria-pressed="true"]')?.dataset.v;
        return !!p && !!want && !document.querySelector('.st-sk') && !p.hasAttribute('aria-busy') &&
          (p.dataset.range === want || p.dataset.pane === 'calendar') &&
          (p.dataset.pane !== 'overview' || p.querySelector('.st-content').dataset.charts !== 'pending') &&
          !!p.querySelector('.st-content:not([hidden]) :is(.card, .vl-row), .st-empty:not([hidden])'); }""", timeout=timeout)


def calls(harness, name="screentime_get"):
    return harness.rpc_count(name)


def tab(page):
    return page.eval_on_selector('.tabs [aria-selected="true"]', "e => e.dataset.tab")


def rng(page):
    return page.eval_on_selector('[data-seg="range"] [aria-pressed="true"]', "e => e.dataset.v")


def go_tab(page, name):
    page.click(f'.tabs [data-tab="{name}"]')
    page.wait_for_function(f"document.querySelector('.tabs [data-tab=\"{name}\"]').getAttribute('aria-selected') === 'true'")
    wait_loaded(page)


def go_range(page, name):
    page.click(f'[data-seg="range"] [data-v="{name}"]')
    page.wait_for_function(f"document.querySelector('[data-seg=\"range\"] [aria-pressed=\"true\"]').dataset.v === '{name}'")
    wait_loaded(page)


def settle(page):
    page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")


def stat(page, key):
    return page.inner_text(f'[data-card="{key}"] .v')


# ---------------------------------------------------------------- where it opens

def test_it_opens_on_overview_and_today_by_default(shell, page, harness):
    open_st(shell, page)
    assert (tab(page), rng(page)) == ("overview", "Today")
    assert page.inner_text("h1") == "Screen Time"
    assert [b.strip() for b in page.eval_on_selector_all(".tabs [role=tab]", "els => els.map(e => e.textContent)")] == \
        ["Overview", "Apps", "Websites", "Switches", "Calendar"]
    assert page.eval_on_selector_all('[data-seg="range"] button', "els => els.map(e => e.textContent)") == RANGES


def test_it_opens_on_the_display_settings_tab_and_range_the_first_time(shell, page, harness):
    harness.db.set_setting("screentime.tab", "Websites")
    harness.db.set_setting("screentime.range", "30 days")
    open_st(shell, page)
    assert (tab(page), rng(page)) == ("websites", "30 days")
    assert page.locator('[data-pane="websites"] .vl-row:not([hidden])').count() > 3
    assert calls(harness) == 1                                                  # the first visit is ONE Api call


def test_then_it_remembers_the_last_tab_and_range_while_the_agent_runs(shell, page, harness):
    harness.db.set_setting("screentime.tab", "Overview")
    harness.db.set_setting("screentime.range", "Today")
    open_st(shell, page)
    go_tab(page, "apps")
    go_range(page, "7 days")
    page.click('#nav [data-nav="about"]')
    page.wait_for_selector('.page-host[data-page="about"]', state="visible")
    harness.db.set_setting("screentime.tab", "Overview")                      # (the default is still what it was)
    page.click('#nav [data-nav="screentime"]')
    page.wait_for_selector('.page-host[data-page="screentime"]', state="visible")
    wait_loaded(page)
    assert (tab(page), rng(page)) == ("apps", "7 days")
    gets = [c[2] for c in harness.calls if c[1] == "screentime_get"]
    assert gets[0] == [None, None] and all(a[0] is not None and a[1] is not None for a in gets[1:])      # asked for the defaults once only


def test_a_link_that_names_a_tab_opens_it(shell, page, harness):
    shell(route="about")
    page.evaluate("async () => { const { router } = await import('/js/router.js'); router.go('screentime', { tab: 'apps' }); }")
    wait_loaded(page)
    assert tab(page) == "apps" and [c[2] for c in harness.calls if c[1] == "screentime_get"][0] == ["apps", None]
    page.evaluate("async () => { const { router } = await import('/js/router.js'); router.go('about'); router.go('screentime', { tab: 'websites' }); }")
    page.wait_for_function("document.querySelector('.tabs [aria-selected=\"true\"]').dataset.tab === 'websites'")


# ---------------------------------------------------------------- every tab under every range

@pytest.mark.parametrize("theme", ["dark", "amoled", "light"])
@pytest.mark.parametrize("name", TABS)
def test_every_tab_and_range_renders_without_errors(shell, page, harness, name, theme):
    open_st(shell, page, width=1280, height=900)
    page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
    go_tab(page, name)
    for r in RANGES:
        go_range(page, r)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        if name == "overview":
            assert re.search(r"\d", stat(page, "active"))
            assert page.locator('[data-card="trend"] svg.ch path.ch-line').count() == 1
            assert page.locator('[data-card="heat"] svg.ch rect.ch-h0, [data-card="heat"] svg.ch rect.ch-h1, [data-card="heat"] svg.ch rect.ch-h2').count() > 100
            assert page.locator('[data-card="trend"] svg.ch line.ch-day, [data-card="trend"] svg.ch line.ch-week').count() == (7 if r == "7 days" else 30)
            assert page.locator('[data-card="bars"]').count() == 0                  # (the "Last 7 days" card is gone: the trend says it)
            assert page.locator('[data-card="donut"] svg.ch circle').count() >= 5
            assert page.locator('[data-card="timeline"]').is_visible() == (r in ("Today", "Yesterday"))
        elif r != "Yesterday" or name == "apps":              # (the synthetic data has few websites yesterday)
            assert page.locator(f'[data-pane="{name}"] .vl-row:not([hidden])').count() >= 1, (name, r)


@pytest.mark.parametrize("name", TABS)
def test_a_narrow_window_has_no_sideways_scroll(shell, page, harness, name):
    harness.db.set_setting("screentime.range", "30 days")
    open_st(shell, page, width=840, height=650)
    go_tab(page, name)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate("""() => { const p = document.querySelector('.st-pane:not([hidden])'); return p.scrollWidth <= p.clientWidth + 1; }""")
    if name != "overview":
        row = page.locator(f'[data-pane="{name}"] .vl-row:not([hidden])').first
        assert row.locator(".name b").is_visible() and row.locator(".st-time").is_visible() and row.locator(".st-chip").is_visible()
        assert not row.locator(".meter").is_visible()                         # (the narrow layout drops the share bar)


def test_the_overview_says_what_stats_says(shell, page, harness):
    open_st(shell, page)
    d = harness.api.screentime_get("overview", "Today")["data"]
    for c in d["cards"]:
        assert page.inner_text(f'[data-card="{c["key"]}"] .v') == c["value"]
        assert page.inner_text(f'[data-card="{c["key"]}"] .d') == c["sub"]
        assert page.inner_text(f'[data-card="{c["key"]}"] .cap').lower() == c["label"].lower()
    assert page.inner_text('[data-card="trend"] h3') == "Trend - last 30 days"
    assert "avg" in page.inner_text('[data-card="trend"] [data-n="note"]')
    assert [t.strip() for t in page.eval_on_selector_all('[data-card="streaks"] .st-streak b', "els => els.map(e => e.textContent)")] == \
        [s["text"] for s in d["streaks"]]
    legend = page.eval_on_selector_all('[data-card="donut"] .legend span', "els => els.map(e => e.textContent)")
    assert len(legend) == len(d["donut"]["parts"]) and legend[0].startswith("Productive")
    assert page.text_content('[data-card="donut"] svg text.ch-big') == d["donut"]["center"]


def test_hovering_a_trend_day_shows_the_four_lines_of_the_api_text(shell, page, harness):
    """Date, screen time, the week so far, and one line per emergency unlock that started that day (the synthetic history has
    one 14:00-14:20 three days ago); the chart adds nothing of its own."""
    harness.db.set_setting("screentime.range", "30 days")
    open_st(shell, page, width=1280, height=900)
    tip = harness.api.screentime_get("overview", "30 days")["data"]["trend"]["points"][26]["tip"]
    box = page.evaluate("(() => { const r = document.querySelectorAll('[data-card=\"trend\"] .ch-hit')[26].getBoundingClientRect(); return [r.x + r.width / 2, r.y + 40]; })()")
    page.mouse.move(*box)
    page.wait_for_selector(".ch-tip:not([hidden])")
    lines = page.inner_text(".ch-tip").split("\n")
    assert "\n".join(lines) == tip and len(lines) == 4
    assert re.fullmatch(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{1,2} [A-Z][a-z]{2}", lines[0]) and lines[1].startswith("Screen time ")
    assert lines[2].startswith("Week average ") and lines[3] == "Emergency unlock 14:00\u201314:20"
    page.mouse.move(*[page.evaluate("(() => { const r = document.querySelectorAll('[data-card=\"trend\"] .ch-hit')[29].getBoundingClientRect(); return r.x + r.width / 2; })()"), box[1]])
    page.wait_for_function("document.querySelector('.ch-tip').textContent.includes('so far')")
    assert page.inner_text(".ch-tip").split("\n")[1].endswith(" so far") and "used" not in page.inner_text(".ch-tip")


def test_the_empty_database_shows_the_tracking_started_card_on_every_tab(shell, page, harness):
    for table in ("activity", "daily_activity", "switch_events"):
        harness.db.conn.execute(f"DELETE FROM {table}")
    harness.db.conn.commit()
    open_st(shell, page)
    for name in TABS:
        go_tab(page, name)
        assert "Tracking started" in page.inner_text(f'[data-pane="{name}"] .st-empty')
        assert page.locator(f'[data-pane="{name}"] .st-empty').is_visible()
        assert not page.locator(f'[data-pane="{name}"] .st-content').is_visible()


# ---------------------------------------------------------------- no re-render: tabs and ranges

def test_a_range_change_updates_the_charts_in_place(shell, page, harness):
    open_st(shell, page)
    page.evaluate("""() => { const q = (s) => document.querySelector(s);
        window.__keep = { trend: q('[data-card="trend"] svg.ch'), heat: q('[data-card="heat"] svg.ch'),
          grid: q('[data-card="trend"] line.ch-grid'), cell: q('[data-card="heat"] rect.ch-h0, [data-card="heat"] rect.ch-h1'),
          line: q('[data-card="trend"] path.ch-line'), donut: q('[data-card="donut"] svg.ch') }; }""")
    before = stat(page, "active")
    go_range(page, "Yesterday")                       # same shapes: not one SVG node is made again
    same = page.evaluate("""() => { const q = (s) => document.querySelector(s), k = window.__keep;
        return k.trend === q('[data-card="trend"] svg.ch') && k.heat === q('[data-card="heat"] svg.ch') &&
          k.grid === q('[data-card="trend"] line.ch-grid') && k.line === q('[data-card="trend"] path.ch-line') && k.donut === q('[data-card="donut"] svg.ch') &&
          k.grid.isConnected && k.cell.isConnected; }""")
    assert same
    assert stat(page, "active") != before                                        # (it did update)
    go_range(page, "7 days")
    go_range(page, "30 days")                         # 30 days: the trend's nodes are made again, the <svg> stays
    assert page.evaluate("window.__keep.trend.isConnected")
    go_range(page, "Today")
    assert page.evaluate("window.__keep.trend === document.querySelector('[data-card=\"trend\"] svg.ch')")


def test_the_overview_paints_the_numbers_and_the_trend_first_and_the_other_charts_a_frame_later(shell, page, harness):
    """One long task used to build all five charts. Now render() writes the four numbers and the trend, and the heatmap,
    donut, timeline and streaks follow right after the frame that painted those (data-charts says "pending" until then)."""
    open_st(shell, page)
    r = page.evaluate("""async () => {
        const mod = await import('/js/pages/screentime/overview.js');
        const a = await window.ld.call('screentime_get', 'overview', '30 days');
        const content = document.querySelector('[data-pane="overview"] .st-content');
        const donut = () => document.querySelector('[data-card="donut"] svg text.ch-big').textContent;
        const frame = () => new Promise((res) => requestAnimationFrame(() => res()));
        await frame(); await frame(); await frame();
        const before = { donut: donut(), active: document.querySelector('[data-card="active"] .v').textContent };
        mod.render(a);
        const now = { state: content.dataset.charts, donut: donut(), active: document.querySelector('[data-card="active"] .v').textContent,
          days: document.querySelectorAll('[data-card="trend"] line.ch-day, [data-card="trend"] line.ch-week').length };
        await frame();
        const one = { state: content.dataset.charts, donut: donut() };
        await frame(); await frame();
        const done = { state: content.dataset.charts, donut: donut() };
        return { before, now, one, done, active: a.cards[0].value, center: a.donut.center }; }""")
    assert r["now"]["state"] == "pending" and r["now"]["active"] == r["active"] and r["now"]["days"] == 30     # the numbers and the trend are there at once
    assert r["now"]["donut"] == r["before"]["donut"] != r["center"]                          # the donut is not
    assert r["one"]["state"] == "pending" and r["one"]["donut"] == r["before"]["donut"]      # (the frame paints what was written)
    assert r["done"] == {"state": "done", "donut": r["center"]}


def test_two_replies_in_a_row_cost_one_second_step(shell, page, harness):
    open_st(shell, page)
    r = page.evaluate("""async () => {
        const mod = await import('/js/pages/screentime/overview.js');
        const a = await window.ld.call('screentime_get', 'overview', '7 days'), b = await window.ld.call('screentime_get', 'overview', '30 days');
        const content = document.querySelector('[data-pane="overview"] .st-content');
        const frame = () => new Promise((res) => requestAnimationFrame(() => res()));
        await frame(); await frame(); await frame();
        let done = 0;
        new MutationObserver(() => { if (content.dataset.charts === 'done') done++; }).observe(content, { attributes: true, attributeFilter: ['data-charts'] });
        mod.render(a); mod.render(b);
        for (let i = 0; i < 6; i++) await frame();
        return { done, days: document.querySelectorAll('[data-card="trend"] line.ch-day, [data-card="trend"] line.ch-week').length,
          donut: document.querySelector('[data-card="donut"] svg text.ch-big').textContent, want: b.donut.center }; }""")
    assert r["done"] == 1 and r["days"] == 30 and r["donut"] == r["want"]                    # (the newest reply is the one drawn)


def test_the_charts_of_a_render_are_laid_out_without_a_forced_layout_per_chart(shell, page, harness):
    """Every chart reads its element's width before it draws; a read after a write lays the page out again. Rendering the
    Overview for another range must cost about the layouts the two frames it spans make by themselves."""
    open_st(shell, page, width=1280, height=900)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Performance.enable")
    count = lambda: {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}["LayoutCount"]
    page.evaluate("""async () => { window.__d = { t: await window.ld.call('screentime_get', 'overview', 'Today'), m: await window.ld.call('screentime_get', 'overview', '30 days') };
        window.__mod = await import('/js/pages/screentime/overview.js');
        window.__frames = async (n) => { for (let i = 0; i < n; i++) await new Promise((res) => requestAnimationFrame(() => res())); }; }""")
    settle = "async (name) => { await window.__frames(4); window.__mod.render(window.__d[name]); await window.__frames(5); }"
    page.evaluate(settle, "t")
    runs = []
    for name in ("m", "t", "m", "t"):
        before = count()
        page.evaluate(settle, name)
        runs.append(count() - before)
    print(f"\nlayouts per Overview render: {runs}")
    assert max(runs) <= 3, runs                 # (two: one per frame that painted something; before this change four)


def test_a_range_change_asks_only_for_the_shown_tab(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "apps")
    n = calls(harness)
    go_range(page, "7 days")
    assert calls(harness) == n + 1
    assert harness.calls[-1][2] == ["apps", "7 days"]
    go_tab(page, "overview")                          # was loaded for another range: asked again once
    assert calls(harness) == n + 2
    assert harness.calls[-1][2] == ["overview", "7 days"]
    go_tab(page, "apps")                              # fresh, same range: nothing
    go_tab(page, "overview")
    assert calls(harness) == n + 2


def test_a_tab_switch_when_fresh_costs_no_call_and_under_50_ms(shell, page, harness, perf):
    open_st(shell, page)
    go_tab(page, "apps")
    go_tab(page, "websites")
    go_tab(page, "overview")
    n = calls(harness)
    times = []
    for name in ("apps", "websites", "overview", "apps", "overview"):
        times.append(perf.click_to_frame(f'.tabs [data-tab="{name}"]'))
    settle(page)
    assert calls(harness) == n
    assert max(times) <= 100, times                    # (target 50; the slack is for a busy machine)
    print("tab switch ms", [round(t, 1) for t in times])
    assert sorted(times)[len(times) // 2] <= 50


def test_the_range_buttons_are_hidden_on_the_calendar(shell, page, harness):
    open_st(shell, page)
    assert page.locator('[data-k="range"]').is_visible()
    page.click('.tabs [data-tab="calendar"]')
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"]') && !document.querySelector('[data-pane=\"calendar\"]').hidden")
    assert not page.locator('[data-k="range"]').is_visible()
    page.click('.tabs [data-tab="apps"]')
    assert page.locator('[data-k="range"]').is_visible()


def test_a_usage_tick_does_not_reload_screen_time(shell, page, harness, push):
    open_st(shell, page)
    n = calls(harness)
    push("usage_tick", {"active_sec": 1234})
    settle(page)
    page.wait_for_timeout(250)
    assert calls(harness) == n


def test_a_dirty_flag_reloads_the_shown_tab_only(shell, page, harness, push):
    open_st(shell, page)
    go_tab(page, "apps")
    n = calls(harness)
    push("dirty", {"page": "screentime"})
    page.wait_for_function("window.__ldCalls !== undefined")
    page.wait_for_timeout(300)
    assert calls(harness) == n + 1
    assert harness.calls[-1][2][0] == "apps"


def test_the_shown_tab_refreshes_every_minute_and_nothing_runs_while_hidden(shell, page, harness, perf):
    open_st(shell, page)
    go_tab(page, "apps")
    n = calls(harness)
    page.evaluate("() => { const real = Date.now.bind(Date); Date.now = () => real() + 61000; }")      # a minute passes
    page.wait_for_timeout(1600)
    assert calls(harness) == n + 1 and harness.calls[-1][2][0] == "apps"
    # hidden: no call, no timer
    perf.set_hidden(True)
    page.wait_for_timeout(300)
    perf.reset_timer_calls()
    n = calls(harness)
    page.evaluate("() => { const real = Date.now.bind(Date); Date.now = () => real() + 61000; }")
    page.wait_for_timeout(2200)
    assert calls(harness) == n and sum(perf.timer_calls().values()) == 0
    perf.set_hidden(False)


# ---------------------------------------------------------------- the tables

def many_apps(harness, n=45):
    """45 apps used yesterday, app i for (i + 1) x 10 minutes: more than any app of the synthetic data."""
    day = NOW.date() - timedelta(days=1)
    for i in range(n):
        for m in range((i + 1) * 10):
            harness.db.add_activity(f"{day} {1 + m // 60:02d}:{m % 60:02d}", f"zz{i:02d}.exe", "", 60, 60)


def test_the_apps_table_is_a_virtual_list_of_the_top_40(shell, page, harness, perf):
    many_apps(harness)
    harness.db.set_setting("screentime.tab", "Apps")
    harness.db.set_setting("screentime.range", "Yesterday")
    page.route("**/icon/**", lambda route: route.fulfill(status=200, content_type="image/svg+xml", body=SVG))
    open_st(shell, page, width=1280, height=800)
    rows = harness.api.screentime_get("apps", "Yesterday")["data"]["rows"]
    assert len(rows) == 40
    vl = '[data-pane="apps"] .st-vl'
    assert page.locator(f"{vl} .vl-row:not([hidden])").count() < 25            # only the rows in view (+ overscan) are nodes
    assert page.eval_on_selector(vl, "e => e.scrollHeight > e.clientHeight")
    first = page.locator(f"{vl} .vl-row:not([hidden])").first
    assert first.inner_text().split("\n")[0].strip() == "1"
    assert rows[0]["key"] == "zz44.exe" and "Zz44" in first.inner_text() and "zz44.exe" in first.inner_text()
    # smooth: scrolling top to bottom, no long frame gap, few live rows
    r = perf.scroll_frames(vl, seconds=1.5, probe=f"{vl} .vl-row:not([hidden])")
    print("scroll", r)
    assert r["max_ms"] <= 100 and r["live_max"] < 30
    assert page.eval_on_selector(vl, "e => e.scrollTop > 0")
    settle(page)
    texts = page.eval_on_selector_all(f"{vl} .vl-row:not([hidden])", "els => els.map(e => e.innerText)")
    assert any(t.startswith("40\n") and rows[39]["key"] in t for t in texts), texts[-2:]       # the 40th row is there


def test_a_row_has_the_name_the_exe_the_share_the_time_the_count_and_the_category(shell, page, harness):
    harness.db.set_setting("screentime.tab", "Apps")
    harness.db.set_setting("screentime.range", "30 days")
    open_st(shell, page)
    d = harness.api.screentime_get("apps", "30 days")["data"]["rows"]
    row = page.locator('[data-pane="apps"] .st-vl .vl-row:not([hidden])').first
    r = d[0]
    assert row.locator(".name b").inner_text() == r["name"] and row.locator(".name span").inner_text() == r["sub"]
    assert row.locator(".st-time").inner_text() == r["time"] and row.locator(".sw").inner_text() == str(r["count"])
    assert row.locator(".st-chip").inner_text() == f"{r['cat_name']} ▾"
    assert row.locator(".meter i").evaluate("e => e.style.width") == "100%"
    assert row.locator("img").get_attribute("src").endswith(f"/icon/app/{r['key']}.png")


def test_websites_have_no_exe_line_and_no_browse_button(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "apps")
    go_tab(page, "websites")
    row = page.locator('[data-pane="websites"] .st-vl .vl-row:not([hidden])').first
    assert row.locator(".name span").inner_text() == ""
    assert "/icon/site/" in row.locator("img").get_attribute("src")
    assert page.locator('[data-pane="websites"] [data-k="browse"]').count() == 0
    assert page.locator('[data-pane="apps"] [data-k="browse"]').count() == 1
    assert "visits" in page.inner_text('[data-pane="websites"] .list-h').lower()
    assert "switches" in page.inner_text('[data-pane="apps"] .list-h').lower()


def test_the_empty_table_says_so_and_the_footer_hint_is_there(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "apps")
    go_range(page, "Today")
    harness.db.conn.execute("DELETE FROM activity WHERE minute >= ?", (f"{NOW.date()} 00:00",))
    harness.db.conn.commit()
    page.evaluate("async () => { const { router } = await import('/js/router.js'); router.reload('screentime'); }")
    page.wait_for_function("!document.querySelector('[data-pane=\"apps\"] [data-k=\"none\"]').hidden")
    assert "Nothing recorded for this period yet." in page.inner_text('[data-pane="apps"] [data-k="none"]')
    assert "Right-click a row to set its category" in page.inner_text('[data-pane="apps"] .st-foot')


# ---------------------------------------------------------------- category chip, right-click, Browse all apps

def test_the_category_chip_opens_the_category_menu_and_picking_one_changes_the_row(shell, page, harness):
    harness.db.set_setting("screentime.tab", "Apps")
    harness.db.set_setting("screentime.range", "30 days")
    open_st(shell, page)
    row = page.locator('[data-pane="apps"] .st-vl .vl-row:not([hidden])').first
    key = harness.api.screentime_get("apps", "30 days")["data"]["rows"][0]["key"]
    row.locator(".st-chip").click()
    page.wait_for_selector("#menu:not([hidden])")
    labels = page.eval_on_selector_all("#menu button", "els => els.map(e => e.textContent.trim())")
    assert labels[:4] == ["Productive", "Neutral", "Games", "Distracting"] and "New category…" in labels and "Edit categories…" in labels
    assert "‹ Back" in labels
    page.click('#menu button:has-text("Productive")')
    page.wait_for_function("(k) => document.querySelector('[data-pane=\"apps\"] .vl-row .name span').textContent === k && "
                           "document.querySelector('[data-pane=\"apps\"] .vl-row .st-chip').textContent.startsWith('Productive')", arg=key)
    assert harness.db.categories()[("app", key)] == "productive"
    assert page.inner_text('[data-pane="apps"] .vl-row .st-chip') == "Productive ▾"


def test_right_click_anywhere_on_a_row_opens_the_app_menu(shell, page, harness):
    harness.db.set_setting("screentime.tab", "Apps")
    harness.db.set_setting("screentime.range", "30 days")
    open_st(shell, page)
    row = page.locator('[data-pane="apps"] .st-vl .vl-row:not([hidden])').nth(1)
    for part in (".name b", ".st-time", ".meter", ".rank"):
        row.locator(part).click(button="right")
        page.wait_for_selector("#menu:not([hidden])")
        labels = page.eval_on_selector_all("#menu button", "els => els.map(e => e.textContent.trim())")
        assert any(x.startswith("Category:") for x in labels) and "Block it…" in labels
        page.keyboard.press("Escape")
        page.wait_for_selector("#menu", state="hidden")


def test_block_it_from_the_menu_goes_to_blocking_add_with_the_app_filled_in(shell, page, harness):
    harness.db.set_setting("screentime.tab", "Apps")
    harness.db.set_setting("screentime.range", "30 days")
    open_st(shell, page)
    page.evaluate("""async () => { const { router } = await import('/js/router.js'); window.__go = []; const real = router.go.bind(router);
        router.go = (id, opts) => { window.__go.push([id, opts]); return true; }; }""")
    key = harness.api.screentime_get("apps", "30 days")["data"]["rows"][0]["key"]
    page.locator('[data-pane="apps"] .st-vl .vl-row:not([hidden])').first.click(button="right")
    page.wait_for_selector("#menu:not([hidden])")
    page.click('#menu button:has-text("Block it")')
    page.wait_for_function("window.__go.length > 0")
    go = page.evaluate("window.__go[0]")
    assert go[0] == "blocking" and go[1]["tab"] == "add" and go[1]["prefill"]["kind"] == "app" and go[1]["prefill"]["name"] == key


def test_browse_all_apps_opens_the_app_browser_in_browse_mode(shell, page, harness):
    from resident import catalog as app_catalog
    cat = app_catalog.AppCatalog(harness.db_path.parent / "cat.json", running_now=lambda: {},
                                 list_apps=lambda: [{"name": f"App {i}", "exe": f"app{i}.exe", "path": f"C:\\a{i}.exe", "steam": False} for i in range(30)])
    cat.load_now()
    harness.ctx.catalog = cat
    page.route("**/icon/**", lambda route: route.fulfill(status=200, content_type="image/svg+xml", body=SVG))
    harness.db.set_setting("screentime.tab", "Apps")
    open_st(shell, page)
    page.click('[data-pane="apps"] [data-k="browse"]')
    page.wait_for_selector("dialog[open] .ab-browse .ab-row")
    assert "Right-click an app to set its category" in page.inner_text("dialog[open] .ab")
    page.keyboard.press("Escape")


# ---------------------------------------------------------------- gear, Display settings, Export CSV

def test_the_gear_opens_the_display_settings_and_what_it_picks_is_followed(shell, page, harness):
    open_st(shell, page)
    page.click('[data-k="gear"]')
    page.wait_for_selector("dialog[open] [data-seg='tab']")
    assert "screen time opens with" in page.inner_text("dialog[open]").lower()
    page.click("dialog[open] [data-seg='tab'] [data-v='Websites']")
    page.wait_for_function("document.querySelector('.tabs [aria-selected=\"true\"]').dataset.tab === 'websites'")
    page.click("dialog[open] [data-seg='range'] [data-v='30 days']")
    page.wait_for_function("document.querySelector('[data-seg=\"range\"] [aria-pressed=\"true\"]').dataset.v === '30 days'")
    assert harness.db.get_setting("screentime.tab") == "Websites" and harness.db.get_setting("screentime.range") == "30 days"
    page.click("dialog[open] button:has-text('Done')")


def test_export_csv_asks_where_and_says_how_many_rows(shell, page, harness, tmp_path, monkeypatch):
    out = tmp_path / "shot.csv"
    monkeypatch.setattr(st_api, "_ask_save_path", lambda window, name: str(out))
    open_st(shell, page)
    page.click('[data-k="csv"]')
    page.wait_for_selector("#toast:not([hidden])")
    assert re.fullmatch(r"\d+ rows saved to .*shot\.csv", page.inner_text("#toast span"))
    assert out.read_text(encoding="utf-8").startswith("date,app,site,minutes,active minutes,category")
    monkeypatch.setattr(st_api, "_ask_save_path", lambda window, name: None)
    page.evaluate("() => { const t = document.getElementById('toast'); t.hidden = true; t.textContent = '';"
                  " window.__csv = null; const a = window.pywebview.api, real = a.screentime_export_csv;"
                  " a.screentime_export_csv = async (...x) => (window.__csv = await real.apply(a, x)); }")
    page.click('[data-k="csv"]')
    page.wait_for_function("() => window.__csv !== null")
    page.wait_for_function("() => !document.querySelector('[data-k=\"csv\"]').disabled")
    assert page.evaluate("window.__csv")["data"].get("cancelled") is True              # the second save was cancelled...
    assert page.evaluate("document.getElementById('toast').hidden") is True     # ...and no new toast came up
    assert "rows saved" not in page.inner_text("#toast")


# ---------------------------------------------------------------- speed and hygiene

def test_the_first_visit_is_quick(shell, page, harness):
    shell(route="about")
    page.wait_for_selector('.page-host[data-page="about"]', state="visible")
    ms = page.evaluate("""async () => { const t0 = performance.now(); document.querySelector('#nav [data-nav="screentime"]').click();
        while (!document.querySelector('[data-pane="overview"] [data-card="active"] .v')?.textContent) {
          await new Promise((r) => requestAnimationFrame(() => r())); if (performance.now() - t0 > 5000) return -1; }
        return performance.now() - t0; }""")
    print("first visit ms", round(ms))
    assert 0 < ms <= 600


def test_a_category_changed_elsewhere_refreshes_the_shown_tab(shell, page, harness):
    harness.db.set_setting("screentime.tab", "Apps")
    open_st(shell, page)
    n = calls(harness)
    page.evaluate("window.dispatchEvent(new CustomEvent('ld:categories-changed'))")
    page.wait_for_timeout(300)
    assert calls(harness) == n + 1
    go_tab(page, "overview")                         # the hidden one was marked: it asks when shown
    assert calls(harness) == n + 2


def test_a_failed_first_load_of_a_tab_shows_the_error_card_and_retry_loads_it(shell, page, harness):
    open_st(shell, page)
    page.evaluate("""() => { window.__real = window.pywebview.api.screentime_get; window.pywebview.api.screentime_get = async () => ({ ok: false, error: 'boom' }); }""")
    page.click('.tabs [data-tab="apps"]')
    page.wait_for_selector(".st-sk .err-card", timeout=8000)
    assert "boom" in page.inner_text(".st-sk .err-card")
    page.evaluate("() => { window.pywebview.api.screentime_get = window.__real; }")
    page.click(".st-sk .err-card button")
    wait_loaded(page)
    assert page.locator('[data-pane="apps"] .vl-row:not([hidden])').count() > 3
    assert not page.locator(".st-sk").count()
