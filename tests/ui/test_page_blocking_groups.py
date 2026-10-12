"""R21: Blocking > Groups (js/pages/blocking/groups.js, js/customize_member.js) in Chrome against the REAL Python Api on a
temporary database (see harness.py).

What is checked: the list of group cards (members line, rule chips, ways, status, "+ New group", "No groups yet.", "No group
open"), the editor (name, the blocker rail, "How members are blocked", the Members box: ticked members only until you type,
matching apps with icons, adding by website / by the app browser), a member's "Group's way / Its own way" and "Extra limits"
dialogs (they validate), Done / Cancel / Remove / Disable, and - the anti-bypass part - that Python asks for the challenge when
Done loosens something (a member taken out, a lighter own way, a raised shared limit, a lighter group way) and not when it only
tightens, with the editor staying put until the challenge passes.

What can only be proven on Windows: the page inside WebView2 (this is Chrome), real app and site icons, the native "Pick a
program file" dialog."""
import pytest

import antibypass as ab
from webapi import draft as drafts

SPOTIFY = {"name": "Spotify", "exe": "spotify.exe", "path": r"C:\Spotify\spotify.exe", "running": False, "steam": False}
NOTEPAD = {"name": "Notepad", "exe": "notepad.exe", "path": r"C:\Windows\notepad.exe", "running": False, "steam": False}
LIST = '[data-k="g-mlist"] > .vl-row:not([hidden])'
BY_PLACE = "els => els.sort((a, b) => a._i - b._i)"


class FakeCatalog:
    def __init__(self, apps):
        self.apps = apps

    def search(self, kind="all", query="", limit=60):
        found = [a for a in self.apps if query.lower() in (a["name"] + " " + a["exe"]).lower() and (kind != "games" or a["steam"])]
        return {"apps": found[:limit], "total": len(found), "loading": False}


@pytest.fixture(autouse=True)
def catalog(harness):
    harness.ctx.catalog = FakeCatalog([SPOTIFY, NOTEPAD])


def wait_for(page, js, timeout=6000):
    page.wait_for_function(js, timeout=timeout)


def settle(page, ms=250):
    page.evaluate("(ms) => new Promise((r) => setTimeout(r, ms))", ms)


def open_groups(shell, page, **kw):
    shell(route="blocking", **kw)
    page.wait_for_selector(".bo-row", timeout=8000)
    page.click('[data-tab="groups"]')
    page.wait_for_selector(".gp-card[data-id]", timeout=8000)
    return page


def open_fun(shell, page, **kw):
    open_groups(shell, page, **kw)
    page.click('.gp-card[data-id]')
    page.wait_for_selector('[data-k="g-editor"]:not([hidden]) [data-k="g-name"]')
    wait_for(page, f"document.querySelectorAll('{LIST}').length > 0")
    return page


def members(page):
    """The names in the Members box, top to bottom."""
    return page.eval_on_selector_all(LIST, f"els => ({BY_PLACE})(els).map(e => e.querySelector('.name b').textContent)")


def member_row(page, name):
    return page.locator(f'{LIST}:has(.name b:text-is("{name}"))').first


def lock(harness, **kw):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "length": 30, **kw})


def autosave(harness, on):
    harness.db.set_setting("ui.autosave", "1" if on else "0")


def group(harness, name="Fun"):
    return next(g for g in harness.db.list_groups() if g["name"] == name)


def item_id(harness, name):
    return next(i["id"] for i in harness.db.list_items() if i["display_name"] == name)


def reload_draft(harness):
    drafts.draft_of(harness.ctx).reload()


def add_notepad(harness, app_block="close"):
    """Notepad (an app) in the Fun group, which closes its apps."""
    g = group(harness)
    pad = harness.db.add_item("Notepad", ["notepad.exe"], "app", "manual", [], block_type="close")
    harness.db.update_group(g["id"], g["name"], g["rules"], {**g["members"], pad: {}}, False,
                            {"app_block": app_block, "site_block": g["site_block"], "member_blocks": {}})
    reload_draft(harness)
    return pad


def dialog_open(page):
    return page.evaluate("document.getElementById('dlg').open")


def dialog_closed(page, timeout=8000):
    """Wait until the dialog is closed AND its OK / close result has been handled. `open` turns false the moment dialog.close()
    runs, but the page handles the result (onClose) in the dialog's 'close' event, a task later - and then empties the dialog.
    Reading the page between those two (a busy machine) sees the old state, so wait for the emptied dialog."""
    wait_for(page, "(() => { const d = document.getElementById('dlg'); return !d.open && d.childElementCount === 0; })()", timeout)


def top_dialog(page):
    return page.locator("dialog[open]").last


def type_challenge(page):
    page.wait_for_selector("dialog[open] textarea.typed", timeout=5000)
    text = page.evaluate("[...document.querySelectorAll('dialog[open] .ch-phrase')].pop().textContent")
    page.focus("dialog[open] textarea.typed")
    page.keyboard.type(text, delay=45)
    page.keyboard.press("Enter")
    wait_for(page, "![...document.querySelectorAll('dialog[open]')].some(d => d.querySelector('.ch-phrase'))")


def cancel_challenge(page):
    page.wait_for_selector("dialog[open] .ch-phrase", timeout=5000)
    page.keyboard.press("Escape")
    wait_for(page, "![...document.querySelectorAll('dialog[open]')].some(d => d.querySelector('.ch-phrase'))")


def notice(page):
    return page.inner_text('.bk [data-k="notice"]')


def frames(page, n=2):
    page.evaluate("(n) => new Promise((r) => { const f = () => (--n <= 0 ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); })", n)


def pick_way(page, name, label):
    """Open a member's way menu and choose an entry. (A menu closes when anything scrolls, so scroll the button into view
    first and let the scroll event pass - Playwright's own scrolling before a click may arrive a frame late on a busy box.)"""
    btn = member_row(page, name).locator('[data-act="way"]')
    btn.scroll_into_view_if_needed()
    frames(page, 4)
    btn.click()
    page.click(f'#menu button:has-text("{label}")')


def tick_blocker(page, t):
    page.click(f'.ge .rail-row[data-t="{t}"] .switch')


# ================================================================ the list

def test_the_tab_lists_the_groups_with_a_new_group_card_and_a_placeholder(shell, page, harness):
    open_groups(shell, page)
    assert page.get_attribute('[data-tab="groups"]', "aria-selected") == "true"
    card = page.locator(".gp-card[data-id]")
    assert card.count() == 1
    assert card.locator("h3").inner_text() == "Fun"
    assert card.locator(".grow .small").inner_text() == "3 members · Reddit, Twitch, YouTube"
    assert card.locator(".gp-chips .bchip").count() == 1
    assert card.locator(".gp-ways").inner_text() == "Apps as each is set · sites as each is set"
    assert card.locator(".chip").inner_text() == "1 of 3 blocked now"
    assert page.is_visible("button.gp-new") and "New group" in page.inner_text("button.gp-new")
    # nothing open yet
    assert "No group open" in page.inner_text('[data-k="g-blank"]') and page.is_visible('[data-k="g-blank"]')
    assert page.is_hidden('[data-k="g-editor"]')
    # what has no PC backend is not there
    assert page.locator('[data-pane="groups"] >> text=Icon').count() == 0 and page.locator(".gp .swatches").count() == 0


def test_with_no_groups_the_list_says_so(shell, page, harness):
    harness.db.remove_group(group(harness)["id"])
    shell(route="blocking")
    page.wait_for_selector(".bo-row, .bo-empty", timeout=8000)
    page.click('[data-tab="groups"]')
    page.wait_for_selector('[data-k="g-none"]:not([hidden])', timeout=8000)
    assert "No groups yet." in page.inner_text('[data-k="g-none"]')
    assert page.locator(".gp-card[data-id]").count() == 0


def test_a_disabled_group_and_an_unsaved_one_say_so(shell, page, harness):
    autosave(harness, False)
    g = group(harness)
    harness.db.update_group(g["id"], g["name"], g["rules"], g["members"], True)
    reload_draft(harness)
    open_groups(shell, page)
    assert page.locator(".gp-card[data-id] .chip").inner_text() == "Disabled - nothing is enforced"
    # a change that waits for Save: "Not applied (unsaved)"
    page.click('.gp-card[data-id]')
    page.wait_for_selector('[data-k="g-editor"]:not([hidden])')
    page.fill('[data-k="g-name"]', "Fun 2")
    page.click('[data-k="g-done"]')
    page.wait_for_selector('.gp-card .chip:has-text("Not applied (unsaved)")')
    assert "press Save changes" in notice(page)
    assert group(harness)["name"] == "Fun"                                  # (not saved yet)


@pytest.mark.parametrize("size", [(1100, 720), (800, 600)])
def test_a_long_group_card_stays_inside_its_card_and_beside_the_editor(shell, page, harness, size):
    import json
    g = group(harness)
    night = json.dumps({"mode": "block", "windows": [{"days": [0, 1, 2, 3, 6], "start": "21:00", "end": "05:00"},
                                                      {"days": [4, 5], "start": "22:00", "end": "05:00"}]})
    harness.db.update_group(g["id"], "Good Night", [{"rule_type": "scheduled", "schedule": night}], g["members"], True)
    reload_draft(harness)
    open_groups(shell, page, width=size[0], height=size[1])
    page.click(".gp-card[data-id]")
    page.wait_for_selector('[data-k="g-editor"]:not([hidden]) [data-k="g-name"]')
    settle(page, 300)
    bad = page.evaluate("""() => {
      const out = [];
      for (const card of document.querySelectorAll('.gp-card[data-id]')) {
        const c = card.getBoundingClientRect();
        for (const e of card.querySelectorAll('.top .grow > *, .top .chip, .gp-chips, .bchip, .gp-ways')) {
          const r = e.getBoundingClientRect();
          if (r.width && (r.left < c.left - 1 || r.right > c.right + 1)) out.push(`${e.className}: ${r.left}-${r.right} vs card ${c.left}-${c.right}`);
        }
      }
      const l = document.querySelector('.gp-list').getBoundingClientRect(), e = document.querySelector('.gp-right').getBoundingClientRect();
      if (!(l.right <= e.left + 1 || l.bottom <= e.top + 1)) out.push(`list ${l.left}-${l.right}/${l.top}-${l.bottom} overlaps editor ${e.left}-${e.right}/${e.top}-${e.bottom}`);
      const t = document.querySelector('.ge-top h2').getBoundingClientRect();
      for (const b of document.querySelectorAll('.ge-top .card-h .btn')) {
        const r = b.getBoundingClientRect();
        if (r.width && r.left < t.right - 1 && r.right > t.left + 1 && r.top < t.bottom - 1 && r.bottom > t.top + 1) out.push('editor title overlaps ' + b.textContent);
      }
      return out;
    }""")
    assert bad == []
    assert page.locator(".gp-card[data-id] .bchip").count() >= 1
    assert "Friday" in page.get_attribute(".gp-card[data-id] .gp-chips", "title")        # the full text is in the tooltip


# ================================================================ the editor

def test_opening_a_group_fills_the_editor(shell, page, harness):
    open_fun(shell, page)
    assert page.inner_text('[data-k="g-title"]') == "Edit group"
    assert page.input_value('[data-k="g-name"]') == "Fun"
    assert page.is_hidden('[data-k="g-blank"]')
    assert page.get_attribute(".gp-card[data-id]", "aria-current") == "true"
    # the shared rail: the group's time limit is ticked, and only that one
    assert page.locator(".ge .rail-row.on").count() == 1 and page.locator('.ge .rail-row.on[data-t="time_limit"]').count() == 1
    assert "1h30" in page.inner_text('.ge .rail-row[data-t="time_limit"] .sum')
    assert page.inner_text(".ge [data-count]") == "1 of 5 on"
    # "how members are blocked": this group never chose, so nothing is ticked and the note says what happens
    assert page.locator(".ge-wcols input[data-way]:checked").count() == 0
    assert "each is blocked the way it was set when it was added" in page.inner_text('[data-k="g-wnote"]')
    # members: the ticked ones, with icons and the way button
    assert members(page) == ["Reddit", "Twitch", "YouTube"]
    assert page.locator(f"{LIST} img[data-icon]").count() == 3
    assert page.inner_text('[data-k="g-mcount"]') == "3 members"
    assert page.inner_text('[data-k="g-mhint"]') == "Only ticked members are shown. Type to find more."
    assert member_row(page, "Reddit").locator('[data-act="way"]').inner_text() == "Group's way"
    assert member_row(page, "Reddit").locator('[data-act="extras"]').inner_text() == "Extra limits"
    # the buttons of an existing group
    for label in ("Done", "Cancel", "Remove group", "Disable"):
        assert page.locator(f'.ge-top button:has-text("{label}")').count() == 1, label
    page.click('.ge-top button:has-text("Cancel")')
    assert page.is_visible('[data-k="g-blank"]') and page.is_hidden('[data-k="g-editor"]')
    assert page.get_attribute(".gp-card[data-id]", "aria-current") == "false"


def test_a_group_that_chose_its_ways_shows_them_ticked(shell, page, harness):
    g = group(harness)
    harness.db.update_group(g["id"], g["name"], g["rules"], g["members"], False,
                            {"app_block": "close,background", "site_block": "dns,close", "member_blocks": {}})
    reload_draft(harness)
    open_fun(shell, page)
    ticked = page.eval_on_selector_all(".ge-wcols input[data-way]:checked", "els => els.map(e => e.dataset.way)")
    assert sorted(ticked) == ["background", "close", "close", "dns"]
    assert page.inner_text('[data-k="g-wnote"]') == ""
    # (the one set of boxes, js/ways.js:) ticking Minimize unticks Close, "its background processes" goes with Close only
    apps = ".ge-wcols > div:first-child"
    assert page.is_enabled(f'{apps} [data-way="minimize"]') and page.is_enabled(f'{apps} [data-way="background"]')
    page.click(f'{apps} label:has([data-way="minimize"])')
    assert page.is_checked(f'{apps} [data-way="minimize"]') and not page.is_checked(f'{apps} [data-way="close"]')
    assert page.is_disabled(f'{apps} [data-way="background"]') and not page.is_checked(f'{apps} [data-way="background"]')
    page.click(f'{apps} label:has([data-way="close"])')
    assert page.is_checked(f'{apps} [data-way="close"]') and not page.is_checked(f'{apps} [data-way="minimize"]')
    assert page.is_enabled(f'{apps} [data-way="background"]')


def test_typing_finds_listed_things_and_apps_with_icons_and_clearing_goes_back_to_the_ticked(shell, page, harness):
    open_fun(shell, page)
    page.fill('[data-k="g-search"]', "spot")
    wait_for(page, "[...document.querySelectorAll('[data-k=\"g-mlist\"] > .vl-row:not([hidden]) .name b')].some(b => b.textContent === 'Spotify')")
    row = member_row(page, "Spotify")
    assert row.locator("img").get_attribute("data-icon") == "app:spotify.exe"
    assert row.locator("input").is_checked() is False
    assert page.inner_text('[data-k="g-mhint"]') == "Showing everything that matches - tick to add."
    assert members(page) == ["Spotify"]
    # something on the blocklist shows too, ticked when it is a member
    page.fill('[data-k="g-search"]', "tube")
    wait_for(page, "[...document.querySelectorAll('[data-k=\"g-mlist\"] > .vl-row:not([hidden]) .name b')].some(b => b.textContent === 'YouTube')")
    assert member_row(page, "YouTube").locator("input").is_checked() is True
    page.fill('[data-k="g-search"]', "zzzz nothing")
    page.wait_for_selector('[data-k="g-mnone"]:not([hidden])')
    assert page.inner_text('[data-k="g-mnone"]') == "No apps or sites match."
    # untyped: the ticked members again
    page.fill('[data-k="g-search"]', "")
    wait_for(page, f"document.querySelectorAll('{LIST}').length === 3")
    assert members(page) == ["Reddit", "Twitch", "YouTube"]


def test_ticking_a_found_app_adds_it_and_unticking_a_member_takes_it_out(shell, page, harness):
    open_fun(shell, page)
    page.fill('[data-k="g-search"]', "spot")
    wait_for(page, "[...document.querySelectorAll('[data-k=\"g-mlist\"] > .vl-row:not([hidden]) .name b')].some(b => b.textContent === 'Spotify')")
    member_row(page, "Spotify").locator("label.check").click()
    assert page.inner_text('[data-k="g-mcount"]') == "4 members"
    page.fill('[data-k="g-search"]', "")
    wait_for(page, f"document.querySelectorAll('{LIST}').length === 4")
    assert members(page) == ["Reddit", "Spotify", "Twitch", "YouTube"]
    member_row(page, "Reddit").locator("label.check").click()                 # un-tick: out of the group
    wait_for(page, f"document.querySelectorAll('{LIST}').length === 3")
    assert members(page) == ["Spotify", "Twitch", "YouTube"]
    assert len(group(harness)["members"]) == 3 and "Spotify" not in [i["display_name"] for i in harness.db.list_items()]


def test_a_website_typed_in_the_box_can_be_added_and_a_listed_one_cannot_be_added_twice(shell, page, harness):
    open_fun(shell, page)
    page.fill('[data-k="g-search"]', "example.org")
    wait_for(page, "[...document.querySelectorAll('[data-k=\"g-mlist\"] > .vl-row:not([hidden]) .name b')].some(b => b.textContent === 'example.org')")
    assert member_row(page, "example.org").locator(".name span").inner_text() == "Add this website"
    page.press('[data-k="g-search"]', "Enter")                                # Enter adds the website
    assert page.inner_text('[data-k="g-mcount"]') == "4 members"
    # the same website again is refused: it is a member already
    page.fill('[data-k="g-search"]', "reddit.com")
    wait_for(page, "[...document.querySelectorAll('[data-k=\"g-mlist\"] > .vl-row:not([hidden]) .name b')].some(b => b.textContent === 'Reddit')")
    assert member_row(page, "Reddit").locator("input").is_checked() is True
    assert page.locator(f'{LIST}:has-text("Add this website")').count() == 0
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    assert notice(page) == "✓ Group Fun saved"
    sites = {i["target"]: i for i in harness.db.list_items()}
    assert "example.org" in sites and sites["example.org"]["item_type"] == "site"
    assert item_id(harness, "Example") in group(harness)["members"]               # (named the way the Add tab names it)


def open_add_menu(page):
    """Open "Add member". Playwright scrolls the button into view when it clicks, and that scroll event arrives after the click: it
    would close the menu that just opened. So bring the button into view first and let the scroll settle, then click."""
    page.locator('[data-k="g-add"]').scroll_into_view_if_needed()
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 100))))")
    page.click('[data-k="g-add"]')
    page.wait_for_selector("#menu button:has-text('Website')", state="visible", timeout=5000)


def test_add_member_browses_the_apps(shell, page, harness):
    open_fun(shell, page)
    open_add_menu(page)
    assert [b.inner_text() for b in page.locator("#menu button").all()] == ["App or program file…", "Website…", "Category…"]
    page.click('#menu button:has-text("App or program file")')
    page.wait_for_selector("#dlg[open] .ab-row", timeout=5000)
    page.click('#dlg .ab-row:has-text("Notepad")')
    dialog_closed(page)
    wait_for(page, f"document.querySelectorAll('{LIST}').length === 4")
    assert members(page) == ["Notepad", "Reddit", "Twitch", "YouTube"]
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    pad = next(i for i in harness.db.list_items() if i["display_name"] == "Notepad")
    assert (pad["item_type"], pad["target"], pad["block_type"], pad["app_path"]) == ("app", "notepad.exe", "close", NOTEPAD["path"])
    assert pad["id"] in group(harness)["members"]


def test_add_member_picks_a_whole_category(shell, page, harness):
    """"Category…" is the Add tab's chooser: the categories to pick from, and the pick becomes a new member that Done puts on the
    blocklist as a category item (closed like an app). The same category twice is refused."""
    open_fun(shell, page)
    open_add_menu(page)
    page.click('#menu button:has-text("Category")')
    page.wait_for_selector("#menu button:has-text('Games')", state="visible", timeout=5000)
    assert [b.inner_text() for b in page.locator("#menu button").all()] == ["Productive", "Neutral", "Games", "Distracting"]
    page.click('#menu button:has-text("Games")')
    wait_for(page, f"document.querySelectorAll('{LIST}').length === 4")
    assert members(page) == ["Games", "Reddit", "Twitch", "YouTube"]
    assert member_row(page, "Games").locator(".name span").inner_text() == "category · everything in it"
    assert member_row(page, "Games").locator('[data-act="way"]').inner_text() == "Group's way"
    open_add_menu(page)                                                       # (the same category again: refused)
    page.click('#menu button:has-text("Category")')
    page.wait_for_selector("#menu button:has-text('Games')", state="visible", timeout=5000)
    page.click('#menu button:has-text("Games")')
    wait_for(page, "document.getElementById('toast').textContent.includes('Games is already in this group')")
    assert page.inner_text('[data-k="g-mcount"]') == "4 members"
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    games = next(i for i in harness.db.list_items() if i["display_name"] == "Games")
    assert (games["item_type"], games["target"], games["block_type"], games["source"]) == ("category", "games", "close", "category")
    assert games["id"] in group(harness)["members"] and len(group(harness)["members"]) == 4


def test_a_new_site_and_a_new_steam_game_made_in_the_editor_are_made_like_the_add_tab_makes_them(shell, page, harness):
    harness.ctx.catalog.apps.append({"name": "Counter-Strike 2", "exe": "cs2.exe", "path": r"D:\Steam\cs2\cs2.exe", "running": False,
                                     "steam": True})
    open_fun(shell, page)
    page.fill('[data-k="g-search"]', "tiktok.com")
    wait_for(page, "[...document.querySelectorAll('[data-k=\"g-mlist\"] > .vl-row:not([hidden]) .name b')].some(b => b.textContent === 'tiktok.com')")
    page.press('[data-k="g-search"]', "Enter")
    page.fill('[data-k="g-search"]', "counter")
    wait_for(page, "[...document.querySelectorAll('[data-k=\"g-mlist\"] > .vl-row:not([hidden]) .name b')].some(b => b.textContent === 'Counter-Strike 2')")
    member_row(page, "Counter-Strike 2").locator("label.check").click()
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    items = {i["display_name"]: i for i in harness.db.list_items()}
    assert items["TikTok"]["target"] == "tiktok.com tiktokcdn.com tiktokv.com"
    assert items["Counter-Strike 2"]["block_type"] == "close,background"


def test_a_new_group_needs_a_name_and_a_blocker_then_is_added(shell, page, harness):
    open_groups(shell, page)
    page.click("button.gp-new")
    page.wait_for_selector('[data-k="g-editor"]:not([hidden])')
    assert page.inner_text('[data-k="g-title"]') == "New group"
    assert page.is_hidden('.ge-top button:has-text("Remove group")') and page.is_hidden('.ge-top button:has-text("Disable")')
    assert page.input_value('[data-k="g-name"]') == ""
    # a new group starts as an item does: close the app / can't load the site
    ticked = page.eval_on_selector_all(".ge-wcols input[data-way]:checked", "els => els.map(e => e.dataset.way)")
    assert sorted(ticked) == ["close", "dns"]
    assert page.inner_text('[data-k="g-mhint"]') == "Only ticked members are shown. Type to find more."
    assert "No members yet" in page.inner_text('[data-k="g-mnone"]')
    page.click('.ge-top button:has-text("Done")')
    assert page.inner_text('[data-k="g-nameerr"]') == "Give the group a name."
    assert page.inner_text('.ge [data-zone="rules"]') == "Tick at least one blocker."
    assert len(harness.db.list_groups()) == 1
    # a mistake inside a blocker is shown under its field
    page.fill('[data-k="g-name"]', "Night")
    tick_blocker(page, "time_limit")
    page.fill('.ge .rule-ed[data-type="time_limit"] [data-f="day"]', "banana")
    page.click('.ge-top button:has-text("Done")')
    assert "Can't read" in page.inner_text('.ge .rule-ed[data-type="time_limit"] [data-zone="periods"]')
    page.fill('.ge .rule-ed[data-type="time_limit"] [data-f="day"]', "45m")
    page.fill('[data-k="g-search"]', "twitch")
    wait_for(page, "[...document.querySelectorAll('[data-k=\"g-mlist\"] > .vl-row:not([hidden]) .name b')].some(b => b.textContent === 'Twitch')")
    member_row(page, "Twitch").locator("label.check").click()
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('.gp-card[data-id]:has-text("Night")', timeout=8000)
    assert notice(page) == "✓ Group Night added"
    night = group(harness, "Night")
    assert night["rules"][0]["daily_limit_min"] == 45 and list(night["members"]) == [item_id(harness, "Twitch")]
    assert (night["app_block"], night["site_block"]) == ("close", "dns")
    assert page.is_visible('[data-k="g-blank"]')


def test_disable_enable_and_remove_group(shell, page, harness):
    open_fun(shell, page)
    page.click('.ge-top button:has-text("Disable")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])')
    assert notice(page) == "✓ Group Fun disabled"
    assert group(harness)["disabled"] == 1
    page.wait_for_selector('.gp-card[data-id] .chip:has-text("Disabled - nothing is enforced")')
    page.click(".gp-card[data-id]")
    page.wait_for_selector('.ge-top button:has-text("Enable")')
    page.click('.ge-top button:has-text("Enable")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])')
    assert notice(page) == "✓ Group Fun enabled" and not group(harness)["disabled"]
    # remove is two clicks
    page.click(".gp-card[data-id]")
    page.wait_for_selector('.ge-top button:has-text("Remove group")')
    page.click('.ge-top button:has-text("Remove group")')
    assert len(harness.db.list_groups()) == 1
    page.click('.ge-top button:has-text("Confirm remove")')
    page.wait_for_selector('[data-k="g-none"]:not([hidden])', timeout=8000)
    assert notice(page) == "✓ Group Fun removed" and harness.db.list_groups() == []


def test_edit_group_from_the_overview_and_the_entry_point(shell, page, harness):
    shell(route="blocking")
    page.wait_for_selector(".bo-row", timeout=8000)
    page.locator('.vl-row[data-t="ghead"] [data-act="edit"]').click()
    page.wait_for_selector('[data-tab="groups"][aria-selected="true"]')
    page.wait_for_selector('[data-k="g-editor"]:not([hidden]) [data-k="g-name"]', timeout=8000)
    assert page.input_value('[data-k="g-name"]') == "Fun"
    # from another page
    page.click('#nav [data-nav="dashboard"]')
    page.wait_for_selector('.page-host[data-page="dashboard"]:not([hidden])')
    page.evaluate(f"""async () => {{ const BK = await import('/js/pages/blocking.js'); BK.editGroup({group(harness)["id"]}); }}""")
    page.wait_for_selector('[data-tab="groups"][aria-selected="true"]')
    page.wait_for_selector('[data-k="g-editor"]:not([hidden]) [data-k="g-name"]', timeout=8000)
    assert page.input_value('[data-k="g-name"]') == "Fun"


# ================================================================ a member's way and extra limits

def test_a_members_own_way_dialog(shell, page, harness):
    add_notepad(harness)
    open_fun(shell, page)
    pick_way(page, "Notepad", "Its own way")
    page.wait_for_selector("#dlg[open]")
    assert page.inner_text("#dlg .dlg-h h2") == "How Notepad is blocked"
    assert page.is_checked('#dlg [data-way="close"]')                           # starts from the way it has now
    assert "replaces the group's" in page.inner_text("#dlg .dlg-b")
    page.click('#dlg label:has([data-way="close"])')
    page.click('#dlg button:has-text("OK")')                                    # nothing ticked: refused
    assert "Tick Close app, Minimize or Block internet" in page.inner_text("#dlg .rule-err")
    page.click('#dlg label:has([data-way="minimize"])')
    page.click('#dlg button:has-text("OK")')
    dialog_closed(page)
    assert member_row(page, "Notepad").locator('[data-act="way"]').inner_text() == "Its own way: Minimize"
    pick_way(page, "Notepad", "Group's way")
    assert member_row(page, "Notepad").locator('[data-act="way"]').inner_text() == "Group's way"
    # a cancelled dialog changes nothing
    pick_way(page, "Notepad", "Its own way")
    page.wait_for_selector("#dlg[open]")
    page.click('#dlg button:has-text("Cancel")')
    dialog_closed(page)
    assert member_row(page, "Notepad").locator('[data-act="way"]').inner_text() == "Group's way"


def test_a_site_members_own_way_is_added_on_top_of_the_groups(shell, page, harness):
    g = group(harness)
    harness.db.update_group(g["id"], g["name"], g["rules"], g["members"], False,
                            {"app_block": None, "site_block": "dns", "member_blocks": {}})
    reload_draft(harness)
    open_fun(shell, page)
    pick_way(page, "YouTube", "Its own way")
    page.wait_for_selector("#dlg[open]")
    assert page.is_checked('#dlg [data-way="dns"]') and page.is_disabled('#dlg [data-way="dns"]')       # the group's way: greyed
    assert "Greyed: the group's way" in page.inner_text("#dlg .dlg-b")
    page.click('#dlg label:has([data-way="close"])')
    page.click('#dlg button:has-text("OK")')
    dialog_closed(page)
    assert member_row(page, "YouTube").locator('[data-act="way"]').inner_text() == "Its own way: Close the tab"
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    assert group(harness)["member_blocks"] == {item_id(harness, "YouTube"): "close"}


def test_a_program_file_member_can_only_be_closed_or_minimised(shell, page, harness):
    g = group(harness)
    jar = harness.db.add_item("Tool", ["tool.jar"], "app", "manual", [], block_type="close")
    harness.db.update_group(g["id"], g["name"], g["rules"], {**g["members"], jar: {}}, False,
                            {"app_block": "close", "site_block": None, "member_blocks": {}})
    reload_draft(harness)
    open_fun(shell, page)
    pick_way(page, "Tool", "Its own way")
    page.wait_for_selector("#dlg[open]")
    # (the one set of boxes, js/ways.js: a program file gets the "file" group - Close / Minimize, nothing else to tick)
    assert page.eval_on_selector_all("#dlg [data-way]", "els => els.map(e => e.dataset.way)") == ["close", "minimize"]
    assert "closed or minimised only" in page.inner_text("#dlg .dlg-b")
    page.click('#dlg button:has-text("Cancel")')


def test_the_extra_limits_dialog_validates(shell, page, harness):
    open_fun(shell, page)
    member_row(page, "Reddit").locator('[data-act="extras"]').click()
    page.wait_for_selector("#dlg[open] .rule-sec")
    assert page.inner_text("#dlg .dlg-h h2") == "Extra limits for Reddit"
    assert page.locator("#dlg .rule-sec").count() == 4                          # scheduled, time limit, opening limit, temporary
    assert "No own limits: only the group's rules apply" in page.inner_text("#dlg .cm-empty")
    assert "can only make it stricter" in page.inner_text("#dlg .dlg-b")
    sec = '#dlg .rule-sec[data-t="time_limit"]'
    page.click(f"{sec} .switch")                                                # a switch turned on starts from the group's values
    assert page.input_value(f'{sec} [data-f="day"]') == "1h30"
    assert page.inner_text("#dlg .cm-empty") == ""
    page.fill(f'{sec} [data-f="day"]', "banana")
    page.click('#dlg button:has-text("OK")')
    page.wait_for_selector(f"{sec} [data-zone='periods']:not([hidden])")
    assert dialog_open(page) and "Can't read" in page.inner_text(f"{sec} [data-zone='periods']")
    page.fill(f'{sec} [data-f="day"]', "30m")
    page.click('#dlg button:has-text("OK")')
    dialog_closed(page)
    page.wait_for_selector('[data-act="extras"]:has-text("Extra limits · 1")', timeout=5000)
    assert member_row(page, "Reddit").locator('[data-act="extras"]').inner_text() == "Extra limits · 1"
    # opening it again shows what was set; a cancelled dialog changes nothing
    member_row(page, "Reddit").locator('[data-act="extras"]').click()
    page.wait_for_selector("#dlg[open] .rule-sec")
    assert page.input_value(f'{sec} [data-f="day"]') == "30m"
    page.click(f"{sec} .switch")
    page.click('#dlg button:has-text("Cancel")')
    dialog_closed(page)
    page.wait_for_selector('[data-act="extras"]:has-text("Extra limits · 1")', timeout=5000)
    assert member_row(page, "Reddit").locator('[data-act="extras"]').inner_text() == "Extra limits · 1"
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    assert group(harness)["members"][item_id(harness, "Reddit")]["time_limit"]["daily_limit_min"] == 30


def test_extra_limits_need_the_groups_blockers_to_be_valid_first(shell, page, harness):
    open_fun(shell, page)
    page.fill('.ge .rule-ed[data-type="time_limit"] [data-f="day"]', "banana")
    member_row(page, "Reddit").locator('[data-act="extras"]').click()
    page.wait_for_selector('[data-k="g-err"]:not([hidden])')
    assert "Fix the group's blockers first" in page.inner_text('[data-k="g-err"]')
    assert not dialog_open(page)


# ================================================================ anti-bypass: what asks for the challenge

def test_taking_a_member_out_while_locked_asks_and_the_editor_stays(shell, page, harness):
    lock(harness)
    open_fun(shell, page)
    member_row(page, "Reddit").locator("label.check").click()
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector("dialog[open] .ch-phrase", timeout=5000)
    assert "Loosen group Fun" in page.inner_text("dialog[open] .ch-changes")
    assert len(group(harness)["members"]) == 3                                 # nothing saved while it asks
    cancel_challenge(page)
    settle(page)
    assert page.is_visible('[data-k="g-editor"]') and members(page) == ["Twitch", "YouTube"]    # the editor stays as it was
    assert len(group(harness)["members"]) == 3
    # again, and this time type the phrase
    page.click('.ge-top button:has-text("Done")')
    type_challenge(page)
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    assert notice(page) == "✓ Group Fun saved"
    assert sorted(group(harness)["members"]) == sorted([item_id(harness, "Twitch"), item_id(harness, "YouTube")])


def test_a_lighter_own_way_while_locked_asks_and_a_stricter_one_does_not(shell, page, harness):
    pad = add_notepad(harness, "close")
    lock(harness)
    open_fun(shell, page)
    pick_way(page, "Notepad", "Its own way")                                    # close -> Minimize: lighter
    page.wait_for_selector("#dlg[open]")
    page.click('#dlg label:has([data-way="close"])')
    page.click('#dlg label:has([data-way="minimize"])')
    page.click('#dlg button:has-text("OK")')
    dialog_closed(page)
    assert member_row(page, "Notepad").locator('[data-act="way"]').inner_text() == "Its own way: Minimize"
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector("dialog[open] .ch-phrase", timeout=5000)
    assert "Loosen group Fun" in page.inner_text("dialog[open] .ch-changes")
    assert group(harness)["member_blocks"] == {}
    cancel_challenge(page)
    settle(page)
    assert page.is_visible('[data-k="g-editor"]') and group(harness)["member_blocks"] == {}
    page.click('.ge-top button:has-text("Done")')
    type_challenge(page)
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    assert group(harness)["member_blocks"] == {pad: "minimize"}
    # a stricter way (the group minimises; the member closes) needs nothing
    g = group(harness)
    harness.db.update_group(g["id"], g["name"], g["rules"], g["members"], False,
                            {"app_block": "minimize", "site_block": None, "member_blocks": {}})
    reload_draft(harness)
    page.click(".gp-card[data-id]")
    page.wait_for_selector('[data-k="g-editor"]:not([hidden])')
    wait_for(page, f"document.querySelectorAll('{LIST}').length === 4")
    pick_way(page, "Notepad", "Its own way")
    page.wait_for_selector("#dlg[open]")
    page.click('#dlg label:has([data-way="minimize"])')
    page.click('#dlg label:has([data-way="close"])')
    page.click('#dlg button:has-text("OK")')
    dialog_closed(page)
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    assert not page.locator("dialog[open] .ch-phrase").count()
    assert group(harness)["member_blocks"] == {pad: "close"}


def test_a_raised_shared_limit_and_a_lighter_group_way_ask_for_the_challenge(shell, page, harness):
    add_notepad(harness, "close")
    lock(harness)
    open_fun(shell, page)
    page.fill('.ge .rule-ed[data-type="time_limit"] [data-f="day"]', "3h")       # 1h30 -> 3h
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector("dialog[open] .ch-phrase", timeout=5000)
    assert "Loosen group Fun" in page.inner_text("dialog[open] .ch-changes")
    cancel_challenge(page)
    settle(page)
    assert group(harness)["rules"][0]["daily_limit_min"] == 90
    page.fill('.ge .rule-ed[data-type="time_limit"] [data-f="day"]', "1h")       # lowering is free
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    assert group(harness)["rules"][0]["daily_limit_min"] == 60
    # the group's own way, lighter: close -> minimize
    page.click(".gp-card[data-id]")
    page.wait_for_selector('[data-k="g-editor"]:not([hidden])')
    page.click('.ge-wcols > div:first-child label:has([data-way="close"])')
    page.click('.ge-wcols > div:first-child label:has([data-way="minimize"])')
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector("dialog[open] .ch-phrase", timeout=5000)
    assert "Loosen group Fun" in page.inner_text("dialog[open] .ch-changes")
    cancel_challenge(page)


def test_a_loosened_extra_limit_asks_but_adding_one_does_not(shell, page, harness):
    g = group(harness)
    reddit = item_id(harness, "Reddit")
    harness.db.update_group(g["id"], g["name"], g["rules"],
                            {**g["members"], reddit: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 30}}})
    reload_draft(harness)
    lock(harness)
    open_fun(shell, page)
    assert member_row(page, "Reddit").locator('[data-act="extras"]').inner_text() == "Extra limits · 1"
    member_row(page, "Reddit").locator('[data-act="extras"]').click()
    page.wait_for_selector("#dlg[open] .rule-sec")
    sec = '#dlg .rule-sec[data-t="time_limit"]'
    assert page.input_value(f'{sec} [data-f="day"]') == "30m"
    page.fill(f'{sec} [data-f="day"]', "45m")                                   # 30m -> 45m: looser
    page.click('#dlg button:has-text("OK")')
    dialog_closed(page)
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector("dialog[open] .ch-phrase", timeout=5000)
    assert "Loosen group Fun" in page.inner_text("dialog[open] .ch-changes")
    cancel_challenge(page)
    settle(page)
    assert group(harness)["members"][reddit]["time_limit"]["daily_limit_min"] == 30
    # a tighter one is free
    member_row(page, "Reddit").locator('[data-act="extras"]').click()
    page.wait_for_selector("#dlg[open] .rule-sec")
    page.fill(f'{sec} [data-f="day"]', "20m")
    page.click('#dlg button:has-text("OK")')
    dialog_closed(page)
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('[data-k="g-blank"]:not([hidden])', timeout=8000)
    assert group(harness)["members"][reddit]["time_limit"]["daily_limit_min"] == 20


def test_disabling_and_removing_a_group_while_locked_ask(shell, page, harness):
    lock(harness)
    open_fun(shell, page)
    page.click('.ge-top button:has-text("Disable")')
    page.wait_for_selector("dialog[open] .ch-phrase", timeout=5000)
    assert "Disable group Fun" in page.inner_text("dialog[open] .ch-changes")
    cancel_challenge(page)
    settle(page)
    assert page.is_visible('[data-k="g-editor"]') and not group(harness)["disabled"]            # the editor stays
    page.click('.ge-top button:has-text("Remove group")')
    page.click('.ge-top button:has-text("Confirm remove")')
    page.wait_for_selector("dialog[open] .ch-phrase", timeout=5000)
    assert "Remove group Fun" in page.inner_text("dialog[open] .ch-changes")
    cancel_challenge(page)
    assert len(harness.db.list_groups()) == 1


def test_a_new_group_is_free_while_locked(shell, page, harness):
    lock(harness)
    open_groups(shell, page)
    page.click("button.gp-new")
    page.wait_for_selector('[data-k="g-editor"]:not([hidden])')
    page.fill('[data-k="g-name"]', "Late")
    tick_blocker(page, "permanent")
    page.click('.ge-top button:has-text("Done")')
    page.wait_for_selector('.gp-card[data-id]:has-text("Late")', timeout=8000)
    assert page.locator("dialog[open] .ch-phrase").count() == 0 and notice(page) == "✓ Group Late added"


# ================================================================ live updates and numbers

def test_a_change_made_elsewhere_shows_up_in_the_list(shell, page, harness, push):
    open_groups(shell, page)
    g = group(harness)
    harness.db.update_group(g["id"], "Fun renamed", g["rules"], g["members"])
    reload_draft(harness)
    push("blocks_changed", {"rev": 99})
    page.wait_for_selector('.gp-card[data-id]:has-text("Fun renamed")', timeout=5000)


def test_the_first_open_and_the_editor_are_fast(shell, page, harness, perf, capsys):
    shell(route="blocking")
    page.wait_for_selector(".bo-row", timeout=8000)
    ms = perf.click_until('[data-tab="groups"]', ".gp-card[data-id]", 4000)
    print(f"Blocking > Groups first open: {ms:.0f} ms")
    assert 0 < ms < 400
    t = page.evaluate("""async () => {
      const t0 = performance.now();
      document.querySelector('.gp-card[data-id]').click();
      while (document.querySelectorAll('[data-k="g-mlist"] > .vl-row:not([hidden])').length < 3) await new Promise((r) => requestAnimationFrame(() => r()));
      return performance.now() - t0; }""")
    print(f"Blocking > Groups editor open (3 members): {t:.0f} ms")
    assert t < 300
    s = page.evaluate("""async () => {
      const input = document.querySelector('[data-k="g-search"]');
      const t0 = performance.now();
      input.value = 'spot';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      while (![...document.querySelectorAll('[data-k="g-mlist"] > .vl-row:not([hidden]) .name b')].some((b) => b.textContent === 'Spotify')) await new Promise((r) => requestAnimationFrame(() => r()));
      return performance.now() - t0; }""")
    print(f"Blocking > Groups member search (typed -> rows): {s:.0f} ms")
    assert s < 400


def test_nothing_runs_while_the_window_is_hidden(shell, page, harness, perf):
    open_fun(shell, page)
    settle(page, 400)
    perf.set_hidden(True)
    before = harness.rpc_count()
    perf.reset_timer_calls()
    page.wait_for_timeout(2500)
    assert perf.timer_calls() == {"timeout": 0, "interval": 0, "raf": 0} or sum(perf.timer_calls().values()) == 0
    assert harness.rpc_count() == before
    perf.set_hidden(False)


def test_a_hundred_members_are_a_few_dozen_rows(shell, page, harness):
    g = group(harness)
    ids = [harness.db.add_item(f"Site {i}", [f"many{i}.example"], "site", "manual", [], block_type="dns") for i in range(120)]
    harness.db.update_group(g["id"], g["name"], g["rules"], {**g["members"], **{i: {} for i in ids}})
    reload_draft(harness)
    open_groups(shell, page)
    page.click(".gp-card[data-id]")
    page.wait_for_selector('[data-k="g-editor"]:not([hidden])')
    wait_for(page, f"document.querySelectorAll('{LIST}').length > 5")
    assert page.inner_text('[data-k="g-mcount"]') == "123 members"
    assert page.locator('[data-k="g-mlist"] > .vl-row').count() < 30                  # a virtual list, not 123 rows
    assert page.evaluate("document.querySelector('[data-k=\"g-mlist\"]').clientHeight") > 100


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_no_console_errors_in_a_narrow_window_and_other_themes(shell, page, harness, theme):
    page.emulate_media(color_scheme=theme)
    open_fun(shell, page, width=760, height=800)
    member_row(page, "Reddit").locator('[data-act="extras"]').click()
    page.wait_for_selector("#dlg[open] .rule-sec")
    page.click('#dlg button:has-text("Cancel")')
    dialog_closed(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
