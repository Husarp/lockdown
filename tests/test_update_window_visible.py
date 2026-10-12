"""About "Check now" and the update dialog through the REAL objects, not a test's own relay: the UpdateFlow wired by
update_flow.attach, the main window's Publisher made by windows.attach_publisher, and the window events wired by
windows.create (a fake pywebview window records what evaluate_js would send to the page).

The bug (Windows, 0.86 / 0.87): back from the taskbar to a MAXIMISED window pywebview fires "maximized", not "restored",
so after minimise -> restore the Publisher still thought the window was hidden. It then sent the page nothing: the
`update` answer of Check now was dropped (it is not even a dirty flag), download progress became a dirty flag only, and
the page - told `visible: false` on the minimise - kept every animation paused, the update dialog's fade-in at opacity 0
(a dark backdrop with nothing on it)."""
import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

import updates
import webapi
from db import Database
from resident import update_flow, windows
from webapi import core

REPO = "someone/lockdown"
ASSET = f"https://github.com/{REPO}/releases/download/v9.9.9/LockdownSetup-9.9.9.exe"


class Handlers(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self

    def fire(self):
        for fn in list(self):
            fn()


class FakeWindow:
    """What pywebview's window is to windows.py and the Publisher."""

    def __init__(self, title):
        self.title, self.scripts, self.calls = title, [], []
        self.events = SimpleNamespace(closing=Handlers(), minimized=Handlers(), restored=Handlers(),
                                      maximized=Handlers(), shown=threading.Event(), loaded=threading.Event())

    def evaluate_js(self, script):
        self.scripts.append(script)

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def restore(self):
        self.calls.append("restore")


@pytest.fixture
def win(monkeypatch):
    fake = types.ModuleType("webview")
    fake.create_window = lambda title, url=None, **kw: FakeWindow(title)
    monkeypatch.setitem(sys.modules, "webview", fake)
    w = windows.create("http://127.0.0.1:1", "#000")
    windows.attach_publisher(w)
    yield w
    w.publisher.stop()


@pytest.fixture
def flow(win, tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "REPO", REPO)
    monkeypatch.setattr(update_flow, "REPO", REPO)
    monkeypatch.setattr(update_flow, "current", None)
    path = tmp_path / "t.db"
    Database(path).close()
    hub = SimpleNamespace(open_db=lambda: Database(path), status_worker=None)
    f = update_flow.attach(hub, SimpleNamespace(check_soon=lambda: None), win.publisher.publish)
    f._latest = lambda: {"version": "9.9.9", "url": "u", "newer": True, "asset": ASSET, "size": 40 * 1048576}
    return f


def sent(win, needle, timeout=3.0):
    """The scripts that reached the page, once one contains `needle` (or all of them after `timeout`)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(needle in s for s in win.main.scripts):
            break
        time.sleep(0.02)
    return [s for s in win.main.scripts if needle in s]


def shown(win):
    windows.page_ready(win, "main")
    windows.show_main(win)
    assert win.publisher.visible


def test_check_now_reaches_the_page_after_minimise_and_restore_of_a_maximised_window(win, flow):
    shown(win)
    win.main.events.maximized.fire()           # the owner maximises the window
    win.main.events.minimized.fire()           # ... minimises it
    assert not win.publisher.visible and sent(win, '"visible":false')
    win.main.events.maximized.fire()           # ... and clicks it in the taskbar: back maximised, no "restored"
    assert win.publisher.visible
    assert flow.check() is True
    flow.join()
    pushed = sent(win, '["update",')
    assert pushed and '"outcome":{"kind":"installable"' in pushed[-1] and "Lockdown 9.9.9 is out (40 MB)." in pushed[-1]


def test_while_hidden_a_missed_check_answer_still_makes_about_ask_again(win, flow):
    shown(win)
    win.main.events.minimized.fire()
    flow.check()
    flow.join()
    win.main.events.restored.fire()
    assert sent(win, '["dirty",{"page":"about"}]')       # (the page reloads About: about_get has the outcome)
    assert not sent(win, '["update",', timeout=0.3)


def test_window_seen_wakes_the_publisher(win, tmp_path):
    """The page's own heal: clicked while it thought the window was hidden -> Api window_seen -> the agent's hook."""
    ctx = webapi.make_context(tmp_path / "t.db")
    api = core.build_api(ctx)
    assert api.window_seen()["ok"]                       # (no agent: nothing to tell)
    ctx.on_window_seen = lambda: windows.set_visible(win, True)
    assert not win.publisher.visible
    assert api.window_seen()["ok"] and win.publisher.visible


def test_the_agent_wires_window_seen():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "src" / "agent.py").read_text(encoding="utf-8")
    assert "ctx.on_window_seen = lambda: windows.set_visible(win, True)" in text


def test_about_get_says_whether_a_check_is_out_and_how_many_finished(flow, tmp_path):
    ctx = webapi.make_context(tmp_path / "t.db")
    ctx.update_flow = flow
    api = core.build_api(ctx)
    gate = threading.Event()
    found = flow._latest
    flow._latest = lambda: (gate.wait(5), found())[1]
    assert api.update_check()["data"] == {"started": True}
    assert api.update_check()["data"] == {"started": False}             # (one out already: the page asks, never waits)
    d = api.about_get()["data"]
    assert d["checking"] is True and d["seq"] == 0
    gate.set()
    flow.join()
    d = api.about_get()["data"]
    assert d["checking"] is False and d["seq"] == 1 and d["outcome"]["kind"] == "installable"


def test_a_check_that_breaks_says_why(flow, monkeypatch):
    monkeypatch.setattr(updates, "last_error", "")

    def broken():
        raise RuntimeError("disk full")
    flow._latest = broken
    flow.check()
    flow.join()
    assert flow.outcome["kind"] == "unreachable" and "(RuntimeError: disk full)" in flow.outcome["text"]
    assert flow.checking is False and flow.seq == 1
