// Screen Time (BLUEPRINT 3.5): the shell of the five tabs - Overview, Apps, Websites, Switches, Calendar - the range
// (Today / Yesterday / 7 days / 30 days, hidden on Calendar), Export CSV and the gear (Display settings).
//
// Where it opens: on the tab and range of the Display settings the FIRST time the page is visited after the window opens;
// after that on the last tab and range, for as long as the agent runs (the page module lives as long as the window does).
// A link that names a tab (Dashboard "Choose categories ..." -> {tab: "apps"}) opens that tab.
//
// A tab is its own module (js/pages/screentime/<tab>.js, registered in js/pages/screentime/index.js), built on its first
// visit and kept. Switching tabs only shows another pane: the tab is asked again only when its data is older than
// REFRESH_MS, belongs to another range, or something it shows changed (the categories). A range change reloads the SHOWN
// tab only (its charts update in place); the others notice the range changed when they are shown. The shown tab is
// refreshed every REFRESH_MS by a 1 s tick that exists only while this page is on screen.
// Python sends no text to format: every number arrives as the words to show (webapi/screentime.py).
import { wireIconReady } from "../appicon.js";
import { ld } from "../bridge.js";
import { el, empty, errorCard, ic, seg, skRows, toast } from "../ui.js";
import { tabs as registry } from "./screentime/index.js";
// The first tab and what it needs are named here too, so the browser fetches them in the same round as this file.
import "./screentime/overview.js";
import "./screentime/kit.js";
import "../charts.js";

export const TABS = [["overview", "Overview"], ["apps", "Apps"], ["websites", "Websites"], ["switches", "Switches"], ["calendar", "Calendar"]];
export const RANGES = ["Today", "Yesterday", "7 days", "30 days"];
export const REFRESH_MS = 60_000;                  // the shown tab asks again this often
export const freshMs = 30_000;                     // a page that was away and got a "dirty" flag reloads when older than this
const SKELETON_MS = 300;

const STYLE = `<style>
.st { gap: var(--sp-3); }
.st .page-h { min-height: calc(36px * var(--s)); }
.st-panes { flex: 1; min-height: 0; display: flex; flex-direction: column; position: relative; }
.st-pane { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: var(--sp-3); overflow-y: auto; }
.st-pane.st-fill { overflow: hidden; }
.st-content.st-fill { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.st-pane[hidden], .st-pane > [hidden] { display: none; }
.st-pane > .card, .st-pane > .stack { flex: none; }
.st-pane[aria-busy="true"] { opacity: .6; transition: opacity var(--dur) var(--ease); }
.st-sk { position: absolute; inset: 0; background: var(--surface-2); z-index: 1; overflow: hidden; }
.st-range[hidden] { display: none; }
</style>`;

// ---------------------------------------------------------------- pure helpers (node tests)

/** The tab id a link asked for (`opts.tab`), or null when it names none we know. */
export function tabFromOpts(opts) {
  const id = opts && typeof opts.tab === "string" ? opts.tab.toLowerCase() : null;
  return id && TABS.some((t) => t[0] === id) ? id : null;
}

/** Does a tab that has been loaded need to ask again now? `st` = {status, stale, range, loadedAt}. Only a "ready" tab does;
 *  one whose data is for another range, was marked stale, or is older than REFRESH_MS. */
export function needsLoad(st, wantRange, now, usesRange = true) {
  if (st.status !== "ready") return false;
  return !!st.stale || (usesRange && st.range !== wantRange) || now - st.loadedAt >= REFRESH_MS;
}

/** The tab / range to open on from the Display settings (`display_get`'s st_tab / st_range, or `screentime_get`'s `defaults`). */
export function defaultsOf(d) {
  const tab = d && typeof (d.tab || d.st_tab) === "string" ? String(d.tab || d.st_tab).toLowerCase() : "overview";
  const range = d && (d.range || d.st_range);
  return { tab: TABS.some((t) => t[0] === tab) ? tab : "overview", range: RANGES.includes(range) ? range : "Today" };
}

// ---------------------------------------------------------------- state (lives as long as the page module)

let host = null, page = null;
let state = null;                    // {tab, range}: where the owner is; null until the first load picks the defaults
let defaults = null;                 // what the Display settings say now
let pendingTab = null;               // a link's tab that came in before the defaults did
let shown = false;
const tabsState = new Map();         // tab id -> {id, pane, content, emptyEl, mod, status, promise, loading, again, stale, loadedAt, range, seq, skeleton}

const q = (k) => host.querySelector(`[data-k="${k}"]`);
const report = (where, err) => { try { ld.call("log_client_error", `${where}: ${err && err.stack ? err.stack : err}`.slice(0, 2000)).catch(() => {}); } catch (_) { /* never throws */ } };
const titleOf = (id) => (TABS.find((t) => t[0] === id) || [id, id])[1];

function stateOf(id) {
  let st = tabsState.get(id);
  if (!st) {
    const pane = host.querySelector(`[data-pane="${id}"]`);
    st = { id, pane, content: pane.querySelector(".st-content"), emptyEl: pane.querySelector(".st-empty"), mod: null, status: "new",
      promise: null, loading: null, again: false, stale: false, loadedAt: 0, range: null, seq: 0, skeleton: null, first: null };
    tabsState.set(id, st);
  }
  return st;
}

const usesRange = (st) => !(st.mod && st.mod.usesRange === false) && st.id !== "calendar";

// ---------------------------------------------------------------- loading a tab

function comingSoon(title) {
  return {
    usesRange: false,
    mount(content) { content.append(el(`<div class="card">${empty("k-brand", "sparkle", "Coming soon", `${title} isn't in the new window yet.`)}</div>`)); },
    async load() {},
  };
}

function tabApi(st) {
  return {
    id: st.id, el: st.content, page,
    range: () => state.range,
    reload: () => reloadTab(st),
    stale: () => { st.stale = true; },
    go: (tab, opts) => select(tab, opts),
  };
}

function showEmpty(st, on) {
  st.content.hidden = on;
  st.emptyEl.hidden = !on;
}

/** Ask for the tab's data and show it. A reply for a range that is not the current one any more is dropped (-> false). */
async function fill(st) {
  const my = ++st.seq, range = state.range, mod = st.mod;
  const other = st.status === "ready" && st.range !== range;       // showing another range's numbers until these arrive
  if (other) st.pane.setAttribute("aria-busy", "true");
  try {
    if (typeof mod.load === "function") {
      await mod.load();
      if (my !== st.seq) return false;
    } else {
      const data = st.first && st.first.range === range ? st.first : await ld.call("screentime_get", st.id, range);
      st.first = null;
      if (my !== st.seq) return false;
      showEmpty(st, !!data.empty);
      if (!data.empty) mod.render(data);
    }
  } finally {
    if (my === st.seq) st.pane.removeAttribute("aria-busy");
  }
  st.range = range;
  st.pane.dataset.range = range;                  // (what the pane shows now: tests and the perf harness wait for it)
  st.loadedAt = Date.now();
  st.stale = false;
  return true;
}

/** Build a tab on its first visit and load it. One promise per tab, so a second request waits for the first. */
function ensure(st) {
  if (st.promise) return st.promise;
  st.status = "loading";
  const timer = setTimeout(() => {              // a skeleton only if it takes a while
    if (st.status !== "loading" || st.skeleton) return;
    st.skeleton = el(`<div class="st-sk">${skRows(8)}</div>`);
    st.pane.parentNode.append(st.skeleton);
  }, SKELETON_MS);
  st.promise = (async () => {
    try {
      if (!st.mod) {
        const loader = registry[st.id];
        const loaded = loader ? await loader() : comingSoon(titleOf(st.id));
        st.mod = loaded && loaded.mount ? loaded : (loaded && loaded.default) || loaded;
        if (!st.mod || typeof st.mod.mount !== "function") throw new Error(`Tab "${st.id}" has no mount()`);
        st.mod.mount(st.content, tabApi(st));
        if (state.tab === st.id && st.mod.show) { try { st.mod.show(); } catch (err) { report(`screentime/${st.id}.show`, err); } }
      }
      st.stale = false;
      await fill(st);
      st.status = "ready";
    } catch (err) {
      st.status = "error";
      report(`screentime/${st.id} first visit`, err);
      const card = el('<div class="st-sk"></div>');
      card.append(errorCard(err && err.message, () => { card.remove(); st.promise = null; ensure(st); }));
      st.pane.parentNode.append(card);
    } finally {
      clearTimeout(timer);
      if (st.skeleton) { st.skeleton.remove(); st.skeleton = null; }
      st.promise = null;
    }
    if (st.status === "ready" && state.tab === st.id && needsLoad(st, state.range, Date.now(), usesRange(st))) reloadTab(st);
  })();
  return st.promise;
}

/** Ask again, quietly (no skeleton): one request at a time, one more after it if something changed meanwhile. */
function reloadTab(st) {
  if (st.promise) return st.promise;
  if (st.status !== "ready") return Promise.resolve();
  if (st.loading) { st.again = true; return st.loading; }
  st.loading = (async () => {
    try {
      st.stale = false;
      await fill(st);
    } catch (err) {
      st.stale = true;                       // not loaded: the next show tries again
      report(`screentime/${st.id}.load`, err);
      if (shown && state.tab === st.id && st.range !== state.range) toast("Screen Time couldn't be loaded.");      // (the numbers on screen are for another range)
    } finally {
      st.loading = null;
    }
    if (st.again) { st.again = false; reloadTab(st); }
  })();
  return st.loading;
}

// ---------------------------------------------------------------- tabs and range

function renderControls() {
  for (const btn of host.querySelectorAll('.tabs [role="tab"]')) btn.setAttribute("aria-selected", String(btn.dataset.tab === state.tab));
  for (const b of q("range").querySelectorAll("[data-v]")) b.setAttribute("aria-pressed", String(b.dataset.v === state.range));
  q("range").hidden = state.tab === "calendar";
}

/** Show a tab. Its content is asked for only if it was never loaded, is for another range, is marked stale, or is old. */
export function select(id, opts) {
  if (!TABS.some((t) => t[0] === id)) id = "overview";
  const prev = state.tab;
  state.tab = id;
  renderControls();
  for (const pane of host.querySelectorAll(".st-pane")) pane.hidden = pane.dataset.pane !== id;
  const st = stateOf(id);
  if (prev !== id) {
    const before = tabsState.get(prev);
    if (before && before.mod && before.mod.hide) { try { before.mod.hide(); } catch (err) { report(`screentime/${prev}.hide`, err); } }
  }
  if (st.status === "new") ensure(st);
  else {
    if (st.mod && st.mod.show) { try { st.mod.show(opts); } catch (err) { report(`screentime/${id}.show`, err); } }
    if (needsLoad(st, state.range, Date.now(), usesRange(st))) reloadTab(st);
  }
}

function setRange(range) {
  if (!RANGES.includes(range) || range === state.range) return;
  state.range = range;
  for (const st of tabsState.values()) st.seq++;           // a reply for the old range that is still on its way is dropped
  renderControls();
  const st = stateOf(state.tab);
  if (st.status === "ready") reloadTab(st);
  else if (st.status === "new") ensure(st);
}

// ---------------------------------------------------------------- Export CSV, gear

async function exportCsv(button) {
  button.disabled = true;
  try {
    const r = await ld.call("screentime_export_csv");
    if (r && !r.cancelled) toast(`${r.rows} row${r.rows === 1 ? "" : "s"} saved to ${r.path}`);
  } catch (err) {
    toast((err && err.message) || "Couldn't save the file.");
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- page contract

export const events = [];                          // nothing is pushed in: the shown tab refreshes itself every REFRESH_MS

export function mount(h, p) {
  host = h;
  page = p;
  host.innerHTML = `${STYLE}<div class="page page-fill st">
    <div class="page-h"><h1>Screen Time</h1>
      <span class="st-range" data-k="range">${seg("range", RANGES.map((r) => [r, r]), "")}</span>
      <button class="btn" type="button" data-k="csv">${ic("download")}Export CSV</button>
      <button class="icon-btn" type="button" data-k="gear" aria-label="Display settings" title="Display settings">${ic("settings")}</button></div>
    <div class="tabs" role="tablist">${TABS.map(([id, label]) => `<button type="button" role="tab" data-tab="${id}" aria-selected="false">${label}</button>`).join("")}</div>
    <div class="st-panes">${TABS.map(([id]) => `<div class="st-pane${id === "apps" || id === "websites" ? " st-fill" : ""}" data-pane="${id}" role="tabpanel" ${id === "overview" ? "" : "hidden"}>` +
      `<div class="st-content${id === "apps" || id === "websites" ? " st-fill" : ""}"></div>` +
      `<div class="card st-empty" hidden>${empty("k-mode", "chart", "Tracking started - come back in a few minutes", "Lockdown records which app or site is in front. Nothing leaves this PC.")}</div></div>`).join("")}</div>
  </div>`;
  host.querySelector(".tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b && state) select(b.dataset.tab);
  });
  host.querySelector(".tabs").addEventListener("keydown", (e) => {
    if (!state || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
    const i = TABS.findIndex((t) => t[0] === state.tab), n = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length][0];
    select(n);
    host.querySelector(`[data-tab="${n}"]`).focus();
  });
  host.addEventListener("seg", (e) => { if (e.detail && e.detail.name === "range" && state) setRange(e.detail.value); });
  q("csv").addEventListener("click", (e) => exportCsv(e.currentTarget));
  q("gear").addEventListener("click", () => { import("../display.js").then((m) => m.openDisplay()).catch((err) => report("screentime.gear", err)); });
  wireIconReady(ld.on, host);
  // a category changed anywhere: every tab shows it next time it is shown, the shown one now
  window.addEventListener("ld:categories-changed", () => {
    for (const st of tabsState.values()) st.stale = true;
    if (shown && state) reloadTab(stateOf(state.tab));
  });
  // the Display settings changed: remember what they say; while this page is on screen the view follows what was just chosen
  window.addEventListener("ld:display", (e) => {
    const next = defaultsOf(e.detail);
    const before = defaults;
    defaults = next;
    if (!shown || !state || !before) return;
    if (next.range !== before.range) setRange(next.range);
    if (next.tab !== before.tab) select(next.tab);
  });
  page.every(() => {
    if (!state) return;
    const st = tabsState.get(state.tab);
    if (st && st.status === "ready" && !st.loading && !st.promise && Date.now() - st.loadedAt >= REFRESH_MS) reloadTab(st);
  });
}

/** One Api call: the first visit (null tab and range = the Display settings' ones, or the tab a link named) gets the data of
 *  where it opens, and `defaults` with it; later calls refresh the shown tab. */
export async function load() {
  if (!state) {
    const first = await ld.call("screentime_get", pendingTab, null);
    defaults = defaultsOf(first.defaults);
    state = { tab: first.tab, range: first.range };
    pendingTab = null;
    renderControls();
    for (const pane of host.querySelectorAll(".st-pane")) pane.hidden = pane.dataset.pane !== state.tab;
    stateOf(state.tab).first = first;             // (the tab uses this answer instead of asking again)
  }
  const st = stateOf(state.tab);
  if (st.promise || st.status === "new") await ensure(st);
  else await reloadTab(st);
}

/** `opts.tab` opens that tab; otherwise the page is as it was left. */
export function show(opts) {
  shown = true;
  const want = tabFromOpts(opts);
  if (!state) { pendingTab = want || pendingTab; return; }
  if (want && want !== state.tab) return select(want, opts);
  const st = stateOf(state.tab);
  if (st.mod && st.mod.show) { try { st.mod.show(opts); } catch (err) { report(`screentime/${state.tab}.show`, err); } }
  if (needsLoad(st, state.range, Date.now(), usesRange(st))) reloadTab(st);
}

export function hide() {
  shown = false;
  const st = state && tabsState.get(state.tab);
  if (st && st.mod && st.mod.hide) { try { st.mod.hide(); } catch (err) { report(`screentime/${state.tab}.hide`, err); } }
}

export function patch() { /* no events */ }
