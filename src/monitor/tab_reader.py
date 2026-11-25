"""Selected browser tab observations for quota sampling and pre-action checks."""
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace

from blocker.site_block import title_site
from monitor import win
from monitor.address import LAST_VALID_SEC
from monitor.browser_catalog import BrowserIdentity, identify
from monitor.browser_url import ChromeRead, _ProbeLane

URL_REFRESH_SEC = 2
NO_BAR_RETRY_SEC = 30
NO_BAR_AFTER = 3
_SUSTAINED_SEC = 4
_MAX_WINDOWS = 64
_MAX_RECENT = 128
_MAX_REPORT_KEYS = 256
_ROLES = {"foreground", "visible", "nonforeground"}
_HIDDEN = {"bar-hidden", "bar-unavailable"}
_EXTRA_BRANDS = {
    "Arc": ("Arc", "Arc Browser"),
    "Zen": ("Zen", "Zen Browser"),
    "LibreWolf": ("LibreWolf",),
    "Waterfox": ("Waterfox", "Waterfox Classic"),
    "Thorium": ("Thorium", "Thorium Browser"),
    "Cromite": ("Cromite",),
    "Supermium": ("Supermium",),
    "Ungoogled Chromium": ("Ungoogled Chromium", "ungoogled-chromium"),
    "SRWare Iron": ("SRWare Iron",),
}


def _title_site(title, browser, confirmed_host=None):
    for brand in _EXTRA_BRANDS.get(browser.product, ()):
        suffix = r"\s[-–—]\s" + re.escape(brand) + r"(?:\s+\([^()]+\))?$"
        title = re.sub(suffix, "", title, flags=re.IGNORECASE)
    return title_site(title, browser.exe, confirmed_host)


@dataclass(frozen=True)
class TabObservation:
    hwnd: int
    pid: int
    role: str
    observed_at: float
    age_sec: float = 0
    browser: BrowserIdentity | None = None
    process_started_at: float | None = None
    tab_token: tuple | None = None
    host: str | None = None
    url: str | None = None
    title: str | None = None
    source: str = "unknown"
    bar_hidden: bool = False
    read_failed: bool = True
    failure_reason: str | None = None
    selector_stage: str = "window"
    window_class: str = "unknown"
    verified_at: float | None = None
    actionable: bool = False

    @property
    def exe(self):
        return self.browser.exe if self.browser else None

    @property
    def product(self):
        return self.browser.product if self.browser else None

    @property
    def version(self):
        return self.browser.version if self.browser else "unknown"

    @property
    def path(self):
        return self.browser.path if self.browser else None


@dataclass
class _Sample:
    role: str
    title: str
    generation: tuple
    read: ChromeRead
    attempted_at: float
    verified: TabObservation | None = None
    failed_since: float | None = None
    failures: int = 0


_recent = OrderedDict()
_version_reports = OrderedDict()
_window_reports = OrderedDict()
_shared_lock = threading.Lock()


def _remember(mapping, key, value, limit):
    mapping[key] = value
    mapping.move_to_end(key)
    if len(mapping) > limit:
        mapping.popitem(last=False)


def recent_observations(max_age_sec: float) -> tuple[TabObservation, ...]:
    """Most recent observation per HWND, newest first, with age at snapshot time."""
    with _shared_lock:
        now = time.monotonic()
        return tuple(replace(obs, age_sec=now - obs.observed_at)
                     for obs in sorted(_recent.values(), key=lambda o: o.observed_at, reverse=True)
                     if 0 <= now - obs.observed_at <= max_age_sec)


def _publish(obs):
    with _shared_lock:
        previous = _recent.get(obs.hwnd)
        if previous is None or obs.observed_at >= previous.observed_at:
            if obs.browser is None:
                _recent.pop(obs.hwnd, None)
            else:
                _remember(_recent, obs.hwnd, obs, _MAX_RECENT)


@dataclass
class _VersionReport:
    failed_window: tuple | None = None
    failed_since: float | None = None
    recoveries: int = 0
    failures: int = 0


def _diagnose(obs, sample, duration):
    from resident import diag

    key = (obs.product, obs.version)
    window = (obs.hwnd, obs.pid, obs.process_started_at)
    messages = []
    with _shared_lock:
        stats = _version_reports.get(key) or _VersionReport()
        if obs.read_failed:
            stats.failures = min(stats.failures + 1, 9999)
            if sample.failures >= NO_BAR_AFTER and obs.observed_at - sample.failed_since >= _SUSTAINED_SEC:
                if stats.failed_window is None:
                    stats.failed_window = window
                    stats.failed_since = sample.failed_since
                    messages.append("version")
            if obs.role == "nonforeground" and window not in _window_reports:
                _remember(_window_reports, window, None, _MAX_REPORT_KEYS)
                messages.append("window")
        elif stats.failed_window == window and not stats.recoveries:
            stats.recoveries += 1
            messages.append("recovery")
        _remember(_version_reports, key, stats, _MAX_REPORT_KEYS)
        count = stats.failures
        failed_since = stats.failed_since if "recovery" in messages else sample.failed_since
    for kind in messages:
        outcome = "recovered" if kind == "recovery" else "degraded" if obs.host else "unreadable"
        failure_sec = round(obs.observed_at - failed_since) if failed_since is not None else 0
        message = (f"Browser tab {outcome} scope={kind} product={obs.product} version={obs.version} "
                   f"window={obs.hwnd:x} class={obs.window_class} role={obs.role} "
                   f"selector={obs.selector_stage} fallback={obs.source} reason={obs.failure_reason or 'none'} "
                   f"read_ms={round(duration * 1000)} failure_sec={failure_sec} failures={count}")
        diag.note(message, important=True)


def _pid_start(pid: int):
    from blocker.apps import start_time

    return start_time(pid)


class TabReader:
    """Use from a single resident thread. Its private UIA thread owns all proxies."""

    def __init__(self):
        self._probe = _ProbeLane()
        self._samples = OrderedDict()

    def read(self, hwnd: int, exe: str | None, *, role: str, refresh: bool = False) -> TabObservation:
        """Read the current selected tab, optionally bypassing the sample cache.

        For quota sampling use host (or URL when present). Before an action call
        with refresh=True and require actionable on the matching window/tab.
        Title-only observations may be sufficient for sampling a site's quota.
        """
        if role not in _ROLES:
            raise ValueError(f"Unknown browser window role {role!r}")
        now = time.monotonic()
        pid = win.window_pid(hwnd)
        if not pid:
            return self._unavailable(hwnd, pid, role, now, "window-unavailable")
        browser = identify(exe, win.pid_path(pid))
        if browser is None:
            return self._unavailable(hwnd, pid, role, now, "unknown-browser")
        generation = (pid, _pid_start(pid), browser)
        title = win.window_title(hwnd)
        window_sample = self._samples.get(hwnd)
        if window_sample and window_sample.generation != generation:
            window_sample = None
        previous = window_sample if window_sample and title and window_sample.title == title else None

        interval = URL_REFRESH_SEC
        if previous and role == previous.role == "visible" and previous.failures >= NO_BAR_AFTER:
            interval = NO_BAR_RETRY_SEC
        resample = (refresh or previous is None or role != previous.role
                    or not 0 <= now - previous.attempted_at < interval)
        started = time.perf_counter()
        read = self._probe.call("read", hwnd, generation, browser, title, resample, refresh or previous is None)
        duration = time.perf_counter() - started
        now = time.monotonic()
        if (win.window_pid(hwnd) != pid or _pid_start(pid) != generation[1] or win.window_title(hwnd) != title):
            return self._unavailable(hwnd, pid, role, now, "window-changed")
        if previous and read.tab_token is not None:
            prior_token = previous.read.tab_token or (previous.verified.tab_token if previous.verified else None)
            if read.tab_token != prior_token:
                previous = None
        if read.sampled or previous is None:
            failed_since = window_sample.failed_since if window_sample and window_sample.failures else now
            failures = window_sample.failures if window_sample else 0
            sample = _Sample(
                role, title, generation, read, now,
                verified=previous.verified if previous else None,
                failed_since=failed_since if read.reason else None,
                failures=failures + 1 if read.reason else 0,
            )
        else:
            sample = previous
        obs = self._observation(hwnd, pid, browser, title, sample, now)
        if read.sampled and not obs.read_failed:
            sample.verified = obs
        _remember(self._samples, hwnd, sample, _MAX_WINDOWS)
        _publish(obs)
        if read.sampled:
            _diagnose(obs, sample, duration)
        return obs

    def _unavailable(self, hwnd, pid, role, now, reason):
        self._samples.pop(hwnd, None)
        obs = TabObservation(hwnd, pid, role, now, failure_reason=reason)
        _publish(obs)
        return obs

    @staticmethod
    def _observation(hwnd, pid, browser, title, sample, now):
        read = sample.read
        obs = TabObservation(
            hwnd, pid, sample.role, sample.attempted_at, age_sec=now - sample.attempted_at, browser=browser,
            process_started_at=sample.generation[1], tab_token=read.tab_token, title=title,
            bar_hidden=read.reason in _HIDDEN, read_failed=read.reason is not None, failure_reason=read.reason,
            selector_stage=read.stage, window_class=read.window_class,
        )
        if read.url:
            return replace(obs, url=read.url, host=read.host, source="uia",
                           verified_at=sample.attempted_at, actionable=bool(read.host))
        prior = sample.verified
        if prior and read.tab_token is not None and read.tab_token != prior.tab_token:
            prior = None
        title_host = _title_site(title, browser, prior.host if prior else None)
        corroborated = bool(prior and prior.host and title_host
                            and (prior.host == title_host or prior.host.endswith("." + title_host)))
        actionable = corroborated and read.tab_token is not None and obs.bar_hidden
        if prior and 0 <= now - prior.verified_at <= LAST_VALID_SEC:
            # An internal URL is useful to legacy callers; it has no site host.
            return replace(obs, host=prior.host, url=prior.url, source="recent_same_title",
                           verified_at=prior.verified_at, actionable=actionable)
        if prior:
            return (replace(obs, host=prior.host, source="confirmed_title", verified_at=prior.verified_at,
                            actionable=actionable) if corroborated else obs)
        # Typed address text and unreadable/unstable windows supply no new title evidence.
        if title_host and read.reason in (_HIDDEN | {"invalid-value"}):
            return replace(obs, host=title_host, source="title")
        return obs
