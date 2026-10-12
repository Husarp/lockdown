"""Grey it in a group (webapi/groups.py): the group's "How apps are blocked", an app member's own way - tidied, never for a
program file, and a lighter way than before is the challenge (antibypass.ways_looser) while a heavier one is free."""
import block_method
from test_rebuild_groups import (asks, call, data, drafts, env, group, lock, member, pass_challenge, result, save,  # noqa: F401
                                 three)
from test_rebuild_groups import LIMIT_2H


def groups_way(ctx, app_block, member_blocks=None):
    ctx.db.update_group(ctx.gid, "Fun", [dict(LIMIT_2H)], group(ctx)["members"], False,
                        {"app_block": app_block, "site_block": "dns", "member_blocks": member_blocks or {}})
    drafts.draft_of(ctx).reload()


def test_a_group_can_grey_its_apps_and_the_way_is_tidied(env):
    assert result(save(env, three(env), ways={"app_block": "grey", "site_block": "dns"}))["saved"]
    assert group(env)["app_block"] == "grey"
    assert result(save(env, three(env), ways={"app_block": "grey,internet", "site_block": "dns"}))["saved"]
    assert group(env)["app_block"] == "grey,internet"
    # closing covers it, minimizing covers it: the boxes never allow both, and Python makes the same of it
    assert result(save(env, three(env), ways={"app_block": "close,grey", "site_block": "dns"}))["saved"]
    assert group(env)["app_block"] == "close"
    assert result(save(env, three(env), ways={"app_block": "minimize,grey,internet", "site_block": "dns"}))["saved"]
    assert group(env)["app_block"] == "minimize,internet"


def test_the_group_blocks_its_apps_the_group_s_way_grey(env):
    groups_way(env, "grey")
    now = env.clock.now
    env.db.update_group(env.gid, "Fun", [{"rule_type": "permanent"}], group(env)["members"], False,
                        {"app_block": "grey", "site_block": "dns", "member_blocks": {}})
    way = next(b["item"]["block_type"] for b in env.db.blocks(now) if b["item"]["id"] == env.game)
    assert way == "grey"                                # the app's own "close" is replaced by the group's choice


def test_a_member_app_can_have_grey_as_its_own_way(env):
    assert result(save(env, [member(env.reddit), member(env.yt), member(env.game, way="grey")]))["saved"]
    assert group(env)["member_blocks"] == {env.game: "grey"}
    assert result(save(env, [member(env.reddit), member(env.yt), member(env.game, way="close,grey")]))["saved"]
    assert group(env)["member_blocks"] == {env.game: "close"}
    assert result(save(env, [member(env.reddit), member(env.yt), member(env.game, way="grey,internet")]))["saved"]
    assert group(env)["member_blocks"] == {env.game: "grey,internet"}


def test_a_program_file_member_cannot_be_greyed(env):
    for way in ("grey", "grey,internet", "close,grey"):
        refused = save(env, three(env) + [member(env.jar, way=way)])
        assert not refused["ok"] and "program file" in refused["error"], way


def test_a_site_has_no_grey_way(env):
    assert not save(env, [member(env.reddit), member(env.yt, way="grey"), member(env.game)])["ok"]
    assert not save(env, three(env), ways={"app_block": "close", "site_block": "grey"})["ok"]


def test_the_group_s_way_going_to_grey_asks_and_going_back_is_free(env):
    lock(env)
    got = asks(env, save(env, three(env), ways={"app_block": "grey", "site_block": "dns"}), "Loosen group Fun")
    assert group(env)["app_block"] == "close"
    data(env.api.challenge_cancel(got["ticket"]))
    drafts.draft_of(env).discard()
    groups_way(env, "grey")
    assert result(save(env, three(env), ways={"app_block": "close", "site_block": "dns"}))["saved"]
    assert group(env)["app_block"] == "close"
    groups_way(env, "grey")
    assert result(save(env, three(env), ways={"app_block": "minimize", "site_block": "dns"}))["saved"]


def test_minimize_to_grey_asks_for_the_whole_group(env):
    groups_way(env, "minimize")
    lock(env)
    asks(env, save(env, three(env), ways={"app_block": "grey", "site_block": "dns"}), "Loosen group Fun")


def test_a_member_s_own_way_going_to_grey_asks_and_grey_to_close_is_free(env):
    lock(env)
    got = asks(env, save(env, three(env)[:2] + [member(env.game, way="grey")]), "Loosen group Fun")
    assert group(env)["member_blocks"] == {}
    data(env.api.challenge_cancel(got["ticket"]))
    drafts.draft_of(env).discard()
    groups_way(env, "close", {env.game: "grey"})
    assert result(save(env, three(env)[:2] + [member(env.game, way="close")]))["saved"]
    assert group(env)["member_blocks"] == {env.game: "close"}


def test_the_group_says_how_it_blocks_its_apps(env):
    groups_way(env, "grey")
    from webapi import groups
    assert groups._ways_text(group(env)) == "Apps greyed · sites can't load"
    groups_way(env, "grey,internet")
    assert groups._ways_text(group(env)) == "Apps greyed + internet cut · sites can't load"


def test_the_member_row_shows_greyed_as_its_own_way(env):
    groups_way(env, "close", {env.game: "grey"})
    members = data(call(env, "group_get", env.gid))["members"]
    assert {m["name"]: m["way"] for m in members}["Game"] == "grey"
    assert block_method.member_own(group(env), env.game) == "grey"
