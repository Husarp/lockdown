import test from "node:test";
import assert from "node:assert/strict";
import {
  barChart, trendChart, timelineChart, heatmapChart, donutChart, hourBarChart,
  layoutBars, layoutTrend, layoutTimeline, layoutHeatmap, layoutDonut, layoutHourBars, heatLevel,
} from "../../../src/ui/js/charts.js";

// ---- a tiny fake DOM: just what charts.js uses, and a count of every node made / removed / attribute written
function fakeDoc() {
  const stats = { created: 0, removed: 0, attrWrites: 0 };
  class Node {
    constructor(tag) {
      this.tagName = tag; this.children = []; this.attrs = {}; this.parentNode = null; this._ev = {}; this.style = {}; this._text = ""; stats.created++;
    }
    get firstChild() { return this.children[0] || null; }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; stats.removed++; return c; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    setAttribute(k, v) { stats.attrWrites++; this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(name, fn) { (this._ev[name] ||= []).push(fn); }
    fire(name, e) { (this._ev[name] || []).forEach((f) => f(e)); }
    set textContent(t) { this._text = String(t); }
    get textContent() { return this._text; }
    set className(c) { this.attrs.class = c; }
    get className() { return this.attrs.class; }
    count() { return this.children.reduce((n, c) => n + 1 + c.count(), 0); }
    all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  }
  const head = new Node("head"), body = new Node("body");
  const doc = {
    head, body, documentElement: head,
    createElement: (t) => new Node(t),
    createElementNS: (_ns, t) => new Node(t),
    getElementById: (id) => [...head.all(), ...body.all()].find((n) => n.attrs.id === id) || null,
    defaultView: { innerWidth: 1280, getComputedStyle: () => ({ getPropertyValue: () => "1" }) },
  };
  const host = (width = 600) => { const el = new Node("div"); el.ownerDocument = doc; el.clientWidth = width; body.appendChild(el); return el; };
  return { doc, host, stats, body, head };
}
function mount(make, data, width = 600) {
  const f = fakeDoc();
  globalThis.document = f.doc;                 // (the factories use el.ownerDocument; this is for safety)
  const el = f.host(width);
  const chart = make(el);
  chart.update(data);
  return { ...f, el, chart, count: () => chart.svg.count() };
}
const days = (n, f = (i) => 3600 * (1 + (i % 6))) =>
  Array.from({ length: n }, (_, i) => ({ label: String(i + 1), value: f(i), tip: `day ${i + 1}`, today: i === n - 1, unlocks: i % 9 === 0 ? 1 : 0 }));
const rows7 = (f = (r, h) => (r * 7 + h * 3) % 61) =>
  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label, r) => ({ label, minutes: Array.from({ length: 24 }, (_, h) => f(r, h)) }));

// ---------------------------------------------------------------- element counts
test("30-day bar chart: well under 200 elements", () => {
  const m = mount(barChart, { bars: days(30), goal: 18000, goalLabel: "goal 5 h" });
  assert.ok(m.count() <= 200, `bars ${m.count()}`);
  assert.equal(m.count(), 1 + 30 * 4 + 2);
});

test("30-day trend chart: well under 200 elements", () => {
  const points = days(30).map((d, i) => ({ ...d, avg: i < 6 ? null : 10000 }));
  const m = mount(trendChart, { points, goal: 18000 });
  assert.ok(m.count() <= 200, `trend ${m.count()}`);
});

test("7x24 heatmap: 168 cells + labels + legend, 200 elements at most", () => {
  const m = mount(heatmapChart, { rows: rows7() });
  assert.ok(m.count() <= 200, `heatmap ${m.count()}`);
  const cells = m.chart.svg.all().filter((n) => /ch-h\d/.test(n.attrs.class || "") && n.attrs["data-tip"]);
  assert.equal(cells.length, 168);
});

test("day timeline: neighbouring segments join and a crowded day is capped", () => {
  const segments = [];
  for (let i = 0; i < 400; i++) segments.push({ start: 360 + i * 2, len: 1, kind: i % 2 ? "prod" : "dist" });
  const m = mount(timelineChart, { segments, colors: {}, names: { prod: "Productive" } });
  assert.ok(m.count() <= 200, `timeline ${m.count()}`);
  const joined = layoutTimeline({ segments: [{ start: 400, len: 30, kind: "prod" }, { start: 430, len: 30, kind: "prod" }, { start: 461, len: 10, kind: "prod" }, { start: 480, len: 5, kind: "dist" }] }, 600, 46, 1).geom.segs;
  assert.deepEqual(joined.map((s) => [s.start, s.len, s.kind]), [[400, 71, "prod"], [480, 5, "dist"]]);
});

test("donut and hour bars are small", () => {
  const d = mount(donutChart, { parts: [{ value: 5, color: "var(--cat-prod)", tip: "a" }, { value: 3, color: "var(--cat-dist)", tip: "b" }], center: "8h" });
  assert.ok(d.count() <= 12);
  const counts = {}; for (let h = 0; h < 24; h++) counts[h] = h % 5;
  const b = mount(hourBarChart, { counts });
  assert.ok(b.count() <= 100);
});

// ---------------------------------------------------------------- geometry
test("the goal line sits at the y of the goal value and today is marked", () => {
  const data = { bars: days(7), goal: 18000, goalLabel: "goal 5 h" };
  const { prims, geom } = layoutBars(data, 600, 170, 1);
  const goal = prims.find((p) => p.a.class === "ch-goal");
  const expected = geom.bottom - ((geom.bottom - geom.top) * 18000) / geom.peak;
  assert.ok(Math.abs(+goal.a.y1 - expected) < 0.01 && goal.a.y1 === goal.a.y2);
  assert.equal(goal.a.visibility, "visible");
  assert.equal(geom.goalY, expected);
  const bars = prims.filter((p) => /^ch-bar/.test(p.a.class || ""));
  assert.equal(bars.length, 7);
  assert.ok(bars[6].a.class.includes("now"));
  assert.ok(!bars[0].a.class.includes("now"));
  const over = layoutBars({ bars: [{ label: "a", value: 20000 }, { label: "b", value: 100 }], goal: 18000 }, 300, 170, 1).prims.filter((p) => /^ch-bar/.test(p.a.class || ""));
  assert.ok(over[0].a.class.includes("over") && !over[1].a.class.includes("over"));
});

test("a goal that is higher than every day still fits; no goal hides the line", () => {
  const withGoal = layoutBars({ bars: days(5), goal: 100000 }, 500, 170, 1);
  assert.equal(withGoal.geom.peak, 100000);
  assert.ok(withGoal.geom.goalY >= withGoal.geom.top - 0.01);
  const none = layoutBars({ bars: days(5) }, 500, 170, 1);
  assert.equal(none.prims.find((p) => p.a.class === "ch-goal").a.visibility, "hidden");
  assert.equal(none.geom.goalY, null);
});

test("unlock marks: only days with an unlock get a mark and a tooltip line", () => {
  const { prims } = layoutBars({ bars: days(10) }, 500, 170, 1);
  const marks = prims.filter((p) => p.a.class === "ch-mark");
  assert.deepEqual(marks.map((p) => +p.a.r > 0), days(10).map((d) => d.unlocks > 0));
  assert.match(prims.filter((p) => p.a.class === "ch-hit")[0].a["data-tip"], /Emergency unlock used 1x/);
});

test("trend: line points follow the values, the average skips days without one, the goal line is at its y", () => {
  const points = [{ label: "a", value: 0, avg: null }, { label: "b", value: 7200, avg: 3600 }, { label: "c", value: 3600, avg: 3600 }];
  const { prims, geom } = layoutTrend({ points, goal: 3600, minPeak: 7200 }, 300, 180, 1);
  assert.equal(geom.peak, 7200);
  assert.ok(geom.ys[1] < geom.ys[2] && geom.ys[2] < geom.ys[0]);
  const avg = prims.find((p) => p.a.class === "ch-avg").a.d;
  assert.ok(avg.startsWith("M") && (avg.match(/M/g) || []).length === 1 && !avg.includes(`${geom.xs[0]} `));
  const goal = prims.find((p) => p.a.class === "ch-goal");
  assert.ok(Math.abs(+goal.a.y1 - (geom.bottom - (geom.bottom - geom.top) * 0.5)) < 0.01);
  assert.deepEqual(layoutTrend({ points: [points[0]] }, 300, 180, 1).prims, []);
});

test("heatmap levels: 5 of them at 1 / 15 / 30 / 45 minutes", () => {
  assert.deepEqual([0, 0.5, 1, 14, 15, 29, 30, 44, 45, 60].map(heatLevel), [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]);
  const { prims } = layoutHeatmap({ rows: [{ label: "Mon", minutes: [0, 5, 20, 40, 50, ...Array(19).fill(0)] }] }, 600, 100, 1);
  const cells = prims.filter((p) => p.a["data-tip"]);
  assert.deepEqual(cells.slice(0, 5).map((c) => c.a.class), ["ch-h0", "ch-h1", "ch-h2", "ch-h3", "ch-h4"]);
  assert.match(cells[1].a["data-tip"], /Mon 01:00-02:00 {2}5 min active/);
});

test("donut: the parts add up to one full circle, starting at the top", () => {
  const { prims, geom } = layoutDonut({ parts: [{ value: 1, color: "red", tip: "a" }, { value: 3, color: "blue", tip: "b" }, { value: 0, color: "x", tip: "z" }], center: "4h" }, 110, 110, 1);
  const rings = prims.filter((p) => p.a["stroke-dasharray"]);
  const lens = rings.map((r) => +r.a["stroke-dasharray"].split(" ")[0]);
  assert.ok(Math.abs(lens[0] - geom.C / 4) < 0.02 && Math.abs(lens[1] - (geom.C * 3) / 4) < 0.02 && lens[2] === 0);
  assert.ok(Math.abs(+rings[1].a["stroke-dashoffset"] + lens[0]) < 0.02);
  assert.match(rings[0].a.transform, /^rotate\(-90/);
  assert.equal(prims.at(-2).text, "4h");
  const empty = layoutDonut({ parts: [], center: "0" }, 110, 110, 1).prims;
  assert.equal(empty.filter((p) => p.a["stroke-dasharray"]).length, 0);
});

test("hour bars: the peak is the accent, the next two stand out, 07-22 are always shown", () => {
  const { prims, geom } = layoutHourBars({ counts: { 9: 4, 10: 9, 14: 6, 15: 5, 16: 1 } }, 600, 200, 1);
  assert.deepEqual(geom.ranked.slice(0, 3), [10, 14, 15]);
  const bars = prims.filter((p) => /^ch-bar/.test(p.a.class || ""));
  assert.equal(bars.length, 16);                                          // 07..22
  assert.equal(bars.filter((b) => b.a.class.includes("now")).length, 1);
  assert.equal(bars.filter((b) => b.a.class.includes("over")).length, 2);
  assert.equal(bars[3].a.class, "ch-bar now");                            // 10:00
  assert.equal(layoutHourBars({ counts: {}, emptyText: "No switches yet" }, 600, 200, 1).prims[0].text, "No switches yet");
  assert.equal(layoutHourBars({ counts: { 3: 2, 23: 1 } }, 600, 200, 1).prims.filter((p) => /^ch-bar/.test(p.a.class || "")).length, 21);
  assert.match(prims.find((p) => p.a["data-tip"]).a["data-tip"], /07:00-08:00 {2}0 switches/);
});

// ---------------------------------------------------------------- update patches attributes
test("update() with the same shape rewrites attributes only: no nodes made or removed", () => {
  const m = mount(barChart, { bars: days(30), goal: 18000, goalLabel: "goal 5 h" });
  const made = m.stats.created, removed = m.stats.removed;
  const before = m.chart.svg.all().slice();
  m.chart.update({ bars: days(30, (i) => 1800 * (1 + ((i * 5) % 7))), goal: 14400, goalLabel: "goal 4 h" });
  assert.equal(m.stats.created, made);
  assert.equal(m.stats.removed, removed);
  const after = m.chart.svg.all();
  assert.equal(after.length, before.length);
  assert.ok(after.every((n, i) => n === before[i]));                      // the very same nodes
  assert.equal(after.find((n) => n.attrs.class === "ch-goal").attrs.y1, String(layoutBars({ bars: days(30, (i) => 1800 * (1 + ((i * 5) % 7))), goal: 14400 }, 600, 170, 1).geom.goalY.toFixed(2).replace(/\.?0+$/, "")));
});

test("update() with identical data writes nothing", () => {
  const data = { points: days(30).map((d) => ({ ...d, avg: 9000 })), goal: 18000 };
  const m = mount(trendChart, data);
  const writes = m.stats.attrWrites, made = m.stats.created;
  m.chart.update(JSON.parse(JSON.stringify(data)));
  assert.equal(m.stats.attrWrites, writes);
  assert.equal(m.stats.created, made);
});

test("a changed shape (more days) rebuilds, a different value count does not break anything", () => {
  const m = mount(barChart, { bars: days(7) });
  m.chart.update({ bars: days(30) });
  assert.equal(m.count(), 1 + 30 * 4 + 2);
  m.chart.update({ bars: [] });
  assert.equal(m.count(), 0);
});

test("trend: the hover dot follows the day under the pointer and goes away on leave", () => {
  const m = mount(trendChart, { points: days(10), goal: 0 });
  const dot = () => m.chart.svg.all().find((n) => n.attrs.class === "ch-dot");
  assert.equal(dot().attrs.r, "0");
  const hit = m.chart.svg.all().filter((n) => n.attrs.class === "ch-hit")[4];
  const made = m.count();
  m.chart.svg.fire("pointermove", { target: hit, clientX: 100, clientY: 100 });
  assert.ok(+dot().attrs.r > 0 && dot().attrs.cx !== "0");
  assert.equal(m.count(), made);
  m.chart.svg.fire("pointerleave", {});
  assert.equal(dot().attrs.r, "0");
});

// ---------------------------------------------------------------- tooltip, styles, theme
test("one shared tooltip for all charts, shown from data-tip and hidden away from bars", () => {
  const f = fakeDoc();
  const a = barChart(f.host(), {}), b = donutChart(f.host(), {});
  a.update({ bars: days(5) });
  b.update({ parts: [{ value: 1, color: "red", tip: "x" }], center: "1" });
  const hit = a.svg.all().find((n) => n.attrs.class === "ch-hit");
  a.svg.fire("pointermove", { target: hit, clientX: 50, clientY: 60 });
  const tips = f.body.children.filter((c) => c.attrs.class === "ch-tip");
  assert.equal(tips.length, 1);
  assert.equal(tips[0].hidden, false);
  assert.equal(tips[0].textContent, "day 1\nEmergency unlock used 1x");
  b.svg.fire("pointermove", { target: { getAttribute: () => null }, clientX: 1, clientY: 1 });
  assert.equal(tips[0].hidden, true);
  assert.equal(f.body.children.filter((c) => c.attrs.class === "ch-tip").length, 1);
  assert.equal(f.head.children.filter((c) => c.attrs.id === "ld-chart-css").length, 1);   // the <style> is added once
});

test("colours come from CSS variables, none are hard-coded", () => {
  const f = fakeDoc();
  const m = timelineChart(f.host(), {});
  m.update({ segments: [{ start: 400, len: 60, kind: "prod" }, { start: 500, len: 30, kind: "custom" }], colors: { custom: "var(--k-mode)" } });
  const fills = m.svg.all().map((n) => n.attrs.style).filter(Boolean);
  assert.deepEqual(fills, ["fill:var(--cat-prod)", "fill:var(--k-mode)"]);
  const css = f.head.children[0].textContent;
  assert.ok(!/#[0-9a-fA-F]{3,6}\b/.test(css), "the chart <style> has no hex colours");
  assert.ok(!/gradient|blur\(/.test(css));
  for (const m2 of css.matchAll(/var\((--[\w-]+)/g)) assert.ok(/^--(brand|surface|surface-2|surface-3|line|text|text-2|text-3|cat-\w+|k-\w+|font|fs-\w+|fw-\w+|r-md|shadow-pop)$/.test(m2[1]), m2[1]);
});

test("destroy removes the chart", () => {
  const m = mount(barChart, { bars: days(3) });
  m.chart.destroy();
  assert.equal(m.el.children.length, 0);
});

test("the size follows the container width and the interface scale", () => {
  const wide = layoutBars({ bars: days(10) }, 1000, 170, 1);
  const narrow = layoutBars({ bars: days(10) }, 300, 170, 1);
  const x = (r) => r.prims.filter((p) => /^ch-bar/.test(p.a.class || "")).map((p) => +p.a.d.match(/^M([\d.]+)/)[1]);
  assert.ok(x(wide)[9] > x(narrow)[9]);
  const m = mount(barChart, { bars: days(4) }, 800);
  assert.equal(m.chart.svg.attrs.viewBox, "0 0 800 170");
});
