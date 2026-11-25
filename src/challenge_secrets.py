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
- Supervisor codes: a separate TOTP secret, with the same storage, trusted clock, replay protection and slowdown.
  Pairings use 40 seconds when new and 30 seconds if legacy. Only the current and previous counters are accepted.
  An accepted code authorizes only the pending action, skipping its challenges and waits (a general unlock only for the explicit "Unlock to edit" action).
  Setup, replacement and removal go through the normal guard (or an existing supervisor code).
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
import sqlite3
import struct
import time
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import quote

SECRETS_KEY = "antibypass.secrets"
TIME_FMT = "%Y-%m-%d %H:%M:%S"
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}          # ~0.1 s and 32 MiB per check: slow to guess, fine to type once
SCRYPT_MAXMEM = 64 * 1024 * 1024
SALT_BYTES, HASH_BYTES = 16, 32
PASSWORD_MIN, PASSWORD_MAX = 4, 200
TOTP_DIGITS, TOTP_PERIOD, TOTP_WINDOW = 6, 30, 1
SUPERVISOR_PERIOD = 40
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


def pairing_period(value) -> int:
    if type(value) is not int or value not in (TOTP_PERIOD, SUPERVISOR_PERIOD):
        raise ValueError("Choose a 30- or 40-second period.")
    return value


def match_supervisor(secret: str, code, at: float, period: int, last: int = -1) -> int | None:
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        return None
    # Oldest first: the newest match wins unless the same value was already consumed and is still live.
    key, now = secret_key(secret), int(at // pairing_period(period))
    found, replay = None, False
    for step in range(max(0, now - 1), now + 1):
        if hmac.compare_digest(hotp(key, step), code):
            if step <= last:
                replay = True
            else:
                found = step
    return None if replay else found


def otpauth_uri(secret: str, name: str | None = None, period: int = TOTP_PERIOD) -> str:
    """otpauth://totp/Lockdown:<pc name>?secret=...&issuer=Lockdown (the QR code's text)."""
    label = quote("Lockdown") + ":" + quote(name or pc_name(), safe="")
    return (f"otpauth://totp/{label}?secret={secret}&issuer=Lockdown&algorithm=SHA1&digits={TOTP_DIGITS}"
            f"&period={period}")


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
    """The stored secrets: {"password": {...hash, set}, "totp": {secret, set, last}, "supervisor": {secret, set, last}, "recovery": {...hash, made},
    "tries": {fails, next}} - each part only when it is there ("tries": wrong tries in a row, see Checker)."""
    try:
        data = json.loads(db.get_setting(SECRETS_KEY, "") or "{}")
    except ValueError:
        data = {}
    return {k: v for k, v in data.items() if k in ("password", "totp", "supervisor", "recovery", "tries") and isinstance(v, dict)} \
        if isinstance(data, dict) else {}


@contextmanager
def _mutation(db):
    """Serialize the whole read/modify/write on the calling thread's SQLite connection."""
    conn = db.conn
    deadline = time.monotonic() + 5
    old_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.execute("PRAGMA busy_timeout=150")
    try:
        while True:
            try:
                conn.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as exc:
                if (exc.sqlite_errorcode & 0xff) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        with conn:
            data = load(db)
            before = json.dumps(data, sort_keys=True)
            yield data
            after = json.dumps(data, sort_keys=True)
            if after != before:
                conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (SECRETS_KEY, after))
    finally:
        conn.execute(f"PRAGMA busy_timeout={old_timeout}")


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
            "supervisor": {"set": "supervisor" in data, "since": _since(data.get("supervisor", {}).get("set")),
                           "period": data.get("supervisor", {}).get("period", TOTP_PERIOD)},
            "recovery": {"exists": "recovery" in data, "made": _since(data.get("recovery", {}).get("made"))}}


def set_password(db, text: str, now):
    """Store a new password (its hash). No recovery code comes with it (forgotten = uninstalling is the way out)."""
    record = {**hash_secret(check_password_text(text)), "set": now.strftime(TIME_FMT)}
    with _mutation(db) as data:
        data["password"] = record


def set_totp(db, secret: str, step: int, now, kind="totp", period=TOTP_PERIOD):
    """Store the authenticator and consume its confirmation counter."""
    if kind not in ("totp", "supervisor"):
        raise ValueError("Unknown authenticator kind.")
    secret_key(secret)
    with _mutation(db) as data:
        data[kind] = {"secret": secret, "set": now.strftime(TIME_FMT), "last": int(step)}
        if kind == "supervisor":
            data[kind]["period"] = pairing_period(period)


def confirm_supervisor(db, secret, step, now, period, replaces):
    """Check the authorized setup against the still-active pairing and commit the consumed counter."""
    with _mutation(db) as data:
        active = data.get("supervisor", {}).get("secret")
        if active != replaces:
            raise ValueError("The supervisor pairing changed. Start setup again.")
        data["supervisor"] = {"secret": secret, "set": now.strftime(TIME_FMT), "last": step, "period": period}


def remove(db, kind: str):
    """Forget a stored secret."""
    with _mutation(db) as data:
        data.pop(kind, None)


def _new_recovery(data: dict, now) -> str:
    code = new_recovery_code()
    data["recovery"] = {**hash_secret(clean_recovery(code)), "made": now.strftime(TIME_FMT)}
    return code


def make_recovery(db, now) -> str:
    """A new recovery code (the old one stops working). Returns it - the only time it is ever shown."""
    record = {}
    code = _new_recovery(record, now)
    with _mutation(db) as data:
        data.update(record)
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
        return self._wait(load(db), self._now(db))

    @staticmethod
    def _wait(data, now) -> float:
        try:
            left = float((data.get("tries") or {}).get("next") or 0) - now
        except (TypeError, ValueError):
            left = 0.0
        return max(0.0, min(left, MAX_WAIT_SEC))

    def fails(self) -> int:
        try:
            return int((load(self._db()).get("tries") or {}).get("fails") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _result(data, right: bool, now: float) -> bool:
        if right:
            data.pop("tries", None)
        else:
            try:
                fails = int((data.get("tries") or {}).get("fails") or 0) + 1
            except (TypeError, ValueError):
                fails = 1
            wait = min(MAX_WAIT_SEC, 2 ** min(9, fails - FREE_TRIES)) if fails >= FREE_TRIES else 0
            data["tries"] = {"fails": fails, "next": now + wait if wait else 0}
        return right

    def ready(self) -> set:
        return ready(self._db())

    def has_recovery(self) -> bool:
        return "recovery" in load(self._db())

    def check_password(self, value) -> bool:
        db = self._db()
        record = load(db).get("password")
        right = verify_secret(value, record)
        with _mutation(db) as data:
            now = self._now(db)
            if self._wait(data, now) or data.get("password") != record:
                return False
            return self._result(data, right, now)

    def check_totp(self, value, kind="totp") -> bool:
        if kind == "supervisor":
            return self.check_supervisor(value) is True
        return self._consume_code(value, "totp") is True

    def check_supervisor(self, value) -> bool | None:
        """True after a committed approval, None during slowdown, False for a refused code."""
        return self._consume_code(value, "supervisor")

    def _consume_code(self, value, kind) -> bool | None:
        db = self._db()
        with _mutation(db) as data:
            now = self._now(db)
            if self._wait(data, now):
                return None
            record = data.get(kind)
            if not record:
                return False
            step = None
            try:
                last = int(record.get("last", -1))
                if kind == "supervisor":
                    step = match_supervisor(record["secret"], value, now, record.get("period", TOTP_PERIOD), last)
                else:
                    step = match_code(record["secret"], value, now, last)
            except (KeyError, TypeError, ValueError):
                pass
            if step is not None:
                record["last"] = step
            return self._result(data, step is not None, now)

    def use_recovery(self, value) -> str | None:
        """Consume the old recovery code and make a new one atomically."""
        db = self._db()
        record = load(db).get("recovery")
        code = clean_recovery(value)
        right = bool(record and code and verify_secret(code, record))
        with _mutation(db) as data:
            now = self._now(db)
            if self._wait(data, now) or data.get("recovery") != record:
                return None
            self._result(data, right, now)
            if right:
                return _new_recovery(data, datetime.fromtimestamp(now))
