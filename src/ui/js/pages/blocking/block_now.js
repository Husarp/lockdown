// One immediate tightening, independent of the draft's Save / Discard controls.
import { ld } from "../../bridge.js";
import { parseNaive } from "../../challenge.js";
import { openDialog } from "../../dialog.js";
import { esc, toast } from "../../ui.js";

const iso = (ms) => new Date(ms).toISOString().slice(0, 19);
const words = (ms) => iso(ms).replace("T", " ").slice(0, 16);

export async function blockNow(id, group = false, onDone) {
  let options;
  try { options = await ld.call("blocking_block_now_options", id, group); }
  catch (e) { toast(e.message || "Couldn't open Block now."); return; }
  const now = parseNaive(options.now), today = iso(now).slice(0, 10);
  const tomorrow = iso(now + 86400000).slice(0, 10);
  let requested, effective, busy = false;
  const h = openDialog({ key: "block-now", title: `Block now · ${options.name}`,
    body: `<label class="rule-lbl">Duration<select class="input" data-duration aria-label="Block duration"><option value="hour">1 hour</option><option value="today">Rest of today</option><option value="until">Until...</option></select></label>
      <div data-custom hidden><label class="rule-lbl">Day<select class="input" data-day aria-label="Block day"><option value="${today}">Today</option><option value="${tomorrow}">Tomorrow</option></select></label>
      <label class="rule-lbl">Time<input class="input" type="time" data-time aria-label="Block until time" value="${iso(now + 3600000).slice(11, 16)}"></label></div>
      <p data-preview></p>${options.note ? `<p class="small">${esc(options.note)}</p>` : ""}<p class="small muted">Saved immediately, even with Auto-save off. Discard will keep this block.</p><p class="rule-err" data-error role="alert" hidden></p>`,
    dismissable: () => !busy,
    buttons: [{ label: "Cancel", onClick: () => !busy }, { label: "Block now", kind: "primary", onClick: () => {
      if (busy || !update()) return false;
      busy = true;
      ld.call("blocking_block_now", id, "until", iso(requested), group).then((r) => {
        h.close(true);
        toast(r.text);
        if (onDone) onDone(r);
      }).catch((e) => { busy = false; error.textContent = e.message || "Couldn't save the block."; error.hidden = false; });
      return false;
    } }],
  });
  if (!h) return;
  const duration = h.body.querySelector("[data-duration]"), custom = h.body.querySelector("[data-custom]");
  const day = h.body.querySelector("[data-day]"), time = h.body.querySelector("[data-time]");
  const preview = h.body.querySelector("[data-preview]"), error = h.body.querySelector("[data-error]");
  if (iso(now + 3600000).slice(0, 10) !== today) day.value = tomorrow;
  function update() {
    custom.hidden = duration.value !== "until";
    requested = duration.value === "hour" ? now + 3600000 : duration.value === "today" ? parseNaive(`${tomorrow}T00:00:00`)
      : parseNaive(`${day.value}T${time.value}:00`);
    const valid = Number.isFinite(requested) && requested > now;
    effective = Math.max(requested, options.until ? parseNaive(options.until) : 0);
    preview.textContent = valid ? `Block until ${words(effective)}?` : "Pick a future time today or tomorrow.";
    error.hidden = true;
    return valid;
  }
  h.body.addEventListener("change", update);
  update();
}


/** Preserve a longer editor draft, including a duration that starts only when Save is pressed. */
export function mergeBlockNow(rail, until, now) {
  const editor = rail.editor("temporary");
  const pending = rail.ticked().includes("temporary") ? editor.check().rule : null;
  const pendingEnd = pending?.temp_until ? parseNaive(pending.temp_until)
    : pending?.duration_min ? now + pending.duration_min * 60000 : 0;
  if (!(pendingEnd > parseNaive(until))) {
    editor.load({ rule_type: "temporary", temp_until: until.replace("T", " ") });
  }
  rail.tick("temporary");
  rail.refresh();
}
