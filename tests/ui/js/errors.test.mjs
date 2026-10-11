import test from "node:test";
import assert from "node:assert/strict";
import { createBridge } from "../../../src/ui/js/bridge.js";

// A fake window that keeps its listeners, so a test can raise an "error" / "unhandledrejection" by hand.
function fakePage() {
  const listeners = {}, logged = [];
  const window = {
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); },
    pywebview: { api: { log_client_error: async (m) => { logged.push(m); return { ok: true, data: null }; } } },
  };
  const env = { window, document: { visibilityState: "visible", addEventListener() {} } };
  return { env, logged, raise: (name, ev) => (listeners[name] || []).forEach((f) => f(ev)) };
}
const settle = () => new Promise((r) => setTimeout(r, 5));

test("an uncaught error on any page that loads the bridge is logged once with its message", async () => {
  const p = fakePage();
  createBridge(p.env);
  p.raise("error", { message: "x is not defined", filename: "corner.js", lineno: 7 });
  await settle();
  assert.deepEqual(p.logged, ["x is not defined (corner.js:7)"]);
});

test("an unhandled promise rejection is logged with its stack", async () => {
  const p = fakePage();
  createBridge(p.env);
  p.raise("unhandledrejection", { reason: { stack: "Error: nope\n  at f" } });
  p.raise("unhandledrejection", { reason: "plain words" });
  await settle();
  assert.deepEqual(p.logged, ["unhandled: Error: nope\n  at f", "unhandled: plain words"]);
});

test("at most five errors are sent, and a long message is cut", async () => {
  const p = fakePage();
  createBridge(p.env);
  for (let i = 0; i < 9; i++) p.raise("error", { message: "m".repeat(5000), filename: "a.js", lineno: i });
  await settle();
  assert.equal(p.logged.length, 5);
  assert.ok(p.logged.every((m) => m.length === 2000));
});

test("a failing log call never throws into the page", async () => {
  const p = fakePage();
  p.env.window.pywebview.api.log_client_error = async () => { throw new Error("gone"); };
  createBridge(p.env);
  p.raise("error", { message: "boom", filename: "a.js", lineno: 1 });
  await settle();                       // (no unhandled rejection: the test would fail)
});
