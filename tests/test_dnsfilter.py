import socket
import struct
import threading

from blocker import dnsfilter


def query(name: str, qtype: int = 1, qid: int = 0x1234) -> bytes:
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split("."))
    return struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + labels + b"\0" + struct.pack(">HH", qtype, 1)


def test_question_and_blocked_reply():
    msg = query("Bad.Example.com")
    name, qtype, end = dnsfilter.question(msg)
    assert (name, qtype, end) == ("bad.example.com", 1, len(msg))
    reply = dnsfilter.blocked_reply(msg, qtype, end)
    qid, flags, qd, an = struct.unpack(">HHHH", reply[:8])
    assert qid == 0x1234 and flags & 0x8000 and flags & 0x0100 and (qd, an) == (1, 1)
    assert reply.endswith(socket.inet_aton("127.0.0.1"))
    aaaa = query("bad.example.com", qtype=28)
    reply = dnsfilter.blocked_reply(aaaa, 28, len(aaaa))
    assert struct.unpack(">H", reply[6:8])[0] == 0 and reply[12:] == aaaa[12:]   # empty answer
    assert dnsfilter.question(b"\0" * 5) is None


def test_server_blocks_listed_names_and_forwards_the_rest():
    # a fake "network DNS server" that answers every query with its own marker
    upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    upstream.bind(("127.0.0.1", 0))

    def serve():
        while True:
            msg, addr = upstream.recvfrom(512)
            upstream.sendto(msg[:2] + b"\x81\x80" + msg[4:] + b"UPSTREAM", addr)
    threading.Thread(target=serve, daemon=True).start()

    server = dnsfilter.Server(lambda name: name.endswith("bad.test"), listen=[("127.0.0.1", socket.AF_INET)], port=0)
    server.upstreams, server.upstream_port = ["127.0.0.1"], upstream.getsockname()[1]
    assert server.answer(query("www.bad.test")).endswith(socket.inet_aton("127.0.0.1"))
    assert server.answer(query("good.test")).endswith(b"UPSTREAM")
    server.upstreams = ["127.0.0.1"]
    server.upstream_port = 9   # nothing listens there: SERVFAIL
    reply = server.answer(query("good.test"))
    assert struct.unpack(">H", reply[2:4])[0] & 0xF == 2


def test_plan_points_adapters_at_the_filter_and_keeps_originals():
    current = {
        "{a}": {"v4": "", "dhcp": "192.168.1.1", "v6": ""},                       # automatic DNS
        "{b}": {"v4": "1.1.1.1,8.8.8.8", "dhcp": "", "v6": "2606:4700::1111"},     # own DNS servers
        "{c}": {"v4": "", "dhcp": "", "v6": ""},                                   # no DNS: left alone
    }
    saved, changes, upstreams = dnsfilter.plan(current, {})
    assert saved["{a}"] == {"v4": "", "v6": ""} and saved["{b}"] == {"v4": "1.1.1.1,8.8.8.8", "v6": "2606:4700::1111"}
    assert ("{a}", "127.0.0.1,192.168.1.1", False) in changes and ("{a}", "::1", True) in changes
    assert ("{b}", "127.0.0.1,1.1.1.1,8.8.8.8", False) in changes
    assert not [c for c in changes if c[0] == "{c}"]
    assert upstreams == ["192.168.1.1", "1.1.1.1", "8.8.8.8"]

    # next check: already pointed at the filter -> nothing to do, originals kept
    current["{a}"] = {"v4": "127.0.0.1,192.168.1.1", "dhcp": "192.168.1.1", "v6": "::1"}
    current["{b}"] = {"v4": "127.0.0.1,1.1.1.1,8.8.8.8", "dhcp": "", "v6": "::1"}
    saved2, changes, _ = dnsfilter.plan(current, saved)
    assert saved2 == saved and changes == []

    # a new network (different DHCP DNS) on the same adapter: follows it
    current["{a}"]["dhcp"] = "10.0.0.1"
    _, changes, upstreams = dnsfilter.plan(current, saved)
    assert changes == [("{a}", "127.0.0.1,10.0.0.1", False)] and upstreams[0] == "10.0.0.1"


def test_set_dns_signature_is_accepted():
    # without admin rights Windows refuses (access denied) - but it must not be "invalid parameter" (87)
    assert dnsfilter.set_dns("{00000000-0000-0000-0000-000000000000}", "") != 87
