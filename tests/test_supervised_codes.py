import base64
from datetime import datetime

import pytest

import antibypass
import backup
import challenge_secrets as cs
import supervised_codes as sc
import webapi
from webapi import core

RFC = base64.b32encode(b"12345678901234567890").decode()


@pytest.fixture
def ctx(tmp_path):
    return webapi.make_context(tmp_path / "codes.db")


@pytest.mark.parametrize("at,want", [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
                                     (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130")])
def test_rfc(ctx, monkeypatch, at, want):
    monkeypatch.setattr(cs, "trusted_epoch", lambda db: at)
    sc.change(ctx.db, "add", "Someone", RFC)
    data = sc.codes(ctx.db)
    assert data["entries"][0]["code"] == want
    assert data["remaining"] == 30 - at % 30
    assert "secret" not in data["entries"][0]


@pytest.mark.parametrize("kind", ["supervisor", "totp"])
def test_refuse_own(ctx, kind):
    cs.set_totp(ctx.db, RFC, 0, datetime.now(), kind)
    padded = base64.b32encode(cs.secret_key(RFC) + b"\0").decode()
    for value in (RFC.lower(), cs.group_key(RFC), cs.otpauth_uri(RFC), padded):
        with pytest.raises(ValueError, match="own supervisor or authenticator"):
            sc.change(ctx.db, "add", "Me", value)
    assert sc.codes(ctx.db)["entries"] == []


def test_crud_backup_and_later_own(ctx):
    sc.change(ctx.db, "add", "One", cs.otpauth_uri(RFC))
    sc.change(ctx.db, "add", "Two", cs.new_totp_secret())
    entry = sc.codes(ctx.db)["entries"][0]
    sc.change(ctx.db, "rename", "Renamed", entry_id=entry["id"])
    assert sc.codes(ctx.db)["entries"][0]["name"] == "Renamed"
    exported = backup.export(ctx.db)
    assert sc.KEY not in exported["settings"]
    before = ctx.db.get_setting(sc.KEY)
    exported["settings"][sc.KEY] = "[]"
    backup.restore(ctx.db, exported)
    assert ctx.db.get_setting(sc.KEY) == before
    cs.set_totp(ctx.db, RFC, 0, datetime.now())
    assert len(sc.codes(ctx.db)["entries"]) == 1
    sc.change(ctx.db, "delete", entry_id=entry["id"])
    assert len(sc._load(ctx.db)) == 1


@pytest.mark.parametrize("value", ["", "!!!", "otpauth://hotp/X?secret=" + RFC,
    "otpauth://totp/X?secret=" + RFC + "&digits=8", "otpauth://totp/X?secret=" + RFC + "&algorithm=SHA256",
    "otpauth://totp/X?secret=" + RFC + "&period=60", "otpauth://totp/X?secret=" + RFC + "&secret=" + RFC])
def test_invalid(value):
    with pytest.raises(ValueError):
        sc.parse_key(value)


def test_api_pending_and_free_crud(ctx):
    antibypass.save(ctx.db, {**antibypass.settings(ctx.db), "phrase": True, "length": 30})
    api = core.build_api(ctx)
    ctx.supervisor_pending = {"secret": RFC}
    assert not api.supervised_codes_change("add", "Own pending", RFC)["ok"]
    ctx.supervisor_pending = None
    reply = api.supervised_codes_change("add", "Other", RFC)
    assert reply["ok"] and "needs_challenge" not in reply["data"]
    entry = reply["data"]["entries"][0]
    assert api.supervised_codes_change("rename", "New", "", entry["id"])["ok"]
    assert api.supervised_codes_change("delete", "", "", entry["id"])["ok"]


def test_rollover(ctx, monkeypatch):
    clock = {"at": 1111111109}
    monkeypatch.setattr(cs, "trusted_epoch", lambda db: clock["at"])
    sc.change(ctx.db, "add", "Other", RFC)
    assert sc.codes(ctx.db)["remaining"] == 1
    assert sc.codes(ctx.db)["entries"][0]["code"] == "081804"
    clock["at"] += 1
    assert sc.codes(ctx.db)["remaining"] == 30
    assert sc.codes(ctx.db)["entries"][0]["code"] == "050471"
