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
