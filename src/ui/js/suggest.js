// The suggestions under the Add tab's search box: your earlier sites ("mine"), the popular sites and the installed apps /
// Steam games that fit what is typed - Python ranks them and sends at most 8 (Api `suggest`).
//   header "SUGGESTIONS · “typed”", rows (icon, name cut at 30 letters, host or "exe · Steam game", a site / app badge),
//   a foot "Steam games sit under Steam" with "Clear my suggestions" while a "mine" row shows.
//   Up / Down choose, Enter or a click picks, Esc hides it, and it hides when the box loses the focus - so no timer watches
//   the window (the old window polled Win32 every 250 ms for that).
// The caller debounces (120 ms) and calls update(text); the dropdown makes its own Api call, drops answers that came too
// late, and paints with row nodes it keeps (never an innerHTML of the whole list).
import { ld } from "./bridge.js";
import { iconKey, iconUrl, wireIconReady } from "./appicon.js";
import { el, ic, setText } from "./ui.js";

export const MAX_ROWS = 8;
export const MAX_NAME_CHARS = 30;

/** A name cut to 30 letters with an ellipsis. */
export const clip = (name, n = MAX_NAME_CHARS) => (name.length > n ? name.slice(0, n - 1) + "…" : name);
export const headerText = (typed) => (typed ? `SUGGESTIONS · “${typed}”` : "SUGGESTIONS");
export const subText = (m) => (m.kind === "app" ? m.exe + (m.steam ? " · Steam game" : "") : m.host);
export const anyMine = (rows) => rows.some((r) => r.mine);

export function createSuggest({ input, onPick }) {
  const root = el(`<div class="sg" role="listbox" aria-label="Suggestions" hidden>` +
    `<div class="sg-h cap"></div><div class="sg-list"></div>` +
    `<div class="sg-f"><span class="small muted">Steam games sit under Steam</span><button class="btn btn-sm btn-ghost" type="button" data-clear hidden>Clear my suggestions</button></div></div>`);
  const head = root.querySelector(".sg-h"), list = root.querySelector(".sg-list"), clear = root.querySelector("[data-clear]");
  const nodes = [];
  let rows = [], active = -1, seq = 0, text = "";

  function node(i) {
    if (!nodes[i]) {
      const n = el(`<button type="button" class="sg-row" role="option"><span class="aic"><img alt="" decoding="async" draggable="false"></span>` +
        `<span class="sg-name"><b class="ellip"></b><span class="ellip small muted"></span></span><span class="chip chip-soft"></span></button>`);
      n._img = n.querySelector("img");
      n._b = n.querySelector("b");
      n._sub = n.querySelector(".sg-name span");
      n._badge = n.querySelector(".chip");
      n._i = i;
      list.append(n);
      nodes[i] = n;
    }
    return nodes[i];
  }

  function paint() {
    setText(head, headerText(text));
    rows.forEach((m, i) => {
      const n = node(i);
      n.hidden = false;
      setText(n._b, clip(m.name));
      n.title = m.name;
      setText(n._sub, subText(m));
      setText(n._badge, m.kind === "app" ? "app" : "site");
      const key = iconKey(m.kind, m.kind === "app" ? m.exe : m.host);
      if (n._img.dataset.icon !== key) { n._img.dataset.icon = key; n._img.src = iconUrl(m.kind, m.kind === "app" ? m.exe : m.host); }
    });
    for (let i = rows.length; i < nodes.length; i++) nodes[i].hidden = true;
    clear.hidden = !anyMine(rows);
    mark(-1);
  }

  function mark(i) {
    active = i;
    nodes.forEach((n, k) => { if (k < rows.length) n.setAttribute("aria-selected", String(k === i)); });
    if (i >= 0) nodes[i].scrollIntoView({ block: "nearest" });
  }

  const api = {
    el: root,
    /** Is the dropdown showing? */
    get open() { return !root.hidden; },
    hide() { root.hidden = true; seq++; active = -1; },
    /** Ask for what fits `value` and show it (nothing typed: hide). */
    async update(value) {
      text = value.trim();
      const mine = ++seq;
      if (!text) return api.hide();
      let found;
      try { found = await ld.call("suggest", text); } catch (_) { return; }
      if (mine !== seq || document.activeElement !== input) return;       // a later keystroke, or the box lost the focus meanwhile
      rows = found.slice(0, MAX_ROWS);
      if (!rows.length) return api.hide();
      paint();
      root.hidden = false;
    },
    destroy() { off(); root.remove(); },
  };

  const pick = (i) => { const m = rows[i]; api.hide(); if (m) onPick(m); };
  list.addEventListener("click", (e) => { const n = e.target.closest(".sg-row"); if (n) pick(n._i); });
  root.addEventListener("mousedown", (e) => e.preventDefault());           // (a click keeps the focus in the box, so it isn't hidden by blur first)
  clear.addEventListener("click", async () => {
    try { await ld.call("suggest_clear_history"); } catch (_) { return; }
    api.update(input.value);
  });
  input.addEventListener("keydown", (e) => {
    if (!api.open) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = rows.length, i = active < 0 ? (e.key === "ArrowDown" ? 0 : n - 1) : (active + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
      mark(i);
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();
      e.stopImmediatePropagation();                                        // (the box's own Enter - "submit" - must not also run)
      pick(active);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      api.hide();
    }
  });
  input.addEventListener("blur", () => api.hide());
  const off = wireIconReady(ld.on, root);
  return api;
}
