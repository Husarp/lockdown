"""Locked browser policies so browsers can't skip the hosts file.

- DNS-over-HTTPS off: DoH resolves names through the browser's own server, bypassing the hosts file.
- QUIC/HTTP3 off: QUIC runs over UDP, which connections.close_to() can't close; browsers fall back to TCP.

Browsers show "managed by your organization" while these are set. Needs admin (HKLM).
"""
import json
import winreg

REG_SZ, REG_DWORD, REG_MULTI_SZ = winreg.REG_SZ, winreg.REG_DWORD, winreg.REG_MULTI_SZ

_CHROMIUM = {"DnsOverHttpsMode": (REG_SZ, "off"), "QuicAllowed": (REG_DWORD, 0)}
_FIREFOX_PREFS = json.dumps({"network.http.http3.enable": {"Value": False, "Status": "locked", "Type": "boolean"}})

# registry key (under HKLM) -> {value name: (type, data)}
POLICIES = {
    r"SOFTWARE\Policies\Google\Chrome": _CHROMIUM,
    r"SOFTWARE\Policies\Microsoft\Edge": _CHROMIUM,
    r"SOFTWARE\Policies\BraveSoftware\Brave": _CHROMIUM,
    r"SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS": {"Enabled": (REG_DWORD, 0), "Locked": (REG_DWORD, 1)},
    r"SOFTWARE\Policies\Mozilla\Firefox": {"Preferences": (REG_MULTI_SZ, [_FIREFOX_PREFS])},
}


def apply() -> bool:
    """Set every policy that is missing or different. Returns True if anything was written."""
    changed = False
    for path, values in POLICIES.items():
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            for name, (kind, data) in values.items():
                try:
                    if winreg.QueryValueEx(key, name) == (data, kind):
                        continue
                except FileNotFoundError:
                    pass
                winreg.SetValueEx(key, name, 0, kind, data)
                changed = True
    return changed


def remove():
    """Delete the values set by apply() (used on uninstall). Keys are left in place."""
    for path, values in POLICIES.items():
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE) as key:
                for name in values:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass
