// R15: the pure helpers of Screen Time's Switches and Calendar tabs - the chart data, the colours of a row, month stepping, which
// cell shows which day, how many week rows a month needs - and that both tabs are registered.
import assert from "node:assert/strict";
import test from "node:test";
import { hoursData, toneColor } from "../../../src/ui/js/pages/screentime/switches.js";
import { dayAt, stepMonth, usesRange, weekRows } from "../../../src/ui/js/pages/screentime/calendar.js";
import { tabs } from "../../../src/ui/js/pages/screentime/index.js";

test("both tabs are registered", () => {
  assert.equal(typeof tabs.switches, "function");
  assert.equal(typeof tabs.calendar, "function");
});

test("the hours chart gets the 24 counts and the empty words", () => {
  const counts = Array.from({ length: 24 }, (_, h) => h);
  assert.deepEqual(hoursData({ counts }), { counts, emptyText: "No switches yet" });
});

test("a row's tone maps to a colour variable; an unknown tone is the muted one", () => {
  assert.equal(toneColor("warn"), "var(--k-limit)");
  assert.equal(toneColor("ok"), "var(--ok)");
  assert.equal(toneColor("muted"), "var(--text-3)");
  assert.equal(toneColor("what"), "var(--text-3)");
});

test("stepping a month wraps the year both ways", () => {
  assert.deepEqual(stepMonth(2026, 10, -1), { year: 2026, month: 9 });
  assert.deepEqual(stepMonth(2026, 1, -1), { year: 2025, month: 12 });
  assert.deepEqual(stepMonth(2025, 12, 1), { year: 2026, month: 1 });
  assert.deepEqual(stepMonth(2026, 10, 0), { year: 2026, month: 10 });
});

test("the Calendar has no range", () => assert.equal(usesRange, false));

test("a cell shows the day at its place after the blank cells of the first week", () => {
  const month = { offset: 3, days: Array.from({ length: 31 }, (_, i) => ({ day: i + 1 })) };      // October 2026: the 1st is a Thursday
  assert.equal(dayAt(month, 0), null);
  assert.equal(dayAt(month, 2), null);
  assert.equal(dayAt(month, 3).day, 1);
  assert.equal(dayAt(month, 33).day, 31);
  assert.equal(dayAt(month, 34), null);
  assert.equal(dayAt(month, 41), null);
});

test("a month needs 4, 5 or 6 week rows", () => {
  const month = (offset, n) => ({ offset, days: new Array(n).fill({}) });
  assert.equal(weekRows(month(3, 31)), 5);        // Oct 2026
  assert.equal(weekRows(month(0, 28)), 4);        // a February that starts on a Monday
  assert.equal(weekRows(month(6, 31)), 6);        // a 31-day month that starts on a Sunday
  assert.equal(weekRows(month(5, 30)), 5);
});
