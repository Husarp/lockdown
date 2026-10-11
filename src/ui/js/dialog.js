// One reused <dialog id="dlg"> for every in-page dialog: content swapped, not stacked - except a `stack: true` dialog
// (the challenge), which opens its own <dialog> on top of whatever is open and leaves it untouched. Native showModal() makes
// the rest of the page inert; this adds the focus trap (Tab wraps), Esc / the X to close (unless the dialog says it
// can't be dismissed - the challenge, overlay rules), focus back to whoever opened it, and a `once` rule: opening a
// dialog whose key is already open does nothing, so a double click can't open two.
import { esc, ic, el } from "./ui.js";

const stack = [];        // open dialogs, bottom first: {key, dismissable, onClose, opener, resolve, result, body, el, stacked, handle}
const FOCUSABLE = 'button:not(:disabled), [href], input:not(:disabled):not([type=hidden]), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

const top = () => stack[stack.length - 1] || null;
const recFor = (d) => stack.find((r) => r.el === d) || null;
const canDismiss = (r) => !!(typeof r.dismissable === "function" ? r.dismissable() : r.dismissable);
const wired = new WeakSet();

function wire(d) {
  if (wired.has(d)) return;
  wired.add(d);
  d.addEventListener("cancel", (e) => {                 // Esc
    const r = recFor(d);
    if (!r || !canDismiss(r)) e.preventDefault();
  });
  d.addEventListener("close", () => { const r = recFor(d); if (r) finish(r); });
  d.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const items = [...d.querySelectorAll(FOCUSABLE)].filter((x) => x.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || !d.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}

function finish(cur) {
  const at = stack.indexOf(cur);
  if (at < 0) return;
  stack.splice(at, 1);
  if (cur.stacked) cur.el.remove(); else cur.el.innerHTML = "";
  if (cur.opener && cur.opener.isConnected && typeof cur.opener.focus === "function") cur.opener.focus();
  try { if (cur.onClose) cur.onClose(cur.result); } finally { cur.resolve(cur.result); }
}

export function isOpen(key) {
  return key === undefined ? stack.length > 0 : stack.some((r) => r.key === key);
}

/**
 * Open a dialog.
 *   key         `once` identity - the same key twice opens it once (the open handle is returned again)
 *   title, body (string of HTML, or a Node), buttons [{label, kind: "primary"|"danger"|"text", value, onClick, disabled}]
 *   wide        720 px instead of 520
 *   dismissable false: no X, Esc does nothing (or a function, asked each time, for "not right now")
 *   stack       true: when a dialog is open already, open on top of it in a layer of its own instead of swapping it;
 *               the one underneath is not touched and gets its focus back when this one closes
 *   onClose(result)
 * A button closes the dialog with its `value` unless its onClick returns false. Returns {body, close(result), done, el}.
 * While an undismissable dialog (or a stacked one) is open no other dialog may replace it (null is returned).
 */
export function openDialog({ key, title = "", body = "", buttons = [], wide = false, dismissable = true, stack: stackIt = false, onClose } = {}) {
  const below = top();
  let layer = false;
  if (below) {
    const same = key !== undefined && stack.find((r) => r.key === key);
    if (same) return same.handle;
    if (stackIt) layer = true;
    else {
      if (below.stacked || !canDismiss(below)) return null;
      stack.pop();                                      // swapped, not stacked
      try { if (below.onClose) below.onClose("replaced"); } finally { below.resolve("replaced"); }
    }
  }
  const d = layer ? document.createElement("dialog") : document.getElementById("dlg");
  if (layer) document.body.append(d);
  wire(d);
  const opener = document.activeElement;
  d.className = "dialog" + (wide ? " wide" : "");
  d.innerHTML = `<div class="dlg-h"><h2>${esc(title)}</h2>${dismissable ? `<button class="icon-btn" type="button" data-close aria-label="Close">${ic("x")}</button>` : ""}</div><div class="dlg-b"></div>${buttons.length ? '<div class="dlg-f"></div>' : ""}`;
  const bodyEl = d.querySelector(".dlg-b");
  if (typeof body === "string") bodyEl.innerHTML = body; else bodyEl.append(body);
  let resolve;
  const done = new Promise((r) => { resolve = r; });
  const cur = { key, dismissable, onClose, opener, resolve, result: undefined, body: bodyEl, el: d, stacked: layer };
  stack.push(cur);
  const close = (result) => {
    if (top() !== cur) return;
    cur.result = result;
    d.close();
  };
  cur.handle = { body: bodyEl, close, done, el: d };
  const x = d.querySelector("[data-close]");
  if (x) x.addEventListener("click", () => { if (canDismiss(cur)) close(undefined); });
  const foot = d.querySelector(".dlg-f");
  buttons.forEach((b) => {
    const btn = el(`<button class="btn ${b.kind === "primary" ? "btn-primary" : b.kind === "danger" ? "btn-primary btn-danger-fill" : b.kind === "text" ? "btn-text" : ""}" type="button"></button>`);
    btn.textContent = b.label;
    if (b.disabled) btn.disabled = true;
    if (b.grow) foot.append(el('<span class="grow"></span>'));
    btn.addEventListener("click", () => {
      if (b.onClick && b.onClick(cur.handle) === false) return;
      close(b.value);
    });
    foot.append(btn);
    if (b.kind === "primary" || b.kind === "danger") btn.dataset.primary = "1";
  });
  if (!d.open) d.showModal();
  const target = d.querySelector("[autofocus]") || d.querySelector("[data-primary]") || d.querySelector(".dlg-b input, .dlg-b select, .dlg-b textarea")
    || d.querySelector(".dlg-f button") || x;
  if (target) target.focus();
  return cur.handle;
}

export function closeDialog(result) {
  const cur = top();
  if (cur) cur.handle.close(result);
}

/** Yes / no question. Resolves true on the confirming button, false on anything else (Cancel, X, Esc). */
export function confirmDialog({ title, text, ok = "OK", cancel = "Cancel", danger = false, key } = {}) {
  const h = openDialog({
    key, title, body: `<p>${esc(text)}</p>`,
    buttons: [{ label: cancel, value: false }, { label: ok, kind: danger ? "danger" : "primary", value: true }],
  });
  return h ? h.done.then((v) => v === true) : Promise.resolve(false);
}
