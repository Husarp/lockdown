"""0.85.1 opened to a BLACK SCREEN on Adam's PC (the new window, around midnight = bedtime). Start-up can never leave a
dark screen again:

- the asset server sends fixed content types (a .js module served as text/plain - as Windows' registry can say - is
  refused by the browser, and the page stays empty);
- no window is shown before its page has said it works (Api `page_ready`): the full-screen cover (bedtime / strict
  break) and the corner wait for their page, and a cover whose page never comes is told another way (a Windows
  notification for bedtime) instead of a black screen with no way out;
- the main window waits for its page too; a page that never says it works (or no WebView2, or a failing start) hands
  the run to the old window (agent.FALLBACK), with the reason in the logs;
- every script error at start reaches the log (the pages' early error hook);
- the new window is the default, a saved old-window choice no longer counts, the switch is gone (test_rebuild_entry.py,
  test_rebuild_notifications_settings.py).

What only Windows can prove: WebView2 really drawing once the window is shown."""
import http.client
import mimetypes
import queue
import re
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent
import webapi
from resident import corner as corner_mod
from resident import diag, notices, overlay as overlay_mod, reminder_ui, windows
from resident.assets import AssetServer
from resident.corner import Corner, CornerHost
from resident.overlay import Overlay

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "ui"
BEDTIME = [("Dismiss", "dismiss"), ("Disable alerts", "disable")]


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")
    monkeypatch.setattr(diag, "path", lambda: tmp_path / "lockdown-newui.log")


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


# ================================================================ content types

def test_module_scripts_and_styles_get_fixed_types_whatever_the_registry_says(tmp_path, monkeypatch):
    """Windows' registry can map .js to text/plain (mimetypes reads it): ES modules then refuse to run - a blank page.
    The server never asks the registry."""
    root = tmp_path / "ui"
    root.mkdir()
    files = {"a.js": "text/javascript", "b.mjs": "text/javascript", "c.css": "text/css", "d.svg": "image/svg+xml",
             "e.json": "application/json", "f.woff2": "font/woff2", "index.html": "text/html"}
    for name in files:
        (root / name).write_bytes(b"x")
    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: ("text/plain", None))
    for ext in (".js", ".mjs", ".css", ".svg", ".json", ".woff2"):
        monkeypatch.setitem(mimetypes.types_map, ext, "text/plain")
    srv = AssetServer(root)
    srv.start()
    try:
        for name, wanted in files.items():
            conn = http.client.HTTPConnection("127.0.0.1", srv.httpd.server_address[1], timeout=5)
            conn.request("GET", "/" + name)
            r = conn.getresponse()
            r.read()
            conn.close()
            assert r.status == 200 and r.getheader("Content-Type").split(";")[0] == wanted, name
            assert r.getheader("X-Content-Type-Options") == "nosniff"
    finally:
        srv.stop()
    source = (ROOT / "src" / "resident" / "assets.py").read_text(encoding="utf-8")
    assert "import mimetypes" not in source and "SimpleHTTPRequestHandler" not in source


def test_the_server_notes_the_first_requests_for_the_start_up_log(tmp_path):
    root = tmp_path / "ui"
    root.mkdir()
    (root / "index.html").write_text("x")
    lines = []
    srv = AssetServer(root, log=lines.append)
    srv.start()
    try:
        port = srv.httpd.server_address[1]
        for path, host in (("/index.html", None), ("/nope.js", None), ("/index.html", "evil.example")):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path, headers={"Host": host} if host else {})
            conn.getresponse().read()
            conn.close()
    finally:
        srv.stop()
    assert lines[:2] == ["asset server: /index.html 200", "asset server: /nope.js 404"]
    assert lines[2].startswith("asset server: /index.html (refused: host 'evil.example')") and lines[2].endswith(" 403")
    assert srv.served == 3


# ================================================================ the full-screen cover waits for its page

class Host:
    def __init__(self):
        self.calls = []

    def show(self, background):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def refresh(self):
        pass


class Pub:
    def __init__(self):
        self.sent = []

    def publish(self, event, payload=None, key=None):
        self.sent.append((event, payload))

    def set_visible(self, visible):
        pass


def gated_overlay(ready_wait=0.2, fallback=None):
    host, logs = Host(), []
    cover = Overlay(Pub(), host, answer=lambda k, a: None, background=lambda: "#191817", log=logs.append,
                    page_ready=False, fallback=fallback, ready_wait=ready_wait)
    return cover, host, logs


def test_a_bedtime_screen_due_before_the_page_is_ready_is_not_shown_until_it_is():
    cover, host, _logs = gated_overlay(ready_wait=30)
    cover.show("sleep", "Time for bed", "Go to sleep.", None, BEDTIME)
    assert cover.shown and not cover.on_screen and host.calls == []          # due, but no black window
    assert cover.publisher.sent[-1][1]["shown"] is True                       # (the page still gets it, for when it loads)
    cover.page_loaded()
    assert host.calls == ["show"] and cover.on_screen and cover.page_ok
    cover.page_loaded()                                                       # (once is enough)
    assert host.calls == ["show"]
    cover.close("sleep")
    assert host.calls == ["show", "hide"]


def test_a_page_that_never_comes_means_a_notification_not_a_black_screen():
    told = []
    cover, host, logs = gated_overlay(fallback=lambda kind, title, text: told.append((kind, title, text)))
    cover.show("sleep", "Time for bed", "Go to sleep.", None, BEDTIME)
    assert wait_until(lambda: told)
    assert told == [("sleep", "Time for bed", "Go to sleep.")] and host.calls == []
    assert any("did not say it was ready" in line for line in logs)
    time.sleep(0.3)
    assert len(told) == 1                                                     # once per screen
    cover.page_loaded()                                                       # it came after all: the screen comes up
    assert host.calls == ["show"]


def test_no_fallback_when_the_page_comes_in_time_or_the_screen_went():
    told = []
    cover, host, _logs = gated_overlay(fallback=lambda *a: told.append(a))
    cover.show("sleep", "Time for bed", "x", None, BEDTIME)
    cover.page_loaded()
    cover2, host2, _ = gated_overlay(fallback=lambda *a: told.append(a))
    cover2.show("sleep", "Time for bed", "x", None, BEDTIME)
    cover2.close("sleep")
    time.sleep(0.4)
    assert told == [] and host.calls == ["show"] and host2.calls == []


def test_the_agent_wires_the_cover_gated_and_bedtime_falls_back_to_a_windows_notification(tmp_path):
    from db import Database
    db = Database(tmp_path / "w.db")
    sent = []
    win = windows.Windows(overlay=SimpleNamespace(evaluate_js=lambda s: None, show=lambda: None, hide=lambda: None))
    ui = SimpleNamespace(worker=SimpleNamespace(db=db, answer=lambda k, a: None, poke=lambda: None),
                         overlay_handler=None, toaster=SimpleNamespace(send=lambda text, title=None: sent.append((title, text))))
    hub = SimpleNamespace(snapshot=None, clock=SimpleNamespace(now_with=lambda off: None))
    ctx = SimpleNamespace()
    publisher = overlay_mod.attach(win, hub, ctx, ui)
    try:
        cover = ctx.overlay
        assert not cover.page_ok
        cover._fallback("sleep", "Time for bed", "Go to sleep.")
        cover._fallback("break", "Break time", "Look away.")
        assert sent == [("Time for bed", "Go to sleep.")]                     # (a break keeps the windows down anyway)
        windows.page_ready(win, "overlay")
        assert cover.page_ok
    finally:
        publisher.stop()


def test_a_challenge_never_goes_to_a_cover_that_is_not_on_screen():
    cover, _host, _ = gated_overlay(ready_wait=30)
    cover.show("sleep", "Time for bed", "x", None, BEDTIME)
    shown = []
    win = windows.Windows(main=SimpleNamespace(show=lambda: shown.append(1), restore=lambda: None), publisher=Pub(),
                          overlay_ctl=cover, overlay_publisher=Pub())
    windows.page_ready(win, "main")
    windows.open_challenge(win, {"needs_challenge": True, "ticket": "T"})
    assert win.overlay_publisher.sent == [] and win.publisher.sent == [("challenge_open", {"needs_challenge": True,
                                                                                          "ticket": "T"})]
    assert shown == [1]


def test_the_cover_page_says_ready_only_after_it_drew_what_is_up():
    js = (UI / "js" / "overlay.js").read_text(encoding="utf-8")
    boot = js[js.index('ld.call("overlay_ready")'):]
    assert boot.index("overlay.render(state)") < boot.index('ld.call("page_ready", "overlay")')


# ================================================================ the corner waits for its page

def test_the_corner_window_waits_for_its_page_and_cards_wait_with_it():
    host = SimpleNamespace(calls=[])
    host.show = lambda: host.calls.append("show")
    host.hide = lambda: host.calls.append("hide")
    corner = Corner(Pub(), host, page_ready=False)
    corner.popup("custom:water", "Water", "Drink some", [("Done", "done")])
    assert host.calls == [] and not corner.page_ok
    corner.page_loaded()
    assert host.calls == ["show"] and corner.page_ok


def test_while_the_corner_page_is_not_ready_popups_and_notices_go_to_windows_notifications():
    corner = Corner(Pub(), None, page_ready=False)
    sent = []
    toaster = SimpleNamespace(send=lambda text, buttons=(), **kw: sent.append((kw.get("title"), text)) or "toast",
                              works=lambda: True, remove=lambda t: None)
    hub = SimpleNamespace(snapshot=None, notify=None, win=SimpleNamespace(is_fullscreen=lambda: False))
    ui = reminder_ui.CornerReminderUI(hub, corner, toaster)
    ui.popup("custom:water", "Water", "Drink some", [("Done", "done")])
    assert corner.cards() == [] and sent == [("Water", "Drink some")] and "custom:water" in ui.held_notices
    sink = notices.Notices(hub, corner, toaster, read_format=lambda: "inapp")
    sink.show("Blocked reddit.com")
    assert corner.cards() == [] and sent[-1] == (None, "Blocked reddit.com")
    corner.page_loaded()
    sink.show("Blocked x.com")
    assert [c["text"] for c in corner.cards()] == ["Blocked x.com"]            # (from now on: the card, as set)
    ui.popup("custom:tea", "Tea", "Make tea", [("Done", "done")])
    assert any(c["key"] == "custom:tea" for c in corner.cards())


def test_the_corner_is_shown_through_showwindow_so_webview2_draws(monkeypatch):
    """SetWindowPos(SWP_SHOWWINDOW) alone shows the frame without telling WinForms (no WM_SHOWWINDOW), and WebView2
    draws nothing while its form counts as hidden: an empty dark box. ShowWindow(SW_SHOWNOACTIVATE) first."""
    import ctypes
    calls = []

    class User32:
        def __init__(self, visible):
            self.visible = visible

        def __getattr__(self, name):
            def fn(*args):
                calls.append(name)
                return {"IsWindowVisible": self.visible, "GetWindowLongW": 0}.get(name, 1)
            return fn

    monkeypatch.setattr(CornerHost, "_hwnd", staticmethod(lambda: 42))
    host = CornerHost(window=None, work_area=lambda: (0, 0, 1920, 1040))
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: User32(visible=False), raising=False)
    host.show()
    assert calls.index("ShowWindow") < calls.index("SetWindowPos")
    calls.clear()
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: User32(visible=True), raising=False)
    host.place(200, 1.0)                                       # (already up: only moved / resized)
    assert "ShowWindow" not in calls and "SetWindowPos" in calls
    assert corner_mod.SW_SHOWNOACTIVATE == 4


# ================================================================ the main window waits for its page

class MainWin:
    def __init__(self):
        self.calls = []

    def show(self):
        self.calls.append("show")

    def restore(self):
        self.calls.append("restore")


def test_the_main_window_is_shown_only_once_its_page_says_it_works():
    win = windows.Windows(main=MainWin(), publisher=SimpleNamespace(set_visible=lambda v: None))
    windows.show_main(win)
    assert win.main.calls == [] and win.show_wanted
    assert windows.page_ready(win, "main") is True
    assert win.main.calls == ["show", "restore"] and not win.show_wanted
    assert windows.page_ready(win, "main") is False and windows.page_ready(win, "nope") is False
    windows.show_main(win)                                     # (ready: at once)
    assert win.main.calls == ["show", "restore", "show", "restore"]


def test_page_ready_hooks_run_once_and_late_hooks_at_once():
    win = windows.Windows()
    seen = []
    windows.on_page_ready(win, "corner", lambda: seen.append("early"))
    windows.page_ready(win, "corner")
    windows.on_page_ready(win, "corner", lambda: seen.append("late"))
    windows.page_ready(win, "corner")
    assert seen == ["early", "late"]


def test_a_visible_start_whose_page_never_comes_times_out_with_the_reason():
    win = windows.Windows(main=MainWin())
    reasons = []
    windows.watch_main(win, True, reasons.append, describe=lambda: "files served 0", visible_sec=0.3, hidden_sec=30,
                       step=0.02)
    assert wait_until(lambda: reasons, 3)
    assert re.fullmatch(r"the main window's page did not say it was ready within \d+ s \(files served 0\)", reasons[0])
    time.sleep(0.1)
    assert len(reasons) == 1


def test_a_page_ready_in_time_never_times_out():
    win = windows.Windows(main=MainWin())
    reasons = []
    windows.watch_main(win, True, reasons.append, visible_sec=0.4, step=0.02)
    windows.page_ready(win, "main")
    time.sleep(0.6)
    assert reasons == []


def test_a_tray_start_waits_longer_but_a_show_request_starts_the_short_clock():
    win = windows.Windows(main=MainWin(), publisher=SimpleNamespace(set_visible=lambda v: None))
    reasons = []
    windows.watch_main(win, False, reasons.append, visible_sec=0.3, hidden_sec=30, step=0.02)
    time.sleep(0.5)
    assert reasons == []                                      # (hidden: 30 s)
    windows.show_main(win)                                    # tray Open: from now on the short clock
    assert wait_until(lambda: reasons, 3)


def test_quitting_stops_the_watch():
    win = windows.Windows(main=MainWin())
    reasons = []
    windows.watch_main(win, True, reasons.append, visible_sec=0.3, step=0.02)
    win.quitting = True
    time.sleep(0.5)
    assert reasons == []


def test_the_main_page_says_ready_before_it_waits_for_data():
    js = (UI / "js" / "shell.js").read_text(encoding="utf-8")
    boot = js[js.index("async function boot()"):]
    assert boot.index("router.start()") < boot.index('ld.call("page_ready", "main")') < boot.index('ld.call("get_status")')


def test_the_page_ready_api_names_only_the_three_pages(tmp_path):
    ctx = webapi.make_context(tmp_path / "t.db")
    api = webapi.build_api(ctx)
    assert api.page_ready("main")["ok"]                       # (no agent here: nothing to tell)
    seen = []
    ctx.on_page_ready = seen.append
    for name in ("main", "overlay", "corner"):
        assert api.page_ready(name)["ok"]
    for bad in ("Main", "", None, 1, "../main", ["main"]):
        assert not api.page_ready(bad)["ok"]
    assert seen == ["main", "overlay", "corner"]


# ================================================================ the agent hands a broken start to the old window

class FakeWebview:
    """Stands in for pywebview: windows that record what is done to them; start() runs the agent's ready() and returns
    when every window is destroyed (the GUI loop ending) - or after `limit` seconds."""

    def __init__(self, renderer="edgechromium", page_ready_after=None, limit=15):
        self.renderer_next, self.page_ready_after, self.limit = renderer, page_ready_after, limit
        self.module = types.ModuleType("webview")
        self.module.create_window = self.create_window
        self.module.start = self.start
        self.module.renderer = None
        self.created = []
        self.api = None

    def create_window(self, title, url=None, **kw):
        class HL(list):
            def __iadd__(self, fn):
                self.append(fn)
                return self
        w = SimpleNamespace(title=title, url=url, kw=kw, calls=[], destroyed=threading.Event(),
                            events=SimpleNamespace(closing=HL(), minimized=HL(), restored=HL(),
                                                   shown=threading.Event(), loaded=threading.Event()))
        w.events.shown.set()
        w.show = lambda: w.calls.append("show")
        w.hide = lambda: w.calls.append("hide")
        w.restore = lambda: w.calls.append("restore")
        w.destroy = lambda: w.destroyed.set()
        w.evaluate_js = lambda script: None
        w.move = lambda x, y: None
        w.resize = lambda w_, h_: None
        self.created.append(w)
        if self.api is None:
            self.api = kw.get("js_api")
        return w

    def start(self, func=None, **kw):
        self.module.renderer = self.renderer_next
        threading.Thread(target=func, daemon=True).start()
        if getattr(self, "start_hook", None) is not None:
            time.sleep(0.05)
            self.start_hook()
        if self.page_ready_after is not None:
            time.sleep(self.page_ready_after)
            self.api.page_ready("main")                       # what the page's shell.js does
        deadline = time.monotonic() + self.limit
        while time.monotonic() < deadline and not all(w.destroyed.is_set() for w in self.created[:3]):
            time.sleep(0.01)


@pytest.fixture
def agent_env(tmp_path, monkeypatch):
    """The real agent._run on a temporary data folder, with a fake pywebview, tray and background jobs."""
    import paths
    from db import Database
    from gui import single_instance
    from resident import catalog, workers
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "config.db")
    Database(tmp_path / "config.db").close()
    monkeypatch.setattr(single_instance, "acquire", lambda events: True)
    monkeypatch.setattr(workers.Workers, "start", lambda self: None)
    monkeypatch.setattr(catalog.AppCatalog, "start", lambda self: None)
    monkeypatch.setattr(webapi.screentime, "start_warmup", lambda ctx: None)
    monkeypatch.setattr(webapi.protection, "start_warmup", lambda ctx: None)
    trays = []

    class Tray:
        def __init__(self, **kw):
            self.stopped = False
            trays.append(self)

        def notify(self, *a, **k):
            pass

        def start(self):
            pass

        def stop(self):
            self.stopped = True
    import gui.tray
    monkeypatch.setattr(gui.tray, "Tray", Tray)
    handed = []
    monkeypatch.setattr(agent, "hand_over", lambda visible: handed.append(visible) or True)

    def run(fake, argv=("Lockdown.exe",), ready_sec=1.0):
        monkeypatch.setitem(sys.modules, "webview", fake.module)
        monkeypatch.setattr(windows, "MAIN_READY_SEC", ready_sec)
        return agent.main(list(argv))
    return SimpleNamespace(run=run, handed=handed, trays=trays, tmp=tmp_path)


def newui_log(env):
    return (env.tmp / "lockdown-newui.log").read_text(encoding="utf-8")


def test_without_webview2_the_old_window_takes_over_at_once(agent_env):
    fake = FakeWebview(renderer="mshtml")
    start = time.monotonic()
    assert agent_env.run(fake) == agent.FALLBACK
    assert time.monotonic() - start < 5 and agent_env.handed == [True]
    assert all(w.calls == [] for w in fake.created)                       # nothing was ever shown
    log = (agent_env.tmp / "lockdown.log").read_text(encoding="utf-8")
    assert "WebView2 was not found" in log and "'mshtml'" in log


def test_a_main_page_that_never_says_ready_hands_over_after_the_wait(agent_env):
    fake = FakeWebview()
    assert agent_env.run(fake, ready_sec=0.5) == agent.FALLBACK
    assert agent_env.handed == [True] and agent_env.trays and agent_env.trays[0].stopped
    assert all(w.calls == [] for w in fake.created)                       # no dark window was left on screen
    assert "did not say it was ready" in (agent_env.tmp / "lockdown.log").read_text(encoding="utf-8")
    assert "files served" in newui_log(agent_env) and "start " in newui_log(agent_env)


def test_a_tray_start_hands_over_to_the_old_window_in_the_tray(agent_env, monkeypatch):
    monkeypatch.setattr(windows, "MAIN_READY_HIDDEN_SEC", 0.5)
    fake = FakeWebview()
    assert agent_env.run(fake, argv=("Lockdown.exe", "--tray")) == agent.FALLBACK
    assert agent_env.handed == [False]


def test_a_page_that_says_ready_is_shown_and_nothing_is_handed_over(agent_env):
    fake = FakeWebview(page_ready_after=0.3, limit=1.5)
    assert agent_env.run(fake, ready_sec=1.0) == 0
    main = fake.created[0]
    assert main.title == "Lockdown" and main.calls[:2] == ["show", "restore"]
    assert agent_env.handed == []
    assert "main page ready after" in newui_log(agent_env)


def test_a_failing_start_hands_over_too(agent_env, monkeypatch):
    import gui.tray

    def broken(**kw):
        raise OSError("no tray here")
    monkeypatch.setattr(gui.tray, "Tray", broken)
    assert agent_env.run(FakeWebview()) == agent.FALLBACK
    assert "the agent failed to start" in (agent_env.tmp / "lockdown.log").read_text(encoding="utf-8")


def test_hand_over_starts_the_old_window_once_after_letting_go_of_the_port(monkeypatch):
    import subprocess
    from gui import single_instance
    monkeypatch.setattr(agent, "_handed_over", [])
    released, started = [], []
    monkeypatch.setattr(single_instance, "release", lambda: released.append(1))
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    assert agent.hand_over(False) is True
    assert agent.hand_over(True) is False                                 # once per process
    assert released == [1] and len(started) == 1
    assert "--legacy" in started[0] and "--tray" in started[0] and started[0].startswith("ping -n 3 127.0.0.1")
    monkeypatch.setattr(agent, "_handed_over", [])
    started.clear()
    agent.hand_over(True)
    assert "--legacy" in started[0] and "--tray" not in started[0]


def test_release_lets_another_instance_take_the_port(monkeypatch):
    import socket
    from gui import single_instance
    monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR, raising=False)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(single_instance, "PORT", port)
    assert single_instance.acquire(queue.Queue()) is True
    assert single_instance.running()
    single_instance.release()
    assert wait_until(lambda: not single_instance.running(), 3)
    single_instance.release()                                             # (twice: nothing happens)


# ================================================================ every start-up error reaches the log

PAGES = ("index.html", "overlay.html", "corner.html", "challenge.html")


@pytest.mark.parametrize("name", PAGES)
def test_every_page_has_the_early_error_hook_before_its_modules(name):
    html = (UI / name).read_text(encoding="utf-8")
    hook = html.index("w.__ldErrorHook=1")
    assert hook < html.index('<script type="module"')
    for needed in ('addEventListener("error"', ",true)", '"unhandledrejection"', '"pywebviewready"',
                   "log_client_error"):
        assert needed in html[hook - 200:hook + 1200], needed


def test_the_hook_is_the_same_on_every_page():
    hooks = {re.search(r"<script>\(function\(w\)\{.*?\}\)\(window\)</script>", (UI / n).read_text(encoding="utf-8"),
                       re.S).group(0) for n in PAGES}
    assert len(hooks) == 1


def test_the_bridge_leaves_reporting_to_the_early_hook():
    js = (UI / "js" / "bridge.js").read_text(encoding="utf-8")
    assert "!root.__ldErrorHook" in js


# ================================================================ the uninstaller's challenge window

@pytest.fixture
def challenge_env(tmp_path, monkeypatch):
    import random
    import antibypass as ab
    from resident import challenge_cli
    from webapi.guard import GuardService
    ctx = webapi.make_context(tmp_path / "t.db")
    ctx.guard = GuardService(lambda: ctx.db, rng=random.Random(3))
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})        # (a challenge is needed)
    monkeypatch.setattr(webapi, "make_context", lambda: ctx)
    legacy = []
    monkeypatch.setattr(challenge_cli, "legacy_challenge", lambda text: legacy.append(text) or 0)
    monkeypatch.setattr(windows, "storage_dir", lambda: tmp_path)
    monkeypatch.setattr(challenge_cli, "READY_SEC", 0.3)

    def run(fake):
        monkeypatch.setitem(sys.modules, "webview", fake.module)
        return challenge_cli.run(["Lockdown.exe", "--challenge", "Uninstall Lockdown"])
    return SimpleNamespace(run=run, legacy=legacy, ctx=ctx)


def test_a_challenge_window_without_webview2_hands_over_to_the_old_challenge(challenge_env, tmp_path):
    assert challenge_env.run(FakeWebview(renderer="mshtml", limit=5)) == 0
    assert challenge_env.legacy == ["Uninstall Lockdown"]
    assert "the old window's challenge answers instead" in (tmp_path / "lockdown.log").read_text(encoding="utf-8")


def test_a_challenge_page_that_never_says_ready_hands_over_too(challenge_env):
    fake = FakeWebview(limit=5)
    assert challenge_env.run(fake) == 0 and challenge_env.legacy == ["Uninstall Lockdown"]
    assert fake.created[0].destroyed.is_set()


def test_a_challenge_page_that_works_is_not_handed_over(challenge_env):
    fake = FakeWebview(limit=1.0)
    fake.start_hook = lambda: fake.api.page_ready("challenge")
    assert challenge_env.run(fake) == 1                                    # (closed without passing: 1, as before)
    assert challenge_env.legacy == []


def test_the_challenge_page_says_ready():
    html = (UI / "challenge.html").read_text(encoding="utf-8")
    assert 'ld.call("page_ready", "challenge")' in html
