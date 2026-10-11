// Network Log: which app connected to which site in the last hour (BLUEPRINT 3.6).
//
// Python (webapi/network.py) keeps the last hour cached and does ALL the filtering, searching and counting; this page asks
// for the newest 100 rows that pass the filters (network_get) and paints them in ONE virtual list (about 30 row nodes
// whether there are 100 rows or 20 000, keyed by the row's key). "Show more (N older)" - and scrolling near the end - doubles what is loaded.
// Live: network_get(live=true) registers the filters with Python's feed, which pushes `net_rows` every 3 s with ONLY the new
// or changed rows, the new totals and the new summary; they are merged into the list in place (no reload, and a list
// scrolled down keeps showing the same rows). While the window is hidden nothing is pushed or asked.
// Changing a filter starts again from the newest 100 rows; the search waits 120 ms for the next key.
//
// Row menu: Block site / Block app open Blocking > Add filled in (nothing is blocked here), Copy site / app name use the
// browser clipboard, Show only <app> filters. Export CSV: Python opens Windows' Save dialog and writes the file on a worker.
import { openAppMenu } from "../appmenu.js";
import { iconKey, iconUrl, wireIconReady } from "../appicon.js";
import { ld } from "../bridge.js";
import { barChart } from "../charts.js";
import { openMenu } from "../menu.js";
import { el, empty, esc, ic, seg, setText, sw, toast } from "../ui.js";
import { createVList } from "../vlist.js";

export const freshMs = 3000;
export const events = ["net_rows", "net_export", "visible"];
export const PAGE = 100;
export const SEARCH_MS = 120;
const HINT = "Keeps the last hour · click a row to block it or copy it";

// ---------------------------------------------------------------- plain helpers (tested in node)

/** Does row `a` come before row `b` in the list? Newest minute first; in one minute connections (busiest first) and then
 *  blocked visits (newest first) - the order Python sorts in. */
export function before(a, b) {
  if (a.m !== b.m) return a.m > b.m;
  if (a.b !== b.b) return a.b < b.b;
  if (a.b) return a.i > b.i;
  return a.n > b.n;
}

/**
 * Merge the rows of a live push into the loaded list, in place. A row we already have is updated where it is; a new one is
 * put where it belongs (the scan starts at the top, where new rows are) - or left out when it belongs among the rows that are
 * not loaded yet (`complete`: every matching row is loaded, so it is added at the end instead). Rows older than `cutoff` (the
 * hour moved on) are dropped from the end. `anchor` = the index of the first row in view: returns how many rows went in at or
 * above it (so it moved down by that many), for the caller to keep the view where it was.
 */
export function mergeRows(rows, byKey, incoming, { cutoff = "", anchor = 0, complete = false } = {}) {
  let above = 0, added = 0, updated = 0, first = anchor;
  for (const row of incoming) {
    const old = byKey.get(row.k);
    if (old) { Object.assign(old, row); updated++; continue; }
    let i = 0;
    while (i < rows.length && !before(row, rows[i])) i++;
    if (i === rows.length && !complete) continue;
    rows.splice(i, 0, row);
    byKey.set(row.k, row);
    added++;
    if (i <= first) { above++; first++; }                         // (it went in at or above the row in view: that row moved down)
  }
  let dropped = 0;
  while (cutoff && rows.length && rows[rows.length - 1].m < cutoff) { byKey.delete(rows.pop().k); dropped++; }
  return { above, added, updated, dropped };
}

export const olderText = (n) => `Show more (${n} older)`;

// ---------------------------------------------------------------- the page

const STYLE = `<style>
.nl-g { grid-template-columns: minmax(0, 1.25fr) calc(60px * var(--s)) minmax(0, 2fr) minmax(0, 1.1fr) calc(60px * var(--s)) calc(100px * var(--s)); font-size: var(--fs-sm); }
.nl-head, #nl-list { scrollbar-gutter: stable; }                       /* (the header and the rows keep the same width with or without a scroll bar) */
.nl-head { overflow-y: hidden; }
.nl-app { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
.nl-app .aic img { background: transparent; }
.nl-g .time, .nl-g .port { color: var(--text-2); font-variant-numeric: tabular-nums; }
.nl-g .n { color: var(--text-3); margin-left: var(--sp-1); }
.nl-g .st { white-space: nowrap; color: var(--ok); }
.nl-g.blk { background: color-mix(in srgb, var(--k-block) 9%, var(--surface)); }
.nl-g.blk:hover { background: color-mix(in srgb, var(--k-block) 15%, var(--surface)); }
.nl-g.blk .st { color: var(--k-block); }
.nl-g.vl-row { cursor: pointer; }
.nl-foot { display: flex; align-items: center; gap: var(--sp-3); min-height: calc(40px * var(--s)); padding: 0 var(--sp-4); border-top: 1px solid var(--line); background: var(--surface-2); flex: none; }
.nl-foot .grow { color: var(--text-2); font-size: var(--fs-sm); }
.nl-empty { position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none; }
.nl-tab { position: relative; display: flex; flex-direction: column; flex: 1; min-height: 0; }
.nl-graph { flex: 1; min-height: 0; overflow-y: auto; display: grid; gap: var(--sp-4); align-content: start; }
.nl-graph .legend { color: var(--k-block); font-size: var(--fs-sm); }
.nl-top .mini-row { padding: 0 var(--sp-4); }
.nl-top .mini-row .grow { display: block; }
.nl-top .aic img { background: transparent; }
@media (max-width: 860px) { .nl-g { grid-template-columns: minmax(0, 1fr) calc(56px * var(--s)) minmax(0, 1.6fr) calc(92px * var(--s)); } .nl-g .rule, .nl-g .port { display: none; } }
</style>`;

const ROW_HTML = `<div class="nl-app"><span class="aic aic-sm"><img alt="" loading="lazy" decoding="async" draggable="false"></span><span class="ellip" data-r="app"></span></div>
  <span class="time" data-r="t"></span>
  <span class="ellip"><span data-r="site"></span><span class="n" data-r="n"></span></span>
  <span class="ellip muted rule" data-r="rule"></span>
  <span class="port" data-r="port"></span>
  <span class="st" data-r="st"></span>`;

let root, page, vl, chart;
let list, q, searchTimer = 0;
let rows = [], byKey = new Map();
let total = 0, epoch = 0, seq = 0;
let limit = PAGE;
let view = "table", live = true, active = false, loadedAt = 0, reqId = 0, graphBusy = false, graphAgain = false;
let appsSig = "";
const state = { status: "all", app: "", query: "", windows: false, local: false };

export const filters = () => ({ status: state.status, app: state.app, query: state.query, windows: state.windows, local: state.local });

function createRow() {
  const node = el(`<div class="row row-sm nl-g" tabindex="0">${ROW_HTML}</div>`);
  const r = {};
  for (const n of node.querySelectorAll("[data-r]")) r[n.dataset.r] = n;
  r.img = node.querySelector("img");
  node._refs = r;
  return node;
}

function setIcon(img, kind, name) {
  const key = iconKey(kind, name);
  if (img.dataset.icon !== key) { img.dataset.icon = key; img.src = iconUrl(kind, name); }
}

function renderRow(node, row) {
  const r = node._refs;
  node._row = row;
  node.classList.toggle("blk", !!row.b);
  if (row.b) setIcon(r.img, "site", row.host || row.site); else setIcon(r.img, "app", row.exe || "unknown");
  setText(r.app, row.app);
  setText(r.t, row.t);
  setText(r.site, row.site);
  setText(r.n, row.n > 1 ? `×${row.n}` : "");
  setText(r.rule, row.rule);
  setText(r.port, row.port === "" ? "" : String(row.port));
  setText(r.st, row.b ? "✗ Blocked" : "✓ Allowed");
}

export function mount(el_, page_) {
  root = el_;
  page = page_;
  root.innerHTML = `${STYLE}<div class="page page-fill nl">
    <div class="page-h"><div class="grow"><h1>Network Log</h1><p class="sub" data-k="summary">&nbsp;</p></div>
      ${seg("nlview", [["table", "Table"], ["graph", "Graph"]], "table")}</div>
    <div class="toolbar">
      ${seg("nlstatus", [["all", "All"], ["allowed", "Allowed"], ["blocked", "Blocked"]], "all")}
      <div class="search" style="width:min(240px,100%)">${ic("search")}<input class="input" placeholder="Search site or app" aria-label="Search site or app" data-k="search" autocomplete="off" spellcheck="false"></div>
      <select class="select" aria-label="App" data-k="app"><option value="">All apps</option></select>
      <span class="grow"></span>
      <label class="hstack small">${sw(true, 'data-k="live"')}Live</label>
      <button class="btn" type="button" data-k="export">${ic("download")}Export CSV</button>
    </div>
    <div class="toolbar">
      <label class="hstack small">${sw(false, 'data-k="windows"')}Windows' own connections</label>
      <label class="hstack small">${sw(false, 'data-k="local"')}Local network</label>
    </div>
    <div class="nl-tab" data-k="tabtable"><div class="card list-card">
      <div class="list-h nl-g nl-head"><span class="cap">App</span><span class="cap">Time</span><span class="cap">Site</span><span class="cap rule">Rule</span><span class="cap port">Port</span><span class="cap">Status</span></div>
      <div class="nl-tab"><div id="nl-list" data-k="list"></div>
        <div class="nl-empty" data-k="empty" hidden></div></div>
      <footer class="nl-foot"><span class="grow" data-k="hint">${esc(HINT)}</span><button class="btn btn-sm" type="button" data-k="more" hidden></button></footer>
    </div></div>
    <div class="nl-graph" data-k="graph" hidden>
      <section class="card"><header class="card-h"><h3>Connections per minute</h3><span class="small muted">last hour</span><span class="grow"></span><span class="legend">■ minute with a blocked attempt</span></header>
        <div class="card-b" data-k="chart"></div></section>
      <div class="grid-2 nl-top">
        <section class="card"><header class="card-h"><h3>Top apps</h3></header><div class="card-b" data-k="topapps"></div></section>
        <section class="card"><header class="card-h"><h3>Top sites</h3></header><div class="card-b" data-k="topsites"></div></section>
      </div>
    </div></div>`;
  q = (k) => root.querySelector(`[data-k="${k}"]`);
  list = q("list");
  vl = createVList(list, { rowVar: "--row-h-sm", rows: [], key: (row) => row.k, create: createRow, render: renderRow });
  list.addEventListener("click", onRowClick);
  list.addEventListener("contextmenu", (e) => { if (onRowClick(e)) e.preventDefault(); });
  list.addEventListener("keydown", (e) => {
    const node = e.target.closest && e.target.closest(".vl-row");
    if (node && e.target === node && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      const box = node.getBoundingClientRect();
      openRowMenu({ x: box.left + 40, y: box.bottom }, node._row);
    }
  });
  root.addEventListener("seg", (e) => {
    const { name, value } = e.detail;
    if (name === "nlview") setView(value);
    else if (name === "nlstatus") { state.status = value; refresh(); }
  });
  q("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.query = q("search").value.trim(); refresh(); }, SEARCH_MS);
  });
  q("app").addEventListener("change", () => setApp(q("app").value));
  q("windows").addEventListener("change", () => { state.windows = q("windows").checked; refresh(); });
  q("local").addEventListener("change", () => { state.local = q("local").checked; refresh(); });
  q("live").addEventListener("change", () => {
    live = q("live").checked;
    if (live) refresh(true); else ld.call("network_live_stop").catch(() => {});
  });
  q("more").addEventListener("click", loadMore);
  list.addEventListener("scroll", () => {                       // near the end of what is loaded: the next batch comes by itself
    if (total > rows.length && list.scrollTop + list.clientHeight * 3 >= list.scrollHeight) loadMore();
  }, { passive: true });
  q("export").addEventListener("click", exportCsv);
  wireIconReady(ld.on, root);
}

const report = (err) => { toast((err && err.message) || "Something went wrong."); };

// ---------------------------------------------------------------- data

async function fetchRows(keepScroll) {
  const my = ++reqId;
  if (keepScroll) limit = Math.max(limit, rows.length);
  const d = await ld.call("network_get", filters(), limit, live && active);
  if (my !== reqId) return;                    // a newer question was asked meanwhile
  rows = d.rows;
  byKey = new Map(rows.map((r) => [r.k, r]));
  total = d.total;
  epoch = d.epoch;
  seq = d.seq;
  paintMeta(d);
  vl.setRows(rows, { keepScroll });
  if (view === "graph") await loadGraph();
  loadedAt = Date.now();
}

/** More rows: a virtual list takes a long list as cheaply as a short one, so each step doubles what is loaded (20 000 rows
 *  are 8 steps). The button asks for it; scrolling near the end does too. */
let loadingMore = false;
function loadMore() {
  if (loadingMore) return;
  loadingMore = true;
  limit += Math.max(PAGE, limit);
  fetchRows(true).catch(report).finally(() => { loadingMore = false; });
}

/** A filter changed (or Live was switched on): start again from the newest rows. */
function refresh(keepLimit) {
  if (!keepLimit) limit = PAGE;
  if (!keepLimit) list.scrollTop = 0;
  fetchRows(!!keepLimit).catch(report);
}

export async function load() {
  await fetchRows(true);
}

function paintMeta(d) {
  setText(q("summary"), d.summary.text);
  const older = Math.max(0, total - rows.length);
  const more = q("more");
  more.hidden = older === 0;
  if (older) setText(more, olderText(older));
  const hint = q("hint");
  const e = q("empty");
  if (d.empty) {
    setText(hint, "No connections in the last hour yet.");
    e.hidden = false;
    e.innerHTML = empty("k-grey", "activity", "No connections yet", "The log fills in as apps go online.");
  } else if (!rows.length) {
    setText(hint, HINT);
    e.hidden = false;
    e.innerHTML = empty("k-grey", "search", "Nothing matches", "Try another word or another filter.");
  } else {
    setText(hint, HINT);
    if (!e.hidden) { e.hidden = true; e.innerHTML = ""; }
  }
  paintApps(d.apps);
}

function paintApps(apps) {
  const sig = JSON.stringify(apps) + "|" + state.app;
  if (sig === appsSig) return;
  appsSig = sig;
  const sel = q("app");
  const shown = apps.some((a) => a.exe === state.app) || !state.app ? apps : [...apps, { exe: state.app, name: state.app }];
  sel.innerHTML = `<option value="">All apps</option>` + shown.map((a) => `<option value="${esc(a.exe)}">${esc(a.name)}</option>`).join("");
  sel.value = state.app;
}

function setApp(exe) {
  state.app = exe;
  refresh();
}

// ---------------------------------------------------------------- the Graph tab

function setView(v) {
  view = v;
  q("tabtable").hidden = v !== "table";
  q("graph").hidden = v !== "graph";
  if (v === "graph") {
    if (!chart) chart = barChart(q("chart"), { height: 170, label: "Connections per minute" });
    loadGraph().catch(report);
  } else vl.measure();
}

function topRows(host, kind, n) {
  const nodes = [];
  for (let i = 0; i < n; i++) {
    const node = el(`<div class="mini-row" hidden><span class="aic aic-sm"><img alt="" loading="lazy" decoding="async" draggable="false"></span><div class="grow"><b class="ellip"></b></div><span class="num muted"></span></div>`);
    host.append(node);
    nodes.push({ node, img: node.querySelector("img"), name: node.querySelector("b"), n: node.querySelector(".num") });
  }
  const none = el(`<p class="muted small" style="margin:0">Nothing yet.</p>`);
  host.append(none);
  return { nodes, none, kind };
}
let tops = null;

function paintTop(group, items) {
  group.none.hidden = items.length > 0;
  group.nodes.forEach((r, i) => {
    const it = items[i];
    r.node.hidden = !it;
    if (!it) return;
    if (group.kind === "app") { setIcon(r.img, "app", it.exe); setText(r.name, it.name); }
    else { setIcon(r.img, "site", it.site); setText(r.name, it.site); }
    setText(r.n, String(it.n));
  });
}

async function loadGraph() {
  if (graphBusy) { graphAgain = true; return; }
  graphBusy = true;
  try {
    do {
      graphAgain = false;
      const d = await ld.call("network_graph", filters());
      if (view !== "graph") break;
      chart.update({ bars: d.bars });
      if (!tops) tops = { apps: topRows(q("topapps"), "app", 8), sites: topRows(q("topsites"), "site", 8) };
      paintTop(tops.apps, d.apps);
      paintTop(tops.sites, d.sites);
    } while (graphAgain);
  } finally { graphBusy = false; }
}

// ---------------------------------------------------------------- live pushes

function onRows(p) {
  if (!live || !active || !p || p.epoch !== epoch) return;       // not ours (the filters changed meanwhile)
  if (p.resync) { page.reload(); return; }
  if (p.seq <= seq) return;
  seq = p.seq;
  const anchor = Math.floor(list.scrollTop / vl.rowHeight);
  const m = mergeRows(rows, byKey, p.rows, { cutoff: p.cutoff, anchor, complete: total <= rows.length });
  total = p.total;
  paintMeta({ summary: p.summary, apps: p.apps, empty: false });
  vl.setRows(rows, { keepScroll: true });
  if (m.above && list.scrollTop > 0) {                           // scrolled down: the same rows stay under the pointer
    list.scrollTop += m.above * vl.rowHeight;
    vl.refresh();
  }
  if (view === "graph") loadGraph().catch(report);
}

export function patch({ event, payload }) {
  if (event === "net_rows") onRows(payload);
  else if (event === "visible") { if (payload && payload.visible && active) page.reload(); }
  else if (event === "net_export") {
    if (!payload) return;
    toast(payload.ok ? `Saved ${payload.rows} rows to ${String(payload.path).split(/[\\/]/).pop()}` : payload.error || "Couldn't save the file.");
  }
}

export function show() {
  active = true;
  if (view !== "table") { root.querySelector('[data-seg="nlview"] [data-v="table"]').click(); }      // always back to the Table
  if (loadedAt) page.reload();                                    // (the first visit loads by itself)
}

export function hide() {
  active = false;
  if (live) ld.call("network_live_stop").catch(() => {});
}

// ---------------------------------------------------------------- the row menu

function onRowClick(e) {
  const node = e.target.closest && e.target.closest(".vl-row");
  if (!node || !node._row) return false;
  openRowMenu(e, node._row);
  return true;
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const t = document.createElement("textarea");
    t.value = text;
    t.style.cssText = "position:fixed;opacity:0";
    document.body.append(t);
    t.select();
    try { document.execCommand("copy"); } finally { t.remove(); }
  }
  toast(`Copied ${text}`);
}

async function blockTo(kind, value) {
  try {
    const t = await ld.call("network_block_target", kind, value);
    const prefill = t.site
      ? { site: t.site, kind: "site", name: t.site[1], display: t.site[0], path: null }
      : { app: t.app, kind: "app", name: t.app.exe, display: t.app.name, path: t.app.path };
    page.go("blocking", { tab: "add", prefill });
  } catch (err) { report(err); }
}

function openRowMenu(anchor, row) {
  const at = { x: anchor.clientX ?? anchor.x ?? 0, y: anchor.clientY ?? anchor.y ?? 0 };
  const items = [];
  const windows = !!row.w;
  if (row.reg) items.push({ label: `Block site ${row.reg}…`, icon: "ban", onClick: () => blockTo("site", row.host) });
  if (row.exe && !windows) items.push({ label: `Block app ${row.app}…`, icon: "ban", onClick: () => blockTo("app", row.exe) });
  if (items.length) items.push({ sep: true });
  if (row.site) items.push({ label: "Copy site", onClick: () => copy(row.host || row.site) });
  if (row.exe) {
    items.push({ label: "Copy app name", onClick: () => copy(row.app) });
    items.push(state.app ? { label: "Show all apps", onClick: () => setApp("") }
      : { label: `Show only ${row.app}`, onClick: () => setApp(row.exe) });
  }
  const more = [];
  if (row.host) more.push({ label: `Category and groups for ${row.host}…`, onClick: () => openAppMenu(at, { kind: "site", name: row.host }) });
  if (row.exe && !windows) more.push({ label: `Category and groups for ${row.app}…`, onClick: () => openAppMenu(at, { kind: "app", name: row.exe, display: row.app }) });
  if (more.length) items.push({ sep: true }, ...more);
  if (items.length) openMenu(at, items);
}

// ---------------------------------------------------------------- Export CSV

async function exportCsv() {
  const b = q("export");
  b.disabled = true;
  try {
    const r = await ld.call("network_export_csv", filters());
    if (r.saved) toast(`Saving ${r.rows} rows…`);
  } catch (err) { report(err); } finally { b.disabled = false; }
}
