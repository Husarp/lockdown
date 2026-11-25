"""Native firewall rules for blocked apps, video addresses and browser QUIC paths."""
import hashlib
import ipaddress
import logging
import ntpath
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass

from blocker.connections import is_loopback

log = logging.getLogger("lockdown.service")
RULE_PREFIX = "Lockdown block "
QUIC_PREFIX = "Lockdown no QUIC "
VIDEO_RULE = "Lockdown video hosts"
_OWNER = "Lockdown managed site transport rule v2"
_CURRENT = {"quic": "Lockdown Site QUIC ", "video": "Lockdown Site Video "}
_RECHECK_SEC = 10.0
VIDEO_DOMAINS = ("googlevideo.com", "nflxvideo.net", "ttvnw.net")


def _netsh(*args: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(["netsh", "advfirewall", "firewall", *args], capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=4)
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("Firewall native %s failed: %s", " ".join(args[:2]), type(error).__name__)
        return False
    if result.returncode:
        log.warning("Firewall native %s returned status %d", " ".join(args[:2]), result.returncode)
    return result.returncode == 0


def rule_name(exe: str) -> str:
    return RULE_PREFIX + exe


def add(exe: str, path: str) -> bool:
    """Block all outgoing and incoming traffic of the program at `path`."""
    remove(exe)
    ok = True
    for direction in ("out", "in"):
        ok &= _netsh("add", "rule", f"name={rule_name(exe)}", f"dir={direction}", "action=block",
                     f"program={path}", "enable=yes")
    return ok


def remove(exe: str) -> bool:
    return _netsh("delete", "rule", f"name={rule_name(exe)}")


def is_video_host(name: str) -> bool:
    name = name.lower().rstrip(".")
    return any(name == d or name.endswith("." + d) for d in VIDEO_DOMAINS)


def program_path(path):
    return ntpath.normcase(ntpath.normpath(path))


def _addresses(values):
    out = set()
    for value in values:
        if is_loopback(value):
            continue
        out.add(str(ipaddress.ip_address(value)))
    return tuple(sorted(out))


@dataclass(frozen=True)
class SiteRule:
    kind: str
    target: str | tuple[str, ...]

    @property
    def name(self):
        key = self.target if self.kind == "quic" else ",".join(self.target)
        return _CURRENT[self.kind] + hashlib.sha256(key.encode()).hexdigest()[:32]

    def add(self):
        if self.kind == "quic":
            return add_quic(self.name, self.target)
        return set_video_block(self.target)


def quic_rule(path):
    return SiteRule("quic", program_path(path))


def video_rule(ips):
    return SiteRule("video", _addresses(ips))


def add_quic(name: str, path: str) -> bool:
    return _netsh("add", "rule", f"name={name}", f"description={_OWNER}", "dir=out", "action=block",
                  "protocol=UDP", "remoteport=443", f"program={path}", "enable=yes", "profile=any")


def set_video_block(ips) -> bool:
    """Install an IPv4/IPv6 rule alongside any preceding generation."""
    rule = video_rule(ips)
    if not rule.target:
        return False
    return _netsh("add", "rule", f"name={rule.name}", f"description={_OWNER}", "dir=out", "action=block",
                  f"remoteip={','.join(rule.target)}", "enable=yes", "profile=any")


@contextmanager
def _firewall_apartment():
    if sys.platform != "win32":
        yield
        return
    import pythoncom

    initialized = False
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
        except pythoncom.com_error as error:
            if error.hresult != -2147417850:  # RPC_E_CHANGED_MODE: this thread already has an apartment
                raise
        yield
    finally:
        if initialized:
            pythoncom.CoUninitialize()


def _native_rules():
    if sys.platform != "win32":
        return ()
    # The automation API avoids parsing localized netsh column labels and shows the
    # actual program, protocol and scope of the rules, including externally changed ones.
    from win32com.client import Dispatch
    return Dispatch("HNetCfg.FwPolicy2").Rules


def _native_ips(text):
    try:
        values = []
        for entry in text.split(","):
            network = ipaddress.ip_network(entry.strip(), strict=True)
            if network.num_addresses != 1 or is_loopback(str(network.network_address)):
                return ()
            values.append(str(network.network_address))
        return tuple(sorted(set(values)))
    except ValueError:
        return ()


def _restriction(native, kind):
    if (native.Direction != 2 or native.Action != 0 or not native.Enabled
            or native.Profiles not in (-1, 0x7FFFFFFF) or native.LocalAddresses != "*"
            or native.InterfaceTypes != "All" or native.ServiceName):
        return None
    if kind == "quic":
        if (native.Protocol == 17 and native.RemotePorts == "443" and native.LocalPorts == "*"
                and native.RemoteAddresses == "*" and native.ApplicationName):
            return quic_rule(native.ApplicationName)
    elif native.Protocol == 256 and not native.ApplicationName:
        values = _native_ips(native.RemoteAddresses)
        if values:
            return SiteRule("video", values)
    return None


def inventory(kind, legacy):
    """Names present in the native store, owned rules, and exactly verified protections."""
    with _firewall_apartment():
        return _inspect_rules(_native_rules(), kind, legacy)


def _inspect_rules(rules, kind, legacy):
    present, owned, installed, duplicates = set(), set(), {}, set()
    legacy = {name.casefold(): rule for name, rule in legacy.items()}
    for native in rules:
        name = native.Name.casefold()
        if not name.startswith(_CURRENT[kind].casefold()) and name not in legacy:
            continue
        if name in present:
            duplicates.add(name)
        present.add(name)
        actual = _restriction(native, kind)
        if getattr(native, "Description", None) == _OWNER and name.startswith(_CURRENT[kind].casefold()):
            owned.add(name)
            if actual and name == actual.name.casefold():
                installed[name] = actual
        elif name in legacy and actual == legacy[name] and not getattr(native, "Description", None):
            # Old versions had no marker. Migrate only an exact previously saved rule.
            owned.add(name)
            installed[name] = actual
    return present, owned - duplicates, {name: rule for name, rule in installed.items() if name not in duplicates}


def remove_site_rule(kind, name, legacy):
    present, owned, _installed = inventory(kind, legacy)
    canonical = name.casefold()
    if canonical not in present:
        return True
    if canonical not in owned:
        return False
    return _netsh("delete", "rule", f"name={name}") and canonical not in inventory(kind, legacy)[0]


def remove_video_block():
    """Remove the marked video rules (legacy cleanup uses the persisted evidence)."""
    _, owned, _ = inventory("video", {})
    return all([remove_site_rule("video", name, {}) for name in sorted(owned)])


class SiteRules:
    """Reconcile one family of rules, holding verified coverage through replacement."""

    def __init__(self, kind):
        self.kind = kind
        self.desired = {}
        self.installed = {}
        self.pending_removal = set()
        self._unsafe = frozenset()
        self._present = None
        self._retry = {}
        self._next_check = float("-inf")

    @property
    def verified(self):
        return self._present is not None

    def confirmed_absent(self, name):
        return self._present is not None and name.casefold() not in self._present

    def _later(self, operation, now):
        attempt = self._retry.get(operation, (0, 0))[0] + 1
        self._retry[operation] = attempt, now + min(30, 2 ** min(attempt, 5))

    def reconcile(self, rules, legacy, now, *, unsafe=()):
        desired = {rule.name.casefold(): rule for rule in rules}
        changed = desired != self.desired or unsafe != self._unsafe
        self.desired, self._unsafe = desired, frozenset(unsafe)
        if not changed and now < self._next_check and all(now < retry for _, retry in self._retry.values()):
            return
        self._next_check = now + _RECHECK_SEC
        try:
            self._sync(legacy, now, changed)
        except Exception as error:
            log.warning("Site firewall %s inventory/operation failed: %s", self.kind, type(error).__name__)
            self._present = None

    def _sync(self, legacy, now, changed):
        desired = self.desired
        present, owned, self.installed = inventory(self.kind, legacy)
        self._present = present
        self.pending_removal = owned - desired.keys()
        self._retry = {op: state for op, state in self._retry.items()
                       if op[0] == "add" and op[1] in desired.keys() - self.installed.keys()
                       or op[0] == "remove" and op[1] in self.pending_removal}
        attempted = []
        for name in sorted(desired.keys() - self.installed.keys()):
            op = "add", name
            if now < self._retry.get(op, (0, now))[1]:
                continue
            rule = desired[name]
            if name in present and not (name in owned and remove_site_rule(self.kind, name, legacy)):
                self._later(op, now)
                log.warning("Site firewall %s rule conflict or failed repair", self.kind)
                continue
            if rule.add():
                attempted.append(name)
            else:
                self._later(op, now)
                log.warning("Site firewall %s add failed and will retry", self.kind)
        if attempted:
            present, owned, self.installed = inventory(self.kind, legacy)
            self._present = present
            self.pending_removal = owned - desired.keys()
            for name in attempted:
                op = "add", name
                if name in self.installed:
                    self._retry.pop(op, None)
                    rule = self.installed[name]
                    if self.kind == "video":
                        ipv6 = sum(":" in ip for ip in rule.target)
                        log.info("Site firewall video IP restriction verified: IPv4=%d IPv6=%d",
                                 len(rule.target) - ipv6, ipv6)
                    else:
                        log.info("Site firewall QUIC path rule verified: %s", ntpath.basename(rule.target))
                else:
                    self._later(op, now)
                    log.warning("Site firewall %s add returned success but the rule was not verified", self.kind)
        if desired.keys() - self.installed.keys():
            # An IP found to be shared must leave even when its replacement failed.
            removable = {name for name in self.pending_removal if name in self.installed
                         and self._unsafe.intersection(self.installed[name].target)}
        else:
            removable = self.pending_removal
        if changed and not desired and self.kind == "video" and not self.pending_removal:
            remove_video_block()
        for name in sorted(removable):
            op = "remove", name
            if now < self._retry.get(op, (0, now))[1]:
                continue
            if remove_site_rule(self.kind, name, legacy):
                self._present.discard(name)
                self.pending_removal.remove(name)
                self.installed.pop(name, None)
                self._retry.pop(op, None)
                log.info("Site firewall %s rule removed", self.kind)
            else:
                self._later(op, now)
                log.warning("Site firewall %s removal failed and will retry", self.kind)
