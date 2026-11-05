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
_BROWSER_END = re.compile(_DASH + r"(?:Mozilla Firefox|Firefox|Google Chrome|Microsoft\u200b?\s?Edge|Brave|Opera|"
                          r"Vivaldi|Chromium|Private Browsing)(?:[^-\u2013\u2014]*)$", re.IGNORECASE)
# A tab-count decoration can use any script or word order; no translation table.
_TAB_COUNT = re.compile(r"\d")
TITLE_SITES = {"YouTube Music": "music.youtube.com", "YouTube Shorts": "youtube.com", "YouTube": "youtube.com"}


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


def title_site(title: str | None, exe: str | None = None, confirmed_host: str | None = None) -> str | None:
    """Recognise the site in a browser title, independent of UI language and tab groups.

    A known browser process cannot disambiguate an earlier page-title segment.
    A successful address read always takes precedence.
    confirmed_host may disambiguate a profile suffix only when an address was read
    under this exact window title. Callers must forget it on a title or address change.
    """
    if not title:
        return None
    page = _BROWSER_END.sub("", title.strip())
    parts = re.split(_DASH, page)
    for i, part in enumerate(parts):
        for name, site in TITLE_SITES.items():
            if not part.casefold().startswith(name.casefold()):
                continue
            extra = part[len(name):]
            # The marker is the site's trailing segment, optionally followed by
            # the browser's tab-count decoration and profile/group name. Knowing
            # the executable doesn't turn an earlier page-title word into a site.
            if not extra and i == len(parts) - 1:
                return site
            if not extra and confirmed_host and (confirmed_host == site or confirmed_host.endswith("." + site)):
                return confirmed_host
            if extra and not (extra[0].isascii() and (extra[0].isalnum() or extra[0] == "_")) and _TAB_COUNT.search(extra):
                return site
    return None
