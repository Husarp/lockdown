"""Synthetic coverage for an old reset transition, a productive browser and DNS-only limits.

No user backup is read here. Runtime usage and browser state aren't part of a settings backup.
"""
import json
import socket
import types
from datetime import datetime, timedelta

import pytest

import keywords
import rules
import service
from blocker import dnsfilter
from db import Database
from monitor import usage, win
from resident.workers import Alerter
from test_app_limit_enforcement import World
from test_dnsfilter import query
from test_firefox_youtube import FIREFOX, firefox


START = datetime(2026, 9, 3, 2, 59, 58)
HOSTS = ["youtube.com", "m.youtube.com", "youtu.be", "googlevideo.com"]


def reset_config():
    # Ten days after a midnight -> 03:00 change; all transition holds have expired.
    return rules.change_reset(None, "03:00", datetime(2026, 8, 24, 12))


def test_expired_reset_transition_uses_natural_day_week_and_month(tmp_path):
    db = Database(tmp_path / "config.db")
    db.set_setting(rules.RESET_KEY, reset_config())
    saved, natural = db.limit_clock(), rules.LimitClock(json.dumps({"time": "03:00"}))
    assert saved.carry_until < START
    assert all(until < START for _key, until, _since in saved.holds.values())
    for offset in (0, 1, 2, 3, 86400, 86402):
        now = START + timedelta(seconds=offset)
        assert saved.day(now) == natural.day(now)
        for period in ("day", "week", "month"):
            assert saved.period(period, now) == natural.period(period, now)


@pytest.fixture
def pc(tmp_path, monkeypatch, firefox):
    apply_hosts = service.hosts.apply
    w = World(tmp_path, monkeypatch, START)
    w.tray_db.set_setting(rules.RESET_KEY, reset_config())
    w.youtube = w.tray_db.add_item("Video", HOSTS, "site", block_type="dns",
                                  rules=[{"rule_type": "time_limit", "daily_limit_min": 30}])
    w.other_site = w.tray_db.add_item("Other", ["other.example"], "site",
                                     rules=[{"rule_type": "time_limit", "daily_limit_min": 30}])
    w.other_app = w.tray_db.add_item("Game", ["game.exe"], "app",
                                    rules=[{"rule_type": "time_limit", "daily_limit_min": 30}])
    w.tray_db.set_category("app", "firefox.exe", "productive")
    old_alert = json.dumps({str(w.youtube): (START - timedelta(hours=8)).timestamp()})
    w.tray_db.set_setting(Alerter.KEY, old_alert)
    keywords.save(w.tray_db, {**keywords.settings(w.tray_db), "safesearch": True, "youtube": False})
    # Protect another name throughout the reset; lifting a personal limit must not lift protection.
    w.enforcer.protection = types.SimpleNamespace(which=lambda h: "scam" if h == "bad.example" else None)
    monkeypatch.setattr(usage.time, "monotonic", lambda: w.ts)
    front = {"exe": "firefox.exe"}
    monkeypatch.setattr(win, "foreground_process", lambda: (FIREFOX, front["exe"], ""))
    monkeypatch.setattr(win, "window_pid", lambda hwnd: 4242)
    monkeypatch.setattr(win, "session_locked", lambda: False)
    monkeypatch.setattr(win, "front_windows", lambda: [])
    hosts_file, backup_file = tmp_path / "hosts", tmp_path / "hosts.bak"
    hosts_file.write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(service.hosts, "_last", None)
    monkeypatch.setattr(service.hosts, "apply", lambda names: apply_hosts(names, hosts_file, backup_file))
    flushes, video_removed = [], []
    monkeypatch.setattr(service.hosts, "flush_dns", lambda: flushes.append(w.now))
    monkeypatch.setattr(service.firewall, "set_video_block", lambda ips: True)
    monkeypatch.setattr(service.firewall, "remove_video_block", lambda: video_removed.append(w.now))
    server = dnsfilter.Server(w.enforcer.blocked_name,
                              safe=lambda name: keywords.safe_target(name, True, False))
    monkeypatch.setattr(server, "_forward", lambda msg, tcp: b"upstream")

    def step(seconds):
        w.ts += seconds
        w.tracker.tick(w.tray_db, usage.sense_desktop, running_exes=lambda: {front["exe"]})
        w.enforcer.enforce_once()

    w.tracker.counted_ts = w.ts
    yield types.SimpleNamespace(w=w, browser=firefox, front=front, step=step, hosts=hosts_file,
                                dns=server, flushes=flushes, video_removed=video_removed, old_alert=old_alert)
    server.pool.shutdown()


@pytest.mark.parametrize("bar", ["readable", "fullscreen", "empty", "autoplay"])
def test_productive_firefox_counts_after_dns_limit_resets_with_old_settings(pc, bar):
    w = pc.w
    old_bucket = rules.time_bucket("day", w.now, w.gui.limit_clock())
    w.tray_db.add_usage([(f"item:{w.youtube}", old_bucket)], 1800, w.now.date())
    assert usage.sense_desktop()[1] == pc.browser.url
    w.enforcer.queue_cut("192.0.2.10", "rr1.googlevideo.com", w.now)
    w.enforcer.note_video("192.0.2.10", "rr1.googlevideo.com")
    w.enforcer.enforce_once()
    assert w.enforcer.blocked_name("www.youtube.com") == "blocked"
    assert pc.dns.answer(query("www.youtube.com")).endswith(socket.inet_aton("127.0.0.1"))
    assert "youtube.com" in pc.hosts.read_text()
    assert w.enforcer.video_ips and w.enforcer.closing
    assert len(pc.flushes) == 1

    if bar in {"fullscreen", "autoplay"}:
        pc.browser.full_screen = True
    elif bar == "empty":
        pc.browser.url = ""
    pc.step(2)  # 03:00:00: separate tray/service connections use the new limit day.
    assert w.used(w.youtube) == 2
    assert w.reason(w.youtube) is None
    assert w.enforcer.blocked_name("www.youtube.com") is None
    assert w.enforcer.blocked_name("rr1.googlevideo.com") is None
    assert pc.dns.answer(query("www.youtube.com")) == b"upstream"
    assert "youtube.com" not in pc.hosts.read_text()
    assert not w.enforcer.video_ips and not w.enforcer.closing
    assert pc.video_removed and len(pc.flushes) == 2
    assert w.enforcer.blocked_name("bad.example") == "scam"
    # Ordinary and passive/fullscreen use keep counting, including after cache expiry.
    for _ in range(59):
        if bar == "autoplay":
            pc.browser.title = f"Clip {_} - YouTube — Mozilla Firefox"
        pc.step(30)
    pc.step(28)
    assert w.used(w.youtube) == 1800
    assert w.reason(w.youtube) == "limit"
    assert w.enforcer.blocked_name("www.youtube.com") == "blocked"
    assert w.tray_db.get_setting(Alerter.KEY) == pc.old_alert
    assert w.tray_db.usage_of([(f"item:{w.youtube}", old_bucket)])[(f"item:{w.youtube}", old_bucket)] == 1800


def test_other_sites_and_apps_count_with_the_same_reset_and_notification_state(pc):
    w = pc.w
    pc.step(2)
    pc.browser.url = "https://other.example/"
    pc.browser.title = "Other — Mozilla Firefox"
    pc.step(30)
    w.tracker.flush(w.tray_db)
    assert w.used(w.other_site) == 30
    assert w.used(w.youtube) == 2
    pc.front["exe"] = "game.exe"
    pc.step(30)
    w.tracker.flush(w.tray_db)
    assert w.used(w.other_app) == 30
    assert w.used(w.youtube) == 2
