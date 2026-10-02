"""Is there a newer Lockdown? Asks GitHub for the latest release of the repository in version.py, and can
fetch and start the installer so you never have to visit a web page.

Nothing is sent but the request itself - no account, no identifier. If you leave "Check for updates
automatically" on, it checks every time you open the window and every EVERY_HOURS hours in the background - and
whenever you press the button on the About page. Two requests are never less than RETRY_MIN minutes apart, so even
opening the window over and over stays far inside GitHub's 60 an hour for a caller without an account; a failed
check is tried again on the next poll. With no repository set (REPO empty)
there is nothing to ask, and the button isn't there. Standard library only.

What was found is kept (FOUND_KEY), so "Lockdown X is available" stays on screen - the banner over every page, a
dot on About, a line in the tray menu - until it is installed, you skip that version, or you ask to be reminded
later (hidden for SNOOZE_HOURS, then back).
"""
import json
import re
import urllib.request
from datetime import datetime, timedelta

from version import REPO, VERSION

API = "https://api.github.com/repos/{repo}/releases/latest"
PAGE = "https://github.com/{repo}"
RELEASES = "https://github.com/{repo}/releases/latest"
TIMEOUT = 8
TIME_FMT = "%Y-%m-%d %H:%M:%S"
CHUNK = 64 * 1024

AUTO_KEY = "updates.auto"          # check by itself (every few hours)
NOTIFY_KEY = "updates.notify"      # and say so (the corner popup) when it finds one
LAST_KEY = "updates.last_check"    # when the last check that got an answer happened
TRIED_KEY = "updates.last_try"     # when the last check was started, answered or not (spaces out retries)
SEEN_KEY = "updates.seen"          # the newest version the popup has already told you about
FOUND_KEY = "updates.found"        # the newest release found (JSON), so the banner survives a restart
SNOOZE_KEY = "updates.snooze_until"   # "Remind me later": popup and banner hidden until then (trusted time)
SKIP_KEY = "updates.skipped"       # the banner's x: this version (and older) is not mentioned again
EVERY_HOURS = 6                    # the automatic check in the background
OPEN_HOURS = 0                     # opening the window always checks (RETRY_MIN still spaces the requests)
RETRY_MIN = 5                      # never two requests within this many minutes (opening and closing the window)
SNOOZE_HOURS = 4                   # "Remind me later"


def numbers(version: str) -> tuple:
    """"v0.73.1" -> (0, 73, 1). Anything unparseable is (0,), which never counts as newer."""
    found = [int(n) for n in re.findall(r"\d+", version)[:4]]
    return tuple(found) if found else (0,)


def is_newer(latest: str, current: str = VERSION) -> bool:
    return numbers(latest) > numbers(current)


def repo_page() -> str | None:
    return PAGE.format(repo=REPO) if REPO else None


def installer_asset(assets) -> tuple[str, int] | None:
    """The .exe attached to a release - what to download. Releases carry other files too."""
    for a in assets or []:
        if (a.get("name") or "").lower().endswith(".exe") and a.get("browser_download_url"):
            return a["browser_download_url"], int(a.get("size") or 0)
    return None


def latest_release(fetch=None) -> dict | None:
    """{"version", "url", "newer", "asset", "size"} for the newest release on GitHub, or None if that can't be
    answered (no repository set, no network, no releases yet). `fetch` is for the tests."""
    if not REPO:
        return None
    try:
        if fetch is None:
            def fetch(url):
                request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                               "User-Agent": f"Lockdown/{VERSION}"})
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    return response.read()
        data = json.loads(fetch(API.format(repo=REPO)))
        tag = (data.get("tag_name") or data.get("name") or "").strip()
        if not tag:
            return None
        found = installer_asset(data.get("assets"))
        return {"version": tag.lstrip("vV"), "url": data.get("html_url") or RELEASES.format(repo=REPO),
                "newer": is_newer(tag), "asset": found[0] if found else None,
                "size": found[1] if found else 0}
    except Exception:
        return None


def can_install(found: dict | None) -> bool:
    """Only ever forwards. Lockdown is a blocker: installing an older build would be a way to drop the rules
    you set, so "update" never means "go back". Without an installer attached there is nothing to run either."""
    return bool(found and found.get("newer") and found.get("asset"))


def download(url: str, dest, progress=None, opener=None):
    """Fetch the installer to `dest`, calling progress(bytes_so_far, bytes_total) as it goes. `opener` is for
    the tests. The caller runs this off the Tk thread."""
    request = urllib.request.Request(url, headers={"User-Agent": f"Lockdown/{VERSION}"})
    open_url = opener or (lambda r: urllib.request.urlopen(r, timeout=TIMEOUT))
    with open_url(request) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as out:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    return dest


def install(path, run=None):
    """Start the downloaded installer; Lockdown closes straight after so its own files can be replaced. `run` is
    for the tests.

    Through the shell (ShellExecute, "runas"), not subprocess: the installer is built to require admin, and
    CreateProcess - what subprocess uses - can't ask for that. Windows refused with error 740 ("The requested
    operation requires elevation"), the error went nowhere and the update sat at 100% for good. ShellExecute shows
    the admin prompt. Raises OSError if it couldn't be started (or you said No), so the caller can say so.
    With INSTALLER_ARGS ("--update") the installer asks nothing - you already said Install here - shows only its
    progress, starts Lockdown again and closes by itself."""
    (run or _run_elevated)(path)


INSTALLER_ARGS = "--update"


def _run_elevated(path):
    import ctypes
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(path), INSTALLER_ARGS, None, 1)
    if result <= 32:   # ShellExecute: anything up to 32 is an error code (5 = refused / No on the admin prompt)
        raise OSError(f"the installer could not be started (ShellExecute {result})")


# ---------- the automatic check ----------

def auto_on(db) -> bool:
    return db.get_setting(AUTO_KEY, "1") == "1"


def notify_on(db) -> bool:
    return db.get_setting(NOTIFY_KEY, "1") == "1"


def _time(db, key: str) -> datetime | None:
    try:
        return datetime.strptime(db.get_setting(key, ""), TIME_FMT)
    except ValueError:
        return None


def due(db, now: datetime, hours: float = EVERY_HOURS) -> bool:
    """Every `hours` (EVERY_HOURS; OPEN_HOURS when the window is opened; 0 when Lockdown starts), only while
    you leave it switched on. A check that got no answer is not written down as done, so the next poll tries
    again - but never within RETRY_MIN minutes of the last try, so a GitHub that is down or refusing (rate
    limit) is not asked every minute."""
    if not REPO or not auto_on(db):
        return False
    tried = _time(db, TRIED_KEY)
    if tried and timedelta(0) <= now - tried < timedelta(minutes=RETRY_MIN):
        return False
    last = _time(db, LAST_KEY)
    return last is None or not timedelta(0) <= now - last < timedelta(hours=hours)


def trying(db, now: datetime):
    """A check is starting (answered or not)."""
    db.set_setting(TRIED_KEY, now.strftime(TIME_FMT))


def checked(db, now: datetime, found: dict | None = None):
    """A check got an answer. `found` (when given) is kept, so what it found stays on screen across restarts."""
    db.set_setting(LAST_KEY, now.strftime(TIME_FMT))
    if found is not None:
        remember(db, found)


def remember(db, found: dict | None):
    """Keep the newest release found - only if it is newer than this version; anything else clears it."""
    if found and found.get("newer") and found.get("version"):
        keep = {k: found.get(k) for k in ("version", "url", "asset", "size")}
        db.set_setting(FOUND_KEY, json.dumps(keep))
    elif found is not None:
        db.set_setting(FOUND_KEY, "")


def available(db) -> dict | None:
    """The release found last time, if it is still newer than the version running now. Once it is installed
    (or something newer than it is), there is nothing to show - never an "update" to an older build."""
    try:
        found = json.loads(db.get_setting(FOUND_KEY, "") or "null")
    except ValueError:
        return None
    if not isinstance(found, dict) or not found.get("version") or not is_newer(str(found["version"]), VERSION):
        return None
    found["newer"] = True
    found.setdefault("size", 0)
    return found


# ---------- "Remind me later" and "Skip this version" ----------

def snooze(db, now: datetime, hours: float = SNOOZE_HOURS):
    """Remind me later: popup and banner hidden for `hours`, then both come back (the popup once more)."""
    db.set_setting(SNOOZE_KEY, (now + timedelta(hours=hours)).strftime(TIME_FMT))
    db.set_setting(SEEN_KEY, "")


def snoozed(db, now: datetime) -> bool:
    until = _time(db, SNOOZE_KEY)
    # (a snooze more than SNOOZE_HOURS ahead can only come from a clock that jumped back: don't let it hide
    # the update for days)
    return bool(until and now < until <= now + timedelta(hours=SNOOZE_HOURS))


def skip(db, version: str):
    """The banner's x: don't mention this version (or anything older) again; a newer one is news again."""
    db.set_setting(SKIP_KEY, version)


def skipped(db, found: dict | None) -> bool:
    mark = db.get_setting(SKIP_KEY, "")
    return bool(found and mark and not is_newer(found["version"], mark))


def banner(db, now: datetime) -> dict | None:
    """What the in-app banner (and the dot on About, and the tray's "Update available") should show: the
    release found, while it is newer than this version, not skipped and not snoozed - else None. Not affected
    by the popup setting or by Do not disturb: it sits quietly in the window, it doesn't interrupt."""
    found = available(db)
    if not found or skipped(db, found) or snoozed(db, now):
        return None
    return found


def worth_saying(db, found: dict | None, now: datetime | None = None) -> bool:
    """The corner popup: once per new version (and once more after "Remind me later" runs out), not once per
    check forever - never for a skipped version, while snoozed, or if you turned the notice off."""
    if not found or not found.get("newer") or not notify_on(db) or skipped(db, found):
        return False
    if now is not None and snoozed(db, now):
        return False
    return db.get_setting(SEEN_KEY, "") != found["version"]


def said(db, version: str):
    db.set_setting(SEEN_KEY, version)


def label(found: dict) -> str:
    """The banner's / tray's words."""
    return f"Lockdown {found['version']} is available"
