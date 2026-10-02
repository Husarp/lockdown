"""Process listing / termination for app blocking (service side, standard library only)."""
import ctypes
import functools
import os
from ctypes import wintypes
from pathlib import PureWindowsPath

TH32CS_SNAPPROCESS = 0x2
PROCESS_TERMINATE = 0x1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Never block these: Windows would break (or Lockdown itself would).
PROTECTED = {"system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
             "svchost.exe", "explorer.exe", "dwm.exe", "fontdrvhost.exe", "sihost.exe", "ctfmon.exe",
             "taskhostw.exe", "runtimebroker.exe", "searchhost.exe", "startmenuexperiencehost.exe",
             "python.exe", "pythonw.exe", "conhost.exe", "audiodg.exe", "spoolsv.exe",
             "rundll32.exe", "dllhost.exe", "msiexec.exe", "consent.exe", "logonui.exe",
             "lockdown.exe", "lockdownservice.exe"}   # Lockdown itself, so you can't block / soft-lock it


# What happens to a blocked app (blocked_items.block_type): a comma-separated set of flags,
# e.g. "close,internet". close and minimize exclude each other; background (also close its background
# processes) only goes with close. None = "close" (the default).
FLAGS = ("close", "background", "minimize", "internet")
_LEGACY = {"kill": {"close"}, "both": {"close", "internet"}, "minimize": {"minimize"},
           "minimize_fw": {"minimize", "internet"}, "firewall": {"internet"}}   # 0.4-0.7 values


def block_flags(block_type: str | None) -> set[str]:
    if not block_type:
        return {"close"}
    return set(_LEGACY.get(block_type) or block_type.split(","))


def make_block_type(flags) -> str:
    return ",".join(f for f in FLAGS if f in flags)


def kills(block_type: str | None) -> bool:
    return "close" in block_flags(block_type)


def kills_background(block_type: str | None) -> bool:
    return {"close", "background"} <= block_flags(block_type)


def minimizes(block_type: str | None) -> bool:
    return "minimize" in block_flags(block_type)


def firewalls(block_type: str | None) -> bool:
    return "internet" in block_flags(block_type)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD)]


def list_processes_full() -> list[tuple[int, int, str]]:
    """(pid, parent pid, lowercase exe name) of every running process."""
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    out = []
    try:
        entry = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile.lower()))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return out


def list_processes() -> list[tuple[int, str]]:
    """(pid, lowercase exe name) of every running process."""
    return [(pid, exe) for pid, _ppid, exe in list_processes_full()]


def descendants(pids: set[int], procs: list[tuple[int, int, str]]) -> set[int]:
    """Every process started (directly or not) by one of `pids`."""
    children: dict[int, list[int]] = {}
    for pid, ppid, _exe in procs:
        if pid != ppid:
            children.setdefault(ppid, []).append(pid)
    out, todo = set(), list(pids)
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in out and child not in pids:
                out.add(child)
                todo.append(child)
    return out


def exe_name(target: str | None) -> str:
    """The process name an app item stands for: lowercase, without quotes or a folder
    ("C:\\Games\\Foo\\Foo.exe" -> "foo.exe"). Windows lists processes by bare name, so a target typed as a full path
    used to match nothing at all - the app was never closed and its time never counted."""
    text = (target or "").strip().strip('"').strip()
    return PureWindowsPath(text).name.lower() if text else ""


def typed_path(target: str | None) -> str | None:
    """The full path of an app item whose target was typed as one (else None)."""
    text = (target or "").strip().strip('"').strip()
    return text if "\\" in text or "/" in text else None


# Unreal Engine games run as <Project>-Win64-Shipping.exe (-WinGDK- in the Xbox / Game Pass build); the
# <Project>.exe you pick only starts it and exits. The game is that exe wherever it is installed.
SHIPPING_SUFFIXES = ("-win64-shipping.exe", "-wingdk-shipping.exe", "-win32-shipping.exe")


@functools.lru_cache(maxsize=4096)
def names_of(target: str | None) -> frozenset[str]:
    """Every process name that IS the app item: its own exe, and for "Game.exe" Unreal's
    "game-win64-shipping.exe" (SHIPPING_SUFFIXES). Matching the exe on the list alone let an Unreal game that is
    not under Steam - or one whose starter Steam skips - run, uncounted, outside its allowed hours (0.84.1)."""
    exe = exe_name(target)
    if not exe:
        return frozenset()
    stem = exe[:-4] if exe.endswith(".exe") else ""
    if not stem or stem.endswith("-shipping"):
        return frozenset({exe})
    return frozenset({exe, *(stem + s for s in SHIPPING_SUFFIXES)})


# Game libraries: a game is installed in a folder of its own inside one of these, and everything running from that
# folder is the game. Each entry is the folders' names (lowercase) just above a game's own folder.
GAME_LIBRARIES = (("steamapps", "common"), ("epic games",), ("gog galaxy", "games"), ("gog games",),
                  ("xboxgames",), ("riot games",), ("ea games",), ("ubisoft game launcher", "games"),
                  ("amazon games", "library"), ("itch", "apps"))


def game_folder(path: str | None) -> str | None:
    """A game's whole install folder (lowercase) for any path inside it - steamapps\\common\\<game>,
    Epic Games\\<game>, GOG, XboxGames, Riot ... (GAME_LIBRARIES) - else None."""
    if not path:
        return None
    folder = PureWindowsPath(path).parent
    lowered = [p.lower() for p in folder.parts]
    for library in GAME_LIBRARIES:
        n = len(library)
        for i in range(1, len(lowered) - n):   # (not the drive; the exe may sit deeper, in Binaries\\Win64 etc.)
            if tuple(lowered[i:i + n]) == library:
                return str(PureWindowsPath(*folder.parts[:i + n + 1])).lower()
    return None


def steam_game_folder(path: str | None) -> str | None:
    """A Steam game's whole folder (steamapps\\common\\<game>, lowercase) for any path inside it, else None."""
    found = game_folder(path)
    return found if found and "\\steamapps\\common\\" in found else None


def helper_folder(app_path: str | None) -> str | None:
    """The app's install folder, if it's safe to treat everything running from it as the app's background
    processes: not inside Windows, not a drive root / top-level folder, not a user's own folders."""
    if not app_path:
        return None
    if game := game_folder(app_path):   # a game from a game library: its whole folder
        return game
    folder = PureWindowsPath(app_path).parent
    windows = PureWindowsPath(os.environ.get("SystemRoot", r"C:\Windows"))
    parts = [p.lower() for p in folder.parts[1:]]
    if len(parts) < 2 or folder == windows or windows in folder.parents:
        return None
    if parts[0] == "users" and (len(parts) < 3 or parts[2] in ("desktop", "downloads", "documents")):
        return None
    return str(folder).lower()


def app_folder(item: dict) -> str | None:
    """The folder whose every process IS this app - closed with it, and its time counted for it:
    - a game's whole install folder, always, when it is in a game library (Steam, Epic, GOG, Xbox, Riot ...:
      game_folder). A game rarely runs as the exe you picked: Unreal games start Game-Win64-Shipping.exe from
      Binaries\\Win64, others go through a launcher. Matching the exe name alone let the real game run untouched
      outside its allowed hours, and its time and allowance were never counted;
    - any other app's install folder, when "also close its background processes" is ticked.
    Otherwise None (an ordinary install folder can hold other programs - Word's holds Excel). What such an app
    starts from its own folder is still the app (service.Enforcer.track_families)."""
    return _app_folder(item.get("app_path") or typed_path(item.get("target")), item.get("block_type"))


@functools.lru_cache(maxsize=4096)
def _app_folder(path: str | None, block_type: str | None) -> str | None:
    if not path:
        return None
    if game := game_folder(path):
        return game
    return helper_folder(path) if kills_background(block_type) else None


def in_folder(path: str | None, folder: str) -> bool:
    return bool(path) and path.lower().replace("/", "\\").startswith(folder + "\\")


EXES_DEPTH = 4   # how deep exes_in looks inside a game's folder (an Unreal game's is <Project>\\Binaries\\Win64)


@functools.lru_cache(maxsize=64)
def exes_in(folder: str) -> dict[str, str]:
    """{lowercase exe name: full path} of the .exe files in a folder, at most EXES_DEPTH folders down (the first one
    found by name wins, shallowest first)."""
    out: dict[str, str] = {}
    base = folder.rstrip("\\/").count(os.sep)
    for root, dirs, files in os.walk(folder):
        if root.count(os.sep) - base >= EXES_DEPTH:
            dirs[:] = []
        for f in files:
            if f.lower().endswith(".exe"):
                out.setdefault(f.lower(), os.path.join(root, f))
    return out


def unreal_starter(shipping_path: str, exe: str) -> str | None:
    """Where Unreal puts the starter `exe` of a running Shipping exe: <game>\\<exe> for
    <game>\\<Project>\\Binaries\\Win64\\<Project>-Win64-Shipping.exe - if that file is there."""
    parts = PureWindowsPath(shipping_path).parents
    if len(parts) < 4 or parts[0].name.lower() not in ("win64", "wingdk", "win32") or parts[1].name.lower() != "binaries":
        return None
    candidate = str(parts[3] / exe)
    return candidate if is_file(candidate) else None


is_file = os.path.isfile


@functools.lru_cache(maxsize=8192)
def parents_of(path: str | None) -> tuple[str, ...]:
    """Every folder a (lowercase) exe path is in, innermost first - in_folder(path, f) is True for exactly these."""
    if not path:
        return ()
    parts = path.lower().replace("/", "\\").split("\\")[:-1]
    return tuple("\\".join(parts[:i]) for i in range(len(parts), 0, -1))


def merge_blocks(old: dict | None, new: dict) -> dict:
    """Two blocks on the same exe (a second item for it, a category blocker, an old item saved as a full path):
    one block that does everything either asks for, closing winning over minimising. The later one used to replace
    the earlier - a "minimize" blocker could quietly undo a "close outside these hours" (0.84.1)."""
    if old is None:
        return new
    flags = block_flags(old["item"].get("block_type")) | block_flags(new["item"].get("block_type"))
    if "close" in flags:
        flags.discard("minimize")
    else:
        flags.discard("background")
    base, other = (new, old) if kills(new["item"].get("block_type")) and not kills(old["item"].get("block_type")) \
        else (old, new)
    item = {**base["item"], "block_type": make_block_type(flags),
            "app_path": base["item"].get("app_path") or other["item"].get("app_path")}
    return {**base, "item": item}


def process_path(pid: int) -> str | None:
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        return buf.value if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else None
    finally:
        _k32.CloseHandle(handle)


def start_time(pid: int) -> float | None:
    """When the process was started (unix seconds), or None."""
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not _k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel),
                                    ctypes.byref(user)):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime   # 100 ns since 1601-01-01
        return ticks / 10_000_000 - 11_644_473_600
    finally:
        _k32.CloseHandle(handle)


def terminate(pid: int) -> bool:
    handle = _k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(_k32.TerminateProcess(handle, 1))
    finally:
        _k32.CloseHandle(handle)
