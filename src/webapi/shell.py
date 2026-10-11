"""Api for the window's shell (BLUEPRINT 3.0): the top status line, sidebar footer, banners, save bar and the look
(theme / accent / size). Everything here is cheap: `get_status` reads a handful of settings and one COUNT, so it
stays far below the 50 ms budget even on a two-year database.

Classification: the look settings are cosmetic (ui). Starting the service, switching Lockdown back on and ending a
pause only make blocking stricter, so they are free (tighten) - nothing here can loosen a block.
"""
import re
import sys
import time
from datetime import datetime

import antibypass
import modes
import pause
import ui_theme
from rules import TIME_FMT

from . import core
from .core import ApiError

HEARTBEAT_KEY = "service_heartbeat"     # service.HEARTBEAT_KEY (not imported: service.py pulls in the whole enforcer)
SERVICE_TIMEOUT_SEC = 15                # no heartbeat for this long = the service is not running
AUTOSAVE_KEY = "ui.autosave"            # gui/draft.py AUTOSAVE_KEY; on by default
SCALES = ("auto", "100", "90", "80", "70")      # "Interface size" (percent of the full size)
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
MAX_CLIENT_ERROR = 600                  # characters of a page error that go to the log


def _iso(value):
    return value.isoformat(timespec="seconds") if value else None


def _service_running(db) -> bool:
    try:
        return time.time() - float(db.get_setting(HEARTBEAT_KEY, "0") or 0) < SERVICE_TIMEOUT_SEC
    except ValueError:
        return False


def _mode(state):
    """The active mode as the page needs it, or None."""
    if not state:
        return None
    phase = state["phase"]
    return {"id": state["mode"]["id"], "name": state["mode"]["name"], "phase": phase[0] if phase else None,
            "ends_at": _iso(state["until"]), "locked": state["locked"], "by_schedule": state["scheduled"]}


def _off_since(db):
    text = antibypass.off_since(db)
    if not text:
        return None
    try:
        return datetime.strptime(text, TIME_FMT).isoformat(timespec="seconds")
    except ValueError:
        return text


def _draft(ctx, db) -> dict:
    """Unsaved changes. The draft itself arrives with R18, which sets `ctx.draft_state` to a function returning
    (dirty, summary); until then nothing is ever dirty."""
    dirty, summary = False, ""
    state = getattr(ctx, "draft_state", None)
    if state is not None:
        dirty, summary = state()
    return {"dirty": bool(dirty), "autosave": db.get_setting(AUTOSAVE_KEY, "1") == "1",
            "change_summary": summary or ""}


def _status(ctx) -> dict:
    """The whole shell status (also what `status` pushes carry, as deltas)."""
    db, now = ctx.db, ctx.now()
    cfg = antibypass.settings(db)
    state = modes.active(db, now)
    paused = pause.state(db, now)
    off = _off_since(db)
    running = _service_running(db)
    if off:
        tray = "OFF - nothing is enforced"
    elif paused:
        tray = f"Blocking paused until {paused['until']:%H:%M}"
    else:
        tray = f"{db.item_count()} sites/apps blocked" + (f" · {state['mode']['name']} mode" if state else "")
    unlocked, waiting = antibypass.unlocked_until(cfg, now), antibypass.waiting_until(cfg, now)
    nxt = antibypass.next_hours(cfg, now) if cfg["hours"] else None
    return {
        "service_running": running,
        "off_since": off,
        "mode": _mode(state),
        "pause": {"started": _iso(paused["started"]), "until": _iso(paused["until"]), "silent": paused["silent"]}
                 if paused else None,
        "clock_now": _iso(now),
        "draft": _draft(ctx, db),
        "antibypass": {"status": antibypass.status(cfg, now), "unlocked_until": _iso(unlocked),
                       "waiting_until": _iso(waiting), "next_open": _iso(nxt)},
        "tray_text": tray,
    }


# ---------------------------------------------------------------- reading

@core.read
def get_status(ctx):
    """service_running, off_since, mode, pause, clock_now, draft, antibypass, tray_text (see _status()).
    The words are BLUEPRINT 3.0's; the `status` pushes (resident/snapshot.py) say running / off / mode.phase as an
    object instead - js/shell.js canon() turns this answer into the push words, so the two merge into one status."""
    return _status(ctx)


@core.read
def get_theme(ctx):
    """The saved look: theme (dark / amoled / light / system), accent (#RRGGBB), scale (auto / 100 / 90 / 80 / 70)."""
    db = ctx.db
    theme = db.get_setting(ui_theme.THEME_KEY, "") or ""
    if theme not in ui_theme.BACKGROUNDS and theme != "system":
        theme = {"light": "light", "system": "system"}.get(db.get_setting("ui.appearance", "") or "", "dark")
    accent = db.get_setting(ui_theme.ACCENT_KEY, "") or ""
    scale = db.get_setting(ui_theme.SIZE_KEY, "") or "auto"
    return {"theme": theme, "accent": accent.upper() if HEX_RE.fullmatch(accent) else ui_theme.ACCENTS["Orange"],
            "scale": scale if scale in SCALES else "auto", "accents": ui_theme.ACCENTS}


# ---------------------------------------------------------------- the look (cosmetic)

@core.ui
def set_appearance(ctx, theme):
    if theme not in ui_theme.THEMES.values():
        raise ApiError("Pick Dark, AMOLED, Light or System.")
    ctx.db.set_setting(ui_theme.THEME_KEY, theme)


@core.ui
def set_accent(ctx, color):
    if not isinstance(color, str) or not HEX_RE.fullmatch(color):
        raise ApiError("That isn't a colour (use #RRGGBB).")
    ctx.db.set_setting(ui_theme.ACCENT_KEY, color.upper())


@core.ui
def set_scale(ctx, value):
    text = str(value).lower() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
    if text not in SCALES:
        raise ApiError("Pick Auto, 100, 90, 80 or 70 percent.")
    ctx.db.set_setting(ui_theme.SIZE_KEY, text)


# ---------------------------------------------------------------- the window talking to us

@core.ui
def ui_ready(ctx):
    """The page has painted its shell and has its first data. (R03 / R09 read ctx.ui_ready_at.)"""
    ctx.ui_ready_at = time.time()


@core.ui
def log_client_error(ctx, msg):
    """An error in the page's own code goes to the Lockdown log. One line, cut short: the page is untrusted and
    must not be able to write fake log lines or fill the disk."""
    if not isinstance(msg, str):
        raise ApiError("Bad request: text expected.")
    text = " | ".join(part.strip() for part in msg.splitlines() if part.strip())
    text = "".join(c for c in text if c.isprintable())[:MAX_CLIENT_ERROR]
    if text:
        core.log_error(f"page error: {text}")


# ---------------------------------------------------------------- free actions (they only make blocking stricter)

def _run_elevated(program: str, params: str):
    """Windows' "run as administrator" (asks for the OK). Only the Windows build can do this."""
    if sys.platform != "win32":
        raise ApiError("Starting the service only works on Windows.")
    import ctypes
    ctypes.windll.shell32.ShellExecuteW(None, "runas", program, params, None, 0)


@core.tighten
def service_start(ctx):
    """(Re)start the enforcement service - the installed Windows service, or the scheduled task when run from
    source. A stuck copy is ended first (Windows ignores a start while one is still running)."""
    import os
    from paths import FROZEN, SERVICE_NAME, TASK_NAME
    if FROZEN:
        powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                  r"System32\WindowsPowerShell\v1.0\powershell.exe")
        _run_elevated(powershell, f"-NoProfile -Command Restart-Service -Name {SERVICE_NAME} -Force")
    else:
        _run_elevated("cmd.exe", f'/c schtasks /End /TN "{TASK_NAME}" & schtasks /Run /TN "{TASK_NAME}"')
    return {"starting": True}


@core.tighten
def antibypass_switch_on(ctx):
    """Turn Lockdown back on after it was switched off. Never needs the challenge (it only adds protection)."""
    antibypass.switch_on(ctx.db)
    return _status(ctx)


@core.tighten
def pause_resume(ctx):
    """End "Pause my blocks" early: blocking is back on at once. Always free."""
    pause.resume(ctx.db)
    return _status(ctx)
