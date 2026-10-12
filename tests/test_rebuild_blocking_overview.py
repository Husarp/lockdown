"""R18: Blocking > Overview (webapi/blocking.py) - the statuses, chips, boxes and order of the list, and the emergency
unlock. The wording is the old window's (see test_blocked_once.py for why "Blocked (until ...)" is said once)."""
import statistics
import time
from datetime import datetime, timedelta

import pytest

import emergency
import group_look
import pause
import webapi
from rules import (allowance_bucket, allowance_owner, effective_rules, make_schedule, schedule_until, time_bucket)
from webapi import core, draft as drafts

NOW = datetime(2026, 10, 11, 15, 42, 17)      # a Sunday afternoon
EVERY_DAY = list(range(7))
HOURS_NOW = make_schedule("block", [(EVERY_DAY, "15:00", "18:00")])      # blocking right now, until 18:00
HOURS_LATER = make_schedule("block", [(EVERY_DAY, "20:00", "22:00")])    # starts in 4 hours
HOURS_SOON = make_schedule("block", [(EVERY_DAY, "15:50", "17:00")])     # starts in 8 minutes


@pytest.fixture
def env(tmp_path):
    ctx = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    ctx.api = core.build_api(ctx)
    ctx.publish = lambda event, payload=None, key=None: None
    ctx.db.set_setting("ui.autosave", "0")
    ctx.db.set_setting("service_heartbeat", str(time.time()))               # the service is running
    return ctx


def add(ctx, name, rules, host=None, kind="site"):
    host = host or f"{name.lower().replace(' ', '')}.com"
    return ctx.db.add_item(name, [host], kind, "manual", rules)


def overview(ctx, sort=None):
    reply = ctx.api.blocking_overview_get(*([sort] if sort else []))
    assert reply["ok"], reply
    return reply["data"]


def entry(view, name):
    return next(e for e in view["entries"] if e["name"] == name)


def status(ctx, name):
    return entry(overview(ctx), name)["status"]


def used(ctx, owner, seconds, period="day"):
    ctx.db.add_usage([(owner, time_bucket(period, NOW, ctx.db.limit_clock()))], seconds, NOW.date())


# ---------------------------------------------------------------- statuses, in today's order

def test_allowed_now(env):
    add(env, "Reddit", [{"rule_type": "scheduled", "schedule": HOURS_LATER}])
    assert status(env, "Reddit") == {"k": "allowed", "text": "Allowed now"}


def test_blocked_says_until_when_once(env):
    add(env, "Reddit", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    got = status(env, "Reddit")
    assert got["k"] == "blocked" and got["text"] == "Blocked (until 18:00)" and got["until"] == "2026-10-11T18:00:00"


def test_a_permanent_block_just_says_blocked(env):
    add(env, "Reddit", [{"rule_type": "permanent"}])
    assert status(env, "Reddit")["text"] == "Blocked"


def test_blocked_by_a_group_limit_and_its_own_limit_is_one_line_without_the_time_left(env):
    twitch = add(env, "Twitch", [{"rule_type": "time_limit", "daily_limit_min": 60}])
    gid = env.db.add_group("Fun", [{"rule_type": "time_limit", "daily_limit_min": 120}], {twitch: {}})
    used(env, f"group:{gid}", 120 * 60)
    used(env, f"item:{twitch}", 60 * 60)
    view = overview(env)
    group = entry(view, "Fun")
    assert group["status"]["text"] == "1 of 1 blocked now"
    member = group["members"][0]
    assert member["status"]["text"] == "Blocked (until Mon 00:00)"
    texts = [c["t"] for c in group["chips"]] + [c["t"] for c in member["chips"]]
    assert all("/" not in t for t in texts), texts           # no "20m left" on something already blocked
    assert {c["k"] for c in group["chips"] + member["chips"]} == {"danger"}


def test_on_the_allowance_says_how_much_is_left(env):
    iid = add(env, "Reddit", [{"rule_type": "scheduled", "schedule": HOURS_NOW, "allowance_min": 10}])
    got = status(env, "Reddit")
    assert got == {"k": "allowance", "text": "On the allowance · 10m left"}
    rule = effective_rules(env.db.list_items()[0], [])[0]
    env.db.add_usage([(allowance_owner(rule), allowance_bucket(rule, schedule_until(HOURS_NOW, NOW)))], 4 * 60, NOW.date())
    assert status(env, "Reddit")["text"] == "On the allowance · 6m left"
    chips = entry(overview(env), "Reddit")["chips"]
    assert [c["k"] for c in chips] == ["warn", "allowance"] and "allowed during blocked hours" in chips[1]["t"]
    env.db.add_usage([(allowance_owner(rule), allowance_bucket(rule, schedule_until(HOURS_NOW, NOW)))], 6 * 60, NOW.date())
    assert status(env, "Reddit")["k"] == "blocked"           # (used up: the hours block again)
    assert iid


def test_pending_when_the_service_is_off(env):
    add(env, "Reddit", [{"rule_type": "permanent"}])
    env.db.set_setting("service_heartbeat", str(time.time() - 3600))
    assert status(env, "Reddit") == {"k": "pending", "text": "Pending · service off"}
    assert overview(env)["counts"]["blocked"] == 1


def test_unlocked_says_how_long_is_left(env):
    iid = add(env, "Reddit", [{"rule_type": "permanent"}])
    add(env, "Twitch", [{"rule_type": "time_limit", "daily_limit_min": 1}])
    used(env, f"item:{iid}", 0)
    twitch = env.db.list_items()[1]
    used(env, f"item:{twitch['id']}", 3600)
    emergency.unlock(env.db, [twitch], NOW, tier=emergency.LEGACY_TIER)
    got = status(env, "Twitch")
    assert got["k"] == "unlocked" and got["text"] == "Unlocked · 20m left" and got["until"] == "2026-10-11T16:02:17"


def test_paused_says_so(env):
    add(env, "Reddit", [{"rule_type": "permanent"}])
    pause.start(env.db, NOW, 30, False)
    got = status(env, "Reddit")
    assert got["k"] == "paused" and got["text"] == "Paused · 30m left"


def test_not_applied_while_the_draft_differs_from_what_is_saved(env):
    iid = add(env, "Reddit", [{"rule_type": "permanent"}])
    add(env, "Twitch", [{"rule_type": "permanent"}])
    assert overview(env)["autosave"] is False
    drafts.run_edit(env, lambda d: d.set_rules(iid, [{"rule_type": "time_limit", "daily_limit_min": 30}]))
    view = overview(env)
    assert entry(view, "Reddit")["status"] == {"k": "unsaved", "text": "Not applied (unsaved)"}
    assert entry(view, "Twitch")["status"]["k"] == "blocked"
    # its chip shows the new rule (the draft), its status keeps saying what is enforced until it is saved
    assert entry(view, "Reddit")["chips"][0]["t"].startswith("Limit")


def test_an_item_in_a_changed_group_is_not_applied_either(env):
    iid = add(env, "Reddit", [])
    gid = env.db.add_group("Fun", [{"rule_type": "permanent"}], {iid: {}})
    drafts.run_edit(env, lambda d: d.set_group(gid, "Fun", [{"rule_type": "time_limit", "daily_limit_min": 5}],
                                              {iid: {}}))
    view = overview(env)
    assert entry(view, "Fun")["status"]["k"] == "unsaved"
    assert entry(view, "Fun")["members"][0]["status"]["k"] == "unsaved"


def test_a_new_unsaved_item_is_listed_as_not_applied(env):
    drafts.run_edit(env, lambda d: d.set_rules(d.add_item("Steam", ["steam.exe"], "manual", "app")["id"],
                                              [{"rule_type": "permanent"}]))
    got = entry(overview(env), "Steam")
    assert got["status"]["k"] == "unsaved" and got["type"] == "app" and got["id"] < 0


def test_a_running_low_rule_is_orange_and_marks_the_row_low(env):
    iid = add(env, "Twitch", [{"rule_type": "time_limit", "daily_limit_min": 60}])
    used(env, f"item:{iid}", 55 * 60)
    got = entry(overview(env), "Twitch")
    assert got["chips"][0]["k"] == "warn" and got["low"] is True
    assert got["chips"][0]["t"] == "Limit: 55m / 1h 00m today"


def test_hours_that_start_in_a_few_minutes_are_orange(env):
    add(env, "Reddit", [{"rule_type": "scheduled", "schedule": HOURS_SOON}])
    assert entry(overview(env), "Reddit")["chips"][0]["k"] == "warn"


def test_while_one_rule_blocks_the_others_go_red_too(env):
    iid = add(env, "Reddit", [{"rule_type": "scheduled", "schedule": HOURS_NOW},
                              {"rule_type": "time_limit", "daily_limit_min": 60}])
    used(env, f"item:{iid}", 10 * 60)
    chips = entry(overview(env), "Reddit")["chips"]
    assert [c["k"] for c in chips] == ["danger", "danger"]
    assert "/" not in chips[1]["t"]


# ---------------------------------------------------------------- boxes

def test_a_group_is_one_box_with_its_members(env):
    a = add(env, "Reddit", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    b = add(env, "Twitch", [])
    loose = add(env, "Steam", [{"rule_type": "permanent"}])
    gid = env.db.add_group("Fun", [{"rule_type": "time_limit", "daily_limit_min": 60}], {a: {}, b: {}})
    view = overview(env)
    names = [e["name"] for e in view["entries"]]
    assert sorted(names) == ["Fun", "Steam"]                     # the members are in the box, not in the list
    box = entry(view, "Fun")
    assert box["t"] == "group" and box["id"] == gid and box["count"] == 2
    assert [m["name"] for m in box["members"]] == ["Reddit", "Twitch"]
    assert box["status"] == {"k": "blocked", "text": "1 of 2 blocked now"}
    assert view["counts"] == {"total": 3, "blocked": 2, "groups": 1, "disabled": 0}
    assert loose and entry(view, "Steam")["groups"] == []


def test_group_statuses(env):
    a = add(env, "Reddit", [{"rule_type": "scheduled", "schedule": HOURS_LATER}])
    empty = env.db.add_group("Nobody", [{"rule_type": "permanent"}], {})
    fine = env.db.add_group("Fine", [{"rule_type": "time_limit", "daily_limit_min": 60}], {a: {}})
    view = overview(env)
    assert entry(view, "Nobody")["status"] == {"k": "empty", "text": "No members yet"}
    assert entry(view, "Fine")["status"] == {"k": "allowed", "text": "Allowed now"}
    env.db.add_unlock([], [], NOW, NOW, tier=None)               # (an unrelated row changes nothing)
    env.db.set_setting("service_heartbeat", "0")
    used(env, f"group:{fine}", 3600)
    assert entry(overview(env), "Fine")["status"] == {"k": "pending", "text": "Pending · service off"}
    assert empty


def test_a_member_with_its_own_rule_in_the_group_says_so(env):
    a = add(env, "Reddit", [])
    env.db.add_group("Fun", [{"rule_type": "time_limit", "daily_limit_min": 120}], {a: {
        "time_limit": {"rule_type": "time_limit", "daily_limit_min": 30}}})
    member = entry(overview(env), "Fun")["members"][0]
    assert member["note"] == "its own limit in this group"
    assert any("Limit (own)" in c["t"] for c in member["chips"])


def test_a_loose_item_in_a_group_lists_the_groups_rules_with_an_arrow(env):
    a = add(env, "Reddit", [{"rule_type": "permanent"}])
    env.db.add_group("Fun", [{"rule_type": "time_limit", "daily_limit_min": 120}], {a: {}})
    view = overview(env)
    assert [e["name"] for e in view["entries"]] == ["Fun"]       # (in a box)
    # in the Disabled state the group's chip on the item is "→ Fun · disabled"
    drafts.run_edit(env, lambda d: d.set_group_disabled(next(iter(d.groups)), True))
    view = overview(env)
    reddit = entry(view, "Reddit")
    assert [c["t"] for c in reddit["chips"]] == ["Permanent", "→ Fun · disabled"]
    assert reddit["groups"] == [{"id": env.db.list_groups()[0]["id"], "name": "Fun"}]
    assert view["disabled"] == [{"t": "group", "id": reddit["groups"][0]["id"], "name": "Fun", "icon": "folder",
                                 "color": group_look.auto_hue(reddit["groups"][0]["id"])}]


def test_a_group_box_and_a_disabled_group_carry_the_groups_icon_and_colour(env):
    a = add(env, "Reddit", [])
    gid = env.db.add_group("Good Night", [{"rule_type": "time_limit", "daily_limit_min": 120}], {a: {}})
    box = entry(overview(env), "Good Night")
    assert (box["icon"], box["color"]) == ("moon", group_look.auto_hue(gid))              # automatic: the name, the id
    env.db.update_group(gid, "Good Night", env.db.list_groups()[0]["rules"], {a: {}}, False, None, {"icon": "car", "color": "blue"})
    drafts.draft_of(env).reload()
    box = entry(overview(env), "Good Night")
    assert (box["icon"], box["color"]) == ("car", "blue")
    env.db.update_group(gid, "Good Night", env.db.list_groups()[0]["rules"], {a: {}}, True)      # disabled: its row keeps the look
    drafts.draft_of(env).reload()
    assert [(g["icon"], g["color"]) for g in overview(env)["disabled"] if g["t"] == "group"] == [("car", "blue")]


def test_a_disabled_member_of_a_group_says_disabled_and_lists_no_blockers(env):
    a = add(env, "Reddit", [{"rule_type": "permanent"}])
    b = add(env, "Twitch", [])
    env.db.add_group("Fun", [{"rule_type": "time_limit", "daily_limit_min": 60}], {a: {}, b: {}})
    env.db.write("UPDATE blocked_items SET disabled = 1 WHERE id = ?", (a,))
    box = entry(overview(env), "Fun")
    reddit = next(m for m in box["members"] if m["name"] == "Reddit")
    assert reddit["status"] == {"k": "disabled", "text": "Disabled"} and reddit["chips"] == []
    assert box["status"]["text"] == "Allowed now"                    # (nothing of it is blocking: the disabled one doesn't count)


def test_disabled_items_and_groups_are_listed_apart(env):
    iid = add(env, "Reddit", [{"rule_type": "permanent"}])
    add(env, "Twitch", [{"rule_type": "permanent"}])
    drafts.run_edit(env, lambda d: d.set_disabled(iid, True))
    view = overview(env)
    assert [e["name"] for e in view["entries"]] == ["Twitch"]
    assert [d["name"] for d in view["disabled"]] == ["Reddit"] and view["disabled"][0]["t"] == "item"
    assert view["counts"]["disabled"] == 1 and view["counts"]["total"] == 1


def test_empty_list(env):
    view = overview(env)
    assert view["entries"] == [] and view["disabled"] == [] and view["counts"]["total"] == 0


def test_the_sub_line_says_what_it_covers_and_how(env):
    env.db.add_item("Discord", ["discord.exe"], "app", "manual", [{"rule_type": "permanent"}], None, "kill")
    add(env, "Reddit", [{"rule_type": "permanent"}], host="reddit.com")
    view = overview(env)
    assert entry(view, "Discord")["sub"].startswith("app · discord.exe · closes")
    assert entry(view, "Reddit")["sub"].startswith("reddit.com · ")
    assert entry(view, "Discord")["icon"] == {"k": "app", "n": "discord.exe"}
    assert entry(view, "Reddit")["icon"] == {"k": "site", "n": "reddit.com"}


def test_notify_is_listed_as_set(env):
    env.db.add_item("Reddit", ["reddit.com"], "site", "manual", [{"rule_type": "permanent"}], "off")
    assert entry(overview(env), "Reddit")["notify"] == "off"


# ---------------------------------------------------------------- order

def order(ctx, sort):
    return [e["name"] for e in overview(ctx, sort)["entries"]]


def test_sort_by_name(env):
    for n in ("Zed", "alpha", "Mid"):
        add(env, n, [{"rule_type": "permanent"}])
    assert order(env, "Name") == ["alpha", "Mid", "Zed"]


def test_sort_by_date_added_newest_first_and_unsaved_on_top(env):
    for n, stamp in (("Old", "2026-01-01 10:00:00"), ("New", "2026-09-01 10:00:00"), ("Mid", "2026-05-01 10:00:00")):
        iid = add(env, n, [{"rule_type": "permanent"}])
        env.db.write("UPDATE blocked_items SET created_at = ? WHERE id = ?", (stamp, iid))
    assert order(env, "Date added") == ["New", "Mid", "Old"]
    drafts.run_edit(env, lambda d: d.set_rules(d.add_item("Fresh", ["fresh.com"], "manual")["id"], [{"rule_type": "permanent"}]))
    assert order(env, "Date added")[0] == "Fresh"


def test_sort_blocked_now_first_then_by_name(env):
    add(env, "Aaa", [{"rule_type": "scheduled", "schedule": HOURS_LATER}])
    add(env, "Bbb", [{"rule_type": "permanent"}])
    add(env, "Ccc", [{"rule_type": "scheduled", "schedule": HOURS_LATER}])
    add(env, "Ddd", [{"rule_type": "permanent"}])
    assert order(env, "Blocked now first") == ["Bbb", "Ddd", "Aaa", "Ccc"]


def test_sort_next_block_soonest_first_blocked_ones_last(env):
    add(env, "Late", [{"rule_type": "scheduled", "schedule": HOURS_LATER}])
    add(env, "Soon", [{"rule_type": "scheduled", "schedule": HOURS_SOON}])
    add(env, "Never", [{"rule_type": "time_limit", "daily_limit_min": 60}])
    add(env, "Now", [{"rule_type": "permanent"}])
    assert order(env, "Next block") == ["Soon", "Late", "Never", "Now"]


def test_a_groups_time_is_the_soonest_of_its_members(env):
    soon = add(env, "Soon item", [{"rule_type": "scheduled", "schedule": HOURS_SOON}])
    late = add(env, "Late item", [{"rule_type": "scheduled", "schedule": HOURS_LATER}])
    add(env, "Between", [{"rule_type": "scheduled", "schedule": make_schedule("block", [(EVERY_DAY, "17:00", "18:00")])}])
    env.db.add_group("Zeta", [], {late: {}, soon: {}})                # soonest member: 15:50
    assert order(env, "Next block") == ["Zeta", "Between"]
    blocked = add(env, "Blocked", [{"rule_type": "permanent"}])
    env.db.add_group("Alpha", [], {blocked: {}, late: {}})            # one blocked member -> the group counts as blocked
    assert order(env, "Blocked now first")[0] == "Alpha"


def test_the_sort_is_remembered_and_only_known_ones_are_taken(env):
    add(env, "B", [{"rule_type": "permanent"}])
    add(env, "A", [{"rule_type": "permanent"}])
    assert overview(env)["sort"] == "Blocked now first"
    assert env.api.blocking_set_sort("Name")["ok"]
    assert env.db.get_setting("ui.blocking.sort") == "Name"
    view = overview(env)
    assert view["sort"] == "Name" and [e["name"] for e in view["entries"]] == ["A", "B"]
    assert overview(env, "Date added")["sort"] == "Date added"
    assert env.db.get_setting("ui.blocking.sort") == "Name"      # (asking for one is not choosing it)
    for bad in ("Random", None, 3, ["Name"]):
        assert env.api.blocking_set_sort(bad)["ok"] is False
    assert env.api.blocking_overview_get("Random")["ok"] is False
    env.db.set_setting("ui.blocking.sort", "garbage")
    assert overview(env)["sort"] == "Blocked now first"


def test_the_methods_are_classified():
    kinds = dict(webapi.api_methods())
    assert kinds["blocking_overview_get"] == kinds["emergency_get_candidates"] == "read"
    assert kinds["blocking_set_sort"] == kinds["draft_set_notify"] == "ui"
    for name in ("draft_remove_item", "draft_set_group_disabled", "draft_remove_group", "emergency_unlock"):
        assert kinds[name] == "loosen"


# ---------------------------------------------------------------- emergency unlock

def kinds_of(ctx):
    got = ctx.api.emergency_get_candidates()
    assert got["ok"], got
    return got["data"]


def unlock(ctx, ids, kind=emergency.LEGACY_TIER, *extra):
    return ctx.api.emergency_unlock(ids, kind, *extra)


def test_candidates_are_the_blocked_items_that_are_not_permanent(env):
    hours = add(env, "Hours", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    perm = add(env, "Perm", [{"rule_type": "permanent"}])
    later = add(env, "Later", [{"rule_type": "scheduled", "schedule": HOURS_LATER}])
    limit = add(env, "Limit", [{"rule_type": "time_limit", "daily_limit_min": 30}])
    used(env, f"item:{limit}", 31 * 60)
    got = kinds_of(env)
    assert got["usable"] is True
    assert sorted(i["name"] for i in got["blocked"]) == ["Hours", "Limit"]
    assert [i["name"] for i in got["open"]] == ["Later"]
    assert got["permanent"] is True
    assert got["blocked"][0]["icon"]["k"] == "site" and hours and perm and later


def test_candidates_list_the_kinds_with_uses_left_and_when_they_reset(env):
    emergency.save_tiers(env.db, [{"id": "a", "uses": 3, "per": "week", "minutes": 20},
                                  {"id": "b", "uses": 1, "per": "day", "minutes": 60}])
    add(env, "Hours", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    unlock(env, [env.db.list_items()[0]["id"]], "b")
    kinds = {k["id"]: k for k in kinds_of(env)["kinds"]}
    assert kinds["a"]["left"] == 3 and kinds["a"]["allowed"] == 3 and kinds["a"]["per_text"] == "this week"
    assert kinds["a"]["minutes"] == 20 and kinds["a"]["resets_text"] == "Mon 12 Oct 00:00"
    assert kinds["b"]["left"] == 0 and kinds["b"]["per_text"] == "today" and kinds["b"]["resets"] == "2026-10-12T00:00:00"
    assert kinds_of(env)["alerts_until"] is None


def test_nothing_to_offer_when_emergency_unlock_is_off(env):
    add(env, "Hours", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    env.db.set_setting("emergency.enabled", "0")
    got = kinds_of(env)
    assert got["usable"] is False and got["kinds"] == [] and got["blocked"] == []
    assert env.api.emergency_unlock([env.db.list_items()[0]["id"]], emergency.LEGACY_TIER)["ok"] is False


def test_unlock_frees_the_items_for_the_kinds_minutes_and_spends_one_use(env):
    emergency.save_tiers(env.db, [{"id": "a", "uses": 2, "per": "week", "minutes": 5}])
    one = add(env, "One", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    two = add(env, "Two", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    reply = unlock(env, [one, two], "a")
    assert reply["ok"], reply
    got = reply["data"]
    assert got["until"] == "2026-10-11T15:47:17" and got["left"] == 1          # 5 minutes: from the kind
    assert got["names"] == ["One", "Two"] and got["text"] == "One, Two unlocked until 15:47"
    assert emergency.left_by_tier(env.db, NOW)[0][1] == 1                        # one use for both
    assert len(env.db.unlocks_since(NOW - timedelta(days=1))) == 1
    assert status(env, "One")["k"] == "unlocked"


def test_unlock_ignores_any_minutes_sent_by_the_page(env):
    emergency.save_tiers(env.db, [{"id": "a", "uses": 2, "per": "week", "minutes": 5}])
    one = add(env, "One", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    assert env.api.emergency_unlock([one], "a", False, 600)["ok"] is False                   # (no such argument)
    assert env.api.emergency_unlock([one], "a", {"minutes": 600})["ok"] is False
    assert env.api.emergency_unlock([one], {"id": "a", "minutes": 600})["ok"] is False
    assert emergency.left_by_tier(env.db, NOW)[0][1] == 2
    assert unlock(env, [one], "a")["data"]["until"] == "2026-10-11T15:47:17"


def test_unlock_refuses_a_permanent_item(env):
    perm = add(env, "Perm", [{"rule_type": "permanent"}])
    reply = unlock(env, [perm])
    assert reply["ok"] is False and "Permanently-blocked" in reply["error"]
    assert emergency.left_by_tier(env.db, NOW)[0][1] == 3 and env.db.unlocks_since(NOW - timedelta(days=1)) == []


def test_unlock_refuses_a_permanent_item_hidden_among_good_ones(env):
    perm = add(env, "Perm", [{"rule_type": "permanent"}])
    good = add(env, "Good", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    assert unlock(env, [good, perm])["ok"] is False
    assert env.db.unlocks_since(NOW - timedelta(days=1)) == []


def test_unlock_refuses_an_item_in_a_permanently_blocked_group(env):
    iid = add(env, "Member", [])
    env.db.add_group("Never", [{"rule_type": "permanent"}], {iid: {}})
    reply = unlock(env, [iid])
    assert reply["ok"] is False and "Permanently-blocked" in reply["error"]


def test_unlock_refuses_unknown_ids_and_bad_input(env):
    good = add(env, "Good", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    for ids in ([999], [good, 999], [-1], "1", [True], [1.5], ["1"], None, {"0": good}, [[good]]):
        assert env.api.emergency_unlock(ids, emergency.LEGACY_TIER)["ok"] is False, ids
    for kind in (None, 5, ["t1"], True, {"id": "t1"}):
        assert env.api.emergency_unlock([good], kind)["ok"] is False, kind
    assert env.api.emergency_unlock([good], "no-such-kind")["error"] == "That kind is gone - pick another."
    assert env.api.emergency_unlock([good], emergency.LEGACY_TIER, "yes")["ok"] is False
    assert env.api.emergency_unlock([], emergency.LEGACY_TIER)["error"] == "Tick at least one."
    assert env.db.unlocks_since(NOW - timedelta(days=1)) == []


def test_unlock_refuses_a_kind_with_no_uses_left(env):
    emergency.save_tiers(env.db, [{"id": "a", "uses": 1, "per": "week", "minutes": 5}])
    one = add(env, "One", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    two = add(env, "Two", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    assert unlock(env, [one], "a")["ok"]
    reply = unlock(env, [two], "a")
    assert reply["ok"] is False and reply["error"] == "No emergency unlocks left."
    assert len(env.db.unlocks_since(NOW - timedelta(days=1))) == 1


def test_unlock_refuses_when_emergency_is_disabled(env):
    one = add(env, "One", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    env.db.set_setting("emergency.enabled", "0")
    reply = unlock(env, [one])
    assert reply["ok"] is False and "turned off" in reply["error"]
    assert env.db.unlocks_since(NOW - timedelta(days=1)) == []


def test_unlock_refuses_a_disabled_item(env):
    iid = add(env, "Off", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    env.db.write("UPDATE blocked_items SET disabled = 1 WHERE id = ?", (iid,))
    assert unlock(env, [iid])["error"] == "A disabled item has nothing to unlock."


def test_unlock_can_pause_the_alerts_with_or_without_items(env):
    one = add(env, "One", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    got = unlock(env, [], emergency.LEGACY_TIER, True)
    assert got["ok"] and got["data"]["text"] == "Bedtime and break alerts paused until 16:02"
    assert kinds_of(env)["alerts_until"] == "2026-10-11T16:02:17"
    both = unlock(env, [one], emergency.LEGACY_TIER, True)["data"]
    assert both["text"] == "One unlocked until 16:02. Bedtime and break alerts paused until 16:02"
    assert emergency.left_by_tier(env.db, NOW)[0][1] == 1


def test_unlock_tells_the_page_the_blocks_changed(env):
    pushed = []
    env.publish = lambda event, payload=None, key=None: pushed.append(event)
    one = add(env, "One", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    unlock(env, [one])
    assert "blocks_changed" in pushed


def test_a_duplicate_id_counts_once(env):
    one = add(env, "One", [{"rule_type": "scheduled", "schedule": HOURS_NOW}])
    got = unlock(env, [one, one])["data"]
    assert got["names"] == ["One"]


# ---------------------------------------------------------------- speed

def _big(ctx, items=200, groups=20):
    """200 items with every kind of rule and 20 groups of five of them."""
    rules = [lambda i: [{"rule_type": "permanent"}],
             lambda i: [{"rule_type": "scheduled", "schedule": HOURS_NOW if i % 2 else HOURS_LATER, "allowance_min": 10}],
             lambda i: [{"rule_type": "time_limit", "daily_limit_min": 30 + i, "weekly_limit_min": 600}],
             lambda i: [{"rule_type": "switch_limit", "daily_switch_limit": 5 + i % 7}],
             lambda i: [{"rule_type": "temporary", "temp_until": "2026-10-12 10:00:00"},
                        {"rule_type": "time_limit", "daily_limit_min": 45}]]
    ids = [add(ctx, f"Item {i:03d}", rules[i % len(rules)](i), host=f"item{i}.example") for i in range(items)]
    for g in range(groups):
        members = {iid: {} for iid in ids[g * 5:g * 5 + 5]}
        ctx.db.add_group(f"Group {g:02d}", [{"rule_type": "time_limit", "daily_limit_min": 90, "weekly_limit_min": 400}],
                         members)
    return ids


def test_the_overview_is_fast_with_200_items_and_20_groups(env):
    _big(env)
    first = time.perf_counter()
    reply = env.api.blocking_overview_get()
    first = (time.perf_counter() - first) * 1000
    assert reply["ok"] and len(reply["data"]["entries"]) == 20 + 100
    runs = []
    for sort in ("Blocked now first", "Next block", "Name", "Date added") * 5:
        t = time.perf_counter()
        env.api.blocking_overview_get(sort)
        runs.append((time.perf_counter() - t) * 1000)
    p95 = sorted(runs)[int(len(runs) * 0.95) - 1]
    print(f"blocking_overview_get 200 items + 20 groups: first {first:.1f} ms, median {statistics.median(runs):.1f} ms, p95 {p95:.1f} ms")
    assert p95 <= 100, f"p95 {p95:.1f} ms (target 50)"


def test_the_overview_is_fast_with_unsaved_edits_too(env):
    ids = _big(env)
    drafts.run_edit(env, lambda d: d.set_notify(ids[3], "on"))
    drafts.run_edit(env, lambda d: d.remove_item(ids[7]))
    runs = []
    for _ in range(15):
        t = time.perf_counter()
        assert env.api.blocking_overview_get()["ok"]
        runs.append((time.perf_counter() - t) * 1000)
    p95 = sorted(runs)[int(len(runs) * 0.95) - 1]
    print(f"blocking_overview_get with an unsaved draft: p95 {p95:.1f} ms")
    assert p95 <= 100, f"p95 {p95:.1f} ms (target 50)"


def test_a_bad_id_is_named_with_the_right_article(env):
    assert env.api.draft_set_notify("x", "on")["error"] == "Bad request: an item number expected."
    assert env.api.draft_remove_group("x")["error"] == "Bad request: a group number expected."
