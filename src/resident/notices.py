"""Where a notice goes: a Windows notification, the corner window's card, or both (Settings > Notifications "Show as").

`Notices.show` is the sink behind `hub.notify` (resident/workers.py Notifier): the mute rule (a muting mode, a silent
pause) is already applied there, so by the time `show` runs the notice is wanted. What is decided here is the WAY:

- A full-screen app in front: never a window of ours (a topmost popup made games stutter) - a Windows notification,
  whatever the setting; it may wait in the notification centre (toast.HELD_EXPIRE_MIN).
- Otherwise by the setting `notify.format`: "toast" = Windows only, "inapp" = the corner card only, "both".
- A notice with buttons never goes only to the balloon: if Windows notifications can't be had (the tray balloon has no
  buttons) it becomes the corner card, so its buttons are kept.
- A sticky notice shown by Windows is a "reminder" (stays on screen until answered) - only when nothing is full-screen.
- The warning for the app in front (`front=True`, settings notify.front.*): ALWAYS the corner card, full-screen app or
  not, Windows' Do Not Disturb / game mode or not - the card is ours, Windows cannot hold it. It never takes the focus
  (the window is topmost and no-activate), so a borderless game keeps running and keeps its focus; nothing can draw over
  an EXCLUSIVE full-screen game, there only the sound (`sound=True`: a short system sound, played without waiting) is
  heard. While the corner's page is not ready it takes the ordinary road above.

Taking a Windows notification out of the notification centre can be slow: it runs in a thread of its own."""
import threading
import time

import alerts
from gui import toast as toasts
from notification_batch import BlockingMessage, NotificationBatch

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
    """A short system sound, without waiting for it. Windows only; never raises."""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


class Notices:
    """hub.notify.show = Notices(hub, corner, toaster).show"""

    def __init__(self, hub, corner, toaster, read_format=None, spawn=None, clock=time.monotonic, beep=None, schedule=None):
        self.hub, self.corner, self.toaster = hub, corner, toaster
        self.spawn = spawn or _spawn
        self._beep = beep or play_sound
        self._read_format = read_format or self._read_setting
        self._clock = clock
        self._fmt = (None, -1e9)      # (value, read at)
        self._db = None
        self._lock = threading.Lock()
        self._block_toast = None
        self._batch = NotificationBatch()
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

    def _flush_blocks(self):
        # Serialize flushes as well as offers: a slow WinRT call must not replace a newer burst.
        with self._batch_lock:
            pending, self._pending = self._pending, []
            self._batch_timer = None
            pending = [(m, f, s) for m, f, s in pending if not self.hub.notify.muted(m.force or f)]
            text = self._batch.merge([m for m, _, _ in pending], self._clock())
            if text:
                self._show(text, front=any(f for _, f, _ in pending),
                           sound=any(s for _, _, s in pending), replace_key="blocks")

    def show(self, message, action=None, actions=None, sticky=False, front=False, sound=False):
        if isinstance(message, BlockingMessage) and not (action or actions or sticky):
            with self._batch_lock:
                self._pending.append((message, front, sound))
                if self._batch_timer is None:
                    self._batch_timer = self._schedule(0.75, self._flush_blocks)
            return
        self._show(message, action, actions, sticky, front, sound)

    def _show(self, message, action=None, actions=None, sticky=False, front=False, sound=False, replace_key=None):
        buttons = list(actions or ([action] if action else []))
        if replace_key and front and getattr(self.corner, "page_ok", True) and self._block_toast is not None:
            self.toaster.remove(self._block_toast)
            self._block_toast = None
        if front and self._front(message, buttons, sticky, sound, replace_key):
            return
        full = self._fullscreen()
        windows, card = route(self.fmt(), full, bool(buttons), self.toaster.works())
        if card and not getattr(self.corner, "page_ok", True):     # (the corner's page has not said it works yet)
            windows, card = True, False
        self.last_toast = None
        if replace_key and not card:
            self.corner.remove("notice:" + replace_key)
        if windows:
            self.last_toast = self.toaster.send(
                message, buttons, scenario="reminder" if sticky and not full else None,
                expire_min=toasts.HELD_EXPIRE_MIN if full     # (missed over a game: still there afterwards)
                else toasts.EXPIRE_MIN if buttons or sticky or replace_key else toasts.PASSING_EXPIRE_MIN,
                **({"replace_key": replace_key} if replace_key else {}))
        if replace_key:
            self._block_toast = self.last_toast
        if card:
            self.corner.notice(message, buttons, sticky=sticky,
                               **({"key": "notice:" + replace_key} if replace_key else {}))

    def clear(self, toast):
        """Take a Windows notification down (on screen and in the notification centre), off the calling thread."""
        if toast is not None:
            self.spawn(lambda: self.toaster.remove(toast))
