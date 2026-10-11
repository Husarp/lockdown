// R17: the words and numbers of a download (js/update.js downloadView) - pure, no DOM.
import test from "node:test";
import assert from "node:assert/strict";
import { downloadView, megabytes } from "../../../src/ui/js/update.js";

const MB = 1048576;

test("downloading shows the version, the percentage and 'X of Y MB'", () => {
  const v = downloadView({ state: "downloading", version: "9.9.9", done: 10 * MB, total: 40 * MB, error: "" });
  assert.deepEqual(v, { text: "Downloading Lockdown 9.9.9…", tone: "", pct: 25, nums: "10.0 of 40.0 MB" });
});

test("an unknown total still shows what is down, and never goes over 100", () => {
  assert.equal(downloadView({ state: "downloading", version: "1", done: 3 * MB, total: 0 }).nums, "3.0 MB");
  assert.equal(downloadView({ state: "downloading", version: "1", done: 3 * MB, total: 0 }).pct, 0);
  assert.equal(downloadView({ state: "downloading", version: "1", done: 50 * MB, total: 40 * MB }).pct, 100);
});

test("ready, installing and failed", () => {
  assert.equal(downloadView({ state: "ready", done: 40 * MB, total: 40 * MB }).pct, 100);
  assert.match(downloadView({ state: "installing", done: 1, total: 1 }).text, /Lockdown will close/);
  const bad = downloadView({ state: "failed", error: "The download didn't finish.", done: 0, total: 0 });
  assert.equal(bad.tone, "bad");
  assert.match(bad.text, /Try again, or get it from GitHub/);
  assert.equal(downloadView({ state: "idle" }).text, "");
});

test("megabytes", () => assert.equal(megabytes(38.2 * MB), "38.2"));
