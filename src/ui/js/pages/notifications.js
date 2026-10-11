// Notifications page (BLUEPRINT 3.9): Blocked Visit Alerts, Upcoming Blocks and the Weekly Summary - the REAL options of
// the old window (the mock's Events / Sound / Position / Quiet hours have no backend here and are left out).
//
// The alert and warning settings are part of the DRAFT: every change goes to Python through `draftSetSetting` (js/draft.js:
// text is sent 400 ms after the last key or when the box is left, switches and menus at once) and shows in the save bar as
// "Notification settings"; with Auto-save off they wait for Save changes. Python validates every value (webapi/draft.py).
// Nothing here reloads the Blocking page. "Remind me again every" is parsed in Python (notify_repeat_set: "30s", "2 min",
// Off). The weekly summary has no draft: digest_set saves it at once.
//
// Page contract: mount() builds the static structure once, load() = one Api call (notifications_get) fills it. The seven
// reasons are built on the first load and reused; a box you are typing in is never overwritten by a reload. A Discard
// (the `draft` push says "discarded") reloads the page so the boxes show the saved values again.
import { ld } from "../bridge.js";
import { applyState, draftSetSetting, flushSettings } from "../draft.js";
import { el, esc, field, ic, kic, seg, stepper, sw, toast } from "../ui.js";

export const freshMs = 10_000;
export const events = ["draft"];

export const PLACEHOLDERS = "{site}  {reason}  {until}";
export const WARN_MIN = 1, WARN_MAX = 60;

// ---------------------------------------------------------------- plain helpers (node tests)

/** The text a message box saves: what is typed, trimmed - an empty box means "the default message" (as in the old window). */
export const messageValue = (text, fallback) => String(text == null ? "" : text).trim() || fallback;

/** "Warn me N min before": a whole number from 1 to 60, or null while the box is empty / not a number. */
export function warnMinutes(text) {
  const n = Number.parseInt(String(text).trim(), 10);
  return Number.isFinite(n) ? Math.min(WARN_MAX, Math.max(WARN_MIN, n)) : null;
}

/** "1" / "0" for a switch. */
export const flagValue = (on) => (on ? "1" : "0");

/** The options of a menu as [value, label] pairs: `values` as they are, labelled by `label(value)`. */
export const menuOptions = (values, label) => values.map((v) => [String(v), label(v)]);

// ---------------------------------------------------------------- structure

const STYLE = `<style>
.nt .nt-cells { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--sp-2); }
.nt .nt-cell { display: flex; border: 1px solid var(--line-strong); border-radius: var(--r-md); padding: var(--sp-3); background: var(--surface); }
.nt .nt-cell:has(input:checked) { border-color: var(--brand); box-shadow: inset 0 0 0 1px var(--brand); }
.nt .nt-cell:has(input:focus-visible) { outline: 2px solid var(--brand); outline-offset: 2px; }
.nt .nt-pad { padding: var(--sp-3) var(--sp-4) 0; }
.nt .nt-msgs { display: grid; gap: var(--sp-2); padding: var(--sp-3) var(--sp-4) var(--sp-2); }
.nt .nt-msg { display: grid; grid-template-columns: calc(210px * var(--s)) minmax(0, 1fr) auto; gap: var(--sp-2); align-items: center; }
.nt .nt-msg label { color: var(--text-2); font-size: var(--fs-sm); }
.nt .nt-more { display: flex; align-items: center; gap: var(--sp-2); width: 100%; border: 0; background: none; padding: var(--sp-3) var(--sp-4) 0; text-align: left; color: var(--text); }
.nt .nt-more .ic { transition: transform var(--dur) var(--ease); color: var(--text-2); }
.nt .nt-more[aria-expanded="true"] .ic { transform: rotate(90deg); }
.nt .nt-note { padding: var(--sp-2) var(--sp-4) var(--sp-4); color: var(--text-2); font-size: var(--fs-sm); text-wrap: pretty; }
.nt .nt-help { padding: 0 var(--sp-4) var(--sp-3); color: var(--text-2); font-size: var(--fs-sm); text-wrap: pretty; }
.nt .nt-digest { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; padding: var(--sp-3) var(--sp-4); }
.nt .nt-repeat { width: calc(84px * var(--s)); text-align: center; }
.nt .nt-err { color: var(--k-block); font-size: var(--fs-sm); padding: 0 var(--sp-4) var(--sp-2); }
.nt .nt-err:empty { display: none; }
@media (max-width: 860px) { .nt .nt-cells { grid-template-columns: repeat(2, minmax(0, 1fr)); } .nt .nt-msg { grid-template-columns: minmax(0, 1fr) auto; } .nt .nt-msg label { grid-column: 1 / -1; } }
</style>`;

const help = (key, label) => `<button class="icon-btn icon-btn-sm" type="button" data-act="help" data-help="${key}" aria-expanded="false" aria-label="More about ${esc(label)}">${ic("info")}</button>`;
const HELP = {
  fmt: "Windows draws its notifications, so they never cover a game the way a pop-up window can. While a full-screen app is in front, Lockdown always uses them (whatever you pick here). Windows' \"Do not disturb when playing a game\" keeps them out of games entirely - they wait in the notification centre (the bell) and clear themselves later.",
  digest: "Screen time compared with the week before, blocked visits, your top app and site, the days within your goal and your streaks.",
};
const helpRow = (key) => `<p class="nt-help" data-helptext="${key}" hidden>${esc(HELP[key])}</p>`;

function alertsCard() {
  return `<section class="card" data-card="alerts">
    <header class="card-h">${kic("k-block", "ban")}<h3>Blocked Visit Alerts</h3></header>
    <p class="muted small nt-pad">Notify me when I try to open a site (or start an app) that is:</p>
    <div class="card-b"><div class="nt-cells" data-k="cells"></div></div>
    <button class="nt-more" type="button" data-act="messages" aria-expanded="false">${ic("chev-r")}<b>Messages</b><span class="muted small">change what each alert says</span></button>
    <div class="nt-msgs" data-k="msgs" hidden></div>
    <div class="card-b" style="padding:0">
      ${field("Don't repeat for the same site within", "", `<select class="select select-sm" data-k="cooldown" aria-label="Cooldown"></select>`)}
      ${field("Show as", "", `${seg("nfmt", [], "")}${help("fmt", "Show as")}`)}
      ${helpRow("fmt")}
    </div>
    <p class="nt-note">Per-site override: the Alerts column in Blocking &gt; Overview.</p>
  </section>`;
}

function warnCard() {
  return `<section class="card" data-card="warn">
    <header class="card-h">${kic("k-limit", "timer")}<h3>Upcoming Blocks</h3></header>
    <div class="card-b" style="padding:0">
      ${field("Warn me", "before something gets blocked", `${sw(false, 'data-k="warn-on" aria-label="Warn me before something gets blocked"')}${stepper(5, "min before", { min: WARN_MIN, max: WARN_MAX, label: "Minutes before" })}`)}
      ${field("While I'm using it, remind me again every", "until it's blocked (Off, 30s, 2 min)", `<input class="input nt-repeat" data-k="repeat" autocomplete="off" spellcheck="false" aria-label="Remind me again every">`)}
      <p class="nt-err" data-k="repeat-err" role="alert"></p>
      ${field("Notify me when a block starts", "", sw(false, 'data-k="started" aria-label="Notify me when a block starts"'))}
    </div>
    <p class="nt-note">Blocks from the same group are announced together (&ldquo;Night schedule starts in 5 min: ...&rdquo;).</p>
  </section>`;
}

function digestCard() {
  return `<section class="card" data-card="digest">
    <header class="card-h">${kic("k-ok", "chart")}<h3>Weekly Summary</h3></header>
    <div class="card-b" style="padding:0">
      <div class="nt-digest">${sw(false, 'data-k="dg-on" aria-label="Send the weekly summary"')}<b>Every</b><select class="select select-sm" data-k="dg-day" aria-label="Day"></select><span class="muted">at</span><select class="select select-sm" data-k="dg-time" aria-label="Time"></select>${help("digest", "the weekly summary")}</div>
      ${helpRow("digest")}
    </div>
  </section>`;
}

// ---------------------------------------------------------------- the page

let root = null, page = null;
let data = null, built = false, pending = false;     // pending: the draft has unsaved edits
let lastRepeat = "Off";
const q = (k) => root.querySelector(`[data-k="${k}"]`);
const focused = (node) => node === document.activeElement;

export function mount(host, p) {
  page = p;
  host.innerHTML = `${STYLE}<div class="page nt"><div class="page-h"><h1>Notifications</h1><button class="btn" type="button" data-act="test">${ic("bell")}Send a test</button></div>
    ${alertsCard()}
    <div class="grid-2">${warnCard()}${digestCard()}</div></div>`;
  root = host;
  root.addEventListener("change", onChange);
  root.addEventListener("input", onInput);
  root.addEventListener("focusout", onFocusOut);
  root.addEventListener("click", onClick);
  root.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches('[data-k="repeat"]')) e.target.blur(); });
  root.addEventListener("seg", (e) => { if (e.detail.name === "nfmt") draftSetSetting("notify.format", e.detail.value, 0); });
}

/** The seven reason cells and message rows, from the reasons Python sent - once. */
function build(d) {
  q("cells").innerHTML = d.reasons.map((r) => `<label class="check nt-cell"><input type="checkbox" data-reason="${esc(r.key)}"><span class="ellip">${esc(r.label)}</span></label>`).join("");
  q("msgs").innerHTML = d.reasons.map((r) => `<div class="nt-msg"><label for="nt-m-${esc(r.key)}">${esc(r.label)}</label><input class="input" id="nt-m-${esc(r.key)}" data-msg="${esc(r.key)}" maxlength="500" autocomplete="off"><button class="btn btn-sm" type="button" data-act="reset-msg" data-reason="${esc(r.key)}">Reset</button></div>`).join("")
    + `<p class="muted small">Placeholders: ${esc(PLACEHOLDERS)}</p>`;
  built = true;
}

function setOptions(select, options, value) {
  const sig = JSON.stringify(options);
  if (select.dataset.sig !== sig) {
    select.dataset.sig = sig;
    select.replaceChildren(...options.map(([v, label]) => { const o = document.createElement("option"); o.value = v; o.textContent = label; return o; }));
  }
  if (select.value !== value) select.value = value;
}

function fill(d) {
  data = d;
  pending = !!d.draft.dirty;
  if (!built) build(d);
  for (const r of d.reasons) {
    const box = root.querySelector(`[data-reason="${r.key}"]:not(button)`);
    if (box.checked !== !!d.enabled[r.key]) box.checked = !!d.enabled[r.key];
    const msg = root.querySelector(`[data-msg="${r.key}"]`);
    if (!focused(msg) && msg.value !== d.messages[r.key]) msg.value = d.messages[r.key];
  }
  setOptions(q("cooldown"), menuOptions(d.cooldown_options, (m) => `${m} min`), String(d.cooldown));
  const fmt = q("cooldown").closest(".card").querySelector('[data-seg="nfmt"]');
  fmt.replaceChildren(...d.formats.map((f) => { const b = document.createElement("button"); b.type = "button"; b.dataset.act = "seg"; b.dataset.v = f.key; b.textContent = f.label; b.setAttribute("aria-pressed", String(f.key === d.format)); return b; }));
  const w = d.warn;
  if (q("warn-on").checked !== w.enabled) q("warn-on").checked = w.enabled;
  const mins = root.querySelector('[data-card="warn"] .stepper input');
  if (!focused(mins) && mins.value !== String(w.minutes)) mins.value = String(w.minutes);
  const rep = q("repeat");
  lastRepeat = w.repeat_text;
  if (!focused(rep) && rep.value !== w.repeat_text) rep.value = w.repeat_text;
  q("repeat-err").textContent = "";
  if (q("started").checked !== d.started) q("started").checked = d.started;
  const g = d.digest;
  if (q("dg-on").checked !== g.enabled) q("dg-on").checked = g.enabled;
  setOptions(q("dg-day"), g.days.map((name, i) => [String(i), name]), String(g.day));
  setOptions(q("dg-time"), g.times.map((t) => [t, t]), g.time);
}

export async function load() {
  fill(await ld.call("notifications_get"));
}

export function show() {
  if (data) page.reload();                      // (another page may have changed the draft meanwhile)
}

export function hide() {
  flushSettings();                              // a setting still waiting for its 400 ms goes now: leaving must not lose it
}

export function patch(delta) {
  const p = delta.payload;
  if (delta.event !== "draft" || !p) return;
  const was = pending;
  pending = !!p.dirty;
  if (p.what === "discarded" || (was && !pending)) page.reload();      // Discard (or Save): the boxes show what is saved
}

// ---------------------------------------------------------------- what you do

async function saveDigest() {
  try {
    const r = await ld.call("digest_set", q("dg-on").checked, +q("dg-day").value, q("dg-time").value);
    data.digest = { ...data.digest, ...r };
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
    page.reload();
  }
}

let repeatSeq = 0;                                  // only the newest answer may change the box (answers can overtake each other)
let repeatDone = 0, repeatFailed = 0;               // the newest call whose answer was taken as "saved", and the newest that failed

async function commitRepeat() {
  const box = q("repeat");
  const text = box.value;
  if (text.trim() === lastRepeat) { box.value = lastRepeat; return; }
  const mine = ++repeatSeq;
  try {
    const r = await ld.call("notify_repeat_set", text);
    applyState(r);
    if (mine > repeatDone) { repeatDone = mine; lastRepeat = r.text; }       // (an older answer still says what is saved)
    if (mine !== repeatSeq) {
      if (repeatFailed === repeatSeq && !focused(box)) box.value = lastRepeat;   // (the newer one failed before this answer came)
      return;
    }
    if (!focused(box)) box.value = r.text;                                   // (never over something being typed right now)
    q("repeat-err").textContent = "";
  } catch (err) {
    if (mine !== repeatSeq) return;
    repeatFailed = mine;
    q("repeat-err").textContent = (err && err.message) || "Write a time like 30s or 2 min, or Off.";
    if (!focused(box)) box.value = lastRepeat;
  }
}

function onChange(e) {
  const t = e.target;
  const reason = t.dataset.reason;
  if (reason && t.matches("input")) return draftSetSetting(`notify.enabled.${reason}`, flagValue(t.checked), 0);
  switch (t.dataset.k) {
    case "cooldown": return draftSetSetting("notify.cooldown_min", t.value, 0);
    case "warn-on": return draftSetSetting("notify.warn.enabled", flagValue(t.checked), 0);
    case "started": return draftSetSetting("notify.started.enabled", flagValue(t.checked), 0);
    case "repeat": return commitRepeat();
    case "dg-on": case "dg-day": case "dg-time": return saveDigest();
    default:
  }
}

function onInput(e) {
  const t = e.target;
  if (t.dataset.msg) {                                                  // a message box: 400 ms after the last key
    const r = data.reasons.find((x) => x.key === t.dataset.msg);
    draftSetSetting(`notify.msg.${t.dataset.msg}`, messageValue(t.value, r.default));
  } else if (t.closest('[data-card="warn"] .stepper')) {                // the minutes before
    const n = warnMinutes(t.value);
    if (n !== null) draftSetSetting("notify.warn.minutes", String(n));
  }
}

function onFocusOut(e) {
  const t = e.target;
  if (t.dataset && t.dataset.msg) {
    const r = data.reasons.find((x) => x.key === t.dataset.msg);
    if (!t.value.trim()) t.value = r.default;                           // an empty box means the default message
    flushSettings();
  } else if (t.closest && t.closest('[data-card="warn"] .stepper')) {
    const n = warnMinutes(t.value);
    t.value = String(n === null ? data.warn.minutes : n);
    flushSettings();
  }
}

async function onClick(e) {
  const b = e.target.closest("[data-act]");
  if (!b || b.dataset.act === "seg" || b.dataset.act === "step" || b.dataset.act === "toggle-press") return;
  switch (b.dataset.act) {
    case "messages": {
      const open = b.getAttribute("aria-expanded") !== "true";
      b.setAttribute("aria-expanded", String(open));
      q("msgs").hidden = !open;
      break;
    }
    case "reset-msg": {
      const r = data.reasons.find((x) => x.key === b.dataset.reason);
      root.querySelector(`[data-msg="${r.key}"]`).value = r.default;
      draftSetSetting(`notify.msg.${r.key}`, r.default, 0);
      break;
    }
    case "help": {
      const open = b.getAttribute("aria-expanded") !== "true";
      b.setAttribute("aria-expanded", String(open));
      root.querySelector(`[data-helptext="${b.dataset.help}"]`).hidden = !open;
      break;
    }
    case "test": {
      b.disabled = true;
      try {
        const r = await ld.call("notify_test");
        toast(r.sent ? "Test notice sent - look in the corner or in Windows' notifications." : "Held back: a mode that mutes notifications is on, or your blocks are paused with silence.");
      } catch (err) {
        toast((err && err.message) || "That didn't work.");
      } finally {
        b.disabled = false;
      }
      break;
    }
    default:
  }
}
