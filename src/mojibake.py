"""Text typed before 0.70.1, when a letter typed with AltGr arrived as the wrong one.

Tk handed the character over as one byte in the keyboard's codepage and tkinter read that byte as Western
European, so on a Polish keyboard "pamiętaj" was saved as "pamiêtaj". New typing is fine (gui/shortcuts), but
whatever was saved before stayed wrong, so it is put back once - only when the text carries a character that
practically cannot be typed on purpose here (a superscript 3, an inverted question mark, an oe ligature...),
which is what makes the mix-up recognisable.
Standard library only.
"""
import ctypes
import json

# cp1250 bytes 0x9C, 0xB3, 0xB9, 0xBF, 0x9F ... read as cp1252. A real sentence doesn't contain these.
TELLTALE = "³¹¿œŸ§±"


LOCALE_ANSI_CP = 0x1004


def _codepage_of(langid: int) -> str:
    buffer = ctypes.create_unicode_buffer(16)
    ctypes.windll.kernel32.GetLocaleInfoW(langid, LOCALE_ANSI_CP, buffer, 16)
    return f"cp{int(buffer.value)}" if buffer.value.isdigit() else "cp1252"


def keyboard_codepage() -> str:
    """The codepage of the keyboard layout you are typing with - cp1250 for the Polish one. NOT the system's
    (GetACP): an English Windows says cp1252 while the Polish layout still produces cp1250 bytes, which is
    exactly the case this whole mix-up happens in."""
    try:
        return _codepage_of(ctypes.windll.user32.GetKeyboardLayout(0) & 0xFFFF)
    except Exception:
        return "cp1252"


def typing_codepage() -> str:
    """For putting old text back: the codepage of an installed layout that isn't Western European, since that
    is the only kind this could have come from. cp1252 (no repair) when there is no such layout."""
    try:
        user32 = ctypes.windll.user32
        count = user32.GetKeyboardLayoutList(0, None)
        layouts = (ctypes.c_void_p * count)()
        user32.GetKeyboardLayoutList(count, layouts)
        pages = [_codepage_of((h or 0) & 0xFFFF) for h in layouts]
        return next((p for p in pages if p != "cp1252"), "cp1252")
    except Exception:
        return "cp1252"


def ansi_codepage() -> str:
    return typing_codepage()


def looks_mangled(text: str, ansi: str | None = None) -> bool:
    return bool(text) and (ansi or ansi_codepage()) != "cp1252" and any(c in TELLTALE for c in text)


def repair(text: str, ansi: str | None = None) -> str:
    """The text as it was typed, or unchanged if it doesn't look mangled (or can't be put back)."""
    ansi = ansi or ansi_codepage()
    if not looks_mangled(text, ansi):
        return text
    try:
        return text.encode("cp1252").decode(ansi)
    except (UnicodeEncodeError, UnicodeDecodeError, LookupError):
        return text


def repair_json(text: str, ansi: str) -> str:
    """The settings are stored as JSON, so the mangled letters sit inside it, escaped. Put every string in it
    back and write the JSON out again - or the text itself if it isn't JSON."""
    try:
        data = json.loads(text)
    except ValueError:
        return repair(text, ansi)

    def walk(value):
        if isinstance(value, str):
            return repair(value, ansi)
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value
    fixed = walk(data)
    return json.dumps(fixed) if fixed != data else text


REPAIRED_KEY = "text.repaired"     # the one-off pass below has run


def repair_saved(db, ansi: str | None = None) -> list[str]:
    """One-off: put back the reminder texts, names and phrases that were saved mangled. Returns what changed."""
    if db.get_setting(REPAIRED_KEY, "") == "1":
        return []
    ansi = ansi or ansi_codepage()
    changed = []
    for key in ("reminders.sleep", "reminders.break", "reminders.custom", "antibypass", "words"):
        value = db.get_setting(key, "")
        fixed = repair_json(value, ansi)
        if fixed != value:
            db.set_setting(key, fixed)
            changed.append(key)
    for item in db.list_items():
        fixed = repair(item["display_name"], ansi)
        if fixed != item["display_name"]:
            db.update_item(item["id"], fixed, item["target"].split(), item["notify"], item["rules"],
                           item.get("block_type"), item.get("app_path"), bool(item.get("disabled")))
            changed.append(fixed)
    for group in db.list_groups():
        fixed = repair(group["name"], ansi)
        if fixed != group["name"]:
            db.update_group(group["id"], fixed, group["rules"], group["members"], bool(group.get("disabled")))
            changed.append(fixed)
    db.set_setting(REPAIRED_KEY, "1")
    return changed
