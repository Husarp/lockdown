"""System tray icon (pystray runs on its own thread)."""
import ctypes

import pystray
from PIL import Image, ImageDraw
from pystray._util import win32

from gui.theme import APP_ICON

GREEN = "#3fb950"   # enforcing
RED = "#f85149"     # service down
NIIF_USER, NIIF_LARGE_ICON = 0x4, 0x20


def _dot(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=color)
    return img


class Tray:
    def __init__(self, on_open, on_exit, on_mode=None):
        self.status_text = "Starting..."
        self.running: bool | None = None
        self.on_mode = on_mode
        self.modes: list[tuple[str, str]] = []   # (id, name)
        self.active_mode: str | None = None
        self.icon = pystray.Icon(
            "Lockdown", _dot(RED), "Lockdown",
            menu=pystray.Menu(
                pystray.MenuItem("Open Lockdown", lambda: on_open(), default=True),
                pystray.MenuItem(lambda item: self.status_text, None, enabled=False),
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
            self.icon.update_menu()

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
        if not getattr(self, "_logo", None):
            self._logo = ctypes.windll.user32.LoadImageW(None, str(APP_ICON), 1, 48, 48, 0x10)   # icon, from file
        self.icon._message(win32.NIM_MODIFY, win32.NIF_INFO, szInfo="")
        self.icon._message(win32.NIM_MODIFY, win32.NIF_INFO, szInfo=message[:255], szInfoTitle="Lockdown",
                           dwInfoFlags=NIIF_USER | NIIF_LARGE_ICON, hBalloonIcon=self._logo)

    def update(self, running: bool, status_text: str):
        self.status_text = status_text
        if running != self.running:
            self.running = running
            self.icon.icon = _dot(GREEN if running else RED)
        self.icon.title = f"Lockdown - {status_text}" + ("" if running else " (service not running)")
        self.icon.update_menu()
