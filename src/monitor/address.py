"""Classify a value read from browser chrome, independently of the block-list format."""
import ipaddress
import re
from urllib.parse import urlsplit

LAST_VALID_SEC = 10

_INTERNAL = re.compile(r"^(?:about|arc|blob|brave|chrome|chrome-extension|chrome-untrusted|data|devtools|edge|"
                       r"edge-extension|file|moz-extension|opera|resource|vivaldi|waterfox|view-source|zen):\S+$",
                       re.IGNORECASE)
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
_WEB_SCHEME = re.compile(r"^https?://", re.IGNORECASE)


def classify_address(value: str | None) -> tuple[str | None, str | None]:
    """Return (address, normalized host). Browser-internal pages have no site host."""
    address = value.strip() if isinstance(value, str) else ""
    if not address or any(c.isspace() or ord(c) < 32 or c == "\\" for c in address):
        return None, None
    if _INTERNAL.fullmatch(address):
        return address, None
    explicit = bool(_WEB_SCHEME.match(address))
    if not explicit and "://" in address:
        return None, None
    try:
        split = urlsplit(address if explicit else "//" + address)
        host = split.hostname
        if not host:
            return None, None
        split.port  # reject malformed ports
        if not explicit and split.username is not None:
            return None, None
        try:
            return address, str(ipaddress.ip_address(host))
        except ValueError:
            host = host.removesuffix(".").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return None, None
    if (not explicit and "." not in host and host != "localhost" and not split.path and split.port is None
            or len(host) > 253 or not all(_LABEL.fullmatch(label) for label in host.split("."))):
        return None, None
    return address, host.removeprefix("www.")


def valid_address(value: str | None) -> bool:
    return classify_address(value)[0] is not None
