// The Overview's data request, started the moment the Blocking page's files are all here (this module is the first import
// of js/pages/blocking.js, so it runs before the other modules are even evaluated) instead of after the page, the tab and
// the Overview are built. overview.js takes the reply in its first load(); nobody else uses it.
import { ld } from "../../bridge.js";

const STALE_MS = 3000;           // an unclaimed reply (the first visit went to another tab) is not used later
let early = null;

if (typeof document !== "undefined") {
  const rev = ld.status.blocks ? ld.status.blocks.rev : undefined;       // (as of the request: a push that comes later still reloads)
  const promise = ld.call("blocking_overview_get");
  promise.catch(() => {});                                                // never "unhandled": whoever takes it sees the error
  early = { promise, rev, at: Date.now() };
}

/** {promise, rev} for the first load, once; null when there is nothing fresh. */
export function takeEarlyOverview() {
  const e = early;
  early = null;
  return e && Date.now() - e.at < STALE_MS ? e : null;
}
