// Categories: the list (cached), the editor dialog, the "New category" prompt, and the two guarded changes the right-click
// menu makes (put an app / site in a category, make a category and put it there). Python decides and checks everything
// (webapi/categories.py): adding, deleting and moving something to a category go through the Anti-Bypass challenge while
// it is locked (`guarded`), recolouring is free.
//
//   loadCategories()          [{key, name, color, color_light, builtin}] (asked once, kept until something changes)
//   colorOf(cat)              the colour for the current theme
//   openCategoryEditor({onClose})   rows swatch + name; built-ins recolour only; own ones Delete; + New category; Done
//   promptNewCategory()       a name and a colour -> {name, color} or null
//   assignCategory(kind, name, key) / assignNewCategory(kind, name, {name, color})   -> the reply, or CANCELLED
// A change anywhere fires the window event "ld:categories-changed" (pages showing categories reload on it).
import { ld } from "./bridge.js";
import { openDialog, isOpen } from "./dialog.js";
import { guarded, CANCELLED } from "./challenge.js";
import { esc, el, confirmButton } from "./ui.js";

export { CANCELLED };
export const NEW_COLORS = ["#8E6CEF", "#2F9FD8", "#D4A017", "#E0559B", "#20B2AA", "#9C6B3F"];
export const MAX_NAME = 30;
const HEX = /^#[0-9a-fA-F]{6}$/;

// ---------------------------------------------------------------- plain helpers (node tests)

/** The next suggested colour for a new category: NEW_COLORS in turn, by how many of your own there are. */
export function nextColor(cats) {
  return NEW_COLORS[cats.filter((c) => !c.builtin).length % NEW_COLORS.length];
}

/** Why `name` can't be a new category's name (or ""): empty, too long, or already taken (any case). */
export function nameProblem(name, cats) {
  const text = String(name == null ? "" : name).trim().replace(/\s+/g, " ");
  if (!text) return "Give the category a name.";
  if (text.length > MAX_NAME) return `A category name can be at most ${MAX_NAME} letters.`;
  if (cats.some((c) => c.name.toLowerCase() === text.toLowerCase())) return `There is already a category called ${text}.`;
  return "";
}

export const isHex = (v) => HEX.test(String(v));

/** A category's colour: `color` on the dark themes, `color_light` on the light one. */
export function colorOf(cat, theme) {
  const t = theme || (typeof document !== "undefined" ? document.documentElement.dataset.theme : "dark");
  return t === "light" ? cat.color_light : cat.color;
}

// ---------------------------------------------------------------- the list

let cache = null;

export function loadCategories() {
  if (!cache) cache = ld.call("categories_list").catch((err) => { cache = null; throw err; });
  return cache;
}

/** Forget the list and tell the pages. */
export function categoriesChanged() {
  cache = null;
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("ld:categories-changed"));
}

/** The stylesheet comes with the component. */
export function ensureCss() {
  if (typeof document === "undefined" || document.querySelector('link[href$="css/categories.css"]')) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = new URL("../css/categories.css", import.meta.url).href;
  document.head.append(link);
}

const message = (err) => (err && err.message) || "Something went wrong.";

// ---------------------------------------------------------------- guarded changes (used by the menu)

/** Put an app (.exe) or site (host) in a category. -> the reply {kind, name, category} or CANCELLED. Throws on a refusal. */
export async function assignCategory(kind, name, key) {
  const r = await guarded(ld.call("category_assign", kind, name, key));
  if (r !== CANCELLED) categoriesChanged();
  return r;
}

/** Make a category and put the app / site in it, as one change. */
export async function assignNewCategory(kind, name, { name: catName, color }) {
  const r = await guarded(ld.call("category_add_and_assign", kind, name, catName, color));
  if (r !== CANCELLED) categoriesChanged();
  return r;
}

// ---------------------------------------------------------------- colour picking (swatches + <input type=color>)

function pickerHtml(current, label = "Custom") {
  const c = String(current || "").toLowerCase();
  return NEW_COLORS.map((v) => `<button type="button" data-c="${v}" style="--c:${v}" aria-label="Colour ${v}" aria-pressed="${v.toLowerCase() === c}"></button>`).join("") +
    `<label>${esc(label)} <input type="color" value="${isHex(current) ? esc(current) : NEW_COLORS[0]}" aria-label="Pick any colour"></label>`;
}

function markPressed(root, hex) {
  const c = String(hex).toLowerCase();
  for (const b of root.querySelectorAll("button[data-c]")) b.setAttribute("aria-pressed", String(b.dataset.c.toLowerCase() === c));
  const input = root.querySelector('input[type="color"]');
  if (input && input.value.toLowerCase() !== c) input.value = c;
}

// ---------------------------------------------------------------- the New category prompt

/** Ask for a name and a colour. Resolves {name, color}, or null (Cancel, Esc, X). */
export function promptNewCategory({ title = "New category", ok = "Add" } = {}) {
  ensureCss();
  let known = [];
  let color = NEW_COLORS[0];
  const body = el(`<div class="cat-ed"><label class="grid" style="gap:var(--sp-1)"><span class="cap">Name</span>` +
    `<input class="input" maxlength="${MAX_NAME}" autocomplete="off" spellcheck="false" aria-label="Name of the new category"></label>` +
    `<div><span class="cap">Colour</span><div class="cat-pick" style="padding-left:0;padding-top:var(--sp-2)">${pickerHtml(color)}</div></div>` +
    `<div class="cat-err" role="alert"></div></div>`);
  const input = body.querySelector("input.input"), err = body.querySelector(".cat-err"), pick = body.querySelector(".cat-pick");
  loadCategories().then((cats) => {
    known = cats;
    color = nextColor(cats);
    markPressed(pick, color);
  }).catch(() => {});
  pick.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-c]");
    if (b) { color = b.dataset.c; markPressed(pick, color); }
  });
  pick.addEventListener("change", (e) => {
    if (e.target.type === "color") { color = e.target.value; markPressed(pick, color); }
  });
  const submit = (h) => {
    const problem = nameProblem(input.value, known);
    err.textContent = problem;
    if (problem) { input.focus(); return false; }
    h.close({ name: input.value.trim().replace(/\s+/g, " "), color });
    return false;
  };
  const h = openDialog({
    key: "newcategory", title, body,
    buttons: [{ label: "Cancel", value: null }, { label: ok, kind: "primary", onClick: submit }],
  });
  if (!h) return Promise.resolve(null);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(h); } });
  input.addEventListener("input", () => { err.textContent = ""; });
  input.focus();
  return h.done.then((v) => (v && typeof v === "object" ? v : null));
}

// ---------------------------------------------------------------- the editor

const EDITOR = "categories";

/**
 * The category editor dialog. `onClose()` runs when it is finished with (Done, X, Esc) - not when a challenge for one of
 * its changes takes the dialog over: it opens again by itself afterwards, so the owner stays where they were.
 */
export function openCategoryEditor({ onClose } = {}) {
  ensureCss();
  const body = el(`<div class="cat-ed"><p class="muted small" style="margin:0">Click a colour to change it. Deleting a category moves its apps and sites back to their default.</p>` +
    `<div class="cat-list" role="list" aria-busy="true"></div>` +
    `<div class="cat-new" hidden><label class="grid" style="gap:var(--sp-1)"><span class="cap">New category</span>` +
    `<input class="input" maxlength="${MAX_NAME}" autocomplete="off" spellcheck="false" aria-label="Name of the new category"></label>` +
    `<div class="cat-pick"></div><div class="hstack"><button class="btn btn-primary btn-sm" type="button" data-add>Add</button>` +
    `<button class="btn btn-sm" type="button" data-cancel-new>Cancel</button></div></div>` +
    `<div class="cat-err" role="alert"></div></div>`);
  const list = body.querySelector(".cat-list"), panel = body.querySelector(".cat-new"), err = body.querySelector(":scope > .cat-err");
  const newInput = panel.querySelector("input.input"), newPick = panel.querySelector(".cat-pick");
  let cats = [], newColor = NEW_COLORS[0], closed = false;

  const fail = (e) => { err.textContent = message(e); };
  const reopen = () => { if (!closed && !isOpen(EDITOR)) openCategoryEditor({ onClose }); };

  function rowHtml(c) {
    return `<div class="cat-row" role="listitem" data-key="${esc(c.key)}">` +
      `<button type="button" class="cat-sw" style="background:${esc(colorOf(c))}" aria-label="Colour of ${esc(c.name)}" aria-expanded="false"></button>` +
      `<span class="cat-name">${esc(c.name)}</span><span class="cat-act">${c.builtin ? '<span class="chip chip-soft chip-xs">Built-in</span>' : ""}</span>` +
      `<div class="cat-pick" hidden>${pickerHtml(colorOf(c))}</div></div>`;
  }

  function render() {
    list.innerHTML = cats.map(rowHtml).join("");
    list.removeAttribute("aria-busy");
    cats.forEach((c, i) => {
      if (c.builtin) return;
      const del = confirmButton({ label: "Delete", confirmLabel: "Click again to delete", cls: "btn btn-sm btn-danger", ms: 4000, onConfirm: () => remove(c) });
      list.children[i].querySelector(".cat-act").append(del);
    });
  }

  async function refresh() {
    try { cats = await loadCategories(); render(); } catch (e) { fail(e); list.removeAttribute("aria-busy"); }
  }

  async function recolor(key, hex, row) {
    err.textContent = "";
    try {
      await ld.call("category_set_color", key, hex);
    } catch (e) { fail(e); return; }
    const c = cats.find((x) => x.key === key);
    if (c) { c.color = hex; c.color_light = hex; }     // (the cached list is this same array)
    row.querySelector(".cat-sw").style.background = hex;
    markPressed(row.querySelector(".cat-pick"), hex);
    categoriesChanged();
  }

  async function remove(c) {
    err.textContent = "";
    try {
      const r = await guarded(ld.call("category_remove", c.key));
      if (r !== CANCELLED) categoriesChanged();
    } catch (e) { fail(e); return; }
    if (isOpen(EDITOR)) refresh(); else reopen();
  }

  async function add() {
    const problem = nameProblem(newInput.value, cats);
    err.textContent = problem;
    if (problem) { newInput.focus(); return; }
    try {
      const r = await guarded(ld.call("category_add", newInput.value.trim().replace(/\s+/g, " "), newColor));
      if (r !== CANCELLED) categoriesChanged();
    } catch (e) { fail(e); return; }
    if (isOpen(EDITOR)) { panel.hidden = true; newInput.value = ""; refresh(); } else reopen();
  }

  list.addEventListener("click", (e) => {
    const sw = e.target.closest(".cat-sw");
    if (sw) {
      const pick = sw.closest(".cat-row").querySelector(".cat-pick");
      pick.hidden = !pick.hidden;
      sw.setAttribute("aria-expanded", String(!pick.hidden));
      return;
    }
    const b = e.target.closest("button[data-c]");
    if (b) { const row = b.closest(".cat-row"); recolor(row.dataset.key, b.dataset.c, row); }
  });
  list.addEventListener("change", (e) => {
    if (e.target.type === "color") { const row = e.target.closest(".cat-row"); recolor(row.dataset.key, e.target.value, row); }
  });

  newPick.innerHTML = pickerHtml(newColor);
  newPick.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-c]");
    if (b) { newColor = b.dataset.c; markPressed(newPick, newColor); }
  });
  newPick.addEventListener("change", (e) => { if (e.target.type === "color") { newColor = e.target.value; markPressed(newPick, newColor); } });
  panel.querySelector("[data-add]").addEventListener("click", add);
  panel.querySelector("[data-cancel-new]").addEventListener("click", () => { panel.hidden = true; err.textContent = ""; });
  newInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });

  const h = openDialog({
    key: EDITOR, title: "Categories", body,
    buttons: [
      { label: "+ New category", onClick: () => {
        panel.hidden = false;
        newColor = nextColor(cats);
        markPressed(newPick, newColor);
        newInput.focus();
        return false;
      } },
      { label: "Done", kind: "primary", grow: true, value: "done" },
    ],
    onClose: (result) => {
      if (result === "replaced") return;       // a challenge took the dialog; it comes back afterwards
      closed = true;
      if (onClose) onClose();
    },
  });
  if (h) refresh();
  return h;
}
