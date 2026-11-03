"""Locked browser policies so browsers can't skip the hosts file.

- DNS-over-HTTPS off: DoH resolves names through the browser's own server, bypassing the hosts file.
- QUIC/HTTP3 off: QUIC runs over UDP, which connections.close_to() can't close; browsers fall back to TCP.
- Chromium's built-in DNS client off: names are then looked up by Windows, so they land in the Windows DNS cache,
  where the network log finds which site each connection belongs to.

- Forced SafeSearch (Protection tab, on by default): Google SafeSearch, Bing strict SafeSearch. YouTube Restricted
  Mode (moderate) is a separate switch. Next to the DNS filter doing the same for every browser.

Browsers show "managed by your organization" while these are set. Needs admin (HKLM).
"""
import json
import winreg

REG_SZ, REG_DWORD, REG_MULTI_SZ = winreg.REG_SZ, winreg.REG_DWORD, winreg.REG_MULTI_SZ

_CHROMIUM = {"DnsOverHttpsMode": (REG_SZ, "off"), "QuicAllowed": (REG_DWORD, 0),
             "BuiltInDnsClientEnabled": (REG_DWORD, 0)}
_FIREFOX_PREFS = json.dumps({"network.http.http3.enable": {"Value": False, "Status": "locked", "Type": "boolean"}})

# registry key (under HKLM) -> {value name: (type, data)}
POLICIES = {
    r"SOFTWARE\Policies\Google\Chrome": _CHROMIUM,
    r"SOFTWARE\Policies\Microsoft\Edge": _CHROMIUM,
    r"SOFTWARE\Policies\BraveSoftware\Brave": _CHROMIUM,
    r"SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS": {"Enabled": (REG_DWORD, 0), "Locked": (REG_DWORD, 1)},
    r"SOFTWARE\Policies\Mozilla\Firefox": {"Preferences": (REG_MULTI_SZ, [_FIREFOX_PREFS])},
}


_SAFE = {"ForceGoogleSafeSearch": (REG_DWORD, 1)}
_YT = {"ForceYouTubeRestrict": (REG_DWORD, 1)}
SAFE_SEARCH = {
    r"SOFTWARE\Policies\Google\Chrome": _SAFE,
    r"SOFTWARE\Policies\Microsoft\Edge": {**_SAFE, "ForceBingSafeSearch": (REG_DWORD, 2)},
    r"SOFTWARE\Policies\BraveSoftware\Brave": _SAFE,
}
YOUTUBE = {path: _YT for path in (r"SOFTWARE\Policies\Google\Chrome", r"SOFTWARE\Policies\Microsoft\Edge",
                                  r"SOFTWARE\Policies\BraveSoftware\Brave")}

# Machine-local originals, separate from portable setup backups. Native registry types retain even binary values.
_BACKUP = r"SOFTWARE\Lockdown\BrowserPolicyBackup"


def _legacy_install() -> bool:
    """Migrate only the complete legacy footprint, once, before this version enforces anything.
    A lone matching administrator policy is not evidence of a Lockdown installation. If an earlier upgrade
    already captured originals, inspect those instead of the currently enforced policies.
    """
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, _BACKUP, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as root:
        try:
            return bool(winreg.QueryValueEx(root, "legacy")[0])
        except FileNotFoundError:
            pass
        matches = True
        for path, entries in POLICIES.items():
            with (winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as key,
                  winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, _BACKUP + "\\" + path, 0, winreg.KEY_READ) as saved):
                for name, (kind, data) in entries.items():
                    try:
                        present = winreg.QueryValueEx(saved, "present." + name)[0]
                    except FileNotFoundError:
                        try:
                            original = winreg.QueryValueEx(key, name)
                        except FileNotFoundError:
                            original = None
                    else:
                        original = winreg.QueryValueEx(saved, "value." + name) if present else None
                    matches = matches and original == (data, kind)
        winreg.SetValueEx(root, "legacy", 0, REG_DWORD, int(matches))
        return matches


def _remember(saved, key, name, own, legacy=False):
    try:
        present = winreg.QueryValueEx(saved, "present." + name)[0]
    except FileNotFoundError:
        try:
            original = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            original = None
    else:
        original = winreg.QueryValueEx(saved, "value." + name) if present else None
        if not (legacy and original == own):
            return
    if legacy and original == own:
        original = None
    if original is not None:
        data, kind = original
        winreg.SetValueEx(saved, "value." + name, 0, kind, data)
    # Marker last: backup failure must never overwrite an independent original policy.
    winreg.SetValueEx(saved, "present." + name, 0, REG_DWORD, int(original is not None))


def _restore_value(saved, key, name, own, forget=False) -> bool:
    try:
        present, _ = winreg.QueryValueEx(saved, "present." + name)
    except FileNotFoundError:
        return False   # never owned by us
    try:
        current = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        current = None
    original = winreg.QueryValueEx(saved, "value." + name) if present else None
    # A different current value was set independently after enforcement; leave it (including deletion) alone.
    changed = current != original and current == own
    if changed:
        if original is None:
            winreg.DeleteValue(key, name)
        else:
            data, kind = original
            winreg.SetValueEx(key, name, 0, kind, data)
    if forget:
        winreg.DeleteValue(saved, "present." + name)
        if present:
            winreg.DeleteValue(saved, "value." + name)
    return changed


def apply(safe_search: bool = False, youtube: bool = False) -> bool:
    """Set policies, saving originals once (optional policies return to their originals when off).
    Returns True if anything was written."""
    changed = False
    legacy = _legacy_install()
    for path in POLICIES.keys() | SAFE_SEARCH.keys() | YOUTUBE.keys():
        wanted = {**POLICIES.get(path, {}), **(SAFE_SEARCH.get(path, {}) if safe_search else {}),
                  **(YOUTUBE.get(path, {}) if youtube else {})}
        with (winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key,
              winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, _BACKUP + "\\" + path, 0,
                                 winreg.KEY_READ | winreg.KEY_SET_VALUE) as saved):
            for name, (kind, data) in wanted.items():
                _remember(saved, key, name, (data, kind), legacy)
                try:
                    if winreg.QueryValueEx(key, name) == (data, kind):
                        continue
                except FileNotFoundError:
                    pass
                winreg.SetValueEx(key, name, 0, kind, data)
                changed = True
            for name in (SAFE_SEARCH.get(path, {}).keys() | YOUTUBE.get(path, {}).keys()) - wanted.keys():
                kind, data = {**SAFE_SEARCH.get(path, {}), **YOUTUBE.get(path, {})}[name]
                if legacy:
                    _remember(saved, key, name, (data, kind), legacy=True)
                changed = _restore_value(saved, key, name, (data, kind)) or changed
    return changed


def remove():
    """Restore originals saved by apply() (used on uninstall). Keys are left in place."""
    for path in POLICIES.keys() | SAFE_SEARCH.keys() | YOUTUBE.keys():
        try:
            with (winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BACKUP + "\\" + path, 0,
                                 winreg.KEY_READ | winreg.KEY_SET_VALUE) as saved,
                  winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0,
                                     winreg.KEY_READ | winreg.KEY_SET_VALUE) as key):
                entries = {**POLICIES.get(path, {}), **SAFE_SEARCH.get(path, {}), **YOUTUBE.get(path, {})}
                for name, (kind, data) in entries.items():
                    _restore_value(saved, key, name, (data, kind), forget=True)
        except FileNotFoundError:
            pass

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BACKUP, 0, winreg.KEY_SET_VALUE) as root:
            winreg.DeleteValue(root, "legacy")
    except FileNotFoundError:
        pass
