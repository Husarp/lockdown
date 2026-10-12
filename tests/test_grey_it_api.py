"""Grey it over the bridge: Blocking > Add / Edit and the Groups editor (webapi/blocking_edit.py, webapi/groups.py) accept it, tidy
it (it goes with neither Close nor Minimize, never with a program file) and Python decides what loosens (the challenge)."""
import pytest

from test_rebuild_add_edit import PERMANENT, data, edit, env, items, lock, new, pass_challenge  # noqa: F401


def test_a_new_app_can_be_greyed_and_the_way_is_saved_as_grey(env):
    got = new(env, {"kind": "app", "target": "foo.exe", "ways": ["grey"], "rules": [PERMANENT]})
    assert got["result"] == "added"
    assert items(env)["Foo"]["block_type"] == "grey"
    got = new(env, {"kind": "app", "target": "bar.exe", "ways": ["internet", "grey"], "rules": [PERMANENT]})
    assert got["result"] == "added" and items(env)["Bar"]["block_type"] == "grey,internet"


def test_a_category_can_grey_its_apps(env):
    env.db.add_item("Games", ["games"], "category", "category", [{"rule_type": "permanent"}], None, "close")
    cat = items(env)["Games"]["id"]
    assert edit(env, cat, {"ways": ["grey"], "rules": [PERMANENT]})["result"] == "saved"
    assert items(env)["Games"]["block_type"] == "grey"


def test_grey_cannot_go_with_close_or_minimize(env):
    for ways in (["close", "grey"], ["minimize", "grey"], ["close", "background", "grey"]):
        got = new(env, {"kind": "app", "target": "foo.exe", "ways": ways, "rules": [PERMANENT]})
        assert got["result"] == "invalid" and "Grey it can't go with Close app or Minimize" in got["errors"][0]["message"], ways
    assert "Foo" not in items(env)


def test_a_program_file_cannot_be_greyed(env):
    for ways in (["grey"], ["close", "grey"]):
        got = new(env, {"kind": "app", "target": "game.jar", "ways": ways, "rules": [PERMANENT]})
        assert got["result"] == "invalid", ways
    assert "Tick Close app or Minimize" in new(env, {"kind": "app", "target": "game.jar", "ways": ["grey"], "rules": [PERMANENT]})["errors"][0]["message"]


def test_a_site_has_no_grey(env):
    with pytest.raises(AssertionError):
        data(env.api.draft_submit_item({"mode": "new", "kind": "site", "target": "reddit.com", "ways": ["grey"], "rules": [PERMANENT]}))


def test_the_item_comes_back_with_grey_ticked(env):
    new(env, {"kind": "app", "target": "foo.exe", "ways": ["grey", "internet"], "rules": [PERMANENT]})
    got = data(env.api.item_get(items(env)["Foo"]["id"]))
    assert got["ways"] == ["grey", "internet"]


def test_switching_to_grey_asks_for_the_challenge_and_back_to_close_is_free(env):
    app = env.db.add_item("Foo", ["foo.exe"], "app", "manual", [{"rule_type": "permanent"}], None, "close")
    mini = env.db.add_item("Bar", ["bar.exe"], "app", "manual", [{"rule_type": "permanent"}], None, "minimize")
    lock(env)
    from_minimize = edit(env, mini, {"ways": ["grey"], "rules": [PERMANENT]})
    assert from_minimize["needs_challenge"] is True and "Loosen Bar" in from_minimize["changes"]
    data(env.api.challenge_cancel(from_minimize["ticket"]))
    assert items(env)["Bar"]["block_type"] == "minimize"
    lighter = edit(env, app, {"ways": ["grey"], "rules": [PERMANENT]})
    assert lighter["needs_challenge"] is True and "Loosen Foo" in lighter["changes"]
    assert items(env)["Foo"]["block_type"] == "close", "nothing is applied before the challenge"
    data(pass_challenge(env, lighter["ticket"]))
    assert items(env)["Foo"]["block_type"] == "grey"
    assert edit(env, app, {"ways": ["close"], "rules": [PERMANENT]})["result"] == "saved"          # grey -> close: free
    assert items(env)["Foo"]["block_type"] == "close"


def test_grey_to_minimize_and_grey_plus_internet_are_free(env):
    app = env.db.add_item("Foo", ["foo.exe"], "app", "manual", [{"rule_type": "permanent"}], None, "grey")
    lock(env)
    assert edit(env, app, {"ways": ["grey", "internet"], "rules": [PERMANENT]})["result"] == "saved"
    assert edit(env, app, {"ways": ["minimize", "internet"], "rules": [PERMANENT]})["result"] == "saved"
    assert items(env)["Foo"]["block_type"] == "minimize,internet"


def test_taking_the_internet_cut_away_from_a_grey_app_asks(env):
    app = env.db.add_item("Foo", ["foo.exe"], "app", "manual", [{"rule_type": "permanent"}], None, "grey,internet")
    lock(env)
    assert edit(env, app, {"ways": ["grey"], "rules": [PERMANENT]})["needs_challenge"] is True


def test_a_mode_s_extra_app_has_no_grey_option(env):
    """A mode that blocks an app closes it: the Modes editor doesn't offer Grey it, and Python refuses it."""
    reply = env.api.mode_extra_check({"kind": "app", "text": "foo.exe", "ways": ["grey"]})
    assert reply["ok"] is False and "how it is blocked" in reply["error"]
    assert env.api.mode_extra_check({"kind": "app", "text": "foo.exe", "ways": ["minimize"]})["ok"]
