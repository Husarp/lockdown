"""LockdownService.exe: the enforcement service (service.py) as a real Windows service ("Lockdown Enforcer",
LocalSystem, starts at boot, Windows restarts it if it crashes). Started by Windows with no arguments; the
installer / uninstaller also run it with a command:
    LockdownService.exe install | remove | start | stop     (pywin32's service commands)
    LockdownService.exe restore-dns | remove-policies | once   (service.py's commands)"""
import sys
import threading

import servicemanager
import win32service
import win32serviceutil

import service
from paths import SERVICE_NAME


class LockdownService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = "Lockdown Enforcer"
    _svc_description_ = ("Enforces Lockdown's blocks: sites (hosts file + DNS filter), apps, protection lists and "
                         "browser policies. Stopping it stops the blocking - Lockdown starts it again.")

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = threading.Event()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stop_event.set()

    def SvcDoRun(self):
        self.ReportServiceStatus(win32service.SERVICE_RUNNING)
        service.main(["run"], stop=self.stop_event)


def main():
    if len(sys.argv) == 1:   # started by Windows
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(LockdownService)
        servicemanager.StartServiceCtrlDispatcher()
    elif sys.argv[1] in ("restore-dns", "remove-policies", "once"):
        sys.exit(service.main(sys.argv[1:]))
    else:
        win32serviceutil.HandleCommandLine(LockdownService)


if __name__ == "__main__":
    main()
