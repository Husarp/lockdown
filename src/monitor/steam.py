"""Installed Steam games, for the app browser: Steam's library folders (libraryfolders.vdf) and each game's manifest
(appmanifest_<id>.acf: name + install folder), with the game's main .exe guessed from its install folder. Blocking a
Steam game ticks "also close its background processes", which covers everything that runs from its folder (launchers,
second exes).

A game's icon places, best first: the .ico of Steam's own shortcut to it (a .url in the Start Menu or on the desktop,
"IconFile=...\\steam\\games\\<hash>.ico"), its .exe, and the small icon in Steam's library cache."""
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
_RUN_ID = re.compile(r"steam://rungameid/(\d+)", re.I)
_HASH = re.compile(r"[0-9a-f]{40}", re.I)


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
    """The game's own .exe: an Unreal game's ...-Shipping.exe (the process that really runs), else one named like
    the game, else the biggest one, preferring shallow ones."""
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
            # an Unreal game's real process is <Name>-Win64-Shipping.exe in Binaries\Win64; the small <Name>.exe
            # in the game's folder only starts it. Picking that starter named the wrong process on the list.
            shipping = "shipping" in f.lower()
            score = (shipping, named, -depth, size)
            if best_score is None or score > best_score:
                best, best_score = path, score
    return best


def shortcut_icons(folders) -> dict[str, str]:
    """{appid: icon file} from Steam's shortcuts (.url) in these folders and one level below (Start Menu\\Steam)."""
    found = {}
    for folder in folders:
        try:
            files = [*Path(folder).glob("*.url"), *Path(folder).glob("*/*.url")]
        except OSError:
            continue
        for file in files:
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            run = _RUN_ID.search(text)
            icon = next((line.split("=", 1)[1].strip() for line in text.splitlines()
                         if line.lower().startswith("iconfile=")), "")
            if run and icon and os.path.isfile(icon):
                found.setdefault(run.group(1), icon)
    return found


def library_icon(steam: Path, appid: str) -> str | None:
    """The game's small icon in Steam's library cache: <appid>_icon.jpg (older Steam), or the <sha1>.jpg in the
    appcache/librarycache/<appid> folder (newer Steam)."""
    cache = steam / "appcache" / "librarycache"
    if not appid.isdigit():
        return None
    old = cache / f"{appid}_icon.jpg"
    if old.is_file():
        return str(old)
    try:
        return next((str(f) for f in sorted((cache / appid).glob("*.jpg")) if _HASH.fullmatch(f.stem)), None)
    except OSError:
        return None


def games(shortcut_folders=()) -> list[dict]:
    """[{name, exe, path, steam: True, icons}] of installed Steam games that have an .exe (`icons`: see the top;
    Steam's shortcuts are looked for in `shortcut_folders`)."""
    steam = steam_path()
    if not steam:
        return []
    icons = shortcut_icons(shortcut_folders)
    out = []
    for library in library_folders(steam):
        for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
            info = parse_manifest(manifest.read_text(encoding="utf-8", errors="ignore"))
            if info.get("appid") in _SKIP_APPS or not info.get("name") or not info.get("installdir"):
                continue
            folder = library / "steamapps" / "common" / info["installdir"]
            exe = main_exe(folder, info["name"]) if folder.exists() else None
            if exe:
                appid = info.get("appid", "")
                places = [icons.get(appid), str(exe), library_icon(steam, appid)]
                out.append({"name": info["name"], "exe": exe.name.lower(), "path": str(exe), "steam": True,
                            "icons": [p for p in places if p]})
    return out
