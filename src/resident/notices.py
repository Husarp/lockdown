"""Where a notice goes: a Windows notification, the corner window's card, or both (Settings > Notifications "Show as").

`Notices.show` is the sink behind `hub.notify` (resident/workers.py Notifier): the mute rule (a muting mode, a silent
pause) is already applied there, so by the time `show` runs the notice is wanted. What is decided here is the WAY:

- A full-screen app in front: never a window of ours (a topmost popup made games stutter) - a Windows notification,
  whatever the setting; it may wait in the notification centre (toast.HELD_EXPIRE_MIN).
- Otherwise by the setting `notify.format`: "toast" = Windows only, "inapp" = the corner card only, "both".
- A notice with buttons never goes only to the balloon: if Windows notifications can't be had (the tray balloon has no
  buttons) it becomes the corner card, so its buttons are kept.
- A sticky notice shown by Windows is a "reminder" (stays on screen until answered) - only when nothing is full-screen.

Taking a Windows notification out of the notification centre can be slow: it runs in a thread of its own."""
import threading
import time

import alerts
from gui import toast as toasts

FORMAT_KEY = "notify.format"
FORMAT_TTL_SEC = 3          # the setting is re-read at most this often


def route(fmt: str, fullscreen: bool, has_buttons: bool, toasts_work: bool) -> tuple[bool, bool]:
    """(send a Windows notification, show the corner card)."""
    card = not fullscreen and (fmt in ("inapp", "both") or (has_buttons and not toasts_work))
    windows = fmt == "both" or not card
    return windows, card


def _spawn(fn):
    threading.Thread(target=fn, name="lockdown-toast", daemon=True).start()


class Notices:
    """hub.notify.show = Notices(hub, corner, toaster).show"""

    def __init__(self, hub, corner, toaster, read_format=None, spawn=None, clock=time.monotonic):
        self.hub, self.corner, self.toaster = hub, corner, toaster
        self.spawn = spawn or _spawn
        self._read_format = read_format or self._read_setting
        self._clock = clock
        self._fmt = (None, -1e9)      # (value, read at)
        self._db = None
        self._lock = threading.Lock()
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

    def show(self, message, action=None, actions=None, sticky=False):
        buttons = list(actions or ([action] if action else []))
        full = self._fullscreen()
        windows, card = route(self.fmt(), full, bool(buttons), self.toaster.works())
        self.last_toast = None
        if windows:
            self.last_toast = self.toaster.send(
                message, buttons, scenario="reminder" if sticky and not full else None,
                expire_min=toasts.HELD_EXPIRE_MIN if full else toasts.EXPIRE_MIN)
        if card:
            self.corner.notice(message, buttons, sticky=sticky)

    def clear(self, toast):
        """Take a Windows notification down (on screen and in the notification centre), off the calling thread."""
        if toast is not None:
            self.spawn(lambda: self.toaster.remove(toast))
