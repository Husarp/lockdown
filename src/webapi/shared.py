"""Api shared by several pages: the app picker's list (`apps_list`) and the program-file dialog (`pick_program_file`).

Both only read: they change nothing. The list comes from the agent's AppCatalog (resident/catalog.py, ready before the
picker opens - `ctx.catalog` if the agent set one, else the shared one). The page never says which file to open: the
dialog is the owner's, and what comes back is checked here in Python (an unknown file type is refused; Lockdown's own
programs and Windows' core processes can't be picked).
"""
import sys

from blocker.apps import PROGRAM_TYPES, PROTECTED, exe_name, hosted_kind, interpreter
from resident import catalog as app_catalog

from . import core
from .core import ApiError, read

FILE_TYPES = (*PROGRAM_TYPES, ".lnk")     # what the dialog offers: .exe .jar .py .bat ... and shortcuts
FILE_TITLE = "Programs, Java games, scripts, shortcuts"


def _catalog(ctx):
    return getattr(ctx, "catalog", None) or app_catalog.shared()


@read
def apps_list(ctx, filter="all", query=""):
    """The apps to pick from: {apps: [{name, exe, path, running, steam}] (best match first, at most 60), total,
    loading}. `filter`: "all" / "running" / "games". While the catalog is still loading `loading` is true and the page
    asks again when it gets the `apps_ready` push."""
    if filter not in app_catalog.FILTERS:
        raise ApiError("Unknown filter.")
    if not isinstance(query, str):
        raise ApiError("Bad request: the search must be text.")
    return _catalog(ctx).search(filter, query)


@core.internal
def describe_program_file(picked: str, read_shortcut=None) -> dict:
    """What a picked file stands for: {path, exe, name, kind, whole_interpreter}. `kind` is exe / java / python / cmd /
    powershell / wsh; a shortcut stands for what it starts (a Java game's shortcut -> the .jar). Raises ApiError with a
    message for the owner when it isn't a program Lockdown can block."""
    from monitor import applist
    try:
        path = applist.program_file(picked, read_shortcut) if read_shortcut else applist.program_file(picked)
    except ValueError as error:
        raise ApiError(str(error)) from None
    exe = exe_name(path)
    if exe in PROTECTED:
        raise ApiError(f"{exe} can't be blocked (Lockdown needs it). To block one game or script, pick its own file "
                       "(.jar, .py, .bat ...) or its shortcut.")
    name = picked.replace("/", "\\").rsplit("\\", 1)[-1].rsplit(".", 1)[0]
    return {"path": path, "exe": exe, "name": name, "kind": hosted_kind(path) or "exe",
            # java.exe / cmd.exe ... itself: blocking it blocks every program it runs - the page asks first
            "whole_interpreter": bool(interpreter(exe))}


@read
def pick_program_file(ctx):
    """Open the Open dialog for a program file (.exe, .jar, a script, a shortcut) and say what was picked
    (describe_program_file), or null when cancelled."""
    picked = _ask_path(getattr(ctx, "main_window", None))
    return describe_program_file(picked) if picked else None


# ---------------------------------------------------------------- the dialog (Windows; thin glue)

OFN_NOCHANGEDIR, OFN_HIDEREADONLY, OFN_PATHMUSTEXIST, OFN_FILEMUSTEXIST = 0x8, 0x4, 0x800, 0x1000
OFN_EXPLORER, OFN_NODEREFERENCELINKS = 0x80000, 0x100000


def _ask_path(window) -> str | None:
    """The file the owner picked, or None. Windows' own Open dialog first, with the shortcut kept as the shortcut
    (OFN_NODEREFERENCELINKS: the standard dialog would hand back only a Java shortcut's target, javaw.exe, without the
    game it runs); pywebview's `create_file_dialog` if that can't be shown."""
    patterns = ";".join("*" + e for e in FILE_TYPES)
    if sys.platform == "win32":
        try:
            return _open_dialog(_handle_of(window), f"{FILE_TITLE}\0{patterns}\0All files\0*.*\0\0")
        except Exception:
            pass
    if window is None:
        raise ApiError("The file dialog isn't available.")
    import webview
    kind = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
    result = window.create_file_dialog(kind, file_types=(f"{FILE_TITLE} ({patterns.replace(';', '; ')})", "All files (*.*)"))
    return result[0].replace("/", "\\") if result else None


def _handle_of(window) -> int:
    """The main window's Win32 handle (the dialog's owner), or 0."""
    try:
        return int(window.native.Handle.ToInt64())
    except Exception:
        return 0


def _open_dialog(owner: int, filters: str) -> str | None:
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
                    ("lpstrFilter", ctypes.c_void_p), ("lpstrCustomFilter", ctypes.c_void_p),
                    ("nMaxCustFilter", wintypes.DWORD), ("nFilterIndex", wintypes.DWORD),
                    ("lpstrFile", ctypes.c_void_p), ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", ctypes.c_void_p),
                    ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", ctypes.c_void_p),
                    ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD), ("nFileOffset", wintypes.WORD),
                    ("nFileExtension", wintypes.WORD), ("lpstrDefExt", ctypes.c_void_p), ("lCustData", wintypes.LPARAM),
                    ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", ctypes.c_void_p), ("pvReserved", ctypes.c_void_p),
                    ("dwReserved", wintypes.DWORD), ("FlagsEx", wintypes.DWORD)]

    filter_buf = ctypes.create_unicode_buffer(filters)   # (the NULs between the parts are kept)
    file_buf = ctypes.create_unicode_buffer(1024)
    ofn = OPENFILENAMEW(lStructSize=ctypes.sizeof(OPENFILENAMEW), hwndOwner=owner or None,
                        lpstrFilter=ctypes.addressof(filter_buf), nFilterIndex=1,
                        lpstrFile=ctypes.addressof(file_buf), nMaxFile=len(file_buf), lpstrTitle="Pick a program file",
                        Flags=OFN_EXPLORER | OFN_NODEREFERENCELINKS | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST
                        | OFN_HIDEREADONLY | OFN_NOCHANGEDIR)
    comdlg32 = ctypes.WinDLL("comdlg32")
    comdlg32.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    if comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return file_buf.value
    if comdlg32.CommDlgExtendedError():
        raise OSError("the Open dialog failed")
    return None   # cancelled
