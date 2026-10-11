// The "New reminder" / "Edit reminder" dialog of the Reminders page (BLUEPRINT 3.8). The page loads this module the
// first time it is needed, so it is not part of the page's first visit. The dialog only collects what is typed and
// sends it as text/numbers: Python (webapi/reminders.py) parses and validates it, and decides whether the change needs
// the Anti-Bypass challenge (removing "Important" or weakening an important reminder). Nothing here compares old
// with new.
import { ld } from "./bridge.js";
import { guarded } from "./challenge.js";
import { openDialog } from "./dialog.js";
import { getRange, getTp } from "./pages/reminders.js";
import { check, days, esc, seg, tp, tpRange } from "./ui.js";

export const SNOOZES = [1, 5, 10, 15, 30];
export const CHECKS = [0, 5, 10, 15, 30];
export const KIND_LABELS = [["interval", "Interval"], ["times", "Times"], ["random", "Random"]];
const DEFAULTS = {
  id: "", text: "", kind: "interval", every: "1h", times: ["12:00"], days: [0, 1, 2, 3, 4, 5, 6], window: ["10:00", "18:00"],
  hours: false, snooze: 5, max_snooze: 3, check: 0, per_day: 0, packs: [], quotes: "", guarded: false,
};

// ---------------------------------------------------------------- the dialog
const selectOf = (key, values, current, label) =>
  `<select class="select select-sm" data-k="${key}" aria-label="${esc(label)}">${values.map(([v, t]) =>
    `<option value="${esc(v)}"${String(v) === String(current) ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>`;

const timeChip = (t) => `<span class="step" data-time>${tp(t, "Time")}<button class="icon-btn icon-btn-sm" type="button" data-act="time-x" aria-label="Remove time">×</button></span>`;

function bodyHtml(r, packs) {
  const row = (label, control, help) => `<div class="field"><div class="field-l"><b>${label}</b>${help ? `<span>${help}</span>` : ""}</div><div class="field-c">${control}</div></div>`;
  return `<div class="stack-sm rem-ed" style="gap:var(--sp-3)">
    <label class="stack-sm"><b>Text</b><input class="input" data-k="text" maxlength="300" placeholder="e.g. Drink water, Stretch, Go outside" value="${esc(r.text)}"></label>
    <div class="stack-sm"><b>When</b><div style="justify-self:start">${seg("rkind", KIND_LABELS, r.kind)}</div>
      <div data-panel="interval"><div class="hstack wrap"><span>Every</span><input class="input" data-k="every" style="width:calc(90px*var(--s));text-align:center" value="${esc(r.every)}" aria-label="Every"><span class="muted small">of use: 60, 30s, 5 min or 1h30 (time away from the PC doesn't count)</span></div>
        <div class="hstack wrap" style="margin-top:var(--sp-2)">${check("Only between", r.hours, 'data-k="hours"')}<span data-range="interval">${tpRange(r.window[0], r.window[1])}</span><span class="muted small">(so it doesn't ask you to do push-ups at 3 a.m.)</span></div></div>
      <div data-panel="times"><div class="steps" data-k="times">${r.times.map(timeChip).join("")}<button class="btn btn-sm" type="button" data-act="time-add">+ Time</button></div></div>
      <div data-panel="random"><div class="hstack wrap"><span>Once a day, at a random time between</span><span data-range="random">${tpRange(r.window[0], r.window[1])}</span></div></div></div>
    <div class="hstack wrap"><b>On</b>${days([0, 1, 2, 3, 4, 5, 6].map((d) => r.days.includes(d)))}</div>
    <div class="card"><div class="flush">
      ${row("Snooze for", selectOf("snooze", SNOOZES.map((n) => [n, `${n} min`]), r.snooze, "Snooze for"))}
      ${row("At most", selectOf("max_snooze", [0, 1, 2, 3, 4, 5].map((n) => [n, String(n)]), r.max_snooze, "Snoozes"), "snoozes, then it stays until Done")}
      ${row("Ask “did you actually do it?”", selectOf("check", CHECKS.map((n) => [n, n ? `${n} min` : "Off"]), r.check, "Check after"), "after Done")}
      ${row("Stop for the day after", selectOf("per_day", [["0", "No limit"], ...Array.from({ length: 12 }, (_, i) => [i + 1, String(i + 1)])], r.per_day, "Stop after"), "times DONE (snoozing or ignoring it doesn't count)")}
      ${row("Important", check("Dismissing needs the challenge", r.guarded, 'data-k="guarded"'), "Turning it off, deleting it or making it ask for less needs the challenge too.")}
    </div></div>
    <div class="stack-sm"><b>Quotes</b><span class="muted small">A random one is shown with the reminder.</span>
      <div class="hstack wrap">${packs.map((p) => check(esc(p), r.packs.includes(p), `data-pack="${esc(p)}"`)).join("")}</div>
      <textarea class="textarea" data-k="quotes" rows="3" maxlength="3000" placeholder="Your own quotes, one per line" aria-label="Your own quotes">${esc(r.quotes)}</textarea></div>
    <p class="small" data-k="error" role="alert" style="color:var(--k-block);min-height:1.2em"></p>
  </div>`;
}

/** Everything in the dialog as the page sends it (Python checks every part). */
export function collect(root, id) {
  const q = (k) => root.querySelector(`[data-k="${k}"]`);
  const kind = root.querySelector('.seg [aria-pressed="true"]').dataset.v;
  const range = getRange(root.querySelector(`[data-range="${kind === "random" ? "random" : "interval"}"]`));
  return {
    id, text: q("text").value, kind, every: q("every").value,
    times: [...root.querySelectorAll("[data-time] .tp")].map(getTp),
    days: [...root.querySelectorAll(".days button")].flatMap((b, i) => (b.getAttribute("aria-pressed") === "true" ? [i] : [])),
    window: range, hours: q("hours").checked,
    snooze: +q("snooze").value, max_snooze: +q("max_snooze").value, check: +q("check").value, per_day: +q("per_day").value,
    packs: [...root.querySelectorAll("[data-pack]")].filter((c) => c.checked).map((c) => c.dataset.pack),
    quotes: q("quotes").value, guarded: q("guarded").checked,
  };
}

function showKind(root, kind) {
  for (const p of root.querySelectorAll("[data-panel]")) p.hidden = p.dataset.panel !== kind;
}

/**
 * Open the editor. `reminder` = an item from reminders_get, or null for a new one. `packs` = the quote pack names.
 * `done()` runs after a save (or a challenge that was shown for it), so the page reloads its rows.
 */
export function openReminderEditor(reminder, packs, done) {
  const r = { ...DEFAULTS, ...(reminder || {}) };
  const root = document.createElement("div");
  root.innerHTML = bodyHtml(r, packs);
  const body = root.firstElementChild;
  showKind(body, r.kind);
  body.addEventListener("seg", (e) => { if (e.detail.name === "rkind") showKind(body, e.detail.value); });
  body.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    if (b.dataset.act === "time-x") {
      if (body.querySelectorAll("[data-time]").length > 1) b.closest("[data-time]").remove();
    } else if (b.dataset.act === "time-add") {
      b.insertAdjacentHTML("beforebegin", timeChip("12:00"));
    }
  });
  const handle = openDialog({
    key: "reminder-editor", title: reminder ? "Edit reminder" : "New reminder", wide: true, body,
    buttons: [{ label: "Cancel", value: false }, {
      label: "Save", kind: "primary", value: true,
      onClick: (h) => { save(h); return false; },
    }],
  });
  if (!handle) return null;
  let busy = false;
  const error = body.querySelector('[data-k="error"]');
  async function save(h) {
    if (busy) return;
    busy = true;
    error.textContent = "";
    try {
      await guarded(ld.call("reminder_save_custom", collect(body, r.id)));
      h.close(true);
      done();
    } catch (err) {
      error.textContent = err.message || "That didn't work.";
    } finally {
      busy = false;
    }
  }
  return handle;
}
