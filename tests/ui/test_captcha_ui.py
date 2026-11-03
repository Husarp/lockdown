"""Captcha pictures and automatic progression in the real challenge dialog and step editor."""
import pytest

from test_challenge import result
from test_challenge_kinds_ui import answer_math, prog, server_run, start, step, wait_kind


@pytest.mark.parametrize("level,length", [(0, 4), (1, 5), (2, 6)])
def test_captcha_picture_blocks_paste_and_submits_once(shell, page, harness, level, length):
    req, clock = start(harness, page, shell, [step("captcha", level, 2), step("math", 0)])
    wait_kind(page, ".ch-captcha-input")
    assert page.evaluate("""() => {
        const img = document.querySelector('#dlg .ch-captcha-image');
        return img.complete && img.naturalWidth > 100 && img.src.startsWith('data:image/png;base64,');
    }""")
    assert page.evaluate("Array.from(document.querySelectorAll('#dlg .dlg-f .btn'), b => b.textContent)") == ["Cancel"]
    for event in ("paste", "drop", "contextmenu", "insertFromPaste", "insertFromDrop", "insertFromYank"):
        assert page.evaluate("""(kind) => {
            const input = document.querySelector('#dlg .ch-captcha-input');
            const event = kind.startsWith('insert')
                ? new InputEvent('beforeinput', {inputType: kind, bubbles: true, cancelable: true})
                : new Event(kind, {bubbles: true, cancelable: true});
            return !input.dispatchEvent(event) && input.value === '';
        }""", event)
    assert harness.rpc_count("challenge_submit") == 0
    secret = server_run(harness).puzzle.answer
    assert secret not in page.content()
    clock.advance()
    old_image = server_run(harness).puzzle.view["captcha_image"]
    page.fill("#dlg .ch-captcha-input", "0" * length)
    page.wait_for_function("document.querySelector('#dlg .ch-msg').classList.contains('err')")
    assert server_run(harness).done == 0 and not req.ran
    assert server_run(harness).puzzle.view["captcha_image"] != old_image
    assert page.inner_text("#dlg .ch-msg") == "Not right - here is a new one."
    for done in range(2):
        secret = server_run(harness).puzzle.answer.lower()
        clock.advance()
        harness.delays["challenge_submit"] = 0.15
        page.evaluate("""(value) => {
            const input = document.querySelector('#dlg .ch-captcha-input');
            input.value = value;
            input.dispatchEvent(new Event('input'));
            input.dispatchEvent(new Event('input'));
            input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
        }""", secret)
        if done == 0:
            page.wait_for_function("document.querySelector('#dlg .ch-prog').textContent.includes('2 of 2')")
            assert server_run(harness).done == 1
        else:
            wait_kind(page, ".ch-ans")
    assert harness.rpc_count("challenge_submit") == 3 and not req.ran
    assert prog(page) == "Step 2 of 2 · Math, Easy"
    answer_math(page, clock, harness)
    assert result(page) == {"saved": 1} and req.ran == 1


def test_captcha_editor_uses_server_levels_times_and_keeps_rows(shell, page):
    shell()
    saved = page.evaluate("""async () => {
        const {ld} = await import('/js/bridge.js');
        const {stepListEditor} = await import('/js/steplist.js');
        const options = (await ld.call('antibypass_get')).challenge;
        const editor = stepListEditor({steps: [{id: 'cap', kind: 'math', level: 3, times: 20}], options});
        document.body.append(editor.el);
        const kind = editor.el.querySelector('[data-f="kind"]');
        kind.value = 'captcha';
        kind.dispatchEvent(new Event('change', {bubbles: true}));
        const levels = Array.from(editor.el.querySelector('[data-f="level"]').options, o => [o.value, o.text]);
        const rows = editor.get();
        editor.set(rows);
        const result = {rows: editor.get(), levels, time: editor.el.querySelector('[data-k="sl-time"]').textContent};
        editor.el.remove();
        return result;
    }""")
    assert saved == {"rows": [{"id": "cap", "kind": "captcha", "level": 2, "times": 20}],
                     "levels": [["0", "Easy"], ["1", "Medium"], ["2", "Hard"]], "time": "about 5 min"}
