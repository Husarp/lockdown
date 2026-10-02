"""Synthetic Lockdown databases for the retention / stats tests and scripts/bench_stats.py.

Rows go in with plain sqlite3 (bulk executemany), so the same builder works on the current code and on an older
checkout (the benchmark measures both). Deterministic: the same arguments give the same database.
"""
import json
import random
import sqlite3
from datetime import date, datetime, timedelta

APPS = ["code.exe", "chrome.exe", "discord.exe", "steam.exe", "spotify.exe", "explorer.exe", "firefox.exe",
        "slack.exe", "game.exe", "word.exe"]
BROWSERS = {"chrome.exe", "firefox.exe"}
SITES = ["youtube.com", "m.youtube.com", "www.reddit.com", "reddit.com", "old.reddit.com", "github.com",
         "news.ycombinator.com", "x.com", "mail.google.com", "docs.python.org", "twitch.tv", "www.twitch.tv",
         "wikipedia.org", "en.wikipedia.org", "stackoverflow.com"]
BLOCKED = [("YouTube", "youtube.com m.youtube.com"), ("Reddit", "reddit.com"), ("Twitch", "twitch.tv"),
           ("X", "x.com twitter.com"), ("Discord", "discord.exe"), ("Steam", "steam.exe")]
REMINDERS = ["break", "sleep", "r1", "r2", "r3"]
RESULTS = ["taken", "away", "snoozed", "dismissed", "done", "not done", "started"]


def build(path, today: date, days: int = 60, seed: int = 1, minutes_per_day: tuple[int, int] = (240, 720),
          switches_per_day: tuple[int, int] = (80, 300), blocks_per_day: tuple[int, int] = (0, 40),
          extra_items: int = 0, network_rows: int = 0, unlock_days=(3, 17, 40, 55)) -> dict:
    """Fill a database (already created by Database(path)) with `days` days of history ending today.
    Returns {"items": [ids], "now": datetime} (now = today 15:42:17)."""
    rnd = random.Random(seed)
    con = sqlite3.connect(path)
    item_ids = []
    for name, target in BLOCKED:
        kind = "app" if target.endswith(".exe") else "site"
        cur = con.execute("INSERT INTO blocked_items (display_name, target, item_type, source) VALUES (?, ?, ?, ?)",
                          (name, target, kind, "synthetic"))
        item_ids.append(cur.lastrowid)
    con.execute("INSERT INTO block_rules (item_id, rule_type, daily_limit_min, weekly_limit_min, monthly_limit_min) "
                "VALUES (?, 'time_limit', 60, 300, 900)", (item_ids[0],))
    con.execute("INSERT INTO block_rules (item_id, rule_type, daily_switch_limit, monthly_switch_limit) "
                "VALUES (?, 'switch_limit', 5, 60)", (item_ids[1],))
    con.execute("INSERT INTO block_rules (item_id, rule_type, schedule) VALUES (?, 'scheduled', ?)",
                (item_ids[2], json.dumps({"mode": "block", "windows": [{"days": list(range(7)), "start": "09:00",
                                                                         "end": "17:00"}]})))
    con.execute("INSERT INTO block_rules (item_id, rule_type) VALUES (?, 'permanent')", (item_ids[3],))
    con.execute("INSERT INTO block_rules (item_id, rule_type, temp_until) VALUES (?, 'temporary', ?)",
                (item_ids[4], f"{today + timedelta(days=2)} 10:00:00"))
    con.execute("INSERT INTO block_rules (item_id, rule_type, daily_limit_min) VALUES (?, 'time_limit', 120)",
                (item_ids[5],))
    con.executemany("INSERT INTO blocked_items (display_name, target, item_type, source) VALUES (?, ?, 'site', ?)",
                    [(f"Site {i}", f"site{i}.example", "synthetic") for i in range(extra_items)])
    con.executemany("INSERT INTO block_rules (item_id, rule_type) SELECT id, 'permanent' FROM blocked_items "
                    "WHERE display_name = ?", [(f"Site {i}",) for i in range(extra_items)])
    gid = con.execute("INSERT INTO block_groups (name) VALUES ('Fun')").lastrowid
    con.execute("INSERT INTO group_rules (group_id, rule_type, daily_limit_min, weekly_limit_min) "
                "VALUES (?, 'time_limit', 90, 400)", (gid,))
    for iid in item_ids[:3]:
        con.execute("INSERT INTO group_members (group_id, item_id, overrides) VALUES (?, ?, '{}')", (gid, iid))

    activity, switches, blocks, reminders, usage = [], [], [], [], {}
    for back in range(days - 1, -1, -1):
        day = today - timedelta(days=back)
        n = rnd.randint(*minutes_per_day) if back else min(rnd.randint(*minutes_per_day), 15 * 60)
        start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=rnd.randint(6 * 60, 10 * 60))
        exe = rnd.choice(APPS)
        for m in range(n):
            t = start + timedelta(minutes=m)
            if t.date() != day or (back == 0 and t.hour >= 15 and t.minute > 40):
                break
            if rnd.random() < 0.08:
                exe = rnd.choice(APPS)
            site = rnd.choice(SITES) if exe in BROWSERS else ""
            active = rnd.choice((60, 60, 60, 44, 0))
            activity.append((t.strftime("%Y-%m-%d %H:%M"), exe, site, 60, active))
            if rnd.random() < 0.15:   # a second app in the same minute
                other = rnd.choice(APPS)
                if other != exe:
                    activity.append((t.strftime("%Y-%m-%d %H:%M"), other, "", 20, rnd.choice((20, 0))))
        for _ in range(rnd.randint(*switches_per_day)):
            ts = datetime.combine(day, datetime.min.time()) + timedelta(seconds=rnd.randint(7 * 3600, 23 * 3600))
            if back == 0 and ts.time() > datetime(2000, 1, 1, 15, 40).time():
                continue
            app = rnd.choice(APPS)
            switches.append((ts.strftime("%Y-%m-%d %H:%M:%S"), app, rnd.choice(SITES) if app in BROWSERS else None))
        for _ in range(rnd.randint(*blocks_per_day)):
            ts = datetime.combine(day, datetime.min.time()) + timedelta(seconds=rnd.randint(0, 86399))
            if back == 0 and ts.time() > datetime(2000, 1, 1, 15, 40).time():
                continue
            k = rnd.randrange(len(BLOCKED))
            host = BLOCKED[k][1].split()[0]
            blocks.append((ts.strftime("%Y-%m-%d %H:%M:%S"), host if rnd.random() < 0.8 else "cdn." + host,
                           item_ids[k] if rnd.random() < 0.9 else None, BLOCKED[k][0],
                           rnd.choice(["limit", "permanent", "schedule"]), None))
        for _ in range(rnd.randint(3, 15)):
            ts = datetime.combine(day, datetime.min.time()) + timedelta(seconds=rnd.randint(8 * 3600, 23 * 3600))
            if back == 0 and ts.time() > datetime(2000, 1, 1, 15, 40).time():
                continue
            reminders.append((ts.strftime("%Y-%m-%d %H:%M:%S"), rnd.choice(REMINDERS), rnd.choice(RESULTS)))
        iso = day.isocalendar()
        for iid in item_ids:
            for bucket in (f"day:{day}", f"week:{iso[0]}-W{iso[1]:02d}", f"month:{day:%Y-%m}", f"open:day:{day}"):
                key = (f"item:{iid}", bucket)
                seconds, first = usage.get(key, (0, day))
                usage[key] = (seconds + rnd.randint(0, 900), first)
        usage[(f"group:{gid}", f"day:{day}")] = (rnd.randint(0, 5400), day)
    switches.sort()
    blocks.sort(key=lambda b: b[0])   # (by time only: an item_id None beside an int on the same second)
    reminders.sort()
    con.executemany("INSERT OR IGNORE INTO activity (minute, exe, site, seconds, active_seconds) VALUES (?, ?, ?, ?, ?)",
                    activity)
    con.executemany("INSERT INTO switch_events (timestamp, exe, site) VALUES (?, ?, ?)", switches)
    con.executemany("INSERT INTO block_events (timestamp, hostname, item_id, display_name, reason, until) "
                    "VALUES (?, ?, ?, ?, ?, ?)", blocks)
    con.executemany("INSERT INTO reminder_log VALUES (?, ?, ?)", reminders)
    con.executemany("INSERT INTO usage (owner, bucket, seconds, day) VALUES (?, ?, ?, ?)",
                    [(o, b, s, f.isoformat()) for (o, b), (s, f) in usage.items()])
    for back in unlock_days:
        if back < days:
            started = datetime.combine(today - timedelta(days=back), datetime.min.time()) + timedelta(hours=14)
            con.execute("INSERT INTO emergency_unlocks (started, until, item_ids, names) VALUES (?, ?, ?, ?)",
                        (started.strftime("%Y-%m-%d %H:%M:%S"),
                         (started + timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S"),
                         json.dumps([item_ids[0]]), json.dumps(["YouTube"])))
    if network_rows:
        now = datetime.combine(today, datetime.min.time()) + timedelta(hours=15, minutes=42)
        con.executemany(
            "INSERT OR IGNORE INTO network_log (minute, exe, ip, port, domain, count, windows, local) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [((now - timedelta(minutes=i % 60)).strftime("%Y-%m-%d %H:%M"), rnd.choice(APPS),
              f"10.{i // 65536 % 256}.{i // 256 % 256}.{i % 256}", 443, rnd.choice(SITES), rnd.randint(1, 9), 0, 0)
             for i in range(network_rows)])
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('stats.goal_hours', '5')")
    con.commit()
    con.close()
    return {"items": item_ids, "now": datetime.combine(today, datetime.min.time()) + timedelta(hours=15, minutes=42,
                                                                                            seconds=17)}
