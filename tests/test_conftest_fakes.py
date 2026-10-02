"""tests/conftest.py lets the suite run off Windows by faking the Win32 bits that src/ touches at import time.
These pin that the fakes are inert (a fake call does nothing and reports "nothing found") and that on Windows
the conftest leaves the real modules alone, so production behaviour is never what the tests stub."""
import ctypes
import sys

import pytest

off_windows = pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows fakes")
on_windows = pytest.mark.skipif(sys.platform != "win32", reason="checks the real Win32 modules are untouched")


@off_windows
def test_a_fake_dll_call_does_nothing():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = None          # src sets argtypes/restype at import - must be accepted
    assert k32.OpenProcess(0, False, 1) == 0
    assert ctypes.windll.user32.GetKeyboardLayout(0) == 0


@off_windows
def test_the_fake_registry_is_empty_not_broken():
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Lockdown") as k:
        assert winreg.QueryInfoKey(k)[0] == 0
        with pytest.raises(FileNotFoundError):     # what Windows raises for a missing value
            winreg.QueryValueEx(k, "anything")


@off_windows
def test_window_only_modules_import():
    import blocker.apps  # noqa: F401  (ctypes.WinDLL at import)
    import blocker.dnsfilter  # noqa: F401  (winreg at import)
    import service_win  # noqa: F401  (pywin32 at import)


@on_windows
def test_windows_keeps_the_real_modules():
    import winreg
    assert ctypes.WinDLL.__module__ == "ctypes"
    assert type(winreg).__name__ == "module" and hasattr(winreg, "HKEYType")
