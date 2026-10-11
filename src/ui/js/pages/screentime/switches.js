// Screen Time > Switches: three numbers, switches per hour (the busiest hour stands out), and the five apps / sites switched to
// most with how the visits look (short checks, long stretches). Built once (mount); render(data) only updates: text when it
// changed, the chart with update(), the five rows keyed. Python sends every word (webapi/screentime.py `_switches`).
import { hourBarChart } from "../../charts.js";
import { openDialog } from "../../dialog.js";
import { iconKey, iconUrl } from "../../appicon.js";
import { el, ic, setText } from "../../ui.js";
import { setVar, syncRows } from "./kit.js";

const HELP = "Many switches with very short visits usually means checking out of habit.";

const STYLE = `<style>
.st-sw .stat .d.t-bad { color: var(--k-limit); } .st-sw .stat .d.t-ok { color: var(--ok); }
.st-sw .stat .d:empty { display: none; }
.st-sw .card-h .sub-note { color: var(--text-2); font-size: var(--fs-sm); }
.st-sw .st-peak { color: var(--text-2); font-size: var(--fs-sm); min-height: 1.4em; margin: 0 0 var(--sp-2); }
.st-sw .st-targets { display: grid; gap: var(--sp-2); }
.st-sw .st-target { display: flex; align-items: center; gap: var(--sp-3); background: var(--surface-2); border-radius: var(--r-md); padding: var(--sp-2) var(--sp-3) var(--sp-2) 0; overflow: hidden; }
.st-sw .st-target .stripe { align-self: stretch; width: 3px; background: var(--c, var(--text-3)); flex: none; }
.st-sw .st-target .grow { display: grid; min-width: 0; line-height: 1.3; }
.st-sw .st-target .grow b { font-weight: var(--fw-semibold); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.st-sw .st-target .grow span { font-size: var(--fs-sm); color: var(--c, var(--text-2)); }
.st-sw .st-target .nums { display: grid; justify-items: end; line-height: 1.2; }
.st-sw .st-target .nums b { font: var(--fw-semibold) var(--fs-lg)/1.1 var(--font-display); font-variant-numeric: tabular-nums; }
.st-sw .st-target .nums span { font-size: var(--fs-cap); color: var(--text-2); }
.st-sw .st-none { color: var(--text-2); }
.st-sw .st-none[hidden] { display: none; }
</style>`;

const TONE = { warn: "var(--k-limit)", ok: "var(--ok)", muted: "var(--text-3)" };

// ---------------------------------------------------------------- pure helpers (node tests)

/** The chart's data: the 24 counts (07-22 at least are drawn), and the words for no switch at all. */
export const hoursData = (h) => ({ counts: h.counts, emptyText: "No switches yet" });
/** The colour of a "most switched to" row's stripe and style text. */
export const toneColor = (tone) => TONE[tone] || TONE.muted;

// ---------------------------------------------------------------- the tab

let root = null, chart = null;
const q = (k) => root.querySelector(`[data-k="${k}"]`);

const cardHtml = (key) => `<div class="card stat" data-card="${key}"><span class="cap" data-n="label"></span><span class="v" data-n="value"></span><span class="d" data-n="sub"></span></div>`;

export function mount(content) {
  root = content;
  root.innerHTML = `${STYLE}<div class="stack st-sw">
    <div class="grid-3" data-k="cards">${["switches", "visit", "short"].map(cardHtml).join("")}</div>
    <div class="grid-2">
      <section class="card"><header class="card-h"><h3>Switches per hour</h3></header>
        <div class="card-b"><p class="st-peak" data-k="peak"></p><div data-k="chart"></div></div></section>
      <section class="card"><header class="card-h"><h3>Most switched to</h3>
        <button class="icon-btn icon-btn-sm" type="button" data-k="help" aria-label="About this list" title="About this list">${ic("info")}</button></header>
        <div class="card-b"><div class="st-targets" data-k="targets"></div><p class="st-none" data-k="none" hidden>No switches yet.</p></div></section>
    </div>
  </div>`;
  chart = hourBarChart(q("chart"), { label: "Switches per hour" });
  q("help").addEventListener("click", () => openDialog({ key: "st-sw-help", title: "Most switched to", body: `<p>${HELP}</p>`, buttons: [{ label: "OK", kind: "primary" }] }));
}

function createTarget() {
  const node = el(`<div class="st-target"><span class="stripe"></span><span class="aic"><img data-icon="" alt="" decoding="async" draggable="false"></span>` +
    `<div class="grow"><b></b><span></span></div><div class="nums"><b></b><span></span></div></div>`);
  node._img = node.querySelector("img");
  node._name = node.querySelector(".grow b");
  node._style = node.querySelector(".grow span");
  node._count = node.querySelector(".nums b");
  node._avg = node.querySelector(".nums span");
  return node;
}

function updateTarget(node, r) {
  const key = iconKey(r.kind, r.name);
  if (node._img.dataset.icon !== key) {
    node._img.dataset.icon = key;
    node._img.src = iconUrl(r.kind, r.name);
  }
  setText(node._name, r.display);
  setText(node._style, r.style);
  setVar(node, "--c", toneColor(r.tone));
  setText(node._count, String(r.count));
  setText(node._avg, r.avg);
}

export function render(d) {
  for (const c of d.cards) {
    const card = root.querySelector(`[data-card="${c.key}"]`);
    setText(card.querySelector('[data-n="label"]'), c.label);
    setText(card.querySelector('[data-n="value"]'), c.value);
    const sub = card.querySelector('[data-n="sub"]');
    setText(sub, c.sub);
    sub.classList.toggle("t-bad", c.tone === "bad");
    sub.classList.toggle("t-ok", c.tone === "ok");
  }
  setText(q("peak"), d.peak);
  chart.update(hoursData(d.hours));
  q("none").hidden = d.targets.length > 0;
  syncRows(q("targets"), d.targets, createTarget, updateTarget);
}
