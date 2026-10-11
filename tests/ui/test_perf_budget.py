"""R09: the performance budget (BLUEPRINT section 2) as tests. The numbers come from tests/ui/perfkit.py - the same
code scripts/perf_ui.py prints a table from - measured once per run on a synthetic two-year database after the
retention job (730 days, 40 items, 20 000 network rows).

Each test asserts the LIMIT (a margin for a slow CI box); the target is what we aim for and is printed as a warning
line when missed. New pages are picked up automatically (the ten sidebar pages come from the router); the parts inside
a page register in perfkit.VIEWS / perfkit.SCROLLS, a `read` Api method with arguments in selftest.READ_ARGS.
"""
import pytest

import perfkit
from perfkit import BUDGET

SIDEBAR = ["dashboard", "blocking", "antibypass", "screentime", "netlog", "modes", "reminders", "notifications",
           "settings", "about"]


def say(text):
    print("\n  " + text)


@pytest.fixture(scope="module")
def measured(browser, tmp_path_factory):
    """(Api timings per database, browser numbers) - measured once for the whole module."""
    long_dir, short_dir = tmp_path_factory.mktemp("perf730"), tmp_path_factory.mktemp("perf60")
    harness, info = perfkit.make_harness(long_dir, days=730, rolled=True)
    try:
        short_db = short_dir / "config.db"
        perfkit.build_db(short_db, 60)
        api = {"60d": perfkit.time_api(short_db), "730d-rolled": perfkit.time_api(harness.db_path)}
        report = {"databases": {"730d-rolled": info}, "api": api,
                  "browser": perfkit.measure_browser(browser, harness)}
    finally:
        harness.stop()
    print("\n" + perfkit.format_report(report))
    return report


def over_target(rows, name):
    for r in rows:
        if r["status"] == "WARN":
            say(f"over the target (not the limit): {r['what']} = {r['value']}{r['unit']} (target {r['target']})")


def check(measured, name):
    """Assert every row of one budget against its limit; warn about rows over the target."""
    rows = [r for r in perfkit.budget_rows(measured) if r["name"] == name]
    assert rows, f"nothing was measured for {name}"
    over_target(rows, name)
    failed = [f"{r['what']}: {r['value']}{r['unit']} > limit {r['limit']}" for r in rows if r["status"] == "FAIL"]
    assert not failed, "\n".join(failed)
    return rows


# ---------------------------------------------------------------- Python: the Api

def test_every_read_method_was_measured(measured):
    for label, info in measured["api"].items():
        assert info["not_measured"] == {}, f"{label}: add a READ_ARGS line (src/resident/selftest.py) for {list(info['not_measured'])}"
        assert len(info["methods"]) >= 3


def test_api_read_p95_is_within_budget_on_a_two_year_database(measured):
    rows = check(measured, "api_read_p95_ms")
    assert len(rows) >= 6                                      # (3+ methods on each of the two databases)
    assert BUDGET["api_read_p95_ms"] == (50, 100)


# ---------------------------------------------------------------- the pages

def test_all_ten_sidebar_pages_were_measured(measured):
    b = measured["browser"]
    assert b["pages"] == SIDEBAR
    assert sorted(b["page_first_ms"]) == sorted(SIDEBAR)
    assert sorted(b["page_reshow_ms"]) == sorted(SIDEBAR)
    assert min(b["page_first_ms"].values()) > 0, "a page never finished loading (-1 = timed out)"


def test_first_page_load_is_within_budget(measured):
    check(measured, "page_first_ms")


def test_page_reshow_is_within_budget(measured):
    check(measured, "page_reshow_ms")


def test_registered_views_load_within_budget(measured):
    for label, ms in measured["browser"]["views_first_ms"].items():
        assert ms > 0, f"view {label} never showed its content"
    if perfkit.VIEWS:
        check(measured, "view_first_ms")


def test_registered_views_and_lists_point_at_real_pages():
    for label, page_id, click, ready in perfkit.VIEWS:
        assert page_id in SIDEBAR, f"VIEWS: {label}"
    for label, page_id, selector, probe in perfkit.SCROLLS:
        assert page_id in SIDEBAR, f"SCROLLS: {label}"


def test_long_lists_scroll_smoothly(measured):
    check(measured, "scroll_gap_ms")
    for label, s in measured["browser"]["scrolls"].items():
        assert s["frames"] >= 60, label
        assert s["live_max"] <= 80, f"{label}: {s['live_max']} live rows (the list must be virtual)"
    assert "vlist 10 000 rows" in measured["browser"]["scrolls"]


def test_shell_first_paint(measured):
    check(measured, "fcp_ms")


def test_heap_does_not_grow_over_page_switches(measured):
    check(measured, "heap_growth_mb")


def test_a_hidden_window_runs_no_timers_and_makes_no_api_calls(measured):
    rows = check(measured, "hidden_timers") + check(measured, "hidden_rpc")
    assert all(r["value"] == 0 for r in rows)
    assert measured["browser"]["hidden"]["seconds"] >= 5


def test_no_page_errors_while_measuring(measured):
    assert measured["browser"]["errors"] == []


# ---------------------------------------------------------------- the helpers themselves

def test_percentile_and_status():
    assert perfkit.percentile(list(range(1, 21)), 95) == 19
    assert perfkit.percentile([5], 95) == 5
    assert perfkit.percentile([1, 2, 3, 4], 50) == 2
    assert perfkit._status(40, 50, 100) == "ok" and perfkit._status(60, 50, 100) == "WARN" and perfkit._status(101, 50, 100) == "FAIL"


def test_budget_rows_flag_what_is_over(measured):
    """check() fails on a number over its limit and only warns over the target (a doctored copy of the real report)."""
    import copy
    bad = copy.deepcopy(measured)
    bad["browser"]["page_reshow_ms"]["modes"] = 150
    bad["browser"]["page_first_ms"]["about"] = 200
    with pytest.raises(AssertionError, match="page re-show: modes: 150"):
        check(bad, "page_reshow_ms")
    rows = check(bad, "page_first_ms")                        # 200 is over the target (150) but within the limit (300)
    assert [r["status"] for r in rows if r["what"].endswith("about")] == ["WARN"]


def test_registered_views_and_lists_are_measured(browser, tmp_path, monkeypatch):
    """The plug-in lines work: a VIEWS entry and a SCROLLS entry end up in the numbers and in the budget rows."""
    harness, _ = perfkit.make_harness(tmp_path, days=14, rolled=False)
    monkeypatch.setattr(perfkit, "VIEWS", [("blocking tab", "dashboard", '#nav [data-nav="blocking"]',
                                            '.page-host[data-page="blocking"] h1')])
    monkeypatch.setattr(perfkit, "SCROLLS", [("view area", "dashboard", "#view", "#nav [data-nav]")])
    try:
        b = perfkit.measure_browser(browser, harness, fcp_runs=1, scroll_seconds=1, hidden_seconds=1)
    finally:
        harness.stop()
    assert b["views_first_ms"]["blocking tab"] >= 0
    assert b["scrolls"]["view area"]["frames"] >= 30 and b["scrolls"]["view area"]["live_max"] == 10
    names = [r["what"] for r in perfkit.budget_rows({"browser": b})]
    assert "view first visit: blocking tab" in names and "scroll, worst frame gap: view area" in names
