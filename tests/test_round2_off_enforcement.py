"""Round 2, item 17: the global off switch suspends words and SafeSearch."""
import contextlib
from datetime import datetime

import antibypass
import keywords
import service
import uiautomation
import pytest
from db import Database
from monitor import browser_url, win, word_guard
from tests.test_app_limit_enforcement import World

NOW = datetime(2026, 10, 2, 12)


def test_global_off_removes_safe_browser_policies_and_on_restores_them(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, NOW)
    policies = []
    monkeypatch.setattr(service.browser_policy, "apply", lambda **kw: policies.append(kw) or False)
    original = keywords.settings(world.gui)
    world.step()
    antibypass.switch_off(world.gui, world.now)
    world.step()
    assert policies[-1] == {"safe_search": False, "youtube": False}
    assert keywords.settings(world.gui) == original
    antibypass.switch_on(world.gui)
    world.step()
    assert policies[-1] == {"safe_search": True, "youtube": True}


@pytest.mark.parametrize("blocked_by", ["words", "site"])
def test_global_off_stops_word_guard_and_on_restores_it(tmp_path, monkeypatch, blocked_by):
    # Change only the default database path; the worker opens its real Database.
    monkeypatch.setattr(Database.__init__, "__defaults__", (tmp_path / "config.db", False))
    db = Database()
    if blocked_by == "site":
        keywords.save(db, {**keywords.settings(db), "enabled": False})
        db.add_item("Google", ["google.com"], rules=[{"rule_type": "permanent"}], block_type="close")
    original = keywords.settings(db)
    monkeypatch.setattr(uiautomation, "UIAutomationInitializerInThread", contextlib.nullcontext, raising=False)
    monkeypatch.setattr(win, "foreground", lambda: (7, "chrome.exe"))
    monkeypatch.setattr(win, "window_title", lambda hwnd: "porn - Google Search")
    monkeypatch.setattr(browser_url, "browser_url", lambda hwnd: "https://www.google.com/search?q=porn")
    monkeypatch.setattr(browser_url, "tab_count", lambda hwnd: 2)
    pressed, notices = [], []
    monkeypatch.setattr(win, "chord", lambda *args: pressed.append(args) or True)
    guard = word_guard.WordGuard(lambda *args: notices.append(args))

    class Ticks:
        n = 0

        def wait(self, seconds):
            self.n += 1
            if self.n == 2:
                antibypass.switch_off(db, NOW)
            elif self.n == 3:
                assert len(pressed) == 1  # no second action while off
                antibypass.switch_on(db)
            return self.n > 3

    guard.stop_event = Ticks()
    # Let a missing off check act again rather than being hidden by the retry gap.
    monkeypatch.setattr(word_guard.time, "monotonic", lambda: guard.stop_event.n * 20.0)
    guard._check()
    assert len(pressed) == 2
    assert len(notices) == (2 if blocked_by == "words" else 0)
    assert keywords.settings(db) == original


def test_global_off_disables_dns_safe_targets_and_restores_them(tmp_path, monkeypatch):
    monkeypatch.setattr(Database.__init__, "__defaults__", (tmp_path / "config.db", False))
    db = Database()
    enforcer = service.Enforcer(db)
    snapshots = []

    class Finished(BaseException):
        pass

    class Server:
        def __init__(self, *args, safe):
            self.safe = safe

        def start(self):
            pass

    servers = []

    def server(*args, **kw):
        instance = Server(*args, **kw)
        servers.append(instance)
        return instance

    def sleep(seconds):
        snapshots.append((servers[0].safe("www.google.com"), servers[0].safe("www.youtube.com")))
        if len(snapshots) == 1:
            antibypass.switch_off(db, NOW)
            enforcer.was_off = True
        elif len(snapshots) == 2:
            antibypass.switch_on(db)
            enforcer.was_off = False
        else:
            raise Finished

    monkeypatch.setattr(service.dnsfilter, "Server", server)
    monkeypatch.setattr(service.dnsfilter, "point_to_filter", lambda *args: ["127.0.0.2"])
    monkeypatch.setattr(service.dnsfilter, "restore", lambda *args: None)
    monkeypatch.setattr(service.hosts, "flush_dns", lambda: None)
    monkeypatch.setattr(service.time, "sleep", sleep)
    try:
        service.dns_loop(enforcer)
    except Finished:
        pass
    safe = ("forcesafesearch.google.com", "restrictmoderate.youtube.com")
    assert snapshots == [safe, (None, None), safe]


def test_global_off_releases_dns_filter_even_with_loaded_protection_lists(tmp_path):
    enforcer = service.Enforcer(Database(tmp_path / "config.db"))
    safe = {"search": True, "youtube": True}
    assert service.filter_wanted(enforcer, safe)
    enforcer.was_off = True
    assert not service.filter_wanted(enforcer, safe)
