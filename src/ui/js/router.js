// The router: ten pages, one container each, kept mounted. Switching = hide one container, show another (no re-render).
//
// Page module (see pages/index.js): { mount(el, page), load(), patch(delta), show(opts), hide(), freshMs, events }.
//   - mount(el, page)  once, on the first visit (module imported then, not before). `page` offers
//                      page.every(fn)  a 1 s tick that runs only while this page is visible and the window is shown,
//                      page.reload()   load() again now,   page.stale()  mark it dirty,   page.go(id, opts).
//   - load()           one Api call that fills the page. Skeleton only if it takes longer than SKELETON_MS.
//   - dirty:{page} push (ld "dirty" event, payload {page}): the visible page reloads at once (never two loads at the
//     same time - one more follows if a flag came in meanwhile); a hidden page is only marked stale and reloads on its
//     next show when it is older than freshMs.
//   - events: pushes listed in the module's `events` array go to patch({event, payload}) while the page is visible;
//     while it is hidden they just mark it stale.
//   - a "show_page" push ({page, ...}) navigates; leaving a page never blocks (an unsaved draft stays pending).
import { ld } from "./bridge.js";
import { pages as registry } from "./pages/index.js";
import { makeStub } from "./pages/stub.js";
import { errorCard, el, skPage } from "./ui.js";

export const SKELETON_MS = 300;
export const DEFAULT_FRESH_MS = 10_000;

/** The ten sidebar pages in order; null = the separator before Settings. index.html carries the same list statically. */
export const NAV = [
  { id: "dashboard", title: "Dashboard", icon: "dashboard" },
  { id: "blocking", title: "Blocking", icon: "ban" },
  { id: "antibypass", title: "Anti-Bypass", icon: "shield" },
  { id: "screentime", title: "Screen Time", icon: "chart" },
  { id: "netlog", title: "Network Log", icon: "activity" },
  { id: "modes", title: "Modes", icon: "target" },
  { id: "reminders", title: "Reminders", icon: "alarm" },
  { id: "notifications", title: "Notifications", icon: "bell" },
  null,
  { id: "settings", title: "Settings", icon: "settings" },
  { id: "about", title: "About", icon: "info" },
];
export const PAGE_IDS = NAV.filter(Boolean).map((n) => n.id);

export function createRouter(env = {}) {
  const doc = env.document || globalThis.document;          // (only used once start() / go() run: importing never touches the DOM)
  const registryOf = env.pages || registry;
  const now = env.now || (() => Date.now());
  const report = (where, err) => { try { ld.call("log_client_error", `${where}: ${err && err.stack ? err.stack : err}`.slice(0, 2000)).catch(() => {}); } catch (_) { /* never throws */ } };

  const states = new Map();      // id -> page state
  let current = null;

  const view = () => doc.getElementById("view");

  function stateOf(id) {
    let p = states.get(id);
    if (!p) {
      p = { id, host: null, mod: null, mounted: false, status: "new", loading: null, again: false, stale: false, loadedAt: 0,
            scrollTop: 0, tickers: [], offs: [], active: false, skeleton: null };
      states.set(id, p);
    }
    return p;
  }

  const freshMs = (p) => (p.mod && typeof p.mod.freshMs === "number" ? p.mod.freshMs : DEFAULT_FRESH_MS);

  // ---------------------------------------------------------------- showing and hiding
  function setNav(id) {
    for (const b of doc.querySelectorAll("#nav [data-nav]")) b.setAttribute("aria-current", b.dataset.nav === id ? "page" : "false");
  }

  function activate(p, opts) {
    if (p.active) return;
    p.active = true;
    p.host.hidden = false;
    view().scrollTop = p.scrollTop;
    p.offs = p.tickers.map((fn) => ld.everySecond(fn));
    try { if (p.mod.show) p.mod.show(opts); } catch (err) { report(`${p.id}.show`, err); }
  }

  function deactivate(p) {
    if (p.active) {
      p.active = false;
      p.scrollTop = view().scrollTop;
      for (const off of p.offs) off();
      p.offs = [];
      try { if (p.mod && p.mod.hide) p.mod.hide(); } catch (err) { report(`${p.id}.hide`, err); }
    }
    p.host.hidden = true;
  }

  // ---------------------------------------------------------------- loading
  function pageApi(p) {
    return {
      id: p.id,
      host: p.host,
      every(fn) {
        p.tickers.push(fn);
        if (p.active) p.offs.push(ld.everySecond(fn));
      },
      reload: () => reload(p),
      stale: () => { p.stale = true; },
      go: (id, opts) => go(id, opts),
    };
  }

  function showSkeleton(p) {
    if (p.skeleton || p.status === "ready") return;
    p.skeleton = el(`<div class="page-skeleton" style="position:absolute;inset:0;background:var(--surface-2);z-index:1">${skPage()}</div>`);
    p.host.append(p.skeleton);
  }

  function clearSkeleton(p) {
    if (p.skeleton) { p.skeleton.remove(); p.skeleton = null; }
  }

  function removeBootSkeleton() {
    const boot = doc.getElementById("boot-skeleton");
    if (boot) boot.remove();
  }

  async function first(p, opts) {
    p.status = "loading";
    const timer = setTimeout(() => showSkeleton(p), SKELETON_MS);
    try {
      if (!p.mounted) {
        const loaded = registryOf[p.id] ? await registryOf[p.id]() : makeStub(p.id, (NAV.find((n) => n && n.id === p.id) || {}).title || p.id);
        p.mod = loaded && loaded.mount ? loaded : (loaded && loaded.default) || loaded;
        if (!p.mod || typeof p.mod.mount !== "function") throw new Error(`Page "${p.id}" has no mount()`);
        p.mod.mount(p.host, pageApi(p));
        removeBootSkeleton();                         // (same task as mount: the static skeleton hands over without a blank frame)
        for (const name of p.mod.events || []) {
          ld.on(name, (payload) => {
            if (p.active && p.status === "ready") { try { p.mod.patch && p.mod.patch({ event: name, payload }); } catch (err) { report(`${p.id}.patch`, err); } }
            else p.stale = true;
          });
        }
        p.mounted = true;
        p.status = "mounted";
        if (current === p) activate(p, opts);
      }
      p.stale = false;                                // (before the call: a flag that comes in during load() survives it)
      await p.mod.load();
      p.loadedAt = now();
      p.status = "ready";
    } catch (err) {
      p.status = "error";
      report(`${p.id} first visit`, err);
      const card = el('<div class="page-error" style="position:absolute;inset:0;background:var(--surface-2);z-index:1"></div>');
      card.append(errorCard(err && err.message, () => { card.remove(); first(p, opts); }));       // a retry never mounts twice
      if (p.mounted) p.host.append(card); else p.host.replaceChildren(card);
    } finally {
      clearTimeout(timer);
      clearSkeleton(p);
      removeBootSkeleton();
    }
    if (p.stale && p.status === "ready" && p.active) reload(p);     // a flag arrived while loading
  }

  function reload(p) {
    if (p.status !== "ready") return p.loading;
    if (p.loading) { p.again = true; return p.loading; }
    p.loading = (async () => {
      try {
        p.stale = false;
        await p.mod.load();
        p.loadedAt = now();
      } catch (err) {
        p.stale = true;                               // not loaded: the next show tries again
        report(`${p.id}.load`, err);
      } finally {
        p.loading = null;
      }
      if (p.again) { p.again = false; reload(p); }
    })();
    return p.loading;
  }

  // ---------------------------------------------------------------- going somewhere
  function go(id, opts) {
    if (!PAGE_IDS.includes(id)) return false;
    const p = stateOf(id);
    if (current === p) {
      if (p.active && p.mod && p.mod.show) { try { p.mod.show(opts); } catch (err) { report(`${id}.show`, err); } }
      return true;
    }
    if (current) deactivate(current);
    current = p;
    setNav(id);
    try { history.replaceState(null, "", "#" + id); } catch (_) { /* a file:// or sandboxed page */ }
    if (p.status === "new") {
      p.host = doc.createElement("section");
      p.host.className = "page-host";
      p.host.dataset.page = id;
      p.host.style.position = "relative";
      view().append(p.host);
      first(p, opts);
    } else if (p.status !== "ready") {                  // loading / mounted / error: show what is there
      p.host.hidden = false;
      if (p.mounted) activate(p, opts);
    } else {                                            // ready: just show it
      activate(p, opts);
      if (p.stale && now() - p.loadedAt >= freshMs(p)) reload(p);
    }
    return true;
  }

  function dirty(id) {
    const p = states.get(id);
    if (!p) return;                                      // never visited: its first visit loads fresh data
    p.stale = true;
    if (p.active && p.status === "ready") reload(p);
  }

  function start() {
    const nav = doc.getElementById("nav");
    nav.addEventListener("click", (e) => {
      const b = e.target.closest("[data-nav]");
      if (b) go(b.dataset.nav);
    });
    ld.on("dirty", (payload) => dirty(payload && payload.page));
    ld.on("show_page", (payload) => {
      const id = typeof payload === "string" ? payload : payload && payload.page;
      go(id, payload && typeof payload === "object" ? payload : undefined);
    });
    const fromHash = () => { const id = (location.hash || "").slice(1); return PAGE_IDS.includes(id) ? id : null; };
    window.addEventListener("hashchange", () => { const id = fromHash(); if (id) go(id); });
    go(fromHash() || "dashboard");
  }

  return {
    NAV, go, dirty, start, reload: (id) => { const p = states.get(id); return p ? reload(p) : undefined; },
    get current() { return current ? current.id : null; },
    state: (id) => states.get(id),
  };
}

export const router = createRouter();
