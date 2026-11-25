"""Close already-open TCP connections to blocked sites (so an open tab can't keep using them).

Uses the Win32 IP Helper API: GetTcpTable + SetTcpEntry(DELETE_TCB). IPv4 only; needs admin.

A connection to a loopback or unspecified address is NEVER closed, whatever the addresses handed in say.
Blocked sites are pointed at 127.0.0.1 by the hosts file, and Windows loads the hosts file into its DNS
cache - so reading that cache back (which is how live connections are matched to names) reported
"youtube.com is at 127.0.0.1" and every loopback socket on the machine was cut every two seconds. That
broke unrelated software: Gradle builds died with "client disconnection detected", and local servers could
not be reached from the same machine. The hosts entry already stops the real traffic, so closing a loopback
socket achieves nothing even when the address is genuinely blocked.
"""
import ctypes
import ipaddress
import logging
import socket
import struct
import time
from ctypes import wintypes

MIB_TCP_STATE_ESTAB = 5
MIB_TCP_STATE_DELETE_TCB = 12
ERROR_INSUFFICIENT_BUFFER = 122
DNS_TIMEOUT = 2.0
log = logging.getLogger("lockdown.service")
_cut_error_at: dict[int, float] = {}


class MIB_TCPROW(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD), ("dwLocalPort", wintypes.DWORD),
                ("dwRemoteAddr", wintypes.DWORD), ("dwRemotePort", wintypes.DWORD)]


_iphlpapi = ctypes.WinDLL("iphlpapi")


def is_loopback(ip: str) -> bool:
    """Loopback (127.0.0.0/8, ::1) or unspecified (0.0.0.0, ::) - never something to close a socket for.
    An address that cannot be parsed counts as loopback too: if we can't tell what it is, we leave it alone."""
    try:
        address = ipaddress.ip_address(ip)
        address = getattr(address, "ipv4_mapped", None) or address
        return address.is_loopback or address.is_unspecified
    except ValueError:
        return True


def dns_servers() -> list[str]:
    """The network's own DNS servers, skipping Lockdown's filter and anything else on loopback."""
    from blocker import dnsfilter
    out = []
    for info in dnsfilter.adapters().values():
        for text in (info.get("v4", ""), info.get("dhcp", ""), info.get("v6", "")):
            for server in dnsfilter._servers(text):
                if not is_loopback(server) and server not in out:
                    out.append(server)
    return out


def _ask(server: str, name: str, deadline: float | None = None) -> set[str]:
    """One A-record question, straight to a real DNS server - never through the system resolver, which reads
    the hosts file we wrote and would answer 127.0.0.1 for everything we blocked."""
    from blocker import dnsfilter
    msg = dnsfilter.query(name, dnsfilter.TYPE_A, b"\x4c\x64")
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    end = time.monotonic() + DNS_TIMEOUT if deadline is None else deadline
    with socket.socket(family, socket.SOCK_DGRAM) as s:
        s.settimeout(max(0.001, min(DNS_TIMEOUT, end - time.monotonic())))
        s.connect((server, 53))
        s.sendall(msg)
        while (remaining := end - time.monotonic()) > 0:
            s.settimeout(min(DNS_TIMEOUT, remaining))
            reply = s.recv(65535)
            if reply[:2] == msg[:2]:
                return {socket.inet_ntop(socket.AF_INET, data)
                        for _ttl, data in dnsfilter.records(reply, dnsfilter.TYPE_A) if len(data) == 4}
    return set()


def resolve(hostnames: list[str], servers: list[str] | None = None, *, deadline: float | None = None) -> set[str]:
    """Real IPv4 addresses of the hostnames, asked of a real DNS server so that the hosts file we write
    cannot feed its own 127.0.0.1 answers back to us. Falls back to the system resolver only when the network
    has no usable DNS server. A deadline skips the unbounded system fallback. Loopback is always dropped."""
    ips: set[str] = set()
    servers = dns_servers() if servers is None else servers
    for h in hostnames:
        answered = False
        for server in servers:
            if deadline is not None and time.monotonic() >= deadline:
                return {ip for ip in ips if not is_loopback(ip)}
            try:
                ips |= _ask(server, h, deadline)
                answered = True
                break
            except (OSError, struct.error, IndexError, UnicodeEncodeError):
                continue
        if not answered and deadline is None:
            try:
                ips.update(info[4][0] for info in socket.getaddrinfo(h, 443, socket.AF_INET, socket.SOCK_STREAM))
            except OSError:
                pass
    return {ip for ip in ips if not is_loopback(ip)}


def _tcp_rows() -> list[MIB_TCPROW]:
    size = wintypes.DWORD(0)
    status = _iphlpapi.GetTcpTable(None, ctypes.byref(size), False)
    for _ in range(3):  # the table can grow between the size probe and the read
        if status != ERROR_INSUFFICIENT_BUFFER or size.value < 4:
            break
        buf = ctypes.create_string_buffer(size.value)
        status = _iphlpapi.GetTcpTable(buf, ctypes.byref(size), False)
        if status == 0:
            count = struct.unpack_from("<I", buf.raw)[0]
            return list((MIB_TCPROW * count).from_buffer_copy(buf.raw, 4))
    if status:
        log.warning("IPv4 TCP table native read returned status %d", status)
    return []


def _delete(row) -> bool:
    row.dwState = MIB_TCP_STATE_DELETE_TCB
    status = _iphlpapi.SetTcpEntry(ctypes.byref(row))
    if status:
        now = time.monotonic()
        if now - _cut_error_at.get(status, float("-inf")) >= 60:
            _cut_error_at[status] = now
            log.warning("IPv4 TCP cut returned native status %d", status)
    return status == 0


def close_to(ips: set[str], rows=None, delete=None) -> list[tuple[str, int]]:
    """Close every established TCP connection to one of `ips`, and say which ones: [(remote ip, port)].
    A loopback or unspecified remote address is skipped whatever `ips` contains - see the note at the top of
    this file. `rows` / `delete` are for the tests."""
    targets = {ip for ip in ips if ":" not in ip and not is_loopback(ip)}
    if not targets:
        return []
    closed = []
    for row in (_tcp_rows() if rows is None else rows):
        if row.dwState != MIB_TCP_STATE_ESTAB:
            continue
        ip = socket.inet_ntoa(struct.pack("<I", row.dwRemoteAddr))
        if is_loopback(ip) or ip not in targets:
            continue
        if (_delete if delete is None else delete)(row):
            closed.append((ip, socket.ntohs(row.dwRemotePort & 0xFFFF)))
    return closed
