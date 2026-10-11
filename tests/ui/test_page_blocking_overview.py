"""R18: Blocking page shell + Overview + save bar + Emergency unlock dialog (js/pages/blocking.js, blocking/overview.js,
emergency.js, draft.js) in Chrome against the REAL Python Api on a temporary database (see harness.py).

What is checked: the list (rows, group boxes, statuses, the Disabled section), the menus and two-click Remove, that a
removal while Anti-Bypass is locked opens the challenge and the row stays, the save bar's "what changed" and Discard,
filter / search on 200 rows, live pushes, the emergency dialog (kinds, items, errors, the success notice), the Locked strip,
the entry points, and the numbers (first open, search, scroll, nothing running while hidden).

What can only be proven on Windows: the page inside WebView2 (this is Chrome), real app / site icons."""
from datetime import timedelta

import pytest

import antibypass as ab
import emergency
from harness import NOW, UiHarness
from rules import make_schedule

HOURS_NOW = make_schedule("block", [(list(range(7)), "15:00", "18:00")])
HOURS_LATER = make_schedule("block", [(list(range(7)), "20:00", "22:00")])


def wait_for(page, js, timeout=6000):
    page.wait_for_function(js, timeout=timeout)


def open_page(shell, page, **kw):
    shell(route="blocking", **kw)
    page.wait_for_selector(".bo-row", timeout=8000)
    return page


LIVE = "#bo-list > .vl-row:not([hidden])"
BY_PLACE = "els => els.sort((a, b) => a._i - b._i)"          # (the nodes of a virtual list are moved, not re-ordered in the DOM)


def names(page):
    """The names in the rows that are in view, top to bottom."""
    return page.eval_on_selector_all(LIVE, f"els => ({BY_PLACE})(els).map(e => e.querySelector('.name b').textContent)")


def listed(name):
    """JS: is a row called `name` in view?"""
    return f"[...document.querySelectorAll('{LIVE}')].some(r => r.querySelector('.name b').textContent === '{name}')"


def row(page, name):
    return page.locator(f'{LIVE}:has(.name b:text-is("{name}"))').first


def kinds_of_rows(page):
    return page.eval_on_selector_all(LIVE, f"els => ({BY_PLACE})(els).map(e => e.dataset.t)")


def status_of(page, name):
    return row(page, name).locator(".bo-st .chip span").inner_text()


def lock(harness, **kw):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "length": 30, **kw})


def autosave(harness, on):
    harness.db.set_setting("ui.autosave", "1" if on else "0")


def item_id(harness, name):
    return next(i["id"] for i in harness.db.list_items() if i["display_name"] == name)


def saved_names(harness):
    return sorted(i["display_name"] for i in harness.db.list_items())


def dialog_open(page):
    return page.evaluate("document.getElementById('dlg').open")


def dialog_closed(page, timeout=8000):
    wait_for(page, "!document.getElementById('dlg').open", timeout)


def type_challenge(page):
    page.wait_for_selector("#dlg[open] textarea.typed", timeout=5000)
    text = page.evaluate("document.querySelector('.ch-phrase').textContent")
    page.focus("#dlg textarea.typed")
    page.keyboard.type(text, delay=45)
    page.keyboard.press("Enter")
    dialog_closed(page)


def settle(page):
    """Let the page finish what a click started (a call and its reload)."""
    page.evaluate("() => new Promise((r) => setTimeout(r, 250))")


def drain(harness, page):
    """Deliver what Python pushed (the agent's Publisher does this in the real app)."""
    pushed, harness.pushed[:] = list(harness.pushed), []
    for event, payload in pushed:
        page.evaluate("([e, p]) => window.ld.push([[e, p]])", [event, payload])


@pytest.fixture
def pushes(harness):
    """What Python pushes to the page is collected here (the agent's Publisher delivers it in the real app)."""
    harness.pushed = []
    harness.ctx.publish = lambda event, payload=None, key=None: harness.pushed.append((event, payload))
    return harness.pushed


# ================================================================ the page and its list

def test_the_page_opens_on_overview_with_the_list(shell, page, harness):
    open_page(shell, page)
    assert page.inner_text(".bk .page-h h1") == "Blocking"
    assert page.eval_on_selector_all('.bk .tabs [role="tab"]', "els => els.map(e => e.textContent)") == \
        ["Overview", "Groups", "Add", "Site protection", "Calendar"]
    assert page.get_attribute('[data-tab="overview"]', "aria-selected") == "true"
    assert page.inner_text('[data-k="title"]') == "Everything blocked (6)"
    assert "Fun" in names(page) and "Discord" in names(page) and "Steam" in names(page)
    # a group is one box: its header, then its members; the members are not loose rows
    kinds = kinds_of_rows(page)
    assert kinds.count("ghead") == 1 and kinds.count("member") == 3
    assert kinds.index("ghead") + 1 == kinds.index("member")
    assert row(page, "Fun").locator(".name span").inner_text() == "group · 3 members"
    assert page.locator('.vl-row[data-t="member"]:not([hidden]) .name b').all_inner_texts() == ["Reddit", "Twitch", "YouTube"]
    # the loose rows carry what Python worked out
    assert status_of(page, "X") == "Blocked"
    assert "Temporary" in row(page, "Discord").locator(".bo-chips").inner_text()
    assert "Edit" in row(page, "Discord").inner_text() and "Remove" in row(page, "Discord").inner_text()
    assert row(page, "Discord").locator(".name span").inner_text().startswith("app · discord.exe")
    # statuses: a member that is blocked says so once
    assert status_of(page, "Twitch").startswith("Blocked (until 17:00")
    assert status_of(page, "Fun") == "1 of 3 blocked now"
    # what has no PC backend is not there
    for text in ("Grey screen", "Mute it", "Island"):
        assert page.locator(f"text={text}").count() == 0


def test_the_list_is_a_virtual_list(shell, page, harness):
    open_page(shell, page)
    assert page.evaluate("document.querySelector('#bo-list').classList.contains('vlist')")


def test_empty_state(shell, page, harness):
    for item in harness.db.list_items():
        harness.db.remove_item(item["id"])
    for group in harness.db.list_groups():
        harness.db.remove_group(group["id"])
    shell(route="blocking")
    page.wait_for_selector('[data-k="empty"]:not([hidden])', timeout=8000)
    assert "Nothing blocked yet" in page.inner_text('[data-k="empty"]') and "use + Add" in page.inner_text('[data-k="empty"]')
    assert page.is_hidden('[data-k="card"]')
    page.click('[data-k="add2"]')
    page.wait_for_selector('[data-tab="add"][aria-selected="true"]')


def test_the_other_tabs_are_coming_soon_and_overview_is_shown_again_on_return(shell, page, harness):
    open_page(shell, page)
    page.click('[data-tab="groups"]')
    page.wait_for_selector('[data-pane="groups"]:not([hidden])')
    assert "Coming soon" in page.inner_text('[data-pane="groups"]')
    assert page.is_hidden('[data-pane="overview"]')
    page.click('#nav [data-nav="dashboard"]')
    page.click('#nav [data-nav="blocking"]')
    page.wait_for_selector('[data-pane="overview"]:not([hidden])')
    assert page.get_attribute('[data-tab="overview"]', "aria-selected") == "true"        # always Overview again


def test_the_tabs_follow_the_arrow_keys(shell, page, harness):
    open_page(shell, page)
    page.focus('[data-tab="overview"]')
    page.keyboard.press("ArrowRight")
    assert page.get_attribute('[data-tab="groups"]', "aria-selected") == "true"
    page.keyboard.press("ArrowLeft")
    assert page.get_attribute('[data-tab="overview"]', "aria-selected") == "true"


# ================================================================ the disabled ones, sorting

def test_disabled_items_and_groups_are_listed_last(shell, page, harness):
    autosave(harness, True)
    harness.db.write("UPDATE blocked_items SET disabled = 1 WHERE display_name = 'Steam'")
    open_page(shell, page)
    assert "Steam" not in names(page)[:-3]
    kinds = kinds_of_rows(page)
    assert kinds[-2:] == ["dhead", "drow"]
    assert page.locator('.vl-row[data-t="dhead"] .name b').text_content() == "Disabled (1)"
    assert page.locator('.vl-row[data-t="drow"] .name b').inner_text() == "Steam"
    assert page.inner_text('[data-k="title"]') == "Everything blocked (5)"


def test_the_sort_menu_remembers_the_choice(shell, page, harness):
    open_page(shell, page)
    assert page.inner_text('[data-k="sort"]') == "Blocked now first"
    page.click('[data-k="sortbtn"]')
    assert page.locator("#menu button").all_inner_texts() == ["Blocked now first", "Next block", "Date added", "Name"]
    page.click('#menu button:has-text("Name")')
    wait_for(page, "document.querySelector('[data-k=\"sort\"]').textContent === 'Name'")
    assert harness.db.get_setting("ui.blocking.sort") == "Name"
    order = [n for n in names(page) if n not in ("Reddit", "Twitch", "YouTube")]
    assert order == sorted(order, key=str.lower)
    # a fresh visit has the same order
    shell(route="blocking")
    page.wait_for_selector(".bo-row")
    assert page.inner_text('[data-k="sort"]') == "Name"


# ================================================================ filter and search

def test_the_filters_and_the_search(shell, page, harness):
    open_page(shell, page)
    page.click('[data-filter="groups"]')
    assert [n for n, k in zip(names(page), kinds_of_rows(page))] == ["Fun", "Reddit", "Twitch", "YouTube"]
    page.click('[data-filter="apps"]')
    assert names(page) == ["Discord", "Steam"]
    page.click('[data-filter="sites"]')
    assert "Discord" not in names(page) and "X" in names(page)
    page.click('[data-filter="block"]')
    assert "X" in names(page) and "Steam" not in names(page)
    page.click('[data-filter="all"]')
    page.fill('[data-k="search"]', "twitch")
    wait_for(page, "document.querySelectorAll('.vl-row:not([hidden])').length === 4")
    assert names(page) == ["Fun", "Reddit", "Twitch", "YouTube"]                      # a member matches: its whole box shows
    page.fill('[data-k="search"]', "zzzz")
    page.wait_for_selector('[data-k="nomatch"]:not([hidden])')
    page.fill('[data-k="search"]', "")
    wait_for(page, "document.querySelectorAll('.vl-row:not([hidden])').length > 4")
    assert page.is_hidden('[data-k="nomatch"]')


# ================================================================ removing, the draft, the save bar

def test_remove_is_two_clicks_and_saves_at_once_with_auto_save(shell, page, harness):
    autosave(harness, True)
    open_page(shell, page)
    btn = row(page, "Steam").locator(".confirm")
    btn.click()
    assert btn.inner_text() == "Confirm" and "armed" in btn.get_attribute("class")
    assert saved_names(harness).count("Steam") == 1
    btn.click()
    wait_for(page, "!" + listed("Steam"))
    assert "Steam" not in saved_names(harness)
    assert page.inner_text('[data-k="notice"]') == "✓ Steam removed"                    # (auto-save on: nothing to press)
    assert page.inner_text('[data-k="title"]') == "Everything blocked (5)"


def test_remove_with_auto_save_off_waits_for_save_and_the_save_bar_says_what_changed(shell, page, harness):
    autosave(harness, False)
    open_page(shell, page)
    assert page.is_hidden("#sb-discard") and page.is_disabled("#sb-save") and page.inner_text("#sb-save") == "Saved"
    row(page, "Steam").locator(".confirm").click()
    row(page, "Steam").locator(".confirm").click()
    wait_for(page, "!" + listed("Steam"))
    assert page.inner_text('[data-k="notice"]') == "✓ Steam removed - press Save changes to apply"
    assert saved_names(harness).count("Steam") == 1                                    # not applied yet
    wait_for(page, "document.querySelector('#sb-text').textContent.includes('Removed Steam')")
    assert page.inner_text("#sb-text") == "Unsaved changes · Removed Steam"
    assert page.is_visible("#sb-discard") and page.inner_text("#sb-save") == "Save changes" and not page.is_disabled("#sb-save")
    # Discard puts the row back and clears the bar
    page.click("#sb-discard")
    wait_for(page, listed("Steam"))
    wait_for(page, "document.querySelector('#sb-text').textContent === ''")
    assert page.is_hidden("#sb-discard") and page.inner_text("#sb-save") == "Saved"
    assert "Steam" in saved_names(harness)


def test_save_applies_the_waiting_change(shell, page, harness):
    autosave(harness, False)
    open_page(shell, page)
    row(page, "Steam").locator(".confirm").click()
    row(page, "Steam").locator(".confirm").click()
    wait_for(page, "document.querySelector('#sb-save').textContent === 'Save changes'")
    page.click("#sb-save")
    wait_for(page, "document.querySelector('#sb-save').textContent === 'Saved'")
    assert "Steam" not in saved_names(harness)


def test_remove_while_locked_opens_the_challenge_and_the_row_stays(shell, page, harness):
    autosave(harness, True)
    lock(harness)
    open_page(shell, page)
    row(page, "Steam").locator(".confirm").click()
    row(page, "Steam").locator(".confirm").click()
    page.wait_for_selector("#dlg[open] .ch-phrase", timeout=5000)
    assert "Remove Steam" in page.inner_text("#dlg .ch-changes")
    assert row(page, "Steam").is_visible() and "Steam" in saved_names(harness)         # the row stays while it asks
    page.keyboard.press("Escape")                                                      # cancel
    dialog_closed(page)
    settle(page)
    assert row(page, "Steam").is_visible() and "Steam" in saved_names(harness)
    assert page.is_hidden("#sb-discard")                                               # (auto-save: the edit was thrown away)
    # again, and this time type the phrase
    row(page, "Steam").locator(".confirm").click()
    row(page, "Steam").locator(".confirm").click()
    type_challenge(page)
    wait_for(page, "!" + listed("Steam"))
    assert "Steam" not in saved_names(harness)
    assert page.inner_text('[data-k="notice"]') == "✓ Steam removed"


def test_removing_a_group_and_disabling_it(shell, page, harness):
    autosave(harness, True)
    open_page(shell, page)
    head = row(page, "Fun")
    head.locator('[data-act="disable"]').click()
    wait_for(page, "document.querySelector('.vl-row[data-t=\"dhead\"]') !== null")
    assert page.inner_text('[data-k="notice"]') == "✓ Group Fun disabled"
    assert harness.db.list_groups()[0]["disabled"] == 1
    assert page.locator('.vl-row[data-t="drow"] .name b').all_inner_texts()[-1] == "Fun (group)"
    # members of a disabled group are loose rows with "→ Fun · disabled" on them
    assert "→ Fun · disabled" in row(page, "Twitch").locator(".bo-chips").inner_text()
    # edit it back on from the Disabled list: the Groups tab is asked to open that group
    page.locator('.vl-row[data-t="drow"]:has-text("Fun (group)") [data-act="edit"]').click()
    page.wait_for_selector('[data-tab="groups"][aria-selected="true"]')


def test_a_refused_change_shows_why(shell, page, harness):
    open_page(shell, page)
    got = page.evaluate("""async () => { const d = await import('/js/draft.js');
        const r = await d.draftCall('draft_remove_item', 99999); return [r === d.FAILED, document.getElementById('toast').textContent]; }""")
    assert got[0] is True and "That item is already gone." in got[1]


def test_remove_group_is_two_clicks(shell, page, harness):
    autosave(harness, True)
    harness.db.add_group("Spare", [{"rule_type": "permanent"}], {})
    open_page(shell, page)
    btn = row(page, "Spare").locator(".confirm")
    btn.click()
    btn.click()
    wait_for(page, "!" + listed("Spare"))
    assert [g["name"] for g in harness.db.list_groups()] == ["Fun"]
    assert page.inner_text('[data-k="notice"]') == "✓ Group Spare removed"


def test_a_reused_row_never_stays_armed(shell, page, harness):
    autosave(harness, True)
    open_page(shell, page)
    btn = row(page, "Steam").locator(".confirm")
    btn.click()
    assert "armed" in btn.get_attribute("class")
    page.click('[data-filter="groups"]')                                               # the nodes are reused for other rows
    page.click('[data-filter="all"]')
    assert page.locator(".vl-row .confirm.armed").count() == 0


# ================================================================ alerts and edit menus

def test_the_alerts_menu_sets_default_on_off(shell, page, harness):
    autosave(harness, True)
    open_page(shell, page)
    row(page, "Steam").locator(".bo-alerts").click()
    assert page.locator("#menu button").all_inner_texts() == ["Default", "On", "Off"]
    assert page.locator('#menu button:has-text("Default") .mk').count() == 1
    page.click('#menu button:has-text("Off")')
    wait_for(page, "[...document.querySelectorAll('.vl-row')].some(r => r.querySelector('.name b').textContent === 'Steam' && r.querySelector('.bo-alerts span').textContent === 'Off')")
    assert next(i for i in harness.db.list_items() if i["display_name"] == "Steam")["notify"] == "off"
    row(page, "Steam").locator(".bo-alerts").click()
    page.click('#menu button:has-text("Default")')
    wait_for(page, "[...document.querySelectorAll('.vl-row')].some(r => r.querySelector('.name b').textContent === 'Steam' && r.querySelector('.bo-alerts span').textContent === 'Default')")
    assert next(i for i in harness.db.list_items() if i["display_name"] == "Steam")["notify"] is None


def test_edit_opens_the_add_tab_and_a_member_click_does_too(shell, page, harness):
    open_page(shell, page)
    assert row(page, "Steam").locator('[data-act="edit"]').inner_text() == "Edit"
    row(page, "Steam").locator('[data-act="edit"]').click()
    page.wait_for_selector('[data-tab="add"][aria-selected="true"]')
    page.click('[data-tab="overview"]')
    page.wait_for_selector('[data-pane="overview"]:not([hidden])')
    row(page, "Twitch").click()                                                          # a member line
    page.wait_for_selector('[data-tab="add"][aria-selected="true"]')


def test_edit_with_groups_is_a_menu_with_the_groups(shell, page, harness):
    harness.db.write("UPDATE block_groups SET disabled = 1")           # its members are loose rows, still "in group Fun"
    open_page(shell, page)
    btn = row(page, "Twitch").locator('[data-act="edit"]')
    assert btn.inner_text() == "Edit ▾"
    btn.click()
    assert page.locator("#menu button").all_inner_texts() == ["Edit its blockers", "Edit group Fun"]
    page.click('#menu button:has-text("Edit group Fun")')
    page.wait_for_selector('[data-tab="groups"][aria-selected="true"]')


def test_the_add_button_and_help(shell, page, harness):
    open_page(shell, page)
    page.click('[data-k="help"]')
    assert page.inner_text("#dlg h2") == "How to read this list" and "red while it is blocking" in page.inner_text("#dlg")
    page.keyboard.press("Escape")
    dialog_closed(page)
    page.click('[data-k="add"]')
    page.wait_for_selector('[data-tab="add"][aria-selected="true"]')


# ================================================================ the Locked strip

def test_no_strip_when_nothing_is_locked(shell, page, harness):
    open_page(shell, page)
    assert page.is_hidden('[data-k="lock"]')


def test_the_locked_strip(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    assert page.is_visible('[data-k="lock"]') and "Locked" in page.inner_text('[data-k="lock"]')
    assert page.is_enabled('[data-k="lock"] button') and page.inner_text('[data-k="lock"] button') == "Unlock to edit"
    page.click('[data-k="lock"] button')
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"lock\"]').hidden")                 # unlocked: the strip goes


def test_the_locked_strip_when_closed_has_a_disabled_button(shell, page, harness):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "hours": True})
    open_page(shell, page)
    assert page.is_visible('[data-k="lock"]')
    assert "only during the allowed hours" in page.inner_text('[data-k="lock"]')
    assert page.is_disabled('[data-k="lock"] button')


# ================================================================ entry points

def test_the_entry_points_other_pages_use(shell, page, harness):
    shell(route="dashboard")
    page.wait_for_selector(".page-host")
    page.evaluate("""async () => { window.BK = await import('/js/pages/blocking.js'); }""")
    page.evaluate("window.BK.editItem(%d)" % item_id(harness, "Steam"))
    page.wait_for_selector('[data-tab="add"][aria-selected="true"]')
    assert page.get_attribute('#nav [data-nav="blocking"]', "aria-current") == "page"
    page.evaluate("window.BK.editGroup(5)")
    page.wait_for_selector('[data-tab="groups"][aria-selected="true"]')
    page.evaluate("window.BK.addPrefilled({site: ['Reddit', 'reddit.com']})")
    page.wait_for_selector('[data-tab="add"][aria-selected="true"]')
    page.evaluate("window.BK.confirm('Thing added')")
    assert page.inner_text('[data-k="notice"]') == "✓ Thing added"                     # (auto-save is on: nothing to press)
    page.evaluate("window.BK.confirm('Thing added', true)")
    assert page.inner_text('[data-k="notice"]') == "✓ Thing added"


def test_confirm_says_to_press_save_when_auto_save_is_off(shell, page, harness):
    autosave(harness, False)
    open_page(shell, page)
    page.evaluate("async () => { window.BK = await import('/js/pages/blocking.js'); }")
    page.evaluate("window.BK.confirm('Thing added')")
    assert page.inner_text('[data-k="notice"]') == "✓ Thing added - press Save changes to apply"
    page.evaluate("window.BK.confirm('Thing added', true)")
    assert page.inner_text('[data-k="notice"]') == "✓ Thing added"


def test_the_notice_goes_after_five_seconds(shell, page, harness):
    open_page(shell, page)
    page.evaluate("async () => { window.BK = await import('/js/pages/blocking.js'); window.BK.confirm('Gone soon'); }")
    assert page.is_visible('[data-k="notice"]')
    wait_for(page, "document.querySelector('[data-k=\"notice\"]').hidden", 7000)


# ================================================================ live updates

def test_pushes_reload_what_changed_and_nothing_else(shell, page, harness, push):
    autosave(harness, True)
    open_page(shell, page)
    settle(page)
    count = lambda: harness.rpc_count("blocking_overview_get")                        # noqa: E731
    n = count()
    push("draft", {"dirty": False, "autosave": True, "change_summary": "", "blocks": False})
    settle(page)
    assert count() == n                                                                # a settings edit: no reload
    push("draft", {"dirty": False, "autosave": True, "change_summary": "", "what": "saved"})
    settle(page)
    assert count() == n + 1
    push("blocks_changed", {"rev": 9})
    settle(page)
    assert count() == n + 2
    page.evaluate("""() => window.ld.push([["blocks_changed", {rev: 10}], ["draft", {dirty: false, autosave: true, change_summary: ""}]])""")
    settle(page)
    assert count() == n + 3                                                            # two pushes together reload once
    # a status push that does not touch the blocks changes nothing; one that does reloads (once per change)
    push("status", {"mode": None})
    settle(page)
    assert count() == n + 3
    push("status", {"blocks": {"count": 1, "total": 6, "next_change": None, "rev": 77}})
    settle(page)
    assert count() == n + 4
    push("status", {"blocks": {"count": 1, "total": 6, "next_change": None, "rev": 77}})
    settle(page)
    assert count() == n + 4
    push("status", {"running": False})                                              # the service stopped: "Pending · service off"
    settle(page)
    assert count() == n + 5
    push("status", {"pause": {"started": "2026-10-11T15:00:00", "until": "2026-10-11T16:00:00", "silent": False}})
    settle(page)
    assert count() == n + 6


def test_a_change_made_elsewhere_shows_up(shell, page, harness, push):
    open_page(shell, page)
    harness.db.write("UPDATE blocked_items SET display_name = 'Steam Deck' WHERE display_name = 'Steam'")
    push("blocks_changed", {"rev": 5})
    wait_for(page, listed("Steam Deck"))


def test_the_emergency_button_follows_the_setting(shell, page, harness, push):
    open_page(shell, page)
    assert page.is_visible('[data-k="emergency"]')
    push("status", {"emergency": {"enabled": False, "usable": False, "left": 0, "kinds": []}})
    wait_for(page, "document.querySelector('[data-k=\"emergency\"]').hidden")
    push("status", {"emergency": {"enabled": True, "usable": True, "left": 3, "kinds": []}})
    wait_for(page, "!document.querySelector('[data-k=\"emergency\"]').hidden")


def test_the_emergency_button_is_not_there_when_it_is_turned_off(shell, page, harness):
    harness.db.set_setting("emergency.enabled", "0")
    open_page(shell, page)
    assert page.is_hidden('[data-k="emergency"]')


def test_countdowns_tick_without_asking_python(shell, page, harness):
    harness.db.add_item("Late", ["late.example"], "site", "manual", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    twitch = next(i for i in harness.db.list_items() if i["display_name"] == "Twitch")
    emergency.unlock(harness.db, [twitch], NOW, tier=emergency.LEGACY_TIER)
    open_page(shell, page)
    assert status_of(page, "Twitch") == "Unlocked · 20m left"
    n = harness.rpc_count()
    page.wait_for_timeout(2600)
    assert harness.rpc_count() == n                                                     # no polling
    # the text is worked out from the end time on the page's own clock
    page.evaluate("""() => { const b = [...document.querySelectorAll('.vl-row')].find(r => r.querySelector('.name b').textContent === 'Twitch');
      b._row.e.status.until = '2026-10-11T15:50:00'; }""")
    wait_for(page, "[...document.querySelectorAll('.vl-row')].some(r => r.querySelector('.name b').textContent === 'Twitch' && r.querySelector('.bo-st .chip span').textContent === 'Unlocked · 7m left')", 3000)


# ================================================================ the emergency dialog

def emergency_open(page):
    page.click('[data-k="emergency"]')
    page.wait_for_selector("#dlg[open] .em-kind", timeout=5000)


def test_the_emergency_dialog_lists_kinds_and_what_can_be_unlocked(shell, page, harness):
    emergency.save_tiers(harness.db, [{"id": "a", "uses": 3, "per": "week", "minutes": 20},
                                      {"id": "b", "uses": 1, "per": "day", "minutes": 60}])
    open_page(shell, page)
    emergency_open(page)
    assert page.inner_text("#dlg h2") == "Emergency unlock"
    kinds = page.locator(".em-kind").all_inner_texts()
    assert "20 min" in kinds[0] and "3 of 3 left this week" in kinds[0] and "resets Mon 12 Oct 00:00" in kinds[0]
    assert "60 min" in kinds[1] and "1 of 1 left today" in kinds[1]
    assert page.is_checked('.em-kind input[value="a"]')                                 # the shortest one is chosen
    cand = harness.api.emergency_get_candidates()["data"]
    assert {"Twitch", "Discord"} <= {i["name"] for i in cand["blocked"]} and "X" not in {i["name"] for i in cand["blocked"] + cand["open"]}
    shown = page.eval_on_selector_all(".em-list .vl-row:not([hidden])", "els => els.sort((a, b) => a._i - b._i).map(e => e.querySelector('.cap').hidden ? e.querySelector('.name').textContent : '## ' + e.querySelector('.cap').textContent)")
    assert shown[0] == "## Blocked now" and "Twitch" in shown and "X" not in shown
    assert shown[: 1 + len(cand["blocked"])][1:] == [i["name"] for i in cand["blocked"]]
    assert "Permanently-blocked items can't be emergency-unlocked." in page.inner_text("#dlg")
    assert page.inner_text("#dlg .dlg-f .btn-primary") == "Unlock for 20 min"
    page.check('.em-kind input[value="b"]', force=True)
    assert page.inner_text("#dlg .dlg-f .btn-primary") == "Unlock for 60 min"


def test_unlock_two_clicks_one_use_and_the_success_notice(shell, page, harness):
    emergency.save_tiers(harness.db, [{"id": "a", "uses": 2, "per": "week", "minutes": 5}])
    open_page(shell, page)
    emergency_open(page)
    page.locator('.em-list .vl-row:not([hidden]):has(.name:text-is("Twitch")) input').check()
    btn = page.locator("#dlg .dlg-f .btn-primary")
    assert btn.inner_text() == "Unlock for 5 min"
    btn.click()
    assert btn.inner_text() == "Confirm - uses 1 of 2"
    btn.click()
    dialog_closed(page)
    wait_for(page, "document.querySelector('[data-k=\"notice\"]').textContent.includes('unlocked')")
    assert page.inner_text('[data-k="notice"]') == "✓ Twitch unlocked until 15:47"
    unlocks = harness.db.unlocks_since(NOW - timedelta(days=1))
    assert len(unlocks) == 1 and unlocks[0]["names"] == ["Twitch"] and unlocks[0]["until"] == NOW + timedelta(minutes=5)
    wait_for(page, "[...document.querySelectorAll('.vl-row')].some(r => r.querySelector('.name b').textContent === 'Twitch' && r.querySelector('.bo-st .chip span').textContent.startsWith('Unlocked'))")


def test_unlock_with_nothing_ticked_says_so_inline(shell, page, harness):
    open_page(shell, page)
    emergency_open(page)
    btn = page.locator("#dlg .dlg-f .btn-primary")
    btn.click()
    btn.click()
    wait_for(page, "document.querySelector('.em-err').textContent.length > 0")
    assert page.inner_text(".em-err") == "Tick at least one."
    assert dialog_open(page) and harness.db.unlocks_since(NOW - timedelta(days=1)) == []


def test_a_kind_with_no_uses_left_cannot_be_picked(shell, page, harness):
    emergency.save_tiers(harness.db, [{"id": "a", "uses": 1, "per": "week", "minutes": 5}])
    twitch = next(i for i in harness.db.list_items() if i["display_name"] == "Twitch")
    emergency.unlock(harness.db, [twitch], NOW, tier="a")
    open_page(shell, page)
    emergency_open(page)
    assert page.is_disabled('.em-kind input[value="a"]')
    assert "none left" in page.inner_text(".em-kind")
    assert page.locator("#dlg .dlg-f .btn-primary").count() == 0
    assert "No emergency unlocks left - next one Mon 12 Oct 00:00" in page.inner_text("#dlg .dlg-f")


def test_the_dialog_can_pause_the_alerts_too(shell, page, harness):
    open_page(shell, page)
    emergency_open(page)
    page.check('[data-k="alerts"]')
    btn = page.locator("#dlg .dlg-f .btn-primary")
    btn.click()
    btn.click()
    dialog_closed(page)
    wait_for(page, "document.querySelector('[data-k=\"notice\"]').textContent.includes('alerts paused')")
    assert page.inner_text('[data-k="notice"]') == "✓ Bedtime and break alerts paused until 16:02"
    assert emergency.alerts_paused_until(harness.db, NOW) is not None


def test_the_emergency_dialog_with_nothing_to_unlock(shell, page, harness):
    for item in harness.db.list_items():
        harness.db.write("UPDATE block_rules SET rule_type='permanent', schedule=NULL, temp_until=NULL, daily_limit_min=NULL, "
                         "weekly_limit_min=NULL, monthly_limit_min=NULL, daily_switch_limit=NULL, monthly_switch_limit=NULL WHERE item_id = ?", (item["id"],))
    harness.db.write("DELETE FROM group_rules")
    harness.db.write("INSERT INTO group_rules (group_id, rule_type) SELECT id, 'permanent' FROM block_groups")
    open_page(shell, page)
    emergency_open(page)
    assert "Nothing on your list can be unlocked right now." in page.inner_text("#dlg")
    assert "Permanently-blocked items can't be emergency-unlocked." in page.inner_text("#dlg")


# ================================================================ 200 items: virtual list, search, speed

@pytest.fixture
def big(browser, tmp_path):
    (tmp_path / "big").mkdir()
    h = UiHarness(tmp_path / "big", days=3, extra_items=200)
    for g in range(20):
        ids = [i["id"] for i in h.db.list_items() if i["display_name"].startswith("Site ")][g * 5:g * 5 + 5]
        h.db.add_group(f"Group {g:02d}", [{"rule_type": "time_limit", "daily_limit_min": 90}], {i: {} for i in ids})
    h.pushed = []
    from perfkit import open_page as new_page
    ctx, page = new_page(browser, h)
    yield h, page
    errors = list(page.errors)
    ctx.close()
    h.stop()
    assert not errors, errors


def test_200_items_are_a_few_dozen_rows_and_search_is_fast(big):
    harness, page = big
    from harness import open_shell
    open_shell(page, harness, route="blocking")
    page.wait_for_selector(".bo-row", timeout=10000)
    assert page.inner_text('[data-k="title"]') == "Everything blocked (206)"
    live = page.evaluate("document.querySelectorAll('#bo-list > .vl-row').length")
    assert live < 45, live                                                           # (a virtual list: not 300 rows)
    total = page.evaluate("document.querySelector('#bo-list').scrollHeight")
    assert total > 200 * 40
    # search: from the key press to the next frame, after the 120 ms debounce
    for text in ("site 17", "group 03", "x"):
        took = page.evaluate("""async (text) => {
            const box = document.querySelector('[data-k="search"]');
            box.value = text;
            const t0 = performance.now();
            box.dispatchEvent(new Event('input', { bubbles: true }));
            await new Promise((r) => setTimeout(r, 135));                              // the debounce (120 ms) has fired
            const t1 = performance.now();
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            return [performance.now() - t1, t1 - t0]; }""", text)
        print(f"search {text!r}: {took[0]:.1f} ms after the debounce")
        assert took[0] < 60, took
    page.fill('[data-k="search"]', "")
    for f in ("block", "low", "apps", "sites", "groups", "all"):
        ms = page.evaluate("""async (f) => { const t0 = performance.now(); document.querySelector(`[data-filter="${f}"]`).click();
            await new Promise((r) => requestAnimationFrame(() => r())); return performance.now() - t0; }""", f)
        assert ms < 60, (f, ms)


def test_scrolling_200_items_is_smooth(big, capsys):
    harness, page = big
    from harness import Perf, open_shell
    open_shell(page, harness, route="blocking")
    page.wait_for_selector(".bo-row", timeout=10000)
    r = Perf(page).scroll_frames("#bo-list", seconds=2.0, probe="#bo-list > .vl-row:not([hidden])")
    print(f"scroll 200 items: {r}")
    assert r["live_max"] < 45 and r["max_ms"] < 100


def test_the_first_open_is_fast(shell, page, harness, perf, capsys):
    shell(route="dashboard")
    page.wait_for_selector(".page-host")
    ms = perf.click_until('#nav [data-nav="blocking"]', ".bo-row", 4000)
    print(f"Blocking first open: {ms:.0f} ms")
    assert 0 < ms < 400
    page.click('#nav [data-nav="dashboard"]')
    ms = perf.click_to_frame('#nav [data-nav="blocking"]')
    print(f"Blocking re-show: {ms:.0f} ms")
    assert ms < 100


def test_nothing_runs_while_the_window_is_hidden(shell, page, harness, perf):
    open_page(shell, page)
    settle(page)
    perf.set_hidden(True)
    page.wait_for_timeout(300)
    perf.reset_timer_calls()
    n = len(harness.calls)
    page.wait_for_timeout(2500)
    assert sum(perf.timer_calls().values()) == 0 and len(harness.calls) == n
    perf.set_hidden(False)


# ================================================================ looks

@pytest.mark.parametrize("theme", ["dark", "amoled", "light"])
def test_no_console_errors_in_any_theme_and_a_narrow_window(shell, page, harness, theme):
    harness.db.set_setting("ui.theme", theme)
    open_page(shell, page, width=1000, height=650)
    page.evaluate("t => document.documentElement.dataset.theme = t", theme)
    assert page.locator(".bo-row").count() > 3
    page.click('[data-k="emergency"]')
    page.wait_for_selector("#dlg[open] .em-kind")
    page.keyboard.press("Escape")
    dialog_closed(page)
    page.set_viewport_size({"width": 800, "height": 600})
    page.wait_for_timeout(200)
    assert page.locator(".bo-row").count() > 3
