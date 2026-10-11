"""R10: the PyInstaller spec carries the new UI + its imports, and the installer makes sure WebView2 is there
(a missing runtime is downloaded; a failure is only a warning). Nothing is built; the spec is read as text."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = (ROOT / "installer" / "lockdown.spec").read_text(encoding="utf-8")


@pytest.fixture
def setup():
    spec = importlib.util.spec_from_file_location("lockdown_setup_r10", ROOT / "installer" / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_spec_bundles_ui_folder_and_imports():
    assert '(os.path.join(SRC, "ui"), "ui")' in SPEC
    for name in ("webview", "webview.platforms.edgechromium", "webview.platforms.winforms", "clr_loader",
                 "pythonnet", "clr", "agent"):
        assert f'"{name}"' in SPEC, name
    assert 'collect_submodules("resident")' in SPEC and 'collect_submodules("webapi")' in SPEC
    # the legacy app is still bundled
    assert "gui.about_page" in SPEC and "UIA_DLLS" in SPEC


def registry(values):
    """A fake reader: values = {(hive, path-ends-with): "version"}."""
    def read(hive, path, name):
        assert name == "pv"
        for (h, tail), v in values.items():
            if h == hive and path.endswith(tail):
                return v
        return None
    return read


WOW = ("HKLM", "WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
HKLM = ("HKLM", "\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
HKCU = ("HKCU", "\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")


def test_webview2_check(setup):
    assert setup.webview2_present(registry({WOW: "120.0.2210.91"})) is True
    assert setup.webview2_present(registry({HKLM: "120.0.1"})) is True
    assert setup.webview2_present(registry({HKCU: "120.0.1"})) is True
    assert setup.webview2_present(registry({})) is False
    assert setup.webview2_present(registry({WOW: "0.0.0.0", HKLM: "", HKCU: None})) is False
    assert setup.webview2_present(registry({WOW: "0.0.0.0", HKCU: "119.0.1"})) is True


def test_present_means_no_download(setup):
    def nope(*a):
        raise AssertionError("must not download")
    assert setup.webview2_step(lambda t: None, present=lambda: True, download=nope, run_it=nope) == "present"


def test_missing_downloads_and_runs_the_bootstrapper(setup):
    calls, logs = [], []
    result = setup.webview2_step(logs.append, present=lambda: False,
                                 download=lambda: calls.append("download") or Path("x.exe"),
                                 run_it=lambda p: calls.append(("run", p)) or 0)
    assert result == "installed" and calls == ["download", ("run", Path("x.exe"))]
    assert any("WebView2" in t for t in logs)
    assert setup.WEBVIEW2_URL == "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def test_failures_are_warnings_never_aborts(setup):
    def boom():
        raise OSError("offline")
    logs = []
    assert setup.webview2_step(logs.append, present=lambda: False, download=boom) == "failed"
    assert any("Warning" in t for t in logs)
    assert setup.webview2_step(logs.append, present=lambda: False, download=lambda: Path("x"),
                               run_it=lambda p: 1) == "failed"


def test_install_runs_webview2_step_last_without_aborting(setup, monkeypatch):
    for name in ("stop_everything", "copy_files", "data_folder", "register_service", "shortcuts",
                 "uninstall_entry", "start"):
        monkeypatch.setattr(setup, name, lambda *a: None)
    monkeypatch.setattr(setup, "webview2_present", lambda read=None: False)
    monkeypatch.setattr(setup, "download_bootstrapper", lambda: (_ for _ in ()).throw(OSError("offline")))
    steps = []
    setup.install(lambda t: None, lambda step, fraction=0.0, name=None: steps.append(step))
    assert steps == list(range(len(setup.INSTALL_WEIGHTS) + 1))


def test_build_runs_and_checks_both_self_tests():
    """While both windows ship, build.ps1 runs the old window's self-test (--legacy) and the new one's (--new-ui),
    each with its own report, and fails the build unless both reports start with OK."""
    script = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")
    assert '"--legacy"' in script and '"--new-ui"' in script and '"--selftest"' in script
    assert "selftest.txt" in script and "selftest-new.txt" in script
    assert 'StartsWith("OK")' in script and "throw" in script
