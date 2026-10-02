"""0.84.8: the PC updater brought in line with APP-STANDARDS.md (sections 2-4).

- the banner's x hides it until Lockdown is next started, never for good (test_update_notice.py:
  test_the_x_hides_the_banner_until_the_next_start, test_nothing_hides_an_update_for_good);
- a failed in-app update says so with Try again and GitHub - nothing opens by itself;
- the installer the update downloaded (LockdownSetup-X.Y.Z.exe in the temp folder) is deleted at the next start;
- the installer is found on a release by its .exe ending, whatever the versioned name;
- the About page offers the switch, Check now, GitHub (the releases page) and Get update.
"""
import json
import types

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


# ---------------------------------------------------------------- a failed update: Try again + GitHub

class Widget:
    def __init__(self):
        self.log = []
        self.state = {}

    def __getattr__(self, name):
        return lambda *a, **k: (self.log.append((name, k)), self.state.update(k))


def fake_about(monkeypatch, tmp_path):
    from gui import about_page
    opened = []
    monkeypatch.setattr(about_page.webbrowser, "open", opened.append)
    me = types.SimpleNamespace(bar=Widget(), get_btn=Widget(), check_btn=Widget(), retry_btn=Widget(),
                               page_btn=Widget(), update_note=Widget(), downloading=True, failed_file=None,
                               found={"version": "0.84.9", "url": "https://github.com/x/releases/tag/v0.84.9",
                                      "newer": True, "asset": "http://x/s.exe", "size": 0})
    for name in ("_failed", "_retry"):
        setattr(me, name, types.MethodType(getattr(about_page.AboutPage, name), me))
    return me, opened


def test_a_failed_download_offers_try_again_and_github_and_opens_nothing(monkeypatch, tmp_path):
    me, opened = fake_about(monkeypatch, tmp_path)
    me._failed(TimeoutError())
    assert ("pack", {"side": "left", "padx": (0, 8), "before": me.page_btn}) in me.retry_btn.log
    assert "Try again" in me.update_note.state["text"] and "GitHub" in me.update_note.state["text"]
    assert not me.downloading and opened == []                     # nothing opened by itself
    again = []
    me.show_found, me._get = (lambda f: again.append("show")), (lambda: again.append("get"))
    me._retry()                                                    # Try again: download again
    assert again == ["show", "get"]


def test_an_installer_that_did_not_start_is_started_again_by_try_again(monkeypatch, tmp_path):
    me, opened = fake_about(monkeypatch, tmp_path)
    dest = tmp_path / "LockdownSetup-0.84.9.exe"
    dest.write_bytes(b"x")
    me._failed(OSError("the installer could not be started (ShellExecute 5)"), dest)
    assert "didn't start" in me.update_note.state["text"] and opened == []
    started = []
    me._got = started.append
    me._retry()
    assert started == [dest] and me.downloading                    # the same file, no second download


def test_check_now_after_a_failure_leaves_one_way_to_get_it(monkeypatch, tmp_path):
    """Review fix: after a failed update, Check now finding the release again put Get update back next to the
    Try again that was still there - two buttons doing the same. A fresh answer takes Try again away."""
    from gui import about_page
    me, opened = fake_about(monkeypatch, tmp_path)
    me.app = types.SimpleNamespace(db=None)
    monkeypatch.setattr(about_page.updates, "said", lambda db, version: None)
    me.show_found = types.MethodType(about_page.AboutPage.show_found, me)
    me._failed(TimeoutError())
    me.show_found(me.found)
    assert me.retry_btn.log[-1][0] == "pack_forget" and me.get_btn.log[-1][0] == "pack"


def test_github_opens_the_release_or_the_releases_page(monkeypatch):
    monkeypatch.setattr(updates, "REPO", "Husarp/lockdown")
    assert updates.releases_page() == "https://github.com/Husarp/lockdown/releases"
    assert updates.releases_page({"url": "https://github.com/Husarp/lockdown/releases/tag/v0.84.9"}) == \
        "https://github.com/Husarp/lockdown/releases/tag/v0.84.9"
