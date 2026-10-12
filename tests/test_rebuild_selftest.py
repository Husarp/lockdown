"""R09: the new --selftest. The Windows window itself can't run here, so the plain-Python part is tested with a
stand-in runtime: windows present, every read method called, the report ("OK" + JSON, or a traceback) and the exit code.
"""
import json
from types import SimpleNamespace

import pytest

import webapi
from db import Database
from resident import selftest
from webapi import core


class FakeRuntime:
    """Stands in for the pywebview runtime: calls check(env) with three fake windows and some timings."""

    def __init__(self, windows=None, timings=None):
        self.windows = windows or SimpleNamespace(main=object(), corner=object(), overlay=object())
        self._timings = timings if timings is not None else {"tray_icon_ms": 400, "main_hydrated_ms": 900,
                                                            "window_show_ms": 80,
                                                            "page_first_ms": {"dashboard": 20.0, "blocking": 31.5},
                                                            "page_reshow_ms": {"dashboard": 17.0, "blocking": 18.0}}
        self.ran = False

    def run(self, check):
        self.ran = True
        check(SimpleNamespace(windows=self.windows, timings=lambda: dict(self._timings)))


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "config.db"
    Database(path)
    return webapi.make_context(path)


def split(report):
    text = report.read_text("utf-8")
    first, _, rest = text.partition("\n")
    return first, rest


def test_selftest_writes_ok_and_json_with_every_read_method(tmp_path, ctx):
    report = tmp_path / "report.txt"
    rt = FakeRuntime()
    assert selftest.run(str(report), runtime=rt, ctx=ctx) == 0
    first, rest = split(report)
    assert first.startswith("OK") and rt.ran
    data = json.loads(rest)
    reads = [name for name, kind in core.api_methods() if kind == "read" and name not in selftest.SKIP_READS]
    assert reads and sorted(data["read_methods"]) == sorted(reads)
    assert all(isinstance(ms, (int, float)) and ms >= 0 for ms in data["read_methods"].values())
    assert data["tray_icon_ms"] == 400 and data["window_show_ms"] == 80
    assert data["page_first_ms"]["blocking"] == 31.5
    assert data["over_budget"] == [] and data["not_called"] == []
    assert f"{len(reads)} read methods" in first


def test_every_read_method_of_the_real_api_can_be_called(ctx):
    """The tripwire: a page ticket that adds a `read` method with arguments adds a READ_ARGS line in
    src/resident/selftest.py (the self-test and scripts/perf_ui.py use it); otherwise this names the method."""
    reads = selftest.call_reads(ctx, strict=True)
    assert reads and all(ms is not None for ms in reads.values())


def test_a_read_method_that_raises_gives_the_traceback_and_exit_1(tmp_path, ctx):
    reg = core.Registry()

    @reg._decorator("read")
    def fine(ctx):
        return {"x": 1}

    @reg._decorator("read")
    def broken(ctx):
        raise ValueError("boom in broken")

    report = tmp_path / "report.txt"
    assert selftest.run(str(report), runtime=FakeRuntime(), registry=reg, ctx=ctx) == 1
    text = report.read_text("utf-8")
    assert not text.startswith("OK")
    assert "Traceback" in text and "ValueError: boom in broken" in text and "in broken" in text


def test_a_missing_window_fails(tmp_path, ctx):
    report = tmp_path / "report.txt"
    windows = SimpleNamespace(main=object(), corner=None, overlay=object())
    assert selftest.run(str(report), runtime=FakeRuntime(windows=windows), ctx=ctx) == 1
    assert "windows not created: ['corner']" in report.read_text("utf-8")


def test_a_runtime_that_cannot_boot_fails_with_its_traceback(tmp_path, ctx):
    class Broken:
        def run(self, check):
            raise RuntimeError("no GUI here")
    report = tmp_path / "report.txt"
    assert selftest.run(str(report), runtime=Broken(), ctx=ctx) == 1
    assert "RuntimeError: no GUI here" in report.read_text("utf-8")


def test_a_read_method_with_arguments_needs_a_sample_line(tmp_path, ctx, monkeypatch):
    reg = core.Registry()

    @reg._decorator("read")
    def lookup(ctx, item_id, extra=None):
        return {"id": item_id}

    report = tmp_path / "report.txt"
    assert selftest.run(str(report), runtime=FakeRuntime(), registry=reg, ctx=ctx) == 0       # not called, but named
    assert json.loads(split(report)[1])["not_called"] == ["lookup"]
    with pytest.raises(selftest.SelftestError, match="READ_ARGS"):
        selftest.call_reads(ctx, reg, strict=True)
    monkeypatch.setitem(selftest.READ_ARGS, "lookup", [7])
    assert selftest.run(str(report), runtime=FakeRuntime(), registry=reg, ctx=ctx) == 0
    assert json.loads(split(report)[1])["not_called"] == []
    monkeypatch.setitem(selftest.READ_ARGS, "lookup", lambda c: [c.db.item_count()])      # a function of the context
    assert selftest.run(str(report), runtime=FakeRuntime(), registry=reg, ctx=ctx) == 0


def test_timings_over_their_target_are_listed_but_do_not_fail(tmp_path, ctx):
    report = tmp_path / "report.txt"
    rt = FakeRuntime(timings={"tray_icon_ms": 1500, "window_show_ms": 350, "page_reshow_ms": {"a": 10, "b": 70}})
    assert selftest.run(str(report), runtime=rt, ctx=ctx) == 0
    over = json.loads(split(report)[1])["over_budget"]
    assert len(over) == 3
    assert any(line.startswith("window_show_ms") and "OVER THE LIMIT" in line for line in over)
    assert any(line.startswith("page_reshow_ms: 70 ms") and "OVER" not in line for line in over)


def test_an_unwritable_report_returns_1(tmp_path, ctx):
    assert selftest.run(str(tmp_path / "no" / "such" / "report.txt"), runtime=FakeRuntime(), ctx=ctx) == 1


def test_the_build_carries_the_page_files():
    assert selftest.check_ui_files() == []


def test_agent_main_runs_the_selftest_without_the_single_instance_lock(tmp_path, monkeypatch):
    import agent
    from gui import single_instance
    seen = []
    monkeypatch.setattr(selftest, "run", lambda report, **kw: seen.append(report) or 0)
    monkeypatch.setattr(single_instance, "acquire", lambda *a: pytest.fail("the self-test must not take the lock"))
    assert agent.main(["Lockdown.exe", "--selftest", "r.txt"]) == 0
    assert agent.main(["Lockdown.exe", "--new-ui", "--selftest", "r2.txt"]) == 0       # (an old flag: ignored)
    assert seen == ["r.txt", "r2.txt"]


def test_the_page_timing_script_is_one_expression():
    js = selftest.PAGE_TIMING_JS.strip()
    assert js.startswith("(async () => {") and js.endswith("})()")
    assert "router.state" in js and "reshow" in js and "first" in js


def test_a_read_method_that_opens_a_dialog_is_skipped_on_purpose(tmp_path, ctx, monkeypatch):
    reg = core.Registry()

    @reg._decorator("read")
    def pick_something(ctx):
        raise AssertionError("must not be called")

    monkeypatch.setitem(selftest.SKIP_READS, "pick_something", "opens a dialog")
    report = tmp_path / "report.txt"
    assert selftest.run(str(report), runtime=FakeRuntime(), registry=reg, ctx=ctx) == 0
    data = json.loads(split(report)[1])
    assert data["read_methods"] == {} and data["skipped_on_purpose"]["pick_something"] == "opens a dialog"


def test_the_real_dialog_opening_read_method_is_on_the_skip_list():
    assert "pick_program_file" in selftest.SKIP_READS
