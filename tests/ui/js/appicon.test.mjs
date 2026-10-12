import test from "node:test";
import assert from "node:assert/strict";
import { iconUrl, parseKey, iconKey, iconImg, wireIconReady, iconFallback, installIconFallback } from "../../../src/ui/js/appicon.js";

test("icon urls: lower-case names, encoded, with an optional cache-buster", () => {
  assert.equal(iconUrl("app", "Chrome.exe"), "/icon/app/chrome.exe.png");
  assert.equal(iconUrl("site", "example.com", 5), "/icon/site/example.com.png?v=5");
  assert.equal(iconUrl("app", "my game (x86).exe"), "/icon/app/my%20game%20(x86).exe.png");
  assert.equal(iconUrl("letter", "Spotify"), "/icon/letter/Spotify.svg");
  assert.equal(iconUrl("app", "a&b.exe"), "/icon/app/a%26b.exe.png");
});

test("keys round-trip and the img markup carries the key", () => {
  assert.deepEqual(parseKey("app:chrome.exe"), { kind: "app", name: "chrome.exe" });
  assert.deepEqual(parseKey("site:a:b.com"), { kind: "site", name: "a:b.com" });
  assert.equal(parseKey("nonsense"), null);
  assert.equal(iconKey("app", "Chrome.EXE"), "app:chrome.exe");
  assert.match(iconImg("app", "Chrome.exe", "aic"), /data-icon="app:chrome.exe" src="\/icon\/app\/chrome.exe.png"/);
});

test("icon_ready swaps the src of the matching images only", () => {
  const mk = (key) => ({ dataset: { icon: key }, src: "old" });
  const imgs = [mk("app:chrome.exe"), mk("app:other.exe"), mk("app:chrome.exe")];
  let handler;
  const off = wireIconReady((ev, fn) => { assert.equal(ev, "icon_ready"); handler = fn; return () => "off"; },
    { querySelectorAll: () => imgs }, () => 77);
  handler({ key: "app:chrome.exe" });
  assert.deepEqual(imgs.map((i) => i.src), ["/icon/app/chrome.exe.png?v=77", "old", "/icon/app/chrome.exe.png?v=77"]);
  handler({ key: "garbage" });
  handler(null);
  assert.equal(off(), "off");
});

test("the data-icon attribute is fully escaped", () => {
  assert.match(iconImg("app", `a&b<c>"d'.exe`), /data-icon="app:a&amp;b&lt;c&gt;&quot;d&#39;\.exe"/);
});

test("a failed icon becomes its letter tile once and never loops", () => {
  const img = { dataset: { icon: "app:osu!(lazer)" }, src: "http://127.0.0.1:5/icon/app/osu!(lazer).png" };
  assert.equal(iconFallback(img), true);
  assert.equal(img.src, "/icon/letter/osu!(lazer).svg");
  img.src = "http://127.0.0.1:5" + img.src;           // (a browser reports the absolute URL)
  assert.equal(iconFallback(img), false);              // the tile itself failing changes nothing
  assert.equal(img.src, "http://127.0.0.1:5/icon/letter/osu!(lazer).svg");
  img.src = "/icon/app/osu!(lazer).png?v=3";           // icon_ready swapped the real one back in: it may fall back again
  assert.equal(iconFallback(img), true);
});

test("the fallback ignores images without a key and keeps the tile name safe", () => {
  assert.equal(iconFallback({ dataset: {}, src: "x" }), false);
  assert.equal(iconFallback({ dataset: { icon: "" }, src: "x" }), false);
  assert.equal(iconFallback({ dataset: { icon: "nonsense" }, src: "x" }), false);
  assert.equal(iconFallback(null), false);
  const img = { dataset: { icon: "site:a/b\\c\u0001d" + "x".repeat(80) }, src: "" };
  iconFallback(img);
  assert.match(img.src, /^\/icon\/letter\/a_b_c_dx{57}\.svg$/);
});

test("one capture-phase error listener is installed once and only acts on <img>", () => {
  const calls = [];
  const doc = { addEventListener: (type, fn, capture) => calls.push({ type, fn, capture }) };
  assert.equal(installIconFallback(doc), true);
  assert.equal(installIconFallback(doc), false);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].type, "error");
  assert.equal(calls[0].capture, true);
  const img = { tagName: "IMG", dataset: { icon: "app:x.exe" }, src: "/icon/app/x.exe.png" };
  calls[0].fn({ target: img });
  assert.equal(img.src, "/icon/letter/x.exe.svg");
  const other = { tagName: "SCRIPT", dataset: { icon: "app:x.exe" }, src: "s.js" };
  calls[0].fn({ target: other });
  assert.equal(other.src, "s.js");
  assert.equal(installIconFallback(null), false);
});
