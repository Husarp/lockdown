"""R27: the Settings page (js/pages/settings.js) in Chrome against the real Python Api (see harness.py).

What is checked: every part of the real page is there, the look (theme, accent, interface size, Reduce motion) applies live
with no reload and is saved, the daily goal, when limits reset (a later time, "From {end}", "Start now" asking for the
challenge), the emergency unlock card (add / remove a kind, a loosening change asks and snaps back when cancelled, the meters
are fresh every time the page is shown), export / import with the diff dialog, the screen-time CSV, the new-window switch,
the Locked strip, and a clean console in every theme."""
import json
import re
import time

import pytest

import antibypass as ab
import backup
import emergency
from webapi import screentime, settings as api_settings


def open_page(shell, page, **kw):
    shell(route="settings", **kw)
    page.wait_for_selector('[data-card="emergency"] .set-kind', timeout=8000)
    page.wait_for_function("document.querySelectorAll('[data-k=swatches] button').length === 9")
    return page


def saved(harness, key):
    return harness.db.get_setting(key)


def eventually(check, seconds=4):
    deadline = time.time() + seconds
    while not check() and time.time() < deadline:
        time.sleep(0.03)
    return check()


def wait_for(page, js, timeout=5000):
    page.wait_for_function(js, timeout=timeout)


def lock(harness, **kw):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "length": 30, **kw})


def mark(page):
    """A marker that a reload of the page would wipe."""
    page.evaluate("window.__marker = 42")


def not_reloaded(page):
    return page.evaluate("window.__marker") == 42


def css_var(page, name):
    return page.evaluate(f"getComputedStyle(document.documentElement).getPropertyValue('{name}').trim()")


def dialog_closed(page, timeout=8000):
    """Wait until the dialog is closed AND its OK / close result has been handled. `open` turns false the moment dialog.close()
    runs, but the page handles the result (onClose) in the dialog's 'close' event, a task later - and then empties the dialog.
    Reading the page between those two (a busy machine) sees the old state, so wait for the emptied dialog."""
    wait_for(page, "(() => { const d = document.getElementById('dlg'); return !d.open && d.childElementCount === 0; })()", timeout)


def type_challenge(page):
    page.wait_for_selector("#dlg[open] textarea.typed", timeout=5000)
    text = page.evaluate("document.querySelector('.ch-phrase').textContent")
    page.focus("#dlg textarea.typed")
    page.keyboard.type(text, delay=45)
    page.keyboard.press("Enter")
    dialog_closed(page)


def cancel_challenge(page):
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    page.keyboard.press("Escape")
    dialog_closed(page)


def kinds(harness):
    return emergency.tiers(harness.db)


def left_now(harness):
    """Uses left of the first kind right now (the synthetic data has spent some)."""
    [(_kind, left, _allowed, _reset)] = emergency.left_by_tier(harness.db, harness.now)
    return left


def kind_ui(page):
    """[(uses, per, minutes)] of the kind rows as the page shows them."""
    return page.evaluate("""[...document.querySelectorAll('.set-kind')].map(r => [r.querySelector('[data-k=k-uses]').value,
      r.querySelector('[data-k=k-per]').value, r.querySelector('[data-k=k-minutes]').value])""")


# ================================================================ the page

def test_the_page_shows_every_part(shell, page, harness):
    open_page(shell, page)
    heads = page.eval_on_selector_all(".set .card-h h3", "els => els.map(e => e.textContent)")
    assert heads == ["Appearance", "When limits reset", "Categories", "Emergency unlock", "Backup & export"]
    text = page.inner_text(".set")
    for label in ("Theme", "Accent colour", "Interface size", "Daily goal", "Reduce motion", "Reset at", "ends ",
                  "Manage categories", "Allow emergency unlocks", "Add another kind", "Export…", "Import…",
                  "Screen time (CSV)…", "Applies at once - no restart"):
        assert label in text, label
    assert page.locator('[data-seg="theme"] button').all_inner_texts() == ["Dark", "AMOLED", "Light", "System"]
    assert page.locator("[data-k=swatches] [data-color]").count() == 8
    assert page.input_value('[data-k="scale"]') == "auto" and page.input_value('[data-k="goal"]') == "5"
    assert page.locator('[data-k="goal"] option').all_inner_texts() == ["Off"] + [f"{h} h" for h in range(1, 13)]
    assert page.locator('[data-k="scale"] option').all_inner_texts() == ["Auto", "100%", "90%", "80%", "70%"]
    assert page.locator(".set-cat").count() >= 3 and "Productive" in page.inner_text('[data-k="cats"]')
    assert page.locator(".set-kind").count() == 1 and kind_ui(page) == [["3", "week", "20"]]
    assert re.fullmatch(r"\d of 3 left this week · resets \w{3} \d+ \w{3} \d\d:\d\d", page.inner_text(".set-meter"))
    assert page.locator(".set-meter .uses i").count() == 3
    assert page.locator('[data-k="lock"]').is_hidden()


def test_the_mocks_invented_settings_are_left_out(shell, page, harness):
    open_page(shell, page)
    text = page.inner_text(".set")
    for gone in ("Start with Windows", "Start in the tray", "Language", "Channel", "Site history", "Service", "Updates"):
        assert gone not in text, gone


# ================================================================ the look: live, no reload

def test_accent_applies_live_without_a_reload_and_is_saved(shell, page, harness):
    open_page(shell, page)
    mark(page)
    page.click('[data-color="#2F6FEB"]')
    wait_for(page, "getComputedStyle(document.documentElement).getPropertyValue('--brand').trim().toUpperCase() === '#2F6FEB'")
    # the swatch is pressed only once the Api replied (paintAccent runs after setLook's await): wait for it
    wait_for(page, "document.querySelector('[data-color=\"#2F6FEB\"]').getAttribute('aria-pressed') === 'true'")
    assert page.get_attribute('[data-color="#DB5126"]', "aria-pressed") == "false"
    assert eventually(lambda: saved(harness, "ui.accent") == "#2F6FEB")
    # a computed style on a real control follows it, and nothing says "restart"
    bg = page.eval_on_selector(".btn-primary", "e => getComputedStyle(e).backgroundColor")
    assert bg == "rgb(47, 111, 235)"
    assert "restart lockdown" not in page.inner_text(".set").lower() and page.locator("button:has-text('Restart')").count() == 0
    assert not_reloaded(page)


def test_a_custom_accent_gets_the_ring(shell, page, harness):
    open_page(shell, page)
    page.evaluate("""() => { const i = document.querySelector('[data-k=accent-input]'); i.value = '#10aa77';
      i.dispatchEvent(new Event('change', {bubbles: true})); }""")
    wait_for(page, "getComputedStyle(document.documentElement).getPropertyValue('--brand').trim().toUpperCase() === '#10AA77'")
    assert eventually(lambda: saved(harness, "ui.accent") == "#10AA77")
    custom = page.locator(".set-custom")
    assert custom.get_attribute("aria-pressed") == "true" and "has" in custom.get_attribute("class")
    assert page.locator("[data-color][aria-pressed=true]").count() == 0
    page.click('[data-color="#DB5126"]')
    wait_for(page, "document.querySelector('.set-custom').getAttribute('aria-pressed') === 'false'")


def test_interface_size_applies_live_and_scales_the_page(shell, page, harness):
    open_page(shell, page)
    mark(page)
    h1 = page.eval_on_selector(".set h1", "e => parseFloat(getComputedStyle(e).fontSize)")
    page.select_option('[data-k="scale"]', "80")
    wait_for(page, "getComputedStyle(document.documentElement).getPropertyValue('--s').trim() === '0.8'")
    smaller = page.eval_on_selector(".set h1", "e => parseFloat(getComputedStyle(e).fontSize)")
    assert smaller == pytest.approx(h1 * 0.8, abs=0.1)
    assert eventually(lambda: saved(harness, "ui.scale") == "80")
    page.select_option('[data-k="scale"]', "auto")
    wait_for(page, "getComputedStyle(document.documentElement).getPropertyValue('--s').trim() === '1'")
    assert not_reloaded(page)


def test_theme_applies_live_and_is_saved(shell, page, harness):
    open_page(shell, page)
    mark(page)
    for value in ("light", "amoled", "system", "dark"):
        page.click(f'[data-seg="theme"] [data-v="{value}"]')
        want = "(matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')" if value == "system" else f"'{value}'"
        wait_for(page, f"document.documentElement.dataset.theme === {want}")
        assert eventually(lambda: saved(harness, "ui.theme") == value)
        assert page.get_attribute(f'[data-seg="theme"] [data-v="{value}"]', "aria-pressed") == "true"
    assert not_reloaded(page)


def test_reduce_motion_turns_animations_off_and_stays_after_a_restart(shell, page, harness):
    open_page(shell, page)
    page.click('[data-k="motion"]')
    wait_for(page, "document.documentElement.dataset.motion === 'off'")
    assert eventually(lambda: saved(harness, "ui.reduce_motion") == "1")
    assert page.eval_on_selector(".btn", "e => getComputedStyle(e).transitionDuration") in ("0s", "0.0s")
    shell(route="settings")                                                  # the app starts again: still off
    wait_for(page, "document.documentElement.dataset.motion === 'off'")
    page.click('[data-k="motion"]')
    wait_for(page, "document.documentElement.dataset.motion === undefined")
    assert eventually(lambda: saved(harness, "ui.reduce_motion") == "0")


def test_a_look_change_is_free_while_locked(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.click('[data-color="#7C4DDB"]')
    assert eventually(lambda: saved(harness, "ui.accent") == "#7C4DDB")
    assert not page.evaluate("document.getElementById('dlg').open")


def test_the_daily_goal_is_saved_and_free_while_locked(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.select_option('[data-k="goal"]', "3")
    assert eventually(lambda: saved(harness, "stats.goal_hours") == "3")
    page.select_option('[data-k="goal"]', "0")
    assert eventually(lambda: saved(harness, "stats.goal_hours") == "0")
    assert not page.evaluate("document.getElementById('dlg').open")


# ================================================================ the Locked strip

def test_the_locked_strip_shows_and_unlock_to_edit_opens_the_challenge(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    wait_for(page, "!document.querySelector('[data-k=lock]').hidden")
    assert "need the challenge" in page.inner_text('[data-k="lock"]')
    page.click('[data-k="lock"] button')
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-k=lock]').hidden")


def test_the_strip_says_closed_hours_and_disables_unlock(shell, page, harness):
    lock(harness, phrase=False, hours=True, windows=[{"days": [0], "start": "03:00", "end": "04:00"}])    # (today is Sunday)
    open_page(shell, page)
    wait_for(page, "!document.querySelector('[data-k=lock]').hidden")
    assert "allowed hours" in page.inner_text('[data-k="lock"]')
    assert page.is_disabled('[data-k="lock"] button')


# ================================================================ when limits reset

def change_reset(page, hh, mm="00"):
    page.fill(".set-reset .tp-h", hh)
    page.fill(".set-reset .tp-m", mm)
    page.click(".set-reset .confirm")                  # arm
    assert page.inner_text(".set-reset .confirm") == "Confirm change"
    page.click(".set-reset .confirm")


def test_a_later_reset_time_is_saved_after_the_second_click(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.fill(".set-reset .tp-h", "04")
    page.click(".set-reset .confirm")
    assert saved(harness, "limits.reset") is None                    # one click only arms the button
    page.click(".set-reset .confirm")
    assert eventually(lambda: saved(harness, "limits.reset") is not None)
    assert json.loads(saved(harness, "limits.reset"))["time"] == "04:00"
    wait_for(page, "document.querySelector('[data-k=reset-info]').textContent.startsWith('ends ')")
    assert not page.evaluate("document.getElementById('dlg').open")
    assert page.inner_text(".set-reset .confirm") == "Change"


def test_the_same_or_a_bad_reset_time_shows_pythons_words(shell, page, harness):
    open_page(shell, page)
    change_reset(page, "00", "00")
    wait_for(page, "document.querySelector('[data-k=reset-err]').textContent.length > 0")
    assert "already the reset time" in page.inner_text('[data-k="reset-err"]')
    page.fill(".set-reset .tp-h", "")
    page.fill(".set-reset .tp-m", "")
    page.click(".set-reset .confirm")
    page.click(".set-reset .confirm")
    wait_for(page, "document.querySelector('[data-k=reset-err]').textContent.includes('04:00')")
    assert saved(harness, "limits.reset") is None


def move_back_dialog(shell, page, harness):
    harness.db.set_setting("limits.reset", json.dumps({"time": "04:00"}))
    open_page(shell, page)
    change_reset(page, "02", "00")
    page.wait_for_selector("#dlg[open] .dlg-f")


def test_moving_it_back_asks_start_now_or_from_the_end_or_cancel(shell, page, harness):
    move_back_dialog(shell, page, harness)
    assert page.inner_text("#dlg h2") == "Move the reset back to 02:00"
    labels = page.locator("#dlg .dlg-f button").all_inner_texts()
    assert labels[0] == "Cancel" and labels[1].startswith("From ") and labels[2] == "Start now"
    assert "Anti-Bypass asks first" in page.inner_text("#dlg")
    page.click("#dlg .dlg-f button:has-text('Cancel')")
    dialog_closed(page)
    assert json.loads(saved(harness, "limits.reset")) == {"time": "04:00"}


def test_from_the_end_of_the_day_is_free_even_while_locked(shell, page, harness):
    lock(harness)
    move_back_dialog(shell, page, harness)
    page.click("#dlg .dlg-f button:has-text('From ')")
    dialog_closed(page)
    assert eventually(lambda: json.loads(saved(harness, "limits.reset"))["time"] == "02:00")
    assert "day_start" in json.loads(saved(harness, "limits.reset"))
    wait_for(page, "!document.querySelector('[data-k=reset-note]').hidden")
    assert "runs to" in page.inner_text('[data-k="reset-note"]')


def test_start_now_asks_for_the_challenge_while_locked(shell, page, harness):
    lock(harness)
    move_back_dialog(shell, page, harness)
    page.click("#dlg .dlg-f button:has-text('Start now')")
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert json.loads(saved(harness, "limits.reset")) == {"time": "04:00"}      # nothing before the challenge
    assert "Start a fresh limit day now (02:00 reset)" in page.inner_text("#dlg")
    type_challenge(page)
    assert eventually(lambda: json.loads(saved(harness, "limits.reset"))["time"] == "02:00")
    assert "day_start" not in json.loads(saved(harness, "limits.reset"))


def test_cancelling_the_challenge_leaves_the_reset_and_the_box_snaps_back(shell, page, harness):
    lock(harness)
    move_back_dialog(shell, page, harness)
    page.click("#dlg .dlg-f button:has-text('Start now')")
    cancel_challenge(page)
    assert json.loads(saved(harness, "limits.reset")) == {"time": "04:00"}
    wait_for(page, "document.querySelector('.set-reset .tp-h').value === '04'")


# ================================================================ categories

def test_categories_are_chips_and_manage_opens_the_editor(shell, page, harness):
    open_page(shell, page)
    names = page.eval_on_selector_all(".set-cat span", "els => els.map(e => e.textContent)")
    assert names[:2] == ["Productive", "Neutral"]
    page.click('[data-act="cats"]')
    page.wait_for_selector("#dlg[open]")
    assert "categor" in page.inner_text("#dlg h2").lower()
    page.keyboard.press("Escape")
    wait_for(page, "!document.getElementById('dlg').open")


def test_the_chips_follow_a_change_made_elsewhere(shell, page, harness):
    open_page(shell, page)
    harness.db.set_setting("stats.categories", json.dumps({"custom": [{"key": "c_kids", "name": "Kids", "color": "#8E6CEF"}]}))
    page.evaluate("window.dispatchEvent(new CustomEvent('ld:categories-changed'))")
    wait_for(page, "[...document.querySelectorAll('.set-cat span')].some(e => e.textContent === 'Kids')")


# ================================================================ emergency unlock

def test_a_fewer_uses_change_saves_at_once_even_while_locked(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.select_option('.set-kind [data-k="k-uses"]', "2")
    assert eventually(lambda: kinds(harness)[0]["uses"] == 2)
    wait_for(page, f"document.querySelector('.set-meter').textContent.startsWith('{min(left_now(harness), 2)} of 2 left')")
    assert not page.evaluate("document.getElementById('dlg').open")


def test_more_uses_while_locked_asks_and_snaps_back_when_cancelled(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.select_option('.set-kind [data-k="k-uses"]', "6")
    cancel_challenge(page)
    assert kinds(harness)[0]["uses"] == 3
    wait_for(page, "document.querySelector('.set-kind [data-k=k-uses]').value === '3'")
    page.select_option('.set-kind [data-k="k-uses"]', "6")
    type_challenge(page)
    assert eventually(lambda: kinds(harness)[0]["uses"] == 6)
    wait_for(page, "/ of 6 left/.test(document.querySelector('.set-meter').textContent)")


def test_longer_and_another_period_ask(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.select_option('.set-kind [data-k="k-minutes"]', "45")
    cancel_challenge(page)
    page.select_option('.set-kind [data-k="k-per"]', "month")
    cancel_challenge(page)
    assert kinds(harness)[0]["minutes"] == 20 and kinds(harness)[0]["per"] == "week"


def test_turning_it_on_asks_and_turning_it_off_is_free(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.click('[data-k="em-on"]')                                     # off: free
    assert eventually(lambda: saved(harness, "emergency.enabled") == "0")
    page.click('[data-k="em-on"]')                                     # on: the challenge
    cancel_challenge(page)
    assert saved(harness, "emergency.enabled") == "0"
    wait_for(page, "!document.querySelector('[data-k=em-on]').checked")


def test_add_and_remove_a_kind(shell, page, harness):
    open_page(shell, page)
    assert page.locator('[data-k="em-new"]').is_hidden()
    page.click('[data-k="em-add"]')
    assert page.locator('[data-k="em-new"]').is_visible() and page.locator('[data-k="em-add"]').is_hidden()
    assert [page.input_value(f'[data-k="n-{f}"]') for f in ("uses", "per", "minutes")] == ["1", "week", "20"]
    page.select_option('[data-k="n-per"]', "month")
    page.select_option('[data-k="n-minutes"]', "60")
    page.click('[data-act="em-add-ok"]')
    wait_for(page, "document.querySelectorAll('.set-kind').length === 2")
    assert [(k["uses"], k["per"], k["minutes"]) for k in kinds(harness)] == [(3, "week", 20), (1, "month", 60)]
    assert page.locator('[data-k="em-new"]').is_hidden()
    assert kind_ui(page) == [["3", "week", "20"], ["1", "month", "60"]]
    # two clicks remove the second one
    x = page.locator(".set-kind").nth(1).locator(".confirm")
    x.click()
    assert x.inner_text() == "Remove?"
    x.click()
    wait_for(page, "document.querySelectorAll('.set-kind').length === 1")
    assert len(kinds(harness)) == 1
    assert kinds(harness)[0]["id"] == "t1"                              # (the first kind kept its id, and so its count)


def test_cancel_closes_the_new_kind_form(shell, page, harness):
    open_page(shell, page)
    page.click('[data-k="em-add"]')
    page.click('[data-act="em-add-cancel"]')
    assert page.locator('[data-k="em-new"]').is_hidden() and page.locator('[data-k="em-add"]').is_visible()
    assert len(kinds(harness)) == 1


def test_up_to_five_kinds_then_no_add_button(shell, page, harness):
    emergency.save_tiers(harness.db, [{"id": f"k{i}", "uses": 1, "per": "day", "minutes": 5} for i in range(5)])
    open_page(shell, page)
    assert page.locator(".set-kind").count() == 5 and page.locator('[data-k="em-add"]').is_hidden()


def test_a_new_kind_while_locked_asks_for_the_challenge(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.click('[data-k="em-add"]')
    page.click('[data-act="em-add-ok"]')
    type_challenge(page)
    assert eventually(lambda: len(kinds(harness)) == 2)


def test_the_meters_are_fresh_every_time_the_page_is_shown(shell, page, harness):
    open_page(shell, page)
    left = left_now(harness)
    assert page.inner_text(".set-meter").startswith(f"{left} of 3 left")
    page.click('#nav [data-nav="dashboard"]')
    page.wait_for_timeout(200)
    emergency.unlock(harness.db, [], harness.now, alerts=True, tier="t1")      # a use is spent elsewhere
    assert left_now(harness) == left - 1
    page.click('#nav [data-nav="settings"]')
    wait_for(page, f"document.querySelector('.set-meter').textContent.startsWith('{left - 1} of 3 left')")
    assert page.locator(".set-meter .uses i.used").count() == 3 - (left - 1)


# ================================================================ backup and export

def make_backup(harness, tmp_path, **change):
    d = backup.export(harness.db)
    d.update(change)
    p = tmp_path / "mine.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def test_export_saves_where_the_owner_chose_and_says_so(shell, page, harness, tmp_path, monkeypatch):
    target = tmp_path / "out.json"
    monkeypatch.setattr(api_settings, "_ask_save_path", lambda window, name: str(target))
    open_page(shell, page)
    page.click('[data-act="export"]')
    wait_for(page, "document.querySelector('[data-k=backup-msg]').textContent.startsWith('Saved to')")
    assert str(target) in page.inner_text('[data-k="backup-msg"]')
    assert json.loads(target.read_text(encoding="utf-8"))["lockdown"] == backup.FORMAT


def test_a_cancelled_export_says_nothing(shell, page, harness, monkeypatch):
    monkeypatch.setattr(api_settings, "_ask_save_path", lambda window, name: None)
    open_page(shell, page)
    page.click('[data-act="export"]')
    page.wait_for_timeout(300)
    assert page.inner_text('[data-k="backup-msg"]') == ""
    assert not page.is_disabled('[data-act="export"]')


def test_import_lists_what_changes_then_replaces_everything(shell, page, harness, tmp_path, monkeypatch):
    before = len(harness.db.list_items())
    assert before > 3
    monkeypatch.setattr(api_settings, "_ask_open_path", lambda window: str(make_backup(harness, tmp_path, items=[])))
    open_page(shell, page)
    page.click('[data-act="import"]')
    page.wait_for_selector("#dlg[open] [data-k=import-lines] li")
    assert page.inner_text("#dlg h2") == "Import these settings?"
    lines = page.locator("[data-k=import-lines] li").all_inner_texts()
    assert any(l.startswith(f"Blocked sites & apps: {before} now -> 0 after") for l in lines)
    assert "mine.json" in page.inner_text("#dlg .dlg-b")
    assert len(harness.db.list_items()) == before                       # nothing is changed by looking
    page.click("#dlg .dlg-f .btn-primary")
    wait_for(page, "document.querySelector('[data-k=backup-msg]').textContent.startsWith('Imported')")
    assert f"Imported 0 blocked sites / apps" in page.inner_text('[data-k="backup-msg"]')
    assert harness.db.list_items() == []


def test_cancel_in_the_import_dialog_changes_nothing(shell, page, harness, tmp_path, monkeypatch):
    before = len(harness.db.list_items())
    monkeypatch.setattr(api_settings, "_ask_open_path", lambda window: str(make_backup(harness, tmp_path, items=[])))
    open_page(shell, page)
    page.click('[data-act="import"]')
    page.wait_for_selector("#dlg[open] [data-k=import-lines]")
    page.click("#dlg .dlg-f button:has-text('Cancel')")
    dialog_closed(page)
    assert len(harness.db.list_items()) == before
    assert harness.rpc_count("backup_import_apply") == 0


def test_import_always_asks_for_the_challenge_while_locked(shell, page, harness, tmp_path, monkeypatch):
    before = len(harness.db.list_items())
    monkeypatch.setattr(api_settings, "_ask_open_path", lambda window: str(make_backup(harness, tmp_path, items=[])))
    lock(harness)
    open_page(shell, page)
    page.click('[data-act="import"]')
    page.wait_for_selector("#dlg[open] [data-k=import-lines]")
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert len(harness.db.list_items()) == before
    cancel_challenge(page)
    assert len(harness.db.list_items()) == before
    page.click('[data-act="import"]')
    page.wait_for_selector("#dlg[open] [data-k=import-lines]")
    page.click("#dlg .dlg-f .btn-primary")
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-k=backup-msg]').textContent.startsWith('Imported')")
    assert harness.db.list_items() == []


def test_an_import_applies_the_backups_look_at_once(shell, page, harness, tmp_path, monkeypatch):
    d = backup.export(harness.db)
    d["settings"]["ui.accent"] = "#12948A"
    p = tmp_path / "look.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setattr(api_settings, "_ask_open_path", lambda window: str(p))
    open_page(shell, page)
    page.click('[data-act="import"]')
    page.wait_for_selector("#dlg[open] [data-k=import-lines]")
    page.click("#dlg .dlg-f .btn-primary")
    wait_for(page, "getComputedStyle(document.documentElement).getPropertyValue('--brand').trim().toUpperCase() === '#12948A'")


def test_a_file_that_is_not_a_backup_says_so_and_changes_nothing(shell, page, harness, tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("hello", encoding="utf-8")
    before = len(harness.db.list_items())
    monkeypatch.setattr(api_settings, "_ask_open_path", lambda window: str(bad))
    open_page(shell, page)
    page.click('[data-act="import"]')
    wait_for(page, "document.querySelector('[data-k=backup-msg]').textContent.length > 0")
    assert page.locator('[data-k="backup-msg"].bad').count() == 1
    assert len(harness.db.list_items()) == before and not page.evaluate("document.getElementById('dlg').open")


def test_the_screen_time_csv_says_how_many_rows(shell, page, harness, tmp_path, monkeypatch):
    target = tmp_path / "st.csv"
    monkeypatch.setattr(screentime, "_ask_save_path", lambda window, name: str(target))
    open_page(shell, page)
    page.click('[data-act="csv"]')
    wait_for(page, "/rows saved to/.test(document.querySelector('[data-k=backup-msg]').textContent)")
    msg = page.inner_text('[data-k="backup-msg"]')
    assert msg.endswith(str(target)) and int(msg.split()[0]) > 0
    assert target.read_text(encoding="utf-8").startswith("date,app,site,minutes")


# ================================================================ no way back to the old window (2026-10-12)

def test_there_is_no_new_or_old_window_switch(shell, page, harness):
    harness.db.set_setting("ui.engine", "legacy")                       # (an old choice is ignored)
    open_page(shell, page)
    text = page.inner_text(".set")
    for gone in ("New window", "Use the new window", "old window", "Preview"):
        assert gone not in text, gone
    assert page.locator('[data-k="engine"], [data-card="engine"]').count() == 0


# ================================================================ looks

@pytest.mark.parametrize("theme", ["light", "dark", "amoled"])
def test_clean_console_in_every_theme(shell, page, harness, theme):
    harness.db.set_setting("ui.theme", theme)
    open_page(shell, page)
    assert page.evaluate("document.documentElement.dataset.theme") == theme
    assert page.evaluate("document.getElementById('view').scrollWidth <= document.getElementById('view').clientWidth")


def test_it_fits_a_small_window_and_scaled_down(shell, page, harness):
    harness.db.set_setting("ui.scale", "70")
    shell(width=1000, height=650, route="settings")
    page.wait_for_selector('[data-card="emergency"] .set-kind', timeout=8000)
    assert page.evaluate("document.getElementById('view').scrollWidth <= document.getElementById('view').clientWidth")
