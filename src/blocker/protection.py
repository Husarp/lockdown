"""Protection lists: always-on community lists of scam / phishing / malware / adult / gambling sites, kept apart from
your own blocklist (not affected by modes or the emergency unlock). The service downloads them once a day into
DATA_DIR/protection/<key>.txt (one domain per line); they are blocked through the hosts file.
Standard library only (runs in the service)."""
import json
import re
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from paths import DATA_DIR

LISTS = {   # key: (name, what it blocks, url, on by default)
    "scam": ("Scam", "fake shops, fake support, crypto and other scams",
             "https://blocklistproject.github.io/Lists/alt-version/scam-nl.txt", True),
    "phishing": ("Phishing", "fake log-in pages that steal passwords",
                 "https://phishing.army/download/phishing_army_blocklist.txt", True),
    "malware": ("Malware", "sites spreading viruses and other malware (abuse.ch URLhaus)",
                "https://urlhaus.abuse.ch/downloads/hostfile/", True),
    "adult": ("Adult", "porn and other adult sites (StevenBlack)",
              "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn-only/hosts", True),
    "gambling": ("Gambling", "betting and casino sites (StevenBlack)",
                 "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/gambling-only/hosts", False),
}
LIST_DIR = DATA_DIR / "protection"
SETTINGS_KEY = "protection"   # JSON {"enabled": [keys], "allowed": [domains], "info": {key: {count, updated}},
#                                     "update_now": timestamp}
UPDATE_EVERY = timedelta(days=1)
RETRY_AFTER = timedelta(hours=1)
MAX_DOWNLOAD = 20 * 1024 * 1024
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


def parse(text: str) -> list[str]:
    """Domains from a hosts file ("0.0.0.0 example.com") or a plain list; comments and localhost lines skipped."""
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if not line:
            continue
        parts = line.split()
        for name in parts[1:] if len(parts) > 1 and parts[0][0].isdigit() else parts[:1]:
            if name not in _SKIP and _DOMAIN.match(name):
                out.append(name)
    return list(dict.fromkeys(out))


def list_path(key: str, folder: Path = LIST_DIR) -> Path:
    return folder / f"{key}.txt"


def download(key: str, folder: Path = LIST_DIR) -> int:
    """Fetch a list, keep its domains in folder/<key>.txt. Returns how many domains."""
    request = urllib.request.Request(LISTS[key][2], headers={"User-Agent": "Lockdown/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read(MAX_DOWNLOAD).decode("utf-8", errors="ignore")
    domains = parse(text)
    if not domains:
        raise ValueError(f"{LISTS[key][0]} list came back empty")
    folder.mkdir(parents=True, exist_ok=True)
    tmp = list_path(key, folder).with_suffix(".tmp")
    tmp.write_text("\n".join(domains), encoding="utf-8")
    tmp.replace(list_path(key, folder))
    return len(domains)


def load(key: str, folder: Path = LIST_DIR) -> list[str]:
    path = list_path(key, folder)
    return path.read_text(encoding="utf-8").split() if path.exists() else []


def _time(d: dict, key: str) -> datetime | None:
    return datetime.fromisoformat(d[key]) if d.get(key) else None


def due(cfg: dict, key: str, now: datetime) -> bool:
    """Needs downloading: never downloaded, a day old, or "Update now" pressed since - but after a failed try,
    wait an hour."""
    info = cfg["info"].get(key, {})
    updated, failed, asked = _time(info, "updated"), _time(info, "failed"), _time(cfg, "update_now")
    if failed and (not updated or failed > updated) and now - failed < RETRY_AFTER:
        return False
    if asked and (not updated or asked > updated):
        return True
    return not updated or now - updated >= UPDATE_EVERY


class Protection:
    """The service's view: which domains to block, reloaded when the lists or settings change."""

    def __init__(self, folder: Path = LIST_DIR):
        self.folder = folder
        self.domains: list[str] = []
        self.owner: dict[str, str] = {}    # domain -> list key (for "it's on the scam list")
        self._key = None

    def refresh(self, cfg: dict) -> bool:
        """Rebuild the domain list if needed. Returns True when it changed."""
        stamps = tuple((k, list_path(k, self.folder).stat().st_mtime if list_path(k, self.folder).exists() else 0)
                       for k in cfg["enabled"] if k in LISTS)
        key = (stamps, tuple(sorted(cfg["allowed"])))
        if key == self._key:
            return False
        self._key = key
        allowed = set(cfg["allowed"]) | {"www." + a for a in cfg["allowed"]}
        owner = {}
        for k, _ in stamps:
            for d in load(k, self.folder):
                if d not in allowed:
                    owner.setdefault(d, k)
        self.owner = owner
        self.domains = sorted(owner)
        return True

    def which(self, host: str) -> str | None:
        """The list a host is on (exact name or without www.), else None."""
        return self.owner.get(host) or self.owner.get(host.removeprefix("www."))


def update_due_lists(db, now: datetime, folder: Path = LIST_DIR, log=None) -> bool:
    """Download what's due (runs in a background thread of the service). Returns True if a list changed."""
    cfg = settings(db)
    changed = False
    for key in cfg["enabled"]:
        if key not in LISTS or not due(cfg, key, now):
            continue
        try:
            count = download(key, folder)
            cfg["info"][key] = {"count": count, "updated": now.isoformat(timespec="seconds")}   # (clears "failed")
            changed = True
            if log:
                log.info("Protection list %s updated: %d domains", key, count)
        except Exception as e:   # offline, site down: keep the old copy, try again in an hour
            cfg["info"].setdefault(key, {})["failed"] = now.isoformat(timespec="seconds")
            if log:
                log.warning("Protection list %s not updated: %s", key, e)
        latest = settings(db)            # the GUI may have changed switches meanwhile: only update the info
        latest["info"] = cfg["info"]
        save_settings(db, latest)
    return changed
