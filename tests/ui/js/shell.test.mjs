import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { NAV, PAGE_IDS } from "../../../src/ui/js/router.js";
import { PATHS, symbol } from "../../../src/ui/js/icons.js";
import { canon, clockText, hhmm, parseNaive } from "../../../src/ui/js/shell.js";
import { DAYN, esc, fmt, status, tag } from "../../../src/ui/js/ui.js";

const html = readFileSync(new URL("../../../src/ui/index.html", import.meta.url), "utf8");

test("the sidebar has the ten pages in the mock's order, a separator before Settings", () => {
  assert.deepEqual(PAGE_IDS, ["dashboard", "blocking", "antibypass", "screentime", "netlog", "modes", "reminders", "notifications", "settings", "about"]);
  assert.equal(NAV.length, 11);
  assert.equal(NAV[8], null);
  assert.equal(NAV[9].id, "settings");
});

test("index.html carries the same navigation as router.js (so the first paint needs no JS)", () => {
  const nav = html.slice(html.indexOf('<nav class="nav"'), html.indexOf("</nav>"));
  const items = [...nav.matchAll(/data-nav="(\w+)"[^>]*title="([^"]+)"><svg[^>]*><use href="#i-([\w-]+)"\/>/g)].map((m) => ({ id: m[1], title: m[2], icon: m[3] }));
  assert.deepEqual(items, NAV.filter(Boolean));
  assert.ok(nav.indexOf('class="sep"') < nav.indexOf('data-nav="settings"'));
  assert.ok(nav.indexOf('class="sep"') > nav.indexOf('data-nav="notifications"'));
});

test("the inline sprite in index.html only holds symbols that icons.js defines, with the same paths", () => {
  const sprite = html.slice(html.indexOf('<svg id="sprite"'), html.indexOf("</svg>", html.indexOf('<svg id="sprite"')) + 6);
  const ids = [...sprite.matchAll(/<symbol id="i-([\w-]+)"/g)].map((m) => m[1]);
  assert.ok(ids.length >= 12);
  for (const id of ids) assert.ok(sprite.includes(symbol(id)), `symbol ${id} differs from icons.js`);
});

test("every icon the page markup uses exists", () => {
  for (const m of html.matchAll(/<use href="#i-([\w-]+)"/g)) assert.ok(PATHS[m[1]], `missing icon ${m[1]}`);
  const src = ["ui.js", "shell.js", "menu.js", "dialog.js", "pages/stub.js"].map((f) => readFileSync(new URL(`../../../src/ui/js/${f}`, import.meta.url), "utf8")).join("\n");
  for (const m of src.matchAll(/\bic\("([\w-]+)"/g)) assert.ok(PATHS[m[1]], `missing icon ${m[1]}`);
  for (const m of src.matchAll(/icon: "([\w-]+)"/g)) assert.ok(PATHS[m[1]], `missing icon ${m[1]}`);
});

test("the shell page has no designer leftovers: no #dev panel, no localStorage state, no inline handlers", () => {
  assert.ok(!html.includes('id="dev"') && !html.includes("Design states"));
  const css = ["tokens", "components", "app"].map((f) => readFileSync(new URL(`../../../src/ui/css/${f}.css`, import.meta.url), "utf8")).join("\n");
  assert.ok(!css.includes("#dev") && !css.includes(".dev-"));
  assert.ok(!/data-size=/.test(css));
  assert.equal((html.match(/localStorage/g) || []).length, 1);            // only the cosmetic look cache read
  assert.ok(!/ on(click|load|error)=/.test(html));
});

test("the designer's rules hold: two shadows, no blur or gradients, transitions at most 120 ms", () => {
  const css = ["tokens", "components", "app"].map((f) => readFileSync(new URL(`../../../src/ui/css/${f}.css`, import.meta.url), "utf8")).join("\n");
  assert.ok(!/blur\(/.test(css) && !/gradient\(/.test(css));
  const shadows = new Set([...css.matchAll(/box-shadow:\s*([^;]+);/g)].map((m) => m[1].trim()).filter((v) => v !== "none" && !v.startsWith("inset")));
  for (const s of shadows) assert.ok(/var\(--shadow-(card|pop)\)|0 0 0 \d/.test(s), `box-shadow ${s}`);
  for (const m of css.matchAll(/transition:[^;]*?(\d+)ms/g)) assert.ok(+m[1] <= 120, m[0]);
  assert.match(css, /--dur: 120ms/);
});

test("naive times are read as written, with no time zone", () => {
  assert.equal(hhmm("2026-10-11T15:42:17"), "15:42");
  assert.equal(hhmm("2026-10-11 07:05:00"), "07:05");
  assert.equal(hhmm("nonsense"), "");
  assert.equal(clockText(parseNaive("2026-10-11T15:42:17")), "Sun 11 Oct · 15:42");
  assert.equal(clockText(parseNaive("2026-12-25T00:00:00")), "Fri 25 Dec · 00:00");
  assert.ok(Number.isNaN(parseNaive(null)));
  assert.equal(parseNaive("2026-10-11T15:43:00") - parseNaive("2026-10-11T15:42:00"), 60000);
});

test("ui builders escape what they are given", () => {
  assert.equal(esc(`<a href="x">'&'</a>`), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;");
  assert.equal(esc(null), "");
  assert.ok(!tag("block", "<script>").includes("<script>"));
  assert.ok(status({ s: "block", until: "18:00" }).includes("Blocked (until 18:00)"));
  assert.ok(!status({ s: "block", until: "18:00", left: "5 min" }).includes("5 min"));      // blocked never shows time left
  assert.ok(status({ s: "block" }, true).includes("Paused"));
  assert.ok(status({ s: "low", left: "4 min" }).includes("4 min left"));
  assert.equal(fmt(125), "2 h 05 m");
  assert.equal(fmt(60), "1 h");
  assert.equal(DAYN.length, 7);
});

test("get_status's words become the snapshot's words, so a push can be merged over a fetched status", () => {
  const fetched = { service_running: false, off_since: "2026-10-11T15:00:00", mode: { id: "focus", name: "Focus", phase: "break", ends_at: null },
    pause: null, clock_now: "2026-10-11T15:42:17", draft: { dirty: false }, antibypass: { status: "free" }, tray_text: "x" };
  const c = canon(fetched);
  assert.equal(c.running, false);
  assert.equal(c.off, "2026-10-11T15:00:00");
  assert.deepEqual(c.mode.phase, { name: "break" });
  assert.ok(!("service_running" in c) && !("off_since" in c));
  assert.equal(fetched.service_running, false);                         // the input is left alone
  // a snapshot-shaped status is left as it is
  const pushed = { running: true, off: null, mode: { id: "focus", name: "Focus", phase: { name: "focus", ends_at: "x", round: 1, rounds: 4 } } };
  assert.deepEqual(canon(pushed), pushed);
  assert.deepEqual(canon({ mode: null }), { mode: null });
});
