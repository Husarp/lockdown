"""The full-screen cover (R08): the bedtime screen and a running strict break, solid colour, on every monitor.

`Overlay` is plain Python and holds the truth: what is on the cover (title, text, buttons, the Dismiss wait, a
countdown), and what each answer does. The page (ui/overlay.html + js/overlay.js) only draws it and ticks the numbers
locally from the "ms left" values it is given; the Api (webapi/overlay.py) hands it the page's requests. The reminder
engine reaches it through the ReminderUI (`show(key, title, text, until, buttons)` / `close(key)`).

Rules kept here, never in the page:
- Dismiss waits (reminders.dismiss_wait_now seconds: the own wait of the bedtime step active then, or the general one;
  from the moment the screen comes up, afresh each time): an early Dismiss is refused. Backing out of Disable alerts /
  Emergency while the wait runs is no Dismiss either - the screen stays and its wait starts over.
- The screen is taken down FIRST and only then does the engine hear the answer (as the corner window does), so a
  challenge or question that follows can never sit behind it.
- Only the buttons the engine gave can be used; an answer works once (a second click finds the screen gone).
- "Challenge before Dismiss" (reminders.sleep `challenge`): the list is fixed WHEN THE SCREEN COMES UP (`challenge_for`,
  read next to the wait: the own list of the bedtime step active then, else the general one), so a screen that stays up
  across the next step's time keeps asking its own. After the wait, Dismiss does not dismiss: `dismiss()` hands the list
  to the Api, which puts it behind the guard's plain challenge, and only a PASSED challenge dismisses (`dismiss_passed`,
  once). While the screen has a challenge, Cancel (backing out of Disable alerts' choice / the emergency list) is never
  a Dismiss either - it goes back to the screen. Disable alerts stays the Anti-Bypass challenge; Emergency is free.

The window is shown only once its page has said it is ready (`page_loaded`, from the page's `page_ready` call after it
drew what is up): a cover shown before is a solid dark screen, always on top, Alt+F4 ignored - a black screen with no
way out when the page never comes (0.85.1 at bedtime). A screen that is due while the page is not ready waits for it;
if it is not ready within PAGE_READY_SEC the owner is told another way (`fallback`: a Windows notification for the
bedtime screen; a strict break keeps its windows minimised all the same) and the reason goes to the log.

`OverlayHost` is the Win32 / pywebview glue (the window on the primary monitor, a plain cover of the same colour on
every other one, always on top). It is kept thin; the maths is in `plan_rects`.

"Block clicks behind the bedtime screen" (reminders.sleep `block_behind`, read when a screen comes up; it covers a strict
break too - one cover): without it the cover is put on top once, and only put back when it is moved, so a window that
makes itself topmost later (the taskbar, another app's always-on-top window) can sit over it and take the clicks, and the
keyboard stays with the app in front when Windows refuses to hand it over (a tray process may not take the foreground;
the covers on the other monitors never take it). With it, every BLOCK_GUARD_SEC the cover and its covers go back on top
of every other always-on-top window and the keyboard is taken back from whatever has it. No keyboard hook: Ctrl+Alt+Del,
Win+L and the cover's own buttons (Dismiss, Disable alerts, Emergency) all keep working.

The pointer is never touched (no SetCursorPos / ClipCursor / mouse_event / SendInput here, nor in 0.92.0). Putting a
window "back on top" only changes its place in the Z-order (SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE); it is only moved
and resized when it really is somewhere else - not where it was put (`misplaced`), and not where Windows put it the last
time it was asked for that place (a pixel off through DPI rounding is not fought every tick) - and the keyboard is only
taken back when a window that is not the cover's (nor one it owns) has it, or nothing has it. What 0.92.0 did instead,
and the suspected cause of the pointer jumping (not proven; only a real PC can show it): with the switch on, every 0.5 s
it passed the screen window and every cover their full position and size again (SetWindowPos with SWP_SHOWWINDOW)
whether anything had moved or not; and, switch on or off, it put a window back at every check (every 2 s, 0.5 s with the
switch on) when Windows had left it a pixel off the wanted rectangle, which display scaling does.

Every cover window is a tool window (WS_EX_TOOLWINDOW, never WS_EX_APPWINDOW - which WinForms puts on every form and which
wins over the tool-window bit): no taskbar button, not in Alt+Tab. It is set while the window is still hidden (the
taskbar only looks when a window comes up), and the guard puts it back if anything resets it."""
import sys
import threading
import time
from datetime import datetime

import challenges

# The page background (css --bg in tokens.css) per theme: the window and the covers on other monitors are painted with
# it, so the cover matches the page exactly. js/theme.js resolves "system" the same way (prefers-color-scheme).
BACKGROUNDS = {"dark": "#191817", "amoled": "#000000", "light": "#F3F2F1"}
DEFAULT_RECT = (0, 0, 1920, 1080)      # when Windows can't be asked: a plain 1080p screen
GUARD_SEC = 2.0                        # while the cover is up the monitors are re-read this often
BLOCK_GUARD_SEC = 0.5                  # ... and this often when it blocks the clicks behind it (back on top, the keyboard)
BREAK_FAILSAFE_SEC = 20                # a break cover leaves this long after its end even if the engine never says
SNOOZE_MIN = 15                        # the one snooze Disable alerts offers
MAX_BUTTONS = 4
KINDS = {"sleep": "sleep", "break-run": "break"}       # engine key -> what the page shows
EMERGENCY_KEY = "sleep"                # the engine hears every emergency under this key (it pauses bedtime AND break)
TITLE = "Lockdown overlay"
PAGE_READY_SEC = 10                    # a screen due before the page is ready waits this long, then the fallback


def overlay_background(theme: str, system_is_light: bool = False) -> str:
    """The cover colour for a saved theme name ("dark", "amoled", "light" or "system")."""
    if theme == "system":
        theme = "light" if system_is_light else "dark"
    return BACKGROUNDS.get(theme, BACKGROUNDS["dark"])


def saved_background() -> str:
    """The cover colour for the saved theme (no Database object: one read-only query, like the window background)."""
    import ui_theme
    return overlay_background(ui_theme.saved_theme(), ui_theme.system_is_light())


def left_ms(until, now) -> int | None:
    """Milliseconds from `now` to `until` (>= 0), or None when there is no end."""
    if until is None:
        return None
    return max(0, round((until - now).total_seconds() * 1000))


def clean_buttons(buttons) -> list[dict]:
    """The buttons as the page draws them: {label, action}; the engine's order, at most MAX_BUTTONS."""
    return [{"label": str(label), "action": str(action)} for label, action in list(buttons)[:MAX_BUTTONS]]


def bedtime_challenge(sleep: dict, now: datetime) -> dict | None:
    """The challenge Dismiss asks for a bedtime screen coming up at `now`: {steps, complex, grid} - the own list of the
    "Comes back" step active then (reminders.active_step, the one that also sets the wait), else the general list - or
    None while "Challenge before Dismiss" is off. The phrase extras are the bedtime ones (no own phrase)."""
    import reminders
    steps = reminders.challenge_steps_now(sleep, now)
    if not steps:
        return None
    return {"steps": steps, "complex": bool(sleep.get("challenge_complex")), "grid": bool(sleep.get("challenge_grid"))}


def plan_rects(found, fallback=DEFAULT_RECT):
    """(the rectangle of the monitor that gets the message, [rectangles of the others that get a plain cover]), in
    physical pixels (left, top, right, bottom) - gui/screens.plan decides which one is the main one."""
    from gui import screens
    main, others = screens.plan(found, fallback)
    return main.rect, [m.rect for m in others]


class Overlay:
    """Thread-safe: the reminder worker, the page's calls and the guard's challenge all arrive on different threads.

    publisher: `publish(event, payload)` (the overlay window's Publisher).   host: `show(background)`, `hide()`,
    `refresh()`.   answer(key, action): the reminder worker's answer (the engine hears it on its own thread).
    now(): the trusted time.   wait_for(key): the Dismiss wait in seconds for that screen (0 = none).
    clock: a monotonic clock in seconds (tests drive it).
    page_ready: is the page known to work? The agent passes False: the window is then only shown after `page_loaded()`.
    fallback(kind, title, text): tells the owner another way when the page is not ready within `ready_wait` seconds.
    block_for(key): does that screen block the clicks behind it ("Block clicks behind the bedtime screen")?
    challenge_for(key): the challenge Dismiss asks for that screen, {steps, complex, grid}, or None (no challenge)."""

    def __init__(self, publisher=None, host=None, answer=None, now=None, wait_for=None, clock=time.monotonic,
                 background=saved_background, log=None, page_ready=True, fallback=None, ready_wait=PAGE_READY_SEC,
                 block_for=None, challenge_for=None):
        self.publisher, self.host, self._answer = publisher, host, answer
        self._now = now or datetime.now
        self._wait_for = wait_for or (lambda key: 0)
        self._block_for = block_for or (lambda key: False)
        self._challenge_for = challenge_for or (lambda key: None)
        self._clock = clock
        self._background = background
        self.log = log or (lambda text: None)
        self._lock = threading.RLock()
        self._host_lock = threading.Lock()
        self._cur: dict | None = None
        self._stash: dict | None = None      # the bedtime screen waiting behind a running break
        self._seq = 0
        self._token = 0
        self._shown = False                  # the window, as the host has it
        self._stop = threading.Event()       # ends the monitor guard of the screen that is up
        self._failsafe: threading.Timer | None = None
        self._page_ok = bool(page_ready)     # the page said it works: only then is the window shown
        self._fallback = fallback
        self._ready_wait = ready_wait
        self._waiting_for_page = None        # the token of the screen whose "page not ready" timer is running

    # ------------------------------------------------------------------ what the page gets
    @property
    def shown(self) -> bool:
        with self._lock:
            return self._cur is not None

    @property
    def on_screen(self) -> bool:
        """Is the window really up? (A screen can be due while its page is not ready yet: then it is not.)"""
        return self._shown

    @property
    def page_ok(self) -> bool:
        return self._page_ok

    @property
    def blocking(self) -> bool:
        """Does the screen that is up block the clicks (and keys) behind it?"""
        with self._lock:
            return bool(self._cur and self._cur["block"])

    def key(self):
        with self._lock:
            return self._cur["key"] if self._cur else None

    def _waiting(self) -> bool:
        return bool(self._cur and self._cur["wait_end"] is not None and self._clock() < self._cur["wait_end"])

    def payload(self) -> dict:
        """The whole cover as the page gets it (an `overlay` push / `overlay_ready`), numbered. The numbers that count
        down are "ms left as of now" - the page adds them to its own steady clock."""
        with self._lock:
            return self._payload()

    def _payload(self) -> dict:
        cur = self._cur
        if cur is None:
            return {"seq": self._seq, "shown": False}
        mono = self._clock()
        out = {"seq": self._seq, "shown": True, "key": cur["key"], "kind": cur["kind"], "title": cur["title"],
               "text": cur["text"], "buttons": [dict(b) for b in cur["buttons"]],
               "wait_ms": max(0, round((cur["wait_end"] - mono) * 1000)) if cur["wait_end"] is not None else 0,
               "until_ms": max(0, round((cur["until_end"] - mono) * 1000)) if cur["until_end"] is not None else None}
        if cur["kind"] == "sleep":
            now = self._trusted_now()
            out["clock_sec"] = now.hour * 3600 + now.minute * 60 + now.second
        if cur.get("challenge"):
            out["asks"] = challenges.steps_text(cur["challenge"]["steps"])      # ("After the wait, Dismiss asks: ...")
        return out

    def _trusted_now(self) -> datetime:
        try:
            return self._now()
        except Exception:
            return datetime.now()

    # ------------------------------------------------------------------ the engine's calls (any thread)
    def show(self, key: str, title: str, text: str, until, buttons):
        """Put the cover up (the same key again replaces it, with a fresh wait). `until`: when a break ends, or None.
        One cover at a time: a running break goes in front of the bedtime screen, which waits behind it (and comes back,
        with a fresh wait, when the break is over)."""
        kind = KINDS.get(key, "sleep")
        try:
            wait = max(0, int(self._wait_for(key) or 0))
        except Exception:
            wait = 0
        try:
            block = bool(self._block_for(key))
        except Exception:
            block = False
        try:
            challenge = self._challenge_for(key) if kind == "sleep" else None
        except Exception:
            challenge = None
        if challenge is not None and not challenges.read_steps(challenge.get("steps")):
            challenge = None
        ms = left_ms(until, self._trusted_now())
        mono = self._clock()
        with self._lock:
            self._token += 1
            screen = {"key": key, "kind": kind, "title": str(title), "text": str(text), "token": self._token,
                      "buttons": clean_buttons(buttons), "wait_total": wait, "block": block, "challenge": challenge,
                      "wait_end": mono + wait if wait > 0 else None,
                      "until_end": mono + ms / 1000 if ms is not None else None}
            cur = self._cur
            if cur is not None and cur["kind"] == "break" and kind != "break":
                self._stash = screen                 # (the bedtime screen is due while a break runs: after it)
                return
            if cur is not None and cur["kind"] != "break" and kind == "break":
                self._stash = cur
            self._cur = screen
            self._arm_failsafe(ms)
        self._changed()

    def close(self, key: str) -> bool:
        """The engine takes the cover down (the night is over, an emergency paused the alerts, the break ended)."""
        with self._lock:
            if self._stash is not None and self._stash["key"] == key:
                self._stash = None
                return True
            if self._cur is None or self._cur["key"] != key:
                return False
            self._drop()
        self._changed()
        return True

    # ------------------------------------------------------------------ answers from the page
    def dismiss(self) -> dict:
        """The Dismiss button. Refused (nothing changes) while its wait runs. Raises ValueError when the screen has no
        Dismiss or is gone. A screen with a bedtime challenge is NOT dismissed here: after the wait it answers
        {challenge: {token, steps, complex, grid}} - the Api asks the guard for exactly that challenge, and only
        `dismiss_passed(token)` (once it is passed) takes the screen down."""
        with self._lock:
            cur = self._screen("dismiss")
            if self._waiting():
                return {"early": True, "wait_ms": self._wait_left_ms()}
            token, key, challenge = cur["token"], cur["key"], cur.get("challenge")
        if challenge:
            return {"challenge": {"token": token, "steps": [dict(s) for s in challenge["steps"]],
                                  "complex": bool(challenge.get("complex")), "grid": bool(challenge.get("grid"))}}
        self.accept("dismiss", token)
        return {"done": True, "key": key}

    def dismiss_passed(self, token: int) -> dict:
        """The bedtime challenge was passed: the same as Dismiss, once. Raises ValueError when the screen it was for has
        gone (or been replaced by a newer one): nothing is dismissed."""
        with self._lock:
            cur = self._cur
            if cur is None or cur["token"] != token or not cur.get("challenge"):
                raise ValueError("That is not on screen any more.")
            key = cur["key"]
        self.accept("dismiss", token)
        return {"done": True, "key": key}

    def cancel(self) -> dict:
        """Cancel in Disable alerts' choice or the emergency list. While the Dismiss wait runs it is no way round it: the
        screen stays and the wait starts over. After the wait it is the same as Dismiss - unless the screen has a bedtime
        challenge: then it is only a way back to the screen (Dismiss still asks its challenge)."""
        with self._lock:
            if self._cur is None:
                raise ValueError("That is not on screen any more.")
            cur = self._cur
            if cur["kind"] == "break" or "dismiss" not in [b["action"] for b in cur["buttons"]]:
                return {"back": True}           # (a running break: Cancel only goes back to the countdown)
            if self._waiting():
                self._restart_wait()
                result = {"back": True}
            elif cur.get("challenge"):
                return {"back": True}           # (the challenge stands between you and Dismiss: no shortcut through here)
            else:
                token = cur["token"]
                result = None
        if result is not None:
            self._changed()
            return result
        self.accept("dismiss", token)
        return {"done": True}

    def challenge_cancelled(self, token: int):
        """Disable alerts' challenge was cancelled (or never passed): back to the screen; its wait starts over if it was
        still running (backing out is no Dismiss)."""
        with self._lock:
            if self._cur is None or self._cur["token"] != token or not self._waiting():
                return
            self._restart_wait()
        self._changed()

    def token(self) -> int:
        """Names the screen that is up now: a pending action (waiting for the challenge) only applies to this one."""
        with self._lock:
            if self._cur is None:
                raise ValueError("That is not on screen any more.")
            return self._cur["token"]

    def allows(self, action: str) -> int:
        """The token of the screen if it offers `action`'s button (Disable alerts for off_tonight / snooze, Emergency for
        the emergency list). Raises ValueError otherwise."""
        need = "emergency" if action.startswith("emergency") else "disable"
        with self._lock:
            return self._screen(need)["token"]

    def accept(self, action: str, token: int):
        """The final answer, once: the cover goes down FIRST, then the engine hears `action`. Raises ValueError when the
        screen is not the one `token` named."""
        with self._lock:
            cur = self._cur
            if cur is None or cur["token"] != token:
                raise ValueError("That is not on screen any more.")
            key = cur["key"]
            self._drop(keep_stash=not action.startswith("emergency"))
        self._changed()
        if self._answer is not None:
            self._answer(EMERGENCY_KEY if action.startswith("emergency") else key, action)

    # ------------------------------------------------------------------ inside
    def _screen(self, need: str) -> dict:
        cur = self._cur
        if cur is None:
            raise ValueError("That is not on screen any more.")
        if need not in [b["action"] for b in cur["buttons"]]:
            raise ValueError("That button is not on this screen.")
        return cur

    def _wait_left_ms(self) -> int:
        cur = self._cur
        return max(0, round((cur["wait_end"] - self._clock()) * 1000)) if cur and cur["wait_end"] is not None else 0

    def _restart_wait(self):
        cur = self._cur
        if cur["wait_total"] > 0:
            cur["wait_end"] = self._clock() + cur["wait_total"]

    def _drop(self, keep_stash: bool = True):
        """The cover is gone (call with the lock held). The bedtime screen that waited behind a break comes up now, with
        a fresh wait - unless the answer was an emergency, which pauses the bedtime alerts too."""
        self._cur = None
        self._token += 1
        if self._failsafe is not None:
            self._failsafe.cancel()
            self._failsafe = None
        behind, self._stash = self._stash, None
        if behind is not None and keep_stash:
            self._token += 1
            behind["token"] = self._token
            if behind["wait_total"] > 0:
                behind["wait_end"] = self._clock() + behind["wait_total"]
            self._cur = behind

    def _arm_failsafe(self, ms):
        """A break cover must never outlive its end because a message got lost: it leaves FAILSAFE seconds after."""
        if self._failsafe is not None:
            self._failsafe.cancel()
            self._failsafe = None
        if ms is None:
            return
        token = self._token
        timer = threading.Timer(ms / 1000 + BREAK_FAILSAFE_SEC, self._expired, args=(token,))
        timer.daemon = True
        timer.start()
        self._failsafe = timer

    def _expired(self, token: int):
        with self._lock:
            if self._cur is None or self._cur["token"] != token:
                return
            self._drop()
        self.log("overlay: a break cover outlived its end and was taken down")
        self._changed()

    # ------------------------------------------------------------------ the page's ready handshake
    def page_loaded(self):
        """The page has loaded and drawn what is up (its `page_ready` call): from now on the window may be shown - and is
        at once if a screen is due."""
        with self._lock:
            if self._page_ok:
                return
            self._page_ok = True
            up = self._cur is not None
        self.log("overlay: its page is ready" + (" - the screen that was waiting comes up now" if up else ""))
        self._sync_host(up)

    def _not_ready(self):
        """A screen is due but the page has not said it works: the window stays down (a cover without its page is a black
        screen with no way out). Once per screen: if the page is still not ready after `ready_wait`, the fallback."""
        with self._lock:
            cur = self._cur
            if cur is None or self._waiting_for_page == cur["token"]:
                return
            token = self._waiting_for_page = cur["token"]
        timer = threading.Timer(self._ready_wait, self._page_late, args=(token,))
        timer.daemon = True
        timer.start()

    def _page_late(self, token: int):
        with self._lock:
            cur = self._cur
            if self._page_ok or cur is None or cur["token"] != token:
                return
            kind, title, text = cur["kind"], cur["title"], cur["text"]
        self.log(f"overlay: its page did not say it was ready within {self._ready_wait:.0f} s - the {kind} screen "
                 "is not shown (it would be a black screen with no way out); told another way")
        if self._fallback is not None:
            try:
                self._fallback(kind, title, text)
            except Exception as error:
                self.log(f"overlay fallback: {error}")

    # ------------------------------------------------------------------ to the page and the window
    def _changed(self):
        with self._lock:
            self._seq += 1
            payload = self._payload()
            up = self._cur is not None
        if self.publisher is not None:
            self.publisher.publish("overlay", payload)
        self._sync_host(up)

    def _sync_host(self, up: bool):
        """Show the window when a cover is up, hide it when not (it looks at the screen as it is NOW, so two threads
        racing here still end right). A window glitch must never stop a reminder."""
        if self.host is None:
            return
        with self._host_lock:
            wanted = self.shown
            try:
                if wanted and not self._shown:
                    if not self._page_ok:
                        self._not_ready()
                        return
                    self._shown = True
                    self._stop = threading.Event()
                    self.host.show(self._background())
                    threading.Thread(target=self._guard, args=(self._stop,), name="lockdown-overlay-guard",
                                     daemon=True).start()
                elif not wanted and self._shown:
                    self._shown = False
                    self._stop.set()
                    self.host.hide()
            except Exception as error:
                self.log(f"overlay window: {error}")

    def _guard(self, stop: threading.Event):
        """While the cover is up: a monitor plugged or unplugged, or the window moved, puts everything back (blocking the
        clicks behind it: back on top of everything and the keyboard taken back, more often)."""
        while not stop.wait(BLOCK_GUARD_SEC if self.blocking else GUARD_SEC):
            try:
                self.host.refresh()
            except Exception as error:
                self.log(f"overlay guard: {error}")


# ====================================================================== the windows (Windows only, thin glue)

HWND_TOPMOST, SWP_SHOWWINDOW, SWP_NOACTIVATE, SW_RESTORE = -1, 0x40, 0x10, 9
SWP_NOSIZE, SWP_NOMOVE, SW_HIDE, SW_SHOWNOACTIVATE = 0x1, 0x2, 0, 4
GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
GA_ROOTOWNER = 3
NOTE_LIMIT = 3            # the guard writes to the log this many times per screen (what it moved, whose keyboard it took)
COVER_HTML = ("<!doctype html><meta charset=utf-8><title>{title}</title>"
              "<body style=\"margin:0;background:{bg};overflow:hidden\">")


def _user32():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32")
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    return user32


def tool_style(exstyle: int) -> int:
    """The extended style that keeps a window off the taskbar and out of Alt+Tab: WS_EX_TOOLWINDOW on, WS_EX_APPWINDOW
    off (WinForms sets it on every form - ShowInTaskbar - and it forces a taskbar button even on a tool window)."""
    return ((exstyle & 0xFFFFFFFF) | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW


def misplaced(actual, wanted, settled=None) -> bool:
    """Is a window at `actual` out of place when it should be at `wanted`? All three are (left, top, right, bottom) in
    physical pixels - the monitors (gui/screens) and GetWindowRect alike, the process being per-monitor DPI aware, so
    there is no scaling between them. `settled`: where Windows put it the last time it was asked for `wanted` (a pixel
    off through DPI rounding, or clamped): that counts as in place, so the guard never moves it again and again. A rect
    that can't be read (None) is left alone."""
    if actual is None:
        return False
    actual = tuple(actual)
    return actual != tuple(wanted) and (settled is None or actual != tuple(settled))


def _hwnd(title: str) -> int:
    """The native handle of the window with this title; 0 off Windows or when it isn't there."""
    if not sys.platform.startswith("win"):
        return 0
    try:
        return _user32().FindWindowW(None, title) or 0
    except Exception:
        return 0


def _rect_of(hwnd: int):
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        _user32().GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def _put(hwnd: int, rect, front: bool = False, show: bool = True):
    """Exactly `rect` (physical pixels), above everything (`show`: and shown); `front` also takes the keyboard."""
    user32 = _user32()
    if show and user32.IsIconic(hwnd):     # (Win+D or a stray click minimised it: it must come back)
        user32.ShowWindow(hwnd, SW_RESTORE)
    elif show and not user32.IsWindowVisible(hwnd):
        # (a cover made hidden: ShowWindow first - it sends WM_SHOWWINDOW, which is how WinForms and its WebView2 learn
        # the window is up; SetWindowPos's SWP_SHOWWINDOW alone leaves the page undrawn, as resident/corner.py found)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    left, top, right, bottom = rect
    user32.SetWindowPos(hwnd, HWND_TOPMOST, left, top, right - left, bottom - top,
                        (SWP_SHOWWINDOW if show else 0) | (0 if front else SWP_NOACTIVATE))
    if front:
        user32.SetForegroundWindow(hwnd)


def _raise(hwnd: int):
    """Back on top of every other always-on-top window - nothing else: not moved, not resized, not activated, not shown
    (a window already on top is not touched at all: Windows sees there is nothing to change)."""
    _user32().SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def _visible(hwnd: int) -> bool:
    try:
        return bool(_user32().IsWindowVisible(hwnd))
    except Exception:
        return True                        # (can't tell: leave it as it is)


def _tool_window(hwnd: int) -> bool:
    """Make `hwnd` a tool window (tool_style). True when its style had to be changed."""
    try:
        import ctypes
        user32 = _user32()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        wanted = tool_style(style)
        if wanted == style:
            return False
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(wanted).value)
        return True
    except Exception:
        return False


def _reshow(hwnd: int):
    """Hidden and shown again, without taking the keyboard: the taskbar only looks at a window's style when it comes up."""
    user32 = _user32()
    user32.ShowWindow(hwnd, SW_HIDE)
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


def _window_name(hwnd: int) -> str:
    """'title' [class] of a window, for the log."""
    try:
        import ctypes
        user32 = _user32()
        title, cls = ctypes.create_unicode_buffer(80), ctypes.create_unicode_buffer(80)
        user32.GetWindowTextW(hwnd, title, 80)
        user32.GetClassNameW(hwnd, cls, 80)
        return f"'{title.value}' [{cls.value}]"
    except Exception:
        return str(hwnd)


def _take_keys(hwnd: int, ours) -> int:
    """The keyboard back to the cover `hwnd` unless one of `ours` (the cover, its covers) - or a window they own, such as
    a drop-down of the page - has it. Windows refuses SetForegroundWindow to a process that is not in front (the tray
    agent never is): for that one call this thread shares the input state of the window in front, the usual way round
    it (when nothing is in front there is nobody to share with, and it is simply asked for). No hook, nothing is pressed,
    the pointer is not touched. Returns the window that had the keyboard when it was taken from it, else 0."""
    import ctypes
    user32 = _user32()
    front = user32.GetForegroundWindow() or 0
    if front and (front in ours or (user32.GetAncestor(front, GA_ROOTOWNER) or 0) in ours):
        return 0
    mine = ctypes.WinDLL("kernel32").GetCurrentThreadId()
    theirs = user32.GetWindowThreadProcessId(front, None) if front else 0
    attached = bool(theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(mine, theirs, False)
    return front


class OverlayHost:
    """Shows / hides the pywebview window `window` ("Lockdown overlay") over the primary monitor and a plain cover of
    the same colour on every other one. `monitors()` -> [gui.screens.Monitor]; `make_cover(title, background)` makes a
    cover window (default: a pywebview window with a blank page). The covers are made and taken down on a thread of
    their own (a new window takes a moment): the bedtime screen itself is up at once. Without Win32 (or when the native
    handle isn't found) pywebview's own move / resize / show are used. `blocking()`: does the screen up now block the clicks
    behind it (then every refresh puts everything back on top and takes the keyboard back)?"""

    def __init__(self, window, monitors=None, make_cover=None, log=None, blocking=None):
        from gui import screens
        self.window = window
        self._monitors = monitors or screens.monitors
        self._make_cover = make_cover or self._pywebview_cover
        self.log = log or (lambda text: None)
        self.blocking = blocking or (lambda: False)
        self._covers: list[tuple[object, tuple]] = []      # (window, rect)
        self._covers_lock = threading.Lock()
        self._wanted = None                                 # (main rect, [cover rects]) as last placed; None = hidden
        self._bg = BACKGROUNDS["dark"]
        self._allow_close = False
        self._lock = threading.RLock()       # show / refresh / hide one at a time: a refresh can't re-show what hide hid
        self._settled: dict = {}             # (hwnd, wanted rect) -> where Windows put it when asked (if not exactly there)
        self._notes = 0                      # guard lines written to the log for the screen that is up

    def show(self, background: str):
        with self._lock:
            self._bg = background
            main, others = plan_rects(self._monitors())
            self._wanted = (main, others)
            self._settled, self._notes = {}, 0
            hwnd = _hwnd(TITLE)
            if hwnd:
                _tool_window(hwnd)               # (while still hidden: the taskbar only looks when a window comes up)
                _put(hwnd, main, show=False)     # (placed while still hidden: no flash in the wrong place)
            self.window.show()
            if hwnd:
                if _tool_window(hwnd):           # (reset while it came up: hidden and shown once more for the taskbar)
                    self.log("overlay: the window came up with a taskbar button - made a tool window again")
                    _reshow(hwnd)
                _put(hwnd, main, front=True)
                self._settle(hwnd, main, "the screen")
            else:
                left, top, right, bottom = main
                self.window.move(left, top)
                self.window.resize(right - left, bottom - top)
            self._covers_soon()

    def refresh(self):
        """Put the window and the covers back where they belong (the monitors may have changed, a window been moved) -
        only what really is out of place is moved. Blocking the clicks behind: also back on top of every other
        always-on-top window (Z-order only), shown again if something hid it, and the keyboard taken back when another
        window has it."""
        with self._lock:                     # (placing only: hide() never waits for another app's input below)
            if self._wanted is None:
                return
            block = self.blocking()
            main, others = plan_rects(self._monitors())
            hwnd = _hwnd(TITLE)
            if hwnd:
                self._restyle(hwnd)
                if self._out_of_place(hwnd, main, block):
                    _put(hwnd, main)
                    self._settle(hwnd, main, "the screen")
                elif block:
                    _raise(hwnd)
            changed = (main, others) != self._wanted
            self._wanted = (main, others)
            covers = [_hwnd(f"{TITLE} cover {i + 1}") for i in range(len(self._covers))]
            for cover in covers:
                if cover:
                    self._restyle(cover)
            if changed or self._covers_moved(block):
                self._covers_soon()
            elif block:
                for cover in covers:
                    if cover:
                        _raise(cover)
        if hwnd and block and self._wanted is not None:
            taken = _take_keys(hwnd, {hwnd} | {c for c in covers if c})
            if taken:
                self._note(f"the keyboard was taken back from {_window_name(taken)}")

    def hide(self):
        with self._lock:
            self._wanted = None
            self.window.hide()
        self._covers_soon()

    # ------------------------------------------------------------------ keeping them in place without fighting Windows
    def _out_of_place(self, hwnd: int, rect, block: bool) -> bool:
        """Moved, resized or minimised (`misplaced`) - or, blocking the clicks behind, hidden by something."""
        actual = _rect_of(hwnd)
        if misplaced(actual, rect, self._settled.get((hwnd, tuple(rect)))):
            self._note(f"window {hwnd} was at {actual}, put back at {tuple(rect)}")
            return True
        return block and not _visible(hwnd)

    def _settle(self, hwnd: int, rect, name: str):
        """Just placed at `rect`: where Windows really put it, if not exactly there, counts as in place from now on (it
        would put it there again; moving a window at every check is the suspected cause of the jumping pointer)."""
        key = (hwnd, tuple(rect))
        got = _rect_of(hwnd)
        if got is not None and tuple(got) != tuple(rect):
            if self._settled.get(key) != tuple(got):
                self.log(f"overlay: {name} was put at {tuple(rect)}, Windows placed it at {tuple(got)} - left there")
            self._settled[key] = tuple(got)
        else:
            self._settled.pop(key, None)

    def _restyle(self, hwnd: int):
        """The tool-window style back if anything reset it (the taskbar picks it up the next time the screen comes up)."""
        if _tool_window(hwnd):
            self._note(f"window {hwnd} had lost its tool-window style (taskbar button) - put back")

    def _note(self, text: str):
        if self._notes < NOTE_LIMIT:
            self._notes += 1
            self.log(f"overlay guard: {text}")

    def closing(self) -> bool:
        """The covers' close event: False (ignored) while they are up - Alt+F4 does nothing."""
        return self._allow_close

    def close_covers(self):
        """The agent is quitting: the covers go now (a window left open would keep the GUI loop, and the agent, alive)."""
        self._wanted = None
        with self._covers_lock:
            self._destroy_covers()

    # ------------------------------------------------------------------ the covers on the other monitors
    def _covers_soon(self):
        threading.Thread(target=self._sync_covers, name="lockdown-overlay-covers", daemon=True).start()

    def _covers_moved(self, block: bool = False) -> bool:
        for i, (_window, rect) in enumerate(self._covers):
            hwnd = _hwnd(f"{TITLE} cover {i + 1}")
            if hwnd and self._out_of_place(hwnd, rect, block):
                return True
        return False

    def _sync_covers(self):
        """Make the covers match what is wanted NOW (called on a thread of its own, one at a time; a call that runs
        after the wishes changed again simply finds nothing more to do). A new cover is made hidden, made a tool window,
        and only then placed and shown (without taking the keyboard)."""
        with self._covers_lock:
            wanted = self._wanted
            rects = [tuple(r) for r in wanted[1]] if wanted else []
            made = []
            if [rect for _w, rect in self._covers] != rects:
                self._destroy_covers()
                for i, rect in enumerate(rects):
                    title = f"{TITLE} cover {i + 1}"
                    try:
                        self._covers.append((self._make_cover(title, self._bg), rect))
                        made.append(len(self._covers) - 1)
                    except Exception as error:
                        self.log(f"overlay cover: {error}")
            for i, (window, rect) in enumerate(self._covers):
                hwnd = _hwnd(f"{TITLE} cover {i + 1}")
                if not hwnd:
                    if i in made:
                        self._show_plain(window, rect)
                    continue
                _tool_window(hwnd)
                if i in made:
                    _put(hwnd, rect, show=False)        # (placed while still hidden: no flash at the default spot)
                if i in made or not _visible(hwnd) or self._out_of_place(hwnd, rect, False):
                    _put(hwnd, rect)
                    self._settle(hwnd, rect, f"cover {i + 1}")
            if self._wanted is None:                    # (hidden while the covers were being made)
                self._destroy_covers()

    def _show_plain(self, window, rect):
        """No native handle (not Windows, or it wasn't found): pywebview's own move / resize / show."""
        try:
            left, top, right, bottom = rect
            window.move(left, top)
            window.resize(right - left, bottom - top)
            window.show()
        except Exception as error:
            self.log(f"overlay cover: {error}")

    def _destroy_covers(self):
        covers, self._covers = self._covers, []
        self._allow_close = True
        try:
            for window, _rect in covers:
                try:
                    window.destroy()
                except Exception as error:
                    self.log(f"overlay cover: {error}")
        finally:
            self._allow_close = False

    def _pywebview_cover(self, title: str, background: str):
        import webview
        window = webview.create_window(      # (hidden: shown once it is a tool window - no taskbar button)
            title, html=COVER_HTML.format(title=title, bg=background), frameless=True, on_top=True, easy_drag=False,
            shadow=False, focus=False, resizable=False, hidden=True, background_color=background)
        window.events.closing += self.closing
        return window


# ====================================================================== wiring into the agent

OVERLAY_PUSH_SEC = 0.03      # the overlay page's pushes go out this soon (the screen is drawn within a frame or two)


def attach(win, hub, ctx, ui, log=None):
    """Wire the cover into the agent: its Publisher, the Overlay (`ctx.overlay`, for the overlay_* Api), the window glue
    and the ReminderUI's `overlay_handler` (`ui`: what reminder_ui.attach returned). Returns the publisher:
    `publisher.stop()` when the agent ends."""
    import reminders
    from resident.publisher import Publisher

    publisher = Publisher(win.overlay, interval=OVERLAY_PUSH_SEC, visible=True, log=log)   # (the page is loaded while
    #                                                                          it is hidden: always "visible" to it)

    def wait_for(key):          # (the engine calls show() on the reminder worker's thread: its own Database is right)
        if key != "sleep" or ui.worker is None:
            return 0
        return reminders.dismiss_wait_now(reminders.load(ui.worker.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP),
                                          now())      # (the wait of the bedtime step active now)

    def now():                  # (any thread: trusted time from the snapshot's offset, no database)
        snap = hub.snapshot
        return hub.clock.now_with(snap.clock_offset if snap else 0.0)

    def answer(key, action):
        ui.worker.answer(key, action)
        ui.worker.poke()        # (an emergency ends a strict break on the engine's very next tick)

    def fallback(kind, title, text):
        """The cover's page never said it works: the bedtime screen becomes a Windows notification (it needs no web
        page); a strict break keeps your windows minimised all the same (MinimizeWorker), so it is only logged."""
        if kind == "break" or getattr(ui, "toaster", None) is None:
            return
        ui.toaster.send(text, title=title)

    def block_for(key):         # (the reminder worker's thread, as wait_for; one cover: a strict break goes by it too)
        if ui.worker is None:
            return False
        return bool(reminders.load(ui.worker.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP).get("block_behind"))

    def challenge_for(key):     # (the reminder worker's thread, as wait_for: the list of the bedtime step active NOW)
        if key != "sleep" or ui.worker is None:
            return None
        return bedtime_challenge(reminders.load(ui.worker.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP), now())

    host = OverlayHost(win.overlay, log=log, blocking=lambda: overlay.blocking)
    overlay = Overlay(publisher, host, answer, now, wait_for, log=log, page_ready=False, fallback=fallback,
                      block_for=block_for, challenge_for=challenge_for)
    ctx.overlay = overlay
    ui.overlay_handler = overlay
    win.overlay_ctl, win.overlay_publisher = overlay, publisher
    from resident import windows
    windows.on_page_ready(win, "overlay", overlay.page_loaded)
    return publisher
