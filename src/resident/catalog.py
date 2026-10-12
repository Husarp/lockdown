"""The installed-apps catalog: every Start Menu app and Steam game (monitor/applist.list_apps), ready BEFORE anyone opens
a picker. It is kept on disk (DATA_DIR/app_catalog.json) for 24 hours, so a normal start only reads a file, and it is
refreshed on a background thread of its own (below normal priority) a little after the agent starts. Searching it is a
plain in-memory loop over a prepared index: a few milliseconds even for 1 500 apps.

Also here: the one-off pass that puts the owner's Steam games into the Games category (distracting.seed_games - the old
window did it five seconds after opening), and `path_for(exe)` for the icon service (where a program lives).

Apps that are open right now (the "Running" filter, the RUNNING mark) come from a window scan at most every few
seconds; apps that are running but not in the catalog yet (just installed) are added to the answer.
"""
import json
import queue
import re
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

import search

TTL_SEC = 24 * 3600
START_DELAY_SEC = 20       # the first refresh waits for the window to hydrate
RUNNING_SEC = 4            # the open-windows scan is redone when older than this
MAX_SHOWN = 60
MAX_QUERY = 100
FILTERS = ("all", "running", "games")
FORMAT = 1
_WORD = re.compile(r"[a-z0-9]+")


def _mask(word: str) -> int:
    """Which of a-z / 0-9 a word contains, one bit each. Two words with at most d edits between them differ in at most
    d letters, so a word that lacks more than d letters of the query can be skipped without measuring the typo."""
    bits = 0
    for c in word:
        bits |= 1 << (ord(c) - 97 if c >= "a" else 26 + ord(c) - 48)
    return bits


class _Entry:
    """One app plus what searching needs, worked out once."""
    __slots__ = ("app", "name_n", "exe_n", "text", "tokens", "sort_name")

    def __init__(self, app: dict):
        self.app = app
        self.name_n = " ".join(_WORD.findall(search.normalize(app["name"])))
        self.exe_n = search.normalize(app["exe"])
        self.text = search.normalize(f"{app['name']} {app['exe']}" + (" steam" if app.get("steam") else ""))
        self.tokens = [(t, _mask(t)) for t in _WORD.findall(self.text)]
        self.sort_name = app["name"].lower()

    def score(self, words: list[str]) -> int | None:
        """search.score(), same answers, from the prepared text."""
        total = 0
        for q in words:
            if q in self.text:
                continue
            allowed = search.typos_allowed(q)
            if not allowed:
                return None
            n, mq = len(q), _mask(q)
            best = allowed + 1
            for token, mt in self.tokens:
                if (mq & ~mt).bit_count() > allowed:
                    continue
                for cand in {token, token[:n], token[:n + 1], token[:max(n - 1, 1)]}:
                    best = min(best, search.distance(q, cand, allowed))
            if best > allowed:
                return None
            total += best
        return total


def default_path() -> Path:
    from paths import DATA_DIR
    return DATA_DIR / "app_catalog.json"


class AppCatalog:
    """`list_apps()` -> [{name, exe, path, running, steam}] and `running_now()` -> {exe: path} default to the real
    ones (Windows, imported on first use). `after_load(apps)` is called on the worker thread whenever a list has been
    loaded (cache or scan); `on_ready()` too, to tell the page. `seed_games=True` runs distracting.seed_games with the
    Steam games after each load."""

    def __init__(self, cache_path: Path | None = None, list_apps=None, running_now=None, clock=time.time,
                 on_ready=None, seed_games: bool = False, db_path=None, start_delay: float = START_DELAY_SEC):
        self.cache_path = Path(cache_path) if cache_path else default_path()
        self._list_apps = list_apps
        self._running_now = running_now
        self.clock = clock
        self.on_ready = on_ready or (lambda: None)
        self.seed_games = seed_games
        self.db_path = db_path
        self.start_delay = start_delay
        self.loaded = threading.Event()
        self._entries: list[_Entry] = []
        self._by_exe: dict[str, str] = {}      # exe -> path
        self._known: set = set()               # every exe in the catalog
        self._running: dict[str, str] = {}     # exe -> path of what is open now
        self._seen: dict[str, str] = {}        # exe -> path of every program ever seen open (per-user installs such as osu!)
        self._running_at = 0.0
        self._running_lock = threading.Lock()
        self._extras: list[_Entry] = []        # running, but not in the catalog
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self.scans = 0                         # list_apps() calls made (tests)

    # ------------------------------------------------------------------ loading
    def start(self):
        """Begin loading in the background (agent start). Returns at once."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="lockdown-catalog", daemon=True)
            self._thread.start()
        self._jobs.put("load")

    def refresh(self):
        """Scan again in the background (e.g. after installing something)."""
        self._jobs.put("scan")

    def _run(self):
        _lower_thread_priority()
        while True:
            job = self._jobs.get()
            try:
                if job == "load" and self.start_delay:
                    time.sleep(self.start_delay)
                if job == "load":
                    self.load_now()
                else:
                    self.scan_now()
            except Exception:
                pass   # the catalog stays as it was; the next refresh tries again

    def load_now(self):
        """Use the file if it is there (stale or not); scan if there is none or it is older than the TTL."""
        saved, apps = self._read_file()
        if apps is not None:
            self._set(apps)
        if apps is None or self.clock() - saved > TTL_SEC:
            self.scan_now()

    def scan_now(self):
        if self._list_apps is None:
            from monitor.applist import list_apps
            self._list_apps = list_apps
        self.scans += 1
        apps = [{"name": a["name"], "exe": a["exe"], "path": a.get("path") or "", "steam": bool(a.get("steam"))}
                for a in self._list_apps()]
        self._write_file(apps)
        self._set(apps)

    def _read_file(self) -> tuple[float, list | None]:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            apps = [{"name": str(a["name"]), "exe": str(a["exe"]), "path": str(a.get("path") or ""),
                     "steam": bool(a.get("steam"))} for a in data["apps"]]
            return float(data["saved"]), apps if data.get("v") == FORMAT else None
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0, None

    def _write_file(self, apps: list):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": FORMAT, "saved": self.clock(), "apps": apps}), encoding="utf-8")
            tmp.replace(self.cache_path)
        except OSError:
            pass   # (no disk cache this time: the next start scans again)

    def _set(self, apps: list):
        entries = [_Entry(a) for a in apps]
        self._by_exe = {a["exe"]: a["path"] for a in apps if a["path"]}
        self._entries = entries
        self._known = {a["exe"] for a in apps}
        self._extras = []
        self._running_at = 0.0
        self.loaded.set()
        if self.seed_games:
            try:
                self._seed([a["exe"] for a in apps if a["steam"]])
            except Exception:
                pass   # (seeding is retried at the next load; the list is usable anyway)
        self.on_ready()

    def _seed(self, exes: list):
        from db import Database
        from importer import distracting
        db = Database(self.db_path) if self.db_path else Database()
        try:
            distracting.seed_games(db, exes)
        finally:
            db.close()

    # ------------------------------------------------------------------ questions
    def path_for(self, exe: str) -> str | None:
        """Where a program lives: the catalog's path, an open window's, or the path stored with one of the owner's
        own app items, or where it was last seen open. Reads the database - call it from a worker thread, never from the
        GUI thread."""
        exe = exe.lower()
        path = self._by_exe.get(exe) or self._running.get(exe) or self._seen.get(exe)
        if path:
            return path
        path = self.running().get(exe)      # (an app installed per user, open right now: the first call scans the windows)
        if path:
            return path
        try:
            from db import Database
            db = Database(self.db_path, ui=True) if self.db_path else Database(ui=True)
            try:
                row = db.conn.execute("SELECT app_path FROM blocked_items WHERE item_type = 'app' AND app_path IS NOT NULL "
                                      "AND app_path != '' AND lower(target) = ? LIMIT 1", (exe,)).fetchone()
            finally:
                db.close()
            return row[0] if row else None
        except Exception:
            return None

    def running(self) -> dict:
        """{exe: path} of the programs with a window open. The first call scans at once; later calls return the last
        scan and, when it is older than RUNNING_SEC, start a new one in the background (never waiting for Win32)."""
        first = self._running_at == 0.0
        if (first or time.monotonic() - self._running_at > RUNNING_SEC) and self._running_lock.acquire(blocking=False):
            if first:
                self._rescan()
            else:
                threading.Thread(target=self._rescan, name="lockdown-running", daemon=True).start()
        return self._running

    def _rescan(self):
        """Scan the open windows (the caller holds _running_lock; released here)."""
        try:
            try:
                found = (self._running_now or _running_windows)()
            except Exception:
                found = self._running
            known = self._known
            self._extras = [_Entry({"name": PureWindowsPath(p).stem.replace("_", " ").title(), "exe": e, "path": p,
                                    "steam": False}) for e, p in found.items() if e not in known and not _hidden(e)]
            self._running = found
            if len(self._seen) < 2000:
                self._seen.update(found)
            self._running_at = time.monotonic()
        finally:
            self._running_lock.release()

    def search(self, kind: str = "all", query: str = "", limit: int = MAX_SHOWN) -> dict:
        """{apps: [{name, exe, path, running, steam}] best first (at most `limit`), total, loading}. Ranked with
        search.rank's rules (every word present, small typos fine, exact first) - an exact name or exe comes first,
        then names starting with the text; then Steam last, running first, by name. `kind`: all / running / games."""
        if kind not in FILTERS:
            kind = "all"
        query = (query or "")[:MAX_QUERY]
        if not self.loaded.is_set():
            return {"apps": [], "total": 0, "loading": True}
        running = self.running()
        entries = self._entries + self._extras
        words = _WORD.findall(search.normalize(query))
        nq = " ".join(words)
        found = []
        for e in entries:
            exe = e.app["exe"]
            if kind == "games" and not e.app["steam"] or kind == "running" and exe not in running:
                continue
            s = e.score(words) if words else 0
            if s is None:
                continue
            exact = 0 if nq and (e.name_n == nq or e.exe_n == nq or e.exe_n.rsplit(".", 1)[0] == nq) else \
                1 if nq and e.name_n.startswith(nq) else 2
            found.append((s, exact, e.app["steam"], exe not in running, e.sort_name, e))
        found.sort(key=lambda f: f[:5])
        return {"apps": [{"name": f[5].app["name"], "exe": f[5].app["exe"], "path": f[5].app["path"],
                          "running": not f[3], "steam": f[5].app["steam"]} for f in found[:limit]],
                "total": len(found), "loading": False}


# ---------------------------------------------------------------- Windows parts (guarded, imported on use)

HIDDEN = {"applicationframehost.exe", "textinputhost.exe", "systemsettings.exe", "shellexperiencehost.exe",
          "lockapp.exe", "control.exe", "mmc.exe"}   # monitor/applist.HIDDEN


def _hidden(exe: str) -> bool:
    from blocker.apps import PROTECTED
    return exe in HIDDEN or exe in PROTECTED


def _running_windows() -> dict:
    """{exe name: path} of the programs with a visible window."""
    from blocker.apps import exe_name
    from monitor import win
    found = {}
    for _hwnd, _title, path in win.top_windows():
        if path.lower().endswith(".exe"):
            found.setdefault(exe_name(path), path)
    return found


def _lower_thread_priority():
    """The catalog scan is not urgent: run it below normal priority (Windows)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentThread.restype = ctypes.c_void_p
        kernel.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)   # THREAD_PRIORITY_BELOW_NORMAL
    except Exception:
        pass


_shared: AppCatalog | None = None


def shared() -> AppCatalog:
    """The agent's one catalog (made on first use; the agent calls .start() on it)."""
    global _shared
    if _shared is None:
        _shared = AppCatalog(seed_games=True)
    return _shared
