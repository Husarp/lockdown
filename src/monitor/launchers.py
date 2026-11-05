"""Installed games outside Steam. Read launcher records, never search entire drives.

Registry views cover older 32-bit launchers as well as 64-bit installs. Manifest paths are also used by the
service to recognise a game's whole folder, including games installed outside the launcher's default library.
No launcher is run and no account, credential or network access is needed.
"""
import functools
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
REGISTRY = (
    (r"Software\Wargaming.net", "Wargaming"),
    (r"Software\Wargaming.net\Games", "Wargaming"),
    (r"Software\Wargaming.net\GameCenter\Apps", "Wargaming"),
    (r"Software\Ubisoft\Launcher\Installs", "Ubisoft"),
    (r"Software\GOG.com\Games", "GOG"),
    (r"Software\Blizzard Entertainment", "Battle.net"),
    (r"Software\EA Games", "EA"),
    (r"Software\Electronic Arts\EA Games", "EA"),
)
LAUNCHERS = {
    "Wargaming": ("Wargaming Game Center", "wgc.exe"), "Epic": ("Epic Games Launcher", "epicgameslauncher.exe"),
    "Battle.net": ("Battle.net", "battle.net.exe"), "EA": ("EA app", "eadesktop.exe"),
    "Ubisoft": ("Ubisoft Connect", "upc.exe"), "GOG": ("GOG Galaxy", "galaxyclient.exe"),
    "Riot": ("Riot Client", "riotclientservices.exe"),
}
_VENDORS = (("wargaming", "Wargaming"), ("epic games", "Epic"), ("blizzard", "Battle.net"),
            ("battle.net", "Battle.net"), ("electronic arts", "EA"), ("ea games", "EA"),
            ("ubisoft", "Ubisoft"), ("gog", "GOG"), ("riot", "Riot"))
_PATHS = ("installlocation", "installdir", "install dir", "installpath", "install_path", "path", "game_path", "gamepath")
_cache = (0.0, [])


def registry_entries():
    """(launcher, key name, lowercase values) from launcher and uninstall registry keys."""
    import winreg
    out = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for location, launcher in (*REGISTRY, (UNINSTALL, "")):
                try:
                    with winreg.OpenKey(hive, location, 0, winreg.KEY_READ | view) as root:
                        for i in range(winreg.QueryInfoKey(root)[0]):
                            try:
                                sub = winreg.EnumKey(root, i)
                                with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view) as key:
                                    values = {}
                                    for j in range(winreg.QueryInfoKey(key)[1]):
                                        name, value, _kind = winreg.EnumValue(key, j)
                                        if isinstance(value, str):
                                            values[name.lower()] = value
                                source = launcher or next((l for word, l in _VENDORS if word in
                                    (values.get("publisher", "") + " " + sub).lower()), "")
                                if source:
                                    out.append((source, sub, values))
                            except OSError:
                                continue
                except OSError:
                    continue
    return out


def safe_folder(folder: str) -> bool:
    """Never turn a drive, shared library, Windows, or a user's general folder into one game."""
    p = PureWindowsPath(folder)
    parts = [s.lower() for s in p.parts]
    windows = PureWindowsPath(os.environ.get("SystemRoot", r"C:\Windows"))
    return ((p.is_absolute() or Path(folder).is_absolute()) and len(parts) >= 2 and p != windows and windows not in p.parents
            and parts[-1] not in {"program files", "program files (x86)", "games", "epic games", "gog games",
                                  "riot games", "ea games", "common", "desktop", "documents", "downloads",
                                  "users", "programdata", "steamapps", "steamlibrary"}
            and not (len(parts) >= 2 and parts[1] == "users" and len(parts) <= 3))


def _record(name, folder, launcher, executable="") -> dict | None:
    folder = os.path.expandvars(str(folder or "").strip().strip('"'))
    if not folder or not safe_folder(folder) or not Path(folder).is_dir():
        return None
    name = str(name or Path(folder).name)
    if any(word in name.lower() for word in ("game center", "gamecenter", "battle.net", "launcher", "gog galaxy",
                                             "ea desktop", "ea app", "ubisoft connect", "riot client")):
        return None
    if name.lower().startswith("wot") or name.lower() in ("world_of_tanks", "worldoftanks"):
        name = "World of Tanks"
    return {"name": name, "folder": folder, "launcher": launcher, "executable": str(executable or "")}


def _json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _xml_records(path, launcher):
    """WGC games.xml/preferences.xml and EA LocalContent manifests (fields or attributes)."""
    tree = ET.fromstring(path.read_text(encoding="utf-8-sig"))
    out = []
    for node in tree.iter():
        values = {k.lower(): v for k, v in node.attrib.items()}
        values.update({c.tag.lower(): (c.text or "").strip() for c in node})
        folder = next((values[k] for k in (*_PATHS, "installpath", "install_path") if values.get(k)), "")
        if folder:
            out.append(_record(values.get("name") or values.get("display_name") or values.get("game_id"),
                               folder, launcher, values.get("executable", "")))
    return out


def installations(refresh=False) -> list[dict]:
    """Installed game roots and optional launch executable; cached for a minute, shared by discovery and blocking."""
    global _cache
    now = time.monotonic()
    if not refresh and _cache[0] and now - _cache[0] < 60:
        return _cache[1]
    out = []
    for launcher, key, values in registry_entries():
        folder = next((values[k] for k in _PATHS if values.get(k)), "")
        out.append(_record(values.get("displayname") or values.get("gamename") or Path(folder).name or key,
                           folder, launcher, values.get("exe", "")))
    data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    manifests = [(p, "Epic", "epic") for p in (data / "Epic/EpicGamesLauncher/Data/Manifests").glob("*.item")]
    manifests += [(data / "Epic/UnrealEngineLauncher/LauncherInstalled.dat", "Epic", "epic-list")]
    manifests += [(p, "Riot", "riot") for p in (data / "Riot Games/Metadata").glob("*/*.yaml")]
    manifests += [(data / "Riot Games/RiotClientInstalls.json", "Riot", "riot-list")]
    for base in (data, local):
        for filename in ("games.xml", "preferences.xml"):
            manifests.append((base / "Wargaming.net/GameCenter" / filename, "Wargaming", "xml"))
    for base in (data / "Origin/LocalContent", data / "EA Desktop/LocalContent"):
        manifests += [(p, "EA", "xml") for p in base.glob("*/*.mfst")]
    for path, launcher, kind in manifests:
        try:
            if kind == "xml":
                out.extend(_xml_records(path, launcher))
            elif kind == "riot":
                text = path.read_text(encoding="utf-8-sig")
                fields = dict(re.findall(r'^([\w_]+):\s*[\'"]?(.*?)[\'"]?\s*$', text, re.M))
                name = fields.get("product_display_name") or path.parent.name.split(".")[0].replace("_", " ").title()
                out.append(_record(name, fields.get("product_install_full_path"), launcher))
            elif kind == "riot-list":
                # Keys of associated_client are game install paths; values point to the shared Riot client.
                for folder in _json(path).get("associated_client", {}):
                    out.append(_record(None, folder, launcher))
            else:
                info = _json(path)
                for row in info.get("InstallationList", []) if kind == "epic-list" else [info]:
                    if row.get("bIsIncompleteInstall"):
                        continue
                    out.append(_record(row.get("DisplayName") or row.get("AppName"), row.get("InstallLocation"),
                                       launcher, row.get("LaunchExecutable", "")))
        except (OSError, ValueError, TypeError, AttributeError, ET.ParseError):
            continue   # one damaged/stale launcher record must not hide the other games
    found = {}
    for game in out:
        if game:
            found.setdefault(game["folder"].lower().replace("/", "\\").rstrip("\\"), game)
    _cache = (now, list(found.values()))
    return _cache[1]


def game_root(path: str) -> str | None:
    where = path.lower().replace("/", "\\")
    roots = (g["folder"].lower().replace("/", "\\").rstrip("\\") for g in installations())
    return (next((r for r in sorted(roots, key=len, reverse=True) if where.startswith(r + "\\")), None)
            or _marker_root(path, int(time.monotonic() // 60)))


@functools.lru_cache(maxsize=4096)
def _marker_root(path: str, minute: int) -> str | None:
    """Install markers survive launcher exit and are readable by SYSTEM even for a per-user launcher registry.
    Limit the walk and cache it: no directory-tree scan on the service's frequent process checks."""
    file = Path(path)
    if not file.is_absolute():
        return None
    for folder in list(file.parents)[:5]:
        if not safe_folder(str(folder)):
            continue
        try:
            if ((folder / "wgc_metadata").is_dir() or (folder / ".egstore").is_dir()
                    or (folder / ".build.info").is_file() or (folder / "uplay_install.state").is_file()
                    or (folder / "__Installer/installerdata.xml").is_file()
                    or next(folder.glob("goggame-*.info"), None)):
                return str(folder).lower().replace("/", "\\").rstrip("\\")
        except OSError:
            continue
    return None


def games() -> list[dict]:
    from monitor.steam import main_exe
    out = []
    sources = set()
    for game in installations(refresh=True):
        sources.add(game["launcher"])
        folder = Path(game["folder"])
        exe = folder / game["executable"] if game["executable"] else None
        if (not exe or not exe.is_file() or exe.suffix.lower() != ".exe"
                or not exe.resolve().is_relative_to(folder.resolve())):
            exe = main_exe(folder, game["name"])
        if exe:
            out.append({"name": game["name"], "exe": exe.name.lower(), "path": str(exe),
                        "steam": False, "game": True, "launcher": game["launcher"]})
    # A selectable fallback even when a game's anti-cheat refuses TerminateProcess. Closing the launcher prevents
    # future launches; it does not promise to close an already-running protected game.
    for source in sorted(sources):
        name, exe = LAUNCHERS[source]
        out.append({"name": name, "exe": exe, "path": "", "steam": False, "launcher": source})
    return out
