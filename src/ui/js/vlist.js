// Virtual list with keyed row reuse: only the rows in view (plus a little overscan) exist as DOM nodes. Scrolling
// moves the same nodes (transform) and rewrites only the rows that are new to the window - never innerHTML for all rows.
// Fixed row height (a CSS variable such as --row-h, so it follows the interface size). No timers: scroll, resize and
// size-change events only.
import { px } from "./ui.js";

/** The rows [first, end) to have as nodes for a scroll position. Pure - tested in node. */
export function visibleRange(scrollTop, viewH, rowH, count, overscan = 6) {
  if (!(count > 0) || !(rowH > 0) || !(viewH > 0)) return [0, 0];
  const first = Math.min(count, Math.max(0, Math.floor(scrollTop / rowH) - overscan));
  const end = Math.min(count, Math.ceil((scrollTop + viewH) / rowH) + overscan);
  return [first, Math.max(first, end)];
}

/**
 * createVList(container, {rowVar | rowHeight, rows, key(row, i), create(), render(node, row, i), overscan, rowClass})
 *   render(node, row, i) must set the row's text / attributes from `row` and be safe to call again on the same node.
 *   key  gives a stable id per row (default: its index); a row that keeps its key keeps its node while you scroll.
 * Returns {setRows(rows, {keepScroll}), refresh(), scrollToIndex(i), measure(), destroy(), rows, liveCount, rowHeight}.
 */
export function createVList(container, opts) {
  const { render, key = (_row, i) => i, overscan = 6, rowClass = "row" } = opts;
  const create = opts.create || (() => { const d = document.createElement("div"); d.className = rowClass; return d; });
  let rows = opts.rows || [];
  let rowH = opts.rowHeight || px(opts.rowVar || "--row-h");
  const live = new Map();       // key -> node (rows in the window)
  const pool = [];              // hidden nodes waiting to be reused
  let raf = 0, a = -1, b = -1, dead = false;

  container.classList.add("vlist");
  const spacer = document.createElement("div");
  spacer.className = "vl-spacer";
  container.append(spacer);

  function place(node, i) {
    if (node._i !== i) {
      node._i = i;
      node.style.transform = `translateY(${i * rowH}px)`;
    }
  }

  function sync(force) {
    raf = 0;
    if (dead) return;
    const [first, end] = visibleRange(container.scrollTop, container.clientHeight, rowH, rows.length, overscan);
    if (!force && first === a && end === b) return;
    a = first; b = end;
    const wanted = new Map();
    for (let i = first; i < end; i++) wanted.set(key(rows[i], i), i);
    for (const [k, node] of live) {
      if (!wanted.has(k)) {
        live.delete(k);
        node.hidden = true;
        pool.push(node);
      }
    }
    for (const [k, i] of wanted) {
      let node = live.get(k);
      if (!node) {
        node = pool.pop();
        if (node) node.hidden = false;
        else { node = create(); node.classList.add("vl-row"); node.style.height = rowH + "px"; container.append(node); }
        live.set(k, node);
        node._i = -1;
        render(node, rows[i], i);
      } else if (force) {
        render(node, rows[i], i);
      }
      place(node, i);
    }
  }

  function schedule() {
    if (!raf && !dead) raf = requestAnimationFrame(() => sync(false));
  }

  function relayout() {
    spacer.style.height = rows.length * rowH + "px";
    for (const node of [...live.values(), ...pool]) { node._i = -1; node.style.height = rowH + "px"; }
    sync(true);
  }

  const onScroll = () => schedule();
  container.addEventListener("scroll", onScroll, { passive: true });
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => { if (!dead) sync(false); }) : null;
  if (ro) ro.observe(container);
  const onScale = () => { rowH = opts.rowHeight || px(opts.rowVar || "--row-h"); relayout(); };
  window.addEventListener("ld:scale", onScale);

  const api = {
    get rows() { return rows; },
    get liveCount() { return live.size; },
    get rowHeight() { return rowH; },
    /** Replace the data. Rows keeping their key keep their node (and are rendered again with the new data). */
    setRows(next, { keepScroll = false } = {}) {
      rows = next;
      if (!keepScroll) container.scrollTop = 0;
      a = b = -1;
      spacer.style.height = rows.length * rowH + "px";
      sync(true);
    },
    /** Render the rows in view again (their data changed in place). */
    refresh() { sync(true); },
    scrollToIndex(i) { container.scrollTop = Math.max(0, Math.min(rows.length - 1, i)) * rowH; schedule(); },
    /** Measure the row height again (after the interface size changed). */
    measure: onScale,
    destroy() {
      dead = true;
      container.removeEventListener("scroll", onScroll);
      window.removeEventListener("ld:scale", onScale);
      if (ro) ro.disconnect();
      for (const node of [...live.values(), ...pool]) node.remove();
      live.clear(); pool.length = 0;
      spacer.remove();
    },
  };
  spacer.style.height = rows.length * rowH + "px";
  sync(true);
  return api;
}
