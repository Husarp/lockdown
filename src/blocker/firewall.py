"""Windows Firewall rules that cut a blocked app's internet access (needs admin)."""
import subprocess

RULE_PREFIX = "Lockdown block "


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
