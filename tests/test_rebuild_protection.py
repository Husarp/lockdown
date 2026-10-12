"""R23: Blocking > Site protection (webapi/protection.py) - what is free, what waits for the challenge, the 10-second
grace, the previewed-address rule, the words, and the speed of "Check a site"."""
import json
import random
import statistics
import time
from datetime import datetime

import pytest

import antibypass as ab
import keywords
import webapi
from blocker import protection
from webapi import core, guard
from webapi import protection as api
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)


class Clock:
    """Trusted time and the monotonic clock, both set by the test."""

    def __init__(self):
        self.now, self.mono = NOW, 1000.0

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
    ctx.protection_dir = tmp_path / "lists"
    ctx.pushed = []
    ctx.publish = lambda event, payload=None, key=None: ctx.pushed.append((event, payload, key))
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})      # LOCKED: loosening asks
    api._previews.clear()
    return ctx


def call(env, name, *args):
    return getattr(env.api, name)(*args)


def data(env, name, *args):
    reply = call(env, name, *args)
    assert reply["ok"], reply
    return reply["data"]


def asks(reply) -> bool:
    return bool(reply.get("ok") and isinstance(reply["data"], dict) and reply["data"].get("needs_challenge"))


def stored(env):
    return env.db.get_setting(protection.SETTINGS_KEY, ""), env.db.get_setting(keywords.SETTINGS_KEY, "")


def cfg(env):
    return protection.settings(env.db)


def set_cfg(env, **changes):
    c = cfg(env)
    c.update(changes)
    protection.save_settings(env.db, c)


def unlock_free(env):
    """Anti-Bypass off: everything runs at once (for arranging)."""
    ab.save(env.db, {**ab.settings(env.db), "phrase": False})


def pass_challenge(env, reply):
    """Type the phrase for a ticket the Api returned -> the reply of challenge_submit."""
    ticket = reply["data"]["ticket"]
    shown = data(env, "challenge_begin", ticket)
    env.clock.mono += len(shown["phrase"]) * guard.MIN_MS_PER_CHAR / 1000 + 1
    return call(env, "challenge_submit", ticket, shown["phrase"])


def manual(key="mine1", name="Mine", hosts=("a.com", "b.com", "c.com")):
    return {"key": key, "name": name, "entries": [{"name": "", "host": h} for h in hosts]}


def with_manual(env, hosts=("a.com", "b.com", "c.com"), on=True, key="mine1"):
    c = cfg(env)
    c["manual"] = [manual(key, "Mine", hosts)]
    c["enabled"] = [k for k in c["enabled"] if k != key] + ([key] if on else [])
    protection.save_settings(env.db, c)


def with_custom(env, on=True, key="custom1"):
    c = cfg(env)
    c["custom"] = [{"key": key, "name": "Crypto", "url": "https://example.com/list.txt"}]
    c["enabled"] = [k for k in c["enabled"] if k != key] + ([key] if on else [])
    protection.save_settings(env.db, c)


# ---------------------------------------------------------------- what kind of method each is

def test_every_method_is_classified_by_what_it_can_do():
    kinds = dict(core.api_methods())
    assert {n: kinds[n] for n in ("protection_get", "manual_get", "protection_check", "words_get", "wordlist_get",
                                  "host_normalize")} == dict.fromkeys(
        ("protection_get", "manual_get", "protection_check", "words_get", "wordlist_get", "host_normalize"), "read")
    for name in ("protection_set_list_enabled", "protection_remove_custom", "manual_set_enabled", "manual_remove",
                 "manual_save_entries", "protection_allow", "words_update"):
        assert kinds[name] == "loosen", name
    for name in ("protection_connect", "manual_create", "protection_unallow"):
        assert kinds[name] == "tighten", name
    for name in ("protection_set_updates", "protection_update_now", "protection_preview"):
        assert kinds[name] == "ui", name


# ---------------------------------------------------------------- loosening while locked: a ticket, nothing changes

def _gated_cases(env):
    with_manual(env)
    set_cfg(env, custom=[{"key": "custom1", "name": "Crypto", "url": "https://example.com/l.txt"}],
            enabled=cfg(env)["enabled"] + ["custom1"])
    return [
        ("list off", lambda: call(env, "protection_set_list_enabled", "scam", False)),
        ("custom list removed while on", lambda: call(env, "protection_remove_custom", "custom1")),
        ("own list off", lambda: call(env, "manual_set_enabled", "mine1", False)),
        ("own list removed while on", lambda: call(env, "manual_remove", "mine1")),
        ("sites taken out of a list that is on", lambda: call(env, "manual_save_entries", "mine1", [["a.com", ""]])),
        ("allow anyway", lambda: call(env, "protection_allow", "fake-shop.com")),
        ("words check off", lambda: call(env, "words_update", {"enabled": False})),
        ("SafeSearch off", lambda: call(env, "words_update", {"safesearch": False})),
        ("YouTube Restricted Mode off", lambda: call(env, "words_update", {"youtube": False})),
        ("a ready list off", lambda: call(env, "words_update", {"lists": {"adult_en": False}})),
        ("your words off", lambda: call(env, "words_update", {"words_on": False})),
        ("a ready word turned off", lambda: call(env, "words_update", {"off": ["xxx"]})),
        ("an exception added", lambda: call(env, "words_update", {"exceptions": ["wikipedia.org"]})),
    ]


def test_every_loosening_path_returns_a_ticket_and_changes_nothing(env):
    keywords.save(env.db, {**keywords.settings(env.db), "words": ["casino"]})
    cases = _gated_cases(env)
    before = stored(env)
    for label, run in cases:
        reply = run()
        assert asks(reply), label
        assert stored(env) == before, label


def test_removing_a_word_of_yours_asks(env):
    keywords.save(env.db, {**keywords.settings(env.db), "words": ["casino", "poker"]})
    before = stored(env)
    assert asks(call(env, "words_update", {"words": ["casino"]}))
    assert stored(env) == before


def test_the_change_runs_only_after_the_challenge_is_passed(env):
    with_manual(env)
    reply = call(env, "protection_set_list_enabled", "scam", False)
    assert asks(reply) and "scam" in cfg(env)["enabled"]
    assert reply["data"]["changes"] == ["Switch the Scam protection list off"]
    done = pass_challenge(env, reply)
    assert done["ok"] and done["data"]["passed"] is True
    assert "scam" not in cfg(env)["enabled"]


def test_a_change_after_the_challenge_keeps_what_changed_meanwhile(env):
    reply = call(env, "protection_set_list_enabled", "scam", False)
    assert asks(reply)
    info = cfg(env)["info"]
    c = cfg(env)
    c["info"] = {**info, "adult": {"count": 5, "updated": "2026-10-11T10:00:00"}}     # the service wrote this meanwhile
    c["update_now"] = "2026-10-11T15:00:00"
    protection.save_settings(env.db, c)
    pass_challenge(env, reply)
    after = cfg(env)
    assert "scam" not in after["enabled"] and after["info"]["adult"]["count"] == 5 and after["update_now"]


def test_a_cancelled_challenge_changes_nothing(env):
    reply = call(env, "protection_allow", "fake-shop.com")
    before = stored(env)
    call(env, "challenge_cancel", reply["data"]["ticket"])
    assert stored(env) == before and cfg(env)["allowed"] == []


def test_with_anti_bypass_free_the_loosening_runs_at_once(env):
    unlock_free(env)
    assert data(env, "protection_set_list_enabled", "scam", False) == {"key": "scam", "enabled": False}
    assert "scam" not in cfg(env)["enabled"]
    assert data(env, "protection_allow", "Fake-Shop.com") == {"host": "fake-shop.com"}
    assert cfg(env)["allowed"] == ["fake-shop.com"]


# ---------------------------------------------------------------- stricter is free, loosening that isn't one is free too

def test_switching_a_list_on_is_free_while_locked(env):
    got = data(env, "protection_set_list_enabled", "gambling", True)
    assert got == {"key": "gambling", "enabled": True} and "gambling" in cfg(env)["enabled"]


def test_connecting_creating_and_unallowing_are_free_while_locked(env):
    got = data(env, "manual_create", "  Distractions   ")
    assert got["name"] == "Distractions" and got["key"] in cfg(env)["enabled"]
    set_cfg(env, allowed=["ok.example.com"])
    assert data(env, "protection_unallow", "OK.example.com") == {"host": "ok.example.com"}
    assert cfg(env)["allowed"] == []
    assert not asks(call(env, "protection_unallow", "not-there.com"))


def test_removing_a_list_that_is_off_is_free(env):
    with_manual(env, on=False)
    with_custom(env, on=False)
    assert data(env, "manual_remove", "mine1") == {"key": "mine1"}
    assert data(env, "protection_remove_custom", "custom1") == {"key": "custom1"}
    assert cfg(env)["manual"] == [] and cfg(env)["custom"] == []


def test_hosts_removed_from_an_enabled_list_loosen_from_a_disabled_one_do_not(env):
    with_manual(env, on=True)
    assert asks(call(env, "manual_save_entries", "mine1", [["a.com", ""]]))
    with_manual(env, on=False)
    assert data(env, "manual_save_entries", "mine1", [["a.com", ""]]) == {"key": "mine1", "count": 1}
    assert [e["host"] for e in cfg(env)["manual"][0]["entries"]] == ["a.com"]


def test_adding_sites_to_an_enabled_list_is_free(env):
    with_manual(env, on=True)
    got = data(env, "manual_save_entries", "mine1", [["new.com", "New"], ["a.com", ""], ["b.com", ""], ["c.com", ""]])
    assert got["count"] == 4
    assert cfg(env)["manual"][0]["entries"][0] == {"name": "New", "host": "new.com"}


def test_adding_words_is_free_and_removing_them_asks(env):
    got = data(env, "words_update", {"words": ["Casino", "  free   spins "]})
    assert got == {"changed": True} and keywords.settings(env.db)["words"] == ["casino", "free spins"]
    assert not asks(call(env, "words_update", {"words": ["casino", "free spins", "poker"]}))
    assert asks(call(env, "words_update", {"words": ["casino"]}))
    assert asks(call(env, "words_update", {"words": []}))


def test_turning_exceptions_on_is_looser_off_is_not(env):
    keywords.save(env.db, {**keywords.settings(env.db), "exceptions_on": False})
    reply = call(env, "words_update", {"exceptions_on": True})
    assert asks(reply) and "Turn exceptions back on" in reply["data"]["changes"]
    keywords.save(env.db, {**keywords.settings(env.db), "exceptions_on": True})
    assert data(env, "words_update", {"exceptions_on": False}) == {"changed": True}


def test_stricter_word_settings_are_free(env):
    unlock_free(env)
    data(env, "words_update", {"enabled": False, "safesearch": False, "youtube": False})
    ab.save(env.db, {**ab.settings(env.db), "phrase": True, "length": 30})
    got = data(env, "words_update", {"enabled": True, "safesearch": True, "youtube": True, "action": "back"})
    assert got == {"changed": True}
    cfg_words = keywords.settings(env.db)
    assert cfg_words["enabled"] and cfg_words["safesearch"] and cfg_words["youtube"] and cfg_words["action"] == "back"


def test_nothing_to_change_changes_nothing(env):
    before = stored(env)
    assert data(env, "protection_set_list_enabled", "scam", True) == {"key": "scam", "enabled": True}
    assert data(env, "words_update", {"enabled": True}) == {"changed": False}
    assert stored(env) == before


def test_update_settings_and_update_now_are_free(env):
    assert data(env, "protection_set_updates", False, 168) == {"auto": False, "every_hours": 168}
    c = cfg(env)
    assert c["auto"] is False and c["every_hours"] == 168
    assert data(env, "protection_update_now")["asked"] == "2026-10-11T15:42:17"
    assert cfg(env)["update_now"] == "2026-10-11T15:42:17"
    for bad in ((True, 5), ("yes", 24), (True, True), (True, "24"), (True, 24.0)):
        assert not call(env, "protection_set_updates", *bad)["ok"]


# ---------------------------------------------------------------- the 10-second grace

def test_an_exact_undo_of_a_tightening_inside_ten_seconds_is_free(env):
    data(env, "protection_set_list_enabled", "gambling", True)          # a tightening (free) ...
    env.clock.mono += guard.GRACE_SEC - 1
    got = call(env, "protection_set_list_enabled", "gambling", False)    # ... undone at once: free
    assert got["ok"] and not asks(got) and "gambling" not in cfg(env)["enabled"]
    again = call(env, "protection_set_list_enabled", "gambling", True)
    assert not asks(again)
    env.clock.mono += 1
    assert not asks(call(env, "protection_set_list_enabled", "gambling", False))          # (the second undo is fresh)
    assert asks(call(env, "protection_set_list_enabled", "scam", False))                  # another list: no grace


def test_the_grace_is_used_up_by_the_undo(env):
    data(env, "protection_set_list_enabled", "gambling", True)
    assert not asks(call(env, "protection_set_list_enabled", "gambling", False))
    data(env, "protection_set_list_enabled", "gambling", True)       # a new tightening gets a new grace ...
    data(env, "protection_set_list_enabled", "anime", True)          # ... until the next tightening replaces it
    assert asks(call(env, "protection_set_list_enabled", "gambling", False))


def test_after_ten_seconds_the_undo_asks(env):
    data(env, "protection_set_list_enabled", "gambling", True)
    env.clock.mono += guard.GRACE_SEC + 0.5
    assert asks(call(env, "protection_set_list_enabled", "gambling", False))
    assert "gambling" in cfg(env)["enabled"]


def test_the_grace_is_exact_not_a_blank_cheque(env):
    with_manual(env, hosts=["a.com", "b.com", "c.com"])
    data(env, "manual_save_entries", "mine1", [["d.com", ""], ["a.com", ""], ["b.com", ""], ["c.com", ""]])   # adds one
    # taking out OTHER sites than the one just added is not an undo
    assert asks(call(env, "manual_save_entries", "mine1", [["d.com", ""]]))
    assert [e["host"] for e in cfg(env)["manual"][0]["entries"]] == ["d.com", "a.com", "b.com", "c.com"]
    # the exact undo is free
    assert not asks(call(env, "manual_save_entries", "mine1", [["a.com", ""], ["b.com", ""], ["c.com", ""]]))


def test_a_list_created_a_moment_ago_can_be_deleted_at_once(env):
    key = data(env, "manual_create", "Oops")["key"]
    assert not asks(call(env, "manual_remove", key))
    assert cfg(env)["manual"] == []


def test_allowing_is_not_covered_by_a_grace_it_never_noted(env):
    assert asks(call(env, "protection_allow", "x.example.com"))


def test_unallow_then_allow_again_within_ten_seconds_is_free(env):
    set_cfg(env, allowed=["x.example.com"])
    data(env, "protection_unallow", "x.example.com")
    assert not asks(call(env, "protection_allow", "x.example.com"))
    assert cfg(env)["allowed"] == ["x.example.com"]


def test_the_words_grace_is_exact_too(env):
    data(env, "words_update", {"youtube": True, "words": ["casino"]})
    unlock_free(env)
    data(env, "words_update", {"youtube": False})
    ab.save(env.db, {**ab.settings(env.db), "phrase": True, "length": 30})
    data(env, "words_update", {"youtube": True})                    # a tightening, noted
    assert not asks(call(env, "words_update", {"youtube": False}))   # the exact undo: free
    data(env, "words_update", {"youtube": True})
    assert asks(call(env, "words_update", {"youtube": False, "safesearch": False}))      # more than the undo
    env.clock.mono += guard.GRACE_SEC + 1
    assert asks(call(env, "words_update", {"youtube": False}))


# ---------------------------------------------------------------- the list address

@pytest.mark.parametrize("url", ["ftp://example.com/list.txt", "file:///etc/passwd", "javascript:alert(1)",
                                 "example.com/list.txt", "http://", "https:///x", "//evil.example/x", "",
                                 "https://exa mple.com/x", "https://example.com/\x00", "gopher://example.com",
                                 "HTTPS-ish://example.com"])
def test_preview_refuses_anything_but_http_and_https(env, url, monkeypatch):
    monkeypatch.setattr(protection, "preview", lambda u: pytest.fail("must not download"))
    reply = call(env, "protection_preview", url)
    assert reply["ok"] is False and "full address" in reply["error"]


@pytest.mark.parametrize("bad", [None, 5, ["https://example.com/x"], {"url": "https://example.com/x"}])
def test_preview_refuses_other_types(env, bad):
    assert call(env, "protection_preview", bad)["ok"] is False


def _preview(env, monkeypatch, url="https://example.com/list.txt", result=(1234, ["a.com", "b.com"])):
    monkeypatch.setattr(protection, "preview", lambda u: result if not isinstance(result, Exception) else (_ for _ in ()).throw(result))
    token = data(env, "protection_preview", url)["token"]
    for _ in range(200):
        if env.pushed:
            break
        time.sleep(0.01)
    return token


def test_a_good_preview_is_pushed_and_can_be_connected_once(env, monkeypatch):
    token = _preview(env, monkeypatch)
    event, payload, key = env.pushed[-1]
    assert event == "protection_preview" and key == token
    assert payload == {"token": token, "ok": True, "count": 1234, "sample": ["a.com", "b.com"],
                       "text": "1,234 sites, e.g. a.com, b.com"}
    got = data(env, "protection_connect", "  Crypto  ", "https://example.com/list.txt", token)
    assert got["name"] == "Crypto" and got["count"] == 1234 and "Connected Crypto (1,234 sites)" in got["text"]
    c = cfg(env)
    assert c["custom"] == [{"key": got["key"], "name": "Crypto", "url": "https://example.com/list.txt"}]
    assert got["key"] in c["enabled"]
    assert call(env, "protection_connect", "Again", "https://example.com/list.txt", token)["ok"] is False   # single use


def test_connect_only_takes_the_address_that_was_previewed(env, monkeypatch):
    token = _preview(env, monkeypatch)
    for url, tok in (("https://evil.example/list.txt", token), ("https://example.com/list.txt", "nope"),
                     ("https://example.com/list.txt", None), ("ftp://example.com/list.txt", token)):
        assert call(env, "protection_connect", "X", url, tok)["ok"] is False
    assert cfg(env)["custom"] == []


def test_connect_without_a_name_uses_the_host(env, monkeypatch):
    token = _preview(env, monkeypatch, url="https://lists.example.org/block.txt")
    got = data(env, "protection_connect", "", "https://lists.example.org/block.txt", token)
    assert got["name"] == "lists.example.org"


def test_a_failed_preview_says_why(env, monkeypatch):
    _preview(env, monkeypatch, result=ValueError("No sites found there - is it a block list?"))
    assert env.pushed[-1][1]["ok"] is False and env.pushed[-1][1]["error"].startswith("No sites found there")
    env.pushed.clear()
    _preview(env, monkeypatch, result=OSError("timed out"))
    assert env.pushed[-1][1]["error"] == "Couldn't download it: timed out"


def test_connect_is_refused_without_a_good_preview(env):
    assert call(env, "protection_connect", "X", "https://example.com/l.txt", "token")["ok"] is False


def test_at_most_two_previews_run_at_once(env, monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(protection, "preview", lambda u: (gate.wait(5), (5, ["a.com"]))[1])
    assert call(env, "protection_preview", "https://a.example/x")["ok"]
    assert call(env, "protection_preview", "https://b.example/x")["ok"]
    assert call(env, "protection_preview", "https://c.example/x")["ok"] is False
    gate.set()
    for _ in range(200):
        if len(env.pushed) == 2:
            break
        time.sleep(0.01)
    assert call(env, "protection_preview", "https://c.example/x")["ok"]
    gate.set()


# ---------------------------------------------------------------- what the page sends is checked

def test_site_addresses_are_normalised_and_refused_in_python(env):
    assert data(env, "host_normalize", "https://www.Reddit.com/r/x") == "reddit.com"
    got = call(env, "host_normalize", "not a site")
    assert got["ok"] is False and got["error"].startswith("Not a valid domain")
    assert call(env, "host_normalize", 5)["ok"] is False
    assert call(env, "host_normalize", "a" * 5000)["ok"] is False
    unlock_free(env)
    assert call(env, "protection_allow", "localhost")["ok"] is False and cfg(env)["allowed"] == []
    assert call(env, "protection_allow", "../etc")["ok"] is False


def test_saved_sites_are_normalised_deduplicated_and_kept_in_order(env):
    with_manual(env, hosts=["a.com"], on=False)
    got = data(env, "manual_save_entries", "mine1", [["https://www.NEW.com/x", "  New   site "], ["a.com", ""],
                                                      ["new.com", "again"], ["Z.org", ""]])
    assert got["count"] == 3
    assert cfg(env)["manual"][0]["entries"] == [{"name": "New site", "host": "new.com"}, {"name": "", "host": "a.com"},
                                                {"name": "", "host": "z.org"}]


def test_a_bad_site_in_the_list_saves_nothing(env):
    with_manual(env, on=False)
    before = stored(env)
    for bad in ([["a.com", ""], ["not a site", ""]], [["a.com"]], [["a.com", 5]], "a.com", [["a.com", "x" * 200]],
                [["a.com", "bad\x00name"]], [{"host": "a.com"}]):
        assert call(env, "manual_save_entries", "mine1", bad)["ok"] is False
    assert stored(env) == before
    assert call(env, "manual_save_entries", "nope", [])["ok"] is False
    assert call(env, "manual_save_entries", "../x", [])["ok"] is False


def test_keys_must_be_lists_that_exist(env):
    for name, args in (("protection_set_list_enabled", ("nope", False)), ("protection_set_list_enabled", ("mine1", False)),
                       ("protection_set_list_enabled", ({"k": 1}, False)), ("protection_set_list_enabled", ("scam", "no")),
                       ("manual_set_enabled", ("scam", False)), ("manual_remove", ("scam",)),
                       ("protection_remove_custom", ("scam",)), ("manual_get", ("a b",))):
        assert call(env, name, *args)["ok"] is False, (name, args)
    assert data(env, "manual_get", "mine9") is None


def test_the_word_settings_are_checked(env):
    for bad in ({}, None, [], {"unknown": True}, {"enabled": "yes"}, {"enabled": 1}, {"action": "explode"},
                {"lists": {"adult_xx": True}}, {"lists": {"adult_en": "no"}}, {"lists": []}, {"off": ["not-a-word"]},
                {"off": "xxx"}, {"off": [5]}, {"words": "a"}, {"words": [5]}, {"words": ["x" * 200]},
                {"exceptions": ["bad\x00"]}, {"words": ["a"] * 6000}):
        assert call(env, "words_update", bad)["ok"] is False, bad
    assert keywords.settings(env.db) == keywords.settings(env.db)


def test_empty_and_duplicate_words_are_dropped(env):
    unlock_free(env)
    data(env, "words_update", {"words": ["B", " ", "b", "a  b", "", "A"], "exceptions": ["Wikipedia.org", "wikipedia.org"]})
    stored_cfg = keywords.settings(env.db)
    assert stored_cfg["words"] == ["b", "a b", "a"] and stored_cfg["exceptions"] == ["wikipedia.org"]


def test_a_new_list_needs_a_name_and_has_a_limit(env):
    assert call(env, "manual_create", "   ")["ok"] is False
    assert call(env, "manual_create", "x" * 100)["ok"] is False
    assert call(env, "manual_create", 5)["ok"] is False
    for i in range(api.MAX_MANUAL):
        assert call(env, "manual_create", f"L{i}")["ok"]
    assert call(env, "manual_create", "one too many")["ok"] is False


def test_a_site_list_is_capped(env):
    with_manual(env, on=False)
    too_many = [[f"s{i}.com", ""] for i in range(api.MAX_ENTRIES + 1)]
    assert call(env, "manual_save_entries", "mine1", too_many)["ok"] is False


# ---------------------------------------------------------------- the page's reads

def test_the_lists_follow_the_old_status_table(env):
    set_cfg(env, enabled=["scam", "phishing", "malware", "gambling"],
            info={"scam": {"count": 1234, "updated": "2026-10-11T15:00:00"},
                  "phishing": {"count": 99, "updated": "2026-10-10T15:00:00", "failed": "2026-10-11T14:00:00"},
                  "malware": {"failed": "2026-10-11T14:00:00"},
                  "adult": {"count": 7, "updated": "2026-10-11T09:00:00"}})
    env.db.set_setting(protection.PROGRESS_KEY, json.dumps({"key": "gambling", "part": 1, "parts": 2, "done": 12_900_000,
                                                            "total": 47_000_000}))
    rows = {r["key"]: r for r in data(env, "protection_get")["lists"]}
    assert rows["scam"]["state"] == "updated" and rows["scam"]["count"] == 1234
    assert rows["scam"]["updated"] == "2026-10-11T15:00:00"
    assert rows["phishing"]["state"] == "update_failed" and rows["phishing"]["count"] == 99
    assert rows["malware"]["state"] == "failed" and rows["malware"]["count"] is None
    assert rows["gambling"]["state"] == "downloading" and rows["gambling"]["text"] == "12.3 of 44.8 MB (part 1 of 2)"
    assert rows["gambling"]["pct"] == 13
    assert rows["adult"]["state"] == "updated" and rows["adult"]["enabled"] is False      # (off, but it has a count)
    assert rows["anime"]["state"] == "off" and rows["anime"]["count"] is None


def test_an_enabled_list_with_no_info_waits_for_the_service(env):
    rows = {r["key"]: r for r in data(env, "protection_get")["lists"]}
    assert rows["scam"]["state"] == "waiting" and rows["gambling"]["state"] == "off"
    assert rows["scam"]["desc"].startswith("fake shops")


def test_protection_get_describes_the_lists_and_the_settings(env):
    with_custom(env)
    with_manual(env, hosts=["a.com", "b.com"])
    set_cfg(env, allowed=["z.com", "b.com"], auto=False, every_hours=12)
    got = data(env, "protection_get")
    assert [r["key"] for r in got["lists"]] == [*protection.LISTS, "custom1"]
    custom = got["lists"][-1]
    assert custom["custom"] is True and custom["desc"] == "your list · https://example.com/list.txt"
    assert got["manual"] == [{"key": "mine1", "name": "Mine", "count": 2, "enabled": True}]
    assert got["allowed"] == ["b.com", "z.com"] and got["auto"] is False and got["every_hours"] == 12
    assert got["intervals"] == [[6, "every 6 hours"], [12, "every 12 hours"], [24, "daily"], [168, "weekly"]]
    assert got["now"] == "2026-10-11T15:42:17" and got["rev"]


def test_the_rev_changes_when_the_service_writes(env):
    first = data(env, "protection_get")["rev"]
    c = cfg(env)
    c["info"]["scam"] = {"count": 3, "updated": "2026-10-11T15:00:00"}
    protection.save_settings(env.db, c)
    second = data(env, "protection_get")["rev"]
    assert second != first
    data(env, "protection_set_list_enabled", "gambling", True)
    assert data(env, "protection_get")["rev"] == second           # (the page's own changes don't count)


def test_manual_get_gives_the_sites(env):
    with_manual(env, hosts=["a.com", "b.com"])
    c = cfg(env)
    c["manual"][0]["entries"][0]["name"] = "The A"
    protection.save_settings(env.db, c)
    assert data(env, "manual_get", "mine1") == {"key": "mine1", "name": "Mine", "enabled": True,
                                               "entries": [["a.com", "The A"], ["b.com", ""]]}


def test_words_get_and_wordlist_get(env):
    keywords.save(env.db, {**keywords.settings(env.db), "words": ["a", "b"], "exceptions": [], "off": ["xxx", "porno*", "seks"],
                           "action": "back", "exceptions_on": False})
    got = data(env, "words_get")
    assert got["action"] == "back" and got["actions"] == [["close", "Close the tab"], ["back", "Go back"]]
    rows = {r["key"]: r for r in got["lists"]}
    assert rows["adult_en"]["total"] == len(keywords.READY["adult_en"][1]) and rows["adult_en"]["off"] == 1
    assert rows["adult_pl"]["off"] == 2 and rows["adult_en"]["name"] == "Adult words - English"
    assert got["words"] == {"on": True, "count": 2} and got["exceptions"] == {"on": False, "count": 0}
    en = data(env, "wordlist_get", "adult_en")
    assert en["kind"] == "ready" and en["off"] == ["xxx"] and en["other_off"] == ["porno*", "seks"]
    assert len(en["words"]) == len(keywords.READY["adult_en"][1])
    mine = data(env, "wordlist_get", "words")
    assert mine == {"kind": "words", "name": "words", "title": "Your words", "words": ["a", "b"],
                    "hint": "a word or a few words"}
    assert data(env, "wordlist_get", "exceptions")["title"] == "Exceptions"
    assert call(env, "wordlist_get", "nothing")["ok"] is False and call(env, "wordlist_get", 5)["ok"] is False


def test_a_ready_list_save_sends_its_off_words_with_the_other_lists(env):
    unlock_free(env)
    data(env, "words_update", {"off": ["seks"]})                          # a Polish word is off already
    en = data(env, "wordlist_get", "adult_en")
    assert en["other_off"] == ["seks"]
    data(env, "words_update", {"off": en["other_off"] + ["xxx", "nsfw"]})   # what the dialog sends for the English list
    assert sorted(keywords.settings(env.db)["off"]) == ["nsfw", "seks", "xxx"]


def test_download_text_says_how_much_of_how_many():
    cases = [({"done": 500, "total": 0, "part": 1, "parts": 1}, "0 KB"),
             ({"done": 5000, "total": 9000, "part": 1, "parts": 1}, "4 of 8 KB"),
             ({"done": 12_900_000, "total": 47_000_000, "part": 2, "parts": 3}, "12.3 of 44.8 MB (part 2 of 3)"),
             ({"done": 3_000_000, "total": 0, "part": 1, "parts": 2}, "2.9 MB (part 1 of 2)")]
    for p, text in cases:
        assert api.download_text(p) == text


# ---------------------------------------------------------------- Check a site

def write_list(env, key, lines):
    folder = env.protection_dir
    folder.mkdir(parents=True, exist_ok=True)
    protection.list_path(key, folder).write_text("\n".join(lines) + "\n")


def test_check_a_site(env):
    write_list(env, "scam", ["fake-shop.com", "www.other-shop.com"])
    write_list(env, "adult", ["*.adult.example", "ok.example"])
    write_list(env, "gambling", ["bet.example.com"])               # a list that is OFF
    set_cfg(env, allowed=["ok.example"])
    with_manual(env, hosts=["mine.example"], on=True)
    check = lambda host: data(env, "protection_check", host)
    assert check("fake-shop.com") == {"host": "fake-shop.com", "allowed_by": None,
                                      "lists": [{"key": "scam", "name": "Scam", "on": True, "mine": False}]}
    assert [x["key"] for x in check("https://www.Other-Shop.com/path")["lists"]] == ["scam"]
    assert [x["key"] for x in check("a.b.adult.example")["lists"]] == ["adult"]           # a "*." entry: subdomains too
    assert check("adult.example")["lists"][0]["key"] == "adult"
    assert check("sub.fake-shop.com")["lists"] == []                                        # an exact entry: not subdomains
    got = check("bet.example.com")
    assert got["lists"] == [{"key": "gambling", "name": "Gambling", "on": False, "mine": False}]
    assert check("nothing.example")["lists"] == []
    assert check("ok.example")["allowed_by"] == "ok.example"
    assert check("deep.ok.example")["allowed_by"] == "ok.example"                           # a parent you allowed covers it
    mine = check("sub.mine.example")["lists"]
    assert mine == [{"key": "mine1", "name": "Mine", "on": True, "mine": True}]
    with_manual(env, hosts=["mine.example"], on=False)
    assert check("mine.example")["lists"] == []                                              # (your list is off: not blocking)
    bad = call(env, "protection_check", "not a site")
    assert bad["ok"] is False and bad["error"].startswith("Not a valid domain")


def test_check_follows_a_list_file_that_changed(env):
    write_list(env, "scam", ["one.example"])
    assert [x["key"] for x in data(env, "protection_check", "one.example")["lists"]] == ["scam"]
    time.sleep(0.02)
    write_list(env, "scam", ["two.example"])
    import os
    path = protection.list_path("scam", env.protection_dir)
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert data(env, "protection_check", "one.example")["lists"] == []
    assert [x["key"] for x in data(env, "protection_check", "two.example")["lists"]] == ["scam"]


def test_check_on_a_million_sites_is_a_lookup_not_a_read(env):
    folder = env.protection_dir
    folder.mkdir(parents=True, exist_ok=True)
    n = 1_000_000
    with open(protection.list_path("adult", folder), "w") as f:
        f.writelines(f"site{i}.example.com\n" for i in range(n - 1))
        f.write("*.needle.example\n")
    t = time.perf_counter()
    first = data(env, "protection_check", "deep.needle.example")
    loaded_ms = (time.perf_counter() - t) * 1000
    assert [x["key"] for x in first["lists"]] == ["adult"]
    times = []
    for i in range(40):
        t = time.perf_counter()
        got = call(env, "protection_check", f"x{i}.needle.example" if i % 2 else f"nothing{i}.example.org")
        times.append((time.perf_counter() - t) * 1000)
        assert got["ok"]
    print(f"\nprotection_check, 1 M sites: first call (loads the table) {loaded_ms:.0f} ms; after: "
          f"median {statistics.median(times):.2f} ms, max {max(times):.2f} ms")
    assert statistics.median(times) <= 5
    assert sorted(times)[-3] <= 25            # (a busy machine may hiccup once or twice)


def test_protection_get_stays_fast_with_a_big_list_of_your_own(env):
    c = cfg(env)
    c["manual"] = [{"key": "mine1", "name": "Big", "entries": [{"name": "", "host": f"site{i}.example.com"}
                                                                for i in range(20_000)]}]
    c["enabled"].append("mine1")
    protection.save_settings(env.db, c)
    data(env, "protection_get")
    times = []
    for _ in range(15):
        t = time.perf_counter()
        data(env, "protection_get")
        times.append((time.perf_counter() - t) * 1000)
    assert statistics.median(times) < 25, times


# ---------------------------------------------------------------- the progress the agent pushes

def test_progress_view():
    assert api.progress_view(None, "r") == {"key": None, "text": "", "pct": None, "rev": "r"}
    p = {"key": "adult", "part": 2, "parts": 2, "done": 5_000_000, "total": 10_000_000}
    assert api.progress_view(p, "r") == {"key": "adult", "text": "4.8 of 9.5 MB (part 2 of 2)", "pct": 75, "rev": "r"}
    assert api.progress_view({"key": "x", "part": 1, "parts": 1, "done": 100, "total": 0})["pct"] is None


def test_the_watcher_pushes_progress_and_the_fingerprint_and_sleeps_while_hidden(env, tmp_path):
    from resident.protection_watch import ProtectionWatcher
    from resident.workers import Hub
    hub = Hub(tmp_path / "t.db")
    pushed, shown = [], [True]
    watcher = ProtectionWatcher(hub, lambda event, payload=None, key=None: pushed.append((event, payload)),
                                lambda: shown[0])
    try:
        watcher.tick()
        rev = pushed[-1][1]["rev"]
        assert pushed == [("protection_progress", {"key": None, "text": "", "pct": None, "rev": rev})]
        watcher.tick()
        assert len(pushed) == 1                                   # nothing changed: nothing sent
        env.db.set_setting(protection.PROGRESS_KEY, json.dumps({"key": "adult", "part": 1, "parts": 1, "done": 2_097_152,
                                                                "total": 4_194_304}))
        watcher.tick()
        assert pushed[-1][1] == {"key": "adult", "text": "2.0 of 4.0 MB", "pct": 50, "rev": rev}
        env.db.set_setting(protection.PROGRESS_KEY, "")
        c = cfg(env)
        c["info"]["adult"] = {"count": 9, "updated": "2026-10-11T15:00:00"}
        protection.save_settings(env.db, c)
        watcher.tick()
        assert pushed[-1][1]["key"] is None and pushed[-1][1]["rev"] != rev
        shown[0] = False
        before = len(pushed)
        env.db.set_setting(protection.PROGRESS_KEY, json.dumps({"key": "scam", "part": 1, "parts": 1, "done": 1, "total": 2}))
        watcher.tick()
        assert len(pushed) == before                              # hidden: it did not even look
        shown[0] = True
        watcher.tick()
        assert pushed[-1][1]["key"] == "scam"
    finally:
        watcher.teardown()
        watcher._db and watcher._db.close()


def test_the_watcher_joins_the_agents_workers(env, tmp_path):
    from resident import protection_watch
    from resident.workers import Workers
    pool = Workers(tmp_path / "t.db")
    before = len(pool.workers)
    watcher = protection_watch.attach(pool, lambda *a, **k: None, lambda: True)
    assert pool.workers[-1] is watcher and len(pool.workers) == before + 1
