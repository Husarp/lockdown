// The five blocker editors (R19), in the designer's look: By time, Time limit, Opening limit, Permanent, Temporary.
// One editor = one blocker's settings (the old gui/rule_editors.py classes). They are reusable pieces: rail.js puts them
// in the Add/Edit and Groups rail (and in the "extra limits for a member" stack); a page can also use one alone.
//
//   const ed = createEditor("time_limit", { shared: false, now: () => Date.now(), onChange: (ed) => {} });
//   host.append(ed.el);
//   ed.load(rule);      // a saved / drafted rule dict (null: the starting values). Does not call onChange.
//   ed.form();          // what the owner typed, as a FORM {rule_type, ...} - send THAT to the Api (rules_validate,
//                       // the Draft methods), never a rule built here: Python reads the form again (src/rule_forms.py)
//   ed.check();         // {rule} | {error: {rule_type, field, message}}: the display copy's reading (summary.js)
//   ed.summary();       // the one-liner for the rail row ("Allowed only 09:00-17:00 Every day")
//   ed.setError(err);   // show {field, message} under that field (null clears it) - e.g. what Python answered
//   ed.focus();
//   opts.shared   a group's editor ("Shared limit", "One pot shared by every member")
//   opts.now      the trusted "now" in ms - only for the "Keep (2h 05m left)" label and refusing an ended keep
//   opts.onChange called after every edit by the owner, in the same task as the input event (no timers, no Api call)
//
// While the owner types, the editor checks its own form with the display copy (summary.js) and shows the error under
// the field; the authority is still Python. Needs ui.installControls() (the shell calls it once) for the segmented
// buttons, steppers, weekday chips and time pickers.
import { el, ic, sw, seg, stepper, tp, days as dayChips, check as checkbox } from "../ui.js";
import * as S from "./summary.js";

// The stylesheet comes with the first editor (only the pages that edit blockers pay for it); the module waits for it,
// so nothing is ever painted unstyled.
if (typeof document !== "undefined" && !document.querySelector("link[data-rules-css]")) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = new URL("../../css/rules.css", import.meta.url).href;
  link.dataset.rulesCss = "";
  document.head.append(link);
  await new Promise((resolve) => { link.onload = link.onerror = resolve; });
}

const $ = (root, sel) => root.querySelector(sel);
const $$ = (root, sel) => [...root.querySelectorAll(sel)];
const setSeg = (g, v) => { for (const b of g.children) b.setAttribute("aria-pressed", String(b.dataset.v === v)); };
const getSeg = (g) => { const b = [...g.children].find((x) => x.getAttribute("aria-pressed") === "true"); return b ? b.dataset.v : ""; };
const errP = (zone) => `<p class="rule-err" data-zone="${zone}" role="alert" hidden></p>`;
const hint = (t) => `<p class="rule-hint">${t}</p>`;
const PERIOD_TITLE = { day: "per day", week: "per week", month: "per month" };

/** The three "per day / week / month" boxes; `what` names them for screen readers. */
const periodBoxes = (what, placeholder) => S.PERIODS.map((p) =>
  `<label class="rule-amt"><input class="input rule-box" data-f="${p}" autocomplete="off" spellcheck="false" placeholder="${placeholder}" aria-label="${what} ${PERIOD_TITLE[p]}"><span class="rule-lbl">${PERIOD_TITLE[p]}</span></label>`).join("");

// ---------------------------------------------------------------- By time
const hhmm = /^(\d{1,2}):(\d{2})$/;
const toMin = (t) => { const m = hhmm.exec(t); return m ? +m[1] * 60 + +m[2] : NaN; };
/** The "hh:mm" in a time picker (padded; left as it is when something is missing, so the error says so). */
export function readTime(box) {
  const h = $(box, ".tp-h").value.trim(), m = $(box, ".tp-m").value.trim();
  return h && m ? `${h.padStart(2, "0")}:${m.padStart(2, "0")}` : `${h}:${m}`;
}
const windowRow = (w) => `<div class="win-row" data-win role="group">${dayChips(Array.from({ length: 7 }, (_, i) => w.days.includes(i)))}` +
  `<span class="hstack">${tp(w.start, "From")}<span class="range-dash">&ndash;</span>${tp(w.end, "To")}</span>` +
  `<span class="small faint" data-next hidden>ends next day</span><span class="grow"></span>` +
  `<button type="button" class="icon-btn icon-btn-sm" data-remove aria-label="Remove this time window">${ic("trash")}</button></div>`;

function hoursEditor(root, { shared }) {
  root.innerHTML = `<div class="rule-row">${seg("mode", [["allow", "Allow only"], ["block", "Block"]], "allow", 'aria-label="Allow only or Block"')}</div>` +
    hint("Allow only: the site / app works only in these times. Block: it's blocked in these times. An end before the start means overnight (22:00 to 07:00).") +
    `<div data-windows></div>${errP("windows")}` +
    `<div class="rule-row"><button type="button" class="btn btn-sm" data-add>${ic("plus")}Add time window</button></div>` +
    `<div class="rule-row"><span>During blocked times, still allow</span>${stepper(0, "minutes", { step: 5, min: 0, max: 1440, label: "Minutes still allowed in a blocked time" })}</div>${errP("allowance")}` +
    hint("0 = fully blocked. Otherwise you can still use it this many minutes in each blocked period; they start again in the next one.") +
    (shared ? `<div class="rule-row">${checkbox("One pot shared by every member", true, "data-pot")}</div>` +
      hint("On: the minutes are spent by whichever member you use, and run out for all of them together. Off: every member gets this many minutes of its own.") : "");
  const list = $(root, "[data-windows]"), mode = $(root, ".seg"), allowance = $(root, ".stepper input");
  const rows = () => $$(list, "[data-win]");
  const refresh = (row) => {       // "ends next day": an end at or before the start runs past midnight
    const a = toMin(readTime($$(row, ".tp")[0])), b = toMin(readTime($$(row, ".tp")[1]));
    $(row, "[data-next]").hidden = !(a >= 0 && b >= 0 && b <= a);
  };
  const renumber = () => rows().forEach((r, i) => r.setAttribute("aria-label", `Time window ${i + 1}`));
  const readWindow = (row) => ({
    days: $$(row, ".days button").map((b, i) => (b.getAttribute("aria-pressed") === "true" ? i : -1)).filter((i) => i >= 0),
    start: readTime($$(row, ".tp")[0]), end: readTime($$(row, ".tp")[1]),
  });
  return {
    fill(form) {
      list.innerHTML = form.windows.map(windowRow).join("");
      rows().forEach(refresh);
      renumber();
      setSeg(mode, form.mode);
      allowance.value = form.allowance;
      if (shared) $(root, "[data-pot]").checked = form.pot !== false;
    },
    read() {
      return { mode: getSeg(mode), windows: rows().map(readWindow), allowance: allowance.value, ...(shared ? { pot: $(root, "[data-pot]").checked } : {}) };
    },
    edited(e) { const row = e.target.closest && e.target.closest("[data-win]"); if (row) refresh(row); },
    click(e, touch) {
      if (e.target.closest("[data-add]")) {
        list.insertAdjacentHTML("beforeend", windowRow({ days: [0, 1, 2, 3, 4, 5, 6], start: "09:00", end: "17:00" }));
        renumber();
        $(rows().pop(), ".tp-h").focus();
        touch();
      } else if (e.target.closest("[data-remove]")) {
        e.target.closest("[data-win]").remove();
        renumber();
        $(root, "[data-add]").focus();
        touch();
      }
    },
    locate(field) {
      const w = /^windows\.(\d+)$/.exec(field);
      if (w) return { zone: "windows", marks: rows().slice(+w[1], +w[1] + 1) };
      if (field === "allowance") return { zone: "allowance", marks: [allowance] };
      return { zone: "windows", marks: field === "windows" ? rows() : [] };
    },
    focus: () => $(root, ".seg button[aria-pressed=true]").focus(),
  };
}

// ---------------------------------------------------------------- Time limit
const QUICK = [["Mon–Fri", 0, 4], ["Sat–Sun", 5, 6], ["Every day", 0, 6]];

function limitEditor(root, { shared }) {
  root.innerHTML = `<div class="rule-row"><span class="rule-lbl">${shared ? "Shared limit" : "At most"}</span>${periodBoxes(shared ? "Shared time limit" : "Time limit", "e.g. 2h")}</div>${errP("periods")}` +
    `<div class="rule-row">${seg("limitMode", [["same", "Same every day"], ["grid", "Mon – Sun"]], "same", 'aria-label="Same limit every day or one per weekday"')}</div>` +
    `<div class="stack-sm" data-grid hidden><div class="day-grid">${S.DAY_NAMES.map((d, i) =>
      `<label>${d.slice(0, 3)}<input class="input" data-f="weekdays.${i}" autocomplete="off" spellcheck="false" aria-label="${d} limit"></label>`).join("")}</div>` +
    `<div class="rule-row rule-fill small"><span>Fill</span><input class="input" data-fill autocomplete="off" spellcheck="false" aria-label="Amount to fill in"><span>into</span>` +
    QUICK.map(([l, a, b]) => `<button type="button" class="btn btn-sm" data-fill-days="${a}-${b}">${l}</button>`).join("") + `</div>` +
    hint("Give each weekday its own daily limit, e.g. 3h Monday to Friday and 2h at the weekend. Leave a day empty for no daily limit that day. A day starts at the limit reset time: with a 03:00 reset, Saturday 01:00 still counts as Friday.") +
    `${errP("weekdays")}</div>` +
    hint(`${shared ? "For all members together. " : ""}E.g. 45m, 2h, 1h30; leave empty for no limit. Counted while the app is in front / the site is the active browser tab; resets at the limit reset time (Settings).`);
  const boxes = Object.fromEntries(S.PERIODS.map((p) => [p, $(root, `[data-f="${p}"]`)]));
  const wk = S.DAY_NAMES.map((_, i) => $(root, `[data-f="weekdays.${i}"]`));
  const fill = $(root, "[data-fill]"), grid = $(root, "[data-grid]"), mode = $(root, ".seg");
  const show = (perDay) => { grid.hidden = !perDay; boxes.day.disabled = perDay; };
  return {
    fill(form) {
      for (const p of S.PERIODS) boxes[p].value = form[p];
      wk.forEach((b, i) => { b.value = form.weekdays[i]; });
      fill.value = "";
      setSeg(mode, form.same ? "same" : "grid");
      show(!form.same);
    },
    read() {
      return { same: getSeg(mode) === "same", day: boxes.day.value, week: boxes.week.value, month: boxes.month.value, weekdays: wk.map((b) => b.value) };
    },
    seg(detail) {      // the owner flipped Same every day <-> Mon-Sun: carry the amount over (as the old editor did)
      if (detail.name !== "limitMode") return;
      if (detail.value === "grid") {
        const t = boxes.day.value.trim();
        wk.forEach((b) => { b.value = t; });
        fill.value = t;
        boxes.day.value = "";
        show(true);
      } else {
        const first = wk.map((b) => b.value.trim()).find(Boolean) || "";
        show(false);
        boxes.day.value = first;
      }
    },
    click(e, touch) {
      const b = e.target.closest("[data-fill-days]");
      if (!b) return;
      const [a, z] = b.dataset.fillDays.split("-").map(Number);
      for (let d = a; d <= z; d++) wk[d].value = fill.value.trim();
      touch();
    },
    locate(field) {
      if (field === "any") return { zone: "periods", marks: S.PERIODS.map((p) => boxes[p]) };
      if (field.startsWith("weekdays")) return { zone: "weekdays", marks: wk.filter((b) => b.dataset.f === field) };
      return { zone: "periods", marks: boxes[field] ? [boxes[field]] : [] };
    },
    focus: () => (boxes.day.disabled ? wk[0] : boxes.day).focus(),
  };
}

// ---------------------------------------------------------------- Opening limit
function switchEditor(root, { shared }) {
  root.innerHTML = `<div class="rule-row"><span class="rule-lbl">Open at most</span>${periodBoxes("Openings", "none")}</div>${errP("periods")}` +
    hint(`${shared ? "All members together. " : ""}Leave empty for no limit; the next opening after a limit is blocked.`) +
    `<div class="rule-row"><span class="rule-lbl">What counts</span>${seg("counts", [["visit", "Launches / new visits"], ["switch", "Every switch"]], "visit", 'aria-label="What counts as opening it"')}</div>` +
    `<div class="rule-row" data-visit><span>Apps: each start of the program. Sites: coming back after</span><input class="input rule-box" data-f="gap" autocomplete="off" aria-label="Minutes away"><span>minutes away</span></div>${errP("gap")}` +
    hint("Clicking away and back sooner than that doesn't count as a new visit.") +
    `<p class="rule-hint" data-switch hidden>Every time it comes to the front counts (alt-tab, clicking its window or browser tab).</p>`;
  const boxes = Object.fromEntries(S.PERIODS.map((p) => [p, $(root, `[data-f="${p}"]`)]));
  const gap = $(root, '[data-f="gap"]'), counts = $(root, ".seg"), visit = $(root, "[data-visit]"), note = $(root, "[data-switch]");
  const show = (v) => { visit.hidden = v !== "visit"; note.hidden = v === "visit"; };
  return {
    fill(form) {
      for (const p of S.PERIODS) boxes[p].value = form[p];
      gap.value = form.gap;
      setSeg(counts, form.counts);
      show(form.counts);
    },
    read() { return { counts: getSeg(counts), day: boxes.day.value, week: boxes.week.value, month: boxes.month.value, gap: gap.value }; },
    seg(detail) { if (detail.name === "counts") show(detail.value); },
    locate(field) {
      if (field === "gap") return { zone: "gap", marks: [gap] };
      if (field === "any") return { zone: "periods", marks: S.PERIODS.map((p) => boxes[p]) };
      return { zone: "periods", marks: boxes[field] ? [boxes[field]] : [] };
    },
    focus: () => boxes.day.focus(),
  };
}

// ---------------------------------------------------------------- Permanent
function permanentEditor(root) {
  root.innerHTML = `<p class="rule-lbl">Always blocked.</p>`;
  return { fill() {}, read: () => ({}), locate: () => ({ zone: "general", marks: [] }), focus() {} };
}

// ---------------------------------------------------------------- Temporary
function temporaryEditor(root, { now }) {
  root.innerHTML = `<div class="rule-row"><span class="rule-lbl">Block for</span><div class="pills" role="group" aria-label="Block for">` +
    S.DURATIONS.map(([label, m]) => `<button type="button" class="pill" data-choice="${m}" aria-pressed="false">${label}</button>`).join("") +
    `<button type="button" class="pill" data-choice="custom" aria-pressed="false">Custom…</button>` +
    `<button type="button" class="pill" data-choice="keep" aria-pressed="false" hidden></button></div></div>` +
    `<div class="rule-row" data-custom hidden><input class="input rule-box" data-f="amount" autocomplete="off" inputmode="numeric" aria-label="Custom amount"> ` +
    `${seg("unit", S.UNITS.map(([u]) => [u, u]), "hours", 'aria-label="Unit"')}</div>${errP("choice")}${errP("amount")}` +
    `<p class="rule-hint" data-note>starting when saved</p>`;
  const pills = $$(root, "[data-choice]"), keepPill = $(root, '[data-choice="keep"]');
  const custom = $(root, "[data-custom]"), amount = $(root, '[data-f="amount"]'), unit = $(root, ".seg"), note = $(root, "[data-note]");
  let choice = "60", keepUntil = null;
  const choose = (c) => {
    choice = c;
    for (const p of pills) p.setAttribute("aria-pressed", String(p.dataset.choice === c));
    custom.hidden = c !== "custom";
    note.hidden = c === "keep";
  };
  return {
    fill(form) {
      keepUntil = form.keep_until || null;
      keepPill.hidden = !keepUntil;
      if (keepUntil) {
        const left = (S.parseStamp(keepUntil) - now()) / 1000;
        keepPill.textContent = `Keep (${Number.isNaN(left) ? "…" : S.durationText(left)} left)`;
      }
      amount.value = form.amount;
      setSeg(unit, form.unit);
      choose(form.choice);
    },
    read() { return { choice, amount: amount.value, unit: getSeg(unit), keep_until: keepUntil }; },
    click(e, touch) {
      const p = e.target.closest("[data-choice]");
      if (!p) return;
      choose(p.dataset.choice);
      if (p.dataset.choice === "custom") amount.focus();
      touch();
    },
    locate(field) {
      if (field === "amount") return { zone: "amount", marks: [amount] };
      return { zone: "choice", marks: [] };
    },
    focus: () => (pills.find((p) => p.getAttribute("aria-pressed") === "true") || pills[0]).focus(),
  };
}

const BUILDERS = { scheduled: hoursEditor, time_limit: limitEditor, switch_limit: switchEditor, permanent: permanentEditor, temporary: temporaryEditor };

/** One blocker's editor (see the top of this file). */
export function createEditor(type, opts = {}) {
  const build = BUILDERS[type];
  if (!build) throw new Error(`Unknown kind of blocker: ${type}`);
  const shared = !!opts.shared;
  const now = opts.now || (() => Date.now());
  const root = el(`<div class="rule-ed" data-type="${type}"></div>`);
  const impl = build(root, { shared, now });
  root.insertAdjacentHTML("beforeend", errP("general"));
  const ed = { type, el: root, shared, error: null, onChange: opts.onChange || null, now };

  ed.form = () => ({ rule_type: type, ...impl.read() });
  ed.check = () => S.checkForm(ed.form(), { shared, now: now() });
  ed.summary = () => { const c = ed.check(); return c.rule ? S.summary(c.rule) : S.BAD_SETTINGS; };
  ed.load = (rule) => { impl.fill(S.formFromRule(rule, shared, type)); ed.setError(null); ed.setError(ed.check().error || null); };
  ed.focus = () => impl.focus();
  ed.setError = (err) => {
    if (!err && !ed.error) return;
    if (err && ed.error && err.field === ed.error.field && err.message === ed.error.message) return;
    for (const p of $$(root, ".rule-err")) { p.hidden = true; p.textContent = ""; }
    for (const m of $$(root, "[aria-invalid]")) m.removeAttribute("aria-invalid");
    ed.error = err || null;
    if (!err) return;
    const where = impl.locate(err.field || "");
    const p = $(root, `[data-zone="${where.zone}"]`) || $(root, '[data-zone="general"]');
    p.textContent = err.message;
    p.hidden = false;
    for (const m of where.marks) m.setAttribute("aria-invalid", "true");
  };

  // an edit by the owner: re-read the form, show / clear the error under the field, tell the owner of the editor -
  // all in the event's own task, so the summary is right in the same frame
  const touch = () => {
    ed.setError(ed.check().error || null);
    if (ed.onChange) ed.onChange(ed);
  };
  const edited = (e) => { if (impl.edited) impl.edited(e); touch(); };
  root.addEventListener("input", edited);       // (a checkbox, a stepper button and a time picker's arrow keys all fire it too)
  root.addEventListener("press", edited);
  root.addEventListener("seg", (e) => { if (impl.seg) impl.seg(e.detail); touch(); });
  if (impl.click) root.addEventListener("click", (e) => impl.click(e, touch));

  ed.load(null);
  return ed;
}
