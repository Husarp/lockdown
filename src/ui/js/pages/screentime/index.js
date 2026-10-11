// The tabs of the Screen Time page (js/pages/screentime.js): a tab id -> a function that loads its module on the FIRST visit
// of that tab (dynamic import, kept mounted after that). A tab with no line here shows "Coming soon".
//
// A tab ticket appends ONE line below (parallel tickets only append; on a clash keep both lines):
//     tabs.switches = () => import("./switches.js");
//
// The ids: overview, apps, websites, switches, calendar. Apps and Websites are the same module: apps.js makes one tab
// module for a kind ("app" / "site"), so each has its own state.
//
// A tab module is { mount(content, tab), render(data), show?(), hide?(), usesRange?, ... }:
//     export function mount(content, tab) {}   // once, on the first visit: build the static structure into `content`.
//         tab.el        the same `content`           tab.page   the router's page handle (page.go ...)
//         tab.range()   the range now ("Today" ...)  tab.reload()   ask again now     tab.stale()   mark it old
//         tab.go(tabId) show another tab
//     export function render(data) {}          // the reply of Api screentime_get(tab, range), already known not to be
//                                              // "empty" (the page shows its own empty card then). Fill IN PLACE: keyed
//                                              // rows, chart.update(), text only when it changed. Never innerHTML a list.
//     export function show() {} / hide() {}    // optional: the tab became visible / was left
//     export const usesRange = false;          // optional: the range buttons don't matter to it (Calendar)
//     export async function load() {}          // optional, INSTEAD of render: a tab that asks the Api for itself
//                                              // (Calendar: screentime_month); the page then calls it for every refresh
export const tabs = {};
tabs.overview = () => import("./overview.js");
tabs.apps = () => import("./apps.js").then((m) => m.create("app"));
tabs.websites = () => import("./apps.js").then((m) => m.create("site"));
