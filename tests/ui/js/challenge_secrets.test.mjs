import test from "node:test";
import assert from "node:assert/strict";

// Own password, authenticator code and recovery code: the pure helpers of the dialog (challenge_kinds.js), the step
// editor (steplist.js) and the Anti-Bypass page's section (pages/antibypass_secrets.js). The flows run in Chrome.
const { recoveryTyped, secretText, stripModel, SHORT } = await import("../../../src/ui/js/challenge_kinds.js");
const { estimate, stepSeconds, stepsLooser } = await import("../../../src/ui/js/steplist.js");
const { dayText, secretLines, missingText, passwordProblem, FORGOTTEN } = await import("../../../src/ui/js/pages/antibypass_secrets.js");

const OPTIONS = {
  kinds: [
    { id: "math", label: "Math", seconds: [6, 10, 20, 35], levels: [{ level: 0, label: "Easy" }] },
    { id: "phrase", label: "Typed phrase", levels: [{ level: 60, label: "Medium" }], min_level: 30, max_level: 2000 },
    { id: "password", label: "Own password", levels: [], seconds: [8], secret: true, times: 1 },
    { id: "totp", label: "Authenticator code", levels: [], seconds: [20], secret: true, times: 1 },
  ],
  max_steps: 6, min_times: 1, max_times: 20, phrase_seconds: { base: 3, per_char: 0.4, grid: 1.3 }, long_seconds: 600,
};
const row = (id, kind, level = 0, times = 1) => ({ id, kind, level, times });
const status = (pw, totp, rec, missing = []) => ({
  password: { set: pw, since: pw ? "2026-10-13 10:12:00" : null },
  totp: { set: totp, since: totp ? "2026-10-12 08:00:00" : null },
  recovery: { exists: rec, made: rec ? "2026-10-13 10:12:00" : null }, used: [], missing,
});

test("recoveryTyped: upper case, only signs and dashes, at most 19", () => {
  assert.equal(recoveryTyped("abcd-efgh jklm_npqr"), "ABCD-EFGHJKLMNPQR");
  assert.equal(recoveryTyped("x".repeat(30)).length, 19);
  assert.equal(recoveryTyped(null), "");
});

test("secretText: what the password, the code and the recovery code ask", () => {
  assert.match(secretText("password", false), /password/);
  assert.match(secretText("totp", false), /6-digit code/);
  assert.match(secretText("totp", true), /recovery code.*used up/);
  assert.equal(SHORT.password, "Password");
  assert.equal(SHORT.totp, "Code");
});

test("the step strip shows a secret step without times", () => {
  const run = { steps: [row("a", "math", 0, 2), row("b", "password"), row("c", "totp")], step: 1, done: 0 };
  assert.deepEqual(stripModel(run), [
    { text: "Math ×2", state: "done", small: "" },
    { text: "Password", state: "cur", small: "" },
    { text: "Code", state: "todo", small: "" },
  ]);
});

test("the editor's estimate and looser rules know the secret kinds", () => {
  assert.equal(stepSeconds(row("p", "password"), OPTIONS), 8);
  assert.equal(stepSeconds(row("t", "totp"), OPTIONS), 20);
  assert.equal(estimate([row("p", "password"), row("t", "totp"), row("m", "math", 0, 2)], OPTIONS).seconds, 40);
  const saved = [row("m", "math"), row("p", "password")];
  assert.deepEqual(stepsLooser(saved, [...saved, row("t", "totp")], OPTIONS), []);          // adding: free
  assert.deepEqual(stepsLooser(saved, [saved[0]], OPTIONS), ["removed Own password ×1"]);
  assert.deepEqual(stepsLooser(saved, [saved[0], row("p", "math")], OPTIONS), ["changed Own password ×1 to Math"]);
});

test("the section's lines: set, set up, made, and the warnings", () => {
  assert.equal(dayText("2026-10-13 10:12:00"), "13 Oct 2026");
  assert.equal(dayText("2026-10-13 10:12:00", true), "13 Oct 2026 10:12");
  assert.equal(dayText(""), "");
  assert.deepEqual(secretLines(status(false, false, false)), {
    password: "Not set.", totp: "Not set up.", recovery: "Made with your first password or authenticator.",
  });
  const on = secretLines(status(true, true, true));
  assert.equal(on.password, "Set 13 Oct 2026.");
  assert.equal(on.totp, "Set up 12 Oct 2026.");
  assert.equal(on.recovery, "Made 13 Oct 2026 10:12 - shown only then.");
  assert.match(secretLines(status(true, false, false)).recovery, /make a new one/);
  assert.equal(missingText(status(false, false, false)), "");
  assert.match(missingText(status(false, false, false, ["password"])), /asks your password, but it isn't set up/);
  assert.match(missingText(status(false, false, false, ["password", "totp"])), /password and authenticator, but they aren't/);
  assert.match(FORGOTTEN, /only the recovery code gets you out - uninstalling Lockdown asks this challenge too/);
});

test("passwordProblem: the boxes are checked before sending (Python checks again)", () => {
  assert.equal(passwordProblem("", ""), "Type a password.");
  assert.equal(passwordProblem("abc", "abc"), "At least 4 characters, please.");
  assert.equal(passwordProblem("abcd", "abce"), "The two passwords are not the same.");
  assert.equal(passwordProblem("abcd", "abcd"), "");
});
