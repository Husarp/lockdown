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
