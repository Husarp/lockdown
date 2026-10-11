"""R11: the icon routes (resident/icons.py), the asset server's cache rules, the app catalog (resident/catalog.py) and
the Api that uses it (webapi/shared.py).

Nothing here needs Windows: the exe-icon extraction, the favicon download, the list of installed apps and the Open dialog
are replaced by fakes. What only a Windows machine proves: a real exe's icon, the real app scan (Start Menu, Steam, open
windows) and the Open dialog."""
import http.client
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import search
import webapi
from db import Database
from resident import catalog as cat
from resident import icons
from resident.assets import AssetServer
from webapi import core, shared

SRC = Path(__file__).resolve().parents[1] / "src"
PNG = b"\x89PNG\r\n\x1a\nfake-icon"


def get(server, path, headers=None, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", server.httpd.server_address[1], timeout=5)
    conn.request(method, path, headers=headers or {})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, r, body


# ================================================================ asset server: no stale UI after an update

@pytest.fixture
def ui_server(tmp_path):
    root = tmp_path / "ui"
    (root / "js").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>x</title>")
    (root / "js" / "a.js").write_text("export const a = 1;")
    (root / "app.css").write_text("body{}")
    server = AssetServer(root)
    server.start()
    yield server, root
    server.stop()


def test_nothing_is_cached_for_a_year(ui_server):
    server, _root = ui_server
    for path in ("/index.html", "/js/a.js", "/app.css"):
        _status, r, _body = get(server, path)
        assert r.getheader("Cache-Control") == "no-cache" and r.getheader("ETag"), path
        assert "max-age" not in r.getheader("Cache-Control")


def test_unchanged_file_is_a_304_and_an_updated_one_is_sent_again(ui_server):
    server, root = ui_server
    _s, r, body = get(server, "/js/a.js")
    etag = r.getheader("ETag")
    status, r2, body2 = get(server, "/js/a.js", {"If-None-Match": etag})
    assert status == 304 and body2 == b"" and r2.getheader("ETag") == etag
    assert get(server, "/js/a.js", {"If-None-Match": '"other"'})[0] == 200
    assert get(server, "/js/a.js", {"If-None-Match": f'"zzz", W/{etag}'})[0] == 304
    (root / "js" / "a.js").write_text("export const a = 22222;")     # the app was updated
    status, r3, body3 = get(server, "/js/a.js", {"If-None-Match": etag})
    assert status == 200 and body3 == b"export const a = 22222;" and r3.getheader("ETag") != etag


def test_a_conditional_request_is_fast(ui_server):
    server, _root = ui_server
    etag = get(server, "/js/a.js")[1].getheader("ETag")
    conn = http.client.HTTPConnection("127.0.0.1", server.httpd.server_address[1], timeout=5)
    times = []
    for _ in range(100):
        t = time.perf_counter()
        conn.request("GET", "/js/a.js", headers={"If-None-Match": etag})
        r = conn.getresponse()
        r.read()
        times.append((time.perf_counter() - t) * 1000)
        assert r.status == 304
    times.sort()
    print(f"304 revalidation: median {times[50]:.2f} ms, p95 {times[94]:.2f} ms (keep-alive, 100 requests)")
    assert times[94] < 25


# ================================================================ icons

@pytest.fixture
def made(tmp_path):
    """An IconService with fakes and no thread (the test runs the jobs), and a way to see what was asked."""
    class Made:
        ready: list = []
        extracted: list = []
        fetched: list = []
        paths = {"chrome.exe": r"C:\Program Files\Chrome\chrome.exe"}
        now = [1000.0]
    m = Made()
    m.ready, m.extracted, m.fetched = [], [], []

    def extract(path):
        m.extracted.append(path)
        return PNG

    def fetch(host):
        m.fetched.append(host)
        return PNG if host != "offline.example" else None

    m.service = icons.IconService(tmp_path / "icons", on_ready=m.ready.append, path_of=m.paths.get, extract=extract,
                                  fetch=fetch, old_dir=tmp_path / "old", start=False, clock=lambda: m.now[0])
    m.dir = tmp_path / "icons"
    return m


def test_a_miss_is_a_letter_tile_at_once_and_one_queued_job(made):
    s = made.service
    r = s.handle("app/chrome.exe.png", {})
    assert r.status == 200 and r.content_type == "image/svg+xml" and r.cache == "no-store"
    assert b"<svg" in r.body and b">C<" in r.body
    assert s.queued == 1
    s.handle("app/chrome.exe.png", {})
    s.handle("app/chrome.exe.png", {"v": ["2"]})
    assert s.queued == 1 and made.extracted == []          # (asking again queues nothing more; nothing ran yet)


def test_the_job_fills_the_cache_tells_the_page_and_the_next_request_is_served_from_disk(made):
    s = made.service
    s.handle("app/chrome.exe.png", {})
    s.run_pending()
    assert made.ready == ["app:chrome.exe"] and made.extracted == [r"C:\Program Files\Chrome\chrome.exe"]
    assert (made.dir / "app_chrome.exe.png").read_bytes() == PNG
    r = s.handle("app/chrome.exe.png", {})
    assert r.content_type == "image/png" and r.body == PNG and r.etag
    assert s.queued == 0
    s.run_pending()
    assert made.extracted == [r"C:\Program Files\Chrome\chrome.exe"]       # not extracted again


def test_sites_get_their_favicon_and_a_failure_is_retried_only_later(made):
    s = made.service
    s.handle("site/example.com.png", {})
    s.handle("site/offline.example.png", {})
    s.run_pending()
    assert made.ready == ["site:example.com"] and sorted(made.fetched) == ["example.com", "offline.example"]
    assert s.handle("site/example.com.png", {}).content_type == "image/png"
    assert s.handle("site/offline.example.png", {}).content_type == "image/svg+xml"
    assert s.queued == 0                                                    # failed: not asked again right away
    made.now[0] += icons.RETRY_SEC + 1
    s.handle("site/offline.example.png", {})
    assert s.queued == 1


def test_an_unknown_program_is_a_letter_and_nothing_is_read_from_a_path_the_page_names(made):
    s = made.service
    s.handle("app/unknown.exe.png", {})
    s.run_pending()
    assert made.extracted == [] and made.ready == []
    assert s.handle("app/unknown.exe.png", {}).content_type == "image/svg+xml"


def test_the_old_windows_cached_icon_is_reused(made, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "app_chrome.exe.png").write_bytes(PNG + b"old")
    (old / "example.com.png").write_bytes(PNG + b"site")
    s = made.service
    s.handle("app/chrome.exe.png", {})
    s.handle("site/example.com.png", {})
    s.run_pending()
    assert made.extracted == [] and made.fetched == []
    assert s.handle("app/chrome.exe.png", {}).body == PNG + b"old"
    assert s.handle("site/example.com.png", {}).body == PNG + b"site"


def test_a_crashing_extractor_leaves_the_letter_and_does_not_stop_the_thread(made):
    def boom(path):
        raise RuntimeError("bad icon")
    made.service.extract = boom
    made.service.handle("app/chrome.exe.png", {})
    made.service.run_pending()
    assert made.ready == [] and made.service.queued == 0


@pytest.mark.parametrize("rest", [
    "app/../secret.png", "app/..%2fsecret.exe.png", "app/a/b.exe.png", "app/..\\x.exe.png", "app/c:x.exe.png",
    "app/con:.exe.png", "app/.hidden.png", "app/x..exe.png", "app/.png", "app/x.exe", "app/x.exe.svg", "app/a\x00b.exe.png",
    "app/<script>.exe.png", "app/a%b.exe.png", "app/" + "a" * 120 + ".exe.png", "app/a;b.exe.png", "app/a|b.exe.png",
    "site/../x.png", "site/a b.com.png", "site/a_b.com.png", "site/.com.png", "site/a..com.png", "site/-a.com.png",
    "site/a.com-.png", "site/" + "a" * 260 + ".com.png", "site/ex%61mple.com.png", "site/a/b.png", "site/a.com.svg",
    "letter/a/b.svg", "letter/<b>.svg", "letter/a:b.svg", "letter/" + "x" * 65 + ".svg", "letter/.svg", "letter/x.png",
    "other/x.png", "x.png", "", "app", "app/",
])
def test_odd_names_are_refused(made, rest):
    assert made.service.handle(rest, {}) is None
    assert made.service.queued == 0


def test_the_route_over_http_cannot_leave_the_cache_folder(made, tmp_path):
    (tmp_path / "secret.png").write_bytes(b"SECRET")
    (made.dir).mkdir(parents=True, exist_ok=True)
    server = AssetServer()
    server.add_icon_routes(made.service)
    server.start()
    try:
        for path in ("/icon/app/..%2f..%2fsecret.png", "/icon/app/%2e%2e/secret.png", "/icon/app/..%5csecret.exe.png",
                     "/icon/site/%2e%2e%2fsecret.png", "/icon/../secret.png", "/icon/app//etc/passwd.png",
                     "/icon/app/%00.exe.png", "/icon/other/chrome.exe.png"):
            status, _r, body = get(server, path)
            assert status in (400, 403, 404) and b"SECRET" not in body, path
        status, r, body = get(server, "/icon/app/chrome.exe.png")
        assert status == 200 and r.getheader("Content-Type") == "image/svg+xml" and r.getheader("Cache-Control") == "no-store"
        made.service.run_pending()
        status, r, body = get(server, "/icon/app/chrome.exe.png")
        assert status == 200 and body == PNG and r.getheader("Content-Type") == "image/png"
        etag = r.getheader("ETag")
        assert get(server, "/icon/app/chrome.exe.png", {"If-None-Match": etag})[0] == 304
        assert get(server, "/icon/app/chrome.exe.png?v=3")[0] == 200            # (the cache-buster the page adds)
    finally:
        server.stop()


def test_the_letter_route_makes_an_svg_and_escapes_the_text(made):
    r = made.service.handle("letter/AT&T Mobile.svg", {})
    assert r.content_type == "image/svg+xml" and b">A<" in r.body and b"<script" not in r.body
    assert made.service.handle("letter/\u0141ukasz.svg", {}).body.count(b"\xc5\x81") == 1
    same = made.service.handle("letter/Spotify.svg", {}).body
    assert same == made.service.handle("letter/Spotify.svg", {}).body            # (the colour follows the name)
    import xml.dom.minidom
    xml.dom.minidom.parseString(r.body)
    assert icons.letter_svg("<&>\"").count(b"<text") == 1 and b"&lt;" not in icons.letter_svg("<&>")


def test_the_thread_gets_the_icon_and_nothing_heavy_is_imported_on_the_request():
    code = ("import sys; sys.path.insert(0, %r)\nfrom resident import icons\n"
            "s = icons.IconService(__import__('pathlib').Path(%r), start=False)\n"
            "s.handle('app/x.exe.png', {}); s.handle('site/a.com.png', {})\n"
            "print([m for m in ('PIL', 'customtkinter', 'tkinter', 'webview') if m in sys.modules])")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = subprocess.run([sys.executable, "-c", code % (str(SRC), d)], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "[]", out.stderr


def test_the_real_thread_runs_the_job_off_the_request(tmp_path):
    done = threading.Event()
    seen = {}

    def extract(path):
        seen["thread"] = threading.current_thread().name
        time.sleep(0.05)
        return PNG

    s = icons.IconService(tmp_path / "i", on_ready=lambda key: (seen.setdefault("key", key), done.set()),
                          path_of=lambda exe: r"C:\a.exe", extract=extract, old_dir=tmp_path / "none")
    t = time.perf_counter()
    r = s.handle("app/a.exe.png", {})
    assert time.perf_counter() - t < 0.04 and r.content_type == "image/svg+xml"      # (not waiting for the 50 ms job)
    assert done.wait(5) and seen["key"] == "app:a.exe" and seen["thread"] == "lockdown-icons"


# ================================================================ catalog

def make_apps(n=1500, seed=1):
    rng = random.Random(seed)
    words = ["adobe", "discord", "disk", "cleanup", "chrome", "firefox", "steam", "game", "studio", "visual", "code", "tool",
             "manager", "office", "word", "excel", "player", "media", "update", "helper", "nvidia", "control", "panel"]
    apps = []
    for i in range(n):
        name = " ".join(rng.choice(words).title() for _ in range(rng.randint(1, 3)))
        apps.append({"name": name, "exe": f"{rng.choice(words)}{i}.exe", "path": f"C:\\Apps\\{i}.exe",
                     "running": i % 50 == 0, "steam": i % 17 == 0})
    return apps


class Scanner:
    def __init__(self, apps):
        self.apps, self.calls = apps, 0

    def __call__(self):
        self.calls += 1
        return self.apps


def loaded_catalog(tmp_path, apps, running=None, **kw):
    c = cat.AppCatalog(tmp_path / "cat.json", list_apps=Scanner(apps), running_now=lambda: running or {}, **kw)
    c.load_now()
    return c


def test_the_cache_file_is_reused_within_the_ttl_and_refreshed_after(tmp_path):
    clock = [1_000_000.0]
    scan = Scanner(make_apps(5))
    first = cat.AppCatalog(tmp_path / "c.json", list_apps=scan, clock=lambda: clock[0])
    first.load_now()
    assert scan.calls == 1 and first.search()["total"] == 5
    clock[0] += 3600
    second = cat.AppCatalog(tmp_path / "c.json", list_apps=scan, clock=lambda: clock[0])    # a new start, an hour later
    second.load_now()
    assert scan.calls == 1 and second.search()["total"] == 5
    clock[0] += cat.TTL_SEC
    third = cat.AppCatalog(tmp_path / "c.json", list_apps=scan, clock=lambda: clock[0])
    third.load_now()
    assert scan.calls == 2                                                               # older than 24 h: scanned again
    assert third.loaded.is_set()


def test_a_stale_file_is_shown_at_once_while_the_new_scan_runs(tmp_path):
    clock = [1_000_000.0]
    old = Scanner([{"name": "Old App", "exe": "old.exe", "path": "", "steam": False}])
    cat.AppCatalog(tmp_path / "c.json", list_apps=old, clock=lambda: clock[0]).load_now()
    clock[0] += cat.TTL_SEC + 5
    seen = []

    def slow():
        seen.append(c.search()["apps"][0]["name"])       # (what a picker would see during the scan)
        return [{"name": "New App", "exe": "new.exe", "path": "", "steam": False}]

    c = cat.AppCatalog(tmp_path / "c.json", list_apps=slow, clock=lambda: clock[0])
    c.load_now()
    assert seen == ["Old App"] and c.search()["apps"][0]["name"] == "New App"


def test_a_broken_cache_file_means_a_scan(tmp_path):
    (tmp_path / "c.json").write_text("{not json")
    scan = Scanner(make_apps(3))
    c = cat.AppCatalog(tmp_path / "c.json", list_apps=scan)
    c.load_now()
    assert scan.calls == 1 and c.search()["total"] == 3
    (tmp_path / "c.json").write_text('{"v": 99, "saved": 1, "apps": []}')            # another format: also a scan
    c2 = cat.AppCatalog(tmp_path / "c.json", list_apps=scan)
    c2.load_now()
    assert scan.calls == 2


def test_the_file_keeps_no_running_flags(tmp_path):
    import json
    loaded_catalog(tmp_path, make_apps(3))
    data = json.loads((tmp_path / "cat.json").read_text())
    assert set(data["apps"][0]) == {"name", "exe", "path", "steam"}


def test_loading_is_reported_until_the_first_list_is_there(tmp_path):
    c = cat.AppCatalog(tmp_path / "c.json", list_apps=Scanner(make_apps(3)))
    assert c.search("all", "") == {"apps": [], "total": 0, "loading": True}
    c.load_now()
    assert c.search()["loading"] is False


def test_the_background_thread_loads_then_tells_the_page(tmp_path):
    done = threading.Event()
    c = cat.AppCatalog(tmp_path / "c.json", list_apps=Scanner(make_apps(4)), on_ready=done.set, start_delay=0)
    c.start()
    assert done.wait(5) and c.search()["total"] == 4


APPS = [
    {"name": "Discord", "exe": "discord.exe", "path": "C:\\d.exe", "steam": False},
    {"name": "Disk Cleanup", "exe": "cleanmgr.exe", "path": "C:\\c.exe", "steam": False},
    {"name": "Dishonored 2", "exe": "dishonored2.exe", "path": "C:\\s.exe", "steam": True},
    {"name": "Display Settings Helper", "exe": "dsh.exe", "path": "", "steam": False},
    {"name": "Google Chrome", "exe": "chrome.exe", "path": "C:\\chrome.exe", "steam": False},
    {"name": "Chromium Edge", "exe": "chromium.exe", "path": "", "steam": False},
    {"name": "Dis", "exe": "dis.exe", "path": "", "steam": False},
    {"name": "Spotify", "exe": "spotify.exe", "path": "", "steam": False},
]


def test_an_exact_name_comes_first_then_names_starting_with_it(tmp_path):
    c = loaded_catalog(tmp_path, APPS, running={"discord.exe": "C:\\d.exe"})
    names = [a["name"] for a in c.search("all", "dis")["apps"]]
    assert names[0] == "Dis"                                      # the exact name
    assert names[1:4] == ["Discord", "Disk Cleanup", "Display Settings Helper"]    # starting with it; running first
    assert names[-1] == "Dishonored 2"                            # Steam games after the rest, as in the old picker
    names = [a["name"] for a in c.search("all", "chrome")["apps"]]
    assert names[0] == "Google Chrome" and "Chromium Edge" not in names[:1]
    assert c.search("all", "chrome.exe")["apps"][0]["exe"] == "chrome.exe"
    assert c.search("all", "spotify")["apps"][0]["exe"] == "spotify.exe"


def test_small_typos_find_the_app_and_every_word_must_match(tmp_path):
    c = loaded_catalog(tmp_path, APPS)
    assert [a["exe"] for a in c.search("all", "discrd")["apps"]] == ["discord.exe"]
    assert c.search("all", "google spotify")["total"] == 0
    assert [a["exe"] for a in c.search("all", "google chrome")["apps"]] == ["chrome.exe"]
    assert [a["exe"] for a in c.search("all", "STEAM")["apps"]] == ["dishonored2.exe"]       # a game also matches "steam"
    assert c.search("all", "   ")["total"] == len(APPS)


def test_running_and_games_filters_and_the_running_mark(tmp_path):
    c = loaded_catalog(tmp_path, APPS, running={"chrome.exe": "C:\\chrome.exe", "newtool.exe": "C:\\New_Tool\\newtool.exe",
                                                  "explorer.exe": "C:\\Windows\\explorer.exe"})
    running = c.search("running", "")["apps"]
    assert [a["exe"] for a in running] == ["chrome.exe", "newtool.exe"]          # explorer.exe can't be blocked: left out
    assert running[1]["name"] == "Newtool" and running[0]["running"] is True       # (open but not in the list yet: added)
    assert [a["exe"] for a in c.search("games", "")["apps"]] == ["dishonored2.exe"]
    allapps = {a["exe"]: a for a in c.search("all", "")["apps"]}
    assert allapps["chrome.exe"]["running"] and not allapps["spotify.exe"]["running"] and allapps["dishonored2.exe"]["steam"]
    assert c.search("nonsense", "")["total"] == len(APPS) + 1                        # (an unknown filter shows everything)


def test_at_most_sixty_are_returned_and_the_total_says_how_many_matched(tmp_path):
    c = loaded_catalog(tmp_path, make_apps(300))
    r = c.search("all", "")
    assert len(r["apps"]) == 60 and r["total"] == 300
    assert len(c.search("all", "a", limit=10)["apps"]) == 10


def test_the_ranking_matches_search_rank(tmp_path):
    apps = make_apps(400, seed=7)
    c = loaded_catalog(tmp_path, apps, running={})
    rng = random.Random(3)
    queries = ["dis", "chorme", "visual studio", "discrd", "x", "nvidia 12", "gam", "ofice", "word excel", "panel", "zzzz",
               "update helper", "medai plyer"] + ["".join(rng.choice("abcdeimnoprstuv ") for _ in range(rng.randint(2, 8)))
                                                    for _ in range(25)]
    text_of = lambda a: f"{a['name']} {a['exe']}" + (" steam" if a["steam"] else "")      # noqa: E731
    for q in queries:
        want = {a["exe"] for a in search.rank(apps, q, text_of)}
        got = c.search("all", q, limit=10_000)["apps"]
        assert {a["exe"] for a in got} == want, q
        by_exe = {a["exe"]: a for a in apps}
        scores = [search.score(q, text_of(by_exe[a["exe"]])) for a in got]
        assert scores == sorted(scores), q                                   # best match (fewest typos) first


def test_apps_list_on_1500_cached_apps_is_fast(tmp_path):
    c = loaded_catalog(tmp_path, make_apps(1500), running={f"x{i}.exe": "C:\\x.exe" for i in range(5)})
    times = {}
    for q in ("dis", "", "visual studio", "chorme"):
        best = []
        for _ in range(15):
            t = time.perf_counter()
            c.search("all", q)
            best.append((time.perf_counter() - t) * 1000)
        best.sort()
        times[q] = (best[0], best[7])
    print("apps_list on 1500 apps (best / median ms): " + ", ".join(f"{q!r}: {a:.1f} / {m:.1f}" for q, (a, m) in times.items()))
    assert times["dis"][1] <= 20 and times[""][1] <= 20
    assert times["visual studio"][1] <= 50 and times["chorme"][1] <= 50


def test_the_open_windows_scan_is_not_repeated_every_call(tmp_path):
    calls = []
    c = cat.AppCatalog(tmp_path / "c.json", list_apps=Scanner(APPS), running_now=lambda: calls.append(1) or {})
    c.load_now()
    for _ in range(5):
        c.search()
    assert len(calls) == 1


def test_a_stale_open_windows_scan_never_makes_the_search_wait(tmp_path):
    gate, calls = threading.Event(), []

    def slow():
        calls.append(1)
        if len(calls) > 1:
            gate.wait(5)
        return {"chrome.exe": "C:\\chrome.exe"} if len(calls) == 1 else {"spotify.exe": "C:\\s.exe"}

    c = cat.AppCatalog(tmp_path / "c.json", list_apps=Scanner(APPS), running_now=slow)
    c.load_now()
    assert [a["exe"] for a in c.search("running")["apps"]] == ["chrome.exe"]       # the first scan is waited for
    c._running_at -= cat.RUNNING_SEC + 1                                         # ... a later one is not:
    t = time.perf_counter()
    assert [a["exe"] for a in c.search("running")["apps"]] == ["chrome.exe"]       # (still the old answer)
    assert time.perf_counter() - t < 0.5 and len(calls) <= 2
    gate.set()
    for _ in range(100):
        if [a["exe"] for a in c.search("running")["apps"]] == ["spotify.exe"]:
            break
        time.sleep(0.02)
    assert [a["exe"] for a in c.search("running")["apps"]] == ["spotify.exe"]


def test_steam_games_are_put_into_games_once(tmp_path):
    db_path = tmp_path / "t.db"
    Database(db_path)
    c = cat.AppCatalog(tmp_path / "c.json", list_apps=Scanner(APPS), seed_games=True, db_path=db_path)
    c.load_now()
    db = Database(db_path)
    assert db.categories().get(("app", "dishonored2.exe")) == "games"
    db.set_category("app", "dishonored2.exe", "productive")                    # the owner's choice sticks
    cat.AppCatalog(tmp_path / "c.json", list_apps=Scanner(APPS), seed_games=True, db_path=db_path).load_now()
    assert Database(db_path).categories().get(("app", "dishonored2.exe")) == "productive"


def test_path_for_uses_the_catalog_then_open_windows_then_the_owners_items(tmp_path):
    db_path = tmp_path / "t.db"
    db = Database(db_path)
    db.add_item("Old Game", ["oldgame.exe"], "app", app_path="D:\\Games\\oldgame.exe")
    c = cat.AppCatalog(tmp_path / "c.json", list_apps=Scanner(APPS), running_now=lambda: {"live.exe": "E:\\live.exe"},
                       db_path=db_path)
    c.load_now()
    c.search()
    assert c.path_for("Chrome.exe") == "C:\\chrome.exe" and c.path_for("live.exe") == "E:\\live.exe"
    assert c.path_for("oldgame.exe") == "D:\\Games\\oldgame.exe"
    assert c.path_for("nothing.exe") is None


def test_icons_find_programs_through_the_catalog(tmp_path):
    c = loaded_catalog(tmp_path, APPS)
    s = icons.IconService(tmp_path / "i", path_of=c.path_for, extract=lambda p: PNG if p == "C:\\chrome.exe" else None,
                          old_dir=tmp_path / "none", start=False)
    s.handle("app/chrome.exe.png", {})
    s.run_pending()
    assert s.handle("app/chrome.exe.png", {}).content_type == "image/png"


# ================================================================ Api: apps_list, pick_program_file

@pytest.fixture
def api(tmp_path):
    ctx = webapi.make_context(tmp_path / "api.db")
    ctx.catalog = loaded_catalog(tmp_path, APPS)
    return core.build_api(ctx), ctx


def test_the_api_methods_are_registered_as_reads():
    kinds = dict(webapi.api_methods())
    assert kinds["apps_list"] == "read" and kinds["pick_program_file"] == "read"
    assert "describe_program_file" not in kinds and "_ask_path" not in kinds


def test_apps_list_through_the_api(api):
    api, _ctx = api
    reply = api.apps_list("all", "disc")
    assert reply["ok"] and reply["data"]["apps"][0] == {"name": "Discord", "exe": "discord.exe", "path": "C:\\d.exe",
                                                       "running": False, "steam": False}
    assert api.apps_list()["data"]["total"] == len(APPS)
    assert not api.apps_list("everything", "")["ok"] and not api.apps_list("all", 5)["ok"]
    assert api.apps_list("all", "x" * 5000)["ok"]                               # (an overlong search is cut, not refused)


PICKED = [
    ("C:\\Games\\Foo\\Foo.exe", "foo.exe", "exe"), ("C:\\Games\\Foo\\game.jar", "game.jar", "java"),
    ("D:\\s\\run.py", "run.py", "python"), ("D:\\s\\run.pyw", "run.pyw", "python"), ("D:\\s\\go.bat", "go.bat", "cmd"),
    ("D:\\s\\go.cmd", "go.cmd", "cmd"), ("D:\\s\\go.ps1", "go.ps1", "powershell"), ("D:\\s\\a.vbs", "a.vbs", "wsh"),
    ("D:\\s\\a.js", "a.js", "wsh"), ("D:\\s\\a.wsf", "a.wsf", "wsh"), ("D:\\S\\UPPER.EXE", "upper.exe", "exe"),
]


@pytest.mark.parametrize("picked,exe,kind", PICKED)
def test_each_program_file_kind_is_recognised(picked, exe, kind):
    got = shared.describe_program_file(picked)
    assert got["exe"] == exe and got["kind"] == kind and got["path"] == picked and not got["whole_interpreter"]
    assert got["name"] == picked.rsplit("\\", 1)[-1].rsplit(".", 1)[0]


def test_a_shortcut_stands_for_what_it_starts():
    read = lambda path: (r"C:\Program Files\Java\bin\javaw.exe", "-jar game.jar", r"C:\Games\Fun")      # noqa: E731
    got = shared.describe_program_file(r"C:\Users\me\Desktop\Fun Game.lnk", read)
    assert got["path"] == r"C:\Games\Fun\game.jar" and got["kind"] == "java" and got["exe"] == "game.jar"
    assert got["name"] == "Fun Game"
    got = shared.describe_program_file(r"C:\x\Foo.lnk", lambda p: (r"C:\Games\Foo.exe", "", ""))
    assert got["kind"] == "exe" and got["exe"] == "foo.exe"
    with pytest.raises(core.ApiError, match="doesn't start a program"):
        shared.describe_program_file(r"C:\x\Doc.lnk", lambda p: (r"C:\docs\a.pdf", "", ""))
    with pytest.raises(core.ApiError, match="Couldn't read the shortcut"):
        shared.describe_program_file(r"C:\x\Bad.lnk", lambda p: 1 / 0)


def test_other_files_and_lockdowns_own_programs_are_refused():
    for bad in ("C:\\docs\\a.pdf", "C:\\docs\\a.txt", "C:\\docs\\noext", "C:\\a\\b.dll"):
        with pytest.raises(core.ApiError, match="Lockdown can block"):
            shared.describe_program_file(bad)
    for exe in ("python.exe", "lockdown.exe", "explorer.exe", "svchost.exe"):
        with pytest.raises(core.ApiError, match="can't be blocked"):
            shared.describe_program_file("C:\\x\\" + exe)


def test_picking_a_whole_interpreter_is_flagged_for_the_page_to_ask():
    for exe in ("javaw.exe", "cmd.exe", "wscript.exe", "powershell.exe"):
        assert shared.describe_program_file("C:\\x\\" + exe)["whole_interpreter"] is True
    assert shared.describe_program_file("C:\\x\\Foo.exe")["whole_interpreter"] is False


def test_pick_program_file_returns_the_pick_or_null(api, monkeypatch):
    api, ctx = api
    ctx.main_window = object()
    seen = []
    monkeypatch.setattr(shared, "_ask_path", lambda window: seen.append(window) or "C:\\Games\\Foo\\game.jar")
    reply = api.pick_program_file()
    assert reply["ok"] and reply["data"]["kind"] == "java" and seen == [ctx.main_window]
    monkeypatch.setattr(shared, "_ask_path", lambda window: None)
    assert api.pick_program_file() == {"ok": True, "data": None}
    monkeypatch.setattr(shared, "_ask_path", lambda window: "C:\\a\\notes.txt")
    reply = api.pick_program_file()
    assert not reply["ok"] and "Lockdown can block" in reply["error"]


def test_the_fallback_dialog_uses_pywebviews_and_keeps_windows_paths(monkeypatch):
    calls = []

    class Window:
        def create_file_dialog(self, kind, **kw):
            calls.append((kind, kw))
            return ("C:/Games/Foo/game.jar",)

    monkeypatch.setattr(shared.sys, "platform", "linux")
    import types
    fake = types.SimpleNamespace(FileDialog=types.SimpleNamespace(OPEN="open"))
    monkeypatch.setitem(sys.modules, "webview", fake)
    assert shared._ask_path(Window()) == "C:\\Games\\Foo\\game.jar"
    assert calls[0][0] == "open" and ".jar" in calls[0][1]["file_types"][0] and ".lnk" in calls[0][1]["file_types"][0]
    with pytest.raises(core.ApiError):
        shared._ask_path(None)
