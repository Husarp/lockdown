"""0.84.4: a newer Lockdown is noticed within hours and stays on screen until you deal with it.

Before, the automatic check ran once a day, so three releases in one morning went unseen; and what it found was
one corner popup that faded after 8 s (or was swallowed by a muted mode) and was then marked "said" for good -
nothing in the window or the tray showed it again. Now: a check a minute after start, every EVERY_HOURS, and on
opening the window; a banner over every page, a dot on About and a line in the tray menu while the update waits;
the popup answers "Install" / "Remind me later" (4 h) / x; the banner's x hides the banner until Lockdown is next
started (0.84.8 - it used to skip that version for good)."""
import types
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


# ---------- the window's wiring (no Tk: the methods run on a stand-in) ----------

class Widget:
    def __init__(self):
        self.log = []

    def __getattr__(self, name):
        return lambda *a, **k: self.log.append((name, k.get("text"), k.get("image")))


def fake_app(db):
    from gui import app as appmod
    me = types.SimpleNamespace(db=db, update_shown=None, update_text=Widget(), update_get=Widget(),
                               update_banner=Widget(), nav_icons={"About": ("plain", "plain-on")},
                               nav_plain={False: ("plain", "plain-on"), True: ("dot", "dot-on")},
                               nav_buttons={"About": (Widget(), None)}, current_page="Dashboard",
                               notices=[], update_popup=None, popup=None, tray_versions=[])
    me.tray = types.SimpleNamespace(set_update=me.tray_versions.append)
    me._update_notice = lambda found: me.notices.append(found["version"])
    for name in ("refresh_update", "update_later", "update_close", "_close_update_popup"):
        setattr(me, name, types.MethodType(getattr(appmod.LockdownApp, name), me))
    return me, appmod


def test_banner_dot_and_tray_follow_the_found_version(db, monkeypatch):
    me, appmod = fake_app(db)
    monkeypatch.setattr(appmod, "now_from_db", lambda d: NOW)
    me.refresh_update()
    assert me.update_shown is None and me.notices == []
    updates.checked(db, NOW, release())
    me.refresh_update()
    assert me.update_shown == ("0.84.4", True)
    assert ("configure", "Lockdown 0.84.4 is available", None) in me.update_text.log
    assert ("grid", None, None) in me.update_banner.log
    assert me.nav_icons["About"] == ("dot", "dot-on") and me.tray_versions == ["0.84.4"]
    assert me.notices == ["0.84.4"]                                # the popup is asked for
    before = len(me.update_banner.log)
    me.refresh_update()                                            # the minute poll, nothing new: no widget touched
    assert len(me.update_banner.log) == before and me.tray_versions == ["0.84.4"]


def test_later_and_the_x_from_the_window(db, monkeypatch):
    me, appmod = fake_app(db)
    clock = [NOW]
    monkeypatch.setattr(appmod, "now_from_db", lambda d: clock[0])
    updates.checked(db, NOW, release())
    me.refresh_update()
    me.update_later()
    assert me.update_shown is None and ("grid_remove", None, None) in me.update_banner.log
    assert me.nav_icons["About"] == ("plain", "plain-on") and me.tray_versions[-1] is None
    clock[0] = NOW + timedelta(hours=4)
    me.refresh_update()
    assert me.update_shown == ("0.84.4", True)                     # back after 4 h
    me.update_close()
    clock[0] = NOW + timedelta(days=2)
    me.refresh_update()
    assert me.update_bar is None                                   # the banner stays away while Lockdown runs
    assert me.update_banner.log[-1][0] == "grid_remove"
    assert me.tray_versions[-1] == "0.84.4" and me.nav_icons["About"] == ("dot", "dot-on")   # the dot and tray stay
    assert updates.available(db)["version"] == "0.84.4"            # (About still offers it)


def test_the_x_hides_the_banner_until_the_next_start(db, monkeypatch):
    """APP-STANDARDS 2: closing the banner hides it until Lockdown is next started - not for good. Closing the
    window to the tray and opening it again (the window object lives on) does not bring it back; a fresh start
    (a new window object, the same database) does. A newer version is news at once."""
    me, appmod = fake_app(db)
    monkeypatch.setattr(appmod, "now_from_db", lambda d: NOW)
    updates.checked(db, NOW, release())
    me.refresh_update()
    me.update_close()
    for _ in range(3):                                             # the minute poll, opening from the tray ...
        me.refresh_update()
    assert me.update_bar is None
    updates.checked(db, NOW, release("0.84.5"))                    # a newer one comes out: the banner is back
    me.refresh_update()
    assert me.update_bar == ("0.84.5", True)
    me.update_close()
    restarted, _ = fake_app(db)                                    # Lockdown started again
    restarted.refresh_update()
    assert restarted.update_bar == ("0.84.5", True) and ("grid", None, None) in restarted.update_banner.log


def test_the_popup_waits_while_you_dont_want_interruptions(db, monkeypatch):
    from gui import app as appmod
    found = release()
    shown = []
    me = types.SimpleNamespace(db=db, update_popup=None, popup=None, update_later=lambda: None,
                               _install=lambda f=None: None)
    me._show = lambda message, **k: (shown.append((message, [a[0] for a in k["actions"]], k["sticky"])) or
                                     me.allow)
    notice = types.MethodType(appmod.LockdownApp._update_notice, me)
    monkeypatch.setattr(appmod.win, "is_fullscreen", lambda: False)
    monkeypatch.setattr(appmod.win, "do_not_disturb", lambda: True)
    me.allow = True
    notice(found)
    assert shown == [] and updates.worth_saying(db, found, NOW)     # Do not disturb: not now, still to say
    monkeypatch.setattr(appmod.win, "do_not_disturb", lambda: False)
    me.allow = False                                               # a muted mode swallowed it
    notice(found)
    assert updates.worth_saying(db, found, NOW)                     # ... so not marked as said
    me.allow = True
    notice(found)
    assert shown[-1][1:] == (["Install", "Remind me later"], True)  # answered, not faded
    assert "0.84.4" in shown[-1][0] and not updates.worth_saying(db, found, NOW)


def test_install_never_starts_a_second_download(db, monkeypatch):
    from gui import app as appmod
    started = []
    about = types.SimpleNamespace(downloading=True, show_found=lambda f: started.append("show"),
                                  _get=lambda: started.append("get"))
    me = types.SimpleNamespace(db=db, pages={"About": about}, update_popup=None, _open_on=lambda p: None)
    me._close_update_popup = lambda: None
    install = types.MethodType(appmod.LockdownApp._install, me)
    install(release())
    assert started == []
    about.downloading = False
    install(release())
    assert started == ["show", "get"]
    opened = []
    monkeypatch.setattr(appmod.webbrowser, "open", opened.append)
    install(release(asset=False))                                  # nothing to install: the release page
    assert opened and started == ["show", "get"]


def test_a_newer_release_replaces_the_open_popup(db, monkeypatch):
    """Found in review: a popup for 0.84.4 left open (it no longer fades) while 0.84.5 came out kept offering
    0.84.4 - its Install would have downloaded the older release, and the guard against two popups kept the new
    one away. It is closed, and the popup for 0.84.5 is asked for."""
    me, appmod = fake_app(db)
    monkeypatch.setattr(appmod, "now_from_db", lambda d: NOW)

    class OpenPopup:
        closed = False

        def winfo_exists(self):
            return not self.closed

        def close(self):
            self.closed = True

    updates.checked(db, NOW, release())
    me.refresh_update()
    old = me.update_popup = OpenPopup()
    me.update_popup_for = "0.84.4"
    updates.said(db, "0.84.4")
    me.refresh_update()                                            # same version: the popup stays
    assert not old.closed and me.update_popup is old
    updates.checked(db, NOW, release("0.84.5"))
    me.refresh_update()
    assert old.closed and me.update_popup is None and me.notices == ["0.84.4", "0.84.5"]


def test_banner_buttons_survive_a_narrow_window(monkeypatch):
    """Found in review: the banner packed its text first and the buttons last, and pack squeezes whatever came
    last - in a window ~760 px wide Install vanished. The buttons are packed first, and in a narrow window
    "you have X" and then "skip this version" make room (its x stays)."""
    from gui import app as appmod
    packed = []

    class W:
        def __init__(self, parent=None, text="", width=0, **k):
            self.text, self.manager, self.binds, self.kids = text, "", [], []
            self.req = {"Install": 90, "Later": 74, "✕": 24, "skip this version": 100}.get(text, 0)
            if parent is not None:
                parent.kids.append(self)

        def pack(self, **k):
            self.manager = "pack"
            packed.append(self.text)

        def pack_forget(self):
            self.manager = ""

        def winfo_manager(self):
            return self.manager

        def winfo_reqwidth(self):
            return self.req

        def winfo_children(self):
            return self.kids

        def bind(self, seq, fn, add=None):
            self.binds.append(fn)

        def grid(self, **k):
            pass

        grid_remove = configure = grid

    monkeypatch.setattr(appmod, "ctk", types.SimpleNamespace(CTkFrame=W, CTkLabel=W, CTkButton=W))
    for name in ("icon", "body", "semi"):
        monkeypatch.setattr(appmod.theme, name, lambda *a, **k: None)
    me = types.SimpleNamespace(update_close=lambda: None, update_later=lambda: None, _install=lambda: None)
    appmod.LockdownApp._build_update_banner(me, W())
    assert packed.index("Install") < packed.index("") and packed.index("Later") < packed.index("")
    assert packed[-1].startswith("you have")                      # the first thing pack squeezes
    bar = me.update_banner
    me.update_text.req = 220
    you = next(w for w in bar.kids if w.text.startswith("you have"))
    assert not any(w.text.startswith("skip") for w in bar.kids)   # 0.84.8: the x no longer skips a version
    you.req = 100
    fit = bar.binds[0]
    bar.winfo_width = lambda: 1000
    fit()
    assert you.manager == "pack"
    bar.winfo_width = lambda: 500                                  # 90+74+24+100+220+60 = 568 > 500
    fit()
    assert you.manager == ""
    assert all(w.manager == "pack" for w in bar.kids if w.text in ("Install", "Later", "✕"))
    bar.winfo_width = lambda: 1000
    fit()
    assert you.manager == "pack"                                   # wide again: back
