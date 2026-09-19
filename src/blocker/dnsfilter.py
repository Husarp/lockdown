"""DNS filter for the protection lists (millions of domains - far too many for the hosts file).

A small DNS server on 127.0.0.1:53 and [::1]:53 (UDP + TCP). A name on a protection list gets 127.0.0.1 (so the
blocked-visit listener can show which list blocked it; other record types get an empty answer); everything else is
passed on unchanged to the network's own DNS servers. Forced SafeSearch: a search engine's name (e.g. www.google.com)
is answered with the addresses of its "safe" name (forcesafesearch.google.com), looked up from the network's DNS.

Windows is pointed at it per network adapter: IPv4 DNS = "127.0.0.1, <the adapter's own DNS servers>", IPv6 DNS =
"::1". Windows only asks the second server when the first doesn't answer, so if the service ever stops, the internet
keeps working (just without the lists). The adapters' original settings are saved and restored by
`service.py restore-dns` (uninstall / repair) and when every list is switched off.
Standard library only (runs in the service)."""
import ctypes
import json
import socket
import struct
import threading
import uuid
import winreg
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

SAVED_KEY = "dns_filter.saved"   # JSON {adapter guid: {"v4": original static DNS or "" (automatic), "v6": ...}}
LISTEN = [("127.0.0.1", socket.AF_INET), ("::1", socket.AF_INET6)]
FILTER_V4, FILTER_V6 = "127.0.0.1", "::1"
UPSTREAM_TIMEOUT = 2.0
BLOCK_TTL = 60
TYPE_A, TYPE_AAAA = 1, 28
_IFACES = {False: r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces",
           True: r"SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters\Interfaces"}


# ---------- DNS messages ----------

def question(msg: bytes) -> tuple[str, int, int] | None:
    """(name, type, end of the question) of a query, or None if it can't be read."""
    if len(msg) < 12 or struct.unpack(">H", msg[4:6])[0] != 1:
        return None
    labels, i = [], 12
    while i < len(msg):
        n = msg[i]
        if n == 0:
            if i + 5 > len(msg):
                return None
            return ".".join(labels).lower(), struct.unpack(">H", msg[i + 1:i + 3])[0], i + 5
        if n & 0xC0 or i + 1 + n > len(msg):
            return None
        labels.append(msg[i + 1:i + 1 + n].decode("ascii", errors="replace"))
        i += 1 + n
    return None


def blocked_reply(msg: bytes, qtype: int, end: int) -> bytes:
    """Answer: 127.0.0.1 for an A query, an empty (but successful) answer for anything else."""
    flags = 0x8180 | (struct.unpack(">H", msg[2:4])[0] & 0x0100)   # response, recursion available, copy RD
    answer = b""
    if qtype == TYPE_A:
        answer = struct.pack(">HHHIH", 0xC00C, TYPE_A, 1, BLOCK_TTL, 4) + socket.inet_aton("127.0.0.1")
    return msg[:2] + struct.pack(">HHHHH", flags, 1, 1 if answer else 0, 0, 0) + msg[12:end] + answer


def query(name: str, qtype: int, qid: bytes) -> bytes:
    labels = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.split("."))
    return qid + struct.pack(">HHHHH", 0x0100, 1, 0, 0, 0) + labels + b"\0" + struct.pack(">HH", qtype, 1)


def _skip_name(msg: bytes, i: int) -> int:
    while msg[i]:
        if msg[i] & 0xC0:
            return i + 2
        i += 1 + msg[i]
    return i + 1


def records(reply: bytes, qtype: int) -> list[tuple[int, bytes]]:
    """(ttl, data) of the answer records of type `qtype` in a DNS reply."""
    count = struct.unpack(">H", reply[6:8])[0]
    i = _skip_name(reply, 12) + 4
    out = []
    for _ in range(count):
        i = _skip_name(reply, i)
        rtype, _cls, ttl, length = struct.unpack(">HHIH", reply[i:i + 10])
        if rtype == qtype:
            out.append((ttl, reply[i + 10:i + 10 + length]))
        i += 10 + length
    return out


def renamed_reply(msg: bytes, qtype: int, end: int, found: list[tuple[int, bytes]]) -> bytes:
    """Answer the question in `msg` with these A / AAAA records (as if they were the asked name's own)."""
    flags = 0x8180 | (struct.unpack(">H", msg[2:4])[0] & 0x0100)
    answers = b"".join(struct.pack(">HHHIH", 0xC00C, qtype, 1, ttl, len(data)) + data for ttl, data in found)
    return msg[:2] + struct.pack(">HHHHH", flags, 1, len(found), 0, 0) + msg[12:end] + answers


def failure_reply(msg: bytes) -> bytes:
    """SERVFAIL - no network DNS server answered."""
    return msg[:2] + struct.pack(">H", 0x8182) + msg[4:6] + b"\0" * 6 + msg[12:]


class Server:
    """blocked(name) -> truthy if the name is on a list; safe(name) -> the "safe" name to answer with instead, or
    None. `upstreams`: the network's DNS servers (set by the service)."""

    def __init__(self, blocked, log=None, listen=LISTEN, port=53, safe=lambda name: None):
        self.blocked, self.log, self.listen, self.port, self.safe = blocked, log, listen, port, safe
        self.upstreams: list[str] = []
        self.upstream_port = 53
        self.pool = ThreadPoolExecutor(max_workers=32)

    def start(self):
        """Bind every address first (raises OSError if port 53 is taken), then serve in background threads."""
        sockets = []
        for host, family in self.listen:
            udp = socket.socket(family, socket.SOCK_DGRAM)
            udp.bind((host, self.port))
            tcp = socket.socket(family, socket.SOCK_STREAM)
            tcp.bind((host, self.port))
            tcp.listen(16)
            sockets += [(self._serve_udp, udp), (self._serve_tcp, tcp)]
        for target, sock in sockets:
            threading.Thread(target=target, args=(sock,), daemon=True).start()

    def answer(self, msg: bytes, tcp: bool = False) -> bytes | None:
        q = question(msg)
        if q and self.blocked(q[0]):
            return blocked_reply(msg, q[1], q[2])
        target = q and self.safe(q[0])
        if target:
            name, qtype, end = q
            if qtype not in (TYPE_A, TYPE_AAAA):   # e.g. HTTPS records could hint the normal addresses: none
                return blocked_reply(msg, qtype, end)
            reply = self._forward(query(target, qtype, msg[:2]), tcp)
            if reply[3] & 0x0F:   # the network's DNS failed: say so (not "no addresses")
                return failure_reply(msg)
            try:
                return renamed_reply(msg, qtype, end, records(reply, qtype))
            except (struct.error, IndexError):
                return failure_reply(msg)
        return self._forward(msg, tcp)

    def _forward(self, msg: bytes, tcp: bool) -> bytes:
        for server in list(self.upstreams):
            family = socket.AF_INET6 if ":" in server else socket.AF_INET
            try:
                with socket.socket(family, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as s:
                    s.settimeout(UPSTREAM_TIMEOUT)
                    if tcp:
                        s.connect((server, self.upstream_port))
                        s.sendall(struct.pack(">H", len(msg)) + msg)
                        return _read_tcp(s)
                    s.sendto(msg, (server, self.upstream_port))
                    while True:
                        reply, _ = s.recvfrom(65535)
                        if reply[:2] == msg[:2]:
                            return reply
            except OSError:
                continue
        return failure_reply(msg)

    def _serve_udp(self, sock):
        while True:
            try:
                msg, addr = sock.recvfrom(65535)
            except OSError:   # e.g. "connection reset" after a reply to a client that gave up
                continue
            self.pool.submit(self._reply_udp, sock, msg, addr)

    def _reply_udp(self, sock, msg, addr):
        try:
            reply = self.answer(msg)
            if reply:
                sock.sendto(reply, addr)
        except Exception:
            if self.log:
                self.log.exception("DNS filter: answering a query failed")

    def _serve_tcp(self, sock):
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            self.pool.submit(self._reply_tcp, conn)

    def _reply_tcp(self, conn):
        with conn:
            try:
                conn.settimeout(5)
                msg = _read_tcp(conn)
                reply = self.answer(msg, tcp=True)
                conn.sendall(struct.pack(">H", len(reply)) + reply)
            except (OSError, ValueError):
                pass


def _read_tcp(s) -> bytes:
    def exactly(n):
        data = b""
        while len(data) < n:
            chunk = s.recv(n - len(data))
            if not chunk:
                raise ValueError("connection closed")
            data += chunk
        return data
    return exactly(struct.unpack(">H", exactly(2))[0])


# ---------- pointing Windows at the filter ----------

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _DnsInterfaceSettings(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("Flags", ctypes.c_ulonglong), ("Domain", wintypes.LPWSTR),
                ("NameServer", wintypes.LPWSTR), ("SearchList", wintypes.LPWSTR),
                ("RegistrationEnabled", wintypes.ULONG), ("RegisterAdapterName", wintypes.ULONG),
                ("EnableLLMNR", wintypes.ULONG), ("QueryAdapterName", wintypes.ULONG),
                ("ProfileNameServer", wintypes.LPWSTR)]


_DNS_SETTING_IPV6, _DNS_SETTING_NAMESERVER = 0x1, 0x2


def set_dns(guid: str, servers: str, ipv6: bool = False) -> int:
    """Set an adapter's DNS servers ("a,b"; "" = automatic). Returns the Windows error code (0 = done)."""
    fn = ctypes.windll.iphlpapi.SetInterfaceDnsSettings
    fn.argtypes = [_GUID, ctypes.POINTER(_DnsInterfaceSettings)]
    fn.restype = wintypes.DWORD
    settings = _DnsInterfaceSettings(Version=1, Flags=_DNS_SETTING_NAMESERVER | (_DNS_SETTING_IPV6 if ipv6 else 0),
                                     NameServer=servers)
    return fn(_GUID.from_buffer_copy(uuid.UUID(guid).bytes_le), ctypes.byref(settings))


def _servers(text: str) -> list[str]:
    return [s for s in text.replace(",", " ").split() if s]


def _value(key, name: str) -> str:
    try:
        value = winreg.QueryValueEx(key, name)[0]
    except OSError:
        return ""
    return " ".join(value) if isinstance(value, list) else str(value or "")


class _AdapterAddresses(ctypes.Structure):
    pass


_AdapterAddresses._fields_ = [   # only the start of IP_ADAPTER_ADDRESSES (up to OperStatus)
    ("Length", wintypes.ULONG), ("IfIndex", wintypes.DWORD), ("Next", ctypes.POINTER(_AdapterAddresses)),
    ("AdapterName", ctypes.c_char_p), ("FirstUnicastAddress", ctypes.c_void_p),
    ("FirstAnycastAddress", ctypes.c_void_p), ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p), ("DnsSuffix", wintypes.LPWSTR), ("Description", wintypes.LPWSTR),
    ("FriendlyName", wintypes.LPWSTR), ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", wintypes.ULONG), ("Flags", wintypes.ULONG), ("Mtu", wintypes.ULONG),
    ("IfType", wintypes.DWORD), ("OperStatus", ctypes.c_int)]


def connected() -> set[str]:
    """GUIDs ("{...}", lowercase) of the network adapters that are up right now (the registry also remembers
    adapters that were unplugged long ago)."""
    size = wintypes.ULONG(64 * 1024)
    for _ in range(3):
        buf = ctypes.create_string_buffer(size.value)
        err = ctypes.windll.iphlpapi.GetAdaptersAddresses(0, 0x0F, None, buf, ctypes.byref(size))   # skip addresses
        if err != 111:   # ERROR_BUFFER_OVERFLOW: size now holds what's needed
            break
    if err:
        return set()
    out, entry = set(), ctypes.cast(buf, ctypes.POINTER(_AdapterAddresses))
    while entry:
        if entry.contents.OperStatus == 1:   # IfOperStatusUp
            out.add(entry.contents.AdapterName.decode().lower())
        entry = entry.contents.Next
    return out


def adapters() -> dict[str, dict]:
    """Connected network adapters that use DNS: {guid: {"v4": static IPv4 DNS, "dhcp": IPv4 DNS from the network,
    "v6": static IPv6 DNS}}."""
    out = {}
    up = connected()
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _IFACES[False]) as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            guid = winreg.EnumKey(root, i)
            if guid.lower() not in up:
                continue
            with winreg.OpenKey(root, guid) as k:
                info = {"v4": _value(k, "NameServer"), "dhcp": _value(k, "DhcpNameServer"), "v6": ""}
            if info["v4"] or info["dhcp"]:
                out[guid] = info
    for guid, info in out.items():
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{_IFACES[True]}\{guid}") as k:
                info["v6"] = _value(k, "NameServer")
        except OSError:
            pass
    return out


def plan(current: dict[str, dict], saved: dict[str, dict]) -> tuple[dict, list, list[str]]:
    """What to change so every adapter asks the filter first.
    Returns (saved originals, [(guid, servers, ipv6)] to set, upstream DNS servers to forward to)."""
    saved = dict(saved)
    changes, upstreams = [], []
    for guid, info in current.items():
        ours = _servers(info["v4"])[:1] == [FILTER_V4]
        if guid not in saved or not ours:   # new adapter, or its DNS was changed since: remember the original
            saved[guid] = {"v4": "" if ours else info["v4"],
                           "v6": "" if info["v6"] == FILTER_V6 else info["v6"]}
        original = _servers(saved[guid]["v4"]) or _servers(info["dhcp"])
        upstream = [s for s in original if s not in (FILTER_V4, FILTER_V6) and not s.startswith("127.")]
        if not upstream:
            continue
        upstreams += [s for s in upstream if s not in upstreams]
        wanted = ",".join([FILTER_V4] + upstream)
        if ",".join(_servers(info["v4"])) != wanted:
            changes.append((guid, wanted, False))
        if info["v6"] != FILTER_V6:
            changes.append((guid, FILTER_V6, True))
    return saved, changes, upstreams


def _load(db) -> dict:
    try:
        return json.loads(db.get_setting(SAVED_KEY, "") or "{}")
    except ValueError:
        return {}


def point_to_filter(db, log=None) -> list[str]:
    """Make every adapter use the filter (new networks too). Returns the upstream DNS servers."""
    saved, changes, upstreams = plan(adapters(), _load(db))
    db.set_setting(SAVED_KEY, json.dumps(saved))
    for guid, servers, ipv6 in changes:
        err = set_dns(guid, servers, ipv6)
        if log:
            if err:
                log.warning("DNS filter: couldn't set DNS of adapter %s (error %d)", guid, err)
            else:
                log.info("DNS filter: adapter %s now uses %s", guid, servers)
    return upstreams


def restore(db, log=None):
    """Give every adapter its original DNS settings back (automatic DNS for one still pointing at the filter whose
    original wasn't saved)."""
    saved = _load(db)
    for guid, info in adapters().items():
        if guid not in saved and (_servers(info["v4"])[:1] == [FILTER_V4] or info["v6"] == FILTER_V6):
            saved[guid] = {"v4": "", "v6": ""}
    for guid, original in saved.items():
        for ipv6 in (False, True):
            err = set_dns(guid, original["v6" if ipv6 else "v4"], ipv6)
            if log and err and err != 2:   # 2: adapter no longer exists
                log.warning("DNS filter: couldn't restore DNS of adapter %s (error %d)", guid, err)
    if saved and log:
        log.info("DNS filter: original DNS settings restored")
    db.set_setting(SAVED_KEY, "{}")
