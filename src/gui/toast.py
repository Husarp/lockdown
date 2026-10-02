"""Windows notifications (native toasts) with buttons - what Lockdown says while you are in a game, and by default.

Windows draws them, not Lockdown: no window of ours goes over a full-screen game (that is what made a game stutter)
and nothing takes the focus. Nothing here breaks through Windows' Do not disturb (no "urgent" toasts) - even a
Windows toast can make a game hitch, so while you play Windows may hold them back into the notification centre;
one sent while something is full-screen stays there a long while (HELD_EXPIRE_MIN). Their buttons call back into
the running tray agent (always running); the callback is handed to the Tk thread (`call_soon`), as WinRT calls it
on a thread of its own. Each notice leaves the notification centre by itself (`expire_min`) - no PowerShell
clean-up afterwards. Without windows-toasts (or its WinRT parts) the tray's balloon says the text instead, without
buttons."""
from datetime import datetime, timedelta

APP_ID = "com.husarp.lockdown"   # the identity main.py registers (name + icon): needed for the buttons to call back
EXPIRE_MIN = 10                  # a passing notice leaves the notification centre after this
HELD_EXPIRE_MIN = 120            # ... one sent over a full-screen app: still there when the game is over


class Toaster:
    def __init__(self, call_soon, fallback, on_click=None):
        """call_soon(fn): run fn on the Tk thread. fallback(text): the balloon. on_click: a click on the body of
        a notice (not on a button) - opens Lockdown."""
        self.call_soon, self.fallback, self.on_click = call_soon, fallback, on_click
        self._toaster = None      # created on first use; False = not possible here (then: the balloon)
        self._live = {}           # tag -> (toast, expires): kept referenced so their click handlers stay alive

    def _get(self):
        if self._toaster is None:
            try:
                from windows_toasts import InteractableWindowsToaster
                self._toaster = InteractableWindowsToaster("Lockdown", notifierAUMID=APP_ID)
            except Exception:
                self._toaster = False
        return self._toaster

    def works(self) -> bool:
        """Can Windows notifications (with buttons) be shown here? False: only the balloon."""
        return bool(self._get())

    def send(self, text: str, actions=(), scenario: str | None = None, expire_min: float = EXPIRE_MIN,
             title: str | None = None):
        """Show `text` (under a bold `title`, if given) with a button per (label, callback) in `actions`.
        scenario "reminder": it stays on screen until answered (needs a button) - only for when nothing is
        full-screen; None: an ordinary notice.
        Returns the notice (for remove), or None when the balloon had to say it."""
        toaster = self._get()
        if toaster:
            try:
                return self._send(toaster, [title, text] if title else [text], list(actions), scenario, expire_min)
            except Exception:
                self._toaster = False   # (WinRT refused: say it anyway - and from now on the balloon / pop-ups)
        self.fallback(f"{title}: {text}" if title else text)
        return None

    def _send(self, toaster, lines, actions, scenario, expire_min):
        from windows_toasts import Toast, ToastButton, ToastScenario
        now = datetime.now().astimezone()   # (aware: WinRT gets an unambiguous time)
        self._live = {k: v for k, v in self._live.items() if v[1] > now}
        calls = {f"lockdown:{i}": fn for i, (_label, fn) in enumerate(actions)}

        def activated(event):     # (a WinRT thread - never touch Tk here)
            fn = calls.get(getattr(event, "arguments", None), self.on_click)
            if fn:
                self.call_soon(fn)

        expires = now + timedelta(minutes=expire_min)
        reminder = scenario == "reminder" and actions   # (Windows ignores it without a button)
        toast = Toast(text_fields=lines, expiration_time=expires, on_activated=activated,
                      scenario=ToastScenario.Reminder if reminder else ToastScenario.Default,
                      actions=[ToastButton(label, arg) for arg, (label, _fn) in zip(calls, actions)])
        toaster.show_toast(toast)
        self._live[toast.tag] = (toast, expires)
        return toast

    def remove(self, toast):
        """Take a notice down (on screen and in the notification centre) - its question was answered elsewhere."""
        if toast is None or not self._toaster:
            return
        self._live.pop(toast.tag, None)
        try:
            self._toaster.remove_toast(toast)
        except Exception:
            pass
