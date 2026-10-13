// Dashboard (BLUEPRINT 3.1): "how am I doing today?" - the numbers, today's timeline, limits, what is coming up, the last
// 7 days, blocked visits, a quick glance, the most used apps today, and the designer's Active blocks / Bedtime / Pause /
// Emergency cards.
//
// mount() builds the static frame once (header, the notices, the grid). load() is ONE Api call (dashboard_get) and fills
// the cards IN PLACE: a card element is made the first time it is shown and kept; rows are keyed and reused; charts get
// update(); text is written only when it changed. Nothing is re-rendered on a data tick:
//   - usage_tick {active_sec}  -> the screen-time number (and its "vs yesterday" words) change, nothing else;
//   - status / blocks_changed / mode_changed -> load() again (one Api call) when something the cards show changed;
//   - the pause and bedtime countdowns tick locally from their end times (page.every, only while this page is shown).
// Nothing reloads on a timer: the agent pushes usage_tick every ~5 s while the window is shown, and a page that was
// left while things changed is marked and reloads on its next show.
// The shell owns the "Lockdown is off" and "service stopped" banners; this page only says it in the Blocked-now number.
// Cards the owner hid are not in dashboard_get's answer (Python does not compute them). Customise opens a list of every card
// (up / down and a switch each, Reset to default) like the phone's Edit Home; each change saves at once (display_set).
import { wireIconReady, iconImg } from "../appicon.js";
import { ld } from "../bridge.js";
import { barChart, timelineChart } from "../charts.js";
import { openDialog } from "../dialog.js";
import { openDisplay } from "../display.js";
import { hhmm, parseNaive, trustedNow } from "../shell.js";
import { el, empty, esc, fmt, ic, setText, sw } from "../ui.js";

export const freshMs = 10_000;
export const events = ["usage_tick", "status", "blocks_changed", "mode_changed"];
const SPAN = { stats: 3, today: 3, limits: 2, week: 2 };           // columns of the 3-column grid (default 1)
const TONE = { ok: "t-ok", warn: "t-warn", bad: "t-bad", muted: "" };
const METER = { ok: "k-ok", warn: "k-limit", bad: "k-block" };
const DOT = { start: "k-block", end: "k-ok", limit: "k-limit" };    // gets blocked / allowed again / about to run out
const STAT_LABELS = [["screen", "Screen time today"], ["blocked", "Blocked now"], ["switches", "Switches today"], ["saved", "Time saved"]];

// ---------------------------------------------------------------- pure helpers (node tests)
/** "4 h 12 m" / "52 m" - the same words stats.hm gives. */
export function hm(sec) {
  const minutes = Math.floor(Math.max(0, sec) / 60);
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")} m` : `${m} m`;
}

/** The screen-time stat's note and tone for `active` seconds against yesterday's at the same time: within a minute is
 *  "about the same". The same rule dashboard.py applies. */
export function screenDelta(active, yesterday) {
  const diff = active - yesterday;
  if (Math.abs(diff) < 60) return { note: "about the same as yesterday", tone: "muted" };
  return { note: `${hm(Math.abs(diff))} ${diff > 0 ? "more" : "less"} than yesterday`, tone: diff > 0 ? "warn" : "ok" };
}

/** Minutes until an end time on the trusted clock, rounded up (never negative). */
export const minutesLeft = (iso, nowMs) => Math.max(0, Math.ceil((parseNaive(iso) - nowMs) / 60000));

/** The header's second line: date, tracking since, the active mode. `mode` is the status snapshot's. */
export function subLine(date, since, mode) {
  let text = date || "";
  if (since) text += `  ·  tracking since ${since}`;
  if (mode) {
    const phase = mode.phase && mode.phase.name;
    const end = phase ? mode.phase.ends_at : mode.ends_at;
    text += `  ·  ${mode.name} mode${phase ? ", " + phase : ""}${end ? " until " + hhmm(end) : ""}`;
  }
  return text;
}

/** Keep `host`'s children equal to `rows` (keyed): rows that stay are reused and only updated, new ones are built,
 *  gone ones removed, and a node is moved only when it is not where it belongs. */
export function syncRows(host, rows, build, update) {
  const old = host._rows || new Map();
  const next = new Map();
  let prev = null;
  for (const r of rows) {
    let node = old.get(r.key);
    if (node) old.delete(r.key); else node = build(r);
    update(node, r);
    next.set(r.key, node);
    const want = prev ? prev.nextSibling : host.firstChild;
    if (node !== want) host.insertBefore(node, want);
    prev = node;
  }
  for (const node of old.values()) node.remove();
  host._rows = next;
}

// ---------------------------------------------------------------- the page
const STYLE = `<style>
.dash-page .dash { grid-auto-flow: row dense; grid-auto-rows: 1px; row-gap: 0; }   /* masonry: rows are 1 px, see fit() */
.dash .span-3 { grid-column: 1 / -1; }
.dash-item { min-width: 0; display: grid; gap: var(--sp-2); align-content: start; }
.dash-item[hidden] { display: none; }
.dash-item .card-h .sub-note { color: var(--text-2); font-size: var(--fs-sm); }
.dash-item .card-h:has(+ .card-b[hidden]) { padding-bottom: var(--sp-4); }   /* a folded card (Blocked visits): the body's bottom padding */
.t-ok { color: var(--ok); } .t-warn { color: var(--k-limit); } .t-bad { color: var(--k-block); }
.stat .d.t-ok, .stat .d.t-warn, .stat .d.t-bad { font-weight: var(--fw-semibold); }
.dash-lg { display: inline-flex; align-items: center; gap: var(--sp-1); color: var(--text-2); font-size: var(--fs-sm); }
.dash-lg i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
.dash-legend { display: flex; flex-wrap: wrap; gap: var(--sp-1) var(--sp-3); }
.aic.gl-tile { --c: var(--k-soft); --fg: var(--k); }
.aic.gl-tile .ic { width: 70%; height: 70%; stroke-width: 2; }
.lim-row { display: grid; gap: var(--sp-1); padding: var(--sp-2) 0; }
.lim-row + .lim-row { border-top: 1px solid var(--line); }
.lim-row .hstack { gap: var(--sp-3); }
.lim-row .name { font-weight: var(--fw-semibold); }
.lim-row .text { color: var(--text-2); font-size: var(--fs-sm); text-align: right; }
.lim-row .meter { height: calc(5px * var(--s)); }
.cm-row { display: flex; gap: var(--sp-3); align-items: flex-start; padding: var(--sp-2) 0; }
.cm-row + .cm-row { border-top: 1px solid var(--line); }
.cm-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--k); margin-top: calc(7px * var(--s)); flex: none; }
.cm-when { min-width: calc(44px * var(--s)); color: var(--brand); font: var(--fw-semibold) var(--fs-lg)/1.3 var(--font-display); font-variant-numeric: tabular-nums; }
.cm-sub { color: var(--text-2); font-size: var(--fs-sm); }
.vis-count { font: var(--fw-semibold) var(--fs-title)/1 var(--font-display); color: var(--brand); font-variant-numeric: tabular-nums; }
.vis-row { display: flex; align-items: center; gap: var(--sp-3); height: calc(34px * var(--s)); }
.gl-row { display: grid; gap: 1px; padding: var(--sp-2) 0; }
.gl-row + .gl-row { border-top: 1px solid var(--line); }
.gl-row b { font-weight: var(--fw-semibold); }
.dash-note { color: var(--text-2); font-size: var(--fs-sm); }
.dash-empty-btns { display: flex; gap: var(--sp-2); flex-wrap: wrap; justify-content: center; margin-top: var(--sp-2); }
.dash-empty-btns .btn { margin-top: 0; }
.cust-rows { display: grid; gap: 2px; }
.cust-row { display: flex; align-items: center; gap: var(--sp-1); min-height: calc(36px * var(--s)); }
.cust-row .name { flex: 1; min-width: 0; }
.cust-row .switch { margin-left: var(--sp-2); }
</style>`;

let root = null, page = null, grid = null;
let data = null;                         // the last dashboard_get answer
const cards = new Map();                 // key -> {node, update(d), tick?()}
let status = {};                         // what the cards were last loaded for (to tell a push that matters)
let savedLayout = Promise.resolve();
let fitter = null;                       // ResizeObserver: a card's grid rows follow its height (fit)

const q = (k) => root.querySelector(`[data-k="${k}"]`);
const nowMs = () => trustedNow();

/** A card: header (title, an optional note) and a body. Returns [wrapper, body, header]. */
function frame(key, title, note = false) {
  const wrap = el(`<div class="dash-item${SPAN[key] ? " span-" + SPAN[key] : ""}" data-card="${key}"><section class="card">
    <header class="card-h"><h3>${esc(title)}</h3>${note ? '<span class="sub-note" data-n="note"></span>' : ""}</header>
    <div class="card-b" data-n="body"></div></section></div>`);
  return [wrap, wrap.querySelector('[data-n="body"]'), wrap.querySelector(".card-h")];
}

const n = (node, k) => node.querySelector(`[data-n="${k}"]`);
/** The icon of a Limits row: the app / site icon, or - for a group's row - the group's own icon and colour (js/groupicons.js is
 *  loaded only when there is such a row; the letter tile shows until it is there). */
function limitIcon(node, row) {
  node.classList.remove("gl-tile");
  for (const c of [...node.classList]) if (c.startsWith("hue-")) node.classList.remove(c);
  node.innerHTML = iconImg(row.icon[0], row.icon[1]);
  if (!row.look) return;
  const sig = node.dataset.sig;
  import("../groupicons.js").then((g) => {
    if (node.dataset.sig !== sig) return;                       // (the row was reused for something else meanwhile)
    node.classList.add("gl-tile");
    g.setHue(node, row.look.color);
    node.innerHTML = ic(g.symbolOf(row.look.icon));
  }).catch(() => {});
}
const iconBox = (icon, cls = "aic-sm") => `<span class="aic ${cls}">${iconImg(icon[0], icon[1])}</span>`;

// ---------------------------------------------------------------- the cards (one builder each: mount once, update(d) in place)
const BUILD = {
  stats(title) {
    const wrap = el(`<div class="dash-item span-3" data-card="stats">
      <div class="grid-4">${STAT_LABELS.map(([k, label]) => `<div class="card stat" data-stat="${k}"><span class="cap">${esc(label)}</span><span class="v" data-n="v"></span><span class="d" data-n="d"></span></div>`).join("")}</div></div>`);
    const boxes = Object.fromEntries(STAT_LABELS.map(([k]) => [k, wrap.querySelector(`[data-stat="${k}"]`)]));
    const put = (k, s) => {
      setText(n(boxes[k], "v"), s.value);
      setText(n(boxes[k], "d"), s.note);
      const d = n(boxes[k], "d");
      const cls = `d ${TONE[s.tone] || ""}`.trim();
      if (d.className !== cls) d.className = cls;
    };
    return {
      node: wrap,
      update(d) { for (const [k] of STAT_LABELS) put(k, d[k]); this.last = d; },
      /** A usage_tick: the screen-time number moves, with its words against yesterday. */
      active(sec) {
        const s = this.last && this.last.screen;
        if (!s) return;
        s.sec = sec;
        put("screen", { value: hm(sec), ...screenDelta(sec, s.yesterday_sec) });
      },
    };
  },

  today(title) {
    const [wrap, body] = frame("today", title);
    body.innerHTML = `<div data-n="chart"></div><div class="dash-legend" data-n="legend" style="margin-top:var(--sp-2)"></div>`;
    let chart = null;
    return {
      node: wrap,
      update(d) {
        const input = { segments: d.segments, startHour: d.start_hour, colors: d.colors, names: d.names };
        if (!chart) chart = timelineChart(n(wrap, "chart"), { label: "Your day" });
        chart.update(input);
        syncRows(n(wrap, "legend"), d.legend, (r) => el(`<span class="dash-lg"><i></i><span></span></span>`), (node, r) => {
          const sw = node.firstChild;
          if (sw.style.background !== r.color) sw.style.background = r.color;
          setText(node.lastChild, r.name);
        });
      },
    };
  },

  limits(title) {
    const [wrap, body] = frame("limits", title, true);
    body.innerHTML = `<div data-n="rows"></div><p class="muted small" data-n="none" hidden>No time or opening limits set.</p>`;
    return {
      node: wrap,
      update(d) {
        setText(n(wrap, "note"), d.note);
        n(wrap, "none").hidden = d.rows.length > 0;
        syncRows(n(wrap, "rows"), d.rows, () => el(`<div class="lim-row"><div class="hstack"><span class="aic aic-sm" data-n="icon"></span><span class="name ellip" data-n="name"></span><span class="grow"></span><span class="text num ellip" data-n="text"></span><b class="num" data-n="left"></b></div><div class="meter"><i></i></div></div>`),
          (node, r) => {
            const icon = n(node, "icon");
            const sig = r.icon.join("|") + (r.look ? `|${r.look.icon}|${r.look.color}` : "");
            if (icon.dataset.sig !== sig) { icon.dataset.sig = sig; limitIcon(icon, r); }
            setText(n(node, "name"), r.name);
            setText(n(node, "text"), r.text);
            const left = n(node, "left");
            setText(left, r.left);
            const cls = `num ${TONE[r.tone] || ""}`.trim();
            if (left.className !== cls) left.className = cls;
            const meter = node.querySelector(".meter");
            const mc = `meter ${METER[r.tone] || ""}`.trim();
            if (meter.className !== mc) meter.className = mc;
            const w = `${Math.round(r.frac * 100)}%`;
            if (meter.firstChild.style.width !== w) meter.firstChild.style.width = w;
          });
      },
    };
  },

  coming(title) {
    const [wrap, body] = frame("coming", title);
    body.innerHTML = `<div data-n="rows"></div><p class="muted small" data-n="none" hidden>Nothing in the next 24 hours.</p>`;
    return {
      node: wrap,
      update(d) {
        n(wrap, "none").hidden = d.rows.length > 0;
        syncRows(n(wrap, "rows"), d.rows, () => el(`<div class="cm-row"><i class="cm-dot"></i><span class="cm-when num" data-n="when"></span><div class="grow"><div class="ellip" data-n="title"></div><div class="cm-sub" data-n="sub"></div></div></div>`),
          (node, r) => {
            const dot = node.firstChild;
            const dc = `cm-dot ${DOT[r.kind] || ""}`;
            if (dot.className !== dc) dot.className = dc;
            setText(n(node, "when"), r.time);
            setText(n(node, "title"), r.title);
            setText(n(node, "sub"), r.sub);
            n(node, "sub").hidden = !r.sub;
          });
      },
    };
  },

  week(title) {
    const [wrap, body] = frame("week", title, true);
    body.innerHTML = `<div data-n="chart"></div>`;
    let chart = null;
    return {
      node: wrap,
      update(d) {
        setText(n(wrap, "note"), d.note);
        if (!chart) chart = barChart(n(wrap, "chart"), { label: "Active screen time per day, last 7 days" });
        chart.update({ bars: d.bars, goal: d.goal, goalLabel: d.goal_label });
      },
    };
  },

  visits(title) {
    const [wrap, body, head] = frame("visits", title);
    const count = el('<span class="vis-count num" data-n="count"></span>');
    const toggle = el('<button class="btn btn-sm" type="button" data-n="toggle"></button>');
    head.append(count, toggle);
    body.innerHTML = `<div data-n="rows"></div><p class="muted small" data-n="none" hidden>No blocked visits today.</p>`;
    let open = false, got = null;
    const shown = () => {
      body.hidden = !open;
      setText(toggle, open ? "Hide" : "Show");
      toggle.setAttribute("aria-expanded", String(open));
    };
    toggle.addEventListener("click", () => {
      open = !open;
      shown();
      ld.call("ui_setting", "dash.visits_open", open ? "1" : "0").catch(() => {});
    });
    return {
      node: wrap,
      update(d) {
        got = d;
        open = d.open;
        shown();
        setText(count, d.count ? String(d.count) : "");
        n(wrap, "none").hidden = d.rows.length > 0;
        syncRows(n(wrap, "rows"), d.rows, () => el(`<div class="vis-row"><span class="aic aic-sm" data-n="icon"></span><span class="ellip grow" data-n="name"></span><span class="muted small num" data-n="text"></span></div>`),
          (node, r) => {
            const icon = n(node, "icon");
            const sig = r.icon.join("|");
            if (icon.dataset.sig !== sig) { icon.dataset.sig = sig; icon.innerHTML = iconImg(r.icon[0], r.icon[1]); }
            setText(n(node, "name"), r.name);
            setText(n(node, "text"), r.text);
          });
      },
    };
  },

  glance(title) {
    const [wrap, body] = frame("glance", title);
    body.innerHTML = `<div data-n="rows"></div><p class="muted small" data-n="none" hidden>Not enough data yet.</p>`;
    return {
      node: wrap,
      update(d) {
        n(wrap, "none").hidden = d.rows.length > 0;
        syncRows(n(wrap, "rows"), d.rows.map((r) => ({ ...r, key: r.label })), () => el(`<div class="gl-row"><span class="cap" data-n="label"></span><div><b data-n="main"></b><span class="muted small" data-n="extra"></span></div></div>`),
          (node, r) => {
            setText(n(node, "label"), r.label);
            setText(n(node, "main"), r.main);
            setText(n(node, "extra"), r.extra ? "  " + r.extra : "");
          });
      },
    };
  },

  active(title) {
    const [wrap, body] = frame("active", title);
    body.innerHTML = `<div data-n="rows"></div><p class="muted small" data-n="more" hidden></p>
      <div data-n="none" hidden><p class="muted small">Nothing is blocked right now.</p><div class="btn-row" style="margin-top:var(--sp-2)"><button class="btn btn-sm" type="button" data-n="add">${ic("plus")}Add a block</button></div></div>`;
    n(wrap, "add").addEventListener("click", () => page.go("blocking", { tab: "add" }));
    return {
      node: wrap,
      update(d) {
        n(wrap, "none").hidden = d.count > 0;
        n(wrap, "more").hidden = !d.more;
        setText(n(wrap, "more"), d.more ? `and ${d.more} more` : "");
        syncRows(n(wrap, "rows"), d.rows, () => el(`<div class="mini-row"><span class="aic aic-sm" data-n="icon"></span><div class="grow ellip"><b class="ellip" data-n="name"></b></div><span class="chip chip-block" data-n="text"></span></div>`),
          (node, r) => {
            const icon = n(node, "icon");
            const sig = r.icon.join("|");
            if (icon.dataset.sig !== sig) { icon.dataset.sig = sig; icon.innerHTML = iconImg(r.icon[0], r.icon[1]); }
            setText(n(node, "name"), r.name);
            setText(n(node, "text"), r.text);
          });
      },
    };
  },

  bedtime(title) {
    const [wrap, body] = frame("bedtime", title);
    body.innerHTML = `<div data-n="on" hidden><div class="big-time num" data-n="time"></div><div class="small muted" style="margin-top:var(--sp-1)" data-n="sub"></div>
        <div class="hstack small muted" style="margin-top:var(--sp-3)" data-n="mode" hidden>${ic("target")}<span data-n="modetext"></span></div></div>
      <div data-n="off" hidden><p class="muted small">The bedtime reminder is off.</p><div class="btn-row" style="margin-top:var(--sp-2)"><button class="btn btn-sm" type="button" data-n="open">Open Reminders</button></div></div>`;
    n(wrap, "open").addEventListener("click", () => page.go("reminders"));
    let d = null;
    const sub = () => {
      if (!d || !d.on || !d.at) return;
      const left = minutesLeft(d.at, nowMs());
      setText(n(wrap, "sub"), `in ${fmt(left)}` + (d.heads_up ? ` · heads-up ${d.heads_up} min before` : ""));
    };
    return {
      node: wrap,
      update(next) {
        d = next;
        n(wrap, "on").hidden = !d.on;
        n(wrap, "off").hidden = d.on;
        setText(n(wrap, "time"), d.bedtime);
        n(wrap, "mode").hidden = !d.mode;
        setText(n(wrap, "modetext"), d.mode ? `Turns on ${d.mode} mode` : "");
        sub();
      },
      tick: sub,
    };
  },

  pause(title) {
    const [wrap, body] = frame("pause", title);
    body.innerHTML = `<div data-n="paused" hidden><div class="notice k-grey">${ic("pause")}<div class="grow"><b>Your blocks are paused</b><div class="small muted" data-n="left"></div></div></div>
        <div class="btn-row" style="margin-top:var(--sp-3)"><button class="btn btn-primary" type="button" data-n="resume">${ic("play")}Resume now</button></div></div>
      <div data-n="on" hidden><p class="muted small">Your blocks are on. Pausing them asks for the challenge.</p>
        <div class="btn-row" style="margin-top:var(--sp-3)"><button class="btn" type="button" data-n="start">${ic("pause")}Pause my blocks</button></div></div>`;
    let d = null;
    n(wrap, "start").addEventListener("click", () => page.go("antibypass", { focus: "pause" }));
    const resume = n(wrap, "resume");
    resume.addEventListener("click", async () => {
      resume.disabled = true;
      try { await ld.call("pause_resume"); } catch (_) { /* the page reloads either way */ }
      resume.disabled = false;
      page.reload();
    });
    const left = () => {
      if (!d || !d.paused) return;
      setText(n(wrap, "left"), `${fmt(minutesLeft(d.until, nowMs()))} left · back on at ${hhmm(d.until)}`);
    };
    return {
      node: wrap,
      update(next) {
        d = next;
        n(wrap, "paused").hidden = !d.paused;
        n(wrap, "on").hidden = d.paused;
        left();
      },
      tick: left,
    };
  },

  emergency(title) {
    const [wrap, body] = frame("emergency", title);
    body.innerHTML = `<div data-n="rows"></div><p class="muted small" data-n="used" hidden></p><p class="muted small" data-n="off" hidden>Emergency unlock is turned off in Settings.</p>
      <div class="btn-row" style="margin-top:var(--sp-2)" data-n="use-row" hidden><button class="btn btn-emerg" type="button" data-n="use">${ic("lifebuoy")}Use emergency unlock</button></div>`;
    n(wrap, "use").addEventListener("click", () =>
      import("../emergency.js").then(({ openEmergency }) => openEmergency({ onDone: () => page.reload() })));
    return {
      node: wrap,
      update(d) {
        n(wrap, "off").hidden = d.usable;
        const used = d.used_today && d.used_today.length ? `Used today ${d.used_today.join(", ")}` : "";
        setText(n(wrap, "used"), used);
        n(wrap, "used").hidden = !used;
        n(wrap, "use-row").hidden = !(d.usable && d.kinds.some((k) => k.left > 0));
        syncRows(n(wrap, "rows"), d.usable ? d.kinds : [], () => el(`<div class="low-row"><div class="hstack"><b class="grow ellip" data-n="detail"></b><span class="small muted num" data-n="text"></span></div><div class="uses" data-n="uses"></div><span class="small faint" data-n="resets"></span></div>`),
          (node, r) => {
            setText(n(node, "detail"), r.detail);
            setText(n(node, "text"), r.text);
            const uses = n(node, "uses");
            while (uses.children.length < r.allowed) uses.append(document.createElement("i"));
            while (uses.children.length > r.allowed) uses.lastChild.remove();
            [...uses.children].forEach((pip, i) => pip.classList.toggle("used", i < r.allowed - r.left));
            setText(n(node, "resets"), `Resets ${r.resets ? resetText(r.resets) : ""}`);
          });
      },
    };
  },

  apps(title) {
    const [wrap, body] = frame("apps", title);
    body.innerHTML = `<div data-n="rows"></div><p class="muted small" data-n="none" hidden>No app use yet today.</p>`;
    return {
      node: wrap,
      update(d) {
        n(wrap, "none").hidden = d.rows.length > 0;
        syncRows(n(wrap, "rows"), d.rows, () => el(`<div class="vis-row"><span class="aic aic-sm" data-n="icon"></span><span class="ellip grow" data-n="name"></span><span class="muted small num" data-n="text"></span></div>`),
          (node, r) => {
            const icon = n(node, "icon");
            const sig = r.icon.join("|");
            if (icon.dataset.sig !== sig) { icon.dataset.sig = sig; icon.innerHTML = iconImg(r.icon[0], r.icon[1]); }
            setText(n(node, "name"), r.name);
            setText(n(node, "text"), r.time);
          });
      },
    };
  },
};

const resetText = (iso) => {
  const day = iso.slice(0, 10) === new Date(nowMs()).toISOString().slice(0, 10) ? "" : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(parseNaive(iso)).getUTCDay()] + " ";
  return `${day}${hhmm(iso)}`;
};

// ---------------------------------------------------------------- the frame
export function mount(el_, pg) {
  root = el_;
  page = pg;
  root.innerHTML = `${STYLE}<div class="page dash-page">
    <div class="page-h"><div class="grow"><h1>Dashboard</h1><div class="sub" data-k="sub"></div></div>
      <button class="btn" type="button" data-k="customise">${ic("dashboard")}Customise</button>
      <button class="icon-btn" type="button" data-k="gear" aria-label="Display settings" title="Display settings">${ic("settings")}</button></div>
    <div class="dash" data-k="grid"></div>
    <div class="card" data-k="empty" hidden>${empty("k-mode", "chart", "Tracking started", "Your first numbers show up here in a few minutes, and the 7-day chart fills in as the week goes on. Nothing to do in the meantime.",
      `<div class="dash-empty-btns"><button class="btn btn-primary" type="button" data-k="first-block">Set up your first block</button><button class="btn" type="button" data-k="categories">Choose categories for your apps</button></div>`)}</div>
  </div>`;
  grid = q("grid");
  q("gear").addEventListener("click", () => openDisplay());
  q("customise").addEventListener("click", () => { if (data) openCustomise(); });
  q("first-block").addEventListener("click", () => page.go("blocking", { tab: "add" }));
  q("categories").addEventListener("click", () => page.go("screentime", { tab: "apps" }));
  fitter = new ResizeObserver(fit);
  wireIconReady(ld.on, root);
  window.addEventListener("ld:display", () => { if (data) page.reload(); });
  page.every(tick);
}

/** Masonry without holes: the grid's rows are 1 px and each card spans its own height plus the card spacing, so the dense
 *  flow puts every card (in the owner's order) right under the shortest column it fits. Runs when a card's height changes:
 *  data in, a window resize, a card shown again. */
function fit(entries) {
  const gap = parseFloat(getComputedStyle(grid).columnGap) || 0;
  for (const e of entries) {
    const h = e.borderBoxSize[0].blockSize;
    if (h) e.target.style.gridRowEnd = `span ${Math.ceil(h + gap)}`;
  }
}

// ---------------------------------------------------------------- Customise: every card in a list (display_set)
function saveLayout() {
  const body = [data.hidden, data.order];
  savedLayout = savedLayout.then(() => ld.call("display_set", ...body)).catch(() => page.reload());
  return savedLayout;
}

function applyLayout() {
  const shown = data.order.filter((k) => !data.hidden.includes(k));
  for (const [k, c] of cards) c.node.hidden = data.hidden.includes(k);
  let prev = null;
  for (const k of shown) {
    const c = cards.get(k);
    if (!c) continue;
    const want = prev ? prev.nextSibling : grid.firstChild;
    if (c.node !== want) grid.insertBefore(c.node, want);
    prev = c.node;
  }
}

/** A change from the Customise list: the grid follows at once and it is saved; a card shown again (or a reset that shows
 *  one) has no data yet, so the page asks again once it is saved. */
function setLayout(order, hidden) {
  const missing = order.some((k) => !hidden.includes(k) && !cards.has(k));
  data.order = order;
  data.hidden = order.filter((k) => hidden.includes(k));
  applyLayout();
  const saved = saveLayout();
  if (missing) saved.then(() => page.reload());
}

/** The Customise list (the phone's Edit Home): every card with up / down and a switch, and Reset to default. */
function openCustomise() {
  const body = el(`<div class="stack-sm"><p class="small muted">Choose the cards on the Dashboard and their order. Saved at once.</p>
    <div class="cust-rows" data-n="rows"></div></div>`);
  const rows = n(body, "rows");
  const render = () => syncRows(rows, data.order.map((key, i) => ({ key, i })), (r) => {
    const title = esc(data.titles[r.key]);
    return el(`<div class="cust-row" data-card="${r.key}"><span class="name ellip">${title}</span>` +
      `<button class="icon-btn icon-btn-sm" type="button" data-cust="up" aria-label="Move ${title} up">${ic("chev-u")}</button>` +
      `<button class="icon-btn icon-btn-sm" type="button" data-cust="down" aria-label="Move ${title} down">${ic("chev-d")}</button>` +
      `${sw(false, `data-cust="on" aria-label="Show ${title}"`)}</div>`);
  }, (node, r) => {
    node.querySelector('[data-cust="up"]').disabled = r.i === 0;
    node.querySelector('[data-cust="down"]').disabled = r.i === data.order.length - 1;
    node.querySelector('[data-cust="on"]').checked = !data.hidden.includes(r.key);
  });
  body.addEventListener("click", (e) => {
    const b = e.target.closest('[data-cust="up"],[data-cust="down"]');
    if (!b) return;
    const order = [...data.order];
    const i = order.indexOf(b.closest(".cust-row").dataset.card), j = i + (b.dataset.cust === "up" ? -1 : 1);
    if (i < 0 || j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    setLayout(order, data.hidden);
    render();
    if (b.disabled) b.closest(".cust-row").querySelector(`[data-cust="${b.dataset.cust === "up" ? "down" : "up"}"]`).focus();
  });
  body.addEventListener("change", (e) => {
    if (!e.target.matches('[data-cust="on"]')) return;
    const key = e.target.closest(".cust-row").dataset.card;
    setLayout(data.order, e.target.checked ? data.hidden.filter((k) => k !== key) : [...data.hidden, key]);
  });
  render();
  openDialog({ key: "dash-customise", title: "Customise Dashboard", body, buttons: [
    { label: "Reset to default", kind: "text", onClick: () => { setLayout(Object.keys(data.titles), []); render(); return false; } },
    { label: "Done", kind: "primary" },
  ] });
}

// ---------------------------------------------------------------- loading and patching
export async function load() {
  const d = await ld.call("dashboard_get");
  data = d;
  status = d.status;
  setText(q("sub"), subLine(d.date, d.since, d.status.mode));
  q("empty").hidden = !d.empty;
  grid.hidden = d.empty;
  q("customise").hidden = d.empty;
  if (d.empty) return;
  for (const key of d.order) {
    if (d.hidden.includes(key)) continue;
    const info = d.cards[key];
    if (!info) continue;
    let c = cards.get(key);
    if (!c) {
      c = BUILD[key](d.titles[key]);
      cards.set(key, c);
      grid.append(c.node);
      fitter.observe(c.node);
    }
    c.update(info);
  }
  applyLayout();
}

export function patch({ event, payload }) {
  if (event === "usage_tick") {
    const c = cards.get("stats");
    if (c && payload && typeof payload.active_sec === "number") c.active(payload.active_sec);
    return;
  }
  if (event === "status") {
    // Something the cards show changed (not just the clock offset or a waiting update): ask again.
    const next = { ...status, ...payload };
    const key = (s) => JSON.stringify([s.running, s.off, s.pause, s.mode, s.emergency, s.blocks && [s.blocks.count, s.blocks.total]]);
    const changed = key(next) !== key(status);
    status = next;
    if (changed) page.reload();
    return;
  }
  page.reload();                 // blocks_changed / mode_changed
}

function tick() {
  for (const c of cards.values()) if (c.tick && !c.node.hidden) c.tick();
}

export function show() {
  if (data) tick();
}
