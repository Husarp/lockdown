"""Bedtime grayscale on the PC ("Grey screen during bedtime" on the Sleep card, webapi/reminders.py): free to turn on, turning it
off while bedtime is Important needs the challenge, and the window it greys is bedtime to wake-up time (reminders.bedtime_grey_wanted;
the GreyWorker's side is in tests/test_grey_screen.py)."""
from datetime import datetime

import pytest

import reminders
from test_rebuild_reminders_page import SLEEP, api, ctx, data, locked, pass_challenge, stored  # noqa: F401


def sleep(ctx):
    return reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)


def test_the_sleep_card_has_a_grey_switch_that_is_off_by_default(api, ctx):
    assert reminders.DEFAULT_SLEEP["grey"] is False
    assert data(api.reminders_get())["sleep"]["grey"] is False
    assert data(api.reminder_save_sleep({**SLEEP, "grey": True}))["saved"]
    assert sleep(ctx)["grey"] is True and data(api.reminders_get())["sleep"]["grey"] is True


def test_a_save_that_says_nothing_about_it_keeps_the_switch(api, ctx):
    data(api.reminder_save_sleep({**SLEEP, "grey": True}))
    data(api.reminder_save_sleep({**SLEEP, "bedtime": "22:30"}))              # (an older page / a script: no "grey" key)
    assert sleep(ctx)["grey"] is True and sleep(ctx)["bedtime"] == "22:30"


def test_the_switch_must_be_yes_or_no(api, ctx):
    assert not api.reminder_save_sleep({**SLEEP, "grey": "yes"})["ok"]
    assert not api.reminder_save_sleep({**SLEEP, "grey": 1})["ok"]
    assert sleep(ctx)["grey"] is False


def test_turning_it_on_is_free_even_when_important_and_locked(api, ctx, locked):
    data(api.reminder_save_sleep({**SLEEP, "guarded": True}))                  # (important from the start: free, nothing weaker)
    out = data(api.reminder_save_sleep({**SLEEP, "guarded": True, "grey": True}))
    assert out == {"saved": True} and sleep(ctx)["grey"] is True


def test_turning_it_off_while_bedtime_is_important_needs_the_challenge(api, ctx, locked):
    data(api.reminder_save_sleep({**SLEEP, "guarded": True, "grey": True}))
    out = data(api.reminder_save_sleep({**SLEEP, "guarded": True, "grey": False}))
    assert out["needs_challenge"] and out["ticket"]
    assert "Turn off the grey screen at bedtime" in out["changes"]
    assert sleep(ctx)["grey"] is True                                          # nothing changed yet
    data(api.challenge_cancel(out["ticket"]))
    assert sleep(ctx)["grey"] is True
    out = data(api.reminder_save_sleep({**SLEEP, "guarded": True, "grey": False}))
    assert pass_challenge(locked, out["ticket"], api)["passed"]
    assert sleep(ctx)["grey"] is False


def test_turning_it_off_when_bedtime_is_not_important_is_free(api, ctx, locked):
    data(api.reminder_save_sleep({**SLEEP, "guarded": False, "grey": True}))
    assert data(api.reminder_save_sleep({**SLEEP, "guarded": False, "grey": False})) == {"saved": True}
    assert sleep(ctx)["grey"] is False


def test_turning_it_off_is_free_while_anti_bypass_is_not_locked(api, ctx):
    data(api.reminder_save_sleep({**SLEEP, "guarded": True, "grey": True}))
    assert data(api.reminder_save_sleep({**SLEEP, "guarded": True, "grey": False})) == {"saved": True}


def test_the_change_is_in_the_app_log_wording(api, ctx):
    import applog_changes
    old = {**reminders.DEFAULT_SLEEP}
    new = {**old, "grey": True}
    lines = applog_changes.HANDLERS["reminders.sleep"](__import__("json").dumps(old), __import__("json").dumps(new))
    assert any("grey screen" in str(line) for line in lines), lines


@pytest.mark.parametrize("bedtime,wake,clock,wanted", [
    ("23:00", "07:00", "22:59", False), ("23:00", "07:00", "23:00", True), ("23:00", "07:00", "03:30", True),
    ("23:00", "07:00", "06:59", True), ("23:00", "07:00", "07:00", False), ("23:00", "07:00", "12:00", False),
    ("01:00", "08:00", "00:59", False), ("01:00", "08:00", "01:00", True), ("01:00", "08:00", "07:59", True),
    ("13:00", "15:00", "14:59", True), ("13:00", "15:00", "15:00", False), ("13:00", "13:00", "13:00", False)])
def test_the_window_is_bedtime_up_to_wake_up_time(bedtime, wake, clock, wanted):
    hour, minute = map(int, clock.split(":"))
    sleep_cfg = {**reminders.DEFAULT_SLEEP, "on": True, "grey": True, "bedtime": bedtime, "wake": wake}
    assert reminders.bedtime_grey_wanted(sleep_cfg, datetime(2026, 10, 12, hour, minute, 30)) is wanted


def test_it_takes_both_switches():
    cfg = {**reminders.DEFAULT_SLEEP, "bedtime": "00:00", "wake": "23:59"}
    now = datetime(2026, 10, 12, 12, 0)
    assert reminders.bedtime_grey_wanted({**cfg, "on": True, "grey": True}, now)
    assert not reminders.bedtime_grey_wanted({**cfg, "on": True, "grey": False}, now)
    assert not reminders.bedtime_grey_wanted({**cfg, "on": False, "grey": True}, now)
    assert not reminders.bedtime_grey_wanted({"on": True}, now)
