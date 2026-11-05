// Blocking > Overview: everything blocked, as one list - loose rules, groups as boxes with their members, and the
// Disabled ones last. Python (blocking_overview_get) works out every status and rule chip; this file sorts nothing
// itself (the order comes from Python), only filters and searches what it got, and paints it.
//
// One virtual list (vlist) holds every row - a loose item, a group's header, one of its members, the "Disabled (N)"
// heading and its rows - all the same fixed height, so 200 items cost about 30 DOM rows. A reload (a push, a draft change,
// every 30 s) replaces the rows with keyed reuse: nodes keep their place and only the text that changed is written.
// Countdowns ("Unlocked · 12m left") tick locally from their end time.
//
// What a click does: Edit -> js/pages/blocking.js entry points; Remove / Disable / Alerts -> the draft (js/draft.js); the
// challenge, if Python wants one, opens by itself and the row stays until it is passed.
import { iconKey, iconUrl, wireIconReady } from "../../appicon.js";
import { ld } from "../../bridge.js";
import { CANCELLED, parseNaive } from "../../challenge.js";
import { openDialog } from "../../dialog.js";
import { draftCall, FAILED } from "../../draft.js";
import { paint, setHue } from "../../groupicons.js";
import { closeMenu, openMenu } from "../../menu.js";
import { el, empty, esc, ic, setText } from "../../ui.js";
import { createVList } from "../../vlist.js";
import { takeEarlyOverview } from "./early.js";
import { blockNow } from "./block_now.js";
import { addTimeItem } from "./add_time.js";

export const freshMs = 10_000;
export const REFRESH_TICKS = 30;          // a reload every 30 s while shown (used times move)
export const SEARCH_MS = 120;

export const FILTERS = [["all", "All"], ["block", "Blocked now"], ["low", "Running low"], ["apps", "Apps & files"], ["sites", "Websites"], ["groups", "Groups"]];
const BLOCKED = new Set(["blocked", "pending"]);
const COUNTDOWN = { paused: "Paused · ", unlocked: "Unlocked · " };

// ---------------------------------------------------------------- plain helpers (tested in node)

/** rules.duration_text: "12m", "1h 05m", "2d 3h". */
export function durationText(seconds) {
  const minutes = Math.max(0, Math.floor(seconds / 60));
  const h = Math.floor(minutes / 60), m = minutes % 60, d = Math.floor(h / 24);
  if (d) return `${d}d ${h % 24}h`;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}

/** The status text, with the time left worked out for `nowMs` when it counts down ("Unlocked · 12m left"). */
export function statusText(status, nowMs) {
  const pre = COUNTDOWN[status.k];
  if (!pre || !status.until) return status.text;
  return `${pre}${durationText((parseNaive(status.until) - nowMs) / 1000)} left`;
}

const typeOf = (e) => e.type;
const isBlocked = (e) => BLOCKED.has(e.status.k);

/** Does this entry (an item or a group box) pass the filter pill? */
export function matchesFilter(e, filter) {
  const members = e.t === "group" ? e.members : null;
  switch (filter) {
    case "block": return members ? members.some(isBlocked) : isBlocked(e);
    case "low": return members ? members.some((m) => m.low) || (!isBlocked(e) && e.chips.some((c) => c.k === "warn")) : !!e.low;
    case "apps": return members ? members.some((m) => typeOf(m) === "app" || typeOf(m) === "category") : typeOf(e) === "app" || typeOf(e) === "category";
    case "sites": return members ? members.some((m) => typeOf(m) === "site" || typeOf(m) === "category") : typeOf(e) === "site" || typeOf(e) === "category";
    case "groups": return e.t === "group";
    default: return true;
  }
}

/** Lower-case text to search in: names, what it covers, the hosts / programs. A group also holds its members'. */
export function hayOf(e) {
  if (e._hay === undefined) {
    const own = [e.name, e.sub, e.icon && e.icon.n].filter(Boolean);
    for (const m of e.members || []) own.push(m.name, m.icon && m.icon.n);
    Object.defineProperty(e, "_hay", { value: own.filter(Boolean).join(" ").toLowerCase(), enumerable: false });
  }
  return e._hay;
}

/** The rows of the list for the entries Python sent, the search and the filter. Keys are stable across reloads. */
export function flatten(entries, disabled, { q = "", filter = "all" } = {}) {
  const query = q.trim().toLowerCase();
  const rows = [];
  for (const e of entries) {
    if (!matchesFilter(e, filter) || (query && !hayOf(e).includes(query))) continue;
    if (e.t === "item") rows.push({ key: `i:${e.id}`, k: "item", e });
    else {
      rows.push({ key: `g:${e.id}`, k: "ghead", e });
      for (const m of e.members) rows.push({ key: `m:${e.id}:${m.id}`, k: "member", e: m, g: e });
    }
  }
  if (filter === "all") {
    const off = disabled.filter((d) => !query || `${d.name} ${(d.icon && d.icon.n) || ""}`.toLowerCase().includes(query));
    if (off.length) {
      rows.push({ key: "dh", k: "dhead", n: off.length });
      for (const d of off) rows.push({ key: `d:${d.t}:${d.id}`, k: "drow", e: d });
    }
  }
  return rows;
}

const STATUS_STYLE = {
  unsaved: ["chip-low", "alert"], disabled: ["chip-paused", ""], paused: ["chip-paused", "pause"], unlocked: ["chip-emerg", "lifebuoy"],
  allowance: ["chip-mode", "clock"], allowed: ["chip-ok", ""], pending: ["chip-low", "alert"], blocked: ["chip-block", "ban"], empty: ["chip-paused", ""],
};
const CHIP_CLASS = { danger: "k-block", warn: "k-limit", ok: "k-ok", allowance: "k-mode", neutral: "k-grey" };
const CHIP_ICON = { permanent: "ban", scheduled: "clock", temporary: "hourglass", time_limit: "timer", switch_limit: "touch", allowance: "clock", group: "layers" };

/** The markup of a row's chips (a string; set only when the chips changed). */
export function chipsHtml(chips) {
  return chips.map((c) => `<span class="bchip ${CHIP_CLASS[c.k] || "k-grey"}">${ic(CHIP_ICON[c.r] || "clock")}<span>${esc(c.t)}</span></span>`).join("");
}

/** Prefer blocking / nearly exhausted rules; retain the server's order within each state. */
export function primaryChip(chips) {
  const rank = { blocked: 0, danger: 0, soon: 1, warn: 1, allowance: 2, allowed: 3, ok: 3, neutral: 4 };
  return chips.reduce((best, c) => !best || (rank[c.state || c.k] ?? 4) < (rank[best.state || best.k] ?? 4) ? c : best, null);
}

// ---------------------------------------------------------------- state

let tab = null, root = null, list = null, vl = null;
let data = null, rows = [], query = "", filter = "all";
let clock = { base: 0, at: 0 };
let loadedRev;
let searchTimer = 0, ticks = 0, reloadQueued = false, expired = false;
const els = {};

const trustedNow = () => clock.base + (performance.now() - clock.at);
const q = (k) => root.querySelector(`[data-k="${k}"]`);

const STYLE = `<style>
.bo { display: flex; flex-direction: column; gap: var(--sp-3); flex: 1; min-height: 0; }
.bo .toolbar .search { width: min(260px, 100%); }
.bo-h { padding-bottom: var(--sp-2); flex-wrap: wrap; }
.bo-h h3 { flex: none; }
.bo-grid { grid-template-columns: var(--app-icon) minmax(140px, 1.1fr) minmax(0, 2fr) calc(190px * var(--s)) calc(40px * var(--s)); }
.bo-head { display: grid; align-items: center; gap: var(--sp-3); height: calc(34px * var(--s)); padding: 0 var(--sp-4); background: var(--surface-2); border-bottom: 1px solid var(--line); }
.bo-row { padding-right: var(--sp-3); }
.bo-row .name { gap: 1px; }
.bo-nl { display: flex; align-items: center; gap: var(--sp-2); min-width: 0; }
.bo-nl b { min-width: 0; }
.bo-type { flex: none; font-style: normal; font-weight: var(--fw-regular); }
.bo-type[hidden] { display: none; }
.bo-chips { display: flex; align-items: center; gap: var(--sp-1); min-width: 0; }
.bo-rules { display: flex; align-items: center; gap: var(--sp-1); width: 100%; min-width: 0; padding: 2px; border: 0; background: transparent; color: var(--text); cursor: pointer; text-align: left; }
.bo-rules .bchip { min-width: 0; }
.bo-more { flex: none; font-size: var(--fs-sm); color: var(--text-2); }
.bo-rule-tip { position: fixed; inset: auto; margin: 0; padding: var(--sp-3); max-width: min(440px, calc(100vw - 16px)); max-height: 60vh; overflow: auto;
  border: 1px solid var(--line-strong); border-radius: var(--r-lg); background: var(--surface); color: var(--text); box-shadow: var(--shadow-card); }
.bo-rule-tip .bchip { height: auto; min-height: calc(20px * var(--s)); white-space: normal; line-height: 1.4; padding-block: 4px; }
.bo-rule-tip .bchip span { overflow: visible; overflow-wrap: anywhere; }
.bo-rule-tip .bchip .ic { flex: none; }
.bo-rule-tip .bchip + .bchip { margin-top: var(--sp-1); }
.bo-rule-tip > div { display: grid; }
.bchip { display: inline-flex; align-items: center; gap: 4px; height: calc(20px * var(--s)); max-width: 100%; padding: 0 var(--sp-2); border-radius: var(--r-pill);
  background: var(--k-soft); color: var(--text); font-size: var(--fs-sm); line-height: 1; white-space: nowrap; }
.bchip .ic { color: var(--k); width: calc(12px * var(--s)); height: calc(12px * var(--s)); }
.bchip span { overflow: hidden; text-overflow: ellipsis; }
.bo-st { min-width: 0; display: flex; }
.bo-st .chip { max-width: 100%; }
.bo-st .chip span { overflow: hidden; text-overflow: ellipsis; }
.bo-acts { display: flex; flex-wrap: wrap; gap: var(--sp-1); justify-content: flex-end; }
.bo-ic .aic { position: relative; }
.bo-ic .aic .bo-tile { width: 62%; height: 62%; stroke-width: 2; }
.bo-ic.tile .aic { --c: var(--brand); --fg: var(--on-brand); }
.bo-ic.bo-grp .aic { --c: var(--k-soft); --fg: var(--k); }
.bo-ic.tile img, .bo-ic:not(.tile) .bo-tile { display: none; }
.bo-row[data-t="ghead"] { background: var(--surface-2); box-shadow: inset 3px 0 0 var(--brand); }
.bo-row[data-t="member"] { cursor: pointer; background: var(--surface-2); box-shadow: inset 3px 0 0 var(--line-strong); padding-left: calc(var(--sp-4) + var(--sp-4)); }
.bo-row[data-t="member"]:hover { background: var(--surface-3); }
.bo-row[data-t="member"] .name b { font-weight: var(--fw-regular); }
.bo-row[data-t="dhead"] { background: var(--surface-2); }
.bo-row[data-t="dhead"] > :not(.name) { display: none; }
.bo-row[data-t="dhead"] .name { grid-column: 1 / -1; }
.bo-row[data-t="dhead"] .name b { font-size: var(--fs-cap); letter-spacing: .04em; text-transform: uppercase; color: var(--text-2); }
.bo-row[data-t="drow"] > .bo-chips, .bo-row[data-t="drow"] > .bo-st { visibility: hidden; }
.bo-row[data-t="drow"] .name b { color: var(--text-2); }
.bo-empty { flex: 1; display: grid; align-content: center; }
.bo-empty[hidden] { display: none; }
.bo-note { padding: var(--sp-2) var(--sp-4); border-top: 1px solid var(--line); color: var(--text-2); font-size: var(--fs-sm); }
@media (max-width: 1100px) { .bo-grid { grid-template-columns: var(--app-icon) minmax(120px, 1fr) minmax(0, 1.4fr) calc(140px * var(--s)) calc(40px * var(--s)); } }
@media (max-width: 860px) { .bo-grid { grid-template-columns: var(--app-icon) minmax(0, 1fr) calc(130px * var(--s)) auto; } .bo-grid .bo-chips, .bo-head .h-chips { display: none; } }
@media (max-width: 860px) {
  .bo-row { grid-template-rows: 1fr 1fr; row-gap: 0; }
  .bo-row > .name { grid-column: 2; grid-row: 1; align-self: end; }
  .bo-row > .name > span { display: none; }
  .bo-row > .bo-chips { display: flex; grid-column: 2; grid-row: 2; align-self: start; }
  .bo-row > .bo-ic { grid-column: 1; grid-row: 1 / 3; }
  .bo-row > .bo-st { grid-column: 3; grid-row: 1 / 3; }
  .bo-row > .bo-acts { grid-column: 4; grid-row: 1 / 3; }
  .bo-row[data-t="dhead"] > .name { grid-column: 1 / -1; grid-row: 1 / 3; align-self: center; }
}
@media (max-width: 620px) { .bo-more span { display: none; } }
</style>`;

const ROW_HTML = `<span class="bo-ic"><span class="aic"><img alt="" loading="lazy" decoding="async" draggable="false">${ic("layers", "bo-tile")}</span></span>
  <div class="name"><div class="bo-nl"><b class="ellip"></b><em class="chip chip-soft chip-xs bo-type" hidden></em></div><span class="ellip"></span></div>
  <div class="bo-chips tags-col"><button type="button" class="bo-rules" data-act="rules"></button></div>
  <div class="bo-st"><span class="chip">${ic("ban")}<span></span></span></div>
  <div class="acts bo-acts"><button class="btn btn-sm" type="button" data-act="more" aria-label="More options" aria-haspopup="menu" aria-expanded="false">⋯</button></div>`;

// ---------------------------------------------------------------- rows

function createRow() {
  const node = el(`<div class="row bo-row bo-grid">${ROW_HTML}</div>`);
  node._refs = {
    ic: node.querySelector(".bo-ic"), img: node.querySelector("img"), use: node.querySelector(".bo-tile use"),
    b: node.querySelector(".name b"), type: node.querySelector(".bo-type"), sub: node.querySelector(".name > span"), chips: node.querySelector(".bo-rules"),
    chip: node.querySelector(".bo-st .chip"), chipUse: node.querySelector(".bo-st .chip use"), chipText: node.querySelector(".bo-st .chip span"),
  };
  return node;
}

function setIcon(r, e, group) {
  if (group) { r.ic.classList.add("tile", "bo-grp"); paint(r.ic, e); return; }       // (the group's own icon and colour)
  r.ic.classList.remove("bo-grp");
  setHue(r.ic, null);
  const icon = e.icon;
  if (!icon || icon.k === "category") { r.ic.classList.add("tile"); r.use.setAttribute("href", "#i-folder"); return; }
  r.ic.classList.remove("tile");
  const key = iconKey(icon.k, icon.n);
  if (r.img.dataset.icon !== key) { r.img.dataset.icon = key; r.img.src = iconUrl(icon.k, icon.n); }
}

function setStatus(r, status) {
  const [cls, icon] = STATUS_STYLE[status.k] || ["chip-paused", ""];
  const className = `chip ${cls}`;
  if (r.chip.className !== className) r.chip.className = className;
  r.chipUse.parentNode.style.display = icon ? "" : "none";
  if (icon) r.chipUse.setAttribute("href", `#i-${icon}`);
  // a countdown shows Python's own words until a second has passed (the page's clock then takes over, see tick())
  setText(r.chipText, performance.now() - clock.at < 1000 ? status.text : statusText(status, trustedNow()));
  r.chip.title = r.chipText.textContent;
}

function setChips(node, chips) {
  const r = node._refs, sig = JSON.stringify(chips);
  if (node._chipSig !== sig) {
    node._chipSig = sig;
    const primary = primaryChip(chips);
    r.chips.innerHTML = primary ? chipsHtml([primary]) + (chips.length > 1 ? `<span class="bo-more">+${chips.length - 1}<span> more</span></span>` : "") : "";
    r.chips.hidden = !primary;
    r.chips.setAttribute("aria-label", primary ? `${primary.t}. Show all ${chips.length} rules` : "No rules");
  }
}

const TYPE_WORDS = { site: "Site", app: "App", category: "Category" };

/** The small "Site / App / Category" badge after a name (hidden for a group and for the Disabled heading). */
function setType(r, type) {
  const word = TYPE_WORDS[type] || "";
  setText(r.type, word);
  r.type.hidden = !word;
}

function renderRow(node, row) {
  const r = node._refs;
  node._row = row;
  node.dataset.t = row.k;
  if (row.k === "member") node.tabIndex = 0; else node.removeAttribute("tabindex");        // (a member line opens its item: Enter / Space too)
  const e = row.e;
  if (row.k === "dhead") {
    setText(r.b, `Disabled (${row.n})`);
    setType(r, "");
    setText(r.sub, "Kept with their blockers, but nothing is enforced. Edit one to turn it back on.");
    return;
  }
  setIcon(r, e, row.k === "ghead" || (row.k === "drow" && e.t === "group"));
  if (row.k === "drow") {
    setText(r.b, e.t === "group" ? `${e.name} (group)` : e.name);
    setType(r, e.t === "group" ? "" : e.type);
    setText(r.sub, "");
    return;
  }
  setText(r.b, e.name);
  setType(r, row.k === "ghead" ? "" : e.type);
  if (row.k === "ghead") setText(r.sub, `group · ${e.count} member${e.count === 1 ? "" : "s"}`);
  else if (row.k === "member") setText(r.sub, e.note || "");
  else setText(r.sub, e.sub);
  setChips(node, e.chips);
  setStatus(r, e.status);
}

// ---------------------------------------------------------------- what a click does

/** Run a draft change; whatever comes of it (done, cancelled, refused), the list is read again. */
async function act(call, done) {
  const result = await call;
  if (result !== FAILED && result !== CANCELLED && done) done(result);
  tab.reload();
}

function removeRow(row) {
  if (!row) return;
  if (row.k === "item") act(draftCall("draft_remove_item", row.e.id), () => tab.confirm(`${row.e.name} removed`));
  else if (row.k === "ghead") act(draftCall("draft_remove_group", row.e.id), () => tab.confirm(`Group ${row.e.name} removed`));
}

function openRowMenu(btn, items) {
  btn.focus({ preventScroll: true });
  const close = () => btn.setAttribute("aria-expanded", "false");
  openMenu(btn, items.map((it) => it.onClick ? { ...it, onClick: () => { close(); it.onClick(); } } : it), { onClose: close });
  btn.setAttribute("aria-expanded", "true");
}

function chooseAlerts(btn, row) {
  const e = row.e;
  openRowMenu(btn, [{ heading: "Blocked-visit alerts" }, ...[[null, "Default"], ["on", "On"], ["off", "Off"]].map(([v, label]) => ({
    label, icon: "bell", checked: e.notify === v, onClick: () => { if (e.notify !== v) act(draftCall("draft_set_notify", e.id, v)); },
  }))]);
}

function editItemMenu(btn, e) {
  openRowMenu(btn, [{ label: "Edit its blockers", icon: "pencil", onClick: () => tab.editItem(e.id) },
    ...e.groups.map((g) => ({ label: `Edit group ${g.name}`, icon: "layers", onClick: () => tab.editGroup(g.id) }))]);
}

function rowMenu(btn, row) {
  if (row.k === "dhead") return;
  closeRules();
  const e = row.e, group = row.k === "ghead" || e.t === "group";
  const edit = () => group ? tab.editGroup(e.id) : row.k === "item" && e.groups.length ? editItemMenu(btn, e) : tab.editItem(e.id);
  const items = [
    { label: "Edit", icon: "pencil", onClick: edit },
    { label: "Block now", icon: "ban", onClick: () => blockNow(e.id, group, () => tab.reload()) },
    addTimeItem(e, group, () => tab.reload()),
  ];
  const off = row.k !== "drow" && e.status?.k !== "disabled";
  items.push({ label: off ? "Disable" : "Enable", icon: off ? "pause" : "play",
    onClick: () => act(draftCall(group ? "draft_set_group_disabled" : "draft_set_disabled", e.id, off),
      () => tab.confirm(`${group ? "Group " : ""}${e.name} ${off ? "disabled" : "enabled"}`)) });
  if (row.k === "item") items.push({ label: "Blocked-visit alerts", icon: "bell", onClick: () => chooseAlerts(btn, row) });
  if (row.k === "item" || row.k === "ghead") items.push({ label: "Remove", icon: "trash", danger: true, onClick: () => {
    // Keep removal a two-step action, then use the same guarded draft call.
    openRowMenu(btn, [{ label: "Confirm", icon: "trash", danger: true, onClick: () => removeRow(row) }]);
  } });
  openRowMenu(btn, items);
}


function onClick(e) {
  const node = e.target.closest(".vl-row");
  if (!node || !node._row) return;
  const row = node._row, btn = e.target.closest("[data-act]");
  if (!btn) { if (row.k === "member") tab.editItem(row.e.id); return; }
  if (btn.dataset.act === "rules") showRules(btn, row.e);
  else if (btn.dataset.act === "more") rowMenu(btn, row);
}

// One top-layer popover: it escapes the virtual list's clipping and never changes row heights.
let ruleTip = null, ruleAnchor = null, ruleTimer = 0;
function closeRules() {
  clearTimeout(ruleTimer);
  if (ruleAnchor) ruleAnchor.removeAttribute("aria-describedby");
  if (ruleTip) { ruleTip.remove(); ruleTip = null; }
  ruleAnchor = null;
}
function showRules(btn, entry) {
  clearTimeout(ruleTimer);
  if (!entry || !entry.chips || !entry.chips.length || ruleAnchor === btn) return;
  closeRules();
  ruleAnchor = btn;
  ruleTip = el(`<div class="bo-rule-tip" popover="manual" role="tooltip" id="bo-rule-tip"><div>${chipsHtml(entry.chips)}</div></div>`);
  root.append(ruleTip);
  btn.setAttribute("aria-describedby", "bo-rule-tip");
  const tip = ruleTip;
  tip.addEventListener("toggle", () => { if (ruleTip === tip && !tip.matches(":popover-open")) closeRules(); });
  tip.addEventListener("pointerenter", () => clearTimeout(ruleTimer));
  tip.addEventListener("pointerleave", () => { if (document.activeElement !== btn) closeRules(); });
  ruleTip.showPopover();
  const rect = btn.getBoundingClientRect(), bounds = ruleTip.getBoundingClientRect();
  ruleTip.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - bounds.width - 8))}px`;
  ruleTip.style.top = `${rect.bottom + bounds.height + 8 < window.innerHeight ? rect.bottom + 4 : Math.max(8, rect.top - bounds.height - 4)}px`;
}

// ---------------------------------------------------------------- the toolbar

const HELP = "A group is one box: its rules at the top, its members underneath - click a member to open its own blockers, or Edit to change the group. A rule is red while it is blocking, orange while it is about to, green while it isn't. Status is what is enforced right now; \"Not applied\" means the change is still waiting for Save changes.";

function openHelp() {
  openDialog({ key: "bo-help", title: "How to read this list", body: `<p>${esc(HELP)}</p>`, buttons: [{ label: "OK", kind: "primary" }] });
}

function openSort(btn) {
  openMenu(btn, data.sorts.map((s) => ({
    label: s, checked: s === data.sort,
    onClick: async () => {
      try { await ld.call("blocking_set_sort", s); } catch (_) { /* the list stays as it is */ }
      tab.reload();
    },
  })));
}

function setFilter(value) {
  filter = value;
  for (const b of root.querySelectorAll("[data-filter]")) b.setAttribute("aria-pressed", String(b.dataset.filter === value));
  applyView(false);
}

function onSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { query = els.search.value; applyView(false); }, SEARCH_MS);
}

function showEmergency() {
  import("../../emergency.js").then(({ openEmergency }) => openEmergency({ onDone: (r) => { tab.confirm(r.text, true); tab.reload(); } }));
}

// ---------------------------------------------------------------- showing the data

function applyView(keepScroll = true) {
  closeMenu();
  closeRules();
  rows = flatten(data.entries, data.disabled, { q: query, filter });
  const none = data.counts.total === 0 && data.disabled.length === 0;
  els.empty.hidden = !none;
  els.card.hidden = none;
  els.nomatch.hidden = none || rows.length > 0;
  list.hidden = !rows.length;                     // (shown before the rows are set: the list needs its height to place them)
  els.toolbar.hidden = none;
  vl.setRows(rows, { keepScroll });
}

function render() {
  setText(els.title, `Everything blocked (${data.counts.total})`);
  setText(els.count, data.counts.total ? `${data.counts.blocked} blocked now` : "");
  setText(els.sort, data.sort);
  els.emergency.hidden = !data.emergency;
  applyView(true);
}

// ---------------------------------------------------------------- the tab contract

export function mount(host, t) {
  tab = t;
  root = host;
  host.classList.add("bk-fill");
  host.innerHTML = `${STYLE}<div class="bo">
    <div class="toolbar" data-k="toolbar">
      <div class="search">${ic("search")}<input class="input" placeholder="Search" aria-label="Search the list" data-k="search" autocomplete="off"></div>
      <div class="pills" role="group" aria-label="Filter">${FILTERS.map(([v, l]) => `<button class="pill" type="button" data-filter="${v}" aria-pressed="${v === "all"}">${l}</button>`).join("")}</div>
    </div>
    <div class="card list-card" data-k="card">
      <header class="card-h bo-h"><h3 data-k="title">Everything blocked</h3><button class="icon-btn icon-btn-sm" type="button" data-k="help" aria-label="How to read this list" title="How to read this list">${ic("info")}</button>
        <span class="grow"></span><span class="small muted" data-k="count"></span>
        <button class="btn btn-sm" type="button" data-k="sortbtn" aria-haspopup="menu">${ic("sort")}<span class="muted">Sort:</span><span data-k="sort">&nbsp;</span></button>
        <button class="btn btn-sm btn-emerg" type="button" data-k="emergency" hidden>${ic("lifebuoy")}Emergency unlock</button>
        <button class="btn btn-sm btn-primary" type="button" data-k="add">${ic("plus")}Add</button></header>
      <div class="bo-head bo-grid"><span></span><span class="cap">Item</span><span class="cap h-chips">Rules</span><span class="cap">Status</span><span></span></div>
      <div id="bo-list" data-k="list"></div>
      <div class="bo-empty" data-k="nomatch" hidden>${empty("k-grey", "search", "Nothing matches", "Try another word, or pick All.")}</div>
    </div>
    <div class="card bo-empty" data-k="empty" hidden>${empty("k-block", "ban", "Nothing blocked yet", "Nothing blocked yet - use + Add to block a site, an app or a whole category.", `<button class="btn btn-primary" type="button" data-k="add2">${ic("plus")}Add</button>`)}</div>
  </div>`;
  for (const k of ["toolbar", "search", "count", "sort", "emergency", "title", "list", "nomatch", "empty", "card"]) els[k] = q(k);
  list = els.list;
  vl = createVList(list, { rowVar: "--row-h", rows: [], key: (row) => row.key, create: createRow, render: renderRow });
  document.addEventListener("pointerdown", (e) => {
    if (ruleTip && !ruleTip.contains(e.target) && !ruleAnchor.contains(e.target)) closeRules();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeRules(); });
  list.addEventListener("click", onClick);
  for (const event of ["pointerover", "focusin"]) list.addEventListener(event, (e) => {
    const btn = e.target.closest(".bo-rules");
    if (btn) showRules(btn, btn.closest(".vl-row")._row.e);
  });
  list.addEventListener("focusout", (e) => { if (e.target.closest(".bo-rules")) closeRules(); });
  list.addEventListener("pointerout", (e) => {
    const btn = e.target.closest(".bo-rules");
    if (btn && !btn.contains(e.relatedTarget) && document.activeElement !== btn && !(ruleTip && ruleTip.contains(e.relatedTarget))) ruleTimer = setTimeout(closeRules, 150);
  });
  list.addEventListener("scroll", closeRules, true);
  list.addEventListener("keydown", (e) => {
    const node = e.target.closest && e.target.closest('.vl-row[data-t="member"]');
    if (node && e.target === node && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); tab.editItem(node._row.e.id); }
  });
  els.search.addEventListener("input", onSearch);
  host.querySelector(".pills").addEventListener("click", (e) => { const b = e.target.closest("[data-filter]"); if (b) setFilter(b.dataset.filter); });
  q("sortbtn").addEventListener("click", (e) => openSort(e.currentTarget));
  q("help").addEventListener("click", openHelp);
  q("add").addEventListener("click", () => tab.go("add"));
  q("add2").addEventListener("click", () => tab.go("add"));
  els.emergency.addEventListener("click", showEmergency);
  wireIconReady(ld.on, host);
  tab.every(tick);
}

export async function load() {
  const early = takeEarlyOverview();                                     // the first load uses the request early.js already sent
  loadedRev = early ? early.rev : ld.status.blocks ? ld.status.blocks.rev : undefined;       // (before the call: a push that comes meanwhile still reloads)
  data = await (early ? early.promise : ld.call("blocking_overview_get"));
  clock = { base: parseNaive(data.now), at: performance.now() };
  expired = false;
  render();
}

function tick() {
  if (!data) return;
  const now = trustedNow();
  for (const node of list.querySelectorAll(".vl-row:not([hidden])")) {
    const row = node._row;
    if (!row || !row.e || !row.e.status) continue;
    const s = row.e.status;
    if (!COUNTDOWN[s.k] || !s.until) continue;
    if (parseNaive(s.until) <= now) {
      if (!expired) { expired = true; tab.reload(); }              // it ran out: Python says what it is now
    } else setText(node._refs.chipText, statusText(s, now));
  }
  if (++ticks % REFRESH_TICKS === 0) tab.reload();
}

function reloadSoon() {
  if (reloadQueued) return;
  reloadQueued = true;
  Promise.resolve().then(() => { reloadQueued = false; tab.reload(); });      // (pushes that arrive together reload once)
}

export function patch({ event, payload }) {
  if (event === "status") {
    // what the statuses depend on: any block changing (rev), the service starting / stopping, Pause my blocks
    if (payload && ((payload.blocks && payload.blocks.rev !== loadedRev) || "running" in payload || "pause" in payload)) reloadSoon();
    if (payload && payload.emergency && data) els.emergency.hidden = !payload.emergency.usable;      // switched on / off in Settings
  } else if (event === "draft") {
    if (!payload || payload.blocks !== false) reloadSoon();
  } else if (event === "blocks_changed") reloadSoon();
}

export function hide() {
  closeMenu();
  closeRules();
  clearTimeout(searchTimer);
}
