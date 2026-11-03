"""Round 2, item 8: category extras must reach actual app/site enforcement."""
from datetime import datetime

import pytest

import modes
from db import Database
from webapi.modes import _build_extra

NOW = datetime(2026, 9, 14, 10)


@pytest.mark.parametrize("ways", [["close"], ["minimize"], ["internet"]])
def test_mode_category_extra_blocks_members(tmp_path, ways):
    db = Database(tmp_path / "config.db")
    db.set_category("app", "game.exe", "games")
    db.set_category("site", "games.example", "games")
    db.set_category("site", "work.example", "productive")
    extra = _build_extra({"kind": "category", "text": "games", "ways": ways}, {"games": "Games"})
    saved = modes.load(db)
    saved[-1]["extra"] = [extra]  # Relax has no other targets to hide a broken extra.
    modes.save(db, saved)
    modes.start(db, "relax", NOW, None)
    blocks = {b["item"]["target"]: b for b in db.blocks(NOW)}
    assert set(blocks) == {"game.exe", "games.example"}
    assert blocks["game.exe"]["item"]["block_type"] == extra["block_type"]
    assert set(db.blocked_hostnames(NOW)) == {"games.example"}


def test_mode_category_extra_includes_existing_members_without_duplicates(tmp_path):
    db = Database(tmp_path / "config.db")
    item_id = db.add_site("Existing", ["games.example"], rules=[])
    db.set_category("site", "games.example", "games")
    mode = {"items": [item_id], "extra": [{"kind": "category", "name": "Games", "targets": ["games"]}]}
    targets = modes.targets(mode, db.list_items(), [], db.categories())
    assert [(i["id"], i["target"]) for i in targets] == [(item_id, "games.example")]


def test_mode_category_extra_uses_chosen_app_way_for_existing_members(tmp_path):
    db = Database(tmp_path / "config.db")
    item_id = db.add_item("Game", ["game.exe"], "app", rules=[], block_type="close")
    db.set_category("app", "game.exe", "games")
    mode = {"extra": [{"kind": "category", "name": "Games", "targets": ["games"], "block_type": "internet"}]}
    targets = modes.targets(mode, db.list_items(), [], db.categories())
    assert [(i["id"], i["target"], i["block_type"]) for i in targets] == [(item_id, "game.exe", "internet")]
