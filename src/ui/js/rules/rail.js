// The blocker rail (R19): "By time / Time limit / Opening limit / Permanent / Temporary" as rows with a switch, a one-line
// summary and a chevron on the left, and ONE open panel on the right (title, subtitle, the editor, a "Next:" hint and
// the summary sentence), so the form never grows downwards. Used by Blocking > Add / Edit and by the group editor.
// The same blockers as stacked cards - the designer's .rule-sec cards, each with its own switch - are a "stack" (used for
// "extra limits for a member").
//
//   import { createRail, createStack } from "./rules/rail.js";
//   const rail = createRail({
//     shared: false,            // a group's editors ("Shared limit", "One pot shared by every member")
//     types: RULE_TYPES,        // which blockers to offer (CUSTOMIZABLE for a member's extras)
//     name: () => "YouTube",    // what the sentence calls the item - call rail.refresh() when the name changes
//     now: () => Date.now(),    // the trusted "now" in ms (only for "Keep (2h 05m left)")
//     requireOne: false,        // validate() refuses a list with no blocker at all
//     compact: false,           // a narrower rail (the group editor)
//     title: (t) => "Also limit this member: " + RULE_NAMES[t].toLowerCase(),   // stack only: the card titles
//     onChange: ({ kind, rule_type }) => {},   // the owner ticked ("tick") or edited ("edit") something
//   });
//   host.append(rail.el);
//   rail.load(rules);           // rule dicts (by rule_type): ticks and loads them, unticks the rest, opens the first
//   rail.forms();               // [{rule_type, ...}] the ticked blockers' FORMS - send these to the Api, not rule dicts
//   rail.check();               // {rules, errors} from the display copy (no Api call)
//   await rail.validate();      // Python's answer {valid, rules, errors, summaries, sentence}; shows the errors
//   rail.showErrors(errors);    // [{rule_type, field, message}] (Python's, from any Api method) under the right fields
//   rail.tick(t, on); rail.open(t); rail.ticked(); rail.editor(t); rail.sentence(); rail.refresh(); rail.focus();
//
// Rules are never built here for saving: Python reads the forms again (src/rule_forms.py) and builds the rule dicts.
// An edit updates the row's summary and the sentence in the same task as the input event - no timer, no Api call.
import { el, esc, ic, kic, sw, setText } from "../ui.js";
import { ld } from "../bridge.js";
import { createEditor } from "./editors.js";
import * as S from "./summary.js";

const KIND_OF = { scheduled: "sched", time_limit: "limit", switch_limit: "opens", permanent: "block", temporary: "temp" };
const GENERAL = '<p class="rule-err" data-zone="rules" role="alert" hidden></p>';

function make(layout, o) {
  const types = o.types || S.RULE_TYPES;
  const shared = !!o.shared;
  const names = { ...S.RULE_NAMES, ...o.names };
  const subtitles = { ...S.RULE_SUBTITLES, ...o.subtitles };
  const now = o.now || (() => Date.now());
  const offText = o.offText || "off";
  const title = (t) => (o.title ? o.title(t) : names[t]);
  const nameText = () => String(typeof o.name === "function" ? o.name() : o.name || "");
  const ticked = new Set();
  const editors = {};
  let openType = null;

  // ---------------------------------------------------------------- markup
  const railRow = (t) => `<div class="card rail-row" data-t="${t}">` +
    `<button type="button" class="rail-main" data-open>${kic(KIND_OF[t])}<span class="rail-txt"><b>${esc(names[t])}</b><span class="sum"></span></span></button>` +
    `${sw(false, `data-tick aria-label="${esc(names[t])}"`)}${ic("chev-r", "chev")}</div>`;
  const stackCard = (t) => `<section class="card rule-sec off" data-t="${t}"><header class="card-h">${kic(KIND_OF[t])}` +
    `<div class="grow"><h3>${esc(title(t))}</h3><span class="sum"></span></div>${sw(false, `data-tick aria-label="${esc(title(t))}"`)}</header>` +
    `<div class="card-b" data-slot></div></section>`;
  const root = layout === "rail"
    ? el(`<div class="rail-wrap"><div class="rail${o.compact ? " compact" : ""}"><div class="rail-side">` +
        `<div class="rail-head"><span class="cap">${o.compact ? "Blockers" : "Blockers · tick any number"}</span><span class="small muted" data-count></span></div>` +
        `${types.map(railRow).join("")}<div class="notice k-brand rail-note">${ic("info")}<p class="small">Blocked when any ticked blocker applies.</p></div></div>` +
        `<section class="card rail-panel"><header class="card-h"><div class="grow"><h3 data-title>Blocker settings</h3><span data-sub></span></div></header>` +
        `<div class="card-b" data-body><p class="rail-blank" data-blank>Tick a blocker on the left to set it up.</p></div>` +
        `<footer class="card-f"><p class="small muted" data-hint></p><p class="small" data-sentence></p></footer></section></div>${GENERAL}</div>`)
    : el(`<div><div class="rule-stack">${types.map(stackCard).join("")}</div>${GENERAL}</div>`);
  const view = {};
  for (const t of types) {
    const row = root.querySelector(`[data-t="${t}"]`);
    view[t] = { row, input: row.querySelector("[data-tick]"), sum: row.querySelector(".sum"),
                chev: row.querySelector(".chev use"), slot: row.querySelector("[data-slot]") };
  }
  const $ = (sel) => root.querySelector(sel);
  const body = $("[data-body]");

  // ---------------------------------------------------------------- editors (made when first needed)
  function editor(t) {
    if (!editors[t]) {
      const ed = editors[t] = createEditor(t, { shared, now, onChange: () => edited(t) });
      if (layout === "rail") { ed.el.hidden = true; body.append(ed.el); } else view[t].slot.append(ed.el);
    }
    return editors[t];
  }

  // ---------------------------------------------------------------- painting
  function paintRow(t) {
    const on = ticked.has(t), v = view[t], ed = editors[t];
    v.input.checked = on;
    v.row.classList.toggle("on", on);
    if (layout === "rail") {
      v.row.classList.toggle("open", openType === t);
      v.row.classList.toggle("bad", on && !!(ed && ed.error));
      v.chev.setAttribute("href", openType === t ? "#i-chev-d" : "#i-chev-r");
      const text = on && ed ? ed.summary() : offText;
      setText(v.sum, text);
      if (v.sum.title !== text) v.sum.title = text;      // (the row may cut a long summary short)
    } else {
      v.row.classList.toggle("off", !on);
      setText(v.sum, on && ed ? ed.summary() : subtitles[t]);
    }
  }
  function paintSentence() {
    if (layout !== "rail") return;
    const parts = types.filter((t) => ticked.has(t) && editors[t]).map((t) => [t, editors[t].summary()]);
    setText($("[data-sentence]"), S.sentenceOf(nameText(), parts));
  }
  function paintAll() {
    for (const t of types) paintRow(t);
    if (layout !== "rail") return;
    setText($("[data-count]"), `${ticked.size} of ${types.length} on`);
    const next = types.find((t) => !ticked.has(t));
    setText($("[data-hint]"), openType == null ? "Blockers combine - it's blocked whenever any ticked one applies."
      : next ? `Next: tick ${names[next]} in the rail to combine it with this one.`
      : "Every blocker is ticked - blocked when any of them applies.");
    paintSentence();
  }
  function show(t) {      // rail: the panel shows this blocker's editor (or the "tick one" blank)
    openType = t;
    for (const x of types) if (editors[x]) editors[x].el.hidden = x !== t;
    $("[data-blank]").hidden = t != null;
    setText($("[data-title]"), t == null ? "Blocker settings" : names[t]);
    setText($("[data-sub]"), t == null ? "" : subtitles[t]);
    if (t != null) editor(t).el.hidden = false;
  }

  // ---------------------------------------------------------------- the owner's actions
  function notify(kind, t) { if (o.onChange) o.onChange({ kind, rule_type: t }); }
  function edited(t) {
    paintRow(t);
    paintSentence();
    notify("edit", t);
  }
  function setTick(t, on) {
    if (on === ticked.has(t)) return false;
    if (on) { ticked.add(t); editor(t); } else ticked.delete(t);
    if (layout === "rail") {
      if (on) show(t);
      else if (openType === t) show(types.find((x) => ticked.has(x)) || null);   // the next ticked one, if any
    }
    return true;
  }
  const api = {
    el: root,
    layout,
    types,
    /** Tick (or untick) a blocker. */
    tick(t, on = true) { if (setTick(t, !!on)) { paintAll(); notify("tick", t); } },
    /** Open a blocker's panel (rail); tick=true also ticks it on, as clicking its row does. */
    open(t, tick = false) {
      if (layout !== "rail") return;
      const changed = tick && setTick(t, true);
      if (!changed) show(t);
      paintAll();
      if (changed) notify("tick", t);
    },
    ticked: () => types.filter((t) => ticked.has(t)),
    editor,
    /** Show these rules (by rule_type): tick + load them, untick and reset the rest, open the first ticked one. */
    load(rules) {
      const byType = {};
      for (const r of rules || []) byType[r.rule_type] = r;
      ticked.clear();
      for (const t of types) {
        if (byType[t]) { editor(t).load(byType[t]); ticked.add(t); } else if (editors[t]) editors[t].load(null);
      }
      if (layout === "rail") show(types.find((t) => ticked.has(t)) || null);
      $('[data-zone="rules"]').hidden = true;       // (each editor cleared its own error when it loaded; a rule that is already wrong shows it)
      paintAll();
    },
    /** The ticked blockers' forms, in display order. */
    forms: () => types.filter((t) => ticked.has(t)).map((t) => editors[t].form()),
    /** {rules, errors} from the display copy - instant, no Api call. */
    check: () => S.checkForms(api.forms(), { shared, now: now(), requireOne: !!o.requireOne }),
    /** Python's answer for the current forms (rules_validate); the errors are shown under their fields. */
    async validate() {
      const res = await ld.call("rules_validate", api.forms(), { shared, name: nameText(), require_one: !!o.requireOne });
      api.showErrors(res.errors);
      return res;
    },
    /** Put [{rule_type, field, message}] under the right fields (anything else clears); opens the first wrong blocker. */
    showErrors(errors) {
      const list = errors || [];
      let first = null;
      for (const t of types) {
        if (!editors[t]) continue;
        const err = ticked.has(t) ? list.find((e) => e.rule_type === t) || null : null;
        editors[t].setError(err);
        if (err && !first) first = t;
      }
      const general = list.find((e) => !e.rule_type || !types.includes(e.rule_type));
      const g = $('[data-zone="rules"]');
      g.textContent = general ? general.message : "";
      g.hidden = !general;
      if (first && layout === "rail" && openType !== first) show(first);
      paintAll();
    },
    /** "YouTube will be blocked: ..." as the panel shows it. */
    sentence: () => (layout === "rail" ? $("[data-sentence]").textContent : ""),
    /** Repaint the sentence (the item's name changed). */
    refresh: paintAll,
    focus() {
      if (layout === "rail" && openType) editor(openType).focus();
      else view[types[0]].input.focus();
    },
    destroy() { root.remove(); },
  };

  // ---------------------------------------------------------------- events (all on our own element)
  root.addEventListener("click", (e) => {
    const open = e.target.closest("[data-open]");
    if (open) api.open(open.closest("[data-t]").dataset.t, true);
  });
  root.addEventListener("change", (e) => {
    const tick = e.target.closest("[data-tick]");
    if (tick) api.tick(tick.closest("[data-t]").dataset.t, tick.checked);
  });

  if (layout === "rail") show(null);
  paintAll();
  return api;
}

export const createRail = (opts = {}) => make("rail", opts);
export const createStack = (opts = {}) => make("stack", opts);
