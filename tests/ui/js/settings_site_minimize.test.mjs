import test from "node:test";
import assert from "node:assert/strict";
import { SITE_MINIMIZE_INTERVALS, siteMinimizeSeconds } from "../../../src/ui/js/pages/settings.js";
import { chipsOf } from "../../../src/ui/js/pages/modes/editor.js";
import { defaultWays, toggleWay, waysProblem, wayText } from "../../../src/ui/js/ways.js";

test("settings offers only whole seconds from 2 through 10", () => {
  assert.deepEqual(SITE_MINIMIZE_INTERVALS, [2, 3, 4, 5, 6, 7, 8, 9, 10]);
  for (const seconds of SITE_MINIMIZE_INTERVALS) assert.equal(siteMinimizeSeconds(String(seconds)), seconds);
  for (const invalid of ["", "1", "11", "2.5", "undefined"]) assert.equal(siteMinimizeSeconds(invalid), null);
});

test("site Minimize combines with DNS and a foreground action", () => {
  assert.deepEqual(defaultWays("site"), ["dns"]);
  const ways = toggleWay("site", ["dns", "back"], "minimize", true);
  assert.deepEqual(ways, ["dns", "back", "minimize"]);
  assert.deepEqual(toggleWay("site", ways, "close", true), ["dns", "close", "minimize"]);
  assert.equal(wayText("site", "dns,minimize"), "Can't load + Minimize");
  assert.match(waysProblem("site", []), /Minimize/);
});

test("mode extras show the selected site way", () => {
  const [chip] = chipsOf({ items: [], groups: [], extra: [
    { kind: "site", name: "Example", targets: ["example.org"], block_type: "dns,minimize" },
  ] }, { items: [], groups: [] });
  assert.equal(chip.way, "Can't load + Minimize");
});
