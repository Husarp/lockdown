// Rule editor forms <-> rule dicts, and the words for them. DISPLAY-ONLY: the page uses this to show an error and a
// one-line summary while the owner types (no round trip per keystroke). The truth is src/rule_forms.py - every save goes
// through it - and tests/fixtures/rule_cases.json (run by tests/test_rebuild_rule_forms.py AND
// tests/ui/js/summary.test.mjs) keeps the two in step. Nothing here decides whether a change loosens a block.
//
// A form is a plain object, all amounts as typed text (see rule_forms.py for the same description):
//   scheduled     {rule_type, mode: "allow"|"block", windows: [{days: [0..6], start, end}], allowance: "0", pot?: bool}
//   time_limit    {rule_type, day, week, month: "30m", same: bool, weekdays: [7 texts Mon..Sun]}
//   switch_limit  {rule_type, day, week, month: "10", counts: "visit"|"switch", gap: "5"}
//   temporary     {rule_type, choice: "15".."1440" | "custom" | "keep", amount, unit: "days"|"hours"|"minutes", keep_until}
//   permanent     {rule_type}
// A rule is what the Draft stores (the same dict Python builds): {rule_type, schedule (JSON text), allowance_min, ...}.
// No DOM in this file: it runs under node (tests/ui/js/summary.test.mjs).

export const RULE_TYPES = ["scheduled", "time_limit", "switch_limit", "permanent", "temporary"];
export const RULE_NAMES = { scheduled: "By time", time_limit: "Time limit", switch_limit: "Opening limit", permanent: "Permanent", temporary: "Temporary" };
export const RULE_SUBTITLES = {
  scheduled: "allowed only inside these windows - or blocked inside them",
  time_limit: "a cap on how long per day / week",
  switch_limit: "a cap on how often it's opened",
  permanent: "blocked all the time, until removed",
  temporary: "blocked for a while from now",
};
/** What a group member can be given on top of the group's rules (CustomizeMember). */
export const CUSTOMIZABLE = ["scheduled", "time_limit", "switch_limit", "temporary"];
/** The temporary block's presets: [label, minutes]. */
export const DURATIONS = [["15 min", 15], ["30 min", 30], ["1 hour", 60], ["2 hours", 120], ["3 hours", 180], ["4 hours", 240], ["8 hours", 480], ["24 hours", 1440]];
export const UNITS = [["days", 1440], ["hours", 60], ["minutes", 1]];       // biggest first
export const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const PERIODS = ["day", "week", "month"];
export const PERIOD_LABELS = { day: "per day", week: "per week", month: "per month" };
const PERIOD_MAX_MIN = { day: 1440, week: 7 * 1440, month: 31 * 1440 };
const TIME_FIELDS = { day: "daily_limit_min", week: "weekly_limit_min", month: "monthly_limit_min" };
const OPEN_FIELDS = { day: "daily_switch_limit", week: "weekly_switch_limit", month: "monthly_switch_limit" };
const DAY_LIMITS_FIELD = "daily_limit_days";
const MAX_TEMPORARY_MIN = 30 * 1440;
const DEFAULT_VISIT_GAP_MIN = 5;
export const BAD_SETTINGS = "check the settings";

/** A form the owner has to fix: .message is the sentence to show, .field the field it belongs to. */
export class RuleFormError extends Error {
  constructor(message, field = "") {
    super(message);
    this.field = field;
  }
}

// ---------------------------------------------------------------- amounts
const pyRepr = (s) => {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  return q + s.replace(/\\/g, "\\\\").split(q).join("\\" + q) + q;
};
/** '45' / '45m' / '2h' / '2h30' / '2h 30m' / '1:30' -> minutes; '' -> null. Throws Error. */
export function parseDuration(text) {
  text = String(text).trim().toLowerCase().replace(/ /g, "");
  if (!text) return null;
  const m = /^(?:(\d+)h)?(?:(\d+)m?)?$/.exec(text) || /^(\d+):(\d{2})$/.exec(text);
  if (!m || !(m[1] || m[2])) throw new Error(`Can't read ${pyRepr(text)} - write e.g. 45m, 2h or 1h30.`);
  return parseInt(m[1] || "0", 10) * 60 + parseInt(m[2] || "0", 10);
}

export function formatDuration(minutes) {
  if (!minutes) return "";
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return h && m ? `${h}h${String(m).padStart(2, "0")}` : h ? `${h}h` : `${m}m`;
}

/** "1h 05m" / "45m" / "2d 3h". */
export function durationText(seconds) {
  const minutes = Math.max(0, Math.floor(seconds / 60));
  let h = Math.floor(minutes / 60);
  const m = minutes % 60, d = Math.floor(h / 24);
  h %= 24;
  if (d) return `${d}d ${h}h`;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}

/** [0,1,2,3,4] -> "Monday–Friday"; [0,2,5,6] -> "Monday, Wednesday, Saturday–Sunday". */
export function daysText(days) {
  if (days.length === 7 && days.every((d, i) => d === i)) return "Every day";
  const runs = [];
  for (const d of [...days].sort((a, b) => a - b)) {
    if (runs.length && d === runs[runs.length - 1][runs[runs.length - 1].length - 1] + 1) runs[runs.length - 1].push(d);
    else runs.push([d]);
  }
  return runs.map((r) => (r.length === 1 ? DAY_NAMES[r[0]] : `${DAY_NAMES[r[0]]}–${DAY_NAMES[r[r.length - 1]]}`)).join(", ");
}

/** [180,180,180,180,180,120,120] -> "3h Mon–Fri, 2h Sat–Sun" (a day without a limit reads "no limit"). */
export function dayLimitsText(days, fmt = formatDuration) {
  const runs = [];
  for (let d = 0; d < 7; d++) {
    if (runs.length && days[d] === days[runs[runs.length - 1][0]]) runs[runs.length - 1].push(d);
    else runs.push([d]);
  }
  const span = (r) => (r.length === 1 ? DAY_NAMES[r[0]].slice(0, 3) : `${DAY_NAMES[r[0]].slice(0, 3)}–${DAY_NAMES[r[r.length - 1]].slice(0, 3)}`);
  return runs.map((r) => `${days[r[0]] ? fmt(days[r[0]]) : "no limit"} ${span(r)}`).join(", ");
}

/** The amount on each weekday (Mon..Sun, null = no limit that day) of a per-weekday daily limit, or null. */
export function dayLimits(rule) {
  let v = rule[DAY_LIMITS_FIELD];
  if (!v) return null;
  try {
    if (typeof v === "string") v = JSON.parse(v);
  } catch (_) {
    return null;
  }
  if (!Array.isArray(v) || v.length !== 7) return null;
  return v.map((d) => (Number.isInteger(d) && d > 0 ? d : null));
}

/** The schedule JSON as an object {mode, windows} (the 0.2.0 format - one "block during" window - too). */
function loadSchedule(json) {
  const s = JSON.parse(json);
  return s.windows ? s : { mode: "block", windows: [{ days: s.days, start: s.start, end: s.end }] };
}

const allowanceShared = (rule) => (rule.allowance_shared != null ? rule.allowance_shared : 1) === 1;

/** JSON text the way Python's json.dumps writes it (", " and ": "), so a schedule string is the same on both sides. */
function pyJson(v) {
  if (Array.isArray(v)) return "[" + v.map(pyJson).join(", ") + "]";
  if (v && typeof v === "object") return "{" + Object.entries(v).map(([k, x]) => JSON.stringify(k) + ": " + pyJson(x)).join(", ") + "}";
  return JSON.stringify(v);
}

/** "2026-10-11 15:42:17" (local time) -> milliseconds, NaN when it is not a real date. */
export function parseStamp(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(String(text == null ? "" : text).trim());
  if (!m) return NaN;
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number);
  const t = new Date(y, mo - 1, d, h, mi, s);
  return t.getFullYear() === y && t.getMonth() === mo - 1 && t.getDate() === d && t.getHours() === h ? t.getTime() : NaN;
}

function whole(t) {
  return /^\+?[0-9]+$/.test(t) ? parseInt(t, 10) : -1;
}
function minutesBox(text, low, high, what, field) {
  text = text.trim();
  const value = text ? whole(text) : 0;
  if (!(value >= low && value <= high)) throw new RuleFormError(`${what} must be ${low}-${high} minutes.`, field);
  return value;
}
function readDuration(text, field) {
  try {
    return parseDuration(text);
  } catch (e) {
    throw new RuleFormError(e.message, field);
  }
}
function readLimit(text, period, field, day = "") {
  const minutes = readDuration(text, field);
  if (minutes !== null && !(minutes >= 1 && minutes <= PERIOD_MAX_MIN[period])) {
    throw new RuleFormError(`The limit ${day ? "on " + day : PERIOD_LABELS[period]} must be between 1m and ${PERIOD_MAX_MIN[period] / 60}h.`, field);
  }
  return minutes;
}
function validHhmm(text) {
  const parts = text.trim().split(":");
  if (parts.length !== 2) return false;
  const [h, m] = parts.map((p) => (/^\s*\+?[0-9]+\s*$/.test(p) ? parseInt(p, 10) : NaN));
  return h >= 0 && h < 24 && m >= 0 && m < 60;
}
const asText = (v, field) => {
  if (v == null) return "";
  if (typeof v === "boolean" || (typeof v !== "string" && !Number.isInteger(v)) || String(v).length > 200) throw new RuleFormError("A box holds something that isn't a short text.", field);
  return String(v);
};
const txt = (form, key) => asText(form[key], key);
const BAD_FORM = "The settings arrived in a form this window can't read.";

// ---------------------------------------------------------------- form -> rule (throws RuleFormError)
function readScheduled(form, shared) {
  const allowance = minutesBox(txt(form, "allowance"), 0, 1440, "Allowance", "allowance");
  if (form.mode !== "allow" && form.mode !== "block") throw new RuleFormError("Choose Allow only or Block.", "mode");
  if (!Array.isArray(form.windows) || form.windows.length > 50) throw new RuleFormError(BAD_FORM, "windows");
  const windows = [];
  form.windows.forEach((w, i) => {
    const days = w && w.days;
    if (!Array.isArray(days) || days.some((d) => !Number.isInteger(d) || d < 0 || d > 6)) throw new RuleFormError("Pick days from Monday to Sunday.", `windows.${i}`);
    if (!days.length) return;                 // a window with no days is ignored
    const start = txt(w, "start"), end = txt(w, "end");
    if (!validHhmm(start) || !validHhmm(end)) throw new RuleFormError("Hours must look like 09:00 or 21:30.", `windows.${i}`);
    windows.push({ days: [...new Set(days)].sort((a, b) => a - b), start: start.trim(), end: end.trim() });
  });
  if (!windows.length) throw new RuleFormError("Add a time window (with at least one day on).", "windows");
  return {
    rule_type: "scheduled", schedule: pyJson({ mode: form.mode, windows }), allowance_min: allowance || null,
    allowance_shared: shared ? (("pot" in form ? !!form.pot : true) ? 1 : 0) : null,
  };
}

function readTimeLimit(form) {
  const same = "same" in form ? !!form.same : true;
  const rule = { rule_type: "time_limit", [DAY_LIMITS_FIELD]: null };
  for (const p of PERIODS) {
    if (p === "day" && !same) continue;
    rule[TIME_FIELDS[p]] = readLimit(txt(form, p), p, p);
  }
  if (!same) {
    if (!Array.isArray(form.weekdays) || form.weekdays.length !== 7) throw new RuleFormError(BAD_FORM, "weekdays");
    const amounts = form.weekdays.map((t, i) => readLimit(asText(t, `weekdays.${i}`), "day", `weekdays.${i}`, DAY_NAMES[i])).map((a) => a || null);
    if (new Set(amounts).size === 1) [rule[TIME_FIELDS.day], rule[DAY_LIMITS_FIELD]] = [amounts[0], null];
    else [rule[TIME_FIELDS.day], rule[DAY_LIMITS_FIELD]] = [null, pyJson(amounts)];
  }
  if (!Object.values(TIME_FIELDS).concat(DAY_LIMITS_FIELD).some((f) => rule[f])) throw new RuleFormError("Fill in at least one time limit (per day, week or month).", "any");
  return rule;
}

function readSwitchLimit(form) {
  const rule = { rule_type: "switch_limit" };
  for (const p of PERIODS) {
    const t = txt(form, p).trim();
    let times = null;
    if (t) {
      times = whole(t);
      if (!(times >= 0 && times <= 10000)) throw new RuleFormError(`Openings ${PERIOD_LABELS[p]} must be a number 0-10000.`, p);
    }
    rule[OPEN_FIELDS[p]] = times;
  }
  if (Object.values(OPEN_FIELDS).every((f) => rule[f] === null)) throw new RuleFormError("Fill in at least one opening limit (per day, week or month).", "any");
  if (form.counts !== "visit" && form.counts !== "switch") throw new RuleFormError("Choose what counts as opening it.", "counts");
  rule.switch_mode = form.counts;
  if (form.counts === "visit") rule.visit_gap_min = minutesBox(txt(form, "gap"), 1, 1440, "Minutes away", "gap");
  return rule;
}

function readTemporary(form, now) {
  const choice = txt(form, "choice") || "60";
  if (choice === "keep") {
    const ends = parseStamp(txt(form, "keep_until"));
    if (Number.isNaN(ends)) throw new RuleFormError("There is no running block to keep.", "choice");
    if (now != null && ends <= now) throw new RuleFormError("That block has already ended - pick a new length.", "choice");
    return { rule_type: "temporary", temp_until: txt(form, "keep_until").trim() };
  }
  if (choice !== "custom") {
    if (!DURATIONS.some(([, m]) => String(m) === choice)) throw new RuleFormError("Pick how long to block.", "choice");
    return { rule_type: "temporary", duration_min: parseInt(choice, 10) };
  }
  const unit = UNITS.find(([u]) => u === txt(form, "unit"));
  const minutes = whole(txt(form, "amount").trim()) * (unit ? unit[1] : 0);
  if (!(minutes >= 1 && minutes <= MAX_TEMPORARY_MIN)) throw new RuleFormError("Custom duration must be between 1 minute and 30 days.", "amount");
  return { rule_type: "temporary", duration_min: minutes };
}

/** The rule dict for one editor's form. opts: {shared: bool, now: ms epoch (only to refuse "keep" on a block that
 *  has ended)}. Throws RuleFormError. Same result as rule_forms.rule_from_form. */
export function ruleFromForm(form, opts = {}) {
  if (!form || typeof form !== "object" || Array.isArray(form)) throw new RuleFormError(BAD_FORM, "rule_type");
  switch (form.rule_type) {
    case "scheduled": return readScheduled(form, !!opts.shared);
    case "time_limit": return readTimeLimit(form);
    case "switch_limit": return readSwitchLimit(form);
    case "temporary": return readTemporary(form, opts.now);
    case "permanent": return { rule_type: "permanent" };
    default: throw new RuleFormError("Unknown kind of blocker.", "rule_type");
  }
}

/** ruleFromForm that doesn't throw: {rule} or {error: {rule_type, field, message}}. */
export function checkForm(form, opts = {}) {
  try {
    return { rule: ruleFromForm(form, opts) };
  } catch (e) {
    if (!(e instanceof RuleFormError)) throw e;
    return { error: { rule_type: form && typeof form.rule_type === "string" ? form.rule_type : null, field: e.field, message: e.message } };
  }
}

/** Every ticked editor's form -> {rules, errors} (rule_forms.check_forms): rules of the forms that are fine, in
 *  display order; one error per form that isn't (a kind given twice is an error too). opts.requireOne as in Python. */
export function checkForms(forms, opts = {}) {
  const rules = [], errors = [], seen = new Set();
  if (!Array.isArray(forms)) return { rules, errors: [{ rule_type: null, field: "rules", message: BAD_FORM }] };
  for (const form of forms) {
    const t = form && typeof form.rule_type === "string" ? form.rule_type : null;
    if (seen.has(t)) {
      errors.push({ rule_type: t, field: "rule_type", message: "Each blocker can only be set once." });
      continue;
    }
    const r = checkForm(form, opts);
    if (r.error) errors.push(r.error);
    else { seen.add(t); rules.push(r.rule); }
  }
  if (opts.requireOne && !forms.length) errors.push({ rule_type: null, field: "rules", message: "Tick at least one blocker." });
  rules.sort((a, b) => RULE_TYPES.indexOf(a.rule_type) - RULE_TYPES.indexOf(b.rule_type));
  return { rules, errors };
}

// ---------------------------------------------------------------- rule -> form
/** The form an editor shows for a saved (or drafted) rule; null rule = its starting values (all days 09:00-17:00
 *  allowed / 30m a day / 10 opens a day / 1 hour). `type` is only needed when there is no rule. */
export function formFromRule(rule, shared = false, type = null) {
  const t = type || (rule && rule.rule_type);
  if (t === "scheduled") {
    const sched = rule && rule.schedule ? loadSchedule(rule.schedule) : null;
    return {
      rule_type: t, mode: sched ? sched.mode : "allow",
      windows: sched ? sched.windows.map((w) => ({ days: [...w.days], start: w.start, end: w.end })) : [{ days: [0, 1, 2, 3, 4, 5, 6], start: "09:00", end: "17:00" }],
      allowance: String((rule && rule.allowance_min) || 0),
      ...(shared ? { pot: allowanceShared(rule || {}) } : {}),
    };
  }
  if (t === "time_limit") {
    rule = rule || { daily_limit_min: 30 };
    const days = dayLimits(rule);
    return {
      rule_type: t, same: !days,
      day: formatDuration(rule[TIME_FIELDS.day]), week: formatDuration(rule[TIME_FIELDS.week]), month: formatDuration(rule[TIME_FIELDS.month]),
      weekdays: days ? days.map((m) => formatDuration(m)) : ["", "", "", "", "", "", ""],
    };
  }
  if (t === "switch_limit") {
    rule = rule || { daily_switch_limit: 10 };
    const box = (p) => (rule[OPEN_FIELDS[p]] == null ? "" : String(rule[OPEN_FIELDS[p]]));
    return { rule_type: t, counts: rule.switch_mode || "visit", day: box("day"), week: box("week"), month: box("month"), gap: String(rule.visit_gap_min || DEFAULT_VISIT_GAP_MIN) };
  }
  if (t === "temporary") {
    rule = rule || {};
    const minutes = rule.duration_min, keep = rule.temp_until;
    const form = { rule_type: t, choice: "60", amount: "1", unit: "hours", keep_until: keep || null };
    if (keep) form.choice = "keep";
    else if (DURATIONS.some(([, m]) => m === minutes)) form.choice = String(minutes);
    else if (minutes) {
      const [unit, per] = UNITS.find(([, m]) => minutes % m === 0);
      Object.assign(form, { choice: "custom", amount: String(minutes / per), unit });
    }
    return form;
  }
  if (t === "permanent") return { rule_type: t };
  throw new Error(`Unknown kind of blocker: ${t}`);
}

// ---------------------------------------------------------------- the words
/** One line describing a rule, shown on a blocker's rail row. */
export function summary(rule) {
  switch (rule.rule_type) {
    case "scheduled": {
      const s = loadSchedule(rule.schedule), first = s.windows[0];
      let t = `${s.mode === "allow" ? "Allowed only" : "Blocked"} ${first.start}-${first.end} ${daysText(first.days)}`;
      if (s.windows.length > 1) t += ` +${s.windows.length - 1} more`;
      if (rule.allowance_min) t += ` · +${rule.allowance_min} min` + (allowanceShared(rule) ? "" : " each");
      return t;
    }
    case "time_limit": {
      const parts = PERIODS.filter((p) => rule[TIME_FIELDS[p]]).map((p) => `${formatDuration(rule[TIME_FIELDS[p]])} ${PERIOD_LABELS[p]}`);
      const days = dayLimits(rule);
      if (days) parts.unshift(`per day ${dayLimitsText(days, formatDuration)}`);
      return parts.join(" · ");
    }
    case "switch_limit":
      return PERIODS.filter((p) => rule[OPEN_FIELDS[p]] != null).map((p) => `${rule[OPEN_FIELDS[p]]} opens ${PERIOD_LABELS[p]}`).join(" · ");
    case "temporary":
      return rule.temp_until ? "keeps its current end" : `for ${durationText(rule.duration_min * 60)}`;
    default:
      return "always blocked";
  }
}

/** summary() of a form, or "check the settings" when it can't be read. */
export function summarizeForm(form, opts = {}) {
  const r = checkForm(form, opts);
  return r.rule ? summary(r.rule) : BAD_SETTINGS;
}

/** The sentence from [rule_type, summary text] pairs - the page also has blockers whose summary is "check the settings". */
export function sentenceOf(name, parts) {
  if (!parts.length) return "";
  return `${(name || "").trim() || "It"} will be blocked: ${parts.map(([t, text]) => `${RULE_NAMES[t].toLowerCase()} (${text})`).join("; ")}.`;
}

/** "YouTube will be blocked: by time (…); time limit (…)." - empty with no rules. */
export function sentence(name, rules) {
  return sentenceOf(name, rules.map((r) => [r.rule_type, summary(r)]));
}
