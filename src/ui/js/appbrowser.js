// The App browser dialog: every installed app and Steam game (and what is running right now), searchable.
//   openAppBrowser({mode: "browse" | "pick", onPick(app), goBlocking, onDone})
//   browse  Screen Time: a list to look through; right-click an app for its menu (category, block it, add to a group ...)
//   pick    Add / Groups / Modes: clicking an app (or Enter) picks it - onPick({name, exe, path}) - and "Pick a program
//           file..." chooses any .exe / .jar / script / shortcut in Windows' Open dialog (checked in Python)
// The list comes from the agent's cached catalog (Api `apps_list`, at most 60 best matches + the total), so the dialog opens
// at once and a search is one small call; the search waits 120 ms after the last key. While the catalog is still loading the
// dialog says so and fills in when the `apps_ready` push arrives. Rows are a virtual list with the real icons.
import { ld } from "./bridge.js";
import { openDialog, closeDialog } from "./dialog.js";
import { createVList } from "./vlist.js";
import { iconUrl, iconKey, wireIconReady } from "./appicon.js";
import { openAppMenu } from "./appmenu.js";
import { ensureCss } from "./categories.js";
import { el, ic, seg } from "./ui.js";

export const SEARCH_DELAY_MS = 120;
const KEY = "appbrowser";
const FILTERS = [["all", "All"], ["running", "Running"], ["games", "Games"]];
let saved = { query: "", filter: "all" };       // where the owner was, for when the dialog opens again after a challenge

/** "12 apps" / "1 500 apps - first 60 shown, type to narrow down" - the line above the list. Plain, tested in node. */
export function countText(total, shown, loading) {
  if (loading) return "Loading apps…";
  const n = `${total} app${total === 1 ? "" : "s"}`;
  return total > shown ? `${n} · first ${shown} shown - type to narrow down` : n;
}

export function openAppBrowser(opts = {}) {
  ensureCss();
  const pick = opts.mode === "pick" && typeof opts.onPick === "function";
  const state = { ...saved };
  let seq = 0, timer = 0, closed = false, finished = false;
  let list = null, offIcons = null, offReady = null;

  const body = el(`<div class="ab ${pick ? "ab-pick" : "ab-browse"}">` +
    `<div class="ab-top"><label class="search">${ic("search")}<input class="input" type="search" placeholder="Search apps and Steam games…" autocomplete="off" spellcheck="false" aria-label="Search apps"></label>` +
    (pick ? `<button class="btn" type="button" data-file>${ic("folder")}Pick a program file…</button>` : "") + `</div>` +
    `<div class="ab-line"><span class="ab-count muted small" role="status" aria-live="polite">Loading apps…</span><span class="grow"></span>${seg("abfilter", FILTERS, state.filter)}</div>` +
    `<div class="notice k-limit ab-warn" hidden>${ic("alert")}<div><div class="ab-warn-text"></div><div class="hstack"><button class="btn btn-sm btn-primary" type="button" data-yes></button><button class="btn btn-sm" type="button" data-no>Cancel</button></div></div></div>` +
    `<div style="position:relative"><div class="ab-list" role="listbox" aria-label="Apps"></div><div class="ab-empty" hidden></div></div>` +
    `<p class="small muted" style="margin:0">${pick ? "Click an app to pick it · right-click for more" : "Right-click an app to set its category, block it or add it to a group"}</p></div>`);
  const input = body.querySelector("input.input"), count = body.querySelector(".ab-count"), empty = body.querySelector(".ab-empty");
  const warn = body.querySelector(".ab-warn");
  input.value = state.query;

  const setCount = (text, bad = false) => { count.textContent = text; count.classList.toggle("bad", bad); };

  // ---------------------------------------------------------------- rows
  function createRow() {
    const row = el(`<div class="row row-sm ab-row" role="option" tabindex="0"><span class="aic"><img data-icon="" alt="" decoding="async" draggable="false"></span>` +
      `<span class="name"><b></b><span></span></span><span class="ab-run" hidden>RUNNING</span></div>`);
    row._img = row.querySelector("img");
    row._b = row.querySelector("b");
    row._sub = row.querySelector(".name span");
    row._run = row.querySelector(".ab-run");
    return row;
  }

  function renderRow(row, app) {
    row._app = app;
    if (row._b.textContent !== app.name) row._b.textContent = app.name;
    const sub = app.exe + (app.steam ? " · Steam" : "");
    if (row._sub.textContent !== sub) row._sub.textContent = sub;
    row._run.hidden = !app.running;
    const key = iconKey("app", app.exe);
    if (row._img.dataset.icon !== key) {
      row._img.dataset.icon = key;
      row._img.src = iconUrl("app", app.exe);
    }
  }

  const choose = (app) => {
    if (!pick) return;
    closeDialog({ picked: { name: app.name, exe: app.exe, path: app.path || null } });
  };

  const menuFor = (event, app) => {
    openAppMenu(event, { kind: "app", name: app.exe, display: app.name, path: app.path || null }, {
      notify: (text) => setCount(text),
      goBlocking: opts.goBlocking,
      onDone: opts.onDone,
      restore: () => { if (!finished) openAppBrowser(opts); },
    });
  };

  // ---------------------------------------------------------------- the list
  async function run() {
    const my = ++seq;
    let r;
    try {
      r = await ld.call("apps_list", state.filter, state.query);
    } catch (err) {
      if (my === seq && !closed) setCount(err.message || "The app list isn't available.", true);
      return;
    }
    if (my !== seq || closed) return;
    if (r.loading) {                       // the catalog isn't ready: apps_ready runs this again
      list.setRows([]);
      empty.hidden = true;
      setCount(countText(0, 0, true));
      return;
    }
    list.setRows(r.apps);
    empty.textContent = "No apps found.";
    empty.hidden = r.apps.length > 0;
    setCount(countText(r.total, r.apps.length, false));
  }

  function later() {
    clearTimeout(timer);
    timer = setTimeout(() => { timer = 0; state.query = input.value.trim(); saved.query = state.query; run(); }, SEARCH_DELAY_MS);
  }

  // ---------------------------------------------------------------- Pick a program file
  async function pickFile() {
    warn.hidden = true;
    let r;
    try { r = await ld.call("pick_program_file"); } catch (err) { setCount(err.message, true); return; }
    if (!r) return;
    const app = { name: r.name, exe: r.exe, path: r.path };
    if (!r.whole_interpreter) return choose(app);
    // java.exe / cmd.exe ... itself: blocking it blocks every program it runs - asked first
    body.querySelector(".ab-warn-text").textContent = `${r.exe} runs other programs too - blocking it blocks every one of them. To block one game or script, pick its own file (.jar, .py, .bat ...) or its shortcut.`;
    const yes = warn.querySelector("[data-yes]");
    yes.textContent = `Block all of ${r.exe}`;
    yes.onclick = () => choose(app);
    warn.hidden = false;
    yes.focus();
  }

  // ---------------------------------------------------------------- the dialog
  const h = openDialog({
    key: KEY, title: pick ? "Browse apps" : "Apps", wide: true, body,
    buttons: [{ label: pick ? "Cancel" : "Close", value: undefined }],
    onClose: (result) => {
      if (result === "replaced") { cleanup(); return; }     // a challenge / prompt took it over: the menu opens it again
      finished = true;
      cleanup();
      if (result && result.picked && opts.onPick) opts.onPick(result.picked);
    },
  });
  if (!h) return null;

  function cleanup() {
    closed = true;
    clearTimeout(timer);
    if (list) list.destroy();
    if (offIcons) offIcons();
    if (offReady) offReady();
  }

  list = createVList(body.querySelector(".ab-list"), {
    rowVar: "--row-h-sm", rows: [], create: createRow, render: renderRow,
  });
  offIcons = wireIconReady(ld.on, body);
  offReady = ld.on("apps_ready", () => { if (!closed) run(); });
  const listEl = body.querySelector(".ab-list");

  input.addEventListener("input", later);
  body.addEventListener("seg", (e) => {
    if (e.detail.name !== "abfilter") return;
    state.filter = e.detail.value;
    saved.filter = state.filter;
    run();
  });
  listEl.addEventListener("click", (e) => {
    const row = e.target.closest(".ab-row");
    if (row && row._app) choose(row._app);
  });
  listEl.addEventListener("contextmenu", (e) => {
    const row = e.target.closest(".ab-row");
    if (!row || !row._app) return;
    e.preventDefault();
    menuFor(e, row._app);
  });
  listEl.addEventListener("keydown", (e) => {
    const row = e.target.closest && e.target.closest(".ab-row");
    if (!row || !row._app) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(row._app); }
    else if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
      e.preventDefault();
      const r = row.getBoundingClientRect();
      menuFor({ x: r.left + 24, y: r.bottom }, row._app);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const i = row._i + (e.key === "ArrowDown" ? 1 : -1);
      if (i < 0 || i >= list.rows.length) return;
      const find = () => [...listEl.querySelectorAll(".ab-row")].find((n) => !n.hidden && n._i === i);
      const n = find();
      if (n) n.focus();
      else { list.scrollToIndex(i); requestAnimationFrame(() => requestAnimationFrame(() => { const m = find(); if (m) m.focus(); })); }
    }
  });
  const file = body.querySelector("[data-file]");
  if (file) file.addEventListener("click", pickFile);
  warn.querySelector("[data-no]").addEventListener("click", () => { warn.hidden = true; input.focus(); });
  input.focus();
  run();
  return h;
}
