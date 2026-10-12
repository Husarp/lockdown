"""UP14: the site suggestions (js/suggest.js, Api `suggest` / `group_member_search`) in every box that takes a site, in Chrome
against the real Python Api: Blocking > Add, a mode's "+ Other site or app", the group editor's Members box, Site
protection's "Allowed anyway" and the dialog of one of your own lists. A picked site goes in with all its host names; a typed
one does not."""
import pytest

from harness import NOW  # noqa: F401  (the trusted time of the harness)

SG_ROW = ".sg:not([hidden]) .sg-row"


class FakeCatalog:
    def search(self, kind="all", query="", limit=60):
        return {"apps": [], "total": 0, "loading": False}


@pytest.fixture(autouse=True)
def catalog(harness, page):
    harness.ctx.catalog = FakeCatalog()
    page.set_default_timeout(8000)


def wait_for(page, js, timeout=6000):
    page.wait_for_function(js, timeout=timeout)


def dropdown(page, scope=""):
    """(names, subs) of the suggestion rows showing."""
    page.wait_for_selector(f"{scope} {SG_ROW}".strip())
    names = page.eval_on_selector_all(f"{scope} {SG_ROW} b".strip(), "els => els.map(e => e.textContent)")
    subs = page.eval_on_selector_all(f"{scope} {SG_ROW} .sg-name span".strip(), "els => els.map(e => e.textContent)")
    return names, subs


# ---------------------------------------------------------------- Blocking > Add

def test_add_tab_suggests_from_the_big_list_with_the_kind_of_site(shell, page, harness):
    shell(1280, 1000, route="blocking")
    page.wait_for_selector(".bo-row")
    page.click('[data-tab="add"]')
    page.wait_for_selector(".ad-search input")
    page.click(".ad-search input")
    page.keyboard.type("allegro")
    names, subs = dropdown(page)
    assert names[0] == "Allegro" and subs[0] == "allegro.pl · Shopping"
    page.click('.sg-row:has(b:text-is("Allegro"))')
    wait_for(page, "document.querySelectorAll('.ad-pills .pill').length === 1")
    page.click('.rail-row[data-t="permanent"] .rail-main')
    page.click('[data-k="ad-submit"]')
    wait_for(page, "document.querySelector('[data-k=\"notice\"]') && document.querySelector('[data-k=\"notice\"]').textContent.includes('Allegro')")
    item = next(i for i in harness.db.list_items() if i["display_name"] == "Allegro")
    assert item["target"] == "allegro.pl" and item["source"] == "popular"


# ---------------------------------------------------------------- a mode's "+ Other site or app"

def test_a_mode_takes_a_picked_site_with_all_its_hosts(shell, page, harness):
    shell(route="modes")
    page.wait_for_selector(".md-row")
    page.click('.md-row[data-id="relax"] [data-act="edit"]')
    page.wait_for_selector('.md-editor:not([hidden]) [data-k="name"]')
    page.click('.md-editor [data-act="other"]')
    page.fill('.md-editor [data-k="p-text"]', "youtu")
    names, _subs = dropdown(page, ".md-editor")
    assert names[0] == "YouTube"
    page.click('.md-editor .sg-row:has(b:text-is("YouTube"))')
    page.click('.md-editor [data-act="pick-add"]')
    wait_for(page, "document.querySelectorAll('.md-editor [data-k=\"chips\"] .pill').length === 1")
    page.click('.md-editor [data-act="done"]')
    wait_for(page, "document.querySelector('.md-editor').hidden")
    import modes
    extra = next(m for m in modes.load(harness.db) if m["id"] == "relax")["extra"]
    assert extra[0]["name"] == "YouTube" and extra[0]["targets"] == ["youtube.com", "m.youtube.com", "youtu.be", "googlevideo.com"]


# ---------------------------------------------------------------- the group editor's Members box

def test_the_members_box_suggests_listed_sites_and_a_pick_brings_all_hosts(shell, page, harness):
    shell(route="blocking")
    page.wait_for_selector(".bo-row")
    page.click('[data-tab="groups"]')
    page.wait_for_selector(".gp-card, [data-k=\"g-new\"]")
    page.click(".gp-card:has-text(\"New group\")")
    page.wait_for_selector('[data-k="g-editor"]:not([hidden]) [data-k="g-name"]')
    page.fill('[data-k="g-name"]', "Shops")
    page.fill('[data-k="g-search"]', "allegro")
    row = '[data-k="g-mlist"] > .vl-row:not([hidden])'
    wait_for(page, f"[...document.querySelectorAll('{row} .name b')].some(b => b.textContent === 'Allegro')")
    sub = page.locator(f'{row}:has(b:text-is("Allegro")) .name span').inner_text()
    assert sub == "allegro.pl · Shopping"
    page.locator(f'{row}:has(b:text-is("Allegro")) label.check').click()
    assert page.inner_text('[data-k="g-mcount"]') == "1 member"
    # a site already on the blocklist is its item row, not a second "site" row
    page.fill('[data-k="g-search"]', "redd")
    wait_for(page, f"[...document.querySelectorAll('{row} .name b')].some(b => b.textContent === 'Reddit')")
    assert page.locator(f'{row}:has-text("Add this website")').count() == 0
    assert page.locator(f'{row}:has(b:text-is("Reddit"))').count() == 1


# ---------------------------------------------------------------- Site protection > Allowed anyway

def test_allowed_anyway_suggests_sites_and_a_pick_allows_all_its_hosts(shell, page, harness):
    from blocker import protection
    shell(route="blocking")
    page.click('.tabs [data-tab="protect"]')
    page.wait_for_selector('.pl-row[data-key="scam"]')
    page.click('[data-k="a-host"]')
    page.keyboard.type("youtub")
    names, _subs = dropdown(page, ".pl")
    assert names[0] == "YouTube"
    page.click('.sg-row:has(b:text-is("YouTube"))')
    assert page.input_value('[data-k="a-host"]') == "youtube.com"
    page.click('[data-act="allow"]')
    wait_for(page, "document.querySelector('[data-k=\"a-host\"]').value === ''")
    assert set(protection.settings(harness.db)["allowed"]) == {"youtube.com", "m.youtube.com", "youtu.be", "googlevideo.com"}
    # a typed address is allowed on its own
    page.fill('[data-k="a-host"]', "reddit.com")
    page.click('[data-act="allow"]')
    wait_for(page, "document.querySelector('[data-k=\"a-host\"]').value === ''")
    allowed = set(protection.settings(harness.db)["allowed"])
    assert "reddit.com" in allowed and "old.reddit.com" not in allowed


# ---------------------------------------------------------------- one of your own lists

def test_the_list_dialog_suggests_sites_and_a_pick_puts_all_hosts_in(shell, page, harness):
    from blocker import protection
    cfg = protection.settings(harness.db)
    cfg.update(manual=[{"key": "mine1", "name": "Distractions", "entries": []}], enabled=cfg["enabled"] + ["mine1"])
    protection.save_settings(harness.db, cfg)
    shell(route="blocking")
    page.click('.tabs [data-tab="protect"]')
    page.wait_for_selector('.pl-mine [data-act="mine-open"]')
    page.click('.pl-mine [data-act="mine-open"]')
    page.wait_for_selector("#dlg[open] .ml-list")
    page.click('#dlg [data-k="addr"]')
    page.keyboard.type("reddit")
    names, _subs = dropdown(page, "#dlg")
    assert names[0] == "Reddit"
    page.click('#dlg .sg-row:has(b:text-is("Reddit"))')
    wait_for(page, "document.querySelector('#dlg [data-k=\"count\"]').textContent === '5 sites'")
    rows = page.eval_on_selector_all("#dlg .ml-list .vl-row:not([hidden])",
                                     "els => els.sort((a, b) => a._i - b._i).map(e => e.querySelector('.host').textContent)")
    assert rows[0] == "reddit.com" and "redd.it" in rows
