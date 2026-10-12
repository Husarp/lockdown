"""A group's icon and colour - the phone's GroupLook.kt in Python. Looks only: nothing here changes what a group blocks, so
choosing them is always free (never part of what antibypass.draft_changes / group_looser compare).

The pickable icons are the KEYS below (the page's picker, ui/js/groupicons.js, lists the same keys - a test keeps the two
lists equal); the pickable colours are HUES (CSS tokens --hue-<name> in ui/css/tokens.css). A group stores what was picked
(None = automatic); `look()` is what to show: the picked one, or else the icon its NAME suggests and a colour from its id."""
import re

KEYS = (
    "moon", "bed", "sunrise", "alarm", "calendar", "home", "family", "pets", "coffee", "food",
    "phone", "game", "social", "video", "chat", "music", "news", "web", "photo", "film", "tv", "mic", "headphones", "mail",
    "casino", "puzzle", "trophy", "art",
    "work", "office", "laptop", "code", "pen", "school", "library", "book", "idea", "focus", "target", "timer",
    "shopping", "bag", "money", "card", "gift",
    "sport", "run", "pill", "pulse", "lotus", "heart", "mountain", "tree",
    "car", "bike", "plane", "map",
    "star", "bolt", "lock", "shield", "sparkles", "folder",
)
DEFAULT_KEY = "folder"

HUES = ("red", "orange", "amber", "green", "teal", "blue", "indigo", "violet", "pink", "slate")
# The automatic colours: never red or slate, so a group doesn't look blocked or paused unless it was picked so.
AUTO_HUES = ("orange", "amber", "green", "teal", "blue", "indigo", "violet", "pink")

# Name words (English and Polish stems) -> icon, first row that matches wins. A stem under 4 letters (and WHOLE) only matches
# the whole word: "bet" isn't "Better", "sen" isn't "Senior", "gra" isn't "Grafika".
WORDS = (
    ("moon", ("night", "bed", "bedtime", "sleep", "evening", "noc", "nocn", "sen")),
    ("game", ("game", "gaming", "play", "gry", "gra")),
    ("social", ("social", "insta", "tiktok", "facebook", "snap", "społ")),
    ("video", ("video", "youtube", "netflix", "tv", "film", "movie", "stream", "wideo")),
    ("chat", ("chat", "messag", "whatsapp", "discord", "czat")),
    ("music", ("music", "spotify", "muzyk")),
    ("news", ("news", "wiadomo")),
    ("shopping", ("shop", "amazon", "allegro", "zakup")),
    ("casino", ("bet", "betting", "casino", "gambl", "hazard")),
    ("work", ("work", "praca")),
    ("school", ("study", "school", "learn", "nauka", "szkoł")),
    ("book", ("read", "book")),
)
WHOLE = frozenset({"read"})                 # stems of 4 letters or more that still only match the whole word ("Ready")
_SPLIT = re.compile(r"[\W_]+")


def auto_icon(name: str) -> str:
    """The icon a name suggests: a word of it (lower case) that starts with one of WORDS - by word start, not anywhere, so
    "Instagram" isn't a game ("gra"); a short stem only as the whole word. Anything else: a folder."""
    words = [w for w in _SPLIT.split((name or "").lower()) if w]

    def hit(word: str, stem: str) -> bool:
        return word == stem if len(stem) < 4 or stem in WHOLE else word.startswith(stem)

    return next((key for key, stems in WORDS if any(hit(w, s) for w in words for s in stems)), DEFAULT_KEY)


def auto_hue(group_id) -> str:
    """The colour of a group that picked none: from its id, so it stays the same (and neighbours differ)."""
    return AUTO_HUES[group_id % len(AUTO_HUES)] if isinstance(group_id, int) else AUTO_HUES[0]


def look(group: dict) -> dict:
    """{icon, color, icon_picked, color_picked} of a group as the page shows it. An unknown key / hue (from a newer copy)
    counts as not picked."""
    icon, color = group.get("icon"), group.get("color")
    icon_picked, color_picked = icon in KEYS, color in HUES
    return {"icon": icon if icon_picked else auto_icon(group.get("name") or ""),
            "color": color if color_picked else auto_hue(group.get("id")),
            "icon_picked": icon_picked, "color_picked": color_picked}
