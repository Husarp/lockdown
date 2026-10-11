// The picker of Blocking > Add: what to block. A search box with suggestions (js/suggest.js), a filter (All / Installed apps /
// Websites / Program files), a list of what fits (virtual list, tick any number), "Browse apps" (the App browser dialog),
// "Browse for a file…" (Windows' Open dialog for an .exe, a Java game, a script or a shortcut - Python checks the file) and
// "Category…" (block everything in a category). What is ticked is the *selection*: the Add tab shows it as pills, asks
// for a display name when it is one thing, and gives every picked target the same blockers.
//
//   const picker = createPicker({ onChange, onEnter, notify });
//   picker.el  picker.selection() -> [target]   picker.add(target) / .remove(key) / .clear()   picker.refresh()   picker.reset(read = true)
//   target = {key, kind: "site" | "app" | "category", target: host | exe | category key, name, path, picked, steam, file, mine}
//
// The pure helpers (how typed text is read, hosts, rows) are exported for the node tests. They only decide what to show -
// webapi/blocking_edit.py checks the address, the file and the ways again (the page is untrusted).
import { ld } from "./bridge.js";
import { iconKey, iconUrl, wireIconReady } from "./appicon.js";
import { confirmDialog } from "./dialog.js";
import { openMenu } from "./menu.js";
import { createSuggest } from "./suggest.js";
import { el, ic, seg, setText } from "./ui.js";
import { createVList } from "./vlist.js";

export const SEARCH_MS = 120;
export const LIST_MAX = 60;
export const KINDS = [["all", "All"], ["apps", "Installed apps"], ["sites", "Websites"], ["files", "Program files"]];
export const HOSTED_EXT = [".jar", ".class", ".py", ".pyw", ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf"];
// The App browser and the categories are only needed when their button is pressed (or the apps filter is chosen): loaded then, so
// the tab's first visit fetches fewer files.
const appBrowser = () => import("./appbrowser.js");
const categories = () => import("./categories.js");
const HOST_RE = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

// ---------------------------------------------------------------- pure helpers

/** The file name in a typed path or name, lower case, without quotes ("C:\\Games\\Foo\\Foo.exe" -> "foo.exe"). */
export function fileName(text) {
  const t = String(text || "").trim().replace(/^"+|"+$/g, "").trim();
  return t.split(/[\\/]/).pop().toLowerCase();
}
/** Is this a path (has a folder part)? */
export const hasPath = (text) => /[\\/]/.test(String(text || "").trim().replace(/^"+|"+$/g, ""));
/** A program file run by an interpreter: a Java game, a script (blocker.apps.HOSTED). */
export const isHosted = (name) => HOSTED_EXT.some((ext) => String(name).toLowerCase().endsWith(ext));

/** Typed as an app: an .exe, or a program file (game.jar, start.bat) - a .py only as a path ("x.com.py" is a site). */
export function typedProgram(text) {
  const name = fileName(text);
  return name.endsWith(".exe") || (isHosted(name) && (!name.endsWith(".py") || hasPath(text)));
}

/** blocker.hosts.normalize_host: "https://www.Reddit.com/r/x" -> "reddit.com"; null when it isn't a site address. */
export function normalizeHost(text) {
  let host = String(text || "").trim().toLowerCase().replace(/^[a-z]+:\/\//, "");
  host = host.split(/[/?#:]/, 1)[0].replace(/^www\./, "");
  return HOST_RE.test(host) ? host : null;
}

/** What typed text stands for: {kind: "app", target, path, file} / {kind: "site", target} / {error}. */
export function classify(text) {
  const t = String(text || "").trim();
  if (!t) return { error: "Pick a site, an app or a category first." };
  if (typedProgram(t)) {
    const name = fileName(t);
    return { kind: "app", target: name, path: hasPath(t) ? t.replace(/^"+|"+$/g, "").trim() : null, file: isHosted(name) };
  }
  const host = normalizeHost(t);
  return host ? { kind: "site", target: host } : { error: `Write a site like reddit.com (“${t}” isn't one).` };
}

export const keyOf = (kind, target) => `${kind}:${target}`;

/** A list row for a suggestion from Python ({kind: site | app, ...}). */
export function rowOf(m) {
  if (m.kind === "app") {
    return { key: keyOf("app", m.exe), kind: "app", target: m.exe, name: m.name, sub: m.exe + (m.steam ? " · Steam" : ""), badge: m.running ? "running" : m.steam ? "Steam" : "app",
      path: m.path || null, steam: !!m.steam, file: isHosted(m.exe), icon: ["app", m.exe] };
  }
  return { key: keyOf("site", m.host), kind: "site", target: m.host, name: m.name, sub: m.host, badge: m.mine ? "mine" : "site", mine: !!m.mine, icon: ["site", m.host] };
}

/** The "Add website …" / "Block program …" row for typed text that isn't in the list (null when it is, or isn't an address). */
export function typedRow(text, rows) {
  const c = classify(text);
  if (c.error) return null;
  const key = keyOf(c.kind, c.target);
  if (rows.some((r) => r.key === key)) return null;
  if (c.kind === "site") return { key, kind: "site", target: c.target, name: `Add website “${c.target}”`, sub: "", badge: "site", typed: true, icon: ["site", c.target] };
  return { key, kind: "app", target: c.target, name: `Block ${c.file ? "program file" : "program"} “${c.target}”`, sub: "", badge: c.file ? "file" : "app", typed: true,
    path: c.path, file: c.file, steam: false, icon: ["app", c.target] };
}

/** The selection entry for a list row (the typed rows start with no display name: Python guesses one). */
export function targetOf(row) {
  return { key: row.key, kind: row.kind, target: row.target, name: row.typed ? "" : row.name, path: row.path || null,
    picked: row.kind === "app" && !row.typed, steam: !!row.steam, steamKnown: true, file: !!row.file, mine: !!row.mine };
}

/** The words for a pill / the sentence: a category is "category · Name". */
export const labelOf = (t) => (t.kind === "category" ? `category · ${t.name}` : t.name || t.target);

// ---------------------------------------------------------------- the picker

export function createPicker({ onChange = () => {}, onEnter = () => {}, notify = () => {} } = {}) {
  const root = el(`<div class="card ad-pick">
    <div class="ad-pick-top">
      <div class="search search-lg ad-search">${ic("search")}<input class="input" type="text" placeholder="Search apps, or type a website like example.com" autocomplete="off" spellcheck="false" aria-label="Search apps and sites" aria-autocomplete="list"></div>
      <div class="hstack wrap">${seg("adkind", KINDS, "all")}</div>
      <div class="hstack wrap">
        <button class="btn btn-sm" type="button" data-browse>${ic("app")}Browse apps</button>
        <button class="btn btn-sm" type="button" data-file>${ic("folder")}Browse for a file…</button>
        <button class="btn btn-sm" type="button" data-cat aria-haspopup="menu">${ic("layers")}Category…</button></div>
    </div>
    <div class="ad-list" role="listbox" aria-multiselectable="true" aria-label="Apps and websites"></div>
    <div class="ad-empty" hidden></div>
    <div class="ad-foot small muted" role="status" aria-live="polite"></div>
  </div>`);
  const input = root.querySelector("input.input"), listEl = root.querySelector(".ad-list"), emptyEl = root.querySelector(".ad-empty"), foot = root.querySelector(".ad-foot");
  const sel = new Map();                // key -> target
  const files = [];                     // program files picked or typed in this session (the "Program files" filter lists them)
  let kind = "all", query = "", rows = [], seq = 0, timer = 0, early = null;

  const dropdown = createSuggest({ input, onPick: (m) => { choose(targetOf(rowOf(m))); clearText(); } });
  input.parentNode.append(dropdown.el);

  // ---------------------------------------------------------------- the list
  const vl = createVList(listEl, { rowVar: "--row-h-sm", rows: [], key: (r) => r.key, create: createRow, render: renderRow });

  function createRow() {
    const n = el(`<label class="row row-sm member-row check ad-row" role="option"><span class="check"><input type="checkbox"></span>` +
      `<span class="aic"><img alt="" decoding="async" draggable="false"></span><span class="name"><b></b><span></span></span><span class="chip chip-soft"></span></label>`);
    n._in = n.querySelector("input");
    n._img = n.querySelector("img");
    n._b = n.querySelector("b");
    n._sub = n.querySelector(".name span");
    n._badge = n.querySelector(".chip");
    return n;
  }

  function renderRow(n, row) {
    n._row = row;
    n._in.checked = sel.has(row.key);
    setText(n._b, row.name);
    setText(n._sub, row.sub);
    n._sub.hidden = !row.sub;
    setText(n._badge, row.badge);
    n.setAttribute("aria-selected", String(sel.has(row.key)));
    const key = iconKey(row.icon[0], row.icon[1]);
    if (n._img.dataset.icon !== key) { n._img.dataset.icon = key; n._img.src = iconUrl(row.icon[0], row.icon[1]); }
  }

  const matches = (r) => !query || `${r.name} ${r.target}`.toLowerCase().includes(query.toLowerCase());

  /** Read the list again for the filter and the text (one Api call). */
  async function refresh() {
    const my = ++seq;
    let list = [], note = "", loading = false;
    try {
      if (kind === "files") list = files.filter(matches);
      else if (kind === "apps") {
        const r = await ld.call("apps_list", "all", query);
        loading = r.loading;
        list = r.apps.map((a) => rowOf({ kind: "app", ...a }));
        note = (await appBrowser()).countText(r.total, r.apps.length, r.loading);
      } else {
        const asked = kind === "all" && !query && early ? early : ld.call("suggest", query, kind === "sites" ? "sites" : "all", LIST_MAX);
        early = null;                                // (only the very first read can use it)
        list = (await asked).map(rowOf);
      }
    } catch (err) {
      if (my === seq) { setText(foot, (err && err.message) || "The list isn't available."); }
      return;
    }
    if (my !== seq) return;
    let typed = typedRow(query, list);
    if (typed && ((kind === "apps" && typed.kind !== "app") || (kind === "sites" && typed.kind !== "site") || (kind === "files" && !typed.file))) typed = null;
    rows = typed ? [typed, ...list] : list;
    vl.setRows(rows);
    emptyEl.hidden = rows.length > 0 || loading;
    setText(emptyEl, kind === "files" && !query ? "Program files - a Java game, a script, a shortcut - are picked with Browse for a file…" : "Nothing fits. Type a website like example.com to block it.");
    setText(foot, note);
  }

  function later() {
    clearTimeout(timer);
    if (!input.value.trim()) dropdown.hide();
    timer = setTimeout(() => { timer = 0; query = input.value.trim(); refresh(); dropdown.update(input.value); }, SEARCH_MS);
  }

  function clearText(read = true) {
    clearTimeout(timer);
    timer = 0;
    input.value = "";
    query = "";
    dropdown.hide();
    if (read) refresh();
  }

  // ---------------------------------------------------------------- the selection
  function choose(t) {
    if (t.file && !files.some((f) => f.key === t.key)) files.unshift({ key: t.key, kind: "app", target: t.target, name: t.name || t.target, sub: t.target, badge: "file", path: t.path, file: true, steam: false, icon: ["app", t.target] });
    sel.set(t.key, t);
    changed();
    if (t.kind === "app" && !t.file && !t.steam && !t.steamKnown) learnSteam(t);
  }

  /** An app picked in the App browser doesn't say whether it is a Steam game: ask the catalog (its defaults depend on it). */
  async function learnSteam(t) {
    if (t.steamAsked) return;
    t.steamAsked = true;
    try {
      const r = await ld.call("apps_list", "games", t.target);
      if (r.apps.some((a) => a.exe === t.target) && sel.get(t.key) === t) { t.steam = true; changed(); }
    } catch (_) { /* the defaults stay Close app */ }
  }

  function changed() {
    vl.refresh();
    onChange();
  }

  function commitTyped() {
    const text = input.value.trim();
    if (!text) return true;
    const c = classify(text);
    if (c.error) { notify(c.error); return false; }
    choose({ key: keyOf(c.kind, c.target), kind: c.kind, target: c.target, name: "", path: c.path || null, picked: false, steam: false, steamKnown: true, file: !!c.file, mine: false });
    clearText();
    return true;
  }

  // ---------------------------------------------------------------- buttons
  async function browseFile() {
    let r;
    try { r = await ld.call("pick_program_file"); } catch (err) { notify((err && err.message) || "The file dialog isn't available."); return; }
    if (!r) return;
    if (r.whole_interpreter) {
      const yes = await confirmDialog({ key: "ad-interpreter", title: `Block all of ${r.exe}?`, ok: `Block all of ${r.exe}`, danger: true,
        text: `${r.exe} runs other programs too - blocking it blocks every one of them. To block one game or script, pick its own file (.jar, .py, .bat ...) or its shortcut.` });
      if (!yes) return;
    }
    choose({ key: keyOf("app", r.exe), kind: "app", target: r.exe, name: r.name || r.exe, path: r.path || null, picked: true, steam: false, steamKnown: true, file: r.kind !== "exe", mine: false });
  }

  async function pickCategory(btn) {
    let cats, colorOf;
    try {
      const mod = await categories();
      colorOf = mod.colorOf;
      cats = await mod.loadCategories();
    } catch (err) { notify((err && err.message) || "The categories aren't available."); return; }
    openMenu(btn, [{ heading: "Block a whole category" }, ...cats.map((c) => ({
      label: c.name, swatch: colorOf(c),
      onClick: () => choose({ key: keyOf("category", c.key), kind: "category", target: c.key, name: c.name, path: null, picked: false, steam: false, file: false, mine: false }),
    }))]);
  }

  // ---------------------------------------------------------------- events
  input.addEventListener("input", later);
  input.addEventListener("keydown", (e) => {            // (js/suggest.js has had its say first: an arrowed-to suggestion is picked by it)
    if (e.key !== "Enter" || e.isComposing) return;
    e.preventDefault();
    if (commitTyped()) onEnter();
  });
  root.addEventListener("seg", (e) => {
    if (e.detail.name !== "adkind") return;
    kind = e.detail.value;
    refresh();
  });
  listEl.addEventListener("change", (e) => {
    const n = e.target.closest(".vl-row");
    if (!n || !n._row) return;
    const row = n._row;
    if (e.target.checked) choose(targetOf(row)); else { sel.delete(row.key); changed(); }
  });
  root.querySelector("[data-browse]").addEventListener("click", async () => (await appBrowser()).openAppBrowser({ mode: "pick", onPick: (app) => choose({ key: keyOf("app", app.exe), kind: "app", target: app.exe, name: app.name, path: app.path || null, picked: true, steam: false, file: isHosted(app.exe), mine: false }) }));
  root.querySelector("[data-file]").addEventListener("click", browseFile);
  root.querySelector("[data-cat]").addEventListener("click", (e) => pickCategory(e.currentTarget));
  const offIcons = wireIconReady(ld.on, root);
  const offReady = ld.on("apps_ready", () => { if (root.offsetParent !== null && kind !== "sites" && kind !== "files") refresh(); });

  return {
    el: root,
    input,
    selection: () => [...sel.values()],
    has: (key) => sel.has(key),
    add(t) { choose(t); },
    remove(key) { if (sel.delete(key)) changed(); },
    clear() { if (sel.size) { sel.clear(); changed(); } },
    /** A fresh picker: nothing typed, nothing ticked, All. */
    reset(read = true) {
      sel.clear();
      kind = "all";
      for (const b of root.querySelectorAll('[data-seg="adkind"] button')) b.setAttribute("aria-pressed", String(b.dataset.v === "all"));
      clearText(read);
      onChange();
    },
    refresh,
    /** The first read of the list may use an answer that was asked for already (a promise of the same call). */
    useEarly(promise) { early = promise; },
    /** Fetch what the buttons need now that the tab is up (nothing waits for it). */
    warm() { appBrowser(); categories(); },
    focus: () => input.focus(),
    setVisible(visible) { if (!visible) dropdown.hide(); },
    destroy() { clearTimeout(timer); offIcons(); offReady(); dropdown.destroy(); vl.destroy(); },
  };
}
