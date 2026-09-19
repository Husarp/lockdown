"""Installed Steam games, for the app browser: Steam's library folders (libraryfolders.vdf) and each game's manifest
(appmanifest_<id>.acf: name + install folder), with the game's main .exe guessed from its install folder. Blocking a
Steam game ticks "also close its background processes", which covers everything that runs from its folder (launchers,
second exes)."""
import os
import re
import winreg
from pathlib import Path

_PAIR = re.compile(r'"([^"]+)"\s+"([^"]*)"')
# not the game: crash reporters, installers, anti-cheat, engine / redistributable helpers
_NOT_GAME = re.compile(r"crash|report|redist|vcredist|vc_|dxsetup|directx|dotnet|unins|setup|install|prereq|"
                       r"easyanticheat|eac|battleye|be_service|cefprocess|webhelper|update|helper|dxwebsetup|oalinst|"
                       r"physx|launcherpatcher", re.I)
_SKIP_APPS = {"228980"}   # Steamworks Common Redistributables
MAX_DEPTH = 3


def steam_path() -> Path | None:
    for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as k:
                path = Path(winreg.QueryValueEx(k, value)[0])
            if path.exists():
                return path
        except OSError:
            continue
    return None


def library_folders(steam: Path) -> list[Path]:
    """Every Steam library ("path" entries of libraryfolders.vdf) - the Steam folder itself included."""
    folders = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        for key, value in _PAIR.findall(vdf.read_text(encoding="utf-8", errors="ignore")):
            if key == "path":
                folders.append(Path(value.replace("\\\\", "\\")))
    return list(dict.fromkeys(p for p in folders if (p / "steamapps").exists()))


def parse_manifest(text: str) -> dict:
    return {k: v for k, v in _PAIR.findall(text)}


def main_exe(folder: Path, name: str) -> Path | None:
    """The game's own .exe: named like the game if possible, else the biggest one, preferring shallow ones."""
    wanted = re.sub(r"[^a-z0-9]", "", name.lower())
    best, best_score = None, None
    for root, dirs, files in os.walk(folder):
        depth = len(Path(root).relative_to(folder).parts)
        if depth >= MAX_DEPTH:
            dirs.clear()
        for f in files:
            if not f.lower().endswith(".exe") or _NOT_GAME.search(f):
                continue
            path = Path(root) / f
            stem = re.sub(r"[^a-z0-9]", "", f[:-4].lower())
            named = bool(stem) and (stem in wanted or wanted in stem)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            score = (named, -depth, size)
            if best_score is None or score > best_score:
                best, best_score = path, score
    return best


def games() -> list[dict]:
    """[{name, exe, path, steam: True}] of installed Steam games that have an .exe."""
    steam = steam_path()
    if not steam:
        return []
    out = []
    for library in library_folders(steam):
        for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
            info = parse_manifest(manifest.read_text(encoding="utf-8", errors="ignore"))
            if info.get("appid") in _SKIP_APPS or not info.get("name") or not info.get("installdir"):
                continue
            folder = library / "steamapps" / "common" / info["installdir"]
            exe = main_exe(folder, info["name"]) if folder.exists() else None
            if exe:
                out.append({"name": info["name"], "exe": exe.name.lower(), "path": str(exe), "steam": True})
    return out
