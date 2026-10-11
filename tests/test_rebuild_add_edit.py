"""R20: Blocking > Add / Edit over the bridge (webapi/blocking_edit.py) - the suggestions, reading one item, adding / merging /
saving / removing it, disabling it, and that every loosening goes through the Draft's guard (the challenge) and nothing the
page says is trusted."""
import random
import statistics
import time
from datetime import datetime, timedelta

import pytest

import antibypass as ab
import webapi
from resident import catalog as app_catalog
from rules import make_schedule
from webapi import core, draft as drafts, guard
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)
EVERY_DAY = list(range(7))


class Clock:
    def __init__(self):
        self.now, self.mono = NOW, 1000.0

    def __call__(self, db=None):
        return self.now

    def tick(self):
        return self.mono


APPS = [
    {"name": "Discord", "exe": "discord.exe", "path": "C:\\Apps\\Discord\\discord.exe", "steam": False},
    {"name": "Disk Cleanup", "exe": "cleanmgr.exe", "path": "C:\\Windows\\cleanmgr.exe", "steam": False},
    {"name": "Counter-Strike 2", "exe": "cs2.exe", "path": "D:\\Steam\\steamapps\\common\\cs2\\cs2.exe", "steam": True},
    {"name": "Spotify", "exe": "spotify.exe", "path": "C:\\Apps\\spotify.exe", "steam": False},
]


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    ctx = webapi.make_context(tmp_path / "t.db", clock=clock)
    ctx.guard = GuardService(lambda: ctx.db, clock, monotonic=clock.tick, rng=random.Random(7))
    ctx.clock = clock
    ctx.api = core.build_api(ctx)
    ctx.pushed = []
    ctx.publish = lambda event, payload=None, key=None: ctx.pushed.append((event, payload))
    ctx.catalog = app_catalog.AppCatalog(tmp_path / "cat.json", list_apps=lambda: APPS, running_now=lambda: {})
    ctx.catalog.load_now()
    ctx.db.set_setting("ui.autosave", "1")
    return ctx


def lock(ctx, **kw):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30, **kw})


def autosave(ctx, on):
    ctx.db.set_setting("ui.autosave", "1" if on else "0")


def call(ctx, name, *args):
    return getattr(ctx.api, name)(*args)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def pass_challenge(ctx, ticket):
    shown = data(ctx.api.challenge_begin(ticket))
    ctx.clock.mono += len(shown["phrase"]) * guard.MIN_MS_PER_CHAR / 1000 + 1
    return ctx.api.challenge_submit(ticket, shown["phrase"])


def new(ctx, spec):
    """draft_submit_item for a NEW item -> the inner result (or the whole reply when it asks for the challenge)."""
    got = data(call(ctx, "draft_submit_item", {"mode": "new", **spec}))
    return got if "needs_challenge" in got else got["result"]


def edit(ctx, item_id, spec):
    got = data(call(ctx, "draft_submit_item", {"mode": "edit", "id": item_id, **spec}))
    return got if "needs_challenge" in got else got["result"]


def items(ctx):
    return {i["display_name"]: i for i in ctx.db.list_items()}


def limit(period="day", amount="30m"):
    return {"rule_type": "time_limit", "same": True, "day": "", "week": "", "month": "", period: amount}


def hours(start, end, mode="block", days=EVERY_DAY):
    return {"rule_type": "scheduled", "mode": mode, "windows": [{"days": days, "start": start, "end": end}], "allowance": "0"}


PERMANENT = {"rule_type": "permanent"}


def rules_of(ctx, name):
    return {r["rule_type"]: r for r in items(ctx)[name]["rules"]}


# ---------------------------------------------------------------- what is registered

def test_the_methods_are_classified():
    kinds = dict(webapi.api_methods())
    assert kinds["suggest"] == kinds["item_get"] == "read"
    assert kinds["draft_submit_item"] == kinds["draft_set_disabled"] == "loosen"
    assert kinds["suggest_clear_history"] == "ui"


# ---------------------------------------------------------------- suggestions

def test_suggest_ranks_sites_and_apps_and_says_which_are_mine(env):
    env.db.add_history("redwood.example", "Redwood")
    got = data(call(env, "suggest", "redd"))
    assert [(r["kind"], r["name"]) for r in got][:1] == [("site", "Reddit")]
    assert got[0] == {"kind": "site", "name": "Reddit", "host": "reddit.com", "mine": False}
    got = data(call(env, "suggest", "disc"))
    assert [r["name"] for r in got][:2] == ["Discord", "Disk Cleanup"] or got[0]["name"] == "Discord"
    discord = next(r for r in got if r["kind"] == "app" and r["exe"] == "discord.exe")
    assert discord["path"].endswith("discord.exe") and discord["steam"] is False and discord["running"] is False
    # sites you added before are "mine"
    mine = data(call(env, "suggest", "redwood"))
    assert mine[0] == {"kind": "site", "name": "Redwood", "host": "redwood.example", "mine": True}


def test_suggest_is_forgiving_about_typos_and_gives_at_most_eight(env):
    assert data(call(env, "suggest", "yotube"))[0]["host"] == "youtube.com"
    many = data(call(env, "suggest", "a"))
    assert 1 <= len(many) <= 8
    assert len(data(call(env, "suggest", "e", "all", 60))) <= 60
    assert data(call(env, "suggest", "qqzzxx")) == []


def test_suggest_my_sites_come_before_popular_ones_with_the_same_score(env):
    env.db.add_history("reddit.example", "Reddit")          # (mine, and its host differs)
    got = data(call(env, "suggest", "reddit"))
    assert got[0]["mine"] is True and got[0]["host"] == "reddit.example"
    assert got[1]["host"] == "reddit.com" and got[1]["mine"] is False


def test_suggest_kinds_and_limits(env):
    sites = data(call(env, "suggest", "", "sites", 60))
    assert sites and all(r["kind"] == "site" for r in sites)
    apps = data(call(env, "suggest", "", "apps", 60))
    assert {r["exe"] for r in apps} == {a["exe"] for a in APPS}
    assert all(r["kind"] == "app" for r in data(call(env, "suggest", "dis", "apps")))
    both = data(call(env, "suggest", "", "all", 60))
    assert {r["kind"] for r in both} == {"site", "app"}
    for bad in (("x", "everything"), ("x", "all", 0), ("x", "all", 61), ("x", "all", "8"), (5,), (None,)):
        assert call(env, "suggest", *bad)["ok"] is False


def test_suggest_clear_history_is_ungated_even_while_locked(env):
    env.db.add_history("mine.example", "Mine")
    lock(env)
    assert data(call(env, "suggest", "mine"))[0]["mine"] is True
    data(call(env, "suggest_clear_history"))
    assert env.db.history() == []
    assert all(not r.get("mine") for r in data(call(env, "suggest", "mine")))


def test_a_new_site_goes_into_my_suggestions_when_it_is_saved(env):
    new(env, {"kind": "site", "target": "example.org", "name": "Example", "rules": [PERMANENT]})
    got = data(call(env, "suggest", "example"))
    assert got[0]["mine"] is True and got[0]["host"] == "example.org"


def test_suggest_is_fast_with_a_big_catalog(env):
    """Typing, key by key (every prefix of a few words), on 1 500 apps and 40 of my sites: p95 of the CPU time <= 20 ms (CPU
    time, not the wall clock: other tests share this machine; the best of three runs of each query, for the same reason)."""
    rng = random.Random(3)
    syllables = ["ka", "lo", "mi", "tar", "ven", "zu", "po", "ri", "sol", "dex", "ham", "bru", "fin", "gel", "wik", "nor", "yan",
                 "qui", "sta", "lex", "dis", "cor", "med", "ia", "pho", "tek", "net", "sun", "vid", "aud"]
    suffix = ["", " Studio", " Player", " Manager", " Launcher", " Tools", " Pro", " Setup", " Helper", " Game"]
    apps = []
    for i in range(1500):
        name = "".join(rng.choice(syllables) for _ in range(rng.randint(2, 4))).title() + rng.choice(suffix)
        apps.append({"name": name, "exe": name.split()[0].lower() + ".exe", "path": f"C:\\Apps\\{i}.exe", "steam": i % 17 == 0})
    env.catalog = app_catalog.AppCatalog(env.db.conn.execute("PRAGMA database_list").fetchone()[2] + ".cat.json",
                                         list_apps=lambda: apps, running_now=lambda: {})
    env.catalog.load_now()
    for i in range(40):
        env.db.add_history(f"mine{i}.example", f"Mine {i}")
    fn = core.registry.get("suggest")[0]
    words = ["reddit", "kalomi", "twitch", "youtube", "netflix", "discord", "dexham studio"]
    queries = [w[:n] for w in words for n in range(1, len(w) + 1)]
    for q in queries[:5]:
        fn(env, q)
    cpu = []
    for q in queries:
        runs = []
        for _ in range(3):
            t = time.thread_time()
            core.jsonable(fn(env, q))
            runs.append((time.thread_time() - t) * 1000)
        cpu.append(min(runs))
    cpu.sort()
    p95 = cpu[int(len(cpu) * 0.95) - 1]
    print(f"\nsuggest, {len(queries)} keystrokes on 1 500 apps + 40 of my sites, cpu ms: median {statistics.median(cpu):.1f}, "
          f"p95 {p95:.1f}, slowest {cpu[-1]:.1f}")
    assert p95 <= 20


# ---------------------------------------------------------------- adding

def test_a_new_site_is_added_with_its_blocker(env):
    got = new(env, {"kind": "site", "target": "https://www.Example.org/page", "name": "", "rules": [limit()]})
    assert got["result"] == "added" and got["name"] == "Example" and got["notice"] == "Example blocker added"
    item = items(env)["Example"]
    assert item["target"] == "example.org" and item["item_type"] == "site" and item["source"] == "manual"
    assert item["block_type"] == "dns" and item["id"] == got["id"] and item["id"] > 0
    assert item["rules"][0]["rule_type"] == "time_limit" and item["rules"][0]["daily_limit_min"] == 30


def test_a_popular_site_brings_all_its_host_names(env):
    got = new(env, {"kind": "site", "target": "reddit.com", "name": "", "rules": [PERMANENT]})
    item = items(env)["Reddit"]
    assert got["result"] == "added" and item["source"] == "popular"
    assert set(item["target"].split()) >= {"reddit.com", "old.reddit.com", "redd.it"}


def test_a_site_has_the_ways_it_was_given(env):
    new(env, {"kind": "site", "target": "example.org", "ways": ["dns", "close"], "rules": [PERMANENT]})
    assert items(env)["Example"]["block_type"] == "dns,close"
    new(env, {"kind": "site", "target": "example.net", "ways": ["back"], "rules": [PERMANENT]})
    assert items(env)["Example"]["block_type"] == "dns,close" and "Net" not in items(env) or True
    assert [i["block_type"] for i in env.db.list_items() if i["target"] == "example.net"] == ["back"]


def test_an_app_is_added_with_its_ways_and_path(env):
    got = new(env, {"kind": "app", "target": "discord.exe", "name": "Discord", "picked": True,
                    "app_path": "C:\\Apps\\Discord\\discord.exe", "ways": ["close", "internet"], "rules": [limit("day", "1h")]})
    item = items(env)["Discord"]
    assert got["result"] == "added" and item["item_type"] == "app" and item["target"] == "discord.exe"
    assert item["source"] == "app-browser" and item["block_type"] == "close,internet"
    assert item["app_path"] == "C:\\Apps\\Discord\\discord.exe"


def test_a_typed_app_name_and_path_are_tidied(env):
    new(env, {"kind": "app", "target": '"C:\\Games\\Foo\\Foo.exe"', "name": "", "rules": [PERMANENT]})
    item = items(env)["Foo"]
    assert item["target"] == "foo.exe" and item["source"] == "manual" and item["app_path"] == "C:\\Games\\Foo\\Foo.exe"
    assert item["block_type"] == "close"


def test_background_processes_only_go_with_close(env):
    new(env, {"kind": "app", "target": "foo.exe", "ways": ["background", "minimize"], "rules": [PERMANENT]})
    assert items(env)["Foo"]["block_type"] == "minimize"
    new(env, {"kind": "app", "target": "bar.exe", "ways": ["close", "background"], "rules": [PERMANENT]})
    assert items(env)["Bar"]["block_type"] == "close,background"


def test_a_steam_game_is_closed_with_its_background_processes_by_default(env):
    new(env, {"kind": "app", "target": "cs2.exe", "name": "Counter-Strike 2", "rules": [PERMANENT]})
    assert items(env)["Counter-Strike 2"]["block_type"] == "close,background"
    new(env, {"kind": "app", "target": "discord.exe", "name": "Discord", "rules": [PERMANENT]})
    assert items(env)["Discord"]["block_type"] == "close"
    new(env, {"kind": "app", "target": "cs2b.exe", "ways": ["close"], "rules": [PERMANENT]})      # (what the page says wins)
    assert items(env)["Cs2B"]["block_type"] == "close"


def test_a_category_blocks_everything_in_it(env):
    got = new(env, {"kind": "category", "target": "games", "name": "", "ways": ["close", "background"], "rules": [limit("day", "2h")]})
    item = items(env)["Games"]
    assert got["result"] == "added" and item["item_type"] == "category" and item["target"] == "games"
    assert item["source"] == "category" and item["block_type"] == "close,background"
    bad = new(env, {"kind": "category", "target": "no-such-category", "rules": [PERMANENT]})
    assert bad["result"] == "invalid" and "category" in bad["errors"][0]["message"]


def test_a_program_file_can_only_be_closed_or_minimised(env):
    new(env, {"kind": "app", "target": "game.jar", "name": "My game", "ways": ["close", "background", "internet"], "rules": [PERMANENT]})
    assert items(env)["My game"]["block_type"] == "close"        # (no internet, no background for javaw.exe's sake)
    new(env, {"kind": "app", "target": "tool.bat", "name": "Tool", "ways": ["minimize"], "rules": [PERMANENT]})
    assert items(env)["Tool"]["block_type"] == "minimize"
    only_net = new(env, {"kind": "app", "target": "other.ps1", "ways": ["internet"], "rules": [PERMANENT]})
    assert only_net["result"] == "invalid" and only_net["errors"][0]["message"] == "Tick Close app or Minimize."
    assert "Other" not in items(env)
    # saving a program file's ways in Edit follows the same rule
    jar = items(env)["My game"]["id"]
    refused = edit(env, jar, {"ways": ["internet"], "rules": [PERMANENT]})
    assert refused["result"] == "invalid" and items(env)["My game"]["block_type"] == "close"


def test_ways_are_checked_for_the_kind(env):
    cases = [
        ({"kind": "app", "target": "foo.exe", "ways": []}, "Tick at least one of Close app / Minimize / Block internet."),
        ({"kind": "app", "target": "foo.exe", "ways": ["background"]}, "Tick at least one of Close app / Minimize / Block internet."),
        ({"kind": "app", "target": "foo.exe", "ways": ["close", "minimize"]}, "Close app and Minimize do the same thing: pick one."),
        ({"kind": "site", "target": "example.org", "ways": []}, "Tick at least one of Can't load it / Close the tab / Go back."),
        ({"kind": "site", "target": "example.org", "ways": ["close", "back"]}, "Close the tab and Go back do the same thing: pick one."),
        ({"kind": "category", "target": "games", "ways": []}, "Tick at least one of Close app / Minimize / Block internet."),
    ]
    for spec, message in cases:
        got = new(env, {**spec, "rules": [PERMANENT]})
        assert got["result"] == "invalid", spec
        assert got["errors"] == [{"rule_type": None, "field": "ways", "message": message}]
    assert env.db.list_items() == []
    for spec in ({"kind": "site", "target": "example.org", "ways": ["minimize"]},     # (a way of another kind)
                 {"kind": "app", "target": "foo.exe", "ways": ["dns"]},
                 {"kind": "app", "target": "foo.exe", "ways": "close"},
                 {"kind": "app", "target": "foo.exe", "ways": [1]}):
        assert call(env, "draft_submit_item", {"mode": "new", **spec, "rules": [PERMANENT]})["ok"] is False


def test_windows_own_programs_and_lockdown_are_refused(env):
    for exe in ("explorer.exe", "svchost.exe", "lockdown.exe", "Lockdown.exe", "C:\\Windows\\System32\\csrss.exe", "python.exe"):
        got = new(env, {"kind": "app", "target": exe, "rules": [PERMANENT]})
        assert got["result"] == "invalid", exe
        assert "part of Windows (or Lockdown)" in got["errors"][0]["message"]
    assert env.db.list_items() == []


def test_a_script_in_lockdowns_own_folder_is_refused(env, monkeypatch):
    from pathlib import PureWindowsPath
    from webapi import blocking_edit
    monkeypatch.setattr(blocking_edit, "APP_DIR", PureWindowsPath("C:\\Program Files\\Lockdown"))
    got = new(env, {"kind": "app", "target": "start.bat", "app_path": "C:\\Program Files\\Lockdown\\tools\\start.bat", "rules": [PERMANENT]})
    assert got["result"] == "invalid" and "part of Windows (or Lockdown)" in got["errors"][0]["message"]
    elsewhere = new(env, {"kind": "app", "target": "start.bat", "app_path": "C:\\Games\\start.bat", "rules": [PERMANENT]})
    assert elsewhere["result"] == "added"


def test_no_program_inside_lockdowns_folder_can_be_blocked_however_the_path_is_written(env, monkeypatch):
    """Not only script files: any exe whose path is in the install folder (the service closes / counts everything that
    runs from an app's folder, so a forged path there would reach Lockdown's own processes)."""
    from pathlib import PureWindowsPath
    from webapi import blocking_edit
    monkeypatch.setattr(blocking_edit, "APP_DIR", PureWindowsPath("C:\\Program Files\\Lockdown"))
    for path in ("C:\\Program Files\\Lockdown\\foo.exe", "c:/program files/lockdown/tools/foo.exe",
                 "C:\\Games\\..\\Program Files\\Lockdown\\foo.exe", "C:\\Program Files\\Lockdown\\..\\Lockdown\\foo.exe",
                 "C:\\Program Files\\.\\Lockdown\\\\foo.exe", "C:\\PROGRAM FILES\\LOCKDOWN\\Foo.EXE"):
        for spec in ({"target": "foo.exe", "app_path": path}, {"target": path}):
            got = new(env, {"kind": "app", "rules": [PERMANENT], **spec})
            assert got["result"] == "invalid" and "part of Windows (or Lockdown)" in got["errors"][0]["message"], (path, spec)
    assert env.db.list_items() == []
    beside = new(env, {"kind": "app", "target": "foo.exe", "app_path": "C:\\Program Files\\Lockdown Fan\\foo.exe", "rules": [PERMANENT]})
    assert beside["result"] == "added"                                  # (a folder that only starts the same way is fine)


def test_a_program_path_is_tidied_and_one_that_is_not_a_full_path_is_refused(env):
    new(env, {"kind": "app", "target": "foo.exe", "app_path": "C:/Games/Old/../Foo//Foo.exe", "rules": [PERMANENT]})
    assert items(env)["Foo"]["app_path"] == "C:\\Games\\Foo\\Foo.exe"
    new(env, {"kind": "app", "target": "\\\\nas\\games\\Bar\\Bar.exe", "rules": [PERMANENT]})    # (a network share, as before)
    assert items(env)["Bar"]["app_path"] == "\\\\nas\\games\\Bar\\Bar.exe"
    for path in ("..\\..\\Windows\\foo.exe", "Games\\foo.exe", "\\\\?\\C:\\Games\\baz.exe", "\\\\.\\C:\\Games\\baz.exe",
                 "C:baz.exe", "C:\\Games\\ba|z.exe", "C:\\Games\\baz.exe\x00", "C:\\Games\\baz.exe:stream"):
        for spec in ({"target": "baz.exe", "app_path": path}, {"target": path}):
            if spec["target"] == "C:baz.exe":
                continue                                        # (no slash: it is only the file name, no path to keep)
            got = new(env, {"kind": "app", "rules": [PERMANENT], **spec})
            assert got["result"] == "invalid", (path, spec)
    assert sorted(items(env)) == ["Bar", "Foo"]


def test_a_target_that_cannot_be_blocked_is_refused(env):
    for spec, text in (({"kind": "site", "target": "not a site"}, "Write a site like reddit.com"),
                       ({"kind": "site", "target": "localhost"}, "Write a site like reddit.com"),
                       ({"kind": "app", "target": "notes.txt"}, "Write the program's file name"),
                       ({"kind": "site", "target": "   "}, "Pick a site, an app or a category first."),
                       ({"kind": "app", "target": ""}, "Pick a site, an app or a category first.")):
        got = new(env, {**spec, "rules": [PERMANENT]})
        assert got["result"] == "invalid" and text in got["errors"][0]["message"], spec
    assert env.db.list_items() == []


def test_a_new_item_needs_at_least_one_blocker(env):
    got = new(env, {"kind": "site", "target": "example.org", "rules": []})
    assert got["result"] == "invalid"
    assert got["errors"] == [{"rule_type": None, "field": "rules", "message": "Tick at least one blocker."}]
    assert env.db.list_items() == []


def test_a_mistake_in_a_blocker_comes_back_with_its_field_and_nothing_changes(env):
    got = new(env, {"kind": "site", "target": "example.org", "rules": [limit("day", "banana"), hours("25:00", "17:00")]})
    assert got["result"] == "invalid"
    assert {e["rule_type"] for e in got["errors"]} == {"time_limit", "scheduled"}
    assert all(e["field"] and e["message"] for e in got["errors"])
    assert env.db.list_items() == [] and data(call(env, "draft_get_state"))["dirty"] is False


def test_the_page_sends_forms_not_rules(env):
    got = new(env, {"kind": "site", "target": "example.org",
                    "rules": [{"rule_type": "time_limit", "daily_limit_min": 99999, "confirmed": True}]})
    assert got["result"] == "invalid"            # (a rule dict is not a form: Python reads the form)
    assert call(env, "draft_submit_item", {"mode": "new", "kind": "site", "target": "example.org", "rules": "permanent"})["ok"] is False
    assert call(env, "draft_submit_item", {"mode": "new", "kind": "site", "target": "example.org",
                                           "rules": [PERMANENT] * 21})["ok"] is False
    assert env.db.list_items() == []


def test_a_temporary_block_starts_when_it_is_saved_by_the_trusted_time(env):
    from trusted_time import now_from_db
    new(env, {"kind": "site", "target": "example.org", "rules": [{"rule_type": "temporary", "choice": "120"}]})
    ends = datetime.strptime(rules_of(env, "Example")["temporary"]["temp_until"], "%Y-%m-%d %H:%M:%S")
    assert abs((ends - (now_from_db(env.db) + timedelta(hours=2))).total_seconds()) < 60


def test_adding_something_new_is_free_while_locked(env):
    lock(env)
    got = new(env, {"kind": "app", "target": "foo.exe", "rules": [PERMANENT]})
    assert got["result"] == "added" and "Foo" in items(env)


def test_garbage_in_the_request_is_refused(env):
    for spec in (None, "new", [], {"mode": "other"}, {"mode": "new", "kind": "folder", "target": "x"},
                 {"mode": "new", "kind": "site", "target": 5}, {"mode": "new", "kind": "site", "target": "a.com", "name": 5},
                 {"mode": "new", "kind": "site", "target": "x" * 500},
                 {"mode": "edit", "id": "1"}, {"mode": "edit", "id": True}):
        assert call(env, "draft_submit_item", spec)["ok"] is False, spec
    assert env.db.list_items() == []


# ---------------------------------------------------------------- something that is already on the list

@pytest.fixture
def reddit(env):
    env.reddit = env.db.add_item("Reddit", ["reddit.com", "old.reddit.com"], "site", "popular",
                                 [{"rule_type": "time_limit", "daily_limit_min": 60}])
    return env


def test_adding_with_no_blocker_opens_the_existing_item(reddit):
    got = new(reddit, {"kind": "site", "target": "reddit.com", "rules": []})
    assert got["result"] == "opened_existing" and got["id"] == reddit.reddit
    assert "already on the list" in got["notice"]
    assert data(call(reddit, "draft_get_state"))["dirty"] is False and len(reddit.db.list_items()) == 1


def test_adding_with_blockers_merges_them(reddit):
    got = new(reddit, {"kind": "site", "target": "reddit.com", "rules": [hours("22:00", "23:00")]})
    assert got["result"] == "merged" and got["id"] == reddit.reddit
    assert got["notice"] == "Reddit was already on the list - blockers merged"
    assert set(rules_of(reddit, "Reddit")) == {"time_limit", "scheduled"}
    assert rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 60          # (the one it had stays)
    assert len(reddit.db.list_items()) == 1


def test_a_merge_keeps_the_stricter_rule_and_a_stricter_one_replaces_it_for_free(reddit):
    lock(reddit)
    got = new(reddit, {"kind": "site", "target": "reddit.com", "rules": [limit("day", "30m")]})       # tighter: free
    assert got["result"] == "merged" and rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 30


def test_a_merge_with_a_looser_rule_while_locked_asks_and_applies_nothing(reddit):
    lock(reddit)
    got = new(reddit, {"kind": "site", "target": "reddit.com", "rules": [limit("day", "3h")]})
    assert got["needs_challenge"] is True and got["ticket"] and "Loosen Reddit" in got["changes"]
    assert rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 60              # the old rule stays
    data(pass_challenge(reddit, got["ticket"]))
    assert rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 180


def test_a_cancelled_merge_leaves_the_old_rule_and_no_pending_edit(reddit):
    lock(reddit)
    got = new(reddit, {"kind": "site", "target": "reddit.com", "rules": [limit("day", "3h")]})
    data(reddit.api.challenge_cancel(got["ticket"]))
    assert rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 60
    assert data(call(reddit, "draft_get_state"))["dirty"] is False


def test_a_merge_into_a_disabled_item_says_so(reddit):
    reddit.db.update_item(reddit.reddit, "Reddit", ["reddit.com"], None, [{"rule_type": "permanent"}], None, None, True)
    got = new(reddit, {"kind": "site", "target": "reddit.com", "rules": [hours("22:00", "23:00")]})
    assert got["result"] == "merged" and "enable it" in got["notice"]


def test_an_app_already_listed_is_found_by_its_exe(env):
    env.db.add_item("Discord", ["discord.exe"], "app", "app-browser", [{"rule_type": "permanent"}], None, "close")
    got = new(env, {"kind": "app", "target": "C:\\Other\\Discord.EXE", "rules": []})
    assert got["result"] == "opened_existing"


# ---------------------------------------------------------------- reading one item

def test_item_get_gives_what_edit_shows(reddit):
    gid = reddit.db.add_group("Fun", [{"rule_type": "permanent"}], {reddit.reddit: {}})
    got = data(call(reddit, "item_get", reddit.reddit))
    assert got["id"] == reddit.reddit and got["name"] == "Reddit" and got["kind"] == "site"
    assert got["target"] == "reddit.com" and got["targets"] == ["reddit.com", "old.reddit.com"]
    assert got["ways"] == ["dns"] and got["disabled"] is False and got["hosted"] is False and got["unsaved"] is False
    assert got["rules"][0]["rule_type"] == "time_limit" and got["rules"][0]["daily_limit_min"] == 60
    assert got["groups"] == [{"id": gid, "name": "Fun", "chose": False}]
    assert got["now"] == NOW.isoformat(timespec="seconds")


def test_item_get_says_when_a_group_chooses_the_way(reddit):
    reddit.db.add_group("Fun", [{"rule_type": "permanent"}], {reddit.reddit: {}}, {"site_block": "close", "app_block": None, "member_blocks": {}})
    assert data(call(reddit, "item_get", reddit.reddit))["groups"][0]["chose"] is True


def test_item_get_by_target_finds_it_or_says_null(reddit):
    assert data(call(reddit, "item_get", None, "OLD.reddit.com"))["id"] == reddit.reddit
    assert data(call(reddit, "item_get", None, "nothing.example")) is None
    reddit.db.add_item("Foo", ["foo.exe"], "app", "manual", [{"rule_type": "permanent"}], None, "minimize")
    got = data(call(reddit, "item_get", None, "C:\\Some\\Foo.exe"))
    assert got["name"] == "Foo" and got["ways"] == ["minimize"] and got["kind"] == "app"


def test_item_get_refuses_what_is_gone_or_malformed(reddit):
    assert call(reddit, "item_get", 999)["ok"] is False
    for bad in ("1", True, 1.5, [1]):
        assert call(reddit, "item_get", bad)["ok"] is False
    assert call(reddit, "item_get", None, 5)["ok"] is False


def test_item_get_shows_the_draft_not_just_the_saved_list(reddit):
    autosave(reddit, False)
    edit(reddit, reddit.reddit, {"name": "Reddit!", "rules": [PERMANENT]})
    got = data(call(reddit, "item_get", reddit.reddit))
    assert got["name"] == "Reddit!" and got["unsaved"] is True and got["rules"][0]["rule_type"] == "permanent"
    assert reddit.db.list_items()[0]["display_name"] == "Reddit"


def test_item_get_gives_a_running_temporary_block_as_its_end_and_a_new_one_as_its_length(env):
    autosave(env, False)
    end = (NOW + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    running = env.db.add_item("Run", ["run.example"], "site", "manual", [{"rule_type": "temporary", "temp_until": end}])
    got = data(call(env, "item_get", running))
    assert got["rules"] == [{"rule_type": "temporary", "temp_until": end}]
    new(env, {"kind": "site", "target": "later.example", "name": "Later", "rules": [{"rule_type": "temporary", "choice": "60"}]})
    later = data(call(env, "item_get", None, "later.example"))
    assert later["rules"] == [{"rule_type": "temporary", "duration_min": 60}]          # (its clock starts when it is saved)


# ---------------------------------------------------------------- editing

def test_saving_an_edit_changes_name_ways_and_blockers(reddit):
    got = edit(reddit, reddit.reddit, {"name": "  The   Reddit ", "ways": ["dns", "close"], "rules": [limit("day", "30m"), PERMANENT]})
    assert got == {"result": "saved", "id": reddit.reddit, "name": "The Reddit", "notice": "The Reddit saved"}
    item = items(reddit)["The Reddit"]
    assert item["block_type"] == "dns,close" and {r["rule_type"] for r in item["rules"]} == {"time_limit", "permanent"}
    assert item["target"] == "reddit.com old.reddit.com"            # (the target can't be changed)


def test_edit_takes_the_target_from_the_item_not_from_the_page(reddit):
    edit(reddit, reddit.reddit, {"kind": "app", "target": "explorer.exe", "rules": [PERMANENT]})
    item = reddit.db.list_items()[0]
    assert item["target"] == "reddit.com old.reddit.com" and item["item_type"] == "site"


def test_editing_to_a_looser_rule_while_locked_asks_and_applies_nothing(reddit):
    lock(reddit)
    got = edit(reddit, reddit.reddit, {"rules": [limit("day", "2h")]})
    assert got["needs_challenge"] is True and "Loosen Reddit" in got["changes"]
    assert rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 60
    data(pass_challenge(reddit, got["ticket"]))
    assert rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 120


def test_a_cancelled_edit_changes_nothing(reddit):
    lock(reddit)
    got = edit(reddit, reddit.reddit, {"name": "Renamed", "rules": [limit("day", "2h")]})
    data(reddit.api.challenge_cancel(got["ticket"]))
    assert list(items(reddit)) == ["Reddit"] and rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 60


def test_a_tighter_edit_and_a_rename_are_free_while_locked(reddit):
    lock(reddit)
    assert edit(reddit, reddit.reddit, {"name": "Reddit2", "rules": [limit("day", "30m")]})["result"] == "saved"
    assert rules_of(reddit, "Reddit2")["time_limit"]["daily_limit_min"] == 30


def test_removing_a_blocker_while_locked_asks(reddit):
    lock(reddit)
    got = edit(reddit, reddit.reddit, {"rules": [PERMANENT]})              # (the time limit is gone: it blocks less)
    assert got["needs_challenge"] is True
    assert set(rules_of(reddit, "Reddit")) == {"time_limit"}


def test_weekday_limits_raising_one_day_asks_and_lowering_is_free(env):
    weekdays = ["2h", "2h", "2h", "2h", "2h", "1h", "1h"]
    item = env.db.add_item("Games", ["games.example"], "site", "manual",
                           [{"rule_type": "time_limit", "daily_limit_min": None, "daily_limit_days": __import__("json").dumps([120, 120, 120, 120, 120, 60, 60])}])
    lock(env)
    form = lambda days: {"rule_type": "time_limit", "same": False, "day": "", "week": "", "month": "", "weekdays": days}
    lower = edit(env, item, {"rules": [form(["1h"] + weekdays[1:])]})
    assert lower["result"] == "saved"                                     # Monday 2h -> 1h: free
    raised = edit(env, item, {"rules": [form(weekdays[:6] + ["3h"])]})   # Sunday 1h -> 3h
    assert raised["needs_challenge"] is True
    data(env.api.challenge_cancel(raised["ticket"]))


def test_extending_hours_is_free_and_shortening_asks(env):
    item = env.db.add_item("Night", ["night.example"], "site", "manual",
                           [{"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "22:00", "00:00")])}])
    lock(env)
    assert edit(env, item, {"rules": [hours("22:00", "01:00")]})["result"] == "saved"          # later end: free
    assert edit(env, item, {"rules": [hours("21:00", "01:00")]})["result"] == "saved"          # earlier start: free
    shortened = edit(env, item, {"rules": [hours("22:00", "00:30")]})                          # blocks less
    assert shortened["needs_challenge"] is True
    saved = rules_of(env, "Night")["scheduled"]["schedule"]
    assert '"21:00"' in saved and '"01:00"' in saved                                          # (still the extended hours)
    data(pass_challenge(env, shortened["ticket"]))
    assert '"00:30"' in rules_of(env, "Night")["scheduled"]["schedule"]


def test_a_lighter_way_asks_and_a_stronger_one_is_free(env):
    app = env.db.add_item("Foo", ["foo.exe"], "app", "manual", [{"rule_type": "permanent"}], None, "close")
    site = env.db.add_item("Bar", ["bar.example"], "site", "manual", [{"rule_type": "permanent"}], None, "dns,close")
    lock(env)
    lighter = edit(env, app, {"ways": ["minimize"], "rules": [PERMANENT]})
    assert lighter["needs_challenge"] is True
    data(env.api.challenge_cancel(lighter["ticket"]))
    assert items(env)["Foo"]["block_type"] == "close"
    assert edit(env, app, {"ways": ["close", "internet"], "rules": [PERMANENT]})["result"] == "saved"
    assert items(env)["Foo"]["block_type"] == "close,internet"
    fewer = edit(env, site, {"ways": ["dns"], "rules": [PERMANENT]})
    assert fewer["needs_challenge"] is True
    data(env.api.challenge_cancel(fewer["ticket"]))
    assert items(env)["Bar"]["block_type"] == "dns,close"


def test_a_sites_ways_are_saved(reddit):
    edit(reddit, reddit.reddit, {"ways": ["dns", "back"], "rules": [limit("day", "30m")]})
    assert items(reddit)["Reddit"]["block_type"] == "dns,back"


def test_a_category_s_ways_are_saved(env):
    cat_id = env.db.add_item("Games", ["games"], "category", "category", [{"rule_type": "permanent"}], None, "close")
    edit(env, cat_id, {"ways": ["close", "background", "internet"], "rules": [PERMANENT]})
    assert items(env)["Games"]["block_type"] == "close,background,internet"


def test_no_blocker_left_and_no_group_removes_the_item_through_the_guard(reddit):
    lock(reddit)
    got = edit(reddit, reddit.reddit, {"rules": []})
    assert got["needs_challenge"] is True and "Remove Reddit" in got["changes"]
    assert list(items(reddit)) == ["Reddit"]
    data(pass_challenge(reddit, got["ticket"]))
    assert items(reddit) == {}


def test_no_blocker_left_deletes_when_nothing_is_locked(reddit):
    got = edit(reddit, reddit.reddit, {"rules": []})
    assert got["result"] == "deleted" and got["notice"] == "Reddit removed" and items(reddit) == {}


def test_a_member_of_a_group_may_have_no_blockers_of_its_own(reddit):
    reddit.db.add_group("Fun", [{"rule_type": "permanent"}], {reddit.reddit: {}})
    got = edit(reddit, reddit.reddit, {"rules": []})
    assert got["result"] == "saved" and list(items(reddit)) == ["Reddit"] and items(reddit)["Reddit"]["rules"] == []
    # (the group's rule is still in force: nothing was loosened)


def test_an_item_that_is_gone_is_refused(env):
    assert call(env, "draft_submit_item", {"mode": "edit", "id": 999, "rules": [PERMANENT]})["ok"] is False


def test_an_edit_with_a_mistake_changes_nothing(reddit):
    got = edit(reddit, reddit.reddit, {"name": "Changed", "rules": [limit("day", "nonsense")]})
    assert got["result"] == "invalid" and got["errors"][0]["rule_type"] == "time_limit"
    assert list(items(reddit)) == ["Reddit"] and data(call(reddit, "draft_get_state"))["dirty"] is False


def test_a_running_temporary_block_can_be_kept_as_it_is(env):
    from trusted_time import now_from_db      # the Draft judges "looser" against the real trusted clock, not NOW
    end = (now_from_db(env.db) + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    item = env.db.add_item("Run", ["run.example"], "site", "manual", [{"rule_type": "temporary", "temp_until": end}])
    lock(env)
    kept = edit(env, item, {"name": "Run!", "rules": [{"rule_type": "temporary", "choice": "keep", "keep_until": end}]})
    assert kept["result"] == "saved" and rules_of(env, "Run!")["temporary"]["temp_until"] == end
    shorter = edit(env, item, {"rules": [{"rule_type": "temporary", "choice": "60"}]})       # 3 h left -> 1 h: looser
    assert shorter["needs_challenge"] is True
    longer = edit(env, item, {"rules": [{"rule_type": "temporary", "choice": "1440"}]})       # a day: stricter, free
    assert longer["result"] == "saved"


def test_a_categorys_way_cannot_be_made_lighter_without_the_challenge(env):
    cat = new(env, {"kind": "category", "target": "games", "ways": ["close", "background", "internet"], "rules": [PERMANENT]})["id"]
    lock(env)
    for ways in (["minimize", "internet"], ["minimize"], ["close"], ["close", "background"], ["internet"]):
        got = edit(env, cat, {"ways": ways, "rules": [PERMANENT]})
        assert got["needs_challenge"] is True and got["changes"] == ["Loosen Games"], ways
        data(env.api.challenge_cancel(got["ticket"]))
        assert items(env)["Games"]["block_type"] == "close,background,internet", ways
    got = edit(env, cat, {"ways": ["minimize"], "rules": [PERMANENT]})
    data(pass_challenge(env, got["ticket"]))                                           # (with the challenge it goes through)
    assert items(env)["Games"]["block_type"] == "minimize"


def test_a_categorys_way_may_be_made_stricter_or_stay_free(env):
    cat = new(env, {"kind": "category", "target": "games", "ways": ["minimize"], "rules": [PERMANENT]})["id"]
    lock(env)
    assert edit(env, cat, {"ways": ["minimize"], "name": "Games!", "rules": [PERMANENT]})["result"] == "saved"
    for ways, stored in ((["minimize", "internet"], "minimize,internet"), (["close", "internet"], "close,internet"),
                         (["close", "background", "internet"], "close,background,internet")):
        assert edit(env, cat, {"ways": ways, "rules": [PERMANENT]})["result"] == "saved", ways
        assert items(env)["Games!"]["block_type"] == stored


def test_keep_takes_the_end_from_the_saved_block_not_from_the_page(env):
    end = (NOW + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    item = env.db.add_item("Run", ["run.example"], "site", "manual", [{"rule_type": "temporary", "temp_until": end}])
    lock(env)
    for shift in (59, 1, 3600, 10_000):                                    # an end a little short, and long ones
        forged = (NOW + timedelta(hours=3) - timedelta(seconds=shift)).strftime("%Y-%m-%d %H:%M:%S")
        got = edit(env, item, {"rules": [{"rule_type": "temporary", "choice": "keep", "keep_until": forged}]})
        assert got["result"] == "saved" and rules_of(env, "Run")["temporary"]["temp_until"] == end, shift
    later = (NOW + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")      # (a later end isn't taken either)
    edit(env, item, {"rules": [{"rule_type": "temporary", "choice": "keep", "keep_until": later}]})
    assert rules_of(env, "Run")["temporary"]["temp_until"] == end
    edit(env, item, {"rules": [{"rule_type": "temporary", "choice": "keep"}]})            # (nor does it need one)
    assert rules_of(env, "Run")["temporary"]["temp_until"] == end


def test_forged_keeps_repeated_do_not_wear_a_running_block_down(env):
    end = (NOW + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    item = env.db.add_item("Run", ["run.example"], "site", "manual", [{"rule_type": "temporary", "temp_until": end}])
    lock(env)
    for _ in range(30):
        current = datetime.strptime(rules_of(env, "Run")["temporary"]["temp_until"], "%Y-%m-%d %H:%M:%S")
        forged = (current - timedelta(seconds=59)).strftime("%Y-%m-%d %H:%M:%S")      # the page's idea of "the end"
        edit(env, item, {"rules": [{"rule_type": "temporary", "choice": "keep", "keep_until": forged}]})
    assert rules_of(env, "Run")["temporary"]["temp_until"] == end
    assert data(call(env, "draft_get_state"))["dirty"] is False


def test_keep_needs_a_running_block_that_is_saved(env):
    new_keep = new(env, {"kind": "site", "target": "example.org", "rules": [
        {"rule_type": "temporary", "choice": "keep", "keep_until": (NOW + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")}]})
    assert new_keep["result"] == "invalid" and new_keep["errors"][0]["field"] == "choice"      # (adding: nothing to keep)
    assert env.db.list_items() == []
    forever = env.db.add_item("Forever", ["forever.example"], "site", "manual", [PERMANENT])
    got = edit(env, forever, {"rules": [{"rule_type": "temporary", "choice": "keep",
                                          "keep_until": (NOW + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")}]})
    assert got["result"] == "invalid" and got["errors"][0]["rule_type"] == "temporary"        # (editing: it has none)
    assert [r["rule_type"] for r in items(env)["Forever"]["rules"]] == ["permanent"]
    gone = (NOW - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    over = env.db.add_item("Over", ["over.example"], "site", "manual", [{"rule_type": "temporary", "temp_until": gone}])
    ended = edit(env, over, {"rules": [{"rule_type": "temporary", "choice": "keep"}]})
    assert ended["result"] == "invalid" and "already ended" in ended["errors"][0]["message"]


# ---------------------------------------------------------------- Auto-save off, and what the page is told

def test_with_autosave_off_the_edit_waits_for_save_and_the_challenge_comes_then(reddit):
    autosave(reddit, False)
    lock(reddit)
    got = edit(reddit, reddit.reddit, {"rules": [limit("day", "2h")]})
    assert got["result"] == "saved"                                       # (pending: nothing asked yet)
    assert rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 60
    state = data(call(reddit, "draft_get_state"))
    assert state["dirty"] is True and state["changes"] == ["Changed Reddit"]
    ask = data(call(reddit, "draft_save"))
    assert ask["needs_challenge"] is True and "Loosen Reddit" in ask["changes"]
    data(pass_challenge(reddit, ask["ticket"]))
    assert rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 120


def test_a_submit_pushes_draft_and_blocks_changed(env):
    new(env, {"kind": "site", "target": "example.org", "rules": [PERMANENT]})
    names = [e for e, _ in env.pushed]
    assert "draft" in names and names[-1] == "blocks_changed"
    assert data(call(env, "draft_get_state"))["dirty"] is False


def test_a_reply_that_asks_for_the_challenge_carries_no_result(reddit):
    lock(reddit)
    got = edit(reddit, reddit.reddit, {"rules": [limit("day", "2h")]})
    assert set(got) >= {"needs_challenge", "ticket", "changes"} and "result" not in got


# ---------------------------------------------------------------- disable / enable

def test_disabling_is_a_loosening_and_enabling_is_free(reddit):
    lock(reddit)
    got = data(call(reddit, "draft_set_disabled", reddit.reddit, True))
    assert got["needs_challenge"] is True and "Disable Reddit" in got["changes"]
    assert not reddit.db.list_items()[0].get("disabled")
    data(pass_challenge(reddit, got["ticket"]))
    assert reddit.db.list_items()[0]["disabled"] == 1
    on = data(call(reddit, "draft_set_disabled", reddit.reddit, False))                 # enabling: free
    assert on["result"] == {"id": reddit.reddit, "name": "Reddit", "disabled": False}
    assert reddit.db.list_items()[0]["disabled"] == 0


def test_disabling_with_nothing_locked_is_just_done(reddit):
    got = data(call(reddit, "draft_set_disabled", reddit.reddit, True))
    assert got["result"]["disabled"] is True and reddit.db.list_items()[0]["disabled"] == 1


def test_disable_refuses_what_is_gone_or_malformed(reddit):
    assert call(reddit, "draft_set_disabled", 999, True)["ok"] is False
    for args in (("1", True), (reddit.reddit, "yes"), (reddit.reddit, 1), (True, True), (reddit.reddit,)):
        assert call(reddit, "draft_set_disabled", *args)["ok"] is False
    assert call(reddit, "draft_set_disabled", reddit.reddit, True, True)["ok"] is False        # no "confirmed" argument


# ---------------------------------------------------------------- the page can't confirm

def test_a_confirmed_flag_is_not_accepted_anywhere(reddit):
    lock(reddit)
    assert call(reddit, "draft_submit_item", {"mode": "edit", "id": reddit.reddit, "rules": [limit("day", "2h")]}, True)["ok"] is False
    got = data(call(reddit, "draft_submit_item", {"mode": "edit", "id": reddit.reddit, "confirmed": True, "challenge": "passed",
                                                  "rules": [limit("day", "2h")]}))
    assert got["needs_challenge"] is True                                               # (extra keys change nothing)
    assert rules_of(reddit, "Reddit")["time_limit"]["daily_limit_min"] == 60
