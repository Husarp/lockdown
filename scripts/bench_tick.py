"""Time one usage-tracker tick (monitor/usage.py) - the tray's "which windows are in front" check - with fakes.

    python scripts/bench_tick.py                          # this checkout
    python scripts/bench_tick.py --src /tmp/ld-old/src    # an older checkout, for before / after numbers
    python scripts/bench_tick.py --src /tmp/ld-old/src --tick 0.5   # what simply ticking faster would cost

Windows is faked at the same seams the tests use: the window list (win.enum_windows / win.window_facts: two
monitors, ~60 top-level windows, Chrome in front on monitor 1 and a game on monitor 2), the foreground window, the
address-bar read (browser_url.browser_url) and the process list (blocker.apps.list_processes). The two calls that
are expensive on a real PC are given a simulated CPU cost (--uia-ms, --procs-ms) and counted. The database is a
real SQLite file (20 limited items, a group with an allowance), and every commit is counted - on a real disk each
one is a flush.

Simulated time runs at the tracker's own TICK_SEC for --seconds, so a faster tick shows up as more ticks per
second. Prints, per scenario: CPU per tick (median, ms), CPU per second of real time, and per minute the address
bar reads, process-list reads and database commits. Runs on Windows and (with the test fakes) on Linux.
"""
import argparse
import importlib
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M1, M2 = (0, 0, 1920, 1080), (1920, 0, 3840, 1080)
CHROME, GAME, WORD = r"C:\Chrome\chrome.exe", r"D:\Games\Game\game.exe", r"C:\Office\winword.exe"


def burn(ms: float):
    """Spend `ms` of CPU, standing in for a Windows call."""
    end = time.process_time() + ms / 1000
    while time.process_time() < end:
        pass


class FakeTime:
    """Simulated wall / monotonic time, moved by hand (the tracker's own clocks and the trusted time)."""

    def __init__(self):
        self.t = 1_800_000_000.0

    def monotonic(self):
        return self.t

    def time(self):
        return self.t


class Desktop:
    def __init__(self, win, uia_ms: float, procs_ms: float):
        self.win, self.uia_ms, self.procs_ms = win, uia_ms, procs_ms
        self.uia = self.procs = 0
        self.burnt = 0.0
        self.foreground = 1
        self.titles = {1: "Cats - YouTube - Google Chrome", 2: "Game", 3: "Document1 - Word"}
        self.urls = {1: "https://www.youtube.com/watch?v=cats"}
        F = win.WindowFacts
        shown = [F(1, rect=M1), F(2, rect=M2), F(3, rect=M1)]
        hidden = [F(100 + i, visible=False) for i in range(50)] + [F(200 + i, minimized=True) for i in range(6)]
        self.windows = shown + hidden
        self.pids = {1: (100, CHROME), 2: (200, GAME), 3: (300, WORD)}
        self.process_list = [(100, "chrome.exe"), (200, "game.exe"), (300, "winword.exe")] + \
                            [(1000 + i, f"svc{i}.exe") for i in range(250)]

    def install(self, browser_url, usage_mod, apps):
        win = self.win
        by_hwnd = {w.hwnd: w for w in self.windows}
        win.enum_windows = lambda: [w.hwnd for w in self.order()]
        win.window_facts = lambda hwnd: by_hwnd[hwnd]
        win.monitors = lambda: [M1, M2]
        win.window_pid = lambda hwnd: self.pids.get(hwnd, (0, ""))[0]
        win.pid_path = lambda pid: next((p for q, p in self.pids.values() if q == pid), "")
        win.foreground_process = self.foreground_process
        win.session_locked = lambda: False
        win.idle_seconds = lambda: 0.0
        win.window_title = lambda hwnd: self.titles.get(hwnd, "")
        browser_url.browser_url = self.browser_url
        apps.list_processes = self.list_processes
        usage_mod.list_processes = self.list_processes

    def order(self):
        front = [w for w in self.windows if w.hwnd == self.foreground]
        return front + [w for w in self.windows if w.hwnd != self.foreground]

    def foreground_process(self):
        pid, path = self.pids[self.foreground]
        return self.foreground, self.win.exe_name(path), path

    def browser_url(self, hwnd):
        self.uia += 1
        t = time.process_time()
        burn(self.uia_ms)
        self.burnt += time.process_time() - t
        return self.urls.get(hwnd)

    def list_processes(self):
        self.procs += 1
        t = time.process_time()
        burn(self.procs_ms)
        self.burnt += time.process_time() - t
        return list(self.process_list)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "src"), help="the src folder to measure")
    ap.add_argument("--seconds", type=int, default=600, help="simulated seconds per scenario")
    ap.add_argument("--uia-ms", type=float, default=2.0, help="simulated CPU of one address-bar read")
    ap.add_argument("--procs-ms", type=float, default=1.5, help="simulated CPU of one process-list read")
    ap.add_argument("--tick", type=float, help="override TICK_SEC (e.g. the old code ticked 4x as fast)")
    args = ap.parse_args()

    sys.path.insert(0, args.src)
    sys.path.insert(1, str(ROOT / "tests"))
    if sys.platform != "win32":
        import conftest  # noqa: F401  (inert Windows / Tk fakes so the modules import)
    db_mod = importlib.import_module("db")
    usage_mod = importlib.import_module("monitor.usage")
    win = importlib.import_module("monitor.win")
    browser_url = importlib.import_module("monitor.browser_url")
    apps = importlib.import_module("blocker.apps")
    rules = importlib.import_module("rules")
    if args.tick:
        usage_mod.TICK_SEC = args.tick
    tick_sec = usage_mod.TICK_SEC
    print(f"src: {args.src}")
    print(f"TICK_SEC = {tick_sec}   simulated: address-bar read {args.uia_ms} ms, process list {args.procs_ms} ms")
    print(f"{'scenario':<12}{'ticks/s':>8}{'CPU/tick':>10}{'own/tick':>10}{'CPU/s':>8}"
          f"{'URL reads':>11}{'proc lists':>11}{'commits':>9}   (CPU in ms; reads and commits per minute)")

    for scenario in ("steady", "browsing"):
        path = Path(tempfile.mkdtemp(prefix="lockdown-tick-")) / "config.db"
        db = db_mod.Database(path)
        allow = rules.make_schedule("allow", [(list(range(7)), "16:00", "18:00")])
        ids = [db.add_item(f"Site {i}", [f"site{i}.com"], "site",
                           rules=[{"rule_type": "time_limit", "daily_limit_min": 600}]) for i in range(16)]
        ids.append(db.add_item("YouTube", ["youtube.com"], "site",
                               rules=[{"rule_type": "time_limit", "daily_limit_min": 600}]))
        ids.append(db.add_item("Game", ["game.exe"], "app",
                               rules=[{"rule_type": "time_limit", "daily_limit_min": 600}]))
        db.add_group("Evenings", [{"rule_type": "scheduled", "schedule": allow, "allowance_min": 600}],
                     {i: {} for i in ids[-2:]})
        commits = []
        db.conn.set_trace_callback(lambda sql: sql == "COMMIT" and commits.append(1))

        clock = FakeTime()
        real_monotonic, real_time = time.monotonic, time.time
        time.monotonic, time.time = clock.monotonic, clock.time
        start = datetime(2026, 10, 2, 19, 0)
        usage_mod.now_from_db = lambda _db: start + timedelta(seconds=clock.t - 1_800_000_000.0)
        desk = Desktop(win, args.uia_ms, args.procs_ms)
        desk.install(browser_url, usage_mod, apps)
        usage_mod._no_bar.clear()
        usage_mod._seen.clear()
        tracker = usage_mod.UsageTracker()
        samples, own = [], []
        try:
            n = round(args.seconds / tick_sec)
            for i in range(n):
                clock.t += tick_sec
                elapsed = (i + 1) * tick_sec
                if scenario == "browsing":
                    if elapsed % 4 < tick_sec:          # another page every 4 s
                        desk.titles[1] = f"Video {int(elapsed)} - YouTube - Google Chrome"
                    if elapsed % 15 < tick_sec:         # switch between Chrome and Word every 15 s
                        desk.foreground = 3 if desk.foreground == 1 else 1
                burnt = desk.burnt
                t = time.process_time()
                tracker.tick(db, usage_mod.sense_desktop)
                spent = time.process_time() - t
                samples.append(spent * 1000)
                own.append((spent - (desk.burnt - burnt)) * 1000)
            if hasattr(tracker, "flush"):
                tracker.flush(db)
        finally:
            time.monotonic, time.time = real_monotonic, real_time
        per_min = 60 / args.seconds
        cpu = statistics.median(samples)
        mean = statistics.mean(samples)
        print(f"{scenario:<12}{1 / tick_sec:>8.1f}{cpu:>10.2f}{statistics.median(own):>10.2f}"
              f"{mean / tick_sec:>8.2f}{desk.uia * per_min:>11.0f}{desk.procs * per_min:>11.0f}"
              f"{len(commits) * per_min:>9.0f}")
        db.close()


if __name__ == "__main__":
    main()
