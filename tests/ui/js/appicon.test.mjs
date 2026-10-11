import test from "node:test";
import assert from "node:assert/strict";
import { iconUrl, parseKey, iconKey, iconImg, wireIconReady } from "../../../src/ui/js/appicon.js";

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
