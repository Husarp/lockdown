"""Export / import everything you set up (Settings > Backup): blocked sites / apps with their rules, groups,
categories and settings - one JSON file. Runtime state (the service's clock, heartbeats, network adapter backups,
download progress, the mode that's on, an Anti-Bypass unlock) isn't included. Plus a screen-time CSV export.
Importing replaces your blocks and settings (the GUI asks for the Anti-Bypass challenge first)."""
import csv
import json
import sqlite3
from datetime import date, timedelta

import applog
import block_method
import emergency

FORMAT = 2   # 2: a group's app member's own way replaces the group's (1: it was added on top - converted on restore)
OLDEST = 1
RUNTIME_KEYS = {"service_heartbeat", "clock_offset", "clock_last_trusted", "clock_zone", "dns_filter.saved",
                "firewall_rules", "agent.exited", "protection.progress", "modes.active", "digest.last",
                "retention.last_day",   # (retention.LAST_KEY: when it last ran - machine state, not a choice)
                "usage.counted_until", "firewall_quic", "firewall_video", "apps.members",
                "pause.blocks",   # (a running "Pause my blocks" - an import neither carries one in nor ends one)
                "app.off"}        # (Lockdown switched off: switching off has its own challenge, even with none set)
# (monitor.usage.COUNTED_KEY, service.QUIC_KEY, service.VIDEO_KEY, monitor.usage.MEMBERS_KEY, pause.KEY,
#  antibypass.OFF_KEY)


def export(db) -> dict:
    items = db.list_items()
    return {
        "lockdown": FORMAT,
        "items": [{k: item[k] for k in ("id", "display_name", "target", "item_type", "source", "notify", "block_type",
                                         "app_path")} | {"rules": [_rule(r) for r in item["rules"]]} for item in items],
        "groups": [{"name": g["name"], "rules": [_rule(r) for r in g["rules"]],
                    "members": {str(i): o for i, o in g["members"].items()},
                    # how its members are blocked (0.84.11; absent / None: as each member is set)
                    "app_block": g.get("app_block"), "site_block": g.get("site_block"),
                    "member_blocks": {str(i): w for i, w in (g.get("member_blocks") or {}).items()},
                    # its picked icon and colour (absent / None: automatic)
                    "icon": g.get("icon"), "color": g.get("color")}
                   for g in db.list_groups()],
        "categories": [{"kind": k, "name": n, "category": c} for (k, n), c in db.categories().items()],
        "settings": {k: _without_unlock(k, v) for k, v in db.all_settings().items() if k not in RUNTIME_KEYS},
    }


def _without_unlock(key: str, value: str) -> str:
    """An Anti-Bypass "unlocked for 5 minutes" isn't carried over."""
    if key != "antibypass":
        return value
    try:
        return json.dumps({**json.loads(value), "unlocked_until": None})
    except ValueError:
        return value


def _rule(rule: dict) -> dict:
    from db import RULE_COLUMNS
    return {c: rule.get(c) for c in RULE_COLUMNS if rule.get(c) is not None}


def save(db, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(export(db), f, indent=1)
    applog.record("backup_export", "A backup was saved (everything you set up, in one file)", db)


def load(path: str) -> dict:
    """Read and check a backup file. Raises ValueError if it isn't one."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"Couldn't read that file ({e}).") from None
    if (not isinstance(data, dict) or data.get("lockdown") not in range(OLDEST, FORMAT + 1)
            or not isinstance(data.get("items"), list)):
        raise ValueError("That isn't a Lockdown backup file.")
    return data


def _norm(key: str, value):
    """Normalise a setting for comparison: drop the volatile bits so a diff doesn't flag them."""
    if value is None or (key == "emergency.tiers" and value == ""):   # ("": the single setting, as when missing)
        return None
    if key in ("antibypass", "protection"):
        try:
            d = json.loads(value)
            for drop in ("unlocked_until", "info", "update_now"):
                d.pop(drop, None)
            return json.dumps(d, sort_keys=True)
        except ValueError:
            return value
    return value


def diff(db, data: dict) -> list[str]:
    """A short, human review of what importing `data` would change vs the current setup (import replaces
    everything). Empty-ish backups still return at least one line."""
    lines = []
    cur_items = db.list_items()
    cur_t, new_t = {i["target"] for i in cur_items}, {i["target"] for i in data.get("items", [])}
    added, removed = len(new_t - cur_t), len(cur_t - new_t)
    if len(cur_items) != len(data.get("items", [])) or added or removed:
        extra = f" (+{added}, -{removed})" if added or removed else ""
        lines.append(f"Blocked sites & apps: {len(cur_items)} now -> {len(data.get('items', []))} after{extra}")
    cur_g = {g["name"] for g in db.list_groups()}
    new_g = {g["name"] for g in data.get("groups", [])}
    if cur_g != new_g:
        lines.append(f"Groups: {len(cur_g)} -> {len(new_g)}")
    cur_c = db.categories()
    new_c = {(c["kind"], c["name"]): c["category"] for c in data.get("categories", [])}
    if cur_c != new_c:
        lines.append(f"App / site categories: {len(cur_c)} -> {len(new_c)}")
    cur_s = {k: v for k, v in db.all_settings().items() if k not in RUNTIME_KEYS}
    new_s = data.get("settings", {})
    labels = {"antibypass": "Anti-Bypass challenge", "protection": "Protection lists / your blocklists",
              "keywords": "Safe search & blocked words", "limits.reset": "When limits reset",
              "stats.categories": "Categories", "ui.theme": "Theme", "ui.accent": "Accent colour",
              "emergency.tiers": "Emergency unlock kinds"}
    changed = {k for k in set(cur_s) | set(new_s) if _norm(k, cur_s.get(k)) != _norm(k, new_s.get(k))}
    for key, label in labels.items():
        if key in changed:
            lines.append(f"{label} will change")
            changed.discard(key)
    if changed:
        lines.append(f"{len(changed)} other setting{'s' * (len(changed) != 1)} will change")
    return lines or ["No differences - it matches your current setup."]


class _NoCommit:
    """db.conn while a restore runs: the Database's own `with conn:` blocks commit nothing, so every write of the
    restore lands in ONE transaction (restore commits it, or rolls it all back)."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


def restore(db, data: dict):
    """Replace blocks, groups, categories and settings with the backup's - all of it or nothing: a file that fails
    half-way (a damaged entry) leaves the blocks as they were instead of emptied (R28)."""
    import time
    from db import WRITE_RETRY_SEC, _locked
    conn = db.conn
    deadline = time.monotonic() + WRITE_RETRY_SEC
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as e:      # (the service is writing: wait for it, as every write does)
            if not _locked(e) or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    db.conn = _NoCommit(conn)
    try:
        with applog.quiet():     # (one line below instead of a line per setting)
            _restore(db, data)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        db.conn = conn
    applog.record("backup_import", f"Backup imported: {len(data['items'])} sites and apps, {len(data.get('groups', []))} "
                  f"groups, {len(data.get('categories', []))} category choices and the settings now replace the old ones", db)


def _restore(db, data: dict):
    for item_id in db.item_ids():
        db.remove_item(item_id)
    for group_id in db.group_ids():
        db.remove_group(group_id)
    new_ids = {}
    blocks = {}
    for item in data["items"]:
        new_ids[str(item["id"])] = db.add_item(item["display_name"], item["target"].split(), item["item_type"],
                                               item.get("source") or "backup", item.get("rules", []),
                                               item.get("notify"), item.get("block_type"), item.get("app_path"))
        blocks[new_ids[str(item["id"])]] = item.get("block_type")
    for g in data.get("groups", []):
        members = {new_ids[i]: o for i, o in g.get("members", {}).items() if i in new_ids}
        member_blocks = {new_ids[i]: w for i, w in (g.get("member_blocks") or {}).items() if i in new_ids}
        if data.get("lockdown", FORMAT) < 2:   # a backup from before an app's own way replaced the group's
            kinds = {new_ids[str(it["id"])]: it["item_type"] for it in data["items"]}
            member_blocks = {i: block_method.own_app_way(g.get("app_block"), blocks[i], w)
                             if kinds[i] != "site" else w for i, w in member_blocks.items()}
        ways = {"app_block": g.get("app_block"), "site_block": g.get("site_block"),
                "member_blocks": member_blocks}
        db.add_group(g["name"], g.get("rules", []), members, ways,
                     {k: g[k] for k in ("icon", "color") if isinstance(g.get(k), str) and len(g[k]) <= 40})
    for c in data.get("categories", []):
        db.set_category(c["kind"], c["name"], c["category"])
    settings = data.get("settings", {})
    for key, value in settings.items():
        if key not in RUNTIME_KEYS:
            db.set_setting(key, value)
    if "emergency.tiers" not in settings:   # a backup from before emergency kinds: its single setting applies
        db.set_setting("emergency.tiers", "")
        for key in ("emergency.uses", "emergency.per", "emergency.minutes"):   # (not in it: it had the default)
            if key not in settings:
                db.set_setting(key, emergency.DEFAULTS[key])


def screen_time_csv(db, path: str, days: int = 365) -> int:
    """Screen time per day and app / site (minutes in front, minutes active) -> CSV. Returns the number of rows."""
    since = (date.today() - timedelta(days=days)).isoformat()
    # the per-minute detail and, for days retention has rolled up, the daily totals (same day / app / site sums)
    rows = db.conn.execute(
        "SELECT day, exe, site, SUM(s), SUM(a) FROM ("
        " SELECT substr(minute, 1, 10) AS day, exe, site, SUM(seconds) AS s, SUM(active_seconds) AS a FROM activity"
        "  WHERE minute >= ? GROUP BY day, exe, site"
        " UNION ALL"
        " SELECT day, exe, site, seconds AS s, active_seconds AS a FROM daily_activity WHERE day >= ?"
        ") GROUP BY day, exe, site ORDER BY day, SUM(s) DESC", (since, since)).fetchall()
    categories = db.categories()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "app", "site", "minutes", "active minutes", "category"])
        for day, exe, site, sec, active in rows:
            category = categories.get(("site", site)) if site else categories.get(("app", exe))
            w.writerow([day, exe, site or "", round(sec / 60, 1), round((active or 0) / 60, 1), category or ""])
    return len(rows)
