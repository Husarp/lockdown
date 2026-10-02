"""End to end: does a game really get closed when its hours or its time limit say so? (0.84.1)

Adam played 1 h 30 m outside his game's allowed hours and nothing stopped it. These tests drive the REAL decision
path the way the running app does - the service's Enforcer.enforce_once (trusted clock -> db.blocks -> the app
blocks) and Enforcer.enforce_apps (the 4x-a-second process check), with the tray app's real UsageTracker.tick
doing the counting into the same database file through its own connection, and the window's connection (ui=True)
making the changes. Only Windows itself is faked: the process list, process paths / start times, TerminateProcess,
the hosts file, browser policies, the firewall and the time zone. Time is a controllable trusted clock (the real
TrustedClock, fed a fake tick counter), so "Friday 22:00" is exactly that for the service and the tray alike.
"""
import json
import logging
from datetime import datetime, timedelta

import pytest

import emergency
import modes
import rules
import service
from blocker import apps
from db import Database
from monitor import steam
from monitor import usage as usage_mod
from trusted_time import TrustedClock

EVERY_DAY = list(range(7))
FRIDAY = datetime(2026, 10, 2)          # a Friday
STEAM = r"D:\SteamLibrary\steamapps\common\Hollow Game"
BOOT = STEAM + r"\HollowGame.exe"                                   # Unreal's starter, what the picker used to pick
SHIPPING = STEAM + r"\HollowGame\Binaries\Win64\HollowGame-Win64-Shipping.exe"   # the game itself


def at(day: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return day.replace(hour=h, minute=m)


def allow(start: str, end: str, **extra) -> dict:
    return {"rule_type": "scheduled", "schedule": rules.make_schedule("allow", [(EVERY_DAY, start, end)]), **extra}


def block_during(start: str, end: str, **extra) -> dict:
    return {"rule_type": "scheduled", "schedule": rules.make_schedule("block", [(EVERY_DAY, start, end)]), **extra}


class World:
    """One PC: the service, the tray app's tracker and the window, sharing one database file."""

    def __init__(self, tmp_path, monkeypatch, start: datetime):
        self.path = tmp_path / "config.db"
        self.ts = start.timestamp()
        self.procs: list[tuple[int, int, str]] = []
        self.paths: dict[int, str] = {}
        self.started: dict[int, float] = {}
        self.killed: list[int] = []
        self.refuse: set[int] = set()     # processes Windows won't let us close (anti-cheat, another account)
        self.front: tuple[str | None, str | None] = (None, None)   # (exe, path) of the window in front
        self.front_pid: int | None = None
        self.files: set[str] = set()      # exe files on disk (besides the running ones), for learn_paths
        self.tray_running = True
        self._pid = 1000
        m = monkeypatch
        # --- Windows, faked ---
        m.setattr(service, "zone_name", lambda: "Test Standard Time")
        m.setattr(service.hosts, "apply", lambda names, *a, **k: False)
        m.setattr(service.hosts, "flush_dns", lambda: None)
        m.setattr(service.browser_policy, "apply", lambda **k: False)
        m.setattr(service.connections, "resolve", lambda names, *a, **k: set())
        m.setattr(service.connections, "close_to", lambda ips, *a, **k: [])
        m.setattr(service.netlog, "dns_names", lambda: {})
        m.setattr(service.firewall, "add", lambda exe, path: True)
        m.setattr(service.firewall, "remove", lambda exe: True)
        m.setattr(apps, "list_processes_full", lambda: list(self.procs))
        m.setattr(apps, "process_path", lambda pid: self.paths.get(pid))
        m.setattr(apps, "start_time", lambda pid: self.started.get(pid))
        m.setattr(apps, "terminate", self._terminate)
        m.setattr(apps, "exes_in", self._exes_in)
        m.setattr(apps, "is_file", lambda path: path.lower() in self._on_disk())
        m.setattr(service.time, "time", lambda: self.ts)
        # --- the trusted clock: the real TrustedClock, on a tick counter we move (no internet time) ---
        m.setattr(service, "TrustedClock",
                  lambda last=None: TrustedClock(last, ntp=lambda: None, tick=lambda: self.ts, system=lambda: self.ts))
        m.setattr(usage_mod, "now_from_db", lambda db: self.now)
        # --- the three programs ---
        self.gui = Database(self.path, ui=True)       # the window: where you change things
        self.tray_db = Database(self.path)            # the tray app's tracker
        self.enforcer = service.Enforcer(Database(self.path))   # the service
        self.enforcer.visit_db = Database(self.path)  # (its listener connection: block events for the tray)
        self.tracker = usage_mod.UsageTracker()

    @property
    def now(self) -> datetime:
        return datetime.fromtimestamp(self.ts)

    # ---------- processes ----------

    def launch(self, name: str, path: str | None = None, parent: int = 4) -> int:
        """Start a program, a couple of seconds after whatever happened last."""
        self.ts += 2
        self._pid += 1
        self.procs.append((self._pid, parent, name.lower()))
        if path:
            self.paths[self._pid] = path
        self.started[self._pid] = self.ts
        return self._pid

    def alive(self, pid: int) -> bool:
        return any(p == pid for p, _pp, _n in self.procs)

    def _terminate(self, pid: int) -> bool:
        if pid in self.refuse or not self.alive(pid):
            return False
        self.procs = [p for p in self.procs if p[0] != pid]
        self.killed.append(pid)
        return True

    def play(self, pid: int):
        """Bring this process's window to the front."""
        name = next(n for p, _pp, n in self.procs if p == pid)
        self.front = (name, self.paths.get(pid))
        self.front_pid = pid

    def _on_disk(self) -> dict[str, str]:
        return {p.lower(): p for p in [*self.files, *self.paths.values()]}

    def _exes_in(self, folder: str) -> dict[str, str]:
        out = {}
        for low, path in sorted(self._on_disk().items(), key=lambda kv: kv[0].count("\\")):
            if low.startswith(folder + "\\") and low.endswith(".exe"):
                out.setdefault(low.rsplit("\\", 1)[1], path)
        return out

    # ---------- time ----------

    def step(self, seconds: float = 2):
        """`seconds` pass; then the tray counts (one tick, however late) and the service does a pass."""
        self.ts += seconds
        if self.tray_running:
            exe, path = self.front if self.front[0] in {n for _p, _pp, n in self.procs} else (None, None)
            pid = self.front_pid if exe and self.alive(self.front_pid or 0) else None
            self.tracker.tick(self.tray_db, lambda: (exe, None, 0.0, path, pid),
                              running_exes=lambda: {n for _p, _pp, n in self.procs})
        self.enforcer.enforce_once()
        self.enforcer.enforce_apps()

    def run(self, minutes: float, every: float = 10):
        for _ in range(round(minutes * 60 / every)):
            self.step(every)

    def jump(self, to: datetime):
        """Time passes with the PC on but nothing to count (no game running), straight to `to`."""
        self.ts = to.timestamp() - 2
        self.front = (None, None)
        self.step(2)

    # ---------- looking ----------

    def reason(self, item_id: int) -> str | None:
        return next((b["reason"] for b in self.gui.blocks(self.now) if b["item"]["id"] == item_id), None)

    def used(self, item_id: int, period: str = "day") -> int:
        return self.gui.usage_lookup(self.now)(f"item:{item_id}", rules.time_bucket(period, self.now,
                                                                                    self.gui.limit_clock()))


@pytest.fixture
def world(tmp_path, monkeypatch):
    def make(start: datetime) -> World:
        return World(tmp_path, monkeypatch, start)
    return make


def add_game(w: World, rule_list, target="hollowgame.exe", path=BOOT, block_type="close") -> int:
    return w.gui.add_item("Hollow Game", [target], "app", rules=rule_list, block_type=block_type, app_path=path)


# ---------------------------------------------------------------- (a) allowed hours only

def test_allow_only_hours_game_closed_outside_kept_inside(world):
    w = world(at(FRIDAY, "17:00"))
    add_game(w, [allow("16:00", "20:00")])
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.run(60)                                         # 17:00-18:00: allowed
    assert w.alive(game) and w.killed == []
    w.jump(at(FRIDAY, "19:58"))
    w.run(1.9, every=2)                               # still allowed at 19:59:56
    assert w.alive(game)
    w.run(0.5, every=2)                               # 20:00: the hours end while it is open
    assert w.reason(1) == "schedule"
    w.run(10 / 60 + 0.05, every=2)                    # asked to close, force-closed after 10 s
    assert not w.alive(game)


def test_allow_only_hours_game_started_outside_is_closed_at_once(world):
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "20:00")])
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.step(1)
    assert w.killed == [game]


# ---------------------------------------------------------------- (b) blocked during a window

def test_block_during_window(world):
    w = world(at(FRIDAY, "20:30"))
    add_game(w, [block_during("21:00", "23:00")])
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.run(29, every=10)
    assert w.alive(game)                              # 20:59: not yet
    w.run(1 + 15 / 60, every=5)                       # 21:00 + grace
    assert not w.alive(game)
    again = w.launch("hollowgame.exe", BOOT)          # started again inside the window: closed at once
    w.step(1)
    assert not w.alive(again)
    w.jump(at(FRIDAY, "23:00"))
    after = w.launch("hollowgame.exe", BOOT)
    w.run(5)
    assert w.alive(after)


# ---------------------------------------------------------------- (c) time limits, counted by the real tracker

def test_daily_limit_counted_by_the_tracker_blocks_after_not_before(world):
    w = world(at(FRIDAY, "14:00"))
    item = add_game(w, [{"rule_type": "time_limit", "daily_limit_min": 60}])
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.play(game)
    w.run(59)
    assert w.alive(game) and w.reason(item) is None
    assert 59 * 60 - 20 <= w.used(item) <= 59 * 60 + 20
    w.run(1.5)
    assert w.reason(item) == "limit"
    assert not w.alive(game)
    w.launch("hollowgame.exe", BOOT)
    w.step(1)
    assert len(w.killed) == 2                         # and it can't be started again today


def test_weekly_limit(world):
    monday = FRIDAY - timedelta(days=4)
    w = world(at(monday, "18:00"))
    item = add_game(w, [{"rule_type": "time_limit", "weekly_limit_min": 120}])
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.play(game)
    w.run(70)                                         # Monday: 70 min
    w.killed.clear()
    w.procs.clear()
    w.jump(at(monday + timedelta(days=1), "18:00"))
    game = w.launch("hollowgame.exe", BOOT)
    w.play(game)
    w.run(45)                                         # Tuesday: 115 min this week
    assert w.alive(game) and w.reason(item) is None
    w.run(6)
    assert w.reason(item) == "limit" and not w.alive(game)
    w.jump(at(monday + timedelta(days=3), "18:00"))   # Thursday: still blocked, it's a weekly limit
    w.launch("hollowgame.exe", BOOT)
    w.step(1)
    assert len(w.killed) == 2
    w.jump(at(monday + timedelta(days=7), "00:01"))   # next Monday: a new week
    assert w.reason(item) is None


def test_slow_ticks_are_counted_as_the_time_that_really_passed(world):
    """The tracker ticks every 2 s - when the tray app is busy, much later. Each tick used to add a fixed 2 s,
    so with ticks 11 s apart two hours of play counted as ~21 minutes and the allowance never ran out."""
    w = world(at(FRIDAY, "21:00"))
    item = add_game(w, [allow("16:00", "18:00", allowance_min=15)])
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.play(game)
    w.run(14, every=11)
    assert w.alive(game) and w.reason(item) is None
    w.run(2, every=11)
    assert w.reason(item) == "schedule" and not w.alive(game)


# ---------------------------------------------------------------- (d) a limit day that starts at 04:00

def test_limit_reset_at_four_in_the_morning(world):
    w = world(at(FRIDAY, "23:00"))
    w.gui.set_setting(rules.RESET_KEY, json.dumps({"time": "04:00"}))
    item = add_game(w, [{"rule_type": "time_limit", "daily_limit_min": 60}])
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.play(game)
    w.run(61)                                         # 23:00 -> 00:01: used up
    assert not w.alive(game)
    w.jump(at(FRIDAY + timedelta(days=1), "03:50"))   # after midnight - but the limit day runs to 04:00
    assert w.reason(item) == "limit"
    w.launch("hollowgame.exe", BOOT)
    w.step(1)
    assert len(w.killed) == 2
    w.jump(at(FRIDAY + timedelta(days=1), "04:00"))
    assert w.reason(item) is None
    game = w.launch("hollowgame.exe", BOOT)
    w.run(1)
    assert w.alive(game)


# ---------------------------------------------------------------- (e) a group's rules

def test_group_hours_and_shared_limit_cover_the_game(world):
    w = world(at(FRIDAY, "22:00"))
    game_item = add_game(w, [])                       # no rules of its own: only its groups'
    other = w.gui.add_item("Other Game", ["other.exe"], "app", rules=[], block_type="close")
    w.gui.add_group("Games", [allow("16:00", "20:00")], {game_item: {}})
    w.gui.add_group("Shared", [{"rule_type": "time_limit", "daily_limit_min": 60}], {game_item: {}, other: {}})
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.step(1)
    assert w.killed == [game]                         # the group's hours close it

    w.jump(at(FRIDAY + timedelta(days=1), "16:00"))   # inside the group's hours: the shared hour applies
    a = w.launch("other.exe", r"C:\Games\Other\other.exe")
    w.play(a)
    w.run(40)
    assert w.alive(a)
    b = w.launch("hollowgame.exe", BOOT)
    w.play(b)
    w.run(21)                                         # 61 min between the two of them
    assert not w.alive(a) and not w.alive(b)


# ---------------------------------------------------------------- (f) a Steam game's other exes

def test_steam_game_running_as_another_exe_is_closed_and_counted(world):
    """Adam's case: allowed 16-18 plus 15 minutes during the blocked hours. The list says HollowGame.exe (the
    starter the picker chose); the window in front belongs to HollowGame-Win64-Shipping.exe. Its time was never
    counted (the allowance never ran out) and only the starter was closed - the game played on."""
    w = world(at(FRIDAY, "21:00"))
    item = add_game(w, [allow("16:00", "18:00", allowance_min=15)])     # "close" only, no background flag
    steam_client = w.launch("steam.exe", r"C:\Program Files (x86)\Steam\steam.exe")
    w.step()
    boot = w.launch("hollowgame.exe", BOOT, parent=steam_client)
    real = w.launch("hollowgame-win64-shipping.exe", SHIPPING, parent=boot)
    w.play(real)
    w.run(14)
    assert w.alive(real) and w.reason(item) is None       # on the allowance
    w.run(1.5)
    assert w.reason(item) == "schedule"
    w.run(0.5)
    assert not w.alive(real) and not w.alive(boot)
    assert w.alive(steam_client)                          # Steam itself is not the game


def test_steam_game_started_directly_without_the_starter_is_closed(world):
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "20:00")])
    w.step()
    real = w.launch("hollowgame-win64-shipping.exe", SHIPPING, parent=2)   # Steam starts the Shipping exe itself
    w.step(1)
    assert w.killed == [real]


def test_child_from_the_apps_own_folder_is_closed_others_are_not(world):
    """Not a Steam game: what the app starts from its own install folder is the app; anything else isn't."""
    w = world(at(FRIDAY, "22:00"))
    w.gui.add_item("Foo", ["foo.exe"], "app", rules=[allow("16:00", "20:00")], block_type="close",
                   app_path=r"C:\Games\Foo\Foo.exe")
    w.step()
    foo = w.launch("foo.exe", r"C:\Games\Foo\Foo.exe")
    child = w.launch("foo-win64-shipping.exe", r"C:\Games\Foo\Binaries\Foo-Win64-Shipping.exe", parent=foo)
    browser = w.launch("msedge.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", parent=foo)
    neighbour = w.launch("editor.exe", r"C:\Games\Foo\editor.exe")       # same folder, not started by it
    w.step(1)
    assert set(w.killed) == {foo, child}
    assert w.alive(browser) and w.alive(neighbour)
    w.step(1)                                         # the starter is gone: its child stays known as the app
    assert set(w.killed) == {foo, child}


def test_main_exe_prefers_unreals_shipping_exe(tmp_path):
    root = tmp_path / "steamapps" / "common" / "Hollow Game"
    (root / "HollowGame" / "Binaries" / "Win64").mkdir(parents=True)
    (root / "HollowGame.exe").write_bytes(b"x" * 3000)
    (root / "HollowGame" / "Binaries" / "Win64" / "HollowGame-Win64-Shipping.exe").write_bytes(b"x" * 9000)
    assert steam.main_exe(root, "Hollow Game").name == "HollowGame-Win64-Shipping.exe"
    pal = tmp_path / "Palworld"
    (pal / "Pal" / "Binaries" / "Win64").mkdir(parents=True)
    (pal / "Palworld.exe").write_bytes(b"x" * 3000)
    (pal / "Pal" / "Binaries" / "Win64" / "Palworld-Win64-Shipping.exe").write_bytes(b"x" * 9000)
    assert steam.main_exe(pal, "Palworld").name == "Palworld-Win64-Shipping.exe"


# ---------------------------------------------------------------- (g) names and paths written differently

@pytest.mark.parametrize("target", ["HollowGame.EXE", " hollowgame.exe ", r"D:\Games\Hollow\HollowGame.exe",
                                    '"D:\\Games\\Hollow\\HollowGame.exe"'])
def test_target_written_any_way_is_still_the_exe(world, target):
    """A full path typed into the box used to become the target as it was - no process is called that, so the
    game was never closed and its time never counted."""
    w = world(at(FRIDAY, "14:00"))
    item = add_game(w, [{"rule_type": "time_limit", "daily_limit_min": 30}], target=target, path=None)
    w.step()
    game = w.launch("hollowgame.exe", r"D:\Games\Hollow\HollowGame.exe")
    w.play(game)
    w.run(29)
    assert w.alive(game)
    assert w.used(item) >= 29 * 60 - 20
    w.run(1.5)
    assert not w.alive(game)


def test_steam_folder_matches_whatever_the_case_of_the_path(world):
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "20:00")], path=r"d:\steamlibrary\STEAMAPPS\Common\hollow game\HollowGame.exe")
    w.step()
    real = w.launch("HollowGame-Win64-Shipping.exe", SHIPPING.upper(), parent=2)
    w.step(1)
    assert w.killed == [real]


def test_exe_name_and_typed_path():
    assert apps.exe_name(r'"C:\Program Files\Foo Bar\Foo Bar.EXE" ') == "foo bar.exe"
    assert apps.exe_name("Game.exe") == "game.exe"
    assert apps.typed_path("game.exe") is None
    assert apps.typed_path(r'"C:\Games\Foo\foo.exe"') == r"C:\Games\Foo\foo.exe"


# ---------------------------------------------------------------- (h) modes never unblock

def _games_mode(w: World, schedule: str | None = None) -> str:
    """A mode of your own called Games that blocks nothing extra (a "games time" mode)."""
    ms = modes.load(w.gui)
    ms.append({"id": "custom1", "name": "Games", "categories": [], "items": [], "groups": [], "extra": [],
               "mute": False, "pomodoro": None, "schedule": schedule})
    modes.save(w.gui, ms)
    return "custom1"


@pytest.mark.parametrize("how", ["by hand", "until a time", "locked", "scheduled"])
@pytest.mark.parametrize("mode_id", ["work", "study", "focus", "dnd", "relax", "games"])
def test_no_mode_lets_the_game_run_outside_its_hours(world, how, mode_id):
    """Modes only ADD blocks (db.blocks): whichever is on, however it was started, the game's own hours still
    close it - a Pomodoro break and Relax included."""
    w = world(at(FRIDAY, "22:00"))
    item = add_game(w, [allow("16:00", "20:00")])
    if mode_id == "games":
        mode_id = _games_mode(w, rules.make_schedule("block", [(EVERY_DAY, "21:00", "23:59")])
                              if how == "scheduled" else None)
    elif how == "scheduled":
        ms = modes.load(w.gui)
        for m in ms:
            if m["id"] == mode_id:
                m["schedule"] = rules.make_schedule("block", [(EVERY_DAY, "21:00", "23:59")])
        modes.save(w.gui, ms)
    if how != "scheduled":
        until = None if how == "by hand" else w.now + timedelta(hours=1)
        modes.start(w.gui, mode_id, w.now - timedelta(minutes=30), until, locked=how == "locked")
    state = modes.active(w.gui, w.now)
    assert state and state["mode"]["id"] == mode_id
    w.step()
    assert w.reason(item) == "schedule"
    game = w.launch("hollowgame.exe", BOOT)
    w.step(1)
    assert w.killed == [game]


def test_a_mode_blocks_the_game_inside_its_allowed_hours(world):
    """...and on top of the game's own rules a mode can block it (blocklist items count as Distracting)."""
    w = world(at(FRIDAY, "17:00"))
    item = add_game(w, [allow("16:00", "20:00")])
    modes.start(w.gui, "work", w.now, w.now + timedelta(hours=1), locked=True)
    w.step()
    assert w.reason(item) == "mode"
    game = w.launch("hollowgame.exe", BOOT)
    w.step(1)
    assert w.killed == [game]


def test_pomodoro_break_does_not_unblock_the_games_own_hours(world):
    w = world(at(FRIDAY, "22:00"))
    item = add_game(w, [allow("16:00", "20:00")])
    modes.start(w.gui, "focus", w.now - timedelta(minutes=26), None)   # 25 min focus, then a 5 min break
    assert modes.active(w.gui, w.now)["phase"][0] == "break"
    w.step()
    assert w.reason(item) == "schedule"


# ---------------------------------------------------------------- (i) a disabled item counts, doesn't block

def test_disabled_item_counts_its_time_but_does_not_block(world):
    """0.79.3: disabling stops the blocking, not the clock - so switching it back on can't hand back a fresh
    limit."""
    w = world(at(FRIDAY, "14:00"))
    limit = [{"rule_type": "time_limit", "daily_limit_min": 30}]
    item = add_game(w, limit)
    w.gui.update_item(item, "Hollow Game", ["hollowgame.exe"], None, limit, "close", BOOT, disabled=True)
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.play(game)
    w.run(45)
    assert w.alive(game) and w.reason(item) is None
    assert w.used(item) >= 45 * 60 - 20                   # counted all along
    w.gui.update_item(item, "Hollow Game", ["hollowgame.exe"], None, limit, "close", BOOT, disabled=False)
    w.run(0.5, every=2)
    assert w.reason(item) == "limit"
    w.run(15 / 60, every=2)
    assert not w.alive(game)


# ---------------------------------------------------------------- (j) an emergency unlock ends

def test_emergency_unlock_ends_and_blocking_resumes(world):
    w = world(at(FRIDAY, "22:00"))
    item = add_game(w, [allow("16:00", "20:00")])
    until = emergency.unlock(w.gui, [{"id": item, "display_name": "Hollow Game"}], w.now)
    assert until == w.now + timedelta(minutes=20)
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.run(19.5)
    assert w.alive(game) and w.reason(item) is None
    w.run(0.5 + 12 / 60, every=2)                       # the unlock ends: asked to close, force-closed after 10 s
    assert w.reason(item) == "schedule"
    assert not w.alive(game)


# ---------------------------------------------------------------- (k) a change from the window, no restart

def test_service_sees_a_rule_added_in_the_window_without_a_restart(world):
    """0.84.0 caches the item list per connection; a change from another connection (the window) must reach
    the running service on its next pass."""
    w = world(at(FRIDAY, "22:00"))
    game = w.launch("hollowgame.exe", BOOT)
    w.run(1)
    assert w.alive(game)
    item = add_game(w, [allow("08:00", "23:00")])
    w.run(1)
    assert w.alive(game)                                 # on the list, allowed now
    w.gui.update_item(item, "Hollow Game", ["hollowgame.exe"], None, [allow("16:00", "20:00")], "close", BOOT)
    w.step(2)
    assert w.enforcer.app_blocks.get("hollowgame.exe")
    w.run(12 / 60, every=2)
    assert not w.alive(game)


def test_service_sees_a_raw_database_edit_without_a_restart(world):
    import sqlite3
    w = world(at(FRIDAY, "22:00"))
    item = add_game(w, [allow("08:00", "23:00")])
    game = w.launch("hollowgame.exe", BOOT)
    w.run(1)
    assert w.alive(game)
    with sqlite3.connect(w.path) as raw:
        raw.execute("UPDATE block_rules SET schedule = ? WHERE item_id = ?",
                    (allow("16:00", "20:00")["schedule"], item))
    w.run(15 / 60, every=2)
    assert not w.alive(game)


# ---------------------------------------------------------------- the tray app isn't counting

def test_without_the_tray_app_the_service_counts_and_the_limit_still_closes_the_game(world):
    """Tray app closed, crashed or stuck: nothing counted at all before - the limit never filled."""
    w = world(at(FRIDAY, "14:00"))
    item = add_game(w, [{"rule_type": "time_limit", "daily_limit_min": 30}])
    w.tray_running = False
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.run(29, every=2)
    assert w.alive(game)
    w.run(1.5, every=2)
    assert w.reason(item) == "limit" and not w.alive(game)


def test_without_the_tray_app_the_allowance_still_runs_out(world):
    w = world(at(FRIDAY, "21:00"))
    item = add_game(w, [allow("16:00", "18:00", allowance_min=15)])
    w.tray_running = False
    w.step()
    real = w.launch("hollowgame-win64-shipping.exe", SHIPPING, parent=2)
    w.run(16, every=2)
    assert w.reason(item) == "schedule" and not w.alive(real)


# ---------------------------------------------------------------- a game that can't be closed

def test_a_refused_close_is_logged_once(world, caplog):
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "20:00")])
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.refuse.add(game)
    with caplog.at_level(logging.WARNING, logger="lockdown.service"):
        w.run(1, every=2)
    refused = [r for r in caplog.records if "Couldn't close" in r.getMessage()]
    assert len(refused) == 1 and "hollowgame.exe" in refused[0].getMessage()
    w.refuse.clear()
    w.step(1)
    assert not w.alive(game)
