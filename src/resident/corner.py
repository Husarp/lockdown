"""The corner window (notices + reminder popups): the cards on it, and the thin glue that shows it.

`Corner` is plain Python and holds the truth: the ordered list of cards (oldest first, so the newest is lowest). Every
change pushes the whole list to the page as one `corner` event (the Publisher coalesces and drops repeats), shows the
window when the first card comes and hides it when the last one goes. The page (ui/corner.html + js/corner.js) draws
the list, times the notices out and tells Python what was pressed (`corner_answer`) and how tall the stack is
(`corner_size`).

Python removes a popup's card FIRST and only then lets the reminder engine hear the answer (as the old popup did: a
challenge or question that follows can never end up behind a popup that is still up).

`CornerHost` is the Win32 / pywebview glue (bottom-right of the work area of the monitor with the window in front,
topmost, never takes the focus). It is kept thin; the maths is in `corner_rect`."""
import itertools
import sys
import threading

CARD_W = 360          # css px: the window is this wide (cards fit it without a sideways scroll)
MARGIN = 12           # css px between the window and the work area's edge
MAX_BUTTONS = 5
NOTICE_MS = 8000      # a notice leaves by itself after this (the page times it; it holds while the mouse is over it)
DEFAULT_WORK = (0, 0, 1920, 1040)       # when Windows can't be asked: a plain 1080p screen minus a taskbar


def corner_rect(work, height_css: float, dpr: float = 1.0, margin: int = MARGIN):
    """(x, y, w, h) in physical pixels for a window `CARD_W` css px wide and `height_css` tall in the bottom-right of the
    work area `work` = (left, top, right, bottom). Never taller than the work area, never off its top."""
    left, top, right, bottom = work
    m = round(margin * dpr)
    w = round(CARD_W * dpr)
    h = min(round(height_css * dpr), max(1, bottom - top - 2 * m))
    return right - m - w, bottom - m - h, w, h


def _clean_buttons(buttons) -> list[dict]:
    """The popup buttons as the page draws them: the first is filled, a "dismiss" is the small dark one."""
    out = []
    for i, (label, action) in enumerate(list(buttons)[:MAX_BUTTONS]):
        style = "dismiss" if action == "dismiss" else ("primary" if i == 0 else "outline")
        out.append({"label": str(label), "action": str(action), "style": style})
    return out


class Corner:
    """The cards. Thread-safe: reminders, workers and the page's calls all arrive on different threads.

    publisher: has `publish(event, payload)` (the corner window's Publisher).   host: `show()`, `hide()`, `place(h, dpr)`.
    spawn(fn): runs a notice's button callback off the calling thread."""

    def __init__(self, publisher=None, host=None, spawn=None, log=None):
        self.publisher, self.host = publisher, host
        self.spawn = spawn or _spawn
        self.log = log or (lambda text: None)
        self._lock = threading.RLock()
        self._host_lock = threading.Lock()
        self._cards: list[dict] = []        # what the page shows, oldest first
        self._meta: dict[str, dict] = {}    # key -> {"guarded", "callbacks"} (never sent to the page)
        self._ids = itertools.count(1)
        self._seq = 0                       # counts the changes: the page ignores a list older than the one it has
        self._shown = False
        self.height = 0
        self.popup_sink = None              # fn(key, action): the reminder engine hears the answer (on its worker)
        self.is_open = lambda key: True     # is the engine still waiting on this popup? (set by the reminder UI)
        self.on_challenge = None            # fn(data): open the challenge in the main window (data = the guard's needs_challenge reply)

    # ------------------------------------------------------------------ adding and removing (any thread)
    def popup(self, key: str, title: str, text: str, buttons, guarded: bool = False):
        """A reminder popup under `key` (a popup already there with that key goes first; this one is the newest)."""
        card = {"key": key, "id": next(self._ids), "kind": "popup", "title": str(title), "text": str(text),
                "buttons": _clean_buttons(buttons)}
        with self._lock:
            self._drop(key)
            self._cards.append(card)
            self._meta[key] = {"guarded": bool(guarded)}
        self._changed()

    def notice(self, message: str, actions=(), sticky: bool = False) -> str:
        """An in-app notice. `actions`: [(label, callback)]; sticky = only its buttons and the ✕ close it."""
        actions = list(actions)[:MAX_BUTTONS]
        key = f"notice:{next(self._ids)}"
        card = {"key": key, "id": next(self._ids), "kind": "notice", "text": str(message), "sticky": bool(sticky),
                "ms": NOTICE_MS, "buttons": [{"label": str(label), "action": f"act:{i}",
                                              "style": "primary" if i == 0 and len(actions) > 1 else "outline"}
                                             for i, (label, _fn) in enumerate(actions)]}
        with self._lock:
            self._cards.append(card)
            self._meta[key] = {"callbacks": [fn for _label, fn in actions]}
        self._changed()
        return key

    def remove(self, key: str) -> bool:
        with self._lock:
            found = self._drop(key)
        if found:
            self._changed()
        return found

    def has(self, key: str) -> bool:
        with self._lock:
            return any(c["key"] == key for c in self._cards)

    def cards(self) -> list[dict]:
        with self._lock:
            return [dict(c) for c in self._cards]

    def _drop(self, key: str):
        before = len(self._cards)
        self._cards = [c for c in self._cards if c["key"] != key]
        self._meta.pop(key, None)
        return len(self._cards) != before

    # ------------------------------------------------------------------ to the page and the window
    def payload(self) -> dict:
        """The whole list as the page gets it (a `corner` push / `corner_ready`), numbered."""
        with self._lock:
            return {"seq": self._seq, "cards": [dict(c) for c in self._cards]}

    def _changed(self):
        with self._lock:
            self._seq += 1
            payload = {"seq": self._seq, "cards": [dict(c) for c in self._cards]}
        if self.publisher is not None:
            self.publisher.publish("corner", payload)
        self._sync_host()

    def _sync_host(self):
        """Show the window when there are cards, hide it when there are none (self-correcting: it looks at the cards as
        they are NOW, so two threads racing here still end right)."""
        if self.host is None:
            return
        with self._host_lock:
            wanted = bool(self._cards)
            if wanted and not self._shown:
                self._shown = True
                self._guard(self.host.show)
            elif not wanted and self._shown:
                self._shown = False
                self.height = 0
                self._guard(self.host.hide)

    def _guard(self, fn, *args):
        try:
            fn(*args)
        except Exception as error:      # a window glitch must never stop a reminder
            self.log(f"corner window: {error}")

    def ready(self) -> dict:
        """What the page asks for when it has loaded: the cards as they are now (it may have missed a push)."""
        return self.payload()

    def page_size(self, height: float, dpr: float = 1.0):
        """The page says how tall the stack is (css px): the window is resized to it, kept in the corner."""
        if height <= 0:
            return
        self.height = height
        with self._host_lock:
            if self.host is not None and self._shown:
                self._guard(self.host.place, height, dpr)

    # ------------------------------------------------------------------ answers from the page
    def answer(self, key: str, action: str, guard=None):
        """A button on a card (or its ✕). The card is taken down FIRST (and the window hidden if it was the last), then
        the answer is acted on. Raises ValueError for a key that isn't up or a button the card doesn't have."""
        with self._lock:
            card = next((c for c in self._cards if c["key"] == key), None)
            if card is None:
                raise ValueError("That is not on screen any more.")
            allowed = [b["action"] for b in card["buttons"]]
            if card["kind"] == "notice":
                allowed.append("close")
            if action not in allowed:     # (a strict break has no ✕: "dismiss" is refused for it)
                raise ValueError("That button is not on this card.")
            meta = self._meta.get(key, {})
            self._drop(key)
        self._changed()                    # <- the card and the window are gone before anything below can show a challenge
        if card["kind"] == "notice":
            if action.startswith("act:"):
                callbacks = meta.get("callbacks", [])
                index = int(action[4:])
                if index < len(callbacks):
                    self.spawn(callbacks[index])
            return None
        if action == "dismiss" and meta.get("guarded") and guard is not None:
            return self._guarded_dismiss(card, guard)
        self._answer(key, action)
        return None

    def _answer(self, key: str, action: str):
        if self.popup_sink is not None:
            self.popup_sink(key, action)

    def _guarded_dismiss(self, card: dict, guard):
        """Waving away an Important reminder needs the Anti-Bypass challenge (when one is on): the card stays down, the
        challenge opens in the main window, passing it answers; cancelling it brings the popup back."""
        key = card["key"]
        what = " ".join(card["text"].split("\n")[0].split())[:60] or card.get("title", "")
        reply = guard.request([f'Dismiss the important reminder "{what}"'],
                              lambda: self._answer(key, "dismiss"), lambda: self._reshow(card))
        data = reply.get("data") if reply.get("ok") else None
        if isinstance(data, dict) and data.get("needs_challenge") and self.on_challenge is not None:
            self._guard(self.on_challenge, data)
        return reply

    def _reshow(self, card: dict):
        """The challenge was cancelled / ran out: the popup is back (if the engine still waits for an answer)."""
        if not self.is_open(card["key"]):
            return
        again = dict(card, id=next(self._ids))
        with self._lock:
            if any(c["key"] == card["key"] for c in self._cards):
                return
            self._cards.append(again)
            self._meta[card["key"]] = {"guarded": True}
        self._changed()


def _spawn(fn):
    threading.Thread(target=fn, name="lockdown-notice-action", daemon=True).start()


# ====================================================================== the window (Windows only, thin glue)

TITLE = "Lockdown notices"
WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, GWL_EXSTYLE = 0x08000000, 0x80, -20
HWND_TOPMOST, SWP_NOACTIVATE, SWP_SHOWWINDOW, SW_HIDE = -1, 0x10, 0x40, 0


def foreground_work_area():
    """The work area (screen minus the taskbar) of the monitor with the window in front, (l, t, r, b) in physical
    pixels; None off Windows or when it can't be read."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
        monitor = user32.MonitorFromWindow(user32.GetForegroundWindow(), 1)    # MONITOR_DEFAULTTOPRIMARY
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return None


class CornerHost:
    """Shows / hides / places the pywebview window `window` ("Lockdown notices"). With Win32 it is placed with
    SWP_NOACTIVATE and WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW (never takes the focus, no taskbar button); without it
    (or when the native handle isn't found) pywebview's own show / hide / move / resize are used."""

    def __init__(self, window, work_area=foreground_work_area):
        self.window, self._work_area = window, work_area
        self.work = DEFAULT_WORK
        self.size = (CARD_W, 120)
        self.dpr = 1.0

    def show(self):
        self.work = self._work_area() or self.work       # the monitor with the window in front, as it is now
        self._put()

    def place(self, height_css: float, dpr: float = 1.0):
        self.size, self.dpr = (CARD_W, height_css), dpr
        self._put()

    def hide(self):
        hwnd = self._hwnd()
        if hwnd:
            import ctypes
            ctypes.WinDLL("user32").ShowWindow(hwnd, SW_HIDE)
        else:
            self.window.hide()

    def _put(self):
        x, y, w, h = corner_rect(self.work, self.size[1], self.dpr)
        hwnd = self._hwnd()
        if not hwnd:
            self.window.resize(round(w / self.dpr), round(h / self.dpr))
            self.window.move(x, y)
            self.window.show()
            return
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, wintypes.UINT]
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        wanted = style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if wanted != style:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(wanted).value)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE | SWP_SHOWWINDOW)

    @staticmethod
    def _hwnd():
        if not sys.platform.startswith("win"):
            return 0
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32")
            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            user32.FindWindowW.restype = wintypes.HWND
            return user32.FindWindowW(None, TITLE) or 0
        except Exception:
            return 0
