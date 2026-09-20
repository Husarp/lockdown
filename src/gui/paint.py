"""Anti-aliased widget art.

Tk's own canvas shapes (create_oval, create_rectangle) have hard, stair-stepped edges - that is what made the
switch knobs and the small segmented tabs look pixelated next to everything else. The few widgets we draw
ourselves go through here instead: Pillow draws them at SS times the size and the result is scaled back down,
the same trick the charts and the app icon already use.

Coordinates are in the widget's own pixels (screen px, i.e. already multiplied by the display scaling)."""
from PIL import Image, ImageDraw, ImageTk

SS = 4


class Art:
    """Art(width, height, background) -> draw on it -> .photo() for a Tk image. Keep a reference to the image
    for as long as it is on a canvas, or Tk shows nothing (Python would free it)."""

    def __init__(self, width: float, height: float, bg: str):
        self.w, self.h = max(1, round(width)), max(1, round(height))
        self.img = Image.new("RGB", (self.w * SS, self.h * SS), bg)
        self.draw = ImageDraw.Draw(self.img)

    def rrect(self, x0, y0, x1, y1, radius, fill=None, outline=None, width: float = 1.0):
        box = [x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1]
        radius = max(0.0, min(radius * SS, (box[2] - box[0]) / 2, (box[3] - box[1]) / 2))
        self.draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=max(1, round(width * SS)))

    def ellipse(self, x0, y0, x1, y1, fill=None, outline=None, width: float = 1.0):
        self.draw.ellipse([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], fill=fill, outline=outline,
                          width=max(1, round(width * SS)))

    def photo(self) -> ImageTk.PhotoImage:
        return ImageTk.PhotoImage(self.img.resize((self.w, self.h), Image.LANCZOS))
