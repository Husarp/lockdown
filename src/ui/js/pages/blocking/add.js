// Blocking > Add: add a site, an app, a program file or a whole category - and, opened on an item (Overview's Edit, "Block it..."
// from another page), edit it. One form for both:
//   what to block    the picker (js/targetpicker.js; hidden when editing) and the pills of what is picked
//   display name     for one target
//   when blocked     the ways for its kind (js/ways.js): Close app / Minimize / Block internet, Can't load it / Close the tab /
//                    Go back, a program file: Close or Minimize only
//   blockers         the rail of R19 (js/rules/rail.js): By time, Time limit, Opening limit, Permanent, Temporary
// Enter in the search box or the name submits. Several picked targets get the same blockers (one Api call each, one notice).
//
// Python does all the judging (webapi/blocking_edit.py): this page sends what was typed and ticked as FORMS, never rule dicts
// or a block type; every mistake comes back as a sentence to show; and what loosens an item that is already on the list
// (a looser rule, a lighter way, Disable, Remove) comes back as a challenge ticket - js/draft.js opens it, and the old
// blockers stay until it is passed. Nothing here compares old with new.
import { ld } from "../../bridge.js";
import { CANCELLED, parseNaive } from "../../challenge.js";
import { draftCall, FAILED } from "../../draft.js";
import { iconImg } from "../../appicon.js";
import { createPicker, fileName, isHosted, keyOf, labelOf, normalizeHost } from "../../targetpicker.js";
import { createWays, defaultWays, groupOf, GROUPS, waysProblem } from "../../ways.js";
import { esc, ic, setText, skCard, toast } from "../../ui.js";
import { createEditHead, groupsLine, title as editTitle } from "./edit.js";
import "../../suggest.js";           // (needed by targetpicker.js: named here too, so it is fetched in the same round as the files above)

export const freshMs = 60_000;

// The first visit is the budget (BLUEPRINT 2): what the picker's list shows is asked for, and the blocker rail (rules/rail.js with its
// editors and stylesheet - the biggest part of this tab) is fetched, from the moment this file runs, while the tab is built.
// The list comes first on screen; the rail takes its place a moment later.
const started = typeof document !== "undefined";
let earlyList = started ? ld.call("suggest", "", "all", 60) : null;
if (earlyList) earlyList.catch(() => {});          // (never "unhandled": the picker that takes it sees the error)
const railModule = started ? import("../../rules/rail.js") : null;
const ADD_TITLE = "Add a site, app or category";
const HINT = "Tick apps and sites on the left, or type a website and press Enter. You can add many at once - they all get the same blockers.";

// ---------------------------------------------------------------- plain helpers (tested in node)

/** What "Block it..." / the network log hand over -> {kind, target, name, path} or null. Three shapes are in use (the network
 *  log sends the first two together, plus {kind, name, display, path}): {site: [name, host]}, {app: {name, exe, path}} and
 *  {kind: "site" | "app", name: host or exe, display, path}. A site is tidied like a typed address (www., a path ...). */
export function prefillTarget(p) {
  if (!p || typeof p !== "object") return null;
  const site = (host, name) => ({ kind: "site", target: normalizeHost(host) || String(host).trim().toLowerCase(), name: String(name || ""), path: null });
  if (Array.isArray(p.site) && p.site[1]) return site(p.site[1], p.site[0]);
  if (p.app && typeof p.app === "object" && p.app.exe) return { kind: "app", target: fileName(p.app.exe), name: String(p.app.name || ""), path: p.app.path || null };
  if (p.kind === "site" && p.name) return site(p.name, p.display);
  if (p.kind === "app" && p.name) return { kind: "app", target: fileName(p.name), name: String(p.display || ""), path: p.path || null };
  return null;
}

/** One notice for what was done to several targets: "Reddit, Twitch blockers added", else each one's own words. */
export function noticeOf(done) {
  if (!done.length) return "";
  if (done.every((d) => d.result === "added")) {
    return done.length === 1 ? done[0].notice : `${done.map((d) => d.name).join(", ")} blockers added`;
  }
  return done.map((d) => d.notice).join(" · ");
}

/** What a request that went through the challenge stands for (Python's own words don't come back after it). */
export const passedResult = (t, what) => ({
  result: what, name: t.name || t.target,
  notice: what === "merged" ? `${t.name || t.target} was already on the list - blockers merged` : `${t.name || t.target} saved`,
});

// ---------------------------------------------------------------- state

let tab = null, root = null, picker = null, rail = null, ways = null, head = null;
let railReady = null, pendingRules = null;           // the rail comes a moment after the rest: its promise, and rules to show once it is here
let mode = "add", item = null, busy = false, navSeq = 0, clock = null;
let pillSig = "", lastSingle = "", warmed = false;
const touched = {};                                   // group -> the owner changed its ways (so Steam doesn't change them back)
const els = {};
const q = (k) => root.querySelector(`[data-k="ad-${k}"]`);

const STYLE = `<style>
.ad { flex: none; display: grid; gap: var(--sp-4); align-content: start; }
.ad-head { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; padding: var(--sp-3) var(--sp-4); }
.ad-head h2 { flex: 1 1 auto; min-width: 0; }
.ad-bar { width: 3px; height: calc(24px * var(--s)); border-radius: 2px; background: var(--brand); flex: none; }
.ad-editacts { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.ad-info { margin: 0; }
.ad-top { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(calc(300px * var(--s)), 1fr); gap: var(--sp-4); align-items: start; }
.ad-top.edit { grid-template-columns: minmax(0, 1fr); }
.ad-top.edit .ad-pick { display: none; }
.ad-sel .card-b { display: grid; gap: var(--sp-3); }
.ad-pills .pill { max-width: 100%; }
.ad-pills .pill .ellip { max-width: calc(260px * var(--s)); }
.ad-name { display: grid; gap: var(--sp-1); }
.ad-err { margin: 0; }
.ad-actions { position: sticky; bottom: 0; z-index: 5; display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-3) var(--sp-4);
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg); box-shadow: var(--shadow-card); }
@media (max-width: 1100px) { .ad-top { grid-template-columns: minmax(0, 1fr); } }
</style>`;

const pillOf = (t) => {
  const icon = t.kind === "category" ? `<span class="aic aic-sm" style="--c:var(--brand);--fg:var(--on-brand)">${ic("layers")}</span>`
    : `<span class="aic aic-sm">${iconImg(t.kind, t.target)}</span>`;
  return `<span class="pill" title="${esc(t.target)}">${icon}<span class="ellip">${esc(labelOf(t))}</span>` +
    `<button class="icon-btn icon-btn-sm x" type="button" data-remove="${esc(t.key)}" aria-label="Remove ${esc(labelOf(t))}">${ic("x")}</button></span>`;
};

// ---------------------------------------------------------------- what the form holds

const trustedNow = () => (clock ? clock.base + (performance.now() - clock.at) : Date.now());

/** The targets the form is about: what is picked, or the item being edited. */
function targets() {
  if (mode === "edit") return item ? [{ key: keyOf(item.kind, item.target), kind: item.kind, target: item.target, name: item.name, file: item.hosted, path: item.app_path }] : [];
  return picker.selection();
}

function currentName() {
  const sel = targets();
  if (sel.length > 1) return "These";
  return els.name.value.trim() || (sel[0] && (sel[0].name || sel[0].target)) || "It";
}

function groupsOf(sel) {
  const present = new Set(sel.map(groupOf));
  return GROUPS.filter((g) => present.has(g));
}

/** The selection or the item changed: redraw what depends on it. */
function paint() {
  const sel = targets();
  if (mode === "add") {
    const sig = sel.map((t) => `${t.key}|${labelOf(t)}`).join("\n");
    if (sig !== pillSig) { pillSig = sig; els.pills.innerHTML = sel.map(pillOf).join(""); }
    setText(els.seltitle, sel.length ? `Adding ${sel.length}` : "Adding");
    els.hint.hidden = sel.length > 0;
    // one target: its name is editable (and starts as the name the list gave it); several: each keeps its own
    const single = sel.length === 1 ? sel[0].key : "";
    if (single !== lastSingle) { lastSingle = single; els.name.value = single ? sel[0].name || "" : ""; }
    els.namewrap.hidden = sel.length !== 1;
  }
  const groups = groupsOf(sel);
  ways.show(groups);
  const steam = sel.some((t) => t.kind === "app" && !t.file && t.steam);
  if (mode === "add" && !touched.app) ways.set("app", defaultWays("app", steam));
  if (rail) rail.refresh();
  clearError();
}

function showError(text) {
  setText(els.error, text);
  els.error.hidden = !text;
}
const clearError = () => showError("");

/** The first problem with the ways of the groups in use, in Python's words ("" when fine). */
function waysProblems(sel) {
  for (const g of groupsOf(sel)) {
    const p = waysProblem(g, ways.values(g));
    if (p) return p;
  }
  return "";
}

// ---------------------------------------------------------------- add / edit modes

function setBusy(on) {
  busy = on;
  els.submit.disabled = on;
}

/** A fresh Add form. */
function resetAdd(read = true) {
  navSeq++;
  mode = "add";
  item = null;
  clock = null;
  picker.reset(read);
  pillSig = lastSingle = "";
  els.name.value = "";
  for (const g of GROUPS) { touched[g] = false; ways.set(g, defaultWays(g)); }
  pendingRules = null;
  if (rail) rail.load([]);
  q("title").textContent = ADD_TITLE;
  els.submit.textContent = "+ Add";
  els.top.classList.remove("edit");
  head.hide();
  els.info.hidden = true;
  paint();
}

/** The form for an item that is on the list. `note`: a line to show under the title (it was found by "Add"). */
function fillEdit(data, note = "") {
  mode = "edit";
  item = data;
  clock = { base: parseNaive(data.now), at: performance.now() };
  q("title").textContent = editTitle(data);
  els.submit.textContent = "Save";
  els.top.classList.add("edit");
  head.show(data);
  const line = [note, groupsLine(data)].filter(Boolean).join(" ");
  setText(els.info, line);
  els.info.hidden = !line;
  els.name.value = data.name;
  els.namewrap.hidden = false;
  const t = targets()[0];
  const g = groupOf(t);
  for (const x of GROUPS) touched[x] = true;
  ways.set(g, data.ways);
  els.pills.innerHTML = `<span class="pill"><span class="aic aic-sm">${data.kind === "category" ? ic("layers") : iconImg(data.kind, data.target)}</span><span class="ellip">${esc(data.kind === "category" ? `category · ${data.name}` : data.targets.join(", "))}</span></span>`;
  pillSig = "";
  setText(els.seltitle, "What is blocked");
  els.hint.hidden = true;
  if (rail) rail.load(data.rules); else pendingRules = data.rules;
  paint();
  clearError();
}

async function openEdit(id, note = "") {
  const my = ++navSeq;
  let data;
  try {
    data = await ld.call("item_get", id);
  } catch (err) {
    if (my === navSeq) { toast((err && err.message) || "That item is already gone."); tab.go("overview"); }
    return;
  }
  if (my === navSeq) fillEdit(data, note);
}

async function applyPrefill(p) {
  const my = ++navSeq;
  const t = prefillTarget(p);
  if (!t) return;
  let found = null;
  try { found = await ld.call("item_get", null, t.target); } catch (_) { /* then it is added as new */ }
  if (my !== navSeq) return;
  if (found) { fillEdit(found, `${found.name} is already on the list - its blockers are loaded, change them and Save.`); return; }
  resetAdd();
  picker.add({ key: keyOf(t.kind, t.target), kind: t.kind, target: t.target, name: t.name, path: t.path, picked: t.kind === "app", steam: false, steamKnown: t.kind === "site",
    file: t.kind === "app" && isHosted(t.target), mine: false });
}

// ---------------------------------------------------------------- submitting

const specOf = (t, forms, single) => ({
  mode: "new", kind: t.kind, target: t.target, name: single ? els.name.value.trim() : t.name || "", picked: !!t.picked, app_path: t.path || null,
  ways: ways.values(groupOf(t)), rules: forms,
});

/** Python's answer had mistakes: rule ones go under their fields in the rail, the rest in the line over the buttons. */
function showInvalid(errors) {
  const inRail = errors.filter((e) => e.rule_type || e.field === "rules");
  const other = errors.filter((e) => !(e.rule_type || e.field === "rules"));
  if (inRail.length) rail.showErrors(inRail);
  showError(other.length ? other[0].message : "");
}

/** After a change that went through: the notice, a fresh form, and Overview with the change in it. */
function finish(text) {
  tab.confirm(text);
  resetAdd();
  tab.go("overview");
}

async function submitAdd() {
  await railReady;
  const sel = picker.selection();
  if (!sel.length) return showError("Pick a site, an app or a category first.");
  const problem = waysProblems(sel);
  if (problem) return showError(problem);
  const forms = rail.forms();
  const done = [];
  let opened = null, stop = null;
  setBusy(true);
  try {
    for (const t of sel) {
      const r = await draftCall("draft_submit_item", specOf(t, forms, sel.length === 1));
      if (r === FAILED) { stop = "failed"; break; }
      if (r === CANCELLED) { stop = "cancelled"; break; }
      const res = r && r.result ? r.result : passedResult(t, "merged");          // (no result after a challenge: only a merge can ask)
      if (res.result === "invalid") { showInvalid(res.errors || []); stop = "invalid"; break; }
      if (res.result === "opened_existing") {
        if (sel.length === 1) opened = res; else done.push({ ...res, notice: `${res.name} is already on the list` });
        continue;
      }
      done.push(res);
      picker.remove(t.key);
    }
  } finally {
    setBusy(false);
  }
  if (opened) return openEdit(opened.id, opened.notice);
  if (stop === "cancelled") toast("Not saved - the challenge wasn't passed, so nothing changed.");
  if (stop) { if (done.length) tab.confirm(noticeOf(done)); return; }
  finish(noticeOf(done) || "Nothing to add");
}

async function submitEdit() {
  await railReady;
  const problem = waysProblems(targets());
  if (problem) return showError(problem);
  const forms = rail.forms();
  if (!forms.length && !item.groups.length) return showError("Tick at least one blocker (or remove it in Overview).");
  setBusy(true);
  let r;
  try {
    r = await draftCall("draft_submit_item", { mode: "edit", id: item.id, name: els.name.value.trim(), ways: ways.values(groupOf(targets()[0])), rules: forms });
  } finally {
    setBusy(false);
  }
  if (r === CANCELLED) return toast("Not saved - the challenge wasn't passed, so nothing changed.");
  if (r === FAILED) {                                         // (it may be gone: expired, or removed in another window)
    try { await ld.call("item_get", item.id); } catch (_) { resetAdd(); tab.go("overview"); }
    return;
  }
  const res = r && r.result ? r.result : passedResult({ name: els.name.value.trim() || item.name }, "saved");
  if (res.result === "invalid") return showInvalid(res.errors || []);
  finish(res.notice);
}

const submit = () => {
  if (busy) return;
  clearError();
  return mode === "edit" ? submitEdit() : submitAdd();
};

// ---------------------------------------------------------------- the edit bar

/** Run a draft change for the item being edited; resolves true when it went through. */
async function change(call, text) {
  const r = await call;
  if (r === FAILED || r === CANCELLED) return false;
  finish(text);
  return true;
}

// ---------------------------------------------------------------- the tab contract

export function mount(host, t) {
  tab = t;
  root = host;
  host.innerHTML = `${STYLE}<div class="ad">
    <div class="card ad-head"><span class="ad-bar"></span><h2 data-k="ad-title">${ADD_TITLE}</h2></div>
    <p class="small muted ad-info" data-k="ad-info" hidden></p>
    <div class="ad-top" data-k="ad-top">
      <div data-k="ad-pickhost"></div>
      <div class="card ad-sel"><header class="card-h"><h3 data-k="ad-seltitle">Adding</h3></header><div class="card-b">
        <div class="pills ad-pills" data-k="ad-pills"></div>
        <p class="small muted" data-k="ad-hint">${esc(HINT)}</p>
        <label class="ad-name" data-k="ad-namewrap" hidden><span class="cap">Display name</span><input class="input" data-k="ad-name" placeholder="Display name (optional)" maxlength="80" autocomplete="off" spellcheck="false"></label>
        <div data-k="ad-wayshost"></div>
      </div></div>
    </div>
    <div data-k="ad-railhost">${skCard(4)}</div>
    <p class="rule-err ad-err" data-k="ad-error" role="alert" hidden></p>
    <div class="ad-actions"><span class="grow"></span>
      <button class="btn" type="button" data-k="ad-cancel">Cancel</button><button class="btn btn-primary" type="button" data-k="ad-submit">+ Add</button></div>
  </div>`;
  for (const k of ["title", "info", "top", "seltitle", "pills", "hint", "namewrap", "name", "error", "cancel", "submit"]) els[k] = q(k);

  picker = createPicker({ onChange: paint, onEnter: submit, notify: (text) => showError(text) });
  picker.useEarly(earlyList);                          // (the list's first answer is already on its way)
  earlyList = null;
  ways = createWays({ onChange: ({ group }) => { touched[group] = true; clearError(); } });
  head = createEditHead({
    onAlerts: async (v) => { const r = await draftCall("draft_set_notify", item.id, v); if (r !== FAILED && r !== CANCELLED) head.setNotify(v); },
    onDisable: () => { const off = !head.disabled; change(draftCall("draft_set_disabled", item.id, off), `${item.name} ${off ? "disabled" : "enabled"}`); },
    onRemove: () => change(draftCall("draft_remove_item", item.id), `${item.name} removed`),
  });
  q("pickhost").replaceWith(picker.el);
  q("wayshost").replaceWith(ways.el);
  railReady = railModule.then(({ createRail }) => {
    rail = createRail({ name: currentName, now: trustedNow, onChange: clearError });
    q("railhost").replaceWith(rail.el);
    rail.load(pendingRules || []);
    pendingRules = null;
    paint();
  });
  root.querySelector(".ad-head").append(head.el);
  els.name.addEventListener("input", () => { if (rail) rail.refresh(); clearError(); });
  els.name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
  els.pills.addEventListener("click", (e) => { const b = e.target.closest("[data-remove]"); if (b) picker.remove(b.dataset.remove); });
  els.submit.addEventListener("click", submit);
  els.cancel.addEventListener("click", () => { resetAdd(); tab.go("overview"); });
  resetAdd(false);
}

export async function load() {
  await Promise.all([picker.refresh(), railReady]);
  if (!warmed) { warmed = true; setTimeout(() => picker.warm(), 400); }       // (the App browser and the categories, ready before a button is pressed)
}

/** `opts`: {edit: itemId} opens that item; {prefill} fills the form (or opens the item when it is on the list already);
 *  nothing: a fresh Add form (a form left open in Edit is not kept). */
export function show(opts) {
  if (opts && typeof opts.edit === "number") openEdit(opts.edit);
  else if (opts && opts.prefill) applyPrefill(opts.prefill);
  else if (mode === "edit") resetAdd();
  if (mode === "add") picker.focus();
}

export function hide() {
  if (picker) picker.setVisible(false);
}

export function patch() {}
