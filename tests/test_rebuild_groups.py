"""R21: Blocking > Groups over the bridge (webapi/groups.py) - the list, one group for the editor, the member search, and
saving a group through the draft with Python deciding what loosens (antibypass.draft_changes / ways_looser) and the page
sending forms and ways only (never "confirmed")."""
import random
import time
from datetime import datetime, timedelta

import pytest

import antibypass as ab
import rule_forms
import webapi
from webapi import core, draft as drafts, guard
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)
LIMIT_2H = {"rule_type": "time_limit", "daily_limit_min": 120}
NIGHT = {"rule_type": "scheduled",
         "schedule": '{"mode": "block", "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "21:00", "end": "23:00"}]}'}


class Clock:
    def __init__(self):
        self.now, self.mono = NOW, 1000.0

    def __call__(self, db=None):
        return self.now

    def tick(self):
        return self.mono


class FakeCatalog:
    def __init__(self, apps):
        self.apps = apps

    def search(self, kind="all", query="", limit=60):
        found = [a for a in self.apps if query.lower() in (a["name"] + a["exe"]).lower()]
        return {"apps": found[:limit], "total": len(found), "loading": False}


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    ctx = webapi.make_context(tmp_path / "t.db", clock=clock)
    ctx.guard = GuardService(lambda: ctx.db, clock, monotonic=clock.tick, rng=random.Random(7))
    ctx.clock = clock
    ctx.api = core.build_api(ctx)
    ctx.pushed = []
    ctx.publish = lambda event, payload=None, key=None: ctx.pushed.append((event, payload))
    ctx.catalog = FakeCatalog([
        {"name": "Notepad", "exe": "notepad.exe", "path": r"C:\Windows\notepad.exe", "running": False, "steam": False},
        {"name": "Game", "exe": "game.exe", "path": r"C:\Games\game.exe", "running": False, "steam": False},
        {"name": "Spotify", "exe": "spotify.exe", "path": r"C:\Spotify\spotify.exe", "running": False, "steam": False}])
    db = ctx.db
    db.set_setting("service_heartbeat", str(time.time()))              # the service is running
    ctx.reddit = db.add_item("Reddit", ["reddit.com"], "site", "manual", [], block_type="dns")
    ctx.yt = db.add_item("YouTube", ["youtube.com", "googlevideo.com"], "site", "manual", [], block_type="dns")
    ctx.game = db.add_item("Game", ["game.exe"], "app", "manual", [], block_type="close")
    ctx.jar = db.add_item("Tool", ["tool.jar"], "app", "manual", [{"rule_type": "permanent"}], block_type="close")
    ctx.gid = db.add_group("Fun", [dict(LIMIT_2H)], {ctx.reddit: {}, ctx.yt: {}, ctx.game: {}},
                           {"app_block": "close", "site_block": "dns", "member_blocks": {}})
    return ctx


def lock(ctx, **kw):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30, **kw})


def autosave(ctx, on):
    ctx.db.set_setting("ui.autosave", "1" if on else "0")
    return ctx


def call(ctx, name, *args):
    return getattr(ctx.api, name)(*args)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def group(ctx, gid=None):
    return next(g for g in ctx.db.list_groups() if g["id"] == (gid or ctx.gid))


def pass_challenge(ctx, ticket):
    shown = data(ctx.api.challenge_begin(ticket))
    ctx.clock.mono += len(shown["phrase"]) * guard.MIN_MS_PER_CHAR / 1000 + 1
    return ctx.api.challenge_submit(ticket, shown["phrase"])


def limit_form(day="2h"):
    return {"rule_type": "time_limit", "same": True, "day": day, "week": "", "month": ""}


def night_form(start="21:00", end="23:00"):
    return {"rule_type": "scheduled", "mode": "block", "allowance": "0", "pot": True,
            "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": start, "end": end}]}


def member(item_id, overrides=None, way=None):
    return {"id": item_id, "overrides": overrides or {}, "way": way}


def save(ctx, members, rules=None, ways=None, name="Fun", pending=None, gid=None):
    ways = ways if ways is not None else {"app_block": "close", "site_block": "dns"}
    rules = [limit_form()] if rules is None else rules
    return call(ctx, "draft_set_group", ctx.gid if gid is None else gid, name, rules, members, ways, pending)


def result(reply):
    got = data(reply)
    return got["result"]


def three(ctx):
    return [member(ctx.reddit), member(ctx.yt), member(ctx.game)]


# ---------------------------------------------------------------- what is registered

def test_the_group_methods_are_classified():
    kinds = dict(webapi.api_methods())
    assert kinds["groups_list"] == kinds["group_get"] == kinds["group_member_search"] == "read"
    assert kinds["draft_set_group"] == "loosen"
    assert kinds["draft_set_group_disabled"] == kinds["draft_remove_group"] == "loosen"      # (R18's, used by the editor)


# ---------------------------------------------------------------- the list

def test_the_list_describes_each_group(env):
    [card] = data(call(env, "groups_list"))["groups"]
    assert (card["name"], card["count"], card["names"]) == ("Fun", 3, ["Game", "Reddit", "YouTube"])
    assert card["ways"] == "Apps closed · sites can't load"
    assert [c["r"] for c in card["chips"]] == ["time_limit"] and "2h" in card["chips"][0]["t"]
    assert card["status"]["k"] == "allowed" and card["status"]["text"] == "Allowed now"
    assert card["disabled"] is False


def test_the_list_says_not_applied_disabled_and_blocked(env):
    autosave(env, False)
    data(call(env, "draft_set_group_disabled", env.gid, True))
    [card] = data(call(env, "groups_list"))["groups"]
    assert card["status"]["text"] == "Not applied (unsaved)"            # waiting for Save: not enforced yet
    data(call(env, "draft_save"))
    [card] = data(call(env, "groups_list"))["groups"]
    assert card["status"]["text"] == "Disabled - nothing is enforced" and card["disabled"] is True
    # a group blocking now: "k of n blocked now", and the end in the title ("Blocked (until ...)", once)
    data(call(env, "draft_set_group_disabled", env.gid, False))
    data(call(env, "draft_save"))
    env.db.update_group(env.gid, "Fun", [{"rule_type": "scheduled", "schedule": '{"mode": "block", "windows": [{"days": '
                                           '[0, 1, 2, 3, 4, 5, 6], "start": "15:00", "end": "18:00"}]}'}],
                        group(env)["members"])
    drafts.draft_of(env).reload()
    [card] = data(call(env, "groups_list"))["groups"]
    assert card["status"]["text"] == "3 of 3 blocked now" and card["status"]["title"] == "Blocked (until 18:00)"


def test_a_group_with_no_members_and_the_empty_list(env):
    env.db.remove_group(env.gid)
    assert data(call(env, "groups_list"))["groups"] == []
    gid = env.db.add_group("Empty", [dict(LIMIT_2H)], {})
    drafts.draft_of(env).reload()
    [card] = data(call(env, "groups_list"))["groups"]
    assert card["count"] == 0 and card["names"] == [] and card["status"]["k"] == "empty" and card["id"] == gid


# ---------------------------------------------------------------- one group for the editor

def test_group_get_gives_the_group_its_members_and_their_ways(env):
    env.db.update_group(env.gid, "Fun", [dict(LIMIT_2H)],
                        {env.reddit: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 30}}, env.yt: {},
                         env.game: {}}, False, {"app_block": "close", "site_block": "dns",
                                                "member_blocks": {env.game: "minimize", env.yt: "close"}})
    drafts.draft_of(env).reload()
    g = data(call(env, "group_get", env.gid))
    assert (g["name"], g["new"], g["disabled"]) == ("Fun", False, False)
    assert g["ways"] == {"app": "close", "site": "dns"}
    assert g["rules"] == [{"rule_type": "time_limit", "daily_limit_min": 120}]
    by = {m["name"]: m for m in g["members"]}
    assert list(by) == ["Game", "Reddit", "YouTube"]
    assert by["Game"]["way"] == "minimize" and by["Game"]["kind"] == "app" and by["Game"]["icon"] == {"k": "app", "n": "game.exe"}
    assert by["YouTube"]["way"] == "close" and by["Reddit"]["way"] is None
    assert by["Reddit"]["overrides"] == {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 30}}


def test_a_new_group_starts_as_an_item_does_and_a_missing_one_is_refused(env):
    g = data(call(env, "group_get"))
    assert g["new"] is True and g["ways"] == {"app": "close", "site": "dns"} and g["members"] == [] and g["rules"] == []
    assert not call(env, "group_get", 9999)["ok"]
    assert not call(env, "group_get", "x")["ok"]


def test_an_extra_temporary_block_that_has_run_out_is_not_shown(env):
    gone = (NOW - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    left = (NOW + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    env.db.update_group(env.gid, "Fun", [dict(LIMIT_2H)],
                        {env.reddit: {"temporary": {"rule_type": "temporary", "temp_until": gone}},
                         env.yt: {"temporary": {"rule_type": "temporary", "temp_until": left}}, env.game: {}})
    drafts.draft_of(env).reload()
    by = {m["name"]: m for m in data(call(env, "group_get", env.gid))["members"]}
    assert by["Reddit"]["overrides"] == {} and "temporary" in by["YouTube"]["overrides"]


def test_a_program_file_member_says_so(env):
    env.db.update_group(env.gid, "Fun", [dict(LIMIT_2H)], {env.jar: {}, env.game: {}})
    drafts.draft_of(env).reload()
    by = {m["name"]: m for m in data(call(env, "group_get", env.gid))["members"]}
    assert by["Tool"]["hosted"] is True and by["Game"]["hosted"] is False


# ---------------------------------------------------------------- the member search

def test_the_search_finds_listed_items_apps_and_a_website(env):
    nothing = data(call(env, "group_member_search", ""))
    assert nothing == {"items": [], "apps": [], "site": None, "loading": False}
    found = data(call(env, "group_member_search", "tube"))
    assert [i["name"] for i in found["items"]] == ["YouTube"] and found["items"][0]["icon"]["k"] == "site"
    apps = data(call(env, "group_member_search", "spot"))
    assert [a["exe"] for a in apps["apps"]] == ["spotify.exe"] and apps["items"] == []
    # an app that is on the list already is an item, not a second row
    assert [a["exe"] for a in data(call(env, "group_member_search", "game"))["apps"]] == []
    assert [i["name"] for i in data(call(env, "group_member_search", "game"))["items"]] == ["Game"]
    site = data(call(env, "group_member_search", "https://www.Example.com/x"))["site"]
    assert site == {"host": "example.com"}
    assert data(call(env, "group_member_search", "reddit.com"))["site"] is None            # (already on the list)
    assert data(call(env, "group_member_search", "no dot here"))["site"] is None
    assert not call(env, "group_member_search", 5)["ok"]


# ---------------------------------------------------------------- saving: the plain cases

def test_done_saves_changes_a_name_and_the_members(env):
    got = result(save(env, [member(env.reddit), member(env.game)], rules=[limit_form("90m")], name="  Time   sinks "))
    assert got == {"saved": True, "added": False, "name": "Time sinks"}
    g = group(env)
    assert g["name"] == "Time sinks" and g["rules"][0]["daily_limit_min"] == 60 + 30
    assert sorted(g["members"]) == sorted([env.reddit, env.game])                    # (YouTube came out: not locked, free)


def test_a_new_group_with_pending_members_creates_the_items(env):
    got = result(call(env, "draft_set_group", None, "Work", [limit_form("1h")], [member(env.reddit)],
                      {"app_block": "close", "site_block": None},
                      [{"kind": "site", "name": "https://www.Twitch.tv/x", "display": "Twitch"},
                       {"kind": "app", "name": "spotify.exe", "display": "Spotify", "path": r"C:\Spotify\spotify.exe"}]))
    assert got["saved"] and got["added"]
    g = next(g for g in env.db.list_groups() if g["name"] == "Work")
    items = {i["id"]: i for i in env.db.list_items()}
    assert sorted(items[i]["display_name"] for i in g["members"]) == ["Reddit", "Spotify", "Twitch"]
    twitch = next(i for i in items.values() if i["display_name"] == "Twitch")
    spotify = next(i for i in items.values() if i["display_name"] == "Spotify")
    assert (twitch["target"], twitch["item_type"], twitch["rules"]) == ("twitch.tv", "site", [])
    assert (spotify["target"], spotify["item_type"], spotify["block_type"], spotify["app_path"]) == \
        ("spotify.exe", "app", "close", r"C:\Spotify\spotify.exe")
    assert g["app_block"] == "close" and g["site_block"] is None


def test_a_pending_target_that_is_on_the_list_already_uses_that_item(env):
    got = result(call(env, "draft_set_group", None, "More", [limit_form()], [],
                      {"app_block": None, "site_block": None}, [{"kind": "site", "name": "reddit.com"}]))
    assert got["saved"]
    g = next(g for g in env.db.list_groups() if g["name"] == "More")
    assert list(g["members"]) == [env.reddit] and len(env.db.list_items()) == 4              # (no second Reddit)


def test_a_pending_target_is_judged_by_the_item_it_resolves_to(env):
    """The entry says "site" but tool.jar is a program file on the list: a site-style way is refused for it."""
    bad = call(env, "draft_set_group", None, "More", [limit_form()], [], {"app_block": None, "site_block": None},
               [{"kind": "site", "name": "tool.jar", "way": "dns,close"}])
    assert bad["ok"] is False and "way" in bad["error"]
    assert all(g["name"] != "More" for g in env.db.list_groups())
    ok = result(call(env, "draft_set_group", None, "More", [limit_form()], [],
                     {"app_block": None, "site_block": None}, [{"kind": "site", "name": "tool.jar"}]))
    assert ok["saved"]


def test_duplicates_are_refused_and_nothing_changes(env):
    autosave(env, False)
    got = result(save(env, three(env), pending=[{"kind": "site", "name": "reddit.com", "display": "Reddit"}]))
    assert got["saved"] is False and "already in this group" in got["errors"][0]["message"]
    twice = result(save(env, [member(env.reddit), member(env.reddit)]))
    assert twice["saved"] is False and "twice" in twice["errors"][0]["message"]
    new_twice = result(save(env, [], pending=[{"kind": "site", "name": "a.com"}, {"kind": "site", "name": "www.a.com"}]))
    assert new_twice["saved"] is False
    assert data(call(env, "draft_get_state"))["dirty"] is False


def test_done_needs_a_name_and_at_least_one_rule(env):
    autosave(env, False)
    got = result(save(env, three(env), rules=[], name="  "))
    assert got["saved"] is False
    assert {(e["field"]) for e in got["errors"]} == {"name", "rules"}
    assert [e["message"] for e in got["errors"] if e["field"] == "name"] == ["Give the group a name."]
    assert "Tick at least one blocker." in [e["message"] for e in got["errors"]]
    bad = result(save(env, three(env), rules=[limit_form("banana")]))
    assert bad["saved"] is False and bad["errors"][0]["rule_type"] == "time_limit"
    long = result(save(env, three(env), name="x" * 101))
    assert long["errors"][0]["field"] == "name"
    assert data(call(env, "draft_get_state"))["dirty"] is False                          # nothing reached the draft


def test_a_member_s_extra_limit_is_checked_like_any_other_form(env):
    bad = result(save(env, [member(env.reddit, {"time_limit": limit_form("nonsense")}), member(env.yt), member(env.game)]))
    assert bad["saved"] is False and bad["errors"][0]["member"] == "Reddit" and bad["errors"][0]["rule_type"] == "time_limit"
    ok_ = result(save(env, [member(env.reddit, {"time_limit": limit_form("30m")}), member(env.yt), member(env.game)]))
    assert ok_["saved"] is True
    assert group(env)["members"][env.reddit]["time_limit"]["daily_limit_min"] == 30
    for forbidden in ({"permanent": {"rule_type": "permanent"}}, {"time_limit": {"rule_type": "scheduled"}}):
        assert not save(env, [member(env.reddit, forbidden)])["ok"]


def test_the_disabled_flag_is_kept_and_orphans_are_dropped(env):
    env.db.update_group(env.gid, "Fun", [dict(LIMIT_2H)], group(env)["members"], True)
    drafts.draft_of(env).reload()
    got = result(save(env, [member(env.reddit)]))                    # YouTube and the game come out; they have no rules
    assert got["saved"]
    assert group(env)["disabled"] == 1 and list(group(env)["members"]) == [env.reddit]
    assert sorted(i["display_name"] for i in env.db.list_items()) == ["Reddit", "Tool"]            # (Tool was in no group)


def test_the_page_cannot_make_things_up(env):
    assert not call(env, "draft_set_group", "1", "A", [limit_form()], [], {})["ok"]
    assert not call(env, "draft_set_group", None, "A", [limit_form()], "all", {})["ok"]
    assert not call(env, "draft_set_group", None, "A", [limit_form()], [{"id": 9999}], {})["ok"]               # not on the list
    assert not call(env, "draft_set_group", None, "A", [limit_form()], [], {"confirmed": True})["ok"]
    assert not call(env, "draft_set_group", 9999, "A", [limit_form()], [], {})["ok"]
    assert not call(env, "draft_set_group", None, "A", [limit_form()], [], {}, [{"kind": "app", "name": "explorer.exe"}])["ok"]
    assert not call(env, "draft_set_group", None, "A", [limit_form()], [], {}, [{"kind": "site", "name": "not a site"}])["ok"]
    assert not call(env, "draft_set_group", None, "A", [limit_form()], [], {}, [{"kind": "file", "name": "x"}])["ok"]
    assert not call(env, "draft_set_group", None, "A", [limit_form()], [], {"app_block": "firewall,nonsense"})["ok"]
    assert not call(env, "draft_set_group", None, "A", [limit_form()], [member(env.game, way="sing")], {})["ok"]
    assert len(env.db.list_groups()) == 1


def test_ways_are_tidied_like_the_tick_boxes(env):
    got = result(save(env, three(env), ways={"app_block": "close,minimize,background", "site_block": "dns,close,back"}))
    assert got["saved"]
    g = group(env)
    assert (g["app_block"], g["site_block"]) == ("close,background", "dns,close")
    got = result(save(env, three(env), ways={"app_block": "background", "site_block": ""}))
    assert (group(env)["app_block"], group(env)["site_block"]) == (None, None)               # nothing chosen: each as set


def test_a_member_s_own_way_is_checked(env):
    # an app's own way needs close, minimize or internet (background alone is nothing)
    assert not save(env, [member(env.game, way="background")])["ok"]
    # a program file is closed or minimised only
    for way in ("internet", "close,background", "close,internet"):
        refused = save(env, [member(env.jar, way=way)])
        assert not refused["ok"] and "program file" in refused["error"], (way, refused)
    assert result(save(env, three(env) + [member(env.jar, way="minimize")]))["saved"]
    assert group(env)["member_blocks"] == {env.jar: "minimize"}
    # a site's own way is what it adds on top (close and back exclude each other)
    assert result(save(env, [member(env.reddit), member(env.yt, way="close,back"), member(env.game)]))["saved"]
    assert group(env)["member_blocks"] == {env.yt: "close"}


# ---------------------------------------------------------------- anti-bypass: what loosens while locked

def asks(ctx, reply, *needles):
    got = data(reply)
    assert got.get("needs_challenge") is True and got["ticket"], got
    for needle in needles:
        assert any(needle in c for c in got["changes"]), (needle, got["changes"])
    return got


def test_a_member_s_own_way_lighter_than_before_needs_the_challenge(env):
    env.db.update_group(env.gid, "Fun", [dict(LIMIT_2H)], group(env)["members"], False,
                        {"app_block": "close", "site_block": "dns", "member_blocks": {}})
    drafts.draft_of(env).reload()
    lock(env)
    got = asks(env, save(env, three(env)[:2] + [member(env.game, way="minimize")]), "Loosen group Fun")     # close -> minimize
    assert group(env)["member_blocks"] == {}                                                # nothing saved yet
    data(pass_challenge(env, got["ticket"]))
    assert group(env)["member_blocks"] == {env.game: "minimize"}


def test_a_member_s_own_way_stricter_is_free(env):
    env.db.update_group(env.gid, "Fun", [dict(LIMIT_2H)], group(env)["members"], False,
                        {"app_block": "minimize", "site_block": "dns", "member_blocks": {}})
    drafts.draft_of(env).reload()
    lock(env)
    got = result(save(env, three(env)[:2] + [member(env.game, way="close")], ways={"app_block": "minimize", "site_block": "dns"}))
    assert got["saved"] and group(env)["member_blocks"] == {env.game: "close"}              # minimize -> close: no challenge


def test_a_site_member_s_own_way_adds_on_top_and_taking_it_away_asks(env):
    lock(env)
    assert result(save(env, [member(env.reddit), member(env.yt, way="close"), member(env.game)]))["saved"]      # adds: free
    assert group(env)["member_blocks"] == {env.yt: "close"}
    asks(env, save(env, [member(env.reddit), member(env.yt), member(env.game)]), "Loosen group Fun")            # takes it away
    assert group(env)["member_blocks"] == {env.yt: "close"}


def test_the_group_s_own_ways_lighter_asks_and_heavier_is_free(env):
    lock(env)
    asks(env, save(env, three(env), ways={"app_block": "minimize", "site_block": "dns"}), "Loosen group Fun")
    drafts.draft_of(env).discard()
    got = result(save(env, three(env), ways={"app_block": "close,internet", "site_block": "dns,close"}))
    assert got["saved"] and group(env)["app_block"] == "close,internet"


def test_taking_a_member_out_while_locked_asks(env):
    lock(env)
    got = asks(env, save(env, [member(env.reddit), member(env.game)]), "Loosen group Fun")
    assert sorted(group(env)["members"]) == sorted([env.reddit, env.yt, env.game])
    data(pass_challenge(env, got["ticket"]))
    assert sorted(group(env)["members"]) == sorted([env.reddit, env.game])


def test_adding_a_member_is_free_while_locked(env):
    lock(env)
    got = result(save(env, three(env) + [member(env.jar)], pending=[{"kind": "site", "name": "twitch.tv"}]))
    assert got["saved"] and len(group(env)["members"]) == 5


def test_raising_a_shared_limit_asks_and_lowering_it_is_free(env):
    lock(env)
    asks(env, save(env, three(env), rules=[limit_form("3h")]), "Loosen group Fun")
    assert group(env)["rules"][0]["daily_limit_min"] == 120
    drafts.draft_of(env).discard()
    assert result(save(env, three(env), rules=[limit_form("1h")]))["saved"]
    assert group(env)["rules"][0]["daily_limit_min"] == 60


def test_taking_a_group_rule_away_asks(env):
    lock(env)
    env.db.update_group(env.gid, "Fun", [dict(LIMIT_2H), dict(NIGHT)], group(env)["members"])
    drafts.draft_of(env).reload()
    asks(env, save(env, three(env), rules=[limit_form("2h")]), "Loosen group Fun")           # the night rule is gone
    drafts.draft_of(env).discard()
    asks(env, save(env, three(env), rules=[limit_form("2h"), night_form("22:00", "23:00")]), "Loosen group Fun")   # shorter hours
    drafts.draft_of(env).discard()
    assert result(save(env, three(env), rules=[limit_form("2h"), night_form("20:00", "23:30")]))["saved"]       # longer hours: free


def test_a_loosened_extra_limit_asks_added_or_tightened_is_free_removed_asks(env):
    lock(env)
    assert result(save(env, [member(env.reddit, {"time_limit": limit_form("30m")}), member(env.yt), member(env.game)]))["saved"]     # added: free
    assert result(save(env, [member(env.reddit, {"time_limit": limit_form("20m")}), member(env.yt), member(env.game)]))["saved"]    # tighter: free
    asks(env, save(env, [member(env.reddit, {"time_limit": limit_form("45m")}), member(env.yt), member(env.game)]), "Loosen group Fun")
    assert group(env)["members"][env.reddit]["time_limit"]["daily_limit_min"] == 20
    drafts.draft_of(env).discard()
    asks(env, save(env, three(env)), "Loosen group Fun")                                    # the extra limit removed
    assert group(env)["members"][env.reddit]["time_limit"]["daily_limit_min"] == 20


def test_an_unchanged_group_saved_again_never_asks(env):
    """Done on a group nobody edited: the forms the page makes of the saved rules read back as the same rules."""
    lock(env)
    env.db.update_group(env.gid, "Fun", [dict(LIMIT_2H), dict(NIGHT)],
                        {env.reddit: {"time_limit": {"rule_type": "time_limit", "daily_limit_min": 30}}, env.yt: {}, env.game: {}},
                        False, {"app_block": "close", "site_block": "dns", "member_blocks": {env.yt: "close"}})
    drafts.draft_of(env).reload()
    g = data(call(env, "group_get", env.gid))
    forms = [rule_forms.form_from_rule(r, True) for r in g["rules"]]
    members = [member(m["id"], {t: rule_forms.form_from_rule(r) for t, r in m["overrides"].items()}, m["way"])
               for m in g["members"]]
    got = save(env, members, rules=forms, ways={"app_block": g["ways"]["app"], "site_block": g["ways"]["site"]})
    assert "needs_challenge" not in data(got) and data(got)["result"]["saved"]
    assert group(env)["members"][env.reddit]["time_limit"]["daily_limit_min"] == 30 and \
        {r["rule_type"]: r for r in group(env)["rules"]}["time_limit"]["daily_limit_min"] == 120


def test_a_cancelled_challenge_keeps_the_group_as_it_was(env):
    lock(env)
    got = asks(env, save(env, [member(env.reddit)]))
    data(env.api.challenge_cancel(got["ticket"]))
    assert sorted(group(env)["members"]) == sorted([env.reddit, env.yt, env.game])
    assert data(call(env, "draft_get_state"))["dirty"] is False                             # (auto-save: the edit is thrown away)


def test_without_autosave_the_change_waits_for_save_and_the_challenge_comes_then(env):
    autosave(env, False)
    lock(env)
    got = result(save(env, [member(env.reddit)]))
    assert got["saved"] and sorted(group(env)["members"]) == sorted([env.reddit, env.yt, env.game])
    state = data(call(env, "draft_get_state"))
    assert state["dirty"] and "Changed group Fun" in state["changes"]
    asks(env, call(env, "draft_save"), "Loosen group Fun")


def test_removing_and_disabling_a_group_go_through_the_same_guard(env):
    lock(env)
    asks(env, call(env, "draft_set_group_disabled", env.gid, True), "Disable group Fun")
    asks(env, call(env, "draft_remove_group", env.gid), "Remove group Fun")
    assert not group(env)["disabled"] and len(env.db.list_groups()) == 1


def test_a_new_group_while_locked_is_free(env):
    lock(env)
    got = result(call(env, "draft_set_group", None, "Night", [night_form()], [member(env.reddit)],
                      {"app_block": None, "site_block": None}))
    assert got["saved"] and got["added"] and len(env.db.list_groups()) == 2


def test_a_group_s_first_choice_is_compared_with_the_way_each_member_had_before(env):
    """The group never chose: the game is blocked the way it was set ("close,internet"). Choosing "close" for the group
    would drop the internet cut for it - lighter - so the challenge; choosing "close,internet" keeps it - free."""
    env.db.update_item(env.game, "Game", ["game.exe"], None, [], block_type="close,internet")
    env.db.update_group(env.gid, "Fun", [dict(LIMIT_2H)], group(env)["members"], False,
                        {"app_block": None, "site_block": None, "member_blocks": {}})
    drafts.draft_of(env).reload()
    lock(env)
    asks(env, save(env, three(env), ways={"app_block": "close", "site_block": None}), "Loosen group Fun")
    drafts.draft_of(env).discard()
    got = result(save(env, three(env), ways={"app_block": "close,internet", "site_block": None}))
    assert got["saved"] and group(env)["app_block"] == "close,internet"


def test_a_forged_keep_that_shortens_a_running_extra_block_keeps_the_saved_end(env):
    """The page can only send a form: 'keep' keeps the SAVED end of the running block (R28) - an earlier keep_until from
    the page is not read, so nothing gets shorter."""
    left = (NOW + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    sooner = (NOW + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    env.db.update_group(env.gid, "Fun", [dict(LIMIT_2H)],
                        {env.reddit: {"temporary": {"rule_type": "temporary", "temp_until": left}}, env.yt: {}, env.game: {}})
    drafts.draft_of(env).reload()
    lock(env)
    forged = {"rule_type": "temporary", "choice": "keep", "keep_until": sooner}
    assert result(save(env, [member(env.reddit, {"temporary": forged}), member(env.yt), member(env.game)]))["saved"]
    assert group(env)["members"][env.reddit]["temporary"]["temp_until"] == left


def test_a_group_keep_keeps_the_saved_end_of_its_own_running_block(env):
    """The same for the group's own temporary blocker: 'keep' with a page end a minute short is the saved end."""
    left = (NOW + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    short = (NOW + timedelta(hours=5, minutes=-1)).strftime("%Y-%m-%d %H:%M:%S")
    env.db.update_group(env.gid, "Fun", [{"rule_type": "temporary", "temp_until": left}], {env.reddit: {}, env.yt: {}, env.game: {}})
    drafts.draft_of(env).reload()
    lock(env)
    keep = {"rule_type": "temporary", "choice": "keep", "keep_until": short}
    assert result(save(env, three(env), rules=[keep]))["saved"]
    assert [r["temp_until"] for r in group(env)["rules"]] == [left]
