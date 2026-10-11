import test from "node:test";
import assert from "node:assert/strict";

// the modules only touch the DOM in mount() / the dialogs, so their pure helpers import fine in node.
const g = await import("../../../src/ui/js/pages/blocking/groups.js");
const cm = await import("../../../src/ui/js/customize_member.js");
const ways = await import("../../../src/ui/js/ways.js");

const item = (id, name, over = {}) => ({ id, name, kind: "site", type: "site", icon: { k: "site", n: `${name.toLowerCase()}.com` }, sub: `site · ${name.toLowerCase()}.com`,
  targets: [`${name.toLowerCase()}.com`], hosted: false, item_way: "dns", ...over });
const tick = (it, over = {}) => ({ ...g.memberOfItem(it), ...over });

test("a card's grey line: N members, the first names, and how many more", () => {
  assert.equal(g.membersLine({ count: 0, names: [] }), "no members");
  assert.equal(g.membersLine({ count: 1, names: ["Reddit"] }), "1 member · Reddit");
  assert.equal(g.membersLine({ count: 3, names: ["Game", "Reddit", "YouTube"] }), "3 members · Game, Reddit, YouTube");
  assert.equal(g.membersLine({ count: 11, names: ["a", "b", "c"] }), "11 members · a, b, c and 8 more");
});

test("the way button and the extras button say what the member has", () => {
  assert.equal(g.wayLabel({ kind: "app", way: null }), "Group's way");
  assert.equal(g.wayLabel({ kind: "app", way: "minimize" }), "Its own way: Minimize");
  assert.equal(g.wayLabel({ kind: "app", way: "close,background,internet" }), "Its own way: Close app + its background processes + Block internet");
  assert.equal(g.wayLabel({ kind: "site", way: "dns,close" }), "Its own way: Can't load + Close the tab");
  assert.equal(g.extrasLabel({ overrides: {} }), "Extra limits");
  assert.equal(g.extrasLabel({ overrides: { time_limit: {}, temporary: {} } }), "Extra limits · 2");
});

test("wayText names the flags in a steady order", () => {
  assert.equal(ways.wayText("app", "internet,close"), "Close app + Block internet");
  assert.equal(ways.wayText("site", "back"), "Go back");
  assert.equal(ways.wayText("app", null), "");
});

test("program files are told apart by their extension (display only - Python checks again)", () => {
  for (const exe of ["game.jar", "run.bat", "tool.PY", "x.ps1", "a.vbs"]) assert.equal(g.isHosted(exe), true, exe);
  for (const exe of ["game.exe", "chrome.exe", "", null]) assert.equal(g.isHosted(exe), false, String(exe));
});

test("untyped, the Members box shows only the ticked members by name; typed, what the search found", () => {
  const members = new Map([[ "i:2", tick(item(2, "Reddit")) ], [ "i:1", tick(item(1, "Game", { kind: "app", type: "app" })) ]]);
  assert.deepEqual(g.memberRows(members, "", null).map((r) => r.m.name), ["Game", "Reddit"]);
  assert.deepEqual(g.memberRows(members, "tube", null), []);
  const found = { items: [item(3, "YouTube")], apps: [{ name: "Spotify", exe: "spotify.exe", path: "C:\\s.exe", steam: false }], site: { host: "tube.com" } };
  const rows = g.memberRows(members, "tube", found);
  assert.deepEqual(rows.map((r) => r.k), ["item", "site", "app"]);
  assert.equal(new Set(rows.map((r) => r.key)).size, 3);                        // (stable, distinct keys for the virtual list)
  assert.ok(!rows.some((r) => r.key.startsWith("m:")));
});

test("a member for something new keeps what Python needs to make it", () => {
  const m = g.memberOfTarget({ kind: "app", name: "spotify.exe", display: "Spotify", path: "C:\\Spotify\\spotify.exe" });
  assert.equal(m.id, null);
  assert.deepEqual(m.target, { kind: "app", name: "spotify.exe", display: "Spotify", path: "C:\\Spotify\\spotify.exe" });
  assert.equal(m.key, "n:app:spotify.exe");
  assert.equal(g.memberOfTarget({ kind: "app", name: "game.jar" }).hosted, true);
  const site = g.memberOfTarget({ kind: "site", name: "twitch.tv" });
  assert.deepEqual([site.name, site.kind, site.hosted, site.target.path], ["twitch.tv", "site", false, null]);
});

test("a whole category is a new member that is blocked like an app and sent to Python as a category", () => {
  const m = g.memberOfTarget({ kind: "category", name: "games", display: "Games" });
  assert.deepEqual([m.id, m.key, m.name, m.kind, m.type, m.hosted], [null, "n:category:games", "Games", "app", "category", false]);
  assert.deepEqual(m.icon, { k: "category", n: "games" });
  assert.deepEqual(m.targets, ["games"]);
  const p = g.donePayload(new Map([[m.key, m]]));
  assert.deepEqual(p.pending, [{ kind: "category", name: "games", display: "Games", path: null, overrides: {}, way: null }]);
  const listed = tick(item(4, "Games", { kind: "app", type: "category", targets: ["games"] }));
  assert.equal(g.duplicateOf(new Map([["i:4", listed]]), m.targets).name, "Games");     // (one that is a member already is refused)
});

test("the same thing twice is found by any of its names", () => {
  const members = new Map([["i:2", tick(item(2, "YouTube", { targets: ["youtube.com", "googlevideo.com"] }))]]);
  assert.equal(g.duplicateOf(members, "YouTube.com").name, "YouTube");
  assert.equal(g.duplicateOf(members, ["x.com", "googlevideo.com"]).name, "YouTube");
  assert.equal(g.duplicateOf(members, "reddit.com"), null);
});

test("Done sends forms for the extra rules, ids for what is listed and targets for what is new", () => {
  const members = new Map();
  members.set("i:2", tick(item(2, "Reddit"), { way: "close", overrides: { time_limit: { rule_type: "time_limit", daily_limit_min: 90 } } }));
  members.set("n:site:twitch.tv", g.memberOfTarget({ kind: "site", name: "twitch.tv", display: "Twitch" }));
  const p = g.donePayload(members);
  assert.equal(p.members.length, 1);
  assert.equal(p.members[0].id, 2);
  assert.equal(p.members[0].way, "close");
  const form = p.members[0].overrides.time_limit;
  assert.equal(form.rule_type, "time_limit");
  assert.equal(form.day, "1h30");                                              // the form the editor shows, not a rule dict
  assert.equal("daily_limit_min" in form, false);
  assert.deepEqual(p.pending, [{ kind: "site", name: "twitch.tv", display: "Twitch", path: null, overrides: {}, way: null }]);
  assert.equal(JSON.stringify(p).includes("confirmed"), false);
});

test("the group's own way boxes: closing excludes minimizing, and an app way of 'background' only is none", () => {
  // createWays builds DOM; the pure part is checked in the browser test. Here: the module exports what the page uses.
  assert.equal(typeof ways.createWays, "function");
  assert.equal(cm.createWays, undefined);                                       // (one implementation: js/ways.js)
  assert.deepEqual(ways.APP_WAYS.map((w) => w[0]), ["close", "background", "minimize", "internet"]);
  assert.deepEqual(ways.SITE_WAYS.map((w) => w[0]), ["dns", "close", "back"]);
});
