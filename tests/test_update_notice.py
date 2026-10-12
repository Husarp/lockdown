"""0.84.4: a newer Lockdown is noticed within hours and stays on screen until you deal with it.

Before, the automatic check ran once a day, so three releases in one morning went unseen; and what it found was
one corner popup that faded after 8 s (or was swallowed by a muted mode) and was then marked "said" for good -
nothing in the window or the tray showed it again. Now: a check a minute after start, every EVERY_HOURS, and on
opening the window; a banner over every page, a dot on About and a line in the tray menu while the update waits;
the popup answers "Install" / "Remind me later" (4 h) / x; the banner's x hides the banner until Lockdown is next
started (0.84.8 - it used to skip that version for good)."""
from datetime import datetime, timedelta

import pytest

import updates
from db import Database

NOW = datetime(2026, 10, 2, 12, 0)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "REPO", "Husarp/lockdown")
    monkeypatch.setattr(updates, "VERSION", "0.84.3")
    return Database(tmp_path / "t.db")


def release(version="0.84.4", asset=True):
    return {"version": version, "url": f"https://github.com/Husarp/lockdown/releases/tag/v{version}",
            "newer": updates.is_newer(version, "0.84.3"), "asset": "http://x/s.exe" if asset else None,
            "size": 35_000_000}


# ---------- when it asks ----------

def test_in_the_background_it_checks_every_six_hours(db):
    assert updates.EVERY_HOURS == 6
    assert updates.due(db, NOW)                                    # never checked yet
    updates.trying(db, NOW)
    updates.checked(db, NOW, release("0.84.3"))
    assert not updates.due(db, NOW + timedelta(hours=5, minutes=59))
    assert updates.due(db, NOW + timedelta(hours=6))


def test_every_window_open_checks_but_not_twice_within_five_minutes(db):
    updates.trying(db, NOW)
    updates.checked(db, NOW)
    assert not updates.due(db, NOW + timedelta(minutes=4), updates.OPEN_HOURS)   # opened again right away: no
    assert updates.due(db, NOW + timedelta(minutes=5), updates.OPEN_HOURS)       # 5 minutes later: yes
    assert not updates.due(db, NOW + timedelta(minutes=30))                     # the background waits 6 h


def test_a_failed_check_is_retried_on_the_next_poll_but_not_every_minute(db):
    updates.trying(db, NOW)                                        # no answer: not written down as checked
    assert not updates.due(db, NOW + timedelta(minutes=updates.RETRY_MIN - 1), 0)
    assert updates.due(db, NOW + timedelta(minutes=updates.RETRY_MIN), 0)


def test_it_stays_far_inside_githubs_rate_limit(db):
    """60 requests an hour for a caller without an account. Poll every minute for a day with GitHub always
    failing, opening the window every minute too: at most one try every RETRY_MIN minutes."""
    tries = []
    for minute in range(24 * 60):
        now = NOW + timedelta(minutes=minute)
        for hours in (updates.EVERY_HOURS, updates.OPEN_HOURS, 0):
            if updates.due(db, now, hours):
                updates.trying(db, now)
                tries.append(now)
    per_hour = max(sum(1 for t in tries if h <= t < h + timedelta(hours=1))
                   for h in (NOW + timedelta(hours=n) for n in range(24)))
    assert per_hour <= 60 // updates.RETRY_MIN == 12   # GitHub allows 60


def test_switched_off_it_never_asks(db):
    db.set_setting(updates.AUTO_KEY, "0")
    assert not updates.due(db, NOW) and not updates.due(db, NOW, 0)


# ---------- the banner (and the About dot, and the tray line) ----------

def test_what_was_found_stays_on_screen_until_installed(db, monkeypatch):
    assert updates.banner(db, NOW) is None
    updates.checked(db, NOW, release())
    shown = updates.banner(db, NOW + timedelta(days=3))            # still there days later, across restarts
    assert shown["version"] == "0.84.4" and updates.can_install(shown)
    assert updates.label(shown) == "Lockdown 0.84.4 is available"
    monkeypatch.setattr(updates, "VERSION", "0.84.4")              # installed: gone
    assert updates.banner(db, NOW) is None


def test_it_never_offers_an_older_or_the_same_version(db):
    updates.checked(db, NOW, release())
    updates.checked(db, NOW, release("0.84.3"))                    # GitHub's latest is what you have: cleared
    assert updates.banner(db, NOW) is None
    updates.remember(db, {"version": "0.80.0", "newer": True})     # (even if told it is "newer")
    assert updates.available(db) is None
    db.set_setting(updates.FOUND_KEY, "{not json")
    assert updates.banner(db, NOW) is None


def test_a_failed_check_keeps_what_was_found_before(db):
    updates.checked(db, NOW, release())
    updates.remember(db, None)                                     # no answer is not "no update"
    assert updates.banner(db, NOW)["version"] == "0.84.4"


def test_remind_me_later_hides_popup_and_banner_for_four_hours(db):
    found = release()
    updates.checked(db, NOW, found)
    updates.said(db, "0.84.4")                                     # the popup came up
    updates.snooze(db, NOW)
    for minutes in (1, 60, 4 * 60 - 1):
        t = NOW + timedelta(minutes=minutes)
        assert updates.banner(db, t) is None and not updates.worth_saying(db, found, t)
    back = NOW + timedelta(hours=updates.SNOOZE_HOURS)
    assert updates.banner(db, back)["version"] == "0.84.4"
    assert updates.worth_saying(db, found, back)                   # the popup once more, too
    updates.said(db, "0.84.4")
    assert not updates.worth_saying(db, found, back)               # ... once


def test_a_clock_turned_back_cannot_stretch_the_snooze(db):
    updates.checked(db, NOW, release())
    updates.snooze(db, NOW + timedelta(days=30))                   # written while the clock was far ahead
    assert updates.banner(db, NOW)["version"] == "0.84.4"


def test_nothing_hides_an_update_for_good(db):
    """0.84.8 (APP-STANDARDS 2): the banner's x used to skip that version for good (updates.skipped). Now nothing in
    the database hides it - an old "skipped" mark from before has no effect - and the x is the window's, until the
    next start (test_the_x_hides_the_banner_until_the_next_start)."""
    db.set_setting("updates.skipped", "0.84.4")                    # left behind by 0.84.7's x
    updates.checked(db, NOW, release())
    assert updates.banner(db, NOW)["version"] == "0.84.4"
    assert updates.worth_saying(db, release(), NOW)


def test_turning_the_popup_off_keeps_the_banner(db):
    """The banner doesn't interrupt anything, so "Tell me when a new version is found" off only stops the popup."""
    db.set_setting(updates.NOTIFY_KEY, "0")
    updates.checked(db, NOW, release())
    assert updates.banner(db, NOW) and not updates.worth_saying(db, release(), NOW)


def test_popup_once_per_version_banner_until_dealt_with(db):
    found = release()
    updates.checked(db, NOW, found)
    assert updates.worth_saying(db, found, NOW)
    updates.said(db, "0.84.4")                                     # popup shown (and closed with x)
    assert not updates.worth_saying(db, found, NOW + timedelta(hours=6))
    assert updates.banner(db, NOW + timedelta(hours=6))            # the banner stays


# ---------- the tray ----------

class FakeIcon:
    def __init__(self):
        self.calls = []

    def update_menu(self):
        self.calls.append("menu")


def test_tray_menu_says_update_available():
    from gui import tray as traymod
    assert traymod.update_text("0.84.4") == "Update available: Lockdown 0.84.4"
    assert traymod.update_text(None) == ""
    asked = []
    t = traymod.Tray(lambda: None, lambda: None, on_update=lambda: asked.append(1))
    t.icon = FakeIcon()
    assert t.set_update("0.84.4") and t.icon.calls == ["menu"]
    assert not t.set_update("0.84.4") and t.icon.calls == ["menu"]   # the minute poll: nothing new, nothing sent
    assert t.set_update(None) and t.icon.calls == ["menu", "menu"]
    t.on_update()
    assert asked == [1]
