"""SQLite database layer (shared by GUI and service)."""
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import modes
from paths import DB_PATH
from rules import RESET_KEY, TIME_FMT, LimitClock, Usage, effective_rules, item_block

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocked_items (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,   -- friendly name: "Reddit", "Discord"
    target TEXT NOT NULL,         -- sites: space-separated hostnames ("x.com twitter.com"); apps: exe name ("discord.exe")
    item_type TEXT NOT NULL,      -- "site" or "app"
    block_type TEXT,
    disabled INTEGER,             -- 1 = paused: kept with its rules, but nothing is enforced              -- apps: kill, firewall, both
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
    daily_limit_min INTEGER,      -- time_limit: minutes per day / week / month (any combination)
    weekly_limit_min INTEGER,
    monthly_limit_min INTEGER,
    daily_switch_limit INTEGER,   -- switch_limit: openings per day / week / month (any combination)
    weekly_switch_limit INTEGER,
    monthly_switch_limit INTEGER,
    schedule TEXT,                -- JSON {"mode": "allow"|"block", "windows": [{"days", "start", "end"}]}
    temp_until DATETIME,          -- local time, "YYYY-MM-DD HH:MM:SS"
    allowance_min INTEGER,        -- scheduled: minutes allowed during blocked hours
    allowance_shared INTEGER,     -- group rule: 0 = each member gets its own allowance (default: one shared pot)
    switch_mode TEXT,             -- switch_limit: "visit" (launches / new visits, default) or "switch" (every switch)
    visit_gap_min INTEGER,        -- switch_limit, visit mode: minutes away before a site visit counts as new
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Groups: named sets of rules shared by their member sites/apps
CREATE TABLE IF NOT EXISTS block_groups (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    disabled INTEGER,             -- 1 = paused: its rules stop applying to every member
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS group_rules (
    id INTEGER PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES block_groups(id) ON DELETE CASCADE,
    rule_type TEXT NOT NULL,
    daily_limit_min INTEGER,      -- a group daily limit is one shared total
    schedule TEXT,
    temp_until DATETIME,
    allowance_min INTEGER,
    allowance_shared INTEGER,
    daily_switch_limit INTEGER,   -- a group opening limit is one shared total
    switch_mode TEXT,
    visit_gap_min INTEGER,
    weekly_limit_min INTEGER,
    monthly_limit_min INTEGER,
    weekly_switch_limit INTEGER,
    monthly_switch_limit INTEGER
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

-- Screen time: seconds per minute per foreground app (and site, when it's a browser)
CREATE TABLE IF NOT EXISTS activity (
    minute TEXT NOT NULL,         -- "YYYY-MM-DD HH:MM" (trusted local time)
    exe TEXT NOT NULL,            -- foreground app
    site TEXT NOT NULL DEFAULT '',-- hostname of the active tab, if the app is a browser
    seconds INTEGER NOT NULL DEFAULT 0,
    active_seconds INTEGER NOT NULL DEFAULT 0,   -- of which with keyboard/mouse input in the last 5 min
    PRIMARY KEY (minute, exe, site)
);

-- Every switch to another app / site (for "you switched to Discord 47 times today")
CREATE TABLE IF NOT EXISTS switch_events (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,           -- trusted local time
    exe TEXT,
    site TEXT
);

-- Emergency unlocks: the chosen items are not blocked until `until` (one use, however many items)
CREATE TABLE IF NOT EXISTS emergency_unlocks (
    id INTEGER PRIMARY KEY,
    started DATETIME,             -- trusted local time
    until DATETIME,
    item_ids TEXT NOT NULL,       -- JSON list
    names TEXT NOT NULL           -- JSON list of display names (kept for history / graphs)
);

-- Network log: new connections per minute, app and address (kept for 1 hour; written by the service)
CREATE TABLE IF NOT EXISTS network_log (
    minute TEXT NOT NULL,         -- "YYYY-MM-DD HH:MM" (trusted local time)
    exe TEXT NOT NULL,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    domain TEXT NOT NULL DEFAULT '',   -- from the Windows DNS cache ('' = unknown)
    count INTEGER NOT NULL DEFAULT 0,  -- connections opened in that minute
    windows INTEGER NOT NULL DEFAULT 0,  -- a Windows program (svchost etc.)
    local INTEGER NOT NULL DEFAULT 0,    -- this PC / local network
    PRIMARY KEY (minute, exe, ip, port)
);

-- What happened with reminders: breaks taken, reminders done / snoozed / "really done?" answers
CREATE TABLE IF NOT EXISTS reminder_log (
    timestamp TEXT,               -- trusted local time
    what TEXT,                    -- "break", "sleep" or a reminder id
    result TEXT
);

-- Screen-time category chosen for an app (exe) or site (hostname)
CREATE TABLE IF NOT EXISTS categories (
    kind TEXT NOT NULL,           -- "app" or "site"
    name TEXT NOT NULL,
    category TEXT NOT NULL,       -- productive, neutral, distracting
    PRIMARY KEY (kind, name)
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
              ("block_rules", "allowance_min", "INTEGER"), ("group_rules", "daily_switch_limit", "INTEGER"),
              ("block_rules", "switch_mode", "TEXT"), ("block_rules", "visit_gap_min", "INTEGER"),
              ("group_rules", "switch_mode", "TEXT"), ("group_rules", "visit_gap_min", "INTEGER"),
              ("block_rules", "allowance_shared", "INTEGER"), ("group_rules", "allowance_shared", "INTEGER"),
              ("blocked_items", "disabled", "INTEGER"), ("block_groups", "disabled", "INTEGER")]
MIGRATIONS += [(t, c, "INTEGER") for t in ("block_rules", "group_rules")
               for c in ("weekly_limit_min", "monthly_limit_min", "weekly_switch_limit", "monthly_switch_limit")]
RULE_COLUMNS = ("rule_type", "schedule", "temp_until", "daily_limit_min", "allowance_min", "allowance_shared",
                "daily_switch_limit",
                "switch_mode", "visit_gap_min", "weekly_limit_min", "monthly_limit_min", "weekly_switch_limit",
                "monthly_switch_limit")
USAGE_DAYS_LOADED = 40   # monthly limits (+ a long day after a reset-time change)


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
        from importer.popular import add_media_hosts
        add_media_hosts(self)   # one-off: youtube.com also covers googlevideo.com now (the video itself)
        import mojibake
        mojibake.repair_saved(self)   # one-off: text typed before 0.70.1, when AltGr letters arrived wrong
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
                f"INSERT INTO {table} ({owner_col}, {', '.join(RULE_COLUMNS)}) "
                f"VALUES ({', '.join('?' * (len(RULE_COLUMNS) + 1))})",
                (owner_id, *(r.get(c) for c in RULE_COLUMNS)))

    def update_item(self, item_id: int, display_name: str, targets: list[str], notify: str | None,
                    rules: list[dict], block_type: str | None = None, app_path: str | None = None,
                    disabled: bool = False):
        """Replace an item's fields and own rules."""
        with self.conn:
            self.conn.execute("UPDATE blocked_items SET display_name = ?, target = ?, notify = ?, block_type = ?, "
                              "app_path = ?, disabled = ? WHERE id = ?",
                              (display_name, " ".join(targets), notify, block_type, app_path,
                               int(bool(disabled)), item_id))
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

    def update_group(self, group_id: int, name: str, rules: list[dict], members: dict[int, dict],
                     disabled: bool = False):
        with self.conn:
            self.conn.execute("UPDATE block_groups SET name = ?, disabled = ? WHERE id = ?",
                              (name, int(bool(disabled)), group_id))
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
        """Every item (site or app) blocked at `now`: {item, reason, until, rule} - by its own rules, its groups,
        or the mode that's on (made-up items with id None for things a mode blocks that aren't on the list)."""
        now = now or datetime.now()
        usage = self.usage_lookup(now)
        groups = self.list_groups()
        items = [i for i in self.list_items() if not i["disabled"]]   # disabled = paused, nothing applies
        out = []
        for item in items:
            block = item_block(effective_rules(item, groups), now, usage)
            if block:
                out.append({"item": item, "reason": block[0], "until": block[1], "rule": block[2]})
        # a blocked category stands for everything in it: put the real sites and apps in the list, keeping the
        # category's own reason. Anything already blocked in its own right keeps that block.
        cats = [b for b in out if b["item"]["item_type"] == "category"]
        if cats:
            categories = self.categories()
            done = {(b["item"]["item_type"], b["item"]["target"].lower()) for b in out}
            for b in cats:
                # the apps it covers are closed / minimised / cut off the internet the way the category says
                for member in modes.category_members({b["item"]["target"]}, items, categories,
                                                     block_type=b["item"].get("block_type")):
                    key = (member["item_type"], member["target"].lower())
                    if key not in done:
                        done.add(key)
                        out.append({**b, "item": member})
        state = modes.active(self, now)
        if modes.blocking(state):
            done = {b["item"]["id"] for b in out}
            until = state["phase"][1] if state["phase"] else state["until"]
            rule = {"rule_type": "mode", "group": None, "mode": state["mode"]["name"]}
            for item in modes.targets(state["mode"], items, groups, self.categories()):
                unlocked = usage.unlocks.get(f"item:{item['id']}")
                if item["id"] is not None and (item["id"] in done or (unlocked and now < unlocked)):
                    continue
                out.append({"item": item, "reason": "mode", "until": until, "rule": rule})
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

    def usage_lookup(self, now: datetime) -> Usage:
        """usage(owner, bucket) -> seconds, over recently written rows; with the limit clock and active unlocks."""
        since = (now.date() - timedelta(days=USAGE_DAYS_LOADED)).isoformat()
        data = {(r["owner"], r["bucket"]): r["seconds"] for r in self.conn.execute(
            "SELECT owner, bucket, seconds FROM usage WHERE day >= ?", (since,))}
        return Usage(data, self.limit_clock(), {f"item:{i}": u for i, u in self.active_unlocks(now).items()})

    def limit_clock(self) -> LimitClock:
        return LimitClock(self.get_setting(RESET_KEY))

    # ---------- emergency unlocks ----------

    def add_unlock(self, item_ids: list[int], names: list[str], start: datetime, until: datetime):
        with self.conn:
            self.conn.execute("INSERT INTO emergency_unlocks (started, until, item_ids, names) VALUES (?, ?, ?, ?)",
                              (start.strftime(TIME_FMT), until.strftime(TIME_FMT), json.dumps(item_ids),
                               json.dumps(names)))

    def unlocks_since(self, since: datetime) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM emergency_unlocks WHERE until > ? ORDER BY started",
                                 (since.strftime(TIME_FMT),))
        return [{"started": datetime.strptime(r["started"], TIME_FMT), "until": datetime.strptime(r["until"], TIME_FMT),
                 "item_ids": json.loads(r["item_ids"]), "names": json.loads(r["names"])} for r in rows]

    def active_unlocks(self, now: datetime) -> dict[int, datetime]:
        """item id -> until, for items in an emergency unlock right now."""
        out = {}
        for u in self.unlocks_since(now):
            if u["started"] <= now:
                for i in u["item_ids"]:
                    out[i] = max(out.get(i, u["until"]), u["until"])
        return out

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

    # ---------- screen time ----------

    def add_activity(self, minute: str, exe: str, site: str, seconds: int, active_seconds: int):
        with self.conn:
            self.conn.execute(
                "INSERT INTO activity (minute, exe, site, seconds, active_seconds) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(minute, exe, site) DO UPDATE SET seconds = seconds + excluded.seconds, "
                "active_seconds = active_seconds + excluded.active_seconds",
                (minute, exe, site, seconds, active_seconds))

    def add_switch(self, timestamp: datetime, exe: str, site: str):
        with self.conn:
            self.conn.execute("INSERT INTO switch_events (timestamp, exe, site) VALUES (?, ?, ?)",
                              (timestamp.strftime(TIME_FMT), exe, site))

    def categories(self) -> dict[tuple[str, str], str]:
        return {(r[0], r[1]): r[2] for r in self.conn.execute("SELECT kind, name, category FROM categories")}

    def set_category(self, kind: str, name: str, category: str):
        with self.conn:
            self.conn.execute("INSERT INTO categories (kind, name, category) VALUES (?, ?, ?) "
                              "ON CONFLICT(kind, name) DO UPDATE SET category = excluded.category",
                              (kind, name, category))

    # ---------- network log ----------

    def add_network(self, rows: list[dict]):
        """rows: {minute, exe, ip, port, domain, count, windows, local}; counts add up, a known domain is kept."""
        with self.conn:
            self.conn.executemany(
                "INSERT INTO network_log (minute, exe, ip, port, domain, count, windows, local) "
                "VALUES (:minute, :exe, :ip, :port, :domain, :count, :windows, :local) "
                "ON CONFLICT(minute, exe, ip, port) DO UPDATE SET count = count + excluded.count, "
                "domain = CASE WHEN excluded.domain != '' THEN excluded.domain ELSE domain END", rows)

    def network_since(self, minute: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM network_log WHERE minute >= ? ORDER BY minute DESC, count DESC", (minute,))]

    def prune_network(self, before_minute: str):
        with self.conn:
            self.conn.execute("DELETE FROM network_log WHERE minute < ?", (before_minute,))

    def block_events_since(self, since: datetime) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM block_events WHERE timestamp >= ? ORDER BY id DESC", (since.strftime(TIME_FMT),))]

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

    def all_settings(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self.conn.execute("SELECT key, value FROM settings")}

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
