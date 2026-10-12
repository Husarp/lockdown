"""UP11: nothing is painted outside its card and no page scrolls sideways - every page and tab of the new window, in Chrome
against the real Python Api (see harness.py), at the real window size (1100x720) and the narrower ones (900x700, 760x600,
the minimum 560x420), light and dark, with long names in the data (a group, blocklist items, a reminder, a mode, a list of
your own) and the Good Night schedule chips, plus the pop-ups: the site suggestions, the group icon picker, the Screen Time
trend legend.

What is checked per page and size
  (a) the page does not scroll sideways: the document, the page scroller (.view) and the top bar are not wider than the window;
      a list (.list-card) is never squeezed to a sliver by a short window (the page scrolls instead)
  (b) for every visible .card, no element inside it with text or a box ends further right than the card (1 px allowed) -
      except inside a scroller that is meant to scroll sideways, and pop-ups (absolute / fixed, e.g. tooltips, menus)
  (c) text that runs out of its own box (a title squeezed to nothing by the buttons beside it) and boxes lying on top of each
      other, inside a card, a page header or a dialog
  (d) a suggestion dropdown (js/suggest.js) lies inside the visible window - it opens upwards when there is no room below.
What can only be proven on Windows: the same pages in WebView2 at the user's display scale."""
import json

import pytest

SIZES = [(1100, 720), (900, 700), (760, 600), (560, 420)]
LONG = "A very long name that goes on and on and on and on and on"
LONGWORD = "Supercalifragilisticexpialidocious_Supercalifragilisticexpialidocious_Words"

# (label, route, tab-button selector or None, a selector that shows the page has its content)
VIEWS = [
    ("dashboard", "dashboard", None, ".page"),
    ("blocking/overview", "blocking", "overview", ".bo-head"),
    ("blocking/groups", "blocking", "groups", ".gp-card[data-id]"),
    ("blocking/add", "blocking", "add", ".ad-search input"),
    ("blocking/protect", "blocking", "protect", '[data-pane="protect"] .card'),
    ("blocking/calendar", "blocking", "calendar", '[data-pane="calendar"] .card'),
    ("antibypass", "antibypass", None, ".page .card"),
    ("screentime/overview", "screentime", "overview", ".page .card"),
    ("screentime/apps", "screentime", "apps", ".page .card"),
    ("screentime/websites", "screentime", "websites", ".page .card"),
    ("screentime/switches", "screentime", "switches", ".page .card"),
    ("screentime/calendar", "screentime", "calendar", ".page .card"),
    ("netlog", "netlog", None, ".page"),
    ("modes", "modes", None, ".md-row"),
    ("reminders", "reminders", None, ".rem"),
    ("notifications", "notifications", None, ".page .card"),
    ("settings", "settings", None, ".page .card"),
    ("about", "about", None, ".page .card"),
]

# in the page: the rectangles that stick out of their card, and the page-level overflow
CHECK_JS = r"""() => {
  const out = [], vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const sel = (e) => { let s = e.tagName.toLowerCase(); if (e.id) s += '#' + e.id;
    const c = (e.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 3); if (c.length) s += '.' + c.join('.');
    const t = (e.childElementCount ? '' : (e.textContent || '').trim().slice(0, 30)); return t ? s + ' "' + t + '"' : s; };
  const cards = [...document.querySelectorAll('.card, .page-h, #dlg[open]')].filter((c) => vis(c) && !c.closest('[hidden]'));
  for (const card of cards) {
    const cr = card.getBoundingClientRect();
    for (const e of card.querySelectorAll('*')) {
      if (['SCRIPT', 'STYLE', 'OPTION'].includes(e.tagName) || e.closest('svg') && e.tagName !== 'svg') continue;
      const r = e.getBoundingClientRect();
      if (!r.width || !r.height || r.right <= cr.right + 1) continue;
      let skip = false;
      for (let p = e; p && p !== card; p = p.parentElement) {
        const cs = getComputedStyle(p);
        if (cs.display === 'none' || cs.visibility === 'hidden') { skip = true; break; }
        if (p !== e && ((cs.overflowX !== 'visible') || cs.position === 'absolute' && p !== e)) {
          const pr = p.getBoundingClientRect();
          if (pr.right <= cr.right + 1) { skip = true; break; }             // clipped / scrolled inside a box that fits the card
        }
        if (p === e && (cs.position === 'absolute' || cs.position === 'fixed')) { skip = true; break; }   // a pop-up
        if (p !== e && cs.position === 'fixed') { skip = true; break; }
        if (p !== e && cs.position === 'absolute' && p.matches('.sg, .tip, [role=tooltip], .menu, .pop')) { skip = true; break; }
      }
      if (skip) continue;
      if (!(e.textContent || '').trim() && !e.childElementCount && e.tagName !== 'INPUT' && e.tagName !== 'IMG' && e.tagName !== 'svg' && !getComputedStyle(e).backgroundColor.replace(/rgba\(0, 0, 0, 0\)/, '')) continue;
      out.push({ card: sel(card), el: sel(e), over: Math.round(r.right - cr.right) });
    }
  }
  const clip = [];
  for (const card of cards) for (const e of card.querySelectorAll('*')) {
    if (!vis(e) || e.closest('svg') || e.closest('[hidden]') || !(e.textContent || '').trim()) continue;
    const cs = getComputedStyle(e);
    if ((cs.overflowX === 'hidden' || cs.overflowX === 'clip') && cs.textOverflow !== 'ellipsis' && e.scrollWidth > e.clientWidth + 1 && !e.matches('input,textarea,select'))
      clip.push({ card: sel(card), el: sel(e), by: e.scrollWidth - e.clientWidth });
  }
  // (c) text spilling out of its own box, and boxes lying on top of each other, inside a card
  const spill = [], overlap = [];
  for (const card of cards) {
    for (const e of card.querySelectorAll('*')) {
      if (!vis(e) || e.closest('svg') || e.closest('[hidden]')) continue;
      const cs = getComputedStyle(e);
      if (cs.position === 'absolute' || cs.position === 'fixed') continue;
      if (cs.overflowX === 'visible' && !e.childElementCount && (e.textContent || '').trim() && e.scrollWidth > e.clientWidth + 1 && !e.matches('input,textarea,select,option'))
        spill.push({ card: sel(card), el: sel(e), by: e.scrollWidth - e.clientWidth });
      if (cs.display.includes('flex') || cs.display.includes('grid')) {
        const kids = [...e.children].filter((k) => vis(k) && !['absolute', 'fixed'].includes(getComputedStyle(k).position));
        for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
          const a = kids[i].getBoundingClientRect(), b = kids[j].getBoundingClientRect();
          const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          if (w > 2 && h > 2) overlap.push({ card: sel(card), el: sel(kids[i]) + ' / ' + sel(kids[j]), by: Math.round(w) + 'x' + Math.round(h) });
        }
      }
    }
  }
  const dlg = document.querySelector('#dlg[open]');
  const pg = [];
  if (dlg) { const r = dlg.getBoundingClientRect();
    if (r.right > innerWidth + 1 || r.bottom > innerHeight + 1 || r.left < -1 || r.top < -1) pg.push('dialog ' + Math.round(r.right) + 'x' + Math.round(r.bottom) + ' in ' + innerWidth + 'x' + innerHeight); }
  for (const l of document.querySelectorAll('.list-card')) if (vis(l) && !l.closest('[hidden]') && l.getBoundingClientRect().height < 150)
    pg.push('list squeezed to ' + Math.round(l.getBoundingClientRect().height) + 'px: ' + sel(l));
  const de = document.scrollingElement, view = document.querySelector('.view'), bar = document.getElementById('topbar');
  const page = pg;
  if (de.scrollWidth > de.clientWidth + 1) page.push('document ' + de.scrollWidth + ' > ' + de.clientWidth);
  if (view && view.scrollWidth > view.clientWidth + 1) page.push('.view ' + view.scrollWidth + ' > ' + view.clientWidth);
  if (bar) { const b = bar.getBoundingClientRect();
    for (const k of bar.children) { if (!vis(k)) continue; const r = k.getBoundingClientRect();
      if (r.right > b.right + 1 || r.left < b.left - 1) page.push('topbar ' + sel(k) + ' ' + Math.round(r.right - b.right)); } }
  return { cards: out, page, clip, spill, overlap };
}"""


def settle(page, ms=250):
    page.evaluate("(ms) => new Promise((r) => setTimeout(r, ms))", ms)


def ok(reply):
    assert reply.get("ok"), reply
    return reply.get("data")


def set_theme(page, theme):
    page.evaluate("async (t) => (await import('/js/shell.js')).setLook({theme: t})", theme)
    page.wait_for_function("(t) => document.documentElement.dataset.theme === t", arg=theme)


def long_data(harness):
    """Long names where a card shows them: blocklist items, groups (and Good Night's schedule chips), a mode, a reminder, a
    list of your own and an allowed site."""
    db, api = harness.db, harness.api
    ids = [db.add_site(LONG, ["long-name-site.example", "another.long-name-site.example", "third.long-name-site.example"]),
           db.add_site(LONGWORD, ["x.example"]),
           db.add_item("An app with a very long program name.exe", ["a_very_long_program_name_indeed.exe"], "app", "manual", [{"rule_type": "permanent"}])]
    night = json.dumps({"mode": "block", "windows": [{"days": [0, 1, 2, 3, 6], "start": "21:00", "end": "05:00"},
                                                      {"days": [4, 5], "start": "22:00", "end": "05:00"}]})
    db.add_group("Good Night", [{"rule_type": "scheduled", "schedule": night}, {"rule_type": "time_limit", "daily_limit_min": 90}],
                 {i: {} for i in ids}, None, {"icon": "moon", "color": "violet"})
    db.add_group(LONG, [{"rule_type": "time_limit", "daily_limit_min": 30, "weekly_limit_min": 120}], {i: {} for i in ids})
    db.add_group(LONGWORD, [{"rule_type": "permanent"}], {ids[0]: {}})
    ok(api.mode_save({"id": "", "name": LONG[:40], "categories": ["distracting"], "items": ids, "groups": [], "extra": [], "mute": False,
                   "pomodoro": None, "windows": None}))
    ok(api.reminder_save_custom({"id": "", "text": LONG + " " + LONG + " " + LONGWORD, "kind": "interval", "every": "30m", "times": ["12:00"], "window": ["09:00", "17:00"], "days": [0, 1, 2, 3, 4, 5, 6],
                              "snooze": 5, "max_snooze": 3, "check": 0, "packs": [], "quotes": "", "hours": False, "per_day": 0, "guarded": False}))
    key = ok(api.manual_create(LONG))["key"]
    ok(api.manual_save_entries(key, [["a-really-quite-long-host-name.of-a-long-site.example.com", LONG], ["short.example", ""]]))
    ok(api.protection_allow("a-really-quite-long-host-name.of-a-long-site.example.com"))


def view_ready(page, route, tab, ready):
    host = f'.page-host[data-page="{route}"]:not([hidden])'
    page.wait_for_selector(host, timeout=8000)
    if tab:
        page.click(f'{host} [data-tab="{tab}"]')
    page.wait_for_selector(f"{host} {ready}", state="attached", timeout=8000)
    settle(page, 150)


def check_view(page, label, size, found):
    r = page.evaluate(CHECK_JS)
    for p in r["page"]:
        found.append(f"{label} @{size[0]}x{size[1]}: PAGE {p}")
    seen = set()
    for kind, text in (("spill", "text runs out of its own box by"), ("overlap", "boxes lie on top of each other:")):
        for c in r[kind]:
            found.append(f"{label} @{size[0]}x{size[1]}: {text} {c['by']}px - {c['el']} in {c['card']}")
    for c in r["cards"]:
        key = (c["card"], c["el"])
        if key in seen:
            continue
        seen.add(key)
        found.append(f"{label} @{size[0]}x{size[1]}: {c['el']} sticks out {c['over']}px of {c['card']}")


def walk(shell, page, harness, size, theme=None):
    found = []
    shell(size[0], size[1], route="dashboard")
    if theme:
        set_theme(page, theme)
    for label, route, tab, ready in VIEWS:
        page.click(f'#nav [data-nav="{route}"]')
        view_ready(page, route, tab, ready)
        check_view(page, label, size, found)
    return found


def wait_for(page, js, timeout=6000):
    page.wait_for_function(js, timeout=timeout)


def report(page, label, size):
    found = []
    check_view(page, label, size, found)
    return found


@pytest.fixture
def loaded(harness):
    long_data(harness)
    return harness


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_nothing_sticks_out_of_its_card_in_light(shell, page, loaded, size):
    harness = loaded
    found = walk(shell, page, harness, size, "light")
    assert not found, "\n".join(found)


@pytest.mark.parametrize("size", [(1100, 720), (560, 420)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_nothing_sticks_out_in_dark_either(shell, page, loaded, size):
    found = walk(shell, page, loaded, size, "dark")
    assert not found, "\n".join(found)


class FakeCatalog:
    def search(self, kind="all", query="", limit=60):
        return {"apps": [], "total": 0, "loading": False}


def inside(page, dd, clip):
    """Is the dropdown `dd` inside the box `clip` (and the window)?  -> (ok, text)"""
    return page.evaluate("""([dd, clip]) => {
      const d = document.querySelector(dd).getBoundingClientRect(), c = document.querySelector(clip).getBoundingClientRect();
      const top = Math.max(0, c.top), bottom = Math.min(innerHeight, c.bottom);
      return [d.top >= top - 1 && d.bottom <= bottom + 1 && d.left >= -1 && d.right <= innerWidth + 1,
              `dropdown ${Math.round(d.top)}..${Math.round(d.bottom)} in ${Math.round(top)}..${Math.round(bottom)}`]; }""", [dd, clip])


@pytest.mark.parametrize("size", [(1100, 720), (760, 600), (560, 420)], ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_site_suggestions_open_inside_the_visible_area(shell, page, harness, size):
    """Every dropdown made by js/suggest.js - Add, a mode's other site, Site protection's Allowed anyway (the lowest box of its
    page: it opens upwards), the dialog of one of your own lists - lies inside the page scroller / the dialog, not past the
    bottom of the window."""
    from blocker import protection
    harness.ctx.catalog = FakeCatalog()
    cfg = protection.settings(harness.db)
    cfg.update(manual=[{"key": "mine1", "name": "Distractions", "entries": []}], enabled=cfg["enabled"] + ["mine1"])
    protection.save_settings(harness.db, cfg)
    page.set_default_timeout(8000)
    shell(size[0], size[1], route="blocking")
    dd = ".sg:not([hidden])"
    # Blocking > Add
    page.click('[data-tab="add"]')
    page.click(".ad-search input")
    page.keyboard.type("allegro")
    page.wait_for_selector(dd)
    assert inside(page, dd, ".view")[0], inside(page, dd, ".view")[1]
    page.keyboard.press("Escape")
    # Site protection > Allowed anyway, at the bottom of its page
    page.click('.tabs [data-tab="protect"]')
    page.wait_for_selector('[data-k="a-host"]')
    page.evaluate("""() => { document.querySelector('[data-k="a-host"]').scrollIntoView({block: 'end'});
      for (const p of document.querySelectorAll('.bk-pane, .view')) p.scrollTop = p.scrollHeight; }""")
    page.click('[data-k="a-host"]')
    page.keyboard.type("o")                                      # (many matches: a full dropdown of 8 rows)
    page.wait_for_function("document.querySelectorAll('.sg:not([hidden]) .sg-row:not([hidden])').length >= 6")
    ok, text = inside(page, dd, ".view")
    assert ok, text
    # a list of your own: its dialog
    page.click('.pl-mine [data-act="mine-open"]')
    page.wait_for_selector("#dlg[open] .ml-list")
    page.click('#dlg [data-k="addr"]')
    page.keyboard.type("reddit")
    page.wait_for_selector("#dlg " + dd)
    ok, text = inside(page, "#dlg " + dd, "#dlg")
    assert ok, text
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    # a mode's "+ Other site or app"
    page.click('#nav [data-nav="modes"]')
    page.wait_for_selector(".md-row")
    page.click('.md-row[data-id="relax"] [data-act="edit"]')
    page.wait_for_selector('.md-editor:not([hidden]) [data-k="name"]')
    page.click('.md-editor [data-act="other"]')
    page.fill('.md-editor [data-k="p-text"]', "youtu")
    page.wait_for_selector(".md-editor " + dd)
    ok, text = inside(page, ".md-editor " + dd, ".view")
    assert ok, text


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_editors_and_dialogs_stay_inside_their_cards(shell, page, loaded, size):
    """Open: the group editor with its icon picker, a mode's editor, the reminder editor, the dialog of a list of your own."""
    page.set_default_timeout(8000)
    shell(size[0], size[1], route="blocking")
    found = []
    page.click('[data-tab="groups"]')
    page.click(".gp-card[data-id]")
    page.wait_for_selector('[data-k="g-editor"]:not([hidden]) [data-k="g-name"]')
    settle(page, 200)
    found += report(page, "group editor", size)
    page.click('[data-k="g-look"]')
    page.wait_for_selector("#dlg[open] .gl-pick")
    settle(page, 150)
    found += report(page, "group icon picker", size)
    page.keyboard.press("Escape")
    page.click('.tabs [data-tab="protect"]')
    page.click('.pl-mine [data-act="mine-open"]')
    page.wait_for_selector("#dlg[open] .ml-list")
    settle(page, 200)
    found += report(page, "own list dialog", size)
    page.keyboard.press("Escape")
    page.click('#nav [data-nav="modes"]')
    page.click('.md-row[data-id="relax"] [data-act="edit"]')
    page.wait_for_selector('.md-editor:not([hidden]) [data-k="name"]')
    settle(page, 200)
    found += report(page, "mode editor", size)
    page.click('#nav [data-nav="reminders"]')
    page.click('.page-host[data-page="reminders"] [data-act="new"]')
    page.wait_for_selector("#dlg[open] .rem-ed")
    settle(page, 200)
    found += report(page, "reminder editor", size)
    assert not found, "\n".join(found)
