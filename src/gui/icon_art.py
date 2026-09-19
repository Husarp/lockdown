"""The Lockdown mark - a shield with a padlock - drawn with Pillow (design/Lockdown Round 2.dc.html, plate 3n).

Used for the app icon (assets/lockdown.ico / .png, built by scripts/make_icons.py) and the tray icon, which
recolours the shield for its three states: green = blocking enforced, yellow = a mode is on, red = service down.
All geometry is in a 64-unit space and scaled to the requested size.
"""
from PIL import Image, ImageDraw

SS = 4   # supersample, then shrink for smooth edges

ACCENT, ACCENT_DARK = "#DB5126", "#B33D18"
GREEN, YELLOW, RED = "#27AE60", "#E2A32B", "#E05A44"
INK = "#0B0E12"


def _bezier(p0, p1, p2, p3, n=26):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def _shield():
    """Full shield outline points (M32,4 L10,11.5 V29.7 C..32,60 S..54,29.7 V11.5 Z), in the 64-unit space."""
    pts = [(32, 4), (10, 11.5), (10, 29.7)]
    pts += _bezier((10, 29.7), (10, 46.2), (32, 60), (32, 60))
    pts += _bezier((32, 60), (32, 60), (54, 46.2), (54, 29.7))
    pts += [(54, 11.5)]
    return pts


def _left_half():
    """The darker left half: left outline down to the point, then straight up the centre."""
    pts = [(32, 4), (10, 11.5), (10, 29.7)]
    pts += _bezier((10, 29.7), (10, 46.2), (32, 60), (32, 60))
    pts += [(32, 4)]
    return pts


def mark(size: int, shield: str, shield_dark: str | None, lock: str, keyhole: str,
         bold: bool = False) -> Image.Image:
    """The mark at `size` px. shield_dark None = a flat single-tone shield (used for the tray). bold = a simplified
    version for tiny sizes (e.g. the 16px title-bar icon): the shield fills the canvas and the keyhole is dropped,
    so it stays crisp instead of turning to mud."""
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if bold:   # map the shield's bounding box to (almost) fill the canvas
        bx0, by0, bx1, by1 = 10, 4, 54, 60
        m = s * 0.04
        k = min((s - 2 * m) / (bx1 - bx0), (s - 2 * m) / (by1 - by0))
        offx, offy = (s - (bx1 - bx0) * k) / 2 - bx0 * k, (s - (by1 - by0) * k) / 2 - by0 * k
    else:
        k, offx, offy = s / 64, 0.0, 0.0

    def P(x, y):
        return (x * k + offx, y * k + offy)

    d.polygon([P(x, y) for x, y in _shield()], fill=shield)
    if shield_dark:
        d.polygon([P(x, y) for x, y in _left_half()], fill=shield_dark)
    d.rounded_rectangle([P(22, 30), P(42, 45)], radius=2.5 * k, fill=lock)   # padlock body
    w = max(1, round(3.6 * k))
    d.arc([*P(26.5, 20), *P(37.5, 31)], 180, 360, fill=lock, width=w)        # shackle
    d.line([P(26.5, 25.5), P(26.5, 30.2)], fill=lock, width=w)
    d.line([P(37.5, 25.5), P(37.5, 30.2)], fill=lock, width=w)
    if not bold:
        d.rounded_rectangle([P(30.6, 35), P(33.4, 41)], radius=1.4 * k, fill=keyhole)   # keyhole
    return img.resize((size, size), Image.LANCZOS)


def app_icon(size: int, bold: bool = False) -> Image.Image:
    """The full-colour app icon: orange two-tone shield, white padlock (bold = simplified for tiny sizes)."""
    return mark(size, ACCENT, None if bold else ACCENT_DARK, "#FFFFFF", ACCENT_DARK, bold=bold)


def tray_icon(state: str, size: int = 64) -> Image.Image:
    """Tray mark: a flat shield in the state colour with a dark padlock. state: green / yellow / red."""
    color = {"green": GREEN, "yellow": YELLOW, "red": RED}[state]
    return mark(size, color, None, INK, color)
