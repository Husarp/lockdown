"""R27: the Notifications page (js/pages/notifications.js) in Chrome against the real Python Api (see harness.py).

What is checked: every part of the real page is there (7 reasons in a 4-column grid, the Messages section, cooldown, Show as,
Upcoming Blocks, Weekly Summary, the warning for the app in front), a change reaches Python through the draft and shows in the save bar, typing in a message box
makes no Api call until the 400 ms debounce / the box is left, nothing reloads the Blocking page, "remind me again every" is
parsed by Python (an error puts the old text back), the weekly summary is saved directly, Discard puts the saved values back,
and the console stays clean."""
import time

import pytest

import alerts


def open_page(shell, page):
    shell(route="notifications")
    page.wait_for_selector('[data-card="digest"]', timeout=8000)
    page.wait_for_function("document.querySelectorAll('.nt-cell').length === 7")
    return page


def saved(harness, key):
    return harness.db.get_setting(key)


def eventually(check, seconds=4):
    deadline = time.time() + seconds
    while not check() and time.time() < deadline:
        time.sleep(0.03)
    return check()


def calls(harness, name):
    return [a for _t, n, a in harness.calls if n == name]


# ---------------------------------------------------------------- the page

def test_the_page_shows_every_part(shell, page, harness):
    open_page(shell, page)
    heads = page.eval_on_selector_all(".nt .card-h h3", "els => els.map(e => e.textContent)")
    assert heads == ["Blocked Visit Alerts", "Upcoming Blocks", "Weekly Summary", "The app in front"]
    cells = page.eval_on_selector_all(".nt-cell span", "els => els.map(e => e.textContent)")
    assert cells == [label for label, _ in alerts.REASONS.values()]
    # a 4-column grid: the first four cells share a row, the next three the second
    tops = page.eval_on_selector_all(".nt-cell", "els => els.map(e => Math.round(e.getBoundingClientRect().top))")
    assert len(set(tops[:4])) == 1 and len(set(tops[4:])) == 1 and tops[4] > tops[0]
    for label in ("Don't repeat for the same site within", "Show as", "Warn me", "While I'm using it, remind me again every",
                  "Notify me when a block starts", "Every", "Per-site override", "Send a test",
                  "Warn before the app in front gets blocked - even in games and muting modes", "Minutes before",
                  "Play a short sound"):
        assert label in page.inner_text(".nt"), label
    assert page.locator('[data-seg="nfmt"] button').all_inner_texts() == [
        "Windows notifications (recommended)", "Lockdown pop-ups", "Both"]
    assert page.locator('[data-seg="nfmt"] [aria-pressed="true"]').inner_text().startswith("Windows")
    assert page.input_value('[data-k="cooldown"]') == "30"
    assert page.input_value('[data-k="repeat"]') == "Off"
    assert page.is_checked('[data-k="front-on"]') and page.is_checked('[data-k="front-sound"]')
    assert page.input_value('[data-k="front-steps"]') == "1"
    assert page.input_value('[data-k="dg-day"]') == "6" and page.input_value('[data-k="dg-time"]') == "19:00"
    assert page.locator(".nt-cell input:checked").count() == 7


def test_a_ticked_cell_gets_the_accent_edge(shell, page, harness):
    open_page(shell, page)
    on = page.eval_on_selector(".nt-cell", "e => getComputedStyle(e).borderTopColor")
    page.click('[data-reason="permanent"] ')
    page.wait_for_function("!document.querySelector('[data-reason=permanent]:not(button)').checked")
    off = page.eval_on_selector(".nt-cell", "e => getComputedStyle(e).borderTopColor")
    assert on != off


def test_messages_open_with_reset_and_the_placeholders_hint(shell, page, harness):
    open_page(shell, page)
    assert page.locator('[data-k="msgs"]').is_hidden()
    page.click('[data-act="messages"]')
    assert page.locator('[data-k="msgs"]').is_visible()
    assert page.locator(".nt-msg input").count() == 7 and page.locator('[data-act="reset-msg"]').count() == 7
    assert "{site} {reason} {until}" in page.inner_text('[data-k="msgs"]')
    assert page.input_value('[data-msg="schedule"]') == alerts.DEFAULT_MESSAGES["schedule"]
    page.click('[data-act="messages"]')
    assert page.locator('[data-k="msgs"]').is_hidden()


# ---------------------------------------------------------------- the draft

def test_a_switch_reaches_python_through_the_draft(shell, page, harness):
    open_page(shell, page)
    before = len(calls(harness, "draft_set_setting"))
    page.click('[data-reason="mode"]:not(button)')
    assert eventually(lambda: saved(harness, "notify.enabled.mode") == "0")
    assert calls(harness, "draft_set_setting")[before:] == [["notify.enabled.mode", "0"]]
    assert page.locator(".nt-cell input:checked").count() == 6


def test_cooldown_show_as_started_and_warn_switches_save(shell, page, harness):
    open_page(shell, page)
    page.select_option('[data-k="cooldown"]', "15")
    assert eventually(lambda: saved(harness, "notify.cooldown_min") == "15")
    page.click('[data-seg="nfmt"] [data-v="both"]')
    assert eventually(lambda: saved(harness, "notify.format") == "both")
    page.click('[data-k="started"]')
    assert eventually(lambda: saved(harness, "notify.started.enabled") == "0")
    page.click('[data-k="warn-on"]')
    assert eventually(lambda: saved(harness, "notify.warn.enabled") == "0")


def test_the_minutes_before_use_the_stepper_and_stay_between_1_and_60(shell, page, harness):
    open_page(shell, page)
    page.click('[data-card="warn"] [data-act="step"][data-d="1"]')
    assert eventually(lambda: saved(harness, "notify.warn.minutes") == "6")
    page.fill('[data-card="warn"] .stepper input', "99")
    page.press('[data-card="warn"] .stepper input', "Tab")
    assert eventually(lambda: saved(harness, "notify.warn.minutes") == "60")
    assert page.input_value('[data-card="warn"] .stepper input') == "60"


def test_typing_in_a_message_box_makes_no_call_until_the_debounce_or_blur(shell, page, harness):
    open_page(shell, page)
    page.click('[data-act="messages"]')
    base = len(calls(harness, "draft_set_setting"))
    page.click('[data-msg="permanent"]')
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type("Nope: {site}", delay=15)
    assert len(calls(harness, "draft_set_setting")) == base                     # still typing: nothing sent
    assert saved(harness, "notify.msg.permanent") is None
    page.wait_for_timeout(700)                                                   # 400 ms after the last key
    sent = calls(harness, "draft_set_setting")[base:]
    assert sent == [["notify.msg.permanent", "Nope: {site}"]]
    assert eventually(lambda: saved(harness, "notify.msg.permanent") == "Nope: {site}")


def test_leaving_the_box_sends_at_once(shell, page, harness):
    open_page(shell, page)
    page.click('[data-act="messages"]')
    page.fill('[data-msg="schedule"]', "Back at {until}")
    page.press('[data-msg="schedule"]', "Tab")
    assert eventually(lambda: saved(harness, "notify.msg.schedule") == "Back at {until}")


def test_reset_puts_the_default_message_back(shell, page, harness):
    open_page(shell, page)
    page.click('[data-act="messages"]')
    page.fill('[data-msg="limit"]', "custom")
    page.press('[data-msg="limit"]', "Tab")
    assert eventually(lambda: saved(harness, "notify.msg.limit") == "custom")
    page.click('[data-act="reset-msg"][data-reason="limit"]')
    assert page.input_value('[data-msg="limit"]') == alerts.DEFAULT_MESSAGES["limit"]
    assert eventually(lambda: saved(harness, "notify.msg.limit") == alerts.DEFAULT_MESSAGES["limit"])


def test_an_empty_message_box_means_the_default(shell, page, harness):
    open_page(shell, page)
    page.click('[data-act="messages"]')
    page.fill('[data-msg="mode"]', "")
    page.press('[data-msg="mode"]', "Tab")
    assert page.input_value('[data-msg="mode"]') == alerts.DEFAULT_MESSAGES["mode"]


def test_the_blocking_page_is_never_reloaded_by_a_notification_setting(shell, page, harness):
    shell(route="blocking")
    page.wait_for_selector('[data-pane="overview"]', timeout=8000)
    page.wait_for_timeout(300)
    page.click('#nav [data-nav="notifications"]')
    page.wait_for_function("document.querySelectorAll('.nt-cell').length === 7")
    before = harness.rpc_count("blocking_overview_get")
    page.select_option('[data-k="cooldown"]', "5")
    assert eventually(lambda: saved(harness, "notify.cooldown_min") == "5")
    page.click('#nav [data-nav="blocking"]')
    page.wait_for_timeout(500)
    assert harness.rpc_count("blocking_overview_get") == before


def test_with_autosave_off_the_save_bar_says_what_waits_and_discard_puts_it_back(shell, page, harness):
    open_page(shell, page)
    page.click("#sb-auto")
    page.wait_for_function("!document.getElementById('sb-auto').checked")
    page.select_option('[data-k="cooldown"]', "60")
    page.wait_for_function("document.getElementById('sb-text').textContent.includes('Notification settings')")
    assert saved(harness, "notify.cooldown_min") is None
    page.click("#sb-discard")
    page.wait_for_function("document.querySelector('[data-k=cooldown]').value === '30'")
    assert page.inner_text("#sb-text") == ""
    page.select_option('[data-k="cooldown"]', "60")
    page.wait_for_function("!document.getElementById('sb-save').disabled")
    page.click("#sb-save")
    assert eventually(lambda: saved(harness, "notify.cooldown_min") == "60")


def test_pending_values_are_still_there_when_the_page_is_shown_again(shell, page, harness):
    open_page(shell, page)
    page.click("#sb-auto")
    page.wait_for_function("!document.getElementById('sb-auto').checked")
    page.select_option('[data-k="cooldown"]', "5")
    page.wait_for_function("document.getElementById('sb-text').textContent.includes('Notification settings')")
    page.click('#nav [data-nav="dashboard"]')
    page.wait_for_timeout(200)
    page.click('#nav [data-nav="notifications"]')
    page.wait_for_timeout(500)
    assert page.input_value('[data-k="cooldown"]') == "5"


def test_leaving_the_page_sends_a_setting_that_is_still_waiting(shell, page, harness):
    open_page(shell, page)
    page.click('[data-act="messages"]')
    page.click('[data-msg="started"]') if page.locator('[data-msg="started"]').count() else page.click('[data-msg="temporary"]')
    page.keyboard.type("bye", delay=10)
    page.click('#nav [data-nav="dashboard"]')                                  # (well inside the 400 ms)
    assert eventually(lambda: str(saved(harness, "notify.msg.temporary")).endswith("bye"))


# ---------------------------------------------------------------- remind me again every

def test_the_repeat_box_is_parsed_by_python(shell, page, harness):
    open_page(shell, page)
    page.fill('[data-k="repeat"]', "45s")
    page.press('[data-k="repeat"]', "Enter")
    assert eventually(lambda: saved(harness, "notify.warn.repeat_sec") == "45")
    assert page.input_value('[data-k="repeat"]') == "45s"
    page.fill('[data-k="repeat"]', "2 min")
    page.press('[data-k="repeat"]', "Tab")
    assert eventually(lambda: saved(harness, "notify.warn.repeat_sec") == "120")
    assert page.input_value('[data-k="repeat"]') == "2 min"
    page.fill('[data-k="repeat"]', "Off")
    page.press('[data-k="repeat"]', "Tab")
    assert eventually(lambda: saved(harness, "notify.warn.repeat_sec") == "0")


def test_a_slow_answer_never_overwrites_what_is_typed_next(shell, page, harness):
    """The first answer comes back late (after the owner already typed the next value): the box keeps what is typed, and a
    bad value afterwards goes back to the value that really is saved."""
    real = harness.rpc
    state = {"slow": True}

    def slow_rpc(name, args):
        reply = real(name, args)
        if name == "notify_repeat_set" and state["slow"]:
            state["slow"] = False
            time.sleep(0.8)                                  # (saved, but the page hears it late)
        return reply

    harness.rpc = slow_rpc
    open_page(shell, page)
    page.fill('[data-k="repeat"]', "2 min")
    page.press('[data-k="repeat"]', "Tab")
    assert eventually(lambda: saved(harness, "notify.warn.repeat_sec") == "120")
    page.fill('[data-k="repeat"]', "Off")                    # (the answer for "2 min" is still on its way)
    page.wait_for_timeout(1000)                              # (it arrives while the box is being typed in)
    assert page.input_value('[data-k="repeat"]') == "Off"
    page.press('[data-k="repeat"]', "Tab")
    assert eventually(lambda: saved(harness, "notify.warn.repeat_sec") == "0")


def test_a_bad_repeat_goes_back_to_the_last_value_with_pythons_words(shell, page, harness):
    open_page(shell, page)
    page.fill('[data-k="repeat"]', "30s")
    page.press('[data-k="repeat"]', "Tab")
    assert eventually(lambda: saved(harness, "notify.warn.repeat_sec") == "30")
    page.wait_for_function("document.querySelector('[data-k=repeat]').value === '30s'")
    page.fill('[data-k="repeat"]', "soonish")
    page.press('[data-k="repeat"]', "Tab")
    page.wait_for_function("document.querySelector('[data-k=repeat-err]').textContent.length > 0")
    assert page.input_value('[data-k="repeat"]') == "30s"
    assert saved(harness, "notify.warn.repeat_sec") == "30"


# ---------------------------------------------------------------- the warning for the app in front

def test_the_front_switches_save_through_the_draft(shell, page, harness):
    open_page(shell, page)
    before = len(calls(harness, "draft_set_setting"))
    page.click('[data-k="front-on"]')
    assert eventually(lambda: saved(harness, "notify.front.enabled") == "0")
    page.click('[data-k="front-sound"]')
    assert eventually(lambda: saved(harness, "notify.front.sound") == "0")
    assert calls(harness, "draft_set_setting")[before:] == [["notify.front.enabled", "0"], ["notify.front.sound", "0"]]
    page.click('[data-k="front-on"]')
    assert eventually(lambda: saved(harness, "notify.front.enabled") == "1")


def test_the_front_steps_are_parsed_by_python(shell, page, harness):
    open_page(shell, page)
    page.fill('[data-k="front-steps"]', "1,5")
    page.press('[data-k="front-steps"]', "Enter")
    assert eventually(lambda: saved(harness, "notify.front.steps") == "5, 1")
    page.wait_for_function("document.querySelector('[data-k=front-steps]').value === '5, 1'")
    assert calls(harness, "notify_front_steps_set") == [["1,5"]]
    page.fill('[data-k="front-steps"]', "10 min")
    page.press('[data-k="front-steps"]', "Tab")
    assert eventually(lambda: saved(harness, "notify.front.steps") == "10")
    assert page.input_value('[data-k="front-steps"]') == "10"


def test_bad_front_steps_go_back_to_the_last_value_with_pythons_words(shell, page, harness):
    open_page(shell, page)
    page.fill('[data-k="front-steps"]', "5, 1")
    page.press('[data-k="front-steps"]', "Tab")
    assert eventually(lambda: saved(harness, "notify.front.steps") == "5, 1")
    for bad in ("0", "45", "1, 2, 3, 4", "soon"):
        page.fill('[data-k="front-steps"]', bad)
        page.press('[data-k="front-steps"]', "Tab")
        page.wait_for_function("document.querySelector('[data-k=front-steps]').value === '5, 1'")
        assert "minutes" in page.inner_text('[data-k="front-steps-err"]')
    assert saved(harness, "notify.front.steps") == "5, 1"
    page.fill('[data-k="front-steps"]', "2")                          # a good value clears the error
    page.press('[data-k="front-steps"]', "Tab")
    page.wait_for_function("document.querySelector('[data-k=front-steps-err]').textContent === ''")
    assert eventually(lambda: saved(harness, "notify.front.steps") == "2")


def test_the_front_steps_show_what_is_saved_and_the_help_opens(shell, page, harness):
    harness.db.set_setting("notify.front.steps", "15, 5, 1")
    harness.db.set_setting("notify.front.enabled", "0")
    open_page(shell, page)
    assert page.input_value('[data-k="front-steps"]') == "15, 5, 1" and not page.is_checked('[data-k="front-on"]')
    assert page.locator('[data-helptext="front"]').is_hidden()
    page.click('[data-act="help"][data-help="front"]')
    assert "true full screen" in page.inner_text('[data-helptext="front"]')


# ---------------------------------------------------------------- weekly summary and the test button

def test_the_weekly_summary_is_saved_directly_without_a_draft(shell, page, harness):
    open_page(shell, page)
    page.click("#sb-auto")
    page.wait_for_function("!document.getElementById('sb-auto').checked")       # (autosave off: a draft would wait)
    page.select_option('[data-k="dg-day"]', "2")
    assert eventually(lambda: saved(harness, "digest.day") == "2")
    page.select_option('[data-k="dg-time"]', "08:00")
    assert eventually(lambda: saved(harness, "digest.time") == "08:00")
    page.click('[data-k="dg-on"]')
    assert eventually(lambda: saved(harness, "digest.enabled") == "0")
    assert page.inner_text("#sb-text") == ""                                    # nothing waits in the save bar


def test_send_a_test_toasts_the_result(shell, page, harness):
    from types import SimpleNamespace
    harness.ctx.hub = SimpleNamespace(notify=lambda message: True)
    open_page(shell, page)
    page.click('[data-act="test"]')
    page.wait_for_function("document.getElementById('toast').textContent.includes('Test notice sent')")
    harness.ctx.hub = SimpleNamespace(notify=lambda message: False)
    page.click('[data-act="test"]')
    page.wait_for_function("document.getElementById('toast').textContent.includes('Held back')")


# ---------------------------------------------------------------- looks

@pytest.mark.parametrize("theme", ["light", "dark", "amoled"])
def test_clean_console_in_every_theme(shell, page, harness, theme):
    harness.db.set_setting("ui.theme", theme)
    open_page(shell, page)
    page.click('[data-act="messages"]')
    assert page.evaluate("document.documentElement.dataset.theme") == theme
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_it_fits_a_small_window(shell, page, harness):
    shell(width=1000, height=650, route="notifications")
    page.wait_for_function("document.querySelectorAll('.nt-cell').length === 7", timeout=8000)
    assert page.evaluate("document.getElementById('view').scrollWidth <= document.getElementById('view').clientWidth")
