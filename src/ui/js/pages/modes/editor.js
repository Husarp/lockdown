// The mode editor of the Modes page (BLUEPRINT 3.7): name, categories to block, "Also block" (from your blocklist, or any
// other site or app), Pomodoro, mute, and the automatic times. Loaded the first time you press New mode or Edit.
//
// It only collects what is typed and sends it: Python (webapi/modes.py) checks every field with the old window's messages,
// checks every new site or app again, and decides whether saving or deleting weakens a locked mode (-> a challenge ticket
// that `guarded()` turns into the challenge; Cancel keeps this editor open with the edits).
// "+ Other site or app" is the same picker as Blocking > Add (js/targetpicker.js, compact: the search box with its suggestions,
// Browse apps, Browse for a file, Category...), a display name, and the same "When blocked" boxes (js/ways.js, with the note
// about program files); both are loaded the first time that button is pressed.
import { iconImg, wireIconReady } from "../../appicon.js";
import { ld } from "../../bridge.js";
import { colorOf } from "../../categories.js";
import { CANCELLED, guarded } from "../../challenge.js";
import { confirmButton, days as dayChips, el, esc, field, ic, kic, setText, stepper, sw, tpRange } from "../../ui.js";
import { createWays, defaultWays, groupOf, GROUPS } from "../../ways.js";

export const POMODORO_FIELDS = [["work", "Focus", "min"], ["break", "Break", "min"], ["rounds", "Rounds", ""], ["long", "Long break", "min"]];
const CAT_HELP = "Categories are set on Screen Time > Apps / Websites; blocked sites and apps count as Distracting unless you chose otherwise.";
export const NEW_MODE = { id: "", name: "", builtin: false, categories: ["distracting"], items: [], groups: [], extra: [], mute: false,
  pomodoro: null, windows: null, held: false };

// ---------------------------------------------------------------- plain helpers (node tests)

/** The "Also block" chips of a mode, groups first, then blocklist items, then the extra sites and apps. `brief` =
 *  blocklist_brief(). Something that is no longer on the blocklist has no chip (Python drops it when saving). */
export function chipsOf(mode, brief) {
  const groups = new Map(brief.groups.map((g) => [g.id, g]));
  const items = new Map(brief.items.map((i) => [i.id, i]));
  const out = [];
  for (const id of mode.groups) if (groups.has(id)) out.push({ key: `g:${id}`, kind: "group", id, label: `Group: ${groups.get(id).name}`, icon: null });
  for (const id of mode.items) {
    const i = items.get(id);
    if (i) out.push({ key: `i:${id}`, kind: "item", id, label: i.name, icon: iconOfTarget(i.kind, i.target) });
  }
  mode.extra.forEach((e, n) => out.push({ key: `x:${n}`, kind: "extra", id: n, label: e.name, icon: iconOfTarget(e.kind, (e.targets || [])[0]) }));
  return out;
}

/** ["site" | "app", name] for an icon, from an item kind and its target ("youtube.com googlevideo.com" -> youtube.com). */
export function iconOfTarget(kind, target) {
  const first = String(target || "").split(/\s+/)[0];
  if (!first) return null;
  return kind === "app" ? ["app", first] : kind === "site" ? ["site", first] : null;
}

// ---------------------------------------------------------------- the editor

const win = (w) => `<div class="win-row" data-win role="group">${dayChips(Array.from({ length: 7 }, (_, i) => w.days.includes(i)))}${tpRange(w.start, w.end)}<span class="grow"></span>` +
  `<button class="icon-btn icon-btn-sm" type="button" data-act="win-x" aria-label="Remove this time window">${ic("trash")}</button></div>`;

const SKELETON = `<header class="card-h">${kic("k-mode", "pencil")}<h3 data-k="title">New mode</h3><span data-k="del"></span>
    <button class="btn" type="button" data-act="cancel">Cancel</button><button class="btn btn-primary" type="button" data-act="done">Done</button></header>
  <div class="card-b md-form stack">
    <p class="md-held" data-k="held" hidden>${ic("lock")} This mode is on and held (locked, or its automatic times are running): making it block less asks for the challenge.</p>
    <label class="stack-sm"><b>Name</b><input class="input" data-k="name" maxlength="40" autocomplete="off" spellcheck="false" placeholder="e.g. Gaming-free evening"></label>
    <div class="stack-sm"><div class="hstack"><b>Block these categories</b><button class="icon-btn icon-btn-sm" type="button" data-act="cat-help" aria-expanded="false" aria-label="About categories">${ic("info")}</button></div>
      <p class="small muted" data-k="cat-help" hidden>${esc(CAT_HELP)}</p><div class="hstack wrap" data-k="cats" style="gap:var(--sp-2) var(--sp-4)"></div></div>
    <div class="stack-sm"><b>Also block</b><div class="pills" data-k="chips"></div><p class="small muted" data-k="nothing">Nothing else.</p>
      <div class="hstack wrap"><select class="select select-sm" data-k="from-list" aria-label="From your blocklist"></select>
        <button class="btn btn-sm" type="button" data-act="other">+ Other site or app</button></div>
      <div class="md-pick" data-k="pick" hidden>
        <div data-k="picker"></div>
        <div class="pills" data-k="p-pills"></div>
        <label class="stack-sm" data-k="p-namewrap" hidden><b class="small">Display name (optional)</b>
          <input class="input" data-k="p-name" placeholder="Display name (optional)" maxlength="60" aria-label="Display name" autocomplete="off"></label>
        <div data-k="ways"></div>
        <p class="md-err" data-k="p-err" role="alert"></p>
        <div class="hstack"><button class="btn btn-sm btn-primary" type="button" data-act="pick-add">Add</button><button class="btn btn-sm" type="button" data-act="pick-cancel">Cancel</button></div></div></div>
    <div class="card flush"><div class="card-b">
      ${field("Pomodoro", "Blocked in focus rounds, free in breaks.", sw(false, 'data-k="pomo-on" aria-label="Pomodoro"'))}
      <div class="field sub md-sub" data-k="pomo" hidden><div class="grid-4" style="width:100%">${POMODORO_FIELDS.map(([k, label, unit]) =>
        `<label class="stack-sm small muted"><b style="color:var(--text)">${label}</b>${stepper(25, unit, { sm: 1, min: 1, max: 240, label: `${label}${unit ? " (" + unit + ")" : ""}` }).replace("<input ", `<input data-pomo="${k}" `)}</label>`).join("")}</div></div>
      ${field("Mute notifications", "Lockdown's own notifications wait while this mode is on - the same as Do Not Disturb.", sw(false, 'data-k="mute" aria-label="Mute notifications"'))}
      ${field("Turn on automatically", "At these times, every week.", sw(false, 'data-k="auto-on" aria-label="Turn on automatically"'))}
      <div class="field sub md-sub" data-k="auto" hidden><div data-k="wins"></div><div><button class="btn btn-sm" type="button" data-act="win-add">${ic("plus")}Add time window</button></div></div>
    </div></div>
    <p class="md-err" data-k="err" role="alert"></p>
  </div>`;

/** Build the editor into `host` (a hidden <section class="card">). hooks: data() -> the page's modes_get answer;
 *  changed(res, message) after a save / delete; closed() when the editor closes. */
export function createEditor(host, hooks) {
  host.innerHTML = SKELETON;
  const q = (k) => host.querySelector(`[data-k="${k}"]`);
  const del = confirmButton({ label: "Delete mode", confirmLabel: "Confirm delete", cls: "btn btn-danger", onConfirm: () => remove() });
  q("del").replaceWith(del);
  wireIconReady(ld.on, host);

  let cur = null;                  // {id, builtin, items, groups, extra}
  let isOpen = false, busy = false, seq = 0;
  let brief = null;                // blocklist_brief(), once it has arrived
  let picker = null, ways = null;  // the target picker and the "When blocked" boxes, built the first time "+ Other site or app" is pressed
  let lastSingle = "", building = null, classify = null;
  const touched = {};              // group -> the owner changed its ways (so a Steam game doesn't change them back)

  const err = (text) => { q("err").textContent = text || ""; };

  // ---------------- the chips and the blocklist menu
  function renderChips() {
    const chips = chipsOf(cur, brief || { groups: [], items: [] });
    const box = q("chips");
    box.replaceChildren(...chips.map((c) => el(`<span class="pill" data-key="${esc(c.key)}">${c.kind === "group" ? ic("layers") : c.icon ? `<span class="aic aic-sm">${iconImg(c.icon[0], c.icon[1])}</span>` : ""}<span>${esc(c.label)}</span>` +
      `<button class="icon-btn icon-btn-sm x" type="button" data-act="chip-x" aria-label="Remove ${esc(c.label)}">${ic("x")}</button></span>`)));
    q("nothing").hidden = chips.length > 0;
    fillList();
  }

  function fillList() {
    const sel = q("from-list");
    const option = (value, text, disabled, selected) => {
      const o = document.createElement("option");
      o.value = value;
      o.textContent = text;
      o.disabled = !!disabled;
      o.selected = !!selected;
      return o;
    };
    const kids = [option("", "+ From your blocklist…", false, true)];
    if (!brief) { sel.replaceChildren(...kids); return; }                 // (not here yet)
    if (!brief.groups.length && !brief.items.length) kids.push(option("", "(your blocklist is empty)", true));
    if (brief.groups.length) {
      const g = document.createElement("optgroup");
      g.label = "Groups";
      g.append(...brief.groups.map((x) => option(`g:${x.id}`, `Group: ${x.name}`, cur.groups.includes(x.id))));
      kids.push(g);
    }
    if (brief.items.length) {
      const g = document.createElement("optgroup");
      g.label = "Items";
      g.append(...brief.items.map((x) => option(`i:${x.id}`, x.name, cur.items.includes(x.id))));
      kids.push(g);
    }
    sel.replaceChildren(...kids);
  }

  async function loadBrief() {
    const my = ++seq;
    try {
      const b = await ld.call("blocklist_brief");
      if (my === seq && isOpen) { brief = b; renderChips(); }
    } catch (_) { /* the chips show what they can; saving still works */ }
  }

  // ---------------- the windows
  function addWindow(w) {
    q("wins").insertAdjacentHTML("beforeend", win(w));
  }
  const tpv = (node) => `${node.querySelector(".tp-h").value.trim().padStart(2, "0")}:${node.querySelector(".tp-m").value.trim().padStart(2, "0")}`;
  function windowsOf() {
    return [...q("wins").querySelectorAll("[data-win]")].map((r) => {
      const [a, b] = r.querySelectorAll(".tp");
      return { days: [...r.querySelectorAll(".days button")].flatMap((x, i) => (x.getAttribute("aria-pressed") === "true" ? [i] : [])), start: tpv(a), end: tpv(b) };
    });
  }

  // ---------------- open / close
  function open(mode) {
    const m = { ...NEW_MODE, ...(mode || {}) };
    const data = hooks.data();
    brief = null;
    cur = { id: m.id, builtin: !!m.builtin, items: [...m.items], groups: [...m.groups], extra: m.extra.map((e) => ({ ...e })) };
    setText(q("title"), mode ? `Edit ${m.name}` : "New mode");
    const name = q("name");
    name.value = m.name;
    name.disabled = cur.builtin;
    del.hidden = !(mode && !cur.builtin);
    q("held").hidden = !m.held;
    q("cats").replaceChildren(...data.categories.map((c) => el(`<label class="check md-cat" style="--cat:${esc(colorOf(c))}"><input type="checkbox" data-cat="${esc(c.key)}" ${m.categories.includes(c.key) ? "checked" : ""}><span class="ellip">${esc(c.name)}</span></label>`)));
    const p = m.pomodoro || data.pomodoro_defaults;
    for (const input of host.querySelectorAll("[data-pomo]")) input.value = String(p[input.dataset.pomo]);
    q("pomo-on").checked = !!m.pomodoro;
    q("pomo").hidden = !m.pomodoro;
    q("mute").checked = !!m.mute;
    q("auto-on").checked = !!m.windows;
    q("auto").hidden = !m.windows;
    q("wins").replaceChildren();
    for (const w of m.windows || [data.default_window]) addWindow(w);
    closePick();
    err("");
    isOpen = true;
    host.hidden = false;
    renderChips();
    loadBrief();
    if (!mode) name.focus();
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    host.hidden = true;
    seq++;
    hooks.closed();
  }

  // ---------------- "+ Other site or app"
  const perr = (text) => { q("p-err").textContent = text || ""; };

  /** Build the picker and the ways boxes (once). */
  function buildPicker() {
    if (!building) {
      building = import("../../targetpicker.js").then((mod) => {
        classify = mod.classify;
        picker = mod.createPicker({ compact: true, onChange: syncPick, onEnter: addPicked, notify: perr });
        picker.input.dataset.k = "p-text";
        q("picker").append(picker.el);
        ways = createWays({ grey: false, onChange: ({ group }) => { touched[group] = true; perr(""); } });   // (a mode that blocks an app closes it: no Grey it here)
        q("ways").append(ways.el);
        ways.show([]);
        picker.input.addEventListener("input", syncPick);
      });
    }
    return building;
  }

  const pillLabel = (t) => (t.kind === "category" ? `category · ${t.name}` : t.name || t.target);

  /** The selection or the typed text changed: its pills, the display name (one target only) and the "When blocked" boxes of
   *  the kinds in it - the typed text counts at once (reddit.com shows the site ways, discord.exe the app ways). */
  function syncPick() {
    const sel = picker.selection();
    const typed = classify(picker.input.value);
    const hasTyped = !typed.error;
    q("p-pills").replaceChildren(...sel.map((t) => el(`<span class="pill" data-key="${esc(t.key)}"><span>${esc(pillLabel(t))}</span>` +
      `<button class="icon-btn icon-btn-sm x" type="button" data-act="p-x" aria-label="Remove ${esc(pillLabel(t))}">${ic("x")}</button></span>`)));
    const single = sel.length === 1 && !hasTyped ? sel[0].key : "";
    if (single !== lastSingle) {
      lastSingle = single;
      if (single || !sel.length) q("p-name").value = single ? sel[0].name || "" : "";
    }
    q("p-namewrap").hidden = sel.length + (hasTyped ? 1 : 0) !== 1;
    const present = new Set(sel.map(groupOf));
    if (hasTyped) present.add(groupOf(typed));
    ways.show(GROUPS.filter((g) => present.has(g)));
    const steam = sel.some((t) => t.kind === "app" && !t.file && t.steam);
    if (!touched.app) ways.set("app", defaultWays("app", steam));
    perr("");
  }

  async function openPick() {
    q("pick").hidden = false;
    await buildPicker();
    if (isOpen) picker.focus();
  }

  function closePick() {
    q("pick").hidden = true;
    q("p-name").value = "";
    lastSingle = "";
    perr("");
    for (const g of GROUPS) touched[g] = false;
    if (picker) {
      picker.reset(false);
      for (const g of GROUPS) ways.set(g, defaultWays(g));
      ways.show([]);
    }
  }

  async function addPicked() {
    perr("");
    if (!picker || !picker.commit()) return;            // (typed text that is no address: the picker said why)
    const sel = picker.selection();
    if (!sel.length) return perr("Pick a site, an app or a category first.");
    for (const t of sel) {
      try {
        const extra = await ld.call("mode_extra_check", {
          kind: t.kind, text: t.target, name: sel.length === 1 ? q("p-name").value : t.name || "", ways: ways.values(groupOf(t)),
          app_path: t.kind === "app" ? t.path || null : null,
        });
        if (!cur.extra.some((e) => e.kind === extra.kind && e.targets.join(" ") === extra.targets.join(" "))) cur.extra.push(extra);
        picker.remove(t.key);
      } catch (e) {
        picker.remove(t.key);                          // (what was refused goes back into the box to be corrected, as it did before)
        if (!picker.input.value.trim()) picker.input.value = t.path || t.target;
        syncPick();
        perr(e.message || "That didn't work.");
        renderChips();
        return;
      }
    }
    closePick();
    renderChips();
  }

  // ---------------- save / delete
  function collect() {
    const data = {
      id: cur.id, name: q("name").value, categories: [...host.querySelectorAll("[data-cat]")].filter((c) => c.checked).map((c) => c.dataset.cat),
      items: cur.items, groups: cur.groups, extra: cur.extra, mute: q("mute").checked, pomodoro: null, windows: null,
    };
    if (q("pomo-on").checked) data.pomodoro = Object.fromEntries([...host.querySelectorAll("[data-pomo]")].map((i) => [i.dataset.pomo, i.value]));
    if (q("auto-on").checked) data.windows = windowsOf();
    return data;
  }

  async function run(call, message) {
    if (busy) return;
    busy = true;
    err("");
    const buttons = host.querySelectorAll('.card-h .btn');
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const res = await guarded(call());
      if (res === CANCELLED) return;                       // the challenge was cancelled: the editor stays with what was typed
      close();
      hooks.changed(res, message);
    } catch (e) {
      err(e.message || "That didn't work.");
    } finally {
      busy = false;
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  const save = () => run(() => ld.call("mode_save", collect()), "Mode saved");
  const remove = () => run(() => ld.call("mode_delete", cur.id), "Mode deleted");

  // ---------------- events
  host.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "done") save();
    else if (act === "cancel") close();
    else if (act === "cat-help") {
      const open_ = q("cat-help").hidden;
      q("cat-help").hidden = !open_;
      b.setAttribute("aria-expanded", String(open_));
    } else if (act === "other") (q("pick").hidden ? openPick : closePick)();
    else if (act === "pick-cancel") closePick();
    else if (act === "pick-add") addPicked();
    else if (act === "p-x") picker.remove(b.closest("[data-key]").dataset.key);
    else if (act === "win-add") addWindow({ days: [0, 1, 2, 3, 4], start: "09:00", end: "17:00" });
    else if (act === "win-x") b.closest("[data-win]").remove();
    else if (act === "chip-x") {
      const key = b.closest("[data-key]").dataset.key;
      const [kind, id] = key.split(":");
      if (kind === "g") cur.groups = cur.groups.filter((x) => x !== +id);
      else if (kind === "i") cur.items = cur.items.filter((x) => x !== +id);
      else cur.extra.splice(+id, 1);
      renderChips();
    }
  });
  host.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.k === "pomo-on") q("pomo").hidden = !t.checked;
    else if (t.dataset.k === "auto-on") q("auto").hidden = !t.checked;
    else if (t.dataset.k === "from-list") {
      const [kind, id] = String(t.value).split(":");
      if (kind === "g" && !cur.groups.includes(+id)) cur.groups.push(+id);
      else if (kind === "i" && !cur.items.includes(+id)) cur.items.push(+id);
      renderChips();
    }
  });
  host.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.dataset.k === "p-name") { e.preventDefault(); addPicked(); }
  });

  return { open, close, isOpen: () => isOpen, openId: () => (isOpen && cur && cur.id) || null };
}
