// Anti-Bypass page (BLUEPRINT 3.4): the lock banner, the challenges, Pause my blocks, Turn Lockdown off and two static
// cards. Python decides everything that matters: what the page sends is the changed settings only (a switch, the list
// of challenge steps, the phrase, the wait as TEXT, the hours), and Python says whether that weakens Anti-Bypass (-> a challenge ticket that
// `guarded()` turns into the challenge dialog), needs a yes first (-> {needs_confirm, line}) or is just saved. This file
// never compares old with new.
//
// Page contract: mount() builds the structure once, load() = one Api call (antibypass_get) that fills it; the banner's
// countdown runs from end timestamps on the page's own clock (page.every, 1 s, only while visible) - no Api call per
// tick; when a countdown reaches its end the page asks once for the new state. `status` pushes (a pause or unlock
// that started elsewhere) reload the page only when they differ from what it shows.
import { ld } from "../bridge.js";
import { CANCELLED, clockOf, countdownText, guarded, parseNaive, whenText } from "../challenge.js";
import { openDialog } from "../dialog.js";
import { stepListEditor, stepsLooser } from "../steplist.js";
import { days as dayChips, esc, field, fmt, ic, kic, seg, setText, sw, toast, tpRange } from "../ui.js";

export const freshMs = 5000;
export const events = ["status"];

// What the "What needs the challenge" card lists (the old window's words).
export const PROTECTS = [
  "Removing a blocked site / app / group, or taking sites or members out of it",
  "Weaker rules: higher or no limits, other blocked hours, a shorter temporary block, gentler app blocking",
  "Switching a protection list off, or allowing a site it blocks",
  "Turning SafeSearch or the blocked-words check off, removing your words, adding exceptions",
  "Emergency unlock: turning it on, a new kind, more uses, longer, a shorter period",
  "Pausing your blocks (Pause my blocks) - resuming early is always free",
  "Closing the tray icon and window (tray Exit - blocking keeps running)",
  "Weakening Anti-Bypass itself (these settings)",
];
const KEEPING = [
  "Tray Exit only closes the icon and the window - blocking, word filters and reminders keep running. If Lockdown is killed (e.g. in Task Manager) it comes back within a minute.",
  "The service comes back within a minute if it's stopped.",
  "Changing the Windows clock does nothing; a new time zone counts only after 24 hours.",
  "Uninstalling Lockdown asks for the challenge too.",
];
const HELP = {
  own: "Set a phrase only you know (e.g. a long sentence). You still type it exactly each time - no pasting. With the 3×3 grid it's split into words by the spaces. While it's switched on, the length and the numbers & capitals option don't apply; switched off, it stays saved and a random phrase is asked. Switching it on needs the challenge (a phrase you know is easier than a fresh random one).",
  wait: "Off, or a time like 10, 1h or 1h30. Passing the challenge starts the wait instead of unlocking at once, and you come back when it ends - an easy phrase plus a long wait stops more than a hard phrase does. The wait is kept in the database, so closing Lockdown or restarting the PC doesn't skip it.",
};
const REASK_MS = 2000;                    // a countdown that ended but the new state still says the same: ask again after this

// ---------------------------------------------------------------- plain helpers (node tests)

/** "7" + "5" -> "07:05"; both empty -> "" (Python then says what is wrong). */
export function normTime(h, m) {
  h = String(h == null ? "" : h).trim();
  m = String(m == null ? "" : m).trim();
  return h || m ? `${h.padStart(2, "0") || "00"}:${m.padStart(2, "0") || "00"}` : "";
}

/** What the lock banner says for the state Python sent (`b` = antibypass_get().banner) and the seconds left to its end. */
export function bannerView(b, nowIso, leftSec) {
  const left = leftSec == null ? 0 : leftSec;
  switch (b.kind) {
    case "closed":
      return { tone: "k-block", icon: "lock", title: "Closed right now", lock: false, unlock: false, bar: null,
        text: `Changes like this are only possible ${b.hours}.${b.next_open ? ` Next chance: ${whenText(b.next_open, nowIso)}.` : ""}` };
    case "unlocked":
      return { tone: "k-ok", icon: "unlock", title: `Unlocked for ${countdownText(left)} - loosening changes are allowed`, lock: true, unlock: false,
        text: "Changes that loosen your blocks are allowed until the timer runs out.",
        bar: b.total_sec ? Math.max(0, Math.min(1, left / b.total_sec)) : 0 };
    case "waiting":
      return { tone: "k-limit", icon: "hourglass", title: `Passed - unlocks in ${countdownText(left)} (at ${clockOf(b.ends_at)})`, lock: false, unlock: false,
        text: "The unlock opens when the wait ends.",
        bar: b.total_sec ? Math.max(0, Math.min(1, 1 - left / b.total_sec)) : 0 };
    case "locked":
      return { tone: "k-block", icon: "lock", title: "Locked", text: b.summary, lock: false, unlock: true, bar: null };
    case "free":
      return { tone: "k-ok", icon: "shield-check", title: "Free inside the allowed hours", lock: false, unlock: false, bar: null,
        text: `${b.summary}${b.ends_at ? ` · until ${clockOf(b.ends_at)}` : ""}` };
    default:
      return { tone: "k-grey", icon: "shield", title: "Anti-Bypass is off", lock: false, unlock: false, bar: null,
        text: "Turn on a challenge below - then anything that loosens a block will need it first." };
  }
}

/** The pause card's one-line state; leftMin = whole minutes left (rounded up). */
export function pauseNote(p, leftMin) {
  if (!p.running) return "Your blocks are on.";
  return `Your blocks are paused until ${clockOf(p.until)}${leftMin > 0 ? ` (${fmt(leftMin)} left)` : ""}${p.silent ? " - notifications are silenced." : "."}`;
}

/** The status the shell and the pushes carry, boiled down to what this page shows (to tell "something changed"). */
export function statusSig(st) {
  const ab = st.antibypass || {};
  return [ab.status, ab.unlocked_until || "", ab.waiting_until || "", st.pause ? st.pause.until : "", st.pause ? !!st.pause.silent : "", !!st.off].join("|");
}
const sigOfPage = (d) => statusSig({ antibypass: { status: d.status, unlocked_until: d.unlocked_until, waiting_until: d.waiting_until },
  pause: d.pause.running ? { until: d.pause.until, silent: d.pause.silent } : null, off: d.lockdown.off });

// ---------------------------------------------------------------- the page

const STYLE = `<style>
.ab .ab-banner { align-items: center; flex-wrap: wrap; border: 1px solid var(--k); }
.ab .ab-bt { flex: 1 1 calc(240px * var(--s)); min-width: 0; display: grid; gap: 2px; }
.ab .ab-banner > .ic { margin-top: 0; }
.ab .ab-bar { flex: 1 1 100%; }
.ab .btn-red { border-color: var(--k-block); color: var(--k-block); }
.ab .btn-red:hover { background: var(--k-block-soft); }
.ab .ab-help { margin: 0; padding: 0 var(--sp-4) var(--sp-3); color: var(--text-2); font-size: var(--fs-sm); text-wrap: pretty; }
.ab .ab-help[hidden] { display: none; }
.ab .ab-err { margin: 0; padding: var(--sp-2) var(--sp-4) var(--sp-3); color: var(--k-block); font-size: var(--fs-sm); font-weight: var(--fw-semibold); }
.ab .ab-err:empty { display: none; }
.ab .ab-dim .field-c { opacity: .55; }
.ab .ab-wins { display: grid; gap: var(--sp-2); align-items: start; }
.ab .ab-wins [data-k="wins"] { display: grid; }
.ab .ab-sub { margin: 0; padding: var(--sp-2) var(--sp-4); background: var(--surface-2); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); color: var(--text-2); font-size: var(--fs-sm); font-weight: var(--fw-semibold); }
.ab .ab-steps { border-top: 0; }
.ab .ab-savebar { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: var(--sp-2) var(--sp-3); padding: var(--sp-3) var(--sp-4); border-top: 1px solid var(--line); }
.ab .ab-why { flex: 1 1 calc(180px * var(--s)); min-width: 0; margin: 0; color: var(--text-3); font-size: var(--fs-sm); }
.ab .ab-why.loose { color: var(--on-brand-soft); }
.ab [data-k="px"][hidden] { display: none; }
.ab .ab-ownrow .field-c { flex: 1 1 calc(300px * var(--s)); flex-wrap: nowrap; }
.ab .ab-own { flex: 1 1 auto; min-width: 0; }
.ab .ab-box { width: calc(88px * var(--s)); text-align: center; }
.ab .ab-note { margin: 0; font-weight: var(--fw-semibold); }
.ab .ab-note.ok { color: var(--ok); }
.ab .ab-pause { display: grid; gap: var(--sp-3); }
.ab .ab-line { margin: 0; padding: var(--sp-2) var(--sp-3); border-radius: var(--r-md); background: var(--surface-3); font-weight: var(--fw-semibold); }
.ab .ab-flash { animation: ab-flash 1.4s var(--ease); }
@keyframes ab-flash { from { box-shadow: 0 0 0 3px var(--brand); } to { box-shadow: var(--shadow-card); } }
.ab .ab-list { margin: 0; padding-left: var(--sp-5); display: grid; gap: var(--sp-2); color: var(--text-2); }
.ab .ab-list li::marker { color: var(--brand); }
.ab .ab-foot { margin: 0; padding: var(--sp-3) var(--sp-4); border: 1px solid var(--line); border-radius: var(--r-md); color: var(--text-2); font-size: var(--fs-sm); }
</style>`;

const help = (key, label) => `<button class="icon-btn icon-btn-sm" type="button" data-act="help" data-help="${key}" aria-expanded="false" aria-label="More about ${esc(label)}">${ic("info")}</button>`;
const helpRow = (key) => `<p class="ab-help" data-helptext="${key}" hidden>${esc(HELP[key])}</p>`;
const win = (w) => `<div class="win-row" data-win role="group">${dayChips(Array.from({ length: 7 }, (_, i) => w.days.includes(i)))}${tpRange(w.start, w.end)}<span class="grow"></span>` +
  `<button class="icon-btn icon-btn-sm" type="button" data-act="win-x" aria-label="Remove this time window">${ic("trash")}</button></div>`;

let root, page, d = null;
let clock = { base: NaN, at: 0 };            // Python's "now" when the page was loaded, and our clock then
let pauseMinutes = 60;                       // what the pause buttons have picked (null = the rest of the day)
let hoursDirty = false;                      // the time windows were edited and not saved yet
let winKey = "";                             // the windows the rows were built from
let lastWait = "";                           // the wait as last saved (what the box shows when nothing is being typed)
let inflight = "";                           // the settings change on its way to Python
let firedFor = "", firedAt = 0;              // the countdown end the page already asked about
let pendingFocus = false;
let steps = null;                            // the step-list editor (steplist.js), made with the first load (it needs Python's options)
let stepsEdited = false;                     // the list was edited and not saved yet
let seenPauseLeft = -1;

const q = (k) => root.querySelector(`[data-k="${k}"]`);
const trustedNow = () => clock.base + (performance.now() - clock.at);

export function mount(el_, pg) {
  root = el_;
  page = pg;
  root.innerHTML = `${STYLE}<div class="page ab">
    <div class="page-h"><div class="grow"><h1>Anti-Bypass</h1><p class="sub">Tightening a rule is instant. Loosening one asks for the challenge first.</p></div>
      <button class="btn" type="button" data-act="try" title="Opens the challenge as it would meet you - there is nothing to change. A practice run: passing it unlocks nothing.">${ic("keyboard")}Try the challenge</button></div>
    <section class="notice ab-banner k-grey" data-k="banner" role="status">${ic("shield")}
      <div class="ab-bt"><b data-k="b-title"></b><span class="muted small" data-k="b-text"></span></div>
      <button class="btn btn-sm" type="button" data-act="lock" hidden>${ic("lock")}Lock now</button>
      <button class="btn btn-sm btn-red" type="button" data-act="unlock" hidden>${ic("unlock")}Unlock to edit</button>
      <div class="meter ab-bar" data-k="b-meter" hidden><i data-k="b-bar" style="width:0%"></i></div></section>
    <div class="grid-2"><div class="stack">${challengeCard()}${alwaysCard()}</div>
      <div class="stack">${pauseCard()}${offCard()}${protectsCard()}${keepingCard()}</div></div></div>`;
  wire();
  page.every(tick);
}

function challengeCard() {
  return `<section class="card" data-card="challenges"><header class="card-h">${kic("k-brand", "keyboard")}<h3>Challenges</h3></header>
    <div class="card-b" style="padding:0">
      ${field("Ask a challenge", "Before anything that loosens a block - the steps below, one after another.", sw(false, 'data-k="phrase" aria-label="Ask a challenge"'))}
      <p class="ab-sub">Steps - done top to bottom</p>
      <div class="ab-steps" data-k="steps"></div>
      <div class="ab-savebar"><p class="ab-why" data-k="steps-why" role="status"></p>
        <button class="btn" type="button" data-act="undo-steps" data-k="undo-steps" disabled>Undo</button>
        <button class="btn" type="button" data-act="save-steps" data-k="save-steps" disabled>Save steps</button></div>
      <div data-k="px">
        <p class="ab-sub">Typed phrase - shown because the list has a phrase step</p>
        ${field("Numbers & CAPITALS", "Adds digits and capital letters to a random phrase - harder to type quickly.", sw(false, 'data-k="complex" aria-label="Numbers and capitals"'), "ab-cx")}
        ${field("3×3 grid", "One word at a time into a random box of a 3×3 grid - you click the box first.", sw(false, 'data-k="grid" aria-label="3 by 3 grid"'))}
        ${field("Use my own phrase", "Instead of a random one. Switched off, it stays saved.", sw(false, 'data-k="own" aria-label="Use my own phrase"'))}
        ${field("Your own phrase", "Asked while the switch above is on.",
          `<input class="input ab-own" data-k="custom" maxlength="2000" placeholder="a phrase only you know" autocomplete="off" spellcheck="false" aria-label="Your own phrase">` +
          `<button class="btn btn-sm" type="button" data-act="save-phrase">Save phrase</button>${help("own", "your own phrase")}`, "ab-ownrow")}
        ${helpRow("own")}
      </div>
      <p class="ab-sub">Also</p>
      ${field("Then wait", "Off, 10, 1h, 1h30 ... before it actually unlocks.",
        `<input class="input ab-box" data-k="wait" autocomplete="off" spellcheck="false" aria-label="Then wait">${help("wait", "the wait")}`)}
      ${helpRow("wait")}
      ${field("Only during these hours", "Outside these times nothing can be loosened at all. An end before the start means overnight.", sw(false, 'data-k="hours" aria-label="Only during these hours"'))}
      <div class="field sub ab-wins"><div data-k="wins"></div>
        <div class="hstack wrap"><button class="btn btn-sm" type="button" data-act="win-add">${ic("plus")}Add time window</button>
          <button class="btn btn-sm" type="button" data-act="save-hours" data-k="save-hours">Save hours</button></div></div>
      <p class="ab-err" data-k="err" role="alert"></p>
    </div></section>`;
}

function pauseCard() {
  return `<section class="card" data-card="pause"><header class="card-h">${kic("k-grey", "pause")}<h3>Pause my blocks</h3></header>
    <div class="card-b ab-pause">
      <p class="ab-note" data-k="p-note"></p>
      <div class="stack-sm" data-k="p-form"><div data-k="p-seg"></div>
        <label class="check"><input type="checkbox" data-k="p-silent"><span>Silence all notifications (warnings, reminders, bedtime and break alerts, pop-ups)</span></label></div>
      <div class="hstack"><button class="btn btn-primary" type="button" data-act="pause" data-k="p-start">${ic("pause")}Pause my blocks</button>
        <button class="btn btn-primary" type="button" data-act="resume" data-k="p-resume" hidden>${ic("play")}Resume blocking</button></div>
      <p class="small muted">Unblocks every site and app on your list for a while - hours, time limits, opening limits, modes, temporary and permanent blocks. The protection lists, blocked words and SafeSearch keep working, and the time you use still counts toward your limits. It ends by itself; starting it needs the challenge, resuming early never does. No emergency unlock is used.</p>
    </div></section>`;
}

function offCard() {
  return `<section class="card" data-card="off"><header class="card-h">${kic("k-block", "power")}<h3>Turn Lockdown off</h3></header>
    <div class="card-b" style="padding:0">
      ${field('<span data-k="o-title">Lockdown is on and enforcing your rules.</span>',
        "Off means off: no site or app blocking, no time limits, no protection lists, no bedtime, breaks or reminders - as if Lockdown weren't installed. It stays off until you turn it back on. Turning it off needs the challenge; turning it back on never does.",
        sw(true, 'data-k="lockdown" aria-label="Lockdown is on"'))}
    </div></section>`;
}

function keepingCard() {
  return `<section class="card"><header class="card-h">${kic("k-protect", "shield-check")}<h3>Keeping Lockdown running</h3></header>
    <div class="card-b"><ul class="ab-list small">${KEEPING.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div></section>`;
}

function alwaysCard() {
  return `<section class="card"><header class="card-h">${kic("k-grey", "lock")}<h3>Always on</h3></header>
    <div class="card-b" style="padding:0">
      ${field("Uninstall protection", "Uninstalling Lockdown always asks for the challenge. This can't be switched off.", '<span class="chip chip-ok">Always on</span>')}
      ${field("Trusted time", "Lockdown keeps its own clock, so changing the Windows clock can't end a block early.", '<span class="chip chip-ok">Always on</span>')}
    </div></section>`;
}

function protectsCard() {
  return `<section class="card"><header class="card-h">${kic("k-block", "ban")}<h3>What needs the challenge</h3></header>
    <div class="card-b stack-sm"><ul class="ab-list small">${PROTECTS.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
      <p class="ab-foot">Making blocks stricter is always instant. Not included: the emergency unlock (it has its own limit) and changing when limits reset (it never shortens a limit day). Stopping a locked mode always needs the phrase, even with no challenge turned on here.</p></div></section>`;
}

// ---------------------------------------------------------------- loading and painting

export async function load() {
  apply(await ld.call("antibypass_get"));
}

/** Show what is saved, in every box - also the one with the focus (a change that was cancelled or declined is dropped). */
async function putBack() {
  apply(await ld.call("antibypass_get"), false, true);
}

function apply(next, keepTyped = false, committed = false) {
  d = next;
  const t = parseNaive(d.now);
  if (!Number.isNaN(t)) clock = { base: t, at: performance.now() };
  paintBanner();
  paintChallenges(keepTyped, committed);
  paintPause();
  paintOff();
  if (pendingFocus) { pendingFocus = false; focusPause(); }
}

/** A box is left alone while it has the focus (the owner may be typing) - unless what it holds was just saved. */
function setBox(box, value, committed) {
  if ((committed || document.activeElement !== box) && box.value !== value) box.value = value;
}
function setSwitch(box, on) {
  if (box.checked !== on) box.checked = on;
}

function paintBanner() {
  const b = d.banner;
  const left = b.ends_at ? (parseNaive(b.ends_at) - trustedNow()) / 1000 : null;
  const v = bannerView(b, d.now, left);
  const root_ = q("banner");
  for (const tone of ["k-grey", "k-block", "k-ok", "k-limit"]) root_.classList.toggle(tone, tone === v.tone);
  const use = root_.querySelector(":scope > .ic use");
  if (use.getAttribute("href") !== `#i-${v.icon}`) use.setAttribute("href", `#i-${v.icon}`);
  setText(q("b-title"), v.title);
  setText(q("b-text"), v.text);
  root_.querySelector('[data-act="lock"]').hidden = !v.lock;
  root_.querySelector('[data-act="unlock"]').hidden = !v.unlock;
  q("b-meter").hidden = v.bar == null;
  if (v.bar != null) q("b-bar").style.width = `${(v.bar * 100).toFixed(1)}%`;
}

function paintChallenges(keepTyped, committed) {
  const c = d.config;
  setSwitch(q("phrase"), c.phrase);
  setSwitch(q("complex"), c.complex);
  setSwitch(q("grid"), c.grid);
  setSwitch(q("own"), c.use_own);
  setSwitch(q("hours"), c.hours);
  paintSteps();
  if (!keepTyped) {
    setBox(q("custom"), c.custom_phrase, committed);
    setBox(q("wait"), c.wait_text, committed);
    lastWait = c.wait_text;
  }
  for (const f of root.querySelectorAll(".ab-cx")) f.classList.toggle("ab-dim", c.use_own);
  const key = JSON.stringify(c.windows);
  if (!hoursDirty && key !== winKey) {
    winKey = key;
    q("wins").innerHTML = c.windows.map(win).join("");
    markWindows();
  }
  q("save-hours").classList.toggle("btn-primary", hoursDirty);
}

/** The step list: made once Python's options are here; the draft is left alone while the owner has edits not saved yet. */
function paintSteps() {
  const c = d.config;
  const info = { length: c.length, grid: c.grid, own_length: c.use_own ? c.custom_phrase.replace(/\s/g, "").length : 0 };
  if (!steps) {
    steps = stepListEditor({ steps: c.steps, options: d.challenge, onChange: stepsChanged, phrase: info, label: "Challenge steps" });
    q("steps").append(steps.el);
  } else {
    steps.setPhrase(info);
    if (!stepsEdited && JSON.stringify(steps.get()) !== JSON.stringify(c.steps)) steps.set(c.steps);
  }
  steps.setSaved(c.steps);
  steps.setDisabled(!c.phrase);
  paintStepsBar();
}

function stepsChanged() {
  stepsEdited = true;
  paintStepsBar();
}

/** The save bar under the list: whether anything changed, and (display only) whether Python will ask the challenge. */
function paintStepsBar() {
  const c = d.config;
  const now = steps.get();
  const changed = JSON.stringify(now) !== JSON.stringify(c.steps);
  const looser = c.phrase ? stepsLooser(c.steps, now, d.challenge) : [];
  const why = q("steps-why");
  why.classList.toggle("loose", looser.length > 0);
  setText(why, !changed ? "No changes." : looser.length ? `Looser: ${looser.join("; ")}.` : c.phrase ? "Only harder or moved - free." : "Saved for when the challenge is on.");
  const save = q("save-steps");
  save.disabled = !changed;
  setText(save, looser.length ? "Save - asks the challenge" : "Save steps");
  save.classList.toggle("btn-primary", changed);
  q("undo-steps").disabled = !changed;
  q("px").hidden = !steps.hasPhrase();
}

function paintPause() {
  const p = d.pause;
  const holder = q("p-seg");
  const labels = p.options.map((o) => o.label).join("|");
  if (holder.dataset.labels !== labels) {                  // built once, from what Python offers
    holder.dataset.labels = labels;
    holder.innerHTML = seg("pause", p.options.map((o) => [o.minutes == null ? "day" : String(o.minutes), o.label]), pauseMinutes == null ? "day" : String(pauseMinutes));
  }
  q("p-form").hidden = p.running;
  q("p-start").hidden = p.running;
  q("p-resume").hidden = !p.running;
  q("p-note").classList.toggle("ok", !p.running);
  seenPauseLeft = -1;
  paintPauseLeft();
}

function pauseLeftMin() {
  const p = d.pause;
  return p.running ? Math.max(0, Math.ceil((parseNaive(p.until) - trustedNow()) / 60000)) : 0;
}

function paintPauseLeft() {
  const left = pauseLeftMin();
  if (left === seenPauseLeft) return;
  seenPauseLeft = left;
  setText(q("p-note"), pauseNote(d.pause, left));
}

function paintOff() {
  const off = d.lockdown.off;
  setSwitch(q("lockdown"), !off);
  q("o-title").style.color = off ? "var(--k-block)" : "var(--ok)";
  setText(q("o-title"), off ? `Lockdown is OFF - nothing has been enforced since ${d.lockdown.since}.` : "Lockdown is on and enforcing your rules.");
}

// ---------------------------------------------------------------- the clock

/** The ticking part of the banner: the countdown in the title and the bar (nothing else changes between two loads). */
function tickBanner(left) {
  const b = d.banner;
  if (b.kind !== "unlocked" && b.kind !== "waiting") return;
  const v = bannerView(b, d.now, left);
  setText(q("b-title"), v.title);
  const w = `${(v.bar * 100).toFixed(1)}%`;
  if (q("b-bar").style.width !== w) q("b-bar").style.width = w;
}

function tick() {
  if (!d) return;
  const b = d.banner;
  if (b.ends_at) {
    const left = (parseNaive(b.ends_at) - trustedNow()) / 1000;
    if (left <= 0) {
      if (firedFor !== b.ends_at || performance.now() - firedAt > REASK_MS) {
        firedFor = b.ends_at;
        firedAt = performance.now();
        tickBanner(0);
        page.reload();                                       // it ended by itself: ask once for what comes next
      }
    } else tickBanner(left);
  }
  if (d.pause.running) {
    if (parseNaive(d.pause.until) <= trustedNow()) {
      if (firedFor !== d.pause.until || performance.now() - firedAt > REASK_MS) { firedFor = d.pause.until; firedAt = performance.now(); page.reload(); }
    } else paintPauseLeft();
  }
}

export function patch({ payload }) {
  if (!d || !payload || !("antibypass" in payload || "pause" in payload || "off" in payload)) return;
  if (statusSig(ld.status) !== sigOfPage(d)) page.reload();         // (ld.status is the merged whole, payload only what changed)
}

// ---------------------------------------------------------------- the time windows

function windowsOf() {
  return [...q("wins").querySelectorAll("[data-win]")].map((row) => {
    const [from, to] = row.querySelectorAll(".tp");
    const time = (box) => normTime(box.querySelector(".tp-h").value, box.querySelector(".tp-m").value);
    return {
      days: [...row.querySelectorAll(".days button")].map((b, i) => (b.getAttribute("aria-pressed") === "true" ? i : -1)).filter((i) => i >= 0),
      start: time(from), end: time(to),
    };
  });
}

function markWindows() {
  const rows = q("wins").querySelectorAll("[data-win]");
  rows.forEach((r, i) => {
    r.setAttribute("aria-label", `Time window ${i + 1}`);
    r.querySelector('[data-act="win-x"]').hidden = rows.length < 2;       // the last window keeps no x (removing it would make the hours pointless)
  });
}

function windowsEdited() {
  hoursDirty = true;
  q("save-hours").classList.add("btn-primary");
}

// ---------------------------------------------------------------- doing things

function showErr(text) {
  setText(q("err"), text || "");
}

/** Send the settings that changed. Python says what happens: saved (-> the page data), a challenge, or a question. */
async function save(patch_, confirmed = false) {
  const key = JSON.stringify([patch_, confirmed]);
  if (inflight === key) return false;
  inflight = key;
  showErr("");
  try {
    const res = await guarded(ld.call("antibypass_save", patch_, confirmed));
    if (res === CANCELLED) { await putBack(); return false; }
    if (res && res.needs_confirm) {
      const yes = await confirmChange(res.line);          // (a second try of the same change waits: `inflight` is still set)
      inflight = "";
      if (!yes) { await putBack(); return false; }
      return save(patch_, true);
    }
    if ("windows" in patch_) hoursDirty = false;
    if ("steps" in patch_) stepsEdited = false;
    apply(res, false, true);
    return true;
  } catch (err) {
    showErr(err.message || "That didn't work.");
    apply(d, true);                                          // put the switches back; what was typed stays for the owner to fix
    return false;
  } finally {
    if (inflight === key) inflight = "";
  }
}

function confirmChange(line) {
  const h = openDialog({
    key: "ab-confirm", title: "Change the Anti-Bypass challenge?",
    body: `<p>This is what you'll need to loosen a block from now on. Make sure you can actually do it - if you can't, you won't be able to unlock anything.</p><p class="ab-line">${esc(line)}</p>`,
    buttons: [{ label: "Cancel", value: false }, { label: "Change it", kind: "primary", value: true }],
  });
  return h ? h.done.then((v) => v === true) : Promise.resolve(false);
}

async function refreshShell() {
  try { (await import("../shell.js")).refreshStatus(); } catch (_) { /* the shell catches up with the next status push */ }
}

function commitWait() {
  const box = q("wait");
  const text = box.value.trim();
  if (text === lastWait) return;
  save({ wait: text });
}

function commitPhrase() {
  save({ custom_phrase: q("custom").value });
}

/** "Use my own phrase": switching it on takes what the box holds too (typed but not saved yet). */
function commitOwn(on) {
  save(on ? { use_own: true, custom_phrase: q("custom").value } : { use_own: false });
}

async function startPause() {
  const btn = q("p-start");
  btn.disabled = true;
  try {
    const res = await guarded(ld.call("pause_start", pauseMinutes, q("p-silent").checked));
    if (res !== CANCELLED) { d.pause = res; paintPause(); refreshShell(); }
  } catch (err) {
    toast(err.message || "That didn't work.");
  } finally {
    btn.disabled = false;
  }
}

async function resumePause() {
  try {
    await ld.call("pause_resume");
    d.pause = await ld.call("pause_get");
    paintPause();
    refreshShell();
  } catch (err) { toast(err.message || "That didn't work."); }
}

async function switchLockdown(on) {
  const box = q("lockdown");
  try {
    if (on) {
      await ld.call("antibypass_switch_on");
      d.lockdown = { off: false, since: null };
    } else {
      const res = await guarded(ld.call("lockdown_switch_off"));
      if (res !== CANCELLED) d.lockdown = res;
    }
  } catch (err) {
    toast(err.message || "That didn't work.");
  }
  paintOff();
  box.checked = !d.lockdown.off;
  refreshShell();
}

async function challengeCall(name) {
  try {
    const res = await guarded(ld.call(name));
    if (res !== CANCELLED) apply(res);
    else await putBack();
  } catch (err) {
    toast(err.message || "That didn't work.");
  }
}

function focusPause() {
  const card = root.querySelector('[data-card="pause"]');
  card.scrollIntoView({ block: "start" });
  card.classList.remove("ab-flash");
  void card.offsetWidth;
  card.classList.add("ab-flash");
  const target = d.pause.running ? q("p-resume") : q("p-start");
  target.focus({ preventScroll: true });
}

export function show(opts) {
  if (opts && opts.focus === "pause") {
    if (d) focusPause(); else pendingFocus = true;
  }
}

// ---------------------------------------------------------------- events (delegated, wired once)

function wire() {
  root.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b || b.disabled) return;
    switch (b.dataset.act) {
      case "try": challengeCall("antibypass_try"); break;
      case "unlock": challengeCall("antibypass_unlock_to_edit"); break;
      case "lock":
        ld.call("antibypass_lock").then((res) => apply(res)).catch((err) => toast(err.message || "That didn't work."));
        break;
      case "save-phrase": commitPhrase(); break;
      case "save-steps": save({ steps: steps.get() }); break;
      case "undo-steps":
        stepsEdited = false;
        steps.set(d.config.steps);
        paintStepsBar();
        break;
      case "help": {
        const open = b.getAttribute("aria-expanded") !== "true";
        b.setAttribute("aria-expanded", String(open));
        root.querySelector(`[data-helptext="${b.dataset.help}"]`).hidden = !open;
        break;
      }
      case "win-add": {
        q("wins").insertAdjacentHTML("beforeend", win({ days: [6], start: "18:00", end: "20:00" }));
        markWindows();
        windowsEdited();
        break;
      }
      case "win-x":
        b.closest("[data-win]").remove();
        markWindows();
        windowsEdited();
        break;
      case "save-hours": save({ windows: windowsOf() }); break;
      case "pause": startPause(); break;
      case "resume": resumePause(); break;
      default:
    }
  });

  root.addEventListener("seg", (e) => {
    const { name, value } = e.detail;
    if (name === "pause") pauseMinutes = value === "day" ? null : Number(value);
  });

  root.addEventListener("press", (e) => { if (e.target.closest("[data-win]")) windowsEdited(); });

  root.addEventListener("input", (e) => {
    const t = e.target;
    if (t.closest("[data-win]")) { windowsEdited(); return; }
  });

  root.addEventListener("change", (e) => {
    const t = e.target;
    const k = t.dataset && t.dataset.k;
    if (k === "phrase" || k === "complex" || k === "grid" || k === "hours") save({ [k]: t.checked });
    else if (k === "own") commitOwn(t.checked);
    else if (k === "wait") commitWait();
    else if (k === "lockdown") switchLockdown(t.checked);
  });

  root.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const k = e.target.dataset && e.target.dataset.k;
    if (k === "wait") { e.preventDefault(); commitWait(); }
    else if (k === "custom") { e.preventDefault(); commitPhrase(); }
  });
}
