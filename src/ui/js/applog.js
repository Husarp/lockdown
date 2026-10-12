// The App log (Settings > App log): everything Lockdown did and everything that happened to it, newest first, grouped by day.
//
// Python (webapi/applog_api.py) does all the filtering and searching and hands out the events a block at a time
// (applog_get, 300 at once; scrolling near the end - or "Show older" - asks for the next older block). The list is ONE
// virtual list (js/vlist.js): about 25 row nodes whether there are 300 events or 20 000; day headings are rows of the same
// height. Filter chips (All / Changes / Updates / Problems / Anti-Bypass) and the search box start the list again from the
// newest event; the search waits SEARCH_MS for the next key. Copy puts the text of the (filtered) newest events on the
// clipboard; Save log… opens the owner's own Save dialog in Python - the page never names a path.
// Nothing about the sites or apps you visit is ever in this log (applog.py): this page cannot show what is not there.
import { ld } from "./bridge.js";
import { empty, ic, seg, setText, toast } from "./ui.js";
import { createVList } from "./vlist.js";

export const CHIPS = [["all", "All"], ["changes", "Changes"], ["updates", "Updates"], ["problems", "Problems"], ["antibypass", "Anti-Bypass"]];
export const PAGE = 300;
export const SEARCH_MS = 150;
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const PROBLEMS = new Set(["problem", "crash", "killed", "watchdog", "window_failed", "window_retry", "hosts_repaired", "clock_changed", "list_failed", "update_failed", "update_check_failed", "challenge_failed"]);
const GUARD = new Set(["challenge_passed", "antibypass_change", "off", "on", "pause", "emergency", "tray_exit", "uninstall"]);

// ---------------------------------------------------------------- plain helpers (tested in node)

/** "2026-10-12 14:03:11" -> "2026-10-12". */
export const dayOf = (ts) => String(ts || "").slice(0, 10);

/** "Today" / "Yesterday" / "Sunday 11 October" (the year too when it is not this year) for a day "YYYY-MM-DD". */
export function dayHeading(day, today) {
  if (day === today) return "Today";
  const [y, m, d] = day.split("-").map(Number);
  const [ty, tm, td] = today.split("-").map(Number);
  const gap = Math.round((Date.UTC(ty, tm - 1, td) - Date.UTC(y, m - 1, d)) / 86400000);
  if (gap === 1) return "Yesterday";
  const name = `${DAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]} ${d} ${MONTHS[m - 1]}`;
  return y === ty ? name : `${name} ${y}`;
}

/** The list rows for events (newest first): a heading row before the first event of each day, then the events. */
export function buildRows(events, today) {
  const rows = [];
  let day = null;
  for (const e of events) {
    const d = dayOf(e.ts);
    // (a heading is keyed by its first event: the clock can go back, so a day may come twice)
    if (d !== day) { day = d; rows.push({ k: `d${e.id}`, head: dayHeading(d, today), day: d }); }
    rows.push({ k: `e${e.id}`, e });
  }
  return rows;
}

/** How an event looks: "bad" (a problem), "guard" (Anti-Bypass), "" (everything else). */
export const toneOf = (kind) => (PROBLEMS.has(kind) ? "bad" : GUARD.has(kind) ? "guard" : "");

/** "14:03:11" of an event's time. */
export const timeOf = (ts) => String(ts || "").slice(11, 19);

// ---------------------------------------------------------------- the card

export const STYLE = `<style>
.al-bar { display: flex; flex-wrap: wrap; gap: var(--sp-2); align-items: center; padding: var(--sp-3) var(--sp-4); }
.al-bar .search { flex: 1 1 180px; min-width: 0; max-width: 320px; }
.al-bar .grow { flex: 1; }
.al-list { height: clamp(calc(var(--row-h-sm) * 6), 55vh, calc(var(--row-h-sm) * 14)); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); background: var(--surface); }
.al-row { display: grid; grid-template-columns: calc(66px * var(--s)) minmax(0, 1fr); align-items: center; gap: var(--sp-3); padding: 0 var(--sp-4); font-size: var(--fs-sm); border-bottom: 1px solid var(--line); }
.al-row .t { color: var(--text-3); font-variant-numeric: tabular-nums; }
.al-row .x { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.al-row .s { color: var(--text-3); margin-right: var(--sp-2); }
.al-row.bad .x { color: var(--k-block); }
.al-row.guard .x { color: var(--k-protect); }
.al-day { display: flex; align-items: flex-end; padding: 0 var(--sp-4) var(--sp-1); font-weight: 600; font-size: var(--fs-sm); color: var(--text-2); background: var(--surface-2); border-bottom: 1px solid var(--line); }
.al-foot { display: flex; flex-wrap: wrap; gap: var(--sp-2); align-items: center; padding: var(--sp-2) var(--sp-4); font-size: var(--fs-sm); color: var(--text-2); }
.al-foot .grow { flex: 1; }
.al-empty { position: absolute; inset: 0; display: grid; place-items: center; }
.al-empty[hidden] { display: none; }
</style>`;

/** The card's HTML (Settings puts it on the page). */
export function appLogCard() {
  return `${STYLE}<section class="card" data-card="applog">
    <header class="card-h"><h3>App log</h3><span class="muted small">Everything Lockdown did and everything that happened to it - never the sites or apps you use.</span></header>
    <div class="al-bar">
      ${seg("alchip", CHIPS, "all")}
      <div class="search">${ic("search")}<input class="input" placeholder="Search the log" aria-label="Search the log" data-al="search" autocomplete="off" spellcheck="false"></div>
      <span class="grow"></span>
      <button class="btn btn-sm" type="button" data-al="copy">${ic("file")}Copy</button>
      <button class="btn btn-sm" type="button" data-al="save">${ic("download")}Save log…</button>
    </div>
    <div class="al-list" data-al="list" tabindex="0" aria-label="App log"><div class="al-empty" data-al="empty" hidden></div></div>
    <div class="al-foot"><span class="grow" data-al="count">&nbsp;</span><button class="btn btn-sm" type="button" data-al="more" hidden>Show older</button></div>
  </section>`;
}

let root = null, vl = null, list = null;
let chip = "all", query = "", events = [], more = false, total = 0, busy = false, searchTimer = 0, generation = 0;
const q = (k) => root.querySelector(`[data-al="${k}"]`);

const todayText = () => {
  const n = new Date();
  return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, "0")}-${String(n.getDate()).padStart(2, "0")}`;
};

function createRow() {
  const d = document.createElement("div");
  d.className = "al-row";
  d.innerHTML = `<span class="t"></span><span class="x"></span>`;
  d._ev = true;
  return d;
}

function renderRow(node, row) {
  if (row.head) {                                    // a day heading (a reused node may have been an event)
    node.className = "vl-row al-day";
    node.title = "";
    node.replaceChildren(document.createTextNode(row.head));
    node._ev = false;
    return;
  }
  if (!node._ev) {
    node.innerHTML = `<span class="t"></span><span class="x"></span>`;
    node._ev = true;
  }
  const e = row.e;
  node.className = `vl-row al-row ${toneOf(e.kind)}`.trim();
  const [t, x] = node.children;
  setText(t, timeOf(e.ts));
  x.replaceChildren();
  if (e.source !== "agent") {
    const s = document.createElement("span");
    s.className = "s";
    s.textContent = e.source;
    x.append(s);
  }
  x.append(document.createTextNode(e.text));
  node.title = `${e.ts}  [${e.source}]  ${e.text}`;
}

/** Wire the card once (after Settings put appLogCard() on the page). `host` = an element that contains it. */
export function mountAppLog(host) {
  root = host;
  list = q("list");
  vl = createVList(list, { rowVar: "--row-h-sm", rows: [], key: (row) => row.k, create: createRow, render: renderRow });
  root.addEventListener("seg", (e) => { if (e.detail.name === "alchip") { chip = e.detail.value; restart(); } });
  q("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { query = q("search").value.trim(); restart(); }, SEARCH_MS);
  });
  q("more").addEventListener("click", () => loadOlder());
  q("copy").addEventListener("click", copyLog);
  q("save").addEventListener("click", saveLog);
  list.addEventListener("scroll", () => {
    if (more && !busy && list.scrollTop + list.clientHeight >= list.scrollHeight - vl.rowHeight * 6) loadOlder();
  }, { passive: true });
}

async function fetchBlock(before) {
  return ld.call("applog_get", chip, query, before || null, PAGE);
}

/** (Re)load from the newest event, with the current chip and search. */
export async function loadAppLog(force = false) {
  if (!root) return;
  if (!force && events.length && list.scrollTop > 0) return;   // (reading older events: a reload of the page leaves them be)
  const mine = ++generation;
  const r = await fetchBlock(null);
  if (mine !== generation) return;                    // (the filter changed meanwhile: that load wins)
  events = r.rows;
  more = r.more;
  total = r.total == null ? total : r.total;
  paint(true);
}

function restart() { loadAppLog(true).catch((err) => toast((err && err.message) || "That didn't work.")); }

async function loadOlder() {
  if (!more || busy || !events.length) return;
  busy = true;
  const mine = generation;
  try {
    const r = await fetchBlock(events[events.length - 1].id);
    if (mine !== generation) return;
    events = events.concat(r.rows);
    more = r.more;
    paint(false);
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
  } finally {
    busy = false;
  }
}

function paint(fromTop) {
  vl.setRows(buildRows(events, todayText()), { keepScroll: !fromTop });
  const empt = q("empty");
  if (!events.length) {
    empt.hidden = false;
    empt.innerHTML = total === 0 && chip === "all" && !query
      ? empty("k-grey", "history", "Nothing logged yet", "Lockdown writes here when it starts, updates, or you change something.")
      : empty("k-grey", "search", "Nothing matches", "Try another word or another filter.");
  } else if (!empt.hidden) {
    empt.hidden = true;
    empt.innerHTML = "";
  }
  const filtered = chip !== "all" || query;
  setText(q("count"), events.length
    ? `${events.length}${more ? "+" : ""} event${events.length === 1 ? "" : "s"} shown${filtered ? "" : ` of ${total}`} · kept for 90 days`
    : filtered ? "No matching events" : "No events yet");
  q("more").hidden = !more;
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
}

async function copyLog() {
  const b = q("copy");
  b.disabled = true;
  try {
    const r = await ld.call("applog_text", chip, query);
    await copy(r.text);
    toast("Copied the log - paste it where you want to share it.");
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
  } finally {
    b.disabled = false;
  }
}

async function saveLog() {
  const b = q("save");
  b.disabled = true;
  try {
    const r = await ld.call("applog_save");
    if (!r.cancelled) toast(`Saved to ${r.path}`);
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
  } finally {
    b.disabled = false;
  }
}
