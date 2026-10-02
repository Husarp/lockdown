"""LockdownSetup.exe - installs, updates and uninstalls Lockdown (built by scripts/build.ps1; runs as admin).

Install / update:
  stop a running Lockdown (app + service; also the older scripts-based setup), copy the program into
  Program Files\\Lockdown, register the "Lockdown Enforcer" Windows service (starts at boot, restarted if it crashes)
  and the "Lockdown Watchdog" task (starts it again within a minute if it's stopped), Start menu + desktop shortcuts,
  an "Apps & features" entry, then start the service and the app.
  Your settings, blocks and history live in C:\\ProgramData\\Lockdown, not in the program folder - an update never
  touches them (and the database adds any new columns itself).
Uninstall (LockdownSetup.exe --uninstall, from "Apps & features"):
  the Anti-Bypass challenge first, then network settings, browser policies, firewall rules and hosts entries are
  undone, the service, tasks, shortcuts and program folder removed; your data only if you tick it.
In-app update (LockdownSetup.exe --update, started by Lockdown's "Download and install"):
  no questions - only the progress page; Lockdown is started again and the window closes by itself.

The window (Lockdown's dark look) goes welcome -> [same version? / Lockdown running? asked in the window] ->
progress (a bar driven by the real steps, "Show details" for the log) -> finish. Everything that touches Windows
runs on a worker thread (`work`), which only posts to a queue; the Tk thread drains it (`_drain`)."""
import math
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import winreg
import zipfile
from contextlib import closing
from pathlib import Path

from version import VERSION   # (src/version.py - the build adds src to the path)

APP = "Lockdown"
INSTALL_DIR = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / APP
DATA_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / APP
SERVICE = "LockdownEnforcer"
WATCHDOG = "Lockdown Watchdog"
OLD_TASK = "Lockdown Enforcer"                   # the scripts-based setup (running from source)
AGENT_WATCHDOG = "Lockdown Agent Watchdog"
UNINSTALL_KEY = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{APP}"
START_MENU = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs"
DESKTOP = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)   # (the name exists on Windows only)
WINDIR = Path(os.environ.get("SystemRoot", r"C:\Windows"))
SYSTEM32 = WINDIR / "System32"
# Windows tools by full path - the admin account's PATH may not include System32 (it didn't on one PC)
TOOLS = {"schtasks": SYSTEM32 / "schtasks.exe", "taskkill": SYSTEM32 / "taskkill.exe", "sc": SYSTEM32 / "sc.exe",
         "icacls": SYSTEM32 / "icacls.exe", "cmd": SYSTEM32 / "cmd.exe", "tasklist": SYSTEM32 / "tasklist.exe",
         "powershell": SYSTEM32 / r"WindowsPowerShell\v1.0\powershell.exe", "explorer.exe": WINDIR / "explorer.exe"}


def tool(name: str) -> str:
    return str(TOOLS.get(name, name))


def run(*args, check=False) -> subprocess.CompletedProcess:
    return subprocess.run([tool(args[0]), *args[1:]], capture_output=True, text=True, creationflags=NO_WINDOW,
                          check=check)


def payload() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "payload.zip"


def installed_version() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY) as key:
            return winreg.QueryValueEx(key, "DisplayVersion")[0]
    except OSError:
        return None


# ---------- steps ----------

def stop_everything(log):
    log("Stopping Lockdown...")
    # the per-user task that brings the app back would restart it (maybe an old copy) meanwhile; the app
    # re-creates it when it starts
    run("schtasks", "/Delete", "/F", "/TN", AGENT_WATCHDOG)
    run("taskkill", "/F", "/IM", "Lockdown.exe")
    # the scripts-based setup: pythonw running src\main.py / src\service.py
    run("powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_Process -Filter \"name = 'pythonw.exe'\" | Where-Object { $_.CommandLine -match "
        "'Lockdown.*(main|service)\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    run("schtasks", "/Change", "/TN", WATCHDOG, "/DISABLE")   # (it would start the service again meanwhile)
    run("sc", "stop", SERVICE)
    for _ in range(30):   # wait until it has stopped
        if "STOPPED" in run("sc", "query", SERVICE).stdout or "1060" in run("sc", "query", SERVICE).stdout:
            break
        time.sleep(0.5)
    run("taskkill", "/F", "/IM", "LockdownService.exe")
    run("schtasks", "/End", "/TN", OLD_TASK)
    run("schtasks", "/Delete", "/F", "/TN", OLD_TASK)


def copy_files(log, each=None):
    """each(fraction_done, name) after every file (the progress bar)."""
    log("Copying the program...")
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    internal = INSTALL_DIR / "_internal"
    if internal.exists():   # an older version's libraries: replace them all
        shutil.rmtree(internal, ignore_errors=True)
    with zipfile.ZipFile(payload()) as z:   # (extractall, one file at a time so the bar can follow)
        members = z.infolist()
        total = sum(m.file_size for m in members) or 1
        done = 0
        for member in members:
            z.extract(member, INSTALL_DIR)
            done += member.file_size
            if each:
                each(done / total, member.filename)
    uninstaller = INSTALL_DIR / "Uninstall Lockdown.exe"
    if Path(sys.executable).resolve() != uninstaller.resolve() and getattr(sys, "frozen", False):
        shutil.copy2(sys.executable, uninstaller)


def data_folder(log):
    log("Preparing the data folder (your settings are kept)...")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    run("icacls", str(DATA_DIR), "/grant", "*S-1-5-32-545:(OI)(CI)M")   # Users: the app (runs as you) writes it


def register_service(log):
    log("Registering the Lockdown Enforcer service...")
    exe = str(INSTALL_DIR / "LockdownService.exe")
    exists = run("sc", "query", SERVICE).returncode == 0
    run(exe, "--startup", "auto", "update" if exists else "install", check=False)
    run("sc", "failure", SERVICE, "reset=", "86400", "actions=", "restart/5000/restart/10000/restart/30000")
    run("sc", "description", SERVICE, "Enforces Lockdown's blocks. Lockdown starts it again if it's stopped.")
    # watchdog: every minute, start it if it was stopped (does nothing while it runs)
    run("schtasks", "/Create", "/F", "/RU", "SYSTEM", "/SC", "MINUTE", "/MO", "1", "/TN", WATCHDOG,
        "/TR", f'"{tool("sc")}" start {SERVICE}')


def shortcuts(log):
    log("Adding shortcuts...")
    target = INSTALL_DIR / "Lockdown.exe"
    for folder in (START_MENU, DESKTOP):
        link = folder / "Lockdown.lnk"
        run("powershell", "-NoProfile", "-Command",
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}'); $s.TargetPath = '{target}'; "
            f"$s.WorkingDirectory = '{INSTALL_DIR}'; $s.Description = 'Lockdown'; $s.Save()")


def uninstall_entry(log):
    size_kb = sum(f.stat().st_size for f in INSTALL_DIR.rglob("*") if f.is_file()) // 1024
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as key:
        for name, value in {"DisplayName": APP, "DisplayVersion": VERSION, "Publisher": APP,
                            "DisplayIcon": str(INSTALL_DIR / "Lockdown.exe"), "InstallLocation": str(INSTALL_DIR),
                            "UninstallString": f'"{INSTALL_DIR / "Uninstall Lockdown.exe"}" --uninstall'}.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in {"NoModify": 1, "NoRepair": 1, "EstimatedSize": size_kb}.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


def start(log):
    log("Starting the Lockdown service...")
    run("schtasks", "/Change", "/TN", WATCHDOG, "/ENABLE")
    run("sc", "start", SERVICE)


def launch_app():
    """Open the Lockdown window. It runs as you, not as admin, so let Explorer start it."""
    subprocess.Popen([tool("explorer.exe"), str(INSTALL_DIR / "Lockdown.exe")], creationflags=NO_WINDOW)


INSTALL_WEIGHTS = (15, 55, 2, 12, 8, 2, 6)   # each step's share of the bar (copying is most of the time)


def install(log, at=None):
    """at(step, fraction=0.0, name=None): which of the steps below is running (the bar); name = a file copied."""
    at = at or (lambda step, fraction=0.0, name=None: None)
    at(0)
    stop_everything(log)
    at(1)
    copy_files(log, lambda fraction, name: at(1, fraction, name))
    at(2)
    data_folder(log)
    at(3)
    register_service(log)
    at(4)
    shortcuts(log)
    at(5)
    uninstall_entry(log)
    at(6)
    start(log)
    at(7)
    log(f"Lockdown {VERSION} is installed. Blocking is active.")


def challenge_passed() -> bool:
    app = INSTALL_DIR / "Lockdown.exe"
    if not app.exists():
        return True
    return run(str(app), "--challenge", "Uninstall Lockdown").returncode == 0


UNINSTALL_WEIGHTS = (35, 25, 25, 10, 5)


def uninstall(log, delete_data: bool, at=None):
    at = at or (lambda step, fraction=0.0, name=None: None)
    service_exe = str(INSTALL_DIR / "LockdownService.exe")
    at(0)
    stop_everything(log)
    at(1)
    log("Undoing network settings, browser policies, firewall rules and hosts entries...")
    run(service_exe, "restore-dns")
    run(service_exe, "remove-policies")
    at(2)
    log("Removing the service and tasks...")
    run(service_exe, "remove")
    run("sc", "delete", SERVICE)
    for task in (WATCHDOG, AGENT_WATCHDOG):
        run("schtasks", "/Delete", "/F", "/TN", task)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP)
    except OSError:
        pass
    at(3)
    log("Removing the shortcuts and the Apps & features entry...")
    for link in (START_MENU / "Lockdown.lnk", DESKTOP / "Lockdown.lnk"):
        link.unlink(missing_ok=True)
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY)
    except OSError:
        pass
    at(4)
    if delete_data:
        log("Deleting your settings and history...")
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    at(5)
    log("Lockdown is uninstalled. The program folder is removed when you close this window.")


def remove_program_folder():
    """This uninstaller runs from the program folder: delete it a moment after we've quit."""
    subprocess.Popen(f'"{tool("cmd")}" /c "{SYSTEM32 / "PING.EXE"}" 127.0.0.1 -n 3 >nul & rmdir /s /q "{INSTALL_DIR}"',
                     creationflags=NO_WINDOW)


# ---------- around the steps: is it running, putting it back, the worker ----------

def lockdown_running() -> bool:
    """Is the Lockdown window / tray app or its service running? (Asked before an interactive install.)"""
    tasks = run("tasklist", "/FO", "CSV", "/NH").stdout.lower()
    if '"lockdown.exe"' in tasks or '"lockdownservice.exe"' in tasks:
        return True
    return "RUNNING" in run("sc", "query", SERVICE).stdout


RECOVERING = "Starting Lockdown again, so blocking stays on..."


def recover(log):
    """A step failed half-way: never leave Lockdown off. Start the service (whatever copy is in place) and its
    watchdog again, and the app, which also brings back its own watchdog task."""
    log(RECOVERING)
    for again in (lambda: run("schtasks", "/Change", "/TN", WATCHDOG, "/ENABLE"),
                  lambda: run("sc", "start", SERVICE),
                  lambda: (INSTALL_DIR / "Lockdown.exe").exists() and launch_app()):
        try:
            again()
        except Exception:
            pass


def work(mode: str, post, delete_data: bool = False):
    """The worker thread: installs / uninstalls and reports through post(kind, value) - never touches Tk.
    Kinds: "log" (a step), "file" (a file copied), "progress" ((value, end of this step), 0-1), then one of
    "done" ("installed" / "uninstalled" / "refused") or "failed" (the error)."""
    weights = UNINSTALL_WEIGHTS if mode == "uninstall" else INSTALL_WEIGHTS

    def at(step, fraction=0.0, name=None):
        post("progress", (overall(weights, step, fraction), overall(weights, step + 1)))
        if name:
            post("file", name)

    def log(text):
        post("log", text)

    touched = False   # (nothing was stopped yet: nothing to put back)
    try:
        if mode == "uninstall":
            log("Checking Anti-Bypass...")
            if not challenge_passed():
                log("Not uninstalled: the Anti-Bypass challenge wasn't passed.")
                post("done", "refused")
                return
            touched = True
            uninstall(log, delete_data, at)
            post("done", "uninstalled")
        else:
            touched = True
            install(log, at)
            if mode == "update":   # an in-app update: Lockdown closed itself for it, so bring it back
                log("Starting Lockdown again...")
                launch_app()
            post("done", "installed")
    except Exception as e:   # show it rather than vanish
        log(f"Something went wrong: {e}")
        if touched:
            recover(log)
        post("failed", str(e) or type(e).__name__)


# ---------- what the window shows (pure, so the tests need no window) ----------

def mode_of(argv) -> str:
    """"uninstall" (from Apps & features), "update" (started by Lockdown itself: no questions) or "install"."""
    args = {a.lower() for a in argv[1:]}
    return "uninstall" if "--uninstall" in args else "update" if "--update" in args else "install"


def next_page(page, mode: str, same: bool = False, running: bool = False) -> str:
    """The page after `page` (None = the first). An in-app update goes straight to the progress page: you already
    said Install in Lockdown, and it closes itself. Only an install you started asks "same version again?" and
    "Lockdown is running - close it?"."""
    if page is None:
        return "progress" if mode == "update" else "welcome"
    if page == "welcome" and same and mode == "install":
        return "same"
    if page in ("welcome", "same") and running and mode == "install":
        return "running"
    if page in ("welcome", "same", "running"):
        return "progress"
    return "finish"


def overall(weights, step: int, fraction: float = 0.0) -> float:
    """How far the bar is (0-1) at `fraction` of step `step` (0-based; len(weights) = all done)."""
    step = max(0, min(step, len(weights)))
    part = weights[step] * min(max(fraction, 0.0), 1.0) if step < len(weights) else 0
    return (sum(weights[:step]) + part) / sum(weights)


CREEP = 0.004   # a step with no progress of its own (waiting for the service to stop) still inches forward


def ease(shown: float, target: float, end: float) -> float:
    """The bar's next frame: glide up to `target`; while a step reports nothing, creep towards (never past) 90%
    of the way to the end of that step, so a long wait doesn't look frozen. Never goes back."""
    if shown < target - 0.0005:
        return min(target, shown + max((target - shown) * 0.22, 0.002))
    goal = target + (end - target) * 0.9
    return shown + (goal - shown) * CREEP if goal > shown else shown


def headings(mode: str, current) -> tuple[str, str]:
    """(title, the line under it) for the window's header."""
    if mode == "uninstall":
        return "Uninstall Lockdown", (f"Version {current}" if current else "")
    if current and current == VERSION and mode == "install":
        return f"Lockdown {VERSION} Setup", "This version is already installed"
    if current or mode == "update":
        return f"Update to Lockdown {VERSION}", (f"You have {current}" if current else "")
    return f"Lockdown {VERSION} Setup", "Blocks sites and apps, tracks your screen time"


# ---------- look: Lockdown's dark theme (src/gui/theme.py) ----------

BG, SURFACE, SURFACE2, BORDER, TRACK = "#101418", "#181D23", "#202730", "#2B333D", "#262D36"
TEXT, MUTED, CONSOLE = "#E9EDF2", "#8D9AA8", "#0B0E12"
ORANGE, SUCCESS, DANGER, WARNING = "#DB5126", "#27AE60", "#E05A44", "#E2A32B"
FONT_FILES = ("Inter-Regular.otf", "Inter-SemiBold.otf", "BarlowCondensed-Bold.ttf")


def asset(*parts) -> Path:
    base = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parents[1] / "assets"
    return base.joinpath(*parts)


def accent() -> str:
    """Your accent colour from Settings > Appearance (orange if none, or it can't be read)."""
    try:
        with closing(sqlite3.connect(f"file:{DATA_DIR / 'config.db'}?mode=ro", uri=True, timeout=1)) as con:
            row = con.execute("SELECT value FROM settings WHERE key = 'ui.accent'").fetchone()
        if row and re.fullmatch(r"#[0-9A-Fa-f]{6}", row[0] or ""):
            return row[0]
    except Exception:
        pass
    return ORANGE


def _rgb(colour: str):
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


def mix(a: str, b: str, t: float) -> str:
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(_rgb(a), _rgb(b)))


def rrect(x0, y0, x1, y1, r):
    """Signed distance to a rounded rectangle (< 0 inside)."""
    cx, cy, hx, hy = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 - r, (y1 - y0) / 2 - r

    def d(x, y):
        qx, qy = abs(x - cx) - hx, abs(y - cy) - hy
        return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
    return d


def circle(cx, cy, r):
    return lambda x, y: math.hypot(x - cx, y - cy) - r


def strokes(points, width):
    """A line through `points`, `width` thick, with round ends."""
    segs = list(zip(points, points[1:]))

    def d(x, y):
        best = 1e9
        for (ax, ay), (bx, by) in segs:
            vx, vy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / ((vx * vx + vy * vy) or 1)))
            best = min(best, math.hypot(x - ax - vx * t, y - ay - vy * t))
        return best - width / 2
    return d


_SUB = [(i + 0.5) / 4 - 0.5 for i in range(4)]


def raster(w: int, h: int, bg: str, layers, x0: int = 0) -> list[list[str]]:
    """A w x h picture as rows of "#rrggbb": each (colour, distance) layer painted over `bg` in order, with
    smooth edges (4 x 4 samples where an edge crosses a pixel). Tk's canvas draws curves without antialiasing,
    so everything round (buttons, the bar, the badges) is a picture made here. x0: start at that column (a slice)."""
    base = _rgb(bg)
    layers = [(_rgb(c), d) for c, d in layers]
    rows = []
    for y in range(h):
        row = []
        for x in range(x0, x0 + w):
            px, py = x + 0.5, y + 0.5
            r, g, b = base
            for (cr, cg, cb), d in layers:
                dist = d(px, py)
                if dist >= 0.71:
                    continue
                a = 1.0 if dist <= -0.71 else sum(d(px + sx, py + sy) < 0 for sx in _SUB for sy in _SUB) / 16
                r, g, b = r + (cr - r) * a, g + (cg - g) * a, b + (cb - b) * a
            row.append("#%02x%02x%02x" % (round(r), round(g), round(b)))
        rows.append(row)
    return rows


def bar_rows(w: int, h: int, filled: int, fill: str, track: str, bg: str) -> list[list[str]]:
    """The progress bar (a rounded track, `filled` pixels of it rounded in `fill`) - the same picture as raster(),
    but only the columns around the round ends are worked out; between them every row is the same colour.
    (Drawn up to 30 times a second while it moves.)"""
    fill, track = fill.lower(), track.lower()
    layers = [(track, rrect(0, 0, w, h, h / 2))] + ([(fill, rrect(0, 0, filled, h, h / 2))] if filled else [])
    k = math.ceil(h / 2) + 1
    edges = set(range(0, k)) | set(range(w - k, w)) | (set(range(filled - k, filled + k)) if filled else set())
    plain = {x: (fill if x < filled else track) for x in range(w) if x not in edges}
    rows = [[] for _ in range(h)]
    x = 0
    while x < w:
        start = x
        if x in plain:
            while x < w and x in plain and plain[x] == plain[start]:
                x += 1
            for row in rows:
                row += [plain[start]] * (x - start)
        else:
            while x < w and x not in plain:
                x += 1
            for row, part in zip(rows, raster(x - start, h, bg, layers, x0=start)):
                row += part
    return rows


def photo(rows) -> tk.PhotoImage:
    image = tk.PhotoImage(width=len(rows[0]), height=len(rows))
    image.put(" ".join("{" + " ".join(row) + "}" for row in rows))
    return image


# ---------- window ----------

class Button(tk.Label):
    """A rounded button: an antialiased picture with the text on it."""

    def __init__(self, ui, parent, text, command, primary=False, colour=None):
        fill = colour or (ui.accent if primary else SURFACE2)
        hover = mix(fill, "#000000", 0.16) if primary else BORDER
        font = ui.font("semi", 13)
        w, h = max(ui.px(92), font.measure(text) + ui.px(40)), ui.px(36)
        shape = rrect(0, 0, w, h, ui.px(8))
        self.images = {state: photo(raster(w, h, parent["bg"], [(c, shape)]))
                       for state, c in (("normal", fill), ("hover", hover), ("disabled", mix(fill, BG, 0.55)))}
        self.fg = "#FFFFFF" if primary else TEXT
        super().__init__(parent, image=self.images["normal"], text=text, compound="center", font=font, fg=self.fg,
                         bg=parent["bg"], bd=0, padx=0, pady=0, highlightthickness=0, cursor="hand2")
        self.command, self.enabled = command, True
        self.bind("<Enter>", lambda e: self.enabled and self.configure(image=self.images["hover"]))
        self.bind("<Leave>", lambda e: self.enabled and self.configure(image=self.images["normal"]))
        self.bind("<ButtonRelease-1>", lambda e: self.invoke())

    def invoke(self):
        if self.enabled:
            self.command()

    def enable(self, on: bool):
        self.enabled = on
        self.configure(image=self.images["normal" if on else "disabled"], fg=self.fg if on else mix(self.fg, BG, 0.5),
                       cursor="hand2" if on else "arrow")


class Check(tk.Frame):
    """A tick box in the app's style."""

    def __init__(self, ui, parent, text, variable):
        super().__init__(parent, bg=parent["bg"])
        n, bg = ui.px(18), parent["bg"]
        box = rrect(0, 0, n, n, ui.px(4))
        tick = strokes([(n * 0.26, n * 0.52), (n * 0.43, n * 0.68), (n * 0.74, n * 0.33)], ui.px(2.2))
        self.images = {True: photo(raster(n, n, bg, [(ui.accent, box), ("#FFFFFF", tick)])),
                       False: photo(raster(n, n, bg, [("#4A5563", box), (bg, rrect(ui.px(1.5), ui.px(1.5), n - ui.px(1.5),
                                                                                         n - ui.px(1.5), ui.px(3)))]))}
        self.variable = variable
        self.box = tk.Label(self, bg=bg, bd=0, cursor="hand2")
        self.box.pack(side="left")
        self.text = tk.Label(self, text=text, bg=bg, fg=TEXT, font=ui.font("body", 13), cursor="hand2")
        self.text.pack(side="left", padx=(ui.px(9), 0))
        for widget in (self, self.box, self.text):
            widget.bind("<ButtonRelease-1>", lambda e: self.toggle())
        self.show()

    def toggle(self):
        self.variable.set(not self.variable.get())
        self.show()

    def show(self):
        self.box.configure(image=self.images[bool(self.variable.get())])


class SetupWindow(tk.Tk):
    W, H = 600, 450   # (at 100% - everything is scaled by px())

    def __init__(self, mode: str):
        super().__init__()
        self.withdraw()
        self.mode = mode
        self.uninstalling = mode == "uninstall"
        self.s = max(1.0, float(self.tk.call("tk", "scaling")) * 72 / 96)   # Windows display scaling
        self.accent = accent()
        self.current = installed_version()
        self.same = mode == "install" and bool(self.current) and self.current == VERSION
        self.running = None          # Lockdown running? asked when you press Install
        self.events = queue.Queue()  # from the worker thread; read on the Tk thread only (_drain)
        self.page = None
        self.busy = False            # installing: the window can't be closed
        self.done = False
        self.outcome = None
        self.delete_data = tk.BooleanVar(value=False)
        self.run_app = tk.BooleanVar(value=True)
        self.shown, self.target, self.end = 0.0, 0.0, 0.0   # the bar: drawn / reported / end of this step
        self.bar_colour = self.accent
        self.bar_px = None
        self.details_open = False
        self.lines = []              # the log, kept so the details box can be (re)built at any time
        self._keep = []              # PhotoImages of the page shown (Tk drops an image nobody refers to)
        self._fonts = {}
        families = set(tkfont.families(self))
        inter = "Inter" in families
        self.families = {"body": ("Inter" if inter else "Segoe UI", "normal"),
                         "semi": ("Inter Semi Bold", "normal") if "Inter Semi Bold" in families else
                         ("Inter", "bold") if inter else ("Segoe UI Semibold", "normal"),
                         "display": ("Barlow Condensed", "bold") if "Barlow Condensed" in families else
                         ("Segoe UI Semibold", "normal"),
                         "mono": (next((f for f in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono")
                                        if f in families), "Courier"), "normal")}

        title, subtitle = headings(mode, self.current)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        w, h = self.px(self.W), self.px(self.H)
        self.geometry(f"{w}x{h}+{(self.winfo_screenwidth() - w) // 2}+{(self.winfo_screenheight() - h) // 3}")
        self._icon()

        header = tk.Frame(self, bg=SURFACE, height=self.px(84))
        header.pack(fill="x")
        header.pack_propagate(False)
        self.logo = self._logo()
        if self.logo:
            tk.Label(header, image=self.logo, bg=SURFACE, bd=0).pack(side="left", padx=(self.px(22), self.px(14)))
        words = tk.Frame(header, bg=SURFACE)
        words.pack(side="left", padx=(0 if self.logo else self.px(28), 0))
        tk.Label(words, text=title, font=self.font("display", 26), fg=TEXT, bg=SURFACE).pack(anchor="w")
        if subtitle:
            tk.Label(words, text=subtitle, font=self.font("body", 12), fg=MUTED, bg=SURFACE).pack(anchor="w")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        self.footer = tk.Frame(self, bg=BG, height=self.px(68))
        self.footer.pack(side="bottom", fill="x")
        self.footer.pack_propagate(False)
        tk.Frame(self, bg=BORDER, height=1).pack(side="bottom", fill="x")
        self.body = tk.Frame(self, bg=BG, padx=self.px(28), pady=self.px(22))
        self.body.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Return>", lambda e: self.primary and self.primary.invoke())
        self.bind("<Escape>", lambda e: self.secondary and self.secondary.invoke())
        self.primary = self.secondary = None
        self.show(next_page(None, mode))
        self.deiconify()
        self._dark_title_bar()
        self.after(30, self._drain)

    # ----- helpers -----

    def px(self, n: float) -> int:
        return int(round(n * self.s))

    def font(self, kind: str, size: int) -> tkfont.Font:
        key = (kind, size)
        if key not in self._fonts:
            family, weight = self.families[kind]
            self._fonts[key] = tkfont.Font(self, family=family, size=-self.px(size), weight=weight)
        return self._fonts[key]

    def keep(self, image):
        self._keep.append(image)
        return image

    def _icon(self):
        try:
            if sys.platform == "win32":
                self.iconbitmap(str(asset("lockdown.ico")))
            else:
                self.iconphoto(True, tk.PhotoImage(file=str(asset("setup", "logo-48.png"))))
        except (tk.TclError, OSError):
            pass

    def _logo(self):
        sizes = (48, 60, 72, 96)
        best = min(sizes, key=lambda n: abs(n - 48 * self.s))
        try:
            return tk.PhotoImage(file=str(asset("setup", f"logo-{best}.png")))
        except (tk.TclError, OSError):
            return None

    def _dark_title_bar(self):
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            on = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
        except Exception:
            pass

    def label(self, parent, text, kind="body", size=13, fg=TEXT, **kw):
        return tk.Label(parent, text=text, font=self.font(kind, size), fg=fg, bg=parent["bg"], justify="left",
                        anchor="w", **kw)

    def badge(self, parent, colour, kind):
        """A round tinted icon: a tick (done), or "!" (look here)."""
        n = self.px(46)
        layers = [(mix(colour, parent["bg"], 0.82), circle(n / 2, n / 2, n / 2))]
        if kind == "tick":
            layers.append((colour, strokes([(n * .31, n * .52), (n * .45, n * .65), (n * .70, n * .37)], self.px(3.4))))
        else:
            layers += [(colour, strokes([(n / 2, n * .29), (n / 2, n * .55)], self.px(3.6))),
                       (colour, circle(n / 2, n * .70, self.px(2.3)))]
        return tk.Label(parent, image=self.keep(photo(raster(n, n, parent["bg"], layers))), bg=parent["bg"], bd=0)

    def chip(self, parent, text, fill, fg):
        font = self.font("semi", 13)
        w, h = font.measure(text) + self.px(22), self.px(28)
        image = self.keep(photo(raster(w, h, parent["bg"], [(fill, rrect(0, 0, w, h, h / 2))])))
        return tk.Label(parent, image=image, text=text, compound="center", font=font, fg=fg, bg=parent["bg"], bd=0)

    def buttons(self, *specs):
        """Footer buttons, right to left: (text, command, primary). Enter = the primary one, Esc = the other."""
        for child in self.footer.winfo_children():
            child.destroy()
        self.primary = self.secondary = None
        for text, command, primary in specs:
            button = Button(self, self.footer, text, command, primary=primary,
                            colour=DANGER if primary and self.uninstalling and self.page == "welcome" else None)
            button.pack(side="right", padx=(0, self.px(10) if self.primary or self.secondary else self.px(22)))
            if primary:
                self.primary = button
            else:
                self.secondary = button
        return self.primary

    # ----- pages -----

    def show(self, page: str):
        self.page = page
        for child in self.body.winfo_children():
            child.destroy()
        self._keep = []
        getattr(self, "_page_" + page)()

    def _next(self):
        """Welcome / "same version?" answered: is Lockdown running? (asked off the Tk thread) - then on."""
        if self.mode == "install" and self.running is None and \
                next_page(self.page, self.mode, self.same, True) == "running":
            if self.primary:
                self.primary.enable(False)
            threading.Thread(target=lambda: self.events.put(("running", self._is_running())), daemon=True).start()
            return
        self.show(next_page(self.page, self.mode, self.same, bool(self.running)))

    @staticmethod
    def _is_running() -> bool:
        try:
            return lockdown_running()
        except Exception:
            return False

    def _page_welcome(self):
        b = self.body
        if self.uninstalling:
            head = "Remove Lockdown from this PC"
            points = ["Blocking stops. Network settings and browser policies go back to how they were.",
                      "The service, its tasks, the shortcuts and the program folder are removed.",
                      "If Anti-Bypass is on, you'll be asked for the challenge first."]
        elif self.same:
            head = f"Lockdown {VERSION} is already installed"
            points = ["Installing it again is safe, and repairs a broken install.",
                      "Your settings, blocks and history are kept."]
        elif self.current:
            head = "Ready to update"
            points = ["Lockdown is closed and its service stopped for a moment.",
                      f"The new version replaces the program in {INSTALL_DIR}.",
                      "Your settings, blocks and history are kept.",
                      "Lockdown starts again when it's done."]
        else:
            head = "Ready to install"
            points = [f"Installs Lockdown in {INSTALL_DIR}.",
                      "Adds the Lockdown Enforcer service, which starts with Windows and keeps blocking on.",
                      "Adds Lockdown to the Start menu and the desktop."]
        self.label(b, head, "semi", 17).pack(anchor="w")
        if self.current and not self.same and not self.uninstalling:
            row = tk.Frame(b, bg=BG)
            row.pack(anchor="w", pady=(self.px(12), 0))
            self.chip(row, self.current, SURFACE2, MUTED).pack(side="left")
            self.label(row, "→", "semi", 15, MUTED).pack(side="left", padx=self.px(8))
            self.chip(row, VERSION, mix(self.accent, BG, 0.78), "#FFFFFF").pack(side="left")
        listing = tk.Frame(b, bg=BG)
        listing.pack(anchor="w", fill="x", pady=(self.px(14), 0))
        dot = self.keep(photo(raster(self.px(6), self.px(6), BG, [(self.accent, circle(self.px(3), self.px(3), self.px(3)))])))
        for point in points:
            row = tk.Frame(listing, bg=BG)
            row.pack(anchor="w", fill="x", pady=self.px(3))
            tk.Label(row, image=dot, bg=BG, bd=0).pack(side="left", anchor="n", pady=(self.px(7), 0))
            self.label(row, point, fg=MUTED, wraplength=self.px(520)).pack(side="left", padx=(self.px(11), 0))
        if self.uninstalling:
            Check(self, b, "Also delete my settings, blocks and history", self.delete_data).pack(
                anchor="w", pady=(self.px(16), 0))
        go = "Uninstall" if self.uninstalling else "Reinstall" if self.same else "Update" if self.current else "Install"
        self.buttons((go, self._next, True), ("Cancel", self._close, False))

    def _prompt(self, colour, title, text, ok, cancel):
        """A question asked inside the window, in its style (not a grey Windows message box)."""
        card = tk.Frame(self.body, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER,
                        padx=self.px(20), pady=self.px(20))
        card.pack(fill="x", pady=(self.px(18), 0))
        self.badge(card, colour, "!").pack(side="left", anchor="n")
        words = tk.Frame(card, bg=SURFACE)
        words.pack(side="left", fill="x", expand=True, padx=(self.px(16), 0))
        self.label(words, title, "semi", 16).pack(anchor="w")
        self.label(words, text, fg=MUTED, wraplength=self.px(420)).pack(anchor="w", pady=(self.px(6), 0))
        self.buttons((ok, self._next, True), (cancel, self._close, False))

    def _page_same(self):
        self._prompt(self.accent, f"Install {VERSION} again?",
                     f"Lockdown {VERSION} is already installed. This is usually the setup file opened twice - "
                     "installing it again is still safe.", "Reinstall", "Cancel")

    def _page_running(self):
        verb = "update" if self.current else "install"
        self._prompt(WARNING, "Lockdown is running",
                     f"It will be closed to {verb} it, then started again. Blocking pauses for a moment "
                     "while the service restarts.", "OK", "Cancel")

    def _page_progress(self):
        b = self.body
        verb = "Uninstalling" if self.uninstalling else \
            "Updating" if self.current or self.mode == "update" else "Installing"
        self.p_head = self.label(b, f"{verb} Lockdown", "semi", 17)
        self.p_head.pack(anchor="w")
        row = tk.Frame(b, bg=BG)
        row.pack(fill="x", pady=(self.px(16), self.px(8)))
        self.p_status = self.label(row, "Starting...", fg=TEXT)
        self.p_status.pack(side="left")
        self.p_pct = self.label(row, "0%", "semi", 13, MUTED)
        self.p_pct.pack(side="right")
        self.bar_w, self.bar_h = self.px(self.W - 56), self.px(10)
        self.p_bar = tk.Label(b, bg=BG, bd=0)
        self.p_bar.pack(anchor="w")
        self.p_file = self.label(b, " ", size=12, fg=MUTED, wraplength=self.px(self.W - 56))
        self.p_file.pack(fill="x", pady=(self.px(8), 0))
        self.p_note = self.label(b, "", size=12, fg=SUCCESS)
        self.p_toggle = self.label(b, "", "semi", 12, MUTED, cursor="hand2")
        self.p_toggle.pack(anchor="w", pady=(self.px(10), 0))
        self.p_toggle.bind("<ButtonRelease-1>", lambda e: self._details(not self.details_open))
        self.p_toggle.bind("<Enter>", lambda e: self.p_toggle.configure(fg=TEXT))
        self.p_toggle.bind("<Leave>", lambda e: self.p_toggle.configure(fg=MUTED))
        self.p_details = tk.Text(b, height=6, bg=CONSOLE, fg=MUTED, font=self.font("mono", 12), relief="flat",
                                 bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER,
                                 padx=self.px(10), pady=self.px(8), wrap="word", insertbackground=CONSOLE,
                                 selectbackground=BORDER)
        self.p_details.insert("end", "\n".join(self.lines))
        self.p_details.configure(state="disabled")
        self._details(self.details_open)
        self._draw_bar(force=True)
        for child in self.footer.winfo_children():
            child.destroy()
        self.primary = self.secondary = None
        note = "This takes a minute or so." if self.uninstalling or not (self.current or self.mode == "update") \
            else "This takes a minute or so. Lockdown starts again by itself when it's done."
        self.p_footnote = self.label(self.footer, note, size=12, fg=MUTED)
        self.p_footnote.pack(side="left", padx=self.px(28))
        self.busy = True
        # not a daemon: if the window goes away mid-install (a Tk error, Windows closing it), the process still
        # waits for the install to finish - and so for Lockdown to be started again - instead of dying half-way
        threading.Thread(target=work, args=(self.mode, self._post, self.delete_data.get())).start()

    def _post(self, kind, value):   # (worker thread: only the queue)
        self.events.put((kind, value))

    def _details(self, show: bool):
        self.details_open = show
        self.p_toggle.configure(text=("▾  Hide details" if show else "▸  Show details"))
        if show:
            self.p_details.pack(fill="x", pady=(self.px(8), 0))   # (its own height: whole lines, none cut off)
            self.p_details.see("end")
        else:
            self.p_details.pack_forget()

    def _page_finish(self):
        b = self.body
        if self.outcome == "refused":
            colour, title = WARNING, "Lockdown was not uninstalled"
            text = "The Anti-Bypass challenge wasn't passed. Lockdown is still installed and blocking."
        elif self.outcome == "uninstalled":
            colour, title = SUCCESS, "Lockdown is uninstalled"
            text = ("Blocking has stopped and your network settings are back to how they were. The program folder "
                    "is removed when you close this window.")
        else:
            colour, title = SUCCESS, f"Lockdown {VERSION} is installed"
            text = "Blocking is active, and Lockdown starts with Windows. Your settings, blocks and history are kept."
        self.badge(b, colour, "tick" if colour == SUCCESS else "!").pack(anchor="w", pady=(self.px(6), 0))
        self.label(b, title, "semi", 19).pack(anchor="w", pady=(self.px(14), 0))
        self.label(b, text, fg=MUTED, wraplength=self.px(540)).pack(anchor="w", pady=(self.px(6), 0))
        if self.outcome == "installed":
            Check(self, b, "Run Lockdown now", self.run_app).pack(anchor="w", pady=(self.px(18), 0))
            self.buttons(("Finish", self._close, True))
        else:
            self.buttons(("Close", self._close, True))

    # ----- the worker's news (Tk thread) -----

    def _drain(self):
        try:
            self._take_news()
        finally:   # (an error here must not stop the window from following the install)
            self.after(30, self._drain)

    def _take_news(self):
        news, new_lines = [], []
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.target, self.end = max(self.target, value[0]), max(self.end, value[1])
                elif kind == "log":
                    new_lines.append(value)
                    if self.page == "progress":
                        self.p_status.configure(text=value)
                        self.p_file.configure(text=" ")
                elif kind == "file":
                    new_lines.append("  " + value)
                    if self.page == "progress":
                        self.p_file.configure(text=value if len(value) < 72 else "..." + value[-69:])
                else:
                    news.append((kind, value))
        except queue.Empty:
            pass
        if new_lines:
            self.lines += new_lines
            if self.page == "progress":
                self.p_details.configure(state="normal")
                self.p_details.insert("end", ("\n" if self.p_details.index("end-1c") != "1.0" else "")
                                      + "\n".join(new_lines))
                self.p_details.see("end")
                self.p_details.configure(state="disabled")
        for kind, value in news:
            getattr(self, "_on_" + kind)(value)
        if self.page == "progress":
            self.shown = ease(self.shown, self.target, self.end)
            self._draw_bar()

    def _draw_bar(self, force=False):
        filled = 0 if self.shown <= 0 else max(self.bar_h, round(self.bar_w * self.shown))
        self.p_pct.configure(text=f"{int(self.shown * 100 + 1e-6)}%")
        if filled == self.bar_px and not force:
            return
        self.bar_px = filled
        self.bar_image = photo(bar_rows(self.bar_w, self.bar_h, filled, self.bar_colour, TRACK, BG))
        self.p_bar.configure(image=self.bar_image)

    def _on_running(self, running: bool):
        self.running = running
        self._next()

    def _on_done(self, outcome: str):
        self.busy, self.done, self.outcome = False, outcome != "refused", outcome
        self.target = self.end = 1.0 if outcome != "refused" else self.target
        if self.mode == "update":   # (work() has started Lockdown again)
            self.shown = 1.0
            self._draw_bar()
            self.p_head.configure(text=f"Lockdown {VERSION} is installed")
            self.p_status.configure(text="Lockdown is starting again. This window closes by itself.")
            self.p_file.configure(text=" ")
            self.p_footnote.configure(text="")
            self.after(1500, self._close)
        else:
            self.after(450, lambda: self.show("finish"))

    def _on_failed(self, error: str):
        """Stays on screen, with the details open, until you close it."""
        self.busy = False
        self.target = self.end = self.shown
        self.bar_colour = DANGER
        self._draw_bar(force=True)
        what = "uninstall" if self.uninstalling else "update" if self.current or self.mode == "update" else "install"
        self.p_head.configure(text=f"The {what} didn't finish")
        self.p_status.configure(text="Something went wrong", fg=DANGER)
        self.p_file.configure(text=error, fg=TEXT)
        self.p_footnote.configure(text="")
        if RECOVERING in self.lines:
            self.p_note.configure(text="Lockdown was started again, so blocking stays on.")
            self.p_note.pack(fill="x", pady=(self.px(4), 0), before=self.p_toggle)
        self.p_details.configure(height=3)   # (the error above takes the room of the other lines)
        self._details(True)
        self.buttons(("Close", self._close, True))

    def _close(self):
        if self.busy:   # (don't quit halfway)
            return
        self.destroy()
        if self.uninstalling and self.done:
            remove_program_folder()
        elif self.mode == "install" and self.done and self.run_app.get():
            launch_app()


def dpi_aware():
    """Sharp text at 125 / 150 %: say so before the window exists (sizes then follow via SetupWindow.px)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def load_fonts():
    """The app's fonts (Inter, Barlow Condensed), for this process only; Segoe UI if they can't be loaded."""
    if sys.platform == "win32":
        import ctypes
        for name in FONT_FILES:
            try:
                ctypes.windll.gdi32.AddFontResourceExW(str(asset("fonts", name)), 0x10, 0)   # FR_PRIVATE
            except Exception:
                pass


def main():
    dpi_aware()
    load_fonts()
    SetupWindow(mode_of(sys.argv)).mainloop()


if __name__ == "__main__":
    main()
