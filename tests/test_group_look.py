"""A group's icon and colour (ui-polish UP6): looks only. Stored in block_groups.icon / color (NULL = automatic), carried by the
draft, backed up, and never part of what the guard compares - choosing them is always free. The automatic icon is the
phone's GroupLook.autoIcon in Python (src/group_look.py); the page's picker (src/ui/js/groupicons.js) offers the same keys."""
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

import antibypass as ab
import backup
import group_look
from db import MIGRATIONS, SCHEMA_VERSION, Database
from gui.draft import Draft

SRC = Path(__file__).resolve().parents[1] / "src"
NOW = datetime(2026, 10, 12, 12, 0)
PHONE_KEYS = ["moon", "phone", "game", "social", "video", "chat", "music", "news", "web", "photo", "shopping", "money", "casino",
              "food", "sport", "work", "school", "book", "focus", "coffee", "heart", "star", "bolt", "folder"]


def _group(db, name="Fun", look=None):
    site = db.add_item("Reddit", ["reddit.com"], "site", rules=[], block_type="dns")
    gid = db.add_group(name, [{"rule_type": "time_limit", "daily_limit_min": 120}], {site: {}}, None, look)
    return gid, site


# ---------------------------------------------------------------- the database

def test_an_older_database_gets_the_columns_and_its_groups_stay_automatic(tmp_path):
    path = tmp_path / "t.db"
    db = Database(path)
    _group(db)
    db.close()
    raw = sqlite3.connect(path)
    for column in ("icon", "color"):
        raw.execute(f"ALTER TABLE block_groups DROP COLUMN {column}")
    raw.execute("PRAGMA user_version=8")
    raw.commit()
    raw.close()
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION >= 9
    assert {r["name"] for r in db.conn.execute("PRAGMA table_info(block_groups)")} >= {"icon", "color"}
    [g] = db.list_groups()
    assert (g["name"], g["icon"], g["color"]) == ("Fun", None, None)
    assert ("block_groups", "icon", "TEXT") in MIGRATIONS and ("block_groups", "color", "TEXT") in MIGRATIONS


def test_add_update_and_list_keep_the_look_and_none_means_not_given(tmp_path):
    db = Database(tmp_path / "t.db")
    gid, site = _group(db, look={"icon": "car", "color": "blue"})
    [g] = db.list_groups()
    assert (g["icon"], g["color"]) == ("car", "blue")
    # a caller that only renames (no look) changes nothing else, as `ways` does
    db.update_group(gid, "Fun 2", g["rules"], g["members"], False)
    [g] = db.list_groups()
    assert (g["name"], g["icon"], g["color"]) == ("Fun 2", "car", "blue")
    db.update_group(gid, "Fun 2", g["rules"], g["members"], False, None, {"icon": "bed", "color": None})
    [g] = db.list_groups()
    assert (g["icon"], g["color"]) == ("bed", None)
    db.update_group(gid, "Fun 2", g["rules"], g["members"], False, None, {"icon": "", "color": ""})
    [g] = db.list_groups()
    assert (g["icon"], g["color"]) == (None, None)
    other = db.add_group("Plain", [])
    assert [(x["icon"], x["color"]) for x in db.list_groups() if x["id"] == other] == [(None, None)]


def test_a_backup_keeps_the_look_and_an_older_one_without_it_loads(tmp_path):
    db = Database(tmp_path / "t.db")
    _group(db, look={"icon": "plane", "color": "teal"})
    _group(db, "Auto")
    data = json.loads(json.dumps(backup.export(db)))
    assert {g["name"]: (g["icon"], g["color"]) for g in data["groups"]} == {"Fun": ("plane", "teal"), "Auto": (None, None)}
    other = Database(tmp_path / "other.db")
    backup.restore(other, data)
    assert {g["name"]: (g["icon"], g["color"]) for g in other.list_groups()} == {"Fun": ("plane", "teal"), "Auto": (None, None)}
    for g in data["groups"]:                                   # a backup from before the look existed
        del g["icon"], g["color"]
    old = Database(tmp_path / "old.db")
    backup.restore(old, data)
    assert {g["name"]: (g["icon"], g["color"]) for g in old.list_groups()} == {"Fun": (None, None), "Auto": (None, None)}
    for g in data["groups"]:                                   # a hand-edited file with nonsense in it is not stored
        g["icon"], g["color"] = {"x": 1}, ["blue"]
    bad = Database(tmp_path / "bad.db")
    backup.restore(bad, data)
    assert {(g["icon"], g["color"]) for g in bad.list_groups()} == {(None, None)}


# ---------------------------------------------------------------- the draft and the guard

def test_a_look_change_is_unsaved_until_saved_and_never_asks_for_the_challenge(tmp_path):
    db = Database(tmp_path / "t.db")
    gid, _site = _group(db)
    d = Draft(db)
    db.set_setting("ui.autosave", "0")
    asked = []
    d.guard = lambda changes, proceed, cancel: asked.append(changes)
    g = d.groups[gid]
    d.set_group(gid, g["name"], g["rules"], g["members"], None, {"icon": "shield", "color": "slate"})
    assert d.is_group_unsaved(gid) and d.dirty
    assert d.loosening() == []
    assert ab.draft_changes(d.saved_items, d.items, d.saved_groups, d.groups, NOW) == []
    d.save()
    assert asked == []                                          # (the guard was never asked)
    [saved] = db.list_groups()
    assert (saved["icon"], saved["color"]) == ("shield", "slate")
    # taking the pick back (automatic) is just as free; a set_group without a look keeps it
    d.set_group(gid, "Renamed", saved["rules"], saved["members"])
    assert (d.groups[gid]["icon"], d.groups[gid]["color"]) == ("shield", "slate")
    d.set_group(gid, "Renamed", saved["rules"], saved["members"], None, {"icon": None, "color": None})
    d.save()
    assert asked == [] and [(g["name"], g["icon"], g["color"]) for g in db.list_groups()] == [("Renamed", None, None)]


def test_a_look_change_is_free_while_anti_bypass_is_locked(tmp_path):
    db = Database(tmp_path / "t.db")
    gid, _site = _group(db)
    ab.save(db, {**ab.settings(db), "phrase": True, "length": 30})
    d = Draft(db)
    db.set_setting("ui.autosave", "1")
    asked = []
    d.guard = lambda changes, proceed, cancel: asked.append(changes)
    g = d.groups[gid]
    d.set_group(gid, g["name"], g["rules"], g["members"], None, {"icon": "car", "color": "blue"})        # (autosave: saved at once)
    assert asked == [] and not d.dirty
    assert [(x["icon"], x["color"]) for x in db.list_groups()] == [("car", "blue")]
    # ... but a loosening change sent with it still goes to the guard
    d.set_group(gid, g["name"], g["rules"], {}, None, {"icon": "bed", "color": "blue"})
    assert asked and "Loosen group Fun" in asked[0]


# ---------------------------------------------------------------- the automatic icon and colour

@pytest.mark.parametrize("name, key", [
    ("Good Night", "moon"), ("Nocna zmiana", "moon"), ("Night-time", "moon"), ("sen", "moon"),
    ("Instagram", "social"),                                    # not a game: "gra" is a whole-word stem
    ("Better", "folder"), ("Betting", "casino"), ("bet", "casino"),
    ("Senior", "folder"), ("Grafika", "folder"), ("Gra", "game"), ("My games", "game"),
    ("Ready", "folder"), ("Read", "book"), ("Reading list", "folder"), ("Book club", "book"),
    ("YouTube", "video"), ("Spotify", "music"), ("Work", "work"), ("Study time", "school"), ("Szkoła", "school"),
    ("Zakupy", "shopping"), ("WhatsApp", "chat"), ("", "folder"), ("???", "folder"), ("Misc", "folder"),
])
def test_auto_icon_follows_the_phones_word_table(name, key):
    assert group_look.auto_icon(name) == key


def test_auto_hue_is_stable_never_red_or_slate_and_neighbours_differ():
    hues = [group_look.auto_hue(i) for i in range(1, 40)]
    assert hues == [group_look.auto_hue(i) for i in range(1, 40)]
    assert set(hues) <= set(group_look.AUTO_HUES) and not {"red", "slate"} & set(hues)
    assert all(a != b for a, b in zip(hues, hues[1:])) and len(set(hues)) == len(group_look.AUTO_HUES)
    assert group_look.auto_hue(-2) in group_look.AUTO_HUES and group_look.auto_hue(None) in group_look.AUTO_HUES


def test_look_gives_the_picked_one_or_the_automatic_and_ignores_an_unknown_one():
    assert group_look.look({"id": 3, "name": "Good Night", "icon": "car", "color": "blue"}) == \
        {"icon": "car", "color": "blue", "icon_picked": True, "color_picked": True}
    auto = group_look.look({"id": 3, "name": "Good Night"})
    assert auto == {"icon": "moon", "color": group_look.auto_hue(3), "icon_picked": False, "color_picked": False}
    # a key / hue from a newer copy counts as not picked (as on the phone)
    assert group_look.look({"id": 3, "name": "Good Night", "icon": "rocketship", "color": "BLUE"}) == auto


# ---------------------------------------------------------------- the lists: Python, the page and the tokens agree

def _js(name):
    return (SRC / "ui" / "js" / name).read_text(encoding="utf-8")


def _block(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


def test_the_icon_keys_are_the_phones_plus_more_and_the_page_offers_the_same_ones():
    keys = list(group_look.KEYS)
    assert len(keys) == len(set(keys)) and 55 <= len(keys) <= 70 and set(PHONE_KEYS) <= set(keys)
    sections = _block(_js("groupicons.js"), "export const SECTIONS = [", "export const KEYS")
    entries = re.findall(r'\["([a-z]+)", "([a-z0-9-]+)", "([^"]+)"\]', sections)
    assert [k for k, _s, _l in entries] == keys                       # same keys, same order (the picker's order)
    assert all(label.strip() for _k, _s, label in entries)
    assert len({label for _k, _s, label in entries}) == len(entries)  # (no two icons with one name)
    assert group_look.DEFAULT_KEY in keys


def test_every_icon_has_a_symbol_in_the_sprite_and_none_is_defined_twice():
    boot = set(re.findall(r"^  '?([a-z0-9-]+)'?: '<", _block(_js("icons.js"), "export const PATHS = {", "\n};"), re.M))
    extra_text = _block(_js("groupicons.js"), "const EXTRA = {", "};")
    extra = re.findall(r"^  '?([a-z0-9-]+)'?: '<", extra_text, re.M)
    assert len(extra) == len(set(extra)) and not boot & set(extra)
    sections = _block(_js("groupicons.js"), "export const SECTIONS = [", "export const KEYS")
    used = set(re.findall(r'\["[a-z]+", "([a-z0-9-]+)", "[^"]+"\]', sections))
    assert used <= boot | set(extra), used - boot - set(extra)
    assert set(extra) <= used                                          # (nothing added that no icon uses)
    assert "stroke" not in extra_text and "fill=" not in extra_text.replace('fill="none"', "")   # (paths only: the sprite sets the stroke)


def test_the_word_table_and_the_hues_are_the_same_in_the_page():
    text = _js("groupicons.js")
    rows = re.findall(r'\["([a-z]+)", \[([^\]]+)\]\]', _block(text, "const WORDS = [", "const WHOLE"))
    assert [(k, tuple(re.findall(r'"([^"]+)"', stems))) for k, stems in rows] == list(group_look.WORDS)
    assert tuple(re.findall(r'\["([a-z]+)", "[A-Z][a-z]+"\]', _block(text, "export const HUES = [", "const HUE_LABEL"))) == group_look.HUES
    assert set(group_look.AUTO_HUES) < set(group_look.HUES) and group_look.WHOLE == {"read"}
    assert 'const WHOLE = new Set(["read"])' in text


def test_the_colour_tokens_exist_in_light_dark_and_as_classes():
    css = (SRC / "ui" / "css" / "tokens.css").read_text(encoding="utf-8")
    light = _block(css, ":root, [data-theme=\"light\"] {", "/* ================= DARK")
    dark = _block(css, "[data-theme=\"dark\"], [data-theme=\"amoled\"] {", "/* ================= AMOLED")
    for hue in group_look.HUES:
        for part in (light, dark):
            assert re.search(rf"--hue-{hue}: #[0-9A-Fa-f]{{6}};", part) and re.search(rf"--hue-{hue}-soft: #[0-9A-Fa-f]{{6}};", part), hue
        assert f".hue-{hue} {{ --k: var(--hue-{hue}); --k-soft: var(--hue-{hue}-soft); }}" in css
