// The dialog for one of your own blocking lists (Blocking > Site protection > Your blocking lists): a name (optional) and a
// site address, Enter or Add puts the site on TOP of the list, a x takes one out, Save / Cancel. Opens once (`once`).
//
// The sites are in a virtual list (js/vlist.js): 20 000 of them cost a few dozen DOM rows and scroll like 20. The address is
// checked by Python (`host_normalize` - the same normalising as everywhere, "https://www.Reddit.com/x" -> "reddit.com") and
// Python checks every NEW site again when you Save. Taking sites out of a list that is on loosens the block: the save then
// comes back as a challenge ticket (antibypass.protection_looser) and the dialog stays until it is passed, with every edit
// still in it if you cancel.
//
//   openManualList(key, {onSaved})   onSaved(result) after a save.
import { ld } from "./bridge.js";
import { CANCELLED, guarded } from "./challenge.js";
import { openDialog } from "./dialog.js";
import { el, esc, ic, setText, toast } from "./ui.js";
import { createVList } from "./vlist.js";

export const MAX_ROWS = 9;              // rows in view; more scroll

// ---------------------------------------------------------------- plain helpers (tested in node)

/** "12 sites" / "1 site". */
export const countText = (n) => `${n.toLocaleString("en-US")} site${n === 1 ? "" : "s"}`;

/** [[host, name]] with a new site on top, or null when it is already there. `seen` is the Set of hosts (kept in step). */
export function withSite(entries, seen, host, name) {
  if (seen.has(host)) return null;
  seen.add(host);
  entries.unshift([host, String(name).split(/\s+/).filter(Boolean).join(" ")]);
  return entries;
}

/** Take the site `host` out (in place); false when it was not there. */
export function withoutSite(entries, seen, host) {
  const at = entries.findIndex((e) => e[0] === host);
  if (at < 0) return false;
  entries.splice(at, 1);
  seen.delete(host);
  return true;
}

// ---------------------------------------------------------------- the dialog

const STYLE = `<style>
.ml-list { position: relative; height: calc(${MAX_ROWS} * var(--row-h-sm)); border: 1px solid var(--line); border-radius: var(--r-md); }
.ml-row { display: flex; align-items: center; gap: var(--sp-2); padding: 0 var(--sp-2) 0 var(--sp-3); }
.ml-row .host { flex: 0 1 auto; font-weight: var(--fw-semibold); }
.ml-row .label { flex: 1; min-width: 0; color: var(--text-2); font-size: var(--fs-sm); }
.ml-row .label:empty { display: none; }
.ml-row .x { margin-left: auto; color: var(--text-2); }
.ml-err { color: var(--k-block); font-size: var(--fs-sm); margin: 0; }
.ml-err:empty { display: none; }
.ml-line { display: flex; gap: var(--sp-2); }
.ml-line .name { flex: 0 1 calc(150px * var(--s)); }
.ml-line .addr { flex: 1; }
</style>`;

const rowNode = () => el(`<div class="ml-row"><span class="host ellip"></span><span class="label ellip"></span><button class="icon-btn icon-btn-sm x" type="button" data-act="x">${ic("x")}</button></div>`);

export async function openManualList(key, { onSaved } = {}) {
  let list;
  try {
    list = await ld.call("manual_get", key);
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
    return null;
  }
  if (!list) { toast("That list isn't there any more."); if (onSaved) onSaved(null); return null; }
  const entries = list.entries.map((e) => [e[0], e[1]]);
  const seen = new Set(entries.map((e) => e[0]));

  const body = `${STYLE}<p class="small muted" data-k="count"></p>
    <div class="ml-line"><input class="input name" data-k="name" placeholder="Name (optional)" maxlength="80" aria-label="Name (optional)" autocomplete="off">
      <input class="input addr" data-k="addr" autofocus placeholder="site address, e.g. reddit.com" aria-label="Site address" autocomplete="off" spellcheck="false">
      <button class="btn" type="button" data-act="add">Add</button></div>
    <p class="ml-err" data-k="err" role="alert"></p>
    <div class="ml-list" data-k="list"></div>`;
  const handle = openDialog({
    key: "manual-list", title: list.name, body,
    buttons: [{ label: "Cancel", value: "cancel" }, { label: "Save", kind: "primary", onClick: () => { save(); return false; } }],
  });
  if (!handle) return null;
  const root = handle.body;
  const q = (k) => root.querySelector(`[data-k="${k}"]`);
  const holder = q("list"), err = q("err"), count = q("count"), name = q("name"), addr = q("addr");

  const vl = createVList(holder, {
    rowVar: "--row-h-sm", rows: entries, key: (e) => e[0], create: rowNode,
    render(node, e) {
      node._host = e[0];
      setText(node.querySelector(".host"), e[0]);
      setText(node.querySelector(".label"), e[1] ? `·  ${e[1]}` : "");
    },
  });
  const recount = () => setText(count, countText(entries.length));
  recount();

  async function add() {
    err.textContent = "";
    let host;
    try {
      host = await ld.call("host_normalize", addr.value);
    } catch (e) {
      err.textContent = (e && e.message) || "That didn't work.";
      return;
    }
    if (withSite(entries, seen, host, name.value)) {
      name.value = addr.value = "";
      vl.setRows(entries);                         // (scrolls to the top: the new site is the first row)
      recount();
    } else err.textContent = `${host} is in the list already.`;
    addr.focus();
  }

  holder.addEventListener("click", (e) => {
    const node = e.target.closest(".vl-row");
    if (!node || !e.target.closest('[data-act="x"]')) return;
    if (withoutSite(entries, seen, node._host)) { vl.setRows(entries, { keepScroll: true }); recount(); }
  });
  addr.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
  root.querySelector('[data-act="add"]').addEventListener("click", add);

  async function save() {
    err.textContent = "";
    try {
      const r = await guarded(ld.call("manual_save_entries", key, entries));
      if (r === CANCELLED) return;                 // stay open: the edits are still here
      handle.close("saved");
      if (onSaved) onSaved(r);
    } catch (e) {
      err.textContent = (e && e.message) || "That didn't work.";
    }
  }
  return handle;
}
