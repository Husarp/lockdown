// Blocking > Groups: named sets of rules shared by their members. A list of group cards on the left, the editor (or "No group
// open") on the right: Name, the blockers (the shared rail), "How members are blocked" per kind, and the Members.
//
// Members: only the ticked ones are shown until you type; typing finds things already on your blocklist, installed apps and
// a website address (a virtual list, real icons); ticking adds, un-ticking takes out. Each ticked member has "Group's way /
// Its own way" (customize_member.js) and "Extra limits" (its own rules on top of the group's). "+ Add member" browses apps,
// types a website or picks a whole category. A new member that is not on the blocklist yet is created when the group is saved.
//
// Done sends the rail's FORMS, the members and the ways to Python (`draft_set_group`) through the draft: Python reads the forms
// again, checks everything and decides what loosens (a member taken out, a raised shared limit, a lighter way ...) - then the
// challenge opens by itself and the editor stays until it passes. Nothing here compares old with new.
import { openAppBrowser } from "../../appbrowser.js";
import { iconKey, iconUrl, wireIconReady } from "../../appicon.js";
import { ld } from "../../bridge.js";
import { CANCELLED, parseNaive } from "../../challenge.js";
import { openCustomizeMember, openMemberWay } from "../../customize_member.js";
import { createWays, wayText } from "../../ways.js";
import { draftCall, FAILED } from "../../draft.js";
import { openMenu } from "../../menu.js";
import { createRail } from "../../rules/rail.js";
import * as S from "../../rules/summary.js";
import { confirmButton, el, empty, ic, setText, toast } from "../../ui.js";
import { createVList } from "../../vlist.js";
import { chipsHtml } from "./overview.js";

export const freshMs = 10_000;
export const REFRESH_TICKS = 30;          // a reload every 30 s while shown (the statuses move with the clock)
export const SEARCH_MS = 120;
const VISIBLE_ROWS = 8;                    // the Members list shows this many rows, then scrolls

// ---------------------------------------------------------------- plain helpers (tested in node)

/** "3 members · a, b, c" / "no members" (the grey line of a group card). */
export function membersLine(card) {
  if (!card.count) return "no members";
  const more = card.count - card.names.length;
  return `${card.count} member${card.count === 1 ? "" : "s"} · ${card.names.join(", ")}${more > 0 ? ` and ${more} more` : ""}`;
}

/** What the "way" button of a member says. */
export function wayLabel(member) {
  if (!member.way) return "Group's way";
  const text = wayText(member.kind, member.way);
  return `Its own way${text ? ": " + text : ""}`;
}

/** What the "Extra limits" button says: "Extra limits" / "Extra limits · 2". */
export function extrasLabel(member) {
  const n = Object.keys(member.overrides || {}).length;
  return n ? `Extra limits · ${n}` : "Extra limits";
}

const HOSTED = /\.(jar|class|py|pyw|bat|cmd|ps1|vbs|vbe|js|jse|wsf)$/i;
/** Is this exe name a program file (a Java game, a script)? Display only - Python checks again. */
export const isHosted = (exe) => HOSTED.test(String(exe || ""));

/** The members of the editor -> what `draft_set_group` takes: {members: [{id, overrides, way}], pending: [{kind, name,
 *  display, path, overrides, way}]}. Extra rules go as FORMS (Python reads them again). */
export function donePayload(members) {
  const out = [], pending = [];
  for (const m of members.values()) {
    const overrides = {};
    for (const [t, rule] of Object.entries(m.overrides || {})) overrides[t] = S.formFromRule(rule, false, t);
    const way = m.way || null;
    if (m.id != null) out.push({ id: m.id, overrides, way });
    else pending.push({ kind: m.target.kind, name: m.target.name, display: m.target.display, path: m.target.path || null, overrides, way });
  }
  return { members: out, pending };
}

/** The member a search row (a blocklist item) becomes. */
export function memberOfItem(item) {
  return { key: `i:${item.id}`, id: item.id, name: item.name, kind: item.kind, type: item.type, icon: item.icon, sub: item.sub,
    targets: item.targets || [], hosted: !!item.hosted, itemWay: item.item_way || null, way: null, overrides: {}, target: null };
}

/** The member for something new: {kind: "app"|"site"|"category", name (exe / host / category key), display, path}. A category
 *  is blocked like an app (`kind` "app"), its `type` says what it is. */
export function memberOfTarget(t) {
  const site = t.kind === "site", category = t.kind === "category";
  return { key: `n:${t.kind}:${t.name}`, id: null, name: t.display || t.name, kind: category ? "app" : t.kind, type: t.kind, icon: { k: t.kind, n: t.name },
    sub: category ? "category · everything in it" : `${t.kind} · ${t.name}`, targets: [t.name], hosted: !site && !category && isHosted(t.name), itemWay: null, way: null, overrides: {},
    target: { kind: t.kind, name: t.name, display: t.display || t.name, path: t.path || null } };
}

/** The member already in the group that stands for one of these names (exe / host), or null: a second one is refused. */
export function duplicateOf(members, names) {
  const wanted = (Array.isArray(names) ? names : [names]).map((n) => String(n).toLowerCase());
  for (const m of members.values()) if (m.targets.some((t) => wanted.includes(t))) return m;
  return null;
}

/** The rows of the Members list. No search text: the ticked members, by name. With text: what the search found. */
export function memberRows(members, query, found) {
  if (!query) {
    return [...members.values()].sort((a, b) => a.name.localeCompare(b.name)).map((m) => ({ key: `m:${m.key}`, k: "member", m }));
  }
  if (!found) return [];
  const rows = found.items.map((item) => ({ key: `c:i:${item.id}`, k: "item", item }));
  if (found.site) rows.push({ key: `c:n:site:${found.site.host}`, k: "site", site: found.site });
  for (const app of found.apps) rows.push({ key: `c:n:app:${app.exe}`, k: "app", app });
  return rows;
}

const STATUS_CLASS = { unsaved: "chip-low", disabled: "chip-paused", allowed: "chip-ok", pending: "chip-low", blocked: "chip-block", empty: "chip-paused" };

// ---------------------------------------------------------------- state

let tab = null, root = null, vl = null;
let cards = [];                      // groups_list
let loaded = false, wantEdit = null, ticks = 0, reloadQueued = false;
let clock = { base: 0, at: 0 };
let ed = null;                       // the open editor: {id, isNew, disabled, name, members: Map, busy}
let rail = null, ways = null;
let query = "", found = null, searchTimer = 0, seq = 0;
const nodes = new Map();             // group id -> its card
const els = {};

const trustedNow = () => clock.base + (performance.now() - clock.at);
const q = (k) => root.querySelector(`[data-k="g-${k}"]`);

const STYLE = `<style>
.gp { display: grid; grid-template-columns: minmax(calc(250px * var(--s)), 1fr) minmax(0, 3fr); gap: var(--sp-4); align-items: start; padding-bottom: var(--sp-4); }
.gp-intro { grid-column: 1 / -1; margin: 0; }
@media (max-width: 900px) { .gp { grid-template-columns: minmax(0, 1fr); } }
.gp-list { display: grid; gap: var(--sp-3); align-content: start; min-width: 0; }
.gp-card { gap: var(--sp-2); position: relative; }
.gp-card[aria-current="true"] { border-color: var(--brand); box-shadow: inset 3px 0 0 var(--brand); }
.gp-card h3 { font-size: var(--fs-md, 1em); }
.gp-card .top .chip { max-width: 55%; flex: none; }
.gp-card .top .chip span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gp-chips { display: flex; flex-wrap: wrap; gap: 3px var(--sp-1); min-width: 0; }
.gp-chips:empty { display: none; }
.bchip { display: inline-flex; align-items: center; gap: 4px; height: calc(20px * var(--s)); max-width: 100%; padding: 0 var(--sp-2); border-radius: var(--r-pill);
  background: var(--k-soft); color: var(--text); font-size: var(--fs-sm); line-height: 1; white-space: nowrap; }
.bchip .ic { color: var(--k); width: calc(12px * var(--s)); height: calc(12px * var(--s)); }
.bchip span { overflow: hidden; text-overflow: ellipsis; }
.gp-new { border-style: dashed; background: transparent; color: var(--text-2); justify-items: center; place-content: center; min-height: calc(64px * var(--s)); text-align: center; }
.gp-none { padding: var(--sp-3) 0; }
.gp-right { min-width: 0; display: grid; gap: var(--sp-4); align-content: start; }
.gp-blank { text-align: center; padding: calc(44px * var(--s)) var(--sp-4); display: grid; gap: var(--sp-2); justify-items: center; }
.gp-blank h3 { margin: 0; }
.gp-blank p { margin: 0; color: var(--text-2); }
.ge { display: grid; gap: var(--sp-4); min-width: 0; }
.ge[hidden], .gp-blank[hidden] { display: none; }
.ge-top .card-h { flex-wrap: wrap; row-gap: var(--sp-2); }
.ge-top .card-h h2 { margin: 0; flex: 1; min-width: 0; }
.ge-name { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.ge-name .input { flex: 1; min-width: calc(180px * var(--s)); max-width: calc(420px * var(--s)); }
.ge-wcols { display: grid; grid-template-columns: repeat(auto-fit, minmax(calc(220px * var(--s)), 1fr)); gap: var(--sp-4); }
.ge-wcols h4 { margin: 0 0 var(--sp-1); font-size: var(--fs-sm); font-weight: var(--fw-semibold); }
.ge-wnote { margin: var(--sp-2) 0 0; }
.ge-mlist { border-top: 1px solid var(--line); margin-top: var(--sp-3); position: relative; }
.ge-mlist .vlist { border-top: 0; }
.ge-mrow { padding: 0 var(--sp-3); }
.ge-ic .ge-tile { display: none; width: 62%; height: 62%; stroke-width: 2; }
.ge-ic.tile { --c: var(--brand); --fg: var(--on-brand); }
.ge-ic.tile img { display: none; }
.ge-ic.tile .ge-tile { display: block; }
.ge-end { display: flex; align-items: center; justify-content: flex-end; gap: var(--sp-2); min-width: 0; }
.ge-sub { max-width: calc(260px * var(--s)); }
.ge-none { padding: var(--sp-4); }
.ge-err { margin: 0; }
.ge-nameerr { flex-basis: 100%; margin: 0; }
</style>`;

// ---------------------------------------------------------------- the list

function createCard() {
  const node = el(`<div class="card card-click group-card gp-card" role="button" tabindex="0">
    <div class="top"><span class="gtile gtile-lg" style="--g: var(--brand)">${ic("layers")}</span><div class="grow"><h3 class="ellip"></h3><span class="small muted ellip"></span></div>
    <span class="chip"><span></span></span></div>
    <div class="gp-chips"></div><div class="small muted gp-ways"></div></div>`);
  node._r = { name: node.querySelector("h3"), line: node.querySelector(".grow .small"), chip: node.querySelector(".chip"), chipText: node.querySelector(".chip span"),
    chips: node.querySelector(".gp-chips"), ways: node.querySelector(".gp-ways") };
  return node;
}

function paintCard(node, g) {
  const r = node._r;
  node.dataset.id = g.id;
  setText(r.name, g.name);
  setText(r.line, membersLine(g));
  const className = `chip ${STATUS_CLASS[g.status.k] || "chip-paused"}`;
  if (r.chip.className !== className) r.chip.className = className;
  setText(r.chipText, g.status.text);
  r.chip.title = g.status.title || g.status.text;
  const sig = JSON.stringify(g.chips);
  if (node._sig !== sig) { node._sig = sig; r.chips.innerHTML = chipsHtml(g.chips); }
  setText(r.ways, g.ways);
  node.setAttribute("aria-current", String(!!ed && ed.id === g.id));
  node.setAttribute("aria-label", `${g.name}, ${g.status.text}`);
}

function renderList() {
  const present = new Set(cards.map((g) => g.id));
  for (const [id, node] of nodes) if (!present.has(id)) { node.remove(); nodes.delete(id); }
  const order = [];
  for (const g of cards) {
    let node = nodes.get(g.id);
    if (!node) { node = createCard(); nodes.set(g.id, node); }
    paintCard(node, g);
    order.push(node);
  }
  const current = [...els.cards.children];
  if (order.length !== current.length || order.some((n, i) => n !== current[i])) els.cards.replaceChildren(...order);
  els.none.hidden = cards.length > 0;
}

function markSelected() {
  for (const [id, node] of nodes) node.setAttribute("aria-current", String(!!ed && ed.id === id));
}

export async function load() {
  const data = await ld.call("groups_list");
  clock = { base: parseNaive(data.now), at: performance.now() };
  cards = data.groups;
  loaded = true;
  renderList();
  openWanted();
}

function openWanted() {
  if (wantEdit == null) return;
  const id = wantEdit;
  wantEdit = null;
  openEditor(id === "new" ? null : id);
}

function reloadSoon() {
  if (reloadQueued) return;
  reloadQueued = true;
  Promise.resolve().then(() => { reloadQueued = false; tab.reload(); });
}

function tick() {
  if (++ticks % REFRESH_TICKS === 0) tab.reload();
}

// ---------------------------------------------------------------- the editor

const nameValue = () => els.name.value.replace(/\s+/g, " ").trim();

function showEditor(on) {
  els.editor.hidden = !on;
  els.blank.hidden = on;
}

async function openEditor(id) {
  let g;
  try {
    g = await ld.call("group_get", id);
  } catch (err) {
    toast((err && err.message) || "That group isn't there any more.");
    tab.reload();
    return;
  }
  clock = { base: parseNaive(g.now), at: performance.now() };
  ed = { id: g.id, isNew: !!g.new, disabled: !!g.disabled, members: new Map(), busy: false };
  setText(els.title, g.new ? "New group" : "Edit group");
  els.name.value = g.name;
  clearErrors();
  els.remove.hidden = els.disable.hidden = g.new;
  setText(els.disable, g.disabled ? "Enable" : "Disable");
  rail.load(g.rules);
  ways.set("app", g.ways.app);
  ways.set("site", g.ways.site);
  noteWays();
  for (const m of g.members) {
    ed.members.set(`i:${m.id}`, { key: `i:${m.id}`, id: m.id, name: m.name, kind: m.kind, type: m.type, icon: m.icon, sub: m.sub,
      targets: m.targets || [], hosted: !!m.hosted, itemWay: m.item_way || null, way: m.way || null, overrides: m.overrides || {}, target: null });
  }
  query = "";
  found = null;
  els.search.value = "";
  seq++;
  showEditor(true);
  showMembers();
  markSelected();
  if (g.new) els.name.focus();
  els.editor.scrollIntoView({ block: "nearest" });
}

function closeEditor() {
  ed = null;
  showEditor(false);
  markSelected();
}

function clearErrors() {
  for (const k of ["err", "nameerr"]) { els[k].hidden = true; els[k].textContent = ""; }
  rail.showErrors([]);
}

function noteWays() {
  const unset = [];
  if (!ways.value("app")) unset.push("apps");
  if (!ways.value("site")) unset.push("sites");
  setText(els.wnote, unset.length ? `Nothing ticked for ${unset.join(" and ")}: each is blocked the way it was set when it was added.` : "");
}

function showErrors(errors) {
  clearErrors();
  const forRail = errors.filter((e) => e.field !== "name" && !e.member && e.field !== "members");
  const name = errors.find((e) => e.field === "name");
  const others = errors.filter((e) => e.member || e.field === "members");
  if (name) { els.nameerr.textContent = name.message; els.nameerr.hidden = false; els.name.focus(); }
  if (forRail.length) rail.showErrors(forRail);
  if (others.length) { els.err.textContent = others.map((e) => (e.member && e.field !== "members" ? `${e.member}: ${e.message}` : e.message)).join(" "); els.err.hidden = false; }
}

/** Done: check what can be checked here (instantly), then Python reads it all again. */
async function done() {
  if (!ed || ed.busy) return;
  clearErrors();
  const name = nameValue();
  const local = [];
  if (!name) local.push({ rule_type: null, field: "name", message: "Give the group a name." });
  local.push(...rail.check().errors);
  if (local.length) { showErrors(local); return; }
  ed.busy = true;
  els.done.disabled = true;
  const { id, isNew } = ed;
  try {
    const { members, pending } = donePayload(ed.members);
    const r = await draftCall("draft_set_group", id, name, rail.forms(), members, { app_block: ways.value("app"), site_block: ways.value("site") }, pending);
    if (r === FAILED || r === CANCELLED) return;                  // a toast said why / the challenge was cancelled: the editor stays
    if (r && r.result && r.result.saved === false) { showErrors(r.result.errors || []); return; }
    closeEditor();
    tab.confirm(`Group ${name} ${isNew ? "added" : "saved"}`);
  } finally {
    if (ed) { ed.busy = false; els.done.disabled = false; } else els.done.disabled = false;
    tab.reload();
  }
}

async function removeGroup() {
  if (!ed || ed.isNew) return;
  const { id } = ed, name = nameValue() || "the group";
  const r = await draftCall("draft_remove_group", id);
  if (r !== FAILED && r !== CANCELLED) { closeEditor(); tab.confirm(`Group ${name} removed`); }
  tab.reload();
}

async function toggleDisabled() {
  if (!ed || ed.isNew) return;
  const { id, disabled } = ed, name = nameValue() || "the group";
  const r = await draftCall("draft_set_group_disabled", id, !disabled);
  if (r !== FAILED && r !== CANCELLED) { closeEditor(); tab.confirm(`Group ${name} ${disabled ? "enabled" : "disabled"}`); }
  tab.reload();
}

// ---------------------------------------------------------------- the Members box

function createMemberRow() {
  const node = el(`<div class="row row-sm member-row ge-mrow" role="listitem">
    <label class="check"><input type="checkbox" data-act="tick"></label>
    <span class="aic ge-ic"><img alt="" decoding="async" draggable="false">${ic("folder", "ge-tile")}</span>
    <span class="name"><b class="ellip"></b><span class="ellip"></span></span>
    <span class="ge-end"><span class="hstack ge-acts"><button class="btn btn-sm" type="button" data-act="way" aria-haspopup="menu"></button><button class="btn btn-sm" type="button" data-act="extras"></button></span><span class="small faint ellip ge-sub"></span></span></div>`);
  node._r = { box: node.querySelector("input"), ic: node.querySelector(".ge-ic"), img: node.querySelector("img"), b: node.querySelector(".name b"),
    sub: node.querySelector(".name span"), acts: node.querySelector(".ge-acts"), way: node.querySelector('[data-act="way"]'),
    extras: node.querySelector('[data-act="extras"]'), end: node.querySelector(".ge-sub") };
  return node;
}

function setIcon(r, icon) {
  if (!icon || icon.k === "category") { r.ic.classList.add("tile"); return; }
  r.ic.classList.remove("tile");
  const key = iconKey(icon.k, icon.n);
  if (r.img.dataset.icon !== key) { r.img.dataset.icon = key; r.img.src = iconUrl(icon.k, icon.n); }
}

function renderMemberRow(node, row) {
  const r = node._r;
  const members = ed ? ed.members : new Map();
  node._row = row;
  node.dataset.t = row.k;
  let name, sub, icon, on;
  if (row.k === "member") { ({ name, sub, icon } = row.m); on = true; node.dataset.key = row.m.key; }
  else if (row.k === "item") { ({ name, sub, icon } = row.item); node.dataset.key = `i:${row.item.id}`; on = members.has(node.dataset.key); }
  else if (row.k === "site") { name = row.site.host; sub = "Add this website"; icon = { k: "site", n: row.site.host }; node.dataset.key = `n:site:${row.site.host}`; on = members.has(node.dataset.key); }
  else { name = row.app.name; sub = row.app.exe + (row.app.steam ? " · Steam" : ""); icon = { k: "app", n: row.app.exe }; node.dataset.key = `n:app:${row.app.exe}`; on = members.has(node.dataset.key); }
  r.box.checked = on;
  r.box.setAttribute("aria-label", `Member ${name}`);
  setText(r.b, name);
  setText(r.sub, sub);
  setIcon(r, icon);
  const controls = row.k === "member" && !query;
  r.acts.hidden = !controls;
  r.end.hidden = controls;
  if (controls) {
    const m = row.m;
    setText(r.way, wayLabel(m));
    r.way.setAttribute("aria-label", `How ${m.name} is blocked: ${wayLabel(m)}`);
    setText(r.extras, extrasLabel(m));
    r.extras.classList.toggle("btn-primary", Object.keys(m.overrides || {}).length > 0);
    r.extras.setAttribute("aria-label", `Extra limits for ${m.name}`);
  } else {
    setText(r.end, row.k === "member" ? row.m.sub : "");
  }
}

/** Paint the Members box. `keep`: the same list changed a little (a tick, a way) - stay where the scrolling is. */
function showMembers(keep = false) {
  const rows = memberRows(ed.members, query, found);
  const n = ed.members.size;
  setText(els.mcount, `${n} member${n === 1 ? "" : "s"}`);
  const none = !rows.length;
  els.mlist.hidden = none;
  els.mnone.hidden = !none;
  if (none) {
    els.mnone.textContent = query ? (found && found.loading ? "Loading apps…" : "No apps or sites match.") : "No members yet - type to find apps and sites to add.";
  } else {
    els.mlist.style.height = `${Math.min(rows.length, VISIBLE_ROWS) * vl.rowHeight}px`;       // (the list needs a height to place its rows in)
  }
  els.mhint.textContent = query ? "Showing everything that matches - tick to add." : "Only ticked members are shown. Type to find more.";
  vl.setRows(rows, { keepScroll: keep });
}

async function runSearch() {
  searchTimer = 0;
  const text = els.search.value.trim();
  const my = ++seq;
  if (!text) { query = ""; found = null; if (ed) showMembers(); return; }
  try {
    const r = await ld.call("group_member_search", text);
    if (my !== seq || !ed) return;
    found = r;
  } catch (err) {
    if (my !== seq) return;
    found = { items: [], apps: [], site: null, loading: false };
    toast((err && err.message) || "The search didn't work.");
  }
  query = text;
  showMembers();
}

function onSearch() {
  clearTimeout(searchTimer);
  if (!els.search.value.trim()) { runSearch(); return; }           // back to the ticked members at once
  searchTimer = setTimeout(runSearch, SEARCH_MS);
}

function addMember(m) {
  const dup = duplicateOf(ed.members, m.targets);
  if (dup || ed.members.has(m.key)) {
    toast(`${m.name} is already in this group.`);
    return false;
  }
  ed.members.set(m.key, m);
  return true;
}

function addTarget(t) {
  if (!ed) return;
  addMember(memberOfTarget(t));
  showMembers(true);
}

function onTick(node, box) {
  const row = node._row;
  if (!row || !ed) return;
  if (row.k === "member") ed.members.delete(row.m.key);
  else if (!box.checked) {
    const key = node.dataset.key;
    ed.members.delete(key);
  } else if (row.k === "item") {
    if (!addMember(memberOfItem(row.item))) box.checked = false;
  } else if (row.k === "site") {
    if (!addMember(memberOfTarget({ kind: "site", name: row.site.host, display: row.site.host }))) box.checked = false;
  } else if (!addMember(memberOfTarget({ kind: "app", name: row.app.exe, display: row.app.name, path: row.app.path }))) box.checked = false;
  showMembers(true);
}

function groupWayFor(member) {
  return ways.value(member.kind === "site" ? "site" : "app");
}

function chooseWay(btn, member) {
  openMenu(btn, [
    { label: "Group's way", checked: !member.way, onClick: () => { member.way = null; showMembers(true); } },
    { label: "Its own way…", checked: !!member.way, onClick: () => openMemberWay({
      name: member.name, kind: member.kind, hosted: member.hosted, groupWay: groupWayFor(member), itemWay: member.itemWay, way: member.way,
      onDone: (way) => { member.way = way; showMembers(true); } }) },
  ]);
}

function customize(member) {
  const check = rail.check();
  const bad = check.errors.filter((e) => e.rule_type);
  if (bad.length) {                                      // the group's own rules must be fine first
    rail.showErrors(check.errors);
    els.err.textContent = "Fix the group's blockers first, then set a member's extra limits.";
    els.err.hidden = false;
    return;
  }
  els.err.hidden = true;
  openCustomizeMember({
    name: member.name, overrides: member.overrides || {}, groupRules: check.rules, now: trustedNow,
    onDone: (overrides) => { member.overrides = overrides; showMembers(true); },
  });
}

function onMembersClick(e) {
  const btn = e.target.closest("[data-act]");
  const node = e.target.closest(".vl-row");
  if (!btn || !node || !node._row || node._row.k !== "member") return;
  if (btn.dataset.act === "way") chooseWay(btn, node._row.m);
  else if (btn.dataset.act === "extras") customize(node._row.m);
}

function addMenu(btn) {
  openMenu(btn, [
    { label: "App or program file…", icon: "app", onClick: () => openAppBrowser({ mode: "pick", onPick: (app) => addTarget({ kind: "app", name: app.exe, display: app.name, path: app.path }) }) },
    { label: "Website…", icon: "globe", onClick: () => { els.search.focus(); els.mhint.textContent = "Type the website address, e.g. reddit.com, then tick it."; } },
    { label: "Category…", icon: "folder", onClick: () => addCategory(btn) },
  ]);
}

/** "Category…": the same chooser as the Add tab's picker (loaded when it is needed); the one picked becomes a new member. */
function addCategory(btn) {
  import("../../targetpicker.js")
    .then((m) => m.chooseCategory(btn, (c) => addTarget({ kind: "category", name: c.key, display: c.name }), toast))
    .catch(() => toast("The categories aren't available."));
}

// ---------------------------------------------------------------- the tab contract

export function mount(host, t) {
  tab = t;
  root = host;
  host.innerHTML = `${STYLE}<div class="gp">
    <p class="small muted gp-intro">A group shares its rules with all members, e.g. "Night schedule" blocks its apps and sites at night; "Games" gives all games 2 h a day together.</p>
    <div class="gp-list">
      <div class="gp-cards" data-k="g-cards" style="display:grid;gap:var(--sp-3)"></div>
      <div class="gp-none" data-k="g-none" hidden>${empty("k-sched", "layers", "No groups yet.", "A group shares one set of rules - for example one daily limit for all your games.")}</div>
      <button class="card card-click group-card gp-card gp-new" type="button" data-k="g-new">${ic("plus", "ic-lg")}<b>New group</b></button>
    </div>
    <div class="gp-right">
      <div class="card gp-blank" data-k="g-blank">${ic("layers", "ic-lg")}<h3>No group open</h3><p>Pick a group on the left to edit its rules and members, or make a new one.</p><button class="btn" type="button" data-k="g-new2">${ic("plus")}New group</button></div>
      <section class="ge" data-k="g-editor" hidden aria-label="Group editor">
        <div class="card ge-top"><header class="card-h"><h2 data-k="g-title">New group</h2><span class="grow"></span>
          <span data-k="g-removeslot"></span>
          <button class="btn" type="button" data-k="g-disable">Disable</button>
          <button class="btn" type="button" data-k="g-cancel">Cancel</button>
          <button class="btn btn-primary" type="button" data-k="g-done">Done</button></header>
          <div class="card-b ge-name"><label class="rule-lbl" for="ge-name">Name</label><input class="input" id="ge-name" data-k="g-name" placeholder="e.g. Night schedule, Games" autocomplete="off" maxlength="100"><p class="rule-err ge-nameerr" data-k="g-nameerr" role="alert" hidden></p></div></div>
        <div data-k="g-rail"></div>
        <section class="card"><header class="card-h"><h3>How members are blocked</h3></header><div class="card-b">
          <div class="ge-wcols"><div><h4>Apps</h4><div data-k="g-wapp"></div></div><div><h4>Sites</h4><div data-k="g-wsite"></div></div></div>
          <p class="small muted ge-wnote" data-k="g-wnote"></p></div></section>
        <section class="card"><header class="card-h"><h3>Members</h3><span class="small muted" data-k="g-mcount"></span>
          <button class="btn btn-sm" type="button" data-k="g-add" aria-haspopup="menu">${ic("plus")}Add member</button></header>
          <div class="card-b" style="padding-bottom:var(--sp-2)"><div class="search">${ic("search")}<input class="input" data-k="g-search" placeholder="Type to find apps and sites to add" autocomplete="off" spellcheck="false" aria-label="Find members"></div>
            <p class="small faint" data-k="g-mhint" style="margin:var(--sp-2) 0 0"></p></div>
          <div class="ge-mlist"><div data-k="g-mlist" role="list" aria-label="Members"></div><p class="muted small ge-none" data-k="g-mnone" hidden></p></div></section>
        <p class="rule-err ge-err" data-k="g-err" role="alert" hidden></p>
      </section>
    </div></div>`;
  for (const k of ["cards", "none", "new", "new2", "blank", "editor", "title", "disable", "cancel", "done", "name", "nameerr", "wnote", "mcount", "search", "mhint",
    "mlist", "mnone", "err", "add"]) els[k] = q(k);
  els.remove = confirmButton({ label: "Remove group", confirmLabel: "Confirm remove", cls: "btn", ms: 4000, onConfirm: removeGroup });
  q("removeslot").replaceWith(els.remove);
  rail = createRail({ shared: true, types: S.RULE_TYPES, name: () => nameValue(), now: trustedNow, requireOne: true, compact: true });
  q("rail").replaceWith(rail.el);
  ways = createWays({ groups: ["app", "site"], compact: true, onChange: noteWays });
  q("wapp").append(ways.section("app"));
  q("wsite").append(ways.section("site"));
  vl = createVList(els.mlist, { rowVar: "--row-h-sm", rows: [], key: (row) => row.key, create: createMemberRow, render: renderMemberRow });
  els.mlist.setAttribute("role", "list");

  const newGroup = () => openEditor(null);
  els.new.addEventListener("click", newGroup);
  els.new2.addEventListener("click", newGroup);
  els.cards.addEventListener("click", (e) => { const n = e.target.closest(".gp-card"); if (n) openEditor(Number(n.dataset.id)); });
  els.cards.addEventListener("keydown", (e) => {
    const n = e.target.closest && e.target.closest(".gp-card");
    if (n && e.target === n && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openEditor(Number(n.dataset.id)); }
  });
  els.cancel.addEventListener("click", closeEditor);
  els.done.addEventListener("click", done);
  els.disable.addEventListener("click", toggleDisabled);
  els.name.addEventListener("input", () => { els.nameerr.hidden = true; rail.refresh(); });
  els.name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); done(); } });
  els.search.addEventListener("input", onSearch);
  els.search.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (found && found.site && ed && !ed.members.has(`n:site:${found.site.host}`)) addTarget({ kind: "site", name: found.site.host, display: found.site.host });
  });
  els.add.addEventListener("click", (e) => addMenu(e.currentTarget));
  els.mlist.addEventListener("change", (e) => {
    const box = e.target.closest('[data-act="tick"]');
    const node = e.target.closest(".vl-row");
    if (box && node) onTick(node, box);
  });
  els.mlist.addEventListener("click", onMembersClick);
  wireIconReady(ld.on, host);
  ld.on("apps_ready", () => { if (ed && query) runSearch(); });
  tab.every(tick);
}

/** `opts.edit`: a group id (or "new") to open - Overview's Edit, other pages' entry points. */
export function show(opts) {
  if (opts && opts.edit != null) {
    wantEdit = opts.edit;
    if (loaded) openWanted();
  }
}

export function hide() {
  clearTimeout(searchTimer);
}

export function patch({ event, payload }) {
  if (event === "status") {
    if (payload && (payload.blocks || "running" in payload || "pause" in payload)) reloadSoon();
  } else if (event === "draft") {
    if (!payload || payload.blocks !== false) reloadSoon();
  } else if (event === "blocks_changed") reloadSoon();
}
