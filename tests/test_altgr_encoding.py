"""AltGr letters arrived in the box, but as the wrong letter: Tk hands the character over as one byte in the
keyboard's codepage and tkinter reads that byte as Latin-1 (the user's own screenshot: "pamiêtaj" for
"pamiętaj"). Every box in the app goes through this, since the fix is a class binding on Entry and Text."""
import pytest

from gui import shortcuts


class Event:
    def __init__(self, char):
        self.char = char


MANGLED = "Jest {time}, pamiêtaj o zdrowiu, zawsze ¿a³ujesz ¿ê siê nie wyspa³eœ i zawali³eœ kolejny dzieñ"
MEANT = "Jest {time}, pamiętaj o zdrowiu, zawsze żałujesz żę się nie wyspałeś i zawaliłeś kolejny dzień"


@pytest.mark.parametrize("arrived, meant", list(zip("êóñ¿³œ¹æŸ", "ęóńżłśąćź")))
def test_the_letter_that_arrives_is_put_back(arrived, meant, monkeypatch):
    monkeypatch.setattr(shortcuts.mojibake, "keyboard_codepage", lambda: "cp1250")
    assert shortcuts.character(Event(arrived)) == meant


def test_the_whole_sentence_from_the_screenshot(monkeypatch):
    monkeypatch.setattr(shortcuts.mojibake, "keyboard_codepage", lambda: "cp1250")
    assert "".join(shortcuts.character(Event(c)) for c in MANGLED) == MEANT


def test_plain_letters_and_shortcuts_are_untouched(monkeypatch):
    monkeypatch.setattr(shortcuts.mojibake, "keyboard_codepage", lambda: "cp1250")
    for char in ("a", "Z", " ", "7", "{", "", "\x16"):
        assert shortcuts.character(Event(char)) == char


def test_a_character_that_was_never_mangled_is_left_alone(monkeypatch):
    """One that doesn't fit in a single Latin-1 byte can't have come from this mix-up."""
    monkeypatch.setattr(shortcuts.mojibake, "keyboard_codepage", lambda: "cp1250")
    for char in ("ę", "ł", "€", "ß"):
        assert shortcuts.character(Event(char)) == char


def test_a_western_windows_is_unaffected(monkeypatch):
    monkeypatch.setattr(shortcuts.mojibake, "keyboard_codepage", lambda: "cp1252")
    for char in ("é", "ü", "ñ", "ç"):
        assert shortcuts.character(Event(char)) == char


class FakeEntry:
    """Just enough of a tk.Entry for the after-the-fact fix."""

    def __init__(self, text=""):
        self.text, self.cursor = text, len(text)

    def index(self, _where):
        return self.cursor

    def get(self):
        return self.text

    def delete(self, first, last):
        self.text = self.text[:first] + self.text[last:]
        self.cursor = first

    def insert(self, at, char):
        self.text = self.text[:at] + char + self.text[at:]
        self.cursor = at + len(char)

    def icursor(self, at):
        self.cursor = at


class TypedEvent:
    def __init__(self, char, widget):
        self.char, self.widget = char, widget


def test_a_letter_tk_typed_in_itself_is_swapped(monkeypatch):
    """Whichever path put it there - Tk's own insert, or ours - the right letter ends up in the box."""
    monkeypatch.setattr(shortcuts.mojibake, "keyboard_codepage", lambda: "cp1250")
    entry = FakeEntry("pami" + "ê")            # what Tk inserted
    shortcuts._fix_typed(TypedEvent("ê", entry))
    assert entry.text == "pamię" and entry.cursor == 5


def test_it_does_nothing_when_the_letter_is_already_right(monkeypatch):
    monkeypatch.setattr(shortcuts.mojibake, "keyboard_codepage", lambda: "cp1250")
    entry = FakeEntry("pamię")                 # our own handler already fixed it
    shortcuts._fix_typed(TypedEvent("ê", entry))
    assert entry.text == "pamię"


def test_plain_typing_is_untouched(monkeypatch):
    monkeypatch.setattr(shortcuts.mojibake, "keyboard_codepage", lambda: "cp1250")
    entry = FakeEntry("hello")
    shortcuts._fix_typed(TypedEvent("o", entry))
    assert entry.text == "hello"
