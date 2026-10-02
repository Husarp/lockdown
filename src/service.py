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

import antibypass
from blocker import apps, browser_policy, connections, dnsfilter, firewall, hosts, netlog, protection, site_block
import keywords
from blocker.listener import BlockListener
from db import Database
from monitor.usage import COUNTED_KEY, MAX_GAP_SEC, MEMBERS_KEY
from paths import DATA_DIR, LOG_PATH
import modes
from rules import counted_rules, usage_targets, visit_targets
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
QUIC_KEY = "firewall_quic"        # JSON {browser exe: path} of the "no QUIC" rules in place (update_quic)
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
    counting = False       # the service is counting app time itself (the tray app isn't, count_unwatched_apps)
    count_carry = 0.0
    count_mark = None      # the mark the service itself wrote last
    kill_failed: frozenset = frozenset()   # pids that couldn't be closed (logged once each)
    count_paths: dict = {}                 # pid -> (exe, lowercase path), for count_unwatched_apps
    quic: dict = {}                        # browser exe -> path with a "no QUIC" firewall rule (update_quic)
    family: dict = {}      # exe -> {pid: (name, start time)} it started from its own folder: they ARE the app
    members: dict = {}     # pid -> sorted exes of the listed apps it is part of, other than by name (publish_members)
    published: dict | None = None
    app_items: dict = {}   # exe -> item, for every app on the list (track_families)
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
        self.quic: dict[str, str] = json.loads(db.get_setting(QUIC_KEY, "{}"))
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
        all_blocks = [] if off else self.db.blocks(now)
        blocks, dns_blocks = {}, {}
        for b in all_blocks:
            if b["item"]["item_type"] == "site":
                for h in b["item"]["target"].split():
                    blocks.setdefault(h, b)
                    # a site set to "close the tab" only is not sent nowhere - the tray agent acts instead
                    if site_block.blocks_dns(b["item"].get("block_type")):
                        dns_blocks.setdefault(h, b)
        # (by the bare exe name: a target typed as a full path is still that exe - apps.exe_name; two blocks on
        # one exe become one that does what both ask, closing winning - apps.merge_blocks)
        app_blocks: dict[str, dict] = {}
        for b in all_blocks:
            exe = apps.exe_name(b["item"]["target"]) if b["item"]["item_type"] == "app" else ""
            if exe and exe not in apps.PROTECTED:
                app_blocks[exe] = apps.merge_blocks(app_blocks.get(exe), b)
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

        newly_blocked = sorted(set(dns_blocks) - set(self.dns_blocks))
        if newly_blocked:
            # resolve before the hosts file points them at 127.0.0.1
            for ip in connections.resolve(hosts.expand(newly_blocked)):
                self.closing[ip] = now + CLOSE_CONNECTIONS_FOR
            self.close_cached(newly_blocked, now)
        self.update_quic(bool(dns_blocks))

        # (the protection lists are not in the hosts file: Windows' DNS client hangs on huge hosts files - they're
        # blocked by the DNS filter, see dns_loop)
        if hosts.apply(sorted(dns_blocks)):
            hosts.flush_dns()
            log.info("Hosts file updated: %d hostnames blocked", len(dns_blocks))
        self.blocks, self.dns_blocks = blocks, dns_blocks

        kw = keywords.settings(self.db)
        if browser_policy.apply(safe_search=kw["safesearch"], youtube=kw["youtube"]):
            log.info("Browser policies (re)applied")

        self.cut_live_connections(now)
        self.closing = {ip: until for ip, until in self.closing.items() if until > now}
        if self.closing:
            closed = connections.close_to(set(self.closing))
            if closed:
                log.info("Closed %d open connection(s) to blocked sites: %s", len(closed),
                         ", ".join(f"{ip}:{port}" for ip, port in sorted(closed)))

        self.db.set_setting(HEARTBEAT_KEY, str(time.time()))

    def blocked_name(self, host: str) -> str | None:
        """What the DNS filter asks about every lookup: a site you blocked - the name itself or anything under
        it, which is how googlevideo.com covers rr1---sn-xxxx.googlevideo.com - or a protection list.
        Called from the filter's threads; self.dns_blocks is replaced whole, never edited in place."""
        if self.was_off:      # switched off: the filter still runs, it just blocks nothing at all
            return None
        host = host.lower().rstrip(".").removeprefix("www.")
        blocks = self.dns_blocks
        if blocks:
            parts = host.split(".")
            for i in range(len(parts) - 1):
                if ".".join(parts[i:]) in blocks:
                    return "blocked"
        return self.protection.which(host)

    def cut_live_connections(self, now: datetime):
        """A page that is already open keeps streaming over the connection it has, whatever the hosts file
        says. Anything open to a blocked name (by the name the app actually looked up) gets cut."""
        if not self.dns_blocks:
            return
        try:
            names = netlog.dns_names()
        except OSError:
            return
        for ip, name in names.items():
            # Windows loads the hosts file into its DNS cache, and this reads that cache back - so every name
            # we blocked answers 127.0.0.1 here. Taking that at face value put loopback on the kill list and
            # cut every local socket on the machine every couple of seconds.
            if self.blocked_name(name) and not connections.is_loopback(ip):
                self.closing[ip] = now + CLOSE_CONNECTIONS_FOR

    def close_cached(self, hostnames: list[str], now: datetime):
        """The addresses this PC itself looked the newly blocked names up as - read from the DNS cache BEFORE the
        hosts file is rewritten and the cache flushed. cut_live_connections reads the cache only after the flush,
        when the names it holds answer 127.0.0.1, so the open video stream (rr1---sn-....googlevideo.com - a name
        nobody can resolve in advance) was never on the list and an open YouTube page played on."""
        try:
            names = netlog.dns_names()
        except OSError:
            return
        wanted = set(hostnames)
        for ip, name in names.items():
            parts = name.lower().rstrip(".").removeprefix("www.").split(".")
            if any(".".join(parts[i:]) in wanted for i in range(len(parts) - 1)) and not connections.is_loopback(ip):
                self.closing[ip] = now + CLOSE_CONNECTIONS_FOR

    def update_quic(self, active: bool):
        """While any site is sent nowhere, browsers may not use QUIC (HTTP/3 over UDP 443): a firewall rule per
        browser. A QUIC connection can't be cut from outside like a TCP one, so a page open when its block
        began kept streaming over it - the QuicAllowed policy only covers Chrome and Edge, and only after a
        restart. Browsers fall back to TCP at once, which the service can cut. Removed when no site is blocked."""
        if active:
            wanted = dict(self.quic)
            for pid, name in apps.list_processes():
                if name in firewall.BROWSERS and name not in wanted and (path := apps.process_path(pid)):
                    wanted[name] = path
        else:
            wanted = {}
        if wanted == self.quic:
            return
        for exe in set(self.quic) - set(wanted):
            firewall.remove_quic(exe)
        for exe, path in wanted.items():
            if self.quic.get(exe) != path:
                firewall.add_quic(exe, path)
                log.info("QUIC (UDP 443) blocked for %s while sites are blocked", exe)
        self.quic = wanted
        self.db.set_setting(QUIC_KEY, json.dumps(wanted))

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

    # ---------- counting app time when the tray app doesn't ----------

    def count_unwatched_apps(self, now: datetime):
        """Backstop for the tray app's usage tracker. Only the tray can see which window is in front, so it does
        the counting - but when it isn't running, has frozen, or its tracker has stalled, nothing was counted
        at all: a daily limit never filled and the "N minutes allowed during blocked hours" never ran out, so
        the game played on for hours outside its allowed time. Now, once nothing has been counted for
        TRACKER_STALE_SEC, the service counts every listed app that is running (its own exe, what it started, or
        any program from its game folder), the category blockers those apps belong to (one pot per category, as
        the tray counts it), and app launches for opening limits - erring on the side of counting, never of
        losing time. Both write the same mark (COUNTED_KEY), so a stretch is counted by one of them, not by both
        and not by neither."""
        now_ts = now.timestamp()
        try:
            mark = float(self.db.get_setting(COUNTED_KEY) or 0)
        except ValueError:
            mark = 0.0
        gap = now_ts - mark
        if mark and abs(gap) <= TRACKER_STALE_SEC:   # counted a moment ago (by the tray, or by us)
            if self.counting and mark != self.count_mark:
                log.info("The tray app is counting usage again")
                self.counting = False
                self.count_running = None
            return
        if not self.counting:
            log.warning("The tray app isn't counting usage - the service counts running blocked apps instead")
            self.counting = True
        total = (min(gap, MAX_GAP_SEC) if mark and gap > 0 else INTERVAL_SEC) + self.count_carry
        seconds = int(total)
        self.count_carry = total - seconds
        procs = apps.list_processes_full()
        alive = {pid for pid, _ppid, _name in procs}
        self.count_paths = {pid: v for pid, v in self.count_paths.items() if pid in alive}
        running = {name for _pid, _ppid, name in procs}
        launched = running - self.count_running if self.count_running is not None else set()
        self.count_running = running
        members = {exe for pid, exes in self.members.items() if pid in alive for exe in exes}
        groups, clock, categories = self.db.list_groups(), self.db.limit_clock(), self.db.categories()
        items = self.db.list_items()
        used = []
        for item in items:
            if item["item_type"] != "app":
                continue
            rules = counted_rules(item, groups)
            if apps.names_of(item["target"]) & launched:   # opening limits ("launches")
                self.db.add_usage(visit_targets(rules, item, now, True, None, clock), 1, now.date())
            if apps.exe_name(item["target"]) in members or self._running(item, running, procs):
                used.append(item)
        # category blockers: the categories of the listed apps that run, and of any running program you put in one
        cats = {modes.item_category(i, categories) for i in used}
        cats |= {cat for (kind, name), cat in categories.items() if kind == "app" and name in running}
        used += [i for i in items if i["item_type"] == "category" and i["target"] in cats]
        for item in used:
            self.db.add_usage(usage_targets(counted_rules(item, groups), item["id"], now, clock), seconds, now.date())
        self.db.set_setting(COUNTED_KEY, f"{now_ts:.3f}")
        self.count_mark = float(f"{now_ts:.3f}")

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
        """Tell the tray app which running processes belong to a listed app other than by name - what the app
        started from its own folder (also after the starter has gone), or a program from its game folder. The
        tray counts the window in front; it can't follow who started what, and a game protected by anti-cheat
        won't even tell it its path, so before this the real game's window was not counted at all."""
        members = self.members
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
        family: dict[str, dict[int, tuple[str, float | None]]] = {}
        members: dict[int, set[str]] = {}
        folders: dict[str, set[str]] = {}
        me = os.getpid()
        for exe, item in self.app_items.items():
            kept = {pid: v for pid, v in self.family.get(exe, {}).items() if names.get(pid) == v[0]}
            main = set().union(*(by_name.get(n, ()) for n in apps.names_of(exe)))
            folder = apps.app_folder(item)
            if main:
                homes = {f for pid in main if (f := apps.helper_folder(self._path(pid, names[pid])))}
                if folder:
                    homes.add(folder)
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
        return {pid: exe for pid, exe in owned.items() if pid != me and names[pid] not in apps.PROTECTED}

    def _path(self, pid: int, name: str) -> str:
        """Lowercase exe path of a process ('' if unknown), looked up once per process."""
        cached = self.path_cache.get(pid)
        if not cached or cached[0] != name:
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
    server = dnsfilter.Server(enforcer.blocked_name, log,
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
        for exe in json.loads(db.get_setting(QUIC_KEY, "{}")):
            firewall.remove_quic(exe)
        db.set_setting(QUIC_KEY, "{}")
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
