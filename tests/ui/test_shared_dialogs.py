"""R12: the shared dialogs in the browser - the App browser (js/appbrowser.js), the right-click menu of an app / site
(js/appmenu.js) and the category editor + New category prompt (js/categories.js) - in the real shell, against the REAL Api
(webapi/categories.py, webapi/shared.py) on a temporary database. The installed-apps catalog is a fake with 1 500 apps and
the icon routes are a stub (the agent serves the real ones).

What can only be proven on Windows: the real Open dialog behind "Pick a program file...", Explorer opening with the file
selected, real icons, and the dialogs inside WebView2 (this is Chrome)."""
import random
import time

import pytest

import antibypass as ab
from resident import catalog as app_catalog
from webapi import categories as cat_api
from webapi import shared

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'


def make_apps(n=1500):
    apps = [{"name": f"App {i:04d}", "exe": f"app{i:04d}.exe", "path": rf"C:\Apps\app{i:04d}.exe", "steam": i % 7 == 0}
            for i in range(n)]
    apps += [{"name": "Zebra Zone", "exe": "zebra.exe", "path": r"C:\Apps\zebra.exe", "steam": False},
             {"name": "Local Security Authority", "exe": "lsass.exe", "path": r"C:\Windows\System32\lsass.exe", "steam": False}]
    return apps


@pytest.fixture
def apps_env(harness, page):
    cat = app_catalog.AppCatalog(harness.db_path.parent / "cat.json", list_apps=make_apps,
                                 running_now=lambda: {"app0003.exe": r"C:\Apps\app0003.exe"})
    cat.load_now()
    harness.ctx.catalog = cat
    page.route("**/icon/**", lambda route: route.fulfill(status=200, content_type="image/svg+xml", body=SVG))
    return cat


@pytest.fixture
def ready(shell, page, apps_env):
    """The shell is up and the three modules are loaded (so a timing below is the dialog, not the first import)."""
    page.set_default_timeout(8000)
    shell()
    page.evaluate("""async () => {
        window.AB = await import('/js/appbrowser.js'); window.CT = await import('/js/categories.js');
        await import('/js/appmenu.js'); window.__ev = []; }""")
    return page


def open_browser(page, mode="browse", extra=""):
    page.evaluate("""([mode]) => { window.__ev = [];
        window.__h = window.AB.openAppBrowser({mode, onPick: (a) => window.__ev.push(['pick', a]),
          goBlocking: (p) => window.__ev.push(['block', p]), onDone: (d) => window.__ev.push(['done', d])}); }""", [mode])
    page.wait_for_selector(".ab-row")


def search(page, text):
    page.fill(".ab input.input", text)
    page.wait_for_function("(t) => document.querySelector('.ab-row:not([hidden]) b')?.textContent.toLowerCase().includes(t)", arg=text.split()[0].lower())


TOP = "[...document.querySelectorAll('dialog[open]')].pop()?.querySelector('h2')?.textContent"


def title(page):
    """The title of the dialog on top (a challenge may sit in a layer of its own over the one it came from)."""
    return page.evaluate(TOP)


def top_text(page):
    return page.locator("dialog[open]").last.inner_text()


def wait_title(page, text):
    page.wait_for_function(f"{TOP} === {text!r}")


def wait_closed(page):
    """No dialog is open any more AND its `close` event has run (that is a task after .open turns false)."""
    page.wait_for_function("!document.querySelector('dialog[open]')")
    page.evaluate("new Promise((r) => setTimeout(r, 20))")


def esc_close(page):
    page.keyboard.press("Escape")
    wait_closed(page)


def lock(harness):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "length": 30})


def right_click_row(page, text):
    page.click(f".ab-row:has-text('{text}')", button="right")
    page.wait_for_selector("#menu:not([hidden]) button")


def menu_texts(page):
    return page.eval_on_selector_all("#menu button, #menu .lbl", "els => els.map(e => e.textContent.trim())")


def click_menu(page, label):
    page.locator("#menu button", has_text=label).first.click()


def say(capsys, text):
    with capsys.disabled():
        print("\n  " + text)


# ---------------------------------------------------------------- opening and searching

def test_opens_fast_from_the_cached_catalog(ready, harness, capsys):
    page = ready
    ms = page.evaluate("""async () => {
        const t0 = performance.now();
        window.AB.openAppBrowser({mode: 'browse'});
        while (!document.querySelector('.ab-row')) await new Promise((r) => requestAnimationFrame(r));
        return performance.now() - t0; }""")
    say(capsys, f"App browser opens (rows on screen), 1 502 apps in the catalog: {ms:.0f} ms")
    assert ms <= 150
    assert title(page) == "Apps"
    assert "1 502 apps" in page.inner_text(".ab-count") or "1502 apps" in page.inner_text(".ab-count")
    assert "first 60 shown" in page.inner_text(".ab-count")
    assert harness.rpc_count("apps_list") == 1


def test_rows_are_a_virtual_list_with_icons_running_and_steam(ready):
    page = ready
    open_browser(page)
    live = page.locator(".ab-row:not([hidden])").count()
    assert 8 <= live < 40                                           # 60 rows exist as data, only the window as nodes
    first = page.locator(".ab-row:not([hidden])").first
    assert first.locator("b").inner_text() == "App 0003"            # running ones first
    assert first.locator(".ab-run").is_visible()
    assert page.locator(".ab-row:not([hidden]) .ab-run:visible").count() == 1
    assert "app0003.exe" in first.locator(".name span").inner_text()
    assert first.locator("img").get_attribute("src") == "/icon/app/app0003.exe.png"
    # the Games filter keeps only Steam games, Running only what is open
    page.click('[data-seg="abfilter"] [data-v="games"]')
    page.wait_for_function("document.querySelector('.ab-count')?.textContent.startsWith('215 apps')")
    assert "Steam" in page.inner_text(".ab-row:not([hidden])")
    page.click('[data-seg="abfilter"] [data-v="running"]')
    page.wait_for_function("document.querySelectorAll('.ab-row:not([hidden])').length === 1")
    assert "1 app" in page.inner_text(".ab-count")


def test_typing_is_debounced_and_filters_within_a_frame_of_the_answer(ready, harness, capsys):
    page = ready
    open_browser(page)
    before = harness.rpc_count("apps_list")
    ms = page.evaluate("""async () => {
        const input = document.querySelector('.ab input.input'); input.focus();
        const t0 = performance.now();
        for (const v of ['z', 'ze', 'zeb', 'zebr', 'zebra']) { input.value = v; input.dispatchEvent(new Event('input', {bubbles: true})); await new Promise((r) => setTimeout(r, 15)); }
        const t1 = performance.now();                       // the last key
        while (document.querySelector('.ab-row:not([hidden]) b')?.textContent !== 'Zebra Zone') await new Promise((r) => requestAnimationFrame(r));
        return performance.now() - t1; }""")
    say(capsys, f"after the last key until the filtered row is on screen: {ms:.0f} ms (debounce is 120)")
    assert ms <= 120 + 80
    assert harness.rpc_count("apps_list") - before == 1             # five keys, one call
    assert page.locator(".ab-row:not([hidden])").count() == 1
    assert page.inner_text(".ab-count") == "1 app"
    page.fill(".ab input.input", "no such thing xyz")
    page.wait_for_selector(".ab-empty:not([hidden])")
    assert page.inner_text(".ab-empty") == "No apps found."


def test_it_says_loading_and_fills_in_when_the_catalog_is_ready(shell, page, harness, push):
    cat = app_catalog.AppCatalog(harness.db_path.parent / "late.json", list_apps=make_apps)
    harness.ctx.catalog = cat                                       # not loaded yet
    page.route("**/icon/**", lambda route: route.fulfill(status=200, content_type="image/svg+xml", body=SVG))
    page.set_default_timeout(8000)
    shell()
    page.evaluate("async () => { window.AB = await import('/js/appbrowser.js'); window.AB.openAppBrowser({}); }")
    page.wait_for_function("document.querySelector('.ab-count')?.textContent.includes('Loading')")
    assert page.locator(".ab-row:not([hidden])").count() == 0
    cat.load_now()
    push("apps_ready", {})
    page.wait_for_selector(".ab-row")
    assert "apps" in page.inner_text(".ab-count") and "Loading" not in page.inner_text(".ab-count")


def test_browse_mode_has_no_pick_and_pick_mode_picks(ready):
    page = ready
    open_browser(page, "browse")
    assert page.locator("[data-file]").count() == 0
    page.click(".ab-row >> nth=0")
    assert title(page) == "Apps" and page.evaluate("window.__ev") == []          # browsing: a click does nothing
    esc_close(page)
    open_browser(page, "pick")
    assert page.locator("[data-file]").count() == 1 and title(page) == "Browse apps"
    search(page, "zebra")
    page.click(".ab-row:has-text('Zebra Zone')")
    wait_closed(page)
    assert page.evaluate("window.__ev") == [["pick", {"name": "Zebra Zone", "exe": "zebra.exe", "path": r"C:\Apps\zebra.exe"}]]


def test_enter_picks_and_the_keyboard_walks_the_list(ready):
    page = ready
    open_browser(page, "pick")
    page.evaluate("() => [...document.querySelectorAll('.ab-row')].find((n) => !n.hidden && n._i === 0).focus()")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.querySelector('b').textContent") == "App 0001"      # (App 0003 runs: first)
    page.keyboard.press("Enter")
    wait_closed(page)
    assert page.evaluate("window.__ev")[0][1]["exe"] == "app0001.exe"


def test_pick_a_program_file(ready, monkeypatch):
    page = ready
    monkeypatch.setattr(shared, "_ask_path", lambda w: r"C:\Games\Foo\foo.exe")
    monkeypatch.setattr("monitor.applist.program_file", lambda p, *a: p, raising=False)
    open_browser(page, "pick")
    page.click("[data-file]")
    wait_closed(page)
    assert page.evaluate("window.__ev") == [["pick", {"name": "foo", "exe": "foo.exe", "path": r"C:\Games\Foo\foo.exe"}]]


def test_a_whole_interpreter_is_asked_about_first(ready, monkeypatch):
    page = ready
    monkeypatch.setattr(shared, "_ask_path", lambda w: r"C:\Java\bin\java.exe")
    monkeypatch.setattr("monitor.applist.program_file", lambda p, *a: p, raising=False)
    open_browser(page, "pick")
    page.click("[data-file]")
    page.wait_for_selector(".ab-warn:not([hidden])")
    assert "blocks every one" in page.inner_text(".ab-warn") and page.evaluate("window.__ev") == []
    page.click(".ab-warn [data-no]")
    assert not page.is_visible(".ab-warn")
    page.click("[data-file]")
    page.click(".ab-warn [data-yes]")
    wait_closed(page)
    assert page.evaluate("window.__ev")[0][1]["exe"] == "java.exe"


def test_a_file_that_cant_be_blocked_says_why(ready, monkeypatch):
    page = ready
    monkeypatch.setattr(shared, "_ask_path", lambda w: r"C:\Windows\System32\lsass.exe")
    monkeypatch.setattr("monitor.applist.program_file", lambda p, *a: p, raising=False)
    open_browser(page, "pick")
    page.click("[data-file]")
    page.wait_for_selector(".ab-count.bad")
    assert "can't be blocked" in page.inner_text(".ab-count")
    assert title(page) == "Browse apps" and page.evaluate("window.__ev") == []


# ---------------------------------------------------------------- the right-click menu

def test_right_click_on_a_protected_exe_shows_the_disabled_line(ready):
    page = ready
    open_browser(page)
    search(page, "local security")
    right_click_row(page, "Local Security")
    texts = menu_texts(page)
    assert "Part of Windows - can't be blocked" in texts
    assert not any(t.startswith("Block it") or t.startswith("Add to group") for t in texts)
    line = page.locator("#menu button", has_text="Part of Windows")
    assert line.is_disabled() and line.is_visible()
    assert any(t.startswith("Category") for t in texts) and "Show in Explorer" in texts


def test_the_menu_works_over_the_dialog_and_esc_closes_only_the_menu(ready):
    page = ready
    open_browser(page)
    right_click_row(page, "App 0003")
    assert page.evaluate("document.getElementById('menu').closest('dialog')?.id") == "dlg"      # inside it: not inert
    assert page.locator("#menu button").first.is_visible()
    page.keyboard.press("Escape")
    assert not page.is_visible("#menu") and page.evaluate("document.getElementById('dlg').open")
    assert page.evaluate("document.getElementById('menu').parentNode === document.body")
    page.keyboard.press("Escape")                                                               # now the dialog
    wait_closed(page)


def test_the_menu_has_category_block_group_and_explorer(ready, harness):
    page = ready
    harness.db.add_group("Study", [{"rule_type": "permanent"}], {})
    open_browser(page)
    search(page, "zebra")
    right_click_row(page, "Zebra Zone")
    texts = menu_texts(page)
    assert texts[:3] == ["Zebra Zone", "Category: Neutral ›", "Block it…"] and "Add to group ›" in texts and "Show in Explorer" in texts
    click_menu(page, "Category")                                                   # the list replaces the menu
    texts = menu_texts(page)
    assert texts[:5] == ["Category", "Productive", "Neutral", "Games", "Distracting"]
    assert page.locator("#menu button", has_text="Neutral").locator(".mk").count() == 1            # the tick
    assert "New category…" in texts and "Edit categories…" in texts and "‹ Back" in texts
    click_menu(page, "Back")
    assert menu_texts(page)[1] == "Category: Neutral ›"


def test_no_groups_yet(ready, harness):
    page = ready
    for g in harness.db.list_groups():
        harness.db.remove_group(g["id"])
    open_browser(page)
    right_click_row(page, "App 0003")
    line = page.locator("#menu button", has_text="Add to group")
    assert line.inner_text().strip() == "Add to group (you have none yet)" and line.is_disabled()


def test_category_pick_is_written_when_free_and_the_browser_stays(ready, harness):
    page = ready
    open_browser(page)
    search(page, "zebra")
    right_click_row(page, "Zebra Zone")
    click_menu(page, "Category")
    click_menu(page, "Productive")
    page.wait_for_function("document.querySelector('.ab-count').textContent.includes('is now Productive')")
    assert harness.db.categories()[("app", "zebra.exe")] == "productive"
    assert title(page) == "Apps" and page.input_value(".ab input.input") == "zebra"
    assert page.evaluate("window.__ev") == [["done", {"type": "category", "category": "productive"}]]


def test_category_pick_while_locked_opens_the_challenge_and_the_browser_comes_back(ready, harness):
    page = ready
    lock(harness)
    open_browser(page)
    search(page, "zebra")
    right_click_row(page, "Zebra Zone")
    click_menu(page, "Category")
    click_menu(page, "Distracting")
    wait_title(page, "This loosens your blocks")
    assert "Move zebra.exe to the Distracting category" in top_text(page)
    assert ("app", "zebra.exe") not in harness.db.categories()                    # nothing written
    page.keyboard.press("Escape")                                                  # Cancel the challenge
    wait_title(page, "Apps")
    assert page.input_value(".ab input.input") == "zebra"                          # where the owner was
    assert ("app", "zebra.exe") not in harness.db.categories()


def test_the_challenge_then_makes_the_change(ready, harness):
    page = ready
    lock(harness)
    open_browser(page)
    search(page, "zebra")
    right_click_row(page, "Zebra Zone")
    click_menu(page, "Category")
    click_menu(page, "Games")
    page.wait_for_selector("dialog[open] textarea.typed")
    phrase = page.evaluate("document.querySelector('dialog[open] .ch-phrase').textContent")       # what Python issued
    page.focus("dialog[open] textarea.typed")
    page.keyboard.type(phrase, delay=45)
    page.click("dialog[open] button:has-text('Continue')")
    page.wait_for_function("document.querySelector('.ab-count')?.textContent.includes('is now Games')")
    assert harness.db.categories()[("app", "zebra.exe")] == "games"
    assert title(page) == "Apps" and page.input_value(".ab input.input") == "zebra"


def test_add_to_group_from_the_menu(ready, harness):
    page = ready
    gid = harness.db.add_group("Study", [{"rule_type": "permanent"}], {})
    open_browser(page)
    search(page, "zebra")
    right_click_row(page, "Zebra Zone")
    click_menu(page, "Add to group")
    assert menu_texts(page)[0] == "Add to group" and "Study" in menu_texts(page)
    click_menu(page, "Study")
    page.wait_for_function("document.querySelector('.ab-count').textContent === 'Zebra Zone added to Study'")
    item = next(i for i in harness.db.list_items() if i["target"] == "zebra.exe")
    assert item["app_path"] == r"C:\Apps\zebra.exe" and item["item_type"] == "app"
    assert list(next(g for g in harness.db.list_groups() if g["id"] == gid)["members"]) == [item["id"]]


def test_block_it_goes_to_blocking_add_prefilled(ready):
    page = ready
    open_browser(page)
    search(page, "zebra")
    right_click_row(page, "Zebra Zone")
    click_menu(page, "Block it")
    wait_closed(page)
    assert page.evaluate("window.__ev") == [["block", {"kind": "app", "name": "zebra.exe", "display": "Zebra Zone",
                                                       "path": r"C:\Apps\zebra.exe"}]]


def test_show_in_explorer_asks_python_with_the_known_path(ready, monkeypatch):
    page = ready
    opened = []
    monkeypatch.setattr(cat_api, "_open_explorer", opened.append)
    open_browser(page)
    search(page, "zebra")
    right_click_row(page, "Zebra Zone")
    click_menu(page, "Show in Explorer")
    page.wait_for_function("window.__never === undefined")
    deadline = time.time() + 3
    while not opened and time.time() < deadline:
        time.sleep(0.05)
    assert opened == [r"C:\Apps\zebra.exe"]


def test_new_category_from_the_menu(ready, harness):
    page = ready
    open_browser(page)
    search(page, "zebra")
    right_click_row(page, "Zebra Zone")
    click_menu(page, "Category")
    click_menu(page, "New category")
    wait_title(page, "New category")
    page.click("#dlg button:has-text('Add')")
    assert "Give the category a name" in page.inner_text("#dlg .cat-err")           # nothing typed
    page.fill("#dlg input.input", "Reading")
    page.click("#dlg button[data-c='#2F9FD8']")
    page.press("#dlg input.input", "Enter")
    wait_title(page, "Apps")          # the browser is back
    assert harness.db.categories()[("app", "zebra.exe")] == "reading"
    assert [c["name"] for c in cat_api.load(harness.db)][-1] == "Reading"
    assert [c["color"] for c in cat_api.load(harness.db)][-1] == "#2f9fd8"


# ---------------------------------------------------------------- the category editor

def open_editor(page):
    page.evaluate("() => { window.__done = 0; window.CT.openCategoryEditor({onClose: () => window.__done++}); }")
    page.wait_for_selector(".cat-row")


def test_editor_rows_builtins_recolour_only(ready):
    page = ready
    open_editor(page)
    assert title(page) == "Categories"
    names = page.eval_on_selector_all(".cat-row .cat-name", "els => els.map(e => e.textContent)")
    assert names == ["Productive", "Neutral", "Games", "Distracting"]
    assert page.locator(".cat-row .confirm").count() == 0                          # no Delete on the built-in ones
    assert page.locator(".cat-row .chip", has_text="Built-in").count() == 4
    page.click("text=Done")
    wait_closed(page)
    assert page.evaluate("window.__done") == 1


def test_recolouring_is_free_even_while_locked(ready, harness):
    page = ready
    lock(harness)
    open_editor(page)
    page.click(".cat-row[data-key='neutral'] .cat-sw")
    assert page.is_visible(".cat-row[data-key='neutral'] .cat-pick")
    page.click(".cat-row[data-key='neutral'] .cat-pick button[data-c='#E0559B']")
    page.wait_for_function("document.querySelector(\".cat-row[data-key='neutral'] .cat-sw\").style.background.includes('rgb(224, 85, 155)')")
    assert {c["key"]: c["color"] for c in cat_api.load(harness.db)}["neutral"] == "#E0559B".lower()
    assert title(page) == "Categories"                                              # no challenge
    # any colour from the native picker
    page.evaluate("""() => { const i = document.querySelector(".cat-row[data-key='neutral'] input[type=color]");
        i.value = '#123456'; i.dispatchEvent(new Event('change', {bubbles: true})); }""")
    page.wait_for_function("document.querySelector(\".cat-row[data-key='neutral'] .cat-sw\").style.background.includes('rgb(18, 52, 86)')")
    assert {c["key"]: c["color"] for c in cat_api.load(harness.db)}["neutral"] == "#123456"


def test_new_category_free_then_delete_with_two_clicks(ready, harness):
    page = ready
    open_editor(page)
    page.click("text=+ New category")
    page.fill(".cat-new input.input", "Chores")
    page.click(".cat-new [data-add]")
    page.wait_for_selector(".cat-row[data-key='chores']")
    assert [c["name"] for c in cat_api.load(harness.db)][-1] == "Chores"
    harness.db.set_category("app", "a.exe", "chores")
    btn = page.locator(".cat-row[data-key='chores'] .confirm")
    btn.click()
    assert "Click again" in btn.inner_text()
    assert [c["key"] for c in cat_api.load(harness.db)][-1] == "chores"            # one click is not enough
    btn.click()
    page.wait_for_selector(".cat-row[data-key='chores']", state="detached")
    assert "chores" not in [c["key"] for c in cat_api.load(harness.db)]
    assert ("app", "a.exe") not in harness.db.categories()


def test_new_category_and_delete_while_locked_open_the_challenge_and_the_editor_comes_back(ready, harness):
    page = ready
    cat_api._add(harness.db, "Reading", "#2f9fd8")
    lock(harness)
    open_editor(page)
    page.click("text=+ New category")
    page.fill(".cat-new input.input", "Chores")
    page.click(".cat-new [data-add]")
    wait_title(page, "This loosens your blocks")
    assert "Add the Chores category" in top_text(page)
    assert "chores" not in [c["key"] for c in cat_api.load(harness.db)]
    page.keyboard.press("Escape")
    wait_title(page, "Categories")
    assert page.evaluate("window.__done") == 0                                      # still in the editor
    btn = page.locator(".cat-row[data-key='reading'] .confirm")
    btn.click(); btn.click()
    wait_title(page, "This loosens your blocks")
    assert "Delete the Reading category" in top_text(page)
    assert "reading" in [c["key"] for c in cat_api.load(harness.db)]


def test_a_name_that_exists_is_refused_in_the_page(ready):
    page = ready
    open_editor(page)
    page.click("text=+ New category")
    page.fill(".cat-new input.input", "neutral")
    page.click(".cat-new [data-add]")
    assert "already a category called neutral" in page.inner_text(".cat-err")
    assert page.locator(".cat-row").count() == 4


def test_a_change_fires_the_categories_event(ready):
    page = ready
    page.evaluate("() => { window.__changed = 0; window.addEventListener('ld:categories-changed', () => window.__changed++); }")
    open_editor(page)
    page.click(".cat-row[data-key='games'] .cat-sw")
    page.click(".cat-row[data-key='games'] .cat-pick button[data-c='#20B2AA']")
    page.wait_for_function("window.__changed === 1")


def test_the_editor_opened_from_the_menu_brings_the_browser_back(ready):
    page = ready
    open_browser(page)
    search(page, "zebra")
    right_click_row(page, "Zebra Zone")
    click_menu(page, "Category")
    click_menu(page, "Edit categories")
    wait_title(page, "Categories")
    page.click("text=Done")
    wait_title(page, "Apps")
    assert page.input_value(".ab input.input") == "zebra"
