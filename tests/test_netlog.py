from datetime import datetime

import service
from blocker import apps, netlog
from db import Database

NOW = datetime(2026, 9, 19, 12, 0)


def test_names_prefer_what_you_asked_for():
    answers = {"youtube-ui.l.google.com": (["1.2.3.4"], []),
               "www.youtube.com": (["1.2.3.4"], ["youtube-ui.l.google.com"]),
               "github.com": (["5.6.7.8"], [])}
    assert netlog.names_for_ips(answers) == {"1.2.3.4": "www.youtube.com", "5.6.7.8": "github.com"}


def test_local_addresses():
    assert netlog.is_local("192.168.1.10") and netlog.is_local("127.0.0.1") and netlog.is_local("::1")
    assert netlog.is_local("::ffff:10.0.0.2") and not netlog.is_local("142.250.74.14")


def test_real_tables_do_not_crash():
    assert isinstance(netlog.tcp_connections(), list)
    assert isinstance(netlog.dns_names(), dict)


def test_logger_counts_new_connections(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    conns = [(10, "1.2.3.4", 443, 50001), (10, "1.2.3.4", 443, 50002), (20, "192.168.1.1", 80, 50003)]
    monkeypatch.setattr(netlog, "tcp_connections", lambda: list(conns))
    monkeypatch.setattr(netlog, "dns_names", lambda: {"1.2.3.4": "www.youtube.com"})
    monkeypatch.setattr(apps, "list_processes", lambda: [(10, "chrome.exe"), (20, "svchost.exe")])
    monkeypatch.setattr(apps, "process_path", lambda pid: {10: r"C:\Program Files\Google\chrome.exe",
                                                          20: r"C:\Windows\System32\svchost.exe"}[pid])
    now = [NOW]
    logger = service.NetworkLogger(db, lambda: now[0])
    logger.tick()
    logger.tick()   # same connections again: not counted twice
    rows = {r["exe"]: r for r in db.network_since("2026-09-19 00:00")}
    assert rows["chrome.exe"]["domain"] == "www.youtube.com" and rows["chrome.exe"]["count"] == 2   # two tabs
    assert rows["chrome.exe"]["windows"] == 0 and rows["svchost.exe"]["windows"] == 1
    assert rows["svchost.exe"]["local"] == 1
    conns.append((10, "1.2.3.4", 8443, 50004))
    logger.tick()
    assert len(db.network_since("2026-09-19 00:00")) == 3
    now[0] = datetime(2026, 9, 19, 13, 30)   # an hour and a half later: old rows are gone
    logger.tick()
    assert db.network_since("2026-09-19 00:00") == []


def test_site_to_block():
    from gui.network import site_to_block
    assert site_to_block("rr3---sn-4g5e.googlevideo.com") == "googlevideo.com"
    assert site_to_block("www.bbc.co.uk") == "bbc.co.uk"
    assert site_to_block("www.youtube.com") == "youtube.com"
