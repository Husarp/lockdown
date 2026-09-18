import threading

import service
from blocker import apps


def full(procs):
    return [(pid, 0, exe) for pid, exe in procs]


def make_enforcer(block_type="kill"):
    e = object.__new__(service.Enforcer)   # skip __init__ (trusted clock, database)
    e.visit_lock = threading.Lock()
    e.last_visit = {}
    e.app_first_seen = {}
    e.block_since = {}
    e.helpers = {}
    e.path_cache = {}
    e.events = []
    e.record_event = lambda key, block: e.events.append(key)
    item = {"id": 1, "display_name": "Discord", "target": "discord.exe", "block_type": block_type}
    e.app_blocks = {"discord.exe": {"item": item, "reason": "schedule", "until": None}}
    return e


def test_polite_then_force(monkeypatch):
    clock = [1000.0]
    killed = []
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    monkeypatch.setattr(apps, "list_processes_full", lambda: full([(10, "discord.exe"), (11, "discord.exe"), (12, "notepad.exe")]))
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
    monkeypatch.setattr(apps, "list_processes_full", lambda: full([(10, "discord.exe")]))
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
    monkeypatch.setattr(apps, "list_processes_full", lambda: full(procs))
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
    monkeypatch.setattr(apps, "list_processes_full", lambda: full([(10, "discord.exe")]))
    e = make_enforcer("minimize")
    e.enforce_apps()
    assert e.events == []


def test_block_flags():
    assert apps.block_flags(None) == {"close"}
    assert apps.block_flags("both") == {"close", "internet"}                 # older values still work
    assert apps.block_flags("minimize,internet") == {"minimize", "internet"}
    assert apps.make_block_type({"internet", "minimize"}) == "minimize,internet"
    assert apps.kills("close,internet") and apps.firewalls("close,internet") and not apps.minimizes("close")


def test_over_opening_limit_is_killed_at_once(monkeypatch):
    killed = []
    monkeypatch.setattr(apps, "list_processes_full", lambda: full([(30, "discord.exe")]))
    monkeypatch.setattr(apps, "terminate", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(apps, "start_time", lambda pid: 1.0)                # started before the block began
    e = make_enforcer()
    e.app_blocks["discord.exe"]["rule"] = {"rule_type": "switch_limit"}     # launches mode (default)
    e.enforce_apps()
    assert killed == [30]


def test_background_processes_closed_after_the_app(monkeypatch):
    killed = []
    procs = [(10, 1, "steam.exe"), (11, 10, "steamwebhelper.exe"), (12, 11, "steamwebhelper.exe"),
             (13, 1, "steamservice.exe"), (14, 1, "notepad.exe"), (15, 1, "explorer.exe")]
    paths = {13: r"C:\Program Files (x86)\Steam\bin\steamservice.exe", 14: r"C:\Windows\notepad.exe",
             15: r"C:\Program Files (x86)\Steam\explorer.exe"}
    monkeypatch.setattr(apps, "list_processes_full", lambda: procs)
    monkeypatch.setattr(apps, "terminate", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(apps, "process_path", lambda pid: paths.get(pid))
    monkeypatch.setattr(apps, "start_time", lambda pid: 1.0)
    e = make_enforcer("close,background")
    e.app_blocks = {"steam.exe": {"item": {"id": 1, "display_name": "Steam", "target": "steam.exe",
                                           "block_type": "close,background",
                                           "app_path": r"C:\Program Files (x86)\Steam\steam.exe"},
                                  "reason": "schedule", "until": None}}
    e.enforce_apps()
    assert killed == []                        # the app is asked to close first; helpers wait
    procs.pop(0)                               # the app closed
    e.enforce_apps()
    assert sorted(killed) == [11, 12, 13]      # its children + what runs from its folder (not Windows' own)


def test_background_flag_needs_close():
    assert apps.kills_background("close,background")
    assert not apps.kills_background("background,minimize")
    assert apps.helper_folder(r"C:\Windows\System32\notepad.exe") is None
