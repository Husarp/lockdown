"""R26: the Reminders page, headless in Chrome against the real Python Api (see harness.py)."""
import challenges
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


def test_each_step_has_its_own_dismiss_wait_while_the_wait_is_on(shell, page, harness):
    open_page(shell, page)
    box = page.locator('[data-tier] [data-k="t-waitbox"]').first
    assert box.is_hidden()                                      # Dismiss wait Off: no per-step waits
    page.click('.seg[data-seg="wait"] button[data-v="30"]')
    wait_for(page, "!document.querySelector('[data-tier] [data-k=\"t-waitbox\"]').hidden")
    page.locator('[data-tier] [data-k="t-wait"]').first.fill("2 min")
    page.locator('[data-tier] [data-k="t-wait"]').first.press("Enter")
    page.wait_for_timeout(300)
    tiers = sleep_saved(harness)["tiers"]
    assert reminders.step_wait(tiers[0]) == 120 and all(reminders.step_wait(t) is None for t in tiers[1:])
    page.locator('[data-k="s-before"]').click()                 # (focus out of the steps: the reload fills them)
    wait_for(page, "document.querySelector('[data-tier] [data-k=\"t-wait\"]').value === '2 min'")


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
    _saved_soon(page, harness, True)
    assert not page.locator("#dlg[open]").count()
    page.click('[data-k="s-grey"]')                                                # off again: bedtime is not Important, free
    _saved_soon(page, harness, False)
    assert not page.locator("#dlg[open]").count()


def _saved_soon(page, harness, want, key="grey"):
    """The switch's save goes through the bridge after the box flips: wait for it (up to 3 s)."""
    for _ in range(30):
        if sleep_saved(harness)[key] is want:
            return
        page.wait_for_timeout(100)
    assert sleep_saved(harness)[key] is want


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


def test_block_clicks_behind_the_bedtime_screen_is_a_switch_on_the_sleep_card(shell, page, harness):
    open_page(shell, page)
    assert page.locator(".rem :text('Block clicks behind the bedtime screen')").count() >= 1
    assert not page.is_checked('[data-k="s-block"]') and sleep_saved(harness)["block_behind"] is False
    page.click('[data-k="s-block"]')                                               # on: free
    wait_for(page, "document.querySelector('[data-k=\"s-block\"]').checked === true")
    assert sleep_saved(harness)["block_behind"] is True and not page.locator("#dlg[open]").count()
    page.click('[data-k="s-block"]')                                               # off again: bedtime is not Important, free
    page.wait_for_timeout(300)
    assert sleep_saved(harness)["block_behind"] is False and not page.locator("#dlg[open]").count()


def test_letting_clicks_through_while_important_and_locked_asks_for_the_challenge(shell, page, harness):
    reminders.save(harness.db, reminders.SLEEP_KEY,
                   {**reminders.DEFAULT_SLEEP, "on": True, "guarded": True, "block_behind": True})
    lock(harness)
    open_page(shell, page)
    assert page.is_checked('[data-k="s-block"]')
    page.click('[data-k="s-block"]')
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "let clicks through the bedtime screen" in page.inner_text("#dlg").lower()
    assert sleep_saved(harness)["block_behind"] is True
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#dlg').open")
    wait_for(page, "document.querySelector('[data-k=\"s-block\"]').checked === true")     # the switch snapped back
    assert sleep_saved(harness)["block_behind"] is True


# ---------------------------------------------------------------- Sleep: Challenge before Dismiss

GENERAL = [{"id": "g1", "kind": "math", "level": 0, "times": 2}]
BODY = '[data-k="c-body"]'


def chal_seg(page, on):
    page.click(f'.seg[data-seg="chal"] button[data-v="{1 if on else 0}"]')


def saved_wait(page, harness, test, timeout=4000):
    """Wait (the lists save a moment after the last edit) until test(sleep settings) holds."""
    for _ in range(timeout // 100):
        if test(sleep_saved(harness)):
            return
        page.wait_for_timeout(100)
    assert test(sleep_saved(harness)), sleep_saved(harness)


def test_challenge_before_dismiss_is_off_by_default_and_the_switch_shows_the_general_list(shell, page, harness):
    open_page(shell, page)
    assert page.locator(f'{BODY}').is_hidden()
    assert page.locator('.seg[data-seg="chal"] button[aria-pressed="true"]').get_attribute("data-v") == "0"
    assert page.locator('[data-tier] ~ [data-k="t-listbox"]').first.is_hidden()           # no list choices while it is off
    chal_seg(page, True)
    saved_wait(page, harness, lambda s: s["challenge"] is True)
    assert page.locator(BODY).is_visible()
    assert reminders.general_steps(sleep_saved(harness)) == challenges.default_bedtime_steps()      # Math Easy x2 to start
    rows = page.locator(f'{BODY} [data-k="c-general"] [data-step]')
    assert rows.count() == 1 and rows.first.locator('[data-f="kind"]').input_value() == "math"
    assert rows.first.locator(".stepper input").input_value() == "2"                       # Math, Easy, x2: the start
    assert page.locator('[data-k="c-px"]').is_hidden()                                       # no phrase step: no phrase extras
    assert page.locator('[data-tier-item] [data-k="t-listbox"]').first.is_visible()
    assert "How long" in page.inner_text(BODY)
    chal_seg(page, False)
    saved_wait(page, harness, lambda s: s["challenge"] is False)
    assert page.locator(BODY).is_hidden()


def test_the_general_list_is_edited_and_saved_with_its_ids_kept(shell, page, harness):
    reminders.save(harness.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "challenge": True, "challenge_steps": GENERAL})
    open_page(shell, page)
    page.click(f'{BODY} [data-act="sl-add"]')                                                # a step added
    saved_wait(page, harness, lambda s: len(s["challenge_steps"]) == 2)
    s = sleep_saved(harness)
    assert s["challenge_steps"][0] == GENERAL[0] and s["challenge_steps"][1]["id"] != "g1"
    page.locator(f'{BODY} [data-step]').first.locator('[data-f="level"]').select_option("2")    # Math Easy -> Hard: free
    saved_wait(page, harness, lambda s: s["challenge_steps"][0]["level"] == 2)
    page.locator(f'{BODY} [data-step]').nth(1).locator('[data-act="sl-x"]').click()
    saved_wait(page, harness, lambda s: len(s["challenge_steps"]) == 1)                      # bedtime is not Important: free
    assert sleep_saved(harness)["challenge_steps"] == [{**GENERAL[0], "level": 2}]


def test_the_phrase_extras_show_only_when_a_list_has_a_phrase_step(shell, page, harness):
    reminders.save(harness.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "challenge": True, "challenge_steps": GENERAL})
    open_page(shell, page)
    assert page.locator('[data-k="c-px"]').is_hidden()
    page.locator(f'{BODY} [data-step]').first.locator('[data-f="kind"]').select_option("phrase")
    saved_wait(page, harness, lambda s: s["challenge_steps"][0]["kind"] == "phrase")
    assert page.locator('[data-k="c-px"]').is_visible()
    assert "own phrase" not in page.inner_text('[data-k="c-px"]').lower()                    # no own phrase at bedtime
    page.click('[data-k="c-complex"]')
    page.click('[data-k="c-grid"]')
    saved_wait(page, harness, lambda s: s["challenge_complex"] is True and s["challenge_grid"] is True)
    page.locator(f'{BODY} [data-step]').first.locator('[data-f="kind"]').select_option("math")
    saved_wait(page, harness, lambda s: s["challenge_steps"][0]["kind"] == "math")
    assert page.locator('[data-k="c-px"]').is_hidden()


def test_a_step_can_use_the_general_list_or_have_its_own(shell, page, harness):
    reminders.save(harness.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "bedtime": "20:00", "challenge": True,
                                                     "challenge_steps": GENERAL})      # (steps in the order 21:00, 00:00, 03:00)
    open_page(shell, page)
    item = page.locator("[data-tier-item]").nth(1)                                           # the 00:00 step
    assert item.locator('[data-k="t-list"]').input_value() == "general" and item.locator('[data-k="t-own"]').is_hidden()
    item.locator('[data-k="t-list"]').select_option("own")
    saved_wait(page, harness, lambda s: any(reminders.tier_steps(t) for t in s["tiers"]))
    s = sleep_saved(harness)
    owns = {t["from"]: reminders.tier_steps(t) for t in reminders.tiers_of(s)}
    assert owns["00:00"] == GENERAL and owns["21:00"] == [] and owns["03:00"] == []         # a copy of the general list
    assert item.locator('[data-k="t-own"]').is_visible() and item.locator("[data-step]").count() == 1
    item.locator('[data-step]').first.locator('[data-f="kind"]').select_option("sort")        # its own list is its own
    saved_wait(page, harness, lambda s: reminders.tier_steps(reminders.tiers_of(s)[1])[0]["kind"] == "sort")
    s = sleep_saved(harness)
    assert reminders.general_steps(s) == GENERAL                                              # the general one is untouched
    assert [reminders.tier_steps(t) for t in reminders.tiers_of(s)][0] == []
    page.locator('[data-tier-item] [data-k="t-list"]').nth(0).focus()                         # (focus out: the reload fills)
    item.locator('[data-k="t-list"]').select_option("general")
    saved_wait(page, harness, lambda s: not any(reminders.tier_steps(t) for t in s["tiers"]))
    assert item.locator('[data-k="t-own"]').is_hidden()


def test_a_steps_own_list_is_kept_while_its_time_is_edited_and_a_phrase_there_shows_the_extras(shell, page, harness):
    own = [{"id": "o1", "kind": "phrase", "level": 30, "times": 1}]
    reminders.save(harness.db, reminders.SLEEP_KEY, {
        **reminders.DEFAULT_SLEEP, "bedtime": "20:00", "challenge": True, "challenge_steps": GENERAL,
        "tiers": [{"from": "21:00", "every_sec": 900}, {"from": "00:00", "every_sec": 300, "challenge_steps": own}]})
    open_page(shell, page)
    assert page.locator('[data-k="c-px"]').is_visible()                                       # the phrase is in a step's list
    item = page.locator("[data-tier-item]").nth(1)
    assert item.locator('[data-k="t-list"]').input_value() == "own" and item.locator('[data-k="t-own"]').is_visible()
    item.locator(".tp-h").fill("01")
    item.locator(".tp-h").press("Enter")
    saved_wait(page, harness, lambda s: reminders.tiers_of(s)[1]["from"] == "01:00")
    assert reminders.tier_steps(reminders.tiers_of(sleep_saved(harness))[1]) == own           # the list moved with its step


def test_making_the_bedtime_challenge_easier_while_important_asks_the_challenge_and_snaps_back(shell, page, harness):
    reminders.save(harness.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "guarded": True,
                                                     "challenge": True, "challenge_steps": GENERAL})
    lock(harness)
    open_page(shell, page)
    page.locator(f'{BODY} [data-step]').first.locator('.stepper [data-act="step"][data-d="-1"]').click()     # 2 sums -> 1
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=6000)
    assert "challenge before dismiss" in page.inner_text("#dlg").lower() and "easier" in page.inner_text("#dlg").lower()
    assert reminders.general_steps(sleep_saved(harness)) == GENERAL
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#dlg').open")
    wait_for(page, "document.querySelector('[data-k=\"c-general\"] .stepper input').value === '2'")      # snapped back
    assert reminders.general_steps(sleep_saved(harness)) == GENERAL


def test_turning_the_bedtime_challenge_off_while_important_asks_and_a_harder_list_does_not(shell, page, harness):
    reminders.save(harness.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "guarded": True,
                                                     "challenge": True, "challenge_steps": GENERAL})
    lock(harness)
    open_page(shell, page)
    page.locator(f'{BODY} [data-step]').first.locator('.stepper [data-act="step"][data-d="1"]').click()     # 2 -> 3: free
    saved_wait(page, harness, lambda s: s["challenge_steps"][0]["times"] == 3)
    assert not page.locator("#dlg[open]").count()
    chal_seg(page, False)
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert sleep_saved(harness)["challenge"] is True
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#dlg').open")
    wait_for(page, "document.querySelector('.seg[data-seg=\"chal\"] button[aria-pressed=\"true\"]').dataset.v === '1'")
    assert sleep_saved(harness)["challenge"] is True


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
