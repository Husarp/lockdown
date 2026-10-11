// The right-click menu of an app or a site, wherever you can search one up (Screen Time lists, Network Log, the App browser):
// set its category (without having to use it first), block it, put it in one of your groups, or find its .exe on disk.
// One menu.js instance; "Category ›" and "Add to group ›" open their list in the same menu with a "‹ Back" line.
//
//   openAppMenu(anchor, target, opts)
//     anchor   a MouseEvent / {x, y} (menu at the pointer) or an element (menu under it, e.g. a category chip)
//     target   {kind: "app" | "site", name: its .exe / host name, display?: shown name, path?: where the .exe is}
//     opts     onDone({type: "category" | "group", ...})   something was changed
//              notify(text)       short feedback (default: the toast; the App browser shows it in its own line)
//              restore()          called when a dialog this menu opened (New category, the editor, a challenge) is over and
//                                 nothing is open any more - the App browser uses it to open itself again
//              goBlocking(prefill) default: the router's Blocking > Add with the app / site filled in
//              start: "category"  open on the category list (a category chip); "‹ Back" leads to the whole menu
// What a change needs (the challenge while Anti-Bypass is locked) is decided in Python - this file only asks.
import { ld } from "./bridge.js";
import { openMenu } from "./menu.js";
import { isOpen, closeDialog } from "./dialog.js";
import { toast } from "./ui.js";
import { loadCategories, colorOf, assignCategory, assignNewCategory, promptNewCategory, openCategoryEditor, ensureCss, CANCELLED } from "./categories.js";

let seq = 0;      // a slow answer for an earlier right-click never opens over a later one

const where = (anchor) => (anchor && anchor.getBoundingClientRect ? anchor : { x: anchor.clientX ?? anchor.x ?? 0, y: anchor.clientY ?? anchor.y ?? 0 });

export async function openAppMenu(anchor, target, opts = {}) {
  ensureCss();
  const at = where(anchor);
  const my = ++seq;
  const kind = target.kind, name = target.name, display = target.display || name;
  let info, cats;
  try {
    [info, cats] = await Promise.all([ld.call("appmenu_info", kind, name), loadCategories()]);
  } catch (err) {
    (opts.notify || toast)(err.message || "That menu isn't available.");
    return;
  }
  if (my !== seq) return;
  const say = (text) => (opts.notify || toast)(text);
  const fail = (err) => say((err && err.message) || "Something went wrong.");
  // a dialog this menu opened (or a challenge) is over: the App browser opens itself again if nothing is open
  const after = (fn) => async (...args) => {
    try { await fn(...args); } catch (err) { fail(err); }
    if (opts.restore && !isOpen()) opts.restore();
  };
  const current = cats.find((c) => c.key === info.category) || cats[1] || cats[0];

  const pickCategory = after(async (c) => {
    const r = await assignCategory(kind, name, c.key);
    if (r === CANCELLED) return;
    say(`${display} is now ${c.name}`);
    if (opts.onDone) opts.onDone({ type: "category", category: c.key });
  });
  const newCategory = after(async () => {
    const v = await promptNewCategory();
    if (!v) return;
    const r = await assignNewCategory(kind, name, v);
    if (r === CANCELLED) return;
    say(`${display} is now ${v.name}`);
    if (opts.onDone) opts.onDone({ type: "category", category: r.category });
  });
  const editCategories = () => {
    const h = openCategoryEditor({ onClose: () => { if (opts.onDone) opts.onDone({ type: "categories" }); if (opts.restore && !isOpen()) opts.restore(); } });
    if (!h && opts.restore && !isOpen()) opts.restore();
  };
  const addToGroup = after(async (g) => {
    const r = await ld.call("draft_add_to_group", { kind, name, display, path: target.path || null }, g.id);
    say(r.notice);
    if (r.added && opts.onDone) opts.onDone({ type: "group", group: g.id });
  });
  const block = async () => {
    closeDialog();
    const prefill = { kind, name, display, path: target.path || null };
    if (opts.goBlocking) return opts.goBlocking(prefill);
    const { router } = await import("./router.js");
    router.go("blocking", { tab: "add", prefill });
  };
  const reveal = async () => {
    try { await ld.call("reveal_in_explorer", target.path); } catch (err) { fail(err); }
  };

  const back = { label: "‹ Back", icon: "", onClick: () => show(top()) };
  const categoryList = () => [
    { heading: "Category" },
    ...cats.map((c) => ({ label: c.name, swatch: colorOf(c), checked: c.key === info.category, onClick: () => pickCategory(c) })),
    { sep: true },
    { label: "New category…", icon: "plus", onClick: newCategory },
    { label: "Edit categories…", icon: "pencil", onClick: editCategories },
    { sep: true }, back,
  ];
  const groupList = () => [
    { heading: "Add to group" },
    ...info.groups.map((g) => ({ label: g.name, icon: "layers", onClick: () => addToGroup(g) })),
    { sep: true }, back,
  ];
  const top = () => {
    const items = [{ heading: display }, { label: `Category: ${current.name} ›`, swatch: colorOf(current), onClick: () => show(categoryList()) }, { sep: true }];
    if (info.protected) items.push({ label: "Part of Windows - can't be blocked", icon: "shield", disabled: true });
    else {
      items.push({ label: "Block it…", icon: "ban", onClick: block });
      items.push(info.groups.length
        ? { label: "Add to group ›", icon: "layers", onClick: () => show(groupList()) }
        : { label: "Add to group (you have none yet)", icon: "layers", disabled: true });
    }
    if (kind === "app" && target.path) items.push({ sep: true }, { label: "Show in Explorer", icon: "folder", onClick: reveal });
    return items;
  };
  const show = (items) => openMenu(at, items);
  show(opts.start === "category" ? categoryList() : top());
}
