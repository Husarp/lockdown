// The Emergency unlock dialog (BLUEPRINT 3.2). Python decides everything that counts: emergency_get_candidates says
// which kinds have uses left and which items can be freed (never a permanent block), and emergency_unlock checks it all
// again and takes the minutes from the kind - this file never sends a length. Several items at once are ONE use.
//
//   openEmergency({onDone})   onDone(result) after it worked: result = {until, names, alerts, left, text} where
//                             text is "{names} unlocked until HH:MM" (the page shows it as a notice).
import { iconKey, iconUrl } from "./appicon.js";
import { ld } from "./bridge.js";
import { openDialog } from "./dialog.js";
import { confirmButton, el, esc, px, setText, toast } from "./ui.js";
import { createVList } from "./vlist.js";

export const MAX_LIST_ROWS = 7;           // rows of the item list in view; more scroll

// ---------------------------------------------------------------- plain helpers (tested in node)

/** The kind that is chosen first: the shortest one that still has a use left (the smallest way out), else null. */
export function pickKind(kinds) {
  const spare = kinds.filter((k) => k.left > 0);
  return spare.length ? spare.reduce((a, b) => (b.minutes < a.minutes ? b : a)).id : null;
}

export const unlockLabel = (kind) => `Unlock for ${kind.minutes} min`;

/** "Confirm - uses 1 of 3" (+ "this week" when there are several kinds, so it is clear which one). */
export const confirmLabel = (kind, several) => `Confirm - uses 1 of ${kind.left}${several ? ` ${kind.per_text}` : ""}`;

/** What a kind says about itself: "3 of 3 left this week · resets Mon 12 Oct 00:00" / "none left · resets ...". */
export function kindText(kind) {
  return kind.left > 0 ? `${kind.left} of ${kind.allowed} left ${kind.per_text} · resets ${kind.resets_text}`
    : `none left · resets ${kind.resets_text}`;
}

/** The rows of the item list: a caption, then its items. */
export function listRows(blocked, open) {
  const rows = [];
  if (blocked.length) rows.push({ key: "c:blocked", cap: "Blocked now" }, ...blocked.map((i) => ({ key: `i:${i.id}`, item: i })));
  if (open.length) rows.push({ key: "c:open", cap: "Not blocked right now" }, ...open.map((i) => ({ key: `i:${i.id}`, item: i })));
  return rows;
}

/** When the next use comes back, when no kind has one: the earliest reset text. */
export function nextUse(kinds) {
  const sorted = kinds.filter((k) => k.resets).sort((a, b) => (a.resets < b.resets ? -1 : 1));
  return sorted.length ? sorted[0].resets_text : "";
}

const STYLE = `<style>
.em-kinds { display: grid; gap: var(--sp-2); }
.em-kind input { position: absolute; opacity: 0; pointer-events: none; }
.em-kind { position: relative; }
.em-kind:has(input:focus-visible) { outline: 2px solid var(--brand); outline-offset: 2px; }
.em-list { position: relative; border: 1px solid var(--line); border-radius: var(--r-md); }
.em-row { display: flex; align-items: center; gap: var(--sp-2); padding: 0 var(--sp-3); }
.em-row .cap { align-self: end; padding-bottom: 4px; }
.em-row .em-item { display: flex; align-items: center; gap: var(--sp-2); width: 100%; min-width: 0; cursor: pointer; }
.em-row .em-item .aic img { width: 100%; height: 100%; }
.em-err { color: var(--k-block); font-size: var(--fs-sm); }
.em-err:empty { display: none; }
</style>`;

function itemRow() {
  return el(`<div class="em-row"><div class="cap" hidden></div><label class="em-item check"><input type="checkbox"><span class="aic aic-sm"><img alt="" loading="lazy" decoding="async" draggable="false"></span><span class="ellip name"></span></label></div>`);
}

export async function openEmergency({ onDone } = {}) {
  let cand;
  try {
    cand = await ld.call("emergency_get_candidates");
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
    return null;
  }
  if (!cand.usable) { toast("Emergency unlock is turned off (Settings)."); return null; }
  const kinds = cand.kinds, several = kinds.length > 1;
  const picked = new Set();
  let chosen = pickKind(kinds);
  const rows = listRows(cand.blocked, cand.open);

  const kindsHtml = kinds.map((k) => `<label class="rcard k-emerg em-kind"><input type="radio" name="em-kind" value="${esc(k.id)}" ${k.id === chosen ? "checked" : ""} ${k.left > 0 ? "" : "disabled"}>` +
    `<span class="grow"><b>${k.minutes} min</b><span class="small muted">${esc(kindText(k))}</span></span>` +
    `<span class="uses" aria-hidden="true">${Array.from({ length: k.allowed }, (_, i) => `<i class="${i < k.allowed - k.left ? "used" : ""}"></i>`).join("")}</span></label>`).join("");
  const body = `${STYLE}<p class="small muted">Unblocks what you choose for a short time. Several at once count as one use. The time you use still counts toward your limits and allowances.</p>
    <div class="stack-sm"><span class="cap">How long</span><div class="em-kinds" data-k="kinds">${kindsHtml}</div></div>
    <div class="stack-sm" ${rows.length ? "" : "hidden"}><span class="cap">Unblock</span><div class="em-list" data-k="items"></div></div>
    <p class="small muted" data-k="none" ${rows.length ? "hidden" : ""}>Nothing on your list can be unlocked right now.</p>
    <label class="check"><input type="checkbox" data-k="alerts"><span>Pause bedtime and break alerts</span></label>
    <p class="small muted" data-k="alertsnote" ${cand.alerts_until ? "" : "hidden"}>${cand.alerts_until ? `Paused already, until ${esc(cand.alerts_until.slice(11, 16))}.` : ""}</p>
    <p class="small muted" data-k="perm" ${cand.permanent ? "" : "hidden"}>Permanently-blocked items can't be emergency-unlocked.</p>
    <p class="em-err" data-k="err" role="alert"></p>`;

  const handle = openDialog({ key: "emergency", title: "Emergency unlock", body, buttons: [{ label: "Close", value: "closed" }] });
  if (!handle) return null;
  const root = handle.body;
  const q = (k) => root.querySelector(`[data-k="${k}"]`);
  const err = q("err"), foot = handle.el.querySelector(".dlg-f");

  let vl = null;
  if (rows.length) {
    const holder = q("items");
    holder.style.height = Math.min(rows.length, MAX_LIST_ROWS) * px("--row-h-sm") + 2 + "px";
    vl = createVList(holder, {
      rowVar: "--row-h-sm", rows, key: (r) => r.key, create: itemRow,
      render(node, r) {
        const cap = node.firstChild, label = node.lastChild;
        cap.hidden = !r.cap;
        label.hidden = !!r.cap;
        node._row = r;
        if (r.cap) { setText(cap, r.cap); return; }
        const img = label.querySelector("img"), cat = r.item.icon.k === "category";
        const kind = cat ? "letter" : r.item.icon.k, name = cat ? r.item.name : r.item.icon.n, key = iconKey(kind, name);
        if (img.dataset.icon !== key) { img.dataset.icon = key; img.src = iconUrl(kind, name); }
        setText(label.querySelector(".name"), r.item.name);
        const box = label.querySelector("input");
        if (box.checked !== picked.has(r.item.id)) box.checked = picked.has(r.item.id);
      },
    });
    holder.addEventListener("change", (e) => {
      const node = e.target.closest(".vl-row");
      if (!node || !node._row || !node._row.item) return;
      if (e.target.checked) picked.add(node._row.item.id); else picked.delete(node._row.item.id);
      err.textContent = "";
    });
  }

  // the button: "Unlock for N min", then "Confirm - uses 1 of {left}" (a new one whenever the kind changes)
  let button = null;
  const setButton = () => {
    const kind = kinds.find((k) => k.id === chosen);
    const next = kind && kind.left > 0
      ? confirmButton({ label: unlockLabel(kind), confirmLabel: confirmLabel(kind, several), cls: "btn btn-primary", onConfirm: unlock })
      : el(`<span class="small muted grow">${esc(kinds.length ? `No emergency unlocks left - next one ${nextUse(kinds)}` : "No emergency unlock set up.")}</span>`);
    if (button) button.replaceWith(next); else foot.append(next);
    button = next;
  };
  setButton();
  q("kinds").addEventListener("change", (e) => { chosen = e.target.value; err.textContent = ""; setButton(); });

  async function unlock() {
    err.textContent = "";
    try {
      const result = await ld.call("emergency_unlock", [...picked], chosen, q("alerts").checked);
      handle.close(result);
      if (onDone) onDone(result);
    } catch (e) {
      err.textContent = (e && e.message) || "That didn't work.";
    }
  }
  return handle;
}
