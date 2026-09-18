from datetime import date, datetime, timedelta

import stats
from db import Database

DAY = date(2026, 9, 14)


def at(hh, mm=0, ss=0):
    return datetime(2026, 9, 14, hh, mm, ss)


def fill(db, start, minutes, exe, site="", active=True):
    for i in range(minutes):
        t = start + timedelta(minutes=i)
        db.add_activity(t.strftime("%Y-%m-%d %H:%M"), exe, site, 60, 60 if active else 0)


def test_totals_sessions_focus_timeline(tmp_path):
    db = Database(tmp_path / "t.db")
    fill(db, at(9), 30, "code.exe")                 # 09:00-09:30 VS Code
    fill(db, at(9, 30), 10, "chrome.exe", "youtube.com")
    fill(db, at(9, 40), 10, "code.exe", active=False)   # idle
    fill(db, at(11), 20, "code.exe")                # after a break: new session
    rows = stats.activity(db, DAY, DAY + timedelta(days=1))
    assert stats.totals(rows) == (60 * 60, 70 * 60)
    assert stats.per_app(rows)["code.exe"] == 50 * 60
    assert stats.per_site(rows) == {"youtube.com": 600}
    assert [(s.hour, s.minute, e.hour, e.minute) for s, e in stats.sessions(rows)] == [(9, 0, 9, 40), (11, 0, 11, 20)]
    assert stats.longest_focus(rows) == (30 * 60, "code.exe", at(9))
    cat = lambda exe, site: "distracting" if site else "productive"
    assert stats.timeline(rows, DAY, cat) == [(540, 30, "productive"), (570, 10, "distracting"), (580, 10, "idle"),
                                              (660, 20, "productive")]
    assert stats.heatmap(rows, [DAY])[0][9] == 3 and stats.heatmap(rows, [DAY])[0][11] == 2


def test_switches_and_visits():
    events = [{"ts": at(10), "exe": "discord.exe", "site": ""}, {"ts": at(10, 0, 20), "exe": "code.exe", "site": ""},
              {"ts": at(10, 10), "exe": "discord.exe", "site": ""}, {"ts": at(10, 10, 10), "exe": "chrome.exe",
                                                                      "site": "reddit.com"}]
    s = stats.switch_summary(events, at(10, 12, 10))
    assert s["count"] == 4 and s["short"] == 2 and s["short_top"] == [("app", "discord.exe")]
    assert s["targets"][0] == (("app", "discord.exe"), 2, 15.0)
    assert s["per_hour"] == {10: 4}
    assert stats.visit_style(38, 42) == "checking" and stats.visit_style(22, 295) == "focused"


def test_categories_default_and_saved(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_site("Reddit", ["reddit.com"])
    items = db.list_items()
    assert stats.category_of("site", "old.reddit.com", db.categories(), items) == "distracting"
    assert stats.category_of("app", "code.exe", db.categories(), items) == "neutral"
    db.set_category("app", "code.exe", "productive")
    assert stats.category_of("app", "code.exe", db.categories(), items) == "productive"


def test_time_saved_uses_usual_visit_length():
    items = [{"id": 1, "target": "reddit.com", "item_type": "site"}]
    history = [{"ts": at(8), "exe": "chrome.exe", "site": "reddit.com"}, {"ts": at(8, 4), "exe": "code.exe", "site": ""},
               {"ts": at(9), "exe": "code.exe", "site": ""}]
    events = [{"item_id": 1, "target": "reddit.com"}, {"item_id": 2, "target": "x.com"}]
    assert stats.time_saved(events, items, [], history, at(12)) == 4 * 60 + stats.DEFAULT_VISIT_SEC


def test_ranges_and_text():
    assert stats.range_dates("Today", DAY) == (DAY, DAY + timedelta(days=1))
    assert stats.range_dates("Yesterday", DAY) == (DAY - timedelta(days=1), DAY)
    assert stats.range_dates("7 days", DAY) == (DAY - timedelta(days=6), DAY + timedelta(days=1))
    assert stats.hm(4 * 3600 + 12 * 60) == "4 h 12 m" and stats.ms(106) == "1 m 46 s"


def test_average_switches_up_to_the_same_time(tmp_path):
    from datetime import time
    db = Database(tmp_path / "t.db")
    y = DAY - timedelta(days=1)
    db.add_activity(f"{y} 09:00", "code.exe", "", 60, 60)
    for hh in (8, 9, 20, 21):
        db.add_switch(datetime.combine(y, time(hh)), "code.exe", "")
    assert stats.average_daily_switches(db, DAY) == 4
    assert stats.average_daily_switches(db, DAY, until=time(10)) == 2
