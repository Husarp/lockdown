"""Popup for picking an app to block: Start Menu apps, Steam games + apps with an open window, with icons and search.
Rows are made once and reused (rebuilding hundreds of buttons on every key made searching slow); the search waits
until you pause typing and shows the first MAX_SHOWN matches. Design 3l: search + "Pick a program file...", a count
line with an All / Running / Games filter, the list on its own bordered surface (icon · name · exe · RUNNING), Cancel
at the foot."""
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk

import search
from blocker.apps import PROGRAM_TYPES, PROTECTED, interpreter
from gui import icons, theme
from gui.components import Rows, Segmented, hairline

MUTED = theme.MUTED
MAX_SHOWN = 60
SEARCH_DELAY_MS = 150
FILTERS = {"All": lambda a: True, "Running": lambda a: a["running"], "Games": lambda a: bool(a.get("steam"))}
_cache: list[dict] | None = None   # the app list takes a few seconds; keep it for the session
_loading = threading.Lock()


def preload():
    """Start loading the app list in the background (so the popup opens fast later)."""
    threading.Thread(target=_load, daemon=True).start()


def _load() -> list[dict]:
    global _cache
    with _loading:
        if _cache is None:
            from monitor.applist import list_apps
            try:
                apps = list_apps()
            except Exception:
                apps = []
            icons.cache_app_icons(apps)   # (first search of a broad word would otherwise extract dozens at once)
            _cache = apps
        return _cache


def matches(apps: list[dict], text: str) -> list[dict]:
    """Apps matching `text` (small typos are fine), best first; then running ones, then by name. Steam games also
    match "steam" - listed after Steam itself."""
    return search.rank(apps, text, lambda a: f"{a['name']} {a['exe']}" + (" steam" if a.get("steam") else ""),
                       tie=lambda a: (bool(a.get("steam")), not a["running"], a["name"].lower()))


class AppBrowser(ctk.CTkToplevel):
    """Every installed app and Steam game, searchable. on_pick(app) picks one (the Add tab uses that); with
    on_pick None it is just a browser - right-click an app to set its category, block it or add it to a group.
    `app_window` is the LockdownApp, needed for the right-click menu."""

    def __init__(self, master, on_pick=None, app_window=None):
        super().__init__(master)
        self.on_pick = on_pick
        self.app_window = app_window or master.winfo_toplevel()
        self.title("Browse apps" if on_pick else "Apps")
        self.geometry("560x600")
        self.configure(fg_color=theme.BG)
        self.transient(master.winfo_toplevel())
        self.after(50, self.grab_set)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=13, pady=(13, 10))
        self.search = ctk.CTkEntry(top, placeholder_text="Search apps and Steam games...")
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind("<KeyRelease>", lambda e: self._search_soon())
        ctk.CTkButton(top, text="Pick a program file...", width=150, **theme.OUTLINE, command=self._browse_file).pack(
            side="left", padx=(9, 0))
        line = ctk.CTkFrame(self, fg_color="transparent")
        line.pack(fill="x", padx=13, pady=(0, 10))
        self.status = ctk.CTkLabel(line, text="Loading apps...", text_color=MUTED, font=theme.body(12), height=18)
        self.status.pack(side="left")
        self.filter = Segmented(line, values=list(FILTERS), command=lambda v: self._render(), height=26)
        self.filter.pack(side="right")
        self.filter.set("All")
        surface = ctk.CTkFrame(self, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER, corner_radius=3)
        surface.pack(fill="both", expand=True, padx=13)
        self.body = ctk.CTkScrollableFrame(surface, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=1, pady=1)
        self.rows = Rows(self.body, self._make_row, "No apps found.", {"fill": "x"})
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=13, pady=(10, 13))
        ctk.CTkButton(foot, text="Close" if not self.on_pick else "Cancel", width=90, **theme.OUTLINE,
                      command=self._cancel).pack(side="right")
        ctk.CTkLabel(foot, text=("Click an app to pick it · right-click for more" if self.on_pick else
                                 "Right-click an app to set its category, block it or add it to a group"),
                     text_color=MUTED, font=theme.body(11)).pack(side="left")
        self.apps: list[dict] | None = None
        self._pending = None
        preload()
        self._wait_for_list()

    def _make_row(self, parent):
        """icon · name · exe (muted) · RUNNING at the right; a hairline under each row; hover tint."""
        row = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        line = ctk.CTkFrame(row, fg_color="transparent", corner_radius=0, cursor="hand2")
        line.pack(fill="x", padx=4, pady=(0, 0))
        row.line = line
        row.icon = ctk.CTkLabel(line, text="", width=18, height=30)
        row.icon.pack(side="left", padx=(8, 10))
        row.name = ctk.CTkLabel(line, text="", font=theme.semi(13), anchor="w")
        row.name.pack(side="left")
        row.exe = ctk.CTkLabel(line, text="", text_color=MUTED, font=theme.body(12), anchor="w")
        row.exe.pack(side="left", padx=(8, 0))
        row.running = ctk.CTkLabel(line, text="RUNNING", text_color=theme.SUCCESS, font=theme.semi(10), width=60,
                                   anchor="e")
        row.running.pack(side="right", padx=(0, 10))
        hairline(row).pack(fill="x")
        for w in (line, row.icon, row.name, row.exe, row.running):
            w.bind("<Enter>", lambda e, r=row: r.line.configure(fg_color=theme.SURFACE2))
            w.bind("<Leave>", lambda e, r=row: r.line.configure(fg_color="transparent"))
        return row

    def _menu(self, a: dict):
        from gui import app_actions
        app_actions.open_menu(self, self.app_window, "app", a["exe"], a["name"], a.get("path"), self._render)

    def _wait_for_list(self):
        """Poll from the Tk thread (Tk must not be called from the loader thread)."""
        if not self.winfo_exists():
            return
        if _cache is None:
            self.after(200, self._wait_for_list)
            return
        self.apps = _cache
        self._render()

    def _search_soon(self):
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(SEARCH_DELAY_MS, self._render)

    def _render(self):
        self._pending = None
        if self.apps is None:
            return
        keep = FILTERS[self.filter.value or "All"]
        found = [a for a in matches(self.apps, self.search.get().strip()) if keep(a)]
        shown = found[:MAX_SHOWN]
        for row, a in zip(self.rows.take(len(shown)), shown):
            row.icon.configure(image=icons.get_app(a["exe"], a["path"], 18))
            row.name.configure(text=a["name"])
            row.exe.configure(text=a["exe"] + (" · Steam" if a.get("steam") else ""))
            row.running.configure(text="RUNNING" if a["running"] else "")
            for w in (row.line, row.icon, row.name, row.exe, row.running):
                w.bind("<Button-1>", lambda e, a=a: self._pick(a))
                w.bind("<Button-3>", lambda e, a=a: self._menu(a))
        more = len(found) - len(shown)
        count = f"{len(found)} app{'s' * (len(found) != 1)}"
        self.status.configure(text=count + (f" · first {len(shown)} shown - type to narrow down" if more else ""),
                              text_color=MUTED)
        self.body._parent_canvas.yview_moveto(0)   # back to the top (a shorter list keeps no stale scroll position)

    def _browse_file(self):
        """Any program file: an .exe, a Java game (.jar), a script, or a shortcut to one - a shortcut stands for
        what it starts (applist.program_file), and is named after the shortcut."""
        from monitor import applist, win
        picked = ask_program_file(self)
        if not picked:
            return
        try:
            path = applist.program_file(picked)
        except ValueError as e:
            self.status.configure(text=str(e), text_color=theme.DANGER)
            return
        exe = win.exe_name(path)
        if interpreter(exe) and not self._whole_interpreter(exe):
            return
        name = picked.rsplit("\\", 1)[-1].rsplit(".", 1)[0]
        self._pick({"name": name, "exe": exe, "path": path})

    def _whole_interpreter(self, exe: str) -> bool:
        """javaw.exe / python.exe / cmd.exe ... picked itself (or a shortcut with no file in it): that blocks every
        program it runs - asked first. Lockdown's own (python.exe) can't be blocked at all."""
        hint = "To block one game or script, pick its own file (.jar, .py, .bat ...) or its shortcut."
        if exe in PROTECTED:
            self.status.configure(text=f"{exe} can't be blocked (Lockdown needs it). {hint}", text_color=theme.DANGER)
            return False
        return messagebox.askyesno("Block every program it runs?", f"{exe} runs other programs too - blocking it "
                                   f"blocks every one of them.\n\n{hint}\n\nBlock all of {exe}?", parent=self)

    def _cancel(self):
        self.grab_release()
        self.destroy()

    def _pick(self, app: dict):
        if self.on_pick is None:   # browsing, not picking: the right-click menu is what acts on a row
            return
        self.grab_release()
        self.destroy()
        self.on_pick(app)


OFN_NOCHANGEDIR, OFN_HIDEREADONLY, OFN_PATHMUSTEXIST, OFN_FILEMUSTEXIST = 0x8, 0x4, 0x800, 0x1000
OFN_EXPLORER, OFN_NODEREFERENCELINKS = 0x80000, 0x100000


def ask_program_file(parent) -> str | None:
    """A program file picked in Windows' Open dialog (None: cancelled). A shortcut stays the shortcut
    (OFN_NODEREFERENCELINKS) - the dialog would otherwise hand back only its target, and a Java game's shortcut
    would come back as javaw.exe without the game it runs. Tk's own dialog if that one can't be shown."""
    patterns = ";".join("*" + e for e in (*PROGRAM_TYPES, ".lnk"))
    try:
        return _open_dialog(parent, f"Programs, Java games, scripts, shortcuts\0{patterns}\0All files\0*.*\0\0")
    except Exception:
        path = filedialog.askopenfilename(parent=parent, title="Pick a program file",
                                          filetypes=[("Programs, Java games, scripts, shortcuts", patterns.replace(";", " ")),
                                                     ("All files", "*.*")])
        return path.replace("/", "\\") if path else None


def _open_dialog(parent, filters: str) -> str | None:
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
    ofn = OPENFILENAMEW(lStructSize=ctypes.sizeof(OPENFILENAMEW), hwndOwner=int(parent.wm_frame(), 16),
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
