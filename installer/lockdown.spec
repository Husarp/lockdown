# PyInstaller spec: Lockdown.exe (GUI + tray agent) and LockdownService.exe (the Windows service) in one folder,
# dist\Lockdown. Built by scripts\build.ps1.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(ROOT, "assets", "lockdown.ico")

gui = Analysis([os.path.join(SRC, "main.py")], pathex=[SRC],
               datas=[(os.path.join(ROOT, "assets"), "assets")],
               hiddenimports=["gui.antibypass_page", "gui.display_settings", "comtypes.stream"],
               excludes=["pytest"])
svc = Analysis([os.path.join(SRC, "service_win.py")], pathex=[SRC],
               hiddenimports=["win32timezone"], excludes=["pytest", "customtkinter", "PIL", "pystray", "uiautomation"])

gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="Lockdown", icon=ICON, console=False)
svc_exe = EXE(PYZ(svc.pure), svc.scripts, [], exclude_binaries=True, name="LockdownService", icon=ICON,
              console=False)
COLLECT(gui_exe, gui.binaries, gui.datas, svc_exe, svc.binaries, svc.datas, name="Lockdown")
