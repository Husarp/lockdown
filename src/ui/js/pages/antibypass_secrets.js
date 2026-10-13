// The "Password & authenticator" section of the Anti-Bypass page's Challenges card: your own password, an authenticator
// app (TOTP, a QR code to scan once) and the recovery code. Python keeps and checks every secret (challenge_secrets.py,
// webapi/antibypass_page.py): this file shows what is set up - never a secret, except a NEW recovery code once and the
// authenticator's key once while it is being set up - and sends what is typed. Setting the first password or
// authenticator is free; changing or removing one and making a new recovery code ask the challenge (guarded()).
//
//   secretsSection(k)          the section's html (k = the card's colour class), put into the Challenges card
//   paintSecrets(root, d)      fills it from antibypass_get() (d.secrets)
//   wireSecrets(root, apply)   its buttons; `apply(pageData)` repaints the page with what Python answered
import { ld } from "../bridge.js";
import { CANCELLED, guarded } from "../challenge.js";
import { digitsOnly } from "../challenge_kinds.js";
import { confirmDialog, openDialog } from "../dialog.js";
import { csec, csecNote, esc, field, setText, toast } from "../ui.js";

// (Not "or uninstalling": uninstalling Lockdown asks this same challenge, password step and all.)
export const FORGOTTEN = "Forgotten = only the recovery code gets you out - uninstalling Lockdown asks this challenge too.";
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WHAT = { password: "password", totp: "authenticator" };
let last = null;                 // the secrets state last painted (antibypass_get().secrets)

// ---------------------------------------------------------------- plain helpers (node tests)

/** "13 Oct 2026" (with `time`: "13 Oct 2026 10:12") from Python's "2026-10-13 10:12:00"; "" when unreadable. */
export function dayText(ts, time = false) {
  const m = /^(\d{4})-(\d\d)-(\d\d)[T ](\d\d:\d\d)/.exec(ts || "");
  return m ? `${+m[3]} ${MONTH[+m[2] - 1]} ${m[1]}${time ? ` ${m[4]}` : ""}` : "";
}

/** The state line of each row ({password, totp, recovery}) from antibypass_get().secrets. */
export function secretLines(s) {
  const since = (t) => (dayText(t) ? ` ${dayText(t)}` : "");
  const any = s.password.set || s.totp.set;
  return {
    password: s.password.set ? `Set${since(s.password.since)}.` : "Not set.",
    totp: s.totp.set ? `Set up${since(s.totp.since)}.` : "Not set up.",
    recovery: s.recovery.exists ? `Made ${dayText(s.recovery.made, true) || "earlier"} - shown only then.`
      : any ? "None (used up or never made) - make a new one." : "Made with your first password or authenticator.",
  };
}

/** The warning for steps in the list whose secret isn't set up ("" = none): they are skipped until it is. */
export function missingText(s) {
  const kinds = (s.missing || []).map((k) => WHAT[k] || k);
  if (!kinds.length) return "";
  return `Your list asks your ${kinds.join(" and ")}, but ${kinds.length > 1 ? "they aren't" : "it isn't"} set up on this PC (a restored backup never carries them) - the step is skipped until you set ${kinds.length > 1 ? "them" : "it"} up.`;
}

/** The two password boxes: "" when they can be sent, else what is wrong (Python checks again). */
export function passwordProblem(a, b) {
  if (!a || !a.trim()) return "Type a password.";
  if (a.length < 4) return "At least 4 characters, please.";
  if (a !== b) return "The two passwords are not the same.";
  return "";
}

// ---------------------------------------------------------------- the section

const STYLE = `<style>
.sx-state { font-weight: var(--fw-semibold); color: var(--text-2); }
.sx-state.on { color: var(--ok); }
.sx-warn { margin: 0; padding: var(--sp-2) var(--sp-5); color: var(--k-limit, var(--text-2)); font-size: var(--fs-sm); font-weight: var(--fw-semibold); text-wrap: pretty; }
.sx-warn[hidden] { display: none; }
.sx-forgot { padding: var(--sp-2) var(--sp-3); border-radius: var(--r-md); background: var(--surface-3); font-weight: var(--fw-semibold); }
.sx-l { display: grid; gap: var(--sp-1); margin-top: var(--sp-3); font-weight: var(--fw-semibold); }
.sx-err { min-height: 1.2em; margin: var(--sp-2) 0 0; color: var(--k-block); font-size: var(--fs-sm); font-weight: var(--fw-semibold); }
.sx-qr { display: flex; justify-content: center; margin: var(--sp-3) 0; }
.sx-qr svg { width: calc(200px * var(--s)); height: auto; border-radius: var(--r-md); background: #fff; }
.sx-key { font-family: ui-monospace, Consolas, monospace; letter-spacing: .06em; word-break: break-all; }
.sx-codein { width: calc(150px * var(--s)); font-size: var(--fs-lg, 1.2em); letter-spacing: .3em; text-align: center; }
.sx-code { margin: var(--sp-3) 0; padding: var(--sp-3); border: 1px dashed var(--line-strong, var(--line)); border-radius: var(--r-md); text-align: center; font-family: ui-monospace, Consolas, monospace; font-size: calc(24px * var(--s)); font-weight: var(--fw-semibold); letter-spacing: .08em; user-select: all; }
</style>`;

const btn = (act, label, attrs = "") => `<button class="btn btn-sm" type="button" data-act="${act}" ${attrs}>${esc(label)}</button>`;

export function secretsSection(k) {
  return csec(k, "lock", "Password & authenticator", `${STYLE}
    ${field("Own password", '<span class="sx-state" data-k="sx-pw"></span>', btn("sx-pw-set", "Set a password") + btn("sx-pw-x", "Remove", "hidden"))}
    ${field("Authenticator app", '<span class="sx-state" data-k="sx-totp"></span>', btn("sx-totp-set", "Set up") + btn("sx-totp-x", "Remove", "hidden"))}
    ${field("Recovery code", '<span class="sx-state" data-k="sx-rec"></span>', btn("sx-rec-new", "Make a new one", "disabled"))}
    <p class="sx-warn" data-k="sx-missing" role="status" hidden></p>
    ${csecNote(`Add "Own password" or "Authenticator code" as a step above to ask for it. Strongest when someone else sets the password or keeps the authenticator on their phone. ${FORGOTTEN}`)}`,
  'data-k="sx"');
}

export function paintSecrets(root, d) {
  const s = d.secrets;
  if (!s) return;
  last = s;
  const q = (k) => root.querySelector(`[data-k="${k}"]`);
  const lines = secretLines(s);
  for (const [k, on] of [["sx-pw", s.password.set], ["sx-totp", s.totp.set], ["sx-rec", s.recovery.exists]]) {
    const el = q(k);
    setText(el, lines[{ "sx-pw": "password", "sx-totp": "totp", "sx-rec": "recovery" }[k]]);
    el.classList.toggle("on", !!on);
  }
  const act = (a) => root.querySelector(`[data-act="${a}"]`);
  setText(act("sx-pw-set"), s.password.set ? "Change" : "Set a password");
  act("sx-pw-x").hidden = !s.password.set;
  setText(act("sx-totp-set"), s.totp.set ? "Set up again" : "Set up");
  act("sx-totp-x").hidden = !s.totp.set;
  act("sx-rec-new").disabled = !(s.password.set || s.totp.set);
  const warn = q("sx-missing");
  setText(warn, missingText(s));
  warn.hidden = !warn.textContent;
}

export function wireSecrets(root, apply) {
  let busy = false;
  root.addEventListener("click", async (e) => {
    const b = e.target.closest('[data-act^="sx-"]');
    if (!b || b.disabled || busy) return;
    busy = true;
    try {
      const act = b.dataset.act;
      if (act === "sx-pw-set") await passwordDialog(!!(last && last.password.set), apply);
      else if (act === "sx-totp-set") await totpDialog(apply);
      else if (act === "sx-pw-x") await removeSecret("password", apply);
      else if (act === "sx-totp-x") await removeSecret("totp", apply);
      else if (act === "sx-rec-new") await newRecovery(apply);
    } finally {
      busy = false;
    }
  });
}

// ---------------------------------------------------------------- what the buttons do

/** Send a change and show what Python answered: the page again (+ the new recovery code, shown once). */
async function done(res, apply) {
  if (res === CANCELLED || !res) return false;
  apply(res);
  if (res.recovery_code) await showRecoveryCode(res.recovery_code);
  return true;
}

function errorIn(h, text) {
  const el = h.body.querySelector('[data-k="sx-err"]');
  if (el) el.textContent = text || "";
}

function passwordDialog(had, apply) {
  let sending = false;
  const h = openDialog({
    key: "sx-pw", title: had ? "Change your Anti-Bypass password" : "Set an Anti-Bypass password",
    body: `<p>${had ? "The new password replaces the old one. Changing it asks the challenge." : 'An "Own password" step of the challenge asks it. Setting it is free; changing or removing it later asks the challenge.'}</p>
      <p class="sx-forgot">${esc(FORGOTTEN)}</p>
      <p class="small muted">Strongest when someone else types it here and keeps it to themselves.</p>
      <label class="sx-l">${had ? "New password" : "Password"}<input class="input" type="password" data-k="sx-p1" maxlength="200" autocomplete="off" spellcheck="false"></label>
      <label class="sx-l">Once more<input class="input" type="password" data-k="sx-p2" maxlength="200" autocomplete="off" spellcheck="false"></label>
      <p class="sx-err" data-k="sx-err" role="alert"></p>`,
    buttons: [{ label: "Cancel", value: false }, { label: had ? "Change password" : "Set password", kind: "primary", onClick: () => { send(); return false; } }],
  });
  if (!h) return Promise.resolve();
  const p1 = h.body.querySelector('[data-k="sx-p1"]'), p2 = h.body.querySelector('[data-k="sx-p2"]');
  p1.focus();
  for (const box of [p1, p2]) box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send(); } });
  async function send() {
    if (sending) return;
    const problem = passwordProblem(p1.value, p2.value);
    if (problem) { errorIn(h, problem); return; }
    sending = true;
    errorIn(h, "");
    try {
      const res = await guarded(ld.call("antibypass_password_set", p1.value, p2.value));
      if (res === CANCELLED) return;
      h.close(true);
      await h.done;                                          // (closed for good before the recovery code opens)
      await done(res, apply);
    } catch (err) {
      errorIn(h, err.message || "That didn't work.");
    } finally {
      sending = false;
    }
  }
  return h.done;
}

async function totpDialog(apply) {
  let setup;
  try {
    setup = await ld.call("antibypass_totp_begin");
  } catch (err) {
    toast(err.message || "That didn't work.");
    return;
  }
  let sending = false;
  const qr = setup.qr ? `<div class="sx-qr" data-k="sx-qr">${setup.qr}</div>` : '<p class="small muted">(No QR code here - type the key below into the app.)</p>';
  const h = openDialog({
    key: "sx-totp", title: setup.replaces ? "Set up the authenticator again" : "Set up an authenticator", wide: true,
    body: `<p><b>1.</b> Scan this with an authenticator app (Google or Microsoft Authenticator, 2FAS, Aegis ...) on the phone that will keep it - yours, or a trusted person's, so you can't loosen without them.</p>
      ${qr}
      <p class="small">Or type this key into the app: <span class="sx-key" data-k="sx-key">${esc(setup.key)}</span></p>
      <p class="small muted">The QR code and the key are shown only now.${setup.replaces ? " Saving replaces the old authenticator: that asks the challenge, and the old one stops working." : ""}</p>
      <label class="sx-l"><span><b>2.</b> Type the 6-digit code the app shows for Lockdown</span>
        <input class="input sx-codein" data-k="sx-code" type="text" inputmode="numeric" maxlength="6" autocomplete="off" spellcheck="false" placeholder="000000"></label>
      <p class="sx-err" data-k="sx-err" role="alert"></p>`,
    buttons: [{ label: "Cancel", value: false }, { label: "Confirm", kind: "primary", onClick: () => { send(); return false; } }],
  });
  if (!h) return;
  const box = h.body.querySelector('[data-k="sx-code"]');
  box.focus();
  box.addEventListener("input", () => {
    const clean = digitsOnly(box.value).slice(0, setup.digits || 6);
    if (clean !== box.value) box.value = clean;
  });
  box.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send(); } });
  async function send() {
    if (sending) return;
    if (box.value.length !== (setup.digits || 6)) { errorIn(h, "Type the 6 digits the app shows."); return; }
    sending = true;
    errorIn(h, "");
    try {
      const res = await guarded(ld.call("antibypass_totp_confirm", setup.token, box.value));
      if (res === CANCELLED) return;
      h.close(true);
      await h.done;                                          // (closed for good before the recovery code opens)
      await done(res, apply);
    } catch (err) {
      box.value = "";
      errorIn(h, err.message || "That didn't work.");
    } finally {
      sending = false;
    }
  }
  await h.done;
}

async function removeSecret(kind, apply) {
  const what = WHAT[kind];
  const yes = await confirmDialog({
    key: "sx-x", title: `Remove your ${what}?`, ok: "Remove", danger: true,
    text: `Removing your ${what} asks the challenge. Take its step out of the list first.`,
  });
  if (!yes) return;
  try {
    await done(await guarded(ld.call("antibypass_secret_remove", kind)), apply);
  } catch (err) {
    toast(err.message || "That didn't work.");
  }
}

async function newRecovery(apply) {
  const yes = await confirmDialog({
    key: "sx-rx", title: "Make a new recovery code?", ok: "Make a new one",
    text: "The old code stops working. Making one asks the challenge: it is a way past your password and authenticator steps.",
  });
  if (!yes) return;
  try {
    await done(await guarded(ld.call("antibypass_recovery_new")), apply);
  } catch (err) {
    toast(err.message || "That didn't work.");
  }
}

function printCode(code) {
  const frame = document.createElement("iframe");
  frame.style.cssText = "position:fixed;width:0;height:0;border:0;right:0;bottom:0";
  document.body.append(frame);
  const doc = frame.contentDocument;
  doc.open();
  doc.write(`<!doctype html><meta charset="utf-8"><title>Lockdown recovery code</title><body style="font-family:sans-serif;padding:40px">
    <h2>Lockdown - Anti-Bypass recovery code</h2><p style="font:600 28px monospace;letter-spacing:.08em">${esc(code)}</p>
    <p>It stands in for the password and authenticator steps of one challenge, then it is used up. Keep it somewhere safe.</p></body>`);
  doc.close();
  try { frame.contentWindow.focus(); frame.contentWindow.print(); } catch (_) { /* no printer: write it down */ }
  setTimeout(() => frame.remove(), 60000);
}

/** The new recovery code, shown once. Only "I have written it down" closes it (no Esc by accident). */
export function showRecoveryCode(code) {
  const h = openDialog({
    key: "sx-rec", title: "Your recovery code", dismissable: false,
    body: `<p>Print it or write it down now and keep it somewhere safe - <b>it is shown only this once</b>.</p>
      <p class="sx-code" data-k="sx-code-shown">${esc(code)}</p>
      <p class="small muted">If the password or the authenticator is lost, typing it instead gets you through their steps of one challenge (the other steps are still asked). Then it is used up - make a new one afterwards.</p>`,
    buttons: [{ label: "Print", onClick: () => { printCode(code); return false; } }, { label: "I have written it down", kind: "primary", value: true }],
  });
  return h ? h.done : Promise.resolve();
}
