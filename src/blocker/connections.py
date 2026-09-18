"""Close already-open TCP connections to blocked sites (so an open tab can't keep using them).

Uses the Win32 IP Helper API: GetTcpTable + SetTcpEntry(DELETE_TCB). IPv4 only; needs admin.
"""
import ctypes
import socket
import struct
from ctypes import wintypes

MIB_TCP_STATE_ESTAB = 5
MIB_TCP_STATE_DELETE_TCB = 12
ERROR_INSUFFICIENT_BUFFER = 122


class MIB_TCPROW(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD), ("dwLocalPort", wintypes.DWORD),
                ("dwRemoteAddr", wintypes.DWORD), ("dwRemotePort", wintypes.DWORD)]


_iphlpapi = ctypes.WinDLL("iphlpapi")


def resolve(hostnames: list[str]) -> set[str]:
    """Real IPv4 addresses of the hostnames. Must run BEFORE they are added to the hosts file."""
    ips = set()
    for h in hostnames:
        try:
            ips.update(info[4][0] for info in socket.getaddrinfo(h, 443, socket.AF_INET, socket.SOCK_STREAM))
        except OSError:
            pass
    return {ip for ip in ips if not ip.startswith("127.") and ip != "0.0.0.0"}


def _tcp_rows() -> list[MIB_TCPROW]:
    size = wintypes.DWORD(0)
    _iphlpapi.GetTcpTable(None, ctypes.byref(size), False)
    buf = ctypes.create_string_buffer(size.value)
    if _iphlpapi.GetTcpTable(buf, ctypes.byref(size), False) != 0:
        return []
    count = struct.unpack_from("<I", buf.raw, 0)[0]
    rows_type = MIB_TCPROW * count
    return list(rows_type.from_buffer_copy(buf.raw, 4))


def close_to(ips: set[str]) -> int:
    """Close every established TCP connection to one of `ips`. Returns how many were closed."""
    closed = 0
    for row in _tcp_rows():
        if row.dwState != MIB_TCP_STATE_ESTAB:
            continue
        if socket.inet_ntoa(struct.pack("<I", row.dwRemoteAddr)) in ips:
            row.dwState = MIB_TCP_STATE_DELETE_TCB
            if _iphlpapi.SetTcpEntry(ctypes.byref(row)) == 0:
                closed += 1
    return closed
