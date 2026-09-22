"""Is there a newer Lockdown? Asks GitHub for the latest release of the repository in version.py, and can
fetch and start the installer so you never have to visit a web page.

Nothing is sent but the request itself - no account, no identifier. The check runs once a day if you leave
"Check for updates automatically" on, and whenever you press the button on the About page. With no repository
set (REPO empty) there is nothing to ask, and the button isn't there. Standard library only.
"""
import json
import re
import subprocess
import urllib.request
from datetime import datetime, timedelta

from version import REPO, VERSION

API = "https://api.github.com/repos/{repo}/releases/latest"
PAGE = "https://github.com/{repo}"
RELEASES = "https://github.com/{repo}/releases/latest"
TIMEOUT = 8
TIME_FMT = "%Y-%m-%d %H:%M:%S"
CHUNK = 64 * 1024

AUTO_KEY = "updates.auto"          # check once a day by itself
NOTIFY_KEY = "updates.notify"      # and say so when it finds one
LAST_KEY = "updates.last_check"    # when the last check happened
SEEN_KEY = "updates.seen"          # the newest version you have already been told about
EVERY_HOURS = 24


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
    """Start the downloaded installer. It asks Windows for admin itself; Lockdown closes straight after so its
    own files can be replaced. `run` is for the tests."""
    (run or (lambda p: subprocess.Popen([str(p)])))(path)


# ---------- the daily check ----------

def auto_on(db) -> bool:
    return db.get_setting(AUTO_KEY, "1") == "1"


def notify_on(db) -> bool:
    return db.get_setting(NOTIFY_KEY, "1") == "1"


def due(db, now: datetime) -> bool:
    """Once a day, and only while you leave it switched on."""
    if not REPO or not auto_on(db):
        return False
    last = db.get_setting(LAST_KEY, "")
    if not last:
        return True
    try:
        return now - datetime.strptime(last, TIME_FMT) >= timedelta(hours=EVERY_HOURS)
    except ValueError:
        return True


def checked(db, now: datetime):
    db.set_setting(LAST_KEY, now.strftime(TIME_FMT))


def worth_saying(db, found: dict | None) -> bool:
    """Once per new version, not once a day forever - and never if you turned the notice off."""
    if not found or not found.get("newer") or not notify_on(db):
        return False
    return db.get_setting(SEEN_KEY, "") != found["version"]


def said(db, version: str):
    db.set_setting(SEEN_KEY, version)
