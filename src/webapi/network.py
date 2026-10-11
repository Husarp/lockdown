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
import threading
import time
from collections import Counter
from datetime import timedelta
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
WORD_CACHE_MAX = 40000
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
def rule_text(row: dict) -> str:
    """The Rule column: which rule blocked the visit; allowed rows show a dash."""
    if not row["blocked"]:
        return "–"
    reason = row.get("reason") or ""
    base = alerts.base_reason(reason)
    return alerts.REASONS[base][0] if base in alerts.REASONS else (base or reason).replace("_", " ").capitalize()


@core.internal
def readable(exe: str) -> str:
    stem = exe[:-4] if exe.endswith(".exe") else exe
    return stem[:1].upper() + stem[1:]


# ---------------------------------------------------------------- the cached last hour

class _View:
    """The rows that pass a set of filters (newest first) and the numbers worked out over them."""

    def __init__(self, rows: list, log: "_Log"):
        conns = blocked = 0
        sites = set()
        per_app, per_site, per_min, bmin = Counter(), Counter(), {}, set()
        for r in rows:
            c = r["count"]
            conns += c
            d, e, m = r["domain"], r["exe"], r["minute"]
            sites.add(d or r["ip"])
            if e:
                per_app[e] += c
            if d:
                per_site[d] += c
            per_min[m] = per_min.get(m, 0) + c
            if r["blocked"]:
                blocked += 1
                bmin.add(m)
        self.rows = rows
        self.connections, self.blocked, self.sites = conns, blocked, len(sites)
        self.per_app, self.per_site, self.per_min, self.blocked_minutes = per_app, per_site, per_min, bmin
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
        self.keys: dict = {}                # key -> row, for both kinds
        self.bkeys: dict = {}               # key -> row, blocked visits only
        self.rows: list = []
        self.exes_all: set = set()          # apps seen, with and without Windows' own
        self.exes_user: set = set()
        self.version = 0
        self.changes: list = []             # (version, row, is_new)
        self.dropped_upto = 0               # changes of versions up to this were forgotten
        self.views: dict = {}
        self.word_cache: dict = {}
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

    def _names(self, ctx):
        """The blocklist's names win; then the app catalog's (once it has loaded); else the file name made readable."""
        blocklist = {i["target"].lower(): i["display_name"] for i in ctx.db.list_items() if i["item_type"] == "app"}
        catalog = getattr(ctx, "catalog", None)
        mono = time.monotonic()
        if catalog is not None and mono - self.catalog_at >= NAMES_SEC and catalog.loaded.is_set():
            self.catalog_at = mono
            self.catalog = {}
            for app in catalog.search("all", "", limit=10 ** 6)["apps"]:
                self.catalog.setdefault(app["exe"], app["name"])
        if blocklist != self.blocklist:
            self.blocklist = blocklist
            self.word_cache.clear()
            self.views.clear()
            self.apps_cache.clear()

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

    def _read(self, ctx, force: bool):
        db = ctx.db
        now = ctx.now()
        cutoff = (now - KEEP).strftime(MIN_FMT)
        full = force or not self.loaded or self.last_now is None or now < self.last_now - timedelta(minutes=2)
        since = cutoff if full else max(cutoff, (self.last_now - timedelta(minutes=RECENT_MIN)).strftime(MIN_FMT))
        self._names(ctx)
        changes = []                          # (row, is_new)
        changed = full and not self.loaded

        # connections: replace every minute from `since` on
        fresh: dict = {}
        old = {r["k"]: r for m, rows in self.net.items() if m >= since for r in rows}
        for minute, exe, ip, port, domain, count, windows, local in db.conn.execute(
                "SELECT minute, exe, ip, port, domain, count, windows, local FROM network_log WHERE minute >= ? "
                "ORDER BY minute DESC, count DESC", (since,)):
            key = f"{minute}|{exe}|{ip}|{port}"
            before = old.pop(key, None)
            if before is not None and before["count"] == count and before["domain"] == domain:
                row = before
            else:
                row = {"k": key, "minute": minute, "exe": exe, "ip": ip, "port": port, "domain": domain,
                       "count": count, "windows": windows, "local": local, "blocked": False, "reason": "", "i": 0}
                changes.append((row, before is None))
            fresh.setdefault(minute, []).append(row)
        if old or changes:
            changed = True
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
                row = {"k": key, "minute": e["timestamp"][:16], "exe": "", "ip": "", "port": "", "domain": e["hostname"],
                       "count": 1, "windows": 0, "local": 0, "blocked": True, "reason": e["reason"] or "", "i": e["id"]}
                changes.append((row, True))
                changed = True
            blk.setdefault(row["minute"], []).append(row)
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
        self.changes.extend((self.version, row, is_new) for row, is_new in changes)
        if len(self.changes) > MAX_CHANGES:
            cut = len(self.changes) - MAX_CHANGES // 2
            self.dropped_upto = self.changes[cut - 1][0]
            del self.changes[:cut]
        rows, keys, user, every = [], {}, set(), set()
        for minute in sorted(set(self.net) | set(self.blk), reverse=True):
            for r in self.net.get(minute, ()):
                rows.append(r)
                keys[r["k"]] = r
                e = r["exe"]
                if e:
                    every.add(e)
                    if not r["windows"]:
                        user.add(e)
            for r in self.blk.get(minute, ()):
                rows.append(r)
                keys[r["k"]] = r
        self.rows, self.keys, self.exes_all, self.exes_user = rows, keys, every, user
        self.views.clear()
        self.apps_cache.clear()

    def changes_since(self, version: int):
        """[(row, is_new)] changed after `version` (a row once, its latest state), or None if that is too long ago."""
        if version < self.dropped_upto:
            return None
        latest = {}
        for v, row, is_new in self.changes:
            if v > version:
                latest[row["k"]] = (row, is_new or latest.get(row["k"], (None, False))[1])
        return [latest[k] for k in latest if k in self.keys]

    # ------------------------------------------------------------ filtering
    def match(self, rows: list, f: Filters) -> list:
        if not f.windows and not f.local:
            rows = [r for r in rows if not (r["windows"] or r["local"])]
        else:
            if not f.windows:
                rows = [r for r in rows if not r["windows"]]
            if not f.local:
                rows = [r for r in rows if not r["local"]]
        if f.app:
            rows = [r for r in rows if r["exe"] == f.app]
        if f.status == "allowed":
            rows = [r for r in rows if not r["blocked"]]
        elif f.status == "blocked":
            rows = [r for r in rows if r["blocked"]]
        if f.query:
            rows = self._search(rows, f.query)
        return rows

    def _search(self, rows: list, query: str) -> list:
        """Every word must be found in domain / exe / app name - exactly or with a small typo (search.score) - or
        exactly in the address. The typo check runs once per (site, app), not once per row."""
        words = search._WORD.findall(search.normalize(query))
        if not words:
            return rows
        cache, wc, out = {}, self.word_cache, []
        if len(wc) > WORD_CACHE_MAX:
            wc.clear()
        for r in rows:
            group = (r["domain"], r["exe"])
            flags = cache.get(group)
            if flags is None:
                text = f"{group[0]} {group[1]} {self.app_name(group[1])}"
                flags = []
                for w in words:
                    ok = wc.get((w, group))
                    if ok is None:
                        ok = wc[(w, group)] = search.score(w, text) is not None
                    flags.append(ok)
                flags = cache[group] = tuple(flags)
            if all(flags):
                out.append(r)
            else:
                ip = r["ip"]
                if ip and all(ok or w in ip for ok, w in zip(flags, words)):
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
                    v = self.views[key] = _View(self.match(self.rows, f), self)
        return v

    def apps(self, windows: bool) -> list:
        """The app menu: [{exe, name}] sorted by name (Windows' own only when that switch is on)."""
        found = self.apps_cache.get(windows)
        if found is None:
            exes = self.exes_all if windows else self.exes_user
            found = self.apps_cache[windows] = sorted(({"exe": e, "name": self.app_name(e)} for e in exes),
                                                      key=lambda a: (a["name"].lower(), a["exe"]))
        return found

    # ------------------------------------------------------------ what the page gets
    def row_json(self, r: dict) -> dict:
        host = r["domain"]
        return {"k": r["k"], "m": r["minute"], "t": r["minute"][11:], "exe": r["exe"],
                "app": "Blocked visit" if r["blocked"] else self.app_name(r["exe"]), "host": host,
                "site": host or r["ip"], "n": r["count"], "rule": rule_text(r), "port": r["port"],
                "b": 1 if r["blocked"] else 0, "w": r["windows"], "l": r["local"], "i": r["i"],
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
            wanted.sort(key=lambda r: (r["minute"], -r["blocked"], r["count"], r["i"]), reverse=True)
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
    """A text cell that a spreadsheet will not take for a formula."""
    text = str(text)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@core.internal
def csv_rows(rows: list, log: _Log):
    yield CSV_HEAD
    for r in rows:
        yield [r["minute"], "Blocked visit" if r["blocked"] else log.app_name(r["exe"]), _cell(r["exe"]),
               _cell(r["domain"]), _cell(r["ip"]), r["port"], r["count"], "blocked" if r["blocked"] else "allowed"]


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
