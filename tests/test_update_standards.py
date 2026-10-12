"""0.84.8: the PC updater brought in line with APP-STANDARDS.md (sections 2-4).

- the banner's x hides it until Lockdown is next started, never for good (test_update_notice.py:
  test_the_x_hides_the_banner_until_the_next_start, test_nothing_hides_an_update_for_good);
- a failed in-app update says so with Try again and GitHub - nothing opens by itself (the About page's own tests:
  tests/ui/test_about.py);
- the installer the update downloaded (LockdownSetup-X.Y.Z.exe in the temp folder) is deleted at the next start;
- the installer is found on a release by its .exe ending, whatever the versioned name;
- the About page offers the switch, Check now, GitHub (the releases page) and Get update.
"""
import json

import updates


def release(assets):
    return json.dumps({"tag_name": "v0.84.9", "html_url": "https://github.com/Husarp/lockdown/releases/tag/v0.84.9",
                       "assets": assets}).encode()


# ---------------------------------------------------------------- finding the installer

def test_the_installer_is_found_by_its_ending_whatever_it_is_called(monkeypatch):
    monkeypatch.setattr(updates, "REPO", "Husarp/lockdown")
    assets = [{"name": "Lockdown-0.84.9.apk", "browser_download_url": "http://x/Lockdown-0.84.9.apk", "size": 9},
              {"name": "LockdownSetup-0.84.9.exe.sha256", "browser_download_url": "http://x/sum", "size": 1},
              {"name": "LockdownSetup-0.84.9.EXE", "browser_download_url": "http://x/LockdownSetup-0.84.9.EXE",
               "size": 35_000_000}]
    found = updates.latest_release(fetch=lambda url: release(assets))
    assert found["asset"] == "http://x/LockdownSetup-0.84.9.EXE" and found["size"] == 35_000_000
    old_name = [{"name": "LockdownSetup.exe", "browser_download_url": "http://x/LockdownSetup.exe", "size": 5}]
    assert updates.latest_release(fetch=lambda url: release(old_name))["asset"] == "http://x/LockdownSetup.exe"


# ---------------------------------------------------------------- the downloaded installer goes at the next start

def test_the_download_is_named_with_its_version_and_deleted_at_the_next_start(tmp_path):
    dest = updates.download_path("0.84.9", tmp_path)
    assert dest == tmp_path / "LockdownSetup-0.84.9.exe"
    dest.write_bytes(b"installer")
    (tmp_path / "LockdownSetup-0.84.8.exe").write_bytes(b"an older one")
    keep = [tmp_path / "LockdownSetup.txt", tmp_path / "Other-0.84.9.exe", tmp_path / "notes.exe"]
    for path in keep:
        path.write_bytes(b"not ours")
    gone = updates.remove_downloads(tmp_path)
    assert sorted(p.name for p in gone) == ["LockdownSetup-0.84.8.exe", "LockdownSetup-0.84.9.exe"]
    assert all(p.exists() for p in keep)


def test_one_that_cannot_be_deleted_waits_for_the_next_start(tmp_path, monkeypatch):
    (tmp_path / "LockdownSetup-0.84.9.exe").write_bytes(b"still running")

    def busy(self, *a, **k):
        raise PermissionError("in use")
    monkeypatch.setattr(updates.Path, "unlink", busy)
    assert updates.remove_downloads(tmp_path) == []                 # no error, nothing else touched


# ---------------------------------------------------------------- GitHub

def test_github_opens_the_release_or_the_releases_page(monkeypatch):
    monkeypatch.setattr(updates, "REPO", "Husarp/lockdown")
    assert updates.releases_page() == "https://github.com/Husarp/lockdown/releases"
    assert updates.releases_page({"url": "https://github.com/Husarp/lockdown/releases/tag/v0.84.9"}) == \
        "https://github.com/Husarp/lockdown/releases/tag/v0.84.9"
