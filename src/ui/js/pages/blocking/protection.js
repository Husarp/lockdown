// Blocking > Site protection (BLUEPRINT 3.3): the always-on lists (switches, update settings, live download progress),
// Connect a list, your own blocking lists, Safe search, Blocked words, Check a site, Allowed anyway.
//
// Python decides everything that matters (webapi/protection.py): which switch loosens a block, the status of each list, what
// a site is on. This file paints it and sends the clicks; a change that loosens comes back as a challenge ticket, the
// challenge opens by itself (js/challenge.js) and the control snaps back to what is saved when it is cancelled - every
// change ends with a re-read, so the page always shows what is really saved.
//
// Nothing is rebuilt: the lists are keyed rows that keep their nodes (only text that changed is written), the "updated N min
// ago" text ticks locally from the time Python gave, and a download-progress push (`protection_progress`, from the agent's
// watcher) patches the one row that is downloading. The two long dialogs (your own list, the word lists) are in
// js/manuallist.js and js/wordlist.js and load when first opened.
import { iconImg, wireIconReady } from "../../appicon.js";
import { ld } from "../../bridge.js";
import { CANCELLED, guarded, parseNaive } from "../../challenge.js";
import { openDialog } from "../../dialog.js";
import { confirmButton, el, esc, field, ic, kic, seg, setText, sw, toast } from "../../ui.js";

export const freshMs = 5000;

// ---------------------------------------------------------------- plain helpers (tested in node)

/** "1,234" - the number of sites, as the old window wrote it. */
export const countFormat = (n) => Number(n).toLocaleString("en-US");

/** "just now" / "12 min ago" / "3 h ago" (the old window's words) for a time Python gave, at `nowMs` (both naive local). */
export function agoText(updatedIso, nowMs) {
  const minutes = Math.floor((nowMs - parseNaive(updatedIso)) / 60000);
  return minutes < 2 ? "just now" : minutes < 120 ? `${minutes} min ago` : `${Math.floor(minutes / 60)} h ago`;
}

/** What a list row says: {count, text} for the state Python worked out (the old window's 5-condition table). */
export function listStatus(row, nowMs) {
  switch (row.state) {
    case "downloading": return { count: "…", text: `downloading ${row.text}` };
    case "updated": return { count: countFormat(row.count), text: `updated ${agoText(row.updated, nowMs)}` };
    case "update_failed": return { count: countFormat(row.count), text: "update failed · retrying in 1 h" };
    case "failed": return { count: "–", text: "download failed · retrying in 1 h" };
    case "waiting": return { count: "–", text: "waiting for the service" };
    default: return { count: "", text: "off" };
  }
}

/** The line under "Check a site": {text, tone: "ok" | "bad", act: {kind: "unallow" | "allow", host, label} | null}. */
export function checkText(r) {
  if (r.allowed_by) {
    const via = r.allowed_by === r.host ? "" : ` (through ${r.allowed_by})`;
    return { text: `${r.host} is allowed anyway by you${via}.`, tone: "ok", act: { kind: "unallow", host: r.allowed_by, label: "Block again" } };
  }
  if (r.lists.length) {
    const names = r.lists.map((l) => (l.mine ? `${l.name} (your list)` : l.on ? l.name : `${l.name} (list is off)`));
    return { text: `${r.host} is on the ${names.join(", ")} list${r.lists.length > 1 ? "s" : ""}.`, tone: "bad", act: { kind: "allow", host: r.host, label: "Allow anyway" } };
  }
  return { text: `${r.host} isn't on any list.`, tone: "ok", act: null };
}

/** "3 sites" / "1 site" / "0 sites". */
export const siteCount = (n) => `${countFormat(n)} site${n === 1 ? "" : "s"}`;

/** The info under a word list: "110 words (3 off)" / "none yet" / "2 entries". */
export function wordsInfo(kind, row) {
  if (kind === "ready") return `${row.total} words${row.off ? ` (${row.off} off)` : ""}`;
  const n = row.count;
  if (kind === "words") return n ? `${n} word${n === 1 ? "" : "s"}` : "none yet";
  return n ? `${n} entr${n === 1 ? "y" : "ies"}` : "none";
}

/** The interval label for the stored hours (the old window fell back to "daily"). */
export function intervalValue(hours, intervals) {
  return intervals.some(([h]) => h === hours) ? hours : 24;
}

/** Make the children of `parent` match `items` (keyed, in order): nodes that stay keep their place, only moved when out of
 *  order; new ones are made with create(key, item); update(node, item) writes what changed. */
export function reconcile(parent, items, keyOf, create, update) {
  const live = new Map();
  for (const node of parent.children) live.set(node._key, node);
  let prev = null;
  for (const item of items) {
    const key = keyOf(item);
    let node = live.get(key);
    if (node) live.delete(key);
    else { node = create(key, item); node._key = key; }
    update(node, item);
    const want = prev ? prev.nextSibling : parent.firstChild;
    if (node !== want) parent.insertBefore(node, want);
    prev = node;
  }
  for (const node of live.values()) node.remove();
}

// ---------------------------------------------------------------- text

const HELP = {
  connect: ["Connect a blocking list", "Subscribe to a block list published online (a hosts file, a plain list of domains or an adblock-style list - ||site.com^ blocks its subdomains too). Lockdown downloads it and keeps it updated daily, like the community lists. Preview it first to see what's in it."],
  mine: ["Your blocking lists", "Your own lists of sites to block. Type a site into a list; turn the list on to block everything in it (and its subdomains). Adding sites is instant; removing them or turning a list off needs the Anti-Bypass challenge."],
  safe: ["Force SafeSearch", "Google, Bing and DuckDuckGo always search with SafeSearch on (adult pictures and videos filtered out) - in every browser, also in private windows."],
  youtube: ["YouTube Restricted Mode", "Hides mature videos on YouTube. Note: Restricted Mode also hides all comments, so turn this off if you want to read comments - SafeSearch above stays on either way."],
  words: ["Blocked words", "The address and title of the tab in front are checked twice a second. Whole words only (\"analysis\" is fine); a word ending in * also matches longer ones (porn* -> pornhub); small typos in searches don't matter here - the words must be spelled right."],
  action: ["When a page has one", "A tab with nothing to go back to is closed."],
  allowed: ["Allowed anyway", "Sites a list blocks by mistake (their subdomains too). They open again within a minute."],
};
const helpBtn = (id) => `<button class="icon-btn icon-btn-sm" type="button" data-help="${id}" aria-label="Help: ${esc(HELP[id][0])}" title="Help">${ic("info")}</button>`;

const STYLE = `<style>
.pl { gap: var(--sp-3); }
.pl-intro { max-width: 90ch; }
.pl-cols { display: grid; grid-template-columns: minmax(0, 4fr) minmax(0, 3fr); gap: var(--sp-4); align-items: start; }
@media (max-width: 900px) { .pl-cols { grid-template-columns: minmax(0, 1fr); } }
.pl-h { flex-wrap: wrap; row-gap: var(--sp-2); }
.pl-h h3 { flex: 1 1 auto; }
.pl-tools { display: inline-flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; margin-left: auto; }
.pl-row { display: grid; grid-template-columns: auto minmax(0, 1fr) minmax(0, auto) auto; align-items: center; gap: var(--sp-1) var(--sp-3);
  padding: var(--sp-3) var(--sp-4); border-top: 1px solid var(--line); }
.pl-row:first-child { border-top: 0; }
.pl-main { display: grid; min-width: 0; line-height: 1.3; }
.pl-main b { font-weight: var(--fw-semibold); }
.pl-main span { color: var(--text-2); font-size: var(--fs-sm); text-wrap: pretty; }
.pl-stat { display: grid; justify-items: end; text-align: right; line-height: 1.25; min-width: calc(110px * var(--s)); }
.pl-stat b { font: var(--fw-semibold) var(--fs-lg)/1.2 var(--font-display); font-variant-numeric: tabular-nums; }
.pl-stat span { color: var(--text-2); font-size: var(--fs-cap); }
.pl-meter { grid-column: 2 / -1; }
.pl-meter i { transition: width .3s linear; }
.pl-sub { padding: var(--sp-3) var(--sp-4) var(--sp-4); border-top: 1px solid var(--line); display: grid; gap: var(--sp-2); }
.pl-sub > .hstack { gap: var(--sp-1); }
.pl-line { display: flex; gap: var(--sp-2); align-items: center; flex-wrap: wrap; }
.pl-line .input { flex: 1 1 calc(150px * var(--s)); }
.pl-line .input.narrow { flex: 0 1 calc(150px * var(--s)); }
.pl-msg { font-size: var(--fs-sm); margin: 0; overflow-wrap: anywhere; }
.pl-msg[hidden] { display: none; }
.pl-bad { color: var(--k-block); }
.pl-good { color: var(--ok); }
.pl-empty { padding: var(--sp-3) var(--sp-4); }
.pl-mine { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto auto; align-items: center; gap: var(--sp-3); padding: var(--sp-2) var(--sp-4); min-height: calc(44px * var(--s)); border-top: 1px solid var(--line); }
.pl-mine:first-child { border-top: 0; }
.pl-mine .count { color: var(--text-2); font-size: var(--fs-sm); }
.pl-wrow { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; align-items: center; gap: var(--sp-3); padding: var(--sp-1) 0; min-height: calc(40px * var(--s)); }
.pl-wrow .info { color: var(--text-2); font-size: var(--fs-sm); }
.pl-card-b { padding: 0 0 var(--sp-3); }
.pl-pad { padding: var(--sp-3) var(--sp-4); display: grid; gap: var(--sp-2); }
.pl-chips { display: flex; flex-wrap: wrap; gap: var(--sp-2); }
.pl-chip { padding-left: var(--sp-2); }
.pl-chip .aic { background: transparent; }
.pl-chip .ellip { max-width: calc(220px * var(--s)); }
.pl-chip .x { color: var(--text-2); }
.pl-cap { padding: var(--sp-2) var(--sp-4) 0; }
</style>`;

// ---------------------------------------------------------------- state

let tab = null, root = null;
let data = null, words = null, clock = { base: 0, at: 0 }, loadedRev = "";
let live = null;                              // {key, text, pct}: the list that is downloading right now (a push)
let shown = false, pending = null, early = null, previewed = null, checkSeq = 0, lastCheck = "";
const els = {};
const trustedNow = () => clock.base + (performance.now() - clock.at);
const q = (k) => root.querySelector(`[data-k="${k}"]`);

// ---------------------------------------------------------------- building the cards (once)

const lead = (id, label, icon, extra = "") => `<header class="card-h pl-h">${kic("protect", icon)}<h3>${label}</h3>${extra}${id ? helpBtn(id) : ""}</header>`;

function listsCard() {
  return `<section class="card" data-card="lists">
    <header class="card-h pl-h">${kic("protect", "shield")}<h3>Always-on lists</h3>
      <span class="pl-tools"><label class="small" for="pl-auto">Update automatically</label>${sw(true, 'id="pl-auto" data-k="auto" data-act="auto"')}
        <select class="select select-sm" data-k="every" data-act="every" aria-label="Update every"></select>
        <button class="btn btn-sm" type="button" data-act="update-now">${ic("refresh")}Update now</button></span></header>
    <div data-k="rows"></div>
    <div class="pl-sub">
      <div class="hstack"><b>Connect a blocking list</b>${helpBtn("connect")}</div>
      <div class="pl-line"><input class="input narrow" data-k="c-name" placeholder="Name (e.g. Crypto)" maxlength="60" aria-label="List name" autocomplete="off">
        <input class="input" data-k="c-url" data-enter="preview" placeholder="https://... list address" aria-label="List address" autocomplete="off" spellcheck="false">
        <button class="btn" type="button" data-act="preview">Preview</button><button class="btn btn-primary" type="button" data-act="connect" data-k="c-go" hidden>Connect</button></div>
      <p class="pl-msg" data-k="c-msg" role="status" hidden></p>
    </div></section>`;
}

function mineCard() {
  return `<section class="card" data-card="mine">${lead("mine", "Your blocking lists", "link")}
    <div data-k="mine-rows" style="margin-top:var(--sp-2)"></div>
    <p class="muted small pl-empty" data-k="mine-empty" hidden>No lists yet - create one below, then open it to add sites.</p>
    <div class="pl-sub"><div class="pl-line"><input class="input" data-k="m-name" data-enter="create" placeholder="New list name (e.g. Distractions)" maxlength="60" aria-label="New list name" autocomplete="off">
      <button class="btn" type="button" data-act="create">Create list</button></div>
      <p class="pl-msg pl-bad" data-k="m-err" role="alert" hidden></p></div></section>`;
}

function safeCard() {
  return `<section class="card" data-card="safe"><header class="card-h">${kic("protect", "search")}<h3>Safe search</h3></header>
    <div class="card-b" style="padding:0">
      ${field("Force SafeSearch", "Google, Bing and DuckDuckGo, in every browser.", `${helpBtn("safe")}${sw(true, 'data-k="safesearch" data-act="word-flag" aria-label="Force SafeSearch"')}`)}
      ${field("YouTube Restricted Mode", "Hides mature videos (and all comments).", `${helpBtn("youtube")}${sw(true, 'data-k="youtube" data-act="word-flag" aria-label="YouTube Restricted Mode"')}`)}
    </div></section>`;
}

function wordRow(id, name) {
  return `<div class="pl-wrow" data-list="${id}"><label class="switch"><input type="checkbox" data-act="word-list" aria-label="${esc(name)}"><span></span></label>
    <b class="ellip" data-k="n"></b><span class="info" data-k="i"></span><button class="btn btn-sm" type="button" data-act="word-open">Open</button></div>`;
}

function wordsCard() {
  return `<section class="card" data-card="words"><header class="card-h">${kic("protect", "type")}<h3>Blocked words</h3>${helpBtn("words")}</header>
    <div class="card-b" style="padding:0">
      ${field("Check the browser tab for blocked words", "", sw(true, 'data-k="enabled" data-act="word-flag" aria-label="Check the browser tab for blocked words"'))}
      ${field("When a page has one", "", `${seg("action", [["close", "Close the tab"], ["back", "Go back"]], "close")}${helpBtn("action")}`)}
      <div class="cap pl-cap">Word lists</div>
      <div class="pl-card-b" style="padding:var(--sp-1) var(--sp-4) var(--sp-3)" data-k="word-rows">
        ${wordRow("adult_en", "Adult words - English")}${wordRow("adult_pl", "Adult words - Polish")}${wordRow("words", "Your words")}${wordRow("exceptions", "Exceptions")}
      </div></div></section>`;
}

function checkCard() {
  return `<section class="card" data-card="check"><header class="card-h">${kic("protect", "eye")}<h3>Check a site</h3></header>
    <div class="pl-pad"><div class="pl-line"><input class="input" data-k="k-host" data-enter="check" placeholder="e.g. suspicious-shop.com" aria-label="Site to check" autocomplete="off" spellcheck="false">
      <button class="btn" type="button" data-act="check">Check</button><button class="btn" type="button" data-act="check-act" data-k="k-act" hidden></button></div>
      <p class="pl-msg" data-k="k-msg" role="status" hidden></p></div></section>`;
}

function allowedCard() {
  return `<section class="card" data-card="allowed">${lead("allowed", "Allowed anyway", "unlock")}
    <div class="pl-pad"><div class="pl-chips" data-k="chips"></div><p class="muted small" data-k="none">None.</p>
      <div class="pl-line"><input class="input" data-k="a-host" data-enter="allow" placeholder="site to allow" aria-label="Site to allow" autocomplete="off" spellcheck="false">
        <button class="btn btn-primary" type="button" data-act="allow">Allow</button></div>
      <p class="pl-msg pl-bad" data-k="a-err" role="alert" hidden></p></div></section>`;
}

// ---------------------------------------------------------------- showing the data (text is written only when it changed)

function createListRow() {
  const node = el(`<div class="pl-row"><label class="switch"><input type="checkbox" data-act="list-on"><span></span></label>
    <div class="pl-main"><b class="ellip"></b><span></span></div><div class="pl-stat"><b class="num"></b><span></span></div><span class="pl-act"></span>
    <div class="meter pl-meter" hidden><i style="width:0"></i></div></div>`);
  node._r = { box: node.querySelector("input"), name: node.querySelector(".pl-main b"), desc: node.querySelector(".pl-main span"),
    count: node.querySelector(".pl-stat b"), text: node.querySelector(".pl-stat span"), act: node.querySelector(".pl-act"),
    meter: node.querySelector(".pl-meter"), bar: node.querySelector(".pl-meter i"), custom: false };
  return node;
}

function paintStatus(node, row) {
  const r = node._r;
  const shownRow = live && live.key === row.key ? { ...row, state: "downloading", text: live.text } : row;
  const s = listStatus(shownRow, trustedNow());
  setText(r.count, s.count);
  setText(r.text, s.text);
  const pct = shownRow.state === "downloading" ? (live && live.key === row.key ? live.pct : row.pct) : null;
  if (r.meter.hidden !== (pct == null)) r.meter.hidden = pct == null;
  if (pct != null && r.bar.style.width !== `${pct}%`) r.bar.style.width = `${pct}%`;
}

/** Set an attribute only when it differs (an unchanged write still counts as a change to the browser). */
function attr(node, name, value) {
  if (node.getAttribute(name) !== value) node.setAttribute(name, value);
}

function updateListRow(node, row) {
  const r = node._r;
  attr(node, "data-key", row.key);
  if (r.box.checked !== row.enabled) r.box.checked = row.enabled;
  attr(r.box, "aria-label", row.name);
  setText(r.name, row.name);
  setText(r.desc, row.desc);
  node._row = row;
  if (row.custom && !r.custom) {                      // your own URL list: a two-click Remove
    r.custom = true;
    r.act.append(confirmButton({ label: "Remove", confirmLabel: "Click again", cls: "btn btn-sm", onConfirm: () => run(() => ld.call("protection_remove_custom", node._key)) }));
  }
  paintStatus(node, row);
}

function createMineRow() {
  const node = el(`<div class="pl-mine"><label class="switch"><input type="checkbox" data-act="mine-on"><span></span></label><b class="ellip"></b>
    <span class="count"></span><button class="btn btn-sm" type="button" data-act="mine-open">Open</button><span class="pl-act"></span></div>`);
  node._r = { box: node.querySelector("input"), name: node.querySelector("b"), count: node.querySelector(".count") };
  node.querySelector(".pl-act").append(confirmButton({ label: "Remove list", confirmLabel: "Click again", cls: "btn btn-sm", onConfirm: () => run(() => ld.call("manual_remove", node._key)) }));
  return node;
}

function updateMineRow(node, m) {
  const r = node._r;
  if (r.box.checked !== m.enabled) r.box.checked = m.enabled;
  attr(r.box, "aria-label", m.name);
  setText(r.name, m.name);
  setText(r.count, siteCount(m.count));
}

function createChip(host) {
  const node = el(`<span class="pill pl-chip"><span class="aic aic-sm">${iconImg("site", host)}</span><span class="ellip"></span>
    <button class="icon-btn icon-btn-sm x" type="button" data-act="unallow">${ic("x")}</button></span>`);
  node.querySelector(".ellip").textContent = host;
  node.querySelector("button").setAttribute("aria-label", `Stop allowing ${host}`);
  return node;
}

function paintWords() {
  const w = words;
  const flag = (k, on) => { const box = q(k); if (box.checked !== on) box.checked = on; };
  flag("enabled", w.enabled); flag("safesearch", w.safesearch); flag("youtube", w.youtube);
  for (const b of root.querySelectorAll('[data-seg="action"] button')) attr(b, "aria-pressed", String(b.dataset.v === w.action));
  const rows = Object.fromEntries(w.lists.map((l) => [l.key, l]));
  for (const id of ["adult_en", "adult_pl", "words", "exceptions"]) {
    const node = root.querySelector(`[data-list="${id}"]`), box = node.querySelector("input");
    const on = rows[id] ? rows[id].on : w[id].on;
    if (box.checked !== on) box.checked = on;
    setText(node.querySelector('[data-k="n"]'), rows[id] ? rows[id].name : id === "words" ? "Your words" : "Exceptions");
    setText(node.querySelector('[data-k="i"]'), wordsInfo(rows[id] ? "ready" : id, rows[id] || w[id]));
  }
}

function render() {
  const d = data;
  const auto = q("auto");
  if (auto.checked !== d.auto) auto.checked = d.auto;
  const every = q("every");
  if (!every.options.length) every.innerHTML = d.intervals.map(([h, label]) => `<option value="${h}">${esc(label)}</option>`).join("");
  const hours = String(intervalValue(d.every_hours, d.intervals));
  if (every.value !== hours) every.value = hours;
  if (every.disabled !== !d.auto) every.disabled = !d.auto;
  reconcile(els.rows, d.lists, (r) => r.key, createListRow, updateListRow);
  reconcile(els.mineRows, d.manual, (m) => m.key, createMineRow, updateMineRow);
  if (els.mineEmpty.hidden !== d.manual.length > 0) els.mineEmpty.hidden = d.manual.length > 0;
  const hosts = d.allowed;
  reconcile(els.chips, hosts, (h) => h, createChip, () => {});
  if (els.none.hidden !== hosts.length > 0) els.none.hidden = hosts.length > 0;
  paintWords();
}

// ---------------------------------------------------------------- what a click does

/** Run a call that may loosen a block: the challenge (if Python wants one) opens by itself. Whatever comes of it, read
 *  again so the controls show what is saved (a cancelled switch snaps back, a refused one too). */
async function run(call, { err, done } = {}) {
  if (err) { err.textContent = ""; err.hidden = true; }
  let result;
  try {
    result = await guarded(call());
    if (result !== CANCELLED && done) done(result);
  } catch (e) {
    const message = (e && e.message) || "That didn't work.";
    if (err) { err.textContent = message; err.hidden = false; } else toast(message);
  }
  tab.reload();
  return result;
}

function say(node, text, tone) {
  node.hidden = !text;
  node.className = `pl-msg${tone ? ` pl-${tone}` : ""}`;
  setText(node, text || "");
}

function openHelp(id) {
  const [title, text] = HELP[id];
  openDialog({ key: "pl-help", title, body: `<p>${esc(text)}</p>`, buttons: [{ label: "OK", kind: "primary" }] });
}

async function preview() {
  const url = els.cUrl.value.trim();
  els.cGo.hidden = true;
  previewed = null;
  pending = null;
  early = null;
  try {
    const { token } = await ld.call("protection_preview", url);
    pending = { token, url };
    say(els.cMsg, "Downloading the list to have a look...", "");
    if (early && early.token === token) onPreview(early);        // (a quick answer can beat the reply of the call)
  } catch (e) {
    say(els.cMsg, (e && e.message) || "That didn't work.", "bad");
  }
}

function onPreview(p) {
  if (!p) return;
  if (!pending || p.token !== pending.token) { early = p; return; }
  early = null;
  const url = pending.url;
  pending = null;
  if (p.ok) {
    previewed = { url, token: p.token, count: p.count };
    say(els.cMsg, p.text, "");
    els.cGo.hidden = false;
  } else say(els.cMsg, p.error, "bad");
}

async function connect() {
  if (!previewed) return;
  const { url, token } = previewed;
  try {
    const r = await ld.call("protection_connect", els.cName.value, url, token);
    previewed = null;
    els.cGo.hidden = true;
    els.cName.value = els.cUrl.value = "";
    say(els.cMsg, r.text, "good");
  } catch (e) {
    say(els.cMsg, (e && e.message) || "That didn't work.", "bad");
  }
  tab.reload();
}

async function check(text = els.kHost.value) {
  const seq = ++checkSeq;
  lastCheck = text;
  els.kAct.hidden = true;
  say(els.kMsg, "Checking...", "");
  try {
    const r = await ld.call("protection_check", text);
    if (seq !== checkSeq) return;
    const v = checkText(r);
    say(els.kMsg, v.text, v.tone === "ok" ? "good" : "bad");
    els.kAct._act = v.act;
    els.kAct.hidden = !v.act;
    if (v.act) els.kAct.textContent = v.act.label;
  } catch (e) {
    if (seq === checkSeq) say(els.kMsg, (e && e.message) || "That didn't work.", "bad");
  }
}

function checkAct() {
  const act = els.kAct._act;
  if (!act) return;
  const call = act.kind === "allow" ? () => ld.call("protection_allow", act.host) : () => ld.call("protection_unallow", act.host);
  run(call, { done: () => check(lastCheck) });
}

function allow() {
  const text = els.aHost.value;
  if (!text.trim()) return;
  run(() => ld.call("protection_allow", text), { err: els.aErr, done: () => { els.aHost.value = ""; } });
}

function createList() {
  const name = els.mName.value;
  if (!name.trim()) return;
  run(() => ld.call("manual_create", name), { err: els.mErr, done: () => { els.mName.value = ""; } });
}

async function openMine(key) {
  const { openManualList } = await import("../../manuallist.js");
  openManualList(key, { onSaved: () => tab.reload() });
}

async function openWords(name) {
  const { openWordList } = await import("../../wordlist.js");
  openWordList(name, { onDone: () => tab.reload() });
}

function onClick(e) {
  const help = e.target.closest("[data-help]");
  if (help) { openHelp(help.dataset.help); return; }
  const b = e.target.closest("[data-act]");
  if (!b || b.disabled) return;
  const act = b.dataset.act;
  if (act === "update-now") {
    run(() => ld.call("protection_update_now"), { done: () => tab.confirm("Lists will update in a few seconds", true) });
  } else if (act === "preview") preview();
  else if (act === "connect") connect();
  else if (act === "create") createList();
  else if (act === "check") check();
  else if (act === "check-act") checkAct();
  else if (act === "allow") allow();
  else if (act === "unallow") {
    const host = b.closest(".pl-chip")._key;
    run(() => ld.call("protection_unallow", host));
  } else if (act === "mine-open") openMine(b.closest(".pl-mine")._key);
  else if (act === "word-open") openWords(b.closest("[data-list]").dataset.list);
}

function onChange(e) {
  const t = e.target, act = t.dataset && t.dataset.act;
  if (!act) return;
  if (act === "list-on") run(() => ld.call("protection_set_list_enabled", t.closest(".pl-row")._key, t.checked));
  else if (act === "mine-on") run(() => ld.call("manual_set_enabled", t.closest(".pl-mine")._key, t.checked));
  else if (act === "auto" || act === "every") run(() => ld.call("protection_set_updates", els.auto.checked, +els.every.value));
  else if (act === "word-flag") run(() => ld.call("words_update", { [t.dataset.k]: t.checked }));
  else if (act === "word-list") {
    const id = t.closest("[data-list]").dataset.list;
    run(() => ld.call("words_update", id === "words" ? { words_on: t.checked } : id === "exceptions" ? { exceptions_on: t.checked } : { lists: { [id]: t.checked } }));
  }
}

// ---------------------------------------------------------------- pushes and the clock

/** Download progress (from the agent's watcher): patch the one row that is downloading; a different fingerprint of what the
 *  service wrote means a list finished - read again. */
export function onProgress(p) {
  if (!p || !data) return;
  if (p.rev && p.rev !== loadedRev) { if (shown) tab.reload(); else tab.stale(); return; }
  if (!shown) return;
  const before = live;
  live = p.key ? { key: p.key, text: p.text, pct: p.pct } : null;
  for (const key of new Set([before && before.key, live && live.key])) {
    if (!key) continue;
    for (const node of els.rows.children) if (node._key === key && node._row) paintStatus(node, node._row);
  }
}

function tick() {
  if (!data) return;
  for (const node of els.rows.children) if (node._row && (node._row.state === "updated")) paintStatus(node, node._row);
}

// ---------------------------------------------------------------- the tab contract

export function mount(host, t) {
  tab = t;
  root = host;
  host.innerHTML = `${STYLE}<div class="stack pl">
    <p class="muted small pl-intro">Always-on lists of harmful sites, kept up to date by the community and downloaded automatically (daily by default). They work next to your own blocklist; modes and the emergency unlock don't switch them off.</p>
    <div class="pl-cols"><div class="stack">${listsCard()}${mineCard()}${safeCard()}${checkCard()}</div><div class="stack">${wordsCard()}${allowedCard()}</div></div></div>`;
  Object.assign(els, {
    rows: q("rows"), mineRows: q("mine-rows"), mineEmpty: q("mine-empty"), chips: q("chips"), none: q("none"), auto: q("auto"), every: q("every"),
    cName: q("c-name"), cUrl: q("c-url"), cGo: q("c-go"), cMsg: q("c-msg"), mName: q("m-name"), mErr: q("m-err"),
    kHost: q("k-host"), kAct: q("k-act"), kMsg: q("k-msg"), aHost: q("a-host"), aErr: q("a-err"),
  });
  host.addEventListener("click", onClick);
  host.addEventListener("change", onChange);
  host.addEventListener("seg", (e) => { if (e.detail.name === "action") run(() => ld.call("words_update", { action: e.detail.value })); });
  host.addEventListener("keydown", (e) => {
    const go = e.key === "Enter" && e.target.dataset && e.target.dataset.enter;
    if (!go) return;
    e.preventDefault();
    ({ preview, create: createList, check: () => check(), allow })[go]();
  });
  els.cUrl.addEventListener("input", () => { previewed = null; pending = null; early = null; els.cGo.hidden = true; });
  wireIconReady(ld.on, host);
  ld.on("protection_progress", onProgress);
  ld.on("protection_preview", onPreview);
  tab.every(tick);
}

export async function load() {
  const d = await ld.call("protection_get");
  data = d;
  words = d.words;
  clock = { base: parseNaive(d.now), at: performance.now() };
  loadedRev = d.rev;
  live = null;                                  // (what was read says where the download is; the next push goes on from there)
  render();
}

export function show() { shown = true; }
export function hide() { shown = false; }
export function patch() { /* nothing in the status / draft pushes concerns this tab */ }
