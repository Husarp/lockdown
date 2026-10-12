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
OWN_ICON_SIZE = 32      # as the extracted icons
KINDS = {"app": ".png", "site": ".png", "letter": ".svg"}


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
    a program (or None); `extract(path)` -> PNG bytes | None and `fetch(host)` -> PNG bytes | None can be replaced
    (tests). `download`: the setting "Download icons for my sites" (the owner of the service keeps it up to date; no
    thread of this class reads the database). `start=False` keeps the threads off: call `run_pending()` instead.
    `wall` is the clock of file ages, `clock` the one of retry times, `sleep` the pause between downloads."""

    def __init__(self, cache_dir: Path | None = None, on_ready=None, path_of=None, extract=None, fetch=None,
                 old_dir: Path | None = None, start: bool = True, clock=time.monotonic, download: bool = True,
                 wall=time.time, sleep=time.sleep, pause: float = PAUSE_SEC):
        self.dir = Path(cache_dir) if cache_dir else default_dir()
        self.old_dir = old_dir if old_dir is not None else old_window_dir()
        self.on_ready = on_ready or (lambda key: None)
        self.path_of = path_of or (lambda exe: None)
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

    # ------------------------------------------------------------------ the route (HTTP server threads)
    def handle(self, rest: str, query: dict) -> Response | None:
        kind, _, name = rest.partition("/")
        ext = KINDS.get(kind)
        if not ext or not name.endswith(ext) or "/" in name:
            return None
        name = name[:-len(ext)]
        if kind == "letter":
            if not name or len(name) > 64 or not name.isprintable():   # (any text: it is XML-escaped; "/" cannot get here)
                return None
            return Response(letter_svg(name), "image/svg+xml", cache="public, max-age=86400")
        name = clean_exe(name) if kind == "app" else clean_host(name)
        if name is None:
            return None
        if kind == "site":
            return self._site(name)
        data = self._cached(kind, name)
        if data is not None:
            return Response(data, "image/png", cache="no-cache", etag=f'"{self._stamp(kind, name)}"')
        self._enqueue(kind, name)
        return Response(letter_svg(name), "image/svg+xml", cache="no-store")

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
        if self.old_dir is not None:   # the old window may have it already (its names: app_<exe>.png, <host>.png)
            old = self.old_dir / f"app_{name}.png"
            if old.is_file() and old.stat().st_size:
                self._save(target, old.read_bytes())
                return True
        if name in OWN_EXES:   # Lockdown's own program: its real icon, no catalog lookup
            data = own_icon()
            if data:
                self._save(target, data)
                return True
        path = self.path_of(name)
        data = self.extract(path) if path else None
        if not data:
            return False
        self._save(target, data)
        return True

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


def extract_exe_icon(path: str) -> bytes | None:
    """The program's own icon (32x32) as PNG bytes - Windows only (ExtractIconEx + DrawIconEx, as gui/icons.py)."""
    if sys.platform != "win32" or not path:
        return None
    image = _exe_icon(path)
    return _png(image) if image else None


def _exe_icon(path: str):
    import ctypes
    from ctypes import wintypes
    from PIL import Image
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
    large = wintypes.HANDLE()
    if shell32.ExtractIconExW(path, 0, ctypes.byref(large), None, 1) < 1 or not large.value:
        return None
    size = 32

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
        user32.DrawIconEx(dc, 0, 0, large, size, size, 0, None, 3)   # DI_NORMAL
        raw = ctypes.string_at(bits, size * size * 4)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc)
        user32.DestroyIcon(large)
    image = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1)
    if image.getextrema()[3][1] == 0:   # old icons without alpha: make drawn pixels opaque
        image.putalpha(Image.eval(image.convert("L"), lambda v: 255 if v else 0))
    return image.copy()
