"""Blocked-visit listener.

The hosts file sends blocked sites to 127.0.0.1, so a browser trying to open one connects here.
We read which hostname it wanted (HTTP Host header / HTTPS SNI), report it, and close the connection.
"""
import socket
import struct
import threading

READ_TIMEOUT_SEC = 3
MAX_READ = 32 * 1024


def parse_sni(data: bytes) -> str | None:
    """Server name from a TLS ClientHello record, or None."""
    try:
        if data[0] != 0x16 or data[5] != 0x01:  # handshake record / ClientHello
            return None
        pos = 5 + 4 + 2 + 32                     # record hdr, handshake hdr, version, random
        pos += 1 + data[pos]                     # session id
        pos += 2 + struct.unpack_from(">H", data, pos)[0]   # cipher suites
        pos += 1 + data[pos]                     # compression methods
        end = pos + 2 + struct.unpack_from(">H", data, pos)[0]
        pos += 2
        while pos + 4 <= end:
            ext_type, ext_len = struct.unpack_from(">HH", data, pos)
            pos += 4
            if ext_type == 0:                    # server_name
                name_len = struct.unpack_from(">H", data, pos + 3)[0]
                return data[pos + 5:pos + 5 + name_len].decode("ascii").lower()
            pos += ext_len
    except (IndexError, struct.error, UnicodeDecodeError):
        pass
    return None


def parse_http_host(data: bytes) -> str | None:
    for line in data.split(b"\r\n")[1:]:
        if line.lower().startswith(b"host:"):
            return line[5:].strip().split(b":")[0].decode("ascii", "replace").lower()
    return None


def _read_request(conn: socket.socket, tls: bool) -> bytes:
    data = b""
    while len(data) < MAX_READ:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
        if tls and len(data) >= 5 and len(data) >= 5 + struct.unpack_from(">H", data, 3)[0]:
            break
        if not tls and b"\r\n\r\n" in data:
            break
    return data


class BlockListener:
    """Listens on 127.0.0.1:80 and :443; calls on_visit(hostname) for each attempt (from worker threads)."""

    def __init__(self, on_visit, log):
        self.on_visit = on_visit
        self.log = log

    def start(self):
        for port in (80, 443):
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                srv.bind(("127.0.0.1", port))
                srv.listen(64)
            except OSError as e:
                self.log.warning("Blocked-visit listener: port %d unavailable (%s)", port, e)
                continue
            threading.Thread(target=self._accept_loop, args=(srv, port == 443), daemon=True).start()
            self.log.info("Blocked-visit listener on 127.0.0.1:%d", port)

    def _accept_loop(self, srv: socket.socket, tls: bool):
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._handle, args=(conn, tls), daemon=True).start()

    def _handle(self, conn: socket.socket, tls: bool):
        try:
            conn.settimeout(READ_TIMEOUT_SEC)
            data = _read_request(conn, tls)
            host = parse_sni(data) if tls else parse_http_host(data)
            if host:
                self.on_visit(host)
        except Exception:
            self.log.exception("Blocked-visit listener error")
        finally:
            conn.close()
