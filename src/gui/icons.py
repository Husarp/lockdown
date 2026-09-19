"""Site icons: favicons downloaded once and cached; a letter icon whenever one isn't available (offline etc.)."""
import hashlib
import os
import threading
import urllib.request
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Lockdown" / "icons"
FAVICON_URL = "https://www.google.com/s2/favicons?domain={domain}&sz=64"
PALETTE = ["#e5534b", "#c69026", "#57ab5a", "#539bf5", "#b083f0", "#dc6d1a", "#39c5cf", "#e275ad"]

_memory: dict[tuple[str, int], ctk.CTkImage] = {}


def _cached_file(domain: str) -> Path:
    return CACHE_DIR / f"{domain}.png"


def _letter_icon(domain: str) -> Image.Image:
    color = PALETTE[int(hashlib.md5(domain.encode()).hexdigest(), 16) % len(PALETTE)]
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, 63, 63), radius=14, fill=color)
    try:
        font = ImageFont.truetype("segoeuib.ttf", 38)
    except OSError:
        font = ImageFont.load_default()
    draw.text((32, 32), domain[:1].upper(), fill="white", font=font, anchor="mm")
    return img


def get(domain: str, size: int = 20) -> ctk.CTkImage:
    """Icon for a domain: cached favicon if we have one, else a letter icon. Never raises."""
    key = (domain, size)
    if key in _memory:
        return _memory[key]
    path = _cached_file(domain)
    try:
        img = Image.open(path).convert("RGBA") if path.exists() else None
    except OSError:
        img = None
    if img is None:  # letter icons aren't memoized: the favicon may arrive later
        return ctk.CTkImage(_letter_icon(domain), size=(size, size))
    _memory[key] = ctk.CTkImage(img, size=(size, size))
    return _memory[key]


def _download(domain: str):
    path = _cached_file(domain)
    if path.exists():
        return
    try:
        req = urllib.request.Request(FAVICON_URL.format(domain=domain), headers={"User-Agent": "Lockdown"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        Image.open(tmp).verify()   # only keep real images
        tmp.replace(path)
    except Exception:
        pass   # offline / not found: the letter icon is used


def _exe_icon(path: str) -> Image.Image | None:
    """The exe's own icon (32x32 RGBA) via ExtractIconEx + DrawIconEx, or None."""
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
    img = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1)
    if img.getextrema()[3][1] == 0:   # old icons without alpha: make drawn pixels opaque
        img.putalpha(Image.eval(img.convert("L"), lambda v: 255 if v else 0))
    return img.copy()


def cache_app_icons(apps: list[dict]):
    """Extract apps' icons to the disk cache ahead of time (background thread; no Tk objects made here)."""
    for app in apps:
        cached = CACHE_DIR / f"app_{app['exe']}.png"
        if cached.exists() or not app.get("path"):
            continue
        try:
            img = _exe_icon(app["path"])
            if img:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                img.save(cached)
        except Exception:
            continue


def get_app(exe: str, path: str | None, size: int = 20) -> ctk.CTkImage:
    """Icon for an app: its exe icon (cached), else a letter icon. Never raises."""
    key = (f"app:{exe}", size)
    if key in _memory:
        return _memory[key]
    cached = CACHE_DIR / f"app_{exe}.png"
    img = None
    try:
        if cached.exists():
            img = Image.open(cached).convert("RGBA")
        elif path:
            img = _exe_icon(path)
            if img:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                img.save(cached)
    except Exception:
        img = None
    if img is None:
        return ctk.CTkImage(_letter_icon(exe), size=(size, size))
    _memory[key] = ctk.CTkImage(img, size=(size, size))
    return _memory[key]


def for_item(item: dict, size: int = 20) -> ctk.CTkImage:
    if item.get("item_type") == "app":
        return get_app(item["target"].lower(), item.get("app_path"), size)
    return get(item["target"].split()[0], size)


def prefetch(domains):
    """Download missing favicons in the background."""
    missing = [d for d in dict.fromkeys(domains) if not _cached_file(d).exists()]
    if missing:
        threading.Thread(target=lambda: [_download(d) for d in missing], daemon=True).start()
