"""Api for Blocking > Site protection (BLUEPRINT 3.3): the always-on lists, connecting a list, your own hand-made lists,
SafeSearch, the blocked words, "Check a site" and "Allowed anyway".

These settings are saved straight away (they do not use the Blocking draft): the protection setting (blocker/protection.py,
JSON "protection") and the words setting (keywords.py, JSON "words"). The page is untrusted. Everything that makes the
protection weaker is decided HERE, with the checks the old window used, never in JS:

- protection: a list switched off or removed while on, a site allowed anyway, a hand-made list that is on losing sites
  -> `antibypass.protection_looser(old, new)`;
- words: SafeSearch / YouTube / the check / a ready list / "your words" off, ready-list words turned off, your words
  removed, exceptions added, exceptions switched back ON -> `keywords.looser(old, new)`.
A change that loosens waits under a challenge ticket (`ctx.guard.request`): the page gets the ticket, the change is a
closure built here from validated input and runs only after Python has checked the challenge. Making things stricter is
free. The 10-second grace (an exact undo of a tightening needs no challenge) lives in the GuardService, per domain
"protection" and "keywords", and is keyed on the whole canonical state - including every site of every hand-made list,
so adding one site cannot buy a free removal of others.

Paths and addresses are restricted: a list address must be http(s); Connect only accepts the address that was previewed
(a server-side token); hosts are normalised here whatever the page sends; list keys must exist; word-list switches and
"off" words must be known values.
"""
import hashlib
import json
import logging
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

import antibypass
import keywords
from blocker import protection
from blocker.hosts import normalize_host

from . import core
from . import sites as site_list
from .core import ApiError

log = logging.getLogger("lockdown.protection")
WARM_DELAY_SEC = 10.0         # `start_warmup` waits this long: the window, the Network Log and Screen Time warm-ups come first

GRACE_PROTECTION, GRACE_WORDS = "protection", "keywords"
MAX_NAME = 60                 # a list name
MAX_ENTRY_NAME = 80           # the optional name of a site in your own list
MAX_ENTRIES = 100_000         # sites in one hand-made list
MAX_MANUAL, MAX_CUSTOM, MAX_ALLOWED = 100, 50, 2000
MAX_URL = 2000
MAX_WORDS, MAX_WORD = 5000, 80
PREVIEW_TTL_SEC = 600         # a good preview can be connected for this long
MAX_PREVIEWS = 2              # downloads running at once
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_KEY = re.compile(r"[a-z0-9]{1,30}")              # (fullmatch: "$" would let a trailing line break in)
OWN_LISTS = {"words": ("Your words", "a word or a few words"),
             "exceptions": ("Exceptions", "site (e.g. wikipedia.org) or word to ignore")}
WORD_FLAGS = ("enabled", "safesearch", "youtube", "words_on", "exceptions_on")

_lock = threading.RLock()              # one change to the protection setting at a time (read, change, write)
_words_lock = threading.RLock()
_previews: dict = {}                   # token -> {url, count, sample, created}: a good preview waiting for Connect
_running = [0]                         # previews being downloaded


# ---------------------------------------------------------------- reading the settings (parsed once per saved text)

class _Text:
    """Stands in for a Database when only the text of the setting is at hand (protection.settings(db) wants a db)."""

    def __init__(self, text):
        self.text = text

    def get_setting(self, key, default=None):
        return self.text


def _parse(text):
    return protection.settings(_Text(text))


def _hosts_by_list(text):
    return {m["key"]: frozenset(e["host"] for e in m["entries"] if e.get("host")) for m in _parse(text)["manual"]}


def _pairs_by_list(text):
    return {m["key"]: [[e["host"], e.get("name", "")] for e in m["entries"]] for m in _parse(text)["manual"]}


def _shared(db) -> dict:
    """The protection setting, parsed once per saved text. Shared: never change it (protection.settings(db) is the
    copy to change)."""
    return db.parsed(protection.SETTINGS_KEY, _parse, "")


def _manual_hosts(db) -> dict:
    return db.parsed(protection.SETTINGS_KEY, _hosts_by_list, "")


@core.internal
def info_rev(cfg: dict) -> str:
    """A short fingerprint of what the SERVICE writes (when each list was downloaded, "Update now"): the page reloads
    when it changes."""
    text = json.dumps([cfg.get("info", {}), cfg.get("update_now", "")], sort_keys=True)
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _rev_of_text(text):
    return info_rev(_parse(text))


@core.internal
def rev_of(db) -> str:
    """info_rev of the saved setting, worked out once per saved text (the agent's watcher asks every second)."""
    return db.parsed(protection.SETTINGS_KEY, _rev_of_text, "")


@core.internal
def download_text(p: dict) -> str:
    """"12.3 of 45.0 MB (part 1 of 2)" - the same words as the old window's gui/protection_tab.download_text."""
    big = max(p["done"], p.get("total") or 0) >= 1_048_576
    size = (lambda n: f"{n / 1_048_576:.1f}") if big else (lambda n: f"{n // 1024}")
    unit = "MB" if big else "KB"
    text = f"{size(p['done'])} of {size(p['total'])} {unit}" if p.get("total") else f"{size(p['done'])} {unit}"
    return text + (f" (part {p['part']} of {p['parts']})" if p.get("parts", 1) > 1 else "")


@core.internal
def progress_view(p: dict | None, rev: str = "") -> dict:
    """What the page needs of a running download: {key, text, pct, rev}; key is None when nothing is downloading."""
    if not p:
        return {"key": None, "text": "", "pct": None, "rev": rev}
    parts, part = max(1, p.get("parts") or 1), max(1, p.get("part") or 1)
    total = p.get("total") or 0
    pct = None
    if total:
        pct = int(min(100, 100 * ((part - 1) + min(1.0, p["done"] / total)) / parts))
    return {"key": p["key"], "text": download_text(p), "pct": pct, "rev": rev}


# ---------------------------------------------------------------- checking what the page sends

def _flag(value) -> bool:
    if type(value) is not bool:
        raise ApiError("Bad request: on or off expected.")
    return value


def _key(value) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise ApiError("Bad request: no such list.")
    return value


def _name(value, limit=MAX_NAME, what="A name") -> str:
    if not isinstance(value, str):
        raise ApiError("Bad request: the name must be text.")
    text = " ".join(value.split())
    if len(text) > limit or _CONTROL.search(text):
        raise ApiError(f"{what} can be at most {limit} letters.")
    return text


def _host(value) -> str:
    """A site address as the old window took it ("https://www.Reddit.com/r/x" -> "reddit.com"); ApiError with its words."""
    if not isinstance(value, str) or len(value) > 300:
        raise ApiError("Not a valid domain.")
    try:
        return normalize_host(value)
    except ValueError as error:
        raise ApiError(str(error)) from None


def _url(value) -> str:
    if (not isinstance(value, str) or len(value.strip()) > MAX_URL
            or not value.strip().lower().startswith(("http://", "https://"))):
        raise ApiError("Paste the list's full address (starting with https://).")
    text = value.strip()
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or _CONTROL.search(text) or " " in text:
        raise ApiError("Paste the list's full address (starting with https://).")
    return text


def _suffixes(host: str) -> list[str]:
    """host, its parent, ... (not the bare TLD): what a "*." entry or an allowed site can cover."""
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


# ---------------------------------------------------------------- changing the protection setting

def _digest(cfg: dict) -> str:
    """The canonical protection state for the grace: which lists are on, what is allowed, your URL lists and EVERY site of
    your own lists. Two states with the same digest are the same protection."""
    state = [sorted(cfg["enabled"]), sorted(cfg["allowed"]), sorted(cfg["custom"], key=lambda c: c["key"]),
             sorted((m["key"], sorted(e["host"] for e in m["entries"])) for m in cfg["manual"])]
    return hashlib.sha1(json.dumps(state, sort_keys=True).encode()).hexdigest()


def _save(db, cfg: dict):
    protection.save_settings(db, cfg)


def _apply(ctx, edit, what: str):
    """Change the protection setting. `edit(cfg)` changes a fresh copy in place, refuses a bad request with ApiError and
    returns what the Api method answers. If the result loosens (antibypass.protection_looser) it waits for the challenge
    unless it exactly undoes a tightening from the last 10 seconds; otherwise it is saved at once. Whatever runs later
    (after the challenge) works on the settings as they are THEN, so changes made meanwhile are kept."""
    db = ctx.db
    with _lock:
        old = _shared(db)
        new = protection.settings(db)
        result = edit(new)
        if new == old:
            return result
        if not antibypass.protection_looser(old, new):
            ctx.guard.grace_note(GRACE_PROTECTION, _digest(old))        # (a tightening: it can be undone quickly)
            _save(db, new)
            return result
        if ctx.guard.grace_ok(GRACE_PROTECTION, _digest(new)):
            _save(db, new)
            return result

    def action():
        with _lock:
            latest = protection.settings(ctx.db)
            answer = edit(latest)
            _save(ctx.db, latest)
            return answer
    return ctx.guard.request([what], action)


def _community(cfg: dict) -> dict:
    """The lists of the "Always-on lists" card: the built-in ones and your URL lists (not the hand-made ones)."""
    manual = {m["key"] for m in cfg["manual"]}
    return {k: v for k, v in protection.all_lists(cfg).items() if k not in manual}


def _manual(cfg: dict, key: str) -> dict:
    found = next((m for m in cfg["manual"] if m["key"] == key), None)
    if found is None:
        raise ApiError("That list isn't there any more.")
    return found


def _set_enabled(cfg: dict, key: str, on: bool):
    if (key in cfg["enabled"]) == on:
        return
    cfg["enabled"] = [k for k in cfg["enabled"] if k != key] + ([key] if on else [])


# ---------------------------------------------------------------- reads

def _state(info: dict, enabled: bool, progress: dict | None, key: str) -> tuple[str, int | None]:
    """The 5-condition table of the old window -> (state, count): downloading, updated (or update failed), download
    failed, waiting for the service, off."""
    if progress and progress.get("key") == key:
        return "downloading", None
    if info.get("count"):
        failed = info.get("failed") and info["failed"] > info.get("updated", "")
        return ("update_failed" if failed else "updated"), info["count"]
    if info.get("failed"):
        return "failed", None
    return ("waiting" if enabled else "off"), None


@core.read
def protection_get(ctx):
    """{now, rev, auto, every_hours, intervals, lists: [{key, name, desc, custom, enabled, state, count, updated,
    text, pct}], manual: [{key, name, count, enabled}], allowed, words}. `words` is words_get, so the page needs one call.
    `state`: downloading | updated | update_failed | failed | waiting | off (the old window's table); `updated` is when the
    list was last downloaded (the page counts "ago")."""
    db = ctx.db
    cfg = _shared(db)
    progress = protection.download_progress(db)
    rev = info_rev(cfg)
    lists = []
    for key, (name, what, _sources, _default) in _community(cfg).items():
        info = cfg["info"].get(key, {})
        enabled = key in cfg["enabled"]
        state, count = _state(info, enabled, progress, key)
        shown = progress_view(progress, rev) if state == "downloading" else {"text": "", "pct": None}
        lists.append({"key": key, "name": name, "desc": what if key in protection.LISTS else f"your list · {what}",
                      "custom": key not in protection.LISTS, "enabled": enabled, "state": state, "count": count,
                      "updated": info.get("updated") if state in ("updated", "update_failed") else None,
                      "text": shown["text"], "pct": shown["pct"]})
    manual = [{"key": m["key"], "name": m["name"], "count": len(m["entries"]), "enabled": m["key"] in cfg["enabled"]}
              for m in cfg["manual"]]
    return {"now": ctx.now().isoformat(timespec="seconds"), "rev": rev, "auto": bool(cfg["auto"]),
            "every_hours": cfg["every_hours"], "intervals": [[hours, label] for label, hours in
                                                              protection.UPDATE_CHOICES.items()],
            "lists": lists, "manual": manual, "allowed": sorted(cfg["allowed"]), "words": words_get(ctx)}


@core.read
def manual_get(ctx, key):
    """One of your own lists: {key, name, enabled, entries: [[site, name], ...]}, or null when it isn't there."""
    key = _key(key)
    cfg = _shared(ctx.db)
    found = next((m for m in cfg["manual"] if m["key"] == key), None)
    if found is None:
        return None
    return {"key": key, "name": found["name"], "enabled": key in cfg["enabled"],
            "entries": ctx.db.parsed(protection.SETTINGS_KEY, _pairs_by_list, "")[key]}


@core.read
def host_normalize(ctx, text):
    """A site address as typed -> the site ("https://www.Reddit.com/r/x" -> "reddit.com"), or an error in the old words."""
    return _host(text)


class _Index:
    """The lists' sites as sorted tables of hashes (a few MB per million sites), loaded the first time "Check a site" is
    used and again for a list whose file changed - the same tables the service's DNS filter keeps, so a lookup takes
    microseconds, not a 70 MB read."""

    def __init__(self):
        self.lock = threading.Lock()
        self.tables = {}             # (folder, key) -> (file time, exact table, wildcard table)

    def table(self, folder, key):
        path = protection.list_path(key, folder)
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return None
        hit = self.tables.get((folder, key))
        if hit is not None and hit[0] == stamp:
            return hit
        with self.lock:
            hit = self.tables.get((folder, key))
            if hit is not None and hit[0] == stamp:
                return hit
            exact, wild = [], []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if line.startswith("*."):
                        wild.append(hash(line[2:]))
                    elif line:
                        exact.append(hash(line))
            built = self.tables[(folder, key)] = (stamp, protection._table(exact), protection._table(wild))
            return built

    def lists_with(self, host, keys, folder):
        """Which of the lists (keys) block `host`: its exact name or www. form, or a "*." entry for it or a parent."""
        exact = {hash(host), hash("www." + host)}
        wild = [hash(s) for s in _suffixes(host)]
        found = []
        for key in keys:
            t = self.table(folder, key)
            if t and (any(protection._has(t[1], h) for h in exact) or any(protection._has(t[2], h) for h in wild)):
                found.append(key)
        return found


def _index(ctx) -> _Index:
    idx = getattr(ctx, "protection_index", None)
    if idx is None:
        idx = ctx.protection_index = _Index()
    return idx


@core.read
def protection_check(ctx, host):
    """Is this site on a list? {host, allowed_by, lists: [{key, name, on, mine}]}. `allowed_by` is the site you allowed
    anyway that covers it (itself or a parent), else null; `lists` are the lists that hold it (also the switched-off ones,
    `on` says which) and your own lists that are on and cover it (`mine`)."""
    host = _host(host)
    cfg = _shared(ctx.db)
    suffixes = _suffixes(host)
    allowed = set(cfg["allowed"])
    allowed_by = next((s for s in suffixes if s in allowed), None)
    lists = _community(cfg)
    folder = getattr(ctx, "protection_dir", None) or protection.LIST_DIR
    on = set(cfg["enabled"])
    found = [{"key": k, "name": lists[k][0], "on": k in on, "mine": False}
             for k in _index(ctx).lists_with(host, list(lists), folder)]
    sites = _manual_hosts(ctx.db)
    for m in cfg["manual"]:
        if m["key"] in on and any(s in sites[m["key"]] for s in suffixes):
            found.append({"key": m["key"], "name": m["name"], "on": True, "mine": True})
    return {"host": host, "allowed_by": allowed_by, "lists": found}


@core.internal
def warm(ctx):
    """(a background thread) Load the lists' tables now, so the first "Check a site" finds them ready instead of reading
    every list file (about 1.5 s on a million sites). Cheap when they are loaded already; a list whose file is not there
    (not downloaded yet) is skipped."""
    folder = getattr(ctx, "protection_dir", None) or protection.LIST_DIR
    _index(ctx).lists_with("example.com", list(_community(_shared(ctx.db))), folder)


@core.internal
def start_warmup(ctx, delay: float = WARM_DELAY_SEC):
    """(the agent, once it is up) A daemon thread that runs `warm` `delay` seconds from now, once. A failure is logged and
    changes nothing (the first check then loads the tables itself). `thread.stop_event.set()` ends it (tests)."""
    stop = threading.Event()

    def work():
        if stop.wait(delay):
            return
        try:
            warm(ctx)
        except Exception:
            log.exception("Site protection warm-up failed")
    thread = threading.Thread(target=work, name="lockdown-protection-warm", daemon=True)
    thread.stop_event = stop
    thread.start()
    return thread


# ---------------------------------------------------------------- the always-on lists

@core.loosen
def protection_set_list_enabled(ctx, key, enabled):
    """Switch one of the Always-on lists (built-in or your URL list) on or off. Off needs the challenge when it is locked."""
    key, on = _key(key), _flag(enabled)

    def edit(cfg):
        lists = _community(cfg)
        if key not in lists:
            raise ApiError("That list isn't there any more.")
        _set_enabled(cfg, key, on)
        return {"key": key, "enabled": on}
    found = _community(_shared(ctx.db)).get(key)
    return _apply(ctx, edit, f"Switch the {found[0]} protection list off" if found else "Switch a protection list off")


@core.ui
def protection_set_updates(ctx, auto, every_hours):
    """Update automatically (on / off) and how often (6, 12, 24 or 168 hours)."""
    auto = _flag(auto)
    if type(every_hours) is not int or every_hours not in protection.UPDATE_CHOICES.values():
        raise ApiError("Bad request: pick one of the update times.")
    with _lock:
        cfg = protection.settings(ctx.db)
        cfg["auto"], cfg["every_hours"] = auto, every_hours
        protection.save_settings(ctx.db, cfg)
    return {"auto": auto, "every_hours": every_hours}


@core.ui
def protection_update_now(ctx):
    """Ask the service to download the lists again in a few seconds."""
    with _lock:
        cfg = protection.settings(ctx.db)
        cfg["update_now"] = ctx.now().isoformat(timespec="seconds")
        protection.save_settings(ctx.db, cfg)
    return {"asked": cfg["update_now"]}


# ---------------------------------------------------------------- connect a list

def _prune_previews(now: float):
    for token in [t for t, p in _previews.items() if now - p["created"] > PREVIEW_TTL_SEC]:
        del _previews[token]
    while len(_previews) > 20:
        del _previews[next(iter(_previews))]


@core.ui
def protection_preview(ctx, url):
    """Download a list from `url` to have a look (in the background): returns {token} at once and pushes
    `protection_preview` {token, ok, count, sample, text | error} when it is done. Only http(s) addresses."""
    url = _url(url)
    with _lock:
        if _running[0] >= MAX_PREVIEWS:
            raise ApiError("Wait for the other preview to finish.")
        _running[0] += 1
    token = secrets.token_urlsafe(12)
    publish = getattr(ctx, "publish", None)

    def work():
        try:
            try:
                count, sample = protection.preview(url)
                result = {"token": token, "ok": True, "count": count, "sample": sample,
                          "text": f"{count:,} sites, e.g. {', '.join(sample)}"}
                with _lock:
                    _prune_previews(time.monotonic())
                    _previews[token] = {"url": url, "count": count, "sample": sample, "created": time.monotonic()}
            except Exception as error:     # offline, not a list, too big
                text = str(error) if isinstance(error, ValueError) else f"Couldn't download it: {error}"
                result = {"token": token, "ok": False, "error": text}
        finally:
            with _lock:
                _running[0] -= 1
        if publish:
            publish("protection_preview", result, token)

    threading.Thread(target=work, name="lockdown-list-preview", daemon=True).start()
    return {"token": token}


@core.tighten
def protection_connect(ctx, name, url, preview_token):
    """Connect the list that was just previewed. Only the address Python previewed is accepted (`preview_token`); a new
    list only blocks more, so there is no challenge."""
    url = _url(url)
    name = _name(name)
    with _lock:
        _prune_previews(time.monotonic())
        shown = _previews.get(preview_token) if isinstance(preview_token, str) else None
        if shown is None or shown["url"] != url:
            raise ApiError("Preview the list first.")

        def edit(cfg):
            if len(cfg["custom"]) >= MAX_CUSTOM:
                raise ApiError(f"You can connect at most {MAX_CUSTOM} lists.")
            label = name or urlsplit(url).hostname
            key = protection.new_custom_key(cfg)
            cfg["custom"].append({"key": key, "name": label[:MAX_NAME], "url": url})
            cfg["enabled"].append(key)
            return {"key": key, "name": label[:MAX_NAME], "count": shown["count"],
                    "text": f"Connected {label[:MAX_NAME]} ({shown['count']:,} sites) - it's downloaded in a few seconds."}
        result = _apply(ctx, edit, "Connect a list")
        _previews.pop(preview_token, None)
    return result


@core.loosen
def protection_remove_custom(ctx, key):
    """Remove one of your URL lists. Removing one that is on needs the challenge when it is locked."""
    key = _key(key)
    cfg = _shared(ctx.db)
    found = next((c for c in cfg["custom"] if c["key"] == key), None)

    def edit(cfg):
        if not any(c["key"] == key for c in cfg["custom"]):
            raise ApiError("That list isn't there any more.")
        cfg["custom"] = [c for c in cfg["custom"] if c["key"] != key]
        cfg["enabled"] = [k for k in cfg["enabled"] if k != key]
        cfg["info"].pop(key, None)
        return {"key": key}
    return _apply(ctx, edit, f"Remove your {found['name']} list" if found else "Remove your list")


# ---------------------------------------------------------------- your own lists

@core.tighten
def manual_create(ctx, name):
    """A new, empty list of your own (on). It blocks nothing yet, so there is no challenge."""
    name = _name(name)
    if not name:
        raise ApiError("Give the list a name.")

    def edit(cfg):
        if len(cfg["manual"]) >= MAX_MANUAL:
            raise ApiError(f"You can have at most {MAX_MANUAL} lists of your own.")
        key = protection.new_manual_key(cfg)
        cfg["manual"] = cfg["manual"] + [{"key": key, "name": name, "entries": []}]
        cfg["enabled"] = cfg["enabled"] + [key]
        return {"key": key, "name": name}
    return _apply(ctx, edit, "New list")


@core.loosen
def manual_set_enabled(ctx, key, enabled):
    """Switch one of your lists on or off. Off needs the challenge when it is locked."""
    key, on = _key(key), _flag(enabled)

    def edit(cfg):
        _manual(cfg, key)
        _set_enabled(cfg, key, on)
        return {"key": key, "enabled": on}
    found = next((m for m in _shared(ctx.db)["manual"] if m["key"] == key), None)
    return _apply(ctx, edit, f"Turn your {found['name']} list off" if found else "Turn your list off")


@core.loosen
def manual_remove(ctx, key):
    """Delete one of your lists. Deleting one that is on needs the challenge when it is locked."""
    key = _key(key)

    def edit(cfg):
        _manual(cfg, key)
        cfg["manual"] = [m for m in cfg["manual"] if m["key"] != key]
        cfg["enabled"] = [k for k in cfg["enabled"] if k != key]
        return {"key": key}
    found = next((m for m in _shared(ctx.db)["manual"] if m["key"] == key), None)
    return _apply(ctx, edit, f"Remove your {found['name']} list" if found else "Remove your list")


def _entries(value, known: dict) -> list[dict]:
    """The sites the page sends [[site, name], ...] -> [{name, host}], every NEW site normalised here (a site already in
    the list is taken as it is), duplicates dropped (the first one counts), the order kept."""
    if not isinstance(value, list) or len(value) > MAX_ENTRIES:
        raise ApiError(f"A list can hold at most {MAX_ENTRIES:,} sites.")
    out, seen = [], set()
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2 or not all(isinstance(x, str) for x in item):
            raise ApiError("Bad request: sites and their names expected.")
        host, label = item
        if host not in known:
            host = _host(host)
        if host in seen:
            continue
        seen.add(host)
        out.append({"name": _name(label, MAX_ENTRY_NAME, "A site's name"), "host": host})
    return out


@core.loosen
def manual_save_entries(ctx, key, entries):
    """Replace the sites of one of your lists with `entries` [[site, name], ...]. Adding sites is free; taking sites out of
    a list that is on needs the challenge when it is locked (out of one that is off, it does not)."""
    key = _key(key)
    shared = _shared(ctx.db)
    current = next((m for m in shared["manual"] if m["key"] == key), None)
    if current is None:
        raise ApiError("That list isn't there any more.")
    clean = _entries(entries, {e["host"] for e in current["entries"]})

    def edit(cfg):
        _manual(cfg, key)["entries"] = clean
        return {"key": key, "count": len(clean)}
    return _apply(ctx, edit, f"Remove sites from your {current['name']} list")


# ---------------------------------------------------------------- allowed anyway

@core.loosen
def protection_allow(ctx, host, whole_site=False):
    """Allow a site (and its subdomains) although a list blocks it. Needs the challenge when it is locked.
    `whole_site` true (the page picked it from the suggestions): a site of the bundled list is allowed with ALL its host
    names (youtube.com and googlevideo.com ...), in this one change - one challenge, not one per name."""
    host = _host(host)
    names = [host]
    if whole_site is True:
        known = site_list.lookup(host)
        if known:
            names = list(known[1])
    summary = (f"Allow {host} (and its subdomains) although a protection list blocks it" if len(names) == 1
               else f"Allow {known[0]} ({len(names)} addresses, and their subdomains) although a protection list blocks it")

    def edit(cfg):
        new = [n for n in names if n not in cfg["allowed"]]
        if not new:
            return {"host": host}
        if len(cfg["allowed"]) + len(new) > MAX_ALLOWED:
            raise ApiError("The allowed list is full.")
        cfg["allowed"] = cfg["allowed"] + new
        return {"host": host}
    return _apply(ctx, edit, summary)


@core.tighten
def protection_unallow(ctx, host):
    """Take a site off "Allowed anyway" (it is blocked by the lists again): stricter, so free."""
    host = _host(host)

    def edit(cfg):
        cfg["allowed"] = [a for a in cfg["allowed"] if a != host]
        return {"host": host}
    return _apply(ctx, edit, f"Stop allowing {host}")


# ---------------------------------------------------------------- the words

@core.read
def words_get(ctx):
    """The Safe search and Blocked words cards: {enabled, safesearch, youtube, action, actions, lists: [{key, name, on,
    total, off}], words: {on, count}, exceptions: {on, count}}."""
    cfg = keywords.settings_shared(ctx.db)
    off = set(cfg["off"])
    return {"enabled": bool(cfg["enabled"]), "safesearch": bool(cfg["safesearch"]), "youtube": bool(cfg["youtube"]),
            "action": cfg["action"], "actions": [[k, v] for k, v in keywords.ACTIONS.items()],
            "lists": [{"key": k, "name": name, "on": bool(cfg["lists"].get(k)), "total": len(words),
                       "off": sum(1 for w in words if w in off)} for k, (name, words) in keywords.READY.items()],
            "words": {"on": bool(cfg["words_on"]), "count": len(cfg["words"])},
            "exceptions": {"on": bool(cfg["exceptions_on"]), "count": len(cfg["exceptions"])}}


@core.read
def wordlist_get(ctx, name):
    """The words of one list for its dialog. A ready list ("adult_en", "adult_pl"): {kind: "ready", title, words, off,
    other_off} (`off` = the ones turned off, `other_off` = turned-off words of the other list, which a save must keep).
    "words" / "exceptions": {kind, title, words, hint}."""
    cfg = keywords.settings_shared(ctx.db)
    if isinstance(name, str) and name in keywords.READY:
        title, words = keywords.READY[name]
        off = set(cfg["off"])
        return {"kind": "ready", "name": name, "title": title, "words": list(words),
                "off": [w for w in words if w in off], "other_off": [w for w in cfg["off"] if w not in words]}
    if isinstance(name, str) and name in OWN_LISTS:
        title, hint = OWN_LISTS[name]
        return {"kind": name, "name": name, "title": title, "words": list(cfg[name]), "hint": hint}
    raise ApiError("There is no such word list.")


def _known_words() -> set:
    return {w for _name, words in keywords.READY.values() for w in words}


def _own_words(value, what: str) -> list[str]:
    """Your words / exceptions as the old window kept them: lower case, spaces collapsed, no duplicates, new ones first
    as sent."""
    if not isinstance(value, list) or len(value) > MAX_WORDS:
        raise ApiError(f"{what} can hold at most {MAX_WORDS:,} entries.")
    out, seen = [], set()
    for item in value:
        if not isinstance(item, str):
            raise ApiError("Bad request: words must be text.")
        word = " ".join(item.split()).lower()
        if not word:
            continue
        if len(word) > MAX_WORD or _CONTROL.search(word):
            raise ApiError(f"A word can be at most {MAX_WORD} letters.")
        if word not in seen:
            seen.add(word)
            out.append(word)
    return out


def _words_patch(partial) -> dict:
    """Validate what the page wants changed -> only known settings, each checked."""
    if not isinstance(partial, dict) or not partial:
        raise ApiError("Bad request: nothing to change.")
    unknown = set(partial) - {*WORD_FLAGS, "action", "lists", "off", "words", "exceptions"}
    if unknown:
        raise ApiError("Bad request: that setting can't be changed here.")
    patch = {}
    for key in WORD_FLAGS:
        if key in partial:
            patch[key] = _flag(partial[key])
    if "action" in partial:
        if not isinstance(partial["action"], str) or partial["action"] not in keywords.ACTIONS:
            raise ApiError("Bad request: close the tab or go back.")
        patch["action"] = partial["action"]
    if "lists" in partial:
        lists = partial["lists"]
        if not isinstance(lists, dict) or set(lists) - set(keywords.READY):
            raise ApiError("Bad request: no such word list.")
        patch["lists"] = {k: _flag(v) for k, v in lists.items()}
    if "off" in partial:
        off = partial["off"]
        if not isinstance(off, list) or any(not isinstance(w, str) for w in off) or set(off) - _known_words():
            raise ApiError("Bad request: those words aren't on the ready lists.")
        patch["off"] = sorted(set(off))
    if "words" in partial:
        patch["words"] = _own_words(partial["words"], "Your words")
    if "exceptions" in partial:
        patch["exceptions"] = _own_words(partial["exceptions"], "Exceptions")
    return patch


def _words_digest(cfg: dict) -> str:
    state = {**cfg, "off": sorted(set(cfg["off"])), "words": sorted(set(cfg["words"])),
             "exceptions": sorted(set(cfg["exceptions"]))}
    return hashlib.sha1(json.dumps(state, sort_keys=True).encode()).hexdigest()


def _merged(cfg: dict, patch: dict) -> dict:
    out = {**cfg, **{k: v for k, v in patch.items() if k != "lists"}}
    if "lists" in patch:
        out["lists"] = {**cfg["lists"], **patch["lists"]}
    return out


@core.loosen
def words_update(ctx, partial):
    """Change some of the word settings: {enabled, safesearch, youtube, action, lists: {adult_en: bool ...}, off: [...],
    words: [...], exceptions: [...], words_on, exceptions_on}. What weakens the check (keywords.looser) needs the
    challenge when it is locked; stricter is free."""
    patch = _words_patch(partial)
    with _words_lock:
        old = keywords.settings(ctx.db)
        new = _merged(old, patch)
        changes = keywords.looser(old, new)
        if not changes:
            if new != old:
                ctx.guard.grace_note(GRACE_WORDS, _words_digest(old))      # (a tightening: it can be undone quickly)
                keywords.save(ctx.db, new)
            return {"changed": new != old}
        if ctx.guard.grace_ok(GRACE_WORDS, _words_digest(new)):
            keywords.save(ctx.db, new)
            return {"changed": True}

    def action():
        with _words_lock:
            keywords.save(ctx.db, _merged(keywords.settings(ctx.db), patch))
        return {"changed": True}
    return ctx.guard.request(changes, action)
