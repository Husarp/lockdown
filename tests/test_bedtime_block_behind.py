"""Block clicks behind the bedtime screen ("Block clicks behind the bedtime screen" on the Sleep card, reminders.sleep
`block_behind`): free to turn on, turning it off while bedtime is Important needs the challenge (as the grey switch,
tests/test_bedtime_grey.py), and the cover (bedtime and a strict break - one cover) then keeps itself on top and takes the
keyboard back (resident/overlay.py). What only Windows can prove: the cover really staying above other always-on-top
windows, and Windows handing the keyboard over."""
import ctypes
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import reminders
from db import Database
from gui import screens
from resident import overlay as overlay_mod
from resident import windows
from resident.overlay import Overlay, OverlayHost
from test_rebuild_reminders_page import SLEEP, api, ctx, data, locked, pass_challenge, stored  # noqa: F401


def sleep(ctx):
    return reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)


# ---------------------------------------------------------------- the switch

def test_the_sleep_card_has_the_switch_and_it_is_off_by_default(api, ctx):
    assert reminders.DEFAULT_SLEEP["block_behind"] is False
    assert data(api.reminders_get())["sleep"]["block_behind"] is False
    assert data(api.reminder_save_sleep({**SLEEP, "block_behind": True}))["saved"]
    assert sleep(ctx)["block_behind"] is True and data(api.reminders_get())["sleep"]["block_behind"] is True


def test_settings_saved_before_it_existed_read_as_off(api, ctx):
    old = {k: v for k, v in reminders.DEFAULT_SLEEP.items() if k != "block_behind"}
    ctx.db.set_setting(reminders.SLEEP_KEY, json.dumps(old))
    assert sleep(ctx)["block_behind"] is False and data(api.reminders_get())["sleep"]["block_behind"] is False


def test_a_save_that_says_nothing_about_it_keeps_the_switch(api, ctx):
    data(api.reminder_save_sleep({**SLEEP, "block_behind": True}))
    data(api.reminder_save_sleep({**SLEEP, "bedtime": "22:30"}))              # (an older page / a script: no key)
    assert sleep(ctx)["block_behind"] is True and sleep(ctx)["bedtime"] == "22:30"


def test_the_switch_must_be_yes_or_no(api, ctx):
    assert not api.reminder_save_sleep({**SLEEP, "block_behind": "yes"})["ok"]
    assert not api.reminder_save_sleep({**SLEEP, "block_behind": 1})["ok"]
    assert sleep(ctx)["block_behind"] is False


def test_turning_it_on_is_free_even_when_important_and_locked(api, ctx, locked):
    data(api.reminder_save_sleep({**SLEEP, "guarded": True}))
    out = data(api.reminder_save_sleep({**SLEEP, "guarded": True, "block_behind": True}))
    assert out == {"saved": True} and sleep(ctx)["block_behind"] is True


def test_turning_it_off_while_bedtime_is_important_needs_the_challenge(api, ctx, locked):
    data(api.reminder_save_sleep({**SLEEP, "guarded": True, "block_behind": True}))
    out = data(api.reminder_save_sleep({**SLEEP, "guarded": True, "block_behind": False}))
    assert out["needs_challenge"] and out["ticket"]
    assert "Let clicks through the bedtime screen" in out["changes"]
    assert sleep(ctx)["block_behind"] is True                                  # nothing changed yet
    data(api.challenge_cancel(out["ticket"]))
    assert sleep(ctx)["block_behind"] is True
    out = data(api.reminder_save_sleep({**SLEEP, "guarded": True, "block_behind": False}))
    assert pass_challenge(locked, out["ticket"], api)["passed"]
    assert sleep(ctx)["block_behind"] is False


def test_turning_it_off_when_bedtime_is_not_important_is_free(api, ctx, locked):
    data(api.reminder_save_sleep({**SLEEP, "guarded": False, "block_behind": True}))
    assert data(api.reminder_save_sleep({**SLEEP, "guarded": False, "block_behind": False})) == {"saved": True}
    assert sleep(ctx)["block_behind"] is False


def test_the_change_is_in_the_app_log_wording():
    import applog_changes
    old = {**reminders.DEFAULT_SLEEP}
    lines = applog_changes.HANDLERS["reminders.sleep"](json.dumps(old), json.dumps({**old, "block_behind": True}))
    assert any("block clicks behind" in str(line) for line in lines), lines


# ---------------------------------------------------------------- the cover

class Host:
    def __init__(self):
        self.refreshes = 0

    def show(self, background):
        pass

    def hide(self):
        pass

    def refresh(self):
        self.refreshes += 1


def test_each_screen_takes_the_switch_as_it_is_when_it_comes_up():
    wanted = {"on": False}
    overlay = Overlay(None, None, lambda k, a: None, background=lambda: "#000000", block_for=lambda key: wanted["on"])
    overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
    assert overlay.blocking is False
    wanted["on"] = True
    overlay.show("break-run", "Break time", "x", datetime.now() + timedelta(minutes=5), [])
    assert overlay.blocking is True                                             # a strict break: the same cover
    overlay.close("break-run")
    assert overlay.blocking is False                                            # the bedtime screen behind kept its own
    overlay.close("sleep")
    assert overlay.blocking is False                                            # nothing up


def test_without_the_switch_nothing_blocks_and_a_broken_reading_counts_as_off():
    overlay = Overlay(None, None, lambda k, a: None, background=lambda: "#000000")
    overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
    assert overlay.blocking is False
    broken = Overlay(None, None, lambda k, a: None, background=lambda: "#000000",
                     block_for=lambda key: (_ for _ in ()).throw(RuntimeError("db")))
    broken.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
    assert broken.shown and broken.blocking is False


def test_a_blocking_cover_is_put_back_more_often(monkeypatch):
    monkeypatch.setattr(overlay_mod, "GUARD_SEC", 5)
    monkeypatch.setattr(overlay_mod, "BLOCK_GUARD_SEC", 0.02)
    host = Host()
    overlay = Overlay(None, host, lambda k, a: None, background=lambda: "#000000", block_for=lambda key: True)
    overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
    deadline = time.time() + 3
    while host.refreshes < 3 and time.time() < deadline:
        time.sleep(0.02)
    overlay.close("sleep")
    assert host.refreshes >= 3


MAIN, OTHER = (0, 0, 1920, 1080), (1920, 0, 3840, 1080)
HWNDS = {"Lockdown overlay": 100, "Lockdown overlay cover 1": 101}


def fake_win32(monkeypatch):
    """The Win32 calls the host makes, recorded: windows found by title, never moved by anyone."""
    put, keys = [], []
    monkeypatch.setattr(overlay_mod, "_hwnd", lambda title: HWNDS.get(title, 0))
    monkeypatch.setattr(overlay_mod, "_rect_of", lambda hwnd: MAIN if hwnd == 100 else OTHER)
    monkeypatch.setattr(overlay_mod, "_put", lambda hwnd, rect, front=False, show=True: put.append(hwnd))
    monkeypatch.setattr(overlay_mod, "_take_keys", lambda hwnd, ours: keys.append((hwnd, set(ours))))
    return put, keys


def make_host(block):
    window = SimpleNamespace(show=lambda: None, hide=lambda: None)
    made = []
    monitors = [screens.Monitor(MAIN, True), screens.Monitor(OTHER)]
    host = OverlayHost(window, monitors=lambda: monitors, blocking=lambda: block["on"],
                       make_cover=lambda title, bg: made.append(title) or SimpleNamespace(destroy=lambda: None))
    return host, made


@pytest.mark.parametrize("on", [False, True])
def test_refresh_puts_everything_back_on_top_and_takes_the_keys_only_when_blocking(monkeypatch, on):
    put, keys = fake_win32(monkeypatch)
    host, made = make_host({"on": on})
    host.show("#000000")
    deadline = time.time() + 3
    while not (made and host._covers) and time.time() < deadline:
        time.sleep(0.02)
    put.clear()
    host.refresh()
    if on:
        assert put == [100, 101] and keys == [(100, {100, 101})]
    else:
        assert put == [] and keys == []                                         # today's behaviour: only when moved
    host.hide()


class FakeUser32:
    def __init__(self, front):
        self.front, self.calls = front, []

    def GetForegroundWindow(self):
        return self.front

    def GetWindowThreadProcessId(self, hwnd, pid):
        return 77

    def AttachThreadInput(self, a, b, on):
        self.calls.append(("attach", a, b, on))
        return 1

    def BringWindowToTop(self, hwnd):
        self.calls.append(("top", hwnd))

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("front", hwnd))


def test_take_keys_leaves_the_cover_alone_and_takes_the_keyboard_from_anything_else(monkeypatch):
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: SimpleNamespace(GetCurrentThreadId=lambda: 5), raising=False)
    user32 = FakeUser32(front=101)
    monkeypatch.setattr(overlay_mod, "_user32", lambda: user32)
    assert overlay_mod._take_keys(100, {100, 101}) is False and user32.calls == []     # a cover has it: nothing
    user32.front = 555                                                                  # an app behind has it
    assert overlay_mod._take_keys(100, {100, 101}) is True
    assert user32.calls == [("attach", 5, 77, True), ("top", 100), ("front", 100), ("attach", 5, 77, False)]


def test_attach_reads_the_switch_from_the_sleep_settings(tmp_path):
    db = Database(tmp_path / "w.db")
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "block_behind": True})
    overlay_window = SimpleNamespace(evaluate_js=lambda script: None, show=lambda: None, hide=lambda: None)
    win = windows.Windows(overlay=overlay_window)
    hub = SimpleNamespace(snapshot=None, clock=SimpleNamespace(now_with=lambda off: datetime(2026, 10, 12, 23, 5)))
    ui = SimpleNamespace(worker=SimpleNamespace(db=db, answer=lambda k, a: None, poke=lambda: None), overlay_handler=None)
    ctx = SimpleNamespace()
    publisher = overlay_mod.attach(win, hub, ctx, ui)
    try:
        host = ctx.overlay.host
        ctx.overlay.host = None                                                 # (no real window here)
        ctx.overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
        assert ctx.overlay.blocking is True and host.blocking() is True
        reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "block_behind": False})
        ctx.overlay.show("sleep", "Time for bed", "x", None, [("Dismiss", "dismiss")])
        assert ctx.overlay.blocking is False and host.blocking() is False
    finally:
        publisher.stop()
