// Blocking page (BLUEPRINT 3.2): the shell of the five tabs - Overview, Groups, Add, Site protection, Calendar - with the
// "Locked" strip, the green notice, and the entry points other pages use (editItem, editGroup, addPrefilled, confirm).
// A tab is its own module (js/pages/blocking/<tab>.js, registered in js/pages/blocking/index.js); each is built on its
// first visit and kept. Coming back to Blocking always shows Overview, as the old window did.
//
// Everything that changes the blocklist goes through the draft (js/draft.js): Python decides what loosens a block and
// asks for the challenge; nothing here compares old with new.
import "./blocking/early.js";          // first: it sends the Overview's data request while the rest is still being evaluated
import { ld } from "../bridge.js";
import { CANCELLED, guarded } from "../challenge.js";
import { autosaveOn } from "../draft.js";
import { router } from "../router.js";
import { el, empty, errorCard, ic, setText, skRows, toast } from "../ui.js";
import { tabs as registry } from "./blocking/index.js";
// The first tab and what it needs are named here too, so the browser fetches them in the same round as this file (an import
// found one file later costs another round trip): the first visit is then two rounds, not four.
import "./blocking/overview.js";
import "../appicon.js";
import "../vlist.js";

export const TABS = [["overview", "Overview"], ["groups", "Groups"], ["add", "Add"], ["protect", "Site protection"], ["calendar", "Calendar"]];
export const freshMs = 15_000;
export const events = ["status", "draft", "blocks_changed"];
export const NOTICE_MS = 5000;
const TAB_FRESH_MS = 10_000;
const SKELETON_MS = 300;

const STYLE = `<style>
.bk { gap: var(--sp-3); }
.bk .page-h { min-height: calc(36px * var(--s)); }
.bk-notice { color: var(--ok); font-weight: var(--fw-semibold); }
.bk-lock { border: 1px solid var(--line); border-radius: var(--r-lg); }
.bk-panes { flex: 1; min-height: 0; display: flex; flex-direction: column; position: relative; }
.bk-pane { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: var(--sp-3); overflow-y: auto; }
.bk-pane.bk-fill { overflow: hidden; }
.bk-pane[hidden] { display: none; }
.bk-pane > .card, .bk-pane > .stack { flex: none; }
.bk-pane.bk-fill > .list-card { flex: 1; }
.bk-sk { position: absolute; inset: 0; background: var(--surface-2); z-index: 1; overflow: hidden; }
</style>`;

let host = null, page = null, noticeEl = null, lockEl = null;
let current = "overview";
let noticeTimer = 0;
const states = new Map();       // tab id -> {id, pane, mod, status, promise, loading, again, stale, loadedAt, skeleton}

const q = (k) => host.querySelector(`[data-k="${k}"]`);
const report = (where, err) => { try { ld.call("log_client_error", `${where}: ${err && err.stack ? err.stack : err}`.slice(0, 2000)).catch(() => {}); } catch (_) { /* never throws */ } };

function stateOf(id) {
  let st = states.get(id);
  if (!st) {
    st = { id, pane: host.querySelector(`[data-pane="${id}"]`), mod: null, status: "new", promise: null, loading: null, again: false, stale: false, loadedAt: 0, skeleton: null };
    states.set(id, st);
  }
  return st;
}

// ---------------------------------------------------------------- entry points (other pages call these)

/** Open Blocking > Add on this item for editing. */
export const editItem = (id) => router.go("blocking", { tab: "add", edit: id });
/** Open Blocking > Groups on this group for editing. */
export const editGroup = (id) => router.go("blocking", { tab: "groups", edit: id });
/** Open Blocking > Add with a site {site: [name, host]} or an app {app: {name, exe, path}} filled in; something already on
 *  the list opens for editing instead (the Add tab decides). */
export const addPrefilled = (target) => router.go("blocking", { tab: "add", prefill: target });

/** The green "✓ text" for 5 seconds; with Auto-save off it adds " - press Save changes to apply" unless `immediate`. */
export function confirm(text, immediate = false) {
  const full = `✓ ${text}` + (!immediate && !autosaveOn() ? " - press Save changes to apply" : "");
  if (!noticeEl) { toast(full); return; }
  noticeEl.textContent = full;
  noticeEl.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { noticeEl.hidden = true; }, NOTICE_MS);
}

// ---------------------------------------------------------------- the Locked strip

/** The strip's words for a status {status: free | phrase | waiting | closed}; null = no strip. Pure (node tests). */
export function lockView(a) {
  if (!a || !a.status || a.status === "free") return null;
  const closed = a.status === "closed";
  return { closed, text: closed ? "- only during the allowed hours" : "- changes that loosen a block need the challenge" };
}

function renderLock() {
  const v = lockView(ld.status.antibypass);
  lockEl.hidden = !v;
  if (!v) return;
  setText(lockEl.querySelector(".muted"), v.text);
  lockEl.querySelector("button").disabled = v.closed;
}

async function refreshLock() {
  try {
    const a = await ld.call("antibypass_status");
    ld.status.antibypass = { status: a.status, unlocked_until: a.unlocked_until, waiting_until: a.waiting_until, next_open: a.next_open };
    renderLock();
  } catch (_) { /* the next status push says it */ }
}

async function unlockToEdit() {
  try {
    const r = await guarded(ld.call("antibypass_unlock_to_edit"));
    if (r !== CANCELLED) await refreshLock();
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
  }
}

// ---------------------------------------------------------------- tabs

function tabApi(id) {
  return {
    id, el: stateOf(id).pane, page,
    confirm, editItem, editGroup, addPrefilled,
    go: (tab, opts) => router.go("blocking", { tab, ...(opts || {}) }),
    every(fn) { page.every(() => { if (current === id) fn(); }); },
    reload: () => reloadTab(id),
    stale: () => { stateOf(id).stale = true; },
  };
}

const tabFresh = (st) => (st.mod && typeof st.mod.freshMs === "number" ? st.mod.freshMs : TAB_FRESH_MS);

function comingSoon(title) {
  return {
    freshMs: Infinity,
    mount(pane) { pane.append(el(`<div class="card">${empty("k-brand", "sparkle", "Coming soon", `${title} isn't in the new window yet.`)}</div>`)); },
    load() {},
  };
}

/** Build a tab on its first visit and load it. One promise per tab, so a second request waits for the first. */
function ensure(id, opts) {
  const st = stateOf(id);
  if (st.promise) return st.promise;
  st.status = "loading";
  const timer = setTimeout(() => {          // a skeleton only if it takes a while
    if (st.status !== "loading" || st.skeleton) return;
    st.skeleton = el(`<div class="bk-sk">${skRows(8)}</div>`);
    st.pane.parentNode.append(st.skeleton);
  }, SKELETON_MS);
  st.promise = (async () => {
    try {
      const title = (TABS.find((t) => t[0] === id) || [id, id])[1];
      const loader = registry[id];
      if (!st.mod) {
        const loaded = loader ? await loader() : comingSoon(title);
        st.mod = loaded && loaded.mount ? loaded : (loaded && loaded.default) || loaded;
        if (!st.mod || typeof st.mod.mount !== "function") throw new Error(`Tab "${id}" has no mount()`);
        const api = tabApi(id);
        st.mod.mount(st.pane, api);
        if (current === id && st.mod.show) { try { st.mod.show(opts); } catch (err) { report(`${id}.show`, err); } }
      }
      st.stale = false;
      await st.mod.load();
      st.loadedAt = Date.now();
      st.status = "ready";
    } catch (err) {
      st.status = "error";
      report(`blocking/${id} first visit`, err);
      const card = el('<div class="bk-sk"></div>');
      card.append(errorCard(err && err.message, () => { card.remove(); st.promise = null; ensure(id, opts); }));
      st.pane.parentNode.append(card);
    } finally {
      clearTimeout(timer);
      if (st.skeleton) { st.skeleton.remove(); st.skeleton = null; }
      st.promise = null;
    }
    if (st.status === "ready" && st.stale && current === id) reloadTab(id);        // a flag came in while loading
  })();
  return st.promise;
}

function reloadTab(id) {
  const st = stateOf(id);
  if (st.promise) return st.promise;
  if (st.status !== "ready") return Promise.resolve();
  if (st.loading) { st.again = true; return st.loading; }
  st.loading = (async () => {
    try {
      st.stale = false;
      await st.mod.load();
      st.loadedAt = Date.now();
    } catch (err) {
      st.stale = true;                     // not loaded: the next show tries again
      report(`blocking/${id}.load`, err);
    } finally {
      st.loading = null;
    }
    if (st.again) { st.again = false; reloadTab(id); }
  })();
  return st.loading;
}

/** Show a tab (and tell it: opts say what to open in it). */
function select(id, opts) {
  if (!TABS.some((t) => t[0] === id)) id = "overview";
  const prev = current;
  current = id;
  for (const btn of host.querySelectorAll('.tabs [role="tab"]')) btn.setAttribute("aria-selected", String(btn.dataset.tab === id));
  for (const pane of host.querySelectorAll(".bk-pane")) pane.hidden = pane.dataset.pane !== id;
  const st = stateOf(id);
  if (prev !== id) {
    const before = states.get(prev);
    if (before && before.mod && before.mod.hide) { try { before.mod.hide(); } catch (err) { report(`blocking/${prev}.hide`, err); } }
  }
  if (st.status === "new") ensure(id, opts);
  else {
    if (st.mod && st.mod.show) { try { st.mod.show(opts); } catch (err) { report(`blocking/${id}.show`, err); } }
    if (st.status === "ready" && st.stale && Date.now() - st.loadedAt >= tabFresh(st)) reloadTab(id);
  }
  renderLock();
}

// ---------------------------------------------------------------- page contract

export function mount(h, p) {
  host = h;
  page = p;
  host.innerHTML = `${STYLE}<div class="page page-fill bk">
    <div class="page-h"><h1>Blocking</h1><span class="bk-notice small" data-k="notice" role="status" hidden></span></div>
    <div class="tabs" role="tablist">${TABS.map(([id, label]) => `<button type="button" role="tab" data-tab="${id}" aria-selected="${id === "overview"}">${label}</button>`).join("")}</div>
    <div class="banner k-block bk-lock" data-k="lock" hidden>${ic("shield-check")}<span class="grow"><b>Locked</b> <span class="muted"></span></span><button class="btn btn-sm btn-primary" type="button">Unlock to edit</button></div>
    <div class="bk-panes">${TABS.map(([id]) => `<div class="bk-pane" data-pane="${id}" ${id === "overview" ? "" : "hidden"}></div>`).join("")}</div>
  </div>`;
  noticeEl = q("notice");
  lockEl = q("lock");
  host.querySelector(".tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) select(b.dataset.tab);
  });
  host.querySelector(".tabs").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = TABS.findIndex((t) => t[0] === current), n = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length][0];
    select(n);
    host.querySelector(`[data-tab="${n}"]`).focus();
  });
  lockEl.querySelector("button").addEventListener("click", unlockToEdit);
}

export async function load() {
  const st = stateOf(current);
  if (st.promise || st.status === "new") await ensure(current);
  else await reloadTab(current);
  renderLock();
}

/** `opts.tab` opens that tab (an entry point or a show_page push); otherwise Overview. */
export function show(opts) {
  const id = opts && typeof opts.tab === "string" ? opts.tab : "overview";
  for (const [other, st] of states) if (other !== id) st.stale = true;           // they may have missed pushes while the page was away
  select(id, opts);
  if (!ld.status.antibypass) refreshLock();
}

export function hide() {
  const st = states.get(current);
  if (st && st.mod && st.mod.hide) { try { st.mod.hide(); } catch (err) { report(`blocking/${current}.hide`, err); } }
}

export function patch(delta) {
  if (delta.event === "status") renderLock();
  else for (const [id, st] of states) if (id !== current) { st.stale = true; st.loadedAt = 0; }     // a change: reload on the next show, however fresh
  const st = states.get(current);
  if (st && st.status === "ready" && st.mod && st.mod.patch) {
    try { st.mod.patch(delta); } catch (err) { report(`blocking/${current}.patch`, err); }
  }
}
