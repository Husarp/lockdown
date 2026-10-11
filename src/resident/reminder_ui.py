"""The ReminderUI the agent gives the reminder engine (workers.ReminderWorker): popups go to the corner window, the
bedtime / break cover to the overlay (R08), a popup held back by a full-screen app becomes a Windows notification.

The engine calls these on the ReminderWorker's thread, so everything here returns quickly. Answers come back through
`Corner.answer` (a card's button) -> `worker.answer(key, action)`, and through a Windows notification's button ->
`worker.held_answer(key, action)`; both run on the worker's thread, because the engine is not thread-safe."""
import emergency
import reminders
from gui import toast as toasts
from resident import tray_actions

MAX_TOAST_BUTTONS = 5      # Windows shows at most this many buttons on a notification
BREAK_COVER_KEY = "break-run"     # the cover shown while a strict break runs (R08)
BREAK_COVER_TEXT = "Look away from the screen, stand up, drink some water. Your work will be right here."


class CornerReminderUI:
    """Implements workers.ReminderUI.

    overlay_handler: the full-screen cover (R08): an object with `show(key, title, text, until, buttons)` and
    `close(key)`; until it is set (R08) no cover is shown.
    notices: resident.notices.Notices (used to take a held popup's Windows notification down again)."""

    def __init__(self, hub, corner, toaster=None, notices=None, overlay_handler=None):
        self.hub, self.corner, self.toaster, self.notices = hub, corner, toaster, notices
        self.overlay_handler = overlay_handler
        self.engine = None          # set by the ReminderWorker before the first tick
        self.worker = None
        self.held_notices: dict = {}      # key -> the Windows notification standing in for a held popup
        self.overlay_keys: set = set()
        corner.popup_sink = self._answer

    # ------------------------------------------------------------------ the engine's calls
    def popup(self, key, title, text, buttons):
        self.close(key)
        self.corner.popup(key, title, text, buttons)

    def overlay(self, key, title, text, until, buttons):
        self.close(key)
        self.overlay_keys.add(key)
        if self.overlay_handler is not None:
            self.overlay_handler.show(key, title, text, until, buttons)

    def held(self, key, title, text, buttons):
        """A popup held back while a full-screen app is in front: a Windows notification with the same buttons (Windows
        draws it, so nothing of ours goes over the game; it may wait in the notification centre). Nothing while a mode
        that mutes is on: the popup still comes once the mode ends."""
        self.close(key)
        snap = self.hub.snapshot
        if snap is not None and snap.mode and snap.mode["mute"]:
            return
        if self.toaster is None:
            return
        self.held_notices[key] = self.toaster.send(
            text, [("Dismiss" if action == "dismiss" else label, lambda a=action: self._held_answer(key, a))
                   for label, action in buttons][:MAX_TOAST_BUTTONS], title=title,
            expire_min=toasts.HELD_EXPIRE_MIN)

    def close(self, key):
        self.corner.remove(key)
        if key in self.overlay_keys:
            self.overlay_keys.discard(key)
            if self.overlay_handler is not None:
                self.overlay_handler.close(key)
        notice = self.held_notices.pop(key, None)
        if notice is not None:        # shown as a popup now, or no longer due: its notification goes
            self._clear(notice)

    def toast(self, text):
        self.hub.notify(text)

    def break_start(self, until):
        """A strict break: every window down now and the "Break time" cover up; MinimizeWorker keeps the windows down
        until `until` and says "Break in progress" every 12 s. Dismiss takes the cover down (the break goes on); it
        comes back only when a window you brought to the front is sent down again (break_blocked)."""
        self.hub.win.minimize_all()
        now = getattr(self.engine, "now", None)
        left = max(1, round((until - now).total_seconds() / 60)) if now else 1
        self.hub.notify(f"Break started - your windows are minimised for {left} min. Step away from the screen.",
                        force=True)
        self._break_cover(until)

    def break_end(self):
        self.close(BREAK_COVER_KEY)
        self.hub.notify("Break over - welcome back.", force=True)

    def break_blocked(self):
        """(MinimizeWorker's thread) You tried to open something during the strict break and it was sent back down:
        the cover comes up again, unless it is up already. It runs on the reminder worker's thread, which owns the
        engine and its database."""
        if self.worker is not None and self.overlay_handler is not None:
            self.worker.submit(self._reshow_break_cover)

    def _reshow_break_cover(self):
        engine = self.engine
        until, now = getattr(engine, "break_until", None), getattr(engine, "now", None)
        if not until or getattr(engine, "paused", None) or (now and now >= until) or self._cover_up():
            return          # (no break any more, an emergency paused it, or the cover is up: nothing to do, no flicker)
        self._break_cover(until)

    def _cover_up(self):
        key = getattr(self.overlay_handler, "key", None)
        return callable(key) and key() == BREAK_COVER_KEY

    def _break_cover(self, until):
        """The strict break's full-screen "Break time" (R08) until it ends - not while Do Not Disturb or a muting mode
        holds the alerts (the windows are minimised all the same). Buttons: Emergency while there are uses left (the
        way out that pauses the break alerts, so nobody is shut out of the PC for the whole break) and Dismiss (free:
        the cover goes, the break goes on)."""
        if self.overlay_handler is None or getattr(self.engine, "quiet", False):
            return
        buttons = []
        if self.worker is not None and getattr(self.engine, "now", None):
            left = emergency.available(self.worker.db, self.engine.now)
            if left:
                buttons.append((f"Emergency ({left} left)", "emergency"))
        buttons.append(("Dismiss", "dismiss"))
        self.overlay(BREAK_COVER_KEY, "Break time", BREAK_COVER_TEXT, until, buttons)

    def start_mode(self, mode_id, until):
        """The bedtime's "start a mode": unless another mode is locked on (that would need the challenge)."""
        db = self.worker.db if self.worker is not None else self.hub.open_db()
        tray_actions.start_mode(self.hub, db, mode_id, until)

    # ------------------------------------------------------------------ answers
    def _answer(self, key, action):
        """(Corner's popup_sink) a popup's button, after its card is gone: the engine hears it on its worker."""
        self.worker.answer(key, action)

    def _held_answer(self, key, action):
        """A button on the Windows notification of a held popup."""
        self.held_notices.pop(key, None)
        self.worker.held_answer(key, action)

    def _clear(self, notice):
        if self.notices is not None:
            self.notices.clear(notice)
        elif self.toaster is not None:
            self.toaster.remove(notice)


CORNER_PUSH_SEC = 0.03      # the corner window's pushes go out this soon (a card is up within a frame or two)


def attach(win, hub, ctx, show_main, log=None):
    """Wire the corner window into the agent: its Publisher, the cards (`ctx.corner`, for the corner_* Api), the notice
    sink (`hub.notify.show`), `hub.open_page`, and the tray's handlers. Returns (reminder_ui, tray_actions, toaster,
    publisher): pass reminder_ui to Workers(reminder_ui=...), the actions to the Tray, set `toaster.fallback` to the
    tray's balloon once the tray exists, and `publisher.stop()` when the agent ends."""
    import threading

    from gui.toast import Toaster
    from resident import windows
    from resident.corner import Corner, CornerHost
    from resident.notices import Notices
    from resident.publisher import Publisher

    publisher = Publisher(win.corner, interval=CORNER_PUSH_SEC, visible=True, log=log)   # (always shown: the page is
    corner = Corner(publisher, CornerHost(win.corner), log=log)                          #  loaded even while it is hidden)
    toaster = Toaster(lambda fn: threading.Thread(target=fn, name="lockdown-toast", daemon=True).start(),
                      lambda text: None, on_click=show_main)
    notices = Notices(hub, corner, toaster)
    hub.notify.show = notices.show
    ui = CornerReminderUI(hub, corner, toaster, notices)
    ctx.corner = corner
    ctx.notices = notices         # R27: Notifications > "Show as" tells it to read the setting again (notices.refresh())
    actions = tray_actions.TrayActions(hub, ctx.guard, show_main, lambda event, payload=None: win.publisher.publish(event, payload))
    hub.open_page = actions.open_page
    actions.open_challenge = lambda data: windows.open_challenge(win, data)   # (on the overlay while it is up)
    return ui, actions, toaster, publisher
