"""Own password, authenticator code and recovery code in Chrome, against the real Python Api (see harness.py).

The setup flows on the Anti-Bypass page (the "Forgotten / lost = only uninstalling ..." warning, no recovery code with
a password or an authenticator, the QR code and key shown once, a current code needed to save), the optional recovery
code (made on purpose, shown once and only closed on purpose, removed for free), the step editor's secret rows (no
level, no times, "Not set up yet", a list that can't be saved without its secret) and the challenge dialog's password /
code steps and its "Use recovery code" (passes the whole run, then the NEW code is shown once). Python judges
everything; these tests drive the page like a person."""
import re

import antibypass as ab
import challenge_secrets as cs
from harness import assert_clean  # noqa: F401  (page fixture checks for console errors itself)

from test_challenge import Req, eventually, open_in_page, result  # noqa: F401
from test_page_antibypass import dialog_closed, open_page

PW = "correct horse"
FORGOTTEN = "Forgotten / lost = only uninstalling Lockdown gets you out (your settings are kept for the next install)."
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


def test_setting_a_password_warns_and_gives_no_recovery_code(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    assert page.inner_text('[data-k="sx-pw"]') == "Not set."
    assert page.inner_text('[data-k="sx-rec"]') == "Off (none made)."
    assert FORGOTTEN in page.inner_text('[data-k="sx"]')
    page.click('[data-act="sx-pw-set"]')
    page.wait_for_selector('#dlg[open] [data-k="sx-p1"]')
    assert FORGOTTEN in page.inner_text("#dlg")
    assert page.get_attribute('#dlg [data-k="sx-p1"]', "type") == "password"
    page.fill('#dlg [data-k="sx-p1"]', PW)
    page.fill('#dlg [data-k="sx-p2"]', PW + "!")
    page.click("#dlg .dlg-f .btn-primary")
    assert "not the same" in page.inner_text('#dlg [data-k="sx-err"]')
    page.fill('#dlg [data-k="sx-p2"]', PW)
    page.press('#dlg [data-k="sx-p2"]', "Enter")
    dialog_closed(page)                                            # (no recovery code comes with it)
    assert cs.verify_secret(PW, cs.load(harness.db)["password"]) and "recovery" not in cs.load(harness.db)
    page.wait_for_function("document.querySelector('[data-k=\"sx-pw\"]').textContent.startsWith('Set')")
    assert page.inner_text('[data-act="sx-pw-set"]') == "Change" and page.is_visible('[data-act="sx-pw-x"]')
    assert page.inner_text('[data-k="sx-rec"]') == "Off (none made)." and not page.is_visible('[data-act="sx-rec-x"]')
    assert PW not in page.content()


def test_the_authenticator_setup_shows_a_qr_and_saves_only_with_a_current_code(shell, page, harness):
    lock(harness)
    open_page(shell, page)
    page.click('[data-act="sx-totp-set"]')
    page.wait_for_selector("#dlg[open] .sx-qr svg")
    assert FORGOTTEN in page.inner_text("#dlg")
    key = page.inner_text('#dlg [data-k="sx-key"]').replace(" ", "")
    assert len(key) == 32 and not cs.status(harness.db)["totp"]["set"]
    page.fill('#dlg [data-k="sx-code"]', other_code(code_now(harness, key)))
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("document.querySelector('#dlg [data-k=\"sx-err\"]').textContent.includes(\"doesn't match\")")
    page.fill('#dlg [data-k="sx-code"]', code_now(harness, key))
    page.click("#dlg .dlg-f .btn-primary")
    dialog_closed(page)                                            # (no recovery code comes with it)
    assert "recovery" not in cs.load(harness.db)
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
    assert "Type your Anti-Bypass password." in page.inner_text("#dlg") and FORGOTTEN in page.inner_text("#dlg")
    assert not page.query_selector("#dlg .ch-rec")                 # (no recovery code: nothing offered)
    page.fill("#dlg .ch-sec", "not it")
    page.press("#dlg .ch-sec", "Enter")
    page.wait_for_function("document.querySelector('#dlg .ch-msg').textContent.includes(\"isn't the password\")")
    page.fill("#dlg .ch-sec", PW)
    page.press("#dlg .ch-sec", "Enter")
    page.wait_for_selector("#dlg input.ch-sec.code")
    assert "Step 2 of 2" in page.inner_text("#dlg .ch-prog")
    page.type("#dlg .ch-sec.code", code_now(harness, secret))      # six digits: sent without Enter
    assert result(page) == {"saved": 1} and req.ran == 1


def test_a_recovery_code_is_made_on_purpose_shown_once_and_removed_for_free(shell, page, harness):
    open_page(shell, page)                                         # (Anti-Bypass off: making one asks nothing)
    assert page.inner_text('[data-act="sx-rec-new"]') == "Make a recovery code"
    page.click('[data-act="sx-rec-new"]')
    page.wait_for_selector("#dlg[open]")
    assert "passes one whole Anti-Bypass challenge" in page.inner_text("#dlg")
    page.click("#dlg .dlg-f .btn-primary")
    code = close_recovery(page)
    assert CODE_RE.match(code), code
    assert cs.verify_secret(cs.clean_recovery(code), cs.load(harness.db)["recovery"])
    page.wait_for_function("document.querySelector('[data-k=\"sx-rec\"]').textContent.startsWith('Made ')")
    assert page.inner_text('[data-act="sx-rec-new"]') == "Make a new one" and code not in page.content()
    lock(harness)                                                  # removing it is free even while locked
    page.click('[data-act="sx-rec-x"]')
    page.wait_for_selector("#dlg[open]")
    page.click("#dlg .dlg-f .btn-primary")
    dialog_closed(page)
    assert eventually(lambda: "recovery" not in cs.load(harness.db))
    page.wait_for_function("document.querySelector('[data-k=\"sx-rec\"]').textContent === 'Off (none made).'")


def test_the_recovery_code_passes_the_whole_challenge_and_the_new_one_is_shown_once(shell, page, harness):
    cs.set_password(harness.db, PW, harness.now)
    code = cs.make_recovery(harness.db, harness.now)
    lock(harness, [{"id": "m", "kind": "math", "level": 3, "times": 5}, step("password")])
    shell()
    req, data = ask(harness)
    open_in_page(page, data)
    page.wait_for_selector('#dlg [data-act="ch-userec"]')          # offered at the start of the run
    page.click('#dlg [data-act="ch-userec"]')
    page.wait_for_selector("#dlg input.ch-sec.rec")
    assert "passes one whole Anti-Bypass challenge" in page.inner_text("#dlg")
    page.type("#dlg .ch-sec.rec", "AAAABBBBCCCCDDDD")
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_function("document.querySelector('#dlg .ch-msg').textContent.includes(\"isn't the recovery code\")")
    page.type("#dlg .ch-sec.rec", code.lower())
    page.click("#dlg .dlg-f .btn-primary")
    page.wait_for_selector('#dlg [data-k="ch-newcode"]')
    new = page.inner_text('#dlg [data-k="ch-newcode"]').strip()
    assert CODE_RE.match(new) and new != code
    assert "the old one no longer works" in page.inner_text("#dlg")
    page.keyboard.press("Escape")                                  # (Esc can't lose it)
    assert page.evaluate("document.getElementById('dlg').open") and page.query_selector('#dlg [data-k="ch-newcode"]')
    assert page.evaluate("window.__res === undefined")
    page.click("#dlg .dlg-f .btn-primary")                         # I've saved it
    assert result(page) == {"saved": 1} and req.ran == 1
    stored = cs.load(harness.db)["recovery"]
    assert cs.verify_secret(cs.clean_recovery(new), stored) and not cs.verify_secret(cs.clean_recovery(code), stored)
