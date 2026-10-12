// R14: Screen Time's pure helpers - which tab a link names, when a tab must ask again, where it opens, the data the charts get,
// the keyed rows, and what the shared menu is told about a row.
import assert from "node:assert/strict";
import test from "node:test";
import { REFRESH_MS, RANGES, TABS, defaultsOf, needsLoad, tabFromOpts } from "../../../src/ui/js/pages/screentime.js";
import { donutData, heatData, timelineData, trendData } from "../../../src/ui/js/pages/screentime/overview.js";
import { chipText, create, targetOf } from "../../../src/ui/js/pages/screentime/apps.js";
import { syncRows } from "../../../src/ui/js/pages/screentime/kit.js";
import { tabs } from "../../../src/ui/js/pages/screentime/index.js";

test("the tabs and ranges are the five tabs and four ranges of the old window", () => {
  assert.deepEqual(TABS.map((t) => t[1]), ["Overview", "Apps", "Websites", "Switches", "Calendar"]);
  assert.deepEqual(RANGES, ["Today", "Yesterday", "7 days", "30 days"]);
});

test("a link's tab: only a known id, any case", () => {
  assert.equal(tabFromOpts({ tab: "apps" }), "apps");
  assert.equal(tabFromOpts({ tab: "Websites" }), "websites");
  assert.equal(tabFromOpts({ tab: "nope" }), null);
  assert.equal(tabFromOpts({}), null);
  assert.equal(tabFromOpts(undefined), null);
  assert.equal(tabFromOpts({ tab: 5 }), null);
});

test("where it opens: the Display settings' tab and range, else Overview / Today", () => {
  assert.deepEqual(defaultsOf({ tab: "websites", range: "30 days" }), { tab: "websites", range: "30 days" });
  assert.deepEqual(defaultsOf({ st_tab: "Apps", st_range: "7 days", tabs: ["x"] }), { tab: "apps", range: "7 days" });   // (display_get's words)
  assert.deepEqual(defaultsOf({ tab: "dancing", range: "last year" }), { tab: "overview", range: "Today" });
  assert.deepEqual(defaultsOf(null), { tab: "overview", range: "Today" });
});

test("a tab asks again only when it is ready and stale, for another range, or older than a minute", () => {
  const now = 1_000_000;
  const fresh = { status: "ready", stale: false, range: "Today", loadedAt: now - 10_000 };
  assert.equal(needsLoad(fresh, "Today", now), false);
  assert.equal(needsLoad({ ...fresh, stale: true }, "Today", now), true);
  assert.equal(needsLoad(fresh, "7 days", now), true);
  assert.equal(needsLoad(fresh, "7 days", now, false), false);            // (Calendar has no range)
  assert.equal(needsLoad({ ...fresh, loadedAt: now - REFRESH_MS }, "Today", now), true);
  assert.equal(needsLoad({ ...fresh, loadedAt: now - REFRESH_MS + 1 }, "Today", now), false);
  assert.equal(needsLoad({ ...fresh, status: "loading", stale: true }, "7 days", now), false);
  assert.equal(needsLoad({ ...fresh, status: "new" }, "Today", now), false);
});

test("the Overview hands the charts what they take", () => {
  const points = [{ label: "1", date: "2026-10-01", weekday: 3, value: 5, tip: "t", avg: 4, unlocks: 0 }];
  assert.deepEqual(trendData({ points, goal: 300 }), { points, goal: 300 });
  assert.deepEqual(heatData({ rows: [{ label: "Mon", minutes: [1] }] }), { rows: [{ label: "Mon", minutes: [1] }] });
  assert.deepEqual(timelineData({ segments: [{ start: 1, len: 2, kind: "idle" }], start_hour: 6, colors: { idle: "x" }, names: { idle: "Idle" }, note: "n" }),
    { segments: [{ start: 1, len: 2, kind: "idle" }], startHour: 6, colors: { idle: "x" }, names: { idle: "Idle" } });
  assert.deepEqual(donutData({ parts: [{ key: "a", name: "A", color: "c", value: 60, time: "1 m", tip: "A  1 m" }], center: "1", caption: "total" }),
    { parts: [{ value: 60, color: "c", tip: "A  1 m" }], center: "1", caption: "total" });
});

test("a row tells the shared menu what it is, and the chip says its category", () => {
  const r = { key: "code.exe", name: "Visual Studio Code", path: "C:\\x\\code.exe", cat_name: "Productive" };
  assert.deepEqual(targetOf("app", r), { kind: "app", name: "code.exe", display: "Visual Studio Code", path: "C:\\x\\code.exe" });
  assert.deepEqual(targetOf("site", { key: "docs.python.org", name: "docs.python.org", path: null }), { kind: "site", name: "docs.python.org", display: "docs.python.org", path: null });
  assert.equal(chipText(r), "Productive ▾");
  assert.equal(typeof create("app").mount, "function");
  assert.equal(typeof create("site").render, "function");
});

test("the registry has Overview, Apps and Websites (Switches and Calendar are appended by their own ticket)", () => {
  for (const id of ["overview", "apps", "websites"]) assert.equal(typeof tabs[id], "function", id);
  assert.ok(Object.keys(tabs).every((id) => TABS.some((t) => t[0] === id)));
});

// ---- syncRows: a DOM that is just enough
function fakeHost() {
  const kids = [];
  const mk = (name) => ({ name, get parentNode() { return host; }, get nextSibling() { return kids[kids.indexOf(this) + 1] || null; }, remove() { const i = kids.indexOf(this); if (i >= 0) kids.splice(i, 1); } });
  const host = { kids, mk, get firstChild() { return kids[0] || null; }, insertBefore(node, ref) { const i = kids.indexOf(node); if (i >= 0) kids.splice(i, 1); const j = ref ? kids.indexOf(ref) : kids.length; kids.splice(j, 0, node); } };
  return host;
}

test("syncRows keeps the nodes of rows that stay, adds new ones, drops gone ones and orders them", () => {
  const host = fakeHost();
  let built = 0;
  const build = (r) => { built++; return host.mk(r.key); };
  const update = (node, r) => { node.label = r.label; };
  syncRows(host, [{ key: "a", label: 1 }, { key: "b", label: 2 }], build, update);
  const [a, b] = host.kids;
  assert.equal(built, 2);
  syncRows(host, [{ key: "b", label: 3 }, { key: "c", label: 4 }, { key: "a", label: 5 }], build, update);
  assert.equal(built, 3);
  assert.deepEqual(host.kids.map((n) => n.name), ["b", "c", "a"]);
  assert.equal(host.kids[0], b);
  assert.equal(host.kids[2], a);
  assert.equal(a.label, 5);
  syncRows(host, [{ key: "c", label: 6 }], build, update);
  assert.deepEqual(host.kids.map((n) => n.name), ["c"]);
});
