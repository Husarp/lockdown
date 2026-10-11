import test from "node:test";
import assert from "node:assert/strict";
import { createBridge } from "../../../src/ui/js/bridge.js";

// A fake page: events, a visibility flag, a fake timer queue and a settable clock.
function fakeEnv() {
  const listeners = {};
  const docListeners = {};
  const timers = new Map();
  let nextId = 1;
  const env = {
    t: 5_000_250,   // ms; 250 ms into a second
    calls: [],
    window: {
      addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
      pywebview: null,
    },
    document: {
      visibilityState: "visible",
      addEventListener(name, fn) { (docListeners[name] ||= []).push(fn); },
    },
    setTimeout(fn, ms) { const id = nextId++; timers.set(id, { fn, at: env.t + ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    now() { return env.t; },
    advance(ms) {
      const end = env.t + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        env.t = due[1].at;
        due[1].fn();
      }
      env.t = end;
    },
    pending: () => timers.size,
    fire: (name) => (listeners[name] || []).forEach((f) => f()),
    setVisibility(state) { env.document.visibilityState = state; (docListeners.visibilitychange || []).forEach((f) => f()); },
  };
  env.window.pywebview = { api: { log_client_error: async (m) => { env.calls.push(["log_client_error", m]); return { ok: true, data: null }; } } };
  return env;
}

test("push dispatches a batch in order", () => {
  const ld = createBridge(fakeEnv());
  const seen = [];
  ld.on("a", (p) => seen.push(["a", p]));
  ld.on("b", (p) => seen.push(["b", p]));
  ld.push([["a", 1], ["b", 2], ["a", 3], ["none", 4]]);
  assert.deepEqual(seen, [["a", 1], ["b", 2], ["a", 3]]);
});

test("status deltas merge into ld.status and the handler sees both", () => {
  const ld = createBridge(fakeEnv());
  let args;
  ld.on("status", (delta, whole) => { args = [delta, { ...whole }]; });
  ld.push([["status", { a: 1, b: 2 }]]);
  ld.push([["status", { b: 3 }]]);
  assert.deepEqual(args, [{ b: 3 }, { a: 1, b: 3 }]);
});

test("a throwing handler does not stop the others and is reported", async () => {
  const env = fakeEnv();
  const ld = createBridge(env);
  const seen = [];
  ld.on("x", () => { throw new Error("boom"); });
  ld.on("x", (p) => seen.push(p));
  assert.doesNotThrow(() => ld.push([["x", 1], ["x", 2]]));
  assert.deepEqual(seen, [1, 2]);
  await new Promise((r) => setImmediate(r));
  const logged = env.calls.filter((c) => c[0] === "log_client_error");
  assert.equal(logged.length, 2);
  assert.match(logged[0][1], /boom/);
});

test("push never throws on bad input; unsubscribe works", () => {
  const ld = createBridge(fakeEnv());
  assert.doesNotThrow(() => { ld.push(null); ld.push("x"); ld.push([null, 5, []]); });
  const seen = [];
  const off = ld.on("e", (p) => seen.push(p));
  ld.push([["e", 1]]);
  off();
  ld.push([["e", 2]]);
  assert.deepEqual(seen, [1]);
});

test("call unwraps the envelope and rejects on ok:false", async () => {
  const env = fakeEnv();
  env.window.pywebview.api.get = async (a, b) => ({ ok: true, data: { sum: a + b } });
  env.window.pywebview.api.bad = async () => ({ ok: false, error: "Nope" });
  env.window.pywebview.api.plain = async () => 7;
  const ld = createBridge(env);
  assert.deepEqual(await ld.call("get", 1, 2), { sum: 3 });
  await assert.rejects(ld.call("bad"), /Nope/);
  assert.equal(await ld.call("plain"), 7);
  await assert.rejects(ld.call("missing"), /Unknown Api method/);
});

test("call waits for pywebviewready once", async () => {
  const env = fakeEnv();
  env.window.pywebview = { api: {} };   // the page is up, Python's methods are not injected yet
  const ld = createBridge(env);
  let done = false;
  const p = ld.call("hello").then((v) => { done = true; return v; });
  await new Promise((r) => setImmediate(r));
  assert.equal(done, false);
  env.window.pywebview.api.hello = async () => ({ ok: true, data: "hi" });
  env.fire("pywebviewready");
  assert.equal(await p, "hi");
  assert.equal(await ld.call("hello"), "hi");
});

test("everySecond ticks aligned to the second, stops while hidden and resumes", () => {
  const env = fakeEnv();
  const ld = createBridge(env);
  const ticks = [];
  const stop = ld.everySecond((t) => ticks.push(t));
  env.advance(749);
  assert.equal(ticks.length, 0);
  env.advance(1);
  assert.deepEqual(ticks, [5_001_000]);          // first tick on the second boundary
  env.advance(1000);
  assert.deepEqual(ticks, [5_001_000, 5_002_000]);
  env.setVisibility("hidden");
  assert.equal(env.pending(), 0);                // timer really cleared
  env.advance(5000);
  assert.equal(ticks.length, 2);
  env.setVisibility("visible");                  // catches up at once, then goes on
  assert.equal(ticks.length, 3);
  env.advance(1000);
  assert.equal(ticks.length, 4);
  stop();
  assert.equal(env.pending(), 0);
});

test("the host's visible push also stops and resumes the ticks", () => {
  const env = fakeEnv();
  const ld = createBridge(env);
  let n = 0;
  ld.everySecond(() => n++);
  ld.push([["visible", { visible: false }]]);
  assert.equal(env.pending(), 0);
  env.advance(3000);
  assert.equal(n, 0);
  ld.push([["visible", { visible: true }]]);
  assert.equal(n, 1);
  env.advance(1000);
  assert.equal(n, 2);
});
