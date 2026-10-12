// The corner window: reminder popups and in-app notices as a stack of cards (newest lowest).
// Python owns the list and pushes it whole (`corner` event, {cards}); this file only reconciles the DOM with it (keyed:
// a card that is still there is not rebuilt), times notices out (8 s, held while the mouse is over one), tells Python what
// was pressed (`corner_answer`) and how tall the stack is (`corner_size`, so the window can be cut to fit).
// Nothing runs while no card is up: the only timers are the notices' own and one animation-frame for the size report.
import { ld } from "./bridge.js";
import { symbol } from "./icons.js";
import { applyLook } from "./theme.js";

/** A popup's text as blocks: paragraphs, "• " lines grouped into one list, and "While you're up:"-style lead lines. */
export function bodyBlocks(text) {
  const blocks = [];
  let list = null;
  for (const raw of String(text == null ? "" : text).split("\n")) {
    const line = raw.trimEnd();
    if (line.startsWith("• ")) {
      if (!list) blocks.push((list = { type: "ul", items: [] }));
      list.items.push(line.slice(2));
      continue;
    }
    list = null;
    if (!line.trim()) continue;
    blocks.push({ type: /:$/.test(line) && line.length < 40 ? "lead" : "p", text: line });
  }
  return blocks;
}

/** Does the card on screen need rebuilding for this new version of it? */
export const sameCard = (a, b) => a.id === b.id;

const SVG = "http://www.w3.org/2000/svg";
function icon(name) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", "ic");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG, "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

function h(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

export function createCorner(env = {}) {
  const doc = env.document || document;
  const root = env.root || doc.getElementById("stack");
  const call = env.call || ((...a) => ld.call(...a));
  const setT = env.setTimeout || ((f, ms) => setTimeout(f, ms));
  const clearT = env.clearTimeout || ((t) => clearTimeout(t));
  const raf = env.raf || ((f) => requestAnimationFrame(f));

  const cards = new Map();        // key -> {data, el, timer, busy, leaving}
  const closed = new Map();       // key -> id of the card the user closed here (a stale push must not bring it back)
  let reported = -1;
  let seq = -1;                   // the newest list seen: an older one (corner_ready answering late) is ignored
  let framePending = false;

  // ------------------------------------------------------------ talking to Python
  function answer(card, action) {
    if (card.busy) return;
    card.busy = true;
    closed.set(card.data.key, card.data.id);
    leave(card);
    Promise.resolve(call("corner_answer", card.data.key, action)).catch(() => {});   // (gone already / refused: nothing to show)
  }

  function reportSize() {
    framePending = false;
    const height = root.childElementCount ? Math.ceil(root.getBoundingClientRect().height) : 0;
    if (height === reported) return;
    reported = height;
    if (height > 0) Promise.resolve(call("corner_size", height, window.devicePixelRatio || 1)).catch(() => {});
  }
  function sizeSoon() {
    if (framePending) return;
    framePending = true;
    raf(reportSize);
  }

  // ------------------------------------------------------------ building a card
  function body(data) {
    const box = h("div", "cc-body");
    for (const block of bodyBlocks(data.text)) {
      if (block.type === "ul") {
        const ul = h("ul");
        for (const item of block.items) ul.append(h("li", "", item));
        box.append(ul);
      } else {
        box.append(h("p", block.type === "lead" ? "lead" : "", block.text));
      }
    }
    return box;
  }

  function buttons(card) {
    const row = h("div", "cc-btns");
    for (const b of card.data.buttons) {
      const dismiss = b.style === "dismiss";
      const btn = h("button", "btn btn-sm " + (dismiss ? "btn-dismiss" : b.style === "primary" ? "btn-primary" : ""));
      btn.type = "button";
      btn.dataset.action = b.action;
      if (dismiss) {
        btn.setAttribute("aria-label", "Dismiss");
        btn.title = "Dismiss";
        btn.append(icon("x"));
      } else {
        btn.textContent = b.label;
      }
      btn.addEventListener("click", (e) => { e.stopPropagation(); answer(card, b.action); });
      row.append(btn);
    }
    return row;
  }

  function popupEl(card) {
    const el = h("section", "cc cc-popup");
    const head = h("div", "cc-h");
    head.append(icon("alarm"), h("b", "", card.data.title));
    el.append(head, body(card.data));
    if (card.data.buttons.length) el.append(buttons(card));
    return el;
  }

  function noticeEl(card) {
    const el = h("section", "cc cc-notice" + (card.data.sticky ? " sticky" : ""));
    const head = h("div", "cc-h");
    const x = h("button", "cc-close");
    x.type = "button";
    x.setAttribute("aria-label", "Close");
    x.append(icon("x"));
    x.addEventListener("click", (e) => { e.stopPropagation(); answer(card, "close"); });
    head.append(icon("shield"), h("b", "", "Lockdown"), h("span", "cc-when", "now"), x);
    el.append(head, h("p", "cc-msg", card.data.text));
    if (card.data.buttons.length) el.append(buttons(card));
    if (!card.data.sticky) {
      el.addEventListener("click", () => answer(card, "close"));
      el.addEventListener("mouseenter", () => { clearT(card.timer); card.timer = 0; });
      el.addEventListener("mouseleave", () => arm(card));
      arm(card);
    }
    return el;
  }

  function arm(card) {
    clearT(card.timer);
    card.timer = setT(() => { if (!card.leaving) answer(card, "close"); }, card.data.ms || 8000);
  }

  function make(data) {
    const card = { data, el: null, timer: 0, busy: false, leaving: false };
    card.el = data.kind === "notice" ? noticeEl(card) : popupEl(card);
    card.el.dataset.key = data.key;
    card.el.classList.add("enter");
    card.el.addEventListener("animationend", () => card.el.classList.remove("enter"), { once: true });
    return card;
  }

  function leave(card) {
    if (card.leaving) return;
    card.leaving = true;
    clearT(card.timer);
    card.el.classList.add("leaving");
    const finish = () => {
      if (cards.get(card.data.key) === card) cards.delete(card.data.key);
      card.el.remove();
      sizeSoon();
    };
    card.el.addEventListener("transitionend", finish, { once: true });
    setT(finish, 200);          // (reduced motion / no transition: still gone)
  }

  // ------------------------------------------------------------ the list from Python
  function render(payload) {
    if (payload && typeof payload.seq === "number") {
      if (payload.seq < seq) return;
      seq = payload.seq;
    }
    const list = (payload && payload.cards) || [];
    const keys = new Set(list.map((c) => c.key));
    for (const key of [...closed.keys()]) if (!keys.has(key)) closed.delete(key);
    if (!list.length) {           // nothing left: Python hides the window, so there is nothing to animate (and a hidden page may not run it)
      for (const card of cards.values()) { clearT(card.timer); card.el.remove(); }
      cards.clear();
      sizeSoon();
      return;
    }
    for (const [key, card] of cards) if (!keys.has(key) && !card.leaving) leave(card);
    let prev = null;
    for (const data of list) {
      if (closed.get(data.key) === data.id) continue;
      let card = cards.get(data.key);
      if (card && (card.leaving || !sameCard(card.data, data))) {
        clearT(card.timer);                // a new version under the same key replaces it (a leaving one goes at once)
        card.el.remove();
        card = null;
      }
      if (!card) {
        card = make(data);
        cards.set(data.key, card);
      }
      const want = prev ? prev.el.nextSibling : root.firstChild;
      if (card.el.parentNode !== root || card.el !== want) root.insertBefore(card.el, want);
      prev = card;
    }
    sizeSoon();
  }

  return { render, cards, closed, sizeSoon, answer };
}

// ---------------------------------------------------------------- boot (only in the real page)
if (typeof document !== "undefined" && document.getElementById("stack")) {
  document.getElementById("sprite").innerHTML = ["alarm", "shield", "x"].map(symbol).join("");   // (the three icons used here)
  const corner = createCorner();
  const lookNow = () => ld.call("get_theme").then((look) => applyLook(look)).catch(() => {});
  ld.on("corner", (payload) => {
    const wasEmpty = !corner.cards.size;
    corner.render(payload);
    if (wasEmpty) lookNow();          // the look may have changed since the last card (cosmetic, after the paint)
  });
  ld.call("corner_ready").then((state) => {
    if (state.look) applyLook(state.look);
    corner.render(state);
    return ld.call("page_ready", "corner");    // only now may Python show the window: the cards are drawn
  }).catch(() => {});
  window.addEventListener("resize", corner.sizeSoon);
  document.documentElement.dataset.ready = "1";
}
