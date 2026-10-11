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


# ---------------------------------------------------------------- the switch

@pytest.mark.parametrize("argv, setting, expected", [
    (["--legacy"], "web", False),                  # --legacy always wins
    (["--legacy", "--new-ui"], "web", False),
    (["--new-ui"], None, True),
    (["--new-ui"], "legacy", True),
    ([], "web", True),
    ([], "legacy", False),
    ([], None, False),                              # no setting = the old window
    (["--tray"], "web", True),
    (["--tray"], None, False),
    (["--watchdog", "--new-ui"], None, True),
])
def test_use_new_ui_truth_table(argv, setting, expected):
    assert main.use_new_ui(argv, setting) is expected
    assert main.use_new_ui(argv, lambda: setting) is expected


def test_the_setting_is_only_read_when_no_flag_decides():
    def boom():
        raise AssertionError("read although a flag decided")
    assert main.use_new_ui(["--new-ui"], boom) is True
    assert main.use_new_ui(["--legacy"], boom) is False


def test_a_failing_setting_read_means_the_old_window():
    def boom():
        raise sqlite3.DatabaseError("no")
    assert main.use_new_ui([], boom) is False


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
    _db_with(tmp_path, monkeypatch, "web")
    assert main.use_new_ui([], main.read_engine_setting) is True
    _db_with(tmp_path, monkeypatch, "legacy")
    assert main.use_new_ui([], main.read_engine_setting) is False
    _db_with(tmp_path, monkeypatch, None)
    assert main.use_new_ui([], main.read_engine_setting) is False


def test_missing_or_corrupt_db_means_the_old_window(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "nope" / "config.db")
    assert main.use_new_ui([], main.read_engine_setting) is False
    bad = tmp_path / "config.db"
    bad.write_bytes(b"this is not a database" * 100)
    monkeypatch.setattr(paths, "DB_PATH", bad)
    assert main.use_new_ui([], main.read_engine_setting) is False


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
