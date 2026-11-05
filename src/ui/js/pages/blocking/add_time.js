// One guarded grant for the current limit day, independent of the editor's draft.
import { ld } from "../../bridge.js";
import { CANCELLED, guarded } from "../../challenge.js";
import { openDialog } from "../../dialog.js";
import { esc, toast } from "../../ui.js";

export function addTimeItem(entry, group, onDone) {
  return { label: entry.add_time ? "Add time" : "Add time · no daily limit of its own today", icon: "timer",
    disabled: !entry.add_time, onClick: () => addTime(entry.id, group, onDone) };
}

export function extraMinutes(choice, custom) {
  const text = choice === "custom" ? custom.trim() : choice;
  if (!/^\d+$/.test(text)) return null;
  const minutes = Number(text);
  return Number.isInteger(minutes) && minutes >= 1 && minutes <= 1440 ? minutes : null;
}

export function addTimeBody(options) {
  return `<label class="rule-lbl">Extra time<select class="input" data-duration aria-label="Extra time"><option value="15">15 min</option><option value="30">30 min</option><option value="60">1 hour</option><option value="120">2 hours</option><option value="custom">Custom minutes</option></select></label>
    <label class="rule-lbl" data-custom hidden>Minutes<input class="input" type="number" min="1" max="1440" step="1" data-minutes aria-label="Custom minutes"></label>
    <p class="small">Today only · expires ${esc(options.until.replace("T", " ").slice(0, 16))}. Daily time limits only; weekly and monthly limits still apply.</p>
    ${options.extra_today ? `<p>${esc(options.extra_today)}</p>` : ""}
    ${options.note ? `<p>${esc(options.note)}</p>` : ""}
    <p class="small muted">Saved immediately, even with Auto-save off.</p>
    <p class="rule-err" data-error role="alert" hidden></p>`;
}

export async function addTime(id, group = false, onDone) {
  let options;
  try { options = await ld.call("blocking_add_time_options", id, group); }
  catch (e) { toast(e.message || "Couldn't open Add time."); return; }
  let busy = false;
  const h = openDialog({ key: "add-time", title: `Add time · ${options.name}`, body: addTimeBody(options),
    dismissable: () => !busy,
    buttons: [{ label: "Cancel", onClick: () => !busy }, { label: "Add time", kind: "primary", onClick: () => {
      if (busy) return false;
      const minutes = extraMinutes(duration.value, customMinutes.value);
      if (minutes === null) {
        error.textContent = "Enter a whole number of minutes from 1 to 1440."; error.hidden = false;
        return false;
      }
      busy = true;
      guarded(ld.call("blocking_add_time", id, minutes, group)).then((r) => {
        busy = false;
        if (r === CANCELLED) return;
        h.close(true);
        toast(r?.text || "Extra time added for today.");
        if (onDone) onDone(r);
      }).catch((e) => { busy = false; error.textContent = e.message || "Couldn't add time."; error.hidden = false; });
      return false;
    } }],
  });
  if (!h) return;
  const duration = h.body.querySelector("[data-duration]"), custom = h.body.querySelector("[data-custom]");
  const customMinutes = h.body.querySelector("[data-minutes]"), error = h.body.querySelector("[data-error]");
  h.body.addEventListener("change", () => { custom.hidden = duration.value !== "custom"; error.hidden = true; });
}
