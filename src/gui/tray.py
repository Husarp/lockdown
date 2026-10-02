"""System tray icon (pystray runs on its own thread).

The window drives it from the Tk thread every few seconds, while the icon and its message loop belong to pystray's
thread. Every touch of the icon (title, picture, balloon) is therefore made only when something actually changed,
one at a time under a lock - not three cross-thread calls every 3 s as before. The menu is never rebuilt from the Tk
thread: that destroyed the native menu while pystray's thread might be showing it (TrackPopupMenuEx). The Tk thread
only marks it out of date; pystray's own thread rebuilds it right before showing it (_Icon).
"""
import ctypes
import threading

import pystray
from pystray._util import win32

from gui import icon_art
from gui.theme import APP_ICON

NIIF_USER, NIIF_LARGE_ICON = 0x4, 0x20


class _Icon(pystray.Icon):
    """update_menu() from any thread only marks the menu out of date; it is rebuilt on pystray's own thread, in
    the click handler (WM_NOTIFY), just before the menu is shown - so it is never destroyed while it is open."""
    menu_dirty = False

    def update_menu(self):
        self.menu_dirty = True

    def _on_notify(self, wparam, lparam):
        if self.menu_dirty:
            self.menu_dirty = False
            self._update_menu()
        return super()._on_notify(wparam, lparam)


def update_text(version: str | None) -> str:
    """The tray menu's line while a newer Lockdown is waiting ("" when there is none)."""
    return f"Update available: Lockdown {version}" if version else ""


class Tray:
    def __init__(self, on_open, on_exit, on_mode=None, on_update=None):
        self.status_text = "Starting..."
        self.running: bool | None = None
        self.on_mode = on_mode
        self.modes: list[tuple[str, str]] = []   # (id, name)
        self.active_mode: str | None = None
        self._icon_state: str | None = None
        self.off = False                         # Lockdown switched off entirely (Anti-Bypass page)
        self.update_version: str | None = None   # a newer Lockdown found and not installed / skipped / snoozed
        self.on_update = on_update
        self._title: str | None = None
        self._lock = threading.Lock()
        self.icon = _Icon(
            "Lockdown", icon_art.tray_icon("red"), "Lockdown",
            menu=pystray.Menu(
                pystray.MenuItem("Open Lockdown", lambda: on_open(), default=True),
                pystray.MenuItem(lambda item: self.status_text, None, enabled=False),
                pystray.MenuItem(lambda item: update_text(self.update_version), None, enabled=False,
                                 visible=lambda item: bool(self.update_version)),
                pystray.MenuItem("Install update", lambda: self.on_update and self.on_update(),
                                 visible=lambda item: bool(self.update_version and self.on_update)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Modes", pystray.Menu(self._mode_items)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", lambda: on_exit()),
            ),
        )

    def _mode_items(self):
        """Quick switch: start a mode (until stopped) or stop the one that's on."""
        def start(mode_id):   # pystray wants actions with no extra arguments
            return lambda: self.on_mode(mode_id)
        for mode_id, name in self.modes:
            yield pystray.MenuItem(name, start(mode_id), checked=lambda item, m=mode_id: self.active_mode == m,
                                   radio=True)
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Stop mode", lambda: self.on_mode(None), enabled=lambda item: bool(self.active_mode))

    def set_modes(self, modes: list[tuple[str, str]], active: str | None):
        if modes != self.modes or active != self.active_mode:
            self.modes, self.active_mode = modes, active
            with self._lock:
                self._refresh_icon()   # a mode turning on/off changes the tray colour (green <-> yellow)
                self.icon.update_menu()

    def set_update(self, version: str | None) -> bool:
        """"Update available" in the menu (or not). Only marks the menu out of date when it changed."""
        if version == self.update_version:
            return False
        self.update_version = version
        with self._lock:
            self.icon.update_menu()
        return True

    def _refresh_icon(self):
        """grey = switched off, green = blocking enforced, yellow = a mode is on, red = service down."""
        state = ("grey" if self.off else "red" if not self.running else "yellow" if self.active_mode
                 else "green")
        if state != self._icon_state:
            self._icon_state = state
            self.icon.icon = icon_art.tray_icon(state)

    def start(self):
        self.icon.run_detached()

    def stop(self):
        self.icon.stop()

    def notify(self, message: str):
        """Windows notification (toast) with the Lockdown logo. It replaces the one still showing, so several in a
        row don't queue up (Windows shows each for a few seconds)."""
        hwnd = getattr(self.icon, "_hwnd", None)   # (pystray's own notify can't set the picture)
        if not hwnd:
            return
        with self._lock:
            if not getattr(self, "_logo", None):
                self._logo = ctypes.windll.user32.LoadImageW(None, str(APP_ICON), 1, 48, 48, 0x10)   # from file
            self.icon._message(win32.NIM_MODIFY, win32.NIF_INFO, szInfo="")
            self.icon._message(win32.NIM_MODIFY, win32.NIF_INFO, szInfo=message[:255], szInfoTitle="Lockdown",
                               dwInfoFlags=NIIF_USER | NIIF_LARGE_ICON, hBalloonIcon=self._logo)

    def update(self, running: bool, status_text: str, off: bool = False) -> bool:
        """Called every 3 s; returns True if anything had to be sent to the icon."""
        title = f"Lockdown - {status_text}" + ("" if running or off else " (service not running)")
        if (status_text, running, off, title) == (self.status_text, self.running, self.off, self._title):
            return False
        self.status_text = status_text
        self.running, self.off = running, off
        with self._lock:
            self._refresh_icon()
            if title != self._title:
                self._title = title
                self.icon.title = title
            self.icon.update_menu()   # (the status line in the menu)
        return True
