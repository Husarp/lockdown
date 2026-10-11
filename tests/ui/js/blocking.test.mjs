import test from "node:test";
import assert from "node:assert/strict";

// the modules only touch the DOM in mount() / openEmergency(), so their pure helpers import fine in node.
const ov = await import("../../../src/ui/js/pages/blocking/overview.js");
const bk = await import("../../../src/ui/js/pages/blocking.js");
const em = await import("../../../src/ui/js/emergency.js");
const draft = await import("../../../src/ui/js/draft.js");
const { ld } = await import("../../../src/ui/js/bridge.js");

const item = (id, name, over = {}) => ({ t: "item", id, name, type: "site", icon: { k: "site", n: `${name.toLowerCase()}.com` }, sub: `${name.toLowerCase()}.com · can't load it`,
  notify: null, status: { k: "allowed", text: "Allowed now" }, chips: [], groups: [], low: false, ...over });
const member = (id, name, over = {}) => ({ id, name, type: "site", icon: { k: "site", n: `${name.toLowerCase()}.com` }, status: { k: "allowed", text: "Allowed now" }, chips: [], note: "", low: false, ...over });
const group = (id, name, members, over = {}) => ({ t: "group", id, name, count: members.length, status: { k: "allowed", text: "Allowed now" }, chips: [], members, ...over });

// ---------------------------------------------------------------- overview

test("durationText is rules.duration_text", () => {
  assert.equal(ov.durationText(0), "0m");
  assert.equal(ov.durationText(59), "0m");
  assert.equal(ov.durationText(60), "1m");
  assert.equal(ov.durationText(20 * 60), "20m");
  assert.equal(ov.durationText(3600), "1h 00m");
  assert.equal(ov.durationText(3600 * 5 + 7 * 60), "5h 07m");
  assert.equal(ov.durationText(86400 + 3 * 3600), "1d 3h");
  assert.equal(ov.durationText(-5), "0m");
});

test("statusText counts down only the statuses that have an end", () => {
  const now = Date.UTC(2026, 9, 11, 15, 42, 17);
  const unlocked = { k: "unlocked", text: "Unlocked · 20m left", until: "2026-10-11T16:02:17" };
  assert.equal(ov.statusText(unlocked, now), "Unlocked · 20m left");
  assert.equal(ov.statusText(unlocked, now + 5 * 60000), "Unlocked · 15m left");
  assert.equal(ov.statusText(unlocked, now + 25 * 60000), "Unlocked · 0m left");
  assert.equal(ov.statusText({ k: "paused", text: "x", until: "2026-10-11T17:42:17" }, now), "Paused · 2h 00m left");
  const blocked = { k: "blocked", text: "Blocked (until 18:00)", until: "2026-10-11T18:00:00" };
  assert.equal(ov.statusText(blocked, now), "Blocked (until 18:00)");           // a block says its end time once, it never counts down
  assert.equal(ov.statusText({ k: "unlocked", text: "T" }, now), "T");
});

test("filters: blocked now, running low, apps, websites, groups", () => {
  const blocked = item(1, "Reddit", { status: { k: "blocked", text: "Blocked" } });
  const pending = item(2, "Pend", { status: { k: "pending", text: "Pending · service off" } });
  const low = item(3, "Twitch", { low: true });
  const app = item(4, "Steam", { type: "app" });
  const cat = item(5, "Games", { type: "category" });
  const box = group(6, "Fun", [member(7, "A"), member(8, "B", { status: { k: "blocked", text: "Blocked" } }), member(9, "C", { type: "app" })]);
  const all = [blocked, pending, low, app, cat, box];
  const pick = (f) => all.filter((e) => ov.matchesFilter(e, f)).map((e) => e.name);
  assert.deepEqual(pick("all"), ["Reddit", "Pend", "Twitch", "Steam", "Games", "Fun"]);
  assert.deepEqual(pick("block"), ["Reddit", "Pend", "Fun"]);          // a group is blocked when one of its members is
  assert.deepEqual(pick("low"), ["Twitch"]);
  assert.deepEqual(pick("apps"), ["Steam", "Games", "Fun"]);            // a category covers apps and sites
  assert.deepEqual(pick("sites"), ["Reddit", "Pend", "Twitch", "Games", "Fun"]);
  assert.deepEqual(pick("groups"), ["Fun"]);
  const lowBox = group(10, "Low", [member(1, "A", { low: true })]);
  assert.equal(ov.matchesFilter(lowBox, "low"), true);
  const warnBox = group(11, "Warn", [member(1, "A")], { chips: [{ t: "x", k: "warn", r: "time_limit" }] });
  assert.equal(ov.matchesFilter(warnBox, "low"), true);
});

test("flatten: a box is its header and its members, in order; search finds members; Disabled comes last", () => {
  const entries = [item(1, "Reddit"), group(2, "Fun", [member(3, "Twitch"), member(4, "YouTube")]), item(5, "Steam", { type: "app", sub: "app · steam.exe" })];
  const disabled = [{ t: "item", id: 9, name: "Old", type: "site", icon: { k: "site", n: "old.com" } }, { t: "group", id: 10, name: "Gone" }];
  let rows = ov.flatten(entries, disabled);
  assert.deepEqual(rows.map((r) => r.key), ["i:1", "g:2", "m:2:3", "m:2:4", "i:5", "dh", "d:item:9", "d:group:10"]);
  assert.deepEqual(rows.map((r) => r.k), ["item", "ghead", "member", "member", "item", "dhead", "drow", "drow"]);
  assert.equal(rows[5].n, 2);
  rows = ov.flatten(entries, disabled, { q: "  TWITCH " });
  assert.deepEqual(rows.map((r) => r.key), ["g:2", "m:2:3", "m:2:4"]);       // a member matches: the whole box shows
  rows = ov.flatten(entries, disabled, { q: "steam.exe" });
  assert.deepEqual(rows.map((r) => r.key), ["i:5"]);                           // what it covers is searched too
  rows = ov.flatten(entries, disabled, { q: "old" });
  assert.deepEqual(rows.map((r) => r.key), ["dh", "d:item:9"]);
  rows = ov.flatten(entries, disabled, { filter: "groups" });
  assert.deepEqual(rows.map((r) => r.key), ["g:2", "m:2:3", "m:2:4"]);         // (no Disabled section under a filter)
  assert.deepEqual(ov.flatten(entries, disabled, { q: "nothing at all" }), []);
  assert.deepEqual(ov.flatten([], []), []);
});

test("flatten keeps keys stable across a reload (a row keeps its node)", () => {
  const a = ov.flatten([item(1, "Reddit"), item(2, "Twitch")], []);
  const b = ov.flatten([item(2, "Twitch", { status: { k: "blocked", text: "Blocked" } }), item(1, "Reddit")], []);
  assert.deepEqual(a.map((r) => r.key).sort(), b.map((r) => r.key).sort());
});

test("the search text is made once and is not part of the data", () => {
  const e = item(1, "Reddit");
  assert.ok(ov.hayOf(e).includes("reddit"));
  assert.equal(JSON.stringify(e).includes("_hay"), false);
});

test("chips are escaped and coloured by state", () => {
  const html = ov.chipsHtml([{ t: "Limit: <b>x</b>", k: "danger", r: "time_limit" }, { t: "+ 10 min", k: "allowance", r: "allowance" }, { t: "→ G · disabled", k: "neutral", r: "group" }]);
  assert.ok(html.includes("k-block") && html.includes("k-mode") && html.includes("k-grey"));
  assert.ok(html.includes("&lt;b&gt;x&lt;/b&gt;") && !html.includes("<b>x</b>"));
  assert.equal(ov.chipsHtml([]), "");
});

// ---------------------------------------------------------------- the page shell

test("the Locked strip: nothing when free, a note when closed", () => {
  assert.equal(bk.lockView(null), null);
  assert.equal(bk.lockView({ status: "free" }), null);
  assert.deepEqual(bk.lockView({ status: "closed" }), { closed: true, text: "- only during the allowed hours" });
  const phrase = bk.lockView({ status: "phrase" });
  assert.equal(phrase.closed, false);
  assert.match(phrase.text, /challenge/);
  assert.equal(bk.lockView({ status: "waiting" }).closed, false);
});

test("the five tabs, in the old window's order", () => {
  assert.deepEqual(bk.TABS.map((t) => t[1]), ["Overview", "Groups", "Add", "Site protection", "Calendar"]);
});

// ---------------------------------------------------------------- the emergency dialog

const kinds = [
  { id: "a", minutes: 20, per: "week", per_text: "this week", left: 3, allowed: 3, resets: "2026-10-12T00:00:00", resets_text: "Mon 12 Oct 00:00" },
  { id: "b", minutes: 5, per: "day", per_text: "today", left: 1, allowed: 1, resets: "2026-10-12T00:00:00", resets_text: "Mon 12 Oct 00:00" },
  { id: "c", minutes: 1, per: "day", per_text: "today", left: 0, allowed: 2, resets: "2026-10-11T23:00:00", resets_text: "Sun 11 Oct 23:00" },
];

test("the shortest kind with a use left is chosen first", () => {
  assert.equal(em.pickKind(kinds), "b");
  assert.equal(em.pickKind([kinds[2]]), null);
  assert.equal(em.pickKind([]), null);
});

test("the button words", () => {
  assert.equal(em.unlockLabel(kinds[0]), "Unlock for 20 min");
  assert.equal(em.confirmLabel(kinds[0], false), "Confirm - uses 1 of 3");
  assert.equal(em.confirmLabel(kinds[0], true), "Confirm - uses 1 of 3 this week");
  assert.equal(em.kindText(kinds[0]), "3 of 3 left this week · resets Mon 12 Oct 00:00");
  assert.equal(em.kindText(kinds[2]), "none left · resets Sun 11 Oct 23:00");
  assert.equal(em.nextUse(kinds), "Sun 11 Oct 23:00");
  assert.equal(em.nextUse([]), "");
});

test("the item list: a caption, then its items", () => {
  const a = { id: 1, name: "A" }, b = { id: 2, name: "B" }, c = { id: 3, name: "C" };
  assert.deepEqual(em.listRows([a, b], [c]).map((r) => r.key), ["c:blocked", "i:1", "i:2", "c:open", "i:3"]);
  assert.deepEqual(em.listRows([], [c]).map((r) => r.key), ["c:open", "i:3"]);
  assert.deepEqual(em.listRows([], []), []);
});

// ---------------------------------------------------------------- draft.js

test("a notification setting is sent once, 400 ms after the last change to it", async () => {
  const calls = [];
  ld.call = async (name, ...args) => { calls.push([name, ...args]); return { dirty: true, autosave: false, change_summary: "Notification settings" }; };
  const pushed = [];
  ld.push = (batch) => pushed.push(...batch);
  draft.draftSetSetting("notify.msg.permanent", "a", 30);
  draft.draftSetSetting("notify.msg.permanent", "ab", 30);
  draft.draftSetSetting("notify.format", "inapp", 30);
  await new Promise((r) => setTimeout(r, 120));
  assert.deepEqual(calls, [["draft_set_setting", "notify.msg.permanent", "ab"], ["draft_set_setting", "notify.format", "inapp"]]);
  assert.equal(pushed.length, 2);
  assert.deepEqual(pushed[0], ["draft", { dirty: true, autosave: false, change_summary: "Notification settings", blocks: false }]);   // (the Blocking page does not reload)
});

test("flushSettings sends what waits at once", async () => {
  const calls = [];
  ld.call = async (name, ...args) => { calls.push([name, ...args]); return {}; };
  ld.push = () => {};
  draft.draftSetSetting("notify.cooldown_min", "15", 60_000);
  draft.flushSettings();
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(calls, [["draft_set_setting", "notify.cooldown_min", "15"]]);
});

test("applyState hands the save bar the three things it shows and ignores anything else", () => {
  const pushed = [];
  ld.push = (batch) => pushed.push(...batch);
  draft.applyState({ dirty: true, autosave: false, change_summary: "Removed X", changes: ["a"], result: 1 });
  draft.applyState({ nothing: 1 });
  draft.applyState(null);
  assert.deepEqual(pushed, [["draft", { dirty: true, autosave: false, change_summary: "Removed X" }]]);
});

test("autosaveOn reads the shell's status and says yes until told otherwise", () => {
  ld.status = {};
  assert.equal(draft.autosaveOn(), true);
  ld.status = { draft: { autosave: false } };
  assert.equal(draft.autosaveOn(), false);
  ld.status = { draft: { autosave: true } };
  assert.equal(draft.autosaveOn(), true);
});

test("draftCall gives the call's own answer and shows where the draft stands", async () => {
  const pushed = [];
  ld.status = {};
  ld.push = (batch) => pushed.push(...batch);
  ld.call = async () => ({ dirty: true, autosave: true, change_summary: "x" });
  assert.deepEqual(await draft.draftCall("draft_set_notify", 1, "on"), { dirty: true, autosave: true, change_summary: "x" });
  assert.deepEqual(pushed, [["draft", { dirty: true, autosave: true, change_summary: "x" }]]);
});

test("draftCall after a change that went through the challenge asks Python where the draft stands", async () => {
  const calls = [], pushed = [];
  ld.status = {};
  ld.push = (batch) => pushed.push(...batch);
  ld.call = async (name) => { calls.push(name); return name === "draft_get_state" ? { dirty: false, autosave: true, change_summary: "", changes: [] } : null; };
  assert.equal(await draft.draftCall("draft_remove_item", 1), null);              // (the answer: no state - e.g. what a passed challenge gives)
  assert.deepEqual(calls, ["draft_remove_item", "draft_get_state"]);
  assert.deepEqual(pushed, [["draft", { dirty: false, autosave: true, change_summary: "" }]]);
});
