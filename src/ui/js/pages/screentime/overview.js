// Screen Time > Overview: four numbers, the trend, the day timeline (Today / Yesterday), the active-hours heatmap, the last days
// as bars, the categories donut, and the streaks. Built once (mount); render(data) only updates: text when it changed, the
// charts with update() (same shape -> attribute writes, no new nodes), the legend and streak rows keyed.
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
  const chartEl = (key) => root.querySelector(`[data-card="${key}"] [data-n="chart"]`);
  charts.trend = trendChart(chartEl("trend"), { label: "Active screen time per day" });
  charts.heat = heatmapChart(chartEl("heat"), { label: "Active screen time by hour, last 7 days" });
  charts.bars = barChart(chartEl("bars"), { label: "Active screen time per day" });
  charts.donut = donutChart(chartEl("donut"), { label: "Active screen time by category" });
  charts.timeline = null;                         // (made the first time a Today / Yesterday arrives)
}

const text = (card, name, value) => setText(root.querySelector(`[data-card="${card}"] [data-n="${name}"]`), value);

function renderLegend(host, rows, valued = false) {
  syncRows(host, rows, () => legendItem(valued), legendUpdate);
}

export function render(d) {
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
  charts.trend.update(trendData(d.trend));
  // the day timeline: Today / Yesterday only
  const tl = root.querySelector('[data-card="timeline"]');
  tl.hidden = !d.timeline;
  if (d.timeline) {
    if (!charts.timeline) charts.timeline = timelineChart(tl.querySelector('[data-n="chart"]'), { label: "Your day" });
    text("timeline", "note", d.timeline.note);
    charts.timeline.update(timelineData(d.timeline));
    renderLegend(tl.querySelector('[data-n="legend"]'), d.timeline.legend);
  }
  charts.heat.update(heatData(d.heat));
  // last N days
  text("bars", "title", d.bars.title);
  text("bars", "note", d.bars.note);
  charts.bars.update(barsData(d.bars));
  // categories
  charts.donut.update(donutData(d.donut));
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
}
