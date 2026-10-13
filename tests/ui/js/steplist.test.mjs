import test from "node:test";
import assert from "node:assert/strict";

// the editor itself needs a DOM (Chrome tests cover it); its pure helpers import fine in node.
const { estimate, stepSeconds, stepsLooser, newId, hasPhrase } = await import("../../../src/ui/js/steplist.js");

const OPTIONS = {
  kinds: [
    { id: "math", label: "Math", seconds: [6, 10, 20, 35], levels: [{ level: 0, label: "Easy" }, { level: 1, label: "Medium" }, { level: 2, label: "Hard" }, { level: 3, label: "Very hard" }] },
    { id: "memory", label: "Memory grid", seconds: [8, 10, 15], levels: [{ level: 0, label: "Easy" }] },
    { id: "sort", label: "Sorting", seconds: [6, 10, 20], levels: [{ level: 0, label: "Easy" }] },
    { id: "phrase", label: "Typed phrase", levels: [{ level: 60, label: "Medium" }], min_level: 30, max_level: 2000 },
  ],
  max_steps: 6, min_times: 1, max_times: 20, phrase_seconds: { base: 3, per_char: 0.4, grid: 1.3 }, long_seconds: 600,
};
const row = (id, kind, level, times) => ({ id, kind, level, times });

test("estimate: the same numbers as Python's challenges.estimate", () => {
  assert.equal(stepSeconds(row("a", "math", 2, 1), OPTIONS), 20);
  assert.equal(stepSeconds(row("a", "phrase", 60, 1), OPTIONS), 27);
  assert.equal(stepSeconds(row("a", "phrase", 60, 1), OPTIONS, { grid: true }), 27 * 1.3);
  assert.equal(stepSeconds(row("a", "phrase", 60, 1), OPTIONS, { own_length: 100 }), 43);
  assert.deepEqual(estimate([row("a", "math", 0, 2)], OPTIONS), { seconds: 12, text: "under 1 min", long: false, note: "" });
  assert.equal(estimate([row("a", "math", 2, 5), row("b", "memory", 1, 2)], OPTIONS).text, "about 2 min");
  const long = estimate([row("a", "math", 3, 20)], OPTIONS);
  assert.deepEqual([long.seconds, long.text, long.long, long.note], [700, "about 12 min", true, "This will take about 12 min."]);
});

test("stepsLooser: adding, raising and moving are free; removing, lowering, fewer times and another kind are not", () => {
  const saved = [row("a", "math", 1, 3), row("b", "phrase", 60, 1)];
  assert.deepEqual(stepsLooser(saved, [...saved, row("c", "sort", 0, 1)], OPTIONS), []);
  assert.deepEqual(stepsLooser(saved, [saved[1], saved[0]], OPTIONS), []);
  assert.deepEqual(stepsLooser(saved, [row("a", "math", 3, 9), saved[1]], OPTIONS), []);
  assert.deepEqual(stepsLooser(saved, [saved[1]], OPTIONS), ["removed Math ×3"]);
  assert.deepEqual(stepsLooser(saved, [row("a", "math", 0, 3), saved[1]], OPTIONS), ["lowered the level of Math ×3"]);
  assert.deepEqual(stepsLooser(saved, [row("a", "math", 1, 2), saved[1]], OPTIONS), ["fewer times on Math"]);
  assert.deepEqual(stepsLooser(saved, [row("a", "sort", 1, 3), saved[1]], OPTIONS), ["changed Math ×3 to Sorting"]);
});

test("newId and hasPhrase", () => {
  assert.match(newId(), /^[a-z0-9]{8}$/);
  assert.notEqual(newId(), newId());
  assert.equal(hasPhrase([row("a", "math", 0, 1)]), false);
  assert.equal(hasPhrase([row("a", "math", 0, 1), row("b", "phrase", 60, 1)]), true);
});
