// The page registry. A page id -> a function that loads its module on the FIRST visit (dynamic import; kept mounted
// after that). An id with no line here shows the "Coming soon" stub (pages/stub.js).
//
// A page ticket appends ONE line below (parallel tickets only append; on a clash keep both lines):
//     pages.dashboard = () => import("./dashboard.js");
//
// The page module contract (js/router.js has the details):
//     export const freshMs = 10000;            // a stale hidden page reloads on show only if older than this
//     export const events = ["usage_tick"];    // pushes the page wants: patch() while visible, else it is marked stale
//     export function mount(el, page) {}       // once: build the static structure into el
//     export async function load() {}          // one Api call -> fill the page (also for reloads)
//     export function patch(delta) {}          // {event, payload} for the events above, only while visible
//     export function show(opts) {}            // the page became visible (opts: from a show_page push, e.g. {tab})
//     export function hide() {}                // the page was left
export const pages = {};

pages.reminders = () => import("./reminders.js");
