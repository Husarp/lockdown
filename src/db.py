"""SQLite database layer (shared by GUI and service)."""
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from paths import DB_PATH
from rules import TIME_FMT, effective_rules, item_block

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocked_items (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,   -- friendly name: "Reddit", "Discord"
    target TEXT NOT NULL,         -- sites: space-separated hostnames ("x.com twitter.com"); apps: exe name ("discord.exe")
    item_type TEXT NOT NULL,      -- "site" or "app"
    block_type TEXT,              -- apps: kill, firewall, both
    note TEXT,
    source TEXT,                  -- manual, popular, app-browser
    notify TEXT,                  -- blocked-visit alerts override: NULL = default, 'on', 'off'
    app_path TEXT,                -- apps: full exe path (firewall rule, icon)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS block_rules (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES blocked_items(id) ON DELETE CASCADE,
    rule_type TEXT NOT NULL,      -- permanent, scheduled, time_limit, switch_limit, temporary
    daily_limit_min INTEGER,
    daily_switch_limit INTEGER,
    schedule TEXT,                -- JSON {"mode": "allow"|"block", "windows": [{"days", "start", "end"}]}
    temp_until DATETIME,          -- local time, "YYYY-MM-DD HH:MM:SS"
    allowance_min INTEGER,        -- scheduled: minutes allowed during blocked hours
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Groups: named sets of rules shared by their member sites/apps
CREATE TABLE IF NOT EXISTS block_groups (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS group_rules (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES block_groups(id) ON DELETE CASCADE,
    rule_type TEXT NOT NULL,
    daily_limit_min INTEGER,      -- a group daily limit is one shared total
    schedule TEXT,
    temp_until DATETIME,
    allowance_min INTEGER
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL REFERENCES block_groups(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES blocked_items(id) ON DELETE CASCADE,
    overrides TEXT NOT NULL DEFAULT '{}',   -- JSON {rule_type: customized rule} for this member
    PRIMARY KEY (group_id, item_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Blocked site visits / closed apps (written by the service, read by the tray agent)
CREATE TABLE IF NOT EXISTS block_events (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,           -- local time
    hostname TEXT,                -- site hostname or app exe name
    item_id INTEGER,
    display_name TEXT,
    reason TEXT,                  -- permanent, temporary, schedule, limit
    until DATETIME                -- local time, NULL = indefinitely
);

-- Seconds used, per owner ("item:<id>" / "group:<id>") and bucket ("day:<date>" / "win:<rule>:<end>")
CREATE TABLE IF NOT EXISTS usage (
    owner TEXT NOT NULL,
    bucket TEXT NOT NULL,
    seconds INTEGER NOT NULL DEFAULT 0,
    day TEXT NOT NULL,            -- date written (for loading recent rows only)
    PRIMARY KEY (owner, bucket)
);

-- Sites the user has blocked before (for suggestions; clearable)
CREATE TABLE IF NOT EXISTS site_history (
    hostname TEXT PRIMARY KEY,
    display_name TEXT,
    last_used DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# Columns added after a table was first released: (table, column, definition)
MIGRATIONS = [("blocked_items", "notify", "TEXT"), ("blocked_items", "app_path", "TEXT"),
              ("block_rules", "allowance_min", "INTEGER")]
RULE_COLUMNS = ("rule_type", "schedule", "temp_until", "daily_limit_min", "allowance_min")
USAGE_DAYS_LOADED = 2


class Database:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        for table, column, definition in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'site_usage'").fetchone():  # 0.3.x table
            self.conn.execute("INSERT OR IGNORE INTO usage (owner, bucket, seconds, day) "
                              "SELECT 'item:' || item_id, 'day:' || date, seconds, date FROM site_usage")
            self.conn.execute("DROP TABLE site_usage")
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- blocked items ----------

    def add_item(self, display_name: str, targets: list[str], item_type: str = "site", source: str = "manual",
                 rules: list[dict] | None = None, notify: str | None = None, block_type: str | None = None,
                 app_path: str | None = None) -> int:
        """Add a site/app with its own rules (may be none if it's only in groups). Returns the item id."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO blocked_items (display_name, target, item_type, source, notify, block_type, app_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (display_name, " ".join(targets), item_type, source, notify, block_type, app_path))
            self._insert_rules("block_rules", "item_id", cur.lastrowid, rules or [])
        return cur.lastrowid

    def add_site(self, display_name: str, hostnames: list[str], source: str = "manual",
                 rules: list[dict] | None = None, notify: str | None = None) -> int:
        """Add a site (default: one permanent rule)."""
        return self.add_item(display_name, hostnames, "site", source, rules or [{"rule_type": "permanent"}], notify)

    def _insert_rules(self, table: str, owner_col: str, owner_id: int, rules: list[dict]):
        for r in rules:
            self.conn.execute(
                f"INSERT INTO {table} ({owner_col}, {', '.join(RULE_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?)",
                (owner_id, *(r.get(c) for c in RULE_COLUMNS)))

    def update_item(self, item_id: int, display_name: str, targets: list[str], notify: str | None,
                    rules: list[dict], block_type: str | None = None, app_path: str | None = None):
        """Replace an item's fields and own rules."""
        with self.conn:
            self.conn.execute("UPDATE blocked_items SET display_name = ?, target = ?, notify = ?, block_type = ?, "
                              "app_path = ? WHERE id = ?",
                              (display_name, " ".join(targets), notify, block_type, app_path, item_id))
            self.conn.execute("DELETE FROM block_rules WHERE item_id = ?", (item_id,))
            self._insert_rules("block_rules", "item_id", item_id, rules)

    def set_app_path(self, item_id: int, path: str):
        with self.conn:
            self.conn.execute("UPDATE blocked_items SET app_path = ? WHERE id = ?", (path, item_id))

    def item_ids(self) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT id FROM blocked_items")}

    def remove_item(self, item_id: int):
        with self.conn:
            self.conn.execute("DELETE FROM blocked_items WHERE id = ?", (item_id,))

    def list_items(self) -> list[dict]:
        """All items, each with a 'rules' list of its own rule dicts."""
        items = [dict(r) for r in self.conn.execute(
            "SELECT * FROM blocked_items ORDER BY display_name COLLATE NOCASE")]
        rules: dict[int, list[dict]] = {}
        for r in self.conn.execute("SELECT * FROM block_rules ORDER BY id"):
            rules.setdefault(r["item_id"], []).append(dict(r))
        for item in items:
            item["rules"] = rules.get(item["id"], [])
        return items

    # ---------- groups ----------

    def list_groups(self) -> list[dict]:
        """All groups: {id, name, rules: [...], members: {item_id: {rule_type: customized rule}}}."""
        groups = {r["id"]: {**dict(r), "rules": [], "members": {}} for r in self.conn.execute(
            "SELECT * FROM block_groups ORDER BY name COLLATE NOCASE")}
        for r in self.conn.execute("SELECT * FROM group_rules ORDER BY id"):
            groups[r["group_id"]]["rules"].append(dict(r))
        for r in self.conn.execute("SELECT * FROM group_members"):
            groups[r["group_id"]]["members"][r["item_id"]] = json.loads(r["overrides"])
        return list(groups.values())

    def add_group(self, name: str, rules: list[dict], members: dict[int, dict] | None = None) -> int:
        with self.conn:
            cur = self.conn.execute("INSERT INTO block_groups (name) VALUES (?)", (name,))
            self._write_group(cur.lastrowid, rules, members or {})
        return cur.lastrowid

    def update_group(self, group_id: int, name: str, rules: list[dict], members: dict[int, dict]):
        with self.conn:
            self.conn.execute("UPDATE block_groups SET name = ? WHERE id = ?", (name, group_id))
            self.conn.execute("DELETE FROM group_rules WHERE group_id = ?", (group_id,))
            self.conn.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
            self._write_group(group_id, rules, members)

    def _write_group(self, group_id: int, rules: list[dict], members: dict[int, dict]):
        self._insert_rules("group_rules", "group_id", group_id, rules)
        for item_id, overrides in members.items():
            self.conn.execute("INSERT INTO group_members (group_id, item_id, overrides) VALUES (?, ?, ?)",
                              (group_id, item_id, json.dumps(overrides or {})))

    def remove_group(self, group_id: int):
        with self.conn:
            self.conn.execute("DELETE FROM block_groups WHERE id = ?", (group_id,))

    def group_ids(self) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT id FROM block_groups")}

    # ---------- evaluation ----------

    def blocks(self, now: datetime | None = None) -> list[dict]:
        """Every item (site or app) blocked at `now`: {item, reason, until, rule}."""
        now = now or datetime.now()
        usage = self.usage_lookup(now)
        groups = self.list_groups()
        out = []
        for item in self.list_items():
            block = item_block(effective_rules(item, groups), now, usage)
            if block:
                out.append({"item": item, "reason": block[0], "until": block[1], "rule": block[2]})
        return out

    def active_blocks(self, now: datetime | None = None) -> dict[str, dict]:
        """hostname -> {item, reason, until, rule} for every site that is blocked at `now`."""
        out = {}
        for b in self.blocks(now):
            if b["item"]["item_type"] == "site":
                for h in b["item"]["target"].split():
                    out.setdefault(h, b)
        return out

    def blocked_hostnames(self, now: datetime | None = None) -> list[str]:
        """Hostnames of all sites that are blocked at `now`."""
        return sorted(self.active_blocks(now))

    def delete_expired_temporary(self, now: datetime | None = None) -> int:
        """Remove expired temporary rules, and items left with no rules and no groups. Returns items removed."""
        now = now or datetime.now()
        cutoff = now.strftime(TIME_FMT)
        with self.conn:
            self.conn.execute("DELETE FROM block_rules WHERE rule_type = 'temporary' AND temp_until <= ?", (cutoff,))
            self.conn.execute("DELETE FROM group_rules WHERE rule_type = 'temporary' AND temp_until <= ?", (cutoff,))
            cur = self.conn.execute(
                "DELETE FROM blocked_items WHERE id NOT IN (SELECT item_id FROM block_rules) "
                "AND id NOT IN (SELECT item_id FROM group_members)")
        return cur.rowcount

    # ---------- usage + history ----------

    def add_usage(self, targets, seconds: int, day: date):
        """Add seconds to every (owner, bucket) in targets."""
        with self.conn:
            for owner, bucket in targets:
                self.conn.execute(
                    "INSERT INTO usage (owner, bucket, seconds, day) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(owner, bucket) DO UPDATE SET seconds = seconds + excluded.seconds",
                    (owner, bucket, seconds, day.isoformat()))

    def usage_lookup(self, now: datetime):
        """usage(owner, bucket) -> seconds, over recently written rows."""
        since = (now.date() - timedelta(days=USAGE_DAYS_LOADED)).isoformat()
        data = {(r["owner"], r["bucket"]): r["seconds"] for r in self.conn.execute(
            "SELECT owner, bucket, seconds FROM usage WHERE day >= ?", (since,))}
        return lambda owner, bucket: data.get((owner, bucket), 0)

    def add_history(self, hostname: str, display_name: str):
        with self.conn:
            self.conn.execute(
                "INSERT INTO site_history (hostname, display_name, last_used) VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(hostname) DO UPDATE SET display_name = excluded.display_name, last_used = CURRENT_TIMESTAMP",
                (hostname, display_name))

    def history(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM site_history ORDER BY last_used DESC")]

    def clear_history(self):
        with self.conn:
            self.conn.execute("DELETE FROM site_history")

    # ---------- block events ----------

    def add_block_event(self, hostname: str, item_id: int, display_name: str, reason: str,
                        until: datetime | None, now: datetime | None = None):
        now = now or datetime.now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO block_events (timestamp, hostname, item_id, display_name, reason, until) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now.strftime(TIME_FMT), hostname, item_id, display_name, reason,
                 until.strftime(TIME_FMT) if until else None),
            )

    def block_events_after(self, last_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM block_events WHERE id > ? ORDER BY id", (last_id,))]

    def last_block_event_id(self) -> int:
        return self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM block_events").fetchone()[0]

    # ---------- settings ----------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
