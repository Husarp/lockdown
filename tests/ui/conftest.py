"""Fixtures for the headless UI tests (see harness.py for what they give you). The whole folder is skipped when
playwright or Chrome is missing, so the plain pytest run stays green anywhere."""
from pathlib import Path

import pytest

import harness as H

HERE = Path(H.__file__).resolve().parent


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_console_errors: this UI test expects errors in the browser console")


def pytest_collection_modifyitems(config, items):
    if H.available():
        return
    skip = pytest.mark.skip(reason="UI tests need playwright and Chrome (%s)" % H.CHROME)
    for item in items:
        if Path(str(item.path)).resolve().is_relative_to(HERE):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=H.CHROME, headless=True,
                              args=["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


@pytest.fixture
def harness(tmp_path):
    h = H.UiHarness(tmp_path)
    yield h
    h.stop()


@pytest.fixture
def page(browser, harness, request):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script(harness.init_script())
    p = ctx.new_page()
    p.errors = []
    p.on("console", lambda m: p.errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    p.on("pageerror", lambda e: p.errors.append(f"pageerror: {e}"))
    p.on("requestfailed", lambda r: p.errors.append(f"requestfailed: {r.url} {r.failure}"))
    yield p
    ctx.close()
    if not request.node.get_closest_marker("allow_console_errors"):
        H.assert_clean(p)


@pytest.fixture
def shell(page, harness):
    def open_(width=None, height=None, route=None):
        return H.open_shell(page, harness, width, height, route)
    return open_


@pytest.fixture
def push(page):
    def push_(event, payload=None):
        page.evaluate("([e, p]) => window.ld.push([[e, p]])", [event, payload])
    return push_


@pytest.fixture
def perf(page):
    return H.Perf(page)
