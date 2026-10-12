"""Api for the About page and the update banner / dialog (BLUEPRINT 3.13).

Nothing here can loosen a block, so nothing needs the challenge: reads, and a few ungated actions (check for an
update, download / install it, the two update switches, open a folder). The page is untrusted, so every action takes
only what it needs and decides the rest itself:
- `open_path("data" | "app")` - only those two names; any other text (a path, "..") is refused.
- `open_url(...)` - takes no choice at all: it opens this repository's GitHub page (arguments are ignored).
- `update_install()` / `update_download()` - take nothing: the release, its address and the file name come from
  Python's own record of the newest release (see resident/update_flow.py), never from the page.
Checking, downloading and installing run on worker threads; the page gets `update` / `download_progress` pushes.
"""
import subprocess
import sys
import webbrowser

import paths
import updates
from resident import update_flow
from version import RELEASED, VERSION

from . import core
from .core import ApiError

FOLDERS = {"data": lambda: paths.DATA_DIR, "app": lambda: paths.APP_DIR}


def _flow(ctx) -> "update_flow.UpdateFlow":
    """The agent's shared flow (resident/update_flow.attach), else one made for this Context."""
    flow = getattr(ctx, "update_flow", None) or update_flow.current
    if flow is None:
        flow = ctx.update_flow = update_flow.UpdateFlow(lambda: ctx.db)
    return flow


def _explorer(path):
    """Show a folder in Explorer. Only Windows has one."""
    if sys.platform != "win32":
        raise ApiError("Opening a folder only works on Windows.")
    subprocess.Popen(["explorer", str(path)])


_opener = _explorer          # (the tests put their own here)
_browser = webbrowser.open


# ---------------------------------------------------------------- reading

@core.read
def about_get(ctx):
    """Everything the About page shows: version, release date, the update state, the two switches, the folders."""
    db, flow = ctx.db, _flow(ctx)
    repo = updates.repo_page()
    return {
        "version": VERSION, "released": RELEASED,
        "repo": repo,                                    # None: "No repository set yet..."
        "auto": updates.auto_on(db), "notify": updates.notify_on(db), "every_hours": updates.EVERY_HOURS,
        "found": update_flow.public(updates.available(db)) if repo else None,
        "outcome": flow.outcome, "download": flow.status(),
        "checking": flow.checking, "seq": flow.seq,      # (the page asks this when a check's `update` push never came)
        "paths": {"data": str(paths.DATA_DIR), "app": str(paths.APP_DIR), "log": str(paths.LOG_PATH)},
    }


@core.read
def update_state(ctx):
    """What the update banner needs: the release to offer (not while "Remind me later" runs), the running version and
    the download state."""
    flow, db = _flow(ctx), ctx.db
    found = updates.banner(db, ctx.now()) if updates.repo_page() else None
    return {"found": update_flow.public(found), "current": VERSION, "download": flow.status()}


@core.read
def update_notes(ctx, version):
    """The changelog lines of the waiting release (the dialog's "What's new"); [] if there are none to show."""
    if not isinstance(version, str):
        raise ApiError("Bad request: text expected.")
    return {"version": version, "items": _flow(ctx).notes(version)}


# ---------------------------------------------------------------- the update (ungated: it only moves forward)

@core.ui
def update_check(ctx):
    """Ask GitHub now. Returns at once; the outcome comes back as an `update` push."""
    if not updates.repo_page():
        raise ApiError("No repository set yet, so there is nowhere to check for updates.")
    return {"started": _flow(ctx).check()}


@core.ui
def update_download(ctx):
    """Start downloading the waiting update (the dialog's progress). Does not run the installer."""
    return _flow(ctx).download()


@core.ui
def update_install(ctx):
    """Download the waiting update if it is not down yet, run its installer and close Lockdown. Never sets the
    "deliberately quit" flag, so Lockdown comes back by itself after the installer."""
    return _flow(ctx).install()


@core.ui
def update_set_prefs(ctx, auto, notify):
    """"Check automatically" and "Tell me when a new version is found"."""
    if not isinstance(auto, bool) or not isinstance(notify, bool):
        raise ApiError("Bad request: on or off expected.")
    ctx.db.set_setting(updates.AUTO_KEY, "1" if auto else "0")
    ctx.db.set_setting(updates.NOTIFY_KEY, "1" if notify else "0")
    return {"auto": auto, "notify": notify}


@core.ui
def update_opened(ctx):
    """The window was opened: look for an update now if the last check is old (the worker keeps its own limits)."""
    _flow(ctx).opened()


# ---------------------------------------------------------------- opening things (fixed targets only)

@core.ui
def open_path(ctx, which):
    """Open the data folder or the program folder in Explorer. Nothing else can be named."""
    if not isinstance(which, str) or which not in FOLDERS:
        raise ApiError("That folder can't be opened from here.")
    _opener(FOLDERS[which]())


@core.ui
def open_url(ctx, *ignored):
    """Open Lockdown's GitHub page - the release that is waiting, else the releases list. Arguments are ignored: the
    page can't choose an address."""
    url = _repo_url(updates.available(ctx.db))
    if not url:
        raise ApiError("No repository set yet.")
    _browser(url)


def _repo_url(found):
    page = updates.repo_page()
    if not page:
        return None
    url = updates.releases_page(found)
    return url if isinstance(url, str) and (url == page or url.startswith(page + "/")) else page + "/releases"
