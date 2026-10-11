import test from "node:test";
import assert from "node:assert/strict";

const cal = await import("../../../src/ui/js/pages/blocking/calendar.js");

test("hhmm and span", () => {
  assert.equal(cal.hhmm(0), "00:00");
  assert.equal(cal.hhmm(1350), "22:30");
  assert.equal(cal.span(540, 1020), "09:00–17:00");
});

test("a strip has one bar per stretch, each with its HH:MM-HH:MM tooltip, and ticks only when asked", () => {
  const svg = cal.stripSvg([[0, 420], [1320, 1440]], true);
  assert.equal((svg.match(/<rect/g) || []).length, 2);
  assert.match(svg, /<title>00:00–07:00<\/title>/);
  assert.match(svg, /x="1320" y="3" width="120"/);
  assert.equal((svg.match(/<line/g) || []).length, 11);
  assert.equal((cal.stripSvg([[0, 60]], false).match(/<line/g) || []).length, 0);
  assert.equal((cal.stripSvg([], true).match(/<rect/g) || []).length, 0);
});

test("emergency circles sit at their minute, the now-line only when given", () => {
  assert.match(cal.marksHtml([720]), /left:50%.*emergency unlock at 12:00/);
  assert.equal(cal.marksHtml([]), "");
  assert.match(cal.trackHtml([], [], { ticks: true, nowMin: 360 }), /cal-now" style="left:25%/);
  assert.doesNotMatch(cal.trackHtml([], [], { ticks: false, nowMin: null }), /cal-now/);
});

test("the limits sentence names them, and counts the rest", () => {
  assert.equal(cal.limitsText({ names: [], more: 0 }), "");
  assert.match(cal.limitsText({ names: ["YouTube", "Steam"], more: 3 }), /not drawn: YouTube, Steam and 3 more\.$/);
});

test("the hint depends on the view", () => {
  assert.match(cal.hintText(1), /edit that item/);
  assert.match(cal.hintText(7), /open it in Day view/);
});
