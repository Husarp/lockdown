// Lockdown charts: small SVG charts drawn from PRE-AGGREGATED arrays (Python does the summing). No library.
//   barChart(el)       bars per day, today highlighted, over-goal tone, dashed goal line, emergency-unlock marks
//   trendChart(el)     area + line trend, hour axis + gridlines, day / week-start lines, dashed average, goal, unlock rings, a dot that follows the pointer
//   timelineChart(el)  one day as a coloured bar (category segments + idle) with hour labels
//   heatmapChart(el)   days x 24 hours, 5 levels
//   donutChart(el)     ring split into parts with the total in the middle
//   hourBarChart(el)   count per hour, the peak and the next two highlighted
// Each returns {svg, update(data), relayout(), destroy()}. A chart is at most ~200 SVG elements; colours come from CSS
// variables (theme-safe: classes in one <style> this file adds once); ONE tooltip <div> is shared by every chart.
// `update(data)` with the same SHAPE (same element kinds in the same order) only rewrites changed attributes - no
// nodes are created or removed - so a data tick costs a handful of setAttribute calls.
// The layout functions (layoutBars ...) are pure: data + size in, a list of drawing primitives out (tested in node).
// Data shapes are described above each layout function. Times are seconds unless a field says otherwise.

const NS = "http://www.w3.org/2000/svg";
const n2 = (v) => Math.round(v * 100) / 100;
const MAX_SEGMENTS = 120;
const HEAT_LEVELS = [1, 15, 30, 45];   // active minutes in an hour: below 1 = level 0, then 1..4

const CSS = `
.ch{display:block;width:100%;overflow:visible;font-family:var(--font)}
.ch text{font-size:var(--fs-cap);fill:var(--text-2);font-variant-numeric:tabular-nums}
.ch .ch-axis{stroke:var(--line);stroke-width:1}
.ch .ch-grid{stroke:var(--line);stroke-width:1}
.ch .ch-day{stroke:var(--line);stroke-width:1;stroke-opacity:.6}
.ch .ch-week{stroke:var(--line-strong);stroke-width:1.5}
.ch .ch-ylab{fill:var(--text-3)}
.ch .ch-bar{fill:var(--cat-neutral)}
.ch .ch-bar.over{fill:var(--k-limit)}
.ch .ch-bar.now{fill:var(--brand)}
.ch .ch-bar.block{fill:var(--k-block)}
.ch .ch-goal{stroke:var(--text-3);stroke-width:1.5;stroke-dasharray:4 3}
.ch .ch-mark{fill:var(--k-mode)}
.ch .ch-ring{fill:var(--surface);stroke:var(--k-mode);stroke-width:1.5}
.ch .ch-area{fill:color-mix(in srgb,var(--brand) 14%,transparent)}
.ch .ch-line{fill:none;stroke:var(--brand);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.ch .ch-avg{fill:none;stroke:var(--text-3);stroke-width:1.4;stroke-dasharray:4 3}
.ch .ch-dot{fill:var(--brand);stroke:var(--surface);stroke-width:2}
.ch .ch-hit{fill:rgba(0,0,0,0)}
.ch .ch-gl{paint-order:stroke;stroke:var(--surface);stroke-width:3px;stroke-linejoin:round}
.ch .ch-trk{fill:var(--cat-idle)}
.ch .ch-h0{fill:var(--surface-3)}
.ch .ch-h1{fill:color-mix(in srgb,var(--brand) 25%,var(--surface-3))}
.ch .ch-h2{fill:color-mix(in srgb,var(--brand) 50%,var(--surface-3))}
.ch .ch-h3{fill:color-mix(in srgb,var(--brand) 75%,var(--surface-3))}
.ch .ch-h4{fill:var(--brand)}
.ch .ch-ringtrack{fill:none;stroke:var(--surface-3)}
.ch .ch-big{font-size:var(--fs-lg);font-weight:var(--fw-semibold);fill:var(--text)}
.ch-tip{position:fixed;z-index:1000;left:0;top:0;pointer-events:none;background:var(--surface-2);color:var(--text);
  border:1px solid var(--line);border-radius:var(--r-md);padding:6px 8px;font-size:var(--fs-sm);line-height:1.35;
  white-space:pre-line;box-shadow:var(--shadow-pop)}
.ch-tip[hidden]{display:none}
`;

let uid = 0;

// ---------------------------------------------------------------- the pieces every chart shares

/** A drawing primitive: tag + attributes (+ text, + `in`: index of the earlier primitive that contains it). */
const P = (tag, a, extra) => ({ tag, a, ...extra });

function ensureStyle(doc) {
  if (doc.getElementById("ld-chart-css")) return;
  const style = doc.createElement("style");
  style.setAttribute("id", "ld-chart-css");
  style.textContent = CSS;
  (doc.head || doc.documentElement).appendChild(style);
}

/** The one tooltip of the document. */
function tooltipOf(doc) {
  if (!doc._ldTip) {
    const tip = doc.createElement("div");
    tip.className = "ch-tip";
    tip.setAttribute("role", "tooltip");
    tip.hidden = true;
    doc.body.appendChild(tip);
    doc._ldTip = tip;
  }
  return doc._ldTip;
}

function showTip(doc, text, x, y) {
  const tip = tooltipOf(doc);
  if (tip.textContent !== text) tip.textContent = text;
  tip.hidden = false;
  const box = tip.getBoundingClientRect ? tip.getBoundingClientRect() : { width: 0, height: 0 };
  const view = doc.defaultView || {};
  const left = Math.max(4, Math.min(x + 14, (view.innerWidth || 1e9) - box.width - 4));
  const top = y - box.height - 10 > 4 ? y - box.height - 10 : y + 18;
  tip.style.transform = `translate(${Math.round(left)}px,${Math.round(top)}px)`;
}

function hideTip(doc) {
  if (doc._ldTip) doc._ldTip.hidden = true;
}

/** Make `svg` show `prims`: rebuild when the element kinds/order changed, otherwise only rewrite what differs. */
function patch(doc, svg, store, prims) {
  const sig = prims.map((p) => p.tag + (p.in === undefined ? "" : "<" + p.in)).join(",");
  if (sig !== store.sig) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    store.nodes = prims.map((p) => {
      const node = doc.createElementNS(NS, p.tag);
      node._a = {};
      node._t = undefined;
      return node;
    });
    prims.forEach((p, i) => (p.in === undefined ? svg : store.nodes[p.in]).appendChild(store.nodes[i]));
    store.sig = sig;
  }
  prims.forEach((p, i) => {
    const node = store.nodes[i];
    for (const k in p.a) {
      const v = String(p.a[k]);
      if (node._a[k] !== v) {
        node.setAttribute(k, v);
        node._a[k] = v;
      }
    }
    for (const k in node._a) {
      if (!(k in p.a)) {
        node.removeAttribute(k);
        delete node._a[k];
      }
    }
    if (p.text !== undefined && node._t !== p.text) {
      node.textContent = p.text;
      node._t = p.text;
    }
  });
}

/**
 * The common chart body. spec = {height, square?, layout(data, W, H, s, state) -> prims, hover?: bool, label}.
 * state.hover = index of the item under the pointer (charts that draw a hover marker).
 */
function createChart(el, spec, opts = {}) {
  const doc = el.ownerDocument || document;
  ensureStyle(doc);
  const svg = doc.createElementNS(NS, "svg");
  svg.setAttribute("class", "ch");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", opts.label || spec.label || "chart");
  el.appendChild(svg);
  const store = { sig: null, nodes: [] };
  const state = { hover: null, id: ++uid };
  let data = null, lastW = 0, observer = null;

  const scale = () => {
    try {
      return parseFloat(doc.defaultView.getComputedStyle(doc.documentElement).getPropertyValue("--s")) || 1;
    } catch (_) {
      return 1;
    }
  };

  const own = {};   // the <svg>'s own attributes as last written (so an unchanged one is not written again)
  const set = (k, v) => {
    if (own[k] !== v) svg.setAttribute(k, (own[k] = v));
  };

  function render() {
    if (!data) return;
    const s = scale();
    const H = n2((opts.height || spec.height) * s);
    const W = spec.square ? H : (opts.width || el.clientWidth || 600);
    lastW = W;
    patch(doc, svg, store, spec.layout(data, W, H, s, state));
    set("viewBox", `0 0 ${n2(W)} ${H}`);
    set("height", String(H));
    if (spec.square) set("width", String(W));
  }

  svg.addEventListener("pointermove", (e) => {
    const target = e.target;
    const tip = target && target.getAttribute ? target.getAttribute("data-tip") : null;
    if (tip) showTip(doc, tip, e.clientX, e.clientY);
    else hideTip(doc);
    if (spec.hover) {
      const i = target && target.getAttribute ? target.getAttribute("data-i") : null;
      const next = i === null || i === undefined ? null : +i;
      if (next !== state.hover) {
        state.hover = next;
        render();
      }
    }
  });
  svg.addEventListener("pointerleave", () => {
    hideTip(doc);
    if (spec.hover && state.hover !== null) {
      state.hover = null;
      render();
    }
  });

  if (!spec.square && !opts.width && typeof ResizeObserver !== "undefined") {
    observer = new ResizeObserver(() => {
      if (el.clientWidth && Math.abs(el.clientWidth - lastW) >= 1) render();
    });
    observer.observe(el);
  }

  return {
    svg,
    update(next) {
      data = next;
      render();
    },
    relayout: render,
    destroy() {
      if (observer) observer.disconnect();
      hideTip(doc);
      svg.remove ? svg.remove() : el.removeChild(svg);
    },
  };
}

/** A bar as a path with rounded top corners (the designer's `3px 3px 0 0`). */
function barPath(x, y, w, bottom, r) {
  const h = Math.max(0, bottom - y);
  r = Math.min(r, h, w / 2);
  return `M${n2(x)} ${n2(bottom)}V${n2(y + r)}Q${n2(x)} ${n2(y)} ${n2(x + r)} ${n2(y)}H${n2(x + w - r)}Q${n2(x + w)} ${n2(y)} ${n2(x + w)} ${n2(y + r)}V${n2(bottom)}Z`;
}

const hh = (h) => String(h).padStart(2, "0");

// ---------------------------------------------------------------- bar chart (a value per day)
/**
 * data = {bars: [{label, value, tip, today?, unlocks?, tone?: "block" | "over" | "now", show?: bool}], goal?: number,
 *         goalLabel?: string, minPeak?: number}      (tone: the bar's colour class, but today's bar is always "now"; show: draw its label - default every 5th)
 * Elements: 1 + 3n + 2 + n  (n = bars).
 */
export function layoutBars(data, W, H, s) {
  const bars = data.bars || [];
  const n = bars.length;
  if (!n) return { prims: [] };
  const top = 14 * s, bottom = H - 20 * s;
  const goal = data.goal > 0 ? data.goal : 0;
  const peak = Math.max(1, data.minPeak || 0, goal, ...bars.map((b) => b.value || 0));
  const y = (v) => bottom - ((bottom - top) * v) / peak;
  const slot = W / n, bw = Math.min(34 * s, slot * 0.6);
  const prims = [P("line", { x1: 0, x2: n2(W), y1: n2(bottom), y2: n2(bottom), class: "ch-axis" })];
  const marks = [], labels = [], hits = [];
  bars.forEach((b, i) => {
    const x = slot * i + (slot - bw) / 2;
    const v = Math.max(0, b.value || 0);
    const tone = b.today ? "now" : b.tone || (goal && v > goal ? "over" : "");
    prims.push(P("path", { d: barPath(x, y(v), bw, bottom, 3 * s), class: "ch-bar" + (tone ? " " + tone : "") }));
    const cy = Math.max(4.5 * s, y(v) - 7 * s);
    marks.push(P("circle", { cx: n2(x + bw / 2), cy: n2(cy), r: b.unlocks ? n2(3.5 * s) : 0, class: "ch-mark" }));
    labels.push(P("text", { x: n2(x + bw / 2), y: n2(bottom + 14 * s), "text-anchor": "middle" },
      { text: (b.show ?? (n <= 10 || i % 5 === 0 || b.today)) ? String(b.label ?? "") : "" }));
    hits.push(P("rect", { x: n2(slot * i), y: 0, width: n2(slot), height: n2(H), class: "ch-hit",
      "data-tip": (b.tip || "") + (b.unlocks ? `\nEmergency unlock used ${b.unlocks}x` : "") }));
  });
  const gy = goal ? y(goal) : bottom;
  prims.push(...marks, ...labels,
    P("line", { x1: 0, x2: n2(W), y1: n2(gy), y2: n2(gy), class: "ch-goal", visibility: goal ? "visible" : "hidden" }),
    P("text", { x: n2(W), y: n2(gy - 5 * s), "text-anchor": "end", class: "ch-gl", visibility: goal ? "visible" : "hidden" },
      { text: goal ? data.goalLabel || "" : "" }),
    ...hits);
  return { prims, geom: { top, bottom, peak, goalY: goal ? gy : null, slot } };
}
export const barChart = (el, opts) => createChart(el, { height: 170, label: "Bars per day", layout: (d, W, H, s) => layoutBars(d, W, H, s).prims }, opts);

// ---------------------------------------------------------------- trend (area + line)
/** An hour-axis label for minutes: 0h, 30m, 1h, 1h30, 2h. */
export const hourLabel = (m) => (m === 0 ? "0h" : m < 60 ? `${m}m` : m % 60 === 0 ? `${m / 60}h` : `${Math.floor(m / 60)}h${hh(m % 60)}`);

/** The trend's Y axis for a peak in minutes: a step of 30 min up to a 2 h peak, 1 h up to 6 h, 2 h up to 12 h, else 4 h; the
 *  top is the peak rounded up to a whole step. Returns {max, ticks: [0, step, ... max]}. */
export function trendAxis(peak) {
  let step = peak <= 120 ? 30 : peak <= 360 ? 60 : peak <= 720 ? 120 : 240;
  while (peak / step > 12) step *= 2;                 // (a runaway value must not make hundreds of lines)
  const max = Math.ceil(peak / step) * step, ticks = [];
  for (let v = 0; v <= max; v += step) ticks.push(v);
  return { max, ticks };
}

/**
 * data = {points: [{label, value, tip, weekday?, avg?: number|null, unlocks?}], goal?, minPeak?}
 * Values are minutes. `label` is the day of the month, `weekday` 0 = Monday (Python decides both): a faint line is drawn at
 * every day and a strong one at every Monday; 7 days are labelled "M T W T F S S" (Mondays "M 12"), more days only the
 * Mondays. `avg` is the running average for that day (the dashed line skips points without one); `tip` the hover text.
 * Elements: 6 + 2 * ticks + 4n  (n = points).
 */
export function layoutTrend(data, W, H, s, state = {}) {
  const pts = data.points || [];
  const n = pts.length;
  if (n < 2) return { prims: [] };
  const goal = data.goal > 0 ? data.goal : 0;
  const axis = trendAxis(Math.max(1, data.minPeak || 0, goal, ...pts.map((p) => p.value || 0)));
  const peak = axis.max, ylabs = axis.ticks.map(hourLabel);
  const gutter = Math.max(...ylabs.map((t) => t.length)) * 6.6 * s + 6 * s;      // (room for the hour labels, left of the plot)
  const top = 14 * s, bottom = H - 20 * s, left = gutter + 14 * s, right = W - 14 * s;
  const y = (v) => bottom - ((bottom - top) * v) / peak;
  const xs = pts.map((_, i) => left + ((right - left) * i) / (n - 1));
  const ys = pts.map((p) => y(Math.max(0, p.value || 0)));
  const line = xs.map((x, i) => `${i ? "L" : "M"}${n2(x)} ${n2(ys[i])}`).join("");
  let avg = "", pen = false;
  pts.forEach((p, i) => {
    if (p.avg === null || p.avg === undefined) { pen = false; return; }
    avg += `${pen ? "L" : "M"}${n2(xs[i])} ${n2(y(p.avg))}`;
    pen = true;
  });
  const gy = goal ? y(goal) : bottom;
  const h = state.hover;
  const prims = [
    P("line", { x1: n2(gutter), x2: n2(W), y1: n2(bottom), y2: n2(bottom), class: "ch-axis" }),
    ...axis.ticks.map((v) => P("line", { x1: n2(gutter), x2: n2(W), y1: n2(y(v)), y2: n2(y(v)), class: "ch-grid" })),
    ...axis.ticks.map((v, i) => P("text", { x: n2(gutter - 6 * s), y: n2(y(v) + 4 * s), "text-anchor": "end", class: "ch-ylab" }, { text: ylabs[i] })),
    ...pts.map((p, i) => P("line", { x1: n2(xs[i]), x2: n2(xs[i]), y1: n2(top), y2: n2(bottom), class: p.weekday === 0 ? "ch-week" : "ch-day" })),
    P("path", { d: `${line}L${n2(xs[n - 1])} ${n2(bottom)}L${n2(xs[0])} ${n2(bottom)}Z`, class: "ch-area" }),
    P("line", { x1: n2(gutter), x2: n2(W), y1: n2(gy), y2: n2(gy), class: "ch-goal", visibility: goal ? "visible" : "hidden" }),
    P("path", { d: avg, class: "ch-avg" }),
    P("path", { d: line, class: "ch-line" }),
  ];
  pts.forEach((p, i) => prims.push(P("circle", { cx: n2(xs[i]), cy: n2(ys[i]), r: p.unlocks ? n2(4 * s) : 0, class: "ch-ring" })));
  pts.forEach((p, i) => {
    const text = p.weekday === 0 ? `M ${p.label}` : n <= 7 ? "MTWTFSS"[p.weekday] ?? "" : "";
    prims.push(P("text", { x: n2(xs[i]), y: n2(bottom + 14 * s), "text-anchor": "middle" }, { text }));
  });
  prims.push(P("circle", { cx: h === null || h === undefined ? 0 : n2(xs[h]), cy: h === null || h === undefined ? 0 : n2(ys[h]),
    r: h === null || h === undefined ? 0 : n2(4 * s), class: "ch-dot" }));
  pts.forEach((p, i) => {                             // (the hover areas meet halfway between two days)
    const x0 = i ? (xs[i - 1] + xs[i]) / 2 : gutter, x1 = i < n - 1 ? (xs[i] + xs[i + 1]) / 2 : W;
    prims.push(P("rect", { x: n2(x0), y: 0, width: n2(x1 - x0), height: n2(H), class: "ch-hit", "data-tip": p.tip || "", "data-i": i }));
  });
  return { prims, geom: { top, bottom, left, peak, goalY: goal ? gy : null, xs, ys, ticks: axis.ticks } };
}
export const trendChart = (el, opts) => createChart(el, { height: 180, hover: true, label: "Trend", layout: (d, W, H, s, st) => layoutTrend(d, W, H, s, st).prims }, opts);

// ---------------------------------------------------------------- day timeline
/**
 * data = {segments: [{start, len, kind}] (minutes since midnight), startHour?: 6, colors?: {kind: css colour},
 *         names?: {kind: text}}. `kind` "idle" is drawn like the empty track. Neighbouring segments of one kind are
 * joined and, past 120, only the longest are drawn.
 */
export const TIMELINE_COLORS = { prod: "var(--cat-prod)", neutral: "var(--cat-neutral)", dist: "var(--cat-dist)", idle: "var(--cat-idle)" };

export function layoutTimeline(data, W, H, s, state = {}) {
  const sh = data.startHour ?? 6;
  const from = sh * 60, span = (24 - sh) * 60;
  const colors = { ...TIMELINE_COLORS, ...(data.colors || {}) };
  const names = data.names || {};
  const barH = 24 * s;
  const x = (min) => ((min - from) / span) * W;
  let segs = [];
  for (const g of [...(data.segments || [])].sort((a, b) => a.start - b.start)) {
    const end = g.start + g.len;
    if (end <= from || g.len <= 0) continue;
    const last = segs[segs.length - 1];
    if (last && last.kind === g.kind && g.start - (last.start + last.len) <= 1) last.len = end - last.start;
    else segs.push({ start: Math.max(g.start, from), len: end - Math.max(g.start, from), kind: g.kind });
  }
  if (segs.length > MAX_SEGMENTS) segs = [...segs].sort((a, b) => b.len - a.len).slice(0, MAX_SEGMENTS).sort((a, b) => a.start - b.start);
  const id = `ldc-clip-${state.id ?? 0}`;
  const prims = [
    P("clipPath", { id }),
    P("rect", { x: 0, y: 0, width: n2(W), height: n2(barH), rx: n2(3 * s) }, { in: 0 }),
    P("rect", { x: 0, y: 0, width: n2(W), height: n2(barH), class: "ch-trk", "clip-path": `url(#${id})` }),
    P("g", { "clip-path": `url(#${id})` }),
  ];
  segs.forEach((g) => {
    const x0 = Math.max(0, x(g.start)), x1 = Math.min(W, x(g.start + g.len));
    const pad = (a) => `${hh(Math.floor(a / 60) % 24)}:${hh(a % 60)}`;
    prims.push(P("rect", { x: n2(x0), y: 0, width: n2(Math.max(2, x1 - x0)), height: n2(barH), style: `fill:${colors[g.kind] || colors.neutral}`,
      "data-tip": `${pad(g.start)}-${pad(g.start + g.len)}  ${names[g.kind] || g.kind}` }, { in: 3 }));
  });
  const step = 24 - sh > 12 ? 3 : 2;
  for (let h = sh; h <= 24; h += step) {
    prims.push(P("text", { x: n2(x(h * 60)), y: n2(barH + 14 * s), "text-anchor": h === sh ? "start" : h === 24 ? "end" : "middle" },
      { text: `${hh(h)}:00` }));
  }
  return { prims, geom: { barH, x, segs } };
}
export const timelineChart = (el, opts) => createChart(el, { height: 46, label: "Your day", layout: (d, W, H, s, st) => layoutTimeline(d, W, H, s, st).prims }, opts);

// ---------------------------------------------------------------- heatmap (days x 24 hours)
/** data = {rows: [{label, minutes: [24 active minutes]}]}. 5 levels: 0, 1-14, 15-29, 30-44, 45+ minutes. */
export const heatLevel = (minutes) => HEAT_LEVELS.reduce((n, t) => n + (minutes >= t ? 1 : 0), 0);

export function layoutHeatmap(data, W, H, s) {
  const rows = data.rows || [];
  if (!rows.length) return { prims: [] };
  const left = 34 * s, top = 16 * s, legend = 18 * s, gap = 3 * s;
  const cw = (W - left) / 24, chh = (H - top - legend) / rows.length;
  const prims = [];
  for (let h = 0; h < 24; h += 3) prims.push(P("text", { x: n2(left + h * cw), y: n2(11 * s), "text-anchor": "start" }, { text: hh(h) }));
  rows.forEach((row, r) => {
    const y = top + r * chh;
    prims.push(P("text", { x: 0, y: n2(y + chh / 2 + 4 * s) }, { text: String(row.label ?? "") }));
    for (let h = 0; h < 24; h++) {
      const m = (row.minutes && row.minutes[h]) || 0;
      prims.push(P("rect", { x: n2(left + h * cw), y: n2(y), width: n2(Math.max(0, cw - gap)), height: n2(Math.max(0, chh - gap)), rx: n2(2 * s),
        class: `ch-h${heatLevel(m)}`, "data-tip": `${row.label} ${hh(h)}:00-${hh((h + 1) % 24)}:00  ${Math.round(m)} min active` }));
    }
  });
  // legend, bottom right: less [][][][][] more
  const box = 10 * s, step = 14 * s, ly = H - legend + 4 * s;
  const x0 = W - 34 * s - 5 * step;
  prims.push(P("text", { x: n2(x0 - 6 * s), y: n2(ly + box - 1), "text-anchor": "end" }, { text: "less" }));
  for (let i = 0; i < 5; i++) prims.push(P("rect", { x: n2(x0 + i * step), y: n2(ly), width: n2(box), height: n2(box), rx: n2(2 * s), class: `ch-h${i}` }));
  prims.push(P("text", { x: n2(W), y: n2(ly + box - 1), "text-anchor": "end" }, { text: "more" }));
  return { prims, geom: { left, top, cw, ch: chh } };
}
export const heatmapChart = (el, opts) => createChart(el, { height: 230, label: "Activity by hour", layout: (d, W, H, s) => layoutHeatmap(d, W, H, s).prims }, opts);

// ---------------------------------------------------------------- donut
/** data = {parts: [{value, color (css), tip}], center: text, caption?: "total"} */
export function layoutDonut(data, W, H, s) {
  const size = Math.min(W, H);
  const ring = 18 * s, r = (size - ring) / 2 - 1, c = size / 2, C = 2 * Math.PI * r;
  const parts = data.parts || [];
  const total = parts.reduce((a, p) => a + Math.max(0, p.value || 0), 0);
  const prims = [P("circle", { cx: n2(c), cy: n2(c), r: n2(r), class: "ch-ringtrack", "stroke-width": n2(ring) })];
  let offset = 0;
  parts.forEach((p) => {
    const len = total ? (Math.max(0, p.value || 0) / total) * C : 0;
    prims.push(P("circle", { cx: n2(c), cy: n2(c), r: n2(r), fill: "none", "stroke-width": n2(ring), style: `stroke:${p.color}`,
      "stroke-dasharray": `${n2(len)} ${n2(C - len)}`, "stroke-dashoffset": n2(-offset), transform: `rotate(-90 ${n2(c)} ${n2(c)})`,
      "data-tip": len ? p.tip || "" : "" }));
    offset += len;
  });
  prims.push(P("text", { x: n2(c), y: n2(c + 1 * s), "text-anchor": "middle", class: "ch-big" }, { text: String(data.center ?? "") }),
    P("text", { x: n2(c), y: n2(c + 15 * s), "text-anchor": "middle" }, { text: data.caption ?? "total" }));
  return { prims, geom: { C, r, c } };
}
export const donutChart = (el, opts) => createChart(el, { height: 110, square: true, label: "Split", layout: (d, W, H, s) => layoutDonut(d, W, H, s).prims }, opts);

// ---------------------------------------------------------------- hour bars (a count per hour)
/** data = {counts: {hour: n} | [n x 24], emptyText?, unit?: "switch", plural?: "switches"}: 07-22 at least; the peak
 *  and the next two stand out. */
export function layoutHourBars(data, W, H, s) {
  const raw = data.counts || {};
  const get = (h) => raw[h] || 0;
  const used = [];
  for (let h = 0; h < 24; h++) if (get(h) > 0) used.push(h);
  if (!used.length) return { prims: [P("text", { x: n2(W / 2), y: n2(H / 2), "text-anchor": "middle" }, { text: data.emptyText ?? "Nothing yet" })] };
  const lo = Math.min(7, ...used), hi = Math.max(22, ...used);
  const hours = [];
  for (let h = lo; h <= hi; h++) hours.push(h);
  const peak = Math.max(...used.map(get));
  const ranked = [...used].sort((a, b) => get(b) - get(a) || a - b);
  const top = 4 * s, bottom = H - 20 * s;
  const slot = W / hours.length, bw = Math.min(30 * s, slot * 0.72);
  const unit = data.unit || "switch", plural = data.plural || unit + "es";
  const prims = [P("line", { x1: 0, x2: n2(W), y1: n2(bottom), y2: n2(bottom), class: "ch-axis" })];
  const labels = [], hits = [];
  hours.forEach((h, i) => {
    const v = get(h);
    const x = slot * i + (slot - bw) / 2;
    const tone = h === ranked[0] ? "now" : ranked.slice(1, 3).includes(h) ? "over" : "";
    prims.push(P("path", { d: barPath(x, bottom - ((bottom - top) * v) / peak, bw, bottom, 3 * s), class: "ch-bar" + (tone ? " " + tone : "") }));
    labels.push(P("text", { x: n2(x + bw / 2), y: n2(bottom + 14 * s), "text-anchor": "middle" }, { text: hours.length <= 18 || h % 2 === 0 ? hh(h) : "" }));
    hits.push(P("rect", { x: n2(slot * i), y: 0, width: n2(slot), height: n2(H), class: "ch-hit",
      "data-tip": `${hh(h)}:00-${hh((h + 1) % 24)}:00  ${v} ${v === 1 ? unit : plural}` }));
  });
  prims.push(...labels, ...hits);
  return { prims, geom: { top, bottom, peak, ranked } };
}
export const hourBarChart = (el, opts) => createChart(el, { height: 200, label: "Per hour", layout: (d, W, H, s) => layoutHourBars(d, W, H, s).prims }, opts);
