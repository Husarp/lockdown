import test from "node:test";
import assert from "node:assert/strict";
import { CHIPS, ONLY, PERIODS, buildRows, dayHeading, dayOf, timeOf, toneOf } from "../../../src/ui/js/applog.js";

const ev = (id, ts, kind = "change", text = "x", source = "agent") => ({ id, ts, kind, text, source });

test("dayHeading: Today, Yesterday, a weekday and date, and the year only when it is not this year", () => {
  assert.equal(dayHeading("2026-10-12", "2026-10-12"), "Today");
  assert.equal(dayHeading("2026-10-11", "2026-10-12"), "Yesterday");
  assert.equal(dayHeading("2026-10-10", "2026-10-12"), "Saturday 10 October");
  assert.equal(dayHeading("2026-12-31", "2027-01-01"), "Yesterday");                    // (across the new year)
  assert.equal(dayHeading("2025-03-02", "2026-10-12"), "Sunday 2 March 2025");
});

test("buildRows: one heading before the first event of each day, events in the order given (newest first)", () => {
  const rows = buildRows([ev(5, "2026-10-12 14:00:00"), ev(4, "2026-10-12 09:00:00"), ev(3, "2026-10-11 23:59:59")], "2026-10-12");
  assert.deepEqual(rows.map((r) => r.k), ["d5", "e5", "e4", "d3", "e3"]);
  assert.equal(rows[0].head, "Today");
  assert.equal(rows[3].head, "Yesterday");
  assert.deepEqual(buildRows([], "2026-10-12"), []);
});

test("buildRows: every row has a key of its own even when the clock went back and a day comes twice (the virtual list reuses nodes by key)", () => {
  const many = Array.from({ length: 500 }, (_, i) => ev(1000 - i, `2026-10-${String(1 + (i % 12)).padStart(2, "0")} 10:00:00`));
  const keys = buildRows(many, "2026-10-12").map((r) => r.k);
  assert.equal(new Set(keys).size, keys.length);
});

test("toneOf: problems are red, Anti-Bypass events teal, the rest plain", () => {
  for (const k of ["problem", "crash", "killed", "window_failed", "hosts_repaired", "update_check_failed", "challenge_failed"]) assert.equal(toneOf(k), "bad", k);
  for (const k of ["challenge_passed", "antibypass_change", "off", "emergency", "tray_exit"]) assert.equal(toneOf(k), "guard", k);
  for (const k of ["change", "start", "updated", "backup_import"]) assert.equal(toneOf(k), "", k);
});

test("dayOf / timeOf cut the stamp Python writes", () => {
  assert.equal(dayOf("2026-10-12 14:03:11"), "2026-10-12");
  assert.equal(timeOf("2026-10-12 14:03:11"), "14:03:11");
  assert.equal(timeOf(undefined), "");
});

test("the filter chips are the six the page promises, in order", () => {
  assert.deepEqual(CHIPS.map((c) => c[1]), ["All", "Changes", "Updates", "Problems", "Anti-Bypass", "Bypass used"]);
});

test("the Only… menu and the period", () => {
  assert.deepEqual(ONLY.map((c) => c[0]), ["", "emergency", "pauses", "switched", "tray_exit", "challenges_passed", "challenges_failed"]);
  assert.deepEqual(PERIODS.map((p) => p[0]), [0, 1, 7, 30]);
});
