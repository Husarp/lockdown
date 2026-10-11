// One reused <dialog id="dlg"> for every in-page dialog: content swapped, never stacked. Native showModal() makes
// the rest of the page inert; this adds the focus trap (Tab wraps), Esc / the X to close (unless the dialog says it
// can't be dismissed - the challenge, overlay rules), focus back to whoever opened it, and a `once` rule: opening a
// dialog whose key is already open does nothing, so a double click can't open two.
import { esc, ic, el } from "./ui.js";

let current = null;      // {key, dismissable, onClose, opener, resolve, result, body}
const FOCUSABLE = 'button:not(:disabled), [href], input:not(:disabled):not([type=hidden]), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

const node = () => document.getElementById("dlg");
let wired = false;

function wire() {
  if (wired) return;
  wired = true;
  const d = node();
  d.addEventListener("cancel", (e) => {                 // Esc
    if (!current || !current.dismissable) e.preventDefault();
  });
  d.addEventListener("close", () => finish());
  d.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const items = [...d.querySelectorAll(FOCUSABLE)].filter((x) => x.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || !d.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}

function finish() {
  const cur = current;
  if (!cur) return;
  current = null;
  const d = node();
  d.innerHTML = "";
  if (cur.opener && cur.opener.isConnected && typeof cur.opener.focus === "function") cur.opener.focus();
  try { if (cur.onClose) cur.onClose(cur.result); } finally { cur.resolve(cur.result); }
}

export function isOpen(key) {
  return !!current && (key === undefined || current.key === key);
}

/**
 * Open a dialog.
 *   key         `once` identity - the same key twice opens it once (the open handle is returned again)
 *   title, body (string of HTML, or a Node), buttons [{label, kind: "primary"|"danger"|"text", value, onClick, disabled}]
 *   wide        720 px instead of 520
 *   dismissable false: no X, Esc does nothing
 *   onClose(result)
 * A button closes the dialog with its `value` unless its onClick returns false. Returns {body, close(result), done}.
 * While an undismissable dialog is open no other dialog may replace it (null is returned).
 */
export function openDialog({ key, title = "", body = "", buttons = [], wide = false, dismissable = true, onClose } = {}) {
  wire();
  const d = node();
  if (current) {
    if (key !== undefined && current.key === key) return current.handle;
    if (!current.dismissable) return null;
    const old = current;                                // swapped, not stacked
    current = null;
    try { if (old.onClose) old.onClose("replaced"); } finally { old.resolve("replaced"); }
  }
  const opener = document.activeElement;
  d.className = "dialog" + (wide ? " wide" : "");
  d.innerHTML = `<div class="dlg-h"><h2>${esc(title)}</h2>${dismissable ? `<button class="icon-btn" type="button" data-close aria-label="Close">${ic("x")}</button>` : ""}</div><div class="dlg-b"></div>${buttons.length ? '<div class="dlg-f"></div>' : ""}`;
  const bodyEl = d.querySelector(".dlg-b");
  if (typeof body === "string") bodyEl.innerHTML = body; else bodyEl.append(body);
  let resolve;
  const done = new Promise((r) => { resolve = r; });
  const cur = current = { key, dismissable, onClose, opener, resolve, result: undefined, body: bodyEl };
  const close = (result) => {
    if (current !== cur) return;
    cur.result = result;
    d.close();
  };
  cur.handle = { body: bodyEl, close, done, el: d };
  const x = d.querySelector("[data-close]");
  if (x) x.addEventListener("click", () => close(undefined));
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
  if (current) current.handle.close(result);
}

/** Yes / no question. Resolves true on the confirming button, false on anything else (Cancel, X, Esc). */
export function confirmDialog({ title, text, ok = "OK", cancel = "Cancel", danger = false, key } = {}) {
  const h = openDialog({
    key, title, body: `<p>${esc(text)}</p>`,
    buttons: [{ label: cancel, value: false }, { label: ok, kind: danger ? "danger" : "primary", value: true }],
  });
  return h ? h.done.then((v) => v === true) : Promise.resolve(false);
}
