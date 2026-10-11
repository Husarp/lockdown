import test from "node:test";
import assert from "node:assert/strict";
import { visibleRange } from "../../../src/ui/js/vlist.js";

test("visibleRange: the rows in view plus overscan, clamped to the data", () => {
  assert.deepEqual(visibleRange(0, 520, 52, 10000, 6), [0, 16]);          // 10 rows in view + 6
  assert.deepEqual(visibleRange(52 * 100, 520, 52, 10000, 6), [94, 116]);
  assert.deepEqual(visibleRange(52 * 9990, 520, 52, 10000, 6), [9984, 10000]);   // clamped at the end
  assert.deepEqual(visibleRange(1e9, 520, 52, 100, 6), [100, 100]);               // scrolled past the data: nothing
});

test("visibleRange: empty list, no height or a bad row height give nothing", () => {
  assert.deepEqual(visibleRange(0, 520, 52, 0), [0, 0]);
  assert.deepEqual(visibleRange(0, 0, 52, 100), [0, 0]);
  assert.deepEqual(visibleRange(0, 520, 0, 100), [0, 0]);
  assert.deepEqual(visibleRange(0, 520, NaN, 100), [0, 0]);
});

test("visibleRange: a partly visible row counts, and the window never needs more than ~30 rows at 36 px", () => {
  assert.deepEqual(visibleRange(10, 100, 36, 1000, 0), [0, 4]);           // rows 0..3 touch [10, 110)
  const [a, b] = visibleRange(123456, 800, 36, 10000, 6);
  assert.ok(b - a <= 35);
});

test("visibleRange: consecutive scroll steps share almost all rows (what makes node reuse cheap)", () => {
  let prev = visibleRange(0, 600, 36, 10000);
  for (let top = 36; top < 36 * 300; top += 36) {
    const cur = visibleRange(top, 600, 36, 10000);
    const shared = Math.max(0, Math.min(prev[1], cur[1]) - Math.max(prev[0], cur[0]));
    assert.ok(shared >= cur[1] - cur[0] - 1, `step to ${top} shares ${shared} of ${cur[1] - cur[0]}`);
    prev = cur;
  }
});
