"""R17: the About page, the update banner (on every page) and the update dialog - headless in Chrome against the real
Python Api. The network, the installer and the app quitting are fakes (see test_rebuild_about.py for the logic)."""
from pathlib import Path

import pytest

import antibypass
import updates
from resident import update_flow
from webapi import about
from harness import assert_clean

REPO = "someone/lockdown"
ASSET = f"https://github.com/{REPO}/releases/download/v9.9.9/LockdownSetup-9.9.9.exe"
SIZE = 40 * 1048576


def release(version="9.9.9", asset=ASSET, newer=True):
    return {"version": version, "url": f"https://github.com/{REPO}/releases/tag/v{version}", "newer": newer,
            "asset": asset, "size": SIZE}


class Rig:
    """A real UpdateFlow on the harness's Context with a fake network / installer; its pushes are collected here and
    relayed to the page by the test (the Publisher would do that in the agent)."""

    def __init__(self, harness, tmp_path, push):
        self.found, self.pushes, self.sent = None, [], 0
        self.runs, self.quits, self.opened, self.urls = [], [], [], []
        self.push = push
        folder = tmp_path / "dl"
        folder.mkdir()

        def download(url, dest, progress=None):
            Path(dest).write_bytes(b"MZ")
            progress(SIZE // 2, SIZE)
            progress(SIZE, SIZE)
        self.flow = update_flow.UpdateFlow(
            lambda: harness.ctx.db, lambda e, p=None, key=None: self.pushes.append((e, p)),
            quit_app=lambda: self.quits.append(1), latest=lambda: self.found, downloader=download,
            run_installer=lambda path: self.runs.append(path), fetch=lambda url: {"body": "- Faster lists\n- Weekly calendar"},
            sleep=lambda s: None, folder=folder)
        harness.ctx.update_flow = self.flow

    def relay(self):
        """Send everything the flow pushed so far to the page."""
        self.flow.join()
        for event, payload in self.pushes[self.sent:]:
            self.push(event, payload)
        self.sent = len(self.pushes)


@pytest.fixture
def rig(harness, tmp_path, push, monkeypatch):
    monkeypatch.setattr(updates, "REPO", REPO)
    monkeypatch.setattr(update_flow, "REPO", REPO)
    monkeypatch.setattr(update_flow, "current", None)
    r = Rig(harness, tmp_path, push)
    monkeypatch.setattr(about, "_opener", r.opened.append)
    monkeypatch.setattr(about, "_browser", r.urls.append)
    return r


def banner(page):
    return page.locator('#banners [data-banner="update"]:not([hidden])')


def bar_width(page, selector):
    return page.evaluate("(s) => document.querySelector(s).style.width", selector)


# ---------------------------------------------------------------- the About page

def test_about_shows_the_version_the_four_groups_and_where_things_are(rig, shell, page, harness):
    shell(route="about")
    page.wait_for_selector('[data-page="about"] [data-a="ver"]:not(:empty)')
    from version import RELEASED, VERSION
    assert page.inner_text('[data-a="ver"]') == f"Version {VERSION} · released {RELEASED}"
    assert page.locator('[data-page="about"] .card .cap').count() == 4
    assert page.locator('[data-page="about"] .changelog li').count() == 5 + 4 + 3 + 3
    import paths
    assert page.inner_text('[data-a="path-data"]') == str(paths.DATA_DIR)
    assert page.inner_text('[data-a="path-app"]') == str(paths.APP_DIR)
    assert page.inner_text('[data-a="path-log"]') == str(paths.LOG_PATH)
    assert page.is_visible('[data-a="repo-on"]') and not page.is_visible('[data-a="repo-off"]')
    assert page.is_hidden('[data-a="install"]') and page.is_hidden('[data-a="hero"]')
    assert_clean(page)


def test_without_a_repository_the_page_says_so(shell, page, monkeypatch):
    monkeypatch.setattr(updates, "REPO", "")
    shell(route="about")
    page.wait_for_selector('[data-a="repo-off"]:not([hidden])')
    assert "No repository set yet" in page.inner_text('[data-a="repo-off"]')
    assert page.is_hidden('[data-a="repo-on"]')


def test_open_buttons_ask_for_data_and_app_only(rig, shell, page):
    import paths
    shell(route="about")
    page.click('[data-a="open-data"]')
    page.click('[data-a="open-app"]')
    page.wait_for_function("true")
    assert rig.opened == [paths.DATA_DIR, paths.APP_DIR]
    assert page.locator('[data-a="open-log"]').count() == 0          # the log has no Open button
    page.click('[data-a="github"]')
    page.wait_for_function("true")
    assert rig.urls == [f"https://github.com/{REPO}/releases"]


def test_the_two_switches_are_saved(rig, shell, page, harness):
    shell(route="about")
    page.wait_for_selector('[data-pref="auto"]')
    page.locator('[data-pref="auto"]').locator("xpath=..").click()
    page.wait_for_function("true")
    assert updates.auto_on(harness.db) is False and updates.notify_on(harness.db) is True
    page.locator('[data-pref="notify"]').locator("xpath=..").click()
    page.wait_for_function("true")
    assert updates.notify_on(harness.db) is False
    page.reload()
    page.wait_for_function("document.documentElement.dataset.ready === '1'")
    page.wait_for_selector('[data-pref="auto"]')
    assert not page.locator('[data-pref="auto"]').is_checked() and not page.locator('[data-pref="notify"]').is_checked()


@pytest.mark.parametrize("found, kind, text", [
    (None, "unreachable", "Couldn't ask GitHub just now"),
    (release(), "installable", "Lockdown 9.9.9 is out (40 MB)."),
    (release(asset=None), "page", "Lockdown 9.9.9 is out - open the GitHub page to get it."),
    (release(version="0.0.1", newer=False), "newest", "This is the newest version."),
])
def test_check_now_shows_each_outcome(rig, shell, page, found, kind, text):
    rig.found = found
    shell(route="about")
    page.wait_for_selector('[data-a="check"]')
    page.click('[data-a="check"]')
    page.wait_for_function("true")
    rig.relay()
    page.wait_for_function("(t) => document.querySelector('[data-a=note]').textContent.includes(t)", arg=text)
    can = kind == "installable"
    assert page.is_visible('[data-a="install"]') is can
    assert page.is_visible('[data-a="hero"]') is can
    assert page.is_enabled('[data-a="check"]')


def test_download_and_install_moves_the_bar_and_quits_without_the_exited_flag(rig, shell, page, harness):
    rig.found = release()
    shell(route="about")
    page.click('[data-a="check"]')
    page.wait_for_function("true")
    rig.relay()
    page.wait_for_selector('[data-a="install"]:not([hidden])')
    page.click('[data-a="install"]')
    page.wait_for_function("true")
    rig.relay()
    page.wait_for_function("document.querySelector('[data-a=prog-bar]').style.width === '100%'")
    assert rig.runs and rig.quits == [1]
    assert "Starting the installer" in page.inner_text('[data-a="note"]')
    assert harness.db.get_setting(antibypass.EXITED_KEY, "") in ("", "0")


def test_progress_pushes_move_the_bar(rig, shell, page, push):
    shell(route="about")
    page.wait_for_selector('[data-a="check"]')
    push("download_progress", {"state": "downloading", "version": "9.9.9", "done": 10 * 1048576, "total": SIZE})
    page.wait_for_selector('[data-a="prog"]:not([hidden])')
    assert bar_width(page, '[data-a="prog-bar"]') == "25%"
    assert page.inner_text('[data-a="prog-n"]') == "10.0 of 40.0 MB"
    push("download_progress", {"state": "downloading", "version": "9.9.9", "done": 30 * 1048576, "total": SIZE})
    page.wait_for_function("document.querySelector('[data-a=prog-bar]').style.width === '75%'")
    assert page.is_disabled('[data-a="check"]')
    push("download_progress", {"state": "failed", "version": "9.9.9", "done": 30 * 1048576, "total": SIZE,
                               "error": "The download didn't finish (OSError).", "file_down": False})
    page.wait_for_selector('[data-a="retry"]:not([hidden])')
    assert "Try again" in page.inner_text('[data-a="note"]')


# ---------------------------------------------------------------- the banner on every page

def test_the_banner_appears_on_another_page_after_an_update_push_and_can_be_hidden(rig, shell, page, push):
    shell(route="dashboard")
    assert banner(page).count() == 0
    push("update", {"seq": 1, "found": {"version": "9.9.9", "size": SIZE, "installable": True}})
    banner(page).wait_for()
    text = banner(page).inner_text()
    assert "Lockdown 9.9.9 is available" in text
    assert banner(page).locator("button", has_text="Update").count() == 1
    assert banner(page).locator("button", has_text="What's new").count() == 1
    banner(page).locator('button[aria-label="Hide"]').click()
    assert banner(page).count() == 0
    push("update", {"seq": 2, "found": {"version": "9.9.9", "size": SIZE, "installable": True}})
    page.wait_for_function("true")
    assert banner(page).count() == 0                                         # hidden until the next start
    push("update", {"seq": 3, "found": {"version": "9.9.10", "size": SIZE, "installable": True}})
    banner(page).wait_for()                                                  # a newer one is shown again
    push("update", {"seq": 4, "found": None})
    page.wait_for_function("document.querySelector('[data-banner=update]').hidden")


def test_the_banner_follows_the_status_push_and_a_stored_release_at_start(rig, shell, page, push, harness):
    updates.remember(harness.db, release())
    shell(route="blocking")
    banner(page).wait_for()                                                  # update_state at boot
    push("status", {"update": None})
    page.wait_for_function("document.querySelector('[data-banner=update]').hidden")
    push("status", {"update": {"version": "9.9.9", "installable": True}})
    banner(page).wait_for()


def test_a_release_without_an_installer_offers_the_page(rig, shell, page, push):
    shell(route="dashboard")
    push("update", {"seq": 1, "found": {"version": "9.9.9", "size": 0, "installable": False}})
    banner(page).wait_for()
    assert banner(page).locator("button", has_text="Open the page").count() == 1
    banner(page).locator("button", has_text="Open the page").click()
    page.wait_for_function("true")
    assert rig.urls == [f"https://github.com/{REPO}/releases"]


def test_whats_new_goes_to_the_about_page(rig, shell, page, push):
    shell(route="dashboard")
    push("update", {"seq": 1, "found": {"version": "9.9.9", "size": SIZE, "installable": True}})
    banner(page).wait_for()
    banner(page).locator("button", has_text="What's new").click()
    page.wait_for_selector('[data-page="about"] [data-a="ver"]')
    assert page.evaluate("location.hash") == "#about"


# ---------------------------------------------------------------- the dialog

def open_dialog(rig, shell, page, harness, route="dashboard"):
    updates.remember(harness.db, release())
    shell(route=route)
    banner(page).wait_for()
    banner(page).locator("button", has_text="Update").click()
    page.wait_for_selector("#dlg[open]")


def test_the_dialog_downloads_then_restarts_only_when_asked(rig, shell, page, harness):
    open_dialog(rig, shell, page, harness)
    assert "Lockdown 9.9.9" in page.inner_text("#dlg")
    assert "40.0 MB" in page.inner_text("#dlg")                              # "You have X · 40.0 MB"
    page.wait_for_selector('#dlg li:has-text("Faster lists")')
    rig.relay()
    page.wait_for_function("document.querySelector('#dlg .meter i').style.width === '100%'")
    assert "Ready to install" in page.inner_text("#dlg")
    go = page.locator("#dlg .dlg-f button", has_text="Restart & update")
    assert go.is_enabled() and rig.runs == [] and rig.quits == []
    go.click()
    page.wait_for_function("true")
    rig.relay()
    assert len(rig.runs) == 1 and rig.quits == [1]
    assert "Starting the installer" in page.inner_text("#dlg")
    assert harness.db.get_setting(antibypass.EXITED_KEY, "") in ("", "0")


def test_the_dialog_progress_and_later(rig, shell, page, harness, push):
    open_dialog(rig, shell, page, harness)
    rig.flow.join()                                                          # (the fake download is done; nothing relayed yet)
    push("download_progress", {"state": "downloading", "version": "9.9.9", "done": 20 * 1048576, "total": SIZE})
    page.wait_for_function("document.querySelector('#dlg .meter i').style.width === '50%'")
    assert page.inner_text('#dlg [data-up="n"]') == "20.0 of 40.0 MB"
    assert page.locator("#dlg .dlg-f button", has_text="Restart & update").is_disabled()
    page.locator("#dlg .dlg-f button", has_text="Later").click()
    page.wait_for_function("!document.querySelector('#dlg').open")
    assert rig.runs == []


def test_a_failed_download_shows_try_again(rig, shell, page, harness, push):
    open_dialog(rig, shell, page, harness)
    push("download_progress", {"state": "failed", "version": "9.9.9", "done": 0, "total": SIZE, "file_down": False,
                               "error": "The download didn't finish (OSError - no connection, or GitHub didn't answer)."})
    go = page.locator("#dlg .dlg-f button", has_text="Try again")
    go.wait_for()
    assert go.is_enabled() and "didn't finish" in page.inner_text("#dlg")


def test_the_notice_install_button_opens_the_dialog(rig, shell, page, harness, push):
    updates.remember(harness.db, release())
    shell(route="settings")
    push("update", {"seq": -1, "open_dialog": True})
    page.wait_for_selector("#dlg[open]")
    assert "Lockdown 9.9.9" in page.inner_text("#dlg")


# ---------------------------------------------------------------- speed

def test_about_opens_fast_and_its_api_is_cheap(rig, shell, page, perf, harness, capsys):
    import time
    shell(route="dashboard")
    opened = perf.click_until('[data-nav="about"]', '[data-page="about"] [data-a="ver"]:not(:empty)')
    times = []
    for _ in range(40):
        t0 = time.perf_counter()
        harness.api.about_get()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    p95 = times[int(len(times) * 0.95)]
    with capsys.disabled():
        print(f"\n  About: first visit to filled page {opened:.0f} ms, about_get p95 {p95:.1f} ms")
    assert 0 <= opened < 400 and p95 < 50
    # the banner pushes patch in place: no timers run while a push comes in
    perf.reset_timer_calls()
    for n in range(20):
        page.evaluate("(n) => window.ld.push([['download_progress', {state: 'downloading', version: '9', done: n * 100, total: 5000}]])", n)
    assert perf.timer_calls()["interval"] == 0
