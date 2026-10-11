// One context-menu component for the whole app (right-click on an app / site / row, "Edit ▾", category chips ...).
// A single <div id="menu"> is reused. Keyboard: Up / Down / Home / End move, Enter / Space choose, Esc closes and gives
// the focus back, Tab closes. Click away, scrolling or resizing closes it. Listeners exist only while it is open.
import { esc, ic } from "./ui.js";

let open = null;      // {opener, onClose, off}

const node = () => document.getElementById("menu");

/**
 * Items: {label, icon, onClick, danger, disabled, checked}, {sep: true}, {heading: "Text"}.
 * `anchor` is an element (menu opens under it), a MouseEvent / {x, y} (menu opens at the pointer).
 */
export function openMenu(anchor, items, { onClose } = {}) {
  closeMenu();
  const m = node();
  const opener = document.activeElement;
  m.innerHTML = items.map((it, i) => {
    if (it.sep) return '<div class="sep" role="separator"></div>';
    if (it.heading) return `<div class="lbl">${esc(it.heading)}</div>`;
    return `<button type="button" role="menuitem" data-i="${i}" class="${it.danger ? "danger" : ""}" ${it.disabled ? "disabled" : ""}>` +
      `${it.icon ? ic(it.icon) : ""}<span>${esc(it.label)}</span>${it.checked ? `<span class="mk">${ic("check")}</span>` : ""}</button>`;
  }).join("");
  m.hidden = false;
  // place it: under the element or at the pointer, kept inside the window
  let x, y;
  if (anchor && anchor.getBoundingClientRect) {
    const r = anchor.getBoundingClientRect();
    x = r.left; y = r.bottom + 2;
  } else {
    x = anchor.clientX ?? anchor.x ?? 0; y = anchor.clientY ?? anchor.y ?? 0;
  }
  const w = m.offsetWidth, h = m.offsetHeight, vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  if (x + w > vw - 4) x = Math.max(4, vw - w - 4);
  if (y + h > vh - 4) y = Math.max(4, (anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect().top - h - 2 : vh - h - 4));
  m.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;

  const buttons = () => [...m.querySelectorAll("button:not(:disabled)")];
  const away = (e) => { if (!m.contains(e.target)) closeMenu(); };
  const onKey = (e) => {
    const b = buttons(), i = b.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
    else if (e.key === "Tab") closeMenu();
    else if (e.key === "ArrowDown") { e.preventDefault(); (b[i + 1] || b[0])?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); (b[i - 1] || b[b.length - 1])?.focus(); }
    else if (e.key === "Home") { e.preventDefault(); b[0]?.focus(); }
    else if (e.key === "End") { e.preventDefault(); b[b.length - 1]?.focus(); }
  };
  const onClick = (e) => {
    const btn = e.target.closest("button[data-i]");
    if (!btn) return;
    const it = items[+btn.dataset.i];
    closeMenu(false, false);
    if (it && it.onClick) it.onClick();
  };
  const close = () => closeMenu();
  document.addEventListener("pointerdown", away, true);
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("scroll", close, true);
  window.addEventListener("resize", close);
  window.addEventListener("blur", close);
  m.addEventListener("click", onClick);
  open = {
    opener, onClose,
    off() {
      document.removeEventListener("pointerdown", away, true);
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("blur", close);
      m.removeEventListener("click", onClick);
    },
  };
  buttons()[0]?.focus({ preventScroll: true });
}

/** Close the menu (`restoreFocus`: give the focus back to the element that had it, e.g. after Esc). */
export function closeMenu(restoreFocus = false, callOnClose = true) {
  if (!open) return;
  const cur = open;
  open = null;
  cur.off();
  const m = node();
  m.hidden = true;
  m.innerHTML = "";
  if (restoreFocus && cur.opener && cur.opener.isConnected) cur.opener.focus();
  if (callOnClose && cur.onClose) cur.onClose();
}

export const isMenuOpen = () => open !== null;

/** Right-click on anything matching `selector` inside `container` opens a menu built by getItems(target). */
export function contextMenu(container, selector, getItems) {
  container.addEventListener("contextmenu", (e) => {
    const target = e.target.closest(selector);
    if (!target || !container.contains(target)) return;
    const items = getItems(target);
    if (!items || !items.length) return;
    e.preventDefault();
    openMenu(e, items);
  });
}
