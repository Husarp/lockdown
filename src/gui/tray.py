"""System tray icon (pystray runs on its own thread)."""
import pystray
from PIL import Image, ImageDraw

GREEN = "#3fb950"   # enforcing
RED = "#f85149"     # service down


def _dot(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=color)
    return img


class Tray:
    def __init__(self, on_open, on_exit):
        self.status_text = "Starting..."
        self.running: bool | None = None
        self.icon = pystray.Icon(
            "Lockdown", _dot(RED), "Lockdown",
            menu=pystray.Menu(
                pystray.MenuItem("Open Lockdown", lambda: on_open(), default=True),
                pystray.MenuItem(lambda item: self.status_text, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", lambda: on_exit()),
            ),
        )

    def start(self):
        self.icon.run_detached()

    def stop(self):
        self.icon.stop()

    def notify(self, message: str):
        """Windows notification (toast) from the tray icon."""
        self.icon.notify(message, "Lockdown")

    def update(self, running: bool, status_text: str):
        self.status_text = status_text
        if running != self.running:
            self.running = running
            self.icon.icon = _dot(GREEN if running else RED)
        self.icon.title = f"Lockdown - {status_text}" + ("" if running else " (service not running)")
        self.icon.update_menu()
