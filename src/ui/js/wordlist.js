// The word-list dialogs of Blocking > Site protection (BLUEPRINT 3.3), all `once` (one open at a time):
//   a ready list (Adult words - English / Polish): a tick per word (untick = don't block it), "X of N words blocked",
//     search, All on, Save / Cancel;
//   your words / exceptions: add (lower case, spaces collapsed, no duplicates, new ones on top), x to remove, "N entries",
//     search, Save / Cancel.
// The words are in a virtual list (js/vlist.js), so a few thousand words cost a few dozen rows. Nothing is saved until Save.
// Python decides what the save loosens (keywords.looser): `words_update` comes back as a challenge ticket when it does, the
// challenge opens over this dialog and, if it is cancelled, the dialog stays with every edit in it.
//
//   openWordList(name, {onDone})   name = "adult_en" | "adult_pl" | "words" | "exceptions"; onDone() after a save.
import { ld } from "./bridge.js";
import { CANCELLED, guarded } from "./challenge.js";
import { openDialog } from "./dialog.js";
import { el, esc, ic, setText, toast } from "./ui.js";
import { createVList } from "./vlist.js";

export const SEARCH_MS = 120;
export const MAX_ROWS = 9;              // rows in view; more scroll
const MAX_WORD = 80;

// ---------------------------------------------------------------- plain helpers (tested in node)

/** A word as the old window kept it: spaces collapsed, lower case ("  Free   Spins " -> "free spins"). */
export const normalizeWord = (text) => String(text).split(/\s+/).filter(Boolean).join(" ").toLowerCase();

const WORD = /[a-z0-9]+/g;

/** Lower case, no accents (l with stroke too) - as search.py. */
export function plain(text) {
  return String(text).toLowerCase().replace(/ł/g, "l").normalize("NFKD").replace(/[̀-ͯ]/g, "");
}

export const typosAllowed = (word) => (word.length <= 3 ? 0 : word.length <= 6 ? 1 : 2);

/** Edits (insert, delete, change, swap two neighbours) from a to b; over `limit` is returned as limit + 1 (search.py). */
export function distance(a, b, limit) {
  if (Math.abs(a.length - b.length) > limit) return limit + 1;
  let prev2 = null, prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = new Array(b.length + 1).fill(0);
    cur[0] = i;
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0));
      if (prev2 && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) cur[j] = Math.min(cur[j], prev2[j - 2] + 1);
    }
    if (Math.min(...cur) > limit) return limit + 1;
    prev2 = prev; prev = cur;
  }
  return prev[prev.length - 1];
}

/** How well `text` matches: 0 = every typed word found exactly, higher = more typos, null = no match (search.py's score). */
export function fuzzyScore(query, text) {
  const words = plain(query).match(WORD) || [];
  if (!words.length) return 0;
  const hay = plain(text), tokens = hay.match(WORD) || [];
  let total = 0;
  for (const q of words) {
    if (hay.includes(q)) continue;
    const allowed = typosAllowed(q);
    if (!allowed) return null;
    const n = q.length;
    let best = allowed + 1;
    for (const t of tokens) {
      for (const cand of new Set([t, t.slice(0, n), t.slice(0, n + 1), t.slice(0, Math.max(n - 1, 1))])) {
        best = Math.min(best, distance(q, cand, allowed));
      }
    }
    if (best > allowed) return null;
    total += best;
  }
  return total;
}

/** The words that match the query, best first (then in their own order); no query = all of them (the same array). */
export function rankWords(words, query) {
  if (!String(query).trim()) return words;
  const found = [];
  words.forEach((w, i) => { const s = fuzzyScore(query, w); if (s !== null) found.push([s, i, w]); });
  found.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return found.map((f) => f[2]);
}

/** The count line: "73 of 110 words blocked" / "3 entries". */
export function countText(kind, words, off) {
  if (kind === "ready") return `${words.length - off.size} of ${words.length} words blocked`;
  return `${words.length} entr${words.length === 1 ? "y" : "ies"}`;
}

/** Put a new word on top; null when it is empty or already there (the old window ignored it). */
export function addWord(words, text) {
  const word = normalizeWord(text);
  if (!word || words.includes(word)) return null;
  return [word, ...words];
}

// ---------------------------------------------------------------- the dialog

const STYLE = `<style>
.wl-list { position: relative; height: calc(${MAX_ROWS} * var(--row-h-sm)); border: 1px solid var(--line); border-radius: var(--r-md); }
.wl-row { display: flex; align-items: center; gap: var(--sp-2); padding: 0 var(--sp-3); }
.wl-row .check { flex: 1; min-width: 0; height: 100%; }
.wl-row .w { flex: 1; min-width: 0; }
.wl-err { color: var(--k-block); font-size: var(--fs-sm); margin: 0; }
.wl-err:empty { display: none; }
.wl-empty { position: absolute; inset: 0; display: grid; place-items: center; color: var(--text-2); font-size: var(--fs-sm); pointer-events: none; }
.wl-empty[hidden] { display: none; }
</style>`;

const readyRow = () => el(`<div class="wl-row"><label class="check"><input type="checkbox"><span class="ellip"></span></label></div>`);
const ownRow = () => el(`<div class="wl-row"><span class="w ellip"></span><button class="icon-btn icon-btn-sm" type="button" data-act="x">${ic("x")}</button></div>`);

export async function openWordList(name, { onDone } = {}) {
  let list;
  try {
    list = await ld.call("wordlist_get", name);
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
    return null;
  }
  const ready = list.kind === "ready";
  const words = [...list.words];
  const off = new Set(list.off || []);
  let query = "", shown = words, searchTimer = 0;

  const body = `${STYLE}<p class="small muted" data-k="count"></p>
    ${ready ? "" : `<div class="hstack"><input class="input grow" data-k="add" autofocus placeholder="${esc(list.hint)}" maxlength="${MAX_WORD}" aria-label="Add" autocomplete="off"><button class="btn" type="button" data-act="add">Add</button></div>`}
    <div class="search">${ic("search")}<input class="input" data-k="search" ${ready ? "autofocus" : ""} placeholder="Search" aria-label="Search the words" autocomplete="off"></div>
    <div class="wl-list" data-k="list"><div class="wl-empty" data-k="none" hidden>Nothing matches</div></div>
    <p class="wl-err" data-k="err" role="alert"></p>`;
  const buttons = [];
  if (ready) buttons.push({ label: "All on", onClick: () => { off.clear(); vl.refresh(); recount(); return false; } });
  buttons.push({ label: "Cancel", value: "cancel", grow: ready }, { label: "Save", kind: "primary", onClick: () => { save(); return false; } });
  const handle = openDialog({ key: "word-list", title: list.title, body, buttons, onClose: () => clearTimeout(searchTimer) });
  if (!handle) return null;
  const root = handle.body;
  const q = (k) => root.querySelector(`[data-k="${k}"]`);
  const holder = q("list"), err = q("err"), none = q("none"), count = q("count");

  const vl = createVList(holder, {
    rowVar: "--row-h-sm", rows: shown, key: (w) => w, create: ready ? readyRow : ownRow,
    render(node, w) {
      node._w = w;
      if (ready) {
        setText(node.querySelector(".ellip"), w);
        const box = node.querySelector("input");
        if (box.checked !== !off.has(w)) box.checked = !off.has(w);
      } else setText(node.querySelector(".w"), w);
    },
  });

  function recount() { setText(count, countText(ready ? "ready" : "own", words, off)); }
  function apply(keepScroll = true) {
    shown = rankWords(words, query);
    none.hidden = shown.length > 0;
    vl.setRows(shown, { keepScroll });
    recount();
  }
  recount();

  holder.addEventListener("change", (e) => {
    const node = e.target.closest(".vl-row");
    if (!node || node._w === undefined) return;
    if (e.target.checked) off.delete(node._w); else off.add(node._w);
    recount();
  });
  holder.addEventListener("click", (e) => {
    const node = e.target.closest(".vl-row");
    if (!node || !e.target.closest('[data-act="x"]')) return;
    const at = words.indexOf(node._w);
    if (at >= 0) words.splice(at, 1);
    apply(true);
  });
  q("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { query = q("search").value; apply(false); }, SEARCH_MS);
  });
  if (!ready) {
    const add = () => {
      const input = q("add"), next = addWord(words, input.value);
      input.value = "";
      if (next) { words.splice(0, words.length, ...next); apply(false); }
    };
    q("add").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
    root.querySelector('[data-act="add"]').addEventListener("click", add);
  }

  async function save() {
    err.textContent = "";
    const change = ready ? { off: [...list.other_off, ...[...off].sort()] } : { [list.kind]: [...words] };
    try {
      const r = await guarded(ld.call("words_update", change));
      if (r === CANCELLED) return;                 // stay open: the edits are still here
      handle.close("saved");
      if (onDone) onDone(r);
    } catch (e) {
      err.textContent = (e && e.message) || "That didn't work.";
    }
  }
  return handle;
}
