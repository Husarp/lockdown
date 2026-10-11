"""R22: Blocking > Calendar (js/pages/blocking/calendar.js) in Chrome against the REAL Python Api (webapi/calendar.py) on a
temporary database (see harness.py).

What is checked: the Day / 3 days / Week views (header, title, columns, strips, bars with tooltips, circles, now-line, cards,
legend), the click that used to open the wrong item (every row's strip edits ITS OWN item), a day opened from a multi-day
strip, week navigation, the Sites / Apps filter, disabled items, live reloads, and the numbers (first open, 200 items).

What can only be proven on Windows: the tab inside WebView2 (this is Chrome), real app / site icons."""
from datetime import datetime, timedelta

import pytest

from harness import NOW, Perf, open_shell
from rules import make_schedule

EVERY_DAY = list(range(7))


def hours(start, end, days=EVERY_DAY, mode="block"):
    return {"rule_type": "scheduled", "schedule": make_schedule(mode, [(days, start, end)])}


def add(harness, name, rules, kind="site"):
    target = f"{name.lower()}.com" if kind == "site" else f"{name.lower()}.exe"
    return harness.db.add_item(name, [target], kind, "manual", rules)


def open_calendar(shell, page, **kw):
    shell(route="blocking", **kw)
    page.wait_for_selector('[data-tab="calendar"]', timeout=8000)
    page.click('[data-tab="calendar"]')
    page.wait_for_selector(".cal-row:not([hidden]), .cal-empty:not([hidden])", timeout=8000)
    return page


ROWS = ".cal-list > .vl-row:not([hidden])"
BY_PLACE = "els => els.sort((a, b) => a._i - b._i)"


def names(page):
    return page.eval_on_selector_all(ROWS, f"els => ({BY_PLACE})(els).map(e => e.querySelector('.cal-who b').textContent)")


def row(page, name):
    return page.locator(f'{ROWS}:has(.cal-who b:text-is("{name}"))').first


def spy_on_go(page):
    """Record router.go(...) instead of navigating, so a test can see which item a click asked to edit."""
    page.evaluate("""async () => {
      const { router } = await import(new URL('js/router.js', location.href).href);
      window.__went = [];
      const go = router.go.bind(router);
      router.go = (...a) => { window.__went.push(a); };
      window.__realGo = go;
    }""")


def went(page):
    return page.evaluate("window.__went")


@pytest.fixture
def three(harness):
    """Three rows, in name order: Aaa (22-07), Bbb (permanent), Ccc (apps: 09-17)."""
    ids = {"Aaa": add(harness, "Aaa", [hours("22:00", "07:00")]),
           "Bbb": add(harness, "Bbb", [{"rule_type": "permanent"}]),
           "Ccc": add(harness, "Ccc", [hours("09:00", "17:00")], kind="app")}
    return ids


# ---------------------------------------------------------------- Day view

def test_day_view_header_rows_strips_and_cards(shell, page, harness):
    open_calendar(shell, page)
    assert page.inner_text('.cal [data-k="title"]') == "Sunday 11 October"
    assert page.is_visible('.cal [data-k="today"]')
    assert page.inner_text('.cal [data-k="head"]') == "BLOCKED TODAY"
    assert page.get_attribute('[data-seg="range"] [data-v="1"]', "aria-pressed") == "true"
    got = names(page)
    assert "Twitch" in got and "X" in got                      # the synthetic data: 09-17 daily, and permanent
    twitch = row(page, "Twitch")
    assert twitch.locator(".cal-bar").count() == 1
    assert twitch.locator(".cal-bar title").text_content() == "09:00–17:00"
    assert twitch.locator(".cal-now").count() == 1             # now = 15:42 is on today's strip
    assert twitch.locator(".cal-sum b").inner_text() == "8 h blocked"
    assert row(page, "X").locator(".cal-sum b").inner_text() == "blocked all day"
    assert page.is_visible('.cal [data-k="cards"]')
    assert page.inner_text('.cal [data-k="cnext"]').endswith("Twitch allowed again") or "allowed again" in page.inner_text('.cal [data-k="cnext"]')
    assert page.inner_text('.cal [data-k="cbusy"]').startswith("09:00–17:00")
    legend = page.inner_text('.cal [data-k="legend"]')
    assert "Productive" in legend and "Distracting" in legend and "now 15:42" in legend and "emergency unlock" in legend
    assert [t for t in page.eval_on_selector_all(".cal-axis span", "e => e.map(x => x.textContent)")] == [f"{h:02d}" for h in range(0, 24, 2)]


def test_every_day_view_strip_edits_its_own_row_not_the_last_one(shell, page, harness, three):
    """The old window opened the LAST row's item from every strip (the loop variable was captured late)."""
    open_calendar(shell, page)
    spy_on_go(page)
    got = names(page)
    assert got.index("Aaa") < got.index("Bbb") < got.index("Ccc")
    for name in ("Aaa", "Ccc", "Bbb"):                          # not in row order, on purpose
        row(page, name).locator(".cal-track").click()
        edit = went(page)[-1]
        assert edit[0] == "blocking" and edit[1]["tab"] == "add" and edit[1]["edit"] == three[name], (name, edit)
    n = len(went(page))
    row(page, "Aaa").locator(".cal-who b").click()              # the name does the same
    assert went(page)[n][1]["edit"] == three["Aaa"]


def test_the_emergency_circle_sits_on_its_hour_and_has_a_tooltip(shell, page, harness, three):
    start = NOW.replace(hour=12, minute=0, second=0)
    harness.db.add_unlock([three["Bbb"]], ["Bbb"], start, start + timedelta(minutes=30))
    open_calendar(shell, page)
    e = row(page, "Bbb").locator(".cal-e")
    assert e.count() == 1 and e.get_attribute("title") == "emergency unlock at 12:00"
    assert "left:50%" in e.get_attribute("style").replace(" ", "")


def test_a_disabled_item_is_faint_and_says_so(shell, page, harness):
    iid = add(harness, "Zzz", [hours("10:00", "11:00")])
    harness.db.update_item(iid, "Zzz", ["zzz.com"], None, [hours("10:00", "11:00")], disabled=True)
    open_calendar(shell, page)
    r = row(page, "Zzz")
    assert "off" in r.get_attribute("class") and r.locator(".cal-sum b").inner_text() == "Disabled"


def test_limits_are_named_not_drawn(shell, page, harness):
    open_calendar(shell, page)
    note = page.inner_text('.cal [data-k="note"]')
    assert "YouTube" in note and "Reddit" in note and "not drawn" in note
    assert "YouTube" not in names(page)


def test_nothing_to_draw_says_why(shell, page, harness):
    for item in harness.db.list_items():
        harness.db.update_item(item["id"], item["display_name"], item["target"].split(), None, [], disabled=False)
    open_calendar(shell, page)
    assert page.is_visible(".cal-empty") and "aren't tied to a clock time" in page.inner_text(".cal-empty")


# ---------------------------------------------------------------- 3 days, Week, navigation

def test_week_view_columns_today_ring_and_navigation(shell, page, harness, three):
    open_calendar(shell, page)
    page.click('[data-seg="range"] [data-v="7"]')
    page.wait_for_function("document.querySelector('.cal [data-k=title]').textContent === '05 – 11 October'")
    assert page.inner_text('.cal [data-k="head"]') == "WEEK TOTAL"
    assert page.eval_on_selector_all(".cal-axis span", "e => e.map(x => x.textContent)") == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    r = row(page, "Aaa")
    assert r.locator(".cal-track").count() == 7
    assert r.locator(".cal-track.today").count() == 1
    assert r.locator(".cal-sum b").inner_text() == "63 h" and r.locator(".cal-sum span").inner_text() == "blocked this week"
    assert not page.is_visible('.cal [data-k="cards"]')
    page.click('.cal [data-k="prev"]')
    page.wait_for_function("document.querySelector('.cal [data-k=title]').textContent === '28 Sep – 04 Oct'")
    assert row(page, "Aaa").locator(".cal-track.today").count() == 0
    page.click('.cal [data-k="next"]')
    page.click('.cal [data-k="next"]')
    page.wait_for_function("document.querySelector('.cal [data-k=title]').textContent === '12 – 18 October'")
    assert page.is_hidden('.cal [data-k="today"]')


def test_three_days_and_a_click_on_a_day_opens_it_in_day_view(shell, page, harness, three):
    open_calendar(shell, page)
    page.click('[data-seg="range"] [data-v="3"]')
    page.wait_for_function("document.querySelector('.cal [data-k=head]').textContent === '3-DAY TOTAL'")
    assert page.inner_text('.cal [data-k="title"]') == "11 – 13 October"
    spy_on_go(page)
    row(page, "Ccc").locator(".cal-track").nth(1).click()                  # the second day: Monday 12th
    page.wait_for_function("document.querySelector('.cal [data-k=title]').textContent === 'Monday 12 October'")
    assert page.inner_text('.cal [data-k="head"]') == "BLOCKED THAT DAY"
    assert went(page) == []                                                # (it opened the day, it did not edit)
    assert page.get_attribute('[data-seg="range"] [data-v="1"]', "aria-pressed") == "true"
    assert page.inner_text('.cal [data-k="cnext"]') == "(not today)"
    assert page.is_hidden('.cal [data-k="today"]')


# ---------------------------------------------------------------- the filter

def wait_shown(page, name, shown=True):
    """Wait until a row called `name` is in view (or gone). Hidden, recycled nodes keep their old text, so look at live rows."""
    page.wait_for_function(
        """([name, shown]) => [...document.querySelectorAll('.cal-list > .vl-row:not([hidden]) .cal-who b')].some(b => b.textContent === name) === shown""",
        arg=[name, shown], timeout=6000)


def test_filter_sites_hides_apps_and_apps_hides_sites(shell, page, harness, three):
    open_calendar(shell, page)
    assert {"Aaa", "Bbb", "Ccc"} <= set(names(page))
    page.select_option('.cal [data-k="filter"]', "sites")
    wait_shown(page, "Ccc", False)
    assert "Aaa" in names(page) and "Ccc" not in names(page)
    page.select_option('.cal [data-k="filter"]', "apps")
    wait_shown(page, "Aaa", False)
    assert "Ccc" in names(page) and "Bbb" not in names(page)
    page.select_option('.cal [data-k="filter"]', "all")
    wait_shown(page, "Aaa")


# ---------------------------------------------------------------- live

def test_a_change_of_the_blocks_reloads_the_tab(shell, page, harness, push):
    open_calendar(shell, page)
    assert "Newbie" not in names(page)
    add(harness, "Newbie", [hours("01:00", "02:00")])
    push("blocks_changed")
    wait_shown(page, "Newbie")


def test_a_changed_rule_redraws_its_bar(shell, page, harness, push):
    iid = add(harness, "Mover", [hours("10:00", "11:00")])
    open_calendar(shell, page)
    assert row(page, "Mover").locator(".cal-bar title").text_content() == "10:00–11:00"
    harness.db.update_item(iid, "Mover", ["mover.com"], None, [hours("10:00", "12:30")])
    push("blocks_changed")
    page.wait_for_function("[...document.querySelectorAll('.cal-bar title')].some(t => t.textContent === '10:00–12:30')", timeout=6000)


# ---------------------------------------------------------------- numbers and looks

@pytest.mark.parametrize("theme", ["dark", "amoled", "light"])
def test_no_console_errors_in_any_theme_and_narrow(shell, page, harness, three, theme):
    harness.db.set_setting("ui.theme", theme)
    open_calendar(shell, page, width=800, height=700)
    page.click('[data-seg="range"] [data-v="7"]')
    page.wait_for_selector(".cal-track.today")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


@pytest.fixture
def big(harness, page):
    for i in range(200):
        add(harness, f"Site {i:03d}", [hours("22:00", "07:00"), hours("08:00", "20:00", [0, 1], "allow")])
    return harness, page


def test_200_items_week_view_is_light_and_scrolls_smoothly(big, capsys):
    harness, page = big
    open_shell(page, harness, route="blocking")
    page.click('[data-tab="calendar"]')
    page.wait_for_selector(".cal-row")
    page.click('[data-seg="range"] [data-v="7"]')
    page.wait_for_selector(".cal-track.today")
    live = page.eval_on_selector_all(ROWS, "e => e.length")
    r = Perf(page).scroll_frames("#cal-list", seconds=2.0, probe=ROWS)
    print(f"calendar scroll 200 items x 7 days: {r}, live rows {live}")
    assert live < 45 and r["live_max"] < 45 and r["max_ms"] < 100


def test_the_first_open_is_fast(shell, page, harness, perf, three):
    shell(route="blocking")
    page.wait_for_selector(".bo-row, .vl-row", timeout=8000)
    ms = perf.click_until('[data-tab="calendar"]', ".cal-row", 4000)
    print(f"Calendar first open: {ms:.0f} ms")
    assert 0 < ms < 400


def test_nothing_runs_while_the_window_is_hidden(shell, page, harness, perf, three):
    open_calendar(shell, page)
    perf.set_hidden(True)
    page.wait_for_timeout(300)
    perf.reset_timer_calls()
    n = len(harness.calls)
    page.wait_for_timeout(2500)
    assert sum(perf.timer_calls().values()) == 0 and len(harness.calls) == n
    perf.set_hidden(False)
