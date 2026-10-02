"""SQLite database layer (shared by GUI and service)."""
import functools
import json
import logging
import sqlite3
import threading
import time
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
    names TEXT NOT NULL,          -- JSON list of display names (kept for history / graphs)
    alerts INTEGER                -- 1: bedtime and break alerts are paused until `until` as well (0.84.7)
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

-- Daily roll-ups, kept forever. Per-minute / per-event detail older than retention.DETAIL_DAYS is added here and
-- deleted in the same transaction (retention.py), so a day is counted exactly once: raw rows + roll-up rows.
CREATE TABLE IF NOT EXISTS daily_activity (
    day TEXT NOT NULL,            -- "YYYY-MM-DD"
    exe TEXT NOT NULL,
    site TEXT NOT NULL DEFAULT '',
    seconds INTEGER NOT NULL DEFAULT 0,
    active_seconds INTEGER NOT NULL DEFAULT 0,
    first_minute TEXT,            -- earliest "YYYY-MM-DD HH:MM" it was recorded that day
    PRIMARY KEY (day, exe, site)
);

CREATE TABLE IF NOT EXISTS daily_switches (
    day TEXT NOT NULL,
    exe TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL DEFAULT '',
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, exe, site)
);

CREATE TABLE IF NOT EXISTS daily_block_events (
    day TEXT NOT NULL,
    item_id INTEGER,
    display_name TEXT,
    hostname TEXT,
    reason TEXT,
    count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_reminders (
    day TEXT NOT NULL,
    what TEXT,
    result TEXT,
    count INTEGER NOT NULL DEFAULT 0
);

-- Bumped (by the triggers below, in the same transaction) whenever any connection changes the blocked items or
-- their rules: list_items() reuses what it loaded until this changes - it was 45 ms with 6 000 items, every 2-5 s.
CREATE TABLE IF NOT EXISTS change_counter (
    name TEXT PRIMARY KEY,
    n INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS blocked_items_insert_count AFTER INSERT ON blocked_items BEGIN
    INSERT INTO change_counter (name, n) VALUES ('items', 1) ON CONFLICT(name) DO UPDATE SET n = n + 1;
END;
CREATE TRIGGER IF NOT EXISTS blocked_items_update_count AFTER UPDATE ON blocked_items BEGIN
    INSERT INTO change_counter (name, n) VALUES ('items', 1) ON CONFLICT(name) DO UPDATE SET n = n + 1;
END;
CREATE TRIGGER IF NOT EXISTS blocked_items_delete_count AFTER DELETE ON blocked_items BEGIN
    INSERT INTO change_counter (name, n) VALUES ('items', 1) ON CONFLICT(name) DO UPDATE SET n = n + 1;
END;
CREATE TRIGGER IF NOT EXISTS block_rules_insert_count AFTER INSERT ON block_rules BEGIN
    INSERT INTO change_counter (name, n) VALUES ('items', 1) ON CONFLICT(name) DO UPDATE SET n = n + 1;
END;
CREATE TRIGGER IF NOT EXISTS block_rules_update_count AFTER UPDATE ON block_rules BEGIN
    INSERT INTO change_counter (name, n) VALUES ('items', 1) ON CONFLICT(name) DO UPDATE SET n = n + 1;
END;
CREATE TRIGGER IF NOT EXISTS block_rules_delete_count AFTER DELETE ON block_rules BEGIN
    INSERT INTO change_counter (name, n) VALUES ('items', 1) ON CONFLICT(name) DO UPDATE SET n = n + 1;
END;
"""

# Columns added after a table was first released: (table, column, definition)
MIGRATIONS = [("blocked_items", "notify", "TEXT"), ("blocked_items", "app_path", "TEXT"),
              ("block_rules", "allowance_min", "INTEGER"), ("group_rules", "daily_switch_limit", "INTEGER"),
              ("block_rules", "switch_mode", "TEXT"), ("block_rules", "visit_gap_min", "INTEGER"),
              ("group_rules", "switch_mode", "TEXT"), ("group_rules", "visit_gap_min", "INTEGER"),
              ("block_rules", "allowance_shared", "INTEGER"), ("group_rules", "allowance_shared", "INTEGER"),
              ("blocked_items", "disabled", "INTEGER"), ("block_groups", "disabled", "INTEGER"),
              ("emergency_unlocks", "alerts", "INTEGER")]
MIGRATIONS += [(t, c, "INTEGER") for t in ("block_rules", "group_rules")
               for c in ("weekly_limit_min", "monthly_limit_min", "weekly_switch_limit", "monthly_switch_limit")]
RULE_COLUMNS = ("rule_type", "schedule", "temp_until", "daily_limit_min", "allowance_min", "allowance_shared",
                "daily_switch_limit",
                "switch_mode", "visit_gap_min", "weekly_limit_min", "monthly_limit_min", "weekly_switch_limit",
                "monthly_switch_limit")
USAGE_DAYS_LOADED = 40   # monthly limits (+ a long day after a reset-time change)

# One per hot WHERE / ORDER BY (see design/rebuild/inventory-perf.md #3-#5). activity and network_log need none: their
# primary keys start with `minute`, which is what every query on them ranges over.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_switch_events_ts ON switch_events(timestamp)",      # stats.switches, averages
    "CREATE INDEX IF NOT EXISTS idx_block_events_ts ON block_events(timestamp)",        # blocked_events(day), log
    "CREATE INDEX IF NOT EXISTS idx_usage_day ON usage(day)",                           # usage_lookup, every tick
    "CREATE INDEX IF NOT EXISTS idx_reminder_log_what_ts ON reminder_log(what, timestamp)",   # reminders.counts
    "CREATE INDEX IF NOT EXISTS idx_reminder_log_ts ON reminder_log(timestamp)",        # retention
    "CREATE INDEX IF NOT EXISTS idx_block_rules_item ON block_rules(item_id)",          # update_item, cascades
    "CREATE INDEX IF NOT EXISTS idx_block_rules_temp ON block_rules(temp_until) WHERE rule_type = 'temporary'",
    "CREATE INDEX IF NOT EXISTS idx_group_rules_group ON group_rules(group_id)",        # update_group, cascades
    "CREATE INDEX IF NOT EXISTS idx_group_rules_temp ON group_rules(temp_until) WHERE rule_type = 'temporary'",
    "CREATE INDEX IF NOT EXISTS idx_group_members_item ON group_members(item_id)",      # item delete, cleanup
    "CREATE INDEX IF NOT EXISTS idx_daily_block_events_day ON daily_block_events(day)",
    "CREATE INDEX IF NOT EXISTS idx_daily_reminders_what_day ON daily_reminders(what, day)",
]
# PRAGMA user_version of a database that has every table, column and index above. Bump it whenever SCHEMA,
# MIGRATIONS or INDEXES change: a database already at this version skips the whole migration pass on open.
SCHEMA_VERSION = 4   # 2: change_counter + its triggers (0.84.0 review); 3: media hosts for www. sites (0.84.1)
#                      4: emergency_unlocks.alerts (0.84.7)
UI_BUSY_SEC = 1.5        # the window's connection: wait at most this long for a lock (it was 10 s - a frozen window)
BUSY_SEC = 10            # everyone else (service, worker threads)
WRITE_RETRY_SEC = 10     # a structural write from the window is retried this long before it gives up (as before)
# Settings the window may keep in memory while the database is locked (written by a background thread as soon as it
# is free): only how the window looks / what it last showed. Everything else is written at once (see set_setting).
DEFERRABLE_PREFIXES = ("ui.", "dash.", "screentime.", "stats.goal_hours", "notify.last_alert", "updates.",
                       "digest.last")


def deferrable(key: str) -> bool:
    return key.startswith(DEFERRABLE_PREFIXES)

log = logging.getLogger("lockdown.db")


def _locked(error: Exception) -> bool:
    text = str(error).lower()
    return "locked" in text or "busy" in text


def _write(fn):
    """A write the window must not lose: on its connection (short busy timeout) a lock is retried for up to
    WRITE_RETRY_SEC - only for the outermost call, outside any open transaction, so nothing is half-done twice."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if not self.ui or self._write_depth or self.conn.in_transaction:
            self._write_depth += 1
            try:
                return fn(self, *args, **kwargs)
            finally:
                self._write_depth -= 1
        deadline = time.monotonic() + WRITE_RETRY_SEC
        while True:
            self._write_depth += 1
            try:
                return fn(self, *args, **kwargs)
            except sqlite3.OperationalError as e:
                if not _locked(e) or self.conn.in_transaction or time.monotonic() >= deadline:
                    raise
            finally:
                self._write_depth -= 1
            time.sleep(0.05)
    return wrapper



class Database:
    def __init__(self, path: Path = DB_PATH, ui: bool = False):
        """ui=True for the window's own connection: a short busy timeout, so a lock held by the service or a
        worker never freezes the window for 10 s. Settings written while locked are kept in memory (and read
        back from there) and written by a background thread as soon as the lock is free - never lost."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.ui = Path(path), ui
        # (the long timeout while opening: right after an update the service may be busy creating the indexes)
        self.conn = sqlite3.connect(path, timeout=BUSY_SEC, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._write_depth = 0
        self._items = None                            # (items_version, list_items() result)
        self._parsed: dict = {}                       # (key, parse) -> (raw text, parsed value)
        self._pending: dict[str, tuple] = {}          # ui: key -> (newest value, database value when it was set)
        self._pending_lock = threading.Lock()
        self._flushed = threading.Condition(self._pending_lock)
        self._flushing = False
        self._wal(BUSY_SEC)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        if ui:
            self.conn.execute(f"PRAGMA busy_timeout={int(UI_BUSY_SEC * 1000)}")

    def _wal(self, timeout: float):
        """WAL mode (persistent in the file, so normally only read here). Switching a brand-new file to WAL
        doesn't wait for SQLite's busy handler, so two processes creating it together (first start: service and
        tray) could fail with "database is locked" - wait for it here instead."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                if str(self.conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                    self.conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as e:
                if not _locked(e) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)

    def _migrate(self):
        """Create / upgrade the schema - only when PRAGMA user_version says this database hasn't had it yet, so a
        normal open is two PRAGMAs instead of ~20 queries (it is opened four or more times per session)."""
        if self.conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        self.conn.executescript(SCHEMA)
        self.conn.execute("BEGIN IMMEDIATE")   # the service may be opening it at the same moment
        try:
            for table, column, definition in MIGRATIONS:
                cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            if self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'site_usage'").fetchone():  # 0.3.x
                self.conn.execute("INSERT OR IGNORE INTO usage (owner, bucket, seconds, day) "
                                  "SELECT 'item:' || item_id, 'day:' || date, seconds, date FROM site_usage")
                self.conn.execute("DROP TABLE site_usage")
            for statement in INDEXES:
                self.conn.execute(statement)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        from importer.popular import add_media_hosts
        add_media_hosts(self)   # one-off: youtube.com also covers googlevideo.com now (the video itself)
        import mojibake
        mojibake.repair_saved(self)   # one-off: text typed before 0.70.1, when AltGr letters arrived wrong
        # one-time statistics for the new indexes (sampled, so it is quick even on a years-old database)
        self.conn.execute("PRAGMA analysis_limit=1000")
        self.conn.execute("ANALYZE")
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.conn.commit()

    def close(self):
        self.flush()
        self.conn.close()

    # ---------- blocked items ----------

    @_write
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

    @_write
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

    @_write
    def set_app_path(self, item_id: int, path: str):
        with self.conn:
            self.conn.execute("UPDATE blocked_items SET app_path = ? WHERE id = ?", (path, item_id))

    def item_ids(self) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT id FROM blocked_items")}

    @_write
    def remove_item(self, item_id: int):
        with self.conn:
            self.conn.execute("DELETE FROM blocked_items WHERE id = ?", (item_id,))

    def items_version(self) -> int | None:
        """None when it can't be told (the counter table missing from a damaged or hand-edited database): then
        list_items loads the items every time instead of failing - enforcement must never stop over a cache."""
        try:
            row = self.conn.execute("SELECT n FROM change_counter WHERE name = 'items'").fetchone()
        except sqlite3.OperationalError:
            return None
        return row[0] if row else 0

    def item_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM blocked_items").fetchone()[0]

    def list_items(self) -> list[dict]:
        """All items, each with a 'rules' list of its own rule dicts. Loaded again only when change_counter says
        some connection changed the items or rules; each call gets its own copies (callers may change them)."""
        version = self.items_version()
        cached = self._items
        if cached is None or version is None or cached[0] != version or self.conn.in_transaction:
            items = [dict(r) for r in self.conn.execute(
                "SELECT * FROM blocked_items ORDER BY display_name COLLATE NOCASE")]
            rules: dict[int, list[dict]] = {}
            for r in self.conn.execute("SELECT * FROM block_rules ORDER BY id"):
                rules.setdefault(r["item_id"], []).append(dict(r))
            for item in items:
                item["rules"] = rules.get(item["id"], [])
            if self.conn.in_transaction:   # (uncommitted: don't remember it)
                return items
            cached = self._items = (version, items)
        return [dict(item, rules=[dict(r) for r in item["rules"]]) for item in cached[1]]

    # ---------- groups ----------

    def list_groups(self) -> list[dict]:
        """All groups: {id, name, rules: [...], members: {item_id: {rule_type: extra rule on top of the group's}}}."""
        groups = {r["id"]: {**dict(r), "rules": [], "members": {}} for r in self.conn.execute(
            "SELECT * FROM block_groups ORDER BY name COLLATE NOCASE")}
        for r in self.conn.execute("SELECT * FROM group_rules ORDER BY id"):
            groups[r["group_id"]]["rules"].append(dict(r))
        for r in self.conn.execute("SELECT * FROM group_members"):
            groups[r["group_id"]]["members"][r["item_id"]] = json.loads(r["overrides"])
        return list(groups.values())

    @_write
    def add_group(self, name: str, rules: list[dict], members: dict[int, dict] | None = None) -> int:
        with self.conn:
            cur = self.conn.execute("INSERT INTO block_groups (name) VALUES (?)", (name,))
            self._write_group(cur.lastrowid, rules, members or {})
        return cur.lastrowid

    @_write
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

    @_write
    def remove_group(self, group_id: int):
        with self.conn:
            self.conn.execute("DELETE FROM block_groups WHERE id = ?", (group_id,))

    def group_ids(self) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT id FROM block_groups")}

    # ---------- evaluation ----------

    def blocks(self, now: datetime | None = None, usage: Usage | None = None, items: list[dict] | None = None,
               groups: list[dict] | None = None) -> list[dict]:
        """Every item (site or app) blocked at `now`: {item, reason, until, rule} - by its own rules, its groups,
        or the mode that's on (made-up items with id None for things a mode blocks that aren't on the list).
        usage / items / groups: what the caller already loaded for this same tick (one usage_lookup per tick)."""
        now = now or datetime.now()
        usage = usage if usage is not None else self.usage_lookup(now)
        groups = groups if groups is not None else self.list_groups()
        items = [i for i in (items if items is not None else self.list_items())
                 if not i["disabled"]]   # disabled = paused, nothing applies
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
                    # an emergency unlock on one of its sites/apps frees that one (as it does from a mode) -
                    # but not from a permanently-blocked category, which the emergency unlock never touches
                    unlocked = usage.unlocks.get(f"item:{member['id']}")
                    if member["id"] is not None and b["reason"] != "permanent" and unlocked and now < unlocked:
                        continue
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

    @_write
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

    @_write
    def add_usage(self, targets, seconds: int, day: date):
        """Add seconds to every (owner, bucket) in targets."""
        with self.conn:
            for owner, bucket in targets:
                self.conn.execute(
                    "INSERT INTO usage (owner, bucket, seconds, day) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(owner, bucket) DO UPDATE SET seconds = seconds + excluded.seconds",
                    (owner, bucket, seconds, day.isoformat()))

    @_write
    def add_tracked(self, usage=(), activity=(), switches=(), settings: dict | None = None):
        """The usage tracker's batch (monitor/usage.py, 0.84.2) in ONE transaction - one commit every couple of
        seconds instead of three or more on every tick: usage (owner, bucket, day, seconds), screen time
        (minute, exe, site, seconds, active seconds), switches (timestamp, exe, site) and settings (its
        counted-until mark, written with the time it covers so the two can never disagree)."""
        with self.conn:
            self.conn.executemany(
                "INSERT INTO usage (owner, bucket, seconds, day) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(owner, bucket) DO UPDATE SET seconds = seconds + excluded.seconds",
                [(owner, bucket, seconds, day.isoformat() if isinstance(day, date) else day)
                 for owner, bucket, day, seconds in usage])
            self.conn.executemany(
                "INSERT INTO activity (minute, exe, site, seconds, active_seconds) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(minute, exe, site) DO UPDATE SET seconds = seconds + excluded.seconds, "
                "active_seconds = active_seconds + excluded.active_seconds", list(activity))
            self.conn.executemany("INSERT INTO switch_events (timestamp, exe, site) VALUES (?, ?, ?)",
                                  [(ts.strftime(TIME_FMT), exe, site) for ts, exe, site in switches])
            for key, value in (settings or {}).items():
                self.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def usage_of(self, targets) -> dict[tuple[str, str], int]:
        """Seconds (or openings) used in each (owner, bucket) - 0 where nothing is written yet. A primary-key
        lookup each: the tracker asks for the few limits in use, not the whole usage table (usage_lookup)."""
        out = {}
        for owner, bucket in targets:
            row = self.conn.execute("SELECT seconds FROM usage WHERE owner = ? AND bucket = ?",
                                    (owner, bucket)).fetchone()
            out[(owner, bucket)] = row[0] if row else 0
        return out

    def usage_lookup(self, now: datetime) -> Usage:
        """usage(owner, bucket) -> seconds, over recently written rows; with the limit clock and active unlocks."""
        since = (now.date() - timedelta(days=USAGE_DAYS_LOADED)).isoformat()
        data = {(r["owner"], r["bucket"]): r["seconds"] for r in self.conn.execute(
            "SELECT owner, bucket, seconds FROM usage WHERE day >= ?", (since,))}
        return Usage(data, self.limit_clock(), {f"item:{i}": u for i, u in self.active_unlocks(now).items()})

    def limit_clock(self) -> LimitClock:
        return self.parsed(RESET_KEY, LimitClock)   # (a LimitClock never changes once made)

    # ---------- emergency unlocks ----------

    @_write
    def add_unlock(self, item_ids: list[int], names: list[str], start: datetime, until: datetime,
                   alerts: bool = False):
        with self.conn:
            self.conn.execute("INSERT INTO emergency_unlocks (started, until, item_ids, names, alerts) "
                              "VALUES (?, ?, ?, ?, ?)",
                              (start.strftime(TIME_FMT), until.strftime(TIME_FMT), json.dumps(item_ids),
                               json.dumps(names), int(bool(alerts))))

    def unlocks_since(self, since: datetime) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM emergency_unlocks WHERE until > ? ORDER BY started",
                                 (since.strftime(TIME_FMT),))
        return [{"started": datetime.strptime(r["started"], TIME_FMT), "until": datetime.strptime(r["until"], TIME_FMT),
                 "item_ids": json.loads(r["item_ids"]), "names": json.loads(r["names"]), "alerts": bool(r["alerts"])}
                for r in rows]

    def active_unlocks(self, now: datetime) -> dict[int, datetime]:
        """item id -> until, for items in an emergency unlock right now."""
        out = {}
        for u in self.unlocks_since(now):
            if u["started"] <= now:
                for i in u["item_ids"]:
                    out[i] = max(out.get(i, u["until"]), u["until"])
        return out

    @_write
    def add_history(self, hostname: str, display_name: str):
        with self.conn:
            self.conn.execute(
                "INSERT INTO site_history (hostname, display_name, last_used) VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(hostname) DO UPDATE SET display_name = excluded.display_name, last_used = CURRENT_TIMESTAMP",
                (hostname, display_name))

    def history(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM site_history ORDER BY last_used DESC")]

    @_write
    def clear_history(self):
        with self.conn:
            self.conn.execute("DELETE FROM site_history")

    # ---------- screen time ----------

    @_write
    def add_activity(self, minute: str, exe: str, site: str, seconds: int, active_seconds: int):
        with self.conn:
            self.conn.execute(
                "INSERT INTO activity (minute, exe, site, seconds, active_seconds) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(minute, exe, site) DO UPDATE SET seconds = seconds + excluded.seconds, "
                "active_seconds = active_seconds + excluded.active_seconds",
                (minute, exe, site, seconds, active_seconds))

    @_write
    def add_switch(self, timestamp: datetime, exe: str, site: str):
        with self.conn:
            self.conn.execute("INSERT INTO switch_events (timestamp, exe, site) VALUES (?, ?, ?)",
                              (timestamp.strftime(TIME_FMT), exe, site))

    def categories(self) -> dict[tuple[str, str], str]:
        return {(r[0], r[1]): r[2] for r in self.conn.execute("SELECT kind, name, category FROM categories")}

    @_write
    def set_category(self, kind: str, name: str, category: str):
        with self.conn:
            self.conn.execute("INSERT INTO categories (kind, name, category) VALUES (?, ?, ?) "
                              "ON CONFLICT(kind, name) DO UPDATE SET category = excluded.category",
                              (kind, name, category))

    # ---------- network log ----------

    @_write
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

    @_write
    def prune_network(self, before_minute: str):
        with self.conn:
            self.conn.execute("DELETE FROM network_log WHERE minute < ?", (before_minute,))

    def block_events_since(self, since: datetime) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            # (+id: sort the few recent rows found through the timestamp index, rather than walk the whole table
            # newest-first by id - which is what SQLite picks otherwise)
            "SELECT * FROM block_events WHERE timestamp >= ? ORDER BY +id DESC", (since.strftime(TIME_FMT),))]

    # ---------- block events ----------

    @_write
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

    @_write
    def write(self, sql: str, args=()) -> int:
        """One write statement in its own transaction (retried on the window's connection, like the methods above).
        Returns the rows changed."""
        with self.conn:
            return self.conn.execute(sql, args).rowcount

    # ---------- settings ----------
    # Read straight from the database (a primary-key SELECT costs no more than checking whether a cache is stale -
    # measured), with what the window has waiting for the lock laid over it; parsed() caches the *parsed* value per
    # distinct text, which is where the time went.

    def all_settings(self) -> dict[str, str]:
        with self._pending_lock:   # (snapshot first: the writer may commit and clear them during the SELECT)
            pending = {k: v for k, (v, _base) in self._pending.items()}
        out = {r[0]: r[1] for r in self.conn.execute("SELECT key, value FROM settings")}
        out.update(pending)
        return out

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        pending = self._pending.get(key)
        if pending is not None:
            return pending[0]
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def parsed(self, key: str, parse, default: str | None = None):
        """parse(setting text) - worked out once per distinct text, not on every call (protection lists, the clock
        offset, the limit clock). The result is shared: callers must not change it."""
        raw = self.get_setting(key, default)
        hit = self._parsed.get((key, parse))
        if hit is not None and hit[0] == raw:
            return hit[1]
        value = parse(raw)
        self._parsed[(key, parse)] = (raw, value)
        return value

    def set_setting(self, key: str, value: str):
        """On the window's connection only display settings (DEFERRABLE) may wait in memory for a lock; everything
        else - Anti-Bypass, modes, protection lists, limits, reminders ... - is written now, retried for up to
        WRITE_RETRY_SEC like any other structural write, so it can neither be lost nor land late on top of a newer
        value written elsewhere."""
        if self.ui and deferrable(key):
            with self._pending_lock:
                if self._pending or self._flushing:   # keep the order: queue behind what is already waiting
                    self._defer(key, value)
                    return
            try:
                self._store_setting(self.conn, key, value)
            except sqlite3.OperationalError as e:
                if not _locked(e) or self.conn.in_transaction:
                    raise
                with self._pending_lock:
                    self._defer(key, value)
        else:
            self._set_now(key, value)

    @_write
    def _set_now(self, key: str, value: str):
        self._store_setting(self.conn, key, value)

    @staticmethod
    def _store_setting(conn, key: str, value: str):
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def _defer(self, key: str, value: str):
        """(holding _pending_lock) Remember the value - and what the database held when it was set, so that a newer
        value written meanwhile by another connection is not overwritten - and make sure the writer is on its way."""
        old = self._pending.get(key)
        if old is not None:
            base = old[1]
        else:
            row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()   # (WAL: reads
            base = row[0] if row else None                                                          # never wait)
        self._pending[key] = (value, base)
        if not self._flushing:
            self._flushing = True
            threading.Thread(target=self._flush_pending, name="lockdown-settings-writer", daemon=True).start()

    @staticmethod
    def _write_batch(conn, batch: dict) -> list[str]:
        """Write the waiting values in one IMMEDIATE transaction - each only if the database still holds what it
        held when the value was set (compare-and-set). Returns the keys skipped because someone wrote them since."""
        skipped = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            for key, (value, base) in batch.items():
                row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                now = row[0] if row else None
                if now != base and now != value:
                    skipped.append(key)
                    continue
                conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return skipped

    def _flush_pending(self):
        """Writer thread: wait for the lock on a connection of its own and write what is pending; whatever arrives
        meanwhile goes in the next round. Any database error is retried (logged once) - a waiting value is only
        dropped by a newer one."""
        conn, logged = None, False
        try:
            conn = sqlite3.connect(self.path, timeout=BUSY_SEC, isolation_level=None)
            while True:
                with self._pending_lock:
                    batch = dict(self._pending)
                    if not batch:
                        self._flushing = False
                        self._flushed.notify_all()
                        return
                try:
                    skipped = self._write_batch(conn, batch)
                except sqlite3.OperationalError as e:
                    if not _locked(e) and not logged:
                        log.exception("Writing settings in the background failed - retrying")
                        logged = True
                    time.sleep(0.2 if _locked(e) else 1.0)
                    continue
                if skipped:
                    log.warning("Settings %s were changed elsewhere while waiting for the lock - kept the newer "
                                "value", ", ".join(skipped))
                with self._pending_lock:
                    for k, v in batch.items():
                        if self._pending.get(k) is v:
                            del self._pending[k]
        except Exception:
            log.exception("Writing settings in the background failed")
            with self._pending_lock:
                self._flushing = False
                self._flushed.notify_all()
        finally:
            if conn is not None:
                conn.close()

    def flush(self, timeout: float = 30) -> bool:
        """Wait until settings written while the database was locked are on disk (before exit / restart). If the
        writer thread is gone with values still waiting, one last synchronous attempt. True if nothing is left."""
        end = time.monotonic() + timeout
        with self._pending_lock:
            while self._flushing:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._flushed.wait(left)
            batch = dict(self._pending)
        if batch:
            try:
                self._write_batch(self.conn, batch)
            except sqlite3.Error:
                log.exception("Writing waiting settings failed")
                return False
            with self._pending_lock:
                for k, v in batch.items():
                    if self._pending.get(k) is v:
                        del self._pending[k]
        return not self._pending
