"""R23: Blocking > Site protection (js/pages/blocking/protection.js, js/manuallist.js, js/wordlist.js) in Chrome against the
REAL Python Api on a temporary database (see harness.py).

What is checked: the cards and the list rows with the old window's status table, the switches (off while locked asks for the
challenge and snaps back when cancelled, on is free), the update settings, Connect a list (preview -> Connect, only http(s)),
your own lists and their dialog (20 000 sites in a virtual list, add / remove, the save asks when it loosens), the word-list
dialogs (ticks, search, All on, your words, exceptions), Check a site, Allowed anyway, the download-progress push (one row only),
the local "ago" clock, nothing running while hidden, and the numbers.

What can only be proven on Windows: the page inside WebView2 (this is Chrome), the agent's real watcher thread feeding the push
(here the push is made by the test), real favicons."""
import json
import time

import antibypass as ab
import keywords
from blocker import protection
from harness import NOW

DLG_OPEN = "#dlg[open]"


def wait_for(page, js, timeout=6000):
    page.wait_for_function(js, timeout=timeout)


def settle(page, ms=250):
    page.evaluate("(ms) => new Promise((r) => setTimeout(r, ms))", ms)


def open_tab(shell, page):
    shell(route="blocking")
    page.click('.tabs [data-tab="protect"]')
    page.wait_for_selector('.pl-row[data-key="scam"]', timeout=8000)
    return page


def lock(harness):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "length": 30})


def cfg(harness):
    return protection.settings(harness.db)


def set_cfg(harness, **changes):
    c = cfg(harness)
    c.update(changes)
    protection.save_settings(harness.db, c)


def manual(key="mine1", name="Distractions", hosts=("reddit.com", "x.com")):
    return {"key": key, "name": name, "entries": [{"name": "", "host": h} for h in hosts]}


def asks(page, timeout=5000):
    """A challenge is open (its own layer when a dialog is open already, else the shared dialog)."""
    page.wait_for_selector("dialog[open] textarea.typed", timeout=timeout)


def type_challenge(page):
    asks(page)
    text = page.evaluate("document.querySelector('.ch-phrase').textContent")
    page.focus("dialog[open] textarea.typed")
    page.keyboard.type(text, delay=45)
    page.keyboard.press("Enter")
    wait_for(page, "document.querySelectorAll('.ch-phrase').length === 0", 8000)


def cancel_challenge(page):
    asks(page)
    page.keyboard.press("Escape")
    wait_for(page, "document.querySelectorAll('.ch-phrase').length === 0", 8000)


def challenge_lines(page):
    return page.inner_text("dialog[open] .ch-changes")


def row(page, key):
    return page.locator(f'.pl-row[data-key="{key}"]')


def switch_of(page, key):
    return row(page, key).locator("input")


def status_of(page, key):
    return row(page, key).locator(".pl-stat span").inner_text()


def count_of(page, key):
    return row(page, key).locator(".pl-stat b").inner_text()


def dialog_gone(page, timeout=8000):
    wait_for(page, "!document.getElementById('dlg').open", timeout)


# ---------------------------------------------------------------- the cards and the rows

def test_the_tab_shows_every_card_and_the_lists(harness, shell, page):
    open_tab(shell, page)
    titles = page.eval_on_selector_all('.pl [data-card] h3', "els => els.map(e => e.textContent)")
    assert titles == ["Always-on lists", "Your blocking lists", "Safe search", "Check a site", "Blocked words", "Allowed anyway"]
    keys = page.eval_on_selector_all(".pl-row", "els => els.map(e => e.dataset.key)")
    assert keys == list(protection.LISTS)
    on = {k: switch_of(page, k).is_checked() for k in keys}
    assert on == {"scam": True, "phishing": True, "malware": True, "adult": True, "gambling": False, "anime": False}
    assert status_of(page, "scam") == "waiting for the service" and count_of(page, "scam") == "–"
    assert status_of(page, "gambling") == "off" and count_of(page, "gambling") == ""
    assert "fake shops" in row(page, "scam").locator(".pl-main span").inner_text()
    assert page.is_checked('[data-k="auto"]') and page.input_value('[data-k="every"]') == "24"
    assert page.inner_text('[data-k="mine-empty"]').startswith("No lists yet")
    assert page.inner_text('[data-k="none"]') == "None."
    # the words card, with the old window's lines
    assert page.inner_text('[data-list="adult_en"] [data-k="i"]') == f"{len(keywords.READY['adult_en'][1])} words"
    assert page.inner_text('[data-list="words"] [data-k="i"]') == "none yet"
    assert page.inner_text('[data-list="exceptions"] [data-k="i"]') == "none"


def test_list_rows_follow_the_status_table(harness, shell, page):
    set_cfg(harness, enabled=["scam", "phishing", "malware", "gambling"], info={
        "scam": {"count": 41220, "updated": "2026-10-11T09:00:00"},
        "phishing": {"count": 182004, "updated": "2026-10-11T15:35:00", "failed": "2026-10-11T15:40:00"},
        "malware": {"failed": "2026-10-11T14:00:00"},
        "adult": {"count": 7, "updated": "2026-10-11T15:41:00"}})
    harness.db.set_setting(protection.PROGRESS_KEY, json.dumps({"key": "gambling", "part": 1, "parts": 2, "done": 12_900_000, "total": 47_000_000}))
    open_tab(shell, page)
    assert (count_of(page, "scam"), status_of(page, "scam")) == ("41,220", "updated 6 h ago")
    assert (count_of(page, "phishing"), status_of(page, "phishing")) == ("182,004", "update failed · retrying in 1 h")
    assert (count_of(page, "malware"), status_of(page, "malware")) == ("–", "download failed · retrying in 1 h")
    assert (count_of(page, "adult"), status_of(page, "adult")) == ("7", "updated just now")           # (the list is off, but has a count)
    assert (count_of(page, "gambling"), status_of(page, "gambling")) == ("…", "downloading 12.3 of 44.8 MB (part 1 of 2)")
    assert row(page, "gambling").locator(".pl-meter").is_visible() and not row(page, "scam").locator(".pl-meter").is_visible()
    assert (count_of(page, "anime"), status_of(page, "anime")) == ("", "off")


# ---------------------------------------------------------------- the switches

def test_switching_a_list_off_while_locked_asks_and_snaps_back_when_cancelled(harness, shell, page):
    open_tab(shell, page)
    lock(harness)
    row(page, "scam").locator(".switch").click()
    asks(page)
    assert "Switch the Scam protection list off" in challenge_lines(page)
    assert "scam" in cfg(harness)["enabled"]                                   # nothing is changed while it asks
    cancel_challenge(page)
    wait_for(page, "document.querySelector('.pl-row[data-key=\"scam\"] input').checked === true")
    assert "scam" in cfg(harness)["enabled"]
    row(page, "scam").locator(".switch").click()                               # again, and this time pass it
    type_challenge(page)
    wait_for(page, "document.querySelector('.pl-row[data-key=\"scam\"] input').checked === false")
    assert "scam" not in cfg(harness)["enabled"]
    wait_for(page, "document.querySelector('.pl-row[data-key=\"scam\"] .pl-stat span').textContent === 'off'")


def test_switching_a_list_on_is_free_while_locked(harness, shell, page):
    open_tab(shell, page)
    lock(harness)
    row(page, "gambling").locator(".switch").click()
    wait_for(page, "document.querySelector('.pl-row[data-key=\"gambling\"] input').checked === true")
    settle(page)
    assert "gambling" in cfg(harness)["enabled"] and page.locator("dialog[open]").count() == 0
    assert status_of(page, "gambling") == "waiting for the service"
    row(page, "gambling").locator(".switch").click()                           # the exact undo within 10 seconds: free (grace)
    wait_for(page, "document.querySelector('.pl-row[data-key=\"gambling\"] input').checked === false")
    settle(page)
    assert "gambling" not in cfg(harness)["enabled"] and page.locator("dialog[open]").count() == 0


def test_update_settings_and_update_now(harness, shell, page):
    open_tab(shell, page)
    lock(harness)                                                              # (none of this needs the challenge)
    page.select_option('[data-k="every"]', "168")
    settle(page)
    assert cfg(harness)["every_hours"] == 168 and cfg(harness)["auto"] is True
    page.locator('.pl-tools .switch').click()
    wait_for(page, "document.querySelector('[data-k=\"every\"]').disabled === true")
    assert cfg(harness)["auto"] is False and page.input_value('[data-k="every"]') == "168"
    page.locator('.pl-tools .switch').click()
    wait_for(page, "document.querySelector('[data-k=\"every\"]').disabled === false")
    page.click('[data-act="update-now"]')
    page.wait_for_selector("text=Lists will update in a few seconds")
    assert cfg(harness)["update_now"] == NOW.isoformat(timespec="seconds")


# ---------------------------------------------------------------- Connect a list

def fake_preview(monkeypatch, harness, result=(1234, ["a.com", "b.com"])):
    harness.ctx.pushed = []
    harness.ctx.publish = lambda event, payload=None, key=None: harness.ctx.pushed.append((event, payload))
    monkeypatch.setattr(protection, "preview", lambda url: (_ for _ in ()).throw(result) if isinstance(result, Exception) else result)


def deliver(harness, page, push, event="protection_preview"):
    """What the Publisher does: hand what Python pushed to the page."""
    for _ in range(300):
        got = [p for p in harness.ctx.pushed if p[0] == event]
        if got:
            push(*got[-1])
            return got[-1][1]
        time.sleep(0.01)
    raise AssertionError("nothing was pushed")


def test_preview_then_connect(harness, shell, page, push, monkeypatch):
    fake_preview(monkeypatch, harness)
    open_tab(shell, page)
    lock(harness)
    assert page.is_hidden('[data-k="c-go"]')
    page.fill('[data-k="c-name"]', "Crypto")
    page.fill('[data-k="c-url"]', "ftp://example.com/list.txt")
    page.click('[data-act="preview"]')
    wait_for(page, "document.querySelector('[data-k=\"c-msg\"]').textContent.includes('full address')")
    assert "pl-bad" in page.get_attribute('[data-k="c-msg"]', "class") and page.is_hidden('[data-k="c-go"]')
    page.fill('[data-k="c-url"]', "https://example.com/list.txt")
    page.click('[data-act="preview"]')
    wait_for(page, "document.querySelector('[data-k=\"c-msg\"]').textContent.startsWith('Downloading the list')")
    deliver(harness, page, push)
    wait_for(page, "document.querySelector('[data-k=\"c-msg\"]').textContent === '1,234 sites, e.g. a.com, b.com'")
    assert page.is_visible('[data-k="c-go"]')
    page.fill('[data-k="c-url"]', "https://example.com/list2.txt")             # another address: the preview doesn't count
    assert page.is_hidden('[data-k="c-go"]')
    page.fill('[data-k="c-url"]', "https://example.com/list.txt")
    harness.ctx.pushed.clear()
    page.click('[data-act="preview"]')
    deliver(harness, page, push)
    wait_for(page, "document.querySelector('[data-k=\"c-go\"]').hidden === false")
    page.click('[data-k="c-go"]')                                              # connecting is free: a new list only blocks more
    page.wait_for_selector('.pl-row[data-key="custom1"]')
    assert "Connected Crypto (1,234 sites)" in page.inner_text('[data-k="c-msg"]')
    assert page.input_value('[data-k="c-name"]') == "" and page.is_hidden('[data-k="c-go"]')
    assert cfg(harness)["custom"] == [{"key": "custom1", "name": "Crypto", "url": "https://example.com/list.txt"}]
    assert "custom1" in cfg(harness)["enabled"]
    assert row(page, "custom1").locator(".pl-main span").inner_text() == "your list · https://example.com/list.txt"
    assert status_of(page, "custom1") == "waiting for the service"


def test_a_failed_preview_says_why(harness, shell, page, push, monkeypatch):
    fake_preview(monkeypatch, harness, result=ValueError("No sites found there - is it a block list (hosts file, domains or adblock format)?"))
    open_tab(shell, page)
    page.fill('[data-k="c-url"]', "https://example.com/nothing.txt")
    page.click('[data-act="preview"]')
    deliver(harness, page, push)
    wait_for(page, "document.querySelector('[data-k=\"c-msg\"]').textContent.startsWith('No sites found there')")
    assert page.is_hidden('[data-k="c-go"]') and "pl-bad" in page.get_attribute('[data-k="c-msg"]', "class")


def test_removing_your_url_list_while_it_is_on_asks(harness, shell, page):
    set_cfg(harness, custom=[{"key": "custom1", "name": "Crypto", "url": "https://example.com/l.txt"}], enabled=cfg(harness)["enabled"] + ["custom1"])
    open_tab(shell, page)
    lock(harness)
    row(page, "custom1").locator(".confirm").click()
    row(page, "custom1").locator(".confirm").click()                            # two clicks
    asks(page)
    assert "Remove your Crypto list" in challenge_lines(page)
    cancel_challenge(page)
    settle(page)
    assert row(page, "custom1").is_visible() and cfg(harness)["custom"]
    row(page, "custom1").locator(".confirm").click()
    row(page, "custom1").locator(".confirm").click()
    type_challenge(page)
    page.wait_for_selector('.pl-row[data-key="custom1"]', state="detached")
    assert cfg(harness)["custom"] == []


# ---------------------------------------------------------------- your own lists

def test_create_a_list_with_enter_and_remove_it(harness, shell, page):
    open_tab(shell, page)
    lock(harness)
    page.fill('[data-k="m-name"]', "  Distractions  ")
    page.press('[data-k="m-name"]', "Enter")
    page.wait_for_selector(".pl-mine")
    assert page.inner_text(".pl-mine b") == "Distractions" and page.inner_text(".pl-mine .count") == "0 sites"
    assert page.is_hidden('[data-k="mine-empty"]') and page.input_value('[data-k="m-name"]') == ""
    assert page.is_checked(".pl-mine input")
    assert cfg(harness)["manual"][0]["name"] == "Distractions"
    page.locator(".pl-mine .confirm").click()
    page.locator(".pl-mine .confirm").click()                                   # (made a moment ago: the undo is free)
    wait_for(page, "document.querySelectorAll('.pl-mine').length === 0")
    assert cfg(harness)["manual"] == [] and page.is_visible('[data-k="mine-empty"]')


def test_switching_your_list_off_while_locked_asks(harness, shell, page):
    set_cfg(harness, manual=[manual()], enabled=cfg(harness)["enabled"] + ["mine1"])
    open_tab(shell, page)
    lock(harness)
    page.locator(".pl-mine .switch").click()
    asks(page)
    assert "Turn your Distractions list off" in challenge_lines(page)
    cancel_challenge(page)
    wait_for(page, "document.querySelector('.pl-mine input').checked === true")
    assert "mine1" in cfg(harness)["enabled"]


def open_manual(page):
    page.click('.pl-mine [data-act="mine-open"]')
    page.wait_for_selector(f"{DLG_OPEN} .ml-list")
    wait_for(page, "document.querySelectorAll('#dlg .ml-list .vl-row:not([hidden])').length > 0")


def manual_rows(page):
    return page.eval_on_selector_all("#dlg .ml-list .vl-row:not([hidden])",
                                     "els => els.sort((a, b) => a._i - b._i).map(e => e.querySelector('.host').textContent)")


def test_the_list_dialog_adds_on_top_normalises_and_ignores_duplicates(harness, shell, page):
    set_cfg(harness, manual=[manual()], enabled=cfg(harness)["enabled"] + ["mine1"])
    open_tab(shell, page)
    open_manual(page)
    assert page.inner_text("#dlg h2") == "Distractions" and page.inner_text('#dlg [data-k="count"]') == "2 sites"
    assert manual_rows(page) == ["reddit.com", "x.com"]
    page.fill('#dlg [data-k="name"]', "  The   news ")
    page.fill('#dlg [data-k="addr"]', "https://www.News.com/path?x=1")
    page.press('#dlg [data-k="addr"]', "Enter")
    wait_for(page, "document.querySelector('#dlg [data-k=\"count\"]').textContent === '3 sites'")
    assert manual_rows(page) == ["news.com", "reddit.com", "x.com"]            # normalised, on top
    assert page.locator("#dlg .ml-row", has_text="news.com").locator(".label").inner_text() == "· The news"
    page.fill('#dlg [data-k="addr"]', "REDDIT.com")
    page.click('#dlg [data-act="add"]')                                         # a duplicate: ignored
    wait_for(page, "document.querySelector('#dlg .ml-err').textContent.length > 0")
    assert page.inner_text('#dlg [data-k="count"]') == "3 sites"
    page.fill('#dlg [data-k="addr"]', "not a site")
    page.press('#dlg [data-k="addr"]', "Enter")
    wait_for(page, "document.querySelector('#dlg .ml-err').textContent.startsWith('Not a valid domain')")
    page.locator("#dlg .ml-row", has_text="x.com").locator('[data-act="x"]').click()
    wait_for(page, "document.querySelector('#dlg [data-k=\"count\"]').textContent === '2 sites'")
    assert manual_rows(page) == ["news.com", "reddit.com"]
    assert [e["host"] for e in cfg(harness)["manual"][0]["entries"]] == ["reddit.com", "x.com"]       # nothing saved yet
    page.click('#dlg button:has-text("Cancel")')
    dialog_gone(page)
    assert [e["host"] for e in cfg(harness)["manual"][0]["entries"]] == ["reddit.com", "x.com"]


def test_saving_a_list_that_loses_sites_asks_while_locked_and_keeps_the_edits(harness, shell, page):
    set_cfg(harness, manual=[manual(hosts=("a.com", "b.com", "c.com"))], enabled=cfg(harness)["enabled"] + ["mine1"])
    open_tab(shell, page)
    open_manual(page)
    lock(harness)
    page.locator("#dlg .ml-row", has_text="b.com").locator('[data-act="x"]').click()
    page.click('#dlg button:has-text("Save")')
    asks(page)
    assert "Remove sites from your Distractions list" in challenge_lines(page)
    cancel_challenge(page)
    assert page.locator(DLG_OPEN).count() == 1 and manual_rows(page) == ["a.com", "c.com"]       # still open, the edit is still there
    assert [e["host"] for e in cfg(harness)["manual"][0]["entries"]] == ["a.com", "b.com", "c.com"]
    page.click('#dlg button:has-text("Save")')
    type_challenge(page)
    dialog_gone(page)
    assert [e["host"] for e in cfg(harness)["manual"][0]["entries"]] == ["a.com", "c.com"]
    wait_for(page, "document.querySelector('.pl-mine .count').textContent === '2 sites'")


def test_adding_sites_to_a_list_that_is_on_is_free(harness, shell, page):
    set_cfg(harness, manual=[manual(hosts=("a.com",))], enabled=cfg(harness)["enabled"] + ["mine1"])
    open_tab(shell, page)
    open_manual(page)
    lock(harness)
    page.fill('#dlg [data-k="addr"]', "new.com")
    page.press('#dlg [data-k="addr"]', "Enter")
    wait_for(page, "document.querySelector('#dlg [data-k=\"count\"]').textContent === '2 sites'")
    page.click('#dlg button:has-text("Save")')
    dialog_gone(page)
    assert [e["host"] for e in cfg(harness)["manual"][0]["entries"]] == ["new.com", "a.com"]
    wait_for(page, "document.querySelector('.pl-mine .count').textContent === '2 sites'")


def test_a_list_of_20000_sites_scrolls_smoothly_with_few_rows(harness, shell, page, perf):
    hosts = [f"site{i}.example.com" for i in range(20_000)]
    set_cfg(harness, manual=[manual(hosts=hosts)], enabled=cfg(harness)["enabled"] + ["mine1"])
    open_tab(shell, page)
    t = time.perf_counter()
    page.click('.pl-mine [data-act="mine-open"]')
    page.wait_for_selector(f"{DLG_OPEN} .ml-list .vl-row:not([hidden])")
    opened_ms = (time.perf_counter() - t) * 1000
    assert page.inner_text('#dlg [data-k="count"]') == "20,000 sites"
    nodes = page.eval_on_selector_all("#dlg .ml-list .vl-row", "els => els.length")
    assert nodes <= 80, nodes
    r = perf.scroll_frames("#dlg .ml-list", seconds=2.0, probe="#dlg .ml-list .vl-row:not([hidden])")
    print(f"\nmanual list, 20 000 sites: opened in {opened_ms:.0f} ms, {nodes} row nodes, scroll {r['frames']} frames, "
          f"max gap {r['max_ms']:.1f} ms, p95 {r['p95_ms']:.1f} ms, live rows max {r['live_max']}")
    assert r["live_max"] <= 80 and r["p95_ms"] <= 50 and r["max_ms"] <= 250        # (a busy machine may drop one frame)
    # add and remove inside the big list
    page.fill('#dlg [data-k="addr"]', "first.example.org")
    page.press('#dlg [data-k="addr"]', "Enter")
    wait_for(page, "document.querySelector('#dlg [data-k=\"count\"]').textContent === '20,001 sites'")
    assert manual_rows(page)[0] == "first.example.org"
    page.locator("#dlg .ml-row", has_text="first.example.org").locator('[data-act="x"]').click()
    wait_for(page, "document.querySelector('#dlg [data-k=\"count\"]').textContent === '20,000 sites'")
    assert page.eval_on_selector_all("#dlg .ml-list .vl-row", "els => els.length") <= 80
    page.click('#dlg button:has-text("Cancel")')


def test_opening_a_list_twice_opens_one_dialog(harness, shell, page):
    set_cfg(harness, manual=[manual()], enabled=cfg(harness)["enabled"] + ["mine1"])
    open_tab(shell, page)
    page.click('.pl-mine [data-act="mine-open"]')
    page.evaluate("() => document.querySelector('.pl-mine [data-act=\"mine-open\"]').click()")   # (a double click)
    page.wait_for_selector(f"{DLG_OPEN} .ml-list")
    settle(page)
    assert page.locator("dialog[open]").count() == 1 and page.locator("#dlg .ml-list").count() == 1


# ---------------------------------------------------------------- safe search and the words

def test_safe_search_and_the_words_switches_ask_when_they_loosen(harness, shell, page):
    open_tab(shell, page)
    lock(harness)
    for sel, line in (('[data-k="safesearch"]', "Turn forced SafeSearch off"), ('[data-k="youtube"]', "Turn YouTube Restricted Mode off"),
                      ('[data-k="enabled"]', "Turn the blocked-words check off")):
        page.locator(f".switch:has({sel})").click()
        asks(page)
        assert line in challenge_lines(page)
        cancel_challenge(page)
        wait_for(page, f"document.querySelector('{sel}').checked === true")
    assert keywords.settings(harness.db)["safesearch"] and keywords.settings(harness.db)["enabled"]
    page.locator('.switch:has([data-k="youtube"])').click()
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"youtube\"]').checked === false")
    assert keywords.settings(harness.db)["youtube"] is False


def test_the_action_is_free_and_the_word_lists_follow_their_switches(harness, shell, page):
    open_tab(shell, page)
    lock(harness)
    page.click('[data-seg="action"] [data-v="back"]')
    settle(page)
    assert keywords.settings(harness.db)["action"] == "back" and page.locator("dialog[open]").count() == 0
    assert page.get_attribute('[data-seg="action"] [data-v="back"]', "aria-pressed") == "true"
    page.locator('[data-list="adult_pl"] .switch').click()                      # a ready list off: asks
    asks(page)
    assert "Turn the Adult words - Polish list off" in challenge_lines(page)
    cancel_challenge(page)
    wait_for(page, "document.querySelector('[data-list=\"adult_pl\"] input').checked === true")
    page.locator('[data-list="words"] .switch').click()
    asks(page)
    assert "Turn your blocked words off" in challenge_lines(page)
    cancel_challenge(page)
    wait_for(page, "document.querySelector('[data-list=\"words\"] input').checked === true")


def test_turning_exceptions_back_on_asks(harness, shell, page):
    keywords.save(harness.db, {**keywords.settings(harness.db), "exceptions_on": False})
    open_tab(shell, page)
    lock(harness)
    assert not page.is_checked('[data-list="exceptions"] input')
    page.locator('[data-list="exceptions"] .switch').click()
    asks(page)
    assert "Turn exceptions back on" in challenge_lines(page)
    cancel_challenge(page)
    wait_for(page, "document.querySelector('[data-list=\"exceptions\"] input').checked === false")


def open_words(page, name):
    page.click(f'[data-list="{name}"] [data-act="word-open"]')
    page.wait_for_selector(f"{DLG_OPEN} .wl-list")
    wait_for(page, "document.querySelectorAll('#dlg .wl-list .vl-row:not([hidden])').length > 0 || document.querySelector('#dlg .wl-empty') !== null")


def word_rows(page):
    return page.eval_on_selector_all("#dlg .wl-list .vl-row:not([hidden])",
                                     "els => els.sort((a, b) => a._i - b._i).map(e => e.textContent.trim())")


def tick_word(page, word):
    """Flip one word's tick: find it with the search (the rows of a virtual list move when it scrolls), click it, clear the search."""
    page.fill('#dlg [data-k="search"]', word)
    wait_for(page, f"[...document.querySelectorAll('#dlg .wl-list .vl-row:not([hidden])')].some(r => r.textContent.trim() === '{word}')")
    page.locator("#dlg .wl-list .vl-row:not([hidden])", has=page.locator(f"text=/^{word}$/")).first.locator(".check").click()
    page.fill('#dlg [data-k="search"]', "")
    wait_for(page, "document.querySelectorAll('#dlg .wl-list .vl-row:not([hidden])').length > 3")


def test_the_ready_list_dialog_ticks_searches_and_saves(harness, shell, page, perf):
    words = keywords.READY["adult_en"][1]
    open_tab(shell, page)
    open_words(page, "adult_en")
    assert page.inner_text("#dlg h2") == "Adult words - English"
    assert page.inner_text('#dlg [data-k="count"]') == f"{len(words)} of {len(words)} words blocked"
    assert page.eval_on_selector_all("#dlg .wl-list .vl-row", "els => els.length") <= 30
    assert all(page.eval_on_selector_all("#dlg .wl-list .vl-row:not([hidden]) input", "els => els.map(e => e.checked)"))
    tick_word(page, "xxx")
    assert page.inner_text('#dlg [data-k="count"]') == f"{len(words) - 1} of {len(words)} words blocked"
    # search: the list is filtered one frame after the 120 ms debounce
    ms = page.evaluate("""async () => {
        const input = document.querySelector('#dlg [data-k="search"]');
        const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
        await frame();
        const t0 = performance.now();
        input.value = 'hentia';
        input.dispatchEvent(new Event('input', {bubbles: true}));
        while (document.querySelectorAll('#dlg .wl-list .vl-row:not([hidden])').length !== 1) {
          await frame();
          if (performance.now() - t0 > 2000) return -1;
        }
        return performance.now() - t0; }""")
    print(f"\nword list search: filtered {ms:.0f} ms after typing (debounce 120 ms)")
    assert 0 < ms < 400
    assert word_rows(page) == ["hentai*"]
    page.fill('#dlg [data-k="search"]', "xxx")
    wait_for(page, "document.querySelectorAll('#dlg .wl-list .vl-row:not([hidden])').length >= 1 && "
                   "[...document.querySelectorAll('#dlg .wl-list .vl-row:not([hidden])')].some(r => r.textContent.trim() === 'xxx')")
    assert page.locator("#dlg .wl-row", has_text="xxx").first.locator("input").is_checked() is False       # (the tick survived the search)
    page.fill('#dlg [data-k="search"]', "")
    wait_for(page, f"document.querySelector('#dlg [data-k=\"count\"]').textContent === '{len(words) - 1} of {len(words)} words blocked'")
    page.click('#dlg button:has-text("All on")')
    assert page.inner_text('#dlg [data-k="count"]') == f"{len(words)} of {len(words)} words blocked"
    tick_word(page, "xxx")
    lock(harness)
    page.click('#dlg button:has-text("Save")')                                   # a word off weakens the check: asks
    asks(page)
    assert "Stop blocking 1 word: xxx" in challenge_lines(page)
    cancel_challenge(page)
    assert page.locator(DLG_OPEN).count() == 1 and keywords.settings(harness.db)["off"] == []
    page.click('#dlg button:has-text("Save")')
    type_challenge(page)
    dialog_gone(page)
    assert keywords.settings(harness.db)["off"] == ["xxx"]
    wait_for(page, f"document.querySelector('[data-list=\"adult_en\"] [data-k=\"i\"]').textContent === '{len(words)} words (1 off)'")


def test_your_words_dialog_adds_lowercased_on_top_and_saving_more_is_free(harness, shell, page):
    open_tab(shell, page)
    open_words(page, "words")
    assert page.inner_text("#dlg h2") == "Your words" and page.inner_text('#dlg [data-k="count"]') == "0 entries"
    assert page.get_attribute('#dlg [data-k="add"]', "placeholder") == "a word or a few words"
    lock(harness)
    for text in ("  Free   SPINS ", "casino*", "FREE spins", "   "):
        page.fill('#dlg [data-k="add"]', text)
        page.press('#dlg [data-k="add"]', "Enter")
    assert word_rows(page) == ["casino*", "free spins"] and page.inner_text('#dlg [data-k="count"]') == "2 entries"
    page.click('#dlg button:has-text("Save")')                                   # adding words only: free
    dialog_gone(page)
    assert keywords.settings(harness.db)["words"] == ["casino*", "free spins"]
    wait_for(page, "document.querySelector('[data-list=\"words\"] [data-k=\"i\"]').textContent === '2 words'")
    open_words(page, "words")                                                    # take one out: asks
    page.locator("#dlg .wl-row", has_text="casino*").locator('[data-act="x"]').click()
    assert page.inner_text('#dlg [data-k="count"]') == "1 entry"
    page.click('#dlg button:has-text("Save")')
    asks(page)
    assert "Stop blocking 1 word: casino*" in challenge_lines(page)
    cancel_challenge(page)
    assert page.locator(DLG_OPEN).count() == 1 and keywords.settings(harness.db)["words"] == ["casino*", "free spins"]
    page.click('#dlg button:has-text("Cancel")')
    dialog_gone(page)


def test_the_exceptions_dialog_save_asks_because_an_exception_weakens_the_check(harness, shell, page):
    open_tab(shell, page)
    open_words(page, "exceptions")
    assert page.get_attribute('#dlg [data-k="add"]', "placeholder").startswith("site (e.g. wikipedia.org)")
    lock(harness)
    page.fill('#dlg [data-k="add"]', "Wikipedia.org")
    page.click('#dlg [data-act="add"]')
    assert word_rows(page) == ["wikipedia.org"]
    page.click('#dlg button:has-text("Save")')
    asks(page)
    assert "New exceptions: wikipedia.org" in challenge_lines(page)
    type_challenge(page)
    dialog_gone(page)
    assert keywords.settings(harness.db)["exceptions"] == ["wikipedia.org"]
    wait_for(page, "document.querySelector('[data-list=\"exceptions\"] [data-k=\"i\"]').textContent === '1 entry'")


# ---------------------------------------------------------------- Check a site and Allowed anyway

def write_list(harness, tmp_path, key, lines):
    folder = tmp_path / "lists"
    folder.mkdir(parents=True, exist_ok=True)
    protection.list_path(key, folder).write_text("\n".join(lines) + "\n")
    harness.ctx.protection_dir = folder


def check(page, text):
    page.fill('[data-k="k-host"]', text)
    page.press('[data-k="k-host"]', "Enter")
    wait_for(page, "!document.querySelector('[data-k=\"k-msg\"]').textContent.startsWith('Checking')")
    return page.inner_text('[data-k="k-msg"]')


def test_check_a_site(harness, shell, page, tmp_path):
    write_list(harness, tmp_path, "scam", ["fake-shop.com"])
    write_list(harness, tmp_path, "gambling", ["bet.example.com"])               # a list that is off
    set_cfg(harness, manual=[manual("mine1", "Distractions", ("reddit.com",))], enabled=cfg(harness)["enabled"] + ["mine1"], allowed=["ok.example"])
    open_tab(shell, page)
    lock(harness)
    assert check(page, "https://www.Fake-Shop.com/x") == "fake-shop.com is on the Scam list."
    assert "pl-bad" in page.get_attribute('[data-k="k-msg"]', "class") and page.inner_text('[data-k="k-act"]') == "Allow anyway"
    assert check(page, "bet.example.com") == "bet.example.com is on the Gambling (list is off) list."
    assert check(page, "old.reddit.com") == "old.reddit.com is on the Distractions (your list) list."
    assert check(page, "nothing.example") == "nothing.example isn't on any list." and page.is_hidden('[data-k="k-act"]')
    assert "pl-good" in page.get_attribute('[data-k="k-msg"]', "class")
    assert check(page, "not a site").startswith("Not a valid domain") and "pl-bad" in page.get_attribute('[data-k="k-msg"]', "class")
    assert page.is_hidden('[data-k="k-act"]')
    assert check(page, "ok.example") == "ok.example is allowed anyway by you." and page.inner_text('[data-k="k-act"]') == "Block again"
    # Block again: stricter, free; then the same site is on no list but the check says so
    page.click('[data-k="k-act"]')
    wait_for(page, "document.querySelector('[data-k=\"k-msg\"]').textContent === \"ok.example isn't on any list.\"")
    assert cfg(harness)["allowed"] == []
    # Allow anyway on a listed site: asks while locked
    check(page, "fake-shop.com")
    page.click('[data-k="k-act"]')
    asks(page)
    assert "Allow fake-shop.com (and its subdomains) although a protection list blocks it" in challenge_lines(page)
    cancel_challenge(page)
    settle(page)
    assert cfg(harness)["allowed"] == []
    page.click('[data-k="k-act"]')
    type_challenge(page)
    wait_for(page, "document.querySelector('[data-k=\"k-msg\"]').textContent === 'fake-shop.com is allowed anyway by you.'")
    assert cfg(harness)["allowed"] == ["fake-shop.com"]
    assert page.inner_text('[data-k="k-act"]') == "Block again"


def test_allowed_anyway_chips_entry_and_errors(harness, shell, page):
    set_cfg(harness, allowed=["khanacademy.org", "docs.python.org"])
    open_tab(shell, page)
    lock(harness)
    chips = page.eval_on_selector_all(".pl-chip .ellip", "els => els.map(e => e.textContent)")
    assert chips == ["docs.python.org", "khanacademy.org"]                       # sorted
    assert page.is_hidden('[data-k="none"]')
    assert page.eval_on_selector_all(".pl-chip img", "els => els.map(e => e.dataset.icon)") == ["site:docs.python.org", "site:khanacademy.org"]
    page.locator(".pl-chip", has_text="docs.python.org").locator('[data-act="unallow"]').click()      # x: free
    wait_for(page, "document.querySelectorAll('.pl-chip').length === 1")
    assert cfg(harness)["allowed"] == ["khanacademy.org"] and page.locator("dialog[open]").count() == 0
    page.fill('[data-k="a-host"]', "not a site")
    page.press('[data-k="a-host"]', "Enter")
    wait_for(page, "document.querySelector('[data-k=\"a-err\"]').textContent.startsWith('Not a valid domain')")
    assert page.is_visible('[data-k="a-err"]')
    page.fill('[data-k="a-host"]', "https://www.Example.org/page")
    page.press('[data-k="a-host"]', "Enter")                                      # allowing loosens: asks
    asks(page)
    assert "Allow example.org (and its subdomains) although a protection list blocks it" in challenge_lines(page)
    type_challenge(page)
    wait_for(page, "document.querySelectorAll('.pl-chip').length === 2")
    assert cfg(harness)["allowed"] == ["khanacademy.org", "example.org"]
    assert page.input_value('[data-k="a-host"]') == "" and page.is_hidden('[data-k="a-err"]')
    page.locator(".pl-chip", has_text="example.org").locator('[data-act="unallow"]').click()
    wait_for(page, "document.querySelectorAll('.pl-chip').length === 1")
    page.locator(".pl-chip", has_text="khanacademy.org").locator('[data-act="unallow"]').click()
    wait_for(page, "document.querySelectorAll('.pl-chip').length === 0")
    assert page.is_visible('[data-k="none"]')


def test_allowed_site_blocked_words_switch(harness, shell, page):
    set_cfg(harness, allowed=["old.example", "dict.example"], allowed_words=["dict.example"])
    open_tab(shell, page)
    lock(harness)
    box = lambda host: page.locator(".pl-chip", has_text=host).locator('[data-act="allow-words"]')
    assert box("old.example").is_checked() is False and box("dict.example").is_checked() is True     # existing: as saved (off)
    assert page.locator('[data-k="a-words"]').is_checked()                                          # a new entry starts on
    box("dict.example").uncheck()                                                                   # off: free
    settle(page)
    assert cfg(harness)["allowed_words"] == [] and page.locator("dialog[open]").count() == 0
    box("old.example").check()                                                                      # on: loosens, asks
    asks(page)
    assert "Stop checking blocked words on old.example" in challenge_lines(page)
    cancel_challenge(page)
    settle(page)
    assert cfg(harness)["allowed_words"] == [] and box("old.example").is_checked() is False         # snaps back
    box("old.example").check()
    type_challenge(page)
    wait_for(page, "document.querySelectorAll('.pl-chip [data-act=\"allow-words\"]:checked').length === 1")
    assert cfg(harness)["allowed_words"] == ["old.example"]


def test_allowing_a_new_site_with_or_without_the_words_switch(harness, shell, page):
    open_tab(shell, page)
    unlock = lambda: ab.save(harness.db, {**ab.settings(harness.db), "phrase": False})
    unlock()
    page.fill('[data-k="a-host"]', "dictionary.example")
    page.press('[data-k="a-host"]', "Enter")
    wait_for(page, "document.querySelectorAll('.pl-chip').length === 1")
    assert cfg(harness)["allowed_words"] == ["dictionary.example"]
    page.locator('[data-k="a-words"]').uncheck()
    page.fill('[data-k="a-host"]', "plain.example")
    page.press('[data-k="a-host"]', "Enter")
    wait_for(page, "document.querySelectorAll('.pl-chip').length === 2")
    assert cfg(harness)["allowed"] == ["dictionary.example", "plain.example"] and cfg(harness)["allowed_words"] == ["dictionary.example"]


# ---------------------------------------------------------------- live

def test_a_download_progress_push_updates_one_row_only(harness, shell, page, push):
    set_cfg(harness, info={"scam": {"count": 100, "updated": "2026-10-11T15:30:00"}})
    open_tab(shell, page)
    page.evaluate("""() => {
        document.querySelectorAll('.pl-row').forEach((n) => { n.__tag = n.dataset.key; });
        window.__touched = new Set();
        new MutationObserver((records) => { for (const r of records) {
          const node = r.target.nodeType === 1 ? r.target : r.target.parentElement;
          const owner = node && node.closest('.pl-row');
          window.__touched.add(owner ? owner.dataset.key : 'outside:' + (node && node.className)); } })
          .observe(document.querySelector('.pl'), {subtree: true, childList: true, attributes: true, characterData: true});
    }""")
    calls = harness.rpc_count("protection_get")
    from webapi import protection as api
    live_rev = api.info_rev(protection.settings(harness.db))
    push("protection_progress", {"key": "adult", "text": "12.3 of 45.0 MB (part 1 of 2)", "pct": 27, "rev": live_rev})
    wait_for(page, "document.querySelector('.pl-row[data-key=\"adult\"] .pl-stat span').textContent.startsWith('downloading')")
    assert (count_of(page, "adult"), status_of(page, "adult")) == ("…", "downloading 12.3 of 45.0 MB (part 1 of 2)")
    assert page.eval_on_selector(".pl-row[data-key=\"adult\"] .pl-meter i", "e => e.style.width") == "27%"
    assert page.evaluate("[...window.__touched]") == ["adult"]                         # nothing else was written
    assert page.eval_on_selector_all(".pl-row", "els => els.every(n => n.__tag === n.dataset.key)")
    # a newer number: still only that row
    push("protection_progress", {"key": "adult", "text": "30.0 of 45.0 MB (part 1 of 2)", "pct": 66, "rev": live_rev})
    wait_for(page, "document.querySelector('.pl-row[data-key=\"adult\"] .pl-stat span').textContent.includes('30.0 of')")
    assert page.evaluate("[...window.__touched]") == ["adult"]
    # it moves on to another list: the first goes back to what it was, the new one shows it
    page.evaluate("window.__touched.clear()")
    push("protection_progress", {"key": "phishing", "text": "1 of 9 KB", "pct": 11, "rev": live_rev})
    wait_for(page, "document.querySelector('.pl-row[data-key=\"phishing\"] .pl-stat span').textContent.startsWith('downloading')")
    assert status_of(page, "adult") == "waiting for the service"
    assert sorted(page.evaluate("[...window.__touched]")) == ["adult", "phishing"]
    # done: the row goes back (and nothing was read again: the fingerprint is the same)
    push("protection_progress", {"key": None, "text": "", "pct": None, "rev": live_rev})
    wait_for(page, "document.querySelector('.pl-row[data-key=\"phishing\"] .pl-stat span').textContent === 'waiting for the service'")
    assert harness.rpc_count("protection_get") == calls


def test_a_new_fingerprint_reads_the_lists_again(harness, shell, page, push):
    open_tab(shell, page)
    calls = harness.rpc_count("protection_get")
    c = cfg(harness)
    c["info"]["scam"] = {"count": 555, "updated": "2026-10-11T15:41:00"}                        # the service finished a list
    protection.save_settings(harness.db, c)
    push("protection_progress", {"key": None, "text": "", "pct": None, "rev": "something-new"})
    wait_for(page, "document.querySelector('.pl-row[data-key=\"scam\"] .pl-stat b').textContent === '555'")
    assert harness.rpc_count("protection_get") == calls + 1
    assert status_of(page, "scam") == "updated just now"


def test_the_ago_text_ticks_locally_without_asking_python(harness, shell, page):
    set_cfg(harness, info={"scam": {"count": 100, "updated": "2026-10-11T15:37:00"}})          # 5 minutes before the page's "now"
    open_tab(shell, page)
    assert status_of(page, "scam") == "updated 5 min ago"
    calls = harness.rpc_count()
    page.evaluate("() => { const o = performance.now.bind(performance); performance.now = () => o() + 3 * 60 * 1000; }")
    wait_for(page, "document.querySelector('.pl-row[data-key=\"scam\"] .pl-stat span').textContent === 'updated 8 min ago'", 4000)
    assert harness.rpc_count() == calls


def test_nothing_runs_while_the_window_is_hidden(harness, shell, page, perf):
    set_cfg(harness, info={"scam": {"count": 100, "updated": "2026-10-11T15:37:00"}})
    open_tab(shell, page)
    settle(page, 400)
    perf.set_hidden(True)
    settle(page, 300)
    perf.reset_timer_calls()
    calls = harness.rpc_count()
    time.sleep(2.5)
    assert sum(perf.timer_calls().values()) == 0 and harness.rpc_count() == calls
    perf.set_hidden(False)


# ---------------------------------------------------------------- the numbers

def test_first_visit_and_layout_numbers(harness, shell, page, perf):
    shell(route="blocking")
    page.wait_for_selector(".bo-row, .bo-empty", timeout=8000, state="attached")
    ms = perf.click_until('.tabs [data-tab="protect"]', '.pl-row[data-key="scam"]')
    nodes = page.evaluate("document.querySelectorAll('.pl *').length")
    print(f"\nSite protection tab: first visit {ms:.0f} ms, {nodes} DOM nodes")
    assert ms < 600 and nodes < 600
    t = perf.click_to_frame('.tabs [data-tab="overview"]')
    t2 = perf.click_to_frame('.tabs [data-tab="protect"]')
    print(f"Site protection tab: leave {t:.0f} ms, come back {t2:.0f} ms")
    assert t2 < 100
    # it still works at the smallest window the app allows
    page.set_viewport_size({"width": 1000, "height": 650})
    settle(page, 200)
    assert page.evaluate("document.querySelector('.view').scrollWidth <= document.querySelector('.view').clientWidth + 1")
