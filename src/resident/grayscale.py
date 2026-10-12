"""The screen's colour: "Grey it" (an app blocked that way stays open, the whole screen goes grey while it is in front) and
bedtime grayscale (grey from bedtime to wake-up time) - the PC's version of the phone's Grayscale (remind/Reminders.kt).

How (Windows, no admin): the Magnification API. `MagSetFullscreenColorEffect` with a greyscale colour matrix (luma weights
0.3 / 0.59 / 0.11) greys the whole desktop, the identity matrix gives the colour back, `MagUninitialize` on exit. It is the
agent that does it (it runs in your session and owns the desktop; the service has no desktop), and every Mag call is made
on ONE thread of its own - the API belongs to the thread that called MagInitialize.

`GreyScreen` is what the rest of the agent talks to. Two things can want the screen grey at once ("app" - the window in
front is an app blocked with Grey it, "bedtime"); it is grey while any of them does, and the colour comes back once none
does - so they never fight. Rules it keeps:
- it only ever undoes what Lockdown itself applied. The colour effect that was there before (Windows' Magnifier, another
  tool) is read first and put back, not replaced by the identity; an effect somebody else has set since is left alone.
  (Windows' own Color filters, Settings > Accessibility, are a separate setting and are never touched.)
- the colour comes back when nothing wants grey, on `release()` (the agent stops, Lockdown is switched off, the agent
  crashes: `install_exit_hooks`) - and a refused write is tried again, never given up on for good.
- Windows only: anywhere else (the tests) the backend is a fake that records what it was asked.
"""
import atexit
import logging
import queue
import sys
import threading
import time

REASSERT_SEC = 2.0         # while grey: how often it is checked that the screen still shows it (a display change resets it)
RETRY_SEC = 30.0           # a start / write that failed is tried again this often (and logged this often)
RELEASE_WAIT_SEC = 2.0     # release() waits this long for the colour to be back

IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 0.0, 1.0)
LUMA = (0.3, 0.59, 0.11)   # the weight of red, green and blue in a grey pixel
# (Mag takes a 5x5 matrix a colour row vector [R G B A 1] is multiplied by: the first three ROWS are what each input
# channel adds to the output, so each of them is its weight in all three output columns.)
GREY = (LUMA[0], LUMA[0], LUMA[0], 0.0, 0.0,
        LUMA[1], LUMA[1], LUMA[1], 0.0, 0.0,
        LUMA[2], LUMA[2], LUMA[2], 0.0, 0.0,
        0.0, 0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 0.0, 1.0)
SAME = 1e-3                # two matrices closer than this are the same (the API keeps 32-bit floats)


def same(a, b) -> bool:
    return a is not None and b is not None and all(abs(x - y) < SAME for x, y in zip(a, b))


# ---------------------------------------------------------------- backends

class MagBackend:
    """The Windows Magnification API (magnification.dll). Only ever used from GreyScreen's thread."""

    def __init__(self):
        self._dll = None
        self._effect = None
        self.started = False

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        if self.started:
            return True
        import ctypes
        from ctypes import wintypes

        class MAGCOLOREFFECT(ctypes.Structure):
            _fields_ = [("transform", ctypes.c_float * 25)]
        self._effect = MAGCOLOREFFECT
        dll = ctypes.WinDLL("magnification")
        dll.MagInitialize.restype = wintypes.BOOL
        dll.MagUninitialize.restype = wintypes.BOOL
        dll.MagSetFullscreenColorEffect.argtypes = [ctypes.POINTER(MAGCOLOREFFECT)]
        dll.MagSetFullscreenColorEffect.restype = wintypes.BOOL
        dll.MagGetFullscreenColorEffect.argtypes = [ctypes.POINTER(MAGCOLOREFFECT)]
        dll.MagGetFullscreenColorEffect.restype = wintypes.BOOL
        if not dll.MagInitialize():
            return False
        self._dll, self.started = dll, True
        return True

    def read(self):
        """The colour matrix in force (25 numbers), or None when it can't be read."""
        if not self.started:
            return None
        import ctypes
        effect = self._effect()
        return tuple(effect.transform) if self._dll.MagGetFullscreenColorEffect(ctypes.byref(effect)) else None

    def apply(self, matrix) -> bool:
        if not self.started:
            return False
        import ctypes
        effect = self._effect()
        for i, value in enumerate(matrix):
            effect.transform[i] = value
        return bool(self._dll.MagSetFullscreenColorEffect(ctypes.byref(effect)))

    def stop(self):
        if self.started and self._dll is not None:
            self._dll.MagUninitialize()
        self.started = False


class FakeBackend:
    """What the tests (and any system that is not Windows) get: the screen is just a matrix, every call is recorded.
    `broken`: start() / apply() refuse (no DLL, the call fails)."""

    def __init__(self, screen=IDENTITY, broken: bool = False):
        self.screen, self.broken, self.started = screen, broken, False
        self.calls: list = []
        self.thread_names: set = set()

    def _note(self, what, *args):
        self.calls.append((what, *args))
        self.thread_names.add(threading.current_thread().name)

    def start(self) -> bool:
        self._note("start")
        if self.broken:
            return False
        self.started = True
        return True

    def read(self):
        self._note("read")
        return self.screen if self.started else None

    def apply(self, matrix) -> bool:
        self._note("apply", tuple(matrix))
        if self.broken or not self.started:
            return False
        self.screen = tuple(matrix)
        return True

    def stop(self):
        self._note("stop")
        self.started = False

    @property
    def grey(self) -> bool:
        return same(self.screen, GREY)


# ---------------------------------------------------------------- the one place that owns the screen's colour

class GreyScreen:
    """The screen's colour for the whole agent. `want(who, on)` from any thread; the colour is grey while any `who` wants it.

    Everything that touches the API runs on the "lockdown-grey" thread, started the first time somebody wants grey and
    ended by `release()`. `backend`: what to talk to (default: the Windows API; a FakeBackend elsewhere). `log` takes a
    line of text for the app log."""

    def __init__(self, backend=None, reassert_sec: float = REASSERT_SEC, retry_sec: float = RETRY_SEC, log=None):
        self._backend = backend
        self.reassert_sec, self.retry_sec = reassert_sec, retry_sec
        self._log = log or (lambda text: logging.getLogger("lockdown.grayscale").warning(text))
        self._lock = threading.Lock()
        self._wants: dict[str, bool] = {}
        self._commands: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ending = False                  # the thread has been told to stop: the next command starts a fresh one
        self._pending = 0                     # commands posted and not done yet (wait_idle)
        self._done = threading.Condition()
        self.applied = False                  # Lockdown's grey is on the screen right now
        self._prev = None                     # the colour effect that was there before it (None: none / the identity)
        self._last_problem = 0.0

    # ----- for any thread
    @property
    def wanted(self) -> bool:
        with self._lock:
            return any(self._wants.values())

    def want(self, who: str, on: bool):
        """`who` wants the screen grey (True) or not (False) from now on. Cheap when nothing changed."""
        with self._lock:
            if self._wants.get(who, False) == bool(on):
                return
            self._wants[who] = bool(on)
            if not on and not self.applied and not any(self._wants.values()):
                return                        # (it never was grey: nothing to do)
        self._post("sync")

    def release(self, timeout: float = RELEASE_WAIT_SEC) -> bool:
        """Colour back and the API let go of - the agent is stopping or has crashed. Nobody wants grey after this (a
        later `want(who, True)` starts it again). True once it is done."""
        with self._lock:
            self._wants.clear()
            thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        self._post("stop")
        if thread is not threading.current_thread():
            thread.join(timeout)
        return not thread.is_alive()

    def wait_idle(self, timeout: float = 2.0) -> bool:
        """(tests) Wait until everything asked so far has been done."""
        with self._done:
            return self._done.wait_for(lambda: self._pending == 0, timeout)

    # ----- the thread
    def _post(self, command: str):
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive() and not self._ending
            if command == "stop":
                if not alive:
                    return
                self._ending = True           # (it ends after this one; a later command starts a fresh thread)
            elif not alive:
                previous, self._ending = self._thread, False
                self._thread = threading.Thread(target=self._run, args=(previous,), name="lockdown-grey", daemon=True)
                self._thread.start()
        with self._done:
            self._pending += 1
        self._commands.put(command)

    def _wanted(self) -> bool:
        with self._lock:
            return any(self._wants.values())

    def _problem(self, text: str):
        now = time.monotonic()
        if now - self._last_problem >= self.retry_sec:
            self._last_problem = now
            self._log(text)

    def _run(self, previous=None):
        if previous is not None and previous is not threading.current_thread():
            previous.join()                   # (one thread at a time talks to the API)
        backend = self._backend if self._backend is not None else (MagBackend() if sys.platform == "win32" else FakeBackend())
        failed_at = None                      # when a start / write was last refused (it is tried again after retry_sec)
        try:
            while True:
                waiting = self.applied or self._wanted()      # (grey, or about to be: look again now and then)
                try:
                    command = self._commands.get(timeout=self.reassert_sec) if waiting else self._commands.get()
                except queue.Empty:
                    command = "check"
                try:
                    if command == "stop":
                        return
                    if self._wanted():
                        refused_lately = failed_at is not None and time.monotonic() - failed_at < self.retry_sec
                        if command != "check" or not refused_lately:
                            failed_at = None if self._grey(backend) else time.monotonic()
                    else:
                        self._restore(backend)
                        failed_at = None
                finally:
                    if command != "check":
                        with self._done:
                            self._pending -= 1
                            self._done.notify_all()
        except Exception as error:
            self._problem(f"The grey screen failed: {error!r}")
        finally:
            try:
                self._restore(backend)
                backend.stop()
            except Exception as error:
                self._problem(f"The colour could not be given back: {error!r}")
            self.applied = False

    def _grey(self, backend) -> bool:
        """Make the screen grey, or check that it still is. False when Windows refused."""
        if not backend.start():
            self._problem("The grey screen could not start (the Magnification API refused).")
            return False
        current = backend.read()
        if self.applied and (current is None or same(current, GREY)):
            return True                       # (still grey - or Windows won't say: nothing to do)
        if not same(current, GREY):
            # taking the screen over (or somebody changed the colour meanwhile): the effect to give back is the one
            # that is there now - unless it is plain colour
            self._prev = None if current is None or same(current, IDENTITY) else tuple(current)
        if not backend.apply(GREY):
            self._problem("The grey screen could not be applied (the Magnification API refused).")
            return False
        self.applied = True
        return True

    def _restore(self, backend):
        """Colour back - but only what is Lockdown's: nothing is written when the screen no longer shows our grey."""
        if not self.applied:
            return
        current = backend.read() if backend.started else None
        if current is None or same(current, GREY):
            if not backend.apply(self._prev if self._prev is not None else IDENTITY):
                self._problem("The colour could not be given back (the Magnification API refused).")
                return                        # applied stays True: tried again at the next check
        self.applied, self._prev = False, None


# ---------------------------------------------------------------- when the agent ends

def install_exit_hooks(screen: GreyScreen):
    """The colour comes back when the agent exits normally (atexit) and when it crashes: an uncaught exception on the main
    thread (about to end the process) or on any thread (the workers then ask for grey again when they still want it)."""
    atexit.register(screen.release)
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        try:
            screen.release()
        finally:
            previous(exc_type, exc, tb)
    sys.excepthook = hook
    previous_thread = threading.excepthook

    def thread_hook(args):
        try:
            if args.exc_type is not SystemExit:
                screen.release()
        finally:
            previous_thread(args)
    threading.excepthook = thread_hook
