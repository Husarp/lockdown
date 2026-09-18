import ssl

from blocker import connections
from blocker.listener import parse_http_host, parse_sni


def client_hello(server_name: str) -> bytes:
    """A real TLS ClientHello, as a browser would send it."""
    ctx = ssl.create_default_context()
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = ctx.wrap_bio(incoming, outgoing, server_hostname=server_name)
    try:
        tls.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


def test_parse_sni():
    assert parse_sni(client_hello("www.reddit.com")) == "www.reddit.com"


def test_parse_sni_garbage():
    assert parse_sni(b"") is None
    assert parse_sni(b"GET / HTTP/1.1\r\n\r\n") is None
    assert parse_sni(client_hello("x.com")[:40]) is None


def test_parse_http_host():
    req = b"GET /r/python HTTP/1.1\r\nUser-Agent: x\r\nHost: Old.Reddit.com:80\r\n\r\n"
    assert parse_http_host(req) == "old.reddit.com"
    assert parse_http_host(b"GET / HTTP/1.1\r\n\r\n") is None


def test_resolve_skips_loopback():
    assert connections.resolve(["localhost"]) == set()


def test_tcp_table_readable():
    rows = connections._tcp_rows()
    assert isinstance(rows, list)
