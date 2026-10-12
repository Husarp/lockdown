// Group icons and colours: what a group can wear (the phone's GroupLook, with more to choose from) and the picker.
//   SECTIONS / KEYS     the pickable icons: [key, sprite symbol, label] in the picker's sections - the SAME keys as
//                       src/group_look.py (Python checks what the page sends against its list; a test keeps them equal)
//   HUES                the pickable colours (CSS tokens --hue-<name> / classes .hue-<name> in css/tokens.css)
//   chipHtml / paint    a group's coloured icon chip (the `.kic` look of the kind icons, tinted by the group's hue)
//   autoIcon            the icon a NAME suggests (a copy of group_look.auto_icon for the editor's live preview only -
//                       Python decides what is shown after Save)
//   openLookPicker      the dialog behind the editor's icon button
// The symbols that are not in the boot sprite (js/icons.js) are added to the page's sprite when this module is first
// imported, so only the pages that show groups pay for them. Icons: Lucide (ISC licence), 24x24, stroke 1.75 like icons.js.
import { openDialog } from "./dialog.js";
import { injectSprite, PATHS } from "./icons.js";
import { el, esc, ic } from "./ui.js";

const EXTRA = {
  'monitor-play': '<path d="M10 7.75a.75.75 0 0 1 1.142-.638l3.664 2.249a.75.75 0 0 1 0 1.278l-3.664 2.25a.75.75 0 0 1-1.142-.64z"/><path d="M12 17v4"/><path d="M8 21h8"/><rect x="2" y="3" width="20" height="14" rx="2"/>',
  newspaper: '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8V6Z"/>',
  banknote: '<rect width="20" height="12" x="2" y="6" rx="2"/><circle cx="12" cy="12" r="2"/><path d="M6 12h.01M18 12h.01"/>',
  dices: '<rect width="12" height="12" x="2" y="10" rx="2" ry="2"/><path d="m17.92 14 3.5-3.5a2.24 2.24 0 0 0 0-3l-5-4.92a2.24 2.24 0 0 0-3 0L10 6"/><path d="M6 18h.01"/><path d="M10 14h.01"/><path d="M15 6h.01"/><path d="M18 9h.01"/>',
  utensils: '<path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2"/><path d="M7 2v20"/><path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3Zm0 0v7"/>',
  'graduation-cap': '<path d="M21.42 10.922a1 1 0 0 0-.019-1.838L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.832l8.57 3.908a2 2 0 0 0 1.66 0z"/><path d="M22 10v6"/><path d="M6 12.5V16a6 3 0 0 0 12 0v-3.5"/>',
  brain: '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/><path d="M17.599 6.5a3 3 0 0 0 .399-1.375"/><path d="M6.003 5.125A3 3 0 0 0 6.401 6.5"/><path d="M3.477 10.896a4 4 0 0 1 .585-.396"/><path d="M19.938 10.5a4 4 0 0 1 .585.396"/><path d="M6 18a4 4 0 0 1-1.967-.516"/><path d="M19.967 17.484A4 4 0 0 1 18 18"/>',
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
  bed: '<path d="M2 4v16"/><path d="M2 8h18a2 2 0 0 1 2 2v10"/><path d="M2 17h20"/><path d="M6 8v9"/>',
  sunrise: '<path d="M12 2v8"/><path d="m4.93 10.93 1.41 1.41"/><path d="M2 18h2"/><path d="M20 18h2"/><path d="m19.07 10.93-1.41 1.41"/><path d="M22 22H2"/><path d="m8 6 4-4 4 4"/><path d="M16 18a4 4 0 0 0-8 0"/>',
  house: '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  baby: '<path d="M9 12h.01"/><path d="M15 12h.01"/><path d="M10 16c.5.3 1.2.5 2 .5s1.5-.2 2-.5"/><path d="M19 6.3a9 9 0 0 1 1.8 3.9 2 2 0 0 1 0 3.6 9 9 0 0 1-17.6 0 2 2 0 0 1 0-3.6A9 9 0 0 1 12 3c2 0 3.5 1.1 3.5 2.5s-.9 2.5-2 2.5c-.8 0-1.5-.4-1.5-1"/>',
  'paw-print': '<circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="20" cy="16" r="2"/><path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.045Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10Z"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/>',
  headphones: '<path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/>',
  mail: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
  sparkles: '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>',
  'building-2': '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/><path d="M10 6h4"/><path d="M10 10h4"/><path d="M10 14h4"/><path d="M10 18h4"/>',
  laptop: '<path d="M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16"/>',
  'pen-line': '<path d="M12 20h9"/><path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z"/>',
  library: '<path d="m16 6 4 14"/><path d="M12 6v14"/><path d="M8 8v12"/><path d="M4 4v16"/>',
  lightbulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
  'shopping-bag': '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>',
  'credit-card': '<rect width="20" height="14" x="2" y="5" rx="2"/><line x1="2" x2="22" y1="10" y2="10"/>',
  gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5"/>',
  footprints: '<path d="M4 16v-2.38C4 11.5 2.97 10.5 3 8c.03-2.72 1.49-6 4.5-6C9.37 2 10 3.8 10 5.5c0 3.11-2 5.66-2 8.68V16a2 2 0 1 1-4 0Z"/><path d="M20 20v-2.38c0-2.12 1.03-3.12 1-5.62-.03-2.72-1.49-6-4.5-6C14.63 6 14 7.8 14 9.5c0 3.11 2 5.66 2 8.68V20a2 2 0 1 0 4 0Z"/><path d="M16 17h4"/><path d="M4 13h4"/>',
  pill: '<path d="m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z"/><path d="m8.5 8.5 7 7"/>',
  'heart-pulse': '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/><path d="M3.22 12H9.5l.5-1 2 4.5 2-7 1.5 3.5h5.27"/>',
  'flower-2': '<path d="M12 5a3 3 0 1 1 3 3m-3-3a3 3 0 1 0-3 3m3-3v1M9 8a3 3 0 1 0 3 3M9 8h1m5 0a3 3 0 1 1-3 3m3-3h-1m-2 3v-1"/><circle cx="12" cy="8" r="2"/><path d="M12 10v12"/><path d="M12 22c4.2 0 7-1.667 7-5-4.2 0-7 1.667-7 5Z"/><path d="M12 22c-4.2 0-7-1.667-7-5 4.2 0 7 1.667 7 5Z"/>',
  mountain: '<path d="m8 3 4 8 5-5 5 15H2L8 3z"/>',
  'tree-pine': '<path d="m17 14 3 3.3a1 1 0 0 1-.7 1.7H4.7a1 1 0 0 1-.7-1.7L7 14h-.3a1 1 0 0 1-.7-1.7L9 9h-.2A1 1 0 0 1 8 7.3L12 3l4 4.3a1 1 0 0 1-.8 1.7H15l3 3.3a1 1 0 0 1-.7 1.7H17Z"/><path d="M12 22v-3"/>',
  car: '<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>',
  bike: '<circle cx="18.5" cy="17.5" r="3.5"/><circle cx="5.5" cy="17.5" r="3.5"/><circle cx="15" cy="5" r="1"/><path d="M12 17.5V14l-3-3 4-3 2 3h2"/>',
  plane: '<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/>',
  map: '<path d="M14.106 5.553a2 2 0 0 0 1.788 0l3.659-1.83A1 1 0 0 1 21 4.619v12.764a1 1 0 0 1-.553.894l-4.553 2.277a2 2 0 0 1-1.788 0l-4.212-2.106a2 2 0 0 0-1.788 0l-3.659 1.83A1 1 0 0 1 3 19.381V6.618a1 1 0 0 1 .553-.894l4.553-2.277a2 2 0 0 1 1.788 0z"/><path d="M15 5.764v15"/><path d="M9 3.236v15"/>'
};
Object.assign(PATHS, EXTRA);
if (typeof document !== "undefined" && document.body) injectSprite();

// [section, [[key, sprite symbol, label], ...]]
export const SECTIONS = [
  ["Everyday", [
    ["moon", "moon", "Night"], ["bed", "bed", "Sleep"], ["sunrise", "sunrise", "Morning"], ["alarm", "alarm", "Alarm"],
    ["calendar", "calendar", "Calendar"], ["home", "house", "Home"], ["family", "baby", "Family"], ["pets", "paw-print", "Pets"],
    ["coffee", "coffee", "Coffee"], ["food", "utensils", "Food"]]],
  ["Apps and media", [
    ["phone", "phone", "Phone"], ["game", "gamepad", "Games"], ["social", "users", "Social"], ["video", "monitor-play", "Video"],
    ["chat", "message", "Chat"], ["music", "music", "Music"], ["news", "newspaper", "News"], ["web", "globe", "Web"],
    ["photo", "camera", "Photos"], ["film", "film", "Films"], ["tv", "tv", "TV"], ["mic", "mic", "Podcasts"],
    ["headphones", "headphones", "Listening"], ["mail", "mail", "Mail"]]],
  ["Games and fun", [
    ["casino", "dices", "Casino"], ["puzzle", "puzzle", "Puzzles"], ["trophy", "trophy", "Trophy"], ["art", "palette", "Art"]]],
  ["Work and study", [
    ["work", "briefcase", "Work"], ["office", "building-2", "Office"], ["laptop", "laptop", "Laptop"], ["code", "code", "Coding"],
    ["pen", "pen-line", "Writing"], ["school", "graduation-cap", "School"], ["library", "library", "Library"],
    ["book", "book", "Books"], ["idea", "lightbulb", "Ideas"], ["focus", "brain", "Focus"], ["target", "target", "Goals"],
    ["timer", "timer", "Timer"]]],
  ["Money and shopping", [
    ["shopping", "cart", "Shopping"], ["bag", "shopping-bag", "Bag"], ["money", "banknote", "Money"],
    ["card", "credit-card", "Card"], ["gift", "gift", "Gifts"]]],
  ["Health and outdoors", [
    ["sport", "dumbbell", "Sport"], ["run", "footprints", "Running"], ["pill", "pill", "Health"], ["pulse", "heart-pulse", "Pulse"],
    ["lotus", "flower-2", "Calm"], ["heart", "heart", "Heart"], ["mountain", "mountain", "Mountains"], ["tree", "tree-pine", "Trees"]]],
  ["Travel", [
    ["car", "car", "Driving"], ["bike", "bike", "Cycling"], ["plane", "plane", "Travel"], ["map", "map", "Maps"]]],
  ["More", [
    ["star", "star", "Star"], ["bolt", "zap", "Energy"], ["lock", "lock", "Lock"], ["shield", "shield", "Shield"],
    ["sparkles", "sparkles", "Sparkles"], ["folder", "folder", "Folder"]]],
];
export const KEYS = SECTIONS.flatMap(([, list]) => list.map(([key]) => key));
const BY_KEY = new Map(SECTIONS.flatMap(([, list]) => list.map(([key, sprite, label]) => [key, { sprite, label }])));

export const HUES = [["red", "Red"], ["orange", "Orange"], ["amber", "Amber"], ["green", "Green"], ["teal", "Teal"],
  ["blue", "Blue"], ["indigo", "Indigo"], ["violet", "Violet"], ["pink", "Pink"], ["slate", "Slate"]];
const HUE_LABEL = new Map(HUES);
const AUTO_HUE = "orange";                // (only if Python sends nothing usable)

/** The sprite symbol of an icon key (a folder for a key this copy doesn't know). */
export const symbolOf = (key) => (BY_KEY.get(key) || BY_KEY.get("folder")).sprite;
export const iconLabel = (key) => (BY_KEY.get(key) || BY_KEY.get("folder")).label;
export const hueOf = (color) => (HUE_LABEL.has(color) ? color : AUTO_HUE);
export const hueLabel = (color) => HUE_LABEL.get(hueOf(color));

// Name words (English and Polish stems) -> icon, first row that matches wins - src/group_look.py WORDS, the same table.
// A stem under 4 letters (and WHOLE) only matches the whole word: "bet" isn't "Better", "gra" isn't "Grafika".
const WORDS = [
  ["moon", ["night", "bed", "bedtime", "sleep", "evening", "noc", "nocn", "sen"]],
  ["game", ["game", "gaming", "play", "gry", "gra"]],
  ["social", ["social", "insta", "tiktok", "facebook", "snap", "społ"]],
  ["video", ["video", "youtube", "netflix", "tv", "film", "movie", "stream", "wideo"]],
  ["chat", ["chat", "messag", "whatsapp", "discord", "czat"]],
  ["music", ["music", "spotify", "muzyk"]],
  ["news", ["news", "wiadomo"]],
  ["shopping", ["shop", "amazon", "allegro", "zakup"]],
  ["casino", ["bet", "betting", "casino", "gambl", "hazard"]],
  ["work", ["work", "praca"]],
  ["school", ["study", "school", "learn", "nauka", "szkoł"]],
  ["book", ["read", "book"]],
];
const WHOLE = new Set(["read"]);

/** The icon a name suggests (display only - see the header). */
export function autoIcon(name) {
  const words = String(name || "").toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const hit = (w, stem) => (stem.length < 4 || WHOLE.has(stem) ? w === stem : w.startsWith(stem));
  const row = WORDS.find(([, stems]) => words.some((w) => stems.some((s) => hit(w, s))));
  return row ? row[0] : "folder";
}

/** A group's coloured icon chip. `look`: {icon, color} as Python sends them (picked or automatic). */
export const chipHtml = (look, cls = "") => `<span class="kic hue-${hueOf(look.color)} ${cls}">${ic(symbolOf(look.icon))}</span>`;

/** Give `node` the class of this colour (and none of another hue) - only what changed. `color` null: no hue at all. */
export function setHue(node, color) {
  const hue = color ? `hue-${hueOf(color)}` : "";
  for (const c of [...node.classList]) if (c.startsWith("hue-") && c !== hue) node.classList.remove(c);
  if (hue && !node.classList.contains(hue)) node.classList.add(hue);
}

/** Repaint a chip made by chipHtml (or any element holding the group's one `<use>`) for a new look - only what changed. */
export function paint(node, look) {
  setHue(node, look.color);
  const use = node.querySelector("use");
  const href = `#i-${symbolOf(look.icon)}`;
  if (use && use.getAttribute("href") !== href) use.setAttribute("href", href);
}

// ---------------------------------------------------------------- the picker

const PICK_STYLE = `.gl-pick { display: grid; gap: var(--sp-4); }
.gl-prev { display: flex; align-items: center; gap: var(--sp-3); }
.gl-prev .kic { width: calc(44px * var(--s)); height: calc(44px * var(--s)); border-radius: var(--r-lg); }
.gl-prev .kic .ic { width: calc(24px * var(--s)); height: calc(24px * var(--s)); }
.gl-prev p { margin: 0; display: grid; }
.gl-line { display: flex; align-items: center; gap: var(--sp-3); }
.gl-cap { margin: 0; flex: none; width: calc(48px * var(--s)); font-size: var(--fs-sm); font-weight: var(--fw-semibold); color: var(--text-2); }
.gl-hues { display: flex; flex-wrap: wrap; gap: var(--sp-2); align-items: center; }
.gl-hue { width: calc(28px * var(--s)); height: calc(28px * var(--s)); border-radius: 50%; padding: 0; border: 0; background: var(--k); cursor: pointer; }
.gl-hue[aria-checked="true"] { box-shadow: 0 0 0 2px var(--surface), 0 0 0 4px var(--text); }
.gl-sec { margin: var(--sp-2) 0 var(--sp-1); font-size: var(--fs-sm); font-weight: var(--fw-semibold); color: var(--text-3); }
.gl-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(calc(38px * var(--s)), 1fr)); gap: var(--sp-1); }
.gl-ic { aspect-ratio: 1; display: grid; place-items: center; padding: 0; border: 1px solid var(--line); border-radius: var(--r-md);
  background: var(--surface); color: var(--text-2); cursor: pointer; }
.gl-ic:hover { background: var(--surface-2); }
.gl-ic .ic { width: calc(20px * var(--s)); height: calc(20px * var(--s)); }
.gl-ic[aria-checked="true"] { border-color: var(--k); color: var(--k); background: var(--k-soft); box-shadow: inset 0 0 0 1px var(--k); }
.gl-auto { height: var(--control-h-sm); padding: 0 var(--sp-3); border: 1px solid var(--line); border-radius: var(--r-pill); background: var(--surface);
  color: var(--text-2); font: inherit; font-size: var(--fs-sm); cursor: pointer; }
.gl-auto[aria-checked="true"] { border-color: var(--brand); color: var(--brand); box-shadow: inset 0 0 0 1px var(--brand); }
.gl-pick button:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }`;

/** Arrow keys walk a group of radio buttons (the one picked is the tab stop); the walk picks, like any radio group. */
function roving(group) {
  const radios = () => [...group.querySelectorAll('[role="radio"]')];
  const sync = () => { const all = radios(), cur = all.find((b) => b.getAttribute("aria-checked") === "true") || all[0]; all.forEach((b) => { b.tabIndex = b === cur ? 0 : -1; }); };
  group.addEventListener("keydown", (e) => {
    const all = radios(), i = all.indexOf(document.activeElement);
    if (i < 0 || e.altKey || e.ctrlKey || e.metaKey) return;
    let next = null;
    if (e.key === "ArrowRight") next = all[(i + 1) % all.length];
    else if (e.key === "ArrowLeft") next = all[(i - 1 + all.length) % all.length];
    else if (e.key === "Home") next = all[0];
    else if (e.key === "End") next = all[all.length - 1];
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const here = all[i].getBoundingClientRect(), down = e.key === "ArrowDown";
      const rows = all.map((b) => ({ b, r: b.getBoundingClientRect() })).filter(({ r }) => (down ? r.top > here.bottom - 2 : r.bottom < here.top + 2));
      if (rows.length) {
        const row = down ? Math.min(...rows.map(({ r }) => r.top)) : Math.max(...rows.map(({ r }) => r.top));
        const x = here.left + here.width / 2;
        next = rows.filter(({ r }) => r.top === row).sort((a, b) => Math.abs(a.r.left + a.r.width / 2 - x) - Math.abs(b.r.left + b.r.width / 2 - x))[0].b;
      }
    } else return;
    e.preventDefault();
    if (next) { next.focus(); next.click(); }
  });
  group.addEventListener("click", () => queueMicrotask(sync));
  sync();
}

/**
 * The icon-and-colour dialog of the group editor. `look` {icon, color} is what the group has picked (null = automatic);
 * `name` () => the name typed so far and `autoColor` the colour used while none is picked (both for the preview). Every
 * choice goes to `onChange({icon, color})` at once; "Done", the X and Esc just close it.
 */
export function openLookPicker({ look, name, autoColor, onChange }) {
  if (!document.getElementById("gl-style")) document.head.insertAdjacentHTML("beforeend", `<style id="gl-style">${PICK_STYLE}</style>`);
  const cur = { icon: look.icon || null, color: look.color || null };
  const shown = () => ({ icon: cur.icon || autoIcon(name()), color: cur.color || autoColor });
  const body = document.createElement("div");
  body.className = "gl-pick";
  body.innerHTML = `<div class="gl-prev"><span data-n="chip"></span><p><b data-n="title"></b><span class="small muted" data-n="sub"></span></p></div>
    <div class="gl-line"><p class="gl-cap" id="gl-cap-c">Colour</p><div class="gl-hues" role="radiogroup" aria-labelledby="gl-cap-c" data-n="hues">
      ${HUES.map(([c, label]) => `<button class="gl-hue hue-${c}" type="button" role="radio" data-hue="${c}" aria-label="${esc(label)}" title="${esc(label)}"></button>`).join("")}
      <button class="gl-auto" type="button" role="radio" data-hue="" title="Lockdown chooses a colour for the group">Automatic</button></div></div>
    <div role="radiogroup" aria-labelledby="gl-cap-i" data-n="icons">
      <div class="gl-line"><p class="gl-cap" id="gl-cap-i">Icon</p><button class="gl-auto" type="button" role="radio" data-key="" title="Lockdown picks the icon from the group's name">Automatic</button></div>
      ${SECTIONS.map(([title, list]) => `<p class="gl-sec">${esc(title)}</p><div class="gl-grid">${list.map(([key, sprite, label]) =>
        `<button class="gl-ic" type="button" role="radio" data-key="${key}" aria-label="${esc(label)}" title="${esc(label)}">${ic(sprite)}</button>`).join("")}</div>`).join("")}</div>`;
  const n = (k) => body.querySelector(`[data-n="${k}"]`);
  const chip = el(chipHtml(shown()));
  n("chip").replaceWith(chip);
  const hues = n("hues"), icons = n("icons");
  const refresh = () => {
    const now = shown();
    paint(chip, now);
    setHue(body, now.color);                                       // (the picked icon's tint follows the colour)
    n("title").textContent = `${iconLabel(now.icon)}${cur.icon ? "" : " · from the name"}`;
    n("sub").textContent = `${hueLabel(now.color)}${cur.color ? "" : " · automatic"}`;
    hues.querySelectorAll("[data-hue]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.hue === (cur.color || ""))));
    icons.querySelectorAll("[data-key]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.key === (cur.icon || ""))));
  };
  body.addEventListener("click", (e) => {
    const b = e.target.closest("[data-hue], [data-key]");
    if (!b) return;
    if ("hue" in b.dataset) cur.color = b.dataset.hue || null; else cur.icon = b.dataset.key || null;
    refresh();
    onChange({ icon: cur.icon, color: cur.color });
  });
  roving(hues);
  roving(icons);
  refresh();
  const handle = openDialog({ key: "group-look", title: "Icon and colour", body, wide: true, buttons: [{ label: "Done", kind: "primary" }] });
  const picked = icons.querySelector('[aria-checked="true"]');
  if (handle && picked) picked.focus();
  return handle;
}
