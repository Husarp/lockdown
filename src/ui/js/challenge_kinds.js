// The per-kind parts of the challenge dialog (js/challenge.js): the progress line + step strip, and the Math (drawn on a
// canvas), Memory grid (Ready, then tap) and Sorting (tap into a row) puzzles. Python makes and judges every puzzle
// (challenges.py): this file only draws what challenge_begin / challenge_submit return and collects the answer. It never
// decides that anything is right - the early "Missed" for a square that wasn't lit is cosmetic, Python judges the taps.
//
// The renderers get a `host` from challenge.js:
//   host.frame(text)            html: the instruction line (+ the note about what passing does) and the progress / strip
//   host.lead(text)             the instruction line's text (to change it later: q(".ch-lead").textContent = ...)
//   host.html(html, buttons)    shows the puzzle, returns the footer (buttons: [{label, primary, disabled, onClick}])
//   host.setButtons(buttons)    swaps the footer buttons
//   host.q(sel)                 querySelector inside the dialog body
//   host.send(value, onError, wrongText)   hands the answer to Python; a right/wrong reply draws the next puzzle itself
//   host.cancelBtn, host.onlyOk(foot), host.closed()
import { esc } from "./ui.js";

export const SHORT = { math: "Math", memory: "Memory", sort: "Sorting", phrase: "Phrase" };

// ---------------------------------------------------------------- plain helpers (node tests)

/** Is the progress line / strip worth showing? Not for one step done once (a single phrase looks as it always did). */
export function showsProgress(run) {
  return !!run && Array.isArray(run.steps) && (run.steps.length > 1 || run.steps.some((s) => s.times > 1));
}

/** The pieces of the progress line: {step: "Step 1 of 3" (only with several steps), label: "Math, Hard", round: "3 of 5"
 *  (only when the step is done more than once)}; null when there is nothing to show. */
export function progressParts(run) {
  if (!showsProgress(run)) return null;
  const s = run.steps[run.step] || run.steps[run.steps.length - 1];
  return {
    step: run.steps.length > 1 ? `Step ${run.step + 1} of ${run.steps.length}` : "",
    label: s.label,
    round: s.times > 1 ? `${Math.min(run.done + 1, s.times)} of ${s.times}` : "",
  };
}

/** "Step 1 of 3 \u00b7 Math, Hard \u00b7 3 of 5"; one step only: "Math, Hard \u00b7 3 of 5"; once only: no "n of N". */
export function progressText(run) {
  const p = progressParts(run);
  return p ? [p.step, p.label, p.round].filter(Boolean).join(" \u00b7 ") : "";
}

/** The strip: [{text: "Math ×5", state: "done" | "cur" | "todo", small: "2/5" (the current one only)}]. */
export function stripModel(run) {
  if (!showsProgress(run)) return [];
  return run.steps.map((s, i) => ({
    text: `${SHORT[s.kind] || s.kind} ×${s.times}`,
    state: i < run.step ? "done" : i === run.step ? "cur" : "todo",
    small: i === run.step ? `${Math.min(run.done, s.times)}/${s.times}` : "",
  }));
}

/** The progress line + strip as html ("" when there is nothing to show). */
export function runHeaderHtml(run) {
  const p = progressParts(run);
  if (!p) return "";
  const line = [p.step && `<b>${esc(p.step)}</b>`, esc(p.label), p.round && `<b>${esc(p.round)}</b>`].filter(Boolean).join(" \u00b7 ");
  const chips = stripModel(run).map((c) =>
    `<span class="ch-stp ${c.state}">${c.state === "done" ? "\u2713 " : ""}${esc(c.text)}${c.small ? ` <small>${esc(c.small)}</small>` : ""}</span>`).join("");
  return `<div class="ch-prog" aria-live="polite">${line}</div><div class="ch-strip">${chips}</div>`;
}

/** A shuffled copy of `ids` (Fisher-Yates). The ids themselves are untouched, so a tile keeps the item number Python
 *  knows it by; only the order they are shown in changes. `rnd` returns [0, 1). */
export function shuffleKeepIds(ids, rnd = Math.random) {
  const out = ids.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.min(i, Math.floor(rnd() * (i + 1)));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/** Only the digits of `s` (at most 15 of them: the answer is a whole number, never negative). */
export const digitsOnly = (s) => String(s == null ? "" : s).replace(/[^0-9]/g, "").slice(0, 15);

/** Is the last tap one that can't be right? A square that wasn't lit; on the ordered level also one that isn't next. */
export function missedAt(lit, ordered, taps) {
  if (!taps.length) return false;
  const last = taps[taps.length - 1];
  if (!lit.includes(last)) return true;
  return ordered ? lit[taps.length - 1] !== last : false;
}

/** The sorting level that re-shuffles the tiles still to tap after each tap (Hard). */
export const reshufflesPool = (level) => level === 2;

// ---------------------------------------------------------------- math

/** Draw the sum on `canvas` in the theme's colours, crisp on a HiDPI screen. A picture on purpose: nothing to select,
 *  copy or read out of the page. */
export function drawExpr(canvas, expr) {
  const cs = getComputedStyle(canvas);
  const root = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--s")) || 1;
  const size = 32 * root, padX = 14 * root, h = Math.round(size * 1.7);
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  const font = `600 ${size}px ${cs.fontFamily || "sans-serif"}`;
  const c = canvas.getContext("2d");
  c.font = font;
  const w = Math.ceil(c.measureText(expr).width + padX * 2);
  canvas.width = Math.ceil(w * dpr);
  canvas.height = Math.ceil(h * dpr);
  canvas.style.width = w + "px";
  canvas.style.height = h + "px";
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, w, h);
  c.fillStyle = cs.color || "#888";
  c.globalAlpha = 0.07;                                // faint slanted lines behind the sum (as in the mockup)
  c.lineWidth = 1;
  c.strokeStyle = c.fillStyle;
  for (let x = -h; x < w; x += 10) { c.beginPath(); c.moveTo(x, h); c.lineTo(x + h * 0.2, 0); c.stroke(); }
  c.globalAlpha = 1;
  c.font = font;
  c.textBaseline = "middle";
  c.textAlign = "center";
  c.fillText(expr, w / 2, h / 2 + size * 0.04);
}

export function mountMath(host, data) {
  const find = !!data.find_x;
  const foot = host.html(`
    ${host.frame(find ? "Find x and type it." : "Work it out and type the answer.")}
    <div class="ch-sum"><canvas class="ch-pic" role="img" aria-label="A sum, drawn as a picture"></canvas>
      <span class="ch-eq">${find ? "x =" : "="}</span>
      <input class="input ch-ans" inputmode="numeric" autocomplete="off" spellcheck="false" maxlength="15" placeholder="?" aria-label="Answer">
    </div>
    <div class="small ch-msg-line"><span class="muted ch-msg">Type the answer and press Enter. Paste is off.</span></div>`,
    [host.cancelBtn, { label: "Next", primary: true, disabled: true, onClick: go }]);
  const go_ = foot.lastChild, input = host.q(".ch-ans"), msg = host.q(".ch-msg");
  drawExpr(host.q(".ch-pic"), data.expr || "");
  if (host.message) { msg.textContent = host.message; msg.classList.add("err"); }
  let busy = false;
  function go() {
    const text = digitsOnly(input.value);
    if (!text || busy) return;
    busy = true;
    go_.disabled = true;
    input.disabled = true;
    host.send(text, (err, gone) => {
      busy = false;
      input.disabled = false;
      input.value = "";
      go_.disabled = true;
      msg.textContent = err;
      msg.classList.add("err");
      if (gone) host.onlyOk(host.foot()); else input.focus();
    });
  }
  input.addEventListener("input", () => {
    const clean = digitsOnly(input.value);
    if (clean !== input.value) input.value = clean;
    go_.disabled = busy || !clean;
  });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  input.focus();
}

// ---------------------------------------------------------------- memory grid

export function mountMemory(host, data) {
  const size = data.size, lit = data.lit || [], ordered = !!data.ordered, n = lit.length;
  const cells = Array.from({ length: size * size }, (_, i) => {
    const k = lit.indexOf(i);
    return `<button type="button" class="ch-cell${k >= 0 ? " lit" : ""}" data-i="${i}" aria-label="Square ${i + 1}">${k >= 0 && ordered ? k + 1 : ""}</button>`;
  }).join("");
  const showText = `Watch the squares that light up${ordered ? " and remember their order" : ""}. Press Ready when you have them.`;
  const foot = host.html(`
    ${host.frame(showText)}
    <div class="ch-mem g${size}" role="group" aria-label="Memory grid">${cells}</div>
    <div class="small hstack"><span class="muted grow ch-msg"></span><span class="num muted ch-count"></span></div>`,
    [host.cancelBtn, { label: "Ready", primary: true, onClick: ready }]);
  const btns = [...host.q(".ch-mem").querySelectorAll(".ch-cell")];
  const msg = host.q(".ch-msg"), count = host.q(".ch-count"), lead = host.q(".ch-lead");
  if (host.message) { msg.textContent = host.message; msg.classList.add("err"); }
  const taps = [];
  let phase = "show", busy = false;
  foot.lastChild.focus();

  function ready() {
    if (phase !== "show") return;
    phase = "recall";
    for (const b of btns) { b.classList.remove("lit"); b.textContent = ""; }
    if (lead) lead.textContent = host.lead(`Tap the squares that were lit${ordered ? ", in the order they were numbered" : ""}.`);
    msg.textContent = "";
    msg.classList.remove("err");
    count.textContent = `0 / ${n}`;
    host.setButtons([host.cancelBtn, { label: "Start over", onClick: clear }]);
  }
  function clear() {
    if (busy) return;
    taps.length = 0;
    for (const b of btns) { b.classList.remove("pick", "miss"); b.textContent = ""; }
    msg.textContent = "";
    count.textContent = `0 / ${n}`;
  }
  function finish(missed) {
    busy = true;
    for (const b of btns) b.disabled = true;
    if (missed) { msg.textContent = "Missed - a new pattern"; msg.classList.add("err"); }
    const send = () => {
      if (host.closed()) return;
      host.send(taps.slice(), (err, gone) => {
        busy = false;
        for (const b of btns) b.disabled = false;
        clear();
        msg.textContent = err;
        msg.classList.add("err");
        if (gone) host.onlyOk(host.foot());
      }, missed ? "Missed - a new pattern" : "");
    };
    if (missed) setTimeout(send, 450); else send();
  }
  btns.forEach((b, i) => b.addEventListener("click", () => {
    if (phase !== "recall" || busy || taps.includes(i)) return;
    taps.push(i);
    b.classList.add("pick");
    if (ordered) b.textContent = taps.length;
    count.textContent = `${taps.length} / ${n}`;
    if (missedAt(lit, ordered, taps)) { b.classList.remove("pick"); b.classList.add("miss"); finish(true); }
    else if (taps.length >= n) finish(false);
  }));
}

// ---------------------------------------------------------------- sorting

export function mountSort(host, data) {
  const items = data.items || [];
  const reshuffle = reshufflesPool(data.run && data.run.level);
  const ids = items.map((_, i) => i);
  const foot = host.html(`
    ${host.frame("Tap the numbers from smallest to largest.")}
    <div class="ch-sorta" aria-label="Your order, smallest first"></div>
    <div class="ch-pool" role="group" aria-label="Numbers to sort"></div>
    <div class="small"><span class="muted ch-msg">${reshuffle ? "The rest move around after every tap." : "Tap the smallest first."}</span></div>`,
    [host.cancelBtn, { label: "Clear", onClick: clear }]);
  const answer = host.q(".ch-sorta"), pool = host.q(".ch-pool"), msg = host.q(".ch-msg");
  if (host.message) { msg.textContent = host.message; msg.classList.add("err"); }
  let rest = ids.slice(), taken = [], busy = false;
  const tile = (id, cls) => `<button type="button" class="ch-tile${cls ? " " + cls : ""}" data-id="${id}"${cls ? " disabled" : ""}>${esc(items[id])}</button>`;
  function paint() {
    answer.innerHTML = taken.map((id) => tile(id, "in")).join("");
    pool.innerHTML = rest.map((id) => tile(id)).join("");
  }
  function clear() {
    if (busy) return;
    rest = ids.slice();
    taken = [];
    paint();
  }
  pool.addEventListener("click", (e) => {
    const b = e.target.closest(".ch-tile");
    if (!b || busy) return;
    const id = Number(b.dataset.id);
    if (!rest.includes(id)) return;
    taken.push(id);
    rest = rest.filter((x) => x !== id);
    if (reshuffle) rest = shuffleKeepIds(rest);
    paint();
    if (!rest.length) {
      busy = true;
      host.send(taken.slice(), (err, gone) => {
        busy = false;
        clear();
        msg.textContent = err;
        msg.classList.add("err");
        if (gone) host.onlyOk(host.foot());
      });
    }
  });
  paint();
}
