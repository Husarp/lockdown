"""Process listing / termination for app blocking (service side, standard library only)."""
import ctypes
import functools
import os
import re
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


# ---------- program files run by an interpreter (a Java game, a script) ----------
# A .jar runs as javaw.exe -jar game.jar, a script as python / cmd / powershell / wscript with the file in its
# command line: the process is the interpreter, which other programs use too. Such an item (target "game.jar") is
# the interpreter process whose command line names that file (hosted_file) - by its file name, wherever it is.
HOSTED = {".jar": "java", ".class": "java", ".py": "python", ".pyw": "python", ".bat": "cmd", ".cmd": "cmd", ".ps1": "powershell",
          ".vbs": "wsh", ".vbe": "wsh", ".js": "wsh", ".jse": "wsh", ".wsf": "wsh"}
INTERPRETERS = {"java.exe": "java", "javaw.exe": "java", "py.exe": "python", "pyw.exe": "python",
                "cmd.exe": "cmd", "powershell.exe": "powershell", "pwsh.exe": "powershell",
                "wscript.exe": "wsh", "cscript.exe": "wsh"}
_PYTHON = re.compile(r"pythonw?(\d+(\.\d+)*)?\.exe")   # python.exe, pythonw.exe, python3.12.exe (Store) ...
_PS1 = re.compile(r"""'([^']+?\.ps1)'|"([^"]+?\.ps1)"|([^\s'"&;|]+?\.ps1)(?=$|[\s'";&|)])""", re.IGNORECASE)
_PY_CLUSTER = re.compile(r"-[BdEiIOPqRsSuvVx]*[cm]")
_BAT_STOP = re.compile(r"[&|<>()^,;=]")
_BAT_SPLIT = re.compile(r"[\s&|<>()^,;=]+")
_BAT_SCAN = 8192   # (Windows allows 32 K command lines; a .bat name is looked for in the first 8 K only)
_CALL = re.compile(r"^call\s+", re.IGNORECASE)
JAVA_VALUE_OPTIONS = {"-cp", "-classpath", "--class-path", "-p", "--module-path", "--upgrade-module-path",
                      "--add-modules", "--add-opens", "--add-exports", "--add-reads", "--limit-modules"}
_CMD_SWITCH = re.compile(r'(?:^|\s)/[ckCK](?=\s|"|$)')
PROGRAM_TYPES = (".exe", *HOSTED)


def hosted_kind(target: str | None) -> str | None:
    """The interpreter family ("java", "python", "cmd", "powershell", "wsh") that runs this program file; None
    for an .exe (or anything else)."""
    return HOSTED.get(PureWindowsPath(exe_name(target)).suffix)


def is_hosted(target: str | None) -> bool:
    return hosted_kind(target) is not None


def interpreter(name: str | None) -> str | None:
    """The family of an interpreter's exe name ("javaw.exe" -> "java"), or None."""
    name = (name or "").lower()
    return INTERPRETERS.get(name) or ("python" if _PYTHON.fullmatch(name) else None)


def hosted_way(block_type: str | None) -> str | None:
    """How a program file is blocked: closed or minimised only. Cutting the internet would cut it for javaw.exe /
    python.exe - every Java / Python program - and "its background processes" would be whatever runs from its
    folder. None when nothing is left (a group that only cuts its members' internet)."""
    flags = block_flags(block_type) - {"internet", "background"}
    return make_block_type(flags) if flags else None


def _first_arg(cmd: str) -> tuple[str, str]:
    """(the program in a command line, the rest of it). The program is read as Windows does: up to the closing
    quote, or to the first space - no escapes."""
    cmd = cmd.lstrip(" \t")
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return (cmd[1:end], cmd[end + 1:]) if end > 0 else (cmd[1:], "")
    parts = re.split(r"[ \t]", cmd, maxsplit=1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def split_command_line(cmd: str | None) -> list[str]:
    """A Windows command line split into its arguments, as programs see them (CommandLineToArgvW's rules: quotes
    group, \\" is a quote, 2n backslashes before a quote are n, "" inside quotes is a quote)."""
    first, rest = _first_arg(cmd or "")
    if not first and not rest.strip():
        return []
    args, i, n = [first], 0, len(rest)
    while True:
        while i < n and rest[i] in " \t":
            i += 1
        if i >= n:
            return args
        arg, quoted = [], False
        while i < n and (quoted or rest[i] not in " \t"):
            c = rest[i]
            if c == "\\":
                j = i
                while j < n and rest[j] == "\\":
                    j += 1
                if j < n and rest[j] == '"':
                    arg.append("\\" * ((j - i) // 2))
                    if (j - i) % 2:   # an odd one escapes the quote
                        arg.append('"')
                        j += 1
                else:
                    arg.append("\\" * (j - i))
                i = j
            elif c == '"':
                if quoted and i + 1 < n and rest[i + 1] == '"':
                    arg.append('"')
                    i += 2
                else:
                    quoted = not quoted
                    i += 1
            else:
                arg.append(c)
                i += 1
        args.append("".join(arg))


def hosted_file(name: str | None, cmd: str | None) -> str | None:
    """The program file an interpreter process (exe `name`) runs, from its command line - as written there (a
    full path or just a file name; quotes removed, / turned into \\) - or None:
    - java / javaw: the file after -jar (java -Xmx2G -jar "C:\\Games\\game.jar" nogui);
    - python / pythonw / py: the first argument that isn't an option (none for -c / -m);
    - cmd: the first word after /c or /k (cmd.exe /c ""C:\\x\\start.bat" " is how Explorer starts a .bat);
    - powershell / pwsh: the first .ps1 in it (-File x.ps1, -Command "& 'x.ps1'", Run with PowerShell);
    - wscript / cscript: the first argument not starting with //.
    Other launch styles are in hosted_files. Not seen: a script started without its extension, an encoded command."""
    kind = interpreter(name)
    if not kind or not cmd:
        return None
    found = None
    if kind == "java":
        args = split_command_line(cmd)[1:]
        k = 0
        while k < len(args):   # (only the options: after the main class, "-jar" is the program's own argument)
            a = args[k]
            if a.lower() == "-jar":
                found = args[k + 1] if k + 1 < len(args) else None
                break
            if a in JAVA_VALUE_OPTIONS:
                k += 1
            elif not a.startswith(("-", "@")):
                break
            k += 1
    elif kind == "wsh":
        found = next((a for a in split_command_line(cmd)[1:] if not a.startswith("//")), None)
    elif kind == "python":
        args, k = split_command_line(cmd)[1:], 0
        while k < len(args):
            a = args[k]
            if a in ("-c", "-m", "-") or _PY_CLUSTER.match(a):   # a command, a module, stdin (-uc, -mmod: the rest of a cluster is its value)
                break
            if a in ("-W", "-X", "--check-hash-based-pycs"):   # (an option with its value after it)
                k += 2
                continue
            if not a.startswith("-"):
                found = a
                break
            k += 1
    elif kind == "cmd":
        rest = _first_arg(cmd)[1]
        switch = _CMD_SWITCH.search(rest)
        if switch:
            rest = _CALL.sub("", rest[switch.end():].strip(), count=1)   # (/c call x.bat)
            if rest.startswith('""'):   # /c ""C:\x y\start.bat" args": the outer quotes go
                rest = rest[1:]
            if rest.startswith('"'):
                end = rest.find('"', 1)
                found = rest[1:end] if end > 0 else rest[1:]
            elif rest:
                found = rest.split()[0]
    else:   # powershell
        match = _PS1.search(_first_arg(cmd)[1])
        found = next((g for g in match.groups() if g), None) if match else None
    found = (found or "").strip().strip('"').strip().replace("/", "\\")
    return found or None


def hosted_files(name: str | None, cmd: str | None) -> list[str]:
    """Every program file an interpreter process may be running, for matching against the listed ones: hosted_file's,
    plus what the other launch styles name - java -cp: each .jar on the class path and the main class
    ("com.game.Main" -> com.game.Main.class, and com\\game\\Main.class marked "-m:"); python -m tool: tool.py and tool\\__main__.py, python -m pkg.sub: pkg\\sub.py and pkg\\sub\\__main__.py (marked "-m:" - they
    match a listed file by its path, not its name alone: service.hosted_processes); cmd /c "cd /d X && x.bat":
    every .bat / .cmd anywhere in the /c command."""
    first = hosted_file(name, cmd)
    out = [first] if first else []
    kind = interpreter(name)
    if not kind or not cmd:
        return out
    if kind == "java" and not first:
        args, k = split_command_line(cmd)[1:], 0
        while k < len(args):
            a = args[k]
            if a in ("-cp", "-classpath", "--class-path") and k + 1 < len(args):
                out += [e for e in args[k + 1].replace("/", "\\").split(";") if e.lower().endswith(".jar")]
                k += 1
            elif a.startswith("--class-path="):
                out += [e for e in a[13:].replace("/", "\\").split(";") if e.lower().endswith(".jar")]
            elif a in JAVA_VALUE_OPTIONS:
                k += 1
            elif not a.startswith(("-", "@")):
                out.append(a + ".class")   # (the main class; -m module/Class is the first non-option too)
                if "." in a:   # (a packaged class is a listed ...\\com\\game\\Main.class: by its path, like python -m pkg.sub)
                    out.append("-m:" + a.replace(".", "\\") + ".class")
                break
            k += 1
    elif kind == "python":
        args, k = split_command_line(cmd)[1:], 0
        while k < len(args):
            a, module = args[k], None
            cluster = _PY_CLUSTER.match(a)
            if a == "-m" or (cluster and a[cluster.end() - 1] == "m"):
                module = (a[cluster.end():] if cluster and cluster.end() < len(a) else
                          args[k + 1] if k + 1 < len(args) else "")
            if module:
                rel = module.replace(".", "\\")
                out += ["-m:" + rel + ".py", "-m:" + rel + "\\__main__.py"]
                break
            if a in ("-c", "-") or cluster or not a.startswith("-"):
                break
            k += 2 if a in ("-W", "-X", "--check-hash-based-pycs") else 1
    elif kind == "cmd":
        switch = _CMD_SWITCH.search(_first_arg(cmd)[1])
        if switch:
            rest = _first_arg(cmd)[1][switch.end():].strip()[:_BAT_SCAN]
            seen = set()
            for part in rest.split('"'):   # (a quoted path may hold spaces: every piece between quotes is read whole and by words)
                whole = part.strip()
                for word in ([] if _BAT_STOP.search(whole) else [whole]) + _BAT_SPLIT.split(part):
                    if word.lower().endswith((".bat", ".cmd")) and word not in seen:
                        seen.add(word)
                        out.append(word.replace("/", "\\"))
    return out


def shortcut_program(target: str | None, arguments: str | None = "", workdir: str | None = "") -> str | None:
    """What a shortcut (.lnk) starts, for blocking: the program file its interpreter runs (javaw.exe with
    -jar game.jar -> <its working folder>\\game.jar), else its target - an .exe or a program file. None when it
    starts something else (a document, a folder, a web address)."""
    target = (target or "").strip().strip('"')
    name = exe_name(target)
    if interpreter(name):
        file = hosted_file(name, f'"{target}" {arguments or ""}')
        if file and is_hosted(file):
            if workdir and not PureWindowsPath(file).is_absolute():
                file = str(PureWindowsPath(workdir) / file)
            return file
        return target
    return target if name.endswith(PROGRAM_TYPES) else None


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
    # (a program file has no "background processes": hosted_way)
    return helper_folder(path) if kills_background(block_type) and not is_hosted(path) else None


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


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


PROCESS_COMMAND_LINE_INFORMATION = 60   # NtQueryInformationProcess, Windows 8.1+
TOO_SMALL = {0xC0000004, 0x80000005, 0xC0000023}   # STATUS_INFO_LENGTH_MISMATCH, _BUFFER_OVERFLOW, _BUFFER_TOO_SMALL
_ntdll = ctypes.WinDLL("ntdll")
_ntdll.NtQueryInformationProcess.restype = ctypes.c_long
_ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
                                             ctypes.POINTER(wintypes.ULONG)]


def command_line(pid: int) -> str | None:
    """A process's command line (None if it can't be read - gone, or protected). Needs only the limited query
    right, like process_path, so the service reads any user's programs."""
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.ULONG(0)
        buf = ctypes.create_string_buffer(2048)
        for _ in range(2):
            status = _ntdll.NtQueryInformationProcess(handle, PROCESS_COMMAND_LINE_INFORMATION, buf, len(buf),
                                                      ctypes.byref(size)) & 0xFFFFFFFF
            if status not in TOO_SMALL or size.value <= len(buf):   # (a long one - Java's -cp: asked again)
                break
            buf = ctypes.create_string_buffer(size.value)
        if status:
            return None
        text = UNICODE_STRING.from_buffer(buf)
        return ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Buffer and text.Length else ""
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
