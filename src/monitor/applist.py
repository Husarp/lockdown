"""Apps the user can pick to block: Start Menu shortcuts, installed Steam games and apps with an open window.

Each app also brings the places its icon can be taken from, best first ("icons": "path" or "path,index"), when there is
more than its own program file: the shortcut's own icon (IconLocation), Steam's icon of the game (a game's uninstall entry
and Steam's shortcuts name it), and the DisplayIcon of the uninstall entry (registry) whose install folder holds the
program - so an app never run since Lockdown was installed gets its real icon too (resident/icons.py)."""
import os
from pathlib import Path, PureWindowsPath

from blocker.apps import PROGRAM_TYPES, PROTECTED, shortcut_program
from monitor import win

START_MENUS = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
               Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"]
SKIP_WORDS = ("uninstall", "unins0", "setup", "update", "helper", "crashreport")
# Windows parts that show up as "apps" but can't/shouldn't be blocked
HIDDEN = {"applicationframehost.exe", "textinputhost.exe", "systemsettings.exe", "shellexperiencehost.exe",
          "lockapp.exe", "control.exe", "mmc.exe"}


UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
DESKTOPS = [Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop", Path(os.environ.get("USERPROFILE", "")) / "Desktop"]


def _resolve_shortcuts() -> list[tuple[str, str, str]]:
    """(name, exe path, icon place) of Start Menu shortcuts that point at an .exe. The icon place is the shortcut's
    IconLocation ("path,index"), or "" when it names no file."""
    import comtypes
    import comtypes.client
    from resident.icons import split_location
    comtypes.CoInitialize()
    try:
        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        out = []
        for root in START_MENUS:
            for lnk in root.rglob("*.lnk"):
                try:
                    link = shell.CreateShortcut(str(lnk))
                    target = link.TargetPath
                    icon = link.IconLocation or ""
                except Exception:
                    continue
                if target and target.lower().endswith(".exe") and os.path.exists(target):
                    out.append((lnk.stem, target, icon if split_location(icon)[0] else ""))
        return out
    finally:
        comtypes.CoUninitialize()


def read_shortcut(path: str) -> tuple[str, str, str]:
    """(target, arguments, working folder) of a shortcut (.lnk)."""
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    try:
        link = comtypes.client.CreateObject("WScript.Shell", dynamic=True).CreateShortcut(str(path))
        return link.TargetPath or "", link.Arguments or "", link.WorkingDirectory or ""
    finally:
        comtypes.CoUninitialize()


KINDS_TEXT = "programs (.exe), Java games (.jar), scripts (.py, .bat, .cmd, .ps1, .vbs, .js ...) and shortcuts to them"


def program_file(path: str, read=read_shortcut) -> str:
    """The program a file picked in "Pick a program file..." stands for: itself (an .exe, a .jar, a script), or what
    a shortcut starts - a Java game's shortcut runs javaw.exe -jar game.jar: that is game.jar
    (apps.shortcut_program). Raises ValueError with a message for you."""
    name = path.rsplit("\\", 1)[-1]
    if path.lower().endswith(".lnk"):
        try:
            found = shortcut_program(*read(path))
        except Exception:
            raise ValueError(f"Couldn't read the shortcut {name}.") from None
        if not found:
            raise ValueError(f"{name} doesn't start a program. Lockdown can block {KINDS_TEXT}.")
        return found
    if path.lower().endswith(PROGRAM_TYPES):
        return path
    raise ValueError(f"Lockdown can block {KINDS_TEXT} - not {name}.")


def _same_place(a: str, b: str) -> bool:
    from resident.icons import split_location
    (pa, ia), (pb, ib) = split_location(a), split_location(b)
    return ia == ib and pa.lower() == pb.lower()


def _uninstall_entries() -> list[tuple[str, str]]:
    """(DisplayIcon, InstallLocation) of the programs in Windows' uninstall list (both registry views, and the user's)."""
    import winreg
    out = []
    for hive, flag in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY), (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | flag) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, i)) as key:
                            values = []
                            for name in ("DisplayIcon", "InstallLocation"):
                                try:
                                    values.append(str(winreg.QueryValueEx(key, name)[0] or ""))
                                except OSError:
                                    values.append("")
                    except OSError:
                        continue
                    if values[0]:
                        out.append((values[0], values[1]))
        except OSError:
            continue
    return out


def uninstall_folders(entries) -> list[tuple[str, str]]:
    """(install folder in lower case ending in "\\", DisplayIcon) of the uninstall entries, deepest folder first - the
    folder is InstallLocation, else DisplayIcon's own folder. Uninstallers' icons and folders less than two levels deep
    ("C:\\Program Files" written by a careless installer) are left out. Worked out once per scan."""
    from resident.icons import split_location
    out = []
    for icon, folder in entries:
        file = split_location(icon)[0]
        if not file or any(w in PureWindowsPath(file).name.lower() for w in ("unins", "uninst")):
            continue
        base = PureWindowsPath(folder.strip().strip('"') or PureWindowsPath(file).parent)
        if len(base.parts) >= 3:
            out.append((len(base.parts), str(base).lower().rstrip("\\") + "\\", icon))
    out.sort(key=lambda f: -f[0])
    return [(base, icon) for _depth, base, icon in out]


def uninstall_icon(path: str, folders) -> str | None:
    """The DisplayIcon of the uninstall entry whose install folder holds the program at `path` (the deepest one;
    `folders` from uninstall_folders), or None."""
    where = path.lower()
    return next((icon for base, icon in folders if where.startswith(base)), None)


def icon_places(path: str, before=(), after=()) -> list[str]:
    """The places to take an app's icon from, best first, around its own program: [] when that is all there is."""
    places = [p for p in before if p] + [path] + [p for p in after if p]
    out = []
    for place in places:
        if not any(_same_place(place, seen) for seen in out):
            out.append(place)
    return out if len(out) > 1 else []


def list_apps() -> list[dict]:
    """[{name, exe, path, running, steam, icons}] sorted by name; one entry per exe (`icons`: [] when there is nothing
    but the program itself to take the icon from)."""
    from monitor import steam
    running = {}
    for _hwnd, title, path in win.top_windows():
        if path.lower().endswith(".exe"):
            running.setdefault(win.exe_name(path), (title, path))
    try:
        uninstall = uninstall_folders(_uninstall_entries())
    except Exception:
        uninstall = []
    apps: dict[str, dict] = {}
    for name, path, icon in _resolve_shortcuts():
        exe = win.exe_name(path)
        if any(w in name.lower() or w in exe for w in SKIP_WORDS) or exe in apps:
            continue
        apps[exe] = {"name": name, "exe": exe, "path": path, "running": exe in running, "steam": False,
                     "icons": icon_places(path, [icon], [uninstall_icon(path, uninstall)])}
    try:
        games = steam.games(START_MENUS + DESKTOPS)
    except OSError:
        games = []
    for game in games:   # Steam's own name wins over a shortcut's; its uninstall entry's icon is Steam's .ico of the game
        apps[game["exe"]] = {**game, "running": game["exe"] in running,
                             "icons": icon_places(game["path"], [uninstall_icon(game["path"], uninstall)] + game.get("icons", []))}
    for exe, (title, path) in running.items():
        if exe not in apps:
            name = Path(path).stem.replace("_", " ").title()
            apps[exe] = {"name": name, "exe": exe, "path": path, "running": True, "steam": False}
    hidden = HIDDEN | PROTECTED
    return sorted((a for a in apps.values() if a["exe"] not in hidden), key=lambda a: a["name"].lower())
