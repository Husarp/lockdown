import test from "node:test";
import assert from "node:assert/strict";

// the modules only touch the DOM in mount() / openXxx(), so their pure helpers import fine in node.
const pl = await import("../../../src/ui/js/pages/blocking/protection.js");
const wl = await import("../../../src/ui/js/wordlist.js");
const ml = await import("../../../src/ui/js/manuallist.js");

const NOW = Date.UTC(2026, 9, 11, 15, 42, 17);

// ---------------------------------------------------------------- the lists

test("agoText is the old window's words", () => {
  const at = (iso) => pl.agoText(iso, NOW);
  assert.equal(at("2026-10-11T15:42:00"), "just now");
  assert.equal(at("2026-10-11T15:41:00"), "just now");                  // 1 min: still "just now"
  assert.equal(at("2026-10-11T15:40:00"), "2 min ago");
  assert.equal(at("2026-10-11T14:00:00"), "102 min ago");
  assert.equal(at("2026-10-11T13:42:00"), "2 h ago");                    // 120 min
  assert.equal(at("2026-10-10T15:42:00"), "24 h ago");
  assert.equal(at("2026-10-11T15:50:00"), "just now");                   // (a clock that is a little behind)
});

test("listStatus is the 5-condition table", () => {
  const s = (row) => pl.listStatus(row, NOW);
  assert.deepEqual(s({ state: "downloading", text: "12.3 of 44.8 MB (part 1 of 2)" }), { count: "…", text: "downloading 12.3 of 44.8 MB (part 1 of 2)" });
  assert.deepEqual(s({ state: "updated", count: 1234567, updated: "2026-10-11T15:00:00" }), { count: "1,234,567", text: "updated 42 min ago" });
  assert.deepEqual(s({ state: "update_failed", count: 99 }), { count: "99", text: "update failed · retrying in 1 h" });
  assert.deepEqual(s({ state: "failed" }), { count: "–", text: "download failed · retrying in 1 h" });
  assert.deepEqual(s({ state: "waiting" }), { count: "–", text: "waiting for the service" });
  assert.deepEqual(s({ state: "off" }), { count: "", text: "off" });
});

test("checkText says what the old window said", () => {
  const lists = [{ name: "Scam", on: true, mine: false }, { name: "Gambling", on: false, mine: false }, { name: "Distractions", on: true, mine: true }];
  assert.deepEqual(pl.checkText({ host: "x.com", allowed_by: "x.com", lists: [] }),
    { text: "x.com is allowed anyway by you.", tone: "ok", act: { kind: "unallow", host: "x.com", label: "Block again" } });
  assert.equal(pl.checkText({ host: "a.x.com", allowed_by: "x.com", lists }).text, "a.x.com is allowed anyway by you (through x.com).");
  assert.equal(pl.checkText({ host: "a.x.com", allowed_by: "x.com", lists }).act.host, "x.com");
  const hit = pl.checkText({ host: "x.com", allowed_by: null, lists });
  assert.equal(hit.text, "x.com is on the Scam, Gambling (list is off), Distractions (your list) lists.");
  assert.deepEqual([hit.tone, hit.act], ["bad", { kind: "allow", host: "x.com", label: "Allow anyway" }]);
  assert.equal(pl.checkText({ host: "x.com", allowed_by: null, lists: [lists[0]] }).text, "x.com is on the Scam list.");
  assert.deepEqual(pl.checkText({ host: "x.com", allowed_by: null, lists: [] }), { text: "x.com isn't on any list.", tone: "ok", act: null });
});

test("skipsWords: only a listed allowed site skips the blocked words; older data has none", () => {
  assert.equal(pl.skipsWords(["a.com"], "a.com"), true);
  assert.equal(pl.skipsWords(["a.com"], "b.com"), false);
  assert.equal(pl.skipsWords(undefined, "a.com"), false);
  assert.equal(pl.skipsWords("a.com", "a.com"), false);
});

test("counts and info lines", () => {
  assert.equal(pl.siteCount(0), "0 sites");
  assert.equal(pl.siteCount(1), "1 site");
  assert.equal(pl.siteCount(20000), "20,000 sites");
  assert.equal(pl.wordsInfo("ready", { total: 110, off: 0 }), "110 words");
  assert.equal(pl.wordsInfo("ready", { total: 110, off: 3 }), "110 words (3 off)");
  assert.equal(pl.wordsInfo("words", { count: 0 }), "none yet");
  assert.equal(pl.wordsInfo("words", { count: 1 }), "1 word");
  assert.equal(pl.wordsInfo("words", { count: 4 }), "4 words");
  assert.equal(pl.wordsInfo("exceptions", { count: 0 }), "none");
  assert.equal(pl.wordsInfo("exceptions", { count: 1 }), "1 entry");
  assert.equal(pl.wordsInfo("exceptions", { count: 2 }), "2 entries");
});

test("the interval falls back to daily", () => {
  const iv = [[6, "every 6 hours"], [12, "every 12 hours"], [24, "daily"], [168, "weekly"]];
  assert.equal(pl.intervalValue(12, iv), 12);
  assert.equal(pl.intervalValue(5, iv), 24);
});

// a tiny DOM for reconcile: the nodes only need children / insertBefore / remove / nextSibling / firstChild
class Node {
  constructor(key) { this._key = key; this.parent = null; }
  remove() { this.parent.kids.splice(this.parent.kids.indexOf(this), 1); this.parent = null; }
  get nextSibling() { const k = this.parent.kids; return k[k.indexOf(this) + 1] || null; }
}
class Parent {
  constructor() { this.kids = []; this.moves = 0; }
  get children() { return this.kids; }
  get firstChild() { return this.kids[0] || null; }
  insertBefore(node, before) {
    this.moves++;
    if (node.parent) node.remove();
    node.parent = this;
    const at = before ? this.kids.indexOf(before) : this.kids.length;
    this.kids.splice(at, 0, node);
  }
}

test("reconcile keeps nodes, moves only what is out of order, drops what is gone", () => {
  const parent = new Parent(), made = [];
  const create = (key) => { made.push(key); return new Node(key); };
  const update = (node, item) => { node.item = item; };
  pl.reconcile(parent, ["a", "b", "c"], (x) => x, create, update);
  const [a, b, c] = parent.kids;
  assert.deepEqual(parent.kids.map((n) => n._key), ["a", "b", "c"]);
  parent.moves = 0;
  pl.reconcile(parent, ["a", "b", "c"], (x) => x, create, update);
  assert.equal(parent.moves, 0);                                        // nothing out of order: nothing touched
  assert.deepEqual(made, ["a", "b", "c"]);
  pl.reconcile(parent, ["c", "a", "d"], (x) => x, create, update);
  assert.deepEqual(parent.kids.map((n) => n._key), ["c", "a", "d"]);
  assert.equal(parent.kids[0], c);
  assert.equal(parent.kids[1], a);                                      // the same nodes
  assert.deepEqual(made, ["a", "b", "c", "d"]);
  assert.equal(b.parent, null);
  pl.reconcile(parent, [], (x) => x, create, update);
  assert.equal(parent.kids.length, 0);
});

// ---------------------------------------------------------------- words

test("normalizeWord lower-cases and collapses spaces", () => {
  assert.equal(wl.normalizeWord("  Free   SPINS "), "free spins");
  assert.equal(wl.normalizeWord("   "), "");
});

test("fuzzyScore is search.py", () => {
  assert.equal(wl.fuzzyScore("", "anything"), 0);
  assert.equal(wl.fuzzyScore("porn", "porn*"), 0);
  assert.equal(wl.fuzzyScore("xx", "xxx"), 0);
  assert.equal(wl.fuzzyScore("xyz", "xxx"), null);                      // short words allow no typo
  assert.equal(wl.fuzzyScore("hentai", "hentai*"), 0);
  assert.equal(wl.fuzzyScore("hentia", "hentai*"), 1);                  // swapped letters count as one
  assert.equal(wl.fuzzyScore("lethl", "lethal company"), 1);
  assert.equal(wl.fuzzyScore("ruchac", "ruchać"), 0);                   // accents don't matter
  assert.equal(wl.fuzzyScore("lodz", "łódź"), 0);
  assert.equal(wl.fuzzyScore("sex chat", "sex chat"), 0);
  assert.equal(wl.fuzzyScore("sex dating", "sex chat"), null);
  assert.equal(wl.fuzzyScore("masturbaz", "masturba*"), 1);
  // the same answers as search.score in Python (checked by hand against it)
  assert.equal(wl.fuzzyScore("escort srvice", "escort service*"), 1);
  assert.equal(wl.fuzzyScore("brazers", "brazzers"), 1);
  assert.equal(wl.fuzzyScore("nude pic", "nude pics"), 0);
  assert.equal(wl.fuzzyScore("orgi", "orgia"), 0);
  assert.equal(wl.fuzzyScore("xxx", "xvideos"), null);
});

test("rankWords: all with no query, matches best first, ties in their own order", () => {
  const words = ["sex chat", "sexchat", "sex cam*", "hentai*", "escort service*"];
  assert.equal(wl.rankWords(words, ""), words);
  assert.deepEqual(wl.rankWords(words, "sex"), ["sex chat", "sexchat", "sex cam*"]);
  assert.deepEqual(wl.rankWords(words, "hentia"), ["hentai*"]);
  assert.deepEqual(wl.rankWords(words, "zzz"), []);
  assert.deepEqual(wl.rankWords(["b typo", "a exact"], "exact"), ["a exact"]);
  assert.deepEqual(wl.rankWords(["xxxxy one", "xxxx two"], "xxxx two"), ["xxxx two"]);
});

test("addWord puts a new word on top and ignores empty ones and duplicates", () => {
  assert.deepEqual(wl.addWord(["a"], "  B  "), ["b", "a"]);
  assert.equal(wl.addWord(["a"], "A"), null);
  assert.equal(wl.addWord(["a"], "   "), null);
});

test("countText", () => {
  assert.equal(wl.countText("ready", new Array(110).fill("w"), new Set(["a", "b"])), "108 of 110 words blocked");
  assert.equal(wl.countText("own", ["a"], new Set()), "1 entry");
  assert.equal(wl.countText("own", [], new Set()), "0 entries");
  assert.equal(wl.countText("own", ["a", "b"], new Set()), "2 entries");
});

// ---------------------------------------------------------------- your own list

test("withSite puts a site on top once, withoutSite takes it out", () => {
  const entries = [["a.com", ""]], seen = new Set(["a.com"]);
  assert.equal(ml.withSite(entries, seen, "a.com", "again"), null);
  assert.deepEqual(ml.withSite(entries, seen, "b.com", "  The   B  "), [["b.com", "The B"], ["a.com", ""]]);
  assert.ok(seen.has("b.com"));
  assert.equal(ml.withoutSite(entries, seen, "nope.com"), false);
  assert.equal(ml.withoutSite(entries, seen, "a.com"), true);
  assert.deepEqual(entries, [["b.com", "The B"]]);
  assert.ok(!seen.has("a.com"));
});

test("a site picked from the suggestions puts all its host names in, the main one on top, the ones already there skipped", () => {
  const entries = [["old.reddit.com", ""]], seen = new Set(["old.reddit.com"]);
  assert.equal(ml.withSiteHosts(entries, seen, ["reddit.com", "old.reddit.com", "redd.it"], "Reddit"), 2);
  assert.deepEqual(entries.map((e) => e[0]), ["reddit.com", "redd.it", "old.reddit.com"]);
  assert.deepEqual(entries[0], ["reddit.com", "Reddit"]);
  assert.equal(ml.withSiteHosts(entries, seen, ["reddit.com", "redd.it"], "Reddit"), 0);          // (all there already)
});

test("countText of a list", () => {
  assert.equal(ml.countText(0), "0 sites");
  assert.equal(ml.countText(1), "1 site");
  assert.equal(ml.countText(20000), "20,000 sites");
});
