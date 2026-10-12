"""UP15: real site icons for the new window - the bundled icon first, then a background download for your sites.

What is checked (the network is always replaced by fakes - nothing here goes online):
- the icon of a site is the cache, else the icon Lockdown brings (m.youtube.com and youtu.be get youtube.com's, no fetch),
  else a letter tile and a queued download; a cached icon over 30 days old is sent as it is and fetched again meanwhile; a
  failure is retried later (never in a tight loop); the switch "Download icons for my sites" off = no network at all;
- the prefetch of the sites on your lists: only your sites, once each, behind the page's own requests, at most two
  downloads at a time, and the programs' icons never wait for them;
- the chain (the site's favicon.ico, then the home page's <link> icons, only then the favicon service) and - above all -
  that Lockdown never contacts a site that is blocked right now or that resolves to a loopback / private / unspecified
  address (its listener would show a blocked-visit alert), at any step including redirects;
- the limits (sizes, time, ports, schemes, pixels) and the Settings switch's Api.
What only a Windows machine proves: real downloads through the owner's network, and that no blocked-visit alert appears."""
import functools
import http.client
import io
import os
import random
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import webapi
from db import Database
from importer import popular
from resident import icon_prefetch, iconfetch, icons
from webapi import core, settings
from webapi.guard import GuardService

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\nfake-icon"
NOW = datetime(2026, 10, 12, 10, 0, 0)


def make_image(size=(48, 48), color=(200, 30, 30, 255), fmt="PNG", **kw) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGBA", size, color).save(out, fmt, **kw)
    return out.getvalue()


def decode(png: bytes):
    from PIL import Image
    image = Image.open(io.BytesIO(png))
    image.load()
    return image


# ================================================================ the service: cache, bundled icon, letter tile

@pytest.fixture
def made(tmp_path):
    """An IconService with fakes and no threads (the test runs the jobs), and a way to see what was asked."""
    class Made:
        pass
    m = Made()
    m.ready, m.fetched, m.extracted = [], [], []
    m.now = [1000.0]
    m.dir, m.old = tmp_path / "icons", tmp_path / "old"
    m.results = {}                       # host -> bytes | None (default PNG)

    def fetch(host):
        m.fetched.append(host)
        return m.results.get(host, PNG)

    def extract(path):
        m.extracted.append(path)
        return PNG

    m.service = icons.IconService(m.dir, on_ready=m.ready.append, path_of=lambda e: r"C:\x\a.exe", extract=extract,
                                  fetch=fetch, old_dir=m.old, start=False, clock=lambda: m.now[0])
    return m


def site_file(made, host, data=PNG, age_days=0):
    made.dir.mkdir(parents=True, exist_ok=True)
    path = made.dir / f"site_{host}.png"
    path.write_bytes(data)
    when = time.time() - age_days * 86400
    os.utime(path, (when, when))
    return path


def test_the_bundled_icon_is_served_for_a_site_and_its_other_names_without_a_fetch(made):
    expected = (ROOT / "assets" / "sites" / "icons" / "youtube.com.png").read_bytes()
    for host in ("youtube.com", "m.youtube.com", "youtu.be", "www.youtube.com"):
        r = made.service.handle(f"site/{host}.png", {})
        assert r.content_type == "image/png" and r.body == expected and r.etag, host
    assert made.service.queued == 0
    made.service.run_pending()
    assert made.fetched == [] and made.ready == []


def test_many_icons_asked_at_once_before_the_site_list_is_loaded_all_find_their_bundled_icon(made, monkeypatch):
    monkeypatch.setattr(popular, "_SITES", None)
    found, gate = [], threading.Barrier(8)

    def ask():
        gate.wait(5)
        found.append(made.service.handle("site/youtube.com.png", {}).content_type)
    threads = [threading.Thread(target=ask) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert found == ["image/png"] * 8 and made.service.queued == 0


def test_a_listed_site_without_a_bundled_icon_is_a_letter_tile_and_a_download(made):
    site = next(s for s in popular.load_sites() if "icon" not in s)
    host = site["domains"][0]
    r = made.service.handle(f"site/{host}.png", {})
    assert r.content_type == "image/svg+xml" and made.service.queued == 1
    made.service.run_pending()
    assert made.fetched == [host] and made.ready == [f"site:{host}"]


def test_the_cache_comes_before_the_bundled_icon(made):
    site_file(made, "youtube.com", b"\x89PNG-downloaded")
    assert made.service.handle("site/youtube.com.png", {}).body == b"\x89PNG-downloaded"


def test_a_cached_icon_is_reused_and_nothing_is_fetched_again(made):
    s = made.service
    s.handle("site/news.example.org.png", {})
    s.run_pending()
    assert made.fetched == ["news.example.org"] and made.ready == ["site:news.example.org"]
    r = s.handle("site/news.example.org.png", {})
    assert r.content_type == "image/png" and r.body == PNG and s.queued == 0
    s.run_pending()
    assert made.fetched == ["news.example.org"]


def test_an_icon_over_30_days_old_is_sent_now_and_fetched_again_once(made):
    path = site_file(made, "news.example.org", b"\x89PNG-old", age_days=31)
    s = made.service
    made.results["news.example.org"] = b"\x89PNG-new"
    r = s.handle("site/news.example.org.png", {})
    assert r.body == b"\x89PNG-old" and s.queued == 1                    # (the old one meanwhile)
    s.handle("site/news.example.org.png", {})
    assert s.queued == 1
    s.run_pending()
    assert made.fetched == ["news.example.org"] and made.ready == ["site:news.example.org"]
    assert path.read_bytes() == b"\x89PNG-new" and time.time() - path.stat().st_mtime < 60
    assert s.handle("site/news.example.org.png", {}).body == b"\x89PNG-new" and s.queued == 0
    s.run_pending()
    assert made.fetched == ["news.example.org"]                          # (fresh now: not again)


def test_an_icon_under_30_days_old_is_left_alone(made):
    site_file(made, "news.example.org", age_days=29)
    made.service.handle("site/news.example.org.png", {})
    assert made.service.queued == 0


def test_a_failed_refresh_keeps_the_old_icon_and_waits_hours_before_the_next_try(made):
    site_file(made, "news.example.org", b"\x89PNG-old", age_days=40)
    made.results["news.example.org"] = None
    s = made.service
    s.handle("site/news.example.org.png", {})
    s.run_pending()
    assert made.fetched == ["news.example.org"] and made.ready == []
    assert s.handle("site/news.example.org.png", {}).body == b"\x89PNG-old"
    made.now[0] += icons.RETRY_SEC + 1
    s.handle("site/news.example.org.png", {})
    assert s.queued == 0                                                 # (not the 10 minutes of a new icon)
    made.now[0] += icons.REFRESH_RETRY_SEC
    s.handle("site/news.example.org.png", {})
    assert s.queued == 1


def test_a_failure_is_retried_after_the_wait_and_never_in_a_tight_loop(made):
    made.results["gone.example.org"] = None
    s = made.service
    s.handle("site/gone.example.org.png", {})
    s.run_pending()
    for _ in range(50):
        s.handle("site/gone.example.org.png", {})
        s.run_pending()
    assert made.fetched == ["gone.example.org"] and s.queued == 0
    made.now[0] += icons.RETRY_SEC - 1
    s.handle("site/gone.example.org.png", {})
    assert s.queued == 0
    made.now[0] += 2
    s.handle("site/gone.example.org.png", {})
    assert s.queued == 1


@pytest.mark.parametrize("name", ["localhost", "printer", "192.168.1.5", "10.0.0.1", "nas.local", "router.lan",
                                  "host.internal", "x.home.arpa", "a.test", "1.2.3.4"])
def test_a_name_that_cannot_be_a_public_site_is_a_letter_tile_and_never_asked_about(made, name):
    r = made.service.handle(f"site/{name}.png", {})
    assert r is not None and r.content_type == "image/svg+xml"
    assert made.service.queued == 0


# ================================================================ the switch: off = no network at all

def test_with_the_switch_off_nothing_is_queued_or_fetched_but_bundled_and_cached_icons_still_show(made):
    s = made.service
    s.download = False
    site_file(made, "news.example.org", b"\x89PNG-cached", age_days=90)
    assert s.handle("site/news.example.org.png", {}).body == b"\x89PNG-cached" and s.queued == 0   # (no refresh)
    assert s.handle("site/youtube.com.png", {}).content_type == "image/png"
    r = s.handle("site/other.example.org.png", {})
    assert r.content_type == "image/svg+xml" and s.queued == 0
    s.run_pending()
    assert made.fetched == [] and made.ready == []
    assert s.prefetch(["a.example.org", "b.example.org"]) == 0 and s.backlog == 0
    s.run_pending()
    assert made.fetched == []


def test_turning_it_back_on_downloads_again_and_off_left_no_failure_behind(made):
    s = made.service
    s.download = False
    s.handle("site/other.example.org.png", {})
    s.download = True
    s.handle("site/other.example.org.png", {})
    assert s.queued == 1
    s.run_pending()
    assert made.fetched == ["other.example.org"]


def test_with_the_switch_off_an_icon_of_the_old_window_is_still_taken_over_without_a_network(made):
    made.old.mkdir()
    (made.old / "news.example.org.png").write_bytes(PNG + b"old-window")
    s = made.service
    s.download = False
    s.handle("site/news.example.org.png", {})
    s.run_pending()
    assert made.fetched == [] and made.ready == ["site:news.example.org"]
    assert s.handle("site/news.example.org.png", {}).body == PNG + b"old-window"


def test_a_switch_turned_off_while_a_site_waits_stops_its_download(made):
    s = made.service
    s.handle("site/other.example.org.png", {})
    s.download = False
    s.run_pending()
    assert made.fetched == [] and s.queued == 0


def test_the_programs_icons_do_not_depend_on_the_switch(made):
    made.service.download = False
    made.service.handle("app/a.exe.png", {})
    made.service.run_pending()
    assert made.extracted == [r"C:\x\a.exe"] and made.ready == ["app:a.exe"]


# ================================================================ the prefetch of your sites

def test_the_prefetch_queues_only_real_sites_without_an_icon_each_once(made):
    s = made.service
    site_file(made, "cached.example.org")
    site_file(made, "stale.example.org", age_days=45)
    added = s.prefetch(["Fresh.example.org", "fresh.example.org", "cached.example.org", "stale.example.org", "youtube.com",
                        "m.youtube.com", "printer.local", "192.168.1.9", "localhost", "bad name", "", "a_b.example.org"])
    assert added == 2 and s.backlog == 2
    assert s.prefetch(["fresh.example.org", "stale.example.org"]) == 0
    s.run_pending()
    assert made.fetched == ["fresh.example.org", "stale.example.org"]
    assert sorted(made.ready) == ["site:fresh.example.org", "site:stale.example.org"]
    assert s.backlog == 0 and s.queued == 0
    assert s.prefetch(["fresh.example.org", "stale.example.org"]) == 0       # (cached now, fresh)


def test_a_prefetched_site_that_failed_is_not_queued_again_before_the_wait(made):
    made.results["gone.example.org"] = None
    s = made.service
    s.prefetch(["gone.example.org"])
    s.run_pending()
    assert s.prefetch(["gone.example.org"]) == 0
    made.now[0] += icons.RETRY_SEC + 1
    assert s.prefetch(["gone.example.org"]) == 1


def test_the_pages_own_requests_go_before_the_backlog_and_a_site_is_fetched_once(made):
    s = made.service
    s.prefetch(["a.example.org", "b.example.org", "c.example.org"])
    s.handle("site/c.example.org.png", {})                  # (the page wants c now)
    s.handle("site/z.example.org.png", {})
    s.run_pending()
    assert made.fetched == ["c.example.org", "z.example.org", "a.example.org", "b.example.org"]


def test_the_backlog_is_not_held_to_the_page_queues_cap(made):
    s = made.service
    s.prefetch([f"h{i}.example.org" for i in range(300)])
    assert s.backlog == 300 and s.queued == 0
    for i in range(icons.MAX_QUEUED + 5):
        s.handle(f"site/p{i}.example.org.png", {})
    assert s.queued == icons.MAX_QUEUED
    assert s.prefetch([f"more{i}.example.org" for i in range(icons.MAX_BACKLOG)]) == icons.MAX_BACKLOG - 300


def test_at_most_two_downloads_are_in_flight_and_a_program_icon_never_waits_for_them(tmp_path):
    lock, state = threading.Lock(), {"now": 0, "peak": 0, "threads": set()}
    hold, ready = threading.Event(), []

    def fetch(host):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            state["threads"].add(threading.current_thread().name)
        hold.wait(10)
        with lock:
            state["now"] -= 1
        return PNG

    s = icons.IconService(tmp_path / "i", on_ready=ready.append, path_of=lambda e: r"C:\a.exe", extract=lambda p: PNG,
                          fetch=fetch, old_dir=tmp_path / "none", pause=0)
    assert s.prefetch([f"h{i}.example.org" for i in range(6)]) == 6
    deadline = time.time() + 5
    while state["now"] < 2 and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)                                          # (a third one would have started by now)
    assert state["now"] == 2
    s.handle("app/a.exe.png", {})                            # the two threads are busy; the program's icon still comes
    deadline = time.time() + 5
    while "app:a.exe" not in ready and time.time() < deadline:
        time.sleep(0.01)
    assert "app:a.exe" in ready
    hold.set()
    deadline = time.time() + 5
    while len([k for k in ready if k.startswith("site:")]) < 6 and time.time() < deadline:
        time.sleep(0.01)
    assert len([k for k in ready if k.startswith("site:")]) == 6
    assert state["peak"] == 2 and state["threads"] == {"lockdown-icons-net1", "lockdown-icons-net2"}


def test_a_pause_follows_every_download(tmp_path):
    rests = []
    done = threading.Event()
    s = icons.IconService(tmp_path / "i", on_ready=lambda k: None, fetch=lambda h: PNG, old_dir=tmp_path / "none",
                          sleep=lambda sec: (rests.append(sec), len(rests) >= 3 and done.set()))
    s.prefetch(["a.example.org", "b.example.org"])
    s.handle("site/c.example.org.png", {})
    assert done.wait(5) and set(rests) == {icons.PAUSE_SEC}


# ---------------------------------------------------------------- which sites are "yours"

@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


def test_my_site_hosts_are_what_the_pages_show_for_your_lists(db):
    import keywords
    import modes
    from blocker import protection
    youtube = db.add_item("YouTube", ["youtube.com", "m.youtube.com", "googlevideo.com"], "site", "test",
                          [{"rule_type": "permanent"}], None, None, None)
    db.add_item("Old", ["www.oldsite.example.org"], "site", "test", [{"rule_type": "permanent"}], None, None, None)
    db.add_item("Discord", ["discord.exe"], "app", "test", [{"rule_type": "permanent"}], None, None, None)
    db.add_item("Games", ["games"], "category", "test", [{"rule_type": "permanent"}], None, None, None)
    db.add_group("Video", [{"rule_type": "permanent"}], {youtube: {}})           # (a group holds the same items)
    mode_list = modes.load(db)
    mode_list.append({"id": "custom1", "name": "Mine", "builtin": False, "categories": [], "items": [], "groups": [],
                      "extra": [{"kind": "site", "name": "Extra", "targets": ["extra.example.org", "e2.example.org"]},
                                {"kind": "app", "name": "App", "targets": ["x.exe"]}],
                      "mute": False, "pomodoro": None, "schedule": None})
    modes.save(db, mode_list)
    cfg = protection.settings(db)
    cfg["allowed"] = ["Allowed.example.org", "youtube.com"]
    protection.save_settings(db, cfg)
    words = keywords.settings(db)
    words["exceptions"] = ["exception.example.org", "plainword"]
    keywords.save(db, words)
    assert sorted(icon_prefetch.my_site_hosts(db)) == sorted([
        "youtube.com", "www.oldsite.example.org", "extra.example.org", "allowed.example.org", "exception.example.org"])


def test_my_site_hosts_stops_at_the_limit(db):
    for i in range(30):
        db.add_item(f"S{i}", [f"s{i}.example.org"], "site", "test", [{"rule_type": "permanent"}], None, None, None)
    assert len(icon_prefetch.my_site_hosts(db, limit=10)) == 10


class FakeService:
    def __init__(self):
        self.download, self.queued = True, []

    def prefetch(self, hosts):
        self.queued.append(list(hosts))
        return len(hosts)


def test_a_scan_reads_the_switch_and_the_lists(db):
    service = FakeService()
    p = icon_prefetch.Prefetcher(SimpleNamespace(db=db), service, collect=lambda d: ["a.example.org"])
    assert p.scan() == 1 and service.queued == [["a.example.org"]]
    db.set_setting(icons.DOWNLOAD_KEY, "0")
    assert p.scan() == 0 and service.download is False and len(service.queued) == 1
    db.set_setting(icons.DOWNLOAD_KEY, "1")
    assert p.scan() == 1 and service.download is True


def test_a_scan_that_hits_a_damaged_list_does_not_crash(db):
    def boom(d):
        raise ValueError("damaged")
    assert icon_prefetch.Prefetcher(SimpleNamespace(db=db), FakeService(), collect=boom).scan() == 0


def test_the_prefetch_thread_scans_after_the_start_delay_and_after_every_poke(db):
    service, scans = FakeService(), threading.Semaphore(0)
    p = icon_prefetch.Prefetcher(SimpleNamespace(db=db), service,
                                 collect=lambda d: (scans.release(), ["a.example.org"])[1], sleep=lambda sec: None)
    p.start()
    p.start()                                                  # (once)
    assert scans.acquire(timeout=5)
    assert not scans.acquire(timeout=0.2)                      # (nothing more until something changes)
    p.poke()
    assert scans.acquire(timeout=5)


def test_attach_wakes_the_prefetch_on_list_changes_only_and_keeps_publishing(db):
    sent = []
    ctx = SimpleNamespace(db=db, publish=lambda event, payload=None, key=None: sent.append((event, payload)) or "sent")
    service = FakeService()
    p = icon_prefetch.attach(ctx, service)
    assert ctx.icons is service and ctx.icon_prefetch is p
    assert ctx.publish("status", {"a": 1}) == "sent" and not p._flag.is_set()
    assert ctx.publish("icon_ready", {"key": "site:x"}) == "sent" and not p._flag.is_set()
    assert ctx.publish("blocks_changed", {"rev": 1}) == "sent" and p._flag.is_set()
    p._flag.clear()
    ctx.publish("mode_changed", {"rev": 2})
    assert p._flag.is_set() and sent == [("status", {"a": 1}), ("icon_ready", {"key": "site:x"}),
                                         ("blocks_changed", {"rev": 1}), ("mode_changed", {"rev": 2})]


def test_attach_wakes_the_prefetch_even_when_the_publisher_fails(db):
    def broken(event, payload=None, key=None):
        raise RuntimeError("window gone")
    ctx = SimpleNamespace(db=db, publish=broken)
    p = icon_prefetch.attach(ctx, FakeService())
    with pytest.raises(RuntimeError):
        ctx.publish("blocks_changed", {})
    assert p._flag.is_set()


def test_the_switch_tells_the_service_at_once_and_a_turn_on_scans(db):
    service = FakeService()
    p = icon_prefetch.Prefetcher(SimpleNamespace(db=db), service)
    p.setting_changed(False)
    assert service.download is False and not p._flag.is_set()
    p.setting_changed(True)
    assert service.download is True and p._flag.is_set()


def test_the_agent_is_wired_to_it():
    source = (ROOT / "src" / "agent.py").read_text(encoding="utf-8")
    assert "icon_prefetch.attach(ctx, icon_service)" in source and "site_icons.start()" in source
    assert source.index("ctx.publish = win.publisher.publish") < source.index("icon_prefetch.attach(")   # (wraps the real one)
    assert "download=icons.download_on(ctx.db)" in source


# ================================================================ the chain

class Net:
    """A fake `get(url)`: what each address answers (bytes, or missing = nothing), and the list of what was asked."""
    def __init__(self, **pages):
        self.pages = {}
        self.calls = []

    def serve(self, url, body, final=None):
        self.pages[url] = (body, final or url)
        return self

    def __call__(self, url, *a, **kw):
        self.calls.append(url)
        return self.pages.get(url)


def chain(host, net, blocked=lambda h: False):
    return iconfetch.fetch_site_icon(host, get=net, blocked=blocked)


SERVICE = iconfetch.FAVICON_URL


def test_the_sites_own_favicon_is_taken_first_and_nothing_else_is_asked(tmp_path):
    net = Net().serve("https://news.example.org/favicon.ico", make_image((48, 48)))
    png = chain("news.example.org", net)
    assert decode(png).size == (48, 48) and net.calls == ["https://news.example.org/favicon.ico"]


def test_when_favicon_ico_fails_the_home_pages_link_is_parsed_and_used(tmp_path):
    html = ('<html><head><link rel="stylesheet" href="/a.css"><link rel="shortcut icon" href="/static/16.png" sizes="16x16">'
            '<link rel="icon" href="icon-48.png" sizes="48x48"></head></html>')
    net = (Net().serve("https://news.example.org/", html.encode(), final="https://news.example.org/home/")
           .serve("https://news.example.org/home/icon-48.png", make_image((48, 48), (0, 200, 0, 255))))
    png = chain("news.example.org", net)
    assert decode(png).getpixel((0, 0)) == (0, 200, 0, 255)
    assert net.calls == ["https://news.example.org/favicon.ico", "https://news.example.org/",
                         "https://news.example.org/home/icon-48.png"]                    # (the 16 px one is the last choice)


def test_the_best_size_from_32_up_is_tried_first(tmp_path):
    html = ('<link rel="icon" sizes="32x32" href="/32.png"><link rel="icon" sizes="16x16" href="/16.png">'
            '<link rel="icon" sizes="128x128" href="/128.png"><link rel="icon" sizes="64x64" href="/64.png">'
            '<link rel="apple-touch-icon" href="/touch.png"><link rel="mask-icon" href="/mask.svg">'
            '<link rel="icon" type="image/svg+xml" href="/vector"><link rel="icon" href="/x.svg">')
    links = iconfetch.icon_links(html, "https://a.example.org/")
    assert links == ["https://a.example.org/64.png", "https://a.example.org/128.png", "https://a.example.org/touch.png",
                     "https://a.example.org/32.png", "https://a.example.org/16.png"]
    net = Net().serve("https://a.example.org/", html.encode())
    assert chain("a.example.org", net) is None
    assert net.calls[:5] == ["https://a.example.org/favicon.ico", "https://a.example.org/", "https://a.example.org/64.png",
                             "https://a.example.org/128.png", "https://a.example.org/touch.png"]
    assert len(net.calls) == 6 and net.calls[-1] == SERVICE.format(domain="a.example.org")      # (3 links at most, then the service)


def test_links_are_resolved_against_the_page_and_odd_schemes_are_dropped():
    html = ('<link rel="icon" href="//cdn.example.net/i.png"><link rel="icon" href="javascript:alert(1)">'
            '<link rel="icon" href="data:image/png;base64,AAAA"><link rel="icon" href="ftp://x.example.org/i.png">'
            '<link rel="ICON" href=" /spaced.png "><link rel="icon"><link href="/norel.png">')
    assert iconfetch.icon_links(html, "https://a.example.org/dir/page") == [
        "https://cdn.example.net/i.png", "https://a.example.org/spaced.png"]


def test_a_page_with_a_thousand_links_is_cut_short():
    html = "".join(f'<link rel="icon" href="/{i}.png" sizes="64x64">' for i in range(1000))
    assert len(iconfetch.icon_links(html, "https://a.example.org/")) == iconfetch.MAX_LINKS


def test_a_favicon_that_is_not_a_picture_goes_on_to_the_home_page(tmp_path):
    net = (Net().serve("https://news.example.org/favicon.ico", b"<html>404 not found</html>")
           .serve("https://news.example.org/", b'<link rel="icon" href="/i.png">')
           .serve("https://news.example.org/i.png", make_image((32, 32))))
    assert decode(chain("news.example.org", net)).size == (32, 32)
    assert net.calls[-1] == "https://news.example.org/i.png"


def test_when_the_site_gives_nothing_the_favicon_service_is_asked_last(tmp_path):
    net = Net().serve(SERVICE.format(domain="news.example.org"), make_image((64, 64), (1, 2, 3, 255)))
    png = chain("news.example.org", net)
    assert decode(png).getpixel((0, 0)) == (1, 2, 3, 255)
    assert net.calls == ["https://news.example.org/favicon.ico", "https://news.example.org/",
                         SERVICE.format(domain="news.example.org")]


def test_nothing_anywhere_is_no_icon(tmp_path):
    assert chain("news.example.org", Net()) is None


def test_a_site_that_is_blocked_right_now_goes_straight_to_the_service(tmp_path):
    net = Net().serve(SERVICE.format(domain="news.example.org"), make_image((64, 64)))
    assert chain("news.example.org", net, blocked=lambda h: True) is not None
    assert net.calls == [SERVICE.format(domain="news.example.org")]


@pytest.mark.parametrize("name", ["localhost", "printer", "192.168.1.5", "nas.local", "router.lan", "[::1]", "a b.example.org",
                                  "http://x.example.org"])
def test_a_name_that_is_no_public_site_is_not_looked_up_anywhere(name):
    net = Net().serve(SERVICE.format(domain=name), make_image())
    assert chain(name, net) is None and net.calls == []


# ---------------------------------------------------------------- never a loopback / private / unspecified address

def addr_info(ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (ip, 0))


class FakeInternet:
    """Names -> addresses (a getaddrinfo), and connections that answer from `routes` - and note every connection made."""
    def __init__(self, names=None, routes=None):
        self.names = dict(names or {})
        self.routes = dict(routes or {})
        self.lookups, self.connections = [], []
        self.clock = [0.0]

    def getaddrinfo(self, host, port=None, **kw):
        self.lookups.append(host)
        if host not in self.names:
            raise socket.gaierror("no such name")
        return [addr_info(ip) for ip in self.names[host]]

    def resolve(self, host):
        return iconfetch.resolve_public(host, getaddrinfo=self.getaddrinfo)

    def opener(self, scheme, host, addresses, port, timeout):
        self.connections.append((scheme, host, [ip for _f, ip in addresses], port))
        return FakeConn(self, scheme, host)

    def get(self, url, **kw):
        return iconfetch.http_get(url, resolve=self.resolve, opener=self.opener, clock=lambda: self.clock[0], **kw)

    def hosts_connected(self):
        return [c[1] for c in self.connections]


class FakeConn:
    def __init__(self, net, scheme, host):
        self.net, self.scheme, self.host, self.closed = net, scheme, host, False

    def request(self, method, path, headers=None):
        assert method == "GET" and "Accept-Encoding" in headers and "User-Agent" in headers
        self.path = path

    def getresponse(self):
        route = self.net.routes.get((self.scheme, self.host, self.path))
        if isinstance(route, Exception):
            raise route
        return FakeResponse(self.net, *(route or (404, {}, b"")))

    def close(self):
        self.closed = True


class FakeResponse:
    def __init__(self, net, status, headers, body):
        self.net, self.status, self.headers, self.body = net, status, headers, body

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, n=-1):
        self.net.clock[0] += getattr(self.net, "tick", 0)
        chunk, self.body = self.body[:n], self.body[n:]
        return chunk


PRIVATE = ["127.0.0.1", "127.1.2.3", "0.0.0.0", "192.168.0.10", "10.0.0.2", "172.16.5.5", "169.254.1.1", "100.64.0.1",
           "224.0.0.1", "255.255.255.255", "192.0.2.1", "::1", "::", "fe80::1", "fc00::1", "::ffff:127.0.0.1",
           "::ffff:192.168.1.1", "ff02::1"]


@pytest.mark.parametrize("ip", PRIVATE)
def test_a_site_that_resolves_to_a_private_or_loopback_address_is_never_contacted_only_the_service_is(ip):
    net = FakeInternet(names={"blocked.example.org": [ip], "www.google.com": ["142.250.1.1"]},
                       routes={("https", "www.google.com", "/s2/favicons?domain=blocked.example.org&sz=64"):
                               (200, {}, make_image((64, 64)))})
    png = iconfetch.fetch_site_icon("blocked.example.org", get=net.get, blocked=lambda h: False)
    assert png is not None
    assert net.hosts_connected() == ["www.google.com"] and net.connections[0][2] == ["142.250.1.1"]


def test_a_mixed_answer_with_one_private_address_is_refused():
    net = FakeInternet(names={"mixed.example.org": ["93.184.216.34", "127.0.0.1"]})
    assert net.get("https://mixed.example.org/favicon.ico") is None and net.connections == []


def test_public_addresses_are_used_ipv4_first_and_the_connection_goes_to_the_checked_address():
    net = FakeInternet(names={"ok.example.org": ["2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34"]},
                       routes={("https", "ok.example.org", "/favicon.ico"): (200, {}, b"BODY")})
    assert net.get("https://ok.example.org/favicon.ico") == (b"BODY", "https://ok.example.org/favicon.ico")
    assert net.connections == [("https", "ok.example.org", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"], 443)]


def test_when_the_service_host_itself_is_blocked_nothing_is_contacted_at_all():
    net = FakeInternet(names={"a.example.org": ["93.184.216.34"], "www.google.com": ["127.0.0.1"]})
    assert iconfetch.fetch_site_icon("a.example.org", get=net.get, blocked=lambda h: False) is None
    assert "www.google.com" not in net.hosts_connected()


def test_an_unresolvable_name_is_no_connection():
    net = FakeInternet()
    assert net.get("https://nowhere.example.org/") is None and net.connections == []


def test_a_redirect_to_a_loopback_address_or_a_private_name_is_not_followed():
    net = FakeInternet(
        names={"a.example.org": ["93.184.216.34"], "lan.example.org": ["192.168.1.7"], "b.example.org": ["93.184.216.35"]},
        routes={("https", "a.example.org", "/to-ip"): (302, {"Location": "http://127.0.0.1/secret"}, b""),
                ("https", "a.example.org", "/to-name"): (301, {"Location": "https://lan.example.org/x"}, b""),
                ("https", "a.example.org", "/to-ip6"): (302, {"Location": "http://[::1]/"}, b""),
                ("https", "a.example.org", "/to-local"): (302, {"Location": "http://printer.local/"}, b""),
                ("https", "a.example.org", "/to-ok"): (302, {"Location": "https://b.example.org/ok"}, b""),
                ("https", "b.example.org", "/ok"): (200, {}, b"FINE")})
    for path in ("/to-ip", "/to-name", "/to-ip6", "/to-local"):
        assert net.get(f"https://a.example.org{path}") is None
    assert set(net.hosts_connected()) == {"a.example.org"} and all(c[2] != ["192.168.1.7"] for c in net.connections)
    assert net.get("https://a.example.org/to-ok") == (b"FINE", "https://b.example.org/ok")


def test_redirects_are_followed_a_few_times_with_relative_addresses_then_given_up():
    routes = {("https", "a.example.org", f"/{i}"): (302, {"Location": f"/{i + 1}"}, b"") for i in range(10)}
    routes[("https", "a.example.org", "/3")] = (200, {}, b"DONE")
    net = FakeInternet(names={"a.example.org": ["93.184.216.34"]}, routes=routes)
    assert net.get("https://a.example.org/0") == (b"DONE", "https://a.example.org/3")        # (3 redirects)
    assert net.get("https://a.example.org/-1") is None
    assert net.get("https://a.example.org/4") is None                                         # (never ends)
    assert len(net.connections) == 4 + 1 + 4


def test_a_redirect_without_a_place_or_to_another_scheme_is_dropped():
    net = FakeInternet(names={"a.example.org": ["93.184.216.34"]}, routes={
        ("https", "a.example.org", "/none"): (302, {}, b""),
        ("https", "a.example.org", "/ftp"): (302, {"Location": "ftp://a.example.org/x"}, b""),
        ("https", "a.example.org", "/file"): (302, {"Location": "file:///etc/passwd"}, b""),
        ("https", "a.example.org", "/http"): (302, {"Location": "http://a.example.org/p"}, b""),
        ("http", "a.example.org", "/p"): (200, {}, b"PLAIN")})
    for path in ("/none", "/ftp", "/file"):
        assert net.get(f"https://a.example.org{path}") is None
    assert net.get("https://a.example.org/http") == (b"PLAIN", "http://a.example.org/p")


@pytest.mark.parametrize("url", ["ftp://a.example.org/x", "file:///etc/passwd", "javascript:alert(1)", "data:text/plain,hi",
                                 "https://a.example.org:8080/x", "http://a.example.org:22/", "https://a.example.org:444/",
                                 "https://user:pw@a.example.org/x", "https://127.0.0.1/", "https://[::1]/",
                                 "https://192.168.1.1/x", "https://localhost/", "https:///x", "//a.example.org/x", "/x", ""])
def test_only_http_and_https_on_the_usual_ports_to_public_names(url):
    net = FakeInternet(names={"a.example.org": ["93.184.216.34"]},
                       routes={("https", "a.example.org", "/x"): (200, {}, b"X")})
    assert net.get(url) is None and net.connections == []


@pytest.mark.parametrize("status", [204, 301, 400, 403, 404, 500, 503])
def test_only_an_answer_of_200_counts(status):
    net = FakeInternet(names={"a.example.org": ["93.184.216.34"]},
                       routes={("https", "a.example.org", "/x"): (status, {}, b"X")})
    assert net.get("https://a.example.org/x") is None


def test_an_answer_is_cut_at_200_kb_and_a_smaller_limit_is_kept():
    net = FakeInternet(names={"a.example.org": ["93.184.216.34"]},
                       routes={("https", "a.example.org", "/big"): (200, {}, b"x" * 500_000)})
    body, _ = net.get("https://a.example.org/big")
    assert len(body) == 200_000 == iconfetch.MAX_BYTES
    assert len(net.get("https://a.example.org/big", limit=1234)[0]) == 1234


def test_a_server_that_drips_is_given_up_on_after_the_request_time():
    net = FakeInternet(names={"a.example.org": ["93.184.216.34"]},
                       routes={("https", "a.example.org", "/slow"): (200, {}, b"x" * 100_000)})
    net.tick = 2.0                                           # (every read takes 2 s)
    assert net.get("https://a.example.org/slow") is None


@pytest.mark.parametrize("error", [OSError("reset"), TimeoutError("slow"), http.client.BadStatusLine("junk"),
                                   http.client.IncompleteRead(b"x"), ValueError("bad url"), __import__("ssl").SSLError("tls")])
def test_every_network_error_is_just_no_icon(error):
    net = FakeInternet(names={"a.example.org": ["93.184.216.34"]}, routes={("https", "a.example.org", "/x"): error})
    assert net.get("https://a.example.org/x") is None


def test_the_whole_chain_with_a_blocked_looking_site_never_connects_to_it():
    html = b'<link rel="icon" href="/i.png">'
    net = FakeInternet(
        names={"blocked.example.org": ["127.0.0.1"], "www.google.com": ["142.250.1.1"]},
        routes={("https", "blocked.example.org", "/favicon.ico"): (200, {}, make_image()),
                ("https", "blocked.example.org", "/"): (200, {}, html),
                ("https", "www.google.com", "/s2/favicons?domain=blocked.example.org&sz=64"): (200, {}, make_image())})
    assert iconfetch.fetch_site_icon("blocked.example.org", get=net.get, blocked=lambda h: False) is not None
    assert net.hosts_connected() == ["www.google.com"]


def test_a_pinned_connection_goes_to_the_checked_address_not_the_name(monkeypatch):
    """The real connection class: create_connection gets the IP that was checked (the name is only for the certificate)."""
    seen = []

    class Sock:
        def settimeout(self, t): pass
        def close(self): pass

    def create_connection(address, timeout=None):
        seen.append(address)
        return Sock()
    monkeypatch.setattr(iconfetch.socket, "create_connection", create_connection)
    conn = iconfetch.connect("http", "a.example.org", [(socket.AF_INET, "93.184.216.34")], 80, 5)
    conn.connect()
    assert seen == [("93.184.216.34", 80)] and conn.host == "a.example.org"
    bad = iconfetch.connect("http", "a.example.org", [], 80, 5)
    with pytest.raises(OSError):
        bad.connect()


def test_the_real_connection_class_talks_http_to_the_checked_address_and_sends_the_name_as_host():
    """A real socket to a local test server (reached through the fake resolver; the real one would refuse loopback)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers["Host"], self.headers["User-Agent"], self.headers["Accept-Encoding"]))
            if self.path == "/old":
                self.send_response(302)
                self.send_header("Location", "/new?x=1")
                self.end_headers()
                return
            body = b"ICONDATA" * 40_000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        got = iconfetch.http_get("http://a.example.org/old", resolve=lambda h: [(socket.AF_INET, "127.0.0.1")],
                                 opener=lambda scheme, host, addresses, _port, timeout:
                                 iconfetch.connect(scheme, host, addresses, port, timeout))
    finally:
        server.shutdown()
    assert got is not None and got[1] == "http://a.example.org/new?x=1" and got[0] == b"ICONDATA" * 25_000
    assert len(got[0]) == iconfetch.MAX_BYTES
    assert [path for path, *_ in seen] == ["/old", "/new?x=1"]
    assert all(host.split(":")[0] == "a.example.org" and "Lockdown" in ua and enc == "identity" for _p, host, ua, enc in seen)


def test_a_real_https_connection_checks_the_certificate_for_the_name():
    conn = iconfetch.connect("https", "a.example.org", [(socket.AF_INET, "93.184.216.34")], 443, 5)
    assert conn._context.check_hostname and conn._context.verify_mode.name == "CERT_REQUIRED"
    assert iconfetch.connect("https", "b.example.org", [], 443, 5)._context is conn._context          # (made once)


def test_public_ip_judgement():
    for ip in ("93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"):
        assert iconfetch.is_public_ip(ip), ip
    for ip in PRIVATE + ["", "garbage", "999.1.1.1", "fe80::1%eth0"]:
        assert not iconfetch.is_public_ip(ip), ip


# ---------------------------------------------------------------- "blocked right now" from the hosts file

HOSTS = """127.0.0.1 localhost
0.0.0.0 outside.example.org
# >>> Lockdown START (managed automatically - do not edit)
127.0.0.1 youtube.com
127.0.0.1 www.youtube.com
127.0.0.1 reddit.com
127.0.0.1 www.reddit.com
# <<< Lockdown END
127.0.0.1 after.example.org
"""


def test_a_site_is_blocked_right_now_when_its_name_or_a_parent_is_in_lockdowns_part_of_the_hosts_file(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text(HOSTS, encoding="utf-8")
    blocked = functools.partial(iconfetch.hosts_blocked, path=hosts)
    for host in ("youtube.com", "www.youtube.com", "m.youtube.com", "rr1.sn-x.googlevideo.com.youtube.com", "Reddit.com",
                 "old.reddit.com", "reddit.com."):
        assert blocked(host), host
    for host in ("notyoutube.com", "youtube.org", "com", "outside.example.org", "after.example.org", "localhost",
                 "example.org"):
        assert not blocked(host), host
    hosts.write_text(HOSTS.replace("youtube", "vimeo"), encoding="utf-8")                  # (changed: read again)
    assert not blocked("youtube.com") and blocked("vimeo.com")
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    assert not blocked("vimeo.com")


def test_a_missing_hosts_file_is_not_blocked(tmp_path):
    assert iconfetch.hosts_blocked("youtube.com", path=tmp_path / "nothing") is False


# ---------------------------------------------------------------- what is kept: PNG <= 64 px, transparency, pixel cap

def test_a_big_picture_is_shrunk_to_64_px_keeping_its_transparency():
    from PIL import Image
    image = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (0, 0, 100, 100))
    out = io.BytesIO()
    image.save(out, "PNG")
    png = iconfetch.to_png(out.getvalue())
    result = decode(png)
    assert result.format == "PNG" and result.mode == "RGBA" and max(result.size) == 64 == iconfetch.OUT_SIZE
    assert result.size == (64, 32) and result.getpixel((5, 5))[3] == 255 and result.getpixel((60, 5))[3] == 0


def test_a_small_picture_is_not_blown_up():
    assert decode(iconfetch.to_png(make_image((16, 16)))).size == (16, 16)


@pytest.mark.parametrize("fmt", ["PNG", "GIF", "WEBP", "BMP", "JPEG"])
def test_the_usual_picture_formats_are_read(fmt):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (40, 40), (10, 100, 200)).save(out, fmt)
    assert decode(iconfetch.to_png(out.getvalue())).size == (40, 40)


def test_an_ico_with_several_sizes_gives_the_best_one():
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGBA", (256, 256), (9, 9, 200, 255)).save(out, "ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    result = decode(iconfetch.to_png(out.getvalue()))
    assert result.size == (64, 64) and result.getpixel((10, 10)) == (9, 9, 200, 255)       # (the 256 shrunk: smallest from 64 up)
    out = io.BytesIO()
    Image.new("RGBA", (48, 48), (9, 9, 200, 255)).save(out, "ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    assert decode(iconfetch.to_png(out.getvalue())).size == (48, 48)                       # (largest between 32 and 63)


def test_a_huge_picture_is_never_decoded(monkeypatch):
    """3000x3000 is a few KB as a PNG; its header is enough to say no (PIL's own cap is lowered to ours meanwhile)."""
    from PIL import Image
    big = make_image((3000, 3000))
    between = make_image((1200, 1000))                                                       # (over our cap, under PIL's bomb limit)
    assert len(big) < 100_000
    loaded = []
    original = Image.Image.load
    monkeypatch.setattr(Image.Image, "load", lambda self, *a, **k: (loaded.append(1), original(self, *a, **k))[1])
    assert iconfetch.to_png(big) is None and loaded == []
    assert iconfetch.to_png(between) is None and loaded == []
    assert Image.MAX_IMAGE_PIXELS not in (iconfetch.MAX_PIXELS,)                             # (PIL's cap is put back)
    assert iconfetch.to_png(make_image((1000, 1000))) is not None


def test_not_a_picture_is_nothing():
    for junk in (b"", b"<html>no</html>", b"\x00" * 100, b"GIF89a-broken", PNG, b"<svg xmlns='http://www.w3.org/2000/svg'/>",
                 b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n", b"8BPS\x00\x01" + b"\x00" * 50):
        assert iconfetch.to_png(junk) is None, junk[:20]


def test_a_fully_transparent_picture_is_nothing():
    assert iconfetch.to_png(make_image((32, 32), (0, 0, 0, 0))) is None


# ================================================================ the Settings switch (Api)

class Clock:
    now = NOW


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


@pytest.fixture
def ctx(tmp_path):
    c = webapi.make_context(tmp_path / "api.db", clock=lambda db: Clock.now)
    c.guard = GuardService(lambda: c.db, lambda db: Clock.now, rng=random.Random(3))
    c.publish = lambda event, payload=None, key=None: None
    c.told = []
    c.icon_prefetch = SimpleNamespace(setting_changed=c.told.append)
    return c


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def test_the_switch_is_on_by_default_and_the_page_data_says_so(api):
    assert data(api.settings_get())["icons_download"] is True


def test_the_switch_saves_both_ways_tells_the_icon_service_and_is_free_while_locked(api, ctx):
    import antibypass as ab
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})
    assert data(api.set_icons_download(False)) == {"icons_download": False}
    assert ctx.db.get_setting("icons.download") == "0" and data(api.settings_get())["icons_download"] is False
    assert ctx.told == [False]
    assert data(api.set_icons_download(True)) == {"icons_download": True}
    assert ctx.db.get_setting("icons.download") == "1" and ctx.told == [False, True]


@pytest.mark.parametrize("bad", ["yes", "0", 1, 0, None, [], {}, 1.0])
def test_the_switch_wants_a_real_yes_or_no(api, ctx, bad):
    reply = api.set_icons_download(bad)
    assert not reply["ok"] and ctx.db.get_setting("icons.download") is None and ctx.told == []


def test_the_switch_is_a_free_ui_method_and_not_one_the_generic_writer_accepts(api, ctx):
    assert dict(webapi.api_methods())["set_icons_download"] == "ui"
    assert not core.ui_setting_allowed("icons.download")
    assert not api.ui_setting("icons.download", "0")["ok"] and ctx.db.get_setting("icons.download") is None


def test_the_switch_works_without_an_icon_service(tmp_path):
    c = webapi.make_context(tmp_path / "x.db", clock=lambda db: Clock.now)
    assert data(core.build_api(c).set_icons_download(False)) == {"icons_download": False}


def test_an_import_tells_the_icon_service_what_the_backup_says(api, ctx, tmp_path, monkeypatch):
    import json

    import backup
    monkeypatch.setattr(settings, "_ask_open_path", lambda window: str(tmp_path / "b.json"))
    exported = backup.export(ctx.db)
    exported["settings"] = {**exported.get("settings", {}), "icons.download": "0"}
    (tmp_path / "b.json").write_text(json.dumps(exported), encoding="utf-8")
    token = data(api.backup_import_pick())["token"]
    data(api.backup_import_apply(token))
    assert ctx.db.get_setting("icons.download") == "0" and ctx.told[-1] is False


def test_download_on_reads_the_setting_and_defaults_to_on(db):
    assert icons.download_on(db) is True
    db.set_setting("icons.download", "0")
    assert icons.download_on(db) is False
    db.set_setting("icons.download", "banana")
    assert icons.download_on(db) is True
    assert icons.download_on(object()) is True                      # (can't be read: the default)
