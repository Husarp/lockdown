from datetime import datetime, timedelta

import alerts
from blocker import hosts, protection
from db import Database

NOW = datetime(2026, 9, 19, 12, 0)


def test_parse_hosts_and_plain_lists():
    text = """# comment
0.0.0.0 bad.example.com  # trailing
127.0.0.1 localhost
0.0.0.0 0.0.0.0
0.0.0.0 a.test b.test
plain-list.example.org
not a domain!
BAD.EXAMPLE.COM
"""
    assert protection.parse(text) == ["bad.example.com", "a.test", "b.test", "plain-list.example.org"]


def test_due_daily_retry_and_update_now():
    cfg = {"info": {}}
    assert protection.due(cfg, "scam", NOW)
    cfg["info"]["scam"] = {"updated": (NOW - timedelta(hours=5)).isoformat()}
    assert not protection.due(cfg, "scam", NOW)
    assert protection.due(cfg, "scam", NOW + timedelta(hours=20))
    cfg["update_now"] = NOW.isoformat()
    assert protection.due(cfg, "scam", NOW + timedelta(minutes=1))
    cfg["info"]["scam"]["failed"] = (NOW + timedelta(minutes=1)).isoformat()   # failed: wait an hour
    assert not protection.due(cfg, "scam", NOW + timedelta(minutes=30))
    assert protection.due(cfg, "scam", NOW + timedelta(minutes=62))


def test_update_lists_exceptions_and_which(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    lists = {"scam": "0.0.0.0 fake-shop.com\n0.0.0.0 www.fake-shop.com\n", "adult": "adult.example\nok.example\n"}

    def fake_download(key, folder):
        if key not in lists:
            raise OSError("offline")
        folder.mkdir(parents=True, exist_ok=True)
        domains = protection.parse(lists[key])
        protection.list_path(key, folder).write_text("\n".join(domains))
        return len(domains)
    monkeypatch.setattr(protection, "download", fake_download)
    assert protection.update_due_lists(db, NOW, tmp_path)
    info = protection.settings(db)["info"]
    assert info["scam"]["count"] == 2 and "failed" in info["phishing"]
    assert not protection.update_due_lists(db, NOW + timedelta(minutes=5), tmp_path)   # nothing due
    cfg = protection.settings(db)
    cfg["allowed"] = ["ok.example"]
    protection.save_settings(db, cfg)
    p = protection.Protection(tmp_path)
    assert p.refresh(protection.settings(db))
    assert p.domains == ["adult.example", "fake-shop.com", "www.fake-shop.com"]
    assert p.which("www.fake-shop.com") == "scam" and p.which("ok.example") is None
    assert not p.refresh(protection.settings(db))                                    # nothing changed


def test_hosts_writes_list_domains_compactly():
    lines = hosts.build_lines([], ["youtube.com"], [f"d{i}.com" for i in range(10)])
    assert "127.0.0.1 youtube.com" in lines and "127.0.0.1 www.youtube.com" in lines
    assert "127.0.0.1 d0.com d1.com d2.com d3.com d4.com d5.com d6.com d7.com" in lines
    assert "127.0.0.1 d8.com d9.com" in lines


def test_message_names_the_list():
    event = {"display_name": "fake-shop.com", "reason": "protection:scam", "until": None}
    msg = alerts.format_message(alerts.DEFAULT_MESSAGES["protection"], event, NOW)
    assert msg == "fake-shop.com is blocked - it's on the scam list."
    assert alerts.base_reason("protection:scam") == "protection"
