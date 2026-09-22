"""Updating without leaving the app: find the installer on the release, refuse to go backwards, and check
once a day rather than on every tick."""
import io
import json
from datetime import datetime, timedelta

import updates
from db import Database

NOW = datetime(2026, 9, 22, 12, 0)


def release(tag="v0.80.0", assets=True):
    body = {"tag_name": tag, "html_url": f"https://github.com/x/y/releases/tag/{tag}", "assets": []}
    if assets:
        body["assets"] = [{"name": "notes.txt", "browser_download_url": "http://x/notes.txt", "size": 10},
                          {"name": "LockdownSetup.exe", "browser_download_url": "http://x/s.exe",
                           "size": 35_000_000}]
    return json.dumps(body).encode()


def test_it_finds_the_installer_on_the_release(tmp_path):
    found = updates.latest_release(fetch=lambda url: release())
    assert found["version"] == "0.80.0" and found["newer"]
    assert found["asset"] == "http://x/s.exe" and found["size"] == 35_000_000
    assert updates.can_install(found)


def test_a_release_with_nothing_attached_cannot_be_installed(tmp_path):
    found = updates.latest_release(fetch=lambda url: release(assets=False))
    assert found["newer"] and found["asset"] is None
    assert not updates.can_install(found)          # the GitHub page is all that can be offered


def test_it_never_goes_backwards(tmp_path):
    """Lockdown is a blocker: installing an older build would be a way to drop the rules you set."""
    found = updates.latest_release(fetch=lambda url: release(tag="v0.1.0"))
    assert not found["newer"]
    assert not updates.can_install(found)


def test_the_download_writes_the_file_and_reports_progress(tmp_path):
    seen = []

    class Response(io.BytesIO):
        headers = {"Content-Length": "9"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    dest = tmp_path / "setup.exe"
    updates.download("http://x/s.exe", dest, progress=lambda d, t: seen.append((d, t)),
                     opener=lambda r: Response(b"123456789"))
    assert dest.read_bytes() == b"123456789"
    assert seen[-1] == (9, 9)


def test_the_installer_is_started_and_nothing_else(tmp_path):
    started = []
    updates.install(tmp_path / "setup.exe", run=started.append)
    assert started == [tmp_path / "setup.exe"]


# ---------- the daily check ----------

def test_it_asks_once_a_day(tmp_path):
    db = Database(tmp_path / "t.db")
    assert updates.due(db, NOW)                    # never checked yet
    updates.checked(db, NOW)
    assert not updates.due(db, NOW + timedelta(hours=23))
    assert updates.due(db, NOW + timedelta(hours=25))


def test_turning_it_off_stops_the_asking(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_setting(updates.AUTO_KEY, "0")
    assert not updates.due(db, NOW)


def test_you_are_told_once_per_version_not_once_a_day(tmp_path):
    db = Database(tmp_path / "t.db")
    found = updates.latest_release(fetch=lambda url: release())
    assert updates.worth_saying(db, found)
    updates.said(db, found["version"])
    assert not updates.worth_saying(db, found)     # same version tomorrow: silence
    newer = updates.latest_release(fetch=lambda url: release(tag="v0.81.0"))
    assert updates.worth_saying(db, newer)         # a different one is worth saying


def test_the_notice_can_be_turned_off_on_its_own(tmp_path):
    """Off means quiet, not blind: the check still runs, About still shows what it found."""
    db = Database(tmp_path / "t.db")
    db.set_setting(updates.NOTIFY_KEY, "0")
    found = updates.latest_release(fetch=lambda url: release())
    assert not updates.worth_saying(db, found)
    assert updates.due(db, NOW)


def test_nothing_is_said_about_the_version_you_are_on(tmp_path):
    db = Database(tmp_path / "t.db")
    same = updates.latest_release(fetch=lambda url: release(tag=f"v{updates.VERSION}"))
    assert not same["newer"] and not updates.worth_saying(db, same)


def test_a_failed_check_says_nothing_and_is_not_written_down(tmp_path):
    db = Database(tmp_path / "t.db")

    def boom(url):
        raise OSError("no network")

    assert updates.latest_release(fetch=boom) is None
    assert not updates.worth_saying(db, None)
    assert updates.due(db, NOW)                    # so it tries again rather than waiting a day
