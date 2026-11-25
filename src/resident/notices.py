"""Routes normal notices and delivers advance warnings through the resident window."""
import ctypes
import os
import sys
import threading
import time

import alerts
from gui import toast as toasts
from notification_batch import AdvanceWarning, BlockingMessage, NotificationBatch
from resident.corner import GWL_EXSTYLE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW

FORMAT_KEY = "notify.format"
FORMAT_TTL_SEC = 3          # the setting is re-read at most this often


def route(fmt: str, fullscreen: bool, has_buttons: bool, toasts_work: bool) -> tuple[bool, bool]:
    """(send a Windows notification, show the corner card)."""
    card = not fullscreen and (fmt in ("inapp", "both") or (has_buttons and not toasts_work))
    windows = fmt == "both" or not card
    return windows, card


def _spawn(fn):
    threading.Thread(target=fn, name="lockdown-toast", daemon=True).start()


def play_sound():
    """Ask Windows to play the configured system warning sound asynchronously."""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        return True
    except Exception:
        return False


def flash_taskbar(hwnd, *, user32=None, pid=None):
    """Flash the visible main window in this process, including when minimized."""
    from ctypes import wintypes

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]

    if not hwnd:
        return False
    if user32 is None:
        if sys.platform != "win32":
            return False
        user32 = ctypes.WinDLL("user32")
    pid = os.getpid() if pid is None else pid
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    if owner.value != pid or not user32.IsWindowVisible(hwnd):
        return False
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if style & WS_EX_TOOLWINDOW or (not (style & WS_EX_APPWINDOW) and user32.GetWindow(hwnd, 4)):  # GW_OWNER
        return False
    # FLASHW_TRAY, three flashes at the system cadence. The return is the previous caption state.
    user32.FlashWindowEx(ctypes.byref(FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 2, 3, 0)))
    return True


class Notices:
    """hub.notify.show = Notices(hub, corner, toaster).show"""

    def __init__(self, hub, corner, toaster, read_format=None, spawn=None, clock=time.monotonic, beep=None,
                 schedule=None, flash=None, pulse=None):
        self.hub, self.corner, self.toaster = hub, corner, toaster
        self.spawn = spawn or _spawn
        self._beep = beep or play_sound
        self._flash = flash or (lambda: False)
        self._pulse = pulse or (lambda: False)
        self._last_warning_log = float("-inf")
        self._last_warning_state = None
        self._read_format = read_format or self._read_setting
        self._clock = clock
        self._fmt = (None, -1e9)      # (value, read at)
        self._db = None
        self._lock = threading.Lock()
        self._block_toast = None
        self._warning_toast = None
        self._batch = NotificationBatch()
        self._warnings = NotificationBatch()
        self._pending = []
        self._batch_lock = threading.Lock()
        self._batch_timer = None
        self._schedule = schedule or self._timer
        self.last_toast = None        # the Windows notification of the latest notice (to take down again)

    # ------------------------------------------------------------------ the setting
    def _read_setting(self) -> str:
        with self._lock:
            if self._db is None:
                self._db = self.hub.open_db()
            return alerts.get(self._db, FORMAT_KEY)

    def fmt(self) -> str:
        value, at = self._fmt
        now = self._clock()
        if value is None or now - at > FORMAT_TTL_SEC:
            try:
                value = self._read_format()
            except Exception:
                value = value or "toast"
            self._fmt = (value, now)
        return value

    def refresh(self):
        """The Notifications page saved a new "Show as": use it from the next notice on."""
        self._fmt = (None, -1e9)

    # ------------------------------------------------------------------ showing
    def _fullscreen(self) -> bool:
        try:
            return bool(self.hub.win.is_fullscreen())
        except Exception:
            return False

    def _front(self, message, buttons, sticky, sound, replace_key=None) -> bool:
        """The warning for the app in front: the corner card (our own window - over a game, through Do Not Disturb, and
        no Windows notification besides), then the sound. False when the corner's page is not ready: the caller then
        takes the ordinary road (the sound has been played all the same)."""
        on_card = getattr(self.corner, "page_ok", True)
        if on_card:
            self.last_toast = None
            self.corner.notice(message, buttons, sticky=sticky,
                               **({"key": "notice:" + replace_key} if replace_key else {}))
        if sound:
            self._beep()
        return on_card

    @staticmethod
    def _timer(delay, fn):
        timer = threading.Timer(delay, fn)
        timer.daemon = True
        timer.start()
        return timer

    @staticmethod
    def _attempt(fn, success, failed):
        try:
            return failed if fn() is False else success
        except Exception as error:
            return f"{failed}({type(error).__name__})"

    def _game_warning(self, text, sound, ready):
        audible = self._attempt(self._beep, "submitted", "failed") if sound else "off"
        flash = self._attempt(self._flash, "hwnd-submitted", "no-taskbar-target")
        if flash != "hwnd-submitted":
            pulse = self._attempt(self._pulse, "tray-submitted", "tray-unavailable")
            flash = f"{flash}/{pulse}"
        card = self._attempt(lambda: self.corner.warning(text), "submitted", "failed") if ready else "not-ready"
        return card, audible, flash

    def _warning_log(self, pending, now, state):
        fullscreen, dnd, mute, silent, ready, outcome = state
        warnings = [message for message, _, _ in pending]
        lead, steps, repeat = warnings[0].timing
        # Route/outcome transitions are reported immediately. Steady-state repeats are summarized at most every 30 s.
        key = (state, lead, steps, repeat)
        if key == self._last_warning_state and now - self._last_warning_log < 30:
            return
        self._last_warning_state, self._last_warning_log = key, now
        sources = sorted({f"{m.kind}/{'+'.join(m.triggers)}/in-use={m.in_use}" for m in warnings})
        self.hub.log_sink(
            f"Advance warning batch={len(warnings)} lead_sec={lead} steps_sec={steps} repeat_sec={repeat} "
            f"source={','.join(sources[:8])} fullscreen={fullscreen} dnd={dnd} mode_mute={mute} "
            f"silent={silent} card_ready={ready} card={outcome[0]} sound={outcome[1]} flash={outcome[2]}")

    def _flush_warnings(self, pending, now):
        if not pending:
            return
        accepted = [(m, front, sound) for m, front, sound in pending if not self.hub.notify.muted(m.force or front)]
        text = self._warnings.merge([m for m, _, _ in accepted], now, keep_burst=False)
        ready = getattr(self.corner, "page_ok", True)
        full = self._fullscreen()
        try:
            dnd = self.hub.notify.dnd()
        except Exception:
            dnd = None
        snap = self.hub.snapshot
        mute = bool(snap and snap.mode and snap.mode["mute"])
        silent = bool(snap and snap.pause and snap.pause["silent"])
        if text is None:
            if accepted:
                return   # duplicate occurrence
            outcome = ("muted", "skipped", "skipped")
        elif any(front for _, front, _ in accepted):
            outcome = self._game_warning(text, any(sound for _, _, sound in accepted), ready)
            self._warning_log(pending, now, (full, dnd, mute, silent, ready, outcome))
            if outcome[0] != "submitted":
                self._show(text, replace_key="warnings", allow_card=False)
            elif self._warning_toast is not None:
                self.toaster.remove(self._warning_toast)
                self._warning_toast = None
            return
        else:
            self._show(text, replace_key="warnings")
            outcome = ("plain-route", "skipped", "skipped")
        self._warning_log(pending, now, (full, dnd, mute, silent, ready, outcome))

    def _flush_blocks(self):
        # Serialize flushes as well as offers: a slow WinRT call must not replace a newer burst.
        with self._batch_lock:
            pending, self._pending = self._pending, []
            self._batch_timer = None
            now = self._clock()
            warnings = [(m, f, s) for m, f, s in pending if isinstance(m, AdvanceWarning)]
            ordinary = [(m, f, s) for m, f, s in pending
                        if not isinstance(m, AdvanceWarning) and not self.hub.notify.muted(m.force or f)]
            self._flush_warnings(warnings, now)
            text = self._batch.merge([m for m, _, _ in ordinary], now)
            if text:
                self._show(text, front=any(f for _, f, _ in ordinary),
                           sound=any(s for _, _, s in ordinary), replace_key="blocks")

    def show(self, message, action=None, actions=None, sticky=False, front=False, sound=False):
        if isinstance(message, BlockingMessage) and not (action or actions or sticky):
            with self._batch_lock:
                self._pending.append((message, front, sound))
                if self._batch_timer is None:
                    self._batch_timer = self._schedule(0.75, self._flush_blocks)
            return
        self._show(message, action, actions, sticky, front, sound)

    def _show(self, message, action=None, actions=None, sticky=False, front=False, sound=False, replace_key=None,
              allow_card=True):
        buttons = list(actions or ([action] if action else []))
        if replace_key == "blocks" and front and getattr(self.corner, "page_ok", True) and self._block_toast is not None:
            self.toaster.remove(self._block_toast)
            self._block_toast = None
        if front and self._front(message, buttons, sticky, sound, replace_key):
            return
        full = self._fullscreen()
        windows, card = route(self.fmt(), full, bool(buttons), self.toaster.works())
        if card and (not allow_card or not getattr(self.corner, "page_ok", True)):     # (the corner's page has not said it works yet)
            windows, card = True, False
        self.last_toast = None
        if replace_key and not card:
            self.corner.remove("notice:" + replace_key)
        if windows:
            if replace_key == "warnings" and self._warning_toast is not None:
                # A configured repeat should request a new alert even inside the toast replacement window.
                self.toaster.remove(self._warning_toast)
            self.last_toast = self.toaster.send(
                message, buttons, scenario="reminder" if sticky and not full else None,
                expire_min=toasts.HELD_EXPIRE_MIN if full     # (missed over a game: still there afterwards)
                else toasts.EXPIRE_MIN if buttons or sticky or replace_key else toasts.PASSING_EXPIRE_MIN,
                **({"replace_key": replace_key} if replace_key else {}))
        if replace_key == "blocks":
            self._block_toast = self.last_toast
        elif replace_key == "warnings":
            self._warning_toast = self.last_toast
        if card:
            self.corner.notice(message, buttons, sticky=sticky,
                               **({"key": "notice:" + replace_key} if replace_key else {}))

    def clear(self, toast):
        """Take a Windows notification down (on screen and in the notification centre), off the calling thread."""
        if toast is not None:
            self.spawn(lambda: self.toaster.remove(toast))
