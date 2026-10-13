"""Own password, authenticator code and recovery code in Chrome, against the real Python Api (see harness.py).

The setup flows on the Anti-Bypass page (the "Forgotten = ..." warning, the QR code and key shown once, a current code
needed to save, the recovery code shown once and only closed on purpose), the step editor's secret rows (no level, no
times, "Not set up yet", a list that can't be saved without its secret) and the challenge dialog's password / code
steps with "Lost it? Use your recovery code". Python judges everything; these tests drive the page like a person."""
import re

import antibypass as ab
import challenge_secrets as cs
from harness import assert_clean  # noqa: F401  (page fixture checks for console errors itself)

from test_challenge import Req, eventually, open_in_page, result  # noqa: F401
from test_page_antibypass import dialog_closed, open_page

PW = "correct horse"
CODE_RE = re.compile(r"^[2-9A-HJ-NP-Z]{4}(-[2-9A-HJ-NP-Z]{4}){3}$")


def lock(harness, steps=None):
    cfg = {**ab.settings(harness.db), "phrase": True, "length": 30}
    if steps is not None:
        cfg["steps"] = steps
    ab.save(harness.db, cfg)


def step(kind, ident=None):
    return {"id": ident or kind, "kind": kind, "level": 0, "times": 1}


def code_now(harness, secret):
    return cs.totp(cs.secret_key(secret), cs.trusted_epoch(harness.db))


def other_code(code):
    return "000000" if code != "000000" else "111111"


def ask(harness):
    req = Req(harness)
    data = harness.ctx.guard.request(["Remove YouTube"], req.action, req.cancel)["data"]
    assert data["needs_challenge"]
    return req, data


def close_recovery(page):
    """The recovery code dialog: Esc does not close it (the code would be gone), its button does. -> the code shown."""
    page.wait_for_selector('#dlg[open] [data-k="sx-code-shown"]', timeout=5000)
    code = page.inner_text('#dlg [data-k="sx-code-shown"]').strip()
    page.keyboard.press("Escape")
    assert page.evaluate("document.getElementById('dlg').open")
    page.click("#dlg .dlg-f .btn-primary")
    dialog_closed(page)
    return code


def test_setting_a_password_warns_and_shows_the_recovery_code_once(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    assert page.inner_text('[data-k="sx-pw"]') == "Not set."
    assert page.is_disabled('[data-act="sx-rec-new"]')
    page.click('[data-act="sx-pw-set"]')
    page.wait_for_selector('#dlg[open] [data-k="sx-p1"]')
    assert "Forgotten = only the recovery code gets you out - uninstalling Lockdown asks this challenge too." \
        in page.inner_text("#dlg")
    assert page.get_attribute('#dlg [data-k="sx-p1"]', "type") == "password"
    page.fill('#dlg [data-k="sx-p1"]', PW)
    page.fill('#dlg [data-k="sx-p2"]', PW + "!")
    page.click("#dlg .dlg-f .btn-primary")
    assert "not the same" in page.inner_text('#dlg [data-k="sx-err"]')
    page.fill('#dlg [data-k="sx-p2"]', PW)
    page.press('#dlg [data-k="sx-p2"]', "Enter")
    code = close_recovery(page)
    assert CODE_RE.match(code), code
    assert cs.verify_secret(PW, cs.load(harness.db)["password"])
    assert cs.verify_secret(cs.clean_recovery(code), cs.load(harness.db)["recovery"])
    page.wait_for_function("document.querySelector('[data-k=\"sx-pw\"]').textContent.startsWith('Set')")
    assert page.inner_text('[data-act="sx-pw-set"]') == "Change" and page.is_visible('[data-act="sx-pw-x"]')
    assert page.inner_text('[data-k="sx-rec"]').startswith("Made ")
    assert code not in page.content() and PW not in page.content()          # shown once, kept nowhere on the page


def test_the_authenticator_setup_shows_a_qr_and_saves_only_with_a_current_code(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.click('[data-act="sx-totp-set"]')
    page.wait_for_selector("#dlg[open] .sx-qr svg")
    key = page.inner_text('#dlg [data-k="sx-key"]').replace(" ", "")
    assert len(key) == 32 and not cs.status(harness.db)["totp"]["set"]
    page.fill('#dlg [data-k="sx-code"]', other_code(code_now(harness, key)))
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("document.querySelector('#dlg [data-k=\"sx-err\"]').textContent.includes(\"doesn't match\")")
    page.fill('#dlg [data-k="sx-code"]', code_now(harness, key))
    page.click("#dlg .dlg-f .btn-primary")
    code = close_recovery(page)                                    # (the first secret: its recovery code)
    assert CODE_RE.match(code)
    assert eventually(lambda: cs.status(harness.db)["totp"]["set"]) and cs.load(harness.db)["totp"]["secret"] == key
    page.wait_for_function("document.querySelector('[data-k=\"sx-totp\"]').textContent.startsWith('Set up')")
    assert page.inner_text('[data-act="sx-totp-set"]') == "Set up again"


def test_secret_rows_have_no_level_or_times_and_cant_be_saved_unset(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.click('[data-act="sl-add"]')
    row = '[data-step]:nth-child(2)'
    page.select_option(f"{row} [data-f=kind]", "password")
    assert page.inner_text(f'{row} [data-k="sl-fixed"]') == "Not set up yet - set it up below"
    assert not page.query_selector(f"{row} [data-f=level]") and not page.query_selector(f"{row} .stepper")
    page.click('[data-act="save-steps"]')
    page.wait_for_function("document.querySelector('[data-k=\"err\"]').textContent.includes('Set up your password first')")
    assert [s["kind"] for s in ab.settings(harness.db)["steps"]] == ["phrase"]
    cs.set_password(harness.db, PW, harness.now)
    page.reload(wait_until="load")                                # (the page reads it again)
    page.wait_for_function("document.querySelector('[data-k=\"sx-pw\"]')?.textContent.startsWith('Set')", timeout=8000)
    page.click('[data-act="sl-add"]')
    page.select_option(f"{row} [data-f=kind]", "password")
    assert page.inner_text(f'{row} [data-k="sl-fixed"]') == "Asked once"
    page.click('[data-act="save-steps"]')                          # adding a step: stricter, after a yes
    page.wait_for_selector("#dlg[open]")
    page.click("#dlg .dlg-f .btn-primary")
    dialog_closed(page)
    assert eventually(lambda: [s["kind"] for s in ab.settings(harness.db)["steps"]] == ["phrase", "password"])


def test_the_dialog_asks_the_password_then_the_authenticator_code(shell, page, harness):
    cs.set_password(harness.db, PW, harness.now)
    secret = cs.new_totp_secret()
    cs.set_totp(harness.db, secret, 0, harness.now)
    lock(harness, [step("password"), step("totp")])
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    page.wait_for_selector('#dlg input.ch-sec[type="password"]')
    assert "Type your Anti-Bypass password." in page.inner_text("#dlg")
    assert page.query_selector("#dlg .ch-rec")                     # (a recovery code exists: the link is there)
    page.fill("#dlg .ch-sec", "not it")
    page.press("#dlg .ch-sec", "Enter")
    page.wait_for_function("document.querySelector('#dlg .ch-msg').textContent.includes(\"isn't the password\")")
    page.fill("#dlg .ch-sec", PW)
    page.press("#dlg .ch-sec", "Enter")
    page.wait_for_selector("#dlg input.ch-sec.code")
    assert "Step 2 of 2" in page.inner_text("#dlg .ch-prog")
    page.type("#dlg .ch-sec.code", code_now(harness, secret))      # six digits: sent without Enter
    assert result(page) == {"saved": 1} and req.ran == 1


def test_the_recovery_code_stands_in_for_the_password_and_is_used_up(shell, page, harness):
    code = cs.set_password(harness.db, PW, harness.now)
    lock(harness, [step("password")])
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    page.wait_for_selector("#dlg .ch-rec")
    page.click("#dlg .ch-rec")
    page.wait_for_selector("#dlg input.ch-sec.rec")
    assert "used up when you finish" in page.inner_text("#dlg")
    page.type("#dlg .ch-sec.rec", "AAAABBBBCCCCDDDD")
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("document.querySelector('#dlg .ch-msg').textContent.includes(\"isn't the recovery code\")")
    page.type("#dlg .ch-sec.rec", code.lower())
    page.click("#dlg .dlg-f .btn-primary")
    assert result(page) == {"saved": 1}
    assert not cs.status(harness.db)["recovery"]["exists"]
