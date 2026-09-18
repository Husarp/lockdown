"""Network log, service side: which app is connected to which site (standard library only).

- Connections: the TCP tables of Windows (GetExtendedTcpTable, IPv4 + IPv6), with the owning process.
- Names: the Windows DNS cache - the name an app actually looked up, not a reverse lookup (which only gives
  meaningless server names). DnsGetCacheDataTable lists the cached names, DnsQuery (cache only, no network)
  gives their addresses.
"""
import ctypes
import ipaddress
import socket
from ctypes import wintypes

AF_INET, AF_INET6 = 2, 23
TCP_TABLE_OWNER_PID_ALL = 5
STATE_LISTEN = 2
DNS_TYPE_A, DNS_TYPE_CNAME, DNS_TYPE_AAAA = 1, 5, 28
DNS_QUERY_NO_WIRE_QUERY = 0x10
DNS_FREE_RECORD_LIST = 1


class MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD), ("dwLocalPort", wintypes.DWORD),
                ("dwRemoteAddr", wintypes.DWORD), ("dwRemotePort", wintypes.DWORD), ("dwOwningPid", wintypes.DWORD)]


class MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("ucLocalAddr", ctypes.c_ubyte * 16), ("dwLocalScopeId", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD), ("ucRemoteAddr", ctypes.c_ubyte * 16),
                ("dwRemoteScopeId", wintypes.DWORD), ("dwRemotePort", wintypes.DWORD), ("dwState", wintypes.DWORD),
                ("dwOwningPid", wintypes.DWORD)]


class DNS_CACHE_ENTRY(ctypes.Structure):
    pass


DNS_CACHE_ENTRY._fields_ = [("pNext", ctypes.POINTER(DNS_CACHE_ENTRY)), ("pszName", ctypes.c_wchar_p),
                            ("wType", ctypes.c_ushort), ("wDataLength", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong)]


class _DnsData(ctypes.Union):
    _fields_ = [("IpAddress", wintypes.DWORD), ("Ip6Address", ctypes.c_ubyte * 16), ("pNameHost", ctypes.c_wchar_p)]


class DNS_RECORD(ctypes.Structure):
    pass


DNS_RECORD._fields_ = [("pNext", ctypes.POINTER(DNS_RECORD)), ("pName", ctypes.c_wchar_p), ("wType", wintypes.WORD),
                       ("wDataLength", wintypes.WORD), ("Flags", wintypes.DWORD), ("dwTtl", wintypes.DWORD),
                       ("dwReserved", wintypes.DWORD), ("Data", _DnsData)]

_iphlpapi = ctypes.WinDLL("iphlpapi")
_dnsapi = ctypes.WinDLL("dnsapi")
_dnsapi.DnsQuery_W.argtypes = [wintypes.LPCWSTR, wintypes.WORD, wintypes.DWORD, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.POINTER(DNS_RECORD)), ctypes.c_void_p]
_dnsapi.DnsRecordListFree.argtypes = [ctypes.POINTER(DNS_RECORD), ctypes.c_int]
_dnsapi.DnsGetCacheDataTable.argtypes = [ctypes.POINTER(ctypes.POINTER(DNS_CACHE_ENTRY))]


def _table(family: int, row_type):
    size = wintypes.DWORD(0)
    _iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_ALL, 0)
    for _ in range(3):   # the table can grow between the two calls
        buf = ctypes.create_string_buffer(size.value + 4096)
        size = wintypes.DWORD(len(buf))
        if _iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_ALL, 0) == 0:
            count = ctypes.c_uint32.from_buffer_copy(buf.raw[:4]).value
            return (row_type * count).from_buffer_copy(buf.raw, 4)   # after the DWORD entry count
    return []


def tcp_connections() -> list[tuple[int, str, int, int]]:
    """(pid, remote ip, remote port, local port) of every TCP connection to somewhere (not listening sockets)."""
    out = []
    for row in _table(AF_INET, MIB_TCPROW_OWNER_PID):
        if row.dwState != STATE_LISTEN and row.dwRemoteAddr:
            out.append((row.dwOwningPid, socket.inet_ntop(socket.AF_INET, row.dwRemoteAddr.to_bytes(4, "little")),
                        socket.ntohs(row.dwRemotePort & 0xFFFF), socket.ntohs(row.dwLocalPort & 0xFFFF)))
    for row in _table(AF_INET6, MIB_TCP6ROW_OWNER_PID):
        remote = bytes(row.ucRemoteAddr)
        if row.dwState != STATE_LISTEN and any(remote):
            out.append((row.dwOwningPid, socket.inet_ntop(socket.AF_INET6, remote),
                        socket.ntohs(row.dwRemotePort & 0xFFFF), socket.ntohs(row.dwLocalPort & 0xFFFF)))
    return out


def is_local(ip: str) -> bool:
    """This PC or the home / local network."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified


# ---------- DNS cache ----------

def cached_names() -> list[str]:
    head = ctypes.POINTER(DNS_CACHE_ENTRY)()
    if not _dnsapi.DnsGetCacheDataTable(ctypes.byref(head)):
        return []
    names, node = [], head
    while node:
        if node.contents.pszName:
            names.append(node.contents.pszName.lower())
        node = node.contents.pNext
    return list(dict.fromkeys(names))


def cached_answers(name: str) -> tuple[list[str], list[str]]:
    """(addresses, CNAME targets) the cache holds for a name - never asks the network."""
    ips, targets = [], []
    for qtype in (DNS_TYPE_A, DNS_TYPE_AAAA):
        result = ctypes.POINTER(DNS_RECORD)()
        if _dnsapi.DnsQuery_W(name, qtype, DNS_QUERY_NO_WIRE_QUERY, None, ctypes.byref(result), None) != 0:
            continue
        node = result
        while node:
            rec = node.contents
            if rec.wType == DNS_TYPE_A:
                ips.append(socket.inet_ntop(socket.AF_INET, rec.Data.IpAddress.to_bytes(4, "little")))
            elif rec.wType == DNS_TYPE_AAAA:
                ips.append(socket.inet_ntop(socket.AF_INET6, bytes(rec.Data.Ip6Address)))
            elif rec.wType == DNS_TYPE_CNAME and rec.Data.pNameHost:
                targets.append(rec.Data.pNameHost.lower())
            node = rec.pNext
        _dnsapi.DnsRecordListFree(result, DNS_FREE_RECORD_LIST)
    return ips, targets


def names_for_ips(answers: dict[str, tuple[list[str], list[str]]]) -> dict[str, str]:
    """ip -> the name to show. When www.youtube.com is a CNAME for youtube-ui.l.google.com, both are cached with
    the same addresses; the name you asked for (not a CNAME target) wins."""
    targets = {t for _, cnames in answers.values() for t in cnames}
    out: dict[str, str] = {}
    for name, (ips, _) in answers.items():
        for ip in ips:
            if ip not in out or (out[ip] in targets and name not in targets):
                out[ip] = name
    return out


def dns_names() -> dict[str, str]:
    """ip -> name, from everything in the DNS cache right now."""
    return names_for_ips({name: cached_answers(name) for name in cached_names()})
