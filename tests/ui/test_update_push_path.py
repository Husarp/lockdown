"""About "Check now" and the update dialog in Chrome, with the pushes going the REAL way: UpdateFlow -> the real
Publisher -> a window whose evaluate_js the test hands to the page exactly as pywebview wraps it (eval of the escaped
script). The flusher thread is off; `deliver()` sends what the Publisher would send now.

Covers the Windows bug where the Publisher believed the window hidden while it was on screen (see
tests/test_update_window_visible.py): no push came, About stayed on "Asking GitHub…", and the update dialog was a dark
backdrop with nothing on it. Now the page heals itself on a click (window_seen), asks Python when a push is late, and
the dialog never depends on a paused animation."""
import queue
from pathlib import Path

import pytest

import updates
from resident import update_flow
from resident.publisher import Publisher
from webapi import about

REPO = "someone/lockdown"
ASSET = f"https://github.com/{REPO}/releases/download/v9.9.9/LockdownSetup-9.9.9.exe"
SIZE = 40 * 1048576


def release():
    return {"version": "9.9.9", "url": f"https://github.com/{REPO}/releases/tag/v9.9.9", "newer": True,
            "asset": ASSET, "size": SIZE}


class Window:
    """The main window as the Publisher sees it: evaluate_js keeps the script for deliver()."""

    def __init__(self):
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)


class RealPath:
    def __init__(self, harness, tmp_path, page):
        from webview.util import escape_string
        self.page, self.escape = page, escape_string
        self.window = Window()
        self.publisher = Publisher(self.window, start=False, visible=True)
        self.found, self.steps = None, queue.Queue()
        self.runs, self.quits = [], []
        folder = tmp_path / "dl"
        folder.mkdir()
        ticks = iter(range(1, 10_000))

        def download(url, dest, progress=None):     # one progress step per value the test puts in `steps`
            while True:
                done = self.steps.get(timeout=20)
                if done is None:
                    break
                progress(done, SIZE)
            Path(dest).write_bytes(b"MZ")
        self.flow = update_flow.UpdateFlow(
            lambda: harness.ctx.db, self.publisher.publish, quit_app=lambda: self.quits.append(1),
            latest=lambda: self.found, downloader=download, run_installer=lambda path: self.runs.append(path),
            fetch=lambda url: {"body": "- Faster lists"}, sleep=lambda s: None, folder=folder,
            mono=lambda: next(ticks))                # (every progress step is pushed: no 100 ms throttle in the way)
        harness.ctx.update_flow = self.flow

    def step(self, done):
        """The fake download gets `done` bytes further (and has told the flow so)."""
        import time
        self.steps.put(done)
        deadline = time.monotonic() + 5
        while self.flow.done != done and time.monotonic() < deadline:
            time.sleep(0.01)
        assert self.flow.done == done

    def deliver(self):
        """What the Publisher sends now reaches the page, wrapped the way pywebview's evaluate_js wraps it."""
        self.publisher.flush()
        scripts, self.window.scripts = self.window.scripts, []
        for script in scripts:
            error = self.page.evaluate(
                f'() => {{ try {{ eval("{self.escape(script)}"); return null; }} catch (e) {{ return String(e); }} }}')
            assert error is None, error


@pytest.fixture
def real(harness, tmp_path, page, monkeypatch):
    monkeypatch.setattr(updates, "REPO", REPO)
    monkeypatch.setattr(update_flow, "REPO", REPO)
    monkeypatch.setattr(update_flow, "current", None)
    monkeypatch.setattr(about, "_browser", lambda url: None)
    r = RealPath(harness, tmp_path, page)
    yield r
    r.steps.put(None)


def note(page):
    return page.inner_text('[data-a="note"]')


def wait_note(page, text, timeout=15_000):
    page.wait_for_function("(t) => document.querySelector('[data-a=note]').textContent.includes(t)", arg=text,
                           timeout=timeout, polling=100)


def minimised(real, page):
    """The owner minimised the window: the Publisher tells the page `visible: false` and sends nothing more."""
    real.publisher.set_visible(False)
    real.deliver()
    page.wait_for_function("document.documentElement.hasAttribute('data-hidden')", polling=100)


# ---------------------------------------------------------------- Check now

def test_check_now_answers_through_the_real_publisher_with_the_reason(real, shell, page, monkeypatch):
    def unreachable():
        monkeypatch.setattr(updates, "last_error", "URLError: <urlopen error [Errno 11001] getaddrinfo failed>")
        return None
    real.flow._latest = unreachable
    shell(route="about")
    page.wait_for_selector('[data-a="check"]')
    page.click('[data-a="check"]')
    wait_note(page, "Asking GitHub")
    real.flow.join()
    real.deliver()
    wait_note(page, "Couldn't ask GitHub just now", timeout=2_500)              # (the push, not the 3 s follow-up)
    assert "(URLError: <urlopen error [Errno 11001] getaddrinfo failed>)" in note(page)
    assert page.is_enabled('[data-a="check"]')


def test_check_now_answers_when_its_push_is_lost(real, shell, page):
    """The Publisher drops everything (hidden, and nothing heals it): about_get still brings the answer."""
    real.found = release()
    shell(route="about")
    page.wait_for_selector('[data-a="check"]')
    real.publisher.set_visible(False)              # (nothing delivered from here on)
    page.click('[data-a="check"]')
    real.flow.join()
    wait_note(page, "Lockdown 9.9.9 is out (40 MB).", timeout=8_000)
    assert page.is_visible('[data-a="install"]') and page.is_enabled('[data-a="check"]')


def test_a_click_in_a_window_thought_hidden_wakes_the_page_and_python(real, shell, page, harness):
    real.found = release()
    harness.ctx.on_window_seen = lambda: real.publisher.set_visible(True)    # (what agent.py wires)
    shell(route="about")
    page.wait_for_selector('[data-a="check"]')
    minimised(real, page)                          # ... and back maximised: the Publisher was never told
    with page.expect_response(lambda r: r.url.endswith("/rpc/window_seen")):
        page.click('[data-a="check"]')
    assert real.publisher.visible
    assert not page.evaluate("document.documentElement.hasAttribute('data-hidden')")
    real.flow.join()
    real.deliver()
    wait_note(page, "Lockdown 9.9.9 is out (40 MB).", timeout=2_500)


def test_check_now_when_a_check_is_already_out_asks_python_at_once(real, shell, page):
    import threading
    gate = threading.Event()
    real.flow._latest = lambda: (gate.wait(10), release())[1]
    shell(route="about")
    page.wait_for_selector('[data-a="check"]')
    assert real.flow.check() is True               # (an earlier check, still out)
    page.click('[data-a="check"]')                 # update_check -> started: false
    wait_note(page, "Asking GitHub")
    gate.set()
    real.flow.join()                               # its push is never delivered: the page asks for it
    wait_note(page, "Lockdown 9.9.9 is out (40 MB).", timeout=6_000)


# ---------------------------------------------------------------- the update dialog

def open_from_banner(real, shell, page, harness, hidden_first=False):
    updates.remember(harness.db, release())
    shell(route="dashboard")
    banner = page.locator('#banners [data-banner="update"]:not([hidden])')
    banner.wait_for()
    if hidden_first:
        minimised(real, page)
    with page.expect_response(lambda r: r.url.endswith("/rpc/update_download")):
        banner.locator("button", has_text="Update").click()
    page.wait_for_selector("#dlg[open]")


def bar(page, width):
    page.wait_for_function("(w) => document.querySelector('#dlg .meter i').style.width === w", arg=width, polling=100,
                           timeout=8_000)


def dialog_visible(page):
    """The dialog itself is painted (not only its backdrop): opaque, with its version and buttons."""
    page.wait_for_function("getComputedStyle(document.querySelector('#dlg')).opacity === '1'", polling=100,
                           timeout=3_000)
    assert "Lockdown 9.9.9" in page.inner_text("#dlg") and "40.0 MB" in page.inner_text("#dlg")
    assert page.locator("#dlg .dlg-f button", has_text="Later").is_enabled()


def test_banner_update_shows_the_dialog_the_bar_moves_and_the_installer_starts(real, shell, page, harness):
    harness.ctx.on_window_seen = lambda: real.publisher.set_visible(True)
    open_from_banner(real, shell, page, harness, hidden_first=True)     # (the state the dark screen came from)
    dialog_visible(page)
    real.deliver()                                                      # "downloading", 0 %
    real.step(10 * 1048576)
    real.deliver()
    bar(page, "25%")
    assert page.inner_text('#dlg [data-up="n"]') == "10.0 of 40.0 MB"
    real.step(30 * 1048576)
    real.deliver()
    bar(page, "75%")
    real.steps.put(None)
    real.flow.join()
    real.deliver()
    bar(page, "100%")
    go = page.locator("#dlg .dlg-f button", has_text="Restart & update")
    go.wait_for()
    page.wait_for_function("!document.querySelector('#dlg .dlg-f button:last-child').disabled", polling=100)
    with page.expect_response(lambda r: r.url.endswith("/rpc/update_install")):
        go.click()
    real.flow.join()
    real.deliver()
    page.wait_for_function("document.querySelector('#dlg').textContent.includes('Starting the installer')", polling=100)
    assert len(real.runs) == 1 and real.quits == [1]


def test_with_every_push_lost_the_dialog_still_shows_the_progress(real, shell, page, harness):
    open_from_banner(real, shell, page, harness)
    real.publisher.set_visible(False)               # nothing reaches the page from here on
    dialog_visible(page)
    real.step(10 * 1048576)
    bar(page, "25%")                                # (update_state, asked when no push came)
    real.step(30 * 1048576)
    bar(page, "75%")
    real.steps.put(None)
    real.flow.join()
    bar(page, "100%")
    page.wait_for_function("document.querySelector('#dlg').textContent.includes('Ready to install')", polling=100)


def test_the_dialog_is_never_an_empty_dark_screen_even_while_the_page_thinks_it_is_hidden(real, shell, page, harness):
    """No heal (no window_seen hook), the page still marked hidden: the dialog is painted anyway and Esc closes it."""
    updates.remember(harness.db, release())
    shell(route="dashboard")
    page.locator('#banners [data-banner="update"]:not([hidden])').wait_for()
    minimised(real, page)
    page.evaluate("() => import('./js/update.js').then((m) => m.openUpdateDialog())")
    page.wait_for_selector("#dlg[open]")
    assert page.evaluate("document.documentElement.hasAttribute('data-hidden')")
    dialog_visible(page)
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#dlg').open", polling=100)
