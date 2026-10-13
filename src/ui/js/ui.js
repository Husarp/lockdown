// Lockdown UI helpers (ported from the designer's ui.js). Builders return HTML STRINGS for a page's first mount
// (set once with innerHTML or el()); after that pages patch text and attributes in place - they never re-render a
// whole list or page on a data tick. No framework.

export const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const ic = (name, cls) => `<svg class="ic ${cls || ""}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

/** Kinds of block, each with its icon, meaning colour and name. */
export const KIND = {
  block: { i: "ban", c: "k-block", n: "Block always" },
  sched: { i: "clock", c: "k-sched", n: "Scheduled hours" },
  limit: { i: "timer", c: "k-limit", n: "Time limit" },
  opens: { i: "touch", c: "k-limit", n: "Opening limit" },
  temp: { i: "hourglass", c: "k-temp", n: "Temporary block" },
  mode: { i: "target", c: "k-mode", n: "Mode" },
  protect: { i: "shield", c: "k-protect", n: "Protection" },
  emerg: { i: "lifebuoy", c: "k-emerg", n: "Emergency unlock" },
};
export const tag = (k, text) => `<span class="tag ${KIND[k].c}">${ic(KIND[k].i)}${esc(text)}</span>`;
export const kic = (k, icon) => `<span class="kic ${KIND[k] ? KIND[k].c : k}">${ic(icon || KIND[k].i)}</span>`;

/**
 * The one status line of an item. `st` = {s: "block" | "low" | "ok", until?, left?, note?}. Blocked shows
 * "Blocked (until 18:00)" and never the time left; paused turns block / low into "Paused".
 */
export function status(st, paused) {
  if (paused && (st.s === "block" || st.s === "low")) return `<span class="chip chip-paused">${ic("pause")}Paused</span>`;
  if (st.s === "block") return `<span class="chip chip-block">${ic("ban")}Blocked${st.until ? " (until " + esc(st.until) + ")" : ""}</span>`;
  if (st.s === "low") return `<span class="chip chip-low" title="Running low">${ic("timer")}${esc(st.left)} left</span>`;
  return `<span class="hstack"><span class="chip chip-ok">Allowed</span>${st.note ? `<span class="faint small ellip">${esc(st.note)}</span>` : ""}</span>`;
}

// ---------------------------------------------------------------- controls (strings; behaviour: installControls)
export const sw = (on, attrs) => `<label class="switch"><input type="checkbox" ${on ? "checked" : ""} ${attrs || ""}><span></span></label>`;

export const seg = (name, opts, val, attrs) =>
  `<div class="seg" role="group" data-seg="${esc(name)}" ${attrs || ""}>${opts.map((o) => {
    const [v, l] = Array.isArray(o) ? o : [o, o];
    return `<button type="button" data-act="seg" data-v="${esc(v)}" aria-pressed="${v === val}">${l}</button>`;
  }).join("")}</div>`;

export const stepper = (v, unit, o) => {
  o = o || {};
  const step = o.step || 1;
  return `<span class="stepper ${o.sm ? "stepper-sm" : ""}"><button type="button" data-act="step" data-d="-${step}" aria-label="Less">${ic("minus")}</button>` +
    `<input value="${esc(v)}" inputmode="numeric" data-min="${o.min ?? 0}" data-max="${o.max ?? 9999}" aria-label="${esc(o.label || unit || "value")}">` +
    `<button type="button" data-act="step" data-d="${step}" aria-label="More">${ic("plus")}</button>${unit ? `<span class="unit">${esc(unit)}</span>` : ""}</span>`;
};

export const tp = (t, label) => {
  const [h, m] = t.split(":");
  return `<span class="tp" title="Arrow keys or mouse wheel change the value"><input class="tp-h" value="${esc(h)}" maxlength="2" inputmode="numeric" aria-label="${esc(label || "Time")} hours"><i>:</i><input class="tp-m" value="${esc(m)}" maxlength="2" inputmode="numeric" aria-label="${esc(label || "Time")} minutes"></span>`;
};
export const tpRange = (a, b) => `<span class="hstack">${tp(a, "From")}<span class="range-dash">&ndash;</span>${tp(b, "To")}</span>`;

export const DAYN = ["M", "T", "W", "T", "F", "S", "S"];
export const DAYL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const days = (sel) => `<span class="days">${DAYN.map((d, i) =>
  `<button type="button" data-act="toggle-press" aria-pressed="${!!sel[i]}" aria-label="${DAYL[i]}">${d}</button>`).join("")}</span>`;

export const meter = (pct, k) => `<div class="meter ${k || ""}"><i style="width:${Math.min(100, Math.max(0, pct))}%"></i></div>`;
export const field = (label, desc, control, cls) =>
  `<div class="field ${cls || ""}"><div class="field-l"><b>${label}</b>${desc ? `<span>${desc}</span>` : ""}</div><div class="field-c">${control}</div></div>`;
/** A titled section of a sectioned card (.card-x): `k` = a KIND key or a .k-* class (the icon's colour), then the rows. */
export const csec = (k, icon, title, body, attrs) =>
  `<div class="csec ${KIND[k] ? KIND[k].c : k}" ${attrs || ""}><div class="csec-h">${ic(icon)}<span>${title}</span></div>${body}</div>`;
/** A small grey note for a whole section, once at its end. */
export const csecNote = (text) => `<p class="csec-note">${text}</p>`;
/** The (i) button and the longer text it shows (hidden until pressed): toggleHelp(root, button) on its data-act="help". */
export const helpBtn = (key, label) => `<button class="icon-btn icon-btn-sm" type="button" data-act="help" data-help="${key}" aria-expanded="false" aria-label="More about ${esc(label)}">${ic("info")}</button>`;
export const helpText = (key, text) => `<p class="help-t" data-helptext="${key}" hidden>${esc(text)}</p>`;
export function toggleHelp(root, b) {
  const open = b.getAttribute("aria-expanded") !== "true";
  b.setAttribute("aria-expanded", String(open));
  root.querySelector(`[data-helptext="${b.dataset.help}"]`).hidden = !open;
}
export const check = (label, on, attrs, type) =>
  `<label class="check"><input type="${type || "checkbox"}" ${on ? "checked" : ""} ${attrs || ""}><span class="ellip">${label}</span></label>`;

// ---------------------------------------------------------------- skeleton, empty state, error
export const skRows = (n) => Array.from({ length: n }, (_, i) =>
  `<div class="sk-row"><div class="sk"></div><div class="sk sk-line" style="width:${60 + (i * 17) % 30}%"></div><div class="sk sk-line" style="width:${40 + (i * 23) % 40}%"></div><div class="sk sk-line"></div></div>`).join("");
export const skCard = (h) =>
  `<div class="card"><div class="card-b" style="display:grid;gap:12px;padding-top:16px"><div class="sk sk-line" style="width:40%"></div>${Array.from({ length: h || 3 }, (_, i) => `<div class="sk sk-line" style="width:${90 - i * 12}%"></div>`).join("")}</div></div>`;
export const skPage = () => `<div class="page"><div class="sk sk-line" style="width:30%;height:calc(26px*var(--s))"></div><div class="grid-3">${skCard(3)}${skCard(4)}${skCard(2)}</div>${skCard(5)}</div>`;
export const empty = (k, icon, title, text, btn) => `<div class="empty">${kic(k, icon)}<h3>${title}</h3><p>${text}</p>${btn || ""}</div>`;

/** "1 h 05 m" / "12 min". */
export const fmt = (m) => {
  m = Math.round(m);
  const h = Math.floor(m / 60), r = m % 60;
  return h ? `${h} h${r ? " " + String(r).padStart(2, "0") + " m" : ""}` : `${r} min`;
};

// ---------------------------------------------------------------- DOM helpers
/** The first element of an HTML string. */
export function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

/** Write text only when it changed (a ticking value must not touch the DOM when nothing changed). */
export function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

/** Measure a CSS length variable in px (sizes are calc() on --s, so they track the interface size). */
export function px(cssVar, fallback = 48) {
  const d = document.createElement("div");
  d.style.cssText = `position:absolute;visibility:hidden;height:var(${cssVar})`;
  document.body.appendChild(d);
  const h = d.offsetHeight;
  d.remove();
  return h || fallback;
}

/** A card that says what failed, with a Retry button. */
export function errorCard(message, onRetry) {
  const node = el(`<div class="page"><div class="card err-card"><h3>This page couldn't load</h3><p class="muted small"></p><button class="btn" type="button">Try again</button></div></div>`);
  node.querySelector("p").textContent = message || "Something went wrong.";
  node.querySelector("button").addEventListener("click", onRetry);
  return node;
}

let toastTimer = 0;
/** One toast at a time. `action` = [label, fn]. */
export function toast(msg, action) {
  const t = document.getElementById("toast");
  t.textContent = "";
  const span = document.createElement("span");
  span.textContent = msg;
  t.append(span);
  if (action) {
    const b = el(`<button class="btn btn-text btn-sm" type="button"></button>`);
    b.textContent = action[0];
    b.addEventListener("click", () => { t.hidden = true; action[1](); });
    t.append(b);
  }
  const x = el(`<button class="icon-btn icon-btn-sm" type="button" style="color:inherit" aria-label="Close">${ic("x")}</button>`);
  x.addEventListener("click", () => { t.hidden = true; });
  t.append(x);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 4000);
}

/**
 * Two-click button: the first click arms it (label changes, a bar drains in CSS), the second click within `ms`
 * confirms. Disarms by itself when the bar is empty (animationend - no timer).
 */
export function confirmButton({ label, confirmLabel = "Click again to confirm", onConfirm, ms = 4000, cls = "btn", title }) {
  const b = el(`<button class="${esc(cls)} confirm" type="button"></button>`);
  b.textContent = label;
  if (title) b.title = title;
  b.style.setProperty("--confirm-ms", ms + "ms");
  const disarm = () => { b.classList.remove("armed"); b.textContent = label; };
  b.addEventListener("click", () => {
    if (!b.classList.contains("armed")) {
      b.classList.add("armed");
      b.textContent = confirmLabel;
      return;
    }
    disarm();
    onConfirm();
  });
  b.addEventListener("animationend", (e) => { if (e.target === b) disarm(); });
  b.addEventListener("blur", disarm);
  return b;
}

// ---------------------------------------------------------------- behaviour of the controls above (delegated, once)
function bump(inp, d) {
  const isH = inp.classList.contains("tp-h"), max = isH ? 24 : 60, step = isH ? 1 : 5;
  inp.value = String((((+inp.value || 0) + d * step) % max + max) % max).padStart(2, "0");
  inp.dispatchEvent(new Event("input", { bubbles: true }));
}

/**
 * seg (buttons with data-act="seg"), stepper (+/-), weekday chips and the hh:mm time picker do their own thing here;
 * they tell the page with events: `seg` on the .seg ({name, value}), `input` on the stepper / time input, `press` on
 * the weekday chip. Call once.
 */
export function installControls(doc = document) {
  doc.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-act]");
    if (!b || b.disabled) return;
    const act = b.dataset.act;
    if (act === "seg") {
      const g = b.closest(".seg");
      for (const x of g.children) x.setAttribute("aria-pressed", String(x === b));
      g.dispatchEvent(new CustomEvent("seg", { bubbles: true, detail: { name: g.dataset.seg, value: b.dataset.v } }));
    } else if (act === "step") {
      const i = b.parentNode.querySelector("input");
      i.value = Math.min(+i.dataset.max, Math.max(+i.dataset.min, (+i.value || 0) + +b.dataset.d));
      i.dispatchEvent(new Event("input", { bubbles: true }));
    } else if (act === "toggle-press") {
      b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true"));
      b.dispatchEvent(new CustomEvent("press", { bubbles: true }));
    }
  });
  doc.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t.matches && t.matches(".tp input") && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault();
      bump(t, e.key === "ArrowUp" ? 1 : -1);
    }
  });
  doc.addEventListener("wheel", (e) => {
    const t = e.target;
    if (t.matches && t.matches(".tp input") && doc.activeElement === t) {
      e.preventDefault();
      bump(t, e.deltaY < 0 ? 1 : -1);
    }
  }, { passive: false });
}
