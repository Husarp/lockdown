"""C2: the challenge dialog with a LIST of steps (js/challenge.js + js/challenge_kinds.js) against the real GuardService.

Python makes and judges every puzzle (challenges.Run); these tests drive the page like a person: the sum drawn on a
canvas, the Memory grid (Ready, then taps), Sorting taps, a wrong answer (new puzzle, finished work stays), Cancel.
The answers are read from the Run that lives inside the GuardService (this test is Python, the page never sees them).
"""
import time

import antibypass as ab
from harness import assert_clean  # noqa: F401  (page fixture checks for console errors itself)

from test_challenge import Req, applied, eventually, open_in_page, result, wait_ready  # noqa: F401


def step(kind, level, times=1, ident=None):
    return {"id": ident or f"{kind[:2]}{level}x{times}", "kind": kind, "level": level, "times": times}


def configure(harness, steps, **kw):
    ab.save(harness.db, {**ab.settings(harness.db), "phrase": True, "steps": steps, **kw})


def ask(harness, changes=("Remove YouTube",)):
    req = Req(harness)
    data = harness.ctx.guard.request(list(changes), req.action, req.cancel)["data"]
    assert data["needs_challenge"], "Anti-Bypass should be on for this test"
    return req, data


class Clock:
    """The guard's monotonic clock, under the test's hand: 'a person took a moment' = advance()."""

    def __init__(self, harness):
        self.t = 1000.0
        harness.ctx.guard._mono = lambda: self.t

    def advance(self, sec=30):
        self.t += sec


def server_run(harness):
    return harness.ctx.guard._open.run


def start(harness, page, shell, steps, **kw):
    configure(harness, steps, **kw)
    shell()
    clock = Clock(harness)
    req, data = ask(harness)
    open_in_page(page, data)
    return req, clock


def prog(page):
    return page.inner_text("#dlg .ch-prog") if page.query_selector("#dlg .ch-prog") else ""


def strip(page):
    return page.eval_on_selector_all("#dlg .ch-stp", "els => els.map(e => [e.className.replace('ch-stp ', ''), e.textContent.trim()])")


def wait_kind(page, selector):
    page.wait_for_selector("#dlg " + selector)


def answer_math(page, clock, harness, value=None):
    wait_kind(page, ".ch-ans")
    clock.advance()
    puzzle = server_run(harness).puzzle
    page.fill("#dlg .ch-ans", str(puzzle.answer if value is None else value))
    page.press("#dlg .ch-ans", "Enter")


def do_ready_and_tap(page, clock, harness, cells=None):
    wait_kind(page, ".ch-cell")
    clock.advance()
    page.click("#dlg .dlg-f .btn-primary")                       # Ready
    page.wait_for_function("!document.querySelector('#dlg .ch-cell.lit')")
    for c in (server_run(harness).puzzle.answer if cells is None else cells):
        page.click(f'#dlg .ch-cell[data-i="{c}"]')


def type_phrase(page, clock, harness):
    wait_ready(page)
    clock.advance()
    page.fill("#dlg textarea.typed", server_run(harness).puzzle.view["phrase"])
    page.click("#dlg .dlg-f .btn-primary")


def test_a_three_step_run_goes_to_the_end(shell, page, harness):
    req, clock = start(harness, page, shell, [step("math", 0), step("memory", 0), step("phrase", 30)])
    wait_kind(page, ".ch-ans")
    assert prog(page) == "Step 1 of 3 · Math, Easy"
    assert strip(page) == [["cur", "Math ×1 0/1"], ["todo", "Memory ×1"], ["todo", "Phrase ×1"]]
    assert page.inner_text("#dlg h2") == "This loosens your blocks"
    assert "Passing all of them unlocks changes like these for 5 minutes." in page.inner_text("#dlg .ch-lead")
    # the sum is a picture: a canvas with ink, and its text is nowhere in the page
    expr = server_run(harness).puzzle.view["expr"]
    assert expr not in page.inner_text("#dlg")
    assert expr not in page.content()
    assert page.evaluate("""() => { const c = document.querySelector('#dlg canvas.ch-pic');
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        return c.width > 40 && d.some((v, i) => i % 4 === 3 && v > 100); }""")
    answer_math(page, clock, harness)

    wait_kind(page, ".ch-cell")
    assert prog(page) == "Step 2 of 3 · Memory grid, Easy"
    assert strip(page)[0] == ["done", "✓ Math ×1"] and strip(page)[1][0] == "cur"
    assert page.locator("#dlg .ch-cell.lit").count() == 4 and page.locator("#dlg .ch-cell").count() == 16
    assert page.inner_text("#dlg .dlg-f .btn-primary") == "Ready"
    do_ready_and_tap(page, clock, harness)

    wait_ready(page)
    assert prog(page) == "Step 3 of 3 · Typed phrase, Short"
    assert [s[0] for s in strip(page)] == ["done", "done", "cur"]
    type_phrase(page, clock, harness)
    assert result(page) == {"saved": 1}
    assert applied(harness) and req.ran == 1 and req.cancelled == 0
    assert not page.evaluate("document.getElementById('dlg').open")


def test_a_single_phrase_once_has_no_progress_or_strip(shell, page, harness):
    start(harness, page, shell, [step("phrase", 30)])
    wait_ready(page)
    assert page.query_selector("#dlg .ch-prog") is None and page.query_selector("#dlg .ch-strip") is None
    assert page.inner_text("#dlg .ch-lead") == "Type this phrase to continue (no pasting). Passing unlocks changes like these for 5 minutes."
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


def test_one_step_done_several_times_counts_rounds(shell, page, harness):
    req, clock = start(harness, page, shell, [step("math", 0, 3)])
    wait_kind(page, ".ch-ans")
    assert prog(page) == "Math, Easy · 1 of 3"
    assert strip(page) == [["cur", "Math ×3 0/3"]]
    answer_math(page, clock, harness)
    page.wait_for_function("document.querySelector('#dlg .ch-prog').textContent.includes('2 of 3')")
    assert strip(page) == [["cur", "Math ×3 1/3"]]


def test_a_wrong_sum_gives_a_new_puzzle_and_keeps_what_is_done(shell, page, harness):
    req, clock = start(harness, page, shell, [step("math", 1, 3)])
    answer_math(page, clock, harness)                        # one right
    page.wait_for_function("document.querySelector('#dlg .ch-prog').textContent.includes('2 of 3')")
    old = server_run(harness).puzzle
    answer_math(page, clock, harness, value=old.answer + 1)  # then a wrong one
    page.wait_for_function("document.querySelector('#dlg .ch-msg').textContent.includes('Not right')")
    assert page.inner_text("#dlg .ch-msg") == "Not right - here is a new one."
    assert "err" in page.get_attribute("#dlg .ch-msg", "class")
    assert server_run(harness).puzzle is not old             # a new puzzle ...
    assert "2 of 3" in prog(page) and strip(page)[0][1].endswith("1/3")     # ... and the right one still counts
    assert page.input_value("#dlg .ch-ans") == ""
    # too fast is wrong as well, with its own line
    page.fill("#dlg .ch-ans", str(server_run(harness).puzzle.answer))
    page.press("#dlg .ch-ans", "Enter")
    page.wait_for_function("document.querySelector('#dlg .ch-msg').textContent.includes('Too fast')")
    assert page.inner_text("#dlg .ch-msg") == "Too fast - take a proper look. Here is a new one."
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"
    assert eventually(lambda: req.cancelled == 1) and not applied(harness)


def test_the_answer_box_takes_digits_only_and_no_paste(shell, page, harness):
    start(harness, page, shell, [step("math", 0)])
    wait_kind(page, ".ch-ans")
    page.focus("#dlg .ch-ans")
    page.keyboard.type("1a2-b3 ")
    assert page.input_value("#dlg .ch-ans") == "123"
    assert page.inner_text("#dlg .ch-msg") == "Type the answer and press Enter. Paste is off."
    out = page.evaluate("""() => {
        const el = document.querySelector('#dlg .ch-ans'), res = {};
        const dt = new DataTransfer(); dt.setData('text/plain', '999');
        for (const [name, ev] of [
            ['paste', new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })],
            ['drop', new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true })],
            ['beforeinput', new InputEvent('beforeinput', { inputType: 'insertFromPaste', data: '9', bubbles: true, cancelable: true })],
            ['ctrlv', new KeyboardEvent('keydown', { key: 'v', ctrlKey: true, bubbles: true, cancelable: true })],
        ]) { el.dispatchEvent(ev); res[name] = ev.defaultPrevented; }
        return res; }""")
    assert out == {"paste": True, "drop": True, "beforeinput": True, "ctrlv": True}
    assert page.is_enabled("#dlg .dlg-f .btn-primary")
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


def test_find_x_shows_x_equals(shell, page, harness):
    # a Very hard sum is a product or a find-x one: ask until it is find-x (a fresh run each time)
    start(harness, page, shell, [step("math", 3)])
    wait_kind(page, ".ch-ans")
    for _ in range(40):
        if server_run(harness).puzzle.view["find_x"]:
            break
        page.keyboard.press("Escape")
        assert result(page) == "CANCELLED"
        _, data = ask(harness)
        open_in_page(page, data)
        wait_kind(page, ".ch-ans")
    assert server_run(harness).puzzle.view["find_x"]
    assert page.inner_text("#dlg .ch-eq") == "x ="
    page.keyboard.press("Escape")


def test_memory_stays_until_ready_then_hides(shell, page, harness):
    req, clock = start(harness, page, shell, [step("memory", 1)])
    wait_kind(page, ".ch-cell")
    assert page.locator("#dlg .ch-cell").count() == 25 and page.locator("#dlg .ch-cell.lit").count() == 6
    lit = server_run(harness).puzzle.view["lit"]
    assert sorted(int(i) for i in page.eval_on_selector_all("#dlg .ch-cell.lit", "els => els.map(e => e.dataset.i)")) == sorted(lit)
    time.sleep(0.8)                                          # nothing hides it by itself
    assert page.locator("#dlg .ch-cell.lit").count() == 6
    clock.advance()
    page.click("#dlg .dlg-f .btn-primary")                   # Ready
    page.wait_for_function("!document.querySelector('#dlg .ch-cell.lit')")
    assert page.inner_text("#dlg .ch-count") == "0 / 6"
    assert [b.inner_text() for b in page.query_selector_all("#dlg .dlg-f .btn")] == ["Cancel", "Start over"]
    page.click(f'#dlg .ch-cell[data-i="{lit[0]}"]')
    assert page.inner_text("#dlg .ch-count") == "1 / 6"
    assert "pick" in page.get_attribute(f'#dlg .ch-cell[data-i="{lit[0]}"]', "class")
    page.click("#dlg .dlg-f .btn:last-child")                # Start over clears the taps
    assert page.inner_text("#dlg .ch-count") == "0 / 6"
    for c in lit:
        page.click(f'#dlg .ch-cell[data-i="{c}"]')
    assert result(page) == {"saved": 1}


def test_a_tap_that_was_not_lit_misses_at_once(shell, page, harness):
    req, clock = start(harness, page, shell, [step("memory", 0, 2)])
    wait_kind(page, ".ch-cell")
    first = server_run(harness).puzzle
    unlit = next(i for i in range(16) if i not in first.view["lit"])
    clock.advance()
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("!document.querySelector('#dlg .ch-cell.lit')")
    page.click(f'#dlg .ch-cell[data-i="{unlit}"]')
    assert page.inner_text("#dlg .ch-msg") == "Missed - a new pattern"
    page.wait_for_function("document.querySelectorAll('#dlg .ch-cell.lit').length === 4 && !document.querySelector('#dlg .ch-cell.miss')")
    assert server_run(harness).puzzle is not first            # a new pattern, shown (lit) again, waiting for Ready
    assert page.inner_text("#dlg .ch-msg") == "Missed - a new pattern"
    assert prog(page) == "Memory grid, Easy · 1 of 2"
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


def test_the_hard_grid_numbers_the_squares_and_wants_the_order(shell, page, harness):
    req, clock = start(harness, page, shell, [step("memory", 2)])
    wait_kind(page, ".ch-cell")
    lit = server_run(harness).puzzle.view["lit"]
    assert page.locator("#dlg .ch-cell").count() == 25 and len(lit) == 8
    assert [page.inner_text(f'#dlg .ch-cell[data-i="{c}"]') for c in lit] == [str(i) for i in range(1, 9)]
    clock.advance()
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("!document.querySelector('#dlg .ch-cell.lit')")
    assert page.inner_text("#dlg .ch-cell[data-i='0']") == ""                # numbers go with the pattern
    page.click(f'#dlg .ch-cell[data-i="{lit[0]}"]')
    assert page.inner_text(f'#dlg .ch-cell[data-i="{lit[0]}"]') == "1"       # your own tap order
    for c in lit[1:]:
        page.click(f'#dlg .ch-cell[data-i="{c}"]')
    assert result(page) == {"saved": 1}


def test_a_lit_square_out_of_order_misses_on_the_hard_grid(shell, page, harness):
    req, clock = start(harness, page, shell, [step("memory", 2)])
    wait_kind(page, ".ch-cell")
    first = server_run(harness).puzzle
    lit = first.view["lit"]
    clock.advance()
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("!document.querySelector('#dlg .ch-cell.lit')")
    page.click(f'#dlg .ch-cell[data-i="{lit[1]}"]')                           # lit, but not first
    page.wait_for_function("document.querySelectorAll('#dlg .ch-cell.lit').length === 8")
    assert server_run(harness).puzzle is not first
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


def tap_ids(page, ids):
    for i in ids:
        page.click(f'#dlg .ch-tile[data-id="{i}"]')


def test_sorting_taps_into_a_row_and_sends_at_the_end(shell, page, harness):
    req, clock = start(harness, page, shell, [step("sort", 0), step("sort", 2)])
    wait_kind(page, ".ch-tile")
    assert prog(page) == "Step 1 of 2 · Sorting, Easy"
    puzzle = server_run(harness).puzzle
    assert page.locator("#dlg .ch-pool .ch-tile").count() == 6
    assert page.eval_on_selector_all("#dlg .ch-pool .ch-tile", "els => els.map(e => e.textContent)") == puzzle.view["items"]
    clock.advance()
    order = puzzle.answer
    tap_ids(page, order[:3])
    assert page.locator("#dlg .ch-sorta .ch-tile").count() == 3 and page.locator("#dlg .ch-pool .ch-tile").count() == 3
    page.click("#dlg .dlg-f .btn:last-child")                # Clear starts the row again
    assert page.locator("#dlg .ch-sorta .ch-tile").count() == 0 and page.locator("#dlg .ch-pool .ch-tile").count() == 6
    tap_ids(page, order)
    page.wait_for_function("document.querySelector('#dlg .ch-prog').textContent.includes('Step 2 of 2')")
    assert strip(page)[0][0] == "done"


def test_hard_sorting_reshuffles_the_rest_but_keeps_the_item_numbers(shell, page, harness):
    req, clock = start(harness, page, shell, [step("sort", 2)])
    wait_kind(page, ".ch-tile")
    puzzle = server_run(harness).puzzle
    items = puzzle.view["items"]
    assert len(items) == 10
    clock.advance()
    order = puzzle.answer
    seen_orders = set()
    for k, i in enumerate(order[:-1]):
        page.click(f'#dlg .ch-pool .ch-tile[data-id="{i}"]')
        ids = page.eval_on_selector_all("#dlg .ch-pool .ch-tile", "els => els.map(e => e.dataset.id)")
        assert sorted(int(x) for x in ids) == sorted(order[k + 1:])           # same tiles, same ids ...
        for el_id in ids:                                                       # ... each still showing its own text
            assert page.inner_text(f'#dlg .ch-pool .ch-tile[data-id="{el_id}"]') == items[int(el_id)]
        seen_orders.add(tuple(ids))
    assert len(seen_orders) > 3                                                # (the order moves between taps)
    page.click(f'#dlg .ch-pool .ch-tile[data-id="{order[-1]}"]')
    assert result(page) == {"saved": 1}


def test_a_wrong_order_gives_a_new_set(shell, page, harness):
    req, clock = start(harness, page, shell, [step("sort", 0)])
    wait_kind(page, ".ch-tile")
    puzzle = server_run(harness).puzzle
    clock.advance()
    tap_ids(page, list(reversed(puzzle.answer)))
    page.wait_for_function("document.querySelector('#dlg .ch-msg').textContent.includes('Not right')")
    assert server_run(harness).puzzle is not puzzle
    assert page.locator("#dlg .ch-pool .ch-tile").count() == 6 and page.locator("#dlg .ch-sorta .ch-tile").count() == 0
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"


def test_cancel_button_and_x_drop_the_run(shell, page, harness):
    req, clock = start(harness, page, shell, [step("math", 0), step("phrase", 30)])
    answer_math(page, clock, harness)
    wait_ready(page)                                          # on the second step now
    page.click("#dlg .dlg-f .btn:first-child")                # Cancel
    assert result(page) == "CANCELLED"
    assert eventually(lambda: req.cancelled == 1) and not applied(harness) and req.ran == 0
    # the next time starts at step 1 again
    req2, data = ask(harness)
    page.evaluate("window.__res = undefined")
    open_in_page(page, data)
    wait_kind(page, ".ch-ans")
    assert prog(page) == "Step 1 of 2 · Math, Easy"
    page.click("#dlg [data-close]")                           # the X
    assert result(page) == "CANCELLED"
    assert eventually(lambda: req2.cancelled == 1)


def test_title_note_and_plain_from_begin(shell, page, harness):
    configure(harness, [step("math", 0)])
    shell()
    Clock(harness)
    guard = harness.ctx.guard
    real = guard.begin

    def begin(ticket):
        out = real(ticket)
        out["data"].update(title="Dismiss the bedtime screen", note="Do this to dismiss it.", plain=True)
        return out
    guard.begin = begin
    req, data = ask(harness)
    open_in_page(page, data)
    wait_kind(page, ".ch-ans")
    assert page.inner_text("#dlg h2") == "Dismiss the bedtime screen"
    assert page.inner_text("#dlg .ch-lead") == "Work it out and type the answer. Do this to dismiss it."
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"

    def begin_plain(ticket):                                  # plain without a note: never a word about unlocking
        out = real(ticket)
        out["data"]["plain"] = True
        return out
    guard.begin = begin_plain
    req, data = ask(harness)
    page.evaluate("window.__res = undefined")
    open_in_page(page, data)
    wait_kind(page, ".ch-ans")
    assert page.inner_text("#dlg h2") == "This loosens your blocks"
    assert page.inner_text("#dlg .ch-lead") == "Work it out and type the answer."
    page.keyboard.press("Escape")
    assert result(page) == "CANCELLED"
