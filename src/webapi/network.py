"""Api for the Network Log (BLUEPRINT 3.6): which app connected to which site in the last hour.

The service writes `network_log` (one row per minute / app / address, kept an hour) and the blocker writes
`block_events`. This module keeps ONE cached copy of the last hour per agent (`_Log`): rows = connections + blocked
visits (not apps closed), newest first. A refresh happens at most every 3 s and only re-reads the last few minutes
(the service only ever writes the current minute), so opening the page never walks 20 000 rows of SQL again.
Filtering, the fuzzy search, the summary and the graph numbers are worked out here, once per (cache version,
filters); the page only paints what it gets - 100 rows at a time.

Live: `network_get(..., live=True)` registers the page's filters with the `_Feed`; the NetLogWorker
(resident/workers.py) calls `_Feed.tick()` every 3 s while the page is shown and the window is visible, and the feed
pushes ONLY the new / changed rows (`net_rows`), the new totals and the new summary. Nothing is sent or read while the
window is hidden.

Nothing here can loosen a block. "Block site..." / "Block app..." only open Blocking > Add filled in (the draft and the
challenge are Blocking's); `network_block_target` just names what to fill in. Exporting writes a file the owner picked in
Windows' own Save dialog (the page never says where) and is classified `ui`.
"""
import csv
import functools
import re
import threading
import time
from collections import Counter
from datetime import timedelta
from itertools import groupby
from operator import itemgetter
from typing import NamedTuple

import alerts
import search
from importer.popular import POPULAR_SITES

from . import core
from .core import ApiError

MIN_FMT = "%Y-%m-%d %H:%M"
KEEP = timedelta(hours=1)           # the service keeps one hour
REFRESH_SEC = 3.0                   # the cache is re-read at most this often
RECENT_MIN = 3                      # a refresh re-reads this many minutes back (the service writes the current minute)
PAGE = 100                          # rows the page shows at first / adds with "Show more"
MAX_LIMIT = 20000
MAX_QUERY = 100
MAX_CHANGES = 3000                  # the log of changed rows a feed catches up from (older: the page reloads)
VIEWS_KEPT = 6
WORD_CACHE_MAX = 400                # words whose matching groups are kept
GROUPS_MAX = 30000                  # kinds of rows remembered before the ones that are gone are forgotten
RESTART_AFTER = timedelta(minutes=15)   # not looked at for this long: read the hour again from scratch
STATUSES = ("all", "allowed", "blocked")
CSV_HEAD = ["time", "app", "exe", "site", "ip", "port", "connections", "status"]
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}      # example.co.uk -> three labels
NAMES_SEC = 60                      # how long the app catalog's names are kept


class Filters(NamedTuple):
    status: str = "all"
    app: str = ""
    query: str = ""
    windows: bool = False
    local: bool = False


@core.internal
def parse_filters(raw) -> Filters:
    """The page's filters, checked: {status: all|allowed|blocked, app: exe, query: text, windows: bool, local: bool}."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ApiError("Bad request: the filters must be a list of settings.")
    status, app, query = raw.get("status", "all"), raw.get("app") or "", raw.get("query") or ""
    if status not in STATUSES:
        raise ApiError("Bad request: unknown status filter.")
    if not isinstance(app, str) or len(app) > 260 or not isinstance(query, str):
        raise ApiError("Bad request: the app and the search must be text.")
    return Filters(status, app, query.strip()[:MAX_QUERY], bool(raw.get("windows")), bool(raw.get("local")))


@core.internal
def parse_limit(limit) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ApiError("Bad request: the number of rows must be a whole number.")
    return min(limit, MAX_LIMIT)


# ---------------------------------------------------------------- texts (gui/network.py, without the Tk import)

@functools.lru_cache(maxsize=4096)
def site_to_block(host: str) -> str:
    """The domain to block for a host seen in the log (rr3.googlevideo.com -> googlevideo.com)."""
    known = popular_hosts(host)
    if known:
        return known[1][0]
    labels = host.removeprefix("www.").split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


@core.internal
def popular_hosts(host: str):
    for sites in POPULAR_SITES.values():
        for name, hostnames in sites.items():
            if host in hostnames:
                return name, hostnames
    return None


@core.internal
def guess_name(domain: str) -> str:
    """A name for a site that is not in the popular list: its own label (example.co.uk -> Example)."""
    entry = popular_hosts(domain)
    if entry:
        return entry[0]
    labels = domain.split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL else 2
    return labels[-keep].capitalize() if len(labels) >= keep else domain.capitalize()


@core.internal
def rule_text(row: tuple) -> str:
    """The Rule column: which rule blocked the visit; allowed rows show a dash."""
    if not row[BLK]:
        return "–"
    reason = row[REASON] or ""
    base = alerts.base_reason(reason)
    return alerts.REASONS[base][0] if base in alerts.REASONS else (base or reason).replace("_", " ").capitalize()


@core.internal
def readable(exe: str) -> str:
    stem = exe[:-4] if exe.endswith(".exe") else exe
    return stem[:1].upper() + stem[1:]


# ---------------------------------------------------------------- the cached last hour
#
# A row is a plain tuple (cheap to build 20 000 of at once, and to keep): K the key the page knows it by, MIN the minute,
# EXE, IP, PORT, DOM the host name, CNT how many connections, WIN / LOC Windows' own / local network, BLK a blocked visit,
# REASON, ID the block event's id, GRP = (host name, exe) - ONE shared tuple per kind of row, which is what the search
# works on (it looks at the few thousand different groups, never at every row).

K, MIN, EXE, IP, PORT, DOM, CNT, WIN, LOC, BLK, REASON, ID, GRP = range(13)
NET_SQL = ("SELECT minute, exe, ip, port, domain, count, windows, local FROM network_log WHERE minute >= ? "
           "ORDER BY minute DESC, count DESC")
_HEX = re.compile(r"[0-9a-f]+\Z")      # (the only words that can be part of an address)


def _mask(word: str) -> int:
    """The letters / digits of a word as bits: a word can only be a typo of a token that has nearly all of them."""
    bits = 0
    for c in word:
        bits |= 1 << (ord(c) - 97 if c >= "a" else 26 + ord(c) - 48)
    return bits


class _View:
    """The rows that pass a set of filters (newest first) and the numbers worked out over them. `blocked` = the blocked
    visits among them."""

    def __init__(self, rows: list, blocked: list, log: "_Log"):
        per_group, per_min, ips = {}, {}, set()
        get_group, get_min = per_group.get, per_min.get
        for r in rows:
            c, g, m = r[CNT], r[GRP], r[MIN]
            per_group[g] = get_group(g, 0) + c
            per_min[m] = get_min(m, 0) + c
            if not g[0]:
                ips.add(r[IP])
        per_app, per_site, sites = Counter(), Counter(), set(ips)
        for (d, e), c in per_group.items():
            if e:
                per_app[e] += c
            if d:
                per_site[d] += c
                sites.add(d)
        self.rows = rows
        self.connections, self.blocked, self.sites = sum(per_min.values()), len(blocked), len(sites)
        self.per_app, self.per_site, self.per_min = per_app, per_site, per_min
        self.blocked_minutes = {r[MIN] for r in blocked}
        top = per_app.most_common(1)
        self.top = log.app_name(top[0][0]) if top else ""

    def summary(self) -> dict:
        text = (f"Last hour: {self.connections} connections · {self.blocked} blocked · {self.sites} sites · "
                f"{len(self.per_app)} apps" + (f" · top: {self.top}" if self.top else ""))
        return {"text": text, "connections": self.connections, "blocked": self.blocked, "sites": self.sites,
                "apps": len(self.per_app), "top": self.top}


class _Log:
    """The last hour: `rows` (newest first) is replaced whole on every change, never edited, so readers need no lock."""

    def __init__(self):
        self.lock = threading.RLock()
        self.loaded = False
        self.fetched = 0.0
        self.last_now = None
        self.cutoff = ""                    # the oldest minute kept
        self.net: dict = {}                 # minute -> [row] (the order SQL gave: busiest first)
        self.blk: dict = {}                 # minute -> [row] (newest first)
        self.bkeys: dict = {}               # key -> row, blocked visits only
        self.rows: list = []
        self.blocked_rows: list = []        # (the blocked visits among `rows`)
        self._keys = None                   # (rows, {their keys}) - only the live feed needs it
        self.version = 0
        self.changes: list = []             # (version, row, is_new)
        self.dropped_upto = 0               # changes of versions up to this were forgotten
        self.views: dict = {}
        self.bases: dict = {}               # (windows, local) -> rows with those switches applied
        self.gi: dict = {}                  # (host, exe) -> THE group tuple
        self.glist: list = []               # the groups in the order they came
        self.word_sets: dict = {}           # word -> [groups it matches, how many of glist were looked at]
        self.gtext: dict = {}               # group -> (normalised text, [(token, mask)]) the typo check reads
        self.blocklist: dict = {}           # exe -> the blocklist's name for it
        self.catalog: dict = {}
        self.catalog_at = -1e9
        self.apps_cache: dict = {}

    # ------------------------------------------------------------ names
    def app_name(self, exe: str) -> str:
        if not exe:
            return "Browser"
        if exe == "pythonw.exe":                         # Lockdown's own window
            return "Lockdown"
        return self.blocklist.get(exe) or self.catalog.get(exe) or readable(exe)

    def _names_changed(self):
        self.word_sets.clear()
        self.gtext.clear()
        self.views.clear()
        self.apps_cache.clear()

    def _names(self, ctx):
        """The blocklist's names win; then the app catalog's (`catalog_names`); else the file name made readable."""
        blocklist = {i["target"].lower(): i["display_name"] for i in ctx.db.list_items() if i["item_type"] == "app"}
        if blocklist != self.blocklist:
            self.blocklist = blocklist
            self._names_changed()

    def catalog_names(self, ctx) -> bool:
        """(a worker's thread, never a page's call: the catalog's list is long and asking it looks at the open windows)
        Take the app catalog's names, at most once a NAMES_SEC. False while the catalog has not loaded yet."""
        catalog = getattr(ctx, "catalog", None)
        if catalog is None or not catalog.loaded.is_set():
            return False
        mono = time.monotonic()
        if mono - self.catalog_at >= NAMES_SEC:
            names = {}
            for app in catalog.search("all", "", limit=10 ** 6)["apps"]:
                names.setdefault(app["exe"], app["name"])
            with self.lock:
                self.catalog_at = mono
                if names != self.catalog:
                    self.catalog = names
                    self._names_changed()
        return True

    # ------------------------------------------------------------ reading the database
    def refresh(self, ctx, force: bool = False) -> int:
        """Bring the cache up to date (at most every REFRESH_SEC unless `force`); returns the version."""
        if not force and self.loaded and time.monotonic() - self.fetched < REFRESH_SEC:
            return self.version
        with self.lock:
            if not force and self.loaded and time.monotonic() - self.fetched < REFRESH_SEC:
                return self.version
            self._read(ctx, force)
            self.fetched = time.monotonic()
            return self.version

    def _group(self, domain, exe):
        key = (domain, exe)
        g = self.gi.get(key)
        if g is None:
            g = self.gi[key] = key
            self.glist.append(key)
        return g

    def _read(self, ctx, force: bool):
        db = ctx.db
        now = ctx.now()
        cutoff = (now - KEEP).strftime(MIN_FMT)
        # a first read, or one after a long pause, starts over: no comparing with what was cached, no log of changes
        # (a live feed that was behind simply asks the page to reload)
        start_over = (not self.loaded or self.last_now is None or now - self.last_now > RESTART_AFTER)
        full = force or start_over or now < self.last_now - timedelta(minutes=2)
        since = cutoff if full else max(cutoff, (self.last_now - timedelta(minutes=RECENT_MIN)).strftime(MIN_FMT))
        self._names(ctx)
        changes = []                          # (row, is_new)
        changed = start_over

        # connections: replace every minute from `since` on
        old = {} if start_over else {r[K]: r for m, rows in self.net.items() if m >= since for r in rows}
        group, made, found = self._group, [], db.conn.execute(NET_SQL, (since,))
        if start_over:
            known = self.gi.get
            made = [(f"{m}|{e}|{ip}|{p}", m, e, ip, p, d, c, w, l, False, "", 0, known((d, e)) or group(d, e))
                    for m, e, ip, p, d, c, w, l in found]
        else:
            for minute, exe, ip, port, domain, count, windows, local in found:
                key = f"{minute}|{exe}|{ip}|{port}"
                before = old.pop(key, None)
                if before is not None and before[CNT] == count and before[DOM] == domain:
                    made.append(before)
                    continue
                row = (key, minute, exe, ip, port, domain, count, windows, local, False, "", 0, group(domain, exe))
                made.append(row)
                changes.append((row, before is None))
        fresh = {minute: list(rows) for minute, rows in groupby(made, itemgetter(MIN))}
        if old or changes:
            changed = True
        if start_over:
            self.net = fresh
        else:
            for minute in [m for m in self.net if m >= since]:
                del self.net[minute]
            self.net.update(fresh)

        # blocked visits (a few rows: read them all)
        blk, bkeys = {}, {}
        for e in db.block_events_since(now - KEEP):
            if alerts.is_app_event(e):
                continue
            key = f"b{e['id']}"
            row = self.bkeys.get(key)
            if row is None:
                host = e["hostname"]
                row = (key, e["timestamp"][:16], "", "", "", host, 1, 0, 0, True, e["reason"] or "", e["id"], group(host, ""))
                if not start_over:
                    changes.append((row, True))
                changed = True
            blk.setdefault(row[MIN], []).append(row)
            bkeys[key] = row
        if bkeys.keys() != self.bkeys.keys():
            changed = True                    # (a new visit, or one that has aged out)
        self.blk, self.bkeys = blk, bkeys

        # the hour moves on
        for store in (self.net, self.blk):
            for minute in [m for m in store if m < cutoff]:
                del store[minute]
                changed = True
        self.last_now, self.cutoff, self.loaded = now, cutoff, True
        if not changed:
            return
        self.version += 1
        if start_over:
            self.changes.clear()
            self.dropped_upto = self.version
        else:
            self.changes.extend((self.version, row, is_new) for row, is_new in changes)
            if len(self.changes) > MAX_CHANGES:
                cut = len(self.changes) - MAX_CHANGES // 2
                self.dropped_upto = self.changes[cut - 1][0]
                del self.changes[:cut]
        rows, net, blk = [], self.net, self.blk
        for minute in sorted(net.keys() | blk.keys(), reverse=True):
            rows += net.get(minute, ())
            rows += blk.get(minute, ())
        self.rows = rows
        self.blocked_rows = [r for minute in sorted(blk, reverse=True) for r in blk[minute]]
        self.views.clear()
        self.bases.clear()
        self.apps_cache.clear()
        if len(self.glist) > GROUPS_MAX:
            self._forget_groups()

    def _forget_groups(self):
        """Groups of rows that are long gone are dropped (the rows keep theirs)."""
        live = {r[GRP]: r[GRP] for r in self.rows}
        self.gi, self.glist = live, list(live)
        self.word_sets.clear()
        self.gtext.clear()

    def changes_since(self, version: int):
        """[(row, is_new)] changed after `version` (a row once, its latest state), or None if that is too long ago."""
        if version < self.dropped_upto:
            return None
        latest = {}
        for v, row, is_new in self.changes:
            if v > version:
                latest[row[K]] = (row, is_new or latest.get(row[K], (None, False))[1])
        rows = self.rows
        found = self._keys
        if found is None or found[0] is not rows:
            found = self._keys = (rows, {r[K] for r in rows})
        return [latest[k] for k in latest if k in found[1]]

    # ------------------------------------------------------------ filtering
    def base(self, windows: bool, local: bool) -> list:
        """The rows with Windows' own / the local network's left out unless their switch is on."""
        key = (windows, local)
        found = self.bases.get(key)
        if found is None:
            rows = self.rows
            if windows and local:
                found = rows
            elif windows:
                found = [r for r in rows if not r[LOC]]
            elif local:
                found = [r for r in rows if not r[WIN]]
            else:
                found = [r for r in rows if not (r[WIN] or r[LOC])]
            self.bases[key] = found
        return found

    def match(self, rows: list, f: Filters, switches: bool = True) -> list:
        """The rows that pass the filters (`switches`=False: the Windows / local switches were applied already)."""
        if switches and not (f.windows and f.local):
            if not f.windows and not f.local:
                rows = [r for r in rows if not (r[WIN] or r[LOC])]
            elif not f.windows:
                rows = [r for r in rows if not r[WIN]]
            else:
                rows = [r for r in rows if not r[LOC]]
        if f.app:
            rows = [r for r in rows if r[EXE] == f.app]
        if f.status == "allowed":
            rows = [r for r in rows if not r[BLK]]
        elif f.status == "blocked":
            rows = [r for r in rows if r[BLK]]
        if f.query:
            rows = self._search(rows, f.query)
        return rows

    def _word_ok(self, word: str, group: tuple) -> bool:
        """search.score(word, "<site> <exe> <app name>") is not None, with the text worked out once per group."""
        found = self.gtext.get(group)
        if found is None:
            text = search.normalize(f"{group[0]} {group[1]} {self.app_name(group[1])}")
            found = self.gtext[group] = (text, [(t, _mask(t)) for t in search._WORD.findall(text)])
        text, tokens = found
        if word in text:
            return True
        allowed = search.typos_allowed(word)
        if not allowed:
            return False
        n, mq = len(word), _mask(word)
        for token, mt in tokens:
            if (mq & ~mt).bit_count() > allowed:
                continue
            for cand in {token, token[:n], token[:n + 1], token[:max(n - 1, 1)]}:
                if search.distance(word, cand, allowed) <= allowed:
                    return True
        return False

    def _groups_with(self, word: str) -> set:
        """The groups that have the word (exactly or with a small typo), worked out once per word and group."""
        if len(self.word_sets) > WORD_CACHE_MAX:
            self.word_sets.clear()
        entry = self.word_sets.get(word)
        if entry is None:
            entry = self.word_sets[word] = [set(), 0]
        found, done = entry
        glist = self.glist
        if done < len(glist):
            upto = len(glist)
            for g in glist[done:upto]:
                if self._word_ok(word, g):
                    found.add(g)
            entry[1] = upto
        return found

    def _search(self, rows: list, query: str) -> list:
        """Every word must be found in domain / exe / app name - exactly or with a small typo (search.score) - or
        exactly in the address (only words that could be part of one are tried there)."""
        words = search._WORD.findall(search.normalize(query))
        if not words:
            return rows
        sets = [(w, self._groups_with(w)) for w in words]
        text_sets = [s for w, s in sets if not _HEX.match(w)]
        addr_sets = [(w, s) for w, s in sets if _HEX.match(w)]
        strict = set.intersection(*text_sets) if text_sets else None
        if not addr_sets:
            return [r for r in rows if r[GRP] in strict]
        out = []
        for r in rows:
            g = r[GRP]
            if strict is not None and g not in strict:
                continue
            ip = r[IP]
            for w, s in addr_sets:
                if g not in s and w not in ip:
                    break
            else:
                out.append(r)
        return out

    def view(self, f: Filters) -> _View:
        key = f
        v = self.views.get(key)
        if v is None:
            with self.lock:
                v = self.views.get(key)
                if v is None:
                    if len(self.views) >= VIEWS_KEPT:
                        self.views.pop(next(iter(self.views)))
                    v = self.views[key] = _View(self.match(self.base(f.windows, f.local), f, False),
                                                self.match(self.blocked_rows, f), self)
        return v

    def apps(self, windows: bool) -> list:
        """The app menu: [{exe, name}] sorted by name (Windows' own only when that switch is on)."""
        found = self.apps_cache.get(windows)
        if found is None:
            rows = self.rows
            exes = {r[EXE] for r in rows if r[EXE]} if windows else {r[EXE] for r in rows if r[EXE] and not r[WIN]}
            found = self.apps_cache[windows] = sorted(({"exe": e, "name": self.app_name(e)} for e in exes),
                                                      key=lambda a: (a["name"].lower(), a["exe"]))
        return found

    # ------------------------------------------------------------ what the page gets
    def row_json(self, r: tuple) -> dict:
        host = r[DOM]
        return {"k": r[K], "m": r[MIN], "t": r[MIN][11:], "exe": r[EXE],
                "app": "Blocked visit" if r[BLK] else self.app_name(r[EXE]), "host": host,
                "site": host or r[IP], "n": r[CNT], "rule": rule_text(r), "port": r[PORT],
                "b": 1 if r[BLK] else 0, "w": r[WIN], "l": r[LOC], "i": r[ID],
                "reg": site_to_block(host) if host else ""}


# ---------------------------------------------------------------- the live feed

class _Feed:
    """What the page showing the Network Log asked to be told: its filters and the cache version it has."""

    def __init__(self, ctx, log: _Log):
        self.ctx, self.log = ctx, log
        self.lock = threading.Lock()
        self.on = False
        self.filters = Filters()
        self.version = 0
        self.epoch = 0

    def start(self, f: Filters, version: int) -> int:
        with self.lock:
            self.epoch += 1
            self.on, self.filters, self.version = True, f, version
            epoch = self.epoch
        hub = getattr(self.ctx, "hub", None)
        if hub is not None:
            hub.netlog_feed = self
            worker = getattr(hub, "netlog_worker", None)
            if worker is not None:
                worker.poke()
        return epoch

    def stop(self):
        with self.lock:
            self.on = False

    def tick(self) -> bool:
        """(the worker's thread, every 3 s) Push what changed since the page last heard. True if something was sent."""
        with self.lock:
            if not self.on:
                return False
            f, since, epoch = self.filters, self.version, self.epoch
        visible = getattr(self.ctx, "window_visible", None)
        if visible is not None and not visible():              # hidden: no reading, no pushing; the page reloads when shown
            self.stop()
            return False
        publish = getattr(self.ctx, "publish", None)
        if publish is None:
            return False
        log = self.log
        log.refresh(self.ctx)
        version = log.version
        if version == since:
            return False
        changed = log.changes_since(since)
        if changed is None:
            payload = {"epoch": epoch, "resync": True}
        else:
            wanted = log.match([row for row, _ in changed], f)
            wanted.sort(key=lambda r: (r[MIN], -r[BLK], r[CNT], r[ID]), reverse=True)
            v = log.view(f)
            payload = {"epoch": epoch, "seq": version, "rows": [log.row_json(r) for r in wanted], "total": len(v.rows),
                       "summary": v.summary(), "apps": log.apps(f.windows), "cutoff": log.cutoff}
        with self.lock:
            if self.epoch != epoch or not self.on:             # the page asked again meanwhile
                return False
            self.version = version
        publish("net_rows", payload)
        return True


_lock = threading.Lock()


def _log(ctx) -> _Log:
    found = getattr(ctx, "_netlog", None)
    if found is None:
        with _lock:
            found = getattr(ctx, "_netlog", None)
            if found is None:
                found = ctx._netlog = _Log()
    return found


def _feed(ctx) -> _Feed:
    found = getattr(ctx, "_netfeed", None)
    if found is None:
        with _lock:
            found = getattr(ctx, "_netfeed", None)
            if found is None:
                found = ctx._netfeed = _Feed(ctx, _log(ctx))
    return found


@core.internal
def prewarm(ctx):
    """(a worker's thread, once, a few seconds after the agent started) Read the last hour and work out the first thing the
    page asks for, so the first click on the Network Log finds it ready instead of reading 20 000 rows."""
    log = _log(ctx)
    log.refresh(ctx)
    log.view(Filters())
    log.apps(False)
    log.catalog_names(ctx)


@core.internal
def maintain(ctx) -> bool:
    """(a worker's thread, about once a minute while nobody uses the page) While the window is shown, keep the cache a
    minute old at most and the app catalog's names up to date; a hidden window costs nothing. False: the catalog has
    not loaded yet, look again soon."""
    log = _log(ctx)
    if getattr(ctx, "catalog", None) is None:
        return True
    visible = getattr(ctx, "window_visible", None)
    if visible is not None and not visible():
        return log.catalog_names(ctx) if log.catalog_at < 0 else True      # (the names: once, even hidden)
    log.refresh(ctx)
    return log.catalog_names(ctx)


# ---------------------------------------------------------------- the Api

@core.read
def network_get(ctx, filters=None, limit=PAGE, live=False):
    """The Network Log's table: the newest `limit` rows that pass the filters ({status, app, query, windows, local}), the
    summary line, the app menu and (live) a feed registration. {rows, total, older, summary, apps, empty, seq, epoch,
    cutoff}. Rows: {k, m (minute), t (HH:MM), exe, app, host, site, n, rule, port, b (blocked), w (Windows'), l (local),
    i, reg (the domain "Block site" would block)}."""
    f, limit = parse_filters(filters), parse_limit(limit)
    log = _log(ctx)
    version = log.refresh(ctx)               # (the version BEFORE the view: a change that lands meanwhile is sent again, never lost)
    v = log.view(f)
    epoch = _feed(ctx).start(f, version) if live is True else _stop(ctx)
    return {"rows": [log.row_json(r) for r in v.rows[:limit]], "total": len(v.rows), "older": max(0, len(v.rows) - limit),
            "summary": v.summary(), "apps": log.apps(f.windows), "empty": not log.rows, "seq": version,
            "epoch": epoch, "cutoff": log.cutoff}


def _stop(ctx):
    feed = getattr(ctx, "_netfeed", None)
    if feed is not None:
        feed.stop()
    return 0


@core.ui
def network_live_stop(ctx):
    """The page was left (or Live was switched off): stop the pushes."""
    _stop(ctx)


@core.read
def network_graph(ctx, filters=None):
    """The Graph tab: {bars: [60 x {label, value, tip, today, tone, show}] ready for charts.barChart, apps: top 8
    [{exe, name, n}], sites: top 8 [{site, n}]} over the rows that pass the filters."""
    f = parse_filters(filters)
    log = _log(ctx)
    log.refresh(ctx)
    v = log.view(f)
    now = ctx.now()
    bars = []
    for i in range(60):
        at = now - timedelta(minutes=59 - i)
        minute = at.strftime(MIN_FMT)
        n = v.per_min.get(minute, 0)
        blocked = minute in v.blocked_minutes
        hm = minute[11:]
        bars.append({"label": hm if at.minute % 10 == 0 else "", "value": n, "today": i == 59,
                     "tone": "block" if blocked else "", "show": at.minute % 10 == 0,
                     "tip": f"{hm}\n{n} connection{'s' * (n != 1)}" + ("\nBlocked visit" if blocked else "")})
    return {"bars": bars,
            "apps": [{"exe": e, "name": log.app_name(e), "n": n} for e, n in v.per_app.most_common(8)],
            "sites": [{"site": s, "n": n} for s, n in v.per_site.most_common(8)]}


@core.read
def network_block_target(ctx, kind, value):
    """What "Block site..." / "Block app..." fills Blocking > Add with: kind "site" + a host -> {site: [name, domain]};
    kind "app" + an exe -> {app: {name, exe, path}}. It changes nothing."""
    if kind not in ("site", "app") or not isinstance(value, str) or not 0 < len(value) <= 260:
        raise ApiError("Bad request: nothing to block.")
    if kind == "site":
        domain = site_to_block(value.lower())
        known = popular_hosts(domain)
        return {"site": [known[0] if known else guess_name(domain), domain]}
    exe = value.lower()
    log = _log(ctx)
    log.refresh(ctx)
    path = None
    for item in ctx.db.list_items():
        if item["item_type"] == "app" and item["target"].lower() == exe:
            path = item.get("app_path") or None
    catalog = getattr(ctx, "catalog", None)
    if path is None and catalog is not None:
        path = catalog.path_for(exe)
    return {"app": {"name": log.app_name(exe), "exe": exe, "path": path}}


# ---------------------------------------------------------------- Export CSV

def _cell(text) -> str:
    """A text cell that a spreadsheet will not take for a formula (also one that starts with a tab or a line break)."""
    text = str(text)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@core.internal
def csv_rows(rows: list, log: _Log):
    yield CSV_HEAD
    for r in rows:
        yield [r[MIN], _cell(log.app_name(r[EXE])), _cell(r[EXE]), _cell(r[DOM]), _cell(r[IP]), r[PORT], r[CNT],
               "blocked" if r[BLK] else "allowed"]


@core.internal
def write_csv(path, rows: list, log: _Log):
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(csv_rows(rows, log))


def _ask_save_path(ctx):
    """Windows' Save dialog (the owner's choice; the page never says where). None when cancelled."""
    window = getattr(ctx, "main_window", None)
    if window is None:
        raise ApiError("The Save dialog isn't available.")
    import webview
    kind = webview.FileDialog.SAVE if hasattr(webview, "FileDialog") else webview.SAVE_DIALOG
    picked = window.create_file_dialog(kind, save_filename="network-log.csv", file_types=("CSV file (*.csv)",))
    if not picked:
        return None
    path = picked if isinstance(picked, str) else picked[0]
    return path if path.lower().endswith(".csv") else path + ".csv"


def _spawn(fn):
    threading.Thread(target=fn, name="lockdown-netlog-export", daemon=True).start()


@core.ui
def network_export_csv(ctx, filters=None):
    """Export the rows that pass the filters (all of them, not just the ones shown) to a .csv the owner picks in the Save
    dialog. The file is written on a worker; `net_export {ok, path, rows | error}` is pushed when it is done.
    Returns {saved: false} when cancelled, else {saved: true, path, rows}."""
    f = parse_filters(filters)
    path = _ask_save_path(ctx)
    if not path:
        return {"saved": False}
    log = _log(ctx)
    log.refresh(ctx, force=True)
    rows = list(log.view(f).rows)
    publish = getattr(ctx, "publish", None)

    def work():
        try:
            write_csv(path, rows, log)
            result = {"ok": True, "path": str(path), "rows": len(rows)}
        except OSError as error:
            result = {"ok": False, "path": str(path), "error": f"Couldn't save the file: {error.strerror or error}"}
        if publish is not None:
            publish("net_export", result)
    _spawn(work)
    return {"saved": True, "path": str(path), "rows": len(rows)}
