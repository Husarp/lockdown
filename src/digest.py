"""Weekly summary notification (Notifications page): once a week at the chosen day and time the tray app shows how
the last 7 days went - screen time vs the week before, blocked visits, the top app / site, goal days, streaks."""
from datetime import date, datetime, timedelta

import stats
from rules import DAY_NAMES

DEFAULTS = {"digest.enabled": "1", "digest.day": "6", "digest.time": "19:00"}   # Sunday 19:00
LAST_KEY = "digest.last"   # ISO week ("2026-W38") it was last shown for


def get(db, key: str) -> str:
    return db.get_setting(key, DEFAULTS[key])


def due(db, now: datetime) -> bool:
    if get(db, "digest.enabled") != "1" or now.weekday() != int(get(db, "digest.day")):
        return False
    hour, minute = map(int, get(db, "digest.time").split(":"))
    week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
    return now.time() >= now.replace(hour=hour, minute=minute).time() and db.get_setting(LAST_KEY) != week


def mark_shown(db, now: datetime):
    db.set_setting(LAST_KEY, f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}")


def summary(db, today: date, goal_sec: float | None, name_of) -> str:
    """The text: "This week: 21h 40m of screen time (-12% vs last week) ..."; name_of(kind, name) -> display name."""
    week = stats.activity(db, today - timedelta(days=6), today + timedelta(days=1))
    before = stats.activity(db, today - timedelta(days=13), today - timedelta(days=6))
    active, prev = stats.totals(week)[0], stats.totals(before)[0]
    parts = [f"This week: {stats.hm(active)} of screen time"
             + (f" ({(active - prev) / prev:+.0%} vs last week)" if prev else "")]
    blocked = sum(len(stats.blocked_events(db, today - timedelta(days=i))) for i in range(7))
    parts.append(f"{blocked} blocked visit{'s' * (blocked != 1)} turned away")
    apps, sites = stats.per_app(week), stats.per_site(week)
    if apps:
        exe, sec = apps.most_common(1)[0]
        parts.append(f"top app: {name_of('app', exe)} ({stats.hm(sec)})")
    if sites:
        site, sec = sites.most_common(1)[0]
        parts.append(f"top site: {site} ({stats.hm(sec)})")
    if goal_sec:
        per_day = stats.per_day(week)
        met = sum(1 for i in range(7) if per_day.get((today - timedelta(days=i)).isoformat(), 0) <= goal_sec)
        parts.append(f"within your goal on {met} of 7 days")
    streak = stats.streaks(db, today, goal_sec)
    if streak["no_unlock"] >= 7:
        parts.append(f"{streak['no_unlock']} days without an emergency unlock")
    return parts[0] + ". " + "; ".join(parts[1:]) + "."


def schedule_text(db) -> str:
    return f"{DAY_NAMES[int(get(db, 'digest.day'))]} at {get(db, 'digest.time')}"
