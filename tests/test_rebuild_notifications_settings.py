"""R27: the Notifications and Settings pages' Api (webapi/notifications.py, webapi/settings.py).

What is checked: the alert settings go through the DRAFT (only `notify.*`, shown in the save bar, "Show as" tells the notice
sink), the weekly summary is saved directly, every loosening change (reset moved back + start now, more / longer / another
period / new kind / turning on the emergency unlock, every import) waits under a Python-checked challenge ticket while the
free ones (later reset, "From {end}", fewer / shorter / remove, off) are saved at once, an import is read again by its token
and a file that changed in between is refused, nothing takes a path from the page, `ui_engine_set` takes only web / legacy,
and the old window's new switch writes the same key."""
import json
import random
from datetime import datetime
from types import SimpleNamespace

import pytest

import alerts
import antibypass as ab
import backup
import emergency
import webapi
from rules import RESET_KEY
from webapi import core, notifications, settings
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)          # a Sunday


@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


class Clock:
    now = NOW


@pytest.fixture
def clock():
    Clock.now = NOW
    return Clock


@pytest.fixture
def ctx(tmp_path, clock):
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: clock.now)
    c.guard = GuardService(lambda: c.db, lambda db: clock.now, rng=random.Random(3))
    c.published = []
    c.publish = lambda event, payload=None, key=None: c.published.append((event, payload))
    return c


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def error(reply):
    assert not reply["ok"], reply
    return reply["error"]


def locked(ctx, **kw):
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30, **kw})


def needs_ticket(reply):
    d = data(reply)
    assert d.get("needs_challenge") and d.get("ticket"), d
    return d["ticket"]


def pass_challenge(ctx, api, ticket):
    """What the page does: begin, type what Python showed, submit (at a person's speed)."""
    begin = data(api.challenge_begin(ticket))
    mono = ctx.guard._mono
    ctx.guard._mono = lambda: mono() + 60
    return data(api.challenge_submit(ticket, begin["grid_words"] if begin["mode"] == "grid" else begin["phrase"]))


def saved(ctx, key, default=None):
    return ctx.db.get_setting(key, default)


# ---------------------------------------------------------------- classification

def test_the_methods_are_classified():
    kinds = dict(webapi.api_methods())
    assert kinds["notifications_get"] == "read" and kinds["settings_get"] == "read"
    assert kinds["reset_change_preview"] == "read"
    for name in ("digest_set", "notify_repeat_set", "notify_test", "set_goal", "ui_engine_set", "backup_export",
                 "backup_import_pick"):
        assert kinds[name] == "ui", name
    for name in ("reset_change_apply", "emergency_settings_save", "backup_import_apply"):
        assert kinds[name] == "loosen", name


def test_nothing_takes_a_path_or_a_confirmed_flag():
    import inspect
    for name in ("backup_export", "backup_import_pick", "backup_import_apply", "reset_change_apply",
                 "emergency_settings_save"):
        params = list(inspect.signature(core.registry.get(name)[0]).parameters)[1:]
        assert not {"path", "file", "filename", "confirmed", "force"} & set(params), (name, params)
    assert list(inspect.signature(settings.backup_import_apply).parameters)[1:] == ["token"]
    assert list(inspect.signature(settings.backup_export).parameters)[1:] == []
    # the generic settings writer stays closed for everything but the allow-listed cosmetic keys
    assert core.ui_setting_allowed("ui.reduce_motion") and not core.ui_setting_allowed("emergency.enabled")
    assert not core.ui_setting_allowed("limits.reset") and not core.ui_setting_allowed("ui.engine")
    assert not core.ui_setting_allowed("antibypass") and not core.ui_setting_allowed("app.off")


def test_the_engine_key_is_the_one_main_reads():
    import main
    from gui import settings as legacy
    assert settings.ENGINE_KEY == main.ENGINE_KEY == legacy.ENGINE_KEY == "ui.engine"


# ---------------------------------------------------------------- Notifications

def test_the_notifications_page_data_has_everything(api):
    d = data(api.notifications_get())
    assert [r["key"] for r in d["reasons"]] == list(alerts.REASONS) and len(d["reasons"]) == 7
    assert d["reasons"][0]["default"] == alerts.DEFAULT_MESSAGES["permanent"]
    assert all(d["enabled"][k] for k in alerts.REASONS)
    assert d["messages"]["schedule"] == alerts.DEFAULT_MESSAGES["schedule"]
    assert d["cooldown"] == 30 and d["cooldown_options"] == [1, 5, 15, 30, 60]
    assert d["format"] == "toast" and [f["key"] for f in d["formats"]] == ["toast", "inapp", "both"]
    assert d["warn"] == {"enabled": True, "minutes": 5, "repeat_sec": 0, "repeat_text": "Off"}
    assert d["started"] is True
    assert d["digest"]["enabled"] is True and d["digest"]["day"] == 6 and d["digest"]["time"] == "19:00"
    assert d["digest"]["text"] == "Sunday at 19:00" and len(d["digest"]["days"]) == 7
    assert d["draft"] == {"dirty": False, "autosave": True, "change_summary": ""}


def test_a_saved_odd_value_is_still_offered_in_the_menus(api, ctx):
    ctx.db.set_setting("notify.cooldown_min", "7")
    ctx.db.set_setting("digest.time", "19:30")
    d = data(api.notifications_get())
    assert d["cooldown"] == 7 and 7 in d["cooldown_options"]
    assert "19:30" in d["digest"]["times"] and len(d["digest"]["times"]) == 25


def test_notify_settings_go_through_the_draft_and_nothing_else_is_accepted(api, ctx):
    assert data(api.draft_set_setting("notify.cooldown_min", "15"))["dirty"] is False       # autosave: saved at once
    assert saved(ctx, "notify.cooldown_min") == "15"
    for key in ("emergency.enabled", "limits.reset", "antibypass", "digest.enabled", "ui.engine", "notify.nope"):
        assert "can't be changed" in error(api.draft_set_setting(key, "1"))
    assert saved(ctx, "emergency.enabled") is None and saved(ctx, "digest.enabled") is None
    assert "isn't valid" in error(api.draft_set_setting("notify.cooldown_min", "0"))
    assert "isn't valid" in error(api.draft_set_setting("notify.format", "popup"))


def test_with_autosave_off_the_settings_wait_for_save_and_the_page_shows_them(api, ctx):
    data(api.draft_set_autosave(False))
    d = data(api.draft_set_setting("notify.enabled.mode", "0"))
    assert d["dirty"] is True and "Notification settings" in d["change_summary"]
    assert saved(ctx, "notify.enabled.mode") is None                                    # nothing written yet
    page = data(api.notifications_get())
    assert page["enabled"]["mode"] is False and page["draft"]["dirty"] is True            # the page shows the pending value
    data(api.draft_discard())
    assert data(api.notifications_get())["enabled"]["mode"] is True                      # Discard: back to the saved one
    data(api.draft_set_setting("notify.enabled.mode", "0"))
    data(api.draft_save())
    assert saved(ctx, "notify.enabled.mode") == "0" and data(api.notifications_get())["enabled"]["mode"] is False


def test_a_message_box_saves_text_and_empty_means_the_default_the_page_sends(api, ctx):
    data(api.draft_set_setting("notify.msg.permanent", "No {site} for you"))
    assert saved(ctx, "notify.msg.permanent") == "No {site} for you"
    assert data(api.notifications_get())["messages"]["permanent"] == "No {site} for you"


def test_show_as_tells_the_notice_sink_after_the_draft_is_saved(api, ctx):
    calls = []
    ctx.notices = SimpleNamespace(refresh=lambda: calls.append("refresh"))
    data(api.draft_set_setting("notify.format", "both"))                  # autosave on: saved now
    assert saved(ctx, "notify.format") == "both" and calls == ["refresh"]
    data(api.draft_set_autosave(False))
    data(api.draft_set_setting("notify.format", "inapp"))
    assert calls == ["refresh"]                                           # still waiting: nothing changed for the sink
    data(api.draft_save())
    assert calls == ["refresh", "refresh"] and saved(ctx, "notify.format") == "inapp"


def test_the_repeat_box_is_parsed_in_python(api, ctx):
    d = data(api.notify_repeat_set("30s"))
    assert d["seconds"] == 30 and d["text"] == "30s" and saved(ctx, "notify.warn.repeat_sec") == "30"
    assert data(api.notify_repeat_set("2 min"))["seconds"] == 120
    assert data(api.notify_repeat_set("1h30"))["text"] == "1h30"
    assert data(api.notify_repeat_set("Off"))["seconds"] == 0 and saved(ctx, "notify.warn.repeat_sec") == "0"
    assert data(api.notifications_get())["warn"]["repeat_text"] == "Off"
    for bad in ("2 parsecs", "1s", "x" * 50, 5, None):
        error(api.notify_repeat_set(bad))
    assert saved(ctx, "notify.warn.repeat_sec") == "0"


def test_the_repeat_box_waits_in_the_draft_when_autosave_is_off(api, ctx):
    data(api.draft_set_autosave(False))
    d = data(api.notify_repeat_set("45s"))
    assert d["dirty"] is True and saved(ctx, "notify.warn.repeat_sec") is None
    assert data(api.notifications_get())["warn"]["repeat_sec"] == 45


def test_the_weekly_summary_is_saved_directly(api, ctx):
    data(api.draft_set_autosave(False))                                  # (it must not depend on the draft)
    d = data(api.digest_set(False, 2, "08:00"))
    assert d == {"enabled": False, "day": 2, "time": "08:00", "text": "Wednesday at 08:00"}
    assert [saved(ctx, k) for k in ("digest.enabled", "digest.day", "digest.time")] == ["0", "2", "08:00"]
    assert data(api.draft_get_state())["dirty"] is False                 # no draft involved
    assert data(api.digest_set(True, 6, "9:05"))["time"] == "09:05"


@pytest.mark.parametrize("args", [(1, 2, "08:00"), (True, 7, "08:00"), (True, -1, "08:00"), (True, "2", "08:00"),
                                  (True, 2, "25:00"), (True, 2, "late"), (True, 2, 8), (True, 2, "08:00:00")])
def test_the_weekly_summary_checks_its_values(api, ctx, args):
    error(api.digest_set(*args))
    assert saved(ctx, "digest.day") is None


def test_send_a_test_goes_through_the_normal_notice_path(api, ctx):
    error(api.notify_test())                                             # (no agent hub: nothing to send through)
    sent = []
    ctx.hub = SimpleNamespace(notify=lambda message: sent.append(message) or True)
    assert data(api.notify_test()) == {"sent": True} and sent == [notifications.TEST_NOTE]
    ctx.hub = SimpleNamespace(notify=lambda message: False)
    assert data(api.notify_test()) == {"sent": False}


# ---------------------------------------------------------------- Settings: the page data and the cosmetic ones

def test_the_settings_page_data(api, ctx):
    d = data(api.settings_get())
    assert d["now"] == "2026-10-11T15:42:17" and d["engine"] == "legacy" and d["motion"] is False
    assert d["look"]["theme"] == "dark" and d["look"]["accents"]["Orange"] == "#DB5126" and d["look"]["scale"] == "auto"
    assert d["goal"] == 5 and d["goal_options"] == list(range(13))
    assert d["reset"]["time"] == "00:00" and d["reset"]["ends_text"] == "Mon 12 Oct 00:00" and d["reset"]["carry_note"] == ""
    assert [c["key"] for c in d["categories"]][:3] == ["productive", "neutral", "games"]
    e = d["emergency"]
    assert e["enabled"] is True and e["max_kinds"] == 5 and e["max_uses"] == 10 and 20 in e["minute_options"]
    [k] = e["kinds"]
    assert (k["uses"], k["per"], k["minutes"], k["left"], k["allowed"]) == (3, "week", 20, 3, 3)
    assert k["per_text"] == "this week" and k["resets_text"] == "Mon 12 Oct 00:00"


def test_the_meters_count_from_now(api, ctx, clock):
    emergency.unlock(ctx.db, [], NOW, alerts=True, tier="t1")
    [k] = data(api.settings_get())["emergency"]["kinds"]
    assert (k["left"], k["allowed"]) == (2, 3)


def test_get_theme_carries_reduce_motion(api, ctx):
    assert data(api.get_theme())["motion"] is False
    data(api.ui_setting("ui.reduce_motion", "1"))
    assert data(api.get_theme())["motion"] is True and data(api.settings_get())["motion"] is True


def test_the_daily_goal(api, ctx):
    assert data(api.set_goal(8)) == {"goal": 8} and saved(ctx, "stats.goal_hours") == "8"
    assert data(api.set_goal(0))["goal"] == 0 and data(api.settings_get())["goal"] == 0
    for bad in (13, -1, "5", 2.5, True, None):
        error(api.set_goal(bad))
    assert saved(ctx, "stats.goal_hours") == "0"
    assert ("dirty", {"page": "dashboard"}) in ctx.published


def test_the_goal_is_free_while_locked(api, ctx):
    locked(ctx)
    assert data(api.set_goal(3)) == {"goal": 3}


def test_ui_engine_set_takes_only_web_or_legacy(api, ctx):
    assert data(api.ui_engine_set("web")) == {"engine": "web"} and saved(ctx, "ui.engine") == "web"
    assert data(api.settings_get())["engine"] == "web"
    assert data(api.ui_engine_set("legacy")) == {"engine": "legacy"} and saved(ctx, "ui.engine") == "legacy"
    for bad in ("Web", "new", "", None, True, ["web"], "web "):
        error(api.ui_engine_set(bad))
    assert saved(ctx, "ui.engine") == "legacy"
    import main
    assert main.use_new_ui([], "web") and not main.use_new_ui([], "legacy")      # the entry reads exactly these two words


@pytest.mark.parametrize("default", ["legacy", "web"])
def test_settings_get_engine_follows_the_default_when_nothing_is_saved(api, ctx, monkeypatch, default):
    import ui_theme
    monkeypatch.setattr(ui_theme, "DEFAULT_ENGINE", default)
    assert data(api.settings_get())["engine"] == default
    data(api.ui_engine_set("legacy" if default == "web" else "web"))
    assert data(api.settings_get())["engine"] != default                         # a saved choice wins


def test_the_old_windows_switch_writes_the_key(tmp_path):
    from db import Database
    from gui.settings import SettingsPage
    db = Database(tmp_path / "t.db")
    page = SettingsPage.__new__(SettingsPage)
    page.db = db
    page.engine_line = SimpleNamespace(pack=lambda **kw: setattr(page, "line_shown", True))
    page.engine_switch = SimpleNamespace(get=lambda: 1)
    page._engine_changed()
    assert db.get_setting("ui.engine") == "web" and page.line_shown                # (and offers the restart)
    page.engine_switch = SimpleNamespace(get=lambda: 0)
    page._engine_changed()
    assert db.get_setting("ui.engine") == "legacy"


# ---------------------------------------------------------------- Settings: when limits reset

def test_a_later_reset_time_is_free_even_while_locked(api, ctx):
    locked(ctx)
    pv = data(api.reset_change_preview("04:00"))
    assert pv["looser"] is False and pv["time"] == "04:00" and pv["end_text"] == "Mon 12 Oct 00:00"
    d = data(api.reset_change_apply("04:00", False))
    assert d["reset"]["time"] == "04:00" and json.loads(saved(ctx, RESET_KEY))["time"] == "04:00"
    assert ctx.db.limit_clock().time.hour == 4


def test_moving_it_back_then_start_now_asks_while_locked_and_saves_nothing_first(api, ctx):
    data(api.reset_change_apply("04:00", False))
    before = saved(ctx, RESET_KEY)
    locked(ctx)
    pv = data(api.reset_change_preview("02:00"))
    assert pv["looser"] is True and pv["time"] == "02:00" and pv["end_text"].startswith("Mon 12 Oct 04:00")
    ticket = needs_ticket(api.reset_change_apply("02:00", True))
    assert saved(ctx, RESET_KEY) == before                                # nothing is saved before the challenge
    done = pass_challenge(ctx, api, ticket)
    assert done["result"]["reset"]["time"] == "02:00"
    cfg = json.loads(saved(ctx, RESET_KEY))
    assert cfg["time"] == "02:00" and "day_start" not in cfg              # started fresh now: no carried-over day


def test_moving_it_back_from_the_end_of_the_day_is_free(api, ctx):
    data(api.reset_change_apply("04:00", False))
    locked(ctx)
    before_end = data(api.settings_get())["reset"]["ends_text"]
    d = data(api.reset_change_apply("02:00", False))
    cfg = json.loads(saved(ctx, RESET_KEY))
    assert cfg["time"] == "02:00" and cfg["day_start"] and cfg["switch"]  # 02:00 takes over when the running day ends ...
    assert d["reset"]["ends_text"] == before_end                          # ... which does not end any earlier
    assert "runs to" in d["reset"]["carry_note"]


def test_start_now_is_ignored_for_a_later_time(api, ctx):
    """"Start a fresh day now" with a LATER time would hand back the limits of today: it is never honoured."""
    locked(ctx)
    d = data(api.reset_change_apply("05:00", True))
    cfg = json.loads(saved(ctx, RESET_KEY))
    assert d["reset"]["time"] == "05:00" and cfg["time"] == "05:00" and cfg["day_start"]         # (the carry-over: nothing restarted)
    assert d["reset"]["ends_text"] == "Mon 12 Oct 05:00"


def test_start_now_is_free_when_nothing_is_locked(api, ctx):
    data(api.reset_change_apply("04:00", False))
    d = data(api.reset_change_apply("02:00", True))
    assert d["reset"]["time"] == "02:00" and "day_start" not in json.loads(saved(ctx, RESET_KEY))


def test_a_cancelled_challenge_leaves_the_reset_alone(api, ctx):
    data(api.reset_change_apply("04:00", False))
    before = saved(ctx, RESET_KEY)
    locked(ctx)
    ticket = needs_ticket(api.reset_change_apply("02:00", True))
    data(api.challenge_cancel(ticket))
    assert saved(ctx, RESET_KEY) == before


@pytest.mark.parametrize("bad", ["25:00", "abc", "", "12", None, 4, "00:00", "4:0:0", "12:60"])
def test_a_bad_or_unchanged_reset_time_is_refused(api, ctx, bad):
    error(api.reset_change_preview(bad))
    error(api.reset_change_apply(bad, False))
    assert saved(ctx, RESET_KEY) is None


def test_start_now_must_be_yes_or_no(api, ctx):
    for bad in ("yes", 1, None):
        error(api.reset_change_apply("04:00", bad))


def test_the_reset_pokes_the_status_worker_and_tells_the_pages(api, ctx):
    pokes = []
    ctx.hub = SimpleNamespace(status_worker=SimpleNamespace(poke=lambda: pokes.append(1)))
    data(api.reset_change_apply("04:00", False))
    assert pokes == [1] and ("dirty", {"page": "dashboard"}) in ctx.published


# ---------------------------------------------------------------- Settings: emergency unlock

def kinds(ctx):
    return emergency.tiers(ctx.db)


def config(ctx, **edit):
    """The page's picture of the card, with `edit` applied to the first kind (or the whole thing)."""
    cfg = {"enabled": emergency.config(ctx.db)["enabled"], "tiers": [dict(t) for t in kinds(ctx)]}
    cfg.update({k: v for k, v in edit.items() if k in cfg})
    for k, v in edit.items():
        if k not in ("enabled", "tiers"):
            cfg["tiers"][0][k] = v
    return cfg


@pytest.mark.parametrize("edit", [{"uses": 4}, {"minutes": 30}, {"per": "month"}, {"per": "day"}])
def test_more_or_longer_or_another_period_needs_the_challenge(api, ctx, edit):
    locked(ctx)
    ticket = needs_ticket(api.emergency_settings_save(config(ctx, **edit)))
    assert kinds(ctx)[0]["uses"] == 3 and kinds(ctx)[0]["per"] == "week"              # nothing saved yet
    pass_challenge(ctx, api, ticket)
    [k] = kinds(ctx)
    assert {f: k[f] for f in edit} == edit


@pytest.mark.parametrize("edit", [{"uses": 2}, {"minutes": 10}])
def test_fewer_or_shorter_is_saved_at_once_even_while_locked(api, ctx, edit):
    locked(ctx)
    d = data(api.emergency_settings_save(config(ctx, **edit)))
    assert not d.get("needs_challenge")
    [k] = kinds(ctx)
    assert {f: k[f] for f in edit} == edit
    assert d["emergency"]["kinds"][0]["uses"] == k["uses"]


def test_per_day_to_per_week_is_free_but_week_to_month_is_not(api, ctx):
    emergency.save_tiers(ctx.db, [{"id": "t1", "uses": 3, "per": "day", "minutes": 20}])
    locked(ctx)
    d = data(api.emergency_settings_save(config(ctx, per="week")))                  # day -> week: a longer wait, free
    assert not d.get("needs_challenge") and kinds(ctx)[0]["per"] == "week"
    needs_ticket(api.emergency_settings_save(config(ctx, per="month")))             # week -> month: asked
    assert kinds(ctx)[0]["per"] == "week"


def test_a_new_kind_needs_the_challenge_and_gets_an_id_from_python(api, ctx):
    locked(ctx)
    cfg = config(ctx)
    cfg["tiers"].append({"uses": 1, "per": "month", "minutes": 60})
    ticket = needs_ticket(api.emergency_settings_save(cfg))
    assert len(kinds(ctx)) == 1
    pass_challenge(ctx, api, ticket)
    [first, added] = kinds(ctx)
    assert first["id"] == "t1" and len(added["id"]) == 8 and added["minutes"] == 60


def test_an_id_the_page_makes_up_is_never_kept(api, ctx):
    emergency.save_tiers(ctx.db, [{"id": "t1", "uses": 3, "per": "week", "minutes": 20}])
    cfg = {"enabled": True, "tiers": [{"id": "t1", "uses": 3, "per": "week", "minutes": 20},
                                      {"id": "t1", "uses": 1, "per": "day", "minutes": 5},          # the same id again
                                      {"id": "gone", "uses": 1, "per": "day", "minutes": 5}]}      # an id Python never had
    data(api.emergency_settings_save(cfg))
    ids = [t["id"] for t in kinds(ctx)]
    assert ids[0] == "t1" and "gone" not in ids and len(set(ids)) == 3


def test_removing_a_kind_is_free_even_while_locked(api, ctx):
    emergency.save_tiers(ctx.db, [{"id": "a", "uses": 1, "per": "day", "minutes": 5},
                                  {"id": "b", "uses": 2, "per": "week", "minutes": 10}])
    locked(ctx)
    d = data(api.emergency_settings_save({"enabled": True, "tiers": [{"id": "b", "uses": 2, "per": "week", "minutes": 10}]}))
    assert [t["id"] for t in kinds(ctx)] == ["b"] and [k["id"] for k in d["emergency"]["kinds"]] == ["b"]
    data(api.emergency_settings_save({"enabled": True, "tiers": []}))
    assert kinds(ctx) == []


def test_turning_it_on_needs_the_challenge_and_off_is_free(api, ctx):
    locked(ctx)
    data(api.emergency_settings_save(config(ctx, enabled=False)))                   # off: free
    assert saved(ctx, "emergency.enabled") == "0"
    ticket = needs_ticket(api.emergency_settings_save(config(ctx, enabled=True)))
    assert saved(ctx, "emergency.enabled") == "0"
    pass_challenge(ctx, api, ticket)
    assert saved(ctx, "emergency.enabled") == "1"


def test_while_it_is_off_any_edit_is_free(api, ctx):
    data(api.emergency_settings_save(config(ctx, enabled=False)))
    locked(ctx)
    d = data(api.emergency_settings_save(config(ctx, enabled=False, uses=10, minutes=60)))
    assert not d.get("needs_challenge") and kinds(ctx)[0]["uses"] == 10


def test_nothing_is_asked_when_anti_bypass_is_off(api, ctx):
    d = data(api.emergency_settings_save(config(ctx, uses=9)))
    assert not d.get("needs_challenge") and kinds(ctx)[0]["uses"] == 9


@pytest.mark.parametrize("bad", [
    None, [], {"enabled": "yes", "tiers": []}, {"enabled": True}, {"enabled": True, "tiers": "x"},
    {"enabled": True, "tiers": [], "extra": 1},
    {"enabled": True, "tiers": ["x"]},
    {"enabled": True, "tiers": [{"uses": 0, "per": "week", "minutes": 5}]},
    {"enabled": True, "tiers": [{"uses": 11, "per": "week", "minutes": 5}]},
    {"enabled": True, "tiers": [{"uses": 1, "per": "year", "minutes": 5}]},
    {"enabled": True, "tiers": [{"uses": 1, "per": "week", "minutes": 61}]},
    {"enabled": True, "tiers": [{"uses": 1, "per": "week", "minutes": 0}]},
    {"enabled": True, "tiers": [{"uses": "1", "per": "week", "minutes": 5}]},
    {"enabled": True, "tiers": [{"uses": True, "per": "week", "minutes": 5}]},
    {"enabled": True, "tiers": [{"uses": 1, "per": "week", "minutes": 5.5}]},
    {"enabled": True, "tiers": [{"uses": 1, "per": "week", "minutes": 5}] * 6},
])
def test_the_emergency_card_is_checked(api, ctx, bad):
    before = kinds(ctx)
    error(api.emergency_settings_save(bad))
    assert kinds(ctx) == before


def test_the_emergency_save_tells_the_status_worker_and_pages(api, ctx):
    pokes = []
    ctx.hub = SimpleNamespace(status_worker=SimpleNamespace(poke=lambda: pokes.append(1)))
    data(api.emergency_settings_save(config(ctx, uses=2)))
    assert pokes == [1] and ("dirty", {"page": "blocking"}) in ctx.published


# ---------------------------------------------------------------- Settings: backup, import, export

class Dialogs:
    """Stands for the native Open / Save dialogs: what the owner "picks" is set by the test."""
    open_path = None
    save_path = None


@pytest.fixture
def dialogs(monkeypatch):
    Dialogs.open_path = Dialogs.save_path = None
    monkeypatch.setattr(settings, "_ask_open_path", lambda window: Dialogs.open_path)
    monkeypatch.setattr(settings, "_ask_save_path", lambda window, name: Dialogs.save_path)
    return Dialogs


def backup_file(ctx, tmp_path, name="b.json", **change):
    """A backup of the current setup, edited with `change` (top-level keys), written to tmp_path/name."""
    d = backup.export(ctx.db)
    d.update(change)
    p = tmp_path / name
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def add_item(ctx, name="Reddit"):
    ctx.db.add_item(name, [f"{name.lower()}.com"], "site", "test", [{"rule_type": "permanent"}], None, None, None)


def test_export_writes_the_backup_where_the_owner_chose(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    dialogs.save_path = str(tmp_path / "mine")
    d = data(api.backup_export())
    assert d == {"path": str(tmp_path / "mine") + ".json"}
    saved_file = json.loads((tmp_path / "mine.json").read_text(encoding="utf-8"))
    assert saved_file["lockdown"] == backup.FORMAT and [i["display_name"] for i in saved_file["items"]] == ["Reddit"]
    dialogs.save_path = None
    assert data(api.backup_export()) == {"cancelled": True}


def test_export_names_the_file_after_the_trusted_day(api, ctx, monkeypatch):
    seen = []
    monkeypatch.setattr(settings, "_ask_save_path", lambda window, name: seen.append(name))
    data(api.backup_export())
    assert seen == ["lockdown-backup-2026-10-11.json"]


def test_export_to_an_unwritable_place_says_so(api, ctx, dialogs, tmp_path):
    dialogs.save_path = str(tmp_path / "no" / "such" / "folder" / "x.json")
    assert "Couldn't save" in error(api.backup_export())


def test_pick_shows_what_would_change_and_changes_nothing(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    other = backup_file(ctx, tmp_path, items=[])
    dialogs.open_path = str(other)
    d = data(api.backup_import_pick())
    assert d["name"] == "b.json" and d["items"] == 0 and d["token"]
    assert any(line.startswith("Blocked sites & apps: 1 now -> 0 after") for line in d["lines"])
    assert len(ctx.db.list_items()) == 1
    dialogs.open_path = None
    assert data(api.backup_import_pick()) == {"cancelled": True}


@pytest.mark.parametrize("content", ["not json at all", "[]", '{"lockdown": 99, "items": []}', '{"lockdown": 2}',
                                     '{"lockdown": 2, "items": [1]}', '{"lockdown": 2, "items": [], "settings": []}',
                                     '{"lockdown": 2, "items": [], "settings": {"a": 1}}',
                                     '{"lockdown": 2, "items": [], "groups": [3]}'])
def test_a_file_that_is_not_a_backup_is_refused_before_anything_is_touched(api, ctx, dialogs, tmp_path, content):
    add_item(ctx)
    p = tmp_path / "bad.json"
    p.write_text(content, encoding="utf-8")
    dialogs.open_path = str(p)
    error(api.backup_import_pick())
    assert len(ctx.db.list_items()) == 1


def test_a_missing_or_huge_file_is_refused(api, ctx, dialogs, tmp_path, monkeypatch):
    dialogs.open_path = str(tmp_path / "nothing.json")
    assert "Couldn't read" in error(api.backup_import_pick())
    big = tmp_path / "big.json"
    big.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "MAX_BACKUP_BYTES", 1)
    dialogs.open_path = str(big)
    assert "too big" in error(api.backup_import_pick())


def test_import_always_goes_through_the_guard_while_locked(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    dialogs.open_path = str(backup_file(ctx, tmp_path, items=[]))
    token = data(api.backup_import_pick())["token"]
    locked(ctx)
    ticket = needs_ticket(api.backup_import_apply(token))
    assert len(ctx.db.list_items()) == 1                                   # nothing yet
    done = pass_challenge(ctx, api, ticket)
    assert done["result"] == {"items": 0, "groups": 0}
    assert ctx.db.list_items() == []


def test_import_runs_at_once_when_anti_bypass_is_off(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    dialogs.open_path = str(backup_file(ctx, tmp_path, items=[]))
    token = data(api.backup_import_pick())["token"]
    d = data(api.backup_import_apply(token))
    assert d == {"items": 0, "groups": 0} and ctx.db.list_items() == []


def test_import_discards_the_draft_and_tells_every_page(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    data(api.draft_set_autosave(False))
    data(api.draft_set_setting("notify.enabled.mode", "0"))                # a pending draft edit
    dialogs.open_path = str(backup_file(ctx, tmp_path, items=[]))
    token = data(api.backup_import_pick())["token"]
    ctx.published.clear()
    data(api.backup_import_apply(token))
    assert data(api.draft_get_state())["dirty"] is False
    assert {p["page"] for e, p in ctx.published if e == "dirty"} == set(settings.PAGES)
    assert any(e == "draft" and p["what"] == "discarded" for e, p in ctx.published)


def test_a_changed_file_after_the_preview_is_refused(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    p = backup_file(ctx, tmp_path, items=[])
    dialogs.open_path = str(p)
    token = data(api.backup_import_pick())["token"]
    backup_file(ctx, tmp_path, items=[{"id": 1, "display_name": "Evil", "target": "evil.com", "item_type": "site"}])
    assert "changed since" in error(api.backup_import_apply(token))
    assert [i["display_name"] for i in ctx.db.list_items()] == ["Reddit"]
    assert "Choose the backup file again" in error(api.backup_import_apply(token))      # the token was used up


def test_a_file_that_changes_while_the_challenge_is_open_is_refused_when_it_would_run(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    p = backup_file(ctx, tmp_path, items=[])
    dialogs.open_path = str(p)
    token = data(api.backup_import_pick())["token"]
    locked(ctx)
    ticket = needs_ticket(api.backup_import_apply(token))
    backup_file(ctx, tmp_path, items=[{"id": 1, "display_name": "Evil", "target": "evil.com", "item_type": "site"}])
    begin = data(api.challenge_begin(ticket))
    mono = ctx.guard._mono
    ctx.guard._mono = lambda: mono() + 60
    reply = api.challenge_submit(ticket, begin["phrase"])
    assert not reply["ok"] and "changed since" in reply["error"]
    assert [i["display_name"] for i in ctx.db.list_items()] == ["Reddit"]


def test_the_token_is_the_only_way_in(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    dialogs.open_path = str(backup_file(ctx, tmp_path, items=[]))
    token = data(api.backup_import_pick())["token"]
    for bad in ("x", "", None, 5, token + "x", [token]):
        dialogs.open_path = str(backup_file(ctx, tmp_path, items=[]))
        token = data(api.backup_import_pick())["token"]
        error(api.backup_import_apply(bad))
    assert len(ctx.db.list_items()) == 1


def test_a_wrong_token_forgets_the_pick(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    dialogs.open_path = str(backup_file(ctx, tmp_path, items=[]))
    token = data(api.backup_import_pick())["token"]
    error(api.backup_import_apply("nope"))
    error(api.backup_import_apply(token))
    assert len(ctx.db.list_items()) == 1


def test_a_picked_file_expires(api, ctx, dialogs, tmp_path, monkeypatch):
    add_item(ctx)
    dialogs.open_path = str(backup_file(ctx, tmp_path, items=[]))
    now = [1000.0]
    monkeypatch.setattr(settings, "_clock", lambda: now[0])
    token = data(api.backup_import_pick())["token"]
    now[0] += settings.IMPORT_TTL_SEC + 1
    assert "Choose the backup file again" in error(api.backup_import_apply(token))
    assert len(ctx.db.list_items()) == 1


def test_a_second_pick_replaces_the_first(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    dialogs.open_path = str(backup_file(ctx, tmp_path, "one.json", items=[]))
    first = data(api.backup_import_pick())["token"]
    dialogs.open_path = str(backup_file(ctx, tmp_path, "two.json"))
    second = data(api.backup_import_pick())["token"]
    error(api.backup_import_apply(first))
    assert len(ctx.db.list_items()) == 1
    assert second != first


def test_an_import_never_switches_lockdown_off_or_on(api, ctx, dialogs, tmp_path):
    add_item(ctx)
    d = backup.export(ctx.db)
    d["settings"][ab.OFF_KEY] = "2026-10-11 10:00:00"                      # a backup made while Lockdown was off
    p = tmp_path / "off.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    dialogs.open_path = str(p)
    data(api.backup_import_apply(data(api.backup_import_pick())["token"]))
    assert not ab.is_off(ctx.db)


def test_an_import_restores_settings_groups_and_categories(api, ctx, dialogs, tmp_path):
    add_item(ctx, "Reddit")
    add_item(ctx, "Twitter")
    ctx.db.set_setting("limits.reset", json.dumps({"time": "04:00"}))
    p = backup_file(ctx, tmp_path)
    ctx.db.remove_item(min(ctx.db.item_ids()))
    ctx.db.set_setting("limits.reset", json.dumps({"time": "01:00"}))
    dialogs.open_path = str(p)
    d = data(api.backup_import_apply(data(api.backup_import_pick())["token"]))
    assert d["items"] == 2 and len(ctx.db.list_items()) == 2
    assert json.loads(saved(ctx, RESET_KEY))["time"] == "04:00"


def test_csv_export_is_the_screen_time_modules_and_not_registered_twice():
    names = [n for n, _kind in webapi.api_methods()]
    assert names.count("screentime_export_csv") == 1
