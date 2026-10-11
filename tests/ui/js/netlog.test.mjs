import test from "node:test";
import assert from "node:assert/strict";

// the page only touches the DOM in mount(): its pure helpers import fine in node.
const nl = await import("../../../src/ui/js/pages/netlog.js");

const net = (k, m, n, extra = {}) => ({ k, m, t: m.slice(11), exe: "chrome.exe", app: "Chrome", host: "a.com", site: "a.com", n, rule: "–", port: 443, b: 0, w: 0, l: 0, i: 0, reg: "a.com", ...extra });
const blk = (k, m, i) => ({ ...net(k, m, 1), b: 1, i, app: "Blocked visit", exe: "" });
const keyed = (rows) => new Map(rows.map((r) => [r.k, r]));
const keys = (rows) => rows.map((r) => r.k);

test("before: newest minute first, then connections (busiest first), then blocked visits (newest first)", () => {
  const a = net("a", "2026-10-11 15:42", 3), b = net("b", "2026-10-11 15:42", 1), v = blk("v", "2026-10-11 15:42", 7), w = blk("w", "2026-10-11 15:42", 8);
  const old = net("o", "2026-10-11 15:41", 9);
  assert.ok(nl.before(a, b) && !nl.before(b, a));
  assert.ok(nl.before(b, v) && !nl.before(v, b));
  assert.ok(nl.before(w, v) && !nl.before(v, w));
  assert.ok(nl.before(v, old) && !nl.before(old, a));
  assert.ok(!nl.before(a, a));
});

test("mergeRows puts a new row where Python would and updates a known one in place", () => {
  const rows = [net("a", "2026-10-11 15:42", 3), net("b", "2026-10-11 15:42", 1), net("c", "2026-10-11 15:41", 2)];
  const map = keyed(rows);
  const same = rows[1];
  const r = nl.mergeRows(rows, map, [net("n", "2026-10-11 15:42", 2), net("b", "2026-10-11 15:42", 4), net("z", "2026-10-11 15:43", 1)], { complete: true });
  assert.deepEqual(keys(rows), ["z", "a", "n", "b", "c"]);       // (the old order of a, b is kept: b's count changed in place)
  assert.equal(rows[0].k, "z");                                   // a newer minute goes on top
  assert.equal(map.get("b"), same);                               // the same object, updated
  assert.equal(same.n, 4);
  assert.deepEqual([r.added, r.updated], [2, 1]);
});

test("mergeRows counts the rows that went in at or above the row in view", () => {
  const rows = Array.from({ length: 10 }, (_, i) => net("r" + i, "2026-10-11 15:30", 10 - i));
  const map = keyed(rows);
  const incoming = [net("x1", "2026-10-11 15:42", 1), net("x2", "2026-10-11 15:42", 1), net("x3", "2026-10-11 15:42", 1)];
  const r = nl.mergeRows(rows, map, incoming, { anchor: 4, complete: false });
  assert.equal(r.above, 3);                                       // all three are newer than anything loaded: they move row 4 down by 3
  assert.deepEqual(keys(rows).slice(0, 4), ["x1", "x2", "x3", "r0"]);
  const at_top = nl.mergeRows(rows, map, [net("y", "2026-10-11 15:43", 1)], { anchor: 0 });
  assert.equal(at_top.above, 1);
});

test("a row that belongs among the rows not loaded yet is left out; with everything loaded it goes last", () => {
  const rows = [net("a", "2026-10-11 15:42", 1)];
  const map = keyed(rows);
  nl.mergeRows(rows, map, [net("old", "2026-10-11 15:10", 1)], { complete: false });
  assert.deepEqual(keys(rows), ["a"]);
  nl.mergeRows(rows, map, [net("old", "2026-10-11 15:10", 1)], { complete: true });
  assert.deepEqual(keys(rows), ["a", "old"]);
});

test("rows older than the cutoff (the hour moved on) are dropped from the end", () => {
  const rows = [net("a", "2026-10-11 15:42", 1), net("b", "2026-10-11 14:59", 1), net("c", "2026-10-11 14:41", 1)];
  const map = keyed(rows);
  const r = nl.mergeRows(rows, map, [], { cutoff: "2026-10-11 14:43" });
  assert.deepEqual(keys(rows), ["a", "b"]);
  assert.equal(r.dropped, 1);
  assert.ok(!map.has("c"));
});

test("a blocked visit lands after the connections of its minute and before the older minute", () => {
  const rows = [net("a", "2026-10-11 15:42", 2), net("c", "2026-10-11 15:41", 5)];
  const map = keyed(rows);
  nl.mergeRows(rows, map, [blk("v", "2026-10-11 15:42", 3)], { complete: true });
  assert.deepEqual(keys(rows), ["a", "v", "c"]);
});

test("olderText is the old button's text", () => {
  assert.equal(nl.olderText(125), "Show more (125 older)");
});
