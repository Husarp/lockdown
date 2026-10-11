"""The local asset server answers small files at once on a keep-alive connection (no Nagle / delayed-ACK stall of
~40 ms per response: headers and body go out in one write, TCP_NODELAY on)."""
import http.client
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from resident.assets import AssetServer  # noqa: E402


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "ui"
    root.mkdir()
    for i in range(20):
        (root / f"f{i}.js").write_text(f"export const a{i} = {i};")
    srv = AssetServer(root)
    srv.start()
    yield srv
    srv.stop()


def test_twenty_small_files_over_one_keep_alive_connection_do_not_wait_for_acks(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.httpd.server_address[1], timeout=5)
    started = time.perf_counter()
    for i in range(20):
        conn.request("GET", f"/f{i}.js")
        r = conn.getresponse()
        assert r.status == 200 and r.read() == f"export const a{i} = {i};".encode()
    elapsed = time.perf_counter() - started
    conn.close()
    assert elapsed < 0.25, f"20 files took {elapsed * 1000:.0f} ms (a 40 ms stall each would be 800 ms)"


def test_headers_and_body_arrive_together(server):
    s = socket.create_connection(("127.0.0.1", server.httpd.server_address[1]), timeout=5)
    s.sendall(b"GET /f1.js HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    time.sleep(0.15)        # everything the server wrote is here by now
    data = s.recv(65536)
    s.close()
    head, _, body = data.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200") and body == b"export const a1 = 1;"


def test_a_head_request_sends_no_body(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.httpd.server_address[1], timeout=5)
    conn.request("HEAD", "/f2.js")
    r = conn.getresponse()
    assert r.status == 200 and r.read() == b"" and int(r.getheader("Content-Length")) > 0
    conn.close()
