// R13: the Dashboard's pure helpers (js/pages/dashboard.js) and the Display dialog's reading of its controls.
import assert from "node:assert/strict";
import test from "node:test";
import { hm, minutesLeft, screenDelta, subLine, syncRows } from "../../../src/ui/js/pages/dashboard.js";

test("hm says what stats.hm says", () => {
  assert.equal(hm(0), "0 m");
  assert.equal(hm(59), "0 m");
  assert.equal(hm(52 * 60), "52 m");
  assert.equal(hm(4 * 3600 + 12 * 60), "4 h 12 m");
  assert.equal(hm(3600 + 5 * 60), "1 h 05 m");
  assert.equal(hm(-5), "0 m");
});

test("screenDelta: within a minute of yesterday is about the same, else more is amber and less is green", () => {
  assert.deepEqual(screenDelta(3600, 3600), { note: "about the same as yesterday", tone: "muted" });
  assert.deepEqual(screenDelta(3659, 3600), { note: "about the same as yesterday", tone: "muted" });
  assert.deepEqual(screenDelta(3541, 3600), { note: "about the same as yesterday", tone: "muted" });
  assert.deepEqual(screenDelta(3660, 3600), { note: "1 m more than yesterday", tone: "warn" });
  assert.deepEqual(screenDelta(60, 3600), { note: "59 m less than yesterday", tone: "ok" });
});

test("minutesLeft rounds up and never goes below zero", () => {
  const at = Date.UTC(2026, 9, 11, 15, 42, 17);
  assert.equal(minutesLeft("2026-10-11T16:12:17", at), 30);
  assert.equal(minutesLeft("2026-10-11T16:12:18", at), 31);
  assert.equal(minutesLeft("2026-10-11T15:00:00", at), 0);
});

test("the header line: date, tracking since, mode and phase", () => {
  assert.equal(subLine("Sunday, 11 October", "07:12", null), "Sunday, 11 October  ·  tracking since 07:12");
  assert.equal(subLine("Sunday, 11 October", null, null), "Sunday, 11 October");
  assert.equal(subLine("Sunday, 11 October", "07:12", { name: "Study", ends_at: "2026-10-11T17:00:00", phase: null }),
    "Sunday, 11 October  ·  tracking since 07:12  ·  Study mode until 17:00");
  assert.equal(subLine("D", null, { name: "Focus", ends_at: null, phase: { name: "break", ends_at: "2026-10-11T15:50:00" } }),
    "D  ·  Focus mode, break until 15:50");
  assert.equal(subLine("D", null, { name: "Relax", ends_at: null, phase: null }), "D  ·  Relax mode");
});

// a DOM that is just enough for syncRows: nodes with a parent, siblings and insertBefore
class Node {
  constructor(name) { this.name = name; this.parent = null; this.moves = 0; }
}
class Host {
  constructor() { this.kids = []; this.inserts = 0; }
  get firstChild() { return this.kids[0] || null; }
  insertBefore(node, ref) {
    this.inserts++;
    const i = this.kids.indexOf(node);
    if (i >= 0) this.kids.splice(i, 1);
    const at = ref ? this.kids.indexOf(ref) : this.kids.length;
    this.kids.splice(at < 0 ? this.kids.length : at, 0, node);
    node.parent = this;
  }
}
Object.defineProperty(Node.prototype, "nextSibling", { get() { const k = this.parent.kids; return k[k.indexOf(this) + 1] || null; } });
Node.prototype.remove = function () { if (this.parent) this.parent.kids.splice(this.parent.kids.indexOf(this), 1); this.parent = null; };

test("syncRows keeps nodes by key, moves only what has to move, drops what is gone", () => {
  const host = new Host();
  const built = [];
  const build = (r) => { const n = new Node(r.key); built.push(r.key); return n; };
  const upd = (n, r) => { n.text = r.text; };
  syncRows(host, [{ key: "a", text: 1 }, { key: "b", text: 2 }, { key: "c", text: 3 }], build, upd);
  assert.deepEqual(host.kids.map((n) => n.name), ["a", "b", "c"]);
  const [a, b, c] = host.kids;
  host.inserts = 0;
  syncRows(host, [{ key: "a", text: 9 }, { key: "b", text: 2 }, { key: "c", text: 3 }], build, upd);
  assert.equal(host.inserts, 0);                                    // same rows: no node is touched
  assert.equal(a.text, 9);
  syncRows(host, [{ key: "c", text: 3 }, { key: "a", text: 9 }, { key: "d", text: 4 }], build, upd);
  assert.deepEqual(host.kids.map((n) => n.name), ["c", "a", "d"]);
  assert.equal(host.kids[0], c);
  assert.equal(host.kids[1], a);                                    // the same nodes, reused
  assert.equal(b.parent, null);                                     // b is gone
  assert.deepEqual(built, ["a", "b", "c", "d"]);                    // only d was built later
});
