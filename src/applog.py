"""The App log: one append-only list of what Lockdown did and what happened to it - for you to read, and to send to
whoever helps you ("a complete log that I can share").

Everything is one row in the `app_events` table of config.db: (ts, source, kind, text). `source` says who wrote it:
"agent" (the tray app / window), "service" (the enforcer) or "installer" (setup / uninstall). The agent and the service
write to it from their own processes; a write is one INSERT, on an event that is rare by nature (a start, a change you
made, a problem) - never on a loop that runs every second. It is trimmed to MAX_ROWS rows / KEEP_DAYS days.

NEVER written here: the sites or apps you visit or open, the words of a keyword hit, the network log, or any secret
(an Anti-Bypass phrase, a custom phrase). Blocked visits stay in `block_events` (they are what the Dashboard counts) -
this log is about Lockdown itself. tests/test_applog.py asserts that no visit ever lands here.

Writing never raises: a log that cannot be written must not take the program down with it (record() returns False).
Standard library only; also imported by the installer, so it never imports `db` at load time.
"""
import logging
import os
import platform
import sqlite3
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta

MAX_ROWS = 20_000        # the newest this many events are kept ...
KEEP_DAYS = 90           # ... and nothing older than this
TRIM_EVERY = 250         # the trim runs on every Nth row written (and when the app starts)
MAX_TEXT = 500           # one event is one line of at most this many characters
REPORT_EVENTS = 1000     # "Copy" / "Save log" carry this many of the newest events
LOG_TAIL_LINES = 200     # and this many lines from the end of each of the two log files
REPEAT_SEC = 600         # the same error in a row is written once per this long
TS_FORMAT = "%Y-%m-%d %H:%M:%S"

DDL = """
-- What Lockdown did and what happened to it (applog.py): one row per event, newest = highest id. Never holds a visited
-- site or app, a keyword hit or a secret.
CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,        -- local time, "YYYY-MM-DD HH:MM:SS"
    source TEXT NOT NULL,    -- agent | service | installer
    kind TEXT NOT NULL,      -- see KINDS
    text TEXT NOT NULL
);
"""

# kind -> what it is; the page's filter chips are made from CHIPS below.
KINDS = {
    "start": "Lockdown or its service started", "stop": "Lockdown or its service stopped normally",
    "tray_exit": "Quit from the tray", "updated": "Updated to another version",
    "update_found": "A newer version was found", "update_install": "An update was downloaded or started",
    "update_failed": "An update could not be downloaded or installed",
    "update_check_failed": "Looking for an update got no answer", "install": "Install", "uninstall": "Uninstall",
    "change": "Something you set up was changed", "mode": "A mode was started or stopped",
    "backup_export": "A backup was saved", "backup_import": "A backup was imported",
    "problem": "An error", "crash": "A crash", "killed": "Closed without Exit (killed, crashed or power loss)",
    "watchdog": "The watchdog started Lockdown again", "window_failed": "The window could not start",
    "window_retry": "The window was tried again", "hosts_repaired": "Something edited the hosts file; it was repaired",
    "clock_changed": "The Windows clock or time zone changed", "list_failed": "A protection list could not be downloaded",
    "challenge_passed": "Anti-Bypass challenge passed", "challenge_failed": "Anti-Bypass challenge failed",
    "antibypass_change": "Anti-Bypass settings changed", "off": "Lockdown switched off", "on": "Lockdown switched on",
    "pause": "Pause my blocks", "emergency": "Emergency unlock used",
}
CHIPS = {
    "changes": ("change", "mode", "backup_import", "antibypass_change"),
    "updates": ("updated", "update_found", "update_install", "update_failed", "update_check_failed", "install",
                "uninstall"),
    "problems": ("problem", "crash", "killed", "watchdog", "window_failed", "window_retry", "hosts_repaired",
                 "clock_changed", "list_failed", "update_failed", "update_check_failed", "challenge_failed"),
    "antibypass": ("challenge_passed", "challenge_failed", "antibypass_change", "off", "on", "pause", "emergency",
                   "tray_exit", "uninstall"),
}
CHIP_NAMES = ("all",) + tuple(CHIPS)

SOURCES = ("agent", "service", "installer")
_source = ["agent"]               # who this process is (set_source)
_state = threading.local()        # .quiet: depth of quiet(); .busy: inside the log handler
_repeats: dict = {}               # error text -> [when written, times it came since]
_repeat_lock = threading.Lock()


def set_source(source: str):
    """Who this process is: "agent", "service" or "installer". The default is "agent"."""
    _source[0] = source if source in SOURCES else "agent"


def source() -> str:
    return _source[0]


@contextmanager
def quiet():
    """Nothing is recorded inside (a backup import, one big restore: it records ONE line itself instead of a line per
    setting)."""
    _state.quiet = getattr(_state, "quiet", 0) + 1
    try:
        yield
    finally:
        _state.quiet -= 1


def is_quiet() -> bool:
    return getattr(_state, "quiet", 0) > 0


def now_text() -> str:
    return datetime.now().strftime(TS_FORMAT)


def clip(text, limit: int = MAX_TEXT) -> str:
    """One line, trimmed: newlines and runs of blanks become one space; a long text ends in an ellipsis."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# ---------------------------------------------------------------- writing

def insert(conn, ts: str, source: str, kind: str, text: str) -> int:
    """One INSERT on `conn` (the caller commits unless `conn` is in autocommit); every TRIM_EVERY rows also trims."""
    cur = conn.execute("INSERT INTO app_events (ts, source, kind, text) VALUES (?, ?, ?, ?)",
                       (ts, source, kind, text))
    if cur.lastrowid % TRIM_EVERY == 0:
        trim(conn)
    return cur.lastrowid


def trim(conn, now: datetime | None = None):
    """Drop what is older than KEEP_DAYS and everything beyond the newest MAX_ROWS (rows are numbered in order)."""
    cutoff = ((now or datetime.now()) - timedelta(days=KEEP_DAYS)).strftime(TS_FORMAT)
    conn.execute("DELETE FROM app_events WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM app_events WHERE id <= (SELECT MAX(id) FROM app_events) - ?", (MAX_ROWS,))


def record(kind: str, text, db=None, source: str | None = None) -> bool:
    """Write one event. `db` is the caller's Database when it has one (the write then happens on its connection); with
    none, a short connection of its own is opened. Never raises; False when nothing was written."""
    try:
        if is_quiet() or not text:
            return False
        text = clip(text)
        src = source or _source[0]
        if db is not None:
            db.add_app_event(src, kind, text)
        else:
            _standalone(src, kind, text)
        return True
    except Exception:
        return False


def _standalone(src: str, kind: str, text: str):
    import paths
    paths.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(paths.DB_PATH, timeout=5)
    try:
        with conn:
            conn.executescript(DDL)
            insert(conn, now_text(), src, kind, text)
    finally:
        conn.close()


def record_changes(lines, db=None, kind: str = "change", limit: int = 40):
    """A list of "what changed" lines (applog_changes): one event each, at most `limit`, then one "…and N more"."""
    lines = [l for l in lines if l]
    for line in lines[:limit]:
        record(kind, line, db)
    if len(lines) > limit:
        record(kind, f"…and {len(lines) - limit} more changes", db)


# ---------------------------------------------------------------- reading

def chip_kinds(chip: str) -> tuple | None:
    """The kinds a filter chip shows (None = all of them)."""
    return CHIPS.get(chip)


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def query(conn, chip: str = "all", search: str = "", before: int | None = None, limit: int = 300) -> list[dict]:
    """The newest events first, as dicts {id, ts, source, kind, text}: those of the chip's kinds whose text (or source or
    time) holds `search`, with an id below `before` (the page asks for the next older block with it)."""
    where, args = [], []
    kinds = chip_kinds(chip)
    if kinds:
        where.append(f"kind IN ({', '.join('?' * len(kinds))})")
        args += kinds
    search = (search or "").strip()
    if search:
        where.append("(text LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\' OR ts LIKE ? ESCAPE '\\')")
        args += [_like(search)] * 3
    if before:
        where.append("id < ?")
        args.append(int(before))
    sql = "SELECT id, ts, source, kind, text FROM app_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    return [dict(zip(("id", "ts", "source", "kind", "text"), r)) for r in conn.execute(sql, (*args, int(limit)))]


def count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM app_events").fetchone()[0]


def event_line(e: dict) -> str:
    return f"{e['ts']}  [{e['source']}]  {e['text']}"


# ---------------------------------------------------------------- the report (Copy / Save log...)

def _tail(path, lines: int = LOG_TAIL_LINES, block: int = 96 * 1024) -> list[str]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - block))
            data = f.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]


# Lines of the program log that say which app Lockdown closed, or which address a connection went to: the log file is
# Lockdown's own, but the report is shared, so what you opened and where your PC connected stays out of it.
_PRIVATE_LINES = ("Blocked app ", "Force-closed blocked app", "Closed background process", "open connection(s)",
                  "Learned where ", "Firewall rule ", "QUIC (UDP 443)", "Video hosts firewalled")


def clean_tail(lines: list[str]) -> tuple[list[str], int]:
    kept = [l for l in lines if not any(p in l for p in _PRIVATE_LINES)]
    return kept, len(lines) - len(kept)


def windows_text() -> str:
    try:
        return platform.platform()
    except Exception:
        return sys.platform


def report(conn, limit: int = REPORT_EVENTS, chip: str = "all", search: str = "", with_logs: bool = True,
           when: datetime | None = None) -> str:
    """The plain text people share: Lockdown's version, Windows, the newest `limit` events (as filtered) and the ends of
    the two log files. No sites you visited, no secrets."""
    import paths
    from version import RELEASED, VERSION
    when = when or datetime.now()
    events = query(conn, chip, search, limit=limit)
    out = ["Lockdown app log", f"Saved: {when:%Y-%m-%d %H:%M:%S}", f"Lockdown version: {VERSION} (released {RELEASED})",
           f"Windows: {windows_text()}", f"Events in the log: {count(conn)}"]
    if chip != "all" or search:
        out.append(f"Filter: {chip}" + (f", containing {search!r}" if search else ""))
    out += ["", f"== Events (newest first, {len(events)} shown) =="]
    out += [event_line(e) for e in events] or ["(none)"]
    if with_logs:
        from resident import diag
        for title, path in (("lockdown.log", paths.LOG_PATH), ("lockdown-newui.log", diag.path())):
            kept, dropped = clean_tail(_tail(path))
            out += ["", f"== {title} (last {LOG_TAIL_LINES} lines) =="]
            out += kept or ["(empty or not found)"]
            if dropped:
                out.append(f"({dropped} lines about closed apps and connections left out)")
    return "\n".join(out) + "\n"


def file_name(when: datetime | None = None) -> str:
    return f"lockdown-log-{(when or datetime.now()):%Y-%m-%d}.txt"


# ---------------------------------------------------------------- the life of a process

START_ARGS = {"--tray": "hidden in the tray", "--retried": "second try after a start that failed",
              "--watchdog": "started again by the watchdog because it was not running and was not closed with Exit"}


def args_text(argv) -> str:
    """The command-line flags of a start, in words."""
    return ", ".join(START_ARGS[a] for a in argv if a in START_ARGS)


RUN_KEY = "applog.run."        # + source: when this process started, until it stops normally
VERSION_KEY = "applog.version"   # the version that last started


def process_started(db, version: str, source: str | None = None, extra: str = ""):
    """Call once when the agent or the service starts: records "updated from X to Y" (the version is not the one that
    started last), "was not closed normally last time" (the previous run never reached process_stopped - killed, crashed,
    power loss, or stopped for the update) and "started". Never raises."""
    src = source or _source[0]
    who = "Lockdown" if src == "agent" else "The Lockdown service"
    try:
        last_version = db.get_setting(VERSION_KEY, "") or ""
        started, _, was_version = (db.get_setting(RUN_KEY + src, "") or "").partition("|")
        if last_version and last_version != version:
            record("updated", f"Updated from {last_version} to {version}", db, src)
        if started:
            why = ("it was stopped for the update" if was_version and was_version != version else
                   "Windows shut down or logged off, it crashed, or something ended it")
            record("killed", f"{who} was not closed normally (last started {started}): {why}", db, src)
        record("start", f"{who} {version} started" + (f" ({extra})" if extra else ""), db, src)
        db.set_setting(RUN_KEY + src, f"{now_text()}|{version}")
        if last_version != version:
            db.set_setting(VERSION_KEY, version)
        trim_db(db)
    except Exception:
        pass


def process_stopped(db, source: str | None = None, text: str = ""):
    """Call when the agent or the service ends normally."""
    src = source or _source[0]
    try:
        record("stop", text or f"{'Lockdown' if src == 'agent' else 'The Lockdown service'} stopped", db, src)
        db.set_setting(RUN_KEY + src, "")
    except Exception:
        pass


def trim_db(db):
    try:
        with db.conn:
            trim(db.conn)
    except Exception:
        pass


# ---------------------------------------------------------------- crashes and errors (no loop: they happen once)

def _where(tb) -> str:
    frames = traceback.extract_tb(tb) if tb is not None else []
    if not frames:
        return ""
    last = frames[-1]
    return f" at {os.path.basename(last.filename)}:{last.lineno} in {last.name}"


def crash_text(exc_type, exc, tb, where: str = "") -> str:
    name = getattr(exc_type, "__name__", str(exc_type))
    return clip(f"Unexpected error{where}: {name}: {exc}{_where(tb)}", 300)


def _repeat_ok(key: str) -> bool:
    """False when the same text was written less than REPEAT_SEC ago (the count is added to the next one)."""
    now = time.monotonic()
    with _repeat_lock:
        entry = _repeats.get(key)
        if entry and now - entry[0] < REPEAT_SEC:
            entry[1] += 1
            return False
        _repeats[key] = [now, 0]
        if len(_repeats) > 200:
            for k in [k for k, v in _repeats.items() if now - v[0] >= REPEAT_SEC]:
                del _repeats[k]
        return True


def problem(text, kind: str = "problem", db=None, repeat_key: str | None = None) -> bool:
    """An error worth showing, written at most once per REPEAT_SEC per `repeat_key` (default: the text)."""
    key = repeat_key or str(text)
    if not _repeat_ok(key):
        return False
    return record(kind, text, db)


def install_crash_hooks(where: str = ""):
    """Uncaught exceptions - the main thread's and any thread's - become a "crash" event (then the normal handler runs)."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            problem(crash_text(exc_type, exc, tb, where), "crash")
        previous(exc_type, exc, tb)
    sys.excepthook = hook
    previous_thread = threading.excepthook

    def thread_hook(args):
        if args.exc_type is not SystemExit:
            problem(crash_text(args.exc_type, args.exc_value, args.exc_traceback,
                               f" in thread {getattr(args.thread, 'name', '?')}"), "crash")
        previous_thread(args)
    threading.excepthook = thread_hook


class _ErrorHandler(logging.Handler):
    """Every ERROR / exception the program logs (log.exception ...) as a "problem" event - the first line and the
    exception's short reason, not the traceback."""

    def emit(self, record_):
        if getattr(_state, "busy", False):
            return
        _state.busy = True
        try:
            text = " ".join(str(record_.getMessage()).split())
            if record_.exc_info and record_.exc_info[0] is not None:
                et, ev, tb = record_.exc_info
                text += f" ({et.__name__}: {clip(ev, 150)}{_where(tb)})"
            problem(clip(text, 400), "problem", repeat_key=" ".join(str(record_.msg).split())[:200])
        except Exception:
            pass
        finally:
            _state.busy = False


def install_error_log():
    """Mirror the errors of every `lockdown.*` logger into the log (once per process)."""
    logger = logging.getLogger("lockdown")
    if not any(isinstance(h, _ErrorHandler) for h in logger.handlers):
        handler = _ErrorHandler(logging.ERROR)
        logger.addHandler(handler)
