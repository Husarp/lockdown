import threading

import service
from blocker import apps


def make_enforcer(block_type="kill"):
    e = object.__new__(service.Enforcer)   # skip __init__ (trusted clock, database)
    e.visit_lock = threading.Lock()
    e.last_visit = {}
    e.app_first_seen = {}
    e.block_since = {}
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
    monkeypatch.setattr(apps, "start_time", lambda pid: 500.0)   # already open before the block began
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


def test_launched_while_blocked_is_killed_at_once(monkeypatch):
    clock = [1000.0]
    killed = []
    procs = []
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    monkeypatch.setattr(apps, "list_processes", lambda: procs)
    monkeypatch.setattr(apps, "terminate", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(apps, "start_time", lambda pid: 1030.0)
    e = make_enforcer()
    e.enforce_apps()                         # block begins at 1000, app not running
    clock[0] = 1030.2
    procs.append((20, "discord.exe"))        # launched at 1030
    e.enforce_apps()
    assert killed == [20] and e.events == ["discord.exe"]


def test_start_time_real():
    import os, time
    started = apps.start_time(os.getpid())
    assert started and 0 < time.time() - started < 3600


def test_minimize_apps_are_left_to_the_tray_agent(monkeypatch):
    monkeypatch.setattr(apps, "list_processes", lambda: [(10, "discord.exe")])
    e = make_enforcer("minimize")
    e.enforce_apps()
    assert e.events == []


def test_block_type_compose_split():
    for action in apps.ACTIONS:
        for internet in (False, True):
            bt = apps.compose_block_type(action, internet)
            got = apps.split_block_type(bt)
            assert got == (action, True if action == "internet" else internet)
    assert apps.split_block_type(None) == ("close", False)
