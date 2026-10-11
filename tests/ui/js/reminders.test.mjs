import test from "node:test";
import assert from "node:assert/strict";

// the page module only touches the DOM in mount(), so its pure helpers import fine in node.
const { normTime, pad2 } = await import("../../../src/ui/js/pages/reminders.js");

test("normTime: hh and mm boxes become HH:MM, empty stays empty for Python to refuse", () => {
  assert.equal(normTime("7", "5"), "07:05");
  assert.equal(normTime("22", "45"), "22:45");
  assert.equal(normTime(" 9 ", ""), "09:00");
  assert.equal(normTime("", "30"), "00:30");
  assert.equal(normTime("", ""), "");
  assert.equal(normTime(null, undefined), "");
});

test("pad2 pads to two digits", () => {
  assert.equal(pad2(3), "03");
  assert.equal(pad2("12"), "12");
});
