"""R25: the Modes page (js/pages/modes.js + modes/editor.js) in Chrome against the real Python Api (see harness.py).

What is checked: the Now card in each of its states (and that its countdown ticks on the page's own clock, without an Api
call), the Start panel (lengths, "Until..." rolling to tomorrow, Lock off for "Until I stop", a Pomodoro's "Runs N min"),
Stop / replace / delete / weaken of a locked mode asking for the challenge (and Cancel changing nothing), the editor's
validation messages, "Also block" (blocklist, other site or app), a status push from the tray, and a clean console."""
import re
import time
from datetime import timedelta

import antibypass as ab
import modes
from harness import NOW


def open_page(shell, page, **kw):
    shell(route="modes", **kw)
    page.wait_for_selector(".md-row", timeout=8000)
    page.wait_for_function("document.querySelector('[data-k=\"now-text\"]').textContent.length > 0")
    return page


def wait_for(page, js, timeout=5000):
    page.wait_for_function(js, timeout=timeout)


def lock(harness):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "length": 30})


def eventually(check, seconds=4):
    deadline = time.time() + seconds
    while not check() and time.time() < deadline:
        time.sleep(0.03)
    return check()


def stored(harness, mode_id):
    return next(m for m in modes.load(harness.db) if m["id"] == mode_id)


def active(harness):
    return modes.active(harness.db, NOW)


def now_name(page):
    return page.inner_text('[data-k="now-name"]')


def now_text(page):
    return page.inner_text('[data-k="now-text"]')


def dialog_closed(page, timeout=8000):
    """Closed AND its result handled: `open` turns false at dialog.close(), but the page handles the result in the dialog's
    'close' event, a task later, and only then empties the dialog (see test_page_blocking_groups.dialog_closed)."""
    wait_for(page, "(() => { const d = document.getElementById('dlg'); return !d.open && d.childElementCount === 0; })()", timeout)


def type_challenge(page):
    """Do what a person does: type the phrase Python issued and press Enter."""
    page.wait_for_selector("#dlg[open] textarea.typed", timeout=5000)
    text = page.evaluate("document.querySelector('.ch-phrase').textContent")
    page.focus("#dlg textarea.typed")
    page.keyboard.type(text, delay=45)
    page.keyboard.press("Enter")
    dialog_closed(page)


def escape_challenge(page):
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    page.keyboard.press("Escape")
    dialog_closed(page)


def start_panel(page, mode_id, choice=None, lock_it=False):
    page.click(f'.md-row[data-id="{mode_id}"] [data-act="start"]')
    page.wait_for_selector('[data-k="start"]:not([hidden])')
    if choice:
        page.click(f'.seg[data-seg="dur"] button[data-v="{choice}"]')
    if lock_it:
        page.check('[data-k="lock"]')


def start_now(page, mode_id, choice="60", lock_it=False):
    start_panel(page, mode_id, choice, lock_it)
    page.click('[data-k="start"] [data-act="go"]')
    wait_for(page, "document.querySelector('[data-k=\"start\"]').hidden")


def open_editor(page, mode_id=None):
    page.click(f'.md-row[data-id="{mode_id}"] [data-act="edit"]' if mode_id else '[data-act="new"]')
    page.wait_for_selector(".md-editor:not([hidden]) [data-k=\"name\"]", timeout=8000)


def editor_error(page):
    return page.inner_text('.md-editor [data-k="err"]')


# ================================================================ the page

def test_the_page_shows_the_modes_and_what_is_on(shell, page, harness):
    open_page(shell, page)
    assert now_name(page) == "No mode" and now_text(page) == "No mode is on."
    assert page.is_hidden('[data-k="stop"]')
    names = page.eval_on_selector_all(".md-row .n", "els => els.map(e => e.textContent)")
    assert names == ["Work", "Study", "Focus", "Do Not Disturb", "Relax"]
    rows = {r: page.inner_text(f'.md-row[data-id="{r}"]') for r in ("work", "focus", "dnd", "relax")}
    assert "Blocks: Distracting" in rows["work"] and "Blocks: nothing extra" in rows["relax"]
    assert "Pomodoro 25 / 5 min × 4, then 15 min" in rows["focus"] and "Pomodoro" not in rows["work"]
    assert "Blocks: Distracting · mutes notifications" in rows["dnd"]
    assert page.locator(".md-row").count() == 5 and page.locator('.md-row [data-act="start"]').count() == 5
    assert page.locator(".md-row .chip:not([hidden])").count() == 0
    assert page.is_visible('[data-k="hint"]') and page.is_hidden('[data-k="start"]') and page.is_hidden(".md-editor")
    # one Api call fills the page, and the editor module is not loaded yet
    assert harness.rpc_count("modes_get") == 1 and harness.rpc_count("blocklist_brief") == 0


def test_a_mode_started_by_hand_shows_until_and_counts_down_locally(shell, page, harness):
    modes.start(harness.db, "work", NOW, NOW + timedelta(hours=1), locked=False)
    open_page(shell, page)
    assert now_name(page) == "Work"
    assert re.fullmatch(r"Until 16:42 - (59:5\d|1:00:00) left", now_text(page)), now_text(page)
    assert page.inner_text('[data-k="stop"]') == "Stop"
    assert page.locator('.md-row[data-id="work"] .chip:not([hidden])').count() == 1
    before, calls = now_text(page), harness.rpc_count("modes_get")
    wait_for(page, f"document.querySelector('[data-k=\"now-text\"]').textContent !== {before!r}", 4000)
    time.sleep(1.1)
    assert harness.rpc_count("modes_get") == calls                       # the countdown never asks Python


def test_a_pomodoro_shows_its_phase_and_ticks(shell, page, harness):
    modes.start(harness.db, "focus", NOW, None)
    open_page(shell, page)
    assert now_name(page) == "Focus"
    first = now_text(page)
    assert re.fullmatch(r"Focus - (24:5\d|25:00) left \(round 1 of 4\)", first), first
    calls = harness.rpc_count("modes_get")
    wait_for(page, f"document.querySelector('[data-k=\"now-text\"]').textContent !== {first!r}", 4000)
    assert harness.rpc_count("modes_get") == calls
    assert page.inner_text('[data-k="stop"]') == "Stop"


def test_a_locked_mode_says_when_the_lock_ends(shell, page, harness):
    modes.start(harness.db, "work", NOW, NOW + timedelta(hours=1), locked=True)
    open_page(shell, page)
    assert page.inner_text('[data-k="stop"]') == "Stop (locked until 16:42)"


def test_a_scheduled_mode_has_no_stop_and_says_so(shell, page, harness):
    from rules import make_schedule
    ms = modes.load(harness.db)
    ms[1]["schedule"] = make_schedule("block", [(list(range(7)), "00:00", "23:59")])
    modes.save(harness.db, ms)
    open_page(shell, page)
    assert now_name(page) == "Study" and now_text(page) == "On by its schedule until 23:59"
    assert page.is_hidden('[data-k="stop"]')
    assert "Automatically: Every day 00:00-23:59" in page.inner_text('.md-row[data-id="study"]')


def test_until_you_stop_it(shell, page, harness):
    modes.start(harness.db, "relax", NOW, None)
    open_page(shell, page)
    assert now_name(page) == "Relax" and now_text(page) == "Until you stop it"


def test_a_status_push_from_the_tray_changes_the_card_without_an_api_call(shell, page, harness, push):
    open_page(shell, page)
    calls = harness.rpc_count()
    push("status", {"mode": {"id": "study", "name": "Study", "started": "2026-10-11T15:42:17", "ends_at": "2026-10-11T17:00:00",
                             "locked": True, "by_schedule": False, "mute": False, "blocking": True, "phase": None}})
    wait_for(page, "document.querySelector('[data-k=\"now-name\"]').textContent === 'Study'")
    assert page.inner_text('[data-k="stop"]') == "Stop (locked until 17:00)"
    assert page.locator('.md-row[data-id="study"] .chip:not([hidden])').count() == 1
    push("status", {"mode": None})
    wait_for(page, "document.querySelector('[data-k=\"now-name\"]').textContent === 'No mode'")
    assert page.is_hidden('[data-k="stop"]') and page.locator(".md-row .chip:not([hidden])").count() == 0
    assert harness.rpc_count() == calls


# ================================================================ the Start panel

def test_the_start_panel_has_the_lengths_and_a_lock_that_says_the_truth(shell, page, harness):
    open_page(shell, page)
    start_panel(page, "work")
    assert page.inner_text('[data-k="start-title"]') == "Start Work"
    assert page.locator('.seg[data-seg="dur"] button').all_inner_texts() == ["30 min", "1 h", "2 h", "Until…", "Until I stop"]
    assert page.get_attribute('.seg[data-seg="dur"] [data-v="60"]', "aria-pressed") == "true"
    text = page.inner_text('[data-k="start"] label.check')
    assert "stopping it early needs the Anti-Bypass challenge" in text and "not even with the challenge" not in text
    assert page.is_hidden('[data-k="until"]') and page.is_hidden('[data-k="runs"]')
    page.click('.seg[data-seg="dur"] [data-v="at"]')
    assert page.is_visible('[data-k="until"]')
    page.click('[data-act="start-cancel"]')
    assert page.is_hidden('[data-k="start"]') and page.is_visible('[data-k="hint"]')


def test_lock_is_off_for_until_i_stop(shell, page, harness):
    open_page(shell, page)
    start_panel(page, "work", "30", lock_it=True)
    assert page.is_checked('[data-k="lock"]') and page.is_enabled('[data-k="lock"]')
    page.click('.seg[data-seg="dur"] [data-v="stop"]')
    assert page.is_disabled('[data-k="lock"]') and not page.is_checked('[data-k="lock"]')
    page.click('.seg[data-seg="dur"] [data-v="120"]')
    assert page.is_enabled('[data-k="lock"]')


def test_start_an_hour_locked_says_so_and_the_card_follows(shell, page, harness):
    open_page(shell, page)
    start_now(page, "study", "60", lock_it=True)
    assert page.inner_text("#toast") .startswith("Study mode on until 16:42 (locked)")
    assert now_name(page) == "Study" and page.inner_text('[data-k="stop"]') == "Stop (locked until 16:42)"
    st = active(harness)
    assert (st["mode"]["id"], st["locked"], st["until"]) == ("study", True, NOW + timedelta(hours=1))


def test_a_pomodoro_start_panel_has_no_lengths_and_says_how_long_it_runs(shell, page, harness):
    open_page(shell, page)
    start_panel(page, "focus")
    assert page.is_hidden('[data-k="lengths"]')
    assert page.inner_text('[data-k="runs"]') == "Runs 130 min (all rounds)."
    page.click('[data-k="start"] [data-act="go"]')
    wait_for(page, "document.querySelector('[data-k=\"now-name\"]').textContent === 'Focus'")
    assert active(harness)["until"] == NOW + timedelta(minutes=130)
    assert re.search(r"round 1 of 4", now_text(page))


def test_until_a_time_that_has_passed_means_tomorrow(shell, page, harness):
    open_page(shell, page)
    start_panel(page, "work", "at")
    page.fill('[data-k="until"] .tp-h', "9")
    page.fill('[data-k="until"] .tp-m', "30")
    page.click('[data-k="start"] [data-act="go"]')
    wait_for(page, "document.querySelector('[data-k=\"now-name\"]').textContent === 'Work'")
    assert active(harness)["until"] == NOW.replace(hour=9, minute=30, second=0) + timedelta(days=1)
    assert re.fullmatch(r"Until 09:30 - 17:4\d:\d\d left", now_text(page)), now_text(page)


def test_a_bad_time_shows_python_s_words_and_starts_nothing(shell, page, harness):
    open_page(shell, page)
    start_panel(page, "work", "at")
    page.fill('[data-k="until"] .tp-h', "")
    page.fill('[data-k="until"] .tp-m', "")
    page.click('[data-k="start"] [data-act="go"]')
    wait_for(page, "document.querySelector('[data-k=\"start-err\"]').textContent.length > 0")
    assert page.inner_text('[data-k="start-err"]') == "Write the time like 17:00."
    assert page.is_visible('[data-k="start"]') and active(harness) is None


def test_stop_an_unlocked_mode_is_free(shell, page, harness):
    modes.start(harness.db, "work", NOW, NOW + timedelta(hours=1))
    open_page(shell, page)
    page.click('[data-k="stop"]')
    wait_for(page, "document.querySelector('[data-k=\"now-name\"]').textContent === 'No mode'")
    assert "Work mode off" in page.inner_text("#toast") and active(harness) is None
    assert page.is_hidden('[data-k="stop"]')


def test_stop_a_locked_mode_asks_for_the_challenge_even_with_none_turned_on(shell, page, harness):
    modes.start(harness.db, "work", NOW, NOW + timedelta(hours=1), locked=True)
    open_page(shell, page)
    page.click('[data-k="stop"]')
    escape_challenge(page)                                                      # Cancel: nothing changed
    assert now_name(page) == "Work" and active(harness)["locked"] is True
    page.click('[data-k="stop"]')
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"now-name\"]').textContent === 'No mode'")
    assert active(harness) is None


def test_starting_over_a_locked_mode_asks_for_the_challenge(shell, page, harness):
    modes.start(harness.db, "work", NOW, NOW + timedelta(hours=1), locked=True)
    open_page(shell, page)
    start_panel(page, "study", "30")
    page.click('[data-k="start"] [data-act="go"]')
    escape_challenge(page)
    assert page.is_visible('[data-k="start"]') and active(harness)["mode"]["id"] == "work"       # the panel stays open
    page.click('[data-k="start"] [data-act="go"]')
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"now-name\"]').textContent === 'Study'")
    assert active(harness)["mode"]["id"] == "study"


# ================================================================ the editor

def test_the_editor_for_a_new_mode_has_every_option(shell, page, harness):
    open_page(shell, page)
    open_editor(page)
    assert page.inner_text('.md-editor [data-k="title"]') == "New mode"
    assert page.get_attribute('.md-editor [data-k="name"]', "placeholder").startswith("e.g.")
    assert page.is_hidden('.md-editor [data-k="del"]')
    assert page.locator(".md-editor [data-cat]").count() == 4
    assert page.is_checked('.md-editor [data-cat="distracting"]') and not page.is_checked('.md-editor [data-cat="neutral"]')
    # the category checkboxes carry their colours
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", page.evaluate("getComputedStyle(document.querySelector('.md-cat')).getPropertyValue('--cat').trim()"))
    for label in ("Block these categories", "Also block", "Nothing else.", "+ Other site or app", "Pomodoro", "Mute notifications",
                  "Turn on automatically", "Done", "Cancel"):
        assert page.locator(f".md-editor :text('{label}')").count() >= 1, label
    assert page.is_hidden('.md-editor [data-k="pomo"]') and page.is_hidden('.md-editor [data-k="auto"]')
    page.check('.md-editor [data-k="pomo-on"]', force=True)
    assert page.locator(".md-editor [data-pomo]").evaluate_all("els => els.map(e => e.value)") == ["25", "5", "4", "15"]
    page.check('.md-editor [data-k="auto-on"]', force=True)
    assert page.locator(".md-editor [data-win]").count() == 1
    assert page.locator('.md-editor [data-win] .days button[aria-pressed="true"]').count() == 5      # Mon-Fri
    assert page.locator(".md-editor [data-win] .tp-h").evaluate_all("els => els.map(e => e.value)") == ["09", "17"]
    assert page.is_hidden('[data-k="hint"]')
    # the editor module came with the first open, and asked for the blocklist once
    assert harness.rpc_count("blocklist_brief") == 1


def test_the_editor_says_what_is_wrong_in_the_old_words(shell, page, harness):
    open_page(shell, page)
    open_editor(page)
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor [data-k=\"err\"]').textContent.length > 0")
    assert editor_error(page) == "Give the mode a name."
    page.fill('.md-editor [data-k="name"]', "Evening")
    page.check('.md-editor [data-k="pomo-on"]', force=True)
    page.fill('.md-editor [data-pomo="rounds"]', "0")
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor [data-k=\"err\"]').textContent.includes('between')")
    assert editor_error(page) == "Pomodoro numbers must be between 1 and 240."
    page.fill('.md-editor [data-pomo="rounds"]', "x")
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor [data-k=\"err\"]').textContent.includes('whole')")
    assert editor_error(page) == "Pomodoro numbers must be whole numbers."
    page.fill('.md-editor [data-pomo="rounds"]', "4")
    page.check('.md-editor [data-k="auto-on"]', force=True)
    page.evaluate("""() => document.querySelectorAll('.md-editor [data-win] .days button[aria-pressed="true"]').forEach((b) => b.click())""")
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor [data-k=\"err\"]').textContent.includes('at least one day')")
    assert editor_error(page) == "Turn on at least one day for the automatic times."
    assert not [m for m in modes.load(harness.db) if not m.get("builtin")]            # nothing was saved
    assert page.is_visible(".md-editor")


def test_a_new_mode_is_saved_with_all_its_options(shell, page, harness):
    open_page(shell, page)
    open_editor(page)
    page.fill('.md-editor [data-k="name"]', "Evening focus")
    page.check('.md-editor [data-cat="neutral"]')
    page.check('.md-editor [data-k="pomo-on"]', force=True)
    page.fill('.md-editor [data-pomo="work"]', "40")
    page.check('.md-editor [data-k="mute"]', force=True)
    page.check('.md-editor [data-k="auto-on"]', force=True)
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor').hidden")
    m = next(x for x in modes.load(harness.db) if x["name"] == "Evening focus")
    assert m["categories"] == ["neutral", "distracting"] or set(m["categories"]) == {"neutral", "distracting"}
    assert m["pomodoro"] == {"work": 40, "break": 5, "rounds": 4, "long": 15} and m["mute"] is True and m["schedule"]
    row = page.inner_text(f'.md-row[data-id="{m["id"]}"]')
    assert "Evening focus" in row and "mutes notifications" in row and "Pomodoro 40 / 5 min × 4, then 15 min" in row
    assert "Automatically: Monday–Friday 09:00-17:00" in row
    assert page.locator(".md-row").count() == 6 and page.is_visible('[data-k="hint"]')


def test_a_built_in_mode_keeps_its_name_and_cannot_be_deleted(shell, page, harness):
    open_page(shell, page)
    open_editor(page, "work")
    assert page.inner_text('.md-editor [data-k="title"]') == "Edit Work"
    assert page.is_disabled('.md-editor [data-k="name"]') and page.is_hidden('.md-editor [data-k="del"]')
    page.uncheck('.md-editor [data-cat="distracting"]')
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor').hidden")
    assert stored(harness, "work")["categories"] == [] and stored(harness, "work")["name"] == "Work"
    assert "Blocks: nothing extra" in page.inner_text('.md-row[data-id="work"]')


def test_cancel_closes_the_editor_and_changes_nothing(shell, page, harness):
    open_page(shell, page)
    open_editor(page, "study")
    page.uncheck('.md-editor [data-cat="distracting"]')
    page.click('.md-editor [data-act="cancel"]')
    assert page.is_hidden(".md-editor") and stored(harness, "study")["categories"] == ["distracting"]
    assert page.is_visible('[data-k="hint"]')
    open_editor(page, "study")                                                          # opens again with what is saved
    assert page.is_checked('.md-editor [data-cat="distracting"]')


def test_delete_an_own_mode_takes_two_clicks(shell, page, harness):
    harness.api.mode_save({"id": "", "name": "Temp", "categories": [], "items": [], "groups": [], "extra": [], "mute": False,
                           "pomodoro": None, "windows": None})
    open_page(shell, page)
    open_editor(page, "custom1")
    assert page.is_visible(".md-editor .confirm") and page.inner_text(".md-editor .confirm") == "Delete mode"
    page.click(".md-editor .confirm")
    assert page.inner_text(".md-editor .confirm") == "Confirm delete"
    assert any(m["id"] == "custom1" for m in modes.load(harness.db))                   # one click is only the first
    page.click(".md-editor .confirm")
    wait_for(page, "document.querySelector('.md-editor').hidden")
    assert not any(m["id"] == "custom1" for m in modes.load(harness.db))
    assert page.locator('.md-row[data-id="custom1"]').count() == 0


def test_deleting_the_active_locked_mode_asks_for_the_challenge(shell, page, harness):
    harness.api.mode_save({"id": "", "name": "Temp", "categories": ["distracting"], "items": [], "groups": [], "extra": [],
                           "mute": False, "pomodoro": None, "windows": None})
    modes.start(harness.db, "custom1", NOW, NOW + timedelta(hours=1), locked=True)
    open_page(shell, page)
    open_editor(page, "custom1")
    assert page.is_visible(".md-held")
    page.click(".md-editor .confirm")
    page.click(".md-editor .confirm")
    escape_challenge(page)
    assert page.is_visible(".md-editor") and any(m["id"] == "custom1" for m in modes.load(harness.db))
    page.click(".md-editor .confirm")
    page.click(".md-editor .confirm")
    type_challenge(page)
    wait_for(page, "document.querySelector('.md-editor').hidden")
    assert not any(m["id"] == "custom1" for m in modes.load(harness.db))
    wait_for(page, "document.querySelector('[data-k=\"now-name\"]').textContent === 'No mode'")


def test_weakening_the_active_locked_mode_asks_and_cancel_keeps_the_edits(shell, page, harness):
    modes.start(harness.db, "work", NOW, NOW + timedelta(hours=1), locked=True)
    open_page(shell, page)
    open_editor(page, "work")
    page.uncheck('.md-editor [data-cat="distracting"]')
    page.click('.md-editor [data-act="done"]')
    escape_challenge(page)
    assert page.is_visible(".md-editor") and not page.is_checked('.md-editor [data-cat="distracting"]')     # edits kept
    assert stored(harness, "work")["categories"] == ["distracting"]
    page.click('.md-editor [data-act="done"]')
    type_challenge(page)
    wait_for(page, "document.querySelector('.md-editor').hidden")
    assert stored(harness, "work")["categories"] == []


def test_making_the_active_locked_mode_stricter_is_free(shell, page, harness):
    modes.start(harness.db, "work", NOW, NOW + timedelta(hours=1), locked=True)
    open_page(shell, page)
    open_editor(page, "work")
    page.check('.md-editor [data-cat="neutral"]')
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor').hidden")
    assert set(stored(harness, "work")["categories"]) == {"distracting", "neutral"}
    assert not page.evaluate("document.getElementById('dlg').open")


def test_a_lighter_scheduled_pomodoro_asks_for_the_challenge(shell, page, harness):
    from rules import make_schedule
    ms = modes.load(harness.db)
    ms[2]["schedule"] = make_schedule("block", [([0, 1, 2, 3, 4], "09:00", "17:00")])       # Focus, Monday-Friday
    modes.save(harness.db, ms)
    lock(harness)
    open_page(shell, page)
    open_editor(page, "focus")
    page.fill('.md-editor [data-pomo="work"]', "5")
    page.click('.md-editor [data-act="done"]')
    escape_challenge(page)
    assert stored(harness, "focus")["pomodoro"]["work"] == 25
    page.fill('.md-editor [data-pomo="work"]', "50")                                       # longer focus: free
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor').hidden")
    assert stored(harness, "focus")["pomodoro"]["work"] == 50


def test_leaving_the_page_puts_it_back_to_its_default_view(shell, page, harness):
    open_page(shell, page)
    start_panel(page, "work")
    open_editor(page, "study")
    page.click('#nav [data-nav="about"]')
    page.click('#nav [data-nav="modes"]')
    page.wait_for_selector(".md-row")
    assert page.is_hidden('[data-k="start"]') and page.is_hidden(".md-editor") and page.is_visible('[data-k="hint"]')
    assert page.locator('.md-row[aria-current="true"]').count() == 0


# ================================================================ "Also block"

def test_also_block_from_the_blocklist_and_other_sites(shell, page, harness):
    yt = harness.db.add_site("YouTube", ["youtube.com"], rules=[{"rule_type": "permanent"}])
    open_page(shell, page)
    open_editor(page, "relax")
    wait_for(page, "document.querySelectorAll('.md-editor [data-k=\"from-list\"] option').length > 2")
    page.select_option('.md-editor [data-k="from-list"]', f"i:{yt}")
    assert page.locator('.md-editor [data-k="chips"] .pill').count() == 1 and "YouTube" in page.inner_text(".md-editor .pills")
    assert page.is_hidden('.md-editor [data-k="nothing"]')
    # the item can't be added twice: its option is off now
    assert page.evaluate(f"document.querySelector('.md-editor [data-k=\"from-list\"] option[value=\"i:{yt}\"]').disabled")
    page.click('.md-editor [data-act="other"]')
    page.fill('.md-editor [data-k="p-text"]', "not a site")
    page.click('.md-editor [data-act="pick-add"]')
    wait_for(page, "document.querySelector('.md-editor [data-k=\"p-err\"]').textContent.length > 0")
    assert "Write a site like reddit.com" in page.inner_text('.md-editor [data-k="p-err"]')
    page.fill('.md-editor [data-k="p-text"]', "https://www.Example.org/x")
    page.click('.md-editor [data-act="pick-add"]')
    wait_for(page, "document.querySelectorAll('.md-editor [data-k=\"chips\"] .pill').length === 2")
    assert page.is_hidden('.md-editor [data-k="pick"]')
    page.click('.md-editor [data-act="other"]')
    page.fill('.md-editor [data-k="p-text"]', "explorer.exe")                       # (typed as an app: the app ways show)
    page.click('.md-editor [data-act="pick-add"]')
    wait_for(page, "document.querySelector('.md-editor [data-k=\"p-err\"]').textContent.length > 0")
    assert "can't be blocked" in page.inner_text('.md-editor [data-k="p-err"]')
    page.fill('.md-editor [data-k="p-text"]', "discord.exe")
    page.click('.md-editor [data-act="pick-add"]')
    wait_for(page, "document.querySelectorAll('.md-editor [data-k=\"chips\"] .pill').length === 3")
    page.click('.md-editor [data-k="chips"] .pill:has-text("Example") [data-act="chip-x"]')
    assert page.locator('.md-editor [data-k="chips"] .pill').count() == 2
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor').hidden")
    m = stored(harness, "relax")
    assert m["items"] == [yt] and [e["targets"] for e in m["extra"]] == [["discord.exe"]]
    assert m["extra"][0]["block_type"] == "close"
    assert "Blocks: nothing extra" not in page.inner_text('.md-row[data-id="relax"]')
    assert "2 more" in page.inner_text('.md-row[data-id="relax"]')


def test_the_ways_follow_what_is_typed_and_exclude_each_other_like_the_old_picker(shell, page, harness):
    open_page(shell, page)
    open_editor(page, "relax")
    page.click('.md-editor [data-act="other"]')
    page.wait_for_selector('.md-editor [data-k="p-text"]')
    assert page.is_hidden('.md-editor [data-g="site"]') and page.is_hidden('.md-editor [data-g="app"]')       # (nothing typed yet)
    page.fill('.md-editor [data-k="p-text"]', "reddit.com")                      # a site: the site ways, at once
    assert page.is_visible('.md-editor [data-g="site"]') and page.is_hidden('.md-editor [data-g="app"]')
    page.check('.md-editor [data-g="site"] [data-way="close"]')
    assert page.is_checked('.md-editor [data-g="site"] [data-way="dns"]')
    page.check('.md-editor [data-g="site"] [data-way="back"]')
    assert not page.is_checked('.md-editor [data-g="site"] [data-way="close"]')
    page.fill('.md-editor [data-k="p-text"]', "discord.exe")                     # an app: the app ways
    assert page.is_visible('.md-editor [data-g="app"]') and page.is_hidden('.md-editor [data-g="site"]')
    page.check('.md-editor [data-g="app"] [data-way="minimize"]')
    assert not page.is_checked('.md-editor [data-g="app"] [data-way="close"]')
    assert page.is_disabled('.md-editor [data-g="app"] [data-way="background"]')
    page.fill('.md-editor [data-k="p-text"]', "tool.jar")                        # a program file: Close / Minimize and the note
    assert page.is_visible('.md-editor [data-g="file"]') and "Java game or a script" in page.inner_text('.md-editor [data-g="file"]')
    assert page.eval_on_selector_all('.md-editor [data-g="file"] [data-way]', "els => els.map(e => e.dataset.way)") == ["close", "minimize"]


def test_the_picker_suggests_has_a_category_button_and_a_display_name(shell, page, harness):
    open_page(shell, page)
    open_editor(page, "relax")
    page.click('.md-editor [data-act="other"]')
    page.fill('.md-editor [data-k="p-text"]', "redd")
    page.wait_for_selector('.md-editor .sg:not([hidden]) .sg-row', timeout=5000)                         # legacy's suggestions
    assert "reddit" in page.inner_text('.md-editor .sg').lower()
    page.click('.md-editor .sg-row:has-text("reddit")')                          # picking one selects it
    wait_for(page, "document.querySelectorAll('.md-editor .pill[data-key]').length === 1")
    assert page.is_visible('.md-editor [data-k="p-namewrap"]')
    page.fill('.md-editor [data-k="p-name"]', "Time sink")
    page.click('.md-editor [data-act="pick-add"]')
    wait_for(page, "document.querySelectorAll('.md-editor [data-k=\"chips\"] .pill').length === 1")
    assert "Time sink" in page.inner_text('.md-editor [data-k="chips"]')
    # the Category... button blocks everything in a category, as the old picker did
    page.click('.md-editor [data-act="other"]')
    page.click('.md-editor button:has-text("Category")')
    page.click('#menu [role="menuitem"]:has-text("Games")')
    assert "category · Games" in page.inner_text('.md-editor [data-k="p-pills"]')
    assert page.is_visible('.md-editor [data-g="category"]')
    page.click('.md-editor [data-act="pick-add"]')
    wait_for(page, "document.querySelectorAll('.md-editor [data-k=\"chips\"] .pill').length === 2")
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor').hidden")
    extra = stored(harness, "relax")["extra"]
    assert [(e["kind"], e["targets"]) for e in extra][1] == ("category", ["games"]) and extra[1]["block_type"] == "close"
    assert extra[0]["kind"] == "site" and extra[0]["name"] == "Time sink"


def test_an_empty_blocklist_says_so(shell, page, harness):
    page.route("**/rpc/blocklist_brief", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"ok": true, "data": {"groups": [], "items": []}}'))
    open_page(shell, page)
    open_editor(page, "relax")
    wait_for(page, "document.querySelectorAll('.md-editor [data-k=\"from-list\"] option').length >= 2")
    texts = page.locator('.md-editor [data-k="from-list"] option').all_inner_texts()
    assert texts == ["+ From your blocklist…", "(your blocklist is empty)"]


def test_the_blocklist_menu_lists_groups_then_items(shell, page, harness):
    open_page(shell, page)
    open_editor(page, "relax")
    wait_for(page, "document.querySelectorAll('.md-editor [data-k=\"from-list\"] optgroup').length === 2")
    groups = page.locator('.md-editor [data-k="from-list"] optgroup').evaluate_all("els => els.map(e => e.label)")
    assert groups == ["Groups", "Items"]
    assert page.locator('.md-editor [data-k="from-list"] optgroup:first-of-type option').first.inner_text().startswith("Group: ")


# ================================================================ the whole thing

def test_no_console_errors_in_light_and_a_narrow_window(shell, page, harness):
    open_page(shell, page, width=900, height=700)
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    start_panel(page, "work", "at")
    open_editor(page, "focus")
    page.click('.md-editor [data-act="other"]')
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def test_browse_apps_opens_the_shared_browser_and_leaves_the_picker_as_it_was(shell, page, harness):
    open_page(shell, page)
    open_editor(page, "relax")
    page.click('.md-editor [data-act="other"]')
    page.fill('.md-editor [data-k="p-text"]', "discord.exe")
    page.fill('.md-editor [data-k="p-name"]', "Typed name")
    page.click('.md-editor [data-browse]')
    page.wait_for_selector("#dlg[open] .ab-pick", timeout=5000)
    assert page.inner_text("#dlg h2") == "Browse apps"
    page.click("#dlg .dlg-f .btn")                                                       # Cancel
    dialog_closed(page)
    assert page.is_visible('.md-editor [data-k="pick"]') and page.input_value('.md-editor [data-k="p-name"]') == "Typed name"
    assert page.is_visible('.md-editor [data-g="app"]')
