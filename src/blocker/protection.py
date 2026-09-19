"""Protection lists: always-on community lists of scam / phishing / malware / adult / gambling sites, kept apart from
your own blocklist (not affected by modes or the emergency unlock). The service downloads them once a day into
DATA_DIR/protection/<key>.txt (one domain per line, "*.domain" = the domain and all its subdomains) and blocks them
with its DNS filter (blocker/dnsfilter.py) - far too many for the hosts file. In memory each list is a sorted table of
64-bit hashes (a few MB for millions of domains), so a lookup takes microseconds.
Standard library only (runs in the service)."""
import json
import re
import time
import urllib.request
from array import array
from bisect import bisect_left
from datetime import datetime, timedelta
from pathlib import Path

from paths import DATA_DIR

HAGEZI = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/{}-onlydomains.txt"
BLP = "https://blocklistproject.github.io/Lists/alt-version/{}-nl.txt"
# key: (name, what it blocks, [(url, blocks subdomains too)], on by default)
LISTS = {
    "scam": ("Scam", "fake shops, fake support, crypto and other scams (Block List Project + HaGeZi)",
             [(BLP.format("scam"), False), (HAGEZI.format("fake"), True)], True),
    "phishing": ("Phishing", "fake log-in pages that steal passwords (Block List Project + Phishing Army)",
                 [(BLP.format("phishing"), False), ("https://phishing.army/download/phishing_army_blocklist.txt", False)],
                 True),
    "malware": ("Malware", "viruses, malware, crypto-miners and other threats (HaGeZi Threat Intelligence + URLhaus)",
                [(HAGEZI.format("tif"), True), ("https://urlhaus.abuse.ch/downloads/hostfile/", False)], True),
    "adult": ("Adult", "porn and other adult sites (Block List Project + HaGeZi)",
              [(BLP.format("porn"), False), (HAGEZI.format("nsfw"), True)], True),
    "gambling": ("Gambling", "betting and casino sites (HaGeZi)", [(HAGEZI.format("gambling"), True)], False),
}
LIST_DIR = DATA_DIR / "protection"
SETTINGS_KEY = "protection"   # JSON {"enabled": [keys], "allowed": [domains], "info": {key: {count, updated}},
#                                     "update_now": timestamp}
PROGRESS_KEY = "protection.progress"   # JSON {key, part, parts, done, total} while the service downloads, else ""
UPDATE_EVERY = timedelta(days=1)
RETRY_AFTER = timedelta(hours=1)
MAX_DOWNLOAD = 150 * 1024 * 1024
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_SKIP = {"localhost", "localhost.localdomain", "local", "broadcasthost", "0.0.0.0", "ip6-localhost",
         "ip6-loopback"}


def settings(db) -> dict:
    try:
        cfg = json.loads(db.get_setting(SETTINGS_KEY, "") or "{}")
    except ValueError:
        cfg = {}
    cfg.setdefault("enabled", [k for k, v in LISTS.items() if v[3]])
    cfg.setdefault("allowed", [])
    cfg.setdefault("info", {})
    return cfg


def save_settings(db, cfg: dict):
    db.set_setting(SETTINGS_KEY, json.dumps(cfg))


def parse(lines) -> list[str]:
    """Domains from hosts-file lines ("0.0.0.0 example.com") or a plain list; comments and localhost lines skipped."""
    out = []
    for line in lines:
        line = line.split("#", 1)[0].strip().lower()
        if not line:
            continue
        parts = line.split()
        for name in parts[1:] if len(parts) > 1 and parts[0][0].isdigit() else parts[:1]:
            if name not in _SKIP and _DOMAIN.match(name):
                out.append(name)
    return out


def list_path(key: str, folder: Path = LIST_DIR) -> Path:
    return folder / f"{key}.txt"


def download(key: str, folder: Path = LIST_DIR, progress=None) -> int:
    """Fetch a list's sources into folder/<key>.txt, streamed line by line (the big ones are ~50 MB).
    progress(part, parts, done_bytes, total_bytes) is called as it goes. Returns how many domains."""
    folder.mkdir(parents=True, exist_ok=True)
    tmp = list_path(key, folder).with_suffix(".tmp")
    sources = LISTS[key][2]
    count = 0
    with open(tmp, "w", encoding="utf-8") as out:
        for part, (url, wildcard) in enumerate(sources, 1):
            request = urllib.request.Request(url, headers={"User-Agent": "Lockdown/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                total, done = int(response.headers.get("Content-Length") or 0), 0
                for raw in response:
                    done += len(raw)
                    if done > MAX_DOWNLOAD:
                        raise ValueError(f"{LISTS[key][0]} list is unexpectedly big")
                    domains = parse([raw.decode("utf-8", errors="ignore")])
                    out.writelines(("*." if wildcard else "") + d + "\n" for d in domains)
                    count += len(domains)
                    if progress:
                        progress(part, len(sources), done, total)
    if not count:
        tmp.unlink()
        raise ValueError(f"{LISTS[key][0]} list came back empty")
    tmp.replace(list_path(key, folder))
    return count


def lists_with(host: str, keys, folder: Path = LIST_DIR) -> list[str]:
    """Which of the lists (keys) block `host` - for the GUI's "check a site" (reads the files, keeps nothing)."""
    names = {host, f"www.{host}"} | {"*." + ".".join(host.split(".")[i:]) for i in range(host.count("."))}
    found = []
    for key in keys:
        path = list_path(key, folder)
        if path.exists():
            text = "\n" + path.read_text(encoding="utf-8") + "\n"
            if any(f"\n{n}\n" in text for n in names):
                found.append(key)
    return found


def _time(d: dict, key: str) -> datetime | None:
    return datetime.fromisoformat(d[key]) if d.get(key) else None


def sources(key: str) -> str:
    return " ".join(url for url, _ in LISTS[key][2])


def due(cfg: dict, key: str, now: datetime) -> bool:
    """Needs downloading: never downloaded, a day old, its sources changed (a Lockdown update) or "Update now"
    pressed since - but after a failed try, wait an hour."""
    info = cfg["info"].get(key, {})
    updated, failed, asked = _time(info, "updated"), _time(info, "failed"), _time(cfg, "update_now")
    if failed and (not updated or failed > updated) and now - failed < RETRY_AFTER:
        return False
    if asked and (not updated or asked > updated) or updated and info.get("sources") != sources(key):
        return True
    return not updated or now - updated >= UPDATE_EVERY


def _table(values: list[int]) -> array:
    values.sort()
    return array("q", values)


def _has(table: array, value: int) -> bool:
    i = bisect_left(table, value)
    return i < len(table) and table[i] == value


class Protection:
    """The service's view: which list (if any) blocks a host. Tables are rebuilt only for lists whose file changed;
    "allowed anyway" applies at once (checked on every lookup)."""

    def __init__(self, folder: Path = LIST_DIR):
        self.folder = folder
        self.tables: dict[str, tuple[float, array, array]] = {}   # key -> (file time, exact hashes, wildcard hashes)
        self.allowed: frozenset[str] = frozenset()

    def refresh(self, cfg: dict) -> bool:
        """Load changed / newly enabled lists, drop disabled ones. Returns True when the lists changed."""
        self.allowed = frozenset(cfg["allowed"])
        tables, changed = {}, False
        for key in (k for k in LISTS if k in cfg["enabled"]):   # LISTS order: the first list that has it names it
            path = list_path(key, self.folder)
            stamp = path.stat().st_mtime if path.exists() else 0
            if key in self.tables and self.tables[key][0] == stamp:
                tables[key] = self.tables[key]
                continue
            exact, wild = [], []
            if stamp:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        d = line.rstrip("\n")
                        if d.startswith("*."):
                            wild.append(hash(d[2:]))
                        elif d:
                            exact.append(hash(d))
            tables[key], changed = (stamp, _table(exact), _table(wild)), True
        changed |= tables.keys() != self.tables.keys()
        self.tables = tables
        return changed

    def which(self, host: str) -> str | None:
        """The list a host is on (exact name, without www., or a listed parent domain for "*." entries), else
        None - also None when you allowed the host or one of its parent domains."""
        host = host.lower().rstrip(".")
        parts = host.split(".")
        suffixes = [".".join(parts[i:]) for i in range(len(parts) - 1)]   # host, parent, ... (not the bare TLD)
        if any(s in self.allowed for s in suffixes):
            return None
        exact = {hash(host), hash(host.removeprefix("www."))}
        wild = [hash(s) for s in suffixes]
        for key, (_stamp, exact_table, wild_table) in self.tables.items():
            if any(_has(exact_table, h) for h in exact) or any(_has(wild_table, h) for h in wild):
                return key
        return None

    def count(self) -> int:
        return sum(len(e) + len(w) for _s, e, w in self.tables.values())


def update_due_lists(db, now: datetime, folder: Path = LIST_DIR, log=None) -> bool:
    """Download what's due (runs in a background thread of the service). Returns True if a list changed."""
    cfg = settings(db)
    changed = False
    for key in cfg["enabled"]:
        if key not in LISTS or not due(cfg, key, now):
            continue
        last = 0.0

        def progress(part, parts, done, total, key=key):
            nonlocal last
            if time.monotonic() - last >= 0.5:
                last = time.monotonic()
                db.set_setting(PROGRESS_KEY, json.dumps({"key": key, "part": part, "parts": parts, "done": done,
                                                         "total": total}))
        try:
            count = download(key, folder, progress)
            cfg["info"][key] = {"count": count, "updated": now.isoformat(timespec="seconds"),   # (clears "failed")
                                "sources": sources(key)}
            changed = True
            if log:
                log.info("Protection list %s updated: %d domains", key, count)
        except Exception as e:   # offline, site down: keep the old copy, try again in an hour
            cfg["info"].setdefault(key, {})["failed"] = now.isoformat(timespec="seconds")
            if log:
                log.warning("Protection list %s not updated: %s", key, e)
        db.set_setting(PROGRESS_KEY, "")
        latest = settings(db)            # the GUI may have changed switches meanwhile: only update the info
        latest["info"] = cfg["info"]
        save_settings(db, latest)
    return changed


def download_progress(db) -> dict | None:
    """What the service is downloading right now, or None."""
    try:
        return json.loads(db.get_setting(PROGRESS_KEY, "") or "null")
    except ValueError:
        return None
