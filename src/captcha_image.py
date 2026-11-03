"""Small server-side captcha renderer. PNGs have no text, answer metadata or platform font dependency."""
import base64
import io
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from paths import ASSETS


@lru_cache(maxsize=1)
def _font():
    return ImageFont.truetype(str(ASSETS / "fonts" / "Inter-Bold.otf"), 76)


def render(text: str, level: int, rng=random) -> str:
    """A readable picture with increasing rotation, shear and noise. Draw at 2x for smooth edges."""
    width, height = len(text) * 88 + 48, 176
    image = Image.new("RGB", (width, height), (247, 249, 252))
    draw = ImageDraw.Draw(image)
    for _ in range((level + 1) * 55):
        x, y = rng.randrange(width), rng.randrange(height)
        draw.ellipse((x, y, x + 3, y + 3), fill=(177, 190, 207))

    for i, char in enumerate(text):
        glyph = Image.new("RGBA", (144, 144))
        ink = (rng.randint(25, 65), rng.randint(35, 75), rng.randint(55, 95), 255)
        ImageDraw.Draw(glyph).text((72, 72), char, font=_font(), anchor="mm", fill=ink)
        shear = rng.uniform(-1, 1) * (0.04, 0.10, 0.16)[level]
        glyph = glyph.transform(glyph.size, Image.Transform.AFFINE, (1, shear, -72 * shear, 0, 1, 0),
                                resample=Image.Resampling.BICUBIC)
        angle = rng.uniform(-1, 1) * (8, 15, 22)[level]
        glyph = glyph.rotate(angle, resample=Image.Resampling.BICUBIC)
        image.paste(glyph, (i * 88 - 4, 16 + rng.randint(-8 - level * 3, 8 + level * 3)), glyph)

    # Thin, lighter lines leave the dark character shapes readable even on Hard.
    for _ in range(level + 1):
        draw.line((0, rng.randrange(24, height - 24), width, rng.randrange(24, height - 24)),
                  fill=(139, 158, 181), width=2)
    image = image.resize((width // 2, height // 2), Image.Resampling.LANCZOS)
    png = io.BytesIO()
    image.save(png, format="PNG")
    return "data:image/png;base64," + base64.b64encode(png.getvalue()).decode("ascii")
