"""The bundled list of ~500 popular sites (importer.popular, assets/sites/sites.json) as the "add a site" boxes use it: the
ranked suggestions while you type, and a typed host's friendly name and all its host names. Data only - nothing here
changes what is blocked; the boxes add a picked site the way they add a typed one.

Ranking (the phone's engine/SiteList.kt does the same): `search.score` first (every word found, small typos fine), then
within the same score: the name starts with the text, then a domain starts with it ("www." ignored), then a word of the
name starts with it, then the rest; then the list's popularity order. The texts are normalized once, on first use.
"""
import search
from importer import popular

NAME_FIRST, DOMAIN_FIRST, WORD_FIRST, REST = range(4)

_cache: tuple | None = None        # (the list it was made from, [_Entry])


class _Entry:
    __slots__ = ("site", "text", "tokens", "name", "domains")

    def __init__(self, site: dict):
        self.site = site
        self.text = search.normalize(f"{site['name']} {' '.join(site['domains'])}")
        self.tokens = search.words_of(self.text)
        self.name = search.normalize(site["name"])
        self.domains = [search.normalize(d).removeprefix("www.") for d in site["domains"]]


def _entries() -> list[_Entry]:
    global _cache
    sites = popular.load_sites()
    if _cache is None or _cache[0] is not sites:
        _cache = (sites, [_Entry(s) for s in sites])
    return _cache[1]


def tier(query: str, name: str, domains) -> int:
    """How the text sits in a site: NAME_FIRST (the name starts with it) < DOMAIN_FIRST < WORD_FIRST < REST. `query` is
    normalized and stripped; `name` and `domains` are normalized too."""
    if name.startswith(query):
        return NAME_FIRST
    bare = query.removeprefix("www.")
    if bare and any(d.startswith(bare) for d in domains):
        return DOMAIN_FIRST
    if any(w.startswith(query) for w in search.words_of(name)):
        return WORD_FIRST
    return REST


def row_of(site: dict) -> dict:
    return {"kind": "site", "name": site["name"], "host": site["domains"][0], "mine": False, "category": site.get("category", ""),
            "hosts": list(site["domains"])}


def suggest(text: str) -> list[tuple]:
    """Every list site that fits the text, best first: [(score, tier, popularity position, row)]. Nothing typed: all of
    them in popularity order."""
    entries = _entries()
    query = search.normalize(text).strip()
    words = search.words_of(query)
    if not words:
        return [(0, NAME_FIRST, i, row_of(e.site)) for i, e in enumerate(entries)]
    found = []
    for i, e in enumerate(entries):
        score = search.score_prepared(words, e.text, e.tokens)
        if score is not None:
            found.append((score, tier(query, e.name, e.domains), i, row_of(e.site)))
    found.sort(key=lambda f: f[:3])
    return found


def lookup_site(host: str) -> dict | None:
    """The list site that has exactly this host name (a.youtube.com is not one of YouTube's own names: a typed
    sub-address does not take the whole site), else None."""
    host = host.strip().lower().removeprefix("www.")
    site = popular.site_of(host)
    return site if site and host in site["domains"] else None


def category_of(host: str) -> str:
    site = lookup_site(host)
    return site.get("category", "") if site else ""


def lookup(host: str) -> tuple[str, list[str]] | None:
    """(name, all its host names) of the known site this host is. The old 30-site table backs it up when the big list
    could not be read."""
    site = lookup_site(host)
    if site:
        return site["name"], list(site["domains"])
    for sites in popular.POPULAR_SITES.values():
        for name, names in sites.items():
            if host in names:
                return name, names
    return None
