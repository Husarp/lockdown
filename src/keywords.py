"""Bad-word check and forced SafeSearch (Blocking > Protection).

- Words: the address and title of the browser tab in front are checked for blocked words (ready-made English and
  Polish adult word lists - each can be switched off, single words turned off - plus your own). Whole words only
  ("analysis" doesn't match "anal"); a word ending in "*" also matches longer words ("porn*" -> "pornhub");
  several words = that phrase. Accents are ignored ("ruchać" = "ruchac").
  Exceptions: a site (has a dot: that site and its subdomains aren't checked) or a word (never counts).
- SafeSearch: the DNS filter sends Google / Bing / DuckDuckGo to their "safe" addresses; browser policies force it
  as well. YouTube Restricted Mode is a separate switch (it also hides all comments, so it's on its own).
Standard library only (used by the service too)."""
import json
import re
import unicodedata
from urllib.parse import unquote_plus, urlsplit

SETTINGS_KEY = "words"   # JSON {"enabled", "safesearch", "action": "close" / "back", "lists": {ready list: on},
#                                "off": [ready-list words turned off], "words": [yours], "exceptions": [...]}
DEFAULTS = {"enabled": True, "safesearch": True, "action": "close", "lists": {"adult_en": True, "adult_pl": True},
            "youtube": True, "off": [], "words": [], "exceptions": []}
ACTIONS = {"close": "Close the tab", "back": "Go back"}

# Ready-made lists you can switch on (both on by default) and open to turn single words off.
READY = {
    "adult_en": ("Adult words - English", [
        "porn*", "xxx", "xvideos", "xnxx", "xhamster", "redtube", "youporn", "youjizz", "spankbang", "spankwire",
        "brazzers", "bangbros", "realitykings", "naughtyamerica", "eporner", "tnaflix", "tube8", "motherless",
        "hqporner", "beeg", "txxx", "hclips", "fapello", "onlyfans", "fansly", "manyvids", "clips4sale", "chaturbate",
        "stripchat", "livejasmin", "camsoda", "bongacams", "myfreecams", "cam4", "rule34", "rule 34", "r34", "e621",
        "nhentai", "hentai*", "hanime", "ecchi", "futanari", "jav", "nsfw", "lewd", "nudes", "nude pics",
        "nude photos", "naked girls", "naked women", "boobs", "tits", "titties", "pussy", "blowjob*", "handjob*",
        "footjob*", "deepthroat*", "cumshot*", "creampie*", "milf*", "gilf", "bdsm", "bondage", "fetish*",
        "gangbang*", "threesome*", "orgy", "orgies", "orgasm*", "masturba*", "erot*", "camgirl*", "camboy*",
        "sexcam*", "sexting", "sexchat", "sex video*", "sex tape*", "sex chat", "sex cam*", "free sex", "hot sex",
        "sex dating", "anal sex", "oral sex", "bukkake", "cuckold", "upskirt", "strip club*", "stripper*",
        "striptease", "dildo*", "vibrator*", "sex toy*", "fleshlight", "adult video*", "adult movie*", "adult chat",
        "adult dating", "escort service*"]),
    "adult_pl": ("Adult words - Polish", [
        "porno*", "seks", "seksi", "sexi", "darmowy seks", "seks kamerki", "sex kamerki", "sekstelefon",
        "seks telefon", "sex telefon", "sex anonse", "erotyk*", "erotycz*", "ruchanie", "ruchac", "rucha",
        "wyruchal*", "wyruchan*", "bzykanie", "bzykac", "cipka", "cipki", "cipa", "cycki", "cycuszki", "cycate",
        "nago", "nagie", "golasy", "golaski", "rozbierane*", "rozbieranki", "lodzik*", "obciaganie", "dziwka",
        "dziwki", "prostytutk*", "roksa", "anonse towarzyskie", "masturbac*", "walenie konia", "orgia", "orgie",
        "striptiz", "filmy dla doroslych"]),
}
SEARCH_TARGETS = {   # DNS names -> the search engines' "always safe" address (SafeSearch switch)
    "forcesafesearch.google.com": re.compile(r"^(www\.)?google\.(com|[a-z]{2}|co\.[a-z]{2}|com\.[a-z]{2})$"),
    "strict.bing.com": re.compile(r"^(www\.)?bing\.com$"),
    "safe.duckduckgo.com": re.compile(r"^(www\.|start\.|html\.)?duckduckgo\.com$"),
}
YOUTUBE_TARGET = ("restrictmoderate.youtube.com",   # YouTube Restricted Mode switch (separate: it hides comments)
                  re.compile(r"^((www|m)\.youtube\.com|youtubei?\.googleapis\.com|www\.youtube-nocookie\.com)$"))
_TOKEN = re.compile(r"[a-z0-9]+")


def settings(db) -> dict:
    try:
        cfg = json.loads(db.get_setting(SETTINGS_KEY, "") or "{}")
    except ValueError:
        cfg = {}
    return {**DEFAULTS, **cfg}


def save(db, cfg: dict):
    db.set_setting(SETTINGS_KEY, json.dumps(cfg))


def active_words(cfg: dict) -> list[str]:
    """Words of the ready lists that are on (minus the ones you turned off) + yours."""
    off = set(cfg["off"])
    ready = [w for key, (_name, words) in READY.items() if cfg["lists"].get(key) for w in words if w not in off]
    return ready + [w for w in cfg["words"] if w not in ready]


def looser(old: dict, new: dict) -> list[str]:
    """What in `new` weakens the check (for Anti-Bypass): off, lists / words turned off, exceptions added."""
    out = []
    if old["enabled"] and not new["enabled"]:
        out.append("Turn the blocked-words check off")
    if old["safesearch"] and not new["safesearch"]:
        out.append("Turn forced SafeSearch off")
    if old["youtube"] and not new["youtube"]:
        out.append("Turn YouTube Restricted Mode off")
    for key, (name, _words) in READY.items():
        if old["lists"].get(key) and not new["lists"].get(key):
            out.append(f"Turn the {name} list off")
    removed = (set(new["off"]) - set(old["off"])) | (set(old["words"]) - set(new["words"]))
    if removed:
        out.append(f"Stop blocking {len(removed)} word{'s' * (len(removed) > 1)}: " + ", ".join(sorted(removed)[:8]))
    added = set(new["exceptions"]) - set(old["exceptions"])
    if added:
        out.append("New exceptions: " + ", ".join(sorted(added)))
    return out


def normalize(text: str) -> str:
    """Lowercase, no accents (ł -> l too)."""
    text = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(c for c in text if not unicodedata.combining(c))


def _words(text: str) -> list[str]:
    return _TOKEN.findall(normalize(text))


def looks_like_address(text: str) -> bool:
    """A real address, not search words still being typed into the address bar (those are checked once the search
    page opens - its address has them)."""
    text = text.strip()
    return bool(text) and " " not in text and ("." in text or "/" in text)


def _host(address: str) -> str:
    return urlsplit(address if "://" in address else "http://" + address).hostname or ""


def find(address: str | None, title: str | None, cfg: dict) -> str | None:
    """The first blocked word in the tab's address / title, else None."""
    address = address if address and looks_like_address(address) else ""
    host = _host(address) if address else ""
    exceptions = [normalize(e) for e in cfg["exceptions"]]
    if host and any(host == e or host.endswith("." + e) for e in exceptions if "." in e):
        return None
    ignored = {e for e in exceptions if "." not in e}
    from_address, from_title = _words(unquote_plus(address)), _words(title or "")
    tokens = from_address + from_title
    joined = f" {' '.join(from_address)} | {' '.join(from_title)} "   # (a phrase can't span address and title)
    for entry in active_words(cfg):
        word = normalize(entry).strip()
        if not word or word in ignored:
            continue
        if word.endswith("*"):
            stem = word[:-1]
            if " " in stem:
                if f" {stem}" in joined:
                    return entry
            elif any(t.startswith(stem) for t in tokens):
                return entry
        elif f" {word} " in joined:
            return entry
    return None


def safe_target(name: str, safesearch: bool = True, youtube: bool = True) -> str | None:
    """The "safe" address to answer with for a search-engine name (when safesearch) or a YouTube name (when
    youtube), else None."""
    if safesearch:
        for target, pattern in SEARCH_TARGETS.items():
            if pattern.match(name):
                return target
    if youtube and YOUTUBE_TARGET[1].match(name):
        return YOUTUBE_TARGET[0]
    return None
