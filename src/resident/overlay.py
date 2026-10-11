"""The full-screen cover (R08): the bedtime screen and a running strict break, solid colour, on every monitor.

`Overlay` is plain Python and holds the truth: what is on the cover (title, text, buttons, the Dismiss wait, a
countdown), and what each answer does. The page (ui/overlay.html + js/overlay.js) only draws it and ticks the numbers
locally from the "ms left" values it is given; the Api (webapi/overlay.py) hands it the page's requests. The reminder
engine reaches it through the ReminderUI (`show(key, title, text, until, buttons)` / `close(key)`).

Rules kept here, never in the page:
- Dismiss waits (reminders.dismiss_wait seconds, from the moment the screen comes up, afresh each time): an early
  Dismiss is refused. Backing out of Disable alerts / Emergency while the wait runs is no Dismiss either - the screen
  stays and its wait starts over.
- The screen is taken down FIRST and only then does the engine hear the answer (as the corner window does), so a
  challenge or question that follows can never sit behind it.
- Only the buttons the engine gave can be used; an answer works once (a second click finds the screen gone).

`OverlayHost` is the Win32 / pywebview glue (the window on the primary monitor, a plain cover of the same colour on
every other one, always on top). It is kept thin; the maths is in `plan_rects`."""
import sys
import threading
import time
from datetime import datetime

# The page background (css --bg in tokens.css) per theme: the window and the covers on other monitors are painted with
# it, so the cover matches the page exactly. js/theme.js resolves "system" the same way (prefers-color-scheme).
BACKGROUNDS = {"dark": "#191817", "amoled": "#000000", "light": "#F3F2F1"}
DEFAULT_RECT = (0, 0, 1920, 1080)      # when Windows can't be asked: a plain 1080p screen
GUARD_SEC = 2.0                        # while the cover is up the monitors are re-read this often
BREAK_FAILSAFE_SEC = 20                # a break cover leaves this long after its end even if the engine never says
SNOOZE_MIN = 15                        # the one snooze Disable alerts offers
MAX_BUTTONS = 4
KINDS = {"sleep": "sleep", "break-run": "break"}       # engine key -> what the page shows
EMERGENCY_KEY = "sleep"                # the engine hears every emergency under this key (it pauses bedtime AND break)
TITLE = "Lockdown overlay"


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
    clock: a monotonic clock in seconds (tests drive it)."""

    def __init__(self, publisher=None, host=None, answer=None, now=None, wait_for=None, clock=time.monotonic,
                 background=saved_background, log=None):
        self.publisher, self.host, self._answer = publisher, host, answer
        self._now = now or datetime.now
        self._wait_for = wait_for or (lambda key: 0)
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

    # ------------------------------------------------------------------ what the page gets
    @property
    def shown(self) -> bool:
        with self._lock:
            return self._cur is not None

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
        ms = left_ms(until, self._trusted_now())
        mono = self._clock()
        with self._lock:
            self._token += 1
            screen = {"key": key, "kind": kind, "title": str(title), "text": str(text), "token": self._token,
                      "buttons": clean_buttons(buttons), "wait_total": wait,
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
        Dismiss or is gone."""
        with self._lock:
            cur = self._screen("dismiss")
            if self._waiting():
                return {"early": True, "wait_ms": self._wait_left_ms()}
            token, key = cur["token"], cur["key"]
        self.accept("dismiss", token)
        return {"done": True, "key": key}

    def cancel(self) -> dict:
        """Cancel in Disable alerts' choice or the emergency list. While the Dismiss wait runs it is no way round it: the
        screen stays and the wait starts over. After the wait it is the same as Dismiss."""
        with self._lock:
            if self._cur is None:
                raise ValueError("That is not on screen any more.")
            cur = self._cur
            if "dismiss" not in [b["action"] for b in cur["buttons"]]:
                return {"back": True}           # (a running break has nothing to dismiss: it just goes on)
            if self._waiting():
                self._restart_wait()
                result = {"back": True}
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
        """While the cover is up: a monitor plugged or unplugged, or the window moved, puts everything back."""
        while not stop.wait(GUARD_SEC):
            try:
                self.host.refresh()
            except Exception as error:
                self.log(f"overlay guard: {error}")


# ====================================================================== the windows (Windows only, thin glue)

HWND_TOPMOST, SWP_SHOWWINDOW, SWP_NOACTIVATE, SW_RESTORE = -1, 0x40, 0x10, 9
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
    return user32


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
    left, top, right, bottom = rect
    user32.SetWindowPos(hwnd, HWND_TOPMOST, left, top, right - left, bottom - top,
                        (SWP_SHOWWINDOW if show else 0) | (0 if front else SWP_NOACTIVATE))
    if front:
        user32.SetForegroundWindow(hwnd)


class OverlayHost:
    """Shows / hides the pywebview window `window` ("Lockdown overlay") over the primary monitor and a plain cover of
    the same colour on every other one. `monitors()` -> [gui.screens.Monitor]; `make_cover(title, background)` makes a
    cover window (default: a pywebview window with a blank page). The covers are made and taken down on a thread of
    their own (a new window takes a moment): the bedtime screen itself is up at once. Without Win32 (or when the native
    handle isn't found) pywebview's own move / resize / show are used."""

    def __init__(self, window, monitors=None, make_cover=None, log=None):
        from gui import screens
        self.window = window
        self._monitors = monitors or screens.monitors
        self._make_cover = make_cover or self._pywebview_cover
        self.log = log or (lambda text: None)
        self._covers: list[tuple[object, tuple]] = []      # (window, rect)
        self._covers_lock = threading.Lock()
        self._wanted = None                                 # (main rect, [cover rects]) as last placed; None = hidden
        self._bg = BACKGROUNDS["dark"]
        self._allow_close = False

    def show(self, background: str):
        self._bg = background
        main, others = plan_rects(self._monitors())
        self._wanted = (main, others)
        hwnd = _hwnd(TITLE)
        if hwnd:
            _put(hwnd, main, show=False)     # (placed while still hidden: no flash in the wrong place)
        self.window.show()
        if hwnd:
            _put(hwnd, main, front=True)
        else:
            left, top, right, bottom = main
            self.window.move(left, top)
            self.window.resize(right - left, bottom - top)
        self._covers_soon()

    def refresh(self):
        """Put the window and the covers back where they belong (the monitors may have changed, a window been moved)."""
        if self._wanted is None:
            return
        main, others = plan_rects(self._monitors())
        hwnd = _hwnd(TITLE)
        if hwnd and (main != self._wanted[0] or _rect_of(hwnd) != main):
            _put(hwnd, main)
        changed = (main, others) != self._wanted
        self._wanted = (main, others)
        if changed or self._covers_moved():
            self._covers_soon()

    def hide(self):
        self._wanted = None
        self.window.hide()
        self._covers_soon()

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

    def _covers_moved(self) -> bool:
        for i, (_window, rect) in enumerate(self._covers):
            hwnd = _hwnd(f"{TITLE} cover {i + 1}")
            if hwnd and _rect_of(hwnd) != rect:
                return True
        return False

    def _sync_covers(self):
        """Make the covers match what is wanted NOW (called on a thread of its own, one at a time; a call that runs
        after the wishes changed again simply finds nothing more to do)."""
        with self._covers_lock:
            wanted = self._wanted
            rects = [tuple(r) for r in wanted[1]] if wanted else []
            if [rect for _w, rect in self._covers] != rects:
                self._destroy_covers()
                for i, rect in enumerate(rects):
                    title = f"{TITLE} cover {i + 1}"
                    try:
                        self._covers.append((self._make_cover(title, self._bg), rect))
                    except Exception as error:
                        self.log(f"overlay cover: {error}")
            for i, (_window, rect) in enumerate(self._covers):
                hwnd = _hwnd(f"{TITLE} cover {i + 1}")
                if hwnd and _rect_of(hwnd) != rect:
                    _put(hwnd, rect)
            if self._wanted is None:                    # (hidden while the covers were being made)
                self._destroy_covers()

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
        window = webview.create_window(
            title, html=COVER_HTML.format(title=title, bg=background), frameless=True, on_top=True, easy_drag=False,
            shadow=False, focus=False, resizable=False, background_color=background)
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
        return reminders.dismiss_wait(reminders.load(ui.worker.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP))

    def now():                  # (any thread: trusted time from the snapshot's offset, no database)
        snap = hub.snapshot
        return hub.clock.now_with(snap.clock_offset if snap else 0.0)

    def answer(key, action):
        ui.worker.answer(key, action)
        ui.worker.poke()        # (an emergency ends a strict break on the engine's very next tick)

    overlay = Overlay(publisher, OverlayHost(win.overlay, log=log), answer, now, wait_for, log=log)
    ctx.overlay = overlay
    ui.overlay_handler = overlay
    win.overlay_ctl, win.overlay_publisher = overlay, publisher
    return publisher
