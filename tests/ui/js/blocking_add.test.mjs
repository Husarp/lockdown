import test from "node:test";
import assert from "node:assert/strict";

// R20: the pure parts of Blocking > Add / Edit (the modules only touch the DOM when a picker / form is built).
const ways = await import("../../../src/ui/js/ways.js");
const sg = await import("../../../src/ui/js/suggest.js");
const tp = await import("../../../src/ui/js/targetpicker.js");
const add = await import("../../../src/ui/js/pages/blocking/add.js");
const edit = await import("../../../src/ui/js/pages/blocking/edit.js");

// ---------------------------------------------------------------- the ways

test("Close and Minimize exclude each other; the background processes go with Close", () => {
  assert.deepEqual(ways.toggleWay("app", ["close", "background"], "minimize", true), ["minimize"]);
  assert.deepEqual(ways.toggleWay("app", ["minimize"], "close", true), ["close"]);
  assert.deepEqual(ways.toggleWay("app", ["close", "background", "internet"], "close", false), ["internet"]);
  assert.deepEqual(ways.toggleWay("app", ["minimize"], "background", true), ["minimize"], "no Close: no background processes");
  assert.deepEqual(ways.toggleWay("app", ["close"], "background", true), ["close", "background"]);
  assert.deepEqual(ways.toggleWay("app", ["close"], "internet", true), ["close", "internet"]);
  assert.deepEqual(ways.toggleWay("category", ["close"], "minimize", true), ["minimize"]);
});

test("Close the tab and Go back exclude each other; Can't load it combines with either", () => {
  assert.deepEqual(ways.toggleWay("site", ["dns", "close"], "back", true), ["dns", "back"]);
  assert.deepEqual(ways.toggleWay("site", ["dns", "back"], "close", true), ["dns", "close"]);
  assert.deepEqual(ways.toggleWay("site", ["dns"], "dns", false), []);
  assert.deepEqual(ways.toggleWay("site", ["dns"], "minimize", true), ["dns"], "a way of another kind is ignored");
});

test("a program file can only be closed or minimised", () => {
  assert.deepEqual(ways.FILE_WAYS.map((w) => w[0]), ["close", "minimize"]);
  assert.deepEqual(ways.toggleWay("file", ["close"], "internet", true), ["close"]);
  assert.deepEqual(ways.toggleWay("file", ["close"], "minimize", true), ["minimize"]);
  assert.match(ways.HOSTED_NOTE, /javaw\.exe/);
});

test("defaults: Close; a Steam game also its background processes; a site can't load", () => {
  assert.deepEqual(ways.defaultWays("app"), ["close"]);
  assert.deepEqual(ways.defaultWays("app", true), ["close", "background"]);
  assert.deepEqual(ways.defaultWays("file", true), ["close"]);
  assert.deepEqual(ways.defaultWays("site"), ["dns"]);
  assert.deepEqual(ways.defaultWays("category"), ["close"]);
});

test("the group of a target and the problems with its ways, in Python's words", () => {
  assert.equal(ways.groupOf({ kind: "site" }), "site");
  assert.equal(ways.groupOf({ kind: "category" }), "category");
  assert.equal(ways.groupOf({ kind: "app", file: true }), "file");
  assert.equal(ways.groupOf({ kind: "app", file: false }), "app");
  assert.equal(ways.waysProblem("app", ["close"]), "");
  assert.equal(ways.waysProblem("app", ["background"]), "Tick at least one of Close app / Minimize / Block internet.");
  assert.equal(ways.waysProblem("file", ["internet"]), "Tick Close app or Minimize.");
  assert.equal(ways.waysProblem("site", []), "Tick at least one of Can't load it / Close the tab / Go back.");
  assert.equal(ways.enabled("app", ["minimize"], "background"), false);
  assert.equal(ways.enabled("app", ["close"], "background"), true);
});

// ---------------------------------------------------------------- reading typed text

test("a site address is tidied like blocker.hosts.normalize_host", () => {
  assert.equal(tp.normalizeHost("https://www.Reddit.com/r/x?y#z"), "reddit.com");
  assert.equal(tp.normalizeHost("  Example.ORG "), "example.org");
  assert.equal(tp.normalizeHost("old.reddit.com:8080/path"), "old.reddit.com");
  for (const bad of ["", "localhost", "not a site", "a..com", "-a.com", "x.c", "http://", "192.168.0.1"]) assert.equal(tp.normalizeHost(bad), null, bad);
});

test("typed text is a program (an .exe or a program file) or a site", () => {
  assert.deepEqual(tp.classify("discord.exe"), { kind: "app", target: "discord.exe", path: null, file: false });
  assert.deepEqual(tp.classify('"C:\\Games\\Foo\\Foo.exe"'), { kind: "app", target: "foo.exe", path: "C:\\Games\\Foo\\Foo.exe", file: false });
  assert.deepEqual(tp.classify("game.jar"), { kind: "app", target: "game.jar", path: null, file: true });
  assert.deepEqual(tp.classify("C:\\tools\\start.bat"), { kind: "app", target: "start.bat", path: "C:\\tools\\start.bat", file: true });
  assert.deepEqual(tp.classify("x.com.py"), { kind: "site", target: "x.com.py" }, "a .py with no folder is Paraguay's site");
  assert.equal(tp.classify("D:\\code\\tool.py").kind, "app");
  assert.deepEqual(tp.classify("https://www.Reddit.com/r/x"), { kind: "site", target: "reddit.com" });
  assert.match(tp.classify("hello world").error, /Write a site like reddit\.com/);
  assert.match(tp.classify("   ").error, /Pick a site/);
});

test("file names, paths and program files", () => {
  assert.equal(tp.fileName('"C:\\A\\B\\Foo.EXE"'), "foo.exe");
  assert.equal(tp.fileName("/usr/bin/tool.sh"), "tool.sh");
  assert.equal(tp.hasPath("foo.exe"), false);
  assert.equal(tp.hasPath("a/b.exe"), true);
  for (const f of ["a.jar", "A.BAT", "x.ps1", "m.vbs", "s.wsf", "c.class"]) assert.equal(tp.isHosted(f), true, f);
  assert.equal(tp.isHosted("a.exe"), false);
});

// ---------------------------------------------------------------- rows and targets

test("list rows for suggestions: sites, mine, apps, Steam and running", () => {
  const site = tp.rowOf({ kind: "site", name: "Reddit", host: "reddit.com", mine: false });
  assert.deepEqual([site.key, site.sub, site.badge], ["site:reddit.com", "reddit.com", "site"]);
  assert.equal(tp.rowOf({ kind: "site", name: "R", host: "r.example", mine: true }).badge, "mine");
  const app = tp.rowOf({ kind: "app", name: "CS2", exe: "cs2.exe", path: "D:\\cs2.exe", steam: true, running: false });
  assert.deepEqual([app.key, app.sub, app.badge, app.steam], ["app:cs2.exe", "cs2.exe · Steam", "Steam", true]);
  assert.equal(tp.rowOf({ kind: "app", name: "Discord", exe: "discord.exe", steam: false, running: true }).badge, "running");
  assert.equal(tp.rowOf({ kind: "app", name: "Game", exe: "game.jar", steam: false, running: false }).file, true);
});

test("typed text that is not in the list gets its own row", () => {
  const rows = [tp.rowOf({ kind: "site", name: "Reddit", host: "reddit.com", mine: false })];
  assert.equal(tp.typedRow("reddit.com", rows), null, "already listed");
  const site = tp.typedRow("https://example.org/x", rows);
  assert.deepEqual([site.key, site.name, site.typed], ["site:example.org", "Add website “example.org”", true]);
  const app = tp.typedRow("foo.exe", rows);
  assert.deepEqual([app.key, app.name, app.badge], ["app:foo.exe", "Block program “foo.exe”", "app"]);
  assert.equal(tp.typedRow("start.bat", rows).name, "Block program file “start.bat”");
  assert.equal(tp.typedRow("hello world", rows), null);
  assert.equal(tp.typedRow("", rows), null);
});

test("a picked row becomes a target; typed ones start with no display name", () => {
  const picked = tp.targetOf(tp.rowOf({ kind: "app", name: "Discord", exe: "discord.exe", path: "C:\\d.exe", steam: false, running: false }));
  assert.deepEqual([picked.kind, picked.target, picked.name, picked.picked, picked.path], ["app", "discord.exe", "Discord", true, "C:\\d.exe"]);
  const typed = tp.targetOf(tp.typedRow("foo.exe", []));
  assert.deepEqual([typed.name, typed.picked], ["", false]);
  assert.equal(tp.labelOf({ kind: "category", name: "Games", target: "games" }), "category · Games");
  assert.equal(tp.labelOf({ kind: "site", name: "", target: "x.com" }), "x.com");
});

// ---------------------------------------------------------------- the suggestions dropdown

test("the dropdown's words", () => {
  assert.equal(sg.headerText("redd"), "SUGGESTIONS · “redd”");
  assert.equal(sg.headerText(""), "SUGGESTIONS");
  assert.equal(sg.clip("x".repeat(30)).length, 30);
  assert.equal(sg.clip("x".repeat(40)), "x".repeat(29) + "…");
  assert.equal(sg.subText({ kind: "app", exe: "cs2.exe", steam: true }), "cs2.exe · Steam game");
  assert.equal(sg.subText({ kind: "app", exe: "d.exe", steam: false }), "d.exe");
  assert.equal(sg.subText({ kind: "site", host: "reddit.com" }), "reddit.com");
  assert.equal(sg.anyMine([{ mine: false }, { mine: true }]), true);
  assert.equal(sg.anyMine([{}, { mine: false }]), false);
  assert.equal(sg.MAX_ROWS, 8);
});

// ---------------------------------------------------------------- the tab

test("what other pages hand over is read in all three shapes", () => {
  assert.deepEqual(add.prefillTarget({ site: ["Reddit", "Reddit.com"] }), { kind: "site", target: "reddit.com", name: "Reddit", path: null });
  assert.deepEqual(add.prefillTarget({ app: { name: "Discord", exe: "C:\\x\\Discord.exe", path: "C:\\x\\Discord.exe" } }), { kind: "app", target: "discord.exe", name: "Discord", path: "C:\\x\\Discord.exe" });
  assert.deepEqual(add.prefillTarget({ kind: "app", name: "chrome.exe", display: "Chrome", path: null }), { kind: "app", target: "chrome.exe", name: "Chrome", path: null });
  assert.deepEqual(add.prefillTarget({ kind: "site", name: "example.org", display: "" }), { kind: "site", target: "example.org", name: "", path: null });
  // the network log sends both shapes at once, and a site may come with www. or a path
  assert.deepEqual(add.prefillTarget({ site: ["Example", "www.Example.org/page"], kind: "site", name: "example.org", display: "Example", path: null }),
    { kind: "site", target: "example.org", name: "Example", path: null });
  assert.deepEqual(add.prefillTarget({ app: { name: "Chrome", exe: "chrome.exe", path: "C:\\c.exe" }, kind: "app", name: "chrome.exe", display: "Chrome", path: "C:\\c.exe" }),
    { kind: "app", target: "chrome.exe", name: "Chrome", path: "C:\\c.exe" });
  for (const bad of [null, undefined, 5, "x", {}, { site: [] }, { kind: "folder", name: "x" }]) assert.equal(add.prefillTarget(bad), null);
});

test("one notice for what was done to one or several targets", () => {
  const added = (name) => ({ result: "added", name, notice: `${name} blocker added` });
  assert.equal(add.noticeOf([]), "");
  assert.equal(add.noticeOf([added("Reddit")]), "Reddit blocker added");
  assert.equal(add.noticeOf([added("Reddit"), added("Twitch")]), "Reddit, Twitch blockers added");
  assert.equal(add.noticeOf([added("Reddit"), { result: "merged", name: "Twitch", notice: "Twitch was already on the list - blockers merged" }]),
    "Reddit blocker added · Twitch was already on the list - blockers merged");
  assert.match(add.passedResult({ name: "Reddit" }, "merged").notice, /already on the list - blockers merged/);
  assert.equal(add.passedResult({ target: "x.com" }, "saved").notice, "x.com saved");
});

test("the edit bar's words", () => {
  assert.equal(edit.alertWord(null), "Default");
  assert.equal(edit.alertWord(undefined), "Default");
  assert.equal(edit.alertWord("on"), "On");
  assert.equal(edit.alertWord("off"), "Off");
  assert.equal(edit.title({ name: "Reddit" }), "Edit Reddit");
  assert.equal(edit.groupsLine({ groups: [] }), "");
  assert.equal(edit.groupsLine({ groups: [{ id: 1, name: "Fun", chose: false }, { id: 2, name: "Night", chose: false }] }),
    "Also in groups: Fun, Night (edit those in the Groups tab)");
  assert.equal(edit.groupsLine({ groups: [{ id: 1, name: "Fun", chose: true }] }),
    "Also in groups: Fun (edit those in the Groups tab) - when Fun blocks it, that group's way of blocking applies");
});
