"""Theme constants: the settings keys, the theme and accent choices, and the window background per theme. Plain
Python - no customtkinter, PIL or tkinter - so the agent can read them without paying for those imports."""
import sqlite3

from paths import ASSETS

APP_ICON = ASSETS / "lockdown.ico"   # the Lockdown logo (windows, notifications)

THEME_KEY, ACCENT_KEY = "ui.theme", "ui.accent"
SIZE_KEY = "ui.scale"                # "auto", or how big everything is drawn as a percentage ("80")
THEMES = {"Dark": "dark", "AMOLED": "amoled", "Light": "light", "System": "system"}
ACCENTS = {"Orange": "#DB5126", "Red": "#D1342F", "Pink": "#D63F8C", "Purple": "#7C4DDB", "Blue": "#2F6FEB",
           "Teal": "#12948A", "Green": "#23945A", "Yellow": "#C99A0E"}

# The saved window choice of the removed "Try the new window" switch: Lockdown has only the new window now, and
# main.py deletes this key once (forget_engine_choice).
ENGINE_KEY = "ui.engine"

# The page background (design token BG) per theme - the web window is painted with it before the page loads.
BG_LIGHT, BG_DARK, BG_AMOLED = "#EFF1F4", "#101418", "#000000"
BACKGROUNDS = {"dark": BG_DARK, "amoled": BG_AMOLED, "light": BG_LIGHT}


def read_setting(key: str) -> str | None:
    """One saved setting by a plain read-only query: no Database object, no migrations. None when the data folder,
    the file or the setting is missing or unreadable."""
    import paths   # (looked up at call time: tests point DB_PATH somewhere else)
    try:
        with sqlite3.connect(f"file:{paths.DB_PATH}?mode=ro", uri=True, timeout=1) as con:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def system_is_light() -> bool:
    """Windows' "apps use light theme" choice (dark when it can't be read)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except (ImportError, OSError):
        return False


def saved_theme() -> str:
    """The saved theme name: "dark", "amoled", "light" or "system" (same rule as the old window)."""
    saved = read_setting(THEME_KEY)
    if saved in BACKGROUNDS or saved == "system":
        return saved
    return {"light": "light", "system": "system"}.get(read_setting("ui.appearance") or "", "dark")


def background_color() -> str:
    """The window background for the saved theme."""
    name = saved_theme()
    if name == "system":
        name = "light" if system_is_light() else "dark"
    return BACKGROUNDS[name]
