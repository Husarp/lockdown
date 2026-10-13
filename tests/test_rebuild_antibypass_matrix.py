"""R28: the anti-bypass matrix of the whole Api - every method the page can call, classified and checked.

The page is untrusted. What this file proves over the real bridge (core.build_api, the real GuardService):
- every Api method is in the table below with its class, and nothing else is exposed;
- a `read` changes nothing in the database (run on a synthetic one, every table compared before / after);
- a `loosen` method, given an input that loosens, while Anti-Bypass is locked returns a ticket and changes nothing (also
  after the ticket is cancelled); passing the challenge in Python applies it; with Anti-Bypass free (unlocked) it applies
  at once; forged client fields (confirmed / force / free / minutes, extra arguments) never skip the challenge;
- `tighten` / `ui` methods can't express a loosening (each one run on a locked setup leaves every block as it was or
  stricter);
- the rules of BLUEPRINT section 4 and the recent CHANGELOG anti-bypass rules, row by row, through the bridge;
- bridge hygiene: the exposed object, the asset server, fixed paths / URLs, the challenge tickets.
"""
import inspect
import json
import random
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import antibypass as ab
import backup
import challenge_secrets as cs
import emergency
import keywords
import modes
import pause
import reminders
import rules
import synthdata
import webapi
from blocker import protection
from db import RULE_COLUMNS, Database
from resident import selftest
from resident.assets import AssetServer
from webapi import about, categories, core, guard
from webapi import draft as drafts
from webapi import settings as settings_api
from webapi.guard import GuardService

NOW = datetime(2026, 10, 11, 15, 42, 17)          # a Sunday afternoon
ROOT = Path(__file__).resolve().parents[1]

# ================================================================ the classification (every method, by hand)

READ = """about_get antibypass_get applog_get applog_text antibypass_status appmenu_info apps_list blocking_overview_get blocklist_brief calendar_get
categories_list dashboard_get display_get draft_get_state emergency_get_candidates get_status get_theme group_get
group_member_search groups_list host_normalize item_get manual_get mode_extra_check modes_brief modes_get
network_block_target network_get network_graph notifications_get overlay_emergency_kinds pause_get pick_program_file
protection_check protection_get reminders_get reset_change_preview rules_validate screentime_day screentime_get
screentime_month settings_get suggest update_notes update_state wordlist_get words_get"""
TIGHTEN = """antibypass_lock antibypass_recovery_remove antibypass_switch_on break_allow_remove draft_add_to_group manual_create pause_resume
protection_connect protection_unallow service_start"""
LOOSEN = """antibypass_save antibypass_try antibypass_unlock_to_edit antibypass_password_set antibypass_secret_remove
antibypass_totp_confirm antibypass_recovery_new backup_import_apply break_allow_add break_allow_restore
category_add category_add_and_assign
category_assign category_remove draft_remove_group draft_remove_item draft_save draft_set_autosave draft_set_disabled
draft_set_group draft_set_group_disabled draft_submit_item emergency_settings_save emergency_unlock lockdown_switch_off
manual_remove manual_save_entries manual_set_enabled mode_delete mode_save mode_start mode_stop overlay_answer
overlay_disable overlay_emergency pause_start protection_allow protection_allow_words protection_remove_custom protection_set_list_enabled
reminder_delete reminder_save_break reminder_save_custom reminder_save_sleep reminder_set_on reset_change_apply
words_update"""
UI = """antibypass_totp_begin applog_save backup_export backup_import_pick blocking_set_sort category_set_color challenge_begin challenge_cancel challenge_submit
corner_answer corner_ready corner_size digest_set display_set draft_discard draft_set_notify draft_set_setting
log_client_error network_export_csv network_live_stop notify_front_steps_set notify_repeat_set notify_test open_path
open_url overlay_ready
page_ready protection_preview protection_set_updates protection_update_now reveal_in_explorer screentime_export_csv
set_accent set_appearance set_goal set_icons_download set_scale suggest_clear_history ui_ready ui_setting update_check
update_download update_install update_opened update_set_prefs window_seen"""
CLASSES = {kind: text.split() for kind, text in (("read", READ), ("tighten", TIGHTEN), ("loosen", LOOSEN), ("ui", UI))}
COUNTS = {"read": 47, "tighten": 10, "loosen": 47, "ui": 46}           # 150 in all (R28; +1 allowed-site words switch; +3 for "Allowed during breaks", +1 site icons switch, +1 front warning steps, +1 window_seen, +3 App log, +5 password / authenticator / recovery code, +1 recovery code removed)
# Loosen methods that are NOT a challenge: the emergency unlock's quota is their limit (BLUEPRINT 4: "not a challenge").
QUOTA = ("emergency_unlock", "overlay_emergency")


def test_every_api_method_is_classified_here_and_the_count_is_known():
    table = {name: kind for kind, names in CLASSES.items() for name in names}
    assert sum(len(n) for n in CLASSES.values()) == len(table)              # (no name twice)
    assert dict(webapi.api_methods()) == table
    assert {k: len(v) for k, v in CLASSES.items()} == COUNTS and len(table) == 150


def test_every_public_function_of_a_webapi_module_is_an_api_method_or_marked_internal():
    import importlib
    for name in webapi.PAGE_MODULES:
        module = importlib.import_module(f"webapi.{name}")
        for fname, fn in inspect.getmembers(module, inspect.isfunction):
            if fname.startswith("_") or fn.__module__ != module.__name__:
                continue
            assert core.registry.has_function(fn) or getattr(fn, "_api_internal", False), f"{name}.{fname}"


def test_every_loosen_method_has_a_row_in_the_matrix():
    assert set(CASES) | set(QUOTA) == set(CLASSES["loosen"])
    assert set(UI_ARGS) == set(CLASSES["ui"]) | set(CLASSES["tighten"])


# ================================================================ a setup with a bit of everything

class FakeOverlay:
    """resident.overlay.Overlay as the Api uses it: every button is on screen; what was accepted is recorded."""

    def __init__(self):
        self.accepted, self.cancelled = [], []

    def allows(self, action):
        return "token"

    def accept(self, action, token):
        self.accepted.append(action)

    def challenge_cancelled(self, token):
        self.cancelled.append(token)

    def dismiss(self):
        return {"done": True}

    def cancel(self):
        return {"back": True}

    def payload(self):
        return {"shown": None}


class FakeFlow:
    """The update flow (resident/update_flow.UpdateFlow) without the network."""
    outcome = None

    def __init__(self):
        self.calls = []

    def check(self):
        self.calls.append("check")
        return True

    def download(self, install_after=False):
        self.calls.append("download")
        return {"state": "idle"}

    def install(self):
        self.calls.append("install")
        return {"state": "idle"}

    def opened(self):
        self.calls.append("opened")

    def status(self):
        return {"state": "idle"}

    def notes(self, version):
        return []


CUSTOM_MODE = {"id": "custom1", "name": "Deep", "builtin": False, "categories": ["distracting"], "items": [],
               "groups": [], "extra": [], "mute": False, "pomodoro": None, "schedule": None}
SLEEP_CFG = {"on": True, "guarded": True, "bedtime": "23:00", "wake": "07:00", "before": "30", "mode": "", "tiers": [],
             "dismiss_wait": 0, "warn_text": "", "text": ""}
BREAK_CFG = {"on": True, "guarded": True, "every": "45 min", "snooze": "5 min", "length": 5, "strict": False,
             "max_snooze": 2, "twenty": False, "text": "", "twenty_text": ""}
CUSTOM_CFG = {"id": "r1", "text": "Stretch", "kind": "interval", "every": "1h", "times": ["12:00"], "days": list(range(7)),
              "window": ["10:00", "18:00"], "hours": False, "snooze": 5, "max_snooze": 3, "check": 0, "per_day": 0,
              "packs": [], "quotes": "", "guarded": True}
LIMIT_1H = {"rule_type": "time_limit", "same": True, "day": "1h"}


def seed(db) -> dict:
    """Blocks, a group, protection lists, words, guarded reminders, an emergency kind, a locked running mode, a custom
    category, a reset time. -> the ids."""
    a = db.add_item("Reddit", ["reddit.com"], "site", "manual", [{"rule_type": "time_limit", "daily_limit_min": 60}])
    b = db.add_item("Game", ["game.exe"], "app", "manual", [{"rule_type": "permanent"}], block_type="close")
    c = db.add_item("YouTube", ["youtube.com"], "site", "manual", [])
    night = rules.make_schedule("block", [(list(range(7)), "22:00", "00:00")])
    d = db.add_item("Chat", ["chat.exe"], "app", "manual", [{"rule_type": "scheduled", "schedule": night}],
                    block_type="close")
    gid = db.add_group("Fun", [{"rule_type": "time_limit", "daily_limit_min": 60}], {a: {}, c: {}, d: {}},
                       {"app_block": "close", "site_block": None, "member_blocks": {}})
    cfg = protection.settings(db)
    cfg["manual"] = [{"key": "mine1", "name": "Mine", "entries": [{"host": "a.com", "name": ""}, {"host": "b.com", "name": ""}]}]
    cfg["custom"] = [{"key": "custom1", "name": "Theirs", "url": "https://example.com/list.txt"}]
    cfg["enabled"] = cfg["enabled"] + ["mine1", "custom1"]
    protection.save_settings(db, cfg)
    keywords.save(db, keywords.settings(db))
    reminders.save(db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "guarded": True})
    reminders.save(db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": True, "guarded": True})
    reminders.save(db, reminders.CUSTOM_KEY, [{**reminders.DEFAULT_CUSTOM, "id": "r1", "text": "Stretch", "guarded": True}])
    db.set_setting("emergency.enabled", "1")
    emergency.save_tiers(db, [{"id": "t1", "uses": 1, "per": "week", "minutes": 5}])
    modes.save(db, [dict(CUSTOM_MODE)])
    modes.start(db, "custom1", NOW - timedelta(minutes=10), NOW + timedelta(hours=2), locked=True)
    key = categories._add(db, "Mine", "#123456")
    db.set_setting(rules.RESET_KEY, rules.change_reset(None, "04:00", NOW))
    return {"a": a, "b": b, "c": c, "d": d, "gid": gid, "cat": key}


class Env:
    """A seeded database, the real Api over it, a GuardService whose typing clock the test moves. `state`: "locked"
    (a 30-character phrase), "free" (the same, but a passed challenge is unlocked now) or "off" (no challenge turned on)."""

    def __init__(self, tmp_path, state="locked", monkeypatch=None):
        self.path = tmp_path / "t.db"
        self.db = Database(self.path)
        self.ids = seed(self.db)
        self.mono = [1000.0]
        self.ctx = webapi.make_context(self.path, clock=lambda db=None: NOW)
        self.ctx.guard = GuardService(lambda: self.ctx.db, lambda db=None: NOW, monotonic=lambda: self.mono[0],
                                      rng=random.Random(5))
        self.ctx.overlay = FakeOverlay()
        self.ctx.update_flow = FakeFlow()
        self.api = core.build_api(self.ctx)
        if state != "off":
            ab.save(self.db, {**ab.settings(self.db), "phrase": True, "length": 60})
        if state == "free":
            ab.unlock(self.db, NOW)
        if monkeypatch is not None:
            backup_file = tmp_path / "empty-backup.json"
            empty = Database(tmp_path / "empty.db")
            backup.save(empty, str(backup_file))
            empty.close()
            monkeypatch.setattr(settings_api, "_ask_open_path", lambda window: str(backup_file))
            monkeypatch.setattr(settings_api, "_ask_save_path", lambda window, name: str(tmp_path / "out.json"))
            from webapi import network, screentime
            monkeypatch.setattr(network, "_ask_save_path", lambda ctx: None)
            monkeypatch.setattr(screentime, "_ask_save_path", lambda window, name: None)
            monkeypatch.setattr(about, "_opener", lambda path: None)
            monkeypatch.setattr(about, "_browser", lambda url: None)
            monkeypatch.setattr(categories, "_open_explorer", lambda path: None)
            monkeypatch.setattr(protection, "preview", lambda url, limit=0: (1, ["x.com"]))

    def call(self, name, *args):
        return getattr(self.api, name)(*args)

    def pass_challenge(self, ticket):
        begin = self.ctx.guard.begin(ticket)
        assert begin["ok"], begin
        d = begin["data"]
        typed = d["grid_words"] if d["mode"] == "grid" else d["phrase"]
        self.mono[0] += 120
        return self.ctx.guard.submit(ticket, typed)

    def fingerprint(self, ignore=()):
        """Every row of every table (a fresh connection: what is committed), plus what the fake overlay accepted."""
        con = sqlite3.connect(self.path)
        try:
            out = []
            for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
                if table == "app_events":      # (the App log only tells what happened - a passed challenge adds a line)
                    continue
                rows = con.execute(f'SELECT * FROM "{table}"').fetchall()
                if table == "settings":
                    rows = [r for r in rows if r[0] not in ignore]
                out.append((table, sorted(map(repr, rows))))
        finally:
            con.close()
        return out + [("overlay", list(self.ctx.overlay.accepted))]


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def ticket_of(reply):
    d = data(reply)
    assert isinstance(d, dict) and d.get("needs_challenge") is True and d["ticket"], d
    return d["ticket"]


def asks(reply) -> bool:
    return bool(reply.get("ok") and isinstance(reply.get("data"), dict) and reply["data"].get("needs_challenge"))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")
    monkeypatch.setattr("gui.draft.now_from_db", lambda db: NOW)            # (the Draft's trusted clock)
    monkeypatch.setattr(settings_api, "_clock", lambda: 5000.0)
    from resident import update_flow
    monkeypatch.setattr(update_flow, "current", None)


# ================================================================ the loosen rows

def _pending_removal(env):
    """Auto-save off and the Game item removed in the draft (not saved yet)."""
    d = drafts.draft_of(env.ctx)
    with drafts.lock:
        d.autosave = False
        d.remove_item(env.ids["b"])


def _import_token(env):
    env.token = data(env.call("backup_import_pick"))["token"]


def _settings(env, key, default=None):
    con = sqlite3.connect(env.path)
    try:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    finally:
        con.close()
    return row[0] if row else default


def _fresh(env):
    return Database(env.path)


def _prot(env):
    return protection.settings(_fresh(env))


def _rem(env, key, default):
    return reminders.load(_fresh(env), key, default)


def _cats(env):
    return {c["key"]: c for c in categories.load(_fresh(env))}


class Case:
    """One loosen method: `args(env)` a loosening input; `applied(env, result)` - did the change happen;
    `setup(env)` before the fingerprint; `ignore` settings keys the call may write without loosening anything."""

    def __init__(self, args, applied, setup=None, ignore=()):
        self.args, self.applied, self.setup, self.ignore = args, applied, setup, ignore


def _allow_example(env):
    cfg = protection.settings(env.db)
    cfg["allowed"] = ["example.com"]
    protection.save_settings(env.db, cfg)


def _password_set(env):
    cs.set_password(env.db, "old password", NOW)


def _totp_pending(env):
    """An authenticator is set up, and a new one is begun: replacing it is the loosening."""
    cs.set_totp(env.db, cs.new_totp_secret(), 0, NOW)
    begin = data(env.call("antibypass_totp_begin"))
    env.totp = {"token": begin["token"], "secret": begin["key"].replace(" ", "")}


def _totp_code(env):
    return cs.totp(cs.secret_key(env.totp["secret"]), cs.trusted_epoch(env.db))


CASES = {
    "antibypass_save": Case(lambda e: [{"length": 30}], lambda e, r: ab.settings(_fresh(e))["length"] == 30),
    "antibypass_password_set": Case(lambda e: ["new password", "new password"],
                                    lambda e, r: cs.verify_secret("new password", cs.load(_fresh(e))["password"]),
                                    _password_set),
    "antibypass_secret_remove": Case(lambda e: ["password"], lambda e, r: not cs.ready(_fresh(e)), _password_set),
    "antibypass_totp_confirm": Case(lambda e: [e.totp["token"], _totp_code(e)],
                                    lambda e, r: cs.load(_fresh(e))["totp"]["secret"] == e.totp["secret"],
                                    _totp_pending),
    "antibypass_recovery_new": Case(lambda e: [], lambda e, r: bool(r.get("recovery_code")), _password_set),
    "antibypass_try": Case(lambda e: [], lambda e, r: "banner" in r),                 # (a dry run: see its own test)
    "antibypass_unlock_to_edit": Case(lambda e: [], lambda e, r: "banner" in r),      # (passing it IS the unlock)
    "backup_import_apply": Case(lambda e: [e.token], lambda e, r: _fresh(e).list_items() == [], _import_token),
    "break_allow_add": Case(lambda e: ["discord.exe"],
                            lambda e, r: "discord.exe" in reminders.allowed_apps(_rem(e, reminders.BREAK_KEY,
                                                                                      reminders.DEFAULT_BREAK))),
    "break_allow_restore": Case(lambda e: [],
                                lambda e, r: "lockdown.exe" in reminders.allowed_apps(_rem(e, reminders.BREAK_KEY,
                                                                                           reminders.DEFAULT_BREAK)),
                                lambda e: reminders.save(e.db, reminders.BREAK_KEY,
                                                         {**reminders.DEFAULT_BREAK, "allow": ["chrome.exe"]})),
    "category_add": Case(lambda e: ["Mine2", "#112233"],
                         lambda e, r: "Mine2" in {c["name"] for c in _cats(e).values()}),
    "category_add_and_assign": Case(lambda e: ["app", "chrome.exe", "Mine3", "#112233"],
                                    lambda e, r: ("app", "chrome.exe") in _fresh(e).categories()),
    "category_assign": Case(lambda e: ["app", "chrome.exe", "games"],
                            lambda e, r: _fresh(e).categories().get(("app", "chrome.exe")) == "games"),
    "category_remove": Case(lambda e: [e.ids["cat"]], lambda e, r: e.ids["cat"] not in _cats(e)),
    "draft_remove_group": Case(lambda e: [e.ids["gid"]], lambda e, r: e.ids["gid"] not in _fresh(e).group_ids()),
    "draft_remove_item": Case(lambda e: [e.ids["b"]], lambda e, r: e.ids["b"] not in _fresh(e).item_ids()),
    "draft_save": Case(lambda e: [], lambda e, r: e.ids["b"] not in _fresh(e).item_ids(), _pending_removal),
    "draft_set_autosave": Case(lambda e: [True], lambda e, r: e.ids["b"] not in _fresh(e).item_ids(), _pending_removal,
                               ignore=("ui.autosave",)),
    "draft_set_disabled": Case(lambda e: [e.ids["b"], True],
                               lambda e, r: next(i for i in _fresh(e).list_items() if i["id"] == e.ids["b"])["disabled"]),
    "draft_set_group": Case(lambda e: [e.ids["gid"], "Fun", [LIMIT_1H], [{"id": e.ids["a"]}, {"id": e.ids["d"]}],
                                       {"app_block": "close", "site_block": None}],
                            lambda e, r: set(_fresh(e).list_groups()[0]["members"]) == {e.ids["a"], e.ids["d"]}),
    "draft_set_group_disabled": Case(lambda e: [e.ids["gid"], True],
                                     lambda e, r: _fresh(e).list_groups()[0]["disabled"]),
    "draft_submit_item": Case(lambda e: [{"mode": "edit", "id": e.ids["a"], "rules": [{**LIMIT_1H, "day": "2h"}]}],
                              lambda e, r: next(i for i in _fresh(e).list_items() if i["id"] == e.ids["a"])["rules"][0]
                              ["daily_limit_min"] == 120),
    "emergency_settings_save": Case(lambda e: [{"enabled": True, "tiers": [{"id": "t1", "uses": 5, "per": "week",
                                                                            "minutes": 5}]}],
                                    lambda e, r: emergency.tiers(_fresh(e))[0]["uses"] == 5),
    "lockdown_switch_off": Case(lambda e: [], lambda e, r: ab.is_off(_fresh(e))),
    "manual_remove": Case(lambda e: ["mine1"], lambda e, r: not _prot(e)["manual"]),
    "manual_save_entries": Case(lambda e: ["mine1", [["a.com", ""]]],
                                lambda e, r: [x["host"] for x in _prot(e)["manual"][0]["entries"]] == ["a.com"]),
    "manual_set_enabled": Case(lambda e: ["mine1", False], lambda e, r: "mine1" not in _prot(e)["enabled"]),
    "mode_delete": Case(lambda e: ["custom1"], lambda e, r: "custom1" not in {m["id"] for m in modes.load(_fresh(e))}),
    "mode_save": Case(lambda e: [{"id": "custom1", "name": "Deep", "categories": [], "items": [], "groups": [], "extra": [],
                                  "mute": False, "pomodoro": None, "windows": None}],
                      lambda e, r: modes.load(_fresh(e))[-1]["categories"] == []),
    "mode_start": Case(lambda e: ["work"], lambda e, r: modes.active(_fresh(e), NOW)["mode"]["id"] == "work"),
    "mode_stop": Case(lambda e: [], lambda e, r: modes.active(_fresh(e), NOW) is None),
    "overlay_answer": Case(lambda e: ["off_tonight"], lambda e, r: e.ctx.overlay.accepted == ["off_tonight"]),
    "overlay_disable": Case(lambda e: [], lambda e, r: r == {"choose": True}),
    "pause_start": Case(lambda e: [60, False], lambda e, r: pause.state(_fresh(e), NOW) is not None),
    "protection_allow": Case(lambda e: ["example.com"], lambda e, r: "example.com" in _prot(e)["allowed"]),
    "protection_allow_words": Case(lambda e: ["example.com", True], lambda e, r: "example.com" in _prot(e)["allowed_words"],
                                   _allow_example),
    "protection_remove_custom": Case(lambda e: ["custom1"], lambda e, r: not _prot(e)["custom"]),
    "protection_set_list_enabled": Case(lambda e: ["scam", False], lambda e, r: "scam" not in _prot(e)["enabled"]),
    "reminder_delete": Case(lambda e: ["r1"], lambda e, r: _rem(e, reminders.CUSTOM_KEY, []) == []),
    "reminder_save_break": Case(lambda e: [{**BREAK_CFG, "guarded": False}],
                                lambda e, r: not _rem(e, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)["guarded"]),
    "reminder_save_custom": Case(lambda e: [{**CUSTOM_CFG, "guarded": False}],
                                 lambda e, r: not _rem(e, reminders.CUSTOM_KEY, [])[0]["guarded"]),
    "reminder_save_sleep": Case(lambda e: [{**SLEEP_CFG, "guarded": False}],
                                lambda e, r: not _rem(e, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)["guarded"]),
    "reminder_set_on": Case(lambda e: ["sleep", False],
                            lambda e, r: not _rem(e, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)["on"]),
    "reset_change_apply": Case(lambda e: ["02:00", True], lambda e, r: _fresh(e).limit_clock().time.hour == 2),
    "words_update": Case(lambda e: [{"enabled": False}], lambda e, r: not keywords.settings(_fresh(e))["enabled"]),
}
NO_CHANGE = ("antibypass_try", "antibypass_unlock_to_edit", "overlay_disable")     # nothing to apply but the challenge


def _env(tmp_path, monkeypatch, state, name):
    env = Env(tmp_path, state, monkeypatch)
    case = CASES[name]
    if case.setup:
        case.setup(env)
    return env, case


def _result(reply):
    """What the method answered, through a passed challenge ({passed, result}) or at once (the draft wraps its own)."""
    d = data(reply)
    if isinstance(d, dict) and "passed" in d:
        d = d["result"]
    if isinstance(d, dict) and "result" in d and "dirty" in d:
        d = d["result"]
    return d


@pytest.mark.parametrize("name", sorted(CASES))
def test_locked_a_loosening_gives_a_ticket_and_changes_nothing_until_the_challenge(tmp_path, monkeypatch, name):
    env, case = _env(tmp_path, monkeypatch, "locked", name)
    before = env.fingerprint(case.ignore)
    ticket = ticket_of(env.call(name, *case.args(env)))
    assert env.fingerprint(case.ignore) == before, f"{name} changed something before the challenge"
    begin = data(env.call("challenge_begin", ticket))
    assert begin["mode"] == "phrase" and begin["changes"] and all(isinstance(c, str) and c for c in begin["changes"])
    passed = env.pass_challenge(ticket)
    assert data(passed)["passed"]
    if name not in NO_CHANGE:
        assert case.applied(env, _result(passed)), f"{name}: passing the challenge did not apply it"
    assert not env.call("challenge_submit", ticket, begin["phrase"])["ok"]                 # single use


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_cancelled_challenge_leaves_everything_as_it_was(tmp_path, monkeypatch, name):
    env, case = _env(tmp_path, monkeypatch, "locked", name)
    before = env.fingerprint(case.ignore)
    ticket = ticket_of(env.call(name, *case.args(env)))
    data(env.call("challenge_begin", ticket))
    data(env.call("challenge_cancel", ticket))
    assert env.fingerprint(case.ignore) == before
    assert not env.call("challenge_submit", ticket, "anything")["ok"]


@pytest.mark.parametrize("name", sorted(set(CASES) - {"antibypass_try"}))
def test_free_the_same_loosening_applies_at_once(tmp_path, monkeypatch, name):
    env, case = _env(tmp_path, monkeypatch, "free", name)
    reply = env.call(name, *case.args(env))
    assert not asks(reply), f"{name} asked while unlocked"
    assert case.applied(env, _result(reply)), name


def _forged_calls(name, args):
    """The loosening input with forged extras: every optional argument filled with True, one argument too many, and
    every dict argument carrying the flags a page might hope Python trusts."""
    fn = core.registry.get(name)[0]
    params = list(inspect.signature(fn).parameters.values())[1:]
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    out = [args + [True] * k for k in range(1, len(positional) - len(args) + 2)]
    forged = {"confirmed": True, "force": True, "free": True, "minutes": 999, "passed": True, "unlocked": True}
    for i, a in enumerate(args):
        if isinstance(a, dict):
            out.append(args[:i] + [{**a, **forged}] + args[i + 1:])
    return out


@pytest.mark.parametrize("name", sorted(CASES))
def test_forged_client_fields_never_skip_the_challenge(tmp_path, monkeypatch, name):
    env, case = _env(tmp_path, monkeypatch, "locked", name)
    before = env.fingerprint(case.ignore)
    for args in _forged_calls(name, case.args(env)):
        if name == "backup_import_apply":
            _import_token(env)
            args = [env.token] + args[1:]
        reply = env.call(name, *args)
        assert not reply["ok"] or asks(reply), f"{name}{args!r} ran without the challenge: {reply}"
        if asks(reply):
            env.call("challenge_cancel", reply["data"]["ticket"])
        assert env.fingerprint(case.ignore) == before, f"{name}{args!r} changed something"


def test_try_the_challenge_is_a_dry_run_whatever_the_state(tmp_path, monkeypatch):
    for state in ("off", "locked", "free"):
        (tmp_path / state).mkdir()
        env = Env(tmp_path / state, state, monkeypatch)
        before = env.fingerprint()
        out = data(env.pass_challenge(ticket_of(env.call("antibypass_try"))))
        assert out["passed"] and out["dry"] and "waiting_until" not in out
        assert env.fingerprint() == before, state                  # no unlock opened, no wait started


def test_unlock_to_edit_only_opens_the_unlock(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    before = env.fingerprint(ignore=("antibypass",))
    data(env.pass_challenge(ticket_of(env.call("antibypass_unlock_to_edit"))))
    assert ab.status(ab.settings(_fresh(env)), NOW) == "free"
    assert env.fingerprint(ignore=("antibypass",)) == before
    cfg = {k: v for k, v in ab.settings(_fresh(env)).items() if not k.startswith("unlocked")}
    assert cfg == {k: v for k, v in ab.settings(env.db).items() if not k.startswith("unlocked")}


# ---------------------------------------------------------------- the quota rows (not a challenge)

def test_emergency_unlock_is_its_quota_not_the_challenge_and_minutes_come_from_the_kind(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    a, b = env.ids["a"], env.ids["b"]
    assert "Permanently" in env.call("emergency_unlock", [b], "t1")["error"]           # a permanent block: never
    assert not env.call("emergency_unlock", [9999], "t1")["ok"]                          # not an item
    assert not env.call("emergency_unlock", [a], "made-up")["ok"]                        # not a kind
    assert not env.call("emergency_unlock", [a], "t1", "yes")["ok"]                      # a forged flag
    assert not env.call("emergency_unlock", [a], "t1", False, 999)["ok"]                 # forged minutes: bad arguments
    got = data(env.call("emergency_unlock", [a], "t1"))
    assert got["until"] == (NOW + timedelta(minutes=5)).isoformat(timespec="seconds")   # the kind's 5 minutes
    assert "No" in env.call("emergency_unlock", [a], "t1")["error"]                      # 1 a week: spent
    env.db.set_setting("emergency.enabled", "0")
    assert not env.call("emergency_unlock", [a], "t1")["ok"]


def test_the_cover_emergency_spends_only_a_kind_that_has_a_use_left(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    assert not env.call("overlay_emergency", "made-up")["ok"]
    assert not env.call("overlay_emergency", "t1", 999)["ok"]                            # minutes can't be sent
    assert data(env.call("overlay_emergency", "t1"))["minutes"] == 5
    assert env.ctx.overlay.accepted == ["emergency:t1"]
    env.db.add_unlock([], [], NOW, NOW + timedelta(minutes=5), alerts=True, tier="t1")   # (the engine spends it)
    assert "No 5-min" in env.call("overlay_emergency", "t1")["error"]
    env.db.set_setting("emergency.enabled", "0")
    assert not env.call("overlay_emergency", "t1")["ok"]


# ================================================================ reads change nothing

@pytest.fixture(scope="module")
def synth_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("synth") / "config.db"
    Database(path)
    info = synthdata.build(path, NOW.date(), days=10, network_rows=300)
    db = Database(path)
    seed(db)
    db.close()
    return path, info


READS = sorted(n for n in CLASSES["read"] if n not in selftest.SKIP_READS)


@pytest.mark.parametrize("name", READS)
def test_a_read_method_writes_nothing(synth_db, tmp_path, name):
    import shutil
    path = tmp_path / "config.db"
    shutil.copy(synth_db[0], path)
    ctx = webapi.make_context(path, clock=lambda db=None: NOW)
    ctx.update_flow = FakeFlow()
    ctx.overlay = FakeOverlay()
    watcher = sqlite3.connect(path)
    version = watcher.execute("PRAGMA data_version").fetchone()[0]
    env = SimpleNamespace(path=path, ctx=ctx)
    before = Env.fingerprint(env)
    fn = core.registry.get(name)[0]
    reply = core.dispatch(ctx, fn, selftest.read_args(name, fn, ctx))
    assert reply["ok"] or reply.get("error"), reply
    ctx.db.flush()
    assert ctx.db.conn.total_changes == 0, f"{name} wrote to the database"
    assert watcher.execute("PRAGMA data_version").fetchone()[0] == version, f"{name} committed something"
    assert Env.fingerprint(env) == before
    watcher.close()


def test_the_dialog_read_is_skipped_for_a_reason():
    assert set(selftest.SKIP_READS) == {"pick_program_file"}


# ================================================================ tighten / ui can't express a loosening

def _blocking_state(env) -> list:
    """Everything that decides what is blocked: the blocklist (alerts per item aside), and every setting that holds a
    block, a protection or the challenge - cosmetic fields left out."""
    con = sqlite3.connect(env.path)
    try:
        items = con.execute("SELECT id, display_name, target, item_type, block_type, app_path, COALESCE(disabled, 0) FROM blocked_items "
                            "ORDER BY id").fetchall()
        cols = ", ".join(RULE_COLUMNS)                  # (a rewritten rule gets a new row id: compare what it says)
        tables = [sorted(map(repr, con.execute(f"SELECT item_id, {cols} FROM block_rules").fetchall())),
                  sorted(map(repr, con.execute(f"SELECT group_id, {cols} FROM group_rules").fetchall()))]
        tables += [sorted(map(repr, con.execute(f'SELECT * FROM "{t}"').fetchall()))
                   for t in ("block_groups", "group_members", "categories", "emergency_unlocks")]
        settings = dict(con.execute("SELECT key, value FROM settings").fetchall())
    finally:
        con.close()
    prot = json.loads(settings.get(protection.SETTINGS_KEY) or "{}")
    for cosmetic in ("info", "update_now", "auto", "every_hours"):          # (BLUEPRINT 4: update settings ungated)
        prot.pop(cosmetic, None)
    cats = json.loads(settings.get(categories.KEY) or "{}")
    cats.pop("colors", None)                                                # (recolouring: ungated)
    held = [settings.get(k) for k in (ab.SETTINGS_KEY, ab.OFF_KEY, keywords.SETTINGS_KEY, modes.LIST_KEY,
                                      modes.ACTIVE_KEY, pause.KEY, reminders.SLEEP_KEY, reminders.BREAK_KEY,
                                      reminders.CUSTOM_KEY, rules.RESET_KEY, "emergency.enabled", emergency.TIERS_KEY,
                                      drafts.AUTOSAVE_KEY if hasattr(drafts, "AUTOSAVE_KEY") else "ui.autosave")]
    return [items, tables, prot, cats, held]


UI_ARGS = {
    "antibypass_totp_begin": [],                        # (keeps a new secret in memory only: nothing is stored)
    # tighten
    "break_allow_remove": ["systemsettings.exe"],
    "antibypass_lock": [], "antibypass_switch_on": [], "pause_resume": [], "service_start": [],
    "antibypass_recovery_remove": [],                   # (deleting the recovery code: one way past the challenge less)
    "draft_add_to_group": lambda e: [{"kind": "site", "name": "news.com"}, e.ids["gid"]],
    "manual_create": ["Another"], "protection_unallow": ["example.com"],
    "protection_connect": ["X", "https://example.com/l.txt", "not-a-token"],
    # ui
    "backup_export": [], "backup_import_pick": [], "blocking_set_sort": ["Name"],
    "category_set_color": ["games", "#112233"], "challenge_begin": ["nope"], "challenge_cancel": ["nope"],
    "challenge_submit": ["nope", "x"], "corner_answer": ["k", "dismiss"], "corner_ready": [], "corner_size": [100, 1],
    "digest_set": [True, 0, "19:00"], "display_set": [["limits"], []], "draft_discard": [],
    "draft_set_notify": lambda e: [e.ids["b"], "off"], "draft_set_setting": ["notify.format", "{name}"],
    "log_client_error": ["boom"], "network_export_csv": [{}], "network_live_stop": [], "notify_front_steps_set": ["5, 1"],
    "notify_repeat_set": ["30s"],
    "notify_test": [], "open_path": ["data"], "open_url": ["https://evil.example"], "overlay_ready": [],
    "protection_preview": ["https://example.com/l.txt"], "protection_set_updates": [False, 168],
    "protection_update_now": [], "reveal_in_explorer": ["C:\\Windows\\notepad.exe"], "screentime_export_csv": [],
    "set_accent": ["#112233"], "set_appearance": ["light"], "set_goal": [3], "set_icons_download": [False], "set_scale": ["80"],
    "suggest_clear_history": [], "page_ready": ["main"], "ui_ready": [], "ui_setting": ["dash.cards", "x"],
    "update_check": [], "update_download": [], "update_install": [], "update_opened": [], "update_set_prefs": [False, False],
    "window_seen": [],
    "applog_save": [],                                  # (no Save dialog in a test: it answers an error and changes nothing)
}
# What a tighten method may change, checked to be stricter (state before, state after).
def _break_without_allow(db):
    return {k: v for k, v in reminders.load(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK).items() if k != "allow"}


def _apps(db):
    return set(reminders.allowed_apps(reminders.load(db, reminders.BREAK_KEY, reminders.DEFAULT_BREAK)))


STRICTER = {
    "break_allow_remove": lambda b, a: (_apps(a) < _apps(b) and _break_without_allow(a) == _break_without_allow(b)),
    "antibypass_lock": lambda b, a: not ab.unlocked_until(ab.settings(a), NOW),
    "antibypass_switch_on": lambda b, a: not ab.is_off(a),
    "pause_resume": lambda b, a: pause.state(a, NOW) is None,
    "draft_add_to_group": lambda b, a: set(b.list_groups()[0]["members"]) <= set(a.list_groups()[0]["members"]),
    "manual_create": lambda b, a: set(protection.settings(b)["enabled"]) <= set(protection.settings(a)["enabled"]),
}


@pytest.mark.parametrize("name", sorted(UI_ARGS))
def test_a_tighten_or_ui_method_never_loosens_anything(tmp_path, monkeypatch, name):
    env = Env(tmp_path, "locked", monkeypatch)
    pause.start(env.db, NOW, 60, False)                     # (something for "resume" to end)
    if name == "antibypass_lock":
        ab.unlock(env.db, NOW)
    if name == "antibypass_switch_on":
        ab.switch_off(env.db, NOW)
    if name in ("protection_unallow",):
        cfg = protection.settings(env.db)
        cfg["allowed"] = ["example.com"]
        protection.save_settings(env.db, cfg)
    snapshot = tmp_path / "before.db"
    con = sqlite3.connect(env.path)
    con.execute(f"VACUUM INTO '{snapshot}'")
    con.close()
    before = _blocking_state(env)
    spec = UI_ARGS[name]
    args = spec(env) if callable(spec) else spec
    reply = env.call(name, *args)
    assert not asks(reply), name
    after = _blocking_state(env)
    if name in STRICTER:
        assert STRICTER[name](Database(snapshot), _fresh(env)), name
    elif name == "protection_unallow":
        assert "example.com" not in _prot(env)["allowed"] and after[2]["allowed"] == []
    elif name not in ("service_start", "protection_connect"):
        assert after == before, f"{name} changed what is blocked"


def test_ui_setting_takes_only_cosmetic_keys(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    before = env.fingerprint()
    for key in ("antibypass", "app.off", "protection", "words", "modes.active", "pause.blocks", "ui.autosave",
                "limits.reset", "emergency.enabled", "ui.engine", "dash.x\n", "dash.*", "../dash", "", 5, None):
        assert not env.call("ui_setting", key, "1")["ok"], key
    assert env.fingerprint() == before
    assert core.UI_SETTING_PATTERNS == ["dash.*", "ui.blocking.sort", "ui.reduce_motion"]


def test_draft_set_notify_and_set_setting_cannot_disable_or_remove_anything(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    before = _blocking_state(env)
    for bad in ("disabled", True, 1, {"disabled": True}, "remove"):
        assert not env.call("draft_set_notify", env.ids["b"], bad)["ok"]
    for key, value in (("ui.autosave", "0"), ("antibypass", "{}"), ("notify.format", "evil"),
                       ("notify.cooldown_min", "0"), ("notify.enabled", "maybe")):
        assert not env.call("draft_set_setting", key, value)["ok"], key
    data(env.call("draft_set_notify", env.ids["b"], "off"))
    assert _blocking_state(env) == before


def test_protection_unallow_only_takes_a_site_off_allowed(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    cfg = protection.settings(env.db)
    cfg["allowed"] = ["example.com", "other.com"]
    protection.save_settings(env.db, cfg)
    before = _prot(env)
    data(env.call("protection_unallow", "example.com"))
    after = _prot(env)
    assert after["allowed"] == ["other.com"]
    assert {k: v for k, v in after.items() if k != "allowed"} == {k: v for k, v in before.items() if k != "allowed"}


# ================================================================ BLUEPRINT section 4 + CHANGELOG rules, row by row

def _schedule_form(start, end, days=tuple(range(7))):
    return {"rule_type": "scheduled", "mode": "block", "allowance": "0",
            "windows": [{"days": list(days), "start": start, "end": end}]}


def _edit_chat(env, form):
    return env.call("draft_submit_item", {"mode": "edit", "id": env.ids["d"], "rules": [form]})


ROWS = [
    # (row, call(env) -> reply, asks the challenge?)
    ("hours extended later across midnight: free", lambda e: _edit_chat(e, _schedule_form("22:00", "01:00")), False),
    ("hours on more days / earlier start: free", lambda e: _edit_chat(e, _schedule_form("21:00", "00:00")), False),
    ("hours shortened: challenge", lambda e: _edit_chat(e, _schedule_form("22:00", "23:00")), True),
    ("hours on fewer days: challenge", lambda e: _edit_chat(e, _schedule_form("22:00", "00:00", range(6))), True),
    ("temporary block shortened by a minute: challenge (no grace)",
     lambda e: _submit_temporary(e, 59), True),
    ("temporary block kept: free", lambda e: _submit_temporary(e, "keep"), False),
    ("temporary block lengthened: free", lambda e: _submit_temporary(e, 61), False),
    ("member app's own way lighter than the group's: challenge",
     lambda e: _group_way(e, "minimize"), True),
    ("member app's own way stricter: free", lambda e: _group_way(e, "close,internet"), False),
    ("emergency: more uses: challenge", lambda e: _kinds(e, uses=2), True),
    ("emergency: longer: challenge", lambda e: _kinds(e, minutes=10), True),
    ("emergency: a new kind: challenge", lambda e: _kinds(e, extra=True), True),
    ("emergency: per week -> per month: challenge", lambda e: _kinds(e, per="month"), True),
    ("emergency: per week -> per day: challenge (more often)", lambda e: _kinds(e, per="day"), True),
    ("emergency: fewer / shorter / removed / off: free", lambda e: _kinds(e, tiers=[], enabled=False), False),
    ("phrase shorter: challenge", lambda e: e.call("antibypass_save", {"length": 30}), True),
    ("phrase longer: free", lambda e: e.call("antibypass_save", {"length": 120}, True), False),
    ("wait shorter: challenge", lambda e: _wait_then(e, 30, "10"), True),
    ("pause start: challenge", lambda e: e.call("pause_start", 30, False), True),
    ("pause resume: free", lambda e: e.call("pause_resume"), False),
    ("locked mode stop: challenge", lambda e: e.call("mode_stop"), True),
    ("locked mode delete: challenge", lambda e: e.call("mode_delete", "custom1"), True),
    ("locked mode save, blocks more: free",
     lambda e: e.call("mode_save", {**_mode_page(), "categories": ["distracting", "games"]}), False),
    ("scheduled mode: Pomodoro added: challenge", lambda e: _scheduled_pomodoro(e, {"work": 25, "break": 5, "rounds": 4,
                                                                                  "long": 15}), True),
    ("scheduled mode: schedule shortened: challenge", lambda e: _scheduled_pomodoro(e, None, "19:00"), True),
    ("guarded reminder off: challenge", lambda e: e.call("reminder_set_on", "break", False), True),
    ("guarded reminder un-flagged: challenge", lambda e: e.call("reminder_save_sleep", {**SLEEP_CFG, "guarded": False}), True),
    ("guarded reminder deleted: challenge", lambda e: e.call("reminder_delete", "r1"), True),
    ("plain reminder off: free", lambda e: _plain_reminder_off(e), False),
    ("bedtime Dismiss wait shorter: challenge", lambda e: _dismiss_wait(e), True),
    ("cover off tonight: challenge", lambda e: e.call("overlay_answer", "off_tonight"), True),
    ("cover snooze 15: challenge", lambda e: e.call("overlay_answer", "snooze:15"), True),
    ("cover Disable alerts: challenge", lambda e: e.call("overlay_disable"), True),
    ("cover Dismiss: free", lambda e: e.call("overlay_answer", "dismiss"), False),
    ("import: always the challenge", lambda e: (_import_token(e), e.call("backup_import_apply", e.token))[1], True),
    ("reset earlier with Start now: challenge", lambda e: e.call("reset_change_apply", "02:00", True), True),
    ("reset earlier from the next day: free", lambda e: e.call("reset_change_apply", "02:00", False), False),
    ("reset later: free", lambda e: e.call("reset_change_apply", "06:00", True), False),
    ("Lockdown off: challenge", lambda e: e.call("lockdown_switch_off"), True),
    ("Lockdown back on: free", lambda e: e.call("antibypass_switch_on"), False),
    ("words removed: challenge", lambda e: _words(e), True),
    ("exceptions switched back on: challenge", lambda e: _exceptions_back_on(e), True),
    ("category assign (either way): challenge", lambda e: e.call("category_assign", "app", "x.exe", "productive"), True),
    ("site allowed anyway: challenge", lambda e: e.call("protection_allow", "example.com"), True),
    ("protection list switched on: free", lambda e: _list_back_on(e), False),
]


def _submit_temporary(env, minutes):
    """Reddit gets a running temporary block of 60 min; then the editor saves `minutes` (or keep)."""
    end = (NOW + timedelta(minutes=60)).strftime(rules.TIME_FMT)
    env.db.update_item(env.ids["a"], "Reddit", ["reddit.com"], None, [{"rule_type": "temporary", "temp_until": end}])
    drafts.draft_of(env.ctx).reload()
    form = ({"rule_type": "temporary", "choice": "keep", "keep_until": "2026-10-11 15:43:00"} if minutes == "keep" else
            {"rule_type": "temporary", "choice": "custom", "unit": "minutes", "amount": str(minutes)})
    return env.call("draft_submit_item", {"mode": "edit", "id": env.ids["a"], "rules": [form]})


def _group_way(env, way):
    members = [{"id": env.ids["a"]}, {"id": env.ids["c"]}, {"id": env.ids["d"], "way": way}]
    return env.call("draft_set_group", env.ids["gid"], "Fun", [LIMIT_1H], members, {"app_block": "close", "site_block": None})


def _kinds(env, uses=1, minutes=5, per="week", extra=False, tiers=None, enabled=True):
    t = [{"id": "t1", "uses": uses, "per": per, "minutes": minutes}] if tiers is None else tiers
    if extra:
        t.append({"uses": 1, "per": "month", "minutes": 5})
    return env.call("emergency_settings_save", {"enabled": enabled, "tiers": t})


def _wait_then(env, wait, new):
    ab.save(env.db, {**ab.settings(env.db), "wait_min": wait})
    return env.call("antibypass_save", {"wait": new}, True)


def _mode_page():
    return {"id": "custom1", "name": "Deep", "categories": ["distracting"], "items": [], "groups": [], "extra": [],
            "mute": False, "pomodoro": None, "windows": None}


def _scheduled_pomodoro(env, pomodoro, end="21:00"):
    """A mode of your own that turns on by a schedule (Sun 18:00-21:00, not running now); then the editor saves it."""
    page = {**_mode_page(), "id": "", "name": "Evening", "windows": [{"days": [6], "start": "18:00", "end": "21:00"}]}
    ab.unlock(env.db, NOW)                                            # (made while unlocked: a new mode is free anyway)
    mid = data(env.call("mode_save", page))["saved_id"]
    ab.lock(env.db)
    return env.call("mode_save", {**page, "id": mid, "pomodoro": pomodoro,
                                  "windows": [{"days": [6], "start": "18:00", "end": end}]})


def _plain_reminder_off(env):
    reminders.save(env.db, reminders.BREAK_KEY, {**reminders.DEFAULT_BREAK, "on": True, "guarded": False})
    return env.call("reminder_set_on", "break", False)


def _dismiss_wait(env):
    reminders.save(env.db, reminders.SLEEP_KEY, {**reminders.DEFAULT_SLEEP, "on": True, "guarded": False,
                                                 "dismiss_wait": 30})
    return env.call("reminder_save_sleep", {**SLEEP_CFG, "guarded": False, "dismiss_wait": 10})


def _words(env):
    keywords.save(env.db, {**keywords.settings(env.db), "words": ["casino", "poker"]})
    return env.call("words_update", {"words": ["casino"]})


def _exceptions_back_on(env):
    keywords.save(env.db, {**keywords.settings(env.db), "exceptions_on": False, "exceptions": ["wiki.org"]})
    return env.call("words_update", {"exceptions_on": True})


def _list_back_on(env):
    cfg = protection.settings(env.db)
    cfg["enabled"] = [k for k in cfg["enabled"] if k != "gambling"]
    protection.save_settings(env.db, cfg)
    return env.call("protection_set_list_enabled", "gambling", True)


@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
def test_blueprint_and_changelog_rules(tmp_path, monkeypatch, row):
    _name, run, challenge = row
    env = Env(tmp_path, "locked", monkeypatch)
    reply = run(env)
    assert reply["ok"], reply
    assert asks(reply) is challenge, reply
    if not challenge and isinstance(reply["data"], dict) and isinstance(reply["data"].get("result"), dict):
        assert reply["data"]["result"].get("result") != "invalid", reply


def test_a_day_outside_monday_to_sunday_is_refused_not_saved(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    before = env.fingerprint()
    got = data(_edit_chat(env, _schedule_form("22:00", "00:00", [7])))
    assert got["result"]["result"] == "invalid"
    assert env.fingerprint() == before
    assert ab.schedule_looser(rules.make_schedule("block", [([0], "22:00", "00:00")]),
                              rules.make_schedule("block", [([7], "22:00", "00:00")]))


def test_with_no_challenge_set_the_forced_ones_still_ask(tmp_path, monkeypatch):
    """Turn off, a locked mode, a pause over a locked mode: the challenge even with none turned on (BLUEPRINT: force)."""
    env = Env(tmp_path, "off", monkeypatch)
    for name, args in (("lockdown_switch_off", []), ("mode_stop", []), ("mode_delete", ["custom1"]),
                       ("mode_start", ["work"]), ("pause_start", [60, False])):
        reply = env.call(name, *args)
        assert asks(reply), name
        env.call("challenge_cancel", reply["data"]["ticket"])
    assert not ab.is_off(_fresh(env)) and modes.active(_fresh(env), NOW)["mode"]["id"] == "custom1"
    data(env.call("draft_remove_item", env.ids["b"]))                       # (not forced: free with none set)
    assert env.ids["b"] not in _fresh(env).item_ids()


def test_tray_quit_goes_through_the_guard(tmp_path, monkeypatch):
    """Tray Exit asks the guard ("Quit Lockdown...", BLUEPRINT 4: guard, not forced)."""
    from resident import challenge_cli
    src = (ROOT / "src" / "agent.py").read_text("utf-8")
    assert "challenge_cli.ask_in_main(win, ctx, [EXIT_TEXT], quit_now)" in src
    assert 'EXIT_TEXT = "Close the Lockdown icon and window' in src
    env = Env(tmp_path, "locked", monkeypatch)
    ran = []
    monkeypatch.setattr("resident.windows.open_challenge", lambda win, d: ran.append(("asked", d["ticket"])))
    assert challenge_cli.ask_in_main(None, env.ctx, ["Quit Lockdown"], lambda: ran.append("quit")) is False
    assert ran and ran[0][0] == "asked"


# ================================================================ the draft: what the challenge listed is what is saved

def test_a_draft_edited_after_the_ticket_is_judged_again_when_the_challenge_passes(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    _pending_removal(env)
    ticket = ticket_of(env.call("draft_save"))
    d = drafts.draft_of(env.ctx)
    with drafts.lock:
        d.remove_item(env.ids["a"])                           # a second removal after the ticket (auto-save off)
    assert env.call("challenge_begin", ticket)["data"]["changes"] == ["Remove Game"]
    data(env.pass_challenge(ticket))
    ids = _fresh(env).item_ids()
    assert env.ids["b"] not in ids and env.ids["a"] not in ids   # saved - the pass opened the unlock, so it is free
    assert data(env.call("draft_get_state"))["dirty"] is False


def test_the_same_draft_saves_exactly_through_the_ticket(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    _pending_removal(env)
    data(env.pass_challenge(ticket_of(env.call("draft_save"))))
    assert env.ids["b"] not in _fresh(env).item_ids() and env.ids["a"] in _fresh(env).item_ids()


# ================================================================ bridge hygiene

def test_the_exposed_object_has_only_the_api_methods(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    names = {n for n, _k in webapi.api_methods()}
    assert set(dir(env.api)) == names
    for attr in ("db", "window", "ctx", "guard", "_methods", "__class__", "__dict__", "__globals__", "registry",
                 "main_window", "draft", "set_setting", "dispatch"):
        with pytest.raises(AttributeError):
            getattr(env.api, attr)
    fn = env.api.antibypass_status
    assert set(fn.__globals__) == {"__builtins__", "_call"} and fn.__globals__["__builtins__"] == {}
    assert "set_setting" not in names and not any("set_setting" == n for n in names)


def test_the_asset_server_serves_only_the_ui_folder_and_clean_icon_names(tmp_path):
    server = AssetServer()
    root = Path(server.root)
    assert root == (ROOT / "src" / "ui").resolve()
    assert server.find("/index.html") is not None
    for bad in ("/../agent.py", "/%2e%2e/agent.py", "/js/../../agent.py", "/C:/Windows/win.ini", "//etc/passwd",
                "/js/", "/..%2fagent.py", "/js%5c..%5c..%5cagent.py"):
        assert server.find(bad) is None, bad
    from resident.icons import clean_exe, clean_host
    for bad in ("..\\x.exe", "a/b.exe", "c:x.exe", "x.exe\n", ".hidden.exe"):
        assert clean_exe(bad) is None, bad
    for bad in ("evil.com\n", "a..b", "../x", "a b.com", "-x.com"):
        assert clean_host(bad) is None, bad


def test_no_rpc_route_in_production_code():
    hits = [p for p in (ROOT / "src").rglob("*") if p.suffix in (".py", ".js", ".html")
            and "/rpc" in p.read_text("utf-8", errors="ignore")]
    assert hits == []


def test_open_path_open_url_and_explorer_take_only_known_targets(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    opened, browsed, shown = [], [], []
    monkeypatch.setattr(about, "_opener", opened.append)
    monkeypatch.setattr(about, "_browser", browsed.append)
    monkeypatch.setattr(categories, "_open_explorer", shown.append)
    for bad in ("C:\\Windows", "..", "log", "", None, ["data"]):
        assert not env.call("open_path", bad)["ok"]
    data(env.call("open_path", "data"))
    assert len(opened) == 1
    monkeypatch.setattr(about.updates, "repo_page", lambda: "https://github.com/Husarp/lockdown")
    data(env.call("open_url", "https://evil.example", "file:///C:/x"))
    assert browsed and all(u.startswith("https://github.com/Husarp/lockdown") for u in browsed)
    for bad in ("C:\\Windows\\System32\\cmd.exe", "\\\\server\\share\\x.exe", "notepad.exe", "", "C:\\x.exe\n"):
        assert not env.call("reveal_in_explorer", bad)["ok"], bad
    assert shown == []


def test_challenge_tickets_single_use_expiry_typing_time_and_one_at_a_time(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    first = ticket_of(env.call("protection_allow", "one.com"))
    second = ticket_of(env.call("protection_allow", "two.com"))           # the first one is cancelled
    assert not env.call("challenge_begin", first)["ok"]
    begin = data(env.call("challenge_begin", second))
    assert "Too fast" in env.call("challenge_submit", second, begin["phrase"])["error"]     # typed in no time
    env.mono[0] += guard.TICKET_TTL_SEC + 1
    assert not env.call("challenge_submit", second, begin["phrase"])["ok"]                # expired
    assert "two.com" not in _prot(env)["allowed"]
    third = ticket_of(env.call("protection_allow", "three.com"))
    begin = data(env.call("challenge_begin", third))
    assert not env.call("challenge_submit", third, begin["phrase"][:-1])["ok"]            # wrong text
    env.mono[0] += 60
    assert data(env.call("challenge_submit", third, begin["phrase"]))["passed"]
    assert not env.call("challenge_submit", third, begin["phrase"])["ok"]                 # used
    assert guard.TICKET_TTL_SEC == 600 and guard.MIN_MS_PER_CHAR >= 35


# ================================================================ fixes found by the audit (R28)

def test_a_running_temporary_block_cannot_be_cut_a_minute_at_a_time():
    end = "2026-10-11 16:42:17"
    old = {"rule_type": "temporary", "temp_until": end}
    assert ab.rule_looser(old, {"rule_type": "temporary", "temp_until": "2026-10-11 16:41:18"}, NOW)
    assert ab.rule_looser(old, {"rule_type": "temporary", "duration_min": 59}, NOW)
    assert not ab.rule_looser(old, {"rule_type": "temporary", "temp_until": end}, NOW)
    assert not ab.rule_looser(old, {"rule_type": "temporary", "duration_min": 60}, NOW)


def test_rules_validate_caps_the_name_and_the_days(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    assert not env.call("rules_validate", [LIMIT_1H], {"name": "x" * 201})["ok"]
    assert data(env.call("rules_validate", [LIMIT_1H], {"name": "x" * 200}))["valid"]
    got = data(env.call("rules_validate", [{**_schedule_form("09:00", "17:00"), "windows": [{"days": [0] * 8,
                                                                                            "start": "09:00",
                                                                                            "end": "17:00"}]}]))
    assert not got["valid"]


def test_the_update_address_is_read_as_an_address():
    from resident import update_flow
    repo = update_flow.REPO
    good = f"https://github.com/{repo}/releases/download/v0.90.0/LockdownSetup-0.90.0.exe"
    assert update_flow.asset_ok(good)
    for bad in (f"https://github.com/{repo}/releases/download/../../../evil/x.exe",
                f"https://github.com/{repo}/releases/download/v1/%2e%2e/x.exe",
                f"https://github.com/{repo}/releases/download/v1/x.exe?y", f"https://github.com/{repo}/releases/download/v1/x.exe#y",
                f"https://evil@github.com/{repo}/releases/download/v1/x.exe",
                f"https://github.com:8443/{repo}/releases/download/v1/x.exe",
                f"http://github.com/{repo}/releases/download/v1/x.exe",
                f"https://github.com/{repo}/releases/download/v1/a\\..\\x.exe",
                f"https://github.com/{repo}/releases/download/", f"https://github.com/{repo}/releases/downloadx/v1/x.exe",
                f"https://github.com/{repo}/releases/download/v1/x.exe\n", None, 5):
        assert not update_flow.asset_ok(bad), bad


def test_a_list_download_follows_redirects_only_to_the_web():
    import urllib.error
    import urllib.request
    handler = protection._WebRedirectsOnly()
    req = urllib.request.Request("https://example.com/list.txt")
    for bad in ("ftp://example.com/list.txt", "file:///C:/Windows/win.ini", "data:,x"):
        with pytest.raises(urllib.error.HTTPError):
            handler.redirect_request(req, None, 302, "Found", {}, bad)
    assert handler.redirect_request(req, None, 302, "Found", {}, "https://mirror.example/list.txt") is not None
    with pytest.raises(ValueError):
        protection._open("ftp://example.com/list.txt", 1)


def test_bad_word_and_list_requests_are_refusals_not_crashes(tmp_path, monkeypatch):
    env = Env(tmp_path, "locked", monkeypatch)
    log = tmp_path / "lockdown.log"
    for partial in ({"action": ["close"]}, {"action": {"a": 1}}, {"lists": {"adult_en": "yes"}}, {"off": [1]}):
        reply = env.call("words_update", partial)
        assert not reply["ok"] and reply["error"].startswith("Bad request"), partial
    for key in ("mine1\n", "MINE1", "mine-1", "x" * 31):
        assert not env.call("manual_set_enabled", key, False)["ok"], key
    assert not log.exists() or "Traceback" not in log.read_text("utf-8")


def test_a_damaged_backup_is_refused_whole_and_restore_is_all_or_nothing(tmp_path, monkeypatch):
    env = Env(tmp_path, "free", monkeypatch)
    good = backup.export(env.db)
    settings_api._check_shape(good)                                       # a real export passes
    bad_files = [
        {**good, "items": good["items"] + [{**good["items"][0], "rules": ["not a rule"]}]},
        {**good, "items": [{**good["items"][0], "notify": ["x"]}]},
        {**good, "items": [{**good["items"][0], "rules": [{"rule_type": "time_limit", "daily_limit_min": [60]}]}]},
        {**good, "groups": [{**good["groups"][0], "members": {"1": "x"}}]},
        {**good, "groups": [{**good["groups"][0], "app_block": 5}]},
        {**good, "groups": [{**good["groups"][0], "member_blocks": "x"}]},
        {**good, "groups": ["x"]},
    ]
    for bad in bad_files:
        with pytest.raises(core.ApiError):
            settings_api._check_shape(bad)
    before = env.fingerprint()
    broken = {**good, "items": good["items"] + [{**good["items"][0], "rules": [{"rule_type": "x", "schedule": [1]}]}]}
    with pytest.raises(Exception):
        backup.restore(_fresh(env), broken)                               # fails half-way through ...
    assert env.fingerprint() == before                                    # ... and nothing was changed
    db = _fresh(env)
    backup.restore(db, good)                                              # a good one still restores, and commits
    assert len(_fresh(env).list_items()) == len(good["items"])


def test_an_import_never_switches_lockdown_off(tmp_path, monkeypatch):
    env = Env(tmp_path, "free", monkeypatch)
    ab.switch_off(env.db, NOW)
    exported = backup.export(env.db)
    assert ab.OFF_KEY not in exported["settings"]
    ab.switch_on(env.db)
    backup.restore(_fresh(env), {**exported, "settings": {**exported["settings"], ab.OFF_KEY: "2026-10-11 10:00:00"}})
    assert not ab.is_off(_fresh(env))
    assert ab.OFF_KEY in backup.RUNTIME_KEYS


def test_a_short_8_3_path_into_lockdowns_folder_is_read_as_the_long_one(tmp_path, monkeypatch):
    """Windows glue (GetLongPathNameW) faked: C:\\PROGRA~1\\Lockdown\\tool.py is in Lockdown's own folder."""
    from webapi import blocking_edit, shared
    monkeypatch.setattr(blocking_edit, "APP_DIR", "C:\\Program Files\\Lockdown")
    monkeypatch.setattr(shared, "_windows_long_name", lambda p: p.replace("PROGRA~1", "Program Files"))
    env = Env(tmp_path, "locked", monkeypatch)
    got = data(env.call("draft_submit_item", {"mode": "new", "kind": "app", "target": "tool.py",
                                              "app_path": "C:\\PROGRA~1\\Lockdown\\tool.py", "rules": [LIMIT_1H]}))
    assert got["result"]["result"] == "invalid" and "Lockdown" in got["result"]["errors"][0]["message"]
    assert shared.long_path("C:\\Games\\x.exe") == "C:\\Games\\x.exe"
