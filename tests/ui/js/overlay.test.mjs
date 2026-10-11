// R08: the numbers on the full-screen cover (js/overlay.js). The DOM side is tested in tests/ui/test_overlay.py.
import test from "node:test";
import assert from "node:assert/strict";
import { waitLabel, clockText, countdownWake, clockWake, roleOf, kindLine } from "../../../src/ui/js/overlay.js";

test("the Dismiss wait counts whole seconds up and wakes just past the second that changes it", () => {
  assert.deepEqual(waitLabel(25000), { text: "Dismiss (25)", ready: false, wake: 1015 });      // the same numbers gui/screens.dismiss_label gave
  assert.deepEqual(waitLabel(24400), { text: "Dismiss (25)", ready: false, wake: 415 });
  assert.equal(waitLabel(200).text, "Dismiss (1)");
  assert.deepEqual(waitLabel(0), { text: "Dismiss", ready: true, wake: null });
  assert.deepEqual(waitLabel(-3000), { text: "Dismiss", ready: true, wake: null });
  assert.deepEqual(waitLabel(null), { text: "Dismiss", ready: true, wake: null });
});

test("the bedtime clock shows hours and minutes and wraps at midnight", () => {
  assert.equal(clockText(23 * 3600 + 5 * 60 + 59), "23:05");
  assert.equal(clockText(0), "00:00");
  assert.equal(clockText(86400 + 61), "00:01");
  assert.equal(clockText(-60), "23:59");
});

test("the clock wakes at the next minute, the break countdown at the next second", () => {
  assert.equal(clockWake(120), 60015);
  assert.equal(clockWake(150.5), 29515);
  assert.equal(countdownWake(300000), 1015);
  assert.equal(countdownWake(59500), 515);
  assert.equal(countdownWake(0), null);
});

test("buttons look by what they do", () => {
  assert.equal(roleOf("dismiss"), "primary");
  assert.equal(roleOf("disable"), "ghost");
  assert.equal(roleOf("emergency"), "emerg");
  assert.equal(roleOf("something"), "plain");
});

test("a kind says what is left, or when it comes back", () => {
  assert.equal(kindLine({ left: 2, allowed: 3, per_text: "today", resets_text: "Fri 9 Oct 00:00" }), "2 of 3 left today");
  assert.equal(kindLine({ left: 0, allowed: 1, per_text: "this week", resets_text: "Mon 12 Oct 00:00" }), "none left · resets Mon 12 Oct 00:00");
});
