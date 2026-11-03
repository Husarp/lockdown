"""Validation for browser address reads (independent of the block-list host format)."""

LAST_VALID_SEC = 10


def valid_address(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    address = value.strip()
    # Address-bar placeholders are prose. Real addresses can name an IP, an IDN,
    # an intranet host or an internal browser page; block-list validation is too strict.
    return bool(address) and not any(c.isspace() for c in address)
