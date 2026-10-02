"""0.84.8: "Pause my blocks" - Adam asked for a way to unlock ALL his own blockers for a while he picks.

Every site and app blocked by his items, groups, modes and categories - hours, time limits, opening limits,
temporary and permanent blocks - stops blocking until the pause ends, then everything is blocked again by itself.
The protection lists, the blocked words and SafeSearch are not part of it. Time keeps counting toward the limits.
Starting it needs the Anti-Bypass challenge; resuming early is free. Optionally every notification is silenced for
the same while. It is kept in trusted time in the database: the service and the tray app both honour it, it
survives a restart, ends on the dot, and a clock change can't stretch it. It is neither the whole-app off switch
nor an emergency unlock (no use is spent).
"""
import types
from datetime import datetime, timedelta

import pytest

import alerts
import antibypass
import emergency
import keywords
import modes
import pause
import reminders
import service
from db import Database
from gui import icon_art
from tests.test_app_limit_enforcement import BOOT, FRIDAY, add_game, allow, at, world  # noqa: F401 (fixture)
from rules import block_targets, effective_rules
from tests.test_reminders import FakeUI, run

NOW = datetime(2026, 10, 2, 22, 0)   # a Friday evening


def everything(w):
    """One of each: hours, a time limit used up, an opening limit used up, a temporary block, a permanent site,
    a group, a category and a mode."""
    gui = w.gui
    ids = {"hours": add_game(w, [allow("16:00", "20:00")])}
    ids["limit"] = gui.add_item("Limited", ["limited.exe"], "app", rules=[{"rule_type": "time_limit",
                                                                           "daily_limit_min": 1}],
                                block_type="close")
    ids["openings"] = gui.add_item("Opened", ["opened.com"], "site", rules=[{"rule_type": "switch_limit",
                                                                             "daily_switch_limit": 1}])
    ids["temporary"] = gui.add_item("Temp", ["temp.com"], "site", rules=[{
        "rule_type": "temporary", "temp_until": (w.now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")}])
    ids["permanent"] = gui.add_item("Forever", ["forever.com"], "site", rules=[{"rule_type": "permanent"}])
    ids["member"] = gui.add_item("Member", ["member.com"], "site")
    gui.add_group("Night", [allow("08:00", "09:00")], {ids["member"]: {}})
    gui.set_category("site", "catsite.com", "streaming")
    ids["category"] = gui.add_item("Streaming", ["streaming"], "category", rules=[{"rule_type": "permanent"}])
    gui.set_category("app", "discord.exe", "distracting")
    modes.start(gui, "work", w.now, None, locked=False)         # blocks Distracting: discord.exe (not on the list)
    clock = gui.limit_clock()
    groups = gui.list_groups()
    for item in gui.list_items():                               # the limit and the openings: used up
        if item["id"] in (ids["limit"], ids["openings"]):
            used = block_targets(effective_rules(item, groups), w.now, clock)
            gui.add_usage(used, 3600, w.now.date())
    return ids


def blocked(w) -> set[str]:
    return {b["item"]["target"] for b in w.gui.blocks(w.now)}


ALL = {"hollowgame.exe", "limited.exe", "opened.com", "temp.com", "forever.com", "member.com", "streaming",
       "catsite.com", "discord.exe"}


# ---------------------------------------------------------------- what it lifts, and that it comes back

def test_every_block_of_yours_is_lifted_and_comes_back_on_time(world):
    w = world(at(FRIDAY, "22:00"))
    everything(w)
    w.step()
    assert blocked(w) == ALL
    until = pause.start(w.gui, w.now, 60, silent=False)
    assert until == w.now + timedelta(hours=1)
    w.step()
    assert blocked(w) == set()
    assert w.enforcer.blocks == {} and w.enforcer.app_blocks == {}   # the service: hosts file, apps, firewall
    game = w.launch("hollowgame.exe", BOOT)
    discord = w.launch("discord.exe")
    w.run(59.5)
    assert w.alive(game) and w.alive(discord) and w.killed == []
    w.run(0.5 + 12 / 60, every=2)                                   # 23:00: all back, the apps closed
    assert blocked(w) == ALL
    assert not w.alive(game) and not w.alive(discord)


def test_time_keeps_counting_during_the_pause(world):
    w = world(at(FRIDAY, "14:00"))
    item = add_game(w, [{"rule_type": "time_limit", "daily_limit_min": 30}])
    w.step()
    pause.start(w.gui, w.now, 60, silent=False)
    game = w.launch("hollowgame.exe", BOOT)
    w.play(game)
    w.run(45)                                                       # 45 min played, limit 30: paused, not blocked
    assert w.alive(game) and w.reason(item) is None
    assert w.used(item) >= 44 * 60
    w.run(16, every=2)                                              # the pause ends: the limit is used up
    assert w.reason(item) == "limit" and not w.alive(game)


def test_the_protection_lists_words_and_safesearch_are_untouched(world, monkeypatch):
    w = world(at(FRIDAY, "22:00"))
    w.gui.add_item("Mine", ["mine.com"], "site", rules=[{"rule_type": "permanent"}])
    w.enforcer.protection.which = lambda host: "adult" if host == "bad.example" else None
    keywords.save(w.gui, {**keywords.settings(w.gui), "words": ["casino"]})
    policies = []
    monkeypatch.setattr(service.browser_policy, "apply", lambda **k: policies.append(k) or False)
    before = keywords.settings(w.gui)
    pause.start(w.gui, w.now, 240, silent=True)
    w.step()
    assert w.enforcer.blocked_name("mine.com") is None             # yours: lifted
    assert w.enforcer.blocked_name("bad.example") == "adult"       # the protection list: still blocked
    assert keywords.settings(w.gui) == before                      # the words: as they were
    assert keywords.find("https://x.com/casino", "", before) == "casino"
    assert policies[-1] == {"safe_search": before["safesearch"], "youtube": before["youtube"]}


def test_it_is_not_the_off_switch_and_spends_no_emergency(world):
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "20:00")])
    left = emergency.uses_left(w.gui, w.now)[0]
    pause.start(w.gui, w.now, 30, silent=False)
    w.step()
    assert not antibypass.is_off(w.gui) and not w.enforcer.was_off
    assert emergency.uses_left(w.gui, w.now)[0] == left
    assert w.gui.unlocks_since(w.now - timedelta(days=1)) == []


def test_resuming_early_blocks_again_at_once(world):
    w = world(at(FRIDAY, "22:00"))
    item = add_game(w, [allow("16:00", "20:00")])
    pause.start(w.gui, w.now, 240, silent=False)
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.run(10)
    assert w.alive(game)
    pause.resume(w.gui)
    w.run(12 / 60, every=2)                                         # next pass: asked to close, closed after 10 s
    assert w.reason(item) == "schedule" and not w.alive(game)


def test_a_restart_in_the_middle_keeps_the_pause_and_its_end(world):
    w = world(at(FRIDAY, "22:00"))
    item = add_game(w, [allow("16:00", "20:00")])
    pause.start(w.gui, w.now, 60, silent=True)
    w.run(20)
    # the PC restarts: a new service, tray and window, the same database
    w.enforcer = service.Enforcer(Database(w.path))
    w.enforcer.visit_db = Database(w.path)
    w.gui, w.tray_db = Database(w.path, ui=True), Database(w.path)
    w.step()
    assert w.reason(item) is None and pause.silent_until(w.gui, w.now) == at(FRIDAY, "23:00")
    w.run(39.5)
    assert w.reason(item) is None
    w.run(0.5, every=2)
    assert w.reason(item) == "schedule"


def test_a_clock_change_cannot_stretch_it(world):
    """The pause is kept in trusted time. Windows' clock put back an hour changes nothing for the service (its own
    clock), and a time that reads earlier than when it began does not count as paused at all."""
    w = world(at(FRIDAY, "22:00"))
    item = add_game(w, [allow("16:00", "20:00")])
    start = w.now
    pause.start(w.gui, start, 30, silent=False)
    w.enforcer.clock._system = lambda: w.ts - 3600                  # Windows' clock set back an hour
    w.run(30.2, every=2)
    assert w.reason(item) == "schedule"                             # over at 22:30 trusted time all the same
    assert w.enforcer.blocks or w.enforcer.app_blocks
    assert pause.state(w.gui, start - timedelta(minutes=10)) is None    # read before it began: not paused
    assert pause.state(w.gui, start + timedelta(minutes=29)) is not None
    assert pause.state(w.gui, start + timedelta(minutes=30)) is None    # ends exactly on time


def test_a_pause_can_never_run_longer_than_a_day(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_setting(pause.KEY, '{"started": "2026-10-02 22:00:00", "until": "2026-10-09 22:00:00", "silent": true}')
    assert pause.state(db, NOW + timedelta(hours=1)) is None
    db.set_setting(pause.KEY, '{"started": "2026-10-02 22:00:00", "until": "2026-10-03 23:00:00", "silent": true}')
    assert pause.state(db, NOW + timedelta(hours=1)) is None        # 25 h written by hand: not a pause at all


def test_rest_of_the_day_is_never_a_two_day_pause(tmp_path):
    """Review fix: "Rest of the day" ran to the end of the running limit day - and changing the reset time (no
    challenge needed) stretches that day by up to two days. Reset moved 00:00 -> 23:00 at 22:00 kept the day
    running until tomorrow 23:00, so "Rest of the day" paused everything for 25 h (at 00:01 -> 23:59: 48 h).
    Now it ends at the next reset time - here 23:00 tonight - always within a day."""
    from rules import RESET_KEY, change_reset
    db = Database(tmp_path / "t.db")
    db.set_setting(RESET_KEY, change_reset(None, "23:00", NOW))
    assert db.limit_clock().day(NOW)[1] == datetime(2026, 10, 3, 23, 0)   # the stretched limit day
    assert pause.start(db, NOW, None, False) == datetime(2026, 10, 2, 23, 0)
    db.set_setting(RESET_KEY, change_reset(None, "21:00", NOW))
    assert pause.start(db, NOW, None, False) == datetime(2026, 10, 3, 21, 0)
    assert pause.state(db, NOW + timedelta(hours=22, minutes=59)) is not None


def test_a_backup_neither_carries_a_pause_in_nor_ends_one(tmp_path):
    """Review fix: the running pause is machine state like the mode that is on - not exported, not imported."""
    import backup
    db = Database(tmp_path / "t.db")
    pause.start(db, NOW, 60, False)
    data = backup.export(db)
    assert pause.KEY not in data["settings"]
    data["settings"][pause.KEY] = ""
    backup.restore(db, data)
    assert pause.state(db, NOW) is not None


@pytest.mark.parametrize("label,end", [("30 min", datetime(2026, 10, 2, 22, 30)), ("1 h", datetime(2026, 10, 2, 23)),
                                       ("2 h", datetime(2026, 10, 3, 0)), ("4 h", datetime(2026, 10, 3, 2)),
                                       ("Rest of the day", datetime(2026, 10, 3, 0))])
def test_the_lengths(tmp_path, label, end):
    db = Database(tmp_path / "t.db")
    assert pause.start(db, NOW, pause.DURATIONS[label], False) == end


def test_rest_of_the_day_ends_when_the_limit_day_resets(tmp_path):
    from rules import RESET_KEY, change_reset
    db = Database(tmp_path / "t.db")
    db.set_setting(RESET_KEY, change_reset(None, "04:00", NOW - timedelta(days=3)))
    assert pause.start(db, NOW, None, False) == datetime(2026, 10, 3, 4, 0)


# ---------------------------------------------------------------- the warning before it ends

def test_a_warning_says_your_blocks_are_back_on_soon(tmp_path):
    db = Database(tmp_path / "t.db")
    item = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "permanent"}])
    pause.start(db, NOW, 30, silent=False)
    watcher = alerts.BlockWatcher()
    settings = {k: alerts.DEFAULTS[k] for k in alerts.DEFAULTS}
    at_ = NOW + timedelta(minutes=26)
    out = watcher.check(db.list_items(), db.list_groups(), db.usage_lookup(at_), at_, {item}, settings)
    assert out == ["Your blocks are back on in 4 min (22:30): Game will be blocked again."]


# ---------------------------------------------------------------- the challenge, and resuming

def fake_app(db, now=NOW):
    from gui import app as appmod
    me = types.SimpleNamespace(db=db, asked=[], refreshed=0)
    me.refresh_pause = lambda: setattr(me, "refreshed", me.refreshed + 1)
    for name in ("pause_blocks", "resume_blocks", "guard"):
        setattr(me, name, types.MethodType(getattr(appmod.LockdownApp, name), me))
    return me, appmod


def test_starting_it_needs_the_challenge_resuming_never_does(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    me, appmod = fake_app(db)
    monkeypatch.setattr(appmod, "now_from_db", lambda d: NOW)
    shown = []

    class Challenge:
        def __init__(self, app, changes, proceed, cancel):
            shown.append(changes)
            self.proceed = proceed

        def __getattr__(self, name):
            return lambda *a, **k: None
    monkeypatch.setattr(appmod, "ChallengeWindow", Challenge)
    me.challenge, me.deiconify = None, lambda: None
    antibypass.save(db, {**antibypass.settings(db), "phrase": True})
    done = []
    me.pause_blocks(60, True, lambda: done.append(1))
    assert len(shown) == 1 and "Pause all your blocks for 1 h (until 23:00)" in shown[0][0]
    assert "silence all notifications" in shown[0][0]
    assert pause.state(db, NOW) is None and not done                # nothing yet: the phrase isn't typed
    me.challenge.proceed()                                          # passed
    assert pause.state(db, NOW) == {"started": NOW, "until": NOW + timedelta(hours=1), "silent": True}
    assert done == [1]
    me.challenge = None
    me.resume_blocks()                                              # free: no challenge
    assert len(shown) == 1 and pause.state(db, NOW) is None


def test_with_a_locked_mode_on_it_always_asks(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    me, appmod = fake_app(db)
    monkeypatch.setattr(appmod, "now_from_db", lambda d: NOW)
    asked = []
    me.guard = lambda changes, proceed, cancel=None, force=False: asked.append(force)
    me.pause_blocks(30, False)
    modes.start(db, "work", NOW, NOW + timedelta(hours=2), locked=True)
    me.pause_blocks(30, False)
    assert asked == [False, True]


# ---------------------------------------------------------------- silencing the notifications

def test_silenced_bedtime_breaks_and_reminders_then_back(tmp_path):
    db, ui = Database(tmp_path / "t.db"), FakeUI()
    e = reminders.Engine(db, ui)
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "23:00",
                                             "wake": "07:00"})
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "every": 30})
    reminders.save(db, reminders.CUSTOM_KEY, [{**reminders.DEFAULT_CUSTOM, "id": "water", "text": "Drink water",
                                               "kind": "interval", "every": 20}])
    bed = datetime(2026, 10, 2, 23, 0)
    e.tick(bed, 0, False)
    assert "sleep" in e.open
    pause.start(db, bed, 120, silent=True)
    t = run(e, bed, 119)
    assert "sleep" not in e.open and ui.closed.count("sleep") == 1  # the bedtime screen went at once
    assert [s for s in ui.shown if s[0] in ("popup", "overlay")] == [ui.shown[0]]   # nothing else came up
    assert ui.toasts == []
    run(e, t, 2)                                                    # over: the bedtime screen is back
    assert "sleep" in e.open


def test_a_pause_without_silence_keeps_the_reminders(tmp_path):
    db, ui = Database(tmp_path / "t.db"), FakeUI()
    e = reminders.Engine(db, ui)
    reminders.save(db, reminders.CUSTOM_KEY, [{**reminders.DEFAULT_CUSTOM, "id": "water", "text": "Drink water",
                                               "kind": "interval", "every": 20}])
    pause.start(db, NOW, 60, silent=False)
    run(e, NOW, 21)
    assert any(s[1] == "custom:water" for s in ui.shown)


def test_the_windows_notices_and_popups_are_silenced(tmp_path, monkeypatch):
    from gui import app as appmod
    db = Database(tmp_path / "t.db")
    sent = []
    me = types.SimpleNamespace(db=db, toaster=types.SimpleNamespace(send=lambda *a, **k: sent.append(a[0]),
                                                                   works=lambda: True))
    show = types.MethodType(appmod.LockdownApp._show, me)
    clock = [NOW]
    monkeypatch.setattr(appmod, "now_from_db", lambda d: clock[0])
    monkeypatch.setattr(appmod.win, "is_fullscreen", lambda: False)
    db.set_setting("notify.format", "toast")
    pause.start(db, NOW, 30, silent=True)
    assert show("Game will be blocked in 5 min.") is False
    assert show("Your time is up.", force=True) is False            # urgent ones too
    clock[0] = NOW + timedelta(minutes=30)
    assert show("Game will be blocked in 5 min.") is True and sent == ["Game will be blocked in 5 min."]


# ---------------------------------------------------------------- the tray

class FakeIcon:
    icon = None

    def update_menu(self):
        pass


def test_the_tray_turns_blue_and_offers_resume(monkeypatch):
    from gui import tray as traymod
    assert icon_art.tray_icon("blue").size == (64, 64)
    monkeypatch.setattr(traymod.icon_art, "tray_icon", lambda state: state)
    t = traymod.Tray(lambda: None, lambda: None, on_pause=lambda: None, on_resume=lambda: None)
    t.icon = FakeIcon()
    t.update(True, "3 sites/apps blocked")
    assert t.icon.icon == "green"
    assert t.set_paused(NOW) and t.icon.icon == "blue" and not t.set_paused(NOW)
    assert t.set_paused(None) and t.icon.icon == "green"
