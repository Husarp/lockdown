"""R24: the Anti-Bypass page (js/pages/antibypass.js) in Chrome against the real Python Api (see harness.py).

What is checked: every state of the lock banner and its countdown (it ticks on the page's own clock, without an Api
call), saving the challenges (a stricter change asks "are you sure?", a weaker one opens the challenge and snaps back
when it is cancelled, a bad length shows Python's error), Pause my blocks (start asks, resume is free, the top bar's
button lands on the card), Turn Lockdown off, status pushes, and a clean console in every theme."""
import re
import time
from datetime import datetime, timedelta

import pytest

import antibypass as ab
import pause
from harness import NOW


def open_page(shell, page, **kw):
    shell(route="antibypass", **kw)
    page.wait_for_selector('[data-card="challenges"]', timeout=8000)
    page.wait_for_function("document.querySelector('[data-k=\"b-title\"]').textContent.length > 0")
    return page


def wait_for(page, js, timeout=5000):
    page.wait_for_function(js, timeout=timeout)


def title(page):
    return page.inner_text('[data-k="b-title"]')


def lock(harness, **kw):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "length": 30, **kw})


def stored(harness):
    return ab.settings(harness.db)


def eventually(check, seconds=4):
    """The save runs in Python a moment after the dialog closed: wait for what the database should then say."""
    deadline = time.time() + seconds
    while not check() and time.time() < deadline:
        time.sleep(0.03)
    return check()


def dialog_open(page):
    return page.evaluate("document.getElementById('dlg').open")


def dialog_closed(page, timeout=8000):
    """Wait until the dialog is closed AND its OK / close result has been handled. `open` turns false the moment dialog.close()
    runs, but the page handles the result (onClose) in the dialog's 'close' event, a task later - and then empties the dialog.
    Reading the page between those two (a busy machine) sees the old state, so wait for the emptied dialog."""
    wait_for(page, "(() => { const d = document.getElementById('dlg'); return !d.open && d.childElementCount === 0; })()", timeout)


def type_challenge(page):
    """Do what a person does: type the phrase Python issued (35 characters at 45 ms) and press Enter."""
    page.wait_for_selector("#dlg[open] textarea.typed", timeout=5000)
    text = page.evaluate("document.querySelector('.ch-phrase').textContent")
    page.focus("#dlg textarea.typed")
    page.keyboard.type(text, delay=45)
    page.keyboard.press("Enter")
    dialog_closed(page)


def answer_yes(page):
    """The "Change the Anti-Bypass challenge?" question: yes."""
    page.wait_for_selector("#dlg[open]", timeout=5000)
    assert page.inner_text("#dlg h2") == "Change the Anti-Bypass challenge?"
    page.click("#dlg .dlg-f .btn-primary")
    dialog_closed(page)


def answer_no(page):
    page.wait_for_selector("#dlg[open]", timeout=5000)
    assert page.inner_text("#dlg h2") == "Change the Anti-Bypass challenge?"
    page.click("#dlg .dlg-f .btn:not(.btn-primary)")
    dialog_closed(page)


def escape_challenge(page):
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    page.keyboard.press("Escape")
    dialog_closed(page)


def step_rows(page):
    """The step list as the page shows it: [{kind, level (the select's value), times}]."""
    return page.eval_on_selector_all("[data-step]", """rows => rows.map(r => ({
        kind: r.querySelector('[data-f=kind]').value, level: r.querySelector('[data-f=level]').value,
        times: r.querySelector('.stepper input').value}))""")


def escape_dialog(page):
    """Whatever dialog is open (the challenge in any of its shapes): Escape."""
    page.wait_for_selector("#dlg[open]", timeout=5000)
    page.keyboard.press("Escape")
    dialog_closed(page)


def stored_steps(harness):
    return [(s["kind"], s["level"], s["times"]) for s in stored(harness)["steps"]]


MIXED = [{"id": "a1", "kind": "phrase", "level": 30, "times": 1}, {"id": "b2", "kind": "math", "level": 1, "times": 3}]


def tone(page):
    return page.get_attribute('[data-k="banner"]', "class")


def bar_width(page):
    return float(page.evaluate("parseFloat(document.querySelector('[data-k=\"b-bar\"]').style.width)"))


# ================================================================ the page

def test_the_page_shows_every_part(shell, page, harness):
    open_page(shell, page)
    heads = page.eval_on_selector_all(".ab .card-h h3", "els => els.map(e => e.textContent)")
    assert heads == ["Challenges", "Always on", "Pause my blocks", "Turn Lockdown off", "What needs the challenge",
                     "Keeping Lockdown running"]
    for label in ("Ask a challenge", "Steps - done top to bottom", "Add a step", "How long", "Save steps", "Undo",
                  "Numbers & CAPITALS", "3×3 grid", "Use my own phrase", "Your own phrase", "Save phrase", "Then wait",
                  "Only during these hours", "Add time window", "Save hours", "Try the challenge",
                  "Silence all notifications", "Resume blocking", "Uninstall protection", "Trusted time"):
        assert page.locator(f".ab :text('{label}')").count() >= 1, label
    assert page.locator('.ab .card:has(h3:text("What needs the challenge")) li').count() == 8
    assert page.locator('.ab .card:has(h3:text("Keeping Lockdown running")) li').count() == 4
    assert page.locator('[data-k="p-seg"] button').all_inner_texts() == ["30 min", "1 h", "2 h", "4 h", "Rest of the day"]
    # the saved values
    assert page.input_value('[data-k="wait"]') == "Off"
    assert step_rows(page) == [{"kind": "phrase", "level": "60", "times": "1"}]        # the old single phrase, as a list
    assert page.inner_text('[data-k="sl-time"]') == "under 1 min" and page.is_hidden('[data-k="sl-note"]')
    assert page.is_disabled('[data-k="save-steps"]') and page.inner_text('[data-k="steps-why"]') == "No changes."
    assert page.locator("[data-win]").count() == 1
    assert page.locator('[data-win] [data-act="win-x"]').is_hidden()          # no x on the only window
    assert page.is_hidden('[data-act="lock"]') and page.is_hidden('[data-act="unlock"]')
    assert "Tightening a rule is instant" in page.inner_text(".page-h .sub")
    # nothing here is a switch for what has no PC backend
    assert page.locator("text=Longest pause").count() == 0 and page.locator("text=Pause time per day").count() == 0


def test_banner_off(shell, page, harness):
    open_page(shell, page)
    assert title(page) == "Anti-Bypass is off" and "k-grey" in tone(page)
    assert page.is_hidden('[data-k="b-meter"]')


def test_banner_closed_with_the_next_chance(shell, page, harness):
    ab.save(harness.db, {**ab.settings(harness.db), "hours": True})
    open_page(shell, page)
    assert title(page) == "Closed right now" and "k-block" in tone(page)
    text = page.inner_text('[data-k="b-text"]')
    assert "Sunday 18:00-20:00" in text and "Next chance: 18:00." in text
    assert page.is_hidden('[data-act="unlock"]')                           # nothing to unlock: it is closed


def test_banner_locked_has_unlock_to_edit(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    assert title(page) == "Locked" and "k-block" in tone(page)
    assert page.inner_text('[data-k="b-text"]') == "Loosening a block needs: typing a 30-character phrase"
    assert page.is_visible('[data-act="unlock"]') and page.is_hidden('[data-act="lock"]')


def test_banner_unlocked_has_lock_now_and_a_draining_bar(shell, page, harness):
    lock(harness)
    ab.unlock(harness.db, NOW)
    open_page(shell, page)
    assert re.fullmatch(r"Unlocked for [45]:\d\d - loosening changes are allowed", title(page)), title(page)
    assert "k-ok" in tone(page)
    assert page.is_visible('[data-act="lock"]') and page.is_hidden('[data-act="unlock"]')
    assert 98 <= bar_width(page) <= 100


def test_banner_waiting_has_the_time_and_a_filling_bar(shell, page, harness):
    lock(harness, wait_min=10)
    ab.unlock(harness.db, NOW)
    open_page(shell, page)
    assert re.fullmatch(r"Passed - unlocks in (9:\d\d|10:00) \(at 15:52\)", title(page)), title(page)
    assert "k-limit" in tone(page) and page.is_hidden('[data-act="lock"]') and page.is_hidden('[data-act="unlock"]')
    assert 0 <= bar_width(page) <= 2


def test_banner_free_inside_the_allowed_hours(shell, page, harness):
    ab.save(harness.db, {**ab.settings(harness.db), "hours": True})
    harness.now = datetime(2026, 10, 11, 19, 0)
    open_page(shell, page)
    assert title(page) == "Free inside the allowed hours" and "k-ok" in tone(page)
    assert "until 20:00" in page.inner_text('[data-k="b-text"]')


# ================================================================ the clock

def test_the_countdown_ticks_on_the_page_without_an_api_call(shell, page, harness):
    lock(harness)
    ab.unlock(harness.db, NOW - timedelta(seconds=30))                        # 4:30 left
    open_page(shell, page)
    first = title(page)
    bar1 = bar_width(page)
    n = len(harness.calls)
    page.wait_for_timeout(2400)
    second = title(page)
    assert first != second

    def secs(t):
        m = re.search(r"(\d):(\d\d)", t)
        return int(m.group(1)) * 60 + int(m.group(2))
    assert 1 <= secs(first) - secs(second) <= 4, (first, second)
    assert bar_width(page) < bar1
    assert len(harness.calls) == n, harness.calls[n:]                          # no Api call while it counts down


def test_a_countdown_that_ends_asks_once_for_the_new_state(shell, page, harness):
    lock(harness)
    ab.save(harness.db, {**stored(harness), "unlocked_from": NOW.strftime("%Y-%m-%d %H:%M:%S"),
                         "unlocked_until": (NOW + timedelta(seconds=3)).strftime("%Y-%m-%d %H:%M:%S")})
    open_page(shell, page)
    assert title(page).startswith("Unlocked for 0:0")
    harness.now = NOW + timedelta(minutes=1)                                   # (the trusted clock moves on)
    wait_for(page, "document.querySelector('[data-k=\"b-title\"]').textContent === 'Locked'", 8000)
    page.wait_for_timeout(2500)
    assert harness.rpc_count("antibypass_get") == 2                            # the first load and the one at the end


# ================================================================ Lock now / Unlock to edit / Try

def test_lock_now_is_free(shell, page, harness):
    lock(harness)
    ab.unlock(harness.db, NOW)
    open_page(shell, page)
    page.click('[data-act="lock"]')
    wait_for(page, "document.querySelector('[data-k=\"b-title\"]').textContent === 'Locked'")
    assert stored(harness)["unlocked_until"] is None
    assert not dialog_open(page)


def test_unlock_to_edit_asks_for_the_challenge(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.click('[data-act="unlock"]')
    escape_challenge(page)
    assert title(page) == "Locked" and stored(harness)["unlocked_until"] is None
    page.click('[data-act="unlock"]')
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "Allow loosening changes for 5 minutes" in page.inner_text("#dlg")
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"b-title\"]').textContent.startsWith('Unlocked for')")
    assert stored(harness)["unlocked_until"]


def test_try_the_challenge_opens_it_and_changes_no_setting(shell, page, harness):
    open_page(shell, page)                                                     # nothing turned on
    keys = ("phrase", "length", "grid", "complex", "custom_phrase", "hours", "wait_min")
    before = {k: stored(harness)[k] for k in keys}
    page.click('[data-act="try"]')
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "Try the challenge" in page.inner_text("#dlg")
    page.keyboard.press("Escape")
    dialog_closed(page)
    assert {k: stored(harness)[k] for k in keys} == before


# ================================================================ saving the challenges

def test_a_switch_asks_are_you_sure_and_cancelling_snaps_it_back(shell, page, harness):
    open_page(shell, page)
    page.click('[data-k="phrase"]')
    page.wait_for_selector("#dlg[open]", timeout=5000)
    assert "Loosening a block needs: typing a 60-character phrase" in page.inner_text("#dlg")
    answer_no(page)
    wait_for(page, "document.querySelector('[data-k=\"phrase\"]').checked === false")
    assert stored(harness)["phrase"] is False
    page.click('[data-k="phrase"]')
    answer_yes(page)
    wait_for(page, "document.querySelector('[data-k=\"b-title\"]').textContent === 'Locked'")
    assert stored(harness)["phrase"] is True


def test_a_weaker_change_while_locked_opens_the_challenge_and_snaps_back(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.click('[data-k="phrase"]')                                            # off = looser
    escape_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"phrase\"]').checked === true")
    assert stored(harness)["phrase"] is True and not dialog_open(page)
    page.click('[data-k="phrase"]')
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"b-title\"]').textContent === 'Anti-Bypass is off'")
    assert stored(harness)["phrase"] is False and page.is_checked('[data-k="phrase"]') is False


# ---------------------------------------------------------------- the step list

def test_the_stored_steps_are_shown_with_their_kind_level_and_times(shell, page, harness):
    lock(harness, steps=MIXED)
    open_page(shell, page)
    assert step_rows(page) == [{"kind": "phrase", "level": "30", "times": "1"}, {"kind": "math", "level": "1", "times": "3"}]
    assert page.locator('[data-step] [data-f="kind"]').first.locator("option").all_inner_texts() == [
        "Math", "Memory grid", "Sorting", "Typed phrase", "Own password", "Authenticator code"]
    assert page.locator('[data-step] [data-f="level"]').nth(1).locator("option").all_inner_texts() == [
        "Easy", "Medium", "Hard", "Very hard"]
    assert page.locator('[data-step] [data-f="level"]').first.locator("option").all_inner_texts() == [
        "Short 30", "Medium 60", "Long 120", "Very long 250", "Custom length"]
    assert page.inner_text('[data-k="sl-time"]') == "under 1 min"                        # 15 + 3 x 10 s = 45 s
    assert page.locator('[data-step] [data-act="sl-up"]').first.is_disabled()
    assert page.locator('[data-step] [data-act="sl-down"]').last.is_disabled()


def test_adding_a_step_is_free_and_only_asks_are_you_sure(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.click('[data-act="sl-add"]')
    assert step_rows(page)[1] == {"kind": "math", "level": "0", "times": "3"}
    assert page.inner_text('[data-k="steps-why"]') == "Only harder or moved - free."
    assert page.inner_text('[data-k="save-steps"]') == "Save steps"
    assert page.locator('[data-step].chg').count() == 1                                  # the new row is marked
    page.click('[data-k="save-steps"]')
    answer_yes(page)                                                                     # (stricter: a yes, not the challenge)
    assert eventually(lambda: len(stored(harness)["steps"]) == 2)
    assert stored_steps(harness)[1] == ("math", 0, 3) and stored_steps(harness)[0] == ("phrase", 30, 1)
    assert len({s["id"] for s in stored(harness)["steps"]}) == 2 and len(stored(harness)["steps"][1]["id"]) == 8
    wait_for(page, "document.querySelectorAll('[data-step].chg').length === 0")
    assert page.inner_text('[data-k="steps-why"]') == "No changes."


def test_removing_a_step_asks_the_challenge_and_cancelling_keeps_the_edit(shell, page, harness):
    lock(harness, steps=MIXED)
    open_page(shell, page)
    page.locator('[data-step] [data-act="sl-x"]').nth(1).click()
    assert [r["kind"] for r in step_rows(page)] == ["phrase"]
    assert page.inner_text('[data-k="steps-why"]') == "Looser: removed Math ×3."
    assert page.inner_text('[data-k="save-steps"]') == "Save - asks the challenge"
    page.click('[data-k="save-steps"]')
    escape_dialog(page)
    assert len(stored(harness)["steps"]) == 2 and [r["kind"] for r in step_rows(page)] == ["phrase"]   # the draft stays
    page.click('[data-act="undo-steps"]')
    assert [r["kind"] for r in step_rows(page)] == ["phrase", "math"]
    assert page.is_disabled('[data-k="save-steps"]')


def test_removing_a_step_is_saved_after_the_challenge(shell, page, harness):
    lock(harness, steps=[{"id": "a1", "kind": "phrase", "level": 30, "times": 1}, {"id": "b2", "kind": "phrase", "level": 30, "times": 1}])
    open_page(shell, page)
    page.locator('[data-step] [data-act="sl-x"]').nth(1).click()
    page.click('[data-k="save-steps"]')
    page.wait_for_selector("#dlg[open] textarea.typed", timeout=5000)
    for _ in range(2):                                                                   # both steps are asked (the old list)
        text = page.evaluate("document.querySelector('.ch-phrase').textContent")
        page.focus("#dlg textarea.typed")
        page.keyboard.type(text, delay=45)
        page.keyboard.press("Enter")
        page.wait_for_function("(old) => !document.getElementById('dlg').open || "
                               "document.querySelector('.ch-phrase')?.textContent !== old", arg=text, timeout=8000)
        if not dialog_open(page):
            break
    dialog_closed(page)
    assert eventually(lambda: stored_steps(harness) == [("phrase", 30, 1)])
    assert [r["kind"] for r in step_rows(page)] == ["phrase"]


def test_moving_a_step_is_free_and_saves_without_a_question(shell, page, harness):
    lock(harness, steps=MIXED)
    open_page(shell, page)
    page.locator('[data-step] [data-act="sl-down"]').first.click()
    assert [r["kind"] for r in step_rows(page)] == ["math", "phrase"]
    assert page.inner_text('[data-k="steps-why"]') == "Only harder or moved - free."
    page.click('[data-k="save-steps"]')
    assert eventually(lambda: stored_steps(harness) == [("math", 1, 3), ("phrase", 30, 1)])
    assert not dialog_open(page)
    page.locator('[data-step] [data-act="sl-up"]').nth(1).click()
    page.click('[data-k="save-steps"]')
    assert eventually(lambda: stored_steps(harness) == [("phrase", 30, 1), ("math", 1, 3)])
    assert not dialog_open(page)


def test_raising_a_level_is_free_of_the_challenge(shell, page, harness):
    lock(harness, steps=MIXED)
    open_page(shell, page)
    page.locator('[data-step] [data-f="level"]').nth(1).select_option("3")
    assert page.inner_text('[data-k="steps-why"]') == "Only harder or moved - free."
    page.click('[data-k="save-steps"]')
    answer_yes(page)
    assert eventually(lambda: stored_steps(harness) == [("phrase", 30, 1), ("math", 3, 3)])


def test_lowering_the_level_or_the_times_or_changing_the_kind_asks_the_challenge(shell, page, harness):
    lock(harness, steps=MIXED)
    open_page(shell, page)
    page.locator('[data-step] [data-f="level"]').nth(1).select_option("0")
    assert page.inner_text('[data-k="steps-why"]') == "Looser: lowered the level of Math ×3."
    page.click('[data-act="undo-steps"]')
    page.locator('[data-step]').nth(1).locator('[data-act="step"][data-d="-1"]').click()
    assert page.locator('[data-step] .stepper input').nth(1).input_value() == "2"
    assert page.inner_text('[data-k="steps-why"]') == "Looser: fewer times on Math."
    page.click('[data-k="save-steps"]')
    escape_dialog(page)
    assert stored_steps(harness)[1] == ("math", 1, 3)
    page.click('[data-act="undo-steps"]')
    page.locator('[data-step] [data-f="kind"]').nth(1).select_option("sort")
    assert page.inner_text('[data-k="steps-why"]') == "Looser: changed Math ×3 to Sorting."
    assert step_rows(page)[1] == {"kind": "sort", "level": "1", "times": "3"}
    page.click('[data-k="save-steps"]')
    escape_dialog(page)
    assert stored_steps(harness)[1] == ("math", 1, 3)


def test_more_times_is_free_and_the_times_box_can_be_typed(shell, page, harness):
    lock(harness, steps=MIXED)
    open_page(shell, page)
    box = page.locator('[data-step] .stepper input').nth(1)
    box.fill("12")
    assert page.inner_text('[data-k="steps-why"]') == "Only harder or moved - free."
    box.fill("")                                                                          # mid-typing: nothing changes yet
    assert page.inner_text('[data-k="steps-why"]') == "Only harder or moved - free."
    page.click("h1")
    assert box.input_value() == "12"
    page.click('[data-k="save-steps"]')
    answer_yes(page)
    assert eventually(lambda: stored_steps(harness)[1] == ("math", 1, 12))


def test_at_most_six_steps_and_at_least_one(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    assert page.locator('[data-step] [data-act="sl-x"]').first.is_disabled()             # the only step can't go
    for _ in range(5):
        page.click('[data-act="sl-add"]')
    assert page.locator("[data-step]").count() == 6
    assert page.is_disabled('[data-act="sl-add"]') and page.inner_text('[data-k="sl-addnote"]') == "6 steps is the most."
    assert page.locator('[data-step] [data-act="sl-x"]:disabled').count() == 0
    assert len({r["id"] for r in page.eval_on_selector_all("[data-step]", "rows => rows.map(r => ({id: r.dataset.step}))")}) == 6
    for _ in range(5):
        page.locator('[data-step] [data-act="sl-x"]').last.click()
    assert page.locator("[data-step]").count() == 1 and page.locator('[data-step] [data-act="sl-x"]').first.is_disabled()


def test_the_estimate_follows_the_list_and_a_long_one_gets_the_soft_note(shell, page, harness):
    lock(harness, steps=[{"id": "m1", "kind": "math", "level": 3, "times": 20}])        # 20 x 35 s = 700 s
    open_page(shell, page)
    assert page.inner_text('[data-k="sl-time"]') == "about 12 min"
    assert page.inner_text('[data-k="sl-note"]') == "This will take about 12 min."
    page.locator('[data-step] [data-f="level"]').select_option("0")
    assert page.inner_text('[data-k="sl-time"]') == "about 2 min" and page.is_hidden('[data-k="sl-note"]')


def test_the_phrase_extras_only_show_while_the_list_has_a_phrase_step(shell, page, harness):
    lock(harness, steps=[{"id": "m1", "kind": "math", "level": 0, "times": 2}])
    open_page(shell, page)
    assert page.is_hidden('[data-k="px"]') and page.is_hidden('[data-k="own"]') and page.is_hidden('[data-k="grid"]')
    page.click('[data-act="sl-add"]')
    page.locator('[data-step] [data-f="kind"]').nth(1).select_option("phrase")
    assert page.is_visible('[data-k="px"]') and page.is_visible('[data-k="grid"]') and page.is_visible('[data-k="custom"]')
    assert step_rows(page)[1] == {"kind": "phrase", "level": "30", "times": "3"}         # a new phrase takes the current length (30 here)
    page.locator('[data-step] [data-act="sl-x"]').nth(1).click()
    assert page.is_hidden('[data-k="px"]')


def test_a_phrase_step_can_have_a_custom_length(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    level = page.locator('[data-step] [data-f="level"]')
    level.select_option("custom")
    box = page.locator('[data-step] [data-f="len"]')
    assert box.input_value() == "30"
    box.fill("2500")
    box.press("Tab")
    assert box.input_value() == "2000"                                                    # kept inside 30..2000
    box.fill("10")
    box.press("Tab")
    assert box.input_value() == "30"
    box.fill("45")
    box.press("Tab")
    assert page.inner_text('[data-k="sl-time"]') == "under 1 min"
    page.click('[data-k="save-steps"]')
    answer_yes(page)
    assert eventually(lambda: stored_steps(harness) == [("phrase", 45, 1)])
    wait_for(page, "document.querySelector('[data-step] [data-f=len]') !== null")        # (45 is no preset: the box stays)
    assert page.locator('[data-step] [data-f="len"]').input_value() == "45"
    level.select_option("120")
    assert page.locator('[data-step] [data-f="len"]').count() == 0 and step_rows(page)[0]["level"] == "120"


def test_a_stored_phrase_length_of_the_old_config_is_one_phrase_step(shell, page, harness):
    lock(harness, length=120)
    open_page(shell, page)
    assert step_rows(page) == [{"kind": "phrase", "level": "120", "times": "1"}]


def test_numbers_and_the_grid_are_switches(shell, page, harness):
    open_page(shell, page)
    page.click('[data-k="complex"]')
    answer_yes(page)
    wait_for(page, "document.querySelector('[data-k=\"complex\"]').checked === true")
    page.click('[data-k="grid"]')
    answer_yes(page)
    wait_for(page, "document.querySelector('[data-k=\"grid\"]').checked === true")
    assert eventually(lambda: stored(harness)["complex"] is True and stored(harness)["grid"] is True)


def test_the_own_phrase_is_kept_while_off_and_its_switch_dims_the_random_options(shell, page, harness):
    open_page(shell, page)
    page.click('[data-k="own"]')                                               # nothing written yet: refused
    wait_for(page, "document.querySelector('[data-k=\"err\"]').textContent.includes('write it first')")
    assert page.is_checked('[data-k="own"]') is False and not dialog_open(page)
    page.fill('[data-k="custom"]', "I promised myself one focused evening, and I keep my promises.")
    page.click('[data-act="save-phrase"]')                                     # switched off: saved, nothing asked
    assert eventually(lambda: stored(harness)["custom_phrase"].startswith("I promised myself"))
    assert stored(harness)["use_own"] is False and not dialog_open(page)
    assert "ab-dim" not in page.get_attribute(".ab-cx", "class") and "sl-own" not in page.get_attribute(".sl", "class")
    page.click('[data-k="own"]')
    answer_yes(page)                                                           # (no phrase asked yet: only a yes)
    wait_for(page, "document.querySelector('.ab-cx').classList.contains('ab-dim') && document.querySelector('.sl').classList.contains('sl-own')")
    assert stored(harness)["use_own"] is True and page.is_checked('[data-k="own"]')
    page.click('[data-k="own"]')
    answer_yes(page)
    wait_for(page, "!document.querySelector('.ab-cx').classList.contains('ab-dim')")
    assert stored(harness)["use_own"] is False and stored(harness)["custom_phrase"].startswith("I promised myself")
    assert page.input_value('[data-k="custom"]').startswith("I promised myself")
    page.click('[data-act="help"][data-help="own"]')
    assert page.is_visible('[data-helptext="own"]')
    page.click('[data-act="help"][data-help="own"]')
    assert page.is_hidden('[data-helptext="own"]')


def test_switching_your_own_phrase_on_while_locked_is_the_challenge(shell, page, harness):
    lock(harness, custom_phrase="my own words for the lock")                    # saved, switched off
    open_page(shell, page)
    assert page.input_value('[data-k="custom"]') == "my own words for the lock" and not page.is_checked('[data-k="own"]')
    page.click('[data-k="own"]')
    escape_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"own\"]').checked === false")
    assert stored(harness)["use_own"] is False and not dialog_open(page)


def test_then_wait_is_applied_on_enter_and_on_leaving_the_box(shell, page, harness):
    open_page(shell, page)
    page.fill('[data-k="wait"]', "10")
    page.press('[data-k="wait"]', "Enter")
    answer_yes(page)
    wait_for(page, "document.querySelector('[data-k=\"wait\"]').value === '10 min'")
    assert stored(harness)["wait_min"] == 10
    page.fill('[data-k="wait"]', "1h30")
    page.click("h1")                                                            # leaving the box
    answer_yes(page)
    assert eventually(lambda: stored(harness)["wait_min"] == 90)
    page.fill('[data-k="wait"]', "45s")
    page.press('[data-k="wait"]', "Enter")
    wait_for(page, "document.querySelector('[data-k=\"err\"]').textContent.length > 0")
    assert "Between" in page.inner_text('[data-k="err"]') and stored(harness)["wait_min"] == 90


def test_less_waiting_while_locked_is_the_challenge(shell, page, harness):
    lock(harness, wait_min=60)
    open_page(shell, page)
    page.fill('[data-k="wait"]', "Off")
    page.press('[data-k="wait"]', "Enter")
    escape_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"wait\"]').value === '1h'")
    assert stored(harness)["wait_min"] == 60


def test_the_hours_windows(shell, page, harness):
    open_page(shell, page)
    assert page.locator("[data-win]").count() == 1
    page.click('[data-act="win-add"]')
    assert page.locator("[data-win]").count() == 2
    assert page.locator('[data-win] [data-act="win-x"]:visible').count() == 2   # an x on each when there are two
    new = page.locator("[data-win]").nth(1)
    assert new.locator(".tp-h").nth(0).input_value() == "18" and new.locator(".tp-h").nth(1).input_value() == "20"
    assert new.locator('.days button[aria-pressed="true"]').count() == 1       # Sunday
    new.locator(".days button").nth(0).click()                                  # + Monday
    new.locator(".tp-h").nth(0).fill("07")
    assert "btn-primary" in page.get_attribute('[data-act="save-hours"]', "class")
    page.click('[data-act="save-hours"]')
    answer_yes(page)
    wait_for(page, "!document.querySelector('[data-act=\"save-hours\"]').classList.contains('btn-primary')")
    assert stored(harness)["windows"] == [{"days": [6], "start": "18:00", "end": "20:00"},
                                         {"days": [0, 6], "start": "07:00", "end": "20:00"}]
    page.locator('[data-win] [data-act="win-x"]').nth(0).click()
    assert page.locator("[data-win]").count() == 1
    assert page.locator('[data-win] [data-act="win-x"]').is_hidden()


def test_hours_with_no_day_show_the_error_and_the_window_stays(shell, page, harness):
    open_page(shell, page)
    page.locator('[data-win] .days button[aria-pressed="true"]').click()         # Sunday off: no day left
    page.click('[data-act="save-hours"]')
    wait_for(page, "document.querySelector('[data-k=\"err\"]').textContent.length > 0")
    assert "at least one day" in page.inner_text('[data-k="err"]')
    assert stored(harness)["windows"] == [{"days": [6], "start": "18:00", "end": "20:00"}]
    assert page.locator("[data-win]").count() == 1


def test_the_hours_switch_says_what_it_will_do(shell, page, harness):
    open_page(shell, page)
    page.click('[data-k="hours"]')
    page.wait_for_selector("#dlg[open]")
    assert "only Sunday 18:00-20:00" in page.inner_text("#dlg")
    answer_yes(page)
    wait_for(page, "document.querySelector('[data-k=\"b-title\"]').textContent === 'Closed right now'")


def test_fewer_hours_while_closed_only_show_the_closed_note(shell, page, harness):
    ab.save(harness.db, {**ab.settings(harness.db), "hours": True})
    open_page(shell, page)
    page.click('[data-k="hours"]')                                              # off = looser
    wait_for(page, "document.querySelector('#dlg h2') && document.querySelector('#dlg h2').textContent === 'Closed right now'")
    page.click("#dlg .dlg-f .btn-primary")
    dialog_closed(page)
    wait_for(page, "document.querySelector('[data-k=\"hours\"]').checked === true")
    assert stored(harness)["hours"] is True


# ================================================================ Pause my blocks

def card_text(page):
    return page.inner_text('[data-k="p-note"]')


def test_pause_starts_free_when_nothing_is_turned_on_and_resume_is_free(shell, page, harness):
    open_page(shell, page)
    assert card_text(page) == "Your blocks are on." and page.is_visible('[data-k="p-form"]')
    page.click('[data-k="p-seg"] button[data-v="30"]')
    page.click('[data-k="p-silent"]')
    page.click('[data-act="pause"]')
    wait_for(page, "document.querySelector('[data-act=\"resume\"]').hidden === false")
    assert re.fullmatch(r"Your blocks are paused until 16:12 \(.+ left\) - notifications are silenced\.", card_text(page))
    assert page.is_hidden('[data-k="p-form"]') and page.is_hidden('[data-act="pause"]')
    assert pause.state(harness.db, NOW + timedelta(seconds=1))["silent"] is True
    page.click('[data-act="resume"]')
    wait_for(page, "document.querySelector('[data-act=\"pause\"]').hidden === false")
    assert card_text(page) == "Your blocks are on." and pause.state(harness.db, NOW) is None


def test_pause_while_locked_opens_the_challenge_and_resume_does_not(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.click('[data-act="pause"]')                                            # 1 h is picked
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "Pause all your blocks for 1 h (until 16:42)" in page.inner_text("#dlg")
    page.keyboard.press("Escape")
    dialog_closed(page)
    assert pause.state(harness.db, NOW) is None and card_text(page) == "Your blocks are on."
    page.click('[data-act="pause"]')
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-act=\"resume\"]').hidden === false")
    assert re.fullmatch(r"Your blocks are paused until 16:42 \(.+ left\)\.", card_text(page))
    page.click('[data-act="resume"]')                                           # still locked: no challenge
    wait_for(page, "document.querySelector('[data-act=\"pause\"]').hidden === false")
    assert not dialog_open(page) and pause.state(harness.db, NOW) is None


def test_rest_of_the_day_ends_at_the_reset_time(shell, page, harness):
    open_page(shell, page)
    page.click('[data-k="p-seg"] button[data-v="day"]')
    page.click('[data-act="pause"]')
    wait_for(page, "document.querySelector('[data-act=\"resume\"]').hidden === false")
    assert "paused until 00:00" in card_text(page)


def test_the_top_bar_button_lands_on_the_pause_card(shell, page, harness):
    shell(route="about")
    page.wait_for_selector(".about-hero", timeout=8000)
    page.click("#tb-pause")
    page.wait_for_selector('[data-card="pause"]', timeout=8000)
    wait_for(page, "document.activeElement && document.activeElement.dataset.k === 'p-start'")
    assert "ab-flash" in page.get_attribute('[data-card="pause"]', "class")
    # and when the page is already open
    page.click('[data-nav="reminders"]')
    page.click("#tb-pause")
    wait_for(page, "document.activeElement && document.activeElement.dataset.k === 'p-start'")


# ================================================================ Turn Lockdown off

def test_turning_lockdown_off_always_asks_and_cancelling_snaps_back(shell, page, harness):
    open_page(shell, page)                                                      # no challenge turned on at all
    assert page.is_checked('[data-k="lockdown"]')
    page.click('[data-k="lockdown"]')
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "Turn Lockdown off entirely" in page.inner_text("#dlg")
    page.keyboard.press("Escape")
    dialog_closed(page)
    wait_for(page, "document.querySelector('[data-k=\"lockdown\"]').checked === true")
    assert not ab.is_off(harness.db)


def test_turning_lockdown_off_and_back_on(shell, page, harness):
    open_page(shell, page)
    page.click('[data-k="lockdown"]')
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"o-title\"]').textContent.startsWith('Lockdown is OFF')")
    assert ab.is_off(harness.db) and not page.is_checked('[data-k="lockdown"]')
    assert page.inner_text('[data-k="o-title"]').startswith("Lockdown is OFF - nothing has been enforced since 2026-10-11")
    wait_for(page, "document.querySelector('[data-banner=\"off\"]') && !document.querySelector('[data-banner=\"off\"]').hidden")
    lock(harness)                                                               # (now locked: switching on needs nothing)
    page.click('[data-k="lockdown"]')
    wait_for(page, "document.querySelector('[data-k=\"o-title\"]').textContent === 'Lockdown is on and enforcing your rules.'")
    assert page.is_checked('[data-k="lockdown"]') and not ab.is_off(harness.db) and not dialog_open(page)
    wait_for(page, "document.querySelector('[data-banner=\"off\"]').hidden")


# ================================================================ pushes

def push_pause(push, started, until, silent=False):
    push("status", {"pause": {"started": started, "until": until, "silent": silent}})


def test_a_pause_started_elsewhere_shows_up_and_an_equal_push_changes_nothing(shell, page, harness, push):
    open_page(shell, page)
    n = harness.rpc_count("antibypass_get")
    push("status", {"clock_offset": 0.0})                                       # nothing about this page changed
    page.wait_for_timeout(300)
    assert harness.rpc_count("antibypass_get") == n
    pause.start(harness.db, NOW, 30, True)                                      # e.g. the tray's Pause
    push_pause(push, "2026-10-11T15:42:17", "2026-10-11T16:12:17", True)
    wait_for(page, "document.querySelector('[data-act=\"resume\"]').hidden === false")
    assert harness.rpc_count("antibypass_get") == n + 1
    assert "notifications are silenced" in card_text(page)
    page.wait_for_timeout(300)
    push_pause(push, "2026-10-11T15:42:17", "2026-10-11T16:12:17", True)        # the same again: no more reloads
    page.wait_for_timeout(300)
    assert harness.rpc_count("antibypass_get") == n + 1


def test_a_reload_never_overwrites_what_is_being_typed_or_unsaved_hours(shell, page, harness, push):
    open_page(shell, page)
    page.click('[data-act="win-add"]')                                          # unsaved hours (a second window)
    page.focus('[data-k="custom"]')
    page.keyboard.type("half a phrase")
    pause.start(harness.db, NOW, 30, False)
    push_pause(push, "2026-10-11T15:42:17", "2026-10-11T16:12:17")
    wait_for(page, "document.querySelector('[data-act=\"resume\"]').hidden === false")
    assert page.input_value('[data-k="custom"]') == "half a phrase"
    assert page.locator("[data-win]").count() == 2


# ================================================================ themes, speed, hidden window

@pytest.mark.parametrize("theme", ["dark", "amoled", "light"])
def test_no_console_errors_in_any_theme(shell, page, harness, theme):
    lock(harness)
    ab.unlock(harness.db, NOW)
    open_page(shell, page, width=1000, height=650)
    page.evaluate(f"document.documentElement.dataset.theme = '{theme}'")
    page.click('[data-act="help"][data-help="wait"]')
    page.click('[data-act="win-add"]')
    page.click('[data-act="lock"]')
    wait_for(page, "document.querySelector('[data-k=\"b-title\"]').textContent === 'Locked'")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_the_first_visit_and_a_second_look_are_fast(shell, page, harness, perf, capsys):
    shell(route="about")
    page.wait_for_selector(".about-hero", timeout=8000)
    first = perf.click_until('[data-nav="antibypass"]', '[data-card="challenges"]')
    page.click('[data-nav="about"]')
    page.wait_for_timeout(200)
    again = perf.click_to_frame('[data-nav="antibypass"]')
    with capsys.disabled():
        print(f"\n  antibypass: first visit {first:.0f} ms, shown again {again:.0f} ms")
    assert 0 < first < 300 and again < 100


def test_no_timer_runs_while_the_window_is_hidden(shell, page, harness, perf):
    lock(harness)
    ab.unlock(harness.db, NOW)
    open_page(shell, page)
    perf.set_hidden(True)
    page.wait_for_timeout(300)
    perf.reset_timer_calls()
    n = len(harness.calls)
    page.wait_for_timeout(2200)
    assert perf.timer_calls()["timeout"] == 0 and len(harness.calls) == n
    perf.set_hidden(False)
    page.wait_for_timeout(1200)
    assert perf.timer_calls()["timeout"] >= 1                                   # the clock runs again

