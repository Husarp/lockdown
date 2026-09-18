"""SQLite database layer (shared by GUI and service)."""
import sqlite3
from pathlib import Path

from paths import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocked_items (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,   -- friendly name: "Reddit", "Discord"
    target TEXT NOT NULL,         -- sites: space-separated hostnames ("x.com twitter.com"); apps: exe path
    item_type TEXT NOT NULL,      -- "site" or "app"
    block_type TEXT,              -- kill, firewall, both (apps only)
    note TEXT,
    source TEXT,                  -- manual, import, quick-list
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS block_rules (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES blocked_items(id) ON DELETE CASCADE,
    rule_type TEXT NOT NULL,      -- permanent, scheduled, time_limit, switch_limit, temporary
    daily_limit_min INTEGER,
    daily_switch_limit INTEGER,
    schedule TEXT,
    temp_until DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def add_site(self, display_name: str, hostnames: list[str], source: str = "manual") -> int:
        """Add a site item with a permanent rule. Returns the item id."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO blocked_items (display_name, target, item_type, source) VALUES (?, ?, 'site', ?)",
                (display_name, " ".join(hostnames), source),
            )
            self.conn.execute(
                "INSERT INTO block_rules (item_id, rule_type) VALUES (?, 'permanent')", (cur.lastrowid,)
            )
        return cur.lastrowid

    def remove_item(self, item_id: int):
        with self.conn:
            self.conn.execute("DELETE FROM blocked_items WHERE id = ?", (item_id,))

    def list_items(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT i.*, GROUP_CONCAT(r.rule_type) AS rules
               FROM blocked_items i LEFT JOIN block_rules r ON r.item_id = i.id
               GROUP BY i.id ORDER BY i.display_name COLLATE NOCASE"""
        ).fetchall()
        return [dict(r) for r in rows]

    def blocked_hostnames(self) -> list[str]:
        """Hostnames of all sites that currently have a permanent rule."""
        rows = self.conn.execute(
            """SELECT DISTINCT i.target FROM blocked_items i
               JOIN block_rules r ON r.item_id = i.id
               WHERE i.item_type = 'site' AND r.rule_type = 'permanent'"""
        ).fetchall()
        return sorted({h for row in rows for h in row["target"].split()})

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
