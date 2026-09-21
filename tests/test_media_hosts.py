"""A blocked site's video comes from somewhere else (youtube.com -> googlevideo.com). Blocking the page alone
left an open player downloading to the end, so the media domains are part of the site and the DNS filter
matches everything under them."""
from datetime import datetime

from db import Database
from importer.popular import MEDIA_HOSTS_KEY, add_media_hosts, hosts_of
from service import Enforcer

NOW = datetime(2026, 9, 21, 12, 0)


def test_the_known_sites_carry_their_media_hosts():
    assert "googlevideo.com" in hosts_of("youtube.com")
    assert "ttvnw.net" in hosts_of("twitch.tv")
    assert "nflxvideo.net" in hosts_of("netflix.com")
    assert hosts_of("example.com") is None


def _enforcer(tmp_path) -> Enforcer:
    db = Database(tmp_path / "t.db")
    e = Enforcer.__new__(Enforcer)          # no clock, no service: only the name check is under test
    e.db, e.dns_blocks, e.closing = db, {}, {}
    e.protection = type("P", (), {"which": staticmethod(lambda host: None)})()
    return e


def test_anything_under_a_blocked_name_is_blocked_too(tmp_path):
    e = _enforcer(tmp_path)
    e.dns_blocks = {"youtube.com": {}, "googlevideo.com": {}}
    assert e.blocked_name("youtube.com")
    assert e.blocked_name("www.youtube.com")
    assert e.blocked_name("rr1---sn-u2oxu-f5fed.googlevideo.com")   # the video itself
    assert e.blocked_name("GOOGLEVIDEO.COM.")                       # as a resolver hands it over
    assert not e.blocked_name("google.com")
    assert not e.blocked_name("notgooglevideo.com")


def test_nothing_is_blocked_when_nothing_is(tmp_path):
    e = _enforcer(tmp_path)
    assert e.blocked_name("youtube.com") is None


def test_connections_already_open_to_a_blocked_name_are_cut(tmp_path, monkeypatch):
    e = _enforcer(tmp_path)
    e.dns_blocks = {"googlevideo.com": {}}
    monkeypatch.setattr("blocker.netlog.dns_names",
                        lambda: {"1.2.3.4": "rr1---sn-x.googlevideo.com", "5.6.7.8": "example.com"})
    e.cut_live_connections(NOW)
    assert list(e.closing) == ["1.2.3.4"]


def test_the_sites_you_already_had_get_the_media_hosts_once(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_setting(MEDIA_HOSTS_KEY, "")       # as if the item had been added before they existed
    item = db.add_item("YouTube", ["youtube.com", "m.youtube.com", "youtu.be"], "site",
                       rules=[{"rule_type": "permanent"}])
    assert add_media_hosts(db) == ["YouTube"]
    assert "googlevideo.com" in db.list_items()[0]["target"].split()

    db.update_item(item, "YouTube", ["youtube.com"], None, [{"rule_type": "permanent"}])
    assert add_media_hosts(db) == []          # it has run: one you take off yourself stays off
    assert db.list_items()[0]["target"] == "youtube.com"


def test_the_dns_filter_answers_nothing_for_the_video_host(tmp_path):
    """End to end: the filter every lookup goes through says the video host does not exist."""
    from blocker import dnsfilter
    e = _enforcer(tmp_path)
    e.dns_blocks = {"youtube.com": {}, "googlevideo.com": {}}
    server = dnsfilter.Server(e.blocked_name, port=0)
    qid = bytes([0x12, 0x34])
    asked = dnsfilter.query("rr1---sn-u2oxu-f5fed.googlevideo.com", dnsfilter.TYPE_A, qid)
    reply = server.answer(asked)
    assert reply[:2] == qid                    # the same question
    assert reply[-4:] == bytes([127, 0, 0, 1])  # answered with "yourself", the way every block is
    other = server.answer(dnsfilter.query("example.com", dnsfilter.TYPE_A, qid))
    assert other[-4:] != bytes([127, 0, 0, 1])  # anything else is passed on, not blocked
