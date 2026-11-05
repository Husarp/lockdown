"""The wake carries no permissions to change rules and cannot replace periodic enforcement."""
from types import SimpleNamespace

import enforcement_wake


def test_wake_is_auto_reset_signal_only_and_service_keeps_timeout(monkeypatch):
    calls = []
    handle = SimpleNamespace(Close=lambda: calls.append("closed"))
    event = SimpleNamespace(
        CreateEvent=lambda *args: calls.append(("create", args)) or handle,
        OpenEvent=lambda *args: calls.append(("open", args)) or handle,
        SetEvent=lambda h: calls.append(("signal", h)),
        WaitForSingleObject=lambda *args: calls.append(("wait", args)))
    security = SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptor=lambda text, revision: text)
    import sys
    monkeypatch.setitem(sys.modules, "win32event", event)
    monkeypatch.setitem(sys.modules, "win32security", security)
    monkeypatch.setitem(sys.modules, "pywintypes", SimpleNamespace(SECURITY_ATTRIBUTES=SimpleNamespace))
    monkeypatch.setattr(enforcement_wake, "sys", SimpleNamespace(platform="win32"))
    wake = enforcement_wake.Wake()
    enforcement_wake.request()
    wake.wait(None, 2)
    wake.close()
    create = calls[0][1]
    assert create[1:] == (False, False, enforcement_wake.NAME)
    assert "(A;;0x0002;;;AU)" in create[0].SECURITY_DESCRIPTOR
    assert ("open", (enforcement_wake.EVENT_MODIFY_STATE, False, enforcement_wake.NAME)) in calls
    assert ("signal", handle) in calls
    assert ("wait", (handle, 2000)) in calls
    assert calls.count("closed") == 2


def test_unavailable_wake_falls_back_to_periodic_enforcement(monkeypatch):
    import sys
    def unavailable(*args):
        raise OSError("No service")
    monkeypatch.setitem(sys.modules, "win32event", SimpleNamespace(OpenEvent=unavailable, CreateEvent=unavailable))
    monkeypatch.setitem(sys.modules, "pywintypes", SimpleNamespace(SECURITY_ATTRIBUTES=SimpleNamespace))
    monkeypatch.setitem(sys.modules, "win32security", SimpleNamespace(ConvertStringSecurityDescriptorToSecurityDescriptor=unavailable))
    monkeypatch.setattr(enforcement_wake, "sys", SimpleNamespace(platform="win32"))
    enforcement_wake.request()
    wake = enforcement_wake.Wake()
    waits = []
    wake.wait(SimpleNamespace(wait=lambda seconds: waits.append(seconds)), 2)
    wake.close()
    assert waits == [2]
