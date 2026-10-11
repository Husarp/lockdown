"""R06: the challenge component (js/challenge.js, challenge.html) in Chrome against the real GuardService.

Python issues the phrase and decides the result; these tests only drive the page like a person would: typing (with a
realistic delay, or all at once), the grid, the wait, closed hours, Esc, the standalone window, and the speed numbers.
"""
import time

import pytest

import antibypass as ab
from harness import assert_clean

TITLE = "This loosens your blocks"


class Req:
    """A pending change that records whether it ran or was cancelled (GuardService calls these from Python)."""

    def __init__(self, harness):
        self.h, self.ran, self.cancelled = harness, 0, 0

    def action(self):
        self.ran += 1
        self.h.ctx.db.set_setting("test.applied", "yes")
        return {"saved": self.ran}

    def cancel(self):
        self.cancelled += 1


def configure(harness, **kw):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "length": 30, **kw})


def ask(harness, changes=("Remove YouTube",)):
    req = Req(harness)
    data = harness.ctx.guard.request(list(changes), req.action, req.cancel)["data"]
    assert data["needs_challenge"], "Anti-Bypass should be on for this test"
    return req, data


def applied(harness):
    return harness.db.get_setting("test.applied", "") == "yes"


def open_in_page(page, data):
    page.evaluate("""async (d) => {
        const m = await import('/js/challenge.js');
        window.__res = undefined;
        m.openChallenge(d).then((r) => { window.__res = r === m.CANCELLED ? 'CANCELLED' : r; });
    }""", data)


def result(page, timeout=5000):
    page.wait_for_function("window.__res !== undefined", timeout=timeout)
    return page.evaluate("window.__res")


def phrase_text(page):
    return page.evaluate("document.querySelector('.ch-phrase').textContent")


def wait_ready(page):
    page.wait_for_selector("#dlg textarea.typed")


def type_it(page, text, per_char_ms=45):
    page.focus("#dlg textarea.typed")
    page.keyboard.type(text, delay=per_char_ms)


def eventually(check, seconds=3):
    """Python-side results (a cancel callback ...) arrive a moment after the page closed: wait for them."""
    deadline = time.time() + seconds
    while not check() and time.time() < deadline:
        time.sleep(0.03)
    return check()


def say(capsys, text):
    with capsys.disabled():
        print("\n  " + text)


# ================================================================ phrase mode

def test_header_lines_and_more(shell, page, harness):
    configure(harness)
    shell()
    _, data = ask(harness, [f"Change {i}" for i in range(1, 9)])
    open_in_page(page, data)
    wait_ready(page)
    assert page.inner_text("#dlg h2") == TITLE
    lines = page.eval_on_selector_all("#dlg .ch-chg", "els => els.map(e => e.textContent)")
    assert lines == [f"→Change {i}" for i in range(1, 6)]
    assert page.inner_text("#dlg .ch-more") == "and 3 more"
    assert page.is_disabled("#dlg .dlg-f .btn-primary")
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


def test_paste_drop_and_the_menu_are_blocked(shell, page, harness):
    configure(harness)
    shell()
    _, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    out = page.evaluate("""() => {
        const ta = document.querySelector('#dlg textarea.typed'), res = {};
        const dt = new DataTransfer(); dt.setData('text/plain', 'pasted text');
        for (const [name, ev] of [
            ['paste', new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })],
            ['drop', new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true })],
            ['menu', new MouseEvent('contextmenu', { bubbles: true, cancelable: true })],
            ['copy', new ClipboardEvent('copy', { bubbles: true, cancelable: true })],
            ['beforeinput', new InputEvent('beforeinput', { inputType: 'insertFromPaste', data: 'x', bubbles: true, cancelable: true })],
            ['ctrlv', new KeyboardEvent('keydown', { key: 'v', ctrlKey: true, bubbles: true, cancelable: true })],
        ]) { ta.dispatchEvent(ev); res[name] = ev.defaultPrevented; }
        return res; }""")
    assert out == {"paste": True, "drop": True, "menu": True, "copy": True, "beforeinput": True, "ctrlv": True}
    assert page.input_value("#dlg textarea.typed") == ""


def test_a_wrong_character_shows_the_mistake_and_the_count(shell, page, harness):
    configure(harness)
    shell()
    _, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    P = phrase_text(page)
    assert len(P) == 35                                       # 30 letters in groups of five
    page.focus("#dlg textarea.typed")
    page.keyboard.type(P[:4])
    assert page.inner_text("#dlg .ch-count") == "4 / 35"
    assert page.inner_text("#dlg .ch-msg") == "Paste is off. Mistakes show in red."
    wrong = "#" if P[4] != "#" else "%"
    page.keyboard.type(wrong)
    assert page.inner_text("#dlg .ch-msg") == "Mistake at character 5 - fix it to go on"
    assert "err" in page.get_attribute("#dlg .ch-msg", "class")
    assert page.inner_text("#dlg .phrase .bad") == P[4]                     # the expected character is marked
    assert page.inner_text("#dlg .phrase .done") == P[:4]
    assert page.is_disabled("#dlg .dlg-f .btn-primary")
    page.keyboard.press("Backspace")
    assert page.inner_text("#dlg .ch-msg") == "Paste is off. Mistakes show in red."
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


def test_typing_it_properly_applies_the_change(shell, page, harness, capsys):
    configure(harness)
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    P = phrase_text(page)
    type_it(page, P)                                         # 45 ms a character: a person
    assert page.inner_text("#dlg .ch-count") == f"{len(P)} / {len(P)}"
    assert not page.is_disabled("#dlg .dlg-f .btn-primary")
    page.click("#dlg .dlg-f .btn-primary")
    assert result(page) == {"saved": 1}                      # the change's own result comes back
    assert applied(harness) and req.ran == 1 and req.cancelled == 0
    assert ab.unlocked_until(ab.settings(harness.db), harness.now)
    page.wait_for_function("!document.getElementById('dlg').open")
    assert page.inner_text("#dlg") == ""


def test_typing_it_instantly_is_refused_and_nothing_changes(shell, page, harness):
    configure(harness)
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    P = phrase_text(page)
    page.fill("#dlg textarea.typed", P)                      # all at once (a macro)
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("document.querySelector('#dlg .ch-msg').textContent.startsWith('Too fast')")
    assert page.inner_text("#dlg .ch-msg") == "Too fast - type it yourself."
    assert not applied(harness) and req.ran == 0
    assert page.input_value("#dlg textarea.typed") == ""     # a refused try starts again from nothing
    assert page.evaluate("window.__res") is None             # (still open)
    type_it(page, P)                                         # a person can still pass
    page.click("#dlg .dlg-f .btn-primary")
    assert result(page) == {"saved": 1}
    assert applied(harness)


def test_cancel_button_and_esc_run_the_cancel(shell, page, harness):
    configure(harness)
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    page.click("#dlg .dlg-f .btn:not(.btn-primary)")
    assert result(page) == "CANCELLED"
    page.wait_for_function("window.__res === 'CANCELLED'")
    assert req.cancelled == 1 and req.ran == 0 and not applied(harness)
    # Esc
    req2, data2 = ask(harness)
    open_in_page(page, data2)
    wait_ready(page)
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"
    assert req2.cancelled == 1 and harness.rpc_count("challenge_cancel") == 2
    assert not page.evaluate("document.getElementById('dlg').open")


def test_a_long_phrase_scrolls_inside_the_dialog(shell, page, harness, capsys):
    configure(harness, length=2000)
    shell(1000, 650)
    _, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    P = phrase_text(page)
    assert len(P) == 2399                                      # 2000 letters in groups of five
    box = page.evaluate("""() => { const b = document.querySelector('.ch-phrase'), d = document.getElementById('dlg').getBoundingClientRect();
        return { sh: b.scrollHeight, ch: b.clientHeight, dlg: d.height, top: d.top, bottom: d.bottom, vh: innerHeight, oy: getComputedStyle(b).overflowY }; }""")
    assert box["oy"] == "auto" and box["sh"] > box["ch"] * 2          # the box scrolls ...
    assert box["dlg"] <= box["vh"] and box["top"] >= 0 and box["bottom"] <= box["vh"]    # ... the dialog does not outgrow the window
    assert page.is_visible("#dlg .dlg-f .btn-primary") or page.is_disabled("#dlg .dlg-f .btn-primary")
    # typing follows along: after 1500 characters the box has scrolled
    page.evaluate("""(t) => { const ta = document.querySelector('#dlg textarea.typed'); ta.value = t; ta.dispatchEvent(new Event('input')); }""", P[:1500])
    page.wait_for_function("document.querySelector('.ch-phrase').scrollTop > 100")
    say(capsys, f"2000-char phrase: box {box['ch']:.0f}px of {box['sh']}px, dialog {box['dlg']:.0f}px in a {box['vh']}px window")
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


def test_long_phrase_typing_speed_per_input_event(shell, page, harness, capsys):
    configure(harness, length=2000)
    shell()
    _, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    P = phrase_text(page)
    stats = page.evaluate("""(P) => {
        const ta = document.querySelector('#dlg textarea.typed'), box = document.querySelector('.ch-phrase');
        const kids = [...box.children];
        const ms = [];
        for (let k = 1; k <= 400; k++) {                         // 400 characters, one input event each
            ta.value = P.slice(0, k);
            const t0 = performance.now();
            ta.dispatchEvent(new Event('input'));
            ms.push(performance.now() - t0);
        }
        ta.value = P.slice(0, 400) + (P[400] === '#' ? '%' : '#'); ta.dispatchEvent(new Event('input'));   // and a mistake
        ms.sort((a, b) => a - b);
        return { max: ms[ms.length - 1], p95: ms[Math.floor(ms.length * 0.95)], mean: ms.reduce((a, b) => a + b, 0) / ms.length,
                 same: [...box.children].every((c, i) => c === kids[i]), n: box.children.length, count: document.querySelector('.ch-count').textContent }; }""", P)
    say(capsys, f"typing in a 2000-char phrase: mean {stats['mean']:.2f} ms, p95 {stats['p95']:.2f} ms, max {stats['max']:.2f} ms per input event")
    assert stats["same"] and stats["n"] == 4                  # only the spans' text changed: no re-render
    assert stats["p95"] <= 16 and stats["max"] <= 16
    assert stats["count"] == "400 / 2399"
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


def test_dialog_opens_fast(shell, page, harness, capsys):
    configure(harness)
    shell()
    times = []
    for _ in range(5):
        _, data = ask(harness)
        times.append(page.evaluate("""async (d) => {
            const m = await import('/js/challenge.js');
            const t0 = performance.now();
            m.openChallenge(d);
            while (!document.querySelector('#dlg .ch-phrase')) await new Promise((r) => requestAnimationFrame(r));
            const t = performance.now() - t0;
            document.getElementById('dlg').close();
            await new Promise((r) => setTimeout(r, 30));
            return t; }""", data))
    say(capsys, f"challenge dialog open (call -> phrase rendered): {', '.join(f'{t:.0f}' for t in times)} ms")
    assert sorted(times)[len(times) // 2] <= 100 and max(times) <= 150      # (median is the bar; one slow frame on a busy box is not)


# ================================================================ grid mode

def test_the_grid_walks_through_every_word(shell, page, harness, capsys):
    configure(harness, grid=True, length=30)
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    page.wait_for_selector("#dlg .ch-box")
    boxes = page.query_selector_all("#dlg .ch-box")
    assert len(boxes) == 9
    assert all(b.get_attribute("tabindex") == "-1" for b in boxes)
    assert page.evaluate("document.activeElement.classList.contains('ch-box')") is False   # nothing focused for you
    assert page.is_disabled("#dlg .dlg-f .btn-primary")

    seen_lit, n_words = [], None
    step = 0
    while True:
        label = page.inner_text("#dlg .ch-wi")
        if label == "All words typed.":
            break
        word = page.inner_text("#dlg .ch-chip")
        n_words = int(label.split(" of ")[1])
        assert label == f"Word {step + 1} of {n_words}"
        lit = page.evaluate("[...document.querySelectorAll('#dlg .ch-box')].map((b, i) => b.classList.contains('lit') && !b.disabled ? i : -1).filter((i) => i >= 0)")
        assert len(lit) == 1 and page.evaluate("[...document.querySelectorAll('#dlg .ch-box')].filter((b) => !b.disabled).length") == 1
        if seen_lit:
            assert lit[0] != seen_lit[-1]                     # never the same box twice in a row
        seen_lit.append(lit[0])
        assert page.input_value(f"#dlg .ch-box >> nth={lit[0]}") == ""        # cleared each step
        assert page.inner_text("#dlg .ch-status") == "Click the highlighted box and type the word."
        box = page.locator("#dlg .ch-box").nth(lit[0])
        box.click()
        if step == 0:                                         # a wrong start: red border and the typo message
            wrong = "#"
            page.keyboard.type(wrong)
            assert "bad" in box.get_attribute("class")
            assert page.inner_text("#dlg .ch-status") == "Typo - fix it to go on."
            page.keyboard.press("Backspace")
            assert "bad" not in box.get_attribute("class")
        page.keyboard.type(word[:-1], delay=40)
        assert page.inner_text("#dlg .ch-status") == f"{step} of {n_words} words done."
        page.keyboard.type(word[-1], delay=40)
        step += 1
    assert step == n_words and len(set(seen_lit)) > 1
    assert page.is_hidden("#dlg .ch-chip")
    assert not page.is_disabled("#dlg .dlg-f .btn-primary")
    page.click("#dlg .dlg-f .btn-primary")
    assert result(page) == {"saved": 1}
    assert applied(harness) and req.ran == 1
    say(capsys, f"grid: {n_words} words, boxes used {seen_lit}")


def test_the_lit_box_breathes_in_css_and_pauses_when_hidden(shell, page, harness):
    configure(harness, grid=True)
    shell()
    _, data = ask(harness)
    open_in_page(page, data)
    page.wait_for_selector("#dlg .ch-box.lit")
    assert page.evaluate("getComputedStyle(document.querySelector('#dlg .ch-box.lit')).animationName") == "ch-breathe"
    page.evaluate("document.documentElement.setAttribute('data-hidden', '')")
    assert page.evaluate("getComputedStyle(document.querySelector('#dlg .ch-box.lit')).animationPlayState") == "paused"
    page.evaluate("document.documentElement.removeAttribute('data-hidden')")
    # paste into a box is blocked too
    out = page.evaluate("""() => { const b = document.querySelector('#dlg .ch-box.lit'); const dt = new DataTransfer(); dt.setData('text/plain', 'abc');
        const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }); b.dispatchEvent(ev); return ev.defaultPrevented; }""")
    assert out is True
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


def test_a_grid_macro_is_refused_and_starts_over(shell, page, harness):
    configure(harness, grid=True)
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    page.wait_for_selector("#dlg .ch-box")
    words = []
    while page.inner_text("#dlg .ch-wi") != "All words typed.":
        word = page.inner_text("#dlg .ch-chip")
        lit = page.locator("#dlg .ch-box.lit")
        lit.fill(word)                                        # instantly
        words.append(word)
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("document.querySelector('#dlg .ch-status').textContent.startsWith('Too fast')")
    assert not applied(harness) and req.ran == 0
    assert page.inner_text("#dlg .ch-wi").startswith("Word 1 of")        # back to the first word
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


# ================================================================ waiting and closed

def test_a_wait_after_passing_shows_the_countdown_and_changes_nothing(shell, page, harness):
    configure(harness, wait_min=10)
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    P = phrase_text(page)
    type_it(page, P, per_char_ms=75)                          # slow typing: the countdown must start from Python's time, not ours
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_selector("#dlg .ch-cd")
    assert page.inner_text("#dlg h2") == "Passed - now the wait"
    first = page.inner_text("#dlg .ch-cd")
    assert first in ("10:00", "9:59")
    text = page.inner_text("#dlg .ch-wait-line")
    assert text.startswith("Passed - unlocks in ") and text.endswith("(at 15:52)") or "(at 15:5" in text   # 15:42 + 10 min
    assert not applied(harness) and req.ran == 0 and req.cancelled == 1      # nothing happened; the page's cancel ran
    page.wait_for_function("(t) => document.querySelector('#dlg .ch-cd').textContent !== t", arg=first, timeout=4000)
    assert float(page.evaluate("parseFloat(document.querySelector('#dlg .ch-fill').style.width)")) > 0
    page.click("#dlg .dlg-f .btn-primary")                    # OK
    assert result(page) == "CANCELLED"
    # asking again during the wait never shows the phrase
    req2, data2 = ask(harness)
    open_in_page(page, data2)
    page.wait_for_selector("#dlg .ch-cd")
    assert page.query_selector("#dlg textarea") is None
    assert float(page.evaluate("parseFloat(document.querySelector('#dlg .ch-fill').style.width)")) >= 0
    page.click("#dlg .dlg-f .btn-primary")
    assert result(page) == "CANCELLED"
    assert not applied(harness)


def test_closed_hours_show_when_the_next_chance_is(shell, page, harness):
    configure(harness, phrase=False, hours=True, windows=[{"days": [0], "start": "09:00", "end": "10:00"}])
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    page.wait_for_selector("#dlg .ch-note.closed")
    assert page.inner_text("#dlg h2") == "Closed right now"
    note = page.inner_text("#dlg .ch-note")
    assert "only possible Monday 09:00-10:00" in note and "Next chance: Mon 12 Oct 09:00." in note
    assert page.query_selector("#dlg textarea") is None
    page.click("#dlg .dlg-f .btn-primary")                    # OK
    assert result(page) == "CANCELLED"
    assert req.cancelled == 1 and req.ran == 0


# ================================================================ opening it from outside / helpers

def test_a_challenge_open_push_opens_it_in_the_main_window(shell, page, harness, push):
    configure(harness)
    shell()
    req, data = ask(harness, ["Quit Lockdown"])
    push("challenge_open", data)                              # what the tray's Exit sends
    wait_ready(page)
    assert page.text_content("#dlg .ch-chg") == "→Quit Lockdown"
    push("challenge_open", data)                              # the same ticket again: still one dialog
    page.wait_for_timeout(100)
    assert harness.rpc_count("challenge_begin") == 1
    P = phrase_text(page)
    type_it(page, P)
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("!document.getElementById('dlg').open")
    assert applied(harness) and req.ran == 1


def test_the_needs_challenge_event_opens_it_too(shell, page, harness):
    configure(harness)
    shell()
    req, data = ask(harness)
    page.evaluate("(d) => window.dispatchEvent(new CustomEvent('ld:needs-challenge', { detail: d }))", data)
    wait_ready(page)
    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('dlg').open")
    assert eventually(lambda: req.cancelled == 1)


def test_guarded_returns_the_final_result_in_one_line(shell, page, harness):
    configure(harness)
    shell()
    req, data = ask(harness)
    page.evaluate("""async (d) => {
        const m = await import('/js/challenge.js');
        window.__plain = await m.guarded(Promise.resolve({ fine: 1 }));          // not gated: returned as it is
        window.__res = undefined;
        m.guarded(() => Promise.resolve(d)).then((r) => { window.__res = r === m.CANCELLED ? 'CANCELLED' : r; });
    }""", data)
    assert page.evaluate("window.__plain") == {"fine": 1}
    wait_ready(page)
    type_it(page, phrase_text(page))
    page.click("#dlg .dlg-f .btn-primary")
    assert result(page) == {"saved": 1}
    assert applied(harness)


def test_a_new_request_replaces_the_one_that_is_open(shell, page, harness):
    configure(harness)
    shell()
    req1, data1 = ask(harness, ["First"])
    open_in_page(page, data1)
    wait_ready(page)
    page.evaluate("window.__first = null; window.__res = undefined;")
    req2, data2 = ask(harness, ["Second"])                    # Python cancels the first and keeps only this one
    assert req1.cancelled == 1
    page.evaluate("""async (d) => { const m = await import('/js/challenge.js');
        m.openChallenge(d).then((r) => { window.__second = r === m.CANCELLED ? 'CANCELLED' : r; }); }""", data2)
    page.wait_for_function("(document.querySelector('#dlg .ch-chg')?.textContent || '').includes('Second')")
    assert result(page) == "CANCELLED"                        # the first one's promise ended
    wait_ready(page)
    type_it(page, phrase_text(page))
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("window.__second !== undefined")
    assert page.evaluate("window.__second") == {"saved": 1} and req2.ran == 1 and req1.ran == 0


def test_an_expired_ticket_says_so(shell, page, harness):
    configure(harness)
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    harness.ctx.guard.cancel(data["ticket"])                  # (as if replaced / expired meanwhile)
    page.fill("#dlg textarea.typed", phrase_text(page))
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("document.querySelector('#dlg .ch-msg').textContent.includes('expired')")
    assert page.inner_text("#dlg .dlg-f .btn") == "OK"
    page.click("#dlg .dlg-f .btn")
    assert result(page) == "CANCELLED"


# ================================================================ review fixes: closing early, stacking, the submit race

def open_editor(page):
    """A stand-in for an open editor dialog: a text box with unsaved text, and a record of how it was closed."""
    page.evaluate("""async () => {
        const d = await import('/js/dialog.js');
        window.__editorClosed = [];
        d.openDialog({ key: 'editor', title: 'Editor', body: '<textarea id="ed-text" class="textarea"></textarea>',
                       buttons: [{ label: 'Save', kind: 'primary', value: 'saved' }], onClose: (r) => window.__editorClosed.push(r) });
        const t = document.getElementById('ed-text'); t.value = 'my unsaved edits'; t.focus();
    }""")


def editor_state(page):
    return page.evaluate("""() => ({ text: document.getElementById('ed-text')?.value ?? null,
        focused: document.activeElement?.id ?? null, closed: window.__editorClosed,
        open: [...document.querySelectorAll('dialog[open]')].map((d) => d.id || d.querySelector('h2')?.textContent) })""")


def test_esc_before_the_first_answer_closes_quietly(shell, page, harness):
    configure(harness)
    shell()
    req, data = ask(harness)
    harness.delays["challenge_begin"] = 0.6
    open_in_page(page, data)
    page.wait_for_selector("#dlg .dlg-b")
    page.keyboard.press("Escape")                             # before challenge_begin returns
    assert result(page) == "CANCELLED"
    page.wait_for_timeout(900)                                # the late answer arrives into a closed dialog
    assert page.inner_text("#dlg") == "" and not page.evaluate("document.getElementById('dlg').open")
    assert eventually(lambda: req.cancelled == 1) and req.ran == 0
    assert_clean(page)                                        # no TypeError from the emptied dialog


def test_the_challenge_stacks_over_an_open_editor_and_leaves_it_alone(shell, page, harness):
    configure(harness)
    shell()
    open_editor(page)
    req, data = ask(harness)
    open_in_page(page, data)
    page.wait_for_selector("dialog[open] textarea.typed")
    st = editor_state(page)
    assert st["open"] == ["dlg", TITLE] or len(st["open"]) == 2          # two layers
    assert st["closed"] == [] and st["text"] == "my unsaved edits"      # the editor was not replaced
    assert page.evaluate("document.activeElement.classList.contains('typed')")
    assert page.evaluate("document.querySelector('dialog[open] textarea.typed').closest('dialog') !== document.getElementById('dlg')")
    page.keyboard.press("Escape")                             # Esc closes the challenge only
    assert result(page) == "CANCELLED"
    page.wait_for_function("document.querySelectorAll('dialog[open]').length === 1")
    st = editor_state(page)
    assert st["closed"] == [] and st["text"] == "my unsaved edits" and st["focused"] == "ed-text"
    assert eventually(lambda: req.cancelled == 1) and not applied(harness)
    page.keyboard.press("Escape")                             # and a second Esc is the editor's own
    page.wait_for_function("window.__editorClosed.length === 1")
    assert page.evaluate("document.querySelectorAll('dialog[open]').length") == 0
    assert page.evaluate("document.querySelectorAll('body > dialog').length") == 1       # (the temporary layer is gone)


def test_the_editor_survives_a_pass_and_cancel_button(shell, page, harness):
    configure(harness)
    shell()
    open_editor(page)
    req, data = ask(harness)
    open_in_page(page, data)
    page.wait_for_selector("dialog[open] textarea.typed")
    page.click("dialog.wide[open] .dlg-f .btn:not(.btn-primary)")          # Cancel (the top layer's own button)
    assert result(page) == "CANCELLED"
    page.wait_for_function("document.querySelectorAll('dialog[open]').length === 1")
    assert editor_state(page)["text"] == "my unsaved edits"
    req2, data2 = ask(harness)
    open_in_page(page, data2)
    page.wait_for_selector("dialog[open] textarea.typed")
    P = phrase_text(page)
    page.focus("dialog[open] textarea.typed")
    page.keyboard.type(P, delay=45)
    page.click("dialog.wide[open] .dlg-f .btn-primary")
    assert result(page) == {"saved": 1} and applied(harness) and req2.ran == 1
    page.wait_for_function("document.querySelectorAll('dialog[open]').length === 1")
    st = editor_state(page)
    assert st["closed"] == [] and st["text"] == "my unsaved edits" and st["focused"] == "ed-text"


def test_esc_and_cancel_wait_while_the_answer_is_out(shell, page, harness):
    configure(harness)
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    type_it(page, phrase_text(page))
    harness.delays["challenge_submit"] = 1.5
    page.click("#dlg .dlg-f .btn-primary")                    # Continue: the answer is on its way
    page.wait_for_timeout(150)
    page.keyboard.press("Escape")                             # (a second Esc would be closed by the browser itself: next test)
    page.evaluate("document.querySelector('#dlg .dlg-f .btn:not(.btn-primary)').click()")     # Cancel
    page.evaluate("document.querySelector('#dlg [data-close]').click()")                        # the X
    assert page.evaluate("document.getElementById('dlg').open")
    assert page.evaluate("window.__res") is None
    assert result(page, 5000) == {"saved": 1}                 # Python passed: the caller gets the real result
    assert applied(harness) and req.ran == 1 and req.cancelled == 0
    assert harness.rpc_count("challenge_cancel") == 0
    page.wait_for_function("!document.getElementById('dlg').open")


def test_even_a_forced_close_while_the_answer_is_out_gets_the_real_result(shell, page, harness):
    configure(harness)
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    type_it(page, phrase_text(page))
    harness.delays["challenge_submit"] = 0.9
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_timeout(100)
    for _ in range(5):                                        # (the browser closes a dialog on repeated Esc whatever the page says)
        page.keyboard.press("Escape")
    assert result(page, 5000) == {"saved": 1}                 # still the real result, never CANCELLED
    assert applied(harness) and req.ran == 1 and req.cancelled == 0
    assert harness.rpc_count("challenge_cancel") == 0


def test_a_slow_answer_that_is_a_wait_still_ends_cleanly(shell, page, harness):
    configure(harness, wait_min=10)
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    wait_ready(page)
    type_it(page, phrase_text(page))
    harness.delays["challenge_submit"] = 0.6
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_timeout(100)
    page.keyboard.press("Escape")
    page.wait_for_selector("#dlg .ch-cd")                     # the reply decides: the waiting view
    assert not applied(harness) and req.ran == 0
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


# ================================================================ the standalone window (--challenge)

def test_the_standalone_page_runs_the_same_component(page, harness):
    configure(harness)
    req, data = ask(harness, ["Uninstall Lockdown"])
    page.goto(harness.url(f"/challenge.html?ticket={data['ticket']}"), wait_until="load")
    page.wait_for_selector("#dlg textarea.typed")
    assert page.inner_text("#dlg h2") == TITLE
    assert page.text_content("#dlg .ch-chg") == "→Uninstall Lockdown"
    assert page.evaluate("document.documentElement.dataset.standalone !== undefined")
    type_it(page, phrase_text(page))
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("!document.getElementById('dlg').open")
    assert applied(harness) and req.ran == 1
    page.wait_for_function("window.pywebview", timeout=2000)
    time.sleep(0.2)
    assert harness.rpc_count("challenge_cancel") == 0         # passed: nothing to cancel
    assert_clean(page)


def test_the_standalone_page_cancels_with_esc(page, harness):
    configure(harness)
    req, data = ask(harness, ["Uninstall Lockdown"])
    page.goto(harness.url(f"/challenge.html?ticket={data['ticket']}"), wait_until="load")
    page.wait_for_selector("#dlg textarea.typed")
    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('dlg').open")
    deadline = time.time() + 3
    while req.cancelled == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert req.cancelled == 1 and req.ran == 0
    assert_clean(page)
