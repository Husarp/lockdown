// Screen Time > Apps and Websites: the top 40 with at least a minute of use, as a virtual list (vlist) with the real icons.
// One factory, two tabs: create("app") / create("site"). A row: rank, icon, name (+ the .exe for apps), the category chip
// "{Name} ▾" (opens the category list of the shared menu), switches / visits, a share bar in the category's colour, the time.
// Right-click anywhere on a row opens the shared app menu (category, Block it..., Add to group, Show in Explorer);
// "Browse all apps..." (apps only) opens the App browser in browse mode. What changing a category needs (the challenge while
// Anti-Bypass is locked) is decided in Python by those components; this file only asks.
import { iconKey, iconUrl } from "../../appicon.js";
import { openAppBrowser } from "../../appbrowser.js";
import { openAppMenu } from "../../appmenu.js";
import { el, esc, ic, setText } from "../../ui.js";
import { createVList } from "../../vlist.js";
import { setVar } from "./kit.js";

const STYLE = `<style>
.st-list .list-h { flex: none; }
.st-list .st-row { width: 100%; box-sizing: border-box; white-space: nowrap; }
@media (min-width: 861px) {
  .st-list .st-row { grid-template-columns: calc(22px * var(--s)) var(--app-icon) minmax(120px, 1.4fr) calc(130px * var(--s)) calc(76px * var(--s)) minmax(80px, 1.4fr) calc(96px * var(--s)); }
}
@media (max-width: 860px) {
  .st-list .st-row { grid-template-columns: var(--app-icon) minmax(0, 1fr) calc(130px * var(--s)) calc(96px * var(--s)); }
  .st-list .list-h .share { display: none; }
}
.st-list .st-vl-wrap { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
.st-list .st-row .rank { text-align: right; }
.st-list .st-row .num { font-variant-numeric: tabular-nums; }
.st-list .st-row .st-time { text-align: right; font-weight: var(--fw-semibold); }
.st-list .st-row .name b { max-width: 100%; }
.st-chip { height: calc(24px * var(--s)); padding: 0 var(--sp-2); border-radius: var(--r-pill); border: 1px solid var(--c, var(--line-strong));
  background: transparent; color: var(--c, var(--text-2)); font-size: var(--fs-sm); font-weight: var(--fw-semibold); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 100%; cursor: pointer; }
.st-chip:hover { background: color-mix(in srgb, var(--c, var(--text-2)) 14%, transparent); }
.st-chip:focus-visible { outline: 2px solid var(--brand); outline-offset: 1px; }
.st-row .meter { --k: var(--c); }
.st-foot { flex: none; display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-3) var(--sp-4); border-top: 1px solid var(--line); }
.st-foot .grow { color: var(--text-2); font-size: var(--fs-sm); }
.st-none { position: absolute; inset: 0; display: grid; place-items: center; color: var(--text-2); pointer-events: none; }
.st-none[hidden] { display: none; }
</style>`;

const LABELS = {
  app: { head: "App", count: "Switches", hint: "Right-click a row to set its category, block it or add it to a group." },
  site: { head: "Website", count: "Visits", hint: "Right-click a row to set its category, block it or add it to a group." },
};

// ---------------------------------------------------------------- pure helpers (node tests)

/** What the shared menu is told about a row. */
export const targetOf = (kind, r) => ({ kind, name: r.key, display: r.name, path: r.path || null });
/** The chip's words. */
export const chipText = (r) => `${r.cat_name} ▾`;

/** The tab module for one kind. */
export function create(kind) {
  const words = LABELS[kind];
  let root = null, list = null, noneEl = null, rangeShown = null;

  function createRow() {
    const row = el(`<div class="row st-row" role="listitem" tabindex="0"><span class="rank"></span>` +
      `<span class="aic"><img data-icon="" alt="" decoding="async" draggable="false"></span>` +
      `<div class="name"><b></b><span></span></div>` +
      `<span class="cell"><button type="button" class="st-chip" aria-haspopup="menu"></button></span>` +
      `<span class="small muted num sw"></span><span class="meter"><i></i></span><span class="num st-time"></span></div>`);
    row._rank = row.querySelector(".rank");
    row._img = row.querySelector("img");
    row._b = row.querySelector(".name b");
    row._sub = row.querySelector(".name span");
    row._chip = row.querySelector(".st-chip");
    row._sw = row.querySelector(".sw");
    row._bar = row.querySelector(".meter i");
    row._time = row.querySelector(".st-time");
    return row;
  }

  function renderRow(row, r, i) {
    row._r = r;
    setText(row._rank, String(i + 1));
    const key = iconKey(kind, r.key);
    if (row._img.dataset.icon !== key) {
      row._img.dataset.icon = key;
      row._img.src = iconUrl(kind, r.key);
    }
    setText(row._b, r.name);
    setText(row._sub, r.sub);
    setText(row._chip, chipText(r));
    setVar(row, "--c", r.color);
    setText(row._sw, String(r.count));
    const width = `${Math.max(2, Math.round(r.share * 100))}%`;
    if (row._bar.style.width !== width) row._bar.style.width = width;
    setText(row._time, r.time);
    row.setAttribute("aria-label", `${r.name}, ${r.time}, ${r.cat_name}`);
  }

  function mount(content) {
    root = content;
    root.innerHTML = `${STYLE}<div class="card list-card st-list">
      <div class="list-h st-row row" aria-hidden="true"><span class="cap rank">#</span><span></span><span class="cap">${words.head}</span><span class="cap">Category</span>` +
      `<span class="cap sw">${words.count}</span><span class="cap share">Share</span><span class="cap" style="text-align:right">Time</span></div>
      <div class="st-vl-wrap"><div class="st-vl" data-k="list" role="list" aria-label="${kind === "app" ? "Apps" : "Websites"} by time"></div>` +
      `<div class="st-none" data-k="none" hidden>Nothing recorded for this period yet.</div></div>
      <div class="st-foot"><span class="grow">${esc(words.hint)}</span>${kind === "app" ? `<button class="btn btn-sm" type="button" data-k="browse">${ic("search")}Browse all apps…</button>` : ""}</div>
    </div>`;
    const host = root.querySelector('[data-k="list"]');
    noneEl = root.querySelector('[data-k="none"]');
    list = createVList(host, { rowVar: "--row-h", rows: [], key: (r) => r.key, create: createRow, render: renderRow });
    // the category chip: the shared menu, opened on its category list; anywhere else on a row: right-click
    host.addEventListener("click", (e) => {
      const chip = e.target.closest(".st-chip");
      const row = chip && chip.closest(".vl-row");
      if (row && row._r) openAppMenu(chip, targetOf(kind, row._r), { start: "category" });
    });
    host.addEventListener("contextmenu", (e) => {
      const row = e.target.closest(".vl-row");
      if (!row || !row._r) return;
      e.preventDefault();
      openAppMenu(e, targetOf(kind, row._r), {});
    });
    host.addEventListener("keydown", (e) => {          // (Enter on a row = its menu under it, like the context-menu key)
      const row = e.target.closest && e.target.closest(".vl-row");
      if (e.key === "Enter" && row && row._r && e.target === row) { e.preventDefault(); openAppMenu(row, targetOf(kind, row._r), {}); }
    });
    const browse = root.querySelector('[data-k="browse"]');
    if (browse) browse.addEventListener("click", () => openAppBrowser({ mode: "browse" }));
  }

  function render(d) {
    const sameView = rangeShown === d.range;
    rangeShown = d.range;
    noneEl.hidden = d.rows.length > 0;
    list.setRows(d.rows, { keepScroll: sameView });
  }

  return { mount, render, get list() { return list; } };
}
