"""R01: entry switch (new web window vs the old one), theme constants, import hygiene, the local asset server."""
import http.client
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import main
import ui_theme
from resident.assets import AssetServer

SRC = Path(__file__).resolve().parents[1] / "src"


# ---------------------------------------------------------------- the switch (flipped 2026-10-12: the new window)

@pytest.mark.parametrize("argv, setting, expected", [
    (["--legacy"], "web", False),                  # --legacy (hidden: the automatic fallback) always wins
    (["--legacy", "--new-ui"], "web", False),
    (["--legacy"], None, False),
    (["--new-ui"], None, True),
    (["--new-ui"], "legacy", True),
    ([], "web", True),
    ([], "legacy", True),                           # a saved "legacy" from the removed switch no longer counts
    ([], None, True),                               # nothing saved: the new window
    ([], "", True),
    ([], "junk", True),
    (["--tray"], "legacy", True),
    (["--tray"], None, True),
    (["--watchdog"], "legacy", True),
])
def test_use_new_ui_truth_table(argv, setting, expected):
    assert main.use_new_ui(argv, setting) is expected
    assert main.use_new_ui(argv, lambda: setting) is expected


def test_the_setting_is_only_read_when_no_flag_decides():
    def boom():
        raise AssertionError("read although a flag decided")
    assert main.use_new_ui(["--new-ui"], boom) is True
    assert main.use_new_ui(["--legacy"], boom) is False


def test_a_failing_setting_read_means_the_new_window():
    def boom():
        raise sqlite3.DatabaseError("no")
    assert main.use_new_ui([], boom) is True
    assert main.use_new_ui(["--legacy"], boom) is False


@pytest.mark.parametrize("default", ["legacy", "web"])
def test_engine_of(monkeypatch, default):
    monkeypatch.setattr(ui_theme, "DEFAULT_ENGINE", default)
    assert ui_theme.engine_of("web") == "web"
    for other in ("legacy", None, "", "Web", "new", 1, True):      # a saved "legacy" is no choice any more
        assert ui_theme.engine_of(other) == default


def test_the_default_window_is_the_new_one():
    """Flipped 2026-10-12 (Adam: "we are switching to the new one"). The old window stays only as the automatic
    fallback (agent.FALLBACK) and the hidden --legacy flag, until Adam confirms the new one works on his PC."""
    assert ui_theme.DEFAULT_ENGINE == "web"
    assert main.use_new_ui([], None) and main.use_new_ui([], "legacy") and not main.use_new_ui(["--legacy"], None)


def test_legacy_flag_with_selftest_keeps_the_old_self_test():
    argv = ["Lockdown.exe", "--legacy", "--selftest", "report.txt"]
    assert main.use_new_ui(argv, "web") is False
    assert argv[argv.index("--selftest") + 1] == "report.txt"


def _db_with(tmp_path, monkeypatch, engine):
    (tmp_path / "config.db").unlink(missing_ok=True)
    import paths
    path = tmp_path / "config.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    if engine is not None:
        con.execute("INSERT INTO settings VALUES ('ui.engine', ?)", (engine,))
    con.commit()
    con.close()
    monkeypatch.setattr(paths, "DB_PATH", path)


def test_reading_the_setting_from_a_real_db(tmp_path, monkeypatch):
    for saved in ("web", "legacy", None):           # (a saved "legacy" from the old switch: the new window all the same)
        _db_with(tmp_path, monkeypatch, saved)
        assert main.use_new_ui([], main.read_engine_setting) is True
    assert main.use_new_ui(["--legacy"], main.read_engine_setting) is False


def test_missing_or_corrupt_db_means_the_new_window(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "nope" / "config.db")
    assert main.use_new_ui([], main.read_engine_setting) is True
    bad = tmp_path / "config.db"
    bad.write_bytes(b"this is not a database" * 100)
    monkeypatch.setattr(paths, "DB_PATH", bad)
    assert main.use_new_ui([], main.read_engine_setting) is True


def test_a_saved_old_window_choice_is_deleted_once(tmp_path, monkeypatch):
    """Migration: the "Try the new window" switch's ui.engine is removed at start (with the autostart, off the window's
    thread), so nothing can bring the old window back; other settings stay; no database is made where there is none."""
    _db_with(tmp_path, monkeypatch, "legacy")
    con = sqlite3.connect(tmp_path / "config.db")
    con.execute("INSERT INTO settings VALUES ('ui.theme', 'light')")
    con.commit()
    con.close()
    main.forget_engine_choice()
    con = sqlite3.connect(tmp_path / "config.db")
    assert con.execute("SELECT key, value FROM settings").fetchall() == [("ui.theme", "light")]
    con.close()
    main.forget_engine_choice()                     # (nothing left: nothing happens)
    import paths
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "none" / "config.db")
    main.forget_engine_choice()
    assert not (tmp_path / "none").exists()


def test_the_clean_up_runs_with_the_autostart_and_a_failure_is_only_logged(monkeypatch):
    import threading
    done, logged = threading.Event(), []
    monkeypatch.setattr(main, "forget_engine_choice", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    monkeypatch.setattr(main, "register_autostart", done.set)
    monkeypatch.setattr(main, "_log", logged.append)
    main.register_autostart_later()
    assert done.wait(5)                             # (the autostart still happens)
    assert any("old window choice" in line for line in logged)


def test_agent_hands_unfinished_flags_back_to_the_old_window():
    import agent
    assert agent.handles(["--tray"]) and agent.handles([])
    assert agent.handles(["--challenge", "x"]) and agent.handles(["--selftest", "r"])


# ---------------------------------------------------------------- theme constants

def test_gui_theme_still_has_the_same_constants():
    from gui import theme
    assert theme.THEME_KEY == "ui.theme" and theme.ACCENT_KEY == "ui.accent" and theme.SIZE_KEY == "ui.scale"
    assert theme.THEMES == {"Dark": "dark", "AMOLED": "amoled", "Light": "light", "System": "system"}
    assert theme.ACCENTS["Orange"] == "#DB5126" and len(theme.ACCENTS) == 8
    assert theme.ACCENTS is ui_theme.ACCENTS and theme.THEMES is ui_theme.THEMES
    assert theme.BG == ("#EFF1F4", "#101418") and theme.APP_ICON == ui_theme.APP_ICON


def test_background_per_theme(tmp_path, monkeypatch):
    for name, color in (("dark", "#101418"), ("amoled", "#000000"), ("light", "#EFF1F4")):
        monkeypatch.setattr(ui_theme, "read_setting", lambda key, n=name: n if key == ui_theme.THEME_KEY else None)
        assert ui_theme.background_color() == color
    monkeypatch.setattr(ui_theme, "read_setting", lambda key: None)
    assert ui_theme.background_color() == "#101418"
    monkeypatch.setattr(ui_theme, "read_setting", lambda key: "system" if key == ui_theme.THEME_KEY else None)
    monkeypatch.setattr(ui_theme, "system_is_light", lambda: True)
    assert ui_theme.background_color() == "#EFF1F4"


# ---------------------------------------------------------------- import hygiene + speed

def _fresh(code: str) -> str:
    done = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(SRC)!r})\n{code}"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_import_agent_stays_light():
    loaded = _fresh("import agent, resident.windows, resident.assets, ui_theme\n"
                    "bad = [m for m in ('customtkinter', 'PIL', 'uiautomation', 'gui.app', 'tkinter', 'webview', 'pystray')"
                    " if m in sys.modules]\nprint(bad)")
    assert loaded == "[]"


def test_import_agent_is_fast():
    best = min(float(_fresh("import time; t = time.perf_counter(); import agent; "
                            "print((time.perf_counter() - t) * 1000)")) for _ in range(3))
    print(f"import agent: {best:.0f} ms")
    assert best < 300


# ---------------------------------------------------------------- the asset server

@pytest.fixture
def server(tmp_path):
    root = tmp_path / "ui"
    (root / "js").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>x</title>")
    (root / "js" / "a.js").write_text("export const a = 1;")
    (root / "app.css").write_text("body{}")
    (root / "logo.svg").write_text("<svg/>")
    (root / "f.woff2").write_bytes(b"wOF2")
    (root / "secret.py").write_text("x")
    (tmp_path / "outside.txt").write_text("secret")
    srv = AssetServer(root)
    srv.start()
    yield srv
    srv.stop()


def get(srv, path, host=None, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.httpd.server_address[1], timeout=5)
    conn.request(method, path, headers={"Host": host} if host else {})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, r, body


def test_serves_index_and_assets_with_the_right_types(server):
    status, r, body = get(server, "/index.html")
    assert status == 200 and b"<title>x</title>" in body
    assert r.getheader("Content-Type").startswith("text/html") and r.getheader("Cache-Control") == "no-cache"
    assert get(server, "/")[0] == 200
    status, r, _ = get(server, "/js/a.js")
    assert status == 200 and r.getheader("Content-Type").startswith("text/javascript")
    assert r.getheader("Cache-Control") == "no-cache" and r.getheader("ETag")     # (R11: never cached for a year)
    assert get(server, "/app.css")[1].getheader("Content-Type").startswith("text/css")
    assert get(server, "/logo.svg")[1].getheader("Content-Type") == "image/svg+xml"
    assert get(server, "/f.woff2")[1].getheader("Content-Type") == "font/woff2"
    assert get(server, "/index.html", method="HEAD")[2] == b""


@pytest.mark.parametrize("path", [
    "/../outside.txt", "/%2e%2e/outside.txt", "/%2E%2E/outside.txt", "/js/../../outside.txt", "/js/%2e%2e/%2e%2e/outside.txt",
    "/..%2foutside.txt", "/..%5coutside.txt", "/js/", "/js", "//etc/passwd", "/secret.py", "/nope.js", "/%00", "/c:/windows/win.ini",
])
def test_refuses_anything_outside_or_not_a_served_file(server, path):
    assert get(server, path)[0] in (403, 404)


def test_real_source_tree_cannot_be_read_through_the_server():
    srv = AssetServer()
    srv.start()
    try:
        assert get(srv, "/index.html")[0] == 200
        for path in ("/../src/db.py", "/%2e%2e/src/db.py", "/%2e%2e/%2e%2e/src/db.py", "/../db.py"):
            assert get(srv, path)[0] in (403, 404)
    finally:
        srv.stop()


def test_only_loopback_host_names_and_only_get(server):
    assert get(server, "/index.html", host="evil.example.com")[0] == 403
    assert get(server, "/index.html", host=f"localhost:{server.httpd.server_address[1]}")[0] == 200
    conn = http.client.HTTPConnection("127.0.0.1", server.httpd.server_address[1], timeout=5)
    conn.request("POST", "/index.html", body=b"x")
    assert conn.getresponse().status == 405


def test_dynamic_routes(server):
    from resident.assets import Response
    server.add_route("/icon/", lambda rest, q: Response(rest.encode(), "image/svg+xml", cache="no-cache")
                     if rest != "missing" else None)
    status, r, body = get(server, "/icon/site/a.png")
    assert status == 200 and body == b"site/a.png"
    assert get(server, "/icon/missing")[0] == 404
    server.add_route("/boom/", lambda rest, q: 1 / 0)
    assert get(server, "/boom/x")[0] == 500
    assert get(server, "/index.html")[0] == 200   # still alive


def test_ui_placeholders_exist_in_the_source_tree():
    ui = SRC / "ui"
    for name in ("index.html", "corner.html", "overlay.html"):
        assert (ui / name).is_file()


# ---------------------------------------------------------------- R29: start duties of the agent

def test_tray_and_watchdog_starts_stay_hidden_and_a_plain_start_shows_the_window():
    import ast
    import agent
    assert set(agent.TRAY_ARGS) == {"--tray", "--watchdog"}
    tree = ast.parse((SRC / "agent.py").read_text(encoding="utf-8"))
    guards = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "show_main(win)" in ast.unparse(ast.Module(body=node.body, type_ignores=[])):
            guards.append(ast.unparse(node.test))
    # the plain "show the window when the loop is up" call sits under "none of the tray flags was given"
    assert any("TRAY_ARGS" in g and g.startswith("not any(") for g in guards), guards


class _FakeDb:
    def __init__(self, exited):
        self.exited = exited

    def get_setting(self, key, default=None):
        return "1" if self.exited and key == "agent.exited" else default


@pytest.mark.parametrize("running, exited, expected", [
    (False, False, True),
    (True, False, False),      # Lockdown is already running: the watchdog does nothing
    (False, True, False),      # quit with tray Exit: stays off until the next login
    (True, True, False),
])
def test_watchdog_starts_only_when_nothing_runs_and_exit_was_not_chosen(monkeypatch, running, exited, expected):
    import db
    monkeypatch.setattr(main.single_instance, "running", lambda: running)
    monkeypatch.setattr(db, "Database", lambda: _FakeDb(exited))
    monkeypatch.setattr(main, "_log", lambda *a, **k: None)
    assert main.watchdog_should_start() is expected


def test_watchdog_reads_the_exited_flag_from_a_real_db(tmp_path, monkeypatch):
    import antibypass
    import db
    real = db.Database(tmp_path / "w.db")
    monkeypatch.setattr(main.single_instance, "running", lambda: False)
    monkeypatch.setattr(db, "Database", lambda: real)
    monkeypatch.setattr(main, "_log", lambda *a, **k: None)
    assert main.watchdog_should_start() is True
    real.set_setting(antibypass.EXITED_KEY, "1")
    assert main.watchdog_should_start() is False


def test_a_second_launch_open_event_shows_the_main_window(monkeypatch):
    import queue
    import threading
    from resident import windows
    shown = []
    done = threading.Event()
    monkeypatch.setattr(windows, "show_main", lambda win: (shown.append(win), done.set()))
    events, win = queue.Queue(), object()
    windows.pump_open_events(events, win)
    events.put("something else")             # ignored
    events.put("open")
    assert done.wait(3)
    assert shown == [win]


# ---------------------------------------------------------------- the web window's own background

def test_web_backgrounds_equal_the_tokens_css():
    """The window is painted before the page loads: its colours must be the page's --bg (so they can't drift)."""
    from resident import overlay
    css = (SRC / "ui" / "css" / "tokens.css").read_text(encoding="utf-8")
    import re
    found = {}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        bg = re.search(r"--bg:\s*(#[0-9A-Fa-f]{6})", body)
        if bg:
            for theme in re.findall(r'data-theme="(\w+)"', selector):
                found[theme] = bg.group(1)   # (a later rule - amoled after dark - wins, like the cascade)
    assert found == overlay.BACKGROUNDS
    assert {k: v.upper() for k, v in found.items()} == {k: v.upper() for k, v in overlay.BACKGROUNDS.items()}


def test_agent_paints_the_windows_with_the_pages_background():
    text = (SRC / "agent.py").read_text(encoding="utf-8")
    assert "windows.create(base_url, overlay.saved_background()" in text and "ui_theme.background_color" not in text
