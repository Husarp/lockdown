"""App and site icons for the new window, served by the asset server and never made on the page's time.

  /icon/app/<exe>.png      the program's own icon      /icon/site/<host>.png   the site's favicon
  /icon/letter/<text>.svg  a coloured letter tile

A site icon is, in this order: 1) the disk cache (DATA_DIR/icons/site_<host>.png; one older than REFRESH_SEC is sent as it
is and fetched again meanwhile), 2) the icon bundled with Lockdown (assets/sites, found through importer.popular.site_of,
so m.youtube.com and youtu.be use youtube.com's), 3) a letter tile, and a download is queued - only while the setting
"Download icons for my sites" (DOWNLOAD_KEY) is on; off = no network at all for icons. An app icon that isn't in the cache
gets a letter tile at once too. Real icons are made on background threads and the page is told with `icon_ready {key}`
("app:chrome.exe" / "site:example.com") so it swaps the <img> src: ONE thread for the programs' own icons (local and
quick), and NET_WORKERS threads for the net (resident/iconfetch.py), with a short pause between downloads. The page's
requests go before the prefetch backlog (`prefetch`: the sites on the owner's lists, resident/icon_prefetch.py).
Nothing here runs on the GUI thread; PIL is imported on a background thread only.

A name the route cannot use (a ":" or "|" in it, ".." ...) is answered with a tile too - never a 404, so no <img> shows a
broken-image glyph - and it never reaches the file system or the queue. Windows' own processes ("[system process]",
"svchost.exe", "MsMpEng.exe" ...) get a system tile (a gear, or a shield for the security ones) until a real icon is
found (%SystemRoot% and System32 are tried after the catalog); those without an .exe name are never extracted.

An app icon is taken big (up to 256 px) and kept at ICON_SIZE: from the places the app catalog knows for it (a
shortcut's icon, Steam's icon of the game, the uninstall entry's DisplayIcon - see monitor/applist.py) and the program
itself. APP_CACHE_VERSION marks the app icons on disk: older (32 px) ones are deleted once and made again when asked.
When the catalog has loaded, the app icons that failed for want of a path are tried again (`retry_apps`).

The page is untrusted: only plain names are accepted (an exe name - anything Windows allows in a file name except
path characters - or a host name; no folders, no "..", no control characters), and the page never says WHERE an exe is
- the path comes from the app catalog or the owner's own list.
"""
import collections
import hashlib
import queue
import re
import sys
import threading
import time
from html import escape
from pathlib import Path

from resident.assets import Response

PALETTE = ["#e5534b", "#c69026", "#57ab5a", "#539bf5", "#b083f0", "#dc6d1a", "#39c5cf", "#e275ad"]   # as gui/icons.py
DOWNLOAD_KEY = "icons.download"   # Settings > "Download icons for my sites": "0" = off, anything else (or none) = on
MAX_QUEUED = 64          # more distinct icons than this waiting at once: the extra ones keep their letter tile
MAX_FAILED = 2000        # remembered failures (the oldest go first)
RETRY_SEC = 600          # an icon that could not be got (offline, no such program) is tried again after this long
REFRESH_SEC = 30 * 86400            # a downloaded site icon older than this is fetched again (the old one is shown meanwhile)
REFRESH_RETRY_SEC = 6 * 3600        # ... and when that fails, tried again after this long (the old icon stays)
NET_WORKERS = 2          # site downloads in flight at once, at most
PAUSE_SEC = 0.4          # a download thread rests this long after each icon
MAX_BACKLOG = 500        # sites waiting in the prefetch backlog
MAX_NAME = 100
HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
LOCAL_SUFFIXES = frozenset({"local", "localhost", "localdomain", "internal", "lan", "home", "corp", "intranet", "arpa",
                            "invalid", "test", "onion"})   # names of a private network or of nothing: never asked about
BAD_NAME_CHARS = frozenset('\\/:*?"<>|')   # what Windows refuses in a file name (and what could leave the icon folder)
OWN_EXES = frozenset({"lockdown.exe", "lockdownservice.exe", "lockdownsetup.exe"})   # get Lockdown's own icon
ICON_SIZE = 64          # app icons are kept this big (sharp at 40 px on a 150% screen)
EXTRACT_SIZE = 256      # ... and taken from the program this big, then scaled down
OWN_ICON_SIZE = ICON_SIZE   # as the extracted icons
APP_CACHE_VERSION = "2"     # 2: big icons (version 1 kept 32 px ones) - older app icons are made again, once
APP_MARK = "app_icons.version"
IMAGE_TYPES = (".ico", ".png", ".jpg", ".jpeg", ".bmp")   # icon files read as they are (Steam's, a shortcut's .ico)
KINDS = {"app": ".png", "site": ".png", "letter": ".svg"}

# Windows' own processes (lower case, with or without ".exe"): the gear = a system process, the shield = a security one.
SYSTEM_NAMES = {
    **dict.fromkeys((
        "[system process]", "system", "system idle process", "idle", "registry", "memory compression", "secure system",
        "svchost", "csrss", "lsass", "lsaiso", "services", "wininit", "winlogon", "smss", "dwm", "fontdrvhost", "sihost",
        "taskhostw", "runtimebroker", "spoolsv", "audiodg", "conhost", "ctfmon", "searchhost", "searchindexer", "wmiprvse",
        "dllhost", "backgroundtaskhost", "startmenuexperiencehost", "shellexperiencehost", "textinputhost", "explorer"),
        "gear"),
    **dict.fromkeys(("msmpeng", "nissrv", "mpdefendercoreservice", "securityhealthservice", "securityhealthsystray",
                     "smartscreen", "mssense"), "shield"),
}
SYSTEM_COLOR = "#5b6b7c"    # a neutral slate
# The two glyphs, copied from src/ui/js/icons.js (PATHS.settings and PATHS.shield; a test keeps them equal).
GLYPHS = {
    "gear": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
}


def clean_exe(name: str) -> str | None:
    """A program's file name as it is used for the icon (lower case), or None if it isn't a plain name: anything
    Windows allows in a file name ("osu!.exe", "game (x86).exe"); no folders, no \\ / : * ? " < > |, no "..", no
    control characters, no leading or trailing dot or space."""
    name = name.lower()
    if not (0 < len(name) <= MAX_NAME) or ".." in name or name[-1] in ". " or name[0] in ". ":
        return None
    if not name.isprintable() or any(c in BAD_NAME_CHARS for c in name):
        return None
    return name


def clean_host(name: str) -> str | None:
    name = name.lower()
    return name if HOST_RE.fullmatch(name) else None


def is_public_host(name: str) -> bool:
    """Could this be a site on the internet? It has a dot, is not an IP address (a number as the last part) and does
    not end in a private-network name (.local, .lan, .internal ...). Only such names are ever looked up for an icon -
    nothing about a LAN device reaches the net."""
    name = name.lower().rstrip(".")
    if not 3 <= len(name) <= 253 or "." not in name or any(c.isspace() or c in "/\\@:?#" for c in name):
        return False
    last = name.rpartition(".")[2]
    return not last.isdigit() and last not in LOCAL_SUFFIXES


def letter_svg(text: str) -> bytes:
    """A rounded square in a colour picked from the text, with its first letter."""
    color = PALETTE[int(hashlib.md5(text.lower().encode()).hexdigest(), 16) % len(PALETTE)]
    letter = escape(next((c for c in text if c.isalnum()), "?").upper()[:1])
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
            f'<rect width="64" height="64" rx="14" fill="{color}"/>'
            '<text x="32" y="33" text-anchor="middle" dominant-baseline="central" font-family="Segoe UI, system-ui, '
            f'sans-serif" font-weight="700" font-size="38" fill="#fff">{letter}</text></svg>').encode()


def system_kind(name: str) -> str | None:
    """"gear" / "shield" for a Windows process the table knows (name in any case, with or without ".exe"), else None."""
    name = name.lower()
    return SYSTEM_NAMES.get(name[:-4] if name.endswith(".exe") else name)


def system_svg(kind: str) -> bytes:
    """The system tile: a rounded square as the letter tile, in slate, with the white stroke glyph."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
            f'<rect width="64" height="64" rx="14" fill="{SYSTEM_COLOR}"/>'
            f'<g data-glyph="{kind}" transform="translate(16 16) scale(1.3333)" fill="none" stroke="#fff" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{GLYPHS[kind]}</g></svg>').encode()


def tile_svg(name: str) -> bytes:
    """What an app name shows until (or instead of) its real icon: the system tile of a known Windows process, else
    the letter tile."""
    kind = system_kind(name)
    return system_svg(kind) if kind else letter_svg(name[:MAX_NAME])


def extractable(name: str) -> bool:
    """Could a real icon exist for this (cleaned) app name? Not for "[system process]" and the like, nor for a known
    system name without ".exe" ("System", "Registry"): those are not files."""
    return not name.startswith("[") and (name.endswith(".exe") or system_kind(name) is None)


def system_exe_path(name: str) -> str | None:
    """%SystemRoot%\<exe> or %SystemRoot%\System32\<exe> if such a file exists (Windows only; `name` is a cleaned
    ".exe" name, so it has no folder in it)."""
    root = _system_root()
    if root is None or not name.endswith(".exe"):
        return None
    for folder in (root, root / "System32"):
        try:
            path = folder / name
            if path.is_file():
                return str(path)
        except OSError:
            continue
    return None


def _system_root() -> Path | None:
    import os
    base = os.environ.get("SystemRoot") if sys.platform == "win32" else None
    return Path(base) if base else None


def default_dir() -> Path:
    from paths import DATA_DIR
    return DATA_DIR / "icons"


def old_window_dir() -> Path | None:
    """Where the old window kept its icons (its files are reused so nothing is fetched twice)."""
    import os
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Lockdown" / "icons" if base else None


def download_on(db) -> bool:
    """The setting "Download icons for my sites" (on unless it was turned off; also on when it can't be read)."""
    try:
        return db.get_setting(DOWNLOAD_KEY, "1") != "0"
    except Exception:
        return True


_sites_lock = threading.Lock()


def _popular():
    """importer.popular with its site list loaded - by ONE thread: a page asks for many icons at once, and a second
    load while the first fills the table would make a bundled icon look missing for a moment."""
    from importer import popular
    with _sites_lock:
        popular.load_sites()
    return popular


def _default_fetch(host: str):
    from resident import iconfetch   # (the net code is only loaded when a download is really made)
    return iconfetch.fetch_site_icon(host)


class IconService:
    """`on_ready(key)` is called from a background thread when an icon has arrived. `path_of(exe)` gives the full path of
    a program (or None); `sources_of(exe)` the app catalog's icon places for it, best first ("path" or "path,index":
    a shortcut's icon, Steam's, the uninstall entry's - see monitor/applist.py), tried before the path;
    `extract(location)` -> PNG bytes | None and `fetch(host)` -> PNG bytes | None can be replaced (tests). `download`: the setting "Download icons for my sites" (the owner of the service keeps it up to date; no
    thread of this class reads the database). `start=False` keeps the threads off: call `run_pending()` instead.
    `wall` is the clock of file ages, `clock` the one of retry times, `sleep` the pause between downloads."""

    def __init__(self, cache_dir: Path | None = None, on_ready=None, path_of=None, extract=None, fetch=None,
                 old_dir: Path | None = None, start: bool = True, clock=time.monotonic, download: bool = True,
                 wall=time.time, sleep=time.sleep, pause: float = PAUSE_SEC, sources_of=None):
        self.dir = Path(cache_dir) if cache_dir else default_dir()
        self.old_dir = old_dir if old_dir is not None else old_window_dir()
        self.on_ready = on_ready or (lambda key: None)
        self.path_of = path_of or (lambda exe: None)
        self.sources_of = sources_of or (lambda exe: [])
        self.extract = extract or extract_exe_icon
        self.fetch = fetch or _default_fetch
        self.clock, self.wall, self.sleep, self.pause = clock, wall, sleep, pause
        self.download = bool(download)
        self._start = start
        self._jobs: queue.Queue = queue.Queue()                 # programs' icons (local, quick): one thread
        self._pending: set = set()                              # keys queued or being made
        self._failed: dict = {}            # key -> when it failed (a refresh that failed: a later time, see _work)
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)            # the download threads wait on this
        self._front: collections.deque = collections.deque()    # sites the page asked for
        self._back: dict = {}                                   # prefetch backlog: site -> None (in order)
        self._thread: threading.Thread | None = None
        self._net_threads: list = []
        self._app_cache_ok = False         # the app icons on disk are of APP_CACHE_VERSION (checked on the first request)

    # ------------------------------------------------------------------ the route (HTTP server threads)
    def handle(self, rest: str, query: dict) -> Response | None:
        kind, _, name = rest.partition("/")
        ext = KINDS.get(kind)
        if not ext or not name.endswith(ext):
            return None
        name = name[:-len(ext)]
        if kind == "letter":
            if not name or len(name) > 64 or not name.isprintable() or "/" in name:   # (any text: it is XML-escaped)
                return None
            return Response(letter_svg(name), "image/svg+xml", cache="public, max-age=86400")
        raw = name
        name = clean_exe(name) if kind == "app" else clean_host(name)
        if name is None:     # a name this server cannot use: a tile, never a 404 - and nothing is read, written or queued
            return Response(tile_svg(raw) if kind == "app" else letter_svg(raw[:MAX_NAME]), "image/svg+xml",
                            cache="public, max-age=86400")
        if kind == "site":
            return self._site(name)
        self._upgrade_app_cache()
        data = self._cached(kind, name)
        if data is not None:
            return Response(data, "image/png", cache="no-cache", etag=f'"{self._stamp(kind, name)}"')
        if not extractable(name):    # (no real icon can ever come: the tile may be kept)
            return Response(tile_svg(name), "image/svg+xml", cache="public, max-age=86400")
        self._enqueue(kind, name)
        return Response(tile_svg(name), "image/svg+xml", cache="no-store")

    def _site(self, name: str) -> Response:
        """The cache, else the icon bundled with Lockdown, else a letter tile (and a download, if that is on)."""
        data = self._cached("site", name)
        if data is not None:
            if self.download and self._stale(name):
                self._enqueue("site", name)         # (the old icon is sent now; the new one is swapped in when it is here)
            return Response(data, "image/png", cache="no-cache", etag=f'"{self._stamp("site", name)}"')
        bundled = self._bundled(name)
        if bundled is not None:
            return Response(bundled[0], "image/png", cache="no-cache", etag=bundled[1])
        if is_public_host(name) and (self.download or self._old_file(name) is not None):
            self._enqueue("site", name)             # (off: only to take over an icon the old window had - no network)
        return Response(letter_svg(name), "image/svg+xml", cache="no-store")

    def _upgrade_app_cache(self):
        """Once: app icons saved by an older version (small, blurry) are deleted, so each is made again when asked."""
        if self._app_cache_ok:
            return
        with self._lock:            # (the other requests wait: none may send an old icon meanwhile)
            if self._app_cache_ok:
                return
            try:
                mark = (self.dir / APP_MARK).read_text(encoding="utf-8").strip()
            except OSError:
                mark = None
            try:
                if mark != APP_CACHE_VERSION and self.dir.is_dir():   # (no folder yet: marked with the first icon)
                    for old in self.dir.glob("app_*.png"):
                        old.unlink(missing_ok=True)
                    (self.dir / APP_MARK).write_text(APP_CACHE_VERSION, encoding="utf-8")
            except OSError:
                pass                # (tried again at the next start)
            self._app_cache_ok = True

    def _file(self, kind: str, name: str) -> Path:
        return self.dir / f"{kind}_{name}.png"

    def _cached(self, kind: str, name: str) -> bytes | None:
        try:
            data = self._file(kind, name).read_bytes()
        except OSError:
            return None
        return data or None

    def _stamp(self, kind: str, name: str) -> str:
        try:
            info = self._file(kind, name).stat()
            return f"{info.st_mtime_ns:x}-{info.st_size:x}"
        except OSError:
            return "0"

    def _has(self, kind: str, name: str) -> bool:
        try:
            return self._file(kind, name).stat().st_size > 0
        except OSError:
            return False

    def _stale(self, name: str) -> bool:
        """Is the cached icon of this site older than REFRESH_SEC?"""
        try:
            return self.wall() - self._file("site", name).stat().st_mtime > REFRESH_SEC
        except OSError:
            return False

    def _bundled(self, name: str):
        """(PNG bytes, etag) of the icon Lockdown brings for this site (m.youtube.com and youtu.be get youtube.com's)."""
        try:
            popular = _popular()
            site = popular.site_of(name)
            path = popular.site_icon_path(site) if site else None
            if path is None:
                return None
            info = path.stat()
            data = path.read_bytes()
        except Exception:
            return None
        return (data, f'"b{info.st_mtime_ns:x}-{info.st_size:x}"') if data else None

    def _old_file(self, name: str) -> Path | None:
        if self.old_dir is None:
            return None
        old = self.old_dir / f"{name}.png"
        try:
            return old if old.is_file() and old.stat().st_size else None
        except OSError:
            return None

    # ------------------------------------------------------------------ the background threads
    def _enqueue(self, kind: str, name: str):
        key = f"{kind}:{name}"
        with self._lock:
            failed = self._failed.get(key)
            if key in self._pending or (failed is not None and self.clock() - failed < RETRY_SEC):
                return
            if len(self._pending) >= MAX_QUEUED:
                return
            self._pending.add(key)
            if kind == "site":
                self._front.append(name)
                self._wake.notify()
                self._start_net()
                return
            self._jobs.put((kind, name))
            if self._start and self._thread is None:
                self._thread = threading.Thread(target=self._run, name="lockdown-icons", daemon=True)
                self._thread.start()

    def _start_net(self):
        """(lock held) The download threads - up to NET_WORKERS, started when there is something to get."""
        while self._start and len(self._net_threads) < NET_WORKERS:
            t = threading.Thread(target=self._net_run, name=f"lockdown-icons-net{len(self._net_threads) + 1}", daemon=True)
            self._net_threads.append(t)
            t.start()

    def prefetch(self, hosts) -> int:
        """Queue the real icons of these sites behind whatever the page asks for - those without an icon in the cache
        (or with one over REFRESH_SEC old), without a bundled one, not failed a short while ago, not queued already.
        Nothing at all while downloads are off. Returns how many were added."""
        if not self.download:
            return 0
        added = 0
        for host in hosts:
            name = clean_host(str(host))
            if name is None or not is_public_host(name) or self._bundled_path(name) is not None:
                continue
            if self._has("site", name) and not self._stale(name):
                continue
            key = f"site:{name}"
            with self._lock:
                failed = self._failed.get(key)
                if (name in self._back or key in self._pending or len(self._back) >= MAX_BACKLOG
                        or (failed is not None and self.clock() - failed < RETRY_SEC)):
                    continue
                self._back[name] = None
                added += 1
        if added:
            with self._lock:
                self._wake.notify_all()
                self._start_net()
        return added

    @staticmethod
    def _bundled_path(name: str):
        try:
            popular = _popular()
            site = popular.site_of(name)
            return popular.site_icon_path(site) if site else None
        except Exception:
            return None

    def retry_apps(self):
        """The app catalog has (re)loaded: the app icons that could not be made a moment ago (no path known yet) are
        tried again now, and the page is told when they arrive."""
        with self._lock:
            names = [k[4:] for k in self._failed if k.startswith("app:")]
            for name in names:
                del self._failed[f"app:{name}"]
        for name in names:
            self._enqueue("app", name)

    @property
    def queued(self) -> int:
        """Icons waiting to be got (tests)."""
        return len(self._pending)

    @property
    def backlog(self) -> int:
        """Sites waiting in the prefetch backlog (tests)."""
        return len(self._back)

    def _take_site(self) -> str | None:
        """(lock held) The next site to get: the page's requests first, then the backlog. None if there is none."""
        if self._front:
            return self._front.popleft()
        while self._back:
            name = next(iter(self._back))
            del self._back[name]
            key = f"site:{name}"
            failed = self._failed.get(key)
            if key in self._pending or (failed is not None and self.clock() - failed < RETRY_SEC):
                continue
            if self._has("site", name) and not self._stale(name):   # (made meanwhile)
                continue
            self._pending.add(key)
            return name
        return None

    def _run(self):
        while True:
            self._work(*self._jobs.get())

    def _net_run(self):
        while True:
            with self._wake:
                name = self._take_site()
                while name is None:
                    self._wake.wait()
                    name = self._take_site()
            self._work("site", name)
            self.sleep(self.pause)

    def run_pending(self):
        """Do the waiting jobs now, on this thread (tests / start=False): the programs' icons, then the sites."""
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                with self._lock:
                    name = self._take_site()
                if name is None:
                    return
                job = ("site", name)
            self._work(*job)

    def _work(self, kind: str, name: str):
        key = f"{kind}:{name}"
        try:
            ok = self._make(kind, name)
        except Exception:   # an icon is never worth a crash; the letter tile stays
            ok = False
        refresh = ok is False and kind == "site" and self._has(kind, name)   # (it keeps its old icon)
        with self._lock:
            self._pending.discard(key)
            if ok is False:
                now = self.clock()
                self._failed.pop(key, None)
                self._failed[key] = now + (REFRESH_RETRY_SEC - RETRY_SEC if refresh else 0)
                for old in [k for k, t in self._failed.items() if now - t >= RETRY_SEC]:
                    del self._failed[old]
                while len(self._failed) > MAX_FAILED:
                    del self._failed[next(iter(self._failed))]
        if ok:
            try:
                self.on_ready(key)
            except Exception:
                pass

    def _make(self, kind: str, name: str) -> bool | None:
        """True: the icon is in the cache now. False: it could not be got (retried later). None: nothing to do (it was
        made meanwhile, or the download setting is off)."""
        target = self._file(kind, name)
        if kind == "site":
            return self._make_site(target, name)
        if name in OWN_EXES:   # Lockdown's own program: its real icon, no catalog lookup
            data = own_icon()
            if data:
                self._save(target, data)
                return True
        places = list(self.sources_of(name) or [])
        path = self.path_of(name) or system_exe_path(name)
        if path and path not in places:
            places.append(path)
        for place in places:
            data = self.extract(place)
            if data:
                self._save(target, data)
                return True
        if self.old_dir is not None:   # the old window may have it (its names: app_<exe>.png, <host>.png; 32 px)
            old = self.old_dir / f"app_{name}.png"
            if old.is_file() and old.stat().st_size:
                self._save(target, old.read_bytes())
                return True
        return False

    def _make_site(self, target: Path, name: str) -> bool | None:
        have = self._has("site", name)
        if have and not self._stale(name):
            return None
        old = None if have else self._old_file(name)
        if old is not None:            # the old window had it: no network needed
            self._save(target, old.read_bytes())
            return True
        if not self.download:
            return None
        data = self.fetch(name)
        if not data:
            return False
        self._save(target, data)
        return True

    def _save(self, target: Path, data: bytes):
        self.dir.mkdir(parents=True, exist_ok=True)
        if target.name.startswith("app_") and not (self.dir / APP_MARK).exists():
            (self.dir / APP_MARK).write_text(APP_CACHE_VERSION, encoding="utf-8")   # (the new icons are of this version)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)


# ---------------------------------------------------------------- getting the real icons (background thread only)

def _png(image) -> bytes:
    import io
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def own_icon() -> bytes | None:
    """Lockdown's own icon (assets/lockdown.png, else lockdown.ico) as PNG bytes at the size of the other app icons."""
    try:
        from PIL import Image
        from paths import ASSETS
    except Exception:
        return None
    for file in ("lockdown.png", "lockdown.ico"):
        try:
            with Image.open(ASSETS / file) as image:
                return _png(image.convert("RGBA").resize((OWN_ICON_SIZE, OWN_ICON_SIZE), Image.LANCZOS))
        except Exception:
            continue
    return None


def split_location(text: str) -> tuple[str, int]:
    """An icon place as Windows writes it - '"C:\\App\\app.exe",0', 'C:\\x.dll,-101', '%SystemRoot%\\a.ico' - as
    (path, index): quotes off, environment variables filled in, index 0 when none is given."""
    text = str(text or "").strip()
    index = 0
    m = re.fullmatch(r"(.*?),\s*(-?\d+)", text)
    if m:
        text, index = m.group(1).strip(), int(m.group(2))
    import os
    return os.path.expandvars(text.strip().strip('"').strip()), index


def fit(image):
    """An icon picture as kept: RGBA (transparency kept), scaled down to fit ICON_SIZE (never up), square."""
    from PIL import Image
    image = image.convert("RGBA")
    if max(image.size) > ICON_SIZE:
        image.thumbnail((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    if image.width != image.height:
        side = max(image.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        image = square
    return image


def image_file_icon(path: str) -> bytes | None:
    """An icon file (.ico - its biggest picture -, .png, .jpg, .bmp) as PNG bytes (any system: Pillow reads it)."""
    try:
        from PIL import Image
        with Image.open(path) as image:
            return _png(fit(image))
    except Exception:
        return None


def extract_exe_icon(location: str) -> bytes | None:
    """The icon at `location` ("path" or "path,index") as PNG bytes, ICON_SIZE at most. An icon file is read as it is;
    a program's (or a .dll's) icon is taken big (SHDefExtractIcon at EXTRACT_SIZE, Windows only) and scaled down, and
    when that fails the 32 px one is taken as before (ExtractIconEx)."""
    path, index = split_location(location)
    if not path:
        return None
    if path.lower().endswith(IMAGE_TYPES):
        return image_file_icon(path)
    if sys.platform != "win32":
        return None
    try:
        image = _exe_icon_large(path, index)
    except Exception:
        image = None
    if image is None:
        image = _exe_icon(path, index)
    return _png(fit(image)) if image else None


def _gdi():
    import ctypes
    from ctypes import wintypes
    shell32, user32, gdi32 = ctypes.windll.shell32, ctypes.windll.user32, ctypes.windll.gdi32
    for fn in (gdi32.CreateCompatibleDC, gdi32.CreateDIBSection, gdi32.SelectObject):
        fn.restype = wintypes.HANDLE
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HANDLE]
    gdi32.CreateDIBSection.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HANDLE]
    user32.DrawIconEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_int, wintypes.HANDLE, ctypes.c_int,
                                  ctypes.c_int, wintypes.UINT, wintypes.HANDLE, wintypes.UINT]
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    shell32.SHDefExtractIconW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.UINT, ctypes.POINTER(wintypes.HANDLE),
                                          ctypes.POINTER(wintypes.HANDLE), wintypes.UINT]
    shell32.SHDefExtractIconW.restype = ctypes.c_long
    return shell32, user32, gdi32


def _exe_icon_large(path: str, index: int = 0):
    """The program's icon drawn at EXTRACT_SIZE (Windows picks its biggest picture - up to 256 px - for that size)."""
    import ctypes
    from ctypes import wintypes
    shell32, _user32, _gdi32 = _gdi()
    large = wintypes.HANDLE()
    sizes = (16 << 16) | EXTRACT_SIZE                       # MAKELONG(large size, small size)
    if shell32.SHDefExtractIconW(path, index, 0, ctypes.byref(large), None, sizes) != 0 or not large.value:
        return None                                         # (S_FALSE: no icon in that file)
    return _draw_icon(large, EXTRACT_SIZE)


def _exe_icon(path: str, index: int = 0):
    """The program's 32 px icon (ExtractIconEx) - the way before EXTRACT_SIZE, kept as the fallback."""
    import ctypes
    from ctypes import wintypes
    shell32, _user32, _gdi32 = _gdi()
    large = wintypes.HANDLE()
    if shell32.ExtractIconExW(path, index, ctypes.byref(large), None, 1) < 1 or not large.value:
        return None
    return _draw_icon(large, 32)


def _draw_icon(icon, size: int):
    """Draw an HICON into a size x size RGBA picture (Pillow); the icon is destroyed."""
    import ctypes
    from ctypes import wintypes
    from PIL import Image
    _shell32, user32, gdi32 = _gdi()

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    hdr = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=size, biHeight=-size, biPlanes=1,
                           biBitCount=32)
    bits = ctypes.c_void_p()
    dc = gdi32.CreateCompatibleDC(None)
    bmp = gdi32.CreateDIBSection(dc, ctypes.byref(hdr), 0, ctypes.byref(bits), None, 0)
    try:
        gdi32.SelectObject(dc, bmp)
        user32.DrawIconEx(dc, 0, 0, icon, size, size, 0, None, 3)   # DI_NORMAL
        raw = ctypes.string_at(bits, size * size * 4)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc)
        user32.DestroyIcon(icon)
    image = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1)
    if image.getextrema()[3][1] == 0:   # old icons without alpha: make drawn pixels opaque
        image.putalpha(Image.eval(image.convert("L"), lambda v: 255 if v else 0))
    return image.copy()
