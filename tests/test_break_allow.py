"""Strict break: "Allowed during breaks" - apps on the list are not minimised and don't bring the break cover back.

Default list: Lockdown (all its programs) and Windows Settings (a Store app: found through its ApplicationFrameHost.exe
frame). Adding an app loosens the break (the challenge, decided in Python by both windows); removing is free. The list
lives in the break settings ("allow"); the minimise tick takes it from the status snapshot (no database read)."""
import json
import random
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import antibypass as ab
import reminders
import webapi
from monitor import win
from resident import snapshot, workers
from webapi import core
from webapi import settings as settings_api
from webapi.guard import GuardService
from db import Database

NOW = datetime(2026, 10, 11, 15, 42, 17)
LOCKDOWN_PATH = r"C:\Program Files\Lockdown\Lockdown.exe"
SETTINGS_PATH = r"C:\Windows\ImmersiveControlPanel\SystemSettings.exe"
FRAME_PATH = r"C:\Windows\System32\ApplicationFrameHost.exe"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


# ================================================================ the list itself (reminders.py)

def test_the_default_list_is_lockdown_and_windows_settings():
    assert reminders.allowed_apps(reminders.DEFAULT_BREAK) == ["lockdown.exe", "systemsettings.exe"]
    assert reminders.break_allow_name("lockdown.exe") == "Lockdown"
    assert reminders.break_allow_name("systemsettings.exe") == "Windows Settings"
    assert reminders.break_allow_name("discord.exe") == "discord.exe"


def test_lockdown_stands_for_everything_lockdown_runs_as():
    exes = reminders.allowed_exes(reminders.allowed_apps(reminders.DEFAULT_BREAK))
    # the window / agent, the challenge the uninstaller asks for, the uninstaller, the installer / updater, the service
    assert {"lockdown.exe", "uninstall lockdown.exe", "lockdownsetup.exe", "lockdownservice.exe",
            "systemsettings.exe"} <= exes
    assert "chrome.exe" not in exes
    assert "lockdown.exe" not in reminders.allowed_exes(["systemsettings.exe"])        # taken off: none of its programs


def test_settings_saved_before_the_list_existed_read_as_the_default(tmp_path):
    db = Database(tmp_path / "t.db")
    old = {k: v for k, v in reminders.DEFAULT_BREAK.items() if k != "allow"}
    db.set_setting(reminders.BREAK_KEY, json.dumps({**old, "strict": True, "length": 10}))
    b = reminders.load(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)
    assert reminders.allowed_apps(b) == ["lockdown.exe", "systemsettings.exe"] and b["length"] == 10
    db.set_setting(reminders.BREAK_KEY, json.dumps({"strict": True}))                    # (a bare, very old one)
    assert reminders.allowed_apps(reminders.load(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)) == \
        ["lockdown.exe", "systemsettings.exe"]
    db.set_setting(reminders.BREAK_KEY, "")
    assert reminders.allowed_apps(reminders.load(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)) == \
        ["lockdown.exe", "systemsettings.exe"]


def test_an_empty_list_is_a_choice_and_stays_empty(tmp_path):
    db = Database(tmp_path / "t.db")
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "allow": []})
    assert reminders.allowed_apps(reminders.load(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)) == []


def test_a_damaged_list_is_cleaned_not_trusted():
    assert reminders.allowed_apps({"allow": "lockdown.exe"}) == ["lockdown.exe", "systemsettings.exe"]   # not a list: default
    assert reminders.allowed_apps({"allow": None}) == ["lockdown.exe", "systemsettings.exe"]
    messy = [r"C:\Games\Foo.EXE", "foo.exe", 5, None, "", "game.jar", "..\\..\\x", "a" * 300 + ".exe", "ok app.exe", {}]
    assert reminders.allowed_apps({"allow": messy}) == ["foo.exe", "ok app.exe"]
    many = [f"app{i}.exe" for i in range(200)]
    assert len(reminders.allowed_apps({"allow": many})) == reminders.BREAK_ALLOW_MAX


@pytest.mark.parametrize("typed,exe", [("Discord.exe", "discord.exe"), (r'"C:\Apps\Foo\Bar.EXE"', "bar.exe"),
                                       ("C:/x/y.exe", "y.exe"), ("  notepad.exe ", "notepad.exe")])
def test_a_picked_or_typed_app_is_a_lower_case_program_name(typed, exe):
    assert reminders.break_allow_exe(typed) == exe


@pytest.mark.parametrize("bad", ["", "   ", None, "game.jar", "start.bat", "notes", "evil;rm.exe", "a<b.exe", "x" * 120 + ".exe"])
def test_things_that_are_not_programs_are_refused(bad):
    with pytest.raises(ValueError):
        reminders.break_allow_exe(bad)


# ================================================================ the window layer (monitor/win.py, mocked)

class FakeUser32:
    """GetForegroundWindow / IsIconic / ShowWindow / GetClassNameW / GetWindowTextW over a small desktop:
    hwnd -> {pid, cls, title, iconic}."""

    def __init__(self, windows, front):
        self.windows, self.front, self.minimized = windows, front, []

    def GetForegroundWindow(self):
        return self.front

    def IsIconic(self, hwnd):
        return int(self.windows[hwnd].get("iconic", False))

    def ShowWindow(self, hwnd, cmd):
        self.minimized.append(hwnd)

    def GetClassNameW(self, hwnd, buf, size):
        buf.value = self.windows[hwnd].get("cls", "Chrome_WidgetWin_1")

    def GetWindowTextW(self, hwnd, buf, size):
        buf.value = self.windows[hwnd].get("title", "")


PATHS = {1: LOCKDOWN_PATH, 2: CHROME_PATH, 3: FRAME_PATH, 4: SETTINGS_PATH, 5: r"C:\Program Files\Discord\Discord.exe",
         6: r"C:\Windows\System32\CalculatorApp.exe", 7: r"C:\Program Files\Lockdown\Uninstall Lockdown.exe"}


@pytest.fixture
def desktop(monkeypatch):
    """A desktop: 11 Lockdown, 12 Chrome, 13 the Settings frame (ApplicationFrameHost.exe pid 3, its content child owned
    by SystemSettings.exe pid 4), 14 the same kind of frame around Calculator, 15 Discord, 16 the uninstaller, 17 the
    break cover (owned by Lockdown). This process's own pid is not any of them."""
    windows = {11: {"pid": 1, "title": "Lockdown"}, 12: {"pid": 2, "title": "Google"},
               13: {"pid": 3, "cls": win.UWP_FRAME, "title": "Settings", "child": 40},
               14: {"pid": 3, "cls": win.UWP_FRAME, "title": "Calculator", "child": 60},
               15: {"pid": 5, "title": "Discord"}, 16: {"pid": 7, "title": "Uninstall Lockdown"},
               17: {"pid": 1, "title": "Lockdown overlay"}, 40: {"pid": 4}, 60: {"pid": 6}}
    fake = FakeUser32(windows, front=11)
    monkeypatch.setattr(win, "_user32", fake)
    monkeypatch.setattr(win, "window_pid", lambda hwnd: windows[hwnd]["pid"])
    monkeypatch.setattr(win, "pid_path", lambda pid: PATHS.get(pid, ""))
    monkeypatch.setattr(win, "snapshot_name", lambda pid: "")
    monkeypatch.setattr(win, "top_windows", lambda: [(h, w.get("title", ""), PATHS.get(w["pid"], ""))
                                                    for h, w in windows.items() if h < 20])
    monkeypatch.setattr(win, "_u32", SimpleNamespace(
        FindWindowExW=lambda hwnd, after, cls, title: windows[hwnd].get("child", 0)))
    monkeypatch.setattr(win.os, "getpid", lambda: 999)
    return fake


DEFAULT_EXES = reminders.allowed_exes(reminders.BREAK_ALLOW_DEFAULT)


def front(desktop, hwnd, allowed):
    desktop.front, desktop.minimized = hwnd, []
    return win.minimize_foreground(allowed), desktop.minimized


def test_the_default_list_leaves_lockdown_and_windows_settings_up(desktop):
    assert front(desktop, 11, DEFAULT_EXES) == (False, [])                  # Lockdown's window
    assert front(desktop, 16, DEFAULT_EXES) == (False, [])                  # the uninstaller
    assert front(desktop, 13, DEFAULT_EXES) == (False, [])                  # Settings: ApplicationFrameHost -> SystemSettings.exe
    assert win.window_exe(13) == "systemsettings.exe"                       # (the frame's own process is not what counts)


def test_everything_else_is_still_sent_down(desktop):
    assert front(desktop, 12, DEFAULT_EXES) == (True, [12])
    assert front(desktop, 15, DEFAULT_EXES) == (True, [15])
    assert front(desktop, 14, DEFAULT_EXES) == (True, [14])                 # another Store app in an ApplicationFrameHost frame
    assert win.window_exe(14) == "calculatorapp.exe"


def test_an_app_added_by_the_user_is_left_up(desktop):
    allowed = reminders.allowed_exes(reminders.allowed_apps({"allow": [*reminders.BREAK_ALLOW_DEFAULT, "discord.exe"]}))
    assert front(desktop, 15, allowed) == (False, [])
    assert front(desktop, 12, allowed) == (True, [12])


def test_a_default_taken_off_is_sent_down_like_any_app(desktop):
    no_settings = reminders.allowed_exes(["lockdown.exe"])
    assert front(desktop, 13, no_settings) == (True, [13])
    assert front(desktop, 11, no_settings) == (False, [])
    no_lockdown = reminders.allowed_exes(["systemsettings.exe"])
    assert front(desktop, 11, no_lockdown) == (True, [11])
    assert front(desktop, 16, no_lockdown) == (True, [16])


def test_the_break_cover_is_never_sent_down_even_with_lockdown_off_the_list(desktop):
    assert front(desktop, 17, frozenset()) == (False, [])                   # (it carries Emergency, and puts itself back)
    assert front(desktop, 11, frozenset()) == (True, [11])


def test_a_window_that_is_already_down_is_left_alone(desktop):
    desktop.windows[12]["iconic"] = True
    assert front(desktop, 12, DEFAULT_EXES) == (False, [])


def test_the_break_start_minimises_everything_except_the_list(desktop):
    desktop.minimized = []
    assert win.minimize_all(DEFAULT_EXES) == 3
    assert sorted(desktop.minimized) == [12, 14, 15]
    desktop.minimized = []
    allowed = reminders.allowed_exes(["systemsettings.exe", "discord.exe"])
    win.minimize_all(allowed)
    assert sorted(desktop.minimized) == [11, 12, 14, 16]                    # (not the cover, not Settings, not Discord)


def test_with_no_list_given_only_this_process_is_kept_as_before(desktop, monkeypatch):
    monkeypatch.setattr(win.os, "getpid", lambda: 1)
    assert front(desktop, 11, None) == (False, [])
    assert front(desktop, 12, None) == (True, [12])


# ================================================================ the minimise tick (no database reads)

class TickWin:
    """The win module as the worker uses it: the app in front, and the real rule for what a strict break sends down."""

    def __init__(self):
        self.exe, self.seen = "chrome.exe", []

    def minimize_foreground(self, allowed=None):
        self.seen.append(allowed)
        return self.exe not in allowed

    def foreground(self):
        return 0, ""


def tick_env(tmp_path, allow=None):
    db = Database(tmp_path / "t.db")
    if allow is not None:
        reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "allow": allow})
    snap = snapshot.build(db, NOW, wall=0.0)
    covers, notes, tw = [], [], TickWin()
    hub = SimpleNamespace(
        engine=SimpleNamespace(break_until=NOW + timedelta(minutes=5)), snapshot=snap,
        notify=lambda text, **kw: notes.append(text), usage=SimpleNamespace(limit_writes=0),
        clock=SimpleNamespace(now_with=lambda offset: NOW), status_worker=None, win=tw)
    ui = SimpleNamespace(break_blocked=lambda: covers.append(1))
    return db, hub, workers.MinimizeWorker(hub, None, reminder_ui=ui), tw, covers, notes


def test_the_snapshot_carries_the_list_the_default_before_any_choice(tmp_path):
    db = Database(tmp_path / "t.db")
    assert snapshot.build(db, NOW, wall=0.0).break_allow == DEFAULT_EXES
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "allow": ["discord.exe"]})
    assert snapshot.build(db, NOW, wall=0.0).break_allow == frozenset({"discord.exe"})
    assert snapshot.break_allowed(None) == DEFAULT_EXES and snapshot.break_allowed(SimpleNamespace()) == DEFAULT_EXES


def test_an_allowed_app_in_front_is_not_sent_down_and_does_not_bring_the_cover_back(tmp_path):
    _db, hub, worker, tw, covers, notes = tick_env(tmp_path)
    for exe in ("lockdown.exe", "systemsettings.exe", "uninstall lockdown.exe"):
        tw.exe = exe
        worker.tick()
    assert covers == [] and notes == []
    tw.exe = "chrome.exe"
    worker.tick()
    assert covers == [1] and notes == ["Break in progress - 5 min left."]


def test_a_user_added_app_is_allowed_and_a_removed_default_is_sent_down(tmp_path):
    _db, hub, worker, tw, covers, _notes = tick_env(tmp_path, allow=["lockdown.exe", "discord.exe"])
    tw.exe = "discord.exe"
    worker.tick()
    assert covers == []
    tw.exe = "systemsettings.exe"                                           # taken off the list
    worker.tick()
    assert covers == [1]


def test_the_tick_reads_the_list_from_the_snapshot_not_the_database(tmp_path):
    db, hub, worker, tw, covers, _notes = tick_env(tmp_path)
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "allow": []})   # changed after the snapshot
    tw.exe = "lockdown.exe"
    worker.tick()
    assert covers == [] and tw.seen[-1] == DEFAULT_EXES                    # (the next snapshot, a second later, has it)
    hub.snapshot = snapshot.build(db, NOW, wall=0.0)
    worker.tick()
    assert covers == [1] and tw.seen[-1] == frozenset()


def test_the_break_start_minimises_with_the_list_of_the_snapshot(tmp_path):
    from resident.reminder_ui import CornerReminderUI
    _db, hub, _worker, _tw, _covers, _notes = tick_env(tmp_path, allow=["discord.exe"])
    seen = []
    hub.win = SimpleNamespace(minimize_all=lambda allowed=None: seen.append(allowed))
    hub.corner, hub.overlay_handler = None, None
    ui = CornerReminderUI.__new__(CornerReminderUI)
    ui.hub, ui.engine, ui.worker, ui.overlay_handler = hub, None, None, None
    ui.break_start(NOW + timedelta(minutes=5))
    assert seen == [frozenset({"discord.exe"})]


# ================================================================ the new window's Api

@pytest.fixture
def ctx(tmp_path):
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    c.guard = GuardService(lambda: c.db, lambda db: NOW, rng=random.Random(3))
    return c


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def lock(ctx):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})


def allowed(ctx):
    return reminders.allowed_apps(reminders.load(ctx.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK))


def pass_challenge(ctx, api, ticket):
    begin = data(api.challenge_begin(ticket))
    mono = ctx.guard._mono
    ctx.guard._mono = lambda: mono() + 60
    return data(api.challenge_submit(ticket, begin["phrase"]))


def test_the_page_gets_the_list_with_names_and_what_is_missing(api, ctx):
    view = data(api.reminders_get())["break"]
    assert view["allow"] == [{"exe": "lockdown.exe", "name": "Lockdown"},
                             {"exe": "systemsettings.exe", "name": "Windows Settings"}]
    assert view["allow_missing"] == []
    data(api.break_allow_remove("systemsettings.exe"))
    view = data(api.reminders_get())["break"]
    assert [a["exe"] for a in view["allow"]] == ["lockdown.exe"] and view["allow_missing"] == ["systemsettings.exe"]


def test_classification():
    kinds = dict(webapi.api_methods())
    assert kinds["break_allow_add"] == "loosen" and kinds["break_allow_restore"] == "loosen"
    assert kinds["break_allow_remove"] == "tighten"


def test_adding_an_app_needs_the_challenge_when_it_is_on_and_python_stores_only_what_it_checked(api, ctx):
    lock(ctx)
    reply = data(api.break_allow_add(r"C:\Apps\Discord.EXE"))
    assert reply["needs_challenge"] and allowed(ctx) == ["lockdown.exe", "systemsettings.exe"]     # nothing yet
    out = pass_challenge(ctx, api, reply["ticket"])
    assert out["passed"] and allowed(ctx) == ["lockdown.exe", "systemsettings.exe", "discord.exe"]


def test_adding_an_app_is_immediate_when_no_challenge_is_set(api, ctx):
    assert data(api.break_allow_add("discord.exe")) == {"saved": True}
    assert allowed(ctx)[-1] == "discord.exe"
    assert data(api.break_allow_add("discord.exe")) == {"saved": True} and allowed(ctx).count("discord.exe") == 1


def test_removing_an_app_is_free_even_when_locked(api, ctx):
    lock(ctx)
    assert data(api.break_allow_remove("systemsettings.exe")) == {"saved": True}
    assert allowed(ctx) == ["lockdown.exe"]
    assert data(api.break_allow_remove("not-there.exe")) == {"saved": True} and allowed(ctx) == ["lockdown.exe"]
    data(api.break_allow_remove("lockdown.exe"))
    assert allowed(ctx) == []                                                # (and the list may be empty)


def test_bringing_a_default_back_is_a_loosening_and_asks(api, ctx):
    data(api.break_allow_remove("systemsettings.exe"))
    data(api.break_allow_remove("lockdown.exe"))
    lock(ctx)
    reply = data(api.break_allow_restore())
    assert reply["needs_challenge"] and allowed(ctx) == []
    pass_challenge(ctx, api, reply["ticket"])
    assert allowed(ctx) == ["lockdown.exe", "systemsettings.exe"]
    reply = data(api.break_allow_add("lockdown.exe"))                        # (already there: nothing to loosen)
    assert reply == {"saved": True}


def test_a_cancelled_or_never_passed_challenge_changes_nothing(api, ctx):
    lock(ctx)
    ticket = data(api.break_allow_add("discord.exe"))["ticket"]
    data(api.challenge_cancel(ticket))
    assert allowed(ctx) == ["lockdown.exe", "systemsettings.exe"]


def test_the_page_cannot_smuggle_a_bad_name_or_too_many(api, ctx):
    for bad in ("game.jar", "", "x;y.exe", 5, None, ["a.exe"], {"exe": "a.exe"}):
        assert not api.break_allow_add(bad)["ok"], bad
    assert allowed(ctx) == ["lockdown.exe", "systemsettings.exe"]
    for i in range(reminders.BREAK_ALLOW_MAX):
        api.break_allow_add(f"app{i}.exe")
    assert len(allowed(ctx)) == reminders.BREAK_ALLOW_MAX
    assert not api.break_allow_add("one-too-many.exe")["ok"]


def test_saving_the_break_card_keeps_the_list_and_the_page_cannot_change_it_that_way(api, ctx):
    data(api.break_allow_remove("systemsettings.exe"))
    cfg = {"on": True, "guarded": False, "every": "45 min", "snooze": "5 min", "length": 5, "strict": True,
           "max_snooze": 2, "twenty": False, "text": "", "twenty_text": ""}
    data(api.reminder_save_break({**cfg, "allow": ["evil.exe"]}))
    assert allowed(ctx) == ["lockdown.exe"]
    assert reminders.load(ctx.db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)["strict"] is True


# ================================================================ backups

def backup_of(break_setting):
    sets = {} if break_setting is None else {reminders.BREAK_KEY: break_setting}
    return {"lockdown": 2, "items": [], "groups": [], "categories": [], "settings": sets}


def test_an_old_backup_without_the_list_loads_and_reads_as_the_default(tmp_path):
    old = json.dumps({k: v for k, v in reminders.DEFAULT_BREAK.items() if k != "allow"})
    settings_api._check_shape(backup_of(old))
    settings_api._check_shape(backup_of(None))
    import backup
    db = Database(tmp_path / "t.db")
    backup.restore(db, backup_of(old))
    assert reminders.allowed_apps(reminders.load(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)) == \
        ["lockdown.exe", "systemsettings.exe"]


def test_a_backup_with_the_list_round_trips(tmp_path):
    import backup
    db = Database(tmp_path / "a.db")
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "allow": ["discord.exe"]})
    data_ = backup.export(db)
    settings_api._check_shape(data_)
    other = Database(tmp_path / "b.db")
    backup.restore(other, data_)
    assert reminders.allowed_apps(reminders.load(other, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)) == ["discord.exe"]


@pytest.mark.parametrize("allow", ["lockdown.exe", {"a": 1}, [1, 2], [["x.exe"]], ["a.exe"] * (reminders.BREAK_ALLOW_MAX + 1),
                                   ["x" * 300]])
def test_a_backup_with_a_damaged_list_is_refused_before_anything_changes(allow):
    with pytest.raises(webapi.core.ApiError):
        settings_api._check_shape(backup_of(json.dumps({"on": True, "allow": allow})))


def test_an_unreadable_break_setting_in_a_backup_is_not_a_reason_to_refuse_it():
    settings_api._check_shape(backup_of("{not json"))
    settings_api._check_shape(backup_of("[1, 2]"))


def test_the_cover_title_is_the_overlays():
    from resident import overlay
    assert win.COVER_TITLE == overlay.TITLE
