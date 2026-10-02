"""What happens to a blocked website (blocked_items.block_type on a site item): a comma-separated set of flags.

- "dns"   - it can't load at all: the hosts file and the DNS filter send it nowhere (what Lockdown always did)
- "close" - the tab in front is closed (the tray agent, like the bad-word check)
- "back"  - the browser goes back instead; if that doesn't leave the page, the tab is closed

close and back exclude each other. None (everything before 0.66) = "dns".
Standard library only: the service reads this too.
"""
import re

FLAGS = ("dns", "close", "back")
_DASH = r"\s[-\u2013\u2014]\s"
# the browser's own end of a window title: " — Mozilla Firefox", " — Mozilla Firefox Private Browsing",
# " - Google Chrome", " - Brave", " - Microsoft\u200b Edge" ...
_BROWSER_END = re.compile(_DASH + r"(?:Mozilla Firefox|Firefox[\w ]*|Google Chrome|Microsoft\u200b?\s?Edge|Brave|Opera|"
                          r"Vivaldi|Chromium|Private Browsing)(?: Private Browsing)?$")
# Edge's "... and 2 more pages - Personal" (its profile) before " - Microsoft Edge"
_EDGE_EXTRA = re.compile(r"(?:\sand \d+ more pages?)?(?:\s-\s[^-\u2013\u2014]+)?$")
TITLE_SITES = {"YouTube": "youtube.com", "YouTube Music": "music.youtube.com"}


def site_flags(block_type: str | None) -> set[str]:
    flags = {f for f in (block_type or "").split(",") if f in FLAGS}
    return flags or {"dns"}


def make_site_block_type(flags) -> str:
    return ",".join(f for f in FLAGS if f in flags)


def blocks_dns(block_type: str | None) -> bool:
    """Should this hostname go into the hosts file / the DNS filter?"""
    return "dns" in site_flags(block_type)


def tab_action(block_type: str | None) -> str | None:
    """"close" / "back" for a site whose tab should be acted on, else None."""
    flags = site_flags(block_type)
    return "close" if "close" in flags else "back" if "back" in flags else None


def text(block_type: str | None) -> str:
    """How it reads on the Blocking list: "can't load + closes the tab"."""
    flags = site_flags(block_type)
    return " + ".join(w for f, w in (("dns", "can't load"), ("close", "closes the tab"),
                                     ("back", "goes back")) if f in flags)


def title_site(title: str | None) -> str | None:
    """The site a browser window's title says its tab is on, when its address bar can't be read - "youtube.com"
    for "Cats - YouTube — Mozilla Firefox". A YouTube video played full screen hides the address bar, and the
    next video (autoplay, a playlist) changes the title, so the last address read no longer counts: YouTube
    went uncounted and its tab was never sent back (0.84.9). Only YouTube's own titles are known."""
    if not title:
        return None
    page = _BROWSER_END.sub("", title.strip())
    # Edge may add " and 2 more pages" and its profile name: "Cats - YouTube and 2 more pages - Personal - Edge"
    pages = (page, _EDGE_EXTRA.sub("", page)) if "Edge" in title[len(page):] else (page,)
    for name, site in TITLE_SITES.items():
        if any(p == name or re.search(_DASH + re.escape(name) + "$", p) for p in pages):
            return site
    return None
