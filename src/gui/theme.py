"""Design tokens from design/Lockdown Dashboard & Screen Time.dc.html: colours as (light, dark) pairs, fonts,
and the customtkinter defaults built from them. Status colours: blocked now = red, allowed now = green."""
import ctypes

import customtkinter as ctk

from paths import ASSETS

APP_ICON = ASSETS / "lockdown.ico"   # the Lockdown logo (windows, notifications)

# Round 2 tokens (design/Lockdown Round 2.dc.html): (light, dark) pairs.
BG = ("#EFF1F4", "#101418")
SIDEBAR = ("#E3E6EA", "#0B0E12")
SURFACE = ("#FFFFFF", "#181D23")
SURFACE2 = ("#F1F3F6", "#202730")
NAV_ACTIVE = ("#FFFFFF", "#202730")   # selected sidebar item
BORDER = ("#D2D7DE", "#2B333D")
TRACK = ("#AEB6C0", "#2B333D")       # switch "off" track (light fix 7b) / control track
TEXT = ("#14181D", "#E9EDF2")
MUTED = ("#5E6874", "#8D9AA8")
ACCENT = ("#DB5126", "#DB5126")
ACCENT_PRESS = ("#B33D18", "#B33D18")
SUCCESS = ("#1E7A46", "#27AE60")
WARNING = ("#B37514", "#E2A32B")
DANGER = ("#C0392B", "#E05A44")
INFO = ("#2F6FEB", "#4E8FD1")        # emergency unlock
NEUTRAL = ("#A8AFB7", "#5C6875")     # neutral category in charts
BAR = ("#C9CED4", "#3C4753")         # chart bars
BAR_OVER = ("#EBB49E", "#8C4A31")    # a day over the daily goal
HEAT = (("#EFEFEF", "#F6D9CD", "#EEB79E", "#E4886A", "#DB5126"),
        ("#20262E", "#4A2A1C", "#8A3F20", "#C04A22", "#DB5126"))
WHITE = ("#FFFFFF", "#FFFFFF")

# ---- your theme + accent colour (Settings > Appearance). Read here, before any widget exists, because colours are
# fixed when widgets are made - so a new theme / accent applies after a restart (light / dark switch at once).
THEME_KEY, ACCENT_KEY = "ui.theme", "ui.accent"
THEMES = {"Dark": "dark", "AMOLED": "amoled", "Light": "light", "Match Windows": "system"}
MODES = {"dark": "dark", "amoled": "dark", "light": "light", "system": "system"}   # theme -> customtkinter mode
ACCENTS = {"Orange": "#DB5126", "Red": "#D1342F", "Pink": "#D63F8C", "Purple": "#7C4DDB", "Blue": "#2F6FEB",
           "Teal": "#12948A", "Green": "#23945A", "Yellow": "#C99A0E"}


def _saved(key: str) -> str | None:
    import sqlite3
    from paths import DB_PATH
    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1) as con:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _mix(a: str, b: str, t: float) -> str:
    """Colour a..b at t (0-1)."""
    ca, cb = (int(a[i:i + 2], 16) for i in (1, 3, 5)), (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb))


THEME = _saved(THEME_KEY) or {"light": "light", "system": "system"}.get(_saved("ui.appearance") or "", "dark")
if THEME == "amoled":   # pure black for OLED screens
    BG, SIDEBAR = (BG[0], "#000000"), (SIDEBAR[0], "#000000")
    SURFACE, SURFACE2, NAV_ACTIVE = (SURFACE[0], "#0B0B0C"), (SURFACE2[0], "#18181A"), (NAV_ACTIVE[0], "#18181A")
    BORDER, TRACK = (BORDER[0], "#26262A"), (TRACK[0], "#1C1C1F")
ACCENT_HEX = _saved(ACCENT_KEY) or ACCENTS["Orange"]
if ACCENT_HEX != ACCENTS["Orange"] and len(ACCENT_HEX) == 7:   # (orange keeps the design's hand-picked shades)
    ACCENT = (ACCENT_HEX, ACCENT_HEX)
    ACCENT_PRESS = (_mix(ACCENT_HEX, "#000000", 0.2),) * 2
    HEAT = (tuple(_mix("#EFEFEF", ACCENT_HEX, t) for t in (0, 0.25, 0.5, 0.75, 1)),
            tuple(_mix(SURFACE2[1], ACCENT_HEX, t) for t in (0, 0.25, 0.5, 0.8, 1)))
    BAR_OVER = (_mix("#FFFFFF", ACCENT_HEX, 0.4), _mix("#000000", ACCENT_HEX, 0.6))

# Status (blocked = red, allowed = green - the user's choice over the design's green "blocked")
BLOCKED, ALLOWED, PENDING = DANGER, SUCCESS, WARNING
CATEGORY_COLORS = {"productive": SUCCESS, "neutral": MUTED, "distracting": DANGER}

# Secondary buttons: a light-grey fill in light mode (so they don't vanish on white cards - fix 7b), a plain
# outline in dark. Border is darker than the card edge in light mode for contrast.
# Light mode: the design's Secondary button (grey #F1F3F6 fill + #B9C0C9 border) so it never becomes a white box
# on a white card. Dark mode: the card colour as fill = visually border-only on cards (the design's dark look).
OUTLINE = {"fg_color": ("#F1F3F6", SURFACE[1]), "border_width": 1, "border_color": ("#B9C0C9", BORDER[1]),
           "text_color": TEXT, "hover_color": ("#E4E7EB", SURFACE2[1])}
SECONDARY = {"fg_color": ("#F1F3F6", SURFACE2[1]), "text_color": TEXT, "hover_color": ("#E4E7EB", BORDER[1])}

FONT_FILES = ["Inter-Regular.otf", "Inter-Medium.otf", "Inter-SemiBold.otf", "Inter-Bold.otf",
              "BarlowCondensed-SemiBold.ttf", "BarlowCondensed-Bold.ttf", "BarlowCondensed-ExtraBold.ttf"]
BODY = "Inter"
BODY_SEMI = "Inter Semi Bold"
DISPLAY = "Barlow Condensed"                # bold weight = Barlow Condensed Bold
DISPLAY_HEAVY = "Barlow Condensed ExtraBold"


def pick(color) -> str:
    """The colour for the current appearance mode (for plain Tk widgets like Canvas)."""
    if isinstance(color, (tuple, list)):
        return color[1] if ctk.get_appearance_mode() == "Dark" else color[0]
    return color


def load_fonts():
    """Make the bundled fonts available to this process only (before any Tk font is created)."""
    for name in FONT_FILES:
        path = ASSETS / "fonts" / name
        if path.exists():
            ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)   # FR_PRIVATE


# ---------- fonts (need a Tk root) ----------

def page_title():
    return ctk.CTkFont(DISPLAY, 30, "bold")


def numeral(size: int = 32):
    return ctk.CTkFont(DISPLAY, size, "bold")


def card_title():
    return ctk.CTkFont(BODY_SEMI, 14)


def body(size: int = 13, weight: str = "normal"):
    return ctk.CTkFont(BODY, size, weight)


def semi(size: int = 13):
    return ctk.CTkFont(BODY_SEMI, size)


def eyebrow():
    return ctk.CTkFont(BODY_SEMI, 10)


_BUTTON_FONT = None


def _button_font():
    """One shared semibold font for every button (design 3m: 600 12.5px); made lazily once a Tk root exists."""
    global _BUTTON_FONT
    if _BUTTON_FONT is None:
        _BUTTON_FONT = ctk.CTkFont(BODY_SEMI, 13)
    return _BUTTON_FONT


def apply():
    """customtkinter defaults from the tokens. Call before any widget is created."""
    load_fonts()
    # Design plate 3m switches: a 34x18 pill with the 14px knob INSIDE it (customtkinter draws the knob at the full
    # track height, which made ours look thin). gui/switch.py draws it the design's way, as a drop-in.
    from gui.switch import Switch
    ctk.CTkSwitch = Switch
    if not getattr(ctk.CTkButton, "_lockdown_sized", False):
        # Design plate 3m: buttons are padding 9px 18px, 600 12.5px -> about 34px tall with a semibold label.
        # Buttons that pass their own height / font (small x buttons, segmented pills) keep them.
        _orig_button_init = ctk.CTkButton.__init__

        def _button_init(self, *a, **kw):
            kw.setdefault("height", 34)
            if "font" not in kw:
                kw["font"] = _button_font()
            _orig_button_init(self, *a, **kw)
        ctk.CTkButton.__init__ = _button_init
        ctk.CTkButton._lockdown_sized = True
    # every pop-up window gets the Lockdown logo (customtkinter would put its own icon there after 200 ms)
    ctk.CTkToplevel._windows_set_titlebar_icon = lambda self: self.iconbitmap(str(APP_ICON))
    ctk.set_default_color_theme("dark-blue")
    t = ctk.ThemeManager.theme
    t["CTk"]["fg_color"] = list(BG)
    t["CTkToplevel"]["fg_color"] = list(BG)
    t["CTkFrame"].update(fg_color=list(SURFACE), top_fg_color=list(SURFACE2), border_color=list(BORDER),
                         corner_radius=4)
    t["CTkButton"].update(fg_color=list(ACCENT), hover_color=list(ACCENT_PRESS), border_color=list(BORDER),
                          text_color=list(WHITE), text_color_disabled=list(MUTED), corner_radius=2)
    t["CTkLabel"]["text_color"] = list(TEXT)
    t["CTkEntry"].update(fg_color=list(BG), border_color=list(BORDER), text_color=list(TEXT),
                         placeholder_text_color=list(MUTED), corner_radius=2, border_width=1)
    t["CTkCheckBox"].update(fg_color=list(ACCENT), border_color=list(NEUTRAL), hover_color=list(ACCENT_PRESS),
                            checkmark_color=list(WHITE), text_color=list(TEXT), text_color_disabled=list(MUTED),
                            corner_radius=2, border_width=2)
    # The 2px pill ring is a visible grey so the white knob never melts into a white card (design 3m: the knob
    # needs an edge in light mode - CTk can't edge the knob itself, so the ring around it does that job).
    t["CTkSwitch"].update(fg_color=list(TRACK), progress_color=list(ACCENT), button_color=list(WHITE),
                          button_hover_color=list(WHITE), text_color=list(TEXT),
                          border_color=["#9AA3AE", TRACK[1]])
    t["CTkRadioButton"].update(fg_color=list(ACCENT), border_color=list(NEUTRAL), hover_color=list(ACCENT_PRESS),
                               text_color=list(TEXT))
    t["CTkSegmentedButton"].update(fg_color=list(SURFACE2), selected_color=list(ACCENT),
                                   selected_hover_color=list(ACCENT_PRESS), unselected_color=list(SURFACE2),
                                   unselected_hover_color=list(BORDER), text_color=list(TEXT),
                                   text_color_disabled=list(MUTED), corner_radius=2)
    t["CTkOptionMenu"].update(fg_color=list(SURFACE2), button_color=list(SURFACE2), button_hover_color=list(BORDER),
                              text_color=list(TEXT), corner_radius=2)
    t["CTkComboBox"].update(fg_color=list(BG), border_color=list(BORDER), button_color=list(SURFACE2),
                            button_hover_color=list(BORDER), text_color=list(TEXT))
    t["CTkScrollbar"].update(button_color=list(BORDER), button_hover_color=list(NEUTRAL))
    t["CTkProgressBar"].update(fg_color=list(TRACK), progress_color=list(ACCENT), border_color=list(BORDER))
    t["CTkSlider"].update(fg_color=list(TRACK), progress_color=list(ACCENT), button_color=list(ACCENT),
                          button_hover_color=list(ACCENT_PRESS))
    t["CTkTextbox"].update(fg_color=list(BG), border_color=list(BORDER), text_color=list(TEXT))
    t["CTkScrollableFrame"]["label_fg_color"] = list(SURFACE2)
    t["DropdownMenu"].update(fg_color=list(SURFACE), hover_color=list(SURFACE2), text_color=list(TEXT))
    t["CTkFont"].update(family=BODY, size=13, weight="normal")


def icon(name: str, color, size: int = 18) -> ctk.CTkImage:
    """A white Lucide icon from assets/icons, tinted (light / dark variants)."""
    from PIL import Image
    src = Image.open(ASSETS / "icons" / f"{name}.png").convert("RGBA")

    def tint(c: str):
        img = Image.new("RGBA", src.size, c)
        img.putalpha(src.getchannel("A"))
        return img
    light, dark = color if isinstance(color, (tuple, list)) else (color, color)
    return ctk.CTkImage(tint(light), tint(dark), size=(size, size))
