from datetime import datetime, timedelta

import alerts
from blocker import protection
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
    assert protection.parse(text.splitlines()) == ["bad.example.com", "a.test", "b.test", "plain-list.example.org",
                                                   "bad.example.com"]


def test_due_daily_retry_and_update_now():
    cfg = {"info": {}}
    assert protection.due(cfg, "scam", NOW)
    cfg["info"]["scam"] = {"updated": (NOW - timedelta(hours=5)).isoformat()}
    assert protection.due(cfg, "scam", NOW)                                  # downloaded from other sources
    cfg["info"]["scam"]["sources"] = protection.sources("scam")
    assert not protection.due(cfg, "scam", NOW)
    assert protection.due(cfg, "scam", NOW + timedelta(hours=20))
    cfg["update_now"] = NOW.isoformat()
    assert protection.due(cfg, "scam", NOW + timedelta(minutes=1))
    cfg["info"]["scam"]["failed"] = (NOW + timedelta(minutes=1)).isoformat()   # failed: wait an hour
    assert not protection.due(cfg, "scam", NOW + timedelta(minutes=30))
    assert protection.due(cfg, "scam", NOW + timedelta(minutes=62))


def test_update_lists_exceptions_and_which(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    lists = {"scam": "fake-shop.com\nwww.fake-shop.com\n", "adult": "*.adult.example\nok.example\n"}
    seen_progress = []

    def fake_download(key, folder, progress=None, _lists=None):
        if key not in lists:
            raise OSError("offline")
        folder.mkdir(parents=True, exist_ok=True)
        protection.list_path(key, folder).write_text(lists[key])
        progress(1, 1, 50, 100)
        seen_progress.append(protection.download_progress(db))
        return lists[key].count("\n")
    monkeypatch.setattr(protection, "download", fake_download)
    assert protection.update_due_lists(db, NOW, tmp_path)
    assert seen_progress[0] == {"key": "scam", "part": 1, "parts": 1, "done": 50, "total": 100}
    assert protection.download_progress(db) is None                                  # cleared when done
    info = protection.settings(db)["info"]
    assert info["scam"]["count"] == 2 and "failed" in info["phishing"]
    assert not protection.update_due_lists(db, NOW + timedelta(minutes=5), tmp_path)   # nothing due
    cfg = protection.settings(db)
    cfg["allowed"] = ["ok.example"]
    p = protection.Protection(tmp_path)
    assert p.refresh(cfg) and p.count() == 4
    assert p.which("www.fake-shop.com") == "scam" and p.which("FAKE-SHOP.COM.") == "scam"
    assert p.which("sub.fake-shop.com") is None                                    # exact entry: not subdomains
    assert p.which("adult.example") == p.which("a.b.adult.example") == "adult"     # "*." entry: subdomains too
    assert p.which("ok.example") is None and p.which("example") is None
    assert not p.refresh(cfg)                                                        # nothing changed
    cfg["allowed"] = ["adult.example"]              # applies at once - and counts as a change, so the service
    assert p.refresh(cfg)                           # flushes the DNS cache instead of leaving it blocked there
    assert p.which("a.b.adult.example") is None
    cfg["enabled"] = ["scam"]
    assert p.refresh(cfg) and p.count() == 2
    assert protection.lists_with("x.adult.example", ["scam", "adult"], tmp_path) == ["adult"]
    assert protection.lists_with("fake-shop.com", ["scam", "adult"], tmp_path) == ["scam"]
    assert protection.lists_with("nothing.example", ["scam", "adult"], tmp_path) == []


def test_message_names_the_list():
    event = {"display_name": "fake-shop.com", "reason": "protection:scam", "until": None}
    msg = alerts.format_message(alerts.DEFAULT_MESSAGES["protection"], event, NOW)
    assert msg == "fake-shop.com is blocked - it's on the scam list."
    assert alerts.base_reason("protection:scam") == "protection"


def test_adblock_format_and_your_own_lists(tmp_path):
    text = ["! comment", "[Adblock Plus 2.0]", "||tracker.example^", "||ads.example^$third-party",
            "||site.example/path^", "*.wild.example", "plain.example", "0.0.0.0 hosts.example"]
    assert protection.parse_entries(text) == [("tracker.example", True), ("ads.example", True),
                                              ("wild.example", True), ("plain.example", False),
                                              ("hosts.example", False)]
    cfg = {"enabled": ["scam"], "allowed": [], "info": {},
           "custom": [{"key": "custom1", "name": "Crypto", "url": "https://example.org/list.txt"}]}
    lists = protection.all_lists(cfg)
    assert "scam" in lists and lists["custom1"][0] == "Crypto" and protection.new_custom_key(cfg) == "custom2"
    assert protection.due(cfg, "custom1", NOW)                                   # never downloaded
    event = {"display_name": "coin.example", "reason": "protection:custom1", "until": None}
    msg = alerts.format_message("{site} is blocked - it's {reason}.", event, NOW, lists)
    assert msg == "coin.example is blocked - it's on the crypto list."
    (tmp_path / "custom1.txt").write_text("*.coin.example\n")
    cfg["enabled"].append("custom1")
    p = protection.Protection(tmp_path)
    p.refresh(cfg)
    assert p.which("a.coin.example") == "custom1"


def test_your_own_hand_made_list(tmp_path):
    cfg = {"enabled": ["mine1"], "allowed": [], "info": {}, "custom": [],
           "manual": [{"key": "mine1", "name": "Distractions", "entries": [{"name": "Reddit", "host": "reddit.com"},
                                                                           {"name": "", "host": "tiktok.com"}]}]}
    lists = protection.all_lists(cfg)
    assert lists["mine1"][0] == "Distractions" and not lists["mine1"][2]      # no source to download
    assert not protection.due(cfg, "mine1", NOW)                             # never tries to download it
    assert protection.new_manual_key(cfg) == "mine2"
    p = protection.Protection(tmp_path)
    assert p.refresh(cfg) and p.count() == 4                                 # 2 hosts x (exact + wildcard)
    assert p.which("reddit.com") == "mine1" and p.which("old.reddit.com") == "mine1"   # domain + subdomains
    assert p.which("example.com") is None
    cfg["manual"][0]["entries"].pop()                                        # editing it reloads (content hash)
    assert p.refresh(cfg) and p.which("tiktok.com") is None


def test_update_interval_and_automatic_updates_off():
    info = {"scam": {"updated": NOW.isoformat(), "sources": protection.sources("scam")}}
    cfg = {"info": info, "auto": True, "every_hours": 6}
    assert not protection.due(cfg, "scam", NOW + timedelta(hours=5))
    assert protection.due(cfg, "scam", NOW + timedelta(hours=6))
    cfg["auto"] = False                                                   # only "Update now" (or never downloaded)
    assert not protection.due(cfg, "scam", NOW + timedelta(days=30))
    cfg["update_now"] = (NOW + timedelta(days=30)).isoformat()
    assert protection.due(cfg, "scam", NOW + timedelta(days=30, minutes=1))


def test_allowing_a_site_is_reported_so_the_service_can_flush_dns(tmp_path):
    """Allowing a site has to make refresh() say "something changed" - the service flushes the DNS cache on that,
    and without it a site you just allowed stays blocked for as long as clients cache the old answer."""
    (tmp_path / "adult.txt").write_text("badsite.com\n*.tracker.net\n", encoding="utf-8")
    p = protection.Protection(tmp_path)
    cfg = {"enabled": ["adult"], "allowed": [], "manual": [], "custom": []}
    assert p.refresh(cfg) is True                  # first load
    assert p.refresh(cfg) is False                 # nothing changed since
    assert p.which("badsite.com") == "adult"

    cfg["allowed"] = ["badsite.com"]
    assert p.refresh(cfg) is True                  # the allow list changed
    assert p.which("badsite.com") is None
    assert p.which("www.badsite.com") is None      # and its subdomains
    assert p.which("deep.badsite.com") is None
    assert p.which("x.tracker.net") == "adult"     # everything else stays blocked

    cfg["allowed"] = []                            # taking it back is a change too
    assert p.refresh(cfg) is True
    assert p.which("badsite.com") == "adult"
