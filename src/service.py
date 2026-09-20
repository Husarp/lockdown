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
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from blocker import apps, browser_policy, connections, dnsfilter, firewall, hosts, netlog, protection
import keywords
from blocker.listener import BlockListener
from db import Database
from paths import DATA_DIR, LOG_PATH
from trusted_time import LAST_TRUSTED_KEY, OFFSET_KEY, ZONE_KEY, TrustedClock, utc_offset, zone_name, zone_step

INTERVAL_SEC = 2
CLOCK_JUMP_SEC = 60
HEARTBEAT_KEY = "service_heartbeat"
# Keep closing connections to a newly blocked site for this long (browsers cache DNS ~1 min).
CLOSE_CONNECTIONS_FOR = timedelta(minutes=3)
VISIT_DEDUPE_SEC = 10
APP_CHECK_SEC = 0.25
APP_GRACE_SEC = 10          # app already open when its block began: asked to close, force-killed after this
LAUNCH_SLACK_SEC = 1        # started more than this after the block began = launched while blocked
FIREWALL_KEY = "firewall_rules"   # JSON {exe: path} of firewall rules Lockdown has added
NETLOG_SEC = 2
NETLOG_KEEP = timedelta(hours=1)  # the network log only shows the last hour
PROTECTION_CHECK_SEC = 10         # how often the service looks whether a protection list is due for download
DNS_ADAPTER_CHECK_SEC = 10        # how often network adapters are (re)pointed at the DNS filter (new networks)

log = logging.getLogger("lockdown.service")


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
    def __init__(self, db: Database):
        self.db = db
        last = db.get_setting(LAST_TRUSTED_KEY)
        self.clock = TrustedClock(float(last) if last else None)
        self.last_offset: float | None = None
        self.blocks: dict[str, dict] = {}       # current active blocks, read by the listener
        self.closing: dict[str, datetime] = {}  # ip -> keep closing connections until
        self.visit_db = None                    # separate connection for listener threads
        self.visit_lock = threading.Lock()
        self.last_visit: dict[str, float] = {}
        self.app_blocks: dict[str, dict] = {}   # exe -> block, for blocked apps (read by the app thread)
        self.app_first_seen: dict[int, float] = {}
        self.block_since: dict[str, float] = {}    # exe -> when its block began
        self.helpers: dict[str, set[int]] = {}     # exe -> pids it started ("also close background processes")
        self.path_cache: dict[int, tuple[str, str]] = {}   # pid -> (exe, lowercase path)
        self.firewalled: dict[str, str] = json.loads(db.get_setting(FIREWALL_KEY, "{}"))
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

    def enforce_once(self):
        now = self.update_clock()
        if self.db.delete_expired_temporary(now):
            log.info("Removed expired temporary blocks")
        all_blocks = self.db.blocks(now)
        blocks = {}
        for b in all_blocks:
            if b["item"]["item_type"] == "site":
                for h in b["item"]["target"].split():
                    blocks.setdefault(h, b)
        self.app_blocks = {b["item"]["target"].lower(): b for b in all_blocks
                           if b["item"]["item_type"] == "app" and b["item"]["target"].lower() not in apps.PROTECTED}
        self.update_firewall()

        newly_blocked = sorted(set(blocks) - set(self.blocks))
        if newly_blocked:
            # resolve before the hosts file points them at 127.0.0.1
            for ip in connections.resolve(hosts.expand(newly_blocked)):
                self.closing[ip] = now + CLOSE_CONNECTIONS_FOR

        # (the protection lists are not in the hosts file: Windows' DNS client hangs on huge hosts files - they're
        # blocked by the DNS filter, see dns_loop)
        if hosts.apply(sorted(blocks)):
            hosts.flush_dns()
            log.info("Hosts file updated: %d hostnames blocked", len(blocks))
        self.blocks = blocks

        kw = keywords.settings(self.db)
        if browser_policy.apply(safe_search=kw["safesearch"], youtube=kw["youtube"]):
            log.info("Browser policies (re)applied")

        self.closing = {ip: until for ip, until in self.closing.items() if until > now}
        if self.closing:
            closed = connections.close_to(set(self.closing))
            if closed:
                log.info("Closed %d open connections to blocked sites", closed)

        self.db.set_setting(HEARTBEAT_KEY, str(time.time()))

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
            self.visit_db.add_block_event(key, item["id"], item["display_name"], block["reason"], block["until"],
                                          now=self.clock.now())

    # ---------- apps ----------

    def enforce_apps(self):
        """Blocked app launched while blocked: killed at once. Already open when the block began: the tray
        agent asks it to close (so you can save), force-killed after the grace time."""
        now = time.time()
        targets = {exe: b for exe, b in self.app_blocks.items() if apps.kills(b["item"]["block_type"])}
        self.block_since = {exe: self.block_since.get(exe, now) for exe in targets}
        procs = apps.list_processes_full()
        self.enforce_background(targets, procs)
        seen = {}
        for pid, _ppid, exe in procs:
            block = targets.get(exe)
            if not block:
                continue
            if pid not in self.app_first_seen:
                self.record_event(exe, block)
                started = apps.start_time(pid)
                rule = block.get("rule") or {}
                # over an opening limit ("launches" mode): this very launch went over it
                over_openings = rule.get("rule_type") == "switch_limit" and (rule.get("switch_mode") or "visit") == "visit"
                launched_while_blocked = started and started > self.block_since[exe] + LAUNCH_SLACK_SEC
                if (over_openings or launched_while_blocked) and apps.terminate(pid):
                    log.info("Blocked app %s was started - closed immediately (pid %d)", exe, pid)
                    continue
            first = seen[pid] = self.app_first_seen.get(pid, now)
            if now - first >= APP_GRACE_SEC and apps.terminate(pid):
                log.info("Force-closed blocked app %s (pid %d)", exe, pid)
        self.app_first_seen = seen

    def enforce_background(self, targets: dict[str, dict], procs: list[tuple[int, int, str]]):
        """"Also close its background processes": once the app itself is closed, close what it started and
        whatever runs from its install folder (helpers that keep going without it)."""
        wanted = {exe: b for exe, b in targets.items() if apps.kills_background(b["item"]["block_type"])}
        self.helpers = {exe: pids for exe, pids in self.helpers.items() if exe in wanted}
        if not wanted:
            return
        alive = {pid for pid, _ppid, _exe in procs}
        self.path_cache = {pid: v for pid, v in self.path_cache.items() if pid in alive}
        for exe, block in wanted.items():
            main = {pid for pid, _ppid, name in procs if name == exe}
            if main:   # the app goes first (asked to close, then force-closed); remember what it started
                self.helpers.setdefault(exe, set()).update(apps.descendants(main, procs))
                continue
            folder = apps.helper_folder(block["item"]["app_path"])
            for pid, _ppid, name in procs:
                if name in apps.PROTECTED or pid == os.getpid():
                    continue
                if pid not in self.helpers.get(exe, ()) and not (folder and self._in_folder(pid, name, folder)):
                    continue
                if apps.terminate(pid):
                    log.info("Closed background process %s of blocked app %s (pid %d)", name, exe, pid)

    def _in_folder(self, pid: int, name: str, folder: str) -> bool:
        cached = self.path_cache.get(pid)
        if not cached or cached[0] != name:
            cached = self.path_cache[pid] = (name, (apps.process_path(pid) or "").lower())
        return cached[1].startswith(folder + "\\")

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
            self.names.update(netlog.dns_names())
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


def dns_loop(enforcer: Enforcer):
    """DNS filter for the protection lists: keep the lists loaded (a big list takes a few seconds, so not in the
    2-second loop) and the network adapters pointed at the filter; restore them if every list is off."""
    db = Database()
    safe = {"search": False, "youtube": False}   # forced SafeSearch / YouTube Restricted (Protection tab), read every 2 s
    server = dnsfilter.Server(enforcer.protection.which, log,
                              safe=lambda name: keywords.safe_target(name, safe["search"], safe["youtube"]))
    try:
        server.start()
    except OSError as e:   # port 53 taken by another program: never point Windows at a filter that isn't there
        log.error("DNS filter couldn't start (%s) - protection lists are not enforced", e)
        dnsfilter.restore(db, log)
        return
    last_adapters = 0.0
    while True:
        try:
            cfg = protection.settings(db)
            if enforcer.protection.refresh(cfg):
                log.info("Protection lists loaded: %d domains", enforcer.protection.count())
                hosts.flush_dns()   # a site you just allowed would otherwise stay blocked in the DNS cache
            kw = keywords.settings(db)
            safe["search"], safe["youtube"] = kw["safesearch"], kw["youtube"]
            if time.monotonic() - last_adapters >= DNS_ADAPTER_CHECK_SEC:
                last_adapters = time.monotonic()
                if enforcer.protection.count() or safe["search"] or safe["youtube"]:
                    server.upstreams = dnsfilter.point_to_filter(db, log)
                elif db.get_setting(dnsfilter.SAVED_KEY, "{}") != "{}":
                    dnsfilter.restore(db, log)
        except Exception:
            log.exception("DNS filter upkeep failed")
        time.sleep(INTERVAL_SEC)


def netlog_loop(enforcer: Enforcer):
    logger = NetworkLogger(Database(), enforcer.clock.now)
    while True:
        try:
            logger.tick()
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
        hosts.apply([])   # (the Lockdown section of the hosts file goes too)
        hosts.flush_dns()
        log.info("Browser policies, firewall rules, DNS filter and hosts-file entries removed")
        return 0
    enforcer = Enforcer(Database())
    if cmd == "once":
        enforcer.enforce_once()
        return 0
    log.info("Service started")
    BlockListener(enforcer.on_visit, log).start()
    threading.Thread(target=app_loop, args=(enforcer,), daemon=True).start()
    threading.Thread(target=netlog_loop, args=(enforcer,), daemon=True).start()
    threading.Thread(target=protection_loop, args=(enforcer,), daemon=True).start()
    threading.Thread(target=dns_loop, args=(enforcer,), daemon=True).start()
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            enforcer.enforce_once()
        except Exception:
            log.exception("Enforcement pass failed")
        stop.wait(INTERVAL_SEC)
    log.info("Service stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
