"""Apps the user can pick to block: Start Menu shortcuts, installed Steam games and apps with an open window."""
import os
from pathlib import Path

from blocker.apps import PROTECTED
from monitor import win

START_MENUS = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
               Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"]
SKIP_WORDS = ("uninstall", "unins0", "setup", "update", "helper", "crashreport")
# Windows parts that show up as "apps" but can't/shouldn't be blocked
HIDDEN = {"applicationframehost.exe", "textinputhost.exe", "systemsettings.exe", "shellexperiencehost.exe",
          "lockapp.exe", "control.exe", "mmc.exe"}


def _resolve_shortcuts() -> list[tuple[str, str]]:
    """(name, exe path) of Start Menu shortcuts that point at an .exe."""
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    try:
        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        out = []
        for root in START_MENUS:
            for lnk in root.rglob("*.lnk"):
                try:
                    target = shell.CreateShortcut(str(lnk)).TargetPath
                except Exception:
                    continue
                if target and target.lower().endswith(".exe") and os.path.exists(target):
                    out.append((lnk.stem, target))
        return out
    finally:
        comtypes.CoUninitialize()


def list_apps() -> list[dict]:
    """[{name, exe, path, running, steam}] sorted by name; one entry per exe."""
    from monitor import steam
    running = {}
    for _hwnd, title, path in win.top_windows():
        if path.lower().endswith(".exe"):
            running.setdefault(win.exe_name(path), (title, path))
    apps: dict[str, dict] = {}
    for name, path in _resolve_shortcuts():
        exe = win.exe_name(path)
        if any(w in name.lower() or w in exe for w in SKIP_WORDS):
            continue
        apps.setdefault(exe, {"name": name, "exe": exe, "path": path, "running": exe in running, "steam": False})
    try:
        games = steam.games()
    except OSError:
        games = []
    for game in games:   # Steam's own name wins over a shortcut's
        apps[game["exe"]] = {**game, "running": game["exe"] in running}
    for exe, (title, path) in running.items():
        if exe not in apps:
            name = Path(path).stem.replace("_", " ").title()
            apps[exe] = {"name": name, "exe": exe, "path": path, "running": True, "steam": False}
    hidden = HIDDEN | PROTECTED
    return sorted((a for a in apps.values() if a["exe"] not in hidden), key=lambda a: a["name"].lower())
