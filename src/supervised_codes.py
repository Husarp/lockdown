"""Local authenticator for other people's keys. Never exported, never returns key material to the UI."""
import base64
import hashlib
import hmac
import json
import math
import re
import secrets
from urllib.parse import parse_qs, unquote, urlsplit

import challenge_secrets as cs

KEY = "antibypass.supervised"


def parse_key(value, period=cs.SUPERVISOR_PERIOD):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("Paste a base32 key or an otpauth:// URI.")
    value = value.strip()
    if value.lower().startswith("otpauth:"):
        try:
            if re.search(r"[\s\x00-\x1f\x7f]|%(?![0-9a-fA-F]{2})", value) or "#" in value:
                raise ValueError()
            uri = urlsplit(value)
            params = parse_qs(uri.query, keep_blank_values=True, strict_parsing=True, errors="strict")
            label = unquote(uri.path[1:], errors="strict")
            if (uri.netloc.lower() != "totp" or not label.strip() or not label.isprintable()
                    or "/" in uri.path[1:]
                    or set(params) - {"secret", "issuer", "algorithm", "digits", "period"}
                    or any(len(v) != 1 or not v[0].strip() or not v[0].isprintable() for v in params.values())
                    or params.get("algorithm", ["SHA1"])[0].upper() != "SHA1"
                    or params.get("digits", ["6"]) != ["6"]
                    or params.get("period", ["30"])[0] not in ("30", "40")):
                raise ValueError()
            period = int(params.get("period", ["30"])[0])
            value = params.get("secret", [""])[0]
            if not re.fullmatch(r"[A-Za-z2-7]+=*", value):
                raise ValueError()
        except ValueError:
            raise ValueError("Use a valid TOTP link with SHA1, 6 digits and a 30- or 40-second period.") from None
    try:
        key = cs.secret_key(value)
        if not key:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("That is not a valid base32 key.") from None
    return base64.b32encode(key).decode("ascii").rstrip("="), cs.pairing_period(period)


def _own(db, secret, pending=()):
    # HMAC pads short keys with zeroes: a key with extra trailing zero bytes gives the SAME codes.
    def normalized(value):
        key = cs.secret_key(value)
        return (hashlib.sha1(key).digest() if len(key) > 64 else key).ljust(64, b"\0")

    key = normalized(secret)
    own = [v.get("secret") for k, v in cs.load(db).items() if k in ("totp", "supervisor")]
    return any(hmac.compare_digest(key, normalized(s)) for s in [*own, *pending] if s)


def _load(db):
    return json.loads(db.get_setting(KEY, "[]") or "[]")


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 100 or not value.isprintable():
        raise ValueError("Give this key a name (1 to 100 printable characters).")
    return value.strip()


def change(db, action, name="", value="", entry_id="", pending=(), period=cs.SUPERVISOR_PERIOD):
    rows = _load(db)
    if action == "add":
        secret, period = parse_key(value, period)
        if _own(db, secret, pending):
            raise ValueError("You cannot supervise this PC's own supervisor or authenticator key. Use someone else's key.")
        rows.append({"id": secrets.token_hex(12), "name": _name(name), "secret": secret, "period": period})
    elif action in ("rename", "delete"):
        row = next((r for r in rows if r["id"] == entry_id), None)
        if row is None:
            raise ValueError("That supervised key no longer exists.")
        if action == "rename":
            row["name"] = _name(name)
        else:
            rows.remove(row)
    else:
        raise ValueError("Unknown supervised key action.")
    db.set_setting(KEY, json.dumps(rows))


def codes(db, pending=()):
    now = cs.trusted_epoch(db)
    entries = []
    for row in _load(db):
        if _own(db, row["secret"], pending):
            continue
        period = cs.pairing_period(row.get("period", cs.TOTP_PERIOD))
        remaining = math.ceil((int(now // period) + 1) * period - now)
        entries.append({"id": row["id"], "name": row["name"], "period": period,
                        "code": cs.totp(cs.secret_key(row["secret"]), now, period=period),
                        "remaining": remaining, "expires_in": remaining + period})
    return {"entries": entries}
