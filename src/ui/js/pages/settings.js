// Settings page (BLUEPRINT 3.10): the REAL options of the old window - Appearance (theme, accent, interface size, daily
// goal, Reduce motion), When limits reset, Categories, Emergency unlock, Backup & export, and the switch that picks the
// new window. The mock's General / Updates / Protection / Data content (start with Windows, language, channel, ...) has no
// backend and is left out.
//
// The look applies live, with no reload and no restart (js/shell.js setLook). Python decides everything that loosens and
// this file never compares old with new: moving the reset back and starting a fresh day now, more / longer emergency
// unlocks and every import come back as {needs_challenge, ticket}, and `guarded()` opens the challenge. After a refused or
// cancelled change the page reloads, so a control snaps back to what is saved. The page never sends a path: Export, Import
// and the CSV open the owner's own native dialog in Python.
//
// Page contract: mount() builds the static structure once, load() = one Api call (settings_get) fills it. The emergency
// kinds are keyed rows, reused. Showing the page again reloads it, so the uses-left meters are never stale.
import { ld } from "../bridge.js";
import { CANCELLED, guarded } from "../challenge.js";
import { loadCategories, colorOf, openCategoryEditor } from "../categories.js";
import { openDialog } from "../dialog.js";
import { refreshStatus, setLook } from "../shell.js";
import { applyLook } from "../theme.js";
import { confirmButton, el, esc, field, ic, kic, seg, sw, toast, tp } from "../ui.js";

export const events = ["status"];

export const THEME_CHOICES = [["dark", "Dark"], ["amoled", "AMOLED"], ["light", "Light"], ["system", "System"]];
export const SCALE_CHOICES = [["auto", "Auto"], ["100", "100%"], ["90", "90%"], ["80", "80%"], ["70", "70%"]];
export const PER_CHOICES = [["day", "per day"], ["week", "per week"], ["month", "per month"]];

// ---------------------------------------------------------------- plain helpers (node tests)

/** The Locked strip's words for an Anti-Bypass status {status: free | phrase | waiting | closed}; null = no strip. */
export function lockView(a) {
  if (!a || !a.status || a.status === "free") return null;
  const closed = a.status === "closed";
  return { closed, text: closed ? "- only during the allowed hours" : "- changes that loosen a block need the challenge" };
}

/** Is `accent` one of the eight swatches? (anything else is the Custom colour) */
export const accentIsPreset = (accent, accents) => Object.values(accents || {}).some((c) => String(c).toUpperCase() === String(accent).toUpperCase());

/** The uses meter of a kind: one bar per use, "left" of them still lit. */
export const meterBars = (left, allowed) => Array.from({ length: allowed }, (_, i) => i < left);

/** "3 of 3 left this week · resets Mon 12 Oct 00:00" (what the old window said). */
export const kindMeterText = (k) => `${k.left} of ${k.allowed} left ${k.per_text} · resets ${k.resets_text}`;

/** "7" + "5" -> "07:05"; both empty -> "" (Python then says "The time must look like 04:00."). */
export function normTime(h, m) {
  h = String(h == null ? "" : h).trim();
  m = String(m == null ? "" : m).trim();
  const pad = (n) => String(n).padStart(2, "0");
  return h || m ? `${pad(h || "0")}:${pad(m || "0")}` : "";
}

/** The emergency settings as emergency_settings_save wants them, from the rows' values (a row without `id` is new). */
export function emergencyConfig(enabled, kinds) {
  return {
    enabled: !!enabled,
    tiers: kinds.map((k) => ({ ...(k.id ? { id: k.id } : {}), uses: Number(k.uses), per: k.per, minutes: Number(k.minutes) })),
  };
}

export const goalLabel = (hours) => (hours ? `${hours} h` : "Off");

// ---------------------------------------------------------------- structure

const STYLE = `<style>
.set .set-pad { padding: var(--sp-3) var(--sp-4); }
.set .set-note { padding: 0 var(--sp-4) var(--sp-4); color: var(--text-2); font-size: var(--fs-sm); text-wrap: pretty; }
.set .set-err { color: var(--k-block); font-size: var(--fs-sm); padding: 0 var(--sp-4) var(--sp-3); }
.set .set-err:empty, .set .set-msg:empty { display: none; }
.set .set-msg { padding: 0 var(--sp-4) var(--sp-4); font-size: var(--fs-sm); color: var(--ok); text-wrap: pretty; overflow-wrap: anywhere; }
.set .set-msg.bad { color: var(--k-block); }
.set .set-cats { display: flex; flex-wrap: wrap; gap: var(--sp-2) var(--sp-4); align-items: center; }
.set .set-cat { display: inline-flex; align-items: center; gap: var(--sp-2); }
.set .set-cat i { width: calc(12px * var(--s)); height: calc(12px * var(--s)); border-radius: 3px; background: var(--c); }
.set .field-c:has(.swatches) { flex-basis: 100%; margin-left: 0; justify-content: flex-start; }
.set .set-custom { position: relative; background: conic-gradient(#e0443a, #e8b923, #4cae56, #2fb5c9, #4a63e0, #b24ac9, #e0443a) !important; }
.set .set-custom.has { background: var(--g) !important; }
.set .set-custom input { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; pointer-events: none; border: 0; padding: 0; }
.set .set-kinds { display: grid; gap: var(--sp-2); }
.set .set-kind { border: 1px solid var(--line-strong); border-radius: var(--r-md); padding: var(--sp-2) var(--sp-3); display: grid; gap: var(--sp-2); background: var(--surface-2); }
.set .set-kind-top { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.set .set-kind-top .grow { flex: 1; }
.set .set-meter { display: flex; align-items: center; gap: var(--sp-2); font-size: var(--fs-sm); color: var(--text-2); flex-wrap: wrap; }
.set .set-new { border: 1px dashed var(--line-strong); border-radius: var(--r-md); padding: var(--sp-3); display: grid; gap: var(--sp-2); }
.set .set-reset .tp { flex: none; }
.set .set-ap-note { color: var(--text-2); font-size: var(--fs-sm); }
.set .set-lock { border: 1px solid var(--line); border-radius: var(--r-lg); }
</style>`;

const NOTE = {
  reset: "Time limits and opening limits start over at this time every day (weekly limits on Monday, monthly ones on the 1st, at the same time). Screen-time stats keep normal days.",
  goal: "Drawn as the dashed line on every chart. Not part of blocking, so it stays editable even while Anti-Bypass is locked.",
  cats: "Screen time is split into Productive / Neutral / Distracting (plus any of your own, each with a colour). Modes that block \"Distracting\" follow these. Set an app's or site's category on Screen Time > Apps / Websites.",
  emergency: "The \"Emergency unlock\" button (Blocking > Overview) unblocks the sites / apps you pick for a while. Unlocking several at once counts as one use. With more than one kind (e.g. 3 a week for 5 min and 1 a month for 60 min) you pick which kind to use; each counts its own uses.",
  emergencyRule: "Fewer, shorter, per day to per week or month, or removing a kind saves at once. More, longer, any other period, a new kind or turning it on needs the challenge.",
  backup: "A backup has everything you set up: blocked sites and apps with their rules, groups, categories, modes, reminders and settings. Importing one replaces yours - it can loosen blocks, so it asks for the challenge first.",
  engine: "Lockdown opens in the new window the next time it starts. Switch it off to come back to the old window.",
};

const sel = (k, label, extra = "") => `<select class="select select-sm" data-k="${k}" aria-label="${esc(label)}" ${extra}></select>`;

function appearanceCard() {
  return `<section class="card" data-card="look">
    <header class="card-h">${kic("k-brand", "sparkle")}<h3>Appearance</h3><span class="muted small">Applies at once - no restart.</span></header>
    <div class="card-b" style="padding:0">
      ${field("Theme", "", seg("theme", THEME_CHOICES, "dark"))}
      ${field("Accent colour", "", `<span class="swatches" data-k="swatches"></span>`)}
      ${field("Interface size", "How big everything is drawn.", sel("scale", "Interface size"))}
      ${field("Daily goal", NOTE.goal, sel("goal", "Daily goal"))}
      ${field("Reduce motion", "No transitions or animations.", sw(false, 'data-k="motion" aria-label="Reduce motion"'))}
    </div></section>`;
}

function resetCard() {
  return `<section class="card" data-card="reset">
    <header class="card-h">${kic("k-limit", "clock")}<h3>When limits reset</h3></header>
    <div class="card-b" style="padding:0">
      ${field("Reset at", NOTE.reset, `<span class="set-reset hstack">${tp("00:00", "Reset at")}<span data-k="reset-slot"></span></span><span class="muted small" data-k="reset-info"></span>`)}
      <p class="set-err" data-k="reset-err" role="alert"></p>
      <p class="set-note" data-k="reset-note" hidden></p>
    </div></section>`;
}

function categoriesCard() {
  return `<section class="card" data-card="cats">
    <header class="card-h">${kic("k-protect", "layers")}<h3>Categories</h3><button class="btn btn-sm" type="button" data-act="cats">Manage categories…</button></header>
    <div class="card-b"><div class="set-cats" data-k="cats"></div></div>
    <p class="set-note">${esc(NOTE.cats)}</p>
  </section>`;
}

function emergencyCard() {
  const kindSelects = (p) => `${sel(`${p}uses`, "Uses")}${sel(`${p}per`, "Period")}<span class="muted">·</span>${sel(`${p}minutes`, "Minutes")}`;
  return `<section class="card" data-card="emergency">
    <header class="card-h">${kic("k-emerg", "lifebuoy")}<h3>Emergency unlock</h3></header>
    <div class="card-b" style="padding:0">
      ${field("Allow emergency unlocks", NOTE.emergency, sw(false, 'data-k="em-on" aria-label="Allow emergency unlocks"'))}
      <div class="set-pad"><div class="set-kinds" data-k="kinds"></div>
        <p class="muted small" data-k="kinds-empty" hidden>No kinds - add one.</p>
        <div class="set-new" data-k="em-new" hidden><b>New kind</b><div class="set-kind-top">${kindSelects("n-")}</div>
          <div class="btn-row"><button class="btn btn-sm btn-primary" type="button" data-act="em-add-ok">Add</button><button class="btn btn-sm" type="button" data-act="em-add-cancel">Cancel</button></div></div>
        <div style="margin-top:var(--sp-2)"><button class="btn btn-sm" type="button" data-act="em-add" data-k="em-add">${ic("plus")}Add another kind</button></div></div>
      <p class="set-note">${esc(NOTE.emergencyRule)}</p>
    </div></section>`;
}

function backupCard() {
  return `<section class="card" data-card="backup">
    <header class="card-h">${kic("k-sched", "download")}<h3>Backup &amp; export</h3></header>
    <div class="card-b" style="padding:0">
      ${field("Settings backup", NOTE.backup, `<button class="btn btn-sm" type="button" data-act="export">${ic("download")}Export…</button><button class="btn btn-sm" type="button" data-act="import">${ic("upload")}Import…</button>`)}
      ${field("Screen time", "Every day, app and site of the last year, as a CSV file.", `<button class="btn btn-sm" type="button" data-act="csv">${ic("download")}Screen time (CSV)…</button>`)}
      <p class="set-msg" data-k="backup-msg" role="status"></p>
    </div></section>`;
}

function engineCard() {
  return `<section class="card" data-card="engine">
    <header class="card-h">${kic("k-mode", "sparkle")}<h3>New window</h3><span class="chip chip-soft">Preview</span></header>
    <div class="card-b" style="padding:0">
      ${field("Use the new window (preview)", NOTE.engine, sw(false, 'data-k="engine" aria-label="Use the new window (preview)"'))}
      <p class="set-note" data-k="engine-note" role="status"></p>
    </div></section>`;
}

// ---------------------------------------------------------------- the page

let root = null, page = null;
let data = null, built = false, loaded = false;
let resetEdited = false, newKindOpen = false;
let cats = [];
const rows = new Map();                        // kind id -> {id, node, refs, sig}
const q = (k) => root.querySelector(`[data-k="${k}"]`);
const focused = (node) => node === document.activeElement;

export function mount(host, p) {
  page = p;
  host.innerHTML = `${STYLE}<div class="page set"><div class="page-h"><h1>Settings</h1></div>
    <div class="banner k-block set-lock" data-k="lock" hidden>${ic("shield-check")}<span class="grow"><b>Locked</b> <span class="muted"></span></span><button class="btn btn-sm btn-primary" type="button" data-act="unlock">Unlock to edit</button></div>
    <div class="grid-2"><div class="stack">${appearanceCard()}${resetCard()}${categoriesCard()}</div>
      <div class="stack">${emergencyCard()}${backupCard()}${engineCard()}</div></div></div>`;
  root = host;
  q("reset-slot").replaceWith(confirmButton({ label: "Change", confirmLabel: "Confirm change", onConfirm: changeReset, cls: "btn btn-sm", ms: 4000 }));
  root.addEventListener("change", onChange);
  root.addEventListener("input", (e) => { if (e.target.closest(".set-reset .tp")) resetEdited = true; });
  root.addEventListener("click", onClick);
  root.addEventListener("seg", (e) => { if (e.detail.name === "theme") lookChange({ theme: e.detail.value }); });
  window.addEventListener("ld:categories-changed", () => loadCategories().then(renderCategories).catch(() => {}));
}

// ---------------------------------------------------------------- the Locked strip

function renderLock() {
  const v = lockView(ld.status.antibypass);
  const strip = q("lock");
  strip.hidden = !v;
  if (!v) return;
  const text = strip.querySelector(".muted");
  if (text.textContent !== v.text) text.textContent = v.text;
  strip.querySelector("button").disabled = v.closed;
}

async function unlockToEdit() {
  try {
    await guarded(ld.call("antibypass_unlock_to_edit"));
    await refreshStatus();
    renderLock();
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
  }
}

// ---------------------------------------------------------------- filling the page

function setOptions(select, options, value) {
  const sig = JSON.stringify(options);
  if (select.dataset.sig !== sig) {
    select.dataset.sig = sig;
    select.replaceChildren(...options.map(([v, label]) => { const o = document.createElement("option"); o.value = String(v); o.textContent = label; return o; }));
  }
  if (select.value !== String(value)) select.value = String(value);
}

/** The ring on the chosen swatch - or on the Custom one, which shows the colour when it is none of the eight. */
function paintAccent(accent, accents) {
  const sw_ = q("swatches");
  const custom = !accentIsPreset(accent, accents);
  for (const b of sw_.querySelectorAll("[data-color]")) b.setAttribute("aria-pressed", String(b.dataset.color.toUpperCase() === accent.toUpperCase()));
  const cb = sw_.querySelector(".set-custom");
  cb.classList.toggle("has", custom);
  cb.setAttribute("aria-pressed", String(custom));
  if (custom) cb.style.setProperty("--g", accent);
  const input = q("accent-input");
  if (input.value.toLowerCase() !== accent.toLowerCase()) input.value = accent.toLowerCase();
}

function fillLook(d) {
  const look = d.look;
  for (const b of root.querySelectorAll('[data-seg="theme"] button')) b.setAttribute("aria-pressed", String(b.dataset.v === look.theme));
  const sw_ = q("swatches");
  if (!sw_.children.length) {
    sw_.replaceChildren(...Object.entries(look.accents).map(([name, hex]) => {
      const b = el(`<button type="button" data-act="accent" aria-label="${esc(name)}" title="${esc(name)}"></button>`);
      b.dataset.color = hex;
      b.style.setProperty("--g", hex);
      return b;
    }), el(`<button type="button" class="set-custom" data-act="accent-custom" aria-label="Custom colour" title="Custom colour"><input type="color" data-k="accent-input" tabindex="-1" aria-hidden="true"></button>`));
  }
  paintAccent(look.accent, look.accents);
  setOptions(q("scale"), SCALE_CHOICES, look.scale);
  setOptions(q("goal"), d.goal_options.map((h) => [h, goalLabel(h)]), d.goal);
  if (q("motion").checked !== d.motion) q("motion").checked = d.motion;
}

function fillReset(r) {
  const box = q("reset-info").closest(".field").querySelector(".tp");
  if (!resetEdited && !box.contains(document.activeElement)) {
    const [h, m] = r.time.split(":");
    box.querySelector(".tp-h").value = h;
    box.querySelector(".tp-m").value = m;
  }
  q("reset-info").textContent = `ends ${r.ends_text}`;
  const note = q("reset-note");
  note.hidden = !r.carry_note;
  if (note.textContent !== r.carry_note) note.textContent = r.carry_note;
}

function renderCategories(list) {
  cats = list || cats;
  const box = q("cats");
  box.replaceChildren(...cats.map((c) => {
    const n = el(`<span class="set-cat"><i></i><span></span></span>`);
    n.querySelector("i").style.setProperty("--c", colorOf(c));
    n.lastElementChild.textContent = c.name;
    return n;
  }));
}

function kindNode(k, e) {
  const node = el(`<div class="set-kind" data-id="${esc(k.id)}"><div class="set-kind-top">
    ${sel("k-uses", "Uses")}${sel("k-per", "Period")}<span class="muted">·</span>${sel("k-minutes", "Minutes")}<span class="grow"></span><span data-k="k-x"></span></div>
    <div class="set-meter"><span class="uses" data-k="k-bars"></span><span data-k="k-text"></span></div></div>`);
  const x = confirmButton({ label: "×", confirmLabel: "Remove?", onConfirm: () => removeKind(k.id), cls: "btn btn-sm", title: "Remove this kind" });
  node.querySelector('[data-k="k-x"]').replaceWith(x);
  return { id: k.id, node, sig: "", bars: node.querySelector('[data-k="k-bars"]'), text: node.querySelector('[data-k="k-text"]') };
}

function fillKindSelects(get, e, k) {
  setOptions(get("uses"), Array.from({ length: e.max_uses }, (_, i) => [i + 1, String(i + 1)]), k.uses);
  setOptions(get("per"), PER_CHOICES, k.per);
  setOptions(get("minutes"), e.minute_options.map((m) => [m, `${m} min`]), k.minutes);
}

function fillEmergency(e) {
  if (q("em-on").checked !== e.enabled) q("em-on").checked = e.enabled;
  const list = q("kinds");
  const seen = new Set();
  e.kinds.forEach((k, i) => {
    let r = rows.get(k.id);
    if (!r) { r = kindNode(k, e); rows.set(k.id, r); }
    if (list.children[i] !== r.node) list.insertBefore(r.node, list.children[i] || null);
    const get = (f) => r.node.querySelector(`[data-k="k-${f}"]`);
    fillKindSelects(get, e, k);                  // (a menu commits when you pick: nothing half-typed to protect, and a cancelled change must snap back)
    const sig = `${k.left}/${k.allowed}`;
    if (r.sig !== sig) {
      r.sig = sig;
      r.bars.replaceChildren(...meterBars(k.left, k.allowed).map((lit) => el(`<i class="${lit ? "" : "used"}"></i>`)));
    }
    const text = kindMeterText(k);
    if (r.text.textContent !== text) r.text.textContent = text;
    seen.add(k.id);
  });
  for (const [id, r] of [...rows]) if (!seen.has(id)) { r.node.remove(); rows.delete(id); }
  q("kinds-empty").hidden = e.kinds.length > 0;
  q("em-add").hidden = e.kinds.length >= e.max_kinds || newKindOpen;
  if (!newKindOpen) fillKindSelects((f) => q(`n-${f}`), e, e.new_kind);
}

function fill(d) {
  data = d;
  fillLook(d);
  fillReset(d.reset);
  renderCategories(d.categories);
  fillEmergency(d.emergency);
  const web = d.engine === "web";
  if (q("engine").checked !== web) q("engine").checked = web;
  built = true;
}

export async function load() {
  fill(await ld.call("settings_get"));
  renderLock();
  loaded = true;
}

export function show() {
  if (!built) return;
  renderLock();
  page.reload();                               // the meters count from now: never show yesterday's uses
}

export function patch(delta) {
  if (delta.event !== "status") return;
  renderLock();
  if (delta.payload && "emergency" in delta.payload) page.reload();
}

// ---------------------------------------------------------------- the look

async function lookChange(change) {
  try {
    await setLook(change);                     // applies at once; saves; puts the old look back and toasts if Python refuses
    if (change.accent !== undefined) paintAccent(change.accent, data.look.accents);
    if (change.theme !== undefined) renderCategories();      // (category colours differ on the light theme)
  } catch (_) {
    page.reload();
  }
}

async function setMotion(on) {
  applyLook({ motion: on });
  try {
    await ld.call("ui_setting", "ui.reduce_motion", on ? "1" : "0");
  } catch (err) {
    applyLook({ motion: !on });
    q("motion").checked = !on;
    toast((err && err.message) || "That didn't work.");
  }
}

async function setGoal(hours) {
  try {
    await ld.call("set_goal", hours);
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
    page.reload();
  }
}

async function setEngine(on) {
  const note = q("engine-note");
  try {
    await ld.call("ui_engine_set", on ? "web" : "legacy");
    note.textContent = on ? "The new window opens the next time Lockdown starts." : "The old window opens the next time Lockdown starts.";
  } catch (err) {
    q("engine").checked = !on;
    toast((err && err.message) || "That didn't work.");
  }
}

// ---------------------------------------------------------------- when limits reset

function resetError(text) {
  q("reset-err").textContent = text || "";
}

async function applyReset(time, startNow) {
  try {
    await guarded(ld.call("reset_change_apply", time, startNow));
    resetEdited = false;
  } catch (err) {
    resetError((err && err.message) || "That didn't work.");
  }
  page.reload();
}

async function changeReset() {
  resetError("");
  const box = q("reset-info").closest(".field").querySelector(".tp");
  const time = normTime(box.querySelector(".tp-h").value, box.querySelector(".tp-m").value);
  let preview;
  try {
    preview = await ld.call("reset_change_preview", time);
  } catch (err) {
    resetError((err && err.message) || "That didn't work.");
    return;
  }
  if (!preview.looser) return applyReset(preview.time, false);        // a later time only makes a day longer: free
  const from = `From ${preview.end_text}`;
  const h = openDialog({
    key: "reset", title: `Move the reset back to ${preview.time}`,
    body: `<p>The limit day you are in runs until <b>${esc(preview.end_text)}</b>.</p>
      <p><b>Start now</b>: it ends at once and today's limits start over - Anti-Bypass asks first.</p>
      <p><b>${esc(from)}</b>: ${esc(preview.time)} takes over when it ends, and nothing starts over.</p>`,
    buttons: [{ label: "Cancel", value: "cancel" }, { label: from, value: "from" }, { label: "Start now", kind: "primary", value: "now" }],
  });
  if (!h) return;
  const choice = await h.done;
  if (choice === "now") return applyReset(preview.time, true);
  if (choice === "from") return applyReset(preview.time, false);
  page.reload();
}

// ---------------------------------------------------------------- emergency unlock

function readKinds() {
  return [...rows.values()].map((r) => {
    const get = (f) => r.node.querySelector(`[data-k="k-${f}"]`).value;
    return { id: r.id, uses: get("uses"), per: get("per"), minutes: get("minutes") };
  });
}

async function saveEmergency(config, done) {
  try {
    const r = await guarded(ld.call("emergency_settings_save", config));
    if (r !== CANCELLED && done) done();
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
  }
  page.reload();                               // what is saved is the truth: a cancelled challenge puts the card back
}

const saveCurrent = () => saveEmergency(emergencyConfig(q("em-on").checked, readKinds()));

function removeKind(id) {
  saveEmergency(emergencyConfig(q("em-on").checked, readKinds().filter((k) => k.id !== id)));
}

function openNewKind(open) {
  newKindOpen = open;
  q("em-new").hidden = !open;
  q("em-add").hidden = open || rows.size >= data.emergency.max_kinds;
  if (open) fillKindSelects((f) => q(`n-${f}`), data.emergency, data.emergency.new_kind);
}

function addKind() {
  const get = (f) => q(`n-${f}`).value;
  const kinds = [...readKinds(), { uses: get("uses"), per: get("per"), minutes: get("minutes") }];
  saveEmergency(emergencyConfig(q("em-on").checked, kinds), () => openNewKind(false));
}

// ---------------------------------------------------------------- backup and export

function backupNote(text, bad = false) {
  const n = q("backup-msg");
  n.textContent = text;
  n.classList.toggle("bad", bad);
}

async function busy(button, fn) {
  button.disabled = true;
  try {
    await fn();
  } catch (err) {
    backupNote((err && err.message) || "That didn't work.", true);
  } finally {
    button.disabled = false;
  }
}

async function exportBackup() {
  const r = await ld.call("backup_export");
  if (!r.cancelled) backupNote(`Saved to ${r.path}`);
}

async function exportCsv() {
  const r = await ld.call("screentime_export_csv");
  if (!r.cancelled) backupNote(`${r.rows} rows saved to ${r.path}`);
}

async function importBackup() {
  const r = await ld.call("backup_import_pick");
  if (r.cancelled) return;
  const lines = r.lines.map((l) => `<li>${esc(l)}</li>`).join("");
  const h = openDialog({
    key: "import", title: "Import these settings?",
    body: `<p>Importing <b>${esc(r.name)}</b> replaces your current setup with the backup. What changes:</p><ul class="changelog small" data-k="import-lines">${lines}</ul>`,
    buttons: [{ label: "Cancel", value: false }, { label: "Import", kind: "primary", value: true }],
  });
  if (!h || (await h.done) !== true) return;
  const done = await guarded(ld.call("backup_import_apply", r.token));
  if (done === CANCELLED) return;
  backupNote(`Imported ${done.items} blocked sites / apps and your settings.`);
  try { applyLook(await ld.call("get_theme")); } catch (_) { /* the next start has it */ }
  window.dispatchEvent(new CustomEvent("ld:categories-changed"));
  page.reload();
}

// ---------------------------------------------------------------- what you do

function onChange(e) {
  const t = e.target;
  const k = t.dataset.k;
  if (k === "scale") return lookChange({ scale: t.value });
  if (k === "goal") return setGoal(Number(t.value));
  if (k === "motion") return setMotion(t.checked);
  if (k === "engine") return setEngine(t.checked);
  if (k === "accent-input") return lookChange({ accent: t.value });
  if (k === "em-on" || (k && k.startsWith("k-"))) return saveCurrent();
}

function onClick(e) {
  const b = e.target.closest("[data-act]");
  if (!b || ["seg", "step", "toggle-press"].includes(b.dataset.act)) return;
  switch (b.dataset.act) {
    case "accent": return lookChange({ accent: b.dataset.color });
    case "accent-custom": {
      const input = q("accent-input");
      try { input.showPicker(); } catch (_) { input.click(); }
      break;
    }
    case "unlock": return unlockToEdit();
    case "cats": return openCategoryEditor();
    case "em-add": return openNewKind(true);
    case "em-add-cancel": return openNewKind(false);
    case "em-add-ok": return addKind();
    case "export": return busy(b, exportBackup);
    case "import": return busy(b, importBackup);
    case "csv": return busy(b, exportCsv);
    default:
  }
}
