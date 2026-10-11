"""R13: the Dashboard (js/pages/dashboard.js) and the Display settings dialog (js/display.js) in Chrome against the real
Python Api (see harness.py).

What is checked: every card renders without a console error in every theme, hide + move a card (Customise and the
Display dialog) persist after a reload, a `usage_tick` push changes the screen-time number in the SAME element (no
re-render, no Api call), a `status` push asks again only when something the cards show changed, the pause and bedtime
countdowns tick on the page's own clock, the empty state and its two buttons, the first visit is fast, and a hidden
window does nothing."""
import json
import re
from datetime import timedelta

import pytest

import pause
from harness import NOW

ALL_CARDS = ["stats", "today", "limits", "coming", "week", "visits", "glance", "active", "bedtime", "pause", "emergency"]


def open_dash(shell, page, **kw):
    shell(route="dashboard", **kw)
    page.wait_for_selector('[data-card="stats"] [data-stat="screen"] .v', timeout=8000)
    page.wait_for_function("document.querySelector('[data-stat=\"screen\"] .v').textContent.length > 0")
    return page


def wait_for(page, js, timeout=5000):
    page.wait_for_function(js, timeout=timeout)


def order(page):
    return page.eval_on_selector_all(".dash > .dash-item:not([hidden])", "els => els.map(e => e.dataset.card)")


def stored(harness, key):
    return json.loads(harness.db.get_setting(key, "[]"))


# ---------------------------------------------------------------- the cards

@pytest.mark.parametrize("theme", ["dark", "amoled", "light"])
def test_every_card_renders_in_every_theme(shell, page, harness, theme):
    open_dash(shell, page, width=1280, height=900)
    page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
    assert order(page) == ALL_CARDS
    assert page.inner_text('[data-stat="screen"] .v') != ""
    assert re.fullmatch(r"\d+ of \d+", page.inner_text('[data-stat="blocked"] .v'))
    assert page.locator('[data-card="today"] svg.ch rect').count() > 3          # the timeline has segments
    assert page.locator('[data-card="week"] svg.ch path.ch-bar').count() == 7
    assert page.locator('[data-card="limits"] .lim-row').count() >= 3
    assert page.locator('[data-card="emergency"] .low-row').count() == 1
    assert page.inner_text('[data-card="bedtime"] [data-n="off"]').startswith("The bedtime reminder is off")
    assert page.inner_text('[data-card="pause"] [data-n="on"] .btn') == "Pause my blocks"
    assert "daily goal 5 h" in page.inner_text('[data-card="week"]')
    sub = page.inner_text('[data-k="sub"]')
    assert sub.startswith("Sunday, 11 October") and "tracking since" in sub
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_limits_rows_use_the_blocked_until_line_once(shell, page, harness):
    open_dash(shell, page)
    rows = page.eval_on_selector_all('[data-card="limits"] .lim-row', "els => els.map(e => e.innerText)")
    assert rows and not any("Blocked" in r and "left" in r for r in rows)
    assert page.locator('[data-card="limits"] .lim-row .meter i').first.evaluate("e => e.style.width") != ""


def test_a_hidden_card_is_not_asked_for(shell, page, harness):
    harness.db.set_setting("dash.hidden", json.dumps(["glance", "visits"]))
    open_dash(shell, page)
    assert order(page) == [k for k in ALL_CARDS if k not in ("glance", "visits")]
    assert page.locator('[data-card="glance"]').count() == 0


# ---------------------------------------------------------------- customise: hide, move, show

def test_hide_and_move_a_card_persist_after_a_reload(shell, page, harness):
    open_dash(shell, page)
    page.click('[data-k="customise"]')
    assert page.evaluate("document.querySelector('.dash').classList.contains('dash-edit')")
    page.click('[data-card="glance"] [data-hide="glance"]')
    page.click('[data-card="coming"] [data-move="-1"]')                            # coming before limits
    assert order(page)[:4] == ["stats", "today", "coming", "limits"] and "glance" not in order(page)
    for _ in range(50):                                                            # (the save runs in Python a moment later)
        if stored(harness, "dash.hidden") == ["glance"]:
            break
        page.wait_for_timeout(50)
    assert stored(harness, "dash.hidden") == ["glance"]
    assert stored(harness, "dash.order")[:4] == ["stats", "today", "coming", "limits"]
    page.reload()
    page.wait_for_selector('[data-stat="screen"] .v', timeout=8000)
    assert order(page)[:4] == ["stats", "today", "coming", "limits"] and "glance" not in order(page)
    # and back: the hidden card is a pill in Customise; clicking it shows it (and asks for its data)
    page.click('[data-k="customise"]')
    page.click('[data-show="glance"]')
    page.wait_for_selector('[data-card="glance"] .gl-row', timeout=5000)
    assert "glance" in order(page) and stored(harness, "dash.hidden") == []


def test_the_display_dialog_saves_at_once_and_the_dashboard_follows(shell, page, harness):
    open_dash(shell, page)
    page.click('[data-k="gear"]')
    page.wait_for_selector("#dlg[open] .dsp-row")
    assert page.locator("#dlg .dsp-row").count() == 11
    page.uncheck('#dlg [data-card="today"] input')
    wait_for(page, "!document.querySelector('.dash [data-card=\"today\"]:not([hidden])')")
    assert stored(harness, "dash.hidden") == ["today"]
    page.click('#dlg [data-card="week"] [data-dsp="up"]')
    wait_for(page, "document.querySelector('.dash > .dash-item:not([hidden]):nth-child(3)').dataset.card === 'week'")
    page.click('#dlg [data-seg="tab"] [data-v="Apps"]')
    page.click('#dlg [data-seg="range"] [data-v="7 days"]')
    page.wait_for_timeout(300)
    assert harness.db.get_setting("screentime.tab") == "Apps" and harness.db.get_setting("screentime.range") == "7 days"
    assert "Calendar" not in page.inner_text("#dlg")
    page.click("#dlg .dlg-f .btn-primary")
    wait_for(page, "!document.getElementById('dlg').open")


# ---------------------------------------------------------------- live

def test_a_usage_tick_changes_the_number_in_place(shell, page, harness):
    open_dash(shell, page)
    before = page.inner_text('[data-stat="screen"] .v')
    note = page.inner_text('[data-stat="screen"] .d')
    page.evaluate("""() => { window.__box = document.querySelector('[data-stat="screen"]');
        window.__v = window.__box.querySelector('.v'); window.__card = document.querySelector('[data-card="stats"]'); }""")
    calls = harness.rpc_count("dashboard_get")
    page.evaluate("""() => { window.__muts = 0;      // (element nodes added or removed; a text node swapped for new text is fine)
        new MutationObserver((list) => { for (const m of list) for (const n of [...m.addedNodes, ...m.removedNodes]) if (n.nodeType === 1) window.__muts++; })
          .observe(document.querySelector('.dash'), {subtree: true, childList: true}); }""")
    page.evaluate("window.ld.push([['usage_tick', {active_sec: 99999}]])")
    wait_for(page, "document.querySelector('[data-stat=\"screen\"] .v').textContent === '27 h 46 m'")
    assert before != "27 h 46 m"
    assert page.evaluate("document.querySelector('[data-stat=\"screen\"]') === window.__box && "
                         "document.querySelector('[data-stat=\"screen\"] .v') === window.__v && "
                         "document.querySelector('[data-card=\"stats\"]') === window.__card")
    assert page.evaluate("window.__muts") == 0                                  # no node added or removed
    assert harness.rpc_count("dashboard_get") == calls
    assert "more than yesterday" in page.inner_text('[data-stat="screen"] .d') and page.inner_text('[data-stat="screen"] .d') != note


def test_a_status_push_asks_again_only_when_something_shown_changed(shell, page, harness):
    open_dash(shell, page)
    calls = harness.rpc_count("dashboard_get")
    page.evaluate("window.ld.push([['status', {clock_offset: 3.5}]])")
    page.wait_for_timeout(400)
    assert harness.rpc_count("dashboard_get") == calls
    harness.db.set_setting("antibypass.off_since", "2026-10-11 15:00:00")
    import antibypass
    antibypass.switch_off(harness.db, NOW)
    page.evaluate("window.ld.push([['status', {off: '2026-10-11 15:42:17'}]])")
    wait_for(page, "document.querySelector('[data-stat=\"blocked\"] .d').textContent.startsWith('Lockdown is off')")
    assert harness.rpc_count("dashboard_get") == calls + 1


def test_pause_card_counts_down_on_its_own_clock(shell, page, harness):
    pause.start(harness.db, NOW, 30, silent=False)
    open_dash(shell, page)
    wait_for(page, "document.querySelector('[data-card=\"pause\"] [data-n=\"paused\"]:not([hidden])')")
    text = page.inner_text('[data-card="pause"] [data-n="left"]')
    assert re.fullmatch(r"(29|30) min left · back on at 16:12", text), text
    calls = len(harness.calls)
    page.wait_for_timeout(2500)
    assert len(harness.calls) == calls                                          # the countdown asks nobody
    page.click('[data-card="pause"] [data-n="resume"]')
    wait_for(page, "document.querySelector('[data-card=\"pause\"] [data-n=\"on\"]:not([hidden])')")
    assert pause.state(harness.db, NOW) is None


def test_bedtime_card_shows_the_time_and_counts_to_it(shell, page, harness):
    import reminders
    reminders.save(harness.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00", "before": 15})
    open_dash(shell, page)
    wait_for(page, "document.querySelector('[data-card=\"bedtime\"] [data-n=\"on\"]:not([hidden])')")
    assert page.inner_text('[data-card="bedtime"] .big-time') == "23:00"
    assert page.inner_text('[data-card="bedtime"] [data-n="sub"]') == "in 7 h 18 m · heads-up 15 min before"


def test_the_emergency_card_has_a_button_that_opens_the_dialog_and_reloads_after(shell, page, harness):
    import emergency
    emergency.save_tiers(harness.db, [{"id": "a", "uses": 2, "per": "week", "minutes": 5}])
    open_dash(shell, page)
    use = page.locator('[data-card="emergency"] [data-n="use"]')
    assert use.is_visible() and use.inner_text() == "Use emergency unlock"
    assert page.inner_text('[data-card="emergency"] [data-n="text"]') == "2 of 2 left this week"
    calls = harness.rpc_count("dashboard_get")
    use.click()
    page.wait_for_selector("#dlg[open] .em-kind", timeout=5000)
    assert page.inner_text("#dlg h2") == "Emergency unlock"
    page.locator('.em-list .vl-row:not([hidden]):has(.name:text-is("Twitch")) input').first.check()
    button = page.locator("#dlg .dlg-f .btn-primary")
    button.click()
    button.click()
    wait_for(page, "!document.getElementById('dlg').open")
    wait_for(page, "document.querySelector('[data-card=\"emergency\"] [data-n=\"text\"]').textContent === '1 of 2 left this week'")
    assert harness.rpc_count("dashboard_get") == calls + 1                    # the page asked again once


def test_the_emergency_button_is_not_there_without_a_use_left_or_when_it_is_off(shell, page, harness):
    import emergency
    emergency.save_tiers(harness.db, [{"id": "a", "uses": 1, "per": "week", "minutes": 5}])
    emergency.unlock(harness.db, [], NOW, alerts=True, tier="a")
    open_dash(shell, page)
    assert page.locator('[data-card="emergency"] [data-n="use"]').is_hidden()
    assert page.inner_text('[data-card="emergency"] [data-n="text"]') == "0 of 1 left this week"
    harness.db.set_setting("emergency.enabled", "0")
    page.evaluate("window.ld.push([['mode_changed', {}]])")
    wait_for(page, "!document.querySelector('[data-card=\"emergency\"] [data-n=\"off\"]').hidden")
    assert page.locator('[data-card="emergency"] [data-n="use"]').is_hidden()


def test_the_dashboard_does_not_reload_on_a_timer(shell, page, harness):
    open_dash(shell, page)
    calls = len(harness.calls)
    page.evaluate("window.ld.push([['usage_tick', {active_sec: 1234}]])")
    page.wait_for_timeout(3500)                                             # (the old window reloaded every 30 s; ticks only patch)
    assert len(harness.calls) == calls
    assert page.evaluate("import('/js/pages/dashboard.js').then(m => 'REFRESH_SEC' in m)") is False


# ---------------------------------------------------------------- empty state

def test_a_fresh_install_shows_the_empty_state_and_its_two_buttons(shell, page, harness):
    harness.db.write("DELETE FROM activity")
    harness.db.write("DELETE FROM daily_activity")
    page.route("**/js/pages/index.js", lambda route: route.fulfill(     # (only this page is real: the buttons lead to stubs)
        status=200, content_type="text/javascript", body='export const pages = {dashboard: () => import("./dashboard.js")};'))
    shell(route="dashboard")
    page.wait_for_selector('[data-k="empty"]:not([hidden])', timeout=8000)
    assert "Tracking started" in page.inner_text('[data-k="empty"]')
    assert page.locator(".dash").is_hidden()
    page.click('[data-k="categories"]')
    wait_for(page, "location.hash === '#screentime'")
    page.click('[data-nav="dashboard"]')
    page.click('[data-k="first-block"]')
    wait_for(page, "location.hash === '#blocking'")


# ---------------------------------------------------------------- speed, hidden window

def test_the_first_visit_and_a_second_look_are_fast(shell, page, harness, perf, capsys):
    shell(route="about")
    page.wait_for_selector(".about-hero", timeout=8000)
    first = perf.click_until('[data-nav="dashboard"]', '[data-stat="screen"] .v')
    page.wait_for_function("document.querySelector('[data-stat=\"screen\"] .v').textContent.length > 0")
    page.click('[data-nav="about"]')
    page.wait_for_timeout(200)
    again = perf.click_to_frame('[data-nav="dashboard"]')
    with capsys.disabled():
        print(f"\n  dashboard: first visit {first:.0f} ms, shown again {again:.0f} ms")
    assert 0 < first < 300 and again < 100


def test_no_timer_runs_while_the_window_is_hidden(shell, page, harness, perf):
    open_dash(shell, page)
    perf.set_hidden(True)
    page.wait_for_timeout(300)
    perf.reset_timer_calls()
    n = len(harness.calls)
    page.wait_for_timeout(2200)
    assert perf.timer_calls()["timeout"] == 0 and len(harness.calls) == n
    perf.set_hidden(False)
    page.wait_for_timeout(1200)
    assert perf.timer_calls()["timeout"] >= 1                                   # the clock runs again
