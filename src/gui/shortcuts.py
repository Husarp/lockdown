"""Keyboard shortcuts for the whole app (installed once on the Tk root, so every window and text box gets them):
Esc closes a pop-up window (like its X; the bedtime / forced-break screens set `escape_closes = False`),
and in any text box: Ctrl+Z / Ctrl+Y undo / redo, Ctrl+Backspace / Ctrl+Delete delete a word, Ctrl+A selects all."""
import ctypes
import re

MAX_UNDO = 100
_undo: dict[str, list[str]] = {}   # text box -> its earlier values (last = current)
_redo: dict[str, list[str]] = {}


def install(root):
    root.bind_all("<Escape>", lambda e: _escape(root, e), add="+")
    root.bind_class("Entry", "<Control-Key>", _altgr, add="+")
    root.bind_class("Text", "<Control-Key>", _altgr, add="+")
    root.bind_class("Entry", "<FocusIn>", _remember, add="+")
    root.bind_class("Entry", "<KeyRelease>", _remember, add="+")
    for seq, fn in (("<Control-z>", _undo_step), ("<Control-Z>", _undo_step), ("<Control-y>", _redo_step),
                    ("<Control-Y>", _redo_step), ("<Control-BackSpace>", _delete_word_left),
                    ("<Control-Delete>", _delete_word_right), ("<Control-a>", _select_all), ("<Control-A>", _select_all)):
        root.bind_class("Entry", seq, fn)


def _altgr(event):
    """Windows sends AltGr as Ctrl+Alt, and Tk's own "<Control-Key> does nothing" rule then swallows the key -
    so ą ć ę ł ń ó ś ź ż (and other AltGr letters) never reached any box. Type them in instead.
    A real Ctrl shortcut carries a control character ("\\x16" for Ctrl+V), never a printable one, so this
    can't swallow Ctrl+C / Ctrl+V / Ctrl+A."""
    if not event.char or not event.char.isprintable():
        return None
    widget = event.widget
    try:
        if widget.tag_ranges("sel") if hasattr(widget, "tag_ranges") else widget.selection_present():
            widget.delete("sel.first", "sel.last")
        widget.insert("insert", character(event))
    except Exception:
        return None
    return "break"


def _ansi_codepage() -> str:
    """The codepage Windows uses for single-byte text here - cp1250 on a Polish system, cp1252 on a Western one."""
    try:
        return f"cp{ctypes.windll.kernel32.GetACP()}"
    except Exception:
        return "cp1252"


ANSI = _ansi_codepage()


def character(event) -> str:
    """The character a key really produced. Tk hands it over as one byte in the keyboard's codepage, and
    tkinter reads that byte as Western European (cp1252): on a Polish system e-ogonek (0xEA in cp1250) arrives
    as a circumflex e, z-dot as an inverted question mark, l-stroke as a superscript 3, s-acute as an oe
    ligature. Anything that still fits in one cp1252 byte is put back through the real codepage; a character
    that doesn't fit can't have come from this mix-up, so it is left alone."""
    char = event.char
    if not char or char.isascii() or ANSI == "cp1252":
        return char
    try:
        return char.encode("cp1252").decode(ANSI)
    except (UnicodeEncodeError, UnicodeDecodeError, LookupError):
        return char


def _escape(root, event):
    try:
        top = event.widget.winfo_toplevel()
    except (AttributeError, KeyError):   # (e.g. a dropdown menu)
        return
    if top is root or not getattr(top, "escape_closes", True):
        return
    close = top.protocol("WM_DELETE_WINDOW")   # the window's own "X" (e.g. Cancel), if it has one
    if close:
        top.tk.eval(close)
    else:
        top.destroy()


def _set(entry, value: str):
    entry.delete(0, "end")
    entry.insert(0, value)


def _remember(event):
    entry = event.widget
    try:
        value = entry.get()
    except Exception:
        return
    history = _undo.setdefault(str(entry), [value])
    if history[-1] != value:
        history.append(value)
        del history[:-MAX_UNDO]
        _redo.pop(str(entry), None)   # typing after an undo: the redo steps are gone


def _undo_step(event):
    history = _undo.get(str(event.widget), [])
    if len(history) > 1:
        _redo.setdefault(str(event.widget), []).append(history.pop())
        _set(event.widget, history[-1])
    return "break"


def _redo_step(event):
    steps = _redo.get(str(event.widget), [])
    if steps:
        value = steps.pop()
        _undo.setdefault(str(event.widget), []).append(value)
        _set(event.widget, value)
    return "break"


def _delete_word_left(event):
    entry = event.widget
    if entry.selection_present():
        entry.delete("sel.first", "sel.last")
        return "break"
    end = entry.index("insert")
    start = len(re.sub(r"\w*\W*$", "", entry.get()[:end]))   # back over spaces / punctuation, then a word
    entry.delete(start, end)
    return "break"


def _delete_word_right(event):
    entry = event.widget
    start = entry.index("insert")
    rest = entry.get()[start:]
    entry.delete(start, start + len(re.match(r"\W*\w*", rest).group()))
    return "break"


def _select_all(event):
    event.widget.select_range(0, "end")
    event.widget.icursor("end")
    return "break"
