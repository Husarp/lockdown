// The tabs of the Blocking page (js/pages/blocking.js): a tab id -> a function that loads its module on the FIRST visit of
// that tab (dynamic import, kept mounted after that). A tab with no line here shows "Coming soon".
//
// A tab ticket appends ONE line below (parallel tickets only append; on a clash keep both lines):
//     tabs.groups = () => import("./groups.js");
//
// The ids: overview, groups, add, protect (Site protection), calendar.
//
// A tab module has the same contract as a page (js/pages/index.js), plus what the page hands it in mount():
//     export const freshMs = 10000;          // a stale hidden tab reloads when shown only if older than this
//     export const events = [];              // not used: the page forwards every push to patch() of the visible tab
//     export function mount(el, tab) {}      // once, on the first visit. `tab`:
//         tab.el          the tab's own container (scrolls by itself; give it class "bk-fill" to scroll inside instead)
//         tab.page        the router's page handle (page.every / page.go ...)
//         tab.every(fn)   a 1 s tick that runs only while THIS tab is shown (and the window is visible)
//         tab.reload()    load() again now      tab.stale()   mark it old (it reloads when shown next)
//         tab.confirm(text, immediate)  the green "✓ text" under the title (5 s)
//         tab.editItem(id) / tab.editGroup(id) / tab.addPrefilled(target)   open the Add / Groups tab for something
//         tab.go(tabId, opts)           open another tab
//     export async function load() {}        // one Api call -> fill the tab (also for reloads)
//     export function patch({event, payload}) {}   // status / draft / blocks_changed pushes, only while shown
//     export function show(opts) {}          // the tab became visible. opts: {edit: itemId} / {edit: groupId} /
//                                            // {prefill: {site: [name, host]} | {app: {name, exe, path}}}
//     export function hide() {}
export const tabs = {};
tabs.overview = () => import("./overview.js");
tabs.calendar = () => import("./calendar.js");
tabs.protect = () => import("./protection.js");
tabs.groups = () => import("./groups.js");
