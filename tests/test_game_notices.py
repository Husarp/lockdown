"""Ordinary Windows notices, held reminders and advance-warning delivery over full-screen games."""
import sys
import types
from datetime import datetime, timedelta

import pytest

import alerts
import reminders
from gui import toast
from tests.test_reminders import run, setup

NOW = datetime(2026, 9, 14, 9, 0)


def test_windows_notifications_are_the_default():
    assert alerts.DEFAULTS["notify.format"] == "toast"
    assert alerts.FORMATS == {"toast": "Windows notifications (recommended)", "inapp": "Lockdown pop-ups",
                              "both": "Both"}


# ---------------------------------------------------------------- the reminders engine over a game

def _water(db):
    reminders.save(db, reminders.CUSTOM_KEY, [{"id": "water", "text": "Drink water", "kind": "times",
                                              "times": ["09:10"]}])


def test_a_held_reminder_goes_out_once_with_its_buttons_and_no_popup(tmp_path):
    db, ui, e = setup(tmp_path)
    _water(db)
    run(e, NOW, 15, fullscreen=True)
    assert ui.held_back == [("custom:water", "Reminder", ["done", "snooze", "dismiss"])]
    assert not [s for s in ui.shown if s[1] == "custom:water"] and ui.toasts == []   # no Tk popup, no double


def test_answered_on_the_notification_it_is_done_and_no_popup_follows(tmp_path):
    db, ui, e = setup(tmp_path)
    _water(db)
    t = run(e, NOW, 15, fullscreen=True)
    assert e.held_answer("custom:water", "done")
    assert "custom:water" not in e.waiting and reminders.counts(db, "water", NOW)["done"] == 1
    run(e, t, 1)                                                       # the game is over: nothing left to show
    assert not [s for s in ui.shown if s[1] == "custom:water"]
    assert not e.held_answer("custom:water", "done")                   # a second (late) click does nothing
    assert reminders.counts(db, "water", NOW)["done"] == 1


def test_unanswered_the_popup_comes_after_the_game(tmp_path):
    db, ui, e = setup(tmp_path)
    _water(db)
    t = run(e, NOW, 15, fullscreen=True)
    run(e, t, 1)
    assert [s for s in ui.shown if s[1] == "custom:water"]
    assert not e.held_answer("custom:water", "done")                   # the notice's button: too late now


# ---------------------------------------------------------------- gui/toast.py

def fake_windows_toasts():
    mod = types.ModuleType("windows_toasts")

    class Toast:
        count = 0

        def __init__(self, **kw):
            self.__dict__.update(kw)
            Toast.count += 1
            self.tag = f"t{Toast.count}"

    mod.Toast = Toast
    mod.ToastButton = lambda content, arguments: types.SimpleNamespace(content=content, arguments=arguments)
    mod.ToastScenario = types.SimpleNamespace(Default="Default", Reminder="Reminder", Important="Important")
    mod.InteractableWindowsToaster = lambda name, notifierAUMID=None: FakeWinToaster(name, notifierAUMID)
    return mod


class FakeWinToaster:
    def __init__(self, name="Lockdown", aumid=toast.APP_ID):
        self.name, self.aumid, self.shown, self.removed = name, aumid, [], []

    def show_toast(self, t):
        self.shown.append(t)

    def remove_toast(self, t):
        self.removed.append(t)


@pytest.fixture
def winrt(monkeypatch):
    monkeypatch.setitem(sys.modules, "windows_toasts", fake_windows_toasts())


def test_buttons_call_back_through_call_soon_and_the_body_opens_lockdown(winrt):
    queued, said, clicks = [], [], []
    t = toast.Toaster(queued.append, said.append, on_click=lambda: clicks.append("open"))
    notice = t.send("Install now?", [("Install", lambda: clicks.append("install")),
                                     ("Later", lambda: clicks.append("later"))], scenario="reminder")
    assert t._toaster.aumid == "com.husarp.lockdown" and said == []
    assert notice.scenario == "Reminder" and [b.content for b in notice.actions] == ["Install", "Later"]
    notice.on_activated(types.SimpleNamespace(arguments=notice.actions[1].arguments))
    notice.on_activated(types.SimpleNamespace(arguments=""))             # a click on the text itself
    assert clicks == []                                                   # (not on WinRT's thread)
    for fn in queued:
        fn()
    assert clicks == ["later", "open"]


def test_it_expires_by_itself_and_never_breaks_through_do_not_disturb(winrt):
    t = toast.Toaster(lambda fn: None, lambda text: None)
    before = datetime.now().astimezone()
    plain = t.send("10 min left", expire_min=toast.HELD_EXPIRE_MIN)
    assert plain.scenario == "Default" and plain.expiration_time.tzinfo is not None
    assert timedelta(minutes=119) < plain.expiration_time - before <= timedelta(minutes=120, seconds=5)
    assert t.send("No button", scenario="reminder").scenario == "Default"   # (Windows ignores it without one)
    assert t.send("x", scenario="urgent").scenario == "Default"           # no "urgent" toasts at all
    t.remove(plain)
    assert t._toaster.removed == [plain]


def test_without_windows_toasts_the_balloon_says_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "windows_toasts", None)              # import fails
    said = []
    t = toast.Toaster(lambda fn: None, said.append)
    assert t.send("Bedtime soon", [("OK", lambda: None)], title="Sleep") is None
    assert said == ["Sleep: Bedtime soon"]
    t.remove(None)                                                        # (nothing to take down: fine)


def test_a_refused_toast_is_still_said(winrt):
    said = []
    t = toast.Toaster(lambda fn: None, said.append)
    t._get().show_toast = lambda notice: (_ for _ in ()).throw(OSError("WinRT says no"))
    assert t.send("Reddit is blocked.") is None and said == ["Reddit is blocked."]


@pytest.fixture
def warning_pipeline(tmp_path, winrt):
    from types import SimpleNamespace

    from resident.notices import Notices
    from resident.workers import WatcherWorker
    from rules import Usage
    from tests.test_rebuild_reminder_ui import Env
    from tests.test_urgent_alerts import _item

    env = Env(tmp_path)
    sounds, flashes, pulses, jobs, diagnostics = [], [], [], [], []
    toaster = toast.Toaster(lambda fn: fn(), lambda text: None)
    clock = [0]
    env.hub.clock.mono = lambda: clock[0]
    env.hub.log_sink = diagnostics.append
    env.win.fullscreen = env.win.dnd = True
    env.win.foreground = lambda: (74, "game.exe")
    sink = Notices(env.hub, env.corner, toaster, read_format=lambda: "both", clock=lambda: clock[0],
                   beep=lambda: sounds.append(clock[0]), flash=lambda: flashes.append(clock[0]) or True,
                   pulse=lambda: pulses.append(clock[0]) or True,
                   schedule=lambda delay, fn: jobs.append(fn) or object())
    env.hub.notify.show = sink.show
    watcher = WatcherWorker(env.hub)
    watcher.setup()
    env.db.set_setting("notify.warn.minutes", "10")
    env.db.set_setting("notify.front.steps", "5, 1")
    start = NOW.replace(hour=20, minute=50)
    item = _item(1, "Discord")

    def tick(seconds, *, using=False, muted=False, silent=False, items=None, usage=None, blocks=None, flush=True):
        clock[0] = seconds
        env.hub.in_use = lambda: {1} if using else set()
        env.hub.snapshot = SimpleNamespace(now=start + timedelta(seconds=seconds), off=False,
                                            items=[item] if items is None else items, groups=[], usage=usage or Usage(),
                                            mode={"mute": True, "phase": None} if muted else None,
                                            pause={"silent": True} if silent else None, block_list=blocks or [])
        watcher.tick()
        if flush:
            while jobs:
                jobs.pop(0)()

    try:
        yield SimpleNamespace(env=env, tick=tick, jobs=jobs, sink=sink, toaster=toaster, item=item, diagnostics=diagnostics,
                              sounds=sounds, flashes=flashes, pulses=pulses)
    finally:
        watcher.db.close()
        env.db.close()


def test_all_enabled_steps_reach_the_card_with_dnd_and_mode_mute(warning_pipeline):
    p = warning_pipeline
    for when, minutes, using in [(0, 10, False), (300, 5, True), (540, 1, True)]:
        p.tick(when, using=using, muted=True)
        assert f"in {minutes} min" in p.env.corner.cards()[0]["text"]
    assert p.sounds == p.flashes == [0, 300, 540]
    assert p.toaster._toaster is None
    assert "fullscreen=True dnd=True mode_mute=True silent=False" in p.diagnostics[0]
    assert "lead_sec=600 steps_sec=(300, 60)" in p.diagnostics[0]
    assert "schedule/lead:600s/in-use=False" in p.diagnostics[0]


def test_a_coincident_step_and_simultaneous_rules_share_one_card_sound_and_flash(warning_pipeline):
    from tests.test_urgent_alerts import _item

    p = warning_pipeline
    p.env.db.set_setting("notify.warn.minutes", "5")
    p.env.win.fullscreen = False    # a windowed or borderless game can miss the fullscreen heuristic
    p.tick(300, using=True, items=[p.item, _item(2, "Steam")])
    (card,) = p.env.corner.cards()
    assert "Discord" in card["text"] and "Steam" in card["text"]
    assert p.sounds == p.flashes == [300]
    assert "batch=2" in p.diagnostics[0]
    assert "in-use:300s+lead:300s" in p.diagnostics[0]


def test_short_repeats_repost_identical_text_and_diagnostics_are_rate_limited(warning_pipeline):
    p = warning_pipeline
    p.env.db.set_setting("notify.warn.repeat_sec", "5")
    p.tick(0, using=True)
    (first,) = p.env.corner.cards()
    for when in range(5, 26, 5):
        p.tick(when, using=True)
    (latest,) = p.env.corner.cards()
    assert first["text"] == latest["text"] and first["id"] != latest["id"]
    assert p.sounds == p.flashes == list(range(0, 26, 5))
    assert len(p.diagnostics) == 1


def test_switch_off_routes_both_general_and_in_use_steps_plainly_over_a_game(warning_pipeline):
    p = warning_pipeline
    p.env.db.set_setting(alerts.FULLSCREEN_WARNINGS, "0")
    p.tick(0)
    p.tick(300, using=True)
    assert not p.env.corner.cards() and not p.sounds and not p.flashes
    assert [t.text_fields[0] for t in p.toaster._toaster.shown] == [
        "Discord will be blocked in 10 min (21:00).", "Discord will be blocked in 5 min (21:00)."]
    assert all(t.scenario == "Default" for t in p.toaster._toaster.shown)


def test_block_started_and_visit_notifications_keep_the_plain_route(warning_pipeline):
    from notification_batch import BlockingMessage

    p = warning_pipeline
    p.tick(0)
    p.tick(600, using=True, flush=False, blocks=[
        {"item": p.item, "reason": "schedule", "until": NOW, "rule": p.item["rules"][0]}])
    p.env.hub.notify(BlockingMessage("A blocked site was visited."), force=True)
    p.jobs.pop()()
    assert "now blocked" in p.toaster._toaster.shown[-1].text_fields[0]
    assert "site was visited" in p.toaster._toaster.shown[-1].text_fields[0]
    assert p.sounds == p.flashes == [0]
    assert all("now blocked" not in card["text"] for card in p.env.corner.cards())


def test_a_silent_pause_at_offer_or_flush_holds_every_warning_cue(warning_pipeline):
    p = warning_pipeline
    p.tick(0, silent=True)
    assert not p.jobs
    p.tick(300, using=True, flush=False)
    p.env.hub.snapshot.pause = {"silent": True}
    p.jobs.pop()()
    assert not p.sounds and not p.flashes and not p.env.corner.cards()
    assert all("silent=True" in line for line in p.diagnostics)


def test_card_and_sound_failure_still_pulse_tray_with_no_taskbar_target(warning_pipeline, monkeypatch):
    p = warning_pipeline
    show = p.env.host.show
    monkeypatch.setattr(p.env.host, "show", lambda: (_ for _ in ()).throw(OSError("host unavailable")))
    p.sink._beep = lambda: False
    p.sink._flash = lambda: False
    p.tick(0)
    assert p.pulses == [0] and not p.env.corner.cards()
    assert "card=failed(OSError) sound=failed flash=no-taskbar-target/tray-submitted" in p.diagnostics[-1]
    monkeypatch.setattr(p.env.host, "show", show)
    p.sink._beep = lambda: True
    p.sink._flash = lambda: True
    p.tick(300, using=True)
    assert len(p.env.corner.cards()) == 1
    assert "card=submitted sound=submitted flash=hwnd-submitted" in p.diagnostics[-1]


def test_page_not_ready_still_uses_other_cues_and_stale_warning_does_not_pop_up_later(warning_pipeline):
    p = warning_pipeline
    p.env.corner._page_ok = False
    p.tick(0)
    assert p.sounds == p.flashes == [0]
    assert p.toaster._toaster.shown and not p.env.corner.cards()
    assert "card_ready=False card=not-ready" in p.diagnostics[-1]
    p.env.corner.page_loaded()
    assert not p.env.corner.cards()


def test_taskbar_flash_uses_a_bounded_tray_only_call_on_the_visible_own_main_hwnd():
    from unittest.mock import Mock
    from resident.notices import flash_taskbar

    windows = {
        74: (12, True, 0, 0),     # foreground window in a different process
        75: (10, False, 0, 0),    # hidden main window
        76: (10, True, 0x80, 0),  # corner tool window
        77: (10, True, 0, 76),    # owned window with no taskbar button
        78: (10, True, 0, 0),     # main window
    }

    def write_pid(hwnd, process):
        process._obj.value = windows[hwnd][0]

    calls = []
    user32 = types.SimpleNamespace(
        GetWindowThreadProcessId=Mock(side_effect=write_pid),
        IsWindowVisible=Mock(side_effect=lambda h: windows[h][1]),
        GetWindowLongW=Mock(side_effect=lambda h, _: windows[h][2]),
        GetWindow=Mock(side_effect=lambda h, _: windows[h][3]),
        FlashWindowEx=Mock(side_effect=lambda ptr: calls.append(
            (ptr._obj.hwnd, ptr._obj.dwFlags, ptr._obj.uCount, ptr._obj.dwTimeout)) or False),
    )
    for rejected in (None, 74, 75, 76, 77):
        assert not flash_taskbar(rejected, user32=user32, pid=10)
    assert flash_taskbar(78, user32=user32, pid=10)
    assert calls == [(78, 2, 3, 0)]


def test_tray_pulse_restores_status_updated_during_the_pulse(monkeypatch):
    import importlib.util
    import threading
    from pathlib import Path

    monkeypatch.setitem(sys.modules, "pystray", types.SimpleNamespace(Icon=object))
    monkeypatch.setitem(sys.modules, "pystray._util", types.SimpleNamespace(win32=None))
    spec = importlib.util.spec_from_file_location("test_tray_module",
                                                  Path(__file__).resolve().parents[1] / "src/gui/tray.py")
    tray_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tray_module)
    timers = []

    class Timer:
        def __init__(self, delay, run):
            timers.append(self)
            self.delay, self.run, self.cancelled = delay, run, False

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(tray_module.threading, "Timer", Timer)
    monkeypatch.setattr(tray_module.icon_art, "app_icon", lambda *a, **k: "pulse")
    monkeypatch.setattr(tray_module.icon_art, "tray_icon", lambda state: state)
    tray = tray_module.Tray.__new__(tray_module.Tray)
    tray._lock = threading.Lock()
    tray._pulse = None
    tray._stopped = tray.off = False
    tray.running, tray.paused_until, tray.active_mode = True, None, None
    tray._icon_state = "green"
    tray.icon = types.SimpleNamespace(icon="green", visible=True, update_menu=lambda: None, stop=lambda: None)
    assert tray.pulse_warning() and tray.pulse_warning()
    assert tray.icon.icon == "pulse" and len(timers) == 1 and 0 < timers[0].delay < 2
    tray.set_paused(NOW)
    assert tray.icon.icon == "pulse"
    timers[0].run()
    assert tray.icon.icon == "blue"
    assert tray.pulse_warning()
    tray.stop()
    assert timers[-1].cancelled and not tray.pulse_warning()


def test_warning_host_repositions_on_the_active_monitor_without_activating(monkeypatch):
    import ctypes
    from unittest.mock import Mock

    from resident.corner import (SW_SHOWNOACTIVATE, SWP_NOACTIVATE, WS_EX_APPWINDOW,
                                 WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, Corner, CornerHost, corner_rect)

    style, visible, moves, shows = [WS_EX_APPWINDOW], [False], [], []
    area = [(1920, 0, 3840, 1040)]

    def set_style(hwnd, index, value):
        style[0] = value

    def show(hwnd, flag):
        shows.append((hwnd, flag))
        visible[0] = True

    user32 = types.SimpleNamespace(
        GetWindowLongW=Mock(side_effect=lambda *args: style[0]), SetWindowLongW=Mock(side_effect=set_style),
        SetWindowPos=Mock(side_effect=lambda *args: moves.append(args) or True),
        IsWindowVisible=Mock(side_effect=lambda _: visible[0]), ShowWindow=Mock(side_effect=show),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: user32, raising=False)
    native = types.SimpleNamespace(IsHandleCreated=True, Handle=types.SimpleNamespace(ToInt64=lambda: 42))
    host = CornerHost(types.SimpleNamespace(native=native), work_area=lambda: area[0])
    monkeypatch.setattr(sys, "platform", "win32")
    corner = Corner(host=host)
    assert corner.warning("First")
    assert style == [WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE]
    assert shows == [(42, SW_SHOWNOACTIVATE)]
    assert moves[0][2:6] == corner_rect(area[0], 120)
    area[0] = (-1920, 0, 0, 1040)
    assert corner.warning("Second")
    assert moves[-1][2:6] == corner_rect(area[0], 120)
    assert all(move[0] == 42 and move[-1] & SWP_NOACTIVATE for move in moves)
    assert len(shows) == 1


def test_plain_route_requests_new_toasts_at_the_configured_seconds_interval(warning_pipeline):
    p = warning_pipeline
    p.env.db.set_setting(alerts.FULLSCREEN_WARNINGS, "0")
    p.env.db.set_setting("notify.warn.repeat_sec", "5")
    p.tick(0, using=True)
    p.tick(5, using=True)
    first, repeat = p.toaster._toaster.shown
    assert first.text_fields == repeat.text_fields
    assert first.tag == repeat.tag
    assert not getattr(repeat, "suppress_popup", False)
    assert p.sounds == p.flashes == []


def test_an_allowance_start_and_running_low_use_the_same_delivery_path(warning_pipeline):
    from rules import Usage, allowance_bucket, effective_rules

    p = warning_pipeline
    p.item["rules"][0]["allowance_min"] = 12
    p.tick(600, using=True, muted=True)
    assert "12 min of your 12 min allowance left" in p.env.corner.cards()[0]["text"]
    (rule,) = effective_rules(p.item, [])
    ends = (NOW + timedelta(days=1)).replace(hour=5)
    usage = Usage({("item:1", allowance_bucket(rule, ends)): 7 * 60})
    p.tick(600 + 7 * 60, using=True, muted=True, usage=usage)
    assert "5 min of your allowance left" in p.env.corner.cards()[0]["text"]
    assert p.sounds == p.flashes == [600, 1020]
