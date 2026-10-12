// Reminders page (BLUEPRINT 3.8): the Sleep card, the Breaks card and "Your reminders". Everything applies at once
// (like the old window) - a box saves when you leave it or press Enter. Python parses what is typed ("30s", "5 min",
// "1h30") and decides whether a change to an IMPORTANT reminder needs the challenge: this file never compares old
// with new. A change that does gets a ticket back; `guarded()` opens the challenge, and the page reloads afterwards
// so a switch that was refused snaps back to what is saved.
//
// Page contract: mount() builds the static structure once, load() = one Api call (reminders_get) that fills it. The
// rows of "Your reminders" are keyed by id and reused; a field you are typing in is never overwritten by a reload.
import { ld } from "../bridge.js";
import { iconImg } from "../appicon.js";
import { guarded } from "../challenge.js";
import { check, confirmButton, el, empty, esc, field, ic, kic, seg, setText, sw, tp, toast } from "../ui.js";

// ---------------------------------------------------------------- time pickers (the designer's hh : mm boxes)
export const pad2 = (n) => String(n).padStart(2, "0");

/** "7" + "5" -> "07:05"; both empty -> "" (Python then says "Write times like 09:00"). Pure, so node can test it. */
export function normTime(h, m) {
  h = String(h == null ? "" : h).trim();
  m = String(m == null ? "" : m).trim();
  return h || m ? `${pad2(h || "0")}:${pad2(m || "0")}` : "";
}
export function getTp(node) {
  return normTime(node.querySelector(".tp-h").value, node.querySelector(".tp-m").value);
}
export function setTp(node, time) {
  const [h, m] = String(time || "00:00").split(":");
  const hi = node.querySelector(".tp-h"), mi = node.querySelector(".tp-m");
  if (hi.value !== (h || "")) hi.value = h || "";
  if (mi.value !== (m || "")) mi.value = m || "";
}
/** The two pickers of a tpRange(): [from, to]. */
export const getRange = (node) => { const [a, b] = node.querySelectorAll(".tp"); return [getTp(a), getTp(b)]; };
export function setRange(node, range) {
  const [a, b] = node.querySelectorAll(".tp");
  setTp(a, range[0]);
  setTp(b, range[1]);
}

export const freshMs = 10_000;
export const events = ["mode_changed"];     // a mode was added / renamed / deleted: the "Turn on a mode" list is read again
const DEBOUNCE_MS = 500;
const WAITS = [[0, "Off"], [10, "10 s"], [30, "30 s"], [60, "60 s"]];
const LENGTHS = [2, 5, 10, 15];
const BREAK_SNOOZES = [1, 2, 3, 5];

let root = null, page = null, data = null;
let rows = new Map();                       // id -> {node, refs}
let modesKey = "";
const timers = {};
let touched = 0;                            // counts what you do in the cards: a reload that started before it must not overwrite it
let inflight = 0;                           // saves on their way to Python

const q = (k) => root.querySelector(`[data-k="${k}"]`);
const num = (id, v) => `<input class="input" data-k="${id}" data-save="${id.startsWith("b-") ? "break" : "sleep"}" style="width:calc(${v}px*var(--s));text-align:center">`;
const select = (k, save, values) => `<select class="select select-sm" data-k="${k}" data-save="${save}">${values.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("")}</select>`;
const text = (k, save, label) => `<input class="input" data-k="${k}" data-save="${save}" maxlength="300" style="width:min(100%,calc(380px*var(--s)))" aria-label="${esc(label)}">`;
const flag = (k, save, label) => sw(false, `data-k="${k}" data-save="${save}" aria-label="${esc(label)}"`);

const STYLE = `<style>
.rem .help { padding: 0 var(--sp-4) var(--sp-3); color: var(--text-2); font-size: var(--fs-sm); }
.rem .field .field-c.wide { flex: 1 1 100%; justify-content: flex-start; margin-left: 0; }
.rem .tip { margin: var(--sp-3) var(--sp-4) var(--sp-4); padding: var(--sp-3); border: 1px solid var(--line); border-radius: var(--r-md); color: var(--text-2); font-size: var(--fs-sm); }
.rem .err { color: var(--k-block); font-size: var(--fs-sm); padding: 0 var(--sp-4) var(--sp-2); min-height: 0; }
.rem .err:empty { display: none; }
.rem .step input.in-step { width: calc(64px * var(--s)); text-align: center; }
.rem .step .lbl { color: var(--text-2); font-size: var(--fs-sm); }
</style>`;

function sleepCard() {
  return `<section class="card" data-card="sleep">
    <header class="card-h">${kic("sched", "moon")}<h3>Sleep</h3></header>
    <div class="card-b" style="padding:0">
      ${field("Remind me to go to bed", "A bedtime screen, also over games.", flag("s-on", "", "Remind me to go to bed"))}
      ${field("Important", "Turning it off or making it ask for less needs the challenge.", flag("s-guarded", "sleep", "Important"))}
      ${field("Bedtime", "", `<span data-k="s-bed">${tp("23:00", "Bedtime")}</span><span class="muted small">Wake up</span><span data-k="s-wake">${tp("07:00", "Wake up")}</span><button class="btn btn-sm" type="button" data-act="save-times">Save times</button>`)}
      ${field("Heads-up", "Off, 30, 1h or 45s before bedtime (up to 12 h).", `${num("s-before", 90)}<span class="muted small">before bedtime</span>`)}
      <div class="field" style="display:grid;gap:var(--sp-2);align-items:start"><div class="field-l"><b>Comes back</b><span>The later it is, the more often the bedtime screen returns after you dismiss it. Write how often (30s, 5 min, 1h30; at least 5s) and after what time.</span></div>
        <div class="steps" data-k="tiers"></div>
        <div><button class="btn btn-sm" type="button" data-act="tier-add">${ic("plus")}Add step</button></div></div>
      ${field("Dismiss wait", "Dismiss on the bedtime screen works only after this many seconds. A shorter wait (or Off) needs the challenge; a longer one is free.",
        `${seg("wait", WAITS.map(([v, t]) => [String(v), t]), "")}${num("s-wait", 56)}<span class="muted small">s</span>`)}
      ${field("Turn on a mode", "at bedtime, until wake-up time", select("s-mode", "sleep", [["", "No mode"]]))}
      ${field("Grey screen during bedtime", "The whole screen goes grey from bedtime until wake-up time, and gets its colour back after. Turning it off while this is Important needs the challenge. Windows may also have its own Color filters (Settings > Accessibility): Lockdown's grey is separate, and goes away when Lockdown gives the colour back.", flag("s-grey", "sleep", "Grey screen during bedtime"))}
      ${field("Heads-up says", "Your own words, or leave empty. {bedtime}, {wake} and {time} are filled in.", text("s-warn", "sleep", "Heads-up says"))}
      ${field("Bedtime says", "", text("s-text", "sleep", "Bedtime says"))}
      <p class="err" data-k="s-err" role="alert"></p>
      <p class="tip">At bedtime the screen dims with a “Time for bed” message (also over games).</p>
    </div></section>`;
}

function breaksCard() {
  return `<section class="card" data-card="break">
    <header class="card-h">${kic("k-ok", "coffee")}<h3>Breaks</h3><span class="chip chip-soft" data-k="b-today"></span></header>
    <div class="card-b" style="padding:0">
      ${field("Remind me to take breaks", "", flag("b-on", "", "Remind me to take breaks"))}
      ${field("After", "of use without a break: 45 min, 30s, 1h30", num("b-every", 90))}
      ${field("Snooze", "each time", num("b-snooze", 90))}
      ${field("Break length", "", select("b-length", "break", LENGTHS.map((n) => [n, `${n} min`])))}
      ${field("It says", "Your own words, or leave empty. {every} and {length} are filled in.", text("b-text", "break", "It says"))}
      <p class="err" data-k="b-err" role="alert"></p>
      ${field("Important", "Turning it off or making it ask for less needs the challenge.", flag("b-guarded", "break", "Important"))}
      ${field("Strict break", "Minimises everything until the break is over - you can't just dismiss it.", flag("b-strict", "break", "Strict break"))}
      ${field("Snoozes first", "before a strict break starts on its own", select("b-max", "break", BREAK_SNOOZES.map((n) => [n, String(n)])))}
      <div class="field" style="display:grid;gap:var(--sp-2);align-items:start"><div class="field-l"><b>Allowed during breaks</b><span>These apps are not minimised during a strict break and don't bring the break screen back. Adding one needs the challenge; taking one off does not.</span></div>
        <div data-k="allow"></div>
        <div class="hstack"><button class="btn btn-sm" type="button" data-act="allow-add">${ic("plus")}Add app…</button><button class="btn btn-sm" type="button" data-act="allow-restore" hidden>Add Lockdown + Settings back</button></div>
        <p class="err" data-k="allow-err" role="alert"></p></div>
      ${field("20-20-20", "Every 20 min, look 20 feet (6 m) away for 20 seconds.", flag("b-twenty", "break", "20-20-20"))}
      ${field("20-20-20 says", "Your own words, or leave empty.", text("b-twenty-text", "break", "20-20-20 says"))}
    </div></section>`;
}

function ownCard() {
  return `<section class="card" data-card="own">
    <header class="card-h">${kic("k-brand", "alarm")}<h3>Your reminders</h3><button class="btn btn-sm" type="button" data-act="new">${ic("plus")}New reminder</button></header>
    <div class="card-b" style="padding:0"><div data-k="rows"></div>
      <div data-k="empty">${empty("k-brand", "alarm", "No reminders yet", "e.g. “Drink water” every 60 min. Drink water, stretch, take medicine - on a timer, at set times, or once a day at random.")}</div></div>
  </section>`;
}

export function mount(host, p) {
  page = p;
  host.innerHTML = `${STYLE}<div class="page rem"><div class="page-h"><h1>Reminders</h1></div>
    <div class="grid-2"><div class="stack">${sleepCard()}</div><div class="stack">${breaksCard()}${ownCard()}</div></div></div>`;
  root = host;
  root.addEventListener("change", onChange);
  root.addEventListener("input", onInput);
  root.addEventListener("seg", (e) => {
    if (e.detail.name !== "wait") return;
    q("s-wait").value = e.detail.value === "0" ? "Off" : e.detail.value;
    saveSleep();
  });
  root.addEventListener("click", onClick);
}

// ---------------------------------------------------------------- reading the boxes
function tierRow(every, after) {
  const n = el(`<span class="step" data-tier><input class="in-step" data-k="t-every" aria-label="How often" value="${esc(every)}"><span class="lbl">after</span>${tp(after, "After")}<button class="icon-btn icon-btn-sm" type="button" data-act="tier-x" aria-label="Remove step">${ic("x")}</button></span>`);
  return n;
}

function sleepCfg() {
  return {
    on: q("s-on").checked, guarded: q("s-guarded").checked, bedtime: getTp(q("s-bed")), wake: getTp(q("s-wake")),
    before: q("s-before").value, mode: q("s-mode").value, dismiss_wait: q("s-wait").value, grey: q("s-grey").checked,
    warn_text: q("s-warn").value, text: q("s-text").value,
    tiers: [...root.querySelectorAll("[data-tier]")].map((t) => ({ every: t.querySelector("input").value, after: getTp(t.querySelector(".tp")) })),
  };
}

function breakCfg() {
  return {
    on: q("b-on").checked, guarded: q("b-guarded").checked, every: q("b-every").value, snooze: q("b-snooze").value,
    length: +q("b-length").value, strict: q("b-strict").checked, max_snooze: +q("b-max").value,
    twenty: q("b-twenty").checked, text: q("b-text").value, twenty_text: q("b-twenty-text").value,
  };
}

// ---------------------------------------------------------------- saving
async function run(call, errKey) {
  const err = errKey ? q(errKey) : null;
  if (err) err.textContent = "";
  inflight++;
  try {
    await guarded(call());
  } catch (e) {
    if (err) err.textContent = e.message || "That didn't work.";
    else toast(e.message || "That didn't work.");
  } finally {
    inflight--;
  }
  page.reload();                      // what is saved is the truth: a refused switch snaps back, "30" becomes "30 min"
}

const saveSleep = () => run(() => ld.call("reminder_save_sleep", sleepCfg()), "s-err");
const saveBreak = () => run(() => ld.call("reminder_save_break", breakCfg()), "b-err");

function flush(which) {
  clearTimeout(timers[which]);
  delete timers[which];
  return which === "sleep" ? saveSleep() : saveBreak();
}

function onChange(e) {
  touched++;
  const t = e.target;
  const k = t.dataset.k;
  if (k === "s-on" || k === "b-on") {                       // the two main switches: their own call, no form to validate
    return run(() => ld.call("reminder_set_on", k === "s-on" ? "sleep" : "break", t.checked), null);
  }
  const which = t.dataset.save || (t.closest("[data-tier]") ? "sleep" : "");
  if (which) flush(which);
  else if (t.dataset.rowSwitch !== undefined) {
    const id = t.closest("[data-id]").dataset.id;
    run(() => ld.call("reminder_set_on", id, t.checked), null);
  }
}

function onInput(e) {                                       // arrow keys / wheel on a time box: save when they stop
  touched++;
  const t = e.target;
  if (!t.closest(".tp") || !t.closest("[data-tier]")) return;
  clearTimeout(timers.sleep);
  timers.sleep = setTimeout(() => flush("sleep"), DEBOUNCE_MS);
}

/** The editor is its own module, loaded the first time it is needed (the page opens without it). */
async function edit(reminder) {
  const { openReminderEditor } = await import("../reminder_editor.js");
  openReminderEditor(reminder, data.packs, () => page.reload());
}

function onClick(e) {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const act = b.dataset.act;
  if (act === "save-times") flush("sleep");
  else if (act === "tier-add") {
    const last = root.querySelectorAll("[data-tier]");
    b.closest(".field").querySelector('[data-k="tiers"]').append(tierRow(last.length ? "5 min" : "15 min", "21:00"));
    flush("sleep");
  } else if (act === "tier-x") {
    b.closest("[data-tier]").remove();
    flush("sleep");
  } else if (act === "allow-add") {
    import("../appbrowser.js").then((m) => m.openAppBrowser({ mode: "pick", onPick: (app) => run(() => ld.call("break_allow_add", app.exe), "allow-err") }));
  } else if (act === "allow-restore") run(() => ld.call("break_allow_restore"), "allow-err");
  else if (act === "allow-x") run(() => ld.call("break_allow_remove", b.closest("[data-exe]").dataset.exe), "allow-err");
  else if (act === "new") edit(null);
  else if (act === "edit") {
    const item = rows.get(b.closest("[data-id]").dataset.id);
    if (item) edit(item.view);
  }
}

// ---------------------------------------------------------------- filling (never touches a box you are typing in)
const idle = (node) => node !== document.activeElement;
function put(node, value) {
  if (idle(node) && node.value !== String(value)) node.value = String(value);
}
function putChecked(node, on) {
  node.checked = !!on;
}

function fillTiers(tiers) {
  const box = q("tiers");
  if (box.contains(document.activeElement)) return;
  const have = [...box.querySelectorAll("[data-tier]")];
  tiers.forEach((t, i) => {
    let n = have[i];
    if (!n) { n = tierRow(t.every, t.after); box.append(n); }
    const input = n.querySelector("input");
    if (input.value !== t.every) input.value = t.every;
    setTp(n.querySelector(".tp"), t.after);
  });
  have.slice(tiers.length).forEach((n) => n.remove());
}

function fillModes(list, current) {
  const key = JSON.stringify(list);
  const sel = q("s-mode");
  if (key !== modesKey) {
    modesKey = key;
    sel.replaceChildren(...[{ id: "", name: "No mode" }, ...list].map((m) => {
      const o = document.createElement("option");
      o.value = m.id;
      o.textContent = m.name;
      return o;
    }));
  }
  sel.value = list.some((m) => m.id === current) ? current : "";
}

function fillSleep(s, modes) {
  putChecked(q("s-on"), s.on);
  putChecked(q("s-guarded"), s.guarded);
  putChecked(q("s-grey"), s.grey);
  for (const [k, value] of [["s-bed", s.bedtime], ["s-wake", s.wake]]) {
    if (!q(k).contains(document.activeElement)) setTp(q(k), value);
  }
  put(q("s-before"), s.before);
  fillTiers(s.tiers);
  put(q("s-wait"), s.dismiss_wait ? s.dismiss_wait : "Off");
  for (const b of root.querySelectorAll('.seg[data-seg="wait"] button')) b.setAttribute("aria-pressed", String(b.dataset.v === String(s.dismiss_wait)));
  fillModes(modes, s.mode);
  put(q("s-warn"), s.warn_text);
  put(q("s-text"), s.text);
  q("s-warn").placeholder = data.defaults.sleep_warn_text;
  q("s-text").placeholder = data.defaults.sleep_text;
}

function fillBreak(b) {
  putChecked(q("b-on"), b.on);
  putChecked(q("b-guarded"), b.guarded);
  putChecked(q("b-strict"), b.strict);
  putChecked(q("b-twenty"), b.twenty);
  put(q("b-every"), b.every);
  put(q("b-snooze"), b.snooze);
  q("b-length").value = String(b.length);
  q("b-max").value = String(b.max_snooze);
  put(q("b-text"), b.text);
  put(q("b-twenty-text"), b.twenty_text);
  q("b-text").placeholder = data.defaults.break_text;
  q("b-twenty-text").placeholder = data.defaults.twenty_text;
  setText(q("b-today"), `Breaks today: ${b.today}`);
  fillAllow(b.allow, b.allow_missing);
}

let allowKey = "";
/** "Allowed during breaks": one row per app, drawn again only when the list changes. */
export function fillAllow(allow, missing) {
  const key = JSON.stringify(allow);
  if (key !== allowKey) {
    allowKey = key;
    q("allow").replaceChildren(...allow.map((a) => el(`<div class="row row-sm row-simple" data-exe="${esc(a.exe)}"><span class="aic aic-sm">${iconImg("app", a.exe)}</span>` +
      `<span class="name"><b>${esc(a.name)}</b>${a.name !== a.exe ? `<span>${esc(a.exe)}</span>` : ""}</span>` +
      `<button class="btn btn-sm" type="button" data-act="allow-x" aria-label="Remove ${esc(a.name)}">Remove</button></div>`)));
    if (!allow.length) q("allow").replaceChildren(el(`<p class="muted small" style="margin:0">Nothing - a strict break minimises every app.</p>`));
  }
  root.querySelector('[data-act="allow-restore"]').hidden = !missing.length;
}

function newRow(id) {
  const node = el(`<div class="field" data-id="${esc(id)}"><div class="field-l"><b class="t"></b><span class="sub"></span></div>
    <div class="field-c"><span class="tag k-brand imp" hidden>${ic("lock")}Important</span><button class="btn btn-sm" type="button" data-act="edit">Edit</button><span class="del"></span>${sw(false, 'data-row-switch aria-label="On"')}</div></div>`);
  const del = confirmButton({
    label: "Delete", cls: "btn btn-sm btn-danger", confirmLabel: "Sure?",
    onConfirm: () => run(() => ld.call("reminder_delete", id), null),
  });
  node.querySelector(".del").replaceWith(del);
  return { node, view: null, t: node.querySelector(".t"), sub: node.querySelector(".sub"), imp: node.querySelector(".imp"),
    on: node.querySelector("input[data-row-switch]") };
}

function fillRows(items) {
  const list = q("rows");
  const keep = new Set();
  items.forEach((r, i) => {
    keep.add(r.id);
    let row = rows.get(r.id);
    if (!row) { row = newRow(r.id); rows.set(r.id, row); }
    row.view = r;
    setText(row.t, r.text || "(no text)");
    setText(row.sub, `${r.schedule} · ${r.stats}`);
    row.imp.hidden = !r.guarded;
    row.on.checked = r.on;
    if (list.children[i] !== row.node) list.insertBefore(row.node, list.children[i] || null);
  });
  for (const [id, row] of rows) {
    if (!keep.has(id)) { row.node.remove(); rows.delete(id); }
  }
  q("empty").hidden = items.length > 0;
}

export function patch({ event }) {
  if (event === "mode_changed") page.reload();
}

export async function load() {
  const mark = touched;
  data = await ld.call("reminders_get");
  if (mark === touched && !inflight && !Object.keys(timers).length) {      // (else the next reload, after your save, fills them)
    fillSleep(data.sleep, data.modes);
    fillBreak(data.break);
  } else if (!modesKey) fillModes(data.modes, "");
  fillRows(data.custom);
}
