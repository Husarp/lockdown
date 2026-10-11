"""R19: the rule editors' forms -> rule dicts (src/rule_forms.py) and the Api's `rules_validate` (webapi/rules.py).

tests/fixtures/rule_cases.json holds the cases (inputs -> expected rule dicts, errors and summary sentences), written by
hand from what the legacy Tk editors (src/gui/rule_editors.py) do. This file runs them against Python; the same file
runs against the page's display copy in tests/ui/js/summary.test.mjs, which keeps the two in step. Where the legacy
editors have pure helpers (parse_duration, format_duration, _minutes, LimitEditor._read, summary) they are called too,
so a form gives exactly what the old editor would have saved."""
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import rule_forms as rf
import webapi
from db import Database
from rules import (DAY_LIMITS_FIELD, TIME_FMT, Usage, day_limits, day_limits_text, days_text, duration_text,
                   effective_rules, item_block, time_bucket)
from webapi import core

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "rule_cases.json").read_text(encoding="utf-8"))
NOW = datetime.strptime(FIXTURE["now"], TIME_FMT)


def ids(cases):
    return [c["id"] for c in cases]


def now_of(case):
    return datetime.strptime(case["now"], TIME_FMT) if case.get("now") else None


# ---------------------------------------------------------------- the shared cases

@pytest.mark.parametrize("case", FIXTURE["forms"], ids=ids(FIXTURE["forms"]))
def test_every_form_gives_the_expected_rule_or_error_and_summary(case):
    shared, now = case.get("shared", False), now_of(case)
    if "error" in case:
        with pytest.raises(rf.RuleFormError) as e:
            rf.rule_from_form(case["form"], shared, now)
        assert (e.value.field, str(e.value)) == (case["error"]["field"], case["error"]["message"])
        assert isinstance(e.value, ValueError)            # (the old editors raised plain ValueErrors)
    else:
        assert rf.rule_from_form(case["form"], shared, now) == case["rule"]
        # reading the form must not touch it (the page sends the same object back on every call)
    assert rf.summarize_form(case["form"], shared, now) == case["summary"]


@pytest.mark.parametrize("case", [c for c in FIXTURE["forms"] if "rule" in c], ids=lambda c: c["id"])
def test_a_rule_loaded_back_into_a_form_saves_as_the_same_rule(case):
    shared, now = case.get("shared", False), now_of(case)
    form = rf.form_from_rule(case["rule"], shared)
    assert rf.rule_from_form(form, shared, now) == case["rule"]
    assert rf.form_from_rule(case["rule"], shared) == rf.form_from_rule(rf.rule_from_form(form, shared, now), shared)


@pytest.mark.parametrize("case", FIXTURE["loads"], ids=ids(FIXTURE["loads"]))
def test_loading_a_rule_into_an_editor(case):
    shared = case.get("shared", False)
    if case["rule"] is None:
        assert rf.default_form(case["type"], shared) == case["form"]
    else:
        assert rf.form_from_rule(case["rule"], shared) == case["form"]


def test_every_default_form_is_a_valid_one():
    for t in rf.RULE_TYPES:
        for shared in (False, True):
            assert rf.rule_from_form(rf.default_form(t, shared), shared)["rule_type"] == t


@pytest.mark.parametrize("case", FIXTURE["lists"], ids=ids(FIXTURE["lists"]))
def test_lists_of_forms(case):
    rules, errors = rf.check_forms(case["forms"], now=NOW, require_one=case.get("require_one", False))
    assert (rules, errors) == (case["rules"], case["errors"])
    if case["errors"]:
        with pytest.raises(rf.RuleFormError, match=case["errors"][0]["message"].replace(".", r"\.")):
            rf.rules_from_forms(case["forms"], now=NOW, require_one=case.get("require_one", False))
    else:
        assert rf.rules_from_forms(case["forms"], now=NOW) == case["rules"]


@pytest.mark.parametrize("case", FIXTURE["sentences"], ids=ids(FIXTURE["sentences"]))
def test_the_sentence_under_the_editors(case):
    rules, errors = rf.check_forms(case["forms"])
    assert not errors
    assert rf.sentence(case["name"], rules) == case["sentence"]


def test_the_words_and_numbers_the_page_copies():
    for text, expected in FIXTURE["durations"]:
        if isinstance(expected, str):
            with pytest.raises(ValueError) as e:
                rf.parse_duration(text)
            assert str(e.value) == expected
        else:
            assert rf.parse_duration(text) == expected
    for minutes, text in FIXTURE["format_duration"]:
        assert rf.format_duration(minutes) == text
    for seconds, text in FIXTURE["duration_text"]:
        assert duration_text(seconds) == text
    for days, text in FIXTURE["days_text"]:
        assert days_text(days) == text
    for days, text in FIXTURE["day_limits_text"]:
        assert day_limits_text(days, rf.format_duration) == text


def test_the_names_the_page_shows_are_the_same_here():
    meta = FIXTURE["meta"]
    assert list(rf.RULE_TYPES) == meta["rule_types"]
    assert rf.RULE_NAMES == meta["names"] and rf.RULE_SUBTITLES == meta["subtitles"]
    assert [[k, v] for k, v in rf.DURATIONS.items()] == meta["durations"]
    assert [[k, v] for k, v in rf.UNITS.items()] == meta["units"]


# ---------------------------------------------------------------- the same as the legacy editors

@pytest.fixture(scope="module")
def legacy():
    """gui/rule_editors.py (customtkinter; the test conftest fakes it where it is missing) - only its pure helpers."""
    from gui import rule_editors
    return rule_editors


def test_the_constants_are_the_legacy_ones(legacy):
    assert rf.DURATIONS == legacy.DURATIONS and rf.UNITS == legacy.UNITS
    assert rf.MAX_TEMPORARY_MIN == legacy.MAX_TEMPORARY_MIN
    assert rf.PERIOD_LABELS == legacy.PERIOD_LABELS and rf.PERIOD_MAX_MIN == legacy.PERIOD_MAX_MIN
    assert rf.RULE_NAMES == legacy.RULE_NAMES and rf.RULE_SUBTITLES == legacy.RULE_SUBTITLES
    assert list(rf.RULE_TYPES) == list(legacy.EDITORS) == list(legacy.RULE_NAMES)


def _outcome(fn, *args):
    try:
        return "ok", fn(*args)
    except ValueError as e:
        return "error", str(e)


def _noise(rng, n=400):
    alphabet = "0123456789  hHmM:.-+x'\""
    return ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 7))) for _ in range(n)]


def test_parse_and_format_duration_are_the_legacy_ones(legacy):
    texts = [t for t, _ in FIXTURE["durations"]] + _noise(random.Random(19))
    for text in texts:
        assert _outcome(rf.parse_duration, text) == _outcome(legacy.parse_duration, text), repr(text)
    for minutes in (None, 0, 1, 59, 60, 61, 90, 1440, 10000):
        assert rf.format_duration(minutes) == legacy.format_duration(minutes)


def test_amount_boxes_read_like_the_legacy_ones(legacy):
    """Time-limit boxes (LimitEditor._read) and minute boxes (_minutes): same number or same message."""
    rng = random.Random(7)
    texts = [t for t, _ in FIXTURE["durations"]] + _noise(rng) + ["24h", "25h", "168h", "169h", "744h", "745h", "1m", "0m"]
    for text in texts:
        for period in ("day", "week", "month"):
            assert _outcome(rf._read_limit, text, period, "x") == _outcome(legacy.LimitEditor._read, text, period), (text, period)
        assert _outcome(rf._read_limit, text, "day", "x", "Friday") == _outcome(legacy.LimitEditor._read, text, "day", "Friday")
    for text in ("", "0", "5", "1440", "1441", "-3", "abc", " 12 ", "1.5", "007"):
        for low, high in ((0, 1440), (1, 1440)):
            assert _outcome(rf._minutes, text, low, high, "Allowance", "f") == _outcome(legacy._minutes, text, low, high, "Allowance")


class _Editor:
    """What the legacy summary() needs of an editor: value()."""

    def __init__(self, rule):
        self.rule = rule

    def value(self):
        return self.rule


def test_summaries_are_the_legacy_ones(legacy):
    for case in FIXTURE["forms"]:
        if "rule" in case:
            rule = case["rule"]
            assert rf.summary(rule) == legacy.summary(rule["rule_type"], _Editor(rule)), case["id"]
    bad = _Editor(None)
    bad.value = lambda: (_ for _ in ()).throw(ValueError("x"))
    assert legacy.summary("time_limit", bad) == rf.BAD_SETTINGS


def test_the_legacy_hours_default_and_load_match(legacy):
    """HoursEditor.load(None): allow only, all days 09:00-17:00, allowance 0; the others' starting values likewise."""
    assert rf.default_form("scheduled") == {"rule_type": "scheduled", "mode": "allow", "allowance": "0",
                                            "windows": [{"days": list(range(7)), "start": "09:00", "end": "17:00"}]}
    assert rf.default_form("time_limit")["day"] == legacy.format_duration(30)
    assert rf.default_form("switch_limit")["day"] == "10" and rf.default_form("switch_limit")["gap"] == "5"
    assert rf.default_form("temporary")["choice"] == str(legacy.DURATIONS["1 hour"])


# ---------------------------------------------------------------- per-weekday limits round-trip with rules.day_limits

def test_per_weekday_limits_round_trip_with_day_limits():
    amounts = [180, 180, 180, 180, 180, 120, None]
    rule = rf.rule_from_form({"rule_type": "time_limit", "same": False, "day": "", "week": "", "month": "",
                              "weekdays": [rf.format_duration(a) for a in amounts]})
    assert rule["daily_limit_min"] is None and day_limits(rule) == amounts
    form = rf.form_from_rule(rule)
    assert form["same"] is False and form["weekdays"] == ["3h", "3h", "3h", "3h", "3h", "2h", ""]
    assert day_limits(rf.rule_from_form(form)) == amounts
    # a rule saved the old way (one amount) opens as "Same every day" and still saves as one amount
    old = rf.form_from_rule({"rule_type": "time_limit", "daily_limit_min": 45})
    assert old["same"] is True and old["day"] == "45m"
    assert rf.rule_from_form(old)[DAY_LIMITS_FIELD] is None
    # and the rule really limits by weekday when it is enforced
    item = {"id": 1, "display_name": "x", "item_type": "app", "target": "x.exe", "rules": [rule]}
    sat = datetime(2026, 9, 19, 12)                      # a Saturday: 2h
    used = Usage({("item:1", time_bucket("day", sat)): 121 * 60})
    assert item_block(effective_rules(item, []), sat, used) is not None
    sun = datetime(2026, 9, 20, 12)                      # Sunday: no limit
    assert item_block(effective_rules(item, []), sun, Usage({("item:1", time_bucket("day", sun)): 99999})) is None


# ---------------------------------------------------------------- temporary: the end is worked out at save, from the trusted clock

def test_a_temporary_block_starts_at_save_with_the_trusted_time(tmp_path, monkeypatch):
    """Forms carry a LENGTH. The Draft turns it into an end time when it saves, with the trusted clock - a stubbed clock
    far from datetime.now() proves nothing else was used."""
    import gui.draft as draft_module
    db = Database(tmp_path / "t.db")
    db.set_setting("ui.autosave", "0")
    draft = draft_module.Draft(db)
    trusted = datetime(2031, 3, 4, 5, 6, 7)
    monkeypatch.setattr(draft_module, "now_from_db", lambda _db: trusted)
    item = draft.add_item("YouTube", ["youtube.com"], "manual")
    rules = rf.rules_from_forms([{"rule_type": "temporary", "choice": "custom", "amount": "90", "unit": "minutes"}], now=trusted)
    assert rules == [{"rule_type": "temporary", "duration_min": 90}]          # a length, not an end
    draft.set_rules(item["id"], rules)
    draft.save()
    rule = db.list_items()[0]["rules"][0]
    assert rule["temp_until"] == (trusted + timedelta(minutes=90)).strftime(TIME_FMT) == "2031-03-04 06:36:07"
    assert abs((datetime.strptime(rule["temp_until"], TIME_FMT) - datetime.now()).days) > 365   # (not the machine's clock)


def test_keep_leaves_a_running_blocks_end_alone(tmp_path, monkeypatch):
    import gui.draft as draft_module
    db = Database(tmp_path / "t.db")
    db.set_setting("ui.autosave", "0")
    trusted = datetime(2031, 3, 4, 5, 6, 7)
    monkeypatch.setattr(draft_module, "now_from_db", lambda _db: trusted)
    db.add_item("YouTube", ["youtube.com"], "site", "manual", [{"rule_type": "temporary", "temp_until": "2031-03-04 09:00:00"}])
    draft = draft_module.Draft(db)
    item = next(iter(draft.items.values()))
    form = rf.form_from_rule(item["rules"][0])
    assert form["choice"] == "keep" and form["keep_until"] == "2031-03-04 09:00:00"
    draft.set_rules(item["id"], rf.rules_from_forms([form], now=trusted))
    assert not draft.dirty                                                       # nothing changed
    assert draft.loosening() == []


def test_the_rules_it_builds_work_with_the_antibypass_checks():
    """The dicts are what antibypass.rule_looser compares: longer is free, shorter / dropped is not (the Draft decides)."""
    import antibypass
    old = rf.rule_from_form({"rule_type": "time_limit", "day": "1h", "same": True, "week": "", "month": ""})
    longer = rf.rule_from_form({"rule_type": "time_limit", "day": "2h", "same": True, "week": "", "month": ""})
    shorter = rf.rule_from_form({"rule_type": "time_limit", "day": "30m", "same": True, "week": "", "month": ""})
    assert antibypass.rule_looser(old, longer, NOW) is True
    assert antibypass.rule_looser(old, shorter, NOW) is False
    hours = rf.rule_from_form({"rule_type": "scheduled", "mode": "block", "allowance": "0",
                               "windows": [{"days": [0], "start": "09:00", "end": "17:00"}]})
    later = rf.rule_from_form({"rule_type": "scheduled", "mode": "block", "allowance": "0",
                               "windows": [{"days": [0], "start": "09:00", "end": "18:00"}]})
    assert antibypass.rule_looser(hours, later, NOW) is False                    # extending hours is free
    assert antibypass.rule_looser(later, hours, NOW) is True                     # shortening them is not


# ---------------------------------------------------------------- never trusting the page

@pytest.mark.parametrize("form", [
    None, "x", 5, [], {"rule_type": ["scheduled"]},
    {"rule_type": "scheduled", "mode": "allow", "windows": "no", "allowance": "0"},
    {"rule_type": "scheduled", "mode": "allow", "windows": [5], "allowance": "0"},
    {"rule_type": "scheduled", "mode": "allow", "windows": [{"days": "all", "start": "09:00", "end": "10:00"}], "allowance": "0"},
    {"rule_type": "scheduled", "mode": "allow", "windows": [{"days": [7], "start": "09:00", "end": "10:00"}], "allowance": "0"},
    {"rule_type": "scheduled", "mode": "allow", "windows": [{"days": [True], "start": "09:00", "end": "10:00"}], "allowance": "0"},
    {"rule_type": "scheduled", "mode": "allow", "windows": [{"days": [1], "start": ["09:00"], "end": "10:00"}], "allowance": "0"},
    {"rule_type": "scheduled", "mode": "maybe", "windows": [], "allowance": "0"},
    {"rule_type": "scheduled", "mode": "allow", "windows": [{"days": [1], "start": "09:00", "end": "10:00"}] * 51, "allowance": "0"},
    {"rule_type": "scheduled", "mode": "allow", "windows": [], "allowance": [1]},
    {"rule_type": "time_limit", "same": False, "weekdays": "x"},
    {"rule_type": "time_limit", "same": False, "weekdays": ["1h"] * 6},
    {"rule_type": "time_limit", "same": False, "weekdays": ["1h"] * 6 + [{"a": 1}]},
    {"rule_type": "time_limit", "day": {"a": 1}},
    {"rule_type": "time_limit", "day": True},
    {"rule_type": "time_limit", "day": "1" * 300},
    {"rule_type": "switch_limit", "day": "1", "counts": "sometimes"},
    {"rule_type": "switch_limit", "day": "9" * 5000, "counts": "switch"},
    {"rule_type": "temporary", "choice": "custom", "amount": "9" * 5000, "unit": "days"},
    {"rule_type": "temporary", "choice": ["keep"]},
    {"rule_type": "nonsense"},
])
def test_garbage_from_the_page_is_an_error_not_a_crash(form):
    with pytest.raises(rf.RuleFormError):
        rf.rule_from_form(form, now=NOW)
    rules, errors = rf.check_forms([form], now=NOW)
    assert rules == [] and len(errors) == 1 and errors[0]["message"]


def test_reading_a_form_leaves_it_alone():
    form = {"rule_type": "scheduled", "mode": "block", "allowance": "5",
            "windows": [{"days": [3, 1, 1], "start": "09:00", "end": "17:00"}, {"days": [], "start": "x", "end": "y"}]}
    copy = json.loads(json.dumps(form))
    rf.rule_from_form(form)
    assert form == copy


# ---------------------------------------------------------------- the Api: rules_validate

@pytest.fixture
def api(tmp_path):
    ctx = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    return core.build_api(ctx)


def test_rules_validate_is_a_read_method():
    assert dict(webapi.api_methods())["rules_validate"] == "read"


def test_rules_validate_returns_rules_summaries_and_the_sentence(api):
    forms = [{"rule_type": "time_limit", "day": "1h30", "week": "", "month": "", "same": True, "weekdays": [""] * 7},
             rf.default_form("scheduled")]
    reply = api.rules_validate(forms, {"name": "YouTube"})
    assert reply["ok"]
    data = reply["data"]
    assert data["valid"] is True and data["errors"] == []
    assert [r["rule_type"] for r in data["rules"]] == ["scheduled", "time_limit"]       # shown / saved in this order
    assert data["rules"][1] == {"rule_type": "time_limit", "daily_limit_days": None, "daily_limit_min": 90,
                                "weekly_limit_min": None, "monthly_limit_min": None}
    assert data["summaries"] == {"scheduled": "Allowed only 09:00-17:00 Every day", "time_limit": "1h30 per day"}
    assert data["sentence"] == ("YouTube will be blocked: by time (Allowed only 09:00-17:00 Every day); "
                                "time limit (1h30 per day).")


def test_rules_validate_reports_every_bad_blocker(api):
    forms = [{"rule_type": "time_limit", "day": "abc", "week": "", "month": "", "same": True, "weekdays": [""] * 7},
             rf.default_form("switch_limit"),
             {"rule_type": "temporary", "choice": "custom", "amount": "0", "unit": "hours"}]
    data = api.rules_validate(forms)["data"]
    assert data["valid"] is False
    assert data["errors"] == [
        {"rule_type": "time_limit", "field": "day", "message": "Can't read 'abc' - write e.g. 45m, 2h or 1h30."},
        {"rule_type": "temporary", "field": "amount", "message": "Custom duration must be between 1 minute and 30 days."}]
    assert [r["rule_type"] for r in data["rules"]] == ["switch_limit"] and data["sentence"] == ""


def test_rules_validate_context_shared_and_require_one(api):
    form = {**rf.default_form("scheduled", True), "mode": "block", "allowance": "5", "pot": False}
    assert api.rules_validate([form], {"shared": True})["data"]["rules"][0]["allowance_shared"] == 0
    assert api.rules_validate([form])["data"]["rules"][0]["allowance_shared"] is None
    assert api.rules_validate([])["data"]["valid"] is True
    empty = api.rules_validate([], {"require_one": True})["data"]
    assert empty["valid"] is False and empty["errors"][0]["message"] == "Tick at least one blocker."


def test_rules_validate_uses_the_trusted_clock_to_refuse_a_finished_keep(api):
    keep = lambda until: {"rule_type": "temporary", "choice": "keep", "keep_until": until}   # noqa: E731
    assert api.rules_validate([keep("2026-10-11 18:00:00")])["data"]["valid"] is True        # NOW is 15:42
    ended = api.rules_validate([keep("2026-10-11 10:00:00")])["data"]
    assert ended["valid"] is False and "already ended" in ended["errors"][0]["message"]


def test_rules_validate_refuses_bad_requests(api):
    assert api.rules_validate("nope")["ok"] is False
    assert api.rules_validate([], "ctx")["ok"] is False
    assert api.rules_validate([rf.default_form("permanent")] * 100)["ok"] is False
    assert api.rules_validate([float("nan")])["ok"] is False                   # (not plain JSON)
    for ctx in ({"shared": "yes"}, {"name": 5}, {"require_one": 1}):
        assert api.rules_validate([], ctx)["ok"] is False


def test_rules_validate_changes_nothing(tmp_path):
    ctx = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    api = core.build_api(ctx)
    before = ctx.db.conn.total_changes
    api.rules_validate([rf.default_form("temporary"), rf.default_form("permanent")], {"name": "x"})
    assert ctx.db.conn.total_changes == before
