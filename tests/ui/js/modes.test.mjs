import test from "node:test";
import assert from "node:assert/strict";

// the page modules only touch the DOM in mount() / createEditor(), so their pure helpers import fine in node.
const { activeSig, modeIcon, normTime, nowView, phaseTitle, startView, untilArg } = await import("../../../src/ui/js/pages/modes.js");
const { chipsOf, iconOfTarget } = await import("../../../src/ui/js/pages/modes/editor.js");

const at = (iso) => Date.UTC(...iso.match(/\d+/g).map((n, i) => (i === 1 ? +n - 1 : +n)));
const NOW = at("2026-10-11T15:42:17");

test("nowView: nothing on", () => {
  assert.deepEqual(nowView(null, NOW), { name: "No mode", text: "No mode is on.", stop: null, due: NaN });
});

test("nowView: until a time counts down h:mm:ss, then m:ss", () => {
  const a = { id: "work", name: "Work", ends_at: "2026-10-11T17:00:00", locked: false, by_schedule: false, phase: null };
  const v = nowView(a, NOW);
  assert.equal(v.text, "Until 17:00 - 1:17:43 left");
  assert.deepEqual(v.stop, { label: "Stop" });
  assert.equal(v.due, at("2026-10-11T17:00:00"));
  assert.equal(nowView(a, at("2026-10-11T16:30:00")).text, "Until 17:00 - 30:00 left");
  assert.equal(nowView(a, at("2026-10-11T17:00:05")).text, "Until 17:00 - 0:00 left");        // never negative
});

test("nowView: a locked mode says when the lock ends", () => {
  const a = { name: "Work", ends_at: "2026-10-11T16:42:17", locked: true, by_schedule: false, phase: null };
  assert.equal(nowView(a, NOW).stop.label, "Stop (locked until 16:42)");
});

test("nowView: Pomodoro words, round and a phase that is not 'focus'", () => {
  const a = { name: "Focus", ends_at: "2026-10-11T17:52:17", locked: false, by_schedule: false,
    phase: { name: "focus", ends_at: "2026-10-11T16:07:17", round: 1, rounds: 4 } };
  assert.equal(nowView(a, NOW).text, "Focus - 25:00 left (round 1 of 4)");
  assert.equal(nowView(a, NOW).due, at("2026-10-11T16:07:17"));
  const lb = { ...a, phase: { name: "long break", ends_at: "2026-10-11T17:52:17", round: 4, rounds: 4 } };
  assert.equal(nowView(lb, at("2026-10-11T17:50:00")).text, "Long break - 2:17 left (round 4 of 4)");
});

test("nowView: a mode on by its schedule has no Stop and says until when", () => {
  const a = { name: "Study", ends_at: "2026-10-11T23:59:00", locked: false, by_schedule: true, phase: null };
  const v = nowView(a, NOW);
  assert.equal(v.text, "On by its schedule until 23:59");
  assert.equal(v.stop, null);
  assert.equal(nowView({ ...a, ends_at: null }, NOW).text, "On by its schedule");
});

test("nowView: until you stop it never goes stale", () => {
  const v = nowView({ name: "Relax", ends_at: null, locked: false, by_schedule: false, phase: null }, NOW);
  assert.equal(v.text, "Until you stop it");
  assert.ok(Number.isNaN(v.due));
});

test("activeSig tells a changed mode from a repeated push", () => {
  const a = { id: "work", locked: true, by_schedule: false, ends_at: "x", phase: null, name: "Work" };
  assert.equal(activeSig(a), activeSig({ ...a, name: "Renamed", blocking: false }));
  assert.notEqual(activeSig(a), activeSig({ ...a, locked: false }));
  assert.notEqual(activeSig(a), activeSig({ ...a, phase: { name: "focus", ends_at: "y", round: 1 } }));
  assert.equal(activeSig(null), "");
});

test("startView: a Pomodoro has no lengths and says how long it runs", () => {
  assert.deepEqual(startView({ name: "Focus", pomodoro: { work: 25 }, run_min: 130 }), { title: "Start Focus", lengths: false, runs: "Runs 130 min (all rounds)." });
  assert.deepEqual(startView({ name: "Work", pomodoro: null, run_min: 0 }), { title: "Start Work", lengths: true, runs: "" });
});

test("untilArg: what the length buttons send", () => {
  assert.equal(untilArg("30", "17:00"), 30);
  assert.equal(untilArg("120", ""), 120);
  assert.equal(untilArg("at", "09:30"), "09:30");
  assert.equal(untilArg("stop", "09:30"), null);
});

test("normTime and the small ones", () => {
  assert.equal(normTime("7", "5"), "07:05");
  assert.equal(normTime("", ""), "");
  assert.equal(phaseTitle("long break"), "Long break");
  assert.equal(modeIcon("focus"), "target");
  assert.equal(modeIcon("custom3"), "layers");
});

test("chipsOf: groups, items and extras; what is gone from the blocklist has no chip", () => {
  const brief = { groups: [{ id: 2, name: "Games" }], items: [{ id: 5, name: "YouTube", target: "youtube.com googlevideo.com", kind: "site" },
    { id: 6, name: "Steam", target: "steam.exe", kind: "app" }] };
  const mode = { groups: [2, 9], items: [5, 6, 77], extra: [{ kind: "site", name: "Reddit", targets: ["reddit.com"] }, { kind: "app", name: "Discord", targets: ["discord.exe"] }] };
  const chips = chipsOf(mode, brief);
  assert.deepEqual(chips.map((c) => c.label), ["Group: Games", "YouTube", "Steam", "Reddit", "Discord"]);
  assert.deepEqual(chips.map((c) => c.key), ["g:2", "i:5", "i:6", "x:0", "x:1"]);
  assert.deepEqual(chips[1].icon, ["site", "youtube.com"]);
  assert.deepEqual(chips[4].icon, ["app", "discord.exe"]);
  assert.equal(chips[0].icon, null);
});

test("iconOfTarget", () => {
  assert.deepEqual(iconOfTarget("site", "a.com b.com"), ["site", "a.com"]);
  assert.equal(iconOfTarget("category", "games"), null);
  assert.equal(iconOfTarget("site", ""), null);
});
