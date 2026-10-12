import test from "node:test";
import assert from "node:assert/strict";

// R27: the pure parts of the Notifications and Settings pages (the modules only touch the DOM when mounted).
const nt = await import("../../../src/ui/js/pages/notifications.js");
const st = await import("../../../src/ui/js/pages/settings.js");
const theme = await import("../../../src/ui/js/theme.js");

test("a message box saves what is typed, and an empty box means the default message", () => {
  assert.equal(nt.messageValue("  Nope {site}  ", "default"), "Nope {site}");
  assert.equal(nt.messageValue("   ", "default"), "default");
  assert.equal(nt.messageValue(null, "default"), "default");
});

test("warn minutes stay between 1 and 60 and an empty box waits", () => {
  assert.equal(nt.warnMinutes("5"), 5);
  assert.equal(nt.warnMinutes(" 99 "), 60);
  assert.equal(nt.warnMinutes("0"), 1);
  assert.equal(nt.warnMinutes(""), null);
  assert.equal(nt.warnMinutes("abc"), null);
});

test("switches are sent as 1 / 0 and menus as [value, label]", () => {
  assert.equal(nt.flagValue(true), "1");
  assert.equal(nt.flagValue(false), "0");
  assert.deepEqual(nt.menuOptions([1, 5], (m) => `${m} min`), [["1", "1 min"], ["5", "5 min"]]);
});

test("the Locked strip says what Anti-Bypass says, and nothing when it is free", () => {
  assert.equal(st.lockView(null), null);
  assert.equal(st.lockView({ status: "free" }), null);
  assert.deepEqual(st.lockView({ status: "phrase" }), { closed: false, text: "- changes that loosen a block need the challenge" });
  assert.deepEqual(st.lockView({ status: "waiting" }).closed, false);
  assert.deepEqual(st.lockView({ status: "closed" }), { closed: true, text: "- only during the allowed hours" });
});

test("an accent that is none of the eight is the Custom one", () => {
  const accents = { Orange: "#DB5126", Blue: "#2F6FEB" };
  assert.equal(st.accentIsPreset("#db5126", accents), true);
  assert.equal(st.accentIsPreset("#2F6FEB", accents), true);
  assert.equal(st.accentIsPreset("#123456", accents), false);
  assert.equal(st.accentIsPreset("#123456", null), false);
});

test("the emergency meter has one bar per use, lit while left", () => {
  assert.deepEqual(st.meterBars(2, 3), [true, true, false]);
  assert.deepEqual(st.meterBars(0, 2), [false, false]);
  assert.equal(st.kindMeterText({ left: 2, allowed: 3, per_text: "this week", resets_text: "Mon 12 Oct 00:00" }), "2 of 3 left this week · resets Mon 12 Oct 00:00");
  assert.equal(st.usedText({ used: [] }), "");
  assert.equal(st.usedText({}), "");
  assert.equal(st.usedText({ used: ["Mon 12 Oct 14:05–14:25", "Tue 13 Oct 09:00–09:20"] }), "Used Mon 12 Oct 14:05–14:25, Tue 13 Oct 09:00–09:20");
});

test("the reset time boxes become HH:MM, empty stays empty", () => {
  assert.equal(st.normTime("4", "0"), "04:00");
  assert.equal(st.normTime("", "30"), "00:30");
  assert.equal(st.normTime(" 21 ", "5"), "21:05");
  assert.equal(st.normTime("", ""), "");
});

test("the emergency settings go to Python as numbers, a new kind without an id", () => {
  const cfg = st.emergencyConfig(true, [{ id: "t1", uses: "3", per: "week", minutes: "20" }, { uses: "1", per: "month", minutes: "60" }]);
  assert.deepEqual(cfg, { enabled: true, tiers: [{ id: "t1", uses: 3, per: "week", minutes: 20 }, { uses: 1, per: "month", minutes: 60 }] });
  assert.equal("id" in cfg.tiers[1], false);
  assert.deepEqual(st.emergencyConfig(0, []), { enabled: false, tiers: [] });
});

test("the daily goal reads Off or N h", () => {
  assert.equal(st.goalLabel(0), "Off");
  assert.equal(st.goalLabel(5), "5 h");
});

test("Reduce motion sets and clears data-motion on the root, and leaves it alone when not given", () => {
  const root = { dataset: {}, style: { setProperty() {}, getPropertyValue: () => "" } };
  const win = { document: { documentElement: root }, localStorage: { setItem() {} }, Event: class { constructor(n) { this.type = n; } }, dispatchEvent() {}, matchMedia: () => ({ matches: true, addEventListener() {} }) };
  theme.applyLook({ motion: true }, win);
  assert.equal(root.dataset.motion, "off");
  theme.applyLook({ accent: "#2F6FEB" }, win);
  assert.equal(root.dataset.motion, "off");
  theme.applyLook({ motion: false }, win);
  assert.equal(root.dataset.motion, undefined);
});
