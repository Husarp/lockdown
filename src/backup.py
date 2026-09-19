"""Export / import everything you set up (Settings > Backup): blocked sites / apps with their rules, groups,
categories and settings - one JSON file. Runtime state (the service's clock, heartbeats, network adapter backups,
download progress, the mode that's on, an Anti-Bypass unlock) isn't included. Plus a screen-time CSV export.
Importing replaces your blocks and settings (the GUI asks for the Anti-Bypass challenge first)."""
import csv
import json
from datetime import date, timedelta

FORMAT = 1
RUNTIME_KEYS = {"service_heartbeat", "clock_offset", "clock_last_trusted", "clock_zone", "dns_filter.saved",
                "firewall_rules", "agent.exited", "protection.progress", "modes.active", "digest.last"}


def export(db) -> dict:
    items = db.list_items()
    return {
        "lockdown": FORMAT,
        "items": [{k: item[k] for k in ("id", "display_name", "target", "item_type", "source", "notify", "block_type",
                                         "app_path")} | {"rules": [_rule(r) for r in item["rules"]]} for item in items],
        "groups": [{"name": g["name"], "rules": [_rule(r) for r in g["rules"]],
                    "members": {str(i): o for i, o in g["members"].items()}} for g in db.list_groups()],
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


def load(path: str) -> dict:
    """Read and check a backup file. Raises ValueError if it isn't one."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"Couldn't read that file ({e}).") from None
    if not isinstance(data, dict) or data.get("lockdown") != FORMAT or not isinstance(data.get("items"), list):
        raise ValueError("That isn't a Lockdown backup file.")
    return data


def restore(db, data: dict):
    """Replace blocks, groups, categories and settings with the backup's."""
    for item_id in db.item_ids():
        db.remove_item(item_id)
    for group_id in db.group_ids():
        db.remove_group(group_id)
    new_ids = {}
    for item in data["items"]:
        new_ids[str(item["id"])] = db.add_item(item["display_name"], item["target"].split(), item["item_type"],
                                               item.get("source") or "backup", item.get("rules", []),
                                               item.get("notify"), item.get("block_type"), item.get("app_path"))
    for g in data.get("groups", []):
        members = {new_ids[i]: o for i, o in g.get("members", {}).items() if i in new_ids}
        db.add_group(g["name"], g.get("rules", []), members)
    for c in data.get("categories", []):
        db.set_category(c["kind"], c["name"], c["category"])
    for key, value in data.get("settings", {}).items():
        if key not in RUNTIME_KEYS:
            db.set_setting(key, value)


def screen_time_csv(db, path: str, days: int = 365) -> int:
    """Screen time per day and app / site (minutes in front, minutes active) -> CSV. Returns the number of rows."""
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = db.conn.execute(
        "SELECT substr(minute, 1, 10) AS day, exe, site, SUM(seconds), SUM(active_seconds) FROM activity "
        "WHERE minute >= ? GROUP BY day, exe, site ORDER BY day, SUM(seconds) DESC", (since,)).fetchall()
    categories = db.categories()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "app", "site", "minutes", "active minutes", "category"])
        for day, exe, site, sec, active in rows:
            category = categories.get(("site", site)) if site else categories.get(("app", exe))
            w.writerow([day, exe, site or "", round(sec / 60, 1), round((active or 0) / 60, 1), category or ""])
    return len(rows)
