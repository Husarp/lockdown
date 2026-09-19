"""Process listing / termination for app blocking (service side, standard library only)."""
import ctypes
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
             "rundll32.exe", "dllhost.exe", "msiexec.exe", "consent.exe", "logonui.exe"}


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


def helper_folder(app_path: str | None) -> str | None:
    """The app's install folder, if it's safe to treat everything running from it as the app's background
    processes: not inside Windows, not a drive root / top-level folder, not a user's own folders."""
    if not app_path:
        return None
    folder = PureWindowsPath(app_path).parent
    lowered = [p.lower() for p in folder.parts]
    for i in range(len(lowered) - 2):   # a Steam game: its whole folder (the exe may sit in bin\win64 etc.)
        if lowered[i:i + 2] == ["steamapps", "common"]:
            return str(PureWindowsPath(*folder.parts[:i + 3])).lower()
    windows = PureWindowsPath(os.environ.get("SystemRoot", r"C:\Windows"))
    parts = [p.lower() for p in folder.parts[1:]]
    if len(parts) < 2 or folder == windows or windows in folder.parents:
        return None
    if parts[0] == "users" and (len(parts) < 3 or parts[2] in ("desktop", "downloads", "documents")):
        return None
    return str(folder).lower()


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
