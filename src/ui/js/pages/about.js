// About: the version, the update controls, what Lockdown can do, where things are (BLUEPRINT 3.13).
// Static structure built once in mount(); load() is one Api call (about_get); update / download_progress pushes patch
// the note, the buttons and the progress bar in place. The update itself is Python's job (webapi/about.py): this page
// never sends an address, a path or a file name - open_path takes "data" / "app", the rest take nothing.
import { ld } from "../bridge.js";
import { esc, ic, setText, sw, toast } from "../ui.js";
import { downloadView, openUpdateDialog } from "../update.js";

export const freshMs = 5000;
export const events = ["update", "download_progress"];

// What the app does, in the order you would meet it (the old window's list, word for word).
export const FEATURES = [
  ["Blocking", [
    "Block a site, an app or a whole category - and tick any number of blockers on it at once.",
    "By time (hours you pick, with minutes of allowance if you want a way out), a time limit per day / week / month, an opening limit, permanent, or temporary.",
    "Groups: one set of rules over several sites and apps, with a limit they share.",
    "Choose what happens: an app is closed, minimised or cut off the internet; a site can't load, has its tab closed, or sends the browser back.",
    "Disable something instead of deleting it - it keeps its rules and enforces nothing.",
  ]],
  ["Keeping yourself to it", [
    "Anti-Bypass: anything that loosens a block needs a phrase typed out first (no pasting), only inside the hours you allow, and after a wait if you set one.",
    "The enforcer runs as a Windows service with its own trusted clock - changing the Windows clock does nothing.",
    "Emergency unlock: a few uses a week, for when you really need something back.",
    "Pause my blocks (Anti-Bypass page or the tray): everything on your list unblocked for 30 min to the rest of the day, behind the challenge - protection lists and blocked words stay on.",
  ]],
  ["Knowing where the time goes", [
    "Screen Time: what you used, for how long, by category, with a timeline of the day.",
    "Network Log: which app talked to which site, in the last hour.",
    "Dashboard: today at a glance - limits, what is coming up, the last 7 days.",
  ]],
  ["Looking after yourself", [
    "Reminders: bedtime, breaks, 20-20-20, and your own - each saying whatever you want it to say.",
    "Modes: a named set of blocks you turn on for a while (Deep work, Study), optionally locked.",
    "Protection lists: scam, phishing, malware and adult sites, blocked by Lockdown's own DNS filter.",
  ]],
];

const PLACES = [["data", "Your settings, blocks and history", true], ["app", "The program", true], ["log", "The log", false]];
const IDLE_NOTE = "Nothing is sent but the request itself - no account, no identifier.";
const CHECK_GIVE_UP_MS = 20000;       // a check that never answers: stop waiting (once, after a click - not a poll)

let root;
let d = null;                          // the last about_get answer
let checking = false;
let giveUp = 0;
const q = (k) => root.querySelector(`[data-a="${k}"]`);

function field(label, desc, control) {
  return `<div class="field"><div class="field-l"><b>${esc(label)}</b><span>${desc}</span></div><div class="field-c">${control}</div></div>`;
}

export function mount(el) {
  root = el;
  const features = FEATURES.map(([head, lines]) =>
    `<div class="stack-sm"><span class="cap">${esc(head)}</span><ul class="changelog small">${lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></div>`).join("");
  const places = PLACES.map(([key, label, openable]) =>
    `<div class="field"><div class="field-l"><b>${esc(label)}</b><span class="num ellip" data-a="path-${key}" style="user-select:text"></span></div>` +
    `<div class="field-c">${openable ? `<button class="btn btn-sm" type="button" data-a="open-${key}">Open</button>` : ""}</div></div>`).join("");
  el.innerHTML = `<div class="page" style="max-width:760px">
    <div class="page-h"><div class="grow"><h1>About</h1></div></div>
    <section class="card">
      <div class="about-hero"><span class="logo-tile">${ic("logo")}</span>
        <div class="grow"><h2>Lockdown</h2><p class="muted" data-a="ver"></p></div>
        <button class="btn btn-primary" type="button" data-a="hero" hidden>${ic("download")}<span data-a="hero-t"></span></button></div>
      <div data-a="repo-on" hidden>
        <div class="field"><div class="field-l"><b>Updates</b><span data-a="note"></span></div>
          <div class="field-c">
            <button class="btn btn-sm" type="button" data-a="check">Check now</button>
            <button class="btn btn-sm btn-primary" type="button" data-a="install" hidden>Download and install</button>
            <button class="btn btn-sm" type="button" data-a="retry" hidden>Try again</button>
            <button class="btn btn-sm btn-ghost" type="button" data-a="github">${ic("external")}GitHub</button></div></div>
        <div class="field" data-a="prog" hidden><div class="field-l" style="flex-basis:100%"><div class="hstack small"><span class="grow" data-a="prog-s"></span><span class="num muted" data-a="prog-n"></span></div>
          <div class="meter k-brand"><i data-a="prog-bar" style="width:0%"></i></div></div></div>
        ${field("Check automatically", "When Lockdown starts or you open it, and every 6 hours.", sw(true, 'data-pref="auto" aria-label="Check automatically"'))}
        ${field("Tell me when a new version is found", "A small notice in the corner, once per version.", sw(true, 'data-pref="notify" aria-label="Tell me when a new version is found"'))}
      </div>
      <div class="field" data-a="repo-off" hidden><div class="field-l"><span>No repository set yet, so there is nowhere to check for updates.</span></div></div>
    </section>
    <section class="card"><header class="card-h"><h3>What Lockdown can do</h3></header><div class="card-b stack">${features}</div></section>
    <section class="card"><header class="card-h"><h3>Where things are</h3></header><div class="card-b" style="padding:0">${places}</div>
      <div class="card-b"><p class="small muted">An update never touches the data folder; uninstalling only deletes it if you tick the box.</p></div></section>
  </div>`;
  q("check").addEventListener("click", check);
  q("install").addEventListener("click", () => run("update_install"));
  q("retry").addEventListener("click", () => run(d && d.download.state === "failed" && !d.download.file_down ? "update_download" : "update_install"));
  q("github").addEventListener("click", () => ld.call("open_url").catch((e) => toast(e.message)));
  q("hero").addEventListener("click", () => openUpdateDialog());
  for (const [key,, openable] of PLACES) {
    if (openable) q(`open-${key}`).addEventListener("click", () => ld.call("open_path", key).catch((e) => toast(e.message)));
  }
  for (const input of root.querySelectorAll("[data-pref]")) input.addEventListener("change", () => savePrefs(input));
}

export async function load() {
  d = await ld.call("about_get");
  render();
}

export function patch({ event, payload }) {
  if (!d || !payload) return;
  if (event === "download_progress") d.download = { ...d.download, ...payload };
  if (event === "update" && payload.outcome) {
    d.outcome = payload.outcome;
    d.found = payload.outcome.kind === "installable" || payload.outcome.kind === "page"
      ? { version: payload.outcome.version, size: payload.outcome.size || 0, installable: !!payload.outcome.installable } : null;
    setChecking(false);
  }
  render();
}

// ---------------------------------------------------------------- what to show
/** The version the Download button offers: from the last check, else what was kept from the automatic one. */
function offer() {
  const o = d.outcome;
  if (o && o.kind === "installable" && o.installable) return { version: o.version, size: o.size };
  if (o && o.kind !== "installable") return null;
  return d.found && d.found.installable ? d.found : null;
}

function noteText() {
  const v = downloadView(d.download);
  if (v.text) return { text: v.text, tone: v.tone };
  if (checking) return { text: "Asking GitHub…", tone: "" };
  const o = d.outcome;
  if (o) return { text: o.text, tone: o.kind === "unreachable" ? "bad" : o.kind === "newest" ? "" : "ok" };
  const f = d.found;
  if (f) {
    const mb = f.size ? ` (${(f.size / 1048576).toFixed(0)} MB)` : "";
    return f.installable ? { text: `Lockdown ${f.version} is out${mb}.`, tone: "ok" }
      : { text: `Lockdown ${f.version} is out - open the GitHub page to get it.`, tone: "ok" };
  }
  return { text: IDLE_NOTE, tone: "" };
}

function render() {
  setText(q("ver"), `Version ${d.version} · released ${d.released}`);
  q("repo-on").hidden = !d.repo;
  q("repo-off").hidden = !!d.repo;
  for (const [key] of PLACES) setText(q(`path-${key}`), d.paths[key]);
  for (const input of root.querySelectorAll("[data-pref]")) {
    const on = !!d[input.dataset.pref];
    if (input.checked !== on) input.checked = on;
  }
  const dl = d.download, v = downloadView(dl);
  const busy = dl.state === "downloading" || dl.state === "installing";
  const note = noteText();
  setText(q("note"), note.text);
  q("note").style.color = note.tone === "bad" ? "var(--k-block)" : note.tone === "ok" ? "var(--ok)" : "";
  const o = offer();
  q("install").hidden = !o || busy || dl.state === "failed";
  q("retry").hidden = dl.state !== "failed";
  q("check").disabled = checking || busy;
  const show = busy || dl.state === "ready" || dl.state === "failed";
  q("prog").hidden = !show;
  if (show) {
    setText(q("prog-s"), dl.state === "ready" ? "Downloaded." : v.text.split(" - ")[0]);
    setText(q("prog-n"), v.nums);
    const w = `${v.pct}%`;
    if (q("prog-bar").style.width !== w) q("prog-bar").style.width = w;
  }
  const hero = d.found && d.found.installable ? d.found : null;
  q("hero").hidden = !hero;
  if (hero) setText(q("hero-t"), `Update to ${hero.version}`);
}

// ---------------------------------------------------------------- actions
function setChecking(on) {
  checking = on;
  clearTimeout(giveUp);
  if (on) giveUp = setTimeout(() => { checking = false; if (d) render(); }, CHECK_GIVE_UP_MS);
}

async function check() {
  setChecking(true);
  render();
  try {
    await ld.call("update_check");                    // the outcome arrives as an `update` push
  } catch (err) {
    setChecking(false);
    render();
    toast(err.message || "That didn't work.");
  }
}

async function run(name) {
  try {
    d.download = { ...d.download, ...(await ld.call(name)) };
    render();
  } catch (err) { toast(err.message || "That didn't work."); }
}

async function savePrefs(input) {
  const [auto, notify] = ["auto", "notify"].map((k) => root.querySelector(`[data-pref="${k}"]`).checked);
  try {
    await ld.call("update_set_prefs", auto, notify);
    d.auto = auto; d.notify = notify;
  } catch (err) {
    input.checked = !input.checked;
    toast(err.message || "That didn't work.");
  }
}
