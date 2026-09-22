"""Checking GitHub for a newer version: which one is newer, and reading the answer."""
import json

import pytest

import updates


@pytest.mark.parametrize("version, parts", [
    ("0.73.0", (0, 73, 0)), ("v1.2.3", (1, 2, 3)), ("0.9", (0, 9)), ("nightly", (0,)), ("", (0,)),
])
def test_reading_a_version(version, parts):
    assert updates.numbers(version) == parts


@pytest.mark.parametrize("latest, current, newer", [
    ("0.73.1", "0.73.0", True), ("v0.74.0", "0.73.9", True), ("1.0.0", "0.99.9", True),
    ("0.73.0", "0.73.0", False), ("0.72.9", "0.73.0", False), ("rubbish", "0.73.0", False),
])
def test_which_one_is_newer(latest, current, newer):
    assert updates.is_newer(latest, current) is newer


def _release(tag="v0.80.0"):
    return json.dumps({"tag_name": tag, "html_url": f"https://github.com/x/y/releases/tag/{tag}"}).encode()


def test_the_answer_from_github(monkeypatch):
    monkeypatch.setattr(updates, "REPO", "someone/lockdown")
    found = updates.latest_release(fetch=lambda url: _release())
    assert found == {"version": "0.80.0", "url": "https://github.com/x/y/releases/tag/v0.80.0", "newer": True,
                     "asset": None, "size": 0}        # this release has no installer attached


def test_nothing_breaks_without_a_repository_or_a_network(monkeypatch):
    monkeypatch.setattr(updates, "REPO", "")
    assert updates.latest_release() is None and updates.repo_page() is None
    monkeypatch.setattr(updates, "REPO", "someone/lockdown")
    assert updates.repo_page() == "https://github.com/someone/lockdown"

    def boom(url):
        raise OSError("no network")
    assert updates.latest_release(fetch=boom) is None
    assert updates.latest_release(fetch=lambda url: b"not json") is None
    assert updates.latest_release(fetch=lambda url: json.dumps({}).encode()) is None
