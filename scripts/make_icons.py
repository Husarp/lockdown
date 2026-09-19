"""Regenerate the app icon (assets/lockdown.ico + lockdown.png) from the drawn Lockdown mark (gui/icon_art.py).
Run: .venv\\Scripts\\python.exe scripts\\make_icons.py"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from gui import icon_art   # noqa: E402

ASSETS = ROOT / "assets"
SIZES = [16, 24, 32, 48, 64, 128, 256]

base = icon_art.app_icon(256)
base.save(ASSETS / "lockdown.ico", format="ICO", sizes=[(s, s) for s in SIZES])
base.save(ASSETS / "lockdown.png")
print("wrote", ASSETS / "lockdown.ico", "and lockdown.png")
