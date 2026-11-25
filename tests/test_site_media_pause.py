import asyncio
import queue
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

import service
from blocker import pause_request
from db import Database
from monitor import win, word_guard
from resident import site_media

NOW = datetime(2026, 11, 24, 21)
HOST = "youtube.com"
CDN = "googlevideo.com"
OFF = {"enabled": False, "action": "close"}


class Session:
    def __init__(self, source="Chrome", title="Cats", status="playing", can_pause=True, events=None):
        self.source_app_user_model_id = source
        self.title, self.status, self.can_pause = title, status, can_pause
        self.events = [] if events is None else events
        self.result = True

    def get_playback_info(self):
        return SimpleNamespace(playback_status=self.status, controls=SimpleNamespace(is_pause_enabled=self.can_pause))

    async def try_get_media_properties_async(self):
        return SimpleNamespace(title=self.title, artist="Channel", album_title="")

    async def try_pause_async(self):
        self.events.append("pause_start")
        if isinstance(self.result, BaseException):
            raise self.result
        if self.result is None:
            await asyncio.Future()
        if self.result:
            self.status = "paused"
        self.events.append("pause_complete")
        return self.result


def observation(now, hwnd=5, tab="cats", product="Chrome", title="Cats - YouTube - Google Chrome",
                host=HOST, url="https://youtube.com/watch?v=cats", source="uia", role="foreground"):
    return SimpleNamespace(hwnd=hwnd, pid=100 + hwnd, product=product, title=title, host=host, url=url,
                           tab_token=tab, observed_at=now, source=source, role=role, bar_hidden=source != "uia",
                           actionable=bool(host and source == "uia"), process_started_at=0)


class World:
    def __init__(self):
        self.now = 10.0
        self.observations = [observation(self.now)]
        self.sessions = [Session()]
        self.live = {5, 6, 7}
        self.blocks = {HOST: {"dns", "close"}}
        self.notes = []
        self.hub = SimpleNamespace(win=SimpleNamespace(window_pid=lambda hwnd: 100 + hwnd if hwnd in self.live else 0))
        self.worker = site_media.SiteMediaWorker(self.hub, manager=self.manager, recent=lambda _: self.observations,
                                                clock=lambda: self.now, rules=lambda _: self.blocks,
                                                scan=False, trace=self.notes.append)

    async def manager(self):
        return SimpleNamespace(get_sessions=lambda: self.sessions)

    def step(self, action="watch", obs=None):
        job = site_media._Job(frozenset({HOST}), self.now + 0.5, action, obs)
        asyncio.run(self.worker._evaluate(None, [job]))
        return job.status


def test_playing_again_at_one_second_and_replaced_session_then_release():
    w = World()
    first = w.sessions[0]
    assert w.step("network") == "paused"
    assert first.events == ["pause_start", "pause_complete"]
    w.now += 1
    assert w.step() == "paused" and len(first.events) == 2
    w.now += 1                                # the original tab is now behind another window
    w.observations.clear()
    first.status = "playing"
    assert w.step() == "paused" and len(first.events) == 4
    w.now += 1
    w.observations = [observation(w.now)]
    second = Session()
    w.sessions = [second]
    assert w.step() == "paused" and second.events == ["pause_start", "pause_complete"]
    w.blocks.clear()                          # quota reset, regain network after a buffer stall
    assert w.step() == "unavailable" and second.status == "paused"
    second.status = "playing"                  # the person can start it after release
    assert w.step() == "unavailable" and len(second.events) == 2
    assert not w.worker.matches


def test_browser_sources_same_title_profiles_and_unrelated_audio():
    w = World()
    chrome, firefox, other = w.sessions[0], Session("Firefox-308046B0AF4A39CB"), Session("Spotify")
    w.sessions = [chrome, firefox, other]
    w.observations.extend([
        observation(w.now, 6, "cats-work", host="example.org", url="https://example.org/cats", role="nonforeground"),
        observation(w.now, 7, "cats-ff", product="Firefox", title="Cats — YouTube — Mozilla Firefox"),
    ])
    assert w.step("network") == "paused"
    assert not chrome.events and firefox.events and not other.events
    assert any("unidentifiable" in line for line in w.notes)
    w.observations.pop(1)
    w.sessions.append(Session())                 # two Chrome sessions exposing identical tracks
    assert w.step("network") == "paused"         # already paused Firefox
    assert not chrome.events and not w.sessions[-1].events


def test_prior_identity_is_retired_after_window_reuse_or_tab_navigates():
    w = World()
    player = w.sessions[0]
    assert w.step("network") == "paused"
    player.status = "playing"
    w.now += 1
    w.observations = [observation(w.now, host="example.org", url="https://example.org/cats")]
    assert w.step() == "unavailable"
    w.live.clear()
    w.observations = [observation(w.now)]
    assert w.step() == "unavailable" and len(player.events) == 2


@pytest.mark.parametrize(("status", "can_pause", "result", "expected", "trace"), [
    ("buffering", True, True, "paused", "buffering_pause_completed"),
    ("paused", True, True, "paused", "already_paused"),
    ("playing", False, True, "unavailable", "no_control_playing"),
    ("playing", True, False, "attempted", "false_return"),
    ("playing", True, PermissionError(), "attempted", "denied"),
    ("playing", True, RuntimeError(), "attempted", "exception_RuntimeError"),
    ("playing", True, None, "attempted", "timeout"),
])
def test_degraded_winrt_outcomes_are_independent(status, can_pause, result, expected, trace):
    w = World()
    media = w.sessions[0]
    media.status, media.can_pause, media.result = status, can_pause, result
    before = time.monotonic()
    assert w.step("network") == expected
    assert time.monotonic() - before < 0.5
    assert any(trace in line for line in w.notes)


def test_no_selected_tab_or_media_session_has_explicit_diagnostic():
    w = World()
    w.observations = [observation(w.now, tab=None, source="title")]
    assert w.step("network") == "unavailable"
    assert not w.sessions[0].events and any("unidentifiable" in line for line in w.notes)
    w.sessions.clear()
    assert w.step("network") == "unavailable"
    assert any("no_session" in line for line in w.notes)


def test_simultaneous_local_and_network_preflights_share_one_pause():
    w = World()
    local = site_media._Job(frozenset({HOST}), w.now + 0.5, "close", w.observations[0])
    service_job = site_media._Job(frozenset({HOST}), w.now + 0.5, "network")
    asyncio.run(w.worker._evaluate(None, [local, service_job]))
    assert local.status == service_job.status == "paused"
    assert w.sessions[0].events == ["pause_start", "pause_complete"]


def test_release_cancels_inflight_pause():
    w = World()
    media = w.sessions[0]
    media.result = None

    async def run():
        job = site_media._Job(frozenset({HOST}), w.now + 0.5, "network")
        pending = asyncio.create_task(w.worker._evaluate(None, [job]))
        while media.events != ["pause_start"]:
            await asyncio.sleep(0)
        w.blocks.clear()
        await pending
        return job.status

    assert asyncio.run(run()) == "unavailable"
    assert any("playing_released" in line for line in w.notes)


class Endpoint:
    """Two ends of a local fake with independent read queues."""
    def __init__(self):
        self.data = queue.Queue()
        self.available = threading.Event()
        self.closed = threading.Event()

    def connected(self):
        return self.peer.available.is_set()

    def send(self, payload):
        self.peer.data.put_nowait(payload)
        return True

    def receive(self):
        try:
            return self.data.get_nowait()
        except queue.Empty:
            return None

    def alive(self):
        return not self.peer.closed.is_set()

    def close(self):
        self.closed.set()


class Channel:
    def __init__(self):
        self.connection = queue.Queue()

    def offer(self, deadline):
        server, client = Endpoint(), Endpoint()
        server.peer, client.peer = client, server
        self.connection.put(client)
        yield server

    def connect(self):
        try:
            client = self.connection.get_nowait()
        except queue.Empty:
            return None
        client.available.set()
        return client


def test_service_startup_pauses_before_publishing_dns_hosts_firewall_and_cut(tmp_path, monkeypatch):
    db_path = tmp_path / "config.db"
    db = Database(db_path)
    db.add_item("YouTube", [HOST, CDN], "site", block_type="dns,close", rules=[{"rule_type": "permanent"}])
    e = service.Enforcer(db)
    trace, channel = [], Channel()
    player = Session(status="buffering", events=trace)
    hub = SimpleNamespace(open_db=lambda: Database(db_path, ui=True),
                          win=SimpleNamespace(window_pid=lambda hwnd: 105))

    async def manager():
        return SimpleNamespace(get_sessions=lambda: [player])

    worker = site_media.SiteMediaWorker(hub, manager=manager, recent=lambda _: [observation(time.monotonic())],
                                        transport=lambda: channel, scan=False, trace=trace.append)
    monkeypatch.setattr(e, "update_clock", lambda: NOW)
    monkeypatch.setattr(e, "count_unwatched_apps", lambda _: None)
    monkeypatch.setattr(service.apps, "list_processes", lambda: [])
    monkeypatch.setattr(service.netlog, "dns_names", lambda: {"192.0.2.1": "a.googlevideo.com"})
    monkeypatch.setattr(service.browser_policy, "apply", lambda **kw: False)
    monkeypatch.setattr(e, "update_firewall", lambda: trace.append("app_firewall"))
    monkeypatch.setattr(e, "update_quic", lambda _: trace.append("quic"))
    monkeypatch.setattr(service.firewall, "set_video_block", lambda _: trace.append("video_rule") or True)
    monkeypatch.setattr(service.connections, "close_to", lambda _: trace.append("tcp_cut") or [])
    native_request = pause_request.request

    def request(hosts, **kwargs):
        assert hosts == [CDN, HOST]
        assert not e.dns_blocks
        trace.append("request")
        receipt = native_request(hosts, server=channel, **kwargs)
        assert receipt.outcomes == ("paused",)
        trace.append("ack")
        return receipt

    monkeypatch.setattr(pause_request, "request", request)
    system_hosts, backup = tmp_path / "hosts", tmp_path / "hosts.bak"
    system_hosts.write_text("127.0.0.1 localhost\n")
    apply = service.hosts.apply

    def apply_hosts(hostnames):
        assert e.blocked_name(HOST) == "blocked"
        trace.append("hosts")
        return apply(hostnames, system_hosts, backup)

    monkeypatch.setattr(service.hosts, "apply", apply_hosts)
    monkeypatch.setattr(service.hosts, "flush_dns", lambda: trace.append("flush"))
    worker.start()
    try:
        e.enforce_once()
        assert worker.is_alive()
    finally:
        worker.stop()
        worker.join(1)
    assert not worker.is_alive()
    assert trace.index("request") < trace.index("pause_complete") < trace.index("ack")
    assert all(trace.index("ack") < trace.index(step) for step in (
        "app_firewall", "quic", "hosts", "flush", "video_rule", "tcp_cut"))
    assert HOST in system_hosts.read_text()
    # The worker used DB evaluation without any blocked-visit notification.
    assert not e.last_visit
    assert player.status == "paused"


def test_absent_client_and_stale_ack_share_one_deadline():
    now = 0
    absent, stale = Endpoint(), Endpoint()

    class Server:
        def offer(self, deadline):
            assert deadline == 0.5
            yield absent
            yield stale

    def sleep(seconds):
        nonlocal now
        now += seconds

    absent.connected = lambda: False
    stale.connected = lambda: True
    stale.send = lambda payload: stale.data.put(pause_request.ack("a" * 32, "paused")) or True
    receipt = pause_request.request([HOST, CDN], server=Server(), clock=lambda: now, sleep=sleep)
    assert now == 0.5 and absent.closed.is_set() and stale.closed.is_set()
    assert set(receipt.outcomes) == {"invalid_ack", "no_agent", "ack_timeout"}


@pytest.mark.parametrize("switch", ["none", "focus_lost", "unknown_new_tab", "switched_before_close", "unlocked",
                                    "unverified_after_pause"])
def test_word_guard_pauses_then_revalidates_each_chord(switch, monkeypatch):
    now = time.monotonic()
    bad = observation(now)
    new = observation(now, tab="new", title="New tab", url="chrome://newtab/", host=None)
    unknown = observation(now, tab=None, title="", url=None, host=None, source="unknown")
    other = observation(now, tab="other", host="example.org", url="https://example.org/")
    active = bad
    sites = {HOST: "close"}
    events = []
    sleeps = 0

    def before_action(obs, hosts, action):
        nonlocal active
        assert obs is bad and hosts == {HOST} and action == "close"
        events.append("pause_complete")
        if switch == "unlocked":
            sites.clear()
        elif switch == "unverified_after_pause":
            active = observation(now, source="recent_same_title")

    def chord(hwnd, *keys):
        nonlocal active
        if keys[-1] == win.VK_T:
            events.append("open")
            active = new
        elif keys[-1] == win.VK_TAB:
            events.append("switch_back")
            active = bad
        else:
            assert active is bad
            events.append("close")
        return True

    def sleep(_):
        nonlocal active, sleeps
        sleeps += 1
        if switch == "focus_lost" and sleeps == 1:
            active = None
        if switch == "unknown_new_tab" and sleeps == 1:
            active = unknown
        if switch == "switched_before_close" and sleeps == 2:
            active = other

    monkeypatch.setattr(word_guard.time, "sleep", sleep)
    monkeypatch.setattr(word_guard.diag, "note", lambda _: None)
    monkeypatch.setattr("monitor.browser_url.tab_count", lambda hwnd: 1)
    monkeypatch.setattr(win, "chord", chord)
    guard = word_guard.WordGuard(lambda *_: None, before_action)
    guard.tick(OFF, lambda **kw: active, word_guard.act, now, sites, live_sites=lambda: sites)
    expected = {
        "none": ["pause_complete", "open", "switch_back", "close"],
        "focus_lost": ["pause_complete", "open"],
        "unknown_new_tab": ["pause_complete", "open"],
        "switched_before_close": ["pause_complete", "open", "switch_back"],
        "unlocked": ["pause_complete"],
        "unverified_after_pause": ["pause_complete"],
    }
    assert events == expected[switch]



@pytest.mark.parametrize("way", ["dns", "dns,back"])
def test_word_guard_thread_reader_and_effective_site_flags(tmp_path, monkeypatch, way):
    from contextlib import nullcontext

    import monitor
    import uiautomation

    path = tmp_path / "guard.db"
    db = Database(path)
    db.add_item("Video", [HOST], "site", block_type=way, rules=[{"rule_type": "permanent"}])
    reads, events = [], []

    class Reader:
        def read(self, hwnd, exe, *, role, refresh=False):
            reads.append((hwnd, exe, role, refresh))
            return observation(time.monotonic())

    monkeypatch.setattr(monitor, "tab_reader", SimpleNamespace(TabReader=Reader), raising=False)
    monkeypatch.setattr(uiautomation, "UIAutomationInitializerInThread", nullcontext, raising=False)
    monkeypatch.setattr(Database.__init__, "__defaults__", (path, False))
    monkeypatch.setattr(word_guard.keywords, "settings_shared", lambda db: OFF)
    monkeypatch.setattr(win, "foreground", lambda: (5, "chrome.exe"))
    monkeypatch.setattr(win, "press", lambda *args: events.append("back") or True)
    guard = word_guard.WordGuard(lambda *_: None, lambda *args: events.append("pause"))
    ticks = iter((False, True))
    monkeypatch.setattr(guard.stop_event, "wait", lambda _: next(ticks))
    guard._check()
    if way == "dns":
        assert not reads and not events
    else:
        assert reads == [(5, "chrome.exe", "foreground", False), (5, "chrome.exe", "foreground", True)]
        assert events == ["pause", "back"]
