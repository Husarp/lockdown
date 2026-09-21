# PyInstaller spec: Lockdown.exe (GUI + tray agent) and LockdownService.exe (the Windows service) in one folder,
# dist\Lockdown. Built by scripts\build.ps1.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(ROOT, "assets", "lockdown.ico")

# the version resource, so the .exe files show their version in Explorer (Details tab / tooltip)
sys.path.insert(0, SRC)
sys.path.insert(0, SPECPATH)
from version import VERSION
import version_res
RES = os.path.join(ROOT, "build")
os.makedirs(RES, exist_ok=True)
GUI_RES = version_res.write(os.path.join(RES, "version_Lockdown.txt"), VERSION, "Lockdown", "Lockdown")
SVC_RES = version_res.write(os.path.join(RES, "version_LockdownService.txt"), VERSION, "LockdownService",
                            "Lockdown enforcement service")

gui = Analysis([os.path.join(SRC, "main.py")], pathex=[SRC],
               datas=[(os.path.join(ROOT, "assets"), "assets")],
               hiddenimports=["gui.about_page", "gui.antibypass_page", "gui.display_settings", "comtypes.stream"],
               excludes=["pytest"])
svc = Analysis([os.path.join(SRC, "service_win.py")], pathex=[SRC],
               hiddenimports=["win32timezone"], excludes=["pytest", "customtkinter", "PIL", "pystray", "uiautomation"])

gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="Lockdown", icon=ICON, console=False,
              version=GUI_RES)
svc_exe = EXE(PYZ(svc.pure), svc.scripts, [], exclude_binaries=True, name="LockdownService", icon=ICON,
              console=False, version=SVC_RES)
COLLECT(gui_exe, gui.binaries, gui.datas, svc_exe, svc.binaries, svc.datas, name="Lockdown")
