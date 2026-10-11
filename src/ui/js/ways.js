// "When blocked": how a blocked app, program file, category or website is dealt with (blocker/apps.py, blocker/site_block.py).
//   apps and categories   Close app (not with Minimize)  - Also close its background processes (only with Close)  - Minimize
//                         - Block internet. Default Close; a Steam game: Close + its background processes.
//   program files         Close app / Minimize only (a Java game or script runs inside javaw / python / cmd, which other
//                         programs use too: no internet cut, no background processes).
//   websites              Can't load it  - Close the tab (not with Go back)  - Go back. Default: Can't load it.
// The pure rules are exported (tested in node); createWays() draws the checkboxes. Python re-checks every choice
// (webapi/blocking_edit.py) - nothing here is trusted.
import { el, esc, ic } from "./ui.js";

export const APP_WAYS = [
  ["close", "Close app", "Asked to close first (10 s to save), then force-closed; started while blocked: closed at once."],
  ["background", "Also close its background processes", "Once the app is closed: what it started and what runs from its install folder."],
  ["minimize", "Minimize", "Keeps it running (e.g. a browser with many tabs) but minimizes it whenever it's opened."],
  ["internet", "Block internet", "It can't connect to the internet."],
];
export const FILE_WAYS = [APP_WAYS[0], APP_WAYS[2]];
export const SITE_WAYS = [
  ["dns", "Can't load it", "The address goes nowhere, in every browser - what Lockdown has always done."],
  ["close", "Close the tab", "If you open it anyway, the tab is closed (a browser with one tab gets a fresh tab first)."],
  ["back", "Go back", "The browser goes back instead; if that doesn't leave the page, the tab is closed."],
];
export const HOSTED_NOTE = "A Java game or a script runs inside javaw.exe / python / cmd / PowerShell, which other programs use too: Lockdown closes or minimises this one only - found by its file name, wherever the file is (a renamed copy is not). It can't cut its internet: that would cut every Java / Python program's.";

/** The groups of ways: app, category (its apps), file (a program file), site. */
export const GROUPS = ["app", "category", "file", "site"];
const LISTS = { app: APP_WAYS, category: APP_WAYS, file: FILE_WAYS, site: SITE_WAYS };
const TITLES = { app: "Apps", category: "Category (its apps)", file: "Program files", site: "Websites" };
const HINTS = {
  app: "Tick one or more; Close and Minimize exclude each other.",
  category: "How every app in the category is dealt with; Close and Minimize exclude each other.",
  file: "Close or Minimize - the file is found by its name.",
  site: "Tick one or more; Close the tab and Go back exclude each other.",
};

/** Which group a target belongs to: {kind: site | app | category, file: a program file (.jar, .bat ...)}. */
export const groupOf = (t) => (t.kind === "site" ? "site" : t.kind === "category" ? "category" : t.file ? "file" : "app");

/** What a freshly picked target starts with. */
export function defaultWays(group, steam = false) {
  if (group === "site") return ["dns"];
  return steam && group === "app" ? ["close", "background"] : ["close"];
}

/** The ways after the owner ticked / unticked `flag`: Close and Minimize (and Close the tab and Go back) exclude each other,
 *  the background processes go with Close. Returns a new array in list order. */
export function toggleWay(group, flags, flag, on) {
  const set = new Set(flags);
  if (!LISTS[group].some((w) => w[0] === flag)) return LISTS[group].map((w) => w[0]).filter((f) => set.has(f));
  if (on) {
    set.add(flag);
    if (group === "site") { if (flag === "close") set.delete("back"); else if (flag === "back") set.delete("close"); }
    else if (flag === "close") set.delete("minimize");
    else if (flag === "minimize") { set.delete("close"); set.delete("background"); }
    else if (flag === "background" && !set.has("close")) set.delete("background");
  } else {
    set.delete(flag);
    if (flag === "close") set.delete("background");
  }
  return LISTS[group].map((w) => w[0]).filter((f) => set.has(f));
}

/** Can this box be ticked now? (the background processes need Close app) */
export const enabled = (group, flags, flag) => flag !== "background" || flags.includes("close");

/** Why nothing can be saved, or "" - the same words as Python's. */
export function waysProblem(group, flags) {
  if (group === "site") return flags.length ? "" : "Tick at least one of Can't load it / Close the tab / Go back.";
  if (group === "file") return flags.some((f) => f === "close" || f === "minimize") ? "" : "Tick Close app or Minimize.";
  return flags.some((f) => f !== "background") ? "" : "Tick at least one of Close app / Minimize / Block internet.";
}

// ---------------------------------------------------------------- the boxes

const section = (g) => `<section class="ways-g" data-g="${g}" hidden><div class="ways-h"><b class="cap">${esc(TITLES[g])}</b><span class="small muted">${esc(HINTS[g])}</span></div>` +
  `<div class="way-list">${LISTS[g].map(([flag, label, help]) =>
    `<label class="check way${flag === "background" ? " way-sub" : ""}" data-w="${flag}"><input type="checkbox" data-flag="${flag}"><span class="ellip">${esc(label)}</span>` +
    `<span class="way-help" tabindex="0" role="img" aria-label="${esc(label)}: ${esc(help)}" title="${esc(help)}">${ic("info")}</span></label>`).join("")}</div>` +
  (g === "file" ? `<p class="small muted ways-note">${esc(HOSTED_NOTE)}</p>` : "") + `</section>`;

/**
 * createWays({onChange}) -> {el, show(groups), values(group), set(group, flags), problem(group)}
 *   show(["app", "site"])   only these groups are drawn
 *   values(group)           the ticked flags, in list order
 *   set(group, flags)       tick exactly these (no onChange)
 * onChange({group}) runs when the owner ticks something.
 */
export function createWays({ onChange } = {}) {
  const root = el(`<div class="ways">${GROUPS.map(section).join("")}</div>`);
  const state = Object.fromEntries(GROUPS.map((g) => [g, defaultWays(g)]));
  const secs = Object.fromEntries(GROUPS.map((g) => [g, root.querySelector(`[data-g="${g}"]`)]));
  const paint = (g) => {
    for (const label of secs[g].querySelectorAll("[data-w]")) {
      const flag = label.dataset.w, input = label.firstElementChild;
      input.checked = state[g].includes(flag);
      input.disabled = !enabled(g, state[g], flag);
    }
  };
  GROUPS.forEach(paint);
  root.addEventListener("change", (e) => {
    const input = e.target.closest("[data-flag]");
    if (!input) return;
    const g = input.closest("[data-g]").dataset.g;
    state[g] = toggleWay(g, state[g], input.dataset.flag, input.checked);
    paint(g);
    if (onChange) onChange({ group: g });
  });
  return {
    el: root,
    show(groups) { for (const g of GROUPS) secs[g].hidden = !groups.includes(g); root.hidden = !groups.length; },
    values: (g) => [...state[g]],
    set(g, flags) {
      const ordered = LISTS[g].map((w) => w[0]).filter((f) => flags.includes(f));
      state[g] = ordered.reduce((acc, f) => toggleWay(g, acc, f, true), []);
      paint(g);
    },
    problem: (g) => waysProblem(g, state[g]),
  };
}
