import test from "node:test";
import assert from "node:assert/strict";
import { applyLook, contrast, currentLook, deriveAccent, luminance, mix, parseHex, resolveTheme, scaleValue, toHex, CACHE_KEY } from "../../../src/ui/js/theme.js";

const SWATCHES = { Orange: "#DB5126", Red: "#D1342F", Pink: "#D63F8C", Purple: "#7C4DDB", Blue: "#2F6FEB", Teal: "#12948A", Green: "#23945A", Yellow: "#C99A0E" };

test("parseHex / toHex round trip, and refuse what isn't #RRGGBB", () => {
  assert.deepEqual(parseHex("#DB5126"), [219, 81, 38]);
  assert.equal(toHex([219, 81, 38]), "#DB5126");
  for (const bad of ["red", "#12345", "#1234567", "DB5126", "", null, undefined, "#GG0000"]) assert.equal(parseHex(bad), null);
  assert.equal(toHex([300, -4, 0.4]), "#FF0000");
});

test("WCAG maths: black on white is 21", () => {
  assert.ok(Math.abs(contrast([0, 0, 0], [255, 255, 255]) - 21) < 1e-9);
  assert.equal(luminance([0, 0, 0]), 0);
  assert.deepEqual(mix([0, 0, 0], [100, 200, 50], 0.5), [50, 100, 25]);
});

test("every accent swatch gets readable text in every theme", () => {
  for (const [name, hex] of Object.entries(SWATCHES)) {
    for (const theme of ["light", "dark", "amoled"]) {
      const v = deriveAccent(hex, theme);
      assert.equal(v["--brand"], hex, `${name}: --brand is the accent itself`);
      const brand = parseHex(v["--brand"]);
      assert.ok(contrast(parseHex(v["--on-brand"]), brand) >= 3.5, `${name}/${theme}: text on the accent`);
      assert.ok(contrast(parseHex(v["--on-brand-soft"]), parseHex(v["--brand-soft"])) >= 4.5, `${name}/${theme}: text on the tint`);
      assert.notEqual(v["--brand-hover"], v["--brand"]);
    }
  }
});

test("the tint is light on light and dark on dark/amoled, and amoled's is darkest", () => {
  const l = luminance(parseHex(deriveAccent("#2F6FEB", "light")["--brand-soft"]));
  const d = luminance(parseHex(deriveAccent("#2F6FEB", "dark")["--brand-soft"]));
  const a = luminance(parseHex(deriveAccent("#2F6FEB", "amoled")["--brand-soft"]));
  assert.ok(l > 0.6 && d < 0.1 && a < d);
});

test("white text on the accent unless the accent is too pale for it", () => {
  assert.equal(deriveAccent("#2F6FEB", "light")["--on-brand"], "#FFFFFF");
  assert.equal(deriveAccent("#C99A0E", "light")["--on-brand"], "#1A1918");
});

test("a bad accent falls back to the default instead of throwing", () => {
  assert.equal(deriveAccent("nonsense", "dark")["--brand"], "#DB5126");
});

test("the defaults in tokens.css are what deriveAccent gives for Orange", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../../src/ui/css/tokens.css", import.meta.url), "utf8");
  for (const theme of ["light", "dark", "amoled"]) {
    for (const [k, v] of Object.entries(deriveAccent("#DB5126", theme))) {
      if (theme === "amoled" && (k === "--brand" || k === "--brand-hover" || k === "--on-brand")) continue;   // inherited from dark
      assert.ok(css.includes(`${k}: ${v};`), `${theme} ${k}: ${v} is in tokens.css`);
    }
  }
});

test("interface size: Auto is 1, the percentages are what they say, unknown is 1", () => {
  assert.deepEqual(["auto", "100", "90", "80", "70", 70, "Auto", "50", "x"].map(scaleValue), [1, 1, 0.9, 0.8, 0.7, 0.7, 1, 1, 1]);
});

test("System follows the OS; a fixed theme doesn't; unknown means dark", () => {
  assert.equal(resolveTheme("system", true), "dark");
  assert.equal(resolveTheme("system", false), "light");
  assert.equal(resolveTheme("amoled", false), "amoled");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("neon", false), "dark");
});

function fakeWin(systemDark = false) {
  const style = {};
  const store = {};
  const events = [];
  const mq = { matches: systemDark, listeners: [], addEventListener(_t, fn) { this.listeners.push(fn); } };
  return {
    mq, store, events, style,
    document: { documentElement: { dataset: {}, style: { setProperty(k, v) { style[k] = v; }, getPropertyValue(k) { return style[k] || ""; } } } },
    matchMedia: () => mq,
    localStorage: { setItem(k, v) { store[k] = v; } },
    dispatchEvent(e) { events.push(e.type); },
    Event: class { constructor(type) { this.type = type; } },
  };
}

test("applyLook sets the attribute and the variables, caches them, and tells lists when the size changed", () => {
  const w = fakeWin();
  const r = applyLook({ theme: "amoled", accent: "#2f6feb", scale: "80" }, w);
  assert.equal(w.document.documentElement.dataset.theme, "amoled");
  assert.equal(w.style["--brand"], "#2F6FEB");
  assert.equal(w.style["--s"], "0.8");
  assert.deepEqual(w.events, ["ld:scale"]);
  const cache = JSON.parse(w.store[CACHE_KEY]);
  assert.equal(cache.pref, "amoled");
  assert.equal(cache.s, "0.8");
  assert.equal(cache.vars.light["--brand"], "#2F6FEB");
  assert.equal(r.theme, "amoled");
  applyLook({ accent: "nonsense" }, w);                   // ignored
  assert.equal(currentLook().accent, "#2F6FEB");
  applyLook({ scale: "80" }, w);                          // unchanged: no second event
  assert.deepEqual(w.events, ["ld:scale"]);
});

test("System theme re-applies when the OS changes", () => {
  const w = fakeWin(false);
  applyLook({ theme: "system" }, w);
  assert.equal(w.document.documentElement.dataset.theme, "light");
  w.mq.matches = true;
  w.mq.listeners.forEach((fn) => fn());
  assert.equal(w.document.documentElement.dataset.theme, "dark");
});
