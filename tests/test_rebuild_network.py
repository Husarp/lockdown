"""R16: the Network Log's Api (webapi/network.py): the cached last hour, server-side filters and fuzzy search, the summary
and graph numbers, blocked visits merged in, the live feed's deltas, Export CSV, and how fast it all is on 20 000 rows.
The page is tested in tests/ui/test_page_netlog.py."""
import csv
import statistics
import threading
import time
from datetime import datetime, timedelta

import pytest

import search
import webapi
from db import Database
from resident import workers
from synthdata import build
from webapi import core, network

NOW = datetime(2026, 10, 11, 15, 42, 17)


def minute(back):
    return (NOW.replace(second=0) - timedelta(minutes=back)).strftime("%Y-%m-%d %H:%M")


def row(back=0, exe="chrome.exe", ip="1.1.1.1", port=443, domain="example.com", count=1, windows=0, local=0):
    return {"minute": minute(back), "exe": exe, "ip": ip, "port": port, "domain": domain, "count": count,
            "windows": windows, "local": local}


@pytest.fixture
def env(tmp_path):
    ctx = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    ctx.api = core.build_api(ctx)
    ctx.sent = []
    ctx.publish = lambda event, payload=None, key=None: ctx.sent.append((event, payload))
    return ctx


def get(ctx, filters=None, limit=100, live=False):
    reply = ctx.api.network_get(filters or {}, limit, live)
    assert reply["ok"], reply
    return reply["data"]


def fresh(ctx):
    """Make the next call read the database again (the cache keeps what it read for 3 s)."""
    network._log(ctx).fetched = 0.0


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """The synthetic database with 20 000 network rows (the same rows scripts/perf_ui.py uses)."""
    path = tmp_path_factory.mktemp("big") / "big.db"
    Database(path)
    build(path, NOW.date(), days=5, extra_items=40, network_rows=20_000)
    ctx = webapi.make_context(path, clock=lambda db: NOW)
    ctx.api = core.build_api(ctx)
    return ctx


# ---------------------------------------------------------------- what the rows are

def test_rows_are_connections_and_blocked_visits_newest_first(env):
    env.db.add_network([row(5, domain="old.com"), row(0, domain="new.com", count=2), row(0, ip="2.2.2.2", domain="busy.com", count=9)])
    env.db.add_block_event("reddit.com", 1, "Reddit", "permanent", None, NOW - timedelta(seconds=10))
    env.db.add_block_event("chrome.exe", 1, "Chrome", "permanent", None, NOW - timedelta(seconds=9))        # an app closed: not a visit
    env.db.add_block_event("game.exe", 1, "Game", "schedule", None, NOW - timedelta(seconds=8))
    d = get(env)
    assert [r["site"] for r in d["rows"]] == ["busy.com", "new.com", "reddit.com", "old.com"]       # a minute: busiest first, then blocked
    visit = d["rows"][2]
    assert (visit["app"], visit["b"], visit["rule"], visit["exe"], visit["port"]) == ("Blocked visit", 1, "Permanently blocked", "", "")
    assert d["rows"][0]["rule"] == "\u2013" and d["rows"][0]["t"] == "15:42" and d["rows"][0]["app"] == "Chrome"
    assert d["total"] == 4 and d["older"] == 0 and d["empty"] is False


def test_the_hour_is_the_last_hour_and_an_empty_log_says_so(env):
    assert get(env)["empty"] is True and get(env)["rows"] == []
    env.db.add_network([row(70, domain="too-old.com"), row(59, domain="just-in.com")])
    network._log(env).refresh(env, force=True)                              # (rows of old minutes are only read by a first / forced read)
    assert [r["site"] for r in get(env)["rows"]] == ["just-in.com"]


def test_windows_and_local_rows_follow_their_switches(env):
    env.db.add_network([row(0, domain="a.com"), row(1, exe="svchost.exe", ip="3.3.3.3", domain="update.com", windows=1),
                        row(2, ip="192.168.1.5", domain="", local=1), row(3, exe="svchost.exe", ip="192.168.1.6", domain="", windows=1, local=1)])
    assert [r["site"] for r in get(env)["rows"]] == ["a.com"]
    assert [r["site"] for r in get(env, {"windows": True})["rows"]] == ["a.com", "update.com"]
    assert [r["site"] for r in get(env, {"local": True})["rows"]] == ["a.com", "192.168.1.5"]
    assert len(get(env, {"windows": True, "local": True})["rows"]) == 4
    assert [a["exe"] for a in get(env)["apps"]] == ["chrome.exe"]                                  # the app menu leaves Windows' own out
    assert [a["name"] for a in get(env, {"windows": True})["apps"]] == ["Chrome", "Svchost"]


def test_status_and_app_filters(env):
    env.db.add_network([row(0, exe="chrome.exe"), row(1, exe="discord.exe", ip="4.4.4.4", domain="discord.com")])
    env.db.add_block_event("reddit.com", 1, "Reddit", "limit", None, NOW)
    assert [r["b"] for r in get(env, {"status": "blocked"})["rows"]] == [1]
    assert [r["b"] for r in get(env, {"status": "allowed"})["rows"]] == [0, 0]
    assert [r["exe"] for r in get(env, {"app": "discord.exe"})["rows"]] == ["discord.exe"]
    assert get(env, {"app": "discord.exe", "status": "blocked"})["rows"] == []


def test_app_names_come_from_the_blocklist_then_the_file_name(env):
    env.db.add_item("Steam Client", ["steam.exe"], "app", "manual", [{"rule_type": "permanent"}])
    env.db.add_network([row(0, exe="steam.exe", domain="a.com"), row(1, exe="some_tool.exe", ip="5.5.5.5", domain="b.com"),
                        row(2, exe="pythonw.exe", ip="6.6.6.6", domain="c.com")])
    assert [r["app"] for r in get(env)["rows"]] == ["Steam Client", "Some_tool", "Lockdown"]
    assert [a["name"] for a in get(env)["apps"]] == ["Lockdown", "Some_tool", "Steam Client"]


def test_the_rule_column_names_every_reason(env):
    for i, reason in enumerate(["permanent", "schedule", "limit", "switches", "temporary", "mode", "protection:scam", "odd_one"]):
        env.db.add_block_event(f"site{i}.com", 1, "x", reason, None, NOW - timedelta(seconds=i))
    rules = {r["host"]: r["rule"] for r in get(env)["rows"]}
    assert rules["site0.com"] == "Permanently blocked" and rules["site1.com"] == "Outside its allowed hours"
    assert rules["site2.com"] == "Over its time limit" and rules["site3.com"] == "Opened too often"
    assert rules["site4.com"] == "Temporarily blocked" and rules["site5.com"] == "Blocked by a mode"
    assert rules["site6.com"] == "On a protection list" and rules["site7.com"] == "Odd one"


def test_the_summary_line(env):
    env.db.add_network([row(0, count=3, domain="a.com"), row(1, count=2, ip="2.2.2.2", domain="b.com"),
                        row(2, exe="discord.exe", ip="3.3.3.3", domain="a.com", count=1)])
    env.db.add_block_event("reddit.com", 1, "Reddit", "permanent", None, NOW)
    s = get(env)["summary"]
    assert s["text"] == "Last hour: 7 connections \u00b7 1 blocked \u00b7 3 sites \u00b7 2 apps \u00b7 top: Chrome"
    assert (s["connections"], s["blocked"], s["sites"], s["apps"], s["top"]) == (7, 1, 3, 2, "Chrome")
    assert get(env, {"query": "nothing-like-it"})["summary"]["text"] == "Last hour: 0 connections \u00b7 0 blocked \u00b7 0 sites \u00b7 0 apps"
    assert "top:" not in get(env, {"status": "blocked"})["summary"]["text"]


def test_a_site_with_no_name_shows_its_address(env):
    env.db.add_network([row(0, ip="8.8.4.4", domain="", count=2)])
    r = get(env)["rows"][0]
    assert (r["site"], r["host"], r["reg"], r["n"]) == ("8.8.4.4", "", "", 2)


# ---------------------------------------------------------------- limits and bad input

def test_the_limit_and_older(env):
    env.db.add_network([row(i % 50, ip=f"9.9.{i // 200}.{i % 200}", domain=f"s{i}.com") for i in range(250)])
    d = get(env, limit=100)
    assert len(d["rows"]) == 100 and d["total"] == 250 and d["older"] == 150
    assert len(get(env, limit=500)["rows"]) == 250 and get(env, limit=500)["older"] == 0


@pytest.mark.parametrize("filters", [{"status": "everything"}, {"app": 5}, {"query": ["a"]}, "all", 3])
def test_bad_filters_are_refused(env, filters):
    reply = env.api.network_get(filters, 100, False)
    assert reply["ok"] is False and "Bad request" in reply["error"]


@pytest.mark.parametrize("limit", [0, -1, "100", 1.5, True, None])
def test_a_bad_limit_is_refused(env, limit):
    assert env.api.network_get({}, limit, False)["ok"] is False


def test_a_huge_limit_is_capped_and_a_long_query_cut(env):
    env.db.add_network([row(0)])
    assert get(env, {"query": "x" * 5000}, 10 ** 9)["rows"] == []
    assert network.parse_filters({"query": "  " + "x" * 500}).query == "x" * network.MAX_QUERY
    assert network.parse_limit(10 ** 9) == network.MAX_LIMIT


def test_the_methods_are_classified(env):
    kinds = dict(core.registry.methods())
    assert kinds["network_get"] == "read" and kinds["network_graph"] == "read" and kinds["network_block_target"] == "read"
    assert kinds["network_live_stop"] == "ui" and kinds["network_export_csv"] == "ui"
    assert not any(n.startswith("network_") and k in ("loosen", "tighten") for n, k in kinds.items())     # nothing here changes a block


# ---------------------------------------------------------------- the search

def test_search_finds_domain_ip_exe_and_app_name_with_typos(env):
    env.db.add_item("Steam Client", ["steam.exe"], "app", "manual", [{"rule_type": "permanent"}])
    env.db.add_network([row(0, exe="steam.exe", ip="10.1.2.3", domain="store.steampowered.com"),
                        row(1, exe="chrome.exe", ip="172.16.0.9", domain="www.youtube.com"),
                        row(2, exe="discord.exe", ip="9.9.9.9", domain="gateway.discord.gg")])
    found = lambda q: sorted(r["site"] for r in get(env, {"query": q})["rows"])
    assert found("youtube") == ["www.youtube.com"]
    assert found("yotube") == ["www.youtube.com"]                            # a typo
    assert found("172.16") == ["www.youtube.com"]                            # the address
    assert found("steam client") == ["store.steampowered.com"]               # the app's name
    assert found("discord gateway") == ["gateway.discord.gg"]                # both words, in different fields
    assert found("chrome 172") == ["www.youtube.com"]                        # an app and an address
    assert found("steam youtube") == []                                      # every word must be found
    assert found("   ") == sorted(["store.steampowered.com", "www.youtube.com", "gateway.discord.gg"])


def test_search_matches_a_blocked_visit_by_host(env):
    env.db.add_block_event("reddit.com", 1, "Reddit", "permanent", None, NOW)
    env.db.add_network([row(0, domain="example.com")])
    assert [r["site"] for r in get(env, {"query": "redit"})["rows"]] == ["reddit.com"]


# ---------------------------------------------------------------- the graph

def test_the_graph_has_60_minutes_and_the_tops(env):
    env.db.add_network([row(0, count=5, domain="a.com"), row(0, exe="discord.exe", ip="2.2.2.2", count=2, domain="b.com"),
                        row(10, count=1, domain="a.com"), row(59, count=4, domain="c.com")])
    env.db.add_block_event("reddit.com", 1, "Reddit", "permanent", None, NOW - timedelta(minutes=10, seconds=-5))
    g = env.api.network_graph({})["data"]
    bars = g["bars"]
    assert len(bars) == 60 and bars[-1]["today"] and bars[-1]["value"] == 7 and bars[0]["value"] == 4
    assert bars[-1]["label"] == "" and bars[-3]["label"] == "15:40" and bars[-3]["show"] is True
    assert [b["label"] for b in bars if b["label"]] == ["14:50", "15:00", "15:10", "15:20", "15:30", "15:40"]
    assert bars[-11]["tone"] == "block" and bars[-11]["tip"] == "15:32\n2 connections\nBlocked visit"
    assert [b["tone"] for b in bars].count("block") == 1
    assert [(a["name"], a["n"]) for a in g["apps"]] == [("Chrome", 10), ("Discord", 2)]
    assert [(s["site"], s["n"]) for s in g["sites"]] == [("a.com", 6), ("c.com", 4), ("b.com", 2), ("reddit.com", 1)]
    assert env.api.network_graph({"app": "discord.exe"})["data"]["bars"][-1]["value"] == 2


def test_the_tops_are_cut_at_8(env):
    env.db.add_network([row(0, exe=f"app{i}.exe", ip=f"7.7.7.{i}", domain=f"site{i}.com", count=i + 1) for i in range(12)])
    g = env.api.network_graph({})["data"]
    assert len(g["apps"]) == 8 and len(g["sites"]) == 8 and g["apps"][0]["name"] == "App11"


# ---------------------------------------------------------------- the cache

def test_a_refresh_leaves_the_old_minutes_alone(env):
    env.db.add_network([row(0, domain="a.com"), row(30, ip="2.2.2.2", domain="old.com")])
    get(env)
    log = network._log(env)
    old_minute, old_row = log.net[minute(30)], log.net[minute(30)][0]
    env.db.add_network([row(0, domain="b.com", ip="3.3.3.3")])
    env.db.conn.execute("UPDATE network_log SET domain = 'tampered.com' WHERE minute = ?", (minute(30),))   # (not re-read: only the last minutes are)
    env.db.conn.commit()
    fresh(env)
    d = get(env)
    assert sorted(r["site"] for r in d["rows"][:2]) == ["a.com", "b.com"]
    assert log.net[minute(30)] is old_minute and log.rows[-1] is old_row and d["rows"][-1]["site"] == "old.com"
    get(env)
    network._log(env).refresh(env, force=True)                              # a forced read starts from the whole hour
    assert get(env)["rows"][-1]["site"] == "tampered.com"


def test_the_cache_is_reused_for_three_seconds(env):
    env.db.add_network([row(0)])
    assert get(env)["total"] == 1
    env.db.add_network([row(0, ip="2.2.2.2")])
    assert get(env)["total"] == 1                                           # (still the copy read a moment ago)
    fresh(env)
    assert get(env)["total"] == 2


def test_rows_age_out_and_a_changed_count_is_picked_up(env):
    env.db.add_network([row(0, count=1)])
    d = get(env)
    version = d["seq"]
    env.db.add_network([row(0, count=4)])                                   # the same key: the counts add up
    fresh(env)
    d = get(env)
    assert d["rows"][0]["n"] == 5 and d["seq"] == version + 1
    fresh(env)
    assert get(env)["seq"] == d["seq"]                                      # nothing changed: the same version
    env._clock = lambda db: NOW + timedelta(minutes=70)
    fresh(env)
    d = get(env)
    assert d["rows"] == [] and d["empty"] is True and d["seq"] > version


def test_a_clock_that_jumps_back_reads_the_hour_again(env):
    env.db.add_network([row(0, domain="a.com"), row(40, ip="2.2.2.2", domain="b.com")])
    get(env)
    env._clock = lambda db: NOW - timedelta(minutes=30)                      # (the trusted clock was corrected)
    fresh(env)
    assert [r["site"] for r in get(env)["rows"]] == ["a.com", "b.com"]       # nothing raised; the window is the hour before that time


# ---------------------------------------------------------------- the live feed

def test_the_feed_pushes_only_what_changed(env):
    env.db.add_network([row(0, domain="a.com"), row(1, ip="2.2.2.2", domain="b.com")])
    d = get(env, live=True)
    feed = network._feed(env)
    assert feed.on and d["epoch"] == feed.epoch
    assert feed.tick() is False and env.sent == []                          # nothing new: nothing sent
    env.db.add_network([row(0, ip="9.9.9.9", domain="live.com", count=3), row(0, domain="a.com", count=2)])
    fresh(env)
    assert feed.tick() is True
    event, payload = env.sent[-1]
    assert event == "net_rows" and payload["epoch"] == d["epoch"] and payload["seq"] == network._log(env).version
    assert sorted((r["site"], r["n"]) for r in payload["rows"]) == [("a.com", 3), ("live.com", 3)]       # a.com: changed in place
    assert payload["total"] == 3 and payload["summary"]["connections"] == 7 and payload["cutoff"] == minute(60)
    fresh(env)
    assert feed.tick() is False                                              # nothing since


def test_the_feed_only_sends_rows_that_pass_the_filters(env):
    env.db.add_network([row(0, domain="a.com")])
    get(env, {"query": "discord"}, live=True)
    env.db.add_network([row(0, ip="2.2.2.2", domain="x.com"), row(0, exe="discord.exe", ip="3.3.3.3", domain="d.com")])
    fresh(env)
    network._feed(env).tick()
    assert [r["site"] for r in env.sent[-1][1]["rows"]] == ["d.com"] and env.sent[-1][1]["total"] == 1


def test_the_feed_sends_blocked_visits_and_tells_hour_end(env):
    env.db.add_network([row(0, domain="a.com")])
    get(env, live=True)
    env.db.add_block_event("reddit.com", 1, "Reddit", "permanent", None, NOW)
    fresh(env)
    network._feed(env).tick()
    r = env.sent[-1][1]["rows"][0]
    assert (r["app"], r["b"], r["host"]) == ("Blocked visit", 1, "reddit.com") and env.sent[-1][1]["summary"]["blocked"] == 1


def test_a_hidden_window_means_no_reading_and_no_pushing(env):
    env.db.add_network([row(0)])
    get(env, live=True)
    env.window_visible = lambda: False
    env.db.add_network([row(0, ip="2.2.2.2")])
    fresh(env)
    network._log(env).fetched = 0.0
    version = network._log(env).version
    assert network._feed(env).tick() is False
    assert env.sent == [] and network._log(env).version == version        # (it did not even look)
    assert not network._feed(env).on                                       # and it switched itself off: the page asks again when shown


def test_stopping_and_asking_again(env):
    env.db.add_network([row(0)])
    e1 = get(env, live=True)["epoch"]
    assert env.api.network_live_stop()["ok"] and not network._feed(env).on
    e2 = get(env, live=True)["epoch"]
    assert e2 > e1 and network._feed(env).on
    assert get(env, live=False)["epoch"] == 0 and not network._feed(env).on   # Live switched off


def test_a_feed_that_fell_behind_asks_the_page_to_reload(env):
    env.db.add_network([row(0)])
    get(env, live=True)
    log = network._log(env)
    for i in range(network.MAX_CHANGES + 100):                               # more changes than the feed keeps
        log.version += 1
        log.changes.append((log.version, (f"z{i}",), True))
    log.dropped_upto = log.version - 10
    log.version += 1
    assert network._feed(env).tick() is True
    assert env.sent[-1][1] == {"epoch": network._feed(env).epoch, "resync": True}


def test_starting_a_feed_wakes_the_worker(env):
    class Hub:
        netlog_feed = None
        netlog_worker = None

    class Worker:
        poked = 0

        def poke(self):
            self.poked += 1
    env.hub, env.hub.netlog_worker = Hub(), Worker()
    get(env, live=True)
    assert env.hub.netlog_feed is network._feed(env) and env.hub.netlog_worker.poked == 1


def test_the_worker_sleeps_until_a_page_asks(env):
    hub = workers.Hub(db_path=None)
    worker = workers.NetLogWorker(hub)
    assert worker.tick() == workers.NETLOG_IDLE_SEC                          # no feed yet
    env.db.add_network([row(0)])
    env.hub = hub
    get(env, live=True)
    assert hub.netlog_feed.on and worker.tick() is None                      # the usual 3 s
    hub.netlog_feed.stop()
    assert worker.tick() == workers.NETLOG_IDLE_SEC
    assert workers.NETLOG_SEC == 3.0 and not worker.uses_db


# ---------------------------------------------------------------- the right-click menu and Export CSV

def test_block_site_target_names_the_registrable_domain(env):
    t = lambda host: env.api.network_block_target("site", host)["data"]["site"]
    assert t("rr3.googlevideo.com") == ["YouTube", "googlevideo.com"]
    assert t("www.reddit.com") == ["Reddit", "reddit.com"]
    assert t("cdn.example.co.uk") == ["Example", "example.co.uk"]
    assert t("a.b.example.org") == ["Example", "example.org"]


def test_block_app_target_has_the_name_and_path(env):
    env.db.add_item("Steam Client", ["steam.exe"], "app", "manual", [{"rule_type": "permanent"}])
    env.db.set_app_path(env.db.list_items()[0]["id"], r"C:\Steam\steam.exe")
    assert env.api.network_block_target("app", "STEAM.EXE")["data"]["app"] == {"name": "Steam Client", "exe": "steam.exe", "path": r"C:\Steam\steam.exe"}
    assert env.api.network_block_target("app", "tool.exe")["data"]["app"] == {"name": "Tool", "exe": "tool.exe", "path": None}


@pytest.mark.parametrize("args", [("game", "x"), ("site", ""), ("site", 5), ("app", "x" * 300)])
def test_block_target_refuses_bad_input(env, args):
    assert env.api.network_block_target(*args)["ok"] is False


def test_export_csv_writes_all_filtered_rows_on_a_worker(env, tmp_path, monkeypatch):
    env.db.add_network([row(0, count=3, domain="a.com"), row(1, exe="discord.exe", ip="2.2.2.2", domain="=cmd|'/c calc'!A1", port=80),
                        row(2, exe="svchost.exe", ip="3.3.3.3", domain="w.com", windows=1)])
    env.db.add_block_event("reddit.com", 1, "Reddit", "permanent", None, NOW)
    target = tmp_path / "out"
    monkeypatch.setattr(network, "_ask_save_path", lambda ctx: str(target) + ".csv")
    spawned = []
    monkeypatch.setattr(network, "_spawn", spawned.append)                   # held back: the reply comes before the file
    reply = env.api.network_export_csv({})
    assert reply["data"] == {"saved": True, "path": str(target) + ".csv", "rows": 3}
    assert not target.with_suffix(".csv").exists() and len(spawned) == 1
    spawned[0]()
    rows = list(csv.reader(open(str(target) + ".csv", encoding="utf-8", newline="")))
    assert rows[0] == ["time", "app", "exe", "site", "ip", "port", "connections", "status"]
    body = {r[3]: r for r in rows[1:]}
    assert body["a.com"] == [minute(0), "Chrome", "chrome.exe", "a.com", "1.1.1.1", "443", "3", "allowed"]
    assert body["reddit.com"][1:] == ["Browser", "", "reddit.com", "", "", "1", "blocked"]          # (the old window's word for it)
    assert body["'=cmd|'/c calc'!A1"][5] == "80"                              # (a spreadsheet won't run it)
    assert "w.com" not in body                                               # Windows' own: off, as on the page
    assert env.sent[-1] == ("net_export", {"ok": True, "path": str(target) + ".csv", "rows": 3})


def test_csv_cells_never_start_a_formula(env, tmp_path, monkeypatch):
    env.db.add_item("=SUM(A1)", ["evil.exe"], "app", "manual", [{"rule_type": "permanent"}])
    env.db.add_network([row(0, exe="evil.exe", ip="1.1.1.1", domain="+a.com"), row(1, exe="b.exe", ip="2.2.2.2", domain="\tb.com"),
                        row(2, exe="c.exe", ip="3.3.3.3", domain="\rc.com"), row(3, exe="d.exe", ip="4.4.4.4", domain="@d.com"),
                        row(4, exe="e.exe", ip="5.5.5.5", domain="-e.com"), row(5, exe="f.exe", ip="6.6.6.6", domain="fine.com")])
    monkeypatch.setattr(network, "_ask_save_path", lambda ctx: str(tmp_path / "x.csv"))
    monkeypatch.setattr(network, "_spawn", lambda fn: fn())
    assert env.api.network_export_csv({})["data"]["saved"] is True
    rows = list(csv.reader(open(tmp_path / "x.csv", encoding="utf-8", newline="")))[1:]
    assert sorted(r[3] for r in rows) == sorted(["'+a.com", "'-e.com", "'@d.com", "'\rc.com", "'\tb.com", "fine.com"])
    assert [r[1] for r in rows if r[2] == "evil.exe"] == ["'=SUM(A1)"]                  # the app's name too


def test_export_csv_cancelled_and_failing(env, tmp_path, monkeypatch):
    env.db.add_network([row(0)])
    monkeypatch.setattr(network, "_ask_save_path", lambda ctx: None)
    assert env.api.network_export_csv({})["data"] == {"saved": False}
    monkeypatch.setattr(network, "_ask_save_path", lambda ctx: str(tmp_path / "no-such-folder" / "x.csv"))
    monkeypatch.setattr(network, "_spawn", lambda fn: fn())
    assert env.api.network_export_csv({})["data"]["saved"] is True
    event, payload = env.sent[-1]
    assert event == "net_export" and payload["ok"] is False and payload["error"].startswith("Couldn't save the file")


def test_export_needs_the_main_window_for_the_dialog(env):
    reply = env.api.network_export_csv({})
    assert reply["ok"] is False and "Save dialog" in reply["error"]


# ---------------------------------------------------------------- 20 000 rows

def timed(fn, runs=20):
    out = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    out.sort()
    return out[max(0, -(-95 * len(out) // 100) - 1)], statistics.median(out)


FILTER_SETS = [{}, {"status": "blocked"}, {"status": "allowed", "app": "chrome.exe"}, {"query": "youtub"}, {"query": "chrome git"},
               {"query": "10.0.5"}, {"windows": True, "local": True, "query": "redd"}, {"app": "code.exe"}]


def test_20000_rows_filters_and_search(big):
    d = get(big)
    assert d["total"] >= 20_000
    youtube = get(big, {"query": "youtube"})
    assert youtube["total"] > 1000 and all("youtube" in r["site"] for r in youtube["rows"])
    assert 0 < get(big, {"query": "yotube"})["total"] <= youtube["total"] * 2                  # a typo finds them too
    chrome = get(big, {"app": "chrome.exe"})
    assert chrome["total"] > 1000 and {r["exe"] for r in chrome["rows"]} == {"chrome.exe"}
    both = get(big, {"app": "chrome.exe", "query": "github"})
    assert 0 < both["total"] < chrome["total"] and all(r["site"].endswith("github.com") for r in both["rows"])
    assert get(big, {"query": "10.0.5"})["total"] > 0                                         # by address
    assert get(big, {"query": "zzzzqqqq"})["total"] == 0
    assert [a["name"] for a in d["apps"]] == sorted((a["name"] for a in d["apps"]), key=str.lower)
    assert d["summary"]["connections"] > 20_000 and d["summary"]["apps"] == 10


def test_network_get_p95_on_20000_rows(big, capsys):
    """Every call with filters nobody asked for before (the cache is used for what is not new, the answer is not)."""
    log = network._log(big)
    cold = []
    for filters in FILTER_SETS:
        log.views.clear()
        log.word_sets.clear()                      # (a word nobody typed before is scored against every kind of row)
        t = time.perf_counter()
        get(big, filters)
        cold.append((time.perf_counter() - t) * 1000)
    warm_p95, warm_p50 = timed(lambda: get(big, {"query": "youtub"}))
    cold.sort()
    p95 = cold[-2]
    with capsys.disabled():
        print(f"\nnetwork_get on 20 000 rows: new filters p50 {statistics.median(cold):.1f} ms, p95 {p95:.1f} ms, max {cold[-1]:.1f} ms; "
              f"same filters p50 {warm_p50:.2f} ms, p95 {warm_p95:.2f} ms")
    assert p95 <= 50 and warm_p95 <= 50


def test_the_first_read_of_20000_rows_and_the_graph(tmp_path_factory, capsys):
    path = tmp_path_factory.mktemp("cold") / "c.db"
    Database(path)
    build(path, NOW.date(), days=3, network_rows=20_000)
    ctx = webapi.make_context(path, clock=lambda db: NOW)
    ctx.api = core.build_api(ctx)
    ctx.db
    t = time.perf_counter()
    get(ctx)
    first = (time.perf_counter() - t) * 1000
    graph_p95, graph_p50 = timed(lambda: ctx.api.network_graph({"query": "youtub"}), 10)
    with capsys.disabled():
        print(f"\nnetwork_get first read of 20 000 rows (database + cache): {first:.0f} ms; network_graph p50 {graph_p50:.1f} ms, p95 {graph_p95:.1f} ms")
    assert first <= 3000 and graph_p95 <= 300          # (the real numbers are printed; this box runs many jobs at once)


def reference_hits(log, query):
    """The old way, row by row: every word must pass search.score on 'site exe app name', or be inside the address."""
    words = search._WORD.findall(search.normalize(query))
    hits = set()
    for r in log.rows:
        text = f"{r[network.DOM]} {r[network.EXE]} {log.app_name(r[network.EXE])}"
        flags = [search.score(w, text) is not None for w in words]
        ip = r[network.IP]
        if all(flags) or (ip and all(ok or w in ip for ok, w in zip(flags, words))):
            hits.add(r[network.K])
    return hits


@pytest.mark.parametrize("query", ["youtube", "yotube", "chrome git", "10.0.5", "10 0 5", "redd", "cod", "zzzzqqqq", "browser", "face", "git 10"])
def test_the_group_search_finds_what_a_row_by_row_search_finds(big, query):
    log = network._log(big)
    f = network.Filters(windows=True, local=True, query=query)
    got = {r[network.K] for r in log.view(f).rows}
    assert got == reference_hits(log, query)


def test_a_first_read_keeps_no_change_log_and_a_long_pause_starts_over(env):
    env.db.add_network([row(0, domain="a.com")])
    get(env, live=True)
    log, feed = network._log(env), network._feed(env)
    assert log.changes == [] and log.dropped_upto == log.version          # nobody can have asked for older changes
    env.db.add_network([row(0, ip="2.2.2.2", domain="b.com")])
    fresh(env)
    assert feed.tick() is True and len(log.changes) == 1                  # a later read does log what changed
    env.sent.clear()
    env._clock = lambda db: NOW + network.RESTART_AFTER + timedelta(minutes=1)
    fresh(env)
    assert feed.tick() is True
    assert env.sent[-1][1] == {"epoch": feed.epoch, "resync": True} and log.changes == []       # (the page reloads)


class FakeCatalog:
    def __init__(self, loaded=True):
        self.loaded = threading.Event()
        if loaded:
            self.loaded.set()
        self.asked = 0

    def search(self, kind="all", query="", limit=0):
        self.asked += 1
        return {"apps": [{"exe": "chrome.exe", "name": "Google Chrome"}, {"exe": "tool.exe", "name": "Tool Box"}]}


def test_the_catalogs_names_are_taken_by_the_worker_not_by_a_page_call(env):
    env.catalog = FakeCatalog()
    env.db.add_network([row(0, domain="a.com"), row(1, exe="tool.exe", ip="2.2.2.2", domain="b.com")])
    d = get(env)
    assert env.catalog.asked == 0 and d["rows"][0]["app"] == "Chrome"      # (the page's call never asks the catalog)
    assert network.maintain(env) is True and env.catalog.asked == 1
    fresh(env)
    d = get(env)
    assert [r["app"] for r in d["rows"]] == ["Google Chrome", "Tool Box"] and d["summary"]["top"] == "Google Chrome"
    assert [r["site"] for r in get(env, {"query": "google"})["rows"]] == ["a.com"]       # and the search knows the new name
    assert network.maintain(env) is True and env.catalog.asked == 1        # (once a minute at most)


def test_maintain_waits_for_the_catalog_and_leaves_a_hidden_window_alone(env):
    env.catalog = FakeCatalog(loaded=False)
    env.db.add_network([row(0)])
    env.window_visible = lambda: False
    log = network._log(env)
    assert network.maintain(env) is False and env.catalog.asked == 0       # (look again soon)
    env.catalog.loaded.set()
    assert network.maintain(env) is True and env.catalog.asked == 1        # (the names: once, even hidden)
    assert not log.loaded                                                  # ... but the database is not read while hidden
    env.window_visible = lambda: True
    assert network.maintain(env) is True and log.loaded


def test_prewarm_reads_the_hour_and_the_first_click_reads_nothing(env, monkeypatch):
    env.db.add_network([row(i % 30, ip=f"3.3.3.{i}", domain=f"s{i}.com") for i in range(50)])
    network.prewarm(env)
    log = network._log(env)
    assert log.loaded and network.Filters() in log.views and False in log.apps_cache
    reads = []
    monkeypatch.setattr(network._Log, "_read", lambda self, ctx, force: reads.append(1))
    assert get(env)["total"] == 50 and reads == []


def test_the_worker_reads_ahead_and_keeps_the_names(env, monkeypatch):
    monkeypatch.setattr(workers, "NETLOG_PREWARM_SEC", 0.0)
    env.catalog = FakeCatalog(loaded=False)
    env.db.add_network([row(0)])
    hub = workers.Hub(db_path=None)
    worker = workers.NetLogWorker(hub)
    worker.setup()                                                          # (no context: nothing to read)
    assert not hasattr(env, "_netlog")
    hub.netlog_ctx = env
    worker.setup()
    assert network._log(env).loaded
    assert worker.tick() == workers.NETLOG_WAIT_SEC                         # the catalog has not loaded: look again soon
    env.catalog.loaded.set()
    assert worker.tick() == workers.NETLOG_IDLE_SEC and env.catalog.asked == 1


def test_a_live_tick_on_20000_rows_is_cheap(big, capsys):
    get(big, {"query": "youtub"}, live=True)
    feed = network._feed(big)
    big.publish = lambda *a, **k: None
    big.db.add_network([row(0, ip="5.5.5.5", domain="youtube.com", count=2)])
    ms = []
    for i in range(5):
        big.db.add_network([row(0, ip=f"5.5.6.{i}", domain="youtube.com", count=2)])
        fresh(big)
        t = time.perf_counter()
        feed.tick()
        ms.append((time.perf_counter() - t) * 1000)
    with capsys.disabled():
        print(f"\nlive tick with a search active on 20 000 rows: {statistics.median(ms):.1f} ms median, {max(ms):.1f} ms max")
    assert max(ms) <= 400


def test_site_to_block():
    assert network.site_to_block("rr3.googlevideo.com") == "googlevideo.com"
    assert network.site_to_block("www.example.com") == "example.com"
    assert network.site_to_block("a.b.c.example.com.au") == "example.com.au"
    assert network.site_to_block("cdn.example.org") == "example.org"
