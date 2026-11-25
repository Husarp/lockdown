"""Lockdown enforcement service.

Every 2 seconds: evaluate block rules (permanent / hours / temporary / daily limit) using its own trusted
clock (changing the Windows clock has no effect), make the hosts file match
(repairing manual edits), keep browser DoH/QUIC locked off, and close open connections to newly blocked
sites. A listener on 127.0.0.1:80/443 records attempts to open blocked sites for the tray agent to notify.
Network log (every 2 s, own thread): new connections per app and site, kept for an hour.
Protection lists (scam / phishing / malware / adult ...): downloaded daily, blocked by the DNS filter (own threads).
Blocked apps (checked 4x per second): started while blocked -> killed at once; already open when the block
began -> the tray agent asks them to close, force-killed after 10 s. Block type firewall/both adds a Windows
Firewall rule ("Block internet"). "Minimize" is handled by the tray agent (it can see the desktop).
Needs admin/SYSTEM rights.

Usage:
    python src/service.py run              # enforcement loop (what the scheduled task runs)
    python src/service.py once             # single pass, for testing
    python src/service.py remove-policies  # undo browser policies, firewall rules, DNS filter, hosts entries (uninstall)
    python src/service.py restore-dns      # give network adapters their own DNS settings back (repair)
"""
import ctypes
import json
import logging
import ntpath
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import alerts
import antibypass
import applog
import enforcement_wake
from blocker import apps, browser_policy, connections, dnsfilter, firewall, hosts, netlog, pause_request, protection, site_block
import keywords
from blocker.listener import BlockListener
from db import Database
from monitor.usage import COUNTED_KEY, MAX_GAP_SEC, MEMBERS_KEY
from paths import APP_DIR, DATA_DIR, LOG_PATH
import pause
from trusted_time import LAST_TRUSTED_KEY, OFFSET_KEY, ZONE_KEY, TrustedClock, utc_offset, zone_name, zone_step

INTERVAL_SEC = 2
CLOCK_JUMP_SEC = 60
HEARTBEAT_KEY = "service_heartbeat"
# Keep closing connections to a newly blocked site for this long (browsers cache DNS ~1 min).
CLOSE_CONNECTIONS_FOR = timedelta(minutes=3)
ENDPOINT_LOOKUP_SEC = 0.2
VISIT_DEDUPE_SEC = 10
APP_CHECK_SEC = 0.25
APP_GRACE_SEC = 10          # app already open when its block began: asked to close, force-killed after this
LAUNCH_SLACK_SEC = 1        # started more than this after the block began = launched while blocked
FIREWALL_KEY = "firewall_rules"   # JSON {exe: path} of firewall rules Lockdown has added
QUIC_KEY = "firewall_quic"        # known browser paths and any legacy rules still to migrate
VIDEO_KEY = "firewall_video"      # remembered video address evidence
MEDIA_PAGES = {"youtube.com": "googlevideo.com", "youtu.be": "googlevideo.com",
               "netflix.com": "nflxvideo.net", "twitch.tv": "ttvnw.net"}
SHARED_GOOGLE_PAGES = ("youtube.com", "youtu.be", "youtube-nocookie.com", "google.com", "googleusercontent.com",
                       "googleapis.com", "gstatic.com", "ytimg.com")
VIDEO_MAX = 300                   # at most this many addresses in it (the newest)
NETLOG_SEC = 2
NETLOG_KEEP = timedelta(hours=1)  # the network log only shows the last hour
PROTECTION_CHECK_SEC = 10         # how often the service looks whether a protection list is due for download
DNS_ADAPTER_CHECK_SEC = 10        # how often network adapters are (re)pointed at the DNS filter (new networks)
LEARN_PATHS_SEC = 30              # how often apps added by name only get their exe path filled in (learn_paths)
# The tray app counts time (it sees which window is in front). When it hasn't counted for this long - not
# running, exited, frozen, its tracker stalled - the service counts every blocked app that is running instead,
# so limits and "N minutes allowed during blocked hours" keep filling (count_unwatched_apps).
TRACKER_STALE_SEC = 10

log = logging.getLogger("lockdown.service")


def _under(host, parent):
    host = host.lower().rstrip(".").removeprefix("www.")
    return host == parent or host.endswith("." + parent)


def _shared_page(host):
    return any(_under(host, parent) for parent in SHARED_GOOGLE_PAGES)


def _in_scope(host, scope):
    parts = host.lower().rstrip(".").removeprefix("www.").split(".")
    return any(".".join(parts[i:]) in scope for i in range(len(parts) - 1))


def _saved(db, key):
    try:
        value = json.loads(db.get_setting(key, "{}"))
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _path_map(value):
    return {key: firewall.program_path(path) for key, path in value.items()
            if isinstance(path, str) and ntpath.isabs(path)} if isinstance(value, dict) else {}


def _video_map(value):
    return {ip: name for ip, name in value.items()
            if isinstance(name, str) and firewall.is_video_host(name)
            and not connections.is_loopback(ip)} if isinstance(value, dict) else {}


def setup_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)
    if sys.stdout:  # pythonw has no console
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        log.addHandler(console)
    log.setLevel(logging.INFO)


class Enforcer:
    was_off = False        # switched off entirely (see enforce_once); a class default so that the DNS
    # threads can read it before the first tick, and so a bare instance has it
    was_paused = False     # a "Pause my blocks" is running (logged when it starts / ends)
    counting = False       # the service is counting app time itself (the tray app isn't, count_unwatched_apps)
    count_carry = 0.0
    count_mark = None      # the mark the service itself wrote last
    kill_failed: frozenset = frozenset()   # pids that couldn't be closed (logged once each)
    count_paths: dict = {}                 # pid -> (exe, lowercase path), for count_unwatched_apps
    video_ips: dict = {}                   # remembered ip -> media host evidence
    video_seen: dict = {}                  # ip -> video host name of a blocked site, seen since the last update
    net_names: dict = {}                   # ip -> name the network log remembers (NetworkLogger.names, netlog_loop)
    family: dict = {}      # exe -> {pid: (name, start time)} it started from its own folder: they ARE the app
    members: dict = {}     # pid -> sorted exes of the listed apps it is part of (publish_members)
    published: dict | None = None
    app_items: dict = {}   # exe -> item, for every app on the list (track_families)
    hosted: dict = {}      # pid -> the listed program file (game.jar, tool.py ...) that interpreter runs (hosted_processes)
    cmd_cache: dict = {}   # pid -> (exe, the program files its command line names) - read once per process
    count_running: set | None = None       # exes running at the service's last counting pass (launches)
    paths_learned_at = float("-inf")       # (learn_paths)
    def __init__(self, db: Database):
        self.db = db
        last = db.get_setting(LAST_TRUSTED_KEY)
        self.clock = TrustedClock(float(last) if last else None)
        self.last_offset: float | None = None
        self.blocks: dict[str, dict] = {}       # current active blocks, read by the listener
        self.dns_blocks: dict[str, dict] = {}   # those of them that are sent nowhere (hosts file / DNS filter)
        self.closing: dict[str, datetime] = {}  # ip -> keep closing connections until
        self.visit_db = None                    # separate connection for listener threads
        self.visit_lock = threading.Lock()
        self.last_visit: dict[str, float] = {}
        self.app_blocks: dict[str, dict] = {}   # exe -> block, for blocked apps (read by the app thread)
        self.app_first_seen: dict[int, tuple[str, float]] = {}   # pid -> (name, when first seen blocked)
        self.block_since: dict[str, float] = {}    # exe -> when its block began
        self.helpers: dict[str, set[int]] = {}     # exe -> pids it started ("also close background processes")
        self.path_cache: dict[int, tuple[str, str]] = {}   # pid -> (exe, lowercase path)
        self.firewalled: dict[str, str] = json.loads(db.get_setting(FIREWALL_KEY, "{}"))
        self._load_site_transport()
        self.flush_at, self.flush_delay = None, INTERVAL_SEC
        self.stop = None
        self.dns_diagnostics_lock = threading.Lock()
        self.dns_generation = None
        self.dns_answers = set()
        self.policy_reported = False
        self.cache_status = {}
        self.protection = protection.Protection()   # always-on scam / phishing / malware / adult lists

    def update_clock(self) -> datetime:
        """Trusted now; publishes the offset to the system clock and logs clock changes."""
        if self.clock.maybe_sync():
            log.info("Trusted time synced with internet time")
        self.update_zone()
        offset = self.clock.offset()
        if self.last_offset is not None and abs(offset - self.last_offset) > CLOCK_JUMP_SEC:
            log.warning("System clock changed by %+.0f s - ignored, Lockdown keeps its own time",
                        self.last_offset - offset)
            applog.record("clock_changed", f"The Windows clock was changed by {self.last_offset - offset:+.0f} s - "
                          "ignored, Lockdown keeps its own trusted time", self.db)
        self.last_offset = offset
        self.db.set_setting(OFFSET_KEY, f"{offset:.3f}")
        self.db.set_setting(LAST_TRUSTED_KEY, f"{self.clock.now_ts():.0f}")
        return self.clock.now()

    def update_zone(self):
        """A new Windows time zone counts only after 24 hours (it would shift blocked hours)."""
        ts = self.clock.now_ts()
        state = json.loads(self.db.get_setting(ZONE_KEY, "") or "null")
        new, self.clock.zone_shift, message = zone_step(state, zone_name(), utc_offset(ts), ts)
        if new != state:
            self.db.set_setting(ZONE_KEY, json.dumps(new))
        if message:
            log.warning(message)
            applog.record("clock_changed", message, self.db)

    def enforce_once(self):
        now = self.update_clock()
        try:
            self.count_unwatched_apps(now)
        except Exception:   # counting must never stop the blocking
            log.exception("Counting app time failed")
        if self.db.delete_expired_temporary(now):
            log.info("Removed expired temporary blocks")
        # Switched off entirely: carry on with an empty list of blocks rather than skipping the work, so the
        # hosts entries, firewall rules and app blocks are all taken back down by the same code that put them
        # up. Stopping here would leave whatever was in place when you switched it off.
        off = antibypass.is_off(self.db)
        if off != self.was_off:
            log.info("Lockdown switched %s", "off - nothing is enforced" if off else "back on")
            self.was_off = off
        paused = pause.until(self.db, now)   # "Pause my blocks": db.blocks lifts your blocks (not the lists)
        if bool(paused) != self.was_paused:
            log.info("Your blocks are %s", f"paused until {paused:%H:%M}" if paused else "back on")
            self.was_paused = bool(paused)
        all_blocks = [] if off else self.db.blocks(now)
        blocks, dns_blocks = self.site_scopes(all_blocks)
        # (by the bare exe name: a target typed as a full path is still that exe - apps.exe_name; two blocks on
        # one exe become one that does what both ask, closing winning - apps.merge_blocks)
        # (a program file - game.jar, a script - is closed or minimised only: apps.hosted_way)
        app_blocks: dict[str, dict] = {}
        for b in all_blocks:
            exe = apps.exe_name(b["item"]["target"]) if b["item"]["item_type"] == "app" else ""
            if exe and apps.is_hosted(exe):
                way = apps.hosted_way(b["item"].get("block_type"))
                b = {**b, "item": {**b["item"], "block_type": way}} if way else None
            if b and exe and exe not in apps.PROTECTED:
                app_blocks[exe] = apps.merge_blocks(app_blocks.get(exe), b)
        self.update_dns_scope(blocks, dns_blocks, now)
        self.app_blocks = app_blocks
        # every app on the list, blocked now or not: what it starts is followed all the time (track_families)
        app_items = {exe: b["item"] for exe, b in app_blocks.items()}
        for item in self.db.list_items():
            exe = apps.exe_name(item["target"]) if item["item_type"] == "app" else ""
            if exe and exe not in apps.PROTECTED:
                app_items.setdefault(exe, item)
        self.app_items = app_items
        self.update_firewall()
        if not off:
            self.learn_paths()
        self.publish_members()

        self.update_quic(bool(self.dns_blocks))
        self.update_hosts()
        self.cut_live_connections(now)
        self.update_video_block()
        self.prune_cuts(now)
        if self.closing:
            closed = connections.close_to(set(self.closing))
            if closed:
                log.info("Site transport IPv4 TCP cut: %d connection(s)", len(closed))

        kw = keywords.settings(self.db)
        if browser_policy.apply(safe_search=not off and kw["safesearch"], youtube=not off and kw["youtube"]):
            self.policy_reported = False
        if not self.policy_reported:
            log.info("Chrome/Edge/Brave/Firefox browser policies checked in HKLM (effective in-browser status unknown)")
            self.policy_reported = True

        self.db.set_setting(HEARTBEAT_KEY, str(time.time()))

    @staticmethod
    def site_scopes(all_blocks):
        blocks, dns_blocks = {}, {}
        for block in all_blocks:
            item = block["item"]
            if item["item_type"] != "site":
                continue
            for host in item["target"].split():
                blocks.setdefault(host, block)
                if site_block.blocks_dns(item.get("block_type")):
                    dns_blocks.setdefault(host, block)
        return blocks, dns_blocks

    def update_dns_scope(self, blocks, dns_blocks, now):
        previous = self.dns_blocks
        newly_blocked = sorted(host for host in dns_blocks if not _in_scope(host, previous))
        receipt = None
        if newly_blocked:
            log.info("Site Pause request before DNS/hosts/firewall/TCP cut: %d new host(s)", len(newly_blocked))
            try:
                receipt = pause_request.request(newly_blocked, stop=self.stop)
                log.info("Site Pause generation=%s outcomes=%s before enforcement",
                         receipt.generation, ",".join(receipt.outcomes))
            except Exception as error:
                log.warning("Site Pause request failed (%s). Enforcing the block.", type(error).__name__)
            try:
                # An unlock/reset can land during the desktop wait. A rule newly active since
                # this request will get its own bounded preflight on the next pass.
                latest = [] if antibypass.is_off(self.db) else self.db.blocks(self.clock.now())
                latest_blocks, latest_dns = self.site_scopes(latest)
                blocks = {h: latest_blocks[h] for h in blocks.keys() & latest_blocks.keys()}
                dns_blocks = {h: latest_dns[h] for h in dns_blocks.keys() & latest_dns.keys()}
            except Exception:
                log.exception("Site DNS rules could not be reread after Pause preflight")
            if new_scope := sorted(set(newly_blocked) & dns_blocks.keys()):
                self.close_cached(new_scope, now)
        with self.dns_diagnostics_lock:
            if previous.keys() != dns_blocks.keys():
                if self.dns_generation is not None:
                    log.info("Site DNS generation=%s filter_answered=%s", self.dns_generation,
                             ",".join(sorted(self.dns_answers)) or "none observed")
                if not dns_blocks:
                    self.dns_generation = None
                elif newly_blocked:
                    self.dns_generation = receipt.generation if receipt else "unavailable"
                self.dns_answers.clear()
                for page, media in MEDIA_PAGES.items():
                    if any(_under(host, page) for host in dns_blocks):
                        log.info("Site DNS coverage %s: configured media host %s=%s",
                                 page, media, _in_scope(media, dns_blocks))
            self.blocks, self.dns_blocks = blocks, dns_blocks

    def update_hosts(self):
        if hosts.apply(sorted(self.dns_blocks)):
            log.info("Hosts file updated: %d hostnames blocked", len(self.dns_blocks))
            self.flush_at, self.flush_delay = time.monotonic(), INTERVAL_SEC
            if hosts.repaired:
                log.warning("The hosts file was edited by something else - Lockdown's entries were put back")
                applog.record("hosts_repaired", "The hosts file was edited by something else (not by Lockdown): "
                              "Lockdown's entries were put back", self.db)
        now = time.monotonic()
        if self.flush_at is not None and now >= self.flush_at:
            if hosts.flush_dns() is False:
                self.flush_at = now + self.flush_delay
                self.flush_delay = min(30, self.flush_delay * 2)
            else:
                self.flush_at = None

    def on_dns_answer(self, host, category):
        if category != "blocked":
            return
        with self.dns_diagnostics_lock:
            kind = "media" if firewall.is_video_host(host) else "site"
            if (_in_scope(host, self.dns_blocks) and self.dns_generation is not None
                    and kind not in self.dns_answers):
                self.dns_answers.add(kind)
                log.info("Site DNS generation=%s filter_answered=%s", self.dns_generation, kind)

    def blocked_name(self, host: str) -> str | None:
        """Called by DNS threads. The entire personal DNS scope is published in one assignment."""
        if self.was_off:
            return None
        host = host.lower().rstrip(".").removeprefix("www.")
        return "blocked" if _in_scope(host, self.dns_blocks) else self.protection.which(host)

    def queue_cut(self, ip: str, host: str, now: datetime):
        # SetTcpEntry is IPv4-only. Shared page endpoints can also handle Google Search and Gmail.
        if ":" in ip or connections.is_loopback(ip) or _shared_page(host):
            return
        self.closing[ip] = now + CLOSE_CONNECTIONS_FOR
        if "closing_hosts" not in self.__dict__:
            self.closing_hosts = {}
        self.closing_hosts.setdefault(ip, set()).add(host)

    def prune_cuts(self, now: datetime):
        """An unlock releases queued cuts for that host even if another site stays blocked."""
        self.closing_hosts = {ip: {h for h in getattr(self, "closing_hosts", {}).get(ip, ())
                                  if _in_scope(h, self.dns_blocks)}
                              for ip, until in self.closing.items() if until > now}
        self.closing_hosts = {ip: names for ip, names in self.closing_hosts.items() if names}
        self.closing = {ip: until for ip, until in self.closing.items() if ip in self.closing_hosts}

    def _discover_endpoints(self, scope, now, phase):
        try:
            names = netlog.dns_names()
        except OSError as error:
            names = {}
            status = f"cache_read_exception:{type(error).__name__}"
        else:
            matching = [ip for ip, name in names.items() if _in_scope(name, scope)]
            status = ("no_results" if not matching else "loopback_only" if
                      all(connections.is_loopback(ip) for ip in matching) else "addresses_found")
        cache_status = self.__dict__.setdefault("cache_status", {})
        if phase == "before_flush" or cache_status.get(phase) != status:
            log.info("Site DNS endpoint discovery %s: %s", phase, status)
            cache_status[phase] = status

        aliases = getattr(names, "aliases", None)
        if aliases is None:
            aliases = {ip: (name,) for ip, name in names.items()}
        # Only the dedicated CDN names from the logger survive a DNS cache miss.
        remembered = {ip: name for ip, name in self.net_names.items() if firewall.is_video_host(name)}
        evidence = {**remembered, **names}
        unsafe = self.__dict__.setdefault("unsafe_video_ips", set())
        current_scope = self.dns_blocks.keys() | scope if hasattr(self, "dns_blocks") else scope
        for ip, name in evidence.items():
            if connections.is_loopback(ip):
                continue
            others = aliases.get(ip, ()) or (name,)
            in_scope = _in_scope(name, scope)
            video = firewall.is_video_host(name)
            was_video = ip in self.video_ips or ip in self.video_seen or ip in remembered
            if (any(_shared_page(alias) for alias in others)
                    or (was_video or video) and any(not firewall.is_video_host(alias) for alias in others)):
                if was_video or (in_scope and video):
                    unsafe.add(ip)
                self.closing.pop(ip, None)
                continue
            if ip in unsafe or not in_scope:
                continue
            if video:
                self.note_video(ip, name)
            elif any(not _in_scope(alias, current_scope) for alias in others):
                self.closing.pop(ip, None)
                continue
            self.queue_cut(ip, name, now)

        return aliases

    def cut_live_connections(self, now: datetime):
        if self.dns_blocks:
            self._discover_endpoints(self.dns_blocks.keys(), now, "live")

    def close_cached(self, hostnames, now: datetime):
        scope = set(hostnames)
        aliases = self._discover_endpoints(scope, now, "before_flush")
        # Ordinary IPv4 connections can outlive the cache entry. Give direct DNS
        # one budget across the new sites and keep shared page and CDN IPs out.
        if sys.platform != "win32":
            return
        deadline = time.monotonic() + ENDPOINT_LOOKUP_SEC
        current_scope = self.dns_blocks.keys() | scope
        for host in sorted(scope):
            if time.monotonic() >= deadline:
                break
            if firewall.is_video_host(host) or _shared_page(host):
                continue
            try:
                ips = connections.resolve(hosts.expand([host]), deadline=deadline)
            except OSError as error:
                log.warning("Site DNS direct lookup failed: %s", type(error).__name__)
                break
            for ip in ips:
                if ip not in self.unsafe_video_ips and all(
                        not _shared_page(name) and _in_scope(name, current_scope) for name in aliases.get(ip, ())):
                    self.queue_cut(ip, host, now)

    def note_video(self, ip: str, name: str):
        if firewall.is_video_host(name) and not connections.is_loopback(ip):
            self.video_seen = {**self.video_seen, ip: name.lower().rstrip(".")}

    def _load_site_transport(self):
        quic = _saved(self.db, QUIC_KEY)
        self.quic_legacy = _path_map(quic.get("legacy", {}) if "paths" in quic else quic)
        self.quic_paths = set(_path_map(quic.get("paths", {})).values()) | set(self.quic_legacy.values())
        video = _saved(self.db, VIDEO_KEY)
        self.video_ips = _video_map(video.get("addresses", {}) if "addresses" in video else video)
        self.video_legacy = _video_map(video.get("legacy", {}) if "addresses" in video else video)
        self.quic_state = firewall.SiteRules("quic")
        self.video_state = firewall.SiteRules("video")

    def _save_site_state(self, key, state):
        saved = json.dumps(state, sort_keys=True) if any(state.values()) else "{}"
        if self.db.get_setting(key, "{}") != saved:
            self.db.set_setting(key, saved)

    def update_video_block(self):
        """Retain CDN evidence until the matching effective DNS rule lifts."""
        seen, self.video_seen = self.video_seen, {}
        unsafe = getattr(self, "unsafe_video_ips", set())
        wanted = {ip: name for ip, name in {**self.video_ips, **seen}.items()
                  if ip not in unsafe and _in_scope(name, self.dns_blocks)}
        self.video_ips = dict(list(wanted.items())[-VIDEO_MAX:])
        # Evidence is safe to persist before native application, including after a failed add.
        self._save_site_state(VIDEO_KEY, {"addresses": self.video_ips, "legacy": self.video_legacy})
        legacy = {firewall.VIDEO_RULE: firewall.video_rule(self.video_legacy)} if self.video_legacy else {}
        self.video_state.reconcile([firewall.video_rule(self.video_ips)] if self.video_ips else [],
                                   legacy, time.monotonic(), unsafe=unsafe)
        if not self.dns_blocks:
            unsafe.clear()
        if legacy and self.video_state.confirmed_absent(firewall.VIDEO_RULE):
            self.video_legacy = {}
            self._save_site_state(VIDEO_KEY, {"addresses": self.video_ips, "legacy": {}})

    def update_quic(self, active: bool):
        """Deny UDP 443 by validated executable path, including simultaneous installs of a product."""
        wanted = set()
        if active:
            candidates = {(ntpath.basename(path), path) for path in self.quic_paths if apps.is_file(path)}
            candidates.update((exe, path) for pid, exe in apps.list_processes() if (path := apps.process_path(pid)))
            if candidates:
                from monitor import browser_catalog
                wanted = {firewall.program_path(path) for exe, path in candidates
                          if ntpath.isabs(path) and browser_catalog.identify(exe, path)}
        legacy = {firewall.QUIC_PREFIX + exe: firewall.quic_rule(path) for exe, path in self.quic_legacy.items()}
        self.quic_state.reconcile([firewall.quic_rule(path) for path in wanted], legacy, time.monotonic())
        self.quic_paths = wanted
        self.quic_legacy = {exe: path for exe, path in self.quic_legacy.items()
                            if not self.quic_state.confirmed_absent(firewall.QUIC_PREFIX + exe)}
        self._save_site_state(QUIC_KEY, {"paths": {path: path for path in sorted(wanted)}, "legacy": self.quic_legacy})

    def on_visit(self, hostname: str):
        """Called by listener threads when a browser tries to open a blocked hostname."""
        blocks = self.blocks
        block = blocks.get(hostname) or blocks.get(hostname.removeprefix("www."))
        if not block and (on := self.protection.which(hostname)):
            block = {"item": {"id": None, "display_name": hostname}, "reason": f"protection:{on}", "until": None}
        if block:
            self.record_event(hostname, block)

    def record_event(self, key: str, block: dict):
        """Write a block event for the tray agent (at most once per VISIT_DEDUPE_SEC per key)."""
        with self.visit_lock:
            if time.time() - self.last_visit.get(key, 0) < VISIT_DEDUPE_SEC:
                return
            self.last_visit[key] = time.time()
            if self.visit_db is None:
                self.visit_db = Database()
            item = block["item"]
            reason = alerts.tag_reason(block["reason"], block.get("category"))   # "permanent@distracting"
            self.visit_db.add_block_event(key, item["id"], item["display_name"], reason, block["until"],
                                          now=self.clock.now())

    # ---------- counting app time when the tray app doesn't ----------

    def count_unwatched_apps(self, now: datetime):
        """Run the shared service quota accounting at its existing point in the pass."""
        from quota_accounting import count_unwatched_apps
        return count_unwatched_apps(self, now)

    def _running(self, item: dict, running: set[str], procs) -> bool:
        """Is the app running: its exe (bare name, any case; Unreal's Shipping exe too - apps.names_of), or a
        program from its folder (apps.app_folder)."""
        if apps.names_of(item["target"]) & running:
            return True
        folder = apps.app_folder(item)
        if not folder:
            return False
        for pid, _ppid, name in procs:
            cached = self.count_paths.get(pid)
            if not cached or cached[0] != name:
                cached = self.count_paths[pid] = (name, (apps.process_path(pid) or "").lower())
            if name not in apps.PROTECTED and apps.in_folder(cached[1], folder):
                return True
        return False

    def publish_members(self):
        """Tell the tray app which running processes belong to a listed app - by name, what the app
        started from its own folder (also after the starter has gone), or a program from its game folder. The
        tray counts the window in front; it can't follow who started what, and a game protected by anti-cheat
        won't even tell it its path, so before this the real game's window was not counted at all."""
        alive = {pid for pid, _name in apps.list_processes()}
        members = {pid: exes for pid, exes in self.members.items() if pid in alive}
        if members != self.published:
            self.db.set_setting(MEMBERS_KEY, json.dumps({str(pid): exes for pid, exes in members.items()}))
            self.published = members

    # ---------- apps ----------

    def enforce_apps(self):
        """Blocked app launched while blocked: killed at once. Already open when the block began: the tray
        agent asks it to close (so you can save), force-killed after the grace time. "The app" is every process
        that is it (app_processes) - not only the exe named on the list."""
        now = time.time()
        targets = {exe: b for exe, b in self.app_blocks.items() if apps.kills(b["item"]["block_type"])}
        self.block_since = {exe: self.block_since.get(exe, now) for exe in targets}
        procs = apps.list_processes_full()
        names = {pid: name for pid, _ppid, name in procs}
        self.path_cache = {pid: v for pid, v in self.path_cache.items() if names.get(pid) == v[0]}
        self.kill_failed = frozenset(self.kill_failed & set(names))
        self.hosted = self.hosted_processes(names)
        self.track_families(procs, names)
        owned = self.app_processes(targets, procs)
        self.enforce_background(targets, procs)
        seen = {}
        for pid, _ppid, name in procs:
            exe = owned.get(pid)
            if not exe:
                continue
            block = targets[exe]
            what = exe if name == exe else f"{name} (part of {exe})"
            first = self.app_first_seen.get(pid)
            if not first or first[0] != name:   # (a process number Windows has handed to another program: new)
                self.record_event(exe, block)
                started = apps.start_time(pid)
                rule = block.get("rule") or {}
                # over an opening limit ("launches" mode): this very launch went over it
                over_openings = rule.get("rule_type") == "switch_limit" and (rule.get("switch_mode") or "visit") == "visit"
                launched_while_blocked = started and started > self.block_since[exe] + LAUNCH_SLACK_SEC
                if (over_openings or launched_while_blocked) and self._terminate(pid, what):
                    log.info("Blocked app %s was started - closed immediately (pid %d)", what, pid)
                    continue
                first = (name, now)
            seen[pid] = first
            if now - first[1] >= APP_GRACE_SEC and self._terminate(pid, what):
                log.info("Force-closed blocked app %s (pid %d)", what, pid)
        self.app_first_seen = seen

    def _terminate(self, pid: int, what: str) -> bool:
        """Close a process. A refusal is written to the log (once per process) - it used to pass in silence, so a
        game that can't be closed (anti-cheat, another account's process) played on and left no trace."""
        if apps.terminate(pid):
            return True
        if pid not in self.kill_failed:
            self.kill_failed = self.kill_failed | {pid}
            log.warning("Couldn't close blocked app %s (pid %d) - Windows refused; still trying", what, pid)
        return False

    def track_families(self, procs: list[tuple[int, int, str]], names: dict[int, str]):
        """For EVERY app on the list, blocked now or not: what it starts from its own install folder (or its game
        folder) is the app too, remembered after the starter has gone. Only followed while blocked before, so a game
        whose starter ran inside the allowed hours and exited (Game.exe -> Game-Win64-Shipping.exe) left a game
        nobody knew about - not closed when the hours ended, its time never counted. A remembered process is
        recognised by its number AND its name, and its start time is checked again before it is closed, so a
        number Windows hands to another program never makes that program "the game"."""
        by_name: dict[str, set[int]] = {}
        for pid, name in names.items():
            by_name.setdefault(name, set()).add(pid)
        hosted: dict[str, set[int]] = {}
        for pid, file in self.hosted.items():
            hosted.setdefault(file, set()).add(pid)
        family: dict[str, dict[int, tuple[str, float | None]]] = {}
        members: dict[int, set[str]] = {pid: {file} for pid, file in self.hosted.items()}   # (the tray counts by it)
        folders: dict[str, set[str]] = {}
        me = os.getpid()
        for exe, item in self.app_items.items():
            kept = {pid: v for pid, v in self.family.get(exe, {}).items() if names.get(pid) == v[0]}
            main = set().union(*(by_name.get(n, ()) for n in apps.names_of(exe)), hosted.get(exe, ()))
            for pid in main:
                members.setdefault(pid, set()).add(exe)
            folder = apps.app_folder(item)
            if main:
                homes = {f for pid in main if (f := apps.helper_folder(self._path(pid, names[pid])))}
                if folder:
                    homes.add(folder)
                if exe in hosted and (own := apps.helper_folder(item.get("app_path"))):
                    homes.add(own)   # (what a start.bat starts from its own folder: the game it starts)
                for pid in apps.descendants(main, procs) if homes else ():
                    name = names[pid]
                    if pid in kept or name in apps.PROTECTED or pid == me:
                        continue
                    path = self._path(pid, name)
                    # (a game Steam started from a library inside Steam's own folder is that game, not Steam)
                    game = apps.game_folder(path)
                    if (not game or game in homes) and any(apps.in_folder(path, home) for home in homes):
                        kept[pid] = (name, apps.start_time(pid))
            if kept:
                family[exe] = kept
            for pid in kept:
                members.setdefault(pid, set()).add(exe)
            if folder:
                folders.setdefault(folder, set()).add(exe)
        if folders:   # programs from an app's folder: each process's folders looked up, not every folder scanned
            for pid, name in names.items():
                if name in apps.PROTECTED or pid == me:
                    continue
                for parent in apps.parents_of(self._path(pid, name)):
                    for exe in folders.get(parent, ()):
                        if name not in apps.names_of(exe):
                            members.setdefault(pid, set()).add(exe)
        self.family = family
        self.members = {pid: sorted(exes) for pid, exes in members.items()}

    def app_processes(self, targets: dict[str, dict], procs: list[tuple[int, int, str]]) -> dict[int, str]:
        """pid -> blocked exe, for every process that IS a blocked app:
        - the exe on the list (by bare name, any case), and an Unreal game's Shipping exe (apps.names_of);
        - what it started from its own install folder, whenever that was (track_families): an Unreal game's
          Game.exe runs Binaries\\Win64\\Game-Win64-Shipping.exe and exits;
        - for a game from a game library (Steam, Epic, GOG, Xbox ...), anything running from the game's folder,
          whoever started it (apps.app_folder).
        Before, only the exe named on the list was closed: the game itself, under another name, played on.
        Windows' and Lockdown's own processes never count."""
        names = {pid: name for pid, _ppid, name in procs}
        by_name: dict[str, list[int]] = {}
        for pid, name in names.items():
            by_name.setdefault(name, []).append(pid)
        me = os.getpid()
        owned: dict[int, str] = {}
        folders: dict[str, str] = {}
        for exe, block in targets.items():
            mine = {pid for n in apps.names_of(exe) for pid in by_name.get(n, ())}
            mine |= {pid for pid, file in self.hosted.items() if file == exe}
            for pid, (_name, started) in self.family.get(exe, {}).items():
                now_started = apps.start_time(pid) if started is not None else None
                if started is None or now_started is None or abs(now_started - started) < 1:
                    mine.add(pid)
            if folder := apps.app_folder(block["item"]):
                folders.setdefault(folder, exe)
            for pid in mine:
                owned.setdefault(pid, exe)
        if folders:   # (each process's folders looked up - not every blocked folder scanned for every process)
            for pid, name in names.items():
                if pid not in owned:
                    exe = next((folders[f] for f in apps.parents_of(self._path(pid, name)) if f in folders), None)
                    if exe:
                        owned[pid] = exe
        # (python.exe is never closed - Lockdown may run on it - unless it runs a script you listed)
        return {pid: exe for pid, exe in owned.items()
                if pid != me and (names[pid] not in apps.PROTECTED or self.hosted.get(pid) == exe)}

    def hosted_processes(self, names: dict[int, str]) -> dict[int, str]:
        """pid -> the listed program file it runs, for every interpreter process running one: javaw.exe -jar
        game.jar is game.jar, python tool.py is tool.py, cmd.exe /c start.bat is start.bat (apps.hosted_file) -
        by the file's name, wherever it is; other Java / Python programs are not. Each process's command line is
        read once (only interpreters', only while a program file is on the list); one that can't be read is tried
        again on the next pass. Never Lockdown's own (run from source it is python too): not this process, nor a
        script or an interpreter in Lockdown's folder."""
        self.cmd_cache = {pid: v for pid, v in self.cmd_cache.items() if names.get(pid) == v[0]}
        files = {exe for exe in self.app_items if apps.is_hosted(exe)}
        if not files:
            return {}
        kinds = {apps.hosted_kind(f) for f in files}
        me, own = os.getpid(), str(APP_DIR).lower()
        out = {}
        for pid, name in names.items():
            if pid == me or apps.interpreter(name) not in kinds:
                continue
            cached = self.cmd_cache.get(pid)
            if not cached:
                cmd = apps.command_line(pid)
                if not cmd:   # (None: gone or protected; "": not filled in yet - tried again)
                    continue
                cached = self.cmd_cache[pid] = (name, tuple(apps.hosted_files(name, cmd)))
            for path in cached[1]:
                module = path.startswith("-m:")   # (python -m: tool.py by its name, but pkg\\sub.py / tool\\__main__.py by the listed path)
                path = path[3:] if module else path
                file = apps.exe_name(path)
                if module and "\\" in path:
                    item = self.app_items.get(file) or {}
                    listed = (apps.typed_path(item.get("target")) or item.get("app_path") or "").lower().replace("/", "\\")
                    if not listed.endswith("\\" + path.lower()):
                        continue
                if file in files and not apps.in_folder(path, own) and not apps.in_folder(self._path(pid, name), own):
                    out[pid] = file
                    break
        return out

    def _path(self, pid: int, name: str) -> str:
        """Lowercase exe path of a process ('' if unknown), looked up once per process."""
        cached = self.path_cache.get(pid)
        if not cached or cached[0] != name or not cached[1]:
            cached = self.path_cache[pid] = (name, (apps.process_path(pid) or "").lower())
        return cached[1]

    def enforce_background(self, targets: dict[str, dict], procs: list[tuple[int, int, str]]):
        """"Also close its background processes": once the app itself is closed, close what it started and
        whatever runs from its install folder (helpers that keep going without it)."""
        wanted = {exe: b for exe, b in targets.items() if apps.kills_background(b["item"]["block_type"])}
        self.helpers = {exe: pids for exe, pids in self.helpers.items() if exe in wanted}
        if not wanted:
            return
        for exe, block in wanted.items():
            main = {pid for pid, _ppid, name in procs if name == exe}
            if main:   # the app goes first (asked to close, then force-closed); remember what it started
                self.helpers.setdefault(exe, set()).update(apps.descendants(main, procs))
                continue
            folder = apps.helper_folder(block["item"]["app_path"] or apps.typed_path(block["item"]["target"]))
            for pid, _ppid, name in procs:
                if name in apps.PROTECTED or pid == os.getpid():
                    continue
                if pid not in self.helpers.get(exe, ()) and not (folder and self._in_folder(pid, name, folder)):
                    continue
                if self._terminate(pid, f"{name} (background process of {exe})"):
                    log.info("Closed background process %s of blocked app %s (pid %d)", name, exe, pid)

    def _in_folder(self, pid: int, name: str, folder: str) -> bool:
        return apps.in_folder(self._path(pid, name), folder)

    def learn_paths(self):
        """Fill in the exe path of apps added by name only, from what is running (at most every LEARN_PATHS_SEC).
        The path tells which folder is the app's, so a game's other exes are recognised as the game - by the
        service when it closes it and by the tray app when it counts its time. Learned from:
        - a running copy of the exe itself;
        - a program running from a game's folder (Steam, Epic ...) that holds the exe: Steam often starts an Unreal
          game's Shipping exe directly, so the exe you listed never ran and its folder was never learned;
        - an Unreal Shipping exe whose starter sits where Unreal puts it (<game>\\<Project>\\Binaries\\Win64\\..).
        A copy in your Desktop / Downloads / Documents (or a drive's top folder) is never learned - a renamed decoy
        run from there would have hidden where the real game is - and a copy in a game folder wins over any other."""
        mono = time.monotonic()
        if mono - self.paths_learned_at < LEARN_PATHS_SEC:
            return
        self.paths_learned_at = mono
        wanted: dict[str, list[int]] = {}
        for item in self.db.list_items():
            if item["item_type"] == "app" and not item.get("app_path") and not apps.typed_path(item["target"]):
                wanted.setdefault(apps.exe_name(item["target"]), []).append(item["id"])
        if not wanted:
            return
        found: dict[str, str] = {}
        for pid, name in apps.list_processes():
            if name in apps.PROTECTED or not (path := apps.process_path(pid)):
                continue
            candidates = []
            if name in wanted:
                candidates.append((name, path))
            if game := apps.game_folder(path):
                candidates += [(exe, p) for exe, p in apps.exes_in(game).items() if exe in wanted]
            for exe in wanted:
                if name in apps.names_of(exe) and name != exe and (p := apps.unreal_starter(path, exe)):
                    candidates.append((exe, p))
            for exe, p in candidates:
                if apps.game_folder(p):
                    if not apps.game_folder(found.get(exe)):
                        found[exe] = p
                elif apps.helper_folder(p) and exe not in found:
                    found[exe] = p
        for exe, path in found.items():
            for item_id in wanted[exe]:
                self.db.set_app_path(item_id, path)
            log.info("Learned where %s is installed: %s", exe, path)

    def update_firewall(self):
        """Firewall rules for blocked apps with block type 'firewall'/'both'; remove the rest."""
        wanted = {}
        for exe, b in self.app_blocks.items():
            if apps.firewalls(b["item"]["block_type"]):
                path = b["item"]["app_path"] or self._learn_path(exe, b["item"]["id"])
                if path:
                    wanted[exe] = path
        if wanted == self.firewalled:
            return
        for exe in set(self.firewalled) - set(wanted):
            firewall.remove(exe)
            log.info("Firewall rule removed: %s", exe)
        for exe, path in wanted.items():
            if self.firewalled.get(exe) != path:
                firewall.add(exe, path)
                log.info("Firewall rule added: %s (%s)", exe, path)
        self.firewalled = wanted
        self.db.set_setting(FIREWALL_KEY, json.dumps(wanted))

    def _learn_path(self, exe: str, item_id: int) -> str | None:
        """Exe path of a running copy of the app (for apps added by name only)."""
        for pid, name in apps.list_processes():
            if name == exe and (path := apps.process_path(pid)):
                self.db.set_app_path(item_id, path)
                return path
        return None


class NetworkLogger:
    """Every 2 s: which TCP connections are new, which app opened them and which site they go to (Windows DNS
    cache) -> network_log. Rows older than an hour are deleted."""

    def __init__(self, db: Database, now):
        self.db, self.now = db, now
        self.seen: set[tuple[int, str, int, int]] = set()   # (pid, remote ip, remote port, local port)
        self.names: dict[str, str] = {}              # ip -> domain, remembered after the DNS cache forgets it
        self.procs: dict[int, tuple[str, bool]] = {}  # pid -> (exe, is a Windows program)
        self.windows_dir = os.environ.get("SystemRoot", r"C:\Windows").lower() + "\\"

    def _proc(self, pid: int, exe_by_pid: dict[int, str]) -> tuple[str, bool]:
        exe = exe_by_pid.get(pid, "system" if pid in (0, 4) else "unknown")
        cached = self.procs.get(pid)
        if not cached or cached[0] != exe:
            path = (apps.process_path(pid) or "").lower()
            cached = self.procs[pid] = (exe, exe in ("system", "unknown") or path.startswith(self.windows_dir))
        return cached

    def tick(self):
        now = self.now()
        current = set(netlog.tcp_connections())
        new, self.seen = current - self.seen, current
        exe_by_pid = dict(apps.list_processes())
        self.procs = {pid: v for pid, v in self.procs.items() if pid in exe_by_pid}
        if any(ip not in self.names and not netlog.is_local(ip) for _, ip, _, _ in new):
            try:
                self.names = {**self.names, **netlog.dns_names()}
            except OSError as error:
                log.warning("Network log DNS cache read failed (%s). Using remembered names.", type(error).__name__)
            if len(self.names) > 20000:
                self.names = dict(list(self.names.items())[-10000:])
        rows: dict[tuple, dict] = {}
        minute = now.strftime("%Y-%m-%d %H:%M")
        for pid, ip, port, _local_port in new:
            exe, windows = self._proc(pid, exe_by_pid)
            row = rows.setdefault((exe, ip, port), {
                "minute": minute, "exe": exe, "ip": ip, "port": port, "domain": self.names.get(ip, ""), "count": 0,
                "windows": int(windows), "local": int(netlog.is_local(ip))})
            row["count"] += 1
        if rows:
            self.db.add_network(list(rows.values()))
        self.db.prune_network((now - NETLOG_KEEP).strftime("%Y-%m-%d %H:%M"))


def protection_loop(enforcer: Enforcer):
    """Keep the protection lists fresh (downloads take a while, so not in the 2-second loop)."""
    db = Database()
    while True:
        try:
            protection.update_due_lists(db, enforcer.clock.now(), log=log)
        except Exception:
            log.exception("Protection list update failed")
        time.sleep(PROTECTION_CHECK_SEC)


def filter_wanted(enforcer: Enforcer, safe: dict) -> bool:
    """Should the network adapters use the DNS filter? For the protection lists and forced SafeSearch - and while
    any site you blocked is sent nowhere (0.84.9): the hosts file can't name a video host (each video has its
    own, rr1---sn-....googlevideo.com), so a video page open when its block began found the next one; the filter
    answers for everything under googlevideo.com."""
    return not enforcer.was_off and bool(enforcer.protection.count() or safe["search"] or safe["youtube"]
                                        or enforcer.dns_blocks)


def dns_loop(enforcer: Enforcer, stop=None):
    """Keep protection loaded and configure adapters only after all DNS sockets bind."""
    db = Database()
    safe = {"search": False, "youtube": False}
    server = None
    retry_at, retry_delay = float("-inf"), INTERVAL_SEC
    next_adapters = float("-inf")
    try:
        while not (stop and stop.is_set()):
            try:
                cfg = protection.settings(db)
                if enforcer.protection.refresh(cfg):
                    log.info("Protection lists loaded: %d domains", enforcer.protection.count())
                    if hosts.flush_dns() is False:
                        log.warning("DNS filter: Windows may still hold cached protection-list answers")
                kw = keywords.settings(db)
                off = antibypass.is_off(db)
                safe["search"], safe["youtube"] = not off and kw["safesearch"], not off and kw["youtube"]
                now = time.monotonic()
                if server is None and now >= retry_at:
                    candidate = dnsfilter.Server(enforcer.blocked_name, log,
                                                  safe=lambda name: keywords.safe_target(name, safe["search"],
                                                                                         safe["youtube"]))
                    candidate.on_block = enforcer.on_dns_answer
                    try:
                        candidate.start()
                    except OSError as error:
                        candidate.close()
                        retry_at = now + retry_delay
                        retry_delay = min(30, retry_delay * 2)
                        log.error("DNS filter bind failed (%s). Restoring adapter DNS before retry.",
                                  type(error).__name__)
                        dnsfilter.restore(db, log)
                    else:
                        server = candidate
                        log.info("DNS filter listening on IPv4 and IPv6 loopback")
                if server is not None and now >= next_adapters:
                    next_adapters = now + DNS_ADAPTER_CHECK_SEC
                    if filter_wanted(enforcer, safe):
                        server.upstreams = dnsfilter.point_to_filter(db, log,
                                                                     lambda value: setattr(server, "upstreams", value))
                        if not server.upstreams:
                            log.warning("DNS filter has no usable adapter upstreams")
                    elif db.get_setting(dnsfilter.SAVED_KEY, "{}") != "{}":
                        dnsfilter.restore(db, log)
            except Exception:
                log.exception("DNS filter adapter configuration/upkeep failed")
            (stop.wait if stop else time.sleep)(INTERVAL_SEC)
    finally:
        if server and stop is not None:
            try:
                dnsfilter.restore(db, log)
            finally:
                server.close()
        db.close()


def netlog_loop(enforcer: Enforcer):
    logger = NetworkLogger(Database(), enforcer.clock.now)
    while True:
        try:
            logger.tick()
            enforcer.net_names = logger.names
        except Exception:
            log.exception("Network logging failed")
        time.sleep(NETLOG_SEC)


def app_loop(enforcer: Enforcer):
    while True:
        try:
            enforcer.enforce_apps()
        except Exception:
            log.exception("App enforcement failed")
        time.sleep(APP_CHECK_SEC)


def main(argv: list[str] | None = None, stop: threading.Event | None = None):
    """Command line entry (also used by the Windows service, service_win.py, which passes `stop`)."""
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""
    if cmd not in ("run", "once", "remove-policies", "restore-dns"):
        print(__doc__)
        return 2
    setup_logging()
    if not ctypes.windll.shell32.IsUserAnAdmin():
        log.error("Not running as administrator - cannot write the hosts file")
        return 1
    if cmd == "restore-dns":
        dnsfilter.restore(Database(), log)
        return 0
    if cmd == "remove-policies":
        browser_policy.remove()
        db = Database()
        dnsfilter.restore(db, log)
        for exe in json.loads(db.get_setting(FIREWALL_KEY, "{}")):
            firewall.remove(exe)
        db.set_setting(FIREWALL_KEY, "{}")
        enforcer = Enforcer(db)
        enforcer.update_quic(False)
        enforcer.update_video_block()
        pending = enforcer.quic_state.pending_removal | enforcer.video_state.pending_removal
        incomplete = pending or not (enforcer.quic_state.verified and enforcer.video_state.verified)
        hosts.apply([])   # (the Lockdown section of the hosts file goes too)
        flushed = hosts.flush_dns()
        if incomplete or flushed is False:
            log.warning("Policy teardown incomplete. See site firewall and DNS flush diagnostics.")
            return 1
        log.info("Browser policies, firewall rules, DNS filter and hosts-file entries removed")
        return 0
    enforcer = Enforcer(Database())
    if cmd == "once":
        enforcer.enforce_once()
        return 0
    log.info("Service started")
    from version import VERSION
    applog.set_source("service")
    applog.install_crash_hooks()
    applog.install_error_log()
    beat = enforcer.db.get_setting(HEARTBEAT_KEY)
    applog.process_started(enforcer.db, VERSION, extra=(f"last seen alive {datetime.fromtimestamp(float(beat)):%Y-%m-%d %H:%M}"
                                                        if beat and beat.replace('.', '', 1).isdigit() else ""))
    BlockListener(enforcer.on_visit, log).start()
    threading.Thread(target=app_loop, args=(enforcer,), daemon=True).start()
    threading.Thread(target=netlog_loop, args=(enforcer,), daemon=True).start()
    threading.Thread(target=protection_loop, args=(enforcer,), daemon=True).start()
    stop = enforcer.stop = stop or threading.Event()
    dns_thread = threading.Thread(target=dns_loop, args=(enforcer, stop), daemon=True)
    wake = enforcement_wake.Wake()
    try:
        while not stop.is_set():
            try:
                enforcer.enforce_once()
            except Exception:
                log.exception("Enforcement pass failed")
            if not dns_thread.is_alive() and dns_thread.ident is None:
                dns_thread.start()
            wake.wait(stop, INTERVAL_SEC)
    finally:
        stop.set()
        if dns_thread.is_alive():
            dns_thread.join(timeout=2)
        wake.close()
    log.info("Service stopped")
    applog.process_stopped(enforcer.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
