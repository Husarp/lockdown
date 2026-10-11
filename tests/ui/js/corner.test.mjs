// R07: how a popup's text is cut into blocks (js/corner.js). The DOM side is tested in tests/ui/test_corner.py.
import test from "node:test";
import assert from "node:assert/strict";
import { bodyBlocks, sameCard } from "../../../src/ui/js/corner.js";

test("a plain reminder is one paragraph", () => {
  assert.deepEqual(bodyBlocks("Drink some water."), [{ type: "p", text: "Drink some water." }]);
});

test("a quote under the text is a second paragraph", () => {
  assert.deepEqual(bodyBlocks("Stretch\n\nThe journey of a thousand miles."),
    [{ type: "p", text: "Stretch" }, { type: 'p', text: "The journey of a thousand miles." }]);
});

test("bullet lines become one list, a lead line introduces it", () => {
  const blocks = bodyBlocks("Take 5 min away.\n\nWhile you're up:\n• Rest your eyes\n• Drink water");
  assert.deepEqual(blocks, [
    { type: "p", text: "Take 5 min away." },
    { type: "lead", text: "While you're up:" },
    { type: "ul", items: ["Rest your eyes", "Drink water"] },
  ]);
});

test("a grouped popup is only bullets", () => {
  assert.deepEqual(bodyBlocks("• Water\n• Stretch"), [{ type: "ul", items: ["Water", "Stretch"] }]);
});

test("a long line that ends with a colon is still a paragraph", () => {
  const long = "Remember to do the thing that you promised you would do today at lunch:";
  assert.equal(bodyBlocks(long)[0].type, "p");
});

test("empty and missing text give no blocks", () => {
  assert.deepEqual(bodyBlocks(""), []);
  assert.deepEqual(bodyBlocks(null), []);
});

test("a card is the same card only while its id is", () => {
  assert.ok(sameCard({ id: 3 }, { id: 3 }));
  assert.ok(!sameCard({ id: 3 }, { id: 4 }));
});
