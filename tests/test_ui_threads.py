"""0.84.0 crash and stall fixes that outlived the old window: the tray is only touched when something changed (and its
menu is rebuilt on pystray's thread), `schtasks` is off the start-up path and re-created on every launch."""
import threading
import time
import types
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


# ---------- tray ----------

class FakeIcon:
    def __init__(self):
        self.calls = []

    def update_menu(self):
        self.calls.append("menu")

    def __setattr__(self, name, value):
        if name != "calls":
            self.calls.append(name)
        object.__setattr__(self, name, value)


def test_tray_is_only_touched_when_something_changed(monkeypatch):
    from gui import tray as traymod
    monkeypatch.setattr(traymod.icon_art, "tray_icon", lambda state: state)
    t = traymod.Tray(lambda: None, lambda: None)
    t.icon = FakeIcon()
    assert t.update(True, "3 sites/apps blocked")
    assert t.icon.calls == ["icon", "title", "menu"]
    t.icon.calls.clear()
    for _ in range(100):                         # the 3-second status poll, with nothing new
        assert not t.update(True, "3 sites/apps blocked")
    assert t.icon.calls == []
    assert t.update(False, "3 sites/apps blocked")    # service stopped: red icon, new title
    assert t.icon.calls == ["icon", "title", "menu"]
    t.icon.calls.clear()
    t.set_modes([("deep", "Deep work")], None)
    t.set_modes([("deep", "Deep work")], None)
    assert t.icon.calls == ["menu"]


# ---------- autostart ----------

def test_watchdog_task_is_rewritten_on_every_launch(monkeypatch):
    """Anti-bypass: a watchdog task the user disabled (`schtasks /Change /DISABLE`, no admin needed) or edited must
    be replaced by a fresh, enabled one on the next launch - so `/Create /F` runs every time, whatever exists."""
    import main
    runs, values = [], []
    monkeypatch.setattr(main.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(main.subprocess, "run",
                        lambda args, **k: runs.append(args) or types.SimpleNamespace(returncode=0, stderr=b""))
    monkeypatch.setattr(main.winreg, "SetValueEx", lambda key, name, *a: values.append((name, a[-1])), raising=False)
    for _ in range(3):
        main.register_autostart()
    assert len(runs) == 3
    for args in runs:
        assert args[:2] == ["schtasks", "/Create"] and "/F" in args and main.WATCHDOG_TASK in args
        assert "/Query" not in args
    assert [name for name, _ in values] == ["Lockdown"] * 3          # the Run value too, every launch


def test_autostart_failure_is_logged(monkeypatch, tmp_path):
    import main
    import paths
    log = tmp_path / "lockdown.log"
    monkeypatch.setattr(paths, "LOG_PATH", log)

    def fail():
        raise OSError("schtasks /Create failed (1)")
    monkeypatch.setattr(main, "register_autostart", fail)
    main.register_autostart_later()
    for _ in range(100):
        if log.exists() and "registration failed" in log.read_text(encoding="utf-8"):
            break
        time.sleep(0.02)
    assert "schtasks /Create failed" in log.read_text(encoding="utf-8")


def test_autostart_runs_off_the_ui_thread(monkeypatch):
    import main
    seen = threading.Event()
    where = []

    def register():
        where.append(threading.current_thread() is threading.main_thread())
        seen.set()
    monkeypatch.setattr(main, "register_autostart", register)
    main.register_autostart_later()
    assert seen.wait(5) and where == [False]


def test_main_does_not_register_autostart_synchronously():
    source = (SRC / "main.py").read_text(encoding="utf-8")
    entry = source[source.index("def run(argv"):]
    assert "register_autostart_later" in entry and "register_autostart()" not in entry


# ---------- coming up ----------

def test_upcoming_lists_starts_ends_and_limits(tmp_path):
    from db import Database
    from webapi.dashboard import upcoming
    from rules import make_schedule
    db = Database(tmp_path / "t.db")
    night = make_schedule("block", [(list(range(7)), "21:00", "07:00")])
    db.add_item("Discord", ["discord.exe"], "app", rules=[{"rule_type": "scheduled", "schedule": night}])
    db.add_item("Steam", ["steam.exe"], "app", rules=[{"rule_type": "permanent"}])
    now = datetime(2026, 10, 1, 20, 0)
    out = upcoming(now, db.list_items(), db.list_groups(), db.usage_lookup(now))
    assert [(e["kind"], e["when"], e["title"]) for e in out] == [("start", datetime(2026, 10, 1, 21, 0),
                                                                   "Discord blocked")]


def test_tray_menu_is_rebuilt_on_pystrays_thread_only_when_shown(monkeypatch):
    """Review (threading #2): update_menu() from the Tk thread destroyed the native menu while pystray's thread
    could be showing it. Now it only marks it; the rebuild happens in the click handler, on pystray's thread."""
    from gui import tray as traymod
    built, shown = [], []
    monkeypatch.setattr(traymod._Icon.__mro__[1], "_on_notify",          # (pystray's handler: shows the menu)
                        lambda self, w, l: shown.append(list(built)), raising=False)
    icon = traymod._Icon("t")
    icon._update_menu = lambda: built.append(threading.current_thread().name)
    icon.update_menu()
    icon.update_menu()
    assert built == [] and icon.menu_dirty                     # nothing touched from the caller's thread

    def pystray_thread():
        icon._on_notify(0, 0x205)                                # (WM_RBUTTONUP)
        icon._on_notify(0, 0x205)
    t = threading.Thread(target=pystray_thread, name="pystray")
    t.start()
    t.join()
    assert built == ["pystray"] and not icon.menu_dirty          # once, on its thread, before showing
    assert shown == [["pystray"], ["pystray"]]
    assert type(traymod.Tray(lambda: None, lambda: None).icon) is traymod._Icon
