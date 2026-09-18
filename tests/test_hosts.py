import pytest

from blocker import hosts

ORIGINAL = "# Copyright (c) Microsoft\r\n#\r\n# 127.0.0.1 localhost\r\n10.0.0.5 mynas\r\n"


@pytest.fixture
def files(tmp_path):
    h = tmp_path / "hosts"
    h.write_bytes(ORIGINAL.encode())
    return h, tmp_path / "hosts.backup"


@pytest.mark.parametrize("raw,expected", [
    ("reddit.com", "reddit.com"),
    ("https://www.Reddit.com/r/python?x=1", "reddit.com"),
    ("old.reddit.com:443", "old.reddit.com"),
    ("  bbc.co.uk ", "bbc.co.uk"),
])
def test_normalize_ok(raw, expected):
    assert hosts.normalize_host(raw) == expected


@pytest.mark.parametrize("raw", ["", "reddit", "not a domain.com", "-bad.com", "http://"])
def test_normalize_bad(raw):
    with pytest.raises(ValueError):
        hosts.normalize_host(raw)


def test_apply_add_update_remove(files):
    h, backup = files
    assert hosts.apply(["reddit.com"], h, backup) is True
    text = h.read_bytes().decode()
    assert "127.0.0.1 reddit.com\r\n" in text and "127.0.0.1 www.reddit.com\r\n" in text
    assert text.startswith(ORIGINAL)  # user content untouched
    assert backup.read_bytes().decode() == ORIGINAL

    # idempotent
    assert hosts.apply(["reddit.com"], h, backup) is False

    # update replaces section, doesn't duplicate
    hosts.apply(["x.com"], h, backup)
    text = h.read_bytes().decode()
    assert "reddit" not in text and text.count(hosts.START_MARKER) == 1

    # empty list removes the section, original content restored
    hosts.apply([], h, backup)
    assert h.read_bytes().decode() == ORIGINAL


def test_tampering_is_repaired(files):
    h, backup = files
    hosts.apply(["reddit.com"], h, backup)
    tampered = h.read_bytes().decode().replace("127.0.0.1 reddit.com\r\n", "")
    h.write_bytes(tampered.encode())
    assert hosts.apply(["reddit.com"], h, backup) is True
    assert "127.0.0.1 reddit.com\r\n" in h.read_bytes().decode()


def test_no_rewrite_when_nothing_to_do(files):
    h, backup = files
    h.write_bytes(b"127.0.0.1 localhost\n")  # LF endings, no section
    assert hosts.apply([], h, backup) is False
    assert h.read_bytes() == b"127.0.0.1 localhost\n"
    assert not backup.exists()
