"""App and site icons for the new window, served by the asset server and never made on the page's time.

  /icon/app/<exe>.png      the program's own icon      /icon/site/<host>.png   the site's favicon
  /icon/letter/<text>.svg  a coloured letter tile

An icon that is already in the disk cache (DATA_DIR/icons/) is sent as it is. One that isn't gets a letter tile at
once, and ONE background thread then gets the real one (an exe's icon from Windows, a favicon from the net) and tells
the page with `icon_ready {key}` ("app:chrome.exe" / "site:example.com") so it swaps the <img> src. Nothing here runs
on the GUI thread; PIL is imported on that background thread only.

The page is untrusted: only plain names are accepted (an exe name or a host name, no folders, no "..", no odd
characters), and the page never says WHERE an exe is - the path comes from the app catalog or the owner's own list.
"""
import hashlib
import queue
import re
import sys
import threading
import time
import urllib.request
from html import escape
from pathlib import Path

from resident.assets import Response

PALETTE = ["#e5534b", "#c69026", "#57ab5a", "#539bf5", "#b083f0", "#dc6d1a", "#39c5cf", "#e275ad"]   # as gui/icons.py
FAVICON_URL = "https://www.google.com/s2/favicons?domain={domain}&sz=64"
MAX_QUEUED = 64          # more distinct icons than this waiting at once: the extra ones keep their letter tile
MAX_FAILED = 2000        # remembered failures (the oldest go first)
RETRY_SEC = 600          # an icon that could not be got (offline, no such program) is tried again after this long
MAX_NAME = 100
HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
NAME_PUNCT = " ._-+()&'"
KINDS = {"app": ".png", "site": ".png", "letter": ".svg"}


def clean_exe(name: str) -> str | None:
    """A program's file name as it is used for the icon (lower case), or None if it isn't a plain name: letters,
    digits and . _ - + ( ) & ' and spaces; no folders, no "..", no colon, no control characters."""
    name = name.lower()
    if not (0 < len(name) <= MAX_NAME) or ".." in name or name[-1] in ". " or name[0] in ". ":
        return None
    if not all(c.isalnum() or c in NAME_PUNCT for c in name):
        return None
    return name


def clean_host(name: str) -> str | None:
    name = name.lower()
    return name if HOST_RE.match(name) else None


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


class IconService:
    """`on_ready(key)` is called from the background thread when an icon has arrived. `path_of(exe)` gives the full
    path of a program (or None); `extract(path)` -> PNG bytes | None and `fetch(host)` -> PNG bytes | None can be
    replaced (tests). `start=False` keeps the thread off: call `run_pending()` instead."""

    def __init__(self, cache_dir: Path | None = None, on_ready=None, path_of=None, extract=None, fetch=None,
                 old_dir: Path | None = None, start: bool = True, clock=time.monotonic):
        self.dir = Path(cache_dir) if cache_dir else default_dir()
        self.old_dir = old_dir if old_dir is not None else old_window_dir()
        self.on_ready = on_ready or (lambda key: None)
        self.path_of = path_of or (lambda exe: None)
        self.extract = extract or extract_exe_icon
        self.fetch = fetch or fetch_favicon
        self.clock = clock
        self._start = start
        self._jobs: queue.Queue = queue.Queue()
        self._pending: set = set()
        self._failed: dict = {}            # key -> when it failed
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ the route (HTTP server threads)
    def handle(self, rest: str, query: dict) -> Response | None:
        kind, _, name = rest.partition("/")
        ext = KINDS.get(kind)
        if not ext or not name.endswith(ext) or "/" in name:
            return None
        name = name[:-len(ext)]
        if kind == "letter":
            if not name or len(name) > 64 or not all(c.isalnum() or c in NAME_PUNCT for c in name):
                return None
            return Response(letter_svg(name), "image/svg+xml", cache="public, max-age=86400")
        name = clean_exe(name) if kind == "app" else clean_host(name)
        if name is None:
            return None
        data = self._cached(kind, name)
        if data is not None:
            return Response(data, "image/png", cache="no-cache", etag=f'"{self._stamp(kind, name)}"')
        self._enqueue(kind, name)
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

    # ------------------------------------------------------------------ the one background thread
    def _enqueue(self, kind: str, name: str):
        key = f"{kind}:{name}"
        with self._lock:
            failed = self._failed.get(key)
            if key in self._pending or (failed is not None and self.clock() - failed < RETRY_SEC):
                return
            if len(self._pending) >= MAX_QUEUED:
                return
            self._pending.add(key)
            self._jobs.put((kind, name))
            if self._start and self._thread is None:
                self._thread = threading.Thread(target=self._run, name="lockdown-icons", daemon=True)
                self._thread.start()

    @property
    def queued(self) -> int:
        """Icons waiting to be got (tests)."""
        return len(self._pending)

    def _run(self):
        while True:
            self._work(*self._jobs.get())

    def run_pending(self):
        """Do the waiting jobs now, on this thread (tests / start=False)."""
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            self._work(*job)

    def _work(self, kind: str, name: str):
        key = f"{kind}:{name}"
        try:
            ok = self._make(kind, name)
        except Exception:   # an icon is never worth a crash; the letter tile stays
            ok = False
        with self._lock:
            self._pending.discard(key)
            if not ok:
                now = self.clock()
                self._failed.pop(key, None)
                self._failed[key] = now
                for old in [k for k, t in self._failed.items() if now - t >= RETRY_SEC]:
                    del self._failed[old]
                while len(self._failed) > MAX_FAILED:
                    del self._failed[next(iter(self._failed))]
        if ok:
            try:
                self.on_ready(key)
            except Exception:
                pass

    def _make(self, kind: str, name: str) -> bool:
        target = self._file(kind, name)
        if self.old_dir is not None:   # the old window may have it already (its names: app_<exe>.png, <host>.png)
            old = self.old_dir / (f"app_{name}.png" if kind == "app" else f"{name}.png")
            if old.is_file() and old.stat().st_size:
                self._save(target, old.read_bytes())
                return True
        if kind == "app":
            path = self.path_of(name)
            data = self.extract(path) if path else None
        else:
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


def fetch_favicon(host: str) -> bytes | None:
    """The site's favicon as PNG bytes (only a real image is kept), or None (offline, no icon)."""
    try:
        req = urllib.request.Request(FAVICON_URL.format(domain=host), headers={"User-Agent": "Lockdown"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read(200_000)
        import io
        from PIL import Image
        Image.open(io.BytesIO(data)).verify()
        return data
    except Exception:
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
