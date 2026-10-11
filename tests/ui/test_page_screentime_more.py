"""R15: Screen Time's Switches and Calendar tabs (js/pages/screentime/switches.js, calendar.js) in Chrome against the real Python
Api (see harness.py): the three numbers, the hours chart (busiest hour in the accent, the next two darker), the five most
switched-to; the month (‹ › with › off at the current month, future days muted, the dot over the goal, the levels), a click on a
day shows its details WITHOUT reading the month again, the empty states, no sideways scroll, and no console errors in light /
dark / AMOLED. Charts update in place; tab switches when fresh cost no call.

What can only be proven on Windows: real icons, WebView2 (this is Chrome)."""
import re

import pytest

from harness import NOW

RANGES = ["Today", "Yesterday", "7 days", "30 days"]


def open_st(shell, page, **kw):
    shell(route="screentime", **kw)
    page.wait_for_function("() => !!document.querySelector('.st-pane:not([hidden]) .st-content :is(.card, .vl-row)')", timeout=8000)
    return page


def count(harness, name):
    return harness.rpc_count(name)


def go_tab(page, name):
    page.click(f'.tabs [data-tab="{name}"]')
    page.wait_for_function(f"document.querySelector('.tabs [data-tab=\"{name}\"]').getAttribute('aria-selected') === 'true'")
    ready(page, name)


def ready(page, name):
    if name == "calendar":
        page.wait_for_function("() => !!document.querySelector('[data-pane=\"calendar\"] [data-k=\"title\"]')?.textContent && !document.querySelector('.st-sk')", timeout=8000)
    elif name == "switches":
        page.wait_for_function("""() => { const p = document.querySelector('[data-pane="switches"]'); const want = document.querySelector('[data-seg="range"] [aria-pressed="true"]')?.dataset.v;
            return p && !p.hasAttribute('aria-busy') && p.dataset.range === want && !!p.querySelector('[data-card="switches"] .v')?.textContent; }""", timeout=8000)


def go_range(page, name):
    page.click(f'[data-seg="range"] [data-v="{name}"]')
    page.wait_for_function(f"document.querySelector('[data-seg=\"range\"] [aria-pressed=\"true\"]').dataset.v === '{name}'")
    ready(page, "switches")


def settle(page):
    page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")


def card(page, key):
    return [page.inner_text(f'[data-card="{key}"] {s}') for s in (".cap", ".v", ".d")]


def month_calls(harness):
    return [c[2] for c in harness.calls if c[1] == "screentime_month"]


# ---------------------------------------------------------------- Switches

def test_the_switches_tab_shows_what_python_sends(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "switches")
    d = harness.api.screentime_get("switches", "Today")["data"]
    for c in d["cards"]:
        assert [x.lower() for x in card(page, c["key"])] == [c["label"].lower(), c["value"].lower(), c["sub"].lower()]
    assert page.inner_text('[data-k="peak"]') == d["peak"] and d["peak"].startswith("Peak between")
    assert page.locator('[data-k="targets"] .st-target').count() == len(d["targets"]) == 5
    first = page.locator('[data-k="targets"] .st-target').first
    assert first.locator(".grow b").inner_text() == d["targets"][0]["display"]
    assert first.locator(".grow span").inner_text() == d["targets"][0]["style"]
    assert first.locator(".nums b").inner_text() == str(d["targets"][0]["count"])
    assert first.locator(".nums span").inner_text() == d["targets"][0]["avg"]
    assert first.locator("img").get_attribute("src").startswith("/icon/app/")
    assert page.locator('[data-k="none"]').is_hidden()


def test_the_hours_chart_has_07_to_22_and_marks_the_peak_and_two_more(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "switches")
    chart = '[data-k="chart"] svg.ch'
    bars = page.locator(f"{chart} path.ch-bar")
    assert bars.count() >= 16
    assert page.locator(f"{chart} path.ch-bar.now").count() == 1 and page.locator(f"{chart} path.ch-bar.over").count() == 2
    d = harness.api.screentime_get("switches", "Today")["data"]["hours"]["counts"]
    peak = d.index(max(d))
    tips = page.eval_on_selector_all(f"{chart} rect.ch-hit", "els => els.map(e => e.getAttribute('data-tip'))")
    assert any(t.startswith(f"{peak:02d}:00-") and t.endswith(f"{max(d)} switches") for t in tips)


def test_the_label_follows_the_range_and_the_chart_updates_in_place(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "switches")
    page.evaluate("() => { const q = (s) => document.querySelector(s); window.__keep = { svg: q('[data-pane=\"switches\"] svg.ch'), bar: q('[data-pane=\"switches\"] path.ch-bar'), row: q('.st-target') }; }")
    assert page.inner_text('[data-card="switches"] .cap').lower() == "switches today"
    for r, label in (("Yesterday", "switches yesterday"), ("7 days", "switches (7 days)"), ("30 days", "switches (30 days)")):
        go_range(page, r)
        assert page.inner_text('[data-card="switches"] .cap').lower() == label
        want = harness.api.screentime_get("switches", r)["data"]
        assert page.inner_text('[data-card="switches"] .v') == want["cards"][0]["value"]
    same = page.evaluate("""() => { const q = (s) => document.querySelector(s), k = window.__keep;
        return k.svg === q('[data-pane="switches"] svg.ch') && k.bar === q('[data-pane="switches"] path.ch-bar') && k.row === q('.st-target') && k.row.isConnected; }""")
    assert same                                                                # nothing was rebuilt: the 5 rows were reused, the chart redrawn in place


def test_a_tab_switch_when_fresh_costs_no_call(shell, page, harness, perf):
    open_st(shell, page)
    go_tab(page, "switches")
    go_tab(page, "calendar")
    go_tab(page, "overview")
    n = (count(harness, "screentime_get"), count(harness, "screentime_month"), count(harness, "screentime_day"))
    times = [perf.click_to_frame(f'.tabs [data-tab="{t}"]') for t in ("switches", "calendar", "switches", "overview", "calendar")]
    settle(page)
    assert (count(harness, "screentime_get"), count(harness, "screentime_month"), count(harness, "screentime_day")) == n
    assert max(times) <= 100, times
    print("tab switch ms", [round(t, 1) for t in times])


def test_a_range_change_asks_for_the_switches_tab_only(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "switches")
    go_tab(page, "overview")
    n = count(harness, "screentime_get")
    go_tab(page, "switches")
    go_range(page, "7 days")
    gets = [c[2] for c in harness.calls if c[1] == "screentime_get"]
    assert gets[-1] == ["switches", "7 days"] and len([g for g in gets if g[0] == "switches"]) == 2 and count(harness, "screentime_get") >= n + 1


def test_the_help_button_explains_the_list(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "switches")
    page.click('[data-pane="switches"] [data-k="help"]')
    page.wait_for_selector("dialog[open]")
    assert "checking out of habit" in page.inner_text("dialog[open]")
    page.keyboard.press("Escape")


def test_the_empty_switch_log_shows_the_empty_states(shell, page, harness):
    harness.db.conn.execute("DELETE FROM switch_events")
    harness.db.conn.commit()
    open_st(shell, page)
    go_tab(page, "switches")
    assert [page.inner_text(f'[data-card="{k}"] .v') for k in ("switches", "visit", "short")] == ["0", "-", "0"]
    assert page.inner_text('[data-k="peak"]') == ""
    assert "No switches yet" in page.inner_text('[data-k="chart"]')
    assert page.locator('[data-k="none"]').is_visible() and page.locator(".st-target").count() == 0


def test_a_site_in_the_list_gets_a_site_icon(shell, page, harness):
    harness.db.conn.execute("DELETE FROM switch_events")
    harness.db.conn.commit()
    for i in range(6):
        harness.db.add_switch(NOW.replace(hour=9, minute=i), "chrome.exe", "reddit.com")
    open_st(shell, page)
    go_tab(page, "switches")
    row = page.locator(".st-target").first
    assert row.locator(".grow b").inner_text() == "reddit.com" and row.locator("img").get_attribute("src").startswith("/icon/site/reddit.com")


# ---------------------------------------------------------------- Calendar

def test_the_calendar_shows_the_current_month_without_the_range_buttons(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "calendar")
    assert page.inner_text('[data-pane="calendar"] [data-k="title"]') == "October 2026"
    assert not page.locator('[data-k="range"]').is_visible()
    assert page.locator('[data-pane="calendar"] [data-k="next"]').is_disabled() and page.locator('[data-pane="calendar"] [data-k="prev"]').is_enabled()
    assert month_calls(harness) == [[None, None]]                                       # the first ask does not know the date: Python answers
    d = harness.api.screentime_month(2026, 10)["data"]
    assert page.locator('[data-pane="calendar"] .dc:not(.blank)').count() == 31
    assert page.locator('[data-pane="calendar"] .dh').all_inner_texts() == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    cells = page.locator('[data-pane="calendar"] .dc:not(.blank)')
    assert cells.nth(0).inner_text().split("\n")[0] == "1"
    first_col = page.evaluate("""() => { const g = document.querySelector('[data-pane="calendar"] [data-k="grid"]'); const c = g.querySelector('.dc:not(.blank)'); const dh = g.querySelector('.dh');
        return Math.round((c.getBoundingClientRect().left - dh.getBoundingClientRect().left) / (g.getBoundingClientRect().width / 7)); }""")
    assert first_col == d["offset"] == 3                                                # 1 Oct 2026 is a Thursday
    for i, day in enumerate(d["days"]):
        cell = cells.nth(i)
        assert cell.get_attribute("data-l") == str(day["level"])
        assert ("fut" in cell.get_attribute("class").split()) == day["future"]
        assert ("over" in cell.get_attribute("class").split()) == day["over"]
        assert (cell.get_attribute("title") or "") == day["tip"]
        assert cell.locator(".t").inner_text() == day["text"]
    assert page.locator('[data-pane="calendar"] .dc.today').count() == 1 and page.locator('[data-pane="calendar"] .dc.fut').count() == 20
    assert page.locator('[data-pane="calendar"] .dc.fut:disabled').count() == 20
    assert page.locator('[data-pane="calendar"] .dc.over').count() >= 1


def test_the_four_numbers_and_the_goal_dot_legend(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "calendar")
    d = harness.api.screentime_month(2026, 10)["data"]
    for c in d["stats"]:
        assert [x.lower() for x in card(page, c["key"])] == [c["label"].lower(), c["value"].lower(), c["sub"].lower()]
    assert re.fullmatch(r"\d+ / 11", page.inner_text('[data-card="within"] .v'))
    assert page.locator('[data-pane="calendar"] [data-k="goaldot"]').is_visible()
    assert "Over your 5 h goal" in page.inner_text('[data-pane="calendar"] [data-k="goaldot"]')


def test_without_a_goal_the_dot_and_its_legend_are_gone(shell, page, harness):
    harness.db.set_setting("stats.goal_hours", "0")
    open_st(shell, page)
    go_tab(page, "calendar")
    assert page.inner_text('[data-card="within"] .v') == "-" and page.inner_text('[data-card="within"] .d') == "no daily goal set (Settings)"
    assert page.locator('[data-pane="calendar"] .dc.over').count() == 0 and page.locator('[data-pane="calendar"] [data-k="goaldot"]').is_hidden()


def test_the_month_arrows(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "calendar")
    page.click('[data-pane="calendar"] [data-k="prev"]')
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"title\"]').textContent === 'September 2026'")
    assert month_calls(harness)[-1] == [2026, 9]
    assert page.locator('[data-pane="calendar"] [data-k="next"]').is_enabled()
    assert page.locator('[data-pane="calendar"] .dc:not(.blank)').count() == 30 and page.locator('[data-pane="calendar"] .dc.fut').count() == 0
    assert page.locator('[data-pane="calendar"] .dc.today').count() == 0
    page.click('[data-pane="calendar"] [data-k="prev"]')
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"title\"]').textContent === 'August 2026'")
    assert page.locator('[data-pane="calendar"] .dc:not(.blank)').count() == 31
    page.click('[data-pane="calendar"] [data-k="next"]')
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"title\"]').textContent === 'September 2026'")
    page.click('[data-pane="calendar"] [data-k="next"]')
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"title\"]').textContent === 'October 2026'")
    assert page.locator('[data-pane="calendar"] [data-k="next"]').is_disabled()
    page.evaluate("document.querySelector('[data-pane=\"calendar\"] [data-k=\"next\"]').disabled = false; document.querySelector('[data-pane=\"calendar\"] [data-k=\"next\"]').click()")
    page.wait_for_timeout(150)
    assert page.inner_text('[data-pane="calendar"] [data-k="title"]') == "October 2026" and [2026, 11] not in month_calls(harness)       # (even forced, the page does not go past the current month)


def test_a_month_with_five_or_six_rows_and_a_short_february(shell, page, harness):
    harness.now = harness.now.replace(year=2026, month=3, day=14)
    open_st(shell, page)
    go_tab(page, "calendar")
    page.click('[data-pane="calendar"] [data-k="prev"]')
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"title\"]').textContent === 'February 2026'")
    assert page.locator('[data-pane="calendar"] .dc:not(.blank)').count() == 28
    visible = page.locator('[data-pane="calendar"] .dc:not([hidden])').count()
    assert visible == 35                                                                # 1 Feb 2026 is a Sunday: 6 + 28 cells = 5 rows


def test_clicking_a_day_shows_its_details_without_reloading_the_month(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "calendar")
    assert "Click a day to see what you spent time on." in page.inner_text('[data-pane="calendar"] [data-k="hint"]')
    assert page.inner_text('[data-pane="calendar"] [data-k="daytitle"]') == "Day details"
    months, days = len(month_calls(harness)), count(harness, "screentime_day")
    cell = page.locator('[data-pane="calendar"] .dc:not(.blank):not(.fut)').nth(4)             # 5 Oct 2026
    cell.click()
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"daytitle\"]').textContent !== 'Day details'")
    want = harness.api.screentime_day("2026-10-05")["data"]
    assert page.inner_text('[data-pane="calendar"] [data-k="daytitle"]') == want["title"] == "Monday 05 Oct 2026"
    assert page.inner_text('[data-pane="calendar"] [data-k="daynote"]') == want["note"]
    rows = page.locator('[data-pane="calendar"] .st-dayrow')
    assert rows.count() == len(want["rows"]) == 10
    assert [t.replace("\n", " ") for t in rows.all_inner_texts()] == [f"{r['name']} {r['time']}" for r in want["rows"]]
    assert page.locator('[data-pane="calendar"] [data-k="hint"]').is_hidden()
    assert "sel" in cell.get_attribute("class").split() and page.locator('[data-pane="calendar"] .dc.sel').count() == 1
    assert (len(month_calls(harness)), count(harness, "screentime_day")) == (months, days + 1)       # the month was NOT read again
    page.locator('[data-pane="calendar"] .dc:not(.blank):not(.fut)').nth(5).click()
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"daytitle\"]').textContent.startsWith('Tuesday 06')")
    assert page.locator('[data-pane="calendar"] .dc.sel').count() == 1 and len(month_calls(harness)) == months


def test_a_future_day_cannot_be_clicked(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "calendar")
    days = count(harness, "screentime_day")
    page.locator('[data-pane="calendar"] .dc.fut').first.click(force=True)
    page.wait_for_timeout(150)
    assert count(harness, "screentime_day") == days and page.locator('[data-pane="calendar"] .dc.sel').count() == 0


def test_a_day_with_nothing_says_so(shell, page, harness):
    harness.db.conn.execute("DELETE FROM activity WHERE minute >= '2026-10-03' AND minute < '2026-10-04'")
    harness.db.conn.commit()
    open_st(shell, page)
    go_tab(page, "calendar")
    page.locator('[data-pane="calendar"] .dc:not(.blank)').nth(2).click()
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"daynote\"]').textContent === 'nothing recorded'")
    assert page.locator('[data-pane="calendar"] .st-dayrow').count() == 0
    assert page.inner_text('[data-pane="calendar"] [data-k="hint"]') == "Nothing recorded that day."


def test_the_selected_ring_follows_the_day_across_months(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "calendar")
    page.locator('[data-pane="calendar"] .dc:not(.blank):not(.fut)').nth(4).click()
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"daytitle\"]').textContent.startsWith('Monday 05')")
    page.click('[data-pane="calendar"] [data-k="prev"]')
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"title\"]').textContent === 'September 2026'")
    assert page.locator('[data-pane="calendar"] .dc.sel').count() == 0
    assert page.inner_text('[data-pane="calendar"] [data-k="daytitle"]').startswith("Monday 05")        # (its details stay)
    page.click('[data-pane="calendar"] [data-k="next"]')
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"title\"]').textContent === 'October 2026'")
    assert page.locator('[data-pane="calendar"] .dc.sel').count() == 1


def test_the_cells_are_reused_when_the_month_changes(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "calendar")
    page.evaluate("() => { window.__cells = [...document.querySelectorAll('[data-pane=\"calendar\"] .dc')]; }")
    page.click('[data-pane="calendar"] [data-k="prev"]')
    page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"title\"]').textContent === 'September 2026'")
    assert page.evaluate("() => window.__cells.length === 42 && window.__cells.every((c, i) => c === document.querySelectorAll('[data-pane=\"calendar\"] .dc')[i])")


def test_the_help_button_explains_the_colours(shell, page, harness):
    open_st(shell, page)
    go_tab(page, "calendar")
    page.click('[data-pane="calendar"] [data-k="help"]')
    page.wait_for_selector("dialog[open]")
    assert "red dot" in page.inner_text("dialog[open]")
    page.keyboard.press("Escape")


def test_it_can_be_the_tab_it_opens_on(shell, page, harness):
    harness.db.set_setting("screentime.tab", "Calendar")
    shell(route="screentime")
    ready(page, "calendar")
    assert page.inner_text('[data-pane="calendar"] [data-k="title"]') == "October 2026"
    assert page.locator('.tabs [aria-selected="true"]').get_attribute("data-tab") == "calendar" and not page.locator('[data-k="range"]').is_visible()


def test_the_empty_database_shows_the_calendar_with_zeros(shell, page, harness):
    for table in ("activity", "daily_activity", "switch_events"):
        harness.db.conn.execute(f"DELETE FROM {table}")
    harness.db.conn.commit()
    open_st(shell, page)
    go_tab(page, "calendar")
    assert page.inner_text('[data-card="month"] .v') == "0 m" and page.inner_text('[data-card="busiest"] .v') == "-"


# ---------------------------------------------------------------- looks

@pytest.mark.parametrize("theme", ["dark", "amoled", "light"])
@pytest.mark.parametrize("name", ["switches", "calendar"])
def test_both_tabs_render_without_errors_in_every_theme(shell, page, harness, name, theme):
    open_st(shell, page, width=1280, height=900)
    page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
    go_tab(page, name)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if name == "calendar":
        page.locator('[data-pane="calendar"] .dc:not(.blank):not(.fut)').nth(3).click()
        page.wait_for_function("document.querySelector('[data-pane=\"calendar\"] [data-k=\"daytitle\"]').textContent !== 'Day details'")
        bg = page.evaluate("() => { const c = document.querySelector('[data-pane=\"calendar\"] .dc[data-l=\"4\"]'); return c ? getComputedStyle(c).backgroundColor : 'none'; }")
        assert bg != "rgba(0, 0, 0, 0)"
    else:
        for r in RANGES:
            go_range(page, r)
            assert page.locator('[data-pane="switches"] path.ch-bar').count() >= 16


@pytest.mark.parametrize("name", ["switches", "calendar"])
def test_a_narrow_window_has_no_sideways_scroll(shell, page, harness, name):
    open_st(shell, page, width=840, height=650)
    go_tab(page, name)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate("() => { const p = document.querySelector('.st-pane:not([hidden])'); return p.scrollWidth <= p.clientWidth + 1; }")
    if name == "calendar":
        cell = page.locator('[data-pane="calendar"] .dc:not(.blank)').nth(10)
        assert cell.is_visible() and cell.locator("b").is_visible()
