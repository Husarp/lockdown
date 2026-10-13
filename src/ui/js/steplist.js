// The step-list editor: an ordered list of challenge steps (kind + level + times) that the Anti-Bypass page (and the
// bedtime lists on the Reminders page) edit. Python decides everything that matters (it validates and compares);
// this file only draws the rows and hands back the list. Its "How long" line is display only.
//
//   import { stepListEditor } from "../steplist.js";
//   const ed = stepListEditor({ steps, options, onChange, phrase, label });
//
//   steps     [{id, kind, level, times}]  the list to show (copied; the caller's array is never touched)
//   options   what antibypass_get().challenge holds (Python's challenges.options()): {kinds: [{id, label, levels:
//             [{level, label}], min_level?, max_level?, seconds?}], max_steps, min_times, max_times, phrase_seconds:
//             {base, per_char, grid}, long_seconds}
//   onChange  (steps) => void   called after every edit made by the person (never by set()), with a fresh copy
//   phrase    {length, grid, own_length}  length = what a new phrase step starts with (default 60); grid = the 3x3 grid
//             is on; own_length = non-space length of the own phrase while it is in use (0 = none). Used for the
//             estimate only; change it later with setPhrase().
//   label     aria-label of the list (default "Challenge steps")
//   -> { el, get(), set(steps), setSaved(steps), setPhrase(p), hasPhrase(), setDisabled(bool), setReady(kinds) }
//      el            the element to put on the page (.sl); rows [data-step], "+ Add a step" [data-act="sl-add"], the
//                    "How long: about ..." line [data-k="sl-time"] and the soft note [data-k="sl-note"]
//      get()         a fresh copy of the list as shown
//      set(steps)    show another list (a load, an undo) without calling onChange
//      setSaved(s)   the list that is stored: rows that differ from it (by id) get a mark on the left (.chg)
//      setPhrase(p)  new phrase info, repaints the estimate
//      hasPhrase()   does the list hold a Typed phrase step (the caller then shows the phrase extras)
//      setReady(k)   the secret kinds that are set up (["password", "totp"]); a row of another one says "Not set up yet"
// A secret kind (options.kinds[].secret: Own password, Authenticator code - Anti-Bypass only) has no level and is always
// asked once: its row shows that instead of the level and times boxes.
// A new row gets a fresh random id; a row keeps its id while it is edited, moved or changes kind. At least one row
// stays (the last x is disabled), at most options.max_steps.
// Also exported (pure): stepSeconds, estimate, stepsLooser, newId, hasPhrase.
import { esc, ic, stepper } from "./ui.js";

const PHRASE_MIN = 30;
const PHRASE_MAX = 2000;

const kindOf = (options, id) => options.kinds.find((k) => k.id === id) || options.kinds[0];

/** A fresh step id: 8 lowercase letters / digits (the kind of id Python's challenges.new_id makes). */
export function newId(taken = []) {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  for (;;) {
    let id = "";
    for (let i = 0; i < 8; i++) id += chars[Math.floor(Math.random() * chars.length)];
    if (!taken.includes(id)) return id;
  }
}

export const hasPhrase = (steps) => steps.some((s) => s.kind === "phrase");

/** Seconds ONE of this step takes (same numbers as Python's challenges.step_seconds). */
export function stepSeconds(step, options, phrase = {}) {
  if (step.kind === "phrase") {
    const p = options.phrase_seconds;
    return (p.base + p.per_char * (phrase.own_length || step.level)) * (phrase.grid ? p.grid : 1);
  }
  const k = kindOf(options, step.kind);
  return (k.seconds || [])[step.level] || 0;
}

/** {seconds, text, long, note} of the whole list - the same words as Python's challenges.estimate. */
export function estimate(steps, options, phrase = {}) {
  const seconds = Math.round(steps.reduce((t, s) => t + stepSeconds(s, options, phrase) * s.times, 0));
  const text = seconds < 60 ? "under 1 min" : `about ${Math.floor(seconds / 60 + 0.5)} min`;
  const long = seconds > options.long_seconds;
  return { seconds, text, long, note: long ? `This will take ${text}.` : "" };
}

/** What a save of `now` over `saved` would loosen, as short phrases ([] = nothing: adding, raising and moving are free).
 * Rows are matched by id. Display only - Python makes the real decision. */
export function stepsLooser(saved, now, options) {
  const out = [];
  const label = (s) => `${kindOf(options, s.kind).label} ×${s.times}`;
  for (const o of saved) {
    const n = now.find((r) => r.id === o.id);
    if (!n) out.push(`removed ${label(o)}`);
    else if (n.kind !== o.kind) out.push(`changed ${label(o)} to ${kindOf(options, n.kind).label}`);
    else {
      if (n.level < o.level) out.push(`lowered the level of ${label(o)}`);
      if (n.times < o.times) out.push(`fewer times on ${kindOf(options, o.kind).label}`);
    }
  }
  return out;
}

const copy = (steps) => steps.map((s) => ({ id: s.id, kind: s.kind, level: s.level, times: s.times }));

export function stepListEditor({ steps, options, onChange = () => {}, phrase = {}, label = "Challenge steps" }) {
  let rows = copy(steps);
  let saved = null;
  let ready = null;                            // the secret kinds that are set up (null = not told: no note)
  let info = { length: 60, grid: false, own_length: 0, ...phrase };
  const custom = new Set();                    // ids of phrase rows that show the length box (a length that is no preset)
  const el = document.createElement("div");
  el.className = "sl";
  el.innerHTML = `${STYLE}<div class="sl-list" data-k="sl-list" role="group" aria-label="${esc(label)}"></div>
    <div class="sl-foot"><button class="btn btn-sm" type="button" data-act="sl-add">${ic("plus")}Add a step</button>
      <span class="small muted" data-k="sl-addnote"></span></div>
    <div class="sl-time"><b>How long</b> <span data-k="sl-time"></span> <span class="small muted">The whole list, roughly.</span></div>
    <p class="sl-soft small" data-k="sl-note" hidden></p>`;
  const list = el.querySelector('[data-k="sl-list"]');
  const q = (k) => el.querySelector(`[data-k="${k}"]`);
  const max = options.max_steps;

  const presets = () => kindOf(options, "phrase").levels;
  const isCustom = (r) => r.kind === "phrase" && (custom.has(r.id) || !presets().some((p) => p.level === r.level));

  function rowHtml(r, i) {
    const k = kindOf(options, r.kind);
    const kinds = options.kinds.map((x) => `<option value="${x.id}"${x.id === r.kind ? " selected" : ""}>${esc(x.label)}</option>`).join("");
    const lvOpts = k.levels.map((l) => `<option value="${l.level}"${!isCustom(r) && l.level === r.level ? " selected" : ""}>${esc(l.label)}${r.kind === "phrase" ? ` ${l.level}` : ""}</option>`).join("")
      + (r.kind === "phrase" ? `<option value="custom"${isCustom(r) ? " selected" : ""}>Custom length</option>` : "");
    const box = isCustom(r)
      ? `<span class="sl-len"><input class="input" data-f="len" value="${r.level}" inputmode="numeric" maxlength="4" aria-label="Phrase length in characters, ${PHRASE_MIN} to ${PHRASE_MAX}"></span>` : "";
    const unset = k.secret && ready && !ready.includes(r.kind);
    const middle = k.secret
      ? `<span class="sl-level sl-fixed small${unset ? " sl-unset" : " muted"}" data-k="sl-fixed">${unset ? "Not set up yet - set it up below" : "Asked once"}</span>`
      : `<select class="select select-sm sl-level" data-f="level" aria-label="Level">${lvOpts}</select>${box}
      ${stepper(r.times, "", { sm: true, min: options.min_times, max: options.max_times, label: `Times, step ${i + 1}` })}`;
    return `<div class="sl-row" data-step="${esc(r.id)}" role="group" aria-label="Step ${i + 1}"><span class="sl-n">${i + 1}</span>
      <select class="select select-sm sl-kind" data-f="kind" aria-label="Kind">${kinds}</select>
      ${middle}
      <span class="sl-btns"><button class="icon-btn icon-btn-sm" type="button" data-act="sl-up" aria-label="Move up"${i === 0 ? " disabled" : ""}>${ic("chev-u")}</button>
        <button class="icon-btn icon-btn-sm" type="button" data-act="sl-down" aria-label="Move down"${i === rows.length - 1 ? " disabled" : ""}>${ic("chev-d")}</button>
        <button class="icon-btn icon-btn-sm" type="button" data-act="sl-x" aria-label="Remove this step"${rows.length <= 1 ? " disabled" : ""}>${ic("x")}</button></span></div>`;
  }

  function mark() {
    list.querySelectorAll("[data-step]").forEach((row) => {
      const r = rows.find((x) => x.id === row.dataset.step);
      const o = saved && saved.find((x) => x.id === r.id);
      row.classList.toggle("chg", !!saved && (!o || o.kind !== r.kind || o.level !== r.level || o.times !== r.times));
    });
  }

  function paintFoot() {
    q("sl-addnote").textContent = rows.length >= max ? `${max} steps is the most.` : `Up to ${max} steps. Adding is free.`;
    el.querySelector('[data-act="sl-add"]').disabled = rows.length >= max;
    const e = estimate(rows, options, info);
    q("sl-time").textContent = e.text;
    q("sl-note").textContent = e.note;
    q("sl-note").hidden = !e.note;
    el.classList.toggle("sl-own", !!info.own_length);
    mark();
  }

  function render() {
    list.innerHTML = rows.map(rowHtml).join("");
    paintFoot();
  }

  const changed = () => onChange(copy(rows));
  const rowOf = (node) => {
    const holder = node.closest("[data-step]");
    return holder ? rows.findIndex((r) => r.id === holder.dataset.step) : -1;
  };
  const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

  el.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b || b.disabled) return;
    const i = rowOf(b);
    switch (b.dataset.act) {
      case "sl-add":
        if (rows.length >= max) return;
        rows.push({ id: newId(rows.map((r) => r.id)), kind: "math", level: 0, times: 3 });
        break;
      case "sl-up":
        if (i < 1) return;
        rows.splice(i - 1, 0, rows.splice(i, 1)[0]);
        break;
      case "sl-down":
        if (i < 0 || i >= rows.length - 1) return;
        rows.splice(i + 1, 0, rows.splice(i, 1)[0]);
        break;
      case "sl-x":
        if (rows.length <= 1 || i < 0) return;
        custom.delete(rows[i].id);
        rows.splice(i, 1);
        break;
      default: return;
    }
    render();
    changed();
  });

  el.addEventListener("change", (e) => {
    const t = e.target;
    const i = rowOf(t);
    const f = t.dataset && t.dataset.f;
    if (i < 0 || !f) return;
    const r = rows[i];
    if (f === "kind") {
      const was = r.kind;
      r.kind = t.value;
      custom.delete(r.id);
      if (kindOf(options, r.kind).secret) { r.level = 0; r.times = 1; }      // (no level, asked once)
      else if (r.kind === "phrase") r.level = clamp(Math.round(Number(info.length)) || 60, PHRASE_MIN, PHRASE_MAX);
      else r.level = was === "phrase" ? 0 : Math.min(r.level, kindOf(options, r.kind).levels.length - 1);
    } else if (f === "level") {
      if (t.value === "custom") custom.add(r.id);
      else { custom.delete(r.id); r.level = Number(t.value); }
    } else if (f === "len") {
      const n = Math.round(Number(t.value));
      r.level = clamp(Number.isFinite(n) ? n : r.level, PHRASE_MIN, PHRASE_MAX);
      t.value = String(r.level);
      paintFoot();
      changed();
      return;
    } else return;
    render();
    changed();
  });

  // the times box: the +/- buttons (ui.js) and typing both send `input`; the row is not redrawn (the focus stays)
  el.addEventListener("input", (e) => {
    const t = e.target;
    if (!t.closest || !t.closest(".stepper")) return;
    const i = rowOf(t);
    const n = Math.round(Number(t.value));
    if (i < 0 || !Number.isFinite(n) || n < options.min_times || n > options.max_times) return;   // (mid-typing: wait)
    rows[i].times = n;
    paintFoot();
    changed();
  });
  el.addEventListener("focusout", (e) => {                 // leaving the times box: show what the list really holds
    const t = e.target;
    if (!t.closest || !t.closest(".stepper")) return;
    const i = rowOf(t);
    if (i >= 0 && t.value !== String(rows[i].times)) t.value = String(rows[i].times);
  });

  render();
  return {
    el,
    get: () => copy(rows),
    set(next) {
      rows = copy(next);
      for (const id of [...custom]) if (!rows.some((r) => r.id === id)) custom.delete(id);
      render();
    },
    setSaved(next) {
      saved = next ? copy(next) : null;
      mark();
    },
    setPhrase(next) {
      info = { ...info, ...next };
      paintFoot();
    },
    hasPhrase: () => hasPhrase(rows),
    setReady(kinds) {
      const next = Array.isArray(kinds) ? kinds.slice().sort() : null;
      if (JSON.stringify(next) === JSON.stringify(ready)) return;
      ready = next;
      render();
    },
    setDisabled(off) {
      el.classList.toggle("sl-off", !!off);
    },
  };
}

const STYLE = `<style>
.sl .sl-row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2) var(--sp-1); padding: var(--sp-2) var(--sp-3); border-top: 1px solid var(--line); }
.sl .sl-row:first-child { border-top: 0; }
.sl .sl-row.chg { background: linear-gradient(90deg, var(--brand-soft) 0 3px, transparent 3px); }
.sl .sl-n { width: calc(16px * var(--s)); color: var(--text-3); font-weight: var(--fw-semibold); }
/* the two choices share what is left of the row (up to their full width), so the move / remove buttons stay on it */
.sl .sl-kind { flex: 1 1 0; min-width: calc(72px * var(--s)); max-width: calc(128px * var(--s)); }
.sl .sl-level { flex: 1 1 0; min-width: calc(72px * var(--s)); max-width: calc(116px * var(--s)); }
.sl .sl-len { display: inline-flex; align-items: center; gap: var(--sp-2); }
.sl .sl-len .input { width: calc(58px * var(--s)); height: var(--control-h-sm); font-size: var(--fs-sm); text-align: center; }
.sl .sl-btns { display: inline-flex; gap: 0; margin-left: auto; }
.sl .sl-btns .icon-btn:disabled { opacity: .35; cursor: not-allowed; }
.sl .sl-foot { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-2) var(--sp-3); padding: var(--sp-3) var(--sp-4); border-top: 1px solid var(--line); }
.sl .sl-time { padding: var(--sp-2) var(--sp-4) var(--sp-3); }
.sl .sl-soft { margin: 0; padding: 0 var(--sp-4) var(--sp-3); color: var(--k-limit, var(--text-2)); font-weight: var(--fw-semibold); }
.sl .sl-soft[hidden] { display: none; }
/* a narrow list: kind and level fill the first line, the times and the move / remove buttons share the second */
.sl { container-type: inline-size; }
@container (max-width: 440px) {
  .sl .sl-kind, .sl .sl-level { flex: 1 1 40%; max-width: none; }
  .sl .sl-row .stepper { margin-left: calc(16px * var(--s) + var(--sp-1)); }
}
.sl.sl-own .sl-level, .sl.sl-own .sl-len { opacity: .55; }
.sl .sl-fixed { align-self: center; flex: 2 1 auto; max-width: none; }
.sl.sl-own .sl-fixed { opacity: 1; }
.sl .sl-unset { color: var(--k-limit, var(--text-2)); font-weight: var(--fw-semibold); }
.sl.sl-off .sl-list { opacity: .55; }
</style>`;
