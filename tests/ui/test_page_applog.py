"""Settings > App log (js/applog.js + webapi/applog_api.py) in Chrome against the real Python Api: events newest first, grouped by
day, in ONE virtual list; the filter chips and the search box; Copy (the text on the clipboard) and Save log… (the owner's own
Save dialog -> a lockdown-log-YYYY-MM-DD.txt)."""
from datetime import datetime, timedelta

import pytest

import applog
from webapi import applog_api

TODAY = datetime.now().replace(microsecond=0)


def seed(harness, events):
    """[(minutes ago, kind, text)] -> app_events (oldest first), after clearing the log."""
    conn = harness.db.conn
    conn.execute("DELETE FROM app_events")
    conn.executemany("INSERT INTO app_events (ts, source, kind, text) VALUES (?, ?, ?, ?)", [
        ((TODAY - timedelta(minutes=ago)).strftime(applog.TS_FORMAT), "service" if kind == "start" else "agent", kind, text)
        for ago, kind, text in sorted(events, reverse=True)])
    conn.commit()


def open_page(shell, page):
    shell(route="settings")
    page.wait_for_selector('[data-card="applog"]', timeout=8000)
    page.wait_for_selector('[data-card="applog"] .vl-row, [data-card="applog"] .al-empty:not([hidden])', timeout=8000)
    return page


def rows(page):
    return page.locator('[data-card="applog"] .vl-row:not([hidden])')


def texts(page):
    """Everything in view, top to bottom: "# Today" for a day heading, else the event line (with its source tag, if any)."""
    return page.evaluate("""() => [...document.querySelectorAll('[data-card="applog"] .vl-row:not([hidden])')]
        .sort((a, b) => a._i - b._i).map((n) => n.classList.contains('al-day') ? '# ' + n.textContent : n.querySelector('.x').textContent)""")


def event_texts(page):
    """The event lines in view (without the day headings and the source tag), top to bottom."""
    return page.evaluate("""() => [...document.querySelectorAll('[data-card="applog"] .al-row:not([hidden])')]
        .sort((a, b) => a._i - b._i).map((n) => [...n.querySelector('.x').childNodes].filter((c) => c.nodeType === 3).map((c) => c.textContent).join(''))""")


def wait_events(page, expected, timeout=5000):
    """Wait until exactly these event lines are in view (newest first)."""
    page.wait_for_function("""(want) => {
        const got = [...document.querySelectorAll('[data-card="applog"] .al-row:not([hidden])')]
          .sort((a, b) => a._i - b._i).map((n) => [...n.querySelector('.x').childNodes].filter((c) => c.nodeType === 3).map((c) => c.textContent).join(''));
        return JSON.stringify(got) === JSON.stringify(want);
    }""", arg=expected, timeout=timeout)


def chip(page, name):
    page.locator('[data-card="applog"] .seg button', has_text=name).click()


EVENTS = [(600, "change", "Added site YouTube (Time limit: 30m per day)"), (500, "start", "The Lockdown service 0.87.0 started"),
          (400, "crash", "Unexpected error: ValueError: nope"), (300, "challenge_passed", "Anti-Bypass challenge passed (for: Remove YouTube)"),
          (200, "updated", "Updated from 0.86.0 to 0.87.0"), (100, "change", "Rule changed: YouTube - time limit 30m per day → 45m per day")]


def test_the_card_shows_the_events_newest_first_grouped_by_day(harness, shell, page):
    seed(harness, EVENTS + [(60 * 24 * 3, "start", "Lockdown 0.86.0 started")])
    open_page(shell, page)
    assert page.locator('[data-card="applog"] h3').inner_text() == "App log"
    assert page.locator('[data-card="applog"] .seg button').all_inner_texts() == ["All", "Changes", "Updates", "Problems", "Anti-Bypass", "Bypass used"]
    assert page.get_by_role("button", name="Copy").is_visible() and page.get_by_role("button", name="Save log…").is_visible()
    got = texts(page)
    heads = [t for t in got if t.startswith("# ")]
    assert heads[0] in ("# Today", "# Yesterday") and heads[-1] == f"# {(TODAY - timedelta(days=3)):%A} {(TODAY - timedelta(days=3)).day} {(TODAY - timedelta(days=3)):%B}"
    events = event_texts(page)
    assert events[0] == "Rule changed: YouTube - time limit 30m per day → 45m per day"
    assert events[-1] == "Lockdown 0.86.0 started"
    assert page.locator('[data-al="count"]').inner_text() == "7 events shown of 7 · kept for 90 days"


def test_problems_are_red_and_the_other_service_lines_say_who_wrote_them(harness, shell, page):
    seed(harness, EVENTS)
    open_page(shell, page)
    crash = page.locator('[data-card="applog"] .al-row.bad .x')
    assert crash.count() == 1 and "ValueError" in crash.inner_text()
    service_line = page.locator('[data-card="applog"] .al-row', has_text="The Lockdown service")
    assert service_line.locator(".s").inner_text() == "service"
    assert page.locator('[data-card="applog"] .al-row.guard').count() == 1


def test_the_chips_filter_in_python_and_start_from_the_newest(harness, shell, page):
    seed(harness, EVENTS)
    open_page(shell, page)
    chip(page, "Problems")
    wait_events(page, ["Unexpected error: ValueError: nope"])
    chip(page, "Changes")
    wait_events(page, ["Rule changed: YouTube - time limit 30m per day → 45m per day", "Added site YouTube (Time limit: 30m per day)"])
    chip(page, "Updates")
    wait_events(page, ["Updated from 0.86.0 to 0.87.0"])
    chip(page, "Anti-Bypass")
    wait_events(page, ["Anti-Bypass challenge passed (for: Remove YouTube)"])
    chip(page, "All")
    page.wait_for_function("document.querySelectorAll('[data-card=\"applog\"] .al-row:not([hidden])').length === 6")
    assert [c[2][0] for c in harness.calls if c[1] == "applog_get"][-5:] == ["all", "problems", "changes", "updates", "antibypass"] \
        or [c[2][0] for c in harness.calls if c[1] == "applog_get"][-5:] == ["problems", "changes", "updates", "antibypass", "all"]


BYPASS = [(60 * 24 * 10, "emergency", "Emergency unlock used for 5 min until 10:05: reason"),
          (90, "pause", "Pause my blocks started, until 12:00"), (80, "off", "Lockdown switched off: nothing is blocked until it is switched on again"),
          (70, "on", "Lockdown switched back on"), (60, "tray_exit", "Quit from the tray (Exit): the tray icon and the window are closed, blocking keeps running"),
          (50, "challenge_failed", "Anti-Bypass challenge failed: typed too fast (for: Remove YouTube)"),
          (40, "emergency", "Emergency unlock used for 10 min until 11:10: reason")]


def test_bypass_used_the_only_menu_and_the_period(harness, shell, page):
    seed(harness, EVENTS + BYPASS)
    open_page(shell, page)
    chip(page, "Bypass used")
    wait_events(page, ["Emergency unlock used for 10 min until 11:10: reason",
                       "Quit from the tray (Exit): the tray icon and the window are closed, blocking keeps running",
                       "Lockdown switched off: nothing is blocked until it is switched on again", "Pause my blocks started, until 12:00",
                       "Anti-Bypass challenge passed (for: Remove YouTube)", "Emergency unlock used for 5 min until 10:05: reason"])
    sel = page.locator('[data-al="only"]')
    sel.select_option("emergency")                     # (the menu stands in for the chips: none of them is lit)
    wait_events(page, ["Emergency unlock used for 10 min until 11:10: reason", "Emergency unlock used for 5 min until 10:05: reason"])
    assert page.locator('[data-card="applog"] .seg [aria-pressed="true"]').count() == 0
    page.locator('[data-al="period"]').select_option("7")
    wait_events(page, ["Emergency unlock used for 10 min until 11:10: reason"])
    assert [c[2] for c in harness.calls if c[1] == "applog_get"][-1] == ["emergency", "", None, 300, 7]
    sel.select_option("switched")
    wait_events(page, ["Lockdown switched back on", "Lockdown switched off: nothing is blocked until it is switched on again"])
    sel.select_option("challenges_failed")
    wait_events(page, ["Anti-Bypass challenge failed: typed too fast (for: Remove YouTube)"])
    chip(page, "Problems")                             # (a chip clears the menu; the period stays)
    wait_events(page, ["Anti-Bypass challenge failed: typed too fast (for: Remove YouTube)", "Unexpected error: ValueError: nope"])
    assert sel.input_value() == ""
    page.locator('[data-al="period"]').select_option("0")
    sel.select_option("tray_exit")
    wait_events(page, ["Quit from the tray (Exit): the tray icon and the window are closed, blocking keeps running"])
    sel.select_option("")
    page.wait_for_function("document.querySelectorAll('[data-card=\"applog\"] .al-row:not([hidden])').length === 13")
    assert page.locator('[data-card="applog"] .seg [aria-pressed="true"]').inner_text() == "All"


def test_the_search_waits_for_the_next_key_and_asks_python(harness, shell, page):
    seed(harness, EVENTS)
    open_page(shell, page)
    before = harness.rpc_count("applog_get")
    page.locator('[data-al="search"]').type("rule chan", delay=15)
    wait_events(page, ["Rule changed: YouTube - time limit 30m per day → 45m per day"])
    assert harness.rpc_count("applog_get") - before == 1                 # (one call for the whole word, not one per key)
    assert [c[2][1] for c in harness.calls if c[1] == "applog_get"][-1] == "rule chan"
    page.locator('[data-al="search"]').fill("zzzz-nothing")
    page.wait_for_selector('[data-card="applog"] .al-empty:not([hidden]) h3', timeout=5000)
    assert page.locator('[data-card="applog"] .al-empty h3').inner_text() == "Nothing matches"
    assert page.locator('[data-al="count"]').inner_text() == "No matching events"


def test_an_empty_log_says_so(harness, shell, page):
    seed(harness, [])
    open_page(shell, page)
    assert page.locator('[data-card="applog"] .al-empty h3').inner_text() == "Nothing logged yet"
    assert page.locator('[data-al="count"]').inner_text() == "No events yet"


def test_twenty_thousand_events_are_a_few_dozen_nodes_and_older_ones_load_while_scrolling(harness, shell, page):
    conn = harness.db.conn
    conn.execute("DELETE FROM app_events")
    base = TODAY - timedelta(days=60)
    conn.executemany("INSERT INTO app_events (ts, source, kind, text) VALUES (?, 'agent', 'change', ?)",
                     [((base + timedelta(seconds=i * 250)).strftime(applog.TS_FORMAT), f"Rule changed: item {i}") for i in range(20000)])
    conn.commit()
    open_page(shell, page)
    assert page.locator('[data-card="applog"] .vl-row').count() < 60
    assert page.locator('[data-al="count"]').inner_text().startswith("300+ events shown of 20000")
    assert page.locator('[data-al="more"]').is_visible()
    before = harness.rpc_count("applog_get")
    page.evaluate("""() => { const l = document.querySelector('[data-al="list"]'); l.scrollTop = l.scrollHeight; }""")
    page.wait_for_function("document.querySelector('[data-al=\"count\"]').textContent.startsWith('600+')", timeout=5000)
    assert harness.rpc_count("applog_get") - before == 1
    older = [c for c in harness.calls if c[1] == "applog_get"][-1][2]
    assert older[0] == "all" and older[1] == "" and older[2] > 0 and older[3] == 300         # (before = the oldest id it had)
    assert page.locator('[data-card="applog"] .vl-row').count() < 60
    page.locator('[data-al="more"]').click()
    page.wait_for_function("document.querySelector('[data-al=\"count\"]').textContent.startsWith('900+')", timeout=5000)


def test_copy_puts_the_text_of_the_filtered_events_on_the_clipboard(harness, shell, page):
    seed(harness, EVENTS)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    open_page(shell, page)
    page.get_by_role("button", name="Copy").click()
    page.wait_for_function("document.getElementById('toast') && !document.getElementById('toast').hidden")
    text = page.evaluate("navigator.clipboard.readText()")
    assert text.startswith("Lockdown app log\n") and "Lockdown version:" in text and "Windows:" in text
    assert "[agent]  Rule changed: YouTube - time limit 30m per day → 45m per day" in text and "ValueError" in text
    assert "== lockdown.log" not in text                                  # (Copy is the events; Save log… adds the two log files)
    assert [c[2] for c in harness.calls if c[1] == "applog_text"] == [["all", "", 0]]
    chip(page, "Problems")
    wait_events(page, ["Unexpected error: ValueError: nope"])
    page.get_by_role("button", name="Copy").click()
    page.wait_for_function("navigator.clipboard.readText().then((t) => t.includes('Filter: problems'))")
    assert "Rule changed" not in page.evaluate("navigator.clipboard.readText()")


def test_save_log_opens_the_native_dialog_and_writes_the_file(harness, shell, page, tmp_path, monkeypatch):
    seed(harness, EVENTS)
    asked = []
    target = tmp_path / "lockdown-log-test.txt"
    monkeypatch.setattr(applog_api, "_ask_save_path", lambda window, name: asked.append(name) or str(target))
    open_page(shell, page)
    page.get_by_role("button", name="Save log…").click()
    page.wait_for_function("document.getElementById('toast').textContent.includes('Saved to')", timeout=5000)
    assert asked == [f"lockdown-log-{harness.now:%Y-%m-%d}.txt"]
    saved = target.read_text(encoding="utf-8")
    assert "Lockdown app log" in saved and "Updated from 0.86.0 to 0.87.0" in saved and "== lockdown.log" in saved
    assert [c[2] for c in harness.calls if c[1] == "applog_save"] == [[]]


def test_cancelling_the_save_dialog_says_nothing(harness, shell, page, monkeypatch):
    seed(harness, EVENTS)
    monkeypatch.setattr(applog_api, "_ask_save_path", lambda window, name: None)
    open_page(shell, page)
    page.get_by_role("button", name="Save log…").click()
    called = harness.rpc_count("applog_save")
    page.wait_for_function("!document.querySelector('[data-al=\"save\"]').disabled", timeout=5000)
    assert harness.rpc_count("applog_save") == called == 1
    assert page.evaluate("document.getElementById('toast').hidden")


@pytest.mark.allow_console_errors
def test_a_failing_save_shows_why(harness, shell, page, monkeypatch):
    seed(harness, EVENTS)

    def boom(window, name):
        raise applog_api.ApiError("The Save dialog isn't available.")
    monkeypatch.setattr(applog_api, "_ask_save_path", boom)
    open_page(shell, page)
    page.get_by_role("button", name="Save log…").click()
    page.wait_for_function("document.getElementById('toast').textContent.includes('Save dialog')", timeout=5000)
