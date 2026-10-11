// A page module for the router tests: load() makes a real rpc (get_status), everything is logged in window.__probe[id].
// Register it with:  (await import('/js/pages/index.js')).pages.blocking = () => import('/__t/probe-page.js?id=blocking')
import { ld } from "/js/bridge.js";

const id = new URL(import.meta.url).searchParams.get("id") || "probe";
const log = ((window.__probe ||= {})[id] = { loads: 0, done: 0, shows: [], hides: 0, patches: [], mounts: 0, ticks: 0 });
export const freshMs = window.__probeFresh ?? 0;
export const events = ["usage_tick"];
let host;

export function mount(el, page) {
  log.mounts++;
  page.every(() => { log.ticks++; });          // a 1 s tick that must only run while this page is visible
  host = el;
  el.innerHTML = `<div class="page"><h1>Probe ${id}</h1><p class="n">0</p></div>`;
}
export async function load() {
  log.loads++;
  await ld.call("get_status");
  host.querySelector(".n").textContent = String(log.loads);
  log.done++;
}
export function patch(delta) { log.patches.push(delta); }
export function show(opts) { log.shows.push(opts || null); }
export function hide() { log.hides++; }
