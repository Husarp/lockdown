"""Is there a newer Lockdown? Asks GitHub for the latest release of the repository in version.py.

Nothing is sent but the request itself - no account, no identifier, and it only happens when you press the
button on the About page. With no repository set (REPO empty) there is nothing to ask, and the button isn't
there. Standard library only.
"""
import json
import re
import urllib.request

from version import REPO, VERSION

API = "https://api.github.com/repos/{repo}/releases/latest"
PAGE = "https://github.com/{repo}"
RELEASES = "https://github.com/{repo}/releases/latest"
TIMEOUT = 8


def numbers(version: str) -> tuple:
    """"v0.73.1" -> (0, 73, 1). Anything unparseable is (0,), which never counts as newer."""
    found = [int(n) for n in re.findall(r"\d+", version)[:4]]
    return tuple(found) if found else (0,)


def is_newer(latest: str, current: str = VERSION) -> bool:
    return numbers(latest) > numbers(current)


def repo_page() -> str | None:
    return PAGE.format(repo=REPO) if REPO else None


def latest_release(fetch=None) -> dict | None:
    """{"version", "url", "newer"} for the newest release on GitHub, or None if that can't be answered
    (no repository set, no network, no releases yet). `fetch` is for the tests."""
    if not REPO:
        return None
    try:
        if fetch is None:
            def fetch(url):
                request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                               "User-Agent": f"Lockdown/{VERSION}"})
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    return response.read()
        data = json.loads(fetch(API.format(repo=REPO)))
        tag = (data.get("tag_name") or data.get("name") or "").strip()
        if not tag:
            return None
        return {"version": tag.lstrip("vV"), "url": data.get("html_url") or RELEASES.format(repo=REPO),
                "newer": is_newer(tag)}
    except Exception:
        return None
