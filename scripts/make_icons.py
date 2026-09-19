"""Regenerate the app icon (assets/lockdown.ico + lockdown.png) from the drawn Lockdown mark (gui/icon_art.py).
Run: .venv\\Scripts\\python.exe scripts\\make_icons.py"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from gui import icon_art   # noqa: E402

ASSETS = ROOT / "assets"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]   # 20 / 40: what Windows shows at 125% scaling (title bar / taskbar)

# Render each icon size natively (each supersampled for its own resolution) instead of shrinking one 256px
# image down to 16px - a detailed mark shrunk that far turns muddy, which is why the taskbar icon looked poor.
# Windows scales the nearest .ico size when the one it needs is missing, which blurs it: hence 20 and 40.
images = sorted((icon_art.app_icon(s, bold=s <= 32, tiny=s <= 20) for s in SIZES), key=lambda im: im.size[0])
images[-1].save(ASSETS / "lockdown.ico", format="ICO", sizes=[(s, s) for s in SIZES],
                append_images=images[:-1])
images[-1].save(ASSETS / "lockdown.png")
print("wrote", ASSETS / "lockdown.ico", "and lockdown.png")
