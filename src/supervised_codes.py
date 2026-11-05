"""Local authenticator for other people's keys. Never exported, never returns key material to the UI."""
import base64
import hashlib
import hmac
import json
import math
import secrets
from urllib.parse import parse_qs, urlparse

import challenge_secrets as cs

KEY = "antibypass.supervised"


def parse_key(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("Paste a base32 key or an otpauth:// URI.")
    value = value.strip()
    if value.lower().startswith("otpauth:"):
        uri = urlparse(value)
        params = parse_qs(uri.query, keep_blank_values=True)
        if (uri.scheme.lower() != "otpauth" or uri.netloc.lower() != "totp"
                or any(len(v) != 1 for v in params.values())
                or params.get("algorithm", ["SHA1"])[0].upper() != "SHA1"
                or params.get("digits", ["6"]) != ["6"]
                or params.get("period", ["30"]) != ["30"]):
            raise ValueError("Use a TOTP key with SHA1, 6 digits and a 30-second period.")
        value = params.get("secret", [""])[0]
    try:
        key = cs.secret_key(value)
        if not key:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("That is not a valid base32 key.") from None
    return base64.b32encode(key).decode("ascii").rstrip("=")


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


def change(db, action, name="", value="", entry_id="", pending=()):
    rows = _load(db)
    if action == "add":
        secret = parse_key(value)
        if _own(db, secret, pending):
            raise ValueError("You cannot supervise this PC's own supervisor or authenticator key. Use someone else's key.")
        rows.append({"id": secrets.token_hex(12), "name": _name(name), "secret": secret})
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
    expires = (int(now // cs.TOTP_PERIOD) + 1) * cs.TOTP_PERIOD
    return {"remaining": math.ceil(expires - now), "entries": [
        {"id": r["id"], "name": r["name"], "code": cs.totp(cs.secret_key(r["secret"]), now)}
        for r in _load(db) if not _own(db, r["secret"], pending)]}
