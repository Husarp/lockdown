// The full-screen cover: the bedtime screen and a running strict break.
// Python owns what is on it (resident/overlay.py) and pushes it whole (`overlay` event); this file draws it, ticks the
// numbers locally from "ms left" values on the page's own steady clock (performance.now) - the Dismiss wait, the break
// countdown, the bedtime clock - and tells Python what was pressed. Python decides everything: Dismiss only works when
// its wait is over (an early press comes back {early}), Disable alerts and the "Off tonight / Snooze" that follow are
// behind the Anti-Bypass challenge (js/challenge.js, opened INSIDE this page so nothing can hide it), Emergency spends
// the use in Python.
// Nothing animates and nothing redraws continuously: one timer that wakes only when a shown number changes (every
// second while a wait or a break countdown runs, then once a minute for the clock) and none once Python has taken the
// cover down (it hides the window and pushes `shown: false`). The page does not trust `document.visibilityState` to stop
// the Dismiss wait: a wrong "hidden" would leave Dismiss off for good.
import { ld } from "./bridge.js";
import { CANCELLED, countdownText, guarded } from "./challenge.js";
import { symbol } from "./icons.js";
import { applyLook } from "./theme.js";

const SLACK_MS = 15;           // wake just past the boundary, so the shown second has changed (as gui/screens.py did)
const pad = (n) => String(n).padStart(2, "0");

// ---------------------------------------------------------------- plain helpers (node tests)

/** The Dismiss button while its wait runs: {text, ready, wake} - wake = ms until the number changes (null once it works). */
export function waitLabel(msLeft) {
  if (!(msLeft > 0)) return { text: "Dismiss", ready: true, wake: null };
  const left = Math.ceil(msLeft / 1000);
  return { text: `Dismiss (${left})`, ready: false, wake: Math.round(msLeft - (left - 1) * 1000) + SLACK_MS };
}

/** "22:30" from seconds since midnight. */
export const clockText = (sec) => {
  const s = ((Math.floor(sec) % 86400) + 86400) % 86400;
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}`;
};

/** ms until the break countdown's shown second changes (null once it is over). */
export function countdownWake(msLeft) {
  if (!(msLeft > 0)) return null;
  const shown = Math.ceil(msLeft / 1000);
  return Math.round(msLeft - (shown - 1) * 1000) + SLACK_MS;
}

/** ms until the clock shows another minute. */
export const clockWake = (sec) => Math.round((60 - (sec % 60)) * 1000) + SLACK_MS;

/** How a button looks: dismiss = the filled one, disable = quiet, emergency = a text link with the lifebuoy. */
export const roleOf = (action) => (action === "dismiss" ? "primary" : action === "disable" ? "ghost" : action === "emergency" ? "emerg" : "plain");

/** Under the emergency list: what is left of a kind. */
export function kindLine(kind) {
  return kind.left > 0 ? `${kind.left} of ${kind.allowed} left ${kind.per_text}` : `none left · resets ${kind.resets_text}`;
}

// ---------------------------------------------------------------- the page

const SVG = "http://www.w3.org/2000/svg";
function icon(name) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", "ic");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG, "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

function h(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

export function createOverlay(env = {}) {
  const doc = env.document || document;
  const root = env.root || doc.getElementById("fs");
  const call = env.call || ((...a) => ld.call(...a));
  const now = env.now || (() => performance.now());
  const setT = env.setTimeout || ((f, ms) => setTimeout(f, ms));
  const clearT = env.clearTimeout || ((t) => clearTimeout(t));
  const askGuard = env.guarded || guarded;

  let seq = -1;                    // the newest cover seen: an older one (overlay_ready answering late) is ignored
  let st = null;                   // the cover as drawn: {data, base, waitEnd, untilEnd, clockSec}
  let timer = 0;
  let view = "main";               // main | choice | emergency
  let busy = false;
  let sig = "";
  let dismissBtn = null;
  let shownText = { hero: "", dismiss: "" };

  // ------------------------------------------------------------ skeleton, built once
  const halo = h("span", "halo");
  const hero = h("div", "hero num");
  const title = h("h1");
  const text = h("p", "fs-text");
  const row = h("div", "btn-row");
  const extra = h("div", "btn-row");
  const panel = h("div", "fs-panel");
  panel.hidden = true;
  const card = h("div", "fs");
  card.append(halo, hero, title, text, row, extra, panel);
  root.append(card);

  // ------------------------------------------------------------ the numbers
  function msLeft(end) {
    return end == null ? null : end - now();
  }

  function paintNumbers() {
    if (!st) return;
    const wait = st.waitEnd == null ? null : waitLabel(msLeft(st.waitEnd));
    if (dismissBtn && wait) {
      if (wait.text !== shownText.dismiss) { dismissBtn.textContent = wait.text; shownText.dismiss = wait.text; }
      dismissBtn.disabled = !wait.ready;
    }
    let heroText = "";
    if (st.untilEnd != null) heroText = countdownText(Math.max(0, msLeft(st.untilEnd)) / 1000);     // a countdown when there is an end
    else if (st.clockSec != null) heroText = clockText(st.clockSec + (now() - st.base) / 1000);
    if (heroText !== shownText.hero) { hero.textContent = heroText; shownText.hero = heroText; }
    hero.hidden = !heroText;
  }

  function wakeIn() {
    if (!st) return null;
    const wakes = [];
    if (st.waitEnd != null) wakes.push(waitLabel(msLeft(st.waitEnd)).wake);
    if (st.untilEnd != null) wakes.push(countdownWake(msLeft(st.untilEnd)));
    else if (st.clockSec != null) wakes.push(clockWake(st.clockSec + (now() - st.base) / 1000));
    const real = wakes.filter((w) => w != null);
    return real.length ? Math.max(1, Math.min(...real)) : null;
  }

  function schedule() {
    clearT(timer);
    timer = 0;
    if (!st) return;
    const wake = wakeIn();
    if (wake != null) timer = setT(() => { timer = 0; paintNumbers(); schedule(); }, wake);
  }

  // ------------------------------------------------------------ buttons
  function press(action) {
    if (busy) return;
    if (action === "dismiss") return run(() => dismiss());
    if (action === "disable") return run(() => disable());
    if (action === "emergency") return run(() => openEmergency());
  }

  async function run(fn) {
    busy = true;
    try { await fn(); } catch (err) { say(err && err.message ? err.message : "That didn't work."); } finally { busy = false; }
  }

  async function dismiss() {
    const res = await call("overlay_answer", "dismiss");
    if (res && res.early) { setWait(res.wait_ms); }       // Python says the wait is not over: its number wins
  }

  function setWait(ms) {
    if (!st) return;
    st.waitEnd = now() + ms;
    paintNumbers();
    schedule();
  }

  async function disable() {
    const res = await askGuard(call("overlay_disable"));
    if (res && res.choose) showChoice();
    else if (res === CANCELLED) setView("main");          // (Python started the wait over, if it was running: a push follows)
  }

  async function answer(action) {
    const res = await askGuard(call("overlay_answer", action));
    if (res === CANCELLED) return;                        // the challenge was cancelled: stay on the choice
    if (res && res.back) setView("main");
  }

  // ------------------------------------------------------------ the choice and the emergency list
  function say(message) {
    let line = panel.querySelector(".fs-err");
    if (!line) {
      line = h("p", "fs-err");
      line.setAttribute("role", "alert");
      panel.append(line);
    }
    line.textContent = message;
    panel.hidden = false;
  }

  function setView(next) {
    view = next;
    panel.textContent = "";
    panel.hidden = next === "main";
    row.hidden = extra.hidden = next !== "main";
    if (next === "main") focusFirst();
  }

  function button(label, cls, onClick, data) {
    const b = h("button", "btn " + cls, label);
    b.type = "button";
    if (data) b.dataset.action = data;
    b.addEventListener("click", onClick);
    return b;
  }

  function showChoice() {
    setView("choice");
    panel.append(h("h2", "", "Bedtime alerts off"), h("p", "muted", "Off for the rest of tonight, or snooze a while?"));
    const choices = h("div", "btn-row");
    choices.append(
      button("Off tonight", "btn-lg btn-primary", () => run(() => answer("off_tonight")), "off_tonight"),
      button("Snooze 15 min", "btn-lg", () => run(() => answer("snooze:15")), "snooze:15"),
      button("Cancel", "btn-lg btn-ghost", () => run(() => answer("cancel")), "cancel"));
    panel.append(choices);
    choices.firstChild.focus();
  }

  async function openEmergency() {
    const info = await call("overlay_emergency_kinds");
    setView("emergency");
    const kinds = (info && info.kinds) || [];
    if (!info || !info.usable || !kinds.length) {
      panel.append(h("p", "muted", "Emergency unlock is turned off."), emergencyButtons(false));
      return;
    }
    const first = kinds.findIndex((k) => k.left > 0);
    const list = h("div", "kinds k-emerg");
    kinds.forEach((k, i) => {
      const card = h("label", "rcard");
      const input = h("input");
      input.type = "radio";
      input.name = "kind";
      input.value = k.id;
      input.disabled = k.left <= 0;
      input.checked = i === first;
      const kic = h("span", "kic");
      kic.append(icon("lifebuoy"));
      const body = h("span", "grow");
      body.append(h("b", "", `${k.minutes} min`), h("span", "small muted", kindLine(k)));
      card.append(input, kic, body);
      list.append(card);
    });
    panel.append(h("h2", "", "Emergency"), h("p", "muted", "Pauses the bedtime and break alerts for the time you pick. It uses one of that kind’s unlocks; your bedtime settings stay as they are."), list, emergencyButtons(first >= 0));
    const picked = list.querySelector("input:checked");
    if (picked) picked.focus();
  }

  function emergencyButtons(canUse) {
    const bar = h("div", "btn-row");
    const use = button("Use this kind", "btn-lg btn-primary", () => run(async () => {
      const input = panel.querySelector("input[name=kind]:checked");
      if (!input) return;
      await call("overlay_emergency", input.value);      // (Python spends the use and takes the cover down)
    }), "use");
    use.disabled = !canUse;
    bar.append(use, button("Cancel", "btn-lg btn-ghost", () => run(() => answer("cancel")), "cancel"));
    return bar;
  }

  // ------------------------------------------------------------ drawing a cover from Python
  function buttonsFor(data) {
    const main = [], emerg = [];
    for (const b of data.buttons || []) {
      const role = roleOf(b.action);
      const el = h("button", role === "emerg" ? "btn btn-text btn-emerg" : "btn btn-lg" + (role === "primary" ? " btn-primary" : role === "ghost" ? " btn-ghost" : ""));
      el.type = "button";
      el.dataset.action = b.action;
      if (role === "emerg") { el.append(icon("lifebuoy"), document.createTextNode(b.label)); emerg.push(el); }
      else { el.textContent = b.label; main.push(el); }
      el.addEventListener("click", () => press(b.action));
      if (b.action === "dismiss") dismissBtn = el;
    }
    row.replaceChildren(...main);
    extra.replaceChildren(...emerg);
  }

  function focusFirst() {
    const first = root.querySelector(".btn-row:not([hidden]) button:not(:disabled)");
    if (first) first.focus();
  }

  function render(data) {
    if (data && typeof data.seq === "number") {
      if (data.seq < seq) return;
      seq = data.seq;
    }
    if (!data || !data.shown) {              // nothing on the cover: Python hides the window - stop everything here too
      st = null;
      clearT(timer);
      timer = 0;
      if (!root.hidden) { root.hidden = true; setView("main"); }
      return;
    }
    const first = root.hidden;
    const t = now();
    st = {
      data, base: t, waitEnd: data.wait_ms > 0 ? t + data.wait_ms : null,
      untilEnd: typeof data.until_ms === "number" ? t + data.until_ms : null,
      clockSec: typeof data.clock_sec === "number" ? data.clock_sec : null,
    };
    card.className = "fs " + (data.kind === "break" ? "k-ok" : "k-sched");
    halo.replaceChildren(icon(data.kind === "break" ? "coffee" : "moon"));
    title.textContent = data.title || "";
    text.textContent = data.text || "";
    const next = JSON.stringify((data.buttons || []).map((b) => [b.action, b.label]));
    dismissBtn = null;
    if (next !== sig || first || view !== "main") {
      sig = next;
      shownText = { hero: shownText.hero, dismiss: "" };
      if (view !== "main") setView("main");
      buttonsFor(data);
    } else {
      dismissBtn = row.querySelector('[data-action="dismiss"]');
      shownText.dismiss = dismissBtn ? dismissBtn.textContent : "";
    }
    root.hidden = false;
    paintNumbers();
    schedule();
    if (first) focusFirst();
  }

  /** The window was shown again (or the page got a `visible`): draw the numbers as they are now, at once. */
  function sync() {
    if (!st) return;
    paintNumbers();
    schedule();
  }

  doc.addEventListener("keydown", (e) => {              // Esc does nothing here (a challenge open on top closes itself)
    if (e.key === "Escape" && !doc.querySelector("dialog[open]")) e.preventDefault();
  });
  doc.addEventListener("contextmenu", (e) => e.preventDefault());
  doc.addEventListener("visibilitychange", sync);

  return { render, sync, press, get state() { return st; }, get view() { return view; } };
}

// ---------------------------------------------------------------- boot (only in the real page)
if (typeof document !== "undefined" && document.getElementById("fs")) {
  document.getElementById("sprite").innerHTML = ["moon", "coffee", "lifebuoy", "x"].map(symbol).join("");
  const overlay = createOverlay();
  const lookNow = () => ld.call("get_theme").then((look) => applyLook(look)).catch(() => {});
  ld.on("overlay", (payload) => {
    const wasHidden = document.getElementById("fs").hidden;
    overlay.render(payload);
    if (wasHidden && payload && payload.shown) lookNow();     // the look may have changed since the last time (cosmetic, after the paint)
  });
  ld.on("visible", () => overlay.sync());
  ld.call("overlay_ready").then((state) => {
    if (state.look) applyLook(state.look);
    overlay.render(state);
  }).catch(() => {});
  document.documentElement.dataset.ready = "1";
}
