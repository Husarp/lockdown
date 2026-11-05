import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { codeRows, supervisedSection } from "../../../src/ui/js/pages/supervised_codes.js";

const data = { remaining: 1, entries: [{ id: "a", name: '<Alice "PC">', code: "005924" }, { id: "b", name: "Bob", code: "123456" }] };
test("section and rows show multiple escaped names, leading zeroes, countdown and actions", () => {
  assert.match(supervisedSection(), /Codes I supervise/);
  const html = codeRows(data);
  assert.ok(!html.includes('<Alice'));
  assert.match(html, /005924/);
  assert.match(html, /1s left/);
  for (const action of ["copy", "rename", "delete"]) assert.equal((html.match(new RegExp(`data-sc="${action}"`, "g")) || []).length, 2);
  assert.match(codeRows({ entries: [] }), /No supervised keys/);
});

const source = readFileSync(new URL("../../../src/ui/js/pages/supervised_codes.js", import.meta.url), "utf8");
const wire = source.slice(source.indexOf("export function wireSupervised")).replace("export ", "");
test("copy fetches a fresh code, handles clipboard failures and registers visible-page refresh", async () => {
  let click, tick, copied, fail = false;
  const messages = [], calls = [], list = { innerHTML: "" };
  const run = runInNewContext(`(${wire})`, {
    ld: { call: async (method) => { calls.push(method); return data; } },
    codeRows, toast: (s) => messages.push(s),
    navigator: { clipboard: { writeText: async (s) => { if (fail) throw Error("Clipboard denied"); copied = s; } } },
  });
  run({ querySelector: () => list, addEventListener: (_, cb) => { click = cb; } }, { every: (cb) => { tick = cb; } });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(list.innerHTML, /005924/);
  const event = { target: { closest: () => ({ dataset: { sc: "copy", id: "a" } }) } };
  await click(event);
  assert.equal(copied, "005924");
  assert.equal(messages[0], "Copied");
  fail = true;
  await click(event);
  assert.equal(messages[1], "Clipboard denied");
  await tick();
  assert.equal(calls.length, 4);
});

for (const action of ["add", "rename", "delete"]) {
  test(`${action} sends the UI fields without a challenge`, async () => {
    let click, options, saved = false;
    const calls = [], list = {};
    const fields = { "[data-name]": { value: "New name", focus() {} }, "[data-key]": { value: "BASE32" }, "[data-error]": {} };
    const run = runInNewContext(`(${wire})`, {
      ld: { call: async (...args) => { calls.push(args); return data; } },
      codeRows, esc: (s) => s, toast: assert.fail,
      confirmDialog: async () => true,
      openDialog: (opts) => {
        options = opts;
        return { body: { querySelector: (q) => q === "[data-key]" && action === "rename" ? null : fields[q] },
          close: () => { saved = true; }, done: Promise.resolve() };
      },
    });
    run({ querySelector: () => list, addEventListener: (_, cb) => { click = cb; } }, { every() {} });
    await new Promise(resolve => setImmediate(resolve));
    await click({ target: { closest: () => ({ dataset: { sc: action, id: action === "add" ? "" : "a" } }) } });
    if (action !== "delete") {
      options.buttons[1].onClick();
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(saved, true);
    }
    const change = calls.find((args) => args[0] === "supervised_codes_change");
    assert.deepEqual(Array.from(change), ["supervised_codes_change", action, action === "delete" ? "" : "New name",
      action === "add" ? "BASE32" : "", action === "add" ? "" : "a"]);
  });
}
