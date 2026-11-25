"""Associate an identifiable browser player with a blocked site and issue Pause."""
import asyncio
import logging
import ntpath
import queue
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import antibypass
from blocker import pause_request, site_block
from resident import diag
from trusted_time import now_from_db

WATCH_SEC = 1.0
FRESH_SEC = 1.5
NATIVE_SEC = 0.16
ACK_MARGIN_SEC = 0.04
log = logging.getLogger("lockdown.site_media")

# Known desktop application IDs and executable spellings. Match whole IDs, with the
# hashed Firefox installation suffix handled separately. A browser family is insufficient.
_SOURCE_IDS = {
    "chrome": {"chrome", "chrome.exe", "googlechrome"},
    "edge": {"msedge", "msedge.exe", "microsoftedge", "microsoft.microsoftedge_8wekyb3d8bbwe!microsoftedge"},
    "brave": {"brave", "brave.exe", "bravesoftware.brave"},
    "firefox": {"firefox", "firefox.exe", "mozilla.firefox"},
    "opera": {"opera", "opera.exe", "opera stable"},
    "opera gx": {"opera gx", "opera gx stable", "operagx", "operagx.exe"},
    "vivaldi": {"vivaldi", "vivaldi.exe"},
    "arc": {"arc", "arc.exe", "thebrowsercompany.arc_cz0p9ybby5r40!arc"},
    "zen": {"zen", "zen.exe", "zen-browser.exe", "zen browser"},
    "librewolf": {"librewolf", "librewolf.exe"},
    "waterfox": {"waterfox", "waterfox.exe"},
    "chromium": {"chromium", "chromium.exe"},
}
_PRODUCTS = {"google chrome": "chrome", "microsoft edge": "edge", "mozilla firefox": "firefox",
             "brave browser": "brave", "opera browser": "opera", "zen browser": "zen"}
_BROWSER_SUFFIX = re.compile(
    r"\s[-–—]\s(?:google chrome|microsoft\s*edge|brave|(?:mozilla )?firefox|opera(?: gx)?|"
    r"vivaldi|arc|zen(?: browser)?|librewolf|waterfox|chromium|private browsing)(?: [^-–—]*)?$", re.I)
_SITE_SUFFIX = re.compile(r"\s[-–—]\s(?:youtube(?: music| shorts)?)(?:\s+and \d+.*)?$", re.I)


def tab_token(observation):
    return getattr(observation, "tab_token", getattr(observation, "tab_id", None))


def observation_time(observation):
    return getattr(observation, "observed_at", getattr(observation, "at", float("-inf")))


def verified(observation):
    source = getattr(observation, "source", "")
    source = str(getattr(source, "value", source)).casefold().replace("_", "-").replace(" ", "-")
    return bool(getattr(observation, "host", None) and source in {
        "uia", "recent", "recent-same-title", "same-title", "confirmed-title",
    })


def _product(observation):
    raw = getattr(observation, "product", None) or getattr(getattr(observation, "browser", None), "product", "")
    product = " ".join(str(getattr(raw, "value", raw)).lower().replace("\u200b", "").replace("-", " ").split())
    return _PRODUCTS.get(product, product)


def _source_matches(source, observation):
    source = source.casefold()
    product = _product(observation)
    if source in _SOURCE_IDS.get(product, ()):
        return True
    if product == "firefox" and re.fullmatch(r"(?:mozilla\.)?firefox(?:-[a-f0-9]{8,32}|\.[a-f0-9]{8,32})", source):
        return True
    path = getattr(observation, "path", None) or getattr(getattr(observation, "browser", None), "path", None)
    return bool(product and path and ntpath.isabs(source) and ntpath.normcase(path) == ntpath.normcase(source))


def _title(text):
    return " ".join((text or "").replace("\u200b", "").split()).casefold()


def _tab_title(observation):
    title = _BROWSER_SUFFIX.sub("", _title(getattr(observation, "title", None)))
    return _SITE_SUFFIX.sub("", re.sub(r"^\(\d+\) ", "", title)).strip()


def _includes(hosts, name):
    return bool(name and any(name == h or name.endswith("." + h) for h in hosts))


def _site_rules(db):
    # DB evaluation also works before any blocked-visit event exists.
    if antibypass.is_off(db):
        return {}
    out = {}
    for block in db.blocks(now_from_db(db)):
        item = block["item"]
        if item["item_type"] == "site":
            for host in item["target"].split():
                out.setdefault(host, set()).update(site_block.site_flags(item.get("block_type")))
    return out


async def _manager():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
    return await GlobalSystemMediaTransportControlsSessionManager.request_async()


def _recent(age):
    from monitor import tab_reader
    return tab_reader.recent_observations(age)


@dataclass
class _Job:
    hosts: frozenset
    deadline: float
    action: str
    observation: object = None
    generation: str = "local"
    done: threading.Event = field(default_factory=threading.Event)
    status: str = "unavailable"
    pipe: object = None


@dataclass
class _Media:
    source: str
    title: str
    artist: str
    album: str
    status: str
    can_pause: bool

    @property
    def track(self):
        return self.source, self.title, self.artist, self.album


class SiteMediaWorker(threading.Thread):
    """One async owner of WinRT objects and command deadlines, independent of UI polling."""

    def __init__(self, hub, *, manager=_manager, recent=_recent, transport=pause_request.resident_transport,
                 clock=time.monotonic, rules=_site_rules, scan=True, trace=diag.note):
        super().__init__(name="site-media", daemon=True)
        self.hub, self.manager_factory, self.recent = hub, manager, recent
        self.transport_factory, self.clock, self.rules, self.trace = transport, clock, rules, trace
        self.scan = scan
        self.stopping = threading.Event()
        self.scan_needed = threading.Event()
        self.scan_requested = threading.Event()
        self.jobs = queue.Queue(maxsize=32)
        self.manager = None
        self.native_tasks = {}
        self.matches = {}     # live WinRT session -> (complete metadata, selected-tab observation)
        self.pauses = {}      # live session -> (track, completion, result, generation)
        self.last_notes = {}
        self.sent_acks = []

    def stop(self):
        self.stopping.set()
        self.scan_requested.set()

    def before_action(self, observation, blocked_hosts, action):
        job = _Job(frozenset(blocked_hosts), self.clock() + pause_request.WAIT_SEC, action, observation)
        if not self.is_alive() or self.stopping.is_set():
            return job.status
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            return job.status
        if not job.done.wait(max(0, job.deadline - self.clock())):
            self.trace(f"site_pause {action} outcome=caller_timeout before desktop action")
            return "unavailable"
        return job.status

    def _note(self, action, outcome):
        key = action, outcome
        now = self.clock()
        if now - self.last_notes.get(key, float("-inf")) >= 10:
            self.last_notes[key] = now
            self.trace(f"site_pause {action} outcome={outcome}")

    def _finish(self, job, status):
        job.status = status
        if job.pipe:
            sent = (self.clock() < job.deadline and not self.stopping.is_set()
                    and job.pipe.send(pause_request.ack(job.generation, status)))
            if sent:
                self.sent_acks.append((job.pipe, job.deadline))
            else:
                job.pipe.close()
            job.pipe = None
        if job.action != "watch":
            self.trace(f"site_pause generation={job.generation} action={job.action} complete={status}")
        job.done.set()

    def run(self):
        with _apartment():
            db = self.hub.open_db()
            scanner = None
            try:
                if self.scan:
                    scanner = threading.Thread(target=self._observe_tabs, name="site-media-tabs", daemon=True)
                    scanner.start()
                asyncio.run(self._serve(db))
            finally:
                self.manager = None
                self.matches.clear()
                self.pauses.clear()
                self.native_tasks.clear()
                self.stop()
                if scanner is not None:
                    scanner.join(timeout=0.1)
                db.close()

    def _observe_tabs(self):
        """UIA calls belong to their own COM thread and can never hold up an ack."""
        try:
            import uiautomation
            from monitor import browser_catalog, tab_reader

            with uiautomation.UIAutomationInitializerInThread():
                reader = tab_reader.TabReader()
                win = self.hub.win
                while not self.stopping.is_set():
                    self.scan_requested.wait(WATCH_SEC)
                    self.scan_requested.clear()
                    if not self.scan_needed.is_set() or self.stopping.is_set():
                        continue
                    hwnd, exe = win.foreground()
                    if hwnd:
                        reader.read(hwnd, exe, role="foreground", refresh=True)
                    for other, _title_text, path in win.top_windows():
                        if self.stopping.is_set() or not self.scan_needed.is_set():
                            break
                        exe = ntpath.basename(path)
                        if other != hwnd and browser_catalog.identify(exe, path):
                            reader.read(other, exe, role="nonforeground", refresh=True)
        except Exception as error:
            self._note("read", type(error).__name__)

    async def _serve(self, db):
        pipe = None
        pending, generations = [], {}
        active = batch = None
        next_watch = 0.0
        try:
            transport = self.transport_factory()
        except Exception as error:
            transport = None
            self._note("ipc", type(error).__name__)
        try:
            while not self.stopping.is_set():
                now = self.clock()
                generations = {key: end for key, end in generations.items() if end > now}
                for pipe_ack, deadline in tuple(self.sent_acks):
                    if now >= deadline or not pipe_ack.alive():
                        self.sent_acks.remove((pipe_ack, deadline))
                        pipe_ack.close()
                if transport:
                    if pipe is None and not self.sent_acks and not any(
                            job.pipe for job in pending + (batch or [])):
                        pipe = transport.connect()
                    if pipe and (raw := pipe.receive()) is not None:
                        request = pause_request.Request.decode(raw, now)
                        if request and request.generation not in generations:
                            generations[request.generation] = request.deadline
                            pending.append(_Job(request.hosts, request.deadline, "network",
                                                generation=request.generation, pipe=pipe))
                        else:
                            pipe.close()
                        pipe = None
                # Release the handle if the endpoint closes before sending a request.
                if pipe and not pipe.alive():
                    pipe.close()
                    pipe = None
                while not self.jobs.empty():
                    pending.append(self.jobs.get_nowait())
                for job in tuple(pending):
                    if job.deadline <= now:
                        self._finish(job, "unavailable")
                        pending.remove(job)
                if active is not None and active.done():
                    active.result()
                    active = batch = None
                if active is None and (pending or now >= next_watch):
                    batch, pending = pending, []
                    if now >= next_watch:
                        batch.append(_Job(frozenset(), now + pause_request.WAIT_SEC, "watch"))
                        next_watch = now + WATCH_SEC
                    active = asyncio.create_task(self._evaluate(db, batch))
                await asyncio.sleep(pause_request.POLL_SEC)
        finally:
            if pipe:
                pipe.close()
            if active is not None:
                active.cancel()
            for pipe_ack, _ in self.sent_acks:
                pipe_ack.close()
            self.sent_acks.clear()
            for job in pending + (batch or []):
                if not job.done.is_set():
                    self._finish(job, "unavailable")
            while not self.jobs.empty():
                self._finish(self.jobs.get_nowait(), "unavailable")

    async def _call(self, key, start, deadline, keep=None):
        if self.stopping.is_set() or self.clock() >= deadline:
            return None, "timeout"
        try:
            task = self.native_tasks.get(key)
            if task is None:
                task = asyncio.ensure_future(start())
                self.native_tasks[key] = task
                task.add_done_callback(lambda task: self._native_done(key, task))
            loop = asyncio.get_running_loop()
            end = loop.time() + min(NATIVE_SEC, max(0, deadline - self.clock()))
            while not task.done():
                remaining = end - loop.time()
                if remaining <= 0:
                    task.cancel()
                    return None, "timeout"
                if self.stopping.is_set() or (keep is not None and not keep()):
                    task.cancel()
                    return None, "released"
                await asyncio.wait((task,), timeout=min(0.04, remaining) if keep else remaining)
            if task.cancelled():
                return None, "timeout"
            return task.result(), None
        except PermissionError:
            return None, "denied"
        except Exception as error:
            denied = getattr(error, "winerror", None) in (5, -2147024891, 0x80070005)
            return None, "denied" if denied else f"exception_{type(error).__name__}"

    def _native_done(self, key, task):
        if self.native_tasks.get(key) is task:
            del self.native_tasks[key]
        if not task.cancelled():
            task.exception()

    async def _read_media(self, session, deadline):
        try:
            source = session.source_app_user_model_id
            info = session.get_playback_info()
            status = str(getattr(info.playback_status, "name", info.playback_status)).casefold()
            controls = info.controls
            props, error = await self._call(("metadata", session), session.try_get_media_properties_async, deadline)
            if error:
                self._note("metadata", error)
            if props is None:
                # A transient read failure keeps a live prior binding for diagnostics, but cannot
                # establish that the player still represents the same track.
                return None
            return _Media(source, _title(props.title), _title(getattr(props, "artist", None)),
                          _title(getattr(props, "album_title", None)), status,
                          bool(controls and controls.is_pause_enabled))
        except Exception as error:
            self._note("session", f"exception_{type(error).__name__}")
            return None

    def _observations(self, batch):
        observations = list(self.recent(FRESH_SEC))
        observations.extend(job.observation for job in batch if job.observation is not None)
        out = {}
        now = self.clock()
        for obs in observations:
            when = observation_time(obs)
            if not 0 <= now - when <= FRESH_SEC:
                continue
            prior = out.get(obs.hwnd)
            if prior is None or when >= observation_time(prior):
                out[obs.hwnd] = obs
        return out

    def _select(self, session, media, entries, observations):
        candidates = [obs for obs in observations.values()
                      if _source_matches(media.source, obs) and media.title and _tab_title(obs) == media.title]
        if len(candidates) > 1:
            self.matches.pop(session, None)
            return None
        prior = self.matches.get(session)
        if prior:
            track, obs = prior
            current = observations.get(obs.hwnd)
            contradicted = current and (current.pid != obs.pid or (
                tab_token(current) == tab_token(obs)
                and (getattr(current, "host", None) != obs.host or _tab_title(current) != media.title)))
            other = candidates and (candidates[0].hwnd != obs.hwnd or tab_token(candidates[0]) != tab_token(obs))
            if (track == media.track and not contradicted and not other
                    and self.hub.win.window_pid(obs.hwnd) == obs.pid):
                return obs
            self.matches.pop(session, None)
            if other and track == media.track:
                return None
        if not candidates:
            return None
        obs = candidates[0]
        if not verified(obs) or tab_token(obs) is None or not obs.pid or self.hub.win.window_pid(obs.hwnd) != obs.pid:
            return None
        if any(other != session and value.title == media.title and _source_matches(value.source, obs)
               for other, value in entries.items()):
            return None
        self.matches[session] = media.track, obs
        return obs

    @staticmethod
    def _applies(job, obs, rules):
        if job.action == "watch":
            return _includes(rules, obs.host)
        observed = job.observation
        if observed is not None and (
                obs.hwnd != observed.hwnd or obs.pid != observed.pid or tab_token(obs) != tab_token(observed)):
            return False
        if job.action == "network":
            scope = {host for host in job.hosts if "dns" in rules.get(host, ())}
        elif job.action in ("close", "back"):
            # Back can escalate to close on a blocked tab.
            scope = {host for host in job.hosts if {"close", "back"} & rules.get(host, set())}
        else:
            scope = {host for host in job.hosts if job.action in rules.get(host, ())}
        return _includes(scope, obs.host)

    async def _pause(self, db, session, media, obs, jobs):
        now = self.clock()
        transition = [job for job in jobs if job.action != "watch" and now < job.deadline - ACK_MARGIN_SEC]
        if media.status in {"buffering", "changing"} and transition:
            jobs = transition
        deadline = max(job.deadline for job in jobs) - ACK_MARGIN_SEC
        if now >= deadline or self.stopping.is_set():
            return "unavailable", "expired"

        def keep():
            rules = self.rules(db)
            return self.hub.win.window_pid(obs.hwnd) == obs.pid and any(
                self.clock() < job.deadline - ACK_MARGIN_SEC and self._applies(job, obs, rules) for job in jobs)

        if not keep():
            return "unavailable", "released_or_changed"
        if media.status == "paused":
            return "paused", "already_paused"
        generation = next((job.generation for job in transition if job.action == "network"), None)
        last = self.pauses.get(session)
        if transition and last and last[0] == media.track and now - last[1] < WATCH_SEC / 2:
            # A new service episode can reuse a simultaneous local Pause once.
            if generation is None or last[3] in (None, generation):
                self.pauses[session] = last[:3] + (generation or last[3],)
                return last[2], "coalesced"
        if media.status != "playing" and not (transition and media.status in {"changing", "buffering"}):
            return "unavailable", f"status_{media.status}"
        if not media.can_pause:
            return "unavailable", f"no_control_{media.status}"
        paused, error = await self._call(("pause", session), session.try_pause_async, deadline, keep)
        result = "paused" if paused else "unavailable" if error == "released" else "attempted"
        self.pauses[session] = media.track, self.clock(), result, generation
        return result, f"{media.status}_{error or ('pause_completed' if paused else 'false_return')}"

    async def _evaluate(self, db, jobs):
        outcome = {id(job): "unavailable" for job in jobs}
        try:
            rules = self.rules(db)
            if not rules:
                self.scan_needed.clear()
                self.matches.clear()
                self.pauses.clear()
                return
            if not self.scan_needed.is_set() or any(job.action != "watch" for job in jobs):
                self.scan_needed.set()
                self.scan_requested.set()
            self.matches = {session: match for session, match in self.matches.items()
                            if _includes(rules, match[1].host)}
            deadline = max(job.deadline for job in jobs) - ACK_MARGIN_SEC
            if self.manager is None:
                self.manager, error = await self._call("manager", self.manager_factory, deadline)
                if self.manager is None:
                    self._note("manager", error or "unavailable")
                    return
            try:
                sessions = tuple(self.manager.get_sessions())
            except Exception:
                self.manager = None
                raise
            self.matches = {s: self.matches[s] for s in sessions if s in self.matches}
            self.pauses = {s: self.pauses[s] for s in sessions if s in self.pauses}
            if not sessions:
                self._note("session", "no_session")
                return
            entries = dict(zip(sessions, await asyncio.gather(*(self._read_media(s, deadline) for s in sessions))))
            entries = {key: value for key, value in entries.items() if value is not None}
            observations = self._observations(jobs)
            if self.scan and not observations and deadline - self.clock() > 0.1:
                # Give the independent UIA scanner a chance to supply its first observation.
                await asyncio.sleep(0.06)
                observations = self._observations(jobs)
            selected = []
            for session, media in entries.items():
                obs = self._select(session, media, entries, observations)
                if obs is None:
                    self._note("session", "unidentifiable")
                    continue
                applicable = [job for job in jobs if self.clock() < job.deadline and self._applies(job, obs, rules)]
                if applicable:
                    selected.append((session, media, obs, applicable))
            results = await asyncio.gather(*(self._pause(db, *selection) for selection in selected))
            for (_, _, _, applicable), (status, reason) in zip(selected, results):
                for job in applicable:
                    self._note(job.action, reason)
                    previous = outcome[id(job)]
                    if previous == "unavailable" or status == "paused":
                        outcome[id(job)] = status
        except Exception as error:
            self._note("worker", f"exception_{type(error).__name__}")
        finally:
            for job in jobs:
                self._finish(job, outcome[id(job)])


@contextmanager
def _apartment():
    try:
        from winrt import runtime
    except ImportError:   # injection on Linux and graceful diagnostics if WinRT is unavailable
        yield
    else:
        runtime.init_apartment(runtime.ApartmentType.MULTI_THREADED)
        try:
            yield
        finally:
            runtime.uninit_apartment()
