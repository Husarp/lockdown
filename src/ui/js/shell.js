// The shell: sidebar footer, top status line, banners, save bar, trusted clock - all patched in place from
// get_status() and `status` / `draft` pushes. The page content is the router's job (js/router.js).
// Boot order: apply the look -> start the router (first page loads in parallel) -> fetch status -> ui_ready.
import { ld } from "./bridge.js";
import { applyLook, currentLook } from "./theme.js";
import { injectSprite } from "./icons.js";
import { router } from "./router.js";
import { el, esc, ic, installControls, setText, toast, fmt } from "./ui.js";
import { initUpdate } from "./update.js";   // R17: the update banner + dialog
import "./challenge.js";   // R06: the challenge dialog (opens on a challenge_open push / needs-challenge event)

const $ = (id) => document.getElementById(id);
const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");

// ---------------------------------------------------------------- trusted clock
// Python sends the trusted time (naive local, "2026-10-11T14:32:05") with the status. We read it as if it were UTC
// (only to take it apart again - no time zone maths) and let it run on from performance.now().
const clock = { base: 0, at: 0 };

export function parseNaive(iso) {
  const m = /^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?/.exec(iso || "");
  return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) : NaN;
}
export const hhmm = (iso) => { const t = parseNaive(iso); if (Number.isNaN(t)) return ""; const d = new Date(t); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; };
export const clockText = (ms) => { const d = new Date(ms); return `${DAY[d.getUTCDay()]} ${d.getUTCDate()} ${MONTH[d.getUTCMonth()]} · ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; };
export const trustedNow = () => clock.base + (performance.now() - clock.at);

function syncClock(iso) {
  const t = parseNaive(iso);
  if (!Number.isNaN(t)) { clock.base = t; clock.at = performance.now(); }
}

/** Trusted time = the PC clock + `offset` seconds (what the status pushes carry). Read the PC's wall clock as naive. */
export function syncClockOffset(offset) {
  if (typeof offset !== "number" || Number.isNaN(offset)) return;
  clock.base = Date.now() - new Date().getTimezoneOffset() * 60000 + offset * 1000;
  clock.at = performance.now();
}

/** Set the clock from whatever time a status piece carries (clock_now from get_status, clock_offset from a push). */
function syncClockFrom(piece) {
  if (piece.clock_now) syncClock(piece.clock_now);
  else if (typeof piece.clock_offset === "number") syncClockOffset(piece.clock_offset);
}

/**
 * get_status answers in the BLUEPRINT's words (service_running, off_since, mode.phase as a name) but the `status`
 * pushes (resident/snapshot.py) use the snapshot's (running, off, mode.phase as {name, ends_at, round, rounds}). The shell
 * keeps ONE vocabulary - the push one - so a push can be merged over a fetched status.
 */
export function canon(st) {
  const out = { ...st };
  if ("service_running" in out) { out.running = out.service_running; delete out.service_running; }
  if ("off_since" in out) { out.off = out.off_since; delete out.off_since; }
  if (out.mode && typeof out.mode.phase === "string") out.mode = { ...out.mode, phase: { name: out.mode.phase } };
  return out;
}

// ---------------------------------------------------------------- banners (one element per id, updated in place)
const bannerEls = new Map();

export const banners = {
  /**
   * show(id, {tone: "k-block" | "k-brand" | ..., icon, title, text, actions: [{label, primary, onClick}], order, onClose}).
   * Calling it again with the same id updates that banner in place.
   */
  show(id, spec) {
    let b = bannerEls.get(id);
    if (!b) {
      b = { root: el(`<div class="banner" data-banner="${esc(id)}"></div>`), key: "" };
      bannerEls.set(id, b);
      $("banners").append(b.root);
    }
    const sig = JSON.stringify([spec.tone, spec.icon, spec.title, spec.text, (spec.actions || []).map((a) => [a.label, !!a.primary]), !!spec.onClose]);
    b.root.style.order = String(spec.order ?? 5);
    if (b.key !== sig) {                                   // rebuilt only when what it says changed
      b.key = sig;
      b.root.className = `banner ${spec.tone || "k-brand"}`;
      b.root.innerHTML = `${ic(spec.icon || "alert")}<span class="grow"><b></b> <span class="muted"></span></span>`;
      b.root.querySelector("b").textContent = spec.title || "";
      b.root.querySelector(".muted").textContent = spec.text || "";
      for (const a of spec.actions || []) {
        const btn = el(`<button class="btn btn-sm ${a.primary ? "btn-primary" : "btn-text"}" type="button"></button>`);
        btn.textContent = a.label;
        btn.addEventListener("click", () => a.onClick(btn));
        b.root.append(btn);
      }
      if (spec.onClose) {
        const x = el(`<button class="icon-btn icon-btn-sm" type="button" aria-label="Hide">${ic("x")}</button>`);
        x.addEventListener("click", spec.onClose);
        b.root.append(x);
      }
    }
    b.root.hidden = false;
  },
  hide(id) {
    const b = bannerEls.get(id);
    if (b) b.root.hidden = true;
  },
};

// ---------------------------------------------------------------- the status, rendered in place
let status = {};

async function action(name, ...args) {
  try {
    const data = await ld.call(name, ...args);
    if (data && typeof data === "object" && "service_running" in data) { syncClockFrom(data); Object.assign(ld.status, canon(data)); setStatus(ld.status); }
    return data;
  } catch (err) {
    toast(err.message || "That didn't work.");
    return null;
  }
}

export async function refreshStatus() {
  try {
    const data = await ld.call("get_status");
    syncClockFrom(data);
    Object.assign(ld.status, canon(data));
    setStatus(ld.status);
  } catch (err) {
    toast("Can't reach Lockdown: " + (err.message || "unknown error"));
  }
}

function renderTop(st) {
  const off = !!st.off;
  $("tb-prot").hidden = off;
  $("tb-prot-off").hidden = !off;
  $("tb-prot-on").hidden = !off;
  const m = st.mode;
  const mode = $("tb-mode");
  mode.classList.toggle("muted", !m);
  setText(mode.querySelector(".n"), m ? m.name : "No mode");
  setText(mode.querySelector(".u"), !m ? "" : (m.phase && m.phase.name ? `· ${m.phase.name} ` : "") + (m.ends_at ? `until ${hhmm(m.ends_at)}` : "until you stop"));
  const paused = !!st.pause && !off;
  $("tb-div2").hidden = off;
  $("tb-active").hidden = off || paused;
  $("tb-pause").hidden = off || paused;
  $("tb-paused").hidden = !paused;
  $("tb-resume").hidden = !paused;
  renderPauseLeft();
}

function renderPauseLeft() {
  const p = status.pause;
  if (!p || status.off) return;
  const left = Math.max(0, Math.ceil((parseNaive(p.until) - trustedNow()) / 60000));
  setText($("tb-paused-t"), `Blocks paused · ${fmt(left)} left`);
}

function renderFoot(st) {
  const run = !!st.running;
  const dot = $("svc-dot");
  dot.style.setProperty("--k", run ? "var(--ok)" : "var(--k-block)");
  dot.classList.toggle("pulse", run);
  setText($("svc-text"), run ? "Service running" : "Service not running");
  $("side-foot").title = run ? "The Lockdown service is enforcing your blocks." : "The Lockdown service is not running: nothing is enforced.";
}

function renderBanners(st) {
  if (st.off) {
    banners.show("off", {
      tone: "k-block", icon: "power", order: 1, title: "Lockdown is switched off.",
      text: `Nothing is blocked since ${hhmm(st.off)}.`,
      actions: [{ label: "Turn back on", primary: true, onClick: () => action("antibypass_switch_on") }],
    });
  } else banners.hide("off");
  if (!st.running) {
    banners.show("service", {
      tone: "k-block", icon: "alert", order: 2, title: "Blocking isn't enforced right now.",
      text: "The Lockdown service stopped. Screen time is still recorded.",
      actions: [{ label: "Start service", primary: true, onClick: startService }],
    });
  } else banners.hide("service");
}

async function startService() {
  if (await action("service_start")) {
    toast("Starting the service – Windows may ask for permission.");
    setTimeout(refreshStatus, 3000);            // (once, after a click - not a poll)
  }
}

function renderDraft(d) {
  if (!d) return;
  const auto = $("sb-auto");
  if (auto.checked !== !!d.autosave) auto.checked = !!d.autosave;
  $("sb-discard").hidden = !d.dirty;
  const save = $("sb-save");
  save.hidden = !!d.autosave && !d.dirty;
  save.disabled = !d.dirty;
  setText(save, d.dirty ? "Save changes" : "Saved");
  save.classList.toggle("btn-primary", !!d.dirty);
  $("sb-dot").hidden = !d.dirty;
  const text = $("sb-text");
  const key = d.dirty ? d.change_summary || "" : null;            // rewritten only when what it says changed
  if (text.dataset.key !== String(key)) {
    text.dataset.key = String(key);
    text.textContent = "";
    if (d.dirty) { const b = document.createElement("b"); b.textContent = "Unsaved changes"; text.append(b, key ? ` · ${key}` : ""); }
  }
}

export function setStatus(st) {
  status = st;
  renderTop(st);
  renderFoot(st);
  renderBanners(st);
  renderDraft(st.draft);
  $("topbar").dataset.ready = "1";
  tickClock();
}

function tickClock() {
  if (!clock.base) return;
  setText($("tb-clock"), clockText(trustedNow()));
  renderPauseLeft();
}

// ---------------------------------------------------------------- the look, from Settings (R27) or anywhere
/** setLook({theme, accent, scale}) applies at once (live, no reload) and saves through the Api; a refusal puts the old look back. */
export async function setLook(look) {
  const before = currentLook();
  applyLook(look);
  try {
    if (look.theme !== undefined) await ld.call("set_appearance", look.theme);
    if (look.accent !== undefined) await ld.call("set_accent", look.accent);
    if (look.scale !== undefined) await ld.call("set_scale", look.scale);
  } catch (err) {
    applyLook({ theme: before.pref, accent: before.accent, scale: before.scale });
    toast(err.message || "That didn't work.");
    throw err;
  }
}

// ---------------------------------------------------------------- save bar: the draft calls are wired by R18
// A reply {needs_challenge: true, ticket} is announced as a window event "ld:needs-challenge" for the challenge dialog.
async function draftCall(name, ...args) {
  try {
    const data = await ld.call(name, ...args);
    if (data && data.needs_challenge) window.dispatchEvent(new CustomEvent("ld:needs-challenge", { detail: data }));
    return data;
  } catch (err) {
    toast(err.message || "That didn't work.");
    return null;
  }
}

function wireSaveBar() {
  $("sb-auto").addEventListener("change", async (e) => {
    const on = e.target.checked;
    if ((await draftCall("draft_set_autosave", on)) === null) e.target.checked = !on;
  });
  $("sb-save").addEventListener("click", () => draftCall("draft_save"));
  $("sb-discard").addEventListener("click", () => draftCall("draft_discard"));
}

// ---------------------------------------------------------------- hidden window: no animation, no timers
function syncHidden() {
  const hidden = !ld.isVisible();
  const was = document.documentElement.hasAttribute("data-hidden");
  document.documentElement.toggleAttribute("data-hidden", hidden);
  if (was && !hidden) refreshStatus();            // back from hidden / sleep: one fresh status (and the clock)
}

// ---------------------------------------------------------------- errors in our own code go to the log
function reportErrors() {
  let sent = 0;
  const send = (msg) => { if (sent++ < 5) ld.call("log_client_error", String(msg).slice(0, 2000)).catch(() => {}); };
  window.addEventListener("error", (e) => send(`${e.message} (${e.filename}:${e.lineno})`));
  window.addEventListener("unhandledrejection", (e) => send(`unhandled: ${e.reason && e.reason.stack ? e.reason.stack : e.reason}`));
}

// ---------------------------------------------------------------- boot
async function boot() {
  reportErrors();
  installControls();
  wireSaveBar();
  injectSprite();
  $("tb-prot-on").addEventListener("click", () => action("antibypass_switch_on"));
  $("tb-resume").addEventListener("click", () => action("pause_resume"));
  $("tb-pause").addEventListener("click", () => router.go("antibypass", { focus: "pause" }));
  document.addEventListener("visibilitychange", syncHidden);
  ld.on("visible", syncHidden);
  syncHidden();                                   // (the agent loads the window hidden: no animation until it is shown)
  ld.on("status", (delta, whole) => {
    if (delta) syncClockFrom(delta);
    if ("running" in whole) setStatus(whole);
  });   // (not before the first full status)
  ld.on("draft", (d, whole) => { whole.draft = { ...(whole.draft || {}), ...d }; renderDraft(whole.draft); });   // (kept in ld.status too)
  ld.everySecond(tickClock);
  router.start();
  const [look, st] = await Promise.all([ld.call("get_theme"), ld.call("get_status")]);
  applyLook(look);
  syncClockFrom(st);
  Object.assign(ld.status, canon(st));
  setStatus(ld.status);
  initUpdate({ banners, go: (id) => router.go(id) });
  document.documentElement.dataset.ready = "1";
  await ld.call("ui_ready");
}

if (typeof document !== "undefined" && document.getElementById("view")) {
  boot().catch((err) => {
    document.documentElement.dataset.ready = "error";
    toast("Lockdown couldn't start: " + (err && err.message ? err.message : err));
    try { ld.call("log_client_error", "boot: " + (err && err.stack ? err.stack : err)).catch(() => {}); } catch (_) { /* ignore */ }
  });
}
