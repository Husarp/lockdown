"""Wake the service after an immediate change; the service still reads and validates the saved rules itself."""
import logging
import sys

NAME = r"Global\Lockdown.EnforcementRecheck"
EVENT_MODIFY_STATE = 0x0002
log = logging.getLogger("lockdown.enforcement_wake")


def request():
    if sys.platform != "win32":
        return
    import win32event
    try:
        handle = win32event.OpenEvent(EVENT_MODIFY_STATE, False, NAME)
        try:
            win32event.SetEvent(handle)
        finally:
            handle.Close()
    except Exception:
        log.debug("Service wake unavailable; the regular enforcement pass will recheck", exc_info=True)


class Wake:
    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        import pywintypes
        import win32event
        import win32security
        try:
            # Standard users may signal it, but cannot wait on it or change its permissions.
            sa = pywintypes.SECURITY_ATTRIBUTES()
            sa.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x0002;;;AU)", 1)
            self.handle = win32event.CreateEvent(sa, False, False, NAME)  # auto-reset, initially asleep
        except Exception:
            log.warning("Service wake unavailable; keeping the regular enforcement interval", exc_info=True)

    def wait(self, stop, seconds):
        if self.handle is None:
            stop.wait(seconds)
        else:
            import win32event
            win32event.WaitForSingleObject(self.handle, int(seconds * 1000))

    def close(self):
        if self.handle is not None:
            self.handle.Close()
            self.handle = None
