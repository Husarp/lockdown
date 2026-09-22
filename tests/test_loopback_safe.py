"""Blocking a site must never cut local connections.

Reported 2026-09-23: lockdown.log repeated "Closed 10 open connections to blocked sites" every ~2s forever,
right after "Hosts file updated". Anything on a loopback socket broke - Gradle died with "client disconnection
detected, canceling the build", and a local server on 127.0.0.1:42017 could not be reached from the same
machine.

The hosts file points blocked domains at 127.0.0.1, and Windows loads the hosts file into its DNS cache.
cut_live_connections reads that cache back to match open connections to names, so every blocked name answered
"127.0.0.1" and loopback went onto the kill list. The hosts entry already stops the real traffic, so closing a
loopback socket never achieves anything - it only breaks unrelated software."""
import socket
import struct
import threading

import pytest

from blocker.connections import MIB_TCP_STATE_ESTAB, MIB_TCPROW, _tcp_rows, close_to, is_loopback

HELD_TICKS = 15        # the service closes connections every ~2s, so this is 30 seconds of it


@pytest.mark.parametrize("ip, local", [
    ("127.0.0.1", True), ("127.0.0.53", True), ("127.255.255.254", True),
    ("0.0.0.0", True), ("::1", True), ("::", True),
    ("not an ip", True), ("", True),                      # unparseable: leave it alone
    ("8.8.8.8", False), ("142.250.187.206", False), ("192.168.1.10", False),
])
def test_what_counts_as_untouchable(ip, local):
    assert is_loopback(ip) is local


def _is_established(local_port: int) -> bool:
    """Is our loopback connection still up, according to the same table the sweep walks?"""
    wanted = socket.htons(local_port)
    return any(r.dwState == MIB_TCP_STATE_ESTAB and (r.dwLocalPort & 0xFFFF) == wanted for r in _tcp_rows())


def _row(remote_ip: str, port: int = 443, state: int = MIB_TCP_STATE_ESTAB) -> MIB_TCPROW:
    return MIB_TCPROW(dwState=state, dwLocalAddr=0, dwLocalPort=0,
                      dwRemoteAddr=struct.unpack("<I", socket.inet_aton(remote_ip))[0],
                      dwRemotePort=socket.htons(port))


def test_loopback_is_never_closed_even_when_asked_to():
    """The exact failure: the DNS cache said 127.0.0.1 was a blocked site."""
    killed = []
    rows = [_row("127.0.0.1", 42017), _row("127.0.0.1", 5037), _row("127.9.9.9", 8080)]
    closed = close_to({"127.0.0.1", "127.9.9.9", "0.0.0.0"}, rows=rows,
                      delete=lambda r: killed.append(r) or True)
    assert closed == [] and killed == []


def test_a_real_blocked_address_is_still_closed():
    killed = []
    rows = [_row("142.250.187.206", 443), _row("127.0.0.1", 42017)]
    closed = close_to({"142.250.187.206", "127.0.0.1"}, rows=rows,
                      delete=lambda r: killed.append(r) or True)
    assert closed == [("142.250.187.206", 443)]           # and the port is reported, for the log
    assert len(killed) == 1


def test_nothing_is_attempted_when_only_loopback_is_listed(monkeypatch):
    """Guard the whole sweep, not just each row: with nothing real to close it must not even read the table."""
    def boom():
        raise AssertionError("read the TCP table for a loopback-only kill list")

    monkeypatch.setattr("blocker.connections._tcp_rows", boom)
    assert close_to({"127.0.0.1", "::1", "0.0.0.0"}) == []


def test_connections_that_are_not_established_are_left_alone():
    rows = [_row("142.250.187.206", 443, state=MIB_TCP_STATE_ESTAB + 1)]
    assert close_to({"142.250.187.206"}, rows=rows, delete=lambda r: True) == []


def test_a_local_server_stays_reachable_while_a_block_is_on():
    """End to end, with real sockets: a client holds a connection to a local server through 30 seconds' worth
    of the service's closing sweeps, with the blocked site resolving to 127.0.0.1 exactly as the hosts file
    makes it. This is the Gradle / local-server case from the report."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    accepted = []
    threading.Thread(target=lambda: accepted.append(server.accept()[0]), daemon=True).start()

    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        for _ in range(200):                     # wait for the accept to land
            if accepted:
                break
            threading.Event().wait(0.01)
        assert accepted, "the local server never accepted the connection"

        # What the hosts file makes the DNS cache say: every blocked name is at 127.0.0.1. The TEST-NET-3
        # address (RFC 5737, never routed) is in the list so the sweep really does walk the TCP table -
        # a loopback-only list returns before reading it, which would make this prove nothing.
        kill_list = {"127.0.0.1", "0.0.0.0", "203.0.113.5"}
        for _ in range(HELD_TICKS):
            assert close_to(kill_list) == [], "the sweep closed a loopback connection"

        # The connection is still established - checked against the TCP table itself, which is what the
        # sweep reads, rather than by sending bytes (a data round-trip under load made this flaky).
        assert _is_established(port), "the local connection was torn down by the closing sweep"

        accepted[0].settimeout(5)
        client.sendall(b"still here")
        assert accepted[0].recv(32) == b"still here"
    finally:
        client.close()
        if accepted:
            accepted[0].close()
        server.close()
