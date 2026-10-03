"""A group's "How members are blocked" (0.84.11).

Adam: in a group you couldn't choose how things are blocked - for apps whether they are closed or cut off the
internet, for sites whether they just can't load or their tab is closed. He doesn't want his YouTube tabs closed,
least of all several at once. Each member kept the way it was set when it was added, and on top of that the tray
sent back / closed the tab of EVERY blocked site in front (0.84.1, 0.84.9), "can't load" ones too.

Now a group has its own way for its app members and its site members; a member can add to it in its extra-limits
window (stricter only, like its extra rules - 0.84.3); a "can't load" site's tabs are never touched (the network
stops an open video: connections cut, video hosts firewalled); a "close the tab" site has one tab closed at a time;
and making a group's way weaker needs the Anti-Bypass challenge.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

import antibypass as ab
import backup
import block_method
import rules
from db import SCHEMA_VERSION, Database
from gui.draft import Draft
from monitor import usage as usage_mod
from monitor import word_guard
from test_app_limit_enforcement import BOOT, FRIDAY, World, add_game, at, block_during, world  # noqa: F401

NIGHT = rules.make_schedule("block", [(list(range(7)), "21:00", "05:00")])
EVENING = datetime(2026, 10, 2, 22, 0)
OFF = {"enabled": False, "action": "close"}          # bad-word checking off: only blocked sites


def _setup(db, app_block=None, site_block=None, yt_type=None, game_type="close", member_blocks=None):
    """A group blocked 21:00-05:00 with YouTube (a site) and a game (an app) in it."""
    yt = db.add_item("YouTube", ["youtube.com", "googlevideo.com"], "site", rules=[], block_type=yt_type)
    game = db.add_item("Game", ["game.exe"], "app", rules=[], block_type=game_type)
    ways = {"app_block": app_block, "site_block": site_block,
            "member_blocks": {{"yt": yt, "game": game}[k]: w for k, w in (member_blocks or {}).items()}}
    gid = db.add_group("Evening", [{"rule_type": "scheduled", "schedule": NIGHT}], {yt: {}, game: {}}, ways)
    return yt, game, gid


def _way(db, item_id, now=EVENING):
    return next(b["item"]["block_type"] for b in db.blocks(now) if b["item"]["id"] == item_id)


# ---------------------------------------------------------------- the group's way applies to its members

def test_a_group_s_way_applies_to_its_members(tmp_path):
    db = Database(tmp_path / "t.db")
    yt, game, _gid = _setup(db, app_block="internet", site_block="dns,close", yt_type="dns", game_type="close")
    assert _way(db, game) == "internet"            # cut off the internet, not closed - whatever the app's own
    assert _way(db, yt) == "dns,close"
    [g] = db.list_groups()
    assert (g["app_block"], g["site_block"], g["member_blocks"]) == ("internet", "dns,close", {})


def test_a_group_that_hasnt_chosen_blocks_each_member_as_it_is_set(tmp_path):
    """Every group before 0.84.11: nothing changes on upgrade."""
    db = Database(tmp_path / "t.db")
    yt, game, _gid = _setup(db, yt_type="dns,back", game_type="minimize,internet")
    assert _way(db, yt) == "dns,back"
    assert _way(db, game) == "minimize,internet"


def test_the_item_s_own_rules_keep_its_own_way_and_both_together_do_both(tmp_path):
    db = Database(tmp_path / "t.db")
    yt, game, gid = _setup(db, app_block="internet", game_type="minimize")
    g = db.list_groups()[0]
    db.update_item(game, "Game", ["game.exe"], None, [{"rule_type": "temporary", "temp_until": "2026-10-02 23:00:00"}],
                   block_type="minimize")
    assert _way(db, game) == "minimize,internet"                          # both block at 22:00
    db.update_group(gid, g["name"], [], g["members"])                    # only its own rule left
    assert _way(db, game) == "minimize"


def test_a_member_can_only_add_to_the_group_s_way(tmp_path):
    db = Database(tmp_path / "t.db")
    yt, game, _gid = _setup(db, app_block="internet", site_block="dns",
                            member_blocks={"game": "close", "yt": "close"})
    assert _way(db, game) == "close,internet"      # the member's own on top of the group's
    assert _way(db, yt) == "dns,close"
    # weaker than the group's: changes nothing
    group = {"app_block": "close", "site_block": "dns,close", "member_blocks": {1: "back", 2: "minimize"}}
    assert block_method.in_group({"id": 1, "item_type": "site", "block_type": None}, group) == "dns,close"
    assert block_method.in_group({"id": 2, "item_type": "app", "block_type": None}, group) == "close"


def test_an_app_cut_off_by_its_group_is_not_closed_but_firewalled(world):
    """End to end through the service: the group says "cut internet" - the game keeps running, its internet goes;
    the group says "close" - it is closed, whatever the game was set to."""
    w = world(at(FRIDAY, "22:00"))
    game_item = add_game(w, [], block_type="close")
    gid = w.gui.add_group("Games", [block_during("21:00", "05:00")], {game_item: {}}, {"app_block": "internet"})
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.run(1)
    assert w.alive(game) and "hollowgame.exe" in w.enforcer.firewalled
    g = w.gui.list_groups()[0]
    w.gui.update_group(gid, g["name"], g["rules"], g["members"], ways={"app_block": "close"})
    w.run(1)
    assert not w.alive(game) and "hollowgame.exe" not in w.enforcer.firewalled


def test_an_opening_of_an_app_its_group_only_cuts_off_counts(tmp_path):
    """Openings of an app that is closed at once don't spend a shared opening limit (0.84.3) - but one its group
    only cuts off the internet is used, so it counts, whatever the app's own setting says."""
    db = Database(tmp_path / "t.db")
    game = db.add_item("Game", ["game.exe"], "app", rules=[], block_type="close")
    gid = db.add_group("Fun", [{"rule_type": "switch_limit", "daily_switch_limit": 1, "switch_mode": "switch"}],
                       {game: {}}, {"app_block": "internet"})
    item = next(i for i in db.list_items() if i["id"] == game)
    clock = db.limit_clock()
    targets = rules.switch_targets(rules.effective_rules(item, db.list_groups()), game, EVENING, clock)
    db.add_usage(targets, 2, EVENING.date())                             # already over: blocked
    assert usage_mod.UsageTracker.unless_closed(db, item, db.list_groups(), targets, EVENING, clock) == targets
    g = db.list_groups()[0]
    db.update_group(gid, g["name"], g["rules"], g["members"], ways={"app_block": "close"})
    left = usage_mod.UsageTracker.unless_closed(db, item, db.list_groups(), targets, EVENING, clock)
    assert any(t[1].startswith("op:") for t in targets) and not any(t[1].startswith("op:") for t in left)


# ---------------------------------------------------------------- "can't load" never closes a tab

@pytest.fixture
def youtube_tab(monkeypatch):
    """YouTube full screen in Firefox (the address bar hidden: the title says the site)."""
    from test_firefox_youtube import FIREFOX, FakeFirefox
    from monitor import browser_url, win
    ff = FakeFirefox()
    ff.full_screen = True
    monkeypatch.setattr(browser_url.auto, "ControlFromHandle", lambda hwnd: ff if hwnd == FIREFOX else None,
                        raising=False)
    monkeypatch.setattr(win, "foreground", lambda: (FIREFOX, "firefox.exe"))
    monkeypatch.setattr(win, "window_title", lambda hwnd: ff.title if hwnd == FIREFOX else "")
    browser_url._cache.clear()
    word_guard._bar.clear()
    usage_mod._seen.clear()
    usage_mod._no_bar.clear()
    return ff


def test_cant_load_never_closes_its_tabs_but_the_playing_video_is_cut_and_firewalled(tmp_path, monkeypatch,
                                                                                     youtube_tab):
    """Adam's YouTube in his evening group, "can't load" only, a video playing full screen when the block begins.
    The tab check leaves it alone; the service still stops the stream: the video host's open connection is cut
    and its addresses (IPv4 and IPv6) firewalled, and the DNS filter answers nothing for googlevideo.com."""
    w = World(tmp_path, monkeypatch, EVENING - timedelta(seconds=20))
    yt = w.gui.add_item("YouTube", ["youtube.com", "googlevideo.com"], "site", rules=[], block_type="dns,close")
    w.gui.add_group("Evening", [block_during("22:00", "05:00")], {yt: {}}, {"site_block": "dns"})
    video = {"172.217.1.10": "rr3---sn-u2oxu-f5fed.googlevideo.com",
             "2a00:1450:4001:1::a": "rr3---sn-u2oxu-f5fed.googlevideo.com"}
    rule = []
    monkeypatch.setattr("service.netlog.dns_names", lambda: dict(video))
    monkeypatch.setattr("service.firewall.set_video_block", lambda ips: rule.extend(ips) or True)
    monkeypatch.setattr("service.firewall.remove_video_block", lambda: True)
    monkeypatch.setattr(word_guard, "now_from_db", lambda db: w.now)
    acts = []
    guard = word_guard.WordGuard(lambda *a: None)
    for _ in range(10):                                                  # 21:59:40 .. 22:00:20
        w.step(4)
        guard.tick(OFF, word_guard.sense_tab, lambda hwnd, a: acts.append(a) or True, w.ts,
                   word_guard.blocked_sites(w.gui))
    assert _way(w.gui, yt, w.now) == "dns"                               # the group's way, not the item's own
    assert acts == []                                                    # the tab is never closed or sent back
    assert set(w.enforcer.dns_blocks) == {"youtube.com", "googlevideo.com"}
    assert w.enforcer.blocked_name("rr3---sn-u2oxu-f5fed.googlevideo.com") == "blocked"   # (the DNS filter)
    assert sorted(set(rule)) == sorted(video)                            # the stream: firewalled, v4 and v6
    assert w.enforcer.video_ips == video


def test_close_the_tab_closes_just_the_one_in_front_once(youtube_tab):
    """Close chosen: the tab in front is closed - and the next YouTube tab the browser shows after it is not closed
    straight away as well (0.84.9 closed every YouTube tab of the window in a row). It is closed only if it is
    still in front CLOSE_GAP_SEC later, so never several at once."""
    from test_firefox_youtube import FIREFOX
    sites = {"youtube.com": "close", "googlevideo.com": "close"}
    acts, guard = [], word_guard.WordGuard(lambda *a: None)
    do = lambda hwnd, a: acts.append(a) or True                          # noqa: E731
    guard.tick(OFF, word_guard.sense_tab, do, 0.0, sites)
    assert acts == ["close"]
    youtube_tab.title = "Another video - YouTube — Mozilla Firefox"       # the next tab comes to the front
    for t in (0.5, 1.0, 2.5, 5.0, 9.5):
        guard.tick(OFF, word_guard.sense_tab, do, t, sites)
    assert acts == ["close"]                                             # one tab, once
    guard.tick(OFF, word_guard.sense_tab, do, 10.5, sites)                # still watched 10 s later: that one too
    assert acts == ["close", "close"]
    assert guard.last[0][0] == FIREFOX


def test_a_tab_you_open_after_a_close_is_dealt_with_at_once(youtube_tab):
    sites = {"youtube.com": "close"}
    acts, guard = [], word_guard.WordGuard(lambda *a: None)
    do = lambda hwnd, a: acts.append(a) or True                          # noqa: E731
    guard.tick(OFF, word_guard.sense_tab, do, 0.0, sites)
    youtube_tab.title = "cats - Google Search — Mozilla Firefox"         # something else in front ...
    guard.tick(OFF, word_guard.sense_tab, do, 1.0, sites)
    youtube_tab.title = "Cats again - YouTube — Mozilla Firefox"         # ... then YouTube opened again
    guard.tick(OFF, word_guard.sense_tab, do, 1.5, sites)
    assert acts == ["close", "close"]


def test_a_group_set_to_close_the_tab_has_its_sites_closed(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    _setup(db, site_block="dns,close")
    monkeypatch.setattr(word_guard, "now_from_db", lambda d: EVENING)
    assert word_guard.blocked_sites(db) == {"youtube.com": "close", "googlevideo.com": "close"}
    g = db.list_groups()[0]
    db.update_group(g["id"], g["name"], g["rules"], g["members"], ways={"site_block": "dns"})
    assert word_guard.blocked_sites(db) == {}


# ---------------------------------------------------------------- Anti-Bypass: weaker needs the challenge

def _items():
    return {1: {"id": 1, "display_name": "YouTube", "item_type": "site", "target": "youtube.com",
                "block_type": "dns,close", "rules": [], "notify": None, "app_path": None},
            2: {"id": 2, "display_name": "Game", "item_type": "app", "target": "game.exe",
                "block_type": "close", "rules": [], "notify": None, "app_path": None}}


def _group(app=None, site=None, members=None):
    return {"id": 7, "name": "Evening", "rules": [{"rule_type": "permanent"}], "members": {1: {}, 2: {}},
            "app_block": app, "site_block": site, "member_blocks": members or {}}


@pytest.mark.parametrize("old, new, looser", [
    (_group(app="close"), _group(app="internet"), True),                 # closed -> only cut off
    (_group(app="close"), _group(app="minimize"), True),
    (_group(app="internet"), _group(app="close,internet"), False),       # stricter: free
    (_group(app="minimize"), _group(app="close"), False),                # closing covers minimizing
    (_group(site="dns,close"), _group(site="dns"), True),                # "close the tab" dropped
    (_group(site="dns"), _group(site="close"), True),                    # it can load again
    (_group(site="dns"), _group(site="dns,close"), False),
    (_group(site="dns,back"), _group(site="dns,close"), False),          # closing covers going back
    (_group(site="dns,close"), _group(site="dns,back"), True),
    (_group(), _group(site="dns"), True),          # not chosen: YouTube was "dns,close" - choosing "dns" drops a way
    (_group(), _group(site="dns,close", app="close"), False),            # the same as each member was: free
    (_group(site="dns,close"), _group(), False),   # back to each member's own: YouTube's own is "dns,close"
    (_group(app="internet"), _group(), True),      # the game's own "close" doesn't cut its internet
    (_group(site="dns", members={1: "close"}), _group(site="dns"), True),   # a member's own way removed
    (_group(site="dns"), _group(site="dns", members={1: "close"}), False),  # added: free
    (_group(app="internet", members={2: "close"}), _group(app="close,internet"), False),  # now the group's
])
def test_a_weaker_way_needs_the_challenge_a_stronger_one_is_free(old, new, looser):
    items = _items()
    assert ab.group_looser(old, new, datetime(2026, 10, 2, 12), items, items) is looser
    expected = ["Loosen group Evening"] if looser else []
    assert ab.draft_changes(items, items, {7: old}, {7: new}, datetime(2026, 10, 2, 12)) == expected


def test_a_member_s_item_made_weaker_still_needs_it_through_a_group_that_hasnt_chosen():
    items = _items()
    weaker = {**items, 1: {**items[1], "block_type": "dns"}}
    assert ab.draft_changes(items, weaker, {7: _group()}, {7: _group()}, datetime(2026, 10, 2, 12)) == \
        ["Loosen YouTube", "Loosen group Evening"]


# ---------------------------------------------------------------- saved, kept, backed up, upgraded

def test_the_draft_saves_the_ways_and_a_change_is_unsaved_until_then(tmp_path):
    db = Database(tmp_path / "t.db")
    yt, game, gid = _setup(db, site_block="dns")
    draft = Draft(db)
    g = draft.groups[gid]
    draft.db.set_setting("ui.autosave", "0")
    draft.set_group(gid, g["name"], g["rules"], g["members"],
                    {"app_block": "internet", "site_block": "dns,close", "member_blocks": {game: "close"}})
    assert draft.is_group_unsaved(gid)
    draft.save()
    [saved] = db.list_groups()
    assert (saved["app_block"], saved["site_block"], saved["member_blocks"]) == ("internet", "dns,close",
                                                                                  {game: "close"})
    # a change that doesn't touch them (e.g. adding a member from the app browser) keeps them
    draft.set_group(gid, saved["name"], saved["rules"], dict(saved["members"]))
    draft.save()
    assert db.list_groups()[0]["member_blocks"] == {game: "close"}


def test_a_new_member_s_own_way_is_saved_under_its_new_id(tmp_path):
    db = Database(tmp_path / "t.db")
    draft = Draft(db)
    item = draft.add_item("Twitch", ["twitch.tv"], "manual", "site", "dns")
    draft.set_group(None, "Fun", [{"rule_type": "permanent"}], {item["id"]: {}},
                    {"app_block": None, "site_block": "dns", "member_blocks": {item["id"]: "close"}})
    [g] = db.list_groups()
    [twitch] = db.list_items()
    assert g["member_blocks"] == {twitch["id"]: "close"}


def test_a_backup_keeps_the_ways(tmp_path):
    db = Database(tmp_path / "t.db")
    yt, game, _gid = _setup(db, app_block="internet", site_block="dns", member_blocks={"yt": "close"})
    data = json.loads(json.dumps(backup.export(db)))
    other = Database(tmp_path / "other.db")
    backup.restore(other, data)
    [g] = other.list_groups()
    new_yt = next(i["id"] for i in other.list_items() if i["display_name"] == "YouTube")
    assert (g["app_block"], g["site_block"], g["member_blocks"]) == ("internet", "dns", {new_yt: "close"})


def test_an_older_backup_without_them_blocks_each_member_as_it_is_set(tmp_path):
    db = Database(tmp_path / "t.db")
    _setup(db)
    data = backup.export(db)
    for g in data["groups"]:
        for key in ("app_block", "site_block", "member_blocks"):
            del g[key]
    other = Database(tmp_path / "other.db")
    backup.restore(other, data)
    [g] = other.list_groups()
    assert (g["app_block"], g["site_block"], g["member_blocks"]) == (None, None, {})


def test_an_older_database_gets_the_columns_and_its_groups_unchanged(tmp_path):
    path = tmp_path / "t.db"
    db = Database(path)
    yt, game, _gid = _setup(db, yt_type="dns,back")
    db.close()
    raw = sqlite3.connect(path)
    for column in ("app_block", "site_block", "member_blocks"):
        raw.execute(f"ALTER TABLE block_groups DROP COLUMN {column}")
    raw.execute("PRAGMA user_version=4")
    raw.commit()
    raw.close()
    db = Database(path)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION >= 5
    [g] = db.list_groups()
    assert (g["app_block"], g["site_block"], g["member_blocks"]) == (None, None, {})
    assert _way(db, yt) == "dns,back"


# ---------------------------------------------------------------- the editor's tick boxes (needs a display)

def test_the_member_window_greys_the_group_s_way_and_offers_only_more(monkeypatch):
    from test_overlay_screens import _tk_root
    ctk, root = _tk_root()
    try:
        from gui.groups import WayBoxes
        boxes = WayBoxes(root, "site", locked={"dns", "close"})
        assert boxes.boxes["dns"].cget("state") == "disabled" and boxes.boxes["dns"].get()
        assert boxes.boxes["back"].cget("state") == "disabled"          # closing the tab already covers it
        assert boxes.value() is None                                      # nothing of its own
        apps = WayBoxes(root, "app", locked={"internet"})
        apps.boxes["close"].select()
        apps._ticked("close")
        assert apps.value() == "close" and apps.boxes["minimize"].cget("state") == "disabled"
        apps.boxes["background"].select()
        assert apps.value() == "close,background"
        group = WayBoxes(root, "app")
        group.set("close")
        assert group.value() == "close"
        group.set(None)
        assert group.value() is None                                     # not chosen: each as it is set
    finally:
        root.destroy()


def test_the_item_editor_says_when_a_group_s_way_applies_instead(tmp_path):
    """Review: the item's own "When blocked" isn't what a group that chose its way uses - the editor says so."""
    import types

    from test_overlay_screens import _tk_root
    ctk, root = _tk_root()
    try:
        from gui.blocking import AddTab
        db = Database(tmp_path / "t.db")
        yt = db.add_item("YouTube", ["youtube.com"], "site", rules=[], block_type="dns,close")
        db.add_group("Evening", [{"rule_type": "permanent"}], {yt: {}}, {"site_block": "dns"})
        db.add_group("Other", [{"rule_type": "permanent"}], {yt: {}}, {"app_block": "internet"})
        page = types.SimpleNamespace(draft=Draft(db), app=types.SimpleNamespace(db=db),
                                     show_tab=lambda *a: None, confirm=lambda *a: None)
        tab = AddTab(root, page)
        tab.edit(yt)
        assert tab.info.cget("text") == ("Also in groups: Evening, Other (edit those in the Groups tab) - when "
                                         "Evening blocks it, that group's way of blocking applies")
    finally:
        root.destroy()
