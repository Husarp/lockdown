// "Coming soon": what a page shows until its ticket replaces it (see pages/index.js).
import { empty, esc } from "../ui.js";

export function makeStub(id, title) {
  return {
    freshMs: Infinity,
    mount(el) {
      el.innerHTML = `<div class="page"><div class="page-h"><h1>${esc(title)}</h1></div>` +
        `<div class="card">${empty("k-brand", "sparkle", "Coming soon", "This page isn't in the new window yet.")}</div></div>`;
    },
    load() {},
  };
}
