"""R11: the SVG charts (src/ui/js/charts.js) drawn for real in Chrome: colours resolve from the theme variables in
dark and light, the hover tooltip shows, a data update touches no nodes, and a redraw is cheap. (The pure layout and the
update-patches-attributes rules are in tests/ui/js/charts.test.mjs.)"""
import pytest


@pytest.fixture
def charts_page(page, harness):
    page.goto(harness.url("/__t/charts.html"), wait_until="load")
    page.wait_for_function("window.__ready === true", timeout=10_000)
    return page


def counts(page):
    return page.evaluate("Object.fromEntries(Object.entries(charts).map(([k, c]) => [k, c.svg.querySelectorAll('*').length]))")


def fill(page, selector):
    return page.evaluate("(s) => getComputedStyle(document.querySelector(s)).fill", selector)


def test_every_chart_draws_within_the_element_budget(charts_page):
    got = counts(charts_page)
    print("\n  elements per chart:", got)
    assert all(0 < n <= 200 for n in got.values()), got
    assert charts_page.evaluate("document.querySelectorAll('.ch').length") == 6


def test_colours_come_from_the_theme_in_dark_and_light(charts_page):
    page = charts_page
    dark = {"now": fill(page, "#bars .ch-bar.now"), "over": fill(page, "#bars .ch-bar.over"), "plain": fill(page, "#bars .ch-bar:not(.now):not(.over)"),
            "h0": fill(page, "#heat .ch-h0"), "h4": fill(page, "#heat .ch-h4")}
    assert dark["now"] == "rgb(219, 81, 38)" and len(set(dark.values())) == 4, dark   # (brand twice: today and the top heat level)
    heat = [fill(page, f"#heat .ch-h{i}") for i in range(5)]
    assert len(set(heat)) == 5 and all(h.startswith(("rgb", "color(")) for h in heat), heat
    seg = page.evaluate("getComputedStyle(document.querySelector('#timeline g rect')).fill")
    assert seg == page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--cat-prod').trim()") or seg.startswith("rgb")
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    light = {"over": fill(page, "#bars .ch-bar.over"), "h0": fill(page, "#heat .ch-h0")}
    assert light["over"] != dark["over"] and light["h0"] != dark["h0"], (light, dark)
    text = page.evaluate("getComputedStyle(document.querySelector('#bars text')).fill")
    assert text == "rgb(87, 82, 77)"                                                   # --text-2 in light


def test_hovering_a_bar_shows_the_one_shared_tooltip(charts_page):
    page = charts_page
    box = page.evaluate("(() => { const r = document.querySelectorAll('#bars .ch-hit')[4].getBoundingClientRect(); return [r.x + r.width / 2, r.y + 40]; })()")
    page.mouse.move(*box)
    page.wait_for_selector(".ch-tip:not([hidden])")
    assert page.inner_text(".ch-tip").startswith("day 5")
    assert page.evaluate("document.querySelectorAll('.ch-tip').length") == 1
    page.mouse.move(2, 2)
    page.wait_for_selector(".ch-tip", state="hidden")
    donut = page.evaluate("(() => { const r = document.querySelector('#donut circle[data-tip]').getBoundingClientRect(); return [r.x + r.width - 3, r.y + r.height / 2]; })()")   # (3 o'clock: inside the first part)
    page.mouse.move(*donut)
    page.wait_for_selector(".ch-tip:not([hidden])")
    assert "Productive" in page.inner_text(".ch-tip")
    page.mouse.move(*box)
    page.mouse.move(box[0] + 1, box[1])
    cell = page.evaluate("(() => { const r = document.querySelectorAll('#heat rect[data-tip]')[30].getBoundingClientRect(); return [r.x + 2, r.y + 2]; })()")
    page.mouse.move(*cell)
    page.wait_for_function("document.querySelector('.ch-tip').textContent.includes('min active')")


def test_the_trend_dot_follows_the_pointer(charts_page):
    page = charts_page
    r = page.evaluate("(() => { const r = document.querySelectorAll('#trend .ch-hit')[10].getBoundingClientRect(); return [r.x + r.width / 2, r.y + 30]; })()")
    page.mouse.move(*r)
    page.wait_for_function("+document.querySelector('#trend .ch-dot').getAttribute('r') > 0")
    page.mouse.move(2, 2)
    page.wait_for_function("+document.querySelector('#trend .ch-dot').getAttribute('r') === 0")


def test_a_data_update_changes_no_nodes_and_is_quick(charts_page):
    page = charts_page
    out = page.evaluate("""() => {
      const svg = charts.bars.svg;
      const before = [...svg.querySelectorAll('*')];
      const d = { bars: days(30).map((b, i) => ({ ...b, value: 1000 + ((i * 37) % 17) * 900 })), goal: 14400, goalLabel: 'goal 4 h' };
      const t = performance.now();
      for (let i = 0; i < 50; i++) { d.bars[0].value = 1000 + i * 90; charts.bars.update(d); }
      const ms = (performance.now() - t) / 50;
      const after = [...svg.querySelectorAll('*')];
      return { same: before.length === after.length && before.every((n, i) => n === after[i]), ms };
    }""")
    print(f"\n  bar chart update: {out['ms']:.2f} ms each (30 bars)")
    assert out["same"] and out["ms"] < 5


def test_the_chart_follows_the_container_width(charts_page):
    page = charts_page
    w0 = page.evaluate("+charts.bars.svg.getAttribute('viewBox').split(' ')[2]")
    page.evaluate("document.getElementById('bars').style.width = '420px'")
    page.wait_for_function("+charts.bars.svg.getAttribute('viewBox').split(' ')[2] === 420")
    assert w0 == 640
    # the ring never gets wider than it is tall
    assert page.evaluate("+charts.donut.svg.getAttribute('width') === +charts.donut.svg.getAttribute('height')")
