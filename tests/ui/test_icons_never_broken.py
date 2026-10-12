"""UP18: no icon <img> ever shows the browser's broken-image glyph, and Windows' own processes get the system tile.

The page runs in Chrome against the REAL Python Api and the REAL icon route (resident/icons.py, with no thread and no
download), over synthetic data with the names that used to break: "[system process]", "System", "MsMpEng.exe", "svchost.exe",
"osu!.exe", "Msmpeng", an exe with a ":" in it and a host with a space. After the images settle, every <img> in view that has
a src must be `complete && naturalWidth > 0`; the "[system process]" and "MsMpEng.exe" rows show the gear / the shield tile.

What can only be proven on Windows: the real icons from System32 and how the tiles look in WebView2."""
from datetime import timedelta

import pytest

from harness import NOW
from resident import icons

NAMES = ["[system process]", "System", "MsMpEng.exe", "svchost.exe", "osu!.exe", "Msmpeng", "odd:name.exe"]
ODD_HOST = "odd host.example"

# every <img> that is shown (and in the window - the lazy ones below the fold load when they are scrolled to)
BROKEN_JS = """() => {
  const bad = [], seen = [];
  for (const img of document.querySelectorAll('img')) {
    if (!img.getAttribute('src')) continue;
    const r = img.getBoundingClientRect(), cs = getComputedStyle(img);
    if (!r.width || !r.height || cs.visibility === 'hidden' || cs.display === 'none' || r.bottom < 0 || r.top > innerHeight) continue;
    seen.push(img.dataset.icon || img.src);
    if (!img.complete || !img.naturalWidth) bad.push(img.dataset.icon + ' ' + img.src);
  }
  return {bad, seen};
}"""
SETTLED_JS = BROKEN_JS.replace("return {bad, seen};", "return bad.length === 0 && seen.length > 0;")


@pytest.fixture
def service(harness, tmp_path):
    svc = icons.IconService(tmp_path / "icons", start=False, download=False, old_dir=tmp_path / "old",
                            path_of=lambda exe: None, extract=lambda path: None)
    harness.assets.add_route("/icon/", svc.handle)
    return svc


@pytest.fixture(autouse=True)
def slow_ok(page):
    page.set_default_timeout(9000)


def no_broken(page, at_least=1):
    page.wait_for_timeout(300)
    try:
        page.wait_for_function(SETTLED_JS, timeout=6000)
    except Exception:
        pass
    got = page.evaluate(BROKEN_JS)
    assert got["bad"] == [], got
    assert len(got["seen"]) >= at_least, got
    return got["seen"]


def marker_of(page, img_selector):
    """Which glyph the tile shown by this <img> carries ("gear", "shield", "" = a letter tile or a picture)."""
    return page.evaluate("""async (sel) => {
        const img = document.querySelector(sel);
        if (!img) return null;
        const text = await (await fetch(img.currentSrc || img.src)).text();
        const m = /data-glyph="(\\w+)"/.exec(text);
        return m ? m[1] : "";
    }""", img_selector)


def add_data(harness):
    minute = lambda back: (NOW.replace(second=0) - timedelta(minutes=back)).strftime("%Y-%m-%d %H:%M")
    harness.db.add_network([
        {"minute": minute(i), "exe": exe, "ip": f"10.9.0.{i + 1}", "port": 443, "domain": ODD_HOST if i == 4 else f"s{i}.example.org",
         "count": 3, "windows": 0, "local": 0} for i, exe in enumerate(NAMES)])
    day = NOW.date()
    for i, exe in enumerate(NAMES):
        for m in range(40 + i):
            harness.db.add_activity(f"{day} 0{1 + m // 60}:{m % 60:02d}", exe, ODD_HOST if i == 4 else f"s{i}.example.org", 60, 60)
    for name, exe in (("Defender", "MsMpEng.exe"), ("osu", "osu!.exe"), ("Odd", "odd:name.exe"), ("Service Host", "svchost.exe")):
        harness.db.add_item(name, [exe], "app", "test", [{"rule_type": "permanent"}], None, None, None)
    harness.db.add_item("Odd site", [ODD_HOST], "site", "test", [{"rule_type": "permanent"}], None, None, None)


def test_network_log_shows_the_system_tile_and_no_broken_image(shell, page, harness, service):
    add_data(harness)
    shell(route="netlog")
    page.wait_for_selector("#nl-list > .vl-row:not([hidden])")
    seen = no_broken(page, 5)
    assert "app:[system process]" in seen and "app:msmpeng.exe" in seen
    assert marker_of(page, 'img[data-icon="app:svchost.exe"]') == "gear"
    assert marker_of(page, 'img[data-icon="app:msmpeng.exe"]') == "shield"
    assert marker_of(page, 'img[data-icon="app:svchost.exe"]') == "gear"
    assert marker_of(page, 'img[data-icon="app:osu!.exe"]') == ""
    assert marker_of(page, 'img[data-icon="app:msmpeng"]') == "shield"
    assert service.queued <= 4          # (the system names that are not files, and the refused ones, queue nothing)
    assert not (service.dir).exists()


@pytest.mark.parametrize("tab", ["apps", "websites"])
def test_screen_time_tabs_have_no_broken_image(shell, page, harness, service, tab):
    add_data(harness)
    shell(route="screentime")
    page.wait_for_selector('.tabs [data-tab="overview"]')
    page.click(f'.tabs [data-tab="{tab}"]')
    page.wait_for_selector(f'[data-pane="{tab}"] .vl-row:not([hidden])')
    no_broken(page)


def test_the_network_graphs_top_lists_have_no_broken_image(shell, page, harness, service):
    add_data(harness)
    shell(1280, 1400, route="netlog")
    page.wait_for_selector("#nl-list > .vl-row:not([hidden])")
    page.click('[data-seg="nlview"] [data-v="graph"]')
    page.wait_for_selector(".nl-graph svg path.ch-bar", state="attached")
    page.wait_for_selector(".mini-row:not([hidden]) img")
    seen = no_broken(page, 3)
    assert "app:[system process]" in seen or "app:msmpeng.exe" in seen


@pytest.mark.parametrize("route", ["blocking", "dashboard"])
def test_blocking_overview_and_dashboard_have_no_broken_image(shell, page, harness, service, route):
    add_data(harness)
    shell(1280, 1000, route=route)
    page.wait_for_selector(".bo-row" if route == "blocking" else '[data-page="dashboard"]')
    no_broken(page)
    if route == "blocking":
        assert marker_of(page, 'img[data-icon="app:msmpeng.exe"]') == "shield"
        assert marker_of(page, 'img[data-icon="app:svchost.exe"]') == "gear"


class FakeCatalog:
    def search(self, kind="all", query="", limit=60):
        apps = [{"name": n, "exe": e, "path": "", "running": False, "steam": False} for n, e in (
            ("Microsoft Defender", "MsMpEng.exe"), ("Service Host", "svchost.exe"), ("osu!", "osu!.exe"),
            ("Odd", "odd:name.exe"), ("System", "System"))]
        return {"apps": apps, "total": len(apps), "loading": False}


def test_the_add_suggestions_have_no_broken_image(shell, page, harness, service):
    harness.ctx.catalog = FakeCatalog()
    shell(1280, 1000, route="blocking")
    page.wait_for_selector(".bo-row")
    page.click('[data-tab="add"]')
    page.wait_for_selector(".ad-search input")
    page.click(".ad-search input")
    for typed, exe, glyph in (("defender", "msmpeng.exe", "shield"), ("svchost", "svchost.exe", "gear"), ("osu", "osu!.exe", "")):
        page.fill(".ad-search input", typed)
        page.wait_for_selector(f'.sg:not([hidden]) .sg-row img[data-icon="app:{exe}"]')
        no_broken(page, 1)
        assert marker_of(page, f'.sg-row img[data-icon="app:{exe}"]') == glyph, typed
