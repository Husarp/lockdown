"""R20: Blocking > Add / Edit (js/pages/blocking/add.js, edit.js, js/targetpicker.js, suggest.js, ways.js) in Chrome against the REAL
Python Api on a temporary database (see harness.py); the installed-apps catalog is a small fake, the icon routes a stub.

What is checked: the picker (suggestions with their 120 ms debounce, filter, list, Browse for a file, Category menu), the ways
for apps / sites / program files / categories, adding one or several things, Enter, merging into what is on the list, opening
Edit from Overview / "Block it..." / the existing target, editing (Save, Alerts, Disable / Enable, Remove, Also in groups),
that loosening an item while Anti-Bypass is locked opens the challenge and changes nothing until it is passed, mistakes shown
inline, no console errors, and the numbers (first open, typing, nothing running while hidden).

What can only be proven on Windows: the real Open dialog behind "Browse for a file..." (its path is faked here), real icons,
and the page inside WebView2 (this is Chrome)."""
import pytest

import antibypass as ab
from resident import catalog as app_catalog
from webapi import shared

APPS = [
    {"name": "Discord", "exe": "discord.exe", "path": r"C:\Apps\Discord\discord.exe", "steam": False},
    {"name": "Counter-Strike 2", "exe": "cs2.exe", "path": r"D:\Steam\steamapps\common\cs2\cs2.exe", "steam": True},
    {"name": "Spotify", "exe": "spotify.exe", "path": r"C:\Apps\spotify.exe", "steam": False},
    {"name": "Zebra Zone", "exe": "zebra.exe", "path": r"C:\Apps\zebra.exe", "steam": False},
]
SEARCH = ".ad-search input"
LIST = ".ad-list .vl-row:not([hidden])"
NOTICE = '[data-k="notice"]'


@pytest.fixture
def env(shell, page, harness):
    cat = app_catalog.AppCatalog(harness.db_path.parent / "cat.json", list_apps=lambda: APPS, running_now=lambda: {})
    cat.load_now()
    harness.ctx.catalog = cat
    page.set_default_timeout(8000)
    return harness


def open_add(shell, page, route="blocking"):
    shell(1280, 1000, route=route)
    page.wait_for_selector(".bo-row", timeout=8000)
    page.click('[data-tab="add"]')
    page.wait_for_selector(SEARCH)
    page.wait_for_selector(".ad-row")
    return page


def settle(page, ms=250):
    page.evaluate("(ms) => new Promise((r) => setTimeout(r, ms))", ms)


def lock(harness, **kw):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "length": 30, **kw})


def autosave(harness, on):
    harness.db.set_setting("ui.autosave", "1" if on else "0")


def items(harness):
    return {i["display_name"]: i for i in harness.db.list_items()}


def rule(harness, name, rule_type):
    return next((r for r in items(harness)[name]["rules"] if r["rule_type"] == rule_type), None)


def item_id(harness, name):
    return items(harness)[name]["id"]


def type_in_search(page, text):
    page.click(SEARCH)
    page.keyboard.type(text)


def pick_suggestion(page, text, name):
    """Type `text` and click the suggestion called `name`."""
    page.fill(SEARCH, "")
    type_in_search(page, text)
    page.wait_for_selector(f'.sg:not([hidden]) .sg-row:has(b:text-is("{name}"))')
    page.click(f'.sg-row:has(b:text-is("{name}"))')


def tick(page, rule_type):
    """Tick a blocker in the rail and open it (clicking its row does both)."""
    page.click(f'.rail-row[data-t="{rule_type}"] .rail-main')


def set_limit(page, text="30m", period="day"):
    page.fill(f'.rail-panel [data-f="{period}"]', text)


def submit(page):
    page.click('[data-k="ad-submit"]')


def pills(page):
    return page.eval_on_selector_all(".ad-pills .pill .ellip", "els => els.map(e => e.textContent)")


def pills_are(page, expected):
    """The pills (what is picked) - once they show `expected`: picking from a dialog or a file asks Python first, so they come a moment after."""
    try:
        page.wait_for_function("(want) => JSON.stringify([...document.querySelectorAll('.ad-pills .pill .ellip')].map((e) => e.textContent)) === JSON.stringify(want)",
                               arg=expected, timeout=4000)
    except Exception:
        pass
    return pills(page) == expected


def ticked_ways(page, group):
    return page.eval_on_selector_all(f'[data-g="{group}"] [data-flag]', "els => els.filter(e => e.checked).map(e => e.dataset.flag)")


def error(page):
    return page.inner_text('[data-k="ad-error"]') if page.is_visible('[data-k="ad-error"]') else ""


def error_shown(page):
    page.wait_for_function("document.querySelector('[data-k=\"ad-error\"]') && !document.querySelector('[data-k=\"ad-error\"]').hidden")
    return error(page)


def on_overview(page, timeout=6000):
    page.wait_for_function("document.querySelector('[data-tab=\"overview\"]').getAttribute('aria-selected') === 'true'", timeout=timeout)


def dialog_closed(page, timeout=8000):
    """Closed AND its result handled: `open` turns false at dialog.close(), but the page handles the result in the dialog's
    'close' event, a task later, and only then empties the dialog (see test_page_blocking_groups.dialog_closed)."""
    page.wait_for_function("(() => { const d = document.getElementById('dlg'); return !d.open && d.childElementCount === 0; })()", timeout=timeout)


def type_challenge(page):
    page.wait_for_selector("#dlg[open] textarea.typed", timeout=5000)
    text = page.evaluate("document.querySelector('.ch-phrase').textContent")
    page.focus("#dlg textarea.typed")
    page.keyboard.type(text, delay=45)
    page.keyboard.press("Enter")
    dialog_closed(page)


def cancel_challenge(page):
    page.wait_for_selector("#dlg[open] textarea.typed", timeout=5000)
    page.click("#dlg[open] .dlg-f button:has-text('Cancel')")
    dialog_closed(page)


def edit_via_overview(page, name):
    page.click('[data-tab="overview"]')
    page.wait_for_selector(".bo-row")
    row = page.locator(f'.vl-row:not([hidden]):has(.name b:text-is("{name}"))').first
    if row.get_attribute("data-t") == "member":                  # (a member of a group: its line opens it)
        row.click()
    else:
        row.locator('[data-act="edit"]').click()
    page.wait_for_selector('[data-k="ad-title"]:has-text("Edit")')


def go(page, opts_js):
    page.evaluate("async () => { const { router } = await import('/js/router.js'); router.go('blocking', %s); }" % opts_js)


# ================================================================ the tab

def test_the_tab_opens_with_the_picker_the_ways_and_the_rail(shell, page, env):
    open_add(shell, page)
    assert page.inner_text('[data-k="ad-title"]') == "Add a site, app or category"
    assert page.get_attribute(SEARCH, "placeholder") == "Search apps, or type a website like example.com"
    assert page.eval_on_selector_all('[data-seg="adkind"] button', "els => els.map(e => e.textContent)") == \
        ["All", "Installed apps", "Websites", "Program files"]
    assert page.inner_text("[data-browse]") == "Browse apps" and page.inner_text("[data-file]") == "Browse for a file…"
    assert page.is_visible("[data-cat]")
    assert page.inner_text(".rail-head [data-count]") == "0 of 5 on"
    assert page.inner_text('[data-k="ad-submit"]') == "+ Add" and page.is_visible('[data-k="ad-cancel"]')
    assert not page.is_visible(".ad-editacts")                      # (Alerts / Disable / Remove are for editing)
    assert "Tick apps and sites" in page.inner_text(".ad-sel")
    for text in ("Grey screen", "Mute it", "Drop a"):                # (no designer extras without a PC backend)
        assert page.locator(f"text={text}").count() == 0
    assert page.locator(LIST).count() >= 5                          # (the list starts with what is worth suggesting)
    assert page.evaluate("document.querySelector('.ad-list').classList.contains('vlist')")


# ================================================================ suggestions

def test_typing_shows_suggestions_one_frame_after_the_debounce(shell, page, env, capsys):
    open_add(shell, page)
    page.focus(SEARCH)
    got = page.evaluate("""async () => {
        const input = document.querySelector('.ad-search input'), sg = document.querySelector('.sg');
        const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
        await frame();
        input.value = 'redd'; input.dispatchEvent(new Event('input', {bubbles: true}));
        const t0 = performance.now();
        while (sg.hidden) { if (performance.now() - t0 > 3000) return -1; await frame(); }
        return performance.now() - t0; }""")
    with capsys.disabled():
        print(f"\nsuggestions after typing 'redd': {got:.0f} ms (120 ms debounce + one Api call + one frame)")
    assert 100 <= got <= 120 + 130                                  # the debounce, the call and one frame (a busy machine: slack)
    assert page.inner_text(".sg-h").lower() == "suggestions · “redd”"
    names = page.eval_on_selector_all(".sg-row:not([hidden]) b", "els => els.map(e => e.textContent)")
    assert names[0] == "Reddit" and len(names) <= 8
    assert page.inner_text(".sg-row .chip") == "site"
    assert "Steam games sit under Steam" in page.inner_text(".sg-f")
    assert not page.is_visible("[data-clear]")                       # (no "mine" entry shows)


def test_my_own_sites_show_clear_my_suggestions_and_it_is_ungated(shell, page, env):
    env.db.add_history("redwood.example", "Redwood")
    lock(env)
    open_add(shell, page)
    type_in_search(page, "redw")
    page.wait_for_selector(".sg:not([hidden]) .sg-row")
    assert page.is_visible("[data-clear]")
    page.click("[data-clear]")
    page.wait_for_function("document.querySelector('[data-clear]').hidden")
    assert env.db.history() == []                                    # (no challenge although Anti-Bypass is locked)
    assert not page.is_visible("#dlg[open]")


def test_apps_are_suggested_too_with_their_exe_and_steam(shell, page, env):
    open_add(shell, page)
    type_in_search(page, "count")
    page.wait_for_selector('.sg:not([hidden]) .sg-row:has(b:text-is("Counter-Strike 2"))')
    row = page.locator('.sg-row:has(b:text-is("Counter-Strike 2"))')
    assert "cs2.exe · Steam game" in row.inner_text() and "app" in row.inner_text()


def test_esc_hides_the_suggestions_and_so_does_leaving_the_box(shell, page, env):
    open_add(shell, page)
    type_in_search(page, "redd")
    page.wait_for_selector(".sg:not([hidden])")
    page.keyboard.press("Escape")
    assert page.evaluate("document.querySelector('.sg').hidden")
    type_in_search(page, "i")
    page.wait_for_selector(".sg:not([hidden])")
    page.click('[data-k="ad-title"]')                                   # (the box loses the focus)
    assert page.evaluate("document.querySelector('.sg').hidden")


def test_arrow_keys_and_enter_pick_a_suggestion(shell, page, env):
    open_add(shell, page)
    type_in_search(page, "redd")
    page.wait_for_selector(".sg:not([hidden]) .sg-row")
    page.keyboard.press("ArrowDown")
    assert page.get_attribute(".sg-row:not([hidden])", "aria-selected") == "true"
    page.keyboard.press("Enter")
    assert pills(page) == ["Reddit"]
    assert page.evaluate("document.querySelector('.sg').hidden") and page.input_value(SEARCH) == ""
    assert page.get_attribute('[data-tab="add"]', "aria-selected") == "true"      # (Enter picked it, it did not submit)


def test_picking_a_suggestion_selects_it_and_shows_its_ways_and_name(shell, page, env):
    open_add(shell, page)
    pick_suggestion(page, "redd", "Reddit")
    assert pills(page) == ["Reddit"] and page.input_value('[data-k="ad-name"]') == "Reddit"
    assert page.inner_text('[data-k="ad-seltitle"]') == "Adding 1"
    assert ticked_ways(page, "site") == ["dns"]
    assert page.is_visible('[data-g="site"]') and not page.is_visible('[data-g="app"]')
    page.click(".ad-pills [data-remove]")                              # the pill's x
    assert pills(page) == [] and not page.is_visible('[data-g="site"]')


# ================================================================ adding

def test_pick_tick_a_time_limit_and_add_it_appears_in_overview(shell, page, env):
    env.db.remove_item(item_id(env, "Reddit"))                         # (the synthetic list has a Reddit already)
    open_add(shell, page)
    pick_suggestion(page, "redd", "Reddit")
    tick(page, "time_limit")
    set_limit(page, "30m")
    assert page.inner_text(".rail-panel [data-sentence]") == "Reddit will be blocked: time limit (30m per day)."
    submit(page)
    on_overview(page)
    assert page.inner_text(NOTICE) == "✓ Reddit blocker added"
    saved = items(env)["Reddit"]
    assert saved["source"] == "popular" and {"reddit.com", "redd.it"} <= set(saved["target"].split())
    assert saved["block_type"] == "dns" and rule(env, "Reddit", "time_limit")["daily_limit_min"] == 30
    page.wait_for_selector('.vl-row:not([hidden]):has(.name b:text-is("Reddit"))')
    assert "30m" in page.locator('.vl-row:not([hidden]):has(.name b:text-is("Reddit"))').first.inner_text()
    page.click('[data-tab="add"]')                                     # the form is fresh again
    assert pills(page) == [] and page.inner_text(".rail-head [data-count]") == "0 of 5 on"


def test_enter_in_the_box_adds_the_typed_site(shell, page, env):
    open_add(shell, page)
    tick(page, "permanent")
    type_in_search(page, "https://www.Example.org/page")
    page.keyboard.press("Enter")
    on_overview(page)
    assert page.inner_text(NOTICE) == "✓ Example blocker added"
    saved = items(env)["Example"]
    assert saved["target"] == "example.org" and saved["source"] == "manual" and rule(env, "Example", "permanent")


def test_enter_in_the_name_box_submits_too(shell, page, env):
    open_add(shell, page)
    pick_suggestion(page, "snap", "Snapchat")
    tick(page, "permanent")
    page.fill('[data-k="ad-name"]', "My ghost")
    page.press('[data-k="ad-name"]', "Enter")
    on_overview(page)
    assert "My ghost" in items(env) and items(env)["My ghost"]["source"] == "popular"


def test_several_picks_get_the_same_blockers_and_one_notice(shell, page, env):
    open_add(shell, page)
    pick_suggestion(page, "face", "Facebook")
    pick_suggestion(page, "insta", "Instagram")
    assert pills(page) == ["Facebook", "Instagram"] and page.inner_text('[data-k="ad-seltitle"]') == "Adding 2"
    assert not page.is_visible('[data-k="ad-namewrap"]')                  # (a display name is for one target)
    tick(page, "time_limit")
    set_limit(page, "45m")
    submit(page)
    on_overview(page)
    assert page.inner_text(NOTICE) == "✓ Facebook, Instagram blockers added"
    assert rule(env, "Facebook", "time_limit")["daily_limit_min"] == 45 and rule(env, "Instagram", "time_limit")["daily_limit_min"] == 45


def test_ticking_rows_in_the_list_selects_them(shell, page, env):
    open_add(shell, page)
    page.click('.ad-row:has(b:text-is("Facebook")) input')
    page.click('.ad-row:has(b:text-is("Instagram")) input')
    assert pills(page) == ["Facebook", "Instagram"]
    page.click('.ad-row:has(b:text-is("Facebook")) input')               # untick
    assert pills(page) == ["Instagram"]


def test_the_filter_narrows_the_list(shell, page, env):
    open_add(shell, page)
    page.click('[data-seg="adkind"] button:has-text("Websites")')
    page.wait_for_function("document.querySelectorAll('.ad-list .vl-row:not([hidden])').length > 3 && "
                           "![...document.querySelectorAll('.ad-list .vl-row:not([hidden]) .chip')].some(c => c.textContent !== 'site' && c.textContent !== 'mine')")
    badges = page.eval_on_selector_all(f"{LIST} .chip", "els => [...new Set(els.map(e => e.textContent))]")
    assert set(badges) <= {"site", "mine"}
    page.click('[data-seg="adkind"] button:has-text("Installed apps")')
    page.wait_for_function("document.querySelectorAll('.ad-list .vl-row:not([hidden]) .chip').length === 4 && "
                           "![...document.querySelectorAll('.ad-list .vl-row:not([hidden]) .chip')].some(c => c.textContent === 'site')")
    names = page.eval_on_selector_all(f"{LIST} b", "els => els.map(e => e.textContent)")
    assert set(names) == {a["name"] for a in APPS}
    assert "4 apps" in page.inner_text(".ad-foot")
    page.click('[data-seg="adkind"] button:has-text("Program files")')
    page.wait_for_selector(".ad-empty:not([hidden])")
    assert "Browse for a file" in page.inner_text(".ad-empty")


def test_a_typed_site_gets_its_own_row_and_can_be_ticked(shell, page, env):
    open_add(shell, page)
    type_in_search(page, "newsite.example")
    page.wait_for_selector('.ad-row:has(b:text-is("Add website “newsite.example”"))')
    page.click('.ad-row:has(b:text-is("Add website “newsite.example”")) input')
    assert pills(page) == ["newsite.example"]


def test_nothing_ticked_and_nothing_picked_say_so_inline(shell, page, env):
    open_add(shell, page)
    submit(page)
    assert error(page) == "Pick a site, an app or a category first."
    pick_suggestion(page, "face", "Facebook")
    assert error(page) == ""                                              # (a change clears the line)
    submit(page)
    page.wait_for_selector('.rail-wrap > .rule-err[data-zone="rules"]:not([hidden])')
    assert page.inner_text('.rail-wrap > .rule-err[data-zone="rules"]') == "Tick at least one blocker."
    assert "Facebook" not in items(env)


def test_a_blocker_with_a_mistake_is_shown_under_its_field_and_nothing_is_added(shell, page, env):
    open_add(shell, page)
    pick_suggestion(page, "face", "Facebook")
    tick(page, "time_limit")
    set_limit(page, "banana")
    submit(page)
    page.wait_for_selector(".rail-panel .rule-err:not([hidden])")
    assert "banana" in page.inner_text(".rail-panel .rule-err:not([hidden])")
    assert "Facebook" not in items(env) and page.get_attribute('[data-tab="add"]', "aria-selected") == "true"


def test_windows_own_programs_are_refused_with_the_reason(shell, page, env):
    open_add(shell, page)
    tick(page, "permanent")
    type_in_search(page, "explorer.exe")
    page.keyboard.press("Enter")
    assert "part of Windows (or Lockdown) and can't be blocked" in error_shown(page)
    assert "Explorer" not in items(env) and pills(page) == ["explorer.exe"]       # (it stays picked: remove it, or fix and retry)


def test_something_that_is_not_a_site_is_refused_before_anything_is_picked(shell, page, env):
    open_add(shell, page)
    tick(page, "permanent")
    type_in_search(page, "hello world")
    page.keyboard.press("Enter")
    assert "Write a site like reddit.com" in error_shown(page) and pills(page) == []


def test_cancel_goes_back_to_overview_and_forgets_the_form(shell, page, env):
    open_add(shell, page)
    pick_suggestion(page, "face", "Facebook")
    tick(page, "permanent")
    page.click('[data-k="ad-cancel"]')
    on_overview(page)
    page.click('[data-tab="add"]')
    assert pills(page) == [] and page.inner_text(".rail-head [data-count]") == "0 of 5 on"
    assert "Facebook" not in items(env)


# ================================================================ existing things: merge / open

def test_adding_what_is_on_the_list_with_blockers_merges_them(shell, page, env):
    open_add(shell, page)
    pick_suggestion(page, "twit", "Twitch")
    tick(page, "permanent")
    submit(page)
    on_overview(page)
    assert page.inner_text(NOTICE) == "✓ Twitch was already on the list - blockers merged"
    assert {r["rule_type"] for r in items(env)["Twitch"]["rules"]} == {"scheduled", "permanent"}   # (what it had stays)
    assert len([i for i in env.db.list_items() if i["display_name"] == "Twitch"]) == 1


def test_adding_what_is_on_the_list_with_no_blocker_opens_it_for_editing(shell, page, env):
    open_add(shell, page)
    pick_suggestion(page, "twit", "Twitch")
    submit(page)                                                         # (nothing ticked)
    page.wait_for_selector('[data-k="ad-title"]:has-text("Edit Twitch")')
    assert "already on the list - its blockers are loaded" in page.inner_text('[data-k="ad-info"]')
    assert page.inner_text(".rail-head [data-count]") == "1 of 5 on" and page.is_visible(".ad-editacts")
    assert not page.is_visible(".ad-pick")                               # (the picker is for adding)


def test_a_merge_with_a_looser_rule_while_locked_asks_and_changes_nothing_until_passed(shell, page, env):
    lock(env)
    open_add(shell, page)
    pick_suggestion(page, "twit", "Twitch")
    tick(page, "scheduled")                                              # (replaces its hours: allow-only 09:00-17:00 blocks less)
    submit(page)
    page.wait_for_selector("#dlg[open] textarea.typed")
    assert "Loosen Twitch" in page.inner_text("#dlg")
    before = rule(env, "Twitch", "scheduled")["schedule"]
    cancel_challenge(page)
    assert rule(env, "Twitch", "scheduled")["schedule"] == before
    assert page.get_attribute('[data-tab="add"]', "aria-selected") == "true"        # (the form stays: nothing was lost)
    assert "Twitch" in pills(page)


# ================================================================ apps and ways

def test_an_app_from_the_list_has_close_and_a_steam_game_also_its_background(shell, page, env):
    open_add(shell, page)
    page.click('[data-seg="adkind"] button:has-text("Installed apps")')
    page.wait_for_selector('.ad-row:has(b:text-is("Discord"))')
    page.click('.ad-row:has(b:text-is("Discord")) input')
    assert ticked_ways(page, "app") == ["close"] and page.is_visible('[data-g="app"]')
    page.click('.ad-row:has(b:text-is("Discord")) input')                  # untick
    page.click('.ad-row:has(b:text-is("Counter-Strike 2")) input')
    assert ticked_ways(page, "app") == ["close", "background"]


def test_close_and_minimize_exclude_each_other_and_background_goes_with_close(shell, page, env):
    open_add(shell, page)
    page.click('[data-seg="adkind"] button:has-text("Installed apps")')
    page.wait_for_selector('.ad-row:has(b:text-is("Discord"))')
    page.click('.ad-row:has(b:text-is("Discord")) input')
    page.check('[data-g="app"] [data-flag="background"]')
    assert ticked_ways(page, "app") == ["close", "background"]
    page.check('[data-g="app"] [data-flag="minimize"]')
    assert ticked_ways(page, "app") == ["minimize"]                        # (Close and its background processes went)
    assert page.is_disabled('[data-g="app"] [data-flag="background"]')
    page.check('[data-g="app"] [data-flag="close"]')
    assert ticked_ways(page, "app") == ["close"] and not page.is_disabled('[data-g="app"] [data-flag="background"]')
    page.check('[data-g="app"] [data-flag="internet"]')
    assert ticked_ways(page, "app") == ["close", "internet"]
    page.uncheck('[data-g="app"] [data-flag="close"]')
    assert ticked_ways(page, "app") == ["internet"]
    assert page.get_attribute('[data-g="app"] [data-w="close"] .way-help', "title")      # the "?" tooltips


def test_grey_it_is_offered_for_apps_and_categories_and_goes_with_neither_close_nor_minimize(shell, page, env):
    open_add(shell, page)
    page.click('[data-seg="adkind"] button:has-text("Installed apps")')
    page.wait_for_selector('.ad-row:has(b:text-is("Discord"))')
    page.click('.ad-row:has(b:text-is("Discord")) input')
    assert page.eval_on_selector_all('[data-g="app"] [data-flag]', "els => els.map(e => e.dataset.flag)") == \
        ["close", "background", "minimize", "grey", "internet"]
    assert page.inner_text('[data-g="app"] [data-w="grey"] .ellip') == "Grey it"
    help_text = page.get_attribute('[data-g="app"] [data-w="grey"] .way-help', "title")
    assert "goes grey" in help_text and "Color filters" in help_text and "separate" in help_text      # (Windows' own colour filters)
    page.check('[data-g="app"] [data-flag="grey"]')
    assert ticked_ways(page, "app") == ["grey"]                            # (Close went: it covers Grey it)
    assert page.is_disabled('[data-g="app"] [data-flag="background"]')
    page.check('[data-g="app"] [data-flag="internet"]')
    assert ticked_ways(page, "app") == ["grey", "internet"]
    page.check('[data-g="app"] [data-flag="minimize"]')
    assert ticked_ways(page, "app") == ["minimize", "internet"]
    page.check('[data-g="app"] [data-flag="grey"]')
    assert ticked_ways(page, "app") == ["grey", "internet"]
    page.check('[data-g="app"] [data-flag="close"]')
    assert ticked_ways(page, "app") == ["close", "internet"]


def test_a_greyed_app_is_saved_with_its_way(shell, page, env):
    open_add(shell, page)
    page.click('[data-seg="adkind"] button:has-text("Installed apps")')
    page.wait_for_selector('.ad-row:has(b:text-is("Spotify"))')
    page.click('.ad-row:has(b:text-is("Spotify")) input')
    page.check('[data-g="app"] [data-flag="grey"]')
    tick(page, "time_limit")
    set_limit(page, "1h")
    submit(page)
    on_overview(page)
    assert items(env)["Spotify"]["block_type"] == "grey"
    edit_via_overview(page, "Spotify")
    assert ticked_ways(page, "app") == ["grey"]


def test_switching_an_app_to_grey_while_locked_asks_for_the_challenge_and_back_to_close_is_free(shell, page, env):
    lock(env)
    open_add(shell, page)
    edit_via_overview(page, "Discord")
    page.check('[data-g="app"] [data-flag="grey"]')
    submit(page)
    page.wait_for_selector("#dlg[open] textarea.typed")
    assert "Loosen Discord" in page.inner_text("#dlg")
    assert items(env)["Discord"]["block_type"] in (None, "close")             # (nothing applied while the challenge is open)
    cancel_challenge(page)
    assert items(env)["Discord"]["block_type"] in (None, "close") and ticked_ways(page, "app") == ["grey"]    # (the form is kept)
    submit(page)
    type_challenge(page)
    on_overview(page)
    assert items(env)["Discord"]["block_type"] == "grey"
    edit_via_overview(page, "Discord")
    page.check('[data-g="app"] [data-flag="close"]')                           # grey -> close: stronger, free
    submit(page)
    on_overview(page)
    assert items(env)["Discord"]["block_type"] == "close" and not page.is_visible("#dlg[open]")


def test_a_sites_close_the_tab_and_go_back_exclude_each_other(shell, page, env):
    open_add(shell, page)
    pick_suggestion(page, "face", "Facebook")
    page.check('[data-g="site"] [data-flag="close"]')
    assert ticked_ways(page, "site") == ["dns", "close"]
    page.check('[data-g="site"] [data-flag="back"]')
    assert ticked_ways(page, "site") == ["dns", "back"]
    for flag in ("dns", "back"):
        page.uncheck(f'[data-g="site"] [data-flag="{flag}"]')
    submit(page)
    assert error_shown(page) == "Tick at least one of Can't load it / Close the tab / Go back."


def test_an_app_is_added_with_its_ways_and_the_path_from_the_list(shell, page, env):
    open_add(shell, page)
    page.click('[data-seg="adkind"] button:has-text("Installed apps")')
    page.wait_for_selector('.ad-row:has(b:text-is("Spotify"))')
    page.click('.ad-row:has(b:text-is("Spotify")) input')
    page.check('[data-g="app"] [data-flag="internet"]')
    tick(page, "permanent")
    submit(page)
    on_overview(page)
    saved = items(env)["Spotify"]
    assert saved["item_type"] == "app" and saved["target"] == "spotify.exe" and saved["block_type"] == "close,internet"
    assert saved["app_path"] == r"C:\Apps\spotify.exe" and saved["source"] == "app-browser"


def test_browse_apps_picks_an_app_and_a_steam_game_gets_its_defaults(shell, page, env):
    open_add(shell, page)
    page.click("[data-browse]")
    page.wait_for_selector("#dlg[open] .ab-row")
    page.click('#dlg .ab-row:has(b:text-is("Discord"))')
    dialog_closed(page)
    assert pills_are(page, ["Discord"]) and ticked_ways(page, "app") == ["close"]
    page.click(".ad-pills [data-remove]")
    page.click("[data-browse]")
    page.wait_for_selector("#dlg[open] .ab-row")
    page.click('#dlg .ab-row:has(b:text-is("Counter-Strike 2"))')
    dialog_closed(page)
    assert pills_are(page, ["Counter-Strike 2"])
    page.wait_for_function("document.querySelector('[data-g=app] [data-flag=background]').checked")    # (it asked the catalog: a Steam game)
    assert ticked_ways(page, "app") == ["close", "background"]
    tick(page, "permanent")
    submit(page)
    on_overview(page)
    saved = items(env)["Counter-Strike 2"]
    assert saved["block_type"] == "close,background" and saved["app_path"] == r"D:\Steam\steamapps\common\cs2\cs2.exe"


def test_a_steam_game_is_added_closed_with_its_background_processes(shell, page, env):
    open_add(shell, page)
    pick_suggestion(page, "counter", "Counter-Strike 2")
    assert ticked_ways(page, "app") == ["close", "background"]
    tick(page, "permanent")
    submit(page)
    on_overview(page)
    assert items(env)["Counter-Strike 2"]["block_type"] == "close,background"


def test_browse_for_a_file_picks_a_java_game_with_only_close_or_minimize(shell, page, env, monkeypatch):
    monkeypatch.setattr(shared, "_ask_path", lambda w: r"C:\Games\Block\game.jar")
    monkeypatch.setattr("monitor.applist.program_file", lambda p, *a: p, raising=False)
    open_add(shell, page)
    page.click("[data-file]")
    page.wait_for_selector(".ad-pills .pill")
    assert pills(page) == ["game"] and page.input_value('[data-k="ad-name"]') == "game"
    assert page.is_visible('[data-g="file"]') and not page.is_visible('[data-g="app"]')
    assert page.eval_on_selector_all('[data-g="file"] [data-flag]', "els => els.map(e => e.dataset.flag)") == ["close", "minimize"]
    assert "javaw.exe" in page.inner_text('[data-g="file"] .ways-note')
    page.check('[data-g="file"] [data-flag="minimize"]')
    assert ticked_ways(page, "file") == ["minimize"]
    tick(page, "permanent")
    submit(page)
    on_overview(page)
    saved = items(env)["game"]
    assert saved["target"] == "game.jar" and saved["block_type"] == "minimize" and saved["app_path"] == r"C:\Games\Block\game.jar"
    page.click('[data-tab="add"]')                                       # it is listed under "Program files" afterwards
    page.click('[data-seg="adkind"] button:has-text("Program files")')
    page.wait_for_selector('.ad-row:has(b:text-is("game"))')


def test_a_whole_interpreter_is_asked_about_first(shell, page, env, monkeypatch):
    monkeypatch.setattr(shared, "_ask_path", lambda w: r"C:\Java\bin\java.exe")
    monkeypatch.setattr("monitor.applist.program_file", lambda p, *a: p, raising=False)
    open_add(shell, page)
    page.click("[data-file]")
    page.wait_for_selector("#dlg[open]")
    assert "blocks every one" in page.inner_text("#dlg") and pills(page) == []
    page.click("#dlg button:has-text('Cancel')")
    dialog_closed(page)
    assert pills(page) == []
    page.click("[data-file]")
    page.click("#dlg button:has-text('Block all of java.exe')")
    dialog_closed(page)
    assert pills_are(page, ["java"])


def test_a_file_that_cannot_be_blocked_says_why(shell, page, env, monkeypatch):
    monkeypatch.setattr(shared, "_ask_path", lambda w: r"C:\Windows\System32\lsass.exe")
    monkeypatch.setattr("monitor.applist.program_file", lambda p, *a: p, raising=False)
    open_add(shell, page)
    page.click("[data-file]")
    assert "can't be blocked" in error_shown(page) and pills(page) == []


def test_a_typed_program_file_is_added_as_an_app(shell, page, env):
    open_add(shell, page)
    tick(page, "permanent")
    type_in_search(page, "C:\\tools\\start.bat")
    page.keyboard.press("Enter")
    on_overview(page)
    assert items(env)["Start"]["target"] == "start.bat" and items(env)["Start"]["block_type"] == "close"


# ================================================================ categories

def test_the_category_menu_lists_them_with_swatches_and_blocks_the_whole_category(shell, page, env):
    open_add(shell, page)
    page.click("[data-cat]")
    page.wait_for_selector("#menu:not([hidden])")
    assert page.eval_on_selector_all("#menu button span", "els => els.map(e => e.textContent)") == ["Productive", "Neutral", "Games", "Distracting"]
    assert page.locator("#menu .mn-sw").count() == 4
    page.click('#menu button:has-text("Games")')
    assert pills(page) == ["category · Games"] and page.is_visible('[data-g="category"]')
    assert ticked_ways(page, "category") == ["close"]
    assert "grey" in page.eval_on_selector_all('[data-g="category"] [data-flag]', "els => els.map(e => e.dataset.flag)")
    tick(page, "time_limit")
    set_limit(page, "1h")
    submit(page)
    on_overview(page)
    saved = items(env)["Games"]
    assert saved["item_type"] == "category" and saved["target"] == "games" and saved["source"] == "category"
    assert saved["block_type"] == "close"


# ================================================================ editing

def test_edit_opens_with_everything_loaded(shell, page, env):
    open_add(shell, page)
    edit_via_overview(page, "Discord")
    assert page.inner_text('[data-k="ad-title"]') == "Edit Discord" and page.inner_text('[data-k="ad-submit"]') == "Save"
    assert page.input_value('[data-k="ad-name"]') == "Discord" and ticked_ways(page, "app") == ["close"]
    assert page.inner_text(".ad-pills") == "discord.exe"
    assert page.inner_text(".rail-head [data-count]") == "1 of 5 on" and page.is_checked('.rail-row[data-t="temporary"] [data-tick]')
    assert page.is_visible(".ad-editacts") and not page.is_visible(".ad-pick")
    assert page.inner_text("[data-alerts]").endswith("Default") and page.inner_text("[data-disable]") == "Disable"
    assert not page.is_visible('[data-k="ad-info"]')                           # (not in a group)


def test_edit_saves_name_ways_and_blockers_then_goes_to_overview(shell, page, env):
    open_add(shell, page)
    edit_via_overview(page, "Discord")
    page.fill('[data-k="ad-name"]', "Chat app")
    page.check('[data-g="app"] [data-flag="internet"]')
    tick(page, "time_limit")
    set_limit(page, "1h")
    submit(page)
    on_overview(page)
    assert page.inner_text(NOTICE) == "✓ Chat app saved"
    saved = items(env)["Chat app"]
    assert saved["block_type"] == "close,internet" and saved["target"] == "discord.exe"
    assert {r["rule_type"] for r in saved["rules"]} == {"temporary", "time_limit"}


def test_the_alerts_menu_sets_the_items_alerts_at_once(shell, page, env):
    open_add(shell, page)
    edit_via_overview(page, "Discord")
    page.click("[data-alerts]")
    page.click('#menu button:has-text("Off")')
    page.wait_for_function("document.querySelector('[data-alerts]').textContent.endsWith('Off')")
    assert items(env)["Discord"]["notify"] == "off"


def test_also_in_groups_is_said_and_a_member_can_be_saved_with_no_blocker_of_its_own(shell, page, env):
    open_add(shell, page)
    edit_via_overview(page, "Reddit")
    assert "Also in groups: Fun (edit those in the Groups tab)" in page.inner_text('[data-k="ad-info"]')
    page.uncheck('.rail-row[data-t="switch_limit"] [data-tick]')                    # (untick its own opening limit)
    assert page.inner_text(".rail-head [data-count]") == "0 of 5 on"
    submit(page)
    on_overview(page)
    assert items(env)["Reddit"]["rules"] == [] and page.inner_text(NOTICE) == "✓ Reddit saved"


def test_an_item_with_no_group_cannot_be_saved_with_nothing_ticked(shell, page, env):
    open_add(shell, page)
    edit_via_overview(page, "Discord")
    page.uncheck('.rail-row[data-t="temporary"] [data-tick]')
    assert page.inner_text(".rail-head [data-count]") == "0 of 5 on"
    submit(page)
    assert error_shown(page) == "Tick at least one blocker (or remove it in Overview)."
    assert "Discord" in items(env) and rule(env, "Discord", "temporary")


def test_disable_and_enable_from_the_edit_bar(shell, page, env):
    open_add(shell, page)
    edit_via_overview(page, "Discord")
    page.click("[data-disable]")
    on_overview(page)
    assert page.inner_text(NOTICE) == "✓ Discord disabled" and items(env)["Discord"]["disabled"] == 1
    page.wait_for_selector('.vl-row:not([hidden]):has(.name b:text-is("Discord"))')
    page.locator('.vl-row:not([hidden]):has(.name b:text-is("Discord")) [data-act="edit"]').click()      # (from the Disabled card)
    page.wait_for_selector('[data-k="ad-title"]:has-text("Edit Discord")')
    assert page.inner_text("[data-disable]") == "Enable"
    page.click("[data-disable]")
    on_overview(page)
    assert page.inner_text(NOTICE) == "✓ Discord enabled" and items(env)["Discord"]["disabled"] == 0


def test_remove_needs_two_clicks(shell, page, env):
    open_add(shell, page)
    edit_via_overview(page, "Discord")
    page.click(".ad-editacts .confirm")
    assert "Discord" in items(env) and "Click again" in page.inner_text(".ad-editacts .confirm")
    page.click(".ad-editacts .confirm")
    on_overview(page)
    assert page.inner_text(NOTICE) == "✓ Discord removed" and "Discord" not in items(env)


def test_editing_a_looser_rule_while_locked_opens_the_challenge_and_changes_nothing(shell, page, env):
    lock(env)
    open_add(shell, page)
    edit_via_overview(page, "Steam")                                          # (a time limit of 2 h a day)
    set_limit(page, "5h")
    submit(page)
    page.wait_for_selector("#dlg[open] textarea.typed")
    assert "Loosen Steam" in page.inner_text("#dlg")
    assert rule(env, "Steam", "time_limit")["daily_limit_min"] == 120          # (nothing applied while the challenge is open)
    cancel_challenge(page)
    assert rule(env, "Steam", "time_limit")["daily_limit_min"] == 120
    assert page.inner_text('[data-k="ad-title"]') == "Edit Steam" and page.input_value('.rail-panel [data-f="day"]') == "5h"   # (the form is kept)
    submit(page)
    type_challenge(page)
    on_overview(page)
    assert rule(env, "Steam", "time_limit")["daily_limit_min"] == 300
    assert page.inner_text(NOTICE) == "✓ Steam saved"


def test_a_tighter_edit_is_free_while_locked(shell, page, env):
    lock(env)
    open_add(shell, page)
    edit_via_overview(page, "Steam")
    set_limit(page, "30m")
    submit(page)
    on_overview(page)
    assert rule(env, "Steam", "time_limit")["daily_limit_min"] == 30 and not page.is_visible("#dlg[open]")


def test_disabling_while_locked_asks_for_the_challenge(shell, page, env):
    lock(env)
    open_add(shell, page)
    edit_via_overview(page, "Discord")
    page.click("[data-disable]")
    page.wait_for_selector("#dlg[open] textarea.typed")
    assert "Disable Discord" in page.inner_text("#dlg")
    cancel_challenge(page)
    assert items(env)["Discord"]["disabled"] in (0, None) and page.inner_text('[data-k="ad-title"]') == "Edit Discord"


def test_with_autosave_off_the_edit_waits_for_save_changes(shell, page, env):
    autosave(env, False)
    open_add(shell, page)
    pick_suggestion(page, "face", "Facebook")
    tick(page, "permanent")
    submit(page)
    on_overview(page)
    assert page.inner_text(NOTICE) == "✓ Facebook blocker added - press Save changes to apply"
    assert "Facebook" not in items(env)                                       # (pending in the draft)
    page.wait_for_selector('.vl-row:not([hidden]):has(.name b:text-is("Facebook"))')
    assert "Not applied" in page.locator('.vl-row:not([hidden]):has(.name b:text-is("Facebook"))').first.inner_text()


# ================================================================ entry points

def test_block_it_from_another_page_fills_the_form_for_a_new_site_and_an_app(shell, page, env):
    open_add(shell, page)
    page.click('[data-tab="overview"]')
    go(page, "{ tab: 'add', prefill: { site: ['Example', 'example.org'], kind: 'site', name: 'example.org', display: 'Example', path: null } }")   # (the network log sends both shapes)
    page.wait_for_selector(".ad-pills .pill")
    assert pills(page) == ["Example"] and page.is_visible('[data-g="site"]') and page.get_attribute('[data-tab="add"]', "aria-selected") == "true"
    go(page, "{ tab: 'add', prefill: { kind: 'app', name: 'zebra.exe', display: 'Zebra Zone', path: 'C:\\\\Apps\\\\zebra.exe' } }")
    page.wait_for_function("document.querySelector('.ad-pills .pill .ellip')?.textContent === 'Zebra Zone'")
    assert pills(page) == ["Zebra Zone"] and ticked_ways(page, "app") == ["close"]


def test_block_it_for_something_already_listed_opens_it_for_editing(shell, page, env):
    open_add(shell, page)
    page.click('[data-tab="overview"]')
    go(page, "{ tab: 'add', prefill: { app: { name: 'Discord', exe: 'discord.exe', path: null } } }")
    page.wait_for_selector('[data-k="ad-title"]:has-text("Edit Discord")')
    assert "already on the list" in page.inner_text('[data-k="ad-info"]')


def test_the_add_button_of_overview_gives_a_fresh_form_after_an_edit(shell, page, env):
    open_add(shell, page)
    edit_via_overview(page, "Discord")
    page.click('[data-tab="overview"]')
    page.click('[data-k="add"]')
    page.wait_for_selector('[data-k="ad-title"]:has-text("Add a site")')
    assert page.inner_text('[data-k="ad-submit"]') == "+ Add" and not page.is_visible(".ad-editacts")


def test_edit_for_an_item_that_is_gone_goes_back_to_overview(shell, page, env):
    open_add(shell, page)
    go(page, "{ tab: 'add', edit: 99999 }")
    on_overview(page)
    assert "gone" in page.inner_text("#toast")


# ================================================================ nothing odd

def test_no_double_submit_while_it_runs(shell, page, env):
    open_add(shell, page)
    pick_suggestion(page, "face", "Facebook")
    tick(page, "permanent")
    env.delays["draft_submit_item"] = 0.3
    submit(page)
    assert page.is_disabled('[data-k="ad-submit"]')
    on_overview(page)
    assert len([i for i in env.db.list_items() if i["display_name"] == "Facebook"]) == 1


def test_nothing_runs_while_the_window_is_hidden(shell, page, env, perf):
    open_add(shell, page)
    pick_suggestion(page, "face", "Facebook")
    page.wait_for_timeout(300)
    perf.set_hidden(True)
    page.wait_for_timeout(300)
    perf.reset_timer_calls()
    before = env.rpc_count()
    page.wait_for_timeout(2500)                                          # (Playwright's own wait: no timer of the page's)
    calls = perf.timer_calls()
    assert calls["timeout"] + calls["interval"] == 0 and env.rpc_count() == before


# ================================================================ the numbers

def test_the_numbers(shell, page, env, capsys):
    shell(1280, 1000, route="blocking")
    page.wait_for_selector(".bo-row")
    t = page.evaluate("""async () => {
        const t0 = performance.now();
        document.querySelector('[data-tab="add"]').click();
        while (!document.querySelector('.ad-row:not([hidden])')) { if (performance.now() - t0 > 4000) return -1; await new Promise((r) => requestAnimationFrame(r)); }
        return performance.now() - t0; }""")
    page.click(SEARCH)
    page.keyboard.type("disc")
    typing = page.evaluate("""async () => {
        const sg = document.querySelector('.sg'); const t0 = performance.now();
        while (sg.hidden) { if (performance.now() - t0 > 3000) return -1; await new Promise((r) => requestAnimationFrame(r)); }
        return performance.now() - t0; }""")
    page.keyboard.press("Escape")
    tick_ms = page.evaluate("""async () => {
        const row = document.querySelector('.rail-row[data-t="time_limit"] .rail-main');
        await new Promise((r) => requestAnimationFrame(r)); const t0 = performance.now(); row.click();
        await new Promise((r) => requestAnimationFrame(r)); return performance.now() - t0; }""")
    with capsys.disabled():
        print(f"\nAdd tab first open (click -> list on screen): {t:.0f} ms; suggestions after the last key (debounce incl.): {typing:.0f} ms; "
              f"ticking a blocker -> next frame: {tick_ms:.0f} ms")
    assert 0 < t <= 600 and tick_ms <= 100
