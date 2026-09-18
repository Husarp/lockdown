"""SQLite database layer (shared by GUI and service)."""
import sqlite3
from datetime import date, datetime
from pathlib import Path

from paths import DB_PATH
from rules import TIME_FMT, item_block

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocked_items (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,   -- friendly name: "Reddit", "Discord"
    target TEXT NOT NULL,         -- sites: space-separated hostnames ("x.com twitter.com"); apps: exe path
    item_type TEXT NOT NULL,      -- "site" or "app"
    block_type TEXT,              -- kill, firewall, both (apps only)
    note TEXT,
    source TEXT,                  -- manual, import, quick-list
    notify TEXT,                  -- blocked-visit alerts override: NULL = default, 'on', 'off'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS block_rules (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES blocked_items(id) ON DELETE CASCADE,
    rule_type TEXT NOT NULL,      -- permanent, scheduled, time_limit, switch_limit, temporary
    daily_limit_min INTEGER,
    daily_switch_limit INTEGER,
    schedule TEXT,                -- JSON {"days": [0..6], "start": "21:00", "end": "07:00"}
    temp_until DATETIME,          -- local time, "YYYY-MM-DD HH:MM:SS"
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Attempts to open a blocked site (written by the service's listener, read by the tray agent)
CREATE TABLE IF NOT EXISTS block_events (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,           -- local time
    hostname TEXT,
    item_id INTEGER,
    display_name TEXT,
    reason TEXT,                  -- permanent, temporary, schedule, limit
    until DATETIME                -- local time, NULL = indefinitely
);

-- Seconds spent on a site per day (written by the tray agent, read by the service for time limits)
CREATE TABLE IF NOT EXISTS site_usage (
    date TEXT NOT NULL,           -- YYYY-MM-DD (trusted local date)
    item_id INTEGER NOT NULL,
    seconds INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, item_id)
);

-- Sites the user has blocked before (for suggestions; clearable)
CREATE TABLE IF NOT EXISTS site_history (
    hostname TEXT PRIMARY KEY,
    display_name TEXT,
    last_used DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# Columns added after a table was first released: (table, column, definition)
MIGRATIONS = [("blocked_items", "notify", "TEXT")]


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
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- blocked items ----------

    def add_site(self, display_name: str, hostnames: list[str], source: str = "manual",
                 rules: list[dict] | None = None, notify: str | None = None) -> int:
        """Add a site item with its rules (default: one permanent rule). Returns the item id.
        A rule dict has rule_type plus optional schedule / temp_until / daily_limit_min."""
        rules = rules or [{"rule_type": "permanent"}]
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO blocked_items (display_name, target, item_type, source, notify) "
                "VALUES (?, ?, 'site', ?, ?)",
                (display_name, " ".join(hostnames), source, notify),
            )
            self._insert_rules(cur.lastrowid, rules)
        return cur.lastrowid

    def _insert_rules(self, item_id: int, rules: list[dict]):
        for r in rules:
            self.conn.execute(
                "INSERT INTO block_rules (item_id, rule_type, schedule, temp_until, daily_limit_min) "
                "VALUES (?, ?, ?, ?, ?)",
                (item_id, r["rule_type"], r.get("schedule"), r.get("temp_until"), r.get("daily_limit_min")),
            )

    def update_item(self, item_id: int, display_name: str, hostnames: list[str], notify: str | None,
                    rules: list[dict]):
        """Replace an item's fields and rules."""
        with self.conn:
            self.conn.execute("UPDATE blocked_items SET display_name = ?, target = ?, notify = ? WHERE id = ?",
                              (display_name, " ".join(hostnames), notify, item_id))
            self.conn.execute("DELETE FROM block_rules WHERE item_id = ?", (item_id,))
            self._insert_rules(item_id, rules)

    def item_ids(self) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT id FROM blocked_items")}

    def remove_item(self, item_id: int):
        with self.conn:
            self.conn.execute("DELETE FROM blocked_items WHERE id = ?", (item_id,))

    def list_items(self) -> list[dict]:
        """All items, each with a 'rules' list of rule dicts."""
        items = [dict(r) for r in self.conn.execute(
            "SELECT * FROM blocked_items ORDER BY display_name COLLATE NOCASE")]
        rules: dict[int, list[dict]] = {}
        for r in self.conn.execute("SELECT * FROM block_rules ORDER BY id"):
            rules.setdefault(r["item_id"], []).append(dict(r))
        for item in items:
            item["rules"] = rules.get(item["id"], [])
        return items

    def active_blocks(self, now: datetime | None = None) -> dict[str, dict]:
        """hostname -> {item, reason, until} for every site that is blocked at `now`."""
        now = now or datetime.now()
        usage = self.usage_on(now.date())
        out = {}
        for item in self.list_items():
            if item["item_type"] != "site":
                continue
            block = item_block(item["rules"], now, usage.get(item["id"], 0))
            if block:
                for h in item["target"].split():
                    out.setdefault(h, {"item": item, "reason": block[0], "until": block[1]})
        return out

    def blocked_hostnames(self, now: datetime | None = None) -> list[str]:
        """Hostnames of all sites that are blocked at `now`."""
        return sorted(self.active_blocks(now))

    def delete_expired_temporary(self, now: datetime | None = None) -> int:
        """Remove expired temporary rules, and items left with no rules. Returns items removed."""
        now = now or datetime.now()
        with self.conn:
            self.conn.execute("DELETE FROM block_rules WHERE rule_type = 'temporary' AND temp_until <= ?",
                              (now.strftime(TIME_FMT),))
            cur = self.conn.execute(
                "DELETE FROM blocked_items WHERE id NOT IN (SELECT item_id FROM block_rules)")
        return cur.rowcount

    # ---------- usage + history ----------

    def add_usage(self, item_id: int, day: date, seconds: int):
        with self.conn:
            self.conn.execute(
                "INSERT INTO site_usage (date, item_id, seconds) VALUES (?, ?, ?) "
                "ON CONFLICT(date, item_id) DO UPDATE SET seconds = seconds + excluded.seconds",
                (day.isoformat(), item_id, seconds))

    def usage_on(self, day: date) -> dict[int, int]:
        return {r["item_id"]: r["seconds"] for r in self.conn.execute(
            "SELECT item_id, seconds FROM site_usage WHERE date = ?", (day.isoformat(),))}

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
