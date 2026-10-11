import test from "node:test";
import assert from "node:assert/strict";
import { NEW_COLORS, nextColor, nameProblem, isHex, colorOf } from "../../../src/ui/js/categories.js";
import { countText } from "../../../src/ui/js/appbrowser.js";

const builtin = ["productive", "neutral", "games", "distracting"].map((key) => ({ key, name: key[0].toUpperCase() + key.slice(1), builtin: true, color: "#111111", color_light: "#eeeeee" }));
const own = (n) => ({ key: "c" + n, name: "Mine " + n, builtin: false, color: "#222222", color_light: "#222222" });

test("the next colour goes round NEW_COLORS by how many of your own categories there are", () => {
  assert.equal(nextColor(builtin), NEW_COLORS[0]);
  assert.equal(nextColor([...builtin, own(1)]), NEW_COLORS[1]);
  assert.equal(nextColor([...builtin, ...Array.from({ length: 6 }, (_, i) => own(i))]), NEW_COLORS[0]);
});

test("a new name: needed, short, and not taken (any case, spaces tidied)", () => {
  assert.match(nameProblem("", builtin), /name/);
  assert.match(nameProblem("   ", builtin), /name/);
  assert.match(nameProblem("x".repeat(31), builtin), /at most 30/);
  assert.match(nameProblem("NEUTRAL", builtin), /already a category called NEUTRAL/);
  assert.match(nameProblem("  neutral ", builtin), /already/);
  assert.equal(nameProblem("Deep   work", builtin), "");
  assert.equal(nameProblem(null, builtin), "Give the category a name.");
});

test("colours are #rrggbb; the light theme gets the light variant", () => {
  assert.ok(isHex("#a1B2c3"));
  for (const bad of ["red", "#fff", "a1b2c3", "#a1b2c", "#a1b2c3d", null, ""]) assert.ok(!isHex(bad), String(bad));
  assert.equal(colorOf(builtin[0], "light"), "#eeeeee");
  assert.equal(colorOf(builtin[0], "dark"), "#111111");
  assert.equal(colorOf(builtin[0], "amoled"), "#111111");
});

test("the App browser's count line", () => {
  assert.equal(countText(0, 0, true), "Loading apps…");
  assert.equal(countText(1, 1, false), "1 app");
  assert.equal(countText(12, 12, false), "12 apps");
  assert.equal(countText(1502, 60, false), "1502 apps · first 60 shown - type to narrow down");
});
