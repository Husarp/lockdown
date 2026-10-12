// Blocking > Calendar: one row per blocked thing, its clock rules (permanent + by-time) drawn as a 24 h strip. Day view: one
// wide strip per row; 3 days / Week: one mini strip per day. Python (calendar_get, webapi/calendar.py) works out every
// interval, mark, total and the three Day-view cards; this file only draws and handles clicks.
//
// Clicks (one handler on the list, which looks at the row the click is IN when it happens - not at a variable the loop left
// behind, which is how the old window opened the last row's item from every strip):
//   the icon / name           -> Add tab, editing that item
//   a strip in Day view       -> the same: edit THAT row's item
//   a strip in 3 days / Week  -> open that day in Day view
// The rows are a virtual list (js/vlist.js, all rows the same height), so 200 items cost about 30 DOM rows. Limits that depend
// on use have no clock time: they are named under the list, not drawn.
import { iconKey, iconUrl, wireIconReady } from "../../appicon.js";
import { ld } from "../../bridge.js";
import { parseNaive } from "../../challenge.js";
import { el, empty, esc, ic, seg, setText } from "../../ui.js";
import { createVList } from "../../vlist.js";

export const freshMs = 10_000;
export const RANGES = [["1", "Day"], ["3", "3 days"], ["7", "Week"]];
export const FILTERS = [["all", "Everything"], ["sites", "Sites"], ["apps", "Apps"]];
const DAY = 1440;
const TYPE_WORDS = { site: "Site", app: "App", category: "Category" };
const EMPTY = "Nothing is blocked by a clock rule in this range - time / opening limits and temporary blocks aren't tied to a clock time.";

// ---------------------------------------------------------------- plain helpers (tested in node)

export const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
export const span = (a, b) => `${hhmm(a)}–${hhmm(b)}`;

/** The SVG of one day's strip: 2 h ticks (Day view only) and one bar per blocked stretch, each with its "HH:MM-HH:MM" tooltip.
 *  The viewBox is minutes (0..1440) and stretches to the strip's width, so the markup does not depend on the screen size. */
export function stripSvg(intervals, ticks) {
  let out = `<svg class="cal-svg" viewBox="0 0 ${DAY} 20" preserveAspectRatio="none" aria-hidden="true">`;
  if (ticks) for (let h = 2; h < 24; h += 2) out += `<line class="cal-tick" x1="${h * 60}" x2="${h * 60}" y1="0" y2="20"/>`;
  for (const [a, b] of intervals) out += `<rect class="cal-bar" x="${a}" y="3" width="${b - a}" height="14"><title>${span(a, b)}</title></rect>`;
  return `${out}</svg>`;
}

/** The emergency-unlock circles of one day: a string of absolutely placed Ⓔ (so they stay round at any width). `tips` are the
 *  hover texts Python wrote ("Emergency unlock 14:05–14:25 (YouTube, Reddit)"), one per circle. */
export function marksHtml(minutes, tips = []) {
  return minutes.map((m, i) => `<span class="cal-e" style="left:${(m / DAY) * 100}%" title="${esc(tips[i] || `Emergency unlock ${hhmm(m)}`)}">E</span>`).join("");
}

/** The inside of one track (a day's strip): the svg, the circles and, in Day view on today, the now-line. */
export function trackHtml(intervals, marks, { ticks, nowMin, tips }) {
  const now = nowMin == null ? "" : `<span class="cal-now" style="left:${(nowMin / DAY) * 100}%"></span>`;
  return stripSvg(intervals, ticks) + marksHtml(marks, tips) + now;
}

/** The "Limits that depend on use" sentence, or "" when there are none. */
export function limitsText(limits) {
  if (!limits || !limits.names.length) return "";
  const more = limits.more ? ` and ${limits.more} more` : "";
  return `Time and opening limits depend on use, so they are listed by name, not drawn: ${limits.names.join(", ")}${more}.`;
}

export const hintText = (days) => (days === 1 ? "Click a bar to edit that item" : "Each cell is one day, midnight → midnight · click a day to open it in Day view");

// ---------------------------------------------------------------- state

let tab = null, root = null, vl = null;
let data = null, rows = [];
let view = { start: null, days: 1, filter: "all" };         // what the user asked for (start null = today)
let loadedRev, clock = { base: 0, at: 0 }, loadedMinute = -1;
const els = {};
const q = (k) => root.querySelector(`[data-k="${k}"]`);

const STYLE = `<style>
.cal { display: flex; flex-direction: column; gap: var(--sp-3); flex: 1; min-height: 0; overflow-y: auto; }
.cal-bar-h { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.cal-bar-h h3 { font-size: var(--fs-h3, inherit); margin: 0 var(--sp-2); }
.cal-today { color: var(--brand); font-size: var(--fs-cap); font-weight: var(--fw-semibold); letter-spacing: .04em; }
.cal-card { flex: 1; min-height: calc(var(--row-h) * 5); display: flex; flex-direction: column; overflow: hidden; }
.cal-grid { display: grid; grid-template-columns: minmax(180px, 1.1fr) minmax(0, 3fr) calc(170px * var(--s)); align-items: center; gap: var(--sp-3); padding: 0 var(--sp-4); }
.cal-head { height: calc(34px * var(--s)); background: var(--surface-2); border-bottom: 1px solid var(--line); }
.cal-head .r { text-align: right; }
.cal-axis { display: grid; font-size: var(--fs-sm); color: var(--text-3); }
.cal-axis.h24 { grid-template-columns: repeat(12, 1fr); }
.cal-axis.days { gap: 6px; text-align: center; color: var(--text-2); font-weight: var(--fw-semibold); }
.cal-axis .today { color: var(--brand); }
.cal-row { height: var(--row-h); border-bottom: 1px solid var(--line); background: var(--surface); }
.cal-row:hover { background: var(--surface-2); }
.cal-row.off { opacity: .6; }
.cal-who { display: flex; align-items: center; gap: var(--sp-3); min-width: 0; cursor: pointer; height: 100%; }
.cal-who b { font-weight: var(--fw-semibold); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; }
.cal-who .aic { position: relative; }
.cal-who .cal-tile { width: 62%; height: 62%; stroke-width: 2; }
.cal-who.tile .aic { --c: var(--brand); --fg: var(--on-brand); }
.cal-who.tile img, .cal-who:not(.tile) .cal-tile { display: none; }
.cal-badge { flex: none; font-size: var(--fs-cap); color: var(--text-2); border: 1px solid var(--line); border-radius: var(--r-pill); padding: 0 var(--sp-2); }
.cal-tracks { display: grid; gap: 6px; min-width: 0; }
.cal-track { position: relative; height: calc(24px * var(--s)); background: var(--surface-3); border-radius: 3px; overflow: hidden; cursor: pointer; }
.cal-tracks.multi .cal-track { height: calc(20px * var(--s)); }
.cal-track.today { outline: 2px solid var(--brand); outline-offset: -2px; }
.cal-track:focus-visible { outline: 2px solid var(--brand); }
.cal-svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.cal-tick { stroke: var(--line); stroke-width: 1; vector-effect: non-scaling-stroke; }
.cal-bar { fill: var(--k, var(--brand)); fill-opacity: .55; stroke: var(--k, var(--brand)); stroke-width: 1; vector-effect: non-scaling-stroke; }
.cal-e { position: absolute; top: 50%; transform: translate(-50%, -50%); min-width: calc(16px * var(--s)); height: calc(16px * var(--s)); display: grid; place-items: center;
  border-radius: 50%; background: var(--surface); border: 1.5px solid var(--k-emerg); color: var(--k-emerg); font-size: 9px; font-weight: var(--fw-semibold); line-height: 1; }
.cal-tracks.multi .cal-e { min-width: calc(13px * var(--s)); height: calc(13px * var(--s)); font-size: 8px; }
.cal-now { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: var(--brand); }
.cal-sum { display: grid; text-align: right; line-height: 1.25; min-width: 0; }
.cal-sum span { font-size: var(--fs-sm); color: var(--text-2); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.cal-sum b { font-weight: var(--fw-regular); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.cal-list { position: relative; flex: 1; min-height: 0; }
.cal-list[hidden], .cal-empty[hidden], .cal-cards[hidden] { display: none; }
.cal-empty { padding: var(--sp-5) var(--sp-4); color: var(--text-2); }
.cal-foot { display: flex; gap: var(--sp-4); align-items: center; flex-wrap: wrap; padding: var(--sp-2) var(--sp-4); background: var(--surface-2); border-top: 1px solid var(--line); color: var(--text-2); font-size: var(--fs-sm); }
.cal-foot .legend { gap: var(--sp-3); }
.cal-foot .sw { width: 9px; height: 9px; border-radius: 2px; background: var(--c-dark); display: inline-block; }
:root[data-theme="light"] .cal-foot .sw { background: var(--c-light); }
:root[data-theme="light"] .cal-row { --k: var(--c-light); }
.cal-row { --k: var(--c-dark); }
.cal-foot .grow { flex: 1; }
.cal-nowkey { display: inline-block; width: 3px; height: 12px; background: var(--brand); margin-right: var(--sp-1); }
.cal-e-key { color: var(--k-emerg); }
.cal-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--sp-3); flex: none; }
.cal-cardbox { padding: var(--sp-3) var(--sp-4); border-left: 3px solid var(--k); display: grid; gap: var(--sp-1); }
.cal-cardbox .v { font-size: var(--fs-body, inherit); }
.cal-cardbox .v .blocked { color: var(--brand); }
.cal-cardbox .v .allowed { color: var(--ok); }
.cal-note { flex: none; margin: 0; }
@media (max-width: 860px) { .cal-grid { grid-template-columns: minmax(120px, 1fr) minmax(0, 2fr) calc(110px * var(--s)); } .cal-cards { grid-template-columns: 1fr; } .cal-axis.h24 span:nth-child(even) { visibility: hidden; } }
@media (max-width: 640px) { .cal-who .cal-badge { display: none; } }
</style>`;

const NEXT = { blocked: "blocked", allowed: "allowed" };

// ---------------------------------------------------------------- rows

function createRow() {
  const node = el(`<div class="cal-row cal-grid">
    <div class="cal-who" role="button" tabindex="0"><span class="aic aic-sm"><img alt="" loading="lazy" decoding="async" draggable="false">${ic("folder", "cal-tile")}</span><b></b><span class="cal-badge"></span></div>
    <div class="cal-tracks"></div>
    <div class="cal-sum"><b></b><span></span></div></div>`);
  node._r = {
    who: node.querySelector(".cal-who"), img: node.querySelector("img"), name: node.querySelector(".cal-who b"), badge: node.querySelector(".cal-badge"),
    tracks: node.querySelector(".cal-tracks"), sum: node.querySelector(".cal-sum b"), sub: node.querySelector(".cal-sum span"),
  };
  return node;
}

function renderRow(node, row) {
  const r = node._r, d = data;
  node._row = row;
  node.classList.toggle("off", row.disabled);
  const cat = d.categories.find((c) => c.key === row.cat);
  node.style.setProperty("--c-dark", cat ? cat.color : "var(--k-grey)");
  node.style.setProperty("--c-light", cat ? cat.color_light : "var(--k-grey)");
  setText(r.name, row.name);
  setText(r.badge, TYPE_WORDS[row.type] || row.type);
  const icon = row.icon;
  if (icon.k === "category") r.who.classList.add("tile");
  else {
    r.who.classList.remove("tile");
    const key = iconKey(icon.k, icon.n);
    if (r.img.dataset.icon !== key) { r.img.dataset.icon = key; r.img.src = iconUrl(icon.k, icon.n); }
  }
  setText(r.sum, row.sum);
  setText(r.sub, row.sub);
  r.sum.title = row.sub ? `${row.sum} · ${row.sub}` : row.sum;
  // the strips: rebuilt only when this row shows something else than last time (a recycled node, a reload that changed it)
  const sig = `${d.days}|${d.is_today ? d.now.slice(11, 16) : ""}|${d.dates[0].date}|${JSON.stringify(row.days)}${JSON.stringify(row.marks)}${JSON.stringify(row.mark_tips)}`;
  if (node._sig === sig) return;
  node._sig = sig;
  const multi = d.days > 1, nowMin = d.is_today ? Number(d.now.slice(11, 13)) * 60 + Number(d.now.slice(14, 16)) : null;
  r.tracks.className = `cal-tracks${multi ? " multi" : ""}`;
  r.tracks.style.gridTemplateColumns = `repeat(${d.days}, minmax(0, 1fr))`;
  r.tracks.innerHTML = d.dates.map((day, i) =>
    `<div class="cal-track${multi && day.today ? " today" : ""}" role="button" tabindex="0" data-date="${day.date}" aria-label="${esc(multi ? `${row.name}, ${day.label}: open that day` : `${row.name}: edit`)}">${trackHtml(row.days[i], row.marks[i], { ticks: !multi, nowMin: multi ? null : nowMin, tips: (row.mark_tips || [])[i] })}</div>`).join("");
}

// ---------------------------------------------------------------- clicks

function onActivate(e) {
  const node = e.target.closest(".cal-row");
  if (!node || !node._row) return;
  if (e.target.closest(".cal-who")) { tab.editItem(node._row.id); return; }
  const track = e.target.closest(".cal-track");
  if (!track) return;
  if (data.days > 1) go({ start: track.dataset.date, days: 1 });
  else tab.editItem(node._row.id);
}

function go(change) {
  view = { ...view, ...change };
  tab.reload();
}

// ---------------------------------------------------------------- painting

function paint() {
  const d = data;
  setText(els.title, d.title);
  els.todayTag.hidden = !(d.days === 1 && d.is_today);
  for (const b of els.range.children) b.setAttribute("aria-pressed", String(b.dataset.v === String(d.days)));
  if (els.filter.value !== d.filter) els.filter.value = d.filter;
  setText(els.head, d.head);
  // the axis: hours (Day) or the days
  const axisKey = `${d.days}|${d.dates.map((x) => x.date).join()}`;
  if (els.axis._key !== axisKey) {
    els.axis._key = axisKey;
    els.axis.className = `cal-axis ${d.days === 1 ? "h24" : "days"}`;
    els.axis.style.gridTemplateColumns = d.days === 1 ? "" : `repeat(${d.days}, minmax(0, 1fr))`;
    els.axis.innerHTML = d.days === 1
      ? Array.from({ length: 12 }, (_, i) => `<span>${String(i * 2).padStart(2, "0")}</span>`).join("")
      : d.dates.map((x) => `<span class="${x.today ? "today" : ""}">${esc(x.label)}</span>`).join("");
  }
  rows = d.rows;
  const none = !rows.length;
  els.list.hidden = none;
  els.empty.hidden = !none;
  vl.setRows(rows.map((r) => ({ key: r.id, r })), { keepScroll: true });
  paintLegend();
  setText(els.hint, hintText(d.days));
  const note = limitsText(d.limits);
  setText(els.note, note);
  els.note.hidden = !note;
  paintCards();
}

function paintLegend() {
  const d = data;
  const html = d.categories.map((c) => `<span><i class="sw" style="--c-dark:${esc(c.color)};--c-light:${esc(c.color_light)}"></i>${esc(c.name)}</span>`).join("")
    + (d.days === 1 && d.is_today ? `<span><i class="cal-nowkey"></i>now ${d.now.slice(11, 16)}</span>` : "")
    + `<span><span class="cal-e-key">Ⓔ</span> emergency unlock</span>`;
  if (els.legend._html !== html) { els.legend._html = html; els.legend.innerHTML = html; }
}

function paintCards() {
  const s = data.summary;
  els.cards.hidden = !s;
  if (!s) return;
  const n = s.next;
  els.next.innerHTML = n.at ? `<b>${esc(n.at)}</b> — <span class="${NEXT[n.kind]}">${esc(n.text)}</span>` : esc(n.text);
  setText(els.busiest, s.busiest);
  setText(els.free, s.free);
}

// ---------------------------------------------------------------- the tab contract

export function mount(host, t) {
  tab = t;
  root = host;
  host.classList.add("bk-fill");
  host.innerHTML = `${STYLE}<div class="cal">
    <div class="cal-bar-h">
      <button class="icon-btn" type="button" data-k="prev" aria-label="Previous">${ic("chev-l")}</button>
      <button class="icon-btn" type="button" data-k="next" aria-label="Next">${ic("chev-r")}</button>
      <h3 data-k="title">&nbsp;</h3><span class="cal-today" data-k="today" hidden>TODAY</span>
      <span class="grow" style="flex:1"></span>
      ${seg("range", RANGES, "1")}
      <select class="select select-sm" data-k="filter" aria-label="Show">${FILTERS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
    </div>
    <div class="card cal-card">
      <div class="cal-grid cal-head"><span class="cap">Item</span><div class="cal-axis" data-k="axis"></div><span class="cap r" data-k="head">&nbsp;</span></div>
      <div class="cal-list" data-k="list" id="cal-list"></div>
      <div class="cal-empty" data-k="empty" hidden>${empty("k-grey", "calendar", "Nothing to draw", EMPTY)}</div>
      <div class="cal-foot"><div class="legend" data-k="legend"></div><span class="grow"></span><span data-k="hint"></span></div>
    </div>
    <div class="cal-cards" data-k="cards" hidden>
      <div class="card cal-cardbox k-brand"><span class="cap">Next change</span><span class="v" data-k="cnext"></span></div>
      <div class="card cal-cardbox k-limit"><span class="cap">Busiest stretch</span><span class="v" data-k="cbusy"></span></div>
      <div class="card cal-cardbox k-ok"><span class="cap">Free window</span><span class="v" data-k="cfree"></span></div>
    </div>
    <p class="small muted cal-note" data-k="note" hidden></p>
  </div>`;
  Object.assign(els, {
    title: q("title"), todayTag: q("today"), range: host.querySelector(".seg"), filter: q("filter"), axis: q("axis"), head: q("head"), list: q("list"),
    empty: q("empty"), legend: q("legend"), hint: q("hint"), cards: q("cards"), next: q("cnext"), busiest: q("cbusy"), free: q("cfree"), note: q("note"),
  });
  vl = createVList(els.list, { rowVar: "--row-h", rows: [], key: (row) => row.key, create: createRow, render: (node, row) => renderRow(node, row.r) });
  els.list.addEventListener("click", onActivate);
  els.list.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches(".cal-who, .cal-track")) { e.preventDefault(); onActivate(e); }
  });
  q("prev").addEventListener("click", () => data && go({ start: data.prev }));
  q("next").addEventListener("click", () => data && go({ start: data.next }));
  els.range.addEventListener("seg", (e) => go({ days: Number(e.detail.value) }));
  els.filter.addEventListener("change", () => go({ filter: els.filter.value }));
  wireIconReady(ld.on, host);
  tab.every(tick);
}

export async function load() {
  const asked = view;
  loadedRev = ld.status.blocks ? ld.status.blocks.rev : undefined;      // (before the call: a push that comes meanwhile still reloads)
  const next = await ld.call("calendar_get", asked.start, asked.days, asked.filter);
  if (asked !== view) return;                       // the user clicked on meanwhile: that load paints
  data = next;
  clock = { base: parseNaive(data.now), at: performance.now() };
  loadedMinute = Math.floor(clock.base / 60000);
  paint();
}

/** Once a second: when the minute moves, today's now-line and the "Next change" card are read again. */
function tick() {
  if (!data || !data.is_today) return;
  if (Math.floor((clock.base + performance.now() - clock.at) / 60000) !== loadedMinute) tab.reload();
}

let reloadQueued = false;
function reloadSoon() {
  if (reloadQueued) return;
  reloadQueued = true;
  Promise.resolve().then(() => { reloadQueued = false; tab.reload(); });      // (pushes that arrive together reload once)
}

export function patch({ event, payload }) {
  if (event === "draft") { if (!payload || payload.blocks !== false) reloadSoon(); }
  else if (event === "blocks_changed") reloadSoon();
  else if (event === "status" && payload && payload.blocks && payload.blocks.rev !== loadedRev) reloadSoon();
}
