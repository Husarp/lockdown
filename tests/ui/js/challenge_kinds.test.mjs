import test from "node:test";
import assert from "node:assert/strict";
import { showsProgress, progressText, progressParts, stripModel, runHeaderHtml, shuffleKeepIds, digitsOnly, missedAt, reshufflesPool, SHORT } from "../../../src/ui/js/challenge_kinds.js";
import { noteText } from "../../../src/ui/js/challenge.js";

const step = (kind, times, label) => ({ kind, level: 0, times, label });
const run = (steps, at = 0, done = 0) => ({ steps, step: at, done });
const three = [step("math", 5, "Math, Hard"), step("memory", 2, "Memory grid, Easy"), step("phrase", 1, "Typed phrase, Short")];

test("a single phrase done once shows no progress at all", () => {
  const one = run([step("phrase", 1, "Typed phrase, Short")]);
  assert.equal(showsProgress(one), false);
  assert.equal(progressText(one), "");
  assert.deepEqual(stripModel(one), []);
  assert.equal(runHeaderHtml(one), "");
  assert.equal(showsProgress(null), false);
  assert.equal(showsProgress({}), false);
});

test("the progress line says the step, the kind and the round", () => {
  assert.equal(progressText(run(three, 0, 2)), "Step 1 of 3 · Math, Hard · 3 of 5");
  assert.equal(progressText(run(three, 1, 0)), "Step 2 of 3 · Memory grid, Easy · 1 of 2");
  assert.equal(progressText(run(three, 2, 0)), "Step 3 of 3 · Typed phrase, Short");
  assert.equal(progressText(run([step("math", 3, "Math, Easy")], 0, 1)), "Math, Easy · 2 of 3");
  assert.deepEqual(progressParts(run(three, 0, 0)), { step: "Step 1 of 3", label: "Math, Hard", round: "1 of 5" });
});

test("the strip marks done, current and still to come", () => {
  assert.deepEqual(stripModel(run(three, 1, 1)), [
    { text: "Math ×5", state: "done", small: "" },
    { text: "Memory ×2", state: "cur", small: "1/2" },
    { text: "Phrase ×1", state: "todo", small: "" },
  ]);
  assert.equal(SHORT.sort, "Sorting");
  const html = runHeaderHtml(run(three, 1, 1));
  assert.match(html, /ch-stp done">✓ Math ×5/);
  assert.match(html, /ch-stp cur">Memory ×2 <small>1\/2/);
  assert.match(html, /<b>Step 2 of 3<\/b>/);
});

test("html in a label can't get in", () => {
  const evil = run([step("math", 2, "<img src=x>"), step("math", 1, "b")]);
  assert.ok(!runHeaderHtml(evil).includes("<img"));
});

test("shuffling keeps every id exactly once and never changes the input", () => {
  const ids = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
  const copy = ids.slice();
  const seen = new Set();
  for (let n = 0; n < 50; n++) {
    const out = shuffleKeepIds(ids);
    assert.deepEqual(out.slice().sort((a, b) => a - b), copy);
    seen.add(out.join());
  }
  assert.deepEqual(ids, copy);
  assert.ok(seen.size > 10);
  assert.deepEqual(shuffleKeepIds([], Math.random), []);
  assert.deepEqual(shuffleKeepIds([7], () => 0.99), [7]);
  assert.deepEqual(shuffleKeepIds([1, 2, 3], () => 0), [2, 3, 1]);       // a fixed random gives a fixed order
});

test("the answer box keeps digits only", () => {
  assert.equal(digitsOnly("12ab-3 "), "123");
  assert.equal(digitsOnly("−×"), "");
  assert.equal(digitsOnly(null), "");
  assert.equal(digitsOnly("1".repeat(30)).length, 15);
});

test("a memory tap that can't be right is a miss", () => {
  assert.equal(missedAt([3, 5, 9], false, [5]), false);
  assert.equal(missedAt([3, 5, 9], false, [5, 9, 3]), false);
  assert.equal(missedAt([3, 5, 9], false, [5, 4]), true);              // not lit
  assert.equal(missedAt([9, 3, 5], true, [9]), false);
  assert.equal(missedAt([9, 3, 5], true, [9, 5]), true);               // lit, but not next
  assert.equal(missedAt([9, 3, 5], true, [9, 3, 5]), false);
  assert.equal(missedAt([1], false, []), false);
});

test("only Hard sorting moves the tiles around", () => {
  assert.deepEqual([0, 1, 2].map(reshufflesPool), [false, false, true]);
  assert.equal(reshufflesPool(undefined), false);
});

test("the note under the title", () => {
  assert.equal(noteText({ unlock_min: 5 }), "Passing unlocks changes like these for 5 minutes.");
  assert.equal(noteText({ unlock_min: 5 }, 3), "Passing all of them unlocks changes like these for 5 minutes.");
  assert.equal(noteText({ unlock_min: 5, plain: true }), "");
  assert.equal(noteText({ unlock_min: 5, note: "Do it to dismiss." }), "Do it to dismiss.");
  assert.equal(noteText({ unlock_min: 5, note: "" }), "");
  assert.equal(noteText({ plain: true, note: "Pass it." }), "Pass it.");
  assert.equal(noteText(null), "");
});
