import test from "node:test";
import assert from "node:assert/strict";

// the page module only touches the DOM in mount(), so its pure helpers import fine in node.
const { bannerView, normTime, pauseNote, statusSig, PROTECTS } = await import("../../../src/ui/js/pages/antibypass.js");

const NOW = "2026-10-11T15:42:17";

test("normTime: the hh and mm boxes become HH:MM, empty stays empty for Python to refuse", () => {
  assert.equal(normTime("7", "5"), "07:05");
  assert.equal(normTime("18", "00"), "18:00");
  assert.equal(normTime("", "30"), "00:30");
  assert.equal(normTime("", ""), "");
  assert.equal(normTime(null, undefined), "");
});

test("banner: off", () => {
  const v = bannerView({ kind: "off" }, NOW, null);
  assert.deepEqual([v.tone, v.title, v.lock, v.unlock, v.bar], ["k-grey", "Anti-Bypass is off", false, false, null]);
});

test("banner: closed says the hours and the next chance (a time today, else the day)", () => {
  const b = { kind: "closed", hours: "Sunday 18:00-20:00", next_open: "2026-10-11T18:00:00" };
  const v = bannerView(b, NOW, null);
  assert.equal(v.tone, "k-block");
  assert.equal(v.title, "Closed right now");
  assert.equal(v.text, "Changes like this are only possible Sunday 18:00-20:00. Next chance: 18:00.");
  assert.match(bannerView({ ...b, next_open: "2026-10-18T18:00:00" }, NOW, null).text, /Next chance: Sun 18 Oct 18:00\./);
  assert.equal(bannerView({ ...b, next_open: null }, NOW, null).text, "Changes like this are only possible Sunday 18:00-20:00.");
});

test("banner: unlocked counts down m:ss and the bar drains", () => {
  const b = { kind: "unlocked", total_sec: 300, ends_at: "2026-10-11T15:47:17" };
  const full = bannerView(b, NOW, 300);
  assert.equal(full.title, "Unlocked for 5:00 - loosening changes are allowed");
  assert.equal(full.bar, 1);
  assert.deepEqual([full.tone, full.lock, full.unlock], ["k-ok", true, false]);
  const half = bannerView(b, NOW, 150.4);
  assert.equal(half.title, "Unlocked for 2:31 - loosening changes are allowed");
  assert.ok(Math.abs(half.bar - 0.5) < 0.01);
  assert.equal(bannerView(b, NOW, -3).bar, 0);                        // past the end: never negative
});

test("banner: waiting says when it opens and the bar fills", () => {
  const b = { kind: "waiting", total_sec: 600, ends_at: "2026-10-11T15:52:17" };
  const v = bannerView(b, NOW, 600);
  assert.equal(v.title, "Passed - unlocks in 10:00 (at 15:52)");
  assert.equal(v.bar, 0);
  assert.equal(v.tone, "k-limit");
  assert.equal(bannerView(b, NOW, 150).bar, 0.75);
  assert.equal(bannerView(b, NOW, 0).bar, 1);
});

test("banner: locked offers Unlock to edit, free names the end of the hours", () => {
  const locked = bannerView({ kind: "locked", summary: "Loosening a block needs: typing a 60-character phrase" }, NOW, null);
  assert.deepEqual([locked.tone, locked.title, locked.unlock, locked.lock], ["k-block", "Locked", true, false]);
  assert.equal(locked.text, "Loosening a block needs: typing a 60-character phrase");
  const free = bannerView({ kind: "free", summary: "Loosening a block needs: only Sunday 18:00-20:00", ends_at: "2026-10-11T20:00:00" }, NOW, 1000);
  assert.deepEqual([free.tone, free.title], ["k-ok", "Free inside the allowed hours"]);
  assert.match(free.text, /· until 20:00$/);
});

test("pause note", () => {
  assert.equal(pauseNote({ running: false }, 0), "Your blocks are on.");
  assert.equal(pauseNote({ running: true, until: "2026-10-11T16:42:17", silent: false }, 60), "Your blocks are paused until 16:42 (1 h left).");
  assert.equal(pauseNote({ running: true, until: "2026-10-11T16:12:17", silent: false }, 25), "Your blocks are paused until 16:12 (25 min left).");
  assert.equal(pauseNote({ running: true, until: "2026-10-11T16:42:17", silent: true }, 0), "Your blocks are paused until 16:42 - notifications are silenced.");
});

test("statusSig: the page's data and a status push compare equal when nothing changed", () => {
  const push = { antibypass: { status: "phrase", unlocked_until: null, waiting_until: null, next_open: null, active: true }, pause: null, off: null };
  const page = { antibypass: { status: "phrase", unlocked_until: null, waiting_until: null }, pause: null, off: false };
  assert.equal(statusSig(push), statusSig(page));
  const paused = { ...push, pause: { started: "a", until: "2026-10-11T16:12:17", silent: true } };
  assert.notEqual(statusSig(paused), statusSig(push));
  assert.notEqual(statusSig({ ...push, off: "2026-10-11 15:00:00" }), statusSig(push));
  assert.equal(statusSig({ ...push, off: "2026-10-11 15:00:00" }), statusSig({ ...page, off: true }));
  assert.notEqual(statusSig({ ...push, antibypass: { ...push.antibypass, unlocked_until: "2026-10-11T15:47:17" } }), statusSig(push));
});

test("the list of what needs the challenge is the old window's eight lines", () => {
  assert.equal(PROTECTS.length, 8);
  assert.ok(PROTECTS.some((t) => t.startsWith("Pausing your blocks")));
});
