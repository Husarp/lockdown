"""Windows Firewall rules that cut a blocked app's internet access (needs admin)."""
import subprocess

RULE_PREFIX = "Lockdown block "
QUIC_PREFIX = "Lockdown no QUIC "
# browsers that speak QUIC (HTTP/3): while a site is blocked they get no outgoing UDP 443 (service.update_quic)
BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe", "vivaldi.exe", "chromium.exe",
            "arc.exe"}


def _netsh(*args: str) -> bool:
    result = subprocess.run(["netsh", "advfirewall", "firewall", *args], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode == 0


def rule_name(exe: str) -> str:
    return RULE_PREFIX + exe


def add(exe: str, path: str) -> bool:
    """Block all outgoing and incoming traffic of the program at `path`."""
    remove(exe)   # never leave duplicates
    ok = True
    for direction in ("out", "in"):
        ok &= _netsh("add", "rule", f"name={rule_name(exe)}", f"dir={direction}", "action=block",
                     f"program={path}", "enable=yes")
    return ok


def remove(exe: str) -> bool:
    return _netsh("delete", "rule", f"name={rule_name(exe)}")


def add_quic(exe: str, path: str) -> bool:
    """No outgoing UDP 443 (QUIC / HTTP/3) for the browser at `path`: it falls back to TCP, which can be cut."""
    remove_quic(exe)
    return _netsh("add", "rule", f"name={QUIC_PREFIX}{exe}", "dir=out", "action=block", "protocol=UDP",
                  "remoteport=443", f"program={path}", "enable=yes")


def remove_quic(exe: str) -> bool:
    return _netsh("delete", "rule", f"name={QUIC_PREFIX}{exe}")
