"""The option switch (gui.components.Segmented) - Theme, Day / Week, Today / 7 days ... (0.84.1 redesign).

Its labels were drawn with a positive Tk font size - points, which Tk scales by the display's DPI - on top of the
app's own scaling, so Windows' display scaling counted twice: at 150 % the labels were twice the size of the text
around them and spilled out of their chips."""
from gui import components


class _Font:
    made: dict = {}

    def __init__(self, **kw):
        _Font.made = kw

    def measure(self, text: str) -> int:
        return 7 * len(text)


class _Canvas:
    def configure(self, **kw):
        self.size = kw


def _measured(monkeypatch, scale: float, values=("System", "Light", "Dark"), height=None):
    monkeypatch.setattr(components.tkfont, "Font", _Font)
    monkeypatch.setattr(components.ctk.ScalingTracker, "get_widget_scaling", lambda widget: scale, raising=False)
    seg = object.__new__(components.Segmented)      # (no Tk window needed to measure it)
    seg.values, seg.canvas = list(values), _Canvas()
    seg._height = height or components.Segmented.__init__.__defaults__[1]
    seg._measure()
    return seg


def test_labels_are_pixels_scaled_once(monkeypatch):
    seg = _measured(monkeypatch, 1.0)
    assert _Font.made["size"] == -13                 # negative: pixels, as every CTkFont is - the body text's 13 px
    seg = _measured(monkeypatch, 1.5)
    assert _Font.made["size"] == -20                 # 150 %: 13 px x 1.5, and Tk adds nothing on top


def test_it_lines_up_with_the_entries_and_gives_every_label_room(monkeypatch):
    seg = _measured(monkeypatch, 1.0, values=("On", "Allowed"))
    assert seg._size[1] == 28                        # the entries beside it are 28 px tall
    narrow, wide = seg._seg
    assert narrow == components.Segmented.MIN        # a short label still gets a chip worth aiming at
    assert wide == 7 * len("Allowed") + 2 * components.Segmented.SIDE
    assert _measured(monkeypatch, 1.0, height=26)._size[1] == 26   # a caller's own height still wins


def test_clicks_land_on_the_chip_under_the_mouse(monkeypatch):
    seg = _measured(monkeypatch, 1.0, values=("Day", "Week", "Month"))
    (a0, a1), (b0, b1), (c0, c1) = seg._spans()
    assert seg._at(a0 + 1) == "Day" and seg._at(b1 - 1) == "Week" and seg._at(c0 + 1) == "Month"
    # no dead strips under the hand cursor: the track's padding and the gaps go to the nearest chip
    assert seg._at(0) == "Day" and seg._at(seg._size[0]) == "Month"
    assert seg._at((a1 + b0) / 2 - 0.1) == "Day" and seg._at((a1 + b0) / 2 + 0.1) == "Week"
    assert seg._at(-1) is None and seg._at(seg._size[0] + 1) is None   # off the track


def test_text_on_the_accent_stays_readable():
    assert components.on_accent("#DB5126") == "#FFFFFF"     # Orange (the default): white
    assert components.on_accent("#2F6FEB") == "#FFFFFF"     # Blue
    assert components.on_accent("#C99A0E") == "#141414"     # Yellow: white was 2.6:1, so dark text
    assert components.on_accent("#F5F5F5") == "#141414"
