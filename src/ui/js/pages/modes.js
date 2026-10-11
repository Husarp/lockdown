// Modes page (BLUEPRINT 3.7): what is on right now, every mode with Start / Edit, the Start panel and the editor.
//
// Python decides everything that matters (webapi/modes.py): the end time of "30 min" / "Until 17:00" is worked out there on
// the trusted clock, and whether stopping a locked mode, starting over it, deleting it or saving it so that it blocks less
// needs the challenge - this file never compares old with new. A call that does gets a ticket back and `guarded()` opens the
// challenge; Cancel leaves everything as it was (an open editor keeps its edits).
//
// Page contract: mount() builds the structure once, load() = ONE Api call (modes_get) that fills it. The mode cards are
// keyed by id and reused. The countdowns tick on the page's own clock from end timestamps (page.every, only while visible);
// the status push carries the mode that is on, so a mode started from the tray shows here without an Api call. The editor is
// its own module (modes/editor.js), loaded the first time you press New mode or Edit.
import { ld } from "../bridge.js";
import { CANCELLED, clockOf, countdownText, guarded, parseNaive } from "../challenge.js";
import { el, esc, ic, kic, seg, setText, toast, tp } from "../ui.js";

export const freshMs = 10_000;
export const events = ["status", "mode_changed"];

const MODE_ICONS = { work: "briefcase", study: "book", focus: "target", dnd: "mute", relax: "coffee" };
const OWN_ICON = "layers";
const DURATIONS = [["30", "30 min"], ["60", "1 h"], ["120", "2 h"], ["at", "Until…"], ["stop", "Until I stop"]];
const REASK_MS = 2000;            // a countdown that ended but the new state says the same: ask again after this

// ---------------------------------------------------------------- plain helpers (node tests)

/** The icon of a mode: its own for the built-in ones, one for the modes you make. */
export const modeIcon = (id) => MODE_ICONS[id] || OWN_ICON;

/** "focus" -> "Focus", "long break" -> "Long break". */
export const phaseTitle = (name) => (name ? name.charAt(0).toUpperCase() + name.slice(1) : "");

/** "7" + "5" -> "07:05"; both empty -> "" (Python then says "Write the time like 17:00."). */
export function normTime(h, m) {
  h = String(h == null ? "" : h).trim();
  m = String(m == null ? "" : m).trim();
  return h || m ? `${h.padStart(2, "0") || "00"}:${m.padStart(2, "0") || "00"}` : "";
}
const tpValue = (node) => normTime(node.querySelector(".tp-h").value, node.querySelector(".tp-m").value);

/** What the Now card says for the mode that is on (`a` = the status snapshot's `mode`, or null) at `nowMs` (the page's
 *  trusted clock, naive local ms): {name, text, stop: {label} | null, due: when the text is out of date, NaN = never}. */
export function nowView(a, nowMs) {
  if (!a) return { name: "No mode", text: "No mode is on.", stop: null, due: NaN };
  const left = (iso) => countdownText((parseNaive(iso) - nowMs) / 1000);
  let text, due = NaN;
  if (a.phase) {
    text = `${phaseTitle(a.phase.name)} - ${left(a.phase.ends_at)} left (round ${a.phase.round} of ${a.phase.rounds})`;
    due = parseNaive(a.phase.ends_at);
  } else if (a.by_schedule) {
    text = "On by its schedule" + (a.ends_at ? ` until ${clockOf(a.ends_at)}` : "");
    due = a.ends_at ? parseNaive(a.ends_at) : NaN;
  } else if (a.ends_at) {
    text = `Until ${clockOf(a.ends_at)} - ${left(a.ends_at)} left`;
    due = parseNaive(a.ends_at);
  } else {
    text = "Until you stop it";
  }
  // a mode on by its schedule is stopped by changing its times, not here; a locked one asks for the challenge
  const stop = a.by_schedule ? null : { label: a.locked && a.ends_at ? `Stop (locked until ${clockOf(a.ends_at)})` : "Stop" };
  return { name: a.name, text, stop, due };
}

/** A short signature of the mode that is on (to tell "something changed" from a status push that repeats it). */
export const activeSig = (a) => (a ? JSON.stringify([a.id, a.locked, a.by_schedule, a.ends_at, a.phase && [a.phase.name, a.phase.ends_at, a.phase.round]]) : "");

/** The Start panel's words for a mode: its title, whether the length buttons show, the "Runs N min" line. */
export function startView(mode) {
  const pomodoro = !!mode.pomodoro;
  return { title: `Start ${mode.name}`, lengths: !pomodoro, runs: pomodoro ? `Runs ${mode.run_min} min (all rounds).` : "" };
}

/** The `until` argument of mode_start for the length buttons: minutes, "HH:MM" or null (until I stop it). */
export function untilArg(choice, time) {
  if (choice === "stop") return null;
  if (choice === "at") return time;
  return Number(choice);
}

// ---------------------------------------------------------------- the page

const STYLE = `<style>
.md .md-split { grid-template-columns: calc(340px * var(--s)) minmax(0, 1fr); }
.md .md-now .card-b { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.md .md-nowt { flex: 1 1 calc(220px * var(--s)); min-width: 0; display: grid; gap: 2px; }
.md .md-name { font: var(--fw-semibold) var(--fs-num)/1.1 var(--font-display); }
.md .md-text { color: var(--text-2); font-variant-numeric: tabular-nums; }
.md .md-err { margin: 0; color: var(--k-block); font-size: var(--fs-sm); font-weight: var(--fw-semibold); }
.md .md-err:empty { display: none; }
.md .md-now .md-err { flex: 1 1 100%; }
.md .md-row { align-items: flex-start; cursor: default; }
.md .md-row .kic { margin-top: 2px; }
.md .md-row .grow b { font-weight: var(--fw-semibold); }
.md .md-row .grow > span[hidden] { display: none; }
.md .md-btns { margin-top: var(--sp-2); }
.md .md-row .chip { flex: none; }
.md .md-hint { margin: 0; padding: var(--sp-4); border: 1px dashed var(--line-strong); border-radius: var(--r-lg); color: var(--text-2); }
.md .md-form { gap: var(--sp-4); }
.md .md-cat { --cat: var(--brand); }
.md .md-cat input { border-color: var(--cat); }
.md .md-cat input:checked { background: var(--cat); }
.md .md-held { margin: 0; padding: var(--sp-2) var(--sp-3); border-radius: var(--r-md); background: var(--surface-3); font-size: var(--fs-sm); color: var(--text-2); }
.md .md-pick { display: grid; gap: var(--sp-2); padding: var(--sp-3); border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface-2); }
.md .pill .aic { margin-left: calc(var(--sp-1) * -1); }
@media (max-width: 1100px) { .md .md-work { order: -1; } }
</style>`;

let root = null, page = null, d = null;
let clock = { base: NaN, at: 0 };
let rows = new Map();                  // mode id -> {node, refs}
let starting = null;                   // the mode id the Start panel is for
let editor = null;                     // the editor module's object, once loaded
let editorLoading = null;
let firedFor = "", firedAt = 0;
let lastText = "";

const q = (k) => root.querySelector(`[data-k="${k}"]`);
const nowMs = () => clock.base + (performance.now() - clock.at);

function nowCard() {
  return `<section class="card md-now" data-k="now"><div class="card-b">${kic("k-mode", "target")}
    <div class="md-nowt"><b class="md-name" data-k="now-name">No mode</b><span class="md-text" data-k="now-text"></span></div>
    <button class="btn" type="button" data-act="stop" data-k="stop" hidden>Stop</button>
    <p class="md-err" data-k="now-err" role="alert"></p></div></section>`;
}

function startCard() {
  return `<section class="card" data-k="start" hidden><header class="card-h">${kic("k-mode", "play")}<h3 data-k="start-title">Start</h3></header>
    <div class="card-b stack-sm">
      <div class="hstack wrap" data-k="lengths">${seg("dur", DURATIONS, "60")}<span data-k="until" hidden>${tp("17:00", "Until")}</span></div>
      <p class="small muted" data-k="runs" hidden></p>
      <label class="check"><input type="checkbox" data-k="lock"><span>Lock until it ends (stopping it early needs the Anti-Bypass challenge; the emergency unlock still works)</span></label>
      <p class="md-err" data-k="start-err" role="alert"></p>
      <div class="hstack"><button class="btn btn-primary" type="button" data-act="go">${ic("play")}Start</button><button class="btn" type="button" data-act="start-cancel">Cancel</button></div>
    </div></section>`;
}

export function mount(host, p) {
  page = p;
  root = host;
  host.innerHTML = `${STYLE}<div class="page md">
    <div class="page-h"><div class="grow"><h1>Modes</h1><p class="sub">A mode blocks a category plus the sites and apps you pick, on top of your usual rules.</p></div>
      <button class="btn btn-primary" type="button" data-act="new">${ic("plus")}New mode</button></div>
    ${nowCard()}
    <div class="split md-split"><div class="mode-list" data-k="list" aria-label="Your modes"></div>
      <div class="stack md-work">${startCard()}<section class="card md-editor" data-k="editor" hidden></section>
        <p class="md-hint" data-k="hint">Press <b>Start</b> on a mode to run it now, or <b>Edit</b> to change what it blocks, its Pomodoro, its automatic times and whether it mutes notifications.</p></div></div></div>`;
  host.addEventListener("click", onClick);
  host.addEventListener("seg", (e) => { if (e.detail.name === "dur") syncStart(); });
  page.every(tick);
}

// ---------------------------------------------------------------- loading and painting

export async function load() {
  apply(await ld.call("modes_get"));
}

/** Take a whole page answer (from modes_get or from a change that returns it) and paint it. */
function apply(next) {
  d = next;
  const t = parseNaive(d.now);
  if (!Number.isNaN(t)) clock = { base: t, at: performance.now() };
  paintNow(true);
  paintRows();
  if (starting && !d.modes.some((m) => m.id === starting)) closeStart();
}

/** Leaving the page puts it back to its default view, as the old window did: no Start panel, no open editor. */
export function hide() {
  if (starting) closeStart();
  if (editor && editor.isOpen()) editor.close();
}

export function patch({ event, payload }) {
  if (!d) return;
  if (event === "mode_changed") { page.reload(); return; }
  if (event === "status" && payload && "mode" in payload && activeSig(payload.mode) !== activeSig(d.active)) {
    d.active = payload.mode;
    paintNow(true);
    paintRows();
  }
}

function paintNow(force) {
  const v = nowView(d.active, nowMs());
  setText(q("now-name"), v.name);
  setText(q("now-text"), v.text);
  const stop = q("stop");
  stop.hidden = !v.stop;
  if (v.stop) setText(stop, v.stop.label);
  if (force) q("now-err").textContent = "";
  lastText = v.text;
  return v;
}

function tick() {
  if (!d) return;
  const v = nowView(d.active, nowMs());
  if (v.text !== lastText) paintNow(false);
  if (!Number.isNaN(v.due) && v.due <= nowMs()) {
    const key = String(v.due);
    if (firedFor !== key || performance.now() - firedAt > REASK_MS) {       // the countdown ended: ask what is on now (once)
      firedFor = key;
      firedAt = performance.now();
      page.reload();
    }
  }
}

function newRow(m) {
  const node = el(`<div class="mode-item md-row" role="group" data-id="${esc(m.id)}">${kic("k-mode", modeIcon(m.id))}
    <div class="grow"><b class="n"></b><span class="d"></span><span class="p" hidden></span><span class="a" hidden></span>
      <div class="hstack md-btns"><button class="btn btn-sm" type="button" data-act="start">${ic("play")}Start</button><button class="btn btn-sm" type="button" data-act="edit">${ic("pencil")}Edit</button></div></div>
    <span class="chip chip-mode" hidden>On</span></div>`);
  return { node, n: node.querySelector(".n"), dd: node.querySelector(".d"), p: node.querySelector(".p"), a: node.querySelector(".a"),
    on: node.querySelector(".chip"), start: node.querySelector('[data-act="start"]'), edit: node.querySelector('[data-act="edit"]'), mode: m };
}

function paintRows() {
  const list = q("list");
  const keep = new Set();
  d.modes.forEach((m, i) => {
    keep.add(m.id);
    let row = rows.get(m.id);
    if (!row) { row = newRow(m); rows.set(m.id, row); }
    row.mode = m;
    if (row.n.textContent !== m.name) {
      row.n.textContent = m.name;
      row.start.setAttribute("aria-label", `Start ${m.name}`);
      row.edit.setAttribute("aria-label", `Edit ${m.name}`);
    }
    setText(row.dd, m.describe);
    setText(row.p, m.pomodoro_text);
    row.p.hidden = !m.pomodoro_text;
    setText(row.a, m.auto_text);
    row.a.hidden = !m.auto_text;
    row.on.hidden = !(d.active && d.active.id === m.id);
    row.node.setAttribute("aria-current", String(m.id === starting || (editor && editor.openId() === m.id)));
    if (list.children[i] !== row.node) list.insertBefore(row.node, list.children[i] || null);
  });
  for (const [id, row] of rows) {
    if (!keep.has(id)) { row.node.remove(); rows.delete(id); }
  }
}

// ---------------------------------------------------------------- the Start panel

function openStart(mode) {
  starting = mode.id;
  const v = startView(mode);
  setText(q("start-title"), v.title);
  q("lengths").hidden = !v.lengths;
  setText(q("runs"), v.runs);
  q("runs").hidden = !v.runs;
  q("start-err").textContent = "";
  q("lock").checked = false;
  for (const b of q("lengths").querySelectorAll(".seg button")) b.setAttribute("aria-pressed", String(b.dataset.v === "60"));
  syncStart();
  q("start").hidden = false;
  q("hint").hidden = true;
  paintRows();
  q("start").scrollIntoView({ block: "nearest" });
  q("start").querySelector('[data-act="go"]').focus();
}

function closeStart() {
  starting = null;
  q("start").hidden = true;
  if (!editor || !editor.isOpen()) q("hint").hidden = false;
  paintRows();
}

/** The length buttons decide what else shows: the time box for "Until...", and Lock is off for "Until I stop". */
function syncStart() {
  const pressed = q("lengths").querySelector('.seg [aria-pressed="true"]');
  const choice = pressed ? pressed.dataset.v : "60";
  const mode = d && d.modes.find((m) => m.id === starting);
  const pomodoro = !!(mode && mode.pomodoro);
  q("until").hidden = pomodoro || choice !== "at";
  const lock = q("lock");
  lock.disabled = !pomodoro && choice === "stop";
  if (lock.disabled) lock.checked = false;
}

async function go() {
  const mode = d.modes.find((m) => m.id === starting);
  if (!mode) return;
  const pressed = q("lengths").querySelector('.seg [aria-pressed="true"]');
  const choice = pressed ? pressed.dataset.v : "60";
  const err = q("start-err");
  err.textContent = "";
  const button = q("start").querySelector('[data-act="go"]');
  button.disabled = true;
  try {
    const res = await guarded(ld.call("mode_start", mode.id, untilArg(choice, tpValue(q("until").querySelector(".tp"))), q("lock").checked));
    if (res === CANCELLED) return;                                 // challenge cancelled: nothing changed, the panel stays
    done(res);
    closeStart();
  } catch (e) {
    err.textContent = e.message || "That didn't work.";
  } finally {
    button.disabled = false;
  }
}

/** A change came back with the new page (and maybe a message to say). */
function done(res) {
  if (res && res.modes) {
    apply(res);
    if (res.message) toast(res.message);
  } else page.reload();
}

async function stop() {
  const err = q("now-err");
  err.textContent = "";
  const button = q("stop");
  button.disabled = true;
  try {
    const res = await guarded(ld.call("mode_stop"));
    if (res !== CANCELLED) done(res);
  } catch (e) {
    err.textContent = e.message || "That didn't work.";
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- the editor (its own module)

async function openEditor(mode) {
  if (!editor) {
    editorLoading = editorLoading || import("./modes/editor.js");
    const mod = await editorLoading;
    if (!editor) {
      editor = mod.createEditor(q("editor"), {
        data: () => d,
        changed: (res, message) => { done(res); if (message) toast(message); },
        closed: () => { q("hint").hidden = !!starting; paintRows(); },
      });
    }
  }
  editor.open(mode);
  q("hint").hidden = true;
  paintRows();
  q("editor").scrollIntoView({ block: "nearest" });
}

function onClick(e) {
  const b = e.target.closest("[data-act]");
  if (!b || b.closest(".md-editor")) return;
  const act = b.dataset.act;
  const row = b.closest("[data-id]");
  const mode = row ? d.modes.find((m) => m.id === row.dataset.id) : null;
  if (act === "start" && mode) openStart(mode);
  else if (act === "edit" && mode) openEditor(mode);
  else if (act === "new") openEditor(null);
  else if (act === "go") go();
  else if (act === "start-cancel") closeStart();
  else if (act === "stop") stop();
}
