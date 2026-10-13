"""The Anti-Bypass access kinds: your own password, an authenticator code (TOTP) and the recovery code.

Unlike math or a typed phrase these don't cost effort, they need something you may not have at hand - strongest when
SOMEONE ELSE sets the password or keeps the authenticator, so you can't loosen a block without them. They are step kinds
of the Anti-Bypass list only (challenges.SECRET_KINDS), never of the bedtime screen.

- Own password: kept only as a salted scrypt hash (never the password itself), compared in constant time. Forgotten /
  lost (the authenticator too) = only uninstalling Lockdown gets you out: the uninstaller's challenge asks the list
  WITHOUT the password and authenticator steps (antibypass.uninstall_steps), and uninstalling keeps your settings, so a
  new install has the same setup again.
- Authenticator code (RFC 6238): 6 digits, 30 s, HMAC-SHA1, the step before and after are accepted (a phone clock a
  little off), and a code that was accepted once is never accepted again (nor any older one). Set up by scanning a QR
  code (otpauth://totp/Lockdown:<pc>?secret=...&issuer=Lockdown) and typing one current code to confirm.
- Recovery code (optional, none by default): a one-time master key - 16 characters in 4 groups from an alphabet without
  look-alikes, kept only as a hash. Making one (or a new one) asks the challenge, so it is never free; it is shown ONCE
  (print it or write it down). Typing it passes ONE whole Anti-Bypass challenge run (every step, any kind); it is then
  used up and a NEW code is made at once and shown once ("the old one no longer works"). Removing it is free (stricter).
  Wrong codes count as wrong tries (the slowdown below).

Where they are kept: the setting SECRETS_KEY (JSON) in Lockdown's database, like every other setting. Backups leave it
out and a restore leaves it alone (backup.RUNTIME_KEYS). Honest limit: the authenticator's secret has to be stored in a
usable form (it must make the codes), so someone with administrator rights could read it from the database - just as
they could change any Lockdown setting there. Lockdown has no other protected store for it.

Part of Anti-Bypass: read the ANTI-BYPASS RULE note at the top of antibypass.py before changing anything here.
"""
import base64
import hashlib
import hmac
import json
import platform
import re
import secrets as _random
import struct
import time
from datetime import datetime
from urllib.parse import quote

SECRETS_KEY = "antibypass.secrets"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}          # ~0.1 s and 32 MiB per check: slow to guess, fine to type once
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES, HASH_BYTES = 16, 32
PASSWORD_MIN, PASSWORD_MAX = 4, 200
TOTP_DIGITS, TOTP_PERIOD, TOTP_WINDOW = 6, 30, 1
TOTP_SECRET_BYTES = 20                             # 160 bits, as RFC 4226 recommends
RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"   # 32 signs: no 0 / O, no 1 / I
RECOVERY_LEN, RECOVERY_GROUP = 16, 4               # 80 bits, written ABCD-EFGH-JKLM-NPQR
FREE_TRIES = 3                                     # wrong tries in a row before each next one has to wait
MAX_WAIT_SEC = 300                                 # ... 1 s, 2 s, 4 s ... up to this
_CODE = re.compile(r"[0-9]{%d}" % TOTP_DIGITS)
LABELS = {"password": "own password", "totp": "authenticator"}


# ---------------------------------------------------------------- hashing (password, recovery code)

def hash_secret(text: str, salt: bytes | None = None) -> dict:
    """A salted scrypt hash of `text`, with what is needed to check it again: {kdf, n, r, p, salt, hash} (hex)."""
    salt = salt if salt is not None else _random.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(text.encode("utf-8"), salt=salt, dklen=HASH_BYTES, maxmem=SCRYPT_MAXMEM, **SCRYPT)
    return {"kdf": "scrypt", **SCRYPT, "salt": salt.hex(), "hash": digest.hex()}


def verify_secret(text, record) -> bool:
    """Does `text` hash to `record` (hash_secret's)? Compared in constant time; anything unreadable is False."""
    if not isinstance(text, str) or not isinstance(record, dict) or record.get("kdf") != "scrypt":
        return False
    try:
        n, r, p = int(record["n"]), int(record["r"]), int(record["p"])
        if not (2 <= n <= 2 ** 20 and 1 <= r <= 32 and 1 <= p <= 16):
            return False
        want = bytes.fromhex(record["hash"])
        got = hashlib.scrypt(text.encode("utf-8"), salt=bytes.fromhex(record["salt"]), n=n, r=r, p=p,
                             dklen=len(want), maxmem=SCRYPT_MAXMEM * 4)
    except (KeyError, TypeError, ValueError, MemoryError):
        return False
    return hmac.compare_digest(got, want)


def check_password_text(value) -> str:
    """A new password as typed: 4 to 200 characters, printable, not only spaces. Kept exactly (spaces count)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Type a password.")
    if len(value) < PASSWORD_MIN:
        raise ValueError(f"At least {PASSWORD_MIN} characters, please.")
    if len(value) > PASSWORD_MAX:
        raise ValueError(f"At most {PASSWORD_MAX} characters, please.")
    if not value.isprintable():
        raise ValueError("Letters, numbers, spaces and punctuation only.")
    return value


# ---------------------------------------------------------------- authenticator codes (RFC 4226 / 6238)

def hotp(key: bytes, counter: int, digits: int = TOTP_DIGITS, digest: str = "sha1") -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    number = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return str(number).zfill(digits)


def totp(key: bytes, at: float, digits: int = TOTP_DIGITS, period: int = TOTP_PERIOD, digest: str = "sha1") -> str:
    """The code at unix time `at`."""
    return hotp(key, int(at // period), digits, digest)


def new_totp_secret() -> str:
    """A fresh authenticator secret, base32 without padding (what the QR code and the key text carry)."""
    return base64.b32encode(_random.token_bytes(TOTP_SECRET_BYTES)).decode("ascii").rstrip("=")


def secret_key(secret: str) -> bytes:
    """The key bytes of a base32 secret (spaces and lower case allowed, padding optional)."""
    text = re.sub(r"\s", "", str(secret)).upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def match_code(secret: str, code, at: float, last: int = -1) -> int | None:
    """The time step the 6-digit `code` belongs to - the one of `at` or the step just before or after - when it is
    newer than `last` (the step of the code accepted last: a code is never accepted twice); else None. Every candidate
    is compared in constant time."""
    if not isinstance(code, str):
        return None
    code = re.sub(r"\s", "", code)
    if not _CODE.fullmatch(code):
        return None
    key, now = secret_key(secret), int(at // TOTP_PERIOD)
    found = None
    for step in range(now - TOTP_WINDOW, now + TOTP_WINDOW + 1):
        if hmac.compare_digest(hotp(key, step), code) and step > last and found is None:
            found = step
    return found


def otpauth_uri(secret: str, name: str | None = None) -> str:
    """otpauth://totp/Lockdown:<pc name>?secret=...&issuer=Lockdown (the QR code's text)."""
    label = quote("Lockdown") + ":" + quote(name or pc_name(), safe="")
    return (f"otpauth://totp/{label}?secret={secret}&issuer=Lockdown&algorithm=SHA1&digits={TOTP_DIGITS}"
            f"&period={TOTP_PERIOD}")


def pc_name() -> str:
    return (platform.node() or "PC").split(".")[0][:40] or "PC"


def qr_svg(text: str) -> str:
    """The QR code of `text` as inline SVG (black on white, so it scans in a dark theme too); "" when segno is missing
    (the key text is then the way to set it up)."""
    try:
        import segno
    except ImportError:
        return ""
    return segno.make(text, error="m", micro=False).svg_inline(scale=5, border=3, dark="#000", light="#fff")


def group_key(secret: str) -> str:
    """The secret as it is easiest to type: groups of 4 ("ABCD EFGH ...")."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# ---------------------------------------------------------------- recovery codes

def new_recovery_code() -> str:
    raw = "".join(_random.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_LEN))
    return "-".join(raw[i:i + RECOVERY_GROUP] for i in range(0, RECOVERY_LEN, RECOVERY_GROUP))


def clean_recovery(value) -> str:
    """A recovery code as typed -> its 16 signs (case, spaces and dashes don't matter), or "" when it can't be one."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"[\s-]", "", value).upper()
    return text if len(text) == RECOVERY_LEN and all(c in RECOVERY_ALPHABET for c in text) else ""


# ---------------------------------------------------------------- what is stored

def load(db) -> dict:
    """The stored secrets: {"password": {...hash, set}, "totp": {secret, set, last}, "recovery": {...hash, made},
    "tries": {fails, next}} - each part only when it is there ("tries": wrong tries in a row, see Checker)."""
    try:
        data = json.loads(db.get_setting(SECRETS_KEY, "") or "{}")
    except ValueError:
        data = {}
    return {k: v for k, v in data.items() if k in ("password", "totp", "recovery", "tries") and isinstance(v, dict)} \
        if isinstance(data, dict) else {}


def _save(db, data: dict):
    db.set_setting(SECRETS_KEY, json.dumps(data, sort_keys=True))


def ready(db) -> set:
    """The secret step kinds that can be asked: {"password", "totp"} as far as they are set up."""
    return set(load(db)) & {"password", "totp"}


def _since(text) -> str | None:
    return text if isinstance(text, str) and text else None


def status(db) -> dict:
    """What the Anti-Bypass page may show - never a secret or a hash: {password: {set, since}, totp: {set, since},
    recovery: {exists, made}}."""
    data = load(db)
    return {"password": {"set": "password" in data, "since": _since(data.get("password", {}).get("set"))},
            "totp": {"set": "totp" in data, "since": _since(data.get("totp", {}).get("set"))},
            "recovery": {"exists": "recovery" in data, "made": _since(data.get("recovery", {}).get("made"))}}


def set_password(db, text: str, now):
    """Store a new password (its hash). No recovery code comes with it (forgotten = uninstalling is the way out)."""
    data = load(db)
    data["password"] = {**hash_secret(check_password_text(text)), "set": now.strftime(TIME_FMT)}
    _save(db, data)


def set_totp(db, secret: str, step: int, now):
    """Store the authenticator confirmed with the code of time step `step` (that code is then used). No recovery code
    comes with it."""
    secret_key(secret)                       # (a bad secret fails here, before anything is stored)
    data = load(db)
    data["totp"] = {"secret": secret, "set": now.strftime(TIME_FMT), "last": int(step)}
    _save(db, data)


def remove(db, kind: str):
    """Forget the password, the authenticator or the recovery code (`kind` "password" / "totp" / "recovery")."""
    data = load(db)
    if data.pop(kind, None) is not None:
        _save(db, data)


def _new_recovery(data: dict, now) -> str:
    code = new_recovery_code()
    data["recovery"] = {**hash_secret(clean_recovery(code)), "made": now.strftime(TIME_FMT)}
    return code


def make_recovery(db, now) -> str:
    """A new recovery code (the old one stops working). Returns it - the only time it is ever shown."""
    data = load(db)
    code = _new_recovery(data, now)
    _save(db, data)
    return code


def trusted_epoch(db) -> float:
    """Unix time by Lockdown's trusted clock (the system clock plus the offset the service publishes), so turning the
    Windows clock back can't bring an old authenticator code to life."""
    import trusted_time
    try:
        return time.time() + float(db.get_setting(trusted_time.OFFSET_KEY, "0") or 0)
    except (TypeError, ValueError):
        return time.time()


class Checker:
    """Checks what is typed for a password / authenticator step or the recovery code, for the challenge runs
    (challenges.Run). Wrong tries in a row are counted in the database ("tries"), so across runs, restarts and the
    uninstaller's own window: after FREE_TRIES of them each next try has to wait (1 s, 2 s, 4 s ... up to MAX_WAIT_SEC,
    by the trusted clock) - guessing a 6-digit code by a script would take years. A right answer clears the count.
    `db_provider()` gives the calling thread's Database; `epoch(db)` the trusted unix time (default trusted_epoch)."""

    def __init__(self, db_provider, epoch=None):
        self._db, self._epoch = db_provider, epoch

    def _now(self, db) -> float:
        return (self._epoch or trusted_epoch)(db)

    def wait_left(self) -> float:
        """Seconds until the next try is looked at (0 = now; never more than MAX_WAIT_SEC, whatever a clock did)."""
        db = self._db()
        tries = load(db).get("tries") or {}
        try:
            left = float(tries.get("next") or 0) - self._now(db)
        except (TypeError, ValueError):
            left = 0.0
        return max(0.0, min(left, MAX_WAIT_SEC))

    def fails(self) -> int:
        try:
            return int((load(self._db()).get("tries") or {}).get("fails") or 0)
        except (TypeError, ValueError):
            return 0

    def _result(self, right: bool) -> bool:
        db = self._db()
        data = load(db)
        try:
            fails = int((data.get("tries") or {}).get("fails") or 0)
        except (TypeError, ValueError):
            fails = 0
        if right and fails:
            data.pop("tries", None)
            _save(db, data)
        elif not right:
            fails += 1
            wait = min(MAX_WAIT_SEC, 2 ** (fails - FREE_TRIES)) if fails >= FREE_TRIES else 0
            data["tries"] = {"fails": fails, "next": self._now(db) + wait if wait else 0}
            _save(db, data)
        return right

    def ready(self) -> set:
        return ready(self._db())

    def has_recovery(self) -> bool:
        return "recovery" in load(self._db())

    def check_password(self, value) -> bool:
        return self._result(verify_secret(value, load(self._db()).get("password")))

    def check_totp(self, value) -> bool:
        """A right code that is newer than the last one accepted; it is then the last one (never accepted again)."""
        db = self._db()
        data = load(db)
        record = data.get("totp")
        step = None
        if record:
            try:
                step = match_code(record["secret"], value, self._now(db), int(record.get("last", -1)))
            except (KeyError, TypeError, ValueError):
                step = None
        if step is not None:
            data["totp"] = {**record, "last": step}
            _save(db, data)
        return self._result(step is not None)

    def use_recovery(self, value) -> str | None:
        """The recovery code typed: when it is the stored one it is used up and a NEW code takes its place at once
        (made "now" by the trusted clock) - returned to be shown once; else None (a wrong try, counted like any other)."""
        db = self._db()
        data = load(db)
        record = data.get("recovery")
        code = clean_recovery(value)
        if not (record and code and verify_secret(code, record)):
            self._result(False)
            return None
        new = _new_recovery(data, datetime.fromtimestamp(self._now(db)))
        data.pop("tries", None)              # (a right answer clears the wrong tries)
        _save(db, data)
        return new
