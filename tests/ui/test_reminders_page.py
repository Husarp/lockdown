"""R26: the Reminders page, headless in Chrome against the real Python Api (see harness.py)."""
import reminders
import antibypass as ab


def open_page(shell, page):
    shell(route="reminders")
    page.wait_for_selector('[data-card="own"]', timeout=8000)
    page.wait_for_function("document.querySelector('[data-k=\"b-today\"]').textContent.startsWith('Breaks today')")
    return page


def sleep_saved(harness):
    return reminders.load(harness.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)


def break_saved(harness):
    return reminders.load(harness.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)


def customs(harness):
    return reminders.custom_list(harness.db)


def wait_for(page, js, timeout=5000):
    page.wait_for_function(js, timeout=timeout)


def lock(harness):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "length": 30})


def new_reminder(page, text, **kw):
    page.click('[data-act="new"]')
    page.wait_for_selector(".rem-ed")
    page.fill('.rem-ed [data-k="text"]', text)
    return page


def save_editor(page):
    page.click("#dlg .dlg-f .btn-primary")


# ---------------------------------------------------------------- the page

def test_the_page_shows_every_part(shell, page, harness):
    open_page(shell, page)
    titles = page.eval_on_selector_all(".rem .card-h h3", "els => els.map(e => e.textContent)")
    assert titles == ["Sleep", "Breaks", "Your reminders"]
    for label in ("Remind me to go to bed", "Important", "Bedtime", "Heads-up", "Comes back", "Dismiss wait",
                  "Turn on a mode", "Heads-up says", "Bedtime says", "Remind me to take breaks", "After", "Snooze",
                  "Break length", "It says", "Strict break", "Snoozes first", "20-20-20", "No reminders yet"):
        assert page.locator(f".rem :text('{label}')").count() >= 1, label
    assert page.inner_text('[data-k="b-today"]') == "Breaks today: 0"
    assert page.input_value('[data-k="s-before"]') == "30 min"
    assert page.input_value('[data-k="b-every"]') == "45 min"
    assert page.locator("[data-tier]").count() == 3
    assert page.locator('[data-k="empty"]').is_visible()
    assert page.get_attribute('[data-k="s-warn"]', "placeholder") == reminders.SLEEP_WARN_TEXT


# ---------------------------------------------------------------- Sleep

def test_sleep_times_heads_up_and_text_are_saved(shell, page, harness):
    open_page(shell, page)
    page.fill('[data-k="s-bed"] .tp-h', "22")
    page.fill('[data-k="s-bed"] .tp-m', "45")
    page.fill('[data-k="s-before"]', "45s")
    page.fill('[data-k="s-text"]', "Sleep tight")
    page.click('[data-act="save-times"]')
    wait_for(page, "document.querySelector('[data-k=\"s-before\"]').value === '45s'")
    s = sleep_saved(harness)
    assert s["bedtime"] == "22:45" and s["before"] == 0.75 and s["text"] == "Sleep tight"


def test_a_step_time_is_a_compact_hh_mm_field(shell, page, harness):
    open_page(shell, page)
    box = lambda sel: page.locator(sel).first.bounding_box()
    h, m = box("[data-tier] .tp-h"), box("[data-tier] .tp-m")
    assert h["width"] <= 30 and m["width"] <= 30
    assert m["x"] - (h["x"] + h["width"]) < 14


def test_sleep_steps_can_be_added_and_removed(shell, page, harness):
    open_page(shell, page)
    assert len(sleep_saved(harness)["tiers"]) == 3
    page.click('[data-act="tier-add"]')
    wait_for(page, "document.querySelectorAll('[data-tier]').length === 4")
    page.wait_for_timeout(300)
    assert len(sleep_saved(harness)["tiers"]) == 4
    page.locator('[data-tier] [data-act="tier-x"]').first.click()
    page.wait_for_timeout(300)
    assert len(sleep_saved(harness)["tiers"]) == 3
    assert page.locator("[data-tier]").count() == 3
    # a step in seconds, with its own time
    page.locator('[data-tier] input.in-step').first.fill("30s")
    page.locator('[data-tier] input.in-step').first.press("Enter")
    page.wait_for_timeout(300)
    assert 30 in [t["every_sec"] for t in sleep_saved(harness)["tiers"]]


def test_a_step_under_five_seconds_shows_the_error_and_saves_nothing(shell, page, harness):
    open_page(shell, page)
    before = sleep_saved(harness)["tiers"]
    page.locator('[data-tier] input.in-step').first.fill("2s")
    page.locator('[data-tier] input.in-step').first.press("Enter")
    page.wait_for_function("document.querySelector('[data-k=\"s-err\"]').textContent.length > 0")
    assert "5s" in page.inner_text('[data-k="s-err"]')
    assert sleep_saved(harness)["tiers"] == before


def test_dismiss_wait_quick_picks_and_any_seconds(shell, page, harness):
    open_page(shell, page)
    page.click('.seg[data-seg="wait"] button[data-v="30"]')
    page.wait_for_timeout(300)
    assert sleep_saved(harness)["dismiss_wait"] == 30
    assert page.input_value('[data-k="s-wait"]') == "30"
    page.fill('[data-k="s-wait"]', "90")
    page.press('[data-k="s-wait"]', "Enter")
    page.wait_for_timeout(300)
    assert sleep_saved(harness)["dismiss_wait"] == 90
    page.fill('[data-k="s-wait"]', "700")
    page.press('[data-k="s-wait"]', "Enter")
    page.wait_for_function("document.querySelector('[data-k=\"s-err\"]').textContent.includes('600')")
    assert sleep_saved(harness)["dismiss_wait"] == 90


def test_sleep_switch_and_important(shell, page, harness):
    open_page(shell, page)
    page.click('[data-k="s-on"]')
    page.wait_for_timeout(300)
    assert sleep_saved(harness)["on"] is True
    page.click('[data-k="s-guarded"]')
    page.wait_for_timeout(300)
    assert sleep_saved(harness)["guarded"] is True


def test_turning_an_important_sleep_off_while_locked_asks_for_the_challenge(shell, page, harness):
    reminders.save(harness.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "guarded": True})
    lock(harness)
    open_page(shell, page)
    assert page.is_checked('[data-k="s-on"]')
    page.click('[data-k="s-on"]')
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "important" in page.inner_text("#dlg").lower()
    assert sleep_saved(harness)["on"] is True
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#dlg').open")
    wait_for(page, "document.querySelector('[data-k=\"s-on\"]').checked === true")      # the switch snapped back
    assert sleep_saved(harness)["on"] is True


def test_grey_screen_during_bedtime_is_a_switch_on_the_sleep_card(shell, page, harness):
    open_page(shell, page)
    assert page.locator(".rem :text('Grey screen during bedtime')").count() >= 1
    assert not page.is_checked('[data-k="s-grey"]') and sleep_saved(harness)["grey"] is False
    text = page.inner_text('[data-card="sleep"]')
    assert "Color filters" in text and "separate" in text                         # (Windows' own colour filters)
    page.click('[data-k="s-grey"]')                                                # on: free
    wait_for(page, "document.querySelector('[data-k=\"s-grey\"]').checked === true")
    assert sleep_saved(harness)["grey"] is True and not page.locator("#dlg[open]").count()
    page.click('[data-k="s-grey"]')                                                # off again: bedtime is not Important, free
    page.wait_for_timeout(300)
    assert sleep_saved(harness)["grey"] is False and not page.locator("#dlg[open]").count()


def test_turning_the_bedtime_grey_off_while_important_and_locked_asks_for_the_challenge(shell, page, harness):
    reminders.save(harness.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "guarded": True, "grey": True})
    lock(harness)
    open_page(shell, page)
    assert page.is_checked('[data-k="s-grey"]')
    page.click('[data-k="s-grey"]')
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "grey screen at bedtime" in page.inner_text("#dlg").lower()
    assert sleep_saved(harness)["grey"] is True
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#dlg').open")
    wait_for(page, "document.querySelector('[data-k=\"s-grey\"]').checked === true")      # the switch snapped back
    assert sleep_saved(harness)["grey"] is True


# ---------------------------------------------------------------- Breaks

def test_break_seconds_boxes_and_options_are_saved(shell, page, harness):
    open_page(shell, page)
    page.fill('[data-k="b-every"]', "30s")
    page.press('[data-k="b-every"]', "Enter")
    page.wait_for_timeout(300)
    page.fill('[data-k="b-snooze"]', "1h30")
    page.press('[data-k="b-snooze"]', "Enter")
    page.select_option('[data-k="b-length"]', "10")
    page.select_option('[data-k="b-max"]', "5")
    page.click('[data-k="b-strict"]')
    page.click('[data-k="b-twenty"]')
    page.fill('[data-k="b-text"]', "Rest {length}")
    page.press('[data-k="b-text"]', "Enter")
    page.wait_for_timeout(500)
    b = break_saved(harness)
    assert (b["every_sec"], b["snooze_sec"], b["length"], b["max_snooze"]) == (30, 5400, 10, 5)
    assert b["strict"] and b["twenty"] and b["text"] == "Rest {length}"


def test_the_20_20_20_says_text_is_saved_and_shown_again(shell, page, harness):
    open_page(shell, page)
    page.fill('[data-k="b-twenty-text"]', "Look at the trees")
    page.press('[data-k="b-twenty-text"]', "Enter")
    page.wait_for_timeout(500)
    assert break_saved(harness)["twenty_text"] == "Look at the trees"
    shell(route="about")
    open_page(shell, page)
    assert page.input_value('[data-k="b-twenty-text"]') == "Look at the trees"


def test_break_under_five_seconds_is_refused(shell, page, harness):
    open_page(shell, page)
    page.fill('[data-k="b-every"]', "4s")
    page.press('[data-k="b-every"]', "Enter")
    page.wait_for_function("document.querySelector('[data-k=\"b-err\"]').textContent.length > 0")
    assert break_saved(harness)["every_sec"] == 45 * 60


def allow_rows(page):
    return page.eval_on_selector_all('[data-k="allow"] [data-exe]', "els => els.map(e => e.dataset.exe)")


def test_allowed_during_breaks_shows_the_default_and_removing_is_free(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    assert allow_rows(page) == ["lockdown.exe", "systemsettings.exe"]
    assert "Windows Settings" in page.inner_text('[data-k="allow"]')
    assert page.locator('[data-act="allow-restore"]').is_hidden()
    page.locator('[data-exe="systemsettings.exe"] [data-act="allow-x"]').click()     # free, even while locked
    wait_for(page, "document.querySelectorAll('[data-k=\"allow\"] [data-exe]').length === 1")
    assert reminders.allowed_apps(break_saved(harness)) == ["lockdown.exe"]
    assert page.locator('[data-act="allow-restore"]').is_visible()
    assert not page.is_visible("#dlg[open]")


def test_bringing_a_default_back_asks_for_the_challenge_when_locked(shell, page, harness):
    reminders.save(harness.db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "allow": ["lockdown.exe"]})
    lock(harness)
    open_page(shell, page)
    page.click('[data-act="allow-restore"]')
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "Windows Settings" in page.inner_text("#dlg")
    assert reminders.allowed_apps(break_saved(harness)) == ["lockdown.exe"]
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#dlg').open")
    assert reminders.allowed_apps(break_saved(harness)) == ["lockdown.exe"]


def test_bringing_a_default_back_is_immediate_without_a_challenge(shell, page, harness):
    reminders.save(harness.db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "allow": []})
    open_page(shell, page)
    assert allow_rows(page) == [] and "Nothing" in page.inner_text('[data-k="allow"]')
    page.click('[data-act="allow-restore"]')
    wait_for(page, "document.querySelectorAll('[data-k=\"allow\"] [data-exe]').length === 2")
    assert reminders.allowed_apps(break_saved(harness)) == ["lockdown.exe", "systemsettings.exe"]


# ---------------------------------------------------------------- Your reminders

def test_add_edit_switch_and_delete_a_reminder(shell, page, harness):
    open_page(shell, page)
    new_reminder(page, "Drink water")
    page.fill('.rem-ed [data-k="every"]', "30s")
    save_editor(page)
    page.wait_for_selector(".rem [data-id]")
    row = page.locator(".rem [data-id]").first
    assert row.locator(".t").inner_text() == "Drink water"
    assert row.locator(".sub").inner_text() == "every 30s of use · done 0x in 7 days"
    assert not page.locator('[data-k="empty"]').is_visible()
    r = customs(harness)[0]
    assert r["every_sec"] == 30 and r["on"] is True and len(r["id"]) == 8
    # switch off
    row.locator("input[data-row-switch]").click()
    page.wait_for_timeout(300)
    assert customs(harness)[0]["on"] is False
    # edit: kind -> Times; editing keeps `on`
    row.locator('[data-act="edit"]').click()
    page.wait_for_selector(".rem-ed")
    assert page.input_value('.rem-ed [data-k="text"]') == "Drink water"
    page.click('.rem-ed .seg button[data-v="times"]')
    assert page.is_visible('.rem-ed [data-panel="times"]') and not page.is_visible('.rem-ed [data-panel="interval"]')
    page.click('.rem-ed [data-act="time-add"]')
    assert page.locator(".rem-ed [data-time]").count() == 2
    page.locator(".rem-ed [data-time] .tp-h").last.fill("18")
    page.click('.rem-ed .seg button[data-v="random"]')
    assert page.is_visible('.rem-ed [data-panel="random"]')
    page.click('.rem-ed .seg button[data-v="times"]')
    save_editor(page)
    page.wait_for_function("document.querySelector('.rem [data-id] .sub').textContent.includes('at 12:00, 18:00')")
    r = customs(harness)[0]
    assert r["kind"] == "times" and r["times"] == ["12:00", "18:00"] and r["on"] is False
    # delete: two clicks
    del_btn = page.locator(".rem [data-id] .confirm")
    del_btn.click()
    assert customs(harness)
    del_btn.click()
    page.wait_for_function("!document.querySelector('.rem [data-id]')")
    assert customs(harness) == [] and page.locator('[data-k="empty"]').is_visible()


def test_editor_options_days_and_quotes(shell, page, harness):
    open_page(shell, page)
    new_reminder(page, "Pull-ups")
    page.click('.rem-ed .seg button[data-v="random"]')
    page.locator(".rem-ed [data-range=random] .tp-h").first.fill("09")
    page.locator(".rem-ed [data-range=random] .tp-h").last.fill("20")
    page.click('.rem-ed .days button:nth-child(6)')
    page.click('.rem-ed .days button:nth-child(7)')
    page.select_option('.rem-ed [data-k="snooze"]', "15")
    page.select_option('.rem-ed [data-k="max_snooze"]', "1")
    page.select_option('.rem-ed [data-k="check"]', "10")
    page.select_option('.rem-ed [data-k="per_day"]', "4")
    page.check('.rem-ed [data-pack="Stoic"]')
    page.fill('.rem-ed [data-k="quotes"]', "mine one\nmine two")
    page.check('.rem-ed [data-k="guarded"]')
    save_editor(page)
    page.wait_for_selector(".rem [data-id]")
    r = customs(harness)[0]
    assert r["kind"] == "random" and r["window"] == ["09:00", "20:00"] and r["days"] == [0, 1, 2, 3, 4]
    assert (r["snooze"], r["max_snooze"], r["check"], r["per_day"]) == (15, 1, 10, 4)
    assert r["packs"] == ["Stoic"] and r["quotes"] == "mine one\nmine two" and r["guarded"] is True
    assert page.locator(".rem [data-id] .imp").is_visible()


def test_editor_errors_stay_in_the_dialog(shell, page, harness):
    open_page(shell, page)
    new_reminder(page, "")
    save_editor(page)
    page.wait_for_function("document.querySelector('.rem-ed [data-k=error]').textContent.length > 0")
    assert "Write what to remind you of" in page.inner_text(".rem-ed [data-k=error]")
    page.fill('.rem-ed [data-k="text"]', "x")
    page.fill('.rem-ed [data-k="every"]', "3s")
    save_editor(page)
    page.wait_for_function("document.querySelector('.rem-ed [data-k=error]').textContent.startsWith('How often')")
    assert customs(harness) == []
    page.click("#dlg .dlg-f button:first-child")
    page.wait_for_function("!document.querySelector('#dlg').open")


def test_deleting_an_important_reminder_while_locked_asks_for_the_challenge(shell, page, harness):
    reminders.save(harness.db, reminders.CUSTOM_KEY,
                   [{**reminders.DEFAULT_CUSTOM, "id": "abc12345", "text": "Medicine", "guarded": True}])
    lock(harness)
    open_page(shell, page)
    page.wait_for_selector(".rem [data-id] .imp:not([hidden])")
    btn = page.locator(".rem [data-id] .confirm")
    btn.click()
    btn.click()
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "Medicine" in page.inner_text("#dlg")
    assert len(customs(harness)) == 1
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#dlg').open")
    page.wait_for_timeout(300)
    assert len(customs(harness)) == 1 and page.locator(".rem [data-id]").count() == 1


def test_switching_an_important_reminder_off_while_locked_snaps_back(shell, page, harness):
    reminders.save(harness.db, reminders.CUSTOM_KEY,
                   [{**reminders.DEFAULT_CUSTOM, "id": "abc12345", "text": "Medicine", "guarded": True}])
    lock(harness)
    open_page(shell, page)
    page.wait_for_selector(".rem [data-id] .imp:not([hidden])")
    page.locator("input[data-row-switch]").click()
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#dlg').open")
    wait_for(page, "document.querySelector('input[data-row-switch]').checked === true")
    assert customs(harness)[0]["on"] is True


def test_removing_important_in_the_editor_while_locked_asks_for_the_challenge(shell, page, harness):
    reminders.save(harness.db, reminders.CUSTOM_KEY,
                   [{**reminders.DEFAULT_CUSTOM, "id": "abc12345", "text": "Medicine", "guarded": True}])
    lock(harness)
    open_page(shell, page)
    page.wait_for_selector(".rem [data-id] .imp:not([hidden])")
    page.click('.rem [data-act="edit"]')
    page.wait_for_selector(".rem-ed")
    page.uncheck('.rem-ed [data-k="guarded"]')
    save_editor(page)
    page.wait_for_selector("dialog[open] .ch-phrase", timeout=5000)
    assert customs(harness)[0]["guarded"] is True
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('.ch-phrase')")
    assert page.is_visible(".rem-ed")                          # the editor stays open with the edits
    assert page.is_checked('.rem-ed [data-k="guarded"]') is False
    assert customs(harness)[0]["guarded"] is True              # nothing saved


# ---------------------------------------------------------------- speed

def test_page_load_and_rows_are_reused(shell, page, harness, perf, capsys):
    items = [{**reminders.DEFAULT_CUSTOM, "id": f"id{i:06d}", "text": f"Reminder {i}"} for i in range(30)]
    reminders.save(harness.db, reminders.CUSTOM_KEY, items)
    shell()
    ms = perf.click_until('#nav [data-nav="reminders"]', '.rem [data-id]:nth-child(30)')
    assert 0 < ms < 400
    page.evaluate("document.querySelectorAll('.rem [data-id]').forEach((n, i) => { n.dataset.keep = i; })")
    harness.calls.clear()
    page.evaluate("import('/js/router.js').then((m) => m.router.reload('reminders'))")
    page.wait_for_timeout(300)
    kept = page.evaluate("[...document.querySelectorAll('.rem [data-id]')].filter((n) => n.dataset.keep !== undefined).length")
    assert kept == 30                                  # the same nodes after a reload
    assert harness.rpc_count("reminders_get") == 1
    with capsys.disabled():
        print(f"\n  reminders first visit (30 reminders): {ms:.0f} ms to the last row")


def test_a_mode_added_elsewhere_shows_up_in_the_turn_on_a_mode_list_on_a_mode_changed_push(shell, page, harness, push):
    import modes
    open_page(shell, page)
    options = lambda: page.eval_on_selector_all('[data-k="s-mode"] option', "els => els.map(e => e.textContent)")
    assert "Night owl" not in options()
    ms = modes.load(harness.db)
    ms.append({**ms[0], "id": modes.new_id(ms), "name": "Night owl", "builtin": False})
    modes.save(harness.db, ms)
    push("mode_changed", {"rev": 1})
    wait_for(page, "[...document.querySelectorAll('[data-k=\"s-mode\"] option')].some(o => o.textContent === 'Night owl')")
    assert "Night owl" in options()
