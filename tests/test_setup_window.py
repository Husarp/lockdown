"""LockdownSetup.exe (installer/setup.py), 0.84.6: the window like Handy's installer - welcome, "Lockdown is running,
OK to close it?", a progress bar driven by the real steps, finish - and the in-app update (--update) that asks
nothing, starts Lockdown again and closes by itself. The Windows actions are faked; the order of the install
steps and "never leave Lockdown off" are what matter."""
import importlib.util
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import pytest

SETUP_PY = Path(__file__).resolve().parents[1] / "installer" / "setup.py"


@pytest.fixture
def setup():
    spec = importlib.util.spec_from_file_location("lockdown_setup", SETUP_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def events_of(setup, mode, **kw):
    seen = []
    setup.work(mode, lambda kind, value: seen.append((kind, value)), **kw)
    return seen


# ---------- arguments and pages ----------

def test_update_argument_means_an_in_app_update(setup):
    assert setup.mode_of(["LockdownSetup.exe"]) == "install"
    assert setup.mode_of(["LockdownSetup.exe", "--update"]) == "update"
    assert setup.mode_of(["LockdownSetup.exe", "--UPDATE"]) == "update"
    assert setup.mode_of(["Uninstall Lockdown.exe", "--uninstall"]) == "uninstall"
    assert setup.mode_of(["x", "--update", "--uninstall"]) == "uninstall"   # (uninstall never turns into an install)


def test_page_flow_asks_only_when_you_started_it(setup):
    nxt = setup.next_page
    assert nxt(None, "install") == "welcome"
    assert nxt("welcome", "install") == "progress"
    assert nxt("welcome", "install", running=True) == "running"
    assert nxt("running", "install", running=True) == "progress"
    assert nxt("welcome", "install", same=True, running=True) == "same"
    assert nxt("same", "install", same=True, running=True) == "running"
    assert nxt("same", "install", same=True) == "progress"
    assert nxt("progress", "install") == "finish"
    # an in-app update: no welcome, no questions - Lockdown closes itself
    assert nxt(None, "update") == "progress"
    # uninstall: the Anti-Bypass challenge comes first, inside the progress page
    assert nxt(None, "uninstall") == "welcome"
    assert nxt("welcome", "uninstall", same=True, running=True) == "progress"


def test_headings(setup):
    assert setup.headings("install", None)[0] == f"Lockdown {setup.VERSION} Setup"
    assert setup.headings("install", "0.1.0") == (f"Update to Lockdown {setup.VERSION}", "You have 0.1.0")
    assert setup.headings("update", "0.1.0")[0] == f"Update to Lockdown {setup.VERSION}"
    assert setup.headings("uninstall", "0.1.0")[0] == "Uninstall Lockdown"


# ---------- the bar ----------

def test_progress_follows_the_weighted_steps(setup):
    w = setup.INSTALL_WEIGHTS
    assert setup.overall(w, 0) == 0
    assert setup.overall(w, len(w)) == 1
    assert setup.overall(w, 1, 0.5) == pytest.approx((w[0] + w[1] / 2) / sum(w))
    assert setup.overall(w, 1, 1.0) == pytest.approx(setup.overall(w, 2))
    assert setup.overall(w, 1, 7) == setup.overall(w, 2)    # (clamped)
    values = [setup.overall(w, step, f / 10) for step in range(len(w) + 1) for f in range(11)]
    assert values == sorted(values)
    assert sum(setup.UNINSTALL_WEIGHTS) and setup.overall(setup.UNINSTALL_WEIGHTS, 5) == 1


def test_bar_glides_to_the_value_and_never_past_the_step(setup):
    shown = 0.0
    for _ in range(200):
        shown = setup.ease(shown, 0.4, 0.6)
    assert shown >= 0.4
    for _ in range(5000):   # a step that reports nothing creeps, but never reaches its end
        before = shown
        shown = setup.ease(shown, 0.4, 0.6)
        assert before <= shown < 0.6
    assert setup.ease(0.5, 0.3, 0.3) == 0.5   # never goes back


def test_fast_bar_drawing_matches_the_full_picture(setup):
    for w, h in ((544, 10), (816, 15)):
        for filled in (0, 10, 13, 200, w - 4, w):
            layers = [(setup.TRACK, setup.rrect(0, 0, w, h, h / 2))]
            if filled:
                layers.append((setup.ORANGE, setup.rrect(0, 0, filled, h, h / 2)))
            assert setup.bar_rows(w, h, filled, setup.ORANGE, setup.TRACK, setup.BG) == \
                setup.raster(w, h, setup.BG, [(c.lower(), d) for c, d in layers])


def test_copy_reports_every_file(setup, tmp_path, monkeypatch):
    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr("Lockdown.exe", b"x" * 100)
        z.writestr("_internal/a.dll", b"y" * 300)
    monkeypatch.setattr(setup, "INSTALL_DIR", tmp_path / "Lockdown")
    monkeypatch.setattr(setup, "payload", lambda: payload)
    seen = []
    setup.copy_files(lambda text: None, lambda fraction, name: seen.append((fraction, name)))
    assert seen == [(0.25, "Lockdown.exe"), (1.0, "_internal/a.dll")]
    assert (tmp_path / "Lockdown" / "_internal" / "a.dll").read_bytes() == b"y" * 300


def test_install_steps_keep_their_order(setup, monkeypatch):
    done = []
    for name in ("stop_everything", "copy_files", "data_folder", "register_service", "shortcuts",
                 "uninstall_entry", "start"):
        monkeypatch.setattr(setup, name, lambda *a, _n=name: done.append(_n))
    steps = []
    setup.install(lambda text: None, lambda step, fraction=0.0, name=None: steps.append(step))
    assert done == ["stop_everything", "copy_files", "data_folder", "register_service", "shortcuts",
                    "uninstall_entry", "start"]
    assert steps == list(range(len(setup.INSTALL_WEIGHTS) + 1))


# ---------- never leave Lockdown off ----------

@pytest.fixture
def windows(setup, monkeypatch, tmp_path):
    """Records the Windows commands and app launches instead of running them."""
    calls = []
    monkeypatch.setattr(setup, "run", lambda *a, check=False: calls.append(a) or
                        subprocess.CompletedProcess(a, 0, "STOPPED", ""))
    monkeypatch.setattr(setup, "launch_app", lambda: calls.append(("launch",)))
    monkeypatch.setattr(setup, "INSTALL_DIR", tmp_path)
    (tmp_path / "Lockdown.exe").write_bytes(b"")
    return calls


def test_update_mode_starts_lockdown_again(setup, windows, monkeypatch):
    monkeypatch.setattr(setup, "install", lambda log, at: None)
    seen = events_of(setup, "update")
    assert windows == [("launch",)]
    assert seen[-1] == ("done", "installed")
    windows.clear()
    assert events_of(setup, "install")[-1] == ("done", "installed")
    assert windows == []   # (an install you started: the "Run Lockdown now" tick on the finish page)


def test_a_failed_update_starts_the_service_again(setup, windows, monkeypatch):
    def copy_fails(log, each=None):
        raise PermissionError("tk86t.dll is in use")
    monkeypatch.setattr(setup, "stop_everything", lambda log: None)
    monkeypatch.setattr(setup, "copy_files", copy_fails)
    seen = events_of(setup, "update")
    assert ("schtasks", "/Change", "/TN", setup.WATCHDOG, "/ENABLE") in windows
    assert ("sc", "start", setup.SERVICE) in windows
    assert ("launch",) in windows
    assert seen[-1] == ("failed", "tk86t.dll is in use")
    assert ("log", setup.RECOVERING) in seen


def test_a_failed_uninstall_starts_the_service_again(setup, windows, monkeypatch):
    monkeypatch.setattr(setup, "challenge_passed", lambda: True)
    monkeypatch.setattr(setup, "stop_everything", lambda log: None)
    monkeypatch.setattr(setup.winreg, "OpenKey", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    seen = events_of(setup, "uninstall")
    assert ("sc", "start", setup.SERVICE) in windows
    assert seen[-1][0] == "failed"


def test_uninstall_still_needs_the_challenge(setup, windows, monkeypatch):
    monkeypatch.setattr(setup, "challenge_passed", lambda: False)
    monkeypatch.setattr(setup, "uninstall", lambda *a: pytest.fail("uninstalled without the challenge"))
    seen = events_of(setup, "uninstall", delete_data=True)
    assert seen[-1] == ("done", "refused")
    assert windows == []   # nothing stopped, nothing to start again


# ---------- the window (needs a display) ----------

@pytest.fixture
def window_of(setup, monkeypatch):
    tkinter = pytest.importorskip("tkinter")
    try:
        tkinter.Tk().destroy()
    except tkinter.TclError:
        pytest.skip("no display")
    monkeypatch.setattr(setup, "accent", lambda: setup.ORANGE)
    made = []

    def make(mode):
        made.append(setup.SetupWindow(mode))
        return made[-1]
    yield make
    for win in made:
        try:
            win.destroy()
        except tkinter.TclError:
            pass


def pump(win, seconds, until=lambda: False):
    end = time.time() + seconds
    while time.time() < end and not until():
        try:
            win.update()
        except Exception:   # (destroyed)
            return
        time.sleep(0.01)


def test_update_window_asks_nothing_and_closes_after_starting_lockdown(setup, window_of, monkeypatch):
    launched = []
    monkeypatch.setattr(setup, "install", lambda log, at: [at(i) for i in range(8)])
    monkeypatch.setattr(setup, "launch_app", lambda: launched.append(1))
    win = window_of("update")
    assert win.page == "progress"
    gone = []
    win.bind("<Destroy>", lambda e: e.widget is win and gone.append(1))
    pump(win, 4, until=lambda: gone)
    assert launched == [1] and gone


def test_running_lockdown_is_asked_about_in_the_window(setup, window_of, monkeypatch):
    monkeypatch.setattr(setup, "lockdown_running", lambda: True)
    monkeypatch.setattr(setup, "work", lambda *a, **k: None)
    win = window_of("install")
    assert win.page == "welcome"
    win.primary.invoke()
    pump(win, 2, until=lambda: win.page == "running")
    assert win.page == "running"
    win.primary.invoke()   # OK
    assert win.page == "progress" and win.busy


def test_a_failure_stays_on_screen_with_the_details_open(setup, window_of, monkeypatch):
    def fails(mode, post, delete_data=False):
        post("log", "Copying the program...")
        post("log", setup.RECOVERING)
        post("failed", "disk full")
    monkeypatch.setattr(setup, "work", fails)
    win = window_of("update")
    pump(win, 1, until=lambda: not win.busy)
    assert not win.busy and win.details_open
    assert win.p_file.cget("text") == "disk full"
    assert win.primary.cget("text") == "Close"


def test_the_install_outlives_the_window(setup, window_of, monkeypatch):
    """The worker isn't a daemon thread: if the window goes away mid-install, the process still finishes the
    install (and so starts Lockdown again) instead of dying half-way with the service stopped."""
    seen = []
    monkeypatch.setattr(setup, "work", lambda *a, **k: seen.append(threading.current_thread().daemon))
    win = window_of("update")
    pump(win, 1, until=lambda: seen)
    assert seen == [False]


def test_a_bad_event_does_not_stop_the_window_following_the_install(setup, window_of, monkeypatch):
    monkeypatch.setattr(setup, "work", lambda *a, **k: None)
    win = window_of("update")
    monkeypatch.setattr(win, "report_callback_exception", lambda *a: None)
    win.events.put(("nonsense", None))   # (no handler: raises in the drain)
    pump(win, 0.2)
    win.events.put(("log", "Copying the program..."))   # (a later round: only seen if the drain kept going)
    pump(win, 1, until=lambda: win.p_status.cget("text") == "Copying the program...")
    assert win.p_status.cget("text") == "Copying the program..."
