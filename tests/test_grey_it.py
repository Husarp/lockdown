"""Grey it as a way of blocking an app on the PC (blocker/apps.py, block_method.py, antibypass.py, service.py, db.blocks):
the app stays open, the screen goes grey while it is in front (resident/grayscale.py - tests/test_grey_screen.py).

The ways order, from strongest: Close app (covers minimizing and greying), Minimize (covers greying), Grey it. Going down that
list is a loosening (the Anti-Bypass challenge), going up is free. The service never kills a Grey app. A mode that blocks it
still closes it."""
from datetime import datetime, timedelta

import pytest

import antibypass as ab
import block_method
import modes
import service
from blocker import apps
from db import Database
from test_service_apps import full, make_enforcer

NOW = datetime(2026, 10, 12, 22, 0, 0)


# ---------------------------------------------------------------- the flag

def test_grey_is_a_way_of_its_own_that_closes_nothing():
    assert apps.FLAGS == ("close", "background", "minimize", "grey", "internet")
    assert apps.block_flags("grey") == {"grey"}
    assert apps.make_block_type({"internet", "grey"}) == "grey,internet"
    assert apps.greys("grey") and apps.greys("grey,internet")
    assert not apps.kills("grey") and not apps.minimizes("grey") and apps.firewalls("grey,internet")
    assert not apps.greys("close") and not apps.greys(None) and not apps.greys("minimize,internet")
    assert not apps.greys("close,grey") and not apps.greys("minimize,grey"), "closing / minimizing cover it"


def test_a_program_file_cannot_be_greyed_it_is_closed_instead():
    """The window in front of a Java game or a script is javaw.exe / python.exe: a group that greys its members closes the file."""
    assert apps.hosted_way("grey") == "close"
    assert apps.hosted_way("grey,internet") == "close"
    assert apps.hosted_way("minimize,grey") == "minimize"
    assert apps.hosted_way("close") == "close" and apps.hosted_way("internet") is None


def test_two_blocks_on_the_same_exe_do_what_both_ask():
    def block(way):
        return {"item": {"id": 1, "target": "game.exe", "block_type": way, "app_path": None}, "reason": "schedule", "until": None}
    for a, b, expected in (("grey", "close", "close"), ("grey", "minimize", "minimize"), ("grey", "internet", "grey,internet"),
                           ("minimize", "grey,internet", "minimize,internet"), ("grey", "grey", "grey"),
                           ("close,background", "grey", "close,background"), ("grey,internet", "close", "close,internet")):
        assert apps.merge_blocks(block(a), block(b))["item"]["block_type"] == expected, (a, b)
        assert apps.merge_blocks(block(b), block(a))["item"]["block_type"] == expected, (b, a)
        assert block_method.merge("app", a, b) == expected, (a, b)


def test_strength_closing_covers_minimizing_and_both_cover_greying():
    s = lambda way: block_method.strength("app", way)
    assert s("close") == {"close", "minimize", "grey"}
    assert s("minimize") == {"minimize", "grey"}
    assert s("grey") == {"grey"} and s("grey,internet") == {"grey", "internet"}
    assert s("internet") == {"internet"} and s(None) == {"close", "minimize", "grey"}
    assert block_method.strength("site", "close") == {"close", "back"}      # (sites: unchanged)


def test_a_group_s_grey_is_the_way_of_a_member_that_has_none_of_its_own():
    group = {"app_block": "grey", "site_block": None, "member_blocks": {2: "close"}}
    app = {"id": 1, "item_type": "app", "block_type": "close"}
    assert block_method.in_group(app, group) == "grey"                      # the group's way replaces the app's own setting
    assert block_method.in_group({**app, "id": 2}, group) == "close"        # a member's own way replaces the group's
    assert block_method.in_group({**app, "id": 3, "block_type": "minimize"}, {**group, "app_block": None}) == "minimize"


# ---------------------------------------------------------------- the challenge

def item(way, **kw):
    return {"id": 1, "item_type": "app", "display_name": "Game", "target": "game.exe", "block_type": way, "disabled": False,
            "rules": [{"rule_type": "permanent"}], **kw}


@pytest.mark.parametrize("old,new,loosens", [
    ("close", "grey", True), ("close,background", "grey", True), ("minimize", "grey", True), (None, "grey", True),
    ("grey", "close", False), ("grey", "minimize", False), ("grey", "close,background", False), ("grey", "grey,internet", False),
    ("grey,internet", "grey", True), ("grey,internet", "internet", True), ("close", "minimize", True), ("minimize", "close", False),
    ("grey", "grey", False), ("internet", "grey", True), ("grey", "internet", True),
])
def test_switching_to_grey_loosens_and_away_from_it_does_not(old, new, loosens):
    assert ab.item_looser(item(old), item(new), NOW) is loosens


def test_the_ways_of_a_group_are_judged_per_member():
    members = {1: {}}
    group = lambda app_block, own=None: {"id": 5, "name": "Fun", "members": members, "rules": [], "app_block": app_block,
                                          "site_block": None, "member_blocks": own or {}}
    games = lambda way: {1: item(way)}
    assert ab.ways_looser(group("close"), group("grey"), games("close"), games("close"))
    assert not ab.ways_looser(group("grey"), group("close"), games("close"), games("close"))
    assert ab.ways_looser(group("close"), group("close", {1: "grey"}), games("close"), games("close"))      # a member's own
    assert ab.ways_looser(group("minimize"), group("grey"), games("close"), games("close"))
    assert not ab.ways_looser(group("grey"), group("minimize"), games("close"), games("close"))
    assert ab.ways_looser(group(None), group("grey"), games("close"), games("close")), \
        "a group that hadn't chosen blocked each member the way it was set (close): grey is lighter"
    assert not ab.ways_looser(group(None), group("grey"), games("grey"), games("grey"))
    assert ab.draft_changes({1: item("close")}, {1: item("close")}, {5: {**group("close"), "members": {1: {}}}},
                            {5: {**group("grey"), "members": {1: {}}}}, NOW) == ["Loosen group Fun"]


# ---------------------------------------------------------------- the service leaves it alone

def test_the_service_never_kills_a_grey_app(monkeypatch):
    killed = []
    monkeypatch.setattr(apps, "list_processes_full", lambda: full([(10, "discord.exe")]))
    monkeypatch.setattr(apps, "terminate", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(apps, "start_time", lambda pid: 1.0)                    # started long before the block
    for way in ("grey", "grey,internet"):
        e = make_enforcer(way)
        e.enforce_apps()
        e.enforce_apps()
        assert e.events == [] and killed == [], way


def test_a_grey_block_and_a_close_block_on_the_same_app_still_close_it(monkeypatch):
    killed = []
    monkeypatch.setattr(apps, "list_processes_full", lambda: full([(10, "discord.exe")]))
    monkeypatch.setattr(apps, "terminate", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(apps, "start_time", lambda pid: 1.0)
    e = make_enforcer("grey")
    close = {"item": {**e.app_blocks["discord.exe"]["item"], "block_type": "close"}, "reason": "schedule", "until": None}
    e.app_blocks["discord.exe"] = apps.merge_blocks(e.app_blocks["discord.exe"], close)
    e.block_since = {}
    monkeypatch.setattr(service.time, "time", lambda: 2000.0)
    e.enforce_apps()
    assert e.events == ["discord.exe"]
    monkeypatch.setattr(service.time, "time", lambda: 2020.0)
    e.enforce_apps()
    assert killed == [10]


def test_the_firewall_still_follows_a_grey_app_that_also_cuts_the_internet():
    e = make_enforcer("grey,internet")
    assert apps.firewalls(e.app_blocks["discord.exe"]["item"]["block_type"])


# ---------------------------------------------------------------- db.blocks and modes

@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


def way_of(db, item_id, now=NOW):
    return [b["item"]["block_type"] for b in db.blocks(now) if b["item"]["id"] == item_id]


def test_a_grey_app_is_blocked_with_its_way(db):
    game = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "permanent"}], block_type="grey")
    assert way_of(db, game) == ["grey"]


def test_a_mode_that_blocks_the_app_closes_it(db):
    game = db.add_item("Game", ["game.exe"], "app", rules=[{"rule_type": "permanent"}], block_type="grey")
    listed = modes.load(db)
    listed[0]["items"] = [game]
    modes.save(db, listed)
    modes.start(db, "work", NOW, None)
    blocks = [b for b in db.blocks(NOW) if b["item"]["id"] == game]
    assert [(b["reason"], b["item"]["block_type"]) for b in blocks] == [("mode", "close")]     # one block: the mode's, closing


def test_a_mode_takes_the_grey_out_of_a_way_and_keeps_the_rest(db):
    game = db.add_item("Game", ["game.exe"], "app", rules=[], block_type="grey,internet")
    listed = modes.load(db)
    listed[0]["items"] = [game]
    modes.save(db, listed)
    modes.start(db, "work", NOW, None)
    assert way_of(db, game) == ["close,internet"]                  # grey taken out, closing put in, the rest kept


def test_a_mode_leaves_the_other_ways_of_the_item_as_they_were(db):
    mini = db.add_item("Mini", ["mini.exe"], "app", rules=[{"rule_type": "permanent"}], block_type="minimize")
    listed = modes.load(db)
    listed[0]["items"] = [mini]
    modes.save(db, listed)
    modes.start(db, "work", NOW, None)
    assert way_of(db, mini) == ["minimize"]                        # (as always: the item's own block goes first)


def test_a_mode_s_own_extra_apps_are_never_grey():
    mode = {"id": "m", "name": "M", "categories": [], "items": [], "groups": [], "extra": [
        {"kind": "app", "name": "Odd", "targets": ["odd.exe"], "block_type": "grey"}]}
    [extra] = modes.targets(mode, [], [], {})
    assert extra["block_type"] == "close"


def test_the_blocked_list_says_how_the_app_is_blocked(db):
    from webapi import blocking
    assert "greys the screen" in blocking._targets_text(item("grey"))
    assert "greys the screen" in blocking._targets_text(item("grey,internet")) and "internet blocked" in blocking._targets_text(item("grey,internet"))
