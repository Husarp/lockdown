"""Bad-word check and forced SafeSearch (Blocking > Protection).

- Words: the address and title of the browser tab in front are checked for blocked words (built-in English + Polish
  adult words, plus your own). Whole words only ("analysis" doesn't match "anal"); a word ending in "*" also matches
  longer words ("porn*" -> "pornhub"); several words = that phrase. Accents are ignored ("ruchać" = "ruchac").
  Exceptions: a site (has a dot: that site and its subdomains aren't checked) or a word (never counts).
- SafeSearch: the DNS filter sends Google / Bing / DuckDuckGo / YouTube to their "safe" addresses (the search
  engines' own documented way to force SafeSearch / Restricted Mode); browser policies force it as well.
Standard library only (used by the service too)."""
import json
import re
import unicodedata
from urllib.parse import unquote_plus, urlsplit

SETTINGS_KEY = "words"   # JSON {"enabled", "safesearch", "action": "close" / "back", "words": [...], "exceptions": [...]}
DEFAULTS = {"enabled": True, "safesearch": True, "action": "close", "words": [], "exceptions": []}
ACTIONS = {"close": "Close the tab", "back": "Go back"}

BUILT_IN = {
    "English": ["porn*", "xxx", "xvideos", "xnxx", "xhamster", "redtube", "youporn", "spankbang", "brazzers",
                "bangbros", "eporner", "tnaflix", "onlyfans", "fansly", "chaturbate", "stripchat", "livejasmin",
                "camsoda", "bongacams", "rule34", "rule 34", "r34", "e621", "hentai*", "ecchi", "futanari", "nsfw",
                "lewd", "nudes", "boobs", "tits", "titties", "pussy", "blowjob*",
                "handjob*", "deepthroat*", "cumshot*", "creampie*", "milf*", "bdsm", "gangbang*", "threesome*",
                "orgy", "orgies", "orgasm*", "masturba*", "erot*", "camgirl*", "sexcam*", "sexting",
                "sex video*", "sex tape*", "sex chat", "free sex", "hot sex", "bukkake", "strip club*", "stripper*"],
    "Polish": ["porno*", "seks", "seksi", "darmowy seks", "seks kamerki", "sex kamerki", "ruchanie", "ruchac",
               "bzykanie", "cipka", "cipki", "cycki", "cycuszki", "nago", "nagie", "rozbierane*", "lodzik*",
               "obciaganie", "dziwka", "dziwki", "prostytutk*", "roksa", "anonse towarzyskie",
               "laski nago", "golasy", "golaski"],
}
SAFE_TARGETS = {   # DNS names -> the search engines' "always safe" address
    "forcesafesearch.google.com": re.compile(r"^(www\.)?google\.(com|[a-z]{2}|co\.[a-z]{2}|com\.[a-z]{2})$"),
    "strict.bing.com": re.compile(r"^(www\.)?bing\.com$"),
    "safe.duckduckgo.com": re.compile(r"^(www\.|start\.|html\.)?duckduckgo\.com$"),
    "restrictmoderate.youtube.com": re.compile(r"^((www|m)\.youtube\.com|youtubei?\.googleapis\.com|"
                                               r"www\.youtube-nocookie\.com)$"),
}
_TOKEN = re.compile(r"[a-z0-9]+")


def settings(db) -> dict:
    try:
        cfg = json.loads(db.get_setting(SETTINGS_KEY, "") or "{}")
    except ValueError:
        cfg = {}
    return {**DEFAULTS, **cfg}


def save(db, cfg: dict):
    db.set_setting(SETTINGS_KEY, json.dumps(cfg))


def built_in_count() -> int:
    return sum(map(len, BUILT_IN.values()))


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
    for entry in [w for ws in BUILT_IN.values() for w in ws] + list(cfg["words"]):
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


def safe_target(name: str) -> str | None:
    """The "safe" address to answer with for a search engine / YouTube name, else None."""
    for target, pattern in SAFE_TARGETS.items():
        if pattern.match(name):
            return target
    return None
