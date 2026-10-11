// The Display settings dialog (BLUEPRINT 3.11): the gear on the Dashboard and on Screen Time. Which Dashboard cards show
// and in what order, and the tab / range Screen Time opens with (never Calendar). Every click saves at once
// (display_set - cosmetic, so it needs no challenge) and tells the pages: a window event "ld:display" whose detail is
// what display_get now says; the Dashboard reloads on it, Screen Time can read its default from it.
//   import { openDisplay } from "../display.js";   gear.addEventListener("click", () => openDisplay());
import { ld } from "./bridge.js";
import { openDialog } from "./dialog.js";
import { el, esc, ic, seg, toast } from "./ui.js";

const row = (c) =>
  `<div class="dsp-row" data-card="${esc(c.key)}"><label class="check"><input type="checkbox" ${c.shown ? "checked" : ""} data-dsp="shown"><span class="ellip">${esc(c.title)}</span></label>` +
  `<span class="grow"></span><button class="icon-btn icon-btn-sm" type="button" data-dsp="up" aria-label="Move ${esc(c.title)} up">${ic("chev-u")}</button>` +
  `<button class="icon-btn icon-btn-sm" type="button" data-dsp="down" aria-label="Move ${esc(c.title)} down">${ic("chev-d")}</button></div>`;

const STYLE = `<style>
.dsp-rows { display: grid; gap: 2px; }
.dsp-row { display: flex; align-items: center; gap: var(--sp-1); min-height: calc(32px * var(--s)); }
</style>`;

/** What the dialog's controls say now, as display_set wants it. */
export function readDialog(body) {
  const rows = [...body.querySelectorAll(".dsp-row")];
  return {
    hidden: rows.filter((r) => !r.querySelector('[data-dsp="shown"]').checked).map((r) => r.dataset.card),
    order: rows.map((r) => r.dataset.card),
    tab: body.querySelector('[data-seg="tab"] [aria-pressed="true"]').dataset.v,
    range: body.querySelector('[data-seg="range"] [aria-pressed="true"]').dataset.v,
  };
}

let saving = Promise.resolve();

export async function openDisplay() {
  let d;
  try {
    d = await ld.call("display_get");
  } catch (err) {
    toast(err.message || "Couldn't load the display settings.");
    return null;
  }
  const body = el(`<div class="stack">${STYLE}
    <div class="stack-sm"><span class="cap">Dashboard shows</span><div class="dsp-rows" data-dsp="rows">${d.cards.map(row).join("")}</div></div>
    <div class="stack-sm"><span class="cap">Screen Time opens with</span>
      ${seg("tab", d.tabs.map((t) => [t, esc(t)]), d.st_tab)}
      ${seg("range", d.ranges.map((r) => [r, esc(r)]), d.st_range)}</div></div>`);
  const save = () => {
    const s = readDialog(body);
    saving = saving.then(() => ld.call("display_set", s.hidden, s.order, s.tab, s.range))
      .then((now) => window.dispatchEvent(new CustomEvent("ld:display", { detail: now })))
      .catch((err) => toast(err.message || "That didn't save."));
    return saving;
  };
  body.addEventListener("change", (e) => { if (e.target.matches('[data-dsp="shown"]')) save(); });
  body.addEventListener("seg", save);
  body.addEventListener("click", (e) => {
    const b = e.target.closest("[data-dsp=up],[data-dsp=down]");
    if (!b) return;
    const r = b.closest(".dsp-row");
    if (b.dataset.dsp === "up" && r.previousElementSibling) r.parentNode.insertBefore(r, r.previousElementSibling);
    else if (b.dataset.dsp === "down" && r.nextElementSibling) r.parentNode.insertBefore(r.nextElementSibling, r);
    else return;
    b.focus();
    save();
  });
  const h = openDialog({ key: "display", title: "Display", body, buttons: [{ label: "Done", kind: "primary" }] });
  return h ? h.done : null;
}
