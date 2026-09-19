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


def _norm(key: str, value):
    """Normalise a setting for comparison: drop the volatile bits so a diff doesn't flag them."""
    if value is None:
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
              "stats.categories": "Categories", "ui.theme": "Theme", "ui.accent": "Accent colour"}
    changed = {k for k in set(cur_s) | set(new_s) if _norm(k, cur_s.get(k)) != _norm(k, new_s.get(k))}
    for key, label in labels.items():
        if key in changed:
            lines.append(f"{label} will change")
            changed.discard(key)
    if changed:
        lines.append(f"{len(changed)} other setting{'s' * (len(changed) != 1)} will change")
    return lines or ["No differences - it matches your current setup."]


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
