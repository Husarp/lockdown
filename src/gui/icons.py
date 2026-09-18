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


def prefetch(domains):
    """Download missing favicons in the background."""
    missing = [d for d in dict.fromkeys(domains) if not _cached_file(d).exists()]
    if missing:
        threading.Thread(target=lambda: [_download(d) for d in missing], daemon=True).start()
