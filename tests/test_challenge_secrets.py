"""Own password, authenticator code (TOTP) and recovery code: the Anti-Bypass access kinds (challenge_secrets.py, the
step kinds in challenges.SECRET_KINDS, the guard and the Anti-Bypass page's Api).

What is proven here: the password and the recovery code are kept only as salted scrypt hashes; the codes match RFC 6238
(its own test vectors), one step either side is accepted and a used code never again; no recovery code comes with a
password or an authenticator; the optional recovery code is made only through the challenge, shown once, passes ONE
whole run (any kinds) and is then used up with a new one made and shown at once; removing it is free; it is never
offered in a practice run or on the bedtime screen; the stricter / looser rules (adding is free, changing or removing
needs the challenge, a list can't be saved with a secret that isn't set up); backups leave every secret out and a
restore keeps this PC's; the App log names what happened but never a secret; the bedtime screen never gets a secret
kind. (The uninstaller's challenge without the secret steps: test_rebuild_challenge_cli.py.)"""
import base64
import json
import random
from datetime import datetime

import pytest

import antibypass as ab
import backup
import challenge_secrets as cs
import challenges as ch
import reminders
import webapi
from webapi import core
from webapi.guard import GuardService

NOW = datetime(2026, 10, 13, 10, 0, 0)
T0 = 1_760_000_000.0                       # the trusted unix time the tests start at (a fixed point, settable)
RFC_KEY = b"12345678901234567890"          # RFC 6238 appendix B, SHA1
RFC_VECTORS = [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"), (1234567890, "89005924"),
               (2000000000, "69279037"), (20000000000, "65353130")]
PW = "correct horse"


@pytest.fixture(autouse=True)
def _quick(tmp_path, monkeypatch):
    """A light scrypt for speed (its real cost is checked in its own test) and a trusted clock under the test's hand."""
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")
    monkeypatch.setattr(cs, "SCRYPT", {"n": 2 ** 8, "r": 8, "p": 1})
    epoch = {"t": T0}
    monkeypatch.setattr(cs, "trusted_epoch", lambda db: epoch["t"])
    return epoch


@pytest.fixture
def epoch(_quick):
    return _quick


@pytest.fixture
def ctx(tmp_path):
    c = webapi.make_context(tmp_path / "t.db", clock=lambda db: NOW)
    c.guard = GuardService(lambda: c.db, lambda db: NOW, rng=random.Random(5))
    mono = {"t": 1000.0}
    c.guard._mono = lambda: mono["t"]
    c.mono = mono
    return c


@pytest.fixture
def api(ctx):
    return core.build_api(ctx)


def data(reply):
    assert reply["ok"], reply
    return reply["data"]


def error(reply):
    assert not reply["ok"], reply
    return reply["error"]


def needs_ticket(reply):
    d = data(reply)
    assert d.get("needs_challenge") and d.get("ticket"), d
    return d["ticket"]


def locked(ctx, steps=None, **kw):
    """Anti-Bypass on: a loosening change must pass the challenge (one 30-character phrase unless `steps`)."""
    cfg = {**ab.settings(ctx.db), "phrase": True, "length": 30, **kw}
    if steps is not None:
        cfg["steps"] = steps
    ab.save(ctx.db, cfg)


def step(kind, ident=None, level=0, times=1):
    return {"id": ident or kind, "kind": kind, "level": level, "times": times}


def code_at(secret, t):
    return cs.totp(cs.secret_key(secret), t)


def stored_secret(ctx):
    return cs.load(ctx.db)["totp"]["secret"]


def answer_for(ctx, view, password=PW, epoch=None):
    """What a person would type for the puzzle that is up (the Run inside the guard knows the effort answers)."""
    run = ctx.guard._open.run
    kind = run.puzzle.kind
    if kind == "phrase":
        return view["grid_words"] if view["mode"] == "grid" else view["phrase"]
    if kind == "password":
        return password
    if kind == "totp":
        return code_at(stored_secret(ctx), epoch["t"])
    return run.puzzle.answer


def solve(ctx, api, ticket, epoch=None, password=PW, recovery=None):
    """Do the whole challenge like a person (time passes between answers); with `recovery` the code is typed at the
    start instead. -> the last reply's data."""
    view = data(api.challenge_begin(ticket))
    while True:
        ctx.mono["t"] += 60
        if epoch is not None:
            epoch["t"] += 31                    # (a fresh code each time: an accepted one never works again)
        if recovery:
            typed = {"recovery": recovery}
        else:
            typed = answer_for(ctx, view, password, epoch)
        out = data(api.challenge_submit(ticket, typed))
        if out.get("passed"):
            return out
        view = out["next"]


def events(ctx):
    return [tuple(r) for r in ctx.db.conn.execute("SELECT kind, text FROM app_events ORDER BY id").fetchall()]


# ================================================================ hashing

def test_the_password_is_kept_as_a_salted_slow_scrypt_hash_and_compared_safely(monkeypatch):
    monkeypatch.setattr(cs, "SCRYPT", {"n": 2 ** 15, "r": 8, "p": 1})        # the real cost
    one, two = cs.hash_secret(PW), cs.hash_secret(PW)
    assert one["kdf"] == "scrypt" and (one["n"], one["r"], one["p"]) == (2 ** 15, 8, 1)
    assert len(bytes.fromhex(one["salt"])) == 16 and len(bytes.fromhex(one["hash"])) == 32
    assert one["salt"] != two["salt"] and one["hash"] != two["hash"]          # salted: the same password, other hashes
    assert PW not in json.dumps(one)
    assert cs.verify_secret(PW, one) and cs.verify_secret(PW, two)
    assert not cs.verify_secret("correct horsE", one) and not cs.verify_secret("", one)
    assert not cs.verify_secret(None, one) and not cs.verify_secret(123, one)
    assert not cs.verify_secret(PW, {**one, "hash": "00" * 32}) and not cs.verify_secret(PW, {**one, "kdf": "md5"})
    assert not cs.verify_secret(PW, {**one, "n": 2 ** 30}) and not cs.verify_secret(PW, {}) and not cs.verify_secret(PW, None)


def test_a_new_password_is_4_to_200_printable_characters_kept_exactly():
    assert cs.check_password_text(" a b ") == " a b "
    for bad in ("", "   ", "abc", "x" * 201, "tab\there", None, 1234):
        with pytest.raises(ValueError):
            cs.check_password_text(bad)


# ================================================================ authenticator codes

def test_totp_matches_the_rfc_6238_test_vectors():
    for t, want in RFC_VECTORS:
        assert cs.totp(RFC_KEY, t, digits=8) == want
        assert cs.totp(RFC_KEY, t) == want[-6:]                  # 6 digits: the same number, mod 10^6
    secret = base64.b32encode(RFC_KEY).decode().rstrip("=")
    assert cs.secret_key(secret) == RFC_KEY and cs.secret_key(secret.lower()) == RFC_KEY
    assert cs.match_code(secret, "287082", 59) == 1               # (59 s is time step 1)


def test_one_step_either_side_is_accepted_and_a_used_or_older_code_never_again():
    secret = cs.new_totp_secret()
    assert len(cs.secret_key(secret)) == 20 and "=" not in secret
    t = T0 + 7
    now = int(t // 30)
    assert cs.match_code(secret, code_at(secret, t), t) == now
    assert cs.match_code(secret, code_at(secret, t - 30), t) == now - 1
    assert cs.match_code(secret, code_at(secret, t + 30), t) == now + 1
    assert cs.match_code(secret, code_at(secret, t - 60), t) is None
    assert cs.match_code(secret, code_at(secret, t + 60), t) is None
    assert cs.match_code(secret, code_at(secret, t), t, last=now) is None              # used: never again
    assert cs.match_code(secret, code_at(secret, t - 30), t, last=now) is None         # older than the last one
    assert cs.match_code(secret, code_at(secret, t + 30), t, last=now) == now + 1
    assert cs.match_code(secret, " " + code_at(secret, t)[:3] + " " + code_at(secret, t)[3:], t) == now
    for bad in ("12345", "1234567", "abcdef", "", None, 123456, ["123456"]):
        assert cs.match_code(secret, bad, t) is None


def test_the_qr_code_carries_the_otpauth_link(monkeypatch):
    uri = cs.otpauth_uri("ABCDEFGH", "My PC")
    assert uri == "otpauth://totp/Lockdown:My%20PC?secret=ABCDEFGH&issuer=Lockdown&algorithm=SHA1&digits=6&period=30"
    svg = cs.qr_svg(uri)
    assert svg.startswith("<svg") and "#000" in svg and "#fff" in svg
    import builtins
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda n, *a, **k: (_ for _ in ()).throw(ImportError(n))
                        if n == "segno" else real(n, *a, **k))
    assert cs.qr_svg(uri) == ""                                    # no segno: the key text is the way to set it up
    assert cs.group_key("ABCDEFGHIJKL") == "ABCD EFGH IJKL"


# ================================================================ recovery codes

def test_a_recovery_code_is_16_signs_in_4_groups_without_look_alikes_and_typed_loosely():
    code = cs.new_recovery_code()
    assert len(code) == 19 and [len(g) for g in code.split("-")] == [4, 4, 4, 4]
    assert not set(code.replace("-", "")) & set("01OI")
    assert cs.clean_recovery(code.lower().replace("-", " ")) == code.replace("-", "")
    assert cs.clean_recovery(code) == code.replace("-", "")
    for bad in ("", "ABCD-EFGH", code + "A", code.replace(code[0], "0"), None, 5):
        assert cs.clean_recovery(bad) == ""


def test_no_recovery_code_comes_with_a_password_or_an_authenticator(ctx):
    db = ctx.db
    assert cs.set_password(db, PW, NOW) is None
    assert cs.set_totp(db, cs.new_totp_secret(), 5, NOW) is None
    assert cs.status(db)["recovery"] == {"exists": False, "made": None} and "recovery" not in cs.load(db)
    code = cs.make_recovery(db, NOW)                                # (only on purpose - the page asks the challenge)
    assert cs.status(db)["recovery"] == {"exists": True, "made": "2026-10-13 10:00:00"}
    cs.remove(db, "password")
    cs.remove(db, "totp")
    assert cs.verify_secret(cs.clean_recovery(code), cs.load(db)["recovery"])   # (it doesn't depend on them)
    cs.remove(db, "recovery")
    assert not cs.status(db)["recovery"]["exists"]


def test_status_never_holds_a_secret_or_a_hash(ctx):
    cs.set_password(ctx.db, PW, NOW)
    code = cs.make_recovery(ctx.db, NOW)
    secret = cs.new_totp_secret()
    cs.set_totp(ctx.db, secret, 5, NOW)
    text = json.dumps(cs.status(ctx.db))
    stored = cs.load(ctx.db)
    for hidden in (PW, secret, code, code.replace("-", ""), stored["password"]["hash"], stored["recovery"]["hash"],
                   stored["password"]["salt"]):
        assert hidden not in text
    assert cs.status(ctx.db)["password"] == {"set": True, "since": "2026-10-13 10:00:00"}
    assert PW not in ctx.db.get_setting(cs.SECRETS_KEY) and code not in ctx.db.get_setting(cs.SECRETS_KEY)


# ================================================================ the step kinds

def test_secret_kinds_are_anti_bypass_only_asked_once_without_a_level():
    rows = [step("password"), step("totp", "t")]
    assert ch.validate_steps(rows, ch.AB_KINDS) == rows
    with pytest.raises(ValueError, match="Math, Memory grid, Sorting or Typed phrase"):
        ch.validate_steps(rows)                                     # the bedtime list: never
    for bad in ({**rows[0], "times": 2}, {**rows[0], "level": 1}):
        with pytest.raises(ValueError, match="asked once"):
            ch.validate_steps([bad], ch.AB_KINDS)
    assert ch.read_steps(rows) == []                                # the bedtime screen reads none of them
    assert ch.read_steps([{**rows[0], "times": 7, "level": 3}], ch.AB_KINDS) == [step("password")]
    assert [k["id"] for k in ch.options()["kinds"]] == ["math", "memory", "sort", "phrase"]
    secret = [k for k in ch.options(ch.AB_KINDS)["kinds"] if k.get("secret")]
    assert [(k["id"], k["levels"], k["times"]) for k in secret] == [("password", [], 1), ("totp", [], 1)]
    assert ch.step_label(rows[0]) == "Own password"
    assert ch.steps_text([step("math", "m", 2, 3), rows[0]]) == "Math (Hard) ×3, then Own password"
    assert ch.estimate(rows)["seconds"] == 28
    assert reminders.general_steps({"challenge_steps": rows}) == ch.default_bedtime_steps()


def test_an_old_config_reads_as_before_and_a_stored_secret_step_is_kept():
    assert ab.steps_of({"phrase": True, "length": 45}) == ch.default_steps(45)
    assert ab.steps_of({"steps": [step("math", "m"), step("password")]}) == [step("math", "m"), step("password")]
    # a password step whose password isn't set up (a restored backup, an edited database) is skipped, never asked
    cfg = ab.with_defaults({"phrase": True, "steps": [step("math", "m"), step("password")]})
    assert ab.asked_steps(cfg, set()) == [step("math", "m")]
    assert ab.asked_steps(cfg, {"password"}) == cfg["steps"]
    assert ab.asked_steps(ab.with_defaults({"phrase": True, "length": 90, "steps": [step("totp")]}), set()) \
        == ch.default_steps(90)


def test_looser_rules_row_by_row():
    saved = [step("math", "m", 1, 3), step("password", "p")]
    assert not ch.steps_looser(saved, saved + [step("totp", "t")])              # adding: free
    assert not ch.steps_looser(saved, list(reversed(saved)))                     # moving: free
    assert ch.steps_looser(saved, saved[:1])                                     # removing: the challenge
    assert ch.steps_looser(saved, [saved[0], {**saved[1], "kind": "math"}])      # another kind: the challenge
    assert ch.steps_looser([step("totp", "t")], [step("password", "t")])


# ================================================================ a run with secret steps

def checker_for(ctx):
    return cs.Checker(lambda: ctx.db)


def test_a_run_checks_the_password_and_the_code_and_keeps_the_step_until_right(ctx, epoch):
    cs.set_password(ctx.db, PW, NOW)
    secret = cs.new_totp_secret()
    cs.set_totp(ctx.db, secret, 0, NOW)
    run = ch.Run([step("password"), step("totp", "t")], kinds=ch.AB_KINDS, secrets=checker_for(ctx),
                 clock=lambda: ctx.mono["t"])
    assert run.view()["kind"] == "password" and run.view()["recovery"] is False      # (no code: nothing offered)
    assert run.answer("wrong one") == ch.REFUSED and run.last_refused == "password" and run.index == 0
    assert run.answer(PW) == ch.RIGHT and run.view()["kind"] == "totp"
    code = code_at(secret, epoch["t"])
    assert run.answer("000000" if code != "000000" else "111111") == ch.REFUSED and run.last_refused == "totp"
    assert run.answer(code) == ch.PASSED
    # the same code again (another run) is refused: it was used
    again = ch.Run([step("totp", "t")], kinds=ch.AB_KINDS, secrets=checker_for(ctx))
    assert again.answer(code) == ch.REFUSED
    with pytest.raises(ValueError):
        ch.Run([step("password")], kinds=ch.AB_KINDS)              # (no checker: such a run can't be made)


def test_the_recovery_code_passes_the_whole_run_and_a_new_one_takes_its_place(ctx, epoch):
    cs.set_password(ctx.db, PW, NOW)
    code = cs.make_recovery(ctx.db, NOW)
    rows = [step("math", "m", 3, 5), step("password"), step("phrase", "s0", 250)]
    run = ch.Run(rows, kinds=ch.AB_KINDS, secrets=checker_for(ctx), clock=lambda: ctx.mono["t"],
                 rng=random.Random(1), recovery=True)
    assert run.view()["recovery"] is True and run.view()["kind"] == "math"
    assert run.answer({"recovery": "AAAA-BBBB-CCCC-DDDD"}) == ch.REFUSED and run.last_refused == "recovery"
    assert run.answer({"recovery": code.lower(), "x": 1}) == ch.WRONG              # (nothing else may come with it)
    assert run.answer({"recovery": code.lower()}) == ch.PASSED and run.finished    # every step, any kind: passed
    new = run.new_code
    assert cs.clean_recovery(new) and new != code
    checker = checker_for(ctx)
    assert not cs.verify_secret(cs.clean_recovery(code), cs.load(ctx.db)["recovery"])   # the old one is used up
    assert cs.verify_secret(cs.clean_recovery(new), cs.load(ctx.db)["recovery"])   # the new one is the one now
    # wrong codes count as wrong tries: after a few, each next one has to wait (a right one too)
    later = ch.Run([step("password")], kinds=ch.AB_KINDS, secrets=checker, recovery=True)
    for _ in range(cs.FREE_TRIES):
        assert later.answer({"recovery": "AAAA-BBBB-CCCC-DDDD"}) == ch.REFUSED
    assert later.answer({"recovery": new}) == ch.BUSY
    epoch["t"] += 1
    assert later.answer({"recovery": new}) == ch.PASSED and checker.fails() == 0
    # not offered (a practice run, the bedtime screen, or no code): refused without looking at it
    cs.remove(ctx.db, "recovery")
    none = ch.Run([step("password")], kinds=ch.AB_KINDS, secrets=checker, recovery=True)
    assert none.view()["recovery"] is False and none.answer({"recovery": new}) == ch.REFUSED
    newest = cs.make_recovery(ctx.db, NOW)
    plain = ch.Run([step("math", "m")], clock=lambda: ctx.mono["t"])
    assert plain.view()["recovery"] is False and plain.answer({"recovery": newest}) == ch.REFUSED
    assert cs.verify_secret(cs.clean_recovery(newest), cs.load(ctx.db)["recovery"])


def test_wrong_tries_in_a_row_make_the_next_one_wait_also_in_another_run_or_process(ctx, epoch):
    cs.set_password(ctx.db, PW, NOW)
    checker = checker_for(ctx)
    run = ch.Run([step("password")], kinds=ch.AB_KINDS, secrets=checker)
    for _ in range(cs.FREE_TRIES):
        assert run.answer("nope") == ch.REFUSED
    assert checker.wait_left() == 1
    assert run.answer(PW) == ch.BUSY                               # (not even looked at during the wait)
    other = checker_for(ctx)                                       # (a restart, the uninstaller's window: kept)
    assert other.wait_left() == 1 and other.fails() == cs.FREE_TRIES
    epoch["t"] += 1
    assert run.answer("nope") == ch.REFUSED and checker.wait_left() == 2
    epoch["t"] += 2
    for _ in range(20):
        run.answer("nope")
        epoch["t"] += cs.MAX_WAIT_SEC
    assert run.answer("nope") == ch.REFUSED and checker.wait_left() == cs.MAX_WAIT_SEC
    epoch["t"] -= 10_000                                           # (a clock that went back never locks out longer)
    assert checker.wait_left() == cs.MAX_WAIT_SEC
    epoch["t"] += 10_000 + cs.MAX_WAIT_SEC
    assert run.answer(PW) == ch.PASSED and checker.fails() == 0 and "tries" not in cs.load(ctx.db)


# ================================================================ the page's Api

def test_the_first_password_is_free_and_gives_no_recovery_code(ctx, api):
    locked(ctx)
    assert "same" in error(api.antibypass_password_set(PW, PW + "x"))
    assert "At least" in error(api.antibypass_password_set("abc", "abc"))
    out = data(api.antibypass_password_set(PW, PW))
    assert out["secrets"]["password"]["set"] and "recovery_code" not in out
    assert not out["secrets"]["recovery"]["exists"]
    assert cs.verify_secret(PW, cs.load(ctx.db)["password"])


def test_changing_or_removing_the_password_needs_the_challenge(ctx, api):
    cs.set_password(ctx.db, PW, NOW)
    locked(ctx)
    ticket = needs_ticket(api.antibypass_password_set("a new one", "a new one"))
    assert cs.verify_secret(PW, cs.load(ctx.db)["password"])                  # nothing changed yet
    out = solve(ctx, api, ticket)
    assert out["passed"] and "recovery_code" not in out["result"]
    assert cs.verify_secret("a new one", cs.load(ctx.db)["password"])
    ab.lock(ctx.db)
    ticket = needs_ticket(api.antibypass_secret_remove("password"))
    assert cs.status(ctx.db)["password"]["set"]
    solve(ctx, api, ticket)
    assert not cs.status(ctx.db)["password"]["set"]
    assert "Bad request" in error(api.antibypass_secret_remove("phrase"))


def test_a_secret_a_step_asks_cant_be_removed_and_a_list_cant_be_saved_without_it(ctx, api):
    locked(ctx)
    msg = error(api.antibypass_save({"steps": [step("phrase", "s0", 30), step("password", "p")]}, True))
    assert "Set up your password first" in msg
    data(api.antibypass_password_set(PW, PW))
    out = api.antibypass_save({"steps": [step("phrase", "s0", 30), step("password", "p")]})
    assert data(out)["needs_confirm"] and "Own password" in data(out)["line"]   # stricter: free, after a yes
    out = data(api.antibypass_save({"steps": [step("phrase", "s0", 30), step("password", "p")]}, True))
    assert [s["kind"] for s in out["config"]["steps"]] == ["phrase", "password"] and out["secrets"]["used"] == ["password"]
    assert "Take the Own password step out" in error(api.antibypass_secret_remove("password"))
    # removing the step is a loosening: the challenge - which now asks the password too
    ticket = needs_ticket(api.antibypass_save({"steps": [step("phrase", "s0", 30)]}))
    begin = data(api.challenge_begin(ticket))
    assert [s["kind"] for s in begin["run"]["steps"]] == ["phrase", "password"]
    solve(ctx, api, ticket)
    assert [s["kind"] for s in ab.settings(ctx.db)["steps"]] == ["phrase"]
    # a kind change of a secret step is a loosening too
    ab.lock(ctx.db)
    data(api.antibypass_save({"steps": [step("phrase", "s0", 30), step("password", "p")]}, True))
    needs_ticket(api.antibypass_save({"steps": [step("phrase", "s0", 30), step("math", "p")]}, True))


def test_authenticator_setup_needs_a_current_code_and_replacing_it_the_challenge(ctx, api, epoch):
    locked(ctx)
    begin = data(api.antibypass_totp_begin())
    assert begin["qr"].startswith("<svg") and begin["digits"] == 6 and not begin["replaces"]
    secret = begin["key"].replace(" ", "")
    assert not cs.status(ctx.db)["totp"]["set"]                                 # nothing stored before the code
    assert "Start the authenticator setup again" in error(api.antibypass_totp_confirm("forged", "123456"))
    assert "doesn't match" in error(api.antibypass_totp_confirm(begin["token"], code_at(secret, epoch["t"] - 90)))
    out = data(api.antibypass_totp_confirm(begin["token"], code_at(secret, epoch["t"])))
    assert out["secrets"]["totp"]["set"] and "recovery_code" not in out and not out["secrets"]["recovery"]["exists"]
    assert stored_secret(ctx) == secret
    assert "again" in error(api.antibypass_totp_confirm(begin["token"], code_at(secret, epoch["t"] + 30)))   # one use
    # the code used to confirm can't pass a challenge
    data(api.antibypass_save({"steps": [step("totp", "t"), step("phrase", "s0", 30)]}, True))
    ticket = needs_ticket(api.antibypass_unlock_to_edit())
    assert data(api.challenge_begin(ticket))["mode"] == "totp"
    assert "isn't right" in error(api.challenge_submit(ticket, code_at(secret, epoch["t"])))
    api.challenge_cancel(ticket)
    # replacing it: the challenge (which asks the OLD authenticator)
    again = data(api.antibypass_totp_begin())
    assert again["replaces"]
    new_secret = again["key"].replace(" ", "")
    epoch["t"] += 60
    ticket = needs_ticket(api.antibypass_totp_confirm(again["token"], code_at(new_secret, epoch["t"])))
    assert stored_secret(ctx) == secret
    out = solve(ctx, api, ticket, epoch)
    assert out["passed"] and "recovery_code" not in out["result"] and stored_secret(ctx) == new_secret


def test_making_a_recovery_code_needs_the_challenge_and_removing_it_is_free(ctx, api):
    locked(ctx)
    ticket = needs_ticket(api.antibypass_recovery_new())                       # (no password needed for it)
    assert not cs.status(ctx.db)["recovery"]["exists"]
    old = solve(ctx, api, ticket)["result"]["recovery_code"]
    assert cs.clean_recovery(old) and cs.status(ctx.db)["recovery"]["exists"]
    assert "recovery_code" not in data(api.antibypass_get())                  # shown once, never again
    ab.lock(ctx.db)
    new = solve(ctx, api, needs_ticket(api.antibypass_recovery_new()))["result"]["recovery_code"]
    assert cs.clean_recovery(new) and new != old
    assert not cs.verify_secret(cs.clean_recovery(old), cs.load(ctx.db)["recovery"])   # the old one stops working
    ab.lock(ctx.db)
    out = data(api.antibypass_recovery_remove())                               # stricter: free, even while locked
    assert not out["secrets"]["recovery"]["exists"] and "recovery" not in cs.load(ctx.db)
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": False})                  # Anti-Bypass off: free
    assert data(api.antibypass_recovery_new())["recovery_code"]


def test_the_recovery_code_passes_one_run_and_the_new_one_comes_back_once(ctx, api):
    cs.set_password(ctx.db, PW, NOW)
    code = cs.make_recovery(ctx.db, NOW)
    locked(ctx, steps=[step("math", "m", 3, 5), step("password", "p")])
    # a practice run doesn't offer it (nothing to pass there)
    ticket = needs_ticket(api.antibypass_try())
    assert data(api.challenge_begin(ticket))["run"]["recovery"] is False
    assert "isn't the recovery code" in error(api.challenge_submit(ticket, {"recovery": code}))
    api.challenge_cancel(ticket)
    assert cs.verify_secret(cs.clean_recovery(code), cs.load(ctx.db)["recovery"])
    # a real run offers it from the start and passes whole with it
    ticket = needs_ticket(api.antibypass_unlock_to_edit())
    assert data(api.challenge_begin(ticket))["run"]["recovery"] is True
    out = data(api.challenge_submit(ticket, {"recovery": code}))
    assert out["passed"] and ab.unlocked_until(ab.settings(ctx.db), NOW)
    new = out["recovery_code"]
    assert cs.clean_recovery(new) and new != code
    assert cs.verify_secret(cs.clean_recovery(new), cs.load(ctx.db)["recovery"])
    # the old one no longer works, the new one does (and is replaced in its turn)
    ab.lock(ctx.db)
    ticket = needs_ticket(api.antibypass_unlock_to_edit())
    data(api.challenge_begin(ticket))
    assert "isn't the recovery code" in error(api.challenge_submit(ticket, {"recovery": code}))
    third = data(api.challenge_submit(ticket, {"recovery": new}))["recovery_code"]
    assert cs.clean_recovery(third) and third not in (code, new)
    # with a wait set: the code passes the run, the wait still runs - and the new code is shown right away
    ab.save(ctx.db, {**ab.settings(ctx.db), "wait_min": 10, "unlocked_from": None, "unlocked_until": None})
    ticket = needs_ticket(api.antibypass_unlock_to_edit())
    data(api.challenge_begin(ticket))
    out = data(api.challenge_submit(ticket, {"recovery": third}))
    assert out["waiting_until"] and cs.clean_recovery(out["recovery_code"])
    assert not ab.unlocked_until(ab.settings(ctx.db), NOW)


def test_a_cancelled_run_keeps_the_recovery_code(ctx, api):
    cs.set_password(ctx.db, PW, NOW)
    code = cs.make_recovery(ctx.db, NOW)
    locked(ctx, steps=[step("password", "p"), step("math", "m")])
    ticket = needs_ticket(api.antibypass_unlock_to_edit())
    data(api.challenge_begin(ticket))
    assert "isn't the password" in error(api.challenge_submit(ticket, "nope"))
    data(api.challenge_cancel(ticket))
    assert cs.verify_secret(cs.clean_recovery(code), cs.load(ctx.db)["recovery"])
    assert ab.status(ab.settings(ctx.db), NOW) == "phrase"


def test_the_uninstallers_run_offers_the_recovery_code_too(ctx, api):
    """Uninstalling asks the list without the password: the code still passes it whole, and the new code is shown in
    the uninstaller's window (it stays valid for the next install - the settings are kept)."""
    cs.set_password(ctx.db, PW, NOW)
    code = cs.make_recovery(ctx.db, NOW)
    locked(ctx, steps=[step("password", "p"), step("math", "m", 3, 5)])
    done = []
    ticket = needs_ticket(ctx.guard.request(["Uninstall Lockdown"], lambda: done.append(1), uninstall=True))
    begin = data(api.challenge_begin(ticket))
    assert [s["kind"] for s in begin["run"]["steps"]] == ["math"] and begin["run"]["recovery"] is True
    out = data(api.challenge_submit(ticket, {"recovery": code}))
    assert out["passed"] and done == [1] and cs.clean_recovery(out["recovery_code"])
    assert not ab.unlocked_until(ab.settings(ctx.db), NOW)


def test_the_guard_makes_wrong_tries_wait_and_says_so(ctx, api, epoch):
    cs.set_password(ctx.db, PW, NOW)
    locked(ctx, steps=[step("password", "p")])
    ticket = needs_ticket(api.antibypass_unlock_to_edit())
    data(api.challenge_begin(ticket))
    for _ in range(cs.FREE_TRIES):
        assert "isn't the password" in error(api.challenge_submit(ticket, "nope"))
    assert "wait 1 s" in error(api.challenge_submit(ticket, PW))
    epoch["t"] += 1
    assert data(api.challenge_submit(ticket, PW))["passed"]


# ================================================================ backups and the App log

def test_backups_leave_every_secret_out_and_a_restore_keeps_this_pcs(ctx, api, epoch):
    cs.set_password(ctx.db, PW, NOW)
    code = cs.make_recovery(ctx.db, NOW)
    begin = data(api.antibypass_totp_begin())
    secret = begin["key"].replace(" ", "")
    data(api.antibypass_totp_confirm(begin["token"], code_at(secret, epoch["t"])))
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "steps": [step("password", "p"), step("totp", "t")]})
    exported = backup.export(ctx.db)
    text = json.dumps(exported)
    stored = cs.load(ctx.db)
    assert cs.SECRETS_KEY not in exported["settings"]
    for hidden in (PW, secret, code.replace("-", ""), stored["password"]["hash"], stored["recovery"]["hash"],
                   stored["password"]["salt"]):
        assert hidden not in text
    assert "password" in exported["settings"]["antibypass"]                   # (the steps themselves are in it)
    before = ctx.db.get_setting(cs.SECRETS_KEY)
    backup.restore(ctx.db, {**exported, "settings": {**exported["settings"], cs.SECRETS_KEY: "{}"}})
    assert ctx.db.get_setting(cs.SECRETS_KEY) == before                        # a restore never touches them
    # on a PC without them the restored steps are skipped (the rest of the list, or one phrase, is asked)
    cs.remove(ctx.db, "password")
    cs.remove(ctx.db, "totp")
    assert ab.asked_steps(ab.settings(ctx.db), cs.ready(ctx.db)) == ch.default_steps(ab.settings(ctx.db)["length"])
    assert data(api.antibypass_get())["secrets"]["missing"] == ["password", "totp"]


def test_the_app_log_names_what_happened_and_never_a_secret(ctx, api, epoch):
    data(api.antibypass_password_set(PW, PW))
    data(api.antibypass_password_set("second pw", "second pw"))
    begin = data(api.antibypass_totp_begin())
    secret = begin["key"].replace(" ", "")
    data(api.antibypass_totp_confirm(begin["token"], code_at(secret, epoch["t"])))
    code = data(api.antibypass_recovery_new())["recovery_code"]
    new_code = data(api.antibypass_recovery_new())["recovery_code"]
    locked(ctx, steps=[step("password", "p")])
    third = solve(ctx, api, needs_ticket(api.antibypass_unlock_to_edit()), recovery=new_code)["recovery_code"]
    data(api.antibypass_save({"steps": [step("phrase", "s0", 30)]}))           # (unlocked: free)
    data(api.antibypass_secret_remove("password"))
    data(api.antibypass_secret_remove("totp"))
    data(api.antibypass_recovery_remove())
    lines = [text for kind, text in events(ctx) if kind == "antibypass_change"]
    for want in ("own password set", "own password changed", "authenticator set up", "recovery code made, shown once",
                 "new recovery code made", "recovery code used to pass the challenge",
                 "a new recovery code was made and shown once", "own password removed", "authenticator removed",
                 "recovery code removed"):
        assert any(want in line for line in lines), (want, lines)
    everything = json.dumps(events(ctx))
    for hidden in (PW, "second pw", secret) + tuple(c for k in (code, new_code, third) for c in (k, k.replace("-", ""))):
        assert hidden not in everything
