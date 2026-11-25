"""Windows browser identity from the image name and its file-version resource.

The catalog identifies UI Automation families. Policy support and media session
attribution still require their own evidence.
"""
import ntpath
import os
from dataclasses import dataclass
from functools import lru_cache


# Product names are exact aliases from version resources, without channel decoration.
_PRODUCTS = {
    "Chrome": ("chromium", "chrome.exe", ("Google Chrome", "Google Chrome for Testing")),
    "Edge": ("chromium", "msedge.exe", ("Microsoft Edge",)),
    "Brave": ("chromium", "brave.exe", ("Brave Browser", "Brave")),
    "Firefox": ("mozilla", "firefox.exe",
                ("Firefox", "Mozilla Firefox", "Firefox ESR", "Firefox Developer Edition", "Firefox Nightly")),
    "Opera": ("chromium", "opera.exe", ("Opera", "Opera Internet Browser", "Opera Stable")),
    "Opera GX": ("chromium", "opera.exe", ("Opera GX", "Opera GX Internet Browser")),
    "Vivaldi": ("chromium", "vivaldi.exe", ("Vivaldi", "Vivaldi Browser")),
    "Arc": ("chromium", "arc.exe", ("Arc", "Arc Browser")),
    "Zen": ("mozilla", "zen.exe", ("Zen", "Zen Browser")),
    "LibreWolf": ("mozilla", "librewolf.exe", ("LibreWolf",)),
    "Waterfox": ("mozilla", "waterfox.exe", ("Waterfox", "Waterfox Classic")),
    "Chromium": ("chromium", "chromium.exe", ("Chromium",)),
    "Thorium": ("chromium", "thorium.exe", ("Thorium", "Thorium Browser")),
    "Cromite": ("chromium", "cromite.exe", ("Cromite",)),
    "Supermium": ("chromium", "supermium.exe", ("Supermium",)),
    "Ungoogled Chromium": ("chromium", "chromium.exe", ("ungoogled-chromium", "Ungoogled Chromium")),
    "SRWare Iron": ("chromium", "iron.exe", ("SRWare Iron",)),
}
_BY_EXE = {}
_BY_PRODUCT = {}
for _product, (_family, _exe, _aliases) in _PRODUCTS.items():
    _BY_EXE.setdefault(_exe, _product)
    _BY_PRODUCT.update({alias.casefold(): _product for alias in _aliases})
_BY_EXE.update({"opera_gx.exe": "Opera GX", "zen-browser.exe": "Zen"})
BROWSERS = set(_BY_EXE)


@dataclass(frozen=True)
class BrowserIdentity:
    exe: str
    product: str
    family: str
    version: str = "unknown"
    path: str | None = None


@dataclass(frozen=True)
class _FileVersion:
    product: str = ""
    original: str = ""
    version: str = "unknown"


def _read_file_version(path: str) -> _FileVersion:
    import win32api

    try:
        fixed = win32api.GetFileVersionInfo(path, "\\")
        ms, ls = fixed["FileVersionMS"], fixed["FileVersionLS"]
        version = ".".join(str(n & 0xffff) for n in (ms >> 16, ms, ls >> 16, ls))
    except Exception:
        version = "unknown"
    try:
        translations = win32api.GetFileVersionInfo(path, r"\VarFileInfo\Translation")
    except Exception:
        translations = ()
    product = original = ""
    for language, codepage in translations or ():
        prefix = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\"
        try:
            product = win32api.GetFileVersionInfo(path, prefix + "ProductName").strip()
        except Exception:
            pass
        try:
            original = win32api.GetFileVersionInfo(path, prefix + "OriginalFilename").strip()
        except Exception:
            pass
        if product:
            break
    return _FileVersion(product, original, version)


@lru_cache(maxsize=128)
def _file_version(path: str, fingerprint: tuple) -> _FileVersion:
    return _read_file_version(path)


def identify(exe: str | None, path: str | None = None) -> BrowserIdentity | None:
    """Return the actual browser installation, if there is credible browser evidence."""
    path = os.fspath(path) if path else None
    image = ntpath.basename(path or exe or "").casefold()
    product = _BY_EXE.get(image)
    info = _FileVersion()
    if path:
        try:
            st = os.stat(path)
            fingerprint = (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        except OSError:
            fingerprint = ()
        info = _file_version(path, fingerprint)
        named = _BY_PRODUCT.get(info.product.casefold())
        original = ntpath.basename(info.original).casefold()
        if named:
            family, expected, _ = _PRODUCTS[named]
            # Forks and rebranded executables can retain chrome.exe/firefox.exe
            # as their original image. Require an engine-compatible browser image.
            inherited = {"chrome.exe", "chromium.exe"} if family == "chromium" else {"firefox.exe"}
            if image != expected and product != named and original not in (inherited | {expected}):
                return None
            product = named
        elif info.product:
            return None
        if product == "Opera" and "opera gx" in {part.casefold() for part in path.replace("/", "\\").split("\\")}:
            product = "Opera GX"
    if not product:
        return None
    return BrowserIdentity(image, product, _PRODUCTS[product][0], info.version, path)
