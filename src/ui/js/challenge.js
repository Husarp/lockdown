// The Anti-Bypass challenge: ONE component for the main window, the overlay and the uninstaller's standalone window.
// Python issues and checks everything (webapi/guard.py): this file only shows what challenge_begin returns, collects
// what is typed and hands it to challenge_submit. It never decides that a challenge passed - the "client check" here
// (red mistake, enabled Continue) is cosmetic, Python compares again, times the typing and runs the stored change.
//
//   openChallenge(reply)   reply = {ticket, changes, more} from a gated call (or just the ticket). Resolves with the
//                          change's own result once it passed, or CANCELLED (cancel, Esc, wait set, closed hours).
//   guarded(call)          `await guarded(ld.call("some_loosening_method", ...))` - if the answer is needs_challenge it
//                          opens the challenge and returns the final result, otherwise the answer itself.
//   CANCELLED              what both resolve with when nothing was changed.
// A `challenge_open` push (tray Exit, tray Pause ...) and the shell's "ld:needs-challenge" event open it too.
import { ld } from "./bridge.js";
import { openDialog } from "./dialog.js";
import { esc } from "./ui.js";

export const CANCELLED = Symbol("challenge-cancelled");
const BLOCKED_INPUT = new Set(["insertFromPaste", "insertFromDrop", "insertFromPasteAsQuotation", "insertFromYank"]);
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const pad = (n) => String(n).padStart(2, "0");

// ---------------------------------------------------------------- plain helpers (node tests)

/** How many leading characters of `typed` equal `phrase`. */
export function matchLen(typed, phrase) {
  const n = Math.min(typed.length, phrase.length);
  let i = 0;
  while (i < n && typed.charCodeAt(i) === phrase.charCodeAt(i)) i++;
  return i;
}

/** {i: correct characters, bad: a wrong character follows, complete: exactly the phrase}. */
export function phraseState(typed, phrase) {
  const i = matchLen(typed, phrase);
  const bad = typed.length > i;
  return { i, bad, complete: !bad && i === phrase.length };
}

/** One word of the grid: "done" (exact), "ok" (a correct start) or "typo". */
export function wordState(text, word) {
  if (text === word) return "done";
  return word.startsWith(text) ? "ok" : "typo";
}

/** The next box 0..8, random and never `prev` (-1 = none yet). `rnd` returns [0, 1). */
export function pickBox(prev, rnd = Math.random) {
  if (prev < 0 || prev > 8) return Math.min(8, Math.floor(rnd() * 9));
  let k = Math.min(7, Math.floor(rnd() * 8));
  if (k >= prev) k++;
  return k;
}

/** "2026-10-11T15:42:17" (naive local) -> ms read as UTC; only differences of two such values mean anything. */
export function parseNaive(iso) {
  const m = /^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?/.exec(iso || "");
  return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) : NaN;
}

export const clockOf = (iso) => (/[T ](\d\d:\d\d)/.exec(iso || "") || [])[1] || "";

/** Seconds as m:ss (h:mm:ss from an hour on). */
export function countdownText(sec) {
  sec = Math.max(0, Math.ceil(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** "14:00" on the same day as `nowMs`, else "Mon 12 Oct 14:00". */
export function whenText(iso, nowMs) {
  const t = parseNaive(iso);
  if (Number.isNaN(t)) return "";
  const hm = clockOf(iso);
  if (String(iso).slice(0, 10) === String(nowMs || "").slice(0, 10)) return hm;
  const d = new Date(t);
  return `${DAY[d.getUTCDay()]} ${d.getUTCDate()} ${MONTH[d.getUTCMonth()]} ${hm}`;
}

// ---------------------------------------------------------------- the dialog

let active = null;      // {ticket, promise}

/** Open the challenge for a ticket. Asking again for the ticket that is already open returns the same promise; a
 *  different ticket (Python only keeps the newest) first closes the old challenge, then takes its place. */
export function openChallenge(input) {
  const info = typeof input === "string" ? { ticket: input } : (input || {});
  if (typeof info.ticket !== "string" || !info.ticket) return Promise.resolve(CANCELLED);
  if (active && active.ticket === info.ticket) return active.promise;
  if (active) {
    const prev = active;
    prev.close();
    return prev.promise.then(() => openChallenge(info));
  }
  return run(info);
}

export async function guarded(call) {
  const reply = await (typeof call === "function" ? call() : call);
  return reply && typeof reply === "object" && reply.needs_challenge ? openChallenge(reply) : reply;
}

function changesHtml(info) {
  const lines = (info.changes || []).map((c) => `<div class="ch-chg"><b>\u2192</b><span>${esc(c)}</span></div>`).join("");
  const more = info.more > 0 ? `<div class="ch-more">and ${info.more} more</div>` : "";
  return lines || more ? `<div class="ch-changes">${lines}${more}</div>` : "";
}

function run(info) {
  const ticket = info.ticket;
  let finish;
  const promise = new Promise((r) => { finish = r; });
  const me = { ticket, promise };
  let settled = false;          // Python is done with the ticket (passed, or it told us it is gone)
  let outcome = CANCELLED;
  let untick = null;
  let raf = 0;
  let begun = { now: "", at: 0 };    // Python's "now" at challenge_begin and our clock then (the fallback to count down from)
  let closed = false;           // the dialog is gone: answers that arrive later are dropped
  let inflight = false;         // challenge_submit is out: Python may pass it any moment, so Esc / Cancel / X wait for the reply

  const handle = openDialog({     // stack: its own layer, so an editor dialog underneath is left exactly as it was
    key: "challenge:" + ticket, title: "This loosens your blocks", wide: true, dismissable: () => !inflight, stack: true,
    body: changesHtml(info) + '<p class="small muted">One moment\u2026</p>', onClose,
  });
  if (!handle) {                // (no dialog could be opened: the change does not happen)
    ld.call("challenge_cancel", ticket).catch(() => {});
    return Promise.resolve(CANCELLED);
  }
  active = me;
  me.close = () => { if (!inflight) handle.close(); };
  const dlg = handle.el, body = handle.body;
  const q = (sel) => body.querySelector(sel);

  // Esc does nothing while a submit is out (the dialog's cancel event is also refused; this keeps the browser's own
  // "second Esc closes it anyway" rule from firing)
  const holdEsc = (e) => { if (e.key === "Escape" && inflight) e.preventDefault(); };
  dlg.addEventListener("keydown", holdEsc, true);

  // paste, drop and the context menu are off for anything typed here
  body.addEventListener("paste", (e) => e.preventDefault(), true);
  body.addEventListener("drop", (e) => e.preventDefault(), true);
  body.addEventListener("dragover", (e) => e.preventDefault(), true);
  body.addEventListener("contextmenu", (e) => e.preventDefault(), true);
  body.addEventListener("copy", (e) => e.preventDefault(), true);
  body.addEventListener("cut", (e) => e.preventDefault(), true);
  body.addEventListener("beforeinput", (e) => { if (BLOCKED_INPUT.has(e.inputType)) e.preventDefault(); }, true);
  body.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "v" || e.key === "V")) e.preventDefault();
    if (e.shiftKey && e.key === "Insert") e.preventDefault();
  }, true);

  function onClose() {
    closed = true;
    if (untick) { untick(); untick = null; }
    cancelAnimationFrame(raf);
    if (!inflight) conclude();      // (shut by force while Python is checking: the reply decides, see submit)
  }
  function conclude() {
    dlg.removeEventListener("keydown", holdEsc, true);
    const result = settled ? outcome : CANCELLED;
    const done = () => { if (active === me) active = null; finish(result); };
    if (settled) return done();
    settled = true;
    ld.call("challenge_cancel", ticket).catch(() => {}).then(done);
  }

  function view(title, html, buttons) {
    if (closed) return null;
    dlg.querySelector(".dlg-h h2").textContent = title;
    body.innerHTML = changesHtml(begun.info || info) + html;
    let foot = dlg.querySelector(".dlg-f");
    if (!foot) { foot = document.createElement("div"); foot.className = "dlg-f"; dlg.append(foot); }
    foot.textContent = "";
    for (const b of buttons) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn" + (b.primary ? " btn-primary" : "");
      btn.textContent = b.label;
      if (b.disabled) btn.disabled = true;
      btn.addEventListener("click", b.onClick);
      if (b.primary) btn.dataset.primary = "1";
      foot.append(btn);
    }
    return foot;
  }
  const onlyOk = (foot) => {            // the ticket is gone: all that is left is OK
    foot.textContent = "";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-primary";
    btn.textContent = "OK";
    btn.addEventListener("click", () => handle.close());
    foot.append(btn);
  };
  const closeBtn = (label = "OK") => ({ label, primary: true, onClick: () => handle.close() });
  const cancelBtn = { label: "Cancel", onClick: () => { if (!inflight) handle.close(); } };

  // ---- what comes back from Python ------------------------------------------------------
  async function begin() {
    let data;
    try {
      data = await ld.call("challenge_begin", ticket);
    } catch (err) {
      view("This loosens your blocks", `<p class="ch-err">${esc(err.message || "That didn't work.")}</p>`, [closeBtn()]);
      return;
    }
    if (closed) return;
    begun = { now: data.now, at: performance.now(), info: data };
    if (data.mode === "phrase") showPhrase(data);
    else if (data.mode === "grid") showGrid(data);
    else if (data.mode === "waiting") await showWaiting(data.waiting_until, false);
    else showClosed(data);
  }

  /** The typing went to Python. Resolves true when the challenge is over (passed or waiting), false to keep typing. */
  async function submit(typed, onError) {
    let res;
    inflight = true;
    try {
      res = await ld.call("challenge_submit", ticket, typed);
    } catch (err) {
      inflight = false;
      if (closed) return conclude();
      const msg = err.message || "That didn't work.";
      if (/aren't possible right now|nothing to type/.test(msg)) return begin();     // the state changed: show the right view
      onError(msg, /expired/.test(msg));
      return;
    }
    inflight = false;
    settled = true;                                     // the ticket is used up, whatever follows
    if (res && res.waiting_until) return closed ? conclude() : showWaiting(res.waiting_until, true);
    outcome = res ? res.result : undefined;
    if (closed) conclude(); else handle.close();
  }

  // ---- phrase ---------------------------------------------------------------------------
  function showPhrase(data) {
    const P = data.phrase;
    const foot = view("This loosens your blocks", `
      <p class="small muted">Type this phrase to continue (no pasting). Passing unlocks changes like these for ${data.unlock_min} minutes.</p>
      <div class="phrase ch-phrase"><span class="done"></span><i class="ch-cur"></i><span class="bad"></span><span class="rest"></span></div>
      <textarea class="textarea typed" rows="3" placeholder="Start typing\u2026" spellcheck="false" autocomplete="off" autocapitalize="off" aria-label="Type the phrase"></textarea>
      <div class="meter"><i class="ch-fill" style="width:0"></i></div>
      <div class="hstack small"><span class="muted grow ch-msg">Paste is off. Mistakes show in red.</span><span class="num muted ch-count">0 / ${P.length}</span></div>`,
      [cancelBtn, { label: "Continue", primary: true, disabled: true, onClick: go }]);
    const go_ = foot.lastChild;
    const box = q(".phrase"), done = q(".done"), bad = q(".bad"), rest = q(".rest"), cur = q(".ch-cur");
    const ta = q("textarea"), msg = q(".ch-msg"), count = q(".ch-count"), fill = q(".ch-fill");
    for (const s of [done, bad, rest]) s.append(document.createTextNode(""));
    rest.firstChild.nodeValue = P;
    let last = { i: 0, bad: false, n: -1 };
    let busy = false;

    function paint() {
      const t = ta.value, st = phraseState(t, P);
      if (st.i !== last.i || st.bad !== last.bad) {          // only the spans that changed are written
        done.firstChild.nodeValue = P.slice(0, st.i);
        bad.firstChild.nodeValue = st.bad ? (P[st.i] || " ") : "";
        rest.firstChild.nodeValue = P.slice(st.i + (st.bad ? 1 : 0));
        fill.style.width = (P.length ? (100 * st.i) / P.length : 0) + "%";
        if (!raf) raf = requestAnimationFrame(() => { raf = 0; keepInView(); });
      }
      if (st.i !== last.i || st.bad !== last.bad || t.length !== last.n) {
        const text = st.bad ? `Mistake at character ${st.i + 1} - fix it to go on` : "Paste is off. Mistakes show in red.";
        if (msg.textContent !== text) { msg.textContent = text; msg.classList.toggle("err", st.bad); }
        const c = `${st.i} / ${P.length}`;
        if (count.textContent !== c) count.textContent = c;
      }
      go_.disabled = !st.complete || busy;
      last = { i: st.i, bad: st.bad, n: t.length };
    }
    function keepInView() {                                  // a long phrase scrolls inside its box, following the typing
      const top = cur.offsetTop, h = box.clientHeight;
      if (top < box.scrollTop + 24 || top > box.scrollTop + h - 40) box.scrollTop = Math.max(0, top - h / 2);
    }
    function go() {
      if (go_.disabled) return;
      busy = true;
      go_.disabled = true;
      submit(ta.value, (text, gone) => {
        busy = false;
        ta.value = "";                                       // a refused try starts again from nothing
        last = { i: -1, bad: false, n: -1 };
        paint();
        msg.textContent = text;
        msg.classList.add("err");
        if (gone) onlyOk(foot); else ta.focus();
      });
    }
    ta.addEventListener("input", paint);
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); if (!go_.disabled) go(); }
    });
    ta.focus();
  }

  // ---- 3x3 grid -------------------------------------------------------------------------
  function showGrid(data) {
    const words = data.grid_words || [];
    const foot = view("This loosens your blocks", `
      <p class="small muted">Type each word into the highlighted box (click it first, no pasting). Passing unlocks changes like these for ${data.unlock_min} minutes.</p>
      <div class="ch-ghead"><span class="small muted ch-wi"></span><span class="ch-chip"></span><span class="grow"></span><span class="small muted ch-status"></span></div>
      <div class="ch-board">${'<input class="input ch-box" tabindex="-1" autocomplete="off" spellcheck="false" autocapitalize="off" disabled aria-label="Word box">'.repeat(9)}</div>`,
      [cancelBtn, { label: "Continue", primary: true, disabled: true, onClick: go }]);
    const go_ = foot.lastChild;
    const boxes = [...body.querySelectorAll(".ch-box")];
    const wi = q(".ch-wi"), chip = q(".ch-chip"), status = q(".ch-status");
    const typedWords = [];
    let index = 0, lit = -1, busy = false;

    const say = (text, err) => { status.textContent = text; status.classList.toggle("err", !!err); };
    function step() {
      if (index >= words.length) {
        wi.textContent = "All words typed.";
        chip.hidden = true;
        for (const b of boxes) { b.value = ""; b.disabled = true; b.classList.remove("lit", "bad"); }
        say("", false);
        go_.disabled = busy;
        return;
      }
      lit = pickBox(lit);
      boxes.forEach((b, k) => {
        b.value = "";
        b.disabled = k !== lit;
        b.classList.toggle("lit", k === lit);
        b.classList.remove("bad");
      });
      wi.textContent = `Word ${index + 1} of ${words.length}`;
      chip.hidden = false;
      chip.textContent = words[index];
      say("Click the highlighted box and type the word.", false);
      if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
    }
    boxes.forEach((b, k) => b.addEventListener("input", () => {
      if (k !== lit) return;
      const text = b.value.trim(), st = wordState(text, words[index]);
      if (st === "done") { typedWords.push(text); index++; step(); }
      else if (st === "typo") { b.classList.add("bad"); say("Typo - fix it to go on.", true); }
      else { b.classList.remove("bad"); say(`${index} of ${words.length} words done.`, false); }
    }));
    function go() {
      if (go_.disabled) return;
      busy = true;
      go_.disabled = true;
      submit(typedWords.slice(), (text, gone) => {
        busy = false;
        typedWords.length = 0;                               // a refused try starts again from the first word
        index = 0;
        lit = -1;
        step();
        go_.disabled = true;
        say(text, true);
        if (gone) onlyOk(foot);
      });
    }
    step();
  }

  // ---- waiting / closed -----------------------------------------------------------------
  async function showWaiting(waitingUntil, justPassed) {
    // Python's own "now" (antibypass_status) is what the wait counts down from - never our clock plus the typing time
    let nowMs = parseNaive(begun.now) + (performance.now() - begun.at), total = 0;
    try {
      const s = await ld.call("antibypass_status");
      if (s && s.now && !Number.isNaN(parseNaive(s.now))) nowMs = parseNaive(s.now);
      if (!justPassed && s && s.wait_min) total = s.wait_min * 60;     // opened again later: the whole wait is a setting
    } catch (_) { /* the bar then starts from now */ }
    if (closed) return;
    let left0 = (parseNaive(waitingUntil) - nowMs) / 1000;
    left0 = Number.isNaN(left0) ? 0 : Math.max(0, left0);
    total = Math.max(total, left0);
    const at = performance.now();
    const minutes = Math.max(1, Math.ceil(left0 / 60));
    const unlock = begun.info && begun.info.unlock_min ? begun.info.unlock_min : 5;
    view("Passed - now the wait", `
      <div class="ch-wait-line"><b>Passed - unlocks in <span class="num ch-cd">${countdownText(left0)}</span> (at ${esc(clockOf(waitingUntil))})</b></div>
      <div class="meter"><i class="ch-fill" style="width:0"></i></div>
      <div class="ch-note pending">You passed the challenge. Changes like this are possible from ${esc(clockOf(waitingUntil))} - about ${minutes} min from now - and then for ${unlock} minutes.
Nothing has changed yet - come back then and make the change.</div>`, [closeBtn()]);
    const cd = q(".ch-cd"), fill = q(".ch-fill");
    const update = () => {
      const left = Math.max(0, left0 - (performance.now() - at) / 1000);
      const t = countdownText(left);
      if (cd.textContent !== t) cd.textContent = t;
      fill.style.width = (total > 0 ? Math.min(100, (100 * (total - left)) / total) : 100) + "%";
      if (left <= 0 && untick) { untick(); untick = null; }
    };
    update();
    untick = ld.everySecond(update);
  }

  function showClosed(data) {
    const nxt = data.next_open ? whenText(data.next_open, data.now) : "";
    view("Closed right now", `<div class="ch-note closed">Changes like this are only possible ${esc(data.hours_text || "during your allowed hours")}.${nxt ? `\nNext chance: ${esc(nxt)}.` : ""}</div>`, [closeBtn()]);
  }

  begin();
  return promise;
}

// ---------------------------------------------------------------- wiring in a page (not in node tests)
if (typeof window !== "undefined" && window.addEventListener) {
  // the stylesheet comes with the component, so any page that imports it gets the look
  if (document && !document.querySelector('link[href$="css/challenge.css"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = new URL("../css/challenge.css", import.meta.url).href;
    document.head.append(link);
  }
  window.addEventListener("ld:needs-challenge", (e) => { openChallenge(e.detail); });
  ld.on("challenge_open", (payload) => { openChallenge(payload); });
}
