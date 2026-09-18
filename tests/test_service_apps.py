import threading

import service
from blocker import apps


def make_enforcer(block_type="kill"):
    e = object.__new__(service.Enforcer)   # skip __init__ (trusted clock, database)
    e.visit_lock = threading.Lock()
    e.last_visit = {}
    e.app_first_seen = {}
    e.events = []
    e.record_event = lambda key, block: e.events.append(key)
    item = {"id": 1, "display_name": "Discord", "target": "discord.exe", "block_type": block_type}
    e.app_blocks = {"discord.exe": {"item": item, "reason": "schedule", "until": None}}
    return e


def test_polite_then_force(monkeypatch):
    clock = [1000.0]
    killed = []
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    monkeypatch.setattr(apps, "list_processes", lambda: [(10, "discord.exe"), (11, "discord.exe"), (12, "notepad.exe")])
    monkeypatch.setattr(apps, "terminate", lambda pid: killed.append(pid) or True)
    e = make_enforcer()
    e.enforce_apps()
    assert e.events == ["discord.exe", "discord.exe"] and killed == []     # tray agent asked to close
    clock[0] += 5
    e.enforce_apps()
    assert killed == []                                                     # still within grace
    clock[0] += 6
    e.enforce_apps()
    assert killed == [10, 11]                                               # forced after 10 s


def test_firewall_only_apps_are_not_closed(monkeypatch):
    monkeypatch.setattr(apps, "list_processes", lambda: [(10, "discord.exe")])
    e = make_enforcer("firewall")
    e.enforce_apps()
    assert e.events == []


def test_list_processes_real():
    names = {name for _, name in apps.list_processes()}
    assert "python.exe" in names or "pythonw.exe" in names
