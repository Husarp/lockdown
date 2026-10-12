"""An allowed site ("Allowed anyway") can also be skipped by the blocked words ("Blocked words don't apply here"):
default on for a new entry, off for the old ones; turning it on loosens (challenge), off is free; the word check treats
such a site like a keyword-exception site; a backup carries it and an old one loads."""
import json
import random
from datetime import datetime

import pytest

import antibypass as ab
import backup
import keywords as kw
import webapi
from blocker import protection
from monitor.word_guard import WordGuard
from webapi import core, settings
from webapi.guard import GuardService

NOW = datetime(2026, 10, 12, 15, 0, 0)
BAD = "https://dictionary.example.com/define/porn"
CFG = {**kw.DEFAULTS}


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


@pytest.fixture
def env(tmp_path):
    ctx = webapi.make_context(tmp_path / "t.db", clock=lambda db=None: NOW)
    ctx.guard = GuardService(lambda: ctx.db, lambda db=None: NOW, rng=random.Random(4))
    ctx.api = core.build_api(ctx)
    ctx.published = []
    ctx.publish = lambda event, payload=None, key=None: ctx.published.append((event, payload))
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})        # LOCKED: loosening asks
    return ctx


def call(env, name, *args):
    return getattr(env.api, name)(*args)


def asks(reply):
    return bool(reply["ok"] and isinstance(reply["data"], dict) and reply["data"].get("needs_challenge"))


def cfg(env):
    return protection.settings(env.db)


def set_cfg(env, **changes):
    c = cfg(env)
    c.update(changes)
    protection.save_settings(env.db, c)


def unlock(env):
    ab.save(env.db, {**ab.settings(env.db), "phrase": False})


# ---------------------------------------------------------------- the word check

def test_a_skipped_site_and_its_subdomains_are_not_checked_but_other_sites_are():
    assert kw.find(BAD, "porn - Dictionary", CFG) == "porn*"
    skip = frozenset({"dictionary.example.com"})
    assert kw.find(BAD, "porn - Dictionary", CFG, skip) is None
    assert kw.find("https://en.dictionary.example.com/define/porn", "porn", CFG, skip) is None   # a subdomain
    assert kw.find("https://example.com/porn", "", CFG, skip) == "porn*"                          # the parent: still checked
    assert kw.find("https://notdictionary.example.com/porn", "", CFG, skip) == "porn*"            # not a look-alike
    assert kw.find("https://www.google.com/search?q=porn+dictionary.example.com", "", CFG, skip) == "porn*"


def test_a_skipped_site_works_like_a_keyword_exception_and_the_exceptions_still_work():
    assert kw.find(BAD, "", {**CFG, "exceptions": ["dictionary.example.com"]}) is None
    assert kw.find(BAD, "", {**CFG, "exceptions": ["dictionary.example.com"], "exceptions_on": False}) == "porn*"
    skip = frozenset({"dictionary.example.com"})
    assert kw.find(BAD, "", {**CFG, "exceptions": ["x.com"], "exceptions_on": False}, skip) is None   # (not an exception)


def test_the_word_guard_leaves_a_skipped_site_alone():
    pressed = []
    guard = WordGuard(lambda word, action: None)
    do = lambda hwnd, action: pressed.append(action) or True
    tab = (3, BAD, "porn - Dictionary")
    guard.tick(CFG, lambda: tab, do, 100.0, None, frozenset({"dictionary.example.com"}))
    assert pressed == []
    guard.tick(CFG, lambda: tab, do, 101.0, None, frozenset({"other.com"}))
    assert pressed == ["close"]


def test_words_skip_counts_only_sites_that_are_still_allowed():
    c = {"allowed": ["a.com"], "allowed_words": ["a.com", "gone.com"]}
    assert protection.words_skip(c) == {"a.com"}
    assert protection.words_skip({"allowed": ["a.com"]}) == frozenset()                 # older settings: none
    assert protection.words_skip({"allowed": "x", "allowed_words": "y"}) == frozenset()


def test_the_service_side_reads_the_saved_setting(env):
    assert protection.words_skip_sites(env.db) == frozenset()
    set_cfg(env, allowed=["a.com", "b.com"], allowed_words=["b.com"])
    assert protection.words_skip_sites(env.db) == {"b.com"}


# ---------------------------------------------------------------- the settings: old files, damaged files

def test_an_old_setting_without_the_list_reads_as_none_skipped(env):
    env.db.set_setting(protection.SETTINGS_KEY, json.dumps({"allowed": ["old.com"]}))
    assert cfg(env)["allowed"] == ["old.com"] and cfg(env)["allowed_words"] == []
    page = call(env, "protection_get")["data"]
    assert page["allowed"] == ["old.com"] and page["allowed_words"] == []              # existing entries keep today's behaviour


def test_a_damaged_list_reads_as_none(env):
    for bad in ("x", 5, {"a": 1}, None):
        env.db.set_setting(protection.SETTINGS_KEY, json.dumps({"allowed": ["a.com"], "allowed_words": bad}))
        assert cfg(env)["allowed_words"] == []
    env.db.set_setting(protection.SETTINGS_KEY, json.dumps({"allowed": ["a.com"], "allowed_words": ["a.com", 7, None]}))
    assert cfg(env)["allowed_words"] == ["a.com"]


# ---------------------------------------------------------------- the Api

def test_a_new_allowed_site_skips_the_words_by_default_and_asks_while_locked(env):
    reply = call(env, "protection_allow", "dictionary.example.com")
    assert asks(reply) and "blocked words don't apply" in reply["data"]["changes"][0]
    assert cfg(env)["allowed"] == []                                                    # (nothing before the challenge)
    unlock(env)
    assert not asks(call(env, "protection_allow", "dictionary.example.com"))
    assert cfg(env)["allowed"] == ["dictionary.example.com"] and cfg(env)["allowed_words"] == ["dictionary.example.com"]
    page = call(env, "protection_get")["data"]
    assert page["allowed_words"] == ["dictionary.example.com"]


def test_a_new_allowed_site_can_be_added_without_the_words_skip(env):
    unlock(env)
    call(env, "protection_allow", "plain.example.com", False, False)
    assert cfg(env)["allowed"] == ["plain.example.com"] and cfg(env)["allowed_words"] == []
    assert protection.words_skip_sites(env.db) == frozenset()


def test_a_whole_site_gets_the_skip_on_every_one_of_its_names(env):
    unlock(env)
    call(env, "protection_allow", "youtube.com", True)
    names = cfg(env)["allowed"]
    assert len(names) > 1 and cfg(env)["allowed_words"] == sorted(names)


def test_allowing_a_site_that_is_already_allowed_keeps_its_own_choice(env):
    set_cfg(env, allowed=["old.com"])
    call(env, "protection_allow", "old.com")                                           # (nothing is new: no change, no ask)
    assert cfg(env)["allowed_words"] == []


def test_turning_it_on_for_an_existing_entry_needs_the_challenge_and_off_is_free(env):
    set_cfg(env, allowed=["old.com"])
    reply = call(env, "protection_allow_words", "old.com", True)
    assert asks(reply) and cfg(env)["allowed_words"] == []
    unlock(env)
    assert not asks(call(env, "protection_allow_words", "old.com", True))
    assert cfg(env)["allowed_words"] == ["old.com"]
    ab.save(env.db, {**ab.settings(env.db), "phrase": True, "length": 30})             # locked again
    reply = call(env, "protection_allow_words", "old.com", False)
    assert not asks(reply) and cfg(env)["allowed_words"] == []                         # stricter: free


def test_the_switch_refuses_a_site_that_is_not_allowed_and_bad_input(env):
    unlock(env)
    assert not call(env, "protection_allow_words", "nope.com", True)["ok"]
    set_cfg(env, allowed=["a.com"])
    assert not call(env, "protection_allow_words", "a.com", "yes")["ok"]
    assert cfg(env)["allowed_words"] == []


def test_taking_a_site_off_allowed_also_takes_it_off_the_skip_list(env):
    set_cfg(env, allowed=["a.com", "b.com"], allowed_words=["a.com", "b.com"])
    assert not asks(call(env, "protection_unallow", "a.com"))
    assert cfg(env)["allowed"] == ["b.com"] and cfg(env)["allowed_words"] == ["b.com"]


def test_what_loosens_is_judged_by_antibypass_in_python():
    old = {"enabled": ["scam"], "allowed": ["a.com", "b.com"], "allowed_words": ["a.com"], "manual": []}
    assert not ab.protection_looser(old, {**old, "allowed_words": []})                                # off: free
    assert not ab.protection_looser(old, {**old, "allowed": ["a.com"], "allowed_words": ["a.com"]})   # a site removed
    assert ab.protection_looser(old, {**old, "allowed_words": ["a.com", "b.com"]})                    # b.com newly skipped
    older = {k: v for k, v in old.items() if k != "allowed_words"}
    assert not ab.protection_looser(older, older) and not ab.protection_looser(old, older)            # older shape: none
    assert ab.protection_looser(older, old)                                                           # (a skip appears)
    assert ab.protection_looser({k: v for k, v in old.items() if k != "allowed_words"},
                                {**old, "allowed_words": ["b.com"]})
    # a skip for a site that is NOT allowed does nothing, so it loosens nothing
    assert not ab.protection_looser(old, {**old, "allowed_words": ["a.com", "zzz.com"]})


# ---------------------------------------------------------------- backup

def _backup_settings(env):
    return backup.export(env.db)


def test_a_backup_carries_the_skip_list_and_restores_it(env, tmp_path):
    set_cfg(env, allowed=["a.com", "b.com"], allowed_words=["b.com"])
    data = backup.export(env.db)
    path = tmp_path / "b.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    settings._check_shape(backup.load(str(path)))
    set_cfg(env, allowed=[], allowed_words=[])
    backup.restore(env.db, backup.load(str(path)))
    assert cfg(env)["allowed"] == ["a.com", "b.com"] and cfg(env)["allowed_words"] == ["b.com"]
    assert protection.words_skip_sites(env.db) == {"b.com"}


def test_an_old_backup_without_the_list_loads_with_every_entry_off(env, tmp_path):
    data = backup.export(env.db)
    old_protection = {"enabled": ["scam"], "allowed": ["old.com"], "info": {}, "custom": []}
    data["settings"][protection.SETTINGS_KEY] = json.dumps(old_protection)
    path = tmp_path / "old.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = backup.load(str(path))
    settings._check_shape(loaded)
    backup.restore(env.db, loaded)
    assert cfg(env)["allowed"] == ["old.com"] and cfg(env)["allowed_words"] == []


@pytest.mark.parametrize("bad", ["a.com", 5, {"a.com": 1}, ["a.com", 3], ["x" * 300], ["a"] * 2001])
def test_a_backup_with_a_damaged_skip_list_is_refused(env, bad):
    data = backup.export(env.db)
    data["settings"][protection.SETTINGS_KEY] = json.dumps({"allowed": ["a.com"], "allowed_words": bad})
    with pytest.raises(core.ApiError):
        settings._check_shape(data)


def test_the_app_log_names_the_change():
    import applog_changes
    old = json.dumps({"allowed": ["a.com"], "allowed_words": []})
    new = json.dumps({"allowed": ["a.com"], "allowed_words": ["a.com"]})
    assert applog_changes._protection(old, new) == [("change", "Protection: blocked words don't apply on allowed site a.com")]
    assert applog_changes._protection(new, old) == [("change", "Protection: blocked words apply again on allowed site a.com")]
    assert applog_changes._protection(old, json.dumps({"allowed": ["a.com"], "allowed_words": "junk"})) == []
