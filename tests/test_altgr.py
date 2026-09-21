"""Polish letters are typed with AltGr, which Windows reports as Ctrl+Alt. Tk's own "Control+key does nothing"
rule then swallowed them, so ą ć ę ł ń ó ś ź ż never reached any box in the app."""
import pytest

from gui import shortcuts


class FakeBox:
    """Just enough of a tk.Entry for the handler."""

    def __init__(self, text="", selected=None):
        self.text, self.selected = text, selected

    def selection_present(self):
        return self.selected is not None

    def delete(self, first, last):
        assert (first, last) == ("sel.first", "sel.last")
        start, end = self.selected
        self.text = self.text[:start] + self.text[end:]
        self.selected = None

    def insert(self, where, char):
        assert where == "insert"
        self.text += char


class FakeEvent:
    def __init__(self, char, widget):
        self.char, self.widget = char, widget
        self.state = 0x0004 | 0x20000      # Control + Alt, i.e. AltGr


@pytest.mark.parametrize("char", list("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ€"))
def test_altgr_letters_are_typed_in(char):
    box = FakeBox()
    assert shortcuts._altgr(FakeEvent(char, box)) == "break"
    assert box.text == char


@pytest.mark.parametrize("char", ["\x16", "\x01", "\x1a", ""])
def test_real_control_shortcuts_are_left_alone(char):
    """Ctrl+V / Ctrl+A / Ctrl+Z carry a control character, never a printable one - they must not be typed in
    (and pasting must stay blocked in the Anti-Bypass challenge)."""
    box = FakeBox()
    assert shortcuts._altgr(FakeEvent(char, box)) is None
    assert box.text == ""


def test_it_replaces_what_you_had_selected():
    box = FakeBox("hello", selected=(0, 5))
    shortcuts._altgr(FakeEvent("ż", box))
    assert box.text == "ż"
