"""The in-app update, new window: check, download, install, quit - all off the GUI thread, with progress pushed to the
page. One UpdateFlow per agent; the Api (webapi/about.py) and the update notice ("Install") share it.

What may be downloaded is decided HERE, from Python's own records, never from anything the page sends:
- the release is the one the updates module kept (`updates.available`): always newer than the running version,
  so "update" never means "go back";
- the file is that release's installer asset, and its address must start with this repository's release-download
  address (version.REPO) - nothing else is ever fetched;
- the file name is built from the version, which must be plain letters / digits / dots.
The installer is started like today (ShellExecute "runas", `--update`), and only when it started does Lockdown quit -
by closing the window and tray, NOT through the tray's Exit: that one sets EXITED_KEY and keeps Lockdown off until the
next login, but an update is meant to come straight back.

Pushes (all through `publish(event, payload)`):
  download_progress  {state: downloading | ready | installing | failed, version, done, total, error}
  update             {seq, found, outcome}   after a check: `found` = the banner's release (or None), `outcome` = what
                     the About page says ({kind: unreachable | installable | page | newest, text, version, size,
                     installable})  - or {open_dialog: true} from the update notice's Install button.
"""
import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import updates
from version import REPO, VERSION

SAFE_VERSION = re.compile(r"[0-9][0-9A-Za-z.+_-]{0,30}")
QUIT_DELAY_SEC = 1.5          # the page shows "Starting the installer" before Lockdown goes
PROGRESS_EVERY_SEC = 0.1      # at most this many progress pushes (the publisher coalesces further)
MAX_NOTES = 8                 # lines of release notes shown in the update dialog
MAX_NOTE_CHARS = 160

# The shared flow of this process (set by attach()); webapi/about.py uses it when its Context has none.
current: "UpdateFlow | None" = None


def asset_ok(url) -> bool:
    """Is this the installer address of a release of OUR repository? Read as an address, not as text: https, github.com
    itself (no user, no port), our repository's release downloads, and no "." / ".." / escaped segment, query or
    fragment that could lead somewhere else (R28)."""
    if not (REPO and isinstance(url, str) and len(url) < 600) or "\\" in url or "%" in url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    segments = parts.path.split("/")
    return (parts.scheme == "https" and parts.netloc == "github.com" and not parts.query and not parts.fragment
            and parts.path.startswith(f"/{REPO}/releases/download/") and not parts.path.endswith("/")
            and not any(s in (".", "..") for s in segments[1:])
            and not any(ord(c) < 33 for c in url))


def public(found: dict | None) -> dict | None:
    """What the page may know about a release: version, size, whether it can be installed here."""
    if not found:
        return None
    return {"version": str(found.get("version")), "size": int(found.get("size") or 0),
            "installable": updates.can_install(found) and asset_ok(found.get("asset"))}


def outcome(found: dict | None) -> dict:
    """What a check found, in the About page's words (the same ones as the old window)."""
    if not found:
        return {"kind": "unreachable", "installable": False,
                "text": "Couldn't ask GitHub just now - no connection, or no release published yet."}
    version, size = str(found.get("version")), int(found.get("size") or 0)
    if updates.can_install(found):
        mb = f" ({size / 1048576:.0f} MB)" if size else ""
        return {"kind": "installable", "installable": asset_ok(found.get("asset")), "version": version, "size": size,
                "text": f"Lockdown {version} is out{mb}."}
    if found.get("newer"):   # a release with no installer attached: the page is all we can offer
        return {"kind": "page", "installable": False, "version": version, "size": 0,
                "text": f"Lockdown {version} is out - open the GitHub page to get it."}
    return {"kind": "newest", "installable": False, "text": "This is the newest version."}


def notes_from(body: str) -> list[str]:
    """The release text as a few plain lines: bullets first (they are the changelog), else the first lines.
    Markdown marks are dropped; the page shows them as text only."""
    lines = []
    for raw in str(body or "").splitlines():
        text = raw.strip()
        if not text or text.startswith(("#", "```", "---", "**Full Changelog")):
            continue
        bullet = re.match(r"^[-*+]\s+(.*)$", text)
        lines.append((bool(bullet), bullet.group(1) if bullet else text))
    chosen = [t for is_bullet, t in lines if is_bullet] or [t for _, t in lines]
    out = []
    for text in chosen:
        text = re.sub(r"[*_`]|!?\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", text).strip()
        if text:
            out.append(text[:MAX_NOTE_CHARS])
        if len(out) >= MAX_NOTES:
            break
    return out


class UpdateFlow:
    """`get_db()` gives THIS thread's own Database (workers call it on their own thread). Everything that touches the
    network or the disk runs on a thread of its own; the methods the page calls return at once."""

    def __init__(self, get_db, publish=None, quit_app=None, *, latest=None, downloader=None, run_installer=None,
                 fetch=None, sleep=time.sleep, mono=time.monotonic, folder=None):
        self._open_db = get_db
        self.publish = publish or (lambda event, payload=None, key=None: None)
        self.quit_app = quit_app          # closes the window and tray WITHOUT setting EXITED_KEY (agent.py)
        self.on_open = None               # check_soon of the UpdateWorker: the window was opened
        self.on_change = None             # poke the status worker: a check changed what the banner says
        self._latest = latest or updates.latest_release
        self._download = downloader or updates.download
        self._run = run_installer or updates.install
        self._fetch = fetch or _fetch_release
        self._sleep, self._mono, self._folder = sleep, mono, folder
        self._lock = threading.RLock()
        self._local = threading.local()
        self._threads: list[threading.Thread] = []
        self._notes: dict[str, list[str]] = {}
        self._seq = 0
        self._checking = False
        self._install_after = False
        self._last_push = 0.0
        self.outcome: dict | None = None  # the last check's outcome (kept for About, which may be opened later)
        self.state = "idle"               # idle | downloading | ready | installing | failed
        self._file_down = False           # failed AFTER the download: Try again runs the installer again
        self.version = ""
        self.done = self.total = 0
        self.error = ""
        self.path: Path | None = None

    # ------------------------------------------------------------------ small helpers
    def _db(self):
        db = getattr(self._local, "db", None)
        if db is None:
            db = self._local.db = self._open_db()
        return db

    def _release_db(self):
        """A worker thread is done: let go of its connection."""
        db = getattr(self._local, "db", None)
        self._local.db = None
        try:
            if db is not None:
                db.close()
        except Exception:
            pass

    def _spawn(self, target, *args):
        def run():
            try:
                target(*args)
            finally:
                self._release_db()
        thread = threading.Thread(target=run, name="lockdown-update", daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def join(self, timeout: float = 5.0):
        """Wait for the running threads (tests)."""
        for thread in list(self._threads):
            thread.join(timeout)

    def status(self) -> dict:
        return {"state": self.state, "version": self.version, "done": self.done, "total": self.total,
                "error": self.error, "file_down": self._file_down}

    def _push_progress(self):
        self.publish("download_progress", self.status())

    def opened(self):
        """The main window was opened: look for an update now if the last check is old enough (the worker decides)."""
        if self.on_open:
            self.on_open()

    # ------------------------------------------------------------------ checking
    def check(self) -> bool:
        """Ask GitHub now (About "Check now"). False if a check is already out. The outcome is pushed as `update`."""
        with self._lock:
            if self._checking:
                return False
            self._checking = True
        self._spawn(self._check_run)
        return True

    def _check_run(self):
        try:
            found = self._latest()
            db = self._db()
            from trusted_time import now_from_db
            now = now_from_db(db)
            if found:     # (kept as well: the banner and the tray say the same as the About page)
                updates.checked(db, now, found)
                if updates.can_install(found):
                    updates.said(db, found["version"])      # you have seen it: no notice about this one again
            self.outcome = outcome(found)
            self._seq += 1
            self.publish("update", {"seq": self._seq, "found": public(updates.banner(db, now)),
                                    "outcome": self.outcome})
            if self.on_change:
                self.on_change()
        except Exception as error:
            self.outcome = outcome(None)
            self._seq += 1
            self.publish("update", {"seq": self._seq, "found": None, "outcome": self.outcome})
            self._log(error)
        finally:
            self._checking = False

    def _log(self, error):
        try:
            from webapi import core
            core.log_error(f"update flow: {type(error).__name__}: {error}")
        except Exception:
            pass

    # ------------------------------------------------------------------ downloading and installing
    def _wanted(self) -> dict:
        """The release to install, from the record the updates module kept - checked again here."""
        found = updates.available(self._db())
        if not (found and updates.can_install(found) and asset_ok(found.get("asset"))):
            raise ValueError("There is no update to install right now.")
        if not SAFE_VERSION.fullmatch(str(found["version"])):
            raise ValueError("That version number can't be used for a file name.")
        return found

    def download(self, install_after: bool = False) -> dict:
        """Start downloading the waiting update (nothing happens if it is already coming or here). With
        `install_after` the installer runs as soon as it is down. Returns the state."""
        with self._lock:
            if self.state == "installing":
                return self.status()
            if self.state == "downloading":
                self._install_after = self._install_after or install_after
                return self.status()
            have = self.state in ("ready", "failed") and self._file_down and self.path and self.path.exists()
            if have:
                if install_after or self.state == "failed":
                    self._begin_install()
                return self.status()
            try:
                found = self._wanted()
            except ValueError as error:
                self._file_down = False
                self._set_failed(str(error))
                return self.status()
            self.version = str(found["version"])
            self.path = updates.download_path(self.version, self._folder)
            self.done, self.total, self.error = 0, int(found.get("size") or 0), ""
            self.state, self._file_down = "downloading", False
            self._install_after = install_after
            self._last_push = 0.0
        self._push_progress()
        self._spawn(self._download_run, found["asset"], self.path)
        return self.status()

    def install(self) -> dict:
        """Download if needed, then run the installer and quit. Safe to call twice."""
        return self.download(install_after=True)

    def _progress(self, done: int, total: int):
        """(worker thread, once per chunk) Only every PROGRESS_EVERY_SEC reaches the page."""
        self.done, self.total = done, total or self.total
        now = self._mono()
        if now - self._last_push >= PROGRESS_EVERY_SEC:
            self._last_push = now
            self._push_progress()

    def _download_run(self, url: str, path: Path):
        try:
            self._download(url, path, progress=self._progress)
        except Exception as error:
            try:
                Path(path).unlink()
            except OSError:
                pass
            self._file_down = False
            self._set_failed(f"The download didn't finish ({type(error).__name__} - no connection, or GitHub "
                             "didn't answer).")
            return
        with self._lock:
            self.state, self.done, self._file_down = "ready", self.total or self.done, True
            install = self._install_after
        self._push_progress()
        if install:
            self._install_run()

    def _begin_install(self):
        """(lock held) Run the installer that is already down."""
        self.state, self.error = "installing", ""
        self._push_progress()
        self._spawn(self._install_run, True)

    def _install_run(self, announced: bool = False):
        if not announced:
            with self._lock:
                self.state, self.error = "installing", ""
            self._push_progress()
        try:
            self._run(self.path)
        except Exception as error:   # it used to fail silently and sit at 100%
            self._set_failed(f"The installer didn't start ({error}).")
            return
        self._sleep(QUIT_DELAY_SEC)
        if self.quit_app:
            self.quit_app()

    def _set_failed(self, text: str):
        with self._lock:
            self.state, self.error = "failed", text
        self._push_progress()      # (file_down tells the page whether Try again downloads again or runs the file)

    def from_notice(self):
        """The update notice's Install button: start the download; the page opens the update dialog."""
        self.publish("update", {"seq": -1, "open_dialog": True})
        self.download()

    # ------------------------------------------------------------------ release notes (for the dialog)
    def notes(self, version: str) -> list[str]:
        """The release's changelog lines - one small request to OUR repository's release, once per version."""
        if version in self._notes:
            return self._notes[version]
        found = updates.available(self._db())
        if not (found and str(found["version"]) == str(version)):
            return []
        try:
            lines = notes_from(self._fetch(updates.API.format(repo=REPO)).get("body") or "")
        except Exception:
            return []                    # (not cached: try again next time)
        self._notes[version] = lines
        return lines


def _fetch_release(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": f"Lockdown/{VERSION}"})
    with urllib.request.urlopen(request, timeout=updates.TIMEOUT) as response:
        return json.loads(response.read())


def attach(hub, update_worker, publish, show=None, quit_app=None) -> UpdateFlow:
    """Wire the flow into the agent: the update notice's Install button (hub.install_update) opens the window on
    the update dialog, the window opening asks the worker for a check, a check pokes the status worker so the
    banner follows. `show()` brings the main window up; `quit_app()` closes window + tray without EXITED_KEY."""
    global current

    def poke():
        if hub.status_worker is not None:
            hub.status_worker.poke()
    flow = UpdateFlow(hub.open_db, publish, quit_app)
    flow.on_open = update_worker.check_soon
    flow.on_change = poke

    def install(found=None):
        if show:
            show()
        flow.from_notice()
    hub.install_update = install
    current = flow
    return flow
