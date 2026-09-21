"""Steam games belong in Games, and a "Games" you made yourself before that category existed is the same
category - not a second entry in every menu."""
import json

from db import Database
from gui import categories
from importer import distracting

GAMES = ["terraria.exe", "undertale.exe", "peak.exe"]


def _db(tmp_path) -> Database:
    return Database(tmp_path / "t.db")


def test_a_steam_game_lands_in_games(tmp_path):
    db = _db(tmp_path)
    assert distracting.seed_games(db, GAMES) == 3
    assert db.categories()[("app", "terraria.exe")] == "games"


def test_games_seeded_as_distracting_by_an_older_version_move_over(tmp_path):
    db = _db(tmp_path)
    for exe in GAMES:
        db.set_category("app", exe, "distracting")          # what 0.4-0.70 did
    db.set_setting(distracting.SEEN_KEY, json.dumps(sorted(GAMES)))
    db.set_category("app", "undertale.exe", "productive")   # ... except this one, which you changed yourself

    distracting.seed_games(db, GAMES)
    saved = db.categories()
    assert saved[("app", "terraria.exe")] == "games"
    assert saved[("app", "peak.exe")] == "games"
    assert saved[("app", "undertale.exe")] == "productive"  # left alone


def test_the_move_happens_only_once(tmp_path):
    db = _db(tmp_path)
    db.set_category("app", "terraria.exe", "distracting")
    db.set_setting(distracting.SEEN_KEY, json.dumps(["terraria.exe"]))
    distracting.seed_games(db, [])
    db.set_category("app", "terraria.exe", "distracting")   # you put it back yourself
    distracting.seed_games(db, [])
    assert db.categories()[("app", "terraria.exe")] == "distracting"


def test_a_game_you_categorised_yourself_is_never_touched(tmp_path):
    db = _db(tmp_path)
    db.set_category("app", "terraria.exe", "productive")
    distracting.seed_games(db, GAMES)
    assert db.categories()[("app", "terraria.exe")] == "productive"


def test_your_own_games_category_is_not_a_second_one(tmp_path):
    """Made before the built-in one existed, it has the same key - so the two are one category."""
    db = _db(tmp_path)
    db.set_setting(categories.KEY, json.dumps({"custom": [{"key": "games", "name": "Games", "color": "#ff15ff"},
                                                          {"key": "watching", "name": "Watching",
                                                           "color": "#2F9FD8"}],
                                               "colors": {"games": "#ff15ff"}}))
    names = [c["name"] for c in categories.load(db)]
    assert names.count("Games") == 1
    assert "Watching" in names
    assert categories.colors_of(categories.load(db))["games"] == "#ff15ff"   # your colour is kept


def test_the_starter_list_puts_games_in_games(tmp_path):
    db = _db(tmp_path)
    distracting.seed(db)
    saved = db.categories()
    assert saved[("app", "cs2.exe")] == "games"          # the game
    assert saved[("app", "steam.exe")] == "distracting"  # the launcher it starts from
    assert saved[("site", "poki.com")] == "games"
    assert saved[("site", "netflix.com")] == "distracting"


def test_the_starter_list_moves_its_own_games_over_once(tmp_path):
    db = _db(tmp_path)
    db.set_setting(distracting.VERSION_KEY, "1")         # as an older version left it
    for name, category in (("cs2.exe", "distracting"), ("dota2.exe", "productive")):
        db.set_category("app", name, category)
    distracting.seed(db)
    saved = db.categories()
    assert saved[("app", "cs2.exe")] == "games"
    assert saved[("app", "dota2.exe")] == "productive"   # you moved it: left alone
