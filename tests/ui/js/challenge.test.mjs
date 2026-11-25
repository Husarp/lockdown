import test from "node:test";
import assert from "node:assert/strict";
import { matchLen, phraseState, wordState, pickBox, parseNaive, clockOf, countdownText, whenText, CANCELLED, openChallenge, guarded } from "../../../src/ui/js/challenge.js";

test("the typed text is compared from the start", () => {
  assert.equal(matchLen("", "abc def"), 0);
  assert.equal(matchLen("abc", "abc def"), 3);
  assert.equal(matchLen("abx", "abc def"), 2);
  assert.equal(matchLen("abc defg", "abc def"), 7);
  assert.deepEqual(phraseState("abc", "abc def"), { i: 3, bad: false, complete: false });
  assert.deepEqual(phraseState("abx", "abc def"), { i: 2, bad: true, complete: false });
  assert.deepEqual(phraseState("abc def", "abc def"), { i: 7, bad: false, complete: true });
  assert.deepEqual(phraseState("abc deff", "abc def"), { i: 7, bad: true, complete: false });   // too long = a mistake
  assert.deepEqual(phraseState("", ""), { i: 0, bad: false, complete: true });
});

test("a grid word is done, on its way or a typo", () => {
  assert.equal(wordState("river", "river"), "done");
  assert.equal(wordState("riv", "river"), "ok");
  assert.equal(wordState("", "river"), "ok");
  assert.equal(wordState("rivar", "river"), "typo");
  assert.equal(wordState("rivers", "river"), "typo");
});

test("the next box is random and never the one before", () => {
  for (let prev = -1; prev <= 8; prev++) {
    const seen = new Set();
    for (let n = 0; n < 200; n++) {
      const k = pickBox(prev);
      assert.ok(k >= 0 && k <= 8 && Number.isInteger(k));
      assert.notEqual(k, prev);
      seen.add(k);
    }
    assert.equal(seen.size, prev < 0 ? 9 : 8, `all other boxes get used (prev ${prev})`);
  }
  assert.equal(pickBox(3, () => 0.999999), 8);
  assert.equal(pickBox(8, () => 0.999999), 7);
  assert.equal(pickBox(0, () => 0), 1);
});

test("times", () => {
  assert.equal(countdownText(600), "10:00");
  assert.equal(countdownText(599.2), "10:00");
  assert.equal(countdownText(59), "0:59");
  assert.equal(countdownText(0), "0:00");
  assert.equal(countdownText(-4), "0:00");
  assert.equal(countdownText(3725), "1:02:05");
  assert.equal(clockOf("2026-10-11T15:52:17"), "15:52");
  assert.equal(parseNaive("2026-10-11T15:52:17") - parseNaive("2026-10-11T15:42:17"), 600000);
  assert.ok(Number.isNaN(parseNaive("soon")));
  assert.equal(whenText("2026-10-11T18:00:00", "2026-10-11T15:42:17"), "18:00");
  assert.equal(whenText("2026-10-12T09:00:00", "2026-10-11T15:42:17"), "Mon 12 Oct 09:00");
});

test("no ticket means nothing to show", async () => {
  assert.equal(await openChallenge(null), CANCELLED);
  assert.equal(await openChallenge({ changes: ["x"] }), CANCELLED);
});

test("guarded passes plain answers through", async () => {
  assert.deepEqual(await guarded(Promise.resolve({ fine: 1 })), { fine: 1 });
  assert.equal(await guarded(() => Promise.resolve(null)), null);
  assert.equal(await guarded(Promise.resolve("text")), "text");
});

test("the dialog exempts the live supervisor field from paste protection and validates on explicit submit", async () => {
  const { readFileSync } = await import("node:fs");
  const { runInNewContext } = await import("node:vm");
  const source = readFileSync(new URL("../../../src/ui/js/challenge.js", import.meta.url), "utf8");
  const runSource = source.slice(source.indexOf("function run("), source.lastIndexOf("// ---------------------------------------------------------------- wiring"));
  const events = new Map(), calls = [], buttons = [];
  const build = () => ({
    dataset: {},
    addEventListener(type, handler) { this[type] = handler; },
    focus() {},
  });
  const capture = (type, target, detail = {}) => {
    let prevented = false;
    events.get(type)({ target, ...detail, preventDefault: () => { prevented = true; } });
    return prevented;
  };
  const field = build(), err = {}, unrelated = build();
  const body = {
    addEventListener(type, handler, useCapture) {
      if (useCapture) events.set(type, handler);
    },
    querySelector(selector) {
      if (!this.innerHTML?.includes('class="input ch-supervisor"')) return null;
      return selector === ".ch-supervisor" ? field : err;
    },
  };
  const head = {};
  const foot = {
    set textContent(_) { buttons.length = 0; },
    append: (button) => buttons.push(button),
    prepend: (button) => buttons.unshift(button),
  };
  const dlg = {
    querySelector: (selector) => selector === ".dlg-f" ? foot : head,
    addEventListener() {},
    removeEventListener() {},
  };
  const run = runInNewContext(`(${runSource})`, {
    active: null, TITLE: "Title", CANCELLED, changesHtml: () => "", esc: (s) => s,
    performance, cancelAnimationFrame() {},
    BLOCKED_INPUT: new Set(["insertFromPaste", "insertFromPasteAsQuotation", "insertFromDrop", "insertFromYank"]),
    document: { createElement: build },
    openDialog: ({ onClose }) => ({ el: dlg, body, close: onClose }),
    ld: { call: async (method, ...args) => {
      if (method === "challenge_begin") return { mode: "closed", supervisor: true };
      calls.push([method, ...args]);
      return { passed: true, result: "saved" };
    } },
  });
  const result = run({ ticket: "ticket" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(capture("paste", field), true);
  buttons.find((button) => button.textContent === "Supervisor code").click();
  for (const [type, detail] of [
    ["paste", {}], ["contextmenu", {}], ["beforeinput", { inputType: "insertFromPaste" }],
    ["beforeinput", { inputType: "insertFromPasteAsQuotation" }],
    ["keydown", { key: "v", ctrlKey: true }], ["keydown", { key: "V", metaKey: true }],
    ["keydown", { key: "Insert", shiftKey: true }],
  ]) {
    assert.equal(capture(type, field, detail), false, type);
    assert.equal(capture(type, unrelated, detail), true, type);
  }
  for (const [type, detail] of [
    ["drop", {}], ["copy", {}], ["cut", {}], ["beforeinput", { inputType: "insertFromDrop" }],
    ["beforeinput", { inputType: "insertFromYank" }],
  ]) assert.equal(capture(type, field, detail), true, type);

  const useCode = buttons.find((button) => button.textContent === "Use code");
  field.value = "0037840";
  useCode.click();
  assert.match(err.textContent, /6-digit/);
  assert.equal(calls.length, 0);
  field.value = "003784";
  assert.equal(calls.length, 0);
  useCode.click();
  assert.equal(await result, "saved");
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].slice(0, 2), ["challenge_submit", "ticket"]);
  assert.equal(calls[0][2].supervisor, "003784");
});
