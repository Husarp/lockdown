// The draft (unsaved changes) from the page's side. The shell owns the save bar itself (js/shell.js: Auto-save switch,
// Discard, Save changes, and the "what changed" text - all drawn from the `draft` push and get_status); this file is
// what the PAGES use to change the draft.
//
//   draftCall(name, ...args)   call a draft Api method (draft_remove_item, draft_set_notify ...). Python decides what
//                              loosens: if the reply is {needs_challenge, ticket} the challenge opens and the call
//                              resolves with the result once it passed, or CANCELLED. Errors are shown as a toast and
//                              give FAILED. The state Python returns ({dirty, autosave, change_summary}) is applied to the
//                              save bar at once (the same `draft` event a push would send).
//   syncState()                ask Python where the draft stands and show it on the save bar.
//   draftSetSetting(key, value) a notification setting (`notify.*`), sent 400 ms after the last change to that key.
//   flushSettings()             send the waiting ones now (leaving a page).
//   autosaveOn()                is Auto-save on? (for "- press Save changes to apply" in notices)
//
// Nothing here says "confirmed": the page never can.
import { ld } from "./bridge.js";
import { CANCELLED, guarded } from "./challenge.js";
import { toast } from "./ui.js";

export { CANCELLED };
/** What draftCall resolves with when the call failed (an error toast was shown). */
export const FAILED = Symbol("draft-call-failed");

export const SETTING_DEBOUNCE_MS = 400;

/** Is Auto-save on? (the shell keeps ld.status.draft up to date from get_status and the draft pushes) */
export const autosaveOn = () => !(ld.status.draft && ld.status.draft.autosave === false);

/** Hand a draft state ({dirty, autosave, change_summary}) to the save bar, as a `draft` push would. */
export function applyState(state, extra = {}) {
  if (state && typeof state === "object" && "dirty" in state && "autosave" in state) {
    ld.push([["draft", { dirty: !!state.dirty, autosave: !!state.autosave, change_summary: state.change_summary || "", ...extra }]]);
  }
}

/** Ask Python where the draft stands and show it (after a challenge: the reply of the change itself never came back). */
export async function syncState(extra = {}) {
  try {
    applyState(await ld.call("draft_get_state"), extra);
  } catch (_) { /* the next push says it */ }
}

/** Call a draft method; see the top of this file. */
export async function draftCall(name, ...args) {
  try {
    const result = await guarded(ld.call(name, ...args));
    if (result && typeof result === "object" && "dirty" in result) applyState(result);
    else await syncState();             // it went through the challenge (passed or cancelled)
    return result;
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
    return FAILED;
  }
}

const waiting = new Map();      // key -> {value, timer}

/** Send the notification setting `key` = `value` (text) after the last change to that key + 400 ms. */
export function draftSetSetting(key, value, ms = SETTING_DEBOUNCE_MS) {
  const old = waiting.get(key);
  if (old) clearTimeout(old.timer);
  const entry = { value, timer: setTimeout(() => send(key), ms) };
  waiting.set(key, entry);
}

async function send(key) {
  const entry = waiting.get(key);
  if (!entry) return;
  waiting.delete(key);
  try {
    applyState(await ld.call("draft_set_setting", key, entry.value), { blocks: false });     // (the Blocking page keeps its list)
  } catch (err) {
    toast((err && err.message) || "That didn't work.");
  }
}

/** Send every waiting setting now. */
export function flushSettings() {
  for (const [key, entry] of [...waiting]) {
    clearTimeout(entry.timer);
    send(key);
  }
}
