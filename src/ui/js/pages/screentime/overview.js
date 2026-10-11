// Screen Time > Overview: four numbers, the trend, the day timeline (Today / Yesterday), the active-hours heatmap, the last days
// as bars, the categories donut, and the streaks. Built once (mount); render(data) only updates: text when it changed, the
// charts with update() (same shape -> attribute writes, no new nodes), the legend and streak rows keyed.
//
// render() is two steps so the first one is short: the four numbers and the trend now (what the eye goes to first, painted
// at once), then the heatmap, bars, donut, timeline and streaks right after that frame has been painted. Each step reads
// the widths of its charts BEFORE it writes anything, so the browser lays the page out at most once for the step, not once
// per chart. `data-charts` on the tab's element says "pending" until the second step is done (tests wait for "done").
import { barChart, donutChart, heatmapChart, timelineChart, trendChart } from "../../charts.js";
import { el, esc, ic, setText } from "../../ui.js";
import { legendItem, legendUpdate, syncRows } from "./kit.js";

const STYLE = `<style>
.st-ov .card-h .sub-note { color: var(--text-2); font-size: var(--fs-sm); white-space: nowrap; }
.st-ov .card-h .sub-note.t-ok { color: var(--ok); } .st-ov .card-h .sub-note.t-bad { color: var(--k-block); }
.st-ov .card-b { display: grid; gap: var(--sp-2); }
.st-ov .legend span { --k: var(--text-3); }
.st-ov .legend i { flex: none; }
.st-donut { display: flex; align-items: center; gap: var(--sp-4); }
.st-donut .legend { flex-direction: column; gap: var(--sp-1); flex: 1; min-width: 0; }
.st-donut .legend span { display: flex; justify-content: space-between; gap: var(--sp-3); }
.st-donut .legend span > b { margin-left: auto; font-weight: var(--fw-semibold); color: var(--text); font-variant-numeric: tabular-nums; }
.st-streak { display: flex; align-items: center; gap: var(--sp-3); }
.st-streak + .st-streak { border-top: 1px solid var(--line); }
</style>`;

const CARDS = ["active", "idle", "focus", "sessions"];

// ---------------------------------------------------------------- data for the charts (plain, node tests)

/** The reply's trend as the trend chart wants it. */
export const trendData = (t) => ({ points: t.points, goal: t.goal });
/** The reply's bars as the bar chart wants them (goalLabel is the dashed line's text). */
export const barsData = (b) => ({ bars: b.bars, goal: b.goal, goalLabel: b.goal_label });
export const heatData = (h) => ({ rows: h.rows });
/** The day timeline: segments + the category colours and names (Python sends CSS colours that follow the theme). */
export const timelineData = (t) => ({ segments: t.segments, startHour: t.start_hour, colors: t.colors, names: t.names });
export const donutData = (d) => ({ parts: d.parts.map((p) => ({ value: p.value, color: p.color, tip: p.tip })), center: d.center, caption: d.caption });

// ---------------------------------------------------------------- the tab

let root = null;
const charts = {};
const chartOpts = {};                          // chart key -> the options object it was made with (charts.js reads opts.width at every draw)
const chartHost = {};                          // chart key -> the element it draws into
const widthOf = (key) => chartHost[key].clientWidth;
let later = null;                              // the second step of the newest render() that has not run yet
let frame = 0;
const q = (k) => root.querySelector(`[data-k="${k}"]`);

const cardHtml = (key) => `<div class="card stat" data-card="${key}"><span class="cap" data-n="label"></span><span class="v" data-n="value"></span><span class="d" data-n="sub"></span></div>`;
const section = (key, title, note = true, hidden = false) =>
  `<section class="card" data-card="${key}" ${hidden ? "hidden" : ""}><header class="card-h"><h3 data-n="title">${esc(title)}</h3>${note ? '<span class="sub-note" data-n="note"></span>' : ""}</header><div class="card-b" data-n="body"></div></section>`;

export function mount(content) {
  root = content;
  root.innerHTML = `${STYLE}<div class="stack st-ov">
    <div class="grid-4" data-k="cards">${CARDS.map(cardHtml).join("")}</div>
    ${section("trend", "Trend")}
    <div class="grid-2">
      <div class="stack">${section("timeline", "Day timeline", true, true)}${section("heat", "Active hours - last 7 days", false)}</div>
      <div class="stack">${section("bars", "Last 7 days")}${section("donut", "Categories", false)}${section("streaks", "Streaks", false)}</div>
    </div>
  </div>`;
  const body = (key) => root.querySelector(`[data-card="${key}"] [data-n="body"]`);
  body("trend").innerHTML = '<div data-n="chart"></div><div class="legend"><span style="--k:var(--brand)"><i></i>each day</span><span style="--k:var(--text-2)"><i></i>7-day average</span><span style="--k:var(--text-3)"><i></i>your goal</span></div>';
  body("timeline").innerHTML = '<div data-n="chart"></div><div class="legend" data-n="legend"></div>';
  body("heat").innerHTML = '<div data-n="chart"></div>';
  body("bars").innerHTML = '<div data-n="chart"></div>';
  body("donut").innerHTML = '<div class="st-donut"><div data-n="chart"></div><div class="legend" data-n="legend"></div></div>';
  body("streaks").innerHTML = '<div data-n="rows"></div>';
  const make = (key, build, label) => {
    chartHost[key] = root.querySelector(`[data-card="${key}"] [data-n="chart"]`);
    charts[key] = build(chartHost[key], (chartOpts[key] = { label }));
  };
  make("trend", trendChart, "Active screen time per day");
  make("heat", heatmapChart, "Active screen time by hour, last 7 days");
  make("bars", barChart, "Active screen time per day");
  make("donut", donutChart, "Active screen time by category");
  chartHost.timeline = root.querySelector('[data-card="timeline"] [data-n="chart"]');
  charts.timeline = null;                         // (made the first time a Today / Yesterday arrives)
}

/** Draw charts whose widths were read before anything was written: [[key, data, width]]. charts.js uses opts.width when it
 *  is set (instead of reading the element, which after a write would lay the page out again), so it is set for the length of
 *  one update() and cleared again - the chart then follows its element's size by itself, as before. */
function drawCharts(list) {
  for (const [key, data, width] of list) {
    chartOpts[key].width = width;
    try { charts[key].update(data); } finally { chartOpts[key].width = 0; }
  }
}

/** Run `fn` right after the next frame has painted what render() just wrote (a frame callback, then a task after the paint);
 *  a newer call replaces an older one that has not run, so a quick second reply costs one pass. A hidden window gets no
 *  frames: it runs when the window is shown. */
function afterPaint(fn) {
  later = fn;
  if (frame) return;
  frame = requestAnimationFrame(() => setTimeout(() => {
    frame = 0;
    const run = later;
    later = null;
    if (run) run();
  }, 0));
}

const text = (card, name, value) => setText(root.querySelector(`[data-card="${card}"] [data-n="${name}"]`), value);

function renderLegend(host, rows, valued = false) {
  syncRows(host, rows, () => legendItem(valued), legendUpdate);
}

export function render(d) {
  const trendWidth = widthOf("trend");            // (read first: nothing has been written yet)
  root.dataset.charts = "pending";                // (the charts below the trend follow in a frame: tests wait for "done")
  for (const c of d.cards) {
    text(c.key, "label", c.label);
    text(c.key, "value", c.value);
    text(c.key, "sub", c.sub);
  }
  // trend
  text("trend", "title", d.trend.title);
  text("trend", "note", d.trend.note);
  const note = root.querySelector('[data-card="trend"] [data-n="note"]');
  note.classList.toggle("t-ok", d.trend.tone === "ok");
  note.classList.toggle("t-bad", d.trend.tone === "bad");
  root.querySelector('[data-card="timeline"]').hidden = !d.timeline;       // (Today / Yesterday only; its chart follows)
  drawCharts([["trend", trendData(d.trend), trendWidth]]);
  afterPaint(() => renderRest(d));
}

function renderRest(d) {
  const tl = root.querySelector('[data-card="timeline"]');
  const width = { heat: widthOf("heat"), bars: widthOf("bars"), timeline: d.timeline ? widthOf("timeline") : 0 };      // (all reads, then all writes)
  const list = [];
  if (d.timeline) {
    if (!charts.timeline) charts.timeline = timelineChart(chartHost.timeline, (chartOpts.timeline = { label: "Your day" }));
    text("timeline", "note", d.timeline.note);
    list.push(["timeline", timelineData(d.timeline), width.timeline]);
    renderLegend(tl.querySelector('[data-n="legend"]'), d.timeline.legend);
  }
  list.push(["heat", heatData(d.heat), width.heat]);
  // last N days
  text("bars", "title", d.bars.title);
  text("bars", "note", d.bars.note);
  list.push(["bars", barsData(d.bars), width.bars]);
  // categories (the donut is square: its size doesn't depend on the element)
  list.push(["donut", donutData(d.donut), 0]);
  drawCharts(list);
  renderLegend(root.querySelector('[data-card="donut"] [data-n="legend"]'), d.donut.parts.map((p) => ({ key: p.key, color: p.color, name: p.name, value: p.time })), true);
  // streaks
  syncRows(root.querySelector('[data-card="streaks"] [data-n="rows"]'), d.streaks, () => {
    const node = el(`<div class="mini-row st-streak"><span class="kic k-brand"></span><div class="grow"><b></b><span></span></div></div>`);
    node._kic = node.querySelector(".kic");
    node._b = node.querySelector("b");
    node._sub = node.querySelector(".grow span");
    return node;
  }, (node, r) => {
    if (node._icon !== r.icon) { node._icon = r.icon; node._kic.innerHTML = ic(r.icon); node._kic.className = "kic " + (r.icon === "lifebuoy" ? "k-emerg" : "k-brand"); }
    setText(node._b, r.text);
    setText(node._sub, r.sub);
    node._sub.hidden = !r.sub;
  });
  root.dataset.charts = "done";
}
