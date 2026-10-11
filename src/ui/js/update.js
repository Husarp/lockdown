// The update banner (over every page) and the update dialog. Used by the shell (initUpdate) and the About page.
// All decisions are Python's: this file only shows what `update_state` / `update` / `download_progress` say and calls
// update_download / update_install with NO arguments (Python knows which release, which file, which address).
//
// Pushes: `update` {found, outcome, open_dialog}, `download_progress` {state, version, done, total, error, file_down},
// `status.update` {version, installable} (the snapshot's own idea of a waiting update), `visible`.
import { ld } from "./bridge.js";
import { openDialog } from "./dialog.js";
import { ic, setText, toast } from "./ui.js";

const state = {
  found: null,          // {version, size, installable} of the release the banner offers, or null
  current: "",          // the running version
  closed: "",           // the version whose banner was hidden with the x: stays hidden until Lockdown is next started
  dl: { state: "idle", version: "", done: 0, total: 0, error: "", file_down: false },
};
let host = null;        // {banners, go} from the shell
let dialog = null;      // {handle, s, n, bar, notes, later, go} while the update dialog is open
let epoch = 0;          // counts the pushes that named a release: an older update_state answer must not undo them

export const updateState = () => state;
export const megabytes = (bytes) => (bytes / 1048576).toFixed(1);

/** The words for a download state: {text, tone: "" | "ok" | "bad", pct, nums}. Pure (node tests use it). */
export function downloadView(dl) {
  const pct = dl.total > 0 ? Math.min(100, Math.round((dl.done / dl.total) * 100)) : 0;
  const nums = dl.total > 0 ? `${megabytes(dl.done)} of ${megabytes(dl.total)} MB` : dl.done > 0 ? `${megabytes(dl.done)} MB` : "";
  switch (dl.state) {
    case "downloading": return { text: `Downloading Lockdown ${dl.version}…`, tone: "", pct, nums };
    case "ready": return { text: "Ready to install.", tone: "ok", pct: 100, nums };
    case "installing": return { text: "Starting the installer - Lockdown will close. Windows will ask for permission, and may warn that the installer is unsigned.", tone: "", pct: 100, nums };
    case "failed": return { text: `${dl.error || "The update didn't work."} Press Try again, or get it from GitHub.`, tone: "bad", pct, nums };
    default: return { text: "", tone: "", pct: 0, nums: "" };
  }
}

// ---------------------------------------------------------------- the banner
function renderBanner() {
  const f = state.found;
  if (!host) return;
  if (!f || f.version === state.closed) { host.banners.hide("update"); return; }
  const actions = [{ label: "What's new", onClick: () => host.go("about") }];
  if (f.installable) actions.push({ label: "Update", primary: true, onClick: () => openUpdateDialog() });
  else actions.push({ label: "Open the page", primary: true, onClick: () => ld.call("open_url").catch((e) => toast(e.message)) });
  host.banners.show("update", {
    tone: "k-brand", icon: "sparkle", order: 3, title: `Lockdown ${f.version} is available.`,
    text: f.installable ? "Your rules keep running while it installs." : "Open the GitHub page to get it.",
    actions, onClose: () => { state.closed = f.version; renderBanner(); },       // hidden until the next start
  });
}

/** A release was found / is gone (from any source). Keeps the size when the same version is reported again. */
export function setFound(f) {
  epoch++;
  if (f && state.found && state.found.version === f.version) f = { size: state.found.size, ...f };
  state.found = f ? { version: String(f.version), size: f.size || 0, installable: !!f.installable } : null;
  renderBanner();
  if (dialog && !state.found) dialog.handle.close();
}

// ---------------------------------------------------------------- the dialog
function paintDialog() {
  if (!dialog) return;
  const dl = state.dl, v = downloadView(dl);
  setText(dialog.s, v.text || "Starting…");
  dialog.s.style.color = v.tone === "bad" ? "var(--k-block)" : v.tone === "ok" ? "var(--ok)" : "";
  setText(dialog.n, v.nums);
  const w = `${v.pct}%`;
  if (dialog.bar.style.width !== w) dialog.bar.style.width = w;
  const busy = dl.state === "downloading" || dl.state === "installing";
  dialog.go.textContent = dl.state === "failed" ? "Try again" : "Restart & update";
  dialog.go.disabled = busy || dl.state === "idle";
  dialog.later.disabled = dl.state === "installing";
}

async function goClicked() {
  const dl = state.dl;
  try {
    // The file is down (or failed after it was down): run the installer. Failed during the download: download again.
    const next = dl.state === "failed" && !dl.file_down ? await ld.call("update_download") : await ld.call("update_install");
    if (next) setDownload(next);
  } catch (err) { toast(err.message || "That didn't work."); }
}

export function setDownload(dl) {
  state.dl = { ...state.dl, ...dl };
  paintDialog();
}

/** Open the update dialog: starts the download (nothing is installed until "Restart & update"). */
export async function openUpdateDialog() {
  const f = state.found;
  if (!f || !f.installable) return;
  const body = document.createElement("div");
  body.className = "stack";
  body.innerHTML = `<div class="hstack"><span class="logo-tile" style="width:40px;height:40px;border-radius:8px">${ic("logo")}</span>` +
    `<div class="grow"><b></b><p class="small muted" data-up="sub"></p></div></div>` +
    `<div class="stack-sm"><div class="hstack small"><span class="grow" data-up="s"></span><span class="num muted" data-up="n"></span></div>` +
    `<div class="meter k-brand"><i data-up="bar" style="width:0%"></i></div></div>` +
    `<div class="stack-sm"><span class="cap">What's new</span><ul class="changelog small" data-up="notes"></ul>` +
    `<div data-up="more" hidden><button class="btn btn-sm" type="button">Release notes on GitHub</button></div></div>` +
    `<p class="small faint">Your rules keep running during the update. Lockdown restarts in about 5 s.</p>`;
  body.querySelector("b").textContent = `Lockdown ${f.version}`;
  body.querySelector('[data-up="sub"]').textContent = `You have ${state.current}${f.size ? ` · ${megabytes(f.size)} MB` : ""}`;
  const handle = openDialog({
    key: "update", title: "Update Lockdown", body,
    buttons: [{ label: "Later", value: "later" },
      { label: "Restart & update", kind: "primary", disabled: true, onClick: () => { goClicked(); return false; } }],
    onClose: () => { dialog = null; },
  });
  if (!handle) return;
  const q = (k) => body.querySelector(`[data-up="${k}"]`);
  const [later, go] = handle.el.querySelectorAll(".dlg-f button");
  dialog = { handle, s: q("s"), n: q("n"), bar: q("bar"), notes: q("notes"), later, go };
  body.querySelector('[data-up="more"] button').addEventListener("click", () => ld.call("open_url").catch(() => {}));
  paintDialog();
  loadNotes(f.version, q("more"));
  try { setDownload(await ld.call("update_download")); } catch (err) { setDownload({ state: "failed", error: err.message, file_down: false }); }
}

async function loadNotes(version, more) {
  try {
    const { items } = await ld.call("update_notes", version);
    if (!dialog) return;
    for (const text of items) {
      const li = document.createElement("li");
      li.textContent = text;
      dialog.notes.append(li);
    }
    more.hidden = items.length > 0;
  } catch (_) { more.hidden = false; }
}

// ---------------------------------------------------------------- wiring (called once by the shell)
export async function refreshUpdate() {
  const before = epoch;
  const st = await ld.call("update_state");
  state.current = st.current;
  state.dl = { ...state.dl, ...st.download };
  if (epoch === before) setFound(st.found);
  paintDialog();
}

/** `shellHost` = {banners, go}. Listens to the pushes, asks Python once what is waiting, and tells it when the window
 *  is shown (it then checks for an update if the last check is old). Nothing here runs a timer. */
export function initUpdate(shellHost) {
  host = shellHost;
  ld.on("status", (delta, whole) => {
    if (delta && "update" in delta) {                                      // {version, installable} | null
      setFound(delta.update);
      if (state.found && !state.found.size) refreshUpdate().catch(() => {});   // (the snapshot has no size)
    }
  });
  ld.on("update", (p) => {
    if (!p) return;
    if ("found" in p) setFound(p.found);
    if (p.open_dialog) refreshUpdate().then(() => openUpdateDialog()).catch(() => {});
  });
  ld.on("download_progress", (p) => setDownload(p));
  ld.on("visible", (p) => { if (p && p.visible) ld.call("update_opened").catch(() => {}); });
  refreshUpdate().catch(() => {});
  if (ld.isVisible()) ld.call("update_opened").catch(() => {});
}
