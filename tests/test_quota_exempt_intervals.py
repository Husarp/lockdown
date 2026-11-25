from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import pause
import quota_accounting as quota
import rules
from blocker import apps
from db import Database
from monitor import usage
from service import Enforcer

from tests.test_usage_counting import Clock, counting, play

RESET = datetime(2026, 6, 1, 3)  # Monday and the first of the month
TIME_LIMIT = {"rule_type": "time_limit", "daily_limit_min": 5, "weekly_limit_min": 15, "monthly_limit_min": 60}
VISITS = {"rule_type": "switch_limit", "switch_mode": "visit", "daily_switch_limit": 3}
SWITCHES = {"rule_type": "switch_limit", "switch_mode": "switch", "daily_switch_limit": 3}


def spent(db, item, period, at, kind="item"):
    return db.usage_of([(f"{kind}:{item}", rules.time_bucket(period, at, db.limit_clock()))]).popitem()[1]


def raw_screen_time(db):
    return db.conn.execute("SELECT coalesce(sum(seconds), 0) FROM activity").fetchone()[0]


@pytest.mark.parametrize("exempt", [False, True])
def test_split_at_daily_weekly_and_monthly_boundary(tmp_path, monkeypatch, exempt):
    db = Database(tmp_path / "db")
    db.set_setting(rules.RESET_KEY, '{"time":"03:00"}')
    item = db.add_item("Video", ["video.example"], "site", rules=[TIME_LIMIT])
    if exempt:
        db.add_unlock([item], ["Video"], RESET - timedelta(minutes=3), RESET + timedelta(minutes=3))
    clock = Clock(monkeypatch, RESET - timedelta(seconds=2))
    tracker = counting(clock)
    play(db, tracker, clock, 4, 4, lambda: ("browser.exe", "https://video.example", 0))
    assert raw_screen_time(db) == 4
    for period in ("day", "week", "month"):
        assert spent(db, item, period, RESET - timedelta(seconds=1)) == (0 if exempt else 2)
        assert spent(db, item, period, RESET) == (0 if exempt else 2)


def test_emergency_through_reset_preserves_full_next_day_then_counts(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    db.set_setting(rules.RESET_KEY, '{"time":"03:00"}')
    item = db.add_item("Video", ["video.example"], "site", rules=[TIME_LIMIT, VISITS])
    start, end = RESET - timedelta(hours=1), RESET + timedelta(minutes=20)
    # An emergency tier can last up to 60 minutes. Two real-sized grants cover the 80-minute example.
    db.add_unlock([item], ["Video"], start, RESET)
    db.add_unlock([item], ["Video"], RESET - timedelta(minutes=10), end)
    clock = Clock(monkeypatch, start)
    tracker = counting(clock)
    play(db, tracker, clock, 80 * 60, 60, lambda: ("browser.exe", "video.example", 0))
    assert clock.now == end and spent(db, item, "day", end) == 0
    play(db, tracker, clock, 60, 60, lambda: ("browser.exe", "video.example", 0))
    assert spent(db, item, "day", end) == 60
    assert raw_screen_time(db) == 81 * 60
    assert not any(bucket.startswith("op:") for _, bucket in db.usage_lookup(end).data)
    assert len(db.unlocks_since(start)) == 2


def test_pause_replacement_and_trusted_resume_between_ticks_survive_reopen(tmp_path, monkeypatch):
    path = tmp_path / "db"
    db = Database(path)
    item = db.add_item("Game", ["game.exe"], "app", rules=[TIME_LIMIT, VISITS])
    clock = Clock(monkeypatch, RESET)
    tracker = counting(clock)
    pause.start(db, RESET + timedelta(seconds=0.75), 30, False)
    pause.start(db, RESET + timedelta(seconds=1.25), 60, False)
    monkeypatch.setattr("trusted_time.now_from_db", lambda _db: RESET + timedelta(seconds=2.75))
    pause.resume(db)
    db.close()
    db = Database(path)
    assert pause.state(db, RESET + timedelta(seconds=3)) is None
    play(db, tracker, clock, 4, 4, lambda: ("game.exe", None, 0))
    assert spent(db, item, "day", clock.now) == 2
    assert raw_screen_time(db) == 4
    assert list(db.quota_pauses(RESET, clock.now)) == [
        (RESET + timedelta(seconds=.75), RESET + timedelta(seconds=1.25)),
        (RESET + timedelta(seconds=1.25), RESET + timedelta(seconds=2.75)),
    ]


def test_midnight_alias_and_exact_emergency_end_for_openings(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    t = RESET.replace(hour=0)
    item = db.add_item("Video", ["video.example"], "site", rules=[TIME_LIMIT, VISITS])
    db.add_unlock([item], ["Video"], t - timedelta(seconds=1), t + timedelta(seconds=1))
    clock = Clock(monkeypatch, t - timedelta(seconds=2))
    tracker = counting(clock)
    play(db, tracker, clock, 3, 3, lambda: ("browser.exe", "video.example", 0))
    assert spent(db, item, "day", t - timedelta(days=1)) == 1
    assert spent(db, item, "day", t) == 0
    assert [value for (_, key), value in db.usage_lookup(clock.now).data.items() if key.startswith("op:")] == [1]
    assert raw_screen_time(db) == 3


def test_member_exemption_flows_through_category_and_shared_pot(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    video = db.add_item("Video", ["video.example"], "site", rules=[TIME_LIMIT, SWITCHES])
    game = db.add_item("Game", ["game.exe"], "app", rules=[TIME_LIMIT])
    category = db.add_item("Distracting", ["distracting"], "category", rules=[TIME_LIMIT])
    group = db.add_group("Shared", [TIME_LIMIT], {video: {}, game: {}, category: {}})
    db.add_unlock([video], ["Video"], RESET, RESET + timedelta(seconds=6))
    clock = Clock(monkeypatch, RESET)
    tracker = counting(clock)
    foreground = ("browser.exe", "video.example", 0)
    play(db, tracker, clock, 2, 2, lambda: foreground)
    assert spent(db, category, "day", clock.now) == 0
    play(db, tracker, clock, 2, 2, lambda: (*foreground, None, 5, [("game.exe", None, None, 6)]))
    tracker.flush(db)
    assert spent(db, video, "day", clock.now) == 0
    assert spent(db, game, "day", clock.now) == 2
    assert spent(db, category, "day", clock.now) == 2
    assert spent(db, group, "day", clock.now, "group") == 2
    assert tracker.in_use == {video, game, category}


@pytest.mark.parametrize("shared", [False, True])
def test_allowed_hours_boundary_member_extra_and_overlapping_pause(tmp_path, monkeypatch, shared):
    db = Database(tmp_path / "db")
    item = db.add_item("Game", ["game.exe"], "app", rules=[])
    schedule = rules.make_schedule("allow", [(list(range(7)), "02:00", "03:00")])
    group = db.add_group("Hours", [{"rule_type": "scheduled", "schedule": schedule, "allowance_min": 2,
                                    "allowance_shared": shared}],
                         {item: {"time_limit": {"daily_limit_min": 2}}})
    start = RESET - timedelta(seconds=2)
    db.add_unlock([item], ["Game"], RESET + timedelta(seconds=1), RESET + timedelta(seconds=3))
    pause.start(db, RESET + timedelta(seconds=2), 30, False)
    pause.resume(db, RESET + timedelta(seconds=4))
    clock = Clock(monkeypatch, start)
    tracker = counting(clock)
    play(db, tracker, clock, 7, 7, lambda: ("game.exe", None, 0))
    owner = f"group:{group}" if shared else f"item:{item}"
    assert [v for (o, k), v in db.usage_lookup(clock.now).data.items() if o == owner and k.startswith("win:")] == [2]
    assert spent(db, item, "day", clock.now) == 4
    assert raw_screen_time(db) == 7


def test_service_tray_race_and_unrelated_site_share_only_payable_seconds(tmp_path, monkeypatch):
    path = tmp_path / "db"
    tray, service = Database(path), Database(path)
    game = tray.add_item("Game", ["game.exe"], "app", rules=[TIME_LIMIT, VISITS])
    site = tray.add_item("Site", ["site.example"], "site", rules=[TIME_LIMIT])
    group = tray.add_group("Shared", [TIME_LIMIT], {game: {}, site: {}})
    start = RESET - timedelta(seconds=14)
    tray.set_setting(quota.COUNTED_KEY, str(start.timestamp()))
    tray.add_unlock([game], ["Game"], RESET - timedelta(seconds=6), RESET)
    monkeypatch.setattr(apps, "list_processes_full", lambda: [(10, 1, "game.exe")])
    enforcer = object.__new__(Enforcer)
    enforcer.db = service
    clock = Clock(monkeypatch, start)
    tracker = counting(clock)
    clock.now = RESET
    quota.count_unwatched_apps(enforcer, clock.now)
    assert spent(tray, group, "day", clock.now, "group") == 8
    assert not service.commit_quota(quota.COUNTED_KEY, clock.now + timedelta(seconds=1), [], [],
                                    service_mark=str(start.timestamp()))
    clock += 2
    tracker.tick(tray, lambda: ("browser.exe", "site.example", 0, None, 5, [("game.exe", None, None, 10)]),
                 lambda: {"browser.exe", "game.exe"})
    tracker.flush(tray)
    assert spent(tray, game, "day", clock.now) == 10
    assert spent(tray, site, "day", clock.now) == 16
    assert spent(tray, group, "day", clock.now, "group") == 16


def test_alerts_only_and_forged_settings_create_no_exempt_credit(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    item = db.add_item("Game", ["game.exe"], "app", rules=[TIME_LIMIT])
    db.add_unlock([], [], RESET, RESET + timedelta(minutes=5), alerts=True)
    db.add_unlock([item], ["Game"], RESET, RESET + timedelta(days=30))
    db.set_setting(pause.KEY, '{"started":"2026-06-01 03:00:00","until":"2026-06-01 04:00:00"}')
    clock = Clock(monkeypatch, RESET)
    tracker = counting(clock)
    play(db, tracker, clock, 2, 2, lambda: ("game.exe", None, 0))
    assert spent(db, item, "day", clock.now) == 2


def test_desktop_uses_one_tab_reader_with_both_roles(monkeypatch):
    import monitor
    from monitor import win

    calls = []

    class Reader:
        def read(self, hwnd, exe, *, role, refresh=False):
            calls.append((hwnd, exe, role))
            return SimpleNamespace(url=None, host=f"{hwnd}.example")

    monkeypatch.setattr(monitor, "browser_catalog", SimpleNamespace(identify=lambda exe, path: True), raising=False)
    monkeypatch.setattr(monitor, "tab_reader", SimpleNamespace(TabReader=Reader), raising=False)
    monkeypatch.setattr(usage, "_tab_local", SimpleNamespace())
    monkeypatch.setattr(win, "foreground_process", lambda: (1, "browser.exe", "browser.exe"))
    monkeypatch.setattr(win, "session_locked", lambda: False)
    monkeypatch.setattr(win, "window_pid", lambda hwnd: 10)
    monkeypatch.setattr(win, "front_windows", lambda: [(2, 20, "other.exe", "")])
    assert usage.sense_desktop() == ("browser.exe", "1.example", 0, "browser.exe", 10,
                                     [("other.exe", "2.example", None, 20)])
    assert calls == [(2, "other.exe", "visible"), (1, "browser.exe", "foreground")]


def test_custom_reset_hold_keeps_paid_time_in_its_own_bucket(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    rollover = RESET.replace(second=17)
    db.set_setting(rules.RESET_KEY, '{"time":"03:00","day_start":"2026-05-31 00:00:00",'
                   '"switch":"2026-06-01 03:00:17","hold":{"week":["w2026-05-25","2026-06-01 03:00:17"],'
                   '"month":["m2026-05","2026-06-01 03:00:17"]}}')
    item = db.add_item("Game", ["game.exe"], "app", rules=[TIME_LIMIT])
    clock = Clock(monkeypatch, rollover - timedelta(seconds=2))
    tracker = counting(clock)
    play(db, tracker, clock, 4, 4, lambda: ("game.exe", None, 0))
    for period in ("day", "week", "month"):
        assert spent(db, item, period, rollover - timedelta(seconds=1)) == 2
        assert spent(db, item, period, rollover) == 2


def test_paused_launches_and_switches_advance_baselines(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    game = db.add_item("Game", ["game.exe"], "app", rules=[VISITS])
    video = db.add_item("Video", ["video.example"], "site", rules=[SWITCHES])
    clock = Clock(monkeypatch, RESET)
    tracker = counting(clock)
    pause.start(db, RESET, 30, False)
    pause.resume(db, RESET + timedelta(seconds=3))
    play(db, tracker, clock, 1, 1, lambda: ("code.exe", None, 0), lambda: set())
    play(db, tracker, clock, 1, 1, lambda: ("game.exe", "video.example", 0), lambda: {"game.exe"})
    play(db, tracker, clock, 2, 1, lambda: ("game.exe", "video.example", 0), lambda: {"game.exe"})
    assert not any(bucket.startswith("op:") for _, bucket in db.usage_lookup(clock.now).data)
    play(db, tracker, clock, 1, 1, lambda: ("code.exe", None, 0), lambda: set())
    play(db, tracker, clock, 1, 1, lambda: ("game.exe", "video.example", 0), lambda: {"game.exe"})
    usage_data = db.usage_lookup(clock.now).data
    for item in (game, video):
        assert [n for (owner, bucket), n in usage_data.items()
                if owner == f"item:{item}" and bucket.startswith("op:")] == [1]
    assert db.conn.execute("SELECT COUNT(*) FROM switch_events").fetchone()[0] == 3


def test_category_emergency_does_not_waive_independent_item_limits(tmp_path, monkeypatch):
    db = Database(tmp_path / "db")
    game = db.add_item("Game", ["game.exe"], "app", rules=[TIME_LIMIT])
    category = db.add_item("Distracting", ["distracting"], "category", rules=[TIME_LIMIT])
    db.add_unlock([category], ["Distracting"], RESET, RESET + timedelta(minutes=10))
    clock = Clock(monkeypatch, RESET)
    tracker = counting(clock)
    play(db, tracker, clock, 2, 2, lambda: ("game.exe", None, 0))
    assert spent(db, game, "day", clock.now) == 2
    assert spent(db, category, "day", clock.now) == 0


def test_upgrade_seeds_the_saved_pause_without_refunding_spend(tmp_path, monkeypatch):
    path = tmp_path / "db"
    db = Database(path)
    app = db.add_item("Game", ["game.exe"], "app", rules=[TIME_LIMIT])
    db.add_usage([(f"item:{app}", rules.day_bucket(RESET))], 17, RESET.date())
    db.conn.execute("DROP TABLE quota_pauses")
    db.set_setting(pause.KEY, '{"started":"2026-06-01 02:58:00","until":"2026-06-01 03:15:00"}')
    db.close()
    db = Database(path)
    clock = Clock(monkeypatch, RESET)
    tracker = counting(clock)
    play(db, tracker, clock, 2, 2, lambda: ("game.exe", None, 0))
    assert spent(db, app, "day", clock.now) == 17


def test_delayed_tray_retry_deduplicates_old_service_coverage(tmp_path, monkeypatch):
    path = tmp_path / "db"
    tray, service = Database(path), Database(path)
    app = tray.add_item("Game", ["game.exe"], "app", rules=[TIME_LIMIT])
    key = f"item:{app}", rules.day_bucket(RESET)
    tray.commit_quota(quota.COUNTED_KEY, RESET, [], [])
    monkeypatch.setattr(apps, "list_processes_full", lambda: [(10, 1, "game.exe")])
    enforcer = object.__new__(Enforcer)
    enforcer.db = service
    for elapsed in (12, 60, 120, 180, 240, 300):
        quota.count_unwatched_apps(enforcer, RESET + timedelta(seconds=elapsed))
    assert tray.conn.execute("SELECT COUNT(*) FROM quota_service_spans").fetchone()[0] <= 3
    later = RESET + timedelta(seconds=302)
    tray.commit_quota(quota.COUNTED_KEY, later, [
        (key, RESET, RESET + timedelta(seconds=1)),
        (key, RESET + timedelta(seconds=298), later),
    ], [])
    assert spent(tray, app, "day", later) == 302
