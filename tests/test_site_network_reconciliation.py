import json
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

import monitor
import service
from blocker import connections, dnsfilter, firewall, hosts, netlog
from db import Database

NOW = datetime(2026, 11, 24, 21)
CHROME = r"C:\Chrome\chrome.exe"
CHROME_OTHER = r"D:\Chrome\chrome.exe"
CHROME_NEW = r"C:\Chrome-2\chrome.exe"
ZEN = r"E:\Zen\zen.exe"
VIDEO4, VIDEO6 = "192.0.2.10", "2001:db8::10"


class NativeFirewall:
    def __init__(self, monkeypatch):
        self.rules = {}
        self.commands = []
        self.fail = set()
        monkeypatch.setattr(firewall, "_native_rules", lambda: list(self.rules.values()))
        monkeypatch.setattr(firewall, "_netsh", self.netsh)

    def netsh(self, action, _rule, *arguments):
        args = dict(arg.split("=", 1) for arg in arguments)
        name = args["name"].casefold()
        self.commands.append((action, name))
        if (action, name) in self.fail:
            return False
        if action == "delete":
            self.rules.pop(name, None)
        else:
            self.add_native(name, args.get("program"), args.get("remoteip", "*"), args["description"])
        return True

    def add_native(self, name, path=None, ips="*", description=""):
        self.rules[name.casefold()] = SimpleNamespace(
            Name=name, ApplicationName=path or "", RemoteAddresses=ips, LocalAddresses="*",
            Description=description, Direction=2, Action=0, Enabled=True, Profiles=0x7fffffff,
            ServiceName="", InterfaceTypes="All", Protocol=17 if path else 256,
            RemotePorts="443" if path else "", LocalPorts="*")


@pytest.fixture
def native(monkeypatch):
    return NativeFirewall(monkeypatch)


@pytest.fixture
def enforcer(tmp_path, monkeypatch, native):
    e = service.Enforcer(Database(tmp_path / "rules.db"))
    tick = SimpleNamespace(now=0.0)
    monkeypatch.setattr(service.time, "monotonic", lambda: tick.now)
    catalog = SimpleNamespace(identify=lambda exe, path: exe.lower() in {"chrome.exe", "zen.exe"})
    monkeypatch.setattr(monitor, "browser_catalog", catalog, raising=False)
    return e, tick


def test_quic_discovery_migrates_legacy_and_handles_multiple_paths(enforcer, native, monkeypatch):
    e, tick = enforcer
    e.db.set_setting(service.QUIC_KEY, json.dumps({"chrome.exe": CHROME}))
    e._load_site_transport()
    old = firewall.QUIC_PREFIX + "chrome.exe"
    native.add_native(old, CHROME)
    procs = {1: ("chrome.exe", CHROME), 2: ("chrome.exe", CHROME_OTHER), 3: ("zen.exe", ZEN),
             4: ("embed.exe", r"C:\Embed\embed.exe")}
    monkeypatch.setattr(service.apps, "list_processes", lambda: [(p, value[0]) for p, value in procs.items()])
    monkeypatch.setattr(service.apps, "process_path", lambda p: procs[p][1])
    monkeypatch.setattr(service.apps, "is_file", lambda path: False)

    e.update_quic(True)
    assert {rule.target for rule in e.quic_state.installed.values()} == {
        firewall.program_path(path) for path in (CHROME, CHROME_OTHER, ZEN)}
    assert old.casefold() not in native.rules
    assert not json.loads(e.db.get_setting(service.QUIC_KEY))["legacy"]

    other = firewall.quic_rule(CHROME_OTHER).name.casefold()
    native.rules.pop(other)  # native loss with equal desired paths
    tick.now += 11
    e.update_quic(True)
    assert other in native.rules

    native.rules[other].ApplicationName = CHROME
    tick.now += 11
    e.update_quic(True)
    assert native.rules[other].ApplicationName == firewall.program_path(CHROME_OTHER)

    del procs[2]
    procs[5] = ("chrome.exe", CHROME_NEW)
    new = firewall.quic_rule(CHROME_NEW).name.casefold()
    native.fail.add(("add", new))
    e.update_quic(True)
    assert other in native.rules and new not in native.rules
    assert other in e.quic_state.pending_removal
    commands = list(native.commands)
    e.update_quic(True)
    assert native.commands == commands

    native.fail.clear()
    tick.now += 2
    e.update_quic(True)
    assert native.commands.index(("delete", other), len(commands)) > native.commands.index(("add", new), len(commands))
    assert other not in native.rules and new in native.rules


def test_video_replacement_retry_and_partial_teardown(enforcer, native):
    e, tick = enforcer
    e.dns_blocks = {"googlevideo.com": {}, "nflxvideo.net": {}}
    e.note_video(VIDEO4, "a.googlevideo.com")
    e.note_video(VIDEO6, "a.nflxvideo.net")
    old = firewall.video_rule([VIDEO4, VIDEO6]).name.casefold()
    new = firewall.video_rule([VIDEO6]).name.casefold()
    e.update_video_block()
    assert set(e.video_state.installed) == {old}
    assert native.rules[old].RemoteAddresses == f"{VIDEO4},{VIDEO6}"

    e.dns_blocks = {"nflxvideo.net": {}}            # reset releases only YouTube
    native.fail.add(("add", new))
    e.update_video_block()
    assert set(e.video_state.installed) == {old}
    assert set(e.video_state.desired) == {new}
    assert e.video_state.pending_removal == {old}

    native.fail = {("delete", old)}
    tick.now += 2
    e.update_video_block()
    assert set(e.video_state.installed) == {old, new}
    assert e.video_state.pending_removal == {old}

    # Restart can find the still-present old rule even though only Netflix evidence was persisted.
    e = service.Enforcer(e.db)
    e.dns_blocks = {"nflxvideo.net": {}}
    e.update_video_block()
    assert e.video_state.pending_removal == {old}
    e.dns_blocks = {}
    e.update_video_block()
    assert new not in native.rules and old in native.rules
    assert e.video_state.pending_removal == {old}

    native.fail.clear()
    tick.now += 2
    e.update_video_block()
    assert not native.rules and not e.video_state.pending_removal


def test_foreign_rule_and_unconfirmed_legacy_are_never_removed(enforcer, native, monkeypatch):
    e, _tick = enforcer
    legacy = firewall.QUIC_PREFIX + "chrome.exe"
    native.add_native(legacy, CHROME_OTHER)  # same old display name, different path
    e.db.set_setting(service.QUIC_KEY, json.dumps({"chrome.exe": CHROME}))
    e._load_site_transport()
    quic = firewall.quic_rule(CHROME).name.casefold()
    native.add_native(quic, CHROME, description="company policy")
    monkeypatch.setattr(service.apps, "list_processes", lambda: [(1, "chrome.exe")])
    monkeypatch.setattr(service.apps, "process_path", lambda pid: CHROME)
    e.update_quic(True)
    e.update_quic(False)
    assert set(native.rules) == {legacy.casefold(), quic}
    assert not native.commands


def test_cache_failure_preserves_remembered_cdn_and_never_cuts_shared_or_loopback(enforcer, monkeypatch, caplog):
    e, _tick = enforcer
    e.dns_blocks = {"youtube.com": {}, "googlevideo.com": {}}
    shared = "142.250.0.8"
    e.net_names = {VIDEO4: "a.googlevideo.com", VIDEO6: "a.googlevideo.com", shared: "www.youtube.com"}

    def unavailable():
        raise OSError("DNS API unavailable")

    monkeypatch.setattr(netlog, "dns_names", unavailable)
    with caplog.at_level("INFO", logger=service.log.name):
        e.close_cached(e.dns_blocks, NOW)
    assert "cache_read_exception:OSError" in caplog.text
    assert set(e.closing) == {VIDEO4}
    e.update_video_block()
    assert set(e.video_ips) == {VIDEO4, VIDEO6}

    names = netlog.DnsNames({
        "a.googlevideo.com": ([shared], []),
        "www.youtube.com": ([shared], []),
        "googlevideo.com": (["127.0.0.1", "::ffff:127.1.2.3"], []),
    })
    monkeypatch.setattr(netlog, "dns_names", lambda: names)
    e.cut_live_connections(NOW)
    e.update_video_block()
    assert shared not in e.closing and shared not in e.video_ips
    assert not any(connections.is_loopback(ip) for ip in e.closing)


def test_flush_nonzero_status_is_retried(enforcer, monkeypatch):
    e, tick = enforcer
    updates = iter((True, False, False))
    statuses = iter((1, 0))
    monkeypatch.setattr(hosts, "apply", lambda names: next(updates))
    monkeypatch.setattr(hosts.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, next(statuses)))
    e.update_hosts()
    assert e.flush_at == 2
    tick.now = 1
    e.update_hosts()
    assert e.flush_at == 2
    tick.now = 2
    e.update_hosts()
    assert e.flush_at is None


def test_dns_bind_retry_restores_before_pointing_at_listening_filter(enforcer, tmp_path, monkeypatch):
    e, tick = enforcer
    e.dns_blocks = {"googlevideo.com": {}}
    events = []
    monkeypatch.setattr(Database.__init__, "__defaults__", (tmp_path / "rules.db", False))
    monkeypatch.setattr(e.protection, "refresh", lambda cfg: False)

    class Filter:
        def __init__(self, blocked, log, safe):
            self.upstreams = []

        def start(self):
            events.append("bind")
            if events.count("bind") == 1:
                raise OSError("port 53 already held")
            events.append("listening")

        def close(self):
            events.append("close")

    class Stop:
        def is_set(self):
            return tick.now >= 4

        def wait(self, seconds):
            tick.now += seconds

    def point(db, log, ready):
        assert events[-1] == "listening"
        events.append("point")
        ready(["192.0.2.53"])
        return ["192.0.2.53"]

    monkeypatch.setattr(dnsfilter, "Server", Filter)
    monkeypatch.setattr(dnsfilter, "restore", lambda *a: events.append("restore"))
    monkeypatch.setattr(dnsfilter, "point_to_filter", point)
    service.dns_loop(e, Stop())
    assert events == ["bind", "close", "restore", "bind", "listening", "point", "restore", "close"]


def test_service_counting_calls_the_shared_quota_hook(enforcer, monkeypatch):
    e, _tick = enforcer
    calls = []
    fake = SimpleNamespace(count_unwatched_apps=lambda obj, now: calls.append((obj, now)))
    monkeypatch.setitem(sys.modules, "quota_accounting", fake)
    e.count_unwatched_apps(NOW)
    assert calls == [(e, NOW)]


def test_native_success_without_a_rule_is_not_applied(enforcer, native, monkeypatch):
    e, tick = enforcer
    e.dns_blocks = {"googlevideo.com": {}}
    e.note_video(VIDEO4, "a.googlevideo.com")
    install = firewall.set_video_block
    monkeypatch.setattr(firewall, "set_video_block", lambda ips: True)  # command says yes, store says no
    e.update_video_block()
    assert not e.video_state.installed and e.video_ips == {VIDEO4: "a.googlevideo.com"}
    monkeypatch.setattr(firewall, "set_video_block", install)
    tick.now += 2
    e.update_video_block()
    assert len(e.video_state.installed) == 1



def test_preflight_timeout_still_enforces_and_reset_during_wait_releases(enforcer, monkeypatch, tmp_path):
    e, _ = enforcer
    item = e.db.add_item("Video", ["youtube.com", "googlevideo.com"], "site",
                         rules=[{"rule_type": "permanent"}])
    e.update_clock = lambda: NOW
    e.count_unwatched_apps = lambda now: None
    e.app_items = {}
    monkeypatch.setattr(service.apps, "list_processes", lambda: [])
    monkeypatch.setattr(service.browser_policy, "apply", lambda **kw: False)
    monkeypatch.setattr(service.hosts, "flush_dns", lambda: True)
    monkeypatch.setattr(netlog, "dns_names", lambda: {})
    system_hosts = tmp_path / "hosts"
    system_hosts.write_text("127.0.0.1 localhost\n")
    apply = service.hosts.apply
    monkeypatch.setattr(service.hosts, "apply", lambda names: apply(names, system_hosts, tmp_path / "hosts.bak"))

    def release_during_preflight(hosts, **kwargs):
        e.db.remove_item(item)
        return service.pause_request.Receipt("released", ("no_agent",))

    monkeypatch.setattr(service.pause_request, "request", release_during_preflight)
    e.enforce_once()
    assert not e.dns_blocks and "youtube.com" not in system_hosts.read_text()

    e.db.add_item("Video", ["youtube.com", "googlevideo.com"], "site", rules=[{"rule_type": "permanent"}])
    monkeypatch.setattr(service.pause_request, "request",
                        lambda hosts, **kwargs: service.pause_request.Receipt("absent", ("no_agent",)))
    e.enforce_once()
    assert e.blocked_name("youtube.com") == "blocked"
    assert "youtube.com" in system_hosts.read_text()


def test_direct_ipv4_lookup_uses_one_budget_and_excludes_shared_page_aliases(enforcer, monkeypatch):
    e, tick = enforcer
    shared = "142.250.0.8"
    names = netlog.DnsNames({"www.youtube.com": ([shared], [])})
    monkeypatch.setattr(netlog, "dns_names", lambda: names)
    monkeypatch.setattr(service.sys, "platform", "win32")
    calls = []

    def resolve(hosts, *, deadline):
        calls.append((hosts, deadline))
        return {VIDEO4, shared, "127.0.0.1"}

    monkeypatch.setattr(connections, "resolve", resolve)
    e.close_cached(["example.org", "youtube.com", "googlevideo.com"], NOW)
    assert calls == [(["example.org", "www.example.org"], tick.now + service.ENDPOINT_LOOKUP_SEC)]
    assert set(e.closing) == {VIDEO4}
