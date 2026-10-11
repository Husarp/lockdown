"""R12: the Api behind the category editor, the right-click menu of an app / site and the App browser
(webapi/categories.py) - which changes need the Anti-Bypass challenge (all but recolouring), what is refused, and that
the list is the same one the old window keeps."""
import random
from datetime import datetime

import pytest

import antibypass as ab
import webapi
from webapi import categories as page
from webapi import core
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


@pytest.fixture
def ctx(tmp_path):
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    c.guard = GuardService(lambda: c.db, lambda db: NOW, rng=random.Random(5))
    return c


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


def lock(ctx):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def ticket_of(reply):
    d = data(reply)
    assert d.get("needs_challenge") and d.get("ticket"), d
    return d["ticket"]


def pass_challenge(ctx, api, ticket):
    begin = data(api.challenge_begin(ticket))
    mono = ctx.guard._mono
    ctx.guard._mono = lambda: mono() + 60
    return data(api.challenge_submit(ticket, begin["phrase"]))


def keys(ctx):
    return [c["key"] for c in page.load(ctx.db)]


# ---------------------------------------------------------------- the list

def test_methods_are_classified():
    kinds = dict(webapi.api_methods())
    for name in ("categories_list", "blocklist_brief", "appmenu_info"):
        assert kinds[name] == "read", name
    for name in ("category_add", "category_remove", "category_assign", "category_add_and_assign"):
        assert kinds[name] == "loosen", name
    assert kinds["category_set_color"] == "ui" and kinds["reveal_in_explorer"] == "ui"
    assert kinds["draft_add_to_group"] == "tighten"


def test_the_list_starts_with_the_built_ins(api):
    rows = data(api.categories_list())
    assert [r["key"] for r in rows] == ["productive", "neutral", "games", "distracting"]
    assert all(r["builtin"] for r in rows)
    assert rows[0]["color"] == "#27AE60" and rows[0]["color_light"] == "#1E7A46"      # dark / light variants


def test_same_list_and_storage_as_the_old_window(ctx):
    """gui/categories.py imports tkinter, so its few lines are repeated: this keeps both in step."""
    legacy = pytest.importorskip("gui.categories")
    assert page.KEY == legacy.KEY and page.NEW_COLORS == legacy.NEW_COLORS
    assert [(k, n) for k, n, _c in page.BUILTIN] == [(k, n) for k, n, _c in legacy.BUILTIN]
    key = legacy.add(ctx.db, "Reading", "#2f9fd8")
    legacy.set_color(ctx.db, "neutral", "#123456")
    mine = {c["key"]: c for c in page.load(ctx.db)}
    assert mine[key]["name"] == "Reading" and mine[key]["color"] == "#2f9fd8" and not mine[key]["builtin"]
    assert mine["neutral"]["color"] == mine["neutral"]["color_light"] == "#123456"
    assert [c["key"] for c in legacy.load(ctx.db)] == [c["key"] for c in page.load(ctx.db)]
    # and the other way round: what this module writes, the old window reads
    key2 = data(core.build_api(ctx).category_add("Chores", "#d4a017"))["key"]
    assert key2 in [c["key"] for c in legacy.load(ctx.db)]


# ---------------------------------------------------------------- free: status "free" writes at once

def test_when_free_add_assign_and_remove_are_written(ctx, api):
    made = data(api.category_add("Reading", "#2F9FD8"))
    assert made == {"key": "reading", "name": "Reading", "color": "#2f9fd8"}
    assert "reading" in keys(ctx)
    out = data(api.category_assign("app", "Kindle.exe", "reading"))
    assert out == {"kind": "app", "name": "kindle.exe", "category": "reading"}
    assert ctx.db.categories()[("app", "kindle.exe")] == "reading"
    data(api.category_remove("reading"))
    assert "reading" not in keys(ctx)
    assert ("app", "kindle.exe") not in ctx.db.categories()           # back to its default


def test_add_and_assign_is_one_change(ctx, api):
    out = data(api.category_add_and_assign("site", "Reddit.com", "Time sink", "#e0559b"))
    assert out["category"] == "time-sink" and out["name"] == "reddit.com"
    assert ctx.db.categories()[("site", "reddit.com")] == "time-sink"
    assert "time-sink" in keys(ctx)


def test_a_name_can_be_used_once_but_two_names_may_make_the_same_key(ctx, api):
    data(api.category_add("Fun!", "#2f9fd8"))
    reply = api.category_add("fun!", "#2f9fd8")                       # same name (any case): refused
    assert not reply["ok"] and "already" in reply["error"]
    data(api.category_add("Fun?", "#2f9fd8"))                         # another name that makes the key "fun" too
    assert {"fun", "fun-2"} <= set(keys(ctx))


# ---------------------------------------------------------------- locked: ticket, nothing written

def test_locked_add_assign_remove_give_a_ticket_and_write_nothing(ctx, api):
    data(api.category_add("Reading", "#2f9fd8"))                      # (made while free)
    ctx.db.set_category("app", "a.exe", "neutral")
    lock(ctx)
    before = ctx.db.get_setting(page.KEY, "")
    for reply in (api.category_add("Chores", "#2f9fd8"),
                  api.category_assign("app", "a.exe", "distracting"),     # tighter...
                  api.category_assign("app", "a.exe", "productive"),      # ... and looser: both guarded
                  api.category_add_and_assign("app", "a.exe", "New", "#2f9fd8"),
                  api.category_remove("reading")):
        ticket_of(reply)
    assert ctx.db.get_setting(page.KEY, "") == before
    assert ctx.db.categories() == {("app", "a.exe"): "neutral"}


def test_the_challenge_makes_the_change(ctx, api):
    lock(ctx)
    t = ticket_of(api.category_assign("site", "youtube.com", "distracting"))
    assert ("site", "youtube.com") not in ctx.db.categories()
    pass_challenge(ctx, api, t)
    assert ctx.db.categories()[("site", "youtube.com")] == "distracting"


def test_the_challenge_lists_what_changes(ctx, api):
    lock(ctx)
    d = data(api.category_assign("app", "a.exe", "games"))
    assert d["changes"] == ["Move a.exe to the Games category"]


def test_cancelling_the_challenge_changes_nothing(ctx, api):
    lock(ctx)
    t = ticket_of(api.category_add("Chores", "#2f9fd8"))
    data(api.challenge_cancel(t))
    assert "chores" not in keys(ctx)
    assert not api.challenge_submit(t, "x")["ok"]                      # the ticket is gone


def test_a_client_flag_is_not_an_argument(api):
    reply = api.category_assign("app", "a.exe", "neutral", True)
    assert not reply["ok"] and "Bad arguments" in reply["error"]


def test_recolouring_is_free_even_when_locked(ctx, api):
    lock(ctx)
    data(api.category_set_color("neutral", "#AABBCC"))
    mine = {c["key"]: c for c in page.load(ctx.db)}
    assert mine["neutral"]["color"] == "#aabbcc"


# ---------------------------------------------------------------- refusals

def test_a_built_in_category_cannot_be_removed(ctx, api):
    for key in ("productive", "neutral", "games", "distracting"):
        reply = api.category_remove(key)
        assert not reply["ok"] and "built-in" in reply["error"]
    assert keys(ctx)[:4] == ["productive", "neutral", "games", "distracting"]


def test_unknown_things_are_refused(api):
    assert not api.category_remove("nope")["ok"]
    assert not api.category_assign("app", "a.exe", "nope")["ok"]
    assert not api.category_assign("file", "a.exe", "neutral")["ok"]
    assert not api.category_assign("app", "", "neutral")["ok"]
    assert not api.category_assign("site", "two words", "neutral")["ok"]
    assert not api.category_assign("app", ["a.exe"], "neutral")["ok"]
    assert not api.category_set_color("nope", "#ffffff")["ok"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 31, "a\x00b", 5, None])
def test_bad_names_are_refused(api, name):
    assert not api.category_add(name, "#ffffff")["ok"]


@pytest.mark.parametrize("color", ["red", "#fff", "#12345g", "ffffff", "#ffffff;", None, 7])
def test_bad_colours_are_refused(api, color):
    assert not api.category_add("Fine", color)["ok"]
    assert not api.category_set_color("neutral", color)["ok"]


def test_the_name_is_tidied(ctx, api):
    assert data(api.category_add("  Deep   work ", "#ffffff"))["name"] == "Deep work"


def test_a_category_removed_while_the_challenge_was_open_is_not_assigned(ctx, api):
    data(api.category_add("Reading", "#2f9fd8"))
    lock(ctx)
    t = ticket_of(api.category_assign("app", "a.exe", "reading"))
    page._remove(ctx.db, "reading")                                    # (gone behind its back)
    reply = api.challenge_submit(t, data(api.challenge_begin(t))["phrase"])
    assert not reply["ok"]
    assert ("app", "a.exe") not in ctx.db.categories()


# ---------------------------------------------------------------- the menu's facts

def test_appmenu_info(ctx, api):
    ctx.db.set_category("app", "a.exe", "games")
    info = data(api.appmenu_info("app", "A.exe"))
    assert info == {"category": "games", "protected": False, "groups": []}
    assert data(api.appmenu_info("app", "lsass.exe"))["protected"] is True
    assert data(api.appmenu_info("site", "lsass.exe"))["protected"] is False
    assert data(api.appmenu_info("site", "example.com"))["category"] == "neutral"


def test_appmenu_info_lists_the_groups(ctx, api):
    ctx.db.add_group("Zeta", [], {})
    ctx.db.add_group("alpha", [], {})
    assert [g["name"] for g in data(api.appmenu_info("app", "a.exe"))["groups"]] == ["alpha", "Zeta"]


def test_a_deleted_categorys_choice_falls_back_to_neutral(ctx, api):
    data(api.category_add("Reading", "#2f9fd8"))
    ctx.db.set_category("app", "a.exe", "reading")
    page._config(ctx.db)
    cfg = page._config(ctx.db)
    cfg["custom"] = []                                                  # (the category vanished, the row stayed)
    import json
    ctx.db.set_setting(page.KEY, json.dumps(cfg))
    assert data(api.appmenu_info("app", "a.exe"))["category"] == "neutral"


def test_blocklist_brief(ctx, api):
    ctx.db.add_item("YouTube", ["youtube.com"], "site", "manual", [{"rule_type": "permanent"}])
    ctx.db.add_item("Steam", ["steam.exe"], "app", "manual", [{"rule_type": "permanent"}])
    ctx.db.add_group("Study", [], {})
    brief = data(api.blocklist_brief())
    assert [i["name"] for i in brief["items"]] == ["Steam", "YouTube"]
    assert brief["items"][0]["kind"] == "app" and brief["items"][1]["target"] == "youtube.com"
    assert brief["groups"] == [{"id": brief["groups"][0]["id"], "name": "Study", "count": 0, "disabled": False}]


# ---------------------------------------------------------------- Add to group

def _group(ctx, name="Study", rules=None):
    rules = rules if rules is not None else [{"rule_type": "permanent"}]
    return ctx.db.add_group(name, rules, {})


@pytest.fixture(params=["shared draft", "no draft module"])
def draft_mode(request, monkeypatch):
    """Both ways add-to-group can run: on the agent's Draft (webapi/draft.py) and, without that module, on a fresh one."""
    if request.param == "no draft module":
        monkeypatch.setattr(page, "_shared_draft", lambda: None)
    return request.param


def test_add_to_group_puts_the_app_on_the_list_and_in_the_group(ctx, api, draft_mode):
    gid = _group(ctx)
    out = data(api.draft_add_to_group({"kind": "app", "name": "Game.exe", "display": "My Game"}, gid))
    assert (out["added"], out["notice"]) == (True, "My Game added to Study")
    item = next(i for i in ctx.db.list_items() if i["target"] == "game.exe")
    assert item["display_name"] == "My Game" and item["item_type"] == "app"
    group = next(g for g in ctx.db.list_groups() if g["id"] == gid)
    assert list(group["members"]) == [item["id"]]
    again = data(api.draft_add_to_group({"kind": "app", "name": "game.exe"}, gid))
    assert again["added"] is False and "already in" in again["notice"]
    assert len([i for i in ctx.db.list_items() if i["target"] == "game.exe"]) == 1


def test_add_a_site_to_a_group_even_while_locked(ctx, api, draft_mode):
    gid = _group(ctx)
    lock(ctx)                                                           # tightening is free
    out = data(api.draft_add_to_group({"kind": "site", "name": "https://www.Reddit.com/r/x"}, gid))
    assert out["added"] is True and "needs_challenge" not in out
    assert any(i["target"] == "reddit.com" for i in ctx.db.list_items())


def test_add_to_group_refuses_protected_and_unknown(ctx, api):
    gid = _group(ctx)
    assert not api.draft_add_to_group({"kind": "app", "name": "lsass.exe"}, gid)["ok"]
    assert not api.draft_add_to_group({"kind": "app", "name": "a.exe"}, gid + 99)["ok"]
    assert not api.draft_add_to_group({"kind": "app", "name": "a.exe"}, True)["ok"]
    assert not api.draft_add_to_group("a.exe", gid)["ok"]
    assert not api.draft_add_to_group({"kind": "site", "name": "not a host"}, gid)["ok"]
    assert not api.draft_add_to_group({"kind": "site", "name": "localhost"}, gid)["ok"]
    assert ctx.db.list_items() == []


def test_add_to_group_uses_the_agents_draft_when_there_is_one(ctx, api):
    """With R18's shared Draft (ctx.draft) the change stays in the draft: the page's Save bar decides."""
    from gui.draft import Draft
    gid = _group(ctx)
    draft = Draft(ctx.db)
    draft.db.set_setting("ui.autosave", "0")
    ctx.draft = draft
    data(api.draft_add_to_group({"kind": "site", "name": "reddit.com"}, gid))
    assert draft.dirty and ctx.db.list_items() == []                    # not saved yet
    assert [g["name"] for g in data(api.appmenu_info("site", "x.com"))["groups"]] == ["Study"]
    draft.save()
    assert [i["target"] for i in ctx.db.list_items()] == ["reddit.com"]


def test_an_app_path_is_only_kept_when_it_is_a_known_one(ctx, api, monkeypatch):
    gid = _group(ctx)
    monkeypatch.setattr(page, "_known_path", lambda c, p: p if p == r"C:\Games\g.exe" else None)
    data(api.draft_add_to_group({"kind": "app", "name": "g.exe", "path": r"C:\Games\g.exe"}, gid))
    data(api.draft_add_to_group({"kind": "app", "name": "h.exe", "path": r"C:\Evil\h.exe"}, gid))
    paths = {i["target"]: i["app_path"] for i in ctx.db.list_items()}
    assert paths["g.exe"] == r"C:\Games\g.exe" and not paths["h.exe"]


# ---------------------------------------------------------------- Show in Explorer

class FakeCatalog:
    def __init__(self, apps):
        self.apps = apps

    def search(self, kind="all", query="", limit=60):
        return {"apps": [a for a in self.apps if query.lower() in a["exe"]], "total": 1, "loading": False}


@pytest.fixture
def explorer(ctx, monkeypatch):
    ctx.catalog = FakeCatalog([{"name": "Chrome", "exe": "chrome.exe", "path": r"C:\Program Files\Google\chrome.exe",
                                "running": False, "steam": False}])
    opened = []
    monkeypatch.setattr(page, "_open_explorer", opened.append)
    return opened


def test_reveal_opens_only_known_paths(ctx, api, explorer):
    assert data(api.reveal_in_explorer(r"C:\Program Files\Google\chrome.exe")) is None
    assert data(api.reveal_in_explorer("c:/program files/google/CHROME.EXE")) is None      # same file, other spelling
    assert explorer == [r"C:\Program Files\Google\chrome.exe"] * 2                        # the catalog's own spelling
    for bad in (r"C:\Windows\System32\cmd.exe", r"C:\Other\chrome.exe", r"C:\Program Files\Google", "", "x\x00y",
                "https://example.com", r"\\evil\share\chrome.exe", None, 5):
        reply = api.reveal_in_explorer(bad)
        assert not reply["ok"], bad
    assert len(explorer) == 2


def test_reveal_knows_the_paths_of_your_blocked_apps(ctx, api, explorer):
    ctx.db.add_item("Mine", ["mine.exe"], "app", "manual", [{"rule_type": "permanent"}], None, None, r"D:\Tools\mine.exe")
    assert data(api.reveal_in_explorer(r"D:\Tools\mine.exe")) is None
    assert explorer[-1] == r"D:\Tools\mine.exe"
    assert not api.reveal_in_explorer(r"D:\Tools\other.exe")["ok"]


def test_reveal_says_so_off_windows(ctx, api, monkeypatch):
    ctx.catalog = FakeCatalog([{"name": "C", "exe": "c.exe", "path": r"C:\c.exe", "running": False, "steam": False}])
    monkeypatch.setattr(page.sys, "platform", "linux")
    reply = api.reveal_in_explorer(r"C:\c.exe")
    assert not reply["ok"] and "Windows" in reply["error"]
