"""UP14: site suggestions from the bundled ~500 sites in every add-a-site box: the ranking (webapi/sites.py + suggest), a
typed or picked host's friendly name and all its host names, and the boxes that add a site (Add, the group's Members search,
a mode's "Other site", Site protection's "Allowed anyway") - a picked site goes in the way a typed one does, with all its
hosts, and the guard (free vs challenge) is the same."""
import random
import statistics
import time
from datetime import datetime

import pytest

import antibypass as ab
import search
import webapi
from importer import popular
from resident import catalog as app_catalog
from webapi import blocking_edit, core, guard, sites
from webapi.guard import GuardService

APPS = [{"name": "Discord", "exe": "discord.exe", "path": "C:\\Apps\\Discord\\discord.exe", "steam": False}]


class Clock:
    now, mono = datetime(2026, 10, 12, 12, 0, 0), 1000.0

    def __call__(self, db=None):
        return self.now

    def tick(self):
        return self.mono


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    ctx = webapi.make_context(tmp_path / "t.db", clock=clock)
    ctx.guard = GuardService(lambda: ctx.db, clock, monotonic=clock.tick, rng=random.Random(7))
    ctx.clock = clock
    ctx.api = core.build_api(ctx)
    ctx.publish = lambda event, payload=None, key=None: None
    ctx.catalog = app_catalog.AppCatalog(tmp_path / "cat.json", list_apps=lambda: APPS, running_now=lambda: {})
    ctx.catalog.load_now()
    ctx.db.set_setting("ui.autosave", "1")
    return ctx


def call(ctx, name, *args):
    return getattr(ctx.api, name)(*args)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def names(ctx, text, kind="sites", limit=8):
    return [r["name"] for r in data(call(ctx, "suggest", text, kind, limit))]


def lock(ctx):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})


# ---------------------------------------------------------------- the ranking

def test_the_list_is_the_big_one():
    assert len(popular.load_sites()) >= 400


@pytest.mark.parametrize("typed,first", [
    ("you", "YouTube"), ("yout", "YouTube"), ("YOUTUBE", "YouTube"), ("redd", "Reddit"), ("allegro", "Allegro"),
    ("x.c", "Twitter/X"), ("yotube", "YouTube"), ("googlevideo", "YouTube"), ("face", "Facebook"),
])
def test_the_best_site_for_what_is_typed_comes_first(env, typed, first):
    assert names(env, typed)[0] == first


def test_a_name_that_starts_with_the_text_beats_a_word_inside_a_name(env):
    got = names(env, "news", limit=30)
    starts = [n for n in got if n.lower().startswith("news")]
    inside = [n for n in got if not n.lower().startswith("news") and "news" in n.lower().split()]
    assert starts and inside
    assert max(got.index(n) for n in starts) < min(got.index(n) for n in inside)


def test_tiers_name_then_address_then_word_then_the_rest():
    t = sites.tier
    assert t("red", "reddit", ["reddit.com"]) == sites.NAME_FIRST
    assert t("x.c", "twitter/x", ["x.com", "twitter.com"]) == sites.DOMAIN_FIRST
    assert t("www.x.c", "twitter/x", ["x.com"]) == sites.DOMAIN_FIRST                  # ("www." is ignored)
    assert t("news", "hacker news", ["news.ycombinator.com"]) == sites.DOMAIN_FIRST
    assert t("news", "fox news", ["foxnews.com"]) == sites.WORD_FIRST
    assert t("ews", "fox news", ["foxnews.com"]) == sites.REST


def test_within_one_score_and_tier_the_more_popular_site_comes_first():
    ranks = [(s, tier, i) for s, tier, i, _row in sites.suggest("a") if s == 0 and tier == sites.REST]
    assert ranks == sorted(ranks)


def test_mine_come_before_popular_ones_with_the_same_score(env):
    env.db.add_history("reddit.example", "Reddit")
    got = data(call(env, "suggest", "reddit", "sites"))
    assert (got[0]["host"], got[0]["mine"]) == ("reddit.example", True)
    assert (got[1]["host"], got[1]["mine"]) == ("reddit.com", False)


def test_a_bundled_site_you_used_is_not_shown_twice(env):
    env.db.add_history("m.youtube.com", "YouTube")
    got = data(call(env, "suggest", "youtube", "sites"))
    assert [r["host"] for r in got].count("youtube.com") == 0 and got[0]["host"] == "m.youtube.com"
    assert got[0]["mine"] is True and "googlevideo.com" in got[0]["hosts"]


def test_site_rows_carry_the_category_and_all_the_hosts(env):
    row = data(call(env, "suggest", "youtube", "sites"))[0]
    assert row["category"] == "Video & streaming" and row["hosts"][0] == "youtube.com" and "googlevideo.com" in row["hosts"]
    assert row["mine"] is False and row["kind"] == "site"


def test_sites_and_apps_still_share_the_dropdown(env):
    got = data(call(env, "suggest", "disc"))
    assert any(r["kind"] == "app" and r["exe"] == "discord.exe" for r in got)
    assert any(r["kind"] == "site" and r["name"] == "Discord" for r in got) or got[0]["kind"] == "app"


def test_nothing_typed_lists_the_most_popular_sites(env):
    got = names(env, "", limit=60)
    assert got[0] == "YouTube" and len(got) == 60


def test_the_prepared_search_scores_like_the_plain_one():
    for q, text in [("yotube", "YouTube youtube.com"), ("x.c", "Twitter/X x.com"), ("zz", "abc"), ("hacker n", "Hacker News news.ycombinator.com")]:
        norm = search.normalize(text)
        words = search.words_of(q)
        assert search.score(q, text) == (search.score_prepared(words, norm, search.words_of(norm)) if words else 0)


def test_suggest_is_fast(env):
    for i in range(30):
        env.db.add_history(f"mine{i}.example", f"Mine {i}")
    call(env, "suggest", "warm")
    times = []
    for q in ["you", "yout", "redd", "gogle", "x.c", "e", "news", "allegro", "faceb", "a"] * 6:
        t = time.perf_counter()
        data(call(env, "suggest", q, "all", 8))
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    assert times[int(len(times) * 0.95)] <= 50, statistics.mean(times)


# ---------------------------------------------------------------- a typed or picked host

def test_a_host_of_a_listed_site_gets_its_friendly_name_and_every_host_name():
    assert blocking_edit.popular_hosts("googlevideo.com") == ("YouTube", ["youtube.com", "m.youtube.com", "youtu.be", "googlevideo.com"])
    assert blocking_edit.popular_hosts("allegro.pl")[0] == "Allegro"
    assert blocking_edit.guess_name("allegro.pl") == "Allegro"
    assert blocking_edit.popular_hosts("a.youtube.com") is None                      # (a sub-address is not the whole site)
    assert blocking_edit.guess_name("example.org") == "Example"


def new_site(ctx, host, **spec):
    got = data(call(ctx, "draft_submit_item", {"mode": "new", "kind": "site", "target": host, "name": "",
                                               "rules": [{"rule_type": "permanent"}], **spec}))
    return got["result"]


def test_add_with_a_picked_site_adds_all_its_hosts(env):
    pick = data(call(env, "suggest", "allegro", "sites"))[0]
    assert new_site(env, pick["host"])["result"] == "added"
    item = next(i for i in env.db.list_items() if i["display_name"] == "Allegro")
    assert item["target"].split() == pick["hosts"] and item["source"] == "popular"
    pick = data(call(env, "suggest", "yout", "sites"))[0]
    new_site(env, pick["host"])
    assert set(next(i for i in env.db.list_items() if i["display_name"] == "YouTube")["target"].split()) == set(pick["hosts"])


def test_add_a_picked_site_while_locked_is_still_free(env):
    lock(env)
    assert new_site(env, "tiktok.com")["result"] == "added"                           # (adding is stricter: no challenge)


# ---------------------------------------------------------------- the group's Members search

def search_members(ctx, text):
    return data(call(ctx, "group_member_search", text))


def test_the_member_search_suggests_listed_sites_not_on_the_blocklist_yet(env):
    found = search_members(env, "redd")
    assert [s["name"] for s in found["sites"]][:1] == ["Reddit"]
    assert found["sites"][0]["hosts"][0] == "reddit.com" and found["sites"][0]["category"] == "Social"
    env.db.add_item("Reddit", ["reddit.com", "old.reddit.com"], "site", "manual", [], block_type="dns")
    assert "Reddit" not in [s["name"] for s in search_members(env, "redd")["sites"]]       # (a site on the list is an item row)
    assert [i["name"] for i in search_members(env, "redd")["items"]] == ["Reddit"]


def test_the_member_search_with_an_address_of_a_listed_site_gives_the_site_row(env):
    found = search_members(env, "https://www.YouTube.com/watch")
    assert found["site"] is None and found["sites"][0]["name"] == "YouTube" and "googlevideo.com" in found["sites"][0]["hosts"]
    other = search_members(env, "example.org")
    assert other["site"] == {"host": "example.org"}


def test_a_picked_site_member_is_put_on_the_blocklist_with_all_hosts(env):
    pick = search_members(env, "allegro")["sites"][0]
    form = {"rule_type": "time_limit", "same": True, "day": "2h", "week": "", "month": ""}
    got = data(call(env, "draft_set_group", None, "Shops", [form], [], {"app_block": None, "site_block": None},
                    [{"kind": "site", "name": pick["host"], "display": pick["name"]}]))
    assert got["result"]["saved"], got
    item = next(i for i in env.db.list_items() if i["display_name"] == "Allegro")
    assert item["target"].split() == pick["hosts"]


# ---------------------------------------------------------------- a mode's "Other site"

def test_a_mode_extra_site_brings_all_hosts_of_a_listed_site(env):
    out = data(call(env, "mode_extra_check", {"kind": "site", "text": "allegro.pl", "name": "", "ways": ["dns"]}))
    assert out["name"] == "Allegro" and out["targets"] == ["allegro.pl"]
    out = data(call(env, "mode_extra_check", {"kind": "site", "text": "googlevideo.com", "name": "", "ways": ["dns"]}))
    assert out["name"] == "YouTube" and out["targets"] == ["youtube.com", "m.youtube.com", "youtu.be", "googlevideo.com"]


# ---------------------------------------------------------------- Site protection > Allowed anyway

def allowed(ctx):
    return data(call(ctx, "protection_get"))["allowed"]


def test_allow_a_picked_site_allows_all_its_hosts_and_a_typed_one_only_itself(env):
    data(call(env, "protection_allow", "example.org"))
    assert allowed(env) == ["example.org"]
    data(call(env, "protection_allow", "youtube.com", True))
    assert set(allowed(env)) == {"example.org", "youtube.com", "m.youtube.com", "youtu.be", "googlevideo.com"}
    data(call(env, "protection_allow", "reddit.com"))                                 # (typed: just that address)
    assert "old.reddit.com" not in allowed(env) and "reddit.com" in allowed(env)
    data(call(env, "protection_allow", "youtube.com", True))                          # (again: nothing doubles)
    assert allowed(env).count("youtube.com") == 1


def test_allowing_a_picked_site_while_locked_needs_the_challenge_once(env):
    lock(env)
    reply = data(call(env, "protection_allow", "youtube.com", True))
    assert "needs_challenge" in reply and allowed(env) == []
    shown = data(env.api.challenge_begin(reply["ticket"]))
    env.clock.mono += len(shown["phrase"]) * guard.MIN_MS_PER_CHAR / 1000 + 1
    assert env.api.challenge_submit(reply["ticket"], shown["phrase"])["ok"]
    assert set(allowed(env)) == {"youtube.com", "m.youtube.com", "youtu.be", "googlevideo.com"}


def test_a_site_nobody_listed_is_allowed_alone_even_when_picked(env):
    data(call(env, "protection_allow", "example.org", True))
    assert allowed(env) == ["example.org"]
