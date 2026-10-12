"""R17: the About page's Api (webapi/about.py) and the in-app update flow (resident/update_flow.py).

The page is untrusted, so the checks that matter are the refusals: no folder but data / app, no address from the
page, no older release, no download from anywhere but this repository, and an update never sets EXITED_KEY."""
import ast
import inspect
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import antibypass
import updates
import webapi
from resident import update_flow
from webapi import about, core

NOW = datetime(2026, 10, 11, 12, 0)
REPO = "someone/lockdown"
ASSET = f"https://github.com/{REPO}/releases/download/v9.9.9/LockdownSetup-9.9.9.exe"
SRC = Path(__file__).resolve().parents[1] / "src"


def release(version="9.9.9", asset=ASSET, size=40 * 1048576, newer=True):
    return {"version": version, "url": f"https://github.com/{REPO}/releases/tag/v{version}", "newer": newer,
            "asset": asset, "size": size}


@pytest.fixture(autouse=True)
def repo(monkeypatch):
    monkeypatch.setattr(updates, "REPO", REPO)
    monkeypatch.setattr(update_flow, "REPO", REPO)
    monkeypatch.setattr(update_flow, "current", None)


@pytest.fixture
def ctx(tmp_path):
    return webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


class Fakes:
    """Stands in for the network, the disk, the installer and the app quitting."""

    def __init__(self, tmp_path, found=None, fail_download=False, fail_run=False):
        self.found, self.fail_download, self.fail_run = found, fail_download, fail_run
        self.pushes, self.downloads, self.runs, self.quits, self.slept = [], [], [], [], []
        self.folder = tmp_path / "tmp"
        self.folder.mkdir(exist_ok=True)

    def latest(self):
        return self.found

    def download(self, url, dest, progress=None):
        self.downloads.append(url)
        if self.fail_download:
            raise OSError("no network")
        Path(dest).write_bytes(b"MZ")
        progress(1048576, 2097152)
        progress(2097152, 2097152)

    def run(self, path):
        self.runs.append(Path(path))
        if self.fail_run:
            raise OSError("the installer could not be started (ShellExecute 5)")

    def events(self, name):
        return [p for e, p in self.pushes if e == name]


@pytest.fixture
def fakes(tmp_path):
    return Fakes(tmp_path)


@pytest.fixture
def flow(ctx, fakes):
    f = update_flow.UpdateFlow(lambda: ctx.db, lambda event, payload=None, key=None: fakes.pushes.append((event, payload)),
                               quit_app=lambda: fakes.quits.append(1), latest=fakes.latest, downloader=fakes.download,
                               run_installer=fakes.run, sleep=fakes.slept.append, folder=fakes.folder)
    ctx.update_flow = f
    return f


def keep(ctx, found):
    updates.remember(ctx.db, found)


# ---------------------------------------------------------------- the page can't choose what is opened

@pytest.mark.parametrize("which", ["C:\\x", "C:\\Windows", "../x", "log", "", "DATA", None, 5, ["data"], {"a": 1}])
def test_open_path_refuses_everything_but_data_and_app(api, monkeypatch, which):
    opened = []
    monkeypatch.setattr(about, "_opener", opened.append)
    reply = api.open_path(which)
    assert reply["ok"] is False
    assert opened == []


def test_open_path_opens_the_two_known_folders(api, monkeypatch):
    import paths
    opened = []
    monkeypatch.setattr(about, "_opener", opened.append)
    data(api.open_path("data"))
    data(api.open_path("app"))
    assert opened == [paths.DATA_DIR, paths.APP_DIR]


def test_open_url_ignores_what_the_page_sends(api, monkeypatch):
    seen = []
    monkeypatch.setattr(about, "_browser", seen.append)
    data(api.open_url("https://evil.example/x", {"url": "file:///C:/Windows/system32/cmd.exe"}))
    data(api.open_url())
    assert seen == [f"https://github.com/{REPO}/releases", f"https://github.com/{REPO}/releases"]


def test_open_url_only_ever_opens_this_repository(api, ctx, monkeypatch):
    seen = []
    monkeypatch.setattr(about, "_browser", seen.append)
    bad = release()
    bad["url"] = "https://evil.example/Lockdown"
    keep(ctx, bad)
    data(api.open_url())
    assert seen == [f"https://github.com/{REPO}/releases"]
    keep(ctx, release())
    data(api.open_url())
    assert seen[-1] == f"https://github.com/{REPO}/releases/tag/v9.9.9"


def test_without_a_repository_there_is_nothing_to_open_or_check(api, monkeypatch):
    monkeypatch.setattr(updates, "REPO", "")
    assert api.open_url()["ok"] is False
    assert api.update_check()["ok"] is False
    got = data(api.about_get())
    assert got["repo"] is None and got["found"] is None


def test_every_method_is_classified_and_none_takes_an_address_or_path():
    mine = {n: k for n, k in core.api_methods() if n in {
        "about_get", "update_state", "update_notes", "update_check", "update_download", "update_install",
        "update_set_prefs", "update_opened", "open_path", "open_url"}}
    assert set(mine.values()) <= {"read", "ui"} and len(mine) == 10
    names = {"update_download": [], "update_install": [], "update_check": [], "open_url": ["ignored"],
             "update_set_prefs": ["auto", "notify"], "open_path": ["which"], "update_notes": ["version"]}
    for name, params in names.items():
        fn = core.registry.get(name)[0]
        got = [p for p in inspect.signature(fn).parameters if p != "ctx"]
        assert got == params, name          # (open_url's *ignored swallows whatever the page sends)


# ---------------------------------------------------------------- what a check says

def test_check_outcomes_are_mapped_to_words():
    assert update_flow.outcome(None)["kind"] == "unreachable"
    assert "Couldn't ask GitHub" in update_flow.outcome(None)["text"]
    newer = update_flow.outcome(release())
    assert newer["kind"] == "installable" and newer["text"] == "Lockdown 9.9.9 is out (40 MB)."
    page = update_flow.outcome(release(asset=None))
    assert page["kind"] == "page" and "open the GitHub page" in page["text"] and not page["installable"]
    same = update_flow.outcome(release(version="0.0.1", newer=False))
    assert same["kind"] == "newest" and same["text"] == "This is the newest version."
    assert update_flow.outcome(release(version="0.0.1", newer=False))["installable"] is False


def test_a_check_keeps_the_release_pushes_the_outcome_and_pokes_the_status(flow, fakes, ctx):
    fakes.found = release()
    poked = []
    flow.on_change = lambda: poked.append(1)
    assert flow.check() is True
    flow.join()
    (pushed,) = fakes.events("update")
    assert pushed["outcome"]["kind"] == "installable"
    assert pushed["found"] == {"version": "9.9.9", "size": 40 * 1048576, "installable": True}
    assert updates.available(ctx.db)["version"] == "9.9.9" and poked == [1]
    assert flow.outcome == pushed["outcome"]


def test_an_unreachable_check_changes_nothing_that_was_found(flow, fakes, ctx):
    keep(ctx, release())
    fakes.found = None
    flow.check()
    flow.join()
    assert fakes.events("update")[0]["outcome"]["kind"] == "unreachable"
    assert updates.available(ctx.db)["version"] == "9.9.9"


def test_two_checks_at_once_are_one(flow, fakes):
    import threading
    gate = threading.Event()
    fakes.found = release()
    flow._latest = lambda: (gate.wait(2), fakes.found)[1]
    assert flow.check() is True and flow.check() is False
    gate.set()
    flow.join()
    assert len(fakes.events("update")) == 1


def test_an_older_release_is_never_offered(flow, fakes, ctx):
    fakes.found = release(version="0.0.1", newer=False)
    flow.check()
    flow.join()
    assert fakes.events("update")[0]["found"] is None
    assert updates.available(ctx.db) is None
    flow.install()
    flow.join()
    assert flow.state == "failed" and fakes.downloads == [] and fakes.runs == []
    # even a stored record of an older version (an edited database) is refused
    ctx.db.set_setting(updates.FOUND_KEY, '{"version": "0.0.1", "asset": "%s", "size": 5}' % ASSET)
    flow.install()
    assert fakes.downloads == [] and fakes.runs == []


# ---------------------------------------------------------------- download, install, quit

def test_install_downloads_runs_the_installer_then_quits_without_the_exited_flag(flow, fakes, ctx):
    keep(ctx, release())
    flow.install()
    flow.join()
    assert fakes.downloads == [ASSET]
    assert fakes.runs == [fakes.folder / "LockdownSetup-9.9.9.exe"]
    assert fakes.quits == [1] and fakes.slept == [update_flow.QUIT_DELAY_SEC]
    assert ctx.db.get_setting(antibypass.EXITED_KEY, "") in ("", "0")         # Lockdown comes back by itself
    states = [p["state"] for p in fakes.events("download_progress")]
    assert states[0] == "downloading" and "ready" in states and states[-1] == "installing"
    assert all(p["total"] in (0, 2097152, 40 * 1048576) for p in fakes.events("download_progress"))


def test_the_dialog_downloads_first_and_installs_only_on_the_second_step(flow, fakes, ctx):
    keep(ctx, release())
    flow.download()
    flow.join()
    assert flow.state == "ready" and fakes.runs == [] and fakes.quits == []
    flow.install()
    flow.join()
    assert len(fakes.downloads) == 1 and len(fakes.runs) == 1 and fakes.quits == [1]


def test_install_twice_runs_one_installer(flow, fakes, ctx):
    keep(ctx, release())
    flow.install()
    flow.install()
    flow.join()
    assert len(fakes.downloads) == 1 and len(fakes.runs) == 1


def test_the_installer_not_starting_is_said_and_try_again_does_not_download_again(flow, fakes, ctx):
    keep(ctx, release())
    fakes.fail_run = True
    flow.install()
    flow.join()
    assert flow.state == "failed" and "installer didn't start" in flow.error and flow.status()["file_down"]
    assert fakes.quits == []
    fakes.fail_run = False
    flow.install()
    flow.join()
    assert len(fakes.downloads) == 1 and len(fakes.runs) == 2 and fakes.quits == [1]


def test_a_failed_download_removes_the_part_file_and_try_again_downloads_again(flow, fakes, ctx):
    keep(ctx, release())
    fakes.fail_download = True
    flow.download()
    flow.join()
    assert flow.state == "failed" and "download didn't finish" in flow.error and not flow.status()["file_down"]
    assert fakes.runs == [] and fakes.quits == []
    fakes.fail_download = False
    flow.download()
    flow.join()
    assert flow.state == "ready" and len(fakes.downloads) == 2


def test_only_this_repositorys_installer_is_ever_downloaded(flow, fakes, ctx):
    for asset in ("https://evil.example/LockdownSetup.exe",
                  f"https://github.com/other/repo/releases/download/v9/LockdownSetup.exe",
                  f"http://github.com/{REPO}/releases/download/v9/LockdownSetup.exe",
                  f"https://github.com/{REPO}-evil/releases/download/v9/x.exe",
                  "file:///C:/Windows/System32/cmd.exe", "C:\\x.exe"):
        keep(ctx, release(asset=asset))
        flow.state = "idle"
        flow.install()
        flow.join()
        assert fakes.downloads == [] and fakes.runs == [], asset
        assert flow.state == "failed"


def test_a_version_that_is_not_a_plain_number_never_becomes_a_file_name(flow, fakes, ctx):
    keep(ctx, release(version="9.9.9/../../evil"))
    flow.install()
    flow.join()
    assert flow.state == "failed" and fakes.downloads == []


def test_the_page_cannot_name_the_release(api, ctx, flow, fakes):
    keep(ctx, release())
    reply = api.update_install("https://evil.example/x.exe")        # (an extra argument is not even accepted)
    assert reply["ok"] is False and fakes.downloads == []


def test_progress_pushes_are_spaced_out(flow, fakes, ctx):
    keep(ctx, release())
    times = iter(range(1000))
    flow._mono = lambda: next(times) * 0.01          # 10 ms apart
    flow._download = lambda url, dest, progress=None: [progress(i, 100) for i in range(1, 101)] and Path(dest).write_bytes(b"x")
    flow.download()
    flow.join()
    progress = [p for p in fakes.events("download_progress") if p["state"] == "downloading"]
    assert 5 <= len(progress) <= 20            # 100 chunks, one push per 100 ms


# ---------------------------------------------------------------- Api: state, prefs, notes

def test_about_get_and_update_state(api, ctx, flow):
    got = data(api.about_get())
    from version import RELEASED, VERSION
    assert got["version"] == VERSION and got["released"] == RELEASED
    assert got["repo"] == f"https://github.com/{REPO}" and got["found"] is None
    assert got["auto"] is True and got["notify"] is True
    assert set(got["paths"]) == {"data", "app", "log"} and got["download"]["state"] == "idle"
    keep(ctx, release())
    assert data(api.about_get())["found"] == {"version": "9.9.9", "size": 40 * 1048576, "installable": True}
    state = data(api.update_state())
    assert state["found"]["version"] == "9.9.9" and state["current"] == VERSION
    updates.snooze(ctx.db, NOW)                       # "Remind me later": the banner waits, About still says
    assert data(api.update_state())["found"] is None
    assert data(api.about_get())["found"]["version"] == "9.9.9"


def test_the_two_switches_are_saved_and_checked(api, ctx):
    data(api.update_set_prefs(False, True))
    assert ctx.db.get_setting(updates.AUTO_KEY) == "0" and ctx.db.get_setting(updates.NOTIFY_KEY) == "1"
    assert updates.auto_on(ctx.db) is False and updates.notify_on(ctx.db) is True
    data(api.update_set_prefs(True, False))
    assert updates.auto_on(ctx.db) and not updates.notify_on(ctx.db)
    for bad in (("1", True), (True, None), (1, 0), ([True], True)):
        assert api.update_set_prefs(*bad)["ok"] is False
    got = data(api.about_get())
    assert got["auto"] is True and got["notify"] is False


def test_check_now_through_the_api_starts_a_check(api, flow, fakes):
    fakes.found = release()
    assert data(api.update_check()) == {"started": True}
    flow.join()
    assert fakes.events("update")[0]["outcome"]["kind"] == "installable"


def test_window_opened_asks_the_worker_for_a_check(api, flow):
    asked = []
    flow.on_open = lambda: asked.append(1)
    data(api.update_opened())
    assert asked == [1]


def test_release_notes_come_from_our_repository_only_and_for_the_waiting_version(flow, ctx):
    keep(ctx, release())
    urls = []

    def fetch(url):
        urls.append(url)
        return {"body": "## What's new\n- Emergency unlock **kinds**\n- Weekly [calendar](http://x)\n\nThanks!"}
    flow._fetch = fetch
    assert flow.notes("9.9.9") == ["Emergency unlock kinds", "Weekly calendar"]
    assert flow.notes("9.9.9") == ["Emergency unlock kinds", "Weekly calendar"] and len(urls) == 1
    assert urls == [updates.API.format(repo=REPO)]
    assert flow.notes("1.2.3") == []                    # not the waiting version: no request at all
    flow._fetch = lambda url: (_ for _ in ()).throw(OSError("offline"))
    flow._notes.clear()
    assert flow.notes("9.9.9") == []


def test_release_notes_are_short_plain_lines():
    body = "\n".join(f"- line {i}" for i in range(30)) + "\n- " + "x" * 500
    lines = update_flow.notes_from(body)
    assert len(lines) == update_flow.MAX_NOTES and all(len(t) <= update_flow.MAX_NOTE_CHARS for t in lines)
    assert update_flow.notes_from("Just a sentence.\n\n# Heading\nAnother one") == ["Just a sentence.", "Another one"]
    assert update_flow.notes_from("") == []


# ---------------------------------------------------------------- the agent wiring

def test_attach_wires_the_notice_the_window_open_and_the_banner(tmp_path, ctx, fakes):
    asked, shown, poked = [], [], []
    hub = SimpleNamespace(open_db=lambda: ctx.db, install_update=None,
                          status_worker=SimpleNamespace(poke=lambda: poked.append(1)))
    worker = SimpleNamespace(check_soon=lambda: asked.append(1))
    pushes = []
    flow = update_flow.attach(hub, worker, lambda e, p=None, key=None: pushes.append((e, p)), show=lambda: shown.append(1),
                              quit_app=lambda: None)
    assert update_flow.current is flow and flow.on_open is worker.check_soon
    flow.on_change()
    flow.opened()
    assert asked == [1] and poked == [1]
    flow._download, flow._run, flow._folder = fakes.download, fakes.run, fakes.folder
    keep(ctx, release())
    hub.install_update(release())                       # the notice's "Install"
    flow.join()
    assert shown == [1] and ("update", {"seq": -1, "open_dialog": True}) in pushes
    assert flow.state == "ready" and fakes.runs == []   # downloaded, waiting for "Restart & update"


def test_the_agent_calls_attach_and_quits_without_the_exited_flag():
    text = (SRC / "agent.py").read_text(encoding="utf-8")
    assert "update_flow.attach(" in text and "ctx.update_flow" in text
    start = text.index("update_flow.attach(")
    block = text[start:start + 400]
    assert "windows.quit_agent(win)" in block and "EXITED_KEY" not in block
    flow_text = (SRC / "resident" / "update_flow.py").read_text(encoding="utf-8")
    used = {n.id for n in ast.walk(ast.parse(flow_text)) if isinstance(n, ast.Name)} | \
           {n.attr for n in ast.walk(ast.parse(flow_text)) if isinstance(n, ast.Attribute)}
    assert "EXITED_KEY" not in used                       # (only the docstring mentions it)
