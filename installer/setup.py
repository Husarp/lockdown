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
  undone, the service, tasks, shortcuts and program folder removed; your data only if you tick it."""
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import winreg
import zipfile
from pathlib import Path
from tkinter import ttk

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
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def run(*args, check=False) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, creationflags=NO_WINDOW, check=check)


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


def copy_files(log):
    log("Copying the program...")
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    internal = INSTALL_DIR / "_internal"
    if internal.exists():   # an older version's libraries: replace them all
        shutil.rmtree(internal, ignore_errors=True)
    with zipfile.ZipFile(payload()) as z:
        z.extractall(INSTALL_DIR)
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
        "/TR", f"sc start {SERVICE}")


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
    log("Starting Lockdown...")
    run("schtasks", "/Change", "/TN", WATCHDOG, "/ENABLE")
    run("sc", "start", SERVICE)
    # the app runs as you, not as admin: let Explorer start it
    subprocess.Popen(["explorer.exe", str(INSTALL_DIR / "Lockdown.exe")], creationflags=NO_WINDOW)


def install(log):
    stop_everything(log)
    copy_files(log)
    data_folder(log)
    register_service(log)
    shortcuts(log)
    uninstall_entry(log)
    start(log)
    log(f"Lockdown {VERSION} is installed and running.")


def challenge_passed() -> bool:
    app = INSTALL_DIR / "Lockdown.exe"
    if not app.exists():
        return True
    return run(str(app), "--challenge", "Uninstall Lockdown").returncode == 0


def uninstall(log, delete_data: bool):
    service_exe = str(INSTALL_DIR / "LockdownService.exe")
    stop_everything(log)
    log("Undoing network settings, browser policies, firewall rules and hosts entries...")
    run(service_exe, "restore-dns")
    run(service_exe, "remove-policies")
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
    for link in (START_MENU / "Lockdown.lnk", DESKTOP / "Lockdown.lnk"):
        link.unlink(missing_ok=True)
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY)
    except OSError:
        pass
    if delete_data:
        log("Deleting your settings and history...")
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    log("Lockdown is uninstalled. The program folder is removed when you close this window.")


def remove_program_folder():
    """This uninstaller runs from the program folder: delete it a moment after we've quit."""
    subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{INSTALL_DIR}"', creationflags=NO_WINDOW)


# ---------- window ----------

class SetupWindow(tk.Tk):
    def __init__(self, uninstalling: bool):
        super().__init__()
        self.uninstalling = uninstalling
        self.title("Uninstall Lockdown" if uninstalling else "Lockdown Setup")
        self.geometry("520x360")
        self.resizable(False, False)
        icon = Path(getattr(sys, "_MEIPASS", ".")) / "lockdown.ico"
        if icon.exists():
            self.iconbitmap(str(icon))
        box = ttk.Frame(self, padding=18)
        box.pack(fill="both", expand=True)
        current = installed_version()
        if uninstalling:
            intro = ("This removes Lockdown: blocking stops, network settings and browser policies go back to how "
                     "they were. (If Anti-Bypass is on, you'll be asked for the challenge first.)")
        elif current:
            intro = (f"Lockdown {current} is installed. This updates it to {VERSION}.\n"
                     "Your settings, blocks and history are kept.")
        else:
            intro = (f"This installs Lockdown {VERSION} in {INSTALL_DIR}.\nIt blocks sites and apps, tracks screen "
                     "time and starts with Windows.")
        ttk.Label(box, text=intro, wraplength=470, justify="left").pack(anchor="w")
        self.delete_data = tk.BooleanVar(value=False)
        if uninstalling:
            ttk.Checkbutton(box, text="Also delete my settings, blocks and history", variable=self.delete_data).pack(
                anchor="w", pady=(10, 0))
        self.log_box = tk.Text(box, height=9, width=60, state="disabled", relief="flat", background="#F2F2F2",
                               font=("Segoe UI", 9))
        self.log_box.pack(fill="both", expand=True, pady=12)
        buttons = ttk.Frame(box)
        buttons.pack(fill="x")
        self.go = ttk.Button(buttons, text="Uninstall" if uninstalling else ("Update" if current else "Install"),
                             command=self._start)
        self.go.pack(side="right")
        self.close = ttk.Button(buttons, text="Cancel", command=self._close)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.done = False
        self.close.pack(side="right", padx=8)

    def log(self, text: str):
        self.after(0, self._append, text)

    def _append(self, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _start(self):
        self.go.configure(state="disabled")
        self.close.configure(state="disabled")
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            if self.uninstalling:
                self.log("Checking Anti-Bypass...")
                if not challenge_passed():
                    self.log("Not uninstalled: the Anti-Bypass challenge wasn't passed.")
                else:
                    uninstall(self.log, self.delete_data.get())
                    self.done = True
            else:
                install(self.log)
        except Exception as e:   # show it rather than vanish
            self.log(f"Something went wrong: {e}")
        self.after(0, lambda: self.close.configure(state="normal", text="Close"))

    def _close(self):
        if str(self.close.cget("state")) == "disabled":   # (busy: don't quit halfway)
            return
        self.destroy()
        if self.uninstalling and self.done:
            remove_program_folder()


def main():
    SetupWindow("--uninstall" in sys.argv).mainloop()


if __name__ == "__main__":
    main()
