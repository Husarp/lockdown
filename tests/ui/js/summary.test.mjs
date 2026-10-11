// R19: the page's display copy of the rule forms (src/ui/js/rules/summary.js) against the SAME cases the Python authority
// (src/rule_forms.py) runs in tests/test_rebuild_rule_forms.py: tests/fixtures/rule_cases.json.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as S from "../../../src/ui/js/rules/summary.js";

const FIXTURE = JSON.parse(readFileSync(new URL("../../fixtures/rule_cases.json", import.meta.url), "utf8"));
const nowOf = (c) => (c.now ? S.parseStamp(c.now) : undefined);

test("fixture: every form gives the expected rule or error, and the summary sentence", () => {
  assert.ok(FIXTURE.forms.length > 50);
  for (const c of FIXTURE.forms) {
    const opts = { shared: !!c.shared, now: nowOf(c) };
    const r = S.checkForm(c.form, opts);
    if (c.error) {
      assert.ok(r.error, `${c.id}: expected an error`);
      assert.equal(r.error.field, c.error.field, `${c.id}: field`);
      assert.equal(r.error.message, c.error.message, `${c.id}: message`);
    } else {
      assert.deepEqual(r.rule, c.rule, `${c.id}: rule`);
    }
    assert.equal(S.summarizeForm(c.form, opts), c.summary, `${c.id}: summary`);
  }
});

test("fixture: loading a rule into an editor gives the expected form, and it saves back as the same rule", () => {
  for (const c of FIXTURE.loads) {
    const form = S.formFromRule(c.rule, !!c.shared, c.type || null);
    assert.deepEqual(form, c.form, c.id);
  }
  for (const c of FIXTURE.forms) {
    if (!c.rule) continue;
    const form = S.formFromRule(c.rule, !!c.shared);
    assert.deepEqual(S.ruleFromForm(form, { shared: !!c.shared, now: nowOf(c) }), c.rule, `${c.id}: round trip`);
  }
});

test("fixture: lists of forms (display order, a kind given twice, several bad ones, 'tick at least one')", () => {
  for (const c of FIXTURE.lists) {
    const { rules, errors } = S.checkForms(c.forms, { now: S.parseStamp(FIXTURE.now), requireOne: !!c.require_one });
    assert.deepEqual(rules, c.rules, `${c.id}: rules`);
    assert.deepEqual(errors, c.errors, `${c.id}: errors`);
  }
});

test("fixture: the sentence under the editors", () => {
  for (const c of FIXTURE.sentences) {
    const { rules, errors } = S.checkForms(c.forms);
    assert.deepEqual(errors, []);
    assert.equal(S.sentence(c.name, rules), c.sentence, c.id);
  }
});

test("fixture: durations and the little text helpers", () => {
  for (const [text, expected] of FIXTURE.durations) {
    if (typeof expected === "string") assert.throws(() => S.parseDuration(text), { message: expected }, JSON.stringify(text));
    else assert.equal(S.parseDuration(text), expected, JSON.stringify(text));
  }
  for (const [m, t] of FIXTURE.format_duration) assert.equal(S.formatDuration(m), t);
  for (const [s, t] of FIXTURE.duration_text) assert.equal(S.durationText(s), t);
  for (const [d, t] of FIXTURE.days_text) assert.equal(S.daysText(d), t);
  for (const [d, t] of FIXTURE.day_limits_text) assert.equal(S.dayLimitsText(d), t);
});

test("fixture: the names, subtitles, presets and units are the same as Python's", () => {
  const meta = FIXTURE.meta;
  assert.deepEqual(S.RULE_TYPES, meta.rule_types);
  assert.deepEqual(S.RULE_NAMES, meta.names);
  assert.deepEqual(S.RULE_SUBTITLES, meta.subtitles);
  assert.deepEqual(S.DURATIONS, meta.durations);
  assert.deepEqual(S.UNITS, meta.units);
});

test("garbage in a form is an error, never an exception", () => {
  const junk = [null, "x", 5, [], {}, { rule_type: ["scheduled"] }, { rule_type: "scheduled", mode: "allow", windows: "no" },
    { rule_type: "scheduled", mode: "allow", windows: [5], allowance: "0" },
    { rule_type: "scheduled", mode: "allow", windows: [{ days: [9], start: "09:00", end: "10:00" }], allowance: "0" },
    { rule_type: "time_limit", same: false, weekdays: "x" }, { rule_type: "time_limit", same: false, weekdays: ["1h"] },
    { rule_type: "time_limit", day: { a: 1 } }, { rule_type: "time_limit", day: "1".repeat(300) }, { rule_type: "switch_limit", day: "1", counts: "sometimes" },
    { rule_type: "temporary", choice: "custom", amount: "9".repeat(5000), unit: "days" }];
  for (const form of junk) {
    const r = S.checkForm(form);
    assert.ok(r.error && r.error.message, JSON.stringify(form));
  }
});

test("reading a form leaves it alone", () => {
  const form = { rule_type: "scheduled", mode: "block", allowance: "5", windows: [{ days: [3, 1, 1], start: "09:00", end: "17:00" }, { days: [], start: "x", end: "y" }] };
  const copy = JSON.parse(JSON.stringify(form));
  S.ruleFromForm(form);
  assert.deepEqual(form, copy);
});

test("parseStamp: real dates only", () => {
  assert.ok(Number.isFinite(S.parseStamp("2026-10-11 15:42:17")));
  assert.ok(Number.isNaN(S.parseStamp("2026-13-40 10:00:00")));
  assert.ok(Number.isNaN(S.parseStamp("2026-10-11T15:42:17")));
  assert.ok(Number.isNaN(S.parseStamp(null)));
  assert.ok(S.parseStamp("2026-10-11 15:42:18") - S.parseStamp("2026-10-11 15:42:17") === 1000);
});
