"""Numbers for the rebuilt UI's performance budget (BLUEPRINT section 2). Repeatable, runs on Linux and Windows.

    python scripts/perf_ui.py [REPORT_DIR]        # default: the system temp folder -> REPORT_DIR/perf-report.json
    python scripts/perf_ui.py --no-browser        # only the Python Api timings
    python scripts/perf_ui.py --runs 50 --days 730

What it does
  1. builds synthetic databases (tests/synthdata.py): 60 days; 730 days as the raw worst case; 730 days after the
     retention job (30+ days of detail, daily totals - what a real install looks like). 40 blocked items and 20 000
     network rows each.
  2. times every registered `read` Api method (1 cold call, then 20 runs: p50 / p95 / max) through the real exposed
     Api object.
  3. when Playwright and Chrome are there (tests/ui/harness.py): serves the real page over the real Api on the 730-day
     database and measures the shell's first paint, every page's first load and re-show, tabs/lists registered in
     tests/ui/perfkit.py (VIEWS / SCROLLS), the 10 000-row virtual list, JS heap growth over 10 page switches and that a
     hidden window runs no timers and makes no Api calls.
  4. prints the table + which numbers are over their target, writes perf-report.json. Exit code 1 if anything is over
     its hard limit (so a script can gate on it).
New pages need nothing here: see the top of tests/ui/perfkit.py. The Windows window itself is measured by
`Lockdown.exe --new-ui --selftest REPORT` (src/resident/selftest.py).
"""
import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "ui"))
import perfkit  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("report_dir", nargs="?", help="where perf-report.json goes (default: the temp folder)")
    ap.add_argument("--runs", type=int, default=perfkit.RUNS, help="timed runs per Api method (default 20)")
    ap.add_argument("--days", type=int, default=730, help="length of the long database (default 730 = 2 years)")
    ap.add_argument("--no-browser", action="store_true", help="skip the headless Chrome part")
    args = ap.parse_args(argv)
    perfkit.setup_paths()
    work = perfkit.temp_dir()
    out_dir = Path(args.report_dir) if args.report_dir else Path(tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "python": platform.python_version(),
              "platform": platform.platform(), "runs": args.runs, "databases": {}, "api": {}}
    for label, days, rolled, budgeted in perfkit.DATABASES:
        days = args.days if days == 730 else days
        path = work / f"{label}.db"
        print(f"building {label} ...", flush=True)
        info = perfkit.build_db(path, days, rolled)
        info["budgeted"] = budgeted
        report["databases"][label] = info
        print(f"timing the Api on {label} ...", flush=True)
        api = perfkit.time_api(path, args.runs)
        api["budgeted"] = budgeted
        report["api"][label] = api

    why = "--no-browser" if args.no_browser else perfkit.chrome_missing()
    if why:
        report["browser_skipped"] = why
    else:
        from playwright.sync_api import sync_playwright
        print("measuring the pages in headless Chrome ...", flush=True)
        (work / "browser").mkdir()
        harness, _info = perfkit.make_harness(work / "browser", days=args.days, rolled=True)
        try:
            with sync_playwright() as p:
                browser = perfkit.launch(p)
                report["chrome"] = browser.version
                report["browser"] = perfkit.measure_browser(browser, harness, log=lambda t: print("  " + t, flush=True))
                browser.close()
        finally:
            harness.stop()

    report["budget"] = perfkit.budget_rows(report)
    text = perfkit.format_report(report)
    target = out_dir / "perf-report.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n" + text + f"\n\nreport: {target}")
    return 1 if any(r["status"] == "FAIL" for r in report["budget"]) or any(
            a["not_measured"] for a in report["api"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
