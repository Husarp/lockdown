"""R19: the blocker rail and the editors in the browser (tests/ui/static/rule-editors.html uses js/rules/*.js alone).

What is checked: the rail (5 rows, "k of 5 on", the note, the panel with its hint and sentence), every editor's fields, that an
edit updates the row summary and the sentence in the SAME task as the input event with no Api call, the error under the
field, adding / removing time windows from the keyboard, the "ends next day" label, the Mon-Sun grid, "Keep (X left)",
the stack, and that the page's reading of a form is the same as Python's (rules_validate on the real Api)."""
import json

import pytest


@pytest.fixture
def editors(page, harness):
    def open_(query=""):
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(harness.url("/__t/rule-editors.html" + query))
        page.wait_for_function("window.__ready === true")
        return page
    return open_


def js(page, expr, arg=None):
    return page.evaluate(expr, arg) if arg is not None else page.evaluate(expr)


def row(t):
    return f'.rail-row[data-t="{t}"]'


def tick(page, t):
    page.click(f'{row(t)} .switch')


def summary_of(page, t):
    return page.inner_text(f'{row(t)} .sum')


# ---------------------------------------------------------------- the rail

def test_rail_has_five_rows_a_count_a_note_and_a_blank_panel(page, editors):
    editors()
    assert page.locator(".rail-row").count() == 5
    names = page.eval_on_selector_all(".rail-row .rail-txt b", "els => els.map(e => e.textContent)")
    assert names == ["By time", "Time limit", "Opening limit", "Permanent", "Temporary"]
    assert page.inner_text("[data-count]") == "0 of 5 on"
    assert page.inner_text(".rail-note") == "Blocked when any ticked blocker applies."
    assert page.inner_text("[data-title]") == "Blocker settings"
    assert page.is_visible("[data-blank]") and "Tick a blocker" in page.inner_text("[data-blank]")
    assert page.inner_text("[data-hint]") == "Blockers combine - it's blocked whenever any ticked one applies."
    assert page.inner_text("[data-sentence]") == ""
    assert all(page.inner_text(f"{row(t)} .sum") == "off" for t in ("scheduled", "time_limit", "switch_limit", "permanent", "temporary"))
    # a switch is a real checkbox with the blocker's name; the row button opens the panel
    assert page.get_by_role("checkbox", name="Time limit").count() == 1
    # the rows and the panel sit side by side on a wide window
    a, b = page.locator(".rail-side").bounding_box(), page.locator(".rail-panel").bounding_box()
    assert b["x"] > a["x"] + a["width"] and abs(b["y"] - a["y"]) < 40


def test_ticking_opens_the_panel_and_clicking_a_row_ticks_and_opens_it(page, editors):
    editors()
    tick(page, "time_limit")
    assert page.inner_text("[data-count]") == "1 of 5 on"
    assert page.inner_text("[data-title]") == "Time limit"
    assert page.inner_text("[data-sub]") == "a cap on how long per day / week"
    assert summary_of(page, "time_limit") == "30m per day"                  # the starting values
    assert page.inner_text("[data-hint]") == "Next: tick By time in the rail to combine it with this one."
    page.click(f"{row('temporary')} [data-open]")                           # clicking the row ticks it on and opens it
    assert page.inner_text("[data-count]") == "2 of 5 on" and page.inner_text("[data-title]") == "Temporary"
    assert page.is_checked(f"{row('temporary')} input")
    assert page.locator('.rule-ed[data-type="time_limit"]').is_hidden() and page.locator('.rule-ed[data-type="temporary"]').is_visible()
    page.click(f"{row('temporary')} .switch")                               # unticking the open one opens the next ticked one
    assert page.inner_text("[data-title]") == "Time limit" and page.inner_text("[data-count]") == "1 of 5 on"
    page.click(f"{row('time_limit')} .switch")
    assert page.inner_text("[data-title]") == "Blocker settings" and page.is_visible("[data-blank]")
    kinds = js(page, "() => window.changes.map(c => c.kind + ':' + c.rule_type)")
    assert kinds == ["tick:time_limit", "tick:temporary", "tick:temporary", "tick:time_limit"]


def test_hint_when_everything_is_ticked(page, editors):
    editors()
    for t in ("scheduled", "time_limit", "switch_limit", "permanent", "temporary"):
        tick(page, t)
    assert page.inner_text("[data-count]") == "5 of 5 on"
    assert page.inner_text("[data-hint]") == "Every blocker is ticked - blocked when any of them applies."


def test_the_sentence_follows_the_name(page, editors):
    editors()
    tick(page, "scheduled")
    tick(page, "permanent")
    assert page.inner_text("[data-sentence]") == ("YouTube will be blocked: by time (Allowed only 09:00-17:00 Every day); "
                                                  "permanent (always blocked).")
    page.fill("#name", "Games")
    assert page.inner_text("[data-sentence]").startswith("Games will be blocked: by time")
    page.fill("#name", "")
    assert page.inner_text("[data-sentence]").startswith("It will be blocked: by time")


# ---------------------------------------------------------------- an edit is instant and asks nobody

def test_editing_updates_the_summary_in_the_same_frame_without_an_api_call(page, editors, harness):
    editors()
    tick(page, "time_limit")
    calls = len(harness.calls)
    # one task: set the value, fire the input event, read the page back - nothing waits for a timer, a frame or Python
    seen = js(page, """() => {
      const box = document.querySelector('.rule-ed[data-type="time_limit"] [data-f="day"]');
      box.value = "2h"; box.dispatchEvent(new Event("input", { bubbles: true }));
      return { row: document.querySelector('.rail-row[data-t="time_limit"] .sum').textContent,
               sentence: document.querySelector("[data-sentence]").textContent };
    }""")
    assert seen == {"row": "2h per day", "sentence": "YouTube will be blocked: time limit (2h per day)."}
    page.type('.rule-ed[data-type="time_limit"] [data-f="week"]', "5h", delay=20)      # real key presses
    assert summary_of(page, "time_limit") == "2h per day · 5h per week"
    assert len(harness.calls) == calls                                                  # no rpc at all, not one per key
    assert {c["kind"] for c in js(page, "() => window.changes")} == {"tick", "edit"}


def test_invalid_input_shows_the_error_under_the_field_and_clears_when_fixed(page, editors, harness):
    editors()
    tick(page, "time_limit")
    box = '.rule-ed[data-type="time_limit"] [data-f="day"]'
    page.fill(box, "abc")
    err = page.locator('.rule-ed[data-type="time_limit"] [data-zone="periods"]')
    assert err.is_visible() and err.inner_text() == "Can't read 'abc' - write e.g. 45m, 2h or 1h30."
    assert page.get_attribute(box, "aria-invalid") == "true"
    assert summary_of(page, "time_limit") == "check the settings"
    assert "bad" in page.get_attribute(row("time_limit"), "class")
    # the message sits directly under the boxes it is about
    boxes_bottom = page.locator('.rule-ed[data-type="time_limit"] .rule-row').first.bounding_box()
    err_box = err.bounding_box()
    assert 0 < err_box["y"] - (boxes_bottom["y"] + boxes_bottom["height"]) < 30
    page.fill(box, "26h")
    assert err.inner_text() == "The limit per day must be between 1m and 24h."
    page.fill(box, "")
    assert err.inner_text() == "Fill in at least one time limit (per day, week or month)."
    assert page.get_attribute(box, "aria-invalid") == "true"
    page.fill(box, "45m")
    assert err.is_hidden() and page.get_attribute(box, "aria-invalid") is None
    assert summary_of(page, "time_limit") == "45m per day" and "bad" not in page.get_attribute(row("time_limit"), "class")
    assert harness.rpc_count("rules_validate") == 0


def test_the_other_editors_show_their_own_errors(page, editors):
    editors()
    tick(page, "switch_limit")
    ed = '.rule-ed[data-type="switch_limit"]'
    page.fill(f'{ed} [data-f="day"]', "20000")
    assert page.inner_text(f'{ed} [data-zone="periods"]') == "Openings per day must be a number 0-10000."
    page.fill(f'{ed} [data-f="day"]', "5")
    page.fill(f'{ed} [data-f="gap"]', "0")
    assert page.inner_text(f'{ed} [data-zone="gap"]') == "Minutes away must be 1-1440 minutes."
    page.click(f'{ed} .seg button[data-v="switch"]')                         # every switch: the gap no longer matters
    assert page.locator(f'{ed} [data-zone="gap"]').is_hidden() and page.locator(f"{ed} [data-visit]").is_hidden()
    assert page.is_visible(f"{ed} [data-switch]")
    assert summary_of(page, "switch_limit") == "5 opens per day"
    tick(page, "scheduled")
    hours = '.rule-ed[data-type="scheduled"]'
    page.fill(f'{hours} .stepper input', "5000")
    assert page.inner_text(f'{hours} [data-zone="allowance"]') == "Allowance must be 0-1440 minutes."
    page.fill(f'{hours} .stepper input', "10")
    page.fill(f'{hours} .tp-h >> nth=1', "25")                              # the first window's "to" hour
    assert page.inner_text(f'{hours} [data-zone="windows"]') == "Hours must look like 09:00 or 21:30."
    assert page.get_attribute(f"{hours} [data-win]", "aria-invalid") == "true"
    tick(page, "temporary")
    temp = '.rule-ed[data-type="temporary"]'
    page.click(f'{temp} [data-choice="custom"]')
    page.fill(f'{temp} [data-f="amount"]', "0")
    assert page.inner_text(f'{temp} [data-zone="amount"]') == "Custom duration must be between 1 minute and 30 days."


# ---------------------------------------------------------------- By time

def test_time_windows_are_added_and_removed_from_the_keyboard(page, editors):
    editors()
    tick(page, "scheduled")
    ed = '.rule-ed[data-type="scheduled"]'
    wins = lambda: page.locator(f"{ed} [data-win]").count()               # noqa: E731
    assert wins() == 1
    page.focus(f"{ed} [data-add]")
    page.keyboard.press("Enter")                                           # + Add time window
    assert wins() == 2
    assert page.evaluate("document.activeElement.classList.contains('tp-h')")        # focus is in the new window
    assert page.inner_text(f"{row('scheduled')} .sum") == "Allowed only 09:00-17:00 Every day +1 more"
    assert page.get_attribute(f"{ed} [data-win] >> nth=1", "aria-label") == "Time window 2"
    # Tab through the new window to its remove button, press Space
    for _ in range(40):
        if page.evaluate("document.activeElement.hasAttribute('data-remove')"):
            break
        page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.closest('[data-win]') === document.querySelectorAll('.rule-ed[data-type=scheduled] [data-win]')[1]")
    page.keyboard.press("Space")
    assert wins() == 1
    assert page.evaluate("document.activeElement.hasAttribute('data-add')")          # focus is not lost
    assert page.inner_text(f"{row('scheduled')} .sum") == "Allowed only 09:00-17:00 Every day"
    # a day can be switched by keyboard, and a window with no days is ignored
    page.focus(f"{ed} [data-win] .days button >> nth=0")
    page.keyboard.press("Space")
    assert page.get_attribute(f"{ed} [data-win] .days button >> nth=0", "aria-pressed") == "false"
    assert summary_of(page, "scheduled") == "Allowed only 09:00-17:00 Tuesday–Sunday"
    page.focus(f"{ed} [data-add]")
    page.keyboard.press("Enter")
    for i in range(7):                                                      # untick every day of the new window
        page.click(f"{ed} [data-win] >> nth=1 >> .days button >> nth={i}")
    assert summary_of(page, "scheduled") == "Allowed only 09:00-17:00 Tuesday–Sunday"
    forms = js(page, "() => rail.forms()")
    assert forms[0]["windows"][1]["days"] == []                             # (it is sent as it is; Python ignores it)


def test_the_overnight_label_appears_when_a_window_ends_before_it_starts(page, editors):
    editors()
    tick(page, "scheduled")
    ed = '.rule-ed[data-type="scheduled"]'
    nxt = page.locator(f"{ed} [data-next]")
    assert nxt.is_hidden()
    page.fill(f"{ed} .tp-h >> nth=0", "22")
    page.fill(f"{ed} .tp-m >> nth=0", "30")                                 # 22:30 - 17:00
    assert nxt.is_visible() and nxt.inner_text() == "ends next day"
    page.fill(f"{ed} .tp-h >> nth=1", "23")                                 # 22:30 - 23:00
    assert nxt.is_hidden()
    page.fill(f"{ed} .tp-h >> nth=1", "07")
    page.fill(f"{ed} .tp-m >> nth=1", "00")                                 # 22:30 - 07:00
    assert nxt.is_visible()
    assert summary_of(page, "scheduled") == "Allowed only 22:30-07:00 Every day"
    page.fill(f"{ed} .tp-h >> nth=0", "7")                                  # start = end: the whole day, so it runs on
    page.fill(f"{ed} .tp-m >> nth=0", "00")
    assert nxt.is_visible()


def test_hours_editor_modes_allowance_and_the_group_pot(page, editors):
    editors("?shared=1")
    tick(page, "scheduled")
    ed = '.rule-ed[data-type="scheduled"]'
    page.click(f'{ed} .seg button[data-v="block"]')
    page.click(f"{ed} .stepper button[aria-label=More]")                    # +5 minutes
    assert summary_of(page, "scheduled") == "Blocked 09:00-17:00 Every day · +5 min"
    page.uncheck(f"{ed} [data-pot]")
    assert summary_of(page, "scheduled") == "Blocked 09:00-17:00 Every day · +5 min each"
    form = js(page, "() => rail.forms()[0]")
    assert form == {"rule_type": "scheduled", "mode": "block", "allowance": "5", "pot": False,
                    "windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "09:00", "end": "17:00"}]}


# ---------------------------------------------------------------- Time limit

def test_time_limit_weekday_grid_fill_and_carry_over(page, editors):
    editors()
    tick(page, "time_limit")
    ed = '.rule-ed[data-type="time_limit"]'
    day = f'{ed} [data-f="day"]'
    assert page.locator(f"{ed} [data-grid]").is_hidden() and page.input_value(day) == "30m"
    page.click(f'{ed} .seg button[data-v="grid"]')                          # Mon - Sun: every day starts with the amount
    assert page.locator(f"{ed} [data-grid]").is_visible() and page.is_disabled(day) and page.input_value(day) == ""
    assert [page.input_value(f'{ed} [data-f="weekdays.{i}"]') for i in range(7)] == ["30m"] * 7
    assert page.input_value(f"{ed} [data-fill]") == "30m"
    page.fill(f"{ed} [data-fill]", "3h")
    page.click(f'{ed} button[data-fill-days="0-4"]')                        # Fill 3h into Mon-Fri
    page.fill(f"{ed} [data-fill]", "2h")
    page.click(f'{ed} button[data-fill-days="5-6"]')                        # Fill 2h into Sat-Sun
    assert summary_of(page, "time_limit") == "per day 3h Mon–Fri, 2h Sat–Sun"
    page.fill(f'{ed} [data-f="weekdays.6"]', "")                            # Sunday: no limit
    assert summary_of(page, "time_limit") == "per day 3h Mon–Fri, 2h Sat, no limit Sun"
    page.fill(f'{ed} [data-f="weekdays.5"]', "25h")
    assert page.inner_text(f'{ed} [data-zone="weekdays"]') == "The limit on Saturday must be between 1m and 24h."
    assert page.get_attribute(f'{ed} [data-f="weekdays.5"]', "aria-invalid") == "true"
    page.fill(f"{ed} [data-fill]", "1h")
    page.click(f'{ed} button[data-fill-days="0-6"]')                        # Every day
    assert summary_of(page, "time_limit") == "1h per day"                   # (all seven the same is just one amount)
    form = js(page, "() => rail.forms()[0]")
    assert form["same"] is False and form["weekdays"] == ["1h"] * 7 and form["day"] == ""
    page.click(f'{ed} .seg button[data-v="same"]')                          # back: the first weekday's amount comes back
    assert page.input_value(day) == "1h" and page.is_enabled(day) and page.locator(f"{ed} [data-grid]").is_hidden()
    assert summary_of(page, "time_limit") == "1h per day"


def test_time_limit_loads_a_per_weekday_rule_and_labels_a_group_limit(page, editors):
    editors("?shared=1")
    js(page, """() => rail.load([{ rule_type: "time_limit", daily_limit_min: null, daily_limit_days: "[180, 180, 180, 180, 180, 120, 120]", weekly_limit_min: 1200 }])""")
    ed = '.rule-ed[data-type="time_limit"]'
    assert page.locator(f"{ed} [data-grid]").is_visible()
    assert page.input_value(f'{ed} [data-f="weekdays.5"]') == "2h" and page.input_value(f'{ed} [data-f="week"]') == "20h"
    assert summary_of(page, "time_limit") == "per day 3h Mon–Fri, 2h Sat–Sun · 20h per week"
    assert "Shared limit" in page.inner_text(f"{ed} .rule-row >> nth=0") and "For all members together" in page.inner_text(ed)


# ---------------------------------------------------------------- Opening limit / Temporary / Permanent

def test_opening_limit_editor(page, editors):
    editors()
    tick(page, "switch_limit")
    ed = '.rule-ed[data-type="switch_limit"]'
    assert summary_of(page, "switch_limit") == "10 opens per day"
    assert page.input_value(f'{ed} [data-f="gap"]') == "5" and page.locator(f"{ed} [data-visit]").is_visible()
    page.fill(f'{ed} [data-f="week"]', "40")
    page.fill(f'{ed} [data-f="month"]', "100")
    assert summary_of(page, "switch_limit") == "10 opens per day · 40 opens per week · 100 opens per month"
    assert js(page, "() => rail.forms()[0]") == {"rule_type": "switch_limit", "counts": "visit", "day": "10", "week": "40", "month": "100", "gap": "5"}


def test_temporary_editor_presets_custom_and_keep(page, editors):
    editors()
    js(page, """() => rail.load([{ rule_type: "temporary", temp_until: "2026-10-11 18:00:00" }])""")
    ed = '.rule-ed[data-type="temporary"]'
    keep = f'{ed} [data-choice="keep"]'
    assert page.is_visible(keep) and page.inner_text(keep) == "Keep (2h 17m left)"       # 18:00:00 - the trusted 15:42:17
    assert page.get_attribute(keep, "aria-pressed") == "true" and page.locator(f"{ed} [data-note]").is_hidden()
    assert summary_of(page, "temporary") == "keeps its current end"
    page.click(f'{ed} [data-choice="180"]')
    assert page.get_attribute(keep, "aria-pressed") == "false" and page.get_attribute(f'{ed} [data-choice="180"]', "aria-pressed") == "true"
    assert summary_of(page, "temporary") == "for 3h 00m" and page.is_visible(f"{ed} [data-note]")
    page.click(f'{ed} [data-choice="custom"]')
    assert page.is_visible(f"{ed} [data-custom]")
    page.fill(f'{ed} [data-f="amount"]', "2")
    page.click(f'{ed} .seg button[data-v="days"]')
    assert summary_of(page, "temporary") == "for 2d 0h"
    assert js(page, "() => rail.forms()[0]") == {"rule_type": "temporary", "choice": "custom", "amount": "2", "unit": "days", "keep_until": "2026-10-11 18:00:00"}
    page.click(keep)                                                        # keep again
    assert summary_of(page, "temporary") == "keeps its current end"
    # the presets are the legacy ones
    labels = page.eval_on_selector_all(f"{ed} [data-choice]:not([hidden])", "els => els.map(e => e.textContent)")
    assert labels == ["15 min", "30 min", "1 hour", "2 hours", "3 hours", "4 hours", "8 hours", "24 hours", "Custom…", "Keep (2h 17m left)"]


def test_a_keep_that_has_already_ended_says_so_when_it_is_loaded(page, editors):
    editors()
    js(page, """() => rail.load([{ rule_type: "temporary", temp_until: "2026-10-11 10:00:00" }])""")      # the trusted time is 15:42
    ed = '.rule-ed[data-type="temporary"]'
    assert page.inner_text(f'{ed} [data-zone="choice"]') == "That block has already ended - pick a new length."
    assert summary_of(page, "temporary") == "check the settings"
    page.click(f'{ed} [data-choice="60"]')
    assert page.locator(f'{ed} [data-zone="choice"]').is_hidden() and summary_of(page, "temporary") == "for 1h 00m"


def test_a_new_temporary_block_has_no_keep_and_says_when_it_starts(page, editors):
    editors()
    tick(page, "temporary")
    ed = '.rule-ed[data-type="temporary"]'
    assert page.locator(f'{ed} [data-choice="keep"]').is_hidden()
    assert page.get_attribute(f'{ed} [data-choice="60"]', "aria-pressed") == "true" and summary_of(page, "temporary") == "for 1h 00m"
    assert page.inner_text(f"{ed} [data-note]") == "starting when saved"


def test_permanent_editor(page, editors):
    editors()
    tick(page, "permanent")
    assert page.inner_text("[data-title]") == "Permanent" and "Always blocked." in page.inner_text('.rule-ed[data-type="permanent"]')
    assert summary_of(page, "permanent") == "always blocked"
    assert js(page, "() => rail.forms()") == [{"rule_type": "permanent"}]


# ---------------------------------------------------------------- load / forms / Python

RULES = [
    {"rule_type": "scheduled", "schedule": '{"mode": "block", "windows": [{"days": [0, 1, 2, 3, 4], "start": "22:30", "end": "07:00"}]}', "allowance_min": 10, "allowance_shared": None},
    {"rule_type": "time_limit", "daily_limit_min": 90, "weekly_limit_min": 600, "monthly_limit_min": None, "daily_limit_days": None},
    {"rule_type": "switch_limit", "daily_switch_limit": 5, "weekly_switch_limit": None, "monthly_switch_limit": None, "switch_mode": "switch"},
    {"rule_type": "permanent"},
    {"rule_type": "temporary", "duration_min": 150},
]


def test_load_shows_the_rules_and_forms_gives_back_what_was_typed(page, editors):
    editors()
    js(page, "(rules) => rail.load(rules)", RULES)
    assert page.inner_text("[data-count]") == "5 of 5 on" and page.inner_text("[data-title]") == "By time"
    assert [summary_of(page, t) for t in ("scheduled", "time_limit", "switch_limit", "permanent", "temporary")] == [
        "Blocked 22:30-07:00 Monday–Friday · +10 min", "1h30 per day · 10h per week", "5 opens per day",
        "always blocked", "for 2h 30m"]
    forms = js(page, "() => rail.forms()")
    assert [f["rule_type"] for f in forms] == ["scheduled", "time_limit", "switch_limit", "permanent", "temporary"]
    assert forms[4] == {"rule_type": "temporary", "choice": "custom", "amount": "150", "unit": "minutes", "keep_until": None}
    assert forms[2]["counts"] == "switch"
    # what the page reads from them is what it loaded (the display copy)
    check = js(page, "() => rail.check()")
    assert check["errors"] == []
    assert check["rules"][0]["schedule"] == RULES[0]["schedule"] and check["rules"][1]["daily_limit_min"] == 90
    # loading again resets: unticked, starting values, nothing open - and does not call onChange
    n = js(page, "() => window.changes.length")
    js(page, "() => rail.load([])")
    assert page.inner_text("[data-count]") == "0 of 5 on" and page.is_visible("[data-blank]")
    assert js(page, "() => window.changes.length") == n
    tick(page, "time_limit")
    assert summary_of(page, "time_limit") == "30m per day"


def test_validate_asks_python_and_agrees_with_the_page(page, editors, harness):
    editors()
    js(page, "(rules) => rail.load(rules)", RULES)
    local = js(page, "() => rail.check()")
    reply = js(page, "() => rail.validate()")
    assert harness.rpc_count("rules_validate") == 1
    assert reply["valid"] is True and reply["errors"] == []
    # the same rule dicts from both readings, for every blocker (the shared fixture keeps this true in general)
    assert reply["rules"] == local["rules"]
    assert reply["sentence"] == "YouTube will be blocked: " + "; ".join([
        "by time (Blocked 22:30-07:00 Monday–Friday · +10 min)", "time limit (1h30 per day · 10h per week)",
        "opening limit (5 opens per day)", "permanent (always blocked)", "temporary (for 2h 30m)"]) + "."
    assert page.inner_text("[data-sentence]") == reply["sentence"]
    assert reply["summaries"]["time_limit"] == summary_of(page, "time_limit")


def test_validate_shows_what_python_says_under_the_field(page, editors):
    editors()
    js(page, "(rules) => rail.load(rules)", RULES[:2])
    # an error only Python knows about (the display copy is happy): it still lands under the right field
    page.evaluate("() => rail.showErrors([{ rule_type: 'time_limit', field: 'week', message: 'Python says the week is too long.' }])")
    ed = '.rule-ed[data-type="time_limit"]'
    assert page.inner_text("[data-title]") == "Time limit"                   # the first blocker with an error opens
    assert page.inner_text(f'{ed} [data-zone="periods"]') == "Python says the week is too long."
    assert page.get_attribute(f'{ed} [data-f="week"]', "aria-invalid") == "true" and "bad" in page.get_attribute(row("time_limit"), "class")
    page.fill(f'{ed} [data-f="week"]', "9h")                                  # editing clears it
    assert page.locator(f'{ed} [data-zone="periods"]').is_hidden()
    page.evaluate("() => rail.showErrors([{ rule_type: null, field: 'rules', message: 'Tick at least one blocker.' }])")
    assert page.inner_text('.rail-wrap > [data-zone="rules"]') == "Tick at least one blocker."
    page.evaluate("() => rail.showErrors([])")
    assert page.locator('.rail-wrap > [data-zone="rules"]').is_hidden()


def test_validate_with_a_bad_form_gets_pythons_error(page, editors, harness):
    editors("?requireOne=1")
    reply = js(page, "() => rail.validate()")                                 # nothing ticked
    assert reply["valid"] is False and reply["errors"][0]["message"] == "Tick at least one blocker."
    assert page.inner_text('.rail-wrap > [data-zone="rules"]') == "Tick at least one blocker."
    tick(page, "time_limit")
    page.fill('.rule-ed[data-type="time_limit"] [data-f="day"]', "99h")
    reply = js(page, "() => rail.validate()")
    assert reply["valid"] is False
    assert reply["errors"] == [{"rule_type": "time_limit", "field": "day", "message": "The limit per day must be between 1m and 24h."}]
    assert harness.rpc_count("rules_validate") == 2


def test_the_pages_reading_equals_pythons_for_the_shared_fixture(page, editors, harness):
    """Every form in tests/fixtures/rule_cases.json, sent to the REAL Api from the page: the answer is the fixture's."""
    editors()
    from pathlib import Path
    cases = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "rule_cases.json").read_text(encoding="utf-8"))["forms"]
    cases = [c for c in cases if c["form"].get("choice") != "keep"]           # (keep needs the clock: covered in the Python tests)
    answers = js(page, """async (cases) => {
      const out = {};
      for (const c of cases) out[c.id] = await ld.call("rules_validate", [c.form], { shared: !!c.shared });
      return out; }""", cases)
    for c in cases:
        a = answers[c["id"]]
        if "error" in c:
            assert a["valid"] is False and (a["errors"][0]["field"], a["errors"][0]["message"]) == (c["error"]["field"], c["error"]["message"]), c["id"]
        else:
            assert a["valid"] is True and a["rules"] == [c["rule"]], c["id"]


# ---------------------------------------------------------------- the stack (extra limits for a member)

def test_stack_layout_for_a_members_extra_limits(page, editors):
    editors("?kind=stack")
    titles = page.eval_on_selector_all(".rule-sec .card-h h3", "els => els.map(e => e.textContent)")
    assert titles == ["Also limit this member: by time", "Also limit this member: time limit",
                      "Also limit this member: opening limit", "Also limit this member: temporary"]   # (no Permanent)
    assert page.locator(".rule-sec.off").count() == 4
    page.click('.rule-sec[data-t="time_limit"] .switch')
    assert page.locator('.rule-sec[data-t="time_limit"].off').count() == 0
    assert page.is_visible('.rule-sec[data-t="time_limit"] .rule-ed') and page.inner_text('.rule-sec[data-t="time_limit"] .sum') == "30m per day"
    page.fill('.rule-ed[data-type="time_limit"] [data-f="day"]', "1h")
    assert page.inner_text('.rule-sec[data-t="time_limit"] .sum') == "1h per day"
    js(page, """() => rail.load([{ rule_type: "switch_limit", daily_switch_limit: 3, switch_mode: "visit", visit_gap_min: 5 }])""")
    assert page.locator(".rule-sec:not(.off)").count() == 1 and page.inner_text('.rule-sec[data-t="switch_limit"] .sum') == "3 opens per day"
    assert js(page, "() => rail.forms().map(f => f.rule_type)") == ["switch_limit"]
    assert js(page, "() => rail.ticked()") == ["switch_limit"]
    page.evaluate("() => rail.showErrors([{ rule_type: 'switch_limit', field: 'day', message: 'nope' }])")
    assert page.inner_text('.rule-ed[data-type="switch_limit"] [data-zone="periods"]') == "nope"


# ---------------------------------------------------------------- look and speed

@pytest.mark.parametrize("theme", ["dark", "amoled", "light"])
def test_the_rail_fits_every_theme_and_stacks_on_a_narrow_window(page, editors, theme):
    editors(f"?theme={theme}")
    js(page, "(rules) => rail.load(rules)", RULES)
    for width in (1280, 900, 600):
        page.set_viewport_size({"width": width, "height": 900})
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0, f"horizontal scroll at {width}px: {overflow}"
    a, b = page.locator(".rail-side").bounding_box(), page.locator(".rail-panel").bounding_box()
    assert b["y"] > a["y"] + a["height"] - 1 and abs(b["x"] - a["x"]) < 2       # at 600 px the panel is under the rows


def test_editing_is_fast_and_building_the_rail_is_cheap(page, editors, capsys):
    editors()
    build = page.evaluate("() => window.buildMs")
    tick(page, "time_limit")
    t = page.evaluate("""() => {
      const box = document.querySelector('.rule-ed[data-type="time_limit"] [data-f="day"]');
      const t0 = performance.now(); const N = 300;
      for (let i = 0; i < N; i++) { box.value = (i % 20 + 1) + "m"; box.dispatchEvent(new Event("input", { bubbles: true })); }
      return (performance.now() - t0) / N; }""")
    for t_ in ("scheduled", "switch_limit", "permanent", "temporary"):
        tick(page, t_)
    all_open = page.evaluate("""() => {
      const t0 = performance.now(); const N = 200;
      for (let i = 0; i < N; i++) { const b = document.querySelector('.rule-ed[data-type="switch_limit"] [data-f="week"]'); b.value = String(i); b.dispatchEvent(new Event("input", { bubbles: true })); }
      return (performance.now() - t0) / N; }""")
    with capsys.disabled():
        print(f"\n[R19] rail built in {build:.1f} ms (first paint of the component); one keystroke -> summary + sentence: "
              f"{t:.3f} ms (time limit), {all_open:.3f} ms (all 5 blockers on)")
    assert build < 100 and t < 4 and all_open < 4
