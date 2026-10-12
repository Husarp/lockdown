// The page's side of the Python <-> page link.
//   ld.push(batch)       Python calls this (one evaluate_js per <= 100 ms) with [[event, payload], ...].
//   ld.on(event, fn)     subscribe to a pushed event; returns an unsubscribe function.
//   ld.call(name, ...a)  call pywebview.api[name]; resolves with the reply's `data`, rejects on {ok:false}.
//   ld.everySecond(fn)   a clock tick aligned to the second that only runs while the window is visible.
// Events (BLUEPRINT 3.0): status (a delta; ld.status holds the merged whole), draft, notice, dirty ({page}),
// icon_ready, download_progress, challenge_open, mode_changed, blocks_changed, usage_tick, show_page, visible.
// No framework, no polling: nothing here runs while the window is hidden.

export function createBridge(env = {}) {
  const root = env.window || (typeof window !== "undefined" ? window : globalThis);
  const doc = env.document || root.document;
  const setT = env.setTimeout || ((f, ms) => root.setTimeout(f, ms));
  const clearT = env.clearTimeout || ((h) => root.clearTimeout(h));
  const nowMs = env.now || (() => Date.now());

  const handlers = new Map();   // event -> Set<fn>
  const ld = { status: {}, hostVisible: true };

  // ------------------------------------------------------------ calling Python
  let readyPromise = null;
  const apiReady = () => !!(root.pywebview && root.pywebview.api && Object.keys(root.pywebview.api).length);
  function ready() {
    if (!readyPromise) {
      readyPromise = new Promise((resolve) => {
        if (apiReady()) return resolve();
        root.addEventListener("pywebviewready", () => resolve(), { once: true });
      });
    }
    return readyPromise;
  }

  ld.call = async function call(name, ...args) {
    await ready();
    const fn = root.pywebview.api[name];
    if (typeof fn !== "function") throw new Error(`Unknown Api method: ${name}`);
    const res = await fn(...args);
    if (res && typeof res === "object" && "ok" in res) {
      if (!res.ok) {
        const err = new Error(res.error || "Something went wrong.");
        err.reply = res;
        throw err;
      }
      return res.data;
    }
    return res;
  };

  function report(where, err) {
    const msg = `${where}: ${err && err.stack ? err.stack : err}`;
    try {
      ld.call("log_client_error", msg.slice(0, 2000)).catch(() => {});
    } catch (_) { /* reporting must never throw */ }
  }

  // ------------------------------------------------------------ errors in our own code go to the log (every page that loads the bridge)
  // A page whose <head> has the early error hook (root.__ldErrorHook: it also catches a module that fails to load, before
  // this file runs) leaves the reporting to it, so nothing is logged twice.
  let sentErrors = 0;
  const sendError = (msg) => {
    if (sentErrors++ < 5) ld.call("log_client_error", String(msg).slice(0, 2000)).catch(() => {});      // (at most 5 per page)
  };
  if (root.addEventListener && !root.__ldErrorHook) {
    root.addEventListener("error", (e) => sendError(`${e.message} (${e.filename}:${e.lineno})`));
    root.addEventListener("unhandledrejection", (e) => sendError(`unhandled: ${e.reason && e.reason.stack ? e.reason.stack : e.reason}`));
  }

  // ------------------------------------------------------------ pushes from Python
  ld.on = function on(event, fn) {
    let set = handlers.get(event);
    if (!set) handlers.set(event, (set = new Set()));
    set.add(fn);
    return () => set.delete(fn);
  };

  function dispatch(event, payload) {
    if (event === "status" && payload && typeof payload === "object") Object.assign(ld.status, payload);
    if (event === "visible") {
      ld.hostVisible = !!(payload && payload.visible);
      syncTimers();
    }
    const set = handlers.get(event);
    if (!set) return;
    for (const fn of [...set]) {
      try {
        fn(payload, ld.status);
      } catch (err) {
        report(`handler for "${event}"`, err);
      }
    }
  }

  ld.push = function push(batch) {
    try {
      if (!Array.isArray(batch)) return;
      for (const item of batch) {
        if (Array.isArray(item)) dispatch(item[0], item[1]);
      }
    } catch (err) {
      report("push", err);
    }
  };   // returns undefined on purpose: evaluate_js then has nothing to send back

  // ------------------------------------------------------------ local clock ticks
  const ticks = new Set();
  let timer = null;

  ld.isVisible = () => ld.hostVisible && (!doc || doc.visibilityState !== "hidden");

  function runTick(fn) {
    try {
      fn(nowMs());
    } catch (err) {
      report("everySecond", err);
    }
  }

  function schedule() {
    if (timer !== null || !ticks.size || !ld.isVisible()) return;
    timer = setT(() => {
      timer = null;
      for (const fn of [...ticks]) runTick(fn);
      schedule();
    }, 1000 - (nowMs() % 1000));
  }

  function syncTimers() {
    if (ld.isVisible()) {
      if (timer === null && ticks.size) {
        for (const fn of [...ticks]) runTick(fn);   // catch up straight away after being hidden
        schedule();
      }
    } else if (timer !== null) {
      clearT(timer);
      timer = null;
    }
  }

  ld.everySecond = function everySecond(fn) {
    ticks.add(fn);
    schedule();
    return () => {
      ticks.delete(fn);
      if (!ticks.size && timer !== null) {
        clearT(timer);
        timer = null;
      }
    };
  };

  if (doc && doc.addEventListener) doc.addEventListener("visibilitychange", syncTimers);
  root.ld = ld;
  return ld;
}

export const ld = createBridge();
export default ld;

// Focus rings only for the keyboard: Chromium also shows :focus-visible when the window gets focus back from another
// app, which outlined the last-clicked tab / sidebar item. A key that moves focus turns the rings on, a click off.
if (typeof document !== "undefined") {
  const root = document.documentElement;
  addEventListener("keydown", (e) => {
    if (e.key === "Tab" || e.key.startsWith("Arrow") || e.key === "Home" || e.key === "End") root.dataset.kbd = "";
  }, true);
  addEventListener("pointerdown", () => { delete root.dataset.kbd; }, true);
}
