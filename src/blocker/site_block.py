"""What happens to a blocked website (blocked_items.block_type on a site item): a comma-separated set of flags.

- "dns"   - it can't load at all: the hosts file and the DNS filter send it nowhere (what Lockdown always did)
- "close" - the tab in front is closed (the tray agent, like the bad-word check)
- "back"  - the browser goes back instead; if that doesn't leave the page, the tab is closed

close and back exclude each other. None (everything before 0.66) = "dns".
Standard library only: the service reads this too.
"""
FLAGS = ("dns", "close", "back")


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
