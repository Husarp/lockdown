// Screen Time > Calendar: one month at a time (‹ ›, "›" off at the current month), each day coloured by its active time
// (levels at 1 / 2 / 4 / 6 hours, future days muted, a dot over the daily goal), four numbers for the month, and the details
// of the clicked day (its top 10 apps). It has no range: the page hides the range buttons and calls load() for every refresh.
// Built once (mount): 42 day buttons that are only updated. A month change asks `screentime_month`; a click on a day asks
// `screentime_day` and touches nothing else (the month is not read again).
import { ld } from "../../bridge.js";
import { iconKey, iconUrl } from "../../appicon.js";
import { openDialog } from "../../dialog.js";
import { el, esc, ic, setText, toast } from "../../ui.js";
import { syncRows } from "./kit.js";

export const usesRange = false;

const HELP = "Each day is coloured by its active screen time (darker = more); days over your daily goal have a red dot. Hover a day for its time.";
const HINT = "Click a day to see what you spent time on.";
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const CELLS = 42;

const STYLE = `<style>
.st-cal .cal-h { display: flex; align-items: center; gap: var(--sp-2); }
.st-cal .cal-h h3 { min-width: 9em; text-align: center; }
.st-cal .cal-h .grow { flex: 1; }
.st-cal .mcal { gap: var(--sp-1); }
.st-cal .mcal .dh { padding: 0 var(--sp-2) var(--sp-1); }
.st-cal .mcal .dc { position: relative; font: inherit; color: var(--text); text-align: left; cursor: pointer; min-height: calc(58px * var(--s)); }
.st-cal .mcal .dc[hidden] { display: none; }
.st-cal .mcal .dc.blank { visibility: hidden; }
.st-cal .mcal .dc.fut { background: transparent; border-style: dashed; color: var(--text-3); cursor: default; }
.st-cal .mcal .dc[data-l="1"] { background: color-mix(in srgb, var(--brand) 22%, var(--surface-3)); }
.st-cal .mcal .dc[data-l="2"] { background: color-mix(in srgb, var(--brand) 45%, var(--surface-3)); }
.st-cal .mcal .dc[data-l="3"] { background: color-mix(in srgb, var(--brand) 70%, var(--surface-3)); color: var(--on-brand); }
.st-cal .mcal .dc[data-l="4"] { background: var(--brand); color: var(--on-brand); }
.st-cal .mcal .dc[data-l="3"] .t, .st-cal .mcal .dc[data-l="4"] .t { color: var(--on-brand); }
.st-cal .mcal .dc:not(.fut):hover { border-color: var(--line-strong); }
.st-cal .mcal .dc.sel { outline: 2px solid var(--brand); outline-offset: 1px; }
.st-cal .mcal .dc[data-l="3"].sel, .st-cal .mcal .dc[data-l="4"].sel { outline-color: var(--text); }
.st-cal .mcal .dc:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.st-cal .mcal .dc .t { text-align: right; color: var(--text-2); }
.st-cal .mcal .dc.over::after { content: ""; position: absolute; top: calc(8px * var(--s)); right: calc(8px * var(--s)); width: calc(9px * var(--s)); height: calc(9px * var(--s));
  border-radius: 50%; background: var(--k-block); box-shadow: 0 0 0 2px var(--surface); }
.st-cal .st-lg { margin-top: var(--sp-3); display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; color: var(--text-2); font-size: var(--fs-sm); }
.st-cal .st-lg .lv { display: inline-flex; gap: 3px; }
.st-cal .st-lg .lv i { width: calc(14px * var(--s)); height: calc(14px * var(--s)); border-radius: 3px; border: 1px solid var(--line); background: var(--surface-3); }
.st-cal .st-lg .lv i:nth-child(2) { background: color-mix(in srgb, var(--brand) 22%, var(--surface-3)); }
.st-cal .st-lg .lv i:nth-child(3) { background: color-mix(in srgb, var(--brand) 45%, var(--surface-3)); }
.st-cal .st-lg .lv i:nth-child(4) { background: color-mix(in srgb, var(--brand) 70%, var(--surface-3)); }
.st-cal .st-lg .lv i:nth-child(5) { background: var(--brand); }
.st-cal .st-lg .gd { display: inline-flex; align-items: center; gap: var(--sp-1); margin-left: var(--sp-3); }
.st-cal .st-lg .gd i { flex: none; width: calc(9px * var(--s)); height: calc(9px * var(--s)); border-radius: 50%; background: var(--k-block); }
.st-cal .st-lg .gd[hidden] { display: none; }
.st-cal .stat .d:empty { display: none; }
.st-cal .card-h .sub-note { color: var(--text-2); font-size: var(--fs-sm); white-space: nowrap; }
.st-cal .st-hint { color: var(--text-2); }
.st-cal .st-hint[hidden] { display: none; }
.st-cal .st-dayrow { display: flex; align-items: center; gap: var(--sp-3); height: calc(34px * var(--s)); }
.st-cal .st-dayrow + .st-dayrow { border-top: 1px solid var(--line); }
.st-cal .st-dayrow .nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.st-cal .st-dayrow .tm { font-variant-numeric: tabular-nums; font-weight: var(--fw-semibold); }
</style>`;

// ---------------------------------------------------------------- pure helpers (node tests)

/** The month before / after (year, month 1..12). */
export function stepMonth(year, month, by) {
  const i = year * 12 + (month - 1) + by;
  return { year: Math.floor(i / 12), month: (i % 12) + 1 };
}

/** What day cell `i` (0..41) shows for a month reply: its day, or null for a blank cell before the 1st / after the last day. */
export const dayAt = (month, i) => month.days[i - month.offset] || null;

/** How many week rows the month needs (5 or 6, 4 for a February that starts on a Monday). */
export const weekRows = (month) => Math.ceil((month.offset + month.days.length) / 7);

// ---------------------------------------------------------------- the tab

let root = null;
let shownMonth = null;               // the last month reply on screen
let want = null;                     // {year, month} asked for by ‹ ›; null = this month (the first load)
let selected = null;                 // the ISO date whose details are shown
let seq = 0, daySeq = 0;
const cells = [];
const q = (k) => root.querySelector(`[data-k="${k}"]`);

const cardHtml = (key) => `<div class="card stat" data-card="${key}"><span class="cap" data-n="label"></span><span class="v" data-n="value"></span><span class="d" data-n="sub"></span></div>`;

export function mount(content, tab) {
  root = content;
  root.innerHTML = `${STYLE}<div class="stack st-cal">
    <section class="card"><div class="card-b">
      <div class="cal-h"><button class="icon-btn" type="button" data-k="prev" aria-label="Previous month" title="Previous month">${ic("chev-l")}</button>
        <h3 data-k="title" aria-live="polite"></h3>
        <button class="icon-btn" type="button" data-k="next" aria-label="Next month" title="Next month">${ic("chev-r")}</button>
        <button class="icon-btn icon-btn-sm" type="button" data-k="help" aria-label="About the calendar" title="About the calendar">${ic("info")}</button></div>
      <div class="mcal" data-k="grid" role="group" aria-label="Days of the month">${WEEKDAYS.map((d) => `<span class="dh">${d}</span>`).join("")}</div>
      <div class="st-lg"><span>Less</span><span class="lv"><i></i><i></i><i></i><i></i><i></i></span><span>More</span>
        <span class="gd" data-k="goaldot"><i></i><span data-k="goaltext"></span></span></div>
    </div></section>
    <div class="grid-4" data-k="stats">${["month", "average", "within", "busiest"].map(cardHtml).join("")}</div>
    <section class="card"><header class="card-h"><h3 data-k="daytitle">Day details</h3><span class="sub-note" data-k="daynote"></span></header>
      <div class="card-b"><p class="st-hint" data-k="hint">${esc(HINT)}</p><div data-k="dayrows"></div></div></section>
  </div>`;
  const grid = q("grid");
  for (let i = 0; i < CELLS; i++) {
    const b = el('<button type="button" class="dc blank" tabindex="-1"><b></b><span class="t"></span></button>');
    b._n = b.querySelector("b");
    b._t = b.querySelector(".t");
    b._date = null;
    cells.push(b);
    grid.append(b);
  }
  grid.addEventListener("click", (e) => {
    const b = e.target.closest(".dc");
    if (b && b._date && !b.classList.contains("fut")) pick(b._date);
  });
  q("prev").addEventListener("click", () => go(-1, tab));
  q("next").addEventListener("click", () => go(1, tab));
  q("help").addEventListener("click", () => openDialog({ key: "st-cal-help", title: "The calendar", body: `<p>${esc(HELP)}</p>`, buttons: [{ label: "OK", kind: "primary" }] }));
}

function go(by, tab) {
  if (!shownMonth || (by > 0 && !shownMonth.can_next)) return;
  want = stepMonth(shownMonth.year, shownMonth.month, by);
  tab.reload();
}

/** Ask for the month on screen (this month the first time) and show it. A reply that a newer click has overtaken is dropped. */
export async function load() {
  const my = ++seq, ask = want;
  const m = await ld.call("screentime_month", ask ? ask.year : null, ask ? ask.month : null);
  if (my !== seq) return;
  want = { year: m.year, month: m.month };
  renderMonth(m);
  if (selected && selected === m.today) refreshDay(selected);        // (today's details keep up with the minute)
}

function renderMonth(m) {
  shownMonth = m;
  setText(q("title"), m.title);
  q("next").disabled = !m.can_next;
  q("goaldot").hidden = !m.goal;
  setText(q("goaltext"), m.goal ? `Over your ${m.goal_label} goal` : "");
  const rows = weekRows(m);
  cells.forEach((b, i) => {
    b.hidden = i >= rows * 7;
    const d = dayAt(m, i);
    if (!d) {
      if (b._date !== null) { b._date = null; b.className = "dc blank"; b.removeAttribute("title"); b.removeAttribute("aria-label"); b.tabIndex = -1; b.dataset.l = ""; b.disabled = true; }
      return;
    }
    b._date = d.date;
    b.disabled = d.future;
    b.tabIndex = d.future ? -1 : 0;
    setText(b._n, String(d.day));
    setText(b._t, d.text);
    if (b.dataset.l !== String(d.level)) b.dataset.l = String(d.level);
    b.classList.toggle("blank", false);
    b.classList.toggle("fut", d.future);
    b.classList.toggle("over", d.over);
    b.classList.toggle("today", d.date === m.today);
    b.classList.toggle("sel", d.date === selected);
    const tip = d.tip || "";
    if ((b.title || "") !== tip) { if (tip) b.title = tip; else b.removeAttribute("title"); }
    const label = d.future ? `${d.day}, not yet` : (tip || String(d.day));
    if (b.getAttribute("aria-label") !== label) b.setAttribute("aria-label", label);
  });
  for (const c of m.stats) {
    const card = root.querySelector(`[data-card="${c.key}"]`);
    setText(card.querySelector('[data-n="label"]'), c.label);
    setText(card.querySelector('[data-n="value"]'), c.value);
    setText(card.querySelector('[data-n="sub"]'), c.sub);
  }
}

// ---------------------------------------------------------------- the day

function pick(date) {
  selected = date;
  for (const b of cells) b.classList.toggle("sel", b._date === date);
  refreshDay(date);
}

async function refreshDay(date) {
  const my = ++daySeq;
  let d;
  try {
    d = await ld.call("screentime_day", date);
  } catch (err) {
    if (my === daySeq) toast((err && err.message) || "Couldn't load that day.");
    return;
  }
  if (my !== daySeq) return;
  setText(q("daytitle"), d.title);
  setText(q("daynote"), d.note);
  const hint = q("hint");
  hint.hidden = d.rows.length > 0;
  setText(hint, d.rows.length ? HINT : "Nothing recorded that day.");
  syncRows(q("dayrows"), d.rows, () => {
    const node = el('<div class="st-dayrow"><span class="aic"><img data-icon="" alt="" decoding="async" draggable="false"></span><span class="nm"></span><span class="tm"></span></div>');
    node._img = node.querySelector("img");
    node._nm = node.querySelector(".nm");
    node._tm = node.querySelector(".tm");
    return node;
  }, (node, r) => {
    const key = iconKey("app", r.key);
    if (node._img.dataset.icon !== key) { node._img.dataset.icon = key; node._img.src = iconUrl("app", r.key); }
    setText(node._nm, r.name);
    setText(node._tm, r.time);
  });
}
